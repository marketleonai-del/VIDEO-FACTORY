"""
交付管理器模块 - delivery_manager.py

视频生成结果的统一交付入口，负责:
1. 文件收集 - 扫描、分类和验证视频文件
2. Drive 上传 - 创建文件夹结构并批量上传
3. 报告生成 - Google Docs + 本地 Markdown 双输出
4. 邮件通知 - HTML 格式通知邮件

核心类:
- DeliveryManager: 主控制器，编排交付流程
- FileCollector: 文件扫描、解析和验证
- DriveUploader: Drive 文件夹管理和文件上传
- ReportGenerator: 报告生成（Docs + Markdown）
- EmailNotifier: 邮件通知发送

使用示例:
    manager = DeliveryManager(workspace_client, config)
    result = await manager.deliver_batch(batch_id="batch-001")
    print(result.to_summary())
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ============================================================================
# 枚举类型
# ============================================================================


class DeliveryStatus(str, Enum):
    """交付任务生命周期状态

    状态转换图:
        PENDING -> COLLECTING -> ORGANIZING -> UPLOADING -> REPORTING -> NOTIFYING
                                                        |                        |
                                                        v                        v
                                                  [SUCCESS | PARTIAL | FAILED]
    """

    PENDING = "pending"
    COLLECTING = "collecting"
    ORGANIZING = "organizing"
    UPLOADING = "uploading"
    REPORTING = "reporting"
    NOTIFYING = "notifying"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VideoStatus(str, Enum):
    """单个视频文件的处理状态"""

    READY = "ready"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"
    SKIPPED = "skipped"


class FileCategory(str, Enum):
    """文件分类维度 - 用于 Drive 文件夹组织"""

    BY_DATE = "by_date"
    BY_BATCH = "by_batch"
    BY_STATUS = "by_status"
    BY_TEMPLATE = "by_template"
    BY_MODEL = "by_model"


# ============================================================================
# 数据模型
# ============================================================================


class VideoFile(BaseModel):
    """视频文件描述 - 交付的基本单元"""

    # 文件基本属性
    file_path: str = Field(..., description="本地文件绝对路径")
    file_name: str = Field(..., description="文件名")
    file_size: int = Field(0, description="文件大小(字节)")
    created_at: datetime = Field(
        default_factory=datetime.now, description="创建时间"
    )
    modified_at: datetime = Field(
        default_factory=datetime.now, description="修改时间"
    )

    # 业务属性
    status: VideoStatus = Field(default=VideoStatus.READY, description="处理状态")
    task_id: Optional[str] = Field(None, description="关联的任务ID")
    batch_id: Optional[str] = Field(None, description="关联的批次ID")
    gem_template: Optional[str] = Field(None, description="使用的Gem模板名称")
    model_name: Optional[str] = Field(None, description="使用的视频模型名称")

    # 元数据与Drive关联
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="额外元数据"
    )
    checksum: Optional[str] = Field(None, description="文件校验和(MD5)")
    drive_file_id: Optional[str] = Field(
        None, description="上传后的Drive文件ID"
    )
    drive_web_link: Optional[str] = Field(
        None, description="Drive预览链接"
    )

    @field_validator("file_path")
    @classmethod
    def validate_path(cls: type["VideoFile"], v: str) -> str:
        """验证文件路径存在且为绝对路径"""
        path = Path(v)
        if not path.exists():
            raise ValueError(f"文件不存在: {v}")
        return str(path.resolve())

    def compute_checksum(self: "VideoFile") -> str:
        """计算文件MD5校验和，分块读取大文件

        Returns:
            MD5哈希字符串（32位十六进制）

        Raises:
            IOError: 文件读取失败
        """
        md5 = hashlib.md5()
        with open(self.file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5.update(chunk)
        self.checksum = md5.hexdigest()
        return self.checksum

    def to_drive_name(self: "VideoFile") -> str:
        """生成Drive上的标准化文件名

        命名规则: [template]_[model]_[filename]_[task_id]

        Returns:
            标准化文件名字符串
        """
        parts: List[str] = []
        if self.gem_template:
            parts.append(self.gem_template)
        if self.model_name:
            parts.append(self.model_name)
        parts.append(self.file_name)
        if self.task_id:
            parts.append(self.task_id)
        return "_".join(parts)

    def to_dict_summary(self: "VideoFile") -> Dict[str, Any]:
        """导出摘要字典（用于报告和日志）

        Returns:
            包含关键字段的字典
        """
        return {
            "文件名": self.file_name,
            "大小": self._format_size(self.file_size),
            "模板": self.gem_template or "未指定",
            "模型": self.model_name or "未指定",
            "状态": self.status.value,
            "Drive链接": self.drive_web_link or "未上传",
        }

    @staticmethod
    def _format_size(size: int) -> str:
        """格式化文件大小为人类可读格式"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


class UploadResult(BaseModel):
    """单个文件的上传结果"""

    success: bool = Field(..., description="是否成功")
    file_path: str = Field(..., description="本地文件路径")
    file_name: str = Field(..., description="文件名")
    file_id: Optional[str] = Field(None, description="Drive文件ID")
    web_view_link: Optional[str] = Field(None, description="Drive预览链接")
    drive_folder: Optional[str] = Field(None, description="目标文件夹ID")
    file_size: int = Field(0, description="文件大小(字节)")
    uploaded_at: Optional[datetime] = Field(None, description="上传完成时间")
    error: Optional[str] = Field(None, description="错误信息")
    retry_count: int = Field(0, description="重试次数")
    duration_seconds: float = Field(0.0, description="上传耗时(秒)")


class TemplateStats(BaseModel):
    """单个模板的使用统计"""

    template_name: str = Field(..., description="模板名称")
    total_count: int = Field(0, description="使用次数")
    success_count: int = Field(0, description="成功数")
    failed_count: int = Field(0, description="失败数")
    total_size: int = Field(0, description="总文件大小(字节)")
    avg_duration: float = Field(0.0, description="平均上传耗时(秒)")


class ModelStats(BaseModel):
    """单个模型的效果统计"""

    model_name: str = Field(..., description="模型名称")
    total_count: int = Field(0, description="使用次数")
    success_count: int = Field(0, description="成功数")
    failed_count: int = Field(0, description="失败数")
    total_size: int = Field(0, description="总文件大小(字节)")
    success_rate: float = Field(0.0, description="成功率(%)")


class DeliveryStats(BaseModel):
    """交付汇总统计 - 整体任务的数据汇总"""

    total_count: int = Field(0, description="视频总数")
    success_count: int = Field(0, description="上传成功数")
    failed_count: int = Field(0, description="上传失败数")
    skipped_count: int = Field(0, description="跳过数")
    total_size: int = Field(0, description="总文件大小(字节)")
    started_at: Optional[datetime] = Field(None, description="开始时间")
    completed_at: Optional[datetime] = Field(None, description="完成时间")

    # 分类统计
    template_stats: List[TemplateStats] = Field(
        default_factory=list, description="模板统计"
    )
    model_stats: List[ModelStats] = Field(
        default_factory=list, description="模型统计"
    )

    @property
    def duration_seconds(self: "DeliveryStats") -> float:
        """计算总耗时（秒）"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    @property
    def success_rate(self: "DeliveryStats") -> float:
        """计算成功率（百分比）"""
        if self.total_count == 0:
            return 0.0
        return (self.success_count / self.total_count) * 100

    @property
    def formatted_duration(self: "DeliveryStats") -> str:
        """格式化耗时为中文可读字符串

        Returns:
            如 "1小时30分钟45秒" / "5分钟" / "30秒"
        """
        seconds = int(self.duration_seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        parts: List[str] = []
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0:
            parts.append(f"{minutes}分钟")
        if secs > 0 or not parts:
            parts.append(f"{secs}秒")
        return "".join(parts)

    def to_report_section(self: "DeliveryStats") -> str:
        """生成Markdown格式的统计摘要

        Returns:
            Markdown文本，可直接嵌入报告
        """
        lines = [
            "## 任务概览",
            "",
            f"- **视频总数**: {self.total_count}",
            f"- **上传成功**: {self.success_count} ({self.success_rate:.1f}%)",
            f"- **上传失败**: {self.failed_count}",
            f"- **已跳过**: {self.skipped_count}",
            f"- **总文件大小**: {self._format_size(self.total_size)}",
            f"- **总耗时**: {self.formatted_duration}",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def _format_size(size: int) -> str:
        """格式化字节大小"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


class DeliveryReport(BaseModel):
    """交付报告 - Google Docs和本地报告的共用数据载体"""

    batch_id: str = Field(..., description="批次ID")
    title: str = Field(..., description="报告标题")
    created_at: datetime = Field(
        default_factory=datetime.now, description="创建时间"
    )
    stats: DeliveryStats = Field(..., description="汇总统计")
    video_files: List[VideoFile] = Field(
        default_factory=list, description="视频文件列表"
    )
    upload_results: List[UploadResult] = Field(
        default_factory=list, description="上传结果列表"
    )
    doc_url: Optional[str] = Field(None, description="Google Docs报告链接")
    local_path: Optional[str] = Field(None, description="本地报告文件路径")


class DeliveryResult(BaseModel):
    """交付任务最终结果 - deliver() 方法的返回值"""

    batch_id: str = Field(..., description="批次ID")
    status: DeliveryStatus = Field(..., description="最终状态")

    # 统计
    total_count: int = Field(0, description="视频总数")
    success_count: int = Field(0, description="成功数")
    failed_count: int = Field(0, description="失败数")
    skipped_count: int = Field(0, description="跳过数")

    # 产物链接
    report_doc_url: Optional[str] = Field(None, description="报告文档链接")
    report_local_path: Optional[str] = Field(None, description="本地报告路径")
    drive_folder_url: Optional[str] = Field(None, description="Drive文件夹链接")

    # 时间和错误
    started_at: Optional[datetime] = Field(None, description="开始时间")
    completed_at: Optional[datetime] = Field(None, description="完成时间")
    errors: List[str] = Field(default_factory=list, description="错误列表")

    @property
    def duration_seconds(self: "DeliveryResult") -> float:
        """任务总耗时（秒）"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    @property
    def is_success(self: "DeliveryResult") -> bool:
        """判断任务是否成功（含部分成功）"""
        return self.status in (DeliveryStatus.SUCCESS, DeliveryStatus.PARTIAL)

    def to_summary(self: "DeliveryResult") -> str:
        """生成执行摘要（单行/多行文本）

        Returns:
            人类可读的任务摘要文本
        """
        lines = [
            f"批次 {self.batch_id} 交付完成",
            f"状态: {self.status.value}",
            f"视频: {self.success_count}/{self.total_count} 成功",
            f"耗时: {self.duration_seconds:.1f}秒",
        ]
        if self.report_doc_url:
            lines.append(f"报告: {self.report_doc_url}")
        if self.drive_folder_url:
            lines.append(f"文件夹: {self.drive_folder_url}")
        if self.errors:
            lines.append(f"错误: {len(self.errors)} 个")
        return "\n".join(lines)


# ============================================================================
# 配置模型
# ============================================================================


class ReportTemplate(BaseModel):
    """报告模板配置"""

    sections: List[str] = Field(
        default=[
            "overview",
            "template_stats",
            "model_stats",
            "video_details",
            "errors",
        ],
        description="报告章节",
    )
    style_config: Dict[str, Any] = Field(
        default_factory=dict, description="样式配置"
    )
    chart_config: Dict[str, Any] = Field(
        default_factory=dict, description="图表配置"
    )


class EmailTemplate(BaseModel):
    """邮件模板配置"""

    subject_template: str = Field(
        default="视频交付报告 - 批次 {batch_id}",
        description="邮件主题模板",
    )
    body_template: str = Field(default="", description="纯文本正文模板")
    html_template: str = Field(
        default="", description="HTML 模板（优先使用）"
    )


class DeliveryConfig(BaseModel):
    """交付模块总配置"""

    # Drive 配置
    drive_folder_id: Optional[str] = Field(
        default=None, description="Google Drive 根文件夹ID"
    )

    # 本地目录配置
    local_output_dir: str = Field(default="./output", description="本地输出目录")
    reports_dir: str = Field(
        default="./output/reports", description="报告输出目录"
    )

    # 文件解析配置
    file_naming_pattern: Optional[str] = Field(
        default=None, description="文件名解析正则表达式"
    )
    video_extensions: Optional[Set[str]] = Field(
        default=None, description="视频扩展名集合"
    )

    # 通知配置
    notify_emails: List[str] = Field(
        default_factory=list, description="通知邮箱列表"
    )
    notify_sender: Optional[str] = Field(
        default=None, description="发件人邮箱"
    )

    # 并发和重试配置
    max_workers: int = Field(
        default=3, ge=1, le=10, description="最大并发上传数"
    )
    retry_count: int = Field(
        default=3, ge=1, le=10, description="上传重试次数"
    )

    # 子配置
    report_template: Optional[ReportTemplate] = Field(
        default=None, description="报告模板配置"
    )
    email_template: Optional[EmailTemplate] = Field(
        default=None, description="邮件模板配置"
    )

    # 功能开关
    enable_drive_upload: bool = Field(default=True, description="是否启用Drive上传")
    enable_doc_report: bool = Field(
        default=True, description="是否生成Google Docs报告"
    )
    enable_email_notification: bool = Field(
        default=True, description="是否发送邮件通知"
    )
    enable_local_report: bool = Field(
        default=True, description="是否保存本地报告"
    )


# ============================================================================
# 常量
# ============================================================================

VIDEO_EXTENSIONS: Set[str] = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".flv",
    ".wmv",
    ".m4v",
    ".3gp",
}

DEFAULT_NAMING_PATTERN: re.Pattern = re.compile(
    r"(?P<task_id>[\w-]+)_(?P<template>[\w-]+)_(?P<model>[\w-]+).*\.(?P<ext>\w+)$"
)

MIN_FILE_SIZE: int = 1024  # 1KB


# ============================================================================
# FileCollector - 文件收集器
# ============================================================================


class FileCollector:
    """文件收集器

    职责:
    1. 扫描 output/ 目录及其子目录，收集视频文件
    2. 从文件名解析业务元数据（task_id, template, model）
    3. 按不同维度分类视频文件
    4. 验证文件完整性（存在性、非空、可读）
    5. 生成文件清单（JSON 格式）

    非线程安全，单线程使用即可。
    """

    def __init__(
        self,
        output_dir: str = "./output",
        naming_pattern: Optional[str] = None,
        video_extensions: Optional[Set[str]] = None,
    ) -> None:
        """初始化文件收集器

        Args:
            output_dir: 输出目录路径
            naming_pattern: 文件名解析正则表达式字符串
            video_extensions: 视频扩展名集合
        """
        self.output_dir: Path = Path(output_dir).resolve()
        self.naming_pattern: re.Pattern = (
            re.compile(naming_pattern) if naming_pattern else DEFAULT_NAMING_PATTERN
        )
        self.video_extensions: Set[str] = (
            video_extensions or VIDEO_EXTENSIONS.copy()
        )

    def scan(
        self,
        batch_id: Optional[str] = None,
        recursive: bool = True,
    ) -> List[VideoFile]:
        """扫描输出目录，收集视频文件

        Args:
            batch_id: 只收集指定批次的文件
            recursive: 是否递归扫描子目录

        Returns:
            VideoFile 列表
        """
        if not self.output_dir.exists():
            logger.warning(f"输出目录不存在: {self.output_dir}")
            return []

        videos: List[VideoFile] = []
        pattern = "**/*" if recursive else "*"

        for file_path in self.output_dir.glob(pattern):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in self.video_extensions:
                continue
            if batch_id and batch_id not in str(file_path):
                continue

            video = self._parse_file(file_path)
            if video:
                videos.append(video)

        videos.sort(key=lambda v: v.created_at, reverse=True)
        logger.info(
            f"扫描完成: {len(videos)} 个视频文件 (目录: {self.output_dir})"
        )
        return videos

    def categorize(
        self,
        videos: List[VideoFile],
        by: FileCategory = FileCategory.BY_TEMPLATE,
    ) -> List[VideoFile]:
        """对视频文件进行分类标记

        就地修改每个 VideoFile 的 metadata 字段。

        Args:
            videos: 视频文件列表
            by: 分类维度

        Returns:
            同一列表（就地修改后返回）
        """
        for video in videos:
            video.metadata["category"] = by.value
            video.metadata["category_value"] = self._get_category_value(video, by)
        return videos

    def validate(self: "FileCollector", videos: List[VideoFile]) -> List[str]:
        """验证视频文件完整性

        验证失败的文件设置 status = VideoStatus.SKIPPED。

        Args:
            videos: 视频文件列表

        Returns:
            错误信息列表
        """
        errors: List[str] = []

        for video in videos:
            path = Path(video.file_path)

            if not path.exists():
                errors.append(f"文件不存在: {video.file_name}")
                video.status = VideoStatus.SKIPPED
                continue

            if video.file_size < MIN_FILE_SIZE:
                errors.append(f"文件过小({video.file_size}B): {video.file_name}")
                video.status = VideoStatus.SKIPPED
                continue

            if not os.access(path, os.R_OK):
                errors.append(f"文件不可读: {video.file_name}")
                video.status = VideoStatus.SKIPPED
                continue

        return errors

    def generate_manifest(
        self, videos: List[VideoFile], manifest_path: str
    ) -> Path:
        """生成文件清单（JSON 格式）

        Args:
            videos: 视频文件列表
            manifest_path: 清单文件保存路径

        Returns:
            清单文件 Path 对象
        """
        manifest = {
            "generated_at": datetime.now().isoformat(),
            "total_count": len(videos),
            "total_size": sum(v.file_size for v in videos),
            "total_size_formatted": self._format_size(
                sum(v.file_size for v in videos)
            ),
            "videos": [v.model_dump(mode="json") for v in videos],
        }

        path = Path(manifest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

        logger.info(f"文件清单已保存: {path} ({len(videos)} 个文件)")
        return path

    def _parse_file(self: "FileCollector", file_path: Path) -> Optional[VideoFile]:
        """解析单个文件，提取元数据

        Args:
            file_path: 文件路径

        Returns:
            VideoFile 对象或 None
        """
        try:
            stat = file_path.stat()

            video = VideoFile(
                file_path=str(file_path),
                file_name=file_path.name,
                file_size=stat.st_size,
                created_at=datetime.fromtimestamp(stat.st_ctime),
                modified_at=datetime.fromtimestamp(stat.st_mtime),
            )

            match = self.naming_pattern.match(file_path.name)
            if match:
                groups = match.groupdict()
                video.task_id = groups.get("task_id")
                video.gem_template = groups.get("template")
                video.model_name = groups.get("model")
                video.metadata["extension"] = groups.get("ext")

            return video

        except Exception as e:
            logger.warning(f"解析文件失败 {file_path}: {e}")
            return None

    def _get_category_value(
        self, video: VideoFile, category: FileCategory
    ) -> str:
        """获取视频在指定分类维度下的值"""
        mapping: Dict[FileCategory, str] = {
            FileCategory.BY_DATE: video.created_at.strftime("%Y-%m-%d"),
            FileCategory.BY_STATUS: video.status.value,
            FileCategory.BY_TEMPLATE: video.gem_template or "unknown",
            FileCategory.BY_MODEL: video.model_name or "unknown",
            FileCategory.BY_BATCH: video.batch_id or "unknown",
        }
        return mapping.get(category, "unknown")

    @staticmethod
    def _format_size(size: int) -> str:
        """格式化字节大小"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


# ============================================================================
# DriveUploader - Drive 上传器
# ============================================================================


class DriveUploader:
    """Drive 上传器

    职责:
    1. 在 Google Drive 中创建分层文件夹结构
    2. 单个视频文件上传（含自动重试、指数退避）
    3. 并发批量上传（线程池控制）

    线程安全: upload_batch() 使用 ThreadPoolExecutor。
    重试策略: 指数退避，等待时间 = 2^attempt 秒。
    """

    def __init__(
        self,
        workspace_client: Any,
        root_folder_id: Optional[str] = None,
        concurrency: int = 3,
        retry_count: int = 3,
    ) -> None:
        """初始化 Drive 上传器

        Args:
            workspace_client: Google Workspace 客户端
            root_folder_id: Drive 根文件夹 ID
            concurrency: 并发上传数
            retry_count: 每个文件的最大重试次数
        """
        self.client = workspace_client
        self.root_folder_id = root_folder_id
        self.concurrency = concurrency
        self.retry_count = retry_count
        self._upload_log: List[Dict[str, Any]] = []

    def create_structure(
        self,
        batch_id: str,
        videos: List[VideoFile],
        categories: Optional[List[FileCategory]] = None,
    ) -> Dict[str, str]:
        """创建 Drive 文件夹结构

        文件夹层级:
            root_folder/
                YYYY-MM-DD/
                    {batch_id}/
                        uploaded/
                        failed/
                        {template_name}/
                        reports/
                        logs/

        Args:
            batch_id: 批次ID
            videos: 视频列表
            categories: 分类维度列表

        Returns:
            分类名称 -> 文件夹ID 映射字典
        """
        categories = categories or [
            FileCategory.BY_STATUS,
            FileCategory.BY_TEMPLATE,
        ]
        folder_mapping: Dict[str, str] = {}

        # 1. 创建日期文件夹
        date_str = datetime.now().strftime("%Y-%m-%d")
        date_folder = self._ensure_folder(date_str, parent=self.root_folder_id)
        folder_mapping["_date"] = date_folder

        # 2. 创建批次根文件夹
        batch_folder = self._ensure_folder(batch_id, parent=date_folder)
        folder_mapping["_root"] = batch_folder
        folder_mapping["default"] = batch_folder

        # 3. 按分类维度创建子文件夹
        for category in categories:
            values: Set[str] = {
                self._get_category_value(v, category) for v in videos
            }
            for value in values:
                folder_id = self._ensure_folder(value, parent=batch_folder)
                folder_mapping[value] = folder_id

        # 4. 创建系统文件夹
        folder_mapping["_reports"] = self._ensure_folder(
            "reports", parent=batch_folder
        )
        folder_mapping["_logs"] = self._ensure_folder(
            "logs", parent=batch_folder
        )

        logger.info(
            f"文件夹结构创建完成: {len(folder_mapping)} 个文件夹"
        )
        return folder_mapping

    def _ensure_folder(
        self, name: str, parent: Optional[str] = None
    ) -> str:
        """确保文件夹存在，不存在则创建

        Args:
            name: 文件夹名称
            parent: 父文件夹ID

        Returns:
            文件夹ID
        """
        try:
            query = f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
            if parent:
                query += f" and '{parent}' in parents"

            results = self.client._run(
                "drive",
                "files",
                "list",
                "--query",
                query,
                "--fields",
                "files(id,name)",
            )
            files = results.get("files", [])

            if files:
                existing_id = files[0]["id"]
                return existing_id

            folder_id = self.client.drive_create_folder(name, parent=parent)
            return folder_id

        except Exception as e:
            logger.warning(f"确保文件夹失败 '{name}': {e}，回退到父文件夹")
            return parent or ""

    def upload(
        self,
        video: VideoFile,
        target_folder: Optional[str] = None,
    ) -> UploadResult:
        """上传单个视频文件（含自动重试）

        重试策略:
            - 最大重试次数: retry_count
            - 退避间隔: 2^attempt 秒

        Args:
            video: 要上传的视频文件
            target_folder: 目标文件夹 ID

        Returns:
            UploadResult 上传结果
        """
        start_time = time.time()
        drive_name = video.to_drive_name()

        for attempt in range(self.retry_count):
            try:
                video.status = VideoStatus.UPLOADING

                result = self.client.drive_upload(
                    file_path=video.file_path,
                    parent_folder=target_folder,
                    name=drive_name,
                )

                duration = time.time() - start_time

                if result.success:
                    video.status = VideoStatus.UPLOADED
                    video.drive_file_id = result.file_id
                    video.drive_web_link = result.web_view_link

                    upload_result = UploadResult(
                        success=True,
                        file_path=video.file_path,
                        file_name=video.file_name,
                        file_id=result.file_id,
                        web_view_link=result.web_view_link,
                        drive_folder=target_folder,
                        file_size=video.file_size,
                        uploaded_at=datetime.now(),
                        retry_count=attempt,
                        duration_seconds=round(duration, 2),
                    )
                    self._upload_log.append(upload_result.model_dump())
                    return upload_result
                else:
                    raise Exception(result.error or "上传返回失败状态")

            except Exception as e:
                logger.warning(
                    f"上传尝试 {attempt + 1}/{self.retry_count} 失败 "
                    f"({video.file_name}): {str(e)[:100]}"
                )

                if attempt < self.retry_count - 1:
                    backoff = 2**attempt
                    time.sleep(backoff)
                else:
                    duration = time.time() - start_time
                    video.status = VideoStatus.FAILED

                    return UploadResult(
                        success=False,
                        file_path=video.file_path,
                        file_name=video.file_name,
                        error=f"{str(e)[:200]}（已重试{self.retry_count}次）",
                        retry_count=attempt,
                        duration_seconds=round(duration, 2),
                    )

        return UploadResult(
            success=False,
            file_path=video.file_path,
            file_name=video.file_name,
            error="未知错误（重试逻辑异常）",
        )

    def upload_batch(
        self,
        videos: List[VideoFile],
        folder_mapping: Dict[str, str],
    ) -> List[UploadResult]:
        """并发批量上传

        Args:
            videos: 视频列表
            folder_mapping: 文件夹名称 -> 文件夹ID 映射

        Returns:
            UploadResult 列表
        """
        results: List[UploadResult] = []

        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            future_to_video: Dict[Any, VideoFile] = {}
            for video in videos:
                folder = self._resolve_folder_for_video(video, folder_mapping)
                future = executor.submit(self.upload, video, folder)
                future_to_video[future] = video

            for future in as_completed(future_to_video):
                video = future_to_video[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(f"上传任务异常 ({video.file_name}): {e}")
                    results.append(
                        UploadResult(
                            success=False,
                            file_path=video.file_path,
                            file_name=video.file_name,
                            error=f"任务异常: {str(e)[:200]}",
                        )
                    )

        success_count = sum(1 for r in results if r.success)
        logger.info(
            f"批量上传完成: {success_count}/{len(results)} 成功 "
            f"(并发: {self.concurrency})"
        )
        return results

    def get_upload_log(self: "DriveUploader") -> List[Dict[str, Any]]:
        """获取上传日志"""
        return self._upload_log.copy()

    def _resolve_folder_for_video(
        self, video: VideoFile, folder_mapping: Dict[str, str]
    ) -> Optional[str]:
        """为视频文件确定目标文件夹

        优先级: gem_template > model_name > default
        """
        if video.gem_template and video.gem_template in folder_mapping:
            return folder_mapping[video.gem_template]
        if video.model_name and video.model_name in folder_mapping:
            return folder_mapping[video.model_name]
        return folder_mapping.get("default")

    def _get_category_value(
        self, video: VideoFile, category: FileCategory
    ) -> str:
        """获取视频的分类值"""
        mapping = {
            FileCategory.BY_DATE: video.created_at.strftime("%Y-%m-%d"),
            FileCategory.BY_STATUS: video.status.value,
            FileCategory.BY_TEMPLATE: video.gem_template or "unknown",
            FileCategory.BY_MODEL: video.model_name or "unknown",
        }
        return mapping.get(category, "unknown")


# ============================================================================
# ReportGenerator - 报告生成器
# ============================================================================


class ReportGenerator:
    """报告生成器

    双输出策略:
        Google Docs（在线协作）+ Markdown（本地备份）
        Docs 生成失败时自动降级为仅 Markdown。
    """

    def __init__(
        self,
        workspace_client: Any,
        template_config: Optional[ReportTemplate] = None,
    ) -> None:
        """初始化报告生成器

        Args:
            workspace_client: Google Workspace 客户端
            template_config: 报告模板配置
        """
        self.client = workspace_client
        self.template = template_config or ReportTemplate()

    def generate(
        self,
        batch_id: str,
        videos: List[VideoFile],
        upload_results: List[UploadResult],
        folder_mapping: Dict[str, str],
    ) -> DeliveryReport:
        """生成完整报告（Google Docs + 本地 Markdown）

        Args:
            batch_id: 批次ID
            videos: 视频文件列表
            upload_results: 上传结果列表
            folder_mapping: 文件夹映射

        Returns:
            DeliveryReport 对象
        """
        stats = self._build_stats(videos, upload_results)

        report = DeliveryReport(
            batch_id=batch_id,
            title=f"视频交付报告 - 批次 {batch_id}",
            stats=stats,
            video_files=videos,
            upload_results=upload_results,
        )

        # 生成 Google Docs 报告
        try:
            doc_url = self._generate_doc_report(report, folder_mapping)
            if doc_url:
                report.doc_url = doc_url
                logger.info(f"Google Docs 报告已生成: {doc_url}")
        except Exception as e:
            logger.warning(
                f"Google Docs 报告生成失败，将仅使用本地报告: {str(e)[:100]}"
            )

        return report

    def save_local(
        self, report: DeliveryReport, output_dir: str = "./output/reports"
    ) -> Path:
        """保存本地 Markdown 报告

        Args:
            report: 交付报告
            output_dir: 输出目录

        Returns:
            报告文件 Path 对象
        """
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)

        report_path = path / f"report-{report.batch_id}.md"
        content = self._render_markdown(report)
        report_path.write_text(content, encoding="utf-8")

        logger.info(f"本地 Markdown 报告已保存: {report_path}")
        return report_path

    def _build_stats(
        self,
        videos: List[VideoFile],
        upload_results: List[UploadResult],
    ) -> DeliveryStats:
        """构建汇总统计

        Args:
            videos: 视频文件列表
            upload_results: 对应的上传结果列表

        Returns:
            DeliveryStats 完整统计对象
        """
        stats = DeliveryStats(
            total_count=len(videos),
            started_at=datetime.now(),
        )

        # 总体统计
        for result in upload_results:
            if result.success:
                stats.success_count += 1
                stats.total_size += result.file_size
            else:
                stats.failed_count += 1

        # 模板统计
        template_map: Dict[str, TemplateStats] = {}
        for video, result in zip(videos, upload_results):
            name = video.gem_template or "未指定"
            if name not in template_map:
                template_map[name] = TemplateStats(template_name=name)
            ts = template_map[name]
            ts.total_count += 1
            if result.success:
                ts.success_count += 1
                ts.total_size += result.file_size
                ts.avg_duration += result.duration_seconds
            else:
                ts.failed_count += 1

        for ts in template_map.values():
            if ts.success_count > 0:
                ts.avg_duration = round(
                    ts.avg_duration / ts.success_count, 2
                )
        stats.template_stats = list(template_map.values())

        # 模型统计
        model_map: Dict[str, ModelStats] = {}
        for video, result in zip(videos, upload_results):
            name = video.model_name or "未指定"
            if name not in model_map:
                model_map[name] = ModelStats(model_name=name)
            ms = model_map[name]
            ms.total_count += 1
            if result.success:
                ms.success_count += 1
                ms.total_size += result.file_size
            else:
                ms.failed_count += 1

        for ms in model_map.values():
            ms.success_rate = round(
                (ms.success_count / ms.total_count * 100), 1
            ) if ms.total_count > 0 else 0.0
        stats.model_stats = list(model_map.values())

        stats.completed_at = datetime.now()
        return stats

    def _generate_doc_report(
        self,
        report: DeliveryReport,
        folder_mapping: Dict[str, str],
    ) -> Optional[str]:
        """生成 Google Docs 报告

        Args:
            report: 交付报告数据
            folder_mapping: 文件夹映射

        Returns:
            Google Docs 编辑链接，或 None
        """
        doc_title = report.title

        try:
            body = {
                "name": doc_title,
                "mimeType": "application/vnd.google-apps.document",
                "parents": [folder_mapping.get("_reports", "")],
            }
            result = self.client._run(
                "drive",
                "files",
                "create",
                "--json",
                json.dumps(body),
            )
            doc_id = result.get("id")

            if not doc_id:
                logger.warning("创建 Google Doc 未返回 ID")
                return None

            content = self._build_doc_content(report)
            self._write_doc_content(doc_id, content)

            return f"https://docs.google.com/document/d/{doc_id}/edit"

        except Exception as e:
            logger.error(f"生成 Google Docs 报告失败: {e}")
            return None

    def _build_doc_content(
        self, report: DeliveryReport
    ) -> List[Dict[str, Any]]:
        """构建 Google Docs API batchUpdate 请求内容"""
        stats = report.stats
        requests: List[Dict[str, Any]] = []

        # 标题
        requests.append(
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": f"{report.title}\n",
                }
            }
        )
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": {
                        "startIndex": 1,
                        "endIndex": len(report.title) + 1,
                    },
                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                    "fields": "namedStyleType",
                }
            }
        )

        # 基本信息
        requests.append(
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": f"\n生成时间: {report.created_at:%Y-%m-%d %H:%M:%S}\n",
                }
            }
        )
        requests.append(
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": f"批次ID: {report.batch_id}\n\n",
                }
            }
        )

        # 统计摘要
        summary = (
            "任务概览\n"
            f"- 视频总数: {stats.total_count}\n"
            f"- 上传成功: {stats.success_count} ({stats.success_rate:.1f}%)\n"
            f"- 上传失败: {stats.failed_count}\n"
            f"- 总耗时: {stats.formatted_duration}\n\n"
        )
        requests.append(
            {"insertText": {"location": {"index": 1}, "text": summary}}
        )

        # 模板统计
        if stats.template_stats:
            requests.append(
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": "模板使用统计\n",
                    }
                }
            )
            for ts in stats.template_stats:
                requests.append(
                    {
                        "insertText": {
                            "location": {"index": 1},
                            "text": (
                                f"- {ts.template_name}: "
                                f"{ts.total_count}次, "
                                f"成功{ts.success_count}, "
                                f"失败{ts.failed_count}\n"
                            ),
                        }
                    }
                )
            requests.append(
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": "\n",
                    }
                }
            )

        # 视频详情
        requests.append(
            {
                "insertText": {
                    "location": {"index": 1},
                    "text": "视频详情\n",
                }
            }
        )
        for video in report.video_files:
            status_icon = "✓" if video.status.value == "uploaded" else "✗"
            line = (
                f"{status_icon} {video.file_name} — "
                f"{video.gem_template or 'N/A'} / "
                f"{video.model_name or 'N/A'}"
                f"{' (' + video.drive_web_link + ')' if video.drive_web_link else ''}\n"
            )
            requests.append(
                {"insertText": {"location": {"index": 1}, "text": line}}
            )

        # 错误汇总
        failed = [r for r in report.upload_results if not r.success]
        if failed:
            requests.append(
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": "\n错误汇总\n",
                    }
                }
            )
            for r in failed:
                requests.append(
                    {
                        "insertText": {
                            "location": {"index": 1},
                            "text": f"- {r.file_name}: {r.error or '未知错误'}\n",
                        }
                    }
                )

        return requests

    def _write_doc_content(
        self, doc_id: str, requests: List[Dict[str, Any]]
    ) -> None:
        """写入 Google Docs 内容"""
        body = json.dumps({"requests": requests})
        self.client._run(
            "docs",
            "documents",
            "batchUpdate",
            "--document-id",
            doc_id,
            "--json",
            body,
        )

    def _render_markdown(self: "ReportGenerator", report: DeliveryReport) -> str:
        """渲染 Markdown 格式报告

        Args:
            report: 交付报告

        Returns:
            完整 Markdown 文本
        """
        stats = report.stats
        lines: List[str] = []

        # 标题
        lines.append(f"# {report.title}")
        lines.append("")
        lines.append(f"> 生成时间: {report.created_at:%Y-%m-%d %H:%M:%S}")
        lines.append(f"> 批次ID: {report.batch_id}")
        lines.append("")

        # 任务概览
        lines.append("## 任务概览")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 视频总数 | {stats.total_count} |")
        lines.append(
            f"| 上传成功 | {stats.success_count} ({stats.success_rate:.1f}%) |"
        )
        lines.append(f"| 上传失败 | {stats.failed_count} |")
        lines.append(f"| 已跳过 | {stats.skipped_count} |")
        lines.append(f"| 总文件大小 | {self._format_size(stats.total_size)} |")
        lines.append(f"| 总耗时 | {stats.formatted_duration} |")
        lines.append("")

        # 模板使用统计
        if stats.template_stats:
            lines.append("## 模板使用统计")
            lines.append("")
            lines.append(
                "| 模板名称 | 使用次数 | 成功 | 失败 | 平均耗时 |"
            )
            lines.append("|----------|----------|------|------|----------|")
            for ts in stats.template_stats:
                lines.append(
                    f"| {ts.template_name} | {ts.total_count} | "
                    f"{ts.success_count} | {ts.failed_count} | "
                    f"{ts.avg_duration:.1f}s |"
                )
            lines.append("")

        # 模型效果统计
        if stats.model_stats:
            lines.append("## 模型效果统计")
            lines.append("")
            lines.append(
                "| 模型名称 | 使用次数 | 成功 | 失败 | 成功率 |"
            )
            lines.append("|----------|----------|------|------|--------|")
            for ms in stats.model_stats:
                lines.append(
                    f"| {ms.model_name} | {ms.total_count} | "
                    f"{ms.success_count} | {ms.failed_count} | "
                    f"{ms.success_rate:.1f}% |"
                )
            lines.append("")

        # 视频详情
        lines.append("## 视频详情")
        lines.append("")
        lines.append(
            "| # | 文件名 | 模板 | 模型 | 大小 | 状态 | Drive链接 |"
        )
        lines.append(
            "|---|--------|------|------|------|------|-----------|"
        )
        for i, video in enumerate(report.video_files, 1):
            status = "成功" if video.status.value == "uploaded" else "失败"
            size = self._format_size(video.file_size)
            link = video.drive_web_link or "—"
            lines.append(
                f"| {i} | {video.file_name} | "
                f"{video.gem_template or 'N/A'} | "
                f"{video.model_name or 'N/A'} | {size} | {status} | {link} |"
            )
        lines.append("")

        # 错误汇总
        failed = [r for r in report.upload_results if not r.success]
        if failed:
            lines.append("## 错误汇总")
            lines.append("")
            for r in failed:
                lines.append(f"- **{r.file_name}**: {r.error or '未知错误'}")
            lines.append("")

        # 页脚
        lines.append("---")
        lines.append("")
        lines.append("*本报告由 300 Agent 融合系统自动生成*")
        lines.append("")

        if report.doc_url:
            lines.append(f"[查看在线报告]({report.doc_url})")

        return "\n".join(lines)

    @staticmethod
    def _format_size(size: int) -> str:
        """格式化字节大小"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


# ============================================================================
# EmailNotifier - 邮件通知器
# ============================================================================


class EmailNotifier:
    """邮件通知器

    职责:
    1. 渲染 HTML 邮件模板（内联样式，兼容主流邮件客户端）
    2. 发送单条/批量交付结果通知
    3. 邮件内容包含统计摘要、视频详情表格和链接

    降级策略:
        单封邮件发送失败不影响其他邮件。
        邮件发送失败不阻断交付流程。
    """

    def __init__(
        self,
        workspace_client: Any,
        default_sender: Optional[str] = None,
        template_config: Optional[EmailTemplate] = None,
    ) -> None:
        """初始化邮件通知器

        Args:
            workspace_client: Google Workspace 客户端
            default_sender: 默认发件人邮箱
            template_config: 邮件模板配置
        """
        self.client = workspace_client
        self.default_sender = default_sender
        self.template = template_config or EmailTemplate()

    def send(
        self,
        to: str,
        report: DeliveryReport,
        batch_id: str,
        subject: Optional[str] = None,
    ) -> bool:
        """发送单条通知邮件

        Args:
            to: 收件人邮箱地址
            report: 交付报告
            batch_id: 批次ID
            subject: 自定义主题

        Returns:
            是否发送成功
        """
        try:
            subject = subject or self._render_subject(report, batch_id)
            body = self._render_html_body(report)

            result = self.client.gmail_send(
                to=to,
                subject=subject,
                body=body,
                html=True,
            )

            if result.success:
                logger.info(f"通知邮件已发送至 {to}")
                return True
            else:
                logger.warning(f"邮件发送失败 ({to}): {result.error}")
                return False

        except Exception as e:
            logger.error(f"发送通知邮件异常 ({to}): {e}")
            return False

    def send_batch(
        self,
        recipients: List[str],
        report: DeliveryReport,
        batch_id: str,
    ) -> Dict[str, bool]:
        """批量发送通知邮件

        Args:
            recipients: 收件人邮箱列表
            report: 交付报告
            batch_id: 批次ID

        Returns:
            邮箱 -> 是否成功 的字典映射
        """
        results: Dict[str, bool] = {}
        for email in recipients:
            results[email] = self.send(email, report, batch_id)
        return results

    def _render_subject(self: "EmailNotifier", report: DeliveryReport, batch_id: str) -> str:
        """渲染邮件主题

        格式: [状态图标] 视频交付报告 - 批次 {batch_id} ({成功数}/{总数} 成功)
        """
        stats = report.stats
        if stats.failed_count == 0:
            status_icon = "✓"
        elif stats.success_count > 0:
            status_icon = "!"
        else:
            status_icon = "✗"

        return (
            f"[{status_icon}] 视频交付报告 - 批次 {batch_id} "
            f"({stats.success_count}/{stats.total_count} 成功)"
        )

    def _render_html_body(self: "EmailNotifier", report: DeliveryReport) -> str:
        """渲染 HTML 邮件正文

        Returns:
            完整 HTML 字符串
        """
        stats = report.stats

        # 状态颜色和文字
        if stats.failed_count == 0:
            status_color = "#28a745"
            status_text = "全部成功"
        elif stats.success_count > 0:
            status_color = "#ffc107"
            status_text = "部分成功"
        else:
            status_color = "#dc3545"
            status_text = "全部失败"

        # 视频行渲染
        video_rows = ""
        for video in report.video_files:
            icon = "✓" if video.status.value == "uploaded" else "✗"
            link = (
                f'<a href="{video.drive_web_link}" style="color:#1a73e8;">查看</a>'
                if video.drive_web_link
                else "—"
            )
            size = self._format_size(video.file_size)
            video_rows += f"""
                <tr>
                    <td style="padding:8px;border:1px solid #ddd;text-align:center;">{icon}</td>
                    <td style="padding:8px;border:1px solid #ddd;font-size:13px;">{video.file_name}</td>
                    <td style="padding:8px;border:1px solid #ddd;">{video.gem_template or 'N/A'}</td>
                    <td style="padding:8px;border:1px solid #ddd;">{video.model_name or 'N/A'}</td>
                    <td style="padding:8px;border:1px solid #ddd;font-size:12px;color:#666;">{size}</td>
                    <td style="padding:8px;border:1px solid #ddd;text-align:center;">{link}</td>
                </tr>
            """

        # 模板统计行
        template_rows = ""
        for ts in stats.template_stats:
            template_rows += f"""
                <tr>
                    <td style="padding:8px;border:1px solid #ddd;">{ts.template_name}</td>
                    <td style="padding:8px;border:1px solid #ddd;text-align:center;">{ts.total_count}</td>
                    <td style="padding:8px;border:1px solid #ddd;text-align:center;color:#28a745;">{ts.success_count}</td>
                    <td style="padding:8px;border:1px solid #ddd;text-align:center;color:#dc3545;">{ts.failed_count}</td>
                </tr>
            """

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>视频交付报告 - {report.batch_id}</title>
</head>
<body style="margin:0;padding:0;background-color:#f5f5f5;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background-color:#f5f5f5;">
        <tr>
            <td align="center" style="padding:20px 0;">
                <table role="presentation" cellpadding="0" cellspacing="0" width="800" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">

                    <!-- 头部横幅 -->
                    <tr>
                        <td style="background-color:{status_color};padding:24px 30px;color:#ffffff;">
                            <h1 style="margin:0;font-size:24px;font-weight:bold;">视频交付报告</h1>
                            <p style="margin:8px 0 0 0;font-size:14px;opacity:0.95;">
                                批次: <strong>{report.batch_id}</strong> |
                                状态: <strong>{status_text}</strong> |
                                时间: {report.created_at:%Y-%m-%d %H:%M}
                            </p>
                        </td>
                    </tr>

                    <!-- 统计卡片 -->
                    <tr>
                        <td style="padding:20px 30px;">
                            <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
                                <tr>
                                    <td width="25%" style="padding:8px;">
                                        <div style="background:#f8f9fa;padding:16px;border-radius:8px;text-align:center;">
                                            <div style="font-size:28px;font-weight:bold;color:#333;">{stats.total_count}</div>
                                            <div style="font-size:12px;color:#666;margin-top:4px;">视频总数</div>
                                        </div>
                                    </td>
                                    <td width="25%" style="padding:8px;">
                                        <div style="background:#f8f9fa;padding:16px;border-radius:8px;text-align:center;">
                                            <div style="font-size:28px;font-weight:bold;color:#28a745;">{stats.success_count}</div>
                                            <div style="font-size:12px;color:#666;margin-top:4px;">上传成功</div>
                                        </div>
                                    </td>
                                    <td width="25%" style="padding:8px;">
                                        <div style="background:#f8f9fa;padding:16px;border-radius:8px;text-align:center;">
                                            <div style="font-size:28px;font-weight:bold;color:#dc3545;">{stats.failed_count}</div>
                                            <div style="font-size:12px;color:#666;margin-top:4px;">上传失败</div>
                                        </div>
                                    </td>
                                    <td width="25%" style="padding:8px;">
                                        <div style="background:#f8f9fa;padding:16px;border-radius:8px;text-align:center;">
                                            <div style="font-size:28px;font-weight:bold;color:#333;">{stats.formatted_duration}</div>
                                            <div style="font-size:12px;color:#666;margin-top:4px;">总耗时</div>
                                        </div>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                    <!-- 详细统计 -->
                    <tr>
                        <td style="padding:0 30px 20px 30px;">
                            <h2 style="font-size:16px;color:#333;border-bottom:2px solid #eee;padding-bottom:8px;">统计摘要</h2>
                            <p style="font-size:14px;color:#555;line-height:1.8;">
                                <strong>成功率:</strong> {stats.success_rate:.1f}%<br>
                                <strong>总文件大小:</strong> {self._format_size(stats.total_size)}<br>
                                <strong>完成时间:</strong> {report.created_at:%Y-%m-%d %H:%M:%S}
                            </p>
                        </td>
                    </tr>

                    <!-- 模板统计 -->
                    {f"""
                    <tr>
                        <td style="padding:0 30px 20px 30px;">
                            <h2 style="font-size:16px;color:#333;border-bottom:2px solid #eee;padding-bottom:8px;">模板使用统计</h2>
                            <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">
                                <tr style="background:#f0f0f0;">
                                    <th style="padding:10px;border:1px solid #ddd;text-align:left;font-size:13px;">模板名称</th>
                                    <th style="padding:10px;border:1px solid #ddd;text-align:center;font-size:13px;">使用次数</th>
                                    <th style="padding:10px;border:1px solid #ddd;text-align:center;font-size:13px;">成功</th>
                                    <th style="padding:10px;border:1px solid #ddd;text-align:center;font-size:13px;">失败</th>
                                </tr>
                                {template_rows}
                            </table>
                        </td>
                    </tr>
                    """ if stats.template_stats else ""}

                    <!-- 视频详情表格 -->
                    <tr>
                        <td style="padding:0 30px 20px 30px;">
                            <h2 style="font-size:16px;color:#333;border-bottom:2px solid #eee;padding-bottom:8px;">视频详情</h2>
                            <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">
                                <tr style="background:#f0f0f0;">
                                    <th style="padding:10px;border:1px solid #ddd;text-align:center;font-size:13px;width:40px;">状态</th>
                                    <th style="padding:10px;border:1px solid #ddd;text-align:left;font-size:13px;">文件名</th>
                                    <th style="padding:10px;border:1px solid #ddd;text-align:left;font-size:13px;">模板</th>
                                    <th style="padding:10px;border:1px solid #ddd;text-align:left;font-size:13px;">模型</th>
                                    <th style="padding:10px;border:1px solid #ddd;text-align:left;font-size:13px;">大小</th>
                                    <th style="padding:10px;border:1px solid #ddd;text-align:center;font-size:13px;">链接</th>
                                </tr>
                                {video_rows}
                            </table>
                        </td>
                    </tr>

                    <!-- 页脚 -->
                    <tr>
                        <td style="padding:20px 30px;background:#f8f9fa;border-top:1px solid #eee;">
                            <p style="margin:0;font-size:12px;color:#888;text-align:center;">
                                本邮件由 300 Agent 融合系统自动发送<br>
                                如需查看完整报告，请访问 Google Drive 文件夹
                            </p>
                        </td>
                    </tr>

                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""
        return html

    @staticmethod
    def _format_size(size: int) -> str:
        """格式化字节大小"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


# ============================================================================
# DeliveryManager - 交付管理器主类
# ============================================================================


class DeliveryManager:
    """交付管理器 - 视频生成结果的统一交付入口

    状态机:
        PENDING -> COLLECTING -> ORGANIZING -> UPLOADING -> REPORTING -> NOTIFYING
                                                                            |
                                                                            v
                                                                [SUCCESS | PARTIAL | FAILED]

    错误处理策略:
        - 文件收集失败: 返回 FAILED 结果
        - 上传失败: 自动重试，仍失败则标记该文件失败但继续
        - 报告生成失败（Google Docs）: 降级为仅本地 Markdown 报告
        - 邮件发送失败: 记录警告，不阻断流程
        - 整体异常: 捕获后返回 FAILED 结果

    使用示例:
        manager = DeliveryManager(workspace_client, config)
        result = await manager.deliver_batch(batch_id="batch-001")
        print(result.to_summary())
    """

    def __init__(
        self,
        workspace_client: Any,
        config: Optional[DeliveryConfig] = None,
        progress_callback: Optional[
            Callable[[DeliveryStatus, str], None]
        ] = None,
    ) -> None:
        """初始化交付管理器

        Args:
            workspace_client: Google Workspace 客户端实例
            config: 交付配置
            progress_callback: 进度回调函数 (DeliveryStatus, str) -> None
        """
        self.workspace_client = workspace_client
        self.config = config or DeliveryConfig()
        self.progress_callback = progress_callback

        # 初始化子组件
        self.collector = FileCollector(
            output_dir=self.config.local_output_dir,
            naming_pattern=self.config.file_naming_pattern,
            video_extensions=self.config.video_extensions,
        )
        self.uploader = DriveUploader(
            workspace_client=workspace_client,
            root_folder_id=self.config.drive_folder_id,
            concurrency=self.config.max_workers,
            retry_count=self.config.retry_count,
        )
        self.reporter = ReportGenerator(
            workspace_client=workspace_client,
            template_config=self.config.report_template,
        )
        self.notifier = EmailNotifier(
            workspace_client=workspace_client,
            default_sender=self.config.notify_sender,
            template_config=self.config.email_template,
        )

        # 状态管理
        self._state: DeliveryStatus = DeliveryStatus.PENDING
        self._current_batch: Optional[str] = None
        self._cancelled: bool = False

    async def deliver_batch(
        self, batch_id: Optional[str] = None
    ) -> DeliveryResult:
        """执行完整交付流程（主入口，异步）

        编排整个交付流水线:
        1. 收集文件 -> 2. 创建文件夹 -> 3. 批量上传 -> 4. 生成报告 -> 5. 发送通知

        Args:
            batch_id: 批次ID，None 时自动生成

        Returns:
            DeliveryResult: 交付结果
        """
        batch_id = batch_id or f"batch-{datetime.now():%Y%m%d-%H%M%S}"
        self._current_batch = batch_id
        self._cancelled = False
        started_at = datetime.now()

        logger.info(f"[批次 {batch_id}] 交付流程开始")

        try:
            # Step 1: 文件收集
            self._transition(DeliveryStatus.COLLECTING, "正在收集视频文件...")
            videos = await self._collect_files(batch_id)
            if not videos:
                logger.warning(f"[批次 {batch_id}] 未找到视频文件")
                return self._build_result(
                    batch_id=batch_id,
                    started_at=started_at,
                    upload_results=[],
                    errors=["未找到视频文件"],
                    status=DeliveryStatus.FAILED,
                )

            # Step 2: 创建 Drive 文件夹结构
            self._transition(
                DeliveryStatus.ORGANIZING, "正在创建 Drive 文件夹..."
            )
            folder_mapping = await self._create_drive_structure(
                batch_id, videos
            )

            # Step 3: 批量上传
            self._transition(
                DeliveryStatus.UPLOADING,
                f"正在上传 {len(videos)} 个视频...",
            )
            upload_results = await self._upload_videos(videos, folder_mapping)

            # Step 4: 生成报告
            self._transition(DeliveryStatus.REPORTING, "正在生成汇总报告...")
            report = await self._generate_report(
                batch_id, videos, upload_results, folder_mapping
            )

            # Step 5: 发送邮件通知
            if self.config.notify_emails:
                self._transition(
                    DeliveryStatus.NOTIFYING, "正在发送邮件通知..."
                )
                await self._send_notification(report)

            # 完成: 确定最终状态
            status = self._determine_status(upload_results)
            self._transition(status, f"交付完成: {status.value}")

            errors = [
                r.error
                for r in upload_results
                if not r.success and r.error
            ]
            return self._build_result(
                batch_id=batch_id,
                started_at=started_at,
                upload_results=upload_results,
                errors=errors,
                report=report,
                folder_mapping=folder_mapping,
                status=status,
            )

        except Exception as e:
            logger.exception(f"[批次 {batch_id}] 交付流程异常")
            self._transition(DeliveryStatus.FAILED, str(e))
            return self._build_result(
                batch_id=batch_id,
                started_at=started_at,
                upload_results=[],
                errors=[str(e)],
                status=DeliveryStatus.FAILED,
            )

    def get_state(self: "DeliveryManager") -> DeliveryStatus:
        """获取当前交付状态"""
        return self._state

    def cancel(self: "DeliveryManager") -> None:
        """取消当前交付任务

        设置取消标志位，正在上传的文件完成后流程将终止。
        """
        self._cancelled = True
        logger.info(f"[批次 {self._current_batch}] 交付任务已请求取消")

    # 内部流程方法

    async def _collect_files(self: "DeliveryManager", batch_id: str) -> List[VideoFile]:
        """Step 1: 收集并验证视频文件"""
        videos = self.collector.scan(batch_id=batch_id)

        if videos:
            videos = self.collector.categorize(
                videos, by=FileCategory.BY_TEMPLATE
            )
            validation_errors = self.collector.validate(videos)
            if validation_errors:
                logger.warning(
                    f"[批次 {batch_id}] 文件验证发现 {len(validation_errors)} 个问题"
                )

            for video in videos:
                if video.status != VideoStatus.SKIPPED:
                    try:
                        video.compute_checksum()
                    except Exception as e:
                        logger.warning(
                            f"校验和计算失败 {video.file_name}: {e}"
                        )

            logger.info(
                f"[批次 {batch_id}] 收集到 {len(videos)} 个视频文件"
                f"（有效: {sum(1 for v in videos if v.status != VideoStatus.SKIPPED)}）"
            )

        return videos

    async def _create_drive_structure(
        self, batch_id: str, videos: List[VideoFile]
    ) -> Dict[str, str]:
        """Step 2: 创建 Drive 文件夹结构"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.uploader.create_structure,
            batch_id,
            videos,
            [FileCategory.BY_STATUS, FileCategory.BY_TEMPLATE],
        )

    async def _upload_videos(
        self,
        videos: List[VideoFile],
        folder_mapping: Dict[str, str],
    ) -> List[UploadResult]:
        """Step 3: 批量上传视频到 Google Drive"""
        semaphore = asyncio.Semaphore(self.config.max_workers)

        async def upload_one(
            video: VideoFile, index: int
        ) -> UploadResult:
            if self._cancelled:
                return UploadResult(
                    success=False,
                    file_path=video.file_path,
                    file_name=video.file_name,
                    error="任务已取消",
                )

            async with semaphore:
                target_folder = self._resolve_target_folder(
                    video, folder_mapping
                )
                loop = asyncio.get_event_loop()
                result: UploadResult = await loop.run_in_executor(
                    None, self.uploader.upload, video, target_folder
                )

                if self.progress_callback:
                    self.progress_callback(
                        DeliveryStatus.UPLOADING,
                        f"上传进度: {index + 1}/{len(videos)} - {video.file_name}",
                    )

                return result

        tasks = [upload_one(video, i) for i, video in enumerate(videos)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed_results: List[UploadResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"上传任务异常 ({videos[i].file_name}): {result}"
                )
                processed_results.append(
                    UploadResult(
                        success=False,
                        file_path=videos[i].file_path,
                        file_name=videos[i].file_name,
                        error=str(result),
                    )
                )
            else:
                processed_results.append(result)

        success_count = sum(1 for r in processed_results if r.success)
        logger.info(f"批量上传完成: {success_count}/{len(videos)} 成功")
        return processed_results

    async def _generate_report(
        self,
        batch_id: str,
        videos: List[VideoFile],
        upload_results: List[UploadResult],
        folder_mapping: Dict[str, str],
    ) -> DeliveryReport:
        """Step 4: 生成汇总报告"""
        loop = asyncio.get_event_loop()

        report: DeliveryReport = await loop.run_in_executor(
            None,
            self.reporter.generate,
            batch_id,
            videos,
            upload_results,
            folder_mapping,
        )

        # 保存本地报告（降级保障）
        try:
            local_path = await loop.run_in_executor(
                None, self.reporter.save_local, report
            )
            report.local_path = str(local_path)
            logger.info(f"本地报告已保存: {local_path}")
        except Exception as e:
            logger.error(f"本地报告保存失败: {e}")

        return report

    async def _send_notification(self: "DeliveryManager", report: DeliveryReport) -> None:
        """Step 5: 发送邮件通知"""
        for email in self.config.notify_emails:
            try:
                loop = asyncio.get_event_loop()
                success = await loop.run_in_executor(
                    None,
                    self.notifier.send,
                    email,
                    report,
                    report.batch_id,
                )
                if success:
                    logger.info(f"通知邮件已发送至 {email}")
                else:
                    logger.warning(f"通知邮件发送失败: {email}")
            except Exception as e:
                logger.warning(f"通知邮件发送异常 ({email}): {e}")

    # 辅助方法

    def _resolve_target_folder(
        self,
        video: VideoFile,
        folder_mapping: Dict[str, str],
    ) -> Optional[str]:
        """根据视频属性确定目标文件夹

        解析优先级（从高到低）:
            1. 按状态分类
            2. 按模板分类
            3. 按模型分类
            4. 默认: 批次根文件夹
        """
        if video.status.value in folder_mapping:
            return folder_mapping[video.status.value]
        if video.gem_template and video.gem_template in folder_mapping:
            return folder_mapping[video.gem_template]
        if video.model_name and video.model_name in folder_mapping:
            return folder_mapping[video.model_name]
        return folder_mapping.get("default")

    def _determine_status(
        self, results: List[UploadResult]
    ) -> DeliveryStatus:
        """根据上传结果确定最终状态"""
        if not results:
            return DeliveryStatus.FAILED

        success_count = sum(1 for r in results if r.success)

        if success_count == len(results):
            return DeliveryStatus.SUCCESS
        if success_count > 0:
            return DeliveryStatus.PARTIAL
        return DeliveryStatus.FAILED

    def _transition(
        self, new_state: DeliveryStatus, message: str
    ) -> None:
        """执行状态转换

        副作用:
            1. 更新 self._state
            2. 记录日志
            3. 调用 progress_callback（如果已设置）
        """
        old_state = self._state
        self._state = new_state
        logger.info(
            f"状态转换: {old_state.value} -> {new_state.value} | {message}"
        )
        if self.progress_callback:
            try:
                self.progress_callback(new_state, message)
            except Exception as e:
                logger.warning(f"进度回调异常: {e}")

    def _build_result(
        self,
        batch_id: str,
        started_at: datetime,
        upload_results: List[UploadResult],
        errors: List[str],
        report: Optional[DeliveryReport] = None,
        folder_mapping: Optional[Dict[str, str]] = None,
        status: Optional[DeliveryStatus] = None,
    ) -> DeliveryResult:
        """构建 DeliveryResult 结果对象"""
        success_count = sum(1 for r in upload_results if r.success)
        failed_count = sum(1 for r in upload_results if not r.success)
        skipped_count = sum(
            1 for r in upload_results if r.error and "跳过" in r.error
        )

        return DeliveryResult(
            batch_id=batch_id,
            status=status or self._state,
            total_count=len(upload_results),
            success_count=success_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            report_doc_url=report.doc_url if report else None,
            report_local_path=report.local_path if report else None,
            drive_folder_url=folder_mapping.get("_root")
            if folder_mapping
            else None,
            started_at=started_at,
            completed_at=datetime.now(),
            errors=errors,
        )
