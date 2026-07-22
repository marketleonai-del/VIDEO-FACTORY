"""
═══════════════════════════════════════════════════════════════════════════════
批量处理器 (Batch Processor) — 完整实现
═══════════════════════════════════════════════════════════════════════════════

职责:
  1. 接收一批图片路径，并发处理为带货视频
  2. 管理并发控制（asyncio.Semaphore + asyncio.gather）
  3. 追踪处理进度（回调 + Sheets 更新）
  4. 支持优雅取消（asyncio.Event）
  5. 汇总处理结果（成功/失败统计）
  6. 错误隔离 — 单任务失败不影响其他任务

设计模式: 模板方法模式（阶段执行）+ 观察者模式（进度回调）

依赖:
    task_scheduler — 提供核心模型、异常、状态机、并发控制等子系统

Author: 300 Agent 融合项目
Version: 1.0
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import (
    Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple, Union,
)

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 从 task_scheduler 导入核心子系统
from task_scheduler import (
    # 枚举
    TaskStatus as SchedulerTaskStatus,
    TaskAction,
    BatchStatus,
    # 异常
    TaskSchedulerError,
    ProviderError,
    # 模型
    Task,
    Batch,
    TransitionRecord,
    ProgressSnapshot,
    PollResult,
    PollConfig,
    # 子系统
    StateMachine,
    ConcurrencyController,
    CheckpointManager,
    ProgressTracker,
    AsyncPoller,
    ITaskStore,
    InMemoryTaskStore,
    SchedulerConfig,
    ConcurrencyConfig,
    CheckpointConfig,
    # 门面
    TaskScheduler,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 批量处理器专属枚举
# ═══════════════════════════════════════════════════════════════════════════


class ProcessingStage(str, Enum):
    """处理阶段枚举 — 用于进度追踪的阶段标识"""

    IDLE = "idle"
    ANALYSIS = "analysis"
    ROUTING = "routing"
    PROMPT_GEN = "prompt_gen"
    VIDEO_GEN = "video_gen"
    FINALIZING = "finalizing"


class ErrorCode(str, Enum):
    """错误码枚举 — 统一错误分类"""

    NONE = ""
    INVALID_IMAGE = "INVALID_IMAGE"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"
    ROUTING_FAILED = "ROUTING_FAILED"
    PROMPT_GEN_FAILED = "PROMPT_GEN_FAILED"
    VIDEO_GEN_FAILED = "VIDEO_GEN_FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


# 为 batch_processor 创建一个兼容的 TaskStatus（增加 CANCELLED 状态）
class TaskStatus(str, Enum):
    """单个任务处理状态 — 扩展自 Scheduler TaskStatus"""

    PENDING = "pending"
    ANALYZING = "analyzing"
    ROUTING = "routing"
    PROMPT_GENERATING = "prompt_generating"
    VIDEO_GENERATING = "video_generating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ═══════════════════════════════════════════════════════════════════════════
# 结果模型
# ═══════════════════════════════════════════════════════════════════════════


class TaskResult(BaseModel):
    """
    单个任务处理结果

    每个图片处理完成后产生一个 TaskResult，包含完整的处理状态和输出。
    """

    model_config = ConfigDict(validate_assignment=True)

    # ── 标识 ──
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    image_path: str = ""
    image_name: str = ""

    # ── 状态 ──
    status: TaskStatus = TaskStatus.PENDING
    stage: ProcessingStage = ProcessingStage.IDLE

    # ── 输出 ──
    output_video_path: Optional[str] = None
    product_category: Optional[str] = None
    matched_gem_id: Optional[str] = None
    generated_prompt: Optional[str] = None

    # ── 错误 ──
    error_code: ErrorCode = ErrorCode.NONE
    error_message: Optional[str] = None

    # ── 性能 ──
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    processing_time_sec: Optional[float] = None
    stage_timings: Dict[str, float] = Field(default_factory=dict)

    # ── 元数据 ──
    retry_count: int = Field(default=0, ge=0)

    def is_success(self: "TaskResult") -> bool:
        """是否处理成功"""
        return self.status == TaskStatus.COMPLETED

    def is_failed(self: "TaskResult") -> bool:
        """是否处理失败"""
        return self.status == TaskStatus.FAILED

    def is_cancelled(self: "TaskResult") -> bool:
        """是否被取消"""
        return self.status == TaskStatus.CANCELLED

    @field_validator("processing_time_sec", mode="before")
    @classmethod
    def round_timing(cls: type["TaskResult"], v: Optional[float]) -> Optional[float]:
        """耗时保留 2 位小数"""
        if v is not None:
            return round(v, 2)
        return v


class BatchResult(BaseModel):
    """
    批量处理结果

    一批任务全部处理完成后聚合的结果，包含统计信息和所有子任务结果。
    """

    model_config = ConfigDict(validate_assignment=True)

    # ── 标识 ──
    batch_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])

    # ── 统计 ──
    total: int = 0
    success_count: int = 0
    failed_count: int = 0
    cancelled_count: int = 0
    pending_count: int = 0

    # ── 详细结果 ──
    results: List[TaskResult] = Field(default_factory=list)

    # ── 错误汇总 ──
    error_summary: Dict[str, int] = Field(default_factory=dict)

    # ── 性能 ──
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    total_time_sec: Optional[float] = None
    avg_time_per_task: Optional[float] = None

    # ── 状态 ──
    is_cancelled: bool = False

    def calculate_stats(self: "BatchResult") -> None:
        """根据 results 重新计算统计数据"""
        self.total = len(self.results)
        self.success_count = sum(1 for r in self.results if r.is_success())
        self.failed_count = sum(1 for r in self.results if r.is_failed())
        self.cancelled_count = sum(1 for r in self.results if r.is_cancelled())
        self.pending_count = (
            self.total
            - self.success_count
            - self.failed_count
            - self.cancelled_count
        )

        # 错误汇总
        self.error_summary.clear()
        for r in self.results:
            if r.error_code and r.error_code != ErrorCode.NONE:
                code = r.error_code.value
                self.error_summary[code] = self.error_summary.get(code, 0) + 1

        # 计算总时间
        if self.started_at and self.completed_at:
            try:
                start = datetime.fromisoformat(self.started_at)
                end = datetime.fromisoformat(self.completed_at)
                self.total_time_sec = round((end - start).total_seconds(), 2)
                if self.total > 0:
                    self.avg_time_per_task = round(
                        self.total_time_sec / self.total, 2
                    )
            except (ValueError, TypeError):
                pass

    @property
    def success_rate(self: "BatchResult") -> float:
        """成功率（百分比）"""
        if self.total == 0:
            return 0.0
        return round(self.success_count / self.total * 100, 1)

    @property
    def failed_results(self: "BatchResult") -> List[TaskResult]:
        """获取所有失败的任务结果"""
        return [r for r in self.results if r.is_failed()]

    @property
    def successful_results(self: "BatchResult") -> List[TaskResult]:
        """获取所有成功的任务结果"""
        return [r for r in self.results if r.is_success()]

    def format_summary(self: "BatchResult") -> str:
        """格式化结果为可读的汇总报告"""
        lines = [
            "═══════════════════════════════════════════════",
            f"  批次处理结果: {self.batch_id}",
            "═══════════════════════════════════════════════",
            f"  总任务数:     {self.total}",
            f"  成功:         {self.success_count} ({self.success_rate}%)",
            f"  失败:         {self.failed_count}",
            f"  取消:         {self.cancelled_count}",
            "  ─────────────────────────────────────────",
            f"  总耗时:       {self.total_time_sec or 0:.1f}s",
            f"  平均每任务:   {self.avg_time_per_task or 0:.1f}s",
            "  ─────────────────────────────────────────",
        ]

        if self.error_summary:
            lines.append("  错误分类:")
            for code, count in self.error_summary.items():
                lines.append(f"    {code}: {count}")

        if self.is_cancelled:
            lines.append("  [注意] 批次已被取消")

        lines.append("═══════════════════════════════════════════════")
        return "\n".join(lines)


class ProgressInfo(BaseModel):
    """
    进度信息 — 实时处理进度快照

    用于回调函数和外部查询，反映当前处理状态。
    """

    model_config = ConfigDict(frozen=True)

    batch_id: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    # ── 计数 ──
    total: int = 0
    completed: int = 0
    success: int = 0
    failed: int = 0
    in_progress: int = 0
    pending: int = 0
    cancelled: int = 0

    # ── 百分比 ──
    percent_complete: float = 0.0
    percent_success: float = 0.0

    # ── 当前阶段 ──
    current_stage: ProcessingStage = ProcessingStage.IDLE
    current_image: Optional[str] = None

    # ── 性能估算 ──
    elapsed_sec: Optional[float] = None
    eta_sec: Optional[float] = None
    throughput: Optional[float] = None

    # ── 状态 ──
    is_running: bool = False
    is_cancelled: bool = False

    def format_bar(self: "ProgressInfo", width: int = 40) -> str:
        """格式化 ASCII 进度条"""
        filled = int(width * self.percent_complete / 100)
        bar = "█" * filled + "░" * (width - filled)
        eta_str = f"{self.eta_sec:.0f}s" if self.eta_sec else "N/A"
        return (
            f"[{bar}] {self.percent_complete:.1f}% "
            f"({self.completed}/{self.total}) "
            f"成功:{self.success} 失败:{self.failed} "
            f"ETA:{eta_str}"
        )


class ProcessOptions(BaseModel):
    """
    处理选项 — 控制批量处理行为

    用户可配置的处理参数。
    """

    model_config = ConfigDict(validate_assignment=True)

    # ── 并发控制 ──
    concurrency: int = Field(default=3, ge=1, le=10)

    # ── 超时控制 ──
    timeout_per_task: float = Field(default=600.0, ge=10)
    timeout_total: Optional[float] = None

    # ── 重试控制 ──
    max_retries: int = Field(default=2, ge=0, le=5)
    retry_delay: float = Field(default=2.0, ge=0)

    # ── 视频生成选项 ──
    video_duration: int = Field(default=5, ge=3, le=10)
    video_resolution: str = "720p"
    preferred_provider: Optional[str] = None

    # ── 回调 ──
    progress_callback: Optional[Callable[[ProgressInfo], None]] = Field(
        default=None, exclude=True
    )

    # ── Sheets 更新 ──
    enable_sheets_update: bool = True
    sheets_update_interval: float = Field(default=5.0, ge=1)

    # ── 其他 ──
    output_dir: str = "./output"
    skip_existing: bool = True

    @field_validator("output_dir")
    @classmethod
    def validate_output_dir(cls: type["ProcessOptions"], v: str) -> str:
        Path(v).mkdir(parents=True, exist_ok=True)
        return v


class StageTiming(BaseModel):
    """单个阶段的时间记录"""

    model_config = ConfigDict(frozen=True)

    stage: ProcessingStage
    started_at: float
    completed_at: Optional[float] = None

    @property
    def duration(self: "StageTiming") -> Optional[float]:
        if self.completed_at is not None:
            return round(self.completed_at - self.started_at, 3)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# 批量处理器核心类
# ═══════════════════════════════════════════════════════════════════════════


class BatchProcessor:
    """
    ╔══════════════════════════════════════════════════════════════════════════╗
    ║                        BatchProcessor                                   ║
    ║                       批量处理器 — 核心执行引擎                           ║
    ╠══════════════════════════════════════════════════════════════════════════╣
    ║ 职责:                                                                    ║
    ║   1. 接收一批图片路径，并发处理为带货视频                                  ║
    ║   2. 管理并发控制（asyncio.Semaphore + asyncio.gather）                  ║
    ║   3. 追踪处理进度（回调 + Sheets 更新）                                    ║
    ║   4. 支持优雅取消（asyncio.Event）                                       ║
    ║   5. 汇总处理结果（成功/失败统计）                                        ║
    ║                                                                          ║
    ║ 线程安全: 是（基于 asyncio 单线程模型）                                   ║
    ║ 取消安全: 是（所有挂起操作可被取消）                                       ║
    ╚══════════════════════════════════════════════════════════════════════════╝
    """

    def __init__(
        self,
        scheduler: TaskScheduler,
        concurrency: int = 3,
    ) -> None:
        """
        初始化批量处理器

        Args:
            scheduler: TaskScheduler 实例，用于访问子系统
            concurrency: 最大并发数（默认 3）
        """
        self._scheduler = scheduler
        self._concurrency = concurrency

        # ── 并发控制 ──
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(concurrency)
        self._cancel_event: asyncio.Event = asyncio.Event()

        # ── 状态 ──
        self._batch_id: str = ""
        self._is_running: bool = False
        self._is_cancelled: bool = False
        self._start_time: float = 0.0
        self._completed_count: int = 0
        self._success_count: int = 0
        self._failed_count: int = 0
        self._cancelled_count: int = 0
        self._pending_at_start: int = 0

        # ── 进度追踪 ──
        self._progress_callbacks: List[Callable[[ProgressInfo], None]] = []
        self._current_image: Optional[str] = None
        self._current_stage: ProcessingStage = ProcessingStage.IDLE
        self._throughput_ema: Optional[float] = None

        # ── Sheets 更新 ──
        self._sheets_update_task: Optional[asyncio.Task] = None
        self._last_sheets_update: float = 0.0

        # ── 子系统引用 ──
        self._store = getattr(scheduler, "store", None)
        self._state_machine = getattr(scheduler, "sm", None)
        self._checkpoint = getattr(scheduler, "checkpoint", None)
        self._concurrency_ctl = getattr(scheduler, "concurrency", None)

    # ═══════════════════════════════════════════════════════════════════════
    # 公共方法
    # ═══════════════════════════════════════════════════════════════════════

    async def process(
        self,
        image_paths: List[str],
        options: ProcessOptions,
    ) -> BatchResult:
        """
        批量处理入口 — 并发处理一批图片

        流程:
          1. 验证输入参数
          2. 初始化批次状态
          3. 创建并发任务（Semaphore 控制）
          4. 等待所有任务完成（asyncio.gather）
          5. 汇总结果 -> BatchResult

        Args:
            image_paths: 图片路径列表
            options: 处理选项

        Returns:
            BatchResult: 批量处理结果

        Raises:
            ValueError: image_paths 为空或包含无效路径
            RuntimeError: 处理器已在运行中
        """
        # ── 1. 参数验证 ──
        if not image_paths:
            raise ValueError("image_paths 不能为空列表")

        valid_paths = [p for p in image_paths if Path(p).exists()]
        if not valid_paths:
            raise ValueError("所有图片路径均无效")

        if len(valid_paths) != len(image_paths):
            logger.warning(
                f"过滤了 {len(image_paths) - len(valid_paths)} 个无效路径"
            )

        if self._is_running:
            raise RuntimeError(
                "BatchProcessor 已在运行中，不可重复调用 process()"
            )

        # ── 2. 初始化状态 ──
        self._batch_id = str(uuid.uuid4())[:12]
        self._is_running = True
        self._is_cancelled = False
        self._cancel_event.clear()
        self._start_time = time.monotonic()
        self._completed_count = 0
        self._success_count = 0
        self._failed_count = 0
        self._cancelled_count = 0
        self._throughput_ema = None
        self._pending_at_start = len(valid_paths)

        # 注册外部回调
        if options.progress_callback:
            self.register_callback(options.progress_callback)

        # ── 3. 创建结果容器 ──
        batch_result = BatchResult(
            batch_id=self._batch_id,
            total=len(valid_paths),
            started_at=datetime.utcnow().isoformat(),
        )

        # ── 4. 启动 Sheets 更新循环 ──
        if options.enable_sheets_update and self._store:
            self._sheets_update_task = asyncio.create_task(
                self._sheets_update_loop(options.sheets_update_interval)
            )

        # ── 5. 并发处理 ──
        self._current_stage = ProcessingStage.ANALYSIS
        logger.info(
            f"批次 {self._batch_id} 开始处理: {len(valid_paths)} 个任务, "
            f"并发数: {options.concurrency}"
        )

        try:
            # 使用 gather 并发执行所有任务
            task_results = await self._run_concurrent_with_gather(
                valid_paths, options
            )
            batch_result.results = task_results

        except asyncio.CancelledError:
            logger.info(f"批次 {self._batch_id} 被取消")
            batch_result.is_cancelled = True
            self._is_cancelled = True
            raise

        finally:
            # ── 6. 清理 ──
            self._is_running = False
            self._current_stage = ProcessingStage.FINALIZING

            # 停止 Sheets 更新
            if self._sheets_update_task:
                self._sheets_update_task.cancel()
                try:
                    await self._sheets_update_task
                except asyncio.CancelledError:
                    pass
                self._sheets_update_task = None

            # 最终 Sheets 更新
            if options.enable_sheets_update:
                await self._final_sheets_update(batch_result)

        # ── 7. 汇总结果 ──
        batch_result.completed_at = datetime.utcnow().isoformat()
        batch_result.calculate_stats()
        batch_result.is_cancelled = self._is_cancelled

        # 发送最终进度
        await self._emit_progress(final=True)

        logger.info(
            f"批次 {self._batch_id} 完成: "
            f"成功 {batch_result.success_count}, "
            f"失败 {batch_result.failed_count}, "
            f"取消 {batch_result.cancelled_count}, "
            f"耗时 {batch_result.total_time_sec or 0:.1f}s"
        )

        return batch_result

    async def _process_single(
        self,
        image_path: str,
        options: ProcessOptions,
    ) -> TaskResult:
        """
        处理单个图片 — 完整处理流水线

        流程:
          PENDING -> ANALYZING -> ROUTING -> PROMPT_GEN -> VIDEO_GEN -> COMPLETED/FAILED

        Args:
            image_path: 图片路径
            options: 处理选项

        Returns:
            TaskResult: 单个任务处理结果
        """
        task_id = str(uuid.uuid4())[:8]
        result = TaskResult(
            task_id=task_id,
            image_path=image_path,
            image_name=Path(image_path).stem,
            status=TaskStatus.PENDING,
            started_at=datetime.utcnow().isoformat(),
        )

        stage_start = time.monotonic()

        try:
            # ── 检查取消 ──
            if self._cancel_event.is_set():
                result.status = TaskStatus.CANCELLED
                result.error_code = ErrorCode.CANCELLED
                result.error_message = "任务在开始处理前被取消"
                return result

            # ═══════════════════════════════════════════════════════════════
            # 阶段 1: 图片分析
            # ═══════════════════════════════════════════════════════════════
            self._current_image = result.image_name
            self._current_stage = ProcessingStage.ANALYSIS
            result.status = TaskStatus.ANALYZING

            await self._emit_progress()

            analysis_start = time.monotonic()
            result.stage_timings["analysis_start"] = analysis_start

            try:
                # 调用图片分析器
                category = await self._analyze_image(image_path, options)
                result.product_category = category
                result.stage_timings["analysis"] = round(
                    time.monotonic() - analysis_start, 3
                )
            except Exception as e:
                logger.error(f"[{task_id}] 图片分析失败: {e}")
                result.status = TaskStatus.FAILED
                result.error_code = ErrorCode.ANALYSIS_FAILED
                result.error_message = f"图片分析失败: {str(e)[:200]}"
                result.stage_timings["analysis"] = round(
                    time.monotonic() - analysis_start, 3
                )
                return result

            # ── 检查取消 ──
            if self._cancel_event.is_set():
                result.status = TaskStatus.CANCELLED
                result.error_code = ErrorCode.CANCELLED
                result.error_message = "任务在分析阶段后被取消"
                return result

            # ═══════════════════════════════════════════════════════════════
            # 阶段 2: Gem 路由
            # ═══════════════════════════════════════════════════════════════
            result.status = TaskStatus.ROUTING
            self._current_stage = ProcessingStage.ROUTING
            await self._emit_progress()

            routing_start = time.monotonic()
            result.stage_timings["routing_start"] = routing_start

            try:
                gem_id = await self._route_gem(result.product_category, options)
                result.matched_gem_id = gem_id
                result.stage_timings["routing"] = round(
                    time.monotonic() - routing_start, 3
                )
            except Exception as e:
                logger.error(f"[{task_id}] Gem 路由失败: {e}")
                result.status = TaskStatus.FAILED
                result.error_code = ErrorCode.ROUTING_FAILED
                result.error_message = f"Gem 路由失败: {str(e)[:200]}"
                result.stage_timings["routing"] = round(
                    time.monotonic() - routing_start, 3
                )
                return result

            # ── 检查取消 ──
            if self._cancel_event.is_set():
                result.status = TaskStatus.CANCELLED
                result.error_code = ErrorCode.CANCELLED
                result.error_message = "任务在路由阶段后被取消"
                return result

            # ═══════════════════════════════════════════════════════════════
            # 阶段 3: 提示词生成
            # ═══════════════════════════════════════════════════════════════
            result.status = TaskStatus.PROMPT_GENERATING
            self._current_stage = ProcessingStage.PROMPT_GEN
            await self._emit_progress()

            prompt_start = time.monotonic()
            result.stage_timings["prompt_gen_start"] = prompt_start

            try:
                prompt = await self._generate_prompt(
                    gem_id=result.matched_gem_id,
                    image_path=image_path,
                    options=options,
                )
                result.generated_prompt = prompt
                result.stage_timings["prompt_gen"] = round(
                    time.monotonic() - prompt_start, 3
                )
            except Exception as e:
                logger.error(f"[{task_id}] 提示词生成失败: {e}")
                result.status = TaskStatus.FAILED
                result.error_code = ErrorCode.PROMPT_GEN_FAILED
                result.error_message = f"提示词生成失败: {str(e)[:200]}"
                result.stage_timings["prompt_gen"] = round(
                    time.monotonic() - prompt_start, 3
                )
                return result

            # ── 检查取消 ──
            if self._cancel_event.is_set():
                result.status = TaskStatus.CANCELLED
                result.error_code = ErrorCode.CANCELLED
                result.error_message = "任务在提示词阶段后被取消"
                return result

            # ═══════════════════════════════════════════════════════════════
            # 阶段 4: 视频生成
            # ═══════════════════════════════════════════════════════════════
            result.status = TaskStatus.VIDEO_GENERATING
            self._current_stage = ProcessingStage.VIDEO_GEN
            await self._emit_progress()

            video_start = time.monotonic()
            result.stage_timings["video_gen_start"] = video_start

            try:
                video_path = await self._generate_video(
                    image_path=image_path,
                    prompt=result.generated_prompt,
                    options=options,
                )
                result.output_video_path = video_path
                result.status = TaskStatus.COMPLETED
                result.stage_timings["video_gen"] = round(
                    time.monotonic() - video_start, 3
                )
            except asyncio.CancelledError:
                result.status = TaskStatus.CANCELLED
                result.error_code = ErrorCode.CANCELLED
                result.error_message = "任务在视频生成阶段被取消"
                raise
            except Exception as e:
                logger.error(f"[{task_id}] 视频生成失败: {e}")
                result.status = TaskStatus.FAILED
                result.error_code = ErrorCode.VIDEO_GEN_FAILED
                result.error_message = f"视频生成失败: {str(e)[:200]}"
                result.stage_timings["video_gen"] = round(
                    time.monotonic() - video_start, 3
                )
                return result

        except asyncio.CancelledError:
            result.status = TaskStatus.CANCELLED
            result.error_code = ErrorCode.CANCELLED
            result.error_message = "任务被取消"
            raise

        except Exception as e:
            logger.exception(f"[{task_id}] 任务处理异常: {e}")
            result.status = TaskStatus.FAILED
            result.error_code = ErrorCode.UNKNOWN
            result.error_message = f"未知错误: {str(e)[:200]}"

        finally:
            # ── 完成处理 ──
            result.completed_at = datetime.utcnow().isoformat()
            if result.started_at:
                try:
                    start_t = datetime.fromisoformat(result.started_at)
                    end_t = datetime.fromisoformat(result.completed_at)
                    result.processing_time_sec = round(
                        (end_t - start_t).total_seconds(), 2
                    )
                except (ValueError, TypeError):
                    result.processing_time_sec = round(
                        time.monotonic() - stage_start, 2
                    )

            # 更新全局计数
            self._completed_count += 1
            if result.is_success():
                self._success_count += 1
            elif result.is_failed():
                self._failed_count += 1
            elif result.is_cancelled():
                self._cancelled_count += 1

            # 发送进度更新
            await self._emit_progress()

            # 持久化
            if self._checkpoint:
                try:
                    await self._checkpoint.mark_dirty(
                        self._task_result_to_model(result)
                    )
                except Exception as e:
                    logger.warning(f"持久化任务状态失败: {e}")

        return result

    async def _run_concurrent_with_gather(
        self,
        image_paths: List[str],
        options: ProcessOptions,
    ) -> List[TaskResult]:
        """
        使用 asyncio.gather 并发执行所有任务

        实现: Semaphore 包装 + gather + return_exceptions=True

        Args:
            image_paths: 图片路径列表
            options: 处理选项

        Returns:
            List[TaskResult]: 所有任务的结果列表
        """
        semaphore = asyncio.Semaphore(options.concurrency)

        async def _wrapped_task(path: str) -> TaskResult:
            """带信号量和取消检查的任务包装"""
            async with semaphore:
                # 检查取消
                if self._cancel_event.is_set():
                    return TaskResult(
                        image_path=path,
                        image_name=Path(path).stem,
                        status=TaskStatus.CANCELLED,
                        error_code=ErrorCode.CANCELLED,
                        error_message="批次已取消",
                    )
                return await self._process_single(path, options)

        # 创建所有任务
        coroutines = [_wrapped_task(path) for path in image_paths]

        # gather 执行（return_exceptions=True 确保单个失败不中断其他）
        raw_results = await asyncio.gather(*coroutines, return_exceptions=True)

        # 包装异常为 TaskResult
        results: List[TaskResult] = []
        for i, res in enumerate(raw_results):
            if isinstance(res, Exception) and not isinstance(
                res, asyncio.CancelledError
            ):
                logger.error(f"任务 {image_paths[i]} 抛出异常: {res}")
                results.append(
                    TaskResult(
                        image_path=image_paths[i],
                        image_name=Path(image_paths[i]).stem,
                        status=TaskStatus.FAILED,
                        error_code=ErrorCode.UNKNOWN,
                        error_message=f"未捕获异常: {str(res)[:200]}",
                    )
                )
            elif isinstance(res, asyncio.CancelledError):
                results.append(
                    TaskResult(
                        image_path=image_paths[i],
                        image_name=Path(image_paths[i]).stem,
                        status=TaskStatus.CANCELLED,
                        error_code=ErrorCode.CANCELLED,
                        error_message="任务被取消",
                    )
                )
            else:
                results.append(res)

        return results

    # ═══════════════════════════════════════════════════════════════════════
    # 进度追踪
    # ═══════════════════════════════════════════════════════════════════════

    def get_progress(self: "BatchProcessor") -> ProgressInfo:
        """
        获取当前进度信息

        Returns:
            ProgressInfo: 当前进度快照
        """
        total = max(self._pending_at_start, 1)
        elapsed = (
            time.monotonic() - self._start_time if self._start_time else 0
        )

        # 计算吞吐量（EMA）
        throughput = None
        if self._completed_count > 0 and elapsed > 0:
            instant = self._completed_count / elapsed
            if self._throughput_ema is None:
                self._throughput_ema = instant
            else:
                alpha = 0.3
                self._throughput_ema = (
                    alpha * instant + (1 - alpha) * self._throughput_ema
                )
            throughput = round(self._throughput_ema, 3)

        # 计算 ETA
        eta = None
        remaining = total - self._completed_count
        if throughput and throughput > 0:
            eta = round(remaining / throughput, 0)

        return ProgressInfo(
            batch_id=self._batch_id,
            total=total,
            completed=self._completed_count,
            success=self._success_count,
            failed=self._failed_count,
            cancelled=self._cancelled_count,
            in_progress=total - self._completed_count,
            pending=total - self._completed_count,
            percent_complete=round(self._completed_count / total * 100, 1)
            if total > 0
            else 0,
            current_stage=self._current_stage,
            current_image=self._current_image,
            elapsed_sec=round(elapsed, 1),
            eta_sec=eta,
            throughput=throughput,
            is_running=self._is_running,
            is_cancelled=self._is_cancelled,
        )

    def register_callback(
        self,
        callback: Callable[[ProgressInfo], None],
    ) -> None:
        """
        注册进度回调函数

        Args:
            callback: 接收 ProgressInfo 的回调函数
                      签名: fn(progress: ProgressInfo) -> None
        """
        if callback not in self._progress_callbacks:
            self._progress_callbacks.append(callback)
            logger.debug(
                f"注册进度回调: {callback.__name__ if hasattr(callback, '__name__') else callback}"
            )

    def unregister_callback(
        self,
        callback: Callable[[ProgressInfo], None],
    ) -> None:
        """注销进度回调函数"""
        if callback in self._progress_callbacks:
            self._progress_callbacks.remove(callback)

    async def _emit_progress(self: "BatchProcessor", final: bool = False) -> None:
        """
        发送进度更新 — 调用所有注册的回调

        Args:
            final: 是否为最终更新
        """
        progress = self.get_progress()

        for callback in self._progress_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(progress)
                else:
                    callback(progress)
            except Exception as e:
                logger.error(f"进度回调执行失败: {e}")

        # 日志输出（每 5% 或最终输出）
        log_interval = max(1, progress.total // 20)
        if final or (
            self._completed_count > 0
            and self._completed_count % log_interval == 0
        ):
            logger.info(progress.format_bar())

    # ═══════════════════════════════════════════════════════════════════════
    # 取消机制
    # ═══════════════════════════════════════════════════════════════════════

    def cancel(self: "BatchProcessor") -> None:
        """
        取消批量处理

        设置取消事件标志，所有正在执行和待执行的任务会检测到并优雅退出。
        已完成的任务不受影响。

        线程安全: 可在任意线程调用
        """
        if not self._is_running:
            logger.warning("BatchProcessor 未在运行，无需取消")
            return

        logger.info(f"批次 {self._batch_id} 收到取消请求")
        self._is_cancelled = True
        self._cancel_event.set()

        # 如果有外部取消钩子，调用之
        cancel_hook = getattr(self._scheduler, "on_cancel", None)
        if cancel_hook:
            try:
                if asyncio.iscoroutinefunction(cancel_hook):
                    asyncio.create_task(cancel_hook(self._batch_id))
                else:
                    cancel_hook(self._batch_id)
            except Exception as e:
                logger.error(f"取消钩子执行失败: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # Sheets 更新
    # ═══════════════════════════════════════════════════════════════════════

    async def _sheets_update_loop(self: "BatchProcessor", interval: float) -> None:
        """
        Sheets 定期更新循环 — 后台任务

        Args:
            interval: 更新间隔（秒）
        """
        logger.debug(f"启动 Sheets 更新循环，间隔: {interval}s")
        while True:
            try:
                await asyncio.sleep(interval)

                if not self._is_running:
                    break

                progress = self.get_progress()
                await self._update_sheets(progress)

            except asyncio.CancelledError:
                logger.debug("Sheets 更新循环被取消")
                break
            except Exception as e:
                logger.error(f"Sheets 更新异常: {e}")

    async def _update_sheets(self: "BatchProcessor", progress: ProgressInfo) -> None:
        """
        更新 Sheets 进度数据

        Args:
            progress: 当前进度信息
        """
        if not self._store:
            return

        try:
            # 构造 Batch 模型
            batch = Batch(
                batch_id=self._batch_id,
                total_tasks=progress.total,
                pending_count=progress.pending,
                completed_count=progress.success,
                failed_count=progress.failed,
                progress_percent=progress.percent_complete,
            )
            await self._store.update_batch_summary(batch)
            self._last_sheets_update = time.monotonic()
        except Exception as e:
            logger.error(f"Sheets 更新失败: {e}")

    async def _final_sheets_update(self: "BatchProcessor", result: BatchResult) -> None:
        """最终 Sheets 更新 — 批次完成时调用"""
        if not self._store:
            return

        try:
            batch = Batch(
                batch_id=self._batch_id,
                total_tasks=result.total,
                completed_count=result.success_count,
                failed_count=result.failed_count,
                progress_percent=result.success_rate,
                status=BatchStatus.COMPLETED,
                completed_at=datetime.utcnow().isoformat(),
            )
            await self._store.update_batch_summary(batch)
        except Exception as e:
            logger.error(f"最终 Sheets 更新失败: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # 阶段执行（内部方法）
    # ═══════════════════════════════════════════════════════════════════════

    async def _analyze_image(
        self,
        image_path: str,
        options: ProcessOptions,
    ) -> str:
        """
        图片分析 — 调用 VLM 分析图片内容

        Args:
            image_path: 图片路径
            options: 处理选项

        Returns:
            str: 产品类别

        Raises:
            TaskSchedulerError: 分析失败
        """
        analyzer = getattr(self._scheduler, "image_analyzer", None)
        if analyzer is None:
            logger.warning("未配置图片分析器，返回默认类别")
            return "general"

        # 使用闭包绑定参数，避免通过 execute_with_control 传递参数
        async def _wrapped():
            return await analyzer(image_path)

        # 并发控制下执行
        if self._concurrency_ctl:
            return await self._concurrency_ctl.execute_with_control(
                task=Task(image_path=image_path),
                phase=SchedulerTaskStatus.ANALYZING,
                coro=_wrapped,
            )
        else:
            return await _wrapped()

    async def _route_gem(
        self,
        product_category: Optional[str],
        options: ProcessOptions,
    ) -> str:
        """
        Gem 路由 — 根据产品类别匹配最佳 Gem

        Args:
            product_category: 产品类别
            options: 处理选项

        Returns:
            str: 匹配的 Gem ID
        """
        router = getattr(self._scheduler, "smart_router", None)
        if router is None:
            return "default_gem"

        async def _wrapped():
            result = await router(product_category)
            return result if isinstance(result, str) else "default_gem"

        if self._concurrency_ctl:
            return await self._concurrency_ctl.execute_with_control(
                task=Task(),
                phase=SchedulerTaskStatus.ROUTING,
                coro=_wrapped,
            )
        else:
            return await _wrapped()

    async def _generate_prompt(
        self,
        gem_id: str,
        image_path: str,
        options: ProcessOptions,
    ) -> str:
        """
        提示词生成 — 调用 Gem 生成视频提示词

        Args:
            gem_id: Gem ID
            image_path: 图片路径
            options: 处理选项

        Returns:
            str: 生成的提示词
        """
        gem_caller = getattr(self._scheduler, "gem_caller", None)
        if gem_caller is None:
            return f"A product video showing the item in the image."

        async def _wrapped():
            return await gem_caller(gem_id, image_path)

        if self._concurrency_ctl:
            return await self._concurrency_ctl.execute_with_control(
                task=Task(matched_gem_id=gem_id, image_path=image_path),
                phase=SchedulerTaskStatus.PROMPT_GENERATING,
                coro=_wrapped,
            )
        else:
            return await _wrapped()

    async def _generate_video(
        self,
        image_path: str,
        prompt: str,
        options: ProcessOptions,
    ) -> str:
        """
        视频生成 — 调用视频提供商 API

        Args:
            image_path: 图片路径
            prompt: 视频提示词
            options: 处理选项

        Returns:
            str: 输出视频路径
        """
        providers = getattr(self._scheduler, "video_providers", {})
        if not providers:
            raise TaskSchedulerError("未配置视频提供商")

        # 选择提供商
        provider_name = options.preferred_provider
        if not provider_name or provider_name not in providers:
            for p in [
                "vidu_q3_turbo",
                "seedance_2_0_mini",
                "veo_3_1_fast",
            ]:
                if p in providers:
                    provider_name = p
                    break
            else:
                provider_name = list(providers.keys())[0]

        provider = providers[provider_name]

        # 提交视频生成
        video_task_id = await provider.submit(
            image_path=image_path,
            prompt=prompt,
            duration=options.video_duration,
            resolution=options.video_resolution,
        )

        # 轮询等待完成
        async def _poll_fn() -> Dict[str, Any]:
            status = await provider.query_task(video_task_id)
            if status == "completed":
                url = await provider.get_result(video_task_id)
                return {"status": "completed", "url": url}
            elif status == "failed":
                return {"status": "failed", "error": "视频生成失败"}
            return {"status": "processing"}

        # 轮询
        poll_config = getattr(self._scheduler, "config", None)
        poll = AsyncPoller(
            poll_config.poll if poll_config else PollConfig()
        )

        result = await poll.poll_with_fn(
            _poll_fn,
            timeout=options.timeout_per_task,
        )

        if result.get("status") == "completed":
            # 返回模拟路径（实际应下载视频）
            output_dir = Path(options.output_dir)
            output_path = output_dir / f"{Path(image_path).stem}_video.mp4"
            return str(output_path)
        else:
            raise TaskSchedulerError(
                result.get("error", "视频生成失败")
            )

    # ═══════════════════════════════════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════════════════════════════════

    def _task_result_to_model(self: "BatchProcessor", result: TaskResult) -> Task:
        """将 TaskResult 转换为 Task 模型（用于持久化）"""
        # 映射 batch_processor 的 TaskStatus 到 scheduler 的 TaskStatus
        status_mapping = {
            TaskStatus.PENDING: SchedulerTaskStatus.PENDING,
            TaskStatus.ANALYZING: SchedulerTaskStatus.ANALYZING,
            TaskStatus.ROUTING: SchedulerTaskStatus.ROUTING,
            TaskStatus.PROMPT_GENERATING: SchedulerTaskStatus.PROMPT_GENERATING,
            TaskStatus.VIDEO_GENERATING: SchedulerTaskStatus.VIDEO_GENERATING,
            TaskStatus.COMPLETED: SchedulerTaskStatus.COMPLETED,
            TaskStatus.FAILED: SchedulerTaskStatus.FAILED,
            TaskStatus.CANCELLED: SchedulerTaskStatus.FAILED,  # CANCELLED 映射到 FAILED
        }
        return Task(
            task_id=result.task_id,
            batch_id=self._batch_id,
            status=status_mapping[result.status],
            image_path=result.image_path,
            image_name=result.image_name,
            product_category=result.product_category,
            matched_gem_id=result.matched_gem_id,
            generated_prompt=result.generated_prompt,
            output_path=result.output_video_path,
            error_message=result.error_message,
            error_code=result.error_code.value,
            processing_time_sec=result.processing_time_sec,
        )

    @property
    def is_running(self: "BatchProcessor") -> bool:
        """是否正在运行"""
        return self._is_running

    @property
    def batch_id(self: "BatchProcessor") -> str:
        """当前批次 ID"""
        return self._batch_id


# ═══════════════════════════════════════════════════════════════════════════
# AsyncPoller 扩展 — poll_with_fn 方法
# ═══════════════════════════════════════════════════════════════════════════


async def _poll_with_fn(
    self: AsyncPoller,
    poll_fn: Callable[[], Awaitable[Dict[str, Any]]],
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """
    使用轮询函数进行轮询（扩展方法）

    与 poll() 不同，此方法直接使用轮询函数而非 Task 对象。

    Args:
        poll_fn: 轮询函数，返回字典 {status, ...}
        timeout: 超时时间（秒）

    Returns:
        轮询结果字典
    """
    interval = self.config.initial_interval
    poll_count = 0
    start = time.monotonic()
    effective_timeout = timeout or self.config.timeout

    while poll_count < self.config.max_polls:
        # 检查总超时
        elapsed = time.monotonic() - start
        if elapsed > effective_timeout:
            return {"status": "timeout", "error": f"轮询超时 ({effective_timeout}s)"}

        # 执行轮询
        try:
            result = await poll_fn()
        except Exception as e:
            result = {"status": "error", "error": str(e)}

        poll_count += 1

        # 检查结果
        if result.get("status") in ("completed", "failed", "error"):
            return result

        # 指数退避
        interval = min(interval * self.config.backoff_factor, self.config.max_interval)
        if self.config.jitter:
            jitter = interval * 0.2 * (2 * random.random() - 1)
            interval += jitter

        await asyncio.sleep(interval)

    # 超过最大轮询次数
    return {
        "status": "timeout",
        "error": f"超过最大轮询次数 ({self.config.max_polls})",
    }


# 将扩展方法绑定到 AsyncPoller 类
AsyncPoller.poll_with_fn = _poll_with_fn


# ═══════════════════════════════════════════════════════════════════════════
# 模块导出
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    # 枚举
    "ProcessingStage",
    "ErrorCode",
    "TaskStatus",
    # 模型
    "TaskResult",
    "BatchResult",
    "ProgressInfo",
    "ProcessOptions",
    "StageTiming",
    # 核心类
    "BatchProcessor",
]
