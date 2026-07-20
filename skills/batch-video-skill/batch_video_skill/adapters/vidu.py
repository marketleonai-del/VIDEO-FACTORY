# -*- coding: utf-8 -*-
"""
adapters/vidu.py
生数 Vidu 适配器

提供 Vidu RESTful API 的完整封装:
- 文生视频、图生视频、参考生视频、首尾帧生视频
- 异步任务提交与轮询
- 错峰模式支持
- 图片上传

提供商: 生数科技 (Shengshu Technology) - Vidu API v2
API: https://api.vidu.com (海外) / https://api.vidu.cn (国内)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, field_validator

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
# Vidu 专用枚举与模型
# ═══════════════════════════════════════════════════════════


class ViduModel(str, Enum):
    """Vidu 模型版本枚举"""
    VIDU_Q3_PRO = "viduq3-pro"
    VIDU_Q3_TURBO = "viduq3-turbo"
    VIDU_Q3 = "viduq3"
    VIDU_Q3_PRO_FAST = "viduq3-pro-fast"
    VIDU_Q2_PRO = "viduq2-pro"
    VIDU_Q2_PRO_FAST = "viduq2-pro-fast"
    VIDU_Q2_TURBO = "viduq2-turbo"
    VIDU_Q2 = "viduq2"
    VIDU_Q1 = "viduq1"
    VIDU_Q1_CLASSIC = "viduq1-classic"


class ViduResolution(str, Enum):
    """分辨率枚举"""
    P540 = "540p"
    P720 = "720p"
    P1080 = "1080p"


class ViduAspectRatio(str, Enum):
    """宽高比枚举"""
    R16_9 = "16:9"
    R9_16 = "9:16"
    R1_1 = "1:1"
    R4_3 = "4:3"
    R3_4 = "3:4"


class ViduTaskState(str, Enum):
    """任务状态枚举"""
    CREATED = "created"
    QUEUEING = "queueing"
    PREPARATION = "preparation"
    SCHEDULING = "scheduling"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"


class ViduRegion(str, Enum):
    """服务区域枚举"""
    OVERSEAS = "overseas"
    CHINA = "china"


class ViduBaseRequest(BaseModel):
    """Vidu 请求基类"""
    model_config = {"extra": "forbid"}
    model: ViduModel = Field(...)
    prompt: Optional[str] = Field(default=None, max_length=5000)
    duration: Optional[int] = Field(default=None, ge=1, le=16)
    aspect_ratio: Optional[ViduAspectRatio] = Field(default=None)
    resolution: Optional[ViduResolution] = Field(default=None)
    seed: Optional[int] = Field(default=None)
    off_peak: Optional[bool] = Field(default=None)
    callback_url: Optional[str] = Field(default=None)
    payload: Optional[str] = Field(default=None)


class ViduText2VideoRequest(ViduBaseRequest):
    """文生视频请求体"""
    audio: Optional[bool] = Field(default=None)


class ViduImage2VideoRequest(ViduBaseRequest):
    """图生视频请求体"""
    images: List[str] = Field(..., min_length=1, max_length=1)
    audio: Optional[bool] = Field(default=None)


class ViduSubjectReference(BaseModel):
    """参考主体定义"""
    name: str = Field(...)
    image_urls: List[str] = Field(..., min_length=1, max_length=3)


class ViduReference2VideoRequest(ViduBaseRequest):
    """参考生视频请求体"""
    subjects: List[ViduSubjectReference] = Field(..., min_length=1)
    auto_subjects: Optional[bool] = Field(default=False)


class ViduStartEnd2VideoRequest(ViduBaseRequest):
    """首尾帧生视频请求体"""
    start_image_url: str = Field(...)
    end_image_url: str = Field(...)


class ViduCreateTaskResponse(BaseModel):
    """创建任务响应"""
    task_id: str = Field(...)
    state: ViduTaskState = Field(default=ViduTaskState.CREATED)


class ViduTaskOutput(BaseModel):
    """任务输出结果"""
    outputs: List[str] = Field(default_factory=list)
    state: ViduTaskState = Field(...)
    task_id: str = Field(...)
    created_at: Optional[str] = Field(default=None)
    completed_at: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    credits_consumed: Optional[int] = Field(default=None)


class ViduAccountInfo(BaseModel):
    """账户信息响应"""
    credits_balance: int = Field(...)
    total_credits: int = Field(...)
    used_credits: int = Field(...)


class ViduUploadResponse(BaseModel):
    """图片上传响应"""
    url: str = Field(...)
    filename: Optional[str] = Field(default=None)


# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

DEFAULT_BASE_URL_OVERSEAS = "https://api.vidu.com"
DEFAULT_BASE_URL_CHINA = "https://api.vidu.cn"
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_MAX_POLL_TIME = 3600.0

# 模型版本映射
VIDU_MODEL_MAP: Dict[str, ViduModel] = {
    VideoModelVersion.VIDU_Q3_PRO.value: ViduModel.VIDU_Q3_PRO,
    VideoModelVersion.VIDU_Q3_TURBO.value: ViduModel.VIDU_Q3_TURBO,
}

# 状态映射
VIDU_STATUS_MAP = {
    "created": TaskStatus.PENDING,
    "queueing": TaskStatus.QUEUED,
    "preparation": TaskStatus.PROCESSING,
    "scheduling": TaskStatus.PROCESSING,
    "processing": TaskStatus.PROCESSING,
    "success": TaskStatus.SUCCEEDED,
    "failed": TaskStatus.FAILED,
    "canceled": TaskStatus.CANCELLED,
}


# ═══════════════════════════════════════════════════════════
# ViduAdapter
# ═══════════════════════════════════════════════════════════


class ViduAdapter(VideoGenerator):
    """
    生数 Vidu 视频生成适配器

    提供 Vidu 视频生成平台的完整 API 封装。
    """

    PROVIDER = VideoProvider.VIDU
    SUPPORTED_MODELS = [
        VideoModelVersion.VIDU_Q3_PRO,
        VideoModelVersion.VIDU_Q3_TURBO,
    ]
    DEFAULT_MODEL = VideoModelVersion.VIDU_Q3_PRO
    ADAPTER_NAME = "生数 Vidu"
    ADAPTER_VERSION = "2.0.0"

    DEFAULT_TIMEOUT = 60.0

    def __init__(
        self,
        api_keys: List[str],
        region: ViduRegion = ViduRegion.OVERSEAS,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: int = 3,
        max_concurrent: int = 5,
        off_peak_default: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            api_keys=api_keys,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            **kwargs,
        )
        self.region = region
        if not self.base_url or self.base_url == self.DEFAULT_BASE_URL:
            self.base_url = {
                ViduRegion.OVERSEAS: DEFAULT_BASE_URL_OVERSEAS,
                ViduRegion.CHINA: DEFAULT_BASE_URL_CHINA,
            }.get(region, DEFAULT_BASE_URL_OVERSEAS)
        self.max_concurrent = max(max_concurrent, 1)
        self.off_peak_default = off_peak_default
        self._semaphore = asyncio.Semaphore(self.max_concurrent)

    # ── 抽象方法实现 ──────────────────────────────────────

    async def submit(
        self, request: VideoGenerationRequest, model_version: Optional[VideoModelVersion] = None
    ) -> VideoTask:
        """提交视频生成任务"""
        model = self._resolve_model(model_version)
        payload = self._build_payload(request, model)

        # 确定 endpoint
        if request.first_frame or request.reference_images:
            if request.last_frame:
                endpoint = "/ent/v2/start-end2video"
            elif len(request.reference_images) >= 2:
                endpoint = "/ent/v2/reference2video"
            else:
                endpoint = "/ent/v2/img2video"
        else:
            endpoint = "/ent/v2/text2video"

        url = f"{self.base_url}{endpoint}"
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

    async def poll(self: "ViduAdapter", task_id: str) -> VideoTask:
        """查询任务状态"""
        url = f"{self.base_url}/ent/v2/creations/{task_id}"
        try:
            raw = await self._execute_request("GET", url)
            task = self._parse_poll_response(raw)
            self._metrics.record_poll(latency_ms=0)
            return task
        except Exception as e:
            self._metrics.record_error("poll")
            raise

    async def cancel(self: "ViduAdapter", task_id: str) -> bool:
        """取消进行中的任务"""
        url = f"{self.base_url}/ent/v2/creations/{task_id}/cancel"
        try:
            await self._execute_request("POST", url)
            logger.info(f"任务已取消: {task_id}")
            return True
        except Exception as e:
            logger.warning(f"取消任务失败: {task_id}: {e}")
            return False

    async def get_pricing(self: "ViduAdapter") -> List[PricingInfo]:
        """获取价格信息"""
        return [
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.VIDU_Q3_PRO,
                cost_per_second_usd=0.12,
                currency="USD",
            ),
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.VIDU_Q3_TURBO,
                cost_per_second_usd=0.055,
                currency="USD",
            ),
        ]

    def get_capability(self: "ViduAdapter") -> ProviderCapability:
        """声明能力"""
        return ProviderCapability(
            provider=self.PROVIDER,
            supported_models=self.SUPPORTED_MODELS,
            max_duration=16,
            max_reference_images=7,
            supports_4k=False,
            supports_60fps=False,
            supports_reference_videos=False,
            supports_reference_audios=False,
            supports_last_frame=True,
            supports_audio_generation=True,
            supports_negative_prompt=True,
            supports_enhance_prompt=True,
        )

    def _default_base_url(self: "ViduAdapter") -> str:
        return DEFAULT_BASE_URL_OVERSEAS

    def _build_payload(
        self, request: VideoGenerationRequest, model: VideoModelVersion
    ) -> Dict[str, Any]:
        """构建 Vidu API 请求体"""
        vidu_model = VIDU_MODEL_MAP.get(model.value, ViduModel.VIDU_Q3_PRO)

        # 分辨率映射
        resolution_map = {
            VideoResolution.P480: None,
            VideoResolution.P720: ViduResolution.P720,
            VideoResolution.P1080: ViduResolution.P1080,
            VideoResolution.P4K: None,
        }
        vidu_resolution = resolution_map.get(request.resolution)

        # 宽高比映射
        ratio_map = {
            VideoAspectRatio.RATIO_16_9: ViduAspectRatio.R16_9,
            VideoAspectRatio.RATIO_9_16: ViduAspectRatio.R9_16,
            VideoAspectRatio.RATIO_1_1: ViduAspectRatio.R1_1,
            VideoAspectRatio.RATIO_4_3: ViduAspectRatio.R4_3,
            VideoAspectRatio.RATIO_3_4: ViduAspectRatio.R3_4,
        }
        vidu_ratio = ratio_map.get(request.aspect_ratio)

        payload: Dict[str, Any] = {"model": vidu_model.value}

        if request.prompt:
            payload["prompt"] = request.prompt
        if request.duration:
            payload["duration"] = request.duration
        if vidu_resolution:
            payload["resolution"] = vidu_resolution.value
        if vidu_ratio:
            payload["aspect_ratio"] = vidu_ratio.value
        if request.generate_audio:
            payload["audio"] = True
        if self.off_peak_default or request.extra_params.get("off_peak"):
            payload["off_peak"] = True
        if request.extra_params.get("callback_url"):
            payload["callback_url"] = request.extra_params["callback_url"]

        # 根据输入类型添加特定字段
        if request.first_frame:
            payload["images"] = [request.first_frame]
        elif request.reference_images:
            payload["images"] = request.reference_images[:1]

        if request.last_frame and request.first_frame:
            payload["end_image_url"] = request.last_frame

        if len(request.reference_images) >= 2:
            subjects = []
            for i, img_url in enumerate(request.reference_images[:3]):
                subjects.append({
                    "name": f"subject_{i}",
                    "image_urls": [img_url],
                })
            payload["subjects"] = subjects
            payload["auto_subjects"] = False

        return payload

    def _parse_submit_response(self: "ViduAdapter", raw: Dict[str, Any]) -> VideoTask:
        """解析提交响应"""
        task_id = raw.get("task_id", "")
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

    def _parse_poll_response(self: "ViduAdapter", raw: Dict[str, Any]) -> VideoTask:
        """解析轮询响应"""
        task_id = raw.get("task_id", "")
        raw_state = raw.get("state", "created")
        status = VIDU_STATUS_MAP.get(raw_state, TaskStatus.PENDING)

        outputs = raw.get("outputs", [])
        video_url = outputs[0] if outputs else None

        error_message = raw.get("error_message")

        return VideoTask(
            task_id=task_id,
            provider=self.PROVIDER,
            model_version=self.DEFAULT_MODEL or self.SUPPORTED_MODELS[0],
            status=status,
            video_url=video_url,
            error_message=error_message,
        )

    # ── 额外方法 ──────────────────────────────────────────

    async def upload_image(self: "ViduAdapter", image_path: str) -> str:
        """上传图片到 Vidu 服务器"""
        url = f"{self.base_url}/ent/v2/uploads"

        # 读取图片文件
        import os
        if os.path.exists(image_path):
            with open(image_path, "rb") as f:
                image_data = f.read()
            image_b64 = base64.b64encode(image_data).decode("utf-8")
        else:
            # 假设是 URL
            return image_path

        payload = {"image": image_b64}
        try:
            raw = await self._execute_request("POST", url, json_data=payload)
            return raw.get("url", "")
        except Exception as e:
            logger.error(f"图片上传失败: {e}")
            raise

    async def get_account_info(self: "ViduAdapter") -> ViduAccountInfo:
        """查询账户信息"""
        url = f"{self.base_url}/ent/v2/account"
        raw = await self._execute_request("GET", url)
        return ViduAccountInfo(**raw)

    def _build_auth_headers(self: "ViduAdapter", api_key: str) -> Dict[str, str]:
        """Vidu 使用 Token 认证"""
        return {
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


__all__ = ["ViduAdapter", "ViduModel", "ViduTaskState"]
