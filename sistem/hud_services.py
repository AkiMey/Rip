from __future__ import annotations

"""RIPPERINIO HUD V2 background services.

The module intentionally keeps global keyboard monitoring privacy-preserving:
ordinary text keys are never stored. Only modifier shortcuts (Ctrl/Alt/Win + key)
and a small set of standalone system/function keys are emitted to the history.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import psutil

from app_paths import data_path


# ---------------------------------------------------------------------------
# Small JSON helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except Exception:
        return default


def _atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _safe_text(value: Any, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[:limit]


# ---------------------------------------------------------------------------
# Command/action history + selective undo
# ---------------------------------------------------------------------------

HISTORY_FILE = data_path("hud_action_history.json")


class ActionHistory:
    MAX_ITEMS = 800

    def __init__(self, path: Path = HISTORY_FILE):
        self.path = path
        self._lock = threading.RLock()
        rows = _read_json(path, [])
        self._rows: list[dict[str, Any]] = rows if isinstance(rows, list) else []
        self._rows = self._rows[-self.MAX_ITEMS :]
        self._listeners: list[Callable[[list[dict[str, Any]]], None]] = []

    def subscribe(self, callback: Callable[[list[dict[str, Any]]], None]) -> None:
        if callable(callback):
            self._listeners.append(callback)

    def _notify(self) -> None:
        rows = self.recent(40)
        for cb in list(self._listeners):
            try:
                cb(rows)
            except Exception:
                pass

    def add(
        self,
        kind: str,
        label: str,
        details: str = "",
        *,
        undo: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = {
            "id": uuid.uuid4().hex,
            "ts": int(time.time() * 1000),
            "kind": _safe_text(kind, 40),
            "label": _safe_text(label, 260),
            "details": _safe_text(details, 1200),
            "undo": undo if isinstance(undo, dict) else None,
            "undone": False,
            "meta": meta if isinstance(meta, dict) else {},
        }
        with self._lock:
            self._rows.append(row)
            self._rows = self._rows[-self.MAX_ITEMS :]
            try:
                _atomic_json(self.path, self._rows)
            except Exception:
                pass
        self._notify()
        return row

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._rows[-max(1, min(int(limit or 20), 100)) :]][::-1]

    def format_recent(self, limit: int = 12) -> str:
        rows = self.recent(limit)
        if not rows:
            return "Henüz kayıtlı RIP/Windows işlem geçmişi yok."
        lines = []
        for r in rows:
            try:
                stamp = time.strftime("%H:%M:%S", time.localtime(int(r.get("ts", 0)) / 1000))
            except Exception:
                stamp = "--:--:--"
            undoable = " ↶" if r.get("undo") and not r.get("undone") else ""
            lines.append(f"[{stamp}] {r.get('label', '')}{undoable}")
        return "\n".join(lines)

    @staticmethod
    def capture_undo_for_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """Capture a *pre-action* state for operations we can safely reverse."""
        name = str(tool_name or "").strip()
        args = dict(args or {})

        if name == "control_volume":
            try:
                from actions.system_volume import SystemVolume

                state = SystemVolume().get_state()
                if state.get("available"):
                    return {
                        "type": "volume",
                        "volume": int(state.get("volume", 0)),
                        "muted": bool(state.get("muted", False)),
                    }
            except Exception:
                return None

        if name == "window_control" and os.name == "nt":
            action = str(args.get("action", "")).casefold()
            if action in {"list", "listele"}:
                return None
            title = _safe_text(args.get("title"), 240)
            if not title:
                return None
            try:
                import win32gui

                matches: list[tuple[int, str]] = []

                def cb(hwnd, _):
                    try:
                        if win32gui.IsWindowVisible(hwnd):
                            text = str(win32gui.GetWindowText(hwnd) or "")
                            if title.casefold() in text.casefold():
                                matches.append((hwnd, text))
                    except Exception:
                        pass

                win32gui.EnumWindows(cb, None)
                if matches:
                    hwnd, matched = matches[0]
                    placement = win32gui.GetWindowPlacement(hwnd)
                    rect = win32gui.GetWindowRect(hwnd)
                    return {
                        "type": "window",
                        "hwnd": int(hwnd),
                        "title": matched,
                        "placement": list(placement),
                        "rect": list(rect),
                    }
            except Exception:
                return None

        if name == "file_control":
            action = str(args.get("action", "")).strip().casefold()
            if action in {"move", "rename", "tasi", "taşı", "yeniden adlandır", "yeniden adlandir"}:
                source = str(args.get("source") or "").strip()
                target = str(args.get("target") or "").strip()
                if source and target:
                    try:
                        src = Path(source).expanduser().resolve()
                        dst = Path(target).expanduser().resolve()
                        if src.exists() and not dst.exists():
                            return {"type": "file_move", "source": str(src), "target": str(dst)}
                    except Exception:
                        pass

        if name == "hud_theme":
            try:
                from actions.theme_control import get_hud_theme

                return {"type": "hud_theme", "theme": str(get_hud_theme() or "classic")}
            except Exception:
                return None

        if name == "clipboard_sync":
            action = str(args.get("action", "send") or "send").strip().casefold().replace("-", "_")
            # pull/set_pc PC panosunu degistirir; send yalnizca telefona yollar.
            if action in {"pull", "get", "from_phone", "telefondan", "get_from_phone", "set_pc", "pc", "computer"}:
                try:
                    import win32clipboard
                    previous = ""
                    win32clipboard.OpenClipboard()
                    try:
                        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                            previous = str(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) or "")
                    finally:
                        win32clipboard.CloseClipboard()
                    return {"type": "clipboard", "text": previous}
                except Exception:
                    return None

        return None

    def _mark_undone(self, row_id: str) -> None:
        with self._lock:
            for row in self._rows:
                if row.get("id") == row_id:
                    row["undone"] = True
                    break
            try:
                _atomic_json(self.path, self._rows)
            except Exception:
                pass
        self._notify()

    def undo_last(self) -> dict[str, Any]:
        """Undo the newest reversible action. Never guesses an inverse."""
        with self._lock:
            candidate = next(
                (dict(r) for r in reversed(self._rows) if r.get("undo") and not r.get("undone")),
                None,
            )
        if not candidate:
            return {"ok": False, "message": "Geri alınabilir son RIP işlemi yok."}

        u = dict(candidate.get("undo") or {})
        typ = str(u.get("type") or "")
        try:
            if typ == "volume":
                from actions.system_volume import SystemVolume

                vol = SystemVolume()
                ok, msg = vol.set_percent(int(u.get("volume", 0)))
                if ok:
                    vol.set_muted(bool(u.get("muted", False)))
                if not ok:
                    raise RuntimeError(msg)
                detail = f"Ses %{int(u.get('volume', 0))} değerine geri alındı."

            elif typ == "window":
                if os.name != "nt":
                    raise RuntimeError("Pencere geri alma yalnızca Windows'ta çalışır.")
                import win32gui

                hwnd = int(u.get("hwnd", 0))
                if not hwnd or not win32gui.IsWindow(hwnd):
                    raise RuntimeError("Geri alınacak pencere artık açık değil.")
                placement = tuple(u.get("placement") or ())
                rect = list(u.get("rect") or [])
                if len(placement) == 5:
                    try:
                        win32gui.SetWindowPlacement(hwnd, placement)
                    except Exception:
                        pass
                if len(rect) == 4:
                    left, top, right, bottom = map(int, rect)
                    win32gui.MoveWindow(hwnd, left, top, max(120, right-left), max(80, bottom-top), True)
                detail = f"Pencere düzeni geri alındı: {u.get('title', '')}"

            elif typ == "clipboard":
                if os.name != "nt":
                    raise RuntimeError("Pano geri alma yalnizca Windows'ta calisir.")
                import win32clipboard
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(str(u.get("text") or ""), win32clipboard.CF_UNICODETEXT)
                finally:
                    win32clipboard.CloseClipboard()
                detail = "PC panosu onceki haline geri alindi."

            elif typ == "file_copy":
                target = Path(str(u.get("target") or ""))
                if not target.is_file():
                    raise RuntimeError("Geri alinacak kopya dosya bulunamadi.")
                expected_size = int(u.get("size", -1) or -1)
                expected_mtime = int(u.get("mtime_ns", -1) or -1)
                stat = target.stat()
                if expected_size >= 0 and stat.st_size != expected_size:
                    raise RuntimeError("Kopya dosya sonradan degismis; guvenlik icin silinmedi.")
                if expected_mtime >= 0 and getattr(stat, "st_mtime_ns", -2) != expected_mtime:
                    raise RuntimeError("Kopya dosya sonradan degismis; guvenlik icin silinmedi.")
                target.unlink()
                detail = f"Olusturulan kopya geri alindi: {target.name}"

            elif typ == "file_move":
                src = Path(str(u.get("source") or ""))
                dst = Path(str(u.get("target") or ""))
                if not dst.exists():
                    raise RuntimeError("Taşınan hedef dosya artık bulunmuyor.")
                if src.exists():
                    raise RuntimeError("Eski konumda aynı isimli bir dosya var; üzerine yazmadan geri alma durduruldu.")
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(src))
                detail = f"Dosya eski konumuna geri taşındı: {src.name}"

            elif typ == "hud_theme":
                from actions.theme_control import set_hud_theme

                theme = str(u.get("theme") or "classic")
                detail = str(set_hud_theme(theme) or f"HUD teması {theme} olarak geri alındı.")

            else:
                return {"ok": False, "message": "Bu işlemin güvenli geri alma uygulaması yok."}

            self._mark_undone(str(candidate.get("id")))
            self.add("undo", f"GERİ AL → {candidate.get('label', '')}", detail)
            return {
                "ok": True,
                "message": detail,
                "undone": candidate,
                "post": {"theme": u.get("theme")} if typ == "hud_theme" else {},
            }
        except Exception as exc:
            return {"ok": False, "message": f"Geri alma tamamlanamadı: {exc}"}


ACTION_HISTORY = ActionHistory()


def history_control(action: str = "latest", limit: int = 12) -> str:
    key = str(action or "latest").strip().casefold()
    if key in {"latest", "list", "history", "gecmis", "geçmiş", "son"}:
        return ACTION_HISTORY.format_recent(limit)
    if key in {"undo", "geri al", "geri_al", "revert"}:
        return str(ACTION_HISTORY.undo_last().get("message") or "Geri alma tamamlandı.")
    return f"Bilinmeyen geçmiş işlemi: {action}"


# ---------------------------------------------------------------------------
# Privacy-preserving global shortcut monitor
# ---------------------------------------------------------------------------

class GlobalShortcutMonitor:
    """Observe shortcut chords without storing ordinary typed characters."""

    MODIFIERS = {
        "ctrl": "CTRL", "left ctrl": "CTRL", "right ctrl": "CTRL",
        "alt": "ALT", "left alt": "ALT", "right alt": "ALT", "alt gr": "ALTGR",
        "shift": "SHIFT", "left shift": "SHIFT", "right shift": "SHIFT",
        "windows": "WIN", "left windows": "WIN", "right windows": "WIN",
    }
    STANDALONE = {
        "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
        "print screen", "pause", "insert", "delete", "home", "end", "page up", "page down",
        "volume up", "volume down", "volume mute", "play/pause media", "next track", "previous track",
    }

    def __init__(
        self,
        root,
        on_shortcut: Callable[[str], None] | None = None,
        on_palette: Callable[[], None] | None = None,
        on_dashboard: Callable[[], None] | None = None,
    ):
        self.root = root
        self.on_shortcut = on_shortcut
        self.on_palette = on_palette
        self.on_dashboard = on_dashboard
        self.available = False
        self.error = ""
        self._keyboard = None
        self._hook = None
        self._pressed_mods: set[str] = set()
        self._last_emit: tuple[str, float] = ("", 0.0)

    def start(self) -> bool:
        if os.name != "nt":
            return False
        try:
            import keyboard

            self._keyboard = keyboard
            self._hook = keyboard.hook(self._event, suppress=False)
            self.available = True
        except Exception as exc:
            self.error = str(exc)
            self.available = False
        return self.available

    def _dispatch(self, callback, *args):
        if not callable(callback):
            return
        try:
            self.root.after(0, lambda: callback(*args))
        except Exception:
            pass

    @staticmethod
    def _display_key(name: str) -> str:
        aliases = {
            "space": "SPACE", "esc": "ESC", "escape": "ESC", "tab": "TAB",
            "enter": "ENTER", "backspace": "BACKSPACE", "delete": "DELETE",
            "up": "↑", "down": "↓", "left": "←", "right": "→",
            "page up": "PGUP", "page down": "PGDN", "print screen": "PRTSC",
        }
        return aliases.get(name, name.upper())

    def _emit(self, chord: str):
        now = time.monotonic()
        if self._last_emit[0] == chord and now - self._last_emit[1] < 0.18:
            return
        self._last_emit = (chord, now)
        self._dispatch(self.on_shortcut, chord)
        if chord == "CTRL+SPACE":
            self._dispatch(self.on_palette)
        elif chord == "CTRL+SHIFT+SPACE":
            self._dispatch(self.on_dashboard)

    def _event(self, event):
        name = str(getattr(event, "name", "") or "").casefold().strip()
        etype = str(getattr(event, "event_type", "") or "").casefold()
        if not name:
            return

        mod = self.MODIFIERS.get(name)
        if mod:
            if etype == "down":
                self._pressed_mods.add(mod)
            elif etype == "up":
                self._pressed_mods.discard(mod)
            return

        if etype != "down":
            return

        # Critical privacy rule: if there is no modifier, ordinary letters,
        # digits and punctuation are ignored entirely.
        if not self._pressed_mods and name not in self.STANDALONE:
            return

        order = [m for m in ("CTRL", "ALTGR", "ALT", "SHIFT", "WIN") if m in self._pressed_mods]
        key = self._display_key(name)
        chord = "+".join(order + [key]) if order else key
        self._emit(chord)

    def stop(self) -> None:
        try:
            if self._keyboard is not None and self._hook is not None:
                self._keyboard.unhook(self._hook)
        except Exception:
            pass
        self._hook = None
        self._pressed_mods.clear()


# ---------------------------------------------------------------------------
# Windows master-output audio peak for the media visualizer
# ---------------------------------------------------------------------------

class AudioPeakMonitor:
    def __init__(self, on_peak: Callable[[float], None], interval: float = 0.05):
        self.on_peak = on_peak
        self.interval = max(0.03, float(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def _meter():
        if os.name != "nt":
            return None
        from pycaw.pycaw import AudioUtilities

        device = AudioUtilities.GetSpeakers()
        direct = getattr(device, "AudioMeterInformation", None)
        if direct is not None:
            return direct

        from comtypes import CLSCTX_ALL
        from ctypes import POINTER, cast
        from pycaw.pycaw import IAudioMeterInformation

        interface = device.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioMeterInformation))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def run():
            # COM is apartment/thread scoped on Windows. pycaw/comtypes calls from a
            # plain Python worker thread without initializing COM can be unstable.
            com_uninit = None
            if os.name == "nt":
                try:
                    from comtypes import CoInitialize, CoUninitialize
                    CoInitialize()
                    com_uninit = CoUninitialize
                except Exception:
                    com_uninit = None

            meter = None
            smooth = 0.0
            try:
                while not self._stop.is_set():
                    try:
                        if meter is None:
                            meter = self._meter()
                        peak = float(meter.GetPeakValue()) if meter is not None else 0.0
                        peak = max(0.0, min(1.0, peak))
                        smooth = max(peak, smooth * 0.78)
                        self.on_peak(smooth)
                    except Exception:
                        meter = None
                        smooth *= 0.8
                        try:
                            self.on_peak(smooth)
                        except Exception:
                            pass
                    self._stop.wait(self.interval)
            finally:
                if com_uninit is not None:
                    try:
                        com_uninit()
                    except Exception:
                        pass

        self._thread = threading.Thread(target=run, name="rip-audio-peak", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Local-network device map (cached neighbor table; no aggressive port scan)
# ---------------------------------------------------------------------------

class NetworkDeviceScanner:
    def __init__(self):
        self._names: dict[str, str] = {}

    def scan(self, limit: int = 14) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        own_ips: set[str] = set()
        try:
            for addrs in psutil.net_if_addrs().values():
                for a in addrs:
                    if getattr(a.family, "name", "") == "AF_INET" or str(a.family).endswith("AF_INET"):
                        ip = str(a.address or "")
                        if ip and not ip.startswith("127."):
                            own_ips.add(ip)
        except Exception:
            pass

        host = socket.gethostname()
        for ip in sorted(own_ips):
            rows.append({"name": host, "ip": ip, "mac": "", "state": "online", "kind": "PC"})

        if os.name == "nt":
            try:
                cmd = (
                    "Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
                    "Where-Object {$_.IPAddress -notlike '224.*' -and $_.IPAddress -notlike '255.*'} | "
                    "Select-Object IPAddress,LinkLayerAddress,State | ConvertTo-Json -Compress"
                )
                cp = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command", cmd],
                    capture_output=True, text=True, encoding="utf-8", errors="ignore",
                    timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                data = json.loads(cp.stdout or "[]")
                if isinstance(data, dict):
                    data = [data]
                for item in data if isinstance(data, list) else []:
                    ip = str(item.get("IPAddress") or "").strip()
                    if not ip or ip in own_ips or ip.startswith("169.254."):
                        continue
                    state_raw = str(item.get("State") or "").casefold()
                    online = state_raw not in {"unreachable", "incomplete"}
                    rows.append({
                        "name": self._names.get(ip, "Cihaz"),
                        "ip": ip,
                        "mac": str(item.get("LinkLayerAddress") or ""),
                        "state": "online" if online else "offline",
                        "kind": "LAN",
                    })
            except Exception:
                pass
        else:
            try:
                cp = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=3)
                for ip, mac in re.findall(r"\((\d+\.\d+\.\d+\.\d+)\).*?([0-9a-fA-F:]{11,17})", cp.stdout):
                    if ip not in own_ips:
                        rows.append({"name": "Cihaz", "ip": ip, "mac": mac, "state": "online", "kind": "LAN"})
            except Exception:
                pass

        # Deduplicate by IP while preferring online records.
        dedup: dict[str, dict[str, str]] = {}
        for row in rows:
            ip = row.get("ip", "")
            if not ip:
                continue
            if ip not in dedup or row.get("state") == "online":
                dedup[ip] = row
        result = list(dedup.values())
        result.sort(key=lambda r: (r.get("state") != "online", r.get("ip", "")))
        return result[: max(1, int(limit))]


# ---------------------------------------------------------------------------
# Download tracker (browser + Steam + RIP internal transfers)
# ---------------------------------------------------------------------------

class DownloadTracker:
    TEMP_SUFFIXES = {".crdownload", ".part", ".download", ".tmp"}

    def __init__(
        self,
        on_update: Callable[[list[dict[str, Any]]], None] | None = None,
        on_complete: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.on_update = on_update
        self.on_complete = on_complete
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._manual: dict[str, dict[str, Any]] = {}
        self._manual_lock = threading.Lock()
        self._previous: dict[str, dict[str, Any]] = {}
        # V2 rate estimator: id -> {bytes, ts, speed}.  Hiz/sure tamamen
        # gozlenen byte degisiminden hesaplanir; sahte yuzde uretmez.
        self._rate_state: dict[str, dict[str, float]] = {}

    def register_manual(self, task_id: str, name: str, kind: str = "RIP", total: int = 0) -> None:
        with self._manual_lock:
            self._manual[task_id] = {
                "id": task_id, "name": _safe_text(name, 160), "kind": kind,
                "current": 0, "total": max(0, int(total or 0)), "status": "active",
                "updated": time.time(),
            }

    def update_manual(self, task_id: str, current: int, total: int | None = None, status: str = "active") -> None:
        with self._manual_lock:
            row = self._manual.get(task_id)
            if not row:
                return
            row["current"] = max(0, int(current or 0))
            if total is not None:
                row["total"] = max(0, int(total or 0))
            row["status"] = status
            row["updated"] = time.time()

    def finish_manual(self, task_id: str, ok: bool = True) -> None:
        with self._manual_lock:
            row = self._manual.get(task_id)
            if row:
                row["status"] = "done" if ok else "error"
                if row.get("total"):
                    row["current"] = row["total"]
                row["updated"] = time.time()

    @staticmethod
    def _downloads_dir() -> Path:
        if os.name == "nt":
            try:
                import winreg

                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
                raw, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
                return Path(os.path.expandvars(str(raw)))
            except Exception:
                pass
        return Path.home() / "Downloads"

    def _browser_tasks(self) -> list[dict[str, Any]]:
        root = self._downloads_dir()
        if not root.is_dir():
            return []
        rows = []
        now = time.time()
        try:
            for p in root.iterdir():
                if not p.is_file() or p.suffix.casefold() not in self.TEMP_SUFFIXES:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if now - st.st_mtime > 6 * 3600:
                    continue
                name = p.name
                for suffix in self.TEMP_SUFFIXES:
                    if name.casefold().endswith(suffix):
                        name = name[: -len(suffix)] or p.name
                        break
                rows.append({
                    "id": "browser:" + str(p), "name": name, "kind": "BROWSER",
                    "current": int(st.st_size), "total": 0, "status": "active",
                    "path": str(p),
                })
        except Exception:
            pass
        return rows

    @staticmethod
    def _steam_roots() -> list[Path]:
        roots: list[Path] = []
        if os.name == "nt":
            try:
                import winreg

                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
                value, _ = winreg.QueryValueEx(key, "SteamPath")
                roots.append(Path(str(value)))
            except Exception:
                pass
            for p in (
                Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Steam",
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Steam",
            ):
                if p not in roots:
                    roots.append(p)
        return [p for p in roots if p.exists()]

    def _steam_tasks(self) -> list[dict[str, Any]]:
        libraries: list[Path] = []
        for steam in self._steam_roots():
            steamapps = steam / "steamapps"
            libraries.append(steamapps)
            vdf = steamapps / "libraryfolders.vdf"
            try:
                text = vdf.read_text(encoding="utf-8", errors="ignore")
                for raw in re.findall(r'"path"\s+"([^"]+)"', text, flags=re.I):
                    p = Path(raw.replace("\\\\", "\\")) / "steamapps"
                    if p not in libraries:
                        libraries.append(p)
            except Exception:
                pass

        rows: list[dict[str, Any]] = []
        for steamapps in libraries:
            downloading = steamapps / "downloading"
            if not downloading.is_dir():
                continue
            try:
                appids = {p.name for p in downloading.iterdir() if p.is_dir() and p.name.isdigit()}
            except Exception:
                appids = set()
            for appid in appids:
                manifest = steamapps / f"appmanifest_{appid}.acf"
                text = ""
                try:
                    text = manifest.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass

                def field(name: str, default=""):
                    m = re.search(rf'"{re.escape(name)}"\s+"([^"]*)"', text, flags=re.I)
                    return m.group(1) if m else default

                name = field("name", f"Steam App {appid}")
                try:
                    current = int(field("BytesDownloaded", "0") or 0)
                except Exception:
                    current = 0
                try:
                    total = int(field("BytesToDownload", "0") or 0)
                except Exception:
                    total = 0
                if current <= 0:
                    # Cheap directory-size estimate if Steam manifest has no byte fields.
                    try:
                        current = sum(f.stat().st_size for f in (downloading / appid).rglob("*") if f.is_file())
                    except Exception:
                        current = 0
                rows.append({
                    "id": f"steam:{appid}", "name": name, "kind": "STEAM",
                    "current": current, "total": total, "status": "active",
                    "path": str(downloading / appid),
                })
        return rows

    def _epic_tasks(self) -> list[dict[str, Any]]:
        """Epic Launcher'in incomplete .item manifestlerini best-effort izler."""
        if os.name != "nt":
            return []
        manifest_dir = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
        if not manifest_dir.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        now = time.time()
        try:
            manifests = list(manifest_dir.glob("*.item"))
        except Exception:
            manifests = []
        for item in manifests:
            try:
                data = json.loads(item.read_text(encoding="utf-8", errors="ignore"))
                incomplete = bool(data.get("bIsIncompleteInstall", False))
                install = Path(str(data.get("InstallLocation") or "").strip())
                egstore = install / ".egstore" if install else Path()
                recent = False
                if egstore.is_dir():
                    try:
                        recent = any(now - f.stat().st_mtime < 180 for f in egstore.iterdir() if f.is_file())
                    except Exception:
                        recent = False
                if not incomplete and not recent:
                    continue
                name = str(data.get("DisplayName") or data.get("AppName") or item.stem)
                current = 0
                if install.is_dir():
                    # Pahali tam klasor taraması yapma; .egstore geçici parçaları yeterli.
                    try:
                        current = sum(f.stat().st_size for f in egstore.rglob("*") if f.is_file()) if egstore.is_dir() else 0
                    except Exception:
                        current = 0
                rows.append({
                    "id": f"epic:{data.get('AppName') or item.stem}",
                    "name": name, "kind": "EPIC", "current": int(current),
                    "total": 0, "status": "active", "path": str(install),
                })
            except Exception:
                continue
        return rows

    def _manual_tasks(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._manual_lock:
            # keep completed rows briefly so the UI reaches 100%
            stale = [k for k, v in self._manual.items() if v.get("status") in {"done", "error"} and now - float(v.get("updated", now)) > 5]
            for k in stale:
                self._manual.pop(k, None)
            return [dict(v) for v in self._manual.values()]

    def snapshot(self) -> list[dict[str, Any]]:
        rows = self._manual_tasks() + self._steam_tasks() + self._epic_tasks() + self._browser_tasks()
        now = time.monotonic()
        live_ids: set[str] = set()
        for row in rows:
            rid = str(row.get("id") or "")
            live_ids.add(rid)
            total = int(row.get("total", 0) or 0)
            current = int(row.get("current", 0) or 0)
            row["percent"] = max(0.0, min(100.0, current * 100.0 / total)) if total > 0 else None

            prev = self._rate_state.get(rid)
            speed = 0.0
            if prev:
                dt = max(0.001, now - float(prev.get("ts", now)))
                delta = current - int(prev.get("bytes", current))
                inst = max(0.0, float(delta) / dt)
                old_speed = max(0.0, float(prev.get("speed", 0.0)))
                # Hiz gostergesinin ziplamamasi icin EMA; indirme durursa hiz da hizla duser.
                speed = inst if old_speed <= 0 else (old_speed * 0.62 + inst * 0.38)
            self._rate_state[rid] = {"bytes": float(current), "ts": now, "speed": speed}
            row["speed_bps"] = speed
            if total > current and speed > 1024:
                row["eta_seconds"] = max(0.0, (total - current) / speed)
            else:
                row["eta_seconds"] = None

        # Kaybolmus gorevlerin rate kayitlarini temizle.
        for rid in list(self._rate_state):
            if rid not in live_ids:
                self._rate_state.pop(rid, None)
        return rows[:16]

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def run():
            while not self._stop.is_set():
                rows = self.snapshot()
                current = {str(r.get("id")): r for r in rows}
                # Manual tasks explicitly marked done are real completions.
                for r in rows:
                    if r.get("status") == "done" and self._previous.get(str(r.get("id")), {}).get("status") != "done":
                        if callable(self.on_complete):
                            try:
                                self.on_complete(dict(r))
                            except Exception:
                                pass
                # Steam tasks with known total that reached 100.
                for key, old in self._previous.items():
                    if key.startswith("steam:") and key not in current and old.get("percent") is not None and float(old.get("percent") or 0) >= 95:
                        if callable(self.on_complete):
                            try:
                                done = dict(old); done["status"] = "done"; done["percent"] = 100.0
                                self.on_complete(done)
                            except Exception:
                                pass
                self._previous = current
                if callable(self.on_update):
                    try:
                        self.on_update(rows)
                    except Exception:
                        pass
                self._stop.wait(2.0)

        self._thread = threading.Thread(target=run, name="rip-download-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Windows notification reader (WinRT, optional)
# ---------------------------------------------------------------------------

class WindowsNotificationWatcher:
    """Read Windows toast history in an isolated helper process.

    The WinRT notification bindings can terminate the interpreter with a native
    access violation on some Windows/Python combinations. Running that API in a
    short-lived child process keeps RIP alive even if WinRT itself crashes.
    """

    def __init__(self, on_update: Callable[[list[dict[str, Any]]], None], interval: float = 6.0):
        self.on_update = on_update
        self.interval = max(4.0, float(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error = ""
        self._probe = Path(__file__).with_name("win_notification_probe.py")

    def _read_isolated(self) -> list[dict[str, Any]]:
        if not self._probe.exists():
            self.error = "win_notification_probe.py bulunamadi"
            return []
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            result = subprocess.run(
                [sys.executable, str(self._probe)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            self.error = "Windows bildirim okuyucu zaman asimina ugradi"
            return []
        except Exception as exc:
            self.error = str(exc)
            return []

        if result.returncode != 0:
            # A native WinRT crash stays inside the helper process.
            self.error = f"Windows notification helper exit={result.returncode}"
            return []

        raw = (result.stdout or "").strip()
        if not raw:
            self.error = (result.stderr or "").strip()[:240]
            return []
        try:
            payload = json.loads(raw)
        except Exception:
            self.error = f"Bildirim JSON okunamadi: {raw[:160]}"
            return []

        if not isinstance(payload, dict):
            self.error = "Bildirim helper gecersiz veri dondurdu"
            return []
        if not payload.get("ok"):
            self.error = _safe_text(payload.get("error"), 240)
            return []
        rows = payload.get("rows")
        self.error = ""
        return list(rows) if isinstance(rows, list) else []

    def start(self) -> None:
        if os.name != "nt" or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()

        def run():
            while not self._stop.is_set():
                rows = self._read_isolated()
                if callable(self.on_update):
                    try:
                        self.on_update(rows)
                    except Exception:
                        pass
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=run, name="rip-win-notifications", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Smart error window hunter
# ---------------------------------------------------------------------------

class ErrorHunter:
    MARKERS = (
        "error", "hata", "exception", "failed", "failure", "crash", "fatal",
        "warning", "uyarı", "uyari", "not responding", "yanıt vermiyor", "yanit vermiyor",
    )

    def __init__(self, on_error: Callable[[dict[str, Any]], None], interval: float = 1.8):
        self.on_error = on_error
        self.interval = max(1.0, float(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen: dict[str, float] = {}

    def _scan_win(self) -> list[dict[str, Any]]:
        try:
            import win32gui
            import win32process
        except Exception:
            return []
        own_pid = os.getpid()
        found: list[dict[str, Any]] = []

        def enum(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = str(win32gui.GetWindowText(hwnd) or "").strip()
                if not title:
                    return
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == own_pid:
                    return
                process_name = ""
                try:
                    process_name = psutil.Process(pid).name()
                except Exception:
                    process_name = ""
                cls = str(win32gui.GetClassName(hwnd) or "")
                snippets: list[str] = []

                def child(ch, __):
                    try:
                        txt = str(win32gui.GetWindowText(ch) or "").strip()
                        if txt and len(" ".join(snippets)) < 1200:
                            snippets.append(txt)
                    except Exception:
                        pass

                if cls == "#32770" or any(m in title.casefold() for m in self.MARKERS):
                    try:
                        win32gui.EnumChildWindows(hwnd, child, None)
                    except Exception:
                        pass
                joined = " ".join([title] + snippets)
                low = joined.casefold()
                if (cls == "#32770" and any(m in low for m in self.MARKERS)) or any(m in title.casefold() for m in self.MARKERS):
                    found.append({
                        "hwnd": int(hwnd),
                        "pid": int(pid),
                        "process": _safe_text(process_name, 120),
                        "title": title,
                        "class": cls,
                        "text": _safe_text(" ".join(snippets), 900),
                        "detected_at": int(time.time() * 1000),
                    })
            except Exception:
                pass

        try:
            win32gui.EnumWindows(enum, None)
        except Exception:
            pass
        return found

    def start(self) -> None:
        if os.name != "nt" or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()

        def run():
            while not self._stop.is_set():
                now = time.time()
                self._seen = {k: v for k, v in self._seen.items() if now - v < 180}
                for item in self._scan_win():
                    key = f"{item.get('hwnd')}|{item.get('title')}|{item.get('text')}"
                    if key in self._seen:
                        continue
                    self._seen[key] = now
                    try:
                        self.on_error(item)
                    except Exception:
                        pass
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=run, name="rip-error-hunter", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
