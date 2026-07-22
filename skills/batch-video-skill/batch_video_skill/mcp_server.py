#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini MCP Server - 基于 FastMCP 的标准化工具服务

职责:
- 注册并管理 10 个 MCP 工具
- 统一错误处理（异常→标准化错误响应）
- 上下文管理（trace_id、会话状态）
- 生命周期管理（启动、运行、关闭）

Usage:
    server = GeminiMCPServer(skill_controller)
    await server.initialize()
    server.run()  # 阻塞运行

技术栈:
    - fastmcp >= 1.0.0
    - pydantic >= 2.0.0
    - loguru >= 0.7.0
"""

from __future__ import annotations

import asyncio
import functools
import sys
import time
import traceback
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    Optional,
    TypeVar,
)

from loguru import logger
from pydantic import BaseModel, Field, ValidationError as PydanticValidationError, field_validator

# ─── 可选 MCP 框架导入 ───
try:
    from mcp.server.fastmcp import FastMCP

    HAS_FASTMCP = True
except ImportError:
    HAS_FASTMCP = False
    logger.warning("FastMCP not installed. MCP server functionality unavailable.")

    # 占位类型
    class FastMCP:  # type: ignore[no-redef]
        """FastMCP 占位类"""

        def __init__(self: "FastMCP", name: str = "gemini-module") -> None:
            self.name = name

        def tool(self: "FastMCP", name: str | None = None, description: str | None = None):
            """占位工具装饰器"""

            def decorator(func: Callable) -> Callable:
                return func

            return decorator

        def run(self: "FastMCP", transport: str = "stdio") -> None:
            """占位运行方法"""
            raise RuntimeError("FastMCP not installed. Run: pip install fastmcp>=1.0.0")


# ─── 类型定义 ───
T = TypeVar("T")


# ─── 上下文变量 ───
request_ctx: ContextVar[str] = ContextVar("request_ctx", default="")
session_ctx: ContextVar[dict[str, Any]] = ContextVar("session_ctx", default={})


# ═══════════════════════════════════════════════════════════════
# 内部模块依赖（带优雅降级）
# ═══════════════════════════════════════════════════════════════

try:
    from module_gemini.core.client import GeminiClient

    HAS_CLIENT = True
except ImportError:
    HAS_CLIENT = False
    GeminiClient = Any  # type: ignore[misc, assignment]

try:
    from module_gemini.models.config import GeminiConfig

    HAS_CONFIG = True
except ImportError:
    HAS_CONFIG = False

    class GeminiConfig(BaseModel):  # type: ignore[no-redef]
        """Gemini 配置占位"""

        secure_1psid: str = Field(default="", description="__Secure-1PSID Cookie")
        secure_1psidts: str = Field(default="", description="__Secure-1PSIDTS Cookie")
        proxy: Optional[str] = Field(default=None, description="代理地址")
        sheets_id: Optional[str] = Field(default=None, description="Sheets ID")
        sheets_credentials: Optional[str] = Field(default=None, description="Sheets SA凭证路径")
        sheets_enabled: bool = Field(default=True, description="是否启用Sheets同步")
        model: str = Field(default="pro", description="Gemini模型")
        timeout: int = Field(default=120, description="请求超时(秒)")
        max_retries: int = Field(default=3, description="最大重试次数")
        retry_delay: float = Field(default=2.0, description="重试延迟(秒)")
        prompt_max_retries: int = Field(default=3, description="提示词最大重试次数")
        prompt_timeout: int = Field(default=60, description="提示词超时(秒)")
        max_image_size_mb: int = Field(default=20, description="最大图片大小(MB)")
        log_level: str = Field(default="INFO", description="日志级别")

        @classmethod
        def from_env(cls: type["GeminiConfig"]) -> "GeminiConfig":
            """从环境变量加载配置"""
            import os

            return cls(
                secure_1psid=os.environ.get("GEMINI_1PSID", ""),
                secure_1psidts=os.environ.get("GEMINI_1PSIDTS", ""),
                proxy=os.environ.get("GEMINI_PROXY", None),
                sheets_id=os.environ.get("GEMINI_SHEETS_ID", None),
                sheets_credentials=os.environ.get("GEMINI_SHEETS_CREDENTIALS", None),
                sheets_enabled=os.environ.get("GEMINI_SHEETS_ENABLED", "true").lower()
                == "true",
                model=os.environ.get("GEMINI_MODEL", "pro"),
                timeout=int(os.environ.get("GEMINI_TIMEOUT", "120")),
                max_retries=int(os.environ.get("GEMINI_MAX_RETRIES", "3")),
                retry_delay=float(os.environ.get("GEMINI_RETRY_DELAY", "2.0")),
                prompt_max_retries=int(os.environ.get("GEMINI_PROMPT_MAX_RETRIES", "3")),
                prompt_timeout=int(os.environ.get("GEMINI_PROMPT_TIMEOUT", "60")),
                max_image_size_mb=int(os.environ.get("GEMINI_MAX_IMAGE_SIZE_MB", "20")),
                log_level=os.environ.get("GEMINI_LOG_LEVEL", "INFO"),
            )

try:
    from module_gemini.models.prompt import ImageInput

    HAS_IMAGE_INPUT = True
except ImportError:
    HAS_IMAGE_INPUT = False

    class ImageInput(BaseModel):  # type: ignore[no-redef]
        """图片输入占位"""

        path: Optional[str] = None
        url: Optional[str] = None
        data: Optional[bytes] = None

        @classmethod
        def from_path(cls: type["ImageInput"], path: str) -> "ImageInput":
            """从路径创建图片输入"""
            return cls(path=path)


try:
    from module_gemini.exceptions.errors import (
        AuthError,
        GeminiModuleError,
        NotInitializedError,
        ValidationError as GeminiValidationError,
    )

    HAS_EXCEPTIONS = True
except ImportError:
    HAS_EXCEPTIONS = False

    class GeminiModuleError(Exception):  # type: ignore[no-redef]
        """模块基类异常"""

        def __init__(self: "GeminiModuleError", message: str = "", details: dict[str, Any] | None = None) -> None:
            super().__init__(message)
            self.details = details or {}

    class NotInitializedError(GeminiModuleError):  # type: ignore[no-redef]
        """客户端未初始化"""

    class AuthError(GeminiModuleError):  # type: ignore[no-redef]
        """认证失败"""

    class GeminiValidationError(GeminiModuleError):  # type: ignore[no-redef]
        """参数校验失败"""


# ═══════════════════════════════════════════════════════════════
# 工具返回模型
# ═══════════════════════════════════════════════════════════════


class SingleVideoResult(BaseModel):
    """T01: 单产品视频生成返回"""

    task_id: str = Field(description="任务唯一标识")
    status: str = Field(description="最终状态: completed/failed")
    video_url: Optional[str] = Field(default=None, description="视频下载URL")
    video_local_path: Optional[str] = Field(default=None, description="本地保存路径")
    matched_gem_id: str = Field(default="", description="匹配的Gem模板ID")
    matched_gem_name: str = Field(default="", description="匹配的Gem模板名称")
    match_score: float = Field(default=0.0, description="匹配分数（0-1）")
    generated_prompt: str = Field(default="", description="生成的视频提示词")
    video_provider: str = Field(default="", description="实际使用的视频提供商")
    cost_usd: float = Field(default=0.0, description="花费（美元）")
    generation_time_sec: float = Field(default=0.0, description="生成耗时（秒）")
    error_message: Optional[str] = Field(default=None, description="错误信息（失败时）")


class BatchVideoResult(BaseModel):
    """T02: 批量视频生成返回"""

    batch_id: str = Field(description="批次唯一标识")
    total_tasks: int = Field(description="总任务数")
    status: str = Field(description="批次状态: running/paused/completed/failed")
    message: str = Field(description="状态说明")


class BatchProgressResult(BaseModel):
    """T03: 批量任务进度返回"""

    batch_id: str = Field(description="批次ID")
    batch_name: str = Field(default="", description="批次名称")
    total: int = Field(description="总任务数")
    completed: int = Field(default=0, description="已完成数")
    failed: int = Field(default=0, description="失败数")
    pending: int = Field(default=0, description="待处理数")
    analyzing: int = Field(default=0, description="分析中数")
    routing: int = Field(default=0, description="路由中数")
    prompt_generating: int = Field(default=0, description="提示词生成中数")
    video_generating: int = Field(default=0, description="视频生成中数")
    progress_percent: float = Field(default=0.0, description="进度百分比（0-100）")
    eta_seconds: Optional[float] = Field(default=None, description="预计剩余时间（秒）")
    total_cost_usd: float = Field(default=0.0, description="累计花费（美元）")
    throughput: float = Field(default=0.0, description="吞吐量（任务/秒）")
    is_complete: bool = Field(default=False, description="是否已完成")


class ImageFeaturesResult(BaseModel):
    """T04: 图片分析返回"""

    features: dict[str, Any] = Field(description="标准化特征向量")
    confidence_score: float = Field(description="整体置信度（0-1）")
    completeness_score: float = Field(description="特征完整度（0-1）")
    reliability: str = Field(description="可靠性: reliable/moderate/unreliable")
    cache_hit: bool = Field(default=False, description="是否命中缓存")
    analysis_time_ms: int = Field(description="分析耗时（毫秒）")


class MatchItem(BaseModel):
    """T05: 单个匹配项"""

    gem_id: str = Field(description="Gem ID")
    gem_name: str = Field(description="Gem名称")
    score: float = Field(description="匹配分数（0-1）")
    breakdown: dict[str, float] = Field(default_factory=dict, description="维度得分明细")


class MatchGemResult(BaseModel):
    """T05: 智能路由匹配返回"""

    matches: list[MatchItem] = Field(description="匹配结果列表")
    confidence_level: str = Field(description="置信度: high/medium/low")
    reasoning: str = Field(default="", description="匹配推理说明")
    elapsed_ms: int = Field(default=0, description="匹配耗时（毫秒）")


class PromptResult(BaseModel):
    """T06: 提示词生成返回"""

    prompt: str = Field(description="生成的视频提示词")
    gem_used: str = Field(description="使用的Gem ID")
    model_used: str = Field(default="", description="使用的Gemini模型")
    is_valid: bool = Field(description="是否通过校验")
    validation_details: dict[str, Any] = Field(default_factory=dict, description="校验详情")
    generation_time_ms: int = Field(default=0, description="生成耗时（毫秒）")


class VideoRawResult(BaseModel):
    """T07: 原始视频生成返回"""

    task_id: str = Field(description="视频任务ID")
    provider: str = Field(description="实际使用的提供商")
    status: str = Field(description="状态: succeeded/failed")
    video_url: Optional[str] = Field(default=None, description="视频URL")
    video_local_path: Optional[str] = Field(default=None, description="本地路径")
    cost_usd: float = Field(default=0.0, description="花费（美元）")
    generation_time_sec: float = Field(default=0.0, description="生成耗时（秒）")


class GemListResult(BaseModel):
    """T08: Gem列表返回"""

    gems: list[dict[str, Any]] = Field(description="Gem列表")
    total: int = Field(description="总数")
    last_sync_at: Optional[str] = Field(default=None, description="最后同步时间")


class HealthCheckResult(BaseModel):
    """T09: 健康检查返回"""

    status: str = Field(description="整体状态: healthy/degraded/unhealthy")
    version: str = Field(default="4.0.0", description="Skill版本号")
    checks: dict[str, dict[str, Any]] = Field(default_factory=dict, description="各组件检查结果")
    latency_ms: int = Field(default=0, description="检查耗时（毫秒）")


class ConfigResult(BaseModel):
    """T10: 配置返回"""

    config: dict[str, Any] = Field(description="配置项字典（敏感值已脱敏）")
    source: str = Field(default="env", description="配置来源: env/file/default")
    last_reload_at: str = Field(default="", description="最后重载时间")


# ═══════════════════════════════════════════════════════════════
# 参数校验模型
# ═══════════════════════════════════════════════════════════════


class GenerateVideoSingleParams(BaseModel):
    """T01 参数校验"""

    image_path: str = Field(min_length=1, description="产品图片路径")
    user_description: str = Field(default="", max_length=2000)
    preferred_gem_id: Optional[str] = None
    preferred_provider: Optional[str] = None
    output_resolution: str = Field(default="1080p", pattern=r"^(540p|720p|1080p|4k)$")
    output_duration: int = Field(default=5, ge=4, le=16)
    generate_audio: bool = True

    @field_validator("image_path")
    @classmethod
    def validate_image_path(cls: type["GenerateVideoSingleParams"], v: str) -> str:
        if not v or len(v.strip()) == 0:
            raise ValueError("图片路径不能为空")
        return v


class GenerateVideoBatchParams(BaseModel):
    """T02 参数校验"""

    image_dir: str = Field(min_length=1)
    batch_name: str = Field(default="未命名批次", max_length=100)
    user_description: str = Field(default="", max_length=2000)
    max_concurrency: int = Field(default=3, ge=1, le=10)
    preferred_provider: Optional[str] = None
    output_resolution: str = Field(default="1080p", pattern=r"^(540p|720p|1080p|4k)$")
    output_duration: int = Field(default=5, ge=4, le=16)
    continue_on_error: bool = True
    upload_to_drive: bool = True


class GetBatchProgressParams(BaseModel):
    """T03 参数校验"""

    batch_id: str = Field(min_length=1, description="批次ID")


class AnalyzeImageParams(BaseModel):
    """T04 参数校验"""

    image_path: str = Field(min_length=1)
    dimensions: list[str] = Field(default=["product", "audience", "scene", "style"])
    platform_hint: Optional[str] = None

    @field_validator("dimensions")
    @classmethod
    def validate_dimensions(cls: type["AnalyzeImageParams"], v: list[str]) -> list[str]:
        valid = {"product", "audience", "scene", "style"}
        invalid = set(v) - valid
        if invalid:
            raise ValueError(f"无效的分析维度: {invalid}, 可选: {valid}")
        return v


class MatchGemTemplateParams(BaseModel):
    """T05 参数校验"""

    features: dict[str, Any] = Field(min_length=1, description="图片特征")
    top_n: int = Field(default=3, ge=1, le=10)
    min_score: float = Field(default=0.5, ge=0.0, le=1.0)
    platform_hint: Optional[str] = None


class GeneratePromptParams(BaseModel):
    """T06 参数校验"""

    image_path: str = Field(min_length=1)
    gem_id: str = Field(min_length=1)
    features: Optional[dict[str, Any]] = None
    user_description: str = Field(default="", max_length=2000)


class GenerateVideoRawParams(BaseModel):
    """T07 参数校验"""

    prompt: str = Field(min_length=1, max_length=5000)
    reference_image: Optional[str] = None
    provider: str = Field(default="seedance")
    model_version: Optional[str] = None
    resolution: str = Field(default="1080p", pattern=r"^(540p|720p|1080p|4k)$")
    duration: int = Field(default=5, ge=4, le=16)
    generate_audio: bool = True


class ListGemsParams(BaseModel):
    """T08 参数校验"""

    category: Optional[str] = None
    active_only: bool = True
    refresh: bool = False


class GetConfigParams(BaseModel):
    """T10 参数校验"""

    section: Optional[str] = None


# ═══════════════════════════════════════════════════════════════
# 错误响应模型
# ═══════════════════════════════════════════════════════════════


class MCPToolErrorResponse(BaseModel):
    """MCP 工具标准化错误响应"""

    error_code: str = Field(description="错误码，如 C002")
    error_category: str = Field(
        description="错误分类: client|server|network|task|resource|config|system"
    )
    message: str = Field(description="人类可读的错误消息")
    retryable: bool = Field(default=False, description="是否可重试")
    retry_after_sec: int = Field(default=0, description="建议重试等待时间（秒）")
    trace_id: str = Field(default="", description="追踪ID")
    details: dict[str, Any] = Field(default_factory=dict, description="扩展信息")
    remediation_hint: str = Field(default="", description="修复建议")

    def to_dict(self: "MCPToolErrorResponse") -> dict[str, Any]:
        """转换为字典（用于 JSON 序列化）"""
        return self.model_dump()


# ═══════════════════════════════════════════════════════════════
# 异常→错误码映射
# ═══════════════════════════════════════════════════════════════

ERROR_CODE_MAP: dict[type, tuple[str, str, bool, str]] = {
    # (error_code, category, retryable, remediation_hint)
    PydanticValidationError: ("C002", "client", False, "检查参数格式和必填项"),
    GeminiValidationError: ("C002", "client", False, "检查参数格式和必填项"),
    ValueError: ("C002", "client", False, "检查参数值是否合法"),
    TypeError: ("C001", "client", False, "检查参数类型"),
    AuthError: ("C101", "client", False, "检查 Cookie 是否过期，重新认证"),
    NotInitializedError: ("S002", "server", True, "等待服务初始化完成"),
    PermissionError: ("C102", "client", False, "检查权限配置"),
    FileNotFoundError: ("R001", "resource", False, "检查文件路径是否正确"),
    ConnectionError: ("N001", "network", True, "检查网络连接，稍后重试"),
    TimeoutError: ("N002", "network", True, "请求超时，稍后重试"),
    asyncio.TimeoutError: ("N002", "network", True, "请求超时，稍后重试"),
}


def exception_to_error_response(
    exc: Exception,
    trace_id: str = "",
) -> MCPToolErrorResponse:
    """
    将任意异常转换为标准化错误响应

    算法:
    1. 查找异常类型在 ERROR_CODE_MAP 中的映射
    2. 如未找到，检查父类匹配
    3. 如仍未找到，使用 E001（系统错误）作为兜底
    4. 构造 MCPToolErrorResponse
    """
    exc_type = type(exc)
    code, category, retryable, hint = "E001", "system", False, "联系开发团队"

    # 精确匹配
    if exc_type in ERROR_CODE_MAP:
        code, category, retryable, hint = ERROR_CODE_MAP[exc_type]
    else:
        # 检查父类匹配
        for exc_cls, mapping in ERROR_CODE_MAP.items():
            if issubclass(exc_type, exc_cls):
                code, category, retryable, hint = mapping
                break

    # 获取详细信息
    details: dict[str, Any] = {}
    if hasattr(exc, "details"):
        attr_details = exc.details
        if isinstance(attr_details, dict):
            details = attr_details
    elif hasattr(exc, "__dict__"):
        details = {
            k: str(v) for k, v in exc.__dict__.items() if not k.startswith("_")
        }

    return MCPToolErrorResponse(
        error_code=code,
        error_category=category,
        message=str(exc),
        retryable=retryable,
        retry_after_sec=30 if retryable else 0,
        trace_id=trace_id or request_ctx.get(""),
        details=details,
        remediation_hint=hint,
    )


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
    """脱敏参数（隐藏敏感信息）"""
    sensitive_keys = {"cookie", "token", "password", "secret", "api_key"}
    sanitized: dict[str, Any] = {}
    for k, v in args.items():
        if any(sk in k.lower() for sk in sensitive_keys):
            sanitized[k] = "***"
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_args(v)
        else:
            sanitized[k] = v
    return sanitized


# ═══════════════════════════════════════════════════════════════
# 装饰器
# ═══════════════════════════════════════════════════════════════


def traced(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """
    请求追踪装饰器

    为每个工具调用:
    1. 生成 trace_id
    2. 记录开始/结束日志
    3. 记录耗时
    4. 异常时记录堆栈
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        trace_id = f"req_{uuid.uuid4().hex[:12]}"
        token = request_ctx.set(trace_id)
        start_time = time.time()

        logger.info(
            f"[MCP][{trace_id}] Tool '{func.__name__}' started, "
            f"args={_sanitize_args(dict(kwargs))}"
        )

        try:
            result = await func(*args, **kwargs)
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(
                f"[MCP][{trace_id}] Tool '{func.__name__}' completed in {elapsed_ms}ms"
            )
            return result

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.error(
                f"[MCP][{trace_id}] Tool '{func.__name__}' failed after {elapsed_ms}ms: "
                f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            )
            raise

        finally:
            request_ctx.reset(token)

    return wrapper


def handle_errors(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """
    错误处理装饰器

    捕获所有异常，转换为标准化错误响应（JSON 字符串）。
    确保 MCP 工具永远不会抛出未处理异常。
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        trace_id = request_ctx.get("")
        try:
            return await func(*args, **kwargs)

        except GeminiModuleError as e:
            error = exception_to_error_response(e, trace_id)
            logger.warning(
                f"[MCP][{trace_id}] Known error: {error.error_code} - {error.message}"
            )
            return error.to_dict()  # type: ignore[return-value]

        except PydanticValidationError as e:
            error = MCPToolErrorResponse(
                error_code="C002",
                error_category="client",
                message=f"参数校验失败: {e}",
                retryable=False,
                trace_id=trace_id,
                details={"validation_errors": e.errors()},
                remediation_hint="检查参数格式和必填项",
            )
            logger.warning(f"[MCP][{trace_id}] Validation error: {e}")
            return error.to_dict()  # type: ignore[return-value]

        except Exception as e:
            error = exception_to_error_response(e, trace_id)
            logger.error(
                f"[MCP][{trace_id}] Unexpected error in '{func.__name__}': "
                f"{type(e).__name__}: {e}"
            )
            return error.to_dict()  # type: ignore[return-value]

    return wrapper


# ═══════════════════════════════════════════════════════════════
# Tools 类 — 10 个工具的业务逻辑实现
# ═══════════════════════════════════════════════════════════════


class Tools:
    """
    MCP 工具业务逻辑实现

    委托 SkillController 完成业务操作，负责参数转换和结果格式化。

    每个工具的实现流程:
    1. 参数校验（Pydantic 模型验证）
    2. 业务委托（调用 SkillController 或内部模块）
    3. 结果转换（内部结果 -> MCP 返回格式）
    4. 异常包装（业务异常 -> MCP 错误响应）
    """

    def __init__(
        self,
        controller: Any,
        config: GeminiConfig,
        session_store: dict[str, Any],
    ) -> None:
        self._controller = controller
        self._config = config
        self._session = session_store

    # ─── T01: generate_video_single ───

    async def generate_video_single(
        self,
        image_path: str,
        user_description: str = "",
        preferred_gem_id: Optional[str] = None,
        preferred_provider: Optional[str] = None,
        output_resolution: str = "1080p",
        output_duration: int = 5,
        generate_audio: bool = True,
    ) -> dict[str, Any]:
        """
        单产品视频生成（完整流程）

        流程:
        1. 参数校验
        2. 构建 ImageInput
        3. 调用 SkillController.process_single()
        4. 转换结果为 MCP 格式

        超时: 180秒
        """
        # Step 1: 参数校验
        params = GenerateVideoSingleParams(
            image_path=image_path,
            user_description=user_description,
            preferred_gem_id=preferred_gem_id,
            preferred_provider=preferred_provider,
            output_resolution=output_resolution,
            output_duration=output_duration,
            generate_audio=generate_audio,
        )

        # Step 2: 构建图片输入
        image_input = ImageInput.from_path(params.image_path)

        # Step 3: 调用业务层（带超时）
        trace_id = request_ctx.get("")
        logger.info(
            f"[{trace_id}] Starting single video generation for {params.image_path}"
        )

        try:
            task_record = await asyncio.wait_for(
                self._controller.process_single(
                    image_input=image_input,
                    user_description=params.user_description,
                    preferred_gem_id=params.preferred_gem_id,
                    preferred_provider=params.preferred_provider,
                    output_resolution=params.output_resolution,
                    output_duration=params.output_duration,
                    generate_audio=params.generate_audio,
                ),
                timeout=180,
            )
        except asyncio.TimeoutError:
            raise TimeoutError("单产品视频生成超时（180秒）")

        # Step 4: 转换结果
        result = SingleVideoResult(
            task_id=getattr(task_record, "task_id", ""),
            status=_get_status_value(task_record.status),
            video_url=getattr(task_record, "video_url", None),
            video_local_path=getattr(task_record, "video_local_path", None),
            matched_gem_id=getattr(task_record, "matched_gem_id", None) or "",
            matched_gem_name=getattr(task_record, "matched_gem_name", None) or "",
            match_score=getattr(task_record, "match_score", None) or 0.0,
            generated_prompt=getattr(task_record, "generated_prompt", None) or "",
            video_provider=getattr(task_record, "video_provider", None) or "",
            cost_usd=getattr(task_record, "cost_usd", 0.0),
            generation_time_sec=getattr(task_record, "generation_time_sec", 0.0),
            error_message=getattr(task_record, "error_message", None),
        )

        logger.info(
            f"[{trace_id}] Single video generation completed: task_id={result.task_id}"
        )
        return result.model_dump()

    # ─── T02: generate_video_batch ───

    async def generate_video_batch(
        self,
        image_dir: str,
        batch_name: str = "未命名批次",
        user_description: str = "",
        max_concurrency: int = 3,
        preferred_provider: Optional[str] = None,
        output_resolution: str = "1080p",
        output_duration: int = 5,
        continue_on_error: bool = True,
        upload_to_drive: bool = True,
    ) -> dict[str, Any]:
        """
        批量视频生成

        流程:
        1. 参数校验
        2. 扫描目录获取图片列表
        3. 调用 SkillController.process_batch()
        4. 返回批次信息（后台异步执行）

        注意: 批量任务为异步执行，此工具只返回批次ID和状态，
              实际进度通过 get_batch_progress 查询。
        """
        # Step 1: 参数校验
        params = GenerateVideoBatchParams(
            image_dir=image_dir,
            batch_name=batch_name,
            user_description=user_description,
            max_concurrency=max_concurrency,
            preferred_provider=preferred_provider,
            output_resolution=output_resolution,
            output_duration=output_duration,
            continue_on_error=continue_on_error,
            upload_to_drive=upload_to_drive,
        )

        # Step 2: 校验目录
        dir_path = Path(params.image_dir)
        if not dir_path.exists():
            raise FileNotFoundError(f"目录不存在: {params.image_dir}")
        if not dir_path.is_dir():
            raise ValueError(f"路径不是目录: {params.image_dir}")

        # Step 3: 扫描图片
        valid_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
        image_files = [
            str(f)
            for f in dir_path.iterdir()
            if f.is_file() and f.suffix.lower() in valid_extensions
        ]

        if not image_files:
            raise ValueError(f"目录中未找到有效图片文件: {params.image_dir}")

        # Step 4: 调用业务层（异步启动）
        trace_id = request_ctx.get("")
        logger.info(
            f"[{trace_id}] Starting batch: {len(image_files)} images, "
            f"name='{params.batch_name}', concurrency={params.max_concurrency}"
        )

        batch_summary = await self._controller.process_batch(
            image_files=image_files,
            batch_name=params.batch_name,
            user_description=params.user_description,
            max_concurrency=params.max_concurrency,
            preferred_provider=params.preferred_provider,
            output_resolution=params.output_resolution,
            output_duration=params.output_duration,
            continue_on_error=params.continue_on_error,
            upload_to_drive=params.upload_to_drive,
        )

        # Step 5: 存储批次会话信息
        batch_id = getattr(batch_summary, "batch_id", "unknown")
        self._session[f"batch_{batch_id}"] = {
            "batch_name": params.batch_name,
            "total_tasks": len(image_files),
            "start_time": datetime.now().isoformat(),
        }

        result = BatchVideoResult(
            batch_id=batch_id,
            total_tasks=len(image_files),
            status=getattr(batch_summary, "status", "running"),
            message=f"批量任务 '{params.batch_name}' 已启动，共 {len(image_files)} 个任务，"
            f"并发数 {params.max_concurrency}",
        )

        logger.info(f"[{trace_id}] Batch started: batch_id={result.batch_id}")
        return result.model_dump()

    # ─── T03: get_batch_progress ───

    async def get_batch_progress(
        self,
        batch_id: str,
    ) -> dict[str, Any]:
        """
        查询批量任务进度

        流程:
        1. 参数校验
        2. 调用 SkillController.get_progress()
        3. 转换进度信息

        超时: 10秒
        """
        # Step 1: 参数校验
        params = GetBatchProgressParams(batch_id=batch_id)

        # Step 2: 查询进度（带超时）
        trace_id = request_ctx.get("")
        logger.debug(f"[{trace_id}] Querying progress for batch: {params.batch_id}")

        try:
            progress = await asyncio.wait_for(
                self._controller.get_progress(batch_id=params.batch_id),
                timeout=10,
            )
        except asyncio.TimeoutError:
            raise TimeoutError("查询进度超时（10秒）")

        # Step 3: 转换结果
        batch_info = self._session.get(f"batch_{params.batch_id}", {})
        by_status = getattr(progress, "by_status", {}) or {}

        result = BatchProgressResult(
            batch_id=params.batch_id,
            batch_name=batch_info.get("batch_name", ""),
            total=getattr(progress, "total", 0),
            completed=getattr(progress, "completed", 0),
            failed=getattr(progress, "failed", 0),
            pending=by_status.get("pending", 0),
            analyzing=by_status.get("analyzing", 0),
            routing=by_status.get("routing", 0),
            prompt_generating=by_status.get("prompt_generating", 0),
            video_generating=by_status.get("video_generating", 0),
            progress_percent=getattr(progress, "percent", 0.0),
            eta_seconds=getattr(progress, "eta_seconds", None),
            total_cost_usd=getattr(progress, "total_cost_usd", 0.0),
            throughput=getattr(progress, "throughput", 0.0) or 0.0,
            is_complete=getattr(progress, "is_complete", False),
        )

        return result.model_dump()

    # ─── T04: analyze_image ───

    async def analyze_image(
        self,
        image_path: str,
        dimensions: Optional[list[str]] = None,
        platform_hint: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        图片深度分析

        流程:
        1. 参数校验
        2. 构建 ImageInput
        3. 调用 SkillController.analyze_image()
        4. 转换分析结果

        超时: 30秒
        """
        # Step 1: 参数校验
        params = AnalyzeImageParams(
            image_path=image_path,
            dimensions=dimensions or ["product", "audience", "scene", "style"],
            platform_hint=platform_hint,
        )

        # Step 2: 构建图片输入
        image_input = ImageInput.from_path(params.image_path)

        # Step 3: 调用分析（带超时）
        trace_id = request_ctx.get("")
        logger.info(f"[{trace_id}] Analyzing image: {params.image_path}")

        start_time = time.time()
        try:
            features = await asyncio.wait_for(
                self._controller.analyze_image(
                    image_input=image_input,
                    dimensions=params.dimensions,
                    platform_hint=params.platform_hint,
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            raise TimeoutError("图片分析超时（30秒）")

        analysis_time_ms = int((time.time() - start_time) * 1000)

        # Step 4: 转换结果
        result = ImageFeaturesResult(
            features=features.model_dump() if hasattr(features, "model_dump") else dict(features) if features else {},
            confidence_score=getattr(features, "confidence_score", 0.8),
            completeness_score=getattr(features, "completeness_score", 0.8),
            reliability=getattr(features, "reliability", "reliable"),
            cache_hit=getattr(features, "cache_hit", False),
            analysis_time_ms=analysis_time_ms,
        )

        logger.info(f"[{trace_id}] Image analysis completed in {analysis_time_ms}ms")
        return result.model_dump()

    # ─── T05: match_gem_template ───

    async def match_gem_template(
        self,
        features: dict[str, Any],
        top_n: int = 3,
        min_score: float = 0.5,
        platform_hint: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        智能路由匹配

        流程:
        1. 参数校验
        2. 构建 ImageFeatures
        3. 调用 SkillController.match_gem()
        4. 转换匹配结果

        超时: 10秒
        """
        # Step 1: 参数校验
        params = MatchGemTemplateParams(
            features=features,
            top_n=top_n,
            min_score=min_score,
            platform_hint=platform_hint,
        )

        # Step 2: 构建特征对象（从 dict 反序列化）
        image_features = _build_image_features(params.features)

        # Step 3: 调用匹配（带超时）
        trace_id = request_ctx.get("")
        logger.info(f"[{trace_id}] Matching gem template: top_n={params.top_n}")

        start_time = time.time()
        try:
            match_result = await asyncio.wait_for(
                self._controller.match_gem(
                    features=image_features,
                    top_n=params.top_n,
                    min_score=params.min_score,
                    platform_hint=params.platform_hint,
                ),
                timeout=10,
            )
        except asyncio.TimeoutError:
            raise TimeoutError("智能路由匹配超时（10秒）")

        elapsed_ms = int((time.time() - start_time) * 1000)

        # Step 4: 转换结果
        matches = []
        for m in getattr(match_result, "matches", []) or []:
            matches.append(
                MatchItem(
                    gem_id=getattr(m, "gem_id", ""),
                    gem_name=getattr(m, "gem_name", ""),
                    score=getattr(m, "score", 0.0),
                    breakdown=getattr(m, "breakdown", {}),
                )
            )

        result = MatchGemResult(
            matches=matches,
            confidence_level=_get_status_value(getattr(match_result, "confidence_level", "medium")),
            reasoning=getattr(match_result, "reasoning", ""),
            elapsed_ms=elapsed_ms,
        )

        logger.info(
            f"[{trace_id}] Match completed: {len(matches)} matches, "
            f"confidence={result.confidence_level}"
        )
        return result.model_dump()

    # ─── T06: generate_prompt ───

    async def generate_prompt(
        self,
        image_path: str,
        gem_id: str,
        features: Optional[dict[str, Any]] = None,
        user_description: str = "",
    ) -> dict[str, Any]:
        """
        提示词生成

        流程:
        1. 参数校验
        2. 构建 ImageInput
        3. 如有 features，构建 ImageFeatures
        4. 调用 SkillController.generate_prompt()
        5. 转换结果

        超时: 60秒
        """
        # Step 1: 参数校验
        params = GeneratePromptParams(
            image_path=image_path,
            gem_id=gem_id,
            features=features,
            user_description=user_description,
        )

        # Step 2: 构建图片输入
        image_input = ImageInput.from_path(params.image_path)

        # Step 3: 构建特征（如提供）
        image_features = None
        if params.features:
            image_features = _build_image_features(params.features)

        # Step 4: 调用生成（带超时）
        trace_id = request_ctx.get("")
        logger.info(f"[{trace_id}] Generating prompt: gem={params.gem_id}")

        start_time = time.time()
        try:
            prompt_result = await asyncio.wait_for(
                self._controller.generate_prompt(
                    image_input=image_input,
                    gem_id=params.gem_id,
                    features=image_features,
                    user_description=params.user_description,
                ),
                timeout=60,
            )
        except asyncio.TimeoutError:
            raise TimeoutError("提示词生成超时（60秒）")

        generation_time_ms = int((time.time() - start_time) * 1000)

        # Step 5: 转换结果
        validation_details = {}
        if hasattr(prompt_result, "validation"):
            validation_obj = prompt_result.validation
            if hasattr(validation_obj, "model_dump"):
                validation_details = validation_obj.model_dump()
            elif isinstance(validation_obj, dict):
                validation_details = validation_obj

        result = PromptResult(
            prompt=getattr(prompt_result, "prompt", ""),
            gem_used=getattr(prompt_result, "gem_used", ""),
            model_used=getattr(prompt_result, "model_used", ""),
            is_valid=getattr(prompt_result, "is_valid", False),
            validation_details=validation_details,
            generation_time_ms=generation_time_ms,
        )

        logger.info(
            f"[{trace_id}] Prompt generated in {generation_time_ms}ms, "
            f"valid={result.is_valid}"
        )
        return result.model_dump()

    # ─── T07: generate_video_raw ───

    async def generate_video_raw(
        self,
        prompt: str,
        reference_image: Optional[str] = None,
        provider: str = "seedance",
        model_version: Optional[str] = None,
        resolution: str = "1080p",
        duration: int = 5,
        generate_audio: bool = True,
    ) -> dict[str, Any]:
        """
        原始视频生成

        流程:
        1. 参数校验
        2. 如有 reference_image，构建 ImageInput
        3. 调用 SkillController.generate_video_raw()
        4. 转换结果

        超时: 600秒
        """
        # Step 1: 参数校验
        params = GenerateVideoRawParams(
            prompt=prompt,
            reference_image=reference_image,
            provider=provider,
            model_version=model_version,
            resolution=resolution,
            duration=duration,
            generate_audio=generate_audio,
        )

        # Step 2: 构建参考图片输入（如提供）
        ref_image_input = None
        if params.reference_image:
            ref_image_input = ImageInput.from_path(params.reference_image)

        # Step 3: 调用生成（带超时）
        trace_id = request_ctx.get("")
        logger.info(
            f"[{trace_id}] Generating raw video: provider={params.provider}, "
            f"resolution={params.resolution}"
        )

        start_time = time.time()
        try:
            video_result = await asyncio.wait_for(
                self._controller.generate_video_raw(
                    prompt=params.prompt,
                    reference_image=ref_image_input,
                    provider=params.provider,
                    model_version=params.model_version,
                    resolution=params.resolution,
                    duration=params.duration,
                    generate_audio=params.generate_audio,
                ),
                timeout=600,
            )
        except asyncio.TimeoutError:
            raise TimeoutError("视频生成超时（600秒）")

        generation_time_sec = time.time() - start_time

        # Step 4: 转换结果
        result = VideoRawResult(
            task_id=getattr(video_result, "task_id", ""),
            provider=_get_status_value(getattr(video_result, "provider", provider)),
            status=getattr(video_result, "status", "failed"),
            video_url=getattr(video_result, "video_url", None),
            video_local_path=getattr(video_result, "video_local_path", None),
            cost_usd=getattr(video_result, "cost_usd", 0.0),
            generation_time_sec=generation_time_sec,
        )

        logger.info(
            f"[{trace_id}] Raw video generated: task_id={result.task_id}, "
            f"status={result.status}"
        )
        return result.model_dump()

    # ─── T08: list_gems ───

    async def list_gems(
        self,
        category: Optional[str] = None,
        active_only: bool = True,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """
        列出Gem模板

        流程:
        1. 参数校验
        2. 如 refresh=True，先刷新缓存
        3. 调用 SkillController.list_gems()
        4. 过滤和转换结果

        超时: 10秒
        """
        # Step 1: 参数校验
        params = ListGemsParams(
            category=category,
            active_only=active_only,
            refresh=refresh,
        )

        # Step 2: 刷新缓存（如请求）
        if params.refresh:
            trace_id = request_ctx.get("")
            logger.info(f"[{trace_id}] Refreshing gem cache")
            await self._controller.refresh_gem_cache()

        # Step 3: 获取列表（带超时）
        trace_id = request_ctx.get("")
        try:
            gems = await asyncio.wait_for(
                self._controller.list_gems(
                    category=params.category,
                    active_only=params.active_only,
                ),
                timeout=10,
            )
        except asyncio.TimeoutError:
            raise TimeoutError("获取Gem列表超时（10秒）")

        # Step 4: 转换结果
        gem_dicts = []
        for g in gems or []:
            gem_dicts.append(
                {
                    "id": _get_mock_value(g, "gem_id", _get_mock_value(g, "id", "")),
                    "name": _get_mock_value(g, "name", ""),
                    "description": _get_mock_value(g, "description", ""),
                    "category": _get_mock_value(g, "category", ""),
                    "tags": _get_mock_list(g, "tags", []),
                    "is_active": _get_mock_value(g, "is_active", True),
                    "usage_count": _get_mock_value(g, "usage_count", 0),
                    "success_rate": _get_mock_value(g, "success_rate", 1.0),
                }
            )

        # 获取最后同步时间
        last_sync: Optional[datetime] = None
        if hasattr(self._controller, "get_last_sync_time"):
            last_sync = self._controller.get_last_sync_time()

        result = GemListResult(
            gems=gem_dicts,
            total=len(gem_dicts),
            last_sync_at=last_sync.isoformat() if last_sync else None,
        )

        logger.info(f"[{trace_id}] Listed {result.total} gems")
        return result.model_dump()

    # ─── T09: health_check ───

    async def health_check(self: "Tools") -> dict[str, Any]:
        """
        系统健康检查

        流程:
        1. 并发检查所有组件
        2. 汇总健康状态
        3. 返回整体状态

        超时: 10秒
        """
        trace_id = request_ctx.get("")
        logger.debug(f"[{trace_id}] Running health check")

        start_time = time.time()
        checks: dict[str, dict[str, Any]] = {}

        # 检查 Gemini 服务
        try:
            gemini_status = await asyncio.wait_for(
                self._controller.check_gemini_health(),
                timeout=5,
            )
            checks["gemini_service"] = {
                "status": _get_status_value(getattr(gemini_status, "status", "unknown")),
                "latency_ms": getattr(gemini_status, "latency_ms", 0),
                "message": getattr(gemini_status, "message", ""),
            }
        except Exception as e:
            checks["gemini_service"] = {
                "status": "unhealthy",
                "latency_ms": 0,
                "message": f"检查失败: {e}",
            }

        # 检查视频提供商
        try:
            video_status = await asyncio.wait_for(
                self._controller.check_video_health(),
                timeout=5,
            )
            if isinstance(video_status, dict):
                for provider, status in video_status.items():
                    checks[f"video_{provider}"] = status
            else:
                checks["video_providers"] = {
                    "status": "unknown",
                    "latency_ms": 0,
                    "message": "返回格式异常",
                }
        except Exception as e:
            checks["video_providers"] = {
                "status": "unhealthy",
                "latency_ms": 0,
                "message": f"检查失败: {e}",
            }

        # 检查 Workspace
        try:
            workspace_status = await asyncio.wait_for(
                self._controller.check_workspace_health(),
                timeout=5,
            )
            checks["workspace"] = {
                "status": _get_status_value(getattr(workspace_status, "status", "unknown")),
                "latency_ms": getattr(workspace_status, "latency_ms", 0),
                "message": getattr(workspace_status, "message", ""),
            }
        except Exception as e:
            checks["workspace"] = {
                "status": "unhealthy",
                "latency_ms": 0,
                "message": f"检查失败: {e}",
            }

        # 计算整体状态
        latency_ms = int((time.time() - start_time) * 1000)

        statuses = [c.get("status", "unknown") for c in checks.values()]
        if all(s == "healthy" for s in statuses):
            overall_status = "healthy"
        elif any(s == "healthy" for s in statuses):
            overall_status = "degraded"
        else:
            overall_status = "unhealthy"

        result = HealthCheckResult(
            status=overall_status,
            version="4.0.0",
            checks=checks,
            latency_ms=latency_ms,
        )

        logger.info(f"[{trace_id}] Health check: {overall_status}, {latency_ms}ms")
        return result.model_dump()

    # ─── T10: get_config ───

    async def get_config(
        self,
        section: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        获取配置（脱敏）

        流程:
        1. 参数校验
        2. 获取配置
        3. 脱敏敏感字段
        4. 返回配置字典

        超时: 5秒
        """
        # Step 1: 参数校验
        params = GetConfigParams(section=section)

        # Step 2: 获取配置
        config_dict = self._config.model_dump()

        # Step 3: 脱敏
        masked_config = _mask_sensitive(config_dict)

        # 如指定 section，只返回该部分
        if params.section and params.section in masked_config:
            masked_config = {params.section: masked_config[params.section]}

        result = ConfigResult(
            config=masked_config,
            source="env",
            last_reload_at=datetime.now().isoformat(),
        )

        return result.model_dump()


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _get_status_value(status: Any) -> str:
    """从状态对象中提取字符串值"""
    if status is None:
        return "unknown"
    if hasattr(status, "value"):
        return str(status.value)
    return str(status)


def _get_mock_value(obj: Any, attr: str, default: Any) -> Any:
    """从对象获取属性值，处理 MagicMock 自动创建属性的问题"""
    from unittest.mock import MagicMock

    val = getattr(obj, attr, default)
    if isinstance(val, MagicMock):
        # 检查 return_value 是否为非 MagicMock 的具体值
        # （显式设置的属性有 return_value，自动创建的无）
        rv = val.return_value
        if isinstance(rv, MagicMock):
            # 自动创建的子 mock，返回 default
            return default
        # return_value 被显式设置，返回该值
        return rv
    return val


def _get_mock_list(obj: Any, attr: str, default: list) -> list:
    """从对象获取列表属性，处理 MagicMock 问题"""
    from unittest.mock import MagicMock

    val = getattr(obj, attr, default)
    if isinstance(val, MagicMock):
        rv = val.return_value
        if isinstance(rv, MagicMock):
            return default
        return list(rv) if rv is not None else default
    return list(val) if val is not None else default


def _build_image_features(features_dict: dict[str, Any]) -> Any:
    """从字典构建 ImageFeatures 对象"""
    try:
        from module_gemini.interface_spec.models import ImageFeatures

        return ImageFeatures.model_validate(features_dict)
    except ImportError:
        # 返回字典作为降级方案
        return features_dict


def _mask_sensitive(d: dict[str, Any]) -> dict[str, Any]:
    """递归脱敏字典中的敏感字段"""
    sensitive_keys = {
        "secure_1psid",
        "secure_1psidts",
        "cookie",
        "token",
        "password",
        "secret",
        "api_key",
    }
    result: dict[str, Any] = {}
    for k, v in d.items():
        if any(sk in k.lower() for sk in sensitive_keys):
            result[k] = "***" if v else ""
        elif isinstance(v, dict):
            result[k] = _mask_sensitive(v)
        else:
            result[k] = v
    return result


# ═══════════════════════════════════════════════════════════════
# GeminiMCPServer 主类
# ═══════════════════════════════════════════════════════════════


class GeminiMCPServer:
    """
    Gemini MCP Server

    基于 FastMCP 框架，对外暴露 10 个标准化工具。
    提供统一错误处理、请求追踪、上下文管理。

    用法:
        server = GeminiMCPServer(skill_controller)
        await server.initialize()
        server.run()  # 阻塞运行

    属性:
        _mcp: FastMCP 实例
        _controller: SkillController 业务门面
        _config: GeminiConfig 配置
        _initialized: 是否已初始化
        _session_store: 会话存储
        _tool_registry: 工具注册表
    """

    def __init__(
        self,
        skill_controller: Any,
        config: Optional[GeminiConfig] = None,
        server_name: str = "gemini-module",
    ) -> None:
        """
        初始化 MCP Server

        Args:
            skill_controller: SkillController 业务门面实例
            config: Gemini 配置（默认从环境变量加载）
            server_name: MCP Server 名称
        """
        self._controller = skill_controller
        self._config = config or GeminiConfig.from_env()
        self._server_name = server_name
        self._initialized = False

        # FastMCP 实例
        self._mcp = FastMCP(server_name)

        # 会话存储（内存实现，生产环境可替换为 Redis）
        self._session_store: dict[str, dict[str, Any]] = {}

        # 工具注册表 {tool_name: tool_metadata}
        self._tool_registry: dict[str, dict[str, Any]] = {}

        # 统计信息
        self._stats: dict[str, Any] = {
            "total_requests": 0,
            "error_requests": 0,
            "tool_calls": {},
        }

        logger.info(f"GeminiMCPServer '{server_name}' created")

    # ─── 属性访问 ───

    @property
    def is_initialized(self: "GeminiMCPServer") -> bool:
        """是否已完成初始化"""
        return self._initialized

    @property
    def tool_names(self: "GeminiMCPServer") -> list[str]:
        """已注册的工具名称列表"""
        return list(self._tool_registry.keys())

    @property
    def stats(self: "GeminiMCPServer") -> dict[str, Any]:
        """获取统计信息"""
        return self._stats.copy()

    # ─── 生命周期方法 ───

    async def initialize(self: "GeminiMCPServer") -> None:
        """
        初始化 Server

        步骤:
        1. 注册所有工具
        2. 标记初始化完成
        """
        logger.info("Initializing GeminiMCPServer...")

        if not HAS_FASTMCP:
            logger.warning(
                "FastMCP not installed. Tools will not be registered with MCP framework."
            )

        self._register_tools()

        self._initialized = True
        logger.info(
            f"GeminiMCPServer initialized with {len(self._tool_registry)} tools"
        )

    def run(self: "GeminiMCPServer", transport: str = "stdio") -> None:
        """
        启动 MCP Server（阻塞运行）

        Args:
            transport: 传输方式 (stdio|sse)
        """
        if not self._initialized:
            raise RuntimeError("Server not initialized. Call initialize() first.")

        logger.info(f"Starting GeminiMCPServer with transport={transport}")
        self._mcp.run(transport=transport)

    async def close(self: "GeminiMCPServer") -> None:
        """关闭 Server，释放资源"""
        logger.info("Shutting down GeminiMCPServer...")
        self._session_store.clear()
        self._initialized = False
        logger.info("GeminiMCPServer shutdown complete")

    # ─── 工具注册 ───

    def _register_tools(self: "GeminiMCPServer") -> None:
        """
        注册所有 10 个 MCP 工具

        每个工具通过 @self._mcp.tool() 装饰器注册到 FastMCP。
        工具实现定义在 Tools 类中，通过委托方式调用。
        """
        tools = Tools(self._controller, self._config, self._session_store)

        # T01: generate_video_single - 单产品视频生成
        @self._mcp.tool()
        @traced
        @handle_errors
        async def generate_video_single(
            image_path: str,
            user_description: str = "",
            preferred_gem_id: Optional[str] = None,
            preferred_provider: Optional[str] = None,
            output_resolution: str = "1080p",
            output_duration: int = 5,
            generate_audio: bool = True,
        ) -> dict[str, Any]:
            """为单个产品图片生成带货视频（完整流程：分析→路由→提示词→视频）"""
            self._stats["total_requests"] += 1
            self._stats["tool_calls"]["generate_video_single"] = (
                self._stats["tool_calls"].get("generate_video_single", 0) + 1
            )
            return await tools.generate_video_single(
                image_path=image_path,
                user_description=user_description,
                preferred_gem_id=preferred_gem_id,
                preferred_provider=preferred_provider,
                output_resolution=output_resolution,
                output_duration=output_duration,
                generate_audio=generate_audio,
            )

        self._tool_registry["generate_video_single"] = {
            "description": "单产品视频生成",
            "timeout": 180,
        }

        # T02: generate_video_batch - 批量视频生成
        @self._mcp.tool()
        @traced
        @handle_errors
        async def generate_video_batch(
            image_dir: str,
            batch_name: str = "未命名批次",
            user_description: str = "",
            max_concurrency: int = 3,
            preferred_provider: Optional[str] = None,
            output_resolution: str = "1080p",
            output_duration: int = 5,
            continue_on_error: bool = True,
            upload_to_drive: bool = True,
        ) -> dict[str, Any]:
            """为多个产品图片批量生成带货视频"""
            self._stats["total_requests"] += 1
            self._stats["tool_calls"]["generate_video_batch"] = (
                self._stats["tool_calls"].get("generate_video_batch", 0) + 1
            )
            return await tools.generate_video_batch(
                image_dir=image_dir,
                batch_name=batch_name,
                user_description=user_description,
                max_concurrency=max_concurrency,
                preferred_provider=preferred_provider,
                output_resolution=output_resolution,
                output_duration=output_duration,
                continue_on_error=continue_on_error,
                upload_to_drive=upload_to_drive,
            )

        self._tool_registry["generate_video_batch"] = {
            "description": "批量视频生成",
            "timeout": 0,
        }

        # T03: get_batch_progress - 查询批量任务进度
        @self._mcp.tool()
        @traced
        @handle_errors
        async def get_batch_progress(
            batch_id: str,
        ) -> dict[str, Any]:
            """查询指定批次的处理进度"""
            self._stats["total_requests"] += 1
            self._stats["tool_calls"]["get_batch_progress"] = (
                self._stats["tool_calls"].get("get_batch_progress", 0) + 1
            )
            return await tools.get_batch_progress(batch_id=batch_id)

        self._tool_registry["get_batch_progress"] = {
            "description": "查询批量任务进度",
            "timeout": 10,
        }

        # T04: analyze_image - 图片深度分析
        @self._mcp.tool()
        @traced
        @handle_errors
        async def analyze_image(
            image_path: str,
            dimensions: Optional[list[str]] = None,
            platform_hint: Optional[str] = None,
        ) -> dict[str, Any]:
            """对产品图片进行4维度深度分析（产品/人群/场景/风格）"""
            self._stats["total_requests"] += 1
            self._stats["tool_calls"]["analyze_image"] = (
                self._stats["tool_calls"].get("analyze_image", 0) + 1
            )
            return await tools.analyze_image(
                image_path=image_path,
                dimensions=dimensions or ["product", "audience", "scene", "style"],
                platform_hint=platform_hint,
            )

        self._tool_registry["analyze_image"] = {
            "description": "图片深度分析",
            "timeout": 30,
        }

        # T05: match_gem_template - 智能路由匹配
        @self._mcp.tool()
        @traced
        @handle_errors
        async def match_gem_template(
            features: dict[str, Any],
            top_n: int = 3,
            min_score: float = 0.5,
            platform_hint: Optional[str] = None,
        ) -> dict[str, Any]:
            """根据图片分析结果匹配最佳Gem模板"""
            self._stats["total_requests"] += 1
            self._stats["tool_calls"]["match_gem_template"] = (
                self._stats["tool_calls"].get("match_gem_template", 0) + 1
            )
            return await tools.match_gem_template(
                features=features,
                top_n=top_n,
                min_score=min_score,
                platform_hint=platform_hint,
            )

        self._tool_registry["match_gem_template"] = {
            "description": "智能路由匹配",
            "timeout": 10,
        }

        # T06: generate_prompt - 提示词生成
        @self._mcp.tool()
        @traced
        @handle_errors
        async def generate_prompt(
            image_path: str,
            gem_id: str,
            features: Optional[dict[str, Any]] = None,
            user_description: str = "",
        ) -> dict[str, Any]:
            """调用Gem生成视频提示词"""
            self._stats["total_requests"] += 1
            self._stats["tool_calls"]["generate_prompt"] = (
                self._stats["tool_calls"].get("generate_prompt", 0) + 1
            )
            return await tools.generate_prompt(
                image_path=image_path,
                gem_id=gem_id,
                features=features,
                user_description=user_description,
            )

        self._tool_registry["generate_prompt"] = {
            "description": "提示词生成",
            "timeout": 60,
        }

        # T07: generate_video_raw - 原始视频生成
        @self._mcp.tool()
        @traced
        @handle_errors
        async def generate_video_raw(
            prompt: str,
            reference_image: Optional[str] = None,
            provider: str = "seedance",
            model_version: Optional[str] = None,
            resolution: str = "1080p",
            duration: int = 5,
            generate_audio: bool = True,
        ) -> dict[str, Any]:
            """直接调用视频生成API（不包含分析和提示词生成）"""
            self._stats["total_requests"] += 1
            self._stats["tool_calls"]["generate_video_raw"] = (
                self._stats["tool_calls"].get("generate_video_raw", 0) + 1
            )
            return await tools.generate_video_raw(
                prompt=prompt,
                reference_image=reference_image,
                provider=provider,
                model_version=model_version,
                resolution=resolution,
                duration=duration,
                generate_audio=generate_audio,
            )

        self._tool_registry["generate_video_raw"] = {
            "description": "原始视频生成",
            "timeout": 600,
        }

        # T08: list_gems - 列出Gem模板
        @self._mcp.tool()
        @traced
        @handle_errors
        async def list_gems(
            category: Optional[str] = None,
            active_only: bool = True,
            refresh: bool = False,
        ) -> dict[str, Any]:
            """列出所有可用的Gem模板"""
            self._stats["total_requests"] += 1
            self._stats["tool_calls"]["list_gems"] = (
                self._stats["tool_calls"].get("list_gems", 0) + 1
            )
            return await tools.list_gems(
                category=category,
                active_only=active_only,
                refresh=refresh,
            )

        self._tool_registry["list_gems"] = {
            "description": "列出Gem模板",
            "timeout": 10,
        }

        # T09: health_check - 系统健康检查
        @self._mcp.tool()
        @traced
        @handle_errors
        async def health_check() -> dict[str, Any]:
            """检查系统各组件健康状态"""
            self._stats["total_requests"] += 1
            self._stats["tool_calls"]["health_check"] = (
                self._stats["tool_calls"].get("health_check", 0) + 1
            )
            return await tools.health_check()

        self._tool_registry["health_check"] = {
            "description": "系统健康检查",
            "timeout": 10,
        }

        # T10: get_config - 获取配置
        @self._mcp.tool()
        @traced
        @handle_errors
        async def get_config(
            section: Optional[str] = None,
        ) -> dict[str, Any]:
            """获取当前系统配置（脱敏）"""
            self._stats["total_requests"] += 1
            self._stats["tool_calls"]["get_config"] = (
                self._stats["tool_calls"].get("get_config", 0) + 1
            )
            return await tools.get_config(section=section)

        self._tool_registry["get_config"] = {
            "description": "获取配置",
            "timeout": 5,
        }

    def get_tool_schemas(self: "GeminiMCPServer") -> list[dict[str, Any]]:
        """
        获取所有工具的 JSON Schema

        Returns:
            工具 Schema 列表，用于文档生成和客户端代码生成
        """
        schemas = []
        for name, meta in self._tool_registry.items():
            schemas.append(
                {
                    "name": name,
                    "description": meta["description"],
                    "timeout": meta["timeout"],
                }
            )
        return schemas


# ═══════════════════════════════════════════════════════════════
# 入口函数
# ═══════════════════════════════════════════════════════════════


def main() -> None:
    """MCP Server 入口函数"""
    import asyncio

    async def _main() -> None:
        # 创建 Mock Controller（实际使用时应传入真实 SkillController）
        controller = _create_mock_controller()

        server = GeminiMCPServer(
            skill_controller=controller,
            server_name="gemini-module",
        )
        await server.initialize()

        logger.info(f"Registered tools: {server.tool_names}")
        return server

    server = asyncio.run(_main())
    logger.info("Starting MCP Server...")
    server.run(transport="stdio")


def _create_mock_controller() -> Any:
    """创建 Mock Controller（用于独立测试）"""
    from unittest.mock import AsyncMock, MagicMock

    controller = MagicMock()
    controller.process_single = AsyncMock(return_value=MagicMock(
        task_id="mock_task_001",
        status="completed",
        video_url="https://example.com/video.mp4",
        video_local_path="/output/video.mp4",
        matched_gem_id="gem_001",
        matched_gem_name="高端产品展示",
        match_score=0.92,
        generated_prompt="A luxury product showcase video...",
        video_provider="seedance",
        cost_usd=0.023,
        generation_time_sec=67.3,
        error_message=None,
    ))
    controller.process_batch = AsyncMock(return_value=MagicMock(
        batch_id="mock_batch_001",
        status="running",
    ))
    controller.get_progress = AsyncMock(return_value=MagicMock(
        total=10,
        completed=5,
        failed=1,
        by_status={"pending": 2, "analyzing": 0, "routing": 0, "prompt_generating": 1, "video_generating": 1},
        percent=50.0,
        eta_seconds=120.0,
        throughput=0.5,
        is_complete=False,
        total_cost_usd=1.5,
    ))
    controller.analyze_image = AsyncMock(return_value=MagicMock(
        model_dump=lambda: {
            "category": "makeup",
            "product_type": "lipstick",
            "target_gender": "female",
            "target_age": "gen_z",
            "video_style": "fast_paced",
            "dominant_colors": ["#FF6B9D"],
        },
        confidence_score=0.85,
        completeness_score=0.80,
        reliability="reliable",
        cache_hit=False,
    ))
    controller.match_gem = AsyncMock(return_value=MagicMock(
        matches=[
            MagicMock(gem_id="gem_001", gem_name="美妆模板", score=0.92, breakdown={"category": 1.0}),
            MagicMock(gem_id="gem_002", gem_name="护肤模板", score=0.78, breakdown={"category": 0.8}),
        ],
        confidence_level=MagicMock(value="high"),
        reasoning="基于品类匹配度最高",
    ))
    controller.generate_prompt = AsyncMock(return_value=MagicMock(
        prompt="A young woman applying lipstick in a bright studio...",
        gem_used="gem_001",
        model_used="gemini-pro",
        is_valid=True,
        validation=MagicMock(model_dump=lambda: {"has_subject": True, "has_style": True}),
    ))
    controller.generate_video_raw = AsyncMock(return_value=MagicMock(
        task_id="video_task_001",
        provider=MagicMock(value="seedance"),
        status="succeeded",
        video_url="https://example.com/output.mp4",
        video_local_path="/output/video.mp4",
        cost_usd=0.05,
    ))
    controller.list_gems = AsyncMock(return_value=[
        MagicMock(gem_id="gem_001", name="高端产品展示", category="luxury", tags=["product"], is_active=True, description="生成高端产品视频", usage_count=10, success_rate=0.95),
        MagicMock(gem_id="gem_002", name="美妆种草", category="beauty", tags=["makeup"], is_active=True, description="生成美妆种草视频", usage_count=20, success_rate=0.90),
    ])
    controller.check_gemini_health = AsyncMock(return_value=MagicMock(
        status="healthy", latency_ms=120, message="Gemini响应正常",
    ))
    controller.check_video_health = AsyncMock(return_value={
        "seedance": {"status": "healthy", "latency_ms": 450, "message": "即梦API正常"},
    })
    controller.check_workspace_health = AsyncMock(return_value=MagicMock(
        status="healthy", latency_ms=230, message="Workspace连接正常",
    ))
    controller.refresh_gem_cache = AsyncMock(return_value=None)

    return controller


if __name__ == "__main__":
    main()
