from __future__ import annotations

"""Reference HUD skin for the V6.2.1 legacy runtime.

This module is intentionally presentation-only: it does not replace the legacy
voice core, phone bridge, Phone Control Center, Drop Zone, notification center,
media service or automation engine.  It places a reference-style shell over the
existing Tk runtime and delegates actions back to the original objects.

No GitHub updater is started or installed here.
"""

import datetime as _dt
import math
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import deque

import psutil

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None
    ImageTk = None

try:
    from actions.phone_bridge import get_phone_status_record, get_phone_screen_frame
except Exception:  # keep HUD bootable if phone modules are temporarily unavailable
    def get_phone_status_record():
        return {}
    def get_phone_screen_frame():
        return {}

try:
    from actions.phone_control import queue_screen_snapshot
except Exception:
    def queue_screen_snapshot(*_args, **_kwargs):
        return {}

# ---------------------------------------------------------------------------
# Palette sampled/approximated from the supplied reference HUD.
# ---------------------------------------------------------------------------
BG = "#02090d"
BG_2 = "#031016"
HEADER = "#020b10"
SIDEBAR = "#031016"
CARD = "#06141b"
CARD_2 = "#071820"
CARD_3 = "#0a1d25"
LINE = "#14323d"
LINE_BRIGHT = "#0f6473"
CYAN = "#00d9f5"
CYAN_2 = "#00b9d1"
CYAN_DIM = "#087485"
TEAL = "#00bba8"
GREEN = "#57e59a"
TEXT = "#f3fbfd"
TEXT_2 = "#b8c8cd"
DIM = "#73878e"
DIM_2 = "#496068"
RED = "#ff566c"
GOLD = "#ffc86b"

FONT = "Segoe UI"
FONT_MONO = "Consolas"

BASE_W = 1365
BASE_H = 768


def _clamp(v, a, b):
    return max(a, min(b, v))


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def _age_seconds(record: dict) -> int:
    try:
        stamp = int(record.get("received_at", 0) or 0)
        if not stamp:
            return 999999
        if stamp > 10_000_000_000:
            stamp = stamp / 1000.0
        return max(0, int(time.time() - float(stamp)))
    except Exception:
        return 999999


class ReferenceHudShell:
    """A responsive, borderless Tk shell matching the supplied dashboard layout."""

    def __init__(self, ui):
        self.ui = ui
        self.root = ui.root
        self.frame = tk.Frame(self.root, bg=BG, bd=0, highlightthickness=0)
        self.frame.place(x=0, y=0, relwidth=1, relheight=1)
        self.canvas = tk.Canvas(
            self.frame,
            bg=BG,
            bd=0,
            highlightthickness=0,
            relief="flat",
            cursor="arrow",
        )
        self.canvas.pack(fill="both", expand=True)
        self.entry = tk.Entry(
            self.frame,
            bg="#05151b",
            fg=TEXT,
            insertbackground=CYAN,
            selectbackground="#0c5360",
            selectforeground=TEXT,
            relief="flat",
            bd=0,
            font=(FONT, 10),
        )
        self.entry.bind("<Return>", self._submit_entry)
        self.entry.bind("<Escape>", lambda _e: self.canvas.focus_set())

        self._drag_xy = None
        self._maximized = False
        self._normal_geo = ""
        self._last_size = (0, 0)
        self._orb_phase = 0.0
        self._click_regions: list[tuple[float, float, float, float, object, str]] = []
        self._hover_key = ""
        self._logs = deque(maxlen=6)
        self._last_drop_state = "Dosyayı sürükle ve bırak"
        self._alive = True
        self._fps_hint = 0

        # Phone card uses a cached *still* screenshot for realism.  This is not
        # a live stream: one low-resolution snapshot is requested occasionally
        # and the last JPEG is reused between HUD redraws.
        self._phone_preview_photo = None
        self._phone_preview_seq = -1
        self._phone_preview_received_at = 0
        self._phone_preview_last_request = 0.0
        self._phone_preview_requesting = False
        self._settings_window = None

        self._install_log_hook()
        self._configure_window()
        self._bind_events()
        self._redraw()
        self._tick_fast()
        self._tick_slow()

    # ------------------------------------------------------------------ window
    def _configure_window(self):
        sw = max(1024, int(self.root.winfo_screenwidth() or 1920))
        sh = max(700, int(self.root.winfo_screenheight() or 1080))
        # Same 16:9-like proportions as the supplied reference, while keeping
        # enough taskbar breathing room on 1080p displays.
        w = min(1600, max(1180, int(sw * 0.84)))
        h = int(w / 1.7778)
        if h > sh - 70:
            h = sh - 70
            w = int(h * 1.7778)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        self._normal_geo = f"{w}x{h}+{x}+{y}"
        try:
            self.root.overrideredirect(True)
        except Exception:
            pass
        try:
            self.root.attributes("-fullscreen", False)
        except Exception:
            pass
        self.root.geometry(self._normal_geo)
        self.root.minsize(1100, 620)
        self.root.configure(bg=BG)
        self.root.deiconify()
        self.root.lift()
        self.frame.tkraise()
        self.root.after(40, self.frame.tkraise)

    def _bind_events(self):
        self.canvas.bind("<Configure>", lambda _e: self._redraw())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<ButtonPress-1>", self._maybe_start_drag, add="+")
        self.canvas.bind("<B1-Motion>", self._window_drag, add="+")
        self.canvas.bind("<Double-Button-1>", self._header_double_click)
        self.root.bind("<Control-l>", lambda _e: self.entry.focus_set())
        # The visible reference Canvas must itself accept native file drops; the
        # legacy DropZone remains the handler/source of truth.
        try:
            from tkinterdnd2 import DND_FILES
            if hasattr(self.canvas, "drop_target_register"):
                self.canvas.drop_target_register(DND_FILES)
                self.canvas.dnd_bind("<<DropEnter>>", self._dnd_enter)
                self.canvas.dnd_bind("<<DropLeave>>", self._dnd_leave)
                self.canvas.dnd_bind("<<Drop>>", self._dnd_drop)
        except Exception:
            pass

    def _maybe_start_drag(self, e):
        h = self.canvas.winfo_height()
        sy = self._sy(h)
        if e.y <= 54 * sy and e.x < self.canvas.winfo_width() - 150 * self._sx(self.canvas.winfo_width()):
            self._drag_xy = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        else:
            self._drag_xy = None

    def _window_drag(self, e):
        if not self._drag_xy or self._maximized:
            return
        dx, dy = self._drag_xy
        try:
            self.root.geometry(f"+{e.x_root-dx}+{e.y_root-dy}")
        except Exception:
            pass

    def _header_double_click(self, e):
        if e.y <= 54 * self._sy(self.canvas.winfo_height()):
            self._toggle_maximize()

    def _minimize(self):
        try:
            self.root.overrideredirect(False)
            self.root.iconify()
            self.root.after(250, lambda: _safe(self.root.overrideredirect, True))
        except Exception:
            _safe(self.root.iconify)

    def _toggle_maximize(self):
        if self._maximized:
            self._maximized = False
            try:
                self.root.geometry(self._normal_geo)
            except Exception:
                pass
            return
        try:
            self._normal_geo = self.root.geometry()
        except Exception:
            pass
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self._maximized = True
        self.root.geometry(f"{sw}x{max(640, sh-1)}+0+0")

    def _close(self):
        self._alive = False
        try:
            self.ui._shutdown()
        except Exception:
            try:
                self.root.destroy()
            except Exception:
                pass

    # --------------------------------------------------------------- utilities
    @staticmethod
    def _sx(w):
        return max(0.72, w / BASE_W)

    @staticmethod
    def _sy(h):
        return max(0.72, h / BASE_H)

    def _xy(self, x, y, w=None, h=None):
        if w is None:
            w = self.canvas.winfo_width()
        if h is None:
            h = self.canvas.winfo_height()
        return x * self._sx(w), y * self._sy(h)

    def _font(self, size, bold=False, mono=False):
        h = max(620, self.canvas.winfo_height())
        s = max(7, int(round(size * self._sy(h))))
        return ((FONT_MONO if mono else FONT), s, "bold" if bold else "normal")

    def _rect(self, x1, y1, x2, y2, *, fill=CARD, outline=LINE, width=1, tags=()):
        return self.canvas.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline, width=width, tags=tags)

    def _line(self, x1, y1, x2, y2, *, fill=LINE, width=1, tags=()):
        return self.canvas.create_line(x1, y1, x2, y2, fill=fill, width=width, tags=tags)

    def _text(self, x, y, text, *, fill=TEXT, size=10, bold=False, anchor="nw", mono=False, tags=(), justify="left"):
        return self.canvas.create_text(
            x, y, text=str(text), fill=fill, font=self._font(size, bold, mono),
            anchor=anchor, tags=tags, justify=justify,
        )

    def _split_wordmark(self, cx, y, left="RIP", right="PERINIO", *, size=28):
        """Draw a single centered two-tone wordmark without overlapping glyphs.

        The previous skin drew a full white RIPPERINIO and then painted RIP on
        top of it.  At Windows DPI scales that produced the visible
        RIP/RIPPERINIO double-text artifact.  Measure both runs and place them
        edge-to-edge instead.
        """
        font_spec = self._font(size, True)
        try:
            measure = tkfont.Font(font=font_spec)
            left_w = measure.measure(left)
            right_w = measure.measure(right)
        except Exception:
            # Stable fallback when Tk cannot resolve the font during startup.
            left_w = int(size * 1.9 * self._sy(max(620, self.canvas.winfo_height())))
            right_w = int(size * 4.0 * self._sy(max(620, self.canvas.winfo_height())))
        start = cx - (left_w + right_w) / 2
        self.canvas.create_text(start, y, text=left, fill=CYAN, font=font_spec, anchor="nw")
        self.canvas.create_text(start + left_w, y, text=right, fill=TEXT, font=font_spec, anchor="nw")

    def _register(self, x1, y1, x2, y2, command, key):
        self._click_regions.append((x1, y1, x2, y2, command, key))

    def _button(self, x1, y1, x2, y2, label, command, key, *, icon="", active=False, danger=False, small=False):
        hover = self._hover_key == key
        if danger:
            fg = RED
            bg = "#12131a" if not hover else "#27121a"
            out = "#3d2330"
        elif active:
            fg = CYAN
            bg = "#06232b" if not hover else "#08313b"
            out = CYAN_DIM
        else:
            fg = TEXT_2 if not hover else CYAN
            bg = "#07151b" if not hover else "#0a212a"
            out = LINE
        self._rect(x1, y1, x2, y2, fill=bg, outline=out, width=1)
        label_text = (f"{icon}  " if icon else "") + label
        self._text((x1+x2)/2, (y1+y2)/2, label_text, fill=fg, size=8 if small else 9, bold=active,
                   anchor="center")
        self._register(x1, y1, x2, y2, command, key)

    # --------------------------------------------------------------- callbacks
    def _dispatch_text(self, text: str):
        text = str(text or "").strip()
        if not text:
            return
        self._logs.appendleft(f"{_dt.datetime.now():%H:%M}   {text}")
        cb = getattr(self.ui, "on_text_command", None)
        if callable(cb):
            # Legacy UI normally dispatches text commands on a worker thread.
            # Preserve that behaviour so the reference shell never freezes
            # while Gemini/tools are working.
            try:
                import threading
                threading.Thread(target=cb, args=(text,), daemon=True).start()
                return
            except Exception:
                return
        try:
            self.ui.write_log("Siz: " + text)
        except Exception:
            pass

    def _submit_entry(self, _event=None):
        text = self.entry.get().strip()
        if not text:
            return "break"
        self.entry.delete(0, "end")
        self._dispatch_text(text)
        return "break"

    def _open_phone(self, tab="general"):
        center = getattr(self.ui, "phone_control_center", None)
        if center:
            try:
                center.open(tab)
                return
            except Exception:
                pass
        drawer = getattr(self.ui, "phone_edge_drawer", None)
        if drawer:
            _safe(drawer.expand)

    def _legacy_dropzone(self):
        mgr = getattr(self.ui, "widget_manager", None)
        return getattr(mgr, "drop_zone", None) if mgr else None

    def _dnd_enter(self, event):
        self._last_drop_state = "Bırak → RIP DROP ZONE"
        dz = self._legacy_dropzone()
        if dz and hasattr(dz, "_drop_enter"):
            return _safe(dz._drop_enter, event) or getattr(event, "action", "copy") or "copy"
        return getattr(event, "action", "copy") or "copy"

    def _dnd_leave(self, event):
        self._last_drop_state = "Dosyayı sürükle ve bırak"
        dz = self._legacy_dropzone()
        if dz and hasattr(dz, "_drop_leave"):
            return _safe(dz._drop_leave, event) or getattr(event, "action", "copy") or "copy"
        return getattr(event, "action", "copy") or "copy"

    def _dnd_drop(self, event):
        dz = self._legacy_dropzone()
        if dz and hasattr(dz, "_drop"):
            self._last_drop_state = "Dosya alındı · işlemler açılıyor"
            return _safe(dz._drop, event) or getattr(event, "action", "copy") or "copy"
        self._last_drop_state = "Drop Zone kullanılamıyor"
        return getattr(event, "action", "copy") or "copy"

    def _open_dropzone(self):
        mgr = getattr(self.ui, "widget_manager", None)
        dz = getattr(mgr, "drop_zone", None) if mgr else None
        if dz:
            try:
                dz.show_actions()
                return
            except Exception:
                try:
                    dz.show_overlay()
                    return
                except Exception:
                    pass
        self._last_drop_state = "Drop Zone açılamadı"

    def _find_phone(self):
        try:
            from actions.phone_control import queue_find_phone, wait_for_ack
            row = queue_find_phone()
            cid = str(row.get("id") or "")
            self._last_drop_state = "Telefon bul komutu gönderildi"
            # Do not block the Tk thread waiting for Android ACK.
            import threading
            def worker():
                ack = wait_for_ack(cid, 12)
                msg = str((ack or {}).get("detail") or "") if ack else "Telefon yanıtı bekleniyor"
                self.root.after(0, lambda: setattr(self, "_last_drop_state", msg[:70]))
            threading.Thread(target=worker, daemon=True).start()
        except Exception as exc:
            self._last_drop_state = f"Telefon bul hata: {exc}"[:70]

    def _open_phone_location(self):
        center = getattr(self.ui, "phone_control_center", None)
        if center:
            # Location controls added by the V6.2.1b phone patch live on General.
            try:
                center.open("general")
                request = getattr(center, "_request_phone_location", None)
                if callable(request):
                    self.root.after(250, request)
                return
            except Exception:
                pass
        try:
            from actions.phone_control import queue_phone_location
            queue_phone_location()
        except Exception:
            pass

    def _open_settings(self):
        try:
            from hud_settings import ReferenceSettingsWindow
            if self._settings_window is None:
                self._settings_window = ReferenceSettingsWindow(self.root, self.ui)
            self._settings_window.open()
            return
        except Exception as exc:
            try:
                self.ui.write_log("SYS: Ayarlar penceresi açılamadı — " + str(exc))
            except Exception:
                pass

    def _open_v6(self):
        vc = getattr(self.ui, "v6_control_center", None)
        if vc:
            _safe(vc.open)
            return
        _safe(getattr(self.ui, "_open_v6_control_center", lambda: None))

    def _open_downloads(self):
        mgr = getattr(self.ui, "widget_manager", None)
        tracker = getattr(mgr, "download_tracker", None) if mgr else None
        for name in ("show", "open", "open_folder"):
            fn = getattr(tracker, name, None)
            if callable(fn):
                try:
                    fn(); return
                except Exception:
                    pass
        self._open_dropzone()

    def _toggle_pause(self):
        _safe(getattr(self.ui, "_toggle_pause", lambda: None))

    def _media(self, action):
        try:
            from actions.media import control_media
            control_media(action)
        except Exception:
            pass

    # --------------------------------------------------------------- log hook
    def _install_log_hook(self):
        # The legacy UI is still the source of truth.  Mirror its log lines into
        # the reference "Son Komutlar" card instead of replacing logging.
        original = getattr(self.ui, "write_log", None)
        if not callable(original) or getattr(original, "_reference_hud_wrapped", False):
            return

        shell = self
        def wrapped(message, *args, **kwargs):
            try:
                result = original(message, *args, **kwargs)
            except Exception:
                result = None
            try:
                raw = str(message or "").strip().replace("\n", " ")
                if raw:
                    for prefix in ("SYS:", "Siz:", "RIPPERINIO:", "ERR:"):
                        if raw.startswith(prefix):
                            raw = raw[len(prefix):].strip()
                            break
                    shell._logs.appendleft(f"{_dt.datetime.now():%H:%M}   {raw[:54]}")
            except Exception:
                pass
            return result
        wrapped._reference_hud_wrapped = True
        self.ui.write_log = wrapped

    # -------------------------------------------------------------- input mouse
    def _click(self, e):
        for x1, y1, x2, y2, command, _key in reversed(self._click_regions):
            if x1 <= e.x <= x2 and y1 <= e.y <= y2:
                try:
                    command()
                except Exception:
                    pass
                return

    def _motion(self, e):
        key = ""
        for x1, y1, x2, y2, _command, k in reversed(self._click_regions):
            if x1 <= e.x <= x2 and y1 <= e.y <= y2:
                key = k
                break
        if key != self._hover_key:
            self._hover_key = key
            self._redraw()
        self.canvas.configure(cursor="hand2" if key else "arrow")

    def _leave(self, _e=None):
        if self._hover_key:
            self._hover_key = ""
            self._redraw()
        self.canvas.configure(cursor="arrow")

    # -------------------------------------------------------------- data helpers
    def _phone_info(self):
        row = {}
        try:
            row = dict(get_phone_status_record() or {})
        except Exception:
            row = {}
        age = _age_seconds(row)
        # The Android bridge can briefly skip one heartbeat while the phone is
        # dozing.  A 75 s cutoff made a healthy bridge flash red/offline even
        # while fresh device details were visible.  Keep three minutes of grace.
        online = age <= 180
        device = str(row.get("device") or row.get("model") or row.get("device_name") or "Android")
        battery = row.get("battery_percent", row.get("battery", "?"))
        ip = str(row.get("remote_ip") or row.get("ip") or "")
        # Some bridge payloads carry LAN IP under device_ip.
        if not ip:
            ip = str(row.get("device_ip") or "")
        return row, online, device, battery, ip, age

    def _refresh_phone_preview(self, force_request=False):
        """Refresh the cached phone *still* image without starting a live stream.

        A tiny snapshot is requested at most once every 45 seconds while the
        phone is online.  Loading/resizing only happens when a new frame_seq is
        observed, so the 90 ms HUD animation loop never decodes JPEGs.
        """
        row, online, _device, _battery, _ip, _age = self._phone_info()
        now = time.time()

        if online and not self._phone_preview_requesting:
            if force_request or (now - self._phone_preview_last_request >= 45.0):
                self._phone_preview_last_request = now
                self._phone_preview_requesting = True
                try:
                    # 360 px / quality 38 is plenty for the small HUD card and
                    # keeps phone CPU, LAN traffic and Python decode cost tiny.
                    queue_screen_snapshot(quality=38, max_width=360)
                except Exception:
                    pass
                finally:
                    # Command enqueue is synchronous; the Android response
                    # arrives later through the bridge and is picked up below.
                    self._phone_preview_requesting = False

        try:
            meta = dict(get_phone_screen_frame() or {})
            seq = int(meta.get("frame_seq", -1) or -1)
            path = str(meta.get("path") or "")
            if seq < 0 or not path or seq == self._phone_preview_seq:
                return
            if Image is None or ImageTk is None:
                return
            with Image.open(path) as im:
                im = im.convert("RGB")
                # Keep phone aspect ratio.  The card paints this into a
                # 58x96-ish viewport; a small cached bitmap is enough.
                im.thumbnail((54, 86), Image.Resampling.LANCZOS)
                self._phone_preview_photo = ImageTk.PhotoImage(im.copy(), master=self.root)
            self._phone_preview_seq = seq
            self._phone_preview_received_at = int(meta.get("received_at", 0) or 0)
        except Exception:
            # Preview is decorative; never let it affect bridge/HUD stability.
            return

    def _draw_phone_preview(self, x1, y1, x2, y2, sx, sy, online):
        """Draw the cached still screenshot into a phone-like frame."""
        self._rect(x1, y1, x2, y2, fill="#02080c", outline=CYAN_DIM if online else LINE)
        # speaker/notch
        self._line((x1+x2)/2-8*sx, y1+4*sy, (x1+x2)/2+8*sx, y1+4*sy, fill=DIM_2)
        photo = self._phone_preview_photo
        if photo is not None:
            try:
                pw = max(1, photo.width()); ph = max(1, photo.height())
                aw = max(1, x2-x1-6*sx); ah = max(1, y2-y1-10*sy)
                scale = min(aw/pw, ah/ph)
                # PhotoImage cannot be smoothly rescaled on every 90ms redraw.
                # Draw the already-small cached image centered; it is purely a
                # realism thumbnail, not a control surface.
                self.canvas.create_image((x1+x2)/2, (y1+y2)/2+2*sy, image=photo, anchor="center")
                self._text(x1+4*sx, y2-9*sy, "SON KARE", fill=GREEN if online else DIM, size=4, bold=True)
                return
            except Exception:
                pass
        self._text((x1+x2)/2, (y1+y2)/2-5*sy, "▱", fill=CYAN if online else DIM, size=18, bold=True, anchor="center")
        self._text((x1+x2)/2, (y1+y2)/2+19*sy, "EKRAN", fill=DIM, size=4, bold=True, anchor="center")

    def _system_stats(self):
        try:
            cpu = int(round(float(getattr(self.ui, "_stats", {}).get("cpu", psutil.cpu_percent(interval=None)))))
        except Exception:
            cpu = int(psutil.cpu_percent(interval=None))
        try:
            ram = int(round(float(getattr(self.ui, "_stats", {}).get("ram", psutil.virtual_memory().percent))))
        except Exception:
            ram = int(psutil.virtual_memory().percent)
        try:
            disk = int(round(float(getattr(self.ui, "_stats", {}).get("disk", psutil.disk_usage("/").percent))))
        except Exception:
            disk = 0
        return _clamp(cpu, 0, 100), _clamp(ram, 0, 100), _clamp(disk, 0, 100)

    # --------------------------------------------------------------- draw cards
    def _draw_titlebar(self, sx, sy, w, h):
        hh = 54 * sy
        self._rect(0, 0, w, hh, fill=HEADER, outline=HEADER)
        self._line(0, hh, w, hh, fill="#0b222a")

        self._text(20*sx, 16*sy, "RIPPERINIO", fill=CYAN, size=13, bold=True)
        self._text(22*sx, 34*sy, "V7 HYBRID", fill=CYAN_2, size=5, bold=True)
        self._text(153*sx, 25*sy, "›", fill=CYAN_2, size=13, bold=True, anchor="center")

        now = _dt.datetime.now()
        self._text(185*sx, 14*sy, now.strftime("%H:%M"), fill=TEXT, size=18, bold=True)
        months = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
        self._text(260*sx, 14*sy, f"{now.day} {months[now.month]} {now.year}", fill=TEXT_2, size=6)
        tr_days = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
        self._text(260*sx, 28*sy, tr_days[now.weekday()], fill=DIM, size=6)

        self._text(w/2, 17*sy, "DAHA FAZLASI İÇİN, DAHA İYİSİNE.", fill=TEXT_2, size=6, anchor="n")
        self._line(w/2-10*sx, 37*sy, w/2+10*sx, 37*sy, fill=CYAN, width=max(1,int(2*sy)))

        cpu, ram, disk = self._system_stats()
        stats = [("◉", "CPU", f"{cpu}%"), ("◉", "GPU", "3%"), ("◉", "RAM", f"{ram}%"), ("▣", "DISK", f"{disk}%"), ("◉", "PING", "13 ms")]
        start = w - 355*sx
        for i, (ico, label, val) in enumerate(stats):
            xx = start + i*53*sx
            self._text(xx, 12*sy, ico, fill=CYAN, size=8, bold=True)
            self._text(xx+14*sx, 12*sy, label, fill=DIM, size=5)
            self._text(xx+14*sx, 25*sy, val, fill=TEXT_2, size=6, bold=True)

        # Window controls
        bx = w - 86*sx
        self._text(bx, 25*sy, "−", fill=TEXT_2, size=11, anchor="center")
        self._register(bx-12*sx, 5*sy, bx+12*sx, 47*sy, self._minimize, "win_min")
        self._text(bx+29*sx, 25*sy, "□", fill=TEXT_2, size=10, anchor="center")
        self._register(bx+17*sx, 5*sy, bx+41*sx, 47*sy, self._toggle_maximize, "win_max")
        self._text(bx+58*sx, 25*sy, "×", fill=TEXT_2 if self._hover_key != "win_close" else RED, size=12, anchor="center")
        self._register(bx+45*sx, 5*sy, bx+72*sx, 47*sy, self._close, "win_close")

    def _draw_sidebar(self, sx, sy, h):
        top = 55*sy
        sw = 150*sx
        self._rect(0, top, sw, h, fill=SIDEBAR, outline="#071d24")
        items = [
            ("⌂", "Ana Sayfa", lambda: None, "nav_home"),
            ("▦", "Uygulamalar", self._open_v6, "nav_apps"),
            ("▣", "Sistem", self._open_v6, "nav_system"),
            ("▯", "Telefon", lambda: self._open_phone("general"), "nav_phone"),
            ("▰", "Dosya Aktarımı", self._open_dropzone, "nav_files"),
            ("♫", "Medya", lambda: self._media("play_pause"), "nav_media"),
            ("⬇", "İndirme Takibi", self._open_downloads, "nav_downloads"),
            ("≋", "Uzaktan Kontrol", lambda: self._open_phone("connection"), "nav_remote"),
            ("⚙", "Ayarlar", self._open_settings, "nav_settings"),
        ]
        y = 76*sy
        for idx, (ico, label, cmd, key) in enumerate(items):
            active = idx == 0
            if active:
                self._rect(0, y-8*sy, sw, y+32*sy, fill="#06202a", outline="#06202a")
                self._rect(0, y-8*sy, 3*sx, y+32*sy, fill=CYAN, outline=CYAN)
            self._text(20*sx, y+10*sy, ico, fill=CYAN if active else TEXT_2, size=11, bold=True, anchor="center")
            self._text(39*sx, y+10*sy, label, fill=CYAN if active else TEXT_2, size=8, bold=active, anchor="w")
            self._register(8*sx, y-8*sy, sw-4*sx, y+32*sy, cmd, key)
            y += 43*sy

        self._text(24*sx, h-55*sy, "●", fill=CYAN, size=7)
        self._text(43*sx, h-59*sy, "RIP V7", fill=TEXT_2, size=6, bold=True)
        self._text(43*sx, h-47*sy, "HYBRID", fill=CYAN_2, size=6, bold=True)
        self._text(43*sx, h-35*sy, "DEV BUILD", fill=DIM, size=5)

    def _draw_system_card(self, x1, y1, x2, y2, sx, sy):
        self._rect(x1,y1,x2,y2,fill=CARD,outline=LINE)
        self._text(x1+14*sx,y1+14*sy,"⌁",fill=CYAN,size=9,bold=True)
        self._text(x1+30*sx,y1+13*sy,"SİSTEM DURUMU",fill=TEXT,size=8,bold=True)
        cpu, ram, disk = self._system_stats()
        vals = [(cpu,"CPU","3.6 GHz"),(3,"GPU","45 °C"),(ram,"RAM","4.5 / 16 GB"),(disk,"DİSK","SSD - Sağlıklı")]
        cx0=x1+42*sx
        for i,(v,label,sub) in enumerate(vals):
            cx=cx0+i*57*sx; cy=y1+69*sy; r=24*min(sx,sy)
            for rr,col in ((r,"#113440"),(r-3*sy,"#0a1c23")):
                self.canvas.create_oval(cx-rr,cy-rr,cx+rr,cy+rr,outline=col,width=max(1,int(2*sy)))
            # progress arc
            extent=max(7, min(350, 360*v/100.0))
            self.canvas.create_arc(cx-r,cy-r,cx+r,cy+r,start=90,extent=-extent,style="arc",outline=CYAN,width=max(1,int(3*sy)))
            self._text(cx,cy-3*sy,f"{v}%",fill=TEXT,size=9,bold=True,anchor="center")
            self._text(cx,cy+30*sy,label,fill=DIM,size=5,anchor="center")
            self._text(cx,cy+46*sy,sub,fill=TEXT_2,size=5,anchor="center")

    def _draw_quick_card(self, x1, y1, x2, y2, sx, sy):
        self._rect(x1,y1,x2,y2,fill=CARD,outline=LINE)
        self._text(x1+14*sx,y1+12*sy,"⚡",fill=CYAN,size=10,bold=True)
        self._text(x1+31*sx,y1+13*sy,"HIZLI İŞLEMLER",fill=TEXT,size=8,bold=True)
        actions=[
            ("◉","Steam Aç","Steam aç"),("▣","PC Kilitle","Bilgisayarı kilitle"),
            ("◉","Discord Aç","Discord aç"),("◉","PC Kapat","Bilgisayarı kapat"),
            ("◉","Tarayıcı Aç","Tarayıcı aç"),("◐","PC Uyut","Bilgisayarı uyut"),
            ("▣","Oyun Modu","Oyun modunu aç"),("↻","Yeniden Başlat","Bilgisayarı yeniden başlat"),
        ]
        gap=9*sx; bw=(x2-x1-28*sx-gap)/2; bh=31*sy
        yy=y1+45*sy
        for i,(ico,label,command) in enumerate(actions):
            col=i%2; row=i//2; xx=x1+14*sx+col*(bw+gap); y=yy+row*(bh+8*sy)
            self._button(xx,y,xx+bw,y+bh,label,lambda c=command:self._dispatch_text(c),f"quick_{i}",icon=ico,small=True)

    def _draw_commands_card(self, x1,y1,x2,y2,sx,sy):
        self._rect(x1,y1,x2,y2,fill=CARD,outline=LINE)
        self._text(x1+14*sx,y1+12*sy,"◷",fill=CYAN,size=9,bold=True)
        self._text(x1+31*sx,y1+13*sy,"SON KOMUTLAR",fill=TEXT,size=8,bold=True)
        self._text(x2-14*sx,y1+14*sy,"Temizle",fill=CYAN_2,size=6,anchor="ne")
        self._register(x2-58*sx,y1+7*sy,x2-8*sx,y1+27*sy,lambda:self._logs.clear(),"clear_logs")
        lines=list(self._logs)[:5]
        if not lines:
            lines=["02:26   Steam açıldı","02:25   Sistem durumu görüntülendi","02:24   Telefon durumu sorgulandı","02:22   HUD başlatıldı","02:22   RIP hazır"]
        yy=y1+44*sy
        for i,line in enumerate(lines):
            self._text(x1+16*sx,yy+i*25*sy,"●",fill=CYAN if i==0 else DIM_2,size=5)
            self._text(x1+32*sx,yy+i*25*sy,line,fill=GREEN if i==0 else TEXT_2,size=6)

    def _draw_hero(self,x1,y1,x2,y2,sx,sy):
        # background with subtle mountain/terrain strokes from the reference
        self._rect(x1,y1,x2,y2,fill="#020b10",outline="#020b10")
        midx=(x1+x2)/2
        # faint terrain silhouette (pure code, no image asset)
        pts=[(x1,y1+250*sy),(x1+95*sx,y1+215*sy),(x1+160*sx,y1+245*sy),(x1+245*sx,y1+195*sy),(x1+315*sx,y1+230*sy),(x2,y1+165*sy),(x2,y2),(x1,y2)]
        self.canvas.create_polygon(*[v for p in pts for v in p],fill="#041117",outline="")
        # One measured two-colour wordmark.  Do not overlay duplicate text.
        self._split_wordmark(midx, y1+33*sy, "RIP", "PERINIO", size=28)
        self._text(midx,y1+70*sy,"A I   A S S I S T A N T",fill=CYAN,size=6,bold=True,anchor="n")

        # orb
        cx=midx; cy=y1+245*sy; r=83*min(sx,sy)
        phase=self._orb_phase
        for rr,col,width in ((r+18*sy,"#07222a",1),(r+9*sy,"#09404b",1),(r,"#0aaec3",2),(r-5*sy,"#00dff8",2)):
            self.canvas.create_oval(cx-rr,cy-rr,cx+rr,cy+rr,outline=col,width=max(1,int(width*sy)))
        # pseudo glow rings
        for extra,col in ((26,"#06303a"),(19,"#06414b"),(12,"#07535f")):
            rr=(r-extra*sy)
            self.canvas.create_oval(cx-rr,cy-rr,cx+rr,cy+rr,outline=col,width=max(1,int(2*sy)))
        # orbit ellipse
        self.canvas.create_oval(cx-r*1.65,cy-r*0.31,cx+r*1.65,cy+r*0.31,outline="#0c7180",width=max(1,int(1.2*sy)))
        self.canvas.create_arc(cx-r*1.65,cy-r*0.31,cx+r*1.65,cy+r*0.31,start=(phase*26)%360,extent=95,style="arc",outline=CYAN,width=max(1,int(2*sy)))
        # moving ring marks
        for a in (phase,phase+2.15,phase+4.2):
            px=cx+math.cos(a)*r*1.57; py=cy+math.sin(a)*r*0.29
            self.canvas.create_oval(px-3*sx,py-3*sy,px+3*sx,py+3*sy,fill=CYAN,outline="")
        self._text(cx,cy+5*sy,"R",fill=CYAN,size=45,bold=True,anchor="center")
        self._text(cx,cy+44*sy,"RIP",fill=DIM,size=5,bold=True,anchor="center")

        # assistant response bar
        bar_y=y2-150*sy
        self._rect(x1+18*sx,bar_y,x2-18*sx,bar_y+49*sy,fill="#07171d",outline="#1b3942")
        self._text(midx,bar_y+25*sy,"Merhaba, nasıl yardımcı olabilirim?",fill=TEXT_2,size=8,anchor="center")
        chips=["Uygulama Aç","Sistem Durumu","Dosya Gönder","Ekran Yansıt","Daha Fazlası"]
        total=0
        widths=[]
        for c in chips:
            widths.append((len(c)*5.5+26)*sx); total+=widths[-1]
        total+=8*sx*(len(chips)-1)
        xx=midx-total/2; cy2=bar_y+68*sy
        for i,c in enumerate(chips):
            ww=widths[i]
            self._button(xx,cy2,xx+ww,cy2+27*sy,c,lambda cc=c:self._dispatch_text(cc),f"chip_{i}",small=True)
            xx+=ww+8*sx

        # command entry outline is drawn here; real Entry widget is placed in _place_entry
        ey=y2-47*sy
        self._rect(x1+14*sx,ey,x2-14*sx,ey+38*sy,fill="#04141a",outline=CYAN_DIM)
        self._text(x1+30*sx,ey+19*sy,"⌁",fill=CYAN,size=10,bold=True,anchor="center")
        # send button
        self._rect(x2-52*sx,ey+5*sy,x2-20*sx,ey+33*sy,fill=CYAN,outline=CYAN)
        self._text(x2-36*sx,ey+19*sy,"➤",fill="#002027",size=10,bold=True,anchor="center")
        self._register(x2-55*sx,ey,x2-15*sx,ey+38*sy,lambda:self._submit_entry(),"send")

    def _draw_phone_card(self,x1,y1,x2,y2,sx,sy):
        self._rect(x1,y1,x2,y2,fill=CARD,outline=LINE)
        self._text(x1+14*sx,y1+13*sy,"▯",fill=CYAN,size=10,bold=True)
        self._text(x1+32*sx,y1+13*sy,"TELEFON BAĞLANTISI",fill=TEXT,size=8,bold=True)
        row,online,device,battery,ip,age=self._phone_info()
        self._text(x2-65*sx,y1+14*sy,"Bağlı" if online else "Çevrimdışı",fill=GREEN if online else RED,size=6,anchor="ne")
        self.canvas.create_oval(x2-54*sx,y1+12*sy,x2-46*sx,y1+20*sy,fill=GREEN if online else RED,outline="")
        self._text(x2-39*sx,y1+15*sy,"◉",fill=GREEN if online else DIM,size=7,bold=True)

        # Static phone screenshot: decorative realism without live-stream load.
        px=x1+15*sx; py=y1+40*sy
        preview_w=54*sx; preview_h=88*sy
        self._draw_phone_preview(px,py,px+preview_w,py+preview_h,sx,sy,online)
        self._register(px,py,px+preview_w,py+preview_h,lambda:self._open_phone("screen"),"phone_preview")

        info_x=px+preview_w+16*sx
        self._text(info_x,py+4*sy,device,fill=TEXT,size=9,bold=True)
        self._text(info_x,py+25*sy,f"%{battery}  ▣",fill=GREEN if online else DIM,size=6,bold=True)
        self._text(info_x,py+43*sy,("Wi-Fi | "+ip) if ip else (f"Bridge • {age} sn" if online else "Bridge veri bekleniyor"),fill=TEXT_2,size=6)
        if self._phone_preview_received_at:
            try:
                ts=time.strftime("%H:%M:%S", time.localtime(self._phone_preview_received_at/1000))
                self._text(info_x,py+61*sy,f"Ekran görüntüsü • {ts}",fill=DIM,size=5)
            except Exception:
                pass

        labels=[("▣","Ekran Yansıt",lambda:self._open_phone("screen")),("●","Arama",lambda:self._open_phone("general")),("▣","Mesaj",lambda:self._open_phone("notifications")),("▦","Uygulamalar",lambda:self._open_phone("apps")),("•••","Daha Fazla",lambda:self._open_phone("general"))]
        bw=(x2-x1-30*sx)/5
        yy=y2-57*sy
        for i,(ico,label,cmd) in enumerate(labels):
            cx=x1+15*sx+bw*i+bw/2
            self.canvas.create_oval(cx-14*sx,yy,cx+14*sx,yy+28*sy,fill="#0c2028",outline="#15323a")
            self._text(cx,yy+14*sy,ico,fill=TEXT_2,size=8,bold=True,anchor="center")
            self._text(cx,yy+36*sy,label,fill=TEXT_2,size=5,anchor="center")
            self._register(cx-bw/2,yy-4*sy,cx+bw/2,yy+51*sy,cmd,f"phone_act_{i}")

    def _draw_media_card(self,x1,y1,x2,y2,sx,sy):
        self._rect(x1,y1,x2,y2,fill=CARD,outline=LINE)
        self._text(x1+14*sx,y1+13*sy,"♫",fill=CYAN,size=10,bold=True)
        self._text(x1+32*sx,y1+13*sy,"MEDYA OYNATICI",fill=TEXT,size=8,bold=True)
        snap=dict(getattr(self.ui,"_media_snapshot",{}) or {})
        active=bool(snap.get("active") or snap.get("title"))
        self.canvas.create_oval(x2-48*sx,y1+14*sy,x2-40*sx,y1+22*sy,fill=GREEN if active else DIM,outline="")
        self._text(x2-31*sx,y1+14*sy,"Hazır" if active else "Hazır",fill=TEXT_2,size=6)
        ax=x1+16*sx; ay=y1+45*sy
        self._rect(ax,ay,ax+56*sx,ay+56*sy,fill="#10242d",outline="#10242d")
        self._text(ax+28*sx,ay+28*sy,"♫",fill=DIM,size=22,bold=True,anchor="center")
        title=str(snap.get("title") or "Parça seçilmedi")
        artist=str(snap.get("artist") or "- / -")
        self._text(ax+72*sx,ay+9*sy,title[:30],fill=TEXT,size=7,bold=True)
        self._text(ax+72*sx,ay+28*sy,artist[:32],fill=DIM,size=6)
        controls=[("⌘","shuffle"),("◀","previous"),("▶","play_pause"),("▶|","next"),("↻","repeat")]
        xx=x1+40*sx; cy=y1+119*sy; step=(x2-x1-80*sx)/4
        for i,(lab,act) in enumerate(controls):
            cx=xx+i*step
            if i==2:
                self.canvas.create_oval(cx-18*sx,cy-18*sy,cx+18*sx,cy+18*sy,fill="#10252e",outline="#24424a")
            self._text(cx,cy,lab,fill=TEXT_2,size=10,bold=True,anchor="center")
            self._register(cx-20*sx,cy-20*sy,cx+20*sx,cy+20*sy,lambda a=act:self._media(a),f"media_{i}")
        vy=y2-28*sy
        self._text(x1+14*sx,vy,"🔊",fill=TEXT_2,size=7)
        self._rect(x1+42*sx,vy+4*sy,x2-43*sx,vy+9*sy,fill="#183039",outline="#183039")
        vol=int((getattr(self.ui,"_media_volume_snapshot",{}) or {}).get("volume",50) or 50)
        ww=(x2-x1-85*sx)*_clamp(vol,0,100)/100
        self._rect(x1+42*sx,vy+4*sy,x1+42*sx+ww,vy+9*sy,fill=CYAN,outline=CYAN)
        self._text(x2-15*sx,vy,f"%{vol}",fill=TEXT_2,size=5,anchor="ne")

    def _draw_download_card(self,x1,y1,x2,y2,sx,sy):
        self._rect(x1,y1,x2,y2,fill=CARD,outline=LINE)
        self._text(x1+14*sx,y1+12*sy,"⬇",fill=CYAN,size=10,bold=True)
        self._text(x1+32*sx,y1+13*sy,"İNDİRME TAKİBİ",fill=TEXT,size=8,bold=True)
        self._text(x2-13*sx,y1+14*sy,"0 aktif",fill=DIM,size=6,anchor="ne")
        bx1=x1+15*sx; by1=y1+43*sy; bx2=x2-15*sx; by2=y2-14*sy
        self._rect(bx1,by1,bx2,by2,fill="#051117",outline="#1d3840")
        # dashed border feel
        for xx in range(int(bx1+5*sx), int(bx2-5*sx), max(14,int(18*sx))):
            self._line(xx,by1, min(xx+8*sx,bx2),by1,fill="#28464e")
            self._line(xx,by2, min(xx+8*sx,bx2),by2,fill="#28464e")
        self._text((bx1+bx2)/2,(by1+by2)/2-10*sy,"♧",fill=DIM,size=16,bold=True,anchor="center")
        self._text((bx1+bx2)/2,(by1+by2)/2+13*sy,self._last_drop_state,fill=TEXT_2,size=7,bold=True,anchor="center")
        self._text((bx1+bx2)/2,(by1+by2)/2+29*sy,"Hızlı aktarım için buraya bırak",fill=DIM,size=5,anchor="center")
        self._register(bx1,by1,bx2,by2,self._open_dropzone,"drop_card")

    def _draw_footer(self,sx,sy,w,h):
        self._text(w/2,h-16*sy,"R I P P E R I N I O   |   V 7   H Y B R I D   |   A L W A Y S   W I T H   Y O U",fill=DIM_2,size=4,anchor="center")
        self.canvas.create_oval(w-88*sx,h-20*sy,w-81*sx,h-13*sy,fill=GREEN,outline="")
        self._text(w-76*sx,h-22*sy,"DEV BUILD",fill=TEXT_2,size=5)

    # ------------------------------------------------------------------- redraw
    def _redraw(self):
        if not self._alive:
            return
        try:
            w=max(900,self.canvas.winfo_width()); h=max(560,self.canvas.winfo_height())
        except Exception:
            return
        if w < 100 or h < 100:
            return
        sx=self._sx(w); sy=self._sy(h)
        self._last_size=(w,h)
        self.canvas.delete("all")
        self._click_regions.clear()
        self._rect(0,0,w,h,fill=BG,outline=BG)
        self._draw_titlebar(sx,sy,w,h)
        self._draw_sidebar(sx,sy,h)

        # Main body geometry mirrors the reference's 3-column composition.
        body_y=66*sy
        left_x=164*sx
        left_w=305*sx
        right_w=316*sx
        gap=15*sx
        right_x=w-right_w-15*sx
        center_x=left_x+left_w+gap
        center_w=right_x-gap-center_x
        if center_w < 420*sx:
            # On narrower windows shrink side cards before center.
            left_w=265*sx; right_w=285*sx
            left_x=155*sx; right_x=w-right_w-12*sx
            center_x=left_x+left_w+12*sx; center_w=right_x-12*sx-center_x

        self._draw_system_card(left_x,body_y,left_x+left_w,body_y+148*sy,sx,sy)
        self._draw_quick_card(left_x,body_y+162*sy,left_x+left_w,body_y+384*sy,sx,sy)
        self._draw_commands_card(left_x,body_y+398*sy,left_x+left_w,h-42*sy,sx,sy)

        self._draw_hero(center_x,body_y,center_x+center_w,h-42*sy,sx,sy)

        phone_h=195*sy
        media_h=200*sy
        self._draw_phone_card(right_x,body_y,w-15*sx,body_y+phone_h,sx,sy)
        self._draw_media_card(right_x,body_y+phone_h+14*sy,w-15*sx,body_y+phone_h+14*sy+media_h,sx,sy)
        self._draw_download_card(right_x,body_y+phone_h+media_h+28*sy,w-15*sx,h-42*sy,sx,sy)
        self._draw_footer(sx,sy,w,h)
        self._place_entry(center_x, center_w, h, sx, sy)
        self.frame.tkraise()

    def _place_entry(self, center_x, center_w, h, sx, sy):
        # Overlay the input widget in exactly the central command-bar slot.
        ey=h-89*sy
        x=center_x+49*sx
        width=max(120,center_w-119*sx)
        self.entry.place(x=int(x),y=int(ey),width=int(width),height=max(23,int(28*sy)))
        self.entry.configure(font=self._font(8), bg="#04141a", fg=TEXT_2)
        if not self.entry.get() and self.root.focus_get() is not self.entry:
            # Native Entry has no reliable placeholder on older Tk.  Keep empty;
            # prompt is drawn with a label-like text when unfocused.
            pass
        if self.root.focus_get() is not self.entry and not self.entry.get():
            self.canvas.create_text(x+6*sx, ey+14*sy, text="Bir komut yaz... (örn: Steam aç)", fill=DIM,
                                    font=self._font(7), anchor="w")

    # ------------------------------------------------------------------- timers
    def _tick_fast(self):
        if not self._alive:
            return
        self._orb_phase += 0.055
        self._redraw()
        try:
            self.root.after(90, self._tick_fast)
        except Exception:
            pass

    def _tick_slow(self):
        if not self._alive:
            return
        # Track whether the Drop Zone is actually available for native DnD.
        try:
            mgr=getattr(self.ui,"widget_manager",None); dz=getattr(mgr,"drop_zone",None)
            if dz is not None and getattr(dz,"available",False) and self._last_drop_state.startswith("Drop Zone"):
                self._last_drop_state="Dosyayı sürükle ve bırak"
        except Exception:
            pass
        try:
            self._refresh_phone_preview()
        except Exception:
            pass
        try:
            self.root.after(1100, self._tick_slow)
        except Exception:
            pass


def install_reference_hud(ui):
    """Install once and expose as ``ui.reference_hud`` for debugging/tests."""
    existing=getattr(ui,"reference_hud",None)
    if existing is not None:
        return existing
    shell=ReferenceHudShell(ui)
    ui.reference_hud=shell
    return shell
