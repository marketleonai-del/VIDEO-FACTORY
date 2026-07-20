from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .feishu_outbox import FeishuOutbox
from .physical_pipeline import (
    CreativePlanV2,
    PhysicalSegment,
    build_creative_plan_request,
    build_prompt_cache_key,
    build_reference_chain,
    compile_provider_segments,
    sha256_file,
    validate_creative_plan,
)

V4_PROVIDERS = {"auto", "grok", "omni", "both"}


def validate_v4_options(options: Any) -> None:
    if options.video_provider not in V4_PROVIDERS:
        raise ValueError(f"Unsupported V4 provider: {options.video_provider}")
    if options.duration < 1:
        raise ValueError("--duration must be >= 1")
    if options.variants < 1:
        raise ValueError("--variants must be >= 1")
    if options.physical_mode not in {"strict", "balanced", "off"}:
        raise ValueError("--physical-mode must be strict, balanced, or off")
    if options.reference_strategy not in {"image2-chain", "image2-first", "none"}:
        raise ValueError("--reference-strategy must be image2-chain, image2-first, or none")
    if (
        options.physical_mode == "strict"
        and options.reference_strategy == "none"
        and not options.dry_run
    ):
        raise ValueError("strict physical mode requires an Image2 reference strategy")
    if options.reference_host not in {"auto", "url-base", "tunnel"}:
        raise ValueError("--reference-host must be auto, url-base, or tunnel")
    if options.qc_mode not in {"risk", "all", "local"}:
        raise ValueError("--qc-mode must be risk, all, or local")
    if options.max_regenerations not in {0, 1}:
        raise ValueError("--max-regenerations must be 0 or 1")


async def run_v4_batch_folder(options: Any) -> Any:
    from .batch_folder_runner import BatchFolderResult, JobRecord

    validate_v4_options(options)
    image_dir = options.image_dir.expanduser().resolve()
    if not image_dir.exists() or not image_dir.is_dir():
        raise FileNotFoundError(f"Image folder does not exist: {image_dir}")
    images = sorted(
        [
            path
            for path in image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        ],
        key=lambda path: path.name.lower(),
    )
    if options.max_items:
        images = images[: max(0, options.max_items)]
    if not images:
        raise FileNotFoundError(f"No supported images found in: {image_dir}")

    output_name = (
        options.output_subdir or f"batch_video_outputs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    output_dir = image_dir / _safe_filename(output_name)
    directories = {
        name: output_dir / name
        for name in (
            "gem_requests",
            "gem_outputs",
            "prompts",
            "prompt_cache",
            "references",
            "qc",
            "segments",
            "videos",
            "state",
            "routing",
        )
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)

    providers = _resolve_providers(options.video_provider, output_dir)
    records: list[Any] = []
    creative_jobs: list[dict[str, Any]] = []
    segment_rows: list[dict[str, Any]] = []
    outbox = FeishuOutbox(output_dir / "feishu_outbox.jsonl")
    batch_id = output_dir.name
    outbox.enqueue(
        "批次",
        batch_id,
        {
            "batch_id": batch_id,
            "输入文件夹": str(image_dir),
            "商品数量": len(images),
            "状态": "queued" if options.dry_run else "running",
            "成功数": 0,
            "失败数": 0,
            "QC失败数": 0,
        },
    )

    shared_gem_client = None
    if not options.dry_run and options.use_gem:
        from .batch_folder_runner import _make_gem_client, load_config

        shared_gem_client = _make_gem_client(load_config(options.config_path), options)

    for image in images:
        product_name = options.product_name or _product_name_from_file(image)
        image_hash = sha256_file(image)
        route_result = None
        route_picks: list[dict[str, Any]] = []
        if options.auto_gem_route or not options.gem_id:
            from .router840_adapter import route_product_840

            route_result = route_product_840(
                product_name=product_name,
                category=options.category,
                country=options.target_country,
                duration_sec=options.duration,
                n=options.variants,
                description=f"{product_name}; source filename {image.name}",
            )
            route_picks = list(route_result["picks"])
            (directories["routing"] / f"{image_hash[:16]}.json").write_text(
                json.dumps(route_result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        for variant in range(1, options.variants + 1):
            job_id = f"{image_hash[:16]}-v{variant:02d}"
            route_pick = route_picks[variant - 1] if route_picks else {}
            gem_id = str(
                route_pick.get("gem_id")
                or options.gem_id
                or getattr(shared_gem_client, "gem_id", "")
                or "auto-route"
            )
            gem_name = str(route_pick.get("gem_name") or route_pick.get("name") or "")
            request = build_creative_plan_request(
                product_name=product_name,
                category=options.category,
                market=options.target_country,
                duration_sec=options.duration,
                variant=variant,
                source_name=image.name,
            )
            cache_key = build_prompt_cache_key(
                image_sha256=image_hash,
                gem_id=gem_id,
                market=options.target_country,
                duration_sec=options.duration,
                variant=variant,
                request=request,
            )
            request_path = directories["gem_requests"] / f"{job_id}.txt"
            output_path = directories["gem_outputs"] / f"{job_id}.json"
            request_path.write_text(request, encoding="utf-8")

            if options.dry_run or not options.use_gem:
                plan = _fallback_plan(product_name, options.category, options.target_country)
                cache_hit = False
                output_path.write_text(
                    json.dumps(
                        {"dry_run": True, "gem_called": False, "creative_plan": plan.to_dict()},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            else:
                plan, cache_hit = _load_or_call_gem_plan(
                    gem_client=shared_gem_client,
                    active_gem_id=gem_id,
                    cache_dir=directories["prompt_cache"],
                    cache_key=cache_key,
                    request=request,
                    output_path=output_path,
                )
            validate_creative_plan(plan, mode=options.physical_mode)
            plan_path = directories["prompt_cache"] / f"{cache_key}.json"
            if not plan_path.exists():
                plan_path.write_text(plan.to_compact_json(), encoding="utf-8")

            creative_jobs.append(
                {
                    "job_id": job_id,
                    "product_name": product_name,
                    "source_image": str(image),
                    "image_sha256": image_hash,
                    "gem_id": gem_id,
                    "gem_name": gem_name,
                    "route_pick": route_pick,
                    "gem_request_path": str(request_path),
                    "gem_output_path": str(output_path),
                    "plan_path": str(plan_path),
                    "plan_hash": plan.plan_hash,
                    "cache_key": cache_key,
                    "cache_hit": cache_hit,
                    "plan": plan.to_dict(),
                }
            )
            outbox.enqueue(
                "创意计划",
                job_id,
                {
                    "plan_id": job_id,
                    "商品哈希": image_hash,
                    "模板/Gem": gem_id,
                    "Gem输出路径": str(output_path),
                    "计划哈希": plan.plan_hash,
                    "缓存命中": cache_hit,
                    "字符数": len(plan.to_compact_json()),
                    "Token估算": max(1, len(plan.to_compact_json()) // 4),
                },
            )

            for provider in providers:
                compiled = compile_provider_segments(
                    plan,
                    provider=provider,
                    total_duration_sec=options.duration,
                )
                chain = build_reference_chain(compiled, directories["references"])
                provider_reference_dir = directories["references"] / job_id / provider
                provider_reference_dir.mkdir(parents=True, exist_ok=True)
                reference_plan_path = (
                    directories["references"] / f"{job_id}-{provider}-reference-plan.json"
                )
                reference_plan_path.write_text(
                    json.dumps([asdict(item) for item in chain], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                for item, reference in zip(compiled, chain, strict=True):
                    segment_id = f"{job_id}:{provider}:{item.segment_index:03d}"
                    prompt_path = (
                        directories["prompts"] / f"{job_id}-{provider}-{item.segment_index:03d}.txt"
                    )
                    prompt_path.write_text(item.prompt, encoding="utf-8")
                    prompt_hash = hashlib.sha256(item.prompt.encode("utf-8")).hexdigest()
                    execution_fingerprint = _execution_fingerprint(
                        image_sha256=image_hash,
                        plan_hash=plan.plan_hash,
                        prompt_hash=prompt_hash,
                        provider=provider,
                        model=item.model,
                        duration_sec=item.duration_sec,
                        physical_mode=options.physical_mode,
                        reference_strategy=options.reference_strategy,
                        qc_mode=options.qc_mode,
                    )
                    row = {
                        **item.to_dict(),
                        "segment_id": segment_id,
                        "job_id": job_id,
                        "prompt_path": str(prompt_path),
                        "prompt_hash": prompt_hash,
                        "execution_fingerprint": execution_fingerprint,
                        "reference_plan_path": str(reference_plan_path),
                        "keyframe_path": str(provider_reference_dir / reference.keyframe_name),
                        "tail_path": str(provider_reference_dir / reference.tail_name),
                        "video_path": str(
                            directories["segments"]
                            / f"{job_id}-{provider}-{item.segment_index:03d}.mp4"
                        ),
                        "status": "dry_run" if options.dry_run else "queued",
                        "retry_count": 0,
                        "qc_status": "pending",
                    }
                    segment_rows.append(row)
                    outbox.enqueue(
                        "视频分段",
                        segment_id,
                        {
                            "segment_id": segment_id,
                            "模型": item.model,
                            "段号": item.segment_index,
                            "风险等级": item.physical_risk,
                            "参考图": ",".join(reference.reference_names),
                            "提示词哈希": prompt_hash,
                            "提示词摘要": item.prompt[:360],
                            "task ID": "",
                            "重试次数": 0,
                            "QC": "pending",
                            "视频链接": "",
                            "失败原因": "",
                        },
                    )

            record = JobRecord(
                source_image=str(image),
                product_name=product_name,
                variant=variant,
                status="dry_run" if options.dry_run else "queued",
                prompt_path=str(plan_path),
                prompt_source="creative_plan_v2_dry_run" if options.dry_run else "creative_plan_v2",
                gem_id=gem_id,
                gem_name=gem_name,
                gem_route_score=float(route_pick.get("match_score") or 0),
                gem_route_reason="; ".join(
                    str(value) for value in (route_pick.get("reasons") or [])[:4]
                ),
                gem_output_path=str(output_path),
                video_provider=options.video_provider,
                started_at=datetime.now().isoformat(timespec="seconds"),
                completed_at=datetime.now().isoformat(timespec="seconds")
                if options.dry_run
                else "",
            )
            records.append(record)
            (directories["state"] / f"{job_id}.json").write_text(
                json.dumps(record.to_row(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    creative_index = {
        "schema_version": "CreativePlanV2",
        "batch_id": batch_id,
        "jobs": creative_jobs,
    }
    (output_dir / "creative_plan.json").write_text(
        json.dumps(creative_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "segments.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in segment_rows
        ),
        encoding="utf-8",
    )

    manifest_csv = output_dir / "batch_manifest.csv"
    manifest_jsonl = output_dir / "batch_manifest.jsonl"
    failures_csv = output_dir / "failures.csv"
    _write_records(manifest_csv, manifest_jsonl, records)
    _write_records(failures_csv, None, [record for record in records if record.status == "failed"])

    if not options.dry_run:
        await _execute_live_jobs(options, output_dir, creative_jobs, segment_rows, outbox, records)
        _write_records(manifest_csv, manifest_jsonl, records)
        _write_records(
            failures_csv,
            None,
            [record for record in records if record.status in {"failed", "needs_review"}],
        )

    completed = sum(record.status in {"completed", "dry_run"} for record in records)
    failed = sum(record.status in {"failed", "needs_review"} for record in records)
    skipped = sum(record.status == "skipped" for record in records)
    return BatchFolderResult(
        output_dir=str(output_dir),
        total_images=len(images),
        total_jobs=len(records),
        completed=completed,
        failed=failed,
        skipped=skipped,
        dry_run=options.dry_run,
        manifest_csv=str(manifest_csv),
        manifest_jsonl=str(manifest_jsonl),
        failures_csv=str(failures_csv),
        completed_at=datetime.now().isoformat(timespec="seconds"),
    )


def _load_or_call_gem_plan(
    *,
    gem_client: Any,
    active_gem_id: str,
    cache_dir: Path,
    cache_key: str,
    request: str,
    output_path: Path,
) -> tuple[CreativePlanV2, bool]:
    from .physical_pipeline import CreativePlanCache

    if gem_client is None:
        raise RuntimeError("Shared Gem client is not initialized")
    cache = CreativePlanCache(cache_dir)

    def producer() -> str:
        raw = gem_client.generate_prompts(request, gem_id=active_gem_id)
        output_path.write_text(raw, encoding="utf-8")
        return raw

    plan, hit = cache.get_or_create(cache_key, producer)
    if hit and not output_path.exists():
        output_path.write_text(
            json.dumps(
                {"cache_hit": True, "creative_plan": plan.to_dict()}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
    return plan, hit


async def _execute_live_jobs(
    options: Any,
    output_dir: Path,
    creative_jobs: list[dict[str, Any]],
    segment_rows: list[dict[str, Any]],
    outbox: FeishuOutbox,
    records: list[Any],
) -> None:
    from .v4_runtime import execute_v4_jobs

    await execute_v4_jobs(
        options=options,
        output_dir=output_dir,
        creative_jobs=creative_jobs,
        segment_rows=segment_rows,
        outbox=outbox,
        records=records,
    )


def _fallback_plan(product_name: str, category: str, market: str) -> CreativePlanV2:
    lowered = f"{product_name} {category}".lower()
    rotational = any(
        token in lowered for token in ("shaved-ice", "shaved ice", "刨冰", "grinder", "crank")
    )
    action = "rotate" if rotational else "showcase"
    hands = "both" if rotational else "none"
    axis = "vertical clockwise" if rotational else "stationary"
    risk = "high" if rotational else "low"
    start = "product upright and fully supported on the counter"
    end = "product upright and fully supported on the counter"
    return CreativePlanV2(
        product_lock=f"preserve the exact colors, parts, proportions, openings and controls of {product_name}",
        mechanism=(
            "one hand stabilizes the base while the other rotates only the intended crank clockwise"
            if rotational
            else "keep the product fully supported and do not invent an operation"
        ),
        scene_lock=f"ordinary lived-in home in {market}, natural daylight, vertical phone UGC",
        hook=f"show one credible everyday need for {product_name}",
        voiceover="",
        negative_constraints=[
            "no clipping",
            "no extra fingers",
            "no product deformation",
            "no floating parts",
            "no impossible mechanics",
        ],
        segments=[
            PhysicalSegment(
                segment_id="s01",
                start_state=start,
                action=action,
                hand_occupancy=hands,
                motion_axis=axis,
                end_state=end,
                physical_risk=risk,
                visual_goal="one clear cause-and-effect product proof shot",
            )
        ],
    )


def _resolve_providers(value: str, output_dir: Path) -> list[str]:
    if value == "both":
        return ["grok", "omni"]
    if value != "auto":
        return [value]
    history_path = output_dir.parent / ".batch-video-skill-provider-qc.json"
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
            scores = {
                provider: float((history.get(provider) or {}).get("physical_pass_rate") or 0)
                for provider in ("grok", "omni")
            }
            return [max(scores, key=scores.get)]
        except (OSError, ValueError, TypeError):
            pass
    return ["grok"]


def _execution_fingerprint(
    *,
    image_sha256: str,
    plan_hash: str,
    prompt_hash: str,
    provider: str,
    model: str,
    duration_sec: int,
    physical_mode: str,
    reference_strategy: str,
    qc_mode: str,
) -> str:
    payload = {
        "schema": "batch-video-skill-v4-execution-v1",
        "image_sha256": image_sha256,
        "plan_hash": plan_hash,
        "prompt_hash": prompt_hash,
        "provider": provider,
        "model": model,
        "duration_sec": duration_sec,
        "physical_mode": physical_mode,
        "reference_strategy": reference_strategy,
        "qc_mode": qc_mode,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write_records(csv_path: Path, jsonl_path: Path | None, records: list[Any]) -> None:
    from .batch_folder_runner import JobRecord

    fieldnames = list(JobRecord.__dataclass_fields__.keys())
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(record.to_row())
    if jsonl_path is not None:
        jsonl_path.write_text(
            "".join(
                json.dumps(record.to_row(), ensure_ascii=False, separators=(",", ":")) + "\n"
                for record in records
            ),
            encoding="utf-8",
        )


def _product_name_from_file(image: Path) -> str:
    return " ".join(image.stem.replace("_", " ").replace("-", " ").split()) or "product"


def _safe_filename(value: str) -> str:
    for token in '<>:"/\\|?*':
        value = value.replace(token, "_")
    return value.strip().strip(".") or "batch_video_outputs"
