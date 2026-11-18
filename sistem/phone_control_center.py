from __future__ import annotations

import json
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox, filedialog, simpledialog

from hud_window_chrome import HudWindowShell, hud_button
from pathlib import Path

from actions.phone_bridge import get_phone_status_record, get_phone_screen_frame, get_phone_screen_stream_status, get_phone_location_record, save_phone_location_record, NOTIFY_STORE
from actions.phone_commands import queue_clipboard_to_phone, get_phone_clipboard, stage_file_for_phone
from actions.phone_control import (
    get_phone_inventory,
    queue_inventory_refresh,
    queue_open_app,
    queue_open_camera,
    queue_uninstall_app,
    queue_app_settings,
    queue_package_check,
    queue_global_action,
    queue_media_action,
    queue_volume,
    queue_brightness,
    queue_screen_timeout,
    queue_autorotate,
    queue_dnd_toggle,
    queue_flashlight_toggle,
    queue_dismiss_notification,
    queue_open_notification,
    queue_reply_notification,
    queue_delete_phone_file,
    queue_rename_phone_file,
    queue_install_apk,
    queue_open_system_panel,
    queue_vibrate,
    queue_find_phone,
    queue_phone_location,
    queue_clear_notifications,
    queue_open_permission,
    queue_phone_file_to_pc,
    queue_screen_snapshot,
    queue_screen_stream_start,
    queue_screen_stream_stop,
    queue_screen_tap,
    queue_screen_swipe,
    wait_for_ack,
)

BG = "#02090d"
CARD = "#06141b"
CARD2 = "#071820"
LINE = "#14323d"
TEXT = "#f3fbfd"
DIM = "#73878e"
MINT = "#57e59a"
CYAN = "#00d9f5"
PURPLE = "#8a7dff"
RED = "#ff566c"
GOLD = "#ffc86b"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _fmt_bytes(value) -> str:
    try:
        n = float(value)
    except Exception:
        return "—"
    if n < 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return "—"


class PhoneControlCenter:
    def __init__(self, root: tk.Misc, log_callback=None):
        self.root = root
        self.log_callback = log_callback or (lambda _m: None)
        self.win: tk.Toplevel | None = None
        self.notebook = None
        self.status_var = tk.StringVar(value="Telefon bekleniyor")
        self.meta_var = tk.StringVar(value="")
        self.permission_vars: dict[str, tk.StringVar] = {}
        self.app_search_var = tk.StringVar(value="")
        self.app_filter_var = tk.StringVar(value="KULLANICI")
        self.app_sort_var = tk.StringVar(value="AD")
        self.app_count_var = tk.StringVar(value="0 uygulama")
        self.volume_stream_var = tk.StringVar(value="music")
        self.volume_var = tk.IntVar(value=50)
        self.volume_text_var = tk.StringVar(value="MEDYA SESİ  %50")
        self._volume_user_active_until = 0.0
        self._volume_pending_until = 0.0
        self.clip_var = tk.StringVar(value="")
        self.brightness_var = tk.IntVar(value=50)
        self.brightness_text_var = tk.StringVar(value="PARLAKLIK  %50")
        self.timeout_var = tk.StringVar(value="30 sn")
        self.device_state_var = tk.StringVar(value="—")
        self.location_var = tk.StringVar(value="Konum henüz alınmadı.")
        self._last_location: dict = get_phone_location_record()
        self._apps_cache: list[dict] = []
        self._notif_rows: dict[str, dict] = {}
        self._files_cache: list[dict] = []
        self._refresh_job = None
        self._screen_live = False
        self._screen_job = None
        self._screen_photo = None
        self._screen_meta: dict = {}
        self._screen_drag_start = None
        self._screen_request_inflight = False
        self._screen_last_seq = -1
        self._screen_frame_times: list[float] = []
        self._screen_last_render_at = 0.0
        self._screen_live_started_at = 0.0
        self._screen_fallback_mode = False
        self._screen_fallback_reason = ""
        self.screen_fps_var = tk.StringVar(value="15 FPS")

    def open(self, tab: str | None = None):
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            if tab:
                self.select_tab(tab)
            self.refresh()
            return

        win = tk.Toplevel(self.root)
        self.win = win
        win.title("RIP // PHONE CONTROL CENTER")
        win.protocol("WM_DELETE_WINDOW", self.close)
        self._hud_shell = HudWindowShell(
            win,
            title="PHONE CONTROL CENTER",
            subtitle="LIVE BRIDGE // DEVICE // FILES // SCREEN // NOTIFICATIONS",
            on_close=self.close,
            width=1120,
            height=760,
            min_width=940,
            min_height=620,
        )
        body = self._hud_shell.content

        header = tk.Frame(body, bg=BG)
        header.pack(fill="x", padx=20, pady=(14, 7))
        left = tk.Frame(header, bg=BG); left.pack(side="left")
        tk.Label(left, text="RIP // PHONE CONTROL CENTER", bg=BG, fg=TEXT,
                 font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(left, text="ANDROID BRIDGE · CANLI KONTROL YÜZEYİ", bg=BG, fg=DIM,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(2, 0))
        state_box = tk.Frame(header, bg="#05212a", highlightbackground="#0f6473", highlightthickness=1)
        state_box.pack(side="right", pady=4)
        tk.Label(state_box, textvariable=self.status_var, bg="#05212a", fg=MINT,
                 font=("Segoe UI", 10, "bold"), padx=12, pady=4).pack(anchor="e", pady=(3, 0))
        tk.Label(state_box, textvariable=self.meta_var, bg="#05212a", fg=DIM,
                 font=("Segoe UI", 7), padx=12, pady=3).pack(anchor="e", pady=(0, 3))

        style = ttk.Style(win)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Rip.TNotebook", background=BG, borderwidth=0)
        style.configure("Rip.TNotebook.Tab", background=CARD, foreground=DIM,
                        padding=(15, 10), borderwidth=0, font=("Segoe UI", 9, "bold"))
        style.map("Rip.TNotebook.Tab", background=[("selected", "#08232c"), ("active", CARD2)],
                  foreground=[("selected", CYAN), ("active", TEXT)])
        style.configure("Rip.Treeview", background=CARD, fieldbackground=CARD,
                        foreground=TEXT, rowheight=31, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Rip.Treeview.Heading", background=CARD2, foreground=CYAN,
                        borderwidth=0, font=("Segoe UI", 9, "bold"))
        style.map("Rip.Treeview", background=[("selected", "#0a3340")], foreground=[("selected", TEXT)])

        nb = ttk.Notebook(body, style="Rip.TNotebook")
        self.notebook = nb
        nb.pack(fill="both", expand=True, padx=18, pady=(4, 14))

        self.pages = {}
        for key, title in [
            ("general", "GENEL"), ("apps", "UYGULAMALAR"), ("files", "DOSYALAR"),
            ("notifications", "BİLDİRİMLER"), ("control", "KONTROL"), ("device", "CİHAZ"),
            ("screen", "EKRAN"), ("clipboard", "PANO"), ("connection", "BAĞLANTI"), ("permissions", "İZİNLER"),
        ]:
            page = tk.Frame(nb, bg=BG)
            nb.add(page, text=title)
            self.pages[key] = page

        self._build_general(self.pages["general"])
        self._build_apps(self.pages["apps"])
        self._build_files(self.pages["files"])
        self._build_notifications(self.pages["notifications"])
        self._build_control(self.pages["control"])
        self._build_device(self.pages["device"])
        self._build_screen(self.pages["screen"])
        self._build_clipboard(self.pages["clipboard"])
        self._build_connection(self.pages["connection"])
        self._build_permissions(self.pages["permissions"])

        self.app_search_var.trace_add("write", lambda *_: self._render_apps())
        self.app_filter_var.trace_add("write", lambda *_: self._render_apps())
        self.app_sort_var.trace_add("write", lambda *_: self._render_apps())
        if tab:
            self.select_tab(tab)
        self.refresh(request_inventory=True)
        self._schedule_refresh()

    def close(self):
        if self._refresh_job and self.win:
            try:
                self.win.after_cancel(self._refresh_job)
            except Exception:
                pass
        self._refresh_job = None
        if self._screen_live:
            try:
                queue_screen_stream_stop()
            except Exception:
                pass
        self._screen_live = False
        if self._screen_job and self.win:
            try:
                self.win.after_cancel(self._screen_job)
            except Exception:
                pass
        self._screen_job = None
        if self.win:
            try:
                self.win.destroy()
            except Exception:
                pass
        self.win = None

    def select_tab(self, key: str):
        if not self.notebook or key not in self.pages:
            return
        try:
            self.notebook.select(self.pages[key])
        except Exception:
            pass

    def _center_geometry(self, w: int, h: int) -> str:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        return f"{w}x{h}+{max(0, (sw-w)//2)}+{max(0, (sh-h)//2)}"

    def _card(self, parent):
        return tk.Frame(parent, bg=CARD, highlightbackground=LINE, highlightcolor=CYAN, highlightthickness=1)

    def _title(self, parent, text, sub=""):
        box = tk.Frame(parent, bg=BG)
        row = tk.Frame(box, bg=BG); row.pack(fill="x")
        tk.Frame(row, bg=CYAN, width=3, height=22).pack(side="left", padx=(0, 9))
        tk.Label(row, text=text, bg=BG, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(side="left")
        if sub:
            tk.Label(box, text=sub, bg=BG, fg=DIM, font=("Segoe UI", 8)).pack(anchor="w", padx=(12, 0), pady=(3, 0))
        return box

    def _button(self, parent, text, command, accent=CYAN, width=None):
        return hud_button(parent, text, command, color=accent, width=width)

    def _build_general(self, page):
        self._title(page, "TELEFON DURUMU", "Canlı pil, depolama, bağlantı ve izin özeti.").pack(fill="x", padx=12, pady=(16, 8))
        card = self._card(page); card.pack(fill="x", padx=12, pady=6)
        self.general_text = tk.Label(card, text="—", justify="left", anchor="nw",
                                     bg=CARD, fg=TEXT, font=("Segoe UI", 11), padx=16, pady=16)
        self.general_text.pack(fill="x")
        actions = tk.Frame(page, bg=BG); actions.pack(fill="x", padx=12, pady=12)
        self._button(actions, "↻ TELEFONU TARA", lambda: self.refresh(request_inventory=True), MINT).pack(side="left", padx=(0, 8))
        self._button(actions, "◉ TİTREŞTİR", lambda: self._queue(queue_vibrate()), PURPLE).pack(side="left", padx=4)
        self._button(actions, "⌂ ANA EKRAN", lambda: self._queue(queue_global_action("home")), CYAN).pack(side="left", padx=4)
        self._button(actions, "▣ KİLİTLE", lambda: self._queue(queue_global_action("lock_screen")), GOLD).pack(side="left", padx=4)

        loc = self._card(page); loc.pack(fill="x", padx=12, pady=(2, 10))
        tk.Label(loc, text="TELEFON KONUMU", bg=CARD, fg=CYAN, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        tk.Label(loc, textvariable=self.location_var, bg=CARD, fg=TEXT, justify="left", anchor="w", font=("Segoe UI", 10)).pack(fill="x", padx=16, pady=(2, 10))
        locbtn = tk.Frame(loc, bg=CARD); locbtn.pack(fill="x", padx=12, pady=(0, 12))
        self._button(locbtn, "⌖ KONUMU BUL", self._request_phone_location, MINT).pack(side="left", padx=4)
        self._button(locbtn, "HARİTADA AÇ", self._open_phone_location_map, CYAN).pack(side="left", padx=4)
        self._button(locbtn, "🔔 ZİL ÇALDIR", lambda: self._queue(queue_find_phone()), GOLD).pack(side="left", padx=4)
        self._render_saved_location()

    def _render_saved_location(self):
        row = dict(self._last_location or {})
        if not row:
            self.location_var.set("Konum henüz alınmadı. Konum izni açıkken KONUMU BUL'a bas.")
            return
        try:
            lat = float(row.get("latitude")); lon = float(row.get("longitude"))
            acc = float(row.get("accuracy_m", -1) or -1)
            received = int(row.get("received_at", 0) or 0)
            location_time = int(row.get("location_time", 0) or 0)
            source_time = location_time or received
            age = max(0, int(time.time() - source_time / 1000)) if source_time else 0
            provider = str(row.get("provider") or "Android")
            acc_text = f" ±{acc:.0f} m" if acc >= 0 else ""
            stale = "ESKİ · " if age > 300 else ""
            self.location_var.set(f"{stale}{lat:.6f}, {lon:.6f}{acc_text} · {provider} · konum yaşı {age} sn")
        except Exception:
            self.location_var.set("Konum kaydı okunamadı.")

    def _request_phone_location(self):
        self.location_var.set("Telefon konumu isteniyor…")
        try:
            self._queue(queue_phone_location(), on_done=self._location_command_done)
        except Exception as exc:
            self.location_var.set("Konum komutu gönderilemedi: " + str(exc))

    def _location_command_done(self, ack: dict):
        status = str((ack or {}).get("status") or "").casefold()
        detail = str((ack or {}).get("detail") or "")
        if status not in {"ok", "done", "success"}:
            self.location_var.set("Konum alınamadı: " + (detail or "telefon hata döndürdü"))
            return
        try:
            data = json.loads(detail)
            if not isinstance(data, dict) or data.get("kind") != "location":
                raise ValueError("telefon geçerli konum verisi döndürmedi")
            data["received_at"] = int(time.time() * 1000)
            self._last_location = save_phone_location_record(data)
            self._render_saved_location()
            self.log_callback("✓ Telefon konumu alındı: " + self.location_var.get())
        except Exception as exc:
            self.location_var.set("Konum cevabı okunamadı: " + str(exc))

    def _open_phone_location_map(self):
        row = dict(self._last_location or {})
        if not row:
            messagebox.showinfo("RIP Telefon Konumu", "Önce KONUMU BUL ile telefondan konum al.", parent=self.win)
            return
        try:
            lat = float(row.get("latitude")); lon = float(row.get("longitude"))
            webbrowser.open(f"https://www.google.com/maps?q={lat:.7f},{lon:.7f}")
        except Exception as exc:
            messagebox.showerror("RIP Telefon Konumu", "Harita açılamadı: " + str(exc), parent=self.win)

    def _build_apps(self, page):
        self._title(page, "UYGULAMA YÖNETİCİSİ", "Çoklu seç, filtrele ve Android onaylarıyla sırayla kaldır.").pack(fill="x", padx=12, pady=(16, 8))
        top = tk.Frame(page, bg=BG); top.pack(fill="x", padx=12, pady=(0, 6))
        entry = tk.Entry(top, textvariable=self.app_search_var, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Segoe UI", 10))
        entry.pack(side="left", fill="x", expand=True, ipady=8)
        filters = ttk.Combobox(top, textvariable=self.app_filter_var, state="readonly", width=22, values=(
            "KULLANICI", "HEPSİ", "SİSTEM", "AÇILABİLEN", "DEVRE DIŞI", "30+ GÜN KULLANILMAYAN"))
        filters.pack(side="left", padx=6)
        sorts = ttk.Combobox(top, textvariable=self.app_sort_var, state="readonly", width=18, values=(
            "AD", "APK BOYUTU", "SON KULLANIM", "KURULUM TARİHİ", "GÜNCELLEME"))
        sorts.pack(side="left", padx=6)
        self._button(top, "↻", lambda: self.refresh(request_inventory=True), MINT, width=3).pack(side="left")
        bar = tk.Frame(page, bg=BG); bar.pack(fill="x", padx=12, pady=(0, 5))
        tk.Label(bar, textvariable=self.app_count_var, bg=BG, fg=DIM, font=("Segoe UI", 9, "bold")).pack(side="left")
        self._button(bar, "GÖRÜNÜRLERİ SEÇ", self._select_visible_apps, PURPLE).pack(side="right")

        self.apps_tree = ttk.Treeview(page, columns=("name","kind","apk","last","installed","package"), show="headings", style="Rip.Treeview", selectmode="extended")
        headers=(("name","UYGULAMA",210),("kind","TÜR",80),("apk","APK",90),("last","SON KULLANIM",135),("installed","KURULUM",110),("package","PAKET",320))
        for col,title,width in headers:
            self.apps_tree.heading(col,text=title); self.apps_tree.column(col,width=width,anchor="w" if col in {"name","package"} else "center")
        self.apps_tree.pack(fill="both", expand=True, padx=12, pady=4)
        btns = tk.Frame(page, bg=BG); btns.pack(fill="x", padx=12, pady=10)
        self._button(btns, "▶ AÇ", self._open_selected_app, MINT).pack(side="left", padx=(0,8))
        self._button(btns, "ⓘ AYARLAR", self._open_app_settings, CYAN).pack(side="left", padx=4)
        self._button(btns, "⌫ SEÇİLİLERİ SIRAYLA KALDIR", self._batch_uninstall_selected, RED).pack(side="right")

    def _build_files(self, page):
        self._title(page, "DOSYALAR", "RIP Mobile'ın Downloads/RIP klasörüne aldığı dosyalar.").pack(fill="x", padx=12, pady=(16, 8))
        self.files_tree = ttk.Treeview(page, columns=("name","size","date"), show="headings", style="Rip.Treeview")
        self.files_tree.heading("name", text="DOSYA"); self.files_tree.heading("size", text="BOYUT"); self.files_tree.heading("date", text="DEĞİŞTİRİLDİ")
        self.files_tree.column("name", width=520); self.files_tree.column("size", width=130, anchor="e"); self.files_tree.column("date", width=180, anchor="center")
        self.files_tree.pack(fill="both", expand=True, padx=12, pady=4)
        btns = tk.Frame(page, bg=BG); btns.pack(fill="x", padx=12, pady=10)
        self._button(btns, "↻ TARA", lambda: self.refresh(request_inventory=True), MINT).pack(side="left")
        self._button(btns, "⇡ TELEFONA GÖNDER", self._send_file_to_phone, PURPLE).pack(side="left", padx=6)
        self._button(btns, "✎ AD DEĞİŞTİR", self._rename_selected_phone_file, GOLD).pack(side="left", padx=6)
        self._button(btns, "⌫ SİL", self._delete_selected_phone_file, RED).pack(side="left", padx=6)
        self._button(btns, "APK KUR", self._install_selected_apk, MINT).pack(side="right", padx=(6,0))
        self._button(btns, "⇣ PC'YE İNDİR", self._download_selected_phone_file, CYAN).pack(side="right")
        tk.Label(page, text="PC'ye alınan dosyalar: Downloads/RIP Phone", bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0,8))

    def _build_notifications(self, page):
        self._title(page, "BİLDİRİMLER", "Son telefon bildirimleri. Temizle komutu Android bildirim panelini temizler.").pack(fill="x", padx=12, pady=(16, 8))
        self.notif_tree = ttk.Treeview(page, columns=("app","title","text","time"), show="headings", style="Rip.Treeview")
        for col, title, width in [("app","UYGULAMA",130),("title","BAŞLIK",220),("text","METİN",430),("time","SAAT",90)]:
            self.notif_tree.heading(col,text=title); self.notif_tree.column(col,width=width)
        self.notif_tree.pack(fill="both", expand=True, padx=12, pady=4)
        btns = tk.Frame(page,bg=BG); btns.pack(fill="x",padx=12,pady=10)
        self._button(btns,"↻ YENİLE",self.refresh,MINT).pack(side="left")
        self._button(btns,"TELEFONDA AÇ",self._open_selected_notification,CYAN).pack(side="left",padx=6)
        self._button(btns,"SEÇİLİYİ KAPAT",self._dismiss_selected_notification,GOLD).pack(side="left",padx=6)
        self._button(btns,"YANITLA",self._reply_selected_notification,MINT).pack(side="left",padx=6)
        self._button(btns,"BİLDİRİM PANELİ",lambda:self._queue(queue_global_action("notifications")),PURPLE).pack(side="left",padx=6)
        self._button(btns,"TÜMÜNÜ TEMİZLE",lambda:self._queue(queue_clear_notifications()),RED).pack(side="right")

    def _build_control(self, page):
        self._title(page, "UZAK KONTROL", "Accessibility açıkken sistem gezinmesi; medya ve ses kontrolleri doğrudan çalışır.").pack(fill="x", padx=12, pady=(16, 8))
        grid = self._card(page); grid.pack(fill="x", padx=12, pady=6)
        rows = [
            [("← GERİ","back",CYAN),("⌂ ANA EKRAN","home",MINT),("▦ SON UYGULAMALAR","recents",PURPLE)],
            [("▤ BİLDİRİMLER","notifications",CYAN),("⚙ HIZLI AYARLAR","quick_settings",GOLD),("▣ EKRANI KİLİTLE","lock_screen",RED)],
        ]
        for ri, row in enumerate(rows):
            rf=tk.Frame(grid,bg=CARD); rf.pack(fill="x",padx=12,pady=(12 if ri==0 else 4,4))
            for i,(label,act,col) in enumerate(row):
                self._button(rf,label,lambda a=act:self._queue(queue_global_action(a)),col).pack(side="left",fill="x",expand=True,padx=(0 if i==0 else 4,4))
        media=tk.Frame(grid,bg=CARD); media.pack(fill="x",padx=12,pady=(12,8))
        self._button(media,"⏮",lambda:self._queue(queue_media_action("media_previous")),DIM,width=8).pack(side="left",padx=4)
        self._button(media,"⏯",lambda:self._queue(queue_media_action("media_play_pause")),MINT,width=8).pack(side="left",padx=4)
        self._button(media,"⏭",lambda:self._queue(queue_media_action("media_next")),DIM,width=8).pack(side="left",padx=4)
        self._button(media,"◉ TİTREŞTİR",lambda:self._queue(queue_vibrate()),PURPLE).pack(side="right",padx=4)
        self._button(media,"🔔 ZİL ÇALDIR",lambda:self._queue(queue_find_phone()),GOLD).pack(side="right",padx=4)
        volume=tk.Frame(grid,bg=CARD); volume.pack(fill="x",padx=16,pady=(8,16))
        tk.Label(volume,textvariable=self.volume_text_var,bg=CARD,fg=DIM,font=("Segoe UI",9,"bold")).pack(side="left")
        stream=ttk.Combobox(volume,textvariable=self.volume_stream_var,state="readonly",width=11,values=("music","ring","alarm","notification"))
        stream.pack(side="left",padx=(10,0)); stream.bind("<<ComboboxSelected>>", lambda _e:self._sync_volume_from_inventory())
        scale=tk.Scale(volume,from_=0,to=100,orient="horizontal",variable=self.volume_var,bg=CARD,fg=TEXT,
                       troughcolor=CARD2,highlightthickness=0,length=420,command=self._on_volume_drag)
        scale.pack(side="left",fill="x",expand=True,padx=12)
        scale.bind("<ButtonPress-1>", self._on_volume_press)
        scale.bind("<ButtonRelease-1>", self._on_volume_release)
        self._volume_scale = scale
        self._button(volume,"UYGULA",self._apply_volume,MINT).pack(side="right")

    def _on_volume_press(self, _event=None):
        self._volume_user_active_until = time.time() + 8.0

    def _on_volume_drag(self, value):
        try:
            pct = max(0, min(100, int(float(value))))
        except Exception:
            return
        self._volume_user_active_until = time.time() + 3.5
        self.volume_text_var.set(f"MEDYA SESİ  %{pct}")

    def _on_volume_release(self, _event=None):
        self._volume_user_active_until = time.time() + 3.5

    def _volume_label(self):
        return {"music":"MEDYA","ring":"ZİL","alarm":"ALARM","notification":"BİLDİRİM"}.get(self.volume_stream_var.get(),"MEDYA")

    def _sync_volume_from_inventory(self):
        inv=get_phone_inventory(); status=get_phone_status_record()
        volumes=inv.get("volumes") if isinstance(inv.get("volumes"),dict) else status.get("volumes",{})
        row=volumes.get(self.volume_stream_var.get(),{}) if isinstance(volumes,dict) else {}
        try:
            pct=int(row.get("percent",50)); level=int(row.get("level",-1)); vmax=int(row.get("max",-1))
            if 0 <= pct <= 100:
                self.volume_var.set(pct)
                suffix=f"  • kademe {level}/{vmax}" if level>=0 and vmax>0 else ""
                self.volume_text_var.set(f"{self._volume_label()} SESİ  %{pct}{suffix}")
        except Exception: pass

    def _apply_volume(self):
        pct = max(0, min(100, int(self.volume_var.get())))
        self._volume_pending_until = time.time() + 8.0
        self._volume_user_active_until = time.time() + 8.0
        self.volume_text_var.set(f"{self._volume_label()} SESİ  %{pct}  • gönderiliyor…")

        def done(_ack):
            self._volume_pending_until = 0.0
            self._volume_user_active_until = time.time() + 1.0
            try:
                self._queue(queue_inventory_refresh())
            except Exception:
                pass

        self._queue(queue_volume(pct, self.volume_stream_var.get()), on_done=done)

    def _build_device(self, page):
        self._title(page, "CİHAZ KONTROLLERİ", "Parlaklık, ekran süresi, otomatik döndürme, DND, fener ve hızlı Android ayarları.").pack(fill="x", padx=12, pady=(16,8))
        card=self._card(page); card.pack(fill="x",padx=12,pady=6)
        tk.Label(card,textvariable=self.device_state_var,bg=CARD,fg=DIM,font=("Segoe UI",9,"bold"),justify="left").pack(anchor="w",padx=16,pady=(14,6))
        bright=tk.Frame(card,bg=CARD); bright.pack(fill="x",padx=16,pady=8)
        tk.Label(bright,textvariable=self.brightness_text_var,bg=CARD,fg=TEXT,font=("Segoe UI",9,"bold"),width=18,anchor="w").pack(side="left")
        bs=tk.Scale(bright,from_=1,to=100,orient="horizontal",variable=self.brightness_var,bg=CARD,fg=TEXT,troughcolor=CARD2,highlightthickness=0,length=420,command=lambda v:self.brightness_text_var.set(f"PARLAKLIK  %{int(float(v))}"))
        bs.pack(side="left",fill="x",expand=True,padx=10)
        self._button(bright,"UYGULA",lambda:self._queue(queue_brightness(self.brightness_var.get())),MINT).pack(side="right")
        timeout=tk.Frame(card,bg=CARD); timeout.pack(fill="x",padx=16,pady=8)
        tk.Label(timeout,text="EKRAN KAPANMA",bg=CARD,fg=TEXT,font=("Segoe UI",9,"bold")).pack(side="left")
        cb=ttk.Combobox(timeout,textvariable=self.timeout_var,state="readonly",width=14,values=("15 sn","30 sn","1 dk","2 dk","5 dk","10 dk","30 dk")); cb.pack(side="left",padx=12)
        self._button(timeout,"UYGULA",self._apply_timeout,CYAN).pack(side="left")
        toggles=tk.Frame(card,bg=CARD); toggles.pack(fill="x",padx=16,pady=(10,14))
        self._button(toggles,"↻ OTOMATİK DÖNDÜR",self._toggle_autorotate,PURPLE).pack(side="left",padx=(0,6))
        self._button(toggles,"☾ DND AÇ/KAPAT",lambda:self._queue(queue_dnd_toggle()),GOLD).pack(side="left",padx=6)
        self._button(toggles,"☀ FENER AÇ/KAPAT",lambda:self._queue(queue_flashlight_toggle()),MINT).pack(side="left",padx=6)
        panels=self._card(page); panels.pack(fill="x",padx=12,pady=12)
        tk.Label(panels,text="HIZLI AYAR EKRANLARI",bg=CARD,fg=CYAN,font=("Segoe UI",10,"bold")).pack(anchor="w",padx=16,pady=(14,8))
        row=tk.Frame(panels,bg=CARD); row.pack(fill="x",padx=16,pady=(0,14))
        for label,key,col in (("WI-FI","wifi",MINT),("BLUETOOTH","bluetooth",CYAN),("EKRAN","display",PURPLE),("SES","sound",GOLD),("UYGULAMALAR","apps",TEXT)):
            self._button(row,label,lambda k=key:self._queue(queue_open_system_panel(k)),col).pack(side="left",fill="x",expand=True,padx=3)

    def _apply_timeout(self):
        mapping={"15 sn":15,"30 sn":30,"1 dk":60,"2 dk":120,"5 dk":300,"10 dk":600,"30 dk":1800}
        self._queue(queue_screen_timeout(mapping.get(self.timeout_var.get(),30)))

    def _toggle_autorotate(self):
        inv=get_phone_inventory(); current=bool(inv.get("auto_rotate",True))
        self._queue(queue_autorotate(not current))

    def _build_screen(self, page):
        self._title(
            page,
            "CANLI TELEFON EKRANI · HIGH FPS",
            "Akıcı mod MediaProjection kullanır. Telefonda bir kez ekran paylaşım onayı ver; tıklama/kaydırma Accessibility ile gider.",
        ).pack(fill="x", padx=12, pady=(16, 8))
        toolbar = tk.Frame(page, bg=BG)
        toolbar.pack(fill="x", padx=12, pady=(0, 8))
        self.screen_status_var = tk.StringVar(value="Hazır · canlı yayın kapalı")
        tk.Label(toolbar, textvariable=self.screen_status_var, bg=BG, fg=MINT,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        fps_box = ttk.Combobox(toolbar, textvariable=self.screen_fps_var, state="readonly", width=7,
                                values=("10 FPS", "15 FPS", "20 FPS", "24 FPS", "30 FPS"))
        fps_box.pack(side="left", padx=8)
        self.screen_live_btn = self._button(toolbar, "▶ AKICI BAŞLAT", self._toggle_screen_live, MINT)
        self.screen_live_btn.pack(side="right", padx=4)
        self._button(toolbar, "📷 KAMERA MODU", self._camera_screen_mode, PURPLE).pack(side="right", padx=4)
        self._button(toolbar, "↻ TEK KARE", self._request_screen_frame, CYAN).pack(side="right", padx=4)

        card = self._card(page)
        card.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.screen_canvas = tk.Canvas(card, bg="#02050A", highlightthickness=0, cursor="crosshair")
        self.screen_canvas.pack(fill="both", expand=True, padx=10, pady=10)
        self.screen_canvas.create_text(360, 260, text="Telefon ekranı bekleniyor…", fill=DIM,
                                       font=("Segoe UI", 13, "bold"), tags="placeholder")
        self.screen_canvas.bind("<ButtonPress-1>", self._screen_press)
        self.screen_canvas.bind("<ButtonRelease-1>", self._screen_release)
        self.screen_canvas.bind("<Configure>", lambda _e: self._render_screen_frame())

    def _camera_screen_mode(self):
        self._queue(queue_open_camera())
        if not self._screen_live:
            self._start_high_fps_stream(camera_mode=True)

    def _toggle_screen_live(self):
        if self._screen_live:
            self._stop_high_fps_stream()
        else:
            self._start_high_fps_stream(camera_mode=False)

    def _start_high_fps_stream(self, camera_mode=False):
        self._screen_live = True
        if hasattr(self, "screen_live_btn"):
            self.screen_live_btn.configure(text="■ DURDUR")
        self._screen_last_seq = -1
        self._screen_frame_times.clear()
        self._screen_live_started_at = time.monotonic()
        self._screen_fallback_mode = False
        self._screen_fallback_reason = ""
        try:
            target_fps = int(str(self.screen_fps_var.get()).split()[0])
        except Exception:
            target_fps = 15
        self.screen_status_var.set(
            ("KAMERA + AKICI EKRAN · " if camera_mode else "AKICI EKRAN · ") +
            f"telefonda paylaşım onayını ver · hedef {target_fps} FPS"
        )
        try:
            if target_fps >= 30:
                stream_quality, stream_width = 38, 540
            elif target_fps >= 24:
                stream_quality, stream_width = 40, 600
            elif target_fps >= 20:
                stream_quality, stream_width = 43, 640
            else:
                stream_quality, stream_width = 48, 720
            self._queue(
                queue_screen_stream_start(
                    fps=target_fps, quality=stream_quality, max_width=stream_width
                ),
                on_done=lambda _ack: self._schedule_screen_render(20),
            )
        except Exception as exc:
            self._screen_live = False
            self.screen_status_var.set("Akıcı ekran başlatılamadı: " + str(exc))
            return
        self._schedule_screen_render(30)

    def _stop_high_fps_stream(self):
        self._screen_live = False
        self._screen_fallback_mode = False
        self._screen_fallback_reason = ""
        if hasattr(self, "screen_live_btn"):
            self.screen_live_btn.configure(text="▶ AKICI BAŞLAT")
        if self._screen_job and self.win:
            try: self.win.after_cancel(self._screen_job)
            except Exception: pass
        self._screen_job = None
        try:
            self._queue(queue_screen_stream_stop())
        except Exception:
            pass
        self.screen_status_var.set("Akıcı ekran durduruldu")

    def _schedule_screen_render(self, delay_ms=55):
        if not self._screen_live or not self.win or not self.win.winfo_exists():
            return
        if self._screen_job:
            try: self.win.after_cancel(self._screen_job)
            except Exception: pass
        self._screen_job = self.win.after(max(20, int(delay_ms)), self._screen_stream_render_tick)

    def _screen_stream_render_tick(self):
        self._screen_job = None
        if not self._screen_live or not self.win or not self.win.winfo_exists():
            return
        try:
            meta = get_phone_screen_frame()
            diag = get_phone_screen_stream_status()
            seq = int(meta.get("frame_seq", -1) or -1)
            received_at = int(meta.get("received_at", 0) or 0)
            identity = seq if seq >= 0 else received_at
            now_wall_ms = int(time.time() * 1000)
            age_ms = max(0, now_wall_ms - received_at) if received_at > 0 else 999999

            if identity != self._screen_last_seq and received_at > 0:
                self._screen_last_seq = identity
                self._render_screen_frame(meta_override=meta)
                now = time.perf_counter()
                self._screen_frame_times.append(now)
                cutoff = now - 2.0
                self._screen_frame_times = [t for t in self._screen_frame_times if t >= cutoff]
                fps = 0.0
                if len(self._screen_frame_times) >= 2:
                    span = max(0.001, self._screen_frame_times[-1] - self._screen_frame_times[0])
                    fps = (len(self._screen_frame_times) - 1) / span
                transport = str(meta.get("transport") or "stream").upper()
                if transport in {"TCP", "HTTP-FRAME", "HTTP"}:
                    self._screen_fallback_mode = False
                    self._screen_fallback_reason = ""
                prefix = "AUTO FALLBACK" if self._screen_fallback_mode or transport == "SNAPSHOT" else f"AKICI {transport}"
                self.screen_status_var.set(
                    f"{prefix} · {fps:.1f} FPS · {age_ms} ms kare yaşı · "
                    f"{int(meta.get('size',0) or 0)//1024} KB"
                )

            # MediaProjection path should produce a fresh frame quickly. If it does
            # not, automatically keep the display alive with the known-good
            # Accessibility single-frame path instead of forcing the user to click
            # TEK KARE manually. The diagnostic payload tells us which stage failed.
            elapsed = max(0.0, time.monotonic() - self._screen_live_started_at)
            fallback_threshold = 320 if self._screen_fallback_mode else 1200
            if elapsed > 1.2 and age_ms > fallback_threshold:
                self._screen_fallback_mode = True
                d_age = max(0, now_wall_ms - int(diag.get("received_at", 0) or 0)) if diag else 999999
                captured = int(diag.get("capture_frames", 0) or 0) if d_age < 5000 else 0
                sent = int(diag.get("sent_frames", 0) or 0) if d_age < 5000 else 0
                transport = str(diag.get("transport") or "").strip() if d_age < 5000 else ""
                err = str(diag.get("last_error") or "").strip() if d_age < 5000 else ""
                running = bool(diag.get("running", False)) if d_age < 5000 else False
                if not running:
                    reason = "MediaProjection servisi çalışmıyor"
                elif captured <= 0:
                    reason = "MediaProjection açık ama kare üretilmiyor"
                elif sent <= 0:
                    reason = "kare üretiliyor ama PC'ye gönderilemiyor"
                else:
                    reason = "stream kareleri PC'de görünmüyor"
                if transport:
                    reason += f" · {transport}"
                if err:
                    reason += f" · {err[:120]}"
                self._screen_fallback_reason = reason
                self.screen_status_var.set(
                    f"AUTO FALLBACK · {reason} · capture {captured} / sent {sent}"
                )
                self._request_screen_frame(quiet=True)
            elif received_at > 0 and age_ms > 1600:
                self.screen_status_var.set(
                    f"AKICI BAĞLANTI BEKLİYOR · son kare {age_ms/1000:.1f} sn önce"
                )
        except Exception as exc:
            self.screen_status_var.set("Akıcı ekran okunamadı: " + str(exc))
        self._schedule_screen_render(20)

    def _request_screen_frame(self, quiet=False):
        if self._screen_request_inflight:
            return
        self._screen_request_inflight = True
        try:
            row = queue_screen_snapshot(quality=45, max_width=720)
        except Exception as exc:
            self._screen_request_inflight = False
            if hasattr(self, "screen_status_var"):
                self.screen_status_var.set("Ekran komutu gönderilemedi: " + str(exc))
            return
        cid = str(row.get("id") or "")
        def worker():
            ack = wait_for_ack(cid, timeout=5.0, interval=0.08)
            def finish():
                self._screen_request_inflight = False
                if not self.win or not self.win.winfo_exists(): return
                if not ack or str(ack.get("status") or "").casefold() not in {"ok","done","success"}:
                    if not quiet:
                        self.screen_status_var.set(str(ack.get("detail") or "Telefon ekranı alınamadı"))
                    return
                self._render_screen_frame()
            if self.win and self.win.winfo_exists():
                self.win.after(0, finish)
        threading.Thread(target=worker, daemon=True, name="rip-phone-screen-frame").start()

    def _render_screen_frame(self, meta_override=None):
        if not hasattr(self, "screen_canvas"):
            return
        meta = dict(meta_override or get_phone_screen_frame())
        path = Path(str(meta.get("path") or ""))
        if not path.is_file():
            return
        try:
            # Read the JPEG fully and close the file before decoding it. The
            # persistent bridge is another Windows process writing the next
            # live frame; keeping a Pillow file handle open could block atomic
            # frame swaps and silently kill the stream.
            import io
            from PIL import Image, ImageTk
            jpeg = path.read_bytes()
            with Image.open(io.BytesIO(jpeg)) as source:
                img = source.convert("RGB")
            cw = max(100, int(self.screen_canvas.winfo_width()))
            ch = max(100, int(self.screen_canvas.winfo_height()))
            scale = min(cw / img.width, ch / img.height)
            dw = max(1, int(img.width * scale)); dh = max(1, int(img.height * scale))
            img = img.resize((dw, dh), Image.Resampling.BILINEAR)
            self._screen_photo = ImageTk.PhotoImage(img)
            self.screen_canvas.delete("all")
            ox = (cw - dw) // 2; oy = (ch - dh) // 2
            self.screen_canvas.create_image(ox, oy, anchor="nw", image=self._screen_photo)
            self._screen_meta = dict(meta)
            self._screen_meta.update({"draw_x": ox, "draw_y": oy, "draw_w": dw, "draw_h": dh})
            if not self._screen_live:
                age = max(0, int((time.time()*1000 - int(meta.get("received_at", 0) or 0))/1000))
                self.screen_status_var.set(f"TEK KARE · {dw}×{dh} · {age} sn önce")
        except Exception as exc:
            self.screen_status_var.set("Ekran çizilemedi: " + str(exc))

    def _screen_press(self, event):
        self._screen_drag_start = (event.x, event.y, time.time())

    def _screen_release(self, event):
        start = self._screen_drag_start
        self._screen_drag_start = None
        if not start or not self._screen_meta:
            return
        sx, sy, st = start
        dx, dy = event.x - sx, event.y - sy
        ox = float(self._screen_meta.get("draw_x", 0)); oy = float(self._screen_meta.get("draw_y", 0))
        dw = float(self._screen_meta.get("draw_w", 1)); dh = float(self._screen_meta.get("draw_h", 1))
        sw = float(self._screen_meta.get("screen_width", 0)); sh = float(self._screen_meta.get("screen_height", 0))
        if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
            return
        def map_pt(x, y):
            rx = (x - ox) / dw; ry = (y - oy) / dh
            if rx < 0 or rx > 1 or ry < 0 or ry > 1: return None
            return max(0, min(sw-1, rx*sw)), max(0, min(sh-1, ry*sh))
        a = map_pt(sx, sy); b = map_pt(event.x, event.y)
        if a is None or b is None: return
        if abs(dx) + abs(dy) < 18:
            self._queue(queue_screen_tap(b[0], b[1]))
        else:
            duration = int(max(120, min(900, (time.time()-st)*1000)))
            self._queue(queue_screen_swipe(a[0], a[1], b[0], b[1], duration))

    def _build_clipboard(self, page):
        self._title(page,"PANO SENKRONU","PC ↔ telefon metin panosu.").pack(fill="x",padx=12,pady=(16,8))
        card=self._card(page); card.pack(fill="both",expand=True,padx=12,pady=6)
        tk.Label(card,text="PC'DEN TELEFONA",bg=CARD,fg=CYAN,font=("Segoe UI",10,"bold")).pack(anchor="w",padx=16,pady=(16,6))
        entry=tk.Entry(card,textvariable=self.clip_var,bg=CARD2,fg=TEXT,insertbackground=TEXT,relief="flat",font=("Segoe UI",10))
        entry.pack(fill="x",padx=16,ipady=9)
        btns=tk.Frame(card,bg=CARD); btns.pack(fill="x",padx=16,pady=10)
        self._button(btns,"PC PANOSUNU AL",self._load_pc_clipboard,CYAN).pack(side="left")
        self._button(btns,"→ TELEFONA GÖNDER",self._send_clipboard_to_phone,MINT).pack(side="right")
        tk.Label(card,text="TELEFONDAN GELEN SON PANO",bg=CARD,fg=PURPLE,font=("Segoe UI",10,"bold")).pack(anchor="w",padx=16,pady=(18,6))
        self.phone_clip_text=tk.Text(card,height=9,bg=CARD2,fg=TEXT,insertbackground=TEXT,relief="flat",wrap="word",font=("Segoe UI",10))
        self.phone_clip_text.pack(fill="both",expand=True,padx=16,pady=(0,16))

    def _build_connection(self, page):
        self._title(page,"BAĞLANTI","LAN / Tailscale bridge rotası ve son canlı durum.").pack(fill="x",padx=12,pady=(16,8))
        card=self._card(page); card.pack(fill="x",padx=12,pady=6)
        self.connection_text=tk.Label(card,text="—",justify="left",anchor="nw",bg=CARD,fg=TEXT,font=("Consolas",10),padx=16,pady=16)
        self.connection_text.pack(fill="x")
        btns=tk.Frame(page,bg=BG); btns.pack(fill="x",padx=12,pady=10)
        self._button(btns,"↻ DURUMU YENİLE",lambda:self.refresh(request_inventory=True),MINT).pack(side="left")
        self._button(btns,"BİLDİRİM İZİN EKRANI",lambda:self._queue(queue_open_permission("notification")),CYAN).pack(side="left",padx=8)

    def _build_permissions(self, page):
        self._title(page,"YETKİ MERKEZİ","İzinler telefondan elle verilir; PC buradan doğru Android ayar sayfasını açtırabilir.").pack(fill="x",padx=12,pady=(16,8))
        card=self._card(page); card.pack(fill="x",padx=12,pady=6)
        items=[
            ("notification","BİLDİRİM ERİŞİMİ","Bildirimleri PC HUD'a aktarır."),
            ("accessibility","ACCESSIBILITY","Geri / ana ekran / son uygulamalar / kilit gibi kontroller."),
            ("overlay","OVERLAY","Gelecekte telefon üstü hızlı kontrol katmanı için."),
            ("battery","PİL OPTİMİZASYONU","Bridge'in arka planda daha kararlı kalması için."),
            ("usage","KULLANIM ERİŞİMİ","Son kullanım tarihini ve kullanılmayan uygulama filtresini açar."),
            ("write_settings","SİSTEM AYARLARI","Parlaklık, ekran süresi ve otomatik döndürme için."),
            ("dnd","RAHATSIZ ETMEYİN","PC'den DND aç/kapat için."),
            ("install_unknown","APK YÜKLEME","PC'den gönderilen APK'nın kurulum ekranını açmak için."),
            ("camera","FENER / KAMERA","El fenerini PC'den açıp kapatmak için."),
            ("answer_calls","ARAMA KONTROLÜ","Normal telefon aramasını PC'den cevapla/reddet için."),
            ("location","KONUM","Telefonun konumunu PC'den isteyebilmek için."),
        ]
        for key,title,sub in items:
            row=tk.Frame(card,bg=CARD); row.pack(fill="x",padx=16,pady=10)
            left=tk.Frame(row,bg=CARD); left.pack(side="left",fill="x",expand=True)
            tk.Label(left,text=title,bg=CARD,fg=TEXT,font=("Segoe UI",10,"bold")).pack(anchor="w")
            tk.Label(left,text=sub,bg=CARD,fg=DIM,font=("Segoe UI",9)).pack(anchor="w")
            var=tk.StringVar(value="○ BİLİNMİYOR"); self.permission_vars[key]=var
            tk.Label(row,textvariable=var,bg=CARD,fg=MINT,font=("Segoe UI",9,"bold"),width=12).pack(side="left",padx=8)
            self._button(row,"TELEFONDA AÇ",lambda k=key:self._queue(queue_open_permission(k)),CYAN).pack(side="right")
        tk.Label(page,text="Not: Android, uygulama kaldırma ve bazı özel işlemlerde telefonda ayrıca onay gösterebilir.",bg=BG,fg=GOLD,font=("Segoe UI",9)).pack(anchor="w",padx=12,pady=10)

    def _queue(self, row: dict, on_done=None):
        cid=str(row.get("id") or "")
        self.log_callback(f"📱 Telefon komutu kuyruğa alındı: {row.get('type','device_action')}")
        def worker():
            ack=wait_for_ack(cid,timeout=14)
            def finish():
                if ack:
                    status=str(ack.get("status") or "")
                    detail=str(ack.get("detail") or "")
                    self.log_callback(("✓ " if status in {"ok","done","success"} else "✕ ")+"Telefon: "+detail)
                    if on_done: on_done(ack)
                    self.refresh()
                else:
                    self.log_callback("⚠ Telefon komutu ACK beklerken zaman aşımına uğradı.")
            if self.win and self.win.winfo_exists():
                self.win.after(0,finish)
        threading.Thread(target=worker,daemon=True,name="rip-phone-center-command").start()
        return row

    def refresh(self, request_inventory=False):
        if request_inventory:
            try:
                self._queue(queue_inventory_refresh())
            except Exception as exc:
                self.log_callback("Telefon tarama hatası: "+str(exc))
        status=get_phone_status_record()
        inv=get_phone_inventory()
        now=time.time()
        stamp=int(status.get("received_at",0) or 0)
        age=max(0,int(now-stamp/1000)) if stamp else None
        online=age is not None and age <= 75
        self.status_var.set(("● BAĞLI" if online else "○ OFFLINE")+"  "+str(status.get("device") or "Android"))
        self.meta_var.set((f"Son veri {age} sn önce" if age is not None else "Henüz durum paketi yok")+" • "+str(status.get("bridge_service") or "—")+" • "+(str(status.get("bridge_latency_ms"))+" ms" if int(status.get("bridge_latency_ms",-1) or -1)>=0 else "—"))
        battery=status.get("battery_percent",-1)
        free=status.get("storage_free_bytes",-1); total=status.get("storage_total_bytes",-1)
        self.general_text.configure(text=(
            f"Cihaz                 {status.get('device','—')}  • Android {status.get('android_version','—')}\n"
            f"Pil                   %{battery if battery is not None and int(battery)>=0 else '?'}{'  •  Şarjda' if status.get('charging') else ''}\n"
            f"Pil sağlığı           {status.get('battery_health','—')}  • {status.get('battery_voltage_mv','—')} mV\n"
            f"Sıcaklık              {status.get('battery_temp_c','—')} °C\n"
            f"Ağ                    {status.get('wifi','—')}\n"
            f"Ses modu              {status.get('ringer_mode','—')}\n"
            f"Medya sesi            %{status.get('music_volume_percent','—')}\n"
            f"Depolama              {_fmt_bytes(free)} boş / {_fmt_bytes(total)} toplam\n"
            f"Bridge                V{status.get('bridge_version','—')} • {status.get('bridge_service','—')}"
        ))
        try:
            volumes=inv.get("volumes") if isinstance(inv.get("volumes"),dict) else status.get("volumes",{})
            row=volumes.get(self.volume_stream_var.get(),{}) if isinstance(volumes,dict) else {}
            raw=row.get("percent", inv.get("music_volume_percent", status.get("music_volume_percent",50)))
            vol=int(raw if raw is not None else 50)
            level=int(row.get("level", inv.get("music_volume_level",status.get("music_volume_level",-1))) or -1)
            vmax=int(row.get("max",inv.get("music_volume_max",status.get("music_volume_max",-1))) or -1)
            now_vol=time.time()
            if 0 <= vol <= 100 and now_vol >= self._volume_user_active_until and now_vol >= self._volume_pending_until:
                self.volume_var.set(vol)
            if 0 <= vol <= 100 and now_vol >= self._volume_pending_until:
                suffix=f"  • kademe {level}/{vmax}" if level>=0 and vmax>0 else ""
                self.volume_text_var.set(f"{self._volume_label()} SESİ  %{vol}{suffix}")
        except Exception:
            pass
        try:
            bp=int(inv.get("brightness_percent",status.get("brightness_percent",50)) or 50)
            if 1 <= bp <= 100: self.brightness_var.set(bp); self.brightness_text_var.set(f"PARLAKLIK  %{bp}")
            timeout_ms=int(inv.get("screen_timeout_ms",status.get("screen_timeout_ms",30000)) or 30000)
            timeout_map={15000:"15 sn",30000:"30 sn",60000:"1 dk",120000:"2 dk",300000:"5 dk",600000:"10 dk",1800000:"30 dk"}
            self.timeout_var.set(timeout_map.get(timeout_ms, f"{max(15,timeout_ms//1000)} sn"))
            dnd=int(inv.get("dnd_filter",status.get("dnd_filter",-1)) or -1)
            self.device_state_var.set(
                f"Otomatik parlaklık: {'AÇIK' if inv.get('brightness_auto',status.get('brightness_auto')) else 'KAPALI'}   •   "
                f"Döndürme: {'AÇIK' if inv.get('auto_rotate',status.get('auto_rotate')) else 'KAPALI'}   •   "
                f"DND: {'KAPALI' if dnd in {-1,1} else 'AÇIK'}")
        except Exception: pass
        self._apps_cache=list(inv.get("apps") or [])
        self._files_cache=list(inv.get("downloads") or [])
        self._render_apps(); self._render_files(); self._render_notifications(); self._render_clipboard(); self._render_connection(status); self._render_permissions(status,inv)

    def _schedule_refresh(self):
        if not self.win or not self.win.winfo_exists(): return
        self._refresh_job=self.win.after(2200,self._periodic)

    def _periodic(self):
        self.refresh(False); self._schedule_refresh()

    def _fmt_epoch_ms(self, value, with_time=False):
        try:
            v=int(value or 0)
            if v <= 0: return "—"
            return time.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y", time.localtime(v/1000))
        except Exception: return "—"

    def _visible_apps(self):
        q=self.app_search_var.get().strip().casefold(); filt=self.app_filter_var.get(); now_ms=int(time.time()*1000)
        rows=[]
        for app in self._apps_cache:
            label=str(app.get("label") or ""); pkg=str(app.get("package") or "")
            if q and q not in label.casefold() and q not in pkg.casefold(): continue
            system=bool(app.get("system")); enabled=bool(app.get("enabled",True)); launch=bool(app.get("launchable"))
            last=int(app.get("last_used_ms",0) or 0)
            if filt=="KULLANICI" and system: continue
            if filt=="SİSTEM" and not system: continue
            if filt=="AÇILABİLEN" and not launch: continue
            if filt=="DEVRE DIŞI" and enabled: continue
            if filt=="30+ GÜN KULLANILMAYAN":
                if system or last<=0 or now_ms-last < 30*86400000: continue
            rows.append(app)
        sort=self.app_sort_var.get()
        if sort=="APK BOYUTU": rows.sort(key=lambda a:int(a.get("apk_bytes",0) or 0),reverse=True)
        elif sort=="SON KULLANIM": rows.sort(key=lambda a:int(a.get("last_used_ms",0) or 0),reverse=True)
        elif sort=="KURULUM TARİHİ": rows.sort(key=lambda a:int(a.get("first_install_ms",0) or 0),reverse=True)
        elif sort=="GÜNCELLEME": rows.sort(key=lambda a:int(a.get("last_update_ms",0) or 0),reverse=True)
        else: rows.sort(key=lambda a:(str(a.get("label") or "").casefold(),str(a.get("package") or "")))
        return rows

    def _render_apps(self):
        if not hasattr(self,"apps_tree"): return
        selected_pkgs={a.get("package") for a in self._selected_apps()} if self.apps_tree.get_children() else set()
        self.apps_tree.delete(*self.apps_tree.get_children())
        visible=self._visible_apps(); reselect=[]
        for app in visible:
            label=str(app.get("label") or ""); pkg=str(app.get("package") or "")
            kind="Sistem" if app.get("system") else "Kullanıcı"
            if not app.get("enabled",True): kind+=" • kapalı"
            iid=self.apps_tree.insert("","end",values=(label,kind,_fmt_bytes(app.get("apk_bytes",0)),self._fmt_epoch_ms(app.get("last_used_ms",0),True),self._fmt_epoch_ms(app.get("first_install_ms",0)),pkg))
            if pkg in selected_pkgs: reselect.append(iid)
        if reselect:
            try:self.apps_tree.selection_set(reselect)
            except Exception:pass
        self.app_count_var.set(f"{len(visible)} gösteriliyor • toplam {len(self._apps_cache)}")

    def _selected_apps(self):
        if not hasattr(self,"apps_tree"): return []
        out=[]
        for iid in self.apps_tree.selection():
            vals=self.apps_tree.item(iid,"values")
            pkg=str(vals[5]) if len(vals)>=6 else ""
            for app in self._apps_cache:
                if str(app.get("package"))==pkg: out.append(app); break
        return out

    def _selected_app(self):
        rows=self._selected_apps(); return rows[0] if rows else None

    def _select_visible_apps(self):
        try:self.apps_tree.selection_set(self.apps_tree.get_children())
        except Exception:pass

    def _open_selected_app(self):
        app=self._selected_app()
        if not app: return
        if not app.get("launchable",True):
            messagebox.showinfo("RIP Phone","Bu uygulamanın açılabilir ana ekranı yok; AYARLAR'ı kullan.",parent=self.win); return
        self._queue(queue_open_app(str(app.get("package") or "")))

    def _open_app_settings(self):
        app=self._selected_app()
        if not app: return
        self._queue(queue_app_settings(str(app.get("package") or "")))

    def _batch_uninstall_selected(self):
        apps=[a for a in self._selected_apps() if str(a.get("package") or "")!="com.ripperinio.mobile"]
        if not apps: return
        user=[a for a in apps if not a.get("system")]
        system=[a for a in apps if a.get("system")]
        chosen=user if user else apps
        extra=f"\n\n{len(system)} sistem uygulaması toplu işlem dışında bırakılacak." if system and user else ""
        if not messagebox.askyesno("RIP Phone",f"{len(chosen)} uygulamanın Android kaldırma ekranı SIRAYLA açılsın mı? Her uygulamayı telefonda yine sen onaylayacaksın.{extra}",parent=self.win): return
        def worker():
            total=len(chosen)
            for idx,app in enumerate(chosen,1):
                pkg=str(app.get("package") or ""); label=str(app.get("label") or pkg)
                self.log_callback(f"⌫ [{idx}/{total}] Kaldırma bekleniyor: {label}")
                cmd=queue_uninstall_app(pkg); ack=wait_for_ack(str(cmd.get("id") or ""),15)
                if not ack or str(ack.get("status") or "") not in {"ok","done","success"}:
                    self.log_callback(f"⚠ {label}: kaldırma ekranı açılamadı, geçiliyor."); continue
                removed=False; deadline=time.time()+25
                while time.time()<deadline:
                    time.sleep(1.5)
                    try:
                        r=queue_package_check(pkg); chk=wait_for_ack(str(r.get("id") or ""),6)
                        if str(chk.get("detail") or "").strip()=="PACKAGE_MISSING": removed=True; break
                    except Exception: pass
                self.log_callback(("✓ Kaldırıldı: " if removed else "↷ Onay verilmedi/iptal edildi, geçildi: ")+label)
            if self.win and self.win.winfo_exists(): self.win.after(0,lambda:self.refresh(request_inventory=True))
        threading.Thread(target=worker,daemon=True,name="rip-batch-uninstall").start()

    def _render_files(self):
        if not hasattr(self,"files_tree"): return
        self.files_tree.delete(*self.files_tree.get_children())
        for row in self._files_cache:
            ts=row.get("modified",0)
            try: date=time.strftime("%d.%m.%Y %H:%M",time.localtime(float(ts))) if ts else "—"
            except Exception: date="—"
            iid=self.files_tree.insert("","end",values=(row.get("name","—"),_fmt_bytes(row.get("size",-1)),date))
            self.files_tree.set(iid,"name",row.get("name","—"))

    def _selected_file(self):
        sel=self.files_tree.selection()
        if not sel: return None
        vals=self.files_tree.item(sel[0],"values")
        name=str(vals[0]) if vals else ""
        for row in self._files_cache:
            if str(row.get("name"))==name: return row
        return None

    def _send_file_to_phone(self):
        path=filedialog.askopenfilename(parent=self.win,title="Telefona gönderilecek dosyayı seç")
        if not path:return
        try:
            staged=stage_file_for_phone(path)
            cid=str(staged.get("command_id") or "")
            self.log_callback("⇡ Telefona gönderiliyor: "+str(staged.get("filename") or path))
            def worker():
                ack=wait_for_ack(cid,timeout=90)
                def done():
                    if ack:self.log_callback("📱 "+str(ack.get("detail") or ack.get("status")))
                    else:self.log_callback("⚠ Dosya aktarımı telefon ACK zaman aşımı.")
                    self.refresh(request_inventory=True)
                if self.win and self.win.winfo_exists():self.win.after(0,done)
            threading.Thread(target=worker,daemon=True,name="rip-phone-file-send").start()
        except Exception as exc:
            messagebox.showerror("RIP Phone",str(exc),parent=self.win)

    def _download_selected_phone_file(self):
        row=self._selected_file()
        if not row: return
        uri=str(row.get("uri") or "")
        if not uri:
            messagebox.showerror("RIP Phone","Bu dosya için indirilebilir URI yok.",parent=self.win); return
        self._queue(queue_phone_file_to_pc(uri,str(row.get("name") or "phone-file")))

    def _rename_selected_phone_file(self):
        row=self._selected_file()
        if not row:return
        uri=str(row.get("uri") or ""); old=str(row.get("name") or "")
        name=simpledialog.askstring("RIP Phone","Yeni dosya adı:",initialvalue=old,parent=self.win)
        if not name:return
        self._queue(queue_rename_phone_file(uri,name),on_done=lambda _a:self.refresh(request_inventory=True))

    def _delete_selected_phone_file(self):
        row=self._selected_file()
        if not row:return
        if not messagebox.askyesno("RIP Phone",f"Telefondan kalıcı olarak silinsin mi?\n\n{row.get('name','')}",parent=self.win):return
        self._queue(queue_delete_phone_file(str(row.get("uri") or "")),on_done=lambda _a:self.refresh(request_inventory=True))

    def _install_selected_apk(self):
        row=self._selected_file()
        if not row:return
        name=str(row.get("name") or "")
        if not name.casefold().endswith(".apk"):
            messagebox.showinfo("RIP Phone","Seçili dosya APK değil.",parent=self.win);return
        if not messagebox.askyesno("RIP Phone",f"Android kurulum ekranı açılsın mı?\n\n{name}",parent=self.win):return
        self._queue(queue_install_apk(str(row.get("uri") or "")))

    def _render_notifications(self):
        if not hasattr(self,"notif_tree"): return
        rows=_read_json(NOTIFY_STORE,[])
        if not isinstance(rows,list): rows=[]
        self.notif_tree.delete(*self.notif_tree.get_children()); self._notif_rows={}
        for r in rows[-100:][::-1]:
            if str(r.get("kind")) not in {"notification","call"}: continue
            ts=int(r.get("received_at",0) or 0); clock=time.strftime("%H:%M:%S",time.localtime(ts/1000)) if ts else "—"
            iid=self.notif_tree.insert("","end",values=(r.get("app","Telefon"),r.get("title",""),r.get("text",""),clock))
            self._notif_rows[iid]=r

    def _selected_notification(self):
        sel=self.notif_tree.selection() if hasattr(self,"notif_tree") else ()
        return self._notif_rows.get(sel[0]) if sel else None

    def _dismiss_selected_notification(self):
        row=self._selected_notification()
        if not row:return
        key=str(row.get("notification_key") or "")
        if not key: messagebox.showinfo("RIP Phone","Bu eski bildirimde canlı Android anahtarı yok.",parent=self.win);return
        self._queue(queue_dismiss_notification(key))

    def _open_selected_notification(self):
        row=self._selected_notification()
        if not row:return
        key=str(row.get("notification_key") or "")
        if not key: messagebox.showinfo("RIP Phone","Bu eski bildirim telefonda açılamaz.",parent=self.win);return
        self._queue(queue_open_notification(key))

    def _reply_selected_notification(self):
        row=self._selected_notification()
        if not row:return
        if not row.get("replyable"):
            messagebox.showinfo("RIP Phone","Bu bildirim Android hızlı yanıt aksiyonu sunmuyor.",parent=self.win);return
        key=str(row.get("notification_key") or "")
        if not key:return
        text=simpledialog.askstring("RIP Phone","Gönderilecek yanıt:",parent=self.win)
        if not text:return
        self._queue(queue_reply_notification(key,text))

    def _load_pc_clipboard(self):
        try: self.clip_var.set(self.root.clipboard_get())
        except Exception: self.clip_var.set("")

    def _send_clipboard_to_phone(self):
        text=self.clip_var.get()
        if not text:
            self._load_pc_clipboard(); text=self.clip_var.get()
        if not text: return
        try: self._queue(queue_clipboard_to_phone(text))
        except Exception as exc: self.log_callback("Pano gönderme hatası: "+str(exc))

    def _render_clipboard(self):
        if not hasattr(self,"phone_clip_text"): return
        row=get_phone_clipboard(); text=str(row.get("text") or "")
        try:
            self.phone_clip_text.delete("1.0","end"); self.phone_clip_text.insert("1.0",text)
        except Exception: pass

    def _render_connection(self,status):
        if not hasattr(self,"connection_text"):return
        stamp=int(status.get("received_at",0) or 0)
        age=max(0,int(time.time()-stamp/1000)) if stamp else None
        self.connection_text.configure(text=(
            f"DEVICE       {status.get('device','—')}\n"
            f"ROUTE        {status.get('bridge_service','—')}\n"
            f"LATENCY      {status.get('bridge_latency_ms','—')} ms\n"
            f"REMOTE IP    {status.get('remote_ip','—')}\n"
            f"NETWORK      {status.get('wifi','—')}\n"
            f"BRIDGE       V{status.get('bridge_version','—')}\n"
            f"LAST SYNC    {str(age)+' sn önce' if age is not None else '—'}"
        ))

    def _render_permissions(self,status,inv):
        perms=dict(inv.get("permissions") or {})
        mapping={
            "notification": bool(perms.get("notification",status.get("notification_access",False))),
            "accessibility": bool(perms.get("accessibility",status.get("accessibility_access",False))),
            "overlay": bool(perms.get("overlay",status.get("overlay_access",False))),
            "battery": bool(perms.get("battery_ignored",status.get("battery_optimization_ignored",False))),
            "usage": bool(perms.get("usage",status.get("usage_access",False))),
            "write_settings": bool(perms.get("write_settings",status.get("write_settings_access",False))),
            "dnd": bool(perms.get("dnd",status.get("dnd_access",False))),
            "install_unknown": bool(perms.get("install_unknown",status.get("install_unknown_access",False))),
            "camera": bool(perms.get("camera",status.get("camera_access",False))),
            "answer_calls": bool(perms.get("answer_calls",status.get("answer_calls_access",False))),
            "location": bool(perms.get("location",status.get("location_access",False))),
        }
        for key,val in mapping.items():
            var=self.permission_vars.get(key)
            if var: var.set("✓ AÇIK" if val else "○ KAPALI")


class PhoneEdgeDrawer:
    """Always-on-top 8px left-edge handle that expands to quick phone controls."""
    def __init__(self, root: tk.Misc, center: PhoneControlCenter, log_callback=None):
        self.root=root; self.center=center; self.log_callback=log_callback or (lambda _m:None)
        self.width=326; self.collapsed=6; self.height=max(560, self.root.winfo_screenheight()); self.pinned=False; self._collapse_job=None; self._expand_job=None
        self.win=tk.Toplevel(root); self.win.overrideredirect(True); self.win.configure(bg=BG)
        try: self.win.attributes("-topmost",True); self.win.attributes("-alpha",0.97)
        except Exception: pass
        self._position(self.collapsed)
        self.win.bind("<Enter>",lambda _e:self._schedule_expand())
        self.win.bind("<Leave>",lambda _e:self._schedule_collapse())
        self._build()
        self._tick()

    def _position(self,w):
        sh=self.root.winfo_screenheight(); self.height=max(560, sh)
        self.win.geometry(f"{w}x{self.height}+0+0")

    def _build(self):
        handle=tk.Frame(self.win,bg=CYAN,width=8); handle.pack(side="left",fill="y")
        body=tk.Frame(self.win,bg=CARD); body.pack(side="left",fill="both",expand=True)
        top=tk.Frame(body,bg=CARD); top.pack(fill="x",padx=14,pady=(16,8))
        tk.Label(top,text="RIP // PHONE",bg=CARD,fg=TEXT,font=("Segoe UI",15,"bold")).pack(side="left")
        self.pin_btn=self._btn(top,"PIN",self._toggle_pin,PURPLE); self.pin_btn.pack(side="right")
        self.state=tk.Label(body,text="○ OFFLINE",bg=CARD,fg=RED,font=("Segoe UI",10,"bold")); self.state.pack(anchor="w",padx=14)
        self.meta=tk.Label(body,text="Telefon bekleniyor",bg=CARD,fg=DIM,font=("Segoe UI",9),justify="left"); self.meta.pack(anchor="w",padx=14,pady=(2,12))
        quick=tk.Frame(body,bg=CARD); quick.pack(fill="x",padx=12)
        for text,action,col in [("⌂ ANA","home",MINT),("← GERİ","back",CYAN),("▦ SON","recents",PURPLE),("▣ KİLİT","lock_screen",GOLD)]:
            self._btn(quick,text,lambda a=action:self._queue(queue_global_action(a)),col).pack(side="left",fill="x",expand=True,padx=2)
        self._btn(body,"▤  BİLDİRİMLER",lambda:self.center.open("notifications"),CYAN).pack(fill="x",padx=14,pady=(14,5))
        self._btn(body,"▦  UYGULAMALAR",lambda:self.center.open("apps"),MINT).pack(fill="x",padx=14,pady=5)
        self._btn(body,"⚙  CİHAZ KONTROLLERİ",lambda:self.center.open("device"),CYAN).pack(fill="x",padx=14,pady=5)
        self._btn(body,"⇄  PANO PC → TELEFON",self._clipboard_to_phone,PURPLE).pack(fill="x",padx=14,pady=5)
        self._btn(body,"◉  TELEFONU TİTREŞTİR",lambda:self._queue(queue_vibrate()),GOLD).pack(fill="x",padx=14,pady=5)
        self._btn(body,"🔔  ZİL ÇALDIR",lambda:self._queue(queue_find_phone()),GOLD).pack(fill="x",padx=14,pady=5)
        self._btn(body,"⌖  KONUMU BUL",self._locate_phone,MINT).pack(fill="x",padx=14,pady=5)
        tk.Frame(body,bg=LINE,height=1).pack(fill="x",padx=14,pady=12)
        tk.Label(body,text="SON BİLDİRİM",bg=CARD,fg=DIM,font=("Segoe UI",8,"bold")).pack(anchor="w",padx=14)
        self.latest=tk.Label(body,text="—",bg=CARD,fg=TEXT,font=("Segoe UI",9),wraplength=285,justify="left"); self.latest.pack(anchor="w",padx=14,pady=(4,12))
        self._btn(body,"TELEFON MERKEZİNİ AÇ  →",self.center.open,MINT).pack(fill="x",padx=14,pady=(4,14))

    def _locate_phone(self):
        try:
            self.center.open("general")
            self.center._request_phone_location()
        except Exception as exc:
            self.log_callback("Telefon konumu açılamadı: "+str(exc))

    def _btn(self,parent,text,cmd,color):
        return tk.Button(parent,text=text,command=cmd,bg=CARD2,fg=color,activebackground="#192C44",activeforeground=TEXT,
                         relief="flat",bd=0,padx=8,pady=7,cursor="hand2",font=("Segoe UI",8,"bold"))

    def _schedule_expand(self):
        if self._collapse_job:
            try:self.win.after_cancel(self._collapse_job)
            except Exception:pass
            self._collapse_job=None
        if self.pinned:
            self.expand(); return
        if self._expand_job:
            try:self.win.after_cancel(self._expand_job)
            except Exception:pass
        self._expand_job=self.win.after(320,self.expand)

    def expand(self):
        self._expand_job=None
        if self._collapse_job:
            try:self.win.after_cancel(self._collapse_job)
            except Exception:pass
            self._collapse_job=None
        self._position(self.width)

    def collapse(self):
        if not self.pinned:self._position(self.collapsed)

    def _schedule_collapse(self):
        if self._expand_job:
            try:self.win.after_cancel(self._expand_job)
            except Exception:pass
            self._expand_job=None
        if self.pinned:return
        self._collapse_job=self.win.after(900,self.collapse)

    def _toggle_pin(self):
        self.pinned=not self.pinned
        self.pin_btn.configure(text="PIN ✓" if self.pinned else "PIN",fg=MINT if self.pinned else PURPLE)
        if self.pinned:self.expand()

    def _queue(self,row):
        cid=str(row.get("id") or "")
        def worker():
            ack=wait_for_ack(cid,10)
            if ack:
                try:self.root.after(0,lambda a=dict(ack):self.log_callback("📱 "+str(a.get("detail") or a.get("status"))))
                except Exception:pass
        threading.Thread(target=worker,daemon=True).start()

    def _clipboard_to_phone(self):
        try:text=self.root.clipboard_get()
        except Exception:text=""
        if not text:
            self.log_callback("Telefon pano gönderimi: PC panosu boş."); return
        try:self._queue(queue_clipboard_to_phone(text))
        except Exception as exc:self.log_callback("Telefon pano gönderimi hata: "+str(exc))

    def _tick(self):
        try:
            status=get_phone_status_record(); stamp=int(status.get("received_at",0) or 0)
            age=max(0,int(time.time()-stamp/1000)) if stamp else 99999; online=age<=75
            self.state.configure(text=("● BAĞLI" if online else "○ OFFLINE")+" • "+str(status.get("device") or "Android"),fg=MINT if online else RED)
            self.meta.configure(text=(f"Pil %{status.get('battery_percent','?')} • {status.get('bridge_service','—')} • {status.get('bridge_latency_ms','—')} ms • {age} sn" if stamp else "Telefon bekleniyor"))
            rows=_read_json(NOTIFY_STORE,[]); row=rows[-1] if isinstance(rows,list) and rows else {}
            self.latest.configure(text=(str(row.get("app") or "Telefon")+"\n"+str(row.get("title") or row.get("text") or "—"))[:220])
        except Exception:pass
        try:self.win.after(1800,self._tick)
        except Exception:pass
