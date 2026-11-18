from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app_paths import data_dir, data_path
from app_config import load_app_config, save_app_config
from bridge_shared import PORT, tailscale_cli
from actions.open_app import open_app
from actions.system_volume import control_volume
from actions.phone_bridge import get_phone_status_record
from actions.phone_control import queue_device_action, wait_for_ack

AUDIT_FILE = data_path("v6", "audit.jsonl")
RULES_FILE = data_path("v6", "automations.json")
PROFILE_FILE = data_path("v6", "profiles.json")
BACKUP_DIR = data_path("backups")

_LOCK = threading.RLock()


def audit(action: str, detail: str = "", status: str = "ok", extra: dict[str, Any] | None = None) -> None:
    row = {
        "ts": int(time.time() * 1000),
        "action": str(action or "")[:120],
        "detail": str(detail or "")[:1000],
        "status": str(status or "ok")[:32],
        "extra": dict(extra or {}),
    }
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with AUDIT_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_audit(limit: int = 200) -> list[dict[str, Any]]:
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()[-max(1, limit):]
    except Exception:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                out.append(row)
        except Exception:
            pass
    return out


def _bridge_health(timeout: float = 0.8) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=timeout) as r:
            obj = json.loads(r.read().decode("utf-8", errors="replace"))
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _port_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.35):
            return True
    except Exception:
        return False


def _start_bridge_hidden() -> tuple[bool, str]:
    if _port_listening(PORT):
        return True, "Bridge zaten açık"
    main_py = Path(__file__).resolve().parent / "main.py"
    if not main_py.is_file():
        return False, "main.py bulunamadı"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        subprocess.Popen(
            [sys.executable, str(main_py), "--persistent-bridge"],
            cwd=str(main_py.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        for _ in range(30):
            if _port_listening(PORT):
                return True, "Bridge yeniden başlatıldı"
            time.sleep(0.1)
        return False, "Bridge başlatıldı ama 8765 dinlemeye geçmedi"
    except Exception as exc:
        return False, str(exc)


def doctor_report(repair: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    def add(name: str, ok: bool, detail: str):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    try:
        root = data_dir()
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".v6_doctor"
        probe.write_text("ok", encoding="utf-8"); probe.unlink(missing_ok=True)
        add("Veri klasörü", True, str(root))
    except Exception as exc:
        add("Veri klasörü", False, str(exc))

    health = _bridge_health()
    if health.get("ok"):
        add("Telefon Bridge 8765", True, f"V{health.get('version','?')} · protocol {health.get('protocol_version','?')}")
    elif repair:
        ok, detail = _start_bridge_hidden()
        add("Telefon Bridge 8765", ok, detail)
        health = _bridge_health(1.2) if ok else {}
    else:
        add("Telefon Bridge 8765", False, "Kapalı / health yanıtı yok")

    ts = tailscale_cli()
    if ts:
        try:
            cp = subprocess.run([str(ts), "status"], capture_output=True, text=True, encoding="utf-8", errors="ignore",
                                timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            first = next((x.strip() for x in cp.stdout.splitlines() if x.strip()), "status boş")
            add("Tailscale", cp.returncode == 0, first[:220])
        except Exception as exc:
            add("Tailscale", False, str(exc))
    else:
        add("Tailscale", False, "tailscale.exe bulunamadı")

    status = get_phone_status_record()
    ts_ms = int(status.get("received_at", 0) or 0)
    age = int(max(0, time.time() - ts_ms / 1000)) if ts_ms else 999999
    add("Telefon heartbeat", age <= 30, f"{age} sn önce" if ts_ms else "Henüz veri yok")
    add("Telefon Accessibility", bool(status.get("accessibility_access", False)), "açık" if status.get("accessibility_access") else "kapalı")
    add("Telefon bildirim erişimi", bool(status.get("notification_access", False)), "açık" if status.get("notification_access") else "kapalı")

    try:
        import sounddevice as sd
        devices = sd.query_devices()
        inputs = [d for d in devices if float(d.get("max_input_channels", 0) or 0) > 0]
        add("Mikrofon", bool(inputs), f"{len(inputs)} giriş aygıtı")
    except Exception as exc:
        add("Mikrofon", False, str(exc))

    cfg = load_app_config()
    add("Gemini API", bool(str(cfg.get("gemini_api_key") or "").strip()), "hazır" if cfg.get("gemini_api_key") else "API key eksik")
    score = round(sum(1 for c in checks if c["ok"]) * 100 / max(1, len(checks)))
    result = {"score": score, "checks": checks, "repaired": bool(repair)}
    audit("doctor", f"score={score}", "ok" if score >= 70 else "warn", result)
    return result


def network_report() -> str:
    lines = []
    health = _bridge_health()
    lines.append(f"BRIDGE: {'OK' if health.get('ok') else 'DOWN'} · 127.0.0.1:{PORT}")
    status = get_phone_status_record()
    lines.append(f"PHONE: {status.get('bridge_service','?')} · {status.get('bridge_latency_ms','?')} ms · remote {status.get('remote_ip','?')}")
    ts = tailscale_cli()
    if ts:
        try:
            cp = subprocess.run([str(ts), "status"], capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=5,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            rows = [x for x in cp.stdout.splitlines() if x.strip()]
            lines.append("TAILSCALE:\n" + "\n".join(rows[:14]))
        except Exception as exc:
            lines.append("TAILSCALE ERROR: " + str(exc))
    else:
        lines.append("TAILSCALE: kurulu değil")
    return "\n".join(lines)


def _phone_action(action: str, **payload) -> str:
    row = queue_device_action(action, **payload)
    ack = wait_for_ack(str(row.get("id") or ""), timeout=6.0, interval=0.08)
    if not ack:
        return "Telefon ACK zaman aşımı"
    return str(ack.get("detail") or ack.get("status") or "tamam")


DEFAULT_PROFILES = {
    "OYUN": {"pc_volume": 70, "apps": ["steam", "discord"], "phone_dnd": True},
    "DERS": {"pc_volume": 35, "apps": [], "phone_dnd": True},
    "MEDYA": {"pc_volume": 65, "apps": ["spotify"], "phone_dnd": False},
    "YAZILIM": {"pc_volume": 45, "apps": ["visual studio code"], "phone_dnd": True},
}


def load_profiles() -> dict[str, dict[str, Any]]:
    profiles = dict(DEFAULT_PROFILES)
    try:
        raw = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(v, dict): profiles[str(k).upper()] = v
    except Exception:
        pass
    return profiles


def apply_profile(name: str) -> str:
    key = str(name or "").strip().upper()
    p = load_profiles().get(key)
    if not p:
        return f"Profil bulunamadı: {name}"
    notes = []
    if "pc_volume" in p:
        notes.append(control_volume("set", p["pc_volume"]))
    for app in p.get("apps", []) or []:
        try: notes.append(open_app(str(app)))
        except Exception as exc: notes.append(f"{app}: {exc}")
    if "phone_dnd" in p:
        try: notes.append(_phone_action("dnd_set", enabled=bool(p["phone_dnd"])))
        except Exception as exc: notes.append("Telefon DND: " + str(exc))
    audit("profile", key, "ok", {"notes": notes})
    return key + " profili uygulandı · " + " | ".join(notes)


def safe_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = BACKUP_DIR / f"RIP-V6-safe-backup-{stamp}.zip"
    root = data_dir()
    excludes = {"api_keys.json", "config.json", "bridge.log", "rip_runtime.log"}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in root.rglob("*"):
            if not p.is_file(): continue
            if p.name in excludes or p.suffix.lower() in {".log", ".key", ".pem"}: continue
            try: z.write(p, p.relative_to(root))
            except Exception: pass
        cfg = load_app_config().copy()
        cfg["gemini_api_key"] = ""
        cfg["youtube_api_key"] = ""
        cfg["obs_password"] = ""
        z.writestr("config/preferences-safe.json", json.dumps(cfg, ensure_ascii=False, indent=2))
    audit("backup", str(out))
    return out


def add_automation(rule: dict[str, Any]) -> None:
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    try: rows = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    except Exception: rows = []
    if not isinstance(rows, list): rows = []
    row = dict(rule)
    row.setdefault("id", f"r{int(time.time()*1000)}")
    row.setdefault("enabled", True)
    row.setdefault("cooldown_sec", 300)
    row["last_fire"] = 0
    rows.append(row)
    RULES_FILE.write_text(json.dumps(rows[-100:], ensure_ascii=False, indent=2), encoding="utf-8")
    audit("automation_add", json.dumps(row, ensure_ascii=False))


def list_automations() -> list[dict[str, Any]]:
    try:
        rows = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def clear_automations() -> None:
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    RULES_FILE.write_text("[]", encoding="utf-8")
    audit("automation_clear")


def _rule_matches(rule: dict[str, Any]) -> bool:
    trigger = str(rule.get("trigger") or "").casefold()
    status = get_phone_status_record()
    if trigger == "phone_battery_below":
        return int(status.get("battery_percent", 101) or 101) <= int(rule.get("value", 20))
    if trigger == "phone_charging":
        return bool(status.get("charging", False)) == bool(rule.get("value", True))
    if trigger == "phone_connected":
        ts_ms = int(status.get("received_at", 0) or 0)
        return bool(ts_ms and time.time() - ts_ms/1000 < 20)
    if trigger == "time":
        return time.strftime("%H:%M") == str(rule.get("value") or "")
    if trigger == "process_running":
        needle = str(rule.get("value") or "").casefold()
        if not needle: return False
        try:
            import psutil
            return any(needle in (p.info.get("name") or "").casefold() for p in psutil.process_iter(["name"]))
        except Exception: return False
    return False


def _run_rule_action(rule: dict[str, Any]) -> str:
    action = str(rule.get("action") or "").casefold()
    value = rule.get("action_value")
    if action == "profile": return apply_profile(str(value or ""))
    if action == "open_app": return open_app(str(value or ""))
    if action == "pc_volume": return control_volume("set", int(value or 50))
    if action == "phone_dnd_on": return _phone_action("dnd_set", enabled=True)
    if action == "phone_dnd_off": return _phone_action("dnd_set", enabled=False)
    if action == "phone_vibrate": return _phone_action("vibrate", duration_ms=int(value or 450))
    return "Bilinmeyen otomasyon eylemi"


class AutomationEngine:
    def __init__(self, interval: float = 2.0):
        self.interval = max(1.0, float(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive(): return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="rip-v6-automations")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self.interval):
            rows = list_automations()
            changed = False
            now = time.time()
            for rule in rows:
                if not isinstance(rule, dict) or not rule.get("enabled", True): continue
                cooldown = max(15, int(rule.get("cooldown_sec", 300) or 300))
                last = float(rule.get("last_fire", 0) or 0)
                if now - last < cooldown: continue
                try:
                    if not _rule_matches(rule): continue
                    detail = _run_rule_action(rule)
                    rule["last_fire"] = now
                    changed = True
                    audit("automation_fire", str(rule.get("id")), "ok", {"detail": detail})
                except Exception as exc:
                    rule["last_fire"] = now
                    changed = True
                    audit("automation_fire", str(exc), "error")
            if changed:
                try: RULES_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception: pass


AUTOMATION_ENGINE = AutomationEngine()

# ---------------------------------------------------------------------------
# V6 Macro Studio + one-shot folder sync
# ---------------------------------------------------------------------------
MACROS_FILE = data_path("v6", "macros.json")
SYNC_STATE_FILE = data_path("v6", "sync_state.json")


def load_macros() -> dict[str, list[dict[str, Any]]]:
    try:
        raw = json.loads(MACROS_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        out: dict[str, list[dict[str, Any]]] = {}
        for name, steps in raw.items():
            if isinstance(steps, list):
                out[str(name).strip().upper()] = [dict(x) for x in steps if isinstance(x, dict)]
        return out
    except Exception:
        return {}


def save_macro(name: str, steps: list[dict[str, Any]]) -> None:
    key = str(name or "").strip().upper()
    if not key:
        raise ValueError("Makro adı boş")
    if not steps:
        raise ValueError("Makro adımı yok")
    rows = load_macros()
    rows[key] = [dict(x) for x in steps[:80]]
    MACROS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MACROS_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    audit("macro_save", key, extra={"steps": len(steps)})


def delete_macro(name: str) -> bool:
    key = str(name or "").strip().upper()
    rows = load_macros()
    existed = key in rows
    rows.pop(key, None)
    MACROS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MACROS_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if existed: audit("macro_delete", key)
    return existed


def parse_macro_script(script: str) -> list[dict[str, Any]]:
    """Parse a deliberately small high-level macro language.

    OPEN steam
    PROFILE OYUN
    VOLUME 55
    WAIT 1.5
    PHONE vibrate 700
    PHONE home
    PHONE find_phone
    """
    steps: list[dict[str, Any]] = []
    for raw in str(script or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        op = parts[0].upper()
        rest = line[len(parts[0]):].strip()
        if op == "OPEN" and rest:
            steps.append({"type": "open_app", "value": rest})
        elif op == "PROFILE" and rest:
            steps.append({"type": "profile", "value": rest.upper()})
        elif op == "VOLUME" and rest:
            steps.append({"type": "pc_volume", "value": max(0, min(100, int(float(rest))))})
        elif op == "WAIT" and rest:
            steps.append({"type": "wait", "value": max(0.05, min(30.0, float(rest)))})
        elif op == "PHONE" and len(parts) >= 2:
            action = parts[1].strip().casefold()
            allowed = {
                "home", "back", "recents", "notifications", "quick_settings", "lock_screen",
                "vibrate", "find_phone", "media_play_pause", "media_next", "media_previous",
                "dnd_toggle", "flashlight_toggle",
            }
            if action not in allowed:
                raise ValueError(f"Desteklenmeyen PHONE makro işlemi: {action}")
            value = None
            if len(parts) >= 3:
                try: value = int(float(parts[2]))
                except Exception: value = parts[2]
            steps.append({"type": "phone", "action": action, "value": value})
        else:
            raise ValueError(f"Bilinmeyen makro satırı: {line}")
    return steps


def run_macro(name: str) -> str:
    key = str(name or "").strip().upper()
    steps = load_macros().get(key)
    if not steps:
        return f"Makro bulunamadı: {name}"
    notes: list[str] = []
    for index, step in enumerate(steps, start=1):
        kind = str(step.get("type") or "")
        try:
            if kind == "open_app":
                notes.append(str(open_app(str(step.get("value") or ""))))
            elif kind == "profile":
                notes.append(apply_profile(str(step.get("value") or "")))
            elif kind == "pc_volume":
                notes.append(str(control_volume("set", int(step.get("value", 50)))))
            elif kind == "wait":
                time.sleep(max(0.05, min(30.0, float(step.get("value", 1)))))
            elif kind == "phone":
                action = str(step.get("action") or "")
                payload: dict[str, Any] = {}
                if action in {"vibrate", "find_phone"} and step.get("value") is not None:
                    payload["duration_ms"] = int(step.get("value"))
                notes.append(_phone_action(action, **payload))
            else:
                notes.append(f"Adım {index}: bilinmeyen {kind}")
        except Exception as exc:
            notes.append(f"Adım {index} hata: {exc}")
            audit("macro_run", key, "error", {"step": index, "error": str(exc)})
            return f"{key} makrosu {index}. adımda durdu · " + " | ".join(notes[-6:])
    audit("macro_run", key, "ok", {"steps": len(steps)})
    return f"{key} makrosu tamamlandı · " + " | ".join(notes[-6:])


def sync_folder_to_phone(folder: str | Path, *, max_files: int = 40, max_total_mb: int = 250) -> dict[str, Any]:
    """One-shot incremental PC -> phone sync through the authenticated bridge.

    This is intentionally manual, not a permanent background mirroring service.
    Only changed files (mtime/size) are staged. The Android side still verifies SHA-256.
    """
    from actions.phone_commands import stage_file_for_phone

    src = Path(folder).expanduser().resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"Klasör bulunamadı: {src}")
    try:
        state = json.loads(SYNC_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    if not isinstance(state, dict): state = {}
    bucket = state.setdefault(str(src), {})
    candidates: list[Path] = []
    total = 0
    for p in sorted(src.rglob("*")):
        if not p.is_file(): continue
        try:
            size = p.stat().st_size
            rel = str(p.relative_to(src)).replace("\\", "/")
            sig = f"{size}:{p.stat().st_mtime_ns}"
        except Exception:
            continue
        if bucket.get(rel) == sig: continue
        if len(candidates) >= max_files: break
        if total + size > max_total_mb * 1024 * 1024: break
        candidates.append(p); total += size
    sent = []
    errors = []
    for p in candidates:
        try:
            row = stage_file_for_phone(p)
            cid = str(row.get("command_id") or "")
            ack = wait_for_ack(cid, timeout=90.0, interval=0.12)
            if str(ack.get("status") or "").casefold() not in {"ok", "done", "success"}:
                raise RuntimeError(str(ack.get("detail") or "Telefon ACK alınamadı"))
            rel = str(p.relative_to(src)).replace("\\", "/")
            bucket[rel] = f"{p.stat().st_size}:{p.stat().st_mtime_ns}"
            sent.append(rel)
        except Exception as exc:
            errors.append(f"{p.name}: {exc}")
    SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"folder": str(src), "sent": sent, "errors": errors, "count": len(sent), "bytes": total}
    audit("folder_sync", str(src), "ok" if not errors else "warn", result)
    return result

# ---------------------------------------------------------------------------
# Opt-in PC -> phone automatic clipboard sync
# ---------------------------------------------------------------------------
CLIPBOARD_CFG_FILE = data_path("v6", "clipboard_sync.json")


def clipboard_auto_enabled() -> bool:
    try:
        row = json.loads(CLIPBOARD_CFG_FILE.read_text(encoding="utf-8"))
        return bool(row.get("pc_to_phone", False)) if isinstance(row, dict) else False
    except Exception:
        return False


def set_clipboard_auto(enabled: bool) -> None:
    CLIPBOARD_CFG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLIPBOARD_CFG_FILE.write_text(json.dumps({"pc_to_phone": bool(enabled)}, ensure_ascii=False, indent=2), encoding="utf-8")
    if enabled: CLIPBOARD_ENGINE.start()
    audit("clipboard_auto", "on" if enabled else "off")


def _read_windows_clipboard_text() -> str:
    if os.name != "nt": return ""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT): return ""
            return str(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT) or "")
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""


class ClipboardSyncEngine:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last = ""

    def start(self):
        if self._thread and self._thread.is_alive(): return
        self._stop.clear()
        self._last = _read_windows_clipboard_text()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="rip-v6-clipboard")
        self._thread.start()

    def stop(self): self._stop.set()

    def _loop(self):
        from actions.phone_commands import queue_clipboard_to_phone
        while not self._stop.wait(0.8):
            if not clipboard_auto_enabled(): continue
            text = _read_windows_clipboard_text()
            if not text or text == self._last: continue
            self._last = text
            try:
                queue_clipboard_to_phone(text[:12000])
                audit("clipboard_auto_send", f"{len(text)} karakter")
            except Exception as exc:
                audit("clipboard_auto_send", str(exc), "error")


CLIPBOARD_ENGINE = ClipboardSyncEngine()
