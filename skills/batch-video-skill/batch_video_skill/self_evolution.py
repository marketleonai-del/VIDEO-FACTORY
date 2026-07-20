"""Offline feedback-driven self-evolution for batch video outputs.

This module deliberately avoids model calls and paid provider calls. It learns
only from artifacts that already exist: batch manifests, per-job state files,
prompt files, generated media paths, failure records, and optional human
feedback. The output is a report plus a candidate skill note for human review.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


GOOD_STATUS = {"completed"}
DRY_STATUS = {"dry_run", "skipped"}
BAD_STATUS = {"failed"}

PROMPT_POSITIVE_MARKERS = {
    "camera",
    "close-up",
    "close up",
    "lighting",
    "cinematic",
    "ugc",
    "handheld",
    "lifestyle",
    "product",
    "motion",
    "scene",
    "vertical",
}

PROMPT_RISK_MARKERS = {
    "watermark",
    "logo",
    "caption",
    "subtitle",
    "text overlay",
    "low quality",
}

COMMENT_PATTERNS = {
    "first_frame": ("first frame", "首帧", "首图", "封面", "reference image"),
    "product_relevance": ("off product", "not product", "不像产品", "产品不对", "跑题"),
    "visual_quality": ("blurry", "模糊", "low quality", "artifact", "畸变", "变形"),
    "text_artifacts": ("watermark", "caption", "subtitle", "文字", "水印", "logo"),
    "hook": ("boring", "hook", "开头", "前三秒", "吸引力", "痛点"),
    "provider": ("timeout", "api", "provider", "quota", "auth", "429", "超时", "鉴权"),
}


@dataclass(slots=True)
class FeedbackEntry:
    key: str
    rating: float | None = None
    approved: bool | None = None
    comment: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JobAssessment:
    key: str
    status: str
    product_name: str
    variant: str
    score: float
    hard_pass: bool
    reasons: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    feedback: FeedbackEntry | None = None
    manifest: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvolutionReport:
    output_dir: str
    total_jobs: int
    hard_pass_jobs: int
    average_score: float
    status_counts: dict[str, int]
    failure_patterns: dict[str, int]
    recommendation_counts: dict[str, int]
    assessments: list[JobAssessment]
    candidate_skill_path: str = ""
    report_path: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["assessments"] = [
            {
                **asdict(item),
                "feedback": asdict(item.feedback) if item.feedback else None,
            }
            for item in self.assessments
        ]
        return data


def analyze_batch_output(
    output_dir: str | Path,
    feedback_path: str | Path | None = None,
    *,
    write_candidate: bool = True,
) -> EvolutionReport:
    """Analyze a batch output folder and write an offline evolution report."""

    root = Path(output_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Batch output directory does not exist: {root}")

    rows = _load_manifest_rows(root)
    if not rows:
        raise FileNotFoundError(
            f"No manifest rows found under {root}. Expected batch_manifest.csv or batch_manifest.jsonl."
        )

    feedback = _load_feedback(feedback_path) if feedback_path else {}
    assessments = [_assess_row(root, row, feedback) for row in rows]

    status_counts = _count_by(assessments, lambda item: item.status or "unknown")
    failure_patterns = _collect_patterns(assessments)
    recommendation_counts = _collect_recommendations(assessments)

    total = len(assessments)
    hard_pass = sum(1 for item in assessments if item.hard_pass)
    average = round(sum(item.score for item in assessments) / total, 4) if total else 0.0

    report = EvolutionReport(
        output_dir=str(root),
        total_jobs=total,
        hard_pass_jobs=hard_pass,
        average_score=average,
        status_counts=status_counts,
        failure_patterns=failure_patterns,
        recommendation_counts=recommendation_counts,
        assessments=assessments,
    )

    out_dir = root / "self_evolution"
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if write_candidate:
        candidate = out_dir / f"candidate_skill_{timestamp}.md"
        candidate.write_text(_render_candidate_skill(report), encoding="utf-8")
        report.candidate_skill_path = str(candidate)

    report_path = out_dir / f"evolution_report_{timestamp}.json"
    report.report_path = str(report_path)
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    summary_path = out_dir / "latest_summary.md"
    summary_path.write_text(_render_summary(report), encoding="utf-8")

    return report


def _load_manifest_rows(root: Path) -> list[dict[str, Any]]:
    jsonl = root / "batch_manifest.jsonl"
    if jsonl.exists():
        rows: list[dict[str, Any]] = []
        with jsonl.open(encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    csv_path = root / "batch_manifest.csv"
    if csv_path.exists():
        with csv_path.open(encoding="utf-8-sig", newline="") as file:
            return [dict(row) for row in csv.DictReader(file)]

    return []


def _load_feedback(path: str | Path) -> dict[str, FeedbackEntry]:
    feedback_path = Path(path).expanduser().resolve()
    if not feedback_path.exists():
        raise FileNotFoundError(f"Feedback file does not exist: {feedback_path}")

    if feedback_path.suffix.lower() == ".jsonl":
        raw_rows = [
            json.loads(line)
            for line in feedback_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    elif feedback_path.suffix.lower() == ".json":
        data = json.loads(feedback_path.read_text(encoding="utf-8"))
        raw_rows = data if isinstance(data, list) else data.get("items", [])
    else:
        with feedback_path.open(encoding="utf-8-sig", newline="") as file:
            raw_rows = [dict(row) for row in csv.DictReader(file)]

    entries: dict[str, FeedbackEntry] = {}
    for row in raw_rows:
        normalized = {str(k).strip().lower(): v for k, v in dict(row).items()}
        key = _feedback_key(normalized)
        if not key:
            continue
        entry = FeedbackEntry(
            key=key,
            rating=_coerce_rating(normalized.get("rating") or normalized.get("score")),
            approved=_coerce_bool(
                normalized.get("approved")
                or normalized.get("accepted")
                or normalized.get("pass")
                or normalized.get("ok")
            ),
            comment=str(normalized.get("comment") or normalized.get("feedback") or "").strip(),
            raw=dict(row),
        )
        entries[key] = entry
    return entries


def _assess_row(
    root: Path,
    row: dict[str, Any],
    feedback_entries: dict[str, FeedbackEntry],
) -> JobAssessment:
    status = str(row.get("status") or "").strip().lower()
    key = _manifest_key(row)
    prompt_text = _read_optional_text(root, row.get("prompt_path"))
    gem_output_text = _read_optional_text(root, row.get("gem_output_path"))
    local_video = _resolve_optional_path(root, row.get("local_video"))
    first_frame = _resolve_optional_path(root, row.get("first_frame_path"))
    feedback = _find_feedback(row, feedback_entries)

    score = 0.0
    reasons: list[str] = []
    recommendations: list[str] = []

    if status in GOOD_STATUS:
        score += 0.45
        reasons.append("completed status")
    elif status in DRY_STATUS:
        score += 0.25
        reasons.append("dry-run or skipped status")
    elif status in BAD_STATUS:
        reasons.append("failed status")
        recommendations.append("Fix provider/config/runtime failure before changing creative rules.")
    else:
        score += 0.05
        reasons.append(f"unknown status: {status or 'empty'}")

    if local_video and local_video.exists() and local_video.stat().st_size > 0:
        score += 0.25
        reasons.append("local video exists")
    elif status == "completed":
        recommendations.append("Completed rows must include a playable local_video file.")

    if first_frame and first_frame.exists() and first_frame.stat().st_size > 0:
        score += 0.1
        reasons.append("first frame exists")
    elif row.get("first_frame_path") and status not in DRY_STATUS:
        recommendations.append("Improve first-frame generation/upload reliability.")

    prompt_score, prompt_reasons, prompt_recs = _score_prompt(prompt_text, gem_output_text)
    score += prompt_score * 0.2
    reasons.extend(prompt_reasons)
    recommendations.extend(prompt_recs)

    if feedback:
        feedback_score = _score_feedback(feedback)
        score = (score * 0.45) + (feedback_score * 0.55)
        reasons.append("human feedback applied")
        recommendations.extend(_recommend_from_comment(feedback.comment))

    error = str(row.get("error") or "").strip()
    if error:
        reasons.append(f"error: {_shorten(error, 160)}")
        recommendations.extend(_recommend_from_comment(error))

    score = round(max(0.0, min(1.0, score)), 4)
    hard_pass = score >= 0.72 and status in GOOD_STATUS.union(DRY_STATUS)

    return JobAssessment(
        key=key,
        status=status or "unknown",
        product_name=str(row.get("product_name") or ""),
        variant=str(row.get("variant") or ""),
        score=score,
        hard_pass=hard_pass,
        reasons=_unique(reasons),
        recommendations=_unique(recommendations),
        feedback=feedback,
        manifest=row,
    )


def _score_prompt(prompt_text: str, gem_output_text: str) -> tuple[float, list[str], list[str]]:
    text = (prompt_text or gem_output_text or "").strip()
    if not text:
        return 0.0, ["missing prompt text"], ["Ensure every row writes prompt and Gem output artifacts."]

    lower = text.lower()
    score = 0.0
    reasons: list[str] = []
    recs: list[str] = []

    if 80 <= len(text) <= 1800:
        score += 0.25
        reasons.append("prompt length in useful range")
    else:
        recs.append("Keep video prompts specific but bounded: roughly 80-1800 characters.")

    marker_hits = sum(1 for marker in PROMPT_POSITIVE_MARKERS if marker in lower)
    score += min(0.4, marker_hits * 0.05)
    if marker_hits < 4:
        recs.append("Add concrete subject, scene, camera motion, lighting, and platform style details.")
    else:
        reasons.append(f"prompt has {marker_hits} useful creative markers")

    risk_hits = [marker for marker in PROMPT_RISK_MARKERS if marker in lower]
    if risk_hits:
        # Negative prompts may intentionally include these terms. Penalize only
        # weakly, then ask for explicit negative-prompt separation.
        score += 0.1
        recs.append("Keep watermark/logo/caption constraints in negative_prompt, not the main visual prompt.")
    else:
        score += 0.2
        reasons.append("no obvious text/watermark risk markers")

    if re.search(r"\b(5|10|15)\s*(s|sec|second|seconds)\b", lower):
        score += 0.15
        reasons.append("duration is explicit")
    else:
        recs.append("Make duration explicit for provider compatibility.")

    return min(1.0, score), reasons, recs


def _score_feedback(entry: FeedbackEntry) -> float:
    if entry.approved is True and entry.rating is None:
        return 1.0
    if entry.approved is False and entry.rating is None:
        return 0.0
    if entry.rating is not None:
        return max(0.0, min(1.0, entry.rating / 5.0))
    return 0.5


def _recommend_from_comment(comment: str) -> list[str]:
    if not comment:
        return []
    lower = comment.lower()
    recs: list[str] = []
    for pattern_name, keywords in COMMENT_PATTERNS.items():
        if any(keyword in lower for keyword in keywords):
            if pattern_name == "first_frame":
                recs.append("Strengthen first-frame continuity with product reference and scene constraints.")
            elif pattern_name == "product_relevance":
                recs.append("Force product identity and visible use-case to stay central in every prompt.")
            elif pattern_name == "visual_quality":
                recs.append("Add anti-artifact, sharpness, natural-hand and realistic-material constraints.")
            elif pattern_name == "text_artifacts":
                recs.append("Ban captions, subtitles, watermarks, logos and random text in negative_prompt.")
            elif pattern_name == "hook":
                recs.append("Use a first-3-second pain/rescue hook before feature explanation.")
            elif pattern_name == "provider":
                recs.append("Treat provider/auth/quota/timeouts as runtime failures, not creative failures.")
    return recs


def _render_candidate_skill(report: EvolutionReport) -> str:
    top_recs = sorted(
        report.recommendation_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )[:12]
    failures = sorted(report.failure_patterns.items(), key=lambda item: (-item[1], item[0]))[:8]

    lines = [
        "# Feedback-Evolved Batch Video Skill Candidate",
        "",
        f"Created: {report.created_at}",
        f"Source output: `{report.output_dir}`",
        "",
        "## First Principles",
        "",
        "- Optimize verified generated-content quality, not raw generation volume.",
        "- Learn only from observed outputs, failure records, and human feedback.",
        "- Separate runtime/provider failures from creative prompt failures.",
        "- Keep every change reviewable; do not auto-activate candidate rules.",
        "",
        "## Current Evidence",
        "",
        f"- Jobs analyzed: {report.total_jobs}",
        f"- Hard-pass jobs: {report.hard_pass_jobs}",
        f"- Average offline score: {report.average_score:.3f}",
        f"- Status counts: `{json.dumps(report.status_counts, ensure_ascii=False)}`",
        "",
        "## Learned Rules To Consider",
        "",
    ]

    if top_recs:
        for rec, count in top_recs:
            lines.append(f"- ({count}x) {rec}")
    else:
        lines.append("- No recurring recommendation was found; keep the current skill unchanged.")

    lines.extend(["", "## Failure Patterns", ""])
    if failures:
        for name, count in failures:
            lines.append(f"- ({count}x) {name}")
    else:
        lines.append("- No recurring failure pattern was detected.")

    lines.extend(
        [
            "",
            "## Activation Gate",
            "",
            "- Do not activate this candidate unless the next dry-run or reviewed live batch improves average score.",
            "- If live providers were not called, treat this as prompt/process guidance only.",
            "- Preserve the previous skill version before applying any rule manually.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_summary(report: EvolutionReport) -> str:
    lines = [
        "# Self-Evolution Summary",
        "",
        f"- Output: `{report.output_dir}`",
        f"- Jobs: {report.total_jobs}",
        f"- Hard pass: {report.hard_pass_jobs}",
        f"- Average score: {report.average_score:.3f}",
        f"- Report: `{report.report_path}`",
    ]
    if report.candidate_skill_path:
        lines.append(f"- Candidate: `{report.candidate_skill_path}`")
    lines.append("")
    lines.append("## Top Recommendations")
    for rec, count in sorted(report.recommendation_counts.items(), key=lambda item: (-item[1], item[0]))[:8]:
        lines.append(f"- ({count}x) {rec}")
    if not report.recommendation_counts:
        lines.append("- No recurring recommendation was found.")
    return "\n".join(lines) + "\n"


def _manifest_key(row: dict[str, Any]) -> str:
    local_video = str(row.get("local_video") or "").strip()
    if local_video:
        return local_video
    prompt_path = str(row.get("prompt_path") or "").strip()
    if prompt_path:
        return prompt_path
    return f"{row.get('source_image', '')}|{row.get('variant', '')}"


def _feedback_key(row: dict[str, Any]) -> str:
    for field_name in ("job_id", "local_video", "video_path", "prompt_path"):
        value = str(row.get(field_name) or "").strip()
        if value:
            return value
    product = str(row.get("product_name") or row.get("product") or "").strip()
    variant = str(row.get("variant") or "").strip()
    source = str(row.get("source_image") or row.get("image") or "").strip()
    if source or product:
        return f"{source or product}|{variant}"
    return ""


def _find_feedback(
    row: dict[str, Any],
    feedback_entries: dict[str, FeedbackEntry],
) -> FeedbackEntry | None:
    keys = [
        str(row.get("local_video") or "").strip(),
        str(row.get("prompt_path") or "").strip(),
        f"{row.get('source_image', '')}|{row.get('variant', '')}",
        f"{row.get('product_name', '')}|{row.get('variant', '')}",
    ]
    for key in keys:
        if key and key in feedback_entries:
            return feedback_entries[key]
    return None


def _read_optional_text(root: Path, value: Any) -> str:
    path = _resolve_optional_path(root, value)
    if not path or not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _resolve_optional_path(root: Path, value: Any) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path


def _count_by(items: list[JobAssessment], fn: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(fn(item))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _collect_patterns(items: list[JobAssessment]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        if item.status in BAD_STATUS:
            counts["failed_status"] = counts.get("failed_status", 0) + 1
        error = str(item.manifest.get("error") or item.feedback.comment if item.feedback else item.manifest.get("error") or "")
        for pattern_name, keywords in COMMENT_PATTERNS.items():
            lower = error.lower()
            if any(keyword in lower for keyword in keywords):
                counts[pattern_name] = counts.get(pattern_name, 0) + 1
    return dict(sorted(counts.items()))


def _collect_recommendations(items: list[JobAssessment]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        for rec in item.recommendations:
            counts[rec] = counts.get(rec, 0) + 1
    return dict(sorted(counts.items()))


def _coerce_rating(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "pass", "ok", "approved", "是", "通过"}:
        return True
    if normalized in {"0", "false", "no", "n", "fail", "rejected", "否", "不通过"}:
        return False
    return None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _shorten(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
