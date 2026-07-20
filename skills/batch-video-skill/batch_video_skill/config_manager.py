# -*- coding: utf-8 -*-
"""
配置管理器 — 统一配置管理模块

位置: batch_video_skill/infrastructure/config_manager.py
功能: 4层配置加载 + Pydantic校验 + SecureVault + 热更新

配置层次（从低到高优先级）:
    1. DEFAULT — 内置默认配置
    2. USER — 用户配置文件 (~/.batch-video-skill/config.json)
    3. ENV — 环境变量 (BVS_前缀)
    4. CLI — 命令行参数

设计原则:
1. 高优先级配置覆盖低优先级
2. 字典递归合并，列表直接替换
3. SecretStr 自动脱敏
4. 热更新白名单机制
"""

from __future__ import annotations

import copy
import fnmatch
import json
import logging
import os
import re
import signal
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Set,
    Tuple,
    Union,
)

try:
    from pydantic import (
        BaseModel,
        ConfigDict,
        Field,
        SecretStr,
        field_validator,
        model_validator,
    )
except ImportError:
    raise ImportError("pydantic >= 2.0 is required. Install: pip install pydantic")

logger = logging.getLogger(__name__)


# ============================================================================
# 枚举类型
# ============================================================================


class LogLevel(str, Enum):
    """日志级别枚举"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @classmethod
    def to_python_level(cls: type["LogLevel"], level: "LogLevel") -> int:
        """转换为 Python logging 模块的级别值"""
        import logging as pylogging

        mapping = {
            cls.DEBUG: pylogging.DEBUG,
            cls.INFO: pylogging.INFO,
            cls.WARNING: pylogging.WARNING,
            cls.ERROR: pylogging.ERROR,
            cls.CRITICAL: pylogging.CRITICAL,
        }
        return mapping.get(level, pylogging.INFO)


class CostTier(str, Enum):
    """视频提供商成本层级枚举"""

    PREMIUM = "premium"
    STANDARD = "standard"
    BUDGET = "budget"

    @classmethod
    def cost_rank(cls: type["CostTier"], tier: "CostTier") -> int:
        """成本等级排序（数值越大越贵）"""
        mapping = {cls.BUDGET: 1, cls.STANDARD: 2, cls.PREMIUM: 3}
        return mapping.get(tier, 2)


class AuthType(str, Enum):
    """认证类型枚举"""

    API_KEY = "api_key"
    COOKIE = "cookie"
    OAUTH2 = "oauth2"
    SERVICE_ACCOUNT = "service_account"


class StorageBackend(str, Enum):
    """安全存储后端类型枚举"""

    KEYRING = "keyring"
    ENCRYPTED_FILE = "encrypted_file"
    ENV_ONLY = "env_only"


class QualityPreference(str, Enum):
    """视频质量偏好枚举"""

    BEST = "best"
    BALANCED = "balanced"
    FAST = "fast"
    CHEAPEST = "cheapest"


class ProgressFormat(str, Enum):
    """进度显示格式枚举"""

    SIMPLE = "simple"
    DETAILED = "detailed"
    JSON = "json"


class LogFormat(str, Enum):
    """日志格式枚举"""

    SIMPLE = "simple"
    DETAILED = "detailed"
    JSON = "json"


# ============================================================================
# Pydantic 配置模型
# ============================================================================


class AuthCredentials(BaseModel):
    """认证凭证配置"""

    model_config = ConfigDict(extra="allow")

    api_keys: List[SecretStr] = Field(
        default_factory=list,
        description="API Key 列表（支持多 Key 轮询负载均衡）",
    )
    cookie_name: Optional[str] = Field(default=None, description="Cookie 名称")
    cookie_value: Optional[SecretStr] = Field(
        default=None, description="Cookie 值（敏感，自动脱敏）"
    )
    client_id: Optional[str] = Field(default=None, description="OAuth2 客户端 ID")
    client_secret: Optional[SecretStr] = Field(
        default=None, description="OAuth2 客户端密钥（敏感）"
    )
    refresh_token: Optional[SecretStr] = Field(
        default=None, description="OAuth2 刷新令牌（敏感）"
    )
    service_account_file: Optional[str] = Field(
        default=None, description="GCP 服务账号 JSON 文件路径"
    )

    @field_validator("api_keys", mode="before")
    @classmethod
    def _parse_api_keys(cls: type["AuthCredentials"], v: Any) -> List[SecretStr]:
        """支持多种格式的 API Key 输入"""
        if isinstance(v, str):
            return [SecretStr(v)]
        if isinstance(v, list):
            return [
                SecretStr(k) if isinstance(k, str) else k for k in v
            ]
        return v

    @field_validator("service_account_file")
    @classmethod
    def _expand_service_account_path(
        cls, v: Optional[str]
    ) -> Optional[str]:
        """展开用户目录路径"""
        if v:
            return os.path.expanduser(v)
        return v


class ProviderAuthConfig(BaseModel):
    """单个服务提供商的认证配置"""

    model_config = ConfigDict(extra="allow")

    enabled: bool = Field(default=True, description="是否启用该提供商认证")
    auth_type: AuthType = Field(default=AuthType.API_KEY, description="认证类型")
    endpoint: Optional[str] = Field(default=None, description="API 端点 URL")
    credentials: AuthCredentials = Field(
        default_factory=AuthCredentials, description="认证凭证"
    )
    scopes: List[str] = Field(
        default_factory=list, description="OAuth2 权限范围"
    )


class AuthConfig(BaseModel):
    """认证总配置"""

    model_config = ConfigDict(extra="allow")

    preferred_storage: StorageBackend = Field(
        default=StorageBackend.KEYRING, description="首选安全存储后端"
    )
    encrypted_file_password: Optional[SecretStr] = Field(
        default=None, description="加密文件密码（如使用加密文件存储）"
    )

    # 各服务认证配置
    gemini: ProviderAuthConfig = Field(
        default_factory=lambda: ProviderAuthConfig(
            enabled=True,
            auth_type=AuthType.COOKIE,
            endpoint="https://gemini.google.com",
        )
    )
    google_workspace: ProviderAuthConfig = Field(
        default_factory=lambda: ProviderAuthConfig(
            enabled=True,
            auth_type=AuthType.SERVICE_ACCOUNT,
            endpoint="https://www.googleapis.com",
            scopes=[
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/documents.readonly",
            ],
        )
    )
    jimeng: ProviderAuthConfig = Field(
        default_factory=lambda: ProviderAuthConfig(
            enabled=True,
            auth_type=AuthType.API_KEY,
            endpoint="https://ark.cn-beijing.volces.com",
        )
    )
    kling: ProviderAuthConfig = Field(
        default_factory=lambda: ProviderAuthConfig(
            enabled=True,
            auth_type=AuthType.API_KEY,
            endpoint="https://api-beijing.klingai.com",
        )
    )
    vidu: ProviderAuthConfig = Field(
        default_factory=lambda: ProviderAuthConfig(
            enabled=True,
            auth_type=AuthType.API_KEY,
            endpoint="https://api.vidu.com",
        )
    )
    veo3: ProviderAuthConfig = Field(
        default_factory=lambda: ProviderAuthConfig(
            enabled=True,
            auth_type=AuthType.API_KEY,
            endpoint="https://generativelanguage.googleapis.com/v1beta",
        )
    )


class VideoProviderConfig(BaseModel):
    """单个视频提供商配置"""

    model_config = ConfigDict(extra="allow")

    provider: str = Field(
        ..., description="提供商标识，如 kling_3_0, vidu_q3_turbo"
    )
    enabled: bool = Field(default=True, description="是否启用该提供商")
    model: Optional[str] = Field(default=None, description="使用的模型版本")
    api_keys: List[SecretStr] = Field(
        default_factory=list, description="API Key 列表（多个 Key 轮询）"
    )
    weight: int = Field(
        default=1, ge=0, le=10,
        description="路由权重(0=禁用路由, 10=最高优先级)"
    )
    priority: int = Field(
        default=5, ge=1, le=10, description="优先级(1=最高, 10=最低)"
    )
    rate_limit_rpm: int = Field(
        default=10, ge=1, description="每分钟请求数限制(RPM)"
    )
    rate_limit_rpd: int = Field(
        default=1000, ge=1, description="每日请求数限制(RPD)"
    )
    concurrency_limit: int = Field(
        default=3, ge=1, le=20, description="并发请求限制"
    )
    cost_tier: CostTier = Field(default=CostTier.STANDARD)
    max_cost_per_video_usd: float = Field(
        default=1.0, ge=0.0, description="单个视频最大成本(USD)"
    )
    extra: Dict[str, Any] = Field(
        default_factory=dict, description="提供商特有配置扩展字段"
    )

    @field_validator("api_keys", mode="before")
    @classmethod
    def _parse_api_keys(cls: type["VideoProviderConfig"], v: Any) -> List[SecretStr]:
        """支持从字符串列表或 SecretStr 列表解析"""
        if isinstance(v, str):
            return [SecretStr(v)]
        if isinstance(v, list):
            return [SecretStr(k) if isinstance(k, str) else k for k in v]
        return v

    @field_validator("provider")
    @classmethod
    def _validate_provider_name(cls: type["VideoProviderConfig"], v: str) -> str:
        """验证提供商标识格式"""
        if not v or not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                f"提供商标识 '{v}' 格式无效，应为小写字母+数字+下划线"
            )
        return v

    def __hash__(self: "VideoProviderConfig") -> int:
        """支持基于 provider 名称的哈希"""
        return hash(self.provider)

    def __eq__(self: "VideoProviderConfig", other: object) -> bool:
        if isinstance(other, VideoProviderConfig):
            return self.provider == other.provider
        return NotImplemented


class VideoGenerationConfig(BaseModel):
    """视频生成模块配置"""

    model_config = ConfigDict(extra="allow")

    default_resolution: Literal["540p", "720p", "1080p", "4k"] = Field(
        default="720p", description="默认视频分辨率"
    )
    default_aspect_ratio: Literal[
        "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"
    ] = Field(default="9:16", description="默认视频宽高比")
    default_duration: int = Field(
        default=5, ge=4, le=15, description="默认视频时长(秒)"
    )
    default_generate_audio: bool = Field(
        default=True, description="默认是否生成音频"
    )
    default_quality_preference: QualityPreference = Field(
        default=QualityPreference.BALANCED, description="默认质量偏好"
    )
    providers: List[VideoProviderConfig] = Field(
        default_factory=list, description="视频提供商配置列表"
    )
    smart_routing_enabled: bool = Field(
        default=True, description="是否启用智能路由"
    )
    fallback_enabled: bool = Field(
        default=True, description="是否启用失败回退"
    )
    fallback_max_attempts: int = Field(
        default=3, ge=1, le=5, description="最大回退尝试次数"
    )
    download_output_dir: str = Field(
        default="./output/videos", description="视频下载输出目录"
    )
    auto_download: bool = Field(default=True, description="是否自动下载")
    download_timeout_sec: int = Field(
        default=300, ge=30, description="下载超时(秒)"
    )

    @field_validator("download_output_dir")
    @classmethod
    def _expand_download_path(cls: type["VideoGenerationConfig"], v: str) -> str:
        """展开用户目录路径"""
        return os.path.expanduser(v)

    @model_validator(mode="after")
    def _validate_providers(self: "VideoGenerationConfig") -> "VideoGenerationConfig":
        """验证提供商列表（检查重复）"""
        names = [p.provider for p in self.providers]
        seen: Set[str] = set()
        for name in names:
            if name in seen:
                raise ValueError(f"视频提供商 '{name}' 重复配置")
            seen.add(name)
        return self


class SheetsConfig(BaseModel):
    """Google Sheets 配置"""

    model_config = ConfigDict(extra="allow")

    gem_metadata_spreadsheet_id: Optional[str] = Field(
        default=None, description="Gem 元数据 Spreadsheet ID"
    )
    task_queue_spreadsheet_id: Optional[str] = Field(
        default=None, description="任务队列 Spreadsheet ID"
    )
    gem_metadata_sheet_name: str = Field(default="gem-metadata")
    task_queue_sheet_name: str = Field(default="task-queue")
    gem_metadata_range: str = Field(default="A1:Z1000")
    task_queue_range: str = Field(default="A1:AD10000")
    service_account_file: Optional[str] = Field(
        default=None, description="GCP 服务账号 JSON 文件路径"
    )
    use_gws_cli: bool = Field(default=True, description="是否使用 gws CLI")
    gws_cli_path: str = Field(default="gws", description="gws CLI 路径")

    @field_validator("service_account_file")
    @classmethod
    def _expand_path(cls: type["SheetsConfig"], v: Optional[str]) -> Optional[str]:
        return os.path.expanduser(v) if v else v


class DriveConfig(BaseModel):
    """Google Drive 配置"""

    model_config = ConfigDict(extra="allow")

    enabled: bool = Field(default=True, description="是否启用 Drive 上传")
    output_folder_id: Optional[str] = Field(
        default=None, description="输出文件夹 ID"
    )
    create_subfolders: bool = Field(default=True, description="是否创建子文件夹")
    subfolder_name_template: str = Field(
        default="batch-{batch_id}-{date}",
        description="子文件夹命名模板",
    )
    share_with_anyone: bool = Field(default=False, description="是否公开分享")

    @field_validator("subfolder_name_template")
    @classmethod
    def _validate_template(cls: type["DriveConfig"], v: str) -> str:
        """验证模板变量（仅允许 {batch_id} 和 {date}）"""
        vars_found = set(re.findall(r"\{(\w+)\}", v))
        allowed = {"batch_id", "date"}
        invalid = vars_found - allowed
        if invalid:
            raise ValueError(
                f"子文件夹模板包含非法变量: {invalid}，只允许: {allowed}"
            )
        return v


class GmailConfig(BaseModel):
    """Gmail 通知配置"""

    model_config = ConfigDict(extra="allow")

    enabled: bool = Field(default=False, description="是否启用邮件通知")
    notification_email: Optional[str] = Field(
        default=None, description="通知邮箱地址"
    )
    notify_on_complete: bool = Field(default=True, description="完成时通知")
    notify_on_error: bool = Field(default=True, description="错误时通知")
    notify_on_batch_summary: bool = Field(default=True, description="批次汇总时通知")
    smtp_host: Optional[str] = Field(default=None, description="SMTP 服务器")
    smtp_port: int = Field(default=587, ge=1, le=65535, description="SMTP 端口")
    smtp_username: Optional[str] = Field(default=None, description="SMTP 用户名")
    smtp_password: Optional[SecretStr] = Field(
        default=None, description="SMTP 密码（敏感）"
    )

    @model_validator(mode="after")
    def _validate_email_config(self: "GmailConfig") -> "GmailConfig":
        """启用邮件通知时验证必要字段"""
        if self.enabled:
            if not self.notification_email:
                raise ValueError(
                    "启用邮件通知时必须设置 notification_email"
                )
            if not re.match(
                r"^[^@\s]+@[^@\s]+\.[^@\s]+$", self.notification_email
            ):
                raise ValueError(
                    f"邮箱格式无效: {self.notification_email}"
                )
        return self


class RouteMatchWeights(BaseModel):
    """路由匹配权重配置"""

    model_config = ConfigDict(extra="allow")

    product_category: float = Field(default=0.30, ge=0.0, le=1.0)
    marketing_scene: float = Field(default=0.20, ge=0.0, le=1.0)
    target_audience: float = Field(default=0.15, ge=0.0, le=1.0)
    price_range: float = Field(default=0.10, ge=0.0, le=1.0)
    video_style: float = Field(default=0.10, ge=0.0, le=1.0)
    history_data: float = Field(default=0.10, ge=0.0, le=1.0)
    other: float = Field(default=0.05, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_total(self: "RouteMatchWeights") -> "RouteMatchWeights":
        """校验权重之和等于 1.0（允许 0.01 的浮点误差）"""
        total = (
            self.product_category
            + self.marketing_scene
            + self.target_audience
            + self.price_range
            + self.video_style
            + self.history_data
            + self.other
        )
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"路由权重之和必须等于 1.0，当前: {total:.4f}"
            )
        return self

    def to_dict(self: "RouteMatchWeights") -> Dict[str, float]:
        """转为字典格式"""
        return {
            "product_category": self.product_category,
            "marketing_scene": self.marketing_scene,
            "target_audience": self.target_audience,
            "price_range": self.price_range,
            "video_style": self.video_style,
            "history_data": self.history_data,
            "other": self.other,
        }


class RoutingConfig(BaseModel):
    """智能路由配置"""

    model_config = ConfigDict(extra="allow")

    enabled: bool = Field(default=True, description="是否启用智能路由")
    top_n: int = Field(default=3, ge=1, le=5, description="返回前 N 个推荐")
    high_confidence_threshold: float = Field(
        default=0.85, ge=0.0, le=1.0, description="高置信度阈值"
    )
    medium_confidence_threshold: float = Field(
        default=0.70, ge=0.0, le=1.0, description="中置信度阈值"
    )
    weights: RouteMatchWeights = Field(
        default_factory=RouteMatchWeights, description="路由匹配权重"
    )
    analysis_model: str = Field(
        default="gemini-2.5-flash", description="图片分析模型"
    )
    analysis_prompt_template: Optional[str] = Field(
        default=None, description="自定义分析 Prompt 模板"
    )
    analysis_timeout_sec: int = Field(
        default=60, ge=10, description="图片分析超时(秒)"
    )
    self_learning_enabled: bool = Field(
        default=True, description="是否启用自学习"
    )
    learning_data_file: str = Field(
        default="~/.batch-video-skill/learning_data.json",
        description="自学习数据文件路径",
    )
    learning_weight_adjustment: float = Field(
        default=0.1, ge=0.0, le=0.5, description="自学习权重调整幅度"
    )

    @field_validator("learning_data_file")
    @classmethod
    def _expand_path(cls: type["RoutingConfig"], v: str) -> str:
        return os.path.expanduser(v)

    @model_validator(mode="after")
    def _validate_thresholds(self: "RoutingConfig") -> "RoutingConfig":
        """校验置信度阈值大小关系"""
        if self.high_confidence_threshold <= self.medium_confidence_threshold:
            raise ValueError(
                f"high_confidence_threshold ({self.high_confidence_threshold}) "
                f"必须大于 medium_confidence_threshold "
                f"({self.medium_confidence_threshold})"
            )
        return self


class BatchConfig(BaseModel):
    """批量任务配置"""

    model_config = ConfigDict(extra="allow")

    max_concurrency: int = Field(
        default=3, ge=1, le=10, description="最大并发数"
    )
    task_timeout_sec: int = Field(
        default=600, ge=60, le=3600, description="任务超时(秒)"
    )
    max_retries: int = Field(
        default=3, ge=0, le=5, description="最大重试次数"
    )
    retry_delay_sec: int = Field(
        default=10, ge=1, description="重试延迟(秒)"
    )
    retry_backoff_multiplier: float = Field(
        default=2.0, ge=1.0, description="重试退避倍数"
    )
    resume_from_last: bool = Field(
        default=True, description="是否从上次断点续传"
    )
    checkpoint_interval_sec: int = Field(
        default=60, ge=10, description="检查点保存间隔(秒)"
    )
    progress_update_interval_sec: int = Field(
        default=30, ge=5, description="进度更新间隔(秒)"
    )
    progress_display_format: ProgressFormat = Field(
        default=ProgressFormat.DETAILED, description="进度显示格式"
    )
    auto_cleanup_temp: bool = Field(
        default=True, description="是否自动清理临时文件"
    )
    temp_retention_hours: int = Field(
        default=24, ge=0, description="临时文件保留时长(小时)"
    )


class MonitoringConfig(BaseModel):
    """监控与可观测性配置"""

    model_config = ConfigDict(extra="allow")

    enable_metrics: bool = Field(
        default=True, description="是否启用性能指标收集"
    )
    metrics_output_file: Optional[str] = Field(
        default="~/.batch-video-skill/metrics.json",
        description="指标输出文件路径",
    )
    enable_cost_tracking: bool = Field(
        default=True, description="是否启用成本追踪"
    )
    cost_alert_threshold_usd: float = Field(
        default=50.0, ge=0.0, description="成本告警阈值(USD)"
    )
    health_check_interval_sec: int = Field(
        default=300, ge=60, description="健康检查间隔(秒)"
    )
    auto_validate_credentials: bool = Field(
        default=True, description="是否自动验证凭证"
    )

    @field_validator("metrics_output_file")
    @classmethod
    def _expand_path(cls: type["MonitoringConfig"], v: Optional[str]) -> Optional[str]:
        return os.path.expanduser(v) if v else v


class SecurityConfig(BaseModel):
    """安全配置"""

    model_config = ConfigDict(extra="allow")

    storage_backend: StorageBackend = Field(
        default=StorageBackend.KEYRING, description="安全存储后端类型"
    )
    encryption_key_file: Optional[str] = Field(
        default=None, description="自定义加密密钥文件路径"
    )
    sensitive_fields: List[str] = Field(
        default_factory=lambda: [
            "auth.*.credentials.api_keys",
            "auth.*.credentials.cookie_value",
            "auth.*.credentials.client_secret",
            "auth.*.credentials.refresh_token",
            "auth.encrypted_file_password",
            "video_generation.providers.*.api_keys",
            "sheets.service_account_file",
            "gmail.smtp_password",
        ],
        description="敏感字段模式列表（支持 * 通配符）",
    )
    config_file_permissions: str = Field(
        default="0o600", description="配置文件权限模式（八进制字符串）"
    )

    @field_validator("config_file_permissions")
    @classmethod
    def _validate_permissions(cls: type["SecurityConfig"], v: str) -> str:
        """验证权限模式格式"""
        if not re.match(r"^0o[0-7]{3}$", v):
            raise ValueError(
                f"权限模式 '{v}' 格式无效，应为八进制字符串如 '0o600'"
            )
        return v


class SkillConfig(BaseModel):
    """Skill 完整配置模型（根配置）"""

    model_config = ConfigDict(extra="allow")

    # ── 版本控制 ──
    config_version: str = Field(default="1.0", description="配置版本号")
    config_schema_version: int = Field(
        default=1, ge=1, description="配置 Schema 版本号"
    )

    # ── 子模块配置 ──
    auth: AuthConfig = Field(
        default_factory=AuthConfig, description="认证配置"
    )
    video_generation: VideoGenerationConfig = Field(
        default_factory=VideoGenerationConfig, description="视频生成配置"
    )
    sheets: SheetsConfig = Field(
        default_factory=SheetsConfig, description="Google Sheets 配置"
    )
    drive: DriveConfig = Field(
        default_factory=DriveConfig, description="Google Drive 配置"
    )
    gmail: GmailConfig = Field(
        default_factory=GmailConfig, description="Gmail 通知配置"
    )
    routing: RoutingConfig = Field(
        default_factory=RoutingConfig, description="智能路由配置"
    )
    batch: BatchConfig = Field(
        default_factory=BatchConfig, description="批量任务配置"
    )
    monitoring: MonitoringConfig = Field(
        default_factory=MonitoringConfig, description="监控配置"
    )
    security: SecurityConfig = Field(
        default_factory=SecurityConfig, description="安全配置"
    )

    # ── 全局设置 ──
    log_level: LogLevel = Field(default=LogLevel.INFO, description="日志级别")
    log_file: Optional[str] = Field(
        default="~/.batch-video-skill/skill.log", description="日志文件路径"
    )
    log_format: LogFormat = Field(
        default=LogFormat.DETAILED, description="日志格式"
    )
    log_max_bytes: int = Field(
        default=10 * 1024 * 1024, ge=1024,
        description="日志文件最大字节数(默认 10MB)"
    )
    log_backup_count: int = Field(
        default=5, ge=0, description="日志备份数量"
    )
    cache_dir: str = Field(
        default="~/.batch-video-skill/cache", description="缓存目录"
    )
    temp_dir: str = Field(default="./tmp", description="临时目录")

    # ── 运行时统计（不持久化）──
    _loaded_at: Optional[datetime] = None
    _loaded_sources: List[str] = []
    _config_file_path: Optional[str] = None

    @field_validator("log_file", "cache_dir", "temp_dir")
    @classmethod
    def _expand_paths(cls: type["SkillConfig"], v: Optional[str]) -> Optional[str]:
        """展开用户目录路径"""
        return os.path.expanduser(v) if v else v

    def get_provider(self: "SkillConfig", name: str) -> Optional[VideoProviderConfig]:
        """按名称获取视频提供商配置"""
        for p in self.video_generation.providers:
            if p.provider == name:
                return p
        return None

    def get_enabled_providers(self: "SkillConfig") -> List[VideoProviderConfig]:
        """获取所有启用的视频提供商"""
        return [
            p for p in self.video_generation.providers if p.enabled
        ]


# ============================================================================
# 配置管理异常
# ============================================================================


class ConfigError(Exception):
    """配置管理基类异常"""

    def __init__(
        self,
        message: str = "",
        key_path: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.key_path = key_path
        self.original_error = original_error
        super().__init__(message)

    def __str__(self: "ConfigError") -> str:
        parts = [self.args[0] if self.args else "配置错误"]
        if self.key_path:
            parts.append(f" (路径: {self.key_path})")
        if self.original_error:
            parts.append(f" [原始错误: {self.original_error}]")
        return "".join(parts)


class ConfigNotLoadedError(ConfigError):
    """配置未加载异常"""

    def __init__(
        self, message: str = "配置未加载，请先调用 load()"
    ) -> None:
        super().__init__(message)


class ConfigKeyError(ConfigError):
    """配置键不存在异常"""

    def __init__(
        self,
        key_path: str = "",
        message: Optional[str] = None,
    ) -> None:
        msg = message or f"配置键不存在: {key_path}"
        super().__init__(msg, key_path=key_path)


class ConfigValidationError(ConfigError):
    """配置验证失败异常"""

    def __init__(
        self,
        result: Optional[Any] = None,
        message: Optional[str] = None,
    ) -> None:
        self.result = result
        super().__init__(message or "配置验证失败")

    @property
    def error_count(self: "ConfigValidationError") -> int:
        """错误数量"""
        return 0

    @property
    def warning_count(self: "ConfigValidationError") -> int:
        """警告数量"""
        return 0


class ConfigFileError(ConfigError):
    """配置文件操作失败异常"""

    def __init__(
        self,
        path: str = "",
        operation: str = "",
        message: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.path = path
        self.operation = operation
        msg = message or f"配置文件操作失败 [{operation}]: {path}"
        super().__init__(msg, original_error=original_error)


class ConfigReloadError(ConfigError):
    """配置热重载失败异常"""

    def __init__(
        self,
        trigger: str = "",
        message: Optional[str] = None,
        original_error: Optional[Exception] = None,
    ) -> None:
        self.trigger = trigger
        msg = message or f"配置热重载失败 (触发方式: {trigger})"
        super().__init__(msg, original_error=original_error)


class ReloadNotAllowedError(ConfigReloadError):
    """字段不允许热更新异常"""

    def __init__(self: "ReloadNotAllowedError", key_path: str = "") -> None:
        self.key_path = key_path
        super().__init__(
            message=f"字段 '{key_path}' 不在热更新白名单中，不允许热更新"
        )


# ============================================================================
# 工具函数
# ============================================================================


def get_nested_value(
    data: Dict[str, Any],
    key_path: str,
    default: Any = None,
    separator: str = ".",
) -> Any:
    """通过点分路径获取嵌套字典值"""
    if not key_path:
        return data
    keys = key_path.split(separator)
    current: Any = data
    for key in keys:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(key, default)
        elif isinstance(current, list):
            if not key.isdigit():
                return default
            idx = int(key)
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return default
        else:
            return default
    return current


def set_nested_value(
    data: Any,
    key_path: str,
    value: Any,
    separator: str = ".",
    create_intermediate: bool = True,
) -> None:
    """通过点分路径设置嵌套字典或 Pydantic 模型值"""
    if not key_path:
        return
    keys = key_path.split(separator)
    current: Any = data
    for key in keys[:-1]:
        if key.isdigit() and isinstance(current, list):
            idx = int(key)
            current = current[idx]
        elif isinstance(current, dict):
            if key not in current and create_intermediate:
                current[key] = {}
            current = current[key]
        elif hasattr(current, key):
            # Pydantic BaseModel 或其他对象属性
            current = getattr(current, key)
        else:
            raise ConfigKeyError(
                key_path,
                f"无法在类型 {type(current).__name__} 上设置路径 '{key_path}'",
            )
    last_key = keys[-1]
    if hasattr(current, "__setitem__"):
        if last_key.isdigit() and isinstance(current, list):
            idx = int(last_key)
            if 0 <= idx < len(current):
                current[idx] = value
            else:
                raise ConfigKeyError(
                    key_path, f"列表索引越界: {idx} (长度: {len(current)})"
                )
        else:
            current[last_key] = value
    elif hasattr(current, last_key):
        setattr(current, last_key, value)
    else:
        raise ConfigKeyError(
            key_path, f"目标对象没有属性 '{last_key}'"
        )


def flatten_dict(
    data: Dict[str, Any],
    parent_key: str = "",
    separator: str = ".",
) -> Dict[str, Any]:
    """将嵌套字典扁平化为点分路径格式"""
    items: List[Tuple[str, Any]] = []
    for key, value in data.items():
        new_key = f"{parent_key}{separator}{key}" if parent_key else str(key)
        if isinstance(value, dict):
            items.extend(
                flatten_dict(value, new_key, separator).items()
            )
        elif isinstance(value, list):
            for i, item in enumerate(value):
                list_key = f"{new_key}{separator}{i}"
                if isinstance(item, dict):
                    items.extend(
                        flatten_dict(item, list_key, separator).items()
                    )
                else:
                    items.append((list_key, item))
        else:
            items.append((new_key, value))
    return dict(items)


def deep_merge(
    base: Dict[str, Any],
    override: Dict[str, Any],
) -> Dict[str, Any]:
    """
    深度合并两个字典

    合并规则:
    1. 字典字段递归合并
    2. 列表字段完全替换
    3. 标量字段直接覆盖
    4. None 值表示"使用默认值"，不参与覆盖
    """
    result = copy.deepcopy(base)
    for key, val in override.items():
        if val is None:
            continue
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(val, dict)
        ):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def merge_configs(
    layers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    合并多层配置（从最低优先级到最高优先级逐层合并）
    """
    result: Dict[str, Any] = {}
    for layer in layers:
        result = deep_merge(result, layer)
    return result


def strip_json_comments(content: str) -> str:
    """移除 JSON 中的注释（支持 // 和 /* */）

    安全处理: 不将 URL 中的 // (如 http://) 视为注释。
    """
    result = []
    i = 0
    length = len(content)

    while i < length:
        char = content[i]

        # 字符串内原样保留
        if char == '"':
            result.append(char)
            i += 1
            while i < length:
                c = content[i]
                result.append(c)
                if c == "\\" and i + 1 < length:
                    result.append(content[i + 1])
                    i += 2
                    continue
                if c == '"':
                    i += 1
                    break
                i += 1
            continue

        # 块注释 /* */
        if char == "/" and i + 1 < length and content[i + 1] == "*":
            i += 2
            while i < length - 1:
                if content[i] == "*" and content[i + 1] == "/":
                    i += 2
                    break
                i += 1
            continue

        # 单行注释 // (仅在行首或空白字符后出现)
        if (
            char == "/"
            and i + 1 < length
            and content[i + 1] == "/"
            and (i == 0 or content[i - 1] in " \t\n\r")
        ):
            while i < length and content[i] not in "\n\r":
                i += 1
            continue

        result.append(char)
        i += 1

    return "".join(result)


def match_pattern(key_path: str, pattern: str) -> bool:
    """匹配配置键模式（支持 * 和 ? 通配符）"""
    return fnmatch.fnmatch(key_path, pattern)


# ============================================================================
# SecureVault — 安全存储
# ============================================================================


class SecureVault:
    """
    安全存储管理器

    自动选择最安全可用的存储后端:
    1. 系统密钥环 (keyring) — 最安全
    2. 加密文件 (keyrings.cryptfile) — 服务器环境
    3. 环境变量 — CI/开发

    Attributes:
        preferred_backend: 首选存储后端类型

    Example:
        >>> vault = SecureVault()
        >>> vault.store("kling", "api_key", "sk-xxx")
        >>> key = vault.retrieve("kling", "api_key")
    """

    def __init__(
        self,
        preferred_backend: StorageBackend = StorageBackend.KEYRING,
        password: Optional[str] = None,
    ) -> None:
        """
        初始化安全存储

        Args:
            preferred_backend: 首选存储后端
            password: 加密文件密码 (如使用加密文件存储)
        """
        self.preferred_backend = preferred_backend
        self._password = password
        self._keyring = None
        self._cryptfile = None
        self._init_backends()

    def _init_backends(self: "SecureVault") -> None:
        """初始化存储后端"""
        # 尝试 keyring
        try:
            import keyring as kr

            self._keyring = kr
            logger.info("系统密钥环后端已加载: %s", kr.get_keyring())
        except ImportError:
            logger.debug("keyring 库未安装")

        # 尝试加密文件
        try:
            from keyrings.cryptfile.cryptfile import CryptFileKeyring

            self._cryptfile = CryptFileKeyring()
            storage_dir = (
                Path.home() / ".batch-video-skill" / "secrets"
            )
            storage_dir.mkdir(parents=True, exist_ok=True)
            self._cryptfile.file_path = str(storage_dir / "vault.cfg")
            logger.info("加密文件存储后端已加载")
        except ImportError:
            logger.debug("keyrings.cryptfile 库未安装")

    def store(self: "SecureVault", service: str, key: str, value: str) -> bool:
        """
        存储凭证

        Args:
            service: 服务名称
            key: 凭证键名
            value: 凭证值

        Returns:
            True 表示存储成功
        """
        # 按优先级尝试存储
        if self.preferred_backend == StorageBackend.KEYRING:
            if self._store_keyring(service, key, value):
                return True
            if self._store_cryptfile(service, key, value):
                return True
        elif self.preferred_backend == StorageBackend.ENCRYPTED_FILE:
            if self._store_cryptfile(service, key, value):
                return True

        # 回退到环境变量
        env_key = f"BVS_VAULT_{service.upper()}_{key.upper()}"
        os.environ[env_key] = value
        logger.info("凭证已存储到环境变量 [%s]", env_key)
        return True

    def retrieve(self: "SecureVault", service: str, key: str) -> Optional[str]:
        """
        检索凭证

        Args:
            service: 服务名称
            key: 凭证键名

        Returns:
            凭证值 或 None
        """
        # 按优先级尝试检索
        if self.preferred_backend == StorageBackend.KEYRING:
            value = self._get_keyring(service, key)
            if value:
                return value
            value = self._get_cryptfile(service, key)
            if value:
                return value
        elif self.preferred_backend == StorageBackend.ENCRYPTED_FILE:
            value = self._get_cryptfile(service, key)
            if value:
                return value

        # 回退到环境变量
        env_key = f"BVS_VAULT_{service.upper()}_{key.upper()}"
        return os.environ.get(env_key)

    def delete(self: "SecureVault", service: str, key: str) -> bool:
        """删除凭证"""
        deleted = False
        if self._keyring:
            try:
                self._keyring.delete_password(service, key)
                deleted = True
            except Exception:
                pass
        if self._cryptfile:
            try:
                self._cryptfile.delete_password(service, key)
                deleted = True
            except Exception:
                pass
        env_key = f"BVS_VAULT_{service.upper()}_{key.upper()}"
        if env_key in os.environ:
            del os.environ[env_key]
            deleted = True
        return deleted

    def _store_keyring(self: "SecureVault", service: str, key: str, value: str) -> bool:
        """存储到系统密钥环"""
        if not self._keyring:
            return False
        try:
            self._keyring.set_password(service, key, value)
            logger.info("凭证已存储到系统密钥环 [%s/%s]", service, key)
            return True
        except Exception as e:
            logger.warning("存储到密钥环失败: %s", e)
            return False

    def _get_keyring(self: "SecureVault", service: str, key: str) -> Optional[str]:
        """从系统密钥环检索"""
        if not self._keyring:
            return None
        try:
            return self._keyring.get_password(service, key)
        except Exception as e:
            logger.warning("从密钥环读取失败: %s", e)
            return None

    def _store_cryptfile(self: "SecureVault", service: str, key: str, value: str) -> bool:
        """存储到加密文件"""
        if not self._cryptfile:
            return False
        try:
            if not hasattr(self._cryptfile, "keyring_key") or not self._cryptfile.keyring_key:
                if self._password:
                    self._cryptfile.keyring_key = self._password
                else:
                    return False
            self._cryptfile.set_password(service, key, value)
            logger.info("凭证已存储到加密文件 [%s/%s]", service, key)
            return True
        except Exception as e:
            logger.warning("存储到加密文件失败: %s", e)
            return False

    def _get_cryptfile(self: "SecureVault", service: str, key: str) -> Optional[str]:
        """从加密文件检索"""
        if not self._cryptfile:
            return None
        try:
            if not hasattr(self._cryptfile, "keyring_key") or not self._cryptfile.keyring_key:
                if self._password:
                    self._cryptfile.keyring_key = self._password
                else:
                    return None
            return self._cryptfile.get_password(service, key)
        except Exception as e:
            logger.warning("从加密文件读取失败: %s", e)
            return None


# ============================================================================
# 配置源抽象基类
# ============================================================================


class BaseConfigSource:
    """配置源抽象基类"""

    @property
    def name(self: "BaseConfigSource") -> str:
        """配置源名称"""
        return self.__class__.__name__

    def load(self: "BaseConfigSource") -> Dict[str, Any]:
        """加载配置，返回字典"""
        raise NotImplementedError


class DefaultConfigSource(BaseConfigSource):
    """默认配置源 — 内置默认值"""

    @property
    def name(self: "DefaultConfigSource") -> str:
        return "DEFAULT"

    def load(self: "DefaultConfigSource") -> Dict[str, Any]:
        """加载内置默认配置"""
        config = SkillConfig()
        return config.model_dump(exclude_none=True)


class UserFileConfigSource(BaseConfigSource):
    """用户配置文件源"""

    def __init__(
        self,
        path: str = "~/.batch-video-skill/config.json",
    ) -> None:
        self.path = os.path.expanduser(path)

    @property
    def name(self: "UserFileConfigSource") -> str:
        return "USER"

    def load(self: "UserFileConfigSource") -> Dict[str, Any]:
        """从用户配置文件加载"""
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                content = f.read()
                content = strip_json_comments(content)
                return json.loads(content)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("读取用户配置文件失败 [%s]: %s", self.path, e)
            return {}


class EnvVarConfigSource(BaseConfigSource):
    """环境变量配置源 — BVS_ 前缀"""

    PREFIX = "BVS_"

    @property
    def name(self: "EnvVarConfigSource") -> str:
        return "ENV"

    def load(self: "EnvVarConfigSource") -> Dict[str, Any]:
        """从环境变量加载 BVS_ 前缀的变量"""
        result: Dict[str, Any] = {}
        for key, value in os.environ.items():
            if key.startswith(self.PREFIX):
                # BVS_LOG_LEVEL -> log_level
                config_key = key[len(self.PREFIX) :].lower()
                # 支持嵌套: BVS_AUTH_KLING_ENDPOINT -> auth.kling.endpoint
                if "_" in config_key:
                    # 尝试解析为点分路径
                    path_parts = config_key.split("_")
                    self._set_nested_env(result, path_parts, value)
                else:
                    result[config_key] = self._parse_value(value)
        return result

    def _set_nested_env(
        self, data: Dict[str, Any], parts: List[str], value: str
    ) -> None:
        """设置嵌套环境变量值"""
        current = data
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = self._parse_value(value)

    def _parse_value(self: "EnvVarConfigSource", value: str) -> Any:
        """解析环境变量值"""
        # 尝试 bool
        if value.lower() in ("true", "false"):
            return value.lower() == "true"
        # 尝试 int
        try:
            return int(value)
        except ValueError:
            pass
        # 尝试 float
        try:
            return float(value)
        except ValueError:
            pass
        # 字符串
        return value


class CLIConfigSource(BaseConfigSource):
    """命令行参数配置源"""

    def __init__(self: "CLIConfigSource", args: Optional[Dict[str, Any]] = None) -> None:
        self.args = args or {}

    @property
    def name(self: "CLIConfigSource") -> str:
        return "CLI"

    def load(self: "CLIConfigSource") -> Dict[str, Any]:
        """从命令行参数加载"""
        return self.args


# ============================================================================
# HotReloader — 热更新模块
# ============================================================================


class HotReloader:
    """
    配置热更新模块

    支持三种触发方式:
    1. 文件监听 (watchdog)
    2. 信号触发 (SIGHUP)
    3. API 调用

    白名单机制: 只有白名单中的字段才允许热更新

    Attributes:
        whitelist: 允许热更新的字段模式列表
        callbacks: 配置变更回调函数列表

    Example:
        >>> reloader = HotReloader()
        >>> reloader.add_callback(lambda changes: print("Config changed:", changes))
        >>> reloader.start_watching("config.json")
    """

    # 默认热更新白名单
    DEFAULT_WHITELIST = [
        "log_level",
        "log_format",
        "video_generation.providers.*.weight",
        "video_generation.providers.*.enabled",
        "video_generation.smart_routing_enabled",
        "video_generation.fallback_enabled",
        "routing.enabled",
        "routing.weights.*",
        "routing.top_n",
        "batch.max_concurrency",
        "batch.max_retries",
        "monitoring.enable_metrics",
        "monitoring.cost_alert_threshold_usd",
    ]

    def __init__(
        self,
        whitelist: Optional[List[str]] = None,
    ) -> None:
        """
        初始化热更新模块

        Args:
            whitelist: 热更新白名单字段模式列表
        """
        self.whitelist = whitelist or self.DEFAULT_WHITELIST
        self.callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self._watching = False
        self._watch_thread: Optional[threading.Thread] = None

    def is_allowed(self: "HotReloader", key_path: str) -> bool:
        """
        检查字段是否允许热更新

        Args:
            key_path: 点分路径

        Returns:
            True 如果字段在白名单中
        """
        for pattern in self.whitelist:
            if match_pattern(key_path, pattern):
                return True
        return False

    def filter_changes(self: "HotReloader", changes: Dict[str, Any]) -> Dict[str, Any]:
        """
        过滤变更，仅保留白名单中的字段

        Args:
            changes: {路径: 新值} 字典

        Returns:
            过滤后的变更字典
        """
        return {
            k: v for k, v in changes.items() if self.is_allowed(k)
        }

    def add_callback(
        self, callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """
        添加配置变更回调

        Args:
            callback: 回调函数(changes_dict) -> None
        """
        self.callbacks.append(callback)

    def notify(self: "HotReloader", changes: Dict[str, Any]) -> None:
        """
        通知所有回调配置变更

        Args:
            changes: 变更字典 {路径: 新值}
        """
        allowed_changes = self.filter_changes(changes)
        if not allowed_changes:
            logger.debug("没有允许热更新的变更")
            return

        logger.info("配置热更新: %s", allowed_changes)
        for callback in self.callbacks:
            try:
                callback(allowed_changes)
            except Exception as e:
                logger.error("热更新回调执行失败: %s", e)

    def compute_changes(
        self,
        old_config: Dict[str, Any],
        new_config: Dict[str, Any],
    ) -> Dict[str, Tuple[Any, Any]]:
        """
        计算两个字典之间的差异

        Returns:
            {路径: (旧值, 新值)}
        """
        old_flat = flatten_dict(old_config)
        new_flat = flatten_dict(new_config)
        changes: Dict[str, Tuple[Any, Any]] = {}

        all_keys = set(old_flat.keys()) | set(new_flat.keys())
        for key in all_keys:
            old_val = old_flat.get(key)
            new_val = new_flat.get(key)
            if old_val != new_val:
                changes[key] = (old_val, new_val)

        return changes

    def register_signal_handler(self: "HotReloader") -> None:
        """注册 SIGHUP 信号处理器"""
        try:
            signal.signal(signal.SIGHUP, self._signal_handler)
            logger.info("已注册 SIGHUP 信号处理器")
        except (AttributeError, ValueError):
            # Windows 不支持 SIGHUP
            logger.debug("当前平台不支持 SIGHUP 信号")

    def _signal_handler(self: "HotReloader", signum: int, frame: Any) -> None:
        """信号处理器"""
        logger.info("收到信号 %s，触发配置热重载", signum)
        # 通知回调进行重载
        self.notify({"__signal__": (None, signum)})


# ============================================================================
# ConfigManager — 配置管理器主类
# ============================================================================


class ConfigManager:
    """
    统一配置管理器

    提供配置的全生命周期管理:
    - 分层加载: DEFAULT -> USER -> ENV -> CLI
    - Schema 验证: Pydantic v2 强类型校验
    - 安全存储: SecureVault 自动加密敏感字段
    - 热更新: 白名单机制，文件/信号/API 三种触发
    - 配置访问: 点分路径 + 类型安全 + 变更订阅

    线程安全: 所有公共方法使用 RLock 保证线程安全。

    Example:
        >>> manager = ConfigManager()
        >>> manager.load()
        >>> print(manager.get("video_generation.default_resolution"))
        "720p"
        >>> manager.set("log_level", "DEBUG")
    """

    # 配置源优先级（从低到高）
    SOURCE_ORDER = ["DEFAULT", "USER", "ENV", "CLI"]

    def __init__(
        self,
        config_file_path: str = "~/.batch-video-skill/config.json",
        cli_args: Optional[Dict[str, Any]] = None,
        vault_password: Optional[str] = None,
    ) -> None:
        """
        初始化配置管理器

        Args:
            config_file_path: 用户配置文件路径
            cli_args: 命令行参数字典
            vault_password: 安全存储密码
        """
        self._config_file_path = config_file_path
        self._cli_args = cli_args or {}
        self._vault_password = vault_password

        # 配置源
        self._sources: Dict[str, BaseConfigSource] = {
            "DEFAULT": DefaultConfigSource(),
            "USER": UserFileConfigSource(config_file_path),
            "ENV": EnvVarConfigSource(),
            "CLI": CLIConfigSource(cli_args),
        }

        # 运行时状态
        self._merged_raw: Dict[str, Any] = {}
        self._config: Optional[SkillConfig] = None
        self._loaded_sources: List[str] = []
        self._lock = threading.RLock()
        self._vault: Optional[SecureVault] = None
        self._reloader: Optional[HotReloader] = None
        self._change_callbacks: List[Callable[[str, Any, Any], None]] = []

    # ─── 属性 ───

    @property
    def config(self: "ConfigManager") -> SkillConfig:
        """获取当前配置（确保已加载）"""
        if self._config is None:
            raise ConfigNotLoadedError()
        return self._config

    @property
    def is_loaded(self: "ConfigManager") -> bool:
        """配置是否已加载"""
        return self._config is not None

    @property
    def loaded_sources(self: "ConfigManager") -> List[str]:
        """已加载的配置源列表"""
        return list(self._loaded_sources)

    # ─── 核心加载 ───

    def load(self: "ConfigManager") -> "ConfigManager":
        """
        加载所有配置源并合并

        加载顺序: DEFAULT -> USER -> ENV -> CLI
        高优先级配置覆盖低优先级。

        Returns:
            self（支持链式调用）
        """
        with self._lock:
            layers: List[Dict[str, Any]] = []
            self._loaded_sources = []

            for source_name in self.SOURCE_ORDER:
                source = self._sources[source_name]
                try:
                    data = source.load()
                    if data:
                        layers.append(data)
                        self._loaded_sources.append(source_name)
                        logger.debug(
                            "配置源 %s 已加载: %d 个顶层键",
                            source_name,
                            len(data),
                        )
                except Exception as e:
                    logger.warning(
                        "配置源 %s 加载失败: %s", source_name, e
                    )

            # 合并所有层
            self._merged_raw = merge_configs(layers)

            # Pydantic 校验
            try:
                self._config = SkillConfig(**self._merged_raw)
            except Exception as e:
                raise ConfigValidationError(
                    message=f"配置验证失败: {e}"
                ) from e

            # 初始化 SecureVault
            if self._config:
                self._vault = SecureVault(
                    preferred_backend=self._config.security.storage_backend,
                    password=self._vault_password,
                )

            logger.info(
                "配置加载完成 (源: %s)", ", ".join(self._loaded_sources)
            )
            return self

    def reload(self: "ConfigManager") -> "ConfigManager":
        """
        重新加载配置

        Returns:
            self（支持链式调用）
        """
        with self._lock:
            old_config = self._config
            self.load()

            # 通知变更
            if old_config and self._reloader:
                old_dict = old_config.model_dump(exclude_none=True)
                new_dict = (
                    self._config.model_dump(exclude_none=True)
                    if self._config
                    else {}
                )
                changes = self._reloader.compute_changes(old_dict, new_dict)
                if changes:
                    self._reloader.notify(
                        {k: v[1] for k, v in changes.items()}
                    )

            return self

    # ─── 配置访问 ───

    def get(self: "ConfigManager", key_path: str, default: Any = None) -> Any:
        """
        通过点分路径获取配置值

        Args:
            key_path: 点分路径，如 "video_generation.default_resolution"
            default: 默认值

        Returns:
            配置值或默认值
        """
        with self._lock:
            if not self.is_loaded:
                raise ConfigNotLoadedError()
            return get_nested_value(
                self._config.model_dump(), key_path, default=default
            )

    def get_typed(self: "ConfigManager", key_path: str, type_: type) -> Any:
        """
        获取配置值并强制类型转换

        Args:
            key_path: 点分路径
            type_: 期望的类型

        Returns:
            类型转换后的值

        Raises:
            ConfigKeyError: 路径不存在
            TypeError: 类型不匹配
        """
        value = self.get(key_path)
        if value is None:
            raise ConfigKeyError(key_path)
        if not isinstance(value, type_):
            raise TypeError(
                f"配置 '{key_path}' 类型不匹配: "
                f"期望 {type_.__name__}, 实际 {type(value).__name__}"
            )
        return value

    def set(self: "ConfigManager", key_path: str, value: Any) -> None:
        """
        设置配置值

        Args:
            key_path: 点分路径
            value: 新值
        """
        with self._lock:
            if not self.is_loaded:
                raise ConfigNotLoadedError()

            old_value = self.get(key_path)
            set_nested_value(self._config, key_path, value)

            # 通知变更回调
            for callback in self._change_callbacks:
                try:
                    callback(key_path, old_value, value)
                except Exception as e:
                    logger.error("配置变更回调失败: %s", e)

    def has(self: "ConfigManager", key_path: str) -> bool:
        """
        检查配置路径是否存在

        Args:
            key_path: 点分路径

        Returns:
            True 如果路径存在且值不为 None
        """
        return self.get(key_path) is not None

    # ─── 配置存储 ───

    def save(self: "ConfigManager", path: Optional[str] = None) -> None:
        """
        保存配置到文件

        SecretStr 字段自动脱敏为 "********"

        Args:
            path: 保存路径 (默认使用用户配置文件路径)
        """
        with self._lock:
            if not self.is_loaded:
                raise ConfigNotLoadedError()

            save_path = os.path.expanduser(
                path or self._config_file_path
            )
            os.makedirs(os.path.dirname(save_path), exist_ok=True)

            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(
                    self._config.model_dump(mode="json", exclude_none=True),
                    f,
                    indent=2,
                    ensure_ascii=False,
                )

            logger.info("配置已保存到 %s", save_path)

    # ─── 点分路径访问器 ───

    def get_provider(self: "ConfigManager", name: str) -> Optional[VideoProviderConfig]:
        """按名称获取视频提供商配置"""
        return self.config.get_provider(name)

    def get_enabled_providers(self: "ConfigManager") -> List[VideoProviderConfig]:
        """获取所有启用的视频提供商"""
        return self.config.get_enabled_providers()

    # ─── SecureVault 集成 ───

    @property
    def vault(self: "ConfigManager") -> SecureVault:
        """获取安全存储实例"""
        if self._vault is None:
            raise ConfigNotLoadedError("SecureVault 未初始化")
        return self._vault

    def store_secret(self: "ConfigManager", service: str, key: str, value: str) -> bool:
        """
        存储敏感配置到安全存储

        Args:
            service: 服务名称
            key: 凭证键名
            value: 凭证值

        Returns:
            True 表示存储成功
        """
        return self.vault.store(service, key, value)

    def retrieve_secret(self: "ConfigManager", service: str, key: str) -> Optional[str]:
        """
        从安全存储检索敏感配置

        Args:
            service: 服务名称
            key: 凭证键名

        Returns:
            凭证值或 None
        """
        return self.vault.retrieve(service, key)

    # ─── 热更新集成 ───

    @property
    def reloader(self: "ConfigManager") -> HotReloader:
        """获取热更新模块"""
        if self._reloader is None:
            self._reloader = HotReloader()
            self._reloader.add_callback(self._on_hot_reload)
        return self._reloader

    def _on_hot_reload(self: "ConfigManager", changes: Dict[str, Any]) -> None:
        """热更新回调"""
        logger.info("配置热更新生效: %s", changes)
        # 重新加载配置
        try:
            self.reload()
        except Exception as e:
            logger.error("热更新重载失败: %s", e)

    def enable_hot_reload(self: "ConfigManager", whitelist: Optional[List[str]] = None) -> None:
        """
        启用热更新功能

        Args:
            whitelist: 热更新白名单 (None 使用默认白名单)
        """
        if whitelist:
            self.reloader.whitelist = whitelist
        self.reloader.register_signal_handler()
        logger.info("热更新已启用，白名单: %s", self.reloader.whitelist)

    def add_change_callback(
        self, callback: Callable[[str, Any, Any], None]
    ) -> None:
        """
        添加配置变更回调

        Args:
            callback: 回调函数(key_path, old_value, new_value) -> None
        """
        self._change_callbacks.append(callback)

    # ─── 序列化 ───

    def to_dict(self: "ConfigManager") -> Dict[str, Any]:
        """导出为字典"""
        with self._lock:
            if not self.is_loaded:
                raise ConfigNotLoadedError()
            return self._config.model_dump(mode="json", exclude_none=True)

    def to_json(self: "ConfigManager", indent: int = 2) -> str:
        """导出为 JSON 字符串"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    # ─── 上下文管理器 ───

    def __enter__(self: "ConfigManager") -> "ConfigManager":
        if not self.is_loaded:
            self.load()
        return self

    def __exit__(self: "ConfigManager", exc_type, exc_val, exc_tb: Any | None) -> None:
        pass

    def __repr__(self: "ConfigManager") -> str:
        status = "loaded" if self.is_loaded else "not_loaded"
        sources = ", ".join(self._loaded_sources)
        return f"<ConfigManager(status={status}, sources=[{sources}])>"


# ============================================================================
# 模块导出
# ============================================================================

__all__ = [
    # 枚举
    "LogLevel",
    "CostTier",
    "AuthType",
    "StorageBackend",
    "QualityPreference",
    "ProgressFormat",
    "LogFormat",
    # Pydantic 模型
    "AuthCredentials",
    "ProviderAuthConfig",
    "AuthConfig",
    "VideoProviderConfig",
    "VideoGenerationConfig",
    "SheetsConfig",
    "DriveConfig",
    "GmailConfig",
    "RouteMatchWeights",
    "RoutingConfig",
    "BatchConfig",
    "MonitoringConfig",
    "SecurityConfig",
    "SkillConfig",
    # 异常
    "ConfigError",
    "ConfigNotLoadedError",
    "ConfigKeyError",
    "ConfigValidationError",
    "ConfigFileError",
    "ConfigReloadError",
    "ReloadNotAllowedError",
    # 工具函数
    "get_nested_value",
    "set_nested_value",
    "flatten_dict",
    "deep_merge",
    "merge_configs",
    "strip_json_comments",
    "match_pattern",
    # SecureVault
    "SecureVault",
    # 配置源
    "BaseConfigSource",
    "DefaultConfigSource",
    "UserFileConfigSource",
    "EnvVarConfigSource",
    "CLIConfigSource",
    # 热更新
    "HotReloader",
    # 主类
    "ConfigManager",
]
