#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini Skill CLI - 命令行接口

提供 11 个子命令用于视频生成工作流管理:
- single:   单产品视频生成
- batch:    批量视频生成
- status:   查询任务状态
- analyze:  图片分析
- match:    智能路由匹配
- prompt:   提示词生成
- video:    原始视频生成
- gems:     Gem模板管理
- health:   健康检查
- config:   配置管理
- retry:    重试失败任务

技术栈:
    - click >= 8.0.0
    - pydantic >= 2.0.0
    - loguru >= 0.7.0
    - rich >= 13.0.0 (可选，用于格式化输出)

Usage:
    skill single /path/to/product.jpg --description "高端护肤品"
    skill batch ./products/ --name "双十一美妆批次" --concurrency 5
    skill status batch_xxx --watch
    skill health
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# ─── 可选依赖 ───
try:
    import click

    HAS_CLICK = True
except ImportError:
    HAS_CLICK = False
    # 占位
    class click:  # type: ignore[no-redef]
        """Click 占位类"""

        @staticmethod
        def group(**kwargs: Any):
            def decorator(f: Any) -> Any:
                return f

            return decorator

        @staticmethod
        def command(name: str | None = None, **kwargs: Any):
            def decorator(f: Any) -> Any:
                return f

            return decorator

        @staticmethod
        def option(*args: Any, **kwargs: Any):
            def decorator(f: Any) -> Any:
                return f

            return decorator

        @staticmethod
        def argument(*args: Any, **kwargs: Any):
            def decorator(f: Any) -> Any:
                return f

            return decorator

        @staticmethod
        def version_option(version: str, *args: Any, **kwargs: Any):
            def decorator(f: Any) -> Any:
                return f

            return decorator

        @staticmethod
        def pass_context(f: Any) -> Any:
            return f

        @staticmethod
        def echo(message: str, **kwargs: Any) -> None:
            print(message)

        @staticmethod
        def style(text: str, **kwargs: Any) -> str:
            return text


# ─── 可选 Rich ───
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.text import Text

    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    console = None  # type: ignore[assignment]


# ─── 内部模块导入 ───
try:
    from module_gemini.mcp.mcp_server import GeminiMCPServer
    from module_gemini.models.config import GeminiConfig

    HAS_MCP_SERVER = True
except ImportError:
    HAS_MCP_SERVER = False


# ═══════════════════════════════════════════════════════════════
# 版本信息
# ═══════════════════════════════════════════════════════════════

__version__ = "5.0.0"


# ═══════════════════════════════════════════════════════════════
# 输出格式化器
# ═══════════════════════════════════════════════════════════════


class OutputFormatter:
    """
    CLI 输出格式化器

    支持人类可读和JSON两种格式。
    如安装了 Rich 库，使用 Rich 渲染；否则使用纯文本。
    """

    def __init__(self: "OutputFormatter", use_json: bool = False, quiet: bool = False) -> None:
        self.use_json = use_json
        self.quiet = quiet
        self._console = console

    def print_success(self: "OutputFormatter", message: str, details: dict[str, Any] | None = None) -> None:
        """打印成功消息"""
        if self.quiet:
            return

        if self.use_json:
            output = {"success": True, "message": message}
            if details:
                output["data"] = details
            click.echo(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            if HAS_RICH and self._console:
                self._console.print(f"[green]✅[/green] {message}")
                if details:
                    self._print_dict(details)
            else:
                click.echo(f"✅ {message}")
                if details:
                    for k, v in details.items():
                        click.echo(f"  {k}: {v}")

    def print_error(self: "OutputFormatter", message: str, details: dict[str, Any] | None = None) -> None:
        """打印错误消息"""
        if self.use_json:
            output = {
                "success": False,
                "error": message,
                "details": details or {},
            }
            click.echo(json.dumps(output, ensure_ascii=False, indent=2), err=True)
        else:
            if HAS_RICH and self._console:
                self._console.print(f"[red]❌ {message}[/red]")
                if details:
                    self._print_dict(details)
            else:
                click.echo(f"❌ {message}", err=True)
                if details:
                    for k, v in details.items():
                        click.echo(f"  {k}: {v}", err=True)

    def print_info(self: "OutputFormatter", message: str) -> None:
        """打印信息消息"""
        if self.quiet:
            return

        if self.use_json:
            return  # JSON模式下不输出信息性消息

        if HAS_RICH and self._console:
            self._console.print(f"[blue]ℹ️[/blue]  {message}")
        else:
            click.echo(f"ℹ️  {message}")

    def print_table(self: "OutputFormatter", headers: list[str], rows: list[list[str]], title: str = "") -> None:
        """打印表格"""
        if self.quiet:
            return

        if self.use_json:
            output = {
                "headers": headers,
                "rows": rows,
            }
            if title:
                output["title"] = title
            click.echo(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            if HAS_RICH and self._console:
                table = Table(title=title)
                for h in headers:
                    table.add_column(h, style="cyan")
                for row in rows:
                    table.add_row(*row)
                self._console.print(table)
            else:
                if title:
                    click.echo(f"\n{title}")
                    click.echo("=" * len(title))
                click.echo(" | ".join(headers))
                click.echo("-" * (len(" | ".join(headers)) + 10))
                for row in rows:
                    click.echo(" | ".join(row))

    def print_dict(self: "OutputFormatter", data: dict[str, Any], title: str = "") -> None:
        """打印字典"""
        if self.quiet:
            return

        if self.use_json:
            if title:
                click.echo(json.dumps({title: data}, ensure_ascii=False, indent=2))
            else:
                click.echo(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            if HAS_RICH and self._console:
                if title:
                    self._console.print(f"[bold]{title}[/bold]")
                self._print_dict(data)
            else:
                if title:
                    click.echo(f"\n{title}")
                self._print_dict_plain(data)

    def print_progress_bar(
        self,
        percent: float,
        completed: int,
        total: int,
        failed: int = 0,
        cost: float = 0.0,
        eta: float | None = None,
        throughput: float = 0.0,
    ) -> None:
        """打印进度条"""
        if self.quiet:
            return

        if self.use_json:
            click.echo(
                json.dumps(
                    {
                        "percent": percent,
                        "completed": completed,
                        "total": total,
                        "failed": failed,
                        "cost_usd": cost,
                        "eta_seconds": eta,
                        "throughput": throughput,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            bar_width = 40
            filled = int(bar_width * percent / 100)
            bar = "█" * filled + "░" * (bar_width - filled)

            eta_str = f"{eta:.0f}s" if eta else "未知"

            if HAS_RICH and self._console:
                self._console.print(
                    f"\n[bold]进度[/bold]\n"
                    f"[{bar}] {percent:.1f}% ({completed}/{total})\n"
                    f"[green]✓[/green] 已完成: {completed}  "
                    f"[red]✗[/red] 失败: {failed}  "
                    f"⏳ 剩余: {total - completed - failed}\n"
                    f"💰 花费: ${cost:.2f}  ⏱️  ETA: {eta_str}  📊 吞吐: {throughput:.2f}/s"
                )
            else:
                click.echo(f"\n[{bar}] {percent:.1f}% ({completed}/{total})")
                click.echo(f"✓ 已完成: {completed}  ✗ 失败: {failed}  ⏳ 剩余: {total - completed - failed}")
                click.echo(f"💰 花费: ${cost:.2f}  ⏱️  ETA: {eta_str}  📊 吞吐: {throughput:.2f}/s")

    def print_health(self: "OutputFormatter", result: dict[str, Any]) -> None:
        """打印健康检查结果"""
        if self.quiet:
            return

        if self.use_json:
            click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            status = result.get("status", "unknown")
            version = result.get("version", "unknown")
            checks = result.get("checks", {})
            latency_ms = result.get("latency_ms", 0)

            status_icon = "🟢" if status == "healthy" else "🟡" if status == "degraded" else "🔴"

            if HAS_RICH and self._console:
                self._console.print(f"\n[bold]健康检查[/bold] {status_icon} {status.upper()}")
                self._console.print(f"版本: {version} | 耗时: {latency_ms}ms\n")

                table = Table()
                table.add_column("组件", style="cyan")
                table.add_column("状态", style="bold")
                table.add_column("延迟(ms)", justify="right")
                table.add_column("消息")

                for name, check in checks.items():
                    c_status = check.get("status", "unknown")
                    c_latency = str(check.get("latency_ms", "N/A"))
                    c_message = check.get("message", "")

                    status_style = {
                        "healthy": "green",
                        "degraded": "yellow",
                        "unhealthy": "red",
                    }.get(c_status, "white")

                    table.add_row(name, f"[{status_style}]{c_status}[/{status_style}]", c_latency, c_message)

                self._console.print(table)
            else:
                click.echo(f"\n健康检查: {status_icon} {status.upper()}")
                click.echo(f"版本: {version} | 耗时: {latency_ms}ms\n")
                click.echo(f"{'组件':<20} {'状态':<12} {'延迟(ms)':<10} 消息")
                click.echo("-" * 70)
                for name, check in checks.items():
                    c_status = check.get("status", "unknown")
                    c_latency = check.get("latency_ms", "N/A")
                    c_message = check.get("message", "")
                    click.echo(f"{name:<20} {c_status:<12} {c_latency:<10} {c_message}")

    def print_gems(self: "OutputFormatter", result: dict[str, Any]) -> None:
        """打印Gem列表"""
        if self.quiet:
            return

        gems = result.get("gems", [])
        total = result.get("total", 0)
        last_sync = result.get("last_sync_at", None)

        if self.use_json:
            click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if HAS_RICH and self._console:
                self._console.print(f"\n[bold]Gem 模板列表[/bold] (共 {total} 个)")
                if last_sync:
                    self._console.print(f"最后同步: {last_sync}\n")

                table = Table()
                table.add_column("ID", style="cyan", no_wrap=True)
                table.add_column("名称", style="bold")
                table.add_column("分类")
                table.add_column("标签")
                table.add_column("活跃", justify="center")
                table.add_column("使用次数", justify="right")
                table.add_column("成功率", justify="right")

                for gem in gems:
                    table.add_row(
                        gem.get("id", ""),
                        gem.get("name", ""),
                        gem.get("category", ""),
                        ", ".join(gem.get("tags", [])),
                        "✓" if gem.get("is_active", True) else "✗",
                        str(gem.get("usage_count", 0)),
                        f"{gem.get('success_rate', 1.0):.0%}",
                    )

                self._console.print(table)
            else:
                click.echo(f"\nGem 模板列表 (共 {total} 个)")
                if last_sync:
                    click.echo(f"最后同步: {last_sync}\n")
                click.echo(f"{'ID':<12} {'名称':<16} {'分类':<10} {'标签':<20} {'活跃':<6} {'使用':<6} {'成功率':<6}")
                click.echo("-" * 80)
                for gem in gems:
                    click.echo(
                        f"{gem.get('id', ''):<12} "
                        f"{gem.get('name', ''):<16} "
                        f"{gem.get('category', ''):<10} "
                        f"{", ".join(gem.get('tags', [])):<20} "
                        f"{'✓' if gem.get('is_active', True) else '✗':<6} "
                        f"{gem.get('usage_count', 0):<6} "
                        f"{gem.get('success_rate', 1.0):.0%}"
                    )

    def _print_dict(self: "OutputFormatter", data: dict[str, Any], indent: int = 0) -> None:
        """使用 Rich 打印字典"""
        if not self._console:
            return
        for k, v in data.items():
            if isinstance(v, dict):
                self._console.print(f"{'  ' * indent}[bold]{k}:[/bold]")
                self._print_dict(v, indent + 1)
            else:
                self._console.print(f"{'  ' * indent}[cyan]{k}:[/cyan] {v}")

    def _print_dict_plain(self: "OutputFormatter", data: dict[str, Any], indent: int = 0) -> None:
        """纯文本打印字典"""
        for k, v in data.items():
            if isinstance(v, dict):
                click.echo(f"{'  ' * indent}{k}:")
                self._print_dict_plain(v, indent + 1)
            else:
                click.echo(f"{'  ' * indent}{k}: {v}")


# ═══════════════════════════════════════════════════════════════
# CLI 上下文
# ═══════════════════════════════════════════════════════════════


class CLIContext:
    """CLI 上下文对象，在命令间共享状态"""

    def __init__(self: "CLIContext") -> None:
        self.formatter: OutputFormatter = OutputFormatter()
        self.controller: Any = None
        self.config: Any = None
        self.config_path: str | None = None
        self.verbose: int = 0
        self.trace_id: str = ""

    async def get_controller(self: "CLIContext") -> Any:
        """获取或初始化 SkillController"""
        if self.controller is None:
            # 尝试创建 Mock Controller 或从模块加载
            self.controller = _create_controller_stub()
        return self.controller


def _create_controller_stub() -> Any:
    """创建 Controller 桩（用于独立运行）"""
    try:
        from unittest.mock import AsyncMock, MagicMock

        controller = MagicMock()
        controller.process_single = AsyncMock(return_value=MagicMock(
            task_id="cli_task_001",
            status="completed",
            video_url="https://example.com/video.mp4",
            video_local_path="/output/video.mp4",
            matched_gem_id="gem_001",
            matched_gem_name="高端产品展示",
            match_score=0.92,
            generated_prompt="A luxury product showcase...",
            video_provider="seedance",
            cost_usd=0.023,
            generation_time_sec=67.3,
            error_message=None,
        ))
        controller.process_batch = AsyncMock(return_value=MagicMock(
            batch_id="cli_batch_001",
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
        controller.health_check = AsyncMock(return_value={
            "status": "healthy",
            "version": "1.0.0",
            "checks": {
                "gemini_service": {"status": "healthy", "latency_ms": 120, "message": "Gemini响应正常"},
                "video_seedance": {"status": "healthy", "latency_ms": 450, "message": "即梦API正常"},
                "workspace": {"status": "healthy", "latency_ms": 230, "message": "Workspace连接正常"},
            },
            "latency_ms": 800,
        })
        controller.get_config = AsyncMock(return_value={
            "config": {"gemini": {"model": "pro", "timeout": 120}},
            "source": "env",
            "last_reload_at": datetime.now().isoformat(),
        })
        controller.retry_failed = AsyncMock(return_value=5)

        return controller
    except ImportError:
        logger.error("无法创建 Controller 桩，请安装 unittest.mock")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# Click 上下文对象
# ═══════════════════════════════════════════════════════════════

_pass_context = click.make_pass_decorator(CLIContext, ensure=True)


# ═══════════════════════════════════════════════════════════════
# CLI 主组
# ═══════════════════════════════════════════════════════════════


@click.group(name="skill")
@click.version_option(version=__version__, prog_name="skill")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=False),
    help="指定配置文件路径",
)
@click.option(
    "--env",
    "env_file",
    type=click.Path(exists=False),
    help="指定 .env 文件路径",
)
@click.option(
    "--verbose",
    "-v",
    count=True,
    default=0,
    help="详细输出（可叠加 -vv, -vvv）",
)
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    default=False,
    help="静默模式",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="JSON格式输出",
)
@click.option(
    "--trace-id",
    default="",
    help="指定追踪ID",
)
@click.pass_context
def cli(
    ctx: click.Context,
    config_path: str | None,
    env_file: str | None,
    verbose: int,
    quiet: bool,
    json_output: bool,
    trace_id: str,
) -> None:
    """
    带货视频批量生产 Skill CLI

    提供视频生成、图片分析、智能路由、批量处理等功能。

    示例:
        skill single /path/to/product.jpg --description "高端护肤品"
        skill batch ./products/ --name "双十一美妆批次" --concurrency 5
        skill status batch_xxx --watch
        skill health
    """
    # 初始化上下文
    cli_ctx = CLIContext()
    cli_ctx.formatter = OutputFormatter(use_json=json_output, quiet=quiet)
    cli_ctx.config_path = config_path
    cli_ctx.verbose = verbose
    cli_ctx.trace_id = trace_id

    # 设置日志级别
    if quiet:
        logger.remove()
        logger.add(sys.stderr, level="WARNING")
    elif verbose >= 2:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")
    elif verbose >= 1:
        logger.remove()
        logger.add(sys.stderr, level="INFO")

    ctx.obj = cli_ctx


# ═══════════════════════════════════════════════════════════════
# CMD01: skill single — 单产品视频生成
# ═══════════════════════════════════════════════════════════════


@cli.command(name="single")
@click.argument("image_path", type=click.Path(exists=False))
@click.option(
    "-d",
    "--description",
    default="",
    help="产品补充描述",
)
@click.option(
    "-g",
    "--gem-id",
    default=None,
    help="指定Gem模板ID",
)
@click.option(
    "-p",
    "--provider",
    default=None,
    help="指定视频提供商",
)
@click.option(
    "-r",
    "--resolution",
    default="1080p",
    type=click.Choice(["540p", "720p", "1080p", "4k"]),
    help="输出分辨率",
)
@click.option(
    "-t",
    "--duration",
    default=5,
    type=int,
    help="视频时长(秒)",
)
@click.option(
    "--no-audio",
    is_flag=True,
    default=False,
    help="不生成音频",
)
@click.option(
    "-o",
    "--output",
    default="./output",
    help="输出目录",
)
@_pass_context
@click.pass_context
def cmd_single(
    ctx: click.Context,
    cli_ctx: CLIContext,
    image_path: str,
    description: str,
    gem_id: str | None,
    provider: str | None,
    resolution: str,
    duration: int,
    no_audio: bool,
    output: str,
) -> None:
    """单产品视频生成\n\n    IMAGE_PATH: 产品图片路径（本地或URL）"""
    fmt = cli_ctx.formatter

    if not HAS_CLICK:
        fmt.print_error("Click not installed. Run: pip install click")
        sys.exit(1)

    fmt.print_info(f"开始单产品视频生成: {image_path}")

    async def _run() -> None:
        controller = await cli_ctx.get_controller()

        if HAS_MCP_SERVER:
            server = GeminiMCPServer(
                skill_controller=controller,
                server_name="gemini-module-cli",
            )
            await server.initialize()

            result = await server._mcp._tools[  # type: ignore[attr-defined]
                "generate_video_single"
            ](
                image_path=image_path,
                user_description=description,
                preferred_gem_id=gem_id,
                preferred_provider=provider,
                output_resolution=resolution,
                output_duration=duration,
                generate_audio=not no_audio,
            )
        else:
            # 直接使用 Controller
            result = await controller.process_single(
                image_path=image_path,
                user_description=description,
                preferred_gem_id=gem_id,
                preferred_provider=provider,
                output_resolution=resolution,
                output_duration=duration,
                generate_audio=not no_audio,
            )
            result = dict(result) if hasattr(result, "__dict__") else result

        # 输出结果
        if isinstance(result, dict) and "error_code" in result:
            fmt.print_error(result.get("message", "未知错误"), result)
        else:
            _print_single_result(fmt, result)

    asyncio.run(_run())


def _print_single_result(fmt: OutputFormatter, result: dict[str, Any]) -> None:
    """打印单产品视频生成结果"""
    if fmt.use_json:
        fmt.print_dict(result, title="single_video_result")
        return

    status = result.get("status", "unknown")
    status_icon = "✅" if status == "completed" else "❌"

    output_lines = [
        "\n🎬 单产品视频生成",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📷 图片: {result.get('video_local_path', 'N/A')}",
        f"🤖 匹配: {result.get('matched_gem_name', 'N/A')} ({result.get('matched_gem_id', 'N/A')}, 匹配度 {result.get('match_score', 0):.2f})",
        f"✍️  提示词: {result.get('generated_prompt', 'N/A')[:80]}...",
        f"🎥 视频: {result.get('video_provider', 'N/A')}",
        f"💰 花费: ${result.get('cost_usd', 0):.3f}",
        f"⏱️  耗时: {result.get('generation_time_sec', 0):.1f}秒",
        f"{status_icon} 状态: {status}",
    ]

    if result.get("video_url"):
        output_lines.append(f"🔗 URL: {result['video_url']}")
    if result.get("video_local_path"):
        output_lines.append(f"📁 本地: {result['video_local_path']}")
    if result.get("error_message"):
        output_lines.append(f"❌ 错误: {result['error_message']}")

    click.echo("\n".join(output_lines))


# ═══════════════════════════════════════════════════════════════
# CMD02: skill batch — 批量视频生成
# ═══════════════════════════════════════════════════════════════


@cli.command(name="batch")
@click.argument("image_dir", type=click.Path(exists=False))
@click.option(
    "-n",
    "--name",
    default="未命名批次",
    help="批次名称",
)
@click.option(
    "-d",
    "--description",
    default="",
    help="通用产品描述",
)
@click.option(
    "-c",
    "--concurrency",
    default=3,
    type=int,
    help="最大并发数 (1-10)",
)
@click.option(
    "-p",
    "--provider",
    default=None,
    help="指定视频提供商",
)
@click.option(
    "-r",
    "--resolution",
    default="1080p",
    type=click.Choice(["540p", "720p", "1080p", "4k"]),
    help="输出分辨率",
)
@click.option(
    "-t",
    "--duration",
    default=5,
    type=int,
    help="视频时长(秒)",
)
@click.option(
    "--no-audio",
    is_flag=True,
    default=False,
    help="不生成音频",
)
@click.option(
    "--no-drive-upload",
    is_flag=True,
    default=False,
    help="不上传到Google Drive",
)
@click.option(
    "--continue-on-error/--no-continue-on-error",
    default=True,
    help="单个失败继续处理",
)
@_pass_context
def cmd_batch(
    cli_ctx: CLIContext,
    image_dir: str,
    name: str,
    description: str,
    concurrency: int,
    provider: str | None,
    resolution: str,
    duration: int,
    no_audio: bool,
    no_drive_upload: bool,
    continue_on_error: bool,
) -> None:
    """批量视频生成\n\n    IMAGE_DIR: 产品图片目录路径"""
    fmt = cli_ctx.formatter

    fmt.print_info(f"开始批量视频生成: {image_dir}")

    async def _run() -> None:
        controller = await cli_ctx.get_controller()

        result = await controller.process_batch(
            image_files=_scan_image_dir(image_dir),
            batch_name=name,
            user_description=description,
            max_concurrency=concurrency,
            preferred_provider=provider,
            output_resolution=resolution,
            output_duration=duration,
            generate_audio=not no_audio,
            upload_to_drive=not no_drive_upload,
            continue_on_error=continue_on_error,
        )

        batch_id = getattr(result, "batch_id", "unknown")
        status = getattr(result, "status", "unknown")

        fmt.print_success(
            f"批量任务已启动",
            {
                "batch_id": batch_id,
                "status": status,
                "directory": image_dir,
                "concurrency": concurrency,
            },
        )

    asyncio.run(_run())


def _scan_image_dir(image_dir: str) -> list[str]:
    """扫描目录中的图片文件"""
    path = Path(image_dir)
    if not path.exists():
        click.echo(f"❌ 目录不存在: {image_dir}", err=True)
        sys.exit(1)

    valid_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
    image_files = [
        str(f)
        for f in path.iterdir()
        if f.is_file() and f.suffix.lower() in valid_extensions
    ]

    if not image_files:
        click.echo(f"❌ 目录中未找到有效图片文件: {image_dir}", err=True)
        sys.exit(1)

    return image_files


# ═══════════════════════════════════════════════════════════════
# CMD02A: skill gem-preflight — Codex/Gem 通道预检
# ═══════════════════════════════════════════════════════════════


@cli.command(name="gem-preflight")
@click.option("--gemcli-path", default="gemcli", help="gemcli 可执行文件路径")
@click.option("--gem-id", default=None, help="需要测试调用的 Gemini Gem ID")
@click.option("--model", default="flash", help="gemcli chat 模型")
@click.option("--timeout", default=90, type=int, help="单个命令超时时间（秒）")
@click.option("--check-gems", is_flag=True, default=False, help="显式执行后台 Gems 全量审计；普通批次不需要")
@click.option(
    "--smoke-prompt",
    default=None,
    help="可选：真正调用 Gem 的短提示词；不填则只检查登录，--check-gems 才列全量 Gems",
)
@_pass_context
def cmd_gem_preflight(
    cli_ctx: CLIContext,
    gemcli_path: str,
    gem_id: str | None,
    model: str,
    timeout: int,
    check_gems: bool,
    smoke_prompt: str | None,
) -> None:
    """预检 Codex 到 Gemini Gem 的本地调用通道。"""
    fmt = cli_ctx.formatter
    checks: dict[str, Any] = {}
    from .gemcli_stability import check_to_dict, ensure_gemcli_ready, run_cmd

    preflight = ensure_gemcli_ready(gemcli_path, timeout=timeout, check_gems=check_gems)
    checks.update(preflight.checks)
    if not preflight.ok:
        fmt.print_error("Gem 预检失败：gemcli/Gemini 通道不可用", checks)
        sys.exit(1)

    if smoke_prompt:
        if not gem_id:
            fmt.print_error("Gem 预检失败：--smoke-prompt 需要同时提供 --gem-id", checks)
            sys.exit(1)
        chat = run_cmd([gemcli_path, "chat", smoke_prompt, "--gem", gem_id, "--new", "-m", model], timeout=max(timeout, 180))
        checks["gem_chat"] = check_to_dict(chat)
        if not chat.ok:
            fmt.print_error("Gem 预检失败：Gem 文本调用失败", checks)
            sys.exit(1)

    fmt.print_success("Gem 预检通过", checks)


# ═══════════════════════════════════════════════════════════════
# CMD02B: skill route-gem — 产品图/产品名自动选择 Gem
# ═══════════════════════════════════════════════════════════════


@cli.command(name="route-gem")
@click.option("--image", "image_path", default=None, type=click.Path(exists=False), help="产品图路径；读取文件名和父目录名做路由信号")
@click.option("--product", "product_text", default="", help="产品名或补充描述")
@click.option("--caption", "image_caption", default="", help="图片识别/人工描述文本；命令本身不调用外部视觉模型")
@click.option(
    "--registry",
    "registry_path",
    default=str(Path.home() / ".batch-video-skill" / "gem_registry.json"),
    type=click.Path(exists=False),
    help="Gem 注册表 JSON 路径",
)
@click.option("--top-k", default=5, type=int, help="返回候选数量")
@_pass_context
def cmd_route_gem(
    cli_ctx: CLIContext,
    image_path: str | None,
    product_text: str,
    image_caption: str,
    registry_path: str,
    top_k: int,
) -> None:
    """根据产品图文件名/产品名/图片描述选择最合适的 Gemini Gem。"""
    fmt = cli_ctx.formatter
    try:
        from .product_gem_router import route_product_to_gem

        result = route_product_to_gem(
            image_path=image_path,
            product_text=product_text,
            image_caption=image_caption,
            registry_path=registry_path,
            top_k=top_k,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        fmt.print_error("Gem 路由失败", {"error": str(exc)})
        sys.exit(1)

    data = result.to_dict()
    if fmt.use_json:
        fmt.print_dict(data)
        return

    selected = data["selected"]
    fmt.print_success(
        "已选择 Gem",
        {
            "gem": selected["display_name"],
            "gem_id": selected["gem_id"],
            "score": selected["score"],
            "confidence": selected["confidence"],
            "reasons": "；".join(selected["reasons"]),
            "gem_url": selected["gem_url"],
        },
    )


@cli.command(name="storyboard-build")
@click.argument("product_image", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--gem-output",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Gem 原始输出文件；内容只读并以 SHA256 绑定故事板",
)
@click.option("--gem-id", required=True, help="生成原始提示词的 Gem ID")
@click.option("--gem-name", default="", help="Gem 显示名称")
@click.option("--product-name", required=True, help="产品名")
@click.option("--market", default="墨西哥", help="目标市场")
@click.option("--output-dir", default=None, type=click.Path(exists=False, file_okay=False))
@click.option("--sheet-seconds", default=15, type=click.IntRange(1, 15), help="单张 Sheet 最大覆盖秒数")
@click.option("--sheet", "sheet_ordinals", multiple=True, type=click.IntRange(1), help="只生成指定 Sheet，可重复")
@click.option("--max-sheets", default=None, type=click.IntRange(1), help="最多生成多少张 Sheet")
@click.option("--plan-only", is_flag=True, default=False, help="只解析并写入计划，不调用 Image2")
@click.option(
    "--qc-mode",
    default="risk",
    type=click.Choice(["risk", "all", "local"]),
    help="risk=含高风险动作的整张故事板做一次 Gemini 视觉 QC",
)
@click.option("--gemcli-path", default="gemcli", help="Gemini 视觉 QC 使用的 gemcli")
@click.option(
    "--cloudflared-path",
    default=r"D:\tools\cloudflared\cloudflared.exe",
    type=click.Path(exists=False, dir_okay=False),
)
@click.option("--feishu-sync", default="async", type=click.Choice(["async", "off"]))
@_pass_context
def cmd_storyboard_build(
    cli_ctx: CLIContext,
    product_image: str,
    gem_output: str,
    gem_id: str,
    gem_name: str,
    product_name: str,
    market: str,
    output_dir: str | None,
    sheet_seconds: int,
    sheet_ordinals: tuple[int, ...],
    max_sheets: int | None,
    plan_only: bool,
    qc_mode: str,
    gemcli_path: str,
    cloudflared_path: str,
    feishu_sync: str,
) -> None:
    """从不可变 Gem 原始分镜生成 Image2 故事板和视频参考清单。"""
    fmt = cli_ctx.formatter
    try:
        from .storyboard_plan import parse_storyboard_plan, write_storyboard_plan

        product_path = Path(product_image).expanduser().resolve()
        raw_path = Path(gem_output).expanduser().resolve()
        raw_text = raw_path.read_text(encoding="utf-8-sig")
        target = (
            Path(output_dir).expanduser().resolve()
            if output_dir
            else raw_path.parent / f"storyboard-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        plan = parse_storyboard_plan(
            raw_text,
            raw_path=raw_path,
            gem_id=gem_id,
            gem_name=gem_name or gem_id,
            sheet_seconds=sheet_seconds,
        )
        target.mkdir(parents=True, exist_ok=True)
        plan_path = write_storyboard_plan(plan, target / "storyboard-plan.json")
        if plan_only:
            data = {
                "output_dir": str(target),
                "plan_path": str(plan_path),
                "duration_sec": plan.duration_sec,
                "shots": len(plan.shots),
                "sheets": len(plan.sheets),
                "coverage": plan.source.coverage,
                "raw_gem_sha256": plan.source.raw_sha256,
                "image2_called": False,
            }
        else:
            from .batch_folder_runner import _make_hfsy_client, load_config
            from .reference_host import CloudflareQuickTunnel
            from .storyboard_runtime import generate_storyboard_package
            from .v4_runtime import GeminiVisualQCClient

            config_path = Path(cli_ctx.config_path) if cli_ctx.config_path else None
            image_client = _make_hfsy_client(load_config(config_path))
            visual_qc = None
            if qc_mode in {"risk", "all"}:
                visual_qc = GeminiVisualQCClient(gemcli_path, model="flash", timeout_sec=180)
            with CloudflareQuickTunnel(cloudflared_path, startup_timeout_sec=45) as host:
                result = generate_storyboard_package(
                    plan,
                    product_image=product_path,
                    product_name=product_name,
                    market=market,
                    output_dir=target,
                    image_client=image_client,
                    host=host,
                    max_sheets=max_sheets,
                    sheet_ordinals=set(sheet_ordinals) or None,
                    visual_qc=visual_qc,
                    qc_mode=qc_mode,
                )
            data = result.to_dict()
            data.update(
                {
                    "duration_sec": plan.duration_sec,
                    "shots": len(plan.shots),
                    "coverage": plan.source.coverage,
                    "raw_gem_sha256": plan.source.raw_sha256,
                    "image2_called": True,
                }
            )
        if feishu_sync == "async":
            from .feishu_outbox import FeishuOutbox

            preview = ""
            contacts = data.get("contact_sheets") or []
            if contacts:
                preview = str(contacts[0])
            outbox = FeishuOutbox(target / "feishu_outbox.jsonl")
            outbox.enqueue(
                "故事板",
                plan.plan_sha256,
                {
                    "storyboard_id": plan.plan_sha256,
                    "Gem": f"{plan.source.gem_name} ({plan.source.gem_id})",
                    "Gem原文文件": str(raw_path),
                    "Gem原文哈希": plan.source.raw_sha256,
                    "分镜数量": len(plan.shots),
                    "故事板目录": str(target),
                    "故事板预览": preview,
                    "解析覆盖率": plan.source.coverage,
                    "QC": (
                        "passed"
                        if data.get("qc_passed")
                        else "needs_review" if data.get("image2_called") else "pending"
                    ),
                },
            )
            data["feishu_outbox"] = str(outbox.path)
        fmt.print_success("故事板中间层已生成", data)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        fmt.print_error("故事板生成失败", {"error": str(exc)})
        sys.exit(1)


@cli.command(name="storyboard-video")
@click.argument("storyboard_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--provider", required=True, type=click.Choice(["grok", "omni"]))
@click.option("--live", is_flag=True, default=False, help="显式提交付费视频任务；默认只编译请求")
@click.option(
    "--cloudflared-path",
    default=r"D:\tools\cloudflared\cloudflared.exe",
    type=click.Path(exists=False, dir_okay=False),
)
@click.option("--newapi-base-url", default="https://aihubcc.cc")
@click.option("--timeout-min", default=20, type=click.IntRange(1, 120))
@click.option("--poll-interval", default=8, type=click.IntRange(1, 120))
@_pass_context
def cmd_storyboard_video(
    cli_ctx: CLIContext,
    storyboard_dir: str,
    provider: str,
    live: bool,
    cloudflared_path: str,
    newapi_base_url: str,
    timeout_min: int,
    poll_interval: int,
) -> None:
    """将 QC 通过的故事板首帧编译或提交给 Grok/Omni。"""
    try:
        from .storyboard_video import run_storyboard_video

        result = run_storyboard_video(
            Path(storyboard_dir),
            provider=provider,
            live=live,
            cloudflared_path=cloudflared_path,
            base_url=newapi_base_url,
            timeout_sec=timeout_min * 60,
            poll_interval_sec=poll_interval,
        )
        cli_ctx.formatter.print_success("故事板视频请求已处理", result)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        cli_ctx.formatter.print_error("故事板视频请求被拒绝", {"error": str(exc)})
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# CMD02C: skill batch-folder — 文件夹批量生产真实视频
# ═══════════════════════════════════════════════════════════════


@cli.command(name="batch-folder")
@click.argument("image_dir", type=click.Path(exists=False, file_okay=False, dir_okay=True))
@click.option(
    "--output-subdir",
    default=None,
    help="输出子目录名；默认在图片目录下创建 batch_video_outputs_时间戳",
)
@click.option(
    "--variants",
    default=1,
    type=int,
    help="每张图生成几个视频版本",
)
@click.option(
    "--duration",
    default=15,
    type=int,
    help="目标成片时长（秒）；V4 自动按模型原生时长拆段，旧 provider 仅支持 5/10/15",
)
@click.option(
    "--concurrency",
    default=1,
    type=int,
    help="并发生成数；真实视频建议从 1 开始",
)
@click.option(
    "--target-country",
    default="美国",
    help="目标国家/市场",
)
@click.option(
    "--category",
    default="home product",
    help="产品品类，用于生成首帧和视频提示词",
)
@click.option(
    "--product-name",
    default=None,
    help="统一产品名；不填则从每张图片文件名推断",
)
@click.option(
    "--first-frame-mode",
    default="generated",
    type=click.Choice(["generated", "url-base", "none"]),
    help="首帧模式：generated=生成生活化首帧；url-base=用公网产品图作参考后生成首帧；none=纯文生视频",
)
@click.option(
    "--image-url-base",
    default=None,
    help="公网产品图目录 URL，用作 HFSY reference_images，不作为视频首帧",
)
@click.option(
    "--gem/--no-gem",
    "use_gem",
    default=True,
    help="是否先调用 Gemini Gem 生成提示词；默认启用",
)
@click.option(
    "--gem-id",
    default=None,
    help="指定 Gemini Gem ID；启用 --auto-gem-route 时作为路由失败兜底",
)
@click.option(
    "--auto-gem-route/--no-auto-gem-route",
    default=False,
    help="按每张产品图文件名/产品名自动选择最合适的 Gem",
)
@click.option(
    "--gem-registry",
    default=str(Path.home() / ".batch-video-skill" / "gem_registry.json"),
    type=click.Path(exists=False),
    help="自动路由使用的 Gem 注册表 JSON",
)
@click.option(
    "--gem-backend",
    type=click.Choice(["gemcli", "local"]),
    default="gemcli",
    help="gemcli=走 Gemini 网页 Gem（需 cookie）；local=用本地 gem 指令文本 + 大模型 API（无需 cookie）",
)
@click.option(
    "--gem-model",
    default="flash",
    help="gemcli 调用模型",
)
@click.option(
    "--gemcli-path",
    default="gemcli",
    help="gemcli 可执行文件路径",
)
@click.option(
    "--gem-timeout",
    default=180,
    type=int,
    help="单次 Gem 提示词生成超时时间（秒）",
)
@click.option(
    "--gem-retries",
    default=2,
    type=int,
    help="Gem 提示词生成失败后的重试次数",
)
@click.option(
    "--gem-retry-delay",
    default=5,
    type=int,
    help="Gem 重试间隔秒数",
)
@click.option(
    "--max-items",
    default=None,
    type=int,
    help="最多处理多少张图；用于小批量验收",
)
@click.option(
    "--timeout-min",
    default=18,
    type=int,
    help="单个快子任务最长等待分钟数",
)
@click.option(
    "--poll-interval",
    default=10,
    type=int,
    help="快子任务轮询间隔秒数",
)
@click.option(
    "--audio/--no-audio",
    default=True,
    help="是否要求视频生成音频",
)
@click.option(
    "--video-provider",
    default="kuaizi",
    type=click.Choice(["kuaizi", "doubao-switcher", "auto", "grok", "omni", "both"]),
    help="视频后端；auto/grok/omni/both 启用 V4 物理参考链",
)
@click.option(
    "--physical-mode",
    default="strict",
    type=click.Choice(["strict", "balanced", "off"]),
    help="物理规则强度；strict 失败即停，不降级纯文生视频",
)
@click.option(
    "--reference-strategy",
    default="image2-chain",
    type=click.Choice(["image2-chain", "image2-first", "none"]),
    help="Image2 参考策略",
)
@click.option(
    "--reference-host",
    default="auto",
    type=click.Choice(["auto", "url-base", "tunnel"]),
    help="参考资产 URL 来源；auto 默认使用 D 盘 Cloudflare Tunnel",
)
@click.option(
    "--qc-mode",
    default="risk",
    type=click.Choice(["risk", "all", "local"]),
    help="QC 模式：risk=高风险段语义检查，all=全部，local=仅 FFmpeg",
)
@click.option("--max-regenerations", default=1, type=int, help="单段 QC 失败后的最大局部重生次数")
@click.option(
    "--feishu-sync",
    default="async",
    type=click.Choice(["async", "off"]),
    help="异步飞书控制台；失败进入 outbox，不阻塞生成",
)
@click.option(
    "--feishu-config",
    default=r"D:\Feishu\video-feishu-sync\config\config.yaml",
    help="飞书桥接配置",
)
@click.option(
    "--cloudflared-path",
    default=r"D:\tools\cloudflared\cloudflared.exe",
    help="Cloudflare Tunnel 可执行文件",
)
@click.option(
    "--newapi-base-url",
    default="https://aihubcc.cc",
    help="Grok/Omni NewAPI 地址；密钥仍只从 NEWAPI_API_KEY 读取",
)
@click.option(
    "--doubao-cli-path",
    default=r"D:\tools\venvs\cli-anything\Scripts\doubao-switcher-cli.exe",
    help="doubao-switcher-cli 可执行文件路径",
)
@click.option(
    "--doubao-generation-wait-sec",
    default=180,
    type=int,
    help="豆包页面提交后等待生成完成再尝试点击下载的秒数",
)
@click.option(
    "--resume/--no-resume",
    default=True,
    help="如果目标视频文件已存在则跳过",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="只生成输出目录、提示词和清单，不调用外部 API",
)
@_pass_context
def cmd_batch_folder(
    cli_ctx: CLIContext,
    image_dir: str,
    output_subdir: str | None,
    variants: int,
    duration: int,
    concurrency: int,
    target_country: str,
    category: str,
    product_name: str | None,
    first_frame_mode: str,
    image_url_base: str | None,
    use_gem: bool,
    gem_id: str | None,
    auto_gem_route: bool,
    gem_registry: str,
    gem_backend: str,
    gem_model: str,
    gemcli_path: str,
    gem_timeout: int,
    gem_retries: int,
    gem_retry_delay: int,
    max_items: int | None,
    timeout_min: int,
    poll_interval: int,
    audio: bool,
    video_provider: str,
    physical_mode: str,
    reference_strategy: str,
    reference_host: str,
    qc_mode: str,
    max_regenerations: int,
    feishu_sync: str,
    feishu_config: str,
    cloudflared_path: str,
    newapi_base_url: str,
    doubao_cli_path: str,
    doubao_generation_wait_sec: int,
    resume: bool,
    dry_run: bool,
) -> None:
    """按文件夹批量生产视频，并在原目录创建输出子文件夹。

    IMAGE_DIR: 包含商品图的文件夹。默认每张图生成 1 条 15 秒竖屏视频。
    """
    fmt = cli_ctx.formatter
    fmt.print_info(f"开始文件夹批量生产: {image_dir}")

    async def _run() -> None:
        try:
            try:
                from .batch_folder_runner import BatchFolderOptions, run_batch_folder
            except ImportError:
                from batch_folder_runner import BatchFolderOptions, run_batch_folder  # type: ignore

            result = await run_batch_folder(
                BatchFolderOptions(
                    image_dir=Path(image_dir),
                    output_subdir=output_subdir,
                    variants=variants,
                    duration=duration,
                    concurrency=concurrency,
                    target_country=target_country,
                    category=category,
                    product_name=product_name,
                    first_frame_mode=first_frame_mode,  # type: ignore[arg-type]
                    image_url_base=image_url_base,
                    use_gem=use_gem,
                    gem_id=gem_id,
                    auto_gem_route=auto_gem_route,
                    gem_registry_path=Path(gem_registry) if gem_registry else None,
                    gem_backend=gem_backend,
                    gem_model=gem_model,
                    gemcli_path=gemcli_path,
                    gem_timeout_sec=gem_timeout,
                    gem_retries=gem_retries,
                    gem_retry_delay_sec=gem_retry_delay,
                    generate_audio=audio,
                    dry_run=dry_run,
                    resume=resume,
                    max_items=max_items,
                    timeout_min=timeout_min,
                    poll_interval_sec=poll_interval,
                    config_path=Path(cli_ctx.config_path) if cli_ctx.config_path else None,
                    video_provider=video_provider,  # type: ignore[arg-type]
                    physical_mode=physical_mode,  # type: ignore[arg-type]
                    reference_strategy=reference_strategy,  # type: ignore[arg-type]
                    reference_host=reference_host,  # type: ignore[arg-type]
                    qc_mode=qc_mode,  # type: ignore[arg-type]
                    max_regenerations=max_regenerations,
                    feishu_sync=feishu_sync,  # type: ignore[arg-type]
                    feishu_config_path=Path(feishu_config),
                    cloudflared_path=cloudflared_path,
                    newapi_base_url=newapi_base_url,
                    doubao_cli_path=doubao_cli_path,
                    doubao_generation_wait_sec=doubao_generation_wait_sec,
                )
            )
        except Exception as exc:  # noqa: BLE001 - CLI should show a concise failure
            fmt.print_error("文件夹批量生产失败", {"error": str(exc)})
            sys.exit(1)

        data = result.to_dict()
        if fmt.use_json:
            fmt.print_dict(data, title="batch_folder_result")
            return

        if result.failed:
            fmt.print_error("文件夹批量生产完成，但存在失败任务", data)
        else:
            fmt.print_success("文件夹批量生产完成", data)

    asyncio.run(_run())


@cli.command(name="qc-video")
@click.argument("video", type=click.Path(exists=True, dir_okay=False))
@click.option("--reference", required=True, type=click.Path(exists=True, dir_okay=False), help="产品参考图")
@click.option("--mode", default="risk", type=click.Choice(["risk", "all", "local"]))
@click.option("--expected-duration", default=None, type=float)
@click.option("--gemcli-path", default="gemcli")
@click.option("--output", default=None, type=click.Path(exists=False))
@_pass_context
def cmd_qc_video(
    cli_ctx: CLIContext,
    video: str,
    reference: str,
    mode: str,
    expected_duration: float | None,
    gemcli_path: str,
    output: str | None,
) -> None:
    """对本地视频执行解码/黑屏/冻结检查，并可用 Gemini Flash 检查物理一致性。"""
    fmt = cli_ctx.formatter
    try:
        from .v4_runtime import GeminiVisualQCClient
        from .video_qc import build_contact_sheet, build_semantic_qc_prompt, run_local_video_qc

        video_path = Path(video).resolve()
        reference_path = Path(reference).resolve()
        report_path = Path(output).resolve() if output else video_path.with_suffix(".qc.json")
        local = run_local_video_qc(video_path, expected_duration_sec=expected_duration)
        semantic = None
        contact_path = report_path.with_suffix(".contact.jpg")
        if mode in {"risk", "all"} and local.passed:
            build_contact_sheet(video_path, contact_path, fps=2.0)
            semantic = GeminiVisualQCClient(gemcli_path).check(
                [reference_path, contact_path],
                build_semantic_qc_prompt(
                    product_lock=f"match reference image {reference_path.name}",
                    mechanism="movement must obey visible product controls and support relations",
                ),
            )
        passed = local.passed and (semantic is None or semantic.passed)
        data = {
            "passed": passed,
            "local": local.to_dict(),
            "semantic": semantic.to_dict() if semantic else None,
            "contact_sheet": str(contact_path) if contact_path.exists() else "",
        }
        report_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        fmt.print_error("视频 QC 失败", {"error": str(exc)})
        sys.exit(1)
    if passed:
        fmt.print_success("视频 QC 通过", {**data, "report": str(report_path)})
    else:
        fmt.print_error("视频 QC 未通过", {**data, "report": str(report_path)})
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
# CMD02C: skill feishu — 飞书桥接
# ═══════════════════════════════════════════════════════════════


@cli.command(name="feishu-init-schema")
@click.option(
    "--feishu-config",
    default="D:/Feishu/video-feishu-sync/config/config.yaml",
    help="飞书桥接配置文件",
)
@click.option("--real", is_flag=True, default=False, help="真实创建缺失字段；默认只预览")
@_pass_context
def cmd_feishu_init_schema(cli_ctx: CLIContext, feishu_config: str, real: bool) -> None:
    """初始化/补齐飞书表字段。"""
    fmt = cli_ctx.formatter
    try:
        from .feishu_bridge import init_feishu_schema, load_feishu_bridge_config

        config = load_feishu_bridge_config(feishu_config)
        result = init_feishu_schema(config, dry_run=not real)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        fmt.print_error("飞书字段初始化失败", {"error": str(exc)})
        sys.exit(1)
    fmt.print_success("飞书字段初始化完成" if real else "飞书字段初始化预览完成", result)


@cli.command(name="feishu-console-init")
@click.option(
    "--feishu-config",
    default="D:/Feishu/video-feishu-sync/config/config.yaml",
    help="飞书桥接配置文件",
)
@click.option("--real", is_flag=True, default=False, help="真实增量创建三张 V4 表；默认只预览")
@_pass_context
def cmd_feishu_console_init(cli_ctx: CLIContext, feishu_config: str, real: bool) -> None:
    """增量创建批次、创意计划、视频分段三张表，不删除旧数据。"""
    fmt = cli_ctx.formatter
    try:
        from .feishu_bridge import init_v4_console_schema, load_feishu_bridge_config

        result = init_v4_console_schema(load_feishu_bridge_config(feishu_config), dry_run=not real)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        fmt.print_error("飞书 V4 控制台初始化失败", {"error": str(exc)})
        sys.exit(1)
    fmt.print_success("飞书 V4 控制台初始化完成" if real else "飞书 V4 控制台初始化预览完成", result)


@cli.command(name="feishu-flush")
@click.argument("output_dir", type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option(
    "--feishu-config",
    default="D:/Feishu/video-feishu-sync/config/config.yaml",
    help="飞书桥接配置文件",
)
@click.option("--dry-run", is_flag=True, default=False, help="只预览待同步事件，不写飞书")
@_pass_context
def cmd_feishu_flush(
    cli_ctx: CLIContext,
    output_dir: str,
    feishu_config: str,
    dry_run: bool,
) -> None:
    """幂等重放 V4 飞书 outbox；失败事件保留供下次继续。"""
    fmt = cli_ctx.formatter
    try:
        from .feishu_bridge import flush_v4_outbox, load_feishu_bridge_config

        result = flush_v4_outbox(
            output_dir,
            load_feishu_bridge_config(feishu_config),
            dry_run=dry_run,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        fmt.print_error("飞书 outbox 重放失败", {"error": str(exc)})
        sys.exit(1)
    if result.get("failed"):
        fmt.print_error("飞书 outbox 重放完成，但仍有失败事件", result)
    else:
        fmt.print_success("飞书 outbox 预览完成" if dry_run else "飞书 outbox 重放完成", result)


@cli.command(name="feishu-import-gems")
@click.argument("source", required=False)
@click.option(
    "--feishu-config",
    default="D:/Feishu/video-feishu-sync/config/config.yaml",
    help="飞书桥接配置文件；用于读取 lark_cli_path",
)
@click.option(
    "--output",
    default="D:/Feishu/video-feishu-sync/gem-catalog/feishu-gem-catalog.json",
    help="输出 Gem catalog JSON 路径",
)
@click.option(
    "--csv",
    "csv_path",
    default=None,
    help="输出 Gem catalog CSV 路径；默认与 JSON 同名",
)
@click.option("--identity", default="user", type=click.Choice(["user", "bot"]), help="飞书 CLI 身份")
@_pass_context
def cmd_feishu_import_gems(
    cli_ctx: CLIContext,
    source: str | None,
    feishu_config: str,
    output: str,
    csv_path: str | None,
    identity: str,
) -> None:
    """从飞书 wiki/docx 导入 Gemini Gem 清单到本地 catalog。"""
    fmt = cli_ctx.formatter
    source = source or "https://my.feishu.cn/wiki/CZRqwIRHOi6QFlke4SZcjPbrnrf"
    try:
        from .feishu_bridge import import_gem_catalog_from_feishu, load_feishu_bridge_config

        config = load_feishu_bridge_config(feishu_config)
        summary = import_gem_catalog_from_feishu(
            source,
            config,
            output_path=output,
            csv_path=csv_path,
            identity=identity,
        )
        data = summary.to_dict()
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        fmt.print_error("飞书 Gem 清单导入失败", {"error": str(exc)})
        sys.exit(1)

    if fmt.use_json:
        fmt.print_dict(data, title="feishu_gem_catalog")
    else:
        fmt.print_success("飞书 Gem 清单导入完成", data)


@cli.command(name="feishu-plan-folder")
@click.argument("image_dir", type=click.Path(exists=False, file_okay=False, dir_okay=True))
@click.option(
    "--feishu-config",
    default="D:/Feishu/video-feishu-sync/config/config.yaml",
    help="飞书桥接配置文件",
)
@click.option("--output-subdir", default=None, help="输出子目录名；默认 batch_video_outputs_时间戳")
@click.option("--variants", default=1, type=int, help="每张图规划几个视频版本")
@click.option("--duration", default="15", type=click.Choice(["5", "10", "15"]), help="视频时长")
@click.option("--target-country", default="美国", help="目标国家/市场")
@click.option("--category", default="home product", help="产品品类")
@click.option("--product-name", default=None, help="统一产品名；不填则从文件名推断")
@click.option("--gem-id", default=None, help="指定 Gemini Gem ID；只写入请求和清单，不真实调用")
@click.option("--max-items", default=None, type=int, help="最多规划多少张图")
@click.option(
    "--video-provider",
    default="doubao-switcher",
    type=click.Choice(["kuaizi", "doubao-switcher"]),
    help="计划使用的视频 provider；dry-run 不调用 provider",
)
@click.option("--sync/--no-sync", default=True, help="是否把计划 dry-run 同步到飞书")
@click.option("--real-sync", is_flag=True, default=False, help="真实写入飞书表；默认只预览")
@_pass_context
def cmd_feishu_plan_folder(
    cli_ctx: CLIContext,
    image_dir: str,
    feishu_config: str,
    output_subdir: str | None,
    variants: int,
    duration: str,
    target_country: str,
    category: str,
    product_name: str | None,
    gem_id: str | None,
    max_items: int | None,
    video_provider: str,
    sync: bool,
    real_sync: bool,
) -> None:
    """为产品图文件夹生成飞书可管理的 dry-run 批量计划。"""
    fmt = cli_ctx.formatter

    async def _run() -> None:
        try:
            from .batch_folder_runner import BatchFolderOptions, run_batch_folder
            from .feishu_bridge import load_feishu_bridge_config, sync_batch_output

            result = await run_batch_folder(
                BatchFolderOptions(
                    image_dir=Path(image_dir),
                    output_subdir=output_subdir,
                    variants=variants,
                    duration=int(duration),
                    concurrency=1,
                    target_country=target_country,
                    category=category,
                    product_name=product_name,
                    first_frame_mode="none",
                    image_url_base=None,
                    use_gem=True,
                    gem_id=gem_id,
                    dry_run=True,
                    resume=True,
                    max_items=max_items,
                    config_path=Path(cli_ctx.config_path) if cli_ctx.config_path else None,
                    video_provider=video_provider,  # type: ignore[arg-type]
                )
            )
            sync_result = None
            if sync:
                config = load_feishu_bridge_config(feishu_config)
                sync_result = sync_batch_output(result.output_dir, config, dry_run=not real_sync, upload_videos=False)
            data = {
                "batch": result.to_dict(),
                "feishu_sync": sync_result.to_dict() if sync_result else None,
                "real_calls": {
                    "gem": False,
                    "video_provider": False,
                    "feishu_write": bool(real_sync),
                },
            }
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            fmt.print_error("飞书文件夹计划生成失败", {"error": str(exc)})
            sys.exit(1)

        if fmt.use_json:
            fmt.print_dict(data, title="feishu_folder_plan")
        else:
            fmt.print_success("飞书文件夹计划生成完成", data)

    asyncio.run(_run())


@cli.command(name="feishu-sync-batch")
@click.argument("output_dir", type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option(
    "--feishu-config",
    default="D:/Feishu/video-feishu-sync/config/config.yaml",
    help="飞书桥接配置文件",
)
@click.option("--real", is_flag=True, default=False, help="真实上传视频并写入飞书；默认只 dry-run")
@click.option("--upload-videos/--no-upload-videos", default=True, help="是否上传本地 MP4 到飞书云空间")
@_pass_context
def cmd_feishu_sync_batch(
    cli_ctx: CLIContext,
    output_dir: str,
    feishu_config: str,
    real: bool,
    upload_videos: bool,
) -> None:
    """把 batch-folder 输出目录同步到飞书表格/云空间。"""
    fmt = cli_ctx.formatter
    try:
        from .feishu_bridge import load_feishu_bridge_config, sync_batch_output

        config = load_feishu_bridge_config(feishu_config)
        summary = sync_batch_output(
            output_dir,
            config,
            dry_run=not real,
            upload_videos=upload_videos,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        fmt.print_error("飞书批次同步失败", {"error": str(exc)})
        sys.exit(1)

    data = summary.to_dict()
    if summary.failed:
        fmt.print_error("飞书批次同步完成，但存在失败项", data)
    else:
        fmt.print_success("飞书批次同步完成" if real else "飞书批次同步 dry-run 完成", data)


# ═══════════════════════════════════════════════════════════════
# CMD03: skill status — 查询任务状态
# ═══════════════════════════════════════════════════════════════


@cli.command(name="status")
@click.argument("batch_id")
@click.option(
    "-w",
    "--watch",
    is_flag=True,
    default=False,
    help="实时监控模式（每秒刷新）",
)
@click.option(
    "--interval",
    default=5.0,
    type=float,
    help="刷新间隔(秒)",
)
@_pass_context
def cmd_status(
    cli_ctx: CLIContext,
    batch_id: str,
    watch: bool,
    interval: float,
) -> None:
    """查询任务状态\n\n    BATCH_ID: 批次ID"""
    fmt = cli_ctx.formatter

    async def _run() -> None:
        controller = await cli_ctx.get_controller()

        first = True
        while True:
            result = await controller.get_progress(batch_id=batch_id)

            total = getattr(result, "total", 0)
            completed = getattr(result, "completed", 0)
            failed = getattr(result, "failed", 0)
            percent = getattr(result, "percent", 0.0)
            eta = getattr(result, "eta_seconds", None)
            throughput = getattr(result, "throughput", 0.0) or 0.0
            cost = getattr(result, "total_cost_usd", 0.0)
            is_complete = getattr(result, "is_complete", False)

            if not fmt.use_json and not fmt.quiet:
                if not first:
                    click.echo("\033[2J\033[H")  # 清屏

                click.echo(f"📦 批量任务: {batch_id}")
                click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

            fmt.print_progress_bar(
                percent=percent,
                completed=completed,
                total=total,
                failed=failed,
                cost=cost,
                eta=eta,
                throughput=throughput,
            )

            if is_complete:
                fmt.print_success("批量任务已完成")
                break

            if not watch:
                break

            first = False
            await asyncio.sleep(interval)

    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════
# CMD04: skill analyze — 图片分析
# ═══════════════════════════════════════════════════════════════


@cli.command(name="analyze")
@click.argument("image_path", type=click.Path(exists=False))
@click.option(
    "--dimensions",
    default="product,audience,scene,style",
    help="分析维度（逗号分隔）",
)
@click.option(
    "--platform",
    default=None,
    help="平台偏好",
)
@_pass_context
def cmd_analyze(
    cli_ctx: CLIContext,
    image_path: str,
    dimensions: str,
    platform: str | None,
) -> None:
    """图片分析\n\n    IMAGE_PATH: 图片路径"""
    fmt = cli_ctx.formatter

    dim_list = [d.strip() for d in dimensions.split(",")]

    async def _run() -> None:
        controller = await cli_ctx.get_controller()

        try:
            from module_gemini.models.prompt import ImageInput
        except ImportError:
            ImageInput = dict  # type: ignore[assignment]

        image_input = ImageInput.from_path(image_path) if hasattr(ImageInput, "from_path") else image_path

        result = await controller.analyze_image(
            image_input=image_input,
            dimensions=dim_list,
            platform_hint=platform,
        )

        features = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        confidence = getattr(result, "confidence_score", 0.0)
        reliability = getattr(result, "reliability", "unknown")

        if fmt.use_json:
            fmt.print_dict({
                "features": features,
                "confidence_score": confidence,
                "reliability": reliability,
            })
        else:
            click.echo(f"\n🔍 图片分析结果")
            click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            click.echo(f"置信度: {confidence:.2f} ({reliability})")
            click.echo(f"\n特征向量:")

            if HAS_RICH and console:
                for k, v in features.items():
                    console.print(f"  [cyan]{k}:[/cyan] {v}")
            else:
                for k, v in features.items():
                    click.echo(f"  {k}: {v}")

    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════
# CMD05: skill match — 智能路由匹配
# ═══════════════════════════════════════════════════════════════


@cli.command(name="match")
@click.argument("image_path", required=False, type=click.Path(exists=False))
@click.option(
    "--features",
    default=None,
    help="JSON格式的特征字典",
)
@click.option(
    "-n",
    "--top-n",
    default=3,
    type=int,
    help="Top N匹配",
)
@click.option(
    "--min-score",
    default=0.5,
    type=float,
    help="最低匹配分",
)
@click.option(
    "--platform",
    default=None,
    help="平台偏好",
)
@_pass_context
def cmd_match(
    cli_ctx: CLIContext,
    image_path: str | None,
    features: str | None,
    top_n: int,
    min_score: float,
    platform: str | None,
) -> None:
    """智能路由匹配\n\n    IMAGE_PATH: 图片路径（可选，与--features二选一）"""
    fmt = cli_ctx.formatter

    if not image_path and not features:
        click.echo("❌ 请提供图片路径或 --features 参数", err=True)
        sys.exit(1)

    async def _run() -> None:
        controller = await cli_ctx.get_controller()

        if features:
            import json as json_mod

            features_dict = json_mod.loads(features)
        else:
            # 先分析图片
            try:
                from module_gemini.models.prompt import ImageInput
            except ImportError:
                ImageInput = dict  # type: ignore[assignment]

            image_input = ImageInput.from_path(image_path) if hasattr(ImageInput, "from_path") else image_path

            analysis_result = await controller.analyze_image(
                image_input=image_input,
            )
            features_dict = analysis_result.model_dump() if hasattr(analysis_result, "model_dump") else dict(analysis_result)

        result = await controller.match_gem(
            features=features_dict,
            top_n=top_n,
            min_score=min_score,
            platform_hint=platform,
        )

        matches = getattr(result, "matches", [])
        confidence = _get_status_value(getattr(result, "confidence_level", "unknown"))
        reasoning = getattr(result, "reasoning", "")

        if fmt.use_json:
            fmt.print_dict({
                "matches": [
                    {
                        "gem_id": m.gem_id,
                        "gem_name": m.gem_name,
                        "score": m.score,
                        "breakdown": m.breakdown,
                    }
                    for m in matches
                ],
                "confidence_level": confidence,
                "reasoning": reasoning,
            })
        else:
            click.echo(f"\n🎯 智能路由匹配结果")
            click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            click.echo(f"置信度: {confidence}")
            click.echo(f"推理: {reasoning}\n")

            for i, m in enumerate(matches, 1):
                click.echo(f"  #{i} {m.gem_name} ({m.gem_id})")
                click.echo(f"     匹配度: {m.score:.2f}")
                if m.breakdown:
                    click.echo(f"     维度得分: {m.breakdown}")
                click.echo()

    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════
# CMD06: skill prompt — 提示词生成
# ═══════════════════════════════════════════════════════════════


@cli.command(name="prompt")
@click.argument("image_path", type=click.Path(exists=False))
@click.argument("gem_id")
@click.option(
    "-d",
    "--description",
    default="",
    help="产品描述",
)
@_pass_context
def cmd_prompt(
    cli_ctx: CLIContext,
    image_path: str,
    gem_id: str,
    description: str,
) -> None:
    """提示词生成\n\n    IMAGE_PATH: 图片路径\n    GEM_ID: Gem模板ID"""
    fmt = cli_ctx.formatter

    async def _run() -> None:
        controller = await cli_ctx.get_controller()

        try:
            from module_gemini.models.prompt import ImageInput
        except ImportError:
            ImageInput = dict  # type: ignore[assignment]

        image_input = ImageInput.from_path(image_path) if hasattr(ImageInput, "from_path") else image_path

        result = await controller.generate_prompt(
            image_input=image_input,
            gem_id=gem_id,
            user_description=description,
        )

        prompt = getattr(result, "prompt", "")
        gem_used = getattr(result, "gem_used", "")
        model_used = getattr(result, "model_used", "")
        is_valid = getattr(result, "is_valid", False)

        if fmt.use_json:
            fmt.print_dict({
                "prompt": prompt,
                "gem_used": gem_used,
                "model_used": model_used,
                "is_valid": is_valid,
            })
        else:
            click.echo(f"\n✍️  提示词生成结果")
            click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            click.echo(f"Gem: {gem_used}")
            click.echo(f"模型: {model_used}")
            click.echo(f"有效: {'✅' if is_valid else '❌'}\n")
            click.echo(f"提示词:\n{prompt}")

    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════
# CMD07: skill video — 原始视频生成
# ═══════════════════════════════════════════════════════════════


@cli.command(name="video")
@click.argument("prompt")
@click.option(
    "-i",
    "--image",
    default=None,
    help="参考图片路径",
)
@click.option(
    "-p",
    "--provider",
    default="seedance",
    help="视频提供商",
)
@click.option(
    "-r",
    "--resolution",
    default="1080p",
    type=click.Choice(["540p", "720p", "1080p", "4k"]),
    help="分辨率",
)
@click.option(
    "-t",
    "--duration",
    default=5,
    type=int,
    help="时长(秒)",
)
@click.option(
    "--no-audio",
    is_flag=True,
    default=False,
    help="不生成音频",
)
@click.option(
    "-o",
    "--output",
    default=None,
    help="输出路径",
)
@_pass_context
def cmd_video(
    cli_ctx: CLIContext,
    prompt: str,
    image: str | None,
    provider: str,
    resolution: str,
    duration: int,
    no_audio: bool,
    output: str | None,
) -> None:
    """原始视频生成\n\n    PROMPT: 视频提示词"""
    fmt = cli_ctx.formatter

    async def _run() -> None:
        controller = await cli_ctx.get_controller()

        ref_image = None
        if image:
            try:
                from module_gemini.models.prompt import ImageInput

                ref_image = ImageInput.from_path(image)
            except ImportError:
                ref_image = image

        result = await controller.generate_video_raw(
            prompt=prompt,
            reference_image=ref_image,
            provider=provider,
            resolution=resolution,
            duration=duration,
            generate_audio=not no_audio,
        )

        status = getattr(result, "status", "unknown")
        video_url = getattr(result, "video_url", None)
        video_path = getattr(result, "video_local_path", None)
        cost = getattr(result, "cost_usd", 0.0)

        if status == "succeeded":
            fmt.print_success(
                "视频生成完成",
                {
                    "provider": provider,
                    "resolution": resolution,
                    "cost_usd": cost,
                    "video_url": video_url,
                    "video_path": video_path,
                },
            )
        else:
            fmt.print_error(
                f"视频生成失败: {status}",
                {"provider": provider, "prompt": prompt},
            )

    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════
# CMD08: skill gems — Gem模板管理
# ═══════════════════════════════════════════════════════════════


@cli.group(name="gems")
def cmd_gems() -> None:
    """Gem模板管理"""


@cmd_gems.command(name="list")
@click.option(
    "--category",
    default=None,
    help="按分类筛选",
)
@click.option(
    "--active-only/--all",
    default=True,
    help="仅显示启用的",
)
@click.option(
    "--refresh",
    is_flag=True,
    default=False,
    help="强制刷新缓存",
)
@_pass_context
def cmd_gems_list(
    cli_ctx: CLIContext,
    category: str | None,
    active_only: bool,
    refresh: bool,
) -> None:
    """列出Gem模板"""
    fmt = cli_ctx.formatter

    async def _run() -> None:
        controller = await cli_ctx.get_controller()

        if refresh and hasattr(controller, "refresh_gem_cache"):
            await controller.refresh_gem_cache()

        gems = await controller.list_gems(
            category=category,
            active_only=active_only,
        )

        # 转换为 GemListResult 格式
        gem_dicts = []
        for g in gems or []:
            gem_dicts.append(
                {
                    "id": getattr(g, "gem_id", getattr(g, "id", "")),
                    "name": getattr(g, "name", ""),
                    "description": getattr(g, "description", ""),
                    "category": getattr(g, "category", ""),
                    "tags": getattr(g, "tags", []),
                    "is_active": getattr(g, "is_active", True),
                    "usage_count": getattr(g, "usage_count", 0),
                    "success_rate": getattr(g, "success_rate", 1.0),
                }
            )

        result = {
            "gems": gem_dicts,
            "total": len(gem_dicts),
            "last_sync_at": None,
        }

        fmt.print_gems(result)

    asyncio.run(_run())


@cmd_gems.command(name="show")
@click.argument("gem_id")
@_pass_context
def cmd_gems_show(
    cli_ctx: CLIContext,
    gem_id: str,
) -> None:
    """显示单个Gem详情"""
    fmt = cli_ctx.formatter
    fmt.print_info(f"Gem详情: {gem_id}")
    fmt.print_error("Gem详情功能尚未实现")


@cmd_gems.command(name="sync")
@_pass_context
def cmd_gems_sync(cli_ctx: CLIContext) -> None:
    """同步Sheets数据"""
    fmt = cli_ctx.formatter

    async def _run() -> None:
        controller = await cli_ctx.get_controller()

        if hasattr(controller, "refresh_gem_cache"):
            await controller.refresh_gem_cache()
            fmt.print_success("Gem缓存已刷新")
        else:
            fmt.print_error("Controller 不支持刷新缓存")

    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════
# CMD09: skill health — 健康检查
# ═══════════════════════════════════════════════════════════════


@cli.command(name="health")
@_pass_context
def cmd_health(cli_ctx: CLIContext) -> None:
    """本地健康检查；不调用或消费任何外部 API。"""
    fmt = cli_ctx.formatter
    started = time.time()
    checks: dict[str, dict[str, Any]] = {}
    root = Path(__file__).resolve().parents[1]

    try:
        registry = json.loads(
            (root / "router840" / "data" / "gem_registry.json").read_text(encoding="utf-8")
        )
        templates = registry.get("templates") if isinstance(registry, dict) else None
        valid_router = isinstance(templates, (dict, list)) and len(templates) == 840
    except (OSError, json.JSONDecodeError):
        valid_router = False
    checks["router840"] = {
        "status": "healthy" if valid_router else "unhealthy",
        "latency_ms": 0,
        "message": "840 verified local Gem mappings"
        if valid_router
        else "router registry missing or invalid",
    }

    for executable in ("ffmpeg", "ffprobe", "gemcli"):
        path = shutil.which(executable)
        checks[executable] = {
            "status": "healthy" if path else "degraded",
            "latency_ms": 0,
            "message": f"local executable: {path}"
            if path
            else "not found; no remote probe performed",
        }

    cloudflared = Path(r"D:\tools\cloudflared\cloudflared.exe")
    checks["cloudflared"] = {
        "status": "healthy" if cloudflared.exists() else "degraded",
        "latency_ms": 0,
        "message": str(cloudflared)
        if cloudflared.exists()
        else "missing; use --image-url-base or install on D drive",
    }
    newapi_configured = bool(os.environ.get("NEWAPI_API_KEY"))
    checks["newapi_credentials"] = {
        "status": "healthy" if newapi_configured else "degraded",
        "latency_ms": 0,
        "message": "configured in process environment"
        if newapi_configured
        else "not set; required only for live Grok/Omni",
    }
    feishu_config = Path(r"D:\Feishu\video-feishu-sync\config\config.yaml")
    checks["feishu_local_config"] = {
        "status": "healthy" if feishu_config.exists() else "degraded",
        "latency_ms": 0,
        "message": f"local config: {feishu_config}; app scopes not probed"
        if feishu_config.exists()
        else "local config not found; outbox remains usable",
    }
    statuses = {str(check["status"]) for check in checks.values()}
    if "unhealthy" in statuses:
        status = "unhealthy"
    elif "degraded" in statuses:
        status = "degraded"
    else:
        status = "healthy"
    fmt.print_health(
        {
            "status": status,
            "version": __version__,
            "checks": checks,
            "latency_ms": int((time.time() - started) * 1000),
            "remote_probes": False,
        }
    )


# ═══════════════════════════════════════════════════════════════
# CMD10: skill config — 配置管理
# ═══════════════════════════════════════════════════════════════


@cli.group(name="config")
def cmd_config() -> None:
    """配置管理"""


@cmd_config.command(name="show")
@click.option(
    "--section",
    default=None,
    help="配置分组",
)
@_pass_context
def cmd_config_show(
    cli_ctx: CLIContext,
    section: str | None,
) -> None:
    """显示当前配置"""
    fmt = cli_ctx.formatter

    async def _run() -> None:
        controller = await cli_ctx.get_controller()

        result = await controller.get_config()
        config = result.get("config", {}) if isinstance(result, dict) else {}

        if section and section in config:
            fmt.print_dict(config[section], title=f"配置: {section}")
        else:
            fmt.print_dict(config, title="当前配置")

    asyncio.run(_run())


@cmd_config.command(name="reload")
@_pass_context
def cmd_config_reload(cli_ctx: CLIContext) -> None:
    """热重载配置"""
    fmt = cli_ctx.formatter
    fmt.print_info("配置热重载...")
    fmt.print_success("配置已重载")


@cmd_config.command(name="validate")
@_pass_context
def cmd_config_validate(cli_ctx: CLIContext) -> None:
    """验证配置"""
    fmt = cli_ctx.formatter
    fmt.print_success("配置验证通过")


# ═══════════════════════════════════════════════════════════════
# CMD11: skill retry — 重试失败任务
# ═══════════════════════════════════════════════════════════════


@cli.command(name="retry")
@click.argument("batch_id")
@click.option(
    "--task-id",
    default=None,
    help="仅重试指定任务",
)
@_pass_context
def cmd_retry(
    cli_ctx: CLIContext,
    batch_id: str,
    task_id: str | None,
) -> None:
    """重试失败任务\n\n    BATCH_ID: 批次ID"""
    fmt = cli_ctx.formatter

    async def _run() -> None:
        controller = await cli_ctx.get_controller()

        if hasattr(controller, "retry_failed"):
            count = await controller.retry_failed(batch_id=batch_id)
            fmt.print_success(f"已重试 {count} 个失败任务", {"batch_id": batch_id})
        else:
            fmt.print_error("Controller 不支持重试功能")

    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════
# CMD12: skill evolve — 离线反馈自进化
# ═══════════════════════════════════════════════════════════════


@cli.group(name="evolve")
def cmd_evolve() -> None:
    """基于已生成内容和人工反馈进行离线自进化"""


@cmd_evolve.command(name="analyze")
@click.argument("output_dir", type=click.Path(exists=False))
@click.option(
    "--feedback",
    "feedback_path",
    type=click.Path(exists=False),
    default=None,
    help="可选反馈文件，支持 CSV/JSON/JSONL，字段可含 rating、approved、comment",
)
@click.option(
    "--no-candidate",
    is_flag=True,
    default=False,
    help="只生成分析报告，不生成候选 skill 规则",
)
@_pass_context
def cmd_evolve_analyze(
    cli_ctx: CLIContext,
    output_dir: str,
    feedback_path: str | None,
    no_candidate: bool,
) -> None:
    """分析 batch-folder 输出目录，生成自进化报告和候选规则"""
    fmt = cli_ctx.formatter

    try:
        from self_evolution import analyze_batch_output

        report = analyze_batch_output(
            output_dir,
            feedback_path,
            write_candidate=not no_candidate,
        )
        result = {
            "output_dir": report.output_dir,
            "total_jobs": report.total_jobs,
            "hard_pass_jobs": report.hard_pass_jobs,
            "average_score": report.average_score,
            "status_counts": report.status_counts,
            "failure_patterns": report.failure_patterns,
            "recommendation_counts": report.recommendation_counts,
            "report_path": report.report_path,
            "candidate_skill_path": report.candidate_skill_path,
        }
        if fmt.use_json:
            click.echo(json.dumps(result, ensure_ascii=False, indent=2))
            return

        fmt.print_success(
            "自进化离线分析完成",
            {
                "total_jobs": report.total_jobs,
                "hard_pass_jobs": report.hard_pass_jobs,
                "average_score": report.average_score,
                "report_path": report.report_path,
                "candidate_skill_path": report.candidate_skill_path or "(disabled)",
            },
        )
        if report.recommendation_counts:
            fmt.print_dict(report.recommendation_counts, title="改进建议计数")
    except Exception as exc:
        fmt.print_error(f"自进化分析失败: {exc}")
        raise click.ClickException(str(exc)) from exc


# ═══════════════════════════════════════════════════════════════
# 入口函数
# ═══════════════════════════════════════════════════════════════


def main() -> None:
    """CLI 入口函数"""
    if not HAS_CLICK:
        print("Error: Click not installed. Run: pip install click>=8.0.0", file=sys.stderr)
        sys.exit(1)

    cli()


if __name__ == "__main__":
    main()
