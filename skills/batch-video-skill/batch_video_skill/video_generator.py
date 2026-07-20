# -*- coding: utf-8 -*-
"""
video_generator.py
视频生成子系统 - 抽象基类、注册表、配置管理与核心框架

包含:
1. 枚举类型 (Enums) - 所有视频生成相关的枚举
2. 数据模型 (Models) - Pydantic v2 数据模型
3. 异常体系 (Exceptions) - 完整的异常层次结构
4. 熔断器 (CircuitBreaker) - 状态机实现
5. 重试策略 (RetryPolicy) - 指数退避重试
6. 指标收集 (AdapterMetrics) - 运行指标
7. 抽象基类 (VideoGenerator ABC) - 所有适配器必须继承
8. 适配器注册表 (AdapterRegistry) - 注册/发现/查询
9. 配置管理 (AdapterConfigManager) - 统一配置

设计原则:
- 单一职责: 每个适配器只负责一个提供商
- 接口统一: 所有适配器对外暴露相同接口
- 内部隔离: 提供商差异在 _build_payload/_parse_response 中处理
- 可观测: 所有操作都带有结构化日志和指标
- 容错: 内置重试、熔断、降级策略

Python: 3.11+
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import logging
import pkgutil
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Coroutine,
    Dict,
    Generic,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)

import aiohttp
from pydantic import BaseModel, ConfigDict, Field


# ═══════════════════════════════════════════════════════════
# 1. 枚举类型
# ═══════════════════════════════════════════════════════════


class VideoProvider(str, Enum):
    """视频生成提供商枚举"""
    SEEDANCE = "seedance"      # 即梦 (火山引擎)
    KLING = "kling"            # 可灵 (快手)
    VIDU = "vidu"              # 生数 Vidu
    VEO3 = "veo3"              # Google Veo 3
    RUNWAY = "runway"          # Runway ML
    PIKA = "pika"              # Pika Labs
    KUAIZI = "kuaizi"          # 丽帧 / 快子


class VideoModelVersion(str, Enum):
    """视频模型版本枚举"""
    # Seedance
    SEEDANCE_2_0_STANDARD = "seedance-2.0-standard"
    SEEDANCE_2_0_FAST = "seedance-2.0-fast"
    SEEDANCE_2_0_MINI = "seedance-2.0-mini"
    SEEDANCE_1_5_PRO = "seedance-1.5-pro"
    # Kling
    KLING_3_0 = "kling-3.0"
    KLING_3_0_TURBO = "kling-3.0-turbo"
    KLING_3_0_OMNI = "kling-3.0-omni"
    # Vidu
    VIDU_Q3_PRO = "vidu-q3-pro"
    VIDU_Q3_TURBO = "vidu-q3-turbo"
    # Veo3
    VEO_3_1_STANDARD = "veo-3.1-standard"
    VEO_3_1_FAST = "veo-3.1-fast"
    VEO_3_1_LITE = "veo-3.1-lite"
    # Runway
    RUNWAY_GEN_4 = "runway-gen-4"
    RUNWAY_GEN_4_TURBO = "runway-gen-4-turbo"
    RUNWAY_GEN_4_5 = "runway-gen-4.5"
    # Pika
    PIKA_2_2 = "pika-2.2"
    # Kuaizi / Lizhen
    KUAIZI_LZ_FAST = "kuaizi-lz-fast"


class TaskStatus(str, Enum):
    """统一任务状态枚举"""
    PENDING = "pending"         # 待处理
    QUEUED = "queued"           # 排队中
    PROCESSING = "processing"   # 处理中
    SUCCEEDED = "succeeded"     # 成功
    FAILED = "failed"           # 失败
    CANCELLED = "cancelled"     # 已取消
    EXPIRED = "expired"         # 已过期


class VideoResolution(str, Enum):
    """视频分辨率枚举"""
    P480 = "480p"
    P720 = "720p"
    P1080 = "1080p"
    P4K = "4k"


class VideoAspectRatio(str, Enum):
    """视频宽高比枚举"""
    RATIO_16_9 = "16:9"
    RATIO_9_16 = "9:16"
    RATIO_1_1 = "1:1"
    RATIO_4_3 = "4:3"
    RATIO_3_4 = "3:4"
    RATIO_21_9 = "21:9"


class RoutingStrategy(str, Enum):
    """路由策略枚举"""
    BALANCED = "balanced"               # 平衡
    SPEED_FIRST = "speed_first"         # 速度优先
    QUALITY_FIRST = "quality_first"     # 质量优先
    COST_FIRST = "cost_first"           # 成本优先
    CHARACTER_FIRST = "character_first" # 人物一致性优先
    MANUAL = "manual"                   # 手动指定


class ProviderStatus(str, Enum):
    """提供商健康状态"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class CircuitState(str, Enum):
    """熔断器状态"""
    CLOSED = "closed"           # 正常状态
    OPEN = "open"               # 熔断状态
    HALF_OPEN = "half_open"     # 半开状态


class ScoreDimension(str, Enum):
    """评分维度"""
    SPEED = "speed"
    QUALITY = "quality"
    COST = "cost"
    CHARACTER = "character"
    CHINESE = "chinese"
    CAPABILITY = "capability"


class RuleLevel(str, Enum):
    """规则级别"""
    MANDATORY = "mandatory"
    RECOMMENDED = "recommended"
    INFO = "info"


# ═══════════════════════════════════════════════════════════
# 2. 数据模型 (Pydantic v2)
# ═══════════════════════════════════════════════════════════


class VideoGenerationRequest(BaseModel):
    """统一的视频生成请求"""
    model_config = ConfigDict(extra="allow")

    prompt: str = Field(..., description="文本提示词")
    negative_prompt: Optional[str] = Field(None, description="负面提示词")
    resolution: VideoResolution = Field(VideoResolution.P720)
    aspect_ratio: VideoAspectRatio = Field(VideoAspectRatio.RATIO_16_9)
    duration: int = Field(5, ge=1, le=16)
    fps: Optional[int] = Field(None)
    generate_audio: bool = Field(True)
    enhance_prompt: bool = Field(False)
    routing_strategy: RoutingStrategy = Field(RoutingStrategy.BALANCED)
    max_budget_usd: Optional[float] = Field(None)
    max_wait_time_sec: Optional[int] = Field(None)

    # 输入参考
    first_frame: Optional[str] = Field(None, description="首帧图片 URL/路径")
    last_frame: Optional[str] = Field(None, description="尾帧图片 URL/路径")
    reference_images: List[str] = Field(default_factory=list)
    reference_videos: List[str] = Field(default_factory=list)
    reference_audios: List[str] = Field(default_factory=list)

    # 手动指定
    preferred_provider: Optional[VideoProvider] = Field(None)
    preferred_model: Optional[VideoModelVersion] = Field(None)
    fallback_providers: List[VideoProvider] = Field(default_factory=list)

    # 扩展参数
    extra_params: Dict[str, Any] = Field(default_factory=dict)


class VideoTask(BaseModel):
    """统一视频任务对象"""
    model_config = ConfigDict(extra="allow")

    task_id: str = Field(..., description="任务唯一标识")
    provider: VideoProvider = Field(...)
    model_version: VideoModelVersion = Field(...)
    status: TaskStatus = Field(TaskStatus.PENDING)
    prompt: str = Field("")
    video_url: Optional[str] = Field(None)
    progress_pct: float = Field(0.0, ge=0.0, le=100.0)
    error_code: Optional[str] = Field(None)
    error_message: Optional[str] = Field(None)
    created_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = Field(None)
    generation_time_sec: Optional[float] = Field(None)
    cost_usd: float = Field(0.0)

    def is_finished(self: "VideoTask") -> bool:
        """任务是否已完成"""
        return self.status in (
            TaskStatus.SUCCEEDED, TaskStatus.FAILED,
            TaskStatus.CANCELLED, TaskStatus.EXPIRED,
        )

    def is_success(self: "VideoTask") -> bool:
        """任务是否成功"""
        return self.status == TaskStatus.SUCCEEDED


class PricingInfo(BaseModel):
    """价格信息"""
    model_config = ConfigDict(extra="allow")

    provider: VideoProvider = Field(...)
    model_version: VideoModelVersion = Field(...)
    cost_per_second_usd: float = Field(0.0)
    cost_per_request_usd: float = Field(0.0)
    currency: str = Field("USD")


class ProviderCapability(BaseModel):
    """提供商能力声明"""
    model_config = ConfigDict(extra="allow")

    provider: VideoProvider = Field(...)
    supported_models: List[VideoModelVersion] = Field(default_factory=list)
    max_duration: int = Field(5)
    max_reference_images: int = Field(0)
    supports_4k: bool = Field(False)
    supports_60fps: bool = Field(False)
    supports_reference_videos: bool = Field(False)
    supports_reference_audios: bool = Field(False)
    supports_last_frame: bool = Field(False)
    supports_audio_generation: bool = Field(False)
    supports_negative_prompt: bool = Field(False)
    supports_enhance_prompt: bool = Field(False)


class ProviderHealth(BaseModel):
    """提供商健康状态"""
    model_config = ConfigDict(extra="allow")

    provider: VideoProvider = Field(...)
    status: str = Field("unknown")
    latency_ms: float = Field(0.0)
    last_check_at: Optional[float] = Field(None)
    error_message: Optional[str] = Field(None)


class RoutingDecision(BaseModel):
    """路由决策结果"""
    model_config = ConfigDict(extra="allow")

    primary_provider: VideoProvider = Field(...)
    primary_model: VideoModelVersion = Field(...)
    fallback_chain: List[VideoProvider] = Field(default_factory=list)
    reason: str = Field("")
    estimated_cost_usd: float = Field(0.0)
    estimated_time_sec: float = Field(0.0)


class AdapterConfig(BaseModel):
    """单个适配器的配置"""
    model_config = ConfigDict(extra="allow")

    provider: VideoProvider = Field(...)
    api_keys: List[str] = Field(default_factory=list)
    base_url: Optional[str] = Field(None)
    timeout: float = Field(120.0)
    max_retries: int = Field(3)
    enabled: bool = Field(True)
    extra: Dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# 3. 异常体系
# ═══════════════════════════════════════════════════════════


class VideoGenerationError(Exception):
    """视频生成子系统基础异常"""
    def __init__(self: "VideoGenerationError", message: str, code: str = "UNKNOWN", details: Optional[Dict[str, Any]] = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


class AdapterClosedError(VideoGenerationError):
    """适配器已关闭"""
    def __init__(self: "AdapterClosedError", message: str = "适配器已关闭") -> None:
        super().__init__(message, code="ADAPTER_CLOSED")


class AdapterNotFoundError(VideoGenerationError):
    """适配器未找到"""
    def __init__(self: "AdapterNotFoundError", message: str = "适配器未找到") -> None:
        super().__init__(message, code="ADAPTER_NOT_FOUND")


class DuplicateAdapterError(VideoGenerationError):
    """重复注册适配器"""
    def __init__(self: "DuplicateAdapterError", message: str = "适配器已存在") -> None:
        super().__init__(message, code="DUPLICATE_ADAPTER")


class CircuitBreakerOpenError(VideoGenerationError):
    """熔断器已打开"""
    def __init__(self: "CircuitBreakerOpenError", message: str = "熔断器已打开", retry_after_sec: float = 60.0) -> None:
        self.retry_after_sec = retry_after_sec
        super().__init__(message, code="CIRCUIT_BREAKER_OPEN")


class NetworkError(VideoGenerationError):
    """网络错误"""
    def __init__(self: "NetworkError", message: str = "网络错误", url: Optional[str] = None, provider: Optional[str] = None) -> None:
        super().__init__(message, code="NETWORK_ERROR", details={"url": url, "provider": provider})


class RateLimitError(VideoGenerationError):
    """限流错误"""
    def __init__(self: "RateLimitError", message: str = "请求被限流", retry_after_sec: float = 60.0, provider: Optional[str] = None) -> None:
        self.retry_after_sec = retry_after_sec
        super().__init__(message, code="RATE_LIMIT", details={"retry_after_sec": retry_after_sec, "provider": provider})


class QuotaExceededError(VideoGenerationError):
    """配额超限"""
    def __init__(self: "QuotaExceededError", message: str = "配额超限", provider: Optional[str] = None, api_key_preview: Optional[str] = None) -> None:
        super().__init__(message, code="QUOTA_EXCEEDED", details={"provider": provider, "api_key_preview": api_key_preview})


class SubmissionError(VideoGenerationError):
    """提交错误"""
    def __init__(self: "SubmissionError", message: str = "提交失败", status_code: int = 0, provider: Optional[str] = None) -> None:
        self.status_code = status_code
        super().__init__(message, code="SUBMISSION_ERROR", details={"status_code": status_code, "provider": provider})


class PollError(VideoGenerationError):
    """轮询错误"""
    def __init__(self: "PollError", message: str = "轮询失败") -> None:
        super().__init__(message, code="POLL_ERROR")


class TaskTimeoutError(VideoGenerationError):
    """任务超时"""
    def __init__(self: "TaskTimeoutError", message: str = "任务超时", task_id: Optional[str] = None) -> None:
        super().__init__(message, code="TASK_TIMEOUT", details={"task_id": task_id})


class TaskFailedError(VideoGenerationError):
    """任务失败"""
    def __init__(self: "TaskFailedError", message: str = "任务失败", task_id: Optional[str] = None, error_code: Optional[str] = None) -> None:
        super().__init__(message, code="TASK_FAILED", details={"task_id": task_id, "error_code": error_code})


class TaskCancelledError(VideoGenerationError):
    """任务被取消"""
    def __init__(self: "TaskCancelledError", message: str = "任务已取消", task_id: Optional[str] = None) -> None:
        super().__init__(message, code="TASK_CANCELLED", details={"task_id": task_id})


class TaskNotFoundError(VideoGenerationError):
    """任务不存在"""
    def __init__(self: "TaskNotFoundError", message: str = "任务不存在", task_id: Optional[str] = None) -> None:
        super().__init__(message, code="TASK_NOT_FOUND", details={"task_id": task_id})


class UnsupportedRequestError(VideoGenerationError):
    """不支持的请求"""
    def __init__(self: "UnsupportedRequestError", message: str = "请求不被支持") -> None:
        super().__init__(message, code="UNSUPPORTED_REQUEST")


class NoSuitableProviderError(VideoGenerationError):
    """没有合适的提供商"""
    def __init__(self: "NoSuitableProviderError", message: str = "没有满足条件的提供商", rules_applied: Optional[List[str]] = None) -> None:
        super().__init__(message, code="NO_SUITABLE_PROVIDER", details={"rules_applied": rules_applied})


class ManualProviderNotAvailableError(VideoGenerationError):
    """手动指定的提供商不可用"""
    def __init__(self: "ManualProviderNotAvailableError", message: str = "手动指定的提供商不可用") -> None:
        super().__init__(message, code="MANUAL_PROVIDER_UNAVAILABLE")


class BudgetExceededError(VideoGenerationError):
    """预算超限"""
    def __init__(self: "BudgetExceededError", message: str = "预算超限") -> None:
        super().__init__(message, code="BUDGET_EXCEEDED")


# ═══════════════════════════════════════════════════════════
# 4. 熔断器
# ═══════════════════════════════════════════════════════════


@dataclass
class CircuitBreakerConfig:
    """熔断器配置"""
    failure_threshold: int = 5
    recovery_timeout_sec: float = 300.0
    half_open_max_calls: int = 2
    success_threshold_half_open: int = 1


class CircuitBreaker:
    """
    熔断器实现

    状态转换:
        CLOSED --(连续失败达到阈值)--> OPEN
        OPEN --(超时)--> HALF_OPEN
        HALF_OPEN --(成功达到阈值)--> CLOSED
        HALF_OPEN --(再次失败)--> OPEN
    """

    def __init__(self: "CircuitBreaker", config: Optional[CircuitBreakerConfig] = None) -> None:
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

    async def call(self: "CircuitBreaker", fn: Callable[..., Coroutine[Any, Any, T]], *args: Any, **kwargs: Any) -> T:
        """在熔断器保护下执行异步函数"""
        async with self._lock:
            await self._transition_state()

            if self.state == CircuitState.OPEN:
                remaining = self.config.recovery_timeout_sec - (time.time() - self._last_failure_time)
                raise CircuitBreakerOpenError(
                    f"熔断器已打开，请 {max(0, remaining):.0f} 秒后重试",
                    retry_after_sec=max(0, remaining),
                )

            if self.state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitBreakerOpenError(
                        "半开状态试探请求数已达上限",
                        retry_after_sec=self.config.recovery_timeout_sec,
                    )
                self._half_open_calls += 1

        try:
            result = await fn(*args, **kwargs)
            await self._on_success()
            return result
        except Exception:
            await self._on_failure()
            raise

    async def _transition_state(self: "CircuitBreaker") -> None:
        """根据时间自动转换状态"""
        if self.state == CircuitState.OPEN:
            elapsed = time.time() - self._last_failure_time if self._last_failure_time else float("inf")
            if elapsed >= self.config.recovery_timeout_sec:
                self.state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self._success_count = 0

    async def _on_success(self: "CircuitBreaker") -> None:
        """记录成功"""
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold_half_open:
                    self.state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._half_open_calls = 0
            else:
                self._failure_count = max(0, self._failure_count - 1)

    async def _on_failure(self: "CircuitBreaker") -> None:
        """记录失败"""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
            elif self._failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN

    def get_state(self: "CircuitBreaker") -> Dict[str, Any]:
        """获取当前状态信息"""
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "half_open_calls": self._half_open_calls,
            "last_failure_time": self._last_failure_time,
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout_sec": self.config.recovery_timeout_sec,
                "half_open_max_calls": self.config.half_open_max_calls,
            },
        }


# ═══════════════════════════════════════════════════════════
# 5. 重试策略
# ═══════════════════════════════════════════════════════════


@dataclass
class RetryPolicy:
    """重试策略配置"""
    max_retries: int = 3
    base_delay_sec: float = 1.0
    max_delay_sec: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    retryable_exceptions: Tuple[Type[Exception], ...] = field(
        default_factory=lambda: (
            NetworkError,
            RateLimitError,
            asyncio.TimeoutError,
            aiohttp.ClientError,
        )
    )

    def compute_delay(self: "RetryPolicy", attempt: int) -> float:
        """计算第 attempt 次重试的延迟时间"""
        delay = self.base_delay_sec * (self.exponential_base ** attempt)
        delay = min(delay, self.max_delay_sec)
        if self.jitter:
            delay = delay * (0.5 + random.random())
        return delay


# ═══════════════════════════════════════════════════════════
# 6. 指标收集
# ═══════════════════════════════════════════════════════════


@dataclass
class AdapterMetrics:
    """适配器运行指标"""
    total_submissions: int = 0
    total_polls: int = 0
    total_cancels: int = 0
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    rate_limit_hits: int = 0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    last_operation_at: Optional[float] = None
    errors_by_type: Dict[str, int] = field(default_factory=dict)

    def record_submission(self: "AdapterMetrics", latency_ms: float, success: bool) -> None:
        """记录一次提交操作"""
        self.total_submissions += 1
        self.total_latency_ms += latency_ms
        self.last_operation_at = time.time()
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        self._update_avg()

    def record_poll(self: "AdapterMetrics", latency_ms: float) -> None:
        """记录一次轮询操作"""
        self.total_polls += 1
        self.total_latency_ms += latency_ms
        self.last_operation_at = time.time()
        self._update_avg()

    def record_error(self: "AdapterMetrics", error_type: str) -> None:
        """记录错误"""
        self.errors_by_type[error_type] = self.errors_by_type.get(error_type, 0) + 1

    def record_rate_limit(self: "AdapterMetrics") -> None:
        """记录限流命中"""
        self.rate_limit_hits += 1

    def _update_avg(self: "AdapterMetrics") -> None:
        """更新平均延迟"""
        total_ops = self.total_submissions + self.total_polls + self.total_cancels
        if total_ops > 0:
            self.avg_latency_ms = self.total_latency_ms / total_ops

    def to_dict(self: "AdapterMetrics") -> Dict[str, Any]:
        """序列化为字典"""
        total_ops = self.total_submissions + self.total_polls + self.total_cancels
        return {
            "total_operations": total_ops,
            "total_submissions": self.total_submissions,
            "total_polls": self.total_polls,
            "total_cancels": self.total_cancels,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": round(self.success_count / max(self.total_submissions, 1), 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "rate_limit_hits": self.rate_limit_hits,
            "errors_by_type": self.errors_by_type,
            "last_operation_at": self.last_operation_at,
        }


# ═══════════════════════════════════════════════════════════
# 7. 抽象基类 VideoGenerator
# ═══════════════════════════════════════════════════════════

T = TypeVar("T")


class VideoGenerator(ABC):
    """
    视频生成器抽象基类
    =================
    所有视频提供商适配器必须继承此类并实现抽象方法。

    属性:
        PROVIDER: 类属性，标识此适配器对应的提供商
        SUPPORTED_MODELS: 类属性，支持的模型版本列表
        DEFAULT_MODEL: 类属性，默认使用的模型版本
        ADAPTER_NAME: 类属性，适配器可读名称
        ADAPTER_VERSION: 类属性，适配器版本号
    """

    # ---- 类属性 (子类必须覆盖) ----
    PROVIDER: VideoProvider = VideoProvider.SEEDANCE  # type: ignore
    SUPPORTED_MODELS: List[VideoModelVersion] = []
    DEFAULT_MODEL: Optional[VideoModelVersion] = None
    ADAPTER_NAME: str = ""
    ADAPTER_VERSION: str = "1.0.0"

    # ---- 类属性 (子类可选覆盖) ----
    DEFAULT_TIMEOUT: float = 120.0
    DEFAULT_MAX_RETRIES: int = 3
    DEFAULT_BASE_URL: str = ""

    def __init__(
        self,
        api_keys: List[str],
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
        retry_policy: Optional[RetryPolicy] = None,
        **kwargs: Any,
    ) -> None:
        """
        初始化适配器

        Args:
            api_keys: API Key 列表 (支持多账号)
            base_url: 自定义 API 基础 URL
            timeout: HTTP 请求超时(秒)
            max_retries: 最大重试次数
            circuit_breaker_config: 熔断器配置
            retry_policy: 重试策略配置
            **kwargs: 扩展参数

        Raises:
            ValueError: api_keys 为空或未配置 PROVIDER
        """
        if not self.PROVIDER:
            raise ValueError(f"{self.__class__.__name__} 必须设置 PROVIDER 类属性")
        if not self.SUPPORTED_MODELS:
            raise ValueError(f"{self.__class__.__name__} 必须设置 SUPPORTED_MODELS 类属性")
        if not api_keys:
            raise ValueError(f"{self.PROVIDER.value}: 必须提供至少一个 API Key")

        self.api_keys = api_keys
        self.base_url = base_url or self._default_base_url() or self.DEFAULT_BASE_URL
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self.max_retries = max_retries or self.DEFAULT_MAX_RETRIES
        self.config = kwargs

        # 内部组件
        self._key_pool: Optional[Any] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._circuit_breaker = CircuitBreaker(circuit_breaker_config)
        self._metrics = AdapterMetrics()
        self._logger = logging.getLogger(f"video_generation.adapter.{self.PROVIDER.value}")
        self._closed = False
        self._key_index: int = 0

        self._logger.info(
            "适配器初始化完成",
            extra={
                "provider": self.PROVIDER.value,
                "adapter": self.__class__.__name__,
                "version": self.ADAPTER_VERSION,
                "api_keys_count": len(api_keys),
                "base_url": self.base_url,
            },
        )

    # ═══════════════════════════════════════════════════════════
    # 抽象方法 (子类必须实现)
    # ═══════════════════════════════════════════════════════════

    @abstractmethod
    async def submit(
        self,
        request: VideoGenerationRequest,
        model_version: Optional[VideoModelVersion] = None,
    ) -> VideoTask:
        """提交视频生成任务"""
        ...

    @abstractmethod
    async def poll(self: "VideoGenerator", task_id: str) -> VideoTask:
        """查询任务状态"""
        ...

    @abstractmethod
    async def cancel(self: "VideoGenerator", task_id: str) -> bool:
        """取消进行中的任务"""
        ...

    @abstractmethod
    async def get_pricing(self: "VideoGenerator") -> List[PricingInfo]:
        """获取当前提供商的价格信息"""
        ...

    @abstractmethod
    def get_capability(self: "VideoGenerator") -> ProviderCapability:
        """声明此提供商的能力"""
        ...

    @abstractmethod
    def _default_base_url(self: "VideoGenerator") -> str:
        """返回默认的 API 基础 URL"""
        ...

    @abstractmethod
    def _build_payload(
        self, request: VideoGenerationRequest, model: VideoModelVersion
    ) -> Dict[str, Any]:
        """将统一请求转换为提供商特定格式"""
        ...

    @abstractmethod
    def _parse_submit_response(self: "VideoGenerator", raw: Dict[str, Any]) -> VideoTask:
        """解析提交响应为统一 VideoTask"""
        ...

    @abstractmethod
    def _parse_poll_response(self: "VideoGenerator", raw: Dict[str, Any]) -> VideoTask:
        """解析轮询响应为统一 VideoTask"""
        ...

    # ═══════════════════════════════════════════════════════════
    # 具体方法 (可覆盖)
    # ═══════════════════════════════════════════════════════════

    async def wait_for_completion(
        self,
        task: VideoTask,
        poll_interval: float = 3.0,
        max_poll_interval: float = 30.0,
        timeout: float = 600.0,
        on_progress: Optional[Callable[[VideoTask], None]] = None,
    ) -> VideoTask:
        """
        阻塞等待任务完成 (指数退避轮询)

        Args:
            task: 要等待的任务
            poll_interval: 初始轮询间隔(秒)
            max_poll_interval: 最大轮询间隔(秒)
            timeout: 总超时(秒)
            on_progress: 进度回调函数

        Returns:
            VideoTask: 最终状态的任务

        Raises:
            TaskTimeoutError: 超时
            TaskFailedError: 任务失败
            TaskCancelledError: 任务被取消
        """
        if self._closed:
            raise AdapterClosedError("适配器已关闭，无法等待任务完成")

        start_time = time.time()
        current_interval = poll_interval
        last_status = task.status

        self._logger.info("开始等待任务完成", extra={"task_id": task.task_id, "timeout": timeout})

        while True:
            if self._closed:
                raise AdapterClosedError("适配器已关闭，轮询中断")

            elapsed = time.time() - start_time
            if elapsed > timeout:
                task.status = TaskStatus.EXPIRED
                task.error_code = "TIMEOUT"
                task.error_message = f"任务超时 ({timeout}s)"
                self._metrics.record_error("timeout")
                self._metrics.timeout_count += 1
                raise TaskTimeoutError(f"任务 {task.task_id} 超时 ({timeout}s)", task_id=task.task_id)

            try:
                task = await self.poll(task.task_id)
            except Exception as e:
                self._logger.warning("轮询查询失败，将在间隔后重试", extra={"task_id": task.task_id, "error": str(e)})
                await asyncio.sleep(current_interval)
                current_interval = min(current_interval * 1.5, max_poll_interval)
                continue

            # 状态变更通知
            if task.status != last_status:
                self._logger.info(
                    "任务状态变更",
                    extra={"task_id": task.task_id, "from": last_status.value if last_status else None, "to": task.status.value},
                )
                if on_progress:
                    try:
                        if asyncio.iscoroutinefunction(on_progress):
                            await on_progress(task)
                        else:
                            on_progress(task)
                    except Exception as cb_err:
                        self._logger.warning("进度回调异常", extra={"error": str(cb_err)})
                last_status = task.status

            # 检查终止状态
            if task.status == TaskStatus.SUCCEEDED:
                task.completed_at = time.time()
                task.generation_time_sec = task.completed_at - task.created_at
                self._metrics.record_submission(latency_ms=task.generation_time_sec * 1000, success=True)
                return task

            if task.status == TaskStatus.FAILED:
                self._metrics.record_submission(latency_ms=elapsed * 1000, success=False)
                raise TaskFailedError(f"任务 {task.task_id} 失败: {task.error_message}", task_id=task.task_id, error_code=task.error_code)

            if task.status == TaskStatus.CANCELLED:
                raise TaskCancelledError(f"任务 {task.task_id} 已取消", task_id=task.task_id)

            jitter = random.uniform(-0.2 * current_interval, 0.2 * current_interval)
            await asyncio.sleep(max(1.0, current_interval + jitter))
            current_interval = min(current_interval * 1.5, max_poll_interval)

    def supports_request(self: "VideoGenerator", request: VideoGenerationRequest) -> Tuple[bool, str]:
        """
        检查是否支持给定请求

        Returns:
            Tuple[bool, str]: (是否支持, 不支持原因/OK)
        """
        cap = self.get_capability()

        if request.duration > cap.max_duration:
            return False, f"时长 {request.duration}s 超过最大 {cap.max_duration}s"
        if request.resolution == VideoResolution.P4K and not cap.supports_4k:
            return False, "不支持 4K 分辨率"
        if request.fps and request.fps > 30 and not cap.supports_60fps:
            return False, "不支持 60fps"
        if len(request.reference_images) > cap.max_reference_images:
            return False, f"参考图片 {len(request.reference_images)} 超过上限 {cap.max_reference_images}"
        if request.reference_videos and not cap.supports_reference_videos:
            return False, "不支持参考视频输入"
        if request.reference_audios and not cap.supports_reference_audios:
            return False, "不支持参考音频输入"
        if request.last_frame and not cap.supports_last_frame:
            return False, "不支持尾帧输入"
        if request.generate_audio and not cap.supports_audio_generation:
            return False, "不支持音频生成"
        if request.negative_prompt and not cap.supports_negative_prompt:
            return False, "不支持负面提示词"
        if request.enhance_prompt and not cap.supports_enhance_prompt:
            return False, "不支持提示词增强"

        return True, "OK"

    async def health_check(self: "VideoGenerator") -> ProviderHealth:
        """健康检查"""
        start = time.time()
        try:
            await self.get_pricing()
            latency_ms = (time.time() - start) * 1000
            return ProviderHealth(provider=self.PROVIDER, status=ProviderStatus.HEALTHY.value, latency_ms=latency_ms, last_check_at=time.time())
        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            return ProviderHealth(provider=self.PROVIDER, status=ProviderStatus.DOWN.value, latency_ms=latency_ms, last_check_at=time.time(), error_message=str(e))

    async def download_video(
        self, video_url: str, save_path: str, chunk_size: int = 8192, timeout: Optional[float] = None
    ) -> str:
        """下载视频到本地"""
        if self._closed:
            raise AdapterClosedError("适配器已关闭，无法下载")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        download_timeout = timeout or self.timeout
        try:
            async with self._session.get(video_url, timeout=aiohttp.ClientTimeout(total=download_timeout)) as resp:
                resp.raise_for_status()
                downloaded = 0
                with open(save_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(chunk_size):
                        f.write(chunk)
                        downloaded += len(chunk)
                return save_path
        except aiohttp.ClientError as e:
            raise NetworkError(f"视频下载失败: {str(e)}", url=video_url) from e
        except OSError as e:
            raise NetworkError(f"文件写入失败: {str(e)}", path=save_path) from e

    def _map_status(self: "VideoGenerator", raw_status: str) -> TaskStatus:
        """将提供商原始状态映射为统一状态"""
        status_map: Dict[str, TaskStatus] = {
            "pending": TaskStatus.PENDING, "queued": TaskStatus.QUEUED, "waiting": TaskStatus.PENDING,
            "submitted": TaskStatus.PENDING, "processing": TaskStatus.PROCESSING, "running": TaskStatus.PROCESSING,
            "generating": TaskStatus.PROCESSING, "in_progress": TaskStatus.PROCESSING, "working": TaskStatus.PROCESSING,
            "completed": TaskStatus.SUCCEEDED, "done": TaskStatus.SUCCEEDED, "success": TaskStatus.SUCCEEDED,
            "succeeded": TaskStatus.SUCCEEDED, "finished": TaskStatus.SUCCEEDED, "failed": TaskStatus.FAILED,
            "error": TaskStatus.FAILED, "failure": TaskStatus.FAILED, "rejected": TaskStatus.FAILED,
            "cancelled": TaskStatus.CANCELLED, "canceled": TaskStatus.CANCELLED, "aborted": TaskStatus.CANCELLED,
            "expired": TaskStatus.EXPIRED, "timeout": TaskStatus.EXPIRED, "deadline_exceeded": TaskStatus.EXPIRED,
        }
        return status_map.get(raw_status.lower(), TaskStatus.PENDING)

    # ═══════════════════════════════════════════════════════════
    # Key 池集成
    # ═══════════════════════════════════════════════════════════

    def set_key_pool(self: "VideoGenerator", key_pool: Any) -> None:
        """注入 Key 池管理器"""
        self._key_pool = key_pool

    def _get_next_key(self: "VideoGenerator") -> str:
        """从 Key 池中获取下一个可用 Key"""
        if self._key_pool is not None:
            return self._key_pool.get_next_key()
        if not self.api_keys:
            raise ValueError(f"{self.PROVIDER.value}: 未配置 API Key")
        idx = self._key_index % len(self.api_keys)
        self._key_index = (self._key_index + 1) % len(self.api_keys)
        return self.api_keys[idx]

    def _mark_key_success(self: "VideoGenerator", key: str, response_time_ms: float) -> None:
        """标记 Key 请求成功"""
        if self._key_pool is not None:
            self._key_pool.mark_success(key, response_time_ms)

    def _mark_key_failure(self: "VideoGenerator", key: str) -> None:
        """标记 Key 请求失败"""
        if self._key_pool is not None:
            self._key_pool.mark_failure(key)

    # ═══════════════════════════════════════════════════════════
    # HTTP 请求执行
    # ═══════════════════════════════════════════════════════════

    async def _execute_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
        api_key: Optional[str] = None,
        use_circuit_breaker: bool = True,
    ) -> Dict[str, Any]:
        """执行 HTTP 请求 (带完整错误处理和重试)"""
        if self._closed:
            raise AdapterClosedError("适配器已关闭")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        key = api_key or self._get_next_key()
        request_headers = self._build_auth_headers(key)
        if headers:
            request_headers.update(headers)

        start_time = time.time()

        async def _do_request() -> Dict[str, Any]:
            try:
                async with self._session.request(
                    method=method, url=url, headers=request_headers,
                    json=json_data, params=params,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    body = await resp.json() if resp.content_type == "application/json" else await resp.text()
                    latency_ms = (time.time() - start_time) * 1000

                    if resp.status in (200, 201):
                        self._mark_key_success(key, latency_ms)
                        self._metrics.record_submission(latency_ms, success=True)
                        return body if isinstance(body, dict) else {"raw": body}
                    elif resp.status == 429:
                        self._metrics.record_rate_limit()
                        retry_after = float(resp.headers.get("Retry-After", 60))
                        raise RateLimitError("请求被限流 (429)", retry_after_sec=retry_after, provider=self.PROVIDER.value)
                    elif resp.status in (401, 403):
                        self._mark_key_failure(key)
                        raise QuotaExceededError(f"认证失败或配额超限 ({resp.status}): {body}", provider=self.PROVIDER.value, api_key_preview=key[:8] + "...")
                    elif resp.status >= 500:
                        self._mark_key_failure(key)
                        raise SubmissionError(f"服务端错误 ({resp.status}): {body}", status_code=resp.status, provider=self.PROVIDER.value)
                    else:
                        self._mark_key_failure(key)
                        raise SubmissionError(f"请求失败 ({resp.status}): {body}", status_code=resp.status, provider=self.PROVIDER.value)
            except aiohttp.ClientError as e:
                self._metrics.record_error("network")
                raise NetworkError(f"网络请求失败: {str(e)}", url=url, provider=self.PROVIDER.value) from e

        if use_circuit_breaker:
            return await self._circuit_breaker.call(_do_request)
        return await _do_request()

    def _build_auth_headers(self: "VideoGenerator", api_key: str) -> Dict[str, str]:
        """构建认证请求头 (默认 Bearer Token)"""
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ═══════════════════════════════════════════════════════════
    # 重试装饰器
    # ═══════════════════════════════════════════════════════════

    def with_retry(self: "VideoGenerator", policy: Optional[RetryPolicy] = None) -> Callable:
        """重试装饰器"""
        retry_policy = policy or RetryPolicy(max_retries=self.max_retries)

        def decorator(fn: Callable) -> Callable:
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                last_error: Optional[Exception] = None
                for attempt in range(retry_policy.max_retries + 1):
                    try:
                        return await fn(*args, **kwargs)
                    except retry_policy.retryable_exceptions as e:
                        last_error = e
                        if attempt < retry_policy.max_retries:
                            delay = retry_policy.compute_delay(attempt)
                            self._logger.warning(f"操作失败，第 {attempt + 1}/{retry_policy.max_retries} 次重试，延迟 {delay:.2f}s")
                            await asyncio.sleep(delay)
                        else:
                            break
                    except Exception:
                        raise
                raise last_error or SubmissionError("所有重试均失败")
            return wrapper
        return decorator

    # ═══════════════════════════════════════════════════════════
    # 模型版本选择
    # ═══════════════════════════════════════════════════════════

    def _resolve_model(self: "VideoGenerator", model_version: Optional[VideoModelVersion] = None) -> VideoModelVersion:
        """解析模型版本"""
        chosen = model_version or self.DEFAULT_MODEL or self.SUPPORTED_MODELS[0]
        if chosen not in self.SUPPORTED_MODELS:
            raise UnsupportedRequestError(f"不支持的模型版本: {chosen.value}")
        return chosen

    # ═══════════════════════════════════════════════════════════
    # 指标和日志
    # ═══════════════════════════════════════════════════════════

    def get_metrics(self: "VideoGenerator") -> Dict[str, Any]:
        """获取适配器运行指标"""
        return {
            "provider": self.PROVIDER.value,
            "adapter": self.__class__.__name__,
            **self._metrics.to_dict(),
            "circuit_breaker": self._circuit_breaker.get_state(),
        }

    def get_circuit_breaker_state(self: "VideoGenerator") -> Dict[str, Any]:
        """获取熔断器状态"""
        return self._circuit_breaker.get_state()

    # ═══════════════════════════════════════════════════════════
    # 生命周期管理
    # ═══════════════════════════════════════════════════════════

    async def close(self: "VideoGenerator") -> None:
        """关闭适配器，释放资源"""
        self._closed = True
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self._logger.info("适配器已关闭", extra={"provider": self.PROVIDER.value})

    async def __aenter__(self: "VideoGenerator") -> VideoGenerator:
        return self

    async def __aexit__(self: "VideoGenerator", exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException], exc_tb: Optional[Any]) -> None:
        await self.close()

    def __repr__(self: "VideoGenerator") -> str:
        return f"{self.__class__.__name__}(provider={self.PROVIDER.value}, models={[m.value for m in self.SUPPORTED_MODELS]}, keys={len(self.api_keys)})"


# ═══════════════════════════════════════════════════════════
# 8. 适配器注册表 AdapterRegistry
# ═══════════════════════════════════════════════════════════


_global_registry: Optional[AdapterRegistry] = None
_registry_callbacks: List[Callable[[AdapterRegistry], None]] = []


def get_registry() -> AdapterRegistry:
    """获取全局注册表实例 (懒加载单例)"""
    global _global_registry
    if _global_registry is None:
        _global_registry = AdapterRegistry()
    return _global_registry


def reset_registry() -> None:
    """重置全局注册表 (主要用于测试)"""
    global _global_registry
    _global_registry = None


def register_adapter(cls: Type[VideoGenerator]) -> Type[VideoGenerator]:
    """类装饰器: 自动注册适配器类到全局注册表"""
    def _callback(registry: AdapterRegistry) -> None:
        registry._register_class(cls)

    _registry_callbacks.append(_callback)
    if _global_registry is not None:
        _callback(_global_registry)
    return cls


class AdapterRegistry:
    """
    适配器注册中心

    管理所有视频提供商适配器的注册、查询和生命周期。
    支持: 手动注册、自动发现、装饰器注册、配置驱动注册
    """

    def __init__(self: "AdapterRegistry") -> None:
        self._adapters: Dict[VideoProvider, VideoGenerator] = {}
        self._adapter_classes: Dict[VideoProvider, Type[VideoGenerator]] = {}
        self._capability_cache: Dict[VideoProvider, ProviderCapability] = {}
        self._logger = logging.getLogger("video_generation.registry")

    def register(self: "AdapterRegistry", adapter: VideoGenerator) -> None:
        """手动注册适配器实例"""
        if not isinstance(adapter, VideoGenerator):
            raise TypeError(f"adapter 必须是 VideoGenerator 实例，实际是 {type(adapter).__name__}")

        provider = adapter.PROVIDER
        if provider in self._adapters:
            raise DuplicateAdapterError(f"提供商 {provider.value} 的适配器已注册")

        self._adapters[provider] = adapter
        self._adapter_classes[provider] = type(adapter)

        try:
            self._capability_cache[provider] = adapter.get_capability()
        except Exception as e:
            self._logger.warning(f"获取 {provider.value} 能力声明失败: {e}")

        self._logger.info("适配器已注册", extra={"provider": provider.value, "adapter_class": type(adapter).__name__})

    def _register_class(self: "AdapterRegistry", cls: Type[VideoGenerator]) -> None:
        """注册适配器类 (不实例化)"""
        provider = cls.PROVIDER
        if provider is None:
            self._logger.warning(f"类 {cls.__name__} 未设置 PROVIDER，跳过注册")
            return
        self._adapter_classes[provider] = cls

    def auto_discover(
        self,
        package_path: str = "video_generation.adapters",
        instantiate: bool = False,
        config_manager: Optional[AdapterConfigManager] = None,
    ) -> List[VideoProvider]:
        """自动发现适配器"""
        discovered: List[VideoProvider] = []
        try:
            package = importlib.import_module(package_path)
        except ImportError as e:
            self._logger.error(f"无法导入包 {package_path}: {e}")
            return discovered

        for _, module_name, is_pkg in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
            if is_pkg:
                continue
            try:
                module = importlib.import_module(module_name)
            except ImportError as e:
                self._logger.warning(f"无法导入模块 {module_name}: {e}")
                continue

            for name, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, VideoGenerator)
                    and obj is not VideoGenerator
                    and not inspect.isabstract(obj)
                    and obj.PROVIDER is not None
                ):
                    provider = obj.PROVIDER
                    self._register_class(obj)
                    discovered.append(provider)
                    if instantiate and config_manager:
                        self._instantiate_from_config(provider, obj, config_manager)

        return discovered

    def register_from_config(self: "AdapterRegistry", config: Dict[str, Any]) -> List[VideoProvider]:
        """从配置字典批量注册适配器"""
        registered: List[VideoProvider] = []
        for provider_name, provider_config in config.items():
            try:
                provider = VideoProvider(provider_name)
            except ValueError:
                self._logger.warning(f"未知的提供商: {provider_name}，跳过")
                continue

            adapter_class = self._adapter_classes.get(provider)
            if adapter_class is None:
                self.auto_discover()
                adapter_class = self._adapter_classes.get(provider)

            if adapter_class is None:
                self._logger.warning(f"未找到提供商 {provider.value} 的适配器类")
                continue

            api_keys = provider_config.get("api_keys", [])
            if not api_keys:
                self._logger.warning(f"提供商 {provider.value} 未配置 API Keys，跳过")
                continue

            init_kwargs = {
                "api_keys": api_keys,
                "base_url": provider_config.get("base_url"),
                "timeout": provider_config.get("timeout"),
                "max_retries": provider_config.get("max_retries"),
                **provider_config.get("extra", {}),
            }
            init_kwargs = {k: v for k, v in init_kwargs.items() if v is not None}

            try:
                adapter = adapter_class(**init_kwargs)
                self.register(adapter)
                registered.append(provider)
            except Exception as e:
                self._logger.error(f"实例化适配器 {provider.value} 失败: {e}")

        return registered

    def _instantiate_from_config(
        self, provider: VideoProvider, cls: Type[VideoGenerator], config_manager: AdapterConfigManager
    ) -> None:
        """从配置管理器实例化适配器"""
        try:
            adapter_config = config_manager.get_provider_config(provider)
            if adapter_config and adapter_config.api_keys:
                adapter = cls(
                    api_keys=adapter_config.api_keys,
                    base_url=adapter_config.base_url,
                    timeout=adapter_config.timeout,
                    max_retries=adapter_config.max_retries,
                    **(adapter_config.extra or {}),
                )
                self.register(adapter)
        except Exception as e:
            self._logger.warning(f"从配置实例化 {provider.value} 失败: {e}")

    def get(self: "AdapterRegistry", provider: VideoProvider) -> VideoGenerator:
        """获取指定提供商的适配器实例"""
        adapter = self._adapters.get(provider)
        if adapter is None:
            raise AdapterNotFoundError(f"未找到提供商 {provider.value} 的适配器实例")
        return adapter

    def get_optional(self: "AdapterRegistry", provider: VideoProvider) -> Optional[VideoGenerator]:
        """获取指定提供商的适配器实例 (不抛出异常)"""
        return self._adapters.get(provider)

    def get_adapter(self: "AdapterRegistry", provider: Union[str, VideoProvider]) -> Optional[VideoGenerator]:
        """兼容旧控制器调用，通过字符串或枚举获取适配器。"""
        try:
            provider_enum = provider if isinstance(provider, VideoProvider) else VideoProvider(provider)
        except ValueError:
            return None
        return self.get_optional(provider_enum)

    def list_all(self: "AdapterRegistry") -> List[VideoGenerator]:
        """获取所有已注册的适配器实例"""
        return list(self._adapters.values())

    def list_providers(self: "AdapterRegistry") -> List[VideoProvider]:
        """获取所有已注册的提供商"""
        return list(self._adapters.keys())

    def filter_by_capability(self: "AdapterRegistry", request: VideoGenerationRequest) -> List[Tuple[VideoGenerator, str]]:
        """过滤出支持给定请求的适配器"""
        results: List[Tuple[VideoGenerator, str]] = []
        for adapter in self._adapters.values():
            supported, reason = adapter.supports_request(request)
            if supported:
                results.append((adapter, reason))
        return results

    async def get_healthy(self: "AdapterRegistry") -> List[VideoGenerator]:
        """获取所有健康的适配器"""
        healthy = []
        for adapter in self._adapters.values():
            try:
                health = await adapter.health_check()
                if health.status == ProviderStatus.HEALTHY.value:
                    healthy.append(adapter)
            except Exception as e:
                self._logger.warning(f"健康检查异常: {adapter.PROVIDER.value}: {e}")
        return healthy

    def is_registered(self: "AdapterRegistry", provider: VideoProvider) -> bool:
        """检查指定提供商是否已注册"""
        return provider in self._adapters

    def get_capability(self: "AdapterRegistry", provider: VideoProvider) -> Optional[ProviderCapability]:
        """获取指定提供商的能力声明 (缓存)"""
        return self._capability_cache.get(provider)

    async def unregister(self: "AdapterRegistry", provider: VideoProvider) -> None:
        """注销指定提供商的适配器"""
        adapter = self._adapters.pop(provider, None)
        self._adapter_classes.pop(provider, None)
        self._capability_cache.pop(provider, None)
        if adapter:
            await adapter.close()
            self._logger.info("适配器已注销", extra={"provider": provider.value})

    async def clear(self: "AdapterRegistry") -> None:
        """注销所有适配器并释放资源"""
        providers = list(self._adapters.keys())
        for provider in providers:
            await self.unregister(provider)
        self._logger.info("注册表已清空")

    def __len__(self: "AdapterRegistry") -> int:
        return len(self._adapters)

    def __contains__(self: "AdapterRegistry", provider: VideoProvider) -> bool:
        return provider in self._adapters

    def __repr__(self: "AdapterRegistry") -> str:
        return f"AdapterRegistry(registered={len(self._adapters)}, providers={[p.value for p in self._adapters.keys()]})"


# ═══════════════════════════════════════════════════════════
# 9. 配置管理 AdapterConfigManager
# ═══════════════════════════════════════════════════════════


class AdapterConfigManager:
    """
    适配器配置管理器

    统一管理所有适配器的配置，支持从字典、文件加载配置。
    """

    def __init__(self: "AdapterConfigManager", config: Optional[Dict[str, Any]] = None) -> None:
        """
        初始化配置管理器

        Args:
            config: 配置字典，格式:
                {
                    "seedance": {"api_keys": ["key1"], "timeout": 120, ...},
                    "kling": {"api_keys": ["key2"], ...},
                    ...
                }
        """
        self._configs: Dict[VideoProvider, AdapterConfig] = {}
        self._logger = logging.getLogger("video_generation.config")
        if config:
            self.load_from_dict(config)

    def load_from_dict(self: "AdapterConfigManager", config: Dict[str, Any]) -> None:
        """从字典加载配置"""
        for provider_name, provider_config in config.items():
            try:
                provider = VideoProvider(provider_name)
                self._configs[provider] = AdapterConfig(provider=provider, **provider_config)
            except ValueError:
                self._logger.warning(f"未知的提供商: {provider_name}")
            except Exception as e:
                self._logger.error(f"加载 {provider_name} 配置失败: {e}")

    def get_provider_config(self: "AdapterConfigManager", provider: VideoProvider) -> Optional[AdapterConfig]:
        """获取指定提供商的配置"""
        return self._configs.get(provider)

    def set_provider_config(self: "AdapterConfigManager", provider: VideoProvider, config: AdapterConfig) -> None:
        """设置指定提供商的配置"""
        self._configs[provider] = config

    def list_configured_providers(self: "AdapterConfigManager") -> List[VideoProvider]:
        """获取所有已配置的提供商"""
        return list(self._configs.keys())

    def to_dict(self: "AdapterConfigManager") -> Dict[str, Any]:
        """导出为字典"""
        return {p.value: c.model_dump() for p, c in self._configs.items()}

    def __repr__(self: "AdapterConfigManager") -> str:
        return f"AdapterConfigManager(configured={len(self._configs)} providers)"


# ═══════════════════════════════════════════════════════════
# 10. ModelSelector 专用数据模型
# ═══════════════════════════════════════════════════════════


class ProviderRule(BaseModel):
    """单条选型规则"""
    model_config = ConfigDict(frozen=True)

    rule_id: str = Field(..., description="规则唯一标识")
    name: str = Field(..., description="规则名称")
    description: str = Field(..., description="规则详细说明")
    level: RuleLevel = Field(..., description="规则级别")
    condition_desc: str = Field("", description="条件文字描述")
    provider_priority: List[Tuple[VideoProvider, VideoModelVersion]] = Field(default_factory=list)
    score_bonus: Dict[Tuple[VideoProvider, VideoModelVersion], float] = Field(default_factory=dict)
    score_penalty: Dict[Tuple[VideoProvider, VideoModelVersion], float] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)


class RuleEngineResult(BaseModel):
    """规则引擎执行结果"""
    model_config = ConfigDict(extra="allow")

    mandatory_filtered: List[Tuple[VideoProvider, VideoModelVersion]] = Field(default_factory=list)
    mandatory_rules_applied: List[str] = Field(default_factory=list)
    score_adjustments: Dict[Tuple[VideoProvider, VideoModelVersion], float] = Field(default_factory=dict)
    recommended_rules_applied: List[str] = Field(default_factory=list)
    reasoning_parts: List[str] = Field(default_factory=list)

    def get_reasoning(self: "RuleEngineResult") -> str:
        parts = []
        if self.mandatory_rules_applied:
            parts.append(f"强制规则: {', '.join(self.mandatory_rules_applied)}")
        if self.recommended_rules_applied:
            parts.append(f"建议规则: {', '.join(self.recommended_rules_applied)}")
        parts.extend(self.reasoning_parts)
        return "; ".join(parts) if parts else "默认平衡策略"


class ModelScore(BaseModel):
    """单个提供商-模型组合的评分结果"""
    model_config = ConfigDict(extra="allow")

    provider: VideoProvider = Field(...)
    model_version: VideoModelVersion = Field(...)
    raw_scores: Dict[ScoreDimension, float] = Field(default_factory=dict)
    total_score: float = Field(0.0)
    dimension_breakdown: Dict[str, float] = Field(default_factory=dict)
    rule_bonus: float = Field(0.0)
    rule_penalty: float = Field(0.0)
    disqualified: bool = Field(False)
    disqualify_reason: Optional[str] = Field(None)
    estimated_cost_usd: float = Field(0.0)
    estimated_time_sec: float = Field(0.0)

    def __str__(self: "ModelScore") -> str:
        status = "x 已排除" if self.disqualified else f"* {self.total_score:.1f}"
        return f"{self.provider.value}/{self.model_version.value}: {status}"


class ScoringWeights(BaseModel):
    """评分权重配置"""
    model_config = ConfigDict(frozen=True)

    strategy: RoutingStrategy = Field(...)
    speed_weight: float = Field(0.0, ge=0, le=1)
    quality_weight: float = Field(0.0, ge=0, le=1)
    cost_weight: float = Field(0.0, ge=0, le=1)
    character_weight: float = Field(0.0, ge=0, le=1)
    chinese_weight: float = Field(0.0, ge=0, le=1)
    capability_weight: float = Field(0.0, ge=0, le=1)
    rule_bonus_max: float = Field(15.0)
    rule_penalty_max: float = Field(10.0)

    @property
    def weight_dict(self: "ScoringWeights") -> Dict[ScoreDimension, float]:
        return {
            ScoreDimension.SPEED: self.speed_weight,
            ScoreDimension.QUALITY: self.quality_weight,
            ScoreDimension.COST: self.cost_weight,
            ScoreDimension.CHARACTER: self.character_weight,
            ScoreDimension.CHINESE: self.chinese_weight,
            ScoreDimension.CAPABILITY: self.capability_weight,
        }


class ModelRequirements(BaseModel):
    """模型选型需求"""
    model_config = ConfigDict(extra="allow")

    strategy: RoutingStrategy = Field(RoutingStrategy.BALANCED)
    resolution: VideoResolution = Field(VideoResolution.P720)
    aspect_ratio: VideoAspectRatio = Field(VideoAspectRatio.RATIO_16_9)
    duration: int = Field(5, ge=1, le=16)
    fps: Optional[int] = Field(None)
    generate_audio: bool = Field(True)
    has_text_prompt: bool = Field(True)
    has_first_frame: bool = Field(False)
    has_last_frame: bool = Field(False)
    reference_image_count: int = Field(0)
    has_reference_videos: bool = Field(False)
    has_reference_audios: bool = Field(False)
    negative_prompt: Optional[str] = Field(None)
    enhance_prompt: bool = Field(False)
    prompt_text: str = Field("")
    chinese_ratio: float = Field(0.0, ge=0, le=1)
    has_character_description: bool = Field(False)
    has_camera_switching: bool = Field(False)
    has_digital_human: bool = Field(False)
    has_dialogue: bool = Field(False)
    has_editing_instruction: bool = Field(False)
    max_budget_usd: Optional[float] = Field(None)
    max_wait_time_sec: Optional[int] = Field(None)
    off_peak: bool = Field(False)
    preferred_provider: Optional[VideoProvider] = Field(None)
    preferred_model: Optional[VideoModelVersion] = Field(None)
    fallback_providers: List[VideoProvider] = Field(default_factory=list)
    extra_features: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_video_request(cls: type["ModelRequirements"], request: VideoGenerationRequest) -> ModelRequirements:
        """从 VideoGenerationRequest 构建 ModelRequirements"""
        prompt = request.prompt or ""
        chinese_chars = sum(1 for c in prompt if "\u4e00" <= c <= "\u9fff")
        total_chars = len(prompt) if prompt else 1
        chinese_ratio = chinese_chars / total_chars

        character_keywords = ["人物", "角色", "人", "person", "character", "woman", "man", "girl", "boy", "face", "肖像", "portrait", "avatar"]
        has_character = any(kw in prompt.lower() for kw in character_keywords)

        camera_keywords = ["镜头", "切换", "scene", "cut to", "transition", "特写", "全景", "推拉", "摇镜", "分镜"]
        has_camera = any(kw in prompt.lower() for kw in camera_keywords)

        digital_human_keywords = ["数字人", "口播", "主播", "讲解", "digital human", "news anchor", "presenter"]
        has_digital = any(kw in prompt.lower() for kw in digital_human_keywords)

        dialogue_keywords = ["对话", "说话", "交谈", "speak", "talk", "dialogue", "conversation", "say", "said", "speaking"]
        has_dialogue = any(kw in prompt.lower() for kw in dialogue_keywords)

        editing_keywords = ["编辑", "修改", "替换", "edit", "replace", "remove", "删除", "增加", "adjust", "modify"]
        has_editing = any(kw in prompt.lower() for kw in editing_keywords)

        return cls(
            strategy=request.routing_strategy,
            resolution=request.resolution,
            aspect_ratio=request.aspect_ratio,
            duration=request.duration,
            fps=request.fps,
            generate_audio=request.generate_audio,
            has_text_prompt=bool(request.prompt),
            has_first_frame=request.first_frame is not None,
            has_last_frame=request.last_frame is not None,
            reference_image_count=len(request.reference_images),
            has_reference_videos=len(request.reference_videos) > 0,
            has_reference_audios=len(request.reference_audios) > 0,
            negative_prompt=request.negative_prompt,
            enhance_prompt=request.enhance_prompt,
            prompt_text=prompt,
            chinese_ratio=chinese_ratio,
            has_character_description=has_character,
            has_camera_switching=has_camera,
            has_digital_human=has_digital,
            has_dialogue=has_dialogue,
            has_editing_instruction=has_editing,
            max_budget_usd=request.max_budget_usd,
            max_wait_time_sec=request.max_wait_time_sec,
            off_peak=request.extra_params.get("off_peak", False),
            preferred_provider=request.preferred_provider,
            preferred_model=request.preferred_model,
            fallback_providers=request.fallback_providers,
        )


class SelectorConfig(BaseModel):
    """ModelSelector 全局配置"""
    model_config = ConfigDict(extra="allow")

    default_strategy: RoutingStrategy = Field(RoutingStrategy.BALANCED)
    min_acceptable_score: float = Field(10.0)
    mandatory_strict_mode: bool = Field(True)
    fallback_chain_max_length: int = Field(3)
    fallback_require_compatible: bool = Field(True)
    cache_scores: bool = Field(True)
    score_cache_ttl_sec: int = Field(300)
    log_decision_process: bool = Field(True)
    log_level: str = Field("INFO")


__all__ = [
    # Enums
    "VideoProvider", "VideoModelVersion", "TaskStatus", "VideoResolution",
    "VideoAspectRatio", "RoutingStrategy", "ProviderStatus", "CircuitState",
    "ScoreDimension", "RuleLevel",
    # Models
    "VideoGenerationRequest", "VideoTask", "PricingInfo", "ProviderCapability",
    "ProviderHealth", "RoutingDecision", "AdapterConfig",
    # Exceptions
    "VideoGenerationError", "AdapterClosedError", "AdapterNotFoundError",
    "DuplicateAdapterError", "CircuitBreakerOpenError", "NetworkError",
    "RateLimitError", "QuotaExceededError", "SubmissionError", "PollError",
    "TaskTimeoutError", "TaskFailedError", "TaskCancelledError",
    "TaskNotFoundError", "UnsupportedRequestError", "NoSuitableProviderError",
    "ManualProviderNotAvailableError", "BudgetExceededError",
    # Core classes
    "CircuitBreakerConfig", "CircuitBreaker", "RetryPolicy", "AdapterMetrics",
    "VideoGenerator", "AdapterRegistry", "AdapterConfigManager",
    "get_registry", "reset_registry", "register_adapter",
    # Selector models
    "ProviderRule", "RuleEngineResult", "ModelScore", "ScoringWeights",
    "ModelRequirements", "SelectorConfig",
]
