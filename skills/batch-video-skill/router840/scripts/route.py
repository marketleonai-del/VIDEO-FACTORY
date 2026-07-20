# -*- coding: utf-8 -*-
"""Route one product profile to an exact Top-N set of ecommerce video Gems.

The default path is deterministic and offline: domain/category rules, BM25,
audience and need matching, then quality-gated MMR. Optional embeddings are a
small reranking signal and are never required.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
INDEX = HERE.parent / "data" / "templates_index.json"
CONFIG = HERE.parent / "data" / "router_config.json"
REGISTRY = HERE.parent / "data" / "gem_registry.json"
ROUTER_VERSION = "3.0.0"

COHORTS = {
    "all",
    "legacy",
    "decision",
    "emotion",
    "sensory",
    "ecosystem",
    "category-deep",
    "audience-mindset",
    "scene-timing",
}

GENERIC_CATEGORY_TERMS = {
    "家电",
    "工具",
    "日用",
    "食品",
    "家居",
    "数码",
    "清洁",
    "用品",
    "好物",
    "商品",
}


def _load_json(path: os.PathLike[str] | str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_index(path: os.PathLike[str] | str = INDEX) -> list[dict[str, Any]]:
    rows = _load_json(path)
    if not isinstance(rows, list):
        raise ValueError("templates_index.json must contain a JSON array")
    return rows


def load_config(path: os.PathLike[str] | str = CONFIG) -> dict[str, Any]:
    config = _load_json(path)
    weights = config.get("weights", {})
    if abs(sum(float(v) for v in weights.values()) - 1.0) > 1e-6:
        raise ValueError("router weights must sum to 1.0")
    return config


def load_registry(path: os.PathLike[str] | str = REGISTRY) -> dict[str, Any]:
    if not Path(path).exists():
        return {"templates": {}}
    data = _load_json(path)
    if "templates" not in data:
        raise ValueError("gem_registry.json is missing 'templates'")
    return data


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", " ", text).strip()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_normalize(item) for item in value if _normalize(item)]
    text = _normalize(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"[,，;；|]", text) if part.strip()]


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _split_terms(value: Any) -> list[str]:
    text = _normalize(value)
    if not text:
        return []
    raw = re.split(r"[/、,，;；|｜()（）\[\]【】:+×\s]+", text)
    return [item for item in raw if len(item) >= 2]


def _cjk_ngrams(text: str, sizes: tuple[int, ...] = (2, 3)) -> set[str]:
    grams: set[str] = set()
    for chunk in re.findall(r"[一-鿿]+", _normalize(text)):
        for size in sizes:
            if len(chunk) < size:
                continue
            grams.update(chunk[i : i + size] for i in range(len(chunk) - size + 1))
    return grams


def _dice(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def _keyword_hits(text: str, mapping: dict[str, list[str]]) -> set[str]:
    normalized = _normalize(text)
    return {
        label
        for label, keywords in mapping.items()
        if any(_normalize(keyword) in normalized for keyword in keywords)
    }


def _domain_hits(text: str, config: dict[str, Any]) -> set[str]:
    return _keyword_hits(text, config.get("domain_groups", {}))


def _lexical_tokens(text: str, config: dict[str, Any]) -> list[str]:
    normalized = _normalize(text)
    tokens: list[str] = re.findall(r"[a-z][a-z0-9_-]*|\d+(?:\.\d+)?", normalized)
    tokens.extend(_cjk_ngrams(normalized, (2, 3)))
    tokens.extend(f"domain:{item}" for item in sorted(_domain_hits(normalized, config)))
    tokens.extend(
        f"need:{item}"
        for item in sorted(_keyword_hits(normalized, config.get("need_keywords", {})))
    )
    return tokens


def _bm25_scores(
    rows: list[dict[str, Any]], query_text: str, config: dict[str, Any]
) -> dict[str, float]:
    query = _lexical_tokens(query_text, config)
    if not query:
        return {row["id"]: 0.0 for row in rows}

    documents: list[list[str]] = []
    frequencies: list[Counter[str]] = []
    document_frequency: Counter[str] = Counter()
    for row in rows:
        category = f"{row.get('category', '')} {row.get('fit', '')}"
        text = " ".join(
            [
                row.get("name", ""),
                row.get("profile", ""),
                category,
                category,
                row.get("carrier", ""),
                row.get("angle", ""),
                row.get("demographic", ""),
                " ".join(row.get("effect_tags", [])),
            ]
        )
        tokens = _lexical_tokens(text, config)
        freq = Counter(tokens)
        documents.append(tokens)
        frequencies.append(freq)
        document_frequency.update(freq.keys())

    count = len(rows)
    average_length = sum(len(doc) for doc in documents) / max(count, 1)
    k1, b = 1.5, 0.72
    raw_scores: list[float] = []
    for tokens, freq in zip(documents, frequencies):
        score = 0.0
        length = len(tokens)
        for term in set(query):
            tf = freq.get(term, 0)
            if not tf:
                continue
            df = document_frequency.get(term, 0)
            idf = math.log(1.0 + (count - df + 0.5) / (df + 0.5))
            denominator = tf + k1 * (1.0 - b + b * length / max(average_length, 1.0))
            score += idf * tf * (k1 + 1.0) / denominator
        raw_scores.append(score)

    maximum = max(raw_scores, default=0.0)
    if maximum <= 0:
        return {row["id"]: 0.0 for row in rows}
    return {
        row["id"]: round(raw / maximum * 100.0, 6)
        for row, raw in zip(rows, raw_scores)
    }


def infer_needs(text: str, config: dict[str, Any]) -> list[str]:
    return sorted(_keyword_hits(text, config.get("need_keywords", {})))


def infer_demographic(audience: Any, description: str, config: dict[str, Any]) -> str:
    audience_text = _normalize(_flatten_text(audience))
    description_text = _normalize(description)
    demographics = config.get("demographics", {})
    for source in (audience_text, description_text):
        if not source:
            continue
        for label, aliases in demographics.items():
            if label.lower() in source or any(_normalize(alias) in source for alias in aliases):
                return label
    return ""


def infer_visual_modes(text: str, config: dict[str, Any]) -> list[str]:
    return sorted(_keyword_hits(text, config.get("visual_modes", {})))


def infer_decision_tags(text: str, config: dict[str, Any]) -> list[str]:
    return sorted(_keyword_hits(text, config.get("decision_keywords", {})))


def infer_scenario_tags(text: str, config: dict[str, Any]) -> list[str]:
    return sorted(_keyword_hits(text, config.get("scenario_keywords", {})))


def infer_content_goal_tags(text: str, config: dict[str, Any]) -> list[str]:
    return sorted(_keyword_hits(text, config.get("content_goal_keywords", {})))


def _merge_profile(
    product_profile: dict[str, Any] | None,
    *,
    desc: str,
    category: str,
    audience: Any,
    country: str,
    platform: str,
    price: float | None,
    need: Any,
    want_carrier: Any,
    want_angle: Any,
) -> dict[str, Any]:
    profile = dict(product_profile or {})
    if desc:
        profile["description"] = desc
    if category:
        profile["category"] = category
    if audience:
        profile["target_audience"] = audience
    if country:
        profile["country"] = country
    if platform:
        profile["platform"] = platform
    if price is not None:
        profile["price"] = price
    if need:
        profile["need_tags"] = _as_list(need)
    if want_carrier:
        profile["desired_carriers"] = _as_list(want_carrier)
    if want_angle:
        profile["desired_angles"] = _as_list(want_angle)
    return profile


def _make_context(profile: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    category_parts = [
        profile.get("product_name", ""),
        profile.get("category", ""),
        profile.get("subcategory", ""),
    ]
    category_text = " ".join(_flatten_text(part) for part in category_parts if part)
    description = _flatten_text(profile.get("description", ""))
    audience = profile.get("target_audience", profile.get("audience", ""))
    selling_text = " ".join(
        _flatten_text(profile.get(key, ""))
        for key in (
            "selling_points",
            "pain_points",
            "usage_scenes",
            "visual_proofs",
            "constraints",
        )
    )
    routing_intent_text = " ".join(
        _flatten_text(profile.get(key, ""))
        for key in ("decision_stage", "content_goal", "scenario", "emotional_goal")
    )
    full_text = " ".join(
        [category_text, description, _flatten_text(audience), selling_text, routing_intent_text]
    )
    explicit_needs = _as_list(profile.get("need_tags"))
    need_tags = explicit_needs or infer_needs(full_text, config)
    explicit_visual = _as_list(profile.get("visual_modes"))
    visual_modes = explicit_visual or infer_visual_modes(
        f"{selling_text} {description}", config
    )
    decision_tags = _as_list(profile.get("decision_tags")) or infer_decision_tags(full_text, config)
    scenario_tags = _as_list(profile.get("scenario_tags")) or infer_scenario_tags(full_text, config)
    content_goal_tags = _as_list(profile.get("content_goal_tags")) or infer_content_goal_tags(full_text, config)
    platform = _normalize(profile.get("platform", ""))
    platform_preferences = config.get("platform_preferences", {}).get(platform, [])
    try:
        parsed_price = float(profile["price"]) if profile.get("price") not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ValueError("price must be numeric") from exc

    return {
        "profile": profile,
        "category_text": category_text,
        "category_terms": _split_terms(category_text),
        "has_category": bool(_normalize(category_text)),
        "domains": _domain_hits(category_text, config),
        "full_text": full_text,
        "need_tags": need_tags,
        "visual_modes": visual_modes,
        "decision_tags": decision_tags,
        "scenario_tags": scenario_tags,
        "content_goal_tags": content_goal_tags,
        "demographic": infer_demographic(audience, description, config),
        "has_audience": bool(_normalize(_flatten_text(audience))),
        "platform": platform,
        "platform_preferences": platform_preferences,
        "country": _normalize(profile.get("country", "")),
        "price": parsed_price,
        "desired_carriers": _as_list(profile.get("desired_carriers")),
        "desired_angles": _as_list(profile.get("desired_angles")),
    }


def _template_category(row: dict[str, Any]) -> tuple[str, str]:
    if _normalize(row.get("category", "")):
        return str(row["category"]), str(row.get("category_specificity", "specific"))
    if row.get("category_specificity"):
        return "", str(row.get("category_specificity", "generic"))
    template_id = str(row.get("id", ""))
    if re.fullmatch(r"U(?:20[1-9]|2\d\d|3\d\d|400)", template_id) and _normalize(row.get("fit", "")):
        return str(row["fit"]), "broad"
    return "", "generic"


def _direct_category_match(query_terms: list[str], template_text: str) -> str:
    normalized_template = _normalize(template_text)
    for term in sorted(query_terms, key=len, reverse=True):
        if len(term) >= 2 and term in normalized_template:
            return term
    return ""


def _category_score(
    row: dict[str, Any], context: dict[str, Any], config: dict[str, Any]
) -> tuple[float, list[str], dict[str, Any]]:
    template_text, specificity = _template_category(row)
    if not context["has_category"]:
        return 50.0, ["未提供商品类目，类目按中性处理"], {
            "specificity": specificity,
            "domains": sorted(_domain_hits(template_text, config)),
            "exact": "",
        }
    if not template_text:
        return 55.0, ["通用模板，无类目硬限制"], {
            "specificity": specificity,
            "domains": [],
            "exact": "",
        }

    exact = _direct_category_match(context["category_terms"], template_text)
    query_domains = context["domains"]
    template_domains = _domain_hits(template_text, config)
    overlap = query_domains & template_domains
    lexical = _dice(
        _cjk_ngrams(context["category_text"]), _cjk_ngrams(template_text)
    )
    reasons: list[str] = []

    if exact:
        score = 100.0
        reasons.append(f"类目直接命中:{exact}")
    elif overlap:
        coverage = len(overlap) / max(len(query_domains), 1)
        score = 68.0 + 22.0 * coverage + min(8.0, lexical * 20.0)
        if specificity == "broad":
            score = min(score, 86.0)
        elif specificity == "specific":
            extra_domains = template_domains - query_domains
            score -= min(18.0, len(extra_domains) * 9.0)
            # Sharing a broad domain (for example "baby") is not enough for a
            # product-specific template such as a hair clipper to match a night light.
            if row.get("cohort") != "category-deep" and lexical < 0.12:
                score = min(score, 45.0)
            elif row.get("cohort") != "category-deep" and lexical < 0.22:
                score = min(score, 65.0)
        reasons.append("同类目域:" + "/".join(sorted(overlap)))
    else:
        score = max(5.0, min(52.0, lexical * 100.0))
        if specificity == "specific" and lexical < 0.2:
            score = min(score, 18.0)
        reasons.append(f"类目词面相似:{lexical:.2f}")

    return max(0.0, min(100.0, score)), reasons, {
        "specificity": specificity,
        "domains": sorted(template_domains),
        "exact": exact,
    }


def _audience_score(row: dict[str, Any], context: dict[str, Any]) -> tuple[float, list[str]]:
    audience_tags = {_normalize(item) for item in _as_list(row.get("audience_tags"))}
    template_demo = _normalize(row.get("demographic", ""))
    if template_demo:
        audience_tags.add(template_demo)
    query_demo = _normalize(context["demographic"])
    if not query_demo:
        return (55.0 if not audience_tags else 45.0), ["人群未限定"]
    if not audience_tags:
        return 62.0, ["通用人群模板"]
    if query_demo in audience_tags:
        return 100.0, [f"人群精准:{context['demographic']}"]
    return 15.0, [f"人群不一致:{'/'.join(sorted(audience_tags))}"]


def _need_score(row: dict[str, Any], context: dict[str, Any]) -> tuple[float, list[str]]:
    query = set(context["need_tags"])
    template = set(row.get("effect_tags", []))
    if not query:
        return 50.0, ["需求标签未限定"]
    if not template:
        return 40.0, ["模板无显式效果标签"]
    overlap = query & template
    if not overlap:
        return 25.0, ["需求标签无直接交集"]
    score = 50.0 + 50.0 * len(overlap) / len(query)
    return min(100.0, score), ["需求匹配:" + "/".join(sorted(overlap))]


def _visual_score(
    row: dict[str, Any], context: dict[str, Any], config: dict[str, Any]
) -> tuple[float, list[str]]:
    query = set(context["visual_modes"])
    template_text = " ".join(
        [
            row.get("name", ""),
            row.get("carrier", ""),
            row.get("angle", ""),
            row.get("profile", ""),
        ]
    )
    template = _keyword_hits(template_text, config.get("visual_modes", {}))
    if not query:
        return 50.0, ["可视化证据未限定"]
    if not template:
        return 35.0, ["模板可视化机制较弱"]
    overlap = query & template
    if not overlap:
        return 25.0, ["可视化机制不一致"]
    score = 55.0 + 45.0 * len(overlap) / len(query)
    return min(100.0, score), ["可视化匹配:" + "/".join(sorted(overlap))]


def _platform_score(row: dict[str, Any], context: dict[str, Any]) -> tuple[float, list[str]]:
    preferences = set(context["platform_preferences"])
    if not preferences:
        return 50.0, ["平台未限定或无偏好表"]
    tags = set(row.get("effect_tags", []))
    overlap = preferences & tags
    if overlap:
        score = 80.0 + min(20.0, 10.0 * (len(overlap) - 1))
        return score, ["平台机制契合:" + "/".join(sorted(overlap))]
    if not tags:
        return 45.0, ["模板平台机制中性"]
    return 30.0, ["平台偏好无直接交集"]


def _price_score(row: dict[str, Any], context: dict[str, Any]) -> tuple[float, list[str]]:
    price = context["price"]
    if price is None:
        return 50.0, ["未提供客单价"]
    low, high = row.get("price", [0, 100000])
    low, high = float(low), float(high)
    if low <= price <= high:
        return 100.0, [f"客单价命中:{low:g}-{high:g}"]
    distance = low - price if price < low else price - high
    scale = max(high - low, low, 100.0)
    score = max(15.0, 75.0 - 60.0 * distance / scale)
    return score, [f"客单价偏离:{low:g}-{high:g}"]


def _requested_match(requested: list[str], actual: str) -> float:
    if not requested:
        return 50.0
    normalized_actual = _normalize(actual)
    return 100.0 if any(item in normalized_actual or normalized_actual in item for item in requested) else 10.0


def _style_score(row: dict[str, Any], context: dict[str, Any]) -> tuple[float, list[str]]:
    carrier_score = _requested_match(context["desired_carriers"], row.get("carrier", ""))
    angle_score = _requested_match(context["desired_angles"], row.get("angle", ""))
    if not context["desired_carriers"] and not context["desired_angles"]:
        return 50.0, ["未指定载体/角度"]
    matched: list[str] = []
    if carrier_score == 100.0:
        matched.append("载体")
    if angle_score == 100.0:
        matched.append("角度")
    return (carrier_score + angle_score) / 2.0, [
        "指定风格匹配:" + ("/".join(matched) if matched else "无")
    ]


def _family_fit_score(
    row: dict[str, Any],
    context: dict[str, Any],
    config: dict[str, Any],
    *,
    category_score: float,
    audience_score: float,
    need_score: float,
    visual_score: float,
    lexical_score: float,
) -> tuple[float, list[str]]:
    cohort = str(row.get("cohort", "legacy"))
    if cohort == "legacy":
        return 55.0, ["传统模板族按通用基线"]

    if cohort == "decision":
        query = set(context["decision_tags"])
        template = set(_as_list(row.get("decision_tags")))
        if not query:
            return 50.0, ["未限定购买决策阶段"]
        overlap = query & template
        if overlap:
            base = min(100.0, 72.0 + 14.0 * len(overlap))
            return 0.65 * base + 0.35 * lexical_score, ["决策阶段匹配:" + "/".join(sorted(overlap))]
        return 28.0, ["决策阶段无直接交集"]

    if cohort == "emotion":
        query = set(context["need_tags"])
        template = set(row.get("effect_tags", []))
        if not query:
            return 48.0, ["未限定情绪目标"]
        overlap = query & template
        if overlap:
            return 0.65 * need_score + 0.35 * lexical_score, ["情绪机制匹配:" + "/".join(sorted(overlap))]
        return 35.0, ["情绪机制无直接交集"]

    if cohort == "sensory":
        return 0.65 * visual_score + 0.35 * lexical_score, ["感官节奏结合视觉证据与语义适配"]

    if cohort == "ecosystem":
        query = set(context["content_goal_tags"])
        template = set(_as_list(row.get("content_goal_tags")))
        if not query:
            return 12.0, ["未提出评论/合拍/系列/分发/矩阵目标"]
        overlap = query & template
        if overlap:
            base = min(100.0, 82.0 + 9.0 * len(overlap))
            return 0.7 * base + 0.3 * lexical_score, ["平台生态目标匹配:" + "/".join(sorted(overlap))]
        return 20.0, ["平台生态目标不一致"]

    if cohort == "category-deep":
        return 0.45 * category_score + 0.55 * lexical_score, ["品类深挖结合类目与模板语义适配"]

    if cohort == "audience-mindset":
        if not context["has_audience"]:
            return 25.0, ["人群心智模板缺少目标人群输入"]
        return (
            0.45 * audience_score + 0.35 * lexical_score + 0.2 * need_score,
            ["人群心智结合受众、痛点与模板语义适配"],
        )

    if cohort == "scene-timing":
        query = set(context["scenario_tags"])
        template = set(_as_list(row.get("scenario_tags")))
        if not query:
            return 18.0, ["未提供季节/节点/日内时机场景"]
        overlap = query & template
        if overlap:
            base = min(100.0, 82.0 + 9.0 * len(overlap))
            return 0.65 * base + 0.35 * lexical_score, ["场景时机匹配:" + "/".join(sorted(overlap))]
        return 20.0, ["场景时机不一致"]

    return 50.0, [f"未知模板族:{cohort}"]


def _unfit_conflict(row: dict[str, Any], context: dict[str, Any]) -> str:
    unfit = _normalize(row.get("unfit", ""))
    if not unfit:
        return ""
    for term in sorted(context["category_terms"], key=len, reverse=True):
        if len(term) < 3 or term in GENERIC_CATEGORY_TERMS:
            continue
        if term in unfit:
            return term
    return ""


def _confidence(score: float) -> str:
    if score >= 78:
        return "high"
    if score >= 62:
        return "medium"
    if score >= 45:
        return "low"
    return "weak"


def _score_template(
    row: dict[str, Any],
    context: dict[str, Any],
    config: dict[str, Any],
    lexical_score: float,
) -> dict[str, Any] | None:
    conflict = _unfit_conflict(row, context)
    if conflict:
        return None

    category, category_reasons, category_meta = _category_score(row, context, config)
    raw_category = category
    if row.get("cohort") == "category-deep" and category >= 25 and not category_meta["exact"]:
        category = 0.55 * category + 0.45 * lexical_score
        category_reasons.append("品类深挖已融合模板语义精度")
    audience, audience_reasons = _audience_score(row, context)
    need, need_reasons = _need_score(row, context)
    visual, visual_reasons = _visual_score(row, context, config)
    platform, platform_reasons = _platform_score(row, context)
    price, price_reasons = _price_score(row, context)
    style, style_reasons = _style_score(row, context)
    family_fit, family_reasons = _family_fit_score(
        row,
        context,
        config,
        category_score=category,
        audience_score=audience,
        need_score=need,
        visual_score=visual,
        lexical_score=lexical_score,
    )

    breakdown = {
        "category": category,
        "audience": audience,
        "need": need,
        "visual": visual,
        "lexical": lexical_score,
        "platform": platform,
        "price": price,
        "style": style,
        "family_fit": family_fit,
    }
    weights = config["weights"]
    score = sum(breakdown[key] * float(weights[key]) for key in breakdown)
    warnings: list[str] = []
    if context["has_category"] and category_meta["specificity"] == "specific":
        if category < 25:
            score = min(35.0, score - 20.0)
            warnings.append("具体类目冲突，已硬降权")
        elif category < 42:
            score -= 10.0
            warnings.append("具体类目匹配偏弱")
    if context["has_audience"] and row.get("demographic") and audience < 20:
        score -= 4.0
        warnings.append("目标人群不一致")
    cohort = str(row.get("cohort", "legacy"))
    if cohort == "category-deep" and context["has_category"] and raw_category < 25:
        score = min(score, 35.0)
        warnings.append("品类深挖模板与商品类目冲突，已硬降权")
    elif cohort == "category-deep" and raw_category >= 65 and lexical_score >= 95:
        # A narrow tie-break for an exact deep-category intent.  It is applied
        # after the weighted score so the documented V3 weights stay intact.
        score += 1.0
        category_reasons.append("品类深挖语义精确命中")
    if cohort == "audience-mindset" and context["has_audience"] and audience < 20:
        score = min(score, 35.0)
        warnings.append("人群心智模板与目标人群冲突，已硬降权")
    if cohort == "ecosystem" and not context["content_goal_tags"]:
        score = min(score, 40.0)
        warnings.append("未提出平台运营目标，生态模板不进入头部")
    if cohort == "scene-timing" and not context["scenario_tags"]:
        score = min(score, 45.0)
        warnings.append("未提供明确时机，场景模板不进入头部")
    score = max(0.0, min(100.0, score))

    reason_groups = [
        (category * weights["category"], category_reasons),
        (audience * weights["audience"], audience_reasons),
        (need * weights["need"], need_reasons),
        (visual * weights["visual"], visual_reasons),
        (platform * weights["platform"], platform_reasons),
        (price * weights["price"], price_reasons),
        (style * weights["style"], style_reasons),
        (family_fit * weights["family_fit"], family_reasons),
    ]
    reason_groups.sort(key=lambda item: item[0], reverse=True)
    reasons = [reason for _, group in reason_groups[:5] for reason in group]

    return {
        "template": row,
        "relevance_score": score,
        "breakdown": {key: round(value, 2) for key, value in breakdown.items()},
        "reasons": reasons,
        "warnings": warnings,
        "template_domains": category_meta["domains"],
    }


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    left, right = set(a), set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _template_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    a, b = left["template"], right["template"]
    carrier = 1.0 if _normalize(a.get("carrier")) == _normalize(b.get("carrier")) else 0.0
    angle = 1.0 if _normalize(a.get("angle")) == _normalize(b.get("angle")) else 0.0
    demographic = (
        1.0
        if a.get("demographic")
        and _normalize(a.get("demographic")) == _normalize(b.get("demographic"))
        else 0.0
    )
    tags = _jaccard(a.get("effect_tags", []), b.get("effect_tags", []))
    domains = _jaccard(left.get("template_domains", []), right.get("template_domains", []))
    cohort = 1.0 if a.get("cohort") and a.get("cohort") == b.get("cohort") else 0.0
    family = 1.0 if a.get("family") and a.get("family") == b.get("family") else 0.0
    return (
        0.2 * carrier
        + 0.15 * angle
        + 0.1 * demographic
        + 0.2 * tags
        + 0.2 * domains
        + 0.1 * cohort
        + 0.05 * family
    )


def mmr_select(
    candidates: list[dict[str, Any]],
    n: int,
    *,
    lam: float = 0.82,
    quality_floor: float = 45.0,
    relevance_window: float = 1.5,
) -> list[dict[str, Any]]:
    if n < 1:
        raise ValueError("n must be >= 1")
    if not 0.0 <= lam <= 1.0:
        raise ValueError("lambda must be between 0 and 1")
    if relevance_window < 0:
        raise ValueError("relevance_window must be >= 0")
    if len(candidates) < n:
        raise ValueError(f"requested Top-{n}, but only {len(candidates)} eligible templates exist")

    ordered = sorted(
        candidates,
        key=lambda item: (-item["relevance_score"], item["template"]["id"]),
    )
    pool = list(ordered)
    selected = [pool.pop(0)]
    selected[0]["selection_score"] = selected[0]["relevance_score"]
    max_similarity = {
        id(item): _template_similarity(item, selected[0]) for item in pool
    }
    while len(selected) < n:
        best_remaining = pool[0]["relevance_score"]
        band_floor = best_remaining - relevance_window
        choice_pool = [item for item in pool if item["relevance_score"] >= band_floor]
        best_item: dict[str, Any] | None = None
        best_mmr = -1.0
        for item in choice_pool:
            similarity = max_similarity[id(item)]
            mmr = lam * (item["relevance_score"] / 100.0) + (1.0 - lam) * (1.0 - similarity)
            tie_key = (mmr, item["relevance_score"], item["template"]["id"])
            if best_item is None or tie_key > (
                best_mmr,
                best_item["relevance_score"],
                best_item["template"]["id"],
            ):
                best_item = item
                best_mmr = mmr
        assert best_item is not None
        pool.remove(best_item)
        best_item["selection_score"] = best_mmr * 100.0
        if best_item["relevance_score"] < quality_floor:
            best_item["warnings"].append(
                f"为满足精确 Top-{n}，该项低于质量优选线 {quality_floor:.1f}"
            )
        selected.append(best_item)
        max_similarity.pop(id(best_item), None)
        for item in pool:
            key = id(item)
            max_similarity[key] = max(
                max_similarity[key],
                _template_similarity(item, best_item),
            )
    return selected


def _embedding_rerank(
    candidates: list[dict[str, Any]], query_text: str, *, blend: float = 0.12
) -> None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
    texts = [query_text] + [item["template"].get("profile", "") for item in candidates]
    request = urllib.request.Request(
        base.rstrip("/") + "/embeddings",
        data=json.dumps({"model": model, "input": texts}).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.loads(response.read())
    vectors = [item["embedding"] for item in payload["data"]]
    query = vectors[0]

    def cosine(a: list[float], b: list[float]) -> float:
        numerator = sum(x * y for x, y in zip(a, b))
        denominator = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
        return numerator / max(denominator, 1e-12)

    for item, vector in zip(candidates, vectors[1:]):
        semantic = max(0.0, min(1.0, cosine(query, vector))) * 100.0
        item["breakdown"]["embedding"] = round(semantic, 2)
        item["relevance_score"] = (
            (1.0 - blend) * item["relevance_score"] + blend * semantic
        )
        item["reasons"].append(f"可选 embedding 语义:{semantic:.1f}")


def route(
    desc: str = "",
    category: str = "",
    audience: Any = "",
    country: str = "",
    platform: str = "",
    price: float | None = None,
    need: Any = "",
    want_carrier: Any = "",
    want_angle: Any = "",
    n: int = 3,
    series: str = "both",
    cohort: str = "all",
    lam: float = 0.82,
    index_path: os.PathLike[str] | str = INDEX,
    config_path: os.PathLike[str] | str = CONFIG,
    registry_path: os.PathLike[str] | str = REGISTRY,
    product_profile: dict[str, Any] | None = None,
    quality_floor: float = 45.0,
    relevance_window: float = 1.5,
    embed: bool = False,
) -> dict[str, Any]:
    if n < 1:
        raise ValueError("n must be >= 1")
    series = series.upper() if series.lower() != "both" else "both"
    if series not in {"both", "G", "U"}:
        raise ValueError("series must be one of: both, G, U")
    cohort = cohort.lower()
    if cohort not in COHORTS:
        raise ValueError("cohort must be one of: " + ", ".join(sorted(COHORTS)))

    config = load_config(config_path)
    rows = load_index(index_path)
    if series in {"G", "U"}:
        rows = [row for row in rows if row.get("series") == series]
    if cohort != "all":
        rows = [row for row in rows if row.get("cohort", "legacy") == cohort]
    if n > len(rows):
        raise ValueError(f"requested Top-{n}, but selected series contains only {len(rows)} templates")

    profile = _merge_profile(
        product_profile,
        desc=desc,
        category=category,
        audience=audience,
        country=country,
        platform=platform,
        price=price,
        need=need,
        want_carrier=want_carrier,
        want_angle=want_angle,
    )
    context = _make_context(profile, config)
    query_text = " ".join(
        [
            context["category_text"],
            context["category_text"],
            context["full_text"],
            " ".join(context["need_tags"]),
            " ".join(context["visual_modes"]),
            " ".join(context["decision_tags"]),
            " ".join(context["scenario_tags"]),
            " ".join(context["content_goal_tags"]),
        ]
    )
    lexical = _bm25_scores(rows, query_text, config)
    candidates = [
        scored
        for row in rows
        if (scored := _score_template(row, context, config, lexical.get(row["id"], 0.0)))
        is not None
    ]
    if len(candidates) < n:
        raise ValueError(
            f"requested Top-{n}, but only {len(candidates)} templates remain after unfit filters"
        )
    if embed:
        try:
            _embedding_rerank(candidates, query_text)
        except Exception as exc:  # optional enhancement must not break offline routing
            print(f"[embedding skipped] {exc}", file=sys.stderr)

    selected = mmr_select(
        candidates,
        n,
        lam=lam,
        quality_floor=quality_floor,
        relevance_window=relevance_window,
    )
    registry = load_registry(registry_path)
    registrations = registry.get("templates", {})
    picks: list[dict[str, Any]] = []
    for rank, item in enumerate(selected, 1):
        template = item["template"]
        registration = registrations.get(template["id"], {})
        warnings = list(item["warnings"])
        if not registration.get("gem_id"):
            warnings.append("Gem ID 未登记，调用前需同步 gem_registry.json")
        picks.append(
            {
                "rank": rank,
                "template_id": template["id"],
                "series": template.get("series", ""),
                "cohort": template.get("cohort", "legacy"),
                "family": template.get("family", ""),
                "subgroup": template.get("subgroup", ""),
                "name": template.get("name", ""),
                "carrier": template.get("carrier", ""),
                "angle": template.get("angle", ""),
                "demographic": template.get("demographic", ""),
                "category": template.get("category", ""),
                "fit": template.get("fit", ""),
                "effect_tags": template.get("effect_tags", []),
                "match_score": round(item["relevance_score"], 2),
                "selection_score": round(item.get("selection_score", item["relevance_score"]), 2),
                "confidence": _confidence(item["relevance_score"]),
                "score_breakdown": item["breakdown"],
                "family_fit_score": item["breakdown"].get("family_fit", 0.0),
                "matched_signals": item["reasons"],
                "reasons": item["reasons"],
                "warnings": warnings,
                "gem_id": registration.get("gem_id", ""),
                "gem_name": registration.get("gem_name", ""),
                "gem_verified": bool(registration.get("verified", False)),
            }
        )

    return {
        "route_version": ROUTER_VERSION,
        "query": {
            "product_profile": profile,
            "detected": {
                "domains": sorted(context["domains"]),
                "need_tags": context["need_tags"],
                "visual_modes": context["visual_modes"],
                "demographic": context["demographic"],
                "platform_preferences": context["platform_preferences"],
                "decision_tags": context["decision_tags"],
                "scenario_tags": context["scenario_tags"],
                "content_goal_tags": context["content_goal_tags"],
            },
            "requested_n": n,
            "series": series,
            "cohort": cohort,
            "lambda": lam,
            "quality_floor": quality_floor,
            "relevance_window": relevance_window,
        },
        "candidate_count": len(candidates),
        "returned_n": len(picks),
        "picks": picks,
    }


def _format_result(result: dict[str, Any]) -> str:
    query = result["query"]
    detected = query["detected"]
    lines = [
        f"【路由结果 V{result['route_version']}】精确 Top-{query['requested_n']} / 候选 {result['candidate_count']}",
        f"识别: 类目域={detected['domains']} 人群={detected['demographic'] or '未限定'} 需求={detected['need_tags']} 可视化={detected['visual_modes']}",
        "",
    ]
    for pick in result["picks"]:
        gem = pick["gem_id"] or "未登记"
        lines.append(
            f"{pick['rank']}. [{pick['template_id']}] {pick['name']}  族={pick['cohort']}  匹配={pick['match_score']:.2f}%  置信={pick['confidence']}  Gem={gem}"
        )
        lines.append(f"   载体/角度: {pick['carrier']} / {pick['angle']}")
        lines.append(f"   理由: {'; '.join(pick['reasons'])}")
        if pick["warnings"]:
            lines.append(f"   警告: {'; '.join(pick['warnings'])}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Route a product to an exact Top-N set of the best matching Gemini Gems."
    )
    parser.add_argument("--profile", help="Product profile JSON created by the host AI")
    parser.add_argument("--desc", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--audience", default="")
    parser.add_argument("--country", default="")
    parser.add_argument("--platform", default="")
    parser.add_argument("--price", type=float)
    parser.add_argument("--need", default="")
    parser.add_argument("--carrier", default="")
    parser.add_argument("--angle", default="")
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--series", choices=["both", "G", "U"], default="both")
    parser.add_argument(
        "--cohort",
        choices=sorted(COHORTS),
        default="all",
        help="Optional template-family filter; default searches all 840 templates",
    )
    parser.add_argument("--lambda", dest="lam", type=float, default=0.82)
    parser.add_argument("--quality-floor", type=float, default=45.0)
    parser.add_argument(
        "--relevance-window",
        type=float,
        default=1.5,
        help="MMR may diversify only within this many relevance points of the current best",
    )
    parser.add_argument("--index", default=str(INDEX))
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--registry", default=str(REGISTRY))
    parser.add_argument("--embed", action="store_true", help="Optional API embedding rerank")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", help="Write the same result to this file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    profile = _load_json(args.profile) if args.profile else None
    result = route(
        desc=args.desc,
        category=args.category,
        audience=args.audience,
        country=args.country,
        platform=args.platform,
        price=args.price,
        need=args.need,
        want_carrier=args.carrier,
        want_angle=args.angle,
        n=args.n,
        series=args.series,
        cohort=args.cohort,
        lam=args.lam,
        index_path=args.index,
        config_path=args.config,
        registry_path=args.registry,
        product_profile=profile,
        quality_floor=args.quality_floor,
        relevance_window=args.relevance_window,
        embed=args.embed,
    )
    rendered = (
        json.dumps(result, ensure_ascii=False, indent=2)
        if args.json
        else _format_result(result)
    )
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
