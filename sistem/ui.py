"""
RIPPERINIO — Windows UI v4
Concentric teal rings · Segmented arcs
"""

import os, time, math, random, threading, asyncio
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from pathlib import Path
import psutil

from PIL import Image, ImageTk, ImageDraw, ImageFont, ImageOps, ImageFilter
from app_config import has_gemini_api_key, load_app_config, save_app_config
from version import STAMP
from actions.weather import get_weather_summary, current_location_label
from actions.platform_utils import IS_WIN, PLATFORM_LABEL, open_path
from actions.open_app import open_app
from actions.media import control_media
from actions.system_volume import SystemVolume, control_volume
from actions.now_playing import NowPlayingReader
from actions.theme_control import get_hud_theme, get_hud_theme_palette, set_hud_theme
from hud_widgets import HUDWidgetManager, IncomingCallCard
from global_hotkeys import GlobalPTTHotkey
from app_paths import resource_path


def _make_root():
    """TkDnD varsa native dosya drop destekli root, yoksa normal Tk."""
    try:
        from tkinterdnd2 import TkinterDnD
        return TkinterDnD.Tk()
    except Exception:
        return tk.Tk()

BASE_DIR = Path(__file__).resolve().parent

SYSTEM_NAME = "R.I.P"

MODEL_BADGE = f"VOICE CORE · {PLATFORM_LABEL}"

_TR_MONTHS = (
    "", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)
_TR_DAYS = (
    "Pazartesi", "Salı", "Çarşamba", "Perşembe",
    "Cuma", "Cumartesi", "Pazar",
)

def _turkish_date_text() -> str:
    now = __import__("datetime").datetime.now()
    return f"{now.day} {_TR_MONTHS[now.month]} {now.year}"

def _turkish_day_text() -> str:
    now = __import__("datetime").datetime.now()
    return _TR_DAYS[now.weekday()]


def _enable_dpi_awareness():
    """Windows'ta keskin font/canvas icin en yuksek DPI farkindaligini ac."""
    if not IS_WIN:
        return
    try:
        import ctypes
        # Windows 10+ / 11: per-monitor-v2. 125/150/175% olceklerde Tk'nin
        # Windows tarafindan bitmap olarak buyutulmesini engeller.
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            return
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
            return
        except Exception:
            pass
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()

# ── Breeze Compact renk paleti ───────────────────────────────────────────────
# V5 UI: saf siyah/teal yerine daha ferah slate + sky + violet.
# Eski theme_control API'si korunur; tam HUD icinden tema degistirme hala calisir.
BREEZE_PALETTE = {
    "label": "Aurora Glass",
    "bg": "#070B14",
    "pri": "#5EEBFF",
    "mid": "#7E91AF",
    "dim": "#1D2B42",
    "dimmer": "#0A1220",
    "text": "#F5F8FF",
    "panel": "#0E1728",
    "secondary": "#A58BFF",
    "listening": "#55F2C2",
    "speaking": "#6DA8FF",
}
_HUD_THEME_NAME = "breeze"
_HUD_THEME = dict(BREEZE_PALETTE)
C_BG      = _HUD_THEME["bg"]
C_PRI     = _HUD_THEME["pri"]
C_ORG     = "#A58BFF"
C_ORG2    = "#5EEBFF"
C_MID     = _HUD_THEME["mid"]
C_DIM     = _HUD_THEME["dim"]
C_DIMMER  = _HUD_THEME["dimmer"]
C_TEXT    = _HUD_THEME["text"]
C_PANEL   = _HUD_THEME["panel"]
C_GREEN   = _HUD_THEME["listening"]
C_RED     = "#FF647C"
C_MUTED   = "#F17CB0"
C_BLUE    = _HUD_THEME["secondary"]
C_GOLD    = "#F5C76B"

# Orb durum renkleri
def _hex_rgb(value: str):
    value = str(value or "#000000").lstrip("#")
    try:
        return tuple(int(value[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return (124, 181, 255)


ORB_COLORS = {
    "LISTENING":    _hex_rgb(_HUD_THEME["listening"]),
    "SPEAKING":     _hex_rgb(_HUD_THEME["speaking"]),
    "THINKING":     (124, 181, 255),
    "PROCESSING":   (182, 156, 255),
    "MUTED":        (232, 121, 169),
    "PAUSED":       (91, 108, 138),
    "STANDBY":      (122, 145, 181),
    "ERROR":        (255, 107, 122),
    "INITIALISING": (124, 181, 255),
}

# ── Boyutlar ─────────────────────────────────────────────────────────────────
W_TARGET = 1360
H_TARGET = 820
W_MIN = 1040
H_MIN = 680
LEFT_W_T = 256
RIGHT_W_T = 330
HDR_H    = 70
FOOTER_H = 26
INPUT_H  = 40
CONTROL_H = 106

VOICES = ["Charon", "Puck", "Aoede", "Kore", "Fenrir", "Leda", "Orus", "Zephyr"]

# ── Font sistemi ─────────────────────────────────────────────────────────────
# Grift fontu macOS'ta kullanicinin sisteminde yuklu. Windows'ta ise paketle
# gelen Fonts/ klasorunden calisma aninda yuklenir — kurulum gerekmez.
FONT_BODY_FAMILY = "Segoe UI Variable Text" if IS_WIN else "Grift"
FONT_DISPLAY_FAMILY = "Segoe UI Variable Display" if IS_WIN else "Grift Extra Bold"

# Grift bulunamazsa kullanilacak yedekler
FONT_FALLBACK_BODY = "Segoe UI" if IS_WIN else "Helvetica Neue"
FONT_FALLBACK_DISPLAY = "Segoe UI Semibold" if IS_WIN else "Helvetica Neue"

FONTS_DIR = resource_path("Fonts")


def _register_bundled_fonts() -> int:
    """
    Fonts/ icindeki .ttf dosyalarini bu surece ozel olarak yukler.
    Sisteme kalici kurulum yapmaz, yonetici izni istemez.
    tk.Tk() olusturulmadan once cagrilmali.
    """
    if not IS_WIN or not FONTS_DIR.is_dir():
        return 0

    try:
        import ctypes

        FR_PRIVATE = 0x10
        loaded = 0
        for font_file in sorted(FONTS_DIR.glob("*.ttf")):
            try:
                if ctypes.windll.gdi32.AddFontResourceExW(str(font_file), FR_PRIVATE, 0):
                    loaded += 1
            except Exception:
                continue
        return loaded
    except Exception:
        return 0


_register_bundled_fonts()


def _resolve_font_families(root: tk.Tk) -> None:
    """Grift gercekten kullanilabiliyor mu? Degilse yedek aileye gec."""
    global FONT_BODY_FAMILY, FONT_DISPLAY_FAMILY
    try:
        available = {name.lower() for name in tkfont.families(root)}
    except Exception:
        return

    body_ok = FONT_BODY_FAMILY.lower() in available
    if not body_ok:
        FONT_BODY_FAMILY = FONT_FALLBACK_BODY

    if FONT_DISPLAY_FAMILY.lower() in available:
        return

    # Windows, extra bold varyantini "Grift ExtBd" gibi kisaltilmis bir aile
    # adiyla kaydeder. Govde ailesinin agir varyantini ara.
    if body_ok:
        stem = FONT_BODY_FAMILY.lower()
        for name in sorted(available):
            if name.startswith(stem) and name != stem and any(
                tag in name for tag in ("ext", "black", "heavy", "bold")
            ):
                FONT_DISPLAY_FAMILY = name
                return
        FONT_DISPLAY_FAMILY = FONT_BODY_FAMILY
    else:
        FONT_DISPLAY_FAMILY = FONT_FALLBACK_DISPLAY


def font_body(size: int):
    return (FONT_BODY_FAMILY, size)


def font_body_bold(size: int):
    return (FONT_BODY_FAMILY, size, "bold")


def font_display(size: int):
    return (FONT_DISPLAY_FAMILY, size)


STATE_HEX_COLORS = {
    "LISTENING": _HUD_THEME["listening"],
    "SPEAKING": _HUD_THEME["speaking"],
    "THINKING": "#00dcff",
    "PROCESSING": C_GOLD,
    "STANDBY": "#5a8c96",
    "INITIALISING": C_RED,
    "ERROR": C_RED,
}


# ── SoundManager ─────────────────────────────────────────────────────────────
import subprocess as _sp

# Ses calma platforma gore dallanir: macOS'ta afplay, Windows'ta MCI.
# Her ikisi de ayni poll()/terminate() arayuzunu dondurur.
from actions.audio_player import spawn_player

def _resolve_sfx_dir() -> Path:
    return resource_path("SFX")


_SFX_DIR = _resolve_sfx_dir()
_HUD_FILE = _SFX_DIR / "HUD.mp3"
_START_FILE = _SFX_DIR / "Start.mp3"
_THINK_FILE = _SFX_DIR / "Think.mp3"
_DONE_FILE = _SFX_DIR / "Done.mp3"
_ERROR_FILE = _SFX_DIR / "Error.mp3"


class SoundManager:
    def __init__(self):
        self._enabled = True
        self._ambient_proc = None
        self._volume = 0.20
        self._ambient_stop = None
        self._ambient_thread = None
        self._foreground_proc = None
        self._foreground_stop = None
        self._foreground_thread = None
        self._foreground_tag = ""
        self._lock = threading.Lock()

    @staticmethod
    def _terminate_process(proc):
        if not proc or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def start_ambient(self):
        if not _HUD_FILE.exists():
            return
        with self._lock:
            if not self._enabled:
                return
            if self._foreground_proc and self._foreground_proc.poll() is None:
                return
            if self._ambient_thread and self._ambient_thread.is_alive():
                return
            stop_event = threading.Event()
            worker = threading.Thread(
                target=self._loop_ambient,
                args=(stop_event,),
                daemon=True,
            )
            self._ambient_stop = stop_event
            self._ambient_thread = worker
        worker.start()

    def _loop_ambient(self, stop_event: threading.Event):
        while not stop_event.is_set():
            with self._lock:
                if not self._enabled or self._ambient_stop is not stop_event:
                    break
                volume = self._volume
            try:
                proc = spawn_player(_HUD_FILE, volume)
            except Exception:
                break

            with self._lock:
                if self._ambient_stop is not stop_event or not self._enabled:
                    self._terminate_process(proc)
                    break
                self._ambient_proc = proc

            while proc.poll() is None and not stop_event.wait(0.2):
                pass

            if stop_event.is_set():
                self._terminate_process(proc)

            with self._lock:
                if self._ambient_proc is proc:
                    self._ambient_proc = None

            if stop_event.is_set():
                break
            time.sleep(0.2)

        with self._lock:
            if self._ambient_stop is stop_event:
                self._ambient_stop = None
            if self._ambient_thread and self._ambient_thread.ident == threading.get_ident():
                self._ambient_thread = None

    def _stop_ambient(self):
        with self._lock:
            stop_event = self._ambient_stop
            proc = self._ambient_proc
            self._ambient_stop = None
            self._ambient_thread = None
            self._ambient_proc = None
        if stop_event:
            stop_event.set()
        self._terminate_process(proc)

    def _stop_foreground(self):
        with self._lock:
            stop_event = self._foreground_stop
            proc = self._foreground_proc
            self._foreground_stop = None
            self._foreground_thread = None
            self._foreground_proc = None
            self._foreground_tag = ""
        if stop_event:
            stop_event.set()
        self._terminate_process(proc)

    def _play_foreground(
        self,
        path: Path,
        tag: str,
        loop: bool = False,
        volume_factor: float = 1.0,
        pause_ambient: bool = True,
    ):
        if not path.exists():
            return
        with self._lock:
            if not self._enabled:
                return
            if loop and self._foreground_tag == tag and self._foreground_thread and self._foreground_thread.is_alive():
                return
            base_volume = self._volume
        if pause_ambient:
            self._stop_ambient()
        self._stop_foreground()

        stop_event = threading.Event()
        worker = threading.Thread(
            target=self._foreground_worker,
            args=(
                path,
                tag,
                stop_event,
                loop,
                max(0.0, min(1.0, base_volume * volume_factor)),
                pause_ambient,
            ),
            daemon=True,
        )
        with self._lock:
            self._foreground_stop = stop_event
            self._foreground_thread = worker
            self._foreground_tag = tag
        worker.start()

    def _foreground_worker(
        self,
        path: Path,
        tag: str,
        stop_event: threading.Event,
        loop: bool,
        volume: float,
        resume_ambient: bool,
    ):
        while not stop_event.is_set():
            try:
                proc = spawn_player(path, volume)
            except Exception:
                break

            with self._lock:
                if self._foreground_stop is not stop_event or not self._enabled:
                    self._terminate_process(proc)
                    break
                self._foreground_proc = proc

            while proc.poll() is None and not stop_event.wait(0.12):
                pass

            if stop_event.is_set():
                self._terminate_process(proc)

            with self._lock:
                if self._foreground_proc is proc:
                    self._foreground_proc = None

            if not loop or stop_event.is_set():
                break
            time.sleep(0.08)

        with self._lock:
            if self._foreground_stop is stop_event:
                self._foreground_stop = None
                self._foreground_thread = None
                self._foreground_tag = ""
            should_restart = resume_ambient and self._enabled and self._foreground_stop is None
        if should_restart:
            self.start_ambient()

    def play_startup(self):
        self._play_foreground(_START_FILE, tag="start", loop=False, volume_factor=0.95)

    def play_success(self):
        self._play_foreground(
            _DONE_FILE,
            tag="done",
            loop=False,
            volume_factor=0.68,
            pause_ambient=False,
        )

    def play_error(self):
        self._play_foreground(_ERROR_FILE, tag="error", loop=False, volume_factor=0.95)

    def start_thinking(self):
        self._play_foreground(
            _THINK_FILE,
            tag="think",
            loop=True,
            volume_factor=0.82,
            pause_ambient=False,
        )

    def stop_thinking(self):
        with self._lock:
            is_thinking = self._foreground_tag == "think"
        if is_thinking:
            self._stop_foreground()

    def toggle(self) -> bool:
        self.set_enabled(not self._enabled)
        return self._enabled

    def set_enabled(self, enabled: bool):
        enabled = bool(enabled)
        with self._lock:
            self._enabled = enabled
        if enabled:
            self.start_ambient()
        else:
            self._stop_ambient()
            self._stop_foreground()

    def set_volume(self, volume: float):
        with self._lock:
            self._volume = max(0.0, min(1.0, float(volume)))
            fg_tag = self._foreground_tag
            can_restart_ambient = self._enabled and not fg_tag
        if fg_tag == "think":
            self._stop_foreground()
            self.start_thinking()
        elif can_restart_ambient:
            self._stop_ambient()
            self.start_ambient()

    def stop_all(self):
        with self._lock:
            self._enabled = False
        self._stop_ambient()
        self._stop_foreground()

    def get_volume(self) -> float:
        return self._volume


# ─────────────────────────────────────────────────────────────────────────────

class RipUI:
    def __init__(self):
        self.root = _make_root()
        self.root.withdraw()  # Tam render olana kadar gizle — siyah flash önlenir
        _resolve_font_families(self.root)
        # Arayuzun kurulmasi ~10 sn suruyor. Kisayoldan (pythonw, konsolsuz)
        # acilista bu sure boyunca ekranda hicbir sey olmuyordu; kullanici
        # "acilmadi" sanip tekrar tikliyordu. Once aciliş ekranini goster.
        self._show_splash()
        self.root.title("R.I.P")
        # Pencere serbestce boyutlandirilabilir (macOS surumundeki gibi).
        # Yerlesim _on_configure → _resize_surface ile kendini yeniden kurar.
        self.root.resizable(True, True)
        self.root.minsize(W_MIN, H_MIN)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.W = min(sw - 48, W_TARGET)
        self.H = min(sh - 84, H_TARGET)
        _geo = f"{self.W}x{self.H}+{(sw-self.W)//2}+{(sh-self.H)//2}"
        self.root.geometry(_geo)
        self.root.configure(bg=C_BG)
        try:
            self.root.option_add("*Font", font_body(10))
        except Exception:
            pass

        self._window_geometry = _geo
        self._normal_size = (self.W, self.H)
        self._fullscreen = False

        # Legacy migration: eski dev/fullscreen HUD tercihini bir kez sifirla.
        # Ozellikler silinmez; floating widgetlar F10 edit modunda geri gelir.
        try:
            _cfg44 = load_app_config()
            if not bool(_cfg44.get("breeze_compact_v44_migrated", False)):
                save_app_config({
                    "breeze_compact_v44_migrated": True,
                    "window_fullscreen": False,
                    "hud_widgets_visible": False,
                    "ui_shell_mode": "compact",
                })
        except Exception:
            pass
        self._resize_job = None

        self._set_layout_metrics(self.W, self.H)

        # ── State ────────────────────────────────────────────────────────────
        self.speaking        = False
        self.user_speaking   = False
        self.muted           = False
        self.paused          = False
        self.mic_available   = True
        self.mic_name        = ""
        self.scale           = 1.0
        self.target_scale    = 1.0
        self.halo_a          = 55.0
        self.target_halo     = 55.0
        self.last_t          = time.time()
        self.tick            = 0
        self.rings_spin      = [0.0, 45.0, 90.0, 200.0]  # 4 ayrı halka
        self.pulse_r         = []
        self.status_blink    = True
        self._ripperinio_state   = "INITIALISING"
        self._user_speaking_until = 0.0

        # ── Webcam ───────────────────────────────────────────────────────────
        self._webcam_active        = False
        self._webcam_photo         = None
        self._cam_label: "tk.Label | None" = None
        self._cam_orb_shift        = 0.0   # orb'un anlık kayması (animasyonlu)
        self._cam_orb_shift_target = 0.0   # hedef kayma
        self._cam_orb_face         = 0.0   # orb'un anlık face boyutu (0 → FACE kullan)
        self._cam_orb_face_target  = 0.0   # hedef face boyutu
        self._weather_card = {
            "city": "",
            "primary": "--",
            "details": ["Hava durumu yükleniyor..."],
        }
        self._panel_focus = ""
        self._panel_focus_until = 0.0
        self._brief_refresh_busy = False
        self._started_at = time.time()
        self._error_hold_until = 0.0
        self._settings_open = False
        self._settings_tab = "settings"

        # ── Medya HUD / Windows ana ses ─────────────────────────────────────
        self._media_reader = NowPlayingReader()
        self._media_volume = SystemVolume()
        self._media_snapshot = {
            "available": False, "active": False, "title": "", "artist": "",
            "album": "", "source": "SİSTEM MEDYASI", "status": "IDLE",
            "thumbnail": b"", "error": "",
        }
        self._media_stop_event = threading.Event()
        self._media_thread = None
        self._media_volume_thread = None
        self._media_volume_snapshot = {
            "available": False, "volume": 0, "muted": False, "error": "",
        }
        self._media_thumb_photo = None
        self._media_thumb_key = None
        # V2 visualizer: Windows master-output meter gercek ses enerjisini verir.
        # FFT degil; ancak beat/pulse ve bar hareketi gercek ses genligine baglidir.
        self._media_peak = 0.0
        self._media_peak_raw = 0.0
        self._media_beat = 0.0
        self._media_energy = deque([0.0] * 24, maxlen=24)
        self._media_wave_bins = [0.0] * 48
        self._phone_connected = False

        # RIPPERINIO Telefon paneli
        self._phone_open = False
        try:
            from ripperinio_web.launcher import PhoneServer
            self._phone_server = PhoneServer()
        except Exception:
            self._phone_server = None
        self._phone_qr_photo = None
        self._phone_geometry = {"btn_w": 250, "btn_h": 38, "panel_w": 300, "panel_h": 440}
        self._debug_entries = deque(maxlen=160)
        self._startup_sfx_played = False
        self._settings_geometry = {
            "btn_x": 14,
            "btn_y": 12,
            "btn_w": 250,
            "btn_h": 46,
            "panel_x": 14,
            "panel_y": HDR_H + 10,
            "panel_w": 320,
            "panel_h": 430,
        }
        self.setup_frame = None
        self.api_entry = None
        self.youtube_api_entry = None
        self.youtube_handle_entry = None

        # ── Callbacks ────────────────────────────────────────────────────────
        self.on_text_command = None
        self.on_pause_toggle = None
        self.on_stop_command = None
        self.on_voice_change = None
        self.on_effects_state_change = None
        self.on_webcam_toggle = None
        self.on_ptt_change = None
        self.on_voice_input_mode_change = None
        self.on_undo_request = None

        # ── Voice ────────────────────────────────────────────────────────────
        self._current_voice = self._load_voice()
        try:
            _voice_cfg = load_app_config()
        except Exception:
            _voice_cfg = {}
        self.voice_input_mode = str(_voice_cfg.get("voice_input_mode", "continuous") or "continuous")
        if self.voice_input_mode not in {"continuous", "ptt", "wake"}:
            self.voice_input_mode = "continuous"
        self._ptt_active = False

        # ── Sound ────────────────────────────────────────────────────────────
        self.sound = SoundManager()

        # ── Stats ────────────────────────────────────────────────────────────
        self._stats      = {'cpu': 0.0, 'ram': 0.0, 'disk': 0.0,
                            'battery': 100.0, 'net_up': 0.0, 'net_down': 0.0}
        self._cpu_hist   = [0.0] * 24
        self._last_net   = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._wave_ripperinio = [random.randint(4, 26) for _ in range(18)]
        self._wave_user   = [random.randint(2, 10) for _ in range(18)]

        # ── Typing ───────────────────────────────────────────────────────────
        self.typing_queue = deque()
        self.is_typing    = False

        # ── Partiküller (arka plan, az sayıda) ───────────────────────────────
        self.particles = [
            {
                'x':  random.uniform(0, self.W),
                'y':  random.uniform(0, self.H),
                'vx': random.uniform(-0.15, 0.15),
                'vy': random.uniform(-0.15, 0.15),
                'r':  random.uniform(0.5, 1.8),
                'a':  random.randint(15, 70),
            }
            for _ in range(10)
        ]

        self.orb_particles = [
            {
                'angle': random.uniform(0, math.tau),
                'orbit': random.uniform(0.06, 0.98),
                'speed': random.uniform(-0.030, 0.030),
                'size': random.uniform(0.8, 2.8),
                'phase': random.uniform(0, math.tau),
                'wobble': random.uniform(0.010, 0.040),
                'depth': random.uniform(0.30, 1.00),
            }
            for _ in range(54)
        ]
        self.orb_shell_particles = [
            {
                'angle': random.uniform(0, math.tau),
                'speed': random.uniform(-0.020, 0.020),
                'size': random.uniform(1.4, 3.8),
                'phase': random.uniform(0, math.tau),
                'glow': random.uniform(0.4, 1.0),
            }
            for _ in range(28)
        ]

        # ── Canvas ───────────────────────────────────────────────────────────
        self.bg = tk.Canvas(self.root, width=self.W, height=self.H,
                            bg=C_BG, highlightthickness=0)
        self.bg.place(x=0, y=0)

        # ── Log ──────────────────────────────────────────────────────────────
        self.log_frame = tk.Frame(self.root, bg="#0D1728",
                                  highlightbackground="#0D1728",
                                  highlightthickness=0)
        self.log_frame.place(x=self.CHAT_X, y=self.CHAT_Y,
                             width=self.CHAT_W, height=self.CHAT_H)
        self.log_text = tk.Text(
            self.log_frame, fg=C_TEXT, bg="#0D1728",
            insertbackground=C_TEXT, borderwidth=0,
            wrap="word", font=font_body(11), padx=14, pady=10, highlightthickness=0)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        self.log_text.tag_config("you", foreground="#DCE8FF")
        self.log_text.tag_config("ai",  foreground=C_PRI)
        self.log_text.tag_config("sys", foreground=C_GOLD)
        self.log_text.tag_config("err", foreground=C_RED)

        self._build_input_bar(self.CHAT_W)
        self._build_mute_button()
        self._build_pause_button()
        self._build_webcam_button()
        self._build_shutdown_button()
        self._build_social_bar()
        self._build_media_panel()
        self._build_phone_panel()
        self._build_settings_panel()
        self._build_voice_selector(self._settings_body)
        self._build_theme_selector(self._settings_body)
        self._build_sfx_button(self._settings_body)
        self._build_api_button(self._settings_body)
        self._build_fx_slider(self._settings_body)
        self._build_autostart_button(self._settings_body)
        self._build_shortcut_button(self._settings_body)
        self._build_v6_button(self._settings_body)
        self._layout_settings_controls()
        self._place_layout_widgets()

        # Sürükle-bırak HUD widget sistemi + büyük gelen arama kartı.
        self.widget_manager = HUDWidgetManager(self)
        self._call_card = IncomingCallCard(self)
        self._build_compact_dock()

        # Orb tıklama = pause/resume
        self.bg.bind("<Button-1>", self._on_canvas_click)

        self.root.bind("<F4>",        lambda e: self._toggle_mute())
        self.root.bind("<Escape>",    lambda e: self._esc_action())
        self.root.bind("<F5>",        lambda e: self._toggle_pause())
        self.root.bind("<F6>",        lambda e: self._toggle_webcam_ui())
        self.root.bind("<F7>",        lambda e: self._cycle_voice_input_mode())
        self.root.bind("<KeyPress-F8>",   self._ptt_press)
        self.root.bind("<KeyRelease-F8>", self._ptt_release)
        self.root.bind("<F9>",        lambda e: self._open_v6_control_center())
        self.root.bind("<F10>",       lambda e: self._toggle_widgets())
        self.root.bind("<F11>",       lambda e: self._toggle_fullscreen())
        self.root.bind("<Control-Shift-space>", lambda e: self.toggle_dashboard())
        self._global_ptt = GlobalPTTHotkey(self.root, self._ptt_press, self._ptt_release)
        if IS_WIN and not self._global_ptt.start():
            self.write_debug("Global F8 PTT hook açılamadı; RIP penceresi odaktayken F8 çalışır.", level="WARN")
        # macOS'ta ⌘, Windows'ta Ctrl — ikisi de bagli, platforma gore biri calisir
        self.root.bind("<Command-m>", lambda e: self._toggle_mute())
        self.root.bind("<Command-f>", lambda e: self._toggle_fullscreen())
        self.root.bind("<Control-m>", lambda e: self._toggle_mute())
        self.root.bind("<Control-f>", lambda e: self._toggle_fullscreen())

        self._api_key_ready = has_gemini_api_key()
        if not self._api_key_ready:
            self._show_setup_ui()

        self.root.bind("<Configure>", self._on_configure)

        self._effects_active = None
        self._sync_sound_state()
        self._kick_brief_refresh()
        # İlk render — deiconify sonrası _draw() zorunlu, macOS cached content'i atmıyor
        self.root.update_idletasks()
        self._draw()
        self.root.update()
        self._animate()
        self._destroy_splash()
        # Compact Shell: dev dashboard acilista ekran kaplamaz.
        self._fullscreen = False
        try:
            self.root.attributes("-fullscreen", False)
        except Exception:
            pass
        self.root.geometry(self._window_geometry)
        self._resize_surface(*self._normal_size)
        self.root.withdraw()
        self._compact_show("mini", reason="startup")

        # V6.2.1e: tam Phone Control Center korunur, fakat ekranın sol kenarına
        # fare götürünce otomatik açılan legacy PhoneEdgeDrawer devre dışıdır.
        # Telefon kontrollerine ana HUD'daki TELEFON / PHONE CENTER düğmelerinden girilir.
        self.phone_control_center = None
        self.phone_edge_drawer = None
        try:
            from phone_control_center import PhoneControlCenter
            self.phone_control_center = PhoneControlCenter(self.root, self.write_log)
        except Exception as exc:
            try:
                self.write_debug(f"Phone Control Center baslatilamadi: {exc}", level="WARN")
            except Exception:
                pass

        # V6 Control Center + Automation Engine. F9 ile de açılır.
        self.v6_control_center = None
        try:
            from v6_control_center import V6ControlCenter
            from v6_core import AUTOMATION_ENGINE
            AUTOMATION_ENGINE.start()
            self.v6_control_center = V6ControlCenter(
                self.root, self.write_log, getattr(self, "phone_control_center", None)
            )
        except Exception as exc:
            try: self.write_debug(f"V6 Control Center baslatilamadi: {exc}", level="WARN")
            except Exception: pass

        self._start_media_monitor()
        # V5.2.8: RIP acilir acilmaz telefon bridge'ini otomatik hazirla.
        # PhoneServer.start() idempotenttir: bu kurulumun bridge'i zaten
        # 8765'te aciksa ikinci bir surec baslatmaz, sadece durumu tazeler.
        self.root.after(350, self._auto_start_phone_connection)
        self.root.after(1200, self._poll_theme_config)

        # V6.2.1c: supplied reference screenshot skin.  This is deliberately
        # presentation-only: the legacy runtime stays alive underneath so the
        # complete Phone Control Center, Drop Zone, Find Phone, calls, bridge,
        # media, notifications and voice callbacks remain available.
        # No GitHub/self-update service is installed or started here.
        self.reference_hud = None
        try:
            from reference_hud import install_reference_hud
            self.reference_hud = install_reference_hud(self)
        except Exception as exc:
            try:
                self.write_debug(f"Reference HUD baslatilamadi: {exc}", level="WARN")
            except Exception:
                pass

        self.root.protocol("WM_DELETE_WINDOW", self._shutdown)

    # ── Acilis ekrani ─────────────────────────────────────────────────────────
    def _show_splash(self):
        """Agir arayuz kurulmadan once aninda gorunen kucuk bir pencere."""
        self._splash = None
        try:
            splash = tk.Toplevel(self.root)
            splash.overrideredirect(True)
            splash.configure(bg=C_BG)
            splash.attributes("-topmost", True)

            w, h = 420, 150
            sw = splash.winfo_screenwidth()
            sh = splash.winfo_screenheight()
            splash.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

            canvas = tk.Canvas(splash, width=w, height=h, bg=C_BG,
                               highlightthickness=0)
            canvas.pack()
            # Kose parantezleri — ana arayuzle ayni dil
            bl = 14
            for bx, by, sx, sy in [(2, 2, 1, 1), (w - 2, 2, -1, 1),
                                   (2, h - 2, 1, -1), (w - 2, h - 2, -1, -1)]:
                canvas.create_line(bx, by, bx + sx * bl, by, fill=C_PRI, width=2)
                canvas.create_line(bx, by, bx, by + sy * bl, fill=C_PRI, width=2)
            canvas.create_text(w // 2, h // 2 - 16, text=SYSTEM_NAME,
                               fill=C_PRI, font=font_display(30))
            canvas.create_text(w // 2, h // 2 + 26, text="BAŞLATILIYOR...",
                               fill=C_MID, font=font_body_bold(11))

            self._splash = splash
            splash.update()
        except Exception:
            self._splash = None

    def _destroy_splash(self):
        splash = getattr(self, "_splash", None)
        if splash is None:
            return
        self._splash = None
        try:
            splash.destroy()
        except Exception:
            pass

    # ── Layout & Fullscreen ───────────────────────────────────────────────────
    def _on_configure(self, event):
        if event.widget is not self.root:
            return
        if not hasattr(self, "bg"):
            return
        # DIKKAT: event.width/height BAYAT olabilir. Tam ekrana gecerken
        # Tk once dogru boyutu uyguluyor, ardindan ESKI pencere olcusunu
        # tasiyan gecikmis bir Configure olayi geliyordu; bu da arayuzu
        # 1920x1080 ekranda 1540x940'lik alana sikistiriyordu.
        # Bu yuzden olcuyu olaydan degil, callback anindaki GERCEK pencereden
        # okuyoruz.
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(80, self._apply_current_size)

    def _apply_current_size(self):
        self._resize_job = None
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
        except Exception:
            return
        if w < 200 or h < 200:      # pencere henuz gerceklesmemis
            return
        if w == self.W and h == self.H:
            return

        # Pencere modundayken kullanicinin ayarladigi boyutu hatirla; tam
        # ekrandan cikinca eski sabit boyuta degil, buna donulur.
        if not self._fullscreen and w >= W_MIN and h >= H_MIN:
            self._normal_size = (w, h)
            self._window_geometry = f"{w}x{h}+{self.root.winfo_x()}+{self.root.winfo_y()}"

        self._resize_surface(w, h)

    def _save_window_state(self):
        """Boyut ve tam ekran tercihini bir sonraki acilis icin sakla."""
        try:
            save_app_config({
                "window_geometry": self._window_geometry,
                "window_fullscreen": bool(self._fullscreen),
            })
        except Exception:
            pass

    def _restore_window_state(self) -> bool:
        """
        Kayitli pencere tercihini uygular.
        Tam ekran isteniyorsa (veya kayit yoksa) True doner.
        """
        try:
            config = load_app_config()
        except Exception:
            return False

        geometry = str(config.get("window_geometry", "") or "").strip()
        if geometry:
            try:
                size = geometry.split("+")[0]
                w, h = (int(part) for part in size.split("x"))
                if w >= W_MIN and h >= H_MIN:
                    self._window_geometry = geometry
                    self._normal_size = (w, h)
            except Exception:
                pass

        return bool(config.get("window_fullscreen", False))

    def _set_layout_metrics(self, width: int, height: int):
        self.W = int(width)
        self.H = int(height)
        if os.environ.get("RIPPERINIO_LAYOUT_DEBUG") == "1":
            try:
                import time as _t
                with open(os.path.join(os.environ.get("TEMP", "."), "ripperinio_layout.log"),
                          "a", encoding="utf-8") as _f:
                    _f.write(f"{_t.strftime('%H:%M:%S')} W={self.W} H={self.H} fullscreen={getattr(self, '_fullscreen', '?')}\n")
            except Exception:
                pass
        self.LEFT_W = min(LEFT_W_T, max(220, int(self.W * 0.195)))
        self.RIGHT_W = min(RIGHT_W_T, max(300, int(self.W * 0.245)))
        center_w = self.W - self.LEFT_W - self.RIGHT_W
        orb_area_h = self.H - HDR_H - CONTROL_H - FOOTER_H - 28
        self.FCX = self.LEFT_W + center_w // 2
        self.FCY = HDR_H + orb_area_h // 2 + 2
        self.FACE = min(int(orb_area_h * 0.66), int(center_w * 0.55), 350)
        self.CENTER_X0 = self.LEFT_W
        self.CENTER_X1 = self.W - self.RIGHT_W
        self.CTRL_X = self.LEFT_W + 20
        self.CTRL_Y = HDR_H + orb_area_h + 4
        self.CTRL_W = center_w - 40
        self.CHAT_PANEL_X = self.W - self.RIGHT_W + 8
        self.CHAT_PANEL_Y = HDR_H + 10
        self.CHAT_PANEL_W = self.RIGHT_W - 18
        self.CHAT_PANEL_H = self.H - HDR_H - FOOTER_H - 20
        self.CHAT_X = self.CHAT_PANEL_X + 12
        self.CHAT_Y = self.CHAT_PANEL_Y + 42
        self.CHAT_W = self.CHAT_PANEL_W - 24
        media_space = max(228, min(300, int((self.CHAT_PANEL_H - 92) * 0.40)))
        self.MEDIA_X = self.CHAT_X
        self.MEDIA_W = self.CHAT_W
        self.MEDIA_H = media_space
        self.CHAT_H = max(178, self.CHAT_PANEL_H - 102 - self.MEDIA_H)
        self.MEDIA_Y = self.CHAT_Y + self.CHAT_H + 10
        self.CHAT_INPUT_Y = self.CHAT_PANEL_Y + self.CHAT_PANEL_H - INPUT_H - 12
        # background is size-aware; invalidate only on resize.
        if getattr(self, "_aurora_bg_key", (None,None))[0:2] != (self.W,self.H):
            self._aurora_bg_key = None

    def _enter_fullscreen(self):
        self.root.resizable(True, True)          # macOS native fullscreen için gerekli
        self.root.attributes("-fullscreen", True)
        # Tam ekran uygulanmadan olculursek eski boyutu aliriz — once isle.
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        w = self.root.winfo_width() or self.root.winfo_screenwidth()
        h = self.root.winfo_height() or self.root.winfo_screenheight()
        if w < 200 or h < 200:
            w, h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self._resize_surface(w, h)
        # Gecis tamamlandiktan sonra gercek olcuyle bir kez daha dogrula
        self.root.after(150, self._apply_current_size)

    def _toggle_fullscreen(self):
        self._fullscreen = not self._fullscreen
        self.root.after(600, self._save_window_state)
        if self._fullscreen:
            self._enter_fullscreen()
        else:
            self.root.attributes("-fullscreen", False)
            # Pencere modunda da serbest boyutlandirma acik kalir;
            # kullanicinin en son ayarladigi boyuta geri donulur.
            self.root.geometry(self._window_geometry)
            self._resize_surface(*self._normal_size)

    def _esc_action(self):
        """Esc tam HUD'u kapatmaz; compact moda geri doner."""
        if self._fullscreen:
            self._fullscreen = False
            self.root.attributes("-fullscreen", False)
            self.root.geometry(self._window_geometry)
            self._resize_surface(*self._normal_size)
        else:
            self.hide_dashboard()
        return "break"

    def _resize_surface(self, width: int, height: int):
        self._set_layout_metrics(width, height)
        self.bg.configure(width=self.W, height=self.H)
        self.bg.place(x=0, y=0)
        self._place_layout_widgets()
        if hasattr(self, "_media_frame"):
            self._layout_media_panel()
        if hasattr(self, "_social_bar"):
            self._social_bar.place(x=14, y=self.H - FOOTER_H - 52)
        self._place_phone_widgets()
        for p in getattr(self, "particles", []):
            p["x"] %= self.W
            p["y"] %= self.H
        # Kamera açıksa layout değiştiğinde hedefleri ve label konumunu güncelle
        if self._webcam_active:
            cam_w, cam_h, cam_x, cam_y, shift, face = self._calc_cam_layout()
            self._cam_orb_shift_target = float(shift)
            self._cam_orb_face_target  = float(face)
            if self._cam_label is not None:
                self._cam_label.place(x=cam_x, y=cam_y,
                                      width=cam_w, height=cam_h)

    # ── Breeze Compact Shell ───────────────────────────────────────────────────
    @staticmethod
    @staticmethod
    def _round_rect(canvas, x1, y1, x2, y2, radius=18, **kwargs):
        """Rounded card primitive used by the V5.1 Aurora shell."""
        r = max(2, min(int(radius), int((x2-x1)/2), int((y2-y1)/2)))
        pts = [
            x1+r,y1, x2-r,y1, x2,y1, x2,y1+r,
            x2,y2-r, x2,y2, x2-r,y2, x1+r,y2,
            x1,y2, x1,y2-r, x1,y1+r, x1,y1,
        ]
        return canvas.create_polygon(pts, smooth=True, splinesteps=36, **kwargs)

    @staticmethod
    def _rgb(value: str):
        v = str(value or "#000000").lstrip("#")
        try:
            return tuple(int(v[i:i+2], 16) for i in (0,2,4))
        except Exception:
            return (255,255,255)

    def _draw_pill(self, canvas, text: str, accent: str, *, active=False, danger=False, font_size=10):
        """Tek tip modern buton; eski piksel-kose braketlerini tamamen kaldirir."""
        try:
            w = int(float(canvas.cget("width")))
            h = int(float(canvas.cget("height")))
        except Exception:
            w, h = 120, 36
        canvas.delete("all")
        bg = "#2A1520" if danger and active else ("#15273A" if active else "#101B2D")
        outline = accent if active else "#23334D"
        self._round_rect(canvas, 1, 1, max(2,w-1), max(2,h-1), radius=max(8,h//2),
                         fill=bg, outline=outline, width=1)
        if active:
            canvas.create_line(18, h-2, max(18,w-18), h-2, fill=accent, width=2, capstyle=tk.ROUND)
        canvas.create_text(w//2, h//2, text=text, fill=accent,
                           font=font_body_bold(font_size))

    def _background_photo_for_current_size(self):
        """Resize aninda uretilen yumusak gradient; frame basina tekrar hesaplanmaz."""
        key = (self.W, self.H, C_BG, C_PRI, C_BLUE)
        if getattr(self, "_aurora_bg_key", None) == key and getattr(self, "_aurora_bg_photo", None):
            return self._aurora_bg_photo
        w, h = max(2,self.W), max(2,self.H)
        top = self._rgb("#070B14")
        bot = self._rgb("#0A1220")
        img = Image.new("RGB", (w,h), top)
        draw = ImageDraw.Draw(img)
        for yy in range(h):
            f = yy / max(1,h-1)
            rgb = tuple(int(top[i]*(1-f) + bot[i]*f) for i in range(3))
            draw.line((0,yy,w,yy), fill=rgb)
        glow = Image.new("RGBA", (w,h), (0,0,0,0))
        gd = ImageDraw.Draw(glow)
        r = max(240, min(430, int(min(w,h)*0.48)))
        cx, cy = int(self.FCX), int(self.FCY)
        gd.ellipse((cx-r,cy-r,cx+r,cy+r), fill=(52,116,190,34))
        r2 = int(r*0.68)
        gd.ellipse((cx-r2,cy-r2,cx+r2,cy+r2), fill=(126,92,255,22))
        glow = glow.filter(ImageFilter.GaussianBlur(max(48,int(r*0.28))))
        img = Image.alpha_composite(img.convert("RGBA"), glow)
        self._aurora_bg_photo = ImageTk.PhotoImage(img)
        self._aurora_bg_key = key
        return self._aurora_bg_photo

    def _draw_orb_premium(self, c):
        """PIL supersampling ile keskin olmayan, yumusak Aurora orb."""
        state = "PAUSED" if self.paused else self._ripperinio_state
        accent = self._state_color(state)
        accent_rgb = self._rgb(accent)
        sec_rgb = self._rgb(C_BLUE)
        peak = max(0.0, min(1.0, float(getattr(self, "_media_peak", 0.0) or 0.0)))
        beat = max(0.0, min(1.0, float(getattr(self, "_media_beat", 0.0) or 0.0)))
        activity = 0.20 if self.paused else (0.95 if self.speaking else 0.68 if self.user_speaking else 0.45)
        diameter = max(250, min(430, int(self.FACE * 1.32)))
        ss = 2
        size = diameter * ss
        img = Image.new("RGBA", (size,size), (0,0,0,0))
        cx = cy = size//2
        base = int(size*0.33)

        # soft glow layers
        glow = Image.new("RGBA", img.size, (0,0,0,0))
        gd = ImageDraw.Draw(glow)
        outer = int(base*(1.25 + beat*0.04))
        gd.ellipse((cx-outer,cy-outer,cx+outer,cy+outer), fill=(*accent_rgb, 45 + int(activity*28)))
        inner = int(base*0.70)
        gd.ellipse((cx-inner,cy-inner,cx+inner,cy+inner), fill=(*sec_rgb, 28))
        glow = glow.filter(ImageFilter.GaussianBlur(int(size*0.065)))
        img = Image.alpha_composite(img, glow)
        d = ImageDraw.Draw(img)

        # very subtle orbital tracks
        for mul, alpha, width in ((1.16,42,2),(0.98,62,2),(0.79,48,2)):
            r = int(base*mul)
            d.ellipse((cx-r,cy-r,cx+r,cy+r), outline=(119,151,199,alpha), width=width*ss)

        # animated arcs
        tick = float(self.tick)
        arcs = [
            (1.16, (tick*1.35)%360, 88+int(peak*46), accent_rgb, 230, 4),
            (0.98, (215-tick*0.55)%360, 56, sec_rgb, 195, 3),
            (0.79, (40+tick*0.32)%360, 34, (255,255,255), 110, 2),
        ]
        for mul,start,extent,rgb,alpha,width in arcs:
            r = int(base*mul)
            d.arc((cx-r,cy-r,cx+r,cy+r), start=start, end=start+extent,
                  fill=(*rgb,alpha), width=width*ss)

        # core: layered glass gradient
        core = int(base*(0.45 + beat*0.018))
        for i in range(22,0,-1):
            f = i/22
            rr = max(1,int(core*f))
            mix = tuple(int(13 + (accent_rgb[j]-13)*(1-f)*0.13) for j in range(3))
            alpha = 245
            d.ellipse((cx-rr,cy-rr,cx+rr,cy+rr), fill=(*mix,alpha))
        d.ellipse((cx-core,cy-core,cx+core,cy+core), outline=(*accent_rgb,205), width=2*ss)
        core2 = int(core*0.78)
        d.ellipse((cx-core2,cy-core2,cx+core2,cy+core2), outline=(180,210,255,70), width=1*ss)

        # three orbiting nodes: minimal decoration, no pixel brackets
        for i in range(3):
            ang = (tick*0.012 + i*math.tau/3.0)
            rr = int(base*1.16)
            nx = int(cx + math.cos(ang)*rr)
            ny = int(cy + math.sin(ang)*rr)
            nr = (3 if i == 0 else 2)*ss
            d.ellipse((nx-nr,ny-nr,nx+nr,ny+nr), fill=(*accent_rgb,220 if i==0 else 130))

        # supersampled downscale
        img = img.resize((diameter,diameter), Image.Resampling.LANCZOS)
        self._orb_premium_photo = ImageTk.PhotoImage(img)
        c.create_image(self.FCX, self.FCY, image=self._orb_premium_photo)

        # Text stays in Tk so theme/font changes remain live.
        c.create_text(self.FCX, self.FCY-4, text="R", fill=C_TEXT,
                      font=font_display(max(24,int(self.FACE*0.085))))
        c.create_text(self.FCX, self.FCY+29, text="RIP", fill="#8195B6",
                      font=font_body_bold(8))

    def _build_compact_dock(self):
        self._compact_state = "mini"
        self._compact_last_activity = time.monotonic()
        self._compact_hide_job = None
        self._compact_hover = False
        self._compact_win = tk.Toplevel(self.root)
        self._compact_win.withdraw()
        self._compact_win.overrideredirect(True)
        try:
            self._compact_win.attributes("-topmost", True)
            self._compact_win.attributes("-alpha", 0.97)
        except Exception:
            pass
        self._compact_win.configure(bg="#0B1220")
        self._compact_canvas = tk.Canvas(
            self._compact_win, bg="#0B1220", highlightthickness=0, bd=0,
        )
        self._compact_canvas.pack(fill="both", expand=True)
        self._compact_canvas.bind("<Button-1>", self._compact_click)
        self._compact_canvas.bind("<Double-Button-1>", lambda _e: self.show_dashboard())
        self._compact_canvas.bind("<Button-3>", lambda _e: self._compact_hide())
        self._compact_win.bind("<Enter>", lambda _e: setattr(self, "_compact_hover", True))
        self._compact_win.bind("<Leave>", lambda _e: setattr(self, "_compact_hover", False))
        self.root.after(120, self._compact_tick)

    def _compact_geometry(self, mode: str):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        if mode == "active":
            w, h = 430, 108
        else:
            w, h = 76, 76
        x = max(16, sw - w - 28)
        y = max(16, sh - h - 74)
        return w, h, x, y

    def _compact_show(self, mode: str = "active", reason: str = ""):
        if not hasattr(self, "_compact_win"):
            return
        mode = "active" if mode == "active" else "mini"
        self._compact_state = mode
        self._compact_last_activity = time.monotonic()
        w, h, x, y = self._compact_geometry(mode)
        try:
            self._compact_win.geometry(f"{w}x{h}+{x}+{y}")
            self._compact_canvas.configure(width=w, height=h)
            self._compact_win.deiconify()
            self._compact_win.lift()
            self._compact_draw()
        except Exception:
            pass

    def _compact_hide(self):
        try:
            self._compact_win.withdraw()
        except Exception:
            pass

    def _compact_activity(self, force=False):
        state = str(getattr(self, "_ripperinio_state", "STANDBY") or "STANDBY")
        if force or state in {"THINKING", "PROCESSING", "SPEAKING", "ERROR", "INITIALISING"}:
            self._compact_show("active", reason=state)
        else:
            self._compact_last_activity = time.monotonic()
            try:
                if not self._compact_win.winfo_viewable():
                    self._compact_show("mini")
            except Exception:
                pass

    def _compact_click(self, event):
        mode = getattr(self, "_compact_state", "mini")
        if mode == "mini":
            self._compact_show("active", reason="click")
            return
        w = max(1, self._compact_win.winfo_width())
        x = int(event.x)
        if x >= w - 58:
            self.show_dashboard()
        elif x >= w - 122:
            self.open_command_palette()
        elif x <= 76:
            self._compact_show("mini")

    def _compact_phone_text(self):
        try:
            status = dict(getattr(self.widget_manager, "_last_phone_status", {}) or {})
            if not status:
                return "Telefon offline"
            stamp = int(status.get("received_at", 0) or 0)
            age = max(0, int(time.time() - stamp/1000)) if stamp else 9999
            if age > 75:
                return "Telefon offline"
            batt = status.get("battery_percent", "?")
            return f"Telefon %{batt}"
        except Exception:
            return "Telefon --"

    def _compact_media_text(self):
        snap = dict(getattr(self, "_media_snapshot", {}) or {})
        title = str(snap.get("title") or "").strip()
        if title:
            return title[:34]
        return "RIP hazir"

    def _compact_draw(self):
        if not hasattr(self, "_compact_canvas"):
            return
        c = self._compact_canvas
        try:
            w = max(1, int(c.winfo_width()))
            h = max(1, int(c.winfo_height()))
        except Exception:
            return
        c.delete("all")
        bg = "#162033"
        edge = "#2B3B57"
        self._round_rect(c, 2, 2, w-2, h-2, 22, fill=bg, outline=edge, width=1)
        state = "PAUSED" if self.paused else str(getattr(self, "_ripperinio_state", "STANDBY"))
        col = self._state_color(state)
        beat = max(0.0, min(1.0, float(getattr(self, "_media_beat", 0.0) or 0.0)))
        if getattr(self, "_compact_state", "mini") == "mini":
            cx, cy = w//2, h//2
            rr = 22 + int(3*beat)
            c.create_oval(cx-rr-5, cy-rr-5, cx+rr+5, cy+rr+5, outline="#243650", width=2)
            c.create_arc(cx-rr-4, cy-rr-4, cx+rr+4, cy+rr+4,
                         start=(self.tick*2.2)%360, extent=92, style="arc", outline=col, width=3)
            c.create_oval(cx-13, cy-13, cx+13, cy+13, fill="#111D2F", outline=col, width=2)
            c.create_text(cx, cy, text="R", fill=C_TEXT, font=font_display(14))
            return

        # Active dock
        cx, cy = 42, h//2
        rr = 23 + int(4*beat)
        c.create_oval(cx-rr-5, cy-rr-5, cx+rr+5, cy+rr+5, outline="#243650", width=2)
        c.create_arc(cx-rr-4, cy-rr-4, cx+rr+4, cy+rr+4,
                     start=(self.tick*2.0)%360, extent=110, style="arc", outline=col, width=3)
        c.create_oval(cx-13, cy-13, cx+13, cy+13, fill="#111D2F", outline=col, width=2)
        c.create_text(cx, cy, text="R", fill=C_TEXT, font=font_display(14))

        c.create_text(78, 27, text="R.I.P", anchor="w", fill=C_TEXT, font=font_display(13))
        c.create_text(78, 50, text=f"● {state.title()}", anchor="w", fill=col, font=font_body_bold(9))
        c.create_text(78, 77, text=self._compact_media_text(), anchor="w", fill="#BFD1EA", font=font_body(9))
        c.create_text(250, 27, text=self._compact_phone_text(), anchor="w", fill="#9CB3D4", font=font_body(8))
        c.create_text(250, 50, text="Ctrl+Space · komut", anchor="w", fill="#7F96B8", font=font_body(8))

        # CMD + Dashboard touch targets
        self._round_rect(c, w-120, h-38, w-64, h-10, 10, fill="#111D2F", outline="#334766")
        c.create_text(w-92, h-24, text="CMD", fill=C_PRI, font=font_body_bold(8))
        self._round_rect(c, w-56, h-38, w-10, h-10, 10, fill="#111D2F", outline="#334766")
        c.create_text(w-33, h-24, text="HUD", fill=C_BLUE, font=font_body_bold(8))

    def _compact_tick(self):
        try:
            visible = bool(self._compact_win.winfo_viewable())
            state = str(getattr(self, "_ripperinio_state", "STANDBY") or "STANDBY")
            active_state = state in {"THINKING", "PROCESSING", "SPEAKING", "ERROR", "INITIALISING"}
            if active_state:
                if not visible or getattr(self, "_compact_state", "mini") != "active":
                    self._compact_show("active", reason=state)
                self._compact_last_activity = time.monotonic()
            elif visible and getattr(self, "_compact_state", "mini") == "active":
                if not self._compact_hover and time.monotonic() - self._compact_last_activity > 7.5:
                    self._compact_show("mini", reason="idle")
            if visible:
                self._compact_draw()
        except Exception:
            pass
        try:
            self.root.after(180, self._compact_tick)
        except Exception:
            pass

    def show_dashboard(self):
        try:
            self._compact_show("mini")
            self.root.deiconify()
            self.root.attributes("-fullscreen", False)
            self._fullscreen = False
            self.root.geometry(self._window_geometry)
            self.root.lift()
            self.root.focus_force()
            self._resize_surface(*self._normal_size)
            self._draw()
        except Exception:
            pass

    def hide_dashboard(self):
        try:
            self.root.withdraw()
        except Exception:
            pass
        self._compact_show("mini", reason="dashboard-hide")

    def toggle_dashboard(self):
        try:
            if self.root.state() == "withdrawn" or not self.root.winfo_viewable():
                self.show_dashboard()
            else:
                self.hide_dashboard()
        except Exception:
            self.show_dashboard()
        return "break"

    # ── Voice ─────────────────────────────────────────────────────────────────
    def _load_voice(self) -> str:
        try:
            return str(load_app_config().get("voice", "Charon") or "Charon")
        except Exception:
            return "Charon"

    # ── Voice input gate / PTT ──────────────────────────────────────────────────
    def _set_voice_input_mode(self, mode: str, persist: bool = True):
        mode = str(mode or "continuous").strip().casefold()
        if mode not in {"continuous", "ptt", "wake"}:
            mode = "continuous"
        self.voice_input_mode = mode
        self._ptt_active = False
        if persist:
            try:
                save_app_config({"voice_input_mode": mode})
            except Exception:
                pass
        labels = {"continuous": "SÜREKLİ", "ptt": "PUSH-TO-TALK (F8)", "wake": "HEY"}
        self.write_log(f"SYS: Ses girişi → {labels.get(mode, mode)}")
        self._refresh_settings_status()
        cb = getattr(self, "on_voice_input_mode_change", None)
        if callable(cb):
            try:
                cb(mode)
            except Exception:
                pass

    def _cycle_voice_input_mode(self):
        order = ["continuous", "ptt", "wake"]
        current = getattr(self, "voice_input_mode", "continuous")
        try:
            nxt = order[(order.index(current) + 1) % len(order)]
        except Exception:
            nxt = "continuous"
        self._set_voice_input_mode(nxt)
        return "break"

    def _ptt_press(self, _event=None):
        if getattr(self, "_ptt_active", False):
            return "break"
        self._ptt_active = True
        cb = getattr(self, "on_ptt_change", None)
        if callable(cb):
            try:
                cb(True)
            except Exception:
                pass
        if getattr(self, "voice_input_mode", "continuous") == "ptt":
            self.set_state("LISTENING")
        return "break"

    def _ptt_release(self, _event=None):
        if not getattr(self, "_ptt_active", False):
            return "break"
        self._ptt_active = False
        cb = getattr(self, "on_ptt_change", None)
        if callable(cb):
            try:
                cb(False)
            except Exception:
                pass
        return "break"

    def _toggle_widgets(self):
        """F10 = HUD edit mode. Widget gizleme edit modunda sağ tıkla yapılır."""
        try:
            enabled = self.widget_manager.toggle_edit_mode()
            self.write_log(f"SYS: HUD EDIT MODE {'AÇIK' if enabled else 'KAPALI'}.")
        except Exception as exc:
            self.write_debug(f"HUD edit mode: {exc}", level="WARN")
        return "break"

    def show_edit_mode_banner(self, enabled: bool):
        try:
            if enabled:
                self._edit_banner = tk.Label(
                    self.root, text="F10 · HUD EDIT MODE   SÜRÜKLE · KÖŞEDEN BOYUTLANDIR · CTRL+TEKER OPACITY · SAĞ TIK GİZLE",
                    fg="#001313", bg="#00dcff", font=font_body_bold(10), padx=14, pady=6,
                )
                self._edit_banner.place(relx=0.5, y=72, anchor="n")
                self._edit_banner.lift()
            else:
                banner = getattr(self, "_edit_banner", None)
                if banner is not None:
                    banner.destroy()
                self._edit_banner = None
        except Exception:
            pass

    def update_phone_widget(self, status: dict):
        status = status or {}
        try:
            stamp = int(status.get("received_at", 0) or 0)
            age = max(0.0, time.time() - stamp / 1000.0) if stamp else 99999.0
            self._phone_connected = bool(status) and age <= 75.0
            self.widget_manager.update_phone(status)
            # Keep the lower-left RIPPERINIO TELEFON chip in sync with the
            # same live heartbeat that drives the PHONE LIVE widget.
            self._draw_phone_button()
        except Exception:
            pass

    def add_notification(self, record: dict):
        try:
            self.widget_manager.add_notification(record or {})
        except Exception:
            pass

    def notify_internal(self, title: str, body: str):
        record = {
            "source": "RIP", "app": "RIP", "title": str(title or "RIP"),
            "text": str(body or ""), "received_at": int(time.time() * 1000),
        }
        self.add_notification(record)
        self.write_log(f"SYS: {title} · {body}")
        try:
            self._compact_activity(force=True)
        except Exception:
            pass

    def show_screen_target(self, result: dict):
        try:
            self.widget_manager.show_screen_target(result or {})
        except Exception:
            pass

    def open_command_palette(self):
        try:
            self._compact_show("active", reason="command")
            self.widget_manager.command_palette.show()
        except Exception:
            pass

    def set_media_peak(self, peak: float):
        try:
            raw = max(0.0, min(1.0, float(peak)))
        except Exception:
            raw = 0.0
        self._media_peak_raw = raw
        previous = float(getattr(self, "_media_peak", 0.0) or 0.0)
        # Hizli attack / yavas decay: ritim vuruslari kaybolmaz, titreme azalir.
        if raw >= previous:
            smooth = previous * 0.22 + raw * 0.78
        else:
            smooth = previous * 0.86 + raw * 0.14
        self._media_peak = max(0.0, min(1.0, smooth))

        energy = getattr(self, "_media_energy", None)
        if energy is not None:
            avg = (sum(energy) / max(1, len(energy))) if len(energy) else 0.0
            beat = float(getattr(self, "_media_beat", 0.0) or 0.0) * 0.78
            if raw > 0.075 and raw > max(0.095, avg * 1.50):
                beat = min(1.0, 0.58 + raw * 0.75)
            self._media_beat = beat
            energy.append(raw)

        # Coklu halka hareketi: her bar ayni boyda degil; gercek peak ile beslenen
        # farkli decay/phase tepkileri daha organik bir visualizer verir.
        bins = getattr(self, "_media_wave_bins", None)
        if isinstance(bins, list):
            for i in range(len(bins)):
                phase = 0.52 + 0.48 * abs(math.sin(i * 0.71 + self.tick * 0.045))
                target = raw * phase * (1.0 + self._media_beat * (0.28 if i % 4 == 0 else 0.10))
                oldv = float(bins[i] or 0.0)
                bins[i] = target if target > oldv else oldv * 0.82 + target * 0.18

    def show_incoming_call(self, app: str, caller: str, call_id: str = "", avatar_b64: str = "", can_accept: bool = True, can_decline: bool = True, action_source: str = ""):
        try:
            self._call_card.show(app, caller, call_id, avatar_b64, can_accept, can_decline, action_source)
        except Exception:
            pass

    def hide_incoming_call(self):
        try:
            self._call_card.hide()
        except Exception:
            pass

    # ── Live theme ────────────────────────────────────────────────────────────
    def _theme_color_map(self, old_palette: dict, new_palette: dict) -> dict:
        mapping = {}
        for key in ("bg", "pri", "mid", "dim", "dimmer", "text", "panel", "secondary", "listening", "speaking"):
            old = str(old_palette.get(key, "") or "").lower()
            new = str(new_palette.get(key, "") or "")
            if old and new:
                mapping[old] = new
        extras = {
            "#0D1728": new_palette["panel"], "#0D1728": new_palette["panel"],
            "#0D1728": new_palette["panel"], "#111D2F": new_palette["dimmer"],
            "#111827": new_palette["bg"], "#0D1728": new_palette["dimmer"],
            "#16243A": new_palette["dim"], "#16243A": new_palette["dim"],
            "#17263C": new_palette["dim"], "#8195B6": new_palette["mid"],
            "#8195B6": new_palette["mid"], "#90A4C2": new_palette["mid"],
            "#D9E6F7": new_palette["text"], "#E7F0FC": new_palette["text"],
        }
        mapping.update({k.lower(): v for k, v in extras.items()})
        return mapping

    def _recolor_widget_tree(self, widget, color_map: dict):
        for option in (
            "background", "foreground", "activebackground", "activeforeground",
            "highlightbackground", "highlightcolor", "selectbackground",
            "selectforeground", "troughcolor", "insertbackground",
        ):
            try:
                current = str(widget.cget(option) or "").lower()
                replacement = color_map.get(current)
                if replacement:
                    widget.configure(**{option: replacement})
            except Exception:
                pass
        try:
            if isinstance(widget, tk.Canvas):
                for item in widget.find_all():
                    for option in ("fill", "outline", "activefill", "activeoutline"):
                        try:
                            current = str(widget.itemcget(item, option) or "").lower()
                            replacement = color_map.get(current)
                            if replacement:
                                widget.itemconfigure(item, **{option: replacement})
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            children = widget.winfo_children()
        except Exception:
            children = []
        for child in children:
            self._recolor_widget_tree(child, color_map)

    def apply_hud_theme_live(self, name: str, persist: bool = False, announce: bool = True):
        global _HUD_THEME_NAME, _HUD_THEME
        global C_BG, C_PRI, C_MID, C_DIM, C_DIMMER, C_TEXT, C_PANEL, C_BLUE, C_GREEN

        old_palette = dict(_HUD_THEME)
        requested = str(name or "breeze").strip().casefold()
        if requested == "breeze":
            new_name = "breeze"
            new_palette = dict(BREEZE_PALETTE)
        else:
            if persist:
                set_hud_theme(requested)
            new_name = get_hud_theme() if persist else requested
            new_palette = get_hud_theme_palette(new_name)
            if not new_palette:
                return
        _HUD_THEME_NAME = new_name
        _HUD_THEME = dict(new_palette)
        C_BG = new_palette["bg"]
        C_PRI = new_palette["pri"]
        C_MID = new_palette["mid"]
        C_DIM = new_palette["dim"]
        C_DIMMER = new_palette["dimmer"]
        C_TEXT = new_palette["text"]
        C_PANEL = new_palette["panel"]
        C_BLUE = new_palette["secondary"]
        C_GREEN = new_palette["listening"]
        ORB_COLORS["LISTENING"] = _hex_rgb(new_palette["listening"])
        ORB_COLORS["SPEAKING"] = _hex_rgb(new_palette["speaking"])
        STATE_HEX_COLORS["LISTENING"] = new_palette["listening"]
        STATE_HEX_COLORS["SPEAKING"] = new_palette["speaking"]
        try:
            self.root.configure(bg=C_BG)
            self._recolor_widget_tree(self.root, self._theme_color_map(old_palette, new_palette))
            self._draw()
            self.root.update_idletasks()
        except Exception:
            pass
        try:
            if hasattr(self, "_theme_var"):
                self._theme_var.set(str(new_name))
        except Exception:
            pass
        if announce:
            self.write_log(f"SYS: Tema → {new_palette.get('label', new_name)}")

    def _poll_theme_config(self):
        try:
            # Breeze varsayilan ve sabit kalir. Eski tema secilirse theme_control izlenir.
            current = str(globals().get("_HUD_THEME_NAME") or "breeze")
            if current != "breeze":
                name = get_hud_theme()
                if name != current:
                    self.apply_hud_theme_live(name, persist=False, announce=False)
        except Exception:
            pass
        try:
            self.root.after(1200, self._poll_theme_config)
        except Exception:
            pass

    # ── Social bar ───────────────────────────────────────────────────────────
    def _build_social_bar(self):
        ICON_SIZE = 28
        ICON_DIR = resource_path("Icon")

        bar = tk.Frame(self.root, bg=C_BG)
        self._social_bar = bar

        def _open_instagram(_event=None):
            def worker():
                try:
                    result = open_app("Instagram")
                    if "bulunamadi" in (result or "").casefold() or "bulunamadı" in (result or "").casefold():
                        open_path("https://www.instagram.com/")
                except Exception:
                    try:
                        open_path("https://www.instagram.com/")
                    except Exception:
                        pass
            threading.Thread(target=worker, daemon=True).start()

        def _open_youtube(_event=None):
            # Kullanıcının isteği: YouTube uygulaması değil, varsayılan tarayıcı.
            try:
                open_path("https://www.youtube.com/")
            except Exception:
                pass

        def _load_icon(filename: str):
            try:
                img = Image.open(ICON_DIR / filename).convert("RGBA")
                img = img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
                return ImageTk.PhotoImage(img)
            except Exception:
                return None

        name_lbl = tk.Label(
            bar, text="Miralay\nÜnlü",
            fg="#8195B6", bg=C_BG,
            font=font_display(14), cursor="hand2",
            justify="left",
        )
        name_lbl.pack(side="left", padx=(0, 10))
        name_lbl.bind("<Button-1>", _open_instagram)

        self._icon_ig = _load_icon("instagram-logo.png")
        self._icon_yt = _load_icon("youtube-logo.png")

        if self._icon_ig:
            ig_lbl = tk.Label(bar, image=self._icon_ig, bg=C_BG, cursor="hand2")
            ig_lbl.pack(side="left", padx=4)
            ig_lbl.bind("<Button-1>", _open_instagram)

        if self._icon_yt:
            yt_lbl = tk.Label(bar, image=self._icon_yt, bg=C_BG, cursor="hand2")
            yt_lbl.pack(side="left", padx=4)
            yt_lbl.bind("<Button-1>", _open_youtube)

    # ── ŞİMDİ ÇALIYOR / MEDYA HUD ───────────────────────────────────────────
    def _media_button(self, parent, text, command, fg=C_PRI, width=8):
        return tk.Button(
            parent, text=text, command=command,
            fg=fg, bg="#111D2F",
            activeforeground=C_TEXT, activebackground="#172943",
            font=font_body_bold(9), borderwidth=0, relief="flat", cursor="hand2",
            highlightthickness=0, width=width,
        )

    def _build_media_panel(self):
        panel = tk.Frame(
            self.root, bg="#0D1728",
            highlightbackground="#0D1728", highlightthickness=0,
        )
        self._media_frame = panel

        self._media_header = tk.Label(
            panel, text="ŞİMDİ ÇALIYOR", fg=C_PRI, bg="#0D1728",
            font=font_body_bold(9), anchor="w",
        )
        self._media_source = tk.Label(
            panel, text="SİSTEM MEDYASI", fg="#7185A5", bg="#0D1728",
            font=font_body_bold(7), anchor="e",
        )
        self._media_thumb = tk.Label(
            panel, bg="#09111E", bd=0,
            highlightthickness=0,
        )
        self._media_title = tk.Label(
            panel, text="Medya bekleniyor...", fg=C_TEXT, bg="#0D1728",
            font=font_body_bold(10), anchor="w", justify="left",
        )
        self._media_artist = tk.Label(
            panel, text="", fg="#8195B6", bg="#0D1728",
            font=font_body(9), anchor="w", justify="left",
        )
        self._media_progress = tk.Canvas(
            panel, bg="#0D1728", highlightthickness=0, bd=0, height=8
        )
        self._media_time = tk.Label(
            panel, text="--:-- / --:--", fg=C_MID, bg="#0D1728",
            font=font_body(8), anchor="e",
        )

        self._media_vol_minus = self._media_button(
            panel, "🔉 -10", lambda: self._media_volume_action("change", -10)
        )
        self._media_vol_value = tk.Label(
            panel, text="SES --%", fg=C_PRI, bg="#111D2F",
            font=font_body_bold(9), highlightthickness=1,
            highlightbackground="#243650",
        )
        self._media_vol_plus = self._media_button(
            panel, "🔊 +10", lambda: self._media_volume_action("change", 10)
        )
        self._media_mute_btn = self._media_button(
            panel, "🔇", lambda: self._media_volume_action("toggle_mute"),
            fg=C_TEXT, width=4,
        )

        self._media_prev = self._media_button(
            panel, "⏮", lambda: self._media_playback_action("previous"), width=6
        )
        self._media_play = self._media_button(
            panel, "▶ BAŞLAT", lambda: self._media_playback_action("play_pause"), width=10
        )
        self._media_next = self._media_button(
            panel, "⏭", lambda: self._media_playback_action("next"), width=6
        )

        self._layout_media_panel()
        self._render_media_placeholder()

    def _layout_media_panel(self):
        if not hasattr(self, "_media_frame"):
            return

        w = max(180, int(getattr(self, "MEDIA_W", self.CHAT_W)))
        h = max(210, int(getattr(self, "MEDIA_H", 260)))
        pad = 8

        self._media_frame.place(x=self.MEDIA_X, y=self.MEDIA_Y, width=w, height=h)
        self._media_header.place(x=pad, y=6, width=max(90, w // 2 - pad), height=18)
        self._media_source.place(x=w // 2, y=6, width=max(70, w // 2 - pad), height=18)

        play_h = 34
        vol_h = 31
        play_y = h - play_h - 7
        vol_y = play_y - vol_h - 6

        thumb_y = 27
        thumb_h = max(72, min(126, int(h * 0.39)))
        thumb_h = min(thumb_h, max(68, vol_y - thumb_y - 49))
        self._media_thumb.place(x=pad, y=thumb_y, width=w - pad * 2, height=thumb_h)

        info_y = thumb_y + thumb_h + 3
        info_h = max(46, vol_y - info_y - 3)
        progress_h = 8
        time_h = 14
        title_h = min(24, max(17, (info_h - progress_h - time_h) // 2 + 2))
        artist_h = max(14, info_h - title_h - progress_h - time_h)
        self._media_title.place(x=pad, y=info_y, width=w - pad * 2, height=title_h)
        self._media_artist.place(
            x=pad, y=info_y + title_h, width=w - pad * 2, height=artist_h,
        )
        progress_y = info_y + title_h + artist_h
        self._media_progress.place(x=pad, y=progress_y, width=w-pad*2, height=progress_h)
        self._media_time.place(x=pad, y=progress_y+progress_h, width=w-pad*2, height=time_h)

        usable = w - pad * 2
        gap = 4
        mute_w = 38
        cell = max(43, (usable - mute_w - gap * 3) // 3)
        x = pad
        self._media_vol_minus.place(x=x, y=vol_y, width=cell, height=vol_h)
        x += cell + gap
        self._media_vol_value.place(x=x, y=vol_y, width=cell, height=vol_h)
        x += cell + gap
        self._media_vol_plus.place(x=x, y=vol_y, width=cell, height=vol_h)
        x += cell + gap
        self._media_mute_btn.place(
            x=x, y=vol_y, width=max(34, usable - (x - pad)), height=vol_h
        )

        third = (usable - gap * 2) // 3
        self._media_prev.place(x=pad, y=play_y, width=third, height=play_h)
        self._media_play.place(x=pad + third + gap, y=play_y, width=third, height=play_h)
        self._media_next.place(x=pad + (third + gap) * 2, y=play_y, width=third, height=play_h)

        self._render_media_thumbnail(self._media_snapshot.get("thumbnail", b""), force=True)

    def _render_media_placeholder(self):
        self._render_media_thumbnail(b"", force=True)

    def _render_media_thumbnail(self, raw: bytes, force: bool = False):
        if not hasattr(self, "_media_thumb"):
            return

        target_w = max(120, int(getattr(self, "MEDIA_W", 280)) - 16)
        target_h = max(68, min(126, int(getattr(self, "MEDIA_H", 260) * 0.39)))
        key = (len(raw or b""), hash(raw or b""), target_w, target_h)
        if not force and key == self._media_thumb_key:
            return

        try:
            if raw:
                from io import BytesIO
                image = Image.open(BytesIO(raw)).convert("RGB")
                image = ImageOps.fit(image, (target_w, target_h), method=Image.LANCZOS)
            else:
                image = Image.new("RGB", (target_w, target_h), "#09111E")
                draw = ImageDraw.Draw(image)
                draw.rectangle((0, 0, target_w - 1, target_h - 1), outline="#243650")
                cx, cy = target_w // 2, target_h // 2
                r = max(12, min(target_w, target_h) // 7)
                draw.ellipse((cx-r, cy-r, cx+r, cy+r), outline="#345274", width=2)
                draw.polygon(
                    [(cx-r//3, cy-r//2), (cx-r//3, cy+r//2), (cx+r//2, cy)],
                    fill="#5EEBFF",
                )
            self._media_thumb_photo = ImageTk.PhotoImage(image)
            self._media_thumb.configure(image=self._media_thumb_photo)
            self._media_thumb_key = key
        except Exception:
            pass

    def _media_playback_action(self, action: str):
        def worker():
            try:
                control_media(action)
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _media_volume_action(self, action: str, value=None):
        def worker():
            try:
                control_volume(action, value)
                self._media_volume_snapshot = self._media_volume.get_state()
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _start_media_monitor(self):
        if self._media_thread is None or not self._media_thread.is_alive():
            self._media_stop_event.clear()

            def media_worker():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    while not self._media_stop_event.is_set():
                        try:
                            fresh = loop.run_until_complete(
                                self._media_reader.read_async()
                            )
                            if isinstance(fresh, dict):
                                fresh["_read_monotonic"] = time.monotonic()
                            self._media_snapshot = fresh
                        except Exception as exc:
                            previous = dict(self._media_snapshot or {})
                            previous["error"] = str(exc)
                            self._media_snapshot = previous
                        self._media_stop_event.wait(0.70)
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass

            self._media_thread = threading.Thread(
                target=media_worker, name="ripperinio-media-metadata", daemon=True
            )
            self._media_thread.start()

        if self._media_volume_thread is None or not self._media_volume_thread.is_alive():
            def volume_worker():
                while not self._media_stop_event.is_set():
                    try:
                        self._media_volume_snapshot = self._media_volume.get_state()
                    except Exception as exc:
                        self._media_volume_snapshot = {
                            "available": False, "volume": 0,
                            "muted": False, "error": str(exc),
                        }
                    self._media_stop_event.wait(0.25)

            self._media_volume_thread = threading.Thread(
                target=volume_worker, name="ripperinio-volume-live", daemon=True
            )
            self._media_volume_thread.start()

        self.root.after(150, self._refresh_media_panel_ui)

    def _refresh_media_panel_ui(self):
        if not hasattr(self, "_media_frame"):
            return

        snap = dict(getattr(self, "_media_snapshot", {}) or {})
        active = bool(snap.get("active"))
        title = str(snap.get("title", "") or "").strip()
        artist = str(snap.get("artist", "") or "").strip()
        album = str(snap.get("album", "") or "").strip()
        source = str(snap.get("source", "SİSTEM MEDYASI") or "SİSTEM MEDYASI")
        status = str(snap.get("status", "IDLE") or "IDLE").upper()

        status_tr = "ÇALIYOR" if "PLAYING" in status else "DURAKLATILDI" if "PAUSED" in status else "BEKLEMEDE"
        self._media_source.configure(text=f"{source} · {status_tr}")

        if active:
            self._media_title.configure(text=title or "Bilinmeyen medya")
            self._media_artist.configure(text=artist or album or "Windows medya oturumu")
        else:
            self._media_title.configure(text="Medya bekleniyor...")
            self._media_artist.configure(text="YouTube / Spotify açınca burada görünür")

        if "PLAYING" in status:
            self._media_play.configure(text="⏸ DURDUR")
        else:
            self._media_play.configure(text="▶ BAŞLAT")

        vol = dict(getattr(self, "_media_volume_snapshot", {}) or {})
        if vol.get("available"):
            percent = int(vol.get("volume", 0) or 0)
            muted = bool(vol.get("muted"))
            self._media_vol_value.configure(text=f"SES {percent}%")
            self._media_mute_btn.configure(text="🔈" if muted else "🔇")
        else:
            self._media_vol_value.configure(text="SES --%")

        try:
            pos = max(0.0, float(snap.get("position_seconds", 0.0) or 0.0))
            dur = max(0.0, float(snap.get("duration_seconds", 0.0) or 0.0))
            # Windows medya oturumu konumu bazen yalniz metadata yenilenince atlar.
            # PLAYING iken son okuma zamanindan itibaren lokalde ilerleterek bar/sure akar.
            read_at = float(snap.get("_read_monotonic", 0.0) or 0.0)
            if "PLAYING" in status and read_at > 0:
                pos += max(0.0, min(2.0, time.monotonic() - read_at))
            if dur > 0:
                pos = min(pos, dur)
            pw = max(20, int(self._media_progress.winfo_width() or 20))
            self._media_progress.delete("all")
            cy = 4
            self._media_progress.create_line(0, cy, pw, cy, fill="#0a3835", width=3)
            if dur > 0:
                frac = max(0.0, min(1.0, pos / dur))
                self._media_progress.create_line(0, cy, int(pw * frac), cy, fill=C_PRI, width=3)
                rem = max(0.0, dur - pos)
                fmt = lambda sec: f"{int(sec)//60:02d}:{int(sec)%60:02d}"
                self._media_time.configure(text=f"{fmt(pos)}  ·  -{fmt(rem)}")
            else:
                self._media_time.configure(text="--:-- / --:--")
        except Exception:
            pass

        self._render_media_thumbnail(snap.get("thumbnail", b""))
        try:
            self.root.after(250, self._refresh_media_panel_ui)
        except Exception:
            pass

    # ── RIPPERINIO Telefon paneli ────────────────────────────────────────────────
    def _build_phone_panel(self):
        geo = self._phone_geometry
        PANEL_BG = "#0D1728"

        self._phone_btn_canvas = tk.Canvas(
            self.root, width=geo["btn_w"], height=geo["btn_h"],
            bg=C_BG, highlightthickness=0, cursor="hand2",
        )
        self._phone_btn_canvas.bind("<Button-1>", lambda e: self._toggle_phone_panel())
        self._draw_phone_button()

        panel = tk.Frame(self.root, bg=PANEL_BG,
                         highlightbackground="#243650", highlightthickness=1)
        panel.place_forget()
        self._phone_panel = panel

        self._phone_title = tk.Label(panel, text="RIPPERINIO TELEFON", fg=C_PRI,
                                     bg=PANEL_BG, font=font_display(11))
        # Hangi surumu calistirdigini tahmin etmeye gerek kalmasin
        self._phone_version = tk.Label(panel, text=STAMP, fg=C_DIM, bg=PANEL_BG,
                                       font=font_body(7), anchor="e")
        self._phone_close = tk.Label(panel, text="✕", fg=C_MID, bg=PANEL_BG,
                                     font=font_body_bold(11), cursor="hand2")
        self._phone_close.bind("<Button-1>", lambda e: self._toggle_phone_panel())

        self._phone_status = tk.Label(panel, text="Kapali", fg=C_MID, bg=PANEL_BG,
                                      font=font_body(9), anchor="w", justify="left")

        # QR alani
        self._phone_qr_label = tk.Label(panel, bg=PANEL_BG, fg=C_MID,
                                        text="QR kod baslatinca gorunur",
                                        font=font_body(9))

        self._phone_addr = tk.Label(panel, text="", fg=C_TEXT, bg=PANEL_BG,
                                    font=font_body(8), anchor="w", justify="left",
                                    wraplength=geo["panel_w"] - 28)

        # Token satiri + kopyalama
        self._phone_token_caption = tk.Label(panel, text="TOKEN", fg=C_MID, bg=PANEL_BG,
                                             font=font_body_bold(8), anchor="w")
        self._phone_token_lbl = tk.Label(panel, text="", fg="#D9E6F7", bg=PANEL_BG,
                                         font=font_body(8), anchor="w",
                                         wraplength=geo["panel_w"] - 28)
        self._phone_copy = tk.Label(panel, text="⧉ KOPYALA", fg=C_DIM, bg=PANEL_BG,
                                    font=font_body_bold(8), cursor="hand2")
        self._phone_copy.bind("<Button-1>", lambda e: self._copy_phone_token())

        # Baslat / Durdur
        self._phone_action = tk.Canvas(panel, height=32, bg=PANEL_BG,
                                       highlightthickness=0, cursor="hand2")
        self._phone_action.bind("<Button-1>", lambda e: self._phone_start_stop())
        self._draw_phone_action()

    def _place_phone_widgets(self):
        """Dugme sosyal barin hemen ustunde; panel yukari dogru acilir."""
        if not hasattr(self, "_phone_btn_canvas"):
            return
        geo = self._phone_geometry
        btn_y = self.H - FOOTER_H - 52 - geo["btn_h"] - 10
        self._phone_btn_canvas.place(x=14, y=btn_y)
        # DIKKAT: Canvas uzerinde HEM lift() HEM tkraise() canvas OGELERINI
        # kaldiran tag_raise'e eslenir ve tagOrId ister. Araci one almak icin
        # widget seviyesindeki metodu acikca cagirmak gerekir.
        tk.Misc.tkraise(self._phone_btn_canvas)

        if not self._phone_open:
            self._phone_panel.place_forget()
            return

        available = btn_y - (HDR_H + 8) - 8
        panel_h = max(300, min(geo["panel_h"], available))
        panel_y = max(HDR_H + 8, btn_y - panel_h - 8)
        self._phone_panel.place(x=14, y=panel_y,
                                width=geo["panel_w"], height=panel_h)
        tk.Misc.tkraise(self._phone_panel)
        self._layout_phone_controls(geo["panel_w"], panel_h)

    def _layout_phone_controls(self, w: int, h: int):
        pad = 14
        inner = w - pad * 2
        self._phone_title.place(x=pad, y=10)
        self._phone_close.place(x=w - 28, y=8)
        self._phone_status.place(x=pad, y=32, width=inner)

        action_h = 32
        # QR icin kalan alan dusulur: adres + TOKEN basligi + token satiri + buton.
        # Token artik TAM gosterildigi icin iki satir yer kapliyor.
        reserved = 56 + 34 + 26 + 22 + action_h + 20
        qr_size = max(96, min(200, inner, h - reserved))
        self._phone_qr_label.place(x=(w - qr_size) // 2, y=56,
                                   width=qr_size, height=qr_size)

        y = 56 + qr_size + 8
        self._phone_addr.place(x=pad, y=y, width=inner)
        y += 30
        # TOKEN basligi solda, KOPYALA sagda, token TAM olarak altta
        self._phone_token_caption.place(x=pad, y=y)
        self._phone_copy.place(x=w - pad - 80, y=y)
        self._phone_token_lbl.place(x=pad, y=y + 15, width=inner)

        # Surum damgasi: baslik yaninda yer yok, DURDUR'un hemen ustune koy
        self._phone_version.place(x=pad, y=h - action_h - 30, width=inner)

        self._phone_action.configure(width=inner)
        self._phone_action.place(x=pad, y=h - action_h - 12, width=inner)
        self._draw_phone_action()

    def _draw_phone_button(self):
        # The persistent bridge can be healthy while the Android phone is not
        # actually connected. The old chip only looked at PhoneServer.state,
        # so it could stay grey even while PHONE LIVE was receiving fresh
        # heartbeats. Prefer the real Android heartbeat tracked by
        # update_phone_widget(); keep bridge health only as a secondary state.
        connected = bool(getattr(self, "_phone_connected", False))
        bridge_running = bool(self._phone_server and self._phone_server.state == "running")
        if connected:
            accent, label, active = C_GREEN, "●  TELEFON  BAĞLI", True
        elif bridge_running:
            accent, label, active = C_BLUE, "◉  BRIDGE  HAZIR", True
        else:
            accent, label, active = (C_BLUE if self._phone_open else "#7D91B0"), "○  RIPPERINIO TELEFON", self._phone_open
        label += "   ▾" if self._phone_open else "   ▸"
        self._draw_pill(self._phone_btn_canvas, label, accent, active=active, font_size=9)

    def _draw_phone_action(self):
        state = self._phone_server.state if self._phone_server else "stopped"
        if state == "running":
            col, label, active = C_GREEN, "↻  YENİLE", True
        elif state == "starting":
            col, label, active = C_GOLD, "…  BAŞLATILIYOR", True
        else:
            col, label, active = C_GREEN, "▶  BAŞLAT", False
        self._draw_pill(self._phone_action, label, col, active=active, font_size=9)

    def _toggle_phone_panel(self):
        self._phone_open = not self._phone_open
        self._draw_phone_button()
        self._place_layout_widgets()

    def _auto_start_phone_connection(self):
        """RIP acilisinda Persistent Bridge'i otomatik ve sessizce baslat."""
        try:
            from ripperinio_web.launcher import PhoneServer

            if self._phone_server is None:
                self._phone_server = PhoneServer()

            if self._phone_server.state == "starting":
                return

            self.write_log("SYS: Telefon bağlantısı otomatik başlatılıyor...")
            self._phone_server.start(self._phone_on_update)
            self._draw_phone_action()
            self._draw_phone_button()
        except Exception as exc:
            # Telefon bridge'i acilamasa bile RIP'in kendisi kapanmasin.
            try:
                self.write_debug(
                    f"Telefon otomatik başlatma hatası: {exc}",
                    level="WARN",
                )
                self._draw_phone_action()
                self._draw_phone_button()
            except Exception:
                pass

    def _phone_start_stop(self):
        from ripperinio_web.launcher import PhoneServer

        if self._phone_server is None:
            self._phone_server = PhoneServer()

        if self._phone_server.state == "starting":
            self.write_log("SYS: RIPPERINIO Telefon zaten baslatiliyor...")
        elif self._phone_server.state == "running":
            self.write_log("SYS: RIPPERINIO Telefon durumu yenileniyor...")
            self._phone_server.start(self._phone_on_update)
        else:
            self.write_log("SYS: RIPPERINIO Telefon baslatiliyor...")
            self._phone_server.start(self._phone_on_update)
        self._draw_phone_action()
        self._draw_phone_button()

    def _phone_on_update(self, info: dict):
        # Arka plan thread'inden gelir — Tk yalnizca ana thread'den guncellenebilir
        self.root.after(0, lambda: self._apply_phone_state(info))

    def _apply_phone_state(self, info: dict):
        try:
            state = info.get("state", "stopped")
            self._phone_status.configure(
                text=info.get("message", ""),
                fg=C_GREEN if state == "running" else (C_RED if state == "error" else C_GOLD),
            )
            url = info.get("url", "")
            token = info.get("token", "")

            if state == "running" and url:
                self._render_phone_qr(url)
                # Tunel yoksa yesil "her sey yolunda" yaniltici olur: telefon
                # ayni Wi-Fi'da olmali ve sertifika uyarisini gecmeli. Altin
                # renk + acik metin, kullaniciyi dogru adima yonlendirir.
                if not info.get("tunnel"):
                    self._phone_status.configure(fg=C_GOLD)
                # QR'da tam adres var; panelde SADECE sunucu adresini goster.
                # Tam adres uc satira sariyor ve TOKEN satirinin uzerine
                # biniyordu. Token zaten hemen altta ayrica yaziyor.
                self._phone_addr.configure(
                    text=("KALICI EŞLEŞTİRME · sadece ilk kez\n" + url.split("/?")[0])
                )
                # Token TAM gosterilir: telefon elle sorarsa okunabilmeli
                self._phone_token_lbl.configure(text=token or "(token uretilemedi)")
                self._phone_copy.configure(fg=C_PRI if token else C_DIM)
                # Taze kurulumda en sik sorun: anahtar yok → telefon baglanir
                # ama RIPPERINIO cevap veremez. Panelde acikca uyar.
                if not token:
                    # Token bos kalirsa adres ?t= ile biter ve telefon token
                    # SORAR. Sessizce gecme — sebebi burada gorunsun.
                    self._phone_status.configure(
                        text="Token yazilamadi — telefon token soracak!",
                        fg=C_RED,
                    )
                elif not has_gemini_api_key():
                    self._phone_status.configure(
                        text="Sunucu hazir — ama Gemini API anahtari girilmemis!",
                        fg=C_RED,
                    )
            else:
                self._phone_qr_photo = None
                self._phone_qr_label.configure(
                    image="", text="QR kod baslatinca gorunur", fg=C_MID)
                self._phone_addr.configure(text="")
                self._phone_token_lbl.configure(text="")
                self._phone_copy.configure(fg=C_DIM)

            self._draw_phone_action()
            self._draw_phone_button()
        except Exception:
            pass

    def _render_phone_qr(self, url: str):
        """QR kodu panelin icinde gorsel olarak cizer."""
        try:
            import qrcode

            qr = qrcode.QRCode(box_size=4, border=2)
            qr.add_data(url)
            qr.make()
            img = qr.make_image(fill_color="#00d4c0", back_color="#0D1728")
            # qrcode'un PilImage sarmalayicisindan gercek PIL goruntusunu al
            img = getattr(img, "get_image", lambda: img)()
            size = min(200, self._phone_geometry["panel_w"] - 60)
            img = img.convert("RGB").resize((size, size), Image.NEAREST)
            self._phone_qr_photo = ImageTk.PhotoImage(img)
            self._phone_qr_label.configure(image=self._phone_qr_photo, text="")
        except Exception as exc:
            self._phone_qr_photo = None
            self._phone_qr_label.configure(image="", text=f"QR olusturulamadi: {exc}",
                                           fg=C_RED)

    def _copy_phone_token(self):
        token = self._phone_server.token if self._phone_server else ""
        if not token:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(token)
            self.root.update_idletasks()
            self._phone_copy.configure(text="✓ KOPYALANDI", fg=C_GREEN)
            self.root.after(1800, lambda: self._phone_copy.configure(
                text="⧉ KOPYALA", fg=C_PRI))
        except Exception:
            self._phone_copy.configure(text="✕ KOPYALANAMADI", fg=C_RED)

    # ── Shutdown button (sağ alt, büyük) ────────────────────────────────────
    def _build_shutdown_button(self):
        BW, BH = 140, 36
        self._shutdown_canvas = tk.Canvas(
            self.root, width=BW, height=BH,
            bg=C_BG, highlightthickness=0, cursor="hand2")
        self._shutdown_canvas.bind("<Button-1>", lambda e: self._shutdown())
        self._draw_shutdown_button()

    def _draw_shutdown_button(self):
        self._draw_pill(self._shutdown_canvas, "POWER", C_RED, active=False, danger=True)

    def _build_settings_panel(self):
        geo = self._settings_geometry
        self._settings_btn_canvas = tk.Canvas(
            self.root,
            width=geo["btn_w"],
            height=geo["btn_h"],
            bg=C_BG,
            highlightthickness=0,
            cursor="hand2",
        )
        self._settings_btn_canvas.place(x=geo["btn_x"], y=geo["btn_y"])
        self._settings_btn_canvas.bind("<Button-1>", lambda e: self._toggle_settings_panel())
        self._draw_settings_button()

        self._settings_panel = tk.Frame(
            self.root,
            bg="#0D1728",
            highlightbackground="#243650",
            highlightthickness=1,
        )
        self._settings_panel.place_forget()

        self._settings_title = tk.Label(
            self._settings_panel,
            text="SETTINGS",
            fg=C_PRI,
            bg="#0D1728",
            font=font_display(11),
        )
        self._settings_tab_settings = tk.Canvas(
            self._settings_panel,
            width=108,
            height=28,
            bg="#0D1728",
            highlightthickness=0,
            cursor="hand2",
        )
        self._settings_tab_settings.bind("<Button-1>", lambda e: self._set_settings_tab("settings"))
        self._settings_tab_debug = tk.Canvas(
            self._settings_panel,
            width=96,
            height=28,
            bg="#0D1728",
            highlightthickness=0,
            cursor="hand2",
        )
        self._settings_tab_debug.bind("<Button-1>", lambda e: self._set_settings_tab("debug"))
        self._settings_body = tk.Frame(self._settings_panel, bg="#0D1728")
        self._debug_body = tk.Frame(self._settings_panel, bg="#0D1728")
        self._settings_sfx_label = tk.Label(
            self._settings_body,
            text="SFX",
            fg=C_MID,
            bg="#0D1728",
            font=font_body_bold(8),
        )
        self._settings_status_primary = tk.Label(
            self._settings_body,
            text="",
            fg=C_TEXT,
            bg="#0D1728",
            font=font_body_bold(9),
            anchor="w",
            justify="left",
        )
        self._settings_status_secondary = tk.Label(
            self._settings_body,
            text="",
            fg=C_MID,
            bg="#0D1728",
            font=font_body(9),
            anchor="w",
            justify="left",
        )
        self._debug_text = tk.Text(
            self._debug_body,
            fg=C_TEXT,
            bg="#0D1728",
            insertbackground=C_TEXT,
            borderwidth=0,
            wrap="word",
            font=font_body(10),
            padx=10,
            pady=10,
            highlightthickness=1,
            highlightbackground=C_DIM,
        )
        self._debug_text.tag_config("info", foreground=C_TEXT)
        self._debug_text.tag_config("warn", foreground=C_GOLD)
        self._debug_text.tag_config("err", foreground=C_RED)
        self._debug_text.configure(state="disabled")
        self._draw_settings_tabs()
        self._render_debug_logs()
        self._refresh_settings_status()

    def _draw_settings_button(self):
        c = self._settings_btn_canvas
        bw, bh = int(c["width"]), int(c["height"])
        c.delete("all")
        accent = C_BLUE if self._settings_open else "#8497B6"
        self._round_rect(c, 1, 1, bw-1, bh-1, radius=15,
                         fill="#111D2F" if self._settings_open else "#0D1728",
                         outline=accent if self._settings_open else "#23334D", width=1)
        c.create_text(15, 16, text="SYSTEM SETTINGS", fill=C_TEXT, font=font_display(10), anchor="w")
        c.create_text(15, 34, text=MODEL_BADGE, fill="#7488A8", font=font_body(8), anchor="w")
        c.create_text(bw-15, bh//2, text="▾" if self._settings_open else "›",
                      fill=accent, font=font_display(16), anchor="e")

    def _toggle_settings_panel(self):
        self._settings_open = not self._settings_open
        self._draw_settings_button()
        self._place_layout_widgets()

    def _draw_settings_tabs(self):
        for key, canvas, label in (("settings", self._settings_tab_settings, "SETTINGS"), ("debug", self._settings_tab_debug, "DEBUG")):
            active = self._settings_tab == key
            bw, bh = int(canvas["width"]), int(canvas["height"])
            canvas.delete("all")
            col = C_PRI if active else "#8293AF"
            self._round_rect(canvas, 1, 1, bw-1, bh-1, radius=10,
                             fill="#132238" if active else "#0D1728",
                             outline="#29415E" if active else "#1B2A40", width=1)
            canvas.create_text(bw//2, bh//2, text=label, fill=col, font=font_body_bold(8))

    def _set_settings_tab(self, tab: str):
        self._settings_tab = "debug" if tab == "debug" else "settings"
        self._draw_settings_tabs()
        self._place_layout_widgets()

    def _layout_settings_controls(self):
        inner_w = self._settings_geometry["panel_w"] - 24
        self._api_canvas.place(x=0, y=2)
        self._sfx_canvas.place(x=inner_w - int(self._sfx_canvas["width"]) - 4, y=0)
        self._settings_status_primary.place(x=0, y=38, width=inner_w)
        self._settings_status_secondary.place(x=0, y=58, width=inner_w)
        self._settings_sfx_label.place(x=0, y=92)
        self._volume_label.place(x=0, y=116)
        self._volume_scale.place(x=0, y=136, width=inner_w, height=26)
        self._voice_label.place(x=0, y=178)
        self._voice_menu.place(x=88, y=172, width=inner_w - 88, height=30)
        self._autostart_canvas.place(x=0, y=216, width=inner_w, height=30)
        self._shortcut_canvas.place(x=0, y=256, width=inner_w, height=30)
        self._v6_canvas.place(x=0, y=294, width=inner_w, height=30)
        self._theme_label.place(x=0, y=340)
        self._theme_menu.place(x=88, y=332, width=inner_w - 88, height=30)

    def _build_theme_selector(self, parent=None):
        parent = parent or self.root
        self._theme_label = tk.Label(parent, text="THEME", fg=C_MID, bg=parent.cget("bg"), font=font_body_bold(9))
        _names = ["breeze", "blue", "purple", "classic"]
        self._theme_var = tk.StringVar(value="breeze")
        self._theme_menu = tk.OptionMenu(parent, self._theme_var, *_names, command=self._on_theme_menu)
        self._theme_menu.configure(
            bg="#111D2F", fg=C_TEXT, activebackground=C_DIM, activeforeground=C_TEXT,
            highlightthickness=0, borderwidth=0, font=font_body(9),
        )
        try:
            self._theme_menu["menu"].configure(bg="#111D2F", fg=C_TEXT, activebackground=C_DIM, activeforeground=C_TEXT)
        except Exception:
            pass

    def _on_theme_menu(self, value):
        try:
            self.apply_hud_theme_live(str(value), persist=True)
        except Exception as exc:
            self.write_debug(f"Tema değişimi: {exc}", level="WARN")

    def _refresh_settings_status(self):
        if not hasattr(self, "_settings_status_primary"):
            return
        cfg = load_app_config()
        gemini_ready = bool(str(cfg.get("gemini_api_key", "") or "").strip())
        primary = "Gemini hazir" if gemini_ready else "Gemini API eksik"
        self._settings_status_primary.configure(text=primary)
        _mode_labels = {"continuous": "Sürekli", "ptt": "PTT · F8 basılı tut", "wake": "Hey"}
        self._settings_status_secondary.configure(
            text=f"Ses girişi: {_mode_labels.get(getattr(self, 'voice_input_mode', 'continuous'), 'Sürekli')} · F7 değiştir"
        )

    def write_debug(self, text: str, level: str = "INFO"):
        clean = " ".join(str(text or "").split())
        if not clean:
            return
        self.root.after(0, self._append_debug_entry, clean, level)

    def _append_debug_entry(self, text: str, level: str = "INFO"):
        stamp = time.strftime("%H:%M:%S")
        lvl = (level or "INFO").upper()
        self._debug_entries.append((lvl, f"[{stamp}] {lvl}: {text}"))
        self._render_debug_logs()

    def _render_debug_logs(self):
        if not hasattr(self, "_debug_text"):
            return
        self._debug_text.configure(state="normal")
        self._debug_text.delete("1.0", tk.END)
        if not self._debug_entries:
            self._debug_text.insert(tk.END, "Henüz not edilebilir hata yok.\n", "info")
        else:
            for level, line in self._debug_entries:
                tag = "err" if level == "ERROR" else "warn" if level == "WARN" else "info"
                self._debug_text.insert(tk.END, line + "\n", tag)
        self._debug_text.see(tk.END)
        self._debug_text.configure(state="disabled")

    def _build_api_button(self, parent=None):
        parent = parent or self.root
        bw, bh = 154, 28
        self._api_canvas = tk.Canvas(
            parent, width=bw, height=bh,
            bg=parent.cget("bg"), highlightthickness=0, cursor="hand2")
        self._api_canvas.bind("<Button-1>", lambda e: self._open_api_settings())
        self._draw_api_button()

    def _draw_api_button(self):
        self._draw_pill(self._api_canvas, "API SETTINGS", C_BLUE, active=False, font_size=9)

    def _build_fx_slider(self, parent=None):
        parent = parent or self.root
        slider_w = 280
        self._volume_label = tk.Label(
            parent,
            text=f"FX LEVEL  {int(self.sound.get_volume() * 100)}%",
            fg=C_PRI,
            bg=parent.cget("bg"),
            font=font_body_bold(10),
        )
        self._volume_scale = tk.Scale(
            parent,
            from_=0,
            to=100,
            orient="horizontal",
            length=slider_w,
            showvalue=False,
            resolution=1,
            troughcolor="#17263C",
            bg=parent.cget("bg"),
            fg=C_TEXT,
            activebackground=C_PRI,
            highlightthickness=0,
            borderwidth=0,
            sliderlength=18,
            width=10,
            command=self._on_volume_change,
        )
        self._volume_scale.set(int(self.sound.get_volume() * 100))

    def _on_volume_change(self, value):
        try:
            volume = max(0, min(100, int(float(value))))
        except (TypeError, ValueError):
            return
        self._volume_label.configure(text=f"FX LEVEL  {volume}%")
        self.sound.set_volume(volume / 100.0)

    # ── Autostart toggle ─────────────────────────────────────────────────────
    def _autostart_plist_dst(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / "com.alp.ripperinio.plist"

    def _build_autostart_plist(self) -> str:
        """LaunchAgent plist'ini bu makineye göre DİNAMİK üretir.
        Sabit kullanıcı yolu (örn. /Users/...) gömmez; her bilgisayarda çalışır."""
        import sys
        python_exe = sys.executable or "/usr/bin/python3"
        py_dir     = str(Path(python_exe).parent)
        main_py    = BASE_DIR / "main.py"
        out_log    = BASE_DIR / "ripperinio.log"
        err_log    = BASE_DIR / "ripperinio_error.log"
        home       = Path.home()
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.alp.ripperinio</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_exe}</string>
        <string>{main_py}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{BASE_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{out_log}</string>
    <key>StandardErrorPath</key>
    <string>{err_log}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{py_dir}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>{home}</string>
    </dict>
</dict>
</plist>
"""

    def _is_autostart_installed(self) -> bool:
        if IS_WIN:
            try:
                from make_shortcut import is_startup_enabled
                return is_startup_enabled()
            except Exception:
                return False
        return self._autostart_plist_dst().exists()

    def _build_autostart_button(self, parent=None):
        parent = parent or self.root
        self._autostart_canvas = tk.Canvas(
            parent, height=30,
            bg=parent.cget("bg"), highlightthickness=0, cursor="hand2"
        )
        self._autostart_canvas.bind("<Button-1>", lambda e: self._toggle_autostart())
        self._draw_autostart_button()

    def _draw_autostart_button(self):
        on = self._is_autostart_installed()
        col = C_GREEN if on else "#7D91B0"
        text = f"{'●' if on else '○'}  AÇILIŞTA BAŞLAT  {'AÇIK' if on else 'KAPALI'}"
        self._draw_pill(self._autostart_canvas, text, col, active=on, font_size=9)

    def _toggle_autostart(self):
        try:
            if IS_WIN:
                self._toggle_autostart_win()
            else:
                self._toggle_autostart_mac()
        except Exception as exc:
            self.write_log(f"SYS: Autostart hatası — {exc}")
        finally:
            self.root.after(0, self._draw_autostart_button)

    def _toggle_autostart_win(self):
        """Windows: Baslangic klasorune .lnk koyar veya kaldirir."""
        from make_shortcut import create_startup_shortcut, remove_startup_shortcut

        if self._is_autostart_installed():
            remove_startup_shortcut()
            self.write_log("SYS: Otomatik başlatma kapatıldı.")
        else:
            create_startup_shortcut()
            self.write_log("SYS: Otomatik başlatma açıldı. Windows açılışında RIPPERINIO başlar.")

    def _toggle_autostart_mac(self):
        dst = self._autostart_plist_dst()
        if self._is_autostart_installed():
            _sp.run(["launchctl", "unload", str(dst)],
                    capture_output=True, check=False)
            dst.unlink(missing_ok=True)
            self.write_log("SYS: Otomatik başlatma kapatıldı.")
        else:
            # Kur — plist'i bu makineye göre dinamik üret
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(self._build_autostart_plist(), encoding="utf-8")
            _sp.run(["launchctl", "load", str(dst)],
                    capture_output=True, check=False)
            self.write_log("SYS: Otomatik başlatma açıldı. Mac açılışında RIPPERINIO başlar.")

    def _build_v6_button(self, parent=None):
        parent = parent or self.root
        self._v6_canvas = tk.Canvas(parent, height=30, bg=parent.cget("bg"), highlightthickness=0, cursor="hand2")
        self._v6_canvas.bind("<Button-1>", lambda e: self._open_v6_control_center())
        self._draw_pill(self._v6_canvas, "V6 CONTROL CENTER  ·  F9", C_PRI, active=True, font_size=9)

    def _open_v6_control_center(self):
        center = getattr(self, "v6_control_center", None)
        if center is not None:
            try: center.open()
            except Exception as exc: self.write_log(f"SYS: V6 merkez açılamadı — {exc}")

    # ── Desktop shortcut button ──────────────────────────────────────────────
    def _build_shortcut_button(self, parent=None):
        parent = parent or self.root
        self._shortcut_canvas = tk.Canvas(
            parent, height=30,
            bg=parent.cget("bg"), highlightthickness=0, cursor="hand2"
        )
        self._shortcut_canvas.bind("<Button-1>", lambda e: self._create_desktop_shortcut())
        self._draw_shortcut_button()

    def _draw_shortcut_button(self, state: str = "idle"):
        if state == "ok":
            col, label, active = C_GREEN, "✓  MASAÜSTÜ KISAYOLU OLUŞTURULDU", True
        elif state == "err":
            col, label, active = C_RED, "✕  KISAYOL OLUŞTURULAMADI", True
        else:
            col, label, active = "#7D91B0", "MASAÜSTÜNE KISAYOL EKLE", False
        self._draw_pill(self._shortcut_canvas, label, col, active=active, font_size=9)

    def _create_desktop_shortcut(self):
        def _run():
            try:
                from make_shortcut import create_desktop_shortcut
                created = create_desktop_shortcut()
                self.write_log(f"SYS: Masaüstü kısayolu oluşturuldu → {Path(created).name}")
                self.root.after(0, lambda: self._draw_shortcut_button("ok"))
                self.root.after(3000, lambda: self._draw_shortcut_button("idle"))
            except Exception as exc:
                self.write_log(f"SYS: Kısayol hatası — {exc}")
                self.root.after(0, lambda: self._draw_shortcut_button("err"))
                self.root.after(3000, lambda: self._draw_shortcut_button("idle"))

        threading.Thread(target=_run, daemon=True).start()

    def _play_startup_sfx_once(self):
        if self._startup_sfx_played:
            return
        self._startup_sfx_played = True
        if self._effects_active:
            self.sound.play_startup()

    def _sync_sound_state(self):
        enabled = self._sfx_on and not self.paused
        self.sound.set_enabled(enabled)
        if enabled and self._ripperinio_state == "THINKING":
            self.sound.start_thinking()
        if enabled != self._effects_active:
            self._effects_active = enabled
            if self.on_effects_state_change:
                threading.Thread(
                    target=self.on_effects_state_change,
                    args=(enabled,),
                    daemon=True,
                ).start()

    def _open_api_settings(self):
        self._show_setup_ui(edit_mode=self._api_key_ready)

    def _close_setup_ui(self):
        if self.setup_frame and self.setup_frame.winfo_exists():
            self.setup_frame.destroy()
        self.setup_frame = None
        self.api_entry = None
        self.youtube_api_entry = None
        self.youtube_handle_entry = None

    # ── SFX toggle ───────────────────────────────────────────────────────────
    def _build_sfx_button(self, parent=None):
        parent = parent or self.root
        BW, BH = 98, 36
        self._sfx_canvas = tk.Canvas(parent, width=BW, height=BH,
                                     bg=parent.cget("bg"), highlightthickness=0, cursor="hand2")
        self._sfx_canvas.bind("<Button-1>", lambda e: self._toggle_sfx())
        self._sfx_on = True
        self._draw_sfx_button()

    def _draw_sfx_button(self):
        col = C_PRI if self._sfx_on else "#7185A5"
        self._draw_pill(self._sfx_canvas, "SFX  ON" if self._sfx_on else "SFX  OFF", col, active=self._sfx_on, font_size=8)

    def _toggle_sfx(self):
        self._sfx_on = not self._sfx_on
        self._draw_sfx_button()
        self._sync_sound_state()

    # ── Voice selector ───────────────────────────────────────────────────────
    def _build_voice_selector(self, parent=None):
        parent = parent or self.root
        self._voice_var = tk.StringVar(value=self._current_voice)
        self._voice_label = tk.Label(parent, text="VOICE", fg=C_MID, bg=parent.cget("bg"),
                                     font=font_body_bold(8))

        self._voice_menu = tk.OptionMenu(parent, self._voice_var, *VOICES,
                                         command=self._on_voice_select)
        self._voice_menu.config(
            fg=C_PRI, bg=C_PANEL, activeforeground=C_BG,
            activebackground=C_PRI, font=font_body(10),
            borderwidth=0, highlightthickness=1,
            highlightbackground="#243650", width=12)
        self._voice_menu["menu"].config(
            fg=C_PRI, bg=C_PANEL, font=font_body(10),
            activeforeground=C_BG, activebackground=C_PRI)

    def _on_voice_select(self, voice: str):
        self._current_voice = voice
        save_app_config({"voice": voice})
        if self.on_voice_change:
            threading.Thread(target=self.on_voice_change, args=(voice,), daemon=True).start()

    # ── Mute button ──────────────────────────────────────────────────────────
    def _build_mute_button(self):
        self._mute_canvas = tk.Canvas(self.root, width=126, height=36,
                                      bg=C_BG, highlightthickness=0, cursor="hand2")
        self._mute_canvas.bind("<Button-1>", lambda e: self._toggle_mute())
        self._draw_mute_button()

    def _draw_mute_button(self):
        if self.muted:
            self._draw_pill(self._mute_canvas, "MIC  MUTED", C_MUTED, active=True)
        else:
            self._draw_pill(self._mute_canvas, "MIC  LIVE", C_GREEN, active=True)

    def _build_pause_button(self):
        self._pause_canvas = tk.Canvas(self.root, width=126, height=36,
                                       bg=C_BG, highlightthickness=0, cursor="hand2")
        self._pause_canvas.bind("<Button-1>", lambda e: self._toggle_pause())
        self._draw_pause_button()

    def _draw_pause_button(self):
        if self.paused:
            self._draw_pill(self._pause_canvas, "▶  RESUME", C_GOLD, active=True)
        else:
            self._draw_pill(self._pause_canvas, "Ⅱ  PAUSE", C_BLUE, active=False)

    def _build_webcam_button(self):
        self._cam_canvas = tk.Canvas(self.root, width=110, height=36,
                                     bg=C_BG, highlightthickness=0, cursor="hand2")
        self._cam_canvas.bind("<Button-1>", lambda e: self._toggle_webcam_ui())
        self._draw_webcam_button()

    def _draw_webcam_button(self):
        if self._webcam_active:
            self._draw_pill(self._cam_canvas, "●  CAM ON", C_RED, active=True, danger=True)
        else:
            self._draw_pill(self._cam_canvas, "○  CAM", "#7386A6", active=False)

    def _toggle_webcam_ui(self):
        if self.on_webcam_toggle:
            self.on_webcam_toggle(not self._webcam_active)

    def _toggle_mute(self):
        self.muted = not self.muted
        self._draw_mute_button()
        if self.muted:
            self.write_log("SYS: Mikrofon kapatıldı.")
        else:
            self.write_log("SYS: Mikrofon açık.")
        self._sync_sound_state()

    # ── Orb tıklama = pause ──────────────────────────────────────────────────
    def _on_canvas_click(self, event):
        dx = event.x - self.FCX
        dy = event.y - self.FCY
        if dx*dx + dy*dy <= (self.FACE * 0.40)**2:
            self._toggle_pause()

    def _toggle_pause(self):
        self.paused = not self.paused
        self._draw_pause_button()
        if self.paused:
            self.set_state("PAUSED")
            self.write_log("SYS: RIPPERINIO duraklatıldı.")
        else:
            self.set_state("THINKING")
            self.write_log("SYS: RIPPERINIO devam ediyor...")
        self._sync_sound_state()
        if self.on_pause_toggle:
            threading.Thread(target=self.on_pause_toggle, args=(self.paused,), daemon=True).start()

    def _shutdown(self):
        try:
            self._media_stop_event.set()
        except Exception:
            pass
        self._save_window_state()
        try:
            self._global_ptt.stop()
        except Exception:
            pass
        try:
            self.widget_manager.stop()
        except Exception:
            pass
        # V5: Telefon Bridge HUD'dan bagimsiz kalici servistir.
        # HUD kapanirken Bridge kesinlikle durdurulmaz.
        self.sound.stop_all()
        self.write_log("SYS: RIPPERINIO kapatılıyor...")
        self.root.after(380, os._exit, 0)

    # ── Input bar ────────────────────────────────────────────────────────────
    def _build_input_bar(self, lw: int):
        x0 = self.CHAT_X
        btn_w = 76
        gap = 8
        inp_w = lw - btn_w - gap

        self._input_var = tk.StringVar()
        self._input_entry = tk.Entry(
            self.root, textvariable=self._input_var,
            fg=C_TEXT, bg="#101C2E", insertbackground=C_PRI,
            borderwidth=0, relief="flat", font=font_body(11),
            highlightthickness=0,
        )
        self._input_entry.place(x=x0, y=self.CHAT_INPUT_Y, width=inp_w, height=INPUT_H)
        self._input_entry.bind("<Return>", self._on_input_submit)
        self._input_entry.bind("<KP_Enter>", self._on_input_submit)

        self._send_btn = tk.Canvas(
            self.root, width=btn_w, height=INPUT_H,
            bg=C_BG, bd=0, highlightthickness=0, cursor="hand2",
        )
        self._send_btn.bind("<Button-1>", self._on_input_submit)
        self._send_btn.place(x=x0+inp_w+gap, y=self.CHAT_INPUT_Y, width=btn_w, height=INPUT_H)
        self._draw_pill(self._send_btn, "SEND  ›", C_BLUE, active=True, font_size=9)

    def _place_layout_widgets(self):
        self.log_frame.place(x=self.CHAT_X, y=self.CHAT_Y, width=self.CHAT_W, height=self.CHAT_H)
        if hasattr(self, "_media_frame"):
            self._layout_media_panel()
        gap = 10
        mute_w = 126
        pause_w = 126
        cam_w  = 110
        shutdown_w = int(self._shutdown_canvas["width"])
        total = mute_w + pause_w + cam_w + shutdown_w + gap * 3
        start_x = self.FCX - total // 2
        row1_y = self.CTRL_Y + 20

        self._mute_canvas.place(x=start_x, y=row1_y)
        self._pause_canvas.place(x=start_x + mute_w + gap, y=row1_y)
        self._cam_canvas.place(x=start_x + mute_w + pause_w + gap * 2, y=row1_y)
        self._shutdown_canvas.place(x=start_x + mute_w + pause_w + cam_w + gap * 3, y=row1_y)

        geo = self._settings_geometry
        panel_x = geo["panel_x"]
        panel_y = geo["panel_y"]
        panel_w = geo["panel_w"]
        panel_h = geo["panel_h"]
        if self._settings_open:
            self._settings_panel.place(x=panel_x, y=panel_y, width=panel_w, height=panel_h)
            self._settings_panel.lift()
            self._settings_title.place(x=14, y=12)
            self._settings_tab_settings.place(x=14, y=40)
            self._settings_tab_debug.place(x=130, y=40)
            if self._settings_tab == "debug":
                self._settings_body.place_forget()
                self._debug_body.place(x=12, y=76, width=panel_w - 24, height=panel_h - 88)
                self._debug_text.place(x=0, y=0, width=panel_w - 24, height=panel_h - 88)
                self._debug_body.lift()
            else:
                self._debug_body.place_forget()
                self._settings_body.place(x=12, y=76, width=panel_w - 24, height=panel_h - 88)
                self._settings_body.lift()
        else:
            self._settings_panel.place_forget()
            self._settings_title.place_forget()
            self._settings_tab_settings.place_forget()
            self._settings_tab_debug.place_forget()
            self._settings_body.place_forget()
            self._debug_body.place_forget()

        if hasattr(self, "_social_bar"):
            self._social_bar.place(x=14, y=self.H - FOOTER_H - 52)
        self._place_phone_widgets()

        inp_w = self.CHAT_W - 84
        self._input_entry.place(x=self.CHAT_X, y=self.CHAT_INPUT_Y, width=inp_w, height=INPUT_H)
        self._send_btn.place(x=self.CHAT_X + inp_w + 8, y=self.CHAT_INPUT_Y, width=76, height=INPUT_H)

    def _on_input_submit(self, event=None):
        text = self._input_var.get().strip()
        if not text:
            return
        if self.paused:
            self.write_log("SYS: RIPPERINIO duraklatılmış durumda. Devam etmek için pause'u kapat.")
            return
        self._input_var.set("")
        if text.lower() in ("sus", "dur", "stop", "sessiz", "kes"):
            self.write_log("SYS: ⏹ Ses kesildi.")
            if self.on_stop_command:
                threading.Thread(target=self.on_stop_command, daemon=True).start()
            return
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(text,), daemon=True).start()

    # ── State & callbacks ────────────────────────────────────────────────────
    def set_state(self, state: str):
        # Mikrofon yokken eski kodun LISTENING'e zorlamasini engelle.
        if state == "LISTENING" and not getattr(self, "mic_available", True):
            state = "STANDBY"
        previous = getattr(self, "_ripperinio_state", "")
        self._ripperinio_state = state
        self.speaking = (state == "SPEAKING")
        if state in {"THINKING", "PROCESSING"}:
            self.sound.start_thinking()
        elif previous in {"THINKING", "PROCESSING"}:
            self.sound.stop_thinking()
        if state == "ERROR" and previous != "ERROR":
            self.sound.play_error()
        try:
            if state in {"THINKING", "PROCESSING", "SPEAKING", "ERROR", "INITIALISING"}:
                self._compact_activity(force=True)
            elif previous in {"THINKING", "PROCESSING", "SPEAKING", "ERROR", "INITIALISING"}:
                self._compact_last_activity = time.monotonic()
        except Exception:
            pass

    def set_mic_status(self, available: bool, name: str = ""):
        self.mic_available = bool(available)
        self.mic_name = str(name or "")
        if not self.mic_available:
            if not self.paused and self._ripperinio_state not in {
                "SPEAKING", "THINKING", "PROCESSING", "ERROR"
            }:
                self.set_state("STANDBY")
        elif not self.paused and not self.muted and self._ripperinio_state == "STANDBY":
            self.set_state("LISTENING")

    def set_user_speaking(self, value: bool):
        self.mark_user_activity(value)

    def mark_user_activity(self, active: bool = True):
        self.user_speaking = active
        self._user_speaking_until = time.time() + (0.9 if active else 0.0)

    def get_effects_volume(self) -> float:
        return self.sound.get_volume()

    def effects_enabled(self) -> bool:
        return bool(self._effects_active)

    def play_success_sfx(self):
        self.root.after(0, self.sound.play_success)

    def play_error_sfx(self):
        self.root.after(0, self.sound.play_error)

    # ── Webcam layout hesabı ─────────────────────────────────────────────────
    def _calc_cam_layout(self):
        """Kamera paneli boyutları + orb kayma değerlerini döndürür.

        Dönen değerler: (cam_w, cam_h, cam_x, cam_y, orb_shift, orb_face)
        """
        center_w  = self.CENTER_X1 - self.CENTER_X0
        cam_w     = min(center_w - 40, 580)
        cam_h     = int(cam_w * 9 / 16)
        total_h   = self.CTRL_Y - HDR_H          # merkez alanın toplam yüksekliği
        remaining = total_h - cam_h - 24         # kamera altında orb için kalan alan
        new_face  = max(120, min(int(remaining * 0.82),
                                 int(center_w  * 0.70), 520))
        new_cy    = HDR_H + cam_h + 16 + remaining // 2   # orb'un yeni merkezi
        shift     = new_cy - self.FCY
        cam_x     = self.FCX - cam_w // 2
        cam_y     = HDR_H + 8
        return cam_w, cam_h, cam_x, cam_y, shift, new_face

    def set_webcam_active(self, active: bool):
        self._webcam_active = bool(active)
        self.root.after(0, self._draw_webcam_button)
        if active:
            _, _, _, _, shift, face = self._calc_cam_layout()
            self._cam_orb_shift_target = float(shift)
            self._cam_orb_face_target  = float(face)
            # İlk açılışta face'i FACE'den başlat ki animasyon doğal görünsün
            if self._cam_orb_face < 1.0:
                self._cam_orb_face = float(self.FACE)
        else:
            self._cam_orb_shift_target = 0.0
            # Hedef: normal FACE boyutuna geri dön. Animasyon bitince 0'a sıfırlanır
            # (_animate içinde), böylece FACE→0 geçişi görünmez.
            self._cam_orb_face_target  = float(self.FACE)
            self._webcam_photo  = None
            if self._cam_label is not None:
                self._cam_label.place_forget()
        self.write_log(f"SYS: Webcam {'CANLI' if active else 'KAPALI'}")

    def update_webcam_preview(self, jpeg_bytes: bytes) -> None:
        """Live webcam karesini dikdörtgen panel'de gösterir. Thread-safe."""
        self.root.after(0, lambda: self._show_webcam_preview(jpeg_bytes))

    def _show_webcam_preview(self, jpeg_bytes: bytes) -> None:
        """JPEG'i üst-orta alana dikdörtgen Label olarak basar."""
        if not self._webcam_active:
            return
        try:
            import io
            cam_w, cam_h, cam_x, cam_y, _, _ = self._calc_cam_layout()

            img   = Image.open(io.BytesIO(jpeg_bytes))
            # Oranı koru, merkez crop ile 16:9'a getir
            iw, ih  = img.size
            target_ratio = cam_w / cam_h
            src_ratio    = iw / ih
            if src_ratio > target_ratio:            # daha geniş → sol/sağ kırp
                new_w = int(ih * target_ratio)
                img   = img.crop(((iw - new_w) // 2, 0,
                                  (iw + new_w) // 2, ih))
            else:                                   # daha uzun → üst/alt kırp
                new_h = int(iw / target_ratio)
                img   = img.crop((0, (ih - new_h) // 2,
                                  iw, (ih + new_h) // 2))
            img   = img.resize((cam_w, cam_h), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)

            if self._cam_label is None:
                self._cam_label = tk.Label(
                    self.root, bg=C_BG,
                    highlightthickness=1,
                    highlightbackground="#243650",
                )
            self._cam_label.configure(image=photo)
            self._webcam_photo = photo          # referans tut — GC koruması
            self._cam_label.place(x=cam_x, y=cam_y,
                                  width=cam_w, height=cam_h)
            # bg canvas'ın hemen üstüne taşı — settings paneli vs. üstte kalır
            self._cam_label.lift(self.bg)
        except Exception as exc:
            print(f"[UI] Webcam preview güncellenemedi: {exc}")

    def focus_panel(self, section: str, duration_ms: int = 4200):
        section = (section or "").strip().lower()
        if not section:
            return

        def _apply():
            self._panel_focus = section
            self._panel_focus_until = time.time() + max(0.8, duration_ms / 1000.0)

        self.root.after(0, _apply)

    def _state_color(self, state: str | None = None) -> str:
        effective = state or self._ripperinio_state
        if effective == "PAUSED":
            return C_MID
        return STATE_HEX_COLORS.get(effective, C_PRI)

    @staticmethod
    def _state_badge_text(state: str) -> str:
        if state == "INITIALISING":
            return "CONNECTING"
        if state == "ERROR":
            return "ERROR"
        return "ONLINE"

    # ── Log ──────────────────────────────────────────────────────────────────
    def write_log(self, text: str):
        self.typing_queue.append(text)
        tl = text.lower()
        try:
            if tl.startswith(("siz:", "you:", "ripperinio:", "ai:", "err:")):
                self._compact_activity(force=True)
        except Exception:
            pass
        if tl.startswith("siz:") or tl.startswith("you:"):
            self.mark_user_activity(True)
            self.set_state("THINKING")
        elif tl.startswith("err:") or "error" in tl:
            self._error_hold_until = time.time() + 8.0
            self.set_state("ERROR")
            self.write_debug(text, level="ERROR")
        if not self.is_typing:
            self._start_typing()

    def _start_typing(self):
        if not self.typing_queue:
            self.is_typing = False
            if self._ripperinio_state == "ERROR" and time.time() < self._error_hold_until:
                return
            if not self.speaking:
                self.set_state("LISTENING")
            return
        self.is_typing = True
        text = self.typing_queue.popleft()
        tl   = text.lower()
        if   tl.startswith("siz:") or tl.startswith("you:"):   tag = "you"
        elif tl.startswith("ripperinio:") or tl.startswith("ai:"): tag = "ai"
        elif tl.startswith("err:") or "error" in tl:           tag = "err"
        else:                                                    tag = "sys"
        self.log_text.configure(state="normal")
        self._type_char(text, 0, tag)

    def _type_char(self, text, i, tag):
        if i < len(text):
            self.log_text.insert(tk.END, text[i], tag)
            self.log_text.see(tk.END)
            self.root.after(7, self._type_char, text, i+1, tag)
        else:
            self.log_text.insert(tk.END, "\n")
            self.log_text.configure(state="disabled")
            self.root.after(20, self._start_typing)

    # ── Stats ────────────────────────────────────────────────────────────────
    def _update_stats(self):
        try:
            self._stats['cpu']  = psutil.cpu_percent()
            self._stats['ram']  = psutil.virtual_memory().percent
            self._stats['disk'] = psutil.disk_usage('/').percent
            batt = psutil.sensors_battery()
            self._stats['battery'] = batt.percent if batt else 100.0
            now = time.time()
            net = psutil.net_io_counters()
            dt  = now - self._last_net_t
            if dt > 0:
                self._stats['net_up']   = max(0, (net.bytes_sent - self._last_net.bytes_sent) / dt / 1024)
                self._stats['net_down'] = max(0, (net.bytes_recv - self._last_net.bytes_recv) / dt / 1024)
            self._last_net   = net
            self._last_net_t = now
            self._cpu_hist.pop(0)
            self._cpu_hist.append(self._stats['cpu'])
        except Exception:
            pass

    # ── Animation loop ───────────────────────────────────────────────────────
    def _animate(self):
        self.tick += 1
        t   = self.tick
        now = time.time()

        if self.user_speaking and now > self._user_speaking_until:
            self.user_speaking = False

        # The old HUD remains alive as backend/state, but once the reference
        # shell is visible there is no reason to repaint the hidden legacy
        # canvas 25 times per second. Keep periodic stats/brief refreshes only.
        ref_hud = getattr(self, "reference_hud", None)
        if ref_hud is not None and getattr(ref_hud, "_alive", False):
            if t % 12 == 0:
                threading.Thread(target=self._update_stats, daemon=True).start()
            if t % 300 == 1:
                self._kick_brief_refresh()
            self.root.after(120, self._animate)
            return

        if t % 90 == 0:
            threading.Thread(target=self._update_stats, daemon=True).start()
        if t % 1800 == 1:
            self._kick_brief_refresh()

        if self.speaking and t % 3 == 0:
            self._wave_ripperinio = [random.randint(6, 30) for _ in range(18)]
        if self.user_speaking and t % 3 == 0:
            self._wave_user = [random.randint(5, 24) for _ in range(18)]

        if now - self.last_t > (0.12 if self.speaking else 0.50):
            if self.paused:
                self.target_scale = random.uniform(0.58, 0.64)
                self.target_halo  = random.uniform(5, 10)
            elif self.speaking:
                self.target_scale = random.uniform(0.98, 1.10)
                self.target_halo  = random.uniform(180, 250)
            elif self.user_speaking:
                self.target_scale = random.uniform(0.88, 0.98)
                self.target_halo  = random.uniform(120, 175)
            elif self._ripperinio_state in ("THINKING", "PROCESSING", "INITIALISING"):
                self.target_scale = random.uniform(0.80, 0.88)
                self.target_halo  = random.uniform(95, 145)
            else:
                self.target_scale = random.uniform(0.72, 0.80)
                self.target_halo  = random.uniform(34, 58)
            self.last_t = now

        sp          = 0.34 if self.speaking else 0.18
        self.scale  += (self.target_scale - self.scale) * sp
        self.halo_a += (self.target_halo   - self.halo_a) * sp

        # Kamera orb animasyonu — ~0.07 ease ≈ 400 ms settle @40ms frame
        _CE = 0.07
        self._cam_orb_shift += (self._cam_orb_shift_target - self._cam_orb_shift) * _CE
        self._cam_orb_face  += (self._cam_orb_face_target  - self._cam_orb_face)  * _CE
        if abs(self._cam_orb_shift_target - self._cam_orb_shift) < 0.5:
            self._cam_orb_shift = self._cam_orb_shift_target
        if abs(self._cam_orb_face_target - self._cam_orb_face) < 0.5:
            self._cam_orb_face = self._cam_orb_face_target
            # Kamera kapandıktan sonra animasyon bitince face'i temizle
            if not self._webcam_active:
                self._cam_orb_face        = 0.0
                self._cam_orb_face_target = 0.0

        if self.paused:
            spds = [0.0, 0.0, 0.0, 0.0]
        elif self.speaking:
            spds = [1.6, -1.1, 2.4, -0.7]
        else:
            spds = [0.55, -0.35, 0.90, -0.28]
        for i, spd in enumerate(spds):
            self.rings_spin[i] = (self.rings_spin[i] + spd) % 360

        # Pulse rings
        pspd  = 4.2 if self.speaking else 1.8
        limit = self.FACE * 0.68
        self.pulse_r = [r + pspd for r in self.pulse_r if r + pspd < limit]
        if len(self.pulse_r) < 3 and random.random() < (0.07 if self.speaking else 0.02):
            self.pulse_r.append(0.0)

        for p in self.particles:
            p['x'] = (p['x'] + p['vx']) % self.W
            p['y'] = (p['y'] + p['vy']) % self.H

        if t % 38 == 0:
            self.status_blink = not self.status_blink

        self._draw()
        self.root.after(40, self._animate)

    # ── Yardımcı ─────────────────────────────────────────────────────────────
    @staticmethod
    def _ac(r, g, b, a):
        f = max(0, min(255, int(a))) / 255.0
        return f"#{int(r*f):02x}{int(g*f):02x}{int(b*f):02x}"

    def _orb_rgb(self):
        state = "PAUSED" if self.paused else self._ripperinio_state
        return ORB_COLORS.get(state, ORB_COLORS["LISTENING"])

    @staticmethod
    def _split_summary_lines(text: str, limit: int = 4) -> list[str]:
        raw = (text or "").strip()
        if not raw:
            return []
        raw = raw.replace(" ve ", ", ")
        parts = [part.strip(" .") for part in raw.split(",") if part.strip()]
        return parts[:limit]

    def _parse_weather_card(self, text: str) -> dict:
        if not text or "alınamadı" in text.lower() or "alınamadi" in text.lower():
            return {
                "city": "Atakum / Samsun",
                "primary": "--",
                "details": ["Hava durumu alınamadı."],
            }

        prefix, _, body = text.partition(":")
        city = "Atakum / Samsun"
        if " için" in prefix:
            city = prefix.split(" için", 1)[0].strip()

        details = [part.strip(" .") for part in body.split(",") if part.strip()]
        primary = "--"
        if details:
            primary = details[0].replace(" derece", "°C")
        return {
            "city": city,
            "primary": primary,
            "details": details[1:4] or ["Anlık veri hazır."],
        }

    def _kick_brief_refresh(self):
        if self._brief_refresh_busy:
            return
        self._brief_refresh_busy = True
        threading.Thread(target=self._refresh_brief_cards, daemon=True).start()

    def _refresh_brief_cards(self):
        try:
            # Konum ARTIK SABIT DEGIL: kullanici elle ayarlamadiysa
            # bulundugu sehir otomatik tespit edilir.
            weather = get_weather_summary()
            self._weather_card = self._parse_weather_card(weather)
        except Exception:
            self._weather_card = {
                "city": current_location_label(),
                "primary": "--",
                "details": ["Hava durumu alınamadı."],
            }
        finally:
            self._brief_refresh_busy = False

    def _bar(self, c, x, y, w, h, pct, color):
        c.create_rectangle(x, y, x+w, y+h, fill="#17263C", outline=C_DIM, width=1)
        fw = max(1, int(w * pct / 100))
        c.create_rectangle(x+1, y+1, x+fw, y+h-1, fill=color, outline="")

    def _sparkline(self, c, x, y, w, h, data):
        c.create_rectangle(x, y, x+w, y+h, fill="#0D1728", outline=C_DIM, width=1)
        n = len(data)
        if n < 2:
            return
        step = (w - 2) / (n - 1)
        h2   = h - 2
        coords = []
        for i, v in enumerate(data):
            coords.append(x + 1 + i * step)
            coords.append(y + h - 1 - int(h2 * v / 100))
        c.create_line(*coords, fill=C_PRI, width=1, smooth=True)

    def _bracket(self, c, x0, y0, pw, ph, col=None, bl=12):
        col = col or C_PRI
        for bx, by, sx, sy in [(x0, y0, 1, 1), (x0+pw, y0, -1, 1),
                                (x0, y0+ph, 1, -1), (x0+pw, y0+ph, -1, -1)]:
            c.create_line(bx, by, bx+sx*bl, by, fill=col, width=2)
            c.create_line(bx, by, bx, by+sy*bl, fill=col, width=2)

    def _draw_info_card(self, c, x0, y0, pw, ph, title, accent=C_PRI):
        focus = max(0.0, min(1.0, getattr(self, "_card_focus_boost", 0.0)))
        dimmed = bool(getattr(self, "_card_dimmed", False))
        glow = int(55 + 120 * focus)
        border = accent if focus > 0.08 else ("#35504d" if dimmed else self._ac(0, 120, 112, 190))
        fill = "#071111" if dimmed else "#030d0d"
        c.create_rectangle(x0, y0, x0+pw, y0+ph, fill=fill, outline="")
        if focus > 0.08:
            for inset in range(3):
                c.create_rectangle(
                    x0-inset, y0-inset, x0+pw+inset, y0+ph+inset,
                    outline=self._ac(*ORB_COLORS["LISTENING"], max(12, glow - inset * 28)),
                    width=1,
                )
        self._bracket(c, x0, y0, pw, ph, col=border, bl=10)
        title_fill = "#6f7d7b" if dimmed else accent
        line_fill = "#173130" if dimmed else C_DIM
        c.create_text(x0+14, y0+14, text=title, fill=title_fill,
                      font=font_display(10), anchor="w")
        c.create_line(x0+12, y0+28, x0+pw-12, y0+28, fill=line_fill)

    def _focus_boost_for(self, section: str) -> float:
        if self._panel_focus != section:
            return 0.0
        remaining = self._panel_focus_until - time.time()
        if remaining <= 0:
            return 0.0
        pulse = 0.65 + 0.35 * math.sin(self.tick * 0.12)
        return min(1.0, remaining / 4.0) * pulse

    # ── Sol panel ─────────────────────────────────────────────────────────────
    def _draw_left_panel(self, c):
        """Aurora bilgi rail'i: soft cards + daha buyuk tipografi + yuvarlak meterlar."""
        x = 16
        w = max(198, self.LEFT_W - 28)
        y = HDR_H + 14

        def card(title, value, lines, accent=C_PRI, height=124):
            nonlocal y
            # faint shadow
            self._round_rect(c, x+2, y+4, x+w+2, y+height+4, radius=18, fill="#050912", outline="")
            self._round_rect(c, x, y, x+w, y+height, radius=18, fill="#0D1728", outline="#1D2A40", width=1)
            c.create_text(x+16, y+18, text=title.upper(), anchor="w", fill="#8193AE", font=font_body_bold(8))
            c.create_oval(x+w-27, y+15, x+w-19, y+23, fill=accent, outline="")
            c.create_text(x+16, y+49, text=value, anchor="w", fill=C_TEXT, font=font_display(25))
            yy = y+78
            for line in list(lines)[:2]:
                c.create_text(x+16, yy, text=str(line), anchor="w", fill="#B8C6DB", font=font_body(9))
                yy += 18
            y += height + 12

        card("Bugün", time.strftime("%H:%M"), [_turkish_date_text(), _turkish_day_text()], C_PRI, 116)
        weather = dict(getattr(self, "_weather_card", {}) or {})
        card("Hava · " + str(weather.get("city") or ""), str(weather.get("primary") or "--"),
             weather.get("details") or [], C_GREEN, 126)

        h = 176
        self._round_rect(c, x+2, y+4, x+w+2, y+h+4, radius=18, fill="#050912", outline="")
        self._round_rect(c, x, y, x+w, y+h, radius=18, fill="#0D1728", outline="#1D2A40", width=1)
        c.create_text(x+16, y+19, text="SİSTEM", anchor="w", fill="#8193AE", font=font_body_bold(8))
        rows = [
            ("CPU", self._stats.get("cpu",0), C_PRI),
            ("RAM", self._stats.get("ram",0), C_BLUE),
            ("DISK", self._stats.get("disk",0), C_GOLD),
            ("BAT", self._stats.get("battery",100), C_GREEN),
        ]
        yy = y+47
        for label,val,col in rows:
            val = max(0.0,min(100.0,float(val)))
            c.create_text(x+16, yy, text=label, anchor="w", fill="#B8C6DB", font=font_body_bold(8))
            c.create_text(x+w-16, yy, text=f"{val:.0f}%", anchor="e", fill=C_TEXT, font=font_body_bold(9))
            by = yy+14
            x1, x2 = x+17, x+w-17
            c.create_line(x1,by,x2,by,fill="#1A2940",width=6,capstyle=tk.ROUND)
            fillx = x1 + (x2-x1)*val/100.0
            if fillx > x1+1:
                c.create_line(x1,by,fillx,by,fill=col,width=6,capstyle=tk.ROUND)
            yy += 33

    def _draw_right_panel(self, c):
        x0 = self.CHAT_PANEL_X
        y0 = self.CHAT_PANEL_Y
        pw = self.CHAT_PANEL_W
        ph = self.CHAT_PANEL_H
        self._round_rect(c, x0+2, y0+4, x0+pw+2, y0+ph+4, radius=22, fill="#050912", outline="")
        self._round_rect(c, x0, y0, x0+pw, y0+ph, radius=22, fill="#0B1423", outline="#1D2A40", width=1)
        state = "PAUSED" if self.paused else self._ripperinio_state
        col = self._state_color(state)
        c.create_text(x0+16, y0+21, text="AKIŞ", fill="#8193AE", font=font_body_bold(9), anchor="w")
        # status pill
        pill_w = 94
        self._round_rect(c, x0+pw-pill_w-12, y0+10, x0+pw-12, y0+32,
                         radius=11, fill="#101D2F", outline="#1E304A", width=1)
        c.create_oval(x0+pw-pill_w, y0+18, x0+pw-pill_w+6, y0+24, fill=col, outline="")
        c.create_text(x0+pw-20, y0+21, text=state.title(), fill=col, font=font_body_bold(8), anchor="e")

    def _draw_orb(self, c, cam: bool = False):
        state = "PAUSED" if self.paused else self._ripperinio_state
        t    = self.tick
        speak_pulse = 1.0
        if self.speaking:
            speak_pulse = 1.0 + 0.12 * math.sin(t * 0.23) + 0.05 * math.sin(t * 0.11 + 1.2)
        elif self.user_speaking:
            speak_pulse = 1.0 + 0.06 * math.sin(t * 0.18 + 0.7)
        elif state in ("THINKING", "PROCESSING", "INITIALISING"):
            speak_pulse = 1.0 + 0.03 * math.sin(t * 0.10)
        else:
            speak_pulse = 1.0 + 0.01 * math.sin(t * 0.07)

        move_x = 0
        move_y = 0
        if self.user_speaking:
            move_x = int(6 * math.sin(t * 0.06))
            move_y = int(4 * math.cos(t * 0.09 + 0.5))
        elif state in ("THINKING", "PROCESSING", "INITIALISING"):
            move_x = int(3 * math.sin(t * 0.045))
            move_y = int(2 * math.cos(t * 0.05 + 0.4))

        FCX  = self.FCX + move_x
        # _cam_orb_shift her zaman uygulanır — animasyon hem açılışta hem
        # kapanışta çalışır; kamera kapalıysa target=0 olduğundan doğal döner.
        FCY  = self.FCY + move_y + int(self._cam_orb_shift)
        base_face = (int(self._cam_orb_face) if self._cam_orb_face > 1.0 else self.FACE)
        FW   = int(base_face * self.scale * speak_pulse)
        R, G, B = self._orb_rgb()
        ha   = self.halo_a
        field_r = int(FW * 0.49)
        inner_r = int(FW * 0.34)
        activity = (
            0.10 if self.paused else
            1.00 if self.speaking else
            0.78 if self.user_speaking else
            0.62 if state in ("THINKING", "PROCESSING", "INITIALISING") else
            0.26
        )
        if state == "PROCESSING":
            accent_rgb = (255, 210, 72)
        elif state in ("THINKING", "INITIALISING"):
            accent_rgb = (0, 220, 255)
        elif self.speaking:
            accent_rgb = (170, 220, 255)
        elif self.user_speaking:
            accent_rgb = (118, 200, 255)
        else:
            accent_rgb = (120, 255, 185)

        # Pulse rings
        for pr in self.pulse_r:
            alpha = max(0, int(160 * (1.0 - pr / (FW * 0.70))))
            rr = int(pr + field_r * 0.96)
            c.create_oval(
                FCX-rr, FCY-rr, FCX+rr, FCY+rr,
                outline=self._ac(R, G, B, alpha),
                width=1,
            )

        # Large outer glow
        if not self.paused:
            for i in range(10, 0, -1):
                frac = i / 10
                rr = int(field_r * (1.02 + 0.045 * frac))
                alpha = int(ha * 0.10 * frac)
                if self.speaking:
                    ox = 0
                    oy = 0
                else:
                    ox = int(3 * math.sin(t * 0.010 + i))
                    oy = int(3 * math.cos(t * 0.009 + i * 1.3))
                c.create_oval(
                    FCX-rr+ox, FCY-rr+oy, FCX+rr+ox, FCY+rr+oy,
                    outline=self._ac(R, G, B, alpha),
                    width=3,
                )

        # Structural circles — kamera açıkken gizle (webcam zaten üstünü kapatır)
        if not cam:
            for frac, width, alpha_mult in (
                (1.00, 2, 0.34),
                (0.90, 2, 0.24),
                (0.76, 1, 0.18),
                (0.62, 1, 0.12),
            ):
                rr = int(field_r * frac)
                c.create_oval(
                    FCX-rr, FCY-rr, FCX+rr, FCY+rr,
                    outline=self._ac(R, G, B, int(ha * alpha_mult * (0.4 if self.paused else 1.0))),
                    width=width,
                )

        speak_shell_push = 1.16 if self.speaking else 1.07 if self.user_speaking else 1.0
        # Orb shell particles
        shell_r = field_r * 0.93 * speak_shell_push
        for idx, sp in enumerate(self.orb_shell_particles):
            angle = sp['angle'] + t * sp['speed'] * (2.8 if self.speaking else 1.6 if self.user_speaking else 1.1)
            wobble = 1.0 + (0.07 if self.speaking else 0.035) * math.sin(t * 0.08 + sp['phase'])
            x = FCX + math.cos(angle) * shell_r * wobble
            y = FCY + math.sin(angle) * shell_r * wobble
            alpha = int((70 + 120 * sp['glow']) * (0.26 if self.paused else 0.52 + activity * 0.45))
            if idx % 9 == 0 and not self.paused:
                col = self._ac(accent_rgb[0], accent_rgb[1], accent_rgb[2], min(255, alpha + 30))
            else:
                col = self._ac(R, G, B, alpha)
            pr = sp['size'] * (1.0 + 0.24 * math.sin(t * 0.05 + sp['phase']))
            c.create_oval(x-pr, y-pr, x+pr, y+pr, fill=col, outline="")

        # Rotating segmented arcs — kamera açıkken gizle
        if not cam:
            arc_r1 = int(field_r * 0.96)
            arc_r2 = int(field_r * 0.78)
            for start, extent, width, accent in (
                (self.rings_spin[0], 52 if self.speaking else 34, 3, False),
                ((self.rings_spin[0] + 148) % 360, 26, 2, True),
                ((self.rings_spin[2] + 28) % 360, 64 if self.user_speaking else 40, 3, False),
                ((self.rings_spin[2] + 212) % 360, 18, 2, True),
            ):
                rr = arc_r1 if width == 3 else arc_r2
                if accent and not self.paused:
                    col = self._ac(accent_rgb[0], accent_rgb[1], accent_rgb[2], int(120 + 80 * activity))
                else:
                    col = self._ac(R, G, B, int(ha * (1.2 if width == 3 else 0.7)))
                c.create_arc(
                    FCX-rr, FCY-rr, FCX+rr, FCY+rr,
                    start=start, extent=extent,
                    outline=col, width=width, style="arc",
                )

        # Particle orb field
        field_limit = inner_r * (
            0.82 if self.paused else
            1.36 if self.speaking else
            1.16 if self.user_speaking else
            1.0
        )
        for idx, p in enumerate(self.orb_particles):
            speed_mult = (
                0.10 if self.paused else
                3.10 if self.speaking else
                2.00 if self.user_speaking else
                1.10
            )
            angle = p['angle'] + t * p['speed'] * speed_mult
            wobble = 1.0 + (0.30 if self.speaking else 0.18) * math.sin(t * p['wobble'] + p['phase'])
            orbit = field_limit * p['orbit'] * wobble
            depth = 0.5 + 0.5 * math.sin(angle * 2.0 + t * 0.013 + p['phase'])
            y_squash = 0.62 + depth * 0.38
            drift = (8.0 if self.speaking else 5.0 if self.user_speaking else 4.0) * p['depth']
            x = FCX + math.cos(angle) * orbit + math.sin(t * 0.011 + p['phase']) * drift
            y = FCY + math.sin(angle) * orbit * y_squash + math.cos(t * 0.010 + p['phase']) * drift
            base_alpha = int((18 + 155 * p['depth']) * (0.24 + activity * 0.86) * (0.45 + depth * 0.75))
            if self.paused:
                base_alpha = int(base_alpha * 0.40)
            if idx % 11 == 0 and not self.paused:
                col = self._ac(accent_rgb[0], accent_rgb[1], accent_rgb[2], min(255, base_alpha + 25))
            elif self.user_speaking and idx % 7 == 0:
                col = self._ac(120, 205, 255, min(255, base_alpha + 20))
            else:
                col = self._ac(R, G, B, base_alpha)
            pr = p['size'] * (0.70 if self.paused else 0.90 + depth * 0.65 + 0.30 * activity * p['depth'])
            c.create_oval(x-pr, y-pr, x+pr, y+pr, fill=col, outline="")
            if idx % 18 == 0 and not self.paused:
                c.create_line(
                    FCX + (x-FCX) * 0.18,
                    FCY + (y-FCY) * 0.18,
                    x, y,
                    fill=self._ac(R, G, B, int(18 + 35 * p['depth'] * activity)),
                    width=1,
                )

        # Media Visualizer V2 — gercek Windows master-output enerjisiyle beat-reactive.
        if not cam and not self.paused:
            try:
                peak = max(0.0, min(1.0, float(getattr(self, "_media_peak", 0.0))))
                beat = max(0.0, min(1.0, float(getattr(self, "_media_beat", 0.0))))
                media_status = str((getattr(self, "_media_snapshot", {}) or {}).get("status", "")).upper()
                bins = list(getattr(self, "_media_wave_bins", []) or [])
                if "PLAYING" in media_status and peak > 0.004:
                    bars = len(bins) if bins else 48
                    base = field_r * (1.068 + beat * 0.018)
                    for i in range(bars):
                        ang = (math.tau * i / bars) - math.pi / 2
                        energy = bins[i] if i < len(bins) else peak
                        wave = 0.68 + 0.32 * math.sin(t * 0.09 + i * 0.57)
                        length = 2.5 + energy * (15.0 + 31.0 * wave) + beat * (7.0 if i % 3 == 0 else 2.0)
                        x1 = FCX + math.cos(ang) * base
                        y1 = FCY + math.sin(ang) * base
                        x2 = FCX + math.cos(ang) * (base + length)
                        y2 = FCY + math.sin(ang) * (base + length)
                        alpha = min(245, 105 + int(115 * max(peak, energy)) + int(25 * beat))
                        width = 3 if beat > 0.72 and i % 4 == 0 else 2
                        c.create_line(x1, y1, x2, y2, fill=self._ac(R,G,B,alpha), width=width)
                    # Beat aninda orbun etrafinda tek karelik dis pulse.
                    if beat > 0.16:
                        rr = int(field_r * (1.13 + 0.055 * beat))
                        c.create_oval(FCX-rr, FCY-rr, FCX+rr, FCY+rr,
                                      outline=self._ac(accent_rgb[0],accent_rgb[1],accent_rgb[2],80+int(130*beat)), width=2)
            except Exception:
                pass

        # Telefon bridge canlıysa küçük mavi bağlantı halkası.
        if not cam and getattr(self, "_phone_connected", False):
            rr = int(field_r * 1.14)
            c.create_arc(FCX-rr, FCY-rr, FCX+rr, FCY+rr,
                         start=(t * 1.7) % 360, extent=82, outline=C_BLUE, width=3, style="arc")

        # Center void keeps the orb airy instead of lens-like.
        void_r = int(inner_r * (0.18 if self.paused else 0.12))
        if void_r > 0:
            c.create_oval(
                FCX-void_r, FCY-void_r, FCX+void_r, FCY+void_r,
                fill=C_BG,
                outline="",
            )

    def _draw_orb_minimal(self, c):
        """Premium ama sade orb: az halka, tek accent arc, audio pulse."""
        cx, cy = self.FCX, self.FCY
        face = max(180, int(self.FACE))
        base = face * 0.36
        state = "PAUSED" if self.paused else self._ripperinio_state
        col = self._state_color(state)
        R,G,B = self._orb_rgb()
        peak = max(0.0, min(1.0, float(getattr(self, "_media_peak", 0.0) or 0.0)))
        beat = max(0.0, min(1.0, float(getattr(self, "_media_beat", 0.0) or 0.0)))

        # Soft field
        for mul, edge in ((1.34,"#1D2A40"),(1.05,"#243550"),(0.78,"#2C3F5E")):
            r=int(base*mul + beat*5)
            c.create_oval(cx-r,cy-r,cx+r,cy+r,outline=edge,width=1)
        r=int(base*1.14 + beat*8)
        c.create_arc(cx-r,cy-r,cx+r,cy+r,start=(self.tick*1.6)%360,extent=88+int(peak*45),style="arc",outline=col,width=3)
        r2=int(base*0.92)
        c.create_arc(cx-r2,cy-r2,cx+r2,cy+r2,start=210-(self.tick*0.55)%360,extent=54,style="arc",outline=C_BLUE,width=2)

        core=int(base*0.43 + beat*3)
        c.create_oval(cx-core,cy-core,cx+core,cy+core,fill="#111D2F",outline="#315074",width=1)
        c.create_oval(cx-core+9,cy-core+9,cx+core-9,cy+core-9,fill="#122038",outline=col,width=2)
        c.create_text(cx,cy-3,text="R",fill=C_TEXT,font=font_display(max(18,int(face*0.07))))
        c.create_text(cx,cy+24,text="RIP",fill="#8195B6",font=font_body_bold(7))

        # Audio energy: minimal 12 ticks, only when media actually moves.
        if peak > 0.025:
            for i in range(12):
                a=math.tau*i/12 + self.tick*0.006
                length=5+int(18*peak*(0.55+0.45*abs(math.sin(i*1.7+self.tick*0.05))))
                br=base*1.40
                x1=cx+math.cos(a)*br; y1=cy+math.sin(a)*br
                x2=cx+math.cos(a)*(br+length); y2=cy+math.sin(a)*(br+length)
                c.create_line(x1,y1,x2,y2,fill=col,width=2)

        if getattr(self, "_phone_connected", False):
            rr=int(base*1.48)
            c.create_arc(cx-rr,cy-rr,cx+rr,cy+rr,start=(self.tick*1.2)%360,extent=46,style="arc",outline="#7CB5FF",width=2)

    # ── Ana çizim ─────────────────────────────────────────────────────────────
    def _draw(self):
        c  = self.bg
        W  = self.W
        H  = self.H
        c.delete("all")

        # Aurora gradient + soft center glow. No grid, scanline or pixel brackets.
        bg_photo = self._background_photo_for_current_size()
        c.create_image(0, 0, image=bg_photo, anchor="nw")

        self._draw_left_panel(c)
        self._draw_right_panel(c)

        if not self._webcam_active:
            self._draw_orb_premium(c)
        else:
            self._draw_orb(c, cam=True)

        state_label = "PAUSED" if self.paused else self._ripperinio_state
        state_col = self._state_color(state_label)
        c.create_text(self.FCX, self.CTRL_Y - 38, text=SYSTEM_NAME,
                      fill=C_TEXT, font=font_display(20))
        c.create_text(self.FCX, self.CTRL_Y - 14, text=f"●  {state_label.title()}",
                      fill=state_col, font=font_body_bold(10))

        # Header: floating brand bar instead of hard top/bottom lines.
        self._round_rect(c, 12, 10, W-12, HDR_H-8, radius=20, fill="#0B1423", outline="#1D2A40", width=1)
        c.create_text(W//2, 29, text=SYSTEM_NAME, fill=C_TEXT, font=font_display(21))
        c.create_text(W//2, 51, text="personal assistant", fill="#7185A5", font=font_body(8))

        # model badge left
        self._round_rect(c, 24, 23, 210, 48, radius=12, fill="#101C2E", outline="#1E304A", width=1)
        c.create_text(36, 35, text=MODEL_BADGE, fill="#8EA0BB", font=font_body_bold(8), anchor="w")

        # state badge right
        indicator_state = "PAUSED" if self.paused else self._ripperinio_state
        ind_col = self._state_color(indicator_state)
        indicator_text = self._state_badge_text(indicator_state)
        badge_w = 122
        self._round_rect(c, W-badge_w-24, 23, W-24, 48, radius=12,
                         fill="#101C2E", outline="#1E304A", width=1)
        c.create_oval(W-badge_w-11, 32, W-badge_w-3, 40, fill=ind_col, outline="")
        c.create_text(W-34, 36, text=indicator_text, fill=ind_col, font=font_body_bold(9), anchor="e")
        if self._webcam_active:
            c.create_text(W-30, 58, text="● CAM LIVE", fill=C_RED, font=font_body_bold(8), anchor="e")

        # Footer is intentionally quiet.
        c.create_text(18, H-14, fill="#425571", font=font_body(8),
                      text=f"RIP · {PLATFORM_LABEL} · {STAMP}", anchor="w")
        c.create_text(W-18, H-14, fill="#425571", font=font_body(8),
                      text="Ctrl+Space komut  ·  Ctrl+Shift+Space HUD  ·  F10 edit", anchor="e")

    def wait_for_api_key(self):
        while not self._api_key_ready:
            time.sleep(0.1)

    def _show_setup_ui(self, edit_mode: bool = False):
        self._close_setup_ui()

        self.setup_frame = tk.Frame(self.root, bg="#0D1728",
                                    highlightbackground="#2B4567",
                                    highlightthickness=1)
        self.setup_frame.place(relx=0.5, rely=0.5, anchor="center")

        title = "◈ API AYARLARI" if edit_mode else "◈ İLK KURULUM GEREKLİ"
        subtitle = (
            "Gemini API anahtarinizi guncelleyin."
            if edit_mode else
            "Gemini API anahtarinizi girin."
        )
        config = load_app_config()

        tk.Label(self.setup_frame, text=title,
                 fg=C_PRI, bg="#0D1728", font=font_display(16)).pack(pady=(18, 4))
        tk.Label(self.setup_frame, text=subtitle,
                 fg=C_MID, bg="#0D1728", font=font_body(11)).pack(pady=(0, 10))
        tk.Label(self.setup_frame, text="GEMINI API KEY",
                 fg=C_DIM, bg="#0D1728", font=font_body(11)).pack(pady=(8, 2))

        self.api_entry = tk.Entry(
            self.setup_frame, width=52,
            fg=C_TEXT, bg="#0D1728", insertbackground=C_TEXT,
            borderwidth=0, font=font_body(12), show="*")
        self.api_entry.pack(pady=(0, 6))

        current_key = str(config.get("gemini_api_key", "") or "")
        if current_key:
            self.api_entry.insert(0, current_key)

        buttons = tk.Frame(self.setup_frame, bg="#0D1728")
        buttons.pack(pady=14)

        tk.Button(buttons, text="▸ KAYDET",
                  command=self._save_api_key, bg=C_BG, fg=C_PRI,
                  activebackground="#003344", font=font_body_bold(12),
                  borderwidth=0, padx=18, pady=8).pack(side="left", padx=6)

        if edit_mode:
            tk.Button(buttons, text="KAPAT",
                      command=self._close_setup_ui, bg="#08111a", fg=C_DIM,
                      activebackground="#10202b", font=font_body_bold(12),
                      borderwidth=0, padx=18, pady=8).pack(side="left", padx=6)

    def _save_api_key(self):
        was_ready = self._api_key_ready
        key = self.api_entry.get().strip() if self.api_entry else ""
        if not key:
            return
        youtube_key = self.youtube_api_entry.get().strip() if self.youtube_api_entry else ""
        youtube_handle = self.youtube_handle_entry.get().strip() if self.youtube_handle_entry else ""
        save_app_config(
            {
                "gemini_api_key": key,
                "youtube_api_key": youtube_key,
                "youtube_channel_handle": youtube_handle,
                "voice": self._current_voice,
            }
        )
        self._close_setup_ui()
        self._api_key_ready = True
        self._refresh_settings_status()
        if was_ready:
            self.write_log("SYS: API ayarlari guncellendi.")
        else:
            self.set_state("LISTENING")
            self.write_log("SYS: RIPPERINIO hazır. Dinliyorum...")
