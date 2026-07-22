"""Product-image to Gemini Gem routing.

This module keeps the routing layer local and deterministic. It does not call
Gemini, video providers, or paid vision APIs. Callers can pass a product image
path, a product name, or an image caption extracted by another tool; the router
uses those signals to select the best Gem from the local registry.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_REGISTRY_PATH = Path.home() / ".batch-video-skill" / "gem_registry.json"


@dataclass(slots=True)
class GemRouteCandidate:
    index: int
    title: str
    display_name: str
    gem_id: str
    gem_url: str
    score: float
    confidence: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GemRouteResult:
    source_text: str
    selected: GemRouteCandidate
    candidates: list[GemRouteCandidate]
    fallback_used: bool = False
    registry_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "selected": self.selected.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "fallback_used": self.fallback_used,
            "registry_path": self.registry_path,
        }


PRODUCT_ROUTE_KEYWORDS: dict[int, list[str]] = {
    28: ["美妆", "彩妆", "口红", "唇釉", "唇膏", "眼影", "睫毛膏", "眼线", "腮红", "粉底", "遮瑕", "makeup", "lipstick", "mascara", "eyeshadow", "blush", "foundation"],
    29: ["护肤", "面霜", "精华", "爽肤水", "乳液", "防晒", "面膜", "洁面", "洗面奶", "skincare", "serum", "cream", "lotion", "sunscreen", "cleanser"],
    30: ["假发", "接发", "发片", "发套", "wig", "hair extension", "hairpiece"],
    31: ["穿戴甲", "美甲", "甲片", "指甲", "nail", "manicure", "press on nails"],
    32: ["女装", "连衣裙", "裙子", "上衣", "衬衫", "外套", "裤子", "dress", "skirt", "blouse", "women clothing"],
    33: ["内衣", "塑身", "文胸", "胸罩", "bra", "shapewear", "underwear", "lingerie"],
    34: ["男装", "运动服", "卫衣", "夹克", "短裤", "跑步服", "menswear", "sportswear", "hoodie", "jacket"],
    35: ["鞋", "靴", "拖鞋", "运动鞋", "高跟鞋", "sneaker", "shoe", "boots", "sandals", "slippers"],
    36: ["珠宝", "饰品", "项链", "耳环", "戒指", "手链", "jewelry", "necklace", "earring", "ring", "bracelet"],
    37: ["手表", "腕表", "watch", "wristwatch"],
    38: ["手机壳", "手机膜", "充电器", "数据线", "支架", "phone case", "screen protector", "charger", "cable", "phone stand"],
    39: ["蓝牙耳机", "耳机", "音箱", "扬声器", "headphone", "earbud", "speaker", "bluetooth"],
    40: ["智能手表", "手环", "smart watch", "smartwatch", "fitness tracker", "band"],
    41: ["厨房小家电", "空气炸锅", "榨汁机", "破壁机", "咖啡机", "电饭煲", "blender", "air fryer", "juicer", "coffee maker"],
    42: ["清洁", "清理", "清洁剂", "去污", "除垢", "油污", "拖把", "刷子", "厨房清理剂", "cleaner", "cleaning", "degreaser", "stain remover", "mop", "brush"],
    43: ["收纳", "整理", "置物架", "收纳盒", "衣架", "storage", "organizer", "rack", "shelf"],
    44: ["床品", "四件套", "床单", "被套", "枕套", "被子", "bedding", "bedsheet", "duvet", "pillowcase"],
    45: ["窗帘", "地毯", "家纺", "毯子", "窗纱", "curtain", "rug", "carpet", "blanket", "textile"],
    46: ["刀具", "锅具", "菜刀", "平底锅", "炒锅", "锅", "knife", "cookware", "pan", "wok", "pot"],
    47: ["母婴", "婴儿", "奶瓶", "尿布", "推车", "baby", "mother", "bottle", "diaper", "stroller"],
    48: ["玩具", "儿童玩具", "积木", "娃娃", "拼图", "toy", "kids toy", "blocks", "puzzle", "doll"],
    49: ["宠物", "猫", "狗", "猫砂", "宠物窝", "pet", "cat", "dog", "litter", "pet bed"],
    50: ["车载", "汽车", "车用", "车载支架", "车载充电", "car", "auto", "vehicle", "car mount", "car charger"],
    51: ["五金", "工具", "螺丝刀", "扳手", "电钻", "钳子", "hardware", "tool", "screwdriver", "wrench", "drill", "pliers"],
    52: ["露营", "户外", "帐篷", "睡袋", "野餐", "camping", "outdoor", "tent", "sleeping bag", "picnic"],
    53: ["健身", "哑铃", "瑜伽", "拉力带", "跑步机", "fitness", "dumbbell", "yoga", "resistance band", "treadmill"],
    54: ["零食", "食品", "饼干", "糖果", "坚果", "饮料", "snack", "food", "cookie", "candy", "nuts", "drink"],
    55: ["个护", "剃须刀", "吹风机", "卷发棒", "牙刷", "按摩仪", "personal care", "shaver", "hair dryer", "toothbrush", "massager"],
    56: ["眼镜", "墨镜", "太阳镜", "镜框", "glasses", "sunglasses", "eyewear"],
    57: ["箱包", "包", "背包", "手提包", "行李箱", "钱包", "bag", "backpack", "handbag", "suitcase", "wallet"],
}


STYLE_ROUTE_KEYWORDS: dict[int, list[str]] = {
    1: ["女主播", "主播", "host", "presenter"],
    2: ["街头采访", "采访", "street interview"],
    3: ["教程", "教学", "讲解", "how to", "tutorial"],
    4: ["前后对比", "对比", "before after", "comparison"],
    5: ["开箱", "unboxing"],
    22: ["痛点", "救场", "促销", "pain rescue", "offer"],
    24: ["通用", "全类目", "general"],
}


def route_product_to_gem(
    *,
    image_path: str | Path | None = None,
    product_text: str = "",
    image_caption: str = "",
    registry_path: str | Path | None = None,
    top_k: int = 5,
) -> GemRouteResult:
    registry_file = Path(registry_path or DEFAULT_REGISTRY_PATH).expanduser()
    registry = load_registry(registry_file)
    source_text = build_source_text(image_path=image_path, product_text=product_text, image_caption=image_caption)
    candidates = score_registry(registry, source_text)
    fallback_used = False
    if not candidates:
        fallback = find_fallback(registry)
        candidates = [fallback]
        fallback_used = True
    candidates = sorted(candidates, key=lambda item: (-item.score, item.index, item.display_name))
    selected = candidates[0]
    return GemRouteResult(
        source_text=source_text,
        selected=selected,
        candidates=candidates[: max(1, top_k)],
        fallback_used=fallback_used or selected.index == 24 and selected.score <= 1.0,
        registry_path=str(registry_file),
    )


def load_registry(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Gem registry not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError(f"Gem registry must be a JSON list: {path}")
    return [item for item in data if isinstance(item, dict) and item.get("gem_id")]


def build_source_text(
    *,
    image_path: str | Path | None = None,
    product_text: str = "",
    image_caption: str = "",
) -> str:
    parts: list[str] = []
    if image_path:
        path = Path(image_path)
        parts.extend([path.name, path.stem])
        parts.extend(parent.name for parent in list(path.parents)[:3] if parent.name)
    if product_text:
        parts.append(product_text)
    if image_caption:
        parts.append(image_caption)
    return normalize_text(" ".join(parts))


def score_registry(registry: list[dict[str, Any]], source_text: str) -> list[GemRouteCandidate]:
    scored: list[GemRouteCandidate] = []
    for item in registry:
        index = _parse_int(item.get("index"))
        title = str(item.get("title") or "")
        display_name = str(item.get("display_name") or item.get("route_name") or title or item.get("gem_id"))
        aliases = [str(alias) for alias in (item.get("aliases") or []) if alias]
        reasons: list[str] = []
        score = 0.0

        for alias in [title, display_name, *aliases]:
            alias_norm = normalize_text(alias)
            if alias_norm and _contains(source_text, alias_norm):
                score += 3.0
                reasons.append(f"命中 Gem 名称/别名: {alias}")

        for keyword in PRODUCT_ROUTE_KEYWORDS.get(index, []):
            if _contains(source_text, normalize_text(keyword)):
                weight = 3.8 if _is_chinese(keyword) else 2.8
                score += weight
                reasons.append(f"命中产品品类关键词: {keyword}")

        for keyword in STYLE_ROUTE_KEYWORDS.get(index, []):
            if _contains(source_text, normalize_text(keyword)):
                score += 2.2
                reasons.append(f"命中拍法/模板关键词: {keyword}")

        if 28 <= index <= 57 and score > 0:
            score += 0.8
            reasons.append("优先使用 V2 品类 Gem")

        if score > 0:
            scored.append(
                GemRouteCandidate(
                    index=index,
                    title=title,
                    display_name=display_name,
                    gem_id=str(item.get("gem_id") or ""),
                    gem_url=str(item.get("gem_url") or ""),
                    score=round(score, 3),
                    confidence=_confidence(score),
                    reasons=reasons[:6],
                )
            )
    return scored


def find_fallback(registry: list[dict[str, Any]]) -> GemRouteCandidate:
    item = next((row for row in registry if _parse_int(row.get("index")) == 24), None)
    if item is None:
        item = next((row for row in registry if "通用" in str(row.get("display_name") or row.get("title") or "")), None)
    if item is None:
        if not registry:
            raise ValueError("Gem registry is empty")
        item = registry[0]
    return GemRouteCandidate(
        index=_parse_int(item.get("index")),
        title=str(item.get("title") or ""),
        display_name=str(item.get("display_name") or item.get("route_name") or item.get("title") or item.get("gem_id")),
        gem_id=str(item.get("gem_id") or ""),
        gem_url=str(item.get("gem_url") or ""),
        score=0.5,
        confidence="fallback",
        reasons=["未命中明确品类，使用全类目通用 Gem 兜底"],
    )


def normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _contains(source_text: str, keyword: str) -> bool:
    if not keyword:
        return False
    if _is_chinese(keyword):
        return keyword in source_text
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", source_text) is not None


def _is_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _parse_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        match = re.search(r"(\d+)", str(value or ""))
        return int(match.group(1)) if match else 0


def _confidence(score: float) -> str:
    if score >= 6.0:
        return "high"
    if score >= 3.0:
        return "medium"
    return "low"
