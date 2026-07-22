from __future__ import annotations

import mimetypes
import queue
import re
import secrets
import subprocess
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class ReferenceHostError(RuntimeError):
    """Raised when a strict reference asset host cannot be established."""


class WhitelistAssetServer:
    def __init__(self, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._prefix = secrets.token_urlsafe(24)
        self._routes: dict[str, Path] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def local_base_url(self) -> str:
        if not self._server:
            raise ReferenceHostError("Whitelist asset server is not started")
        return f"http://{self.host}:{self._server.server_port}"

    def start(self) -> None:
        if self._server:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                route = urllib.parse.urlparse(self.path).path
                with owner._lock:
                    file_path = owner._routes.get(route)
                if not file_path or not file_path.exists() or not file_path.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(file_path.stat().st_size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with file_path.open("rb") as file:
                    while chunk := file.read(1024 * 1024):
                        self.wfile.write(chunk)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def register(self, path: Path) -> str:
        resolved = path.expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"Reference asset does not exist: {resolved}")
        digest = secrets.token_urlsafe(18)
        route = f"/{self._prefix}/{digest}/{urllib.parse.quote(resolved.name)}"
        with self._lock:
            self._routes[route] = resolved
        return f"{self.local_base_url}{route}"

    def close(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
        with self._lock:
            self._routes.clear()


class CloudflareQuickTunnel:
    URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)

    def __init__(
        self,
        executable: str | Path,
        *,
        startup_timeout_sec: int = 30,
    ) -> None:
        self.executable = str(executable)
        self.startup_timeout_sec = startup_timeout_sec
        self.server = WhitelistAssetServer()
        self.process: subprocess.Popen[str] | None = None
        self.public_base_url = ""

    def start(self) -> None:
        executable = Path(self.executable)
        if not executable.exists():
            raise ReferenceHostError(f"cloudflared not found: {executable}")
        self.server.start()
        command = [
            str(executable),
            "tunnel",
            "--url",
            self.server.local_base_url,
            "--no-autoupdate",
        ]
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines: queue.Queue[str] = queue.Queue()

        def collect(stream: object) -> None:
            if not stream:
                return
            for line in stream:  # type: ignore[union-attr]
                lines.put(str(line))

        for stream in (self.process.stdout, self.process.stderr):
            threading.Thread(target=collect, args=(stream,), daemon=True).start()
        deadline = time.time() + self.startup_timeout_sec
        captured: list[str] = []
        while time.time() < deadline:
            if self.process.poll() is not None:
                break
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                continue
            captured.append(line)
            match = self.URL_PATTERN.search(line)
            if match:
                self.public_base_url = match.group(0).rstrip("/")
                return
        self.close()
        detail = "".join(captured)[-1500:]
        raise ReferenceHostError(f"Cloudflare Quick Tunnel did not become ready: {detail}")

    def register(self, path: Path) -> str:
        local_url = self.server.register(path)
        if not self.public_base_url:
            raise ReferenceHostError("Cloudflare Quick Tunnel is not started")
        parsed = urllib.parse.urlparse(local_url)
        return f"{self.public_base_url}{parsed.path}"

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        self.public_base_url = ""
        self.server.close()

    def __enter__(self) -> CloudflareQuickTunnel:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
