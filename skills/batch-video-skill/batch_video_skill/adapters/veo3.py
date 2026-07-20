# -*- coding: utf-8 -*-
"""
adapters/veo3.py
Google Veo 3 适配器

提供 Google Veo 3 视频生成模型的完整封装:
- 文生视频、图生视频、参考生视频、首尾帧生视频
- 视频延长
- 双认证模式: Gemini Developer API / Vertex AI
- 原生音频生成

提供商: Google DeepMind - Veo 3.1
API: Google AI Studio / Vertex AI
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from video_generator import (
    PricingInfo,
    ProviderCapability,
    ProviderStatus,
    SubmissionError,
    TaskFailedError,
    TaskStatus,
    TaskTimeoutError,
    VideoAspectRatio,
    VideoGenerationRequest,
    VideoModelVersion,
    VideoProvider,
    VideoResolution,
    VideoTask,
    VideoGenerator,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Veo 3 专用枚举与模型
# ═══════════════════════════════════════════════════════════


class VeoModelTier(str, Enum):
    """Veo 3.1 模型档次枚举"""
    STANDARD = "standard"
    FAST = "fast"
    LITE = "lite"


class VeoModelId(str, Enum):
    """Veo 模型 ID 枚举"""
    VEO_3_1_STANDARD = "veo-3.1-generate-preview"
    VEO_3_1_FAST = "veo-3.1-fast-generate-preview"
    VEO_3_1_LITE = "veo-3.1-lite-generate-preview"


class VeoResolution(str, Enum):
    """视频分辨率枚举"""
    P720 = "720p"
    P1080 = "1080p"
    P4K = "4k"


class VeoAspectRatio(str, Enum):
    """视频宽高比枚举"""
    R16_9 = "16:9"
    R9_16 = "9:16"


class VeoDuration(int, Enum):
    """视频时长枚举(秒)"""
    SHORT = 4
    MEDIUM = 6
    LONG = 8


class VeoAuthMode(str, Enum):
    """认证模式枚举"""
    DEVELOPER_API = "developer_api"
    VERTEX_AI = "vertex_ai"


class VeoPersonGeneration(str, Enum):
    """人物生成策略枚举"""
    DONT_ALLOW = "DONT_ALLOW"
    ALLOW_ADULT = "ALLOW_ADULT"


class VeoReferenceType(str, Enum):
    """参考图片类型枚举"""
    ASSET = "asset"
    STYLE = "style"


class VeoTaskStatus(str, Enum):
    """Veo3Adapter 内部任务状态"""
    SUBMITTED = "submitted"
    QUEUED = "queued"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VeoGeneratedVideo(BaseModel):
    """生成的视频信息"""
    video_uri: str = Field(...)
    mime_type: str = Field(default="video/mp4")
    local_path: Optional[str] = Field(default=None)
    rai_media_filtered: bool = Field(default=False)


class VeoTaskResult(BaseModel):
    """任务执行结果"""
    task_id: str = Field(...)
    status: VeoTaskStatus = Field(...)
    videos: List[VeoGeneratedVideo] = Field(default_factory=list)
    error_message: Optional[str] = Field(default=None)
    created_at: Optional[float] = Field(default=None)
    completed_at: Optional[float] = Field(default=None)

    @property
    def is_success(self: "VeoTaskResult") -> bool:
        return self.status == VeoTaskStatus.COMPLETED and len(self.videos) > 0

    @property
    def primary_video(self: "VeoTaskResult") -> Optional[VeoGeneratedVideo]:
        return self.videos[0] if self.videos else None


class VeoTaskStatusResponse(BaseModel):
    """任务状态查询响应"""
    task_id: str = Field(...)
    done: bool = Field(...)
    state: str = Field(...)
    progress: Optional[float] = Field(default=None)
    error: Optional[str] = Field(default=None)


# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

# 定价表 (美元/秒，有音频)
PRICING = {
    (VeoModelTier.STANDARD, VeoResolution.P720): 0.40,
    (VeoModelTier.STANDARD, VeoResolution.P1080): 0.40,
    (VeoModelTier.STANDARD, VeoResolution.P4K): 0.60,
    (VeoModelTier.FAST, VeoResolution.P720): 0.15,
    (VeoModelTier.FAST, VeoResolution.P1080): 0.15,
    (VeoModelTier.FAST, VeoResolution.P4K): 0.30,
    (VeoModelTier.LITE, VeoResolution.P720): 0.05,
    (VeoModelTier.LITE, VeoResolution.P1080): 0.08,
}

# 模型映射
VEO_MODEL_MAP: Dict[str, str] = {
    VideoModelVersion.VEO_3_1_STANDARD.value: VeoModelId.VEO_3_1_STANDARD.value,
    VideoModelVersion.VEO_3_1_FAST.value: VeoModelId.VEO_3_1_FAST.value,
    VideoModelVersion.VEO_3_1_LITE.value: VeoModelId.VEO_3_1_LITE.value,
}

# 模型到 tier 的映射
VEO_MODEL_TIER_MAP: Dict[str, VeoModelTier] = {
    VideoModelVersion.VEO_3_1_STANDARD.value: VeoModelTier.STANDARD,
    VideoModelVersion.VEO_3_1_FAST.value: VeoModelTier.FAST,
    VideoModelVersion.VEO_3_1_LITE.value: VeoModelTier.LITE,
}


# ═══════════════════════════════════════════════════════════
# Veo3Adapter
# ═══════════════════════════════════════════════════════════


class Veo3Adapter(VideoGenerator):
    """
    Google Veo 3 视频生成适配器

    提供 Google Veo 3 视频生成模型的完整 API 封装。
    支持双认证模式: Gemini Developer API / Vertex AI
    """

    PROVIDER = VideoProvider.VEO3
    SUPPORTED_MODELS = [
        VideoModelVersion.VEO_3_1_STANDARD,
        VideoModelVersion.VEO_3_1_FAST,
        VideoModelVersion.VEO_3_1_LITE,
    ]
    DEFAULT_MODEL = VideoModelVersion.VEO_3_1_FAST
    ADAPTER_NAME = "Google Veo 3"
    ADAPTER_VERSION = "3.1.0"

    DEFAULT_TIMEOUT = 300.0  # Veo 生成较慢

    def __init__(
        self,
        api_keys: List[str],
        auth_mode: VeoAuthMode = VeoAuthMode.DEVELOPER_API,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            api_keys=api_keys,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            **kwargs,
        )
        self.auth_mode = auth_mode
        self._client: Optional[Any] = None

    def _get_client(self: "Veo3Adapter") -> Any:
        """获取或创建 genai 客户端（延迟初始化）"""
        if self._client is None:
            try:
                from google import genai
                if self.auth_mode == VeoAuthMode.VERTEX_AI:
                    project = self.config.get("vertex_project") or os.environ.get("GOOGLE_CLOUD_PROJECT")
                    location = self.config.get("vertex_location") or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
                    self._client = genai.Client(vertexai=True, project=project, location=location)
                else:
                    api_key = self.api_keys[0] if self.api_keys else None
                    self._client = genai.Client(api_key=api_key)
                logger.info(f"genai client created (auth_mode={self.auth_mode.value})")
            except ImportError:
                logger.error("google-genai SDK 未安装，使用模拟模式")
                self._client = None
        return self._client

    # ── 抽象方法实现 ──────────────────────────────────────

    async def submit(
        self, request: VideoGenerationRequest, model_version: Optional[VideoModelVersion] = None
    ) -> VideoTask:
        """提交视频生成任务"""
        model = self._resolve_model(model_version)
        model_id = VEO_MODEL_MAP.get(model.value, VeoModelId.VEO_3_1_FAST.value)

        start = time.time()

        try:
            client = self._get_client()
            if client is None:
                # 模拟模式: 返回一个模拟任务
                task_id = f"veo-mock-{int(time.time() * 1000)}"
                logger.warning(f"模拟模式: 返回模拟任务 {task_id}")
                return VideoTask(
                    task_id=task_id,
                    provider=self.PROVIDER,
                    model_version=model,
                    status=TaskStatus.PENDING,
                    prompt=request.prompt,
                    created_at=time.time(),
                )

            # 使用 SDK 提交任务
            from google.genai import types

            config = self._build_sdk_config(request)

            # 加载图片 (如果有)
            image = None
            if request.first_frame:
                image = await self._load_image(request.first_frame)

            operation = await asyncio.to_thread(
                lambda: client.models.generate_videos(
                    model=model_id,
                    prompt=request.prompt,
                    image=image,
                    config=config,
                )
            )

            task_id = operation.name if hasattr(operation, "name") else str(operation)

            latency_ms = (time.time() - start) * 1000
            self._metrics.record_submission(latency_ms, success=True)
            logger.info(f"任务已提交: {task_id} (model={model.value})")

            return VideoTask(
                task_id=task_id,
                provider=self.PROVIDER,
                model_version=model,
                status=TaskStatus.PENDING,
                prompt=request.prompt,
                created_at=time.time(),
            )

        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            self._metrics.record_submission(latency_ms, success=False)
            raise SubmissionError(f"提交失败: {str(e)}", provider=self.PROVIDER.value) from e

    async def poll(self: "Veo3Adapter", task_id: str) -> VideoTask:
        """查询任务状态"""
        try:
            client = self._get_client()
            if client is None:
                # 模拟模式: 模拟完成
                return VideoTask(
                    task_id=task_id,
                    provider=self.PROVIDER,
                    model_version=self.DEFAULT_MODEL,
                    status=TaskStatus.SUCCEEDED,
                    video_url=f"https://mock.veo.example/videos/{task_id}.mp4",
                )

            # 使用 SDK 查询
            operation = await asyncio.to_thread(
                lambda: client.operations.get(name=task_id)
            )

            done = operation.done if hasattr(operation, "done") else False
            if done:
                # 任务完成
                result = operation.response if hasattr(operation, "response") else None
                video_url = None
                if result and hasattr(result, "generated_videos"):
                    videos = result.generated_videos
                    if videos:
                        video = videos[0]
                        video_url = video.video.uri if hasattr(video, "video") else None

                error_message = None
                if hasattr(operation, "error") and operation.error:
                    error_message = str(operation.error)
                    status = TaskStatus.FAILED
                else:
                    status = TaskStatus.SUCCEEDED if video_url else TaskStatus.FAILED

                return VideoTask(
                    task_id=task_id,
                    provider=self.PROVIDER,
                    model_version=self.DEFAULT_MODEL,
                    status=status,
                    video_url=video_url,
                    error_message=error_message,
                )
            else:
                # 任务仍在进行中
                state = "running"
                progress = None
                if hasattr(operation, "metadata") and operation.metadata:
                    progress = getattr(operation.metadata, "progress_percentage", None)

                status = TaskStatus.PROCESSING if state != "pending" else TaskStatus.PENDING
                return VideoTask(
                    task_id=task_id,
                    provider=self.PROVIDER,
                    model_version=self.DEFAULT_MODEL,
                    status=status,
                    progress_pct=progress or 0.0,
                )

        except Exception as e:
            self._metrics.record_error("poll")
            raise

    async def cancel(self: "Veo3Adapter", task_id: str) -> bool:
        """取消进行中的任务"""
        logger.warning(f"Veo 3 不支持取消任务: {task_id}")
        return False

    async def get_pricing(self: "Veo3Adapter") -> List[PricingInfo]:
        """获取价格信息"""
        return [
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.VEO_3_1_STANDARD,
                cost_per_second_usd=0.25,
            ),
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.VEO_3_1_FAST,
                cost_per_second_usd=0.15,
            ),
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.VEO_3_1_LITE,
                cost_per_second_usd=0.05,
            ),
        ]

    def get_capability(self: "Veo3Adapter") -> ProviderCapability:
        """声明能力"""
        return ProviderCapability(
            provider=self.PROVIDER,
            supported_models=self.SUPPORTED_MODELS,
            max_duration=8,
            max_reference_images=3,
            supports_4k=True,
            supports_60fps=False,
            supports_reference_videos=False,
            supports_reference_audios=False,
            supports_last_frame=True,
            supports_audio_generation=True,
            supports_negative_prompt=True,
            supports_enhance_prompt=True,
        )

    def _default_base_url(self: "Veo3Adapter") -> str:
        return "https://generativelanguage.googleapis.com"

    def _build_payload(
        self, request: VideoGenerationRequest, model: VideoModelVersion
    ) -> Dict[str, Any]:
        """构建 REST API 请求体 (备用)"""
        model_id = VEO_MODEL_MAP.get(model.value, VeoModelId.VEO_3_1_FAST.value)

        # 分辨率映射
        resolution_map = {
            VideoResolution.P720: VeoResolution.P720,
            VideoResolution.P1080: VeoResolution.P1080,
            VideoResolution.P4K: VeoResolution.P4K,
        }
        veo_resolution = resolution_map.get(request.resolution, VeoResolution.P720)

        # 宽高比映射
        ratio_map = {
            VideoAspectRatio.RATIO_16_9: VeoAspectRatio.R16_9,
            VideoAspectRatio.RATIO_9_16: VeoAspectRatio.R9_16,
        }
        veo_ratio = ratio_map.get(request.aspect_ratio, VeoAspectRatio.R16_9)

        # 时长映射
        if request.duration <= 4:
            veo_duration = VeoDuration.SHORT
        elif request.duration <= 6:
            veo_duration = VeoDuration.MEDIUM
        else:
            veo_duration = VeoDuration.LONG

        payload: Dict[str, Any] = {
            "model": model_id,
            "prompt": request.prompt,
            "duration_seconds": veo_duration.value,
            "aspect_ratio": veo_ratio.value,
            "resolution": veo_resolution.value,
            "number_of_videos": 1,
            "generate_audio": request.generate_audio,
        }

        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if request.enhance_prompt:
            payload["enhance_prompt"] = True

        return payload

    def _parse_submit_response(self: "Veo3Adapter", raw: Dict[str, Any]) -> VideoTask:
        """解析提交响应"""
        task_id = raw.get("name", raw.get("task_id", ""))
        if not task_id:
            raise SubmissionError("响应中未找到 task_id", provider=self.PROVIDER.value)

        return VideoTask(
            task_id=task_id,
            provider=self.PROVIDER,
            model_version=self.DEFAULT_MODEL or self.SUPPORTED_MODELS[0],
            status=TaskStatus.PENDING,
            prompt="",
            created_at=time.time(),
        )

    def _parse_poll_response(self: "Veo3Adapter", raw: Dict[str, Any]) -> VideoTask:
        """解析轮询响应"""
        task_id = raw.get("name", "")
        done = raw.get("done", False)
        state = raw.get("state", "pending")

        if done:
            response = raw.get("response", {})
            videos = response.get("generatedVideos", [])
            video_url = None
            if videos:
                video_url = videos[0].get("video", {}).get("uri")

            error = raw.get("error")
            status = TaskStatus.FAILED if error else (TaskStatus.SUCCEEDED if video_url else TaskStatus.FAILED)

            return VideoTask(
                task_id=task_id,
                provider=self.PROVIDER,
                model_version=self.DEFAULT_MODEL or self.SUPPORTED_MODELS[0],
                status=status,
                video_url=video_url,
                error_message=str(error) if error else None,
            )
        else:
            status = TaskStatus.PROCESSING if state != "pending" else TaskStatus.PENDING
            return VideoTask(
                task_id=task_id,
                provider=self.PROVIDER,
                model_version=self.DEFAULT_MODEL or self.SUPPORTED_MODELS[0],
                status=status,
            )

    # ── SDK 相关方法 ──────────────────────────────────────

    def _build_sdk_config(self: "Veo3Adapter", request: VideoGenerationRequest) -> Any:
        """构建 google-genai SDK 配置"""
        try:
            from google.genai import types

            # 分辨率映射
            resolution_map = {
                VideoResolution.P720: "720p",
                VideoResolution.P1080: "1080p",
                VideoResolution.P4K: "4k",
            }
            resolution = resolution_map.get(request.resolution, "720p")

            # 宽高比映射
            ratio_map = {
                VideoAspectRatio.RATIO_16_9: "16:9",
                VideoAspectRatio.RATIO_9_16: "9:16",
            }
            aspect_ratio = ratio_map.get(request.aspect_ratio, "16:9")

            # 时长
            if request.duration <= 4:
                duration = 4
            elif request.duration <= 6:
                duration = 6
            else:
                duration = 8

            config = types.GenerateVideosConfig(
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                duration_seconds=duration,
                number_of_videos=1,
            )

            if request.generate_audio:
                # Veo 3.x 原生支持音频
                pass

            if request.person_generation:
                config.person_generation = request.person_generation

            return config
        except ImportError:
            return None

    async def _load_image(self: "Veo3Adapter", image_path: str) -> Any:
        """加载图片"""
        try:
            from google.genai import types
            import pathlib

            path = pathlib.Path(image_path)
            if path.exists():
                return types.Image.from_file(path=image_path)
            else:
                # 假设是 URL，下载
                return types.Image.from_url(url=image_path)
        except ImportError:
            logger.warning("google-genai SDK 未安装，无法加载图片")
            return None

    # ── 额外方法 ──────────────────────────────────────────

    def estimate_cost(
        self,
        model_tier: VeoModelTier,
        resolution: VeoResolution,
        duration: int = 5,
    ) -> float:
        """估算视频生成费用 (美元)"""
        price_per_sec = PRICING.get((model_tier, resolution), 0.15)
        return price_per_sec * duration

    def _build_auth_headers(self: "Veo3Adapter", api_key: str) -> Dict[str, str]:
        """Veo 3 使用 API Key 作为查询参数 (REST API)"""
        return {
            "Content-Type": "application/json",
        }


__all__ = ["Veo3Adapter", "VeoModelTier", "VeoAuthMode"]
