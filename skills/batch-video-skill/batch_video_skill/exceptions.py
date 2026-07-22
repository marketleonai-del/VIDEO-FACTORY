# -*- coding: utf-8 -*-
"""
异常体系 - SkillException 及其完整继承树

位置: batch_video_skill/infrastructure/exceptions.py
功能: 统一异常基类 + 7大类34个子类 + 异常工厂

设计原则:
1. 所有异常继承 SkillException，确保统一接口
2. 每个异常携带 error_code, category, retryable 等元信息
3. 支持自动序列化为 JSON，用于日志和 API 响应
4. ExceptionFactory 将外部异常转换为统一异常体系

类型签名说明:
- 每个类属性标记为 ClassVar，表示类级别常量
- __init__ 参数使用关键字参数，增强可读性
- to_dict() 返回 Dict[str, Any]，可直接 json.dumps()
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple, Type


# ============================================================================
# 枚举类型
# ============================================================================


class ErrorCategory(str, Enum):
    """
    错误分类枚举 - 7大类错误体系

    每个异常类必须归属一个分类，用于:
    - 错误统计和报表
    - 告警路由
    - 用户提示策略
    """

    CLIENT = "client"  # 客户端错误(4xx) - 不重试
    SERVER = "server"  # 服务端错误(5xx) - 可重试
    NETWORK = "network"  # 网络错误 - 可重试
    TASK = "task"  # 任务执行错误 - 部分可重试
    RESOURCE = "resource"  # 资源错误 - 部分可重试
    CONFIGURATION = "config"  # 配置错误 - 不重试
    SYSTEM = "system"  # 系统内部错误 - 视情况


class NotificationLevel(str, Enum):
    """
    通知级别枚举 - 决定用户可见的提示颜色和紧急程度

    值    | 颜色   | 用户可见形式      | 是否需要操作
    ------|--------|-------------------|-------------
    info  | 绿色   | 轻提示/Toast      | 否
    warning| 黄色  | 警告框            | 可选
    error | 红色   | 错误提示          | 是
    critical| 红色弹窗 | 弹窗阻塞      | 必须立即处理
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class FallbackLevel(str, Enum):
    """
    降级级别枚举 - 三级降级体系

    MODEL:    模型级降级 - 同一Provider内切换模型(延迟最低)
    PROVIDER: Provider级降级 - 跨Provider切换(质量可能变化)
    SERVICE:  服务级降级 - 降级到不同服务模式(功能受限)
    """

    MODEL = "level1_model"
    PROVIDER = "level2_provider"
    SERVICE = "level3_service"


class BackoffStrategy(str, Enum):
    """
    退避策略枚举 - 重试等待时间计算方式

    EXPONENTIAL:  delay = base * 2^attempt + jitter  (默认)
    LINEAR:       delay = base * (attempt + 1) + jitter
    FIXED:        delay = base + jitter
    ADAPTIVE:     delay = base * (1 + error_rate * 10) * 2^min(attempt,5) + jitter
    """

    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    FIXED = "fixed"
    ADAPTIVE = "adaptive"


class CircuitState(str, Enum):
    """
    熔断器状态枚举 - 三状态机

    CLOSED:   闭合 - 正常处理所有请求
    OPEN:     断开 - 拒绝请求，快速失败
    HALF_OPEN: 半开 - 允许有限探测请求
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# ============================================================================
# 数据类
# ============================================================================


@dataclass
class ErrorContext:
    """
    错误上下文信息

    全链路追踪的关键载体，每个异常必须携带上下文，
    确保在分布式环境中能追踪请求的完整路径。

    Attributes:
        trace_id: 全链路追踪ID，格式 "err_{uuid前12位}"
        service: 服务名 (video_generation/gemini/gws)
        provider: Provider名 (seedance/kling/veo/gemini)
        model: 模型名 (如 "2.5-pro")
        user_id: 用户ID
        session_id: 会话ID
        extra: 扩展字段字典，可存放任意业务相关数据
    """

    trace_id: str = ""
    service: str = ""
    provider: str = ""
    model: str = ""
    user_id: str = ""
    session_id: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self: "ErrorContext") -> Dict[str, Any]:
        """序列化为字典，用于日志和API响应"""
        return {
            "trace_id": self.trace_id,
            "service": self.service,
            "provider": self.provider,
            "model": self.model,
            "user_id": self.user_id,
            "session_id": self.session_id,
            **self.extra,
        }

    @classmethod
    def from_dict(cls: type["ErrorContext"], data: Dict[str, Any]) -> "ErrorContext":
        """从字典反序列化"""
        extra = {
            k: v
            for k, v in data.items()
            if k
            not in {
                "trace_id",
                "service",
                "provider",
                "model",
                "user_id",
                "session_id",
            }
        }
        return cls(
            trace_id=data.get("trace_id", ""),
            service=data.get("service", ""),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            user_id=data.get("user_id", ""),
            session_id=data.get("session_id", ""),
            extra=extra,
        )


@dataclass
class UserNotification:
    """
    用户通知

    将技术错误转换为用户友好的通知对象，包含可操作的建议和按钮。

    Attributes:
        level: 通知级别 (info/warning/error/critical)
        title: 通知标题
        message: 通知正文
        suggestion: 给用户的修复建议
        trace_id: 追踪ID（用于客服排查）
        actions: 可操作按钮列表 [{"label": "重试", "action": "retry"}]
    """

    level: NotificationLevel
    title: str
    message: str
    suggestion: str = ""
    trace_id: str = ""
    actions: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self: "UserNotification") -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "level": self.level.value,
            "title": self.title,
            "message": self.message,
            "suggestion": self.suggestion,
            "trace_id": self.trace_id,
            "actions": self.actions,
        }


# ============================================================================
# 异常基类
# ============================================================================


class SkillException(Exception):
    """
    Skill 统一异常基类

    所有自定义异常必须继承此类，确保全链路错误处理一致性。
    支持自动序列化为 JSON，用于日志记录和 API 响应。

    Class Attributes (子类必须覆盖):
        error_code: 统一错误码，格式 "{分类字母}{3位数字}"，如 "S001"
        error_category: 错误分类 (ErrorCategory 枚举)
        notification_level: 通知级别 (NotificationLevel 枚举)
        default_message: 默认错误消息
        retryable: 是否可重试 (布尔值)
        remediation_hint: 给用户的修复建议

    Instance Attributes:
        message: 实际错误消息
        context: ErrorContext 错误上下文
        cause: 原始异常 (异常链)
        extra_data: 额外数据字典
        error_id: 唯一错误实例ID

    Methods:
        to_dict() -> Dict[str, Any]: 序列化为字典
        to_notification() -> UserNotification: 转换为用户通知
        __repr__() -> str: 调试字符串

    Raises:
        无 (本类为基类，不直接抛出)

    Example:
        >>> try:
        ...     raise ValidationError("参数缺失: prompt")
        ... except SkillException as e:
        ...     print(e.error_code)  # "C002"
        ...     print(e.to_dict())   # JSON可序列化字典
    """

    # 类属性: 子类必须覆盖这些默认值
    error_code: ClassVar[str] = "E003"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.SYSTEM
    notification_level: ClassVar[NotificationLevel] = NotificationLevel.ERROR
    default_message: ClassVar[str] = "未知错误"
    retryable: ClassVar[bool] = False
    remediation_hint: ClassVar[str] = "联系支持团队"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        context: Optional[ErrorContext] = None,
        cause: Optional[Exception] = None,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        初始化异常

        Args:
            message: 错误消息 (为 None 时使用 default_message)
            context: 错误上下文 (为 None 时创建空上下文)
            cause: 原始异常，用于异常链
            extra_data: 额外业务数据

        Note:
            - trace_id 为空时会自动生成 error_id
            - message 通过 super().__init__() 传递给 Exception 基类
        """
        self.message: str = message or self.default_message
        self.context: ErrorContext = context or ErrorContext()
        self.cause: Optional[Exception] = cause
        self.extra_data: Dict[str, Any] = extra_data or {}
        self.error_id: str = f"err_{uuid.uuid4().hex[:12]}"

        # 自动填充 trace_id (若未提供)
        if not self.context.trace_id:
            self.context.trace_id = self.error_id

        super().__init__(self.message)

    @property
    def is_retryable(self: "SkillException") -> bool:
        """是否可重试 - 供重试引擎判断"""
        return self.retryable

    @property
    def user_message(self: "SkillException") -> str:
        """给用户看的友好消息"""
        return self.message

    def to_dict(self: "SkillException") -> Dict[str, Any]:
        """
        序列化为字典，用于日志和 API 响应

        Returns:
            包含所有元信息的字典，可直接 json.dumps()

        Example Output:
            {
                "error_id": "err_a1b2c3d4e5f6",
                "error_code": "S001",
                "category": "server",
                "level": "info",
                "message": "请求太频繁",
                "retryable": True,
                "remediation_hint": "系统将自动重试，请稍等",
                "context": {"trace_id": "...", "service": "..."},
                "cause": None,
                "extra_data": {},
            }
        """
        return {
            "error_id": self.error_id,
            "error_code": self.error_code,
            "category": self.error_category.value,
            "level": self.notification_level.value,
            "message": self.message,
            "user_message": self.user_message,
            "retryable": self.is_retryable,
            "remediation_hint": self.remediation_hint,
            "context": self.context.to_dict(),
            "cause": str(self.cause) if self.cause else None,
            "extra_data": self.extra_data,
        }

    def to_notification(self: "SkillException") -> UserNotification:
        """
        转换为用户友好的通知

        Returns:
            UserNotification 对象，包含标题、消息、建议
        """
        return UserNotification(
            level=self.notification_level,
            title=f"错误 [{self.error_code}]",
            message=self.user_message,
            suggestion=self.remediation_hint,
            trace_id=self.context.trace_id,
            actions=[
                {"label": "重试", "action": "retry"},
                {"label": "联系支持", "action": "contact_support"},
            ]
            if self.retryable
            else [
                {"label": "联系支持", "action": "contact_support"},
            ],
        )

    def __repr__(self: "SkillException") -> str:
        return (
            f"{self.__class__.__name__}("
            f"code={self.error_code}, "
            f"msg={self.message!r}, "
            f"retryable={self.retryable}, "
            f"trace_id={self.context.trace_id}"
            f")"
        )

    def __str__(self: "SkillException") -> str:
        return f"[{self.error_code}] {self.message}"


# ============================================================================
# 客户端错误 (C开头 - 不重试)
# ============================================================================


class ValidationError(SkillException):
    """
    参数校验错误 - 客户端传入参数有问题

    Error Code: C002
    Retryable: No (需用户修正参数)
    HTTP Mapping: 400
    """

    error_code = "C002"
    error_category = ErrorCategory.CLIENT
    notification_level = NotificationLevel.ERROR
    default_message = "参数格式错误"
    retryable = False
    remediation_hint = "检查请求参数是否符合API文档要求"


class ParamMissingError(ValidationError):
    """
    参数缺失错误 - 必填参数未提供

    Error Code: C003
    HTTP Mapping: 400
    """

    error_code = "C003"
    default_message = "缺少必要参数"
    remediation_hint = "检查必填参数是否完整"


class ParamFormatError(ValidationError):
    """
    参数格式错误 - 参数类型或格式不正确

    Error Code: C004
    HTTP Mapping: 400
    """

    error_code = "C004"
    default_message = "参数格式不正确"
    remediation_hint = "检查参数类型和格式是否符合要求"


class ParamValueError(ValidationError):
    """
    参数值错误 - 参数值超出有效范围

    Error Code: C005
    HTTP Mapping: 400
    """

    error_code = "C005"
    default_message = "参数值无效"
    remediation_hint = "检查参数值是否在有效范围内"


class AuthError(SkillException):
    """
    认证失败 - Cookie/API Key 等凭证无效

    Error Code: C101
    Retryable: No (需重新认证)
    HTTP Mapping: 401
    """

    error_code = "C101"
    error_category = ErrorCategory.CLIENT
    notification_level = NotificationLevel.ERROR
    default_message = "认证失败"
    retryable = False
    remediation_hint = "检查认证信息是否过期，尝试重新登录"


class PermissionDeniedError(SkillException):
    """
    权限不足 - 账户无权使用该功能

    Error Code: C201
    Retryable: No
    HTTP Mapping: 403
    """

    error_code = "C201"
    error_category = ErrorCategory.CLIENT
    notification_level = NotificationLevel.ERROR
    default_message = "权限不足"
    retryable = False
    remediation_hint = "升级账户权限或联系管理员"


class QuotaExceededError(SkillException):
    """
    配额超限 - 月度/日度配额已用完

    Error Code: C301
    Retryable: No
    HTTP Mapping: 429
    """

    error_code = "C301"
    error_category = ErrorCategory.CLIENT
    notification_level = NotificationLevel.ERROR
    default_message = "配额已用完"
    retryable = False
    remediation_hint = "升级套餐或等待配额重置"


class UnsupportedOperationError(SkillException):
    """
    不支持的操作 - 请求的操作不被支持

    Error Code: C401
    Retryable: No
    """

    error_code = "C401"
    error_category = ErrorCategory.CLIENT
    notification_level = NotificationLevel.WARNING
    default_message = "不支持的操作"
    retryable = False
    remediation_hint = "查看支持的操作列表"


class UnsupportedFormatError(SkillException):
    """
    不支持的文件格式

    Error Code: C402
    Retryable: No
    HTTP Mapping: 415
    """

    error_code = "C402"
    error_category = ErrorCategory.CLIENT
    notification_level = NotificationLevel.WARNING
    default_message = "不支持的文件格式"
    retryable = False
    remediation_hint = "转换为支持的格式(JPG/PNG/MP4/WEBM)后重试"


class UnsupportedModelError(SkillException):
    """
    不支持的模型 - 请求的模型不可用

    Error Code: C403
    Retryable: No
    """

    error_code = "C403"
    error_category = ErrorCategory.CLIENT
    notification_level = NotificationLevel.WARNING
    default_message = "不支持的模型"
    retryable = False
    remediation_hint = "查看支持的模型列表"


# ============================================================================
# 服务端错误 (S开头 - 可重试)
# ============================================================================


class RateLimitError(SkillException):
    """
    速率限制 - 请求过于频繁，需等待后重试

    Error Code: S001
    Retryable: Yes (特殊处理: 优先使用服务端 Retry-After)
    HTTP Mapping: 429

    Special Attributes:
        retry_after: 服务端建议等待时间(秒)

    Example:
        >>> raise RateLimitError("请求过于频繁", retry_after=5.0)
    """

    error_code = "S001"
    error_category = ErrorCategory.SERVER
    notification_level = NotificationLevel.INFO
    default_message = "请求太频繁"
    retryable = True
    remediation_hint = "系统将自动重试，请稍等"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        retry_after: float = 1.0,
        context: Optional[ErrorContext] = None,
        cause: Optional[Exception] = None,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.retry_after: float = retry_after
        super().__init__(message, context=context, cause=cause, extra_data=extra_data)

    def to_dict(self: "RateLimitError") -> Dict[str, Any]:
        """覆盖: 增加 retry_after 字段"""
        data = super().to_dict()
        data["retry_after"] = self.retry_after
        return data


class ServerOverloadedError(SkillException):
    """
    服务器过载 - 服务端负载过高

    Error Code: S002
    Retryable: Yes
    HTTP Mapping: 503
    """

    error_code = "S002"
    error_category = ErrorCategory.SERVER
    notification_level = NotificationLevel.INFO
    default_message = "服务器繁忙"
    retryable = True
    remediation_hint = "系统正在自动排队处理，请稍等"


class ServiceUnavailableError(SkillException):
    """
    服务不可用 - 服务维护或临时故障

    Error Code: S003
    Retryable: Yes
    HTTP Mapping: 503
    """

    error_code = "S003"
    error_category = ErrorCategory.SERVER
    notification_level = NotificationLevel.WARNING
    default_message = "服务暂时不可用"
    retryable = True
    remediation_hint = "服务正在维护或临时故障，系统将自动重试"


class DependencyError(SkillException):
    """
    依赖服务错误 - 下游依赖服务故障

    Error Code: S004
    Retryable: Yes
    HTTP Mapping: 502
    """

    error_code = "S004"
    error_category = ErrorCategory.SERVER
    notification_level = NotificationLevel.WARNING
    default_message = "依赖服务暂时不可用"
    retryable = True
    remediation_hint = "依赖服务正在恢复，系统将自动重试"


class InternalServerError(SkillException):
    """
    内部服务器错误 - 服务端未预期错误

    Error Code: S101
    Retryable: Yes
    HTTP Mapping: 500
    """

    error_code = "S101"
    error_category = ErrorCategory.SERVER
    notification_level = NotificationLevel.WARNING
    default_message = "服务器内部错误"
    retryable = True
    remediation_hint = "系统正在自动重试，如持续失败请联系支持"


# ============================================================================
# 网络错误 (N开头 - 可重试)
# ============================================================================


class ConnectionTimeoutError(SkillException):
    """
    连接超时 - TCP 连接建立超时

    Error Code: N001
    Retryable: Yes (指数退避)
    """

    error_code = "N001"
    error_category = ErrorCategory.NETWORK
    notification_level = NotificationLevel.INFO
    default_message = "连接服务器超时"
    retryable = True
    remediation_hint = "检查网络连接，系统将自动重试"


class ReadTimeoutError(SkillException):
    """
    读取超时 - 服务端响应超时

    Error Code: N002
    Retryable: Yes (指数退避，较长延迟)
    """

    error_code = "N002"
    error_category = ErrorCategory.NETWORK
    notification_level = NotificationLevel.INFO
    default_message = "服务器响应超时"
    retryable = True
    remediation_hint = "任务可能仍在执行，系统将自动检查"


class DNSResolutionError(SkillException):
    """
    DNS 解析失败 - 域名无法解析

    Error Code: N003
    Retryable: Yes (固定间隔，DNS 缓存需要时间)
    """

    error_code = "N003"
    error_category = ErrorCategory.NETWORK
    notification_level = NotificationLevel.WARNING
    default_message = "域名解析失败"
    retryable = True
    remediation_hint = "检查 DNS 设置或网络配置"


class ConnectionResetError(SkillException):
    """
    连接被重置 - 网络连接被中断

    Error Code: N004
    Retryable: Yes
    """

    error_code = "N004"
    error_category = ErrorCategory.NETWORK
    notification_level = NotificationLevel.INFO
    default_message = "网络连接被中断"
    retryable = True
    remediation_hint = "网络波动，系统将自动重试"


# ============================================================================
# 任务错误 (T开头 - 部分可重试)
# ============================================================================


class TaskTimeoutError(SkillException):
    """
    任务执行超时 - 任务处理时间超过预期

    Error Code: T001
    Retryable: Yes
    """

    error_code = "T001"
    error_category = ErrorCategory.TASK
    notification_level = NotificationLevel.INFO
    default_message = "任务处理时间较长"
    retryable = True
    remediation_hint = "系统将自动重试，请耐心等待"


class TaskFailedError(SkillException):
    """
    任务执行失败 - 任务逻辑执行失败 (不可重试)

    Error Code: T002
    Retryable: No (业务逻辑错误，重试无用)
    """

    error_code = "T002"
    error_category = ErrorCategory.TASK
    notification_level = NotificationLevel.ERROR
    default_message = "任务执行失败"
    retryable = False
    remediation_hint = "查看错误详情后调整请求参数"


class TaskCancelledError(SkillException):
    """
    任务被取消 - 用户或系统主动取消

    Error Code: T003
    Retryable: No
    """

    error_code = "T003"
    error_category = ErrorCategory.TASK
    notification_level = NotificationLevel.INFO
    default_message = "任务已取消"
    retryable = False
    remediation_hint = "任务已被取消，如需重新执行请重新发起"


class GenerationError(SkillException):
    """
    内容生成失败 - 生成任务失败 (可重试)

    Error Code: T101
    Retryable: Yes
    """

    error_code = "T101"
    error_category = ErrorCategory.TASK
    notification_level = NotificationLevel.WARNING
    default_message = "内容生成失败"
    retryable = True
    remediation_hint = "简化提示词后系统将自动重试"


class SafetyBlockedError(SkillException):
    """
    安全拦截 - 内容触发安全过滤器 (不可重试)

    Error Code: T102
    Retryable: No (需用户修改输入)
    """

    error_code = "T102"
    error_category = ErrorCategory.TASK
    notification_level = NotificationLevel.WARNING
    default_message = "内容触发安全过滤器"
    retryable = False
    remediation_hint = "修改提示词，避免敏感或违规内容"


class ContentFilteredError(SkillException):
    """
    内容被过滤 - 生成内容被过滤 (不可重试)

    Error Code: T103
    Retryable: No (需用户修改输入)
    """

    error_code = "T103"
    error_category = ErrorCategory.TASK
    notification_level = NotificationLevel.WARNING
    default_message = "生成内容被过滤"
    retryable = False
    remediation_hint = "调整提示词描述，避免触发过滤规则"


class VideoGenerationError(SkillException):
    """
    视频生成失败 - 视频生成任务失败 (可重试 + 降级)

    Error Code: T104
    Retryable: Yes (触发降级前最多重试3次)
    """

    error_code = "T104"
    error_category = ErrorCategory.TASK
    notification_level = NotificationLevel.WARNING
    default_message = "视频生成失败"
    retryable = True
    remediation_hint = "系统将自动切换到备用服务"


class ImageGenerationError(SkillException):
    """
    图片生成失败 - 图片生成任务失败 (可重试)

    Error Code: T105
    Retryable: Yes
    """

    error_code = "T105"
    error_category = ErrorCategory.TASK
    notification_level = NotificationLevel.WARNING
    default_message = "图片生成失败"
    retryable = True
    remediation_hint = "系统将自动重试"


class AllProvidersFailedError(SkillException):
    """
    所有提供商失败 - 所有 Provider 均不可用 (严重)

    Error Code: T201
    Retryable: No
    """

    error_code = "T201"
    error_category = ErrorCategory.TASK
    notification_level = NotificationLevel.CRITICAL
    default_message = "所有可用服务均不可用"
    retryable = False
    remediation_hint = "请稍后重试或联系客服支持"


class FallbackChainExhaustedError(SkillException):
    """
    降级链耗尽 - 所有降级选项已用完 (严重)

    Error Code: T202
    Retryable: No
    """

    error_code = "T202"
    error_category = ErrorCategory.TASK
    notification_level = NotificationLevel.CRITICAL
    default_message = "备用服务也已耗尽"
    retryable = False
    remediation_hint = "所有降级选项已用完，请等待服务恢复"


# ============================================================================
# 资源错误 (R开头)
# ============================================================================


class ResourceNotFoundError(SkillException):
    """
    资源不存在

    Error Code: R001
    Retryable: No
    HTTP Mapping: 404
    """

    error_code = "R001"
    error_category = ErrorCategory.RESOURCE
    notification_level = NotificationLevel.WARNING
    default_message = "请求的资源不存在"
    retryable = False
    remediation_hint = "检查资源ID是否正确"


class ResourceExpiredError(SkillException):
    """
    资源已过期 - 资源链接或临时文件过期

    Error Code: R002
    Retryable: No
    """

    error_code = "R002"
    error_category = ErrorCategory.RESOURCE
    notification_level = NotificationLevel.WARNING
    default_message = "资源链接已过期"
    retryable = False
    remediation_hint = "重新生成资源"


class FormatNotSupportedError(SkillException):
    """
    格式不支持 - 文件格式无法处理

    Error Code: R004
    Retryable: No
    """

    error_code = "R004"
    error_category = ErrorCategory.RESOURCE
    notification_level = NotificationLevel.WARNING
    default_message = "不支持的文件格式"
    retryable = False
    remediation_hint = "转换为支持的格式(jpg/png/mp4/webm)"


# ============================================================================
# 配置错误 (F开头 - 不重试)
# ============================================================================


class ConfigNotFoundError(SkillException):
    """
    配置缺失 - 缺少必要的配置文件或配置项

    Error Code: F001
    Retryable: No (需修正配置)
    """

    error_code = "F001"
    error_category = ErrorCategory.CONFIGURATION
    notification_level = NotificationLevel.ERROR
    default_message = "缺少必要配置"
    retryable = False
    remediation_hint = "检查环境变量和配置文件"


class CredentialExpiredError(SkillException):
    """
    凭证过期 - API Key 或认证凭证过期

    Error Code: F003
    Retryable: No
    """

    error_code = "F003"
    error_category = ErrorCategory.CONFIGURATION
    notification_level = NotificationLevel.ERROR
    default_message = "凭证已过期"
    retryable = False
    remediation_hint = "更新凭证信息"


class EnvVarMissingError(SkillException):
    """
    环境变量缺失 - 需要的环境变量未设置

    Error Code: F004
    Retryable: No
    """

    error_code = "F004"
    error_category = ErrorCategory.CONFIGURATION
    notification_level = NotificationLevel.ERROR
    default_message = "缺少环境变量"
    retryable = False
    remediation_hint = "设置对应的环境变量"


# ============================================================================
# 系统错误 (E开头)
# ============================================================================


class InternalError(SkillException):
    """
    内部错误 - 系统未预期错误 (谨慎重试，仅1次)

    Error Code: E001
    Retryable: Yes (但 RetryEngine 策略应限制为1次)
    """

    error_code = "E001"
    error_category = ErrorCategory.SYSTEM
    notification_level = NotificationLevel.ERROR
    default_message = "系统内部错误"
    retryable = True
    remediation_hint = "系统正在自动恢复，如持续失败请联系支持"


class NotImplementedError(SkillException):
    """
    功能未实现 - 调用的功能尚未开发完成

    Error Code: E002
    Retryable: No
    """

    error_code = "E002"
    error_category = ErrorCategory.SYSTEM
    notification_level = NotificationLevel.WARNING
    default_message = "该功能正在开发中"
    retryable = False
    remediation_hint = "等待功能上线"


class UnknownError(SkillException):
    """
    未知错误 - 无法分类的错误 (基类默认)

    Error Code: E003
    Retryable: No (默认策略)
    """

    error_code = "E003"
    error_category = ErrorCategory.SYSTEM
    notification_level = NotificationLevel.ERROR
    default_message = "未知错误"
    retryable = False
    remediation_hint = "联系支持团队"


# ============================================================================
# 异常工厂
# ============================================================================


class ExceptionFactory:
    """
    异常工厂 - 将各类原始异常转换为 SkillException

    支持转换来源:
    - HTTP 状态码 -> SkillException
    - Provider 原始错误码 -> SkillException
    - 标准 Python 异常 (aiohttp/asyncio) -> SkillException
    - 未知异常 -> InternalError

    Class Attributes:
        HTTP_EXCEPTION_MAP: HTTP状态码到异常类的映射
        PROVIDER_ERROR_MAP: Provider错误关键词到异常类的映射

    Methods:
        from_http_status: 根据 HTTP 状态码创建异常
        from_provider_error: 根据 Provider 错误码创建异常
        from_exception: 将任意异常智能转换

    Example:
        >>> # 从 HTTP 429 创建 RateLimitError
        >>> exc = ExceptionFactory.from_http_status(429, "Too Many Requests")
        >>> isinstance(exc, RateLimitError)
        True

        >>> # 从 Provider 错误创建
        >>> exc = ExceptionFactory.from_provider_error("kling", "rate_limit")
        >>> isinstance(exc, RateLimitError)
        True

        >>> # 从任意异常转换
        >>> exc = ExceptionFactory.from_exception(TimeoutError())
        >>> isinstance(exc, ReadTimeoutError)
        True
    """

    # HTTP 状态码到异常类的映射表
    HTTP_EXCEPTION_MAP: Dict[int, Any] = {
        400: ValidationError,
        401: AuthError,
        403: PermissionDeniedError,
        404: ResourceNotFoundError,
        408: ConnectionTimeoutError,
        413: (ValidationError, "文件过大"),
        415: UnsupportedFormatError,
        429: RateLimitError,
        500: InternalServerError,
        502: DependencyError,
        503: ServiceUnavailableError,
        504: ReadTimeoutError,
    }

    # 各 Provider 错误关键词映射表 (可扩展)
    PROVIDER_ERROR_MAP: Dict[str, Dict[str, Type[SkillException]]] = {
        "gemini": {
            "auth_error": AuthError,
            "cookie_expired": AuthError,
            "rate_limit": RateLimitError,
            "usage_limit_exceeded": QuotaExceededError,
            "temporarily_blocked": RateLimitError,
            "model_invalid": UnsupportedModelError,
            "safety": SafetyBlockedError,
            "censor": ContentFilteredError,
        },
        "seedance": {
            "rate_limit": RateLimitError,
            "quota_exceeded": QuotaExceededError,
            "generation_failed": VideoGenerationError,
            "content_filtered": ContentFilteredError,
            "unavailable": ServiceUnavailableError,
        },
        "kling": {
            "rate_limit": RateLimitError,
            "quota_exceeded": QuotaExceededError,
            "task_failed": TaskFailedError,
            "generation_error": VideoGenerationError,
            "content_moderation": SafetyBlockedError,
        },
        "veo": {
            "rate_limit": RateLimitError,
            "quota_exceeded": QuotaExceededError,
            "generation_failed": VideoGenerationError,
            "unavailable": ServiceUnavailableError,
        },
        "vidu": {
            "rate_limit": RateLimitError,
            "quota_exceeded": QuotaExceededError,
            "generation_failed": VideoGenerationError,
            "unavailable": ServiceUnavailableError,
        },
    }

    @classmethod
    def from_http_status(
        cls,
        status_code: int,
        message: Optional[str] = None,
        *,
        provider: str = "",
        trace_id: str = "",
        retry_after: Optional[float] = None,
        **kwargs: Any,
    ) -> SkillException:
        """
        根据 HTTP 状态码创建对应异常

        Args:
            status_code: HTTP 状态码
            message: 错误消息 (可选)
            provider: Provider 名称
            trace_id: 追踪 ID
            retry_after: 速率限制等待时间 (S001 专用)
            **kwargs: 额外上下文数据

        Returns:
            SkillException 子类实例

        Raises:
            不抛出异常，未知状态码映射为 InternalServerError
        """
        exc_info = cls.HTTP_EXCEPTION_MAP.get(status_code, InternalServerError)

        if isinstance(exc_info, tuple):
            exc_class, default_msg = exc_info
            message = message or default_msg
        else:
            exc_class = exc_info

        context = ErrorContext(
            trace_id=trace_id,
            provider=provider,
            extra={"http_status": status_code, **kwargs},
        )

        # 速率限制特殊处理: 提取 retry_after
        if exc_class == RateLimitError:
            return RateLimitError(
                message, retry_after=retry_after or 1.0, context=context
            )

        return exc_class(message, context=context)

    @classmethod
    def from_provider_error(
        cls,
        provider: str,
        error_code: str,
        message: Optional[str] = None,
        *,
        trace_id: str = "",
        **kwargs: Any,
    ) -> SkillException:
        """
        根据 Provider 错误码创建对应异常

        Args:
            provider: Provider 名称 (gemini/seedance/kling/veo/vidu)
            error_code: Provider 原始错误码/关键词
            message: 错误消息 (可选)
            trace_id: 追踪 ID
            **kwargs: 额外上下文数据

        Returns:
            SkillException 子类实例

        Note:
            未知 Provider 或错误码映射为 InternalError
        """
        provider_errors = cls.PROVIDER_ERROR_MAP.get(provider, {})
        exc_class = provider_errors.get(error_code.lower(), InternalServerError)

        context = ErrorContext(
            trace_id=trace_id,
            provider=provider,
            extra={"provider_error_code": error_code, **kwargs},
        )

        return exc_class(
            message or f"{provider} 错误: {error_code}", context=context
        )

    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        *,
        service: str = "",
        provider: str = "",
        trace_id: str = "",
    ) -> SkillException:
        """
        将任意异常转换为 SkillException (智能判断类型)

        Args:
            exc: 原始异常
            service: 服务名称
            provider: Provider 名称
            trace_id: 追踪 ID

        Returns:
            SkillException 子类实例

        Note:
            - 已是 SkillException 则直接返回
            - 支持 aiohttp 和 asyncio 常见异常
            - 无法识别的映射为 InternalError
        """
        # 已是 SkillException，直接返回
        if isinstance(exc, SkillException):
            return exc

        # 根据异常类型映射
        exc_type_map: List[Tuple[type, Any]] = [
            # (源异常类型, 目标异常类或转换函数)
            (ConnectionRefusedError, exceptions_module_ConnectionResetError),
            (TimeoutError, ReadTimeoutError),
        ]

        # 尝试导入可选依赖
        try:
            import aiohttp

            exc_type_map.extend([
                (aiohttp.ClientConnectionError, exceptions_module_ConnectionResetError),
                (aiohttp.ServerTimeoutError, ConnectionTimeoutError),
                (
                    aiohttp.ClientResponseError,
                    lambda e: cls.from_http_status(
                        e.status, str(e), provider=provider, trace_id=trace_id
                    ),
                ),
            ])
        except ImportError:
            pass

        try:
            import asyncio

            exc_type_map.append((asyncio.TimeoutError, ReadTimeoutError))
        except ImportError:
            pass

        for src_type, target in exc_type_map:
            if isinstance(exc, src_type):
                ctx = ErrorContext(
                    trace_id=trace_id,
                    service=service,
                    provider=provider,
                )
                if callable(target) and not isinstance(target, type):
                    return target(exc)
                return target(str(exc), context=ctx, cause=exc)

        # 默认转为内部错误
        context = ErrorContext(
            trace_id=trace_id,
            service=service,
            provider=provider,
            extra={"original_type": type(exc).__name__},
        )
        return InternalError(str(exc), context=context, cause=exc)

    @classmethod
    def register_provider_errors(
        cls,
        provider: str,
        error_map: Dict[str, Type[SkillException]],
    ) -> None:
        """
        注册新 Provider 的错误映射 (扩展点)

        Args:
            provider: Provider 名称
            error_map: {错误关键词: 异常类} 映射
        """
        if provider not in cls.PROVIDER_ERROR_MAP:
            cls.PROVIDER_ERROR_MAP[provider] = {}
        cls.PROVIDER_ERROR_MAP[provider].update(error_map)


# 本地引用别名（解决循环引用问题）
exceptions_module_ConnectionResetError = ConnectionResetError


# ============================================================================
# 模块导出
# ============================================================================

__all__ = [
    # 枚举
    "ErrorCategory",
    "NotificationLevel",
    "FallbackLevel",
    "BackoffStrategy",
    "CircuitState",
    # 数据类
    "ErrorContext",
    "UserNotification",
    # 异常基类
    "SkillException",
    # 客户端错误
    "ValidationError",
    "ParamMissingError",
    "ParamFormatError",
    "ParamValueError",
    "AuthError",
    "PermissionDeniedError",
    "QuotaExceededError",
    "UnsupportedOperationError",
    "UnsupportedFormatError",
    "UnsupportedModelError",
    # 服务端错误
    "RateLimitError",
    "ServerOverloadedError",
    "ServiceUnavailableError",
    "DependencyError",
    "InternalServerError",
    # 网络错误
    "ConnectionTimeoutError",
    "ReadTimeoutError",
    "DNSResolutionError",
    "ConnectionResetError",
    # 任务错误
    "TaskTimeoutError",
    "TaskFailedError",
    "TaskCancelledError",
    "GenerationError",
    "SafetyBlockedError",
    "ContentFilteredError",
    "VideoGenerationError",
    "ImageGenerationError",
    "AllProvidersFailedError",
    "FallbackChainExhaustedError",
    # 资源错误
    "ResourceNotFoundError",
    "ResourceExpiredError",
    "FormatNotSupportedError",
    # 配置错误
    "ConfigNotFoundError",
    "CredentialExpiredError",
    "EnvVarMissingError",
    # 系统错误
    "InternalError",
    "NotImplementedError",
    "UnknownError",
    # 工厂
    "ExceptionFactory",
]
