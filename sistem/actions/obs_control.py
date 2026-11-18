from __future__ import annotations

"""Small OBS WebSocket v5 client.

OBS Studio ships obs-websocket in modern releases. RIP only connects to the
endpoint the user explicitly configures; it does not enable OBS remote control
or change firewall settings on its own.
"""

import base64
import hashlib
import json
import uuid
from typing import Any

from app_config import load_app_config


def _auth(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest()).decode("ascii")
    return base64.b64encode(hashlib.sha256((secret + challenge).encode("utf-8")).digest()).decode("ascii")


def _endpoint() -> tuple[str, int, str]:
    cfg = load_app_config()
    host = str(cfg.get("obs_host") or "127.0.0.1").strip() or "127.0.0.1"
    try: port = int(cfg.get("obs_port") or 4455)
    except Exception: port = 4455
    password = str(cfg.get("obs_password") or "")
    return host, max(1, min(65535, port)), password


def _request(request_type: str, request_data: dict[str, Any] | None = None, timeout: float = 3.0) -> dict[str, Any]:
    try:
        from websockets.sync.client import connect
    except Exception as exc:
        raise RuntimeError("websockets paketi yok; RIP kurulumunu onar") from exc

    host, port, password = _endpoint()
    uri = f"ws://{host}:{port}"
    with connect(uri, open_timeout=timeout, close_timeout=1) as ws:
        hello = json.loads(ws.recv(timeout=timeout))
        if int(hello.get("op", -1)) != 0:
            raise RuntimeError("OBS WebSocket Hello alınamadı")
        data = hello.get("d") or {}
        identify: dict[str, Any] = {"rpcVersion": min(1, int(data.get("rpcVersion", 1) or 1))}
        auth = data.get("authentication") or {}
        if auth:
            if not password:
                raise RuntimeError("OBS WebSocket parola istiyor; V6 Control Center > OBS bölümüne parolayı gir")
            identify["authentication"] = _auth(password, str(auth.get("salt") or ""), str(auth.get("challenge") or ""))
        ws.send(json.dumps({"op": 1, "d": identify}, ensure_ascii=False))
        identified = json.loads(ws.recv(timeout=timeout))
        if int(identified.get("op", -1)) != 2:
            raise RuntimeError("OBS WebSocket kimlik doğrulaması başarısız")

        req_id = uuid.uuid4().hex
        body: dict[str, Any] = {"requestType": request_type, "requestId": req_id}
        if request_data: body["requestData"] = dict(request_data)
        ws.send(json.dumps({"op": 6, "d": body}, ensure_ascii=False))
        while True:
            msg = json.loads(ws.recv(timeout=timeout))
            if int(msg.get("op", -1)) != 7: continue
            d = msg.get("d") or {}
            if str(d.get("requestId") or "") != req_id: continue
            status = d.get("requestStatus") or {}
            if not status.get("result", False):
                raise RuntimeError(str(status.get("comment") or f"OBS hata kodu {status.get('code')}") )
            return d.get("responseData") or {}


def obs_control(action: str = "status", target: str = "") -> str:
    key = str(action or "status").strip().casefold()
    if key == "status":
        rec = _request("GetRecordStatus")
        stream = _request("GetStreamStatus")
        scene = _request("GetCurrentProgramScene")
        return (
            f"OBS bağlı · Sahne: {scene.get('currentProgramSceneName','?')} · "
            f"Kayıt: {'AÇIK' if rec.get('outputActive') else 'kapalı'} · "
            f"Yayın: {'AÇIK' if stream.get('outputActive') else 'kapalı'}"
        )
    mapping = {
        "record_start": "StartRecord",
        "record_stop": "StopRecord",
        "record_toggle": "ToggleRecord",
        "stream_start": "StartStream",
        "stream_stop": "StopStream",
        "stream_toggle": "ToggleStream",
    }
    if key in mapping:
        _request(mapping[key]); return f"OBS {key} komutu uygulandı"
    if key == "scene":
        name = str(target or "").strip()
        if not name: raise ValueError("Sahne adı gerekli")
        _request("SetCurrentProgramScene", {"sceneName": name}); return f"OBS sahnesi değişti: {name}"
    raise ValueError(f"Bilinmeyen OBS işlemi: {action}")
