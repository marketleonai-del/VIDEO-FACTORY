from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

FEISHU_TABLE_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "批次": [
        {"name": "batch_id", "type": "text"},
        {"name": "输入文件夹", "type": "text"},
        {"name": "商品数量", "type": "number"},
        {
            "name": "状态",
            "type": "select",
            "options": [
                {"name": name}
                for name in ("queued", "running", "completed", "needs_review", "failed")
            ],
        },
        {"name": "成功数", "type": "number"},
        {"name": "失败数", "type": "number"},
        {"name": "QC失败数", "type": "number"},
        {"name": "更新时间", "type": "date"},
    ],
    "创意计划": [
        {"name": "plan_id", "type": "text"},
        {"name": "商品哈希", "type": "text"},
        {"name": "模板/Gem", "type": "text"},
        {"name": "Gem输出路径", "type": "text"},
        {"name": "计划哈希", "type": "text"},
        {"name": "缓存命中", "type": "checkbox"},
        {"name": "字符数", "type": "number"},
        {"name": "Token估算", "type": "number"},
        {"name": "更新时间", "type": "date"},
    ],
    "视频分段": [
        {"name": "segment_id", "type": "text"},
        {"name": "模型", "type": "text"},
        {"name": "段号", "type": "number"},
        {
            "name": "风险等级",
            "type": "select",
            "options": [{"name": name} for name in ("low", "medium", "high")],
        },
        {"name": "参考图", "type": "text"},
        {"name": "提示词哈希", "type": "text"},
        {"name": "提示词摘要", "type": "text"},
        {"name": "task ID", "type": "text"},
        {"name": "重试次数", "type": "number"},
        {
            "name": "QC",
            "type": "select",
            "options": [{"name": name} for name in ("pending", "passed", "failed", "needs_review")],
        },
        {"name": "视频链接", "type": "text"},
        {"name": "失败原因", "type": "text"},
        {"name": "更新时间", "type": "date"},
    ],
    "故事板": [
        {"name": "storyboard_id", "type": "text"},
        {"name": "Gem", "type": "text"},
        {"name": "Gem原文文件", "type": "text"},
        {"name": "Gem原文哈希", "type": "text"},
        {"name": "分镜数量", "type": "number"},
        {"name": "故事板目录", "type": "text"},
        {"name": "故事板预览", "type": "text"},
        {"name": "解析覆盖率", "type": "number"},
        {
            "name": "QC",
            "type": "select",
            "options": [{"name": name} for name in ("pending", "passed", "failed", "needs_review")],
        },
        {"name": "更新时间", "type": "date"},
    ],
}


@dataclass(frozen=True, slots=True)
class FeishuOutboxEvent:
    event_id: str
    table: str
    stable_id: str
    fields: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FeishuFlushSummary:
    scanned: int = 0
    synced: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FeishuOutbox:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.status_path = path.with_name(f"{path.stem}.status.jsonl")
        self._lock = threading.RLock()

    def enqueue(self, table: str, stable_id: str, fields: dict[str, Any]) -> FeishuOutboxEvent:
        if table not in FEISHU_TABLE_SCHEMAS:
            raise ValueError(f"Unsupported Feishu table: {table}")
        safe_fields = _sanitize_fields(table, fields)
        canonical = json.dumps(
            {"table": table, "stable_id": stable_id, "fields": safe_fields},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        event = FeishuOutboxEvent(
            event_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            table=table,
            stable_id=stable_id,
            fields=safe_fields,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        with self._lock:
            if event.event_id not in {item.event_id for item in self.events()}:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(
                        json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
        return event

    def events(self) -> list[FeishuOutboxEvent]:
        with self._lock:
            if not self.path.exists():
                return []
            events: list[FeishuOutboxEvent] = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                data = json.loads(line)
                events.append(FeishuOutboxEvent(**data))
            return events

    def pending(self) -> list[FeishuOutboxEvent]:
        statuses: dict[str, str] = {}
        if self.status_path.exists():
            for line in self.status_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    data = json.loads(line)
                    statuses[str(data.get("event_id"))] = str(data.get("status"))
        return [event for event in self.events() if statuses.get(event.event_id) != "synced"]

    def mark(self, event: FeishuOutboxEvent, status: str, error: str = "") -> None:
        with self._lock:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "event_id": event.event_id,
                "status": status,
                "error": error[:1000],
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            with self.status_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def flush_outbox(
    outbox: FeishuOutbox,
    writer: Callable[[FeishuOutboxEvent], None],
) -> FeishuFlushSummary:
    pending = outbox.pending()
    summary = FeishuFlushSummary(scanned=len(pending))
    for event in pending:
        try:
            writer(event)
        except Exception as exc:  # noqa: BLE001 - an offline console must not stop generation
            summary.failed += 1
            summary.errors.append(f"{event.event_id}: {exc}")
            outbox.mark(event, "failed", str(exc))
        else:
            summary.synced += 1
            outbox.mark(event, "synced")
    return summary


def _sanitize_fields(table: str, fields: dict[str, Any]) -> dict[str, Any]:
    allowed = {str(item["name"]) for item in FEISHU_TABLE_SCHEMAS[table]}
    result: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in allowed:
            continue
        cleaned = _sanitize_value(value)
        if isinstance(cleaned, (dict, list)):
            cleaned = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))[:1200]
        if isinstance(cleaned, str) and _is_url_field(key):
            cleaned = _strip_url_query(cleaned)
        result[key] = cleaned
    return result


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_sensitive_text(value)[:1200]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, nested in value.items():
            if _is_secret_key(str(key)):
                continue
            safe[str(key)] = _sanitize_value(nested)
        return safe
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    return _redact_sensitive_text(str(value))[:1200]


def _is_secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    aliases = {
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "refresh_token",
        "cookie",
        "cookies",
        "password",
        "secret",
        "token",
        "full_prompt",
        "prompt",
    }
    return normalized in aliases or normalized.endswith(("_api_key", "_access_token", "_secret"))


def _is_url_field(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return "链接" in key or normalized.endswith(("url", "_url", "link", "_link"))


def _strip_url_query(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _redact_sensitive_text(value: str) -> str:
    patterns = (
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}",
        r"\bsk-[A-Za-z0-9_-]{12,}\b",
        r"\bAIza[A-Za-z0-9_-]{20,}\b",
        r"(?i)(__Secure-[A-Za-z0-9_-]+\s*=\s*)[^;\s]+",
        r"(?i)\b(?:access[_-]?token|refresh[_-]?token|authorization|api[_-]?key|cookie|secret|password)\b\s*[:=]\s*[\"']?[^,;\s\"'}]+",
    )
    result = value
    for pattern in patterns:
        result = re.sub(pattern, "[REDACTED]", result)
    return result
