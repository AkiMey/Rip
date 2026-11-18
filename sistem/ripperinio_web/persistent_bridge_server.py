from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import secrets
import socket
import struct
import sys
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
import uvicorn

WEB_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = WEB_DIR.parent
if str(SYSTEM_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEM_DIR))

from bridge_shared import LOG_FILE, PID_FILE, PORT, SCREEN_STREAM_PORT, public_info, token, token_key_id
from version import PROTOCOL_VERSION, VERSION
from core.logging_utils import append_log, should_log
from actions.phone_bridge import PairingMismatchError, decrypt_phone_envelope, save_phone_payload, save_raw_screen_frame
from actions.phone_commands import (
    ack_phone_command,
    claim_phone_command,
    cleanup_phone_transfers,
    resolve_transfer,
)
from actions.power_control import execute_power_action, get_power_info

TOKEN = token()
app = FastAPI(title="RIP Persistent Phone Bridge", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# Low-latency raw TCP screen stream (V6.2.1)
# ---------------------------------------------------------------------------
# FastAPI's request-body streaming remains as a compatibility fallback, but
# Android HttpURLConnection may buffer a long-lived chunked POST on some
# Samsung/Android builds. A tiny authenticated TCP stream avoids that buffering
# entirely and gives the HUD each frame as soon as the phone writes it.
_SCREEN_SERVER_STOP = threading.Event()
_SCREEN_SERVER_SOCKET = None

def _recv_exact(sock: socket.socket, size: int) -> bytes:
    out = bytearray()
    while len(out) < size:
        chunk = sock.recv(size - len(out))
        if not chunk:
            raise ConnectionError("screen stream closed")
        out.extend(chunk)
    return bytes(out)

def _screen_client(sock: socket.socket, address) -> None:
    try:
        sock.settimeout(15.0)
        if _recv_exact(sock, 4) != b"RIPS":
            return
        token_len = struct.unpack(">H", _recv_exact(sock, 2))[0]
        if token_len <= 0 or token_len > 512:
            return
        supplied = _recv_exact(sock, token_len).decode("utf-8", errors="ignore")
        if not TOKEN or not secrets.compare_digest(supplied, TOKEN):
            try: sock.sendall(b"\x00")
            except Exception: pass
            return
        sock.sendall(b"\x01")
        sock.settimeout(30.0)
        frames = 0
        while not _SCREEN_SERVER_STOP.is_set():
            header = _recv_exact(sock, 24)
            if header[:4] != b"RIPF":
                raise ValueError("invalid screen frame magic")
            jpeg_len = struct.unpack(">I", header[4:8])[0]
            if jpeg_len <= 0 or jpeg_len > 2 * 1024 * 1024:
                raise ValueError("invalid screen frame size")
            captured_at = struct.unpack(">Q", header[8:16])[0]
            screen_w = struct.unpack(">I", header[16:20])[0]
            screen_h = struct.unpack(">I", header[20:24])[0]
            jpeg = _recv_exact(sock, jpeg_len)
            save_raw_screen_frame(jpeg, screen_w, screen_h, captured_at, transport="tcp")
            frames += 1
    except Exception as exc:
        # The launcher performs a tiny authenticated readiness probe and closes
        # immediately without sending a frame. Do not turn that into log spam.
        if frames > 0 and should_log("phone-screen-tcp-client", 5.0):
            log("screen-tcp client %s ended after %s frames: %r" % (address, frames, exc))
    finally:
        try: sock.close()
        except Exception: pass

def _screen_tcp_server() -> None:
    global _SCREEN_SERVER_SOCKET
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _SCREEN_SERVER_SOCKET = server
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", int(SCREEN_STREAM_PORT)))
        server.listen(4)
        server.settimeout(1.0)
        log("SCREEN_TCP START port=%s" % SCREEN_STREAM_PORT)
        while not _SCREEN_SERVER_STOP.is_set():
            try:
                client, address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except Exception:
                pass
            threading.Thread(
                target=_screen_client, args=(client, address), daemon=True,
                name="rip-screen-tcp-client",
            ).start()
    except Exception as exc:
        log("SCREEN_TCP FATAL: %r" % exc)
    finally:
        try: server.close()
        except Exception: pass
        _SCREEN_SERVER_SOCKET = None
        log("SCREEN_TCP STOP")

def start_screen_tcp_server() -> threading.Thread:
    _SCREEN_SERVER_STOP.clear()
    thread = threading.Thread(target=_screen_tcp_server, daemon=True, name="rip-screen-tcp-server")
    thread.start()
    return thread

def stop_screen_tcp_server() -> None:
    _SCREEN_SERVER_STOP.set()
    sock = _SCREEN_SERVER_SOCKET
    if sock is not None:
        try: sock.close()
        except Exception: pass



def _safe_upload_name(value: str) -> str:
    name = Path(str(value or "phone-file").replace("\\", "/")).name.strip()
    name = "".join(ch if ch not in '<>:"/\\|?*\x00' else "_" for ch in name)
    return (name or "phone-file")[-180:]


def _dedupe_upload(directory: Path, name: str) -> Path:
    target = directory / name
    if not target.exists():
        return target
    for index in range(2, 1000):
        candidate = directory / f"{target.stem} ({index}){target.suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{int(time.time())}-{name}"


def log(message: str) -> None:
    append_log(LOG_FILE, message)


def authorized(request: Request) -> bool:
    supplied = (
        request.headers.get("X-RIP-Token")
        or request.query_params.get("t")
        or ""
    )
    return bool(TOKEN) and secrets.compare_digest(str(supplied), str(TOKEN))


@app.get("/health")
async def health():
    info = public_info()
    return {
        "ok": True,
        "service": "RIP Persistent Bridge",
        "version": VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "long_poll": True,
        "screen_stream_tcp_port": SCREEN_STREAM_PORT,
        "screen_stream_transport": "tcp",
        "key_id": token_key_id(TOKEN),
        "computer": info["computer"],
        "tailscale": bool(info["tailscale"]),
    }


@app.post("/api/phone-notification")
async def phone_notification(request: Request):
    try:
        envelope = await request.json()
        payload = decrypt_phone_envelope(TOKEN, envelope)
        payload["_remote_ip"] = request.client.host if request.client else ""
        saved = save_phone_payload(payload)
        response = {"ok": True, "kind": saved.get("kind", "notification")}
        if str(saved.get("kind") or "") == "clipboard":
            response["pc_clipboard"] = bool(saved.get("pc_clipboard", False))
            response["received_at"] = int(saved.get("received_at", 0) or 0)
        return response
    except PairingMismatchError as exc:
        if should_log("phone-pairing-mismatch", 30.0):
            log("phone-notification pairing mismatch: " + str(exc))
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        if should_log("phone-notification-error", 10.0):
            log("phone-notification error: " + repr(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/phone-screen-frame")
async def phone_screen_frame(request: Request):
    """Reliable one-frame HTTP fallback for live screen streaming.

    Raw TCP 8766 is the fast path. If a firewall/ACL blocks it, Android posts
    individual JPEG frames here instead of using a long chunked request that
    some Samsung builds buffer for seconds.
    """
    if not authorized(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        jpeg = await request.body()
        if not jpeg or len(jpeg) > 2 * 1024 * 1024:
            raise ValueError("invalid screen frame size")
        captured_at = int(request.headers.get("X-RIP-Captured-At") or int(time.time() * 1000))
        screen_w = int(request.headers.get("X-RIP-Screen-Width") or 0)
        screen_h = int(request.headers.get("X-RIP-Screen-Height") or 0)
        save_raw_screen_frame(jpeg, screen_w, screen_h, captured_at, transport="http-frame")
        return {"ok": True, "size": len(jpeg)}
    except HTTPException:
        raise
    except Exception as exc:
        if should_log("phone-screen-frame-error", 5.0):
            log("screen-frame fallback error: %r" % exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/phone-screen-stream")
async def phone_screen_stream(request: Request):
    """Receive a continuous length-prefixed JPEG stream over one HTTP request.

    Frame format (network byte order):
      4 bytes magic RIPF
      4 bytes JPEG length
      8 bytes capture timestamp ms
      4 bytes source screen width
      4 bytes source screen height
      N bytes JPEG

    Keeping one chunked POST open removes the per-frame HTTP/Tailscale RTT.
    """
    if not authorized(request):
        raise HTTPException(status_code=403, detail="Unauthorized phone bridge")
    buffer = bytearray()
    frames = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            buffer.extend(chunk)
            # Bound unparsed data so a malformed stream cannot grow memory forever.
            if len(buffer) > 5 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Screen stream buffer too large")
            while True:
                if len(buffer) < 24:
                    break
                if buffer[:4] != b"RIPF":
                    pos = buffer.find(b"RIPF", 1)
                    if pos < 0:
                        del buffer[:-3]
                        break
                    del buffer[:pos]
                    if len(buffer) < 24:
                        break
                jpeg_len = struct.unpack(">I", buffer[4:8])[0]
                if jpeg_len <= 0 or jpeg_len > 2 * 1024 * 1024:
                    raise HTTPException(status_code=400, detail="Invalid screen frame size")
                total = 24 + jpeg_len
                if len(buffer) < total:
                    break
                captured_at = struct.unpack(">Q", buffer[8:16])[0]
                screen_w = struct.unpack(">I", buffer[16:20])[0]
                screen_h = struct.unpack(">I", buffer[20:24])[0]
                jpeg = bytes(buffer[24:total])
                del buffer[:total]
                save_raw_screen_frame(jpeg, screen_w, screen_h, captured_at, transport="http")
                frames += 1
        return {"ok": True, "frames": frames}
    except HTTPException:
        raise
    except Exception as exc:
        if should_log("phone-screen-stream-error", 5.0):
            log("phone-screen-stream error: " + repr(exc))
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/phone-command")
async def get_phone_command(request: Request, wait_ms: int = 0):
    """Low-latency long poll for PC -> Android commands.

    Android keeps one request open instead of waking every four seconds. The
    endpoint returns immediately when a queued command appears, or returns an
    empty result when the bounded wait expires. Legacy callers with wait_ms=0
    keep the old one-shot behaviour.
    """
    if not authorized(request):
        raise HTTPException(status_code=403, detail="Unauthorized phone bridge")
    cleanup_phone_transfers()
    wait_ms = max(0, min(int(wait_ms or 0), 25000))
    deadline = time.monotonic() + wait_ms / 1000.0
    while True:
        command = claim_phone_command()
        if command is not None or wait_ms <= 0:
            return {"ok": True, "command": command}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"ok": True, "command": None}
        await asyncio.sleep(min(0.075, remaining))


@app.post("/api/phone-command-ack")
async def phone_command_ack(request: Request):
    if not authorized(request):
        raise HTTPException(status_code=403, detail="Unauthorized phone bridge")
    body = await request.json()
    return {
        "ok": True,
        "ack": ack_phone_command(
            str(body.get("id") or ""),
            str(body.get("status") or "ok"),
            str(body.get("detail") or ""),
        ),
    }


@app.get("/api/phone-file/{transfer_id}")
async def phone_file(transfer_id: str, request: Request):
    if not authorized(request):
        raise HTTPException(status_code=403, detail="Unauthorized phone bridge")
    path = resolve_transfer(transfer_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Transfer bulunamadi")
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return FileResponse(
        str(path),
        filename=path.name,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-RIP-SHA256": h.hexdigest(),
            "X-Content-Type-Options": "nosniff",
        },
    )



@app.post("/api/phone-upload")
async def phone_upload(request: Request):
    if not authorized(request):
        raise HTTPException(status_code=403, detail="Unauthorized phone bridge")
    filename = _safe_upload_name(request.query_params.get("filename") or "phone-file")
    target_dir = Path.home() / "Downloads" / "RIP Phone"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = _dedupe_upload(target_dir, filename)
    digest = hashlib.sha256()
    total = 0
    max_bytes = 2 * 1024 * 1024 * 1024
    try:
        with target.open("wb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail="Telefon dosyası 2 GB sınırını aşıyor")
                out.write(chunk)
                digest.update(chunk)
    except Exception:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return {
        "ok": True,
        "filename": target.name,
        "size": total,
        "sha256": digest.hexdigest(),
        "path": str(target),
    }

@app.get("/api/power-info")
async def power_info(request: Request):
    if not authorized(request):
        raise HTTPException(status_code=403, detail="Unauthorized phone bridge")
    return {"ok": True, "power": get_power_info()}


@app.post("/api/power-action")
async def power_action(request: Request):
    if not authorized(request):
        raise HTTPException(status_code=403, detail="Unauthorized phone bridge")
    body = await request.json()
    action = str(body.get("action") or "").strip().casefold()
    try:
        return {"ok": True, "result": execute_power_action(action)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def main() -> int:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="ascii")
    info = public_info()
    log(
        "START port=%s lan=%s tailscale=%s"
        % (PORT, info.get("lan"), info.get("tailscale"))
    )
    screen_thread = start_screen_tcp_server()
    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning", access_log=False)
    except Exception:
        log("FATAL\n" + traceback.format_exc())
        raise
    finally:
        stop_screen_tcp_server()
        try:
            screen_thread.join(timeout=1.5)
        except Exception:
            pass
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        log("STOP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
