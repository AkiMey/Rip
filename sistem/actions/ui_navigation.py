from __future__ import annotations

import json
import os
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from app_config import get_app_config_value
from actions.platform_utils import IS_WIN


# Purchases/destructive/security-affecting clicks are not executed from a vague
# navigation request. Normal tabs/buttons such as Library, Store, Settings, Chat,
# Search, Downloads etc. do NOT require confirmation.
RISKY_TARGET_PATTERNS = (
    r"\bbuy\b", r"\bpurchase\b", r"\bcheckout\b", r"\bpay\b",
    r"sat[iı]n\s*al", r"ödeme", r"odeme", r"sipariş", r"siparis",
    r"\bdelete\b", r"\bremove\b", r"\buninstall\b", r"\berase\b",
    r"sil(?:me|mek)?", r"kald[iı]r", r"format", r"factory\s*reset",
    r"\bshutdown\b", r"\brestart\b", r"\bsign\s*out\b", r"\blog\s*out\b",
    r"hesab[iı].*sil", r"güvenlik.*kapat", r"guvenlik.*kapat",
)

INTERACTIVE_TYPES = {
    "Button", "TabItem", "Hyperlink", "MenuItem", "ListItem", "TreeItem",
    "Edit", "ComboBox", "CheckBox", "RadioButton", "DataItem", "Text",
}


def _normalize(text: str) -> str:
    table = str.maketrans({
        "ı": "i", "İ": "i", "I": "i",
        "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
        "ü": "u", "Ü": "u", "ö": "o", "Ö": "o",
        "ç": "c", "Ç": "c",
    })
    value = str(text or "").casefold().translate(table)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _needs_confirmation(target: str) -> bool:
    normalized = _normalize(target)
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in RISKY_TARGET_PATTERNS)


def _confirmation(target: str) -> str:
    return (
        f"ONAY GEREKLI: '{target}' etiketli öğe ödeme/silme/çıkış veya başka "
        "riskli bir işlem olabilir. Kullanıcı bu spesifik tıklamayı açıkça "
        "onaylarsa aynı aracı confirmed=true ile tekrar çağır."
    )


def _find_hwnd(window_title: str = "") -> tuple[int, str]:
    import win32gui

    needle = _normalize(window_title)
    if not needle:
        hwnd = int(win32gui.GetForegroundWindow() or 0)
        title = str(win32gui.GetWindowText(hwnd) or "") if hwnd else ""
        return hwnd, title

    candidates: list[tuple[int, str]] = []

    def cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            if title and needle in _normalize(title):
                candidates.append((int(hwnd), title))
        except Exception:
            pass

    win32gui.EnumWindows(cb, None)
    if not candidates:
        return 0, ""

    # Prefer the shortest matching title; it is usually the main app window.
    candidates.sort(key=lambda item: len(item[1]))
    return candidates[0]


def _focus_hwnd(hwnd: int) -> None:
    if not hwnd:
        return
    try:
        import win32con
        import win32gui

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            win32gui.BringWindowToTop(hwnd)
        except Exception:
            pass
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        time.sleep(0.12)
    except Exception:
        pass


def _score(target: str, name: str, control_type: str) -> float:
    t = _normalize(target)
    n = _normalize(name)
    if not t or not n:
        return -1.0

    if n == t:
        score = 1000.0
    elif n.startswith(t):
        score = 900.0
    elif t in n:
        score = 820.0
    elif all(part in n for part in t.split()):
        score = 730.0
    else:
        ratio = SequenceMatcher(None, t, n).ratio()
        if ratio < 0.58:
            return -1.0
        score = 400.0 + ratio * 200.0

    priorities = {
        "TabItem": 80, "Button": 75, "Hyperlink": 70, "MenuItem": 70,
        "ListItem": 55, "TreeItem": 55, "Edit": 45, "ComboBox": 45,
        "CheckBox": 40, "RadioButton": 40, "DataItem": 25, "Text": 5,
    }
    return score + priorities.get(control_type, 0)


def _control_name(wrapper: Any) -> str:
    try:
        text = str(wrapper.window_text() or "").strip()
        if text:
            return text
    except Exception:
        pass
    try:
        return str(wrapper.element_info.name or "").strip()
    except Exception:
        return ""


def _control_type(wrapper: Any) -> str:
    try:
        return str(wrapper.element_info.control_type or "").strip()
    except Exception:
        return ""


def _uia_window(hwnd: int):
    from pywinauto import Desktop
    return Desktop(backend="uia").window(handle=hwnd)


def _uia_candidates(hwnd: int, target: str = "") -> list[tuple[float, Any, str, str]]:
    window = _uia_window(hwnd)
    rows: list[tuple[float, Any, str, str]] = []

    try:
        descendants = window.descendants()
    except Exception:
        descendants = []

    for wrapper in descendants:
        name = _control_name(wrapper)
        ctype = _control_type(wrapper)
        if not name or ctype not in INTERACTIVE_TYPES:
            continue
        score = _score(target, name, ctype) if target else 0.0
        if target and score < 0:
            continue
        rows.append((score, wrapper, name, ctype))

    rows.sort(key=lambda row: row[0], reverse=True)
    return rows


def _uia_inspect(hwnd: int, query: str = "", limit: int = 60) -> str:
    rows = _uia_candidates(hwnd, query)
    if not rows:
        return "UI Automation üzerinden görünür/erişilebilir kontrol bulunamadı."

    rendered = []
    for _, wrapper, name, ctype in rows[: max(1, min(int(limit or 60), 100))]:
        try:
            rect = wrapper.rectangle()
            geom = f"{rect.left},{rect.top} {rect.width()}x{rect.height()}"
        except Exception:
            geom = "?"
        rendered.append(f"{ctype} | {name} | {geom}")
    return "\n".join(rendered)


def _uia_click(hwnd: int, target: str, action: str) -> tuple[bool, str]:
    rows = _uia_candidates(hwnd, target)
    if not rows:
        return False, "UIA eşleşmesi bulunamadı."

    score, wrapper, name, ctype = rows[0]
    if score < 500:
        return False, f"UIA eşleşmesi yeterince güvenilir değil: {name}"

    try:
        try:
            wrapper.scroll_into_view()
        except Exception:
            pass

        key = _normalize(action)
        if key in {"select", "select_tab", "tab"}:
            try:
                wrapper.select()
            except Exception:
                wrapper.click_input()
        elif key in {"double_click", "double"}:
            wrapper.double_click_input()
        elif key in {"right_click", "right"}:
            wrapper.right_click_input()
        else:
            # click_input is more reliable than invoke() for Chromium/Steam UIA.
            wrapper.click_input()

        return True, f"'{name}' öğesine tıklandı ({ctype}, UI Automation)."
    except Exception as exc:
        return False, f"UIA tıklaması başarısız: {exc}"


def _uia_type(hwnd: int, target: str, text: str, clear_first: bool = True) -> tuple[bool, str]:
    from pywinauto.keyboard import send_keys

    wrapper = None
    if target:
        rows = _uia_candidates(hwnd, target)
        if rows and rows[0][0] >= 500:
            wrapper = rows[0][1]

    try:
        if wrapper is not None:
            try:
                wrapper.click_input()
            except Exception:
                wrapper.set_focus()
        if clear_first:
            send_keys("^a{BACKSPACE}")
        # with_spaces=True preserves ordinary spaces; braces are escaped by
        # pywinauto if passed literally through this helper.
        send_keys(str(text or ""), with_spaces=True, pause=0.01)
        return True, "Metin girildi."
    except Exception as exc:
        return False, f"Metin girişi başarısız: {exc}"


def _send_hotkey(keys: str) -> str:
    from pywinauto.keyboard import send_keys

    raw = str(keys or "").strip()
    if not raw:
        return "Kısayol belirtilmedi."

    mapping = {
        "ctrl": "^", "control": "^", "alt": "%", "shift": "+",
        "win": "{LWIN}", "windows": "{LWIN}",
        "enter": "{ENTER}", "esc": "{ESC}", "escape": "{ESC}",
        "tab": "{TAB}", "space": "{SPACE}", "backspace": "{BACKSPACE}",
        "delete": "{DELETE}", "up": "{UP}", "down": "{DOWN}",
        "left": "{LEFT}", "right": "{RIGHT}", "home": "{HOME}", "end": "{END}",
    }

    parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", raw) if p.strip()]
    if not parts:
        return "Kısayol çözümlenemedi."

    sequence = ""
    normal_keys = []
    for part in parts:
        if part in {"ctrl", "control", "alt", "shift"}:
            sequence += mapping[part]
        elif part in mapping:
            normal_keys.append(mapping[part])
        elif len(part) == 1:
            normal_keys.append(part)
        elif re.fullmatch(r"f(?:[1-9]|1[0-2])", part):
            normal_keys.append("{" + part.upper() + "}")
        else:
            normal_keys.append(part)

    sequence += "".join(normal_keys)
    try:
        send_keys(sequence, with_spaces=True)
        return f"Kısayol gönderildi: {raw}"
    except Exception as exc:
        return f"Kısayol gönderilemedi: {exc}"


def _window_scroll_point(hwnd: int, target: str = "") -> tuple[int, int]:
    """Return a screen coordinate INSIDE the target app's content area."""
    import win32gui

    # If the user named a particular scroll region/control, put the pointer in
    # the center of that control first.
    if target:
        try:
            rows = _uia_candidates(hwnd, target)
            if rows:
                rect = rows[0][1].rectangle()
                if rect.width() > 20 and rect.height() > 20:
                    return (
                        int(rect.left + rect.width() * 0.5),
                        int(rect.top + rect.height() * 0.5),
                    )
        except Exception:
            pass

    # IMPORTANT: pywinauto.mouse.scroll() defaults to screen coordinate (0, 0)
    # when coords isn't supplied. V1 was therefore sending the wheel to the
    # desktop/top-left instead of Steam/Discord/etc.
    try:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        sx, sy = win32gui.ClientToScreen(hwnd, (left, top))
        ex, ey = win32gui.ClientToScreen(hwnd, (right, bottom))
        width = max(1, ex - sx)
        height = max(1, ey - sy)

        # Slightly right/below center avoids most left navigation sidebars and
        # title/header bars while staying inside the main content view.
        return (
            int(sx + width * 0.62),
            int(sy + height * 0.58),
        )
    except Exception:
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            return (int((left + right) / 2), int((top + bottom) / 2))
        except Exception:
            return (500, 500)


def _uia_scroll(hwnd: int, direction: str, count: int, target: str = "") -> tuple[bool, str]:
    """Try the native UI Automation ScrollPattern before mouse-wheel fallback."""
    try:
        window = _uia_window(hwnd)
        controls = []

        if target:
            try:
                controls.extend(row[1] for row in _uia_candidates(hwnd, target))
            except Exception:
                pass

        try:
            controls.extend(window.descendants())
        except Exception:
            pass

        # Larger central controls are more likely to be the actual page/content
        # viewport than a tiny sidebar/list.
        ranked = []
        seen = set()
        for wrapper in controls:
            try:
                runtime_id = tuple(wrapper.element_info.runtime_id or ())
            except Exception:
                runtime_id = (id(wrapper),)
            if runtime_id in seen:
                continue
            seen.add(runtime_id)

            try:
                rect = wrapper.rectangle()
                area = max(0, rect.width()) * max(0, rect.height())
                if area < 3000:
                    continue
                ctype = _control_type(wrapper)
                bonus = 0
                if ctype in {"Document", "Pane", "List", "Tree", "DataGrid", "Custom"}:
                    bonus = 10_000_000
                ranked.append((area + bonus, wrapper, ctype))
            except Exception:
                continue

        ranked.sort(key=lambda item: item[0], reverse=True)
        direction_key = "up" if _normalize(direction) in {"up", "yukari", "yukarı"} else "down"

        for _, wrapper, ctype in ranked[:25]:
            try:
                # UIA scroll supports direction = up/down and amount = line/page.
                wrapper.scroll(
                    direction=direction_key,
                    amount="line",
                    count=max(1, min(count, 12)),
                    retry_interval=0.03,
                )
                return True, f"Sayfa {direction_key} kaydırıldı (UI Automation / {ctype})."
            except Exception:
                continue
    except Exception:
        pass

    return False, "UIA scroll pattern bulunamadı."


def _scroll(
    hwnd: int,
    direction: str,
    amount: int = 5,
    target: str = "",
    method: str = "auto",
) -> str:
    from pywinauto import mouse

    count = max(1, min(abs(int(amount or 5)), 30))
    direction_key = "up" if _normalize(direction) in {"up", "yukari", "yukarı"} else "down"
    method_key = _normalize(method or "auto")

    # 1) Native UIA scroll when available. This is especially good for normal
    # Windows controls and doesn't depend on the pointer position.
    if method_key in {"auto", "uia", "ui automation"}:
        ok, message = _uia_scroll(hwnd, direction_key, count, target)
        if ok:
            return message
        if method_key in {"uia", "ui automation"}:
            return message

    # 2) Real mouse-wheel fallback for Steam/Chromium/Electron/custom canvases.
    # The crucial V1.1 fix is the explicit coordinate INSIDE the target window.
    x, y = _window_scroll_point(hwnd, target)
    wheel = count if direction_key == "up" else -count
    try:
        mouse.move(coords=(x, y))
        time.sleep(0.05)
        mouse.scroll(coords=(x, y), wheel_dist=wheel)
        return (
            f"Sayfa {'yukarı' if wheel > 0 else 'aşağı'} kaydırıldı "
            f"(mouse wheel @ {x},{y})."
        )
    except Exception as exc:
        # 3) Last-resort keyboard paging. Since the target window was focused by
        # _ui_action_local, PageUp/PageDown is often accepted by embedded web UIs.
        try:
            from pywinauto.keyboard import send_keys
            key = "{PGUP}" if direction_key == "up" else "{PGDN}"
            repeats = max(1, min((count + 4) // 5, 5))
            for _ in range(repeats):
                send_keys(key)
                time.sleep(0.05)
            return f"Sayfa {direction_key} kaydırıldı (PageUp/PageDown fallback)."
        except Exception as key_exc:
            return f"Kaydırma başarısız: mouse={exc}; keyboard={key_exc}"


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _vision_locate_and_click(target: str, action: str) -> tuple[bool, str]:
    # Reuse Ripperinio's existing active-window screenshot implementation.
    from actions.screen_vision import _build_image_part, _capture_active_window

    ok, detail, payload = _capture_active_window()
    if not ok or not payload:
        return False, detail or "Aktif pencere görüntüsü alınamadı."

    image_path = Path(str(payload.get("image_path") or ""))
    bounds = dict(payload.get("bounds") or {})

    try:
        api_key = str(get_app_config_value("gemini_api_key", "") or "").strip()
        if not api_key:
            return False, "Vision fallback için Gemini API anahtarı gerekli."

        prompt = f"""
You are locating ONE clickable desktop UI element in a screenshot.
Target visible label or meaning: {target!r}

Return ONLY compact JSON in this exact shape:
{{"found":true,"x":0.50,"y":0.50,"label":"visible text","confidence":0.90}}

Rules:
- x and y are normalized 0..1 coordinates of the CENTER of the intended clickable element relative to the screenshot.
- Find a normal navigation tab/button/menu/link matching the target.
- Do not guess. If not clearly visible, return {{"found":false,"x":0,"y":0,"label":"","confidence":0}}.
- Prefer the actual clickable label/button area, not nearby descriptive text.
- No markdown and no explanation.
""".strip()

        client = genai.Client(api_key=api_key)
        image_part = _build_image_part(image_path)
        response = client.models.generate_content(
            model="models/gemini-2.5-flash-lite",
            contents=[types.Part.from_text(text=prompt), image_part],
            config=types.GenerateContentConfig(temperature=0.0),
        )

        response_text = str(getattr(response, "text", "") or "")
        data = _extract_json(response_text)
        if not data or not bool(data.get("found")):
            return False, f"Vision hedefi ekranda bulamadı: {target}"

        confidence = float(data.get("confidence", 0.0) or 0.0)
        x_norm = float(data.get("x", -1.0) or -1.0)
        y_norm = float(data.get("y", -1.0) or -1.0)
        label = str(data.get("label", "") or target)
        if confidence < 0.60 or not (0.0 <= x_norm <= 1.0 and 0.0 <= y_norm <= 1.0):
            return False, f"Vision eşleşmesi yeterince güvenilir değil ({confidence:.2f})."

        if _needs_confirmation(label):
            return False, _confirmation(label)

        left = int(bounds.get("x", 0) or 0)
        top = int(bounds.get("y", 0) or 0)
        width = int(bounds.get("width", 0) or 0)
        height = int(bounds.get("height", 0) or 0)
        if width < 40 or height < 40:
            return False, "Aktif pencere sınırları alınamadı."

        sx = int(round(left + x_norm * width))
        sy = int(round(top + y_norm * height))

        from pywinauto import mouse
        key = _normalize(action)
        if key in {"double_click", "double"}:
            mouse.double_click(coords=(sx, sy))
        elif key in {"right_click", "right"}:
            mouse.click(button="right", coords=(sx, sy))
        else:
            mouse.click(button="left", coords=(sx, sy))

        return True, f"'{label}' öğesine tıklandı (Vision fallback, güven {confidence:.2f})."
    except Exception as exc:
        return False, f"Vision tıklaması başarısız: {exc}"
    finally:
        try:
            if image_path.exists():
                image_path.unlink()
        except Exception:
            pass


def _ui_action_local(
    action: str,
    window_title: str = "",
    target: str = "",
    text: str = "",
    keys: str = "",
    direction: str = "down",
    amount: int = 5,
    method: str = "auto",
    clear_first: bool = True,
    confirmed: bool = False,
) -> str:
    if not IS_WIN:
        return "Uygulama içi navigasyon yalnızca Windows'ta kullanılabilir."

    key = _normalize(action)
    method_key = _normalize(method or "auto")

    if target and _needs_confirmation(target) and not confirmed:
        return _confirmation(target)

    hwnd, matched_title = _find_hwnd(window_title)
    if not hwnd:
        return f"Uygulama penceresi bulunamadı: {window_title or 'aktif pencere'}"
    _focus_hwnd(hwnd)

    if key in {"inspect", "list_controls", "controls", "listele"}:
        return f"[Pencere: {matched_title}]\n" + _uia_inspect(hwnd, target)

    if key in {"hotkey", "shortcut", "kisayol", "kısayol"}:
        return _send_hotkey(keys)

    if key in {"scroll", "kaydir", "kaydır"}:
        return _scroll(hwnd, direction, amount, target, method_key)

    if key in {"type_text", "type", "write", "yaz"}:
        ok, message = _uia_type(hwnd, target, text, bool(clear_first))
        return message

    if key not in {"click", "select", "select_tab", "tab", "double_click", "right_click", "double", "right"}:
        return f"Bilinmeyen UI işlemi: {action}"

    if not target:
        return "Tıklanacak öğenin görünen adı/etiketi belirtilmedi."

    if method_key in {"auto", "uia", "ui automation"}:
        ok, message = _uia_click(hwnd, target, key)
        if ok:
            return message
        if method_key != "auto":
            return message

    # Steam/Chromium/Electron controls may not expose useful UIA names. In auto
    # mode, fall back to current Ripperinio screen vision and click the label.
    ok, message = _vision_locate_and_click(target, key)
    return message


def ui_navigate(
    action: str,
    window_title: str = "",
    target: str = "",
    text: str = "",
    keys: str = "",
    direction: str = "down",
    amount: int = 5,
    method: str = "auto",
    clear_first: bool = True,
    admin: bool = False,
    confirmed: bool = False,
) -> str:
    """
    Public Ripperinio application-internal navigation tool.

    admin=True relaunches the SAME narrow UI action through the existing UAC
    helper, useful when the target app itself is elevated.
    """
    if admin:
        if not confirmed:
            return (
                "ONAY GEREKLI: Yükseltilmiş bir uygulamanın arayüzüne müdahale "
                "etmek için Ripperinio UI kontrolü UAC ile yönetici olarak çalışacak. "
                "Kullanıcı bu spesifik işlemi onaylarsa confirmed=true ile tekrar çağır."
            )
        from actions.pc_control import _run_elevated_job
        return _run_elevated_job({
            "type": "ui_action",
            "action": action,
            "window_title": window_title,
            "target": target,
            "text": text,
            "keys": keys,
            "direction": direction,
            "amount": int(amount or 5),
            "method": method,
            "clear_first": bool(clear_first),
            "confirmed": True,
        })

    return _ui_action_local(
        action, window_title, target, text, keys,
        direction, amount, method, clear_first, confirmed,
    )
