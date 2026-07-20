# -*- coding: utf-8 -*-
"""Kuaizi/Lizhen video adapter.

This adapter wraps the verified Kuaizi Lizhen API used by the local
universal-video-generator project. Authentication uses the `ApiKey` header.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from video_generator import (
    PricingInfo,
    ProviderCapability,
    SubmissionError,
    TaskStatus,
    VideoAspectRatio,
    VideoGenerationRequest,
    VideoGenerator,
    VideoModelVersion,
    VideoProvider,
    VideoResolution,
    VideoTask,
)


class KuaiziAdapter(VideoGenerator):
    """Kuaizi Lizhen image/text-to-video adapter."""

    PROVIDER = VideoProvider.KUAIZI
    SUPPORTED_MODELS = [VideoModelVersion.KUAIZI_LZ_FAST]
    DEFAULT_MODEL = VideoModelVersion.KUAIZI_LZ_FAST
    ADAPTER_NAME = "Kuaizi Lizhen"
    ADAPTER_VERSION = "1.0.0"
    DEFAULT_BASE_URL = "https://aiopenapi.kuaizi.cn"
    DEFAULT_TIMEOUT = 120.0

    async def submit(
        self,
        request: VideoGenerationRequest,
        model_version: Optional[VideoModelVersion] = None,
    ) -> VideoTask:
        payload = self._build_payload(request, model_version or self.DEFAULT_MODEL)
        raw = await self._execute_request(
            "POST",
            f"{self.base_url}/ai-open-platform-api/v1/lz/video/task/create",
            json_data=payload,
        )
        task = self._parse_submit_response(raw)
        task.prompt = request.prompt
        return task

    async def poll(self, task_id: str) -> VideoTask:
        raw = await self._execute_request(
            "POST",
            f"{self.base_url}/ai-open-platform-api/v1/lz/video/task/status",
            json_data={"task_id": task_id},
        )
        task = self._parse_poll_response(raw)
        task.task_id = task_id
        return task

    async def cancel(self, task_id: str) -> bool:
        return False

    async def get_pricing(self) -> List[PricingInfo]:
        return [
            PricingInfo(
                provider=self.PROVIDER,
                model_version=VideoModelVersion.KUAIZI_LZ_FAST,
                cost_per_second_usd=0.0,
                currency="CNY",
            )
        ]

    def get_capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider=self.PROVIDER,
            supported_models=self.SUPPORTED_MODELS,
            max_duration=10,
            max_reference_images=1,
            supports_audio_generation=True,
        )

    def _default_base_url(self) -> str:
        return self.DEFAULT_BASE_URL

    def _build_auth_headers(self, api_key: str) -> Dict[str, str]:
        return {
            "ApiKey": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _build_payload(
        self, request: VideoGenerationRequest, model: VideoModelVersion
    ) -> Dict[str, Any]:
        duration = max(3, min(10, int(request.duration or 5)))
        payload: Dict[str, Any] = {
            "prompt": request.prompt,
            "mode": "fast",
            "resolution": "720p" if request.resolution == VideoResolution.P720 else request.resolution.value,
            "ratio": request.aspect_ratio.value if isinstance(request.aspect_ratio, VideoAspectRatio) else "9:16",
            "duration": duration,
            "generate_audio": bool(request.generate_audio),
        }
        first_frame = request.first_frame or (request.reference_images[0] if request.reference_images else None)
        if first_frame:
            payload["images"] = [{"url": self._normalize_image_reference(first_frame), "role": "first_frame"}]
        return payload

    def _parse_submit_response(self, raw: Dict[str, Any]) -> VideoTask:
        if raw.get("code") == 0 and raw.get("data", {}).get("task_id"):
            return VideoTask(
                task_id=str(raw["data"]["task_id"]),
                provider=self.PROVIDER,
                model_version=VideoModelVersion.KUAIZI_LZ_FAST,
                status=TaskStatus.QUEUED,
            )
        raise SubmissionError(f"Kuaizi submit failed: {raw.get('message') or raw}")

    def _parse_poll_response(self, raw: Dict[str, Any]) -> VideoTask:
        data = raw.get("data") or {}
        status = str(data.get("status") or "").lower()
        mapped = {
            "succeeded": TaskStatus.SUCCEEDED,
            "success": TaskStatus.SUCCEEDED,
            "failed": TaskStatus.FAILED,
            "running": TaskStatus.PROCESSING,
            "processing": TaskStatus.PROCESSING,
            "queued": TaskStatus.QUEUED,
        }.get(status, TaskStatus.PROCESSING)
        return VideoTask(
            task_id=str(data.get("task_id") or ""),
            provider=self.PROVIDER,
            model_version=VideoModelVersion.KUAIZI_LZ_FAST,
            status=mapped,
            video_url=data.get("video_url") or data.get("url"),
            error_message=data.get("error") or raw.get("message"),
            progress_pct=100.0 if mapped == TaskStatus.SUCCEEDED else 0.0,
            completed_at=time.time() if mapped in (TaskStatus.SUCCEEDED, TaskStatus.FAILED) else None,
        )

    def _normalize_image_reference(self, image: str) -> str:
        if image.startswith(("http://", "https://", "data:")):
            return image
        path = Path(image)
        if path.exists():
            raise SubmissionError(
                "Kuaizi requires first_frame/reference_images to be reachable "
                "HTTP(S) URLs. Upload local image files before submitting."
            )
        return image
