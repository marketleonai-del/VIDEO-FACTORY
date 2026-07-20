from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path


@contextmanager
def prompt_attachment(text: str, *, prefix: str) -> Iterator[Path]:
    root = Path(
        os.environ.get("BATCH_VIDEO_TEMP_DIR", r"D:\tmp\batch-video-skill\prompt-transport")
    )
    root.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f"{prefix}-", suffix=".txt", dir=root)
    os.close(descriptor)
    path = Path(raw_path)
    try:
        path.write_text(text, encoding="utf-8")
        with suppress(OSError):
            path.chmod(0o600)
        yield path
    finally:
        path.unlink(missing_ok=True)
