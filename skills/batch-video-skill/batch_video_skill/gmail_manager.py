"""
gmail_manager.py — Gmail 邮件操作管理器

封装 Gmail 邮件发送功能，支持纯文本/HTML、附件、模板渲染。
提供通知邮件模板和批量发送能力。

用法:
    >>> from workspace_client import WorkspaceClient
    >>> client = await WorkspaceClient.from_service_account("/path/to/sa.json")
    >>> gmail = client.gmail
    >>> result = await gmail.send(
    ...     to=["user@example.com"],
    ...     subject="任务完成通知",
    ...     body="<b>所有任务</b>已完成！",
    ...     html=True,
    ...     attachments=["./report.pdf"],
    ... )
    >>> result = await gmail.send_simple("user@example.com", "主题", "正文")

Python 3.11+ | Pydantic v2 | asyncio
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════════
# 从 workspace_models 导入共享组件（打破循环导入）
# ═══════════════════════════════════════════════════════════

from workspace_models import (
    BaseManager,
    ClientConfig,
    GwsExitCodeError,
    GmailApiError,
    SendResult,
    ValidationError,
)

logger = logging.getLogger(__name__)


class GmailManager(BaseManager):
    """Gmail 邮件操作管理器

    提供邮件发送功能，支持纯文本、HTML、附件和抄送。
    内置通知邮件模板，支持任务完成、失败等场景的通知。

    Args:
        config: 客户端配置
        runner: gws 命令执行器
        default_from: 默认发件人邮箱

    Example:
        >>> gmail = client.gmail
        >>> result = await gmail.send(
        ...     to=["user@example.com"],
        ...     subject="任务完成通知",
        ...     body="<b>所有任务</b>已完成！",
        ...     html=True,
        ... )
    """

    SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

    # 内置邮件模板
    _HTML_TEMPLATE_BASE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }}
.container {{ max-width: 600px; margin: 0 auto; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
.header {{ background: {header_color}; color: #fff; padding: 20px; text-align: center; }}
.header h1 {{ margin: 0; font-size: 20px; }}
.body {{ padding: 24px; color: #333; line-height: 1.6; }}
.footer {{ background: #fafafa; padding: 16px 24px; font-size: 12px; color: #999; text-align: center; border-top: 1px solid #eee; }}
table.details {{ width: 100%%; border-collapse: collapse; margin-top: 16px; }}
table.details td {{ padding: 8px 12px; border-bottom: 1px solid #eee; }}
table.details td:first-child {{ width: 30%%; color: #666; font-weight: 500; }}
.status-success {{ color: #28a745; }}
.status-failure {{ color: #dc3545; }}
.status-warning {{ color: #ffc107; }}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>{icon} {title}</h1>
</div>
<div class="body">
{body_content}
</div>
<div class="footer">
<p>此邮件由批量视频处理系统自动发送</p>
<p>发送时间: {timestamp}</p>
</div>
</div>
</body>
</html>
"""

    _TEXT_TEMPLATE_BASE = """\
{title}
{'=' * len(title)}

{body_content}

---
此邮件由批量视频处理系统自动发送
发送时间: {timestamp}
"""

    def __init__(
        self,
        config: ClientConfig,
        runner: Any,
        default_from: Optional[str] = None,
    ) -> None:
        super().__init__(config, runner)
        self._default_from = default_from

    # ── 邮件发送 ──────────────────────────────────────────────

    async def send(
        self,
        to: List[str],
        subject: str,
        body: str,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        attachments: Optional[List[str]] = None,
        html: bool = False,
        from_address: Optional[str] = None,
        draft: bool = False,
    ) -> SendResult:
        """发送邮件

        使用 gws gmail +send helper 发送邮件。
        自动处理 MIME 编码、base64 编码和附件。

        Args:
            to: 收件人邮箱列表
            subject: 邮件主题
            body: 邮件正文
            cc: 抄送邮箱列表
            bcc: 密送邮箱列表
            attachments: 附件文件路径列表
            html: 是否为 HTML 格式
            from_address: 发件人邮箱（覆盖默认）
            draft: 是否保存为草稿

        Returns:
            SendResult 包含发送结果

        Raises:
            ValidationError: 参数无效
            GmailApiError: 发送失败

        Example:
            >>> result = await gmail.send(
            ...     to=["user@example.com"],
            ...     subject="任务完成通知",
            ...     body="<b>所有任务</b>已完成！",
            ...     html=True,
            ...     attachments=["./report.pdf"],
            ... )
        """
        # 参数校验
        if not to:
            raise ValidationError("收件人列表不能为空", field="to")
        if not subject:
            raise ValidationError("邮件主题不能为空", field="subject")
        if not body:
            raise ValidationError("邮件正文不能为空", field="body")

        start_time = asyncio.get_event_loop().time()

        # 构建命令参数
        cmd_args = ["gmail", "+send"]

        # 收件人
        for addr in to:
            cmd_args.extend(["--to", addr])

        # 主题
        cmd_args.extend(["--subject", subject])

        # 正文
        cmd_args.extend(["--body", body])

        # HTML 格式
        if html:
            cmd_args.append("--html")

        # 抄送
        if cc:
            for addr in cc:
                cmd_args.extend(["--cc", addr])

        # 密送
        if bcc:
            for addr in bcc:
                cmd_args.extend(["--bcc", addr])

        # 附件
        if attachments:
            for path in attachments:
                cmd_args.extend(["--attach", path])

        # 发件人
        from_addr = from_address or self._default_from
        if from_addr:
            cmd_args.extend(["--from", from_addr])

        # 草稿
        if draft:
            cmd_args.append("--draft")

        try:
            result = await self._run_with_retry(*cmd_args)
        except GwsExitCodeError as e:
            raise GmailApiError(
                message=f"发送邮件失败: {e.message}",
                status_code=400,
                operation="gmail.send",
                recipients=to,
            ) from e

        # 解析返回结果
        message_id = result.get("id")
        thread_id = result.get("threadId")

        return SendResult(
            success=message_id is not None,
            operation="gmail.send",
            message_id=message_id,
            thread_id=thread_id,
            recipients=to,
            duration_ms=self._elapsed_ms(start_time),
            raw_response=result,
        )

    async def send_simple(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
    ) -> SendResult:
        """发送简单邮件（便捷方法）

        Args:
            to: 单个收件人邮箱
            subject: 邮件主题
            body: 邮件正文
            html: 是否为 HTML 格式

        Returns:
            SendResult
        """
        return await self.send(to=[to], subject=subject, body=body, html=html)

    # ── 邮件模板 ──────────────────────────────────────────────

    async def send_notification(
        self,
        to: str,
        notification_type: str,
        title: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        html: bool = True,
    ) -> SendResult:
        """发送通知邮件（使用预设模板）

        Args:
            to: 收件人邮箱
            notification_type: 通知类型标识 (success/failure/warning/info)
            title: 通知标题
            message: 通知消息
            details: 附加详情字典
            html: 是否使用 HTML 格式
        """
        if html:
            body = self._render_notification_html(
                notification_type, title, message, details
            )
        else:
            body = self._render_notification_text(
                notification_type, title, message, details
            )

        return await self.send_simple(
            to=to,
            subject=f"[视频批量处理] {title}",
            body=body,
            html=html,
        )

    async def send_batch_complete_notification(
        self,
        to: str,
        batch_id: str,
        total_tasks: int,
        succeeded_count: int,
        failed_count: int,
        total_cost: float = 0.0,
        total_duration_sec: float = 0.0,
        drive_folder_url: Optional[str] = None,
    ) -> SendResult:
        """发送批次完成通知

        Args:
            to: 收件人邮箱
            batch_id: 批次 ID
            total_tasks: 总任务数
            succeeded_count: 成功数
            failed_count: 失败数
            total_cost: 总花费
            total_duration_sec: 总耗时(秒)
            drive_folder_url: Drive 文件夹链接
        """
        success_rate = succeeded_count / total_tasks if total_tasks > 0 else 0.0

        details = {
            "批次 ID": batch_id,
            "总任务数": total_tasks,
            "成功": succeeded_count,
            "失败": failed_count,
            "成功率": f"{success_rate:.1%}",
            "总花费": f"${total_cost:.2f}",
            "总耗时": f"{total_duration_sec / 60:.1f} 分钟",
        }
        if drive_folder_url:
            details["视频文件夹"] = f'<a href="{drive_folder_url}">点击查看</a>'

        notification_type = "success" if failed_count == 0 else "warning" if succeeded_count > 0 else "failure"

        return await self.send_notification(
            to=to,
            notification_type=notification_type,
            title=f"批次 {batch_id} 处理完成",
            message=f"批次 {batch_id} 的所有视频处理任务已结束。"
                    f"成功{succeeded_count}个，失败{failed_count}个。",
            details=details,
            html=True,
        )

    async def send_error_alert(
        self,
        to: str,
        error_title: str,
        error_message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """发送错误告警邮件

        Args:
            to: 收件人邮箱
            error_title: 错误标题
            error_message: 错误详情
            context: 额外上下文信息
        """
        return await self.send_notification(
            to=to,
            notification_type="failure",
            title=error_title,
            message=error_message,
            details=context,
            html=True,
        )

    # ── 批量发送 ──────────────────────────────────────────────

    async def bulk_send(
        self,
        messages: list[dict[str, Any]],
        max_concurrency: int = 5,
    ) -> list[SendResult]:
        """批量发送邮件

        Args:
            messages: 邮件参数列表，每个元素为 send() 的字典参数
            max_concurrency: 最大并发数

        Returns:
            SendResult 列表
        """
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _send_one(msg: dict[str, Any]) -> SendResult:
            async with semaphore:
                try:
                    return await self.send(**msg)
                except Exception as e:
                    logger.error(f"批量发送邮件失败: {e}")
                    return SendResult(
                        success=False,
                        operation="gmail.bulk_send",
                        error=str(e),
                    )

        tasks = [_send_one(msg) for msg in messages]
        return await asyncio.gather(*tasks)

    # ── 内部模板方法 ──────────────────────────────────────────

    @staticmethod
    def _render_notification_html(
        notification_type: str,
        title: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """渲染 HTML 通知邮件"""
        colors = {
            "success": "#28a745",
            "failure": "#dc3545",
            "warning": "#ffc107",
            "info": "#17a2b8",
        }
        icons = {
            "success": "✅",
            "failure": "❌",
            "warning": "⚠️",
            "info": "ℹ️",
        }

        header_color = colors.get(notification_type, "#6c757d")
        icon = icons.get(notification_type, "📧")

        # 构建正文内容
        body_parts = [f"<p>{message}</p>"]

        if details:
            rows = "\n".join(
                f"<tr><td>{k}</td><td>{v}</td></tr>"
                for k, v in details.items()
            )
            body_parts.append(f"""
            <table class="details">
                <thead><tr><td colspan="2"><strong>详细信息</strong></td></tr></thead>
                <tbody>{rows}</tbody>
            </table>
            """)

        body_content = "\n".join(body_parts)

        return GmailManager._HTML_TEMPLATE_BASE.format(
            header_color=header_color,
            icon=icon,
            title=title,
            body_content=body_content,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    @staticmethod
    def _render_notification_text(
        notification_type: str,
        title: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """渲染纯文本通知邮件"""
        lines = [f"[{notification_type.upper()}] {title}", "", message, ""]
        if details:
            lines.append("--- 详细信息 ---")
            for k, v in details.items():
                lines.append(f"{k}: {v}")
        return "\n".join(lines)

    # ── 便捷属性 ──────────────────────────────────────────────

    @property
    def default_from(self) -> Optional[str]:
        """默认发件人地址"""
        return self._default_from


__all__ = [
    "GmailManager",
]
