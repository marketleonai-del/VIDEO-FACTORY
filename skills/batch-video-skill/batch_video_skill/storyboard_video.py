from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .newapi_provider import NewApiVideoClient
from .reference_host import CloudflareQuickTunnel

PROVIDER_CAPABILITIES = {
    "grok": {"model": "grok-imagine-video-cli", "max_seconds": 15},
    "omni": {"model": "omni-fast", "max_seconds": 10},
}


def compile_storyboard_video_requests(
    storyboard_dir: Path,
    *,
    provider: str,
) -> list[dict[str, Any]]:
    capability = PROVIDER_CAPABILITIES.get(provider)
    if not capability:
        raise ValueError(f"Unsupported storyboard video provider: {provider}")
    manifest_path = storyboard_dir / "video-reference-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    requests: list[dict[str, Any]] = []
    for segment in manifest.get("segments", []):
        if not segment.get("ready_for_video"):
            raise RuntimeError(
                f"Storyboard segment {segment.get('segment_id')} is not QC-ready; video submission is blocked"
            )
        seconds = int(round(float(segment.get("duration_sec") or 0)))
        if seconds < 1 or seconds > int(capability["max_seconds"]):
            raise ValueError(
                f"{provider} supports at most {capability['max_seconds']}s per storyboard segment; "
                f"rebuild with --sheet-seconds {capability['max_seconds']}"
            )
        reference = Path(str(segment.get("input_reference") or ""))
        if not reference.exists():
            raise FileNotFoundError(f"Storyboard reference is missing: {reference}")
        expected_hash = str(segment.get("input_reference_sha256") or "")
        actual_hash = _sha256_file(reference)
        if not expected_hash or actual_hash != expected_hash:
            raise RuntimeError(
                f"Storyboard reference hash mismatch for {segment.get('segment_id')}; rerun QC"
            )
        prompt = str(segment.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"Storyboard segment {segment.get('segment_id')} has no prompt")
        requests.append(
            {
                "segment_id": str(segment["segment_id"]),
                "provider": provider,
                "model": str(capability["model"]),
                "seconds": seconds,
                "input_reference_path": str(reference.resolve()),
                "input_reference_sha256": actual_hash,
                "prompt": prompt,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "prompt_chars": len(prompt),
            }
        )
    if not requests:
        raise ValueError("Storyboard manifest contains no video segments")
    return requests


def run_storyboard_video(
    storyboard_dir: Path,
    *,
    provider: str,
    live: bool,
    cloudflared_path: str,
    base_url: str,
    timeout_sec: int = 1200,
    poll_interval_sec: int = 8,
) -> dict[str, Any]:
    storyboard_dir = storyboard_dir.expanduser().resolve()
    requests = compile_storyboard_video_requests(storyboard_dir, provider=provider)
    plan_path = storyboard_dir / f"{provider}-video-requests.json"
    public_plan = [
        {key: value for key, value in item.items() if key != "prompt"} for item in requests
    ]
    plan_path.write_text(json.dumps(public_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if not live:
        return {
            "live": False,
            "provider": provider,
            "request_count": len(requests),
            "request_plan": str(plan_path),
            "videos": [],
        }

    api_key = os.environ.get("NEWAPI_API_KEY", "")
    if not api_key:
        raise RuntimeError("NEWAPI_API_KEY is required for --live and is never stored in the Skill")
    client = NewApiVideoClient(api_key=api_key, base_url=base_url)
    state_dir = storyboard_dir / "state"
    video_dir = storyboard_dir / "videos"
    state_dir.mkdir(exist_ok=True)
    video_dir.mkdir(exist_ok=True)
    videos: list[dict[str, Any]] = []
    with CloudflareQuickTunnel(cloudflared_path, startup_timeout_sec=45) as host:
        for item in requests:
            segment_id = item["segment_id"]
            state_path = state_dir / f"{provider}-{segment_id}.json"
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            task_id = str(state.get("task_id") or "")
            reference_url = str(state.get("input_reference_url") or "")
            if not task_id:
                reference_url = host.register(Path(item["input_reference_path"]))

            def persist(submitted_id: str, *, _reference_url: str = reference_url) -> None:
                state_path.write_text(
                    json.dumps(
                        {"task_id": submitted_id, "input_reference_url": _reference_url},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

            output = video_dir / f"{provider}-{segment_id}.mp4"
            result = client.generate(
                model=item["model"],
                prompt=item["prompt"],
                seconds=item["seconds"],
                output=output,
                input_reference=reference_url,
                task_id=task_id,
                on_submitted=persist,
                timeout_sec=timeout_sec,
                poll_interval_sec=poll_interval_sec,
            )
            result_data = result.to_dict()
            state_path.write_text(
                json.dumps({**result_data, "status": "completed"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            videos.append(result_data)
    return {
        "live": True,
        "provider": provider,
        "request_count": len(requests),
        "request_plan": str(plan_path),
        "videos": videos,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
