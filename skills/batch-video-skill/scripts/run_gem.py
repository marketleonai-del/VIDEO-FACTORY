#!/usr/bin/env python3
"""Route a product to the best gems, then run the chosen gem on a text model.

This replaces the manual "open Gemini, paste, copy the answer back" step.

    # 1) see which gems fit, without spending anything
    python scripts/run_gem.py --product "手摇刨冰机" --category 厨房 --market 墨西哥 --route-only

    # 2) run the top gem and write gem-raw-output.md for storyboard-build
    export GEM_TEXT_API_KEY=sk-...
    python scripts/run_gem.py --product "手摇刨冰机" --category 厨房 --market 墨西哥 \
        --duration 60 --mode storyboard --out ./job/gem-raw-output.md

    # 3) hand it to the existing pipeline (unchanged)
    batch-video-skill storyboard-build product.jpg --gem-output ./job/gem-raw-output.md ...
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
ROUTER = SKILL_ROOT / "router840" / "scripts" / "route.py"


def _load_local_gem_client():
    """Import the module without pulling the package's heavy optional deps."""
    pkg = types.ModuleType("_bvs")
    pkg.__path__ = [str(SKILL_ROOT / "batch_video_skill")]
    sys.modules["_bvs"] = pkg
    exc = types.ModuleType("_bvs.exceptions")

    class ConfigError(Exception):
        pass

    exc.ConfigError = ConfigError
    sys.modules["_bvs.exceptions"] = exc
    spec = importlib.util.spec_from_file_location(
        "_bvs.local_gem_client", SKILL_ROOT / "batch_video_skill" / "local_gem_client.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["_bvs.local_gem_client"] = module
    spec.loader.exec_module(module)
    return module


def route(desc: str, category: str, market: str, top_n: int) -> list[dict]:
    """Ask router840 for the best-matching gems (deterministic, offline).

    Returns the router's `picks` list; each entry carries template_id + name.
    """
    if not ROUTER.exists():
        return []
    cmd = [sys.executable, str(ROUTER), "--desc", desc, "--n", str(top_n), "--json"]
    if category:
        cmd += ["--category", category]
    if market:
        cmd += ["--country", market]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        print(f"[route] router840 unavailable ({proc.returncode}): {proc.stderr[:300]}",
              file=sys.stderr)
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"[route] unparsable router output: {proc.stdout[:200]}", file=sys.stderr)
        return []
    return data.get("picks") or []


def build_request(args) -> str:
    return (
        f"产品名：{args.product}\n"
        f"品类：{args.category}\n"
        f"目标国家/市场：{args.market}\n"
        f"视频总时长：{args.duration}秒（单段不超过15秒）\n"
        f"产品事实：{args.facts or '（未提供额外事实，按品类常识写，禁止编造具体数据）'}\n"
        "要求：严格按你的 Gem 指令与《万能引擎手册》执行；画面零文字；"
        "前 60% 零推销；产品首现时点按手册铁律。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--product", required=True)
    parser.add_argument("--category", default="")
    parser.add_argument("--market", required=True, help="目标国家/地区，手册铁律要求必填")
    parser.add_argument("--facts", default="", help="真实可核验的产品事实")
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--gem", default="", help="指定 gem 编号（如 U601）；不给则用路由第一名")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--route-only", action="store_true", help="只看路由结果，不调模型")
    parser.add_argument("--mode", choices=["json", "storyboard"], default="storyboard")
    parser.add_argument("--model", default="", help="覆盖模型名，如 deepseek-chat")
    parser.add_argument("--base-url", default="", help="覆盖 API 地址")
    parser.add_argument("--out", default="", help="输出文件；不给则打印到 stdout")
    parser.add_argument("--show-system-prompt", action="store_true")
    args = parser.parse_args()

    lgc = _load_local_gem_client()

    gem_code = args.gem.upper()
    if not gem_code:
        desc = " ".join(x for x in (args.product, args.category, args.facts) if x)
        ranked = route(desc, args.category, args.market, args.top_n)
        if ranked:
            print(f"路由结果（前 {len(ranked)} 名）：", file=sys.stderr)
            for item in ranked:
                print(
                    f"  #{item.get('rank')} {item.get('template_id')} "
                    f"{item.get('name', '')}  score={item.get('score', '')}",
                    file=sys.stderr,
                )
            gem_code = str(ranked[0].get("template_id", "")).upper()
        if args.route_only:
            return 0
        if not gem_code:
            print("路由不可用，请用 --gem 指定编号（如 --gem U601）", file=sys.stderr)
            return 2
    elif args.route_only:
        print(f"已指定 {gem_code}，无需路由", file=sys.stderr)
        return 0

    conf: dict = {"text_models": {}}
    if args.model:
        conf["text_models"]["model"] = args.model
    if args.base_url:
        conf["text_models"]["base_url"] = args.base_url

    client = lgc.make_local_gem_client(
        conf,
        gem_id=gem_code,
        output_mode=args.mode,
        allow_empty_key=args.show_system_prompt,  # preview needs no credentials
    )
    gem = client.library.resolve(gem_code)
    print(f"使用 Gem：{gem.code} {gem.name}（{gem.family}）｜知识库：{gem.manual}", file=sys.stderr)

    if args.show_system_prompt:
        print(client.preview_system_prompt(gem_code))
        return 0

    raw = client.generate_prompts(build_request(args), gem_id=gem_code)

    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(raw, encoding="utf-8")
        print(f"已写入 {out_path}（{len(raw)} 字符）", file=sys.stderr)
    else:
        print(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
