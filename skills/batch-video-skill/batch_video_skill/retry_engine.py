# -*- coding: utf-8 -*-
"""
智能重试引擎

位置: batch_video_skill/infrastructure/retry_engine.py
功能: 4种退避策略 + 自动重试判断 + 熔断器集成 + 指标上报

设计原则:
1. execute() 为核心入口，统一处理重试逻辑
2. 退避策略可配置: 指数/线性/固定/自适应
3. 优先使用服务端 Retry-After 头
4. 熔断器检查在每次尝试前执行
5. 所有操作均有日志和指标记录
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Type

from exceptions import (
    BackoffStrategy,
    ErrorContext,
    RateLimitError,
    ServiceUnavailableError,
    SkillException,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 重试策略
# ============================================================================


@dataclass
class RetryPolicy:
    """
    重试策略配置

    Attributes:
        max_attempts: 最大重试次数(不含首次尝试)，默认 3
        base_delay: 基础延迟(秒)，默认 1.0
        max_delay: 最大延迟上限(秒)，默认 60.0
        strategy: 退避策略，默认 EXPONENTIAL
        jitter_factor: 抖动因子(0-1)，默认 0.5
        retryable_exceptions: 允许重试的异常类型列表
        on_retry: 重试回调函数 (attempt, max_attempts, delay, exception)
        on_exhausted: 重试耗尽回调函数 (max_attempts, last_exception)

    Methods:
        calculate_delay(attempt, error_rate) -> float: 计算第N次等待时间

    Example:
        >>> policy = RetryPolicy(max_attempts=3, base_delay=1.0)
        >>> policy.calculate_delay(0)  # 首次重试
        1.23  # ~1.0 * 2^0 + jitter
        >>> policy.calculate_delay(1)  # 第二次重试
        2.87  # ~1.0 * 2^1 + jitter
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    jitter_factor: float = 0.5
    retryable_exceptions: List[Type[Exception]] = field(
        default_factory=lambda: [SkillException]
    )
    on_retry: Optional[Callable] = None
    on_exhausted: Optional[Callable] = None

    def __post_init__(self: "RetryPolicy") -> None:
        """参数校验"""
        if self.max_attempts < 0:
            raise ValueError(
                f"max_attempts must be >= 0, got {self.max_attempts}"
            )
        if self.base_delay < 0:
            raise ValueError(f"base_delay must be >= 0, got {self.base_delay}")
        if self.max_delay < self.base_delay:
            raise ValueError(
                f"max_delay ({self.max_delay}) must be >= base_delay ({self.base_delay})"
            )
        if not (0 <= self.jitter_factor <= 1):
            raise ValueError(
                f"jitter_factor must be 0-1, got {self.jitter_factor}"
            )

    def calculate_delay(self: "RetryPolicy", attempt: int, error_rate: float = 0.0) -> float:
        """
        计算第 attempt 次重试的等待时间(秒)

        Args:
            attempt: 当前尝试次数(从0开始)
            error_rate: 当前错误率(0-1，用于自适应策略)

        Returns:
            等待时间(秒)，已包含抖动，不超过 max_delay

        Algorithm by strategy:
            EXPONENTIAL: delay = base * 2^attempt + jitter
            LINEAR:      delay = base * (attempt + 1) + jitter
            FIXED:       delay = base + jitter
            ADAPTIVE:    delay = base * (1 + error_rate * 10) * 2^min(attempt,5) + jitter

        Jitter formula:
            jitter = delay * jitter_factor * random(0, 1)
        """
        if self.strategy == BackoffStrategy.EXPONENTIAL:
            delay = self.base_delay * (2 ** attempt)
        elif self.strategy == BackoffStrategy.LINEAR:
            delay = self.base_delay * (attempt + 1)
        elif self.strategy == BackoffStrategy.FIXED:
            delay = self.base_delay
        elif self.strategy == BackoffStrategy.ADAPTIVE:
            delay = (
                self.base_delay
                * (1 + error_rate * 10)
                * (2 ** min(attempt, 5))
            )
        else:
            delay = self.base_delay

        # 添加抖动避免惊群效应
        if self.jitter_factor > 0:
            jitter = delay * self.jitter_factor * random.random()
            delay = delay + jitter

        return min(delay, self.max_delay)


# ============================================================================
# 预设策略配置
# ============================================================================

RETRY_POLICIES: Dict[str, RetryPolicy] = {
    "api_call": RetryPolicy(  # 通用API调用 - 快速失败
        max_attempts=3,
        base_delay=1.0,
        max_delay=30.0,
        strategy=BackoffStrategy.EXPONENTIAL,
    ),
    "video_generation": RetryPolicy(  # 视频生成 - 耐心重试
        max_attempts=5,
        base_delay=5.0,
        max_delay=180.0,
        strategy=BackoffStrategy.EXPONENTIAL,
    ),
    "image_generation": RetryPolicy(  # 图片生成 - 适中重试
        max_attempts=3,
        base_delay=2.0,
        max_delay=60.0,
        strategy=BackoffStrategy.EXPONENTIAL,
    ),
    "chat": RetryPolicy(  # 对话 - 快速重试
        max_attempts=3,
        base_delay=0.5,
        max_delay=10.0,
        strategy=BackoffStrategy.EXPONENTIAL,
    ),
    "auth_refresh": RetryPolicy(  # 认证刷新 - 谨慎固定间隔
        max_attempts=3,
        base_delay=2.0,
        max_delay=30.0,
        strategy=BackoffStrategy.FIXED,
        jitter_factor=0.2,
    ),
    "network": RetryPolicy(  # 网络请求 - 快速恢复
        max_attempts=5,
        base_delay=1.0,
        max_delay=30.0,
        strategy=BackoffStrategy.EXPONENTIAL,
    ),
    "rate_limit": RetryPolicy(  # 速率限制 - 固定间隔
        max_attempts=10,
        base_delay=2.0,
        max_delay=120.0,
        strategy=BackoffStrategy.FIXED,
        jitter_factor=0.0,
    ),
}


def get_retry_policy(name: str) -> RetryPolicy:
    """
    获取预设重试策略

    Args:
        name: 策略名称 (api_call/video_generation/chat/...)

    Returns:
        RetryPolicy 实例 (找不到时返回 api_call 默认策略)
    """
    return RETRY_POLICIES.get(name, RETRY_POLICIES["api_call"])


# ============================================================================
# 重试引擎
# ============================================================================


class RetryEngine:
    """
    智能重试引擎

    核心执行器，自动处理重试逻辑:
    1. 执行目标函数
    2. 捕获异常 -> 判断是否可重试
    3. 计算等待时间 -> 记录日志 -> 等待
    4. 重复直到成功或重试耗尽

    Attributes:
        policy: RetryPolicy 重试策略配置
        circuit_breaker: CircuitBreaker 熔断器实例(可选)
        logger: logging.Logger 日志器(可选)
        metrics: MetricsCollector 指标收集器(可选)

    Methods:
        execute(fn, *args, **kwargs) -> Any: 执行函数，失败自动重试
        _is_retryable(exc) -> bool: 判断异常是否可重试
        _get_retry_after(exc) -> Optional[float]: 获取服务端建议等待时间
        _calculate_delay(attempt) -> float: 计算等待时间

    Example:
        >>> engine = RetryEngine(RETRY_POLICIES["api_call"])
        >>> result = await engine.execute(api_call, request)
        >>> # 失败会自动重试，最终仍失败则抛出异常
    """

    def __init__(
        self,
        policy: RetryPolicy,
        *,
        circuit_breaker: Optional[Any] = None,
        metrics: Optional[Any] = None,
    ) -> None:
        """
        初始化重试引擎

        Args:
            policy: 重试策略配置
            circuit_breaker: 熔断器实例(可选)
            metrics: 指标收集器(可选)
        """
        self.policy = policy
        self.circuit_breaker = circuit_breaker
        self.metrics = metrics
        # 自适应策略用的错误窗口(60秒滑动窗口)
        self._error_window: List[float] = []
        self._window_size = 60

    @property
    def _current_error_rate(self: "RetryEngine") -> float:
        """
        计算当前错误率(用于自适应策略)

        Returns:
            0.0-1.0 的错误率估计值
        """
        now = time.time()
        # 清理过期记录
        self._error_window = [
            t for t in self._error_window if now - t < self._window_size
        ]
        if not self._error_window:
            return 0.0
        # 简化的错误率估算
        return min(
            len(self._error_window) / max(len(self._error_window), 10), 1.0
        )

    def _is_retryable(self: "RetryEngine", exc: Exception) -> bool:
        """
        判断异常是否可重试

        判断优先级:
        1. SkillException 子类 -> 检查 retryable 属性
        2. 配置的重试异常类型列表 -> 类型匹配
        3. 默认 -> 不可重试

        Args:
            exc: 异常实例

        Returns:
            True 表示可重试，False 表示不可重试
        """
        # SkillException 子类检查 retryable 属性
        if isinstance(exc, SkillException):
            return exc.is_retryable
        # 检查是否在允许重试的异常类型列表中
        for exc_type in self.policy.retryable_exceptions:
            if isinstance(exc, exc_type):
                return True
        return False

    def _get_retry_after(self: "RetryEngine", exc: Exception) -> Optional[float]:
        """
        获取服务端建议的重试等待时间

        目前支持:
        - RateLimitError.retry_after 属性

        Args:
            exc: 异常实例

        Returns:
            等待时间(秒) 或 None (使用策略默认计算)
        """
        if isinstance(exc, RateLimitError):
            return exc.retry_after
        return None

    def _calculate_delay(self: "RetryEngine", attempt: int, exc: Exception) -> float:
        """
        计算重试等待时间

        优先级:
        1. 异常携带的 retry_after (如 RateLimitError)
        2. 策略默认计算

        Args:
            attempt: 当前尝试次数
            exc: 异常实例(用于提取 retry_after)

        Returns:
            等待时间(秒)
        """
        retry_after = self._get_retry_after(exc)
        if retry_after is not None:
            return retry_after
        return self.policy.calculate_delay(
            attempt, error_rate=self._current_error_rate
        )

    async def execute(
        self,
        fn: Callable[..., Any],
        *args: Any,
        context: Optional[ErrorContext] = None,
        **kwargs: Any,
    ) -> Any:
        """
        执行函数，失败时自动重试

        执行流程:
        1. 检查熔断器状态
        2. 执行目标函数
        3. 成功 -> 通知熔断器 + 返回结果
        4. 失败 -> 判断可重试性 -> 计算延迟 -> 等待 -> 重试
        5. 重试耗尽 -> 记录日志 + 抛出最终异常

        Args:
            fn: 要执行的异步函数
            *args: 位置参数
            context: 错误上下文(用于日志和追踪)
            **kwargs: 关键字参数

        Returns:
            函数返回值

        Raises:
            SkillException: 重试耗尽后的最终异常
            Exception: 不可重试的异常直接抛出

        Example:
            >>> async def api_call(endpoint: str) -> dict:
            ...     ...
            >>> engine = RetryEngine(RETRY_POLICIES["api_call"])
            >>> result = await engine.execute(api_call, "/generate")
        """
        trace_id = context.trace_id if context else ""
        service = context.service if context else ""
        last_exception: Optional[Exception] = None

        # attempt=0 为首次尝试，attempt>0 为重试
        for attempt in range(self.policy.max_attempts + 1):
            try:
                # === 步骤1: 检查熔断器 ===
                if (
                    self.circuit_breaker
                    and not self.circuit_breaker.can_execute()
                ):
                    raise self.circuit_breaker.create_exception()

                # === 步骤2: 执行目标函数 ===
                result = await fn(*args, **kwargs)

                # === 步骤3: 成功处理 ===
                if self.circuit_breaker:
                    self.circuit_breaker.record_success()

                # 记录重试成功(非首次尝试)
                if attempt > 0:
                    logger.info(
                        "retry_succeeded: trace_id=%s, attempt=%d/%d",
                        trace_id,
                        attempt,
                        self.policy.max_attempts,
                    )

                # 上报重试成功指标
                if self.metrics and attempt > 0:
                    self.metrics.record_retry_success(service)

                return result

            except Exception as exc:
                last_exception = exc

                # 通知熔断器记录失败
                if self.circuit_breaker:
                    self.circuit_breaker.record_failure()

                # 记录错误到窗口(用于自适应策略)
                self._error_window.append(time.time())

                # 最后一次尝试 -> 直接退出循环
                if attempt >= self.policy.max_attempts:
                    break

                # 检查是否可重试
                if not self._is_retryable(exc):
                    raise  # 不可重试，立即抛出

                # 计算等待时间
                delay = self._calculate_delay(attempt, exc)

                # 记录重试日志
                logger.warning(
                    "retry_scheduled: trace_id=%s, attempt=%d/%d, "
                    "delay=%.2fs, error=%s, error_code=%s",
                    trace_id,
                    attempt + 1,
                    self.policy.max_attempts,
                    delay,
                    str(exc),
                    getattr(exc, "error_code", "unknown"),
                )

                # 上报重试指标
                if self.metrics:
                    self.metrics.record_retry_attempt(
                        service,
                        getattr(exc, "error_code", type(exc).__name__),
                        attempt + 1,
                    )

                # 执行重试回调
                if self.policy.on_retry:
                    await self.policy.on_retry(
                        attempt=attempt + 1,
                        max_attempts=self.policy.max_attempts,
                        delay=delay,
                        exception=exc,
                    )

                # 等待后重试
                await asyncio.sleep(delay)

        # === 步骤5: 重试耗尽 ===
        logger.error(
            "retry_exhausted: trace_id=%s, max_attempts=%d, "
            "last_error=%s, error_code=%s",
            trace_id,
            self.policy.max_attempts,
            str(last_exception),
            getattr(last_exception, "error_code", "unknown"),
        )

        # 执行耗尽回调
        if self.policy.on_exhausted:
            await self.policy.on_exhausted(
                max_attempts=self.policy.max_attempts,
                last_exception=last_exception,
            )

        # 上报重试耗尽指标
        if self.metrics:
            self.metrics.record_retry_exhausted(
                service,
                getattr(last_exception, "error_code", "unknown"),
            )

        # 抛出最终异常
        if isinstance(last_exception, SkillException):
            raise last_exception
        raise last_exception  # type: ignore


# ============================================================================
# 重试装饰器
# ============================================================================


def with_retry(
    policy_name: str = "api_call",
    **policy_overrides: Any,
) -> Callable:
    """
    重试装饰器 - 零侵入集成

    使用预设策略，支持参数覆盖。

    Args:
        policy_name: 预设策略名称 (api_call/video_generation/chat/...)
        **policy_overrides: 策略参数覆盖 (max_attempts/base_delay/...)

    Returns:
        装饰器函数

    Example:
        >>> @with_retry("video_generation")
        ... async def generate_video(request) -> VideoResult:
        ...     ...

        >>> @with_retry("api_call", max_attempts=5, base_delay=2.0)
        ... async def api_call(endpoint: str) -> dict:
        ...     ...

        >>> # 运行时传递上下文
        >>> result = await generate_video(req, retry_context=ctx)
    """

    def decorator(fn: Callable) -> Callable:
        # 获取基础策略并应用覆盖
        base_policy = RETRY_POLICIES.get(
            policy_name, RETRY_POLICIES["api_call"]
        )
        policy = RetryPolicy(
            **{
                "max_attempts": base_policy.max_attempts,
                "base_delay": base_policy.base_delay,
                "max_delay": base_policy.max_delay,
                "strategy": base_policy.strategy,
                **policy_overrides,
            }
        )
        engine = RetryEngine(policy)

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            context = kwargs.pop("retry_context", None)
            return await engine.execute(
                fn, *args, context=context, **kwargs
            )

        # 附加 engine 以便外部访问
        wrapper._retry_engine = engine  # type: ignore
        return wrapper

    return decorator


# ============================================================================
# 模块导出
# ============================================================================

__all__ = [
    "RetryPolicy",
    "RetryEngine",
    "RETRY_POLICIES",
    "get_retry_policy",
    "with_retry",
]
