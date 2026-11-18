#!/usr/bin/env python3
"""
RIPPERINIO Windows — Gercek zamanli sesli yardimci cekirdegi
macOS ortamina uyarlanmis calisma akisi
"""

import asyncio
import datetime
import threading
import traceback
import os
import subprocess
import sys
import time
import re
from pathlib import Path

from actions.platform_utils import (
    IS_WIN,
    acquire_single_instance,
    attach_parent_console,
    configure_console_output,
    focus_window,
)

# Konsol ciktisini pythonw (konsolsuz) ve Turkce kod sayfasi icin guvenli yap.
# Diger importlardan once calismali — bazi moduller import aninda yazdirir.
configure_console_output()

# UAC helper: sadece pc_control.py içindeki dar, korumalı görevleri çalıştırır.
if "--rip-admin-helper" in sys.argv:
    from actions.pc_control import run_admin_helper_cli
    _idx = sys.argv.index("--rip-admin-helper")
    _job = sys.argv[_idx + 1] if _idx + 1 < len(sys.argv) else ""
    raise SystemExit(run_admin_helper_cli(_job))

import pyaudio
import psutil
from google import genai
from google.genai import types

from app_config import get_app_config_value
from ui import RipUI
from memory.memory_manager import load_memory, update_memory, delete_memory, format_memory_for_prompt
from actions.open_app import open_app
from actions.sys_info  import sys_info
from actions.calendar import get_calendar_events, add_calendar_event, delete_calendar_event
from actions.reminders import get_reminders, add_reminder
from actions.browser   import browser_control
from actions.shell     import shell_run
from actions.whatsapp  import send_whatsapp_message, save_whatsapp_contact
from actions.media     import play_media, control_media
from actions.system_volume import control_volume
from actions.weather   import get_weather_summary
from actions.health    import get_health_data
from actions.screen_vision import analyze_screen, locate_screen_target
from actions.clipboard_sync import clipboard_sync
from actions.youtube_stats import get_youtube_channel_report
from actions.pc_control import (
    app_control, window_control, file_control, process_control,
    service_control, windows_control, shell_control,
)
from actions.ui_navigation import ui_navigate
from actions.ui_control import ui_control
from actions.phone_bridge import phone_messages, phone_status, start_phone_bridge_watcher
from actions.phone_control import phone_control
from actions.theme_control import set_hud_theme
from actions.obs_control import obs_control
from wake_word import WakeWordStream
from plugin_manager import PluginManager, ensure_plugin_readme
from hud_services import ACTION_HISTORY
from v6_core import doctor_report, network_report, apply_profile, safe_backup, run_macro, load_macros

# ── Paths ───────────────────────────────────────────────────────────────────
from app_paths import resource_path

BASE_DIR        = Path(__file__).resolve().parent
PROMPT_PATH     = resource_path("core", "prompt.txt")


# ── WebcamStreamer ──────────────────────────────────────────────────────────
class WebcamStreamer:
    """
    Webcam'dan sürekli kare çeker ve en güncel JPEG'i bellekte tutar.
    Queue yerine tek bir 'latest frame' yaklaşımı — eski kare birikimi olmaz.
    """

    JPEG_QUALITY = 72
    MAX_DIM      = 640
    WARMUP       = 6
    # Gecici okuma hatalari normaldir (USB kamera, guc tasarrufu, baska
    # uygulamanin kisa erisimi). Tek hatada akisi kapatmak yerine tolere et.
    MAX_READ_FAILURES = 30
    OPEN_ATTEMPTS = 3
    CAPTURE_INTERVAL = 0.05   # ~20 FPS yakalama (CPU dostu)

    def __init__(self):
        self._latest: bytes | None = None
        self._lock   = threading.Lock()
        self._active = False
        self._thread: threading.Thread | None = None

    @property
    def is_active(self) -> bool:
        return self._active

    def get_latest_frame(self) -> bytes | None:
        """Thread-safe, her zaman en güncel kareyi döner."""
        with self._lock:
            return self._latest

    def start(self) -> str:
        with self._lock:
            if self._active:
                return "already_active"

        # Onceki cekim thread'i kamerayi serbest birakana kadar BEKLE.
        # Beklemezsek yeni VideoCapture "cihaz mesgul" diye acilamiyordu ve
        # kamera kapatilip acildiktan sonra bir daha calismiyordu.
        previous = self._thread
        if previous and previous.is_alive():
            previous.join(timeout=3.0)

        with self._lock:
            self._active = True
            self._latest = None
        t = threading.Thread(target=self._run, daemon=True)
        self._thread = t
        t.start()
        return "ok"

    def stop(self):
        with self._lock:
            self._active = False
            self._latest = None

    def _open_capture(self, cv2):
        """
        Kamerayi acar. Windows'ta DirectShow varsayilandan belirgin hizli
        acilir (~1 sn). Kamera bir onceki oturumdan henuz serbest kalmamis
        olabilecegi icin birkac kez denenir.
        """
        backends = []
        if IS_WIN:
            backends.append(("DirectShow", lambda: cv2.VideoCapture(0, cv2.CAP_DSHOW)))
        backends.append(("varsayilan", lambda: cv2.VideoCapture(0)))

        for attempt in range(1, self.OPEN_ATTEMPTS + 1):
            for label, factory in backends:
                try:
                    cap = factory()
                except Exception:
                    continue
                if cap is not None and cap.isOpened():
                    return cap
                try:
                    if cap is not None:
                        cap.release()
                except Exception:
                    pass
            if attempt < self.OPEN_ATTEMPTS:
                # Kamerayi baska bir surec/onceki thread hala tutuyor olabilir
                time.sleep(0.6)
        return None

    def _run(self):
        try:
            import cv2
        except ImportError:
            print("[Webcam] opencv-python yüklü değil.")
            with self._lock:
                self._active = False
            return

        cap = self._open_capture(cv2)
        if cap is None:
            print("[Webcam] Kamera açılamadı.")
            with self._lock:
                self._active = False
            return

        # Isınma — sensörün otomatik pozlaması oturuncaya kadar bekle
        for _ in range(self.WARMUP):
            cap.read()

        enc_params = [cv2.IMWRITE_JPEG_QUALITY, self.JPEG_QUALITY]
        read_failures = 0

        try:
            while True:
                with self._lock:
                    if not self._active:
                        break

                ret, frame = cap.read()
                if not ret or frame is None:
                    # Tek basarisiz okuma akisi oldurmesin — bir sure dene
                    read_failures += 1
                    if read_failures >= self.MAX_READ_FAILURES:
                        print("[Webcam] Kameradan kare alinamiyor, akis durduruldu.")
                        break
                    time.sleep(0.05)
                    continue
                read_failures = 0

                h, w = frame.shape[:2]
                if max(h, w) > self.MAX_DIM:
                    s = self.MAX_DIM / max(h, w)
                    frame = cv2.resize(frame, (int(w * s), int(h * s)))

                frame = cv2.flip(frame, 1)  # yatay ayna — hem UI hem AI tutarlı
                ok, buf = cv2.imencode(".jpg", frame, enc_params)
                if ok:
                    with self._lock:
                        self._latest = buf.tobytes()

                # ~20 FPS yakala. Onceden 33 FPS'ti ama her kare JPEG'e
                # kodlandigi icin bosuna CPU yiyordu; UI onizlemesi icin
                # 20 FPS gozle ayirt edilemeyecek kadar akici.
                time.sleep(self.CAPTURE_INTERVAL)
        finally:
            cap.release()
            with self._lock:
                self._active = False
                self._latest = None
            print("[Webcam] Kamera serbest bırakıldı.")


CONTROL_TOKEN_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

# ── Model ───────────────────────────────────────────────────────────────────
LIVE_MODEL = "models/gemini-2.5-flash-native-audio-latest"

# Webcam acikken modele kac saniyede bir kare gonderilecek.
# 0.5 sn = akici "canli goruyor" hissi. Bilgisayar zorlanirsa (eski makine,
# yuksek CPU) bu degeri 1.0-1.5'e cikarmak yeterlidir.
WEBCAM_SEND_INTERVAL = 0.5

# ── Audio ───────────────────────────────────────────────────────────────────
FORMAT           = pyaudio.paInt16
CHANNELS         = 1
SEND_SAMPLE_RATE = 16000
RECV_SAMPLE_RATE = 24000
CHUNK_SIZE       = 1024
# Wake ile açılan konuşma, son gerçek konuşma/yanıttan sonra bu kadar süre açık kalır.
# Eski 10-15 sn pencere çok kısaydı ve 2-3 tur sonra sessizce kapanıyordu.
WAKE_IDLE_SECONDS = 45.0
WAKE_INITIAL_SECONDS = 60.0
pya              = pyaudio.PyAudio()

# ── Tool tanımları + izole plugin araçları ─────────────────────────────────
from tool_defs import TOOL_DECLARATIONS as CORE_TOOL_DECLARATIONS

ensure_plugin_readme()
PLUGIN_MANAGER = PluginManager()
PLUGIN_MANAGER.load_all({"surface": "desktop"})
TOOL_DECLARATIONS = list(CORE_TOOL_DECLARATIONS) + list(PLUGIN_MANAGER.tool_declarations)


def get_api_key() -> str:
    return str(get_app_config_value("gemini_api_key", "") or "")


def load_system_prompt() -> str:
    # Prompt tek dosyada tutulur; Windows'a uyarlamayi prompt_loader yapar.
    from prompt_loader import load_system_prompt as _load

    return _load()


class RipLive:
    def __init__(self, ui: RipUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._music_proc    = None
        self._webcam_streamer = WebcamStreamer()

        self.ui.on_text_command  = self._on_text_command
        self.ui.on_pause_toggle  = self._on_pause_toggle
        self.ui.on_effects_state_change = self._on_effects_state_change
        self.ui.on_webcam_toggle = self._on_webcam_toggle_ui
        self.ui.on_ptt_change = self._on_ptt_change
        self.ui.on_voice_input_mode_change = self._on_voice_input_mode_change
        self.ui.on_undo_request = self._on_undo_request
        self._paused             = False
        self._ptt_active         = False
        self._wake_open_until    = 0.0
        self._wake_was_open      = False
        self._wake_last_activity = 0.0
        self._playback_active    = False
        self._wake_detector = WakeWordStream(
            self._on_wake_detected,
            sample_rate=SEND_SAMPLE_RATE,
            on_status=self._on_wake_status,
        )
        self._wake_detector.start()
        self._wake_detector.set_enabled(getattr(self.ui, "voice_input_mode", "continuous") == "wake")

        # Mikrofon yokken Gemini Live session'ini tekrar tekrar dusurmemek icin
        # ses girisini bagimsiz takip ediyoruz. Mikrofon sonradan takilirsa
        # _listen_audio dongusu otomatik olarak yeniden algilar.
        self._mic_last_available: bool | None = None
        self._mic_last_name = ""

    def _on_pause_toggle(self, paused: bool):
        self._paused = paused
        if paused:
            self._stop_music()

    def _on_effects_state_change(self, enabled: bool):
        if not enabled:
            self._stop_music()

    def _on_ptt_change(self, active: bool):
        self._ptt_active = bool(active)
        if not active and self._loop and self.session:
            try:
                asyncio.run_coroutine_threadsafe(
                    self.session.send_realtime_input(audio_stream_end=True), self._loop
                )
            except Exception:
                pass

    def _on_voice_input_mode_change(self, mode: str):
        mode = str(mode or "continuous")
        self._ptt_active = False
        self._wake_open_until = 0.0
        self._wake_was_open = False
        self._wake_last_activity = 0.0
        try:
            self._wake_detector.set_enabled(mode == "wake")
        except Exception:
            pass
        if self._loop and self.session:
            try:
                asyncio.run_coroutine_threadsafe(
                    self.session.send_realtime_input(audio_stream_end=True), self._loop
                )
            except Exception:
                pass
        try:
            if mode == "wake":
                self.ui.set_state("STANDBY")
                self.ui.write_log("SYS: Ses modu HEY · yerel wake bekleniyor.")
            elif mode == "ptt":
                self.ui.set_state("STANDBY")
            else:
                self.ui.set_state("LISTENING")
        except Exception:
            pass

    def _on_wake_status(self, code: str, detail: str = ""):
        """Yerel wake motorunun sadece anlamli durumlarini HUD'a tasir."""
        code = str(code or "")
        detail = str(detail or "")
        if code == "ready":
            msg, level = "HEY yerel ses profili hazir.", "INFO"
        elif code == "wake":
            msg, level = "HEY yerel olarak algilandi.", "INFO"
        elif code == "needs_calibration":
            msg, level = "HEY profili yok · RIP_SES_KALIBRE.bat calistir.", "WARN"
        elif code == "error":
            msg, level = f"HEY yerel motor hatasi: {detail}", "WARN"
        else:
            # candidate/long_reject gibi normal arka-plan olaylari AKIS'i spamlamaz.
            return
        try:
            self.ui.write_debug(msg, level=level)
        except Exception:
            pass

    def _touch_wake_session(self, seconds: float = WAKE_IDLE_SECONDS):
        if str(getattr(self.ui, "voice_input_mode", "continuous") or "continuous") != "wake":
            return
        now = time.monotonic()
        self._wake_last_activity = now
        self._wake_open_until = max(self._wake_open_until, now + max(5.0, float(seconds)))
        self._wake_was_open = True

    def _on_wake_detected(self, command: str = ""):
        # Wake artik STT metnine degil, yerel akustik profile dayanir.
        self._touch_wake_session(WAKE_INITIAL_SECONDS)
        try:
            self._wake_detector.clear()
            self._wake_detector.set_enabled(False)
        except Exception:
            pass

        def _notify():
            self.ui.write_log("SYS: Hey algilandi · konuşma oturumu açıldı.")
            self.ui.set_state("LISTENING")
            spoken_command = str(command or "").strip()
            if spoken_command:
                self._on_text_command(spoken_command)
            elif str(getattr(self.ui, "voice_input_mode", "continuous")) == "wake":
                # Kisa sesli onay; oturum bunun ardindan 45 sn sessizlige kadar acik kalir.
                self._send_internal_text(
                    "Kullanici seni 'Hey' diyerek uyandirdi. "
                    "Yalnizca cok kisa ve dogal bir sesli onay ver: 'Dinliyorum.'"
                )
        try:
            self.ui.root.after(0, _notify)
        except Exception:
            pass

    def _voice_gate_open(self) -> bool:
        mode = str(getattr(self.ui, "voice_input_mode", "continuous") or "continuous")
        if mode == "ptt":
            return bool(self._ptt_active)
        if mode == "wake":
            opened = time.monotonic() < self._wake_open_until
            if not opened and self._wake_was_open:
                self._wake_was_open = False
            return opened
        return True

    @staticmethod
    def _looks_like_virtual_input(name: str) -> bool:
        n = str(name or "").strip().lower()
        virtual_hints = (
            "stereo mix", "what u hear", "wave out mix",
            "cable output", "vb-audio", "virtual audio",
            "voicemeeter output",
        )
        return any(hint in n for hint in virtual_hints)

    def _microphone_candidates(self):
        """Default input'u tercih eder; bozuksa diger fiziksel inputlara duser."""
        preferred = []
        fallback_virtual = []
        default_index = None
        try:
            info = pya.get_default_input_device_info()
            default_index = int(info.get("index", -1))
        except Exception:
            pass

        try:
            count = int(pya.get_device_count())
        except Exception:
            count = 0

        seen = set()
        indices = []
        if default_index is not None and default_index >= 0:
            indices.append(default_index)
        indices.extend(i for i in range(count) if i != default_index)

        for index in indices:
            if index in seen:
                continue
            seen.add(index)
            try:
                info = pya.get_device_info_by_index(index)
                if int(info.get("maxInputChannels", 0) or 0) <= 0:
                    continue
                name = str(info.get("name", "Mikrofon") or "Mikrofon").strip()
            except Exception:
                continue
            item = (int(index), name)
            if self._looks_like_virtual_input(name):
                fallback_virtual.append(item)
            else:
                preferred.append(item)

        # Fiziksel input yoksa kullanicinin bilerek sectigi sanal inputu tamamen
        # reddetmek yerine son care olarak dene.
        return preferred + fallback_virtual

    def _find_usable_microphone(self):
        candidates = self._microphone_candidates()
        return candidates[0] if candidates else None

    def _open_usable_microphone(self):
        errors = []
        for device_index, device_name in self._microphone_candidates():
            try:
                stream = pya.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=SEND_SAMPLE_RATE,
                    input=True,
                    input_device_index=device_index,
                    frames_per_buffer=CHUNK_SIZE,
                )
                return stream, device_index, device_name, errors
            except Exception as exc:
                errors.append(f"{device_name}: {exc}")
        return None, -1, "", errors

    def _publish_mic_status(self, available: bool, name: str = ""):
        available = bool(available)
        name = str(name or "").strip()
        changed = (
            self._mic_last_available is None
            or self._mic_last_available != available
            or (available and name != self._mic_last_name)
        )
        self._mic_last_available = available
        self._mic_last_name = name if available else ""
        if not changed:
            return

        def _apply():
            try:
                self.ui.set_mic_status(available, name)
            except Exception:
                try:
                    self.ui.mic_available = available
                except Exception:
                    pass

            if available:
                mode = str(getattr(self.ui, "voice_input_mode", "continuous") or "continuous")
                if mode == "wake" and not self._voice_gate_open():
                    self.ui.set_state("STANDBY")
                    self.ui.write_log(
                        f"SYS: Mikrofon hazir ({name}) · Hey bekleniyor."
                        if name else "SYS: Mikrofon hazir · Hey bekleniyor."
                    )
                else:
                    self.ui.set_state("LISTENING")
                    self.ui.write_log(
                        f"SYS: Mikrofon hazir ({name}). Dinliyorum."
                        if name else "SYS: Mikrofon hazir. Dinliyorum."
                    )
            else:
                self.ui.set_state("STANDBY")
                self.ui.write_log(
                    "SYS: Mikrofon bulunamadi — STANDBY. "
                    "Mikrofon baglaninca otomatik etkinlesecek."
                )

        try:
            self.ui.root.after(0, _apply)
        except Exception:
            _apply()

    def _on_webcam_toggle_ui(self, activate: bool):
        if activate:
            status = self._webcam_streamer.start()
            self.ui.set_webcam_active(status == "ok" or status == "already_active")
        else:
            self._webcam_streamer.stop()
            self.ui.set_webcam_active(False)

    def _focus_ui_section_for_tool(self, tool_name: str, args: dict):
        if tool_name == "sys_info":
            query = str(args.get("query", "")).strip().lower()
            if query in {"time", "saat", "zaman", "date", "tarih"}:
                self.ui.focus_panel("time", duration_ms=5200)
            else:
                self.ui.focus_panel("system", duration_ms=5200)
        elif tool_name == "get_weather":
            self.ui.focus_panel("weather", duration_ms=5600)

    def _send_internal_text(self, text: str):
        if self._paused or not self._loop or not self.session:
            return False
        try:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"parts": [{"text": str(text)}]},
                    turn_complete=True,
                ),
                self._loop,
            )
            return True
        except Exception:
            return False

    def _on_text_command(self, text: str):
        if self._paused:
            return
        self.ui.write_log(f"Siz: {text}")
        try:
            ACTION_HISTORY.add("command", f"KOMUT → {str(text)[:180]}")
        except Exception:
            pass
        if not self._loop or not self.session:
            self.ui.write_log("ERR: RIPPERINIO bağlantısı henüz hazır değil.")
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _on_undo_request(self):
        result = ACTION_HISTORY.undo_last()
        message = str(result.get("message") or "Geri alma tamamlandı.")
        post = dict(result.get("post") or {})

        def apply_ui():
            try:
                if post.get("theme"):
                    self.ui.apply_hud_theme_live(str(post["theme"]), persist=False)
            except Exception:
                pass
            try:
                self.ui.write_log("SYS: " + message)
            except Exception:
                pass

        try:
            self.ui.root.after(0, apply_ui)
        except Exception:
            apply_ui()
        return message

    async def _interrupt_audio(self):
        try:
            if self.audio_in_queue:
                while not self.audio_in_queue.empty():
                    try:
                        self.audio_in_queue.get_nowait()
                    except Exception:
                        break
            if self.session:
                await self.session.send_realtime_input(audio_stream_end=True)
            self.set_speaking(False)
        except Exception:
            pass

    def _stop_music(self):
        proc = self._music_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        self._music_proc = None

    def set_speaking(self, value: bool):
        value = bool(value)
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
            return
        mode = str(getattr(self.ui, "voice_input_mode", "continuous") or "continuous")
        if mode == "wake" and not self._voice_gate_open():
            self.ui.set_state("STANDBY")
        elif mode == "ptt" and not self._ptt_active:
            self.ui.set_state("STANDBY")
        else:
            self.ui.set_state("LISTENING")

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.ui.write_debug(f"{tool_name}: {short}", level="ERROR")
        self.ui.set_state("ERROR")

    @staticmethod
    def _result_looks_like_error(result) -> bool:
        text = str(result or "").strip().lower()
        if not text:
            return False
        error_markers = (
            "hata",
            "error",
            "alinamadi",
            "alınamadı",
            "bulunamadi",
            "bulunamadı",
            "acilamadi",
            "açılamadı",
            "tamamlanamadi",
            "tamamlanamadı",
            "gecersiz",
            "geçersiz",
            "izin gerekiyor",
            "izin gerekli",
            "baglanti",
            "bağlantı",
            "gerekli.",
        )
        return any(marker in text for marker in error_markers)

    @staticmethod
    def _should_play_success_sfx(tool_name: str, args: dict, result) -> bool:
        action_tools = {
            "open_app",
            "add_calendar_event",
            "add_reminder",
            "delete_calendar_event",
            "remove_calendar_event",
        }
        if tool_name in action_tools:
            return True

        if tool_name == "send_whatsapp_message":
            text = str(result or "").lower()
            if bool(args.get("send_now", False)):
                return "gönderildi" in text or "gonderildi" in text
            return False

        return False

    @staticmethod
    def _clean_transcript_text(text: str) -> tuple[str, bool]:
        raw = str(text or "")
        had_noise = False
        if CONTROL_TOKEN_RE.search(raw):
            had_noise = True
            raw = CONTROL_TOKEN_RE.sub(" ", raw)
        cleaned = []
        for ch in raw:
            if ch in "\n\r\t" or ord(ch) >= 32:
                cleaned.append(ch)
            else:
                had_noise = True
        normalized = " ".join("".join(cleaned).split())
        return normalized.strip(), had_noise

    def _build_config(self) -> types.LiveConnectConfig:
        import datetime
        memory  = load_memory()
        mem_str = format_memory_for_prompt(memory)
        sys_p   = load_system_prompt()
        now     = datetime.datetime.now()
        time_ctx = f"[ŞU ANKİ ZAMAN]\n{now.strftime('%A, %d %B %Y — %H:%M')}\n\n"

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str + "\n\n")
        parts.append(sys_p)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=str(get_app_config_value("voice", "Charon") or "Charon")
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        print(f"[RIP] 🔧 {name} {args}")
        self.ui.set_state("PROCESSING")
        try:
            undo_snapshot = ACTION_HISTORY.capture_undo_for_tool(name, args)
        except Exception:
            undo_snapshot = None

        loop   = asyncio.get_event_loop()
        result = "Tamam."
        had_exception = False

        try:
            if name == "save_memory":
                cat = str(args.get("category", "") or "notes").strip() or "notes"
                key = str(args.get("key", "") or "").strip()
                val = str(args.get("value", "") or "").strip()
                if not key or not val:
                    # Eskiden bu durumda da "ok" donuyordu; model kaydettigini
                    # saniyor ama hicbir sey yazilmiyordu. Artik durumu bildiriyoruz.
                    result = (
                        "Hafizaya yazilamadi: key ve value dolu olmali. "
                        "Ornek: category='identity', key='name', value='Alp'"
                    )
                else:
                    update_memory({cat: {key: {"value": val}}})
                    print(f"[Memory] 💾 {cat}/{key} = {val}")
                    result = f"Hafizaya kaydedildi: {cat}/{key} = {val}"

            elif name == "delete_memory":
                result = delete_memory(
                    args.get("category", ""),
                    args.get("key", ""),
                    args.get("match_text", ""),
                )

            elif name == "open_app":
                r = await loop.run_in_executor(
                    None, lambda: open_app(args.get("app_name", "")))
                result = r or f"{args.get('app_name')} açıldı."

            elif name == "sys_info":
                self._focus_ui_section_for_tool(name, args)
                r = await loop.run_in_executor(
                    None, lambda: sys_info(args.get("query", "all")))
                result = r or "Bilgi alındı."

            elif name == "get_health_data":
                r = await loop.run_in_executor(
                    None, lambda: get_health_data(args.get("query", "all")))
                result = r or "Saglik verisi alindi."

            elif name == "get_weather":
                self._focus_ui_section_for_tool(name, args)
                r = await loop.run_in_executor(
                    None, lambda: get_weather_summary(args.get("location") or None))
                result = r or "Hava durumu bilgisi alindi."

            elif name == "get_calendar_events":
                r = await loop.run_in_executor(
                    None,
                    lambda: get_calendar_events(
                        args.get("query", "today"),
                        int(args.get("limit", 6) or 6),
                    ),
                )
                result = r or "Takvim bilgisi alindi."

            elif name == "add_calendar_event":
                r = await loop.run_in_executor(
                    None,
                    lambda: add_calendar_event(
                        args.get("title", ""),
                        args.get("start_iso", ""),
                        args.get("end_iso", ""),
                        args.get("notes", ""),
                        args.get("location", ""),
                        args.get("calendar_name", ""),
                        bool(args.get("all_day", False)),
                    ),
                )
                result = r or "Takvim etkinligi eklendi."

            elif name == "delete_calendar_event":
                r = await loop.run_in_executor(
                    None,
                    lambda: delete_calendar_event(
                        args.get("title", ""),
                        args.get("start_iso", ""),
                        args.get("calendar_name", ""),
                        bool(args.get("delete_all_matches", False)),
                    ),
                )
                result = r or "Takvim etkinligi silindi."

            elif name == "get_reminders":
                r = await loop.run_in_executor(
                    None,
                    lambda: get_reminders(
                        args.get("query", "upcoming"),
                        int(args.get("limit", 8) or 8),
                        args.get("list_name", ""),
                    ),
                )
                result = r or "Animsatici bilgisi alindi."

            elif name == "add_reminder":
                r = await loop.run_in_executor(
                    None,
                    lambda: add_reminder(
                        args.get("title", ""),
                        args.get("due_iso", ""),
                        args.get("notes", ""),
                        args.get("list_name", ""),
                        args.get("priority", ""),
                        bool(args.get("all_day", False)),
                    ),
                )
                result = r or "Animsatici eklendi."

            elif name == "browser_control":
                r = await loop.run_in_executor(
                    None, lambda: browser_control(
                        args.get("action"),
                        args.get("url"),
                        args.get("query")
                    ))
                result = r or "Tamam."

            elif name == "shell_run":
                r = await loop.run_in_executor(
                    None, lambda: shell_run(args.get("command", "")))
                result = r or "Komut çalıştırıldı."

            elif name == "toggle_webcam":
                action = str(args.get("action", "start")).strip().lower()
                if action == "start":
                    status = self._webcam_streamer.start()
                    if status == "ok":
                        self.ui.set_webcam_active(True)
                        result = (
                            "Webcam akışı başlatıldı. "
                            "Artık kameranı görüyorum — dilediğin zaman soru sorabilirsin."
                        )
                    elif status == "already_active":
                        result = "Webcam zaten açık, görüntü alıyorum."
                    else:
                        result = "Webcam başlatılamadı: opencv-python yüklü değil."
                else:
                    self._webcam_streamer.stop()
                    self.ui.set_webcam_active(False)
                    result = "Webcam akışı durduruldu."

            elif name == "play_media":
                r = await loop.run_in_executor(
                    None,
                    lambda: play_media(
                        args.get("query", ""),
                        args.get("provider", "auto"),
                        bool(args.get("autoplay", True)),
                    ),
                )
                result = r or "Medya oynatma başlatıldı."

            elif name == "control_media":
                r = await loop.run_in_executor(
                    None, lambda: control_media(args.get("action", "pause")))
                result = r or "Medya komutu gonderildi."

            elif name == "control_volume":
                r = await loop.run_in_executor(
                    None,
                    lambda: control_volume(
                        args.get("action", "get"),
                        args.get("value"),
                    ),
                )
                result = r or "Ses seviyesi güncellendi."

            elif name == "ui_control":
                r = await loop.run_in_executor(
                    None,
                    lambda: ui_control(
                        args.get("action", "click"),
                        int(args.get("x")) if args.get("x") is not None else None,
                        int(args.get("y")) if args.get("y") is not None else None,
                        args.get("text", ""), args.get("keys", ""),
                        args.get("button", "left"), int(args.get("clicks", 1) or 1),
                        0.05, bool(args.get("confirmed", False)),
                    ),
                )
                result = r or "UI işlemi tamamlandı."

            elif name == "ui_navigate":
                r = await loop.run_in_executor(
                    None,
                    lambda: ui_navigate(
                        args.get("action", "click"), args.get("window_title", ""),
                        args.get("target", ""), args.get("text", ""), args.get("keys", ""),
                        args.get("direction", "down"), int(args.get("amount", 5) or 5),
                        args.get("method", "auto"), bool(args.get("clear_first", True)),
                        bool(args.get("admin", False)), bool(args.get("confirmed", False)),
                    ),
                )
                result = r or "Uygulama içi navigasyon tamamlandı."

            elif name == "app_control":
                r = await loop.run_in_executor(None, lambda: app_control(
                    args.get("action", "open"), args.get("app_name", ""),
                    bool(args.get("admin", False)), bool(args.get("force", False)),
                    args.get("arguments", ""), bool(args.get("confirmed", False))))
                result = r or "Uygulama işlemi tamamlandı."

            elif name == "window_control":
                r = await loop.run_in_executor(None, lambda: window_control(
                    args.get("action", "list"), args.get("title", ""), args.get("x"), args.get("y"),
                    args.get("width"), args.get("height")))
                result = r or "Pencere işlemi tamamlandı."

            elif name == "file_control":
                r = await loop.run_in_executor(None, lambda: file_control(
                    args.get("action", ""), args.get("source", ""), args.get("target", ""),
                    args.get("content", ""), bool(args.get("overwrite", False)),
                    bool(args.get("admin", False)), bool(args.get("confirmed", False))))
                result = r or "Dosya işlemi tamamlandı."

            elif name == "process_control":
                r = await loop.run_in_executor(None, lambda: process_control(
                    args.get("action", "list"), args.get("query", ""),
                    int(args.get("pid")) if args.get("pid") is not None else None,
                    bool(args.get("force", False)), bool(args.get("admin", False)),
                    bool(args.get("confirmed", False)), int(args.get("limit", 20) or 20)))
                result = r or "Süreç işlemi tamamlandı."

            elif name == "service_control":
                r = await loop.run_in_executor(None, lambda: service_control(
                    args.get("action", "status"), args.get("service_name", ""),
                    bool(args.get("confirmed", False)), int(args.get("limit", 30) or 30)))
                result = r or "Hizmet işlemi tamamlandı."

            elif name == "windows_control":
                r = await loop.run_in_executor(None, lambda: windows_control(
                    args.get("action", "open_settings"), args.get("page", ""),
                    args.get("adapter", ""), args.get("value"), bool(args.get("confirmed", False))))
                result = r or "Windows işlemi tamamlandı."

            elif name == "shell_control":
                r = await loop.run_in_executor(None, lambda: shell_control(
                    args.get("shell", "powershell"), args.get("command", ""),
                    bool(args.get("admin", False)), bool(args.get("confirmed", False)),
                    int(args.get("timeout", 30) or 30)))
                result = r or "Komut tamamlandı."

            elif name == "phone_messages":
                r = await loop.run_in_executor(None, lambda: phone_messages(
                    args.get("action", "latest"), int(args.get("limit", 10) or 10),
                    args.get("app_filter", "")))
                result = r or "Telefon mesajları alındı."

            elif name == "phone_status":
                r = await loop.run_in_executor(None, lambda: phone_status(args.get("query", "all")))
                result = r or "Telefon durumu alındı."

            elif name == "phone_control":
                r = await loop.run_in_executor(None, lambda: phone_control(
                    args.get("action", ""), args.get("target", ""),
                    int(args.get("value")) if args.get("value") is not None else None,
                    bool(args.get("confirmed", False))))
                result = r or "Telefon işlemi tamamlandı."

            elif name == "obs_control":
                result = await loop.run_in_executor(
                    None, lambda: obs_control(args.get("action", "status"), args.get("target", ""))
                )

            elif name == "rip_system":
                action = str(args.get("action", "doctor") or "doctor").strip().casefold()
                if action in {"doctor", "repair"}:
                    rep = await loop.run_in_executor(None, lambda: doctor_report(repair=(action == "repair")))
                    lines = [f"RIP HEALTH: {rep.get('score', 0)}/100"]
                    for item in rep.get("checks", []):
                        lines.append(f"{'OK' if item.get('ok') else 'HATA'} {item.get('name')}: {item.get('detail')}")
                    result = "\n".join(lines)
                elif action == "profile":
                    result = await loop.run_in_executor(None, lambda: apply_profile(args.get("target", "")))
                elif action == "network":
                    result = await loop.run_in_executor(None, network_report)
                elif action == "backup":
                    out = await loop.run_in_executor(None, safe_backup)
                    result = f"Güvenli RIP yedeği oluşturuldu: {out}"
                elif action == "macro":
                    result = await loop.run_in_executor(None, lambda: run_macro(args.get("target", "")))
                elif action == "macro_list":
                    names = sorted(load_macros())
                    result = "Kayıtlı makrolar: " + (", ".join(names) if names else "henüz yok")
                else:
                    result = "Bilinmeyen RIP V6 sistem işlemi."

            elif name == "hud_theme":
                theme_name = args.get("theme", "classic")
                r = await loop.run_in_executor(None, lambda: set_hud_theme(theme_name))
                self.ui.root.after(0, lambda n=theme_name: self.ui.apply_hud_theme_live(n, persist=False))
                result = r or "HUD teması değiştirildi."

            elif name == "get_youtube_channel_report":
                r = await loop.run_in_executor(
                    None,
                    lambda: get_youtube_channel_report(
                        args.get("query", "overview"),
                        args.get("handle", ""),
                        int(args.get("video_limit", 6) or 6),
                    ),
                )
                result = r or "YouTube kanal raporu alindi."

            elif name == "analyze_screen":
                r = await loop.run_in_executor(
                    None,
                    lambda: analyze_screen(
                        args.get("query", "Ekranda ne var?"),
                        args.get("target", "active_window"),
                    ),
                )
                result = r or "Ekran analizi tamamlandi."

            elif name == "screen_target":
                target_result = await loop.run_in_executor(
                    None,
                    lambda: locate_screen_target(
                        args.get("query", "Ekrandaki hedefi bul"),
                        args.get("target", "active_window"),
                    ),
                )
                if isinstance(target_result, dict) and target_result.get("found"):
                    self.ui.root.after(0, lambda r=dict(target_result): self.ui.show_screen_target(r))
                result = str(target_result.get("message") if isinstance(target_result, dict) else target_result)

            elif name == "history_control":
                action = str(args.get("action", "latest") or "latest").strip().casefold()
                if action in {"undo", "geri al", "geri_al", "revert"}:
                    undo_result = ACTION_HISTORY.undo_last()
                    try:
                        post = dict(undo_result.get("post") or {})
                        if post.get("theme"):
                            self.ui.root.after(0, lambda n=str(post["theme"]): self.ui.apply_hud_theme_live(n, persist=False))
                    except Exception:
                        pass
                    result = str(undo_result.get("message") or "Geri alma tamamlandı.")
                else:
                    result = ACTION_HISTORY.format_recent(int(args.get("limit", 12) or 12))

            elif name == "clipboard_sync":
                r = await loop.run_in_executor(
                    None,
                    lambda: clipboard_sync(args.get("action", "send"), args.get("text", "")),
                )
                result = r or "Pano senkronu tamamlandı."

            elif name == "send_whatsapp_message":
                r = await loop.run_in_executor(
                    None,
                    lambda: send_whatsapp_message(
                        args.get("message", ""),
                        args.get("phone_number", ""),
                        args.get("recipient_name", ""),
                        bool(args.get("send_now", False)),
                        args.get("app_target", "auto"),
                    ),
                )
                result = r or "WhatsApp işlemi tamamlandı."

            elif name == "save_whatsapp_contact":
                r = await loop.run_in_executor(
                    None,
                    lambda: save_whatsapp_contact(
                        args.get("display_name", ""),
                        args.get("phone_number", ""),
                        args.get("aliases", ""),
                    ),
                )
                result = r or "WhatsApp kişisi kaydedildi."

            elif PLUGIN_MANAGER.can_execute(name):
                r = await loop.run_in_executor(None, lambda: PLUGIN_MANAGER.execute(name, args))
                result = r or "Plugin aracı tamamlandı."

            else:
                result = f"Bilinmeyen araç: {name}"

        except Exception as e:
            result = f"Hata: {e}"
            had_exception = True
            traceback.print_exc()
            self.speak_error(name, e)

        tool_failed = self._result_looks_like_error(result)
        try:
            ACTION_HISTORY.add(
                "tool",
                f"RIP TOOL → {name}",
                str(result)[:260] if tool_failed else "",
                undo=None if tool_failed else undo_snapshot,
            )
        except Exception:
            pass
        if tool_failed:
            if not had_exception:
                self.ui.set_state("ERROR")
        elif self._should_play_success_sfx(name, args, result):
            self.ui.play_success_sfx()

        if not tool_failed and not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[RIP] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _stream_webcam_frames(self):
        """
        Webcam aktifken her 1.5s'de EN GÜNCEL kareyi session'a gönderir.
        Queue'suz 'latest frame' yaklaşımı: model hep şimdiki görüntüyü görür.
        """
        _last_sent: bytes | None = None
        while True:
            if not self._webcam_streamer.is_active:
                await asyncio.sleep(0.2)
                continue

            jpeg = self._webcam_streamer.get_latest_frame()
            if jpeg is None or jpeg is _last_sent:
                await asyncio.sleep(0.2)
                continue

            _last_sent = jpeg
            try:
                await self.session.send_realtime_input(
                    media={"data": jpeg, "mime_type": "image/jpeg"}
                )
            except Exception as e:
                print(f"[Webcam] Frame gönderilemedi: {e}")

            await asyncio.sleep(WEBCAM_SEND_INTERVAL)

    async def _update_ui_webcam_preview(self):
        """UI önizlemesini ~24 FPS günceller. AI akışından bağımsız."""
        frame_interval = 1.0 / 24.0   # ~0.0417 sn → 24 FPS
        while True:
            if self._webcam_streamer.is_active:
                jpeg = self._webcam_streamer.get_latest_frame()
                if jpeg:
                    self.ui.update_webcam_preview(jpeg)
            await asyncio.sleep(frame_interval)

    async def _listen_audio(self):
        """Mikrofonu hot-plug destekli dinler; cihaz yoksa session'i dusurmez."""
        print("[RIP] 🎤 Mikrofon izleyicisi basladi")
        last_gate_open = False

        while True:
            stream = None
            device_index = -1
            device_name = ""
            try:
                stream, device_index, device_name, open_errors = await asyncio.to_thread(
                    self._open_usable_microphone
                )
                if stream is None:
                    self._publish_mic_status(False)
                    try:
                        self._wake_detector.set_enabled(False)
                    except Exception:
                        pass
                    if open_errors:
                        try:
                            self.ui.write_debug(
                                "Mikrofon acma denemeleri basarisiz: " + " | ".join(open_errors[:4]),
                                level="WARN",
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(2.0)
                    continue

                self._publish_mic_status(True, device_name)

                while True:
                    try:
                        data = await asyncio.to_thread(
                            stream.read, CHUNK_SIZE, exception_on_overflow=False
                        )
                    except Exception as read_error:
                        print(f"[RIP] Mikrofon baglantisi kesildi: {read_error}")
                        break

                    with self._speaking_lock:
                        ripperinio_speaking = self._is_speaking

                    mode = str(getattr(self.ui, "voice_input_mode", "continuous") or "continuous")
                    gate_open = self._voice_gate_open()

                    if (
                        mode == "wake"
                        and not gate_open
                        and not ripperinio_speaking
                        and not self.ui.muted
                        and not self._paused
                    ):
                        self._wake_detector.set_enabled(True)
                        self._wake_detector.feed(data)
                    else:
                        self._wake_detector.set_enabled(False)

                    if gate_open and not ripperinio_speaking and not self.ui.muted and not self._paused:
                        await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})

                    if last_gate_open and not gate_open and self.session:
                        try:
                            await self.session.send_realtime_input(audio_stream_end=True)
                        except Exception:
                            pass
                        if mode == "wake":
                            try:
                                self.ui.set_state("STANDBY")
                            except Exception:
                                pass
                    last_gate_open = gate_open

            except Exception as open_error:
                print(f"[RIP] Mikrofon acilamadi: {open_error}")
            finally:
                try:
                    self._wake_detector.set_enabled(False)
                except Exception:
                    pass
                if stream is not None:
                    try:
                        await asyncio.to_thread(stream.stop_stream)
                    except Exception:
                        pass
                    try:
                        await asyncio.to_thread(stream.close)
                    except Exception:
                        pass

            self._publish_mic_status(False)
            last_gate_open = False
            await asyncio.sleep(2.0)

    async def _receive_audio(self):
        print("[RIP] 👂 Alım başladı")
        out_buf, in_buf = [], []
        output_noise = False
        output_noise_samples = []
        try:
            while True:
                async for response in self.session.receive():
                    if response.data:
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            self.set_speaking(True)
                            raw_txt = sc.output_transcription.text.strip()
                            if raw_txt:
                                txt, had_noise = self._clean_transcript_text(raw_txt)
                                if had_noise:
                                    output_noise = True
                                    if len(output_noise_samples) < 4:
                                        output_noise_samples.append(raw_txt)
                                if txt:
                                    out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)
                                if str(getattr(self.ui, "voice_input_mode", "continuous")) == "wake":
                                    self._touch_wake_session(WAKE_IDLE_SECONDS)
                                self.ui.mark_user_activity(True)

                        if sc.turn_complete:
                            self.set_speaking(False)
                            if str(getattr(self.ui, "voice_input_mode", "continuous")) == "wake" and self._wake_was_open:
                                # Yanittan sonra kullaniciya yeni bir soru/komut icin tam sure ver.
                                self._touch_wake_session(WAKE_IDLE_SECONDS)

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"Siz: {full_in}")
                                try:
                                    ACTION_HISTORY.add("command", f"SES → {full_in[:180]}")
                                except Exception:
                                    pass
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"RIPPERINIO: {full_out}")
                                if output_noise_samples:
                                    self.ui.write_debug(
                                        "Kısmen filtrelenen ses transcripti: " + " | ".join(output_noise_samples),
                                        level="WARN",
                                    )
                            elif output_noise:
                                self.ui.write_log("ERR: RIPPERINIO sesli yanıtını çözümlerken bir hata oluştu.")
                                if output_noise_samples:
                                    self.ui.write_debug(
                                        "Filtrelenen ham transcript: " + " | ".join(output_noise_samples),
                                        level="WARN",
                                    )
                                self.ui.set_state("ERROR")
                            out_buf = []
                            output_noise = False
                            output_noise_samples = []

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[RIP] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses)

        except Exception as e:
            print(f"[RIP] ❌ Alım: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        """Gemini sesini calar ve speaking kilidini kuyruk bosalinca kesin birakir.

        Eski kod son audio chunk'inda _is_speaking=True birakabiliyordu; bu da
        mikrofonu sonsuza kadar bloke ediyordu.
        """
        print("[RIP] 🔊 Ses çalma başladı")
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT, channels=CHANNELS,
            rate=RECV_SAMPLE_RATE, output=True,
        )
        playback_active = False
        last_chunk_at = 0.0
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(self.audio_in_queue.get(), timeout=0.30)
                except asyncio.TimeoutError:
                    if playback_active and time.monotonic() - last_chunk_at >= 0.24:
                        playback_active = False
                        self._playback_active = False
                        self.set_speaking(False)
                    continue

                if not chunk:
                    continue
                if not playback_active:
                    playback_active = True
                    self._playback_active = True
                    self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
                last_chunk_at = time.monotonic()
        except Exception as e:
            print(f"[RIP] ❌ Ses: {e}")
            raise
        finally:
            self._playback_active = False
            self.set_speaking(False)
            try:
                stream.close()
            except Exception:
                pass

    async def run(self):
        connect_attempts = 0
        while True:
            # Duraklatılmışsa bağlanma, bekle
            if self._paused:
                await asyncio.sleep(1)
                continue

            try:
                # Client'ı her bağlanışta yeniden oluştur ve anahtarı tazeden oku.
                # Böylece yeni girilen API anahtarı anında geçerli olur; ilk
                # deneme başarısız olsa bile otomatik tekrar (3sn) kendini onarır.
                client = genai.Client(
                    api_key=get_api_key(),
                    http_options={"api_version": "v1alpha"}
                )
                print("[RIP] 🔌 Bağlanıyor...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)

                    print("[RIP] ✅ Bağlandı.")
                    connect_attempts = 0          # başarılı bağlantı → sayaç sıfırla
                    # Dinliyor durumuna ancak gercek mikrofon akisi acildiginda gec.
                    # Bu sayede mikrofon yokken reconnect/log spam olusmaz.
                    self.ui.set_state("STANDBY")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._stream_webcam_frames())
                    tg.create_task(self._update_ui_webcam_preview())

            except Exception as e:
                print(f"[RIP] ⚠️ {e}")
                traceback.print_exc()
                self.set_speaking(False)
                # Webcam akışını durdur — yeni session'da yeniden başlayacak
                if self._webcam_streamer.is_active:
                    self._webcam_streamer.stop()
                    self.ui.set_webcam_active(False)

                connect_attempts += 1
                # İlk birkaç deneme sessiz: yeni girilen API anahtarı Google
                # tarafında saniyeler içinde aktifleşebilir. Kullanıcıya hemen
                # "hatalı anahtar" göstermeyip kısa aralıkla otomatik tekrar dene.
                if connect_attempts <= 3:
                    self.ui.set_state("INITIALISING")
                    print(f"[RIP] 🔄 Bağlanmayı tekrar deniyor ({connect_attempts}/3)...")
                    await asyncio.sleep(2)
                else:
                    self.ui.write_log(
                        f"ERR: RIPPERINIO baglanamiyor — API anahtarini ve internet "
                        f"baglantisini kontrol et. ({e})"
                    )
                    self.ui.set_state("ERROR")
                    print("[RIP] 🔄 5 saniyede yeniden bağlanıyor...")
                    await asyncio.sleep(5)


def run_selftest() -> int:
    """
    Kurulum tanisi: `RIP.exe --selftest`

    Arayuzu acmadan yollari, kritik modulleri ve temel araclari dener.
    Destek isterken kullaniciya "bunu calistirip ciktiyi gonder" demek icin.
    """
    import app_paths

    # Penceresiz .exe'de konsol yoktur → ciktiyi dosyaya da yaz.
    lines: list[str] = []

    def out(text=""):
        lines.append(str(text))
        print(text)

    out("R.I.P — kurulum tanisi\n")
    out(app_paths.describe())

    ok = bad = 0

    def check(label, fn):
        nonlocal ok, bad
        try:
            detail = fn()
            out(f"  [OK]   {label}" + (f" — {detail}" if detail else ""))
            ok += 1
        except Exception as exc:
            out(f"  [HATA] {label} — {type(exc).__name__}: {exc}")
            bad += 1

    out("\nKritik moduller:")
    for mod in ("win32com.client", "pythoncom", "win32gui", "cv2",
                "pyaudio", "mss", "psutil", "PIL.Image", "google.genai",
                "pywinauto", "pyautogui"):
        check(mod, lambda m=mod: __import__(m) and "")

    out("\nGomulu kaynaklar:")
    for rel in (("core", "prompt.txt"), ("SFX", "HUD.mp3")):
        check("/".join(rel), lambda r=rel: "bulundu" if app_paths.resource_path(*r).exists()
              else (_ for _ in ()).throw(FileNotFoundError(app_paths.resource_path(*r))))

    out("\nYazma izni:")

    def _write_probe():
        p = app_paths.data_path("config", ".probe")
        p.write_text("ok", encoding="utf-8")
        p.unlink()
        return str(p.parent)

    check("veri klasorune yazilabiliyor", _write_probe)

    out("\nAyarlar:")
    check("API anahtari girilmis mi",
          lambda: "evet" if get_app_config_value("gemini_api_key", "") else "HAYIR (uygulamadan gir)")

    out("\nV5 cekirdek:")
    check("surum", lambda: __import__("version").STAMP)
    check("tool listesinde get_health_data", lambda: "evet" if any(
        d.get("name") == "get_health_data" for d in CORE_TOOL_DECLARATIONS
    ) else (_ for _ in ()).throw(RuntimeError("tool yok")))
    check("telefon pair id", lambda: __import__("bridge_shared").token_key_id())

    out("\nAraclar:")
    check("sys_info", lambda: sys_info("battery"))
    check("takvim", lambda: get_calendar_events("today"))
    check("PowerShell", lambda: shell_run("Write-Output tamam"))
    check("COM (kisayol/Outlook icin)", lambda: _com_probe())

    out(f"\nSONUC: {ok} basarili, {bad} hatali")

    try:
        log = app_paths.data_path("ripperinio_tani.log")
        log.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nRapor kaydedildi: {log}")
    except Exception:
        pass

    return 1 if bad else 0


def _com_probe() -> str:
    from actions.platform_utils import com_context

    with com_context():
        import win32com.client

        shell = win32com.client.Dispatch("WScript.Shell")
        return "WScript.Shell olusturuldu"


def _dispatch_web_modes():
    """
    Telefon/web modlari. .exe icinde "python server.py" calistirilamadigi icin
    RIP.exe kendini bu bayraklarla yeniden cagirir.
    """
    argv = sys.argv[1:]

    if "--web" in argv:
        # Penceresiz .exe'de telefon servisleri icin ana konsola baglan;
        # adres ve QR kodu gorunsun.
        attach_parent_console()
        from ripperinio_web.launcher import run_orchestrator

        raise SystemExit(run_orchestrator())

    if "--web-server" in argv:
        # server.py kendi argparse'ini kullaniyor — kendi bayragimizi cikar
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--web-server"]
        from ripperinio_web.server import main as server_main

        raise SystemExit(server_main() or 0)

    if "--web-agent" in argv:
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--web-agent"]
        from ripperinio_web.agent import main as agent_main

        try:
            asyncio.run(agent_main())
        except KeyboardInterrupt:
            pass
        raise SystemExit(0)

    if "--persistent-bridge" in argv:
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--persistent-bridge"]
        from ripperinio_web.persistent_bridge_server import main as bridge_main
        raise SystemExit(bridge_main() or 0)

    if "--mobile-server" in argv:
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--mobile-server"]
        from ripperinio_web.mobile_remote_server import main as mobile_server_main
        raise SystemExit(mobile_server_main() or 0)

    if "--mobile-remote" in argv:
        sys.argv = [sys.argv[0]] + [a for a in argv if a != "--mobile-remote"]
        from ripperinio_web.mobile_remote_runtime import run_daemon
        raise SystemExit(run_daemon() or 0)



def main():
    if "--selftest" in sys.argv:
        raise SystemExit(run_selftest())

    _dispatch_web_modes()

    if os.environ.get("TERM_PROGRAM") == "vscode":
        print("[RIP] VS Code icinden baslatildi.")

    # Zaten calisan bir RIPPERINIO varsa yenisini acma, mevcut pencereyi one getir.
    if not acquire_single_instance():
        print("[RIP] Zaten calisiyor — mevcut pencere one getiriliyor.")
        focus_window("R.I.P")
        return

    # V6.2.1 startup self-heal: even if Windows Task Scheduler/Run startup was
    # delayed or a previous version pointed at an old folder, opening RIP itself
    # brings the persistent bridge + Mobile Remote online automatically.
    def phone_services_bootstrap():
        try:
            from ripperinio_web.launcher import ensure_phone_services
            ensure_phone_services()
        except Exception as exc:
            try:
                print(f"[RIP] Telefon servis startup self-heal: {type(exc).__name__}: {exc}")
            except Exception:
                pass

    threading.Thread(
        target=phone_services_bootstrap,
        daemon=True,
        name="rip-phone-services-autostart",
    ).start()

    ui = RipUI()
    start_phone_bridge_watcher(ui)

    def runner():
        ui.wait_for_api_key()
        ripperinio = RipLive(ui)
        try:
            asyncio.run(ripperinio.run())
        except KeyboardInterrupt:
            print("\n🔴 Kapatılıyor...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
