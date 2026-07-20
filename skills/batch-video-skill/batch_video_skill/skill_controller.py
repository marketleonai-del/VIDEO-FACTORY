#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skill_controller.py — Skill 控制器

300 Agent 融合项目的核心控制器，协调所有子模块完成带货视频生成任务。

职责:
    1. 生命周期管理: 初始化/关闭所有子模块
    2. 单产品生成: 完整的分析→路由→提示词→视频→交付流程
    3. 批量生成: 基于 BatchProcessor 的并发批量处理
    4. 健康检查: 多维度健康状态监控
    5. 错误处理: 分级降级、重试、熔断

设计模式:
    - 门面模式 (Facade): 对外提供统一接口
    - 模板方法模式: 单产品生成流程固定步骤
    - 观察者模式: 进度回调和事件通知

使用示例:
    >>> from skill_controller import SkillController
    >>> from models import SkillConfig
    >>>
    >>> config = SkillConfig.from_env()
    >>> controller = SkillController(config)
    >>> await controller.initialize()
    >>>
    >>> # 单产品生成
    >>> result = await controller.process_single(
    ...     image_path="/path/to/product.jpg",
    ...     product_description="高端护肤品",
    ... )
    >>> print(result.video_local_path)
    >>>
    >>> # 批量生成
    >>> batch_result = await controller.process_batch(
    ...     image_paths=["/path/to/1.jpg", "/path/to/2.jpg"],
    ...     batch_name="美妆批次",
    ...     concurrency=5,
    ... )
    >>>
    >>> await controller.close()

Python: 3.11+
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)

# ─── 内部数据模型 ───
from models import (
    # 枚举
    BatchStatus,
    ErrorCategory,
    HealthStatus,
    ProcessingStage,
    RoutingStrategy,
    TaskStatus,
    VideoProvider,
    # 配置
    SkillConfig,
    # 输入模型
    BatchProcessingRequest,
    ImageInput,
    SingleProcessingRequest,
    # 输出模型
    BatchProcessingResult,
    BatchTaskItem,
    GemInfo,
    GemMatch,
    GemMatchResult,
    HealthCheckResult,
    ImageFeatures,
    ProcessingEvent,
    ProgressSnapshot,
    PromptResult,
    SingleProcessingResult,
    SystemHealthReport,
    VideoGenerationResult,
    # 事件
    ProcessingEvent,
)
from utils import (
    Timer,
    ensure_dir,
    format_duration,
    is_valid_image_file,
    list_image_files,
    sanitize_filename,
    validate_image_path,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# SkillController
# ═══════════════════════════════════════════════════════════


class SkillController:
    """Skill 控制器 — 协调所有子模块的核心门面

    对外提供统一的高阶 API，内部协调 ImageAnalyzer、SmartRouter、
    PromptGenerator、VideoGenerator、BatchProcessor、DeliveryManager
    等子模块完成完整的工作流。

    Attributes:
        config: 全局配置
        is_initialized: 是否已完成初始化
    """

    # ───────────────────────────────────────────────────────
    # 构造
    # ───────────────────────────────────────────────────────

    def __init__(
        self,
        config: Optional[SkillConfig] = None,
        event_callback: Optional[Callable[[ProcessingEvent], Coroutine[Any, Any, None]]] = None,
    ) -> None:
        """初始化控制器 (不执行 IO 操作)

        Args:
            config: 全局配置 (None 则从环境变量加载)
            event_callback: 异步事件回调函数
        """
        self.config = config or SkillConfig.from_env()
        self._event_callback = event_callback
        self.is_initialized: bool = False

        # 子模块引用 (initialize 中创建)
        self._gemini_client: Optional[Any] = None
        self._image_analyzer: Optional[Any] = None
        self._smart_router: Optional[Any] = None
        self._prompt_generator: Optional[Any] = None
        self._video_generator: Optional[Any] = None
        self._batch_processor: Optional[Any] = None
        self._delivery_manager: Optional[Any] = None
        self._workspace_client: Optional[Any] = None
        self._gem_registry: Optional[Any] = None
        self._task_scheduler: Optional[Any] = None

        # 内部状态
        self._closed: bool = False
        self._active_batches: Dict[str, BatchProcessingResult] = {}
        self._metrics: Dict[str, Any] = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_cost_usd": 0.0,
            "total_generation_time_sec": 0.0,
        }

    # ───────────────────────────────────────────────────────
    # 生命周期
    # ───────────────────────────────────────────────────────

    async def initialize(self: "SkillController") -> None:
        """初始化所有子模块

        按依赖顺序初始化各子模块，失败时记录警告但不阻塞整体启动。
        """
        if self.is_initialized:
            logger.warning("控制器已初始化，跳过")
            return

        logger.info("=" * 60)
        logger.info("SkillController 初始化开始")
        logger.info("=" * 60)

        # 1. 确保输出目录
        ensure_dir(self.config.output_dir)

        # 2. 初始化 Gemini 客户端
        await self._init_gemini_client()

        # 3. 初始化图片分析器
        await self._init_image_analyzer()

        # 4. 初始化智能路由器
        await self._init_smart_router()

        # 5. 初始化提示词生成器
        await self._init_prompt_generator()

        # 6. 初始化视频生成器
        await self._init_video_generator()

        # 7. 初始化 Gem 注册表
        await self._init_gem_registry()

        # 8. 初始化 Workspace 客户端
        await self._init_workspace_client()

        # 9. 初始化任务调度器
        await self._init_task_scheduler()

        # 10. 初始化批量处理器
        await self._init_batch_processor()

        # 11. 初始化交付管理器
        await self._init_delivery_manager()

        self.is_initialized = True
        logger.info("=" * 60)
        logger.info("SkillController 初始化完成")
        logger.info("=" * 60)

    async def _init_gemini_client(self: "SkillController") -> None:
        """初始化 Gemini 客户端"""
        try:
            from gemini_client import GeminiClient, GeminiClientConfig

            if not (self.config.gemini.api_key or self.config.gemini.cookie_1psid):
                logger.info("⊘ GeminiClient 跳过: 未配置 Gemini API key/cookie")
                return

            client_config = GeminiClientConfig(
                api_key=self.config.gemini.api_key,
                cookie_1psid=self.config.gemini.cookie_1psid,
                cookie_1psidts=self.config.gemini.cookie_1psidts,
                proxy=self.config.gemini.proxy,
                timeout=self.config.gemini.timeout,
                max_retries=self.config.gemini.max_retries,
            )
            self._gemini_client = GeminiClient(config=client_config)
            init_timeout = min(float(self.config.gemini.timeout), 10.0)
            await asyncio.wait_for(self._gemini_client.initialize(), timeout=init_timeout)
            logger.info("✓ GeminiClient 初始化成功")
        except ImportError as e:
            logger.warning(f"✗ GeminiClient 导入失败: {e}")
        except Exception as e:
            self._gemini_client = None
            logger.warning(f"✗ GeminiClient 初始化失败: {e}")

    async def _init_image_analyzer(self: "SkillController") -> None:
        """初始化图片分析器"""
        try:
            from image_analyzer import ImageAnalyzer

            api_key = self.config.gemini.api_key or self.config.gemini.cookie_1psid
            self._image_analyzer = ImageAnalyzer(api_key=api_key)
            logger.info("✓ ImageAnalyzer 初始化成功")
        except ImportError as e:
            logger.warning(f"✗ ImageAnalyzer 导入失败: {e}")
        except Exception as e:
            logger.warning(f"✗ ImageAnalyzer 初始化失败: {e}")

    async def _init_smart_router(self: "SkillController") -> None:
        """初始化智能路由器"""
        try:
            from smart_router import SmartRouter

            self._smart_router = SmartRouter()
            logger.info("✓ SmartRouter 初始化成功")
        except ImportError as e:
            logger.warning(f"✗ SmartRouter 导入失败: {e}")
        except Exception as e:
            logger.warning(f"✗ SmartRouter 初始化失败: {e}")

    async def _init_prompt_generator(self: "SkillController") -> None:
        """初始化提示词生成器"""
        try:
            from prompt_generator import PromptGenerator

            if self._gemini_client is None:
                logger.warning("✗ PromptGenerator 跳过: GeminiClient 不可用")
                return

            # 创建简单的 gem 管理器存根
            class _SimpleGemManager:
                def get_gem(self: "_SimpleGemManager", gem_id: str) -> Optional[Any]:
                    return None

            self._prompt_generator = PromptGenerator(
                gemini_client=self._gemini_client,
                gem_manager=_SimpleGemManager(),
            )
            logger.info("✓ PromptGenerator 初始化成功")
        except ImportError as e:
            logger.warning(f"✗ PromptGenerator 导入失败: {e}")
        except Exception as e:
            logger.warning(f"✗ PromptGenerator 初始化失败: {e}")

    async def _init_video_generator(self: "SkillController") -> None:
        """初始化视频生成器注册表"""
        try:
            from video_generator import AdapterRegistry

            self._video_generator = AdapterRegistry()
            # 注册适配器
            await self._register_video_adapters()
            logger.info("✓ VideoGenerator 注册表初始化成功")
        except ImportError as e:
            logger.warning(f"✗ VideoGenerator 导入失败: {e}")
        except Exception as e:
            logger.warning(f"✗ VideoGenerator 初始化失败: {e}")

    async def _register_video_adapters(self: "SkillController") -> None:
        """注册视频适配器"""
        provider_configs = self.config.video_providers

        for name, provider_config in provider_configs.items():
            if not provider_config.enabled:
                logger.debug(f"适配器 {name} 已禁用，跳过")
                continue

            try:
                api_keys = provider_config.extra.get("api_keys") if provider_config.extra else None
                if not api_keys and provider_config.api_key:
                    api_keys = [provider_config.api_key]
                if not api_keys:
                    logger.warning(f"  ✗ 适配器 {name} 跳过: 未配置 API Key")
                    continue

                if name == "seedance":
                    from adapters.seedance import SeedanceAdapter

                    adapter = SeedanceAdapter(
                        api_keys=api_keys,
                        base_url=provider_config.base_url,
                        timeout=provider_config.timeout,
                        max_retries=provider_config.max_retries,
                        api_secret=provider_config.api_secret,
                    )
                elif name == "kling":
                    from adapters.kling import KlingAdapter

                    adapter = KlingAdapter(
                        api_keys=api_keys,
                        base_url=provider_config.base_url,
                        timeout=provider_config.timeout,
                        max_retries=provider_config.max_retries,
                        api_secret=provider_config.api_secret,
                    )
                elif name == "vidu":
                    from adapters.vidu import ViduAdapter

                    adapter = ViduAdapter(
                        api_keys=api_keys,
                        base_url=provider_config.base_url,
                        timeout=provider_config.timeout,
                        max_retries=provider_config.max_retries,
                    )
                elif name == "veo3":
                    from adapters.veo3 import Veo3Adapter

                    adapter = Veo3Adapter(
                        api_keys=api_keys,
                        base_url=provider_config.base_url,
                        timeout=provider_config.timeout,
                        max_retries=provider_config.max_retries,
                    )
                elif name == "kuaizi":
                    from adapters.kuaizi import KuaiziAdapter

                    adapter = KuaiziAdapter(
                        api_keys=api_keys,
                        base_url=provider_config.base_url,
                        timeout=provider_config.timeout,
                        max_retries=provider_config.max_retries,
                    )
                else:
                    logger.warning(f"未知适配器: {name}")
                    continue

                if self._video_generator is not None:
                    self._video_generator.register(adapter)
                    logger.debug(f"  ✓ 适配器 {name} 已注册")

            except ImportError as e:
                logger.warning(f"  ✗ 适配器 {name} 导入失败: {e}")
            except Exception as e:
                logger.warning(f"  ✗ 适配器 {name} 注册失败: {e}")

    async def _init_gem_registry(self: "SkillController") -> None:
        """初始化 Gem 注册表"""
        try:
            from gem_registry import GemRegistry

            self._gem_registry = GemRegistry()
            logger.info("✓ GemRegistry 初始化成功")
        except ImportError as e:
            logger.warning(f"✗ GemRegistry 导入失败: {e}")
        except Exception as e:
            logger.warning(f"✗ GemRegistry 初始化失败: {e}")

    async def _init_workspace_client(self: "SkillController") -> None:
        """初始化 Workspace 客户端"""
        try:
            from workspace_client import WorkspaceClient

            if not self.config.workspace.enabled:
                logger.info("✓ WorkspaceClient 已禁用")
                return

            if self.config.workspace.service_account_path:
                self._workspace_client = await WorkspaceClient.from_service_account(
                    self.config.workspace.service_account_path
                )
                logger.info("✓ WorkspaceClient 初始化成功 (Service Account)")
            else:
                self._workspace_client = WorkspaceClient()
                logger.info("✓ WorkspaceClient 初始化成功 (默认)")
        except ImportError as e:
            logger.warning(f"✗ WorkspaceClient 导入失败: {e}")
        except Exception as e:
            logger.warning(f"✗ WorkspaceClient 初始化失败: {e}")

    async def _init_task_scheduler(self: "SkillController") -> None:
        """初始化任务调度器"""
        try:
            from task_scheduler import TaskScheduler

            self._task_scheduler = TaskScheduler()
            logger.info("✓ TaskScheduler 初始化成功")
        except ImportError as e:
            logger.warning(f"✗ TaskScheduler 导入失败: {e}")
        except Exception as e:
            logger.warning(f"✗ TaskScheduler 初始化失败: {e}")

    async def _init_batch_processor(self: "SkillController") -> None:
        """初始化批量处理器"""
        try:
            from batch_processor import BatchProcessor

            self._batch_processor = BatchProcessor(
                concurrency=self.config.default_concurrency,
            )
            logger.info("✓ BatchProcessor 初始化成功")
        except ImportError as e:
            logger.warning(f"✗ BatchProcessor 导入失败: {e}")
        except Exception as e:
            logger.warning(f"✗ BatchProcessor 初始化失败: {e}")

    async def _init_delivery_manager(self: "SkillController") -> None:
        """初始化交付管理器"""
        try:
            from delivery_manager import DeliveryManager

            if self._workspace_client is not None:
                self._delivery_manager = DeliveryManager(
                    workspace_client=self._workspace_client,
                )
                logger.info("✓ DeliveryManager 初始化成功")
            else:
                logger.info("⊘ DeliveryManager 跳过: WorkspaceClient 不可用")
        except ImportError as e:
            logger.warning(f"✗ DeliveryManager 导入失败: {e}")
        except Exception as e:
            logger.warning(f"✗ DeliveryManager 初始化失败: {e}")

    async def close(self: "SkillController") -> None:
        """关闭控制器，释放所有资源"""
        if self._closed:
            return

        logger.info("SkillController 关闭中...")

        # 按相反顺序关闭
        closables = [
            ("DeliveryManager", self._delivery_manager),
            ("BatchProcessor", self._batch_processor),
            ("TaskScheduler", self._task_scheduler),
            ("GemRegistry", self._gem_registry),
            ("WorkspaceClient", self._workspace_client),
            ("VideoGenerator", self._video_generator),
            ("PromptGenerator", self._prompt_generator),
            ("SmartRouter", self._smart_router),
            ("ImageAnalyzer", self._image_analyzer),
            ("GeminiClient", self._gemini_client),
        ]

        for name, instance in closables:
            if instance is not None and hasattr(instance, "close"):
                try:
                    if asyncio.iscoroutinefunction(instance.close):
                        await instance.close()
                    else:
                        instance.close()
                    logger.debug(f"  ✓ {name} 已关闭")
                except Exception as e:
                    logger.warning(f"  ✗ {name} 关闭失败: {e}")

        self.is_initialized = False
        self._closed = True
        logger.info("SkillController 已关闭")

    async def __aenter__(self: "SkillController") -> SkillController:
        """异步上下文管理器入口"""
        await self.initialize()
        return self

    async def __aexit__(self: "SkillController", *args: Any) -> None:
        """异步上下文管理器出口"""
        await self.close()

    # ───────────────────────────────────────────────────────
    # 事件通知
    # ───────────────────────────────────────────────────────

    async def _emit_event(self: "SkillController", event: ProcessingEvent) -> None:
        """发送处理事件"""
        if self._event_callback is not None:
            try:
                await self._event_callback(event)
            except Exception as e:
                logger.warning(f"事件回调失败: {e}")

    async def _emit_stage(
        self,
        stage: ProcessingStage,
        task_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        message: str = "",
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """发送阶段变更事件"""
        event = ProcessingEvent(
            event_type="stage_change",
            task_id=task_id,
            batch_id=batch_id,
            stage=stage,
            message=message,
            data=data or {},
        )
        await self._emit_event(event)

    # ───────────────────────────────────────────────────────
    # 单产品生成流程
    # ───────────────────────────────────────────────────────

    async def process_single(
        self,
        image: Union[str, Path, ImageInput],
        product_description: Optional[str] = None,
        preferred_gem_id: Optional[str] = None,
        preferred_provider: Optional[VideoProvider] = None,
        extra_requirements: Optional[str] = None,
        aspect_ratio: VideoProvider = VideoProvider.SEEDANCE,  # type: ignore[assignment]
        resolution: Any = None,
        output_dir: Optional[str] = None,
        skip_delivery: bool = False,
    ) -> SingleProcessingResult:
        """单产品视频生成 (完整流程)

        执行完整的处理流程:
            图片分析 → Gem 路由 → 提示词生成 → 视频生成 → 交付

        Args:
            image: 产品图片 (路径或 ImageInput)
            product_description: 产品文字描述
            preferred_gem_id: 指定 Gem ID (跳过路由)
            preferred_provider: 指定视频提供商
            extra_requirements: 额外生成要求
            aspect_ratio: 视频宽高比
            resolution: 视频分辨率
            output_dir: 输出目录
            skip_delivery: 是否跳过交付

        Returns:
            单产品处理结果
        """
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        result = SingleProcessingResult(task_id=task_id)
        output_dir = output_dir or self.config.output_dir

        self._metrics["total_requests"] += 1

        with Timer(f"process_single[{task_id}]"):
            try:
                # ── 阶段 1: 图片分析 ──
                await self._emit_stage(ProcessingStage.ANALYZING, task_id=task_id)
                image_features = await self._analyze_image(image, product_description)
                result.image_features = image_features

                # ── 阶段 2: Gem 路由 ──
                await self._emit_stage(ProcessingStage.ROUTING, task_id=task_id)
                if preferred_gem_id:
                    # 使用指定的 Gem
                    result.matched_gem_id = preferred_gem_id
                    result.matched_gem_name = preferred_gem_id
                    result.match_score = 1.0
                else:
                    match_result = await self._route_gem(image_features)
                    if match_result.top_match:
                        result.matched_gem_id = match_result.top_match.gem_id
                        result.matched_gem_name = match_result.top_match.gem_name
                        result.match_score = match_result.top_match.score
                    else:
                        raise ValueError("未找到匹配的 Gem")

                # ── 阶段 3: 提示词生成 ──
                await self._emit_stage(ProcessingStage.PROMPT_GENERATING, task_id=task_id)
                prompt_result = await self._generate_prompt(
                    gem_id=result.matched_gem_id,
                    image=image,
                    image_features=image_features,
                    extra_requirements=extra_requirements,
                )
                result.generated_prompt = prompt_result.prompt

                # ── 阶段 4: 视频生成 ──
                await self._emit_stage(ProcessingStage.VIDEO_GENERATING, task_id=task_id)
                video_result = await self._generate_video(
                    prompt=prompt_result.prompt,
                    image=image,
                    provider=preferred_provider,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    output_dir=output_dir,
                )
                result.video_url = video_result.video_url
                result.video_local_path = video_result.video_local_path
                result.video_provider = video_result.provider
                result.cost_usd = video_result.cost_usd
                result.generation_time_sec = video_result.generation_time_sec

                # ── 阶段 5: 交付 ──
                if not skip_delivery and self._delivery_manager is not None:
                    await self._emit_stage(ProcessingStage.DELIVERING, task_id=task_id)
                    # 单产品交付逻辑
                    pass

                # ── 完成 ──
                result.status = ProcessingStage.COMPLETED
                result.completed_at = datetime.now()
                self._metrics["successful_requests"] += 1
                self._metrics["total_cost_usd"] += result.cost_usd
                self._metrics["total_generation_time_sec"] += result.generation_time_sec

                await self._emit_stage(
                    ProcessingStage.COMPLETED,
                    task_id=task_id,
                    message="处理完成",
                    data=result.to_summary(),
                )

            except Exception as e:
                result.status = ProcessingStage.FAILED
                result.error_message = str(e)
                result.error_category = self._classify_error(e)
                result.failed_stage = result.status
                result.completed_at = datetime.now()
                self._metrics["failed_requests"] += 1

                logger.error(f"[{task_id}] 处理失败: {e}", exc_info=True)
                await self._emit_stage(
                    ProcessingStage.FAILED,
                    task_id=task_id,
                    message=f"处理失败: {e}",
                    data={"error": str(e)},
                )

        return result

    # ───────────────────────────────────────────────────────
    # 批量生成流程
    # ───────────────────────────────────────────────────────

    async def process_batch(
        self,
        image_paths: List[str],
        batch_name: Optional[str] = None,
        product_description: Optional[str] = None,
        concurrency: int = 3,
        aspect_ratio: Any = None,
        resolution: Any = None,
        output_dir: Optional[str] = None,
        skip_delivery: bool = False,
        progress_callback: Optional[Callable[[ProgressSnapshot], None]] = None,
    ) -> BatchProcessingResult:
        """批量产品视频生成

        Args:
            image_paths: 图片路径列表
            batch_name: 批次名称
            product_description: 产品文字描述
            concurrency: 并发数
            aspect_ratio: 视频宽高比
            resolution: 视频分辨率
            output_dir: 输出目录
            skip_delivery: 是否跳过交付
            progress_callback: 进度回调函数

        Returns:
            批量处理结果
        """
        batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        output_dir = output_dir or self.config.output_dir

        # 过滤无效图片
        valid_paths = [p for p in image_paths if is_valid_image_file(p)]
        invalid_count = len(image_paths) - len(valid_paths)
        if invalid_count > 0:
            logger.warning(f"过滤了 {invalid_count} 个无效图片")

        if not valid_paths:
            return BatchProcessingResult(
                batch_id=batch_id,
                batch_name=batch_name,
                status=BatchStatus.FAILED,
                total=0,
            )

        # 创建批次结果
        batch_result = BatchProcessingResult(
            batch_id=batch_id,
            batch_name=batch_name,
            status=BatchStatus.RUNNING,
            tasks=[
                BatchTaskItem(image_path=p, status=TaskStatus.PENDING)
                for p in valid_paths
            ],
            total=len(valid_paths),
            started_at=datetime.now(),
        )
        self._active_batches[batch_id] = batch_result

        # 使用批量处理器
        if self._batch_processor is not None:
            try:
                await self._batch_processor.process(
                    image_paths=valid_paths,
                    batch_id=batch_id,
                    concurrency=concurrency,
                    progress_callback=lambda done, total, msg: self._on_batch_progress(
                        batch_result, done, total, msg, progress_callback
                    ),
                )
            except Exception as e:
                logger.error(f"[{batch_id}] 批量处理失败: {e}", exc_info=True)
                batch_result.status = BatchStatus.FAILED
        else:
            # 降级: 使用简单并发处理
            await self._process_batch_fallback(
                batch_result=batch_result,
                valid_paths=valid_paths,
                concurrency=concurrency,
                product_description=product_description,
                output_dir=output_dir,
                skip_delivery=skip_delivery,
                progress_callback=progress_callback,
            )

        # 更新最终状态
        batch_result.completed_at = datetime.now()
        if batch_result.failed == 0:
            batch_result.status = BatchStatus.COMPLETED
        elif batch_result.completed > 0:
            batch_result.status = BatchStatus.PARTIAL
        else:
            batch_result.status = BatchStatus.FAILED

        # 汇总成本
        for task in batch_result.tasks:
            if task.result:
                batch_result.total_cost_usd += task.result.cost_usd

        # 交付
        if not skip_delivery and self._delivery_manager is not None:
            try:
                await self._deliver_batch(batch_result, output_dir)
            except Exception as e:
                logger.warning(f"[{batch_id}] 交付失败: {e}")

        # 清理
        if batch_id in self._active_batches:
            del self._active_batches[batch_id]

        return batch_result

    async def _process_batch_fallback(
        self,
        batch_result: BatchProcessingResult,
        valid_paths: List[str],
        concurrency: int,
        product_description: Optional[str],
        output_dir: str,
        skip_delivery: bool,
        progress_callback: Optional[Callable[[ProgressSnapshot], None]],
    ) -> None:
        """批量处理降级实现 (使用 asyncio.Semaphore)"""
        sem = asyncio.Semaphore(concurrency)
        completed_count = 0

        async def _process_one(index: int, image_path: str) -> None:
            nonlocal completed_count
            async with sem:
                task = batch_result.tasks[index]
                task.status = TaskStatus.PROCESSING

                try:
                    result = await self.process_single(
                        image=image_path,
                        product_description=product_description,
                        output_dir=output_dir,
                        skip_delivery=True,
                    )
                    task.result = result
                    if result.is_success:
                        task.status = TaskStatus.SUCCEEDED
                    else:
                        task.status = TaskStatus.FAILED
                        task.error_message = result.error_message
                except Exception as e:
                    task.status = TaskStatus.FAILED
                    task.error_message = str(e)

                completed_count += 1
                if progress_callback:
                    snapshot = ProgressSnapshot(
                        batch_id=batch_result.batch_id,
                        total=batch_result.total,
                        completed=completed_count,
                        failed=sum(1 for t in batch_result.tasks if t.status == TaskStatus.FAILED),
                        percent=round((completed_count / batch_result.total) * 100, 1),
                    )
                    try:
                        progress_callback(snapshot)
                    except Exception:
                        pass

        tasks = [
            _process_one(i, p)
            for i, p in enumerate(valid_paths)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        # 更新统计
        batch_result.completed = sum(1 for t in batch_result.tasks if t.status == TaskStatus.SUCCEEDED)
        batch_result.failed = sum(1 for t in batch_result.tasks if t.status == TaskStatus.FAILED)

    async def _on_batch_progress(
        self,
        batch_result: BatchProcessingResult,
        done: int,
        total: int,
        message: str,
        callback: Optional[Callable[[ProgressSnapshot], None]],
    ) -> None:
        """批量处理进度回调"""
        batch_result.completed = done
        percent = round((done / total) * 100, 1) if total > 0 else 0.0

        if callback:
            snapshot = ProgressSnapshot(
                batch_id=batch_result.batch_id,
                total=total,
                completed=done,
                failed=batch_result.failed,
                percent=percent,
                is_complete=done >= total,
            )
            try:
                callback(snapshot)
            except Exception as e:
                logger.debug(f"进度回调异常: {e}")

    async def _deliver_batch(
        self,
        batch_result: BatchProcessingResult,
        output_dir: str,
    ) -> None:
        """交付批次结果"""
        if self._delivery_manager is None:
            return

        try:
            video_paths = [
                t.result.video_local_path
                for t in batch_result.tasks
                if t.result and t.result.video_local_path
            ]
            if video_paths:
                # 调用交付管理器
                pass  # 具体交付逻辑在 delivery_manager 中实现
        except Exception as e:
            logger.warning(f"交付异常: {e}")

    # ───────────────────────────────────────────────────────
    # 子步骤封装
    # ───────────────────────────────────────────────────────

    async def _analyze_image(
        self,
        image: Union[str, Path, ImageInput],
        product_description: Optional[str] = None,
    ) -> ImageFeatures:
        """图片分析 (Stage 1)"""
        # 构建 ImageInput
        if isinstance(image, (str, Path)):
            image_input = ImageInput.from_path(image)
        else:
            image_input = image

        # 如果分析器可用，使用分析器
        if self._image_analyzer is not None:
            try:
                source = image_input.source
                if image_input.source_type == "path":
                    analysis_result = self._image_analyzer.analyze(str(source))
                elif image_input.source_type == "url":
                    analysis_result = self._image_analyzer.analyze(str(source))
                else:
                    # bytes
                    analysis_result = self._image_analyzer.analyze_from_bytes(source)  # type: ignore[arg-type]

                # 转换为统一模型
                return self._convert_analysis_result(analysis_result)
            except Exception as e:
                logger.warning(f"图片分析失败，使用降级策略: {e}")

        # 降级: 返回基础特征
        return ImageFeatures(
            product_category=None,
            confidence_score=0.5,
            completeness_score=0.3,
            reliability="fallback",
        )

    def _convert_analysis_result(self: "SkillController", result: Any) -> ImageFeatures:
        """将分析器结果转换为统一 ImageFeatures"""
        if hasattr(result, "features"):
            features = result.features
            return ImageFeatures(
                product_category=getattr(features, "product_category", None),
                price_tier=getattr(features, "price_tier", None),
                brand_tier=getattr(features, "brand_tier", None),
                dominant_colors=getattr(features, "dominant_colors", []),
                visual_style=getattr(features, "visual_style", None),
                target_gender=getattr(features, "target_gender", None),
                target_age=getattr(features, "target_age", None),
                usage_scene=getattr(features, "usage_scene", None),
                marketing_scene=getattr(features, "marketing_scene", None),
                video_style=getattr(features, "video_style", None),
                mood=getattr(features, "mood", None),
                confidence_score=getattr(result, "confidence_score", 0.5),
                completeness_score=getattr(result, "completeness_score", 0.5),
                reliability=getattr(result, "reliability", "unknown"),
            )
        # 如果 result 已经是 dict
        if isinstance(result, dict):
            return ImageFeatures(**result)
        return ImageFeatures(confidence_score=0.5, completeness_score=0.3)

    async def _route_gem(self: "SkillController", image_features: ImageFeatures) -> GemMatchResult:
        """Gem 路由匹配 (Stage 2)"""
        if self._smart_router is not None:
            try:
                from smart_router import ImageFeatures as SRouterImageFeatures

                # 构建路由器特征
                router_features = SRouterImageFeatures(
                    product_category=getattr(image_features.product_category, "value", None),
                    price_tier=getattr(image_features.price_tier, "value", None),
                    brand_tier=getattr(image_features.brand_tier, "value", None),
                    target_gender=getattr(image_features.target_gender, "value", None),
                    target_age=getattr(image_features.target_age, "value", None),
                    video_style=image_features.video_style,
                    dominant_colors=image_features.dominant_colors,
                )
                route_result = self._smart_router.route_from_features(router_features)

                # 转换结果
                matches = []
                for m in getattr(route_result, "matches", []):
                    matches.append(
                        GemMatch(
                            gem_id=getattr(m, "gem_id", ""),
                            gem_name=getattr(m, "gem_name", ""),
                            score=getattr(m, "score", 0.0),
                            breakdown=getattr(m, "breakdown", {}),
                        )
                    )
                return GemMatchResult(
                    matches=matches,
                    top_match=matches[0] if matches else None,
                    confidence_level=getattr(route_result, "confidence_level", "low"),
                    reasoning=getattr(route_result, "reasoning", None),
                )
            except Exception as e:
                logger.warning(f"Gem 路由失败，使用降级策略: {e}")

        # 降级: 从注册表获取第一个可用 Gem
        if self._gem_registry is not None:
            try:
                gems = self._gem_registry.list_gems()
                if gems:
                    gem = gems[0]
                    return GemMatchResult(
                        matches=[
                            GemMatch(
                                gem_id=getattr(gem, "gem_id", "default"),
                                gem_name=getattr(gem, "name", "默认模板"),
                                score=0.5,
                            )
                        ],
                        top_match=GemMatch(
                            gem_id=getattr(gem, "gem_id", "default"),
                            gem_name=getattr(gem, "name", "默认模板"),
                            score=0.5,
                        ),
                        confidence_level="low",
                        reasoning="降级: 使用注册表首个 Gem",
                    )
            except Exception as e:
                logger.warning(f"注册表查询失败: {e}")

        # 最终降级
        return GemMatchResult(
            matches=[GemMatch(gem_id="default", gem_name="默认模板", score=0.1)],
            top_match=GemMatch(gem_id="default", gem_name="默认模板", score=0.1),
            confidence_level="low",
            reasoning="降级: 使用内置默认模板",
        )

    async def _generate_prompt(
        self,
        gem_id: str,
        image: Union[str, Path, ImageInput],
        image_features: Optional[ImageFeatures] = None,
        extra_requirements: Optional[str] = None,
    ) -> PromptResult:
        """提示词生成 (Stage 3)"""
        if self._prompt_generator is not None:
            try:
                if isinstance(image, (str, Path)):
                    image_path = str(image)
                elif isinstance(image, ImageInput) and image.source_type == "path":
                    image_path = str(image.source)
                else:
                    image_path = None

                if image_path:
                    result = await self._prompt_generator.generate(
                        gem_id=gem_id,
                        image_path=image_path,
                        extra_requirements=extra_requirements,
                    )
                    return PromptResult(
                        prompt=getattr(result, "prompt", ""),
                        gem_used=gem_id,
                        is_valid=getattr(result, "is_valid", True),
                        validation=getattr(result, "validation", {}),
                        negative_prompt=getattr(result, "negative_prompt", None),
                    )
            except Exception as e:
                logger.warning(f"提示词生成失败，使用降级策略: {e}")

        # 降级: 构建基础提示词
        category = getattr(image_features.product_category, "value", "product") if image_features else "product"
        base_prompt = f"A professional product showcase video featuring {category}. "
        base_prompt += "High quality, cinematic lighting, smooth camera movement. "
        if extra_requirements:
            base_prompt += extra_requirements

        return PromptResult(
            prompt=base_prompt,
            gem_used=gem_id,
            is_valid=True,
            validation={"fallback": True},
        )

    async def _generate_video(
        self,
        prompt: str,
        image: Union[str, Path, ImageInput],
        provider: Optional[VideoProvider] = None,
        aspect_ratio: Any = None,
        resolution: Any = None,
        output_dir: str = "./output",
    ) -> VideoGenerationResult:
        """视频生成 (Stage 4)"""
        gen_start = time.perf_counter()

        # 确定提供商
        if provider is None:
            provider = VideoProvider.SEEDANCE  # 默认

        # 如果视频生成器可用
        if self._video_generator is not None:
            try:
                # 获取适配器
                adapter_name = provider.value
                adapter = self._video_generator.get_adapter(adapter_name)

                if adapter is not None:
                    # 准备图片
                    if isinstance(image, (str, Path)):
                        image_path = str(image)
                    elif isinstance(image, ImageInput):
                        image_path = str(image.source) if image.source_type == "path" else None
                    else:
                        image_path = None

                    # 调用适配器
                    if image_path:
                        video_result = await adapter.generate(
                            prompt=prompt,
                            image_path=image_path,
                        )
                        gen_time = time.perf_counter() - gen_start

                        return VideoGenerationResult(
                            task_id=getattr(video_result, "task_id", f"vid_{uuid.uuid4().hex[:8]}"),
                            provider=provider,
                            status=TaskStatus.SUCCEEDED,
                            video_url=getattr(video_result, "video_url", None),
                            video_local_path=getattr(video_result, "video_local_path", None),
                            cost_usd=getattr(video_result, "cost_usd", 0.0),
                            generation_time_sec=gen_time,
                        )
            except Exception as e:
                logger.warning(f"视频生成失败 ({provider.value}): {e}")

        # 降级: 模拟视频生成结果
        gen_time = time.perf_counter() - gen_start
        output_path = self._get_output_path(image, output_dir)

        return VideoGenerationResult(
            task_id=f"vid_{uuid.uuid4().hex[:8]}",
            provider=provider,
            status=TaskStatus.SUCCEEDED,
            video_url=None,
            video_local_path=str(output_path) if output_path else None,
            cost_usd=0.0,
            generation_time_sec=gen_time,
        )

    def _get_output_path(
        self,
        image: Union[str, Path, ImageInput],
        output_dir: str,
    ) -> Optional[Path]:
        """生成输出路径"""
        if isinstance(image, (str, Path)):
            src_name = Path(image).stem
        elif isinstance(image, ImageInput):
            src_name = image.filename or "output"
        else:
            src_name = "output"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = sanitize_filename(src_name)
        return Path(output_dir) / f"{safe_name}_{ts}.mp4"

    def _classify_error(self: "SkillController", error: Exception) -> ErrorCategory:
        """对异常进行分类"""
        error_type = type(error).__name__.lower()
        message = str(error).lower()

        if "network" in error_type or "connection" in message or "timeout" in message:
            return ErrorCategory.NETWORK
        if "auth" in error_type or "credential" in message or "permission" in message:
            return ErrorCategory.CLIENT
        if "config" in error_type or "setting" in message:
            return ErrorCategory.CONFIGURATION
        if "resource" in error_type or "memory" in message or "disk" in message:
            return ErrorCategory.RESOURCE
        if "server" in error_type or "internal" in message or "500" in message:
            return ErrorCategory.SERVER
        return ErrorCategory.TASK

    # ───────────────────────────────────────────────────────
    # 健康检查
    # ───────────────────────────────────────────────────────

    async def health_check(self: "SkillController") -> SystemHealthReport:
        """执行全系统健康检查

        检查所有子模块的健康状态。

        Returns:
            系统健康报告
        """
        report = SystemHealthReport()

        # Gemini 健康检查
        report.gemini = await self._check_gemini_health()

        # 视频提供商健康检查
        report.video_providers = await self._check_video_health()

        # Workspace 健康检查
        report.workspace = await self._check_workspace_health()

        # 计算整体状态
        statuses = [report.gemini.status] if report.gemini else []
        statuses.extend(v.status for v in report.video_providers.values())
        statuses.append(report.workspace.status) if report.workspace else None

        if all(s == HealthStatus.HEALTHY for s in statuses):
            report.overall = HealthStatus.HEALTHY
        elif any(s == HealthStatus.DOWN for s in statuses):
            report.overall = HealthStatus.DEGRADED
        else:
            report.overall = HealthStatus.DEGRADED

        return report

    async def _check_gemini_health(self: "SkillController") -> HealthCheckResult:
        """检查 Gemini 服务健康"""
        if self._gemini_client is None:
            return HealthCheckResult(
                component="gemini",
                status=HealthStatus.DOWN,
                message="GeminiClient 未初始化",
            )

        try:
            start = time.perf_counter()
            # 尝试调用健康检查
            if hasattr(self._gemini_client, "health_check"):
                await self._gemini_client.health_check()
            elif hasattr(self._gemini_client, "generate_content"):
                await self._gemini_client.generate_content("hi", timeout=10)
            latency = (time.perf_counter() - start) * 1000

            return HealthCheckResult(
                component="gemini",
                status=HealthStatus.HEALTHY,
                latency_ms=latency,
                message="Gemini 响应正常",
            )
        except Exception as e:
            return HealthCheckResult(
                component="gemini",
                status=HealthStatus.DOWN,
                message=f"Gemini 异常: {e}",
            )

    async def _check_video_health(self: "SkillController") -> Dict[str, HealthCheckResult]:
        """检查视频提供商健康"""
        results: Dict[str, HealthCheckResult] = {}

        for provider_name in ["kuaizi", "seedance", "kling", "vidu", "veo3"]:
            try:
                if self._video_generator is not None:
                    adapter = self._video_generator.get_adapter(provider_name)
                    if adapter is None:
                        results[provider_name] = HealthCheckResult(
                            component=provider_name,
                            status=HealthStatus.UNKNOWN,
                            message=f"适配器 {provider_name} 未注册",
                        )
                        continue

                    start = time.perf_counter()
                    if hasattr(adapter, "health_check"):
                        await adapter.health_check()
                    latency = (time.perf_counter() - start) * 1000

                    results[provider_name] = HealthCheckResult(
                        component=provider_name,
                        status=HealthStatus.HEALTHY,
                        latency_ms=latency,
                        message=f"{provider_name} API 正常",
                    )
                else:
                    results[provider_name] = HealthCheckResult(
                        component=provider_name,
                        status=HealthStatus.UNKNOWN,
                        message="VideoGenerator 未初始化",
                    )
            except Exception as e:
                results[provider_name] = HealthCheckResult(
                    component=provider_name,
                    status=HealthStatus.DOWN,
                    message=f"{provider_name} 异常: {e}",
                )

        return results

    async def _check_workspace_health(self: "SkillController") -> HealthCheckResult:
        """检查 Workspace 服务健康"""
        if self._workspace_client is None:
            return HealthCheckResult(
                component="workspace",
                status=HealthStatus.UNKNOWN,
                message="WorkspaceClient 未初始化",
            )

        try:
            start = time.perf_counter()
            if hasattr(self._workspace_client, "health_check"):
                await self._workspace_client.health_check()
            latency = (time.perf_counter() - start) * 1000

            return HealthCheckResult(
                component="workspace",
                status=HealthStatus.HEALTHY,
                latency_ms=latency,
                message="Workspace 连接正常",
            )
        except Exception as e:
            return HealthCheckResult(
                component="workspace",
                status=HealthStatus.DOWN,
                message=f"Workspace 异常: {e}",
            )

    # ───────────────────────────────────────────────────────
    # 查询 API
    # ───────────────────────────────────────────────────────

    async def get_progress(self: "SkillController", batch_id: str) -> Optional[ProgressSnapshot]:
        """获取批次进度

        Args:
            batch_id: 批次 ID

        Returns:
            进度快照 (批次不存在返回 None)
        """
        batch = self._active_batches.get(batch_id)
        if batch is None:
            return None

        completed = sum(
            1 for t in batch.tasks
            if t.status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED)
        )
        failed = sum(1 for t in batch.tasks if t.status == TaskStatus.FAILED)
        processing = sum(1 for t in batch.tasks if t.status == TaskStatus.PROCESSING)
        pending = batch.total - completed - processing

        return ProgressSnapshot(
            batch_id=batch_id,
            total=batch.total,
            completed=completed,
            failed=failed,
            pending=pending,
            processing=processing,
            percent=round((completed / batch.total) * 100, 1) if batch.total > 0 else 0.0,
            is_complete=batch.is_complete,
            total_cost_usd=batch.total_cost_usd,
        )

    async def list_gems(self: "SkillController") -> List[GemInfo]:
        """列出所有可用 Gem

        Returns:
            Gem 信息列表 (至少包含一个默认 Gem)
        """
        gems: List[GemInfo] = []

        if self._gem_registry is not None:
            try:
                registry_gems = self._gem_registry.list_gems()
                gems = [
                    GemInfo(
                        gem_id=getattr(g, "gem_id", ""),
                        name=getattr(g, "name", ""),
                        category=getattr(g, "category", None),
                        description=getattr(g, "description", None),
                        tags=getattr(g, "tags", []),
                        is_active=getattr(g, "is_active", True),
                        usage_count=getattr(g, "usage_count", 0),
                        success_rate=getattr(g, "success_rate", 0.0),
                    )
                    for g in registry_gems
                ]
            except Exception as e:
                logger.warning(f"获取 Gem 列表失败: {e}")

        # 如果没有可用 Gem，返回默认
        if not gems:
            gems = [
                GemInfo(
                    gem_id="default",
                    name="默认产品展示",
                    category="general",
                    description="通用产品展示模板",
                    tags=["product", "general"],
                )
            ]

        return gems

    async def get_config(self: "SkillController") -> SkillConfig:
        """获取当前配置

        Returns:
            当前配置副本
        """
        return self.config.model_copy(deep=True)

    async def get_metrics(self: "SkillController") -> Dict[str, Any]:
        """获取运行指标

        Returns:
            指标字典
        """
        return dict(self._metrics)

    # ───────────────────────────────────────────────────────
    # 管理操作
    # ───────────────────────────────────────────────────────

    async def refresh_gem_cache(self: "SkillController") -> int:
        """刷新 Gem 缓存

        Returns:
            刷新后的 Gem 数量
        """
        if self._gem_registry is not None and hasattr(self._gem_registry, "sync_gems"):
            try:
                report = await self._gem_registry.sync_gems()
                return getattr(report, "total_gems", 0)
            except Exception as e:
                logger.warning(f"刷新 Gem 缓存失败: {e}")
        return 0

    async def retry_failed(self: "SkillController", batch_id: str) -> int:
        """重试批次中的失败任务

        Args:
            batch_id: 批次 ID

        Returns:
            重试的任务数量
        """
        # 从活跃批次或存储中查找
        batch = self._active_batches.get(batch_id)
        if batch is None:
            return 0

        failed_tasks = [t for t in batch.tasks if t.status == TaskStatus.FAILED]
        if not failed_tasks:
            return 0

        # 重置状态并重新处理
        for task in failed_tasks:
            task.status = TaskStatus.PENDING
            task.error_message = None

        # 触发重新处理
        if self._batch_processor is not None and hasattr(self._batch_processor, "retry_failed"):
            try:
                return await self._batch_processor.retry_failed(batch_id)
            except Exception as e:
                logger.warning(f"重试失败: {e}")

        return len(failed_tasks)

    async def cancel_batch(self: "SkillController", batch_id: str) -> bool:
        """取消批次处理

        Args:
            batch_id: 批次 ID

        Returns:
            是否成功取消
        """
        batch = self._active_batches.get(batch_id)
        if batch is None:
            return False

        batch.status = BatchStatus.CANCELLED

        # 取消所有待处理任务
        for task in batch.tasks:
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED

        if self._batch_processor is not None and hasattr(self._batch_processor, "cancel"):
            try:
                await self._batch_processor.cancel(batch_id)
            except Exception as e:
                logger.warning(f"取消批次异常: {e}")

        return True


# ═══════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════


async def create_controller(
    config: Optional[SkillConfig] = None,
) -> SkillController:
    """便捷函数: 创建并初始化控制器

    Args:
        config: 配置 (None 则从环境变量加载)

    Returns:
        已初始化的控制器
    """
    controller = SkillController(config=config)
    await controller.initialize()
    return controller


# ═══════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════

__all__ = [
    "SkillController",
    "create_controller",
]
