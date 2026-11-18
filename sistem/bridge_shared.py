from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

from app_paths import data_path
from version import PROTOCOL_VERSION, STAMP

DATA_DIR = data_path("persistent_bridge")
CONFIG_FILE = DATA_DIR / "config.json"
LOG_FILE = DATA_DIR / "bridge.log"
PID_FILE = DATA_DIR / "bridge.pid"
PORT = 8765
SCREEN_STREAM_PORT = 8766


def data_file(name: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / name


def _read_json(path: Path, default):
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        return raw if isinstance(raw, type(default)) else default
    except Exception:
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def ensure_config() -> dict[str, Any]:
    cfg = _read_json(CONFIG_FILE, {})
    access_token = str(cfg.get("token") or "").strip()
    if len(access_token) < 24:
        access_token = secrets.token_hex(24)
        cfg["token"] = access_token
    cfg["port"] = PORT
    cfg["protocol_version"] = PROTOCOL_VERSION
    cfg["created_by"] = f"RIPPERINIO {STAMP}"
    _write_json(CONFIG_FILE, cfg)
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass
    return cfg


def token() -> str:
    return str(ensure_config().get("token") or "")


def token_key_id(value: str | None = None) -> str:
    """Non-secret fingerprint used to detect stale phone pairing before AES decrypt."""
    raw = str(value if value is not None else token()).encode("utf-8")
    return hashlib.sha256(raw + b"|ripperinio-pairing-id-v5").hexdigest()[:12]


def lan_ip() -> str:
    candidates: list[str] = []
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
            return ip
    return ""


def tailscale_cli() -> Path | None:
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Tailscale" / "tailscale.exe",
    ]
    for p in candidates:
        if p and p.is_file():
            return p
    try:
        cp = subprocess.run(
            ["where.exe", "tailscale.exe"], capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=4,
        )
        for line in cp.stdout.splitlines():
            p = Path(line.strip())
            if p.is_file():
                return p
    except Exception:
        pass
    return None


def tailscale_ip() -> str:
    exe = tailscale_cli()
    if exe is None:
        return ""
    try:
        cp = subprocess.run(
            [str(exe), "ip", "-4"], capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in cp.stdout.splitlines():
            value = line.strip()
            if value.startswith("100.") and value.count(".") == 3:
                return value
    except Exception:
        pass
    return ""


def service_running() -> bool:
    try:
        cp = subprocess.run(
            ["sc.exe", "query", "Tailscale"], capture_output=True, text=True,
            encoding="utf-8", errors="ignore", timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return "RUNNING" in cp.stdout.upper()
    except Exception:
        return False


def endpoints() -> dict[str, str]:
    lip = lan_ip()
    tip = tailscale_ip()
    return {
        "lan": f"http://{lip}:{PORT}" if lip else "",
        "tailscale": f"http://{tip}:{PORT}" if tip else "",
    }


def pairing_url() -> str:
    cfg = ensure_config()
    eps = endpoints()
    primary = eps["tailscale"] or eps["lan"] or f"http://127.0.0.1:{PORT}"
    params = {
        "t": str(cfg["token"]),
        "lan": eps["lan"],
        "ts": eps["tailscale"],
        "v": str(PROTOCOL_VERSION),
        "kid": token_key_id(str(cfg["token"])),
    }
    return primary + "/?" + urllib.parse.urlencode(params)


def public_info() -> dict[str, Any]:
    eps = endpoints()
    return {
        "token": token(),
        "key_id": token_key_id(),
        "protocol_version": PROTOCOL_VERSION,
        "lan": eps["lan"],
        "tailscale": eps["tailscale"],
        "pair_url": pairing_url(),
        "tailscale_installed": tailscale_cli() is not None,
        "tailscale_service": service_running(),
        "computer": socket.gethostname(),
        "port": PORT,
    }


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="print", choices=["init", "print"])
    args = ap.parse_args()
    ensure_config()
    info = public_info()
    if args.action == "init":
        print("[OK] Kalici Bridge token hazir.")
    print("PC        :", info["computer"])
    print("LAN       :", info["lan"] or "yok")
    print("TAILSCALE :", info["tailscale"] or "yok / oturum acilmamis")
    print("PAIR ID   :", info["key_id"])
    print("PAIR URL  :", info["pair_url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
