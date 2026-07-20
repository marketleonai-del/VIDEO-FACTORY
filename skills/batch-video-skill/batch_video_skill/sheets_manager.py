"""
sheets_manager.py — Google Sheets 管理器

封装三张核心表的完整 CRUD 操作：
- gem-metadata: Gem 模板配置表（25列）
- task-queue: 任务调度表（29列）
- batch-summary: 批次汇总表（18列）

提供批量操作优化、数据序列化/反序列化、状态转换校验和
令牌桶限流 + 退避重试。

用法:
    >>> from workspace_client import WorkspaceClient
    >>> client = await WorkspaceClient.from_service_account("/path/to/sa.json")
    >>> sheets = client.sheets
    >>> gems = await sheets.get_gem_metadata("SPREADSHEET_ID")
    >>> tasks = await sheets.get_pending_tasks("SPREADSHEET_ID")
    >>> await sheets.update_task_status("SPREADSHEET_ID", row_idx, "processing")

Python 3.11+ | Pydantic v2 | asyncio
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Callable, List, Optional, Tuple, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

# ═══════════════════════════════════════════════════════════
# 从 workspace_models 导入共享组件（打破循环导入）
# ═══════════════════════════════════════════════════════════

from workspace_models import (
    BaseManager,
    BulkOperationError,
    BulkSheetsResult,
    ClientConfig,
    GwsExitCodeError,
    SheetsApiError,
    SheetsReadResult,
    SheetsWriteResult,
    ValidationError,
    GemType,
    Gender,
    TaskPriority,
    TaskStatus,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 1. 表头常量与列映射
# ═══════════════════════════════════════════════════════════


# ── gem-metadata 表头 ─────────────────────────────────────

GEM_METADATA_HEADERS: list[str] = [
    "gem_id",               # A
    "gem_name",             # B
    "gem_type",             # C
    "gem_internal_name",    # D
    "is_active",            # E
    "auto_discovered",      # F
    "product_categories",   # G
    "price_ranges",         # H
    "product_features",     # I
    "target_audiences",     # J
    "age_ranges",           # K
    "gender",               # L
    "marketing_scenes",     # M
    "usage_scenes",         # N
    "platforms",            # O
    "video_styles",         # P
    "tones",                # Q
    "avg_generation_time_sec",  # R
    "usage_count",              # S
    "success_rate",             # T
    "avg_match_score",          # U
    "best_for_examples",    # V
    "notes",                # W
    "created_at",           # X
    "updated_at",           # Y
]

GEM_METADATA_COL_INDEX: dict[str, int] = {
    h: i for i, h in enumerate(GEM_METADATA_HEADERS)
}

GEM_METADATA_COL_MAP: dict[str, int] = {
    h: i + 1 for i, h in enumerate(GEM_METADATA_HEADERS)
}

GEM_METADATA_COL_LETTER: dict[str, str] = {
    "gem_id": "A",
    "gem_name": "B",
    "gem_type": "C",
    "gem_internal_name": "D",
    "is_active": "E",
    "auto_discovered": "F",
    "product_categories": "G",
    "price_ranges": "H",
    "product_features": "I",
    "target_audiences": "J",
    "age_ranges": "K",
    "gender": "L",
    "marketing_scenes": "M",
    "usage_scenes": "N",
    "platforms": "O",
    "video_styles": "P",
    "tones": "Q",
    "avg_generation_time_sec": "R",
    "usage_count": "S",
    "success_rate": "T",
    "avg_match_score": "U",
    "best_for_examples": "V",
    "notes": "W",
    "created_at": "X",
    "updated_at": "Y",
}

GEM_METADATA_RANGE = "gem-metadata!A1:Y1000"
GEM_METADATA_APPEND_RANGE = "gem-metadata!A1"


# ── task-queue 表头 ───────────────────────────────────────

TASK_QUEUE_HEADERS: list[str] = [
    "task_id",              # A
    "sequence_no",          # B
    "batch_id",             # C
    "image_path",           # D
    "user_description",     # E
    "product_category",     # F
    "selected_gem_id",      # G
    "selected_gem_name",    # H
    "match_score",          # I
    "generated_prompt",     # J
    "video_model",          # K
    "video_resolution",     # L
    "video_duration",       # M
    "status",               # N
    "retry_count",          # O
    "priority",             # P
    "task_api_id",          # Q
    "video_url",            # R
    "video_local_path",     # S
    "drive_file_id",        # T
    "drive_file_url",       # U
    "cost_usd",             # V
    "generation_time_sec",  # W
    "total_time_sec",       # X
    "error_code",           # Y
    "error_message",        # Z
    "created_at",           # AA
    "started_at",           # AB
    "completed_at",         # AC
]

TASK_QUEUE_COL_INDEX: dict[str, int] = {
    h: i for i, h in enumerate(TASK_QUEUE_HEADERS)
}

TASK_QUEUE_COL_MAP: dict[str, int] = {
    h: i + 1 for i, h in enumerate(TASK_QUEUE_HEADERS)
}

TASK_QUEUE_COL_LETTER: dict[str, str] = {
    "task_id": "A",
    "sequence_no": "B",
    "batch_id": "C",
    "image_path": "D",
    "user_description": "E",
    "product_category": "F",
    "selected_gem_id": "G",
    "selected_gem_name": "H",
    "match_score": "I",
    "generated_prompt": "J",
    "video_model": "K",
    "video_resolution": "L",
    "video_duration": "M",
    "status": "N",
    "retry_count": "O",
    "priority": "P",
    "task_api_id": "Q",
    "video_url": "R",
    "video_local_path": "S",
    "drive_file_id": "T",
    "drive_file_url": "U",
    "cost_usd": "V",
    "generation_time_sec": "W",
    "total_time_sec": "X",
    "error_code": "Y",
    "error_message": "Z",
    "created_at": "AA",
    "started_at": "AB",
    "completed_at": "AC",
}

TASK_QUEUE_RANGE = "task-queue!A1:AC10000"
TASK_QUEUE_APPEND_RANGE = "task-queue!A1"


# ── batch-summary 表头 ────────────────────────────────────

BATCH_SUMMARY_HEADERS: list[str] = [
    "batch_id",             # A
    "batch_name",           # B
    "total_tasks",          # C
    "succeeded_count",      # D
    "failed_count",         # E
    "pending_count",        # F
    "cancelled_count",      # G
    "total_cost_usd",       # H
    "avg_cost_usd",         # I
    "total_time_sec",       # J
    "avg_generation_time_sec",  # K
    "success_rate",         # L
    "gem_usage_json",       # M
    "model_usage_json",     # N
    "error_breakdown_json", # O
    "product_categories",   # P
    "drive_folder_id",      # Q
    "drive_folder_url",     # R
    "notification_sent",    # S
    "created_at",           # T
    "started_at",           # U
    "completed_at",         # V
    "notes",                # W
]

BATCH_SUMMARY_COL_INDEX: dict[str, int] = {
    h: i for i, h in enumerate(BATCH_SUMMARY_HEADERS)
}

BATCH_SUMMARY_COL_MAP: dict[str, int] = {
    h: i + 1 for i, h in enumerate(BATCH_SUMMARY_HEADERS)
}

BATCH_SUMMARY_COL_LETTER: dict[str, str] = {
    "batch_id": "A",
    "batch_name": "B",
    "total_tasks": "C",
    "succeeded_count": "D",
    "failed_count": "E",
    "pending_count": "F",
    "cancelled_count": "G",
    "total_cost_usd": "H",
    "avg_cost_usd": "I",
    "total_time_sec": "J",
    "avg_generation_time_sec": "K",
    "success_rate": "L",
    "gem_usage_json": "M",
    "model_usage_json": "N",
    "error_breakdown_json": "O",
    "product_categories": "P",
    "drive_folder_id": "Q",
    "drive_folder_url": "R",
    "notification_sent": "S",
    "created_at": "T",
    "started_at": "U",
    "completed_at": "V",
    "notes": "W",
}

BATCH_SUMMARY_RANGE = "batch-summary!A1:W5000"
BATCH_SUMMARY_APPEND_RANGE = "batch-summary!A1"


# ═══════════════════════════════════════════════════════════
# 2. 状态转换规则
# ═══════════════════════════════════════════════════════════

VALID_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "pending":     ["processing", "cancelled"],
    "processing":  ["succeeded", "failed", "expired"],
    "failed":      ["pending"],
    "succeeded":   [],
    "cancelled":   [],
    "expired":     ["pending"],
}


# ═══════════════════════════════════════════════════════════
# 3. Pydantic 数据模型
# ═══════════════════════════════════════════════════════════


class GemMetadata(BaseModel):
    """Gem 模板元数据模型

    对应 Sheets 的 gem-metadata 表，共 25 个有效字段（Y列）。
    所有 STRING[] 字段在 Sheets 中以逗号分隔存储。
    """
    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    # ── 基础信息 (A-E) ─────────────────────────────────────
    gem_id: str = Field(..., pattern=r"^gem_\d{3}$", description="唯一标识")
    gem_name: str = Field(..., min_length=1, max_length=50, description="显示名称")
    gem_type: GemType = Field(..., description="模板类型")
    gem_internal_name: str = Field(default="", max_length=100, description="Gemini 内部名称")
    is_active: bool = Field(default=True, description="是否启用")

    # ── 发现方式 (F) ───────────────────────────────────────
    auto_discovered: bool = Field(default=False, description="是否自动发现")

    # ── 适配维度 (G-Q) ─────────────────────────────────────
    product_categories: list[str] = Field(default_factory=list, description="适配产品品类")
    price_ranges: list[str] = Field(default_factory=list, description="适配价格带")
    product_features: list[str] = Field(default_factory=list, description="适配产品特征")
    target_audiences: list[str] = Field(default_factory=list, description="适配人群")
    age_ranges: list[str] = Field(default_factory=list, description="适配年龄段")
    gender: Gender = Field(default=Gender.UNISEX, description="性别倾向")
    marketing_scenes: list[str] = Field(default_factory=list, description="适配营销场景")
    usage_scenes: list[str] = Field(default_factory=list, description="适配使用场景")
    platforms: list[str] = Field(default_factory=list, description="适配平台")
    video_styles: list[str] = Field(default_factory=list, description="擅长视频风格")
    tones: list[str] = Field(default_factory=list, description="擅长画面调性")

    # ── 统计指标 (R-U) ─────────────────────────────────────
    avg_generation_time_sec: float = Field(default=30.0, ge=0, description="平均生成耗时(秒)")
    usage_count: int = Field(default=0, ge=0, description="累计使用次数")
    success_rate: float = Field(default=1.0, ge=0, le=1, description="历史成功率")
    avg_match_score: float = Field(default=0.0, ge=0, le=1, description="平均匹配度分数")

    # ── 示例与备注 (V-W) ───────────────────────────────────
    best_for_examples: list[str] = Field(default_factory=list, description="最佳应用示例")
    notes: str = Field(default="", max_length=500, description="备注说明")

    # ── 时间戳 (X-Y) ───────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")
    updated_at: Optional[datetime] = Field(default=None, description="更新时间")

    # ── 内部字段 ────────────────────────────────────────────
    _source_row_index: Optional[int] = None

    # ── 校验器 ─────────────────────────────────────────────

    @field_validator("gem_id")
    @classmethod
    def validate_gem_id_format(cls, v: str) -> str:
        if not re.match(r"^gem_\d{3}$", v):
            raise ValueError(f"gem_id 格式错误，期望 gem_NNN，got: {v}")
        return v

    @field_validator(
        "product_categories", "price_ranges", "product_features",
        "target_audiences", "age_ranges", "marketing_scenes",
        "usage_scenes", "platforms", "video_styles", "tones", "best_for_examples",
        mode="before",
    )
    @classmethod
    def parse_string_list(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            if not v:
                return []
            return [item.strip() for item in v.split(",") if item.strip()]
        if isinstance(v, list):
            return [str(item).strip() for item in v if item]
        if v is None:
            return []
        raise ValueError(f"无法解析为字符串列表: {v}")

    @field_validator("is_active", "auto_discovered", mode="before")
    @classmethod
    def parse_boolean(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes", "y")
        if isinstance(v, (int, float)):
            return bool(v)
        return bool(v)

    @field_validator("updated_at", mode="before")
    @classmethod
    def parse_optional_datetime(cls, v: Any) -> Any:
        """将空字符串解析为 None，其他值保持原样由 Pydantic 处理"""
        if v is None or v == "":
            return None
        return v

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, v: Any) -> Any:
        """将空字符串解析为当前时间"""
        if v is None or v == "":
            return datetime.utcnow()
        return v

    @field_validator("gender", mode="before")
    @classmethod
    def parse_gender(cls, v: Any) -> Any:
        """将空字符串解析为默认性别"""
        if v is None or v == "":
            return Gender.UNISEX
        return v

    @field_validator(
        "avg_generation_time_sec", "usage_count", "success_rate", "avg_match_score",
        mode="before",
    )
    @classmethod
    def parse_numeric_field(cls, v: Any, info) -> Any:
        """将空字符串解析为 0（Pydantic 会自动匹配字段类型）"""
        if v is None or v == "":
            return 0
        return v

    # ── 序列化器 ───────────────────────────────────────────

    @field_serializer("gem_type")
    def serialize_gem_type(self, gem_type: GemType) -> str:
        return gem_type.value

    @field_serializer("gender")
    def serialize_gender(self, gender: Gender) -> str:
        return gender.value

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, dt: Optional[datetime]) -> str:
        if dt is None:
            return ""
        return dt.isoformat()

    @field_serializer(
        "product_categories", "price_ranges", "product_features",
        "target_audiences", "age_ranges", "marketing_scenes",
        "usage_scenes", "platforms", "video_styles", "tones", "best_for_examples",
    )
    def serialize_string_list(self, items: list[str]) -> str:
        return ",".join(items)

    # ── 便捷属性 ───────────────────────────────────────────

    @property
    def source_row_index(self) -> Optional[int]:
        return self._source_row_index

    @source_row_index.setter
    def source_row_index(self, value: int) -> None:
        self._source_row_index = value

    @property
    def is_matchable(self) -> bool:
        return self.is_active

    @property
    def capabilities_summary(self) -> str:
        cats = ", ".join(self.product_categories[:3]) if self.product_categories else "通用"
        return f"[{self.gem_id}] {self.gem_name} ({self.gem_type.value}) | 品类: {cats} | 成功率: {self.success_rate:.0%}"

    # ── Sheets 转换 ────────────────────────────────────────

    def to_sheets_row(self) -> list[str]:
        """转换为 Sheets 行数据（按 GEM_METADATA_HEADERS 顺序）"""
        data = self.model_dump(by_alias=False)
        return [str(data.get(h, "")) for h in GEM_METADATA_HEADERS]

    @classmethod
    def from_sheets_row(cls, row: list[str], row_index: Optional[int] = None) -> "GemMetadata":
        padded_row = row + [""] * (len(GEM_METADATA_HEADERS) - len(row))
        data = dict(zip(GEM_METADATA_HEADERS, padded_row))
        instance = cls.model_validate(data)
        if row_index is not None:
            instance.source_row_index = row_index
        return instance


class TaskRecord(BaseModel):
    """任务记录模型

    对应 Sheets 的 task-queue 表，共 29 个有效字段（AC列）。
    是任务在整个生命周期中的完整数据表示。
    """
    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    # ── 基础信息 (A-D) ─────────────────────────────────────
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="任务唯一标识")
    sequence_no: int = Field(..., ge=1, description="序号")
    batch_id: str = Field(..., max_length=50, description="批次 ID")
    image_path: str = Field(..., max_length=500, description="产品图片路径")

    # ── 输入描述 (E) ───────────────────────────────────────
    user_description: str = Field(default="", max_length=2000, description="用户额外描述")

    # ── 智能路由结果 (F-I) ─────────────────────────────────
    product_category: str = Field(default="", description="识别出的产品品类")
    selected_gem_id: str = Field(default="", pattern=r"^(gem_\d{3})?$", description="选中的 Gem ID")
    selected_gem_name: str = Field(default="", max_length=50, description="选中的 Gem 名称")
    match_score: float = Field(default=0.0, ge=0, le=1, description="匹配度分数")

    # ── 生成配置 (J-M) ─────────────────────────────────────
    generated_prompt: str = Field(default="", max_length=4000, description="生成的视频提示词")
    video_model: str = Field(default="veo_2", description="使用的视频模型")
    video_resolution: str = Field(default="720p", description="视频分辨率")
    video_duration: int = Field(default=5, ge=4, le=15, description="视频时长(秒)")

    # ── 状态管理 (N-P) ─────────────────────────────────────
    status: str = Field(default="pending", description="任务状态")
    retry_count: int = Field(default=0, ge=0, le=5, description="重试次数")
    priority: str = Field(default="normal", description="优先级")

    # ── 外部 API 追踪 (Q) ──────────────────────────────────
    task_api_id: str = Field(default="", max_length=100, description="API 返回的任务 ID")

    # ── 输出结果 (R-S) ─────────────────────────────────────
    video_url: str = Field(default="", max_length=500, description="视频下载 URL")
    video_local_path: str = Field(default="", max_length=500, description="本地视频路径")

    # ── Drive 关联 (T-U) ───────────────────────────────────
    drive_file_id: str = Field(default="", max_length=100, description="Drive 文件 ID")
    drive_file_url: str = Field(default="", max_length=500, description="Drive 文件链接")

    # ── 成本与耗时 (V-X) ───────────────────────────────────
    cost_usd: float = Field(default=0.0, ge=0, description="花费(美元)")
    generation_time_sec: float = Field(default=0.0, ge=0, description="生成耗时(秒)")
    total_time_sec: float = Field(default=0.0, ge=0, description="总耗时(秒)")

    # ── 错误信息 (Y-Z) ─────────────────────────────────────
    error_code: str = Field(default="", max_length=50, description="错误代码")
    error_message: str = Field(default="", max_length=1000, description="错误描述")

    # ── 时间戳 (AA-AC) ─────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")
    started_at: Optional[datetime] = Field(default=None, description="开始处理时间")
    completed_at: Optional[datetime] = Field(default=None, description="完成时间")

    # ── 内部字段 ────────────────────────────────────────────
    _source_row_index: Optional[int] = None

    # ── 校验器 ─────────────────────────────────────────────

    @field_validator("selected_gem_id")
    @classmethod
    def validate_gem_id_optional(cls, v: str) -> str:
        if not v:
            return ""
        if not re.match(r"^gem_\d{3}$", v):
            raise ValueError(f"gem_id 格式错误，期望 gem_NNN 或空，got: {v}")
        return v

    @field_validator("status", mode="before")
    @classmethod
    def parse_status(cls, v: Any) -> str:
        if isinstance(v, str):
            v_lower = v.lower().strip()
            valid = ["pending", "processing", "succeeded", "failed", "cancelled", "expired"]
            if v_lower not in valid:
                raise ValueError(f"无效的状态值: {v}，有效值: {valid}")
            return v_lower
        raise ValueError(f"无效的状态类型: {type(v)}")

    @field_validator("priority", mode="before")
    @classmethod
    def parse_priority(cls, v: Any) -> str:
        if isinstance(v, str):
            v_lower = v.lower().strip()
            valid = ["low", "normal", "high", "urgent"]
            if v_lower not in valid:
                raise ValueError(f"无效的优先级值: {v}，有效值: {valid}")
            return v_lower
        raise ValueError(f"无效的优先级类型: {type(v)}")

    @field_validator("video_model", mode="before")
    @classmethod
    def parse_video_model(cls, v: Any) -> str:
        if isinstance(v, str):
            v_lower = v.lower().strip()
            valid = ["veo_2", "runway", "kling", "pika"]
            if v_lower not in valid:
                raise ValueError(f"无效的视频模型: {v}，有效值: {valid}")
            return v_lower
        raise ValueError(f"无效的视频模型类型: {type(v)}")

    @field_validator("video_resolution", mode="before")
    @classmethod
    def parse_video_resolution(cls, v: Any) -> str:
        if isinstance(v, str):
            v_lower = v.lower().strip()
            if not v_lower:
                return "720p"  # 默认值
            valid = ["360p", "480p", "720p", "1080p", "1440p", "4k"]
            if v_lower not in valid:
                raise ValueError(f"无效的分辨率: {v}，有效值: {valid}")
            return v_lower
        raise ValueError(f"无效的分辨率类型: {type(v)}")

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def parse_optional_datetime(cls, v: Any) -> Any:
        """将空字符串解析为 None"""
        if v is None or v == "":
            return None
        return v

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, v: Any) -> Any:
        """将空字符串解析为当前时间"""
        if v is None or v == "":
            return datetime.utcnow()
        return v

    @field_validator(
        "match_score", "cost_usd", "generation_time_sec", "total_time_sec",
        mode="before",
    )
    @classmethod
    def parse_numeric_field(cls, v: Any) -> Any:
        """将空字符串解析为 0"""
        if v is None or v == "":
            return 0
        return v

    # ── 序列化器 ───────────────────────────────────────────

    @field_serializer("created_at", "started_at", "completed_at")
    def serialize_datetime(self, dt: Optional[datetime]) -> str:
        return dt.isoformat() if dt else ""

    # ── 状态转换方法 ───────────────────────────────────────

    def transition_status(self, new_status: str) -> None:
        """转换任务状态（自动更新相关时间戳和重试计数）"""
        new_status = new_status.lower().strip()
        allowed = VALID_STATUS_TRANSITIONS.get(self.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"非法状态转换: {self.status} -> {new_status}, "
                f"允许的转换: {allowed}"
            )

        old_status = self.status
        self.status = new_status

        now = datetime.utcnow()
        if new_status == "processing":
            self.started_at = now
        elif new_status in ("succeeded", "failed", "cancelled"):
            self.completed_at = now
            if old_status == "processing" and self.started_at:
                self.generation_time_sec = (now - self.started_at).total_seconds()
        elif new_status == "pending" and old_status == "failed":
            self.retry_count += 1

    def mark_succeeded(
        self,
        video_url: str = "",
        video_local_path: str = "",
        drive_file_id: str = "",
        drive_file_url: str = "",
        cost_usd: float = 0.0,
    ) -> None:
        """标记任务为成功状态"""
        self.video_url = video_url
        self.video_local_path = video_local_path
        self.drive_file_id = drive_file_id
        self.drive_file_url = drive_file_url
        self.cost_usd = cost_usd
        self.transition_status("succeeded")

    def mark_failed(self, error_code: str, error_message: str) -> None:
        """标记任务为失败状态"""
        self.error_code = error_code
        self.error_message = error_message
        self.transition_status("failed")

    # ── 便捷属性 ───────────────────────────────────────────

    @property
    def source_row_index(self) -> Optional[int]:
        return self._source_row_index

    @source_row_index.setter
    def source_row_index(self, value: int) -> None:
        self._source_row_index = value

    @property
    def is_terminal(self) -> bool:
        return self.status in ("succeeded", "cancelled")

    @property
    def is_retryable(self) -> bool:
        return self.status == "failed" and self.retry_count < 5

    @property
    def duration_display(self) -> str:
        sec = self.generation_time_sec
        if sec < 60:
            return f"{sec:.1f}秒"
        return f"{sec / 60:.1f}分钟"

    # ── Sheets 转换 ────────────────────────────────────────

    def to_sheets_row(self) -> list[str]:
        data = self.model_dump(by_alias=False)
        return [str(data.get(h, "")) for h in TASK_QUEUE_HEADERS]

    @classmethod
    def from_sheets_row(cls, row: list[str], row_index: Optional[int] = None) -> "TaskRecord":
        padded_row = row + [""] * (len(TASK_QUEUE_HEADERS) - len(row))
        data = dict(zip(TASK_QUEUE_HEADERS, padded_row))
        instance = cls.model_validate(data)
        if row_index is not None:
            instance.source_row_index = row_index
        return instance


class BatchSummary(BaseModel):
    """批次汇总记录模型

    对应 Sheets 的 batch-summary 表，共 18 个有效字段（W列）。
    JSON 字段在 Sheets 中以 JSON 字符串形式存储。
    """
    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    # ── 批次标识 (A-B) ─────────────────────────────────────
    batch_id: str = Field(..., max_length=50, description="批次唯一标识")
    batch_name: str = Field(default="", max_length=100, description="批次显示名称")

    # ── 统计计数 (C-G) ─────────────────────────────────────
    total_tasks: int = Field(default=0, ge=0, description="总任务数")
    succeeded_count: int = Field(default=0, ge=0, description="成功数")
    failed_count: int = Field(default=0, ge=0, description="失败数")
    pending_count: int = Field(default=0, ge=0, description="待处理数")
    cancelled_count: int = Field(default=0, ge=0, description="取消数")

    # ── 成本指标 (H-I) ─────────────────────────────────────
    total_cost_usd: float = Field(default=0.0, ge=0, description="总花费(美元)")
    avg_cost_usd: float = Field(default=0.0, ge=0, description="平均花费(美元)")

    # ── 耗时指标 (J-K) ─────────────────────────────────────
    total_time_sec: float = Field(default=0.0, ge=0, description="总耗时(秒)")
    avg_generation_time_sec: float = Field(default=0.0, ge=0, description="平均生成耗时(秒)")

    # ── 成功率 (L) ─────────────────────────────────────────
    success_rate: float = Field(default=0.0, ge=0, le=1, description="成功率")

    # ── JSON 统计 (M-O) ────────────────────────────────────
    gem_usage_json: dict[str, int] = Field(default_factory=dict, description="各 Gem 使用次数")
    model_usage_json: dict[str, int] = Field(default_factory=dict, description="各模型使用次数")
    error_breakdown_json: dict[str, int] = Field(default_factory=dict, description="错误类型分布")

    # ── 关联信息 (P-R) ─────────────────────────────────────
    product_categories: list[str] = Field(default_factory=list, description="涉及的产品品类")
    drive_folder_id: str = Field(default="", max_length=100, description="Drive 文件夹 ID")
    drive_folder_url: str = Field(default="", max_length=500, description="Drive 文件夹链接")

    # ── 通知状态 (S) ───────────────────────────────────────
    notification_sent: bool = Field(default=False, description="通知是否已发送")

    # ── 时间戳 (T-V) ───────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow, description="批次创建时间")
    started_at: Optional[datetime] = Field(default=None, description="开始处理时间")
    completed_at: Optional[datetime] = Field(default=None, description="完成时间")

    # ── 备注 (W) ───────────────────────────────────────────
    notes: str = Field(default="", max_length=1000, description="备注")

    # ── 内部字段 ────────────────────────────────────────────
    _source_row_index: Optional[int] = None

    # ── 校验器 ─────────────────────────────────────────────

    @field_validator("gem_usage_json", "model_usage_json", "error_breakdown_json", mode="before")
    @classmethod
    def parse_json_field(cls, v: Any) -> dict[str, int]:
        if isinstance(v, dict):
            return {str(k): int(val) for k, val in v.items()}
        if isinstance(v, str):
            if not v or v == "{}":
                return {}
            try:
                parsed = json.loads(v)
                return {str(k): int(val) for k, val in parsed.items()}
            except (json.JSONDecodeError, ValueError):
                return {}
        if v is None:
            return {}
        raise ValueError(f"无法解析为 JSON 字典: {v}")

    @field_validator("product_categories", mode="before")
    @classmethod
    def parse_categories(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [str(i).strip() for i in v if i]
        if isinstance(v, str):
            if not v:
                return []
            return [item.strip() for item in v.split(",") if item.strip()]
        return []

    @field_validator("notification_sent", mode="before")
    @classmethod
    def parse_boolean(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes")
        return bool(v)

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def parse_optional_datetime(cls, v: Any) -> Any:
        """将空字符串解析为 None"""
        if v is None or v == "":
            return None
        return v

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, v: Any) -> Any:
        """将空字符串解析为当前时间"""
        if v is None or v == "":
            return datetime.utcnow()
        return v

    @field_validator(
        "total_tasks", "succeeded_count", "failed_count", "pending_count", "cancelled_count",
        "total_cost_usd", "avg_cost_usd", "total_time_sec", "avg_generation_time_sec",
        "success_rate",
        mode="before",
    )
    @classmethod
    def parse_numeric_field(cls, v: Any) -> Any:
        """将空字符串解析为 0"""
        if v is None or v == "":
            return 0
        return v

    # ── 序列化器 ───────────────────────────────────────────

    @field_serializer("gem_usage_json", "model_usage_json", "error_breakdown_json")
    def serialize_json_field(self, data: dict[str, int]) -> str:
        return json.dumps(data, ensure_ascii=False)

    @field_serializer("product_categories")
    def serialize_categories(self, items: list[str]) -> str:
        return ",".join(items)

    @field_serializer("created_at", "started_at", "completed_at")
    def serialize_datetime(self, dt: Optional[datetime]) -> str:
        return dt.isoformat() if dt else ""

    # ── 便捷属性 ───────────────────────────────────────────

    @property
    def source_row_index(self) -> Optional[int]:
        return self._source_row_index

    @source_row_index.setter
    def source_row_index(self, value: int) -> None:
        self._source_row_index = value

    @property
    def is_completed(self) -> bool:
        return self.completed_at is not None

    @property
    def completion_percentage(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return (self.succeeded_count + self.failed_count + self.cancelled_count) / self.total_tasks

    @property
    def summary_text(self) -> str:
        return (
            f"批次 {self.batch_id}: "
            f"共{self.total_tasks}个任务, "
            f"成功{self.succeeded_count}, "
            f"失败{self.failed_count}, "
            f"成功率{self.success_rate:.1%}, "
            f"总花费${self.total_cost_usd:.2f}"
        )

    def recalculate_stats(self) -> None:
        if self.total_tasks > 0:
            self.success_rate = self.succeeded_count / self.total_tasks
            self.avg_cost_usd = self.total_cost_usd / self.total_tasks

    # ── Sheets 转换 ────────────────────────────────────────

    def to_sheets_row(self) -> list[str]:
        data = self.model_dump(by_alias=False)
        return [str(data.get(h, "")) for h in BATCH_SUMMARY_HEADERS]

    @classmethod
    def from_sheets_row(cls, row: list[str], row_index: Optional[int] = None) -> "BatchSummary":
        padded_row = row + [""] * (len(BATCH_SUMMARY_HEADERS) - len(row))
        data = dict(zip(BATCH_SUMMARY_HEADERS, padded_row))
        instance = cls.model_validate(data)
        if row_index is not None:
            instance.source_row_index = row_index
        return instance

    @classmethod
    def from_task_records(cls, batch_id: str, tasks: list[TaskRecord]) -> "BatchSummary":
        """从任务列表计算批次汇总"""
        total = len(tasks)
        if total == 0:
            return cls(batch_id=batch_id)

        succeeded = sum(1 for t in tasks if t.status == "succeeded")
        failed = sum(1 for t in tasks if t.status == "failed")
        pending = sum(1 for t in tasks if t.status == "pending")
        cancelled = sum(1 for t in tasks if t.status == "cancelled")

        total_cost = sum(t.cost_usd for t in tasks)
        total_gen_time = sum(t.generation_time_sec for t in tasks if t.generation_time_sec > 0)
        gen_count = sum(1 for t in tasks if t.generation_time_sec > 0)

        gem_usage: dict[str, int] = {}
        model_usage: dict[str, int] = {}
        error_breakdown: dict[str, int] = {}
        categories: set[str] = set()

        for t in tasks:
            if t.selected_gem_id:
                gem_usage[t.selected_gem_id] = gem_usage.get(t.selected_gem_id, 0) + 1
            if t.video_model:
                model_usage[t.video_model] = model_usage.get(t.video_model, 0) + 1
            if t.status == "failed" and t.error_code:
                error_breakdown[t.error_code] = error_breakdown.get(t.error_code, 0) + 1
            if t.product_category:
                categories.add(t.product_category)

        return cls(
            batch_id=batch_id,
            total_tasks=total,
            succeeded_count=succeeded,
            failed_count=failed,
            pending_count=pending,
            cancelled_count=cancelled,
            total_cost_usd=total_cost,
            avg_cost_usd=total_cost / total if total > 0 else 0,
            total_time_sec=total_gen_time,
            avg_generation_time_sec=total_gen_time / gen_count if gen_count > 0 else 0,
            success_rate=succeeded / total if total > 0 else 0,
            gem_usage_json=gem_usage,
            model_usage_json=model_usage,
            error_breakdown_json=error_breakdown,
            product_categories=sorted(list(categories)),
        )


# ═══════════════════════════════════════════════════════════
# 4. SheetsSerializer — 数据序列化器
# ═══════════════════════════════════════════════════════════

T = TypeVar("T", bound=BaseModel)


class SheetsSerializer:
    """Sheets 数据序列化器

    提供 Pydantic 模型和 Sheets 行数据之间的双向转换。
    所有转换方法都是纯函数，无状态无副作用。
    """

    _MODEL_HEADER_MAP: dict[type[BaseModel], list[str]] = {
        GemMetadata: GEM_METADATA_HEADERS,
        TaskRecord: TASK_QUEUE_HEADERS,
        BatchSummary: BATCH_SUMMARY_HEADERS,
    }

    @classmethod
    def model_to_row(cls, model: BaseModel) -> list[str]:
        """将 Pydantic 模型转换为 Sheets 行数据"""
        headers = cls._get_headers_for_model(type(model))
        data = model.model_dump(by_alias=False)
        return [cls._serialize_value(data.get(h, "")) for h in headers]

    @classmethod
    def row_to_model(
        cls,
        row: list[str],
        model_class: type[T],
        row_index: Optional[int] = None,
    ) -> T:
        """将 Sheets 行数据转换为 Pydantic 模型"""
        headers = cls._get_headers_for_model(model_class)
        padded_row = row + [""] * (len(headers) - len(row))
        data = dict(zip(headers, padded_row))
        instance = model_class.model_validate(data)
        if hasattr(instance, "source_row_index") and row_index is not None:
            instance.source_row_index = row_index  # type: ignore[attr-defined]
        return instance

    @classmethod
    def bulk_models_to_rows(cls, models: list[BaseModel]) -> list[list[str]]:
        """批量将模型列表转换为行数据列表"""
        return [cls.model_to_row(m) for m in models]

    @classmethod
    def bulk_rows_to_models(
        cls,
        rows: list[list[str]],
        model_class: type[T],
        skip_header: bool = True,
    ) -> list[T]:
        """批量将行数据列表转换为模型列表"""
        start_idx = 1 if skip_header else 0
        results: list[T] = []
        for i, row in enumerate(rows[start_idx:], start=start_idx):
            if any(cell.strip() for cell in row):
                results.append(cls.row_to_model(row, model_class, row_index=i))
        return results

    # ── 内部辅助 ────────────────────────────────────────────

    @classmethod
    def _get_headers_for_model(cls, model_class: type[BaseModel]) -> list[str]:
        headers = cls._MODEL_HEADER_MAP.get(model_class)
        if headers is None:
            raise ValueError(f"不支持的模型类型: {model_class.__name__}")
        return headers

    @staticmethod
    def _serialize_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)


# ═══════════════════════════════════════════════════════════
# 5. SheetsManager — Sheets 操作管理器
# ═══════════════════════════════════════════════════════════


class SheetsManager(BaseManager):
    """Google Sheets 操作管理器

    封装三张核心表的完整 CRUD 操作：
    - gem-metadata: Gem 模板配置表
    - task-queue: 任务调度表
    - batch-summary: 批次汇总表

    Args:
        config: 客户端配置
        runner: gws 命令执行器

    Example:
        >>> sheets = client.sheets
        >>> gems = await sheets.get_gem_metadata("SPREADSHEET_ID")
        >>> tasks = await sheets.get_pending_tasks("SPREADSHEET_ID")
    """

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(self, config: ClientConfig, runner: Any) -> None:
        super().__init__(config, runner)
        self._batch_size = config.sheets_batch_size

    # ── 核心读取方法 ──────────────────────────────────────────

    async def read(
        self,
        spreadsheet_id: str,
        range_name: str,
    ) -> SheetsReadResult:
        """读取 Sheets 数据范围

        Args:
            spreadsheet_id: 电子表格 ID
            range_name: 数据范围，如 "Sheet1!A1:D10"

        Returns:
            SheetsReadResult 包含行数据、表头和元信息
        """
        start_time = asyncio.get_event_loop().time()

        try:
            result = await self._run_with_retry(
                "sheets", "+read",
                "--spreadsheet", spreadsheet_id,
                "--range", range_name,
            )
        except GwsExitCodeError as e:
            raise SheetsApiError(
                message=f"读取 Sheets 失败: {e.message}",
                status_code=400,
                operation="sheets.read",
                spreadsheet_id=spreadsheet_id,
                range_name=range_name,
            ) from e

        # 解析数据
        values = result.get("values", [])
        headers = values[0] if values else []
        data = values[1:] if len(values) > 1 else []

        duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)

        return SheetsReadResult(
            success=True,
            operation="sheets.read",
            row_count=len(data),
            column_count=len(headers),
            data=data,
            headers=headers,
            duration_ms=duration_ms,
            raw_response=result,
        )

    # ── 核心写入方法 ──────────────────────────────────────────

    async def write(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> SheetsWriteResult:
        """写入 Sheets 数据（覆盖）

        Args:
            spreadsheet_id: 电子表格 ID
            range_name: 目标范围
            values: 二维字符串列表

        Returns:
            SheetsWriteResult
        """
        start_time = asyncio.get_event_loop().time()

        # 序列化值
        json_values = json.dumps(values)

        try:
            result = await self._run_with_retry(
                "sheets", "+write",
                "--spreadsheet", spreadsheet_id,
                "--range", range_name,
                "--values", json_values,
            )
        except GwsExitCodeError as e:
            raise SheetsApiError(
                message=f"写入 Sheets 失败: {e.message}",
                status_code=400,
                operation="sheets.write",
                spreadsheet_id=spreadsheet_id,
                range_name=range_name,
            ) from e

        duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)

        return SheetsWriteResult(
            success=True,
            operation="sheets.write",
            rows_written=len(values),
            range_written=range_name,
            duration_ms=duration_ms,
            raw_response=result,
        )

    async def append(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
    ) -> SheetsWriteResult:
        """追加 Sheets 数据

        Args:
            spreadsheet_id: 电子表格 ID
            range_name: 目标范围
            values: 二维字符串列表

        Returns:
            SheetsWriteResult
        """
        start_time = asyncio.get_event_loop().time()

        json_values = json.dumps(values)

        try:
            result = await self._run_with_retry(
                "sheets", "+append",
                "--spreadsheet", spreadsheet_id,
                "--range", range_name,
                "--values", json_values,
            )
        except GwsExitCodeError as e:
            raise SheetsApiError(
                message=f"追加 Sheets 失败: {e.message}",
                status_code=400,
                operation="sheets.append",
                spreadsheet_id=spreadsheet_id,
                range_name=range_name,
            ) from e

        duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)

        return SheetsWriteResult(
            success=True,
            operation="sheets.append",
            rows_written=len(values),
            duration_ms=duration_ms,
            raw_response=result,
        )

    async def bulk_append(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: list[list[str]],
        batch_size: Optional[int] = None,
    ) -> BulkSheetsResult:
        """批量追加 Sheets 数据（分批次）

        Args:
            spreadsheet_id: 电子表格 ID
            range_name: 目标范围
            values: 二维字符串列表
            batch_size: 每批行数，默认使用配置值

        Returns:
            BulkSheetsResult 汇总结果
        """
        batch_size = batch_size or self._batch_size
        total_rows = len(values)

        if total_rows == 0:
            return BulkSheetsResult()

        result = BulkSheetsResult(total_rows=total_rows)
        errors: list[str] = []

        for i in range(0, total_rows, batch_size):
            batch = values[i:i + batch_size]
            try:
                write_result = await self.append(spreadsheet_id, range_name, batch)
                result.rows_written += write_result.rows_written
                result.batches += 1
            except Exception as e:
                error_msg = f"批次 {i // batch_size + 1} 写入失败: {e}"
                logger.error(error_msg)
                errors.append(error_msg)
                result.rows_failed += len(batch)

        result.errors = errors
        return result

    # ── gem-metadata 表操作 ─────────────────────────────────

    async def get_gem_metadata(
        self,
        spreadsheet_id: str,
        active_only: bool = True,
    ) -> list[GemMetadata]:
        """获取所有 Gem 元数据

        Args:
            spreadsheet_id: 电子表格 ID
            active_only: 是否只返回启用的 Gem

        Returns:
            GemMetadata 列表
        """
        result = await self.read(spreadsheet_id, GEM_METADATA_RANGE)

        if not result.data:
            return []

        gems = SheetsSerializer.bulk_rows_to_models(
            result.data, GemMetadata, skip_header=False
        )

        if active_only:
            gems = [g for g in gems if g.is_active]

        return gems

    async def add_gem_metadata(
        self,
        spreadsheet_id: str,
        gem: GemMetadata,
    ) -> SheetsWriteResult:
        """添加 Gem 元数据

        Args:
            spreadsheet_id: 电子表格 ID
            gem: Gem 元数据对象

        Returns:
            SheetsWriteResult
        """
        row = gem.to_sheets_row()
        return await self.append(spreadsheet_id, GEM_METADATA_APPEND_RANGE, [row])

    async def update_gem_metadata(
        self,
        spreadsheet_id: str,
        row_index: int,
        gem: GemMetadata,
    ) -> SheetsWriteResult:
        """更新 Gem 元数据

        Args:
            spreadsheet_id: 电子表格 ID
            row_index: 行索引（0-based，不含表头）
            gem: 更新的 Gem 元数据

        Returns:
            SheetsWriteResult
        """
        # 行索引 + 2（表头占1行，且 Sheets 是1-based）
        sheet_row = row_index + 2
        range_name = f"gem-metadata!A{sheet_row}:Y{sheet_row}"
        row = gem.to_sheets_row()
        return await self.write(spreadsheet_id, range_name, [row])

    # ── task-queue 表操作 ───────────────────────────────────

    async def get_pending_tasks(
        self,
        spreadsheet_id: str,
        batch_id: Optional[str] = None,
    ) -> list[TaskRecord]:
        """获取所有待处理任务

        Args:
            spreadsheet_id: 电子表格 ID
            batch_id: 可选批次 ID 过滤

        Returns:
            TaskRecord 列表
        """
        result = await self.read(spreadsheet_id, TASK_QUEUE_RANGE)

        if not result.data:
            return []

        tasks = SheetsSerializer.bulk_rows_to_models(
            result.data, TaskRecord, skip_header=False
        )

        # 过滤 pending 状态
        pending = [t for t in tasks if t.status == "pending"]

        if batch_id:
            pending = [t for t in pending if t.batch_id == batch_id]

        return pending

    async def add_task(
        self,
        spreadsheet_id: str,
        task: TaskRecord,
    ) -> SheetsWriteResult:
        """添加任务

        Args:
            spreadsheet_id: 电子表格 ID
            task: 任务记录

        Returns:
            SheetsWriteResult
        """
        row = task.to_sheets_row()
        return await self.append(spreadsheet_id, TASK_QUEUE_APPEND_RANGE, [row])

    async def update_task_status(
        self,
        spreadsheet_id: str,
        row_index: int,
        new_status: str,
        **updates: Any,
    ) -> SheetsWriteResult:
        """更新任务状态

        Args:
            spreadsheet_id: 电子表格 ID
            row_index: 行索引（0-based，不含表头）
            new_status: 新状态
            **updates: 额外更新字段

        Returns:
            SheetsWriteResult
        """
        # 先读取当前行
        sheet_row = row_index + 2
        range_name = f"task-queue!A{sheet_row}:AC{sheet_row}"

        read_result = await self.read(spreadsheet_id, range_name)
        if not read_result.data:
            raise SheetsApiError(
                message=f"找不到行 {row_index}",
                operation="sheets.update_task_status",
            )

        task = TaskRecord.from_sheets_row(read_result.data[0], row_index)
        task.transition_status(new_status)

        # 应用额外更新
        for key, value in updates.items():
            if hasattr(task, key):
                setattr(task, key, value)

        row = task.to_sheets_row()
        return await self.write(spreadsheet_id, range_name, [row])

    async def batch_update_tasks(
        self,
        spreadsheet_id: str,
        tasks: list[TaskRecord],
    ) -> BulkSheetsResult:
        """批量更新任务

        Args:
            spreadsheet_id: 电子表格 ID
            tasks: 任务列表（需设置 source_row_index）

        Returns:
            BulkSheetsResult
        """
        rows = []
        for task in tasks:
            if task.source_row_index is None:
                raise ValidationError(
                    message="批量更新需要设置 source_row_index",
                    field="source_row_index",
                )
            row = task.to_sheets_row()
            sheet_row = task.source_row_index + 2
            range_name = f"task-queue!A{sheet_row}:AC{sheet_row}"
            rows.append((range_name, [row]))

        result = BulkSheetsResult(total_rows=len(tasks))
        errors: list[str] = []

        for range_name, row_data in rows:
            try:
                await self.write(spreadsheet_id, range_name, row_data)
                result.rows_written += 1
            except Exception as e:
                error_msg = f"更新范围 {range_name} 失败: {e}"
                logger.error(error_msg)
                errors.append(error_msg)
                result.rows_failed += 1

        result.errors = errors
        result.batches = len(rows)
        return result

    # ── batch-summary 表操作 ────────────────────────────────

    async def get_batch_summaries(
        self,
        spreadsheet_id: str,
    ) -> list[BatchSummary]:
        """获取所有批次汇总

        Args:
            spreadsheet_id: 电子表格 ID

        Returns:
            BatchSummary 列表
        """
        result = await self.read(spreadsheet_id, BATCH_SUMMARY_RANGE)

        if not result.data:
            return []

        return SheetsSerializer.bulk_rows_to_models(
            result.data, BatchSummary, skip_header=False
        )

    async def add_batch_summary(
        self,
        spreadsheet_id: str,
        summary: BatchSummary,
    ) -> SheetsWriteResult:
        """添加批次汇总

        Args:
            spreadsheet_id: 电子表格 ID
            summary: 批次汇总对象

        Returns:
            SheetsWriteResult
        """
        row = summary.to_sheets_row()
        return await self.append(spreadsheet_id, BATCH_SUMMARY_APPEND_RANGE, [row])

    async def update_batch_summary(
        self,
        spreadsheet_id: str,
        row_index: int,
        summary: BatchSummary,
    ) -> SheetsWriteResult:
        """更新批次汇总

        Args:
            spreadsheet_id: 电子表格 ID
            row_index: 行索引（0-based）
            summary: 更新的批次汇总

        Returns:
            SheetsWriteResult
        """
        sheet_row = row_index + 2
        range_name = f"batch-summary!A{sheet_row}:W{sheet_row}"
        row = summary.to_sheets_row()
        return await self.write(spreadsheet_id, range_name, [row])


__all__ = [
    "SheetsManager",
    "GemMetadata",
    "TaskRecord",
    "BatchSummary",
    "SheetsSerializer",
    "GEM_METADATA_HEADERS",
    "TASK_QUEUE_HEADERS",
    "BATCH_SUMMARY_HEADERS",
    "VALID_STATUS_TRANSITIONS",
]
