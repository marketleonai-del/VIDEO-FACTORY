from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = "StoryboardPlanV1"
DEFAULT_SHEET_SECONDS = 15

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CITE_RE = re.compile(r"\[\s*cite\s*:[^\]]+\]", re.IGNORECASE)
_SHOT_CANDIDATE_RE = re.compile(
    r"^\s*[•\-*]?\s*(?:分镜|镜头)\s*([一二三四五六七八九十百零〇两\d]+)\s*[:：]?"
)
_SHOT_RE = re.compile(
    r"^\s*[•\-*]?\s*(?:分镜|镜头)\s*([一二三四五六七八九十百零〇两\d]+)\s*[:：]?.*?"
    r"[（(]\s*(\d+(?:\.\d+)?)\s*秒?\s*[-—–~至]\s*(\d+(?:\.\d+)?)\s*秒?\s*[）)]"
)


@dataclass(frozen=True, slots=True)
class StoryboardSource:
    gem_id: str
    gem_name: str
    raw_response_path: str
    raw_sha256: str
    parser_version: str
    coverage: float


@dataclass(frozen=True, slots=True)
class StoryboardLocks:
    product: str = ""
    character: str = ""
    scene: str = ""
    light: str = ""
    style: str = "vertical realistic ecommerce UGC"
    aspect_ratio: str = "9:16"


@dataclass(frozen=True, slots=True)
class StoryboardShot:
    shot_id: str
    ordinal: int
    title: str
    start_sec: float
    end_sec: float
    visual: str
    audio: str
    dialogue: str
    camera: str
    primary_action: str
    physical_risk: Literal["low", "medium", "high"]
    source_lines: tuple[int, int]
    source_text_sha256: str
    raw_text: str

    @property
    def duration_sec(self) -> float:
        return round(self.end_sec - self.start_sec, 3)


@dataclass(frozen=True, slots=True)
class StoryboardSheet:
    sheet_id: str
    ordinal: int
    start_sec: float
    end_sec: float
    shot_ids: tuple[str, ...]
    end_state: str = ""

    @property
    def duration_sec(self) -> float:
        return round(self.end_sec - self.start_sec, 3)


@dataclass(slots=True)
class StoryboardPlan:
    source: StoryboardSource
    locks: StoryboardLocks
    shots: list[StoryboardShot]
    sheets: list[StoryboardSheet]
    duration_sec: float
    sheet_seconds: int = DEFAULT_SHEET_SECONDS
    schema_version: str = SCHEMA_VERSION
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def plan_sha256(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class StoryboardParseError(ValueError):
    """Raised when a Gem response cannot be traced into executable shots."""


def parse_storyboard_plan(
    raw_text: str,
    *,
    raw_path: Path,
    gem_id: str,
    gem_name: str,
    sheet_seconds: int = DEFAULT_SHEET_SECONDS,
) -> StoryboardPlan:
    if sheet_seconds < 1 or sheet_seconds > 15:
        raise ValueError("sheet_seconds must be between 1 and 15")
    if not raw_text.strip():
        raise StoryboardParseError("Gem output is empty")

    clean = _ANSI_RE.sub("", raw_text).replace("\r\n", "\n").replace("\r", "\n")
    lines = clean.splitlines()
    candidates = [index for index, line in enumerate(lines) if _SHOT_CANDIDATE_RE.search(line)]
    matches: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = _SHOT_RE.search(line)
        if match:
            matches.append((index, match))
    if not matches:
        raise StoryboardParseError("Gem output contains no timed storyboard shots")

    shots: list[StoryboardShot] = []
    for position, (line_index, match) in enumerate(matches):
        next_index = matches[position + 1][0] if position + 1 < len(matches) else len(lines)
        effective_end = _shot_block_end(lines, line_index, next_index)
        block_lines = lines[line_index:effective_end]
        raw_block = "\n".join(block_lines).rstrip()
        ordinal = _parse_ordinal(match.group(1), fallback=position + 1)
        start_sec = float(match.group(2))
        end_sec = float(match.group(3))
        if end_sec <= start_sec:
            raise StoryboardParseError(f"Shot {ordinal} has invalid timing: {start_sec}-{end_sec}")
        title_match = re.search(r"【([^】]+)】", lines[line_index])
        title = _clean_text(title_match.group(1) if title_match else f"分镜{ordinal}")
        visual = _extract_field(block_lines, ("画面要求", "画面", "视觉"))
        audio = _extract_field(block_lines, ("声音/ASMR", "声音", "音效", "ASMR"))
        dialogue = _extract_field(
            block_lines,
            ("墨西哥西语口播", "西语口播", "口播要求", "口播", "台词"),
        )
        if not visual:
            visual = _clean_text(" ".join(block_lines[1:]))
        camera = _camera_summary(visual)
        risk = _physical_risk(visual)
        shots.append(
            StoryboardShot(
                shot_id=f"shot-{ordinal:03d}",
                ordinal=ordinal,
                title=title,
                start_sec=start_sec,
                end_sec=end_sec,
                visual=visual,
                audio=audio,
                dialogue=dialogue,
                camera=camera,
                primary_action=_primary_action(visual),
                physical_risk=risk,
                source_lines=(line_index + 1, effective_end),
                source_text_sha256=hashlib.sha256(raw_block.encode("utf-8")).hexdigest(),
                raw_text=raw_block,
            )
        )

    _validate_shots(shots)
    coverage = round(len(matches) / max(1, len(candidates)), 4)
    if coverage < 1.0:
        raise StoryboardParseError(
            f"Timed shot coverage is incomplete: {len(matches)}/{len(candidates)}"
        )
    declared_duration = _declared_duration(clean)
    duration = max(shot.end_sec for shot in shots)
    warnings: list[str] = []
    if declared_duration and abs(declared_duration - duration) > 0.5:
        warnings.append(
            f"Declared duration {declared_duration:g}s differs from shot timeline {duration:g}s"
        )
    locks = StoryboardLocks(
        product=_extract_lock(lines, ("产品锁",)),
        character=_extract_lock(lines, ("人物 DNA 锁", "人物DNA锁", "人物锁")),
        scene=_extract_lock(lines, ("场景锚", "场景锁")),
        light=_extract_lock(lines, ("光线锁",)),
    )
    state_cards = _extract_state_cards(lines)
    sheets = _build_sheets(shots, state_cards, sheet_seconds)
    source = StoryboardSource(
        gem_id=gem_id,
        gem_name=gem_name,
        raw_response_path=str(raw_path.expanduser().resolve()),
        raw_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        parser_version="1.0",
        coverage=coverage,
    )
    return StoryboardPlan(
        source=source,
        locks=locks,
        shots=shots,
        sheets=sheets,
        duration_sec=duration,
        sheet_seconds=sheet_seconds,
        warnings=warnings,
    )


def write_storyboard_plan(plan: StoryboardPlan, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_storyboard_plan(path: Path) -> StoryboardPlan:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    source = StoryboardSource(**data["source"])
    locks = StoryboardLocks(**data["locks"])
    shots = [
        StoryboardShot(
            **{
                **item,
                "source_lines": tuple(item["source_lines"]),
            }
        )
        for item in data["shots"]
    ]
    sheets = [
        StoryboardSheet(
            **{
                **item,
                "shot_ids": tuple(item["shot_ids"]),
            }
        )
        for item in data["sheets"]
    ]
    return StoryboardPlan(
        source=source,
        locks=locks,
        shots=shots,
        sheets=sheets,
        duration_sec=float(data["duration_sec"]),
        sheet_seconds=int(data.get("sheet_seconds") or DEFAULT_SHEET_SECONDS),
        schema_version=str(data.get("schema_version") or SCHEMA_VERSION),
        warnings=list(data.get("warnings") or []),
    )


def build_panel_prompt(
    plan: StoryboardPlan,
    shot: StoryboardShot,
    *,
    product_name: str,
    market: str,
    frame_role: Literal["start", "end"] = "start",
) -> str:
    role = (
        "the physically valid state immediately before the action begins"
        if frame_role == "start"
        else "the physically valid state immediately after the action finishes"
    )
    prompt = (
        "Create one single clean frame for an ecommerce video storyboard, not a collage or layout. "
        "Vertical 9:16 realistic phone-camera UGC, full-bleed image. "
        f"Market: {market}. Product: {product_name}. Frame role: {role}. "
        f"PRODUCT LOCK: {plan.locks.product or 'match the supplied product reference exactly'}. "
        f"CHARACTER LOCK: {plan.locks.character or 'credible hands only when required by the shot'}. "
        f"SCENE LOCK: {plan.locks.scene or 'one stable lived-in home setting'}. "
        f"LIGHT LOCK: {plan.locks.light or 'natural continuous light'}. "
        f"EXACT GEM SHOT {shot.ordinal} ({shot.start_sec:g}-{shot.end_sec:g}s): {shot.visual}. "
        f"Primary action: {shot.primary_action}. Camera: {shot.camera}. "
        "Show one physically possible state with correct support, contact points, hand count and product geometry. "
        "No text, subtitles, labels, arrows, numbers, borders, split screen, grid, poster, UI, watermark, "
        "extra fingers, clipping, floating objects, redesigned parts, impossible mechanics or motion blur."
    )
    return prompt[:1800]


def _shot_block_end(lines: list[str], start: int, next_start: int) -> int:
    end = next_start
    for index in range(start + 1, next_start):
        value = lines[index]
        stripped = value.strip()
        if (
            "段末状态卡" in value
            or re.search(r"【第[一二三四五六七八九十]+段", value)
            or re.match(r"第[一二三四五六七八九十]+步[：:]", stripped)
            or (len(stripped) >= 12 and set(stripped) <= {"─", "-"})
        ):
            end = index
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def _extract_field(lines: list[str], labels: tuple[str, ...]) -> str:
    for index, line in enumerate(lines):
        normalized = line.strip().lstrip("•- ")
        label = next((item for item in labels if normalized.startswith(item)), None)
        if not label:
            continue
        value = normalized[len(label) :].lstrip("：: ")
        parts = [value] if value else []
        for continuation in lines[index + 1 :]:
            if re.match(r"^\s*[•\-*]\s*[^\s]", continuation):
                break
            if "段末状态卡" in continuation:
                break
            if continuation.strip():
                parts.append(continuation.strip())
        return _clean_text(" ".join(parts))
    return ""


def _extract_lock(lines: list[str], labels: tuple[str, ...]) -> str:
    for index, line in enumerate(lines):
        stripped = line.strip().lstrip("•- ")
        label = next((item for item in labels if stripped.startswith(item)), None)
        if not label:
            continue
        parts = [stripped[len(label) :].lstrip("：: ")]
        for continuation in lines[index + 1 :]:
            if re.match(r"^\s*[•\-*]\s*(?:人物|产品|场景|光线)", continuation):
                break
            if not continuation.strip() or set(continuation.strip()) <= {"─", "-"}:
                break
            parts.append(continuation.strip())
        return _clean_text(" ".join(parts))
    return ""


def _extract_state_cards(lines: list[str]) -> list[str]:
    cards: list[str] = []
    for index, line in enumerate(lines):
        if "段末状态卡" not in line:
            continue
        value = line.split("：", 1)[-1] if "：" in line else line
        parts = [value.strip("▌ ")]
        for continuation in lines[index + 1 :]:
            stripped = continuation.strip()
            if not stripped or set(stripped) <= {"─", "-"}:
                break
            if stripped.startswith("▌"):
                parts.append(stripped.strip("▌ "))
            else:
                break
        cards.append(_clean_text(" ".join(parts)))
    return cards


def _build_sheets(
    shots: list[StoryboardShot], state_cards: list[str], sheet_seconds: int
) -> list[StoryboardSheet]:
    groups: list[list[StoryboardShot]] = []
    current: list[StoryboardShot] = []
    for shot in sorted(shots, key=lambda item: (item.start_sec, item.ordinal)):
        if shot.duration_sec > sheet_seconds + 0.001:
            raise StoryboardParseError(
                f"Shot {shot.shot_id} exceeds storyboard sheet limit {sheet_seconds}s"
            )
        if current and shot.end_sec - current[0].start_sec > sheet_seconds + 0.001:
            groups.append(current)
            current = []
        current.append(shot)
    if current:
        groups.append(current)
    sheets: list[StoryboardSheet] = []
    for ordinal, group in enumerate(groups, start=1):
        start = min(item.start_sec for item in group)
        end = max(item.end_sec for item in group)
        if end - start > sheet_seconds + 0.001:
            raise StoryboardParseError(f"Storyboard sheet {ordinal} exceeds {sheet_seconds}s")
        sheets.append(
            StoryboardSheet(
                sheet_id=f"sheet-{ordinal:03d}",
                ordinal=ordinal,
                start_sec=start,
                end_sec=end,
                shot_ids=tuple(item.shot_id for item in group),
                end_state=state_cards[ordinal - 1] if ordinal <= len(state_cards) else "",
            )
        )
    return sheets


def _validate_shots(shots: list[StoryboardShot]) -> None:
    ordinals = [shot.ordinal for shot in shots]
    if len(ordinals) != len(set(ordinals)):
        raise StoryboardParseError("Duplicate storyboard shot numbers")
    ordered = sorted(shots, key=lambda item: (item.start_sec, item.ordinal))
    for previous, current in zip(ordered, ordered[1:]):
        if current.start_sec < previous.end_sec - 0.001:
            raise StoryboardParseError(
                f"Storyboard timing overlaps: {previous.shot_id} and {current.shot_id}"
            )


def _declared_duration(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*秒分镜脚本", text)
    return float(match.group(1)) if match else None


def _parse_ordinal(value: str, *, fallback: int) -> int:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        return tens * 10 + (digits.get(right, 0) if right else 0)
    return digits.get(value, fallback)


def _camera_summary(visual: str) -> str:
    tokens = re.findall(
        r"(?:第一视角\s*POV|POV|大远景|远景|全景|中景|近景|特写|微距|固定|跟拍|手持|推近|拉开)",
        visual,
        flags=re.IGNORECASE,
    )
    return " / ".join(dict.fromkeys(tokens)) or "stable medium shot"


def _primary_action(visual: str) -> str:
    first = re.split(r"[。；;]", visual, maxsplit=1)[0].strip()
    return first[:240] or "hold a stable observable state"


def _physical_risk(visual: str) -> Literal["low", "medium", "high"]:
    high = ("旋转", "转动", "摇动", "顺时针", "逆时针", "刀片", "拆", "盖", "锁", "倒", "淋", "冲洗", "水龙头")
    medium = ("抓", "拿", "放", "握", "伸", "打开", "舀", "撒", "按")
    if any(token in visual for token in high):
        return "high"
    if any(token in visual for token in medium):
        return "medium"
    return "low"


def _clean_text(value: str) -> str:
    value = _CITE_RE.sub("", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\n。")
