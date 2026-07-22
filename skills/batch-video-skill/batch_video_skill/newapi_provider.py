from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class NewApiVideoResult:
    task_id: str
    model: str
    video_url: str
    video_path: str
    file_size_bytes: int
    elapsed_sec: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NewApiVideoClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://aihubcc.cc",
        request_json: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] | None = None,
        download_file: Callable[[str, Path], int] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("NewAPI key is required")
        self._api_key = api_key
        base = validate_http_base_url(base_url).rstrip("/")
        self.base_api = base if base.endswith("/v1") else f"{base}/v1"
        self._request_json = request_json or self._default_request_json
        self._download_file = download_file or self._default_download
        self._uses_default_download = download_file is None
        self._sleep = sleep

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        seconds: int,
        output: Path,
        input_reference: str = "",
        task_id: str = "",
        on_submitted: Callable[[str], None] | None = None,
        timeout_sec: int = 1200,
        poll_interval_sec: int = 8,
        max_poll_errors: int = 8,
    ) -> NewApiVideoResult:
        if not task_id and not prompt.strip():
            raise ValueError("A prompt is required for a new NewAPI video task")
        start = time.time()
        if not task_id:
            payload: dict[str, Any] = {
                "model": model,
                "prompt": prompt,
                "seconds": str(seconds),
                "aspect_ratio": "9:16",
                "resolution": "720p",
                "size": "720x1280",
            }
            if input_reference:
                payload["input_reference"] = input_reference
            submitted = self._request_json("POST", f"{self.base_api}/videos", payload)
            task_id = _task_id(submitted)
            if on_submitted:
                on_submitted(task_id)

        deadline = start + max(1, timeout_sec)
        poll_errors = 0
        last: dict[str, Any] = {}
        while time.time() <= deadline:
            if poll_interval_sec:
                self._sleep(poll_interval_sec)
            try:
                last = self._request_json(
                    "GET",
                    f"{self.base_api}/videos/{urllib.parse.quote(task_id, safe='')}",
                    None,
                )
                poll_errors = 0
            except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError):
                poll_errors += 1
                if poll_errors > max_poll_errors:
                    raise
                continue
            status = _status(last)
            if status in {"completed", "succeeded", "success", "done"}:
                url = _video_url(last, self.base_api) or (
                    f"{self.base_api}/videos/{urllib.parse.quote(task_id, safe='')}/content"
                )
                _validate_download_url(
                    url,
                    trusted_origin=self.base_api,
                    resolve_dns=self._uses_default_download,
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                size = self._download_file(url, output)
                return NewApiVideoResult(
                    task_id=task_id,
                    model=model,
                    video_url=url,
                    video_path=str(output),
                    file_size_bytes=size,
                    elapsed_sec=round(time.time() - start, 3),
                )
            if status in {"failed", "error", "cancelled", "canceled"}:
                raise RuntimeError(f"NewAPI task failed: {task_id}; {_safe_json(last)}")
        raise TimeoutError(f"NewAPI task timed out: {task_id}; last={_safe_json(last)}")

    def _default_request_json(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not _same_origin(url, self.base_api):
            raise ValueError("NewAPI request URL must stay on the configured API origin")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - URL is same-origin validated above
            url, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"NewAPI HTTP {exc.code}: {_redact(text, self._api_key)}") from exc
        except (
            urllib.error.URLError,
            ConnectionError,
            TimeoutError,
            http.client.HTTPException,
        ) as exc:
            raise RuntimeError(f"NewAPI network error: {getattr(exc, 'reason', exc)}") from exc
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"NewAPI returned non-JSON: {_redact(text[:500], self._api_key)}"
            ) from exc
        if not isinstance(result, dict):
            raise RuntimeError("NewAPI returned a non-object JSON response")
        return result

    def _default_download(self, url: str, output: Path) -> int:
        return download_http_file(
            url,
            output,
            trusted_origin=self.base_api,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout_sec=300,
            max_bytes=2 * 1024 * 1024 * 1024,
            allowed_content_types=("video/", "application/octet-stream"),
        )


class _SafeDownloadRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, trusted_origin: str) -> None:
        super().__init__()
        self._trusted_origin = trusted_origin

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_download_url(newurl, trusted_origin=self._trusted_origin, resolve_dns=True)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and not _same_origin(newurl, self._trusted_origin):
            redirected.remove_header("Authorization")
        return redirected


def download_http_file(
    url: str,
    output: Path,
    *,
    trusted_origin: str,
    headers: dict[str, str] | None = None,
    timeout_sec: int = 300,
    max_bytes: int = 1024 * 1024 * 1024,
    allowed_content_types: tuple[str, ...] = (),
    verify_response_peer: bool = True,
) -> int:
    _validate_download_url(url, trusted_origin=trusted_origin, resolve_dns=True)
    safe_headers = dict(headers or {})
    if not _same_origin(url, trusted_origin):
        safe_headers.pop("Authorization", None)
    request = urllib.request.Request(  # noqa: S310 - URL is validated above
        url,
        headers=safe_headers,
        method="GET",
    )
    opener = urllib.request.build_opener(_SafeDownloadRedirectHandler(trusted_origin))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".part")
    total = 0
    try:
        with opener.open(request, timeout=timeout_sec) as response:  # noqa: S310
            final_url = response.geturl()
            _validate_download_url(final_url, trusted_origin=trusted_origin, resolve_dns=True)
            if verify_response_peer and not _same_origin(final_url, trusted_origin):
                _validate_response_peer(response)
            content_type = str(response.headers.get_content_type() or "").lower()
            if allowed_content_types and not any(
                content_type.startswith(prefix) for prefix in allowed_content_types
            ):
                raise ValueError(f"Unexpected download content type: {content_type or 'missing'}")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError("Download exceeds configured size limit")
            with temporary.open("wb") as file:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("Download exceeds configured size limit")
                    file.write(chunk)
        temporary.replace(output)
        return total
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _validate_response_peer(response: Any) -> None:
    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is None:
        raise ValueError("Unable to verify cross-origin download peer address")
    address = str(sock.getpeername()[0])
    if not ipaddress.ip_address(address).is_global:
        raise ValueError("Cross-origin download connected to a non-public address")


def validate_http_base_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("NewAPI base URL must use http:// or https:// with a hostname")
    if parsed.username or parsed.password:
        raise ValueError("NewAPI base URL must not contain credentials")
    return url.strip()


def _validate_download_url(url: str, *, trusted_origin: str, resolve_dns: bool) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Video download URL must use http:// or https:// with a hostname")
    if parsed.username or parsed.password:
        raise ValueError("Video download URL must not contain credentials")
    if _same_origin(url, trusted_origin):
        return

    addresses: set[str] = set()
    try:
        addresses.add(str(ipaddress.ip_address(parsed.hostname)))
    except ValueError:
        if resolve_dns:
            try:
                addresses.update(
                    str(result[4][0])
                    for result in socket.getaddrinfo(
                        parsed.hostname, parsed.port, type=socket.SOCK_STREAM
                    )
                )
            except OSError as exc:
                raise ValueError(
                    f"Video download hostname could not be resolved: {parsed.hostname}"
                ) from exc
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("Cross-origin video download URL resolves to a non-public address")


def _same_origin(left: str, right: str) -> bool:
    def origin(value: str) -> tuple[str, str, int | None]:
        parsed = urllib.parse.urlparse(value)
        port = parsed.port or (
            443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
        )
        return parsed.scheme.lower(), (parsed.hostname or "").lower(), port

    return origin(left) == origin(right)


def _task_id(raw: dict[str, Any]) -> str:
    value = raw.get("task_id") or raw.get("id") or raw.get("name")
    if not value and isinstance(raw.get("data"), dict):
        value = raw["data"].get("task_id") or raw["data"].get("id")
    if not value:
        raise RuntimeError(f"NewAPI submit response has no task ID: {_safe_json(raw)}")
    return str(value)


def _status(raw: dict[str, Any]) -> str:
    value = raw.get("status") or raw.get("state")
    if not value and isinstance(raw.get("data"), dict):
        value = raw["data"].get("status") or raw["data"].get("state")
    return str(value or "").lower()


def _video_url(raw: dict[str, Any], base_api: str) -> str | None:
    candidates: list[str] = []
    data = raw.get("data")
    objects = [raw]
    if isinstance(data, dict):
        objects.append(data)
    elif isinstance(data, list):
        objects.extend(item for item in data if isinstance(item, dict))
    for item in objects:
        for key in ("video_url", "url", "output_url", "content_url"):
            value = item.get(key)
            if isinstance(value, str) and value:
                candidates.append(value)
    for value in candidates:
        if value.startswith(("https://", "http://")):
            return value
        if value.startswith("/"):
            parsed = urllib.parse.urlparse(base_api)
            return f"{parsed.scheme}://{parsed.netloc}{value}"
    if candidates:
        raise ValueError("Video download URL must use http:// or https:// with a hostname")
    return None


def _safe_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)[:1200]


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text
