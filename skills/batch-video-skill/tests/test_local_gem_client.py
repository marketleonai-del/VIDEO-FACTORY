"""End-to-end check of the local gem backend against a mock chat API.

Proves the whole hand-off works without touching a paid endpoint:
  gem text index -> system prompt -> chat call -> JSON contract -> prompts.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[1]
INDEX = REPO_ROOT / "gems" / "index" / "gem_texts.jsonl"


def _load_module():
    """Import local_gem_client standalone (package __init__ pulls heavy deps)."""
    pkg = types.ModuleType("bvs_test")
    pkg.__path__ = [str(SKILL_ROOT / "batch_video_skill")]
    sys.modules["bvs_test"] = pkg
    exc = types.ModuleType("bvs_test.exceptions")

    class ConfigError(Exception):
        pass

    exc.ConfigError = ConfigError
    sys.modules["bvs_test.exceptions"] = exc
    spec = importlib.util.spec_from_file_location(
        "bvs_test.local_gem_client",
        SKILL_ROOT / "batch_video_skill" / "local_gem_client.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bvs_test.local_gem_client"] = module
    spec.loader.exec_module(module)
    return module


lgc = _load_module()
pytestmark = pytest.mark.skipif(
    not INDEX.exists(), reason="run gems/tools/build_gem_index.py first"
)

CAPTURED: dict = {}

GEM_REPLY = json.dumps(
    {
        "first_frame_prompt": "Vertical 9:16 phone shot, morning kitchen, hands holding a shaved ice maker",
        "video_prompt": "UGC handheld, person struggles then the product solves it, natural light",
        "negative_prompt": "no text, no watermark, no subtitles",
        "style_notes": "handheld, no captions, product appears after 33s",
        "model_suggestion": "grok-video",
        "shots": [{"index": 1, "start_sec": 0, "end_sec": 15, "beat": "hook", "prompt": "..."}],
        "gem_reasoning": "尺码/使用门槛类障碍与该框架吻合",
    },
    ensure_ascii=False,
)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        CAPTURED["path"] = self.path
        CAPTURED["auth"] = self.headers.get("Authorization")
        CAPTURED["body"] = body
        payload = json.dumps({"choices": [{"message": {"content": GEM_REPLY}}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload.encode())

    def log_message(self, *args):  # silence test output
        return


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def library():
    return lgc.LocalGemLibrary(index_path=INDEX)


def test_library_loads_every_gem(library):
    assert len(library) >= 600
    assert all(library.get(code) for code in ("G001", "G101", "U401", "U601"))


def test_resolve_accepts_code_and_registry_name(library):
    assert library.resolve("U601").code == "U601"
    assert library.resolve("", "G012 教笨手室友").code == "G012"
    with pytest.raises(Exception):
        library.resolve("Z999")


def test_system_prompt_carries_gem_text_manual_and_contract(library):
    gem = library.resolve("U601")
    prompt = library.build_system_prompt(gem)
    assert gem.text[:40] in prompt, "gem instruction must be verbatim"
    assert "万能引擎手册" in prompt, "knowledge manual must be attached"
    assert "first_frame_prompt" in prompt and "video_prompt" in prompt
    assert "画面零文字" in prompt, "hard rules must survive into the system prompt"


def test_generate_prompts_round_trip(server, library):
    client = lgc.LocalGemClient(
        library=library, api_key="test-key", base_url=server, model="mock-model"
    )
    raw = client.generate_prompts("产品名：手摇刨冰机\n目标国家：墨西哥", gem_id="U601")

    assert CAPTURED["path"] == "/v1/chat/completions"
    assert CAPTURED["auth"] == "Bearer test-key"
    messages = CAPTURED["body"]["messages"]
    assert messages[0]["role"] == "system" and "U601" in messages[0]["content"]
    assert "手摇刨冰机" in messages[1]["content"]

    data = json.loads(raw)
    assert data["first_frame_prompt"] and data["video_prompt"]


def test_missing_api_key_fails_loudly(library):
    with pytest.raises(Exception) as err:
        lgc.LocalGemClient(library=library, api_key="")
    assert "API key" in str(err.value)


# --- storyboard mode ------------------------------------------------------

STORYBOARD_REPLY = """分镜1（0-15秒）【翻车共鸣】
画面要求：Vertical 9:16 handheld shot, a person opens a parcel and holds the garment against their body, hesitant expression, morning window light, no text on screen
声音/ASMR：包装袋窸窣声、清晨鸟鸣
口播：Misma talla M, tres cuerpos distintos.（同一件M码，穿在三个人身上）
段末状态卡：人物站在窗边手持衣物，衣物吊牌朝向镜头，自然光偏暖

分镜2（15-30秒）【三种身材实穿】
画面要求：Three people of different builds try on the same size in turn, natural indoor light, respectful framing, no captions
声音/ASMR：布料摩擦声
口播：Cada quien enseña dónde le queda justo.（各自指出哪里紧）
段末状态卡：三人并排定格，正面朝向镜头

分镜3（30-45秒）【参照法则】
画面要求：Close-up of hands pinching key measurement points on the garment, tape measure visible, no text overlay
声音/ASMR：卷尺回弹声
口播：Si te pareces a ella, escoge su talla.（接近谁就照谁选）
段末状态卡：三个量位被手指依次点过，衣物平放桌面

分镜4（45-60秒）【换码预演】
画面要求：Person walks and turns wearing the fitting size, relaxed smile, natural light, no on-screen text
声音/ASMR：脚步声与衣物摆动声
口播：Y si fallas, así se cambia.（选错了，这样换）
段末状态卡：人物转身定格，衣摆自然下垂
"""


def test_storyboard_mode_output_parses_into_a_plan(server, library, tmp_path):
    """The storyboard contract must yield text the real parser accepts."""
    import importlib.util as _ilu

    global GEM_REPLY
    original = GEM_REPLY
    GEM_REPLY = STORYBOARD_REPLY
    try:
        client = lgc.LocalGemClient(
            library=library,
            api_key="test-key",
            base_url=server,
            model="mock-model",
            output_mode="storyboard",
        )
        system_prompt = client.preview_system_prompt("U601")
        assert "分镜1（0-15秒）" in system_prompt, "storyboard format must be specified"
        assert "first_frame_prompt" not in system_prompt, "JSON contract must not leak in"

        raw = client.generate_prompts("产品名：连衣裙\n目标国家：墨西哥\n总时长：60秒", gem_id="U601")
    finally:
        GEM_REPLY = original

    spec = _ilu.spec_from_file_location(
        "sb_plan", SKILL_ROOT / "batch_video_skill" / "storyboard_plan.py"
    )
    sb = _ilu.module_from_spec(spec)
    sys.modules["sb_plan"] = sb  # dataclasses resolves annotations via sys.modules
    spec.loader.exec_module(sb)

    raw_path = tmp_path / "gem-raw-output.md"
    raw_path.write_text(raw, encoding="utf-8")
    plan = sb.parse_storyboard_plan(
        raw, raw_path=raw_path, gem_id="U601", gem_name="尺码翻译官体", sheet_seconds=15
    )
    assert len(plan.shots) == 4
    assert plan.shots[0].start_sec == 0 and plan.shots[-1].end_sec == 60
    assert all(shot.source_text_sha256 for shot in plan.shots), "traceability preserved"
