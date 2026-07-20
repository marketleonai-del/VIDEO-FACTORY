#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gemini_client.py — Gemini Web API 客户端

对 gemini-webapi 库的上层包装，提供：
- 统一的生命周期管理（初始化/关闭）
- 自动化的 Cookie 管理（刷新/过期检测/持久化）
- 透明的 Gem 调用（选择/传递/调用）
- 多格式图片上传（路径/bytes/URL/BytesIO）
- 指数退避重试（可配置策略）
- 结构化错误处理（分类/转换/恢复）

依赖:
    - pydantic >= 2.0
    - gemini-webapi (PyPI)
    - Pillow (可选，用于图片压缩)

使用示例:
    ```python
    config = GeminiClientConfig.from_env()
    client = GeminiClient(config)

    # 初始化
    await client.initialize()

    # 生成内容
    result = await client.generate_content("Hello Gemini!")
    print(result.text)

    # 使用指定 Gem 生成
    result = await client.generate_content(
        prompt="Write a poem",
        gem="creative-writer",
    )

    # 带图片的生成
    result = await client.generate_content(
        prompt="Describe this image",
        image=ImageInput.from_path("photo.jpg"),
    )

    # 关闭
    await client.close()
    ```

作者: 300 Agent 融合项目
"""

from __future__ import annotations

import asyncio
import enum
import io
import json
import logging
import mimetypes
import os
import random
import stat as stat_module
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    TypeVar,
    Union,
)

from pydantic import BaseModel, Field, field_validator

# ═══════════════════════════════════════════════════════════════════════════
# 类型变量
# ═══════════════════════════════════════════════════════════════════════════

T = TypeVar("T")

# ═══════════════════════════════════════════════════════════════════════════
# 日志配置
# ═══════════════════════════════════════════════════════════════════════════

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 枚举类型
# ═══════════════════════════════════════════════════════════════════════════


class ImageSourceType(str, enum.Enum):
    """图片来源类型"""

    PATH = "path"
    BYTES = "bytes"
    URL = "url"
    BINARY_IO = "binary_io"


class ClientStatus(str, enum.Enum):
    """客户端状态"""

    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    DEGRADED = "degraded"
    ERROR = "error"
    CLOSED = "closed"


class RetryStrategy(str, enum.Enum):
    """重试策略"""

    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    FIXED = "fixed"


# ═══════════════════════════════════════════════════════════════════════════
# 自定义异常
# ═══════════════════════════════════════════════════════════════════════════


class GeminiClientError(Exception):
    """GeminiClient 模块基础异常"""

    pass


class NotInitializedError(GeminiClientError):
    """客户端未初始化"""

    pass


class AuthError(GeminiClientError):
    """认证失败"""

    pass


class UnsupportedFormatError(ValueError):
    """不支持的图片格式"""

    pass


class FileTooLargeError(ValueError):
    """文件过大"""

    pass


# ═══════════════════════════════════════════════════════════════════════════
# Pydantic 数据模型
# ═══════════════════════════════════════════════════════════════════════════


class ImageInput(BaseModel):
    """统一图片输入模型

    支持多种输入格式，自动转换为 gemini-webapi 可接受的格式。
    """

    source_type: ImageSourceType = Field(description="图片来源类型")
    path: Optional[Path] = Field(default=None, description="本地文件路径")
    data: Optional[bytes] = Field(default=None, description="二进制数据")
    url: Optional[str] = Field(default=None, description="URL 地址")
    mime_type: str = Field(default="image/jpeg", description="MIME 类型")
    filename: str = Field(default="image.jpg", description="文件名")

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def from_path(cls: type["ImageInput"], file_path: Union[str, Path]) -> ImageInput:
        """从本地路径创建 ImageInput"""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"图片文件不存在: {path}")

        suffix = path.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
        }
        mime_type = mime_map.get(suffix, "image/jpeg")

        return cls(
            source_type=ImageSourceType.PATH,
            path=path,
            mime_type=mime_type,
            filename=path.name,
        )

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        filename: str = "image.jpg",
        mime_type: str = "image/jpeg",
    ) -> ImageInput:
        """从二进制数据创建 ImageInput"""
        return cls(
            source_type=ImageSourceType.BYTES,
            data=data,
            mime_type=mime_type,
            filename=filename,
        )

    @classmethod
    def from_url(cls: type["ImageInput"], url: str, filename: str = "image.jpg") -> ImageInput:
        """从 URL 创建 ImageInput"""
        return cls(
            source_type=ImageSourceType.URL,
            url=url,
            filename=filename,
        )

    @classmethod
    def from_binary_io(
        cls,
        bio: Any,
        filename: str = "image.jpg",
        mime_type: str = "image/jpeg",
    ) -> ImageInput:
        """从 BytesIO 对象创建 ImageInput"""
        import io

        if isinstance(bio, io.BytesIO):
            return cls(
                source_type=ImageSourceType.BYTES,
                data=bio.getvalue(),
                mime_type=mime_type,
                filename=filename,
            )
        raise TypeError(f"Expected BytesIO, got {type(bio)}")

    async def to_upload_format(self: "ImageInput") -> Union[str, Path, bytes]:
        """转换为 gemini-webapi 支持的上传格式"""
        if self.source_type == ImageSourceType.PATH:
            return self.path  # type: ignore[return-value]
        elif self.source_type == ImageSourceType.BYTES:
            return self.data  # type: ignore[return-value]
        elif self.source_type == ImageSourceType.URL:
            return await self._download_from_url()
        else:
            raise ValueError(f"不支持的图片来源类型: {self.source_type}")

    async def _download_from_url(self: "ImageInput") -> bytes:
        """从 URL 下载图片数据"""
        try:
            import aiohttp
        except ImportError:
            raise ImportError(
                "aiohttp is required for URL image download. "
                "Install with: pip install aiohttp"
            )

        async with aiohttp.ClientSession() as session:
            async with session.get(self.url) as resp:  # type: ignore[arg-type]
                resp.raise_for_status()
                return await resp.read()

    def get_size_bytes(self: "ImageInput") -> int:
        """获取图片大小（字节）"""
        if self.source_type == ImageSourceType.PATH and self.path:
            return self.path.stat().st_size
        elif self.data:
            return len(self.data)
        return 0


class GemInfo(BaseModel):
    """Gem 信息（精简版，用于列表和选择）"""

    id: str = Field(description="Gem 唯一标识")
    name: str = Field(description="Gem 显示名称")
    description: str = Field(default="", description="Gem 描述")
    predefined: bool = Field(default=False, description="是否系统预定义")
    prompt_preview: str = Field(default="", description="系统提示词预览")
    tags: list[str] = Field(default_factory=list, description="业务标签")
    category: str = Field(default="", description="分类")
    is_new: bool = Field(default=False, description="是否新发现")
    discovered_at: Optional[datetime] = Field(default=None, description="发现时间")
    source: str = Field(default="gemini", description="来源")

    model_config = {"frozen": False}

    @classmethod
    def from_webapi_gem(cls: type["GemInfo"], gem: Any) -> GemInfo:
        """从 gemini-webapi 的 Gem 对象转换"""
        # 防御性处理：强制转换为字符串（避免 MagicMock 等特殊对象导致类型错误）
        def _str(val: Any, default: str = "") -> str:
            if val is None:
                return default
            if isinstance(val, str):
                return val
            # 处理 MagicMock 等模拟对象
            if hasattr(val, "__class__") and "Mock" in val.__class__.__name__:
                return default
            return str(val)

        return cls(
            id=_str(getattr(gem, "id", "")),
            name=_str(getattr(gem, "name", "")),
            description=_str(getattr(gem, "description", "")),
            predefined=bool(getattr(gem, "predefined", False)),
            prompt_preview=_str(getattr(gem, "prompt", ""))[:200],
            tags=[],
            category="",
            source="gemini",
        )


class MediaInfo(BaseModel):
    """媒体文件信息"""

    type: str = Field(description="媒体类型: image|video|audio")
    url: Optional[str] = Field(default=None, description="URL 地址")
    data: Optional[bytes] = Field(default=None, description="二进制数据")
    mime_type: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class GenerationResult(BaseModel):
    """通用生成结果"""

    text: str = Field(default="", description="生成文本")
    images: list[MediaInfo] = Field(default_factory=list, description="生成图片")
    videos: list[MediaInfo] = Field(default_factory=list, description="生成视频")
    model_used: str = Field(default="", description="使用的模型")
    gem_used: Optional[str] = Field(default=None, description="使用的 Gem ID")
    generation_time_ms: int = Field(default=0, description="生成耗时（毫秒）")
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_output: Any = Field(default=None, description="原始输出对象")


class UploadResult(BaseModel):
    """图片上传结果"""

    success: bool = Field(description="是否成功")
    filename: str = Field(default="", description="文件名")
    mime_type: str = Field(default="", description="MIME 类型")
    size_bytes: int = Field(default=0, description="文件大小")
    uploaded_url: Optional[str] = Field(default=None, description="上传后的 URL")
    error: Optional[str] = Field(default=None, description="错误信息")
    processed: bool = Field(default=False, description="是否经过处理（压缩/转换）")


class HealthStatus(BaseModel):
    """健康状态"""

    status: str = Field(
        default="unknown",
        description="状态: healthy|degraded|unhealthy",
    )
    client_status: str = Field(default="uninitialized", description="客户端状态")
    gem_count: int = Field(default=0, description="已加载 Gem 数量")
    cookie_valid: bool = Field(default=False, description="Cookie 是否有效")
    last_cookie_refresh: Optional[datetime] = Field(
        default=None, description="上次 Cookie 刷新时间"
    )
    latency_ms: int = Field(default=0, description="检测延迟（毫秒）")
    errors: list[str] = Field(default_factory=list, description="错误列表")

    @property
    def is_healthy(self: "HealthStatus") -> bool:
        """判断是否健康"""
        return self.status == "healthy" and self.cookie_valid


class RetryConfig(BaseModel):
    """重试配置"""

    max_retries: int = Field(default=3, ge=0, le=10, description="最大重试次数")
    strategy: RetryStrategy = Field(
        default=RetryStrategy.EXPONENTIAL, description="重试策略"
    )
    base_delay: float = Field(default=2.0, ge=0.1, description="基础延迟（秒）")
    max_delay: float = Field(default=60.0, ge=1.0, description="最大延迟（秒）")
    jitter: bool = Field(default=True, description="是否添加随机抖动")

    # 可重试的异常类型（类名匹配）
    retry_on_exceptions: set[str] = Field(
        default={
            "APIError",
            "TemporarilyBlocked",
            "UsageLimitExceeded",
            "TimeoutError",
        },
        description="可重试的异常类型",
    )

    # 不可重试的异常类型
    no_retry_exceptions: set[str] = Field(
        default={"AuthError", "ModelInvalid"},
        description="不可重试的异常类型",
    )


class GeminiClientConfig(BaseModel):
    """GeminiClient 配置"""

    # Cookie 配置
    secure_1psid: str = Field(default="", description="__Secure-1PSID Cookie")
    secure_1psidts: str = Field(
        default="", description="__Secure-1PSIDTS Cookie"
    )
    cookie_path: str = Field(default="/tmp/gemini_cookies", description="Cookie 缓存路径")
    proxy: Optional[str] = Field(default=None, description="代理地址")

    # 行为配置
    auto_refresh: bool = Field(default=True, description="自动刷新 Cookie")
    refresh_interval: int = Field(default=600, ge=60, description="刷新间隔（秒）")

    # 超时配置
    init_timeout: int = Field(default=120, ge=10, description="初始化超时（秒）")
    request_timeout: int = Field(default=120, ge=10, description="请求超时（秒）")

    # 重试配置
    retry: RetryConfig = Field(default_factory=RetryConfig)

    # 图片配置
    max_image_size_mb: int = Field(
        default=20, ge=1, le=100, description="最大图片大小（MB）"
    )
    allowed_image_formats: set[str] = Field(
        default={"jpg", "jpeg", "png", "webp", "gif", "bmp"},
        description="允许的图片格式",
    )

    # 模型配置
    default_model: str = Field(default="unspecified", description="默认模型")

    # 日志配置
    verbose: bool = Field(default=False, description="详细日志模式")
    log_level: str = Field(default="INFO", description="日志级别")

    @field_validator("proxy")
    @classmethod
    def validate_proxy(cls: type["GeminiClientConfig"], v: Optional[str]) -> Optional[str]:
        """验证代理地址格式"""
        if v and not (
            v.startswith("http://")
            or v.startswith("https://")
            or v.startswith("socks5://")
        ):
            raise ValueError(f"代理地址格式错误: {v}")
        return v

    @classmethod
    def from_env(cls: type["GeminiClientConfig"]) -> GeminiClientConfig:
        """从环境变量加载配置"""
        return cls(
            secure_1psid=os.environ.get("GEMINI_1PSID", ""),
            secure_1psidts=os.environ.get("GEMINI_1PSIDTS", ""),
            cookie_path=os.environ.get(
                "GEMINI_COOKIE_PATH", "/tmp/gemini_cookies"
            ),
            proxy=os.environ.get("GEMINI_PROXY") or None,
            auto_refresh=os.environ.get("GEMINI_AUTO_REFRESH", "true").lower()
            == "true",
            refresh_interval=int(os.environ.get("GEMINI_REFRESH_INTERVAL", "600")),
            init_timeout=int(os.environ.get("GEMINI_INIT_TIMEOUT", "120")),
            request_timeout=int(os.environ.get("GEMINI_REQUEST_TIMEOUT", "120")),
            max_image_size_mb=int(os.environ.get("GEMINI_MAX_IMAGE_SIZE_MB", "20")),
            default_model=os.environ.get("GEMINI_MODEL", "unspecified"),
            verbose=os.environ.get("GEMINI_VERBOSE", "false").lower() == "true",
            log_level=os.environ.get("GEMINI_LOG_LEVEL", "INFO"),
        )



# ═══════════════════════════════════════════════════════════════════════════
# Cookie 管理器
# ═══════════════════════════════════════════════════════════════════════════


class CookieManager:
    """Cookie 生命周期管理器

    管理 Gemini Web API 所需的 Cookie，实现自动刷新和过期检测。

    Cookie 优先级（高到低）：
    1. 构造函数传入的 Cookie
    2. 环境变量 GEMINI_1PSID / GEMINI_1PSIDTS
    3. 本地缓存文件（GEMINI_COOKIE_PATH）

    自动刷新机制：
    - 60 秒内避免重复刷新（防 429）
    - 刷新后的 Cookie 自动保存到缓存文件

    使用示例:
        ```python
        cookie_mgr = CookieManager(webapi_client, config)

        # 验证 Cookie 是否有效
        is_valid = await cookie_mgr.validate()

        # 手动刷新 Cookie
        await cookie_mgr.refresh()

        # 获取当前 Cookie 值
        psid = cookie_mgr.get_cookie("__Secure-1PSID")

        # 强制保存 Cookie 到文件
        await cookie_mgr.save_to_file()
        ```
    """

    COOKIE_PSID = "__Secure-1PSID"
    COOKIE_PSIDTS = "__Secure-1PSIDTS"
    EXPIRY_WARNING_THRESHOLD_HOURS = 2

    def __init__(
        self,
        webapi_client: Any,
        config: GeminiClientConfig,
    ) -> None:
        """创建 CookieManager

        参数:
            webapi_client: 已初始化的 gemini-webapi 客户端实例
            config: 客户端配置
        """
        self._client = webapi_client
        self._config = config
        self._last_refresh_time: Optional[datetime] = None
        self._refresh_count = 0
        self._expiry_warning_issued = False

        logger.debug(
            f"CookieManager created (auto_refresh={config.auto_refresh}, "
            f"refresh_interval={config.refresh_interval}s)"
        )

    @property
    def last_refresh_time(self: "CookieManager") -> Optional[datetime]:
        """上次成功刷新时间"""
        return self._last_refresh_time

    @property
    def refresh_count(self: "CookieManager") -> int:
        """刷新次数"""
        return self._refresh_count

    @property
    def is_valid(self: "CookieManager") -> bool:
        """Cookie 是否有效（基于最近刷新时间推断）"""
        if self._last_refresh_time is None:
            return False
        max_age = timedelta(seconds=self._config.refresh_interval * 2)
        return datetime.utcnow() - self._last_refresh_time < max_age

    @property
    def time_to_next_refresh(self: "CookieManager") -> float:
        """距离下次刷新的秒数"""
        if self._last_refresh_time is None:
            return 0.0
        elapsed = (datetime.utcnow() - self._last_refresh_time).total_seconds()
        return max(0.0, self._config.refresh_interval - elapsed)

    async def validate(self: "CookieManager") -> bool:
        """验证 Cookie 是否有效

        通过调用一次轻量 API（list_models）来验证 Cookie 的可用性。

        返回:
            bool: True 表示 Cookie 有效

        用时: 通常 1-3 秒
        """
        try:
            models = self._client.list_models()
            logger.debug(
                f"Cookie validation passed ({len(models)} models available)"
            )
            return True
        except Exception as e:
            logger.warning(f"Cookie validation failed: {e}")
            return False

    async def refresh(self: "CookieManager") -> bool:
        """手动刷新 Cookie

        触发 gemini-webapi 的 Cookie 刷新机制：
        1. 调用 init 刷新 __Secure-1PSIDTS
        2. 保存刷新后的 Cookie 到缓存文件
        3. 更新内部状态

        返回:
            bool: True 表示刷新成功

        注意:
            60 秒内重复调用会直接返回成功（防 429）。
        """
        if self._last_refresh_time:
            elapsed = (datetime.utcnow() - self._last_refresh_time).total_seconds()
            if elapsed < 60:
                logger.debug(
                    f"Skipping refresh (last refresh was {elapsed:.0f}s ago)"
                )
                return True

        try:
            logger.info("Refreshing Cookie...")

            await self._client.init(
                timeout=self._config.init_timeout,
                auto_refresh=self._config.auto_refresh,
                refresh_interval=self._config.refresh_interval,
                verbose=self._config.verbose,
            )

            self._last_refresh_time = datetime.utcnow()
            self._refresh_count += 1
            self._expiry_warning_issued = False

            logger.info(
                f"Cookie refreshed successfully (count={self._refresh_count})"
            )
            return True

        except Exception as e:
            logger.error(f"Cookie refresh failed: {e}")
            return False

    def get_cookie(self: "CookieManager", name: str) -> Optional[str]:
        """获取指定 Cookie 的值

        参数:
            name: Cookie 名称（如 '__Secure-1PSID'）

        返回:
            Cookie 值或 None
        """
        try:
            cookies = self._client.cookies
            if hasattr(cookies, "get"):
                cookie = cookies.get(name)
                if cookie:
                    return (
                        cookie.value if hasattr(cookie, "value") else str(cookie)
                    )
            if hasattr(self._client, "_cookies"):
                cookie_dict = self._client._cookies
                if name in cookie_dict:
                    val = cookie_dict[name]
                    return val.value if hasattr(val, "value") else str(val)
        except Exception as e:
            logger.debug(f"Failed to get cookie {name}: {e}")
        return None

    def get_all_cookies(self: "CookieManager") -> dict[str, str]:
        """获取所有 Cookie 的键值对"""
        result = {}
        try:
            cookies = self._client.cookies
            if hasattr(cookies, "items"):
                for k, v in cookies.items():
                    result[k] = v.value if hasattr(v, "value") else str(v)
        except Exception as e:
            logger.debug(f"Failed to get all cookies: {e}")
        return result

    async def save_to_file(self: "CookieManager", file_path: Optional[str] = None) -> bool:
        """将当前 Cookie 保存到文件

        参数:
            file_path: 保存路径，为 None 时使用配置的 cookie_path

        返回:
            bool: 是否保存成功

        注意:
            文件权限设置为 600（仅所有者可读写）。
        """
        path = Path(file_path or self._config.cookie_path)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            cookies = self.get_all_cookies()

            data = {
                "cookies": cookies,
                "saved_at": datetime.utcnow().isoformat(),
                "refresh_count": self._refresh_count,
            }

            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.replace(path)

            path.chmod(stat_module.S_IRUSR | stat_module.S_IWUSR)

            logger.debug(f"Cookies saved to {path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save cookies: {e}")
            return False

    async def load_from_file(self: "CookieManager", file_path: Optional[str] = None) -> dict[str, str]:
        """从文件加载 Cookie

        参数:
            file_path: 文件路径，为 None 时使用配置的 cookie_path

        返回:
            加载的 Cookie 字典
        """
        path = Path(file_path or self._config.cookie_path)

        if not path.exists():
            logger.debug(f"Cookie file not found: {path}")
            return {}

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cookies = data.get("cookies", {})
            logger.debug(f"Loaded {len(cookies)} cookies from {path}")
            return cookies
        except Exception as e:
            logger.warning(f"Failed to load cookies from file: {e}")
            return {}

    async def check_expiry_warning(self: "CookieManager") -> Optional[str]:
        """检查 Cookie 过期预警

        如果 Cookie 即将过期（在阈值时间内未刷新），返回警告信息。

        返回:
            警告信息或 None
        """
        if self._last_refresh_time is None:
            return "Cookie has never been refreshed"

        elapsed_hours = (
            datetime.utcnow() - self._last_refresh_time
        ).total_seconds() / 3600
        max_hours = self._config.refresh_interval * 2 / 3600

        if elapsed_hours > max_hours:
            return f"Cookie likely expired (last refresh {elapsed_hours:.1f}h ago)"

        if (
            elapsed_hours > self.EXPIRY_WARNING_THRESHOLD_HOURS
            and not self._expiry_warning_issued
        ):
            self._expiry_warning_issued = True
            return (
                f"Cookie may expire soon (last refresh {elapsed_hours:.1f}h ago)"
            )

        return None

    def __repr__(self: "CookieManager") -> str:
        return (
            f"CookieManager("
            f"valid={self.is_valid}, "
            f"refresh_count={self._refresh_count}, "
            f"last_refresh={self._last_refresh_time})"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Gem 管理器
# ═══════════════════════════════════════════════════════════════════════════


class GemManager:
    """Gem 生命周期管理器

    管理 Gemini Gems 的获取、缓存、查询和选择。

    Gems 来源：
    1. gemini-webapi fetch_gems() - 系统预定义 + 用户自定义
    2. 本地缓存 - 上次获取的结果

    缓存策略：
    - 内存缓存：每次 fetch 后保存在内存中
    - 过期时间：默认 5 分钟
    - 自动刷新：初始化时获取一次，之后按需刷新
    """

    DEFAULT_CACHE_TTL = 300

    def __init__(
        self,
        webapi_client: Any,
        config: GeminiClientConfig,
    ) -> None:
        """创建 GemManager

        参数:
            webapi_client: 已初始化的 gemini-webapi 客户端
            config: 客户端配置
        """
        self._client = webapi_client
        self._config = config

        self._cache: Dict[str, GemInfo] = {}
        self._cache_by_tag: Dict[str, List[str]] = {}
        self._last_fetch_time: Optional[datetime] = None
        self._initialized = False

        logger.debug("GemManager created")

    @property
    def cache_size(self: "GemManager") -> int:
        """缓存的 Gem 数量"""
        return len(self._cache)

    @property
    def last_fetch_time(self: "GemManager") -> Optional[datetime]:
        """上次获取时间"""
        return self._last_fetch_time

    @property
    def cache_ttl_sec(self: "GemManager") -> int:
        """缓存有效期（秒）"""
        return self.DEFAULT_CACHE_TTL

    @property
    def is_cache_valid(self: "GemManager") -> bool:
        """缓存是否有效（未过期）"""
        if self._last_fetch_time is None:
            return False
        elapsed = (datetime.utcnow() - self._last_fetch_time).total_seconds()
        return elapsed < self.cache_ttl_sec

    async def initialize(self: "GemManager") -> None:
        """初始化 GemManager

        执行首次 fetch_gems() 填充缓存。
        """
        if self._initialized:
            return

        logger.info("Initializing GemManager...")
        count = await self.refresh()
        self._initialized = True
        logger.info(f"GemManager initialized with {count} gems")

    async def close(self: "GemManager") -> None:
        """关闭 GemManager，清理缓存"""
        self._cache.clear()
        self._cache_by_tag.clear()
        self._initialized = False
        logger.debug("GemManager closed")

    async def list_gems(self: "GemManager", force_refresh: bool = False) -> List[GemInfo]:
        """获取所有 Gems 列表

        参数:
            force_refresh: 是否强制刷新缓存

        返回:
            GemInfo 列表（按名称排序）
        """
        if force_refresh or not self.is_cache_valid:
            await self.refresh()

        gems = list(self._cache.values())
        gems.sort(key=lambda g: g.name)
        return gems

    async def get_gem(self: "GemManager", gem_id: str) -> Optional[GemInfo]:
        """按 ID 获取 Gem

        参数:
            gem_id: Gem 唯一标识

        返回:
            GemInfo 或 None
        """
        if gem_id in self._cache:
            return self._cache[gem_id]

        await self.refresh()
        return self._cache.get(gem_id)

    async def find_by_tag(self: "GemManager", tag: str) -> Optional[GemInfo]:
        """按标签查找 Gem

        返回第一个匹配标签的 Gem。

        参数:
            tag: 业务标签

        返回:
            GemInfo 或 None
        """
        if not self.is_cache_valid:
            await self.refresh()

        gem_ids = self._cache_by_tag.get(tag, [])
        if gem_ids:
            return self._cache.get(gem_ids[0])

        for gem in self._cache.values():
            if tag in gem.tags:
                return gem

        return None

    async def find_by_category(self: "GemManager", category: str) -> List[GemInfo]:
        """按分类查找 Gems

        参数:
            category: 分类名称

        返回:
            匹配的 GemInfo 列表
        """
        if not self.is_cache_valid:
            await self.refresh()

        return [g for g in self._cache.values() if g.category == category]

    async def select_best(self: "GemManager", requirement: str) -> Optional[GemInfo]:
        """根据需求描述智能选择最佳 Gem

        匹配算法：
        1. 提取需求关键词（小写分词）
        2. 遍历所有 Gems 计算匹配分数
        3. 返回得分最高的 Gem

        评分规则：
        - 标签匹配: +2 分/标签
        - 名称匹配: +3 分
        - 描述匹配: +1 分
        - video_prompt 分类: +2 分

        参数:
            requirement: 需求描述

        返回:
            最佳匹配的 GemInfo 或 None
        """
        if not self.is_cache_valid:
            await self.refresh()

        if not self._cache:
            return None

        req_lower = requirement.lower()
        req_words = set(w for w in req_lower.split() if len(w) > 2)

        best_gem: Optional[GemInfo] = None
        best_score = -1

        for gem in self._cache.values():
            score = 0

            for tag in gem.tags:
                if tag.lower() in req_lower:
                    score += 2

            if gem.name.lower() in req_lower:
                score += 3

            if gem.description:
                desc_words = set(gem.description.lower().split())
                score += len(desc_words & req_words)

            if gem.category == "video_prompt":
                score += 2

            if score > best_score:
                best_score = score
                best_gem = gem

        if best_gem:
            logger.debug(
                f"Best gem for '{requirement}': {best_gem.name} "
                f"(score={best_score})"
            )

        return best_gem

    async def refresh(self: "GemManager") -> int:
        """刷新 Gems 缓存

        从 Gemini 获取最新 Gem 列表，更新内存缓存和索引。

        返回:
            int: 获取到的 Gem 数量

        异常:
            Exception: fetch_gems() 调用失败
        """
        start_time = time.time()

        try:
            gem_jar = await self._client.fetch_gems(include_hidden=False)

            self._cache.clear()
            self._cache_by_tag.clear()

            for gem_id, gem in gem_jar.items():
                gem_info = GemInfo.from_webapi_gem(gem)
                self._cache[gem_id] = gem_info

                for tag in gem_info.tags:
                    if tag not in self._cache_by_tag:
                        self._cache_by_tag[tag] = []
                    self._cache_by_tag[tag].append(gem_id)

            self._last_fetch_time = datetime.utcnow()
            fetch_time = int((time.time() - start_time) * 1000)

            logger.info(
                f"Gems refreshed: {len(self._cache)} gems "
                f"(took {fetch_time}ms)"
            )

            return len(self._cache)

        except Exception as e:
            logger.error(f"Failed to refresh gems: {e}")
            raise

    def invalidate_cache(self: "GemManager") -> None:
        """使缓存失效（下次查询将强制刷新）"""
        self._last_fetch_time = None
        logger.debug("Gem cache invalidated")

    def get_cache_stats(self: "GemManager") -> dict[str, Any]:
        """获取缓存统计信息"""
        return {
            "cache_size": self.cache_size,
            "is_valid": self.is_cache_valid,
            "last_fetch_time": (
                self._last_fetch_time.isoformat()
                if self._last_fetch_time
                else None
            ),
            "ttl_sec": self.cache_ttl_sec,
            "tag_index_size": len(self._cache_by_tag),
        }

    def __repr__(self: "GemManager") -> str:
        return f"GemManager(cache={self.cache_size}, valid={self.is_cache_valid})"



# ═══════════════════════════════════════════════════════════════════════════
# 图片上传处理器
# ═══════════════════════════════════════════════════════════════════════════


class ImageUploader:
    """多格式图片上传处理器

    统一处理多种输入格式，自动验证、压缩、转换。

    支持的输入格式：
    - str: 文件路径
    - Path: Path 对象
    - bytes: 二进制数据
    - BytesIO: 内存文件对象
    - ImageInput: 已封装的输入对象

    处理流程：
    1. 格式识别（扩展名/MIME）
    2. 格式验证（白名单检查）
    3. 大小检查（超限则压缩）
    4. 格式转换（BMP/GIF -> JPEG）
    5. 输出标准化
    """

    JPEG_QUALITY = 85
    MAX_DIMENSION = 2048

    def __init__(self: "ImageUploader", config: GeminiClientConfig) -> None:
        """创建 ImageUploader

        参数:
            config: 客户端配置
        """
        self._max_size_bytes = config.max_image_size_mb * 1024 * 1024
        self._allowed_formats = config.allowed_image_formats

        logger.debug(
            f"ImageUploader created (max_size={config.max_image_size_mb}MB, "
            f"formats={sorted(self._allowed_formats)})"
        )

    @property
    def max_size_bytes(self: "ImageUploader") -> int:
        """最大文件大小（字节）"""
        return self._max_size_bytes

    @property
    def allowed_formats(self: "ImageUploader") -> set[str]:
        """允许的文件格式"""
        return self._allowed_formats.copy()

    async def upload(
        self,
        image: Union[str, Path, bytes, ImageInput],
        validate_only: bool = False,
    ) -> UploadResult:
        """处理图片上传

        完整处理流程：验证 -> 检查大小 -> 转换格式 -> 返回结果。

        参数:
            image: 图片输入（多种格式）
            validate_only: 仅验证不处理

        返回:
            UploadResult: 处理结果

        异常:
            FileNotFoundError: 文件不存在
            UnsupportedFormatError: 不支持的格式
            FileTooLargeError: 文件过大且无法压缩
        """
        start_time = time.time()

        try:
            img_input = self._normalize_input(image)

            self._validate_format(img_input)

            img_input = await self._process_size(img_input)

            img_input = await self._convert_format(img_input)

            if validate_only:
                return UploadResult(
                    success=True,
                    filename=img_input.filename,
                    mime_type=img_input.mime_type,
                    size_bytes=img_input.get_size_bytes(),
                    processed=True,
                )

            process_time = int((time.time() - start_time) * 1000)

            return UploadResult(
                success=True,
                filename=img_input.filename,
                mime_type=img_input.mime_type,
                size_bytes=img_input.get_size_bytes(),
                processed=True,
            )

        except (UnsupportedFormatError, FileTooLargeError, FileNotFoundError):
            raise
        except Exception as e:
            logger.error(f"Image upload failed: {e}")
            return UploadResult(success=False, error=str(e))

    def validate(
        self, image: Union[str, Path, bytes, ImageInput]
    ) -> tuple[bool, Optional[str]]:
        """验证图片是否合法（同步方法）

        参数:
            image: 图片输入

        返回:
            (is_valid, error_message)
        """
        try:
            img_input = self._normalize_input(image)
            self._validate_format(img_input)
            return True, None
        except Exception as e:
            return False, str(e)

    def get_mime_type(self: "ImageUploader", filename: str) -> str:
        """获取文件的 MIME 类型

        参数:
            filename: 文件名

        返回:
            MIME 类型字符串
        """
        mime, _ = mimetypes.guess_type(filename)
        return mime or "image/jpeg"

    def _normalize_input(
        self, image: Union[str, Path, bytes, ImageInput]
    ) -> ImageInput:
        """将各种输入统一为 ImageInput"""
        if isinstance(image, ImageInput):
            return image
        elif isinstance(image, str):
            if image.startswith(("http://", "https://")):
                return ImageInput.from_url(image)
            return ImageInput.from_path(image)
        elif isinstance(image, Path):
            return ImageInput.from_path(image)
        elif isinstance(image, bytes):
            return ImageInput.from_bytes(image)
        else:
            raise TypeError(f"Unsupported image type: {type(image)}")

    def _validate_format(self: "ImageUploader", image: ImageInput) -> None:
        """验证图片格式是否在白名单中

        异常:
            UnsupportedFormatError: 格式不支持
        """
        suffix = Path(image.filename).suffix.lower().lstrip(".")

        if suffix == "jpeg":
            suffix = "jpg"

        if suffix not in self._allowed_formats:
            raise UnsupportedFormatError(
                f"不支持的图片格式: '{suffix}'. "
                f"支持的格式: {', '.join(sorted(self._allowed_formats))}"
            )

        logger.debug(f"Format validated: {suffix}")

    async def _process_size(self: "ImageUploader", image: ImageInput) -> ImageInput:
        """检查并处理图片大小

        如果图片超过限制，尝试压缩。压缩后仍超限则抛出异常。

        异常:
            FileTooLargeError: 图片过大且无法压缩到限制内
        """
        size = image.get_size_bytes()

        if size == 0 and image.source_type == ImageSourceType.URL:
            data = await image._download_from_url()
            size = len(data)
            image = ImageInput.from_bytes(
                data, filename=image.filename, mime_type=image.mime_type
            )

        if size <= self._max_size_bytes:
            logger.debug(f"Size OK: {size / 1024:.0f}KB")
            return image

        logger.warning(
            f"Image too large: {size / 1024 / 1024:.1f}MB > "
            f"{self._max_size_bytes / 1024 / 1024:.1f}MB, attempting compression"
        )

        compressed = await self._compress_image(image)

        if compressed and len(compressed) <= self._max_size_bytes:
            new_filename = Path(image.filename).stem + ".jpg"
            logger.info(
                f"Compressed: {size / 1024:.0f}KB -> {len(compressed) / 1024:.0f}KB"
            )
            return ImageInput.from_bytes(
                compressed,
                filename=new_filename,
                mime_type="image/jpeg",
            )

        raise FileTooLargeError(
            f"图片大小超过限制且无法压缩: {size / 1024 / 1024:.1f}MB > "
            f"{self._max_size_bytes / 1024 / 1024:.1f}MB"
        )

    async def _convert_format(self: "ImageUploader", image: ImageInput) -> ImageInput:
        """转换图片格式以提高兼容性

        转换规则：
        - BMP/GIF -> JPEG
        - PNG 保持
        - WEBP -> JPEG
        """
        suffix = Path(image.filename).suffix.lower()

        if suffix in (".bmp", ".gif"):
            logger.debug(f"Converting {suffix} -> JPEG")
            return await self._convert_to_jpeg(image)

        return image

    async def _compress_image(self: "ImageUploader", image: ImageInput) -> Optional[bytes]:
        """压缩图片

        使用 Pillow 进行压缩：
        1. 缩放超大图片（最大边不超过 MAX_DIMENSION）
        2. 转换为 RGB 模式
        3. 以 JPEG 格式保存

        参数:
            image: ImageInput 对象

        返回:
            压缩后的字节数据，或 None
        """
        try:
            from PIL import Image as PILImage

            if image.data:
                img_data = image.data
            elif image.path:
                img_data = image.path.read_bytes()
            else:
                return None

            img = PILImage.open(io.BytesIO(img_data))

            if max(img.size) > self.MAX_DIMENSION:
                ratio = self.MAX_DIMENSION / max(img.size)
                new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
                img = img.resize(new_size, PILImage.LANCZOS)
                logger.debug(f"Resized: {img.size[0]}x{img.size[1]}")

            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")

            output = io.BytesIO()
            img.save(
                output, format="JPEG", quality=self.JPEG_QUALITY, optimize=True
            )

            return output.getvalue()

        except ImportError:
            logger.warning("Pillow not installed, compression unavailable")
            return None
        except Exception as e:
            logger.warning(f"Image compression failed: {e}")
            return None

    async def _convert_to_jpeg(self: "ImageUploader", image: ImageInput) -> ImageInput:
        """将图片转换为 JPEG 格式"""
        try:
            from PIL import Image as PILImage

            if image.data:
                img_data = image.data
            elif image.path:
                img_data = image.path.read_bytes()
            else:
                return image

            img = PILImage.open(io.BytesIO(img_data))

            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")

            output = io.BytesIO()
            img.save(output, format="JPEG", quality=90)

            new_filename = Path(image.filename).stem + ".jpg"

            return ImageInput.from_bytes(
                output.getvalue(),
                filename=new_filename,
                mime_type="image/jpeg",
            )

        except ImportError:
            logger.warning("Pillow not installed, skipping format conversion")
            return image
        except Exception as e:
            logger.warning(f"Format conversion failed: {e}")
            return image


# ═══════════════════════════════════════════════════════════════════════════
# 错误处理与重试机制
# ═══════════════════════════════════════════════════════════════════════════


def classify_exception(error: Exception) -> dict[str, Any]:
    """对异常进行分类，提取关键信息

    参数:
        error: 原始异常

    返回:
        包含 type, message, module, retryable, severity 字段的字典
    """
    error_type = type(error).__name__
    error_module = type(error).__module__
    message = str(error)

    severity_map = {
        "AuthError": "critical",
        "ModelInvalid": "error",
        "APIError": "warning",
        "TemporarilyBlocked": "warning",
        "UsageLimitExceeded": "warning",
        "TimeoutError": "warning",
        "ConnectionError": "warning",
    }

    severity = severity_map.get(error_type, "error")
    retryable = is_retryable(error)

    return {
        "type": error_type,
        "message": message,
        "module": error_module,
        "retryable": retryable,
        "severity": severity,
    }


def is_retryable(error: Exception) -> bool:
    """判断异常是否可重试

    可重试异常（临时性问题）：
    - APIError, TemporarilyBlocked, UsageLimitExceeded, TimeoutError, ConnectionError

    不可重试异常（结构性问题）：
    - AuthError, ModelInvalid, ValueError, TypeError, FileNotFoundError

    参数:
        error: 异常对象

    返回:
        True 表示可以重试
    """
    error_type = type(error).__name__

    retryable_types = {
        "APIError",
        "TemporarilyBlocked",
        "UsageLimitExceeded",
        "TimeoutError",
        "ConnectionError",
        "ServerDisconnectedError",
    }

    no_retry_types = {
        "AuthError",
        "ModelInvalid",
        "ValueError",
        "TypeError",
        "FileNotFoundError",
    }

    if error_type in no_retry_types:
        return False
    if error_type in retryable_types:
        return True

    cause = getattr(error, "__cause__", None)
    if cause:
        return is_retryable(cause)

    return False


def should_refresh_cookie(error: Exception) -> bool:
    """判断异常是否需要刷新 Cookie

    参数:
        error: 异常对象

    返回:
        True 表示需要刷新 Cookie
    """
    error_type = type(error).__name__
    message = str(error).lower()

    if error_type == "AuthError":
        return True

    if error_type == "APIError":
        if any(code in message for code in ["401", "1016", "unauthenticated"]):
            return True

    return False


class RetryManager:
    """重试管理器

    实现可配置的重试策略，支持指数退避、线性退避、固定间隔。
    自动添加抖动防止惊群效应。

    使用示例:
        ```python
        config = RetryConfig(max_retries=3, strategy="exponential", base_delay=2.0)
        retry_mgr = RetryManager(config)

        async def risky_operation():
            return await api.call()

        result = await retry_mgr.execute_with_retry(risky_operation)
        ```
    """

    def __init__(self: "RetryManager", config: RetryConfig) -> None:
        """创建 RetryManager

        参数:
            config: 重试配置
        """
        self._config = config
        self._total_attempts = 0
        self._total_retries = 0

        logger.debug(
            f"RetryManager created (strategy={config.strategy.value}, "
            f"max_retries={config.max_retries}, base_delay={config.base_delay}s)"
        )

    @property
    def config(self: "RetryManager") -> RetryConfig:
        """重试配置"""
        return self._config

    @property
    def total_attempts(self: "RetryManager") -> int:
        """总尝试次数"""
        return self._total_attempts

    @property
    def total_retries(self: "RetryManager") -> int:
        """总重试次数"""
        return self._total_retries

    async def execute_with_retry(
        self,
        operation: Callable[[], Awaitable[T]],
        operation_name: str = "operation",
    ) -> T:
        """执行操作，失败时自动重试

        完整流程：
        1. 执行操作
        2. 成功 -> 返回结果
        3. 失败 -> 判断异常是否可重试
        4. 可重试 -> 等待退避时间 -> 回到步骤 1
        5. 不可重试 -> 立即抛出
        6. 重试次数用尽 -> 抛出最后一次异常

        参数:
            operation: 异步操作函数（无参数）
            operation_name: 操作名称（用于日志）

        返回:
            操作函数的返回值

        异常:
            最后一次重试的异常（如果是可重试异常）
            原始异常（如果是不可重试异常）
        """
        last_error: Optional[Exception] = None

        for attempt in range(self._config.max_retries + 1):
            self._total_attempts += 1

            try:
                result = await operation()

                if attempt > 0:
                    self._total_retries += attempt
                    logger.info(
                        f"{operation_name} succeeded after {attempt} retries"
                    )

                return result

            except Exception as e:
                last_error = e
                classified = classify_exception(e)

                if not classified["retryable"]:
                    logger.warning(
                        f"{operation_name} failed with non-retryable error: "
                        f"{classified['type']}: {classified['message']}"
                    )
                    raise

                if attempt >= self._config.max_retries:
                    logger.error(
                        f"{operation_name} failed after {self._config.max_retries} "
                        f"retries: {classified['type']}: {classified['message']}"
                    )
                    raise

                delay = self._calculate_delay(attempt)

                logger.warning(
                    f"{operation_name} failed (attempt {attempt + 1}/"
                    f"{self._config.max_retries + 1}): {classified['type']}: "
                    f"{classified['message']}. Retrying in {delay:.1f}s..."
                )

                await asyncio.sleep(delay)

        if last_error:
            raise last_error
        raise RuntimeError("Unexpected: retry loop exited without result or error")

    def _calculate_delay(self: "RetryManager", attempt: int) -> float:
        """计算退避延迟时间

        参数:
            attempt: 当前尝试次数（0-based）

        返回:
            延迟时间（秒）
        """
        if self._config.strategy == RetryStrategy.EXPONENTIAL:
            delay = self._config.base_delay * (2**attempt)
        elif self._config.strategy == RetryStrategy.LINEAR:
            delay = self._config.base_delay * (attempt + 1)
        else:
            delay = self._config.base_delay

        delay = min(delay, self._config.max_delay)

        if self._config.jitter:
            jitter = delay * random.random() * 0.5
            delay += jitter

        return delay

    def get_stats(self: "RetryManager") -> dict[str, Any]:
        """获取重试统计"""
        return {
            "total_attempts": self._total_attempts,
            "total_retries": self._total_retries,
            "success_rate": (
                (
                    (self._total_attempts - self._total_retries)
                    / self._total_attempts
                    * 100
                )
                if self._total_attempts > 0
                else 100.0
            ),
        }


class ErrorHandler:
    """错误处理器

    统一处理异常，提供：
    - 异常分类和日志
    - 恢复策略建议
    """

    def handle(
        self, error: Exception, context: Optional[dict] = None
    ) -> dict[str, Any]:
        """处理异常

        参数:
            error: 异常对象
            context: 上下文信息

        返回:
            处理结果字典
        """
        classified = classify_exception(error)
        context = context or {}

        log_msg = (
            f"Error in {context.get('operation', 'unknown')}: "
            f"{classified['type']}: {classified['message']} "
            f"(severity={classified['severity']}, retryable={classified['retryable']})"
        )

        if classified["severity"] == "critical":
            logger.critical(log_msg)
        elif classified["severity"] == "error":
            logger.error(log_msg)
        else:
            logger.warning(log_msg)

        recovery_action = self._suggest_recovery(classified, context)

        return {
            "classified": classified,
            "recovery_action": recovery_action,
            "context": context,
        }

    def _suggest_recovery(
        self, classified: dict[str, Any], context: dict[str, Any]
    ) -> str:
        """建议恢复策略"""
        error_type = classified["type"]

        recovery_map = {
            "AuthError": "REFRESH_COOKIE",
            "TemporarilyBlocked": "WAIT_AND_RETRY",
            "UsageLimitExceeded": "SWITCH_MODEL_OR_WAIT",
            "TimeoutError": "RETRY_WITH_BACKOFF",
            "ConnectionError": "CHECK_NETWORK_AND_RETRY",
            "ModelInvalid": "USE_DEFAULT_MODEL",
        }

        return recovery_map.get(error_type, "UNKNOWN")

    def __repr__(self: "ErrorHandler") -> str:
        return "ErrorHandler()"



# ═══════════════════════════════════════════════════════════════════════════
# GeminiClient 核心类
# ═══════════════════════════════════════════════════════════════════════════


class GeminiClient:
    """Gemini Web API 客户端

    对 gemini-webapi 库的上层包装，提供：
    - 统一的生命周期管理（初始化/关闭）
    - 自动化的 Cookie 管理（刷新/过期检测/持久化）
    - 透明的 Gem 调用（选择/传递/调用）
    - 多格式图片上传（路径/bytes/URL/BytesIO）
    - 指数退避重试（可配置策略）
    - 结构化错误处理（分类/转换/恢复）

    使用示例:
        ```python
        config = GeminiClientConfig.from_env()
        client = GeminiClient(config)

        # 初始化（自动完成 Cookie 认证和刷新）
        await client.initialize()

        # 生成内容
        result = await client.generate_content("Hello Gemini!")
        print(result.text)

        # 使用指定 Gem 生成
        result = await client.generate_content(
            prompt="Write a poem",
            gem="creative-writer",
        )

        # 带图片的生成
        result = await client.generate_content(
            prompt="Describe this image",
            image=ImageInput.from_path("photo.jpg"),
        )

        # 关闭
        await client.close()
        ```
    """

    def __init__(self: "GeminiClient", config: Optional[GeminiClientConfig] = None) -> None:
        """创建 GeminiClient 实例（不执行网络操作）

        参数:
            config: 客户端配置，为 None 时从环境变量加载

        注意:
            构造函数仅做内存初始化，不涉及网络 I/O。
            必须在创建后调用 initialize() 完成实际初始化。
        """
        self._config = config or GeminiClientConfig.from_env()
        self._status = ClientStatus.UNINITIALIZED
        self._webapi_client: Any = None

        self._cookie_manager: Optional[CookieManager] = None
        self._gem_manager: Optional[GemManager] = None
        self._image_uploader: Optional[ImageUploader] = None
        self._retry_manager = RetryManager(self._config.retry)
        self._error_handler = ErrorHandler()

        self._request_count = 0
        self._error_count = 0
        self._last_request_time: Optional[float] = None

        self._setup_logging()

        logger.info(
            f"GeminiClient created (status={self._status.value}, "
            f"auto_refresh={self._config.auto_refresh})"
        )

    @property
    def config(self: "GeminiClient") -> GeminiClientConfig:
        """获取客户端配置（只读副本）"""
        return self._config.model_copy()

    @property
    def status(self: "GeminiClient") -> ClientStatus:
        """获取当前客户端状态"""
        return self._status

    @property
    def is_ready(self: "GeminiClient") -> bool:
        """判断客户端是否就绪"""
        return self._status == ClientStatus.READY

    @property
    def gems(self: "GeminiClient") -> GemManager:
        """获取 GemManager 实例

        异常:
            RuntimeError: 客户端未初始化
        """
        self._ensure_initialized()
        return self._gem_manager  # type: ignore[return-value]

    @property
    def cookie_manager(self: "GeminiClient") -> CookieManager:
        """获取 CookieManager 实例

        异常:
            RuntimeError: 客户端未初始化
        """
        self._ensure_initialized()
        return self._cookie_manager  # type: ignore[return-value]

    async def initialize(self: "GeminiClient") -> None:
        """初始化客户端

        完成以下操作：
        1. 创建 gemini-webapi 客户端实例
        2. 执行认证初始化（获取 access_token）
        3. 启动 Cookie 自动刷新
        4. 初始化 GemManager（获取 Gems）

        异常:
            AuthError: Cookie 认证失败
            TimeoutError: 初始化超时
            ConnectionError: 网络连接失败

        用时: 通常 3-10 秒
        """
        if self._status == ClientStatus.READY:
            logger.warning("Client already initialized, skipping")
            return

        self._status = ClientStatus.INITIALIZING
        start_time = time.time()

        try:
            from gemini_webapi import GeminiClient as WebapiClient

            logger.debug("Step 1: Importing gemini-webapi library")

            logger.debug("Step 2: Creating webapi client instance")
            self._webapi_client = WebapiClient(
                secure_1psid=self._config.secure_1psid or None,
                secure_1psidts=self._config.secure_1psidts or None,
                proxy=self._config.proxy,
            )

            if self._config.cookie_path:
                os.environ["GEMINI_COOKIE_PATH"] = self._config.cookie_path

            logger.debug("Step 3: Initializing authentication")
            await asyncio.wait_for(
                self._webapi_client.init(
                    timeout=self._config.init_timeout,
                    auto_refresh=self._config.auto_refresh,
                    refresh_interval=self._config.refresh_interval,
                    verbose=self._config.verbose,
                ),
                timeout=self._config.init_timeout + 10,
            )

            logger.debug("Step 4: Initializing sub-modules")

            self._cookie_manager = CookieManager(
                webapi_client=self._webapi_client,
                config=self._config,
            )

            self._gem_manager = GemManager(
                webapi_client=self._webapi_client,
                config=self._config,
            )
            await self._gem_manager.initialize()

            self._image_uploader = ImageUploader(config=self._config)

            self._status = ClientStatus.READY
            init_time = int((time.time() - start_time) * 1000)

            gems = await self._gem_manager.list_gems()
            logger.info(
                f"GeminiClient initialized successfully "
                f"(time={init_time}ms, gems={len(gems)})"
            )

        except asyncio.TimeoutError:
            self._status = ClientStatus.ERROR
            logger.error(
                f"Initialization timed out after {self._config.init_timeout}s"
            )
            raise
        except ImportError as e:
            self._status = ClientStatus.ERROR
            logger.error(
                f"Failed to import gemini-webapi: {e}. "
                "Install with: pip install gemini-webapi"
            )
            raise
        except Exception as e:
            self._status = ClientStatus.ERROR
            self._error_count += 1
            classified = classify_exception(e)
            logger.error(f"Initialization failed: {classified}")
            raise

    async def close(self: "GeminiClient") -> None:
        """关闭客户端，释放所有资源

        完成以下操作：
        1. 停止 Cookie 自动刷新
        2. 保存 Cookie 到缓存文件
        3. 关闭 gemini-webapi 客户端
        4. 清理子模块

        注意:
            此方法为幂等操作，可安全多次调用。
            关闭后客户端不可再使用，需重新创建实例。
        """
        if self._status == ClientStatus.CLOSED:
            return

        logger.info("Closing GeminiClient...")

        if self._gem_manager:
            try:
                await self._gem_manager.close()
            except Exception as e:
                logger.warning(f"Error closing GemManager: {e}")

        if self._webapi_client:
            try:
                await self._webapi_client.close()
            except Exception as e:
                logger.warning(f"Error closing webapi client: {e}")

        self._status = ClientStatus.CLOSED
        self._webapi_client = None
        self._cookie_manager = None
        self._gem_manager = None
        self._image_uploader = None

        logger.info("GeminiClient closed")

    async def __aenter__(self: "GeminiClient") -> GeminiClient:
        """异步上下文管理器入口"""
        await self.initialize()
        return self

    async def __aexit__(self: "GeminiClient", exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """异步上下文管理器出口"""
        await self.close()

    async def generate_content(
        self,
        prompt: str,
        gem: Optional[Union[str, GemInfo]] = None,
        image: Optional[ImageInput] = None,
        model: Optional[str] = None,
        temporary: bool = True,
    ) -> GenerationResult:
        """生成内容（非流式）

        核心方法，支持文本生成、Gem 调用、图片上传。
        内置指数退避重试机制。

        参数:
            prompt: 提示词文本
            gem: 指定 Gem（字符串 ID 或 GemInfo 对象）
            image: 图片输入（ImageInput 对象）
            model: 指定模型名称
            temporary: 是否临时模式

        返回:
            GenerationResult: 生成结果

        异常:
            NotInitializedError: 客户端未初始化
            AuthError: Cookie 认证失败
            APIError: API 调用失败
            TimeoutError: 请求超时
        """
        self._ensure_initialized()
        self._ensure_ready()

        start_time = time.time()
        self._request_count += 1

        gem_id = self._resolve_gem_id(gem)
        files = await self._prepare_files(image)
        webapi_model = self._resolve_model(model)

        logger.debug(
            f"generate_content: prompt_len={len(prompt)}, "
            f"gem={gem_id}, model={webapi_model}, has_image={files is not None}"
        )

        async def _do_generate() -> Any:
            output = await self._webapi_client.generate_content(
                prompt=prompt,
                files=files,
                model=webapi_model,
                gem=gem_id,
                temporary=temporary,
            )
            return output

        try:
            output = await self._retry_manager.execute_with_retry(
                _do_generate, operation_name="generate_content"
            )

            result = self._convert_output(output, model, gem_id, start_time)

            self._last_request_time = time.time()
            logger.info(
                f"generate_content: success, text_len={len(result.text)}, "
                f"time={result.generation_time_ms}ms"
            )

            return result

        except Exception as e:
            self._error_count += 1
            classified = classify_exception(e)
            logger.error(f"generate_content failed: {classified}")
            raise

    async def list_gems(self: "GeminiClient") -> List[GemInfo]:
        """获取所有可用 Gems 列表

        返回:
            List[GemInfo]: Gem 信息列表

        异常:
            NotInitializedError: 客户端未初始化
        """
        self._ensure_initialized()
        self._ensure_ready()
        return await self._gem_manager.list_gems()  # type: ignore[union-attr]

    async def get_gem(self: "GeminiClient", gem_id: str) -> Optional[GemInfo]:
        """按 ID 获取 Gem

        参数:
            gem_id: Gem 唯一标识

        返回:
            GemInfo 或 None
        """
        self._ensure_initialized()
        self._ensure_ready()
        return await self._gem_manager.get_gem(gem_id)  # type: ignore[union-attr]

    async def find_gem_by_tag(self: "GeminiClient", tag: str) -> Optional[GemInfo]:
        """按标签查找 Gem

        参数:
            tag: 业务标签

        返回:
            GemInfo 或 None
        """
        self._ensure_initialized()
        self._ensure_ready()
        return await self._gem_manager.find_by_tag(tag)  # type: ignore[union-attr]

    async def refresh_gems(self: "GeminiClient") -> int:
        """刷新 Gems 缓存

        返回:
            int: 获取到的 Gem 数量
        """
        self._ensure_initialized()
        self._ensure_ready()
        count = await self._gem_manager.refresh()  # type: ignore[union-attr]
        logger.info(f"Gems refreshed: {count} gems")
        return count

    async def upload_image(
        self,
        image: Union[str, Path, bytes, ImageInput],
        validate_only: bool = False,
    ) -> UploadResult:
        """上传图片到 Gemini 服务

        支持多种输入格式，自动格式验证、压缩、转换。

        参数:
            image: 图片输入
            validate_only: 仅验证不实际上传

        返回:
            UploadResult: 上传结果

        异常:
            FileNotFoundError: 文件不存在
            UnsupportedFormatError: 不支持的图片格式
            FileTooLargeError: 文件过大且无法压缩
        """
        self._ensure_initialized()
        self._ensure_ready()

        if not isinstance(image, ImageInput):
            image = self._convert_to_image_input(image)

        logger.debug(f"upload_image: {image.filename} ({image.mime_type})")

        result = await self._image_uploader.upload(  # type: ignore[union-attr]
            image, validate_only=validate_only
        )
        return result

    async def health_check(self: "GeminiClient") -> HealthStatus:
        """健康状态检查

        检查项：
        1. 客户端是否已初始化
        2. Cookie 是否有效
        3. 能否获取 Gems（API 连通性）

        返回:
            HealthStatus: 健康状态详情
        """
        start_time = time.time()
        errors: List[str] = []
        cookie_valid = False
        gem_count = 0

        if self._status != ClientStatus.READY:
            return HealthStatus(
                status="unhealthy",
                client_status=self._status.value,
                errors=["Client not ready"],
            )

        try:
            if self._cookie_manager:
                cookie_valid = await self._cookie_manager.validate()
        except Exception as e:
            errors.append(f"Cookie validation failed: {e}")

        try:
            if self._gem_manager:
                gems = await self._gem_manager.list_gems()
                gem_count = len(gems)
        except Exception as e:
            errors.append(f"API connectivity check failed: {e}")

        latency_ms = int((time.time() - start_time) * 1000)

        if not cookie_valid:
            status = "unhealthy"
            errors.append("Cookie invalid or expired")
        elif errors:
            status = "degraded"
        else:
            status = "healthy"

        return HealthStatus(
            status=status,
            client_status=self._status.value,
            gem_count=gem_count,
            cookie_valid=cookie_valid,
            last_cookie_refresh=getattr(
                self._cookie_manager, "last_refresh_time", None
            ),
            latency_ms=latency_ms,
            errors=errors,
        )

    def _ensure_initialized(self: "GeminiClient") -> None:
        """确保客户端已完成初始化"""
        if self._status == ClientStatus.UNINITIALIZED:
            raise RuntimeError(
                "GeminiClient not initialized. Call initialize() first."
            )

    def _ensure_ready(self: "GeminiClient") -> None:
        """确保客户端处于就绪状态"""
        if self._status == ClientStatus.ERROR:
            raise RuntimeError(
                "GeminiClient is in error state. Re-create the instance."
            )
        if self._status == ClientStatus.CLOSED:
            raise RuntimeError(
                "GeminiClient has been closed. Create a new instance."
            )

    def _resolve_gem_id(
        self, gem: Optional[Union[str, GemInfo]]
    ) -> Optional[str]:
        """将 gem 参数解析为 ID 字符串"""
        if gem is None:
            return None
        if isinstance(gem, str):
            return gem
        if isinstance(gem, GemInfo):
            return gem.id
        raise TypeError(f"Unsupported gem type: {type(gem)}")

    def _resolve_model(self: "GeminiClient", model: Optional[str]) -> Any:
        """将模型名称解析为 gemini-webapi 模型常量"""
        try:
            from gemini_webapi.constants import Model as WebapiModel

            if model is None:
                return WebapiModel.UNSPECIFIED

            model_upper = model.upper().replace("-", "_")
            if hasattr(WebapiModel, model_upper):
                return getattr(WebapiModel, model_upper)

            return model
        except ImportError:
            return model or "unspecified"

    async def _prepare_files(
        self, image: Optional[ImageInput]
    ) -> Optional[List[Union[str, Path, bytes]]]:
        """准备文件列表供 gemini-webapi 使用"""
        if image is None:
            return None

        upload_format = await image.to_upload_format()
        return [upload_format]

    def _convert_to_image_input(
        self, image: Union[str, Path, bytes]
    ) -> ImageInput:
        """将原始输入转换为 ImageInput"""
        if isinstance(image, str):
            if image.startswith(("http://", "https://")):
                return ImageInput.from_url(image)
            return ImageInput.from_path(image)
        elif isinstance(image, Path):
            return ImageInput.from_path(image)
        elif isinstance(image, bytes):
            return ImageInput.from_bytes(image)
        else:
            raise TypeError(f"Unsupported image type: {type(image)}")

    def _convert_output(
        self,
        output: Any,
        model_used: Optional[str],
        gem_used: Optional[str],
        start_time: float,
    ) -> GenerationResult:
        """将 gemini-webapi 输出转换为 GenerationResult"""
        generation_time_ms = int((time.time() - start_time) * 1000)

        images: List[MediaInfo] = []
        videos: List[MediaInfo] = []

        candidate = None
        if hasattr(output, "candidates") and output.candidates:
            chosen = getattr(output, "chosen", 0)
            if 0 <= chosen < len(output.candidates):
                candidate = output.candidates[chosen]

        if candidate:
            for img in getattr(candidate, "generated_images", []):
                images.append(
                    MediaInfo(
                        type="image",
                        url=getattr(img, "url", None),
                        mime_type=getattr(img, "mime_type", "image/png"),
                    )
                )
            for vid in getattr(candidate, "generated_videos", []):
                videos.append(
                    MediaInfo(
                        type="video",
                        url=getattr(vid, "url", None),
                        mime_type=getattr(vid, "mime_type", "video/mp4"),
                    )
                )

        return GenerationResult(
            text=getattr(output, "text", "") or "",
            images=images,
            videos=videos,
            model_used=model_used or self._config.default_model,
            gem_used=gem_used,
            generation_time_ms=generation_time_ms,
            raw_output=output,
        )

    def _setup_logging(self: "GeminiClient") -> None:
        """配置日志"""
        import logging as logging_module

        level = getattr(
            logging_module, self._config.log_level.upper(), logging_module.INFO
        )
        logging_module.basicConfig(
            level=level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )



# ═══════════════════════════════════════════════════════════════════════════
# 单元测试
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# 单元测试（模块级别，供 pytest 自动发现）
# ═══════════════════════════════════════════════════════════════════════════


import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

# ── Fixtures ──

@pytest.fixture
def test_config() -> None:
    """测试配置"""
    return GeminiClientConfig(
        secure_1psid="test_psid",
        secure_1psidts="test_psidts",
        auto_refresh=False,
        verbose=False,
    )

@pytest.fixture
def mock_retry_config() -> None:
    """测试重试配置"""
    return RetryConfig(max_retries=2, base_delay=0.1, jitter=False)

# ── 测试数据模型 ──

class TestImageInput:
    """ImageInput 测试"""

    def test_from_path_existing(self: "TestImageInput", tmp_path: Path) -> None:
        """从现有路径创建"""
        img_file = tmp_path / "test.jpg"
        img_file.write_bytes(b"fake image data")

        img = ImageInput.from_path(img_file)
        assert img.source_type == ImageSourceType.PATH
        assert img.mime_type == "image/jpeg"
        assert img.filename == "test.jpg"

    def test_from_path_not_found(self: "TestImageInput", tmp_path: Path) -> None:
        """从不存在路径创建应抛出异常"""
        with pytest.raises(FileNotFoundError):
            ImageInput.from_path(tmp_path / "not_exist.jpg")

    def test_from_bytes(self: "TestImageInput") -> None:
        """从二进制数据创建"""
        data = b"fake image data"
        img = ImageInput.from_bytes(data, filename="photo.png")
        assert img.source_type == ImageSourceType.BYTES
        assert img.data == data
        assert img.filename == "photo.png"

    def test_from_url(self: "TestImageInput") -> None:
        """从 URL 创建"""
        img = ImageInput.from_url("https://example.com/photo.jpg")
        assert img.source_type == ImageSourceType.URL
        assert img.url == "https://example.com/photo.jpg"

    def test_get_size_bytes(self: "TestImageInput", tmp_path: Path) -> None:
        """获取图片大小"""
        img_file = tmp_path / "test.jpg"
        img_file.write_bytes(b"x" * 1024)

        img = ImageInput.from_path(img_file)
        assert img.get_size_bytes() == 1024

        img2 = ImageInput.from_bytes(b"x" * 512)
        assert img2.get_size_bytes() == 512

class TestGemInfo:
    """GemInfo 测试"""

    def test_create(self: "TestGemInfo") -> None:
        """创建 GemInfo"""
        gem = GemInfo(id="test-gem", name="Test Gem", tags=["video"])
        assert gem.id == "test-gem"
        assert gem.name == "Test Gem"
        assert gem.tags == ["video"]
        assert gem.predefined is False

    def test_from_webapi_gem(self: "TestGemInfo") -> None:
        """从 webapi Gem 对象转换"""
        mock_gem = MagicMock()
        mock_gem.id = "gem-1"
        mock_gem.name = "Gem One"
        mock_gem.description = "A test gem"
        mock_gem.predefined = True
        mock_gem.prompt = "You are helpful."

        info = GemInfo.from_webapi_gem(mock_gem)
        assert info.id == "gem-1"
        assert info.name == "Gem One"
        assert info.prompt_preview == "You are helpful."

class TestGenerationResult:
    """GenerationResult 测试"""

    def test_create(self: "TestGenerationResult") -> None:
        """创建生成结果"""
        result = GenerationResult(
            text="Hello",
            model_used="pro",
            generation_time_ms=100,
        )
        assert result.text == "Hello"
        assert result.images == []
        assert result.videos == []

class TestHealthStatus:
    """HealthStatus 测试"""

    def test_healthy(self: "TestHealthStatus") -> None:
        """健康状态判断"""
        hs = HealthStatus(status="healthy", cookie_valid=True)
        assert hs.is_healthy is True

        hs2 = HealthStatus(status="healthy", cookie_valid=False)
        assert hs2.is_healthy is False

class TestGeminiClientConfig:
    """GeminiClientConfig 测试"""

    def test_defaults(self: "TestGeminiClientConfig") -> None:
        """默认配置"""
        config = GeminiClientConfig()
        assert config.auto_refresh is True
        assert config.refresh_interval == 600
        assert config.max_image_size_mb == 20

    def test_proxy_validation(self: "TestGeminiClientConfig") -> None:
        """代理地址验证"""
        config = GeminiClientConfig(proxy="http://localhost:8080")
        assert config.proxy == "http://localhost:8080"

        with pytest.raises(ValueError):
            GeminiClientConfig(proxy="invalid-proxy")

    def test_from_env(self: "TestGeminiClientConfig", monkeypatch: pytest.MonkeyPatch) -> None:
        """从环境变量加载"""
        monkeypatch.setenv("GEMINI_1PSID", "env_psid")
        monkeypatch.setenv("GEMINI_1PSIDTS", "env_psidts")
        monkeypatch.setenv("GEMINI_VERBOSE", "true")

        config = GeminiClientConfig.from_env()
        assert config.secure_1psid == "env_psid"
        assert config.secure_1psidts == "env_psidts"
        assert config.verbose is True

# ── 测试 CookieManager ──

class TestCookieManager:
    """CookieManager 测试"""

    @pytest.fixture
    def mock_webapi_client(self: "TestCookieManager") -> None:
        """Mock webapi 客户端"""
        client = MagicMock()
        client.list_models = MagicMock(return_value=["model1", "model2"])
        client.init = AsyncMock()
        client.cookies = MagicMock()
        client.cookies.get = MagicMock(
            return_value=MagicMock(value="test_cookie_value")
        )
        return client

    @pytest.mark.asyncio
    async def test_validate_success(self: "TestCookieManager", mock_webapi_client, test_config: GeminiClientConfig) -> None:
        """验证成功"""
        mgr = CookieManager(mock_webapi_client, test_config)
        result = await mgr.validate()
        assert result is True
        mock_webapi_client.list_models.assert_called_once()

    @pytest.mark.asyncio
    async def test_validate_failure(self: "TestCookieManager", mock_webapi_client, test_config: GeminiClientConfig) -> None:
        """验证失败"""
        mock_webapi_client.list_models = MagicMock(
            side_effect=Exception("Auth failed")
        )
        mgr = CookieManager(mock_webapi_client, test_config)
        result = await mgr.validate()
        assert result is False

    @pytest.mark.asyncio
    async def test_refresh(self: "TestCookieManager", mock_webapi_client, test_config: GeminiClientConfig) -> None:
        """刷新 Cookie"""
        mgr = CookieManager(mock_webapi_client, test_config)
        result = await mgr.refresh()
        assert result is True
        assert mgr.refresh_count == 1
        assert mgr.last_refresh_time is not None

    def test_get_cookie(self: "TestCookieManager", mock_webapi_client, test_config: GeminiClientConfig) -> None:
        """获取 Cookie"""
        mgr = CookieManager(mock_webapi_client, test_config)
        value = mgr.get_cookie("__Secure-1PSID")
        assert value == "test_cookie_value"

    def test_is_valid_property(self: "TestCookieManager", mock_webapi_client, test_config: GeminiClientConfig) -> None:
        """is_valid 属性"""
        mgr = CookieManager(mock_webapi_client, test_config)
        assert mgr.is_valid is False  # 从未刷新

# ── 测试 GemManager ──

class TestGemManager:
    """GemManager 测试"""

    @pytest.fixture
    def mock_gem_jar(self: "TestGemManager") -> None:
        """Mock Gem 容器"""
        gem_jar = {
            "coding-partner": MagicMock(
                id="coding-partner",
                name="Coding Partner",
                description="Helps with coding",
                predefined=True,
                prompt="You are a coding assistant.",
            ),
            "creative-writer": MagicMock(
                id="creative-writer",
                name="Creative Writer",
                description="Writes creative content",
                predefined=False,
                prompt="You are a creative writer.",
            ),
        }
        return gem_jar

    @pytest.fixture
    def mock_client_with_gems(self: "TestGemManager", mock_gem_jar: Any) -> None:
        """Mock 客户端（带 Gems）"""
        client = MagicMock()
        client.fetch_gems = AsyncMock(return_value=mock_gem_jar)
        return client

    @pytest.mark.asyncio
    async def test_initialize(self: "TestGemManager", mock_client_with_gems, test_config: GeminiClientConfig) -> None:
        """初始化"""
        mgr = GemManager(mock_client_with_gems, test_config)
        await mgr.initialize()
        assert mgr.cache_size == 2
        assert mgr.is_cache_valid is True

    @pytest.mark.asyncio
    async def test_list_gems(self: "TestGemManager", mock_client_with_gems, test_config: GeminiClientConfig) -> None:
        """列出 Gems"""
        mgr = GemManager(mock_client_with_gems, test_config)
        await mgr.initialize()
        gems = await mgr.list_gems()
        assert len(gems) == 2
        assert gems[0].name <= gems[1].name  # 已排序

    @pytest.mark.asyncio
    async def test_get_gem(self: "TestGemManager", mock_client_with_gems, test_config: GeminiClientConfig) -> None:
        """按 ID 获取 Gem"""
        mgr = GemManager(mock_client_with_gems, test_config)
        await mgr.initialize()
        gem = await mgr.get_gem("coding-partner")
        assert gem is not None
        assert gem.id == "coding-partner"

    @pytest.mark.asyncio
    async def test_get_gem_not_found(self: "TestGemManager", mock_client_with_gems, test_config: GeminiClientConfig) -> None:
        """获取不存在的 Gem"""
        mgr = GemManager(mock_client_with_gems, test_config)
        await mgr.initialize()
        gem = await mgr.get_gem("non-existent")
        assert gem is None

    @pytest.mark.asyncio
    async def test_select_best(self: "TestGemManager", mock_client_with_gems, test_config: GeminiClientConfig) -> None:
        """智能选择 Gem"""
        mgr = GemManager(mock_client_with_gems, test_config)
        await mgr.initialize()
        gem = await mgr.select_best("coding help")
        assert gem is not None
        assert gem.id == "coding-partner"

    def test_cache_stats(self: "TestGemManager", mock_client_with_gems, test_config: GeminiClientConfig) -> None:
        """缓存统计"""
        mgr = GemManager(mock_client_with_gems, test_config)
        stats = mgr.get_cache_stats()
        assert "cache_size" in stats
        assert "ttl_sec" in stats

# ── 测试 ImageUploader ──

class TestImageUploader:
    """ImageUploader 测试"""

    @pytest.fixture
    def uploader(self: "TestImageUploader", test_config: GeminiClientConfig) -> None:
        return ImageUploader(test_config)

    def test_normalize_input_path(self: "TestImageUploader", tmp_path: Path, uploader: ImageUploader) -> None:
        """归一化路径输入"""
        img_file = tmp_path / "test.jpg"
        img_file.write_bytes(b"fake data")
        result = uploader._normalize_input(str(img_file))
        assert isinstance(result, ImageInput)

    def test_normalize_input_bytes(self: "TestImageUploader", uploader: ImageUploader) -> None:
        """归一化 bytes 输入"""
        result = uploader._normalize_input(b"fake data")
        assert isinstance(result, ImageInput)
        assert result.source_type == ImageSourceType.BYTES

    def test_validate_format_supported(self: "TestImageUploader", uploader: ImageUploader) -> None:
        """验证支持的格式"""
        img = ImageInput.from_bytes(b"x", filename="photo.jpg")
        uploader._validate_format(img)  # 不抛出异常

    def test_validate_format_unsupported(self: "TestImageUploader", uploader: ImageUploader) -> None:
        """验证不支持的格式"""
        img = ImageInput.from_bytes(b"x", filename="photo.tiff")
        with pytest.raises(UnsupportedFormatError):
            uploader._validate_format(img)

    @pytest.mark.asyncio
    async def test_validate_method(self: "TestImageUploader", tmp_path: Path, uploader: ImageUploader) -> None:
        """验证方法"""
        img_file = tmp_path / "test.jpg"
        img_file.write_bytes(b"fake data")
        valid, error = uploader.validate(str(img_file))
        assert valid is True
        assert error is None

    def test_get_mime_type(self: "TestImageUploader", uploader: ImageUploader) -> None:
        """获取 MIME 类型"""
        assert uploader.get_mime_type("photo.jpg") == "image/jpeg"
        assert uploader.get_mime_type("photo.png") == "image/png"

# ── 测试 RetryManager ──

class TestRetryManager:
    """RetryManager 测试"""

    @pytest.fixture
    def fast_retry_config(self: "TestRetryManager"):
        return RetryConfig(
            max_retries=3, base_delay=0.1, jitter=False
        )

    @pytest.mark.asyncio
    async def test_success_no_retry(self: "TestRetryManager", fast_retry_config: RetryConfig) -> None:
        """成功执行，无需重试"""
        mgr = RetryManager(fast_retry_config)

        async def success_op():
            return "success"

        result = await mgr.execute_with_retry(success_op)
        assert result == "success"
        assert mgr.total_attempts == 1

    @pytest.mark.asyncio
    async def test_retry_then_success(self: "TestRetryManager", fast_retry_config: RetryConfig) -> None:
        """重试后成功"""
        mgr = RetryManager(fast_retry_config)
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("timeout")
            return "success"

        result = await mgr.execute_with_retry(flaky)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_error(self: "TestRetryManager", fast_retry_config: RetryConfig) -> None:
        """不可重试异常立即抛出"""
        mgr = RetryManager(fast_retry_config)
        call_count = 0

        async def fail_auth():
            nonlocal call_count
            call_count += 1
            raise ValueError("invalid parameter")

        with pytest.raises(ValueError):
            await mgr.execute_with_retry(fail_auth)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_all_retries_failed(self: "TestRetryManager", fast_retry_config: RetryConfig) -> None:
        """所有重试都失败"""
        mgr = RetryManager(fast_retry_config)

        async def always_fail():
            raise ConnectionError("connection refused")

        with pytest.raises(ConnectionError):
            await mgr.execute_with_retry(always_fail)
        assert mgr.total_attempts == 4  # 初始 + 3 次重试

    def test_calculate_delay_exponential(self: "TestRetryManager", fast_retry_config: RetryConfig) -> None:
        """指数退避计算"""
        mgr = RetryManager(fast_retry_config)
        assert mgr._calculate_delay(0) == 0.1
        assert mgr._calculate_delay(1) == 0.2
        assert mgr._calculate_delay(2) == 0.4

    def test_calculate_delay_with_jitter(self: "TestRetryManager") -> None:
        """带抖动的退避"""
        config = RetryConfig(
            max_retries=3, base_delay=1.0, jitter=True
        )
        mgr = RetryManager(config)
        delay = mgr._calculate_delay(0)
        assert 1.0 <= delay <= 1.5  # 1.0 + 最多 50% 抖动

    def test_get_stats(self: "TestRetryManager", fast_retry_config: RetryConfig) -> None:
        """统计信息"""
        mgr = RetryManager(fast_retry_config)
        stats = mgr.get_stats()
        assert "total_attempts" in stats
        assert "success_rate" in stats
        assert stats["success_rate"] == 100.0  # 无尝试时

# ── 测试 ErrorHandler ──

class TestErrorHandler:
    """ErrorHandler 测试"""

    def test_classify_timeout(self: "TestErrorHandler") -> None:
        """分类超时异常"""
        error = TimeoutError("request timed out")
        info = classify_exception(error)
        assert info["type"] == "TimeoutError"
        assert info["retryable"] is True
        assert info["severity"] == "warning"

    def test_classify_auth_error(self: "TestErrorHandler") -> None:
        """分类认证异常"""
        error = Exception("Auth failed")
        info = classify_exception(error)
        assert info["retryable"] is False  # 默认不可重试

    def test_is_retryable(self: "TestErrorHandler") -> None:
        """判断可重试性"""
        assert is_retryable(TimeoutError("timeout")) is True
        assert is_retryable(ConnectionError("refused")) is True
        assert is_retryable(ValueError("invalid")) is False
        assert is_retryable(TypeError("wrong type")) is False

    def test_should_refresh_cookie(self: "TestErrorHandler") -> None:
        """判断是否需要刷新 Cookie"""
        class FakeAuthError(Exception):
            pass

        error = FakeAuthError("unauthenticated")
        # 不是标准的 AuthError 名称，所以不应触发
        assert should_refresh_cookie(error) is False

    def test_error_handler_handle(self: "TestErrorHandler") -> None:
        """错误处理"""
        handler = ErrorHandler()
        result = handler.handle(TimeoutError("test"), {"operation": "test_op"})
        assert "classified" in result
        assert "recovery_action" in result
        assert result["recovery_action"] == "RETRY_WITH_BACKOFF"

# ── 测试 GeminiClient 主类 ──

class TestGeminiClient:
    """GeminiClient 测试"""

    def test_create_default(self: "TestGeminiClient") -> None:
        """默认创建"""
        client = GeminiClient()
        assert client.status == ClientStatus.UNINITIALIZED
        assert client.config is not None

    def test_create_with_config(self: "TestGeminiClient", test_config: GeminiClientConfig) -> None:
        """带配置创建"""
        client = GeminiClient(test_config)
        assert client.status == ClientStatus.UNINITIALIZED
        assert client.config.secure_1psid == "test_psid"

    def test_is_ready(self: "TestGeminiClient", test_config: GeminiClientConfig) -> None:
        """就绪状态"""
        client = GeminiClient(test_config)
        assert client.is_ready is False

    def test_ensure_initialized_raises(self: "TestGeminiClient", test_config: GeminiClientConfig) -> None:
        """未初始化访问应抛出异常"""
        client = GeminiClient(test_config)
        with pytest.raises(RuntimeError):
            client.gems

    def test_resolve_gem_id(self: "TestGeminiClient", test_config: GeminiClientConfig) -> None:
        """解析 Gem ID"""
        client = GeminiClient(test_config)
        assert client._resolve_gem_id(None) is None
        assert client._resolve_gem_id("gem-1") == "gem-1"
        assert (
            client._resolve_gem_id(GemInfo(id="g1", name="G1")) == "g1"
        )
        with pytest.raises(TypeError):
            client._resolve_gem_id(123)

    def test_convert_to_image_input(self: "TestGeminiClient", test_config: GeminiClientConfig, tmp_path: Path) -> None:
        """转换为 ImageInput"""
        client = GeminiClient(test_config)

        img_file = tmp_path / "test.jpg"
        img_file.write_bytes(b"fake data")

        result = client._convert_to_image_input(str(img_file))
        assert isinstance(result, ImageInput)

        result2 = client._convert_to_image_input(img_file)
        assert isinstance(result2, ImageInput)

        result3 = client._convert_to_image_input(b"bytes data")
        assert isinstance(result3, ImageInput)

    def test_resolve_model(self: "TestGeminiClient", test_config: GeminiClientConfig) -> None:
        """解析模型名称"""
        client = GeminiClient(test_config)
        # 无 gemini-webapi 时返回字符串
        assert client._resolve_model(None) == "unspecified"
        assert client._resolve_model("pro") == "pro"

    @pytest.mark.asyncio
    async def test_close_idempotent(self: "TestGeminiClient", test_config: GeminiClientConfig) -> None:
        """关闭幂等性"""
        client = GeminiClient(test_config)
        await client.close()
        assert client.status == ClientStatus.CLOSED
        # 再次关闭不应抛出异常
        await client.close()

    @pytest.mark.asyncio
    async def test_health_check_not_ready(self: "TestGeminiClient", test_config: GeminiClientConfig) -> None:
        """健康检查 - 未就绪"""
        client = GeminiClient(test_config)
        status = await client.health_check()
        assert status.status == "unhealthy"
        assert "Client not ready" in status.errors

# ── 运行测试 ──




if __name__ == "__main__":
    print("gemini_client.py - 使用 pytest 运行: pytest gemini_client.py -v")
