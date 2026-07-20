"""
drive_manager.py — Google Drive 管理器

封装 Drive 文件上传、文件夹管理和批量上传功能。
支持基于内容哈希的文件去重、并发上传控制和文件夹路径自动创建。

用法:
    >>> from workspace_client import WorkspaceClient
    >>> client = await WorkspaceClient.from_service_account("/path/to/sa.json")
    >>> drive = client.drive
    >>> result = await drive.upload("./video.mp4", folder_id="FOLDER_ID")
    >>> print(result.file_id, result.web_view_link)
    >>> folder = await drive.create_folder("2026-01")
    >>> results = await drive.bulk_upload(["./a.mp4", "./b.mp4"])

Python 3.11+ | Pydantic v2 | asyncio
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════════
# 从 workspace_models 导入共享组件（打破循环导入）
# ═══════════════════════════════════════════════════════════

from workspace_models import (
    BaseManager,
    BulkOperationError,
    BulkUploadResult,
    ClientConfig,
    DriveApiError,
    FolderInfo,
    GwsExitCodeError,
    UploadResult,
    ValidationError,
)

logger = logging.getLogger(__name__)


class DriveManager(BaseManager):
    """Google Drive 操作管理器

    提供文件上传、文件夹管理和批量上传功能。
    支持基于内容哈希的文件去重和并发上传控制。

    Args:
        config: 客户端配置
        runner: gws 命令执行器
        root_folder_id: Drive 根文件夹 ID（可选）

    Attributes:
        root_folder_id: 默认上传目标文件夹 ID
        folder_cache: 文件夹路径到 ID 的缓存

    Example:
        >>> drive = client.drive
        >>> result = await drive.upload("./video.mp4", folder_id="FOLDER_ID")
        >>> print(result.file_id, result.web_view_link)
    """

    SCOPES = ["https://www.googleapis.com/auth/drive"]

    def __init__(
        self,
        config: ClientConfig,
        runner: Any,
        root_folder_id: Optional[str] = None,
    ) -> None:
        super().__init__(config, runner)
        self._root_folder_id = root_folder_id
        self._concurrency = config.drive_concurrency
        self._chunk_size = config.drive_chunk_size
        # 文件夹缓存: "folder_name:parent_id" -> folder_id
        self._folder_cache: Dict[str, str] = {}
        # 文件去重缓存: md5_hash -> drive_file_id
        self._hash_cache: Dict[str, str] = {}

    # ── 文件上传 ──────────────────────────────────────────────

    async def upload(
        self,
        file_path: str,
        folder_id: Optional[str] = None,
        file_name: Optional[str] = None,
        mime_type: Optional[str] = None,
        description: Optional[str] = None,
    ) -> UploadResult:
        """上传文件到 Google Drive

        使用 gws drive +upload helper 上传文件。
        支持自动 MIME 类型推断和自定义文件名。

        Args:
            file_path: 本地文件路径
            folder_id: 目标文件夹 ID，默认使用 root_folder_id
            file_name: 上传后的文件名，默认使用原文件名
            mime_type: MIME 类型，默认自动推断
            description: 文件描述

        Returns:
            UploadResult 包含上传结果和文件元信息

        Raises:
            ValidationError: 文件路径无效
            DriveApiError: 上传失败

        Example:
            >>> result = await drive.upload("./video.mp4", folder_id="FOLDER_ID")
            >>> print(result.file_id, result.web_view_link)
        """
        path = Path(file_path)
        if not path.exists():
            raise ValidationError(
                f"文件不存在: {file_path}",
                field="file_path",
                value=file_path,
            )
        if not path.is_file():
            raise ValidationError(
                f"路径不是文件: {file_path}",
                field="file_path",
                value=file_path,
            )

        target_folder = folder_id or self._root_folder_id
        name = file_name or path.name
        mime = mime_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        file_size = path.stat().st_size

        # 计算 MD5（用于去重和结果填充）
        md5_hash = await self._compute_md5_async(str(path))

        # 检查去重缓存
        if md5_hash in self._hash_cache:
            existing_id = self._hash_cache[md5_hash]
            logger.info(f"文件去重命中: {name} -> {existing_id}")
            return UploadResult(
                success=True,
                operation="drive.upload.dedup",
                file_id=existing_id,
                file_name=name,
                mime_type=mime,
                file_size=file_size,
                md5_hash=md5_hash,
            )

        start_time = asyncio.get_event_loop().time()

        # 构建命令参数
        cmd_args = [
            "drive", "+upload", str(path),
            "--name", name,
        ]
        if target_folder:
            cmd_args.extend(["--parent", target_folder])
        if description:
            cmd_args.extend(["--description", description])

        try:
            result = await self._run_with_retry(*cmd_args)
        except GwsExitCodeError as e:
            raise DriveApiError(
                message=f"上传文件失败: {e.message}",
                status_code=400,
                operation="drive.upload",
                file_path=file_path,
                file_name=name,
            ) from e

        # 解析返回的文件 ID
        file_id = result.get("id") or result.get("fileId")
        web_link = result.get("webViewLink") or result.get("webContentLink")

        if not file_id:
            file_id = result.get("_results", [{}])[0].get("id") if "_results" in result else None

        upload_duration = asyncio.get_event_loop().time() - start_time

        # 注册到去重缓存
        if file_id:
            self._hash_cache[md5_hash] = file_id

        return UploadResult(
            success=file_id is not None,
            operation="drive.upload",
            file_id=file_id,
            file_name=name,
            mime_type=mime,
            file_size=file_size,
            web_view_link=web_link,
            upload_duration_sec=upload_duration,
            md5_hash=md5_hash,
            raw_response=result,
        )

    # ── 批量上传 ──────────────────────────────────────────────

    async def bulk_upload(
        self,
        file_paths: List[str],
        folder_id: Optional[str] = None,
        file_names: Optional[List[str]] = None,
        max_concurrency: Optional[int] = None,
        skip_duplicates: bool = True,
    ) -> BulkUploadResult:
        """批量上传文件（并发控制 + 去重）

        Args:
            file_paths: 本地文件路径列表
            folder_id: 目标文件夹 ID
            file_names: 对应的文件名列表（可选）
            max_concurrency: 最大并发数，默认使用配置值
            skip_duplicates: 是否跳过重复文件

        Returns:
            BulkUploadResult 汇总所有上传结果

        Example:
            >>> files = ["./a.mp4", "./b.mp4", "./c.mp4"]
            >>> result = await drive.bulk_upload(files, folder_id="ID")
            >>> print(f"成功率: {result.success_rate:.1%}")
        """
        if not file_paths:
            return BulkUploadResult(total_files=0)

        concurrency = max_concurrency or self._concurrency
        target_folder = folder_id or self._root_folder_id
        total_files = len(file_paths)

        semaphore = asyncio.Semaphore(concurrency)
        errors: List[str] = []

        async def _upload_one(
            idx: int, path: str, name: Optional[str] = None
        ) -> UploadResult:
            async with semaphore:
                try:
                    return await self.upload(
                        file_path=path,
                        folder_id=target_folder,
                        file_name=name,
                    )
                except Exception as e:
                    error_msg = f"文件上传失败 [{path}]: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)
                    return UploadResult(
                        success=False,
                        operation="drive.upload",
                        file_name=name or Path(path).name,
                        error=error_msg,
                    )

        start_time = asyncio.get_event_loop().time()

        tasks = [
            _upload_one(i, path, file_names[i] if file_names else None)
            for i, path in enumerate(file_paths)
        ]
        upload_results = await asyncio.gather(*tasks)

        total_duration = asyncio.get_event_loop().time() - start_time

        # 统计结果
        success_count = sum(1 for r in upload_results if r.success and not r.operation.endswith(".dedup"))
        skipped_count = sum(1 for r in upload_results if r.operation.endswith(".dedup"))
        failed_count = total_files - success_count - skipped_count

        return BulkUploadResult(
            total_files=total_files,
            success_count=success_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            results=list(upload_results),
            total_duration_sec=total_duration,
            folder_id=target_folder,
        )

    # ── 文件夹管理 ──────────────────────────────────────────────

    async def create_folder(
        self,
        name: str,
        parent_id: Optional[str] = None,
    ) -> FolderInfo:
        """创建文件夹

        Args:
            name: 文件夹名称
            parent_id: 父文件夹 ID

        Returns:
            FolderInfo 包含文件夹元信息
        """
        cache_key = f"{name}:{parent_id}"
        if cache_key in self._folder_cache:
            folder_id = self._folder_cache[cache_key]
            return FolderInfo(folder_id=folder_id, name=name, parent_id=parent_id)

        json_body = json.dumps({
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id] if parent_id else [],
        })

        try:
            result = await self._run_with_retry(
                "drive", "files", "create",
                "--json", json_body,
            )
        except GwsExitCodeError as e:
            raise DriveApiError(
                message=f"创建文件夹失败: {e.message}",
                status_code=400,
                operation="drive.create_folder",
                folder_name=name,
            ) from e

        folder_id = result.get("id")
        if folder_id:
            self._folder_cache[cache_key] = folder_id

        return FolderInfo(
            folder_id=folder_id or "",
            name=name,
            parent_id=parent_id,
            created_time=result.get("createdTime"),
            web_view_link=result.get("webViewLink"),
        )

    async def ensure_folder_path(
        self,
        path_parts: List[str],
        root_folder_id: Optional[str] = None,
    ) -> str:
        """确保文件夹路径存在，按需逐级创建

        Args:
            path_parts: 路径组成部分，如 ["2026", "01", "食品"]
            root_folder_id: 根文件夹 ID

        Returns:
            最深层文件夹的 ID
        """
        current_parent = root_folder_id or self._root_folder_id
        current_id = current_parent or "root"

        for part in path_parts:
            folder = await self.create_folder(part, parent_id=current_id)
            current_id = folder.folder_id

        return current_id

    async def list_files(
        self,
        folder_id: Optional[str] = None,
        query: Optional[str] = None,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """列出文件夹中的文件

        Args:
            folder_id: 文件夹 ID，默认列出根目录
            query: 额外查询条件
            page_size: 每页数量

        Returns:
            文件信息列表
        """
        folder = folder_id or self._root_folder_id or "root"
        q = f"'{folder}' in parents"
        if query:
            q += f" and {query}"

        try:
            result = await self._run_with_retry(
                "drive", "files", "list",
                "--params", json.dumps({
                    "q": q,
                    "pageSize": page_size,
                    "fields": "files(id,name,mimeType,size,md5Checksum,webViewLink,createdTime)",
                }),
            )
        except GwsExitCodeError as e:
            raise DriveApiError(
                message=f"列出文件失败: {e.message}",
                operation="drive.list_files",
            ) from e

        return result.get("files", [])

    async def delete_file(self, file_id: str) -> bool:
        """删除 Drive 文件

        Args:
            file_id: 要删除的文件 ID

        Returns:
            True 如果删除成功
        """
        try:
            await self._run_with_retry(
                "drive", "files", "delete",
                "--params", json.dumps({"fileId": file_id}),
            )
            return True
        except GwsExitCodeError as e:
            raise DriveApiError(
                message=f"删除文件失败: {e.message}",
                operation="drive.delete_file",
                file_id=file_id,
            ) from e

    async def get_file_info(self, file_id: str) -> Dict[str, Any]:
        """获取文件信息

        Args:
            file_id: 文件 ID

        Returns:
            文件元信息字典
        """
        try:
            result = await self._run_with_retry(
                "drive", "files", "get",
                "--params", json.dumps({
                    "fileId": file_id,
                    "fields": "id,name,mimeType,size,md5Checksum,webViewLink,createdTime,modifiedTime",
                }),
            )
            return result
        except GwsExitCodeError as e:
            raise DriveApiError(
                message=f"获取文件信息失败: {e.message}",
                operation="drive.get_file_info",
                file_id=file_id,
            ) from e

    # ── 缓存管理 ──────────────────────────────────────────────

    def clear_cache(self) -> None:
        """清空文件夹和去重缓存"""
        self._folder_cache.clear()
        self._hash_cache.clear()

    def get_cache_stats(self) -> Dict[str, int]:
        """获取缓存统计信息

        Returns:
            {"folder_cache": N, "hash_cache": N}
        """
        return {
            "folder_cache": len(self._folder_cache),
            "hash_cache": len(self._hash_cache),
        }

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    async def _compute_md5_async(file_path: str, chunk_size: int = 8192) -> str:
        """异步计算文件 MD5 哈希

        在线程池中执行，避免阻塞事件循环。
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, DriveManager._compute_md5_sync, file_path, chunk_size
        )

    @staticmethod
    def _compute_md5_sync(file_path: str, chunk_size: int = 8192) -> str:
        """同步计算文件 MD5 哈希"""
        md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                md5.update(chunk)
        return md5.hexdigest()


__all__ = [
    "DriveManager",
]
