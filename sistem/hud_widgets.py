from __future__ import annotations

import math
import base64
import io
import os
import shutil
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Any

from PIL import Image, ImageTk, ImageOps

from app_config import load_app_config, save_app_config
from actions.phone_commands import last_command_ack, queue_call_action, stage_file_for_phone
from actions.platform_utils import open_path
from hud_window_chrome import HudWindowShell
from hud_services import (
    ACTION_HISTORY,
    AudioPeakMonitor,
    DownloadTracker,
    ErrorHunter,
    GlobalShortcutMonitor,
    NetworkDeviceScanner,
    WindowsNotificationWatcher,
)


BG = "#0D1728"
PANEL = "#111D2F"
CYAN = "#5EEBFF"
CYAN_DIM = "#243650"
TEXT = "#F5F8FF"
TEXT_DIM = "#8195B6"
GREEN = "#55F2C2"
RED = "#FF647C"
GOLD = "#F5C76B"
BLUE = "#A58BFF"


def _blend(hex_color: str, factor: float, base: str = "#070B14") -> str:
    factor = max(0.0, min(1.0, factor))
    try:
        a = tuple(int(hex_color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
        b = tuple(int(base.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
        out = tuple(int(bv + (av - bv) * factor) for av, bv in zip(a, b))
        return "#%02x%02x%02x" % out
    except Exception:
        return hex_color


def _fmt_bytes(value: int | float) -> str:
    n = max(0.0, float(value or 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def _dedupe_path(directory: Path, name: str) -> Path:
    target = directory / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(2, 1000):
        candidate = directory / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{int(time.time())}{suffix}"


class _DragState:
    def __init__(self):
        self.dx = 0
        self.dy = 0
        self.start_w = 0
        self.start_h = 0
        self.start_x = 0
        self.start_y = 0


class DraggableHUDWidget:
    MIN_W = 150
    MIN_H = 64

    def __init__(
        self,
        root: tk.Misc,
        key: str,
        title: str,
        x: int,
        y: int,
        width: int = 220,
        height: int = 88,
        *,
        manager=None,
        body_widget: tk.Widget | None = None,
    ):
        self.root = root
        self.key = key
        self.title_text = title
        self.manager = manager
        self.edit_mode = False
        self.hidden = False
        self.opacity = 1.0
        self._placed = {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}
        self.drag = _DragState()

        self.frame = tk.Frame(root, bg=BG, highlightbackground="#263A57", highlightthickness=1)
        self.frame.place(**self._placed)
        self.header = tk.Label(
            self.frame, text=title, bg=PANEL, fg=CYAN, anchor="w", padx=8,
            font=("Segoe UI Variable Text", 9, "bold"), cursor="arrow",
        )
        self.header.place(x=0, y=0, relwidth=1, height=28)
        self.body = body_widget or tk.Label(
            self.frame, text="--", bg=BG, fg=TEXT, anchor="nw", justify="left",
            padx=8, pady=7, font=("Segoe UI Variable Text", 9),
        )
        self.body.place(x=0, y=28, relwidth=1, relheight=1, height=-28)

        self.resize_handle = tk.Label(
            self.frame, text="◢", fg=CYAN, bg=BG, font=("Segoe UI Variable Text", 9), cursor="arrow",
        )
        self.resize_handle.place_forget()

        for widget in (self.header, self.frame):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<ButtonRelease-1>", self._end_drag)
            widget.bind("<Button-3>", self._toggle_hidden)
            widget.bind("<Control-MouseWheel>", self._opacity_wheel)
        self.resize_handle.bind("<ButtonPress-1>", self._start_resize)
        self.resize_handle.bind("<B1-Motion>", self._resize)
        self.resize_handle.bind("<ButtonRelease-1>", self._end_drag)
        self.resize_handle.bind("<Button-3>", self._toggle_hidden)

    def apply_saved(self, cfg: dict[str, Any]) -> None:
        try:
            self.hidden = bool(cfg.get("hidden", False))
            self.opacity = max(0.35, min(1.0, float(cfg.get("opacity", 1.0))))
            x = int(cfg.get("x", self._placed["x"]))
            y = int(cfg.get("y", self._placed["y"]))
            w = max(self.MIN_W, int(cfg.get("width", self._placed["width"])))
            h = max(self.MIN_H, int(cfg.get("height", self._placed["height"])))
            self._placed = {"x": x, "y": y, "width": w, "height": h}
            self.frame.place_configure(**self._placed)
            self._apply_opacity()
            if self.hidden:
                self.frame.place_forget()
        except Exception:
            pass

    def snapshot(self) -> dict[str, Any]:
        try:
            if self.frame.winfo_ismapped():
                self._placed.update({
                    "x": int(self.frame.winfo_x()), "y": int(self.frame.winfo_y()),
                    "width": int(self.frame.winfo_width()), "height": int(self.frame.winfo_height()),
                })
        except Exception:
            pass
        return {**self._placed, "opacity": round(self.opacity, 2), "hidden": bool(self.hidden)}

    def _save(self):
        if self.manager is not None:
            try:
                self.manager.save_layout()
                return
            except Exception:
                pass
        try:
            cfg = load_app_config()
            layout = dict(cfg.get("hud_widget_layout", {}) or {})
            layout[self.key] = self.snapshot()
            save_app_config({"hud_widget_layout": layout})
        except Exception:
            pass

    def _start_drag(self, event):
        if not self.edit_mode:
            return
        self.drag.dx = event.x_root - self.frame.winfo_rootx()
        self.drag.dy = event.y_root - self.frame.winfo_rooty()
        try:
            self.frame.lift()
        except Exception:
            pass

    def _drag(self, event):
        if not self.edit_mode:
            return
        x = max(0, event.x_root - self.root.winfo_rootx() - self.drag.dx)
        y = max(0, event.y_root - self.root.winfo_rooty() - self.drag.dy)
        try:
            max_x = max(0, self.root.winfo_width() - self.frame.winfo_width())
            max_y = max(0, self.root.winfo_height() - self.frame.winfo_height())
            x = min(x, max_x)
            y = min(y, max_y)
        except Exception:
            pass
        self.frame.place_configure(x=x, y=y)

    def _start_resize(self, event):
        if not self.edit_mode:
            return
        self.drag.start_x = event.x_root
        self.drag.start_y = event.y_root
        self.drag.start_w = self.frame.winfo_width()
        self.drag.start_h = self.frame.winfo_height()

    def _resize(self, event):
        if not self.edit_mode:
            return
        dw = event.x_root - self.drag.start_x
        dh = event.y_root - self.drag.start_y
        w = max(self.MIN_W, self.drag.start_w + dw)
        h = max(self.MIN_H, self.drag.start_h + dh)
        try:
            w = min(w, max(self.MIN_W, self.root.winfo_width() - self.frame.winfo_x()))
            h = min(h, max(self.MIN_H, self.root.winfo_height() - self.frame.winfo_y()))
        except Exception:
            pass
        self.frame.place_configure(width=w, height=h)
        self._place_handle()

    def _end_drag(self, _event=None):
        if self.edit_mode:
            self._save()

    def _toggle_hidden(self, _event=None):
        if not self.edit_mode:
            return
        self.hidden = not self.hidden
        self.header.configure(text=f"{self.title_text}  [{'GİZLİ' if self.hidden else 'AÇIK'}]")
        self._apply_opacity()
        self._save()

    def _opacity_wheel(self, event):
        if not self.edit_mode:
            return "break"
        delta = 0.08 if int(getattr(event, "delta", 0) or 0) > 0 else -0.08
        self.opacity = max(0.35, min(1.0, self.opacity + delta))
        self._apply_opacity()
        self._save()
        return "break"

    def _apply_opacity(self):
        factor = self.opacity * (0.50 if self.hidden and self.edit_mode else 1.0)
        bg = _blend(BG, factor)
        panel = _blend(PANEL, factor)
        fg = _blend(TEXT, max(0.5, factor), base="#1b2a29")
        accent = _blend(CYAN, max(0.55, factor), base="#0a2a28")
        try:
            self.frame.configure(bg=bg, highlightbackground=accent if self.edit_mode else _blend(CYAN_DIM, factor))
            self.header.configure(bg=panel, fg=accent)
            self.body.configure(bg=bg, fg=fg)
            self.resize_handle.configure(bg=bg, fg=accent)
        except Exception:
            pass

    def _place_handle(self):
        try:
            self.resize_handle.place(relx=1.0, rely=1.0, x=-18, y=-18, width=18, height=18)
        except Exception:
            pass

    def set_edit_mode(self, enabled: bool):
        self.edit_mode = bool(enabled)
        if enabled:
            if not self.frame.winfo_ismapped():
                self.frame.place(**self._placed)
            self.header.configure(
                text=f"{self.title_text}  [{'GİZLİ' if self.hidden else 'AÇIK'}]",
                cursor="fleur",
            )
            self.frame.configure(highlightthickness=2)
            self._place_handle()
        else:
            self.header.configure(text=self.title_text, cursor="arrow")
            self.resize_handle.place_forget()
            self.frame.configure(highlightthickness=1)
            if self.hidden:
                self.frame.place_forget()
        self._apply_opacity()
        self._save()

    def set_text(self, text: str):
        try:
            self.body.configure(text=str(text or "--"))
        except Exception:
            pass

    def show(self):
        if not self.hidden or self.edit_mode:
            try:
                self.frame.place(**self._placed)
            except Exception:
                pass

    def hide(self):
        try:
            self.frame.place_forget()
        except Exception:
            pass


class DeviceMapHUDWidget(DraggableHUDWidget):
    def __init__(self, *args, **kwargs):
        # Canvas parent must be the frame, so replace after super initializes.
        super().__init__(*args, **kwargs)
        try:
            self.body.destroy()
        except Exception:
            pass
        self.body = tk.Canvas(self.frame, bg=BG, highlightthickness=0)
        self.body.place(x=0, y=28, relwidth=1, relheight=1, height=-28)
        self.devices: list[dict[str, str]] = []

    def update_devices(self, rows: list[dict[str, str]]):
        self.devices = list(rows or [])[:10]
        self._draw_nodes()

    def _draw_nodes(self):
        c = self.body
        try:
            w = max(150, c.winfo_width())
            h = max(60, c.winfo_height())
            c.delete("all")
            c.configure(bg=_blend(BG, self.opacity))
            if not self.devices:
                c.create_text(8, 10, text="Ağ cihazları bekleniyor...", anchor="nw", fill=TEXT_DIM, font=("Segoe UI", 8))
                return
            cx, cy = w // 2, max(18, h // 2)
            c.create_oval(cx-8, cy-8, cx+8, cy+8, fill=CYAN, outline="")
            c.create_text(cx, cy+17, text="RIP", fill=CYAN, font=("Segoe UI", 7, "bold"))
            radius = max(30, min(w, h) * 0.34)
            others = self.devices[:8]
            for i, row in enumerate(others):
                angle = -math.pi/2 + (math.tau * i / max(1, len(others)))
                x = cx + math.cos(angle) * radius
                y = cy + math.sin(angle) * radius * 0.72
                online = row.get("state") == "online"
                col = GREEN if online else "#52605f"
                c.create_line(cx, cy, x, y, fill="#174642", width=1)
                c.create_oval(x-5, y-5, x+5, y+5, fill=col, outline="")
                label = row.get("name") or row.get("kind") or "Cihaz"
                if label == "Cihaz":
                    label = row.get("ip", "")
                c.create_text(x, y+12, text=str(label)[:12], fill=TEXT if online else TEXT_DIM, font=("Segoe UI", 7))
        except Exception:
            pass

    def set_edit_mode(self, enabled: bool):
        super().set_edit_mode(enabled)
        self._draw_nodes()


class NotificationCenterWindow:
    """Telefon + Windows bildirimlerini uygulama bazinda akilli gruplar."""

    def __init__(self, ui):
        self.ui = ui
        self.root = ui.root
        self.rows: list[dict[str, Any]] = []
        self.filter_mode = "all"
        self.window = tk.Toplevel(self.root)
        self.window.withdraw()
        self.window.title("RIP Notification Center")
        self.window.configure(bg="#111827")
        self.window.geometry("640x680")
        self.window.minsize(500, 440)
        self.window.attributes("-topmost", True)
        self.window.protocol("WM_DELETE_WINDOW", self.hide)

        head = tk.Frame(self.window, bg=PANEL, height=46)
        head.pack(fill="x")
        tk.Label(
            head, text="RIP // AKILLI BILDIRIM MERKEZI", fg=CYAN, bg=PANEL,
            font=("Segoe UI", 12, "bold")
        ).pack(side="left", padx=14, pady=12)
        tk.Button(head, text="×", command=self.hide, bg=PANEL, fg=TEXT, bd=0,
                  font=("Segoe UI", 13)).pack(side="right", padx=10)

        tools = tk.Frame(self.window, bg="#1C2940")
        tools.pack(fill="x", padx=10, pady=(8, 2))
        for label, mode in (("TUMU", "all"), ("TELEFON", "phone"), ("WINDOWS", "windows"), ("ARAMALAR", "calls")):
            tk.Button(
                tools, text=label, command=lambda m=mode: self.set_filter(m),
                bg="#263853", fg=CYAN if mode == "all" else TEXT_DIM, bd=0,
                padx=10, pady=5, cursor="hand2",
            ).pack(side="left", padx=3)
        tk.Button(
            tools, text="TEMIZLE", command=self.clear, bg="#251015", fg=RED,
            bd=0, padx=10, pady=5, cursor="hand2",
        ).pack(side="right", padx=3)

        self.stats = tk.Label(
            self.window, text="", fg=TEXT_DIM, bg="#111827",
            font=("Segoe UI", 8), anchor="w",
        )
        self.stats.pack(fill="x", padx=14, pady=(4, 0))

        self.list_frame = tk.Frame(self.window, bg="#111827")
        self.list_frame.pack(fill="both", expand=True)
        self.text = tk.Text(
            self.list_frame, bg="#111827", fg=TEXT, bd=0, wrap="word",
            font=("Segoe UI", 10), padx=14, pady=12, state="disabled",
        )
        self.text.pack(fill="both", expand=True)
        self.text.tag_config("app", foreground=CYAN, font=("Segoe UI", 11, "bold"))
        self.text.tag_config("count", foreground=GOLD, font=("Segoe UI", 9, "bold"))
        self.text.tag_config("body", foreground=TEXT)
        self.text.tag_config("call", foreground=GREEN, font=("Segoe UI", 10, "bold"))
        self.text.tag_config("missed", foreground=RED, font=("Segoe UI", 10, "bold"))
        self.text.tag_config("dim", foreground=TEXT_DIM)

    def set_filter(self, mode: str):
        self.filter_mode = str(mode or "all")
        self.render()

    def clear(self):
        # Yalniz HUD gorunumunu temizler; Windows/telefon kaynak ayarlarini silmez.
        self.rows.clear()
        self.render()

    def add(self, record: dict[str, Any]):
        rid = str(record.get("id") or "")
        marker = (rid, str(record.get("received_at") or ""), str(record.get("call_state") or ""))
        for old in self.rows[-180:]:
            old_marker = (str(old.get("id") or ""), str(old.get("received_at") or ""), str(old.get("call_state") or ""))
            if marker == old_marker:
                return
        row = dict(record)
        row.setdefault("source", "phone")
        self.rows.append(row)
        self.rows = self.rows[-500:]
        self.render()

    def replace_windows(self, rows: list[dict[str, Any]]):
        phone = [r for r in self.rows if r.get("source") != "windows"]
        prepared = []
        for row in rows:
            item = dict(row)
            item["source"] = "windows"
            prepared.append(item)
        self.rows = (phone + prepared)[-500:]
        self.render()

    def _filtered_rows(self) -> list[dict[str, Any]]:
        mode = self.filter_mode
        if mode == "phone":
            return [r for r in self.rows if r.get("source") != "windows"]
        if mode == "windows":
            return [r for r in self.rows if r.get("source") == "windows"]
        if mode == "calls":
            return [r for r in self.rows if str(r.get("kind")) == "call"]
        return list(self.rows)

    def grouped(self) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for row in sorted(self._filtered_rows(), key=lambda r: int(r.get("received_at", 0) or 0)):
            app = str(row.get("app") or row.get("package") or "Telefon")
            is_call = str(row.get("kind")) == "call"
            key = f"{row.get('source','phone')}|{'call' if is_call else 'notify'}|{app.casefold()}"
            g = groups.setdefault(
                key,
                {"app": app, "count": 0, "latest": row, "source": row.get("source", "phone"), "items": []},
            )
            g["count"] += 1
            g["latest"] = row
            g["items"].append(row)
            g["items"] = g["items"][-4:]
        return sorted(groups.values(), key=lambda g: int(g["latest"].get("received_at", 0) or 0), reverse=True)

    def summary_text(self, max_groups: int = 4) -> str:
        groups = self.grouped()[:max_groups]
        if not groups:
            return "Yeni bildirim yok"
        lines = []
        for g in groups:
            latest = g["latest"]
            count = int(g["count"])
            app = str(g["app"])
            if str(latest.get("kind")) == "call":
                state = str(latest.get("call_state") or "incoming").casefold()
                who = str(latest.get("title") or latest.get("text") or "Arama")
                icon = "CEVAPSIZ" if state == "missed" else "ARAMA"
                lines.append(f"{app} · {icon}\n{who[:42]}")
            else:
                title = str(latest.get("title") or latest.get("text") or "").strip()
                suffix = f" · {count} yeni" if count > 1 else ""
                lines.append(f"{app}{suffix}\n{title[:42]}")
        return "\n".join(lines)

    @staticmethod
    def _stamp(row: dict[str, Any]) -> str:
        try:
            return time.strftime("%H:%M", time.localtime(int(row.get("received_at", 0) or 0) / 1000))
        except Exception:
            return "--:--"

    def render(self):
        try:
            groups = self.grouped()
            filtered = self._filtered_rows()
            phone_n = sum(1 for r in filtered if r.get("source") != "windows")
            win_n = sum(1 for r in filtered if r.get("source") == "windows")
            call_n = sum(1 for r in filtered if str(r.get("kind")) == "call")
            self.stats.configure(text=f"{len(filtered)} olay · Telefon {phone_n} · Windows {win_n} · Arama {call_n}")

            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            if not groups:
                self.text.insert("end", "Bu filtrede bildirim yok.\n", "dim")
            for g in groups:
                latest = g["latest"]
                self.text.insert("end", str(g["app"]), "app")
                if int(g["count"]) > 1:
                    self.text.insert("end", f"   {g['count']} yeni", "count")
                self.text.insert("end", f"   · {str(g.get('source','')).upper()}\n", "dim")

                if str(latest.get("kind")) == "call":
                    # Son arama olaylari kronolojik mini history olarak gorunur.
                    for item in reversed(g.get("items", [])[-4:]):
                        state = str(item.get("call_state") or "incoming").casefold()
                        who = str(item.get("title") or item.get("text") or "Bilinmeyen arayan")
                        state_tr = {
                            "incoming": "GELEN", "ringing": "GELEN", "missed": "CEVAPSIZ",
                            "accepted": "KABUL", "declined": "RED", "ended": "BITTI",
                        }.get(state, state.upper())
                        tag = "missed" if state == "missed" else "call"
                        self.text.insert("end", f"[{self._stamp(item)}] {state_tr} · {who}\n", tag)
                else:
                    for item in reversed(g.get("items", [])[-3:]):
                        title = str(item.get("title") or "")
                        body = str(item.get("text") or "")
                        stamp = self._stamp(item)
                        preview = " — ".join(v for v in (title, body) if v)[:360]
                        self.text.insert("end", f"[{stamp}] {preview}\n", "body")
                self.text.insert("end", "\n")
            self.text.configure(state="disabled")
        except Exception:
            pass

    def show(self):
        try:
            self.render()
            self.window.deiconify()
            self.window.lift()
            self.window.attributes("-topmost", True)
        except Exception:
            pass

    def hide(self):
        try:
            self.window.withdraw()
        except Exception:
            pass


class IncomingCallCard:
    """Incoming Call V3: notification PendingIntent + Telecom fallback + ACK."""

    def __init__(self, ui):
        self.ui = ui
        self.root = ui.root
        self.call_id = ""
        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.99)
        except Exception:
            pass
        self.win.configure(bg="#202D45")
        self.frame = tk.Frame(self.win, bg="#202D45", highlightbackground=CYAN, highlightthickness=2)
        self.frame.pack(fill="both", expand=True)

        tk.Label(
            self.frame, text="RIP // GELEN ARAMA", fg=GREEN, bg="#202D45",
            font=("Segoe UI", 11, "bold")
        ).pack(pady=(14, 3))

        self.avatar = tk.Canvas(self.frame, width=132, height=132, bg="#202D45", highlightthickness=0)
        self.avatar.pack(pady=(3, 4))
        self.avatar.create_oval(9, 9, 123, 123, outline=CYAN, width=3, fill="#0a2d2a")
        self.avatar_text = self.avatar.create_text(66, 66, text="?", fill=TEXT, font=("Segoe UI", 34, "bold"))
        self._avatar_photo = None
        self._avatar_image_id = None

        self.caller = tk.Label(
            self.frame, text="", fg="#ffffff", bg="#202D45",
            font=("Segoe UI", 24, "bold"), wraplength=540,
        )
        self.caller.pack(pady=(1, 2))
        self.app = tk.Label(self.frame, text="", fg="#7dfff6", bg="#202D45", font=("Segoe UI", 10, "bold"))
        self.app.pack(pady=(0, 10))

        actions = tk.Frame(self.frame, bg="#202D45")
        actions.pack(pady=(0, 9))
        self.accept = tk.Button(
            actions, text="✓  ACCEPT", command=lambda: self._call_action("accept"),
            bg="#063a28", fg=GREEN, activebackground=GREEN, activeforeground="#00120b",
            borderwidth=0, font=("Segoe UI", 12, "bold"), width=18, height=2, cursor="hand2",
        )
        self.accept.pack(side="left", padx=8)
        self.decline = tk.Button(
            actions, text="✕  DECLINE", command=lambda: self._call_action("decline"),
            bg="#3b1017", fg=RED, activebackground=RED, activeforeground="#180003",
            borderwidth=0, font=("Segoe UI", 12, "bold"), width=18, height=2, cursor="hand2",
        )
        self.decline.pack(side="left", padx=8)
        self.status = tk.Label(self.frame, text="", fg=TEXT_DIM, bg="#202D45", font=("Segoe UI Variable Text", 9))
        self.status.pack(pady=(0, 11))
        self._visible = False

    @staticmethod
    def _initials(name: str) -> str:
        parts = [p for p in str(name or "?").strip().split() if p]
        return "".join(p[0].upper() for p in parts[:2]) if parts else "?"

    def _render_avatar(self, caller: str, avatar_b64: str = ""):
        try:
            if self._avatar_image_id is not None:
                self.avatar.delete(self._avatar_image_id)
                self._avatar_image_id = None
            self._avatar_photo = None
            self.avatar.itemconfigure(self.avatar_text, state="normal", text=self._initials(caller))
            raw = base64.b64decode(str(avatar_b64 or ""), validate=True) if avatar_b64 else b""
            if not raw:
                return
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            image = ImageOps.fit(image, (108, 108), method=Image.Resampling.LANCZOS)
            self._avatar_photo = ImageTk.PhotoImage(image)
            self._avatar_image_id = self.avatar.create_image(66, 66, image=self._avatar_photo)
            self.avatar.itemconfigure(self.avatar_text, state="hidden")
        except Exception:
            self._avatar_photo = None
            self.avatar.itemconfigure(self.avatar_text, state="normal", text=self._initials(caller))

    def show(self, app: str, caller: str, call_id: str = "", avatar_b64: str = "", can_accept: bool = True, can_decline: bool = True, action_source: str = ""):
        self.call_id = str(call_id or "")
        self.caller.configure(text=str(caller or "Bilinmeyen arayan"))
        self.app.configure(text=f"{str(app or 'Telefon')} · Incoming Call V3")
        source_label = {"notification": "uygulama bildirimi", "telecom": "Android Telecom"}.get(str(action_source or "").casefold(), "telefon köprüsü")
        if self.call_id and (can_accept or can_decline):
            status_text = f"Arama kontrolü hazır · {source_label}"
        elif self.call_id:
            status_text = "Arama algılandı fakat cevapla/reddet eylemi sunulmadı"
        else:
            status_text = "Bu uygulama Android arama eylemi sunmuyor"
        self.status.configure(text=status_text, fg=TEXT_DIM)
        self._render_avatar(caller, avatar_b64)
        self.accept.configure(state="normal" if self.call_id and can_accept else "disabled")
        self.decline.configure(state="normal" if self.call_id and can_decline else "disabled")
        try:
            w, h = 610, 420
            sw = self.win.winfo_screenwidth()
            x = max(10, (sw - w) // 2)
            self.win.geometry(f"{w}x{h}+{x}+28")
            self.win.deiconify(); self.win.lift(); self.win.attributes("-topmost", True)
            self._visible = True
        except Exception:
            pass

    def _call_action(self, action: str):
        if not self.call_id:
            return
        self.accept.configure(state="disabled")
        self.decline.configure(state="disabled")
        try:
            cmd = queue_call_action(self.call_id, action)
            command_id = str(cmd.get("id") or "")
            self.status.configure(text="Telefona komut gidiyor...", fg=GOLD)
            ACTION_HISTORY.add("phone", f"ARAMA → {action.upper()}")
        except Exception as exc:
            self.status.configure(text=f"Gönderilemedi: {exc}", fg=RED)
            self.accept.configure(state="normal")
            self.decline.configure(state="normal")
            return

        def wait_ack():
            deadline = time.monotonic() + 12.0
            ack = {}
            while time.monotonic() < deadline:
                ack = last_command_ack(command_id)
                if ack:
                    break
                time.sleep(0.25)
            status = str(ack.get("status") or "").casefold()
            detail = str(ack.get("detail") or "")
            if status in {"ok", "done", "success"}:
                msg = "ARAMA KABUL EDILDI ✓" if action == "accept" else "ARAMA REDDEDILDI ✓"
                self.root.after(0, lambda: self.status.configure(text=msg, fg=GREEN))
                self.root.after(900, self.hide)
            elif ack:
                self.root.after(0, lambda d=detail: self.status.configure(text=d or "Telefon eylemi basarisiz", fg=RED))
                self.root.after(0, lambda: self.accept.configure(state="normal"))
                self.root.after(0, lambda: self.decline.configure(state="normal"))
            else:
                self.root.after(0, lambda: self.status.configure(text="Telefon ACK vermedi · arama telefondan kontrol edilebilir", fg=GOLD))
                self.root.after(0, lambda: self.accept.configure(state="normal"))
                self.root.after(0, lambda: self.decline.configure(state="normal"))

        threading.Thread(target=wait_ack, daemon=True, name="rip-call-action-ack").start()

    def hide(self):
        try:
            self.win.withdraw()
        except Exception:
            pass
        self._visible = False
        self.call_id = ""


class CommandPalette:
    def __init__(self, ui):
        self.ui = ui
        self.root = ui.root
        self.history: list[str] = []
        self.index = -1
        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.985)
        except Exception:
            pass
        frame = tk.Frame(self.win, bg="#1C2940", highlightbackground=CYAN, highlightthickness=2)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="RIP // KOMUT", fg=CYAN, bg="#1C2940", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(10, 2))
        self.var = tk.StringVar()
        self.entry = tk.Entry(
            frame, textvariable=self.var, bg="#061d1b", fg=TEXT, insertbackground=CYAN,
            relief="flat", font=("Segoe UI", 15), bd=0,
        )
        self.entry.pack(fill="x", padx=14, pady=(3, 7), ipady=9)
        self.hint = tk.Label(frame, text="Enter: çalıştır   ↑↓: geçmiş   Ctrl+Z: RIP geri al   Esc: kapat", fg=TEXT_DIM, bg="#1C2940", font=("Segoe UI", 8))
        self.hint.pack(anchor="w", padx=14, pady=(0, 9))
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Escape>", lambda e: self.hide())
        self.entry.bind("<Up>", lambda e: self._move(-1))
        self.entry.bind("<Down>", lambda e: self._move(1))
        self.entry.bind("<Control-z>", self._undo)
        self.entry.bind("<Control-Z>", self._undo)
        self.win.bind("<FocusOut>", self._focus_out)

    def _focus_out(self, _event=None):
        try:
            if self.win.focus_get() not in (self.entry, self.win):
                self.root.after(120, self.hide)
        except Exception:
            pass

    def _move(self, direction: int):
        if not self.history:
            return "break"
        if self.index < 0:
            self.index = len(self.history)
        self.index = max(0, min(len(self.history) - 1, self.index + direction))
        self.var.set(self.history[self.index])
        self.entry.icursor("end")
        return "break"

    def _undo(self, _event=None):
        cb = getattr(self.ui, "on_undo_request", None)
        if callable(cb):
            threading.Thread(target=cb, daemon=True).start()
        self.hide()
        return "break"

    def _submit(self, _event=None):
        text = self.var.get().strip()
        if not text:
            return "break"
        if not self.history or self.history[-1] != text:
            self.history.append(text)
            self.history = self.history[-80:]
        self.index = -1
        self.hide()
        cb = getattr(self.ui, "on_text_command", None)
        if callable(cb):
            threading.Thread(target=cb, args=(text,), daemon=True).start()
        return "break"

    def show(self):
        try:
            w, h = 620, 112
            sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
            x, y = max(10, (sw-w)//2), max(30, int(sh*0.18))
            self.win.geometry(f"{w}x{h}+{x}+{y}")
            self.win.deiconify()
            self.win.lift()
            self.entry.focus_force()
            self.entry.select_range(0, "end")
        except Exception:
            pass

    def hide(self):
        try:
            self.win.withdraw()
        except Exception:
            pass


class ScreenTargetOverlay:
    """Click-through neon focus lens: kutu + ok + pulse + guven etiketi."""

    TRANSPARENT = "#010203"

    def __init__(self, root):
        self.root = root
        self.win = None
        self.canvas = None
        self._hide_job = None
        self._pulse_job = None
        self._pulse = 0
        self._rect: dict[str, Any] = {}
        self._label = "HEDEF"
        self._confidence = None

    @staticmethod
    def _virtual_screen_rect():
        if os.name == "nt":
            try:
                import ctypes
                u = ctypes.windll.user32
                return (u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79))
            except Exception:
                pass
        return (0, 0, 1920, 1080)

    @staticmethod
    def _click_through(win: tk.Toplevel):
        if os.name != "nt":
            return
        try:
            import ctypes
            hwnd = int(win.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
            )
        except Exception:
            pass

    def hide(self):
        if self._hide_job:
            try: self.root.after_cancel(self._hide_job)
            except Exception: pass
        if self._pulse_job:
            try: self.root.after_cancel(self._pulse_job)
            except Exception: pass
        self._hide_job = self._pulse_job = None
        if self.win is not None:
            try: self.win.destroy()
            except Exception: pass
        self.win = None
        self.canvas = None

    def _draw(self):
        c = self.canvas
        if c is None:
            return
        c.delete("all")
        vx, vy, _, _ = self._virtual_screen_rect()
        r = self._rect
        x = int(r.get("x", 0)) - vx
        y = int(r.get("y", 0)) - vy
        w = max(24, int(r.get("width", 80)))
        h = max(24, int(r.get("height", 40)))
        pad = 8 + (self._pulse % 3) * 2
        x -= pad; y -= pad; w += pad * 2; h += pad * 2
        pulse = 0.55 + 0.45 * math.sin(self._pulse * 0.8)
        color = CYAN if pulse > 0.72 else "#00a89b"

        # Dis kutu ve koseler
        c.create_rectangle(x, y, x+w, y+h, outline=color, width=3)
        corner = min(28, max(12, min(w, h)//3))
        for x0, y0, sx, sy in ((x,y,1,1),(x+w,y,-1,1),(x,y+h,1,-1),(x+w,y+h,-1,-1)):
            c.create_line(x0, y0, x0+sx*corner, y0, fill="#8ffff7", width=4)
            c.create_line(x0, y0, x0, y0+sy*corner, fill="#8ffff7", width=4)

        # Ok: hedefin sol-ustune disaridan bakar; ekran kenarinda ters taraftan gelir.
        start_x = max(26, x - 82) if x > 120 else min(c.winfo_width()-26, x+w+82)
        start_y = max(30, y - 54) if y > 80 else min(c.winfo_height()-30, y+h+54)
        target_x = x + min(w*0.22, 42)
        target_y = y + min(h*0.22, 32)
        c.create_line(start_x, start_y, target_x, target_y, fill="#7dfff6", width=4, arrow=tk.LAST, arrowshape=(18,22,7))
        c.create_oval(target_x-5, target_y-5, target_x+5, target_y+5, outline=GOLD, width=2)

        conf = ""
        if self._confidence is not None:
            try: conf = f" · %{float(self._confidence)*100:.0f}"
            except Exception: conf = ""
        text = f"RIP TARGET // {self._label[:70]}{conf}"
        # Etiket arka plani transparency key'den farkli.
        tx, ty = max(8, min(start_x, c.winfo_width()-360)), max(8, start_y-26)
        c.create_rectangle(tx-5, ty-13, tx+350, ty+13, fill="#001d1a", outline=CYAN)
        c.create_text(tx+5, ty, text=text, fill=CYAN, anchor="w", font=("Segoe UI", 9, "bold"))
        self._pulse += 1
        self._pulse_job = self.root.after(150, self._draw)

    def show(self, rect: dict[str, Any], label: str = "HEDEF", duration_ms: int = 7000, confidence=None):
        self.hide()
        self._rect = dict(rect or {})
        self._label = str(label or "HEDEF")
        self._confidence = confidence
        try:
            vx, vy, vw, vh = self._virtual_screen_rect()
            win = tk.Toplevel(self.root)
            win.overrideredirect(True); win.attributes("-topmost", True)
            win.configure(bg=self.TRANSPARENT)
            try: win.attributes("-transparentcolor", self.TRANSPARENT)
            except Exception: pass
            win.geometry(f"{max(1,vw)}x{max(1,vh)}+{vx}+{vy}")
            canvas = tk.Canvas(win, bg=self.TRANSPARENT, highlightthickness=0, bd=0)
            canvas.pack(fill="both", expand=True)
            win.update_idletasks(); self._click_through(win)
            self.win = win; self.canvas = canvas
            self._draw()
            self._hide_job = self.root.after(max(1500, int(duration_ms)), self.hide)
        except Exception:
            self.hide()


class ErrorPromptCard:
    def __init__(self, ui):
        self.ui = ui
        self.root = ui.root
        self.info: dict[str, Any] = {}
        self.win = tk.Toplevel(self.root)
        self.win.withdraw(); self.win.overrideredirect(True); self.win.attributes("-topmost", True)
        self.win.configure(bg="#291D2A")
        frame = tk.Frame(self.win, bg="#291D2A", highlightbackground=RED, highlightthickness=2)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="⚠  RIP AKILLI HATA AVCISI", fg=RED, bg="#291D2A", font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=14, pady=(12, 4))
        self.label = tk.Label(frame, text="", fg=TEXT, bg="#291D2A", wraplength=510, justify="left", font=("Segoe UI Variable Text", 9))
        self.label.pack(anchor="w", padx=14, pady=(0, 8))
        row = tk.Frame(frame, bg="#291D2A"); row.pack(pady=(0, 12))
        tk.Button(row, text="INCELE", command=self.analyze, bg="#322012", fg=GOLD, bd=0, width=13, cursor="hand2").pack(side="left", padx=5, ipady=5)
        tk.Button(row, text="DETAY KOPYALA", command=self.copy_details, bg="#0b2524", fg=CYAN, bd=0, width=14, cursor="hand2").pack(side="left", padx=5, ipady=5)
        tk.Button(row, text="YOKSAY", command=self.hide, bg="#222", fg=TEXT_DIM, bd=0, width=11, cursor="hand2").pack(side="left", padx=5, ipady=5)

    def _detail_text(self) -> str:
        title = str(self.info.get("title") or "Hata penceresi")
        detail = str(self.info.get("text") or "")
        proc = str(self.info.get("process") or "")
        pid = str(self.info.get("pid") or "")
        hist = ACTION_HISTORY.format_recent(5)
        return f"Pencere: {title}\nProgram: {proc or '?'} · PID {pid or '?'}\nMetin: {detail[:900]}\n\nSon RIP islemleri:\n{hist}"

    def show(self, info: dict[str, Any]):
        self.info = dict(info or {})
        title = str(self.info.get("title") or "Hata penceresi")
        detail = str(self.info.get("text") or "")
        proc = str(self.info.get("process") or "")
        self.label.configure(text=(f"{title}\n{proc + ' · ' if proc else ''}{detail[:280] if detail else 'Hata/uyari penceresi algilandi.'}"))
        try:
            w, h = 555, 190
            sw = self.win.winfo_screenwidth(); x = max(10, sw-w-28); y = 34
            self.win.geometry(f"{w}x{h}+{x}+{y}"); self.win.deiconify(); self.win.lift()
        except Exception:
            pass

    def copy_details(self):
        try:
            self.root.clipboard_clear(); self.root.clipboard_append(self._detail_text())
            self.label.configure(text="Hata detaylari panoya kopyalandi.")
        except Exception:
            pass

    def analyze(self):
        hwnd = int(self.info.get("hwnd", 0) or 0)
        if os.name == "nt" and hwnd:
            try:
                import win32con, win32gui
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
        context = self._detail_text()
        self.hide()
        cb = getattr(self.ui, "on_text_command", None)
        if callable(cb):
            text = (
                "Bu hata penceresini ekran analiziyle incele. Once ekrandaki hata metnini dogrula; "
                "sonra muhtemel nedeni ve en guvenli cozum adimlarini kisa ve net ver. "
                "RIP'in son islemleriyle baglanti varsa belirt.\n\n" + context
            )
            threading.Thread(target=cb, args=(text,), daemon=True).start()

    def hide(self):
        try: self.win.withdraw()
        except Exception: pass


class DropZone:
    """RIP HUD ile uyumlu, onizlemeli ve coklu dosya destekli Drop Zone V2."""

    PANEL_W = 920
    PANEL_H = 570

    def __init__(self, ui, download_tracker: DownloadTracker | None = None):
        self.ui = ui
        self.root = ui.root
        self.download_tracker = download_tracker
        self.paths: list[Path] = []
        self.available = False
        self.panel = None
        self.status = None
        self.progress = None
        self.file_list = None
        self.preview_label = None
        self.preview_canvas = None
        self.preview_meta = None
        self.summary_label = None
        self._preview_img = None
        self._busy = False

        self.overlay = tk.Toplevel(self.root)
        self.overlay.withdraw()
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg="#00110f")

        shell = tk.Frame(
            self.overlay,
            bg="#00110f",
            highlightbackground=CYAN,
            highlightthickness=2,
        )
        shell.pack(fill="both", expand=True, padx=2, pady=2)
        tk.Label(
            shell,
            text="RIP // DROP ZONE",
            bg="#00110f",
            fg=CYAN,
            font=("Segoe UI", 11, "bold"),
        ).pack(pady=(26, 5))
        tk.Label(
            shell,
            text="DOSYALARI BURAYA BIRAK",
            bg="#00110f",
            fg=TEXT,
            font=("Segoe UI", 22, "bold"),
        ).pack(pady=(2, 3))
        tk.Label(
            shell,
            text="Telefon  •  ZIP  •  Taşı  •  Kopyala  •  Yeniden adlandır",
            bg="#00110f",
            fg=TEXT_DIM,
            font=("Segoe UI", 10),
        ).pack(pady=(2, 24))
        self._install_dnd()

    # ---------- DND ----------
    def _install_dnd(self):
        try:
            from tkinterdnd2 import DND_FILES
            if not hasattr(self.root, "drop_target_register"):
                return
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<DropEnter>>", self._drop_enter)
            self.root.dnd_bind("<<DropLeave>>", self._drop_leave)
            self.root.dnd_bind("<<Drop>>", self._drop)
            self.available = True
        except Exception:
            self.available = False

    def _drop_enter(self, event):
        self.show_overlay()
        return getattr(event, "action", "copy") or "copy"

    def _drop_leave(self, event=None):
        self.hide_overlay()
        return getattr(event, "action", "copy") if event is not None else "copy"

    def _parse_paths(self, raw: str) -> list[Path]:
        try:
            values = list(self.root.tk.splitlist(raw))
        except Exception:
            values = [raw]
        out: list[Path] = []
        for value in values:
            value = str(value).strip().strip("{}")
            p = Path(value)
            if p.exists() and p not in out:
                out.append(p)
        return out

    def _drop(self, event):
        self.hide_overlay()
        paths = self._parse_paths(str(getattr(event, "data", "") or ""))
        if paths:
            self.paths = paths
            self.show_actions()
            ACTION_HISTORY.add("drop", f"DROP ZONE → {len(self.paths)} öğe")
        return getattr(event, "action", "copy") or "copy"

    def _panel_drop(self, event):
        incoming = self._parse_paths(str(getattr(event, "data", "") or ""))
        if incoming:
            merged = list(self.paths)
            for p in incoming:
                if p not in merged:
                    merged.append(p)
            self.paths = merged
            self._refresh_panel()
            self._set_status(f"{len(incoming)} öğe eklendi · toplam {len(self.paths)}", None, GREEN)
            ACTION_HISTORY.add("drop", f"DROP ZONE +{len(incoming)} öğe")
        return getattr(event, "action", "copy") or "copy"

    def show_overlay(self):
        try:
            sw, sh = self.overlay.winfo_screenwidth(), self.overlay.winfo_screenheight()
            w, h = min(820, sw - 90), 260
            self.overlay.geometry(f"{w}x{h}+{(sw-w)//2}+{max(45,(sh-h)//2)}")
            self.overlay.deiconify()
            self.overlay.lift()
            try:
                self.overlay.attributes("-alpha", 0.96)
            except Exception:
                pass
        except Exception:
            pass

    def hide_overlay(self):
        try:
            self.overlay.withdraw()
        except Exception:
            pass

    # ---------- panel ----------
    def _center_geometry(self, win, w: int, h: int):
        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = max(12, (sw - w) // 2)
        y = max(12, (sh - h) // 2)
        return f"{w}x{h}+{x}+{y}"

    def _make_button(self, parent, text: str, command, color: str = CYAN):
        b = tk.Button(
            parent,
            text=text,
            command=command,
            bg="#071b19",
            fg=color,
            activebackground=color,
            activeforeground="#00110f",
            bd=0,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
            padx=8,
            pady=9,
        )
        b.bind("<Enter>", lambda e, btn=b, c=color: btn.configure(bg="#0b2926", fg=c))
        b.bind("<Leave>", lambda e, btn=b, c=color: btn.configure(bg="#071b19", fg=c))
        return b

    def show_actions(self):
        if self.panel is not None:
            try:
                self.panel.destroy()
            except Exception:
                pass

        win = tk.Toplevel(self.root)
        self.panel = win
        win.title("RIP // DROP ZONE")
        win.protocol("WM_DELETE_WINDOW", self._close_panel)
        win.bind("<Escape>", lambda _e: self._close_panel())

        self._panel_shell = HudWindowShell(
            win,
            title="DROP ZONE",
            subtitle="FILES // PREVIEW // PHONE TRANSFER // LOCAL ACTIONS",
            on_close=self._close_panel,
            width=980,
            height=640,
            min_width=860,
            min_height=560,
        )
        try:
            win.attributes("-topmost", True)
            win.transient(self.root)
        except Exception:
            pass

        content = self._panel_shell.content

        banner = tk.Frame(content, bg="#02090d")
        banner.pack(fill="x", padx=18, pady=(15, 8))
        left_head = tk.Frame(banner, bg="#02090d")
        left_head.pack(side="left")
        tk.Label(
            left_head,
            text="RIP // DROP ZONE",
            fg="#f3fbfd",
            bg="#02090d",
            font=("Segoe UI", 17, "bold"),
        ).pack(anchor="w")
        tk.Label(
            left_head,
            text="DOSYA KOMUTA MERKEZİ · SÜRÜKLE / BIRAK / TELEFONA AKTAR",
            fg="#73878e",
            bg="#02090d",
            font=("Segoe UI", 7, "bold"),
        ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            banner,
            text="●  TRANSFER READY",
            fg="#57e59a",
            bg="#05212a",
            font=("Segoe UI", 8, "bold"),
            padx=12,
            pady=7,
            highlightbackground="#0f6473",
            highlightthickness=1,
        ).pack(side="right", pady=3)

        body = tk.Frame(content, bg="#02090d")
        body.pack(fill="both", expand=True, padx=18, pady=(2, 10))

        # Preview / metadata card.
        left = tk.Frame(
            body, bg="#06141b", width=270,
            highlightbackground="#14323d", highlightcolor="#00d9f5",
            highlightthickness=1,
        )
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        tk.Label(
            left, text="ÖNİZLEME", bg="#06141b", fg="#00d9f5",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(11, 7))

        preview_box = tk.Frame(
            left, bg="#020b10", height=235,
            highlightbackground="#0f6473", highlightthickness=1,
        )
        preview_box.pack(fill="x", padx=10, pady=(0, 8))
        preview_box.pack_propagate(False)
        self.preview_canvas = tk.Canvas(
            preview_box, bg="#020b10", highlightthickness=0, bd=0
        )
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_label = self.preview_canvas

        self.preview_meta = tk.Label(
            left, text="", bg="#06141b", fg="#b8c8cd",
            justify="left", anchor="nw", wraplength=238,
            font=("Segoe UI", 8),
        )
        self.preview_meta.pack(fill="x", padx=12, pady=(4, 10))

        # File list + actions.
        right = tk.Frame(body, bg="#02090d")
        right.pack(side="left", fill="both", expand=True, padx=(14, 0))

        self.summary_label = tk.Label(
            right, text="", fg="#f3fbfd", bg="#02090d",
            font=("Segoe UI", 14, "bold"), justify="left", anchor="w",
        )
        self.summary_label.pack(fill="x", pady=(2, 8))

        list_card = tk.Frame(
            right, bg="#06141b",
            highlightbackground="#14323d", highlightcolor="#00d9f5",
            highlightthickness=1,
        )
        list_card.pack(fill="both", expand=True, pady=(0, 10))
        list_head = tk.Frame(list_card, bg="#06141b")
        list_head.pack(fill="x", padx=10, pady=(8, 2))
        tk.Label(
            list_head, text="DOSYALAR", bg="#06141b", fg="#00d9f5",
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left")
        tk.Label(
            list_head, text="Çoklu seçim desteklenir", bg="#06141b", fg="#73878e",
            font=("Segoe UI", 7),
        ).pack(side="right")

        list_wrap = tk.Frame(list_card, bg="#071820")
        list_wrap.pack(fill="both", expand=True, padx=9, pady=(4, 9))
        self.file_list = tk.Listbox(
            list_wrap, bg="#071820", fg="#b8c8cd",
            selectbackground="#0a3340", selectforeground="#f3fbfd",
            bd=0, highlightthickness=0, activestyle="none",
            font=("Segoe UI", 9), height=8,
        )
        self.file_list.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        sb = tk.Scrollbar(
            list_wrap, command=self.file_list.yview,
            bg="#14323d", troughcolor="#071820",
            activebackground="#0f6473", relief="flat", bd=0,
        )
        sb.pack(side="right", fill="y")
        self.file_list.configure(yscrollcommand=sb.set)
        self.file_list.bind("<<ListboxSelect>>", lambda _e: self._refresh_preview_from_selection())

        grid = tk.Frame(right, bg="#02090d")
        grid.pack(fill="x")
        actions = [
            ("📱  TELEFONA GÖNDER", self.send_phone, "#57e59a"),
            ("📦  ZIP OLUŞTUR", self.make_zip, "#00d9f5"),
            ("📂  TAŞI", lambda: self.copy_move(move=True), "#00d9f5"),
            ("📄  KOPYALA", lambda: self.copy_move(move=False), "#00d9f5"),
            ("✎  YENİDEN ADLANDIR", self.rename, "#ffc86b"),
            ("▶  AÇ", self.open_items, "#f3fbfd"),
            ("⌖  KONUMU AÇ", self.reveal, "#b8c8cd"),
            ("⧉  YOLU KOPYALA", self.copy_paths, "#b8c8cd"),
        ]
        for i, (label, cmd, color) in enumerate(actions):
            b = self._make_button(grid, label, cmd, color)
            b.configure(
                bg="#071820", activebackground="#0a2630",
                highlightthickness=1, highlightbackground="#14323d",
                padx=10, pady=8,
            )
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=4, pady=4)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        status_card = tk.Frame(
            content, bg="#041219",
            highlightbackground="#14323d", highlightthickness=1,
        )
        status_card.pack(fill="x", padx=18, pady=(0, 14))
        self.status = tk.Label(
            status_card,
            text="Hazır · Yeni dosya eklemek için bu pencereye de sürükleyebilirsin.",
            bg="#041219", fg="#73878e", anchor="w", font=("Segoe UI", 8),
        )
        self.status.pack(fill="x", padx=10, pady=(7, 4))
        self.progress = tk.Canvas(
            status_card, height=5, bg="#071820", highlightthickness=0, bd=0
        )
        self.progress.pack(fill="x", padx=10, pady=(0, 8))

        # Action window itself accepts file drops too.
        try:
            from tkinterdnd2 import DND_FILES
            if hasattr(win, "drop_target_register"):
                win.drop_target_register(DND_FILES)
                win.dnd_bind("<<Drop>>", self._panel_drop)
        except Exception:
            pass

        self._refresh_panel()
        win.lift()
        try:
            win.focus_force()
        except Exception:
            pass

    def _close_panel(self):
        try:
            if self.panel is not None:
                self.panel.destroy()
        finally:
            self.panel = None
            self._preview_img = None

    def _summary(self) -> str:
        if not self.paths:
            return "Dosya yok"
        total = self._total_size(self.paths)
        if len(self.paths) == 1:
            p = self.paths[0]
            kind = "KLASÖR" if p.is_dir() else (p.suffix.upper().lstrip(".") or "DOSYA")
            return f"{p.name}\n{kind}  •  {_fmt_bytes(total)}"
        return f"{len(self.paths)} ÖĞE  •  {_fmt_bytes(total)}"

    def _total_size(self, paths: list[Path]) -> int:
        total = 0
        for p in paths:
            try:
                if p.is_file():
                    total += p.stat().st_size
                elif p.is_dir():
                    # Fast enough for a user-picked set, but never fail the UI.
                    for f in p.rglob("*"):
                        if f.is_file():
                            try:
                                total += f.stat().st_size
                            except Exception:
                                pass
            except Exception:
                pass
        return total

    def _file_meta(self, p: Path) -> str:
        try:
            if p.is_dir():
                count = sum(1 for x in p.iterdir())
                return f"KLASÖR\n{count} öğe\n\n{p}"
            size = _fmt_bytes(p.stat().st_size)
            ext = p.suffix.upper().lstrip(".") or "DOSYA"
            extra = ""
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
                try:
                    with Image.open(p) as im:
                        extra = f"\n{im.width} × {im.height} px"
                except Exception:
                    pass
            return f"{ext}  •  {size}{extra}\n\n{p}"
        except Exception:
            return str(p)

    def _refresh_panel(self):
        if self.panel is None:
            return
        try:
            self.summary_label.configure(text=self._summary())
            self.file_list.delete(0, "end")
            for p in self.paths:
                marker = "DIR " if p.is_dir() else (p.suffix.upper().lstrip(".")[:4] or "FILE")
                try:
                    size = _fmt_bytes(p.stat().st_size) if p.is_file() else "KLASÖR"
                except Exception:
                    size = "--"
                self.file_list.insert("end", f"{marker:<5}  {p.name}    {size}")
            if self.paths:
                self.file_list.selection_set(0)
                self.file_list.activate(0)
                self._refresh_preview(self.paths[0])
        except Exception:
            pass

    def _refresh_preview_from_selection(self):
        if self.file_list is None:
            return
        sel = self.file_list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.paths):
            self._refresh_preview(self.paths[idx])

    @staticmethod
    def _video_preview_frame(p: Path):
        """Videodan gerçek bir kare döndürür; OpenCV yoksa None."""
        try:
            import cv2
            cap = cv2.VideoCapture(str(p))
            if not cap.isOpened():
                cap.release()
                return None
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if total > 10:
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total * 0.08)))
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
            cap.release()
            if not ok or frame is None:
                return None
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb)
        except Exception:
            return None

    def _refresh_preview(self, p: Path):
        if self.preview_canvas is None or self.preview_meta is None:
            return

        canvas = self.preview_canvas
        self._preview_img = None
        try:
            canvas.delete("all")
            canvas.update_idletasks()
        except Exception:
            pass

        cw = max(220, int(canvas.winfo_width() or 0))
        ch = max(195, int(canvas.winfo_height() or 0))
        suffix = p.suffix.lower()
        image_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
        video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".wmv"}

        frame = None
        kind = ""
        try:
            if p.is_file() and suffix in image_exts:
                with Image.open(p) as im:
                    frame = ImageOps.exif_transpose(im).convert("RGB")
                kind = "PHOTO"
            elif p.is_file() and suffix in video_exts:
                frame = self._video_preview_frame(p)
                kind = "VIDEO"
        except Exception:
            frame = None

        if frame is not None:
            try:
                fitted = ImageOps.contain(
                    frame,
                    (max(40, cw - 16), max(40, ch - 24)),
                    method=Image.Resampling.LANCZOS,
                )
                self._preview_img = ImageTk.PhotoImage(fitted)
                canvas.create_image(
                    cw // 2,
                    ch // 2,
                    image=self._preview_img,
                    anchor="center",
                )
                if kind == "VIDEO":
                    canvas.create_text(
                        8, ch - 7,
                        text="VIDEO PREVIEW",
                        fill=TEXT_DIM,
                        anchor="sw",
                        font=("Segoe UI", 7, "bold"),
                    )
            except Exception:
                frame = None

        if frame is None:
            icon = "DIR" if p.is_dir() else (suffix.upper().lstrip(".")[:6] or "FILE")
            if suffix in video_exts:
                icon = "VIDEO"
            canvas.create_text(
                cw // 2,
                ch // 2,
                text=icon,
                fill=CYAN,
                font=("Segoe UI", 28, "bold"),
                anchor="center",
            )

        self.preview_meta.configure(text=self._file_meta(p))

    def _set_status(self, text: str, pct: float | None = None, color: str = TEXT_DIM):
        try:
            if self.status is not None:
                self.status.configure(text=text, fg=color)
            if self.progress is not None:
                self.progress.delete("all")
                self.progress.update_idletasks()
                w = max(1, self.progress.winfo_width())
                if pct is not None:
                    pct = max(0.0, min(100.0, float(pct)))
                    self.progress.create_rectangle(0, 0, w * pct / 100.0, 7, fill=CYAN, outline="")
        except Exception:
            pass

    def _notify(self, title: str, body: str):
        try:
            self.root.after(0, lambda: self.ui.notify_internal(title, body))
        except Exception:
            pass

    # ---------- actions ----------
    def open_items(self):
        for p in self.paths[:8]:
            try:
                open_path(p)
            except Exception:
                pass
        self._set_status("Açma komutu gönderildi.", 100, GREEN)

    def reveal(self):
        if not self.paths:
            return
        p = self.paths[0]
        try:
            if os.name == "nt" and p.is_file():
                subprocess = __import__("subprocess")
                subprocess.Popen(["explorer.exe", "/select,", str(p)])
            else:
                open_path(p.parent if p.is_file() else p)
            self._set_status("Konum açıldı.", 100, GREEN)
        except Exception as exc:
            self._set_status(f"Konum açılamadı: {exc}", None, RED)

    def copy_paths(self):
        text = "\n".join(str(p) for p in self.paths)
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update_idletasks()
            self._set_status("Dosya yolları panoya kopyalandı.", 100, GREEN)
        except Exception as exc:
            self._set_status(f"Pano hatası: {exc}", None, RED)

    def rename(self):
        if len(self.paths) != 1:
            self._set_status("Yeniden adlandırma için tek dosya/klasör seç.", None, GOLD)
            return
        src = self.paths[0]
        new_name = simpledialog.askstring(
            "RIP Rename",
            "Yeni ad:",
            initialvalue=src.name,
            parent=self.panel,
        )
        if not new_name:
            return
        dst = src.parent / new_name
        if dst.exists():
            dst = _dedupe_path(src.parent, new_name)
        try:
            src.rename(dst)
            self.paths = [dst]
            self._refresh_panel()
            self._set_status(f"Yeniden adlandırıldı: {dst.name}", 100, GREEN)
            ACTION_HISTORY.add(
                "file",
                f"DROP RENAME → {dst.name}",
                undo={"type": "file_move", "source": str(src), "target": str(dst)},
            )
        except Exception as exc:
            self._set_status(f"Yeniden adlandırılamadı: {exc}", None, RED)

    def copy_move(self, move: bool):
        if self._busy:
            return
        dest_raw = filedialog.askdirectory(parent=self.panel, title="Hedef klasör")
        if not dest_raw:
            return
        dest = Path(dest_raw)
        paths = list(self.paths)
        self._busy = True

        def worker():
            done: list[Path] = []
            try:
                for i, p in enumerate(paths, 1):
                    target = _dedupe_path(dest, p.name)
                    if move:
                        shutil.move(str(p), str(target))
                    elif p.is_dir():
                        shutil.copytree(p, target)
                    else:
                        shutil.copy2(p, target)
                    done.append(target)
                    if move:
                        ACTION_HISTORY.add(
                            "file",
                            f"DROP MOVE → {target.name}",
                            undo={"type": "file_move", "source": str(p), "target": str(target)},
                        )
                    elif target.is_file():
                        try:
                            st = target.stat()
                            ACTION_HISTORY.add(
                                "file",
                                f"DROP COPY → {target.name}",
                                undo={
                                    "type": "file_copy", "target": str(target),
                                    "size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns),
                                },
                            )
                        except Exception:
                            pass
                    self.root.after(0, lambda i=i, n=len(paths): self._set_status(f"{i}/{n} işlendi", i * 100 / n))
                if move:
                    self.paths = done
                    self.root.after(0, self._refresh_panel)
                msg = "Taşıma tamamlandı." if move else "Kopyalama tamamlandı."
                self.root.after(0, lambda m=msg: self._set_status(m, 100, GREEN))
                self._notify("Drop Zone", msg)
            except Exception as exc:
                self.root.after(0, lambda e=str(exc): self._set_status("Dosya işlemi hatası: " + e, None, RED))
            finally:
                self._busy = False

        threading.Thread(target=worker, daemon=True, name="rip-drop-file-op").start()

    def make_zip(self):
        if not self.paths or self._busy:
            return
        default = (self.paths[0].stem if len(self.paths) == 1 else "RIP_Drop") + ".zip"
        out = filedialog.asksaveasfilename(
            parent=self.panel,
            title="ZIP kaydet",
            defaultextension=".zip",
            initialfile=default,
            filetypes=[("ZIP", "*.zip")],
        )
        if not out:
            return
        paths = list(self.paths)
        task_id = f"zip:{time.time_ns()}"
        total_items = max(1, len(paths))
        self._busy = True
        if self.download_tracker:
            self.download_tracker.register_manual(task_id, Path(out).name, "ZIP", total_items)

        def worker():
            import zipfile
            try:
                with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                    count = 0
                    for p in paths:
                        if p.is_dir():
                            files = [f for f in p.rglob("*") if f.is_file()]
                            for f in files:
                                z.write(f, arcname=str(Path(p.name) / f.relative_to(p)))
                        else:
                            z.write(p, arcname=p.name)
                        count += 1
                        if self.download_tracker:
                            self.download_tracker.update_manual(task_id, count, total_items)
                        self.root.after(0, lambda c=count, n=total_items: self._set_status(f"ZIP {c}/{n}", c * 100 / n))
                if self.download_tracker:
                    self.download_tracker.finish_manual(task_id, True)
                self.root.after(0, lambda: self._set_status(f"ZIP hazır: {Path(out).name}", 100, GREEN))
                self._notify("ZIP tamamlandı", Path(out).name)
            except Exception as exc:
                if self.download_tracker:
                    self.download_tracker.finish_manual(task_id, False)
                self.root.after(0, lambda e=str(exc): self._set_status("ZIP hatası: " + e, None, RED))
            finally:
                self._busy = False

        threading.Thread(target=worker, daemon=True, name="rip-drop-zip").start()

    def send_phone(self):
        if self._busy:
            return
        files = [p for p in self.paths if p.is_file()]
        if not files:
            self._set_status("Klasörü telefona göndermek için önce ZIP oluştur.", None, GOLD)
            return
        task_id = f"phone:{time.time_ns()}"
        total = sum(p.stat().st_size for p in files if p.exists())
        self._busy = True
        if self.download_tracker:
            self.download_tracker.register_manual(task_id, f"Telefona {len(files)} dosya", "PHONE", max(1, total))

        def wait_ack(command_id: str, timeout: float = 900.0):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                ack = last_command_ack(command_id)
                if ack:
                    return ack
                time.sleep(0.45)
            return {}

        def worker():
            done_bytes = 0
            try:
                for i, p in enumerate(files, 1):
                    staged = stage_file_for_phone(p)
                    command_id = str(staged.get("command_id") or "")
                    self.root.after(
                        0,
                        lambda i=i, n=len(files), name=p.name: self._set_status(
                            f"Telefon bekleniyor {i}/{n}: {name}",
                            (done_bytes * 100 / max(1, total)),
                            BLUE,
                        ),
                    )

                    ack = wait_ack(command_id)
                    if not ack:
                        raise TimeoutError(
                            f"Telefon 15 dakika içinde indirmeyi onaylamadı: {p.name}"
                        )
                    status = str(ack.get("status") or "").casefold()
                    detail = str(ack.get("detail") or "")
                    if status not in {"ok", "done", "success"}:
                        raise RuntimeError(detail or f"Telefon dosyayı alamadı: {p.name}")

                    # V3.1 ACK, dosya telefonda gerçekten yazılıp SHA-256 doğrulandıktan
                    # sonra gelir. Böylece sıraya almak artık 'tamamlandı' sayılmaz.
                    done_bytes += p.stat().st_size
                    if self.download_tracker:
                        self.download_tracker.update_manual(task_id, done_bytes, max(1, total))
                    self.root.after(
                        0,
                        lambda i=i, n=len(files), name=p.name: self._set_status(
                            f"Telefonda doğrulandı {i}/{n}: {name}",
                            done_bytes * 100 / max(1, total),
                            GREEN,
                        ),
                    )

                if self.download_tracker:
                    self.download_tracker.finish_manual(task_id, True)
                self.root.after(
                    0,
                    lambda: self._set_status(
                        "Telefon aktarımı TAMAM · byte ve SHA-256 doğrulandı.",
                        100,
                        GREEN,
                    ),
                )
                self._notify("Telefon aktarımı tamamlandı", f"{len(files)} dosya sağlam ulaştı")
                ACTION_HISTORY.add("phone", f"DROP → TELEFON OK · {len(files)} dosya")
            except Exception as exc:
                if self.download_tracker:
                    self.download_tracker.finish_manual(task_id, False)
                self.root.after(
                    0,
                    lambda e=str(exc): self._set_status(
                        "Telefon aktarım hatası: " + e,
                        None,
                        RED,
                    ),
                )
            finally:
                self._busy = False

        threading.Thread(target=worker, daemon=True, name="rip-drop-phone").start()


class HUDWidgetManager:
    def __init__(self, ui):
        self.ui = ui
        self.root = ui.root
        cfg = load_app_config()
        self.visible = bool(cfg.get("hud_widgets_visible", False))
        self.edit_mode = False
        layout = dict(cfg.get("hud_widget_layout", {}) or {})
        legacy_pos = dict(cfg.get("hud_widget_positions", {}) or {})

        def legacy(key, fallback):
            value = legacy_pos.get(key)
            if isinstance(value,(list,tuple)) and len(value)>=2:
                try: return int(value[0]),int(value[1])
                except Exception: pass
            return fallback

        rw = max(900, int(getattr(ui,"W",1400)))
        self.widgets: dict[str, DraggableHUDWidget] = {
            "clock": DraggableHUDWidget(self.root,"clock","CLOCK",*legacy("clock",(18,104)),width=205,height=72,manager=self),
            "pc": DraggableHUDWidget(self.root,"pc","PC HEALTH",*legacy("pc",(18,184)),width=235,height=96,manager=self),
            "phone": DraggableHUDWidget(self.root,"phone","PHONE LIVE",*legacy("phone",(18,290)),width=250,height=116,manager=self),
            "devices": DeviceMapHUDWidget(self.root,"devices","DEVICE MAP",18,416,width=260,height=156,manager=self),
            "notifications": DraggableHUDWidget(self.root,"notifications","NOTIFICATIONS",rw-318,96,width=300,height=150,manager=self),
            "history": DraggableHUDWidget(self.root,"history","ACTION HISTORY",rw-318,256,width=300,height=148,manager=self),
            "downloads": DraggableHUDWidget(self.root,"downloads","DOWNLOADS",rw-318,414,width=300,height=142,manager=self),
        }
        for key, widget in self.widgets.items():
            if isinstance(layout.get(key),dict): widget.apply_saved(layout[key])
        self.widgets["notifications"].body.bind("<Button-1>", lambda e: self.notification_center.show())
        self.widgets["notifications"].body.configure(cursor="hand2")
        self.widgets["history"].body.bind("<Double-Button-1>", lambda e: self._undo_from_history())
        self.widgets["history"].body.configure(cursor="hand2")

        self.notification_center = NotificationCenterWindow(ui)
        self.command_palette = CommandPalette(ui)
        self.target_overlay = ScreenTargetOverlay(self.root)
        self.error_card = ErrorPromptCard(ui)

        self.download_tracker = DownloadTracker(self._downloads_update_thread, self._download_complete_thread)
        self.drop_zone = DropZone(ui, self.download_tracker)
        self.device_scanner = NetworkDeviceScanner()
        self.win_notifications = WindowsNotificationWatcher(self._windows_notifications_thread)
        self.error_hunter = ErrorHunter(self._error_thread)
        self.audio_peak = AudioPeakMonitor(self._audio_peak_thread)
        self.shortcut_monitor = GlobalShortcutMonitor(self.root, self._shortcut_thread, self.command_palette.show, self.ui.toggle_dashboard)

        ACTION_HISTORY.subscribe(self._history_thread)
        self._history_thread(ACTION_HISTORY.recent(20))
        self._last_phone_status: dict[str,Any] = {}
        self._service_stop = threading.Event()
        self._device_thread = threading.Thread(target=self._device_loop, name="rip-device-map", daemon=True)
        self._device_thread.start()
        self.download_tracker.start(); self.win_notifications.start(); self.error_hunter.start(); self.audio_peak.start(); self.shortcut_monitor.start()

        if not self.visible:
            for w in self.widgets.values(): w.hide()
        self._tick()

    def save_layout(self):
        try:
            layout = {k:w.snapshot() for k,w in self.widgets.items()}
            save_app_config({"hud_widget_layout":layout,"hud_widgets_visible":self.visible})
        except Exception: pass

    def toggle(self):
        self.visible = not self.visible
        for w in self.widgets.values(): w.show() if self.visible else w.hide()
        self.save_layout(); return self.visible

    def toggle_edit_mode(self):
        self.edit_mode = not self.edit_mode
        if self.edit_mode:
            self.visible=True
        for w in self.widgets.values():
            w.set_edit_mode(self.edit_mode)
            if self.edit_mode: w.show()
        self.save_layout()
        try: self.ui.show_edit_mode_banner(self.edit_mode)
        except Exception: pass
        return self.edit_mode

    def update_phone(self,status:dict):
        self._last_phone_status=dict(status or {})
        if not status:
            self.widgets["phone"].set_text("● OFFLINE\nBridge: veri bekleniyor"); return
        batt=status.get("battery_percent","?"); ring=str(status.get("ringer_mode") or "unknown")
        ring_label={"normal":"Normal","vibrate":"Titreşim","silent":"Sessiz"}.get(ring,ring)
        charge=" ⚡" if status.get("charging") else ""
        free,total=int(status.get("storage_free_bytes",-1) or -1),int(status.get("storage_total_bytes",-1) or -1)
        stamp=int(status.get("received_at",0) or 0); age=max(0,int(time.time()-stamp/1000)) if stamp else 99999
        online=age<=45; dot="●"; state="BAĞLI" if online else "BAĞLANTI KOPTU"
        storage=f"{_fmt_bytes(free)} / {_fmt_bytes(total)}" if free>=0 and total>0 else "bilinmiyor"
        ip=str(status.get("remote_ip") or "")
        ver=str(status.get("bridge_version") or "?")
        notif="N✓" if status.get("notification_access") else "N✕"
        self.widgets["phone"].set_text(
            f"{dot} {state} · Pil %{batt}{charge} · V{ver} · {notif}\n"
            f"Ses: {ring_label} · Depo: {storage}\n{ip or 'IP ?'} · {age}s once"
        )
        try:
            self.widgets["phone"].body.configure(fg=GREEN if online else RED)
        except Exception: pass

    def add_notification(self,record:dict[str,Any]):
        self.notification_center.add(record)
        self.widgets["notifications"].set_text(self.notification_center.summary_text())

    def _windows_notifications_thread(self,rows):
        try: self.root.after(0,lambda r=list(rows): self._apply_windows_notifications(r))
        except Exception: pass

    def _apply_windows_notifications(self,rows):
        self.notification_center.replace_windows(rows)
        self.widgets["notifications"].set_text(self.notification_center.summary_text())

    def _history_thread(self,rows):
        try: self.root.after(0,lambda r=list(rows): self._apply_history(r))
        except Exception: pass

    def _apply_history(self,rows):
        lines=[]
        for r in rows[:5]:
            try: stamp=time.strftime("%H:%M",time.localtime(int(r.get("ts",0))/1000))
            except Exception: stamp="--:--"
            undoable = " ↶" if r.get("undo") and not r.get("undone") else ""
            lines.append(f"{stamp}  {str(r.get('label',''))[:34]}{undoable}")
        if lines:
            lines.append("Çift tık: son güvenli işlemi geri al")
        self.widgets["history"].set_text("\n".join(lines) if lines else "Geçmiş boş")

    def _undo_from_history(self):
        try:
            result = ACTION_HISTORY.undo_last()
            msg = str(result.get("message") or "Geri alma tamamlandı.")
            self.ui.notify_internal("RIP UNDO", msg)
            post = dict(result.get("post") or {})
            if post.get("theme"):
                self.ui.apply_hud_theme_live(str(post["theme"]), persist=False)
        except Exception as exc:
            try: self.ui.notify_internal("RIP UNDO", f"Geri alınamadı: {exc}")
            except Exception: pass

    def _shortcut_thread(self,chord:str):
        ACTION_HISTORY.add("shortcut",f"KISAYOL → {chord}")

    def _downloads_update_thread(self,rows):
        try: self.root.after(0,lambda r=list(rows): self._apply_downloads(r))
        except Exception: pass

    @staticmethod
    def _eta_text(seconds):
        try:
            sec=int(float(seconds))
        except Exception:
            return ""
        if sec < 0: return ""
        if sec < 60: return f"{sec} sn"
        if sec < 3600: return f"{sec//60} dk {sec%60:02d} sn"
        return f"{sec//3600} sa {(sec%3600)//60:02d} dk"

    def _apply_downloads(self,rows):
        if not rows:
            self.widgets["downloads"].set_text("Aktif indirme yok\nSteam · Epic · Browser · RIP transfer")
            return
        lines=[]
        for r in rows[:3]:
            pct=r.get("percent")
            current=float(r.get("current",0) or 0)
            speed=float(r.get("speed_bps",0) or 0)
            eta=self._eta_text(r.get("eta_seconds"))
            prog=f"%{float(pct):.0f}" if pct is not None else _fmt_bytes(current)
            rate=f" · {_fmt_bytes(speed)}/s" if speed>1024 else ""
            eta_text=f" · {eta}" if eta else ""
            lines.append(f"{r.get('kind','')} · {str(r.get('name',''))[:25]}\n{prog}{rate}{eta_text}")
        self.widgets["downloads"].set_text("\n".join(lines))

    def _download_complete_thread(self,row):
        try: self.root.after(0,lambda r=dict(row): self.ui.notify_internal("İndirme tamamlandı",str(r.get("name") or "Dosya")))
        except Exception: pass

    def _error_thread(self,info):
        try:
            item=dict(info or {})
            ACTION_HISTORY.add(
                "error",
                f"HATA AVCISI → {str(item.get('title') or 'Hata')[:90]}",
                details=f"{item.get('process','')} {item.get('text','')}",
            )
            self.root.after(0,lambda i=item: self.error_card.show(i))
        except Exception: pass

    def _audio_peak_thread(self,peak:float):
        try: self.ui.set_media_peak(float(peak))
        except Exception: pass

    def _device_loop(self):
        while not self._service_stop.is_set():
            rows=self.device_scanner.scan()
            phone=self._last_phone_status
            ip=str(phone.get("remote_ip") or "")
            if ip:
                matched=False
                for row in rows:
                    if row.get("ip")==ip:
                        row["name"]="Telefon"; row["kind"]="PHONE"; matched=True
                if not matched:
                    stamp=int(phone.get("received_at",0) or 0); age=max(0,int(time.time()-stamp/1000)) if stamp else 99999
                    rows.append({"name":"Telefon","ip":ip,"mac":"","state":"online" if age<=75 else "offline","kind":"PHONE"})
            try: self.root.after(0,lambda r=list(rows): self.widgets["devices"].update_devices(r))
            except Exception: pass
            self._service_stop.wait(12.0)

    def show_screen_target(self,result:dict[str,Any]):
        rect=dict(result.get("rect") or {})
        if rect:
            self.target_overlay.show(
                rect, str(result.get("label") or "HEDEF"),
                confidence=result.get("confidence"),
            )

    def _tick(self):
        try:
            self.widgets["clock"].set_text(time.strftime("%H:%M:%S\n%d.%m.%Y"))
            import psutil
            cpu=psutil.cpu_percent(interval=None); ram=psutil.virtual_memory().percent
            try: disk=psutil.disk_usage(os.environ.get("SystemDrive","C:")+"\\" if os.name=="nt" else "/").percent
            except Exception: disk=0
            self.widgets["pc"].set_text(f"CPU %{cpu:.0f}\nRAM %{ram:.0f} · Disk %{disk:.0f}")
            if self._last_phone_status: self.update_phone(self._last_phone_status)
        except Exception: pass
        try: self.root.after(1000,self._tick)
        except Exception: pass

    def stop(self):
        self._service_stop.set()
        for service in (self.download_tracker,self.win_notifications,self.error_hunter,self.audio_peak,self.shortcut_monitor):
            try: service.stop()
            except Exception: pass
        try: self.target_overlay.hide()
        except Exception: pass

