"""Parse the gem markdown library into a machine-readable index.

Output: gems/index/gem_texts.jsonl — one record per gem code, holding the gem's
verbatim instruction text. This is what LocalGemClient feeds to a chat model as
the system prompt, so the model behaves like the online Gemini Gem.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "index"

# "════ U601 ════" style separators (U601-U840, U401-U600, G101-G200)
SEP_RE = re.compile(r"^[=＝═\s]*\s*([GU]\d{3})\s*[=＝═\s]*$")
# "## G001 ..." / "### G001" style headings (G001-G100 import pack)
HEAD_RE = re.compile(r"^#{2,4}\s*\**\s*([GU]\d{3})\b")
NAME_RE = re.compile(r"【名称】\s*(?:粘贴到[^\n：:]*[：:])?\s*(?:[GU]\d{3}[\s·]*)?(.*)")
# The G001-G100 pack puts the name on the line AFTER 【名称】粘贴到「名称」栏：
NAME_NEXTLINE_RE = re.compile(r"【名称】[^\n]*\n\s*(?:[GU]\d{3}[\s·]*)?([^\n]+)")

# Which knowledge manual each range expects in the Gem "knowledge" pane.
MANUALS = [
    (1, 100, "archive/万能引擎手册v2.0_传Gem知识栏.md"),
    (101, 200, "archive/万能引擎手册v2.1_传Gem知识栏.md"),
    (401, 600, "archive/万能引擎手册v2.2_传Gem知识栏.md"),
    (601, 1000, "万能引擎手册v2.4_U601-U840_传Gem知识栏.md"),
]

FAMILIES = [
    (1, 100, "G族·载体×角度"),
    (101, 200, "G族·长视频钩子链"),
    (401, 600, "U族·决策/情绪/感官/生态"),
    (601, 700, "E族·品类实战"),
    (701, 800, "F族·人群心智"),
    (801, 900, "G族·场景时刻"),
    (901, 1000, "H族·转化机制"),
]


def _lookup(table: list[tuple[int, int, str]], num: int, default: str = "") -> str:
    for lo, hi, val in table:
        if lo <= num <= hi:
            return val
    return default


def _split_blocks(text: str) -> list[tuple[str, str]]:
    """Return [(code, block_text)] for one markdown file."""
    lines = text.splitlines()
    marks: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = SEP_RE.match(stripped) or HEAD_RE.match(stripped)
        if m:
            marks.append((i, m.group(1)))
    blocks: list[tuple[str, str]] = []
    for idx, (line_no, code) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        body = "\n".join(lines[line_no + 1 : end]).strip()
        if body:
            blocks.append((code, body))
    return blocks


def build() -> dict[str, dict]:
    records: dict[str, dict] = {}
    for md in sorted(ROOT.rglob("*.md")):
        if md.parent.name in {"index", "tools", "knowledge"} or "archive" in md.parts:
            continue
        if md.name == "README.md":
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        for code, body in _split_blocks(text):
            num = int(code[1:])
            name = ""
            name_match = NAME_RE.search(body)
            if name_match:
                name = name_match.group(1).strip()
            if not name or name.endswith(("：", ":")) or "粘贴" in name:
                nxt = NAME_NEXTLINE_RE.search(body)
                name = nxt.group(1).strip() if nxt else name.strip("：: ")
            prev = records.get(code)
            # Keep the richest version when a code appears in several packs.
            if prev and len(prev["text"]) >= len(body):
                continue
            records[code] = {
                "code": code,
                "name": name,
                "family": _lookup(FAMILIES, num),
                "manual": _lookup(MANUALS, num),
                "source_file": str(md.relative_to(ROOT)).replace("\\", "/"),
                "text": body,
            }
    return records


def main() -> None:
    records = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "gem_texts.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for code in sorted(records, key=lambda c: (c[0], int(c[1:]))):
            fh.write(json.dumps(records[code], ensure_ascii=False) + "\n")

    codes = sorted(records, key=lambda c: (c[0], int(c[1:])))
    summary = {
        "total": len(codes),
        "by_family": {},
        "missing_name": [c for c in codes if not records[c]["name"]],
    }
    for c in codes:
        fam = records[c]["family"] or "unknown"
        summary["by_family"][fam] = summary["by_family"].get(fam, 0) + 1
    (OUT_DIR / "gem_index_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:800])
    print(f"\nwrote {out} ({len(codes)} gems)")


if __name__ == "__main__":
    main()
