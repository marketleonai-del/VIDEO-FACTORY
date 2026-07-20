from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .storyboard_plan import StoryboardPlan, StoryboardShot, build_panel_prompt, write_storyboard_plan


class Image2Client(Protocol):
    def generate_first_frame(
        self,
        prompt: str,
        output_path: Path,
        reference_image_url: str | None = None,
        reference_image_urls: list[str] | None = None,
    ) -> tuple[str, str]: ...


class StoryboardVisualQC(Protocol):
    def check(self, images: list[Path], prompt: str) -> Any: ...


class AssetHost(Protocol):
    def register(self, path: Path) -> str: ...


@dataclass(frozen=True, slots=True)
class StoryboardPanelRecord:
    shot_id: str
    sheet_id: str
    frame_role: str
    image_path: str
    prompt_path: str
    reference_paths: tuple[str, ...]
    image_width: int
    image_height: int
    qc_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StoryboardBuildResult:
    output_dir: str
    plan_path: str
    panels_jsonl: str
    video_manifest_path: str
    contact_sheets: tuple[str, ...]
    panel_count: int
    sheet_count: int
    qc_path: str
    qc_passed: bool
    ready_segment_count: int
    needs_review_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_storyboard_package(
    plan: StoryboardPlan,
    *,
    product_image: Path,
    product_name: str,
    market: str,
    output_dir: Path,
    image_client: Image2Client,
    host: AssetHost,
    max_sheets: int | None = None,
    sheet_ordinals: set[int] | None = None,
    reference_fetch_retries: int = 2,
    visual_qc: StoryboardVisualQC | None = None,
    qc_mode: str = "local",
    sleep: Callable[[float], None] = time.sleep,
) -> StoryboardBuildResult:
    product_image = product_image.expanduser().resolve()
    if not product_image.exists():
        raise FileNotFoundError(f"Product image does not exist: {product_image}")
    output_dir.mkdir(parents=True, exist_ok=True)
    panel_dir = output_dir / "panels"
    prompt_dir = output_dir / "prompts"
    sheet_dir = output_dir / "sheets"
    for path in (panel_dir, prompt_dir, sheet_dir):
        path.mkdir(parents=True, exist_ok=True)

    plan_path = write_storyboard_plan(plan, output_dir / "storyboard-plan.json")
    selected_sheets = [
        sheet for sheet in plan.sheets if not sheet_ordinals or sheet.ordinal in sheet_ordinals
    ]
    if max_sheets is not None:
        selected_sheets = selected_sheets[:max_sheets]
    if not selected_sheets:
        raise ValueError("No storyboard sheets matched the requested selection")
    selected_ids = {shot_id for sheet in selected_sheets for shot_id in sheet.shot_ids}
    shots_by_id = {shot.shot_id: shot for shot in plan.shots}
    product_url = host.register(product_image)
    previous_panel_url = ""
    previous_panel_path: Path | None = None
    records: list[StoryboardPanelRecord] = []
    panel_paths: dict[str, Path] = {}

    for shot in plan.shots:
        if shot.shot_id not in selected_ids:
            continue
        sheet = next(item for item in selected_sheets if shot.shot_id in item.shot_ids)
        panel_path = panel_dir / f"{shot.shot_id}-start.png"
        prompt_path = prompt_dir / f"{shot.shot_id}-start.txt"
        prompt = build_panel_prompt(
            plan,
            shot,
            product_name=product_name,
            market=market,
            frame_role="start",
        )
        prompt_path.write_text(prompt, encoding="utf-8")
        references = [product_url]
        reference_paths = [str(product_image)]
        if previous_panel_url and previous_panel_path:
            references.append(previous_panel_url)
            reference_paths.append(str(previous_panel_path))
        _generate_with_reference_retry(
            image_client,
            prompt=prompt,
            panel_path=panel_path,
            references=references,
            retries=reference_fetch_retries,
            sleep=sleep,
        )
        width, height = validate_storyboard_panel(panel_path)
        previous_panel_url = host.register(panel_path)
        previous_panel_path = panel_path
        panel_paths[shot.shot_id] = panel_path
        records.append(
            StoryboardPanelRecord(
                shot_id=shot.shot_id,
                sheet_id=sheet.sheet_id,
                frame_role="start",
                image_path=str(panel_path),
                prompt_path=str(prompt_path),
                reference_paths=tuple(reference_paths),
                image_width=width,
                image_height=height,
                qc_status="local_passed",
            )
        )

    panels_jsonl = output_dir / "panels.jsonl"
    panels_jsonl.write_text(
        "".join(json.dumps(item.to_dict(), ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    contact_sheets: list[str] = []
    for sheet in selected_sheets:
        paths = [panel_paths[shot_id] for shot_id in sheet.shot_ids if shot_id in panel_paths]
        shots = [shots_by_id[shot_id] for shot_id in sheet.shot_ids if shot_id in panel_paths]
        if not paths:
            continue
        contact = sheet_dir / f"{sheet.sheet_id}.jpg"
        render_storyboard_sheet(plan, sheet.ordinal, shots, paths, contact)
        contact_sheets.append(str(contact))

    manifest_path = output_dir / "video-reference-manifest.json"
    manifest = _build_video_manifest(plan, selected_sheets, panel_paths, contact_sheets)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    qc = review_storyboard_package(
        plan,
        product_image=product_image,
        output_dir=output_dir,
        selected_sheets=selected_sheets,
        visual_qc=visual_qc,
        qc_mode=qc_mode,
    )
    qc_path = output_dir / "storyboard-qc.json"
    return StoryboardBuildResult(
        output_dir=str(output_dir),
        plan_path=str(plan_path),
        panels_jsonl=str(panels_jsonl),
        video_manifest_path=str(manifest_path),
        contact_sheets=tuple(contact_sheets),
        panel_count=len(records),
        sheet_count=len(contact_sheets),
        qc_path=str(qc_path),
        qc_passed=bool(qc["passed"]),
        ready_segment_count=int(qc["ready_segment_count"]),
        needs_review_count=int(qc["needs_review_count"]),
    )


def review_storyboard_package(
    plan: StoryboardPlan,
    *,
    product_image: Path,
    output_dir: Path,
    selected_sheets: list[Any],
    visual_qc: StoryboardVisualQC | None,
    qc_mode: str,
) -> dict[str, Any]:
    if qc_mode not in {"local", "risk", "all"}:
        raise ValueError("qc_mode must be local, risk or all")
    manifest_path = output_dir / "video-reference-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sheets_by_id = {sheet.sheet_id: sheet for sheet in selected_sheets}
    shots_by_id = {shot.shot_id: shot for shot in plan.shots}
    results: list[dict[str, Any]] = []

    for segment in manifest.get("segments", []):
        sheet = sheets_by_id[str(segment["segment_id"])]
        shots = [shots_by_id[shot_id] for shot_id in sheet.shot_ids]
        semantic_required = qc_mode == "all" or (
            qc_mode == "risk" and any(shot.physical_risk == "high" for shot in shots)
        )
        semantic: dict[str, Any] | None = None
        passed = True
        status = "local_passed"
        if semantic_required:
            if visual_qc is None:
                passed = False
                status = "semantic_unavailable"
                semantic = {"passed": False, "reason": "Gemini visual QC is unavailable"}
            else:
                contact_sheet = Path(str(segment["storyboard_sheet"]))
                result = visual_qc.check(
                    [product_image, contact_sheet],
                    build_storyboard_semantic_qc_prompt(plan, shots),
                )
                semantic = result.to_dict() if hasattr(result, "to_dict") else dict(result)
                passed = bool(semantic.get("passed"))
                status = "passed" if passed else "needs_review"
        candidate = str(segment.get("input_reference_candidate") or segment.get("input_reference") or "")
        candidate_hash = _sha256_file(Path(candidate)) if candidate else ""
        segment["input_reference_candidate"] = candidate
        segment["input_reference_sha256"] = candidate_hash
        segment["input_reference"] = candidate if passed else ""
        segment["ready_for_video"] = passed
        segment["qc_status"] = status
        results.append(
            {
                "segment_id": segment["segment_id"],
                "semantic_required": semantic_required,
                "passed": passed,
                "status": status,
                "semantic": semantic,
            }
        )

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    ready = sum(1 for item in results if item["passed"])
    qc = {
        "passed": ready == len(results),
        "qc_mode": qc_mode,
        "panel_count": sum(len(item.get("storyboard_panels") or []) for item in manifest["segments"]),
        "expected_panel_count": sum(len(sheet.shot_ids) for sheet in selected_sheets),
        "ready_segment_count": ready,
        "needs_review_count": len(results) - ready,
        "segments": results,
        "raw_gem_sha256": plan.source.raw_sha256,
        "plan_sha256": plan.plan_sha256,
    }
    (output_dir / "storyboard-qc.json").write_text(
        json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return qc


def build_storyboard_semantic_qc_prompt(
    plan: StoryboardPlan,
    shots: list[StoryboardShot],
) -> str:
    shot_summary = "; ".join(
        f"{shot.shot_id} {shot.primary_action[:120]}" for shot in shots
    )
    return (
        "Image 1 is the authoritative product reference. Image 2 is a labeled storyboard sheet. "
        "Compare every labeled shot against image 1 and fail the whole sheet if any shot changes "
        "the product housing, transparent cup, blade/shaft, lid, crank geometry, handle orientation, "
        "part count or operating mechanism. Also fail clipping, floating parts, impossible hand contact "
        "or extra fingers. Return compact JSON only: "
        '{"passed":bool,"clipping":bool,"extra_fingers":bool,"product_drift":bool,'
        '"wrong_mechanics":bool,"reason":"include failing shot IDs"}. '
        f"Product lock: {plan.locks.product[:700]}. Shots: {shot_summary[:900]}. "
        "Uncertainty must fail for human review."
    )


def _generate_with_reference_retry(
    image_client: Image2Client,
    *,
    prompt: str,
    panel_path: Path,
    references: list[str],
    retries: int,
    sleep: Callable[[float], None],
) -> None:
    for attempt in range(max(0, retries) + 1):
        try:
            image_client.generate_first_frame(
                prompt,
                panel_path,
                reference_image_urls=references,
            )
            return
        except RuntimeError as exc:
            message = str(exc).lower()
            retryable = (
                "image_url fetch failed" in message
                or "could not resolve host" in message
                or "reference image" in message and "fetch" in message
            )
            if not retryable or attempt >= retries:
                raise
            sleep(8.0 + attempt * 7.0)


def validate_storyboard_panel(path: Path) -> tuple[int, int]:
    if not path.exists() or path.stat().st_size < 1024:
        raise RuntimeError(f"Image2 storyboard panel is missing or too small: {path}")
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:  # noqa: BLE001 - image boundary
        raise RuntimeError(f"Image2 storyboard panel is not decodable: {path}") from exc
    if width < 256 or height < 256:
        raise RuntimeError(f"Image2 storyboard panel resolution is too small: {width}x{height}")
    if height <= width:
        raise RuntimeError(f"Image2 storyboard panel must be vertical: {width}x{height}")
    return width, height


def render_storyboard_sheet(
    plan: StoryboardPlan,
    sheet_ordinal: int,
    shots: list[StoryboardShot],
    panel_paths: list[Path],
    output: Path,
) -> Path:
    columns = 2
    rows = (len(panel_paths) + columns - 1) // columns
    cell_width = 780
    cell_height = 820
    header_height = 150
    canvas = Image.new("RGB", (columns * cell_width, header_height + rows * cell_height), "#f5f5f2")
    draw = ImageDraw.Draw(canvas)
    title_font = _font(44)
    label_font = _font(28)
    small_font = _font(24)
    sheet = plan.sheets[sheet_ordinal - 1]
    draw.rectangle((0, 0, canvas.width, header_height), fill="#15212b")
    draw.text((42, 28), f"Storyboard Sheet {sheet_ordinal}", fill="white", font=title_font)
    draw.text(
        (42, 92),
        f"{sheet.start_sec:g}-{sheet.end_sec:g}s | {plan.locks.aspect_ratio} | {plan.source.gem_name}",
        fill="#b9d7d1",
        font=small_font,
    )
    for index, (shot, path) in enumerate(zip(shots, panel_paths)):
        column = index % columns
        row = index // columns
        x = column * cell_width
        y = header_height + row * cell_height
        draw.rectangle((x + 18, y + 18, x + cell_width - 18, y + cell_height - 18), fill="white")
        with Image.open(path) as image:
            thumb = ImageOps.fit(image.convert("RGB"), (700, 620), method=Image.Resampling.LANCZOS)
        canvas.paste(thumb, (x + 40, y + 40))
        draw.text(
            (x + 42, y + 680),
            f"Shot {shot.ordinal:02d}  {shot.start_sec:g}-{shot.end_sec:g}s  {shot.title[:24]}",
            fill="#111827",
            font=label_font,
        )
        draw.text(
            (x + 42, y + 730),
            f"Risk: {shot.physical_risk} | {shot.camera[:42]}",
            fill="#4b5563",
            font=small_font,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=92, optimize=True)
    return output


def _build_video_manifest(
    plan: StoryboardPlan,
    sheets: list[Any],
    panel_paths: dict[str, Path],
    contact_sheets: list[str],
) -> dict[str, Any]:
    shots_by_id = {shot.shot_id: shot for shot in plan.shots}
    segments: list[dict[str, Any]] = []
    for index, sheet in enumerate(sheets):
        available = [shot_id for shot_id in sheet.shot_ids if shot_id in panel_paths]
        if not available:
            continue
        shots = [shots_by_id[shot_id] for shot_id in available]
        segments.append(
            {
                "segment_id": sheet.sheet_id,
                "start_sec": sheet.start_sec,
                "end_sec": sheet.end_sec,
                "duration_sec": sheet.duration_sec,
                "input_reference": str(panel_paths[available[0]]),
                "input_reference_candidate": str(panel_paths[available[0]]),
                "input_reference_sha256": _sha256_file(panel_paths[available[0]]),
                "storyboard_panels": [str(panel_paths[shot_id]) for shot_id in available],
                "storyboard_sheet": contact_sheets[index] if index < len(contact_sheets) else "",
                "prompt": "\n\n".join(shot.raw_text for shot in shots),
                "reference_role": "first_frame",
                "ready_for_video": True,
                "qc_status": "local_passed",
                "provider_rule": (
                    "Upload input_reference to current Grok/Omni. Upload storyboard_sheet only "
                    "when a provider explicitly supports storyboard-reference mode."
                ),
            }
        )
    return {
        "schema_version": "StoryboardVideoManifestV1",
        "source_plan_sha256": plan.plan_sha256,
        "raw_gem_sha256": plan.source.raw_sha256,
        "segments": segments,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _font(size: int) -> ImageFont.ImageFont:
    candidates = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()
