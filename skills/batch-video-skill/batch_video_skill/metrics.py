"""
监控指标收集系统 - metrics.py

提供多维指标收集和 Prometheus 导出能力，核心特性:
- 原生 Prometheus 指标 (Counter/Gauge/Histogram)
- 多维标签支持 (service/provider/status/error_code 等)
- 指标快照和内存统计
- 零依赖启动 (Prometheus 可选，自动降级为内存存储)
- 线程安全 + asyncio 安全

依赖:
    pip install prometheus_client  # 可选，用于 Prometheus 导出

使用示例:
    collector = MetricsCollector()
    collector.record_request("video_gen", "seedance", "generate", 45230.0, True)
    snapshot = collector.get_stats()
    metrics_bytes = collector.export_prometheus()
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

from pydantic import BaseModel, Field


# ============================================================================
# 枚举定义
# ============================================================================


class MetricType(str, Enum):
    """指标类型枚举"""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


class MetricName(str, Enum):
    """指标名称枚举 - 所有预定义指标"""

    REQUESTS_TOTAL = "skill_requests_total"
    REQUEST_DURATION = "skill_request_duration_seconds"
    REQUESTS_IN_FLIGHT = "skill_requests_in_flight"
    RETRIES_TOTAL = "skill_retries_total"
    RETRY_EXHAUSTED_TOTAL = "skill_retry_exhausted_total"
    FALLBACKS_TOTAL = "skill_fallbacks_total"
    FALLBACK_SUCCESS_RATE = "skill_fallback_success_rate"
    FALLBACK_CHAIN_EXHAUSTED = "skill_fallback_chain_exhausted_total"
    CIRCUIT_BREAKER_STATE = "skill_circuit_breaker_state"
    CIRCUIT_BREAKS_TOTAL = "skill_circuit_breaks_total"
    PROVIDER_HEALTH = "skill_provider_health"
    PROVIDER_ERROR_RATE = "skill_provider_error_rate"
    PROVIDER_LATENCY_MS = "skill_provider_latency_ms"
    QUOTA_USAGE_RATIO = "skill_quota_usage_ratio"
    QUOTA_REMAINING = "skill_quota_remaining"
    TEMPLATE_USAGE = "skill_template_usage_total"
    VIDEO_GENERATIONS = "skill_video_generations_total"
    VIDEO_GEN_DURATION = "skill_video_generation_duration_seconds"
    GEM_USAGE = "skill_gem_usage_total"


# ============================================================================
# Pydantic 模型
# ============================================================================


class MetricDefinition(BaseModel):
    """指标定义模型"""

    name: str = Field(..., description="指标名称")
    type: MetricType = Field(..., description="指标类型")
    description: str = Field(..., description="指标描述")
    labels: List[str] = Field(default_factory=list, description="标签列表")
    buckets: Optional[List[float]] = Field(default=None, description="直方图分桶")

    model_config = {"frozen": True}


class ProviderHealth(BaseModel):
    """Provider 健康状态"""

    provider: str = Field(..., description="Provider 名称")
    service: str = Field(..., description="服务名称")
    healthy: bool = Field(..., description="是否健康")
    error_rate: float = Field(default=0.0, description="错误率")
    avg_latency_ms: float = Field(default=0.0, description="平均延迟(ms)")
    circuit_state: str = Field(default="closed", description="熔断器状态")
    last_checked: Optional[str] = Field(default=None, description="最后检查时间")


class MetricsSnapshot(BaseModel):
    """监控指标快照 - 全量指标的状态汇总"""

    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="快照时间戳",
    )
    total_requests: int = Field(default=0, description="总请求数")
    success_count: int = Field(default=0, description="成功数")
    error_count: int = Field(default=0, description="错误数")
    success_rate: float = Field(default=0.0, description="成功率(0.0-1.0)")
    avg_latency_ms: float = Field(default=0.0, description="平均延迟(ms)")
    active_requests: int = Field(default=0, description="活跃请求数")
    total_retries: int = Field(default=0, description="总重试数")
    total_fallbacks: int = Field(default=0, description="总降级数")
    provider_health: List[ProviderHealth] = Field(
        default_factory=list, description="Provider 健康列表"
    )
    circuit_breaker_states: Dict[str, str] = Field(
        default_factory=dict, description="熔断器状态字典"
    )
    quota_usage: Dict[str, float] = Field(
        default_factory=dict, description="配额使用情况"
    )
    custom_metrics: Dict[str, Any] = Field(
        default_factory=dict, description="自定义指标"
    )


# ============================================================================
# 指标定义注册表
# ============================================================================

PREDEFINED_METRICS: List[MetricDefinition] = [
    MetricDefinition(
        name=MetricName.REQUESTS_TOTAL.value,
        type=MetricType.COUNTER,
        description="请求总数",
        labels=["service", "provider", "status", "error_code"],
    ),
    MetricDefinition(
        name=MetricName.REQUEST_DURATION.value,
        type=MetricType.HISTOGRAM,
        description="请求耗时分布(秒)",
        labels=["service", "provider", "method"],
        buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
    ),
    MetricDefinition(
        name=MetricName.REQUESTS_IN_FLIGHT.value,
        type=MetricType.GAUGE,
        description="进行中的请求数",
        labels=["service"],
    ),
    MetricDefinition(
        name=MetricName.RETRIES_TOTAL.value,
        type=MetricType.COUNTER,
        description="重试次数",
        labels=["service", "error_type", "attempt_number"],
    ),
    MetricDefinition(
        name=MetricName.RETRY_EXHAUSTED_TOTAL.value,
        type=MetricType.COUNTER,
        description="重试耗尽次数",
        labels=["service", "error_code"],
    ),
    MetricDefinition(
        name=MetricName.FALLBACKS_TOTAL.value,
        type=MetricType.COUNTER,
        description="降级触发次数",
        labels=["service", "from_provider", "to_provider", "fallback_level"],
    ),
    MetricDefinition(
        name=MetricName.FALLBACK_SUCCESS_RATE.value,
        type=MetricType.GAUGE,
        description="降级成功率",
        labels=["service", "fallback_level"],
    ),
    MetricDefinition(
        name=MetricName.FALLBACK_CHAIN_EXHAUSTED.value,
        type=MetricType.COUNTER,
        description="降级链耗尽次数",
        labels=["service"],
    ),
    MetricDefinition(
        name=MetricName.CIRCUIT_BREAKER_STATE.value,
        type=MetricType.GAUGE,
        description="熔断器状态 (0=CLOSED, 1=HALF_OPEN, 2=OPEN)",
        labels=["service", "provider"],
    ),
    MetricDefinition(
        name=MetricName.CIRCUIT_BREAKS_TOTAL.value,
        type=MetricType.COUNTER,
        description="熔断触发次数",
        labels=["service", "provider"],
    ),
    MetricDefinition(
        name=MetricName.PROVIDER_HEALTH.value,
        type=MetricType.GAUGE,
        description="Provider 健康状态 (0=unhealthy, 1=healthy)",
        labels=["service", "provider"],
    ),
    MetricDefinition(
        name=MetricName.PROVIDER_ERROR_RATE.value,
        type=MetricType.GAUGE,
        description="Provider 错误率 (0.0-1.0)",
        labels=["service", "provider"],
    ),
    MetricDefinition(
        name=MetricName.PROVIDER_LATENCY_MS.value,
        type=MetricType.GAUGE,
        description="Provider 平均延迟(毫秒)",
        labels=["service", "provider"],
    ),
    MetricDefinition(
        name=MetricName.QUOTA_USAGE_RATIO.value,
        type=MetricType.GAUGE,
        description="配额使用率 (0.0-1.0)",
        labels=["quota_type", "provider"],
    ),
    MetricDefinition(
        name=MetricName.QUOTA_REMAINING.value,
        type=MetricType.GAUGE,
        description="剩余配额",
        labels=["quota_type", "provider"],
    ),
    MetricDefinition(
        name=MetricName.TEMPLATE_USAGE.value,
        type=MetricType.COUNTER,
        description="模板使用次数",
        labels=["template_id", "template_category"],
    ),
    MetricDefinition(
        name=MetricName.VIDEO_GENERATIONS.value,
        type=MetricType.COUNTER,
        description="视频生成次数",
        labels=["provider", "model", "status", "resolution"],
    ),
    MetricDefinition(
        name=MetricName.VIDEO_GEN_DURATION.value,
        type=MetricType.HISTOGRAM,
        description="视频生成耗时分布(秒)",
        labels=["provider", "model"],
        buckets=[10.0, 30.0, 60.0, 120.0, 180.0, 300.0, 600.0],
    ),
    MetricDefinition(
        name=MetricName.GEM_USAGE.value,
        type=MetricType.COUNTER,
        description="Gem 使用次数",
        labels=["gem_id", "gem_category"],
    ),
]

CIRCUIT_STATE_MAP = {"closed": 0, "half_open": 1, "open": 2}
BOOL_MAP = {True: 1, False: 0}


# ============================================================================
# 内存指标存储 (Prometheus 不可用时的降级方案)
# ============================================================================


@dataclass
class InMemoryCounter:
    """内存计数器实现

    当 prometheus_client 未安装时，使用此类替代 Prometheus Counter。
    线程安全由外部 MetricsCollector._lock 保证。
    """

    name: str
    description: str
    labels: List[str]
    values: Dict[tuple, float] = field(
        default_factory=lambda: defaultdict(float)
    )

    def inc(self: "InMemoryCounter", amount: float = 1, **label_values: str) -> None:
        """增加计数器值"""
        key = tuple(label_values.get(l, "") for l in self.labels)
        self.values[key] += amount

    def get_value(self: "InMemoryCounter", **label_values: str) -> float:
        """获取指定标签的计数器值"""
        key = tuple(label_values.get(l, "") for l in self.labels)
        return self.values.get(key, 0)

    def get_all(self: "InMemoryCounter") -> Dict[tuple, float]:
        """获取所有标签组合的计数器值"""
        return dict(self.values)


@dataclass
class InMemoryGauge:
    """内存仪表盘实现

    当 prometheus_client 未安装时，使用此类替代 Prometheus Gauge。
    """

    name: str
    description: str
    labels: List[str]
    values: Dict[tuple, float] = field(default_factory=dict)

    def set(self: "InMemoryGauge", value: float, **label_values: str) -> None:
        """设置仪表盘值"""
        key = tuple(label_values.get(l, "") for l in self.labels)
        self.values[key] = value

    def inc(self: "InMemoryGauge", amount: float = 1, **label_values: str) -> None:
        """增加仪表盘值"""
        key = tuple(label_values.get(l, "") for l in self.labels)
        self.values[key] = self.values.get(key, 0) + amount

    def dec(self: "InMemoryGauge", amount: float = 1, **label_values: str) -> None:
        """减少仪表盘值"""
        key = tuple(label_values.get(l, "") for l in self.labels)
        self.values[key] = self.values.get(key, 0) - amount

    def get_value(self: "InMemoryGauge", **label_values: str) -> float:
        """获取指定标签的仪表盘值"""
        key = tuple(label_values.get(l, "") for l in self.labels)
        return self.values.get(key, 0)


@dataclass
class InMemoryHistogram:
    """内存直方图实现

    当 prometheus_client 未安装时，使用此类替代 Prometheus Histogram。
    """

    name: str
    description: str
    labels: List[str]
    buckets: List[float]
    observations: Dict[tuple, List[float]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def observe(self: "InMemoryHistogram", value: float, **label_values: str) -> None:
        """记录一个观测值"""
        key = tuple(label_values.get(l, "") for l in self.labels)
        self.observations[key].append(value)

    def get_buckets(self: "InMemoryHistogram", **label_values: str) -> Dict[str, int]:
        """获取指定标签的分桶统计"""
        key = tuple(label_values.get(l, "") for l in self.labels)
        values = self.observations.get(key, [])

        result: Dict[str, int] = {}
        for bucket in self.buckets:
            result[f"le_{bucket}"] = sum(1 for v in values if v <= bucket)
        result["le_inf"] = len(values)
        return result

    def get_count(self: "InMemoryHistogram", **label_values: str) -> int:
        """获取观测次数"""
        key = tuple(label_values.get(l, "") for l in self.labels)
        return len(self.observations.get(key, []))

    def get_sum(self: "InMemoryHistogram", **label_values: str) -> float:
        """获取观测值总和"""
        key = tuple(label_values.get(l, "") for l in self.labels)
        return sum(self.observations.get(key, []))


# ============================================================================
# MetricsCollector - 主收集器
# ============================================================================


class MetricsCollector:
    """监控指标收集器 - 中央指标管理

    统一管理所有 Prometheus 指标，提供类型安全的方法记录各类事件。
    自动处理 Prometheus 依赖: 如果 prometheus_client 未安装，
    使用内存存储作为降级方案。

    Attributes:
        _use_prometheus: 是否使用 Prometheus 客户端
        _registry: Prometheus CollectorRegistry 或 None
        _metrics: 指标名称 -> 指标对象 的映射
        _request_history: 请求历史记录列表

    使用示例:
        collector = MetricsCollector()
        collector.record_request("video_gen", "seedance", "generate", 5234.0, True)
        snapshot = collector.get_stats()
    """

    def __init__(self: "MetricsCollector", registry: Optional[Any] = None) -> None:
        self._lock = threading.Lock()
        self._use_prometheus = PROMETHEUS_AVAILABLE

        if self._use_prometheus:
            self._registry = registry or CollectorRegistry()
        else:
            self._registry = None

        self._metrics: Dict[str, Any] = {}
        self._request_history: List[Dict[str, Any]] = []
        self._max_history_size = 10000
        self._fallback_stats: Dict[str, Dict[str, int]] = {}

        self._init_metrics()

    def _init_metrics(self: "MetricsCollector") -> None:
        """初始化所有预定义指标"""
        for definition in PREDEFINED_METRICS:
            self._create_metric(definition)

    def _create_metric(self: "MetricsCollector", definition: MetricDefinition) -> Any:
        """创建单个指标

        Args:
            definition: 指标定义

        Returns:
            指标对象 (Prometheus 或内存实现)
        """
        name = definition.name

        if name in self._metrics:
            return self._metrics[name]

        if self._use_prometheus:
            metric = self._create_prometheus_metric(definition)
        else:
            metric = self._create_memory_metric(definition)

        self._metrics[name] = metric
        return metric

    def _create_prometheus_metric(self: "MetricsCollector", definition: MetricDefinition) -> Any:
        """创建 Prometheus 指标

        Args:
            definition: 指标定义

        Returns:
            Prometheus 指标对象
        """
        if definition.type == MetricType.COUNTER:
            return Counter(
                definition.name,
                definition.description,
                definition.labels,
                registry=self._registry,
            )
        elif definition.type == MetricType.GAUGE:
            return Gauge(
                definition.name,
                definition.description,
                definition.labels,
                registry=self._registry,
            )
        elif definition.type == MetricType.HISTOGRAM:
            buckets = definition.buckets or [0.1, 0.5, 1.0, 5.0, 10.0]
            return Histogram(
                definition.name,
                definition.description,
                definition.labels,
                buckets=buckets,
                registry=self._registry,
            )
        return None

    def _create_memory_metric(self: "MetricsCollector", definition: MetricDefinition) -> Any:
        """创建内存指标（降级方案）

        Args:
            definition: 指标定义

        Returns:
            内存指标对象
        """
        if definition.type == MetricType.COUNTER:
            return InMemoryCounter(
                name=definition.name,
                description=definition.description,
                labels=definition.labels,
            )
        elif definition.type == MetricType.GAUGE:
            return InMemoryGauge(
                name=definition.name,
                description=definition.description,
                labels=definition.labels,
            )
        elif definition.type == MetricType.HISTOGRAM:
            return InMemoryHistogram(
                name=definition.name,
                description=definition.description,
                labels=definition.labels,
                buckets=definition.buckets or [0.1, 0.5, 1.0, 5.0, 10.0],
            )
        return None

    def _get_metric(self: "MetricsCollector", name: str) -> Any:
        """获取指标对象

        Args:
            name: 指标名称

        Returns:
            指标对象或 None
        """
        return self._metrics.get(name)

    # 请求指标

    @contextmanager
    def measure_request(self: "MetricsCollector", service: str, provider: str, method: str):
        """测量请求耗时的上下文管理器

        自动记录请求开始(进行中+1)、请求结束(进行中-1, 延迟, 状态)。

        Args:
            service: 服务名称
            provider: Provider 名称
            method: 方法名

        Yields:
            self (MetricsCollector 实例)

        示例:
            with collector.measure_request("video_gen", "seedance", "generate"):
                result = await generate_video(request)
        """
        self._requests_in_flight_inc(service)
        start_time = time.monotonic()

        try:
            yield self
            latency_ms = (time.monotonic() - start_time) * 1000
            self.record_request(service, provider, method, latency_ms, True)
        except Exception as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            error_code = getattr(e, "error_code", "unknown")
            self.record_request(
                service, provider, method, latency_ms, False, error_code
            )
            raise
        finally:
            self._requests_in_flight_dec(service)

    def _requests_in_flight_inc(self: "MetricsCollector", service: str) -> None:
        """增加进行中请求计数"""
        metric = self._get_metric(MetricName.REQUESTS_IN_FLIGHT.value)
        if metric:
            if self._use_prometheus:
                metric.labels(service=service).inc()
            else:
                metric.inc(service=service)

    def _requests_in_flight_dec(self: "MetricsCollector", service: str) -> None:
        """减少进行中请求计数"""
        metric = self._get_metric(MetricName.REQUESTS_IN_FLIGHT.value)
        if metric:
            if self._use_prometheus:
                metric.labels(service=service).dec()
            else:
                metric.dec(service=service)

    def record_request(
        self,
        service: str,
        provider: str,
        method: str,
        latency_ms: float,
        success: bool,
        error_code: str = "none",
    ) -> None:
        """记录请求指标 - 同时更新 Counter(请求总数) 和 Histogram(延迟分布)

        Args:
            service: 服务名称
            provider: Provider 名称
            method: 方法名
            latency_ms: 请求耗时(毫秒)
            success: 是否成功
            error_code: 错误码（失败时）
        """
        status = "success" if success else "error"
        latency_sec = latency_ms / 1000.0

        with self._lock:
            self._request_history.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "service": service,
                    "provider": provider,
                    "method": method,
                    "latency_ms": latency_ms,
                    "success": success,
                    "error_code": error_code,
                }
            )
            if len(self._request_history) > self._max_history_size:
                self._request_history = self._request_history[
                    -self._max_history_size :
                ]

        # Counter: 请求总数
        counter = self._get_metric(MetricName.REQUESTS_TOTAL.value)
        if counter:
            if self._use_prometheus:
                counter.labels(
                    service=service,
                    provider=provider,
                    status=status,
                    error_code=error_code if not success else "none",
                ).inc()
            else:
                counter.inc(
                    service=service,
                    provider=provider,
                    status=status,
                    error_code=error_code if not success else "none",
                )

        # Histogram: 请求延迟
        histogram = self._get_metric(MetricName.REQUEST_DURATION.value)
        if histogram:
            if self._use_prometheus:
                histogram.labels(
                    service=service, provider=provider, method=method
                ).observe(latency_sec)
            else:
                histogram.observe(
                    latency_sec,
                    service=service,
                    provider=provider,
                    method=method,
                )

    # 重试指标

    def record_retry_attempt(
        self, service: str, error_type: str, attempt: int
    ) -> None:
        """记录重试尝试

        Args:
            service: 服务名称
            error_type: 错误类型
            attempt: 尝试次数
        """
        metric = self._get_metric(MetricName.RETRIES_TOTAL.value)
        if metric:
            if self._use_prometheus:
                metric.labels(
                    service=service,
                    error_type=error_type,
                    attempt_number=str(attempt),
                ).inc()
            else:
                metric.inc(
                    service=service,
                    error_type=error_type,
                    attempt_number=str(attempt),
                )

    def record_retry_exhausted(self: "MetricsCollector", service: str, error_code: str) -> None:
        """记录重试耗尽

        Args:
            service: 服务名称
            error_code: 错误码
        """
        metric = self._get_metric(MetricName.RETRY_EXHAUSTED_TOTAL.value)
        if metric:
            if self._use_prometheus:
                metric.labels(service=service, error_code=error_code).inc()
            else:
                metric.inc(service=service, error_code=error_code)

    # 降级指标

    def record_fallback(
        self,
        service: str,
        from_provider: str,
        to_provider: str,
        level: str,
    ) -> None:
        """记录降级事件

        Args:
            service: 服务名称
            from_provider: 原 Provider
            to_provider: 目标 Provider
            level: 降级级别
        """
        metric = self._get_metric(MetricName.FALLBACKS_TOTAL.value)
        if metric:
            if self._use_prometheus:
                metric.labels(
                    service=service,
                    from_provider=from_provider,
                    to_provider=to_provider,
                    fallback_level=level,
                ).inc()
            else:
                metric.inc(
                    service=service,
                    from_provider=from_provider,
                    to_provider=to_provider,
                    fallback_level=level,
                )

    def record_fallback_success(
        self, service: str, level: str, success: bool
    ) -> None:
        """记录降级结果，用于计算成功率

        Args:
            service: 服务名称
            level: 降级级别
            success: 是否成功
        """
        key = f"{service}:{level}"
        if key not in self._fallback_stats:
            self._fallback_stats[key] = {"total": 0, "success": 0}

        self._fallback_stats[key]["total"] += 1
        if success:
            self._fallback_stats[key]["success"] += 1

        gauge = self._get_metric(MetricName.FALLBACK_SUCCESS_RATE.value)
        if gauge:
            stats = self._fallback_stats[key]
            rate = stats["success"] / stats["total"] if stats["total"] > 0 else 0
            if self._use_prometheus:
                gauge.labels(service=service, fallback_level=level).set(rate)
            else:
                gauge.set(rate, service=service, fallback_level=level)

    def record_fallback_chain_exhausted(self: "MetricsCollector", service: str) -> None:
        """记录降级链耗尽

        Args:
            service: 服务名称
        """
        metric = self._get_metric(MetricName.FALLBACK_CHAIN_EXHAUSTED.value)
        if metric:
            if self._use_prometheus:
                metric.labels(service=service).inc()
            else:
                metric.inc(service=service)

    # 熔断指标

    def record_circuit_breaker_state(
        self,
        service: str,
        provider: str,
        state: str,
        previous_state: str = "",
    ) -> None:
        """记录熔断器状态变化

        Args:
            service: 服务名称
            provider: Provider 名称
            state: 当前状态 (open/closed/half_open)
            previous_state: 之前的状态
        """
        gauge = self._get_metric(MetricName.CIRCUIT_BREAKER_STATE.value)
        if gauge:
            state_value = CIRCUIT_STATE_MAP.get(state, 0)
            if self._use_prometheus:
                gauge.labels(service=service, provider=provider).set(state_value)
            else:
                gauge.set(state_value, service=service, provider=provider)

        if state == "open" and previous_state != "open":
            counter = self._get_metric(MetricName.CIRCUIT_BREAKS_TOTAL.value)
            if counter:
                if self._use_prometheus:
                    counter.labels(service=service, provider=provider).inc()
                else:
                    counter.inc(service=service, provider=provider)

    # Provider 健康指标

    def record_provider_health(
        self, service: str, provider: str, healthy: bool
    ) -> None:
        """记录 Provider 健康状态

        Args:
            service: 服务名称
            provider: Provider 名称
            healthy: 是否健康
        """
        gauge = self._get_metric(MetricName.PROVIDER_HEALTH.value)
        if gauge:
            value = BOOL_MAP.get(healthy, 0)
            if self._use_prometheus:
                gauge.labels(service=service, provider=provider).set(value)
            else:
                gauge.set(value, service=service, provider=provider)

    def record_provider_latency(
        self, service: str, provider: str, latency_ms: float
    ) -> None:
        """记录 Provider 延迟

        Args:
            service: 服务名称
            provider: Provider 名称
            latency_ms: 延迟(毫秒)
        """
        gauge = self._get_metric(MetricName.PROVIDER_LATENCY_MS.value)
        if gauge:
            if self._use_prometheus:
                gauge.labels(service=service, provider=provider).set(latency_ms)
            else:
                gauge.set(latency_ms, service=service, provider=provider)

    def record_provider_error_rate(
        self, service: str, provider: str, error_rate: float
    ) -> None:
        """记录 Provider 错误率

        Args:
            service: 服务名称
            provider: Provider 名称
            error_rate: 错误率 (0.0-1.0)
        """
        gauge = self._get_metric(MetricName.PROVIDER_ERROR_RATE.value)
        if gauge:
            if self._use_prometheus:
                gauge.labels(service=service, provider=provider).set(error_rate)
            else:
                gauge.set(error_rate, service=service, provider=provider)

    # 配额指标

    def record_quota_usage(
        self,
        quota_type: str,
        provider: str,
        usage_ratio: float,
        remaining: int = 0,
    ) -> None:
        """记录配额使用情况

        Args:
            quota_type: 配额类型
            provider: Provider 名称
            usage_ratio: 使用率 (0.0-1.0)
            remaining: 剩余配额
        """
        usage_gauge = self._get_metric(MetricName.QUOTA_USAGE_RATIO.value)
        if usage_gauge:
            if self._use_prometheus:
                usage_gauge.labels(
                    quota_type=quota_type, provider=provider
                ).set(usage_ratio)
            else:
                usage_gauge.set(
                    usage_ratio, quota_type=quota_type, provider=provider
                )

        remaining_gauge = self._get_metric(MetricName.QUOTA_REMAINING.value)
        if remaining_gauge:
            if self._use_prometheus:
                remaining_gauge.labels(
                    quota_type=quota_type, provider=provider
                ).set(remaining)
            else:
                remaining_gauge.set(
                    remaining, quota_type=quota_type, provider=provider
                )

    # 业务指标

    def record_task(self: "MetricsCollector", status: str, **labels: str) -> None:
        """记录任务执行状态

        Args:
            status: 任务状态
            **labels: 额外标签
        """
        counter = self._get_metric(MetricName.REQUESTS_TOTAL.value)
        if counter:
            service = labels.get("service", "unknown")
            provider = labels.get("provider", "unknown")
            if self._use_prometheus:
                counter.labels(
                    service=service,
                    provider=provider,
                    status=status,
                    error_code="none",
                ).inc()
            else:
                counter.inc(
                    service=service,
                    provider=provider,
                    status=status,
                    error_code="none",
                )

    def record_template_usage(self: "MetricsCollector", template_id: str, category: str) -> None:
        """记录模板使用

        Args:
            template_id: 模板 ID
            category: 模板分类
        """
        metric = self._get_metric(MetricName.TEMPLATE_USAGE.value)
        if metric:
            if self._use_prometheus:
                metric.labels(
                    template_id=template_id, template_category=category
                ).inc()
            else:
                metric.inc(template_id=template_id, template_category=category)

    def record_video_generation(
        self,
        provider: str,
        model: str,
        success: bool,
        resolution: str,
        duration_sec: float = 0,
    ) -> None:
        """记录视频生成

        Args:
            provider: Provider 名称
            model: 模型名称
            success: 是否成功
            resolution: 分辨率
            duration_sec: 生成耗时(秒)
        """
        status = "success" if success else "failed"

        counter = self._get_metric(MetricName.VIDEO_GENERATIONS.value)
        if counter:
            if self._use_prometheus:
                counter.labels(
                    provider=provider,
                    model=model,
                    status=status,
                    resolution=resolution,
                ).inc()
            else:
                counter.inc(
                    provider=provider,
                    model=model,
                    status=status,
                    resolution=resolution,
                )

        if duration_sec > 0:
            histogram = self._get_metric(MetricName.VIDEO_GEN_DURATION.value)
            if histogram:
                if self._use_prometheus:
                    histogram.labels(provider=provider, model=model).observe(
                        duration_sec
                    )
                else:
                    histogram.observe(
                        duration_sec, provider=provider, model=model
                    )

    def record_gem_usage(self: "MetricsCollector", gem_id: str, category: str) -> None:
        """记录 Gem 使用

        Args:
            gem_id: Gem ID
            category: Gem 分类
        """
        metric = self._get_metric(MetricName.GEM_USAGE.value)
        if metric:
            if self._use_prometheus:
                metric.labels(gem_id=gem_id, gem_category=category).inc()
            else:
                metric.inc(gem_id=gem_id, gem_category=category)

    # 导出方法

    def export_prometheus(self: "MetricsCollector") -> bytes:
        """导出 Prometheus 格式的指标数据

        Returns:
            Prometheus exposition 格式的字节串
        """
        if not self._use_prometheus:
            return self._export_memory_prometheus_format()

        return generate_latest(self._registry)

    def get_content_type(self: "MetricsCollector") -> str:
        """获取 Prometheus Content-Type

        Returns:
            Content-Type 字符串
        """
        if self._use_prometheus:
            return CONTENT_TYPE_LATEST
        return "text/plain; version=0.0.4; charset=utf-8"

    def _export_memory_prometheus_format(self: "MetricsCollector") -> bytes:
        """将内存指标导出为 Prometheus 文本格式

        Returns:
            Prometheus exposition 格式的字节串
        """
        lines: List[str] = []

        for name, metric in self._metrics.items():
            lines.append(f"# HELP {metric.name} {metric.description}")

            if isinstance(metric, InMemoryCounter):
                lines.append(f"# TYPE {metric.name} counter")
                for label_key, value in metric.values.items():
                    label_str = self._format_labels(metric.labels, label_key)
                    lines.append(f"{metric.name}{label_str} {value}")

            elif isinstance(metric, InMemoryGauge):
                lines.append(f"# TYPE {metric.name} gauge")
                for label_key, value in metric.values.items():
                    label_str = self._format_labels(metric.labels, label_key)
                    lines.append(f"{metric.name}{label_str} {value}")

            elif isinstance(metric, InMemoryHistogram):
                lines.append(f"# TYPE {metric.name} histogram")
                for label_key, observations in metric.observations.items():
                    label_str = self._format_labels(metric.labels, label_key)
                    if label_str:
                        # 有标签的情况
                        prefix = f"{metric.name}_bucket" + label_str[:-1]
                        suffix = "}"
                        sum_label_str = label_str
                        count_label_str = label_str
                    else:
                        # 无标签的情况
                        prefix = f"{metric.name}_bucket" + "{"
                        suffix = "}"
                        sum_label_str = ""
                        count_label_str = ""

                    for bucket in metric.buckets:
                        count = sum(1 for v in observations if v <= bucket)
                        lines.append(
                            f'{prefix}le="{bucket}"{suffix} {count}'
                        )
                    lines.append(
                        f'{prefix}le="+Inf"{suffix} {len(observations)}'
                    )

                    if sum_label_str:
                        lines.append(
                            f"{metric.name}_sum{sum_label_str} {sum(observations)}"
                        )
                        lines.append(
                            f"{metric.name}_count{count_label_str} {len(observations)}"
                        )
                    else:
                        lines.append(
                            f"{metric.name}_sum {sum(observations)}"
                        )
                        lines.append(
                            f"{metric.name}_count {len(observations)}"
                        )

            lines.append("")

        return "\n".join(lines).encode("utf-8")

    def _format_labels(
        self, label_names: List[str], label_values: tuple
    ) -> str:
        """格式化标签为 Prometheus 格式

        Args:
            label_names: 标签名称列表
            label_values: 标签值元组

        Returns:
            Prometheus 格式的标签字符串，如 {service="video_gen",status="success"}
        """
        parts = []
        for name, value in zip(label_names, label_values):
            if value:
                parts.append(f'{name}="{value}"')
        if parts:
            return "{" + ",".join(parts) + "}"
        return ""

    def get_stats(self: "MetricsCollector") -> MetricsSnapshot:
        """获取指标快照 - 汇总当前所有指标的状态

        Returns:
            MetricsSnapshot 快照对象
        """
        snapshot = MetricsSnapshot()

        with self._lock:
            if self._request_history:
                snapshot.total_requests = len(self._request_history)
                snapshot.success_count = sum(
                    1 for r in self._request_history if r["success"]
                )
                snapshot.error_count = (
                    snapshot.total_requests - snapshot.success_count
                )
                snapshot.success_rate = (
                    snapshot.success_count / snapshot.total_requests
                    if snapshot.total_requests > 0
                    else 0
                )
                snapshot.avg_latency_ms = sum(
                    r["latency_ms"] for r in self._request_history
                ) / len(self._request_history)

        in_flight = self._get_metric(MetricName.REQUESTS_IN_FLIGHT.value)
        if in_flight and not self._use_prometheus:
            snapshot.active_requests = sum(in_flight.values.values())

        health_gauge = self._get_metric(MetricName.PROVIDER_HEALTH.value)
        latency_gauge = self._get_metric(MetricName.PROVIDER_LATENCY_MS.value)
        error_rate_gauge = self._get_metric(MetricName.PROVIDER_ERROR_RATE.value)

        if health_gauge and not self._use_prometheus:
            for key, healthy_value in health_gauge.values.items():
                if len(key) >= 2:
                    service, provider = key[0], key[1]
                    health = ProviderHealth(
                        provider=provider,
                        service=service,
                        healthy=bool(healthy_value),
                        avg_latency_ms=latency_gauge.get_value(
                            service=service, provider=provider
                        )
                        if latency_gauge
                        else 0,
                        error_rate=error_rate_gauge.get_value(
                            service=service, provider=provider
                        )
                        if error_rate_gauge
                        else 0,
                    )
                    snapshot.provider_health.append(health)

        return snapshot


# ============================================================================
# 便捷函数
# ============================================================================

_global_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """获取全局 MetricsCollector 实例（懒加载）

    Returns:
        MetricsCollector 全局实例
    """
    global _global_collector
    if _global_collector is None:
        _global_collector = MetricsCollector()
    return _global_collector


def set_metrics_collector(collector: MetricsCollector) -> None:
    """设置全局 MetricsCollector 实例

    Args:
        collector: MetricsCollector 实例
    """
    global _global_collector
    _global_collector = collector
