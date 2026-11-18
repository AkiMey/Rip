from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def _data_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "RIPPERINIO"
    return Path.home() / ".ripperinio"


def _tail(path: Path, max_chars: int = 2200) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[-max_chars:]
    except Exception:
        return ""


def _message_box(title: str, message: str) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    except Exception:
        pass


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    system = root / "sistem"
    py = system / ".venv" / "Scripts" / "python.exe"
    main_py = system / "main.py"

    if not py.exists() or not main_py.exists():
        _message_box(
            "RIP baslatilamadi",
            "Python ortami veya sistem\\main.py bulunamadi. BASLAT.bat ile kurulumu tekrar calistir.",
        )
        return 2

    logs = _data_root() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "rip_runtime.log"

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8:replace"
    # pythonw + faulthandler kombinasyonu bazi Windows surumlerinde daha
    # Python kodu calismadan kapanabiliyor. Ana surec normal python.exe ile,
    # fakat CREATE_NO_WINDOW bayragiyla konsolsuz calisir.
    env.pop("PYTHONFAULTHANDLER", None)

    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        log.write("\n=== RIP V6.2.1 START ===\n")
        log.flush()
        try:
            proc = subprocess.Popen(
                [str(py), str(main_py)],
                cwd=str(system),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
                close_fds=True,
            )
        except Exception as exc:
            log.write(f"Launcher error: {type(exc).__name__}: {exc}\n")
            log.flush()
            _message_box(
                "RIP baslatilamadi",
                f"RIP sureci baslatilamadi:\n{type(exc).__name__}: {exc}\n\nLog: {log_path}",
            )
            return 3

        # Baslangic hatalarinin sessizce kaybolmasini onle. Normal acilista
        # launcher birkac saniye sonra cikar ve RIP arka planda yasamaya devam eder.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            code = proc.poll()
            if code is not None:
                log.flush()
                detail = _tail(log_path)
                _message_box(
                    "RIP acilamadi",
                    "RIP baslangicta kapandi. Son hata kaydi:\n\n"
                    + (detail or f"Cikis kodu: {code}")
                    + f"\n\nTam log: {log_path}",
                )
                return int(code or 1)
            time.sleep(0.2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
