from __future__ import annotations

from app_config import get_app_config_value, save_app_config

THEMES = {
    "classic": {
        "label": "Klasik",
        "bg": "#070B14", "pri": "#5EEBFF", "mid": "#7E91AF", "dim": "#1D2B42",
        "dimmer": "#0A1220", "text": "#F5F8FF", "panel": "#0E1728", "secondary": "#A58BFF",
        "listening": "#55F2C2", "speaking": "#6DA8FF",
    },
    "cyan": {
        "label": "Parlak Cyan",
        "bg": "#020a10", "pri": "#00eaff", "mid": "#0b6f80", "dim": "#0a2b35",
        "dimmer": "#06141a", "text": "#a7f7ff", "panel": "#031118", "secondary": "#4a9cff",
        "listening": "#25ffc7", "speaking": "#56a9ff",
    },
    "pink": {
        "label": "Pembe",
        "bg": "#10040b", "pri": "#ff4fb3", "mid": "#7f285c", "dim": "#351126",
        "dimmer": "#1c0915", "text": "#ffd0eb", "panel": "#150710", "secondary": "#bd63ff",
        "listening": "#ff6ec7", "speaking": "#b677ff",
    },
    "purple": {
        "label": "Mor",
        "bg": "#090515", "pri": "#a66cff", "mid": "#56377f", "dim": "#241638",
        "dimmer": "#130b20", "text": "#e0ccff", "panel": "#0f091a", "secondary": "#6f8fff",
        "listening": "#bf82ff", "speaking": "#6f8fff",
    },
    "blue": {
        "label": "Mavi",
        "bg": "#030914", "pri": "#41a5ff", "mid": "#245781", "dim": "#102b45",
        "dimmer": "#081626", "text": "#b8ddff", "panel": "#06101d", "secondary": "#6b7cff",
        "listening": "#5dd7ff", "speaking": "#6b7cff",
    },
    "green": {
        "label": "Yeşil",
        "bg": "#031009", "pri": "#42ee88", "mid": "#237a4c", "dim": "#113a25",
        "dimmer": "#091f14", "text": "#baffd4", "panel": "#07160e", "secondary": "#20c7b5",
        "listening": "#52ff9b", "speaking": "#38c9c4",
    },
}

ALIASES = {
    "klasik": "classic", "classic": "classic",
    "cyan": "cyan", "parlak cyan": "cyan",
    "pembe": "pink", "pink": "pink",
    "mor": "purple", "purple": "purple",
    "mavi": "blue", "blue": "blue",
    "yesil": "green", "yeşil": "green", "green": "green",
}


def normalize_theme(name: str) -> str:
    key = str(name or "classic").strip().casefold()
    return ALIASES.get(key, key if key in THEMES else "")


def get_hud_theme() -> str:
    key = normalize_theme(str(get_app_config_value("hud_theme", "classic") or "classic"))
    return key or "classic"


def get_hud_theme_palette(name: str | None = None) -> dict:
    key = normalize_theme(name or get_hud_theme()) or "classic"
    return dict(THEMES[key])


def set_hud_theme(name: str) -> str:
    key = normalize_theme(name)
    if not key:
        return "Bilinmeyen tema. Seçenekler: Klasik, Parlak Cyan, Pembe, Mor, Mavi, Yeşil."
    save_app_config({"hud_theme": key})
    return f"HUD teması {THEMES[key]['label']} olarak değiştirildi."
