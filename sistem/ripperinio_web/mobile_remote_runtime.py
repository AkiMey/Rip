from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SYSTEM_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = Path(__file__).resolve().parent
if str(SYSTEM_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEM_DIR))

from bridge_shared import data_file, token
from version import VERSION
from core.logging_utils import append_log, configure_utf8_stdio

PORT = 8770
PID_FILE = data_file("mobile_remote_runtime.json")
LOG_FILE = data_file("mobile_remote.log")
SERVER_LOG = data_file("mobile_remote_server.log")
AGENT_LOG = data_file("mobile_remote_agent.log")


def log(text: str) -> None:
    append_log(LOG_FILE, text)


def tail(path: Path, max_lines: int = 18) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception:
        return ""


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def existing_runtime_alive() -> bool:
    try:
        raw = json.loads(PID_FILE.read_text(encoding="utf-8"))
        return pid_alive(int(raw.get("pid") or 0))
    except Exception:
        return False


def port_open(host: str = "127.0.0.1", port: int = PORT, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def remote_server_alive(timeout: float = 0.65) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/mode", timeout=timeout) as r:
            return 200 <= int(getattr(r, "status", 200)) < 300
    except Exception:
        return False


def hidden_flags() -> int:
    return (
        int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        | int(getattr(subprocess, "DETACHED_PROCESS", 0))
    )



def windowless_python() -> str:
    """Force pythonw.exe for background server/agent children on Windows."""
    if os.name != "nt" or bool(getattr(sys, "frozen", False)):
        return sys.executable
    current = Path(sys.executable) if sys.executable else Path("python.exe")
    sibling = current.with_name("pythonw.exe")
    if sibling.exists():
        return str(sibling)
    candidate = SYSTEM_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if candidate.exists():
        return str(candidate)
    return sys.executable

def open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8", errors="replace", buffering=1)


def launch_server(env: dict[str, str]):
    server_py = WEB_DIR / "mobile_remote_server.py"
    out = open_log(SERVER_LOG)
    log("server child baslatiliyor")
    command = (
        [sys.executable, "--mobile-server"]
        if bool(getattr(sys, "frozen", False))
        else [windowless_python(), str(server_py)]
    )
    p = subprocess.Popen(
        command,
        cwd=str(SYSTEM_DIR),
        env=env,
        stdout=out,
        stderr=subprocess.STDOUT,
        creationflags=hidden_flags(),
        close_fds=True,
    )
    p._rip_log_handle = out
    return p


def launch_agent(env: dict[str, str], access_token: str):
    out = open_log(AGENT_LOG)
    if bool(getattr(sys, "frozen", False)):
        cmd = [
            sys.executable, "--web-agent",
            "--server", f"ws://127.0.0.1:{PORT}",
            "--token", access_token,
        ]
    else:
        cmd = [
            windowless_python(), str(WEB_DIR / "agent.py"),
            "--server", f"ws://127.0.0.1:{PORT}",
            "--token", access_token,
        ]
    log("agent child baslatiliyor")
    p = subprocess.Popen(
        cmd,
        cwd=str(SYSTEM_DIR),
        env=env,
        stdout=out,
        stderr=subprocess.STDOUT,
        creationflags=hidden_flags(),
        close_fds=True,
    )
    p._rip_log_handle = out
    return p


def terminate(proc) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        getattr(proc, "_rip_log_handle", None).close()
    except Exception:
        pass


def run_daemon() -> int:
    if existing_runtime_alive():
        return 0

    access_token = token()
    if not access_token:
        log("HATA: persistent bridge token yok")
        return 2

    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(json.dumps({
        "pid": os.getpid(),
        "port": PORT,
        "started_at": int(time.time()),
    }), encoding="utf-8")

    env = os.environ.copy()
    env["RIPPERINIO_WEB_TOKEN"] = access_token
    env.pop("RIPPERINIO_PUBLIC", None)

    # Windows Turkish locale (cp1254) cannot encode the emoji used by
    # server.py / agent.py log messages. A failed print used to crash the
    # WebSocket handler and the agent, causing Android EOFException loops.
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8:replace"

    server_proc = None
    agent_proc = None
    server_failures = 0

    log(f"RIP Mobile Remote V{VERSION} daemon START")
    try:
        while True:
            if not remote_server_alive():
                if server_proc is not None and server_proc.poll() is not None:
                    code = server_proc.returncode
                    log(f"server child kapandi code={code}")
                    terminate(server_proc)
                    server_proc = None
                    server_failures += 1

                # If port is occupied but /mode is not ours, don't spawn-loop forever.
                if port_open() and not remote_server_alive():
                    log("HATA: TCP 8770 baska bir surec tarafindan kullaniliyor.")
                    time.sleep(4.0)
                    continue

                if server_proc is None:
                    server_proc = launch_server(env)

                for _ in range(28):
                    if remote_server_alive():
                        server_failures = 0
                        log("server LIVE")
                        break
                    if server_proc.poll() is not None:
                        break
                    time.sleep(0.25)

                if not remote_server_alive() and server_proc.poll() is not None:
                    log("server acilamadi. son log:\n" + tail(SERVER_LOG, 12))
                    time.sleep(min(8.0, 1.5 + server_failures))

            if remote_server_alive():
                if agent_proc is None or agent_proc.poll() is not None:
                    if agent_proc is not None:
                        terminate(agent_proc)
                    agent_proc = launch_agent(env, access_token)

            time.sleep(2.0)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        import traceback
        log("RUNTIME FATAL:\n" + traceback.format_exc())
        return 3
    finally:
        terminate(agent_proc)
        terminate(server_proc)
        try:
            raw = json.loads(PID_FILE.read_text(encoding="utf-8"))
            if int(raw.get("pid") or 0) == os.getpid():
                PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        log("RIP Mobile Remote daemon STOP")
    return 0


def print_status() -> int:
    runtime = existing_runtime_alive()
    live = remote_server_alive()
    print(f"RIP MOBILE REMOTE V{VERSION}")
    print("Runtime :", "AKTIF" if runtime else "KAPALI")
    print("Server  :", "LIVE" if live else "KAPALI")
    print("Port    :", PORT, "(ACIK)" if port_open() else "(BOS)")
    print("Token   :", "HAZIR" if token() else "YOK")
    print("Runtime log:", LOG_FILE)
    print("Server log :", SERVER_LOG)
    print("Agent log  :", AGENT_LOG)

    if not live:
        s = tail(SERVER_LOG, 20)
        if s:
            print()
            print("----- SERVER SON HATA / LOG -----")
            print(s)
        r = tail(LOG_FILE, 12)
        if r:
            print()
            print("----- RUNTIME SON LOG -----")
            print(r)
    return 0


def main() -> int:
    configure_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        return print_status()
    return run_daemon()


if __name__ == "__main__":
    raise SystemExit(main())
