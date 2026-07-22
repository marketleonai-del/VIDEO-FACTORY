"""
workspace_client.py — Workspace 集成统一客户端

提供对所有 Google Workspace 服务（Sheets / Drive / Gmail）的统一访问入口。
包含完整的异常体系、Pydantic 数据模型、gws CLI 封装、认证管理、
Manager 抽象基类和 WorkspaceClient 主类。

用法:
    >>> from workspace_client import WorkspaceClient
    >>> client = await WorkspaceClient.from_service_account("/path/to/sa.json")
    >>> data = await client.read_sheet("SPREADSHEET_ID", "Sheet1!A1:D10")
    >>> file_id = await client.upload_drive("./video.mp4", folder_id="FOLDER_ID")
    >>> await client.send_email("user@example.com", "Subject", "Body")

Python 3.11+ | Pydantic v2 | asyncio
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import shutil
import time
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 1. 异常体系
# ═══════════════════════════════════════════════════════════


class WorkspaceError(Exception):
    """Workspace 模块根异常

    所有 Workspace 相关错误的基类。携带结构化错误上下文，
    支持日志记录和监控上报。

    Attributes:
        message: 人类可读的错误描述
        operation: 发生错误的操作名称（如 "sheets.read"）
        context: 任意结构化上下文数据
    """

    def __init__(
        self,
        message: str,
        operation: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.operation = operation or "unknown"
        self.context = context or {}

    def __str__(self: "WorkspaceError") -> str:
        base = f"[{self.operation}] {self.message}"
        if self.context:
            ctx_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            base += f" | context: {{{ctx_str}}}"
        return base

    def to_dict(self: "WorkspaceError") -> Dict[str, Any]:
        """序列化为字典，便于 JSON 日志记录"""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "operation": self.operation,
            "context": self.context,
        }


# ── 认证相关异常 ──────────────────────────────────────────


class AuthError(WorkspaceError):
    """认证基础异常"""
    pass


class CredentialNotFoundError(AuthError):
    """凭证文件不存在或路径无效

    Context:
        credentials_path: 请求的凭证文件路径
    """

    def __init__(self: "CredentialNotFoundError", path: str, **kwargs: Any) -> None:
        super().__init__(
            message=f"凭证文件不存在: {path}",
            operation="auth.load_credentials",
            context={"credentials_path": path, **kwargs},
        )


class TokenExpiredError(AuthError):
    """访问令牌已过期且无法刷新"""

    def __init__(self: "TokenExpiredError", expiry: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__(
            message=f"访问令牌已过期" + (f" (过期时间: {expiry})" if expiry else ""),
            operation="auth.token_refresh",
            context={"expiry": expiry, **kwargs},
        )


class ScopeInsufficientError(AuthError):
    """当前授权 Scope 不足以执行操作"""

    def __init__(
        self,
        required: List[str],
        current: List[str],
        **kwargs: Any,
    ) -> None:
        missing = set(required) - set(current)
        super().__init__(
            message=f"权限不足，缺少 Scope: {missing}",
            operation="auth.scope_check",
            context={
                "required_scopes": required,
                "current_scopes": current,
                "missing_scopes": list(missing),
                **kwargs,
            },
        )


# ── 速率限制异常 ──────────────────────────────────────────


class RateLimitError(WorkspaceError):
    """API 速率限制被触发

    Context:
        retry_after: 建议等待秒数
        quota_name: 被触发的配额名称
    """

    def __init__(
        self,
        message: str = "API 速率限制已触发",
        retry_after: Optional[int] = None,
        quota_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message=message,
            operation="rate_limit.check",
            context={
                "retry_after": retry_after,
                "quota_name": quota_name,
                **kwargs,
            },
        )
        self.retry_after = retry_after


# ── API 错误异常 ──────────────────────────────────────────


class ApiError(WorkspaceError):
    """Google API 返回的错误

    Context:
        status_code: HTTP 状态码
        api_error_code: Google API 错误代码
        details: 错误详情列表
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        api_error_code: Optional[str] = None,
        details: Optional[List[Dict]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message=message,
            operation=kwargs.pop("operation", "api.call"),
            context={
                "status_code": status_code,
                "api_error_code": api_error_code,
                "details": details or [],
                **kwargs,
            },
        )
        self.status_code = status_code
        self.api_error_code = api_error_code


class SheetsApiError(ApiError):
    """Sheets API 特定错误"""
    pass


class DriveApiError(ApiError):
    """Drive API 特定错误"""
    pass


class GmailApiError(ApiError):
    """Gmail API 特定错误"""
    pass


# ── 参数校验异常 ──────────────────────────────────────────


class ValidationError(WorkspaceError):
    """参数校验失败"""

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        value: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message=message,
            operation="validation",
            context={"field": field, "value": value, **kwargs},
        )


# ── gws CLI 异常 ──────────────────────────────────────────


class GwsCliError(WorkspaceError):
    """gws CLI 本身错误（非 API 错误）"""
    pass


class GwsNotFoundError(GwsCliError):
    """gws 可执行文件未找到"""

    def __init__(self: "GwsNotFoundError", gws_path: str, **kwargs: Any) -> None:
        super().__init__(
            message=f"gws 可执行文件未找到: {gws_path}，请确认已安装 `npm install -g @googleworkspace/cli`",
            operation="gws.check_binary",
            context={"gws_path": gws_path, **kwargs},
        )


class GwsTimeoutError(GwsCliError):
    """gws 命令执行超时"""

    def __init__(self: "GwsTimeoutError", command: List[str], timeout: int, **kwargs: Any) -> None:
        super().__init__(
            message=f"gws 命令超时 ({timeout}s): {' '.join(command)}",
            operation="gws.execute",
            context={"command": command, "timeout": timeout, **kwargs},
        )


class GwsExitCodeError(GwsCliError):
    """gws 非零退出码

    gws 退出码语义：
        0 = 成功
        1 = API 错误（Google 返回 4xx/5xx）
        2 = 认证错误（凭证缺失、过期或无效）
        3 = 验证错误（参数错误、未知服务）
        4 = Discovery 错误（无法获取 API Schema）
        5 = 内部错误（意外失败）

    Context:
        exit_code: gws 进程退出码
        stderr: gws 标准错误输出
        stdout: gws 标准输出
        command: 完整命令列表
    """

    EXIT_CODE_MAP = {
        1: "API_ERROR",
        2: "AUTH_ERROR",
        3: "VALIDATION_ERROR",
        4: "DISCOVERY_ERROR",
        5: "INTERNAL_ERROR",
    }

    def __init__(
        self,
        exit_code: int,
        stderr: str,
        stdout: str,
        command: List[str],
        **kwargs: Any,
    ) -> None:
        code_meaning = self.EXIT_CODE_MAP.get(exit_code, "UNKNOWN")
        super().__init__(
            message=f"gws 退出码 {exit_code} ({code_meaning}): {stderr[:500]}",
            operation="gws.execute",
            context={
                "exit_code": exit_code,
                "exit_meaning": code_meaning,
                "stderr": stderr,
                "stdout": stdout,
                "command": command,
                **kwargs,
            },
        )
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout

    def is_retryable(self: "GwsExitCodeError", retryable_codes: Optional[List[int]] = None) -> bool:
        """判断此退出码是否可重试

        Args:
            retryable_codes: 可重试退出码列表，默认 [1, 2, 4, 5]
                1=API错误(可能是临时性5xx)
                2=认证错误(令牌可能刚过期)
                4=Discovery错误(网络问题)
                5=内部错误(临时性)
                3=验证错误(不重试，参数问题)
        """
        codes = retryable_codes or [1, 2, 4, 5]
        return self.exit_code in codes


# ── 批量操作异常 ──────────────────────────────────────────


class BulkOperationError(WorkspaceError):
    """批量操作中的单条错误

    此异常不阻断整体批量操作，而是记录到结果中。
    由调用方决定是否抛出。

    Context:
        item_index: 出错的批次内索引
        item: 出错的数据项
    """

    def __init__(
        self,
        message: str,
        item_index: int,
        item: Any,
        original_error: Optional[Exception] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message=message,
            operation="bulk.operation",
            context={
                "item_index": item_index,
                "item": item,
                "original_error": str(original_error) if original_error else None,
                **kwargs,
            },
        )
        self.item_index = item_index
        self.item = item
        self.original_error = original_error


# ═══════════════════════════════════════════════════════════
# 2. Pydantic 数据模型
# ═══════════════════════════════════════════════════════════


# ── 枚举 ──────────────────────────────────────────────────


class AuthMethod(str, Enum):
    """认证方式枚举"""
    SERVICE_ACCOUNT = "service_account"
    OAUTH = "oauth"
    ENV = "env"


class GemType(str, Enum):
    """Gem 模板类型枚举"""
    PROMOTION = "promotion"
    BRAND = "brand"
    UNBOXING = "unboxing"
    TUTORIAL = "tutorial"
    TESTIMONIAL = "testimonial"
    LIFESTYLE = "lifestyle"
    COMPARISON = "comparison"
    GENERIC = "generic"


class Gender(str, Enum):
    """性别倾向枚举"""
    UNISEX = "unisex"
    MALE = "male"
    FEMALE = "female"


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class TaskPriority(str, Enum):
    """任务优先级枚举"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class VideoProvider(str, Enum):
    """视频模型提供商枚举"""
    VEO_2 = "veo_2"
    RUNWAY = "runway"
    KLING = "kling"
    PIKA = "pika"


class VideoResolution(str, Enum):
    """视频分辨率枚举"""
    P360 = "360p"
    P480 = "480p"
    P720 = "720p"
    P1080 = "1080p"
    P1440 = "1440p"
    P4K = "4k"


class ProductCategory(str, Enum):
    """产品品类枚举"""
    FOOD = "食品"
    SNACK = "零食"
    BEVERAGE = "饮料"
    BEAUTY = "美妆护肤"
    SKINCARE = "护肤品"
    MOTHER_BABY = "母婴"
    BABY_PRODUCTS = "婴童用品"
    HOME_APPLIANCE = "家电数码"
    CLOTHING = "服装服饰"
    SPORTS = "运动户外"
    PET = "宠物用品"
    CLEANING = "家居清洁"
    OTHER = "其他"


# ── 配置模型 ──────────────────────────────────────────────


class RetryPolicy(BaseModel):
    """重试策略配置"""
    model_config = ConfigDict(frozen=True)

    max_retries: int = Field(default=5, ge=0, le=20)
    base_delay: float = Field(default=1.0, gt=0)
    max_delay: float = Field(default=60.0, gt=0)
    exponential_base: float = Field(default=2.0, gt=1.0)
    jitter: bool = Field(default=True)
    retryable_exit_codes: list[int] = Field(default=[1, 2, 4, 5])


class RateLimitConfig(BaseModel):
    """限流配置"""
    model_config = ConfigDict(frozen=True)

    sheets_rpm: int = Field(default=300, ge=1)
    drive_rps: float = Field(default=10.0, ge=1.0)
    gmail_rps: float = Field(default=10.0, ge=1.0)


class ClientConfig(BaseModel):
    """Workspace 客户端配置"""
    model_config = ConfigDict(frozen=False)

    auth_method: AuthMethod = Field(default=AuthMethod.SERVICE_ACCOUNT)
    credentials_file: Optional[str] = Field(default=None)
    project_id: Optional[str] = Field(default=None)
    gws_path: str = Field(default="gws")
    gws_timeout: int = Field(default=60, ge=1)
    sheets_batch_size: int = Field(default=500, ge=1)
    drive_concurrency: int = Field(default=5, ge=1)
    drive_chunk_size: int = Field(default=8192, ge=1024)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


# ── 结果模型 ──────────────────────────────────────────────


class AuthStatus(BaseModel):
    """认证状态"""
    model_config = ConfigDict(frozen=False)

    is_authenticated: bool = Field(default=False)
    auth_method: AuthMethod = Field(default=AuthMethod.SERVICE_ACCOUNT)
    token_expiry: Optional[datetime] = Field(default=None)
    scopes: list[str] = Field(default_factory=list)
    email: Optional[str] = Field(default=None)
    project_id: Optional[str] = Field(default=None)


class SheetsReadResult(BaseModel):
    """Sheets 读取结果"""
    model_config = ConfigDict(frozen=False)

    success: bool = Field(default=True)
    operation: str = Field(default="sheets.read")
    row_count: int = Field(default=0, ge=0)
    column_count: int = Field(default=0, ge=0)
    data: list[list[str]] = Field(default_factory=list)
    headers: list[str] = Field(default_factory=list)
    duration_ms: int = Field(default=0)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class SheetsWriteResult(BaseModel):
    """Sheets 写入结果"""
    model_config = ConfigDict(frozen=False)

    success: bool = Field(default=True)
    operation: str = Field(default="sheets.write")
    rows_written: int = Field(default=0, ge=0)
    range_written: Optional[str] = Field(default=None)
    duration_ms: int = Field(default=0)
    raw_response: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = Field(default=None)


class BulkSheetsResult(BaseModel):
    """批量 Sheets 操作结果"""
    model_config = ConfigDict(frozen=False)

    total_rows: int = Field(default=0, ge=0)
    rows_written: int = Field(default=0, ge=0)
    rows_failed: int = Field(default=0, ge=0)
    batches: int = Field(default=0, ge=0)
    total_duration_ms: int = Field(default=0)
    errors: list[str] = Field(default_factory=list)


class UploadResult(BaseModel):
    """Drive 文件上传结果"""
    model_config = ConfigDict(frozen=False)

    success: bool = Field(default=True)
    operation: str = Field(default="drive.upload")
    file_id: Optional[str] = Field(default=None)
    file_name: str = Field(default="")
    mime_type: str = Field(default="application/octet-stream")
    file_size: int = Field(default=0)
    web_view_link: Optional[str] = Field(default=None)
    upload_duration_sec: float = Field(default=0.0)
    md5_hash: str = Field(default="")
    raw_response: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = Field(default=None)


class BulkUploadResult(BaseModel):
    """批量上传结果"""
    model_config = ConfigDict(frozen=False)

    total_files: int = Field(default=0, ge=0)
    success_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    results: list[UploadResult] = Field(default_factory=list)
    total_duration_sec: float = Field(default=0.0)
    folder_id: Optional[str] = Field(default=None)

    @property
    def success_rate(self: "BulkUploadResult") -> float:
        if self.total_files == 0:
            return 0.0
        return self.success_count / self.total_files


class FolderInfo(BaseModel):
    """Drive 文件夹信息"""
    model_config = ConfigDict(frozen=False)

    folder_id: str = Field(default="")
    name: str = Field(default="")
    parent_id: Optional[str] = Field(default=None)
    created_time: Optional[str] = Field(default=None)
    web_view_link: Optional[str] = Field(default=None)


class SendResult(BaseModel):
    """邮件发送结果"""
    model_config = ConfigDict(frozen=False)

    success: bool = Field(default=True)
    operation: str = Field(default="gmail.send")
    message_id: Optional[str] = Field(default=None)
    thread_id: Optional[str] = Field(default=None)
    recipients: list[str] = Field(default_factory=list)
    duration_ms: int = Field(default=0)
    raw_response: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# 3. 基础设施
# ═══════════════════════════════════════════════════════════


class AsyncTokenBucket:
    """异步令牌桶限流器

    基于令牌桶算法实现平滑限流，支持突发请求。

    Args:
        rate: 每秒产生的令牌数
        burst: 桶容量（最大突发请求数）

    Example:
        >>> bucket = AsyncTokenBucket(rate=5, burst=10)
        >>> await bucket.acquire()
    """

    def __init__(self: "AsyncTokenBucket", rate: float, burst: int) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self: "AsyncTokenBucket", timeout: Optional[float] = None) -> bool:
        """获取一个令牌"""
        return await self.acquire_n(1, timeout)

    async def acquire_n(self: "AsyncTokenBucket", n: int, timeout: Optional[float] = None) -> bool:
        """获取 n 个令牌"""
        async with self._lock:
            start = time.monotonic()
            while True:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return True

                if timeout is not None:
                    elapsed = time.monotonic() - start
                    if elapsed >= timeout:
                        return False

                needed = n - self._tokens
                wait_time = needed / self._rate
                if timeout is not None:
                    remaining = timeout - (time.monotonic() - start)
                    wait_time = min(wait_time, remaining)

                await asyncio.sleep(wait_time)
                self._refill()

    def _refill(self: "AsyncTokenBucket") -> None:
        """根据经过时间补充令牌"""
        now = time.monotonic()
        elapsed = now - self._last_update
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_update = now

    @property
    def current_tokens(self: "AsyncTokenBucket") -> float:
        self._refill()
        return self._tokens


# ═══════════════════════════════════════════════════════════
# 4. GwsCommandRunner — gws CLI 子进程封装
# ═══════════════════════════════════════════════════════════


class GwsCommandRunner:
    """gws CLI 命令执行器

    封装所有 gws 子进程调用，提供统一的执行、超时控制、
    错误解析和重试机制。所有 Manager 均通过此类执行 gws 命令。

    Args:
        config: 客户端配置，包含 gws_path、timeout、credentials_file 等

    Example:
        >>> runner = GwsCommandRunner(config)
        >>> result = await runner.execute(
        ...     "sheets", "+read",
        ...     "--spreadsheet", "SPREADSHEET_ID",
        ...     "--range", "Sheet1!A1:B10"
        ... )
        >>> print(result["values"])
    """

    # gws 退出码到异常类的映射
    _EXIT_CODE_EXCEPTION_MAP = {
        1: ApiError,
        2: AuthError,
        3: ValidationError,
        4: GwsCliError,
        5: GwsCliError,
    }

    def __init__(self: "GwsCommandRunner", config: ClientConfig) -> None:
        self._config = config
        self._gws_path = config.gws_path
        self._timeout = config.gws_timeout
        self._retry_policy = config.retry_policy

        # 构建统一的环境变量
        self._env = os.environ.copy()
        if config.credentials_file:
            self._env["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] = config.credentials_file

    # ── 公共方法 ──────────────────────────────────────────────

    async def execute(self: "GwsCommandRunner", *args: str) -> Dict[str, Any]:
        """执行 gws 命令，返回解析后的 JSON 结果

        Args:
            *args: gws 命令参数（不含 gws 本身）

        Returns:
            解析后的 JSON 字典。若 gws 输出为空，返回 {}。

        Raises:
            GwsNotFoundError: gws 可执行文件不存在
            GwsTimeoutError: 命令执行超时
            GwsExitCodeError: gws 返回非零退出码
        """
        # 1. 检查 gws 二进制存在性（首次调用时）
        if not hasattr(self, "_binary_checked"):
            await self._ensure_binary()
            self._binary_checked = True

        # 2. 构建完整命令
        cmd = [self._gws_path, "--format", "json", *args]

        # 3. 执行子进程
        stdout_data, stderr_data, exit_code = await self._run_subprocess(cmd)

        # 4. 解析输出
        result = self._parse_stdout(stdout_data)

        # 5. 处理退出码
        if exit_code != 0:
            raise GwsExitCodeError(
                exit_code=exit_code,
                stderr=stderr_data,
                stdout=stdout_data,
                command=cmd,
                api_details=self._extract_error_details(result),
            )

        return result

    async def check_binary(self: "GwsCommandRunner") -> bool:
        """检查 gws 可执行文件是否可用"""
        path = shutil.which(self._gws_path)
        if path is None:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return proc.returncode == 0 and b"gws" in stdout.lower()
        except Exception:
            return False

    async def get_version(self: "GwsCommandRunner") -> str:
        """获取 gws 版本号"""
        proc = await asyncio.create_subprocess_exec(
            self._gws_path, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            raise GwsNotFoundError(self._gws_path)
        return stdout.decode().strip()

    @property
    def retry_policy(self: "GwsCommandRunner") -> RetryPolicy:
        """获取当前重试策略配置"""
        return self._retry_policy

    # ── 内部方法 ──────────────────────────────────────────────

    async def _ensure_binary(self: "GwsCommandRunner") -> None:
        """确保 gws 可执行文件存在"""
        if shutil.which(self._gws_path) is None:
            raise GwsNotFoundError(self._gws_path)

    async def _run_subprocess(
        self, cmd: List[str]
    ) -> tuple[str, str, int]:
        """执行子进程，返回 (stdout, stderr, exit_code)"""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise GwsTimeoutError(command=cmd, timeout=self._timeout)

        return (
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
            proc.returncode or 0,
        )

    def _parse_stdout(self: "GwsCommandRunner", stdout: str) -> Dict[str, Any]:
        """解析 gws 标准输出的 JSON

        gws 以 --format json 输出时，每行一个 JSON 对象（NDJSON）。
        此方法解析所有行，合并为列表或返回单行字典。
        """
        if not stdout.strip():
            return {}

        lines = [line.strip() for line in stdout.strip().split("\n") if line.strip()]
        if not lines:
            return {}

        parsed_lines: List[Dict[str, Any]] = []
        for line in lines:
            try:
                parsed_lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if len(parsed_lines) == 0:
            return {"_raw_text": stdout}
        if len(parsed_lines) == 1:
            result = parsed_lines[0]
            result["_raw"] = stdout
            return result

        return {"_results": parsed_lines, "_raw": stdout, "_count": len(parsed_lines)}

    def _extract_error_details(
        self, parsed_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """从 gws 输出中提取 API 错误详情"""
        if "error" in parsed_result:
            error = parsed_result["error"]
            if isinstance(error, dict):
                return {
                    "code": error.get("code"),
                    "message": error.get("message"),
                    "status": error.get("status"),
                    "details": error.get("details", []),
                }
        return {}


# ═══════════════════════════════════════════════════════════
# 5. AuthManager — 认证管理
# ═══════════════════════════════════════════════════════════


class AuthManager:
    """Service Account 认证管理器

    管理凭证文件的加载、格式验证和认证状态跟踪。
    实际的 JWT 签名和令牌获取由 gws CLI 内部处理。

    Args:
        config: 客户端配置

    Attributes:
        credentials_file: Service Account JSON 文件路径
        project_id: GCP 项目 ID
        client_email: Service Account 邮箱地址
    """

    REQUIRED_SCOPES: List[str] = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    def __init__(self: "AuthManager", config: ClientConfig) -> None:
        self._config = config
        self._credentials_file = config.credentials_file
        self._project_id: Optional[str] = None
        self._client_email: Optional[str] = None
        self._private_key_id: Optional[str] = None
        self._auth_method = config.auth_method
        self._token_expiry: Optional[datetime] = None
        self._is_validated = False
        self._credentials_data: Optional[Dict[str, Any]] = None

    async def verify(self: "AuthManager") -> AuthStatus:
        """验证认证配置是否有效

        检查凭证文件存在性、JSON 格式、必需字段。
        不实际获取令牌（由 gws 执行时处理）。

        Returns:
            AuthStatus: 当前认证状态

        Raises:
            CredentialNotFoundError: 凭证文件不存在
            AuthError: 凭证格式无效
        """
        # 1. 检查凭证文件路径
        if not self._credentials_file:
            env_path = os.environ.get("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE")
            if env_path:
                self._credentials_file = env_path
            else:
                return AuthStatus(
                    is_authenticated=False,
                    auth_method=self._auth_method,
                    scopes=self.REQUIRED_SCOPES,
                )

        path = Path(self._credentials_file)
        if not path.exists():
            raise CredentialNotFoundError(str(path))

        # 2. 解析 JSON
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise AuthError(
                message=f"凭证文件不是有效的 JSON: {e}",
                operation="auth.parse_credentials",
                context={"path": str(path)},
            )
        except IOError as e:
            raise CredentialNotFoundError(str(path), error=str(e))

        # 3. 验证必需字段
        if data.get("type") != "service_account":
            raise AuthError(
                message=f"不支持的凭证类型: {data.get('type')}，需要 service_account",
                operation="auth.validate_type",
                context={"type": data.get("type")},
            )

        required_fields = ["client_email", "private_key", "token_uri"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            raise AuthError(
                message=f"凭证文件缺少必需字段: {missing}",
                operation="auth.validate_fields",
                context={"missing_fields": missing},
            )

        # 4. 提取信息
        self._credentials_data = data
        self._project_id = data.get("project_id") or self._config.project_id
        self._client_email = data["client_email"]
        self._private_key_id = data.get("private_key_id")
        self._is_validated = True

        # Service Account 令牌有效期约 1 小时
        self._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

        return AuthStatus(
            is_authenticated=True,
            auth_method=AuthMethod.SERVICE_ACCOUNT,
            token_expiry=self._token_expiry,
            scopes=self.REQUIRED_SCOPES,
            email=self._client_email,
            project_id=self._project_id,
        )

    def refresh(self: "AuthManager") -> AuthStatus:
        """刷新认证状态"""
        self._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        return AuthStatus(
            is_authenticated=self._is_validated,
            auth_method=self._auth_method,
            token_expiry=self._token_expiry,
            scopes=self.REQUIRED_SCOPES,
            email=self._client_email,
            project_id=self._project_id,
        )

    def is_valid(self: "AuthManager") -> bool:
        """检查当前认证是否有效（未过期）"""
        if not self._is_validated:
            return False
        if self._token_expiry is None:
            return True
        return datetime.now(timezone.utc) < self._token_expiry

    def get_env(self: "AuthManager") -> Dict[str, str]:
        """获取认证相关的环境变量"""
        env: Dict[str, str] = {}
        if self._credentials_file:
            env["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] = self._credentials_file
        return env

    @property
    def client_email(self: "AuthManager") -> Optional[str]:
        return self._client_email

    @property
    def project_id(self: "AuthManager") -> Optional[str]:
        return self._project_id

    @property
    def credentials_file(self: "AuthManager") -> Optional[str]:
        return self._credentials_file

    @property
    def credentials_data(self: "AuthManager") -> Optional[Dict[str, Any]]:
        return self._credentials_data


# ═══════════════════════════════════════════════════════════
# 6. BaseManager — Manager 抽象基类
# ═══════════════════════════════════════════════════════════


class BaseManager:
    """Manager 抽象基类

    提供所有 Manager 共享的基础设施：
    - gws 命令执行（带信号量限流）
    - 指数退避重试
    - 响应解析和验证

    Args:
        config: 客户端配置
        runner: GwsCommandRunner 实例
    """

    def __init__(self: "BaseManager", config: ClientConfig, runner: GwsCommandRunner) -> None:
        self._config = config
        self._runner = runner
        self._logger = logging.getLogger(self.__class__.__name__)
        # 服务级并发控制（每个 Manager 独立）
        self._semaphore = asyncio.Semaphore(5)

    async def _run(self: "BaseManager", *args: str) -> Dict[str, Any]:
        """执行 gws 命令（带服务级限流）"""
        async with self._semaphore:
            return await self._runner.execute(*args)

    async def _run_with_retry(self: "BaseManager", *args: str) -> Dict[str, Any]:
        """执行 gws 命令（带指数退避重试）"""
        policy = self._runner.retry_policy
        last_exception: Optional[Exception] = None

        for attempt in range(1, policy.max_retries + 1):
            try:
                return await self._run(*args)
            except (GwsExitCodeError, GwsTimeoutError) as e:
                last_exception = e

                should_retry = False
                if isinstance(e, GwsExitCodeError):
                    should_retry = e.is_retryable(policy.retryable_exit_codes)
                elif isinstance(e, GwsTimeoutError):
                    should_retry = True

                if not should_retry or attempt >= policy.max_retries:
                    raise

                delay = self._compute_backoff_delay(attempt, policy)
                self._logger.warning(
                    f"gws 命令失败 (尝试 {attempt}/{policy.max_retries})，"
                    f"{delay:.1f}s 后重试: {e.message[:200]}"
                )
                await asyncio.sleep(delay)

            except Exception:
                raise

        raise last_exception or RuntimeError("重试逻辑异常")

    @staticmethod
    def _compute_backoff_delay(attempt: int, policy: RetryPolicy) -> float:
        """计算指数退避延迟"""
        exponential = policy.base_delay * (policy.exponential_base ** (attempt - 1))
        delay = min(exponential, policy.max_delay)

        if policy.jitter:
            delay *= 0.75 + random.random() * 0.5

        return delay

    @staticmethod
    def _parse_json_response(raw: str) -> Dict[str, Any]:
        """解析 JSON 字符串为字典"""
        if not raw or not raw.strip():
            return {}

        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        results = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if len(results) == 1:
            return results[0]
        elif len(results) > 1:
            return {"_results": results, "_count": len(results)}

        return {"_raw": raw}

    @staticmethod
    def _elapsed_ms(start_time: float) -> int:
        return int((asyncio.get_event_loop().time() - start_time) * 1000)


# ═══════════════════════════════════════════════════════════
# 7. WorkspaceClient — 统一入口类
# ═══════════════════════════════════════════════════════════


class WorkspaceClient:
    """Workspace 集成客户端（统一入口）

    提供对所有 Google Workspace 服务（Sheets/Drive/Gmail）的统一访问。
    通过 gws CLI 子进程调用底层 API，自动处理认证、限流和重试。

    Args:
        config: 客户端配置对象

    Attributes:
        auth: 认证管理器
        sheets: Sheets 管理器
        drive: Drive 管理器
        gmail: Gmail 管理器

    Example:
        >>> config = ClientConfig(
        ...     credentials_file="/path/to/service-account.json",
        ...     project_id="my-project",
        ... )
        >>> client = WorkspaceClient(config)
        >>> await client.health_check()
    """

    def __init__(self: "WorkspaceClient", config: ClientConfig) -> None:
        self._config = config
        self._runner = GwsCommandRunner(config)
        self._auth = AuthManager(config)

        # 延迟导入 managers 避免循环依赖
        from sheets_manager import SheetsManager
        from drive_manager import DriveManager
        from gmail_manager import GmailManager

        self._sheets = SheetsManager(config, self._runner)
        self._drive = DriveManager(config, self._runner)
        self._gmail = GmailManager(config, self._runner)

        self._logger = logging.getLogger("WorkspaceClient")
        self._initialized = False

    # ── 工厂方法 ──────────────────────────────────────────────

    @classmethod
    async def from_service_account(
        cls,
        credentials_path: str,
        project_id: Optional[str] = None,
        **kwargs: Any,
    ) -> "WorkspaceClient":
        """从 Service Account JSON 文件创建客户端

        Args:
            credentials_path: Service Account JSON 文件路径
            project_id: GCP 项目 ID（可选，从凭证文件自动提取）
            **kwargs: 额外的配置参数

        Returns:
            初始化好的 WorkspaceClient 实例

        Raises:
            CredentialNotFoundError: 凭证文件不存在
            AuthError: 凭证格式无效
        """
        config = ClientConfig(
            auth_method=AuthMethod.SERVICE_ACCOUNT,
            credentials_file=credentials_path,
            project_id=project_id,
            **kwargs,
        )
        client = cls(config)
        await client.initialize()
        return client

    @classmethod
    def from_env(cls: type["WorkspaceClient"], **kwargs: Any) -> "WorkspaceClient":
        """从环境变量创建客户端

        读取以下环境变量：
        - GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE: 凭证文件路径
        - GOOGLE_WORKSPACE_PROJECT_ID: GCP 项目 ID

        Args:
            **kwargs: 额外的配置参数（覆盖环境变量）

        Returns:
            WorkspaceClient 实例（未初始化，需手动调用 initialize）
        """
        config = ClientConfig(
            credentials_file=os.environ.get("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"),
            project_id=os.environ.get("GOOGLE_WORKSPACE_PROJECT_ID"),
            **kwargs,
        )
        return cls(config)

    @classmethod
    def from_config(cls: type["WorkspaceClient"], config: ClientConfig) -> "WorkspaceClient":
        """从配置对象创建客户端"""
        return cls(config)

    # ── 初始化 ────────────────────────────────────────────────

    async def initialize(self: "WorkspaceClient") -> AuthStatus:
        """初始化客户端

        验证认证配置、gws 二进制可用性，预热各管理器。

        Returns:
            认证状态

        Raises:
            CredentialNotFoundError: 凭证文件不存在
            GwsNotFoundError: gws 可执行文件不存在
        """
        if self._initialized:
            return await self._auth.verify()

        self._logger.info("初始化 WorkspaceClient...")

        # 1. 验证认证
        try:
            auth_status = await self._auth.verify()
            self._logger.info(
                f"认证成功: method={auth_status.auth_method}, "
                f"email={auth_status.email}, project={auth_status.project_id}"
            )
        except Exception:
            self._logger.error("认证验证失败")
            raise

        # 2. 验证 gws 二进制
        gws_ok = await self._runner.check_binary()
        if not gws_ok:
            raise GwsNotFoundError(self._config.gws_path)

        version = await self._runner.get_version()
        self._logger.info(f"gws 版本: {version}")

        self._initialized = True
        self._logger.info("WorkspaceClient 初始化完成")

        return auth_status

    # ── 便捷方法（Sheets）─────────────────────────────────────

    async def read_sheet(
        self,
        spreadsheet_id: str,
        range_name: str,
    ) -> List[Dict[str, str]]:
        """读取 Sheets 数据（便捷方法）

        将原始行数据转换为字典列表（使用表头作为键）。

        Args:
            spreadsheet_id: 电子表格 ID
            range_name: 数据范围

        Returns:
            字典列表，每行一个字典
        """
        result = await self._sheets.read(spreadsheet_id, range_name)

        if not result.headers or not result.data:
            return []

        dict_rows = []
        for row in result.data:
            row_dict: Dict[str, str] = {}
            for i, header in enumerate(result.headers):
                row_dict[header] = row[i] if i < len(row) else ""
            dict_rows.append(row_dict)

        return dict_rows

    async def write_sheet(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: List[List[str]],
    ) -> SheetsWriteResult:
        """写入 Sheets 数据（便捷方法）"""
        return await self._sheets.write(spreadsheet_id, range_name, values)

    async def append_sheet(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: List[List[str]],
    ) -> SheetsWriteResult:
        """追加 Sheets 数据（便捷方法）"""
        return await self._sheets.append(spreadsheet_id, range_name, values)

    async def bulk_append_sheet(
        self,
        spreadsheet_id: str,
        range_name: str,
        values: List[List[str]],
        batch_size: Optional[int] = None,
    ) -> BulkSheetsResult:
        """批量追加 Sheets 数据（便捷方法）"""
        return await self._sheets.bulk_append(
            spreadsheet_id, range_name, values, batch_size=batch_size
        )

    # ── 便捷方法（Drive）──────────────────────────────────────

    async def upload_drive(
        self,
        file_path: str,
        folder_id: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> str:
        """上传文件到 Drive（便捷方法）

        Returns:
            Drive 文件 ID

        Raises:
            DriveApiError: 上传失败
        """
        result = await self._drive.upload(file_path, folder_id, file_name)
        if not result.success or not result.file_id:
            raise DriveApiError(
                message=f"上传失败: {result.error or '未知错误'}",
                operation="drive.upload",
            )
        return result.file_id

    async def bulk_upload_drive(
        self,
        file_paths: List[str],
        folder_id: Optional[str] = None,
    ) -> BulkUploadResult:
        """批量上传文件到 Drive（便捷方法）"""
        return await self._drive.bulk_upload(file_paths, folder_id)

    async def create_drive_folder(
        self,
        name: str,
        parent_id: Optional[str] = None,
    ) -> FolderInfo:
        """创建 Drive 文件夹（便捷方法）"""
        return await self._drive.create_folder(name, parent_id)

    # ── 便捷方法（Gmail）──────────────────────────────────────

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] = None,
        html: bool = False,
        cc: Optional[List[str]] = None,
    ) -> SendResult:
        """发送邮件（便捷方法）"""
        return await self._gmail.send(
            to=[to],
            subject=subject,
            body=body,
            attachments=attachments or [],
            html=html,
            cc=cc or [],
        )

    # ── 健康检查 ──────────────────────────────────────────────

    async def health_check(self: "WorkspaceClient") -> Dict[str, Any]:
        """执行健康检查

        检查认证状态、gws 可用性和各服务连接。

        Returns:
            健康状态字典
        """
        result: Dict[str, Any] = {
            "status": "unknown",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {},
        }

        # 1. 认证检查
        try:
            auth_ok = self._auth.is_valid()
            result["checks"]["auth"] = {
                "status": "ok" if auth_ok else "needs_refresh",
                "email": self._auth.client_email,
                "project": self._auth.project_id,
            }
        except Exception as e:
            result["checks"]["auth"] = {"status": "error", "message": str(e)}

        # 2. gws 二进制检查
        try:
            gws_ok = await self._runner.check_binary()
            result["checks"]["gws"] = {
                "status": "ok" if gws_ok else "not_found",
                "path": self._config.gws_path,
            }
        except Exception as e:
            result["checks"]["gws"] = {"status": "error", "message": str(e)}

        # 3. 整体状态
        all_ok = all(
            c.get("status") in ("ok", "needs_refresh")
            for c in result["checks"].values()
        )
        result["status"] = "healthy" if all_ok else "unhealthy"

        return result

    # ── 属性访问 ──────────────────────────────────────────────

    @property
    def auth(self: "WorkspaceClient") -> AuthManager:
        """认证管理器"""
        return self._auth

    @property
    def sheets(self: "WorkspaceClient") -> "sheets_manager.SheetsManager":
        """Sheets 管理器"""
        return self._sheets

    @property
    def drive(self: "WorkspaceClient") -> "drive_manager.DriveManager":
        """Drive 管理器"""
        return self._drive

    @property
    def gmail(self: "WorkspaceClient") -> "gmail_manager.GmailManager":
        """Gmail 管理器"""
        return self._gmail

    @property
    def config(self: "WorkspaceClient") -> ClientConfig:
        """当前配置"""
        return self._config


# ── 导出 ──────────────────────────────────────────────────

__all__ = [
    # 异常
    "WorkspaceError",
    "AuthError",
    "CredentialNotFoundError",
    "TokenExpiredError",
    "ScopeInsufficientError",
    "RateLimitError",
    "ApiError",
    "SheetsApiError",
    "DriveApiError",
    "GmailApiError",
    "ValidationError",
    "GwsCliError",
    "GwsNotFoundError",
    "GwsTimeoutError",
    "GwsExitCodeError",
    "BulkOperationError",
    # 枚举
    "AuthMethod",
    "GemType",
    "Gender",
    "TaskStatus",
    "TaskPriority",
    "VideoProvider",
    "VideoResolution",
    "ProductCategory",
    # 模型
    "RetryPolicy",
    "RateLimitConfig",
    "ClientConfig",
    "AuthStatus",
    "SheetsReadResult",
    "SheetsWriteResult",
    "BulkSheetsResult",
    "UploadResult",
    "BulkUploadResult",
    "FolderInfo",
    "SendResult",
    # 基础设施
    "AsyncTokenBucket",
    "GwsCommandRunner",
    "AuthManager",
    "BaseManager",
    # 主类
    "WorkspaceClient",
]
