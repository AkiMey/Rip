from __future__ import annotations

import json
import os
from pathlib import Path

from app_paths import data_path, resource_path

CONFIG_PATH = data_path("config", "api_keys.json")
CONFIG_DIR = CONFIG_PATH.parent
BASE_DIR = CONFIG_DIR.parent

DEFAULT_CONFIG = {
    "gemini_api_key": "",
    "voice": "Charon",
    "youtube_api_key": "",
    "youtube_channel_handle": "",
    "calendar_backend": "auto",
    # Secure-by-default: remote/public exposure must be explicitly enabled.
    "weather_location": "",
    "health_export_dir": "",
    "voice_input_mode": "wake",  # continuous | ptt | wake
    "wake_phrase": "hey",
    "hud_theme": "classic",
    "hud_widgets_visible": True,
    "hud_widget_positions": {},
    "hud_widget_layout": {},
    "obs_host": "127.0.0.1",
    "obs_port": 4455,
    "obs_password": "",
}


def _read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _migrate_legacy_source_config() -> None:
    """One-time migration from pre-V5 source-tree config, if present.

    The V5 source package does not contain that file. This only helps users who
    drop V5 over an existing tree instead of starting from a clean archive.
    """
    if CONFIG_PATH.exists():
        return
    legacy = resource_path("config", "api_keys.json")
    try:
        if legacy.resolve() == CONFIG_PATH.resolve() or not legacy.is_file():
            return
    except Exception:
        return
    raw = _read_json(legacy)
    if not raw:
        return
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(raw, indent=4, ensure_ascii=False), encoding="utf-8")


_WAKE_HEY_MIGRATION_KEY = "wake_hey_single_migrated_v1"

def _migrate_single_hey_mode() -> dict:
    """One-time V6.2.1g migration: make HEY the active wake mode.

    Existing installs often persisted `continuous`, which means saying the wake
    phrase can never open a wake session. Apply the requested HEY mode once,
    then keep the marker so later user-selected Continuous/PTT choices persist.
    """
    raw = _read_json(CONFIG_PATH)
    if bool(raw.get(_WAKE_HEY_MIGRATION_KEY, False)):
        return raw
    migrated = dict(raw)
    migrated["voice_input_mode"] = "wake"
    migrated["wake_phrase"] = "hey"
    migrated[_WAKE_HEY_MIGRATION_KEY] = True
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(migrated, indent=4, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except Exception:
            pass
        return migrated
    except Exception:
        # Even if persistence fails, this run still starts in requested wake mode.
        return migrated


def load_app_config() -> dict:
    _migrate_legacy_source_config()
    config = dict(DEFAULT_CONFIG)
    raw = _migrate_single_hey_mode()
    if raw:
        config.update(raw)

    # Environment variables are useful for CI/portable use and avoid writing keys.
    env_gemini = str(os.environ.get("GEMINI_API_KEY", "") or "").strip()
    if env_gemini:
        config["gemini_api_key"] = env_gemini
    env_youtube = str(os.environ.get("YOUTUBE_API_KEY", "") or "").strip()
    if env_youtube:
        config["youtube_api_key"] = env_youtube
    return config


def save_app_config(updates: dict) -> dict:
    # Do not persist environment-only secrets (e.g. GEMINI_API_KEY) when the
    # user merely changes a UI preference. Save starts from file-backed config.
    _migrate_legacy_source_config()
    config = dict(DEFAULT_CONFIG)
    config.update(_read_json(CONFIG_PATH))
    for key, value in (updates or {}).items():
        if value is None:
            continue
        config[key] = value
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=4, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass
    return config


def get_app_config_value(key: str, default=None):
    return load_app_config().get(key, default)


def has_gemini_api_key() -> bool:
    value = str(get_app_config_value("gemini_api_key", "") or "").strip()
    return bool(value)
