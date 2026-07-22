"""Build a portable .skill archive for batch-video-skill.

The .skill file is a zip archive whose root folder is batch-video-skill/.
It intentionally excludes caches, logs, runtime outputs, and private config.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ALLOWED_ROOT_FILES = {
    "SKILL.md",
    "HANDOFF.md",
    "README.md",
    "MANIFEST.in",
    "pyproject.toml",
    "config.example.json",
    "config.v4.example.json",
    "mcp-config.example.json",
    "gem-metadata-template.csv",
}
ALLOWED_ROOT_DIRS = {
    "agents",
    "assets",
    "batch_video_skill",
    "references",
    "router840",
    "scripts",
}

DEFAULT_EXCLUDES = [
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
    "output",
    "outputs",
    "downloads",
    "temp",
    "tmp",
    "logs",
    "dist",
    "build",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.lock",
    "*.bak",
    "*.backup",
    "*.egg-info",
    "config.json",
    "mcp-config.json",
    ".env*",
    ".pypirc",
    "credentials*.json",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*cookies*.json",
    "*cookies*.txt",
    "*api-key*.txt",
    "*apikey*.txt",
    "*service-account*.json",
    "gcp-service-account.json",
]


def should_exclude(path: Path, root: Path, excludes: list[str]) -> bool:
    rel = path.relative_to(root).as_posix()
    parts = rel.split("/")
    for pattern in excludes:
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
        if fnmatch.fnmatch(rel, pattern):
            return True
    return False


def is_portable_path(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if len(rel.parts) == 1:
        return rel.name in ALLOWED_ROOT_FILES
    return rel.parts[0] in ALLOWED_ROOT_DIRS


def is_link_or_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def scan_skill_archive(output: Path) -> None:
    patterns = {
        "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "openai-style-key": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
        "google-api-key": re.compile(rb"\bAIza[A-Za-z0-9_-]{20,}\b"),
        "gemini-cookie-value": re.compile(
            rb"__Secure-1PSID(?:TS|CC)?\s*[:=]\s*[\"']?[A-Za-z0-9._~-]{20,}"
        ),
    }
    findings: list[str] = []
    with ZipFile(output) as archive:
        for info in archive.infolist():
            if info.file_size > 20 * 1024 * 1024:
                continue
            content = archive.read(info)
            for label, pattern in patterns.items():
                if pattern.search(content):
                    findings.append(f"{info.filename}: {label}")
    if findings:
        output.unlink(missing_ok=True)
        raise RuntimeError("Sensitive content detected in .skill archive: " + "; ".join(findings))


def build_skill_archive(root: Path, output: Path, excludes: list[str]) -> int:
    root = root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    included = 0
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            if is_link_or_reparse_point(file_path):
                continue
            resolved = file_path.resolve()
            if not resolved.is_relative_to(root):
                continue
            if resolved == output:
                continue
            if not is_portable_path(file_path, root):
                continue
            if should_exclude(file_path, root, excludes):
                continue

            rel = file_path.relative_to(root).as_posix()
            archive_name = f"batch-video-skill/{rel}"
            archive.write(file_path, archive_name)
            included += 1
    scan_skill_archive(output)
    return included


def main() -> int:
    parser = argparse.ArgumentParser(description="Build batch-video-skill.skill")
    parser.add_argument(
        "--root",
        default=Path(__file__).resolve().parents[1],
        type=Path,
        help="Skill root directory",
    )
    parser.add_argument(
        "--output",
        default=Path("D:/skills/batch-video-skill.skill"),
        type=Path,
        help="Output .skill archive path",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Additional exclude glob pattern",
    )
    args = parser.parse_args()

    excludes = DEFAULT_EXCLUDES + list(args.exclude)
    included = build_skill_archive(args.root, args.output, excludes)
    print(f"built={args.output}")
    print(f"included_files={included}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
