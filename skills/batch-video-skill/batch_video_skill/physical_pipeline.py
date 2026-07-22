from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = "CreativePlanV2"
MAX_GEM_REQUEST_CHARS = 1800
MAX_GEM_RESPONSE_CHARS = 6000
MAX_VIDEO_PROMPT_CHARS = 1200

PhysicalMode = Literal["strict", "balanced", "off"]
ProviderName = Literal["grok", "omni"]


class PhysicalPlanError(ValueError):
    """Raised when a creative plan cannot be executed without ambiguity."""


@dataclass(frozen=True, slots=True)
class ActionRule:
    name: str
    allowed_hands: frozenset[str]
    axis_tokens: frozenset[str]
    risk: str


ACTION_LIBRARY: dict[str, ActionRule] = {
    "grasp": ActionRule(
        "grasp", frozenset({"left", "right", "both"}), frozenset({"inward", "none"}), "medium"
    ),
    "place": ActionRule(
        "place",
        frozenset({"left", "right", "both"}),
        frozenset({"downward", "horizontal"}),
        "medium",
    ),
    "rotate": ActionRule(
        "rotate",
        frozenset({"left", "right", "both"}),
        frozenset({"clockwise", "counterclockwise", "vertical", "horizontal"}),
        "high",
    ),
    "pour": ActionRule(
        "pour",
        frozenset({"left", "right", "both"}),
        frozenset({"tilt", "downward", "diagonal"}),
        "high",
    ),
    "rinse": ActionRule(
        "rinse", frozenset({"left", "right", "both"}), frozenset({"downward", "stationary"}), "high"
    ),
    "press": ActionRule(
        "press", frozenset({"left", "right", "both"}), frozenset({"downward", "inward"}), "high"
    ),
    "slide": ActionRule(
        "slide",
        frozenset({"left", "right", "both"}),
        frozenset({"horizontal", "forward", "backward"}),
        "medium",
    ),
    "showcase": ActionRule(
        "showcase",
        frozenset({"none", "left", "right", "both"}),
        frozenset({"none", "stationary", "horizontal"}),
        "low",
    ),
}

ACTION_ALIASES = {
    "grab": "grasp",
    "hold": "grasp",
    "set down": "place",
    "turn": "rotate",
    "crank": "rotate",
    "wash": "rinse",
    "display": "showcase",
    "present": "showcase",
}

COMPOSITE_MARKERS = (",", ";", " then ", " and ", " after ", "并", "然后", "再")


@dataclass(slots=True)
class PhysicalSegment:
    segment_id: str
    start_state: str
    action: str
    hand_occupancy: str
    motion_axis: str
    end_state: str
    physical_risk: str
    visual_goal: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int) -> PhysicalSegment:
        return cls(
            segment_id=str(data.get("segment_id") or f"s{index:02d}"),
            start_state=_required(data, "start_state"),
            action=_required(data, "action"),
            hand_occupancy=str(data.get("hand_occupancy") or "none").strip().lower(),
            motion_axis=str(data.get("motion_axis") or "none").strip().lower(),
            end_state=_required(data, "end_state"),
            physical_risk=str(data.get("physical_risk") or "medium").strip().lower(),
            visual_goal=str(data.get("visual_goal") or "").strip(),
        )


@dataclass(slots=True)
class CreativePlanV2:
    product_lock: str
    mechanism: str
    scene_lock: str
    hook: str
    voiceover: str
    segments: list[PhysicalSegment]
    negative_constraints: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_compact_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @property
    def plan_hash(self) -> str:
        return hashlib.sha256(self.to_compact_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    provider: ProviderName
    model: str
    native_duration_sec: int
    supports_input_reference: bool
    default_resolution: str


PROVIDER_CAPABILITIES: dict[str, ProviderCapability] = {
    "grok": ProviderCapability("grok", "grok-imagine-video-cli", 15, True, "720p"),
    "omni": ProviderCapability("omni", "omni-fast", 10, True, "720p"),
}


@dataclass(slots=True)
class CompiledSegment:
    provider: str
    model: str
    segment_index: int
    source_segment_id: str
    duration_sec: int
    prompt: str
    physical_risk: str
    plan_hash: str
    start_state: str
    end_state: str
    task_id: str = ""
    input_reference: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReferenceStep:
    provider: str
    segment_index: int
    keyframe_name: str
    tail_name: str
    reference_names: tuple[str, ...]


def build_creative_plan_request(
    *,
    product_name: str,
    category: str,
    market: str,
    duration_sec: int,
    variant: int,
    source_name: str,
) -> str:
    schema = {
        "schema_version": SCHEMA_VERSION,
        "product_lock": "appearance, parts, proportions",
        "mechanism": "correct operation in one sentence",
        "scene_lock": "one stable real-life scene",
        "hook": "short hook",
        "voiceover": "market-language voiceover",
        "negative_constraints": ["no clipping", "no deformation", "no extra fingers"],
        "segments": [
            {
                "segment_id": "s01",
                "start_state": "observable state",
                "action": "one verb: grasp/place/rotate/pour/rinse/press/slide/showcase",
                "hand_occupancy": "none/left/right/both",
                "motion_axis": "single physical axis",
                "end_state": "observable state used by next segment",
                "physical_risk": "low/medium/high",
                "visual_goal": "one shot goal",
            }
        ],
    }
    request = (
        "Use this Gem template to return compact JSON only. Do not view or request the local image. "
        "Create one reusable ecommerce creative plan; Grok and Omni will share it. Each segment must "
        "contain exactly one main physical action. End state N must equal start state N+1. Keep contact "
        "shots fixed or with slight translation; never cut, zoom, or hide the mechanism during contact. "
        f"Product={product_name}; category={category}; market={market}; duration={duration_sec}s; "
        f"variant={variant}; source_filename={source_name}. Schema="
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )
    if len(request) > MAX_GEM_REQUEST_CHARS:
        raise ValueError(f"Gem request exceeds {MAX_GEM_REQUEST_CHARS} characters")
    return request


def parse_creative_plan(raw_output: str) -> CreativePlanV2:
    data = _extract_json_object(raw_output)
    if not data:
        raise PhysicalPlanError("Gem did not return a JSON object")
    if "segments" not in data and (data.get("video_prompt") or data.get("first_frame_prompt")):
        data = _convert_legacy_prompt(data)
    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise PhysicalPlanError("CreativePlanV2 requires at least one segment")
    negatives = data.get("negative_constraints") or []
    if isinstance(negatives, str):
        negatives = [part.strip() for part in re.split(r"[,;\n]", negatives) if part.strip()]
    plan = CreativePlanV2(
        schema_version=str(data.get("schema_version") or SCHEMA_VERSION),
        product_lock=_required(data, "product_lock"),
        mechanism=_required(data, "mechanism"),
        scene_lock=_required(data, "scene_lock"),
        hook=str(data.get("hook") or "").strip(),
        voiceover=str(data.get("voiceover") or "").strip(),
        negative_constraints=[str(item).strip() for item in negatives if str(item).strip()],
        segments=[
            PhysicalSegment.from_dict(item, index)
            for index, item in enumerate(raw_segments, 1)
            if isinstance(item, dict)
        ],
    )
    if not plan.segments:
        raise PhysicalPlanError("CreativePlanV2 contains no valid segments")
    if len(plan.to_compact_json()) > MAX_GEM_RESPONSE_CHARS:
        raise PhysicalPlanError(f"CreativePlanV2 exceeds {MAX_GEM_RESPONSE_CHARS} characters")
    return plan


def validate_creative_plan(plan: CreativePlanV2, *, mode: PhysicalMode = "strict") -> list[str]:
    if mode == "off":
        return []
    warnings: list[str] = []
    for index, segment in enumerate(plan.segments):
        action = _normalize_action(segment.action)
        if _is_composite_action(segment.action):
            message = f"segment {segment.segment_id} must contain one primary physical action"
            if mode == "strict":
                raise PhysicalPlanError(message)
            warnings.append(message)
        rule = ACTION_LIBRARY.get(action)
        if not rule:
            message = f"segment {segment.segment_id} uses unknown action: {segment.action}"
            if mode == "strict":
                raise PhysicalPlanError(message)
            warnings.append(message)
            continue
        segment.action = action
        if segment.hand_occupancy not in rule.allowed_hands:
            raise PhysicalPlanError(
                f"segment {segment.segment_id} invalid hand occupancy {segment.hand_occupancy} for {action}"
            )
        if not any(token in segment.motion_axis for token in rule.axis_tokens):
            message = f"segment {segment.segment_id} invalid motion axis {segment.motion_axis} for {action}"
            if mode == "strict":
                raise PhysicalPlanError(message)
            warnings.append(message)
        if segment.physical_risk not in {"low", "medium", "high"}:
            raise PhysicalPlanError(f"segment {segment.segment_id} has invalid physical_risk")
        if index:
            previous = plan.segments[index - 1]
            if _normalize_state(previous.end_state) != _normalize_state(segment.start_state):
                message = (
                    f"state continuity failed between {previous.segment_id} and {segment.segment_id}: "
                    f"'{previous.end_state}' != '{segment.start_state}'"
                )
                if mode == "strict":
                    raise PhysicalPlanError(message)
                warnings.append(message)
    return warnings


def compile_provider_segments(
    plan: CreativePlanV2,
    *,
    provider: ProviderName,
    total_duration_sec: int,
) -> list[CompiledSegment]:
    capability = PROVIDER_CAPABILITIES.get(provider)
    if not capability:
        raise ValueError(f"Unsupported V4 provider: {provider}")
    if total_duration_sec < 1:
        raise ValueError("total_duration_sec must be positive")
    count = math.ceil(total_duration_sec / capability.native_duration_sec)
    durations = [capability.native_duration_sec] * count
    remainder = total_duration_sec % capability.native_duration_sec
    if remainder:
        durations[-1] = remainder

    source_segments = list(plan.segments)
    while len(source_segments) < count:
        previous = source_segments[-1]
        source_segments.append(
            PhysicalSegment(
                segment_id=f"f{len(source_segments) + 1:02d}",
                start_state=previous.end_state,
                action="showcase",
                hand_occupancy="none",
                motion_axis="stationary",
                end_state=previous.end_state,
                physical_risk="low",
                visual_goal="stable no-contact detail shot that preserves product geometry",
            )
        )

    compiled: list[CompiledSegment] = []
    for index in range(count):
        segment = source_segments[min(index, len(source_segments) - 1)]
        prompt = _compile_segment_prompt(plan, segment, provider)
        compiled.append(
            CompiledSegment(
                provider=provider,
                model=capability.model,
                segment_index=index + 1,
                source_segment_id=segment.segment_id,
                duration_sec=durations[index],
                prompt=prompt,
                physical_risk=segment.physical_risk,
                plan_hash=plan.plan_hash,
                start_state=segment.start_state,
                end_state=segment.end_state,
            )
        )
    return compiled


def build_reference_chain(
    segments: list[CompiledSegment], reference_dir: Path
) -> list[ReferenceStep]:
    del reference_dir  # Names stay relative so batch folders remain portable.
    steps: list[ReferenceStep] = []
    for segment in segments:
        references = ["canonical_product.png"]
        if segment.segment_index > 1:
            references.append(f"segment-{segment.segment_index - 1:03d}-tail.png")
        steps.append(
            ReferenceStep(
                provider=segment.provider,
                segment_index=segment.segment_index,
                keyframe_name=f"{segment.provider}-segment-{segment.segment_index:03d}-start.png",
                tail_name=f"segment-{segment.segment_index:03d}-tail.png",
                reference_names=tuple(references),
            )
        )
    return steps


def build_prompt_cache_key(
    *,
    image_sha256: str,
    gem_id: str,
    market: str,
    duration_sec: int,
    variant: int,
    request: str = "",
    schema_version: str = SCHEMA_VERSION,
) -> str:
    normalized_request = " ".join(request.split())
    value = {
        "image_sha256": image_sha256,
        "gem_id": gem_id,
        "market": market,
        "duration_sec": duration_sec,
        "variant": variant,
        "request_sha256": hashlib.sha256(normalized_request.encode("utf-8")).hexdigest(),
        "schema_version": schema_version,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class CreativePlanCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def get_or_create(self, key: str, producer: Callable[[], str]) -> tuple[CreativePlanV2, bool]:
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            return parse_creative_plan(path.read_text(encoding="utf-8")), True
        plan = parse_creative_plan(producer())
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(plan.to_compact_json(), encoding="utf-8")
        temp.replace(path)
        return plan, False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compile_segment_prompt(plan: CreativePlanV2, segment: PhysicalSegment, provider: str) -> str:
    negative = ", ".join(plan.negative_constraints[:8])
    prompt = (
        f"Vertical 9:16 realistic phone UGC. Provider={provider}. PRODUCT LOCK: {plan.product_lock}. "
        f"MECHANISM: {plan.mechanism}. SCENE LOCK: {plan.scene_lock}. START: {segment.start_state}. "
        f"ONE ACTION ONLY: {segment.action}; hands={segment.hand_occupancy}; axis={segment.motion_axis}. "
        f"END: {segment.end_state}. GOAL: {segment.visual_goal}. Keep the camera fixed during contact; "
        "no cut, zoom, occlusion, object teleportation, geometry drift, hand-object clipping, extra fingers, "
        f"or impossible mechanics. NEGATIVE: {negative}."
    )
    if len(prompt) > MAX_VIDEO_PROMPT_CHARS:
        prompt = prompt[: MAX_VIDEO_PROMPT_CHARS - 1].rstrip() + "."
    return prompt


def _normalize_action(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip().lower())
    if normalized in ACTION_LIBRARY:
        return normalized
    return ACTION_ALIASES.get(normalized, normalized)


def _is_composite_action(value: str) -> bool:
    lowered = f" {value.strip().lower()} "
    if any(marker in lowered for marker in COMPOSITE_MARKERS):
        return True
    action_words = set(ACTION_LIBRARY) | set(ACTION_ALIASES)
    matches = {word for word in action_words if re.search(rf"\b{re.escape(word)}\b", lowered)}
    return len(matches) > 1


def _normalize_state(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.lower()).strip()


def _required(data: dict[str, Any], field_name: str) -> str:
    value = str(data.get(field_name) or "").strip()
    if not value:
        raise PhysicalPlanError(f"CreativePlanV2 missing {field_name}")
    return value


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
    candidates = [fenced.group(1)] if fenced else []
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _convert_legacy_prompt(data: dict[str, Any]) -> dict[str, Any]:
    video_prompt = str(data.get("video_prompt") or "stable product showcase")
    return {
        "schema_version": SCHEMA_VERSION,
        "product_lock": str(data.get("product_lock") or "preserve the referenced product exactly"),
        "mechanism": str(
            data.get("mechanism") or "show only one simple, physically valid operation"
        ),
        "scene_lock": str(
            data.get("style_notes") or data.get("first_frame_prompt") or "real home UGC scene"
        ),
        "hook": "",
        "voiceover": "",
        "negative_constraints": data.get("negative_prompt") or "no clipping, no deformation",
        "segments": [
            {
                "segment_id": "s01",
                "start_state": "product stable and fully supported",
                "action": "showcase",
                "hand_occupancy": "none",
                "motion_axis": "stationary",
                "end_state": "product stable and fully supported",
                "physical_risk": "low",
                "visual_goal": video_prompt[:500],
            }
        ],
    }
