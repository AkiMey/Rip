"""Small logging/runtime helpers shared by RIP background processes."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()
_LAST: dict[str, float] = {}


def configure_utf8_stdio() -> None:
    """Make redirected Windows stdout/stderr Unicode-safe."""
    for stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _rotate(path: Path, max_bytes: int = 2_000_000, backups: int = 3) -> None:
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
        backups = max(1, int(backups))
        path.with_name(path.name + f".{backups}").unlink(missing_ok=True)
        for idx in range(backups, 0, -1):
            src = path if idx == 1 else path.with_name(path.name + f".{idx - 1}")
            dst = path.with_name(path.name + f".{idx}")
            if src.exists():
                src.replace(dst)
    except Exception:
        pass


def append_log(path: Path, text: str, *, max_bytes: int = 2_000_000) -> None:
    """Append UTF-8 text with tiny size-based rotation; logging must never crash RIP."""
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate(path, max_bytes=max_bytes)
            with path.open("a", encoding="utf-8", errors="replace") as fh:
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + str(text).rstrip() + "\n")
    except Exception:
        pass


def should_log(key: str, interval: float = 30.0) -> bool:
    """Simple process-local rate limiter for repetitive connection failures."""
    now = time.monotonic()
    with _LOCK:
        last = _LAST.get(key, 0.0)
        if now - last < interval:
            return False
        _LAST[key] = now
        return True


def is_benign_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return True
    winerror = getattr(exc, "winerror", None)
    errno = getattr(exc, "errno", None)
    return winerror in {64, 10053, 10054} or errno in {32, 54, 104}
