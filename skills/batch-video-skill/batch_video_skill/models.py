#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
models.py — 共享数据模型

300 Agent 融合项目的核心数据模型层。
集中定义所有 Pydantic 模型、枚举类型和类型别名，
供 Skill 控制器及各子模块共享使用。

包含:
    - 枚举定义: 业务枚举、状态枚举、配置枚举
    - 输入模型: 各种请求参数模型
    - 输出模型: 各种响应结果模型
    - 配置模型: 全局配置结构
    - 类型别名: 常用复合类型

Python: 3.11+
Pydantic: v2
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Coroutine,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    TypeVar,
    Union,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

# ═══════════════════════════════════════════════════════════
# 版本信息
# ═══════════════════════════════════════════════════════════

__version__ = "4.0.0"
__version_info__ = (4, 0, 0)

# ═══════════════════════════════════════════════════════════
# 1. 枚举定义
# ═══════════════════════════════════════════════════════════


class ProcessingStage(str, Enum):
    """处理阶段枚举 — 描述单产品生成的完整流程阶段"""

    IDLE = "idle"
    ANALYZING = "analyzing"           # 图片分析
    ROUTING = "routing"               # Gem 路由匹配
    PROMPT_GENERATING = "prompt_generating"  # 提示词生成
    VIDEO_GENERATING = "video_generating"    # 视频生成
    DELIVERING = "delivering"         # 结果交付
    COMPLETED = "completed"           # 完成
    FAILED = "failed"                 # 失败
    CANCELLED = "cancelled"           # 已取消


class TaskStatus(str, Enum):
    """任务状态枚举 — 统一所有子模块的状态表示"""

    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class BatchStatus(str, Enum):
    """批次状态枚举"""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProductCategory(str, Enum):
    """产品品类 — 一级类目（27个品类）"""

    # 美妆个护
    SKINCARE = "skincare"
    MAKEUP = "makeup"
    FRAGRANCE = "fragrance"
    PERSONAL_CARE = "personal_care"
    # 服饰时尚
    WOMENS_CLOTHING = "womens_clothing"
    MENS_CLOTHING = "mens_clothing"
    SHOES = "shoes"
    BAGS = "bags"
    ACCESSORIES = "accessories"
    # 数码家电
    PHONE = "phone"
    COMPUTER = "computer"
    DIGITAL_ACCESSORY = "digital_accessory"
    HOME_APPLIANCE = "home_appliance"
    # 食品生鲜
    SNACK = "snack"
    BEVERAGE = "beverage"
    FRESH_FOOD = "fresh_food"
    HEALTH_FOOD = "health_food"
    # 家居生活
    FURNITURE = "furniture"
    HOME_DECOR = "home_decor"
    DAILY_NECESSITIES = "daily_necessities"
    # 母婴宠物
    MATERNITY_BABY = "maternity_baby"
    PET = "pet"
    # 运动户外
    SPORTS = "sports"
    OUTDOOR = "outdoor"
    # 珠宝钟表
    JEWELRY = "jewelry"
    WATCH = "watch"
    # 其他
    OTHER = "other"


class PriceTier(str, Enum):
    """价格带 — 5 级离散"""

    BUDGET = "budget"           # 0-50 元
    AFFORDABLE = "affordable"   # 50-200 元
    MID_RANGE = "mid_range"     # 200-1000 元
    PREMIUM = "premium"         # 1000-5000 元
    LUXURY = "luxury"           # 5000+ 元


class BrandTier(str, Enum):
    """品牌调性 — 5 级"""

    MASS = "mass"               # 大众品牌
    MID_MARKET = "mid_market"   # 中端品牌
    PREMIUM = "premium"         # 高端品牌
    LUXURY = "luxury"           # 奢侈品牌
    D2C = "d2c"                 # 新锐/白牌


class TargetGender(str, Enum):
    """目标性别"""

    MALE = "male"
    FEMALE = "female"
    UNISEX = "unisex"
    NOT_SPECIFIED = "not_specified"


class TargetAgeGroup(str, Enum):
    """目标年龄层"""

    GEN_Z = "gen_z"         # 18-25岁
    GEN_Y = "gen_y"         # 26-35岁
    GEN_X = "gen_x"         # 36-45岁
    MIDDLE_AGE = "middle_age"  # 46-55岁
    SENIOR = "senior"       # 55岁以上
    ALL_AGES = "all_ages"   # 全年龄


class VideoProvider(str, Enum):
    """视频生成提供商枚举"""

    SEEDANCE = "seedance"      # 即梦 (火山引擎)
    KLING = "kling"            # 可灵 (快手)
    VIDU = "vidu"              # 生数 Vidu
    VEO3 = "veo3"              # Google Veo 3
    RUNWAY = "runway"          # Runway ML
    PIKA = "pika"              # Pika Labs
    KUAIZI = "kuaizi"          # 丽帧 / 快子


class VideoAspectRatio(str, Enum):
    """视频宽高比枚举"""

    RATIO_16_9 = "16:9"
    RATIO_9_16 = "9:16"
    RATIO_1_1 = "1:1"
    RATIO_4_3 = "4:3"
    RATIO_3_4 = "3:4"
    RATIO_21_9 = "21:9"


class VideoResolution(str, Enum):
    """视频分辨率枚举"""

    P480 = "480p"
    P720 = "720p"
    P1080 = "1080p"
    P4K = "4k"


class RoutingStrategy(str, Enum):
    """路由策略枚举"""

    BALANCED = "balanced"
    SPEED_FIRST = "speed_first"
    QUALITY_FIRST = "quality_first"
    COST_FIRST = "cost_first"
    CHARACTER_FIRST = "character_first"
    MANUAL = "manual"


class ErrorCategory(str, Enum):
    """错误分类枚举"""

    CLIENT = "client"
    SERVER = "server"
    NETWORK = "network"
    TASK = "task"
    RESOURCE = "resource"
    CONFIGURATION = "config"
    SYSTEM = "system"


class NotificationLevel(str, Enum):
    """通知级别枚举"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LogLevel(str, Enum):
    """日志级别枚举"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


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


class CostTier(str, Enum):
    """视频提供商成本层级枚举"""

    PREMIUM = "premium"
    STANDARD = "standard"
    BUDGET = "budget"


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


class DeliveryStatus(str, Enum):
    """交付任务生命周期状态"""

    PENDING = "pending"
    COLLECTING = "collecting"
    ORGANIZING = "organizing"
    UPLOADING = "uploading"
    REPORTING = "reporting"
    NOTIFYING = "notifying"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HealthStatus(str, Enum):
    """健康检查状态"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


# ═══════════════════════════════════════════════════════════
# 2. 类型别名
# ═══════════════════════════════════════════════════════════

T = TypeVar("T")
JsonDict = Dict[str, Any]
PathLike = Union[str, Path]
GemId = str
Tag = str
ProgressCallback = Callable[[int, int, str], None]
AsyncCallback = Callable[..., Coroutine[Any, Any, None]]


# ═══════════════════════════════════════════════════════════
# 3. 输入模型
# ═══════════════════════════════════════════════════════════


class ImageInput(BaseModel):
    """图片输入模型

    支持多种图片来源: 本地路径、URL、bytes 数据。
    """

    model_config = ConfigDict(validate_assignment=True)

    source_type: Literal["path", "url", "bytes"] = Field(
        default="path",
        description="图片来源类型",
    )
    source: Union[str, bytes] = Field(
        ...,
        description="图片数据 (路径/URL/bytes)",
    )
    mime_type: Optional[str] = Field(
        default=None,
        description="MIME 类型 (如 image/jpeg)",
    )
    filename: Optional[str] = Field(
        default=None,
        description="原始文件名",
    )

    @field_validator("source")
    @classmethod
    def validate_source(cls: type["ImageInput"], v: Any, info: Any) -> Any:
        """校验 source 与 source_type 的一致性"""
        data = info.data
        source_type = data.get("source_type", "path")
        if source_type in ("path", "url") and not isinstance(v, str):
            raise ValueError(f"source_type={source_type} 时 source 必须为字符串")
        if source_type == "bytes" and not isinstance(v, bytes):
            raise ValueError("source_type='bytes' 时 source 必须为 bytes")
        return v

    @classmethod
    def from_path(cls: type["ImageInput"], path: PathLike) -> ImageInput:
        """从本地文件路径创建"""
        p = Path(path)
        return cls(
            source_type="path",
            source=str(path),
            filename=p.name,
        )

    @classmethod
    def from_url(cls: type["ImageInput"], url: str) -> ImageInput:
        """从 URL 创建"""
        return cls(source_type="url", source=url)

    @classmethod
    def from_bytes(cls: type["ImageInput"], data: bytes, mime_type: str = "image/jpeg") -> ImageInput:
        """从 bytes 数据创建"""
        return cls(source_type="bytes", source=data, mime_type=mime_type)


class SingleProcessingRequest(BaseModel):
    """单产品处理请求

    调用 SkillController.process_single() 时使用的请求模型。
    """

    model_config = ConfigDict(validate_assignment=True)

    # ── 必填 ──
    image: ImageInput = Field(..., description="产品图片输入")

    # ── 可选业务参数 ──
    product_description: Optional[str] = Field(
        default=None,
        description="产品文字描述 (辅助分析)",
    )
    preferred_gem_id: Optional[str] = Field(
        default=None,
        description="指定 Gem ID (跳过路由)",
    )
    preferred_provider: Optional[VideoProvider] = Field(
        default=None,
        description="指定视频提供商",
    )
    extra_requirements: Optional[str] = Field(
        default=None,
        description="额外生成要求",
    )

    # ── 视频参数 ──
    aspect_ratio: VideoAspectRatio = Field(
        default=VideoAspectRatio.RATIO_9_16,
        description="视频宽高比",
    )
    resolution: VideoResolution = Field(
        default=VideoResolution.P1080,
        description="视频分辨率",
    )
    duration_seconds: Optional[int] = Field(
        default=None,
        description="目标时长 (秒)",
    )

    # ── 控制参数 ──
    output_dir: Optional[str] = Field(
        default=None,
        description="输出目录 (None 则使用配置默认值)",
    )
    skip_delivery: bool = Field(
        default=False,
        description="是否跳过交付步骤",
    )


class BatchProcessingRequest(BaseModel):
    """批量处理请求

    调用 SkillController.process_batch() 时使用的请求模型。
    """

    model_config = ConfigDict(validate_assignment=True)

    # ── 必填 ──
    image_paths: List[str] = Field(
        ...,
        description="图片路径列表",
        min_length=1,
    )

    # ── 批次信息 ──
    batch_name: Optional[str] = Field(
        default=None,
        description="批次名称",
    )
    product_description: Optional[str] = Field(
        default=None,
        description="产品文字描述 (辅助分析)",
    )

    # ── 视频参数 ──
    aspect_ratio: VideoAspectRatio = Field(
        default=VideoAspectRatio.RATIO_9_16,
        description="视频宽高比",
    )
    resolution: VideoResolution = Field(
        default=VideoResolution.P1080,
        description="视频分辨率",
    )

    # ── 并发控制 ──
    concurrency: int = Field(
        default=3,
        description="并发数",
        ge=1,
        le=20,
    )

    # ── 交付配置 ──
    output_dir: Optional[str] = Field(
        default=None,
        description="输出目录",
    )
    skip_delivery: bool = Field(
        default=False,
        description="是否跳过交付",
    )

    @field_validator("image_paths")
    @classmethod
    def validate_paths(cls: type["BatchProcessingRequest"], v: List[str]) -> List[str]:
        """校验至少有一个图片路径"""
        if not v:
            raise ValueError("image_paths 不能为空列表")
        # 过滤重复路径
        seen = set()
        unique = []
        for p in v:
            abs_p = str(Path(p).absolute())
            if abs_p not in seen:
                seen.add(abs_p)
                unique.append(abs_p)
        return unique


class RetryRequest(BaseModel):
    """重试请求"""

    model_config = ConfigDict(validate_assignment=True)

    batch_id: Optional[str] = Field(
        default=None,
        description="批次 ID (重试整个批次中的失败任务)",
    )
    task_ids: Optional[List[str]] = Field(
        default=None,
        description="指定任务 ID 列表",
    )
    max_retries: int = Field(
        default=3,
        description="最大重试次数",
        ge=1,
        le=10,
    )


# ═══════════════════════════════════════════════════════════
# 4. 输出模型
# ═══════════════════════════════════════════════════════════


class ImageFeatures(BaseModel):
    """图片特征分析结果

    4 维度分析输出: 产品 / 人群 / 场景 / 风格
    """

    model_config = ConfigDict(validate_assignment=True)

    # ── 产品维度 ──
    product_category: Optional[ProductCategory] = Field(
        default=None,
        description="产品品类",
    )
    product_subcategory: Optional[str] = Field(
        default=None,
        description="产品子品类",
    )
    price_tier: Optional[PriceTier] = Field(
        default=None,
        description="价格带",
    )
    brand_tier: Optional[BrandTier] = Field(
        default=None,
        description="品牌调性",
    )
    dominant_colors: List[str] = Field(
        default_factory=list,
        description="主色调 (HEX 列表)",
    )
    visual_style: Optional[str] = Field(
        default=None,
        description="视觉风格",
    )

    # ── 人群维度 ──
    target_gender: Optional[TargetGender] = Field(
        default=None,
        description="目标性别",
    )
    target_age: Optional[TargetAgeGroup] = Field(
        default=None,
        description="目标年龄层",
    )

    # ── 场景维度 ──
    usage_scene: Optional[str] = Field(
        default=None,
        description="使用场景",
    )
    marketing_scene: Optional[str] = Field(
        default=None,
        description="营销场景",
    )

    # ── 风格维度 ──
    video_style: Optional[str] = Field(
        default=None,
        description="视频风格偏好",
    )
    mood: Optional[str] = Field(
        default=None,
        description="情绪基调",
    )
    composition: Optional[str] = Field(
        default=None,
        description="构图偏好",
    )

    # ── 置信度 ──
    confidence_score: float = Field(
        default=0.0,
        description="整体置信度 (0-1)",
        ge=0.0,
        le=1.0,
    )
    completeness_score: float = Field(
        default=0.0,
        description="信息完整度 (0-1)",
        ge=0.0,
        le=1.0,
    )
    reliability: Optional[str] = Field(
        default=None,
        description="可靠性评估",
    )

    @model_validator(mode="after")
    def check_confidence(self: "ImageFeatures") -> ImageFeatures:
        """确保置信度分数在有效范围内"""
        if self.confidence_score < 0 or self.confidence_score > 1:
            raise ValueError("confidence_score 必须在 0-1 之间")
        if self.completeness_score < 0 or self.completeness_score > 1:
            raise ValueError("completeness_score 必须在 0-1 之间")
        return self


class GemMatch(BaseModel):
    """单个 Gem 匹配结果"""

    model_config = ConfigDict(validate_assignment=True)

    gem_id: str = Field(..., description="Gem ID")
    gem_name: str = Field(..., description="Gem 名称")
    score: float = Field(
        ...,
        description="匹配分数 (0-1)",
        ge=0.0,
        le=1.0,
    )
    breakdown: Dict[str, float] = Field(
        default_factory=dict,
        description="维度评分明细",
    )
    is_fallback: bool = Field(
        default=False,
        description="是否为降级匹配",
    )


class GemMatchResult(BaseModel):
    """Gem 路由匹配结果"""

    model_config = ConfigDict(validate_assignment=True)

    matches: List[GemMatch] = Field(
        default_factory=list,
        description="排序后的匹配列表",
    )
    top_match: Optional[GemMatch] = Field(
        default=None,
        description="最佳匹配",
    )
    confidence_level: str = Field(
        default="low",
        description="置信度级别 (high/medium/low)",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="匹配理由说明",
    )

    @model_validator(mode="after")
    def set_top_match(self: "GemMatchResult") -> GemMatchResult:
        """自动设置 top_match 为 matches 列表的第一个元素"""
        if self.matches and self.top_match is None:
            self.top_match = self.matches[0]
        return self


class PromptResult(BaseModel):
    """提示词生成结果"""

    model_config = ConfigDict(validate_assignment=True)

    prompt: str = Field(..., description="生成的视频提示词")
    gem_used: str = Field(..., description="使用的 Gem ID")
    model_used: Optional[str] = Field(
        default=None,
        description="使用的模型名称",
    )
    is_valid: bool = Field(
        default=True,
        description="是否通过校验",
    )
    validation: Dict[str, Any] = Field(
        default_factory=dict,
        description="校验详情",
    )
    negative_prompt: Optional[str] = Field(
        default=None,
        description="负面提示词",
    )


class VideoGenerationResult(BaseModel):
    """视频生成结果"""

    model_config = ConfigDict(validate_assignment=True)

    task_id: str = Field(..., description="视频生成任务 ID")
    provider: VideoProvider = Field(..., description="视频提供商")
    status: TaskStatus = Field(..., description="任务状态")
    video_url: Optional[str] = Field(
        default=None,
        description="视频 URL",
    )
    video_local_path: Optional[str] = Field(
        default=None,
        description="本地视频路径",
    )
    cost_usd: float = Field(
        default=0.0,
        description="费用 (USD)",
    )
    generation_time_sec: float = Field(
        default=0.0,
        description="生成耗时 (秒)",
    )


class SingleProcessingResult(BaseModel):
    """单产品处理结果

    SkillController.process_single() 的完整输出。
    """

    model_config = ConfigDict(validate_assignment=True)

    # ── 标识 ──
    task_id: str = Field(
        default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}",
        description="任务 ID",
    )
    status: ProcessingStage = Field(
        default=ProcessingStage.IDLE,
        description="处理状态",
    )

    # ── 分析结果 ──
    image_features: Optional[ImageFeatures] = Field(
        default=None,
        description="图片特征分析结果",
    )

    # ── 路由结果 ──
    matched_gem_id: Optional[str] = Field(
        default=None,
        description="匹配的 Gem ID",
    )
    matched_gem_name: Optional[str] = Field(
        default=None,
        description="匹配的 Gem 名称",
    )
    match_score: Optional[float] = Field(
        default=None,
        description="匹配分数",
    )

    # ── 提示词 ──
    generated_prompt: Optional[str] = Field(
        default=None,
        description="生成的视频提示词",
    )

    # ── 视频结果 ──
    video_url: Optional[str] = Field(
        default=None,
        description="视频 URL",
    )
    video_local_path: Optional[str] = Field(
        default=None,
        description="本地视频文件路径",
    )
    video_provider: Optional[VideoProvider] = Field(
        default=None,
        description="视频提供商",
    )
    cost_usd: float = Field(
        default=0.0,
        description="总费用 (USD)",
    )
    generation_time_sec: float = Field(
        default=0.0,
        description="生成耗时 (秒)",
    )

    # ── 错误 ──
    error_message: Optional[str] = Field(
        default=None,
        description="错误信息 (失败时)",
    )
    error_category: Optional[ErrorCategory] = Field(
        default=None,
        description="错误分类",
    )
    failed_stage: Optional[ProcessingStage] = Field(
        default=None,
        description="失败的阶段",
    )

    # ── 元数据 ──
    started_at: datetime = Field(
        default_factory=datetime.now,
        description="开始时间",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="完成时间",
    )

    @property
    def is_success(self: "SingleProcessingResult") -> bool:
        """判断处理是否成功"""
        return self.status == ProcessingStage.COMPLETED

    @property
    def is_failed(self: "SingleProcessingResult") -> bool:
        """判断处理是否失败"""
        return self.status == ProcessingStage.FAILED

    def to_summary(self: "SingleProcessingResult") -> Dict[str, Any]:
        """生成摘要字典"""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "success": self.is_success,
            "gem": self.matched_gem_name or "N/A",
            "provider": self.video_provider.value if self.video_provider else "N/A",
            "cost_usd": round(self.cost_usd, 4),
            "time_sec": round(self.generation_time_sec, 1),
            "video_path": self.video_local_path or "N/A",
        }


class BatchTaskItem(BaseModel):
    """批次中的单个任务项"""

    model_config = ConfigDict(validate_assignment=True)

    task_id: str = Field(
        default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}",
        description="任务 ID",
    )
    image_path: str = Field(..., description="图片路径")
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        description="任务状态",
    )
    result: Optional[SingleProcessingResult] = Field(
        default=None,
        description="处理结果",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="错误信息",
    )


class BatchProcessingResult(BaseModel):
    """批量处理结果"""

    model_config = ConfigDict(validate_assignment=True)

    # ── 标识 ──
    batch_id: str = Field(
        default_factory=lambda: f"batch_{uuid.uuid4().hex[:12]}",
        description="批次 ID",
    )
    batch_name: Optional[str] = Field(
        default=None,
        description="批次名称",
    )
    status: BatchStatus = Field(
        default=BatchStatus.CREATED,
        description="批次状态",
    )

    # ── 任务列表 ──
    tasks: List[BatchTaskItem] = Field(
        default_factory=list,
        description="所有任务",
    )

    # ── 统计 ──
    total: int = Field(default=0, description="总任务数")
    completed: int = Field(default=0, description="已完成数")
    failed: int = Field(default=0, description="失败数")
    cancelled: int = Field(default=0, description="已取消数")

    # ── 时间 ──
    started_at: Optional[datetime] = Field(
        default=None,
        description="开始时间",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="完成时间",
    )

    # ── 汇总 ──
    total_cost_usd: float = Field(
        default=0.0,
        description="总费用 (USD)",
    )

    @property
    def progress_percent(self: "BatchProcessingResult") -> float:
        """计算进度百分比"""
        if self.total == 0:
            return 0.0
        return round(((self.completed + self.failed) / self.total) * 100, 1)

    @property
    def is_complete(self: "BatchProcessingResult") -> bool:
        """判断批次是否已完成"""
        # 空批次视为已完成
        if self.total == 0:
            return True
        return self.status in (
            BatchStatus.COMPLETED,
            BatchStatus.PARTIAL,
            BatchStatus.FAILED,
            BatchStatus.CANCELLED,
        )

    def to_summary(self: "BatchProcessingResult") -> Dict[str, Any]:
        """生成摘要字典"""
        return {
            "batch_id": self.batch_id,
            "batch_name": self.batch_name,
            "status": self.status.value,
            "progress": f"{self.progress_percent}%",
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "cost_usd": round(self.total_cost_usd, 4),
        }


class ProgressSnapshot(BaseModel):
    """进度快照"""

    model_config = ConfigDict(validate_assignment=True)

    batch_id: str = Field(..., description="批次 ID")
    total: int = Field(default=0, description="总任务数")
    completed: int = Field(default=0, description="已完成")
    failed: int = Field(default=0, description="失败数")
    pending: int = Field(default=0, description="待处理")
    processing: int = Field(default=0, description="处理中")
    percent: float = Field(default=0.0, description="百分比")
    eta_seconds: Optional[float] = Field(
        default=None,
        description="预计剩余时间 (秒)",
    )
    throughput: float = Field(
        default=0.0,
        description="吞吐率 (任务/秒)",
    )
    is_complete: bool = Field(
        default=False,
        description="是否完成",
    )
    total_cost_usd: float = Field(
        default=0.0,
        description="总费用",
    )


class HealthCheckResult(BaseModel):
    """健康检查结果"""

    model_config = ConfigDict(validate_assignment=True)

    component: str = Field(..., description="组件名称")
    status: HealthStatus = Field(..., description="健康状态")
    latency_ms: float = Field(
        default=0.0,
        description="延迟 (毫秒)",
    )
    message: Optional[str] = Field(
        default=None,
        description="状态说明",
    )
    details: Dict[str, Any] = Field(
        default_factory=dict,
        description="详细信息",
    )


class SystemHealthReport(BaseModel):
    """系统健康报告"""

    model_config = ConfigDict(validate_assignment=True)

    overall: HealthStatus = Field(
        default=HealthStatus.UNKNOWN,
        description="整体状态",
    )
    gemini: Optional[HealthCheckResult] = Field(
        default=None,
        description="Gemini 服务状态",
    )
    video_providers: Dict[str, HealthCheckResult] = Field(
        default_factory=dict,
        description="视频提供商状态",
    )
    workspace: Optional[HealthCheckResult] = Field(
        default=None,
        description="Workspace 状态",
    )
    checked_at: datetime = Field(
        default_factory=datetime.now,
        description="检查时间",
    )

    @property
    def is_healthy(self: "SystemHealthReport") -> bool:
        """判断整体是否健康"""
        return self.overall == HealthStatus.HEALTHY


class GemInfo(BaseModel):
    """Gem 信息模型"""

    model_config = ConfigDict(validate_assignment=True)

    gem_id: str = Field(..., description="Gem ID")
    name: str = Field(..., description="Gem 名称")
    category: Optional[str] = Field(
        default=None,
        description="所属品类",
    )
    description: Optional[str] = Field(
        default=None,
        description="描述",
    )
    tags: List[str] = Field(
        default_factory=list,
        description="标签列表",
    )
    is_active: bool = Field(
        default=True,
        description="是否激活",
    )
    usage_count: int = Field(
        default=0,
        description="使用次数",
    )
    success_rate: float = Field(
        default=0.0,
        description="成功率",
        ge=0.0,
        le=1.0,
    )


class DeliveryResult(BaseModel):
    """交付结果"""

    model_config = ConfigDict(validate_assignment=True)

    status: DeliveryStatus = Field(..., description="交付状态")
    drive_folder_id: Optional[str] = Field(
        default=None,
        description="Drive 文件夹 ID",
    )
    drive_folder_url: Optional[str] = Field(
        default=None,
        description="Drive 文件夹 URL",
    )
    uploaded_files: List[str] = Field(
        default_factory=list,
        description="已上传文件列表",
    )
    report_url: Optional[str] = Field(
        default=None,
        description="报告 URL",
    )
    email_sent: bool = Field(
        default=False,
        description="邮件是否已发送",
    )
    errors: List[str] = Field(
        default_factory=list,
        description="错误信息列表",
    )

    def to_summary(self: "DeliveryResult") -> Dict[str, Any]:
        """生成摘要"""
        return {
            "status": self.status.value,
            "drive_folder": self.drive_folder_url or "N/A",
            "uploaded_count": len(self.uploaded_files),
            "report": self.report_url or "N/A",
            "email_sent": self.email_sent,
            "errors": self.errors,
        }


# ═══════════════════════════════════════════════════════════
# 5. 配置模型
# ═══════════════════════════════════════════════════════════


class GeminiConfig(BaseModel):
    """Gemini 服务配置"""

    model_config = ConfigDict(validate_assignment=True)

    api_key: Optional[str] = Field(
        default=None,
        description="API Key",
    )
    cookie_1psid: Optional[str] = Field(
        default=None,
        description="1PSID Cookie",
    )
    cookie_1psidts: Optional[str] = Field(
        default=None,
        description="1PSIDTS Cookie",
    )
    proxy: Optional[str] = Field(
        default=None,
        description="代理地址",
    )
    timeout: int = Field(
        default=120,
        description="超时时间 (秒)",
        ge=10,
        le=600,
    )
    max_retries: int = Field(
        default=3,
        description="最大重试次数",
        ge=0,
        le=10,
    )


class VideoProviderConfig(BaseModel):
    """单个视频提供商配置"""

    model_config = ConfigDict(validate_assignment=True)

    enabled: bool = Field(
        default=True,
        description="是否启用",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API Key",
    )
    api_secret: Optional[str] = Field(
        default=None,
        description="API Secret",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="自定义 API 地址",
    )
    timeout: int = Field(
        default=300,
        description="超时时间 (秒)",
        ge=30,
        le=1800,
    )
    max_retries: int = Field(
        default=3,
        description="最大重试次数",
        ge=0,
        le=10,
    )
    concurrency: int = Field(
        default=2,
        description="并发数",
        ge=1,
        le=10,
    )
    cost_tier: CostTier = Field(
        default=CostTier.STANDARD,
        description="成本层级",
    )
    extra: Dict[str, Any] = Field(
        default_factory=dict,
        description="额外配置参数",
    )


class WorkspaceConfig(BaseModel):
    """Workspace 配置"""

    model_config = ConfigDict(validate_assignment=True)

    enabled: bool = Field(
        default=True,
        description="是否启用",
    )
    service_account_path: Optional[str] = Field(
        default=None,
        description="服务账号凭证路径",
    )
    spreadsheet_id: Optional[str] = Field(
        default=None,
        description="默认 Spreadsheet ID",
    )
    drive_folder_id: Optional[str] = Field(
        default=None,
        description="默认 Drive 文件夹 ID",
    )
    notification_email: Optional[str] = Field(
        default=None,
        description="通知邮箱",
    )


class SkillConfig(BaseModel):
    """Skill 全局配置

    四层配置加载后的最终合并结果。
    """

    model_config = ConfigDict(validate_assignment=True)

    # ── 版本 ──
    version: str = Field(
        default=__version__,
        description="配置版本",
    )

    # ── Gemini ──
    gemini: GeminiConfig = Field(
        default_factory=GeminiConfig,
        description="Gemini 配置",
    )

    # ── 视频提供商 ──
    video_providers: Dict[str, VideoProviderConfig] = Field(
        default_factory=dict,
        description="视频提供商配置",
    )

    # ── Workspace ──
    workspace: WorkspaceConfig = Field(
        default_factory=WorkspaceConfig,
        description="Workspace 配置",
    )

    # ── 路由 ──
    default_routing_strategy: RoutingStrategy = Field(
        default=RoutingStrategy.BALANCED,
        description="默认路由策略",
    )

    # ── 输出 ──
    output_dir: str = Field(
        default="./output",
        description="默认输出目录",
    )
    log_level: LogLevel = Field(
        default=LogLevel.INFO,
        description="日志级别",
    )

    # ── 并发 ──
    default_concurrency: int = Field(
        default=3,
        description="默认并发数",
        ge=1,
        le=20,
    )
    max_batch_size: int = Field(
        default=1000,
        description="最大批次大小",
        ge=1,
        le=10000,
    )

    @classmethod
    def from_env(cls: type["SkillConfig"]) -> SkillConfig:
        """从环境变量创建配置"""
        gemini = GeminiConfig(
            api_key=_env("GEMINI_API_KEY"),
            cookie_1psid=_env("GEMINI_1PSID"),
            cookie_1psidts=_env("GEMINI_1PSIDTS"),
            proxy=_env("GEMINI_PROXY"),
        )
        workspace = WorkspaceConfig(
            service_account_path=_env("GOOGLE_SERVICE_ACCOUNT"),
            spreadsheet_id=_env("GOOGLE_SPREADSHEET_ID"),
            drive_folder_id=_env("GOOGLE_DRIVE_FOLDER_ID"),
            notification_email=_env("NOTIFICATION_EMAIL"),
        )
        return cls(
            gemini=gemini,
            workspace=workspace,
            output_dir=_env("SKILL_OUTPUT_DIR") or "./output",
            log_level=LogLevel(_env("SKILL_LOG_LEVEL", "INFO")),
            default_concurrency=int(_env("SKILL_CONCURRENCY", "3")),
        )


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    """安全读取环境变量"""
    import os

    return os.environ.get(key, default)


# ═══════════════════════════════════════════════════════════
# 6. 事件模型
# ═══════════════════════════════════════════════════════════


class ProcessingEvent(BaseModel):
    """处理事件

    用于进度回调和事件通知。
    """

    model_config = ConfigDict(validate_assignment=True)

    event_type: str = Field(..., description="事件类型")
    task_id: Optional[str] = Field(
        default=None,
        description="关联任务 ID",
    )
    batch_id: Optional[str] = Field(
        default=None,
        description="关联批次 ID",
    )
    stage: Optional[ProcessingStage] = Field(
        default=None,
        description="当前阶段",
    )
    message: Optional[str] = Field(
        default=None,
        description="事件消息",
    )
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="附加数据",
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="事件时间",
    )

    @field_serializer("timestamp")
    def serialize_timestamp(self: "ProcessingEvent", value: datetime) -> str:
        """序列化时间戳为 ISO 格式"""
        return value.isoformat()


# ═══════════════════════════════════════════════════════════
# 7. 便利导出
# ═══════════════════════════════════════════════════════════

__all__ = [
    # 版本
    "__version__",
    "__version_info__",
    # 枚举
    "ProcessingStage",
    "TaskStatus",
    "BatchStatus",
    "ProductCategory",
    "PriceTier",
    "BrandTier",
    "TargetGender",
    "TargetAgeGroup",
    "VideoProvider",
    "VideoAspectRatio",
    "VideoResolution",
    "RoutingStrategy",
    "ErrorCategory",
    "NotificationLevel",
    "LogLevel",
    "AuthType",
    "StorageBackend",
    "CostTier",
    "GemSource",
    "SyncStatus",
    "DeliveryStatus",
    "HealthStatus",
    # 类型别名
    "JsonDict",
    "PathLike",
    "GemId",
    "Tag",
    "ProgressCallback",
    "AsyncCallback",
    # 输入模型
    "ImageInput",
    "SingleProcessingRequest",
    "BatchProcessingRequest",
    "RetryRequest",
    # 输出模型
    "ImageFeatures",
    "GemMatch",
    "GemMatchResult",
    "PromptResult",
    "VideoGenerationResult",
    "SingleProcessingResult",
    "BatchTaskItem",
    "BatchProcessingResult",
    "ProgressSnapshot",
    "HealthCheckResult",
    "SystemHealthReport",
    "GemInfo",
    "DeliveryResult",
    # 配置模型
    "GeminiConfig",
    "VideoProviderConfig",
    "WorkspaceConfig",
    "SkillConfig",
    # 事件模型
    "ProcessingEvent",
]
