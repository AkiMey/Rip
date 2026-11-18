from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import psutil

from actions.open_app import open_app
from actions.platform_utils import IS_WIN, open_path, quiet_popen_kwargs


BASE_DIR = Path(__file__).resolve().parent.parent

# These processes/services are intentionally not stoppable through RIP. They can
# destabilize Windows or disable core security/session infrastructure.
CRITICAL_PROCESSES = {
    "system",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "dwm.exe",
    "fontdrvhost.exe",
    "memory compression",
}

CRITICAL_SERVICES = {
    "rpcss",
    "dcomlaunch",
    "samss",
    "eventlog",
    "plugplay",
    "power",
    "profsvc",
    "winmgmt",
    "cryptsvc",
    "mpssvc",
    "windefend",
    "wscsvc",
}

# Hard blocks apply even after confirmation. They preserve disk/boot/security,
# account integrity and the existing explicit shutdown/restart confirmation flow.
HARD_BLOCK_PATTERNS = [
    r"\bformat(?:\.com)?\b",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\breagentc\b",
    r"\bvssadmin\s+delete\b",
    r"\bwbadmin\s+delete\b",
    r"\bcipher\s+/w\b",
    r"\bclear-disk\b",
    r"\bremove-partition\b",
    r"\bdelete\s+partition\b",
    r"\bclean\s+all\b",
    r"\bshutdown(?:\.exe)?\b",
    r"\bstop-computer\b",
    r"\brestart-computer\b",
    r"\bset-executionpolicy\b",
    r"\bset-mppreference\b.*\bdisable",
    r"\badd-mppreference\b.*\bexclusion",
    r"\bnetsh\s+advfirewall\b.*\bstate\s+off\b",
    r"\bset-netfirewallprofile\b.*\benabled\s+false\b",
    r"\breg(?:\.exe)?\s+(?:add|delete)\b",
    r"\bnew-localuser\b",
    r"\bset-localuser\b",
    r"\bnet\s+user\b",
    r"\badd-localgroupmember\b",
    r"\bnet\s+localgroup\s+administrators\b",
    r"\bschtasks(?:\.exe)?\s+/create\b",
    r"\bregister-scheduledtask\b",
    r"\bnew-scheduledtask\b",
    r"\bwevtutil\s+cl\b",
    r"\bclear-eventlog\b",
    r"\b-encodedcommand\b",
]

# Mutating shell actions are allowed only after an explicit confirmation. For
# common file/process/service actions, dedicated narrow tools are preferred.
CONFIRM_SHELL_PATTERNS = [
    r"^\s*(?:del|erase|rd|rmdir|move|ren|rename|copy|xcopy|robocopy)\b",
    r"\bremove-item\b",
    r"\bmove-item\b",
    r"\brename-item\b",
    r"\bset-content\b",
    r"\bclear-content\b",
    r"\bstop-process\b",
    r"\btaskkill\b",
    r"\bsc(?:\.exe)?\s+(?:start|stop|config)\b",
    r"\bnet\s+(?:start|stop)\b",
    r"\bdisable-netadapter\b",
    r"\benable-netadapter\b",
    r"\bipconfig\s+/(?:release|renew)\b",
    r"\bicacls\b",
    r"\btakeown\b",
]

ADMIN_APP_ALIASES = {
    "cmd": ("cmd.exe", ""),
    "command prompt": ("cmd.exe", ""),
    "komut istemi": ("cmd.exe", ""),
    "powershell": ("powershell.exe", ""),
    "windows powershell": ("powershell.exe", ""),
    "task manager": ("taskmgr.exe", ""),
    "görev yöneticisi": ("taskmgr.exe", ""),
    "gorev yoneticisi": ("taskmgr.exe", ""),
    "registry editor": ("regedit.exe", ""),
    "regedit": ("regedit.exe", ""),
    "device manager": ("mmc.exe", "devmgmt.msc"),
    "aygıt yöneticisi": ("mmc.exe", "devmgmt.msc"),
    "aygit yoneticisi": ("mmc.exe", "devmgmt.msc"),
    "services": ("mmc.exe", "services.msc"),
    "hizmetler": ("mmc.exe", "services.msc"),
}

SETTINGS_URIS = {
    "home": "ms-settings:",
    "system": "ms-settings:system",
    "display": "ms-settings:display",
    "sound": "ms-settings:sound",
    "notifications": "ms-settings:notifications",
    "power": "ms-settings:powersleep",
    "storage": "ms-settings:storagesense",
    "apps": "ms-settings:appsfeatures",
    "startup_apps": "ms-settings:startupapps",
    "network": "ms-settings:network-status",
    "wifi": "ms-settings:network-wifi",
    "ethernet": "ms-settings:network-ethernet",
    "vpn": "ms-settings:network-vpn",
    "bluetooth": "ms-settings:bluetooth",
    "windows_update": "ms-settings:windowsupdate",
    "privacy": "ms-settings:privacy",
    "accounts": "ms-settings:accounts",
    "personalization": "ms-settings:personalization",
}


def _windows_only() -> str | None:
    if not IS_WIN:
        return "Bu PC kontrol aracı yalnızca Windows'ta kullanılabilir."
    return None


def _normalize(value: str) -> str:
    table = str.maketrans({
        "ı": "i", "İ": "i", "I": "i",
        "ş": "s", "Ş": "s",
        "ğ": "g", "Ğ": "g",
        "ü": "u", "Ü": "u",
        "ö": "o", "Ö": "o",
        "ç": "c", "Ç": "c",
    })
    return re.sub(r"\s+", " ", str(value or "").casefold().translate(table)).strip()


def _truncate(text: str, limit: int = 3200) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n... (çıktı kısaltıldı)"


def _confirmation(reason: str) -> str:
    return (
        "ONAY GEREKLI: " + reason.strip() + " "
        "Kullanıcı bu işlemi açıkça onaylarsa aynı aracı confirmed=true ile tekrar çağır."
    )


def _hard_block_reason(command: str) -> str | None:
    low = str(command or "").casefold()
    for pattern in HARD_BLOCK_PATTERNS:
        if re.search(pattern, low, flags=re.IGNORECASE):
            return "Bu komut disk/önyükleme/güvenlik/hesap veya güç korumasını aşabildiği için engellendi."
    return None


def _shell_needs_confirmation(command: str) -> bool:
    low = str(command or "").casefold()
    return any(re.search(pattern, low, flags=re.IGNORECASE) for pattern in CONFIRM_SHELL_PATTERNS)


def _protected_roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        raw = os.environ.get(key)
        if raw:
            roots.append(Path(raw))
    return roots


def _path_is_protected(path: str | Path) -> bool:
    try:
        p = Path(path).expanduser().resolve(strict=False)
    except Exception:
        return False

    p_norm = os.path.normcase(str(p))
    for root in _protected_roots():
        try:
            root_norm = os.path.normcase(str(root.resolve(strict=False)))
        except Exception:
            continue
        if p_norm == root_norm or p_norm.startswith(root_norm.rstrip("\\/") + os.sep):
            return True

    # Creating/moving directly at C:\ (or another drive root) is elevated territory.
    try:
        if p.anchor and p.parent == Path(p.anchor):
            return True
    except Exception:
        pass
    return False


def _run_process(args: list[str], timeout: int = 30, cwd: str | None = None) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=max(1, min(int(timeout or 30), 120)),
            cwd=cwd,
            encoding="utf-8",
            errors="replace",
            **quiet_popen_kwargs(),
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        return result.returncode, _truncate(output or "Komut tamamlandı (çıktı yok).")
    except subprocess.TimeoutExpired:
        return 124, f"Komut zaman aşımına uğradı ({timeout}s)."
    except Exception as exc:
        return 1, f"Hata: {exc}"


def _run_shell_local(shell_name: str, command: str, timeout: int = 30) -> str:
    shell_key = _normalize(shell_name)
    if shell_key in {"cmd", "command prompt", "komut istemi"}:
        code, output = _run_process(
            ["cmd.exe", "/d", "/s", "/c", command],
            timeout=timeout,
        )
        return output if code == 0 else f"CMD hata kodu {code}: {output}"

    code, output = _run_process(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command",
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command,
        ],
        timeout=timeout,
    )
    return output if code == 0 else f"PowerShell hata kodu {code}: {output}"


def _resolve_admin_app(app_name: str, arguments: str = "") -> tuple[str, str]:
    raw = str(app_name or "").strip()
    key = _normalize(raw)
    if key in ADMIN_APP_ALIASES:
        target, extra = ADMIN_APP_ALIASES[key]
        merged = " ".join(part for part in (extra, arguments) if part).strip()
        return target, merged

    expanded = os.path.expandvars(os.path.expanduser(raw))
    if Path(expanded).exists():
        return expanded, arguments

    found = shutil.which(raw)
    if found:
        return found, arguments

    return raw, arguments


def _job_paths() -> tuple[Path, Path]:
    folder = Path(tempfile.gettempdir()) / "ripperinio_admin_jobs"
    folder.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    return folder / f"{job_id}.job.json", folder / f"{job_id}.result.json"


def _run_elevated_job(job: dict[str, Any], timeout: int = 90) -> str:
    if not IS_WIN:
        return "Yönetici yardımcısı yalnızca Windows'ta kullanılabilir."

    job_path, result_path = _job_paths()
    payload = dict(job)
    payload["result_path"] = str(result_path)
    job_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        from win32com.shell import shell, shellcon
        import win32event

        if bool(getattr(sys, "frozen", False)):
            executable = sys.executable
            parameters = subprocess.list2cmdline(["--rip-admin-helper", str(job_path)])
            workdir = str(Path(sys.executable).resolve().parent)
        else:
            executable = sys.executable
            parameters = subprocess.list2cmdline([
                "-m", "actions.pc_control", "--helper", str(job_path)
            ])
            workdir = str(BASE_DIR)

        info = shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
            lpVerb="runas",
            lpFile=executable,
            lpParameters=parameters,
            lpDirectory=workdir,
            nShow=0,
        )
        handle = info.get("hProcess")
        if handle:
            wait = win32event.WaitForSingleObject(handle, max(1, int(timeout)) * 1000)
            if wait == win32event.WAIT_TIMEOUT:
                return "Yönetici işlemi zaman aşımına uğradı."

        for _ in range(20):
            if result_path.exists():
                break
            time.sleep(0.10)

        if not result_path.exists():
            return "Yönetici yardımcısı tamamlandı fakat sonuç dosyası alınamadı."

        data = json.loads(result_path.read_text(encoding="utf-8"))
        return str(data.get("message", "Yönetici işlemi tamamlandı."))
    except Exception as exc:
        text = str(exc)
        if "1223" in text or "canceled" in text.casefold() or "cancelled" in text.casefold():
            return "Yönetici izni kullanıcı tarafından iptal edildi."
        return f"Yönetici yardımcısı başlatılamadı: {exc}"
    finally:
        for path in (job_path, result_path):
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass


def _find_processes(query: str = "", pid: int | None = None) -> list[psutil.Process]:
    if pid:
        try:
            return [psutil.Process(int(pid))]
        except Exception:
            return []

    needle = _normalize(query)
    if not needle:
        return []

    matches: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            name = str(proc.info.get("name") or "")
            exe = str(proc.info.get("exe") or "")
            cmd = " ".join(proc.info.get("cmdline") or [])
            hay = _normalize(" ".join((name, Path(exe).stem if exe else "", cmd)))
            if needle in hay:
                matches.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches


def _critical_process(proc: psutil.Process) -> bool:
    try:
        name = _normalize(proc.name())
    except Exception:
        return True
    return name in {_normalize(x) for x in CRITICAL_PROCESSES}


def _find_service(query: str) -> dict[str, str] | None:
    needle = _normalize(query)
    if not needle:
        return None

    exact = None
    partial = None
    try:
        for svc in psutil.win_service_iter():
            info = svc.as_dict()
            name = str(info.get("name") or "")
            display = str(info.get("display_name") or "")
            if needle in {_normalize(name), _normalize(display)}:
                exact = info
                break
            if needle in _normalize(name) or needle in _normalize(display):
                partial = partial or info
    except Exception:
        return None
    return exact or partial


def _file_local(
    action: str,
    source: str = "",
    target: str = "",
    content: str = "",
    overwrite: bool = False,
) -> str:
    key = _normalize(action)
    src = Path(os.path.expandvars(os.path.expanduser(source))) if source else None
    dst = Path(os.path.expandvars(os.path.expanduser(target))) if target else None

    try:
        if key in {"create_file", "file_create", "dosya olustur"}:
            if dst is None:
                return "Oluşturulacak dosya yolu belirtilmedi."
            if dst.exists() and not overwrite:
                return f"Dosya zaten var: {dst}. Üzerine yazmak için overwrite=true gerekir."
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(str(content or ""), encoding="utf-8")
            return f"Dosya oluşturuldu: {dst}"

        if key in {"create_folder", "mkdir", "folder_create", "klasor olustur"}:
            if dst is None:
                return "Oluşturulacak klasör yolu belirtilmedi."
            dst.mkdir(parents=True, exist_ok=True)
            return f"Klasör hazır: {dst}"

        if key in {"move", "tasi", "taşı"}:
            if src is None or dst is None:
                return "Taşıma için source ve target gerekli."
            if not src.exists():
                return f"Kaynak bulunamadı: {src}"
            if dst.exists() and not overwrite:
                return f"Hedef zaten var: {dst}. overwrite=true olmadan değiştirilmedi."
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and overwrite and src.is_file() and dst.is_file():
                os.replace(str(src), str(dst))
            else:
                shutil.move(str(src), str(dst))
            return f"Taşındı: {src} -> {dst}"

        if key in {"rename", "yeniden adlandir", "yeniden adlandır"}:
            if src is None or dst is None:
                return "Yeniden adlandırma için source ve target gerekli."
            if not src.exists():
                return f"Kaynak bulunamadı: {src}"
            if dst.exists() and not overwrite:
                return f"Hedef zaten var: {dst}. overwrite=true olmadan değiştirilmedi."
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and overwrite and src.is_file() and dst.is_file():
                os.replace(str(src), str(dst))
            else:
                src.rename(dst)
            return f"Yeniden adlandırıldı: {src} -> {dst}"

        return f"Bilinmeyen dosya işlemi: {action}"
    except PermissionError:
        return "İzin reddedildi. Bu konum için admin=true ve açık kullanıcı onayı gerekebilir."
    except Exception as exc:
        return f"Dosya işlemi başarısız: {exc}"


def app_control(
    action: str,
    app_name: str,
    admin: bool = False,
    force: bool = False,
    arguments: str = "",
    confirmed: bool = False,
) -> str:
    err = _windows_only()
    if err:
        return err

    key = _normalize(action)
    app_name = str(app_name or "").strip()
    if not app_name:
        return "Uygulama adı belirtilmedi."

    if key in {"open", "start", "ac", "aç"}:
        if not admin:
            return open_app(app_name)
        if not confirmed:
            return _confirmation(f"'{app_name}' yönetici yetkisiyle açılacak ve Windows UAC soracak.")
        target, merged_args = _resolve_admin_app(app_name, arguments)
        return _run_elevated_job({
            "type": "app_admin",
            "target": target,
            "arguments": merged_args,
        })

    if key in {"close", "terminate", "kapat"}:
        matches = _find_processes(app_name)
        if not matches:
            return f"Çalışan uygulama bulunamadı: {app_name}"
        if any(_critical_process(p) for p in matches):
            return "Bu ad kritik bir Windows süreciyle eşleştiği için kapatma engellendi."
        if force and not confirmed:
            return _confirmation(f"'{app_name}' zorla kapatılacak; kaydedilmemiş veri kaybolabilir.")
        if admin:
            if not confirmed:
                return _confirmation(f"'{app_name}' yönetici yetkisiyle kapatılacak.")
            return _run_elevated_job({
                "type": "process",
                "pids": [p.pid for p in matches],
                "force": bool(force),
            })

        done = []
        denied = []
        for proc in matches:
            try:
                proc.kill() if force else proc.terminate()
                done.append(f"{proc.pid}:{proc.name()}")
            except psutil.AccessDenied:
                denied.append(str(proc.pid))
            except psutil.NoSuchProcess:
                pass
        if denied:
            return (
                f"Kapatılan: {', '.join(done) or 'yok'}. "
                f"İzin reddedilen PID: {', '.join(denied)}. Gerekirse admin=true kullan."
            )
        return f"Kapatma sinyali gönderildi: {', '.join(done) or app_name}"

    return f"Bilinmeyen uygulama işlemi: {action}"


def window_control(
    action: str,
    title: str = "",
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> str:
    err = _windows_only()
    if err:
        return err

    try:
        import win32con
        import win32gui
    except Exception as exc:
        return f"Pencere kontrolü için pywin32 gerekli: {exc}"

    windows: list[tuple[int, str]] = []

    def enum_cb(hwnd, _extra):
        try:
            if win32gui.IsWindowVisible(hwnd):
                name = str(win32gui.GetWindowText(hwnd) or "").strip()
                if name:
                    windows.append((hwnd, name))
        except Exception:
            pass

    win32gui.EnumWindows(enum_cb, None)
    key = _normalize(action)
    needle = _normalize(title)

    if key in {"list", "listele"}:
        filtered = [(h, t) for h, t in windows if not needle or needle in _normalize(t)]
        if not filtered:
            return "Eşleşen görünür pencere yok."
        return "\n".join(f"{hwnd} | {name}" for hwnd, name in filtered[:30])

    candidates = [(h, t) for h, t in windows if needle and needle in _normalize(t)]
    if not candidates:
        return f"Pencere bulunamadı: {title}"
    hwnd, matched = candidates[0]

    try:
        if key in {"focus", "activate", "one getir", "öne getir"}:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            try:
                win32gui.BringWindowToTop(hwnd)
            except Exception:
                pass
            win32gui.SetForegroundWindow(hwnd)
            return f"Pencere öne getirildi: {matched}"

        if key in {"minimize", "kucult", "küçült"}:
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return f"Pencere küçültüldü: {matched}"

        if key in {"maximize", "buyut", "büyüt"}:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
            return f"Pencere büyütüldü: {matched}"

        if key in {"restore", "geri getir", "normal"}:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            return f"Pencere normal boyuta getirildi: {matched}"

        if key in {"close", "kapat"}:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return f"Pencereye kapatma isteği gönderildi: {matched}"

        if key in {"move_resize", "move", "resize", "tasi", "taşı", "boyutlandir", "boyutlandır"}:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            new_x = left if x is None else int(x)
            new_y = top if y is None else int(y)
            new_w = max(120, right - left if width is None else int(width))
            new_h = max(80, bottom - top if height is None else int(height))
            win32gui.MoveWindow(hwnd, new_x, new_y, new_w, new_h, True)
            return f"Pencere taşındı/boyutlandırıldı: {matched} -> {new_x},{new_y} {new_w}x{new_h}"
    except Exception as exc:
        return f"Pencere işlemi başarısız: {exc}"

    return f"Bilinmeyen pencere işlemi: {action}"


def file_control(
    action: str,
    source: str = "",
    target: str = "",
    content: str = "",
    overwrite: bool = False,
    admin: bool = False,
    confirmed: bool = False,
) -> str:
    err = _windows_only()
    if err:
        return err

    key = _normalize(action)
    path_candidates = [p for p in (source, target) if p]
    protected = any(_path_is_protected(p) for p in path_candidates)
    risky_overwrite = bool(overwrite)

    if (protected or admin or risky_overwrite) and not confirmed:
        why = []
        if protected:
            why.append("korumalı Windows/Program Files konumu etkileniyor")
        if admin:
            why.append("yönetici yetkisi istendi")
        if risky_overwrite:
            why.append("mevcut hedefin üzerine yazılabilir")
        return _confirmation("Dosya işlemi: " + ", ".join(why) + ".")

    if protected or admin:
        return _run_elevated_job({
            "type": "file",
            "action": key,
            "source": source,
            "target": target,
            "content": content,
            "overwrite": bool(overwrite),
        })

    return _file_local(key, source, target, content, overwrite)


def process_control(
    action: str,
    query: str = "",
    pid: int | None = None,
    force: bool = False,
    admin: bool = False,
    confirmed: bool = False,
    limit: int = 20,
) -> str:
    err = _windows_only()
    if err:
        return err

    key = _normalize(action)

    if key in {"list", "listele"}:
        needle = _normalize(query)
        rows = []
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                name = str(proc.info.get("name") or "")
                if needle and needle not in _normalize(name):
                    continue
                rss = int(getattr(proc.info.get("memory_info"), "rss", 0) or 0)
                rows.append((rss, proc.pid, name))
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        rows.sort(reverse=True)
        if not rows:
            return "Eşleşen süreç bulunamadı."
        return "\n".join(
            f"PID {pid_} | {name} | {rss / 1024 / 1024:.0f} MB"
            for rss, pid_, name in rows[:max(1, min(int(limit or 20), 40))]
        )

    matches = _find_processes(query, pid)
    if not matches:
        return "Süreç bulunamadı."
    if any(_critical_process(p) for p in matches):
        return "Kritik Windows süreci tespit edildi; bu süreç sonlandırılamaz."

    if key not in {"terminate", "kill", "end", "sonlandir", "sonlandır"}:
        return f"Bilinmeyen süreç işlemi: {action}"

    force_mode = bool(force or key == "kill")
    if force_mode and not confirmed:
        return _confirmation("Süreç zorla sonlandırılacak; kaydedilmemiş veri kaybolabilir.")
    if admin and not confirmed:
        return _confirmation("Süreç yönetici yetkisiyle sonlandırılacak.")

    if admin:
        return _run_elevated_job({
            "type": "process",
            "pids": [p.pid for p in matches],
            "force": force_mode,
        })

    done, denied = [], []
    for proc in matches:
        try:
            proc.kill() if force_mode else proc.terminate()
            done.append(f"{proc.pid}:{proc.name()}")
        except psutil.AccessDenied:
            denied.append(str(proc.pid))
        except psutil.NoSuchProcess:
            pass
    if denied:
        return (
            f"Sonlandırma gönderilen: {', '.join(done) or 'yok'}. "
            f"İzin reddedilen PID: {', '.join(denied)}; admin=true gerekebilir."
        )
    return f"Süreç sonlandırma gönderildi: {', '.join(done)}"


def service_control(
    action: str,
    service_name: str = "",
    confirmed: bool = False,
    limit: int = 30,
) -> str:
    err = _windows_only()
    if err:
        return err

    key = _normalize(action)
    if key in {"list", "listele"}:
        needle = _normalize(service_name)
        rows = []
        try:
            for svc in psutil.win_service_iter():
                info = svc.as_dict()
                name = str(info.get("name") or "")
                display = str(info.get("display_name") or "")
                status = str(info.get("status") or "unknown")
                if needle and needle not in _normalize(name) and needle not in _normalize(display):
                    continue
                rows.append(f"{name} | {status} | {display}")
        except Exception as exc:
            return f"Hizmet listesi alınamadı: {exc}"
        return "\n".join(rows[:max(1, min(int(limit or 30), 60))]) or "Eşleşen hizmet yok."

    info = _find_service(service_name)
    if not info:
        return f"Windows hizmeti bulunamadı: {service_name}"

    name = str(info.get("name") or service_name)
    display = str(info.get("display_name") or name)
    status = str(info.get("status") or "unknown")

    if key in {"status", "durum"}:
        return f"{display} ({name}) durumu: {status}"

    if key not in {"start", "stop", "restart", "baslat", "başlat", "durdur", "yeniden baslat", "yeniden başlat"}:
        return f"Bilinmeyen hizmet işlemi: {action}"

    normalized_name = _normalize(name)
    if key in {"stop", "restart", "durdur", "yeniden baslat", "yeniden başlat"} and normalized_name in {_normalize(x) for x in CRITICAL_SERVICES}:
        return f"Kritik Windows hizmeti '{name}' durdurma/yeniden başlatmaya karşı korunuyor."

    if not confirmed:
        return _confirmation(f"Windows hizmeti '{display}' için {action} işlemi yönetici yetkisiyle uygulanacak.")

    canonical = "start"
    if key in {"stop", "durdur"}:
        canonical = "stop"
    elif key in {"restart", "yeniden baslat", "yeniden başlat"}:
        canonical = "restart"

    return _run_elevated_job({
        "type": "service",
        "action": canonical,
        "service_name": name,
    })


def windows_control(
    action: str,
    page: str = "",
    adapter: str = "",
    value: int | float | None = None,
    confirmed: bool = False,
) -> str:
    err = _windows_only()
    if err:
        return err

    key = _normalize(action)

    if key in {"open_settings", "settings", "ayarlar"}:
        page_key = _normalize(page or "home").replace(" ", "_")
        uri = SETTINGS_URIS.get(page_key, "ms-settings:")
        ok, message = open_path(uri)
        return f"Windows Ayarları açıldı: {page or 'ana sayfa'}" if ok else f"Ayarlar açılamadı: {message}"

    quick_pages = {
        "open_task_manager": "taskmgr.exe",
        "open_control_panel": "control.exe",
        "open_device_manager": "devmgmt.msc",
        "open_services": "services.msc",
        "open_network": SETTINGS_URIS["network"],
        "open_display": SETTINGS_URIS["display"],
        "open_sound": SETTINGS_URIS["sound"],
        "open_windows_update": SETTINGS_URIS["windows_update"],
        "open_apps": SETTINGS_URIS["apps"],
    }
    if key in quick_pages:
        ok, message = open_path(quick_pages[key])
        return "Windows bölümü açıldı." if ok else f"Açılamadı: {message}"

    if key in {"network_status", "adapters", "adapter_list"}:
        rows = []
        try:
            for name, info in psutil.net_if_stats().items():
                rows.append(
                    f"{name}: {'UP' if info.isup else 'DOWN'} | {info.speed or 0} Mbps | MTU {info.mtu}"
                )
        except Exception as exc:
            return f"Ağ bağdaştırıcıları okunamadı: {exc}"
        return "\n".join(rows) or "Ağ bağdaştırıcısı bulunamadı."

    if key in {"flush_dns", "dns_flush"}:
        code, output = _run_process(["ipconfig.exe", "/flushdns"], timeout=20)
        return output if code == 0 else f"DNS temizlenemedi: {output}"

    if key in {"renew_ip", "ip_renew"}:
        if not confirmed:
            return _confirmation("IP yenileme ağ bağlantısını kısa süreli kesebilir.")
        return _run_elevated_job({"type": "renew_ip"}, timeout=120)

    if key in {"enable_adapter", "disable_adapter"}:
        if not adapter:
            return "Ağ bağdaştırıcı adı gerekli. Önce action=network_status ile listeleyebilirsin."
        if not confirmed:
            return _confirmation(
                f"'{adapter}' ağ bağdaştırıcısı {'açılacak' if key == 'enable_adapter' else 'kapatılacak'}; bağlantı etkilenebilir."
            )
        return _run_elevated_job({
            "type": "network_adapter",
            "action": "enable" if key == "enable_adapter" else "disable",
            "adapter": adapter,
        })

    if key in {"get_brightness", "brightness_get"}:
        command = "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop | Select-Object -First 1 -ExpandProperty CurrentBrightness)"
        return _run_shell_local("powershell", command, timeout=15)

    if key in {"set_brightness", "brightness_set"}:
        if value is None:
            return "Parlaklık yüzdesi belirtilmedi."
        target = max(0, min(100, int(round(float(value)))))
        command = (
            "$m=Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop | Select-Object -First 1; "
            f"Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{{Timeout=1;Brightness={target}}} | Out-Null; "
            f"Write-Output 'Parlaklık %{target} yapıldı.'"
        )
        return _run_shell_local("powershell", command, timeout=15)

    return f"Bilinmeyen Windows ayarı işlemi: {action}"


def shell_control(
    shell: str,
    command: str,
    admin: bool = False,
    confirmed: bool = False,
    timeout: int = 30,
) -> str:
    err = _windows_only()
    if err:
        return err

    command = str(command or "").strip()
    if not command:
        return "Çalıştırılacak komut belirtilmedi."

    blocked = _hard_block_reason(command)
    if blocked:
        return "Güvenlik: " + blocked

    if _shell_needs_confirmation(command) and not confirmed:
        return _confirmation("Komut dosya/süreç/hizmet/ağ durumunu değiştirebilir.")

    if admin and not confirmed:
        return _confirmation("Komut yönetici yetkisiyle çalıştırılacak ve Windows UAC soracak.")

    if admin:
        return _run_elevated_job({
            "type": "shell",
            "shell": shell,
            "command": command,
            "timeout": max(1, min(int(timeout or 30), 120)),
        }, timeout=max(45, min(int(timeout or 30) + 30, 150)))

    return _run_shell_local(shell, command, timeout=max(1, min(int(timeout or 30), 120)))


# ---------------------------------------------------------------------------
# Elevated helper implementation
# ---------------------------------------------------------------------------
def _admin_job_process(job: dict[str, Any]) -> str:
    pids = [int(x) for x in (job.get("pids") or [])]
    force = bool(job.get("force", False))
    if not pids:
        return "PID belirtilmedi."

    done = []
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            if _critical_process(proc):
                continue
            args = ["taskkill.exe", "/PID", str(pid), "/T"]
            if force:
                args.append("/F")
            _run_process(args, timeout=20)
            done.append(str(pid))
        except Exception:
            continue
    return f"Yönetici süreç işlemi tamamlandı. PID: {', '.join(done) or 'yok'}"


def _admin_job_service(job: dict[str, Any]) -> str:
    name = str(job.get("service_name") or "").strip()
    action = _normalize(job.get("action") or "")
    if not name:
        return "Hizmet adı yok."
    if action in {"stop", "restart"} and _normalize(name) in {_normalize(x) for x in CRITICAL_SERVICES}:
        return "Kritik hizmet koruması: işlem engellendi."

    outputs = []
    if action == "restart":
        code, out = _run_process(["sc.exe", "stop", name], timeout=30)
        outputs.append(out)
        time.sleep(1.0)
        code2, out2 = _run_process(["sc.exe", "start", name], timeout=30)
        outputs.append(out2)
        return _truncate("\n".join(outputs))

    if action not in {"start", "stop"}:
        return "Bilinmeyen hizmet işlemi."
    code, out = _run_process(["sc.exe", action, name], timeout=30)
    return out if code == 0 else f"Hizmet işlemi hata kodu {code}: {out}"


def _admin_job_network(job: dict[str, Any]) -> str:
    action = _normalize(job.get("action") or "")
    adapter = str(job.get("adapter") or "").strip()
    if action not in {"enable", "disable"} or not adapter:
        return "Geçersiz ağ bağdaştırıcı işi."

    script = (
        "$n=$args[0]; "
        + ("Enable-NetAdapter -Name $n -Confirm:$false -ErrorAction Stop; " if action == "enable" else "Disable-NetAdapter -Name $n -Confirm:$false -ErrorAction Stop; ")
        + "Write-Output ('Adapter ' + $n + ' updated.')"
    )
    code, out = _run_process([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script, adapter
    ], timeout=45)
    return out if code == 0 else f"Ağ işlemi hata kodu {code}: {out}"


def _admin_job_app(job: dict[str, Any]) -> str:
    target = str(job.get("target") or "").strip()
    arguments = str(job.get("arguments") or "").strip()
    if not target:
        return "Yönetici olarak açılacak uygulama belirtilmedi."
    try:
        args = [target]
        if arguments:
            args.extend(shlex.split(arguments, posix=False))
        subprocess.Popen(args, cwd=str(Path(target).parent) if Path(target).exists() else None)
        return f"Yönetici uygulaması başlatıldı: {target}"
    except Exception as exc:
        return f"Yönetici uygulaması açılamadı: {exc}"


def _execute_admin_job(job: dict[str, Any]) -> str:
    job_type = _normalize(job.get("type") or "")

    if job_type == "file":
        return _file_local(
            str(job.get("action") or ""),
            str(job.get("source") or ""),
            str(job.get("target") or ""),
            str(job.get("content") or ""),
            bool(job.get("overwrite", False)),
        )

    if job_type == "process":
        return _admin_job_process(job)

    if job_type == "service":
        return _admin_job_service(job)

    if job_type == "network_adapter":
        return _admin_job_network(job)

    if job_type == "renew_ip":
        code, out = _run_process(["ipconfig.exe", "/renew"], timeout=120)
        return out if code == 0 else f"IP yenileme hata kodu {code}: {out}"

    if job_type == "app_admin":
        return _admin_job_app(job)

    if job_type == "ui_action":
        from actions.ui_navigation import _ui_action_local
        return _ui_action_local(
            str(job.get("action") or "click"),
            str(job.get("window_title") or ""),
            str(job.get("target") or ""),
            str(job.get("text") or ""),
            str(job.get("keys") or ""),
            str(job.get("direction") or "down"),
            int(job.get("amount") or 5),
            str(job.get("method") or "auto"),
            bool(job.get("clear_first", True)),
            bool(job.get("confirmed", True)),
        )

    if job_type == "shell":
        command = str(job.get("command") or "")
        blocked = _hard_block_reason(command)
        if blocked:
            return "Güvenlik: " + blocked
        return _run_shell_local(
            str(job.get("shell") or "powershell"),
            command,
            int(job.get("timeout") or 30),
        )

    return f"Bilinmeyen yönetici işi: {job_type}"


def run_admin_helper_cli(job_path: str) -> int:
    path = Path(str(job_path or ""))
    result_path: Path | None = None
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
        result_path = Path(str(job.get("result_path") or ""))
        message = _execute_admin_job(job)
        payload = {"ok": not str(message).startswith(("Hata", "Güvenlik")), "message": message}
        if result_path:
            result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception as exc:
        try:
            if result_path:
                result_path.write_text(
                    json.dumps({"ok": False, "message": f"Admin helper hatası: {exc}"}, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    if "--helper" in sys.argv:
        idx = sys.argv.index("--helper")
        job_arg = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        raise SystemExit(run_admin_helper_cli(job_arg))
