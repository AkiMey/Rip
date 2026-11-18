from __future__ import annotations

import os
import time

from actions.phone_commands import get_phone_clipboard, last_command_ack, queue_clipboard_to_phone


def _pc_clipboard_get() -> str:
    if os.name == "nt":
        try:
            import win32clipboard
            for _ in range(5):
                try:
                    win32clipboard.OpenClipboard()
                    try:
                        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                            return str(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) or "")
                    finally:
                        win32clipboard.CloseClipboard()
                    break
                except Exception:
                    time.sleep(0.05)
        except Exception:
            pass
    return ""


def _pc_clipboard_set(text: str) -> bool:
    if os.name == "nt":
        try:
            import win32clipboard
            for _ in range(5):
                try:
                    win32clipboard.OpenClipboard()
                    try:
                        win32clipboard.EmptyClipboard()
                        win32clipboard.SetClipboardText(str(text), win32clipboard.CF_UNICODETEXT)
                    finally:
                        win32clipboard.CloseClipboard()
                    return True
                except Exception:
                    time.sleep(0.05)
        except Exception:
            return False
    return False


def clipboard_sync(action: str = "send", text: str = "") -> str:
    """Command-only clipboard sync. No continuous background clipboard spying."""
    key = str(action or "send").strip().casefold().replace("-", "_")

    if key in {"send", "push", "to_phone", "telefona", "send_to_phone"}:
        value = str(text or "")
        if not value:
            value = _pc_clipboard_get()
        if not value.strip():
            return "PC panosu boş; telefona gönderilecek metin yok."
        cmd = queue_clipboard_to_phone(value)
        command_id = str(cmd.get("id") or "")
        # V2: Bridge servisi bagliysa gercek ACK bekle. Bagli degilse komut
        # kuyrukta kalir; kullaniciya teslim edilmis gibi davranmayiz.
        deadline = time.monotonic() + 8.0
        ack = {}
        while time.monotonic() < deadline:
            ack = last_command_ack(command_id)
            if ack:
                break
            time.sleep(0.20)
        if ack:
            status = str(ack.get("status") or "").casefold()
            detail = str(ack.get("detail") or "")
            if status in {"ok", "done", "success"}:
                return f"Telefon panosu güncellendi ✓ {detail}".strip()
            return f"Telefon pano aktarımı başarısız: {detail or status}"
        return (
            f"Telefon şu an ACK vermedi; pano komutu kuyrukta ({command_id[:8]}). "
            "Bridge servisi bağlanınca teslim edilecek."
        )

    if key in {"pull", "get", "from_phone", "telefondan", "get_from_phone"}:
        row = get_phone_clipboard()
        value = str(row.get("text") or "")
        if not value:
            return (
                "Telefondan paylaşılmış pano yok. Android 10+ arka planda pano okumayı kısıtlıyor; "
                "RIP Phone Bridge uygulamasında 'PANOMU RIP'E GÖNDER' düğmesine bir kez dokun."
            )
        stamp = int(row.get("received_at", 0) or 0)
        age = max(0, int(time.time() - stamp / 1000)) if stamp else -1
        copied = _pc_clipboard_set(value)
        suffix = " PC panosuna da kopyaladım." if copied else ""
        return f"Telefon panosu ({age if age >= 0 else '?'} sn önce): {value[:1200]}{suffix}"

    if key in {"set_pc", "pc", "computer"}:
        if not str(text or ""):
            return "PC panosuna yazılacak metin boş."
        return "PC panosu güncellendi." if _pc_clipboard_set(str(text)) else "PC panosu güncellenemedi."

    return f"Bilinmeyen pano senkron işlemi: {action}"
