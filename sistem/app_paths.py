"""RIPPERINIO resource/data path separation.

Source files are treated as read-only resources. User data and secrets live outside
of the source tree by default so a source ZIP can be shared safely.

Environment overrides:
  RIPPERINIO_DATA_DIR=<path>   Explicit writable data directory.
  RIPPERINIO_PORTABLE=1        Keep writable data next to the source/executable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
IS_FROZEN = bool(getattr(sys, "frozen", False))
APP_NAME = "RIPPERINIO"
_data_dir_cache: Path | None = None


def resource_dir() -> Path:
    if IS_FROZEN:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return PROJECT_DIR


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".ripperinio_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _default_user_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def data_dir() -> Path:
    """Return the writable application-data root.

    V5 deliberately does *not* write secrets into the source tree unless
    RIPPERINIO_PORTABLE=1 is explicitly requested.
    """
    global _data_dir_cache
    if _data_dir_cache is not None:
        return _data_dir_cache

    override = str(os.environ.get("RIPPERINIO_DATA_DIR", "") or "").strip()
    if override:
        candidate = Path(override).expanduser()
        if not _is_writable(candidate):
            raise OSError(f"RIPPERINIO_DATA_DIR yazilabilir degil: {candidate}")
        _data_dir_cache = candidate
        return candidate

    portable = str(os.environ.get("RIPPERINIO_PORTABLE", "") or "").strip().casefold() in {
        "1", "true", "yes", "on"
    }
    if portable:
        candidate = Path(sys.executable).resolve().parent if IS_FROZEN else PROJECT_DIR
        if _is_writable(candidate):
            _data_dir_cache = candidate
            return candidate

    candidate = _default_user_data_dir()
    candidate.mkdir(parents=True, exist_ok=True)
    _data_dir_cache = candidate
    return candidate


def resource_path(*parts: str) -> Path:
    return resource_dir().joinpath(*parts)


def data_path(*parts: str) -> Path:
    target = data_dir().joinpath(*parts)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return target


def describe() -> str:
    return f"calisma bicimi={run_mode()}\n  kaynak: {resource_dir()}\n  veri  : {data_dir()}"


def run_mode() -> str:
    if not IS_FROZEN:
        return "kaynak"
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return "exe (klasor)"
    exe_dir = Path(sys.executable).resolve().parent
    try:
        if Path(meipass).resolve().parent == exe_dir:
            return "exe (klasor)"
    except Exception:
        pass
    return "exe (tek dosya)"
