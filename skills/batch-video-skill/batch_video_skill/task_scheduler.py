"""
═══════════════════════════════════════════════════════════════════════════════
任务调度模块 (Task Scheduler) — 完整实现
═══════════════════════════════════════════════════════════════════════════════

职责:
  1. 任务状态机管理 — 7 个状态、12 个合法转换
  2. 并发控制 — 四层信号量（全局/阶段/提供商/重试）
  3. 断点续跑 — 崩溃恢复、一致性检查、自动持久化
  4. 进度追踪 — 实时统计、ETA 计算、回调通知
  5. 批次管理 — 任务提交、执行、暂停、恢复
  6. 异步轮询 — 视频生成状态轮询、指数退避
  7. 存储接口 — Google Sheets 抽象层

设计模式: 门面模式 (Facade) + 依赖注入 (DI)

Author: 300 Agent 融合项目
Version: 1.0
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import (
    Any, Awaitable, Callable, Dict, Generic, List, Optional, Tuple, TypeVar, Union,
)

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 枚举定义
# ═══════════════════════════════════════════════════════════════════════════


class TaskStatus(str, Enum):
    """
    任务状态枚举 — 7 个核心状态

    状态流转:
        PENDING -> ANALYZING -> ROUTING -> PROMPT_GENERATING -> VIDEO_GENERATING -> COMPLETED
          ^                                                                     |
          +----------------------------- FAILED <--------------------------------+

    设计原则:
      - 每个执行中状态对应一个处理阶段，便于阶段级并发控制
      - 终止状态只有 2 个 (COMPLETED/FAILED)，简化判断逻辑
    """

    PENDING = "pending"
    ANALYZING = "analyzing"
    ROUTING = "routing"
    PROMPT_GENERATING = "prompt_generating"
    VIDEO_GENERATING = "video_generating"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskAction(str, Enum):
    """
    可执行的操作 — 触发状态转换

    每个动作对应一个明确的业务事件，所有状态变更必须通过动作触发。
    """

    START = "start"
    ANALYZE_DONE = "analyze_done"
    ROUTE_DONE = "route_done"
    PROMPT_DONE = "prompt_done"
    SUBMIT_VIDEO = "submit_video"
    VIDEO_DONE = "video_done"
    FAIL = "fail"
    RETRY = "retry"
    CANCEL = "cancel"


class BatchStatus(str, Enum):
    """批次状态 — 描述整个批次的生命周期"""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class RecoveryActionType(str, Enum):
    """恢复决策类型"""

    RESUME = "resume"
    REPAIR = "repair"
    DISCARD = "discard"


# ═══════════════════════════════════════════════════════════════════════════
# 异常体系
# ═══════════════════════════════════════════════════════════════════════════


class TaskSchedulerError(Exception):
    """
    任务调度模块基础异常

    所有模块异常的基类，调用方可通过捕获此异常处理所有模块错误。

    Attributes:
        message: 错误信息
        error_code: 标准化错误码
        task_id: 关联的任务ID（可选）
        batch_id: 关联的批次ID（可选）
    """

    def __init__(
        self,
        message: str = "",
        error_code: str = "SCHEDULER_ERROR",
        task_id: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.task_id = task_id
        self.batch_id = batch_id

    def __str__(self: "TaskSchedulerError") -> str:
        parts = [f"[{self.error_code}]"]
        if self.task_id:
            parts.append(f"task={self.task_id}")
        if self.batch_id:
            parts.append(f"batch={self.batch_id}")
        parts.append(self.message)
        return " ".join(parts)


class InvalidTransitionError(TaskSchedulerError):
    """非法状态转换异常"""

    def __init__(self: "InvalidTransitionError", message: str = "", task_id: Optional[str] = None) -> None:
        super().__init__(
            message=message,
            error_code="INVALID_TRANSITION",
            task_id=task_id,
        )


class StoreError(TaskSchedulerError):
    """持久化存储异常"""

    def __init__(
        self,
        message: str = "",
        task_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(
            message=message,
            error_code="STORE_ERROR",
            task_id=task_id,
            batch_id=batch_id,
        )
        self.retryable = retryable


class ConcurrencyError(TaskSchedulerError):
    """并发控制异常"""

    def __init__(self: "ConcurrencyError", message: str = "", task_id: Optional[str] = None) -> None:
        super().__init__(
            message=message,
            error_code="CONCURRENCY_ERROR",
            task_id=task_id,
        )


class CheckpointError(TaskSchedulerError):
    """断点续跑异常"""

    def __init__(self: "CheckpointError", message: str = "", batch_id: Optional[str] = None) -> None:
        super().__init__(
            message=message,
            error_code="CHECKPOINT_ERROR",
            batch_id=batch_id,
        )


class PollTimeoutError(TaskSchedulerError):
    """轮询超时异常"""

    def __init__(
        self,
        message: str = "轮询超时",
        task_id: Optional[str] = None,
        poll_count: int = 0,
    ) -> None:
        super().__init__(
            message=message,
            error_code="POLL_TIMEOUT",
            task_id=task_id,
        )
        self.poll_count = poll_count


class ProviderError(TaskSchedulerError):
    """视频提供商异常"""

    def __init__(
        self,
        message: str = "",
        provider: str = "",
        task_id: Optional[str] = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(
            message=message,
            error_code="PROVIDER_ERROR",
            task_id=task_id,
        )
        self.provider = provider
        self.retryable = retryable


class BatchNotFoundError(TaskSchedulerError):
    """批次不存在异常"""

    def __init__(self: "BatchNotFoundError", batch_id: str) -> None:
        super().__init__(
            message=f"批次不存在: {batch_id}",
            error_code="BATCH_NOT_FOUND",
            batch_id=batch_id,
        )


class TaskNotFoundError(TaskSchedulerError):
    """任务不存在异常"""

    def __init__(self: "TaskNotFoundError", task_id: str) -> None:
        super().__init__(
            message=f"任务不存在: {task_id}",
            error_code="TASK_NOT_FOUND",
            task_id=task_id,
        )


# ═══════════════════════════════════════════════════════════════════════════
# 核心数据模型
# ═══════════════════════════════════════════════════════════════════════════


class TransitionRecord(BaseModel):
    """
    单次状态转换记录（用于审计和历史追踪）

    每次状态转换自动生成一条记录，不可变（frozen）。
    """

    model_config = ConfigDict(frozen=True)

    from_status: str
    to_status: str
    action: str
    reason: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class Task(BaseModel):
    """
    任务数据模型 — 对应 task-queue 表的一行数据

    所有字段变更必须通过状态机驱动，禁止直接修改 status 字段。

    Attributes:
        task_id: 任务唯一标识（8位短UUID）
        batch_id: 所属批次ID
        status: 当前状态（只能通过 StateMachine 修改）
        image_path: 产品图片本地路径
        image_name: 图片文件名（不含扩展名）
        product_category: VLM 分析出的产品类别
        matched_gem_id: 匹配的 Gem 模板ID
        matched_gem_name: 匹配的 Gem 模板名称
        match_score: 匹配分数 (0-1)
        generated_prompt: Gem 生成的视频提示词
        video_provider: 视频提供商名称
        video_model: 视频模型版本
        video_task_id: 视频 API 返回的任务ID
        video_url: 生成视频的下载URL
        output_path: 本地输出路径
        error_message: 错误信息
        error_code: 统一错误码
        retry_count: 当前重试次数
        max_retries: 最大重试次数
        created_at: 创建时间（ISO格式）
        started_at: 开始处理时间
        completed_at: 完成时间
        status_changed_at: 最后一次状态变更时间
        processing_time_sec: 总处理耗时（秒）
        transition_history: 状态转换历史记录
    """

    model_config = ConfigDict(validate_assignment=True)

    task_id: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:8], description="任务唯一标识（8位短UUID）"
    )
    batch_id: str = Field(default="", description="所属批次ID")
    status: TaskStatus = Field(
        default=TaskStatus.PENDING, description="当前状态 — 只能通过 StateMachine 修改"
    )
    image_path: str = Field(default="", description="产品图片本地路径")
    image_name: str = Field(default="", description="图片文件名（不含扩展名）")
    product_category: Optional[str] = Field(
        default=None, description="VLM 分析出的产品类别"
    )
    matched_gem_id: Optional[str] = Field(default=None, description="匹配的 Gem 模板ID")
    matched_gem_name: Optional[str] = Field(
        default=None, description="匹配的 Gem 模板名称"
    )
    match_score: Optional[float] = Field(default=None, description="匹配分数 (0-1)")
    generated_prompt: Optional[str] = Field(
        default=None, description="Gem 生成的视频提示词"
    )
    video_provider: Optional[str] = Field(default=None, description="视频提供商名称")
    video_model: Optional[str] = Field(default=None, description="视频模型版本")
    video_task_id: Optional[str] = Field(
        default=None, description="视频 API 返回的任务ID"
    )
    video_url: Optional[str] = Field(default=None, description="生成视频的下载URL")
    output_path: Optional[str] = Field(default=None, description="本地输出路径")
    error_message: Optional[str] = Field(default=None, description="错误信息")
    error_code: Optional[str] = Field(default=None, description="统一错误码")
    retry_count: int = Field(default=0, ge=0, description="当前重试次数")
    max_retries: int = Field(default=3, ge=0, le=10, description="最大重试次数")
    created_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="创建时间（ISO格式）",
    )
    started_at: Optional[str] = Field(default=None, description="开始处理时间")
    completed_at: Optional[str] = Field(default=None, description="完成时间")
    status_changed_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="最后一次状态变更时间",
    )
    processing_time_sec: Optional[float] = Field(
        default=None, description="总处理耗时（秒）"
    )
    transition_history: List[Dict[str, Any]] = Field(
        default_factory=list, description="状态转换历史记录"
    )

    def is_terminal(self: "Task") -> bool:
        """是否为终止状态（COMPLETED 或 FAILED）"""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)

    def can_retry(self: "Task") -> bool:
        """是否还可以重试（状态为 FAILED 且重试次数未超限）"""
        return self.status == TaskStatus.FAILED and self.retry_count < self.max_retries

    def add_transition(self: "Task", record: TransitionRecord) -> None:
        """添加转换记录到历史"""
        self.transition_history.append(record.model_dump())
        self.status_changed_at = record.timestamp

    def to_row(self: "Task") -> List[Any]:
        """转换为 Sheets 行数据"""
        return [
            self.task_id,
            self.batch_id,
            self.status.value,
            self.image_path,
            self.image_name,
            self.product_category,
            self.matched_gem_id,
            self.matched_gem_name,
            self.match_score,
            self.generated_prompt,
            self.video_provider,
            self.video_model,
            self.video_task_id,
            self.video_url,
            self.output_path,
            self.error_message,
            self.error_code,
            self.retry_count,
            self.max_retries,
            self.created_at,
            self.started_at,
            self.completed_at,
            self.status_changed_at,
            self.processing_time_sec,
            str(self.transition_history) if self.transition_history else "",
        ]

    @classmethod
    def from_row(cls: type["Task"], row: List[Any]) -> Task:
        """从 Sheets 行数据创建 Task"""
        return cls(
            task_id=str(row[0]) if len(row) > 0 else "",
            batch_id=str(row[1]) if len(row) > 1 else "",
            status=TaskStatus(str(row[2])) if len(row) > 2 else TaskStatus.PENDING,
            image_path=str(row[3]) if len(row) > 3 else "",
            image_name=str(row[4]) if len(row) > 4 else "",
            product_category=row[5] if len(row) > 5 else None,
            matched_gem_id=row[6] if len(row) > 6 else None,
            matched_gem_name=row[7] if len(row) > 7 else None,
            match_score=float(row[8]) if len(row) > 8 and row[8] else None,
            generated_prompt=row[9] if len(row) > 9 else None,
            video_provider=row[10] if len(row) > 10 else None,
            video_model=row[11] if len(row) > 11 else None,
            video_task_id=row[12] if len(row) > 12 else None,
            video_url=row[13] if len(row) > 13 else None,
            output_path=row[14] if len(row) > 14 else None,
            error_message=row[15] if len(row) > 15 else None,
            error_code=row[16] if len(row) > 16 else None,
            retry_count=int(row[17]) if len(row) > 17 and row[17] else 0,
            max_retries=int(row[18]) if len(row) > 18 and row[18] else 3,
            created_at=str(row[19]) if len(row) > 19 else datetime.utcnow().isoformat(),
            started_at=row[20] if len(row) > 20 else None,
            completed_at=row[21] if len(row) > 21 else None,
            status_changed_at=str(row[22])
            if len(row) > 22
            else datetime.utcnow().isoformat(),
            processing_time_sec=float(row[23]) if len(row) > 23 and row[23] else None,
        )


class Batch(BaseModel):
    """
    批次数据模型 — 对应 batch-summary 表的一行数据

    Attributes:
        batch_id: 批次唯一标识（12位短UUID）
        batch_name: 批次名称（用户可自定义）
        total_tasks: 任务总数
        pending_count: 待处理任务数
        analyzing_count: 分析中任务数
        routing_count: 路由中任务数
        prompt_generating_count: 提示词生成中任务数
        video_generating_count: 视频生成中任务数
        completed_count: 已完成任务数
        failed_count: 已失败任务数
        retry_count: 重试次数统计
        progress_percent: 进度百分比 (0-100)
        created_at: 创建时间
        started_at: 开始执行时间
        completed_at: 完成时间
        total_time_sec: 总耗时（秒）
        config: 批次配置（JSON序列化）
        status: 批次状态
    """

    model_config = ConfigDict(validate_assignment=True)

    batch_id: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:12],
        description="批次唯一标识（12位短UUID）",
    )
    batch_name: str = Field(default="未命名批次", description="批次名称")
    total_tasks: int = Field(default=0, ge=0, description="任务总数")
    pending_count: int = Field(default=0, description="待处理任务数")
    analyzing_count: int = Field(default=0, description="分析中任务数")
    routing_count: int = Field(default=0, description="路由中任务数")
    prompt_generating_count: int = Field(default=0, description="提示词生成中任务数")
    video_generating_count: int = Field(default=0, description="视频生成中任务数")
    completed_count: int = Field(default=0, description="已完成任务数")
    failed_count: int = Field(default=0, description="已失败任务数")
    retry_count: int = Field(default=0, description="重试次数统计")
    progress_percent: float = Field(
        default=0.0, ge=0.0, le=100.0, description="进度百分比 (0-100)"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(), description="创建时间"
    )
    started_at: Optional[str] = Field(default=None, description="开始执行时间")
    completed_at: Optional[str] = Field(default=None, description="完成时间")
    total_time_sec: Optional[float] = Field(default=None, description="总耗时（秒）")
    config: Dict[str, Any] = Field(default_factory=dict, description="批次配置")
    status: BatchStatus = Field(default=BatchStatus.CREATED, description="批次状态")

    def update_progress(self: "Batch") -> None:
        """根据各状态计数更新进度百分比"""
        if self.total_tasks > 0:
            self.progress_percent = round(
                self.completed_count / self.total_tasks * 100, 1
            )

    def is_complete(self: "Batch") -> bool:
        """批次是否已完成（所有任务到达终止状态）"""
        return (self.completed_count + self.failed_count) >= self.total_tasks

    def to_row(self: "Batch") -> List[Any]:
        """转换为 Sheets 行数据"""
        return [
            self.batch_id,
            self.batch_name,
            self.total_tasks,
            self.pending_count,
            self.analyzing_count,
            self.routing_count,
            self.prompt_generating_count,
            self.video_generating_count,
            self.completed_count,
            self.failed_count,
            self.retry_count,
            self.progress_percent,
            self.created_at,
            self.started_at,
            self.completed_at,
            self.total_time_sec,
            str(self.config),
            self.status.value,
        ]


class ProgressSnapshot(BaseModel):
    """
    进度快照 — 某一时刻的完整进度视图

    不可变对象，每次获取快照时重新创建。
    """

    model_config = ConfigDict(frozen=True)

    batch_id: str = Field(description="批次ID")
    timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(), description="快照时间"
    )
    total: int = Field(description="任务总数")
    by_status: Dict[str, int] = Field(description="各状态计数")
    completed: int = Field(description="已完成数")
    failed: int = Field(description="已失败数")
    in_progress: int = Field(description="执行中数（非终止非待处理）")
    percent: float = Field(description="进度百分比 (0-100)")
    eta_seconds: Optional[float] = Field(default=None, description="预计剩余时间（秒）")
    throughput: Optional[float] = Field(default=None, description="每秒完成数")


class ResumeResult(BaseModel):
    """
    断点续跑恢复结果

    恢复流程完成后返回，包含可恢复任务列表和修复统计。
    """

    model_config = ConfigDict(frozen=True)

    batch_id: str = Field(description="批次ID")
    resumable_tasks: List[Task] = Field(
        default_factory=list, description="可恢复执行的任务列表"
    )
    repaired_count: int = Field(default=0, description="已修复（重置状态）的任务数")
    discarded_count: int = Field(default=0, description="已丢弃（超最大重试）的任务数")
    total_incomplete: int = Field(default=0, description="扫描到的未完成任务总数")

    @property
    def resumable_count(self: "ResumeResult") -> int:
        """可恢复任务数量"""
        return len(self.resumable_tasks)


class RecoveryAction(BaseModel):
    """
    单个任务的恢复决策

    由 CheckpointManager._recover_task() 生成，指导恢复流程。
    """

    model_config = ConfigDict(frozen=True)

    action: RecoveryActionType = Field(description="恢复动作类型")
    reason: str = Field(default="", description="决策原因")
    old_status: Optional[str] = Field(default=None, description="原状态")
    new_status: Optional[str] = Field(default=None, description="新状态（仅 repair 有效）")


# ═══════════════════════════════════════════════════════════════════════════
# 配置模型
# ═══════════════════════════════════════════════════════════════════════════


class ConcurrencyConfig(BaseModel):
    """并发控制配置 — 定义四层并发控制的参数"""

    global_max_workers: int = Field(
        default=3, ge=1, le=20, description="全局最大并发工作者数"
    )
    max_analyzing: int = Field(
        default=5, ge=1, le=50, description="图片分析阶段并发上限"
    )
    max_routing: int = Field(
        default=10, ge=1, le=50, description="路由阶段并发上限"
    )
    max_prompt_generating: int = Field(
        default=3, ge=1, le=20, description="提示词生成并发上限（受 Gemini API 限制）"
    )
    max_video_generating: int = Field(
        default=5, ge=1, le=20, description="视频提交并发上限"
    )
    max_polling: int = Field(
        default=10, ge=1, le=50, description="最大并行轮询数"
    )
    provider_limits: Dict[str, List[int]] = Field(
        default_factory=lambda: {
            "kling_3_0": [2, 60],
            "kling_3_0_turbo": [2, 60],
            "seedance_2_0": [2, 60],
            "seedance_2_0_mini": [3, 120],
            "veo_3_1_fast": [3, 120],
            "veo_3_1_standard": [2, 60],
            "vidu_q3_pro": [5, 300],
            "vidu_q3_turbo": [5, 300],
            "runway_gen4": [2, 30],
            "pika_2_2": [3, 60],
        },
        description="提供商限流配置: {name: [max_concurrent, max_rpm]}",
    )
    max_retries: int = Field(default=3, ge=0, le=10, description="最大重试次数")
    retry_base_delay: float = Field(default=2.0, gt=0, description="重试基础延迟（秒）")
    retry_max_delay: float = Field(default=60.0, gt=0, description="重试最大延迟（秒）")


class PollConfig(BaseModel):
    """异步轮询配置"""

    initial_interval: float = Field(default=2.0, gt=0, description="初始轮询间隔（秒）")
    backoff_factor: float = Field(default=1.5, gt=1.0, description="退避因子")
    max_interval: float = Field(default=60.0, gt=0, description="最大轮询间隔（秒）")
    max_polls: int = Field(default=120, ge=1, description="最大轮询次数")
    timeout: float = Field(default=600.0, gt=0, description="总超时（秒）")
    jitter: bool = Field(default=True, description="启用抖动避免同步")


class CheckpointConfig(BaseModel):
    """断点续跑配置"""

    persist_interval_sec: float = Field(
        default=5.0, gt=0, description="自动持久化间隔（秒）"
    )
    state_max_age_sec: float = Field(
        default=300.0, gt=0, description="状态最大存活时间（超时 2 倍后视为崩溃）"
    )
    enable_auto_persist: bool = Field(default=True, description="启用自动持久化")


class SchedulerConfig(BaseModel):
    """调度器总配置"""

    concurrency: ConcurrencyConfig = Field(
        default_factory=ConcurrencyConfig, description="并发控制配置"
    )
    poll: PollConfig = Field(default_factory=PollConfig, description="轮询配置")
    checkpoint: CheckpointConfig = Field(
        default_factory=CheckpointConfig, description="断点续跑配置"
    )
    spreadsheet_id: str = Field(default="", description="Google Sheets 表格ID")
    enable_progress_tracking: bool = Field(
        default=True, description="启用进度追踪"
    )
    enable_adaptive_concurrency: bool = Field(
        default=False, description="启用自适应并发（P2功能）"
    )


class PollResult(BaseModel):
    """轮询结果"""

    model_config = ConfigDict(frozen=True)

    status: str = Field(
        description="轮询状态: completed / failed / processing / timeout / error"
    )
    data: Dict[str, Any] = Field(default_factory=dict, description="附加数据")
    error: Optional[str] = Field(default=None, description="错误信息")
    video_url: Optional[str] = Field(default=None, description="视频下载URL")


# ═══════════════════════════════════════════════════════════════════════════
# 事件模型
# ═══════════════════════════════════════════════════════════════════════════


class TaskEvent(BaseModel):
    """任务状态变更事件"""

    model_config = ConfigDict(frozen=True)

    event_type: str = Field(
        description="事件类型: status_changed / progress_updated / error"
    )
    task_id: str = Field(description="任务ID")
    batch_id: str = Field(description="批次ID")
    old_status: Optional[str] = Field(default=None, description="原状态")
    new_status: Optional[str] = Field(default=None, description="新状态")
    timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(), description="事件时间"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="附加元数据")


class BatchEvent(BaseModel):
    """批次级别事件"""

    model_config = ConfigDict(frozen=True)

    event_type: str = Field(
        description="事件类型: started / paused / resumed / completed"
    )
    batch_id: str = Field(description="批次ID")
    timestamp: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(), description="事件时间"
    )
    snapshot: Optional[ProgressSnapshot] = Field(default=None, description="进度快照")


# ═══════════════════════════════════════════════════════════════════════════
# 存储接口
# ═══════════════════════════════════════════════════════════════════════════


class ITaskStore(ABC):
    """
    任务存储抽象接口

    所有持久化操作通过此接口进行，便于:
      - 单元测试时注入 Mock 实现
      - 未来替换为其他存储（如 SQLite、PostgreSQL）
    """

    @abstractmethod
    async def init_task_sheet(self: "ITaskStore") -> bool:
        """初始化任务表（创建表头）"""
        pass

    @abstractmethod
    async def add_tasks(self: "ITaskStore", tasks: List[Task]) -> int:
        """批量添加任务，返回写入数量"""
        pass

    @abstractmethod
    async def update_task(self: "ITaskStore", task: Task) -> bool:
        """更新单个任务"""
        pass

    @abstractmethod
    async def get_task(self: "ITaskStore", task_id: str) -> Optional[Task]:
        """按 ID 获取任务"""
        pass

    @abstractmethod
    async def get_tasks_by_batch(self: "ITaskStore", batch_id: str) -> List[Task]:
        """获取批次下所有任务"""
        pass

    @abstractmethod
    async def get_incomplete_tasks(self: "ITaskStore", batch_id: str) -> List[Task]:
        """获取未完成任务（用于断点续跑）"""
        pass

    @abstractmethod
    async def update_batch_summary(self: "ITaskStore", batch: Batch) -> bool:
        """更新批次汇总"""
        pass

    @abstractmethod
    async def get_batch(self: "ITaskStore", batch_id: str) -> Optional[Batch]:
        """获取批次信息"""
        pass

    @abstractmethod
    async def batch_update_cells(self: "ITaskStore", updates: List[Dict[str, Any]]) -> bool:
        """批量更新单元格"""
        pass


# ═══════════════════════════════════════════════════════════════════════════
# 状态机引擎 (StateMachine)
# ═══════════════════════════════════════════════════════════════════════════

# 类型别名
TransitionKey = Tuple[TaskStatus, TaskAction]
TransitionMap = Dict[TransitionKey, TaskStatus]
HookList = List[Callable[[Task, TaskStatus, TaskStatus, TaskAction], None]]


class StateMachine:
    """
    任务状态机引擎

    管理 7 个状态之间的 12 个合法转换。
    所有状态变更必须通过 transition() 方法执行。

    Attributes:
        TRANSITIONS: 类级别转换规则字典
        TIMEOUTS: 各状态超时时间（秒），None 表示无超时

    Example:
        >>> sm = StateMachine()
        >>> task = Task()
        >>> sm.transition(task, TaskAction.START)
        TaskStatus.ANALYZING
    """

    # 转换规则定义: (当前状态, 动作) -> 目标状态
    TRANSITIONS: TransitionMap = {
        # PENDING 状态
        (TaskStatus.PENDING, TaskAction.START): TaskStatus.ANALYZING,
        (TaskStatus.PENDING, TaskAction.FAIL): TaskStatus.FAILED,
        (TaskStatus.PENDING, TaskAction.CANCEL): TaskStatus.FAILED,
        # ANALYZING 状态
        (TaskStatus.ANALYZING, TaskAction.ANALYZE_DONE): TaskStatus.ROUTING,
        (TaskStatus.ANALYZING, TaskAction.FAIL): TaskStatus.FAILED,
        (TaskStatus.ANALYZING, TaskAction.CANCEL): TaskStatus.FAILED,
        # ROUTING 状态
        (TaskStatus.ROUTING, TaskAction.ROUTE_DONE): TaskStatus.PROMPT_GENERATING,
        (TaskStatus.ROUTING, TaskAction.FAIL): TaskStatus.FAILED,
        (TaskStatus.ROUTING, TaskAction.CANCEL): TaskStatus.FAILED,
        # PROMPT_GENERATING 状态
        (
            TaskStatus.PROMPT_GENERATING,
            TaskAction.PROMPT_DONE,
        ): TaskStatus.VIDEO_GENERATING,
        (TaskStatus.PROMPT_GENERATING, TaskAction.FAIL): TaskStatus.FAILED,
        (TaskStatus.PROMPT_GENERATING, TaskAction.CANCEL): TaskStatus.FAILED,
        # VIDEO_GENERATING 状态
        (TaskStatus.VIDEO_GENERATING, TaskAction.VIDEO_DONE): TaskStatus.COMPLETED,
        (TaskStatus.VIDEO_GENERATING, TaskAction.FAIL): TaskStatus.FAILED,
        (TaskStatus.VIDEO_GENERATING, TaskAction.CANCEL): TaskStatus.FAILED,
        # FAILED 状态（唯一可以重试）
        (TaskStatus.FAILED, TaskAction.RETRY): TaskStatus.PENDING,
    }

    # 各状态超时时间（秒），None 表示无超时
    TIMEOUTS: Dict[TaskStatus, Optional[int]] = {
        TaskStatus.PENDING: None,
        TaskStatus.ANALYZING: 120,
        TaskStatus.ROUTING: 60,
        TaskStatus.PROMPT_GENERATING: 180,
        TaskStatus.VIDEO_GENERATING: 600,
        TaskStatus.COMPLETED: None,
        TaskStatus.FAILED: None,
    }

    def __init__(self: "StateMachine") -> None:
        """初始化状态机，创建钩子注册表"""
        # 进入钩子: {目标状态: [回调函数列表]}
        self._on_enter: Dict[TaskStatus, HookList] = {s: [] for s in TaskStatus}
        # 退出钩子: {源状态: [回调函数列表]}
        self._on_exit: Dict[TaskStatus, HookList] = {s: [] for s in TaskStatus}

    # ── 核心转换方法 ──

    def can_transition(self: "StateMachine", current: TaskStatus, action: TaskAction) -> bool:
        """
        检查转换是否合法

        Args:
            current: 当前状态
            action: 欲执行的动作

        Returns:
            True 如果转换合法，False 否则
        """
        return (current, action) in self.TRANSITIONS

    def get_next_state(self: "StateMachine", current: TaskStatus, action: TaskAction) -> TaskStatus:
        """
        获取转换后的目标状态

        Args:
            current: 当前状态
            action: 欲执行的动作

        Returns:
            目标状态

        Raises:
            InvalidTransitionError: 如果转换不合法
        """
        key: TransitionKey = (current, action)
        if key not in self.TRANSITIONS:
            raise InvalidTransitionError(
                message=f"非法状态转换: {current.value} + {action.value} -> ?",
            )
        return self.TRANSITIONS[key]

    def transition(
        self,
        task: Task,
        action: TaskAction,
        reason: str = "",
    ) -> TaskStatus:
        """
        执行状态转换 — 唯一合法的状态变更入口

        执行流程:
            1. 验证转换合法性
            2. 查询目标状态
            3. 执行退出钩子（旧状态）
            4. 修改 task.status
            5. 记录转换历史
            6. 执行进入钩子（新状态）

        Args:
            task: 要转换状态的任务
            action: 触发转换的动作
            reason: 转换原因（用于审计日志）

        Returns:
            转换后的新状态

        Raises:
            InvalidTransitionError: 如果转换不合法
        """
        current = task.status

        # 步骤 1: 验证
        if not self.can_transition(current, action):
            raise InvalidTransitionError(
                message=f"任务 {task.task_id}: 无法从 {current.value} 执行 {action.value}",
                task_id=task.task_id,
            )

        next_state = self.get_next_state(current, action)

        # 步骤 2: 执行退出钩子
        for hook in self._on_exit.get(current, []):
            try:
                hook(task, current, next_state, action)
            except Exception as e:
                logger.warning(f"退出钩子执行失败: {e}")

        # 步骤 3: 执行转换
        old_status = task.status
        task.status = next_state

        # 步骤 4: 记录历史
        record = TransitionRecord(
            from_status=old_status.value,
            to_status=next_state.value,
            action=action.value,
            reason=reason,
        )
        task.add_transition(record)

        logger.debug(
            f"任务 {task.task_id}: {old_status.value} -> {next_state.value} "
            f"(动作: {action.value}, 原因: {reason})"
        )

        # 步骤 5: 执行进入钩子
        for hook in self._on_enter.get(next_state, []):
            try:
                hook(task, old_status, next_state, action)
            except Exception as e:
                logger.warning(f"进入钩子执行失败: {e}")

        return next_state

    # ── 钩子注册（装饰器模式）──

    def on_enter(self: "StateMachine", status: TaskStatus) -> Callable:
        """
        注册进入状态钩子（装饰器）

        当任务进入指定状态时，回调函数被触发。

        Args:
            status: 要监听的目标状态

        Returns:
            装饰器函数
        """

        def decorator(
            fn: Callable[[Task, TaskStatus, TaskStatus, TaskAction], None]
        ) -> Callable:
            self._on_enter[status].append(fn)
            return fn

        return decorator

    def on_exit(self: "StateMachine", status: TaskStatus) -> Callable:
        """
        注册退出状态钩子（装饰器）

        当任务离开指定状态时，回调函数被触发。

        Args:
            status: 要监听的源状态

        Returns:
            装饰器函数
        """

        def decorator(
            fn: Callable[[Task, TaskStatus, TaskStatus, TaskAction], None]
        ) -> Callable:
            self._on_exit[status].append(fn)
            return fn

        return decorator

    # ── 查询方法 ──

    def get_timeout(self: "StateMachine", status: TaskStatus) -> Optional[int]:
        """获取状态的自动超时时间"""
        return self.TIMEOUTS.get(status)

    def is_terminal(self: "StateMachine", status: TaskStatus) -> bool:
        """判断是否为终止状态"""
        return status in (TaskStatus.COMPLETED, TaskStatus.FAILED)

    def is_active(self: "StateMachine", status: TaskStatus) -> bool:
        """
        判断是否为执行中状态（非初始非终止）

        Returns:
            True 如果是 ANALYZING / ROUTING / PROMPT_GENERATING / VIDEO_GENERATING
        """
        return status in (
            TaskStatus.ANALYZING,
            TaskStatus.ROUTING,
            TaskStatus.PROMPT_GENERATING,
            TaskStatus.VIDEO_GENERATING,
        )

    def get_valid_actions(self: "StateMachine", status: TaskStatus) -> List[TaskAction]:
        """获取当前状态下所有合法动作"""
        return [action for (s, action) in self.TRANSITIONS.keys() if s == status]

    @classmethod
    def get_transition_summary(cls: type["StateMachine"]) -> Dict[str, List[str]]:
        """获取转换规则摘要（用于文档和调试）"""
        summary: Dict[str, List[str]] = {}
        for (status, action), target in cls.TRANSITIONS.items():
            if status.value not in summary:
                summary[status.value] = []
            summary[status.value].append(f"{action.value} -> {target.value}")
        return summary


# ═══════════════════════════════════════════════════════════════════════════
# 并发控制器 (ConcurrencyController)
# ═══════════════════════════════════════════════════════════════════════════


class ProviderRateLimiter:
    """
    提供商速率限制器 — 信号量 + 令牌桶组合

    同时管理:
      - 并发请求数（asyncio.Semaphore）
      - 每分钟请求数（Token Bucket 算法）

    使用 async context manager 模式:
        async with limiter:
            # 已获取信号量和令牌
            await call_api()
    """

    def __init__(self: "ProviderRateLimiter", name: str, max_concurrent: int, max_requests_per_minute: int) -> None:
        self.name = name
        self.max_concurrent = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._rpm = max_requests_per_minute
        self._tokens = float(max_requests_per_minute)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self: "ProviderRateLimiter") -> None:
        """
        获取执行许可（信号量 + 令牌）

        流程:
            1. 先获取信号量（并发限制）
            2. 再获取令牌（速率限制）
            3. 无可用令牌时等待
        """
        # 步骤 1: 获取信号量（可能阻塞）
        await self._sem.acquire()

        # 步骤 2: 获取令牌
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill

            # 补充令牌: 按时间比例补充
            refill_rate = self._rpm / 60.0
            self._tokens = min(self._rpm, self._tokens + elapsed * refill_rate)
            self._last_refill = now

            if self._tokens < 1:
                # 令牌不足，计算等待时间
                wait_time = (1 - self._tokens) / refill_rate
                logger.debug(
                    f"提供商 {self.name} 令牌不足，等待 {wait_time:.2f}s"
                )
                await asyncio.sleep(wait_time)
                self._tokens = 0
            else:
                self._tokens -= 1

    def release(self: "ProviderRateLimiter") -> None:
        """释放信号量（令牌不释放，自然补充）"""
        self._sem.release()

    async def __aenter__(self: "ProviderRateLimiter") -> ProviderRateLimiter:
        await self.acquire()
        return self

    async def __aexit__(self: "ProviderRateLimiter", exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()

    def __repr__(self: "ProviderRateLimiter") -> str:
        return f"ProviderRateLimiter({self.name})"


T = TypeVar("T")


class ConcurrencyController:
    """
    分层并发控制器 — 全局/阶段/提供商三级信号量

    提供统一的 execute_with_control 入口，自动管理所有并发限制。

    Attributes:
        config: 并发控制配置
        _global_sem: 全局信号量（限制总工作者数）
        _phase_sems: 阶段信号量字典
        _provider_limiters: 提供商限流器字典
    """

    def __init__(self: "ConcurrencyController", config: ConcurrencyConfig) -> None:
        self.config = config

        # Layer 1: 全局信号量
        self._global_sem = asyncio.Semaphore(config.global_max_workers)

        # Layer 2: 阶段信号量
        self._phase_sems: Dict[TaskStatus, asyncio.Semaphore] = {
            TaskStatus.ANALYZING: asyncio.Semaphore(config.max_analyzing),
            TaskStatus.ROUTING: asyncio.Semaphore(config.max_routing),
            TaskStatus.PROMPT_GENERATING: asyncio.Semaphore(config.max_prompt_generating),
            TaskStatus.VIDEO_GENERATING: asyncio.Semaphore(config.max_video_generating),
        }

        # Layer 3: 提供商限流器
        self._provider_limiters: Dict[str, ProviderRateLimiter] = {}
        for name, (conc, rpm) in config.provider_limits.items():
            self._provider_limiters[name] = ProviderRateLimiter(
                name=name,
                max_concurrent=conc,
                max_requests_per_minute=rpm,
            )

    async def execute_with_control(
        self,
        task: Task,
        phase: TaskStatus,
        coro: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """
        在完整并发控制下执行协程 — 核心入口方法

        控制顺序: 全局 -> 阶段 -> 提供商（仅视频生成阶段）

        Args:
            task: 当前任务（用于获取提供商信息）
            phase: 执行阶段（决定使用哪个阶段信号量）
            coro: 要执行的协程函数
            *args, **kwargs: 传递给协程的参数

        Returns:
            协程的返回值

        Raises:
            ConcurrencyError: 并发控制异常
        """
        try:
            async with self._global_sem:  # L1: 全局并发
                phase_sem = self._phase_sems.get(phase)
                if phase_sem:
                    async with phase_sem:  # L2: 阶段隔离
                        if (
                            phase == TaskStatus.VIDEO_GENERATING
                            and task.video_provider
                        ):
                            limiter = self._provider_limiters.get(
                                task.video_provider
                            )
                            if limiter:
                                async with limiter:  # L3: 提供商限流
                                    return await coro(*args, **kwargs)
                        return await coro(*args, **kwargs)
                else:
                    # 无阶段信号量（如终止状态），直接执行
                    return await coro(*args, **kwargs)
        except asyncio.TimeoutError as e:
            raise ConcurrencyError(
                message=f"并发控制超时: {e}",
                task_id=task.task_id,
            )

    def get_phase_semaphore(self: "ConcurrencyController", phase: TaskStatus) -> Optional[asyncio.Semaphore]:
        """获取阶段信号量（用于手动控制或监控）"""
        return self._phase_sems.get(phase)

    def get_provider_limiter(self: "ConcurrencyController", provider: str) -> Optional[ProviderRateLimiter]:
        """获取提供商限流器"""
        return self._provider_limiters.get(provider)

    def get_all_limiters(self: "ConcurrencyController") -> Dict[str, ProviderRateLimiter]:
        """获取所有提供商限流器（用于监控）"""
        return dict(self._provider_limiters)

    def calc_retry_delay(self: "ConcurrencyController", retry_count: int) -> float:
        """
        计算重试延迟 — 指数退避 + 全抖动

        公式: delay = min(base * 2^retry_count, max_delay) + random(0, 1)

        目的: 避免惊群效应，分散重试请求的到达时间。

        Args:
            retry_count: 当前重试次数（从 0 开始）

        Returns:
            延迟秒数
        """
        base = self.config.retry_base_delay
        max_d = self.config.retry_max_delay

        # 指数退避
        delay = min(base * (2**retry_count), max_d)

        # 全抖动: 添加 0-1 秒的随机偏移
        jitter = random.random()
        total_delay = delay + jitter

        logger.debug(
            f"重试延迟计算: 次数={retry_count}, "
            f"基础={delay:.2f}s, 抖动={jitter:.2f}s, 总计={total_delay:.2f}s"
        )

        return total_delay

    def calc_retry_with_backoff(
        self,
        retry_count: int,
        base_delay: Optional[float] = None,
        max_delay: Optional[float] = None,
        jitter_factor: float = 1.0,
    ) -> float:
        """
        高级重试延迟计算 — 支持自定义参数

        Args:
            retry_count: 当前重试次数
            base_delay: 自定义基础延迟（默认使用配置值）
            max_delay: 自定义最大延迟（默认使用配置值）
            jitter_factor: 抖动系数（0=无抖动, 1=全抖动）

        Returns:
            延迟秒数
        """
        base = base_delay or self.config.retry_base_delay
        max_d = max_delay or self.config.retry_max_delay

        delay = min(base * (2**retry_count), max_d)
        jitter = random.random() * jitter_factor
        return delay + jitter

    def get_status(self: "ConcurrencyController") -> Dict[str, Any]:
        """获取并发控制器状态（用于监控和调试）"""
        return {
            "global": {
                "max": self.config.global_max_workers,
                "available": self._global_sem._value,
            },
            "phases": {
                phase.value: {
                    "max": self.config.max_analyzing
                    if phase == TaskStatus.ANALYZING
                    else self.config.max_routing
                    if phase == TaskStatus.ROUTING
                    else self.config.max_prompt_generating
                    if phase == TaskStatus.PROMPT_GENERATING
                    else self.config.max_video_generating,
                    "available": sem._value,
                }
                for phase, sem in self._phase_sems.items()
            },
            "providers": {
                name: {
                    "max_concurrent": limiter.max_concurrent,
                    "available": limiter._sem._value,
                }
                for name, limiter in self._provider_limiters.items()
            },
        }


# ═══════════════════════════════════════════════════════════════════════════
# 断点管理器 (CheckpointManager)
# ═══════════════════════════════════════════════════════════════════════════


class CheckpointManager:
    """
    断点续跑管理器

    核心机制:
        1. dirty 标记: 状态变更时 mark_dirty(task)，加入待持久化队列
        2. 定时刷盘: 后台协程每 5 秒执行 _flush()
        3. 恢复扫描: resume_batch() 扫描未完成任务并做一致性检查

    Attributes:
        store: 持久化存储接口
        sm: 状态机引擎
        config: 断点续跑配置
        _pending: 待持久化任务字典 {task_id: Task}
        _persist_task: 后台持久化协程任务
    """

    # 各状态最大允许持续时间（秒）
    STATE_TIMEOUTS: Dict[TaskStatus, int] = {
        TaskStatus.ANALYZING: 120,
        TaskStatus.ROUTING: 60,
        TaskStatus.PROMPT_GENERATING: 180,
        TaskStatus.VIDEO_GENERATING: 600,
    }

    def __init__(
        self,
        store: ITaskStore,
        state_machine: StateMachine,
        config: CheckpointConfig,
    ) -> None:
        self.store = store
        self.sm = state_machine
        self.config = config
        self._pending: Dict[str, Task] = {}
        self._persist_task: Optional[asyncio.Task] = None

    # ── 生命周期管理 ──

    async def start_persist_loop(self: "CheckpointManager") -> None:
        """
        启动自动持久化循环（后台任务）

        只有当 enable_auto_persist=True 时才会启动。
        """
        if self.config.enable_auto_persist and self._persist_task is None:
            self._persist_task = asyncio.create_task(
                self._persist_loop(),
                name="checkpoint-persist-loop",
            )
            logger.info("自动持久化循环已启动")

    async def stop_persist_loop(self: "CheckpointManager") -> None:
        """
        停止持久化循环并强制刷盘

        在批次完成或优雅关闭时调用，确保所有变更都已写入 Sheets。
        """
        if self._persist_task:
            self._persist_task.cancel()
            try:
                await self._persist_task
            except asyncio.CancelledError:
                pass
            self._persist_task = None
            logger.info("自动持久化循环已停止")

        # 强制刷盘
        await self._flush()

    # ── 持久化循环 ──

    async def _persist_loop(self: "CheckpointManager") -> None:
        """
        定期持久化循环 — 后台协程

        循环直到被取消:
            1. 等待 persist_interval_sec 秒
            2. 调用 _flush() 刷盘
            3. 异常时记录日志，继续循环
        """
        while True:
            try:
                await asyncio.sleep(self.config.persist_interval_sec)
                await self._flush()
            except asyncio.CancelledError:
                logger.debug("持久化循环被取消")
                break
            except Exception as e:
                logger.error(f"持久化循环异常: {e}", exc_info=True)

    async def _flush(self: "CheckpointManager") -> None:
        """
        批量刷盘 — 将待持久化任务写入 Sheets

        流程:
            1. 复制并清空 _pending 字典
            2. 逐个调用 store.update_task()
            3. 失败的任务重新加入 _pending（下次重试）
        """
        if not self._pending:
            return

        tasks = list(self._pending.values())
        self._pending.clear()

        failed_tasks: Dict[str, Task] = {}
        for task in tasks:
            try:
                await self.store.update_task(task)
                logger.debug(f"任务 {task.task_id} 已持久化")
            except Exception as e:
                logger.error(f"持久化任务 {task.task_id} 失败: {e}")
                failed_tasks[task.task_id] = task

        # 失败的重新加入队列
        if failed_tasks:
            self._pending.update(failed_tasks)
            logger.warning(f"{len(failed_tasks)} 个任务持久化失败，将重试")

    # ── 脏标记接口 ──

    async def mark_dirty(self: "CheckpointManager", task: Task) -> None:
        """
        标记任务需要持久化

        在状态变更后调用，将任务加入待持久化队列。
        不会立即写入 Sheets，而是等待定时刷盘。

        Args:
            task: 状态已变更的任务
        """
        self._pending[task.task_id] = task

    async def mark_batch_dirty(self: "CheckpointManager", tasks: List[Task]) -> None:
        """批量标记脏任务"""
        for task in tasks:
            self._pending[task.task_id] = task

    # ── 断点续跑核心方法 ──

    async def resume_batch(self: "CheckpointManager", batch_id: str) -> ResumeResult:
        """
        恢复批次 — 断点续跑入口

        流程:
            1. 扫描批次中所有未完成任务
            2. 对每个任务进行一致性检查
            3. 根据检查结果分类: 直接恢复 / 修复 / 丢弃
            4. 修复和丢弃的任务写入 Sheets
            5. 返回可恢复任务列表

        Args:
            batch_id: 要恢复的批次ID

        Returns:
            ResumeResult: 恢复结果

        Raises:
            CheckpointError: 恢复过程严重错误
        """
        logger.info(f"开始恢复批次 {batch_id}")

        # 步骤 1: 扫描未完成任务
        try:
            incomplete = await self.store.get_incomplete_tasks(batch_id)
        except Exception as e:
            raise CheckpointError(
                message=f"扫描未完成任务失败: {e}",
                batch_id=batch_id,
            )

        logger.info(f"发现 {len(incomplete)} 个未完成任务")

        # 步骤 2-3: 逐个检查并分类
        resumable: List[Task] = []
        repaired = 0
        discarded = 0

        for task in incomplete:
            action = await self._recover_task(task)

            if action.action == RecoveryActionType.RESUME:
                resumable.append(task)

            elif action.action == RecoveryActionType.REPAIR:
                old_status = task.status.value
                task.status = TaskStatus(action.new_status or "pending")
                task.retry_count += 1
                task.error_message = action.reason
                await self.mark_dirty(task)
                resumable.append(task)
                repaired += 1
                logger.info(
                    f"任务 {task.task_id} 已修复: "
                    f"{old_status} -> {task.status.value} ({action.reason})"
                )

            elif action.action == RecoveryActionType.DISCARD:
                task.status = TaskStatus.FAILED
                task.error_message = action.reason
                await self.mark_dirty(task)
                discarded += 1
                logger.info(f"任务 {task.task_id} 已丢弃: {action.reason}")

        # 步骤 4: 刷盘修复结果
        await self._flush()

        logger.info(
            f"恢复完成: 可恢复 {len(resumable)}, 修复 {repaired}, 丢弃 {discarded}"
        )

        return ResumeResult(
            batch_id=batch_id,
            resumable_tasks=resumable,
            repaired_count=repaired,
            discarded_count=discarded,
            total_incomplete=len(incomplete),
        )

    async def save_checkpoint(self: "CheckpointManager", batch_id: str) -> int:
        """
        手动保存检查点

        Args:
            batch_id: 批次ID

        Returns:
            持久化的任务数量
        """
        await self._flush()
        count = len(self._pending)
        logger.info(f"手动检查点已保存: {count} 个任务")
        return count

    # ── 恢复决策逻辑 ──

    async def _recover_task(self: "CheckpointManager", task: Task) -> RecoveryAction:
        """
        恢复单个任务 — 一致性检查 + 修复决策

        决策逻辑（按优先级）:
          1. 重试次数超限 -> DISCARD
          2. 状态为 VIDEO_GENERATING 且超时 -> 查询视频 API 确认状态
          3. 状态严重超时 (> 2x) -> REPAIR（重置到 PENDING）
          4. 状态轻微超时 (> 1x) -> RESUME（继续执行）
          5. 状态正常 -> RESUME

        Args:
            task: 要恢复的任务

        Returns:
            RecoveryAction: 恢复决策
        """
        now = datetime.utcnow()

        # 规则 1: 重试次数超限 -> 丢弃
        if task.retry_count >= task.max_retries:
            return RecoveryAction(
                action=RecoveryActionType.DISCARD,
                reason=f"重试次数超限: {task.retry_count}/{task.max_retries}",
            )

        # 终止状态不应出现在未完成任务中，但直接返回 RESUME
        if self.sm.is_terminal(task.status):
            return RecoveryAction(
                action=RecoveryActionType.RESUME,
                reason=f"终止状态 {task.status.value}，无需恢复",
            )

        # 计算状态持续时间
        state_age = self._calculate_state_age(task, now)
        timeout = self.STATE_TIMEOUTS.get(task.status)

        if timeout is None:
            # PENDING 状态无超时，直接恢复
            return RecoveryAction(
                action=RecoveryActionType.RESUME,
                reason="PENDING 状态无超时",
            )

        # 规则 2: VIDEO_GENERATING 特殊处理
        if task.status == TaskStatus.VIDEO_GENERATING and task.video_task_id:
            if state_age > timeout * 2:
                # 严重超时，尝试查询视频 API 状态
                try:
                    api_status = await self._query_video_api(task)
                    if api_status == "completed":
                        return RecoveryAction(
                            action=RecoveryActionType.RESUME,
                            reason="视频实际已完成，只需下载",
                        )
                    elif api_status == "processing":
                        return RecoveryAction(
                            action=RecoveryActionType.RESUME,
                            reason="视频仍在处理中，恢复轮询",
                        )
                except Exception as e:
                    logger.warning(f"查询视频状态失败: {e}")

                # API 查询失败或视频也失败 -> 重置
                return RecoveryAction(
                    action=RecoveryActionType.REPAIR,
                    reason=f"视频生成超时 {state_age:.0f}s，重置到 PENDING",
                    old_status=task.status.value,
                    new_status=TaskStatus.PENDING.value,
                )

        # 规则 3: 严重超时（进程可能崩溃）-> 修复
        if state_age > timeout * 2:
            return RecoveryAction(
                action=RecoveryActionType.REPAIR,
                reason=f"状态 {task.status.value} 严重超时 ({state_age:.0f}s > {timeout*2}s)",
                old_status=task.status.value,
                new_status=TaskStatus.PENDING.value,
            )

        # 规则 4: 轻微超时 -> 继续执行
        if state_age > timeout:
            return RecoveryAction(
                action=RecoveryActionType.RESUME,
                reason=f"状态轻微超时 ({state_age:.0f}s > {timeout}s)，继续执行",
            )

        # 规则 5: 状态正常 -> 直接恢复
        return RecoveryAction(
            action=RecoveryActionType.RESUME,
            reason="状态正常",
        )

    # ── 辅助方法 ──

    def _calculate_state_age(self: "CheckpointManager", task: Task, now: datetime) -> float:
        """
        计算当前状态持续时间

        Args:
            task: 任务
            now: 当前时间

        Returns:
            状态持续时间（秒），无法计算返回 inf
        """
        if task.status_changed_at:
            try:
                changed = datetime.fromisoformat(task.status_changed_at)
                return (now - changed).total_seconds()
            except (ValueError, TypeError):
                pass
        return float("inf")

    async def _query_video_api(self: "CheckpointManager", task: Task) -> str:
        """
        查询视频 API 确认任务真实状态

        Args:
            task: 任务（需有 video_provider 和 video_task_id）

        Returns:
            API 状态: "completed" / "processing" / "failed" / "unknown"
        """
        logger.debug(
            f"查询视频状态: provider={task.video_provider}, "
            f"task_id={task.video_task_id}"
        )
        # 骨架代码: 实际需调用视频提供商的查询接口
        return "unknown"

    def get_pending_count(self: "CheckpointManager") -> int:
        """获取待持久化任务数量（用于监控）"""
        return len(self._pending)


# ═══════════════════════════════════════════════════════════════════════════
# 进度追踪器 (ProgressTracker)
# ═══════════════════════════════════════════════════════════════════════════


class ProgressTracker:
    """
    进度追踪器

    核心机制:
        1. 内存计数器: _stats 字典实时跟踪各状态任务数
        2. 状态变更回调: on_task_status_change() 更新计数器
        3. 定时更新: 后台协程每 5 秒写入 Sheets
        4. ETA 计算: 基于指数移动平均的吞吐量估计

    Attributes:
        store: 持久化存储接口
        batch: 当前批次
        _stats: 状态计数器 {status_value: count}
        _start_time: 批次开始时间（monotonic）
        _throughput_ema: 吞吐量指数移动平均值
        _update_task: 后台更新协程
        _callbacks: 进度回调函数列表
    """

    UPDATE_INTERVAL = 5.0  # Sheets 更新间隔（秒）
    ETA_MIN_SAMPLES = 3  # ETA 计算最小样本数
    EMA_ALPHA = 0.3  # 指数移动平均平滑因子

    def __init__(self: "ProgressTracker", store: ITaskStore, batch: Batch) -> None:
        self.store = store
        self.batch = batch
        self._stats: Dict[str, int] = defaultdict(int)
        self._start_time: Optional[float] = None
        self._throughput_ema: Optional[float] = None
        self._update_task: Optional[asyncio.Task] = None
        self._callbacks: List[Callable[[ProgressSnapshot], None]] = []

    # ── 生命周期管理 ──

    def init_stats(self: "ProgressTracker", tasks: List[Task]) -> None:
        """
        从任务列表初始化统计

        Args:
            tasks: 批次中所有任务
        """
        self._stats.clear()
        for t in tasks:
            self._stats[t.status.value] = self._stats.get(t.status.value, 0) + 1
        logger.debug(f"进度统计已初始化: {dict(self._stats)}")

    async def start(self: "ProgressTracker") -> None:
        """
        启动追踪循环

        启动后台协程定期更新 Sheets，并记录开始时间。
        """
        self._start_time = time.monotonic()
        self._update_task = asyncio.create_task(
            self._update_loop(),
            name="progress-update-loop",
        )
        logger.info("进度追踪已启动")

    async def stop(self: "ProgressTracker") -> None:
        """
        停止追踪并强制最终更新

        取消后台协程，并强制将最终状态写入 Sheets。
        """
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
            self._update_task = None

        await self._update_sheets()
        logger.info("进度追踪已停止")

    # ── 状态变更回调 ──

    def on_task_status_change(
        self,
        task: Task,
        old: TaskStatus,
        new: TaskStatus,
    ) -> None:
        """
        任务状态变更回调 — 更新内存计数器

        Args:
            task: 状态变更的任务
            old: 原状态
            new: 新状态
        """
        old_val = old.value
        new_val = new.value

        # 减少旧状态计数
        self._stats[old_val] = max(0, self._stats.get(old_val, 0) - 1)
        # 增加新状态计数
        self._stats[new_val] = self._stats.get(new_val, 0) + 1

        logger.debug(
            f"任务 {task.task_id}: {old_val} -> {new_val} | "
            f"统计: {dict(self._stats)}"
        )

    # ── 快照和查询 ──

    def get_snapshot(self: "ProgressTracker") -> ProgressSnapshot:
        """
        获取当前进度快照

        Returns:
            ProgressSnapshot: 包含完整进度信息的快照
        """
        total = self.batch.total_tasks
        completed = self._stats.get(TaskStatus.COMPLETED.value, 0)
        failed = self._stats.get(TaskStatus.FAILED.value, 0)
        pending = self._stats.get(TaskStatus.PENDING.value, 0)
        in_progress = total - completed - failed - pending
        percent = (completed / total * 100) if total > 0 else 0.0

        # ETA 计算
        eta = self._calc_eta(completed)
        throughput = self._throughput_ema

        return ProgressSnapshot(
            batch_id=self.batch.batch_id,
            total=total,
            by_status=dict(self._stats),
            completed=completed,
            failed=failed,
            in_progress=in_progress,
            percent=round(percent, 1),
            eta_seconds=eta,
            throughput=throughput,
        )

    def _calc_eta(self: "ProgressTracker", completed: int) -> Optional[float]:
        """
        计算预计剩余时间（秒）

        算法: 简单线性估计 + 指数移动平均平滑
            eta = remaining_tasks / throughput_ema
            throughput_ema = alpha * instant + (1-alpha) * throughput_ema

        Args:
            completed: 已完成任务数

        Returns:
            预计剩余秒数，数据不足时返回 None
        """
        if not self._start_time or completed < self.ETA_MIN_SAMPLES:
            return None

        elapsed = time.monotonic() - self._start_time
        if elapsed < 10:  # 数据不足
            return None

        # 瞬时吞吐量（任务/秒）
        instant_throughput = completed / elapsed

        # 指数移动平均平滑
        if self._throughput_ema is None:
            self._throughput_ema = instant_throughput
        else:
            self._throughput_ema = (
                self.EMA_ALPHA * instant_throughput
                + (1 - self.EMA_ALPHA) * self._throughput_ema
            )

        remaining = self.batch.total_tasks - completed
        if self._throughput_ema > 0:
            eta = remaining / self._throughput_ema
            return round(eta, 0)
        return None

    # ── 可视化 ──

    def format_progress_bar(self: "ProgressTracker", width: int = 40) -> str:
        """
        格式化 ASCII 进度条

        Args:
            width: 进度条宽度（字符数）

        Returns:
            进度条字符串

        Example:
            >>> tracker.format_progress_bar()
            "[████████████████░░░░░░░░░░░░░░░░░░░░░░░░] 45.0% (18/40) ETA: 120s"
        """
        snap = self.get_snapshot()
        filled = int(width * snap.percent / 100)
        bar = "█" * filled + "░" * (width - filled)
        eta_str = f"{snap.eta_seconds:.0f}s" if snap.eta_seconds else "N/A"
        return (
            f"[{bar}] {snap.percent:.1f}% "
            f"({snap.completed}/{snap.total}) ETA: {eta_str}"
        )

    def format_compact(self: "ProgressTracker") -> str:
        """紧凑格式进度信息"""
        snap = self.get_snapshot()
        return (
            f"进度: {snap.percent:.1f}% | "
            f"完成: {snap.completed} | "
            f"失败: {snap.failed} | "
            f"执行中: {snap.in_progress}"
        )

    # ── 回调注册 ──

    def register_callback(self: "ProgressTracker", fn: Callable[[ProgressSnapshot], None]) -> None:
        """
        注册进度更新回调

        Args:
            fn: 回调函数，接收 ProgressSnapshot 参数
        """
        self._callbacks.append(fn)

    # ── 后台更新循环 ──

    async def _update_loop(self: "ProgressTracker") -> None:
        """
        定期更新循环 — 后台协程

        每 UPDATE_INTERVAL 秒:
            1. 调用 _update_sheets() 更新 Sheets
            2. 触发回调函数
        """
        while True:
            try:
                await asyncio.sleep(self.UPDATE_INTERVAL)
                await self._update_sheets()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"进度更新异常: {e}", exc_info=True)

    async def _update_sheets(self: "ProgressTracker") -> None:
        """
        更新 Sheets 进度

        策略:
          1. 获取当前快照
          2. 更新 Batch 模型各字段
          3. 检测批次完成
          4. 写入 Sheets（带重试）
          5. 触发回调
        """
        snap = self.get_snapshot()

        # 更新 Batch 模型
        self.batch.pending_count = snap.by_status.get("pending", 0)
        self.batch.analyzing_count = snap.by_status.get("analyzing", 0)
        self.batch.routing_count = snap.by_status.get("routing", 0)
        self.batch.prompt_generating_count = snap.by_status.get(
            "prompt_generating", 0
        )
        self.batch.video_generating_count = snap.by_status.get("video_generating", 0)
        self.batch.completed_count = snap.completed
        self.batch.failed_count = snap.failed
        self.batch.progress_percent = snap.percent

        # 检测批次完成
        if snap.completed + snap.failed >= snap.total:
            self.batch.status = BatchStatus.COMPLETED
            self.batch.completed_at = datetime.utcnow().isoformat()
            if self._start_time:
                self.batch.total_time_sec = round(
                    time.monotonic() - self._start_time, 1
                )

        # 写入 Sheets（带 3 次重试）
        for attempt in range(3):
            try:
                await self.store.update_batch_summary(self.batch)
                break
            except Exception as e:
                delay = 2**attempt
                logger.warning(
                    f"Sheets 更新失败({attempt+1}/3): {e}, {delay}s 后重试"
                )
                await asyncio.sleep(delay)

        # 触发回调
        for fn in self._callbacks:
            try:
                fn(snap)
            except Exception as e:
                logger.error(f"进度回调失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 异步轮询器 (AsyncPoller)
# ═══════════════════════════════════════════════════════════════════════════


class AsyncPoller:
    """
    异步轮询管理器

    特性:
      - 指数退避间隔
      - 自动超时处理
      - 状态变更回调
      - 批量并行轮询

    Attributes:
        config: 轮询配置
        _callbacks: 回调函数字典
    """

    def __init__(self: "AsyncPoller", config: PollConfig) -> None:
        self.config = config
        self._callbacks: Dict[str, List[Callable]] = {
            "on_complete": [],
            "on_failed": [],
            "on_timeout": [],
        }

    def on(self: "AsyncPoller", event: str, fn: Callable) -> None:
        """
        注册回调

        Args:
            event: 事件名 (on_complete / on_failed / on_timeout)
            fn: 回调函数
        """
        self._callbacks.setdefault(event, []).append(fn)

    async def poll(
        self,
        task: Task,
        poll_fn: Callable[[Task], Awaitable[PollResult]],
    ) -> PollResult:
        """
        对单个任务进行轮询

        使用指数退避策略，直到任务完成或超时。

        Args:
            task: 要轮询的任务
            poll_fn: 轮询函数，返回 PollResult

        Returns:
            PollResult: 最终结果

        Raises:
            PollTimeoutError: 轮询超时
        """
        interval = self.config.initial_interval
        poll_count = 0
        start = time.monotonic()

        while poll_count < self.config.max_polls:
            # 检查总超时
            elapsed = time.monotonic() - start
            if elapsed > self.config.timeout:
                result = PollResult(
                    status="timeout",
                    error=f"轮询超时 ({self.config.timeout}s)",
                )
                await self._emit("on_timeout", task, result)
                return result

            # 执行轮询
            try:
                result = await poll_fn(task)
            except Exception as e:
                result = PollResult(status="error", error=str(e))

            poll_count += 1

            # 检查结果
            if result.status == "completed":
                await self._emit("on_complete", task, result)
                return result
            elif result.status == "failed":
                await self._emit("on_failed", task, result)
                return result

            # 指数退避
            interval = min(
                interval * self.config.backoff_factor, self.config.max_interval
            )
            if self.config.jitter:
                jitter = interval * 0.2 * (2 * random.random() - 1)
                interval += jitter

            logger.debug(
                f"任务 {task.task_id} 轮询 #{poll_count}, "
                f"状态={result.status}, 下次间隔={interval:.1f}s"
            )
            await asyncio.sleep(interval)

        # 超过最大轮询次数
        result = PollResult(
            status="timeout",
            error=f"超过最大轮询次数 ({self.config.max_polls})",
        )
        await self._emit("on_timeout", task, result)
        return result

    async def poll_batch(
        self,
        tasks: List[Task],
        poll_fn: Callable[[Task], Awaitable[PollResult]],
        max_concurrent: int = 10,
    ) -> List[PollResult]:
        """
        批量并行轮询

        Args:
            tasks: 任务列表
            poll_fn: 轮询函数
            max_concurrent: 最大并行轮询数

        Returns:
            PollResult 列表（与 tasks 一一对应）
        """
        sem = asyncio.Semaphore(max_concurrent)

        async def _with_sem(t: Task) -> PollResult:
            async with sem:
                return await self.poll(t, poll_fn)

        results = await asyncio.gather(
            *[_with_sem(t) for t in tasks],
            return_exceptions=True,
        )

        # 异常包装为 PollResult
        processed: List[PollResult] = []
        for r in results:
            if isinstance(r, Exception):
                processed.append(PollResult(status="error", error=str(r)))
            else:
                processed.append(r)
        return processed

    async def _emit(self: "AsyncPoller", event: str, task: Task, result: PollResult) -> None:
        """触发回调"""
        for fn in self._callbacks.get(event, []):
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(task, result)
                else:
                    fn(task, result)
            except Exception as e:
                logger.error(f"回调执行失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# 批量处理器 (BatchProcessor) — 单任务流水线
# ═══════════════════════════════════════════════════════════════════════════


class BatchProcessor:
    """
    批量处理器

    执行单个任务的完整流水线，每个阶段都有独立的并发控制和错误处理。

    Attributes:
        sm: 状态机引擎
        concurrency: 并发控制器
        checkpoint: 断点管理器
        progress: 进度追踪器
        image_analyzer: 图片分析器（外部依赖）
        smart_router: 智能路由器（外部依赖）
        gem_caller: Gem 调用器（外部依赖）
        video_providers: 视频提供商字典（外部依赖）
    """

    def __init__(
        self,
        state_machine: StateMachine,
        concurrency: ConcurrencyController,
        checkpoint: CheckpointManager,
        progress: Optional[ProgressTracker] = None,
        image_analyzer: Optional[Callable[[str], Awaitable[Any]]] = None,
        smart_router: Optional[Callable[[Optional[str]], Awaitable[Any]]] = None,
        gem_caller: Optional[Callable[..., Awaitable[str]]] = None,
        video_providers: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.sm = state_machine
        self.concurrency = concurrency
        self.checkpoint = checkpoint
        self.progress = progress
        self.image_analyzer = image_analyzer
        self.smart_router = smart_router
        self.gem_caller = gem_caller
        self.video_providers = video_providers or {}

    # ── 核心方法: 处理单个任务 ──

    async def process_task(self: "BatchProcessor", task: Task) -> Task:
        """
        处理单个任务的完整流水线

        按顺序执行 4 个阶段，每个阶段有独立的错误处理。
        任何阶段失败都会将任务置为 FAILED 状态。

        Args:
            task: 要处理的任务（状态应为 PENDING）

        Returns:
            处理后的任务（状态为 COMPLETED 或 FAILED）

        执行流程:
            1. 状态转换: PENDING -> ANALYZING
            2. 执行图片分析
            3. 状态转换: ANALYZING -> ROUTING
            4. 执行 Gem 路由
            5. 状态转换: ROUTING -> PROMPT_GENERATING
            6. 执行提示词生成
            7. 状态转换: PROMPT_GENERATING -> VIDEO_GENERATING
            8. 执行视频生成 + 轮询
            9. 状态转换: VIDEO_GENERATING -> COMPLETED
        """
        logger.info(f"开始处理任务 {task.task_id}: {task.image_name}")
        task.started_at = datetime.utcnow().isoformat()

        try:
            # 阶段 1: 图片分析
            await self._execute_phase(
                task=task,
                phase=TaskStatus.ANALYZING,
                enter_action=TaskAction.START,
                exit_action=TaskAction.ANALYZE_DONE,
                phase_fn=self._do_analyze,
            )

            # 阶段 2: Gem 路由
            await self._execute_phase(
                task=task,
                phase=TaskStatus.ROUTING,
                enter_action=TaskAction.ANALYZE_DONE,
                exit_action=TaskAction.ROUTE_DONE,
                phase_fn=self._do_route,
            )

            # 阶段 3: 提示词生成
            await self._execute_phase(
                task=task,
                phase=TaskStatus.PROMPT_GENERATING,
                enter_action=TaskAction.ROUTE_DONE,
                exit_action=TaskAction.PROMPT_DONE,
                phase_fn=self._do_prompt_generation,
            )

            # 阶段 4: 视频生成 + 轮询
            await self._execute_phase(
                task=task,
                phase=TaskStatus.VIDEO_GENERATING,
                enter_action=TaskAction.PROMPT_DONE,
                exit_action=TaskAction.VIDEO_DONE,
                phase_fn=self._do_video_generation,
            )

            # 完成: 计算处理时间
            task.completed_at = datetime.utcnow().isoformat()
            if task.started_at:
                start = datetime.fromisoformat(task.started_at)
                end = datetime.fromisoformat(task.completed_at)
                task.processing_time_sec = round((end - start).total_seconds(), 1)

            logger.info(
                f"任务 {task.task_id} 完成，耗时 {task.processing_time_sec}s"
            )

        except Exception as e:
            logger.exception(f"任务 {task.task_id} 处理异常: {e}")
            try:
                self.sm.transition(task, TaskAction.FAIL, reason=str(e))
            except Exception:
                task.status = TaskStatus.FAILED
                task.error_message = str(e)

        # 标记持久化
        await self.checkpoint.mark_dirty(task)
        return task

    async def retry_task(self: "BatchProcessor", task: Task) -> Task:
        """
        重试单个失败任务

        将任务从 FAILED 重置为 PENDING，然后重新执行。

        Args:
            task: 要重试的任务

        Returns:
            重试后的任务
        """
        logger.info(f"重试任务 {task.task_id} (第 {task.retry_count + 1} 次)")
        self.sm.transition(task, TaskAction.RETRY)
        task.error_message = None
        task.error_code = None
        await self.checkpoint.mark_dirty(task)
        return await self.process_task(task)

    # ── 阶段执行模板 ──

    async def _execute_phase(
        self,
        task: Task,
        phase: TaskStatus,
        enter_action: TaskAction,
        exit_action: TaskAction,
        phase_fn: Callable[[Task], Awaitable[None]],
    ) -> None:
        """
        执行单个阶段 — 通用模板方法

        流程:
            1. 状态转换（进入阶段）
            2. 标记持久化
            3. 通知进度追踪器
            4. 在并发控制下执行阶段函数
            5. 状态转换（退出阶段）
            6. 标记持久化

        Args:
            task: 当前任务
            phase: 阶段状态
            enter_action: 进入动作
            exit_action: 退出动作
            phase_fn: 阶段执行函数
        """
        # 步骤 1: 状态转换
        self.sm.transition(task, enter_action)

        # 步骤 2: 标记持久化
        await self.checkpoint.mark_dirty(task)

        # 步骤 3: 通知进度追踪
        if self.progress:
            self.progress.on_task_status_change(task, task.status, phase)

        # 步骤 4: 在并发控制下执行
        await self.concurrency.execute_with_control(
            task=task,
            phase=phase,
            coro=phase_fn,
        )

        # 步骤 5: 状态转换（退出）
        self.sm.transition(task, exit_action)

        # 步骤 6: 标记持久化
        await self.checkpoint.mark_dirty(task)

    # ── 阶段具体实现 ──

    async def _do_analyze(self: "BatchProcessor", task: Task) -> None:
        """
        执行图片分析

        调用 VLM 分析产品图片，提取产品类别。

        Args:
            task: 当前任务
        """
        if not self.image_analyzer:
            task.product_category = "unknown"
            logger.warning("未配置图片分析器，使用默认值")
            return

        logger.debug(f"分析图片: {task.image_path}")
        result = await self.image_analyzer(task.image_path)
        task.product_category = getattr(result, "category", None) or str(result)
        logger.debug(f"分析结果: {task.product_category}")

    async def _do_route(self: "BatchProcessor", task: Task) -> None:
        """
        执行 Gem 路由

        根据产品类别匹配最佳 Gem 模板。

        Args:
            task: 当前任务
        """
        if not self.smart_router:
            logger.warning("未配置智能路由器")
            return

        logger.debug(f"路由产品类别: {task.product_category}")
        result = await self.smart_router(task.product_category)
        matches = getattr(result, "matches", None) or result

        if matches and len(matches) > 0:
            top = matches[0] if isinstance(matches, list) else matches
            task.matched_gem_id = (
                top.get("gem_id") if isinstance(top, dict) else str(top)
            )
            task.matched_gem_name = (
                top.get("name") if isinstance(top, dict) else None
            )
            task.match_score = (
                top.get("score") if isinstance(top, dict) else None
            )
            logger.debug(
                f"匹配 Gem: {task.matched_gem_name} (score={task.match_score})"
            )

    async def _do_prompt_generation(self: "BatchProcessor", task: Task) -> None:
        """
        执行提示词生成

        调用 Gem 生成视频提示词。

        Args:
            task: 当前任务
        """
        if not self.gem_caller:
            task.generated_prompt = f"Generate a product video for {task.image_name}"
            logger.warning("未配置 Gem 调用器，使用默认提示词")
            return

        logger.debug(f"生成提示词: gem={task.matched_gem_id}")
        task.generated_prompt = await self.gem_caller(
            gem_id=task.matched_gem_id,
            image_path=task.image_path,
        )
        logger.debug(f"提示词: {task.generated_prompt[:80]}...")

    async def _do_video_generation(self: "BatchProcessor", task: Task) -> None:
        """
        执行视频生成 + 轮询

        选择提供商 -> 提交视频 -> 轮询直到完成。

        Args:
            task: 当前任务

        Raises:
            TaskSchedulerError: 视频生成失败
            ProviderError: 提供商调用失败
        """
        # 选择提供商
        provider_name = self._select_provider(task)
        task.video_provider = provider_name
        provider = self.video_providers.get(provider_name)

        if not provider:
            raise TaskSchedulerError(
                message=f"未找到视频提供商: {provider_name}",
                task_id=task.task_id,
            )

        # 提交视频
        logger.debug(f"提交视频: provider={provider_name}")
        submit_result = await provider.submit(
            image_path=task.image_path,
            prompt=task.generated_prompt or "",
        )
        task.video_task_id = submit_result if isinstance(submit_result, str) else str(submit_result)
        await self.checkpoint.mark_dirty(task)

        # 轮询
        logger.debug(f"开始轮询: task_id={task.video_task_id}")

        async def _poll_fn(t: Task) -> PollResult:
            """轮询函数 — 查询视频生成状态"""
            status = await provider.query_task(t.video_task_id)
            if status == "completed":
                url = await provider.get_result(t.video_task_id)
                return PollResult(status="completed", video_url=url)
            elif status == "failed":
                return PollResult(status="failed", error="视频生成失败")
            return PollResult(status="processing")

        # 使用简单轮询
        poll_interval = 5.0
        max_wait = 600.0
        start = time.time()

        while True:
            elapsed = time.time() - start
            if elapsed > max_wait:
                raise TaskSchedulerError(
                    message=f"视频生成轮询超时 ({max_wait}s)",
                    task_id=task.task_id,
                )

            result = await _poll_fn(task)
            if result.status == "completed":
                task.video_url = result.video_url
                logger.debug(f"视频完成: {task.video_url}")
                return
            elif result.status == "failed":
                raise ProviderError(
                    message=result.error or "视频生成失败",
                    provider=provider_name,
                    task_id=task.task_id,
                )

            await asyncio.sleep(poll_interval)

    def _select_provider(self: "BatchProcessor", task: Task) -> str:
        """
        选择视频提供商

        优先级:
            1. 任务指定的提供商
            2. 高配额提供商（vidu, seedance_mini, veo_fast, kling_turbo）
            3. 任意可用提供商

        Args:
            task: 当前任务

        Returns:
            提供商名称
        """
        if task.video_provider and task.video_provider in self.video_providers:
            return task.video_provider

        # 按优先级选择
        priority = [
            "vidu_q3_turbo",
            "seedance_2_0_mini",
            "veo_3_1_fast",
            "kling_3_0_turbo",
            "vidu_q3_pro",
            "seedance_2_0",
            "veo_3_1_standard",
            "kling_3_0",
        ]
        for p in priority:
            if p in self.video_providers:
                return p

        # fallback: 第一个可用
        if self.video_providers:
            return list(self.video_providers.keys())[0]

        raise TaskSchedulerError("没有可用的视频提供商")


# ═══════════════════════════════════════════════════════════════════════════
# 任务调度器门面 (TaskScheduler)
# ═══════════════════════════════════════════════════════════════════════════


class TaskScheduler:
    """
    任务调度器 — 模块门面

    整合所有子系统，提供简洁的外部接口。

    Attributes:
        config: 调度器总配置
        sm: 状态机引擎
        store: 持久化存储
        concurrency: 并发控制器
        checkpoint: 断点管理器
        progress: 进度追踪器（运行时创建）
        processor: 批量处理器
        _shutdown: 关闭标志

    Example:
        >>> config = SchedulerConfig(spreadsheet_id="xxx")
        >>> scheduler = TaskScheduler(config)
        >>> batch = await scheduler.submit_batch("./images", "夏季促销")
        >>> await scheduler.run_batch(batch.batch_id)
    """

    def __init__(
        self,
        config: SchedulerConfig,
        image_analyzer: Optional[Callable[[str], Awaitable[Any]]] = None,
        smart_router: Optional[Callable[[Optional[str]], Awaitable[Any]]] = None,
        gem_caller: Optional[Callable[..., Awaitable[str]]] = None,
        video_providers: Optional[Dict[str, Any]] = None,
        store: Optional[ITaskStore] = None,
    ) -> None:
        self.config = config
        self._shutdown = False

        # 初始化子系统
        self.sm = StateMachine()
        self.store = store or _create_in_memory_store()
        self.concurrency = ConcurrencyController(config.concurrency)
        self.checkpoint = CheckpointManager(self.store, self.sm, config.checkpoint)
        self.progress: Optional[ProgressTracker] = None

        # 处理器
        self.processor = BatchProcessor(
            state_machine=self.sm,
            concurrency=self.concurrency,
            checkpoint=self.checkpoint,
            progress=self.progress,
            image_analyzer=image_analyzer,
            smart_router=smart_router,
            gem_caller=gem_caller,
            video_providers=video_providers,
        )

    # ── 核心 API: 批次管理 ──

    async def submit_batch(
        self,
        image_dir: str,
        batch_name: str = "",
        **kwargs: Any,
    ) -> Batch:
        """
        提交新批次

        扫描目录 -> 创建任务 -> 入库 -> 返回批次信息

        Args:
            image_dir: 产品图片目录路径
            batch_name: 批次名称（可选）
            **kwargs: 额外配置参数

        Returns:
            Batch: 创建的批次

        Raises:
            TaskSchedulerError: 目录不存在或无图片
        """
        img_dir = Path(image_dir)
        if not img_dir.exists():
            raise TaskSchedulerError(f"目录不存在: {image_dir}")

        # 扫描图片
        exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
        images = [f for f in img_dir.iterdir() if f.is_file() and f.suffix.lower() in exts]
        if not images:
            raise TaskSchedulerError(f"目录中没有图片: {image_dir}")

        # 创建批次
        batch = Batch(
            batch_name=batch_name
            or f"批次-{datetime.utcnow().strftime('%m%d%H%M')}",
            total_tasks=len(images),
            pending_count=len(images),
            config=kwargs,
        )

        # 创建任务
        tasks = [
            Task(batch_id=batch.batch_id, image_path=str(f), image_name=f.stem)
            for f in sorted(images)
        ]

        # 入库
        await self.store.init_task_sheet()
        await self.store.add_tasks(tasks)
        await self.store.update_batch_summary(batch)

        logger.info(f"批次 {batch.batch_id} 创建完成，共 {len(tasks)} 个任务")
        return batch

    async def run_batch(self: "TaskScheduler", batch_id: str, concurrency: Optional[int] = None) -> None:
        """
        执行批次

        断点恢复 -> 启动工作者 -> 等待完成

        Args:
            batch_id: 批次ID
            concurrency: 覆盖并发数（可选）

        执行流程:
            1. 断点续跑: 恢复未完成任务
            2. 初始化进度追踪
            3. 启动持久化循环
            4. 启动工作者协程
            5. 等待所有工作者完成
            6. 清理资源
        """
        logger.info(f"开始执行批次 {batch_id}")
        self._shutdown = False

        # 步骤 1: 断点续跑
        resume = await self.checkpoint.resume_batch(batch_id)
        tasks = resume.resumable_tasks
        if not tasks:
            logger.info("没有需要执行的任务")
            return

        # 获取批次信息
        batch = await self.store.get_batch(batch_id)
        if not batch:
            batch = Batch(batch_id=batch_id, total_tasks=len(tasks))

        batch.status = BatchStatus.RUNNING
        batch.started_at = datetime.utcnow().isoformat()

        # 步骤 2: 初始化进度追踪
        self.progress = ProgressTracker(self.store, batch)
        self.progress.init_stats(tasks)
        self.processor.progress = self.progress
        await self.progress.start()

        # 步骤 3: 启动持久化
        await self.checkpoint.start_persist_loop()

        try:
            # 步骤 4: 启动工作者
            max_workers = concurrency or self.config.concurrency.global_max_workers
            workers = [
                asyncio.create_task(
                    self._worker_loop(tasks),
                    name=f"worker-{i}",
                )
                for i in range(max_workers)
            ]

            logger.info(f"已启动 {max_workers} 个工作者")

            # 步骤 5: 等待完成
            await asyncio.gather(*workers, return_exceptions=True)
            logger.info(f"批次 {batch_id} 执行完成")

        finally:
            # 步骤 6: 清理
            await self.checkpoint.stop_persist_loop()
            if self.progress:
                await self.progress.stop()
                self.progress = None

    async def pause_batch(self: "TaskScheduler", batch_id: str) -> None:
        """
        暂停批次

        设置关闭标志，工作者完成当前任务后退出。
        不会中断进行中的任务。

        Args:
            batch_id: 批次ID
        """
        logger.info(f"暂停批次 {batch_id}")
        self._shutdown = True

    async def resume_batch(self: "TaskScheduler", batch_id: str) -> ResumeResult:
        """
        恢复批次（外部调用）

        清除关闭标志，返回恢复结果（不自动执行）。

        Args:
            batch_id: 批次ID

        Returns:
            ResumeResult: 恢复结果
        """
        logger.info(f"恢复批次 {batch_id}")
        self._shutdown = False
        return await self.checkpoint.resume_batch(batch_id)

    async def query_batch(self: "TaskScheduler", batch_id: str) -> Optional[ProgressSnapshot]:
        """
        查询批次进度

        Args:
            batch_id: 批次ID

        Returns:
            ProgressSnapshot: 进度快照，批次未运行时返回 None
        """
        if self.progress:
            return self.progress.get_snapshot()

        # 批次未运行，从 Sheets 查询
        batch = await self.store.get_batch(batch_id)
        if not batch:
            raise BatchNotFoundError(batch_id)

        return ProgressSnapshot(
            batch_id=batch_id,
            total=batch.total_tasks,
            by_status={},
            completed=batch.completed_count,
            failed=batch.failed_count,
            in_progress=0,
            percent=batch.progress_percent,
        )

    async def retry_failed(self: "TaskScheduler", batch_id: str) -> int:
        """
        重试批次中所有失败的任务

        Args:
            batch_id: 批次ID

        Returns:
            重试的任务数量
        """
        tasks = await self.store.get_tasks_by_batch(batch_id)
        failed = [t for t in tasks if t.status == TaskStatus.FAILED and t.can_retry()]

        for t in failed:
            try:
                self.sm.transition(t, TaskAction.RETRY)
            except Exception as e:
                logger.warning(f"任务 {t.task_id} 重试转换失败: {e}")
                t.status = TaskStatus.PENDING
                t.retry_count += 1
            await self.checkpoint.mark_dirty(t)

        await self.checkpoint._flush()
        logger.info(f"已重试 {len(failed)} 个失败任务")
        return len(failed)

    async def shutdown(self: "TaskScheduler") -> None:
        """
        优雅关闭

        保存状态 -> 停止循环 -> 刷盘
        """
        logger.info("正在关闭调度器...")
        self._shutdown = True
        await self.checkpoint.stop_persist_loop()
        if self.progress:
            await self.progress.stop()
        logger.info("调度器已关闭")

    # ── 内部方法: 工作者循环 ──

    async def _worker_loop(self: "TaskScheduler", tasks: List[Task]) -> None:
        """
        工作者循环 — 从任务列表中逐个领取并处理

        Args:
            tasks: 任务列表（共享的可迭代对象）
        """
        iterator = iter(tasks)
        worker_name = (
            asyncio.current_task().get_name()
            if asyncio.current_task()
            else "unknown"
        )

        while not self._shutdown:
            try:
                task = next(iterator)
            except StopIteration:
                break

            try:
                logger.debug(f"[{worker_name}] 处理任务 {task.task_id}")
                await self.processor.process_task(task)
            except Exception as e:
                logger.exception(f"[{worker_name}] 任务 {task.task_id} 异常: {e}")

        logger.debug(f"[{worker_name}] 工作者退出")

    # ── 监控方法 ──

    def get_status(self: "TaskScheduler") -> Dict[str, Any]:
        """
        获取调度器状态（用于监控）

        Returns:
            状态字典
        """
        return {
            "shutdown": self._shutdown,
            "concurrency": self.concurrency.get_status(),
            "checkpoint_pending": self.checkpoint.get_pending_count(),
            "batch_running": self.progress is not None,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 内存存储实现（用于测试和默认场景）
# ═══════════════════════════════════════════════════════════════════════════


class InMemoryTaskStore(ITaskStore):
    """
    内存任务存储实现

    用于测试和无需持久化的场景。所有数据保存在内存中，
    进程重启后数据丢失。
    """

    def __init__(self: "InMemoryTaskStore") -> None:
        self._tasks: Dict[str, Task] = {}
        self._batches: Dict[str, Batch] = {}
        self._task_sheet_initialized = False

    async def init_task_sheet(self: "InMemoryTaskStore") -> bool:
        """初始化任务表"""
        self._task_sheet_initialized = True
        return True

    async def add_tasks(self: "InMemoryTaskStore", tasks: List[Task]) -> int:
        """批量添加任务"""
        for task in tasks:
            self._tasks[task.task_id] = task
        return len(tasks)

    async def update_task(self: "InMemoryTaskStore", task: Task) -> bool:
        """更新单个任务"""
        self._tasks[task.task_id] = task
        return True

    async def get_task(self: "InMemoryTaskStore", task_id: str) -> Optional[Task]:
        """按 ID 获取任务"""
        return self._tasks.get(task_id)

    async def get_tasks_by_batch(self: "InMemoryTaskStore", batch_id: str) -> List[Task]:
        """获取批次下所有任务"""
        return [t for t in self._tasks.values() if t.batch_id == batch_id]

    async def get_incomplete_tasks(self: "InMemoryTaskStore", batch_id: str) -> List[Task]:
        """获取未完成任务"""
        all_tasks = await self.get_tasks_by_batch(batch_id)
        return [t for t in all_tasks if not t.is_terminal()]

    async def update_batch_summary(self: "InMemoryTaskStore", batch: Batch) -> bool:
        """更新批次汇总"""
        self._batches[batch.batch_id] = batch
        return True

    async def get_batch(self: "InMemoryTaskStore", batch_id: str) -> Optional[Batch]:
        """获取批次信息"""
        return self._batches.get(batch_id)

    async def batch_update_cells(self: "InMemoryTaskStore", updates: List[Dict[str, Any]]) -> bool:
        """批量更新单元格"""
        return True


def _create_in_memory_store() -> InMemoryTaskStore:
    """创建默认的内存存储"""
    return InMemoryTaskStore()


# ═══════════════════════════════════════════════════════════════════════════
# 模块导出
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    # 枚举
    "TaskStatus",
    "TaskAction",
    "BatchStatus",
    "RecoveryActionType",
    # 异常
    "TaskSchedulerError",
    "InvalidTransitionError",
    "StoreError",
    "ConcurrencyError",
    "CheckpointError",
    "PollTimeoutError",
    "ProviderError",
    "BatchNotFoundError",
    "TaskNotFoundError",
    # 模型
    "Task",
    "Batch",
    "TransitionRecord",
    "ProgressSnapshot",
    "ResumeResult",
    "RecoveryAction",
    "PollResult",
    "TaskEvent",
    "BatchEvent",
    # 配置
    "ConcurrencyConfig",
    "PollConfig",
    "CheckpointConfig",
    "SchedulerConfig",
    # 存储
    "ITaskStore",
    "InMemoryTaskStore",
    # 子系统
    "StateMachine",
    "ProviderRateLimiter",
    "ConcurrencyController",
    "CheckpointManager",
    "ProgressTracker",
    "AsyncPoller",
    "BatchProcessor",
    # 门面
    "TaskScheduler",
]
