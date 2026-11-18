from __future__ import annotations

"""RIP POWER — Windows power control + Wake-on-LAN metadata.

This module intentionally exposes only a fixed allow-list:
lock, sleep, restart, shutdown, abort.
No arbitrary shell input is accepted from the phone.
"""

import ctypes
import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from typing import Any


_ALLOWED = {"lock", "sleep", "restart", "shutdown", "abort"}


def _creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _normalize_mac(value: str) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.lower() in "0123456789abcdef")
    if len(raw) != 12:
        return ""
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2)).upper()


def _fallback_ip() -> str:
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


def _powershell_adapter() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    script = r"""
$cfg = Get-NetIPConfiguration |
  Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up' } |
  Select-Object -First 1
if ($null -eq $cfg) { exit 3 }
$ad = Get-NetAdapter -InterfaceIndex $cfg.InterfaceIndex -ErrorAction Stop
[pscustomobject]@{
  name   = $ad.Name
  desc   = $ad.InterfaceDescription
  mac    = $ad.MacAddress
  ip     = $cfg.IPv4Address.IPAddress
  prefix = $cfg.IPv4Address.PrefixLength
  status = $ad.Status
  speed  = [string]$ad.LinkSpeed
} | ConvertTo-Json -Compress
"""
    try:
        cp = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
            creationflags=_creationflags(),
        )
        if cp.returncode != 0 or not cp.stdout.strip():
            return {}
        value = json.loads(cp.stdout.strip())
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def get_power_info() -> dict[str, Any]:
    """Return the PC's preferred physical adapter details for phone-side WoL."""
    adapter = _powershell_adapter()
    ip = str(adapter.get("ip") or _fallback_ip()).strip()
    prefix = int(adapter.get("prefix") or 24)
    mac = _normalize_mac(str(adapter.get("mac") or ""))

    if not mac:
        node = int(uuid.getnode())
        mac = ":".join(f"{(node >> shift) & 0xff:02X}" for shift in range(40, -1, -8))

    broadcast = ""
    try:
        if ip:
            broadcast = str(ipaddress.ip_interface(f"{ip}/{prefix}").network.broadcast_address)
    except Exception:
        pass

    if not broadcast and ip and "." in ip:
        parts = ip.split(".")
        if len(parts) == 4:
            broadcast = ".".join(parts[:3] + ["255"])

    return {
        "computer": socket.gethostname(),
        "adapter": str(adapter.get("name") or "Primary adapter"),
        "description": str(adapter.get("desc") or ""),
        "mac": mac,
        "ip": ip,
        "prefix": prefix,
        "broadcast": broadcast or "255.255.255.255",
        "wol_port": 9,
        "status": str(adapter.get("status") or "Up"),
        "link_speed": str(adapter.get("speed") or ""),
        "wol_note": (
            "Wake-on-LAN BIOS/UEFI ve ag kartinda etkin olmali. "
            "Telefon ile PC ayni LAN/Wi-Fi'da olmali."
        ),
    }


def _delayed_sleep() -> None:
    time.sleep(1.2)
    # Suspend=False = sleep, forceCritical=True helps avoid app vetoes.
    ok = bool(ctypes.windll.powrprof.SetSuspendState(False, True, False))
    if not ok:
        raise ctypes.WinError()


def _lock_active_console_session() -> None:
    """Lock/disconnect the real interactive desktop even when Bridge runs as SYSTEM.

    Persistent Bridge is installed as an AtStartup SYSTEM scheduled task, so
    user32.LockWorkStation() would only target Session 0. WTSDisconnectSession
    targets the active console user's session and returns it to the Windows
    sign-in/lock screen while keeping apps running.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

    kernel32.WTSGetActiveConsoleSessionId.argtypes = []
    kernel32.WTSGetActiveConsoleSessionId.restype = ctypes.c_uint32

    session_id = int(kernel32.WTSGetActiveConsoleSessionId())
    if session_id == 0xFFFFFFFF:
        raise RuntimeError("Aktif Windows konsol oturumu bulunamadi.")

    wtsapi32.WTSDisconnectSession.argtypes = [
        ctypes.c_void_p,   # WTS_CURRENT_SERVER_HANDLE = NULL
        ctypes.c_uint32,   # SessionId
        ctypes.c_bool,     # bWait
    ]
    wtsapi32.WTSDisconnectSession.restype = ctypes.c_bool

    ctypes.set_last_error(0)
    ok = bool(wtsapi32.WTSDisconnectSession(None, session_id, False))
    if not ok:
        err = ctypes.get_last_error()
        raise ctypes.WinError(err or None)


def _delayed_lock() -> None:
    time.sleep(0.6)
    try:
        _lock_active_console_session()
        return
    except Exception:
        # Fallback for unusual environments where the Bridge happens to run
        # inside the user's own interactive session.
        if ctypes.windll.user32.LockWorkStation():
            return
        raise


def execute_power_action(action: str) -> dict[str, Any]:
    key = str(action or "").strip().casefold()
    if key not in _ALLOWED:
        raise ValueError("Desteklenmeyen power action.")

    if os.name != "nt":
        raise RuntimeError("RIP POWER şu anda Windows için etkin.")

    if key == "lock":
        threading.Thread(target=_delayed_lock, daemon=True, name="rip-power-lock").start()
        return {"action": key, "message": "PC kilitleniyor."}

    if key == "sleep":
        threading.Thread(target=_delayed_sleep, daemon=True, name="rip-power-sleep").start()
        return {"action": key, "message": "PC uyku moduna alınıyor."}

    if key == "restart":
        subprocess.Popen(
            ["shutdown.exe", "/r", "/t", "10", "/c", "RIP Phone Bridge: yeniden başlatma"],
            creationflags=_creationflags(),
        )
        return {
            "action": key,
            "message": "PC 10 saniye içinde yeniden başlayacak.",
            "can_abort": True,
        }

    if key == "shutdown":
        subprocess.Popen(
            ["shutdown.exe", "/s", "/t", "10", "/c", "RIP Phone Bridge: uzaktan kapatma"],
            creationflags=_creationflags(),
        )
        return {
            "action": key,
            "message": "PC 10 saniye içinde kapanacak.",
            "can_abort": True,
        }

    # abort
    cp = subprocess.run(
        ["shutdown.exe", "/a"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=_creationflags(),
    )
    if cp.returncode != 0:
        msg = (cp.stderr or cp.stdout or "Bekleyen kapatma/yeniden başlatma bulunamadı.").strip()
        raise RuntimeError(msg)
    return {"action": key, "message": "Bekleyen kapatma/yeniden başlatma iptal edildi."}
