"""RIPPERINIO system prompt loader — Windows build."""

from __future__ import annotations

from pathlib import Path

from app_paths import resource_path

BASE_DIR = Path(__file__).resolve().parent
PROMPT_PATH = resource_path("core", "prompt.txt")

FALLBACK_PROMPT = (
    "Sen RIP'sin (Ripperinio) — Windows'ta çalışan kişisel AI asistanı. "
    "Türkçe konuş. Kısa ve net yanıtlar ver. "
    "Araçları kullanarak görevleri tamamla, asla taklit etme."
)


def load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return FALLBACK_PROMPT


def adapt_prompt(text: str) -> str:
    return str(text or "")
