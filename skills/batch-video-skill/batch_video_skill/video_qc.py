from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat


@dataclass(frozen=True, slots=True)
class VideoProbe:
    duration_sec: float
    width: int
    height: int
    codec: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LocalQCResult:
    passed: bool
    probe: VideoProbe
    decodable: bool = True
    black_frames_detected: bool = False
    freeze_detected: bool = False
    duration_ok: bool = True
    resolution_ok: bool = True
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SemanticQCResult:
    passed: bool
    clipping: bool = False
    extra_fingers: bool = False
    product_drift: bool = False
    wrong_mechanics: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ImageQCResult:
    passed: bool
    width: int = 0
    height: int = 0
    variance: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_video(video: Path, *, ffprobe_path: str = "ffprobe") -> VideoProbe:
    if not video.exists() or video.stat().st_size <= 0:
        raise FileNotFoundError(f"Video is missing or empty: {video}")
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(video),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {video}: {completed.stderr[-1200:]}")
    data = json.loads(completed.stdout)
    video_stream = next(
        (item for item in data.get("streams", []) if item.get("codec_type") == "video"),
        None,
    )
    if not video_stream:
        raise RuntimeError(f"No video stream found: {video}")
    duration = float(
        (data.get("format") or {}).get("duration") or video_stream.get("duration") or 0
    )
    return VideoProbe(
        duration_sec=duration,
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        codec=str(video_stream.get("codec_name") or ""),
    )


def run_local_video_qc(
    video: Path,
    *,
    expected_duration_sec: int | float | None = None,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
) -> LocalQCResult:
    probe = probe_video(video, ffprobe_path=ffprobe_path)
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-v",
        "info",
        "-i",
        str(video),
        "-vf",
        "blackdetect=d=0.35:pix_th=0.05,freezedetect=n=-50dB:d=1.5",
        "-an",
        "-f",
        "null",
        "NUL",
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(120, int(probe.duration_sec * 4)),
        check=False,
    )
    stderr = completed.stderr or ""
    reasons: list[str] = []
    black = "black_start:" in stderr
    frozen = "freeze_start:" in stderr
    if completed.returncode != 0:
        reasons.append("ffmpeg decode failed")
    if black:
        reasons.append("black frame interval detected")
    if frozen:
        reasons.append("freeze interval detected")
    duration_ok = True
    if expected_duration_sec is not None:
        tolerance = max(1.5, float(expected_duration_sec) * 0.15)
        duration_ok = abs(probe.duration_sec - float(expected_duration_sec)) <= tolerance
        if not duration_ok:
            reasons.append(
                f"duration {probe.duration_sec:.3f}s differs from expected {float(expected_duration_sec):.3f}s"
            )
    resolution_ok = probe.width > 0 and probe.height > 0
    if not resolution_ok:
        reasons.append("invalid video resolution")
    return LocalQCResult(
        passed=completed.returncode == 0
        and not black
        and not frozen
        and duration_ok
        and resolution_ok,
        probe=probe,
        decodable=completed.returncode == 0,
        black_frames_detected=black,
        freeze_detected=frozen,
        duration_ok=duration_ok,
        resolution_ok=resolution_ok,
        reasons=reasons,
    )


def parse_semantic_qc(raw: str) -> SemanticQCResult:
    data = _extract_json(raw)
    if not data:
        raise ValueError("Semantic QC did not return JSON")
    result = SemanticQCResult(
        passed=bool(data.get("passed")),
        clipping=bool(data.get("clipping")),
        extra_fingers=bool(data.get("extra_fingers")),
        product_drift=bool(data.get("product_drift")),
        wrong_mechanics=bool(data.get("wrong_mechanics")),
        reason=str(data.get("reason") or "")[:1000],
    )
    if result.clipping or result.extra_fingers or result.product_drift or result.wrong_mechanics:
        result.passed = False
    return result


def run_local_image_qc(image_path: Path, *, min_side: int = 256) -> ImageQCResult:
    if not image_path.exists() or image_path.stat().st_size <= 0:
        return ImageQCResult(False, reason="image is missing or empty")
    try:
        with Image.open(image_path) as image:
            image.load()
            rgb = image.convert("RGB")
            variance = sum(ImageStat.Stat(rgb).var) / 3.0
            width, height = rgb.size
    except Exception as exc:  # noqa: BLE001 - image decoder boundary
        return ImageQCResult(False, reason=f"image decode failed: {exc}")
    reason = ""
    passed = min(width, height) >= min_side and variance >= 2.0
    if min(width, height) < min_side:
        reason = f"image too small: {width}x{height}"
    elif variance < 2.0:
        reason = "image is nearly blank"
    return ImageQCResult(
        passed, width=width, height=height, variance=round(variance, 3), reason=reason
    )


def build_semantic_qc_prompt(*, product_lock: str, mechanism: str) -> str:
    return (
        "Inspect this contact sheet against the product reference. Return compact JSON only: "
        '{"passed":bool,"clipping":bool,"extra_fingers":bool,"product_drift":bool,'
        '"wrong_mechanics":bool,"reason":"short"}. '
        f"Product lock: {product_lock[:700]}. Correct mechanism: {mechanism[:500]}. "
        "Fail any visible hand-object clipping, extra fingers, product identity/geometry drift, floating parts, "
        "or mechanically impossible movement. Uncertainty must fail for human review."
    )


def extract_tail_frame(video: Path, output: Path, *, ffmpeg_path: str = "ffmpeg") -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path,
        "-y",
        "-sseof",
        "-0.08",
        "-i",
        str(video),
        "-frames:v",
        "1",
        str(output),
    ]
    _run_ffmpeg(command, "tail-frame extraction")
    return output


def build_contact_sheet(
    video: Path,
    output: Path,
    *,
    fps: float = 2.0,
    columns: int = 6,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
) -> Path:
    probe = probe_video(video, ffprobe_path=ffprobe_path)
    frames = max(1, int(probe.duration_sec * fps + 0.999))
    rows = max(1, (frames + columns - 1) // columns)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(video),
        "-vf",
        f"fps={fps},scale=180:-1,tile={columns}x{rows}",
        "-frames:v",
        "1",
        str(output),
    ]
    _run_ffmpeg(command, "contact-sheet generation")
    return output


def stitch_segments(
    segments: list[Path],
    output: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> Path:
    if not segments:
        raise ValueError("No segments to stitch")
    missing = [str(path) for path in segments if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing stitch segments: {missing}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as file:
        concat_path = Path(file.name)
        for segment in segments:
            escaped = str(segment.resolve()).replace("'", "'\\''")
            file.write(f"file '{escaped}'\n")
    try:
        command = [
            ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output),
        ]
        _run_ffmpeg(command, "segment stitch", timeout=3600)
    finally:
        concat_path.unlink(missing_ok=True)
    return output


def _run_ffmpeg(command: list[str], operation: str, *, timeout: int = 600) -> None:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"FFmpeg {operation} failed: {(completed.stderr or '')[-1600:]}")


def _extract_json(raw: str) -> dict[str, Any] | None:
    raw = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw)
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    candidates = [fenced.group(1)] if fenced else []
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        for variant in (candidate, _collapse_json_string_newlines(candidate)):
            try:
                value = json.loads(variant)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return {re.sub(r"\s+", "", str(key)): item for key, item in value.items()}
    return None


def _collapse_json_string_newlines(value: str) -> str:
    output: list[str] = []
    in_string = False
    escaped = False
    for char in value:
        if escaped:
            output.append(char)
            escaped = False
            continue
        if char == "\\" and in_string:
            output.append(char)
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            output.append(char)
            continue
        if in_string and char in "\r\n":
            if not output or output[-1] != " ":
                output.append(" ")
            continue
        output.append(char)
    return "".join(output)
