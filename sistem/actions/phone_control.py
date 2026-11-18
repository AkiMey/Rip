from __future__ import annotations

"""High-level PC -> Android control helpers used by the V5.4 Phone Manager.

All actions are queued through the existing authenticated phone bridge. Android
still enforces its own permission model: destructive/system actions either use
Accessibility (explicitly enabled by the user) or open Android's confirmation UI.
"""

import json
import time
from pathlib import Path
from typing import Any

from bridge_shared import data_file
from actions.phone_commands import enqueue_phone_command, last_command_ack

INVENTORY_STORE = data_file("phone_inventory.json")


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def get_phone_inventory() -> dict[str, Any]:
    value = _read(INVENTORY_STORE, {})
    return value if isinstance(value, dict) else {}


def get_inventory_age_seconds() -> int | None:
    row = get_phone_inventory()
    ts = int(row.get("received_at", 0) or 0)
    if not ts:
        return None
    return max(0, int(time.time() - ts / 1000))


def queue_device_action(action: str, **payload: Any) -> dict[str, Any]:
    body = {"action": str(action or "").strip()}
    body.update(payload)
    return enqueue_phone_command("device_action", body)


def queue_inventory_refresh() -> dict[str, Any]:
    return queue_device_action("refresh_inventory")


def queue_open_app(package: str) -> dict[str, Any]:
    return queue_device_action("open_app", package=str(package or "").strip())

def queue_open_camera() -> dict[str, Any]:
    return queue_device_action("open_camera")


def queue_uninstall_app(package: str) -> dict[str, Any]:
    # Android itself shows the uninstall confirmation screen. We never silently
    # uninstall arbitrary packages from the PC side.
    return queue_device_action("uninstall_app", package=str(package or "").strip())


def queue_app_settings(package: str) -> dict[str, Any]:
    return queue_device_action("app_settings", package=str(package or "").strip())


def queue_package_check(package: str) -> dict[str, Any]:
    return queue_device_action("package_check", package=str(package or "").strip())


def queue_global_action(action: str) -> dict[str, Any]:
    allowed = {"home", "back", "recents", "notifications", "quick_settings", "lock_screen"}
    key = str(action or "").strip().casefold()
    if key not in allowed:
        raise ValueError(f"Desteklenmeyen global telefon işlemi: {action}")
    return queue_device_action(key)


def queue_media_action(action: str) -> dict[str, Any]:
    allowed = {"media_play_pause", "media_next", "media_previous"}
    key = str(action or "").strip().casefold()
    if key not in allowed:
        raise ValueError(f"Desteklenmeyen medya işlemi: {action}")
    return queue_device_action(key)


def queue_volume(percent: int, stream: str = "music") -> dict[str, Any]:
    key = str(stream or "music").strip().casefold()
    if key not in {"music", "ring", "alarm", "notification"}:
        key = "music"
    return queue_device_action("volume_set", percent=max(0, min(100, int(percent))), stream=key)


def queue_brightness(percent: int) -> dict[str, Any]:
    return queue_device_action("brightness_set", percent=max(1, min(100, int(percent))))


def queue_screen_timeout(seconds: int) -> dict[str, Any]:
    return queue_device_action("screen_timeout_set", seconds=max(15, min(1800, int(seconds))))


def queue_autorotate(enabled: bool) -> dict[str, Any]:
    return queue_device_action("autorotate_set", enabled=bool(enabled))


def queue_dnd_toggle() -> dict[str, Any]:
    return queue_device_action("dnd_toggle")


def queue_flashlight_toggle() -> dict[str, Any]:
    return queue_device_action("flashlight_toggle")


def queue_dismiss_notification(key: str) -> dict[str, Any]:
    return queue_device_action("dismiss_notification", key=str(key or "").strip())


def queue_open_notification(key: str) -> dict[str, Any]:
    return queue_device_action("open_notification", key=str(key or "").strip())



def queue_reply_notification(key: str, text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if not value:
        raise ValueError("Yanıt metni boş")
    return queue_device_action("reply_notification", key=str(key or "").strip(), text=value[:2000])

def queue_delete_phone_file(uri: str) -> dict[str, Any]:
    return queue_device_action("delete_file", uri=str(uri or "").strip())


def queue_rename_phone_file(uri: str, new_name: str) -> dict[str, Any]:
    return queue_device_action("rename_file", uri=str(uri or "").strip(), new_name=str(new_name or "").strip())


def queue_install_apk(uri: str) -> dict[str, Any]:
    return queue_device_action("install_apk", uri=str(uri or "").strip())


def queue_open_system_panel(panel: str) -> dict[str, Any]:
    key = str(panel or "settings").strip().casefold()
    if key not in {"settings", "wifi", "bluetooth", "display", "sound", "apps"}:
        key = "settings"
    return queue_device_action("open_system_panel", panel=key)


def queue_vibrate(duration_ms: int = 450) -> dict[str, Any]:
    return queue_device_action("vibrate", duration_ms=max(80, min(3000, int(duration_ms))))

def queue_find_phone(duration_ms: int = 9000) -> dict[str, Any]:
    return queue_device_action("find_phone", duration_ms=max(2500, min(15000, int(duration_ms))))


def queue_phone_location() -> dict[str, Any]:
    """Ask the paired Android device for a fresh/most-recent GPS fix."""
    return queue_device_action("get_location")


def queue_clear_notifications() -> dict[str, Any]:
    return queue_device_action("clear_notifications")


def queue_open_permission(which: str) -> dict[str, Any]:
    allowed = {"notification", "accessibility", "overlay", "battery", "app_settings", "usage", "write_settings", "install_unknown", "dnd", "camera", "answer_calls", "location"}
    key = str(which or "").strip().casefold()
    if key not in allowed:
        raise ValueError(f"Bilinmeyen izin sayfası: {which}")
    return queue_device_action("open_permission", permission=key)


def queue_phone_file_to_pc(uri: str, filename: str) -> dict[str, Any]:
    return queue_device_action("upload_file_to_pc", uri=str(uri or ""), filename=str(filename or ""))



def queue_screen_snapshot(quality: int = 45, max_width: int = 720) -> dict[str, Any]:
    return queue_device_action("screen_snapshot", quality=max(25, min(80, int(quality))), max_width=max(360, min(1080, int(max_width))))


def queue_screen_stream_start(fps: int = 12, quality: int = 50, max_width: int = 720) -> dict[str, Any]:
    return queue_device_action(
        "screen_stream_start",
        fps=max(4, min(30, int(fps))),
        quality=max(30, min(75, int(quality))),
        max_width=max(420, min(1080, int(max_width))),
    )


def queue_screen_stream_stop() -> dict[str, Any]:
    return queue_device_action("screen_stream_stop")


def queue_screen_tap(x: float, y: float) -> dict[str, Any]:
    return queue_device_action("screen_tap", x=float(x), y=float(y))


def queue_screen_swipe(x1: float, y1: float, x2: float, y2: float, duration_ms: int = 350) -> dict[str, Any]:
    return queue_device_action("screen_swipe", x1=float(x1), y1=float(y1), x2=float(x2), y2=float(y2), duration_ms=max(80, min(1600, int(duration_ms))))

def wait_for_ack(command_id: str, timeout: float = 12.0, interval: float = 0.08) -> dict[str, Any]:
    end = time.time() + max(0.5, float(timeout))
    while time.time() < end:
        ack = last_command_ack(command_id)
        if ack:
            return ack
        time.sleep(max(0.1, float(interval)))
    return {}


def _find_app_package(query: str) -> tuple[str, str]:
    q = str(query or "").strip().casefold()
    if not q:
        return "", ""
    apps = list(get_phone_inventory().get("apps") or [])
    exact = []
    partial = []
    for row in apps:
        label = str(row.get("label") or "")
        package = str(row.get("package") or "")
        if q == label.casefold() or q == package.casefold():
            exact.append((package, label))
        elif q in label.casefold() or q in package.casefold():
            partial.append((package, label))
    rows = exact or partial
    return rows[0] if rows else ("", "")


def phone_control(action: str, target: str = "", value: int | None = None, confirmed: bool = False) -> str:
    """Queue a phone action and wait briefly for the Android ACK."""
    key = str(action or "").strip().casefold()
    row: dict[str, Any]
    if key in {"home", "back", "recents", "notifications", "quick_settings", "lock_screen"}:
        row = queue_global_action(key)
    elif key in {"media_play_pause", "media_next", "media_previous"}:
        row = queue_media_action(key)
    elif key == "vibrate":
        row = queue_vibrate(int(value or 450))
    elif key == "find_phone":
        row = queue_find_phone(int(value or 9000))
    elif key in {"get_location", "location", "konum"}:
        row = queue_phone_location()
    elif key == "volume_set":
        row = queue_volume(int(value if value is not None else 50), target or "music")
    elif key == "brightness_set":
        row = queue_brightness(int(value if value is not None else 50))
    elif key == "screen_timeout_set":
        row = queue_screen_timeout(int(value if value is not None else 30))
    elif key == "autorotate_set":
        row = queue_autorotate(bool(value if value is not None else 1))
    elif key == "dnd_toggle":
        row = queue_dnd_toggle()
    elif key == "flashlight_toggle":
        row = queue_flashlight_toggle()
    elif key == "open_system_panel":
        row = queue_open_system_panel(target or "settings")
    elif key == "clear_notifications":
        row = queue_clear_notifications()
    elif key == "refresh_inventory":
        row = queue_inventory_refresh()
    elif key == "open_permission":
        row = queue_open_permission(target or "accessibility")
    elif key == "open_camera":
        row = queue_open_camera()
    elif key == "screen_stream_start":
        row = queue_screen_stream_start(fps=int(value if value is not None else 15), quality=48, max_width=720)
    elif key == "screen_stream_stop":
        row = queue_screen_stream_stop()
    elif key in {"open_app", "uninstall_app"}:
        package, label = _find_app_package(target)
        if not package:
            # Allow an explicit Android package name even before inventory arrives.
            package = str(target or "").strip() if "." in str(target or "") else ""
            label = target
        if not package:
            return "Telefon uygulaması bulunamadı. Önce Phone Control Center'dan TELEFONU TARA yap."
        if key == "uninstall_app":
            if not confirmed:
                return f"{label or package} kaldırma ekranını telefonda açmam için açık onay gerekiyor."
            row = queue_uninstall_app(package)
        else:
            row = queue_open_app(package)
    else:
        return f"Bilinmeyen telefon kontrolü: {action}"

    ack = wait_for_ack(str(row.get("id") or ""), timeout=14)
    if not ack:
        return "Telefon komutu gönderildi fakat 14 saniye içinde yanıt alınamadı."
    detail = str(ack.get("detail") or "")
    status = str(ack.get("status") or "")
    return detail or ("Telefon işlemi tamamlandı." if status in {"ok", "done", "success"} else "Telefon işlemi başarısız.")
