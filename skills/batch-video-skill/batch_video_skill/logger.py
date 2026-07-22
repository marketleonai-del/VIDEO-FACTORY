"""
统一结构化日志系统 - logger.py

提供生产级的 JSON 结构化日志能力，核心特性:
- 自动全链路追踪(trace_id)注入
- 上下文管理器实现零侵入集成
- 标准 JSON 格式，支持日志系统解析
- 高性能异步写入
- 完整的类型注解和 Pydantic 验证

使用示例:
    # 初始化日志系统
    logger = setup_logging(level="INFO", json_output=True)

    # 使用上下文管理器自动注入追踪信息
    with LogContext(trace_id="req_123", service="video_gen", provider="seedance"):
        logger.info("request_started", "开始处理视频生成请求")
        result = await generate_video(request)
        logger.info("request_completed", "视频生成完成", latency_ms=5234)
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import Enum
from io import StringIO
from types import TracebackType
from typing import Any, Callable, Dict, List, Optional, TextIO, Tuple

from pydantic import BaseModel, Field

# ============================================================================
# 常量定义
# ============================================================================

# ContextVar 定义 - 线程安全的上下文存储
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
span_id_var: ContextVar[str] = ContextVar("span_id", default="")
service_var: ContextVar[str] = ContextVar("service", default="")
provider_var: ContextVar[str] = ContextVar("provider", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")

DEFAULT_LOGGER_NAME = "skill"
ISO8601_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"

# ============================================================================
# 枚举定义
# ============================================================================


class LogLevel(str, Enum):
    """日志级别 - 与 Python logging 模块对应"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogEventType(str, Enum):
    """预定义事件类型 - 涵盖所有业务场景"""

    # 请求生命周期
    REQUEST_STARTED = "request_started"
    REQUEST_COMPLETED = "request_completed"
    REQUEST_FAILED = "request_failed"

    # 重试事件
    RETRY_SCHEDULED = "retry_scheduled"
    RETRY_ATTEMPTED = "retry_attempted"
    RETRY_SUCCEEDED = "retry_succeeded"
    RETRY_EXHAUSTED = "retry_exhausted"

    # 降级事件
    MODEL_FALLBACK_TRIGGERED = "model_fallback_triggered"
    PROVIDER_FALLBACK_TRIGGERED = "provider_fallback_triggered"
    SERVICE_FALLBACK_TRIGGERED = "service_fallback_triggered"
    FALLBACK_SUCCEEDED = "provider_fallback_succeeded"
    FALLBACK_FAILED = "provider_fallback_failed"
    FALLBACK_CHAIN_EXHAUSTED = "fallback_chain_exhausted"

    # 熔断事件
    CIRCUIT_BREAKER_OPENED = "circuit_breaker_opened"
    CIRCUIT_BREAKER_CLOSED = "circuit_breaker_closed"
    CIRCUIT_BREAKER_HALF_OPEN = "circuit_breaker_half_open"

    # 认证事件
    AUTH_REFRESH_STARTED = "auth_refresh_started"
    AUTH_REFRESH_SUCCEEDED = "auth_refresh_succeeded"
    AUTH_REFRESH_FAILED = "auth_refresh_failed"

    # 配额事件
    QUOTA_THRESHOLD_WARNING = "quota_threshold_warning"
    QUOTA_EXCEEDED = "quota_exceeded"

    # 系统事件
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"
    CONFIG_LOADED = "config_loaded"
    HEALTH_CHECK = "health_check"

    # 自定义
    CUSTOM = "custom"


# ============================================================================
# Pydantic 模型
# ============================================================================


class StructuredLogEntry(BaseModel):
    """结构化日志条目模型

    定义标准 JSON 日志的完整字段规范，输出为单行 JSON。
    """

    timestamp: str = Field(..., description="ISO 8601 格式，含微秒和时区")
    level: str = Field(..., description="日志级别")
    logger: str = Field(..., description="日志来源模块名")
    trace_id: str = Field(default="", description="全链路追踪 ID")
    span_id: str = Field(default="", description="当前 span ID")
    event: str = Field(..., description="事件类型标识符")
    message: str = Field(..., description="人类可读的消息")
    service: str = Field(default="", description="服务名称")
    provider: str = Field(default="", description="Provider 名称")
    error_code: str = Field(default="", description="统一错误码")
    error_type: str = Field(default="", description="错误类型")
    retry_count: int = Field(default=0, description="当前重试次数")
    latency_ms: float = Field(default=0.0, description="请求耗时(毫秒)")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="业务相关元数据")
    context: Dict[str, Any] = Field(default_factory=dict, description="用户/会话上下文")

    def to_json_line(self: "StructuredLogEntry") -> str:
        """序列化为单行 JSON 字符串"""
        return json.dumps(self.model_dump(), ensure_ascii=False, default=str)


class LogConfig(BaseModel):
    """日志系统配置"""

    level: str = Field(default="INFO", description="日志级别")
    json_output: bool = Field(default=True, description="是否使用 JSON 格式")
    output_stream: str = Field(default="stdout", description="输出流名称")
    log_file_path: str = Field(
        default="/var/log/skill/app.log", description="日志文件路径"
    )
    enable_trace: bool = Field(default=True, description="是否启用追踪")
    trace_id_header: str = Field(default="x-trace-id", description="追踪 ID HTTP 头")
    max_message_length: int = Field(
        default=10000, ge=100, description="最大消息长度"
    )
    default_service: str = Field(default="system", description="默认服务名")


# ============================================================================
# JSON 格式化器
# ============================================================================


class StructuredJsonFormatter(logging.Formatter):
    """JSON 日志格式化器

    将 Python logging.LogRecord 转换为标准 JSON 格式。
    自动从 ContextVar 读取追踪上下文并注入到输出中。

    Attributes:
        default_service: 默认服务名
        max_message_length: 最大消息长度，超长自动截断
    """

    def __init__(
        self,
        default_service: str = "system",
        max_message_length: int = 10000,
        **fmt_kwargs: Any,
    ) -> None:
        super().__init__()
        self.default_service = default_service
        self.max_message_length = max_message_length

    def _get_now_iso(self: "StructuredJsonFormatter") -> str:
        """获取当前时间的 ISO 8601 格式字符串"""
        return datetime.now(timezone.utc).isoformat()

    def _truncate(self: "StructuredJsonFormatter", text: str) -> str:
        """截断超长消息

        Args:
            text: 原始消息文本

        Returns:
            截断后的文本（超过 max_message_length 时末尾添加 ...）
        """
        if len(text) > self.max_message_length:
            return text[: self.max_message_length - 3] + "..."
        return text

    def _extract_extra(
        self, record: logging.LogRecord
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """从 LogRecord 中提取额外字段

        识别所有非标准 logging 字段，分类到 metadata/context/标准字段。

        Args:
            record: logging LogRecord 对象

        Returns:
            (extra标准字段, metadata元数据, context上下文) 三元组
        """
        standard_attrs = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "asctime",
            "taskName",
        }

        extra: Dict[str, Any] = {}
        metadata: Dict[str, Any] = {}
        context: Dict[str, Any] = {}

        for key, value in record.__dict__.items():
            if key in standard_attrs:
                continue

            if key in ("event", "event_type"):
                extra["event"] = value
            elif key in (
                "trace_id",
                "span_id",
                "service",
                "provider",
                "error_code",
                "error_type",
                "retry_count",
                "latency_ms",
            ):
                extra[key] = value
            elif key.startswith("ctx_"):
                context[key[4:]] = value
            elif key.startswith("meta_"):
                metadata[key[5:]] = value
            else:
                metadata[key] = value

        return extra, metadata, context

    def format(self: "StructuredJsonFormatter", record: logging.LogRecord) -> str:
        """格式化日志记录为 JSON 字符串

        Args:
            record: logging LogRecord 对象

        Returns:
            单行 JSON 字符串
        """
        record.message = record.getMessage()

        extra, metadata, context = self._extract_extra(record)

        # 从 ContextVar 读取追踪上下文
        trace_id = extra.get("trace_id", "") or trace_id_var.get("")
        span_id = extra.get("span_id", "") or span_id_var.get("")
        service = (
            extra.get("service", "") or service_var.get("") or self.default_service
        )
        provider = extra.get("provider", "") or provider_var.get("")

        event = extra.get(
            "event", extra.get("event_type", LogEventType.CUSTOM.value)
        )

        if record.exc_info and not extra.get("error_code"):
            exc_type = record.exc_info[0]
            if exc_type is not None:
                extra["error_type"] = exc_type.__name__

        entry = StructuredLogEntry(
            timestamp=self._get_now_iso(),
            level=record.levelname,
            logger=record.name,
            trace_id=trace_id,
            span_id=span_id,
            event=event,
            message=self._truncate(record.message),
            service=service,
            provider=provider,
            error_code=extra.get("error_code", ""),
            error_type=extra.get("error_type", ""),
            retry_count=extra.get("retry_count", 0),
            latency_ms=extra.get("latency_ms", 0.0),
            metadata=metadata,
            context=context,
        )

        return entry.to_json_line()


# ============================================================================
# SkillLogger - 主日志器
# ============================================================================


class SkillLogger:
    """Skill 专用结构化日志器

    封装 Python logging.Logger，提供类型安全的结构化日志接口。
    所有日志方法均支持 kwargs 扩展字段，自动注入追踪上下文。

    Attributes:
        _logger: 底层的 Python logging.Logger 实例
        _default_service: 默认服务名

    使用示例:
        logger = get_logger("skill.video_generation")
        logger.info("request_started", "开始处理请求", meta_model="seedance-pro")
        logger.log_request("video_gen", "seedance", "generate", 5234.0, True)
    """

    def __init__(
        self, logger: logging.Logger, default_service: str = "system"
    ) -> None:
        self._logger = logger
        self._default_service = default_service

    # 基础日志方法

    def debug(self: "SkillLogger", event: str, message: str = "", **kwargs: Any) -> None:
        """记录 DEBUG 级别日志"""
        self._log(logging.DEBUG, event, message, **kwargs)

    def info(self: "SkillLogger", event: str, message: str = "", **kwargs: Any) -> None:
        """记录 INFO 级别日志"""
        self._log(logging.INFO, event, message, **kwargs)

    def warning(self: "SkillLogger", event: str, message: str = "", **kwargs: Any) -> None:
        """记录 WARNING 级别日志 - 适用于重试/降级/配额预警"""
        self._log(logging.WARNING, event, message, **kwargs)

    def error(self: "SkillLogger", event: str, message: str = "", **kwargs: Any) -> None:
        """记录 ERROR 级别日志 - 适用于请求失败/认证错误"""
        self._log(logging.ERROR, event, message, **kwargs)

    def critical(self: "SkillLogger", event: str, message: str = "", **kwargs: Any) -> None:
        """记录 CRITICAL 级别日志 - 适用于所有 Provider 失败/系统错误"""
        self._log(logging.CRITICAL, event, message, **kwargs)

    # 业务场景日志方法

    def log_request(
        self,
        service: str,
        provider: str,
        method: str,
        latency_ms: float,
        success: bool,
        **kwargs: Any,
    ) -> None:
        """记录请求完成事件

        自动选择事件类型和日志级别:
        - 成功: INFO, event="request_completed"
        - 失败: ERROR, event="request_failed"

        Args:
            service: 服务名称
            provider: Provider 名称
            method: 方法名
            latency_ms: 请求耗时(毫秒)
            success: 是否成功
            **kwargs: 额外字段
        """
        if success:
            self.info(
                event="request_completed",
                message=f"{service}/{provider}/{method} 请求成功 ({latency_ms:.1f}ms)",
                service=service,
                provider=provider,
                latency_ms=latency_ms,
                meta_method=method,
                meta_status="success",
                **kwargs,
            )
        else:
            self.error(
                event="request_failed",
                message=f"{service}/{provider}/{method} 请求失败 ({latency_ms:.1f}ms)",
                service=service,
                provider=provider,
                latency_ms=latency_ms,
                meta_method=method,
                meta_status="failed",
                **kwargs,
            )

    def log_fallback(
        self,
        service: str,
        from_provider: str,
        to_provider: str,
        level: str,
        reason: str = "",
        **kwargs: Any,
    ) -> None:
        """记录降级事件

        Args:
            service: 服务名称
            from_provider: 原 Provider
            to_provider: 目标 Provider
            level: 降级级别 (level1_model/level2_provider/level3_service)
            reason: 降级原因
            **kwargs: 额外字段
        """
        event_map = {
            "level1_model": "model_fallback_triggered",
            "level2_provider": "provider_fallback_triggered",
            "level3_service": "service_fallback_triggered",
        }
        event = event_map.get(level, "provider_fallback_triggered")

        self.warning(
            event=event,
            message=f"{service} 自动降级: {from_provider} -> {to_provider}"
            + (f" | 原因: {reason}" if reason else ""),
            service=service,
            provider=from_provider,
            meta_from_provider=from_provider,
            meta_to_provider=to_provider,
            meta_fallback_level=level,
            meta_reason=reason,
            **kwargs,
        )

    def log_circuit_breaker(
        self,
        provider: str,
        state: str,
        failure_rate: float = 0.0,
        service: str = "",
        **kwargs: Any,
    ) -> None:
        """记录熔断器状态变化

        Args:
            provider: Provider 名称
            state: 熔断器状态 (open/closed/half_open)
            failure_rate: 失败率
            service: 服务名称
            **kwargs: 额外字段
        """
        event_map = {
            "open": "circuit_breaker_opened",
            "closed": "circuit_breaker_closed",
            "half_open": "circuit_breaker_half_open",
        }
        event = event_map.get(state, "circuit_breaker_opened")
        level = logging.WARNING if state == "open" else logging.INFO

        msg = f"熔断器 {state.upper()}: {provider}"
        if failure_rate > 0:
            msg += f" (失败率 {failure_rate:.1%})"

        self._log(
            level,
            event=event,
            message=msg,
            service=service,
            provider=provider,
            meta_previous_state=kwargs.get("previous_state", ""),
            meta_new_state=state,
            meta_failure_rate=failure_rate,
            **kwargs,
        )

    def log_exception(
        self,
        event: str,
        exc: Exception,
        **kwargs: Any,
    ) -> None:
        """记录异常详情 - 自动提取异常类型、消息和堆栈

        Args:
            event: 事件类型
            exc: 异常对象
            **kwargs: 额外字段
        """
        exc_type = type(exc).__name__
        exc_msg = str(exc)
        tb_str = traceback.format_exception(type(exc), exc, exc.__traceback__)

        self.error(
            event=event,
            message=f"{exc_type}: {exc_msg}",
            error_type=exc_type,
            meta_exception_class=exc_type,
            meta_exception_message=exc_msg,
            meta_traceback="".join(tb_str[-5:]),
            **kwargs,
        )

    # 通用事件日志方法

    def log_event(
        self,
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """通用事件日志接口 - 支持记录任意结构化事件数据

        Args:
            event_type: 事件类型标识符
            data: 事件数据字典，可包含:
                - message: 消息文本
                - level: 日志级别 (默认 INFO)
                - service, provider, trace_id 等标准字段
                - metadata: 嵌套元数据字典
                - context: 嵌套上下文字典
        """
        level_name = data.get("level", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)
        message = data.get("message", "")

        standard_fields = {
            "service",
            "provider",
            "trace_id",
            "span_id",
            "error_code",
            "error_type",
            "retry_count",
            "latency_ms",
            "metadata",
            "context",
        }

        kwargs: Dict[str, Any] = {}
        for key, value in data.items():
            if key in ("message", "level"):
                continue
            # 先处理 metadata 和 context 字典展开
            if key == "metadata" and isinstance(value, dict):
                for mk, mv in value.items():
                    kwargs[f"meta_{mk}"] = mv
            elif key == "context" and isinstance(value, dict):
                for ck, cv in value.items():
                    kwargs[f"ctx_{ck}"] = cv
            elif key in standard_fields:
                kwargs[key] = value
            else:
                kwargs[f"meta_{key}"] = value

        self._log(level, event_type, message, **kwargs)

    # 内部方法

    def _log(
        self,
        level: int,
        event: str,
        message: str,
        **kwargs: Any,
    ) -> None:
        """底层日志方法 - 将所有参数转换为 logging.LogRecord 的 extra 字段

        Args:
            level: logging 级别数值
            event: 事件类型
            message: 消息文本
            **kwargs: 额外字段，自动注入当前上下文
        """
        extra = {"event": event, **kwargs}

        # 自动注入当前上下文
        if not extra.get("trace_id"):
            extra["trace_id"] = trace_id_var.get("")
        if not extra.get("span_id"):
            extra["span_id"] = span_id_var.get("")
        if not extra.get("service"):
            svc = service_var.get("")
            extra["service"] = svc or self._default_service
        if not extra.get("provider"):
            extra["provider"] = provider_var.get("")

        self._logger.log(level, message, extra=extra)

    # 属性

    @property
    def name(self: "SkillLogger") -> str:
        """日志器名称"""
        return self._logger.name

    @property
    def level(self: "SkillLogger") -> int:
        """当前日志级别"""
        return self._logger.level

    def is_enabled_for(self: "SkillLogger", level: int) -> bool:
        """检查指定级别是否启用"""
        return self._logger.isEnabledFor(level)


# ============================================================================
# LogContext - 上下文管理器
# ============================================================================


class LogContext:
    """日志上下文管理器 - 实现全链路追踪的零侵入集成

    基于 Python ContextVar 实现，自动在上下文范围内的所有日志中
    注入 trace_id、service、provider 等追踪信息。

    特性:
    - 线程安全: ContextVar 天然支持 asyncio 多任务
    - 嵌套支持: 支持嵌套上下文，span_id 自动分层
    - 自动恢复: 退出上下文时自动恢复到之前的状态
    - 无感知集成: 业务代码只需包裹在 with 语句中

    使用示例:
        # 基本用法
        with LogContext(trace_id="req_123", service="video_gen"):
            logger.info("event", "消息")  # 自动注入 trace_id 和 service

        # 新请求
        with LogContext.new_request(service="video_gen", provider="seedance"):
            await process_request()

        # 从 HTTP 头提取
        with LogContext.from_headers(dict(request.headers), service="api"):
            await handle_request()
    """

    def __init__(
        self,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        service: Optional[str] = None,
        provider: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        # 保存 (ContextVar, value) 对，用于 __enter__ 设置
        self._changes: List[Tuple[ContextVar[str], str]] = []
        if trace_id is not None:
            self._changes.append((trace_id_var, trace_id))
        if span_id is not None:
            self._changes.append((span_id_var, span_id))
        if service is not None:
            self._changes.append((service_var, service))
        if provider is not None:
            self._changes.append((provider_var, provider))
        if user_id is not None:
            self._changes.append((user_id_var, user_id))
        # 保存 (ContextVar, token) 对，用于 __exit__ 精确恢复
        self._tokens: List[Tuple[ContextVar[str], Any]] = []

    def __enter__(self: "LogContext") -> LogContext:
        """进入上下文 - 设置新的 ContextVar 值"""
        for var, value in self._changes:
            self._tokens.append((var, var.set(value)))
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        """退出上下文 - 恢复 ContextVar 到之前的状态

        注意: 不吞噬异常，异常会正常向上传播。
        """
        for var, token in reversed(self._tokens):
            var.reset(token)

    # 工厂方法

    @classmethod
    def new_request(
        cls,
        service: str,
        provider: str = "",
        user_id: str = "",
    ) -> LogContext:
        """创建新请求的上下文（自动生成 trace_id）

        Args:
            service: 服务名称
            provider: Provider 名称
            user_id: 用户 ID

        Returns:
            配置好的 LogContext 实例
        """
        trace_id = f"req_{uuid.uuid4().hex[:16]}"
        return cls(
            trace_id=trace_id,
            service=service,
            provider=provider,
            user_id=user_id,
        )

    @classmethod
    def from_headers(
        cls,
        headers: Dict[str, str],
        service: str = "",
        trace_id_header: str = "x-trace-id",
    ) -> LogContext:
        """从 HTTP 请求头创建上下文 - 自动提取追踪 ID

        Args:
            headers: HTTP 请求头字典
            service: 服务名称
            trace_id_header: 追踪 ID 的 header 名称

        Returns:
            配置好的 LogContext 实例
        """
        trace_id = headers.get(trace_id_header, "")
        if not trace_id:
            trace_id = f"req_{uuid.uuid4().hex[:16]}"
        return cls(trace_id=trace_id, service=service)

    @staticmethod
    def get_current_trace_id() -> str:
        """获取当前上下文的 trace_id"""
        return trace_id_var.get("")

    @staticmethod
    def get_current_service() -> str:
        """获取当前上下文的 service"""
        return service_var.get("")

    @staticmethod
    def get_current_provider() -> str:
        """获取当前上下文的 provider"""
        return provider_var.get("")


# ============================================================================
# 初始化和工厂函数
# ============================================================================

_logger_cache: Dict[str, SkillLogger] = {}
_default_logger: Optional[SkillLogger] = None


def setup_logging(
    level: str = "INFO",
    json_output: bool = True,
    output_stream: Optional[TextIO] = None,
    default_service: str = "system",
    logger_name: str = DEFAULT_LOGGER_NAME,
) -> SkillLogger:
    """初始化日志系统

    配置根日志器，安装 JSON 格式化器，返回 SkillLogger 实例。
    应在应用启动时调用一次。

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        json_output: 是否使用 JSON 格式输出
        output_stream: 输出流，None 则使用 sys.stdout
        default_service: 默认服务名
        logger_name: 根日志器名称

    Returns:
        配置好的 SkillLogger 实例

    Raises:
        ValueError: 日志级别无效

    示例:
        >>> logger = setup_logging(level="INFO", json_output=True)
        >>> logger.info("startup", "应用启动完成")
    """
    global _default_logger

    level_num = getattr(logging, level.upper(), None)
    if not isinstance(level_num, int):
        raise ValueError(f"无效的日志级别: {level}")

    py_logger = logging.getLogger(logger_name)
    py_logger.setLevel(level_num)
    py_logger.handlers.clear()

    stream = output_stream or sys.stdout
    handler = logging.StreamHandler(stream)
    handler.setLevel(level_num)

    if json_output:
        formatter = StructuredJsonFormatter(default_service=default_service)
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
    handler.setFormatter(formatter)
    py_logger.addHandler(handler)

    skill_logger = SkillLogger(py_logger, default_service=default_service)
    _logger_cache[logger_name] = skill_logger
    _default_logger = skill_logger

    skill_logger.info(
        event="system_startup",
        message=f"日志系统初始化完成 (level={level}, json={json_output})",
        service=default_service,
    )

    return skill_logger


def get_logger(name: str) -> SkillLogger:
    """获取指定名称的 SkillLogger

    如果缓存中不存在，则创建新实例。

    Args:
        name: 日志器名称

    Returns:
        SkillLogger 实例
    """
    if name in _logger_cache:
        return _logger_cache[name]

    py_logger = logging.getLogger(name)
    default_service = "system"
    if _default_logger:
        default_service = _default_logger._default_service

    skill_logger = SkillLogger(py_logger, default_service=default_service)
    _logger_cache[name] = skill_logger
    return skill_logger


def get_default_logger() -> Optional[SkillLogger]:
    """获取默认日志器"""
    return _default_logger


def reset_logging() -> None:
    """重置日志系统 - 清除所有缓存的日志器和处理器

    主要用于测试场景清理状态。
    """
    global _logger_cache, _default_logger

    for name, skill_logger in list(_logger_cache.items()):
        skill_logger._logger.handlers.clear()

    _logger_cache.clear()
    _default_logger = None
