from __future__ import annotations

"""Shared PC <-> Android command/file/clipboard queue for RIP Phone Bridge V2."""

import json
import hashlib
import mimetypes
import socket
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from bridge_shared import data_file, endpoints

COMMAND_STORE = data_file("phone_commands.json")
ACK_STORE = data_file("phone_command_acks.json")
TRANSFER_DIR = data_file("phone_transfers")
CLIPBOARD_STORE = data_file("phone_clipboard.json")
_LOCK = threading.RLock()
_MAX_COMMANDS = 200


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _safe(value: Any, limit: int = 1000) -> str:
    return str(value or "").replace("\x00", " ").strip()[:limit]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _lan_base() -> str:
    """Telefon aynı ağdaysa tünel yerine direkt PC'yi denemesi için LAN adresi."""
    candidates = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            candidates.append(str(s.getsockname()[0]))
        finally:
            s.close()
    except Exception:
        pass
    try:
        candidates.append(socket.gethostbyname(socket.gethostname()))
    except Exception:
        pass

    for ip in candidates:
        if ip and not ip.startswith("127.") and ip != "0.0.0.0":
            return f"http://{ip}:8765"
    return ""


def enqueue_phone_command(command_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    now = int(time.time() * 1000)
    row = {
        "id": uuid.uuid4().hex,
        "type": _safe(command_type, 60),
        "payload": dict(payload or {}),
        "created_at": now,
        "state": "queued",
        "claimed_at": 0,
        "attempts": 0,
    }
    with _LOCK:
        rows = _read(COMMAND_STORE, [])
        if not isinstance(rows, list):
            rows = []
        rows.append(row)
        _write(COMMAND_STORE, rows[-_MAX_COMMANDS:])
    return row


def claim_phone_command(stale_after_seconds: int = 30) -> dict[str, Any] | None:
    now = int(time.time() * 1000)
    stale_ms = max(10, int(stale_after_seconds)) * 1000
    with _LOCK:
        rows = _read(COMMAND_STORE, [])
        if not isinstance(rows, list):
            rows = []
        claimed = None
        for row in rows:
            state = str(row.get("state") or "queued")
            claimed_at = int(row.get("claimed_at", 0) or 0)
            if state == "queued" or (state == "claimed" and now - claimed_at > stale_ms):
                row["state"] = "claimed"
                row["claimed_at"] = now
                row["attempts"] = int(row.get("attempts", 0) or 0) + 1
                claimed = dict(row)
                break
        # Low-latency long-poll checks this function frequently. Rewriting the
        # JSON file when the queue is empty caused needless disk I/O. Only persist
        # when a command was actually claimed/updated.
        if claimed is not None:
            _write(COMMAND_STORE, rows[-_MAX_COMMANDS:])
        return claimed


def ack_phone_command(command_id: str, status: str = "ok", detail: str = "") -> dict[str, Any]:
    cid = _safe(command_id, 80)
    now = int(time.time() * 1000)
    ack = {"id": cid, "status": _safe(status, 32), "detail": _safe(detail, 500), "ts": now}
    with _LOCK:
        rows = _read(COMMAND_STORE, [])
        if not isinstance(rows, list):
            rows = []
        for row in rows:
            if str(row.get("id")) == cid:
                row["state"] = "done" if str(status).casefold() in {"ok", "done", "success"} else "error"
                row["ack"] = ack
                break
        _write(COMMAND_STORE, rows[-_MAX_COMMANDS:])

        acks = _read(ACK_STORE, [])
        if not isinstance(acks, list):
            acks = []
        acks.append(ack)
        _write(ACK_STORE, acks[-300:])
    return ack


def last_command_ack(command_id: str = "") -> dict[str, Any]:
    acks = _read(ACK_STORE, [])
    if not isinstance(acks, list):
        return {}
    if command_id:
        for row in reversed(acks):
            if str(row.get("id")) == str(command_id):
                return dict(row)
        return {}
    return dict(acks[-1]) if acks else {}


def _dedupe_target(directory: Path, name: str) -> Path:
    target = directory / name
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for i in range(2, 1000):
        candidate = directory / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"


def stage_file_for_phone(source: str | Path) -> dict[str, Any]:
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Dosya bulunamadı: {src}")
    TRANSFER_DIR.mkdir(parents=True, exist_ok=True)
    transfer_id = uuid.uuid4().hex
    bucket = TRANSFER_DIR / transfer_id
    bucket.mkdir(parents=True, exist_ok=True)
    dst = _dedupe_target(bucket, src.name)
    shutil.copy2(src, dst)
    size = dst.stat().st_size
    mime = mimetypes.guess_type(dst.name)[0] or "application/octet-stream"
    digest = _sha256_file(dst)
    # V5.2.5: Tek bir global/tanimsiz _tailscale_base kullanma.
    # Bridge'in o an ilan ettigi LAN + Tailscale endpointlerini ayni kaynaktan al.
    # Android aktif route'u zaten kendisi seciyor; bu alanlar tani/geri-donus icin
    # komutta tutuluyor.
    eps = endpoints()
    command = enqueue_phone_command(
        "download_file",
        {
            "transfer_id": transfer_id,
            "filename": dst.name,
            "size": size,
            "mime": mime,
            "sha256": digest,
            "lan_base": str(eps.get("lan") or ""),
            "tailscale_base": str(eps.get("tailscale") or ""),
        },
    )
    return {
        "ok": True,
        "transfer_id": transfer_id,
        "command_id": command["id"],
        "filename": dst.name,
        "size": size,
        "sha256": digest,
        "path": str(dst),
    }


def resolve_transfer(transfer_id: str) -> Path | None:
    tid = _safe(transfer_id, 80)
    if not tid or any(ch not in "0123456789abcdefABCDEF" for ch in tid):
        return None
    bucket = TRANSFER_DIR / tid
    if not bucket.is_dir():
        return None
    try:
        files = [p for p in bucket.iterdir() if p.is_file()]
    except Exception:
        files = []
    return files[0] if files else None


def queue_clipboard_to_phone(text: str) -> dict[str, Any]:
    value = _safe(text, 12000)
    if not value:
        raise ValueError("Telefona gönderilecek pano metni boş.")
    return enqueue_phone_command("set_clipboard", {"text": value})


def queue_call_action(call_id: str, action: str) -> dict[str, Any]:
    key = str(action or "").strip().casefold()
    if key not in {"accept", "decline"}:
        raise ValueError("Arama işlemi accept veya decline olmalı.")
    return enqueue_phone_command("call_action", {"call_id": _safe(call_id, 220), "action": key})


def save_phone_clipboard(text: str, device: str = "Android") -> dict[str, Any]:
    row = {
        "text": _safe(text, 12000),
        "device": _safe(device, 80),
        "received_at": int(time.time() * 1000),
    }
    if not row["text"]:
        raise ValueError("Boş telefon panosu.")
    with _LOCK:
        _write(CLIPBOARD_STORE, row)
    return row


def get_phone_clipboard() -> dict[str, Any]:
    row = _read(CLIPBOARD_STORE, {})
    return row if isinstance(row, dict) else {}


def cleanup_phone_transfers(max_age_hours: int = 24) -> None:
    root = TRANSFER_DIR
    if not root.is_dir():
        return
    cutoff = time.time() - max(1, int(max_age_hours)) * 3600
    for bucket in root.iterdir():
        try:
            if bucket.is_dir() and bucket.stat().st_mtime < cutoff:
                shutil.rmtree(bucket, ignore_errors=True)
        except Exception:
            pass
