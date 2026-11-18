from __future__ import annotations

import tkinter as tk

from hud_window_chrome import (
    HudWindowShell, BG, PANEL, PANEL_2, LINE, CYAN, CYAN_2,
    TEXT, TEXT_2, DIM, GREEN, RED,
)

GOLD = "#ffc86b"
PURPLE = "#8a7dff"
VOICES = ["Charon", "Puck", "Aoede", "Kore", "Fenrir", "Leda", "Orus", "Zephyr"]


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


class ReferenceSettingsWindow:
    """Reference-HUD settings surface for the legacy V6 runtime.

    It reuses the existing UI callbacks instead of creating a second settings
    backend, so voice, SFX, startup and integration behavior stays identical.
    """

    def __init__(self, root: tk.Misc, ui):
        self.root = root
        self.ui = ui
        self.win = None
        self.shell = None
        self._fx_var = tk.IntVar(value=20)
        self._voice_var = tk.StringVar(value=str(getattr(ui, "_current_voice", "Charon") or "Charon"))
        self._mode_var = tk.StringVar(value=str(getattr(ui, "voice_input_mode", "continuous") or "continuous"))
        self._status_var = tk.StringVar(value="Hazır")

    def open(self):
        if self.win is not None:
            try:
                if self.win.winfo_exists():
                    self.win.deiconify()
                    self.win.lift()
                    self.win.focus_force()
                    self.refresh()
                    return
            except Exception:
                pass

        self.win = tk.Toplevel(self.root)
        self.win.title("RIP // AYARLAR")
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.shell = HudWindowShell(
            self.win,
            title="AYARLAR",
            subtitle="VOICE // INPUT // STARTUP // INTEGRATIONS // LOCAL CONFIG",
            on_close=self.close,
            width=1020,
            height=700,
            min_width=880,
            min_height=600,
        )
        body = self.shell.content

        banner = tk.Frame(body, bg=BG)
        banner.pack(fill="x", padx=22, pady=(18, 8))
        left = tk.Frame(banner, bg=BG)
        left.pack(side="left")
        tk.Label(left, text="RIP // AYARLAR", bg=BG, fg=TEXT,
                 font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(left, text="HUD, SES, BAŞLANGIÇ VE ENTEGRASYONLAR",
                 bg=BG, fg=DIM, font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(2, 0))
        badge = tk.Label(
            banner, text="●  LOCAL CONFIG", bg="#05212a", fg=GREEN,
            font=("Segoe UI", 8, "bold"), padx=12, pady=7,
            highlightbackground="#0f6473", highlightthickness=1,
        )
        badge.pack(side="right", pady=4)

        columns = tk.Frame(body, bg=BG)
        columns.pack(fill="both", expand=True, padx=18, pady=(4, 14))
        columns.grid_columnconfigure(0, weight=1, uniform="settings")
        columns.grid_columnconfigure(1, weight=1, uniform="settings")
        columns.grid_rowconfigure(0, weight=1)

        left_col = tk.Frame(columns, bg=BG)
        right_col = tk.Frame(columns, bg=BG)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        right_col.grid(row=0, column=1, sticky="nsew", padx=(7, 0))

        self._build_audio(left_col)
        self._build_input(left_col)
        self._build_system(right_col)
        self._build_integrations(right_col)

        status = tk.Frame(body, bg="#041219", highlightbackground=LINE, highlightthickness=1)
        status.pack(fill="x", padx=18, pady=(0, 14))
        tk.Label(status, text="DURUM", bg="#041219", fg=CYAN,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(12, 10), pady=9)
        tk.Label(status, textvariable=self._status_var, bg="#041219", fg=TEXT_2,
                 font=("Segoe UI", 8)).pack(side="left", pady=9)

        self.win.bind("<Escape>", lambda _e: self.close(), add="+")
        self.refresh()

    def close(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
        self.win = None
        self.shell = None

    def _card(self, parent, title, subtitle=""):
        card = tk.Frame(parent, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        head = tk.Frame(card, bg=PANEL)
        head.pack(fill="x", padx=14, pady=(12, 8))
        tk.Frame(head, bg=CYAN, width=3, height=22).pack(side="left", padx=(0, 9))
        labels = tk.Frame(head, bg=PANEL)
        labels.pack(side="left", fill="x", expand=True)
        tk.Label(labels, text=title, bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(labels, text=subtitle, bg=PANEL, fg=DIM,
                     font=("Segoe UI", 7)).pack(anchor="w", pady=(2, 0))
        return card

    def _btn(self, parent, text, command, color=CYAN):
        b = tk.Button(
            parent, text=text, command=command,
            bg=PANEL_2, fg=color, activebackground="#0a2630",
            activeforeground=TEXT, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=LINE,
            padx=11, pady=8, cursor="hand2",
            font=("Segoe UI", 8, "bold"),
        )
        b.bind("<Enter>", lambda _e, w=b: w.configure(bg="#0b2731"))
        b.bind("<Leave>", lambda _e, w=b: w.configure(bg=PANEL_2))
        return b

    def _build_audio(self, parent):
        card = self._card(parent, "SES & VOICE", "Gemini voice ve HUD ses efektleri")
        card.pack(fill="x", pady=(0, 12))

        row = tk.Frame(card, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(2, 10))
        tk.Label(row, text="VOICE", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        menu = tk.OptionMenu(row, self._voice_var, *VOICES, command=self._set_voice)
        menu.configure(
            bg=PANEL_2, fg=CYAN, activebackground="#0a2630",
            activeforeground=TEXT, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=LINE,
            font=("Segoe UI", 9), width=12,
        )
        try:
            menu["menu"].configure(bg=PANEL_2, fg=TEXT, activebackground="#0a2630")
        except Exception:
            pass
        menu.pack(side="right")

        sfx_row = tk.Frame(card, bg=PANEL)
        sfx_row.pack(fill="x", padx=14, pady=(0, 10))
        self.sfx_label = tk.Label(sfx_row, text="", bg=PANEL, fg=TEXT_2,
                                  font=("Segoe UI", 8, "bold"))
        self.sfx_label.pack(side="left")
        self.sfx_btn = self._btn(sfx_row, "SFX AÇ / KAPAT", self._toggle_sfx, GREEN)
        self.sfx_btn.pack(side="right")

        fx_row = tk.Frame(card, bg=PANEL)
        fx_row.pack(fill="x", padx=14, pady=(0, 14))
        self.fx_label = tk.Label(fx_row, text="FX LEVEL", bg=PANEL, fg=DIM,
                                 font=("Segoe UI", 8, "bold"))
        self.fx_label.pack(anchor="w")
        scale = tk.Scale(
            fx_row, from_=0, to=100, orient="horizontal",
            variable=self._fx_var, command=self._set_fx,
            showvalue=False, bg=PANEL, fg=TEXT,
            troughcolor="#173039", activebackground=CYAN,
            highlightthickness=0, bd=0, sliderlength=18, width=10,
        )
        scale.pack(fill="x", pady=(3, 0))

    def _build_input(self, parent):
        card = self._card(parent, "SES GİRİŞ MODU", "Sürekli, F8 PTT veya Hey")
        card.pack(fill="x")
        buttons = tk.Frame(card, bg=PANEL)
        buttons.pack(fill="x", padx=14, pady=(2, 14))
        self.mode_buttons = {}
        for key, label in [
            ("continuous", "SÜREKLİ"),
            ("ptt", "PTT · F8"),
            ("wake", "HEY"),
        ]:
            b = self._btn(buttons, label, lambda k=key: self._set_mode(k), CYAN)
            b.pack(side="left", fill="x", expand=True, padx=3)
            self.mode_buttons[key] = b

    def _build_system(self, parent):
        card = self._card(parent, "SİSTEM", "Windows başlangıç ve yerel kısayollar")
        card.pack(fill="x", pady=(0, 12))

        self.autostart_label = tk.Label(card, text="", bg=PANEL, fg=TEXT_2,
                                        font=("Segoe UI", 8, "bold"))
        self.autostart_label.pack(anchor="w", padx=14, pady=(3, 8))

        row = tk.Frame(card, bg=PANEL)
        row.pack(fill="x", padx=14, pady=(0, 14))
        self._btn(row, "AÇILIŞTA BAŞLAT", self._toggle_autostart, GREEN).pack(
            side="left", fill="x", expand=True, padx=(0, 4)
        )
        self._btn(row, "MASAÜSTÜ KISAYOLU", self._desktop_shortcut, CYAN).pack(
            side="left", fill="x", expand=True, padx=(4, 0)
        )

    def _build_integrations(self, parent):
        card = self._card(parent, "ENTEGRASYONLAR", "Mevcut V6 araçlarını ayrı kaybetmeden aç")
        card.pack(fill="both", expand=True)

        grid = tk.Frame(card, bg=PANEL)
        grid.pack(fill="x", padx=14, pady=(4, 12))
        actions = [
            ("API AYARLARI", self._api_settings, CYAN),
            ("PHONE CENTER", self._phone_center, GREEN),
            ("V6 CONTROL CENTER", self._v6_center, PURPLE),
            ("HEY KALİBRE", self._mic_calibrate, GOLD),
        ]
        for i, (label, command, color) in enumerate(actions):
            b = self._btn(grid, label, command, color)
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=4, pady=4)
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        note = (
            "GitHub otomatik güncelleme bu V6 dalında kapalıdır. "
            "Buradaki ayarlar yalnız yerel RIP kurulumunu değiştirir."
        )
        tk.Label(card, text=note, bg=PANEL, fg=DIM, justify="left",
                 wraplength=390, font=("Segoe UI", 8)).pack(
            anchor="w", padx=14, pady=(4, 14)
        )

    def refresh(self):
        self._voice_var.set(str(getattr(self.ui, "_current_voice", self._voice_var.get()) or "Charon"))
        mode = str(getattr(self.ui, "voice_input_mode", "continuous") or "continuous")
        self._mode_var.set(mode)
        for key, btn in getattr(self, "mode_buttons", {}).items():
            active = key == mode
            btn.configure(
                bg="#08232c" if active else PANEL_2,
                fg=GREEN if active else CYAN,
                highlightbackground="#0f6473" if active else LINE,
            )

        sfx = bool(getattr(self.ui, "_sfx_on", True))
        if hasattr(self, "sfx_label"):
            self.sfx_label.configure(text=f"SFX  {'AÇIK' if sfx else 'KAPALI'}", fg=GREEN if sfx else RED)

        try:
            volume = int(round(float(self.ui.sound.get_volume()) * 100))
        except Exception:
            volume = int(self._fx_var.get())
        self._fx_var.set(max(0, min(100, volume)))
        if hasattr(self, "fx_label"):
            self.fx_label.configure(text=f"FX LEVEL  %{self._fx_var.get()}")

        auto = bool(_safe(getattr(self.ui, "_is_autostart_installed", lambda: False)))
        if hasattr(self, "autostart_label"):
            self.autostart_label.configure(
                text=f"Windows ile başlat: {'AÇIK' if auto else 'KAPALI'}",
                fg=GREEN if auto else TEXT_2,
            )

        phone = getattr(self.ui, "phone_control_center", None)
        core = getattr(self.ui, "v6_control_center", None)
        self._status_var.set(
            f"VOICE {self._voice_var.get()} · INPUT {mode.upper()} · "
            f"PHONE {'READY' if phone else 'OFF'} · V6 {'READY' if core else 'OFF'}"
        )

    def _set_voice(self, voice):
        self._voice_var.set(str(voice))
        _safe(getattr(self.ui, "_on_voice_select", lambda _v: None), str(voice))
        self.refresh()

    def _set_mode(self, mode):
        _safe(getattr(self.ui, "_set_voice_input_mode", lambda _m: None), mode)
        self.refresh()

    def _toggle_sfx(self):
        _safe(getattr(self.ui, "_toggle_sfx", lambda: None))
        self.refresh()

    def _set_fx(self, value):
        try:
            v = max(0, min(100, int(float(value))))
        except Exception:
            return
        try:
            self.ui.sound.set_volume(v / 100.0)
        except Exception:
            pass
        if hasattr(self, "fx_label"):
            self.fx_label.configure(text=f"FX LEVEL  %{v}")

    def _toggle_autostart(self):
        _safe(getattr(self.ui, "_toggle_autostart", lambda: None))
        self.root.after(250, self.refresh)

    def _desktop_shortcut(self):
        _safe(getattr(self.ui, "_create_desktop_shortcut", lambda: None))
        self._status_var.set("Masaüstü kısayolu oluşturma isteği gönderildi.")

    def _api_settings(self):
        _safe(getattr(self.ui, "_open_api_settings", lambda: None))
        self._status_var.set("API ayarları açıldı.")

    def _phone_center(self):
        center = getattr(self.ui, "phone_control_center", None)
        if center:
            _safe(center.open, "general")
        else:
            self._status_var.set("Phone Control Center kullanılamıyor.")

    def _v6_center(self):
        center = getattr(self.ui, "v6_control_center", None)
        if center:
            _safe(center.open)
        else:
            self._status_var.set("V6 Control Center kullanılamıyor.")

    def _mic_calibrate(self):
        fn = getattr(self.ui, "_open_mic_calibration", None)
        if callable(fn):
            _safe(fn)
            return
        self._status_var.set("Mikrofon kalibrasyonu için RIP_SES_KALIBRE.bat kullanılabilir.")
