# -*- coding: utf-8 -*-
"""
熔断器实现 - 防止故障级联扩散

位置: batch_video_skill/infrastructure/circuit_breaker.py
功能: 三状态机 + 注册表管理 + 自动恢复

状态机:
    CLOSED (闭合)  -> 正常处理所有请求
    OPEN (断开)    -> 拒绝请求，快速失败
    HALF_OPEN (半开) -> 允许有限探测请求

状态转换:
    CLOSED -> OPEN:     失败率 >= threshold 且 采样数 >= min_samples
    OPEN -> HALF_OPEN:  open_duration 到期
    HALF_OPEN -> CLOSED: 探测成功率 >= threshold 且 探测数 >= min_calls
    HALF_OPEN -> OPEN:   任一探测失败

线程安全:
    使用 threading.Lock 保证线程安全。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from exceptions import (
    CircuitState,
    ErrorContext,
    ServiceUnavailableError,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 熔断器配置
# ============================================================================


@dataclass
class CircuitBreakerConfig:
    """
    熔断器配置

    Attributes:
        failure_threshold: 触发熔断的失败率阈值(0.1-0.9)
        min_samples: 计算失败率的最小采样数(5-100)
        open_duration: OPEN状态持续时间(秒，10-300)
        half_open_max_calls: 半开状态最大探测请求数(1-10)
        half_open_success_threshold: 半开状态恢复所需成功率(0.5-1.0)
        name: 熔断器名称标识

    Example:
        >>> config = CircuitBreakerConfig(
        ...     name="seedance", failure_threshold=0.5, open_duration=30.0
        ... )
    """

    failure_threshold: float = 0.5
    min_samples: int = 10
    open_duration: float = 30.0
    half_open_max_calls: int = 3
    half_open_success_threshold: float = 1.0
    name: str = "default"

    def __post_init__(self: "CircuitBreakerConfig") -> None:
        """参数校验"""
        if not (0.1 <= self.failure_threshold <= 0.9):
            raise ValueError(
                f"failure_threshold must be 0.1-0.9, got {self.failure_threshold}"
            )
        if not (5 <= self.min_samples <= 100):
            raise ValueError(
                f"min_samples must be 5-100, got {self.min_samples}"
            )
        if not (10 <= self.open_duration <= 300):
            raise ValueError(
                f"open_duration must be 10-300, got {self.open_duration}"
            )
        if not (1 <= self.half_open_max_calls <= 10):
            raise ValueError(
                f"half_open_max_calls must be 1-10, got {self.half_open_max_calls}"
            )
        if not (0.5 <= self.half_open_success_threshold <= 1.0):
            raise ValueError(
                f"half_open_success_threshold must be 0.5-1.0, got {self.half_open_success_threshold}"
            )


# ============================================================================
# 熔断器
# ============================================================================


class CircuitBreaker:
    """
    熔断器 - 三状态机实现

    自动状态转换:
    - CLOSED -> OPEN: 失败率超过阈值
    - OPEN -> HALF_OPEN: 断开持续时间到期
    - HALF_OPEN -> CLOSED: 探测请求全部成功
    - HALF_OPEN -> OPEN: 任一探测请求失败

    Attributes:
        config: CircuitBreakerConfig 配置

    Properties:
        state: CircuitState 当前状态(自动触发转换检查)
        failure_rate: float 当前失败率(0.0-1.0)

    Methods:
        can_execute() -> bool: 检查当前是否允许执行
        record_success(): 记录成功请求
        record_failure(): 记录失败请求
        create_exception() -> ServiceUnavailableError: 创建拒绝异常
        get_stats() -> Dict: 获取统计信息

    Example:
        >>> cb = CircuitBreaker(CircuitBreakerConfig(name="api"))
        >>> if cb.can_execute():
        ...     try:
        ...         result = await api_call()
        ...         cb.record_success()
        ...     except Exception:
        ...         cb.record_failure()
    """

    def __init__(self: "CircuitBreaker", config: Optional[CircuitBreakerConfig] = None) -> None:
        """
        初始化熔断器

        Args:
            config: 熔断器配置 (为 None 时使用默认配置)
        """
        self.config = config or CircuitBreakerConfig()

        # 状态
        self._state: CircuitState = CircuitState.CLOSED
        self._lock = threading.RLock()

        # 计数器
        self._failure_count: int = 0
        self._success_count: int = 0
        self._total_count: int = 0
        self._half_open_calls: int = 0
        self._half_open_success: int = 0

        # 时间戳
        self._last_failure_time: float = 0.0
        self._open_time: float = 0.0

    # ─── 属性 ───

    @property
    def state(self: "CircuitBreaker") -> CircuitState:
        """
        获取当前状态(自动触发状态转换检查)

        Returns:
            CircuitState 当前状态
        """
        self._check_state_transition()
        return self._state

    @property
    def failure_rate(self: "CircuitBreaker") -> float:
        """
        计算当前失败率

        Returns:
            0.0-1.0 的失败率
        """
        with self._lock:
            if self._total_count == 0:
                return 0.0
            return self._failure_count / self._total_count

    # ─── 公共方法 ───

    def can_execute(self: "CircuitBreaker") -> bool:
        """
        检查当前是否允许执行请求

        Returns:
            True 表示允许执行，False 表示拒绝(熔断器打开)
        """
        current_state = self.state  # 触发状态检查

        with self._lock:
            if current_state == CircuitState.CLOSED:
                return True
            elif current_state == CircuitState.OPEN:
                return False
            elif current_state == CircuitState.HALF_OPEN:
                return self._half_open_calls < self.config.half_open_max_calls
            return False

    def record_success(self: "CircuitBreaker") -> None:
        """记录成功请求"""
        with self._lock:
            self._success_count += 1
            self._total_count += 1

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_success += 1
                self._half_open_calls += 1

                # 检查是否满足恢复条件
                success_rate = self._half_open_success / self._half_open_calls
                if (
                    success_rate >= self.config.half_open_success_threshold
                    and self._half_open_calls >= self.config.half_open_max_calls
                ):
                    self._transition_to_closed()

    def record_failure(self: "CircuitBreaker") -> None:
        """记录失败请求"""
        with self._lock:
            self._failure_count += 1
            self._total_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls += 1
                # 半开状态任一失败 -> 回到 OPEN
                self._transition_to_open()
                return

            # 检查是否需要熔断
            self._check_should_open()

    def create_exception(self: "CircuitBreaker") -> ServiceUnavailableError:
        """
        创建熔断拒绝异常

        Returns:
            ServiceUnavailableError 实例
        """
        remaining = self._get_remaining_open_time()
        return ServiceUnavailableError(
            f"熔断器已打开 [{self.config.name}]，"
            f"请等待 {remaining:.0f} 秒后重试",
            context=ErrorContext(
                extra={
                    "circuit_breaker": self.config.name,
                    "state": self._state.value,
                    "remaining_sec": remaining,
                    "failure_rate": self.failure_rate,
                }
            ),
        )

    def get_stats(self: "CircuitBreaker") -> Dict[str, Any]:
        """
        获取熔断器统计信息

        Returns:
            包含状态、计数等信息的字典
        """
        with self._lock:
            return {
                "name": self.config.name,
                "state": self._state.value,
                "failure_rate": round(self.failure_rate, 4),
                "total_count": self._total_count,
                "success_count": self._success_count,
                "failure_count": self._failure_count,
                "half_open_calls": self._half_open_calls,
                "half_open_success": self._half_open_success,
                "last_failure_time": self._last_failure_time,
                "open_time": self._open_time,
                "remaining_open_time": self._get_remaining_open_time(),
            }

    def reset(self: "CircuitBreaker") -> None:
        """手动重置熔断器到 CLOSED 状态"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._reset_counts()
            logger.info("熔断器 [%s] 已手动重置", self.config.name)

    def __repr__(self: "CircuitBreaker") -> str:
        return (
            f"<CircuitBreaker(name={self.config.name}, "
            f"state={self._state.value}, "
            f"failure_rate={self.failure_rate:.2f})>"
        )

    # ─── 内部方法 ───

    def _check_state_transition(self: "CircuitBreaker") -> None:
        """检查并执行状态转换"""
        with self._lock:
            if self._state == CircuitState.OPEN:
                # 检查 OPEN 持续时间是否到期
                elapsed = time.time() - self._open_time
                if elapsed >= self.config.open_duration:
                    self._transition_to_half_open()

    def _check_should_open(self: "CircuitBreaker") -> None:
        """检查是否应该切换到 OPEN 状态"""
        if self._state != CircuitState.CLOSED:
            return

        if self._total_count < self.config.min_samples:
            return

        if self.failure_rate >= self.config.failure_threshold:
            self._transition_to_open()

    def _transition_to_open(self: "CircuitBreaker") -> None:
        """转换到 OPEN 状态"""
        if self._state == CircuitState.OPEN:
            return
        self._state = CircuitState.OPEN
        self._open_time = time.time()
        logger.warning(
            "熔断器 [%s] 已打开 (失败率: %.2f, 总请求: %d)",
            self.config.name,
            self.failure_rate,
            self._total_count,
        )

    def _transition_to_half_open(self: "CircuitBreaker") -> None:
        """转换到 HALF_OPEN 状态"""
        self._state = CircuitState.HALF_OPEN
        self._half_open_calls = 0
        self._half_open_success = 0
        logger.info(
            "熔断器 [%s] 进入半开状态，允许 %d 个探测请求",
            self.config.name,
            self.config.half_open_max_calls,
        )

    def _transition_to_closed(self: "CircuitBreaker") -> None:
        """转换到 CLOSED 状态"""
        self._state = CircuitState.CLOSED
        self._reset_counts()
        logger.info("熔断器 [%s] 已闭合，恢复正常服务", self.config.name)

    def _reset_counts(self: "CircuitBreaker") -> None:
        """重置计数器"""
        self._failure_count = 0
        self._success_count = 0
        self._total_count = 0
        self._half_open_calls = 0
        self._half_open_success = 0
        self._last_failure_time = 0.0
        self._open_time = 0.0

    def _get_remaining_open_time(self: "CircuitBreaker") -> float:
        """获取 OPEN 状态剩余时间(秒)"""
        if self._state != CircuitState.OPEN:
            return 0.0
        elapsed = time.time() - self._open_time
        return max(0.0, self.config.open_duration - elapsed)


# ============================================================================
# 熔断器注册表
# ============================================================================


class CircuitBreakerRegistry:
    """
    熔断器注册表 - 管理多个熔断器实例

    提供统一的熔断器管理接口:
    - 懒加载创建
    - 统一配置
    - 批量统计

    Attributes:
        _breakers: Dict[str, CircuitBreaker] 熔断器字典
        _default_config: CircuitBreakerConfig 默认配置

    Example:
        >>> registry = CircuitBreakerRegistry()
        >>> cb = registry.get("seedance")  # 懒加载创建
        >>> if cb.can_execute():
        ...     ...
    """

    def __init__(
        self,
        default_config: Optional[CircuitBreakerConfig] = None,
    ) -> None:
        """
        初始化熔断器注册表

        Args:
            default_config: 默认熔断器配置
        """
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._default_config = default_config or CircuitBreakerConfig()
        self._lock = threading.Lock()

    def get(self: "CircuitBreakerRegistry", name: str) -> CircuitBreaker:
        """
        获取或创建熔断器(懒加载)

        Args:
            name: 熔断器名称

        Returns:
            CircuitBreaker 实例
        """
        with self._lock:
            if name not in self._breakers:
                config = CircuitBreakerConfig(
                    **{
                        **{
                            k: getattr(self._default_config, k)
                            for k in [
                                "failure_threshold",
                                "min_samples",
                                "open_duration",
                                "half_open_max_calls",
                                "half_open_success_threshold",
                            ]
                        },
                        "name": name,
                    }
                )
                self._breakers[name] = CircuitBreaker(config)
            return self._breakers[name]

    def register(
        self, name: str, breaker: CircuitBreaker
    ) -> None:
        """
        注册熔断器

        Args:
            name: 熔断器名称
            breaker: CircuitBreaker 实例
        """
        with self._lock:
            self._breakers[name] = breaker

    def remove(self: "CircuitBreakerRegistry", name: str) -> bool:
        """
        移除熔断器

        Args:
            name: 熔断器名称

        Returns:
            True 如果成功移除，False 如果不存在
        """
        with self._lock:
            if name in self._breakers:
                del self._breakers[name]
                return True
            return False

    def get_all_stats(self: "CircuitBreakerRegistry") -> Dict[str, Dict[str, Any]]:
        """
        获取所有熔断器的统计信息

        Returns:
            {熔断器名称: 统计字典}
        """
        with self._lock:
            return {
                name: breaker.get_stats()
                for name, breaker in self._breakers.items()
            }

    def reset_all(self: "CircuitBreakerRegistry") -> None:
        """重置所有熔断器"""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()

    def list_names(self: "CircuitBreakerRegistry") -> list:
        """列出所有熔断器名称"""
        with self._lock:
            return list(self._breakers.keys())

    def __contains__(self: "CircuitBreakerRegistry", name: str) -> bool:
        """检查熔断器是否存在"""
        with self._lock:
            return name in self._breakers

    def __repr__(self: "CircuitBreakerRegistry") -> str:
        with self._lock:
            return (
                f"<CircuitBreakerRegistry("
                f"breakers={len(self._breakers)}, "
                f"names={list(self._breakers.keys())})>"
            )


# ============================================================================
# 模块导出
# ============================================================================

__all__ = [
    "CircuitBreakerConfig",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
]
