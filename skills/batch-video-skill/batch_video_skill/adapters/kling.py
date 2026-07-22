# -*- coding: utf-8 -*-
"""
adapters/kling.py
可灵 Kling 适配器

提供快手 Kling AI API 的完整封装:
- 文生视频、图生视频、多镜头分镜
- 数字人生成
- 异步任务提交与轮询
- 价格估算

提供商: 快手 (Kuaishou) - Kling AI
API: https://api-beijing.klingai.com
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

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
# Kling 专用枚举与模型
# ═══════════════════════════════════════════════════════════


class KlingModelVersion(str, Enum):
    """可灵支持的模型版本"""
    KLING_V3 = "kling-v3"
    KLING_V3_TURBO = "kling-v3-turbo"
    KLING_V3_OMNI = "kling-v3-omni"
    KLING_V2_6 = "kling-v2-6"
    KLING_V2_5_TURBO = "kling-v2-5-turbo"
    KLING_V1_6 = "kling-v1-6"


class KlingResolution(str, Enum):
    """支持的分辨率"""
    RES_720P = "720p"
    RES_1080P = "1080p"
    RES_4K = "4k"


class KlingAspectRatio(str, Enum):
    """支持的宽高比"""
    R_16_9 = "16:9"
    R_9_16 = "9:16"
    R_1_1 = "1:1"
    R_3_4 = "3:4"
    R_4_3 = "4:3"


class KlingTaskStatus(str, Enum):
    """任务状态枚举"""
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    SUCCEED = "succeed"
    FAILED = "failed"


class KlingMode(str, Enum):
    """生成模式"""
    STD = "std"
    PRO = "pro"


class KlingSound(str, Enum):
    """音频开关"""
    ON = "on"
    OFF = "off"


class KlingTextToVideoRequest(BaseModel):
    """文生视频请求体"""
    model_name: str = Field("kling-v3")
    prompt: str = Field(..., max_length=2000)
    negative_prompt: Optional[str] = Field(None)
    duration: Optional[int] = Field(None)
    mode: KlingMode = Field(KlingMode.STD)
    aspect_ratio: KlingAspectRatio = Field(KlingAspectRatio.R_16_9)
    sound: KlingSound = Field(KlingSound.OFF)
    multi_shot: bool = Field(False)
    callback_url: Optional[str] = Field(None)
    external_task_id: Optional[str] = Field(None)
    cfg_scale: Optional[float] = Field(None, ge=0, le=1)
    camera_control: Optional[Dict[str, Any]] = Field(None)


class KlingImageToVideoRequest(BaseModel):
    """图生视频请求体"""
    contents: List[Dict[str, Any]] = Field(...)
    settings: Dict[str, Any] = Field(default_factory=dict)
    options: Optional[Dict[str, Any]] = Field(None)


class KlingTaskResponse(BaseModel):
    """任务响应"""
    task_id: str = Field(..., alias="id")
    status: KlingTaskStatus = Field(...)
    create_time: Optional[int] = Field(None)
    update_time: Optional[int] = Field(None)
    external_id: Optional[str] = Field(None)
    result: Optional[Dict[str, Any]] = Field(None)

    @property
    def is_finished(self: "KlingTaskResponse") -> bool:
        return self.status in (KlingTaskStatus.SUCCEED, KlingTaskStatus.FAILED)

    @property
    def is_success(self: "KlingTaskResponse") -> bool:
        return self.status == KlingTaskStatus.SUCCEED

    @property
    def video_url(self: "KlingTaskResponse") -> Optional[str]:
        if self.result:
            works = self.result.get("works", [])
            if works:
                resource = works[0].get("resource", {})
                return resource.get("resource_without_watermark")
        return None


class KlingPriceEstimate(BaseModel):
    """价格估算结果"""
    model: str = Field(...)
    duration: int = Field(...)
    resolution: KlingResolution = Field(...)
    sound_enabled: bool = Field(False)
    unit_price_per_second: float = Field(...)
    estimated_cost_yuan: float = Field(...)
    currency: str = Field("CNY")


# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

DEFAULT_BASE_URL = "https://api-beijing.klingai.com"
REQUEST_TIMEOUT = 60.0
MAX_POLL_RETRIES = 120
MIN_POLL_INTERVAL = 5.0
MAX_POLL_INTERVAL = 30.0
POLL_BACKOFF_FACTOR = 1.5

PRICE_TABLE: Dict[str, Dict[str, Dict[str, float]]] = {
    "kling-v3": {
        "720p": {"sound_off": 0.60, "sound_on": 0.90},
        "1080p": {"sound_off": 0.80, "sound_on": 1.20},
        "4k": {"sound_off": 3.00, "sound_on": 3.00},
    },
    "kling-v3-turbo": {
        "720p": {"sound_on": 0.80},
        "1080p": {"sound_on": 1.00},
    },
    "kling-v3-omni": {
        "720p": {"sound_off": 0.60, "sound_on": 0.80},
        "1080p": {"sound_off": 0.80, "sound_on": 1.00},
        "4k": {"sound_off": 3.00, "sound_on": 3.00},
    },
}


# ═══════════════════════════════════════════════════════════
# KlingAdapter
# ═══════════════════════════════════════════════════════════


class KlingAdapter(VideoGenerator):
    """
    可灵 Kling 视频生成适配器

    提供对快手 Kling AI API 的完整封装。
    """

    PROVIDER = VideoProvider.KLING
    SUPPORTED_MODELS = [
        VideoModelVersion.KLING_3_0,
        VideoModelVersion.KLING_3_0_TURBO,
        VideoModelVersion.KLING_3_0_OMNI,
    ]
    DEFAULT_MODEL = VideoModelVersion.KLING_3_0
    ADAPTER_NAME = "可灵 Kling"
    ADAPTER_VERSION = "3.0.0"

    DEFAULT_TIMEOUT = 120.0
    DEFAULT_BASE_URL = "https://api-beijing.klingai.com"

    def __init__(self: "KlingAdapter", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._poll_semaphore = asyncio.Semaphore(5)

    # ── 抽象方法实现 ──────────────────────────────────────

    async def submit(
        self, request: VideoGenerationRequest, model_version: Optional[VideoModelVersion] = None
    ) -> VideoTask:
        """提交视频生成任务"""
        model = self._resolve_model(model_version)
        payload = self._build_payload(request, model)

        # 根据是否有首帧选择不同的 endpoint
        if request.first_frame or request.reference_images:
            url = f"{self.base_url}/v1/videos/image2video"
        else:
            url = f"{self.base_url}/v1/videos/text2video"

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
            raise SubmissionError(f"提交失败: {str(e)}", provider=self.PROVIDER.value) from e

    async def poll(self: "KlingAdapter", task_id: str) -> VideoTask:
        """查询任务状态"""
        url = f"{self.base_url}/v1/videos/{task_id}"
        try:
            raw = await self._execute_request("GET", url)
            task = self._parse_poll_response(raw)
            self._metrics.record_poll(latency_ms=0)
            return task
        except Exception as e:
            self._metrics.record_error("poll")
            raise

    async def cancel(self: "KlingAdapter", task_id: str) -> bool:
        """取消进行中的任务"""
        # Kling API 可能不支持取消，返回 False
        logger.warning(f"Kling 不支持取消任务: {task_id}")
        return False

    async def get_pricing(self: "KlingAdapter") -> List[PricingInfo]:
        """获取价格信息"""
        return [
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.KLING_3_0,
                cost_per_second_usd=0.20,
                currency="CNY",
            ),
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.KLING_3_0_TURBO,
                cost_per_second_usd=0.13,
                currency="CNY",
            ),
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.KLING_3_0_OMNI,
                cost_per_second_usd=0.22,
                currency="CNY",
            ),
        ]

    def get_capability(self: "KlingAdapter") -> ProviderCapability:
        """声明能力"""
        return ProviderCapability(
            provider=self.PROVIDER,
            supported_models=self.SUPPORTED_MODELS,
            max_duration=15,
            max_reference_images=5,
            supports_4k=True,
            supports_60fps=True,
            supports_reference_videos=True,
            supports_reference_audios=False,
            supports_last_frame=True,
            supports_audio_generation=True,
            supports_negative_prompt=True,
            supports_enhance_prompt=False,
        )

    def _default_base_url(self: "KlingAdapter") -> str:
        return DEFAULT_BASE_URL

    def _build_payload(
        self, request: VideoGenerationRequest, model: VideoModelVersion
    ) -> Dict[str, Any]:
        """构建 Kling API 请求体"""
        # 模型名称映射
        model_name_map = {
            VideoModelVersion.KLING_3_0.value: "kling-v3",
            VideoModelVersion.KLING_3_0_TURBO.value: "kling-v3-turbo",
            VideoModelVersion.KLING_3_0_OMNI.value: "kling-v3-omni",
        }
        model_name = model_name_map.get(model.value, "kling-v3")

        # 分辨率映射
        resolution_map = {
            VideoResolution.P720: KlingResolution.RES_720P,
            VideoResolution.P1080: KlingResolution.RES_1080P,
            VideoResolution.P4K: KlingResolution.RES_4K,
        }
        kling_resolution = resolution_map.get(request.resolution, KlingResolution.RES_720P)

        # 宽高比映射
        ratio_map = {
            VideoAspectRatio.RATIO_16_9: KlingAspectRatio.R_16_9,
            VideoAspectRatio.RATIO_9_16: KlingAspectRatio.R_9_16,
            VideoAspectRatio.RATIO_1_1: KlingAspectRatio.R_1_1,
            VideoAspectRatio.RATIO_4_3: KlingAspectRatio.R_4_3,
            VideoAspectRatio.RATIO_3_4: KlingAspectRatio.R_3_4,
        }
        kling_ratio = ratio_map.get(request.aspect_ratio, KlingAspectRatio.R_16_9)

        # 构建请求体
        if request.first_frame or request.reference_images:
            # 图生视频
            contents: List[Dict[str, Any]] = []
            if request.first_frame:
                contents.append({"type": "first_frame", "url": request.first_frame})
            for img_url in request.reference_images:
                contents.append({"type": "image", "url": img_url})
            if request.last_frame:
                contents.append({"type": "last_frame", "url": request.last_frame})

            settings = {
                "resolution": kling_resolution.value,
                "duration": request.duration,
                "aspect_ratio": kling_ratio.value,
            }
            options: Dict[str, Any] = {}
            if request.extra_params.get("callback_url"):
                options["callback_url"] = request.extra_params["callback_url"]

            payload: Dict[str, Any] = {
                "contents": contents,
                "settings": settings,
            }
            if options:
                payload["options"] = options

            if request.prompt:
                payload["prompt"] = request.prompt
        else:
            # 文生视频
            sound = KlingSound.ON if request.generate_audio else KlingSound.OFF

            payload = {
                "model_name": model_name,
                "prompt": request.prompt,
                "duration": request.duration,
                "mode": KlingMode.PRO.value,
                "aspect_ratio": kling_ratio.value,
                "sound": sound.value,
            }
            if request.negative_prompt:
                payload["negative_prompt"] = request.negative_prompt
            if request.extra_params.get("callback_url"):
                payload["callback_url"] = request.extra_params["callback_url"]
            if request.extra_params.get("multi_shot"):
                payload["multi_shot"] = True

        return payload

    def _parse_submit_response(self: "KlingAdapter", raw: Dict[str, Any]) -> VideoTask:
        """解析提交响应"""
        # Kling 返回 {code, message, data: {task_id, ...}}
        data = raw.get("data", raw)
        task_id = data.get("task_id", data.get("id", ""))
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

    def _parse_poll_response(self: "KlingAdapter", raw: Dict[str, Any]) -> VideoTask:
        """解析轮询响应"""
        data = raw.get("data", raw)
        task_id = data.get("task_id", data.get("id", ""))
        raw_status = data.get("status", data.get("task_status", "submitted"))
        status = self._map_status(raw_status)

        result = data.get("result", {})
        video_url = None
        if result:
            works = result.get("works", [])
            if works:
                resource = works[0].get("resource", {})
                video_url = resource.get("resource_without_watermark")

        return VideoTask(
            task_id=task_id,
            provider=self.PROVIDER,
            model_version=self.DEFAULT_MODEL or self.SUPPORTED_MODELS[0],
            status=status,
            video_url=video_url,
        )

    # ── 额外方法 ──────────────────────────────────────────

    def estimate_price(
        self,
        model: str = "kling-v3",
        duration: int = 5,
        resolution: str = "720p",
        sound: bool = False,
        has_reference_video: bool = False,
    ) -> KlingPriceEstimate:
        """估算视频生成费用"""
        if model not in PRICE_TABLE:
            raise ValueError(f"暂不支持模型 {model} 的价格估算")

        model_prices = PRICE_TABLE[model]
        if resolution not in model_prices:
            raise ValueError(f"模型 {model} 不支持分辨率 {resolution}")

        res_prices = model_prices[resolution]
        if has_reference_video and "video_ref_sound_off" in res_prices:
            price_key = f"video_ref_sound_{'on' if sound else 'off'}"
        else:
            price_key = f"sound_{'on' if sound else 'off'}"

        unit_price = res_prices.get(price_key, res_prices.get("sound_off", 0.60))
        cost = unit_price * duration

        return KlingPriceEstimate(
            model=model,
            duration=duration,
            resolution=KlingResolution(resolution),
            sound_enabled=sound,
            unit_price_per_second=unit_price,
            estimated_cost_yuan=round(cost, 4),
            currency="CNY",
        )

    def _build_auth_headers(self: "KlingAdapter", api_key: str) -> Dict[str, str]:
        """Kling 使用 Bearer Token 认证"""
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }


__all__ = ["KlingAdapter", "KlingPriceEstimate"]
