# -*- coding: utf-8 -*-
"""
Key 池管理模块

位置: batch_video_skill/infrastructure/key_pool.py
功能: 5种负载均衡策略 + 故障隔离 + 健康检查

职责:
- 多账号管理: 每个视频提供商维护独立的 Key 池
- 负载均衡: 支持 5 种负载均衡策略
- 故障隔离: 失败 Key 自动冷却，防止级联故障
- 健康检查: 定期检测 Key 可用性，自动恢复
- 统计监控: 每个 Key 的成功率、响应时间、配额追踪
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

try:
    from pydantic import BaseModel, Field, ConfigDict
except ImportError:
    raise ImportError("pydantic >= 2.0 is required")

logger = logging.getLogger(__name__)


# ============================================================================
# 枚举类型
# ============================================================================


class BalanceStrategy(str, Enum):
    """
    负载均衡策略

    支持5种策略:
    - ROUND_ROBIN: 轮询 (默认)
    - RANDOM: 随机
    - LEAST_FAILURES: 最少失败
    - WEIGHTED_RESPONSE: 加权响应时间
    - LEAST_CONNECTIONS: 最少连接
    """

    ROUND_ROBIN = "round_robin"
    RANDOM = "random"
    LEAST_FAILURES = "least_failures"
    WEIGHTED_RESPONSE = "weighted_response"
    LEAST_CONNECTIONS = "least_connections"


class KeyHealthStatus(str, Enum):
    """单个 Key 的健康状态"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    COOLDOWN = "cooldown"
    BANNED = "banned"


class ProviderState(str, Enum):
    """提供商整体状态"""

    FULLY_OPERATIONAL = "fully_operational"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


# ============================================================================
# Pydantic 数据模型
# ============================================================================


class KeyPoolStats(BaseModel):
    """Key 池统计信息"""

    model_config = ConfigDict(extra="allow")

    provider: str = Field(..., description="提供商标识")
    total_keys: int = Field(0, ge=0, description="Key 总数")
    healthy_keys: int = Field(0, ge=0, description="健康 Key 数")
    degraded_keys: int = Field(0, ge=0, description="降级 Key 数")
    cooldown_keys: int = Field(0, ge=0, description="冷却中 Key 数")
    banned_keys: int = Field(0, ge=0, description="已封禁 Key 数")
    total_requests: int = Field(0, ge=0, description="总请求数")
    total_success: int = Field(0, ge=0, description="成功数")
    total_failures: int = Field(0, ge=0, description="失败数")
    overall_success_rate: float = Field(1.0, ge=0, le=1, description="整体成功率")
    avg_response_time_ms: float = Field(0.0, ge=0, description="平均响应时间(ms)")
    strategy: str = Field("round_robin", description="负载均衡策略")
    last_updated: float = Field(default_factory=time.time, description="最后更新时间")


class SingleKeyStats(BaseModel):
    """单个 Key 的详细统计信息"""

    model_config = ConfigDict(extra="allow")

    key_preview: str = Field(..., description="Key 前8位预览 (安全显示)")
    status: str = Field("healthy", description="当前健康状态")
    total_requests: int = Field(0, ge=0)
    success_count: int = Field(0, ge=0)
    failure_count: int = Field(0, ge=0)
    success_rate: float = Field(1.0, ge=0, le=1)
    consecutive_failures: int = Field(0, ge=0)
    avg_response_time_ms: float = Field(0.0, ge=0)
    last_used_at: Optional[float] = Field(None)
    last_failure_at: Optional[float] = Field(None)
    cooldown_until: Optional[float] = Field(None)
    rate_limited: bool = Field(False, description="是否触发速率限制")
    current_connections: int = Field(0, ge=0, description="当前活跃连接数")


class HealthCheckResult(BaseModel):
    """健康检查结果"""

    model_config = ConfigDict(extra="allow")

    provider: str = Field(...)
    key_preview: str = Field(...)
    is_accessible: bool = Field(...)
    response_time_ms: float = Field(0.0)
    checked_at: float = Field(default_factory=time.time)
    error_message: Optional[str] = Field(None)


class CooldownConfig(BaseModel):
    """
    冷却配置

    可针对每个提供商单独配置冷却行为
    """

    model_config = ConfigDict(extra="allow")

    base_cooldown_sec: float = Field(
        60.0, ge=0, description="基础冷却时间(秒)"
    )
    max_cooldown_sec: float = Field(
        600.0, ge=0, description="最大冷却时间(秒)"
    )
    consecutive_failure_threshold: int = Field(
        3, ge=1, description="连续失败阈值 (超过则延长冷却)"
    )
    cooldown_multiplier: float = Field(
        2.0, ge=1.0, description="冷却时间乘数"
    )
    auto_recover_after_sec: float = Field(
        300.0, ge=0, description="自动恢复检查时间(秒)"
    )


# ============================================================================
# 异常体系
# ============================================================================


class KeyPoolError(Exception):
    """Key 池基础异常"""
    pass


class KeyExhaustedError(KeyPoolError):
    """
    所有 Key 均不可用

    属性:
        provider: 提供商标识
        retry_after_sec: 建议重试等待时间(秒)
    """

    def __init__(
        self,
        message: str,
        provider: str = "",
        retry_after_sec: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retry_after_sec = retry_after_sec


class KeyNotFoundError(KeyPoolError):
    """指定的 Key 不存在于池中"""
    pass


class KeyPoolNotFoundError(KeyPoolError):
    """Key 池未找到"""
    pass


class InvalidStrategyError(KeyPoolError):
    """无效的负载均衡策略"""
    pass


# ============================================================================
# 内部数据结构 _KeyEntry
# ============================================================================


@dataclass
class _KeyEntry:
    """
    内部 Key 条目 - 维护单个 API Key 的全部状态信息

    设计说明:
    - 使用 dataclass 减少样板代码
    - 所有运行时状态以下划线开头，与配置属性区分
    - 使用 deque 实现固定大小的历史记录 (防止内存无限增长)
    - 线程不安全，由上层 KeyPool 的锁保证

    内存估算 (单Key):
    - 基础对象: ~500 bytes
    - 请求时间戳 (1000条): ~8KB
    - 响应时间 (100条): ~800 bytes
    - 总计: ~10KB/Key (可忽略)
    """

    # ============ 配置属性 (初始化时设置) ============
    key: str
    priority: int = 0
    weight: float = 1.0
    max_rpm: int = 60
    max_concurrent: int = 10
    tags: Set[str] = field(default_factory=set)

    # ============ 运行时状态 (动态变化) ============
    _request_timestamps: deque = field(
        default_factory=lambda: deque(maxlen=1000)
    )
    _success_timestamps: deque = field(
        default_factory=lambda: deque(maxlen=1000)
    )
    _failure_timestamps: deque = field(
        default_factory=lambda: deque(maxlen=1000)
    )
    _response_times: deque = field(
        default_factory=lambda: deque(maxlen=100)
    )
    _consecutive_failures: int = 0
    _cooldown_until: Optional[float] = None
    _banned: bool = False
    _current_connections: int = 0
    _last_used_at: Optional[float] = None
    _last_failure_at: Optional[float] = None
    _total_requests: int = 0
    _total_success: int = 0
    _total_failures: int = 0

    # ============ 安全相关 ============

    @property
    def key_preview(self: "_KeyEntry") -> str:
        """
        返回 Key 的安全预览 (前8位 + 省略号)

        Returns:
            str: 如 "sk-abcd..."
        """
        return f"{self.key[:8]}..." if len(self.key) > 8 else self.key

    # ============ 状态检查 ============

    @property
    def is_in_cooldown(self: "_KeyEntry") -> bool:
        """检查是否在冷却期"""
        if self._banned:
            return True
        if self._cooldown_until is None:
            return False
        return time.time() < self._cooldown_until

    @property
    def cooldown_remaining_sec(self: "_KeyEntry") -> float:
        """返回冷却剩余时间(秒)"""
        if not self.is_in_cooldown:
            return 0.0
        if self._cooldown_until is None:
            return float("inf")
        return max(0.0, self._cooldown_until - time.time())

    @property
    def is_rate_limited(self: "_KeyEntry") -> bool:
        """
        检查是否触发速率限制 (RPM)

        算法:
        1. 清理60秒前的时间戳
        2. 检查最近60秒内请求数是否 >= max_rpm
        """
        now = time.time()
        while (
            self._request_timestamps
            and now - self._request_timestamps[0] > 60
        ):
            self._request_timestamps.popleft()
        return len(self._request_timestamps) >= self.max_rpm

    @property
    def is_at_capacity(self: "_KeyEntry") -> bool:
        """检查是否达到最大并发连接数"""
        return self._current_connections >= self.max_concurrent

    @property
    def is_available(self: "_KeyEntry") -> bool:
        """
        Key 是否可用 (综合所有检查)

        检查条件 (全部通过才算可用):
        1. 未被封禁
        2. 不在冷却期
        3. 未触发速率限制
        4. 未达到并发上限
        """
        return (
            not self._banned
            and not self.is_in_cooldown
            and not self.is_rate_limited
            and not self.is_at_capacity
        )

    @property
    def health_status(self: "_KeyEntry") -> str:
        """
        获取健康状态枚举值

        状态优先级: BANNED > COOLDOWN > DEGRADED > HEALTHY
        """
        if self._banned:
            return KeyHealthStatus.BANNED.value
        if self.is_in_cooldown:
            return KeyHealthStatus.COOLDOWN.value
        if self._consecutive_failures > 0:
            return KeyHealthStatus.DEGRADED.value
        return KeyHealthStatus.HEALTHY.value

    # ============ 统计计算 ============

    @property
    def success_rate(self: "_KeyEntry") -> float:
        """计算历史成功率"""
        total = self._total_success + self._total_failures
        if total == 0:
            return 1.0
        return self._total_success / total

    @property
    def avg_response_time_ms(self: "_KeyEntry") -> float:
        """计算平均响应时间 (最近100次)"""
        if not self._response_times:
            return 0.0
        return sum(self._response_times) / len(self._response_times)

    @property
    def current_rpm(self: "_KeyEntry") -> int:
        """当前 RPM (最近60秒内请求数)"""
        now = time.time()
        while (
            self._request_timestamps
            and now - self._request_timestamps[0] > 60
        ):
            self._request_timestamps.popleft()
        return len(self._request_timestamps)

    # ============ 请求生命周期 ============

    def record_request(self: "_KeyEntry") -> None:
        """记录一次请求开始"""
        now = time.time()
        self._request_timestamps.append(now)
        self._current_connections += 1
        self._total_requests += 1
        self._last_used_at = now

    def record_success(self: "_KeyEntry", response_time_ms: float) -> None:
        """
        记录请求成功

        Args:
            response_time_ms: 响应时间(毫秒)
        """
        self._total_success += 1
        self._consecutive_failures = 0
        self._current_connections = max(0, self._current_connections - 1)
        self._response_times.append(response_time_ms)
        self._success_timestamps.append(time.time())

    def record_failure(self: "_KeyEntry", cooldown_sec: float = 60.0) -> None:
        """
        记录请求失败

        Args:
            cooldown_sec: 冷却时间(秒)，0表示不冷却
        """
        self._total_failures += 1
        self._consecutive_failures += 1
        self._current_connections = max(0, self._current_connections - 1)
        self._last_failure_at = time.time()
        self._failure_timestamps.append(time.time())

        # 连续失败增加冷却时间
        if cooldown_sec > 0:
            multiplier = min(self._consecutive_failures, 5)
            actual_cooldown = cooldown_sec * multiplier
            self._cooldown_until = time.time() + actual_cooldown

    def record_release(self: "_KeyEntry") -> None:
        """释放连接 (请求完成但未标记成功/失败时)"""
        self._current_connections = max(0, self._current_connections - 1)

    # ============ 管理操作 ============

    def force_cooldown(self: "_KeyEntry", duration_sec: float) -> None:
        """强制进入冷却期"""
        self._cooldown_until = time.time() + duration_sec

    def reset(self: "_KeyEntry") -> None:
        """重置 Key 状态 (管理员操作)"""
        self._cooldown_until = None
        self._consecutive_failures = 0
        self._current_connections = 0

    def ban(self: "_KeyEntry") -> None:
        """封禁 Key (永久不可用，直到手动解封)"""
        self._banned = True

    def unban(self: "_KeyEntry") -> None:
        """解封 Key"""
        self._banned = False
        self._cooldown_until = None
        self._consecutive_failures = 0

    def to_pydantic(self: "_KeyEntry") -> SingleKeyStats:
        """转换为 Pydantic 模型 (用于 API 响应)"""
        return SingleKeyStats(
            key_preview=self.key_preview,
            status=self.health_status,
            total_requests=self._total_requests,
            success_count=self._total_success,
            failure_count=self._total_failures,
            success_rate=self.success_rate,
            consecutive_failures=self._consecutive_failures,
            avg_response_time_ms=self.avg_response_time_ms,
            last_used_at=self._last_used_at,
            last_failure_at=self._last_failure_at,
            cooldown_until=self._cooldown_until,
            rate_limited=self.is_rate_limited,
            current_connections=self._current_connections,
        )


# ============================================================================
# KeyPool — 单个提供商的 Key 池
# ============================================================================


class KeyPool:
    """
    单个提供商的 Key 池

    职责:
    - 管理一组 API Key 的生命周期
    - 提供负载均衡选择
    - 跟踪每个 Key 的使用统计
    - 处理故障隔离 (cooldown)

    线程安全:
    - 本类方法非线程安全，由调用方保证
    - KeyPoolManager 使用 asyncio.Lock 保护
    """

    def __init__(
        self,
        provider: str,
        keys: List[str],
        strategy: BalanceStrategy = BalanceStrategy.ROUND_ROBIN,
        cooldown_config: Optional[CooldownConfig] = None,
        key_weights: Optional[Dict[str, float]] = None,
        key_priorities: Optional[Dict[str, int]] = None,
        key_max_rpm: Optional[Dict[str, int]] = None,
    ) -> None:
        """
        初始化 Key 池

        Args:
            provider: 提供商标识
            keys: API Key 列表
            strategy: 负载均衡策略
            cooldown_config: 冷却配置
            key_weights: Key 权重映射 {key: weight}
            key_priorities: Key 优先级映射 {key: priority}
            key_max_rpm: Key RPM 限制映射 {key: max_rpm}

        Raises:
            ValueError: keys 为空列表
        """
        if not keys:
            raise ValueError(f"[{provider}] 必须提供至少一个 API Key")

        self.provider = provider
        self.strategy = strategy
        self.cooldown_config = cooldown_config or CooldownConfig()

        # 创建 Key 条目
        self._entries: List[_KeyEntry] = []
        for i, key in enumerate(keys):
            entry = _KeyEntry(
                key=key,
                priority=key_priorities.get(key, i)
                if key_priorities
                else i,
                weight=key_weights.get(key, 1.0)
                if key_weights
                else 1.0,
                max_rpm=key_max_rpm.get(key, 60)
                if key_max_rpm
                else 60,
            )
            self._entries.append(entry)

        # 轮询索引
        self._rr_index = -1

    # ============ 属性 ============

    @property
    def total_keys(self: "KeyPool") -> int:
        """Key 总数"""
        return len(self._entries)

    @property
    def available_keys(self: "KeyPool") -> int:
        """当前可用 Key 数"""
        return sum(1 for e in self._entries if e.is_available)

    @property
    def healthy_keys(self: "KeyPool") -> int:
        """健康 Key 数 (未封禁且未冷却)"""
        return sum(
            1
            for e in self._entries
            if not e._banned and not e.is_in_cooldown
        )

    @property
    def has_available_key(self: "KeyPool") -> bool:
        """是否有可用 Key"""
        return any(e.is_available for e in self._entries)

    # ============ 负载均衡选择器 ============

    def _select_round_robin(self: "KeyPool", entries: List[_KeyEntry]) -> _KeyEntry:
        """轮询选择算法"""
        for _ in range(len(entries)):
            self._rr_index = (self._rr_index + 1) % len(entries)
            candidate = entries[self._rr_index]
            if candidate.is_available:
                return candidate
        return entries[0]

    def _select_random(self: "KeyPool", entries: List[_KeyEntry]) -> _KeyEntry:
        """随机选择算法"""
        return random.choice(entries)

    def _select_least_failures(
        self, entries: List[_KeyEntry]
    ) -> _KeyEntry:
        """最少失败选择算法"""
        return min(
            entries,
            key=lambda e: (e._total_failures, e._consecutive_failures),
        )

    def _select_weighted_response(
        self, entries: List[_KeyEntry]
    ) -> _KeyEntry:
        """加权响应时间选择算法"""
        return min(
            entries, key=lambda e: e.avg_response_time_ms or 999999
        )

    def _select_least_connections(
        self, entries: List[_KeyEntry]
    ) -> _KeyEntry:
        """最少连接选择算法"""
        return min(entries, key=lambda e: e._current_connections)

    def _get_selector(
        self, strategy: BalanceStrategy
    ) -> Callable[[List[_KeyEntry]], _KeyEntry]:
        """获取策略选择器函数"""
        selectors = {
            BalanceStrategy.ROUND_ROBIN: self._select_round_robin,
            BalanceStrategy.RANDOM: self._select_random,
            BalanceStrategy.LEAST_FAILURES: self._select_least_failures,
            BalanceStrategy.WEIGHTED_RESPONSE: self._select_weighted_response,
            BalanceStrategy.LEAST_CONNECTIONS: self._select_least_connections,
        }
        selector = selectors.get(strategy)
        if selector is None:
            raise InvalidStrategyError(f"不支持的策略: {strategy.value}")
        return selector

    # ============ 核心方法 ============

    def get_key(self: "KeyPool", strategy: Optional[BalanceStrategy] = None) -> str:
        """
        获取下一个可用的 API Key

        流程:
        1. 筛选可用条目
        2. 根据策略选择
        3. 记录请求
        4. 返回 Key

        Args:
            strategy: 临时覆盖负载均衡策略

        Returns:
            str: 选中的 API Key

        Raises:
            KeyExhaustedError: 所有 Key 均不可用
        """
        use_strategy = strategy or self.strategy

        # 尝试自动恢复
        self._try_auto_recover()

        available = [e for e in self._entries if e.is_available]

        if not available:
            return self._handle_all_unavailable()

        selector = self._get_selector(use_strategy)
        entry = selector(available)
        entry.record_request()

        return entry.key

    def _handle_all_unavailable(self: "KeyPool") -> str:
        """处理所有 Key 均不可用的情况"""
        # 计算建议重试时间
        retry_after = min(
            (
                e.cooldown_remaining_sec
                for e in self._entries
                if e.cooldown_remaining_sec > 0
            ),
            default=60.0,
        )
        raise KeyExhaustedError(
            f"[{self.provider}] 所有 Key 均不可用 ("
            f"冷却: {sum(1 for e in self._entries if e.is_in_cooldown)}, "
            f"封禁: {sum(1 for e in self._entries if e._banned)})",
            provider=self.provider,
            retry_after_sec=retry_after,
        )

    def report_success(self: "KeyPool", key: str, response_time_ms: float = 0.0) -> None:
        """标记 Key 请求成功"""
        entry = self._find_entry(key)
        if entry:
            entry.record_success(response_time_ms)

    def report_failure(self: "KeyPool", key: str, is_fatal: bool = False) -> None:
        """标记 Key 请求失败"""
        entry = self._find_entry(key)
        if not entry:
            return

        if is_fatal:
            entry.ban()
        else:
            entry.record_failure(self.cooldown_config.base_cooldown_sec)
            threshold = self.cooldown_config.consecutive_failure_threshold
            if entry._consecutive_failures >= threshold:
                extended = self.cooldown_config.max_cooldown_sec
                entry.force_cooldown(extended)

    def report_rate_limited(
        self, key: str, retry_after_sec: float = 60.0
    ) -> None:
        """标记 Key 触发速率限制"""
        entry = self._find_entry(key)
        if entry:
            entry.force_cooldown(retry_after_sec)

    def release_key(self: "KeyPool", key: str) -> None:
        """释放 Key"""
        entry = self._find_entry(key)
        if entry:
            entry.record_release()

    # ============ 管理操作 ============

    def reset_key(self: "KeyPool", key: str) -> None:
        """手动重置 Key 状态"""
        entry = self._find_entry(key)
        if entry:
            entry.reset()

    def ban_key(self: "KeyPool", key: str) -> None:
        """手动封禁 Key"""
        entry = self._find_entry(key)
        if entry:
            entry.ban()

    def unban_key(self: "KeyPool", key: str) -> None:
        """手动解封 Key"""
        entry = self._find_entry(key)
        if entry:
            entry.unban()

    def set_strategy(self: "KeyPool", strategy: BalanceStrategy) -> None:
        """更改负载均衡策略"""
        if strategy not in list(BalanceStrategy):
            raise InvalidStrategyError(f"不支持的策略: {strategy.value}")
        self.strategy = strategy

    # ============ 查找辅助 ============

    def _find_entry(self: "KeyPool", key: str) -> Optional[_KeyEntry]:
        """根据 key 值查找条目"""
        for entry in self._entries:
            if entry.key == key:
                return entry
        return None

    # ============ 统计查询 ============

    def get_stats(self: "KeyPool") -> KeyPoolStats:
        """获取 Key 池统计信息"""
        total_requests = sum(e._total_requests for e in self._entries)
        total_success = sum(e._total_success for e in self._entries)
        total_failures = sum(e._total_failures for e in self._entries)

        all_times = []
        for e in self._entries:
            all_times.extend(e._response_times)
        avg_response = sum(all_times) / len(all_times) if all_times else 0.0

        success_rate = 1.0
        if total_success + total_failures > 0:
            success_rate = total_success / (total_success + total_failures)

        healthy = sum(
            1
            for e in self._entries
            if e.health_status == KeyHealthStatus.HEALTHY.value
        )
        degraded = sum(
            1
            for e in self._entries
            if e.health_status == KeyHealthStatus.DEGRADED.value
        )
        cooldown = sum(
            1
            for e in self._entries
            if e.health_status == KeyHealthStatus.COOLDOWN.value
        )
        banned = sum(
            1
            for e in self._entries
            if e.health_status == KeyHealthStatus.BANNED.value
        )

        return KeyPoolStats(
            provider=self.provider,
            total_keys=self.total_keys,
            healthy_keys=healthy,
            degraded_keys=degraded,
            cooldown_keys=cooldown,
            banned_keys=banned,
            total_requests=total_requests,
            total_success=total_success,
            total_failures=total_failures,
            overall_success_rate=success_rate,
            avg_response_time_ms=avg_response,
            strategy=self.strategy.value,
            last_updated=time.time(),
        )

    def get_key_stats(self: "KeyPool") -> List[SingleKeyStats]:
        """获取每个 Key 的详细统计"""
        return [entry.to_pydantic() for entry in self._entries]

    # ============ 健康检查 ============

    async def health_check(
        self,
        check_fn: Optional[Callable[[str], Any]] = None,
    ) -> List[HealthCheckResult]:
        """
        执行健康检查

        Args:
            check_fn: 自定义异步检查函数 (async (key) -> bool)

        Returns:
            List[HealthCheckResult]: 各 Key 的检查结果
        """
        results = []

        for entry in self._entries:
            if entry._banned:
                results.append(
                    HealthCheckResult(
                        provider=self.provider,
                        key_preview=entry.key_preview,
                        is_accessible=False,
                        error_message="Key 已被封禁",
                    )
                )
                continue

            if check_fn:
                try:
                    start = time.time()
                    is_ok = await check_fn(entry.key)
                    elapsed_ms = (time.time() - start) * 1000
                    results.append(
                        HealthCheckResult(
                            provider=self.provider,
                            key_preview=entry.key_preview,
                            is_accessible=is_ok,
                            response_time_ms=elapsed_ms,
                        )
                    )
                except Exception as e:
                    results.append(
                        HealthCheckResult(
                            provider=self.provider,
                            key_preview=entry.key_preview,
                            is_accessible=False,
                            error_message=str(e),
                        )
                    )
            else:
                is_healthy = (
                    not entry.is_in_cooldown and entry.success_rate > 0.3
                )
                results.append(
                    HealthCheckResult(
                        provider=self.provider,
                        key_preview=entry.key_preview,
                        is_accessible=is_healthy,
                    )
                )

        return results

    # ============ 自动恢复 ============

    def _try_auto_recover(self: "KeyPool") -> bool:
        """
        尝试自动恢复过期的冷却 Key

        恢复条件:
        1. Key 在冷却中
        2. Key 未被封禁
        3. 距离上次失败超过 auto_recover_after_sec
        4. 连续失败次数未超过阈值

        Returns:
            bool: 是否有 Key 被恢复
        """
        now = time.time()
        recovered = False
        auto_recover_sec = self.cooldown_config.auto_recover_after_sec

        for entry in self._entries:
            if (
                entry.is_in_cooldown
                and not entry._banned
                and entry._last_failure_at
                and (now - entry._last_failure_at) > auto_recover_sec
                and entry._consecutive_failures
                < self.cooldown_config.consecutive_failure_threshold
            ):
                entry.reset()
                recovered = True

        return recovered


# ============================================================================
# KeyPoolManager — 全局 Key 池管理器
# ============================================================================


class KeyPoolManager:
    """
    全局 Key 池管理器

    职责:
    - 管理所有提供商的 Key 池
    - 提供统一的 Key 获取/报告接口
    - 定期健康检查调度
    - 全局统计聚合
    - 自动故障恢复

    使用示例:
        >>> manager = KeyPoolManager()
        >>> manager.register_pool("seedance", ["sk-1", "sk-2"])
        >>> key = manager.get_key("seedance")
        >>> manager.report_success("seedance", key, response_time_ms=200)
    """

    def __init__(self: "KeyPoolManager") -> None:
        """初始化全局 Key 池管理器"""
        self._pools: Dict[str, KeyPool] = {}
        self._global_stats: Dict[str, Any] = {}
        self._health_check_interval_sec: float = 300.0
        self._health_check_task: Optional[asyncio.Task] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._lock = asyncio.Lock()

    # ============ 池注册与管理 ============

    def register_pool(
        self,
        provider: str,
        keys: List[str],
        strategy: BalanceStrategy = BalanceStrategy.ROUND_ROBIN,
        cooldown_config: Optional[CooldownConfig] = None,
        key_weights: Optional[Dict[str, float]] = None,
        key_priorities: Optional[Dict[str, int]] = None,
        key_max_rpm: Optional[Dict[str, int]] = None,
    ) -> KeyPool:
        """
        注册一个提供商的 Key 池

        Args:
            provider: 提供商标识
            keys: API Key 列表
            strategy: 负载均衡策略
            cooldown_config: 冷却配置
            key_weights: Key 权重映射
            key_priorities: Key 优先级映射
            key_max_rpm: Key RPM 限制映射

        Returns:
            KeyPool: 创建的 Key 池实例

        Raises:
            ValueError: keys 为空
            KeyPoolError: 该提供商已注册
        """
        if not keys:
            raise ValueError(f"[{provider}] 必须提供至少一个 API Key")

        if provider in self._pools:
            raise KeyPoolError(
                f"[{provider}] Key 池已存在，请先注销"
            )

        pool = KeyPool(
            provider=provider,
            keys=keys,
            strategy=strategy,
            cooldown_config=cooldown_config,
            key_weights=key_weights,
            key_priorities=key_priorities,
            key_max_rpm=key_max_rpm,
        )

        self._pools[provider] = pool
        return pool

    def unregister_pool(self: "KeyPoolManager", provider: str) -> None:
        """注销提供商的 Key 池"""
        if provider in self._pools:
            del self._pools[provider]

    def get_pool(self: "KeyPoolManager", provider: str) -> Optional[KeyPool]:
        """获取指定提供商的 Key 池"""
        return self._pools.get(provider)

    def list_providers(self: "KeyPoolManager") -> List[str]:
        """列出所有已注册的提供商"""
        return list(self._pools.keys())

    # ============ Key 获取与报告 ============

    def get_key(
        self,
        provider: str,
        strategy: Optional[BalanceStrategy] = None,
    ) -> str:
        """
        获取指定提供商的下一个可用 Key

        Args:
            provider: 提供商标识
            strategy: 临时覆盖负载均衡策略

        Returns:
            str: API Key

        Raises:
            KeyPoolNotFoundError: 未找到 Key 池
            KeyExhaustedError: 所有 Key 均不可用
        """
        pool = self._pools.get(provider)
        if not pool:
            raise KeyPoolNotFoundError(
                f"未找到 [{provider}] 的 Key 池"
            )

        return pool.get_key(strategy)

    def report_success(
        self,
        provider: str,
        key: str,
        response_time_ms: float = 0.0,
    ) -> None:
        """报告 Key 使用成功"""
        pool = self._pools.get(provider)
        if pool:
            pool.report_success(key, response_time_ms)

    def report_failure(
        self,
        provider: str,
        key: str,
        is_fatal: bool = False,
    ) -> None:
        """报告 Key 使用失败"""
        pool = self._pools.get(provider)
        if pool:
            pool.report_failure(key, is_fatal)

    def report_rate_limited(
        self,
        provider: str,
        key: str,
        retry_after_sec: float = 60.0,
    ) -> None:
        """报告 Key 触发速率限制"""
        pool = self._pools.get(provider)
        if pool:
            pool.report_rate_limited(key, retry_after_sec)

    def release_key(self: "KeyPoolManager", provider: str, key: str) -> None:
        """释放 Key"""
        pool = self._pools.get(provider)
        if pool:
            pool.release_key(key)

    # ============ 管理操作 ============

    def reset_key(self: "KeyPoolManager", provider: str, key: str) -> None:
        """手动重置 Key 状态"""
        pool = self._pools.get(provider)
        if pool:
            pool.reset_key(key)

    def ban_key(self: "KeyPoolManager", provider: str, key: str) -> None:
        """手动封禁 Key"""
        pool = self._pools.get(provider)
        if pool:
            pool.ban_key(key)

    def unban_key(self: "KeyPoolManager", provider: str, key: str) -> None:
        """手动解封 Key"""
        pool = self._pools.get(provider)
        if pool:
            pool.unban_key(key)

    def set_strategy(
        self, provider: str, strategy: BalanceStrategy
    ) -> None:
        """更改负载均衡策略"""
        pool = self._pools.get(provider)
        if pool:
            pool.set_strategy(strategy)

    # ============ 统计查询 ============

    def get_pool_stats(self: "KeyPoolManager", provider: str) -> Optional[KeyPoolStats]:
        """获取指定 Key 池统计"""
        pool = self._pools.get(provider)
        if pool:
            return pool.get_stats()
        return None

    def get_all_stats(self: "KeyPoolManager") -> Dict[str, KeyPoolStats]:
        """获取所有 Key 池统计"""
        return {
            provider: pool.get_stats()
            for provider, pool in self._pools.items()
        }

    def get_key_detail_stats(
        self, provider: str
    ) -> List[SingleKeyStats]:
        """获取 Key 详细统计"""
        pool = self._pools.get(provider)
        if pool:
            return pool.get_key_stats()
        return []

    def get_global_summary(self: "KeyPoolManager") -> Dict[str, Any]:
        """
        获取全局摘要统计

        Returns:
            Dict: 包含 total_providers, total_keys, total_healthy_keys 等
        """
        all_stats = self.get_all_stats()

        total_keys = sum(s.total_keys for s in all_stats.values())
        total_healthy = sum(
            s.healthy_keys for s in all_stats.values()
        )
        total_requests = sum(
            s.total_requests for s in all_stats.values()
        )
        total_success = sum(
            s.total_success for s in all_stats.values()
        )
        total_failures = sum(
            s.total_failures for s in all_stats.values()
        )

        overall_success_rate = 1.0
        if total_success + total_failures > 0:
            overall_success_rate = total_success / (
                total_success + total_failures
            )

        provider_status = {}
        for prov, stats in all_stats.items():
            if stats.healthy_keys > 0:
                status = ProviderState.FULLY_OPERATIONAL.value
            elif stats.total_keys > stats.banned_keys:
                status = ProviderState.DEGRADED.value
            else:
                status = ProviderState.UNAVAILABLE.value
            provider_status[prov] = {
                "status": status,
                "healthy_keys": stats.healthy_keys,
                "total_keys": stats.total_keys,
                "success_rate": round(stats.overall_success_rate, 4),
            }

        return {
            "total_providers": len(self._pools),
            "total_keys": total_keys,
            "total_healthy_keys": total_healthy,
            "total_requests": total_requests,
            "total_success": total_success,
            "total_failures": total_failures,
            "overall_success_rate": round(overall_success_rate, 4),
            "provider_status": provider_status,
            "timestamp": time.time(),
        }

    # ============ 健康检查 ============

    async def health_check(
        self,
        provider: Optional[str] = None,
        check_fn: Optional[Callable[[str], Any]] = None,
    ) -> Dict[str, List[HealthCheckResult]]:
        """
        执行健康检查

        Args:
            provider: 指定提供商 (None 则检查所有)
            check_fn: 自定义检查函数

        Returns:
            Dict[provider, List[HealthCheckResult]]
        """
        results = {}
        targets = [provider] if provider else list(self._pools.keys())

        for p in targets:
            pool = self._pools.get(p)
            if pool:
                results[p] = await pool.health_check(check_fn)

        return results

    async def start_periodic_health_check(
        self,
        interval_sec: float = 300.0,
        check_fn: Optional[Callable[[str], Any]] = None,
        on_unhealthy: Optional[Callable] = None,
    ) -> None:
        """
        启动定期健康检查 (后台任务)

        Args:
            interval_sec: 检查间隔(秒)
            check_fn: 自定义检查函数
            on_unhealthy: 发现不健康时的回调
        """
        self._health_check_interval_sec = interval_sec
        self._shutdown_event = asyncio.Event()

        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=interval_sec,
                )
            except asyncio.TimeoutError:
                pass

            if self._shutdown_event.is_set():
                break

            try:
                results = await self.health_check(check_fn=check_fn)

                for prov, key_results in results.items():
                    unhealthy = [
                        r for r in key_results if not r.is_accessible
                    ]
                    if unhealthy and on_unhealthy:
                        if asyncio.iscoroutinefunction(on_unhealthy):
                            await on_unhealthy(prov, unhealthy)
                        else:
                            on_unhealthy(prov, unhealthy)
            except Exception as e:
                logger.error("定期健康检查异常: %s", e)

    def stop_periodic_health_check(self: "KeyPoolManager") -> None:
        """停止定期健康检查"""
        if self._shutdown_event:
            self._shutdown_event.set()

    # ============ 生命周期 ============

    async def close(self: "KeyPoolManager") -> None:
        """关闭管理器"""
        self.stop_periodic_health_check()
        self._pools.clear()

    async def __aenter__(self: "KeyPoolManager"):
        return self

    async def __aexit__(self: "KeyPoolManager", exc_type, exc_val, exc_tb):
        await self.close()

    def __repr__(self: "KeyPoolManager") -> str:
        return (
            f"<KeyPoolManager(pools={len(self._pools)}, "
            f"providers={list(self._pools.keys())})>"
        )


# ============================================================================
# 模块导出
# ============================================================================

__all__ = [
    # 枚举
    "BalanceStrategy",
    "KeyHealthStatus",
    "ProviderState",
    # Pydantic 模型
    "KeyPoolStats",
    "SingleKeyStats",
    "HealthCheckResult",
    "CooldownConfig",
    # 异常
    "KeyPoolError",
    "KeyExhaustedError",
    "KeyNotFoundError",
    "KeyPoolNotFoundError",
    "InvalidStrategyError",
    # 核心类
    "_KeyEntry",
    "KeyPool",
    "KeyPoolManager",
]
