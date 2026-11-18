from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from bridge_shared import PORT, SCREEN_STREAM_PORT, endpoints, pairing_url, public_info, token, token_key_id
from core.logging_utils import configure_utf8_stdio
from version import PROTOCOL_VERSION, VERSION

WEB_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = WEB_DIR.parent
ROOT = SYSTEM_DIR.parent
TASK_NAME = "Ripperinio V5 Persistent Bridge"
MOBILE_PORT = 8770


def _fetch_json(url: str, timeout: float = 0.6) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if not (200 <= int(getattr(response, "status", 200)) < 300):
                return {}
            raw = response.read(256 * 1024)
        import json
        value = json.loads(raw.decode("utf-8", errors="replace"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _http_ok(url: str, timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(getattr(response, "status", 200)) < 300
    except Exception:
        return False


def _health_payload(timeout: float = 0.6) -> dict:
    return _fetch_json(f"http://127.0.0.1:{PORT}/health", timeout)


def _is_current_bridge_health(info: dict) -> bool:
    if not isinstance(info, dict) or info.get("ok") is not True:
        return False
    if str(info.get("service") or "") != "RIP Persistent Bridge":
        return False
    try:
        protocol = int(info.get("protocol_version"))
    except Exception:
        return False
    if protocol != int(PROTOCOL_VERSION):
        return False
    # V6.2.1: a bridge from the previous source folder can keep 8765 alive after
    # an upgrade. Protocol/token alone are not enough to identify it because they
    # intentionally survive upgrades. Require this exact build and the screen
    # stream capability so V6.1.0 cannot masquerade as the V6.2.1 bridge.
    if str(info.get("version") or "") != str(VERSION):
        return False
    try:
        screen_port = int(info.get("screen_stream_tcp_port") or 0)
    except Exception:
        return False
    if screen_port != int(SCREEN_STREAM_PORT):
        return False
    expected_kid = token_key_id(token())
    return bool(expected_kid) and str(info.get("key_id") or "") == expected_kid


def _is_legacy_rip_bridge(info: dict) -> bool:
    return (
        isinstance(info, dict)
        and info.get("ok") is True
        and str(info.get("service") or "") == "RIP Persistent Bridge"
        and not _is_current_bridge_health(info)
    )


def _health(timeout: float = 0.6) -> bool:
    # Core bridge health. Screen TCP readiness is checked separately so a local
    # 8766 bind conflict can still fall back to reliable HTTP-frame transport.
    return _is_current_bridge_health(_health_payload(timeout))


def _powershell_text(command: str, timeout: float = 5.0) -> str:
    if os.name != "nt":
        return ""
    try:
        cp = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (cp.stdout or "").strip()
    except Exception:
        return ""


def _listener_pid(port: int) -> int | None:
    raw = _powershell_text(
        f"Get-NetTCPConnection -State Listen -LocalPort {int(port)} "
        "-ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess"
    )
    try:
        return int(raw.splitlines()[0].strip()) if raw else None
    except Exception:
        return None


def _screen_listener_ready() -> bool:
    """Verify that 8766 is the authenticated RIP screen listener.

    This is stronger than merely checking whether the port is open and avoids
    expensive PowerShell PID queries during normal HUD startup.
    """
    import socket
    secret = token().encode("utf-8", errors="ignore")
    if not secret or len(secret) > 512:
        return False
    try:
        with socket.create_connection(("127.0.0.1", int(SCREEN_STREAM_PORT)), timeout=0.35) as sock:
            sock.settimeout(0.35)
            sock.sendall(b"RIPS" + len(secret).to_bytes(2, "big") + secret)
            return sock.recv(1) == b"\x01"
    except Exception:
        return False



def _process_command_line(pid: int) -> str:
    return _powershell_text(
        f"(Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\" "
        "-ErrorAction SilentlyContinue).CommandLine"
    )


def _terminate_rip_bridge_pid(pid: int) -> bool:
    if not pid or pid == os.getpid():
        return False
    cmdline = _process_command_line(pid).casefold()
    if not any(mark in cmdline for mark in (
        "persistent_bridge_server.py", "--persistent-bridge", "ripperinio"
    )):
        return False
    try:
        cp = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=6, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if cp.returncode == 0:
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                if not _http_ok(f"http://127.0.0.1:{PORT}/health", 0.25):
                    break
                time.sleep(0.15)
            return True
    except Exception:
        pass
    return False


def _stop_stale_bridge_if_needed() -> bool:
    """Stop an older/incomplete RIP bridge occupying 8765.

    A bridge can be stale in two ways: its /health belongs to an older build, or
    it reports this build while the 8766 screen listener failed to bind. Both
    cases break continuous screen streaming while normal commands still work.
    """
    info = _health_payload(0.8)
    if not isinstance(info, dict) or info.get("ok") is not True:
        return False
    if str(info.get("service") or "") != "RIP Persistent Bridge":
        return False
    needs_restart = (not _is_current_bridge_health(info)) or (not _screen_listener_ready())
    if not needs_restart:
        return False
    pid = _listener_pid(PORT)
    return _terminate_rip_bridge_pid(pid or 0)



def _ensure_current_bridge() -> bool:
    core_ok = _health()
    screen_ok = _screen_listener_ready() if core_ok else False
    if core_ok and screen_ok:
        return True

    # Upgrade self-heal: restart once when an older build owns 8765 or when the
    # current bridge came up without its 8766 side-channel. If 8766 still cannot
    # bind after restart, keep 8765 alive; Android will use HTTP-FRAME fallback.
    _stop_stale_bridge_if_needed()
    if not _health():
        _local_start()
        _wait(_health, 10.0)
    return _health()

def _mobile_health(timeout: float = 0.7) -> bool:
    return _http_ok(f"http://127.0.0.1:{MOBILE_PORT}/mode", timeout)


def _background_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
        getattr(subprocess, "DETACHED_PROCESS", 0)
    )


def _windowless_python() -> str:
    """Return pythonw.exe for source-mode background services on Windows."""
    if os.name != "nt" or bool(getattr(sys, "frozen", False)):
        return sys.executable
    current = Path(sys.executable) if sys.executable else Path("python.exe")
    sibling = current.with_name("pythonw.exe")
    if sibling.exists():
        return str(sibling)
    venv = SYSTEM_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if venv.exists():
        return str(venv)
    return sys.executable


def _spawn(mode: str) -> None:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8:replace"

    frozen = bool(getattr(sys, "frozen", False))
    if mode == "bridge":
        cmd = (
            [sys.executable, "--persistent-bridge"]
            if frozen
            else [_windowless_python(), str(WEB_DIR / "persistent_bridge_server.py")]
        )
    elif mode == "mobile":
        cmd = (
            [sys.executable, "--mobile-remote"]
            if frozen
            else [_windowless_python(), str(WEB_DIR / "mobile_remote_runtime.py"), "--daemon"]
        )
    else:
        raise ValueError(mode)

    subprocess.Popen(
        cmd,
        cwd=str(SYSTEM_DIR),
        env=env,
        creationflags=_background_flags(),
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _task_start() -> None:
    """Start the optional scheduled bridge task if the user installed it."""
    try:
        subprocess.run(
            ["schtasks.exe", "/Run", "/TN", TASK_NAME],
            capture_output=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def _local_start() -> None:
    try:
        _spawn("bridge")
    except Exception:
        pass


def _start_mobile() -> None:
    try:
        _spawn("mobile")
    except Exception:
        pass


def _wait(check, seconds: float = 6.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.25)
    return bool(check())


def ensure_phone_services() -> dict[str, bool]:
    """Bring up the always-on phone services without blocking the UI thread.

    Safe to call repeatedly: the bridge has version/key self-heal and the
    Mobile Remote runtime has its own singleton/PID guard.
    """
    bridge_ok = _ensure_current_bridge()
    mobile_ok = _mobile_health()
    if not mobile_ok:
        _start_mobile()
        mobile_ok = _wait(_mobile_health, 8.0)
    return {"bridge": bool(bridge_ok), "mobile": bool(mobile_ok)}


def _mobile_url() -> str:
    eps = endpoints()
    base = eps.get("tailscale") or eps.get("lan") or f"http://127.0.0.1:{PORT}"
    parsed = urllib.parse.urlsplit(base)
    host = parsed.hostname or "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{parsed.scheme or 'http'}://{host}:{MOBILE_PORT}/?t={urllib.parse.quote(token())}"


def _print_qr(value: str) -> None:
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(value)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except Exception:
        pass


class PhoneServer:
    """HUD facade for the persistent V5 phone bridge.

    Closing the HUD does not stop the bridge. If a scheduled task is not
    installed, V5 falls back to launching the bridge directly.
    """

    def __init__(self) -> None:
        self.token = token()
        self.state = "running" if _health() else "stopped"

    def _info(self, message: str = "") -> dict:
        info = public_info()
        running = _health()
        self.state = "running" if running else "stopped"
        return {
            "state": self.state,
            "message": message
            or (
                "Kalici Bridge aktif · QR sadece ilk eslestirme icin"
                if running
                else "Kalici Bridge kapali · HUD baslatmayi deneyecek"
            ),
            "url": info["pair_url"] if running else "",
            "token": info["token"],
            "key_id": info.get("key_id", ""),
            "protocol_version": info.get("protocol_version", 5),
            "tunnel": bool(info["tailscale"]),
            "persistent": True,
            "lan": info["lan"],
            "tailscale": info["tailscale"],
        }

    def start(self, callback: Callable | None = None) -> None:
        self.state = "starting"

        def worker():
            _ensure_current_bridge()
            if callback:
                callback(self._info())

        threading.Thread(target=worker, daemon=True, name="rip-persistent-bridge-start").start()

    def stop(self, callback: Callable | None = None) -> None:
        self.state = "running" if _health() else "stopped"
        if callback:
            callback(self._info("Kalici Bridge HUD'dan bagimsiz; HUD kapaninca calismaya devam eder."))


def run_orchestrator() -> int:
    """Compatibility entry point for `RIP --web`.

    Starts/observes the persistent bridge (8765) and Mobile Remote (8770), then
    prints the pairing URLs. It intentionally does not create a second 8765 web
    server, avoiding the old port collision between the web server and bridge.
    """
    configure_utf8_stdio()
    print(f"RIPPERINIO V{VERSION} · PHONE SERVICES")
    print("-" * 52)

    ensure_phone_services()

    info = public_info()
    print("Bridge :", "LIVE" if _health() else "KAPALI", f"port {PORT}")
    print("Mobile :", "LIVE" if _mobile_health() else "KAPALI", f"port {MOBILE_PORT}")
    print("Pair ID:", info.get("key_id", ""))
    print("Bridge pairing URL:")
    print(info["pair_url"])
    print()
    print("Mobile Remote URL:")
    mobile_url = _mobile_url()
    print(mobile_url)
    print()
    _print_qr(mobile_url if _mobile_health() else info["pair_url"])

    if not _health() or not _mobile_health():
        print("\n[UYARI] Servislerden biri acilamadi. scripts\\DIAGNOSE.bat calistir.")
        return 1
    return 0
