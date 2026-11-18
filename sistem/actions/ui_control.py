from __future__ import annotations

import re
import time
from typing import Iterable

from actions.platform_utils import IS_WIN

_RISKY_TEXT = re.compile(
    r"(?:sat[iı]n\s*al|purchase|checkout|ödeme|odeme|sil(?:mek|me)?|delete|remove|"
    r"format|factory\s*reset|shutdown|restart|çıkış\s*yap|cikis\s*yap|sign\s*out|log\s*out)",
    re.IGNORECASE,
)


def _confirm(reason: str) -> str:
    return (
        "ONAY GEREKLI: " + reason.strip() + " Kullanıcı bu spesifik işlemi açıkça "
        "onaylarsa aynı aracı confirmed=true ile tekrar çağır."
    )


def _split_keys(keys: str) -> list[str]:
    raw = str(keys or "").replace("+", " ").replace(",", " ")
    return [p.strip().lower() for p in raw.split() if p.strip()]


def ui_control(
    action: str,
    x: int | None = None,
    y: int | None = None,
    text: str = "",
    keys: str = "",
    button: str = "left",
    clicks: int = 1,
    interval: float = 0.05,
    confirmed: bool = False,
) -> str:
    """Direct mouse/keyboard control for the active desktop.

    Semantic app navigation belongs to ui_navigate; this is the narrow direct layer.
    """
    if not IS_WIN:
        return "Genel UI kontrolü yalnızca Windows'ta kullanılabilir."

    act = str(action or "").strip().casefold()
    if act not in {"click", "double_click", "move", "type", "press", "hotkey", "scroll"}:
        return f"Bilinmeyen UI işlemi: {action}"

    descriptor = " ".join([act, str(text or ""), str(keys or "")]).strip()
    if _RISKY_TEXT.search(descriptor) and not confirmed:
        return _confirm("Bu tıklama/yazma komutu ödeme, silme, çıkış veya güç işlemi gibi riskli bir eylem olabilir.")

    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = max(0.0, min(float(interval or 0.05), 0.5))

        if act == "move":
            if x is None or y is None:
                return "move için x ve y gerekli."
            pyautogui.moveTo(int(x), int(y), duration=0.15)
            return f"Fare ({int(x)}, {int(y)}) konumuna taşındı."

        if act in {"click", "double_click"}:
            if x is not None and y is not None:
                pyautogui.moveTo(int(x), int(y), duration=0.12)
            count = 2 if act == "double_click" else max(1, min(int(clicks or 1), 4))
            pyautogui.click(button=str(button or "left").lower(), clicks=count, interval=0.08)
            return f"{count} tıklama yapıldı."

        if act == "type":
            value = str(text or "")
            if not value:
                return "type için text gerekli."
            # pyautogui.write Unicode/Türkçe karakterlerde güvenilir değildir.
            # ASCII dışı metinde Windows Unicode clipboard + Ctrl+V kullan.
            if any(ord(ch) > 127 for ch in value):
                try:
                    import win32clipboard
                    previous = None
                    try:
                        win32clipboard.OpenClipboard()
                        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                            previous = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                        win32clipboard.EmptyClipboard()
                        win32clipboard.SetClipboardText(value, win32clipboard.CF_UNICODETEXT)
                    finally:
                        try: win32clipboard.CloseClipboard()
                        except Exception: pass
                    pyautogui.hotkey("ctrl", "v")
                    time.sleep(0.12)
                    if previous is not None:
                        try:
                            win32clipboard.OpenClipboard()
                            win32clipboard.EmptyClipboard()
                            win32clipboard.SetClipboardText(previous, win32clipboard.CF_UNICODETEXT)
                        finally:
                            try: win32clipboard.CloseClipboard()
                            except Exception: pass
                except Exception:
                    pyautogui.write(value, interval=max(0.0, min(float(interval or 0.03), 0.2)))
            else:
                pyautogui.write(value, interval=max(0.0, min(float(interval or 0.03), 0.2)))
            return "Metin yazıldı."

        if act == "press":
            seq = _split_keys(keys or text)
            if not seq:
                return "press için keys gerekli."
            for key in seq:
                pyautogui.press(key)
            return f"Tuş(lar) basıldı: {' '.join(seq)}"

        if act == "hotkey":
            seq = _split_keys(keys)
            if not seq:
                return "hotkey için keys gerekli."
            pyautogui.hotkey(*seq)
            return f"Kısayol gönderildi: {'+'.join(seq)}"

        if act == "scroll":
            amount = int(y if y is not None else (x if x is not None else 0))
            if amount == 0:
                amount = -5
            pyautogui.scroll(amount)
            return f"Kaydırıldı: {amount}"
    except Exception as exc:
        return f"UI kontrol hatası: {exc}"

    return "UI işlemi tamamlandı."
