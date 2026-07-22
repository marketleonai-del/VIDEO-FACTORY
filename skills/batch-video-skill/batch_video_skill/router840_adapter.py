from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

ROUTER_ROOT = Path(__file__).resolve().parents[1] / "router840"
ROUTE_SCRIPT = ROUTER_ROOT / "scripts" / "route.py"
INDEX_PATH = ROUTER_ROOT / "data" / "templates_index.json"
CONFIG_PATH = ROUTER_ROOT / "data" / "router_config.json"
REGISTRY_PATH = ROUTER_ROOT / "data" / "gem_registry.json"

_MODULE: ModuleType | None = None


def route_product_840(
    *,
    product_name: str,
    category: str,
    country: str,
    duration_sec: int,
    n: int,
    description: str = "",
    target_audience: list[str] | None = None,
    selling_points: list[str] | None = None,
    pain_points: list[str] | None = None,
    usage_scenes: list[str] | None = None,
    visual_proofs: list[str] | None = None,
    platform: str = "TikTok",
) -> dict[str, Any]:
    module = _load_router_module()
    enriched = _enrich_product_signals(product_name, category, description)
    profile = {
        "product_name": product_name,
        "category": enriched["category"],
        "subcategory": "",
        "description": enriched["description"],
        "target_audience": target_audience or ["general ecommerce shoppers"],
        "selling_points": selling_points or enriched["selling_points"],
        "pain_points": pain_points or enriched["pain_points"],
        "usage_scenes": usage_scenes or enriched["usage_scenes"],
        "visual_proofs": visual_proofs or enriched["visual_proofs"],
        "need_tags": enriched["need_tags"],
        "visual_modes": enriched["visual_modes"],
        "decision_stage": "conversion",
        "decision_tags": enriched["decision_tags"],
        "emotional_goal": "relief",
        "scenario": "daily use",
        "scenario_tags": ["daily use"],
        "content_goal": "ecommerce conversion",
        "content_goal_tags": enriched["content_goal_tags"],
        "desired_carriers": enriched["desired_carriers"],
        "desired_angles": enriched["desired_angles"],
        "constraints": ["one physical action per segment", "no product deformation"],
        "country": country,
        "platform": platform,
        "price": None,
        "duration_seconds": duration_sec,
        "aspect_ratio": "9:16",
        "allow_product_image_first_frame": False,
    }
    result = module.route(
        product_profile=profile,
        n=n,
        series="both",
        cohort="all",
        index_path=INDEX_PATH,
        config_path=CONFIG_PATH,
        registry_path=REGISTRY_PATH,
    )
    picks = result.get("picks") or []
    if len(picks) != n:
        raise RuntimeError(f"840 Gem router returned {len(picks)} picks for requested Top-{n}")
    if len({pick.get("gem_id") for pick in picks}) != n:
        raise RuntimeError("840 Gem router returned duplicate or missing Gem IDs")
    return result


def validate_bundled_registry() -> dict[str, Any]:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    templates = registry.get("templates") or {}
    ids = [str(item.get("id") or "") for item in index]
    missing = [
        template_id for template_id in ids if not (templates.get(template_id) or {}).get("gem_id")
    ]
    return {
        "index_count": len(index),
        "registry_count": len(templates),
        "unique_index_ids": len(set(ids)),
        "missing_gem_ids": missing,
        "ok": len(index) == 840 and len(templates) == 840 and len(set(ids)) == 840 and not missing,
    }


def _load_router_module() -> ModuleType:
    global _MODULE
    if _MODULE is not None:
        return _MODULE
    if not ROUTE_SCRIPT.exists():
        raise FileNotFoundError(f"Bundled 840 Gem router is missing: {ROUTE_SCRIPT}")
    spec = importlib.util.spec_from_file_location("batch_video_skill_router840", ROUTE_SCRIPT)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load bundled 840 Gem router: {ROUTE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULE = module
    return module


def _enrich_product_signals(product_name: str, category: str, description: str) -> dict[str, Any]:
    source = f"{product_name} {category} {description}".lower()
    operational = any(
        token in source
        for token in (
            "maker",
            "machine",
            "appliance",
            "tool",
            "cleaner",
            "grinder",
            "crank",
            "kitchen",
            "刨冰",
            "机器",
            "小家电",
            "工具",
            "清洁",
            "手摇",
        )
    )
    kitchen = any(
        token in source for token in ("kitchen", "shaved ice", "shaved-ice", "刨冰", "厨房")
    )
    if operational:
        category_terms = "操作简单工具"
        need_tags = ["操作演示", "一看就会", "效果直观", "正确操作"]
        visual_modes = ["操作", "过程展示", "因果证明"]
        carriers = ["第一视角", "操作演示"]
        angles = ["懒人", "一看就会"]
        selling = ["操作简单", "结果直观", "正确机械过程"]
        pains = ["怕不会用", "怕操作复杂"]
        proofs = ["完整操作过程", "手部接触点", "可见因果", "正确运动轴"]
    else:
        category_terms = "实物商品"
        need_tags = ["产品展示", "效果直观", "转化"]
        visual_modes = ["产品细节", "使用场景", "结果展示"]
        carriers = []
        angles = []
        selling = ["核心卖点可视化"]
        pains = ["购买前不确定效果"]
        proofs = ["产品细节", "真实使用结果"]
    if kitchen:
        category_terms += " 厨房神器 厨房小家电 手动刨冰机"
        pains.extend(["怕买后吃灰", "怕清洗麻烦"])
    return {
        "category": f"{category} {category_terms}".strip(),
        "description": f"{description or product_name}。真实商品，突出正确操作，不使用布幕、合拍或无关转场。",
        "selling_points": selling,
        "pain_points": pains,
        "usage_scenes": ["真实家庭使用", "厨房台面操作"] if kitchen else ["真实日常使用"],
        "visual_proofs": proofs,
        "need_tags": need_tags,
        "visual_modes": visual_modes,
        "decision_tags": ["comparison", "objection"] if kitchen else [],
        "content_goal_tags": ["demonstration", "conversion"],
        "desired_carriers": carriers,
        "desired_angles": angles,
    }
