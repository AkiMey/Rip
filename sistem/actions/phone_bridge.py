from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from bridge_shared import data_file, token_key_id
from actions.phone_commands import save_phone_clipboard, get_phone_clipboard
from actions.clipboard_sync import _pc_clipboard_set

NOTIFY_STORE = data_file("phone_notifications.json")
STATUS_STORE = data_file("phone_status.json")
INVENTORY_STORE = data_file("phone_inventory.json")
LOCATION_STORE = data_file("phone_location.json")
SCREEN_FRAME_FILE = data_file("phone_screen_latest.jpg")  # legacy fallback
SCREEN_FRAME_FILES = (data_file("phone_screen_live_0.jpg"), data_file("phone_screen_live_1.jpg"))
SCREEN_META_FILE = data_file("phone_screen_meta.json")
SCREEN_STREAM_STATUS_FILE = data_file("phone_screen_stream_status.json")
_LOCK = threading.Lock()
_AAD = b"ripperinio-phone-notify-v1"
_KEY_CONTEXT = b"|ripperinio-phone-notify-v1-key"
_MAX_ITEMS = 240
_SCREEN_SEQ = 0


class PairingMismatchError(ValueError):
    """Raised when the Android bridge is paired with a stale/different secret."""



def _safe_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _read_json(path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _atomic_write(path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _key_from_token(token: str) -> bytes:
    return hashlib.sha256(str(token or "").encode("utf-8") + _KEY_CONTEXT).digest()


def decrypt_phone_envelope(token: str, envelope: dict) -> dict:
    if not token:
        raise ValueError("Ripperinio telefon token'i yok.")
    if not isinstance(envelope, dict):
        raise ValueError("Telefon paketi geçersiz.")

    # V5 clients may send a non-secret key id. This catches stale pairing before
    # AES-GCM decryption and turns the old InvalidTag spam into an actionable error.
    supplied_key_id = str(envelope.get("key_id") or envelope.get("kid") or "").strip()
    expected_key_id = token_key_id(token)
    if supplied_key_id and supplied_key_id != expected_key_id:
        raise PairingMismatchError(
            f"Telefon eşleştirme anahtarı uyuşmuyor (PC pair id: {expected_key_id}). Yeniden eşleştir."
        )

    try:
        nonce = base64.b64decode(str(envelope.get("nonce") or ""), validate=True)
        ciphertext = base64.b64decode(str(envelope.get("ciphertext") or ""), validate=True)
    except Exception as exc:
        raise ValueError("Geçersiz şifreli paket kodlaması.") from exc
    if len(nonce) != 12 or not ciphertext:
        raise ValueError("Geçersiz şifreli paket.")

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        clear = AESGCM(_key_from_token(token)).decrypt(nonce, ciphertext, _AAD)
    except InvalidTag as exc:
        raise PairingMismatchError(
            f"Telefon paketi çözülemedi; telefon eski/farklı eşleştirme anahtarı kullanıyor "
            f"(PC pair id: {expected_key_id}). Telefonu RIP ile yeniden eşleştir."
        ) from exc

    try:
        payload = json.loads(clear.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Telefon paketi JSON olarak çözülemedi.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Telefon paketi geçersiz.")
    return payload


def _redact_security_codes(text: str) -> str:
    low = text.casefold()
    words = (
        "otp", "verification code", "security code", "login code",
        "doğrulama kodu", "dogrulama kodu", "giriş kodu", "giris kodu",
        "tek kullanımlık", "tek kullanimlik",
    )
    if any(word in low for word in words):
        text = re.sub(r"(?<!\d)\d{4,8}(?!\d)", "[KOD GİZLENDİ]", text)
    return text




def _is_bridge_heartbeat_record(record: dict) -> bool:
    """Foreground-service keepalive bildirimlerini kullanici akisindan ayir.

    Android Bridge kendi kalici foreground notification'ini periyodik gunceller.
    Bu bir kullanici bildirimi degil; PC tarafinda her update yeni log gibi
    gorunmemeli. Gercek baglanti durumu zaten kind=status paketiyle gelir.
    """
    app = _safe_text(record.get("app") or "", 120).casefold()
    title = _safe_text(record.get("title") or "", 200).casefold()
    text = _safe_text(record.get("text") or "", 500).casefold()
    bridge_named = "rip phone bridge" in app or "rip phone bridge" in title
    heartbeat_text = (
        "pc bağlantısı" in text or "pc baglantisi" in text or
        "canlı durum açık" in text or "canli durum acik" in text or
        "always-on bridge" in text or "bridge service" in text
    )
    return bool(bridge_named and heartbeat_text)


def _prune_bridge_heartbeats() -> None:
    """Eski heartbeat satirlarini notification history'den bir kere temizle."""
    with _LOCK:
        rows = _read_json(NOTIFY_STORE, [])
        if not isinstance(rows, list):
            return
        cleaned = [r for r in rows if not _is_bridge_heartbeat_record(r)]
        if len(cleaned) != len(rows):
            _atomic_write(NOTIFY_STORE, cleaned[-_MAX_ITEMS:])


def save_raw_screen_frame(image: bytes, screen_width: int, screen_height: int, captured_at: int | None = None, stream: bool = True, transport: str = "") -> dict:
    """Store the latest raw JPEG screen frame from the high-FPS stream.

    Persistent bridge and desktop HUD are separate processes, so the newest
    frame is exchanged through an atomic JPEG + tiny metadata file.
    """
    global _SCREEN_SEQ
    if not isinstance(image, (bytes, bytearray)) or not image:
        raise ValueError("Telefon ekran karesi boş.")
    image = bytes(image)
    if len(image) > 2 * 1024 * 1024:
        raise ValueError("Telefon ekran karesi 2 MB sınırını aşıyor.")
    if not image.startswith(b"\xff\xd8"):
        raise ValueError("Telefon ekran karesi JPEG değil.")
    now_ms = int(time.time() * 1000)
    # The bridge and Tk HUD are separate Windows processes. Replacing the same
    # JPEG while Pillow has it open can intermittently fail on Windows and tear
    # down the live stream. Use a two-slot ring so the writer never overwrites
    # the frame currently being decoded by the HUD.
    with _LOCK:
        _SCREEN_SEQ += 1
        seq = _SCREEN_SEQ
        target = SCREEN_FRAME_FILES[seq & 1]
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(image)
        tmp.replace(target)
        meta = {
            "kind": "screen_frame",
            "received_at": now_ms,
            "captured_at": int(captured_at or now_ms),
            "screen_width": max(0, int(screen_width or 0)),
            "screen_height": max(0, int(screen_height or 0)),
            "path": str(target),
            "size": len(image),
            "frame_seq": seq,
            "stream": bool(stream),
            "transport": str(transport or ("stream" if stream else "snapshot")),
        }
        _atomic_write(SCREEN_META_FILE, meta)
    return meta


def save_phone_payload(payload: dict) -> dict:
    """Store a bridge payload. Supports messages, calls and device status."""
    kind = _safe_text(payload.get("kind") or "notification", 32).casefold()
    now_ms = int(time.time() * 1000)

    if kind == "status":
        record = {
            "kind": "status",
            "received_at": now_ms,
            "device": _safe_text(payload.get("device") or "Android", 80),
            "battery_percent": int(payload.get("battery_percent", -1) or -1),
            "charging": bool(payload.get("charging", False)),
            "battery_temp_c": float(payload.get("battery_temp_c", -1) or -1),
            "ringer_mode": _safe_text(payload.get("ringer_mode") or "unknown", 24),
            "storage_free_bytes": int(payload.get("storage_free_bytes", -1) or -1),
            "storage_total_bytes": int(payload.get("storage_total_bytes", -1) or -1),
            "wifi": _safe_text(payload.get("wifi") or "", 120),
            "remote_ip": _safe_text(payload.get("_remote_ip") or payload.get("remote_ip") or "", 80),
            "bridge_version": _safe_text(payload.get("bridge_version") or "", 32),
            "bridge_service": _safe_text(payload.get("bridge_service") or "", 32),
            "bridge_latency_ms": int(payload.get("bridge_latency_ms", -1) or -1),
            "notification_access": bool(payload.get("notification_access", False)),
            "accessibility_access": bool(payload.get("accessibility_access", False)),
            "overlay_access": bool(payload.get("overlay_access", False)),
            "battery_optimization_ignored": bool(payload.get("battery_optimization_ignored", False)),
            "music_volume_percent": int(payload.get("music_volume_percent", -1) if payload.get("music_volume_percent") is not None else -1),
            "music_volume_level": int(payload.get("music_volume_level", -1) if payload.get("music_volume_level") is not None else -1),
            "music_volume_min": int(payload.get("music_volume_min", 0) if payload.get("music_volume_min") is not None else 0),
            "music_volume_max": int(payload.get("music_volume_max", -1) if payload.get("music_volume_max") is not None else -1),
            "volumes": payload.get("volumes") if isinstance(payload.get("volumes"), dict) else {},
            "brightness_percent": int(payload.get("brightness_percent", -1) if payload.get("brightness_percent") is not None else -1),
            "brightness_auto": bool(payload.get("brightness_auto", False)),
            "screen_timeout_ms": int(payload.get("screen_timeout_ms", -1) if payload.get("screen_timeout_ms") is not None else -1),
            "auto_rotate": bool(payload.get("auto_rotate", False)),
            "dnd_filter": int(payload.get("dnd_filter", -1) if payload.get("dnd_filter") is not None else -1),
            "usage_access": bool(payload.get("usage_access", False)),
            "write_settings_access": bool(payload.get("write_settings_access", False)),
            "install_unknown_access": bool(payload.get("install_unknown_access", False)),
            "dnd_access": bool(payload.get("dnd_access", False)),
            "camera_access": bool(payload.get("camera_access", False)),
            "answer_calls_access": bool(payload.get("answer_calls_access", False)),
            "location_access": bool(payload.get("location_access", False)),
            "screen_stream_active": bool(payload.get("screen_stream_active", False)),
            "android_version": _safe_text(payload.get("android_version") or "", 32),
            "sdk": int(payload.get("sdk", -1) if payload.get("sdk") is not None else -1),
            "battery_voltage_mv": int(payload.get("battery_voltage_mv", -1) if payload.get("battery_voltage_mv") is not None else -1),
            "battery_health": _safe_text(payload.get("battery_health") or "", 32),
        }
        with _LOCK:
            _atomic_write(STATUS_STORE, record)
        return record

    if kind == "screen_stream_status":
        record = {
            "kind": "screen_stream_status",
            "received_at": now_ms,
            "device": _safe_text(payload.get("device") or "Android", 80),
            "running": bool(payload.get("running", False)),
            "capture_frames": int(payload.get("capture_frames", 0) or 0),
            "sent_frames": int(payload.get("sent_frames", 0) or 0),
            "tcp_frames": int(payload.get("tcp_frames", 0) or 0),
            "http_frames": int(payload.get("http_frames", 0) or 0),
            "last_capture_at": int(payload.get("last_capture_at", 0) or 0),
            "last_send_at": int(payload.get("last_send_at", 0) or 0),
            "transport": _safe_text(payload.get("transport") or "", 32),
            "last_error": _safe_text(payload.get("last_error") or "", 400),
            "target_fps": int(payload.get("target_fps", 0) or 0),
            "capture_width": int(payload.get("capture_width", 0) or 0),
            "capture_height": int(payload.get("capture_height", 0) or 0),
            "source_width": int(payload.get("source_width", 0) or 0),
            "source_height": int(payload.get("source_height", 0) or 0),
        }
        with _LOCK:
            _atomic_write(SCREEN_STREAM_STATUS_FILE, record)
        return record

    if kind == "screen_frame":
        raw = str(payload.get("jpeg_b64") or "")
        if not raw:
            raise ValueError("Telefon ekran karesi boş.")
        try:
            image = base64.b64decode(raw, validate=True)
        except Exception as exc:
            raise ValueError("Telefon ekran karesi Base64 çözülemedi.") from exc
        return save_raw_screen_frame(
            image,
            int(payload.get("screen_width") or 0),
            int(payload.get("screen_height") or 0),
            int(payload.get("captured_at") or now_ms),
            stream=False,
        )

    if kind == "inventory":
        apps = payload.get("apps") if isinstance(payload.get("apps"), list) else []
        files = payload.get("downloads") if isinstance(payload.get("downloads"), list) else []
        permissions = payload.get("permissions") if isinstance(payload.get("permissions"), dict) else {}
        record = {
            "kind": "inventory",
            "received_at": now_ms,
            "device": _safe_text(payload.get("device") or "Android", 80),
            "apps": apps[:800],
            "downloads": files[:200],
            "permissions": permissions,
            "location_access": bool(permissions.get("location", False)),
            "music_volume_percent": int(payload.get("music_volume_percent", -1) if payload.get("music_volume_percent") is not None else -1),
            "music_volume_level": int(payload.get("music_volume_level", -1) if payload.get("music_volume_level") is not None else -1),
            "music_volume_min": int(payload.get("music_volume_min", 0) if payload.get("music_volume_min") is not None else 0),
            "music_volume_max": int(payload.get("music_volume_max", -1) if payload.get("music_volume_max") is not None else -1),
            "volumes": payload.get("volumes") if isinstance(payload.get("volumes"), dict) else {},
            "brightness_percent": int(payload.get("brightness_percent", -1) if payload.get("brightness_percent") is not None else -1),
            "brightness_auto": bool(payload.get("brightness_auto", False)),
            "screen_timeout_ms": int(payload.get("screen_timeout_ms", -1) if payload.get("screen_timeout_ms") is not None else -1),
            "auto_rotate": bool(payload.get("auto_rotate", False)),
            "dnd_filter": int(payload.get("dnd_filter", -1) if payload.get("dnd_filter") is not None else -1),
            "android_version": _safe_text(payload.get("android_version") or "", 32),
            "sdk": int(payload.get("sdk", -1) if payload.get("sdk") is not None else -1),
        }
        with _LOCK:
            _atomic_write(INVENTORY_STORE, record)
        return record

    if kind == "notification_removed":
        key = _safe_text(payload.get("notification_key") or "", 300)
        if key:
            with _LOCK:
                rows = _read_json(NOTIFY_STORE, [])
                if not isinstance(rows, list): rows = []
                rows = [r for r in rows if str(r.get("notification_key") or "") != key]
                _atomic_write(NOTIFY_STORE, rows[-_MAX_ITEMS:])
        return {"kind": "notification_removed", "notification_key": key, "received_at": now_ms}

    if kind == "clipboard":
        clip = save_phone_clipboard(
            _safe_text(payload.get("text"), 12000),
            _safe_text(payload.get("device") or "Android", 80),
        )

        # V5.2.4: Telefon panosunu sadece JSON'a kaydetmekle kalma;
        # bridge kullanici oturumunda calisiyorsa aninda Windows panosuna yaz.
        # HUD watcher da bu kaydi gorerek kullaniciya bildirim gosterecek.
        copied = _pc_clipboard_set(str(clip.get("text") or ""))
        clip["pc_clipboard"] = bool(copied)

        # Telefon panosu aktarimini HUD notification center'a da dusur.
        preview = _safe_text(clip.get("text") or "", 180)
        notification = {
            "kind": "notification",
            "id": f"clipboard-{clip.get('received_at', now_ms)}",
            "received_at": int(clip.get("received_at", now_ms) or now_ms),
            "posted_at": int(clip.get("received_at", now_ms) or now_ms),
            "package": "com.ripperinio.mobile",
            "app": "RIP Mobile",
            "title": "Telefon panosu alındı ✓",
            "text": (
                "Windows panosuna kopyalandı: " + preview
                if copied else
                "PC'ye ulaştı; HUD Windows panosuna aktarıyor: " + preview
            ),
            "subtext": "Clipboard Sync",
            "category": "clipboard",
            "call_state": "",
            "avatar_b64": "",
        }
        with _LOCK:
            rows = _read_json(NOTIFY_STORE, [])
            if not isinstance(rows, list):
                rows = []
            rows.append(notification)
            _atomic_write(NOTIFY_STORE, rows[-_MAX_ITEMS:])

        return {"kind": "clipboard", **clip}

    record = {
        "kind": "call" if kind == "call" else "notification",
        "id": _safe_text(payload.get("id") or f"n-{now_ms}", 180),
        "received_at": now_ms,
        "posted_at": int(payload.get("posted_at") or now_ms),
        "package": _safe_text(payload.get("package"), 180),
        "app": _safe_text(payload.get("app") or payload.get("package") or "Telefon", 80),
        "title": _safe_text(payload.get("title"), 300),
        "text": _redact_security_codes(_safe_text(payload.get("text"), 1800)),
        "subtext": _safe_text(payload.get("subtext"), 300),
        "category": _safe_text(payload.get("category"), 80),
        "call_state": _safe_text(payload.get("call_state") or "", 32).casefold(),
        "avatar_b64": _safe_text(payload.get("avatar_b64") or "", 60000),
        "notification_key": _safe_text(payload.get("notification_key") or "", 300),
        "clickable": bool(payload.get("clickable", False)),
        "replyable": bool(payload.get("replyable", False)),
        "can_accept": bool(payload.get("can_accept", False)),
        "can_decline": bool(payload.get("can_decline", False)),
        "call_action_source": _safe_text(payload.get("call_action_source") or "", 32),
    }
    if not record["title"] and not record["text"] and record["kind"] != "call":
        raise ValueError("Boş bildirim.")

    # Android foreground service heartbeat'i AKIS/notification listesine yazma.
    # Durum kind=status kanalindan zaten daha zengin sekilde guncelleniyor.
    if record["kind"] == "notification" and _is_bridge_heartbeat_record(record):
        return {"kind": "bridge_heartbeat", "suppressed": True, "received_at": now_ms}

    with _LOCK:
        rows = _read_json(NOTIFY_STORE, [])
        if not isinstance(rows, list):
            rows = []
        fingerprint = (record["id"], record["kind"], record["call_state"], record["title"], record["text"])
        rows = [
            r for r in rows
            if (
                str(r.get("id")), str(r.get("kind")), str(r.get("call_state")),
                str(r.get("title")), str(r.get("text"))
            ) != fingerprint
        ]
        rows.append(record)
        _atomic_write(NOTIFY_STORE, rows[-_MAX_ITEMS:])
    return record


# Backward-compatible name used by V1 patches.
save_phone_notification = save_phone_payload


def _format_record(r: dict) -> str:
    try:
        stamp = time.strftime("%H:%M", time.localtime(int(r.get("received_at", 0)) / 1000))
    except Exception:
        stamp = "--:--"
    app = _safe_text(r.get("app") or r.get("package") or "Telefon", 60)
    title = _safe_text(r.get("title"), 120)
    body = _safe_text(r.get("text"), 500)
    if str(r.get("kind")) == "call":
        state = str(r.get("call_state") or "incoming").upper()
        who = title or body or "Bilinmeyen arayan"
        return f"[{stamp}] {app} ARAMA {state}: {who}"
    text = " — ".join(part for part in (title, body) if part)
    return f"[{stamp}] {app}: {text}"


def phone_messages(action: str = "latest", limit: int = 10, app_filter: str = "") -> str:
    key = str(action or "latest").strip().casefold()
    limit = max(1, min(int(limit or 10), 30))
    rows = _read_json(NOTIFY_STORE, [])
    if not isinstance(rows, list):
        rows = []
    rows = [r for r in rows if str(r.get("kind", "notification")) != "status"]

    needle = str(app_filter or "").casefold().strip()
    if needle:
        rows = [
            r for r in rows
            if needle in str(r.get("app", "")).casefold()
            or needle in str(r.get("package", "")).casefold()
        ]

    if key in {"status", "durum"}:
        if not rows:
            return "Telefon mesaj köprüsü hazır; henüz aktarılmış bildirim yok."
        return f"Telefon mesaj köprüsü çalışıyor. Kayıtlı: {len(rows)}. Son: {_format_record(rows[-1])}"

    if key in {"latest", "list", "messages", "mesajlar", "son"}:
        message_rows = [r for r in rows if str(r.get("kind")) != "call"]
        if not message_rows:
            return "Henüz telefondan aktarılmış mesaj/bildirim yok."
        return "\n".join(_format_record(r) for r in message_rows[-limit:][::-1])

    if key in {"calls", "aramalar", "call"}:
        call_rows = [r for r in rows if str(r.get("kind")) == "call"]
        if not call_rows:
            return "Henüz telefondan aktarılmış arama olayı yok."
        return "\n".join(_format_record(r) for r in call_rows[-limit:][::-1])

    return f"Bilinmeyen telefon mesaj işlemi: {action}"


def _fmt_bytes(value: int) -> str:
    if value is None or value < 0:
        return "bilinmiyor"
    n = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def phone_status(query: str = "all") -> str:
    status = _read_json(STATUS_STORE, {})
    if not isinstance(status, dict) or not status:
        return "Telefon durum bilgisi henüz gelmedi. RIP Phone Bridge V5 Always-On servisini başlat."
    age = max(0, int(time.time() - (int(status.get("received_at", 0)) / 1000)))
    q = str(query or "all").strip().casefold()
    battery = int(status.get("battery_percent", -1) or -1)
    charging = bool(status.get("charging", False))
    temp = float(status.get("battery_temp_c", -1) or -1)
    ringer = str(status.get("ringer_mode") or "unknown")
    free_b = int(status.get("storage_free_bytes", -1) or -1)
    total_b = int(status.get("storage_total_bytes", -1) or -1)

    if q in {"battery", "pil"}:
        extra = "şarj oluyor" if charging else "şarj olmuyor"
        temp_text = f", {temp:.1f}°C" if temp >= 0 else ""
        return f"Telefon pili %{battery if battery >= 0 else '?'} — {extra}{temp_text}. Son veri {age} sn önce."
    if q in {"ringer", "silent", "sessiz", "ses"}:
        labels = {"normal": "normal ses modunda", "vibrate": "titreşimde", "silent": "sessizde"}
        return f"Telefon {labels.get(ringer, ringer)}. Son veri {age} sn önce."
    if q in {"storage", "depolama"}:
        return f"Telefon depolama: {_fmt_bytes(free_b)} boş / {_fmt_bytes(total_b)} toplam. Son veri {age} sn önce."

    labels = {"normal": "normal", "vibrate": "titreşim", "silent": "sessiz"}
    temp_text = f" · {temp:.1f}°C" if temp >= 0 else ""
    connection = "Telefon bağlı" if age <= 75 else "Telefon bağlantısı kopmuş görünüyor"
    version = str(status.get("bridge_version") or "?")
    notif = bool(status.get("notification_access", False))
    access = bool(status.get("accessibility_access", False))
    overlay = bool(status.get("overlay_access", False))
    battery_ok = bool(status.get("battery_optimization_ignored", False))
    usage = bool(status.get("usage_access", False))
    write_settings = bool(status.get("write_settings_access", False))
    dnd = bool(status.get("dnd_access", False))
    location = bool(status.get("location_access", False))
    route = str(status.get("bridge_service") or "?")
    latency = int(status.get("bridge_latency_ms", -1) or -1)
    return (
        f"{connection} (son veri {age} sn önce, Bridge V{version}, {route}"
        f"{f' {latency} ms' if latency >= 0 else ''}). "
        f"Pil %{battery if battery >= 0 else '?'}{' şarjda' if charging else ''}{temp_text}. "
        f"Ses modu: {labels.get(ringer, ringer)}. "
        f"Depolama: {_fmt_bytes(free_b)} boş / {_fmt_bytes(total_b)} toplam. "
        f"İzinler: bildirim {'açık' if notif else 'kapalı'}, Accessibility {'açık' if access else 'kapalı'}, "
        f"overlay {'açık' if overlay else 'kapalı'}, pil muafiyeti {'açık' if battery_ok else 'kapalı'}, "
        f"kullanım erişimi {'açık' if usage else 'kapalı'}, sistem ayarları {'açık' if write_settings else 'kapalı'}, "
        f"DND {'açık' if dnd else 'kapalı'}, konum {'açık' if location else 'kapalı'}."
    )



def save_phone_location_record(record: dict) -> dict:
    """Persist the latest user-requested phone location on this PC only."""
    if not isinstance(record, dict):
        raise ValueError("Telefon konum kaydı geçersiz.")
    row = {
        "kind": "location",
        "received_at": int(record.get("received_at") or int(time.time() * 1000)),
        "latitude": float(record.get("latitude")),
        "longitude": float(record.get("longitude")),
        "accuracy_m": float(record.get("accuracy_m", -1) or -1),
        "provider": _safe_text(record.get("provider") or "", 40),
        "location_time": int(record.get("location_time", 0) or 0),
    }
    with _LOCK:
        _atomic_write(LOCATION_STORE, row)
    return row


def get_phone_location_record() -> dict:
    row = _read_json(LOCATION_STORE, {})
    return row if isinstance(row, dict) else {}

def get_phone_screen_stream_status() -> dict:
    row = _read_json(SCREEN_STREAM_STATUS_FILE, {})
    return row if isinstance(row, dict) else {}


def get_phone_screen_frame() -> dict:
    meta = _read_json(SCREEN_META_FILE, {})
    if not isinstance(meta, dict):
        return {}
    path = Path(str(meta.get("path") or ""))
    if not path.is_file():
        # Compatibility with a pre-V6.2.1 single-file frame.
        if not SCREEN_FRAME_FILE.is_file():
            return {}
        path = SCREEN_FRAME_FILE
    meta["path"] = str(path)
    return meta


def get_phone_status_record() -> dict:
    status = _read_json(STATUS_STORE, {})
    return status if isinstance(status, dict) else {}


def start_phone_bridge_watcher(ui, interval: float = 0.7) -> None:
    """Push real phone notifications/calls into HUD; heartbeat updates stay silent."""
    def worker():
        try:
            _prune_bridge_heartbeats()
        except Exception:
            pass
        current = _read_json(NOTIFY_STORE, [])
        if not isinstance(current, list):
            current = []
        seen = {
            str(r.get("received_at")) + "|" + str(r.get("id")) + "|" + str(r.get("call_state"))
            for r in current[-80:]
        }
        last_status_stamp = 0
        try:
            initial_clip = get_phone_clipboard()
            last_clipboard_stamp = int(initial_clip.get("received_at", 0) or 0)
        except Exception:
            last_clipboard_stamp = 0

        def apply_phone_clipboard_to_hud(row: dict) -> None:
            text = str(row.get("text") or "")
            if not text:
                return
            try:
                ui.root.clipboard_clear()
                ui.root.clipboard_append(text)
                # Tk clipboard ownership survives normal event processing; update_idletasks
                # helps Windows publish it immediately to other applications.
                ui.root.update_idletasks()
                ui.write_log("📋 Telefon panosu Windows panosuna aktarıldı ✓")
            except Exception as exc:
                try:
                    ui.write_log("📋 Telefon panosu alındı fakat Windows panosuna yazılamadı: " + str(exc))
                except Exception:
                    pass

        while True:
            try:
                rows = _read_json(NOTIFY_STORE, [])
                if not isinstance(rows, list):
                    rows = []
                for r in rows[-40:]:
                    if _is_bridge_heartbeat_record(r):
                        continue
                    marker = str(r.get("received_at")) + "|" + str(r.get("id")) + "|" + str(r.get("call_state"))
                    if marker in seen:
                        continue
                    seen.add(marker)
                    if str(r.get("kind")) == "call":
                        state = str(r.get("call_state") or "incoming").casefold()
                        caller = _safe_text(r.get("title") or r.get("text") or "Bilinmeyen arayan", 120)
                        app = _safe_text(r.get("app") or "Telefon", 60)
                        if state in {"incoming", "ringing"}:
                            call_id = _safe_text(r.get("id") or "", 220)
                            avatar = _safe_text(r.get("avatar_b64") or "", 60000)
                            can_accept = bool(r.get("can_accept", False))
                            can_decline = bool(r.get("can_decline", False))
                            action_source = _safe_text(r.get("call_action_source") or "", 32)
                            ui.root.after(0, lambda a=app, c=caller, cid=call_id, av=avatar, ca=can_accept, cd=can_decline, src=action_source: ui.show_incoming_call(a, c, cid, av, ca, cd, src))
                        else:
                            ui.root.after(0, ui.hide_incoming_call)
                        try:
                            ui.root.after(0, lambda rec=dict(r): ui.add_notification(rec))
                        except Exception:
                            pass
                        ui.root.after(0, lambda msg="📞 " + _format_record(r): ui.write_log(msg))
                    else:
                        try:
                            ui.root.after(0, lambda rec=dict(r): ui.add_notification(rec))
                        except Exception:
                            pass
                        ui.root.after(0, lambda msg="📱 " + _format_record(r): ui.write_log(msg))

                clip = get_phone_clipboard()
                clip_stamp = int(clip.get("received_at", 0) or 0) if isinstance(clip, dict) else 0
                if clip_stamp and clip_stamp != last_clipboard_stamp:
                    last_clipboard_stamp = clip_stamp
                    ui.root.after(0, lambda row=dict(clip): apply_phone_clipboard_to_hud(row))

                status = get_phone_status_record()
                stamp = int(status.get("received_at", 0) or 0)
                if stamp and stamp != last_status_stamp:
                    last_status_stamp = stamp
                    try:
                        ui.root.after(0, lambda s=status: ui.update_phone_widget(s))
                    except Exception:
                        pass

                if len(seen) > 800:
                    seen = {
                        str(r.get("received_at")) + "|" + str(r.get("id")) + "|" + str(r.get("call_state"))
                        for r in rows[-150:]
                    }
            except Exception:
                pass
            time.sleep(max(0.3, float(interval)))

    threading.Thread(target=worker, daemon=True, name="rip-phone-bridge-watch").start()


# Backward compatibility.
start_phone_message_watcher = start_phone_bridge_watcher
