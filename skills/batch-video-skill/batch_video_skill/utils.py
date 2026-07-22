#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils.py — 工具函数

300 Agent 融合项目的通用工具模块。
提供类型转换、文件操作、时间处理、校验等常用工具函数。

包含:
    - 类型转换: 枚举转换、路径处理、JSON 序列化
    - 文件操作: 安全读写、目录创建、文件校验
    - 时间处理: 格式化、解析、超时管理
    - 校验工具: 图片校验、路径校验、配置校验
    - 异步工具: 异步上下文管理器、并发辅助
    - 日志工具: 结构化日志、日志脱敏

Python: 3.11+
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    List,
    Optional,
    Type,
    TypeVar,
    Union,
)

# ═══════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 1. 类型转换
# ═══════════════════════════════════════════════════════════

T = TypeVar("T", bound=Enum)


def enum_from_str(enum_cls: Type[T], value: str, default: Optional[T] = None) -> Optional[T]:
    """从字符串获取枚举值 (大小写不敏感)

    Args:
        enum_cls: 枚举类
        value: 字符串值
        default: 默认值 (转换失败时返回)

    Returns:
        枚举值或默认值

    Examples:
        >>> from models import TaskStatus
        >>> enum_from_str(TaskStatus, "completed")
        <TaskStatus.COMPLETED: 'completed'>
        >>> enum_from_str(TaskStatus, "INVALID", TaskStatus.PENDING)
        <TaskStatus.PENDING: 'pending'>
    """
    if not value:
        return default
    try:
        return enum_cls(value.lower())
    except (ValueError, AttributeError):
        # 尝试大小写不敏感匹配
        for member in enum_cls:
            if member.value.lower() == value.lower():
                return member
        return default


def enum_to_str(value: Optional[Enum]) -> str:
    """将枚举值转换为字符串

    Args:
        value: 枚举值

    Returns:
        字符串表示 (枚举值为空时返回空字符串)
    """
    if value is None:
        return ""
    return value.value


def safe_json_loads(data: Union[str, bytes, None], default: Any = None) -> Any:
    """安全的 JSON 反序列化

    Args:
        data: JSON 字符串或 bytes
        default: 解析失败时的默认值

    Returns:
        解析后的 Python 对象或默认值
    """
    if data is None:
        return default
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as e:
        logger.debug(f"JSON 解析失败: {e}, 返回默认值")
        return default


def safe_json_dumps(
    obj: Any,
    default: Optional[Callable[[Any], Any]] = None,
    indent: Optional[int] = None,
    ensure_ascii: bool = False,
) -> str:
    """安全的 JSON 序列化

    支持 datetime、Enum、Path 等非 JSON 原生类型的自动转换。

    Args:
        obj: 要序列化的对象
        default: 自定义序列化函数
        indent: 缩进空格数
        ensure_ascii: 是否转义非 ASCII 字符

    Returns:
        JSON 字符串 (序列化失败返回 "{}")
    """

    def _default_encoder(o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, Path):
            return o.as_posix()
        if isinstance(o, set):
            return list(o)
        if isinstance(o, bytes):
            try:
                return o.decode("utf-8")
            except UnicodeDecodeError:
                return f"<bytes:{len(o)}>"
        if default:
            return default(o)
        raise TypeError(f"不可序列化的类型: {type(o)}")

    try:
        return json.dumps(
            obj,
            default=_default_encoder,
            indent=indent,
            ensure_ascii=ensure_ascii,
        )
    except (TypeError, ValueError) as e:
        logger.warning(f"JSON 序列化失败: {e}")
        return "{}"


def coerce_int(value: Any, default: int = 0, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    """将任意值安全转换为整数

    Args:
        value: 输入值
        default: 转换失败时的默认值
        min_val: 最小值限制
        max_val: 最大值限制

    Returns:
        整数
    """
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if min_val is not None:
        result = max(result, min_val)
    if max_val is not None:
        result = min(result, max_val)
    return result


def coerce_float(value: Any, default: float = 0.0, min_val: Optional[float] = None, max_val: Optional[float] = None) -> float:
    """将任意值安全转换为浮点数

    Args:
        value: 输入值
        default: 转换失败时的默认值
        min_val: 最小值限制
        max_val: 最大值限制

    Returns:
        浮点数
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if min_val is not None:
        result = max(result, min_val)
    if max_val is not None:
        result = min(result, max_val)
    return result


def coerce_bool(value: Any, default: bool = False) -> bool:
    """将任意值安全转换为布尔值

    支持: bool, int, str (true/yes/1/on)

    Args:
        value: 输入值
        default: 转换失败时的默认值

    Returns:
        布尔值
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1", "on", "enabled")
    return default


# ═══════════════════════════════════════════════════════════
# 2. 文件操作
# ═══════════════════════════════════════════════════════════


def ensure_dir(path: Union[str, Path], mode: int = 0o755) -> Path:
    """确保目录存在 (不存在则创建)

    Args:
        path: 目录路径
        mode: 权限模式

    Returns:
        Path 对象
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True, mode=mode)
    return p


def safe_write_file(
    path: Union[str, Path],
    content: Union[str, bytes],
    mode: str = "w",
    encoding: str = "utf-8",
) -> Path:
    """安全地写入文件 (自动创建父目录)

    Args:
        path: 文件路径
        content: 文件内容
        mode: 打开模式 (w/wb/a/ab)
        encoding: 文本编码

    Returns:
        Path 对象
    """
    p = Path(path)
    ensure_dir(p.parent)
    if "b" in mode:
        with open(p, mode) as f:
            f.write(content)  # type: ignore[arg-type]
    else:
        with open(p, mode, encoding=encoding) as f:
            f.write(content)  # type: ignore[arg-type]
    return p


def safe_read_file(
    path: Union[str, Path],
    default: Optional[str] = None,
    encoding: str = "utf-8",
) -> Optional[str]:
    """安全地读取文本文件

    Args:
        path: 文件路径
        default: 读取失败时的默认值
        encoding: 文本编码

    Returns:
        文件内容或默认值
    """
    try:
        p = Path(path)
        if not p.exists():
            return default
        return p.read_text(encoding=encoding)
    except (OSError, UnicodeDecodeError) as e:
        logger.debug(f"读取文件失败 {path}: {e}")
        return default


def safe_read_bytes(path: Union[str, Path], default: Optional[bytes] = None) -> Optional[bytes]:
    """安全地读取二进制文件

    Args:
        path: 文件路径
        default: 读取失败时的默认值

    Returns:
        文件内容或默认值
    """
    try:
        p = Path(path)
        if not p.exists():
            return default
        return p.read_bytes()
    except OSError as e:
        logger.debug(f"读取文件失败 {path}: {e}")
        return default


def compute_file_hash(path: Union[str, Path], algorithm: str = "md5") -> Optional[str]:
    """计算文件哈希值

    Args:
        path: 文件路径
        algorithm: 哈希算法 (md5/sha256/sha1)

    Returns:
        哈希字符串或 None (文件不存在时)
    """
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return None
        hasher = hashlib.new(algorithm)
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except (OSError, ValueError) as e:
        logger.debug(f"计算哈希失败 {path}: {e}")
        return None


def get_file_size(path: Union[str, Path]) -> int:
    """获取文件大小 (字节)

    Args:
        path: 文件路径

    Returns:
        文件大小 (文件不存在返回 0)
    """
    try:
        p = Path(path)
        if not p.exists():
            return 0
        return p.stat().st_size
    except OSError:
        return 0


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小为人类可读形式

    Args:
        size_bytes: 字节数

    Returns:
        人类可读字符串 (如 "1.5 MB")
    """
    if size_bytes < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    size = float(size_bytes)
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    return f"{size:.1f} {units[idx]}"


def list_image_files(directory: Union[str, Path], recursive: bool = False) -> List[Path]:
    """列出目录中的图片文件

    Args:
        directory: 目录路径
        recursive: 是否递归子目录

    Returns:
        图片文件 Path 列表
    """
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"}
    dir_path = Path(directory)
    if not dir_path.exists() or not dir_path.is_dir():
        return []

    files: List[Path] = []
    pattern = "**/*" if recursive else "*"
    for f in dir_path.glob(pattern):
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
            files.append(f)
    return sorted(files)


def cleanup_old_files(
    directory: Union[str, Path],
    max_age_hours: float = 24.0,
    pattern: str = "*",
) -> int:
    """清理超过指定时长的文件

    Args:
        directory: 目录路径
        max_age_hours: 最大保留时长 (小时)
        pattern: 文件匹配模式

    Returns:
        删除的文件数量
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return 0

    cutoff = time.time() - (max_age_hours * 3600)
    count = 0
    for f in dir_path.glob(pattern):
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                count += 1
            except OSError as e:
                logger.debug(f"删除文件失败 {f}: {e}")
    return count


def copy_file_atomic(src: Union[str, Path], dst: Union[str, Path]) -> Path:
    """原子复制文件 (使用临时文件 + rename)

    Args:
        src: 源文件路径
        dst: 目标文件路径

    Returns:
        目标 Path 对象
    """
    import tempfile

    dst_path = Path(dst)
    ensure_dir(dst_path.parent)

    # 创建临时文件
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(dst_path.parent),
        suffix=".tmp",
    )
    try:
        os.close(tmp_fd)
        shutil.copy2(str(src), tmp_path)
        os.replace(tmp_path, str(dst_path))
    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return dst_path


# ═══════════════════════════════════════════════════════════
# 3. 时间处理
# ═══════════════════════════════════════════════════════════


def format_datetime(dt: Optional[datetime] = None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """格式化日期时间

    Args:
        dt: 日期时间对象 (None 则使用当前时间)
        fmt: 格式字符串

    Returns:
        格式化字符串
    """
    if dt is None:
        dt = datetime.now()
    return dt.strftime(fmt)


def format_iso(dt: Optional[datetime] = None) -> str:
    """格式化为 ISO 8601 格式

    Args:
        dt: 日期时间对象 (None 则使用当前时间)

    Returns:
        ISO 格式字符串
    """
    if dt is None:
        dt = datetime.now()
    return dt.isoformat()


def parse_iso(value: str) -> Optional[datetime]:
    """解析 ISO 8601 格式日期时间

    Args:
        value: ISO 格式字符串

    Returns:
        datetime 对象或 None
    """
    try:
        # 处理带 Z 的格式
        value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def format_duration(seconds: float) -> str:
    """格式化时长为人类可读形式

    Args:
        seconds: 秒数

    Returns:
        人类可读字符串 (如 "2分30秒")
    """
    if seconds < 0:
        return "0秒"
    if seconds < 1:
        return f"{seconds * 1000:.0f}毫秒"
    if seconds < 60:
        return f"{seconds:.1f}秒"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}分{secs}秒"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}小时{mins}分{secs}秒"


class Timer:
    """上下文管理器计时器

    用于测量代码块执行时间。

    Examples:
        >>> with Timer("处理任务") as t:
        ...     time.sleep(1)
        >>> print(t.elapsed)
        1.0
    """

    def __init__(self: "Timer", name: str = "operation", logger_obj: Optional[logging.Logger] = None) -> None:
        self.name = name
        self.logger = logger_obj or logger
        self._start: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self: "Timer") -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self: "Timer", *args: Any) -> None:
        self.elapsed = time.perf_counter() - self._start
        self.logger.debug(f"[{self.name}] 耗时: {self.elapsed:.3f}s")


# ═══════════════════════════════════════════════════════════
# 4. 校验工具
# ═══════════════════════════════════════════════════════════


def is_valid_image_file(path: Union[str, Path]) -> bool:
    """校验文件是否为有效图片

    检查文件存在性、可读性和 MIME 类型。

    Args:
        path: 文件路径

    Returns:
        是否为有效图片
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return False
    if p.stat().st_size == 0:
        return False
    mime, _ = mimetypes.guess_type(str(p))
    if mime is None:
        # 尝试通过后缀判断
        return p.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp")
    return mime.startswith("image/")


def validate_image_path(path: Union[str, Path]) -> Optional[str]:
    """校验图片路径并返回错误信息

    Args:
        path: 图片路径

    Returns:
        错误信息 (有效时返回 None)
    """
    p = Path(path)
    if not p.exists():
        return f"文件不存在: {p}"
    if not p.is_file():
        return f"不是文件: {p}"
    if p.stat().st_size == 0:
        return f"文件为空: {p}"
    if not is_valid_image_file(p):
        return f"不是有效图片: {p}"
    return None


def validate_email(email: str) -> bool:
    """校验邮箱地址格式

    Args:
        email: 邮箱地址

    Returns:
        是否有效
    """
    if not email:
        return False
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def validate_url(url: str) -> bool:
    """校验 URL 格式

    Args:
        url: URL 字符串

    Returns:
        是否有效
    """
    if not url:
        return False
    pattern = r"^https?://[^\s/$.?#].[^\s]*$"
    return bool(re.match(pattern, url, re.IGNORECASE))


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """清理文件名 (移除非法字符)

    Args:
        name: 原始文件名
        max_length: 最大长度

    Returns:
        清理后的文件名
    """
    # 移除非法字符
    sanitized = re.sub(r'[\\/*?:"<>|]', "_", name)
    # 移除控制字符
    sanitized = re.sub(r'[\x00-\x1f\x7f]', "", sanitized)
    # 限制长度
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
    # 去除首尾空白和点
    sanitized = sanitized.strip(". ")
    # 确保不为空
    if not sanitized:
        sanitized = "unnamed"
    return sanitized


# ═══════════════════════════════════════════════════════════
# 5. 异步工具
# ═══════════════════════════════════════════════════════════


@asynccontextmanager
async def timeout_context(seconds: float) -> AsyncGenerator[None, None]:
    """异步超时上下文管理器

    Args:
        seconds: 超时秒数

    Raises:
        asyncio.TimeoutError: 超时时抛出

    Examples:
        >>> async with timeout_context(5.0):
        ...     await long_operation()
    """
    try:
        yield
    except asyncio.TimeoutError:
        raise


async def run_with_timeout(
    coro: Any,
    timeout: float,
    default: Any = None,
) -> Any:
    """带超时的运行协程

    Args:
        coro: 协程对象
        timeout: 超时秒数
        default: 超时时的默认值

    Returns:
        协程结果或默认值
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"操作超时 (>{timeout}s)，返回默认值")
        return default


async def semaphore_gather(
    tasks: List[Any],
    concurrency: int = 5,
    return_exceptions: bool = True,
) -> List[Any]:
    """使用信号量限制的并发 gather

    Args:
        tasks: 协程任务列表
        concurrency: 最大并发数
        return_exceptions: 是否返回异常

    Returns:
        结果列表
    """
    sem = asyncio.Semaphore(concurrency)

    async def _wrapped(task: Any) -> Any:
        async with sem:
            return await task

    wrapped_tasks = [_wrapped(t) for t in tasks]
    return await asyncio.gather(*wrapped_tasks, return_exceptions=return_exceptions)


def async_to_sync(coro: Any) -> Any:
    """将协程转为同步调用 (获取事件循环)

    Args:
        coro: 协程对象

    Returns:
        协程结果
    """
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            # 在已有事件循环中，创建新任务
            import nest_asyncio

            nest_asyncio.apply()
            return loop.run_until_complete(coro)
    except RuntimeError:
        pass
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════
# 6. 日志工具
# ═══════════════════════════════════════════════════════════


class SensitiveFormatter(logging.Formatter):
    """脱敏日志格式化器

    自动过滤敏感信息 (API keys, tokens, cookies)。
    """

    # 需要脱敏的字段模式
    SENSITIVE_PATTERNS: List[re.Pattern] = [
        re.compile(r'(api_key["\s]*[:=]["\s]*)([^"\s]{4})[^"\s]*', re.IGNORECASE),
        re.compile(r'(api_secret["\s]*[:=]["\s]*)([^"\s]{4})[^"\s]*', re.IGNORECASE),
        re.compile(r'(1psid["\s]*[:=]["\s]*)([^"\s]{4})[^"\s]*', re.IGNORECASE),
        re.compile(r'(1psidts["\s]*[:=]["\s]*)([^"\s]{4})[^"\s]*', re.IGNORECASE),
        re.compile(r'(token["\s]*[:=]["\s]*)([^"\s]{4})[^"\s]*', re.IGNORECASE),
        re.compile(r'(password["\s]*[:=]["\s]*)([^"\s]{2})[^"\s]*', re.IGNORECASE),
        re.compile(r'(Bearer\s+)([^\s]{4})[^\s]*', re.IGNORECASE),
    ]

    def format(self: "SensitiveFormatter", record: logging.LogRecord) -> str:
        """格式化日志记录 (带脱敏)"""
        msg = super().format(record)
        for pattern in self.SENSITIVE_PATTERNS:
            msg = pattern.sub(r"\1****", msg)
        return msg


def setup_logging(
    level: Union[str, int] = "INFO",
    format_str: Optional[str] = None,
    use_sensitive_filter: bool = True,
) -> None:
    """配置日志

    Args:
        level: 日志级别
        format_str: 格式字符串
        use_sensitive_filter: 是否使用脱敏过滤器
    """
    if format_str is None:
        format_str = (
            "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"
        )

    formatter = SensitiveFormatter(format_str) if use_sensitive_filter else logging.Formatter(format_str)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(level)


def mask_sensitive(value: Optional[str], visible_prefix: int = 4) -> str:
    """脱敏字符串 (保留前缀，其余替换为 *)

    Args:
        value: 原始字符串
        visible_prefix: 保留前缀字符数

    Returns:
        脱敏后的字符串
    """
    if not value:
        return ""
    if len(value) <= visible_prefix:
        return "****"
    return value[:visible_prefix] + "****"


# ═══════════════════════════════════════════════════════════
# 7. 杂项工具
# ═══════════════════════════════════════════════════════════


def chunk_list(items: List[T], chunk_size: int) -> List[List[T]]:
    """将列表分块

    Args:
        items: 原始列表
        chunk_size: 每块大小

    Returns:
        分块后的列表

    Examples:
        >>> chunk_list([1, 2, 3, 4, 5], 2)
        [[1, 2], [3, 4], [5]]
    """
    if chunk_size <= 0:
        return [items]
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def merge_dicts(base: Dict[str, Any], override: Dict[str, Any], deep: bool = True) -> Dict[str, Any]:
    """合并两个字典

    Args:
        base: 基础字典
        override: 覆盖字典
        deep: 是否深度合并

    Returns:
        合并后的字典
    """
    if not deep:
        return {**base, **override}

    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value, deep=True)
        else:
            result[key] = value
    return result


def truncate_string(value: str, max_length: int = 100, suffix: str = "...") -> str:
    """截断字符串

    Args:
        value: 原始字符串
        max_length: 最大长度
        suffix: 截断后缀

    Returns:
        截断后的字符串
    """
    if len(value) <= max_length:
        return value
    return value[: max_length - len(suffix)] + suffix


def retry_sync(
    func: Callable[..., T],
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> T:
    """同步重试装饰器

    Args:
        func: 要执行的函数
        max_retries: 最大重试次数
        delay: 初始延迟 (秒)
        backoff: 退避倍数
        exceptions: 捕获的异常类型

    Returns:
        函数返回值

    Raises:
        最后一次异常
    """
    last_exception: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except exceptions as e:
            last_exception = e
            if attempt < max_retries:
                wait = delay * (backoff**attempt)
                logger.debug(f"第 {attempt + 1} 次重试，等待 {wait:.1f}s: {e}")
                time.sleep(wait)
    raise last_exception  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════
# 8. 便利导出
# ═══════════════════════════════════════════════════════════

__all__ = [
    # 类型转换
    "enum_from_str",
    "enum_to_str",
    "safe_json_loads",
    "safe_json_dumps",
    "coerce_int",
    "coerce_float",
    "coerce_bool",
    # 文件操作
    "ensure_dir",
    "safe_write_file",
    "safe_read_file",
    "safe_read_bytes",
    "compute_file_hash",
    "get_file_size",
    "format_file_size",
    "list_image_files",
    "cleanup_old_files",
    "copy_file_atomic",
    # 时间处理
    "format_datetime",
    "format_iso",
    "parse_iso",
    "format_duration",
    "Timer",
    # 校验工具
    "is_valid_image_file",
    "validate_image_path",
    "validate_email",
    "validate_url",
    "sanitize_filename",
    # 异步工具
    "timeout_context",
    "run_with_timeout",
    "semaphore_gather",
    "async_to_sync",
    # 日志工具
    "SensitiveFormatter",
    "setup_logging",
    "mask_sensitive",
    # 杂项
    "chunk_list",
    "merge_dicts",
    "truncate_string",
    "retry_sync",
]
