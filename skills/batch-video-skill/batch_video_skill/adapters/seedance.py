# -*- coding: utf-8 -*-
"""
adapters/seedance.py
即梦 Seedance 适配器

提供火山引擎 Ark API 的完整封装:
- 文生视频、图生视频、多模态参考生视频
- 异步任务提交与轮询
- 价格估算 (基于 token 数)
- 任务管理（查询、取消）

提供商: 火山引擎 (Volcengine) / 即梦 Seedance
API: https://ark.cn-beijing.volces.com/api/v3
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

# 从主框架导入
from video_generator import (
    PricingInfo,
    ProviderCapability,
    ProviderHealth,
    ProviderStatus,
    SubmissionError,
    TaskFailedError,
    TaskNotFoundError,
    TaskStatus,
    TaskTimeoutError,
    UnsupportedRequestError,
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
# Seedance 专用枚举与模型
# ═══════════════════════════════════════════════════════════


class SeedanceModelVersion(str, Enum):
    """Seedance 支持的模型版本"""
    SEEDANCE_2_0 = "seedance-2.0"
    SEEDANCE_2_0_FAST = "seedance-2.0-fast"
    SEEDANCE_2_0_MINI = "seedance-2.0-mini"
    SEEDANCE_1_5_PRO = "seedance-1.5-pro"
    SEEDANCE_1_0_PRO = "seedance-1.0-pro"
    SEEDANCE_1_0_FAST = "seedance-1.0-fast"


class SeedanceResolution(str, Enum):
    """支持的分辨率"""
    RES_480P = "480p"
    RES_720P = "720p"
    RES_1080P = "1080p"
    RES_4K = "4k"


class SeedanceRatio(str, Enum):
    """支持的宽高比"""
    R_16_9 = "16:9"
    R_4_3 = "4:3"
    R_1_1 = "1:1"
    R_3_4 = "3:4"
    R_9_16 = "9:16"
    R_21_9 = "21:9"
    ADAPTIVE = "adaptive"


class SeedanceTaskStatus(str, Enum):
    """任务状态枚举"""
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


class SeedanceServiceTier(str, Enum):
    """服务等级"""
    DEFAULT = "default"
    FLEX = "flex"


class SeedanceTaskRequest(BaseModel):
    """创建视频生成任务请求体"""
    model: str = Field(..., description="模型ID")
    content: List[Dict[str, Any]] = Field(..., description="输入内容数组")
    callback_url: Optional[str] = Field(None)
    return_last_frame: bool = Field(False)
    service_tier: SeedanceServiceTier = Field(SeedanceServiceTier.DEFAULT)
    execution_expires_after: Optional[int] = Field(None, ge=3600, le=259200)
    generate_audio: bool = Field(True)
    draft: bool = Field(False)
    resolution: SeedanceResolution = Field(SeedanceResolution.RES_720P)
    ratio: SeedanceRatio = Field(SeedanceRatio.ADAPTIVE)
    duration: int = Field(5, ge=-1, le=15)
    frames: Optional[int] = Field(None)
    seed: Optional[int] = Field(None)
    camera_fixed: bool = Field(False)
    watermark: bool = Field(False)
    priority: int = Field(0, ge=0, le=9)

    @field_validator("duration")
    @classmethod
    def validate_duration(cls: type["SeedanceTaskRequest"], v: int) -> int:
        if v == -1:
            return v
        if v < 2 or v > 15:
            raise ValueError("duration 必须在 2-15 之间，或为 -1（自动）")
        return v


class SeedanceGeneratedContent(BaseModel):
    """生成的内容"""
    video_url: Optional[str] = Field(None)
    last_frame_url: Optional[str] = Field(None)


class SeedanceTaskResponse(BaseModel):
    """任务响应"""
    id: str = Field(..., alias="task_id")
    model: str = Field(...)
    status: SeedanceTaskStatus = Field(...)
    created_at: int = Field(...)
    updated_at: int = Field(...)
    service_tier: SeedanceServiceTier = Field(SeedanceServiceTier.DEFAULT)
    content: Optional[SeedanceGeneratedContent] = Field(None)
    error: Optional[Dict[str, Any]] = Field(None)
    duration: Optional[int] = Field(None)
    ratio: Optional[str] = Field(None)
    resolution: Optional[str] = Field(None)

    @property
    def is_finished(self: "SeedanceTaskResponse") -> bool:
        return self.status in (SeedanceTaskStatus.SUCCEEDED, SeedanceTaskStatus.FAILED, SeedanceTaskStatus.EXPIRED)

    @property
    def is_success(self: "SeedanceTaskResponse") -> bool:
        return self.status == SeedanceTaskStatus.SUCCEEDED


class SeedancePriceEstimate(BaseModel):
    """价格估算结果"""
    model: str = Field(...)
    duration: int = Field(...)
    resolution: SeedanceResolution = Field(...)
    has_video_input: bool = Field(False)
    service_tier: SeedanceServiceTier = Field(SeedanceServiceTier.DEFAULT)
    estimated_tokens: int = Field(...)
    unit_price_per_million: float = Field(...)
    estimated_cost_yuan: float = Field(...)
    currency: str = Field("CNY")


# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
REQUEST_TIMEOUT = 60.0
MAX_POLL_RETRIES = 120
MIN_POLL_INTERVAL = 5.0
MAX_POLL_INTERVAL = 30.0
POLL_BACKOFF_FACTOR = 1.5

MODEL_ID_MAP: Dict[str, str] = {
    SeedanceModelVersion.SEEDANCE_2_0.value: "doubao-seedance-2-0-260128",
    SeedanceModelVersion.SEEDANCE_2_0_FAST.value: "doubao-seedance-2-0-fast-260128",
    SeedanceModelVersion.SEEDANCE_2_0_MINI.value: "doubao-seedance-2-0-mini-260615",
    SeedanceModelVersion.SEEDANCE_1_5_PRO.value: "doubao-seedance-1-5-pro-251215",
    SeedanceModelVersion.SEEDANCE_1_0_PRO.value: "doubao-seedance-1-0-pro-250115",
    SeedanceModelVersion.SEEDANCE_1_0_FAST.value: "doubao-seedance-1-0-pro-fast-250115",
}

PRICE_TABLE: Dict[str, Dict[str, float]] = {
    "doubao-seedance-2-0-260128": {"generation": 46.0, "editing": 28.0},
    "doubao-seedance-2-0-fast-260128": {"generation": 32.0, "editing": 20.0},
    "doubao-seedance-2-0-mini-260615": {"image_to_video": 23000.0, "video_to_video": 14000.0},
}

TOKENS_PER_SECOND = 20592


# ═══════════════════════════════════════════════════════════
# SeedanceAdapter
# ═══════════════════════════════════════════════════════════


class SeedanceAdapter(VideoGenerator):
    """
    即梦 Seedance 视频生成适配器

    提供对火山引擎 Ark API 的完整封装。
    """

    PROVIDER = VideoProvider.SEEDANCE
    SUPPORTED_MODELS = [
        VideoModelVersion.SEEDANCE_2_0_STANDARD,
        VideoModelVersion.SEEDANCE_2_0_FAST,
        VideoModelVersion.SEEDANCE_2_0_MINI,
        VideoModelVersion.SEEDANCE_1_5_PRO,
    ]
    DEFAULT_MODEL = VideoModelVersion.SEEDANCE_2_0_STANDARD
    ADAPTER_NAME = "即梦 Seedance"
    ADAPTER_VERSION = "2.0.0"

    DEFAULT_TIMEOUT = 120.0
    DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

    def __init__(self: "SeedanceAdapter", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._poll_semaphore = asyncio.Semaphore(5)

    # ── 抽象方法实现 ──────────────────────────────────────

    async def submit(
        self, request: VideoGenerationRequest, model_version: Optional[VideoModelVersion] = None
    ) -> VideoTask:
        """提交视频生成任务"""
        model = self._resolve_model(model_version)
        payload = self._build_payload(request, model)

        url = f"{self.base_url}/contents/generations/tasks"
        start = time.time()

        try:
            raw = await self._execute_request("POST", url, json_data=payload)
            task = self._parse_submit_response(raw)
            latency_ms = (time.time() - start) * 1000
            self._metrics.record_submission(latency_ms, success=True)
            logger.info(f"任务已提交: {task.task_id} (model={model.value})")
            return task
        except Exception as e:
            latency_ms = (time.time() - start) * 1000
            self._metrics.record_submission(latency_ms, success=False)
            self._metrics.record_error("submit")
            raise SubmissionError(f"提交失败: {str(e)}", provider=self.PROVIDER.value) from e

    async def poll(self: "SeedanceAdapter", task_id: str) -> VideoTask:
        """查询任务状态"""
        url = f"{self.base_url}/contents/generations/tasks/{task_id}"

        try:
            raw = await self._execute_request("GET", url)
            task = self._parse_poll_response(raw)
            self._metrics.record_poll(latency_ms=0)
            return task
        except TaskNotFoundError:
            raise
        except Exception as e:
            self._metrics.record_error("poll")
            raise PollError(f"轮询失败: {str(e)}") from e

    async def cancel(self: "SeedanceAdapter", task_id: str) -> bool:
        """取消进行中的任务"""
        url = f"{self.base_url}/contents/generations/tasks/{task_id}/cancel"
        try:
            await self._execute_request("POST", url)
            logger.info(f"任务已取消: {task_id}")
            return True
        except Exception as e:
            logger.warning(f"取消任务失败: {task_id}: {e}")
            return False

    async def get_pricing(self: "SeedanceAdapter") -> List[PricingInfo]:
        """获取价格信息"""
        return [
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.SEEDANCE_2_0_STANDARD,
                cost_per_second_usd=0.18,
                currency="CNY",
            ),
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.SEEDANCE_2_0_FAST,
                cost_per_second_usd=0.15,
                currency="CNY",
            ),
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.SEEDANCE_2_0_MINI,
                cost_per_second_usd=0.09,
                currency="CNY",
            ),
        ]

    def get_capability(self: "SeedanceAdapter") -> ProviderCapability:
        """声明能力"""
        return ProviderCapability(
            provider=self.PROVIDER,
            supported_models=self.SUPPORTED_MODELS,
            max_duration=15,
            max_reference_images=9,
            supports_4k=True,
            supports_60fps=False,
            supports_reference_videos=True,
            supports_reference_audios=True,
            supports_last_frame=True,
            supports_audio_generation=True,
            supports_negative_prompt=True,
            supports_enhance_prompt=True,
        )

    def _default_base_url(self: "SeedanceAdapter") -> str:
        return DEFAULT_BASE_URL

    def _build_payload(
        self, request: VideoGenerationRequest, model: VideoModelVersion
    ) -> Dict[str, Any]:
        """构建 Seedance API 请求体"""
        model_id = MODEL_ID_MAP.get(model.value, model.value)

        # 构建 content 数组
        content: List[Dict[str, Any]] = [{"type": "text", "text": request.prompt}]

        if request.first_frame:
            content.append({
                "type": "image_url",
                "image_url": {"url": request.first_frame},
                "role": "first_frame",
            })
        if request.last_frame:
            content.append({
                "type": "image_url",
                "image_url": {"url": request.last_frame},
                "role": "last_frame",
            })
        for img_url in request.reference_images:
            content.append({
                "type": "image_url",
                "image_url": {"url": img_url},
                "role": "reference_image",
            })
        for vid_url in request.reference_videos:
            content.append({
                "type": "video_url",
                "video_url": {"url": vid_url},
                "role": "reference_video",
            })
        for aud_url in request.reference_audios:
            content.append({
                "type": "audio_url",
                "audio_url": {"url": aud_url},
                "role": "reference_audio",
            })

        # 分辨率映射
        resolution_map = {
            VideoResolution.P480: SeedanceResolution.RES_480P,
            VideoResolution.P720: SeedanceResolution.RES_720P,
            VideoResolution.P1080: SeedanceResolution.RES_1080P,
            VideoResolution.P4K: SeedanceResolution.RES_4K,
        }
        seedance_resolution = resolution_map.get(request.resolution, SeedanceResolution.RES_720P)

        # 宽高比映射
        ratio_map = {
            VideoAspectRatio.RATIO_16_9: SeedanceRatio.R_16_9,
            VideoAspectRatio.RATIO_9_16: SeedanceRatio.R_9_16,
            VideoAspectRatio.RATIO_1_1: SeedanceRatio.R_1_1,
            VideoAspectRatio.RATIO_4_3: SeedanceRatio.R_4_3,
            VideoAspectRatio.RATIO_3_4: SeedanceRatio.R_3_4,
            VideoAspectRatio.RATIO_21_9: SeedanceRatio.R_21_9,
        }
        seedance_ratio = ratio_map.get(request.aspect_ratio, SeedanceRatio.ADAPTIVE)

        payload = {
            "model": model_id,
            "content": content,
            "generate_audio": request.generate_audio,
            "resolution": seedance_resolution.value,
            "ratio": seedance_ratio.value,
            "duration": request.duration,
            "watermark": request.extra_params.get("watermark", False),
            "camera_fixed": request.extra_params.get("camera_fixed", False),
            "priority": request.extra_params.get("priority", 0),
        }

        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if request.extra_params.get("return_last_frame"):
            payload["return_last_frame"] = True
        if request.extra_params.get("callback_url"):
            payload["callback_url"] = request.extra_params["callback_url"]

        return payload

    def _parse_submit_response(self: "SeedanceAdapter", raw: Dict[str, Any]) -> VideoTask:
        """解析提交响应"""
        task_id = raw.get("id", raw.get("task_id", ""))
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

    def _parse_poll_response(self: "SeedanceAdapter", raw: Dict[str, Any]) -> VideoTask:
        """解析轮询响应"""
        task_id = raw.get("id", raw.get("task_id", ""))
        raw_status = raw.get("status", "pending")
        status = self._map_status(raw_status)

        content = raw.get("content") or {}
        video_url = None
        if isinstance(content, dict):
            video_url = content.get("video_url")

        error = raw.get("error") or {}
        error_code = None
        error_message = None
        if isinstance(error, dict):
            error_code = error.get("code")
            error_message = error.get("message")

        return VideoTask(
            task_id=task_id,
            provider=self.PROVIDER,
            model_version=self.DEFAULT_MODEL or self.SUPPORTED_MODELS[0],
            status=status,
            video_url=video_url,
            error_code=error_code,
            error_message=error_message,
        )

    # ── 额外方法 ──────────────────────────────────────────

    def estimate_price(
        self,
        model: str = SeedanceModelVersion.SEEDANCE_2_0.value,
        duration: int = 5,
        resolution: str = SeedanceResolution.RES_720P.value,
        has_video_input: bool = False,
        service_tier: str = SeedanceServiceTier.DEFAULT.value,
    ) -> SeedancePriceEstimate:
        """估算视频生成费用"""
        model_id = MODEL_ID_MAP.get(model, model)
        if model_id not in PRICE_TABLE:
            raise ValueError(f"暂不支持模型 {model} 的价格估算")

        price_config = PRICE_TABLE[model_id]
        if "mini" in model_id:
            mode_key = "video_to_video" if has_video_input else "image_to_video"
        else:
            mode_key = "editing" if has_video_input else "generation"
        unit_price = price_config.get(mode_key, price_config["generation"])

        estimated_tokens = duration * TOKENS_PER_SECOND
        cost = estimated_tokens / 1_000_000 * unit_price

        tier = SeedanceServiceTier(service_tier)
        if tier == SeedanceServiceTier.FLEX:
            cost *= 0.5

        return SeedancePriceEstimate(
            model=model,
            duration=duration,
            resolution=SeedanceResolution(resolution),
            has_video_input=has_video_input,
            service_tier=tier,
            estimated_tokens=estimated_tokens,
            unit_price_per_million=unit_price,
            estimated_cost_yuan=round(cost, 4),
            currency="CNY",
        )

    def _build_auth_headers(self: "SeedanceAdapter", api_key: str) -> Dict[str, str]:
        """Seedance 使用 Bearer Token 认证"""
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }


__all__ = ["SeedanceAdapter", "SeedanceModelVersion", "SeedancePriceEstimate"]
