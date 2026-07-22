"""
gem_registry.py — GemRegistry 注册中心

300 Agent 融合项目的 Gem 注册中心，统一管理 Gem 的发现、同步、缓存和查询。

核心特性:
- 多级缓存: 内存 L1 缓存 + 持久化 L2 缓存（File/Redis）
- Sheets 同步: 双向同步 Google Sheets gem-metadata 表
- 新增检测: 自动对比发现新 Gem，触发通知
- 并发安全: 使用 asyncio.RLock 保护内部状态
- 事件驱动: 支持注册同步钩子

用法:
    registry = GemRegistry(sheets_manager=sheets_mgr, cache=file_cache)
    await registry.initialize(provider=webapi_adapter)
    report = await registry.sync_gems()
    gem = registry.find_gem("gem_001")
"""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Any, AsyncIterator, Callable, Coroutine, Dict, List, Optional,
    Protocol, Set, Tuple, TypeVar, Union,
)

from pydantic import BaseModel, Field

import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 枚举类型
# ═══════════════════════════════════════════════════════════

class GemSource(str, Enum):
    """Gem 数据来源"""
    GEMINI = "gemini"
    SHEETS = "sheets"
    MANUAL = "manual"
    IMPORT = "import"


class SyncStatus(str, Enum):
    """同步状态"""
    IDLE = "idle"
    SYNCING = "syncing"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class CacheBackend(str, Enum):
    """缓存后端类型"""
    MEMORY = "memory"
    FILE = "file"
    REDIS = "redis"


# ═══════════════════════════════════════════════════════════
# 类型别名
# ═══════════════════════════════════════════════════════════

GemId = str
Tag = str
SyncHook = Callable[[List["GemMetadata"], List["GemMetadata"]], Coroutine[Any, Any, None]]


# ═══════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════

class MatchDimensions(BaseModel):
    """匹配维度 — 描述 Gem 适配的业务维度"""
    product_categories: List[str] = Field(default_factory=list)
    price_ranges: List[str] = Field(default_factory=list)
    product_features: List[str] = Field(default_factory=list)
    target_audiences: List[str] = Field(default_factory=list)
    age_ranges: List[str] = Field(default_factory=list)
    gender: str = Field(default="")
    marketing_scenes: List[str] = Field(default_factory=list)
    usage_scenes: List[str] = Field(default_factory=list)
    platforms: List[str] = Field(default_factory=list)
    video_styles: List[str] = Field(default_factory=list)
    tones: List[str] = Field(default_factory=list)

    def get_all_tags(self: "MatchDimensions") -> List[str]:
        """获取所有标签的集合"""
        tags: List[str] = []
        tags.extend(self.product_categories)
        tags.extend(self.price_ranges)
        tags.extend(self.product_features)
        tags.extend(self.target_audiences)
        tags.extend(self.age_ranges)
        if self.gender:
            tags.append(self.gender)
        tags.extend(self.marketing_scenes)
        tags.extend(self.usage_scenes)
        tags.extend(self.platforms)
        tags.extend(self.video_styles)
        tags.extend(self.tones)
        return tags

    def to_flat_dict(self: "MatchDimensions") -> Dict[str, Any]:
        """转为扁平字典"""
        return self.model_dump()


class GemPerformance(BaseModel):
    """Gem 性能统计"""
    avg_gen_time: float = Field(default=0.0)
    usage_count: int = Field(default=0)
    success_rate: float = Field(default=0.0)
    avg_match_score: float = Field(default=0.0)
    best_for_examples: List[str] = Field(default_factory=list)


class GemMetadata(BaseModel):
    """
    Gem 元数据 — 注册中心的核心数据模型
    """
    gem_id: str = Field(..., description="Gem 唯一标识")
    gem_name: str = Field(default="", description="显示名称")
    gem_type: str = Field(default="", description="类型：促销类/品牌类/剧情类/开箱类/测评类")
    gem_internal_name: str = Field(default="", description="Gemini 中 Gem 的内部名称")
    is_active: bool = Field(default=True)
    auto_discovered: bool = Field(default=False)
    source: str = Field(default=GemSource.MANUAL.value)
    dimensions: MatchDimensions = Field(default_factory=MatchDimensions)
    performance: GemPerformance = Field(default_factory=GemPerformance)
    notes: str = Field(default="")
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    row_index: Optional[int] = None  # Sheets 行号

    def to_sheets_row(self: "GemMetadata", headers: List[str]) -> List[str]:
        """将 Gem 元数据序列化为 Sheets 行数据"""
        d = self.dimensions
        p = self.performance
        field_map = {
            "gem_id": self.gem_id,
            "gem_name": self.gem_name,
            "gem_type": self.gem_type,
            "gem_internal_name": self.gem_internal_name,
            "is_active": "1" if self.is_active else "0",
            "auto_discovered": "1" if self.auto_discovered else "0",
            "product_categories": ",".join(d.product_categories),
            "price_ranges": ",".join(d.price_ranges),
            "product_features": ",".join(d.product_features),
            "target_audiences": ",".join(d.target_audiences),
            "age_ranges": ",".join(d.age_ranges),
            "gender": d.gender,
            "marketing_scenes": ",".join(d.marketing_scenes),
            "usage_scenes": ",".join(d.usage_scenes),
            "platforms": ",".join(d.platforms),
            "video_styles": ",".join(d.video_styles),
            "tones": ",".join(d.tones),
            "avg_gen_time": str(p.avg_gen_time),
            "usage_count": str(p.usage_count),
            "success_rate": str(p.success_rate),
            "avg_match_score": str(p.avg_match_score),
            "best_for_examples": ",".join(p.best_for_examples),
            "notes": self.notes,
            "created_at": self.created_at or "",
            "updated_at": self.updated_at or "",
        }
        return [field_map.get(h, "") for h in headers]

    @classmethod
    def from_sheets_row(cls: type["GemMetadata"], row: List[str], headers: List[str]) -> "GemMetadata":
        """从 Sheets 行数据反序列化为 GemMetadata"""
        data = dict(zip(headers, row))
        d = MatchDimensions(
            product_categories=_split_csv(data.get("product_categories", "")),
            price_ranges=_split_csv(data.get("price_ranges", "")),
            product_features=_split_csv(data.get("product_features", "")),
            target_audiences=_split_csv(data.get("target_audiences", "")),
            age_ranges=_split_csv(data.get("age_ranges", "")),
            gender=data.get("gender", ""),
            marketing_scenes=_split_csv(data.get("marketing_scenes", "")),
            usage_scenes=_split_csv(data.get("usage_scenes", "")),
            platforms=_split_csv(data.get("platforms", "")),
            video_styles=_split_csv(data.get("video_styles", "")),
            tones=_split_csv(data.get("tones", "")),
        )
        p = GemPerformance(
            avg_gen_time=_parse_float(data.get("avg_gen_time", "0")),
            usage_count=_parse_int(data.get("usage_count", "0")),
            success_rate=_parse_float(data.get("success_rate", "0")),
            avg_match_score=_parse_float(data.get("avg_match_score", "0")),
            best_for_examples=_split_csv(data.get("best_for_examples", "")),
        )
        return cls(
            gem_id=data.get("gem_id", ""),
            gem_name=data.get("gem_name", ""),
            gem_type=data.get("gem_type", ""),
            gem_internal_name=data.get("gem_internal_name", ""),
            is_active=data.get("is_active", "1") == "1",
            auto_discovered=data.get("auto_discovered", "0") == "1",
            source=GemSource.SHEETS.value,
            dimensions=d,
            performance=p,
            notes=data.get("notes", ""),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    @classmethod
    def from_provider_gem(cls: type["GemMetadata"], gem: Any) -> "GemMetadata":
        """从 Provider Gem 对象转换为 GemMetadata"""
        if isinstance(gem, GemMetadata):
            return gem
        if isinstance(gem, dict):
            return cls(
                gem_id=gem.get("id", ""),
                gem_name=gem.get("name", ""),
                gem_type=gem.get("type", ""),
                gem_internal_name=gem.get("internal_name", ""),
                is_active=True,
                source=GemSource.GEMINI.value,
            )
        return cls(
            gem_id=str(gem),
            gem_name="",
            source=GemSource.GEMINI.value,
        )


class GemEvent(BaseModel):
    """Gem 变更事件"""
    event_type: str = Field(..., description="事件类型: added/updated/removed/discovered")
    gem_id: str = Field(..., description="Gem ID")
    gem_name: str = Field(default="", description="Gem 名称")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: Dict[str, Any] = Field(default_factory=dict)


class NewGemNotification(BaseModel):
    """新增 Gem 通知"""
    new_gems: List[GemMetadata] = Field(default_factory=list)
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_count: int = Field(default=0)
    previous_count: int = Field(default=0)

    @property
    def new_count(self: "NewGemNotification") -> int:
        return len(self.new_gems)


class SyncReport(BaseModel):
    """同步报告"""
    status: SyncStatus = Field(default=SyncStatus.IDLE)
    gems_fetched: int = Field(default=0)
    gems_from_sheets: int = Field(default=0)
    gems_added: int = Field(default=0)
    gems_updated: int = Field(default=0)
    gems_unchanged: int = Field(default=0)
    gems_deactivated: int = Field(default=0)
    errors: List[str] = Field(default_factory=list)
    sync_time_ms: int = Field(default=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    @property
    def is_success(self: "SyncReport") -> bool:
        return self.status in (SyncStatus.SUCCESS, SyncStatus.PARTIAL)


class GemFilter(BaseModel):
    """
    Gem 查询过滤条件

    所有字段均为可选，支持组合过滤（AND 逻辑）。
    """
    model_config = {"extra": "ignore"}

    gem_id: Optional[str] = Field(default=None)
    gem_type: Optional[str] = Field(default=None)
    is_active: Optional[bool] = Field(default=None)
    tags: Optional[List[str]] = Field(default=None)
    all_tags: Optional[List[str]] = Field(default=None)
    source: Optional[GemSource] = Field(default=None)
    auto_discovered: Optional[bool] = Field(default=None)
    search_keyword: Optional[str] = Field(default=None)
    product_category: Optional[str] = Field(default=None)
    target_audience: Optional[str] = Field(default=None)
    marketing_scene: Optional[str] = Field(default=None)
    platform: Optional[str] = Field(default=None)
    min_success_rate: Optional[float] = Field(default=None)
    min_usage_count: Optional[int] = Field(default=None)

    def match(self: "GemFilter", gem: GemMetadata) -> bool:
        """判断 Gem 是否满足过滤条件"""
        if self.gem_id is not None and gem.gem_id != self.gem_id:
            return False
        if self.gem_type is not None and gem.gem_type != self.gem_type:
            return False
        if self.is_active is not None and gem.is_active != self.is_active:
            return False
        if self.tags is not None and not any(
            t in gem.dimensions.get_all_tags() for t in self.tags
        ):
            return False
        if self.all_tags is not None and not all(
            t in gem.dimensions.get_all_tags() for t in self.all_tags
        ):
            return False
        if self.source is not None and gem.source != self.source.value:
            return False
        if self.auto_discovered is not None and gem.auto_discovered != self.auto_discovered:
            return False
        if self.search_keyword is not None:
            kw = self.search_keyword.lower()
            searchable = f"{gem.gem_name} {gem.gem_type} {gem.notes}".lower()
            if kw not in searchable:
                return False
        if self.product_category is not None:
            if self.product_category not in gem.dimensions.product_categories:
                return False
        if self.target_audience is not None:
            if self.target_audience not in gem.dimensions.target_audiences:
                return False
        if self.marketing_scene is not None:
            if self.marketing_scene not in gem.dimensions.marketing_scenes:
                return False
        if self.platform is not None:
            if self.platform not in gem.dimensions.platforms:
                return False
        if self.min_success_rate is not None:
            if gem.performance.success_rate < self.min_success_rate:
                return False
        if self.min_usage_count is not None:
            if gem.performance.usage_count < self.min_usage_count:
                return False
        return True


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def _split_csv(value: str) -> List[str]:
    """分割逗号分隔的字符串，去除空白"""
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def _parse_float(value: str) -> float:
    """安全解析浮点数"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _parse_int(value: str) -> int:
    """安全解析整数"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


# ═══════════════════════════════════════════════════════════
# 异常定义
# ═══════════════════════════════════════════════════════════

class GeminiModuleError(Exception):
    """模块基础异常"""
    pass


class NotInitializedError(GeminiModuleError):
    """注册中心未初始化"""
    pass


class GemNotFoundError(GeminiModuleError):
    """未找到指定 Gem"""
    def __init__(self: "GemNotFoundError", gem_id: str) -> None:
        self.gem_id = gem_id
        super().__init__(f"Gem '{gem_id}' 不存在")


class SheetsSyncError(GeminiModuleError):
    """Google Sheets 同步失败"""
    pass


class CacheError(GeminiModuleError):
    """缓存操作失败"""
    pass


class SyncInProgressError(GeminiModuleError):
    """同步正在进行中"""
    pass


# ═══════════════════════════════════════════════════════════
# Sheets 表头定义
# ═══════════════════════════════════════════════════════════

GEM_METADATA_HEADERS: List[str] = [
    "gem_id", "gem_name", "gem_type", "gem_internal_name",
    "is_active", "auto_discovered",
    "product_categories", "price_ranges", "product_features",
    "target_audiences", "age_ranges", "gender",
    "marketing_scenes", "usage_scenes", "platforms",
    "video_styles", "tones",
    "avg_gen_time", "usage_count", "success_rate", "avg_match_score",
    "best_for_examples", "notes", "created_at", "updated_at",
]


# ═══════════════════════════════════════════════════════════
# SheetsManager
# ═══════════════════════════════════════════════════════════

class SheetsManager:
    """
    Google Sheets API V4 封装

    使用 Service Account 认证。
    """

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(
        self,
        spreadsheet_id: str,
        credentials_path: str,
    ) -> None:
        self._spreadsheet_id = spreadsheet_id
        self._credentials_path = credentials_path
        self._service: Optional[Any] = None

    async def initialize(self: "SheetsManager") -> None:
        """初始化 Google API 客户端"""
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            credentials = service_account.Credentials.from_service_account_file(
                self._credentials_path,
                scopes=self.SCOPES,
            )
            self._service = build(
                "sheets", "v4", credentials=credentials, cache_discovery=False
            )
        except ImportError:
            logger.warning("google-api-python-client 未安装，SheetsManager 不可用")

    async def read_range(self: "SheetsManager", sheet_name: str, range_a1: str) -> List[List[str]]:
        """读取指定范围的单元格数据"""
        if not self._service:
            raise SheetsSyncError("SheetsManager 未初始化")

        full_range = f"{sheet_name}!{range_a1}"
        try:
            result = self._service.spreadsheets().values().get(
                spreadsheetId=self._spreadsheet_id,
                range=full_range,
                valueRenderOption="FORMATTED_VALUE",
            ).execute()
            return result.get("values", [])
        except Exception as e:
            raise SheetsSyncError(f"Sheets 读取失败 [{full_range}]: {e}") from e

    async def append_row(self: "SheetsManager", sheet_name: str, row_data: List[str]) -> None:
        """追加一行到表尾"""
        if not self._service:
            return
        try:
            self._service.spreadsheets().values().append(
                spreadsheetId=self._spreadsheet_id,
                range=f"{sheet_name}!A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [row_data]},
            ).execute()
        except Exception as e:
            raise SheetsSyncError(f"Sheets 追加失败: {e}") from e

    async def update_row(
        self, sheet_name: str, row_index: int, row_data: List[str]
    ) -> None:
        """更新指定行"""
        if not self._service:
            return
        range_a1 = f"{sheet_name}!A{row_index}"
        try:
            self._service.spreadsheets().values().update(
                spreadsheetId=self._spreadsheet_id,
                range=range_a1,
                valueInputOption="RAW",
                body={"values": [row_data]},
            ).execute()
        except Exception as e:
            raise SheetsSyncError(f"Sheets 更新失败 [{range_a1}]: {e}") from e

    async def update_by_key(
        self,
        sheet_name: str,
        key_column: int,
        key_value: str,
        row_data: List[str],
    ) -> None:
        """按关键字列查找并更新行"""
        col_letter = chr(ord("A") + key_column)
        values = await self.read_range(
            sheet_name, f"{col_letter}1:{col_letter}1000"
        )
        for i, row in enumerate(values, start=1):
            if row and row[0] == key_value:
                await self.update_row(sheet_name, i, row_data)
                return
        raise SheetsSyncError(f"未找到关键字 '{key_value}' 所在的行")

    async def batch_update(
        self, sheet_name: str, updates: Dict[int, List[str]]
    ) -> None:
        """批量更新多行"""
        if not self._service:
            return
        data = []
        for row_index, row_data in updates.items():
            range_a1 = f"{sheet_name}!A{row_index}"
            data.append({"range": range_a1, "values": [row_data]})

        try:
            self._service.spreadsheets().values().batchUpdate(
                spreadsheetId=self._spreadsheet_id,
                body={"valueInputOption": "RAW", "data": data},
            ).execute()
        except Exception as e:
            raise SheetsSyncError(f"Sheets 批量更新失败: {e}") from e


# ═══════════════════════════════════════════════════════════
# 缓存抽象基类
# ═══════════════════════════════════════════════════════════

class GemCache(ABC):
    """
    Gem 缓存抽象基类

    所有缓存操作均为异步。
    """

    @abstractmethod
    async def get(self: "GemCache", gem_id: GemId) -> Optional[GemMetadata]:
        """按 ID 获取缓存的 Gem"""
        pass

    @abstractmethod
    async def set(self: "GemCache", gem_id: GemId, gem: GemMetadata) -> None:
        """缓存 Gem（覆盖写）"""
        pass

    @abstractmethod
    async def delete(self: "GemCache", gem_id: GemId) -> bool:
        """删除缓存"""
        pass

    @abstractmethod
    async def list_all(self: "GemCache") -> List[GemMetadata]:
        """获取所有缓存的 Gems"""
        pass

    @abstractmethod
    async def clear(self: "GemCache") -> None:
        """清空缓存"""
        pass

    @abstractmethod
    async def get_last_sync_time(self: "GemCache") -> Optional[datetime]:
        """获取最后同步时间"""
        pass

    @abstractmethod
    async def set_last_sync_time(self: "GemCache", dt: datetime) -> None:
        """设置最后同步时间"""
        pass


# ═══════════════════════════════════════════════════════════
# MemoryGemCache 实现
# ═══════════════════════════════════════════════════════════

class MemoryGemCache(GemCache):
    """
    纯内存缓存实现

    注意: 应用重启后数据丢失。
    """

    def __init__(self: "MemoryGemCache") -> None:
        self._store: Dict[GemId, GemMetadata] = {}
        self._last_sync: Optional[datetime] = None

    async def get(self: "MemoryGemCache", gem_id: GemId) -> Optional[GemMetadata]:
        return self._store.get(gem_id)

    async def set(self: "MemoryGemCache", gem_id: GemId, gem: GemMetadata) -> None:
        self._store[gem_id] = gem

    async def delete(self: "MemoryGemCache", gem_id: GemId) -> bool:
        if gem_id in self._store:
            del self._store[gem_id]
            return True
        return False

    async def list_all(self: "MemoryGemCache") -> List[GemMetadata]:
        return list(self._store.values())

    async def clear(self: "MemoryGemCache") -> None:
        self._store.clear()
        self._last_sync = None

    async def get_last_sync_time(self: "MemoryGemCache") -> Optional[datetime]:
        return self._last_sync

    async def set_last_sync_time(self: "MemoryGemCache", dt: datetime) -> None:
        self._last_sync = dt


# ═══════════════════════════════════════════════════════════
# FileGemCache 实现
# ═══════════════════════════════════════════════════════════

class FileGemCache(GemCache):
    """
    基于本地 JSON 文件的 Gem 缓存实现
    """

    def __init__(
        self,
        cache_dir: str = "~/.batch-video-skill/cache",
        filename: str = "gem_registry_cache.json",
    ) -> None:
        self._cache_path = Path(cache_dir).expanduser() / filename
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_buffer: Dict[str, Any] = {}
        self._last_sync: Optional[datetime] = None

    async def get(self: "FileGemCache", gem_id: GemId) -> Optional[GemMetadata]:
        data = await self._load_cache()
        gem_data = data.get("gems", {}).get(gem_id)
        if gem_data:
            return GemMetadata(**gem_data)
        return None

    async def set(self: "FileGemCache", gem_id: GemId, gem: GemMetadata) -> None:
        data = await self._load_cache()
        if "gems" not in data:
            data["gems"] = {}
        data["gems"][gem_id] = gem.model_dump()
        data["_meta"] = {
            "version": 1,
            "count": len(data["gems"]),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._save_cache(data)

    async def delete(self: "FileGemCache", gem_id: GemId) -> bool:
        data = await self._load_cache()
        if gem_id in data.get("gems", {}):
            del data["gems"][gem_id]
            await self._save_cache(data)
            return True
        return False

    async def list_all(self: "FileGemCache") -> List[GemMetadata]:
        data = await self._load_cache()
        gems_data = data.get("gems", {})
        return [GemMetadata(**gd) for gd in gems_data.values()]

    async def clear(self: "FileGemCache") -> None:
        empty = {"_meta": {"version": 1, "count": 0}, "gems": {}}
        await self._save_cache(empty)

    async def get_last_sync_time(self: "FileGemCache") -> Optional[datetime]:
        return self._last_sync

    async def set_last_sync_time(self: "FileGemCache", dt: datetime) -> None:
        self._last_sync = dt

    async def _load_cache(self: "FileGemCache") -> Dict[str, Any]:
        """从文件加载缓存"""
        if self._memory_buffer:
            return self._memory_buffer

        if not self._cache_path.exists():
            return {"_meta": {"version": 1, "count": 0}, "gems": {}}

        try:
            import aiofiles
            async with aiofiles.open(self._cache_path, "r", encoding="utf-8") as f:
                content = await f.read()
                self._memory_buffer = json.loads(content)
                return self._memory_buffer
        except Exception:
            return {"_meta": {"version": 1, "count": 0}, "gems": {}}

    async def _save_cache(self: "FileGemCache", data: Dict[str, Any]) -> None:
        """保存缓存到文件"""
        try:
            import aiofiles
            async with aiofiles.open(self._cache_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        except ImportError:
            # 同步回退
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        self._memory_buffer = data


# ═══════════════════════════════════════════════════════════
# RedisGemCache 实现
# ═══════════════════════════════════════════════════════════

class RedisGemCache(GemCache):
    """
    基于 Redis 的 Gem 缓存实现

    Redis Key 设计:
        - gem:{gem_id} → 存储单个 Gem 元数据 JSON
        - gem:meta → 存储元信息
    """

    PREFIX: str = "gem"
    META_KEY: str = "gem:meta"
    DEFAULT_TTL: int = 7 * 24 * 3600  # 7 天

    def __init__(
        self,
        redis_client: Any,
        prefix: str = "gemini:registry",
        ttl: int = DEFAULT_TTL,
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix
        self._ttl = ttl

    def _key(self: "RedisGemCache", gem_id: str) -> str:
        return f"{self._prefix}:gem:{gem_id}"

    async def get(self: "RedisGemCache", gem_id: GemId) -> Optional[GemMetadata]:
        data = await self._redis.get(self._key(gem_id))
        if data:
            return GemMetadata.model_validate_json(data)
        return None

    async def set(self: "RedisGemCache", gem_id: GemId, gem: GemMetadata) -> None:
        key = self._key(gem_id)
        await self._redis.setex(key, self._ttl, gem.model_dump_json())

    async def delete(self: "RedisGemCache", gem_id: GemId) -> bool:
        result = await self._redis.delete(self._key(gem_id))
        return result > 0

    async def list_all(self: "RedisGemCache") -> List[GemMetadata]:
        pattern = f"{self._prefix}:gem:*"
        keys = []
        async for key in self._redis.scan_iter(match=pattern):
            keys.append(key)

        gems: List[GemMetadata] = []
        if keys:
            values = await self._redis.mget(keys)
            for val in values:
                if val:
                    gems.append(GemMetadata.model_validate_json(val))
        return gems

    async def clear(self: "RedisGemCache") -> None:
        pattern = f"{self._prefix}:gem:*"
        async for key in self._redis.scan_iter(match=pattern):
            await self._redis.delete(key)

    async def get_last_sync_time(self: "RedisGemCache") -> Optional[datetime]:
        data = await self._redis.hget(f"{self._prefix}:meta", "last_sync_at")
        if data:
            return datetime.fromisoformat(data)
        return None

    async def set_last_sync_time(self: "RedisGemCache", dt: datetime) -> None:
        await self._redis.hset(
            f"{self._prefix}:meta", "last_sync_at", dt.isoformat()
        )


# ═══════════════════════════════════════════════════════════
# GemRegistry 主类
# ═══════════════════════════════════════════════════════════

class GemRegistry:
    """
    Gem 注册中心 — 统一管理 Gem 的发现、同步、缓存和查询

    核心特性:
        1. 多级缓存: 内存 L1 + 持久化 L2
        2. Sheets 同步: 双向同步 Google Sheets
        3. 新增检测: 自动发现新 Gem
        4. 并发安全: asyncio.RLock
        5. 事件驱动: 同步钩子
    """

    # ─── 类常量 ───
    DEFAULT_SYNC_INTERVAL_SEC: int = 300
    MAX_SYNC_TIME_MS: int = 5000
    SHEET_NAME: str = "gem-metadata"
    SHEET_RANGE: str = "A1:Z1000"
    CACHE_KEY_PREFIX: str = "gem"
    CACHE_VERSION: int = 1

    def __init__(
        self,
        sheets_manager: Optional[SheetsManager] = None,
        cache: Optional[GemCache] = None,
        sync_interval_sec: int = DEFAULT_SYNC_INTERVAL_SEC,
        auto_sync: bool = True,
        enable_new_gem_detection: bool = True,
        notification_callback: Optional[
            Callable[["NewGemNotification"], Coroutine[Any, Any, None]]
        ] = None,
    ) -> None:
        """
        初始化 GemRegistry

        参数:
            sheets_manager: Google Sheets 管理器
            cache: 缓存实现
            sync_interval_sec: 自动同步间隔（秒）
            auto_sync: 是否启用自动定时同步
            enable_new_gem_detection: 是否启用新增 Gem 检测
            notification_callback: 新增 Gem 通知回调协程
        """
        # 依赖组件
        self._sheets: Optional[SheetsManager] = sheets_manager
        self._cache: GemCache = cache or MemoryGemCache()
        self._provider: Optional[Any] = None

        # 配置参数
        self._sync_interval_sec: int = max(sync_interval_sec, 60)
        self._auto_sync: bool = auto_sync
        self._enable_detection: bool = enable_new_gem_detection
        self._notification_callback: Optional[Callable] = notification_callback

        # 内部状态
        self._gems: Dict[GemId, GemMetadata] = {}
        self._initialized: bool = False
        self._last_sync_at: Optional[datetime] = None
        self._last_sync_report: Optional[SyncReport] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._hooks: List[SyncHook] = []
        self._lock: asyncio.Lock = asyncio.Lock()

        # 新增检测状态
        self._known_gem_ids: Set[GemId] = set()
        self._new_gem_history: List[GemEvent] = []

    # ─── 属性访问 ───

    @property
    def is_initialized(self: "GemRegistry") -> bool:
        """是否已完成初始化"""
        return self._initialized

    @property
    def gem_count(self: "GemRegistry") -> int:
        """当前注册的 Gem 数量"""
        return len(self._gems)

    @property
    def last_sync_at(self: "GemRegistry") -> Optional[datetime]:
        """最后同步时间（UTC）"""
        return self._last_sync_at

    @property
    def last_sync_report(self: "GemRegistry") -> Optional[SyncReport]:
        """最后同步报告"""
        return self._last_sync_report

    @property
    def is_auto_sync_running(self: "GemRegistry") -> bool:
        """自动同步任务是否运行中"""
        return self._sync_task is not None and not self._sync_task.done()

    # ═══════════════════════════════════════════════════════════
    # 生命周期管理
    # ═══════════════════════════════════════════════════════════

    async def initialize(
        self,
        provider: Any,
        force_sync: bool = False,
    ) -> SyncReport:
        """
        初始化注册中心

        流程:
            1. 设置 Provider 引用
            2. 从缓存加载已有数据
            3. 执行首次同步
            4. 启动自动同步定时任务
        """
        async with self._lock:
            if self._initialized:
                return self._last_sync_report or SyncReport()

            self._provider = provider

            # Step 1: 从持久化缓存恢复
            await self._restore_from_cache()

            # Step 2: 首次同步
            try:
                report = await self._do_sync(force=force_sync)
                self._last_sync_report = report
            except Exception as e:
                logger.error(f"首次同步失败: {e}")
                report = SyncReport(status=SyncStatus.FAILED, errors=[str(e)])
                self._last_sync_report = report

            # Step 3: 初始化已知 ID 集合
            self._known_gem_ids = set(self._gems.keys())

            # Step 4: 标记初始化完成
            self._initialized = True

            # Step 5: 启动自动同步
            if self._auto_sync:
                self._start_periodic_sync()

            logger.info(
                f"GemRegistry 初始化完成: gems={self.gem_count}, "
                f"sync_status={report.status.value}, auto_sync={self._auto_sync}"
            )
            return report

    async def close(self: "GemRegistry") -> None:
        """
        关闭注册中心，释放资源
        """
        # Step 1: 停止定时任务
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None

        # Step 2: 持久化到缓存
        try:
            await self._persist_to_cache()
        except Exception as e:
            logger.warning(f"关闭时缓存持久化失败: {e}")

        # Step 3: 清理状态
        self._gems.clear()
        self._initialized = False
        self._provider = None
        self._known_gem_ids.clear()

        logger.info("GemRegistry 已关闭")

    # ═══════════════════════════════════════════════════════════
    # 核心同步 API
    # ═══════════════════════════════════════════════════════════

    async def load_gems(self: "GemRegistry", force_refresh: bool = False) -> List[GemMetadata]:
        """
        加载所有 Gem 元数据

        参数:
            force_refresh: 是否强制刷新

        返回:
            按 gem_id 排序的 Gem 元数据列表
        """
        self._ensure_initialized()

        if force_refresh or not self._gems:
            await self.sync_gems()

        return sorted(self._gems.values(), key=lambda g: g.gem_id)

    async def sync_gems(self: "GemRegistry", force: bool = False) -> SyncReport:
        """
        执行完整同步流程

        同步流程:
            1. 从 Provider 获取当前可用 Gems
            2. 从 Google Sheets 读取业务元数据
            3. 合并数据（Sheets 优先级更高）
            4. 检测新增 Gems
            5. 更新内存索引
            6. 持久化到缓存
            7. 触发同步钩子

        参数:
            force: 是否强制同步

        返回:
            同步报告
        """
        self._ensure_initialized()

        if not force and not self._should_sync():
            return self._last_sync_report or SyncReport(status=SyncStatus.IDLE)

        async with self._lock:
            try:
                report = await self._do_sync(force=force)
                self._last_sync_report = report
                self._last_sync_at = datetime.now(timezone.utc)
                return report
            except Exception as e:
                logger.error(f"同步异常: {e}")
                report = SyncReport(
                    status=SyncStatus.FAILED,
                    errors=[str(e)],
                    completed_at=datetime.now(timezone.utc),
                )
                self._last_sync_report = report
                return report

    # ═══════════════════════════════════════════════════════════
    # 查询 API
    # ═══════════════════════════════════════════════════════════

    def find_gem(self: "GemRegistry", gem_id: GemId) -> Optional[GemMetadata]:
        """
        按 ID 精确查找 Gem

        参数:
            gem_id: Gem 唯一标识

        返回:
            Gem 元数据，未找到返回 None

        时间复杂度: O(1)
        """
        return self._gems.get(gem_id)

    def list_gems(
        self,
        filters: Optional[GemFilter] = None,
        sort_by: str = "gem_id",
        descending: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[GemMetadata]:
        """
        按条件过滤查询 Gems

        参数:
            filters: 过滤条件
            sort_by: 排序字段
            descending: 是否降序
            limit: 最大返回数量
            offset: 跳过数量

        返回:
            符合条件的 Gem 列表
        """
        if filters is not None:
            results = [g for g in self._gems.values() if filters.match(g)]
        else:
            results = list(self._gems.values())

        # 排序
        if sort_by == "gem_id":
            results.sort(key=lambda g: g.gem_id, reverse=descending)
        elif sort_by == "gem_name":
            results.sort(key=lambda g: g.gem_name, reverse=descending)
        elif sort_by == "gem_type":
            results.sort(key=lambda g: g.gem_type, reverse=descending)
        elif sort_by == "updated_at":
            results.sort(
                key=lambda g: g.updated_at or g.created_at or "",
                reverse=descending,
            )
        elif sort_by == "success_rate":
            results.sort(
                key=lambda g: g.performance.success_rate, reverse=descending
            )
        elif sort_by == "usage_count":
            results.sort(
                key=lambda g: g.performance.usage_count, reverse=descending
            )
        else:
            results.sort(key=lambda g: g.gem_id, reverse=descending)

        # 分页
        if offset > 0:
            results = results[offset:]
        if limit is not None:
            results = results[:limit]

        return results

    def find_gems_by_tag(self: "GemRegistry", tag: Tag) -> List[GemMetadata]:
        """
        按业务标签查找 Gems（模糊匹配）

        参数:
            tag: 业务标签

        返回:
            包含该标签的 Gem 列表
        """
        return [
            g for g in self._gems.values()
            if tag in g.dimensions.get_all_tags()
        ]

    def find_gems_for_product(
        self,
        product_category: str,
        marketing_scene: Optional[str] = None,
        target_audience: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> List[GemMetadata]:
        """
        根据产品特征查找最匹配的 Gems

        参数:
            product_category: 产品品类（必填）
            marketing_scene: 营销场景（可选）
            target_audience: 目标人群（可选）
            platform: 内容平台（可选）

        返回:
            按匹配度排序的 Gem 列表
        """
        candidates: List[GemMetadata] = []

        for gem in self._gems.values():
            if not gem.is_active:
                continue
            if product_category not in gem.dimensions.product_categories:
                continue
            candidates.append(gem)

        def score(gem: GemMetadata) -> float:
            s = 1.0
            if marketing_scene and marketing_scene in gem.dimensions.marketing_scenes:
                s += 0.5
            if target_audience and target_audience in gem.dimensions.target_audiences:
                s += 0.3
            if platform and platform in gem.dimensions.platforms:
                s += 0.2
            s += gem.performance.success_rate * 0.2
            s += min(gem.performance.usage_count / 100, 1.0) * 0.1
            return s

        candidates.sort(key=score, reverse=True)
        return candidates

    # ═══════════════════════════════════════════════════════════
    # 新增检测 API
    # ═══════════════════════════════════════════════════════════

    def detect_new_gems(self: "GemRegistry") -> List[str]:
        """
        检测新发现的 Gem ID 列表

        对比当前内存中的 Gems 和上一次同步时已知 ID 集合，
        返回当前存在但之前不存在的 Gem ID。

        返回:
            新 Gem ID 列表（已排序）

        注意:
            此方法仅做内存比较，不触发远程获取。
        """
        current_ids = set(self._gems.keys())
        new_ids = list(current_ids - self._known_gem_ids)
        return sorted(new_ids)

    async def acknowledge_new_gem(self: "GemRegistry", gem_id: GemId) -> None:
        """
        确认已知晓某个新 Gem，将其加入已知集合

        参数:
            gem_id: 已确认的 Gem ID
        """
        self._known_gem_ids.add(gem_id)
        logger.debug(f"已确认新 Gem: {gem_id}")

    async def acknowledge_all_new_gems(self: "GemRegistry") -> None:
        """确认所有当前 Gems 为已知"""
        self._known_gem_ids = set(self._gems.keys())
        logger.info(f"已确认所有 {len(self._known_gem_ids)} 个 Gems")

    # ═══════════════════════════════════════════════════════════
    # 元数据修改 API
    # ═══════════════════════════════════════════════════════════

    async def add_gem_metadata(
        self,
        gem: GemMetadata,
        sync_to_sheets: bool = True,
    ) -> None:
        """
        添加新的 Gem 元数据

        参数:
            gem: Gem 元数据
            sync_to_sheets: 是否同步写入 Sheets

        异常:
            ValueError: gem_id 为空或已存在
            SheetsSyncError: Sheets 写入失败
        """
        if not gem.gem_id:
            raise ValueError("gem_id 不能为空")

        async with self._lock:
            if gem.gem_id in self._gems:
                raise ValueError(
                    f"Gem '{gem.gem_id}' 已存在，请使用 update_gem_metadata"
                )

            now = datetime.now(timezone.utc).isoformat()
            if not gem.created_at:
                gem.created_at = now
            gem.updated_at = now

            self._gems[gem.gem_id] = gem

            if sync_to_sheets and self._sheets:
                try:
                    await self._append_to_sheets(gem)
                except Exception as e:
                    logger.error(f"添加 Gem 到 Sheets 失败: {e}")
                    raise SheetsSyncError(f"Sheets 追加失败: {e}") from e

            self._known_gem_ids.add(gem.gem_id)
            await self._persist_to_cache()

        logger.info(f"添加 Gem 成功: {gem.gem_id} ({gem.gem_name})")

    async def update_gem_metadata(
        self,
        gem_id: GemId,
        updates: Dict[str, Any],
        sync_to_sheets: bool = True,
    ) -> GemMetadata:
        """
        更新 Gem 元数据（部分更新）

        参数:
            gem_id: 要更新的 Gem ID
            updates: 更新字段字典（支持嵌套更新 dimensions.*, performance.*）
            sync_to_sheets: 是否同步更新 Sheets

        返回:
            更新后的 Gem 元数据

        异常:
            GemNotFoundError: Gem 不存在
            ValueError: 更新字段无效
        """
        async with self._lock:
            if gem_id not in self._gems:
                raise GemNotFoundError(gem_id)

            gem = self._gems[gem_id]

            for key, value in updates.items():
                if "." in key:
                    parts = key.split(".")
                    obj = gem
                    for part in parts[:-1]:
                        obj = getattr(obj, part)
                    setattr(obj, parts[-1], value)
                elif hasattr(gem, key):
                    setattr(gem, key, value)
                else:
                    raise ValueError(f"无效字段: {key}")

            gem.updated_at = datetime.now(timezone.utc).isoformat()

            if sync_to_sheets and self._sheets:
                try:
                    await self._update_sheets_row(gem)
                except Exception as e:
                    logger.error(f"更新 Sheets 失败: {e}")
                    raise SheetsSyncError(f"Sheets 更新失败: {e}") from e

            await self._persist_to_cache()

        logger.info(f"更新 Gem 成功: {gem_id}")
        return gem

    async def deactivate_gem(
        self,
        gem_id: GemId,
        reason: str = "",
        sync_to_sheets: bool = True,
    ) -> None:
        """
        停用 Gem（软删除，设置 is_active=False）

        参数:
            gem_id: 要停用的 Gem ID
            reason: 停用原因
            sync_to_sheets: 是否同步到 Sheets
        """
        await self.update_gem_metadata(
            gem_id,
            {"is_active": False, "notes": reason},
            sync_to_sheets=sync_to_sheets,
        )
        logger.info(f"停用 Gem: {gem_id}, 原因: {reason}")

    # ═══════════════════════════════════════════════════════════
    # 事件钩子
    # ═══════════════════════════════════════════════════════════

    def register_sync_hook(self: "GemRegistry", hook: SyncHook) -> None:
        """
        注册同步钩子，在每次同步完成后调用

        参数:
            hook: 异步回调函数 (added_gems, updated_gems) -> None
        """
        self._hooks.append(hook)

    def unregister_sync_hook(self: "GemRegistry", hook: SyncHook) -> bool:
        """
        注销同步钩子

        返回:
            是否成功找到并移除
        """
        if hook in self._hooks:
            self._hooks.remove(hook)
            return True
        return False

    # ═══════════════════════════════════════════════════════════
    # 内部方法 — 同步核心逻辑
    # ═══════════════════════════════════════════════════════════

    async def _do_sync(self: "GemRegistry", force: bool = False) -> SyncReport:
        """
        执行同步的核心逻辑

        算法:
            1. gems_provider = provider.list_gems()
            2. gems_sheets = sheets.read_range()
            3. merged = merge(gems_provider, gems_sheets)
            4. new_ids = detect_new(merged, _gems)
            5. update_memory_index(merged)
            6. write_cache(merged)
            7. notify(new_ids)
            8. return report
        """
        start_time = time.monotonic()
        report = SyncReport(
            status=SyncStatus.SYNCING,
            started_at=datetime.now(timezone.utc),
        )

        gems_provider: List[GemMetadata] = []
        gems_sheets: List[GemMetadata] = []

        # Step 1: 从 Provider 获取
        try:
            if self._provider:
                raw_gems = await self._provider.list_gems()
                gems_provider = [GemMetadata.from_provider_gem(g) for g in raw_gems]
                report.gems_fetched = len(gems_provider)
                logger.debug(f"从 Provider 获取 {len(gems_provider)} 个 Gems")
        except Exception as e:
            logger.warning(f"从 Provider 获取 Gems 失败: {e}")
            report.errors.append(f"Provider fetch failed: {e}")

        # Step 2: 从 Sheets 读取
        try:
            if self._sheets:
                gems_sheets = await self._read_from_sheets()
                report.gems_from_sheets = len(gems_sheets)
                logger.debug(f"从 Sheets 读取 {len(gems_sheets)} 个 Gems")
        except Exception as e:
            logger.warning(f"从 Sheets 读取失败: {e}")
            report.errors.append(f"Sheets read failed: {e}")

        # Step 3: 合并数据
        old_gems = dict(self._gems)
        merged_gems = self._merge_metadata(gems_provider, gems_sheets)

        # Step 4: 对比变化
        added_gems: List[GemMetadata] = []
        updated_gems: List[GemMetadata] = []
        unchanged_count = 0

        for gem_id, new_gem in merged_gems.items():
            if gem_id not in old_gems:
                added_gems.append(new_gem)
            elif self._gem_changed(old_gems[gem_id], new_gem):
                updated_gems.append(new_gem)
            else:
                unchanged_count += 1

        # 检测被移除的
        deactivated_count = 0
        for gem_id, old_gem in old_gems.items():
            if gem_id not in merged_gems and old_gem.is_active:
                old_gem.is_active = False
                merged_gems[gem_id] = old_gem
                deactivated_count += 1

        # Step 5: 更新内存索引
        self._gems = merged_gems

        report.gems_added = len(added_gems)
        report.gems_updated = len(updated_gems)
        report.gems_unchanged = unchanged_count
        report.gems_deactivated = deactivated_count

        # Step 6: 持久化缓存
        try:
            await self._persist_to_cache()
        except Exception as e:
            logger.warning(f"缓存持久化失败: {e}")
            report.errors.append(f"Cache persist failed: {e}")

        # Step 7: 触发同步钩子
        if added_gems or updated_gems:
            await self._trigger_hooks(added_gems, updated_gems)

        # Step 8: 新增 Gem 检测和通知
        if self._enable_detection:
            new_ids = self.detect_new_gems()
            if new_ids:
                new_gem_list = [
                    self._gems[g_id] for g_id in new_ids if g_id in self._gems
                ]
                await self._notify_new_gems(new_gem_list)
                self._known_gem_ids.update(new_ids)

        # 完成报告
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        report.sync_time_ms = elapsed_ms
        report.completed_at = datetime.now(timezone.utc)
        report.status = SyncStatus.PARTIAL if report.errors else SyncStatus.SUCCESS

        logger.info(
            f"同步完成: {report.gems_added}+ {report.gems_updated}~ "
            f"{report.gems_unchanged}= {elapsed_ms}ms"
        )
        return report

    def _merge_metadata(
        self,
        gems_provider: List[GemMetadata],
        gems_sheets: List[GemMetadata],
    ) -> Dict[GemId, GemMetadata]:
        """
        合并 Provider 和 Sheets 的 Gem 数据

        合并策略（Sheets 数据优先）:
            1. 以 Provider 数据为基础构建索引
            2. 用 Sheets 数据覆盖对应字段
            3. Sheets 中不存在的 Provider Gem 自动追加
            4. Sheets 中存在但 Provider 中不存在的 Gem 保留
        """
        merged: Dict[GemId, GemMetadata] = {}

        # 先用 Sheets 数据建立基础
        for gem in gems_sheets:
            if gem.gem_id:
                gem.source = GemSource.SHEETS.value
                merged[gem.gem_id] = gem

        # 用 Provider 数据补充/覆盖
        for gem in gems_provider:
            if not gem.gem_id:
                continue

            if gem.gem_id in merged:
                existing = merged[gem.gem_id]
                existing.gem_name = gem.gem_name or existing.gem_name
                existing.gem_internal_name = (
                    gem.gem_internal_name or existing.gem_internal_name
                )
                existing.source = GemSource.SHEETS.value
            else:
                gem.source = GemSource.GEMINI.value
                gem.auto_discovered = True
                gem.created_at = datetime.now(timezone.utc).isoformat()
                merged[gem.gem_id] = gem

        return merged

    def _gem_changed(self: "GemRegistry", old: GemMetadata, new: GemMetadata) -> bool:
        """
        判断两个 Gem 元数据是否发生变化

        比较关键业务字段，忽略时间戳等运行时字段。
        """
        if old.gem_name != new.gem_name:
            return True
        if old.gem_type != new.gem_type:
            return True
        if old.is_active != new.is_active:
            return True
        if old.dimensions.to_flat_dict() != new.dimensions.to_flat_dict():
            return True
        if old.performance.success_rate != new.performance.success_rate:
            return True
        if old.performance.usage_count != new.performance.usage_count:
            return True
        return False

    def _should_sync(self: "GemRegistry") -> bool:
        """判断是否应该执行同步（基于时间间隔）"""
        if self._last_sync_at is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self._last_sync_at).total_seconds()
        return elapsed >= self._sync_interval_sec

    # ═══════════════════════════════════════════════════════════
    # 内部方法 — Sheets 操作
    # ═══════════════════════════════════════════════════════════

    async def _read_from_sheets(self: "GemRegistry") -> List[GemMetadata]:
        """
        从 Google Sheets 读取 Gem 元数据

        返回:
            GemMetadata 列表
        """
        if not self._sheets:
            return []

        try:
            rows = await self._sheets.read_range(
                sheet_name=self.SHEET_NAME,
                range_a1=self.SHEET_RANGE,
            )

            if not rows or len(rows) < 2:
                return []

            headers = rows[0]
            gems: List[GemMetadata] = []
            for i, row in enumerate(rows[1:], start=2):
                if not row or not row[0]:
                    continue
                try:
                    gem = GemMetadata.from_sheets_row(row, headers)
                    gem.row_index = i
                    gems.append(gem)
                except Exception as e:
                    logger.warning(f"解析 Sheets 第 {i} 行失败: {e}")

            return gems

        except Exception as e:
            raise SheetsSyncError(f"Sheets 读取失败: {e}") from e

    async def _append_to_sheets(self: "GemRegistry", gem: GemMetadata) -> None:
        """向 Sheets 追加新 Gem 行"""
        if not self._sheets:
            return
        row = gem.to_sheets_row(GEM_METADATA_HEADERS)
        await self._sheets.append_row(sheet_name=self.SHEET_NAME, row_data=row)
        logger.debug(f"已追加 Gem 到 Sheets: {gem.gem_id}")

    async def _update_sheets_row(self: "GemRegistry", gem: GemMetadata) -> None:
        """更新 Sheets 中指定 Gem 的行数据"""
        if not self._sheets:
            return
        row = gem.to_sheets_row(GEM_METADATA_HEADERS)
        if gem.row_index:
            await self._sheets.update_row(
                sheet_name=self.SHEET_NAME,
                row_index=gem.row_index,
                row_data=row,
            )
        else:
            await self._sheets.update_by_key(
                sheet_name=self.SHEET_NAME,
                key_column=0,
                key_value=gem.gem_id,
                row_data=row,
            )
        logger.debug(f"已更新 Sheets 行: {gem.gem_id}")

    # ═══════════════════════════════════════════════════════════
    # 内部方法 — 缓存操作
    # ═══════════════════════════════════════════════════════════

    async def _restore_from_cache(self: "GemRegistry") -> None:
        """从持久化缓存恢复数据到内存"""
        try:
            cached_gems = await self._cache.list_all()
            if cached_gems:
                self._gems = {g.gem_id: g for g in cached_gems if g.gem_id}
                self._known_gem_ids = set(self._gems.keys())
                logger.info(f"从缓存恢复 {len(self._gems)} 个 Gems")
        except Exception as e:
            logger.warning(f"缓存恢复失败: {e}")

    async def _persist_to_cache(self: "GemRegistry") -> None:
        """将内存数据持久化到缓存"""
        try:
            for gem in self._gems.values():
                await self._cache.set(gem.gem_id, gem)
            logger.debug(f"已持久化 {len(self._gems)} 个 Gems 到缓存")
        except Exception as e:
            logger.warning(f"缓存持久化失败: {e}")

    # ═══════════════════════════════════════════════════════════
    # 内部方法 — 通知
    # ═══════════════════════════════════════════════════════════

    async def _notify_new_gems(self: "GemRegistry", new_gems: List[GemMetadata]) -> None:
        """
        通知业务层有新 Gem 发现

        通知渠道:
            1. 结构化日志
            2. 注册的通知回调
            3. 同步钩子
        """
        if not new_gems:
            return

        gem_names = [f"{g.gem_id}({g.gem_name})" for g in new_gems]
        logger.info(f"发现 {len(new_gems)} 个新 Gem: {', '.join(gem_names)}")

        for gem in new_gems:
            event = GemEvent(
                event_type="discovered",
                gem_id=gem.gem_id,
                gem_name=gem.gem_name,
                details={"source": gem.source, "type": gem.gem_type},
            )
            self._new_gem_history.append(event)

        if self._notification_callback:
            try:
                notification = NewGemNotification(
                    new_gems=new_gems,
                    total_count=self.gem_count,
                    previous_count=self.gem_count - len(new_gems),
                )
                await self._notification_callback(notification)
            except Exception as e:
                logger.error(f"通知回调执行失败: {e}")

    async def _trigger_hooks(
        self,
        added: List[GemMetadata],
        updated: List[GemMetadata],
    ) -> None:
        """触发所有注册的同步钩子"""
        for hook in self._hooks:
            try:
                await hook(added, updated)
            except Exception as e:
                logger.error(f"同步钩子执行失败: {e}")

    # ═══════════════════════════════════════════════════════════
    # 内部方法 — 定时同步
    # ═══════════════════════════════════════════════════════════

    def _start_periodic_sync(self: "GemRegistry") -> None:
        """启动自动定时同步任务"""
        if self._sync_task and not self._sync_task.done():
            return

        self._sync_task = asyncio.create_task(
            self._periodic_sync_loop(),
            name="gemregistry-periodic-sync",
        )
        logger.debug(f"自动同步已启动，间隔: {self._sync_interval_sec}s")

    async def _periodic_sync_loop(self: "GemRegistry") -> None:
        """定时同步循环协程"""
        while True:
            try:
                await asyncio.sleep(self._sync_interval_sec)
                report = await self.sync_gems()
                if report.is_success:
                    logger.debug(
                        f"自动同步成功: +{report.gems_added} ~{report.gems_updated}"
                    )
                else:
                    logger.warning(f"自动同步异常: {report.errors}")
            except asyncio.CancelledError:
                logger.debug("定时同步任务已取消")
                break
            except Exception as e:
                logger.error(f"定时同步循环异常: {e}")
                await asyncio.sleep(60)

    # ═══════════════════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════════════════

    def _ensure_initialized(self: "GemRegistry") -> None:
        """确保已初始化"""
        if not self._initialized:
            raise NotInitializedError("GemRegistry 未初始化，请先调用 initialize()")

    def get_stats(self: "GemRegistry") -> Dict[str, Any]:
        """
        获取注册中心统计信息

        返回:
            统计字典
        """
        active_gems = [g for g in self._gems.values() if g.is_active]
        type_distribution: Dict[str, int] = {}
        for g in self._gems.values():
            type_distribution[g.gem_type] = type_distribution.get(g.gem_type, 0) + 1

        return {
            "total_gems": len(self._gems),
            "active_gems": len(active_gems),
            "type_distribution": type_distribution,
            "last_sync_at": (
                self._last_sync_at.isoformat() if self._last_sync_at else None
            ),
            "auto_sync_running": self.is_auto_sync_running,
            "known_gem_ids": len(self._known_gem_ids),
            "new_gem_events": len(self._new_gem_history),
        }


# ═══════════════════════════════════════════════════════════
# 测试代码
# ═══════════════════════════════════════════════════════════

async def _test():
    """内部测试 — 验证 GemRegistry 核心功能"""
    print("=" * 60)
    print("GemRegistry 注册中心测试")
    print("=" * 60)

    # 1. 测试 MemoryGemCache
    cache = MemoryGemCache()
    gem = GemMetadata(
        gem_id="gem_001",
        gem_name="测试Gem",
        gem_type="促销类",
        is_active=True,
        dimensions=MatchDimensions(product_categories=["食品", "零食"]),
        performance=GemPerformance(usage_count=50, success_rate=0.92),
    )
    await cache.set("gem_001", gem)
    cached = await cache.get("gem_001")
    assert cached is not None
    assert cached.gem_name == "测试Gem"
    all_gems = await cache.list_all()
    assert len(all_gems) == 1
    print("[1] MemoryGemCache 基础 CRUD: 通过")

    # 2. 测试 GemFilter
    filter_active = GemFilter(is_active=True)
    assert filter_active.match(gem) is True
    filter_inactive = GemFilter(is_active=False)
    assert filter_inactive.match(gem) is False
    filter_type = GemFilter(gem_type="促销类")
    assert filter_type.match(gem) is True
    filter_type_bad = GemFilter(gem_type="品牌类")
    assert filter_type_bad.match(gem) is False
    filter_rate = GemFilter(min_success_rate=0.9)
    assert filter_rate.match(gem) is True
    filter_rate_bad = GemFilter(min_success_rate=0.95)
    assert filter_rate_bad.match(gem) is False
    print("[2] GemFilter 过滤逻辑: 通过")

    # 3. 测试 GemMetadata Sheets 序列化/反序列化
    row = gem.to_sheets_row(GEM_METADATA_HEADERS)
    assert len(row) == len(GEM_METADATA_HEADERS)
    assert row[GEM_METADATA_HEADERS.index("gem_id")] == "gem_001"
    assert row[GEM_METADATA_HEADERS.index("gem_name")] == "测试Gem"

    restored = GemMetadata.from_sheets_row(row, GEM_METADATA_HEADERS)
    assert restored.gem_id == "gem_001"
    assert "食品" in restored.dimensions.product_categories
    assert restored.performance.usage_count == 50
    assert abs(restored.performance.success_rate - 0.92) < 0.01
    print("[3] GemMetadata Sheets 序列化/反序列化: 通过")

    # 4. 测试 MatchDimensions
    dims = MatchDimensions(
        product_categories=["美妆", "护肤"],
        platforms=["抖音", "小红书"],
    )
    tags = dims.get_all_tags()
    assert "美妆" in tags
    assert "抖音" in tags
    print("[4] MatchDimensions 标签提取: 通过")

    # 5. 测试 GemRegistry 初始化和 CRUD
    notified: List[NewGemNotification] = []

    async def on_notify(n: NewGemNotification) -> None:
        notified.append(n)

    # Mock Provider
    class MockProvider:
        async def list_gems(self: "MockProvider"):
            return []

    provider = MockProvider()

    registry = GemRegistry(
        sheets_manager=None,
        cache=MemoryGemCache(),
        auto_sync=False,
        enable_new_gem_detection=True,
        notification_callback=on_notify,
    )

    report = await registry.initialize(provider=provider)
    assert registry.is_initialized is True
    assert registry.gem_count == 0
    assert report.status == SyncStatus.SUCCESS
    print("[5] GemRegistry 初始化: 通过")

    # 6. 测试 add_gem_metadata
    await registry.add_gem_metadata(gem, sync_to_sheets=False)
    assert registry.gem_count == 1
    found = registry.find_gem("gem_001")
    assert found is not None
    assert found.gem_name == "测试Gem"
    print("[6] add_gem_metadata: 通过")

    # 7. 测试重复添加
    try:
        await registry.add_gem_metadata(gem, sync_to_sheets=False)
        assert False, "应该抛出 ValueError"
    except ValueError as e:
        assert "已存在" in str(e)
    print("[7] 重复添加检测: 通过")

    # 8. 测试 update_gem_metadata
    updated = await registry.update_gem_metadata(
        "gem_001",
        {"gem_name": "新名称", "is_active": False},
        sync_to_sheets=False,
    )
    assert updated.gem_name == "新名称"
    assert updated.is_active is False
    print("[8] update_gem_metadata: 通过")

    # 9. 测试嵌套字段更新
    updated2 = await registry.update_gem_metadata(
        "gem_001",
        {"dimensions.product_categories": ["新品类"]},
        sync_to_sheets=False,
    )
    assert "新品类" in updated2.dimensions.product_categories
    print("[9] 嵌套字段更新: 通过")

    # 10. 测试 deactivate_gem
    await registry.deactivate_gem("gem_001", reason="测试停用", sync_to_sheets=False)
    deactivated = registry.find_gem("gem_001")
    assert deactivated is not None
    assert deactivated.is_active is False
    print("[10] deactivate_gem: 通过")

    # 11. 测试 list_gems 过滤和排序
    # 添加更多测试数据
    for i in range(5):
        g = GemMetadata(
            gem_id=f"gem_{i+10:03d}",
            gem_name=f"Gem {i}",
            gem_type="促销类" if i % 2 == 0 else "品牌类",
            is_active=True,
            performance=GemPerformance(success_rate=0.5 + i * 0.1),
        )
        await registry.add_gem_metadata(g, sync_to_sheets=False)

    results = registry.list_gems(GemFilter(gem_type="促销类"))
    assert len(results) == 4  # gem_001, gem_010, gem_012, gem_014 (含已停用)
    print(f"[11] list_gems 过滤: 找到 {len(results)} 个促销类 Gem")

    # 12. 测试排序
    sorted_results = registry.list_gems(
        GemFilter(), sort_by="success_rate", descending=True
    )
    rates = [r.performance.success_rate for r in sorted_results]
    assert rates == sorted(rates, reverse=True)
    print("[12] list_gems 排序: 通过")

    # 13. 测试分页
    page1 = registry.list_gems(None, limit=3, offset=0)
    page2 = registry.list_gems(None, limit=3, offset=3)
    assert len(page1) == 3
    assert len(page2) >= 1
    print(f"[13] 分页: page1={len(page1)}, page2={len(page2)}")

    # 14. 测试 find_gems_by_tag
    # gem_001 的 product_categories 在步骤9被更新为 ["新品类"]
    gems_with_新品类 = registry.find_gems_by_tag("新品类")
    assert len(gems_with_新品类) >= 1
    print(f"[14] find_gems_by_tag('新品类'): 找到 {len(gems_with_新品类)} 个")

    # 15. 测试 stats
    stats = registry.get_stats()
    assert stats["total_gems"] == registry.gem_count
    assert "active_gems" in stats
    assert "type_distribution" in stats
    print(f"[15] 统计: {stats}")

    # 16. 测试 close
    await registry.close()
    assert registry.is_initialized is False
    print("[16] close: 通过")

    # 17. 测试 SyncReport
    report = SyncReport(
        status=SyncStatus.SUCCESS,
        gems_added=2,
        gems_updated=1,
        sync_time_ms=1500,
    )
    assert report.is_success is True
    assert report.gems_added == 2
    print("[17] SyncReport: 通过")

    # 18. 测试 NewGemNotification
    notification = NewGemNotification(
        new_gems=[gem],
        total_count=10,
        previous_count=9,
    )
    assert notification.new_count == 1
    print("[18] NewGemNotification: 通过")

    # 19. 测试 GemEvent
    event = GemEvent(
        event_type="discovered",
        gem_id="gem_999",
        gem_name="测试事件",
    )
    assert event.event_type == "discovered"
    print("[19] GemEvent: 通过")

    # 20. 测试 FileGemCache
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        file_cache = FileGemCache(cache_dir=tmpdir, filename="test_cache.json")
        test_gem = GemMetadata(
            gem_id="gem_file_001",
            gem_name="文件缓存测试",
            gem_type="促销类",
        )
        await file_cache.set("gem_file_001", test_gem)
        file_restored = await file_cache.get("gem_file_001")
        assert file_restored is not None
        assert file_restored.gem_name == "文件缓存测试"
        print("[20] FileGemCache 读写: 通过")

    print("\n" + "=" * 60)
    print("所有测试通过!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(_test())
