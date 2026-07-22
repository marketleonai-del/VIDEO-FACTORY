"""Folder-based batch video production runner.

This module is intentionally separate from the older controller-level batch
command. It wraps the locally verified HFSY first-frame -> Kuaizi video path
and persists every job so long batches can resume after interruption.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

try:
    from .newapi_provider import download_http_file, validate_http_base_url
    from .prompt_transport import prompt_attachment
except ImportError:  # Legacy direct-module import compatibility.
    from newapi_provider import download_http_file, validate_http_base_url
    from prompt_transport import prompt_attachment

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
CONFIG_ENV = "BATCH_VIDEO_CONFIG_PATH"
DEFAULT_CONFIG_PATH = Path.home() / ".batch-video-skill" / "config.json"

FIRST_FRAME_MODES = Literal["generated", "url-base", "none"]
VIDEO_PROVIDERS = Literal["kuaizi", "doubao-switcher", "auto", "grok", "omni", "both"]


@dataclass(slots=True)
class BatchFolderOptions:
    image_dir: Path
    output_subdir: str | None = None
    variants: int = 1
    duration: int = 15
    concurrency: int = 1
    target_country: str = "美国"
    category: str = "home product"
    product_name: str | None = None
    first_frame_mode: FIRST_FRAME_MODES = "generated"
    image_url_base: str | None = None
    use_gem: bool = True
    gem_id: str | None = None
    auto_gem_route: bool = False
    gem_registry_path: Path | None = None
    gem_model: str = "flash"
    gem_backend: str = "gemcli"  # "gemcli" (Gemini web) or "local" (text-model API)
    gemcli_path: str = "gemcli"
    gem_timeout_sec: int = 180
    gem_retries: int = 2
    gem_retry_delay_sec: int = 5
    generate_audio: bool = True
    dry_run: bool = False
    resume: bool = True
    max_items: int | None = None
    timeout_min: int = 18
    poll_interval_sec: int = 10
    config_path: Path | None = None
    video_provider: VIDEO_PROVIDERS = "kuaizi"
    doubao_cli_path: str = r"D:\tools\venvs\cli-anything\Scripts\doubao-switcher-cli.exe"
    doubao_generation_wait_sec: int = 180
    physical_mode: Literal["strict", "balanced", "off"] = "strict"
    reference_strategy: Literal["image2-chain", "image2-first", "none"] = "image2-chain"
    reference_host: Literal["auto", "url-base", "tunnel"] = "auto"
    qc_mode: Literal["risk", "all", "local"] = "risk"
    max_regenerations: int = 1
    feishu_sync: Literal["async", "off"] = "async"
    feishu_config_path: Path = Path(r"D:\Feishu\video-feishu-sync\config\config.yaml")
    cloudflared_path: str = r"D:\tools\cloudflared\cloudflared.exe"
    newapi_base_url: str = "https://aihubcc.cc"


@dataclass(slots=True)
class BatchFolderResult:
    output_dir: str
    total_images: int
    total_jobs: int
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    dry_run: bool = False
    manifest_csv: str = ""
    manifest_jsonl: str = ""
    failures_csv: str = ""
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BatchJob:
    source_image: Path
    product_name: str
    variant: int
    prompt: str
    first_frame_prompt: str
    prompt_path: Path
    first_frame_path: Path
    video_path: Path
    gem_request_path: Path
    gem_output_path: Path
    product_reference_url: str | None = None
    first_frame_url: str | None = None
    category: str = ""
    routed_gem_id: str = ""
    routed_gem_name: str = ""
    routed_gem_score: float = 0.0
    routed_gem_reason: str = ""

    @property
    def state_path(self) -> Path:
        return self.prompt_path.parent.parent / "state" / f"{self.video_path.stem}.json"


@dataclass(slots=True)
class JobRecord:
    source_image: str
    product_name: str
    variant: int
    status: str
    prompt_path: str
    prompt_source: str = "local_template"
    gem_id: str = ""
    gem_name: str = ""
    gem_route_score: float = 0.0
    gem_route_reason: str = ""
    gem_output_path: str = ""
    product_reference_url: str = ""
    first_frame_url: str = ""
    first_frame_path: str = ""
    task_id: str = ""
    video_url: str = ""
    local_video: str = ""
    video_provider: str = ""
    error: str = ""
    started_at: str = ""
    completed_at: str = ""
    elapsed_sec: float = 0.0

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


class ConfigError(RuntimeError):
    """Raised when the local production config is missing required fields."""


class HFSYImageClient:
    def __init__(self, api_key: str, base_url: str, timeout_sec: int = 180) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    def generate_first_frame(
        self,
        prompt: str,
        output_path: Path,
        reference_image_url: str | None = None,
        reference_image_urls: list[str] | None = None,
    ) -> tuple[str, str]:
        payload = {
            "model": "gpt-image-2",
            "prompt": prompt,
            "size": "720x1280",
            "n": 1,
            "response_format": "b64_json",
        }
        references = [value for value in (reference_image_urls or []) if value]
        if reference_image_url:
            references.insert(0, reference_image_url)
        if references:
            payload["reference_images"] = list(dict.fromkeys(references))[:6]
        raw = _post_json(
            f"{self.base_url}/v1/images/generations",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_sec=self.timeout_sec,
        )
        item = (raw.get("data") or [{}])[0]
        image_url = item.get("url") or item.get("b64_json")
        b64_data = item.get("b64_data")

        if not image_url and not b64_data:
            raise RuntimeError(
                f"HFSY image generation returned no image: {_safe_json_preview(raw)}"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if b64_data:
            output_path.write_bytes(base64.b64decode(b64_data))
        elif isinstance(image_url, str) and image_url.startswith("http"):
            _download_file(
                image_url,
                output_path,
                timeout_sec=self.timeout_sec,
                trusted_origin=self.base_url,
                max_bytes=50 * 1024 * 1024,
                allowed_content_types=("image/", "application/octet-stream"),
                allow_transparent_proxy=True,
            )
        elif isinstance(image_url, str):
            try:
                output_path.write_bytes(base64.b64decode(image_url, validate=True))
            except (ValueError, base64.binascii.Error) as exc:
                raise RuntimeError(
                    f"Unsupported HFSY image response: {_safe_json_preview(raw)}"
                ) from exc
        else:
            raise RuntimeError(f"Unsupported HFSY image response: {_safe_json_preview(raw)}")

        return str(image_url or ""), str(output_path)


class GemCliClient:
    def __init__(
        self,
        gemcli_path: str,
        gem_id: str,
        model: str,
        timeout_sec: int,
        retries: int,
        retry_delay_sec: int,
    ) -> None:
        self.gemcli_path = gemcli_path
        self.gem_id = gem_id
        self.model = model
        self.timeout_sec = timeout_sec
        self.retries = max(0, retries)
        self.retry_delay_sec = max(1, retry_delay_sec)

    def generate_prompts(self, request_prompt: str, gem_id: str | None = None) -> str:
        last_error: Exception | None = None
        auth_refreshed = False
        for attempt in range(self.retries + 1):
            try:
                return self._generate_once(request_prompt, gem_id=gem_id)
            except Exception as exc:  # noqa: BLE001 - retry gemcli boundary errors
                last_error = exc
                if not auth_refreshed and _looks_like_gem_auth_error(str(exc)):
                    from .gemcli_stability import ensure_gemcli_ready

                    refreshed = ensure_gemcli_ready(
                        self.gemcli_path,
                        timeout=max(90, self.timeout_sec),
                        refresh_on_failure=True,
                        check_gems=False,
                    )
                    auth_refreshed = True
                    if not refreshed.ok:
                        break
                if attempt < self.retries:
                    time.sleep(self.retry_delay_sec)
        raise RuntimeError(
            f"Gem prompt generation failed after {self.retries + 1} attempt(s): {last_error}"
        )

    def _generate_once(self, request_prompt: str, gem_id: str | None = None) -> str:
        active_gem_id = gem_id or self.gem_id
        with prompt_attachment(request_prompt, prefix="gem-request") as request_path:
            command = [
                self.gemcli_path,
                "chat",
                "Follow the attached request exactly and return only its requested JSON.",
                "-f",
                str(request_path),
                "--gem",
                active_gem_id,
                "--new",
                "-m",
                self.model,
            ]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_sec,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(f"gemcli not found: {self.gemcli_path}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"gemcli timed out after {self.timeout_sec}s") from exc

        if completed.returncode != 0:
            stderr = _truncate(completed.stderr.strip() or completed.stdout.strip())
            raise RuntimeError(f"gemcli failed with exit {completed.returncode}: {stderr}")
        output = completed.stdout.strip()
        if not output:
            raise RuntimeError("gemcli returned empty output")
        return output


class KuaiziVideoClient:
    def __init__(self, api_key: str, base_url: str, timeout_sec: int = 120) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    def create_task(
        self,
        *,
        prompt: str,
        duration: int,
        first_frame_url: str | None,
        generate_audio: bool,
    ) -> str:
        payload: dict[str, Any] = {
            "prompt": prompt,
            "mode": "fast",
            "resolution": "720p",
            "ratio": "9:16",
            "duration": duration,
            "generate_audio": generate_audio,
        }
        if first_frame_url:
            payload["images"] = [{"url": first_frame_url, "role": "first_frame"}]

        raw = _post_json(
            f"{self.base_url}/ai-open-platform-api/v1/lz/video/task/create",
            headers=self._headers(),
            payload=payload,
            timeout_sec=self.timeout_sec,
        )
        task_id = ((raw.get("data") or {}).get("task_id")) if raw.get("code") == 0 else None
        if not task_id:
            raise RuntimeError(f"Kuaizi submit failed: {_safe_json_preview(raw)}")
        return str(task_id)

    def wait_for_video(
        self,
        *,
        task_id: str,
        timeout_min: int,
        poll_interval_sec: int,
    ) -> dict[str, Any]:
        deadline = time.time() + max(1, timeout_min) * 60
        last_raw: dict[str, Any] = {}
        while time.time() < deadline:
            raw = _post_json(
                f"{self.base_url}/ai-open-platform-api/v1/lz/video/task/status",
                headers=self._headers(),
                payload={"task_id": task_id},
                timeout_sec=self.timeout_sec,
            )
            last_raw = raw
            data = raw.get("data") or {}
            status = str(data.get("status") or "").lower()
            if status in {"succeeded", "success"} and data.get("video_url"):
                return data
            if status == "failed":
                raise RuntimeError(data.get("error") or raw.get("message") or "Kuaizi task failed")
            time.sleep(max(2, poll_interval_sec))
        raise TimeoutError(f"Kuaizi task timed out: {task_id}; last={_safe_json_preview(last_raw)}")

    def _headers(self) -> dict[str, str]:
        return {
            "ApiKey": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


class DoubaoSwitcherVideoClient:
    def __init__(self, cli_path: str, generation_wait_sec: int = 180) -> None:
        self.cli_path = cli_path
        self.generation_wait_sec = generation_wait_sec

    def generate_video(
        self,
        *,
        prompt: str,
        duration: int,
        output_dir: Path,
        timeout_min: int,
    ) -> dict[str, Any]:
        command = [
            self.cli_path,
            "generate-video",
            "--prompt",
            prompt,
            "--duration",
            str(duration),
            "--output-dir",
            str(output_dir),
            "--timeout-min",
            str(timeout_min),
            "--generation-wait-sec",
            str(self.generation_wait_sec),
            "--json",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max((timeout_min * 60) + self.generation_wait_sec + 120, 300),
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"doubao-switcher-cli not found: {self.cli_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"doubao-switcher-cli timed out: {exc}") from exc

        output = (completed.stdout or "").strip()
        if completed.returncode != 0:
            detail = (completed.stderr or output or "").strip()
            raise RuntimeError(
                f"doubao-switcher-cli failed with exit {completed.returncode}: {_truncate(detail)}"
            )
        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"doubao-switcher-cli returned non-JSON output: {_truncate(output)}"
            ) from exc
        if not result.get("success"):
            raise RuntimeError(
                f"doubao-switcher-cli generation failed: {_safe_json_preview(result)}"
            )
        video_path = Path(str(result.get("video_path") or ""))
        if not video_path.exists() or video_path.stat().st_size <= 0:
            raise RuntimeError(
                f"doubao-switcher-cli reported missing video_path: {_safe_json_preview(result)}"
            )
        return result


async def run_batch_folder(options: BatchFolderOptions) -> BatchFolderResult:
    if options.video_provider in {"auto", "grok", "omni", "both"}:
        from .v4_runner import run_v4_batch_folder

        return await run_v4_batch_folder(options)

    image_dir = options.image_dir.expanduser().resolve()
    if not image_dir.exists() or not image_dir.is_dir():
        raise FileNotFoundError(f"Image folder does not exist: {image_dir}")

    images = _scan_images(image_dir)
    if options.max_items:
        images = images[: max(0, options.max_items)]
    if not images:
        raise FileNotFoundError(f"No supported images found in: {image_dir}")

    _validate_options(options)
    output_dir = _make_output_dir(image_dir, options.output_subdir)
    prompt_dir = output_dir / "prompts"
    gem_request_dir = output_dir / "gem_requests"
    gem_output_dir = output_dir / "gem_outputs"
    first_frame_dir = output_dir / "first_frames"
    video_dir = output_dir / "videos"
    state_dir = output_dir / "state"
    for folder in (
        prompt_dir,
        gem_request_dir,
        gem_output_dir,
        first_frame_dir,
        video_dir,
        state_dir,
    ):
        folder.mkdir(parents=True, exist_ok=True)

    manifest_csv = output_dir / "batch_manifest.csv"
    manifest_jsonl = output_dir / "batch_manifest.jsonl"
    failures_csv = output_dir / "failures.csv"
    _ensure_csv_header(manifest_csv, JobRecord)
    _ensure_csv_header(failures_csv, JobRecord)

    jobs = _build_jobs(
        images, options, prompt_dir, gem_request_dir, gem_output_dir, first_frame_dir, video_dir
    )
    result = BatchFolderResult(
        output_dir=str(output_dir),
        total_images=len(images),
        total_jobs=len(jobs),
        dry_run=options.dry_run,
        manifest_csv=str(manifest_csv),
        manifest_jsonl=str(manifest_jsonl),
        failures_csv=str(failures_csv),
    )

    config = {} if options.dry_run else load_config(options.config_path)
    gem_client = None
    if not options.dry_run and options.use_gem:
        gem_client = _make_gem_client(config, options)
    hfsy_client = None
    if not options.dry_run and options.first_frame_mode in {"generated", "url-base"}:
        hfsy_client = _make_hfsy_client(config)
    kuaizi_client = None
    doubao_client = None
    if not options.dry_run:
        if options.video_provider == "kuaizi":
            kuaizi_client = _make_kuaizi_client(config)
        elif options.video_provider == "doubao-switcher":
            doubao_client = _make_doubao_switcher_client(config, options)
        else:
            raise ConfigError(f"Unsupported video provider: {options.video_provider}")

    lock = asyncio.Lock()
    sem = asyncio.Semaphore(max(1, options.concurrency))

    async def run_one(job: BatchJob) -> JobRecord:
        async with sem:
            record = await asyncio.to_thread(
                _process_job,
                job,
                options,
                gem_client,
                hfsy_client,
                kuaizi_client,
                doubao_client,
            )
            async with lock:
                _append_record(manifest_csv, manifest_jsonl, record)
                if record.status in {"completed", "dry_run"}:
                    result.completed += 1
                elif record.status == "skipped":
                    result.skipped += 1
                else:
                    result.failed += 1
                    _append_csv(failures_csv, record.to_row())
            return record

    await asyncio.gather(*(run_one(job) for job in jobs))
    result.completed_at = _now_iso()
    return result


def load_config(path: Path | None = None) -> dict[str, Any]:
    raw_path = path or Path(os.environ.get(CONFIG_ENV, "")) if os.environ.get(CONFIG_ENV) else path
    config_path = raw_path or DEFAULT_CONFIG_PATH
    config_path = Path(config_path).expanduser()
    if not config_path.exists():
        raise ConfigError(
            f"Config not found: {config_path}. Set {CONFIG_ENV} or create {DEFAULT_CONFIG_PATH}."
        )
    return json.loads(config_path.read_text(encoding="utf-8-sig"))


def _validate_options(options: BatchFolderOptions) -> None:
    if options.variants < 1:
        raise ValueError("--variants must be >= 1")
    if options.duration not in {5, 10, 15}:
        raise ValueError("--duration currently supports 5, 10, or 15 seconds for legacy providers")
    if options.first_frame_mode == "url-base" and not options.image_url_base:
        raise ValueError("--image-url-base is required when --first-frame-mode=url-base")
    if options.first_frame_mode not in {"generated", "url-base", "none"}:
        raise ValueError("--first-frame-mode must be generated, url-base, or none")


def _scan_images(image_dir: Path) -> list[Path]:
    return sorted(
        [p for p in image_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS],
        key=lambda p: p.name.lower(),
    )


def _make_output_dir(image_dir: Path, output_subdir: str | None) -> Path:
    name = output_subdir or f"batch_video_outputs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir = image_dir / _safe_filename(name)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _build_jobs(
    images: list[Path],
    options: BatchFolderOptions,
    prompt_dir: Path,
    gem_request_dir: Path,
    gem_output_dir: Path,
    first_frame_dir: Path,
    video_dir: Path,
) -> list[BatchJob]:
    jobs: list[BatchJob] = []
    for image in images:
        product = options.product_name or _product_name_from_file(image)
        effective_category = options.category
        routed_gem_id = ""
        routed_gem_name = ""
        routed_gem_score = 0.0
        routed_gem_reason = ""
        if options.auto_gem_route:
            try:
                from .product_gem_router import route_product_to_gem

                route = route_product_to_gem(
                    image_path=image,
                    product_text=product,
                    registry_path=options.gem_registry_path,
                    top_k=3,
                )
                selected = route.selected
                routed_gem_id = selected.gem_id
                routed_gem_name = selected.display_name
                routed_gem_score = selected.score
                routed_gem_reason = "; ".join(selected.reasons)
                if selected.title:
                    effective_category = selected.title
            except Exception as exc:  # noqa: BLE001 - routing should not stop dry-run planning
                routed_gem_reason = f"auto route failed: {exc}"
        stem = _safe_filename(image.stem)
        for variant in range(1, options.variants + 1):
            suffix = f"{stem}__v{variant:02d}"
            prompt = build_video_prompt(
                product_name=product,
                target_country=options.target_country,
                category=effective_category,
                duration=options.duration,
                variant=variant,
                source_image_name=image.name,
            )
            first_frame_prompt = build_first_frame_prompt(
                product_name=product,
                target_country=options.target_country,
                category=effective_category,
                source_image_name=image.name,
            )
            product_reference_url = (
                _join_url(options.image_url_base, image.name) if options.image_url_base else None
            )
            jobs.append(
                BatchJob(
                    source_image=image,
                    product_name=product,
                    variant=variant,
                    prompt=prompt,
                    first_frame_prompt=first_frame_prompt,
                    prompt_path=prompt_dir / f"{suffix}.txt",
                    first_frame_path=first_frame_dir / f"{suffix}.jpg",
                    video_path=video_dir / f"{suffix}.mp4",
                    gem_request_path=gem_request_dir / f"{suffix}.txt",
                    gem_output_path=gem_output_dir / f"{suffix}.md",
                    product_reference_url=product_reference_url,
                    category=effective_category,
                    routed_gem_id=routed_gem_id,
                    routed_gem_name=routed_gem_name,
                    routed_gem_score=routed_gem_score,
                    routed_gem_reason=routed_gem_reason,
                )
            )
    return jobs


def build_first_frame_prompt(
    *,
    product_name: str,
    target_country: str,
    category: str,
    source_image_name: str,
) -> str:
    return (
        "Realistic vertical 9:16 iPhone UGC first frame for an ecommerce short video. "
        f"Product: {product_name}. Category: {category}. Target market: {target_country}. "
        f"Source image filename: {source_image_name}. Show an ordinary home-use pain point scene, "
        "with the product naturally visible as the practical solution. Natural lighting, real lived-in "
        "environment, authentic handheld phone footage feeling, no captions, no stickers, no typography, "
        "no before-after labels, no ad layout, no fake glossy studio look."
    )


def build_video_prompt(
    *,
    product_name: str,
    target_country: str,
    category: str,
    duration: int,
    variant: int,
    source_image_name: str,
) -> str:
    hooks = [
        "open on a frustrating everyday problem",
        "open on a close-up of the messy pain point",
        "open on the user almost giving up, then noticing the product",
    ]
    hook = hooks[(variant - 1) % len(hooks)]
    return (
        f"Create a {duration}-second vertical 9:16 realistic UGC ecommerce video for {target_country}. "
        f"Product: {product_name}. Category: {category}. Source image filename: {source_image_name}. "
        f"Use a pain-rescue story arc: 0-2s {hook}; 2-5s product appears naturally as the rescue; "
        "5-10s show one clear use process with visible cause-and-effect; 10-13s show satisfying result "
        f"and relief; 13-{duration}s end with a simple product-in-hand moment and natural CTA energy. "
        "Keep the product appearance consistent with the first frame/reference when provided. "
        "Style: authentic TikTok/Reels phone footage, natural American home lighting, subtle handheld movement, "
        "credible human hands, no subtitles, no text overlays, no logo text, no distorted labels, no split-screen, "
        "no unrealistic transformations, no extra products replacing the hero item."
    )


def build_gem_request_prompt(job: BatchJob, options: BatchFolderOptions) -> str:
    product_reference_line = (
        f"产品参考图URL：{job.product_reference_url}\n"
        if job.product_reference_url
        else "产品参考图URL：无；不要要求查看图片，按文字信息和文件名生成提示词。\n"
    )
    return (
        "请套用你的带货视频/痛点救场模板，生成可直接进入批量视频生产链路的提示词。\n"
        "注意：这一步只调用 Gem 生成提示词，不把本地产品图发给 Gem；产品图不是视频首帧。\n\n"
        "请严格输出 JSON，不要输出 Markdown 代码块之外的解释。字段如下：\n"
        "{\n"
        '  "first_frame_prompt": "用于 HFSY/GPT-image-2 生成生活化首帧的英文提示词",\n'
        '  "video_prompt": "用于快子/Kuaizi 根据首帧生成竖屏带货视频的英文提示词",\n'
        '  "negative_prompt": "负面约束",\n'
        '  "style_notes": "风格和镜头注意事项",\n'
        '  "model_suggestion": "推荐视频模型"\n'
        "}\n\n"
        f"产品名：{job.product_name}\n"
        f"产品图片文件名：{job.source_image.name}\n"
        f"品类：{job.category or options.category}\n"
        f"目标国家/市场：{options.target_country}\n"
        f"视频时长：{options.duration}秒\n"
        f"版本编号：v{job.variant:02d}\n"
        f"{product_reference_line}"
        "生产要求：生活化、真实手机 UGC、痛点开场、产品自然救场、展示使用过程和结果，"
        "不要字幕、不要贴纸、不要品牌文字乱写、不要把产品图当首帧。"
    )


def parse_gem_prompt_output(
    raw_output: str,
    *,
    fallback_first_frame_prompt: str,
    fallback_video_prompt: str,
) -> tuple[str, str]:
    data = _extract_json_object(raw_output)
    if data:
        first_frame = str(data.get("first_frame_prompt") or "").strip()
        video = str(data.get("video_prompt") or "").strip()
        negative = str(data.get("negative_prompt") or "").strip()
        style = str(data.get("style_notes") or "").strip()
        if negative:
            first_frame = f"{first_frame}\nNegative constraints: {negative}".strip()
            video = f"{video}\nNegative constraints: {negative}".strip()
        if style:
            video = f"{video}\nStyle notes: {style}".strip()
        return first_frame or fallback_first_frame_prompt, video or fallback_video_prompt

    first_frame = _extract_labeled_section(
        raw_output,
        labels=("first_frame_prompt", "首帧提示词", "首图提示词", "HFSY", "GPT-image"),
    )
    video = _extract_labeled_section(
        raw_output,
        labels=("video_prompt", "视频提示词", "快子", "Kuaizi", "视频生成提示词"),
    )
    return (
        first_frame or fallback_first_frame_prompt,
        video or raw_output.strip() or fallback_video_prompt,
    )


def _process_job(
    job: BatchJob,
    options: BatchFolderOptions,
    gem_client: Any | None,
    hfsy_client: HFSYImageClient | None,
    kuaizi_client: KuaiziVideoClient | None,
    doubao_client: DoubaoSwitcherVideoClient | None,
) -> JobRecord:
    started = time.time()
    record = JobRecord(
        source_image=str(job.source_image),
        product_name=job.product_name,
        variant=job.variant,
        status="running",
        prompt_path=str(job.prompt_path),
        gem_id=job.routed_gem_id,
        gem_name=job.routed_gem_name,
        gem_route_score=job.routed_gem_score,
        gem_route_reason=job.routed_gem_reason,
        product_reference_url=job.product_reference_url or "",
        video_provider=options.video_provider,
        started_at=_now_iso(),
    )
    try:
        prompt = job.prompt
        first_frame_prompt = job.first_frame_prompt
        gem_request = build_gem_request_prompt(job, options)
        job.gem_request_path.parent.mkdir(parents=True, exist_ok=True)
        job.gem_request_path.write_text(gem_request, encoding="utf-8")

        if options.use_gem and not options.dry_run:
            if gem_client is None:
                raise ConfigError("Gem client is not configured")
            active_gem_id = job.routed_gem_id or gem_client.gem_id
            record.status = "gem_prompt_generating"
            record.gem_id = active_gem_id
            _write_job_state(job, record)
            raw_gem_output = gem_client.generate_prompts(gem_request, gem_id=active_gem_id)
            job.gem_output_path.parent.mkdir(parents=True, exist_ok=True)
            job.gem_output_path.write_text(raw_gem_output, encoding="utf-8")
            first_frame_prompt, prompt = parse_gem_prompt_output(
                raw_gem_output,
                fallback_first_frame_prompt=job.first_frame_prompt,
                fallback_video_prompt=job.prompt,
            )
            record.prompt_source = "gem"
            record.gem_output_path = str(job.gem_output_path)
        elif options.use_gem and options.dry_run:
            record.prompt_source = "gem_dry_run_request_only"
            record.gem_id = job.routed_gem_id or options.gem_id or ""
            record.gem_output_path = str(job.gem_output_path)
            job.gem_output_path.parent.mkdir(parents=True, exist_ok=True)
            job.gem_output_path.write_text(
                "DRY RUN: gemcli was not called. See the matching file in gem_requests/.",
                encoding="utf-8",
            )

        job.prompt_path.parent.mkdir(parents=True, exist_ok=True)
        job.prompt_path.write_text(prompt, encoding="utf-8")
        if job.first_frame_url:
            record.first_frame_url = job.first_frame_url
        _write_job_state(job, record)

        if options.resume and job.video_path.exists() and job.video_path.stat().st_size > 0:
            record.status = "skipped"
            record.local_video = str(job.video_path)
            record = _finish_record(record, started)
            _write_job_state(job, record)
            return record

        if options.dry_run:
            record.status = "dry_run"
            record = _finish_record(record, started)
            _write_job_state(job, record)
            return record

        first_frame_url = job.first_frame_url
        if options.first_frame_mode in {"generated", "url-base"}:
            if hfsy_client is None:
                raise ConfigError("HFSY client is not configured")
            first_frame_url, local_first_frame = hfsy_client.generate_first_frame(
                first_frame_prompt,
                job.first_frame_path,
                reference_image_url=job.product_reference_url,
            )
            record.first_frame_path = local_first_frame

        record.first_frame_url = first_frame_url or ""
        record.status = "first_frame_ready"
        _write_job_state(job, record)
        if options.video_provider == "doubao-switcher":
            if doubao_client is None:
                raise ConfigError("Doubao switcher client is not configured")
            record.status = "video_submitted"
            _write_job_state(job, record)
            doubao_prompt = prompt
            if first_frame_url:
                doubao_prompt = f"{prompt}\nReference first frame or product image URL: {first_frame_url}".strip()
            final = doubao_client.generate_video(
                prompt=doubao_prompt,
                duration=options.duration,
                output_dir=job.video_path.parent,
                timeout_min=options.timeout_min,
            )
            source_video = Path(str(final.get("video_path")))
            job.video_path.parent.mkdir(parents=True, exist_ok=True)
            if source_video.resolve() != job.video_path.resolve():
                shutil.copy2(source_video, job.video_path)
            record.task_id = str(
                final.get("task_id") or final.get("source_path") or source_video.name
            )
            record.video_url = str(final.get("source_path") or "")
            record.local_video = str(job.video_path)
        else:
            if kuaizi_client is None:
                raise ConfigError("Kuaizi client is not configured")
            task_id = kuaizi_client.create_task(
                prompt=prompt,
                duration=options.duration,
                first_frame_url=first_frame_url,
                generate_audio=options.generate_audio,
            )
            record.task_id = task_id
            record.status = "video_submitted"
            _write_job_state(job, record)
            final = kuaizi_client.wait_for_video(
                task_id=task_id,
                timeout_min=options.timeout_min,
                poll_interval_sec=options.poll_interval_sec,
            )
            video_url = final.get("video_url")
            if not video_url:
                raise RuntimeError(
                    f"Kuaizi finished without video_url: {_safe_json_preview(final)}"
                )
            job.video_path.parent.mkdir(parents=True, exist_ok=True)
            _download_file(
                str(video_url),
                job.video_path,
                timeout_sec=240,
                trusted_origin=kuaizi_client.base_url,
                max_bytes=2 * 1024 * 1024 * 1024,
                allowed_content_types=("video/", "application/octet-stream"),
            )
            record.video_url = str(video_url)
            record.local_video = str(job.video_path)
        record.status = "completed"
        record = _finish_record(record, started)
        _write_job_state(job, record)
        return record
    except Exception as exc:  # noqa: BLE001 - record and continue the batch
        record.status = "failed"
        record.error = str(exc)
        record = _finish_record(record, started)
        _write_job_state(job, record)
        return record


def _finish_record(record: JobRecord, started: float) -> JobRecord:
    record.completed_at = _now_iso()
    record.elapsed_sec = round(time.time() - started, 3)
    return record


def _make_hfsy_client(config: dict[str, Any]) -> HFSYImageClient:
    conf = (config.get("custom_providers") or {}).get("hfsy_image") or {}
    api_key = _pick_key(conf, env_names=("HFSY_IMAGE_API_KEY", "HFSY_API_KEY"))
    if not api_key:
        raise ConfigError("Missing HFSY image API key in config.custom_providers.hfsy_image or env")
    return HFSYImageClient(
        api_key=api_key,
        base_url=str(conf.get("base_url") or "https://www.hfsyapi.cn"),
        timeout_sec=int(conf.get("timeout") or 180),
    )


def _make_gem_client(config: dict[str, Any], options: BatchFolderOptions) -> Any:
    """Return the client that turns a gem + product facts into prompts.

    backend="local" runs the gem's instruction text against a chat model API and
    needs no Gemini cookies; backend="gemcli" keeps the original web path.
    """
    if str(options.gem_backend).lower() == "local":
        from .local_gem_client import make_local_gem_client

        return make_local_gem_client(
            config,
            gem_id=options.gem_id or "",
            timeout_sec=options.gem_timeout_sec,
            retries=options.gem_retries,
            retry_delay_sec=options.gem_retry_delay_sec,
        )

    gem_conf = config.get("gemini") or {}
    gem_id = options.gem_id or gem_conf.get("gem_id")
    if not gem_id and not options.auto_gem_route:
        raise ConfigError("Missing Gem ID. Pass --gem-id or set gemini.gem_id in config.")
    gem_id = gem_id or "auto-route"
    from .gemcli_stability import ensure_gemcli_ready

    preflight = ensure_gemcli_ready(
        options.gemcli_path,
        timeout=max(90, options.gem_timeout_sec),
    )
    if not preflight.ok:
        raise ConfigError(f"Gemini Gem channel is not ready: {preflight.checks}")
    return GemCliClient(
        gemcli_path=options.gemcli_path,
        gem_id=str(gem_id),
        model=options.gem_model,
        timeout_sec=options.gem_timeout_sec,
        retries=options.gem_retries,
        retry_delay_sec=options.gem_retry_delay_sec,
    )


def _make_kuaizi_client(config: dict[str, Any]) -> KuaiziVideoClient:
    conf = (config.get("video_providers") or {}).get("kuaizi") or {}
    api_key = _pick_key(conf, env_names=("KUAIZI_API_KEY",))
    if not api_key:
        raise ConfigError("Missing Kuaizi API key in config.video_providers.kuaizi or env")
    return KuaiziVideoClient(
        api_key=api_key,
        base_url=str(conf.get("base_url") or "https://aiopenapi.kuaizi.cn"),
        timeout_sec=int(conf.get("timeout") or 120),
    )


def _make_doubao_switcher_client(
    config: dict[str, Any], options: BatchFolderOptions
) -> DoubaoSwitcherVideoClient:
    conf = (config.get("video_providers") or {}).get("doubao_switcher") or {}
    cli_path = str(conf.get("cli_path") or options.doubao_cli_path or "doubao-switcher-cli")
    if not shutil.which(cli_path) and not Path(cli_path).exists():
        raise ConfigError(f"Missing doubao-switcher-cli executable: {cli_path}")
    return DoubaoSwitcherVideoClient(
        cli_path=cli_path,
        generation_wait_sec=int(
            conf.get("generation_wait_sec") or options.doubao_generation_wait_sec
        ),
    )


def _pick_key(conf: dict[str, Any], *, env_names: tuple[str, ...]) -> str | None:
    for env_name in env_names:
        value = os.environ.get(env_name)
        if value:
            return value
    extra = conf.get("extra") if isinstance(conf.get("extra"), dict) else {}
    api_keys = extra.get("api_keys") or conf.get("api_keys") or []
    if isinstance(api_keys, list):
        for value in api_keys:
            if value:
                return str(value)
    value = conf.get("api_key")
    return str(value) if value else None


def _post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_sec: int,
) -> dict[str, Any]:
    url = validate_http_base_url(url)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - URL scheme and host validated above
        url, data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {_truncate(body)}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error from {url}: {exc.reason}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response from {url}: {_truncate(body)}") from exc


def _download_file(
    url: str,
    output_path: Path,
    timeout_sec: int,
    *,
    trusted_origin: str,
    max_bytes: int,
    allowed_content_types: tuple[str, ...],
    allow_transparent_proxy: bool = False,
) -> None:
    download_http_file(
        url,
        output_path,
        trusted_origin=trusted_origin,
        headers={"User-Agent": "batch-video-skill/4.0"},
        timeout_sec=timeout_sec,
        max_bytes=max_bytes,
        allowed_content_types=allowed_content_types,
        verify_response_peer=not allow_transparent_proxy,
    )


def _ensure_csv_header(path: Path, row_type: type[JobRecord]) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(row_type.__dataclass_fields__.keys()))
        writer.writeheader()


def _append_record(csv_path: Path, jsonl_path: Path, record: JobRecord) -> None:
    row = record.to_row()
    _append_csv(csv_path, row)
    with jsonl_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


def _append_csv(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(row.keys()))
        writer.writerow(row)


def _write_job_state(job: BatchJob, record: JobRecord) -> None:
    job.state_path.parent.mkdir(parents=True, exist_ok=True)
    job.state_path.write_text(
        json.dumps(record.to_row(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    candidates: list[str] = []
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1))
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _extract_labeled_section(raw: str, *, labels: tuple[str, ...]) -> str:
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        normalized = line.strip().strip("#：: *-").lower()
        if any(label.lower() in normalized for label in labels):
            collected: list[str] = []
            for next_line in lines[index + 1 :]:
                stripped = next_line.strip()
                if collected and re.match(
                    r"^#{1,6}\s+|^[\w\s\u4e00-\u9fff_-]{2,30}[：:]$", stripped
                ):
                    break
                if stripped:
                    collected.append(stripped)
            return "\n".join(collected).strip()
    return ""


def _product_name_from_file(image: Path) -> str:
    name = image.stem.replace("_", " ").replace("-", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name or "product"


def _safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip().strip(".")
    return value or "item"


def _join_url(base: str, filename: str) -> str:
    return base.rstrip("/") + "/" + urllib.parse.quote(filename)


def _safe_json_preview(value: Any) -> str:
    return _truncate(json.dumps(value, ensure_ascii=False, default=str))


def _truncate(value: str, limit: int = 500) -> str:
    return value if len(value) <= limit else value[:limit] + "..."


def _looks_like_gem_auth_error(value: str) -> bool:
    lowered = value.lower()
    return any(
        token in lowered
        for token in ("cookie", "token", "login", "auth", "unauthorized", "expired", "401")
    )


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
