"""Run a Gem locally against any OpenAI-compatible chat model.

Why this exists
---------------
The original pipeline reached the creative Gem through ``gemcli``, which drives
the Gemini web app with session cookies. That path breaks whenever cookies
expire, needs the Gem to exist online, and cannot run unattended.

``LocalGemClient`` reproduces the same step offline-friendly: it loads the gem's
verbatim instruction text from ``gems/index/gem_texts.jsonl`` plus the knowledge
manual the gem expects, sends them as the system prompt to a chat model, and
returns the model's raw response. It exposes the exact same
``generate_prompts(request_prompt, gem_id=...)`` signature as ``GemCliClient``,
so it is a drop-in replacement everywhere in the batch runner.

The gem text and the manual are the same bytes a human would paste into
Gemini's instruction and knowledge panes, which is what keeps output quality on
par with the online Gem.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .exceptions import ConfigError

_CODE_RE = re.compile(r"\b([GU]\d{3})\b")

DEFAULT_INDEX_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "gems" / "index" / "gem_texts.jsonl",
    Path(__file__).resolve().parents[2] / "gems" / "index" / "gem_texts.jsonl",
    Path("gems/index/gem_texts.jsonl"),
)

OUTPUT_CONTRACT = """
输出契约（必须遵守，优先级高于 Gem 指令里的排版偏好）：
只输出一个 JSON 对象，不要输出任何解释、前后缀或 Markdown 代码块以外的文字。字段：
{
  "first_frame_prompt": "英文，生活化首帧图提示词（喂给 HFSY / GPT-image-2）",
  "video_prompt": "英文，竖屏 9:16 带货视频提示词（喂给快子 / Grok / Omni）",
  "negative_prompt": "英文负面约束",
  "style_notes": "镜头、节奏、一致性注意事项",
  "model_suggestion": "推荐视频模型",
  "shots": [
    {"index": 1, "start_sec": 0, "end_sec": 15, "beat": "该段职责", "prompt": "英文分镜提示词"}
  ],
  "gem_reasoning": "一句话说明本条为何贴合该产品（中文）"
}
创作本身仍完全按上面的 Gem 指令与《万能引擎手册》执行：铁律（画面零文字、产品首现时点、利他 64 节奏、话术禁令、真实性）一条都不许放松。
""".strip()


STORYBOARD_CONTRACT = """
输出契约（必须遵守，优先级高于 Gem 指令里的排版偏好）：
按下面的固定格式逐条输出分镜，不要输出 JSON，不要输出表格，不要加代码块。
每个分镜一段，段与段之间空一行：

分镜1（0-15秒）【该段一句话标题】
画面要求：英文提示词在前，中文补充在后；一个分镜只描述一个物理可实现的状态和一个主要动作
声音/ASMR：环境音与音效
口播：目标国家本土语言原文（后附中文对照）
段末状态卡：本段最后一帧的人物姿态/产品位置/光线，供下一段续帧使用

分镜2（15-30秒）【…】
…

硬性要求：
- 秒数必须连续无缝且覆盖用户要求的总时长，单段不超过 15 秒。
- 每个分镜都必须带（起始秒-结束秒），格式照抄上面的圆括号写法。
- 画面里禁止出现任何文字、字幕、贴纸、价格、Before/After 字样。
- 产品首现时点、利他 64 节奏、话术禁令、真实性铁律按 Gem 指令与手册执行，一条不许放松。
""".strip()


@dataclass(slots=True)
class GemText:
    code: str
    name: str
    family: str
    manual: str
    source_file: str
    text: str


@dataclass(slots=True)
class LocalGemLibrary:
    """Loads gem instruction text and the matching knowledge manual."""

    index_path: Path
    manual_root: Path | None = None
    _by_code: dict[str, GemText] = field(default_factory=dict, init=False)
    _manual_cache: dict[str, str] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if not self.index_path.exists():
            raise ConfigError(
                f"Gem text index not found: {self.index_path}. "
                "Run: python gems/tools/build_gem_index.py"
            )
        for line in self.index_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            self._by_code[row["code"]] = GemText(
                code=row["code"],
                name=row.get("name", ""),
                family=row.get("family", ""),
                manual=row.get("manual", ""),
                source_file=row.get("source_file", ""),
                text=row["text"],
            )
        if self.manual_root is None:
            self.manual_root = self.index_path.parent.parent / "knowledge"

    def __len__(self) -> int:
        return len(self._by_code)

    @property
    def codes(self) -> list[str]:
        return sorted(self._by_code, key=lambda c: (c[0], int(c[1:])))

    def get(self, code: str) -> GemText | None:
        return self._by_code.get(code.upper())

    def resolve(self, gem_identifier: str, gem_name: str = "") -> GemText:
        """Accept a code (U601), a registry name ("U206 POV第一视角"), or a hex gem_id."""
        for candidate in (gem_identifier or "", gem_name or ""):
            match = _CODE_RE.search(candidate.upper())
            if match:
                found = self._by_code.get(match.group(1))
                if found:
                    return found
        raise ConfigError(
            f"No local gem text for '{gem_identifier or gem_name}'. "
            f"Index holds {len(self._by_code)} gems; pass a code like U601 or G012."
        )

    def manual_text(self, gem: GemText) -> str:
        if not gem.manual:
            return ""
        if gem.manual in self._manual_cache:
            return self._manual_cache[gem.manual]
        path = (self.manual_root or Path(".")) / gem.manual
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        self._manual_cache[gem.manual] = text
        return text

    def build_system_prompt(
        self,
        gem: GemText,
        *,
        include_manual: bool = True,
        output_mode: str = "json",
    ) -> str:
        """output_mode: "json" for the batch prompt contract, "storyboard" for timed shots."""
        parts = [
            "你现在就是下面这条带货视频编导 Gem 本身。严格按【指令】执行，"
            "《万能引擎手册》为知识库，两者冲突时以 Gem 指令为准。",
            f"# Gem 编号\n{gem.code}｜{gem.name}｜{gem.family}",
        ]
        if include_manual:
            manual = self.manual_text(gem)
            if manual:
                parts.append(f"# 知识库《万能引擎手册》\n{manual}")
        parts.append(f"# Gem 指令原文\n{gem.text}")
        parts.append(
            STORYBOARD_CONTRACT if output_mode == "storyboard" else OUTPUT_CONTRACT
        )
        return "\n\n".join(parts)


class LocalGemClient:
    """Drop-in replacement for GemCliClient backed by an OpenAI-compatible API."""

    def __init__(
        self,
        *,
        library: LocalGemLibrary,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        gem_id: str = "",
        gem_name: str = "",
        temperature: float = 0.8,
        max_tokens: int = 4096,
        timeout_sec: int = 180,
        retries: int = 2,
        retry_delay_sec: int = 5,
        include_manual: bool = True,
        output_mode: str = "json",
        allow_empty_key: bool = False,
    ) -> None:
        if not api_key and not allow_empty_key:
            raise ConfigError(
                "Missing text-model API key. Set GEM_TEXT_API_KEY (or DEEPSEEK_API_KEY / "
                "MOONSHOT_API_KEY / OPENAI_API_KEY), or config.text_models.api_key."
            )
        self.library = library
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.gem_id = gem_id
        self.gem_name = gem_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_sec = timeout_sec
        self.retries = max(0, retries)
        self.retry_delay_sec = max(1, retry_delay_sec)
        self.include_manual = include_manual
        self.output_mode = output_mode

    # --- public API (matches GemCliClient) -------------------------------
    def generate_prompts(self, request_prompt: str, gem_id: str | None = None) -> str:
        gem = self.library.resolve(gem_id or self.gem_id, self.gem_name)
        system_prompt = self.library.build_system_prompt(
            gem, include_manual=self.include_manual, output_mode=self.output_mode
        )
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._chat(system_prompt, request_prompt)
            except Exception as exc:  # noqa: BLE001 - retry API boundary errors
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.retry_delay_sec * (attempt + 1))
        raise RuntimeError(
            f"Local gem generation failed after {self.retries + 1} attempt(s) "
            f"[{gem.code} via {self.model}]: {last_error}"
        )

    def preview_system_prompt(self, gem_id: str) -> str:
        gem = self.library.resolve(gem_id, self.gem_name)
        return self.library.build_system_prompt(
            gem, include_manual=self.include_manual, output_mode=self.output_mode
        )

    # --- transport -------------------------------------------------------
    def _chat(self, system_prompt: str, user_prompt: str) -> str:
        if not self.api_key:
            raise ConfigError("Missing text-model API key; set GEM_TEXT_API_KEY.")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"Text model HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Text model unreachable: {exc.reason}") from exc

        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError(f"Text model returned no choices: {str(body)[:300]}")
        content = (choices[0].get("message") or {}).get("content") or ""
        content = content.strip()
        if not content:
            raise RuntimeError("Text model returned empty content")
        return content


# --- factory --------------------------------------------------------------

_ENV_KEYS = (
    "GEM_TEXT_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "KIMI_API_KEY",
    "OPENAI_API_KEY",
)

_PRESETS = {
    "deepseek": ("https://api.deepseek.com", "deepseek-chat"),
    "kimi": ("https://api.moonshot.cn", "moonshot-v1-32k"),
    "moonshot": ("https://api.moonshot.cn", "moonshot-v1-32k"),
    "openai": ("https://api.openai.com", "gpt-4o"),
}


def _find_index(explicit: str | Path | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists():
            return path
        raise ConfigError(f"Gem text index not found: {path}")
    for candidate in DEFAULT_INDEX_CANDIDATES:
        if candidate.exists():
            return candidate
    raise ConfigError(
        "Gem text index not found. Run: python gems/tools/build_gem_index.py"
    )


def make_local_gem_client(
    config: dict[str, Any] | None = None,
    *,
    gem_id: str = "",
    gem_name: str = "",
    timeout_sec: int = 180,
    retries: int = 2,
    retry_delay_sec: int = 5,
    output_mode: str = "json",
    allow_empty_key: bool = False,
) -> LocalGemClient:
    """Build a LocalGemClient from config + environment.

    config.text_models supports: provider, base_url, model, api_key, api_key_env,
    temperature, max_tokens, include_manual, gem_index_path.
    """
    conf = ((config or {}).get("text_models") or {}) if config else {}
    provider = str(conf.get("provider") or os.getenv("GEM_TEXT_PROVIDER") or "deepseek").lower()
    preset_url, preset_model = _PRESETS.get(provider, _PRESETS["deepseek"])

    base_url = str(conf.get("base_url") or os.getenv("GEM_TEXT_BASE_URL") or preset_url)
    model = str(conf.get("model") or os.getenv("GEM_TEXT_MODEL") or preset_model)

    api_key = str(conf.get("api_key") or "")
    if not api_key:
        env_name = conf.get("api_key_env")
        names = (env_name,) if env_name else _ENV_KEYS
        for name in names:
            if name and os.getenv(name):
                api_key = os.environ[name]
                break

    library = LocalGemLibrary(index_path=_find_index(conf.get("gem_index_path")))
    return LocalGemClient(
        library=library,
        api_key=api_key,
        base_url=base_url,
        model=model,
        gem_id=gem_id,
        gem_name=gem_name,
        temperature=float(conf.get("temperature") or 0.8),
        max_tokens=int(conf.get("max_tokens") or 4096),
        timeout_sec=timeout_sec,
        retries=retries,
        retry_delay_sec=retry_delay_sec,
        include_manual=bool(conf.get("include_manual", True)),
        output_mode=output_mode,
        allow_empty_key=allow_empty_key,
    )
