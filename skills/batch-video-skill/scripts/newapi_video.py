#!/usr/bin/env python3
"""Submit and download one video task through an OpenAI-compatible /v1/videos API.

This script is intentionally small and side-effect-light: the API key is read
from an environment variable, never from argv, so it is less likely to leak into
shell history or manifests.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from batch_video_skill.newapi_provider import (
    _SafeDownloadRedirectHandler,
    download_http_file,
    validate_http_base_url,
)

DEFAULT_BASE_URL = "https://aihubcc.cc"
DEFAULT_MODEL = "grok-imagine-video-cli"


def _json_request(
    method: str,
    url: str,
    *,
    api_key: str,
    payload: dict[str, Any] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    validate_http_base_url(url)
    req = urllib.request.Request(  # noqa: S310 - URL scheme and host validated above
        url, data=data, headers=headers, method=method
    )
    opener = urllib.request.build_opener(_SafeDownloadRedirectHandler(url))
    try:
        with opener.open(req, timeout=timeout) as res:
            body = res.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {_redact(body)}") from exc
    except (urllib.error.URLError, ConnectionError, TimeoutError, http.client.HTTPException) as exc:
        reason = getattr(exc, "reason", str(exc))
        raise RuntimeError(f"Network error: {reason}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response: {_redact(body[:500])}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Unexpected JSON response: {type(parsed).__name__}")
    return parsed


def _download(
    url: str,
    *,
    api_key: str,
    output: Path,
    trusted_origin: str,
    timeout: int = 300,
) -> int:
    return download_http_file(
        url,
        output,
        trusted_origin=trusted_origin,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout_sec=timeout,
        max_bytes=2 * 1024 * 1024 * 1024,
        allowed_content_types=("video/", "application/octet-stream"),
    )


def _base(base_url: str) -> str:
    base = validate_http_base_url(base_url).rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def _task_id(raw: dict[str, Any]) -> str:
    value = raw.get("task_id") or raw.get("id") or raw.get("name")
    if not value and isinstance(raw.get("data"), dict):
        value = raw["data"].get("task_id") or raw["data"].get("id")
    if not value:
        raise RuntimeError(f"Submit response has no task id: {_safe_json(raw)}")
    return str(value)


def _status(raw: dict[str, Any]) -> str:
    status = raw.get("status") or raw.get("state")
    if isinstance(raw.get("data"), dict):
        status = status or raw["data"].get("status") or raw["data"].get("state")
    return str(status or "").lower()


def _video_url(raw: dict[str, Any], base_api: str) -> str | None:
    candidates: list[str] = []
    for key in ("video_url", "url", "output_url", "content_url"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)
    data = raw.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                value = item.get("url") or item.get("video_url")
                if isinstance(value, str) and value:
                    candidates.append(value)
    elif isinstance(data, dict):
        for key in ("video_url", "url", "output_url", "content_url"):
            value = data.get(key)
            if isinstance(value, str) and value:
                candidates.append(value)
    for value in candidates:
        if value.startswith("http://") or value.startswith("https://"):
            return value
    for value in candidates:
        if value.startswith("/"):
            parsed = urllib.parse.urlparse(base_api)
            return f"{parsed.scheme}://{parsed.netloc}{value}"
    return None


def _error(raw: dict[str, Any]) -> str:
    err = raw.get("error") or raw.get("message") or raw.get("error_message")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)
    return str(err or "")


def _safe_json(raw: dict[str, Any]) -> str:
    return json.dumps(raw, ensure_ascii=False)[:1200]


def _redact(text: str) -> str:
    secret = os.environ.get("NEWAPI_API_KEY", "")
    return text.replace(secret, "[REDACTED]") if secret else text


def submit_and_wait(args: argparse.Namespace) -> dict[str, Any]:
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise RuntimeError(f"Missing API key env: {args.api_key_env}")
    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8-sig")
    if not args.task_id and (not prompt or not prompt.strip()):
        raise RuntimeError("Missing prompt text")

    base_api = _base(args.base_url)
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "seconds": str(args.seconds),
        "aspect_ratio": args.aspect_ratio,
        "resolution": args.resolution,
    }
    if args.size:
        payload["size"] = args.size
    if args.input_reference:
        payload["input_reference"] = args.input_reference

    if args.task_id:
        task_id = args.task_id
        submit_raw = {"id": task_id, "status": "resuming"}
    else:
        submit_raw = _json_request(
            "POST",
            f"{base_api}/videos",
            api_key=api_key,
            payload=payload,
            timeout=args.submit_timeout,
        )
        task_id = _task_id(submit_raw)
    start = time.time()
    last_raw = submit_raw
    poll_errors = 0

    while True:
        elapsed = time.time() - start
        if elapsed > args.timeout_sec:
            raise TimeoutError(f"Task timed out: {task_id}; last={_safe_json(last_raw)}")
        time.sleep(args.poll_interval)
        try:
            last_raw = _json_request(
                "GET",
                f"{base_api}/videos/{urllib.parse.quote(task_id, safe='')}",
                api_key=api_key,
                timeout=args.poll_timeout,
            )
            poll_errors = 0
        except RuntimeError as exc:
            poll_errors += 1
            if poll_errors > args.max_poll_errors:
                raise
            if args.progress:
                print(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "status": "poll_retry",
                            "poll_error": poll_errors,
                            "elapsed_sec": round(elapsed, 1),
                            "error": _redact(str(exc)),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            continue
        status = _status(last_raw)
        if args.progress:
            print(
                json.dumps(
                    {"task_id": task_id, "status": status, "elapsed_sec": round(elapsed, 1)},
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if status in {"completed", "succeeded", "success", "done"}:
            url = (
                _video_url(last_raw, base_api)
                or f"{base_api}/videos/{urllib.parse.quote(task_id, safe='')}/content"
            )
            download_error: Exception | None = None
            for download_attempt in range(1, args.download_retries + 1):
                try:
                    bytes_written = _download(
                        url,
                        api_key=api_key,
                        output=args.output,
                        trusted_origin=base_api,
                        timeout=args.download_timeout,
                    )
                    break
                except Exception as exc:
                    download_error = exc
                    if download_attempt >= args.download_retries:
                        raise
                    time.sleep(min(10, 2 * download_attempt))
            else:
                assert download_error is not None
                raise download_error
            return {
                "success": True,
                "task_id": task_id,
                "model": args.model,
                "video_url": url,
                "video_path": str(args.output),
                "file_size_bytes": bytes_written,
                "elapsed_sec": round(time.time() - start, 2),
                "status_raw": last_raw,
            }
        if status in {"failed", "error", "cancelled", "canceled"}:
            raise RuntimeError(
                f"Task failed: {task_id}; {_error(last_raw)}; raw={_safe_json(last_raw)}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate one video through /v1/videos")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--prompt-file", default="")
    parser.add_argument(
        "--task-id", default="", help="Resume polling an existing task without submitting again"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-url", default=os.environ.get("NEWAPI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key-env", default="NEWAPI_API_KEY")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--aspect-ratio", default="9:16")
    parser.add_argument("--size", default="720x1280")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--input-reference", default="")
    parser.add_argument("--timeout-sec", type=int, default=900)
    parser.add_argument("--poll-interval", type=int, default=8)
    parser.add_argument("--submit-timeout", type=int, default=120)
    parser.add_argument("--poll-timeout", type=int, default=60)
    parser.add_argument("--download-timeout", type=int, default=300)
    parser.add_argument("--max-poll-errors", type=int, default=8)
    parser.add_argument("--download-retries", type=int, default=3)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)
    result = submit_and_wait(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps({"success": False, "error": _redact(str(exc))}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
