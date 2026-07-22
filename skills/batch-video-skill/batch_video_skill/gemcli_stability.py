from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class GemcliCheck:
    ok: bool
    command: list[str]
    output: str


@dataclass(slots=True)
class GemcliPreflightResult:
    ok: bool
    checks: dict[str, Any] = field(default_factory=dict)


def ensure_gemcli_ready(
    gemcli_path: str = "gemcli",
    *,
    profile_name: str = "default",
    timeout: int = 90,
    refresh_on_failure: bool = True,
    check_gems: bool = False,
) -> GemcliPreflightResult:
    checks: dict[str, Any] = {}
    checks["profile_path"] = ensure_chrome_profile_path(profile_name)

    login = run_cmd([gemcli_path, "login", "--check"], timeout=timeout)
    checks["login_check"] = check_to_dict(login)
    if not login.ok and refresh_on_failure:
        refreshed = run_cmd([gemcli_path, "login"], timeout=max(timeout, 180))
        checks["login_refresh"] = check_to_dict(refreshed)
        login = run_cmd([gemcli_path, "login", "--check"], timeout=timeout)
        checks["login_check_after_refresh"] = check_to_dict(login)

    if not login.ok:
        return GemcliPreflightResult(False, checks)

    # A running source-profile daemon can lock Chrome's Cookies database on
    # Windows while Token Factory tries to copy it. Stopping this daemon does
    # not affect the user's normal Chrome session.
    stop = run_cmd([gemcli_path, "chrome", "stop"], timeout=30)
    checks["chrome_stop_best_effort"] = check_to_dict(stop)

    if check_gems:
        gems = run_cmd([gemcli_path, "gems", "list"], timeout=max(timeout, 180))
        checks["gems_list"] = check_to_dict(gems)
        return GemcliPreflightResult(gems.ok, checks)
    checks["gems_list"] = {"skipped": True, "reason": "use explicit --check-gems for a full audit"}
    return GemcliPreflightResult(True, checks)


def ensure_chrome_profile_path(profile_name: str = "default") -> dict[str, Any]:
    base = Path.home() / ".gemini-web-mcp-cli"
    auth_path = base / "profiles" / profile_name / "auth.json"
    chrome_profile = base / "chrome-profiles" / profile_name
    result = {
        "auth_path": str(auth_path),
        "chrome_profile": str(chrome_profile),
        "updated": False,
        "exists": auth_path.exists(),
    }
    if not auth_path.exists() or not chrome_profile.exists():
        return result

    data = json.loads(auth_path.read_text(encoding="utf-8"))
    if not data.get("chrome_profile_path"):
        data["chrome_profile_path"] = str(chrome_profile)
        auth_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        result["updated"] = True
    result["has_chrome_profile_path"] = bool(data.get("chrome_profile_path"))
    return result


def run_cmd(args: list[str], *, timeout: int) -> GemcliCheck:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return GemcliCheck(False, args, f"command not found: {args[0]}")
    except subprocess.TimeoutExpired:
        return GemcliCheck(False, args, f"timeout after {timeout}s")
    output = ((completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")).strip()
    return GemcliCheck(completed.returncode == 0, args, output)


def check_to_dict(check: GemcliCheck) -> dict[str, Any]:
    return {
        "ok": check.ok,
        "command": _safe_command(check.command),
        "output_preview": check.output[:1500],
    }


def _safe_command(command: list[str]) -> list[str]:
    # Commands here do not contain secrets, but keep this helper so future
    # callers do not accidentally leak pasted auth material.
    hidden_next = {"--cookie", "--cookies", "--token", "--api-key", "--password"}
    safe: list[str] = []
    hide = False
    for item in command:
        if hide:
            safe.append("<redacted>")
            hide = False
            continue
        safe.append(item)
        if item in hidden_next:
            hide = True
    return safe
