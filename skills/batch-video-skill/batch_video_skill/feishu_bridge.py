from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


DEFAULT_FEISHU_CONFIG = Path("D:/Feishu/video-feishu-sync/config/config.yaml")


@dataclass(slots=True)
class FeishuBridgeConfig:
    lark_cli_path: str
    base_token: str
    table_id: str
    drive_folder_token: str
    batch_table_id: str = ""
    creative_table_id: str = ""
    segment_table_id: str = ""
    identity: str = "bot"


@dataclass(slots=True)
class FeishuSyncSummary:
    output_dir: str
    dry_run: bool
    scanned_jobs: int = 0
    upserted_rows: int = 0
    uploaded_videos: int = 0
    skipped_uploads: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FeishuGemCatalogSummary:
    source: str
    document_id: str
    document_title: str
    output_path: str
    csv_path: str
    total_items: int
    shared_gems: int
    inline_prompts: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TASK_FIELD_DEFS: list[dict[str, Any]] = [
    {"name": "job_id", "type": "text"},
    {"name": "batch_id", "type": "text"},
    {"name": "源图片路径", "type": "text"},
    {"name": "源图片文件名", "type": "text"},
    {"name": "产品名称", "type": "text"},
    {"name": "品类", "type": "text"},
    {"name": "目标市场", "type": "text"},
    {"name": "版本号", "type": "number"},
    {"name": "Gem ID", "type": "text"},
    {"name": "Gem 请求路径", "type": "text"},
    {"name": "Gem 输出路径", "type": "text"},
    {"name": "首帧提示词路径", "type": "text"},
    {"name": "视频提示词", "type": "text"},
    {"name": "首帧本地路径", "type": "text"},
    {"name": "首帧URL", "type": "text"},
    {"name": "视频模型", "type": "text"},
    {"name": "视频任务ID", "type": "text"},
    {"name": "视频本地路径", "type": "text"},
    {"name": "输出目录", "type": "text"},
    {"name": "prompt_source", "type": "text"},
    {
        "name": "状态",
        "type": "select",
        "multiple": False,
        "options": [{"name": name} for name in [
            "queued",
            "running",
            "gem_prompt_generating",
            "gem_prompt_ready",
            "first_frame_ready",
            "video_submitted",
            "completed",
            "failed",
            "skipped",
            "dry_run",
        ]],
    },
    {"name": "错误信息", "type": "text"},
    {"name": "开始时间", "type": "date"},
    {"name": "更新时间", "type": "date"},
    {"name": "完成时间", "type": "date"},
    {"name": "耗时秒", "type": "number"},
]


class FeishuCli:
    def __init__(self, executable: str, *, cwd: Path | None = None) -> None:
        self.executable = executable
        self.cwd = cwd or Path.cwd()

    def run(self, args: list[str], *, timeout: int = 120, expect_json: bool = True) -> dict[str, Any]:
        completed = subprocess.run(
            [self.executable, *args],
            cwd=str(self.cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        output = ((completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")).strip()
        data = _parse_json(output) if expect_json else {"raw": output}
        ok = completed.returncode == 0 and data.get("ok", True) is not False
        if not ok:
            raise RuntimeError(f"lark-cli failed: {output[:1500]}")
        return data

    def run_with_json_file(self, args: list[str], payload: dict[str, Any], *, timeout: int = 120) -> dict[str, Any]:
        self.cwd.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", dir=self.cwd, delete=False) as file:
            json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
            temp_path = Path(file.name)
        try:
            return self.run([*args, "--json", f"@{temp_path.name}"], timeout=timeout)
        finally:
            temp_path.unlink(missing_ok=True)


def load_feishu_bridge_config(path: str | Path = DEFAULT_FEISHU_CONFIG) -> FeishuBridgeConfig:
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8-sig")) or {}
    feishu = data.get("feishu") or {}
    return FeishuBridgeConfig(
        lark_cli_path=str(feishu.get("lark_cli_path") or "lark-cli"),
        base_token=str(feishu.get("base_token") or ""),
        table_id=str(feishu.get("table_id") or ""),
        drive_folder_token=str(feishu.get("drive_folder_token") or ""),
        batch_table_id=str(feishu.get("batch_table_id") or ""),
        creative_table_id=str(feishu.get("creative_table_id") or ""),
        segment_table_id=str(feishu.get("segment_table_id") or ""),
        identity=str(feishu.get("identity") or "bot"),
    )


def init_v4_console_schema(config: FeishuBridgeConfig, *, dry_run: bool = True) -> dict[str, Any]:
    """Add the three V4 console tables without touching existing tables or rows."""
    from .feishu_outbox import FEISHU_TABLE_SCHEMAS

    if not config.base_token:
        raise ValueError("Feishu base_token is missing")
    if dry_run:
        return {
            "dry_run": True,
            "planned_tables": list(FEISHU_TABLE_SCHEMAS),
            "created_tables": [],
            "existing_tables": [],
        }
    cli = FeishuCli(config.lark_cli_path)
    raw = cli.run([
        "base",
        "+table-list",
        "--base-token",
        config.base_token,
        "--as",
        config.identity,
    ])
    existing = _extract_table_names(raw)
    created: list[str] = []
    for table_name, fields in FEISHU_TABLE_SCHEMAS.items():
        if table_name in existing:
            continue
        cli.run([
            "base",
            "+table-create",
            "--base-token",
            config.base_token,
            "--name",
            table_name,
            "--fields",
            json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
            "--as",
            config.identity,
            "--format",
            "json",
        ])
        created.append(table_name)
    return {
        "dry_run": False,
        "planned_tables": list(FEISHU_TABLE_SCHEMAS),
        "created_tables": created,
        "existing_tables": sorted(existing),
    }


def flush_v4_outbox(
    output_dir: str | Path,
    config: FeishuBridgeConfig,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    from .feishu_outbox import FeishuOutbox, flush_outbox

    root = Path(output_dir).expanduser().resolve()
    outbox = FeishuOutbox(root / "feishu_outbox.jsonl")
    if dry_run:
        pending = outbox.pending()
        return {
            "dry_run": True,
            "scanned": len(pending),
            "synced": 0,
            "failed": 0,
            "tables": sorted({event.table for event in pending}),
        }
    if not config.base_token:
        raise ValueError("Feishu base_token is missing")
    cli = FeishuCli(config.lark_cli_path, cwd=root)
    table_ids = {
        "批次": config.batch_table_id or "批次",
        "创意计划": config.creative_table_id or "创意计划",
        "视频分段": config.segment_table_id or "视频分段",
    }

    def writer(event: Any) -> None:
        table_id = table_ids[event.table]
        primary_field = next(iter(event.fields), "")
        primary_value = str(event.fields.get(primary_field) or event.stable_id)
        record_id = _find_record_by_field(
            cli,
            config.base_token,
            table_id,
            primary_field,
            primary_value,
            identity=config.identity,
        )
        args = [
            "base",
            "+record-upsert",
            "--base-token",
            config.base_token,
            "--table-id",
            table_id,
            "--as",
            config.identity,
        ]
        if record_id:
            args += ["--record-id", record_id]
        cli.run_with_json_file(args, event.fields)

    return {"dry_run": False, **flush_outbox(outbox, writer).to_dict()}


def init_feishu_schema(config: FeishuBridgeConfig, *, dry_run: bool = True) -> dict[str, Any]:
    cli = FeishuCli(config.lark_cli_path)
    existing = set()
    if config.base_token and config.table_id:
        fields = cli.run([
            "base",
            "+field-list",
            "--base-token",
            config.base_token,
            "--table-id",
            config.table_id,
        ])
        for field in (((fields.get("data") or {}).get("fields")) or []):
            if isinstance(field, dict) and field.get("name"):
                existing.add(str(field["name"]))

    planned: list[str] = []
    created: list[str] = []
    for field_def in TASK_FIELD_DEFS:
        name = str(field_def["name"])
        if name in existing:
            continue
        planned.append(name)
        if dry_run:
            continue
        cli.run_with_json_file([
            "base",
            "+field-create",
            "--base-token",
            config.base_token,
            "--table-id",
            config.table_id,
        ], field_def)
        created.append(name)
    return {"dry_run": dry_run, "planned": planned, "created": created}


def sync_batch_output(
    output_dir: str | Path,
    config: FeishuBridgeConfig,
    *,
    dry_run: bool = True,
    upload_videos: bool = True,
) -> FeishuSyncSummary:
    root = Path(output_dir).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Batch output dir not found: {root}")

    summary = FeishuSyncSummary(output_dir=str(root), dry_run=dry_run)
    jobs = list(_load_job_records(root))
    summary.scanned_jobs = len(jobs)
    cli = FeishuCli(config.lark_cli_path, cwd=root)

    for job in jobs:
        try:
            fields = _build_feishu_fields(root, job)
            video_path = Path(str(job.get("local_video") or ""))
            if upload_videos and video_path.exists() and video_path.is_file():
                if dry_run:
                    summary.skipped_uploads += 1
                else:
                    upload = cli.run([
                        "drive",
                        "+upload",
                        "--file",
                        str(video_path),
                        "--folder-token",
                        config.drive_folder_token,
                    ], timeout=3600)
                    summary.uploaded_videos += 1
                    token = _first_value(upload, ["file_token", "token", "obj_token"])
                    url = _first_value(upload, ["url", "file_url", "web_url", "link"])
                    fields["飞书文件Token"] = token
                    fields["飞书云链接"] = url or (f"https://www.feishu.cn/drive/file/{token}" if token else "")
            else:
                summary.skipped_uploads += 1

            if not dry_run:
                record_id = _find_existing_record_id(
                    cli,
                    config.base_token,
                    config.table_id,
                    str(fields.get("job_id") or ""),
                )
                args = [
                    "base",
                    "+record-upsert",
                    "--base-token",
                    config.base_token,
                    "--table-id",
                    config.table_id,
                ]
                if record_id:
                    args += ["--record-id", record_id]
                cli.run_with_json_file(args, fields)
            summary.upserted_rows += 1
        except Exception as exc:  # noqa: BLE001 - keep batch sync going
            summary.failed += 1
            summary.errors.append(f"{job.get('source_image', '<unknown>')}: {exc}")
    return summary


def import_gem_catalog_from_feishu(
    source: str,
    config: FeishuBridgeConfig,
    *,
    output_path: str | Path,
    csv_path: str | Path | None = None,
    identity: str = "user",
) -> FeishuGemCatalogSummary:
    """Read a Feishu wiki/docx page and build a local Gem catalog.

    The catalog contains only user-provided Gem links and inline prompts from
    the document. It does not call Gemini and does not store credentials.
    """
    cli = FeishuCli(config.lark_cli_path)
    document_id = source
    if "/wiki/" in source or source.startswith("http"):
        node = cli.run([
            "wiki",
            "+node-get",
            "--node-token",
            source,
            "--as",
            identity,
            "--format",
            "json",
        ])
        document_id = str(_first_value(node, ["obj_token", "document_id", "token"]) or "")
        if not document_id:
            raise RuntimeError(f"Could not resolve wiki source to docx token: {source}")

    fetched = cli.run([
        "docs",
        "+fetch",
        "--doc",
        document_id,
        "--api-version",
        "v2",
        "--as",
        identity,
        "--format",
        "json",
    ])
    document = ((fetched.get("data") or {}).get("document") or {})
    content = str(document.get("content") or "")
    title = _strip_markup(str(_between(content, "<title>", "</title>") or document.get("title") or ""))
    items = parse_gem_catalog_content(content, document_title=title or Path(str(source)).stem)

    output = Path(output_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "source": source,
                "document_id": document_id,
                "document_title": title,
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    csv_file = Path(csv_path).expanduser() if csv_path else output.with_suffix(".csv")
    _write_gem_catalog_csv(csv_file, items)
    return FeishuGemCatalogSummary(
        source=source,
        document_id=document_id,
        document_title=title,
        output_path=str(output),
        csv_path=str(csv_file),
        total_items=len(items),
        shared_gems=sum(1 for item in items if item.get("kind") == "shared_gem"),
        inline_prompts=sum(1 for item in items if item.get("kind") == "inline_prompt"),
    )


def parse_gem_catalog_content(content: str, *, document_title: str = "") -> list[dict[str, Any]]:
    tokens = _iter_doc_tokens(content)
    items: list[dict[str, Any]] = []
    current_h1 = ""
    current_heading = ""
    pending_caption = ""
    ordinal = 0

    for token_type, value, attrs in tokens:
        text = _strip_markup(value)
        if token_type == "h1":
            current_h1 = text
            current_heading = ""
            continue
        if token_type in {"h2", "h3", "p"} and _looks_like_gem_heading(text):
            current_heading = text
            pending_caption = ""
            continue
        if token_type == "pre":
            caption = _strip_markup(attrs.get("caption", ""))
            body = text.strip()
            if not body:
                continue
            name = caption or current_heading or pending_caption or f"Gem {ordinal + 1}"
            urls = _extract_gem_urls(body)
            if urls:
                for url in urls:
                    ordinal += 1
                    items.append({
                        "index": ordinal,
                        "kind": "shared_gem",
                        "gem_name": _normalize_gem_name(name),
                        "gem_url": url,
                        "gem_share_id": _extract_gem_id(url),
                        "prompt": "",
                        "source_section": current_h1,
                        "source_heading": current_heading,
                        "document_title": document_title,
                    })
            elif len(body) >= 40:
                ordinal += 1
                items.append({
                    "index": ordinal,
                    "kind": "inline_prompt",
                    "gem_name": _normalize_gem_name(name),
                    "gem_url": "",
                    "gem_share_id": "",
                    "prompt": body,
                    "source_section": current_h1,
                    "source_heading": current_heading,
                    "document_title": document_title,
                })
            continue
        if text:
            pending_caption = text
    return items


def _load_job_records(output_dir: Path) -> list[dict[str, Any]]:
    jsonl = output_dir / "batch_manifest.jsonl"
    if jsonl.exists():
        rows = []
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    csv_path = output_dir / "batch_manifest.csv"
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            return [dict(row) for row in csv.DictReader(file)]

    state_dir = output_dir / "state"
    if state_dir.exists():
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(state_dir.glob("*.json"))]
    return []


def _find_existing_record_id(cli: FeishuCli, base_token: str, table_id: str, job_id: str) -> str:
    if not job_id:
        return ""
    records = cli.run([
        "base",
        "+record-list",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--format",
        "json",
    ])
    for record in _iter_records(records):
        fields = record.get("fields") if isinstance(record, dict) else {}
        if isinstance(fields, dict) and str(fields.get("job_id") or "") == job_id:
            return str(record.get("record_id") or record.get("id") or "")
    return ""


def _find_record_by_field(
    cli: FeishuCli,
    base_token: str,
    table_id: str,
    field_name: str,
    field_value: str,
    *,
    identity: str,
) -> str:
    if not field_name or not field_value:
        return ""
    records = cli.run([
        "base",
        "+record-list",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--as",
        identity,
        "--format",
        "json",
    ])
    for record in _iter_records(records):
        fields = record.get("fields") if isinstance(record, dict) else {}
        if isinstance(fields, dict) and str(fields.get(field_name) or "") == field_value:
            return str(record.get("record_id") or record.get("id") or "")
    return ""


def _extract_table_names(data: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            name = value.get("name") or value.get("table_name")
            if name and (value.get("table_id") or value.get("id")):
                names.add(str(name))
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(data)
    return names


def _iter_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "records"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    nested = data.get("data")
    if isinstance(nested, dict):
        return _iter_records(nested)
    return []


def _build_feishu_fields(output_dir: Path, job: dict[str, Any]) -> dict[str, Any]:
    source = str(job.get("source_image") or "")
    video_path = str(job.get("local_video") or "")
    prompt_path = str(job.get("prompt_path") or "")
    gem_output_path = str(job.get("gem_output_path") or "")
    fields = {
        "job_id": _job_id(job),
        "batch_id": output_dir.name,
        "源图片路径": source,
        "源图片文件名": Path(source).name if source else "",
        "产品名称": job.get("product_name") or "",
        "版本号": _to_number(job.get("variant"), 1),
        "Gem ID": job.get("gem_id") or "",
        "Gem 请求路径": _matching_gem_request_path(output_dir, prompt_path),
        "Gem 输出路径": gem_output_path,
        "首帧提示词路径": prompt_path,
        "视频提示词": _read_short_text(prompt_path),
        "首帧本地路径": job.get("first_frame_path") or "",
        "首帧URL": job.get("first_frame_url") or "",
        "视频模型": "Kuaizi/Seedance",
        "视频任务ID": job.get("task_id") or "",
        "视频本地路径": video_path,
        "本地路径": video_path,
        "文件名": Path(video_path).name if video_path else "",
        "视频名称": Path(video_path).stem if video_path else Path(source).stem,
        "输出目录": str(output_dir),
        "prompt_source": job.get("prompt_source") or "",
        "状态": job.get("status") or "",
        "上传状态": "待上传" if video_path else "",
        "同步状态": "未同步",
        "错误信息": job.get("error") or "",
        "备注": job.get("error") or "",
        "开始时间": job.get("started_at") or "",
        "更新时间": datetime.now().isoformat(timespec="seconds"),
        "完成时间": job.get("completed_at") or "",
        "耗时秒": _to_number(job.get("elapsed_sec"), 0),
    }
    return {key: value for key, value in fields.items() if value not in (None, "")}


def _job_id(job: dict[str, Any]) -> str:
    raw = "|".join([
        str(job.get("source_image") or ""),
        str(job.get("variant") or ""),
        str(job.get("prompt_path") or ""),
    ])
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _matching_gem_request_path(output_dir: Path, prompt_path: str) -> str:
    if not prompt_path:
        return ""
    stem = Path(prompt_path).stem
    candidate = output_dir / "gem_requests" / f"{stem}.txt"
    return str(candidate) if candidate.exists() else ""


def _read_short_text(path: str, limit: int = 1200) -> str:
    if not path:
        return ""
    file = Path(path)
    if not file.exists() or not file.is_file():
        return ""
    text = file.read_text(encoding="utf-8", errors="replace").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _to_number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        data = json.loads(stripped)
        return data if isinstance(data, dict) else {"items": data}
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(stripped[start : end + 1])
                return data if isinstance(data, dict) else {"items": data}
            except json.JSONDecodeError:
                pass
    return {"raw": stripped}


def _first_value(data: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = data.get(key)
        if value:
            return str(value)
    for value in data.values():
        if isinstance(value, dict):
            found = _first_value(value, keys)
            if found:
                return found
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found = _first_value(item, keys)
                    if found:
                        return found
    return ""


def _write_gem_catalog_csv(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "index",
        "kind",
        "gem_name",
        "gem_share_id",
        "gem_url",
        "source_section",
        "source_heading",
        "document_title",
        "prompt",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        for item in items:
            row = {key: item.get(key, "") for key in headers}
            prompt = str(row.get("prompt") or "")
            row["prompt"] = prompt if len(prompt) <= 2000 else prompt[:2000] + "..."
            writer.writerow(row)


def _iter_doc_tokens(content: str) -> list[tuple[str, str, dict[str, str]]]:
    pattern = re.compile(
        r"<(?P<tag>h1|h2|h3|p)\b[^>]*>(?P<body>.*?)</(?P=tag)>"
        r"|<pre(?P<attrs>[^>]*)>\s*<code>(?P<code>.*?)</code>\s*</pre>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    tokens: list[tuple[str, str, dict[str, str]]] = []
    for match in pattern.finditer(content):
        if match.group("tag"):
            tokens.append((match.group("tag").lower(), match.group("body") or "", {}))
        else:
            tokens.append(("pre", match.group("code") or "", _parse_attrs(match.group("attrs") or "")))
    return tokens


def _parse_attrs(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, value in re.findall(r'([a-zA-Z0-9_-]+)="(.*?)"', raw, flags=re.DOTALL):
        attrs[key] = html.unescape(value)
    return attrs


def _strip_markup(value: str) -> str:
    text = value.replace("<br/>", "\n").replace("<br>", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def _between(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        return ""
    start_index += len(start)
    end_index = text.find(end, start_index)
    if end_index < 0:
        return ""
    return text[start_index:end_index]


def _looks_like_gem_heading(text: str) -> bool:
    if not text:
        return False
    if re.match(r"^\d+[\.\、]", text):
        return True
    keywords = ("女主播", "街头采访", "痛点", "带货", "短剧", "开箱", "仓库", "超市", "故事板", "标题优化")
    return any(keyword in text for keyword in keywords)


def _extract_gem_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in re.findall(r"https://gemini\.google\.com/gem/[^\s<>'\"]+", text):
        url = match.rstrip("，。；,.;)")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _extract_gem_id(url: str) -> str:
    match = re.search(r"/gem/([^?/#]+)", url)
    return match.group(1) if match else ""


def _normalize_gem_name(text: str) -> str:
    name = re.sub(r"^\d+[\.\、]\s*", "", text).strip()
    name = re.sub(r"\s+", " ", name)
    return name or text.strip()
