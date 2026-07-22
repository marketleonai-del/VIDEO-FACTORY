from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .feishu_outbox import FeishuOutbox
from .newapi_provider import NewApiVideoClient
from .physical_pipeline import CreativePlanV2, parse_creative_plan
from .prompt_transport import prompt_attachment
from .reference_host import CloudflareQuickTunnel, ReferenceHostError
from .video_qc import (
    build_contact_sheet,
    build_semantic_qc_prompt,
    extract_tail_frame,
    parse_semantic_qc,
    run_local_image_qc,
    run_local_video_qc,
    stitch_segments,
)

RISK_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(slots=True)
class BranchResult:
    provider: str
    status: str
    final_video: str = ""
    error: str = ""


class StaticUrlBaseHost:
    """Map local file names onto a user-managed public asset directory."""

    def __init__(self, base_url: str) -> None:
        if not base_url:
            raise ReferenceHostError("--image-url-base is required for --reference-host=url-base")
        self.base_url = base_url.rstrip("/")

    def start(self) -> None:
        return

    def register(self, path: Path) -> str:
        return f"{self.base_url}/{urllib.parse.quote(path.name)}"

    def close(self) -> None:
        return


class GeminiVisualQCClient:
    def __init__(self, gemcli_path: str, *, model: str = "flash", timeout_sec: int = 180) -> None:
        self.gemcli_path = gemcli_path
        self.model = model
        self.timeout_sec = timeout_sec
        self._lock = threading.Lock()

    def check(self, images: list[Path], prompt: str) -> Any:
        with prompt_attachment(prompt, prefix="visual-qc") as prompt_path:
            command = [
                self.gemcli_path,
                "chat",
                "Inspect the attached assets and return only the requested compact JSON.",
                "-f",
                str(prompt_path),
                "--new",
                "-m",
                self.model,
            ]
            for image in images:
                command.extend(["-f", str(image)])
            with self._lock:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_sec,
                    check=False,
                )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "")[-1500:]
            raise RuntimeError(f"Gemini Flash visual QC failed: {detail}")
        lowered = completed.stdout.lower()
        if "auth error:" in lowered or "session expired" in lowered:
            raise RuntimeError(
                "Gemini Flash visual QC authentication expired; run gemcli login and retry once"
            )
        return parse_semantic_qc(completed.stdout)


async def execute_v4_jobs(
    *,
    options: Any,
    output_dir: Path,
    creative_jobs: list[dict[str, Any]],
    segment_rows: list[dict[str, Any]],
    outbox: FeishuOutbox,
    records: list[Any],
) -> None:
    from .batch_folder_runner import ConfigError, _make_hfsy_client, load_config

    config = load_config(options.config_path)
    hfsy_client = _make_hfsy_client(config)
    api_key = os.environ.get("NEWAPI_API_KEY", "")
    if not api_key:
        raise ConfigError(
            "Missing NEWAPI_API_KEY. V4 never stores this key in config or task files."
        )
    video_client = NewApiVideoClient(
        api_key=api_key,
        base_url=os.environ.get("NEWAPI_BASE_URL", options.newapi_base_url),
    )
    visual_qc = None
    if options.qc_mode in {"risk", "all"}:
        visual_qc = GeminiVisualQCClient(
            options.gemcli_path,
            model="flash",
            timeout_sec=max(90, options.gem_timeout_sec),
        )

    host: Any = None
    if options.reference_strategy != "none":
        host = _make_reference_host(options)
        host.start()

    rows_lock = threading.Lock()
    try:
        sem = asyncio.Semaphore(max(1, min(int(options.concurrency or 1), 2)))

        async def run_product(index: int, creative: dict[str, Any]) -> None:
            async with sem:
                try:
                    branches = await asyncio.to_thread(
                        _execute_product,
                        options,
                        output_dir,
                        creative,
                        segment_rows,
                        hfsy_client,
                        video_client,
                        visual_qc,
                        host,
                        outbox,
                        rows_lock,
                    )
                    failed = [branch for branch in branches if branch.status != "completed"]
                    record = records[index]
                    if failed:
                        record.status = "needs_review"
                        record.error = "; ".join(
                            f"{item.provider}: {item.error}" for item in failed
                        )
                    else:
                        record.status = "completed"
                        record.local_video = ";".join(branch.final_video for branch in branches)
                    record.completed_at = datetime.now().isoformat(timespec="seconds")
                except Exception as exc:  # noqa: BLE001 - isolate products in a large batch
                    record = records[index]
                    record.status = "failed"
                    record.error = str(exc)
                    record.completed_at = datetime.now().isoformat(timespec="seconds")

        await asyncio.gather(
            *(run_product(index, creative) for index, creative in enumerate(creative_jobs))
        )
    finally:
        if host:
            host.close()

    _write_segment_rows(output_dir, segment_rows)
    if options.feishu_sync == "async":
        _schedule_feishu_flush(output_dir, options)


def _execute_product(
    options: Any,
    output_dir: Path,
    creative: dict[str, Any],
    segment_rows: list[dict[str, Any]],
    hfsy_client: Any,
    video_client: NewApiVideoClient,
    visual_qc: GeminiVisualQCClient | None,
    host: Any,
    outbox: FeishuOutbox,
    rows_lock: threading.Lock,
) -> list[BranchResult]:
    plan = parse_creative_plan(json.dumps(creative["plan"], ensure_ascii=False))
    job_id = creative["job_id"]
    source_image = Path(creative["source_image"])
    job_reference_dir = output_dir / "references" / job_id
    job_reference_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = job_reference_dir / "canonical_product.png"
    canonical_state_path = job_reference_dir / "canonical_product.state.json"
    canonical_url = ""

    if options.reference_strategy != "none":
        if not host:
            raise ReferenceHostError("Reference strategy requires a public reference host")
        canonical_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "schema": "canonical-product-v1",
                    "image_sha256": creative["image_sha256"],
                    "product_lock": plan.product_lock,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        canonical_state = _load_json(canonical_state_path)
        image_qc = run_local_image_qc(canonical_path) if canonical_path.exists() else None
        can_resume_canonical = bool(
            getattr(options, "resume", True)
            and canonical_state.get("fingerprint") == canonical_fingerprint
            and image_qc is not None
            and image_qc.passed
        )
        if not can_resume_canonical:
            source_url = host.register(source_image)
            canonical_prompt = _canonical_product_prompt(plan)
            hfsy_client.generate_first_frame(
                canonical_prompt,
                canonical_path,
                reference_image_urls=[source_url],
            )
            image_qc = run_local_image_qc(canonical_path)
            _write_json(canonical_state_path, {"fingerprint": canonical_fingerprint})
        assert image_qc is not None
        _write_json(output_dir / "qc" / f"{job_id}-canonical.json", image_qc.to_dict())
        if not image_qc.passed:
            raise RuntimeError(f"canonical product QC failed: {image_qc.reason}")
        canonical_url = host.register(canonical_path)

    job_rows = [row for row in segment_rows if row["job_id"] == job_id]
    providers = sorted({str(row["provider"]) for row in job_rows})
    results: list[BranchResult] = []
    with ThreadPoolExecutor(max_workers=max(1, len(providers))) as executor:
        futures = {
            executor.submit(
                _execute_provider_branch,
                options,
                output_dir,
                plan,
                source_image,
                canonical_path,
                canonical_url,
                provider,
                [row for row in job_rows if row["provider"] == provider],
                hfsy_client,
                video_client,
                visual_qc,
                host,
                outbox,
                rows_lock,
                segment_rows,
            ): provider
            for provider in providers
        }
        for future in as_completed(futures):
            provider = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - keep comparison branch isolated
                results.append(
                    BranchResult(provider=provider, status="needs_review", error=str(exc))
                )
    return sorted(results, key=lambda item: item.provider)


def _execute_provider_branch(
    options: Any,
    output_dir: Path,
    plan: CreativePlanV2,
    source_image: Path,
    canonical_path: Path,
    canonical_url: str,
    provider: str,
    rows: list[dict[str, Any]],
    hfsy_client: Any,
    video_client: NewApiVideoClient,
    visual_qc: GeminiVisualQCClient | None,
    host: Any,
    outbox: FeishuOutbox,
    rows_lock: threading.Lock,
    all_rows: list[dict[str, Any]],
) -> BranchResult:
    rows.sort(key=lambda row: int(row["segment_index"]))
    execution_order = _ordered_rows(rows, options.reference_strategy)
    completed_tails: dict[int, str] = {}
    for row in execution_order:
        result = _execute_segment(
            options=options,
            output_dir=output_dir,
            plan=plan,
            source_image=source_image,
            canonical_path=canonical_path,
            canonical_url=canonical_url,
            row=row,
            previous_tail_url=completed_tails.get(int(row["segment_index"]) - 1, ""),
            hfsy_client=hfsy_client,
            video_client=video_client,
            visual_qc=visual_qc,
            host=host,
            outbox=outbox,
        )
        with rows_lock:
            _write_segment_rows(output_dir, all_rows)
        if result != "passed":
            return BranchResult(
                provider=provider, status="needs_review", error=str(row.get("error") or "QC failed")
            )
        if host and Path(row["tail_path"]).exists():
            completed_tails[int(row["segment_index"])] = host.register(Path(row["tail_path"]))

    final_path = output_dir / "videos" / f"{rows[0]['job_id']}-{provider}.mp4"
    stitch_segments([Path(row["video_path"]) for row in rows], final_path)
    final_qc = run_local_video_qc(final_path, expected_duration_sec=options.duration)
    _write_json(
        output_dir / "qc" / f"{rows[0]['job_id']}-{provider}-final.json",
        final_qc.to_dict(),
    )
    if not final_qc.passed:
        return BranchResult(
            provider=provider, status="needs_review", error="final stitch QC failed"
        )
    return BranchResult(provider=provider, status="completed", final_video=str(final_path))


def _execute_segment(
    *,
    options: Any,
    output_dir: Path,
    plan: CreativePlanV2,
    source_image: Path,
    canonical_path: Path,
    canonical_url: str,
    row: dict[str, Any],
    previous_tail_url: str,
    hfsy_client: Any,
    video_client: NewApiVideoClient,
    visual_qc: GeminiVisualQCClient | None,
    host: Any,
    outbox: FeishuOutbox,
) -> str:
    state_path = output_dir / "state" / "segments" / f"{_safe_id(row['segment_id'])}.json"
    video_path = Path(row["video_path"])
    raw_state = _load_json(state_path)
    expected_fingerprint = str(row.get("execution_fingerprint") or "")
    resume = bool(getattr(options, "resume", True))
    state, stale_reason = _select_resume_state(
        raw_state,
        expected_fingerprint=expected_fingerprint,
        resume=resume,
    )
    if stale_reason:
        _archive_stale_segment_state(
            output_dir,
            state_path,
            row,
            reason=stale_reason,
        )
    if video_path.exists() and state.get("qc_status") == "passed":
        row.update(state)
        return "passed"

    resumed_task_id = str(state.get("task_id") or "")
    if resumed_task_id:
        row["task_id"] = resumed_task_id
        row["retry_count"] = int(state.get("retry_count") or 0)
        row["input_reference"] = str(state.get("input_reference") or "")

    keyframe_path = Path(row["keyframe_path"])
    input_reference = canonical_url or previous_tail_url
    retry_reference_ready = not resumed_task_id
    if resumed_task_id:
        input_reference, retry_reference_ready = _recover_retry_reference(
            row=row,
            state=state,
            keyframe_path=keyframe_path,
            canonical_url=canonical_url,
            previous_tail_url=previous_tail_url,
            host=host,
        )
    needs_image2 = options.reference_strategy == "image2-chain" or (
        options.reference_strategy == "image2-first" and int(row["segment_index"]) == 1
    )
    retry_count = int(state.get("retry_count") or row.get("retry_count") or 0)
    if needs_image2 and not resumed_task_id:
        if not host:
            raise ReferenceHostError("Image2 reference generation requires a public asset host")
        references = [url for url in (canonical_url, previous_tail_url) if url]
        keyframe_prompt = _keyframe_prompt(plan, row, simplified=retry_count > 0)
        while True:
            hfsy_client.generate_first_frame(
                keyframe_prompt,
                keyframe_path,
                reference_image_urls=references,
            )
            local_image_qc = run_local_image_qc(keyframe_path)
            semantic = None
            if local_image_qc.passed and _should_semantic_qc(row, options.qc_mode):
                if visual_qc is None:
                    raise RuntimeError("Semantic QC is required but Gemini Flash QC is unavailable")
                semantic = visual_qc.check(
                    [source_image, keyframe_path],
                    build_semantic_qc_prompt(
                        product_lock=plan.product_lock, mechanism=plan.mechanism
                    ),
                )
            image_passed = local_image_qc.passed and (semantic is None or semantic.passed)
            _write_json(
                output_dir / "qc" / f"{_safe_id(row['segment_id'])}-keyframe.json",
                {
                    "local": local_image_qc.to_dict(),
                    "semantic": semantic.to_dict() if semantic else None,
                    "passed": image_passed,
                },
            )
            if image_passed:
                break
            if retry_count >= options.max_regenerations:
                row["status"] = "needs_review"
                row["qc_status"] = "needs_review"
                row["error"] = "Image2 keyframe QC failed"
                _persist_segment_state(state_path, row)
                _enqueue_segment(outbox, row)
                return "needs_review"
            retry_count += 1
            row["retry_count"] = retry_count
            keyframe_prompt = _keyframe_prompt(plan, row, simplified=True)
        input_reference = host.register(keyframe_path)
    elif previous_tail_url and not resumed_task_id:
        input_reference = previous_tail_url

    if options.physical_mode == "strict" and not input_reference and not resumed_task_id:
        raise RuntimeError("Strict V4 segment has no input_reference")

    if not resumed_task_id:
        row["status"] = "ready_to_submit"
        row["input_reference"] = input_reference
        _enqueue_segment(outbox, row)
        _persist_segment_state(state_path, row)

    while True:
        task_id = str(state.get("task_id") or row.get("task_id") or "")

        def on_submitted(value: str) -> None:
            row["task_id"] = value
            row["status"] = "video_submitted"
            _persist_segment_state(state_path, row)
            _enqueue_segment(outbox, row)

        prompt = (
            row["prompt"] if retry_count == 0 else simplify_prompt_for_regeneration(row["prompt"])
        )
        result = video_client.generate(
            model=row["model"],
            prompt=prompt,
            seconds=int(row["duration_sec"]),
            output=video_path,
            input_reference=input_reference,
            task_id=task_id,
            on_submitted=on_submitted,
            timeout_sec=max(300, int(options.timeout_min) * 60),
            poll_interval_sec=max(1, int(options.poll_interval_sec)),
        )
        row["task_id"] = result.task_id
        row["video_url"] = result.video_url
        row["status"] = "video_downloaded"
        local_qc = run_local_video_qc(video_path, expected_duration_sec=int(row["duration_sec"]))
        semantic = None
        if local_qc.passed and _should_semantic_qc(row, options.qc_mode):
            if visual_qc is None:
                raise RuntimeError("Semantic QC is required but Gemini Flash QC is unavailable")
            contact = output_dir / "qc" / f"{_safe_id(row['segment_id'])}-contact.jpg"
            build_contact_sheet(video_path, contact, fps=2.0)
            semantic = visual_qc.check(
                [canonical_path, contact],
                build_semantic_qc_prompt(product_lock=plan.product_lock, mechanism=plan.mechanism),
            )
        passed = local_qc.passed and (semantic is None or semantic.passed)
        _write_json(
            output_dir / "qc" / f"{_safe_id(row['segment_id'])}-video.json",
            {
                "local": local_qc.to_dict(),
                "semantic": semantic.to_dict() if semantic else None,
                "passed": passed,
                "retry_count": retry_count,
            },
        )
        if passed:
            row["status"] = "completed"
            row["qc_status"] = "passed"
            row["retry_count"] = retry_count
            extract_tail_frame(video_path, Path(row["tail_path"]))
            _persist_segment_state(state_path, row)
            _enqueue_segment(outbox, row)
            return "passed"
        if retry_count >= options.max_regenerations:
            row["status"] = "needs_review"
            row["qc_status"] = "needs_review"
            row["error"] = "; ".join(local_qc.reasons) or (
                semantic.reason if semantic else "QC failed"
            )
            _persist_segment_state(state_path, row)
            _enqueue_segment(outbox, row)
            return "needs_review"
        if resumed_task_id and not retry_reference_ready:
            row["status"] = "needs_review"
            row["qc_status"] = "needs_review"
            row["error"] = (
                "Recovered task failed QC and its original reference chain is unavailable"
            )
            _persist_segment_state(state_path, row)
            _enqueue_segment(outbox, row)
            return "needs_review"
        retry_count += 1
        row["retry_count"] = retry_count
        row["previous_task_id"] = row.get("task_id", "")
        row["task_id"] = ""
        state = {}
        _persist_segment_state(state_path, row)


def simplify_prompt_for_regeneration(prompt: str) -> str:
    lock = prompt[:800]
    return (
        f"{lock}\nREGENERATION: one slow action only; locked camera; both hands fully visible; "
        "product remains supported; no cuts, zooms, occlusion, secondary action, deformation or clipping."
    )[:1200]


def _should_semantic_qc(row: dict[str, Any], qc_mode: str) -> bool:
    return qc_mode == "all" or (qc_mode == "risk" and row.get("physical_risk") == "high")


def _ordered_rows(rows: list[dict[str, Any]], reference_strategy: str) -> list[dict[str, Any]]:
    if reference_strategy in {"image2-chain", "image2-first"}:
        return sorted(rows, key=lambda row: int(row["segment_index"]))
    return sorted(
        rows,
        key=lambda row: (
            RISK_ORDER.get(str(row["physical_risk"]), 9),
            int(row["segment_index"]),
        ),
    )


def _make_reference_host(options: Any) -> Any:
    if options.reference_host == "url-base" or (
        options.reference_host == "auto" and options.image_url_base
    ):
        return StaticUrlBaseHost(options.image_url_base or "")
    return CloudflareQuickTunnel(options.cloudflared_path)


def _select_resume_state(
    raw_state: dict[str, Any],
    *,
    expected_fingerprint: str,
    resume: bool,
) -> tuple[dict[str, Any], str]:
    if not raw_state:
        return {}, ""
    if not resume:
        return {}, "resume_disabled"
    if not expected_fingerprint or raw_state.get("execution_fingerprint") != expected_fingerprint:
        return {}, "execution_fingerprint_mismatch"
    return raw_state, ""


def _recover_retry_reference(
    *,
    row: dict[str, Any],
    state: dict[str, Any],
    keyframe_path: Path,
    canonical_url: str,
    previous_tail_url: str,
    host: Any,
) -> tuple[str, bool]:
    if host and keyframe_path.exists():
        return str(host.register(keyframe_path)), True
    if previous_tail_url:
        return previous_tail_url, True
    if int(row["segment_index"]) == 1 and canonical_url:
        return canonical_url, True
    return str(state.get("input_reference") or ""), False


def _canonical_product_prompt(plan: CreativePlanV2) -> str:
    return (
        "Create a clean, realistic vertical reference image of the exact product from the supplied source. "
        f"PRODUCT LOCK: {plan.product_lock}. Show all functional parts, correct proportions and correct assembly. "
        "Neutral natural light, product fully supported on a counter, no hands, no text, no added parts, "
        "no redesign, no geometry change, no studio-ad layout."
    )[:1200]


def _keyframe_prompt(plan: CreativePlanV2, row: dict[str, Any], *, simplified: bool) -> str:
    base = (
        "Create the starting keyframe for one physically valid video segment. "
        f"PRODUCT LOCK: {plan.product_lock}. SCENE LOCK: {plan.scene_lock}. START STATE: {row['start_state']}. "
        f"The upcoming single action is {row['prompt']}. Show exact hand contact points, full support relation, "
        "and the correct motion axis. No motion blur, clipping, extra fingers, floating parts or hidden mechanism."
    )
    if simplified:
        base += " Locked camera, wide enough to show both hands and the entire product; no secondary props."
    return base[:1200]


def _enqueue_segment(outbox: FeishuOutbox, row: dict[str, Any]) -> None:
    outbox.enqueue(
        "视频分段",
        row["segment_id"],
        {
            "segment_id": row["segment_id"],
            "模型": row["model"],
            "段号": row["segment_index"],
            "风险等级": row["physical_risk"],
            "参考图": str(row.get("input_reference") or row.get("keyframe_path") or "")[:1000],
            "提示词哈希": row["prompt_hash"],
            "提示词摘要": row["prompt"][:360],
            "task ID": row.get("task_id") or "",
            "重试次数": row.get("retry_count") or 0,
            "QC": row.get("qc_status") or "pending",
            "视频链接": row.get("video_url") or "",
            "失败原因": row.get("error") or "",
        },
    )


def _persist_segment_state(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, row)


def _write_segment_rows(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    path = output_dir / "segments.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _schedule_feishu_flush(output_dir: Path, options: Any) -> None:
    report_path = output_dir / "feishu_flush_report.json"
    try:
        command = [
            sys.executable,
            "-m",
            "batch_video_skill.cli",
            "feishu-flush",
            str(output_dir),
            "--feishu-config",
            str(options.feishu_config_path),
        ]
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        _write_json(
            report_path,
            {
                "scheduled": True,
                "pid": process.pid,
                "pending_outbox": str(output_dir / "feishu_outbox.jsonl"),
                "note": "Generation does not wait for Feishu; use feishu-flush to replay pending events.",
            },
        )
    except Exception as exc:  # noqa: BLE001 - Feishu is asynchronous and never blocks generation
        _write_json(
            report_path,
            {
                "schema": None,
                "flush": None,
                "pending": True,
                "error": str(exc)[:1500],
            },
        )


def _archive_stale_segment_state(
    output_dir: Path,
    state_path: Path,
    row: dict[str, Any],
    *,
    reason: str,
) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stale_dir = output_dir / "state" / "stale" / f"{_safe_id(row['segment_id'])}-{timestamp}"
    stale_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        state_path,
        Path(str(row.get("video_path") or "")),
        Path(str(row.get("keyframe_path") or "")),
        Path(str(row.get("tail_path") or "")),
    ]
    moved: list[str] = []
    for path in candidates:
        if not str(path) or not path.exists() or not path.is_file():
            continue
        destination = stale_dir / path.name
        path.replace(destination)
        moved.append(str(destination))
    _write_json(stale_dir / "reason.json", {"reason": reason, "moved": moved})


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(path)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
