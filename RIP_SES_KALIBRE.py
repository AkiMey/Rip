from __future__ import annotations

import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SYSTEM = ROOT / "sistem"
sys.path.insert(0, str(SYSTEM))

RATE = 16000
CHUNK = 1024
RECORD_SECONDS = 1.6
EXAMPLES = 5

try:
    import pyaudio
except Exception as exc:
    print("[HATA] PyAudio yuklenemedi:", exc)
    raise SystemExit(10)

try:
    from wake_word import (
        derive_threshold,
        dtw_distance,
        extract_features,
        pcm_rms,
        save_profile,
        PROFILE_FILE,
    )
except Exception as exc:
    print("[HATA] V5 yerel wake motoru yuklenemedi:", exc)
    print("Once BASLAT.bat ile V5 ortam kurulumunu tamamla.")
    raise SystemExit(11)


def _virtual(name: str) -> bool:
    n = str(name or "").casefold()
    return any(x in n for x in (
        "stereo mix", "what u hear", "cable output", "vb-audio",
        "virtual audio", "voicemeeter output", "sound mapper",
    ))


def microphone_candidates(pa: pyaudio.PyAudio):
    default_idx = None
    try:
        default_idx = int(pa.get_default_input_device_info().get("index", -1))
    except Exception:
        pass

    rows = []
    for i in range(pa.get_device_count()):
        try:
            info = pa.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0) or 0) <= 0:
                continue
            name = str(info.get("name") or f"Mic {i}")
            rows.append((i, name, _virtual(name)))
        except Exception:
            pass

    rows.sort(key=lambda r: (r[0] != default_idx, r[2], r[0]))
    return rows


def open_mic(pa):
    errors = []
    for idx, name, _ in microphone_candidates(pa):
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=RATE,
                input=True,
                input_device_index=idx,
                frames_per_buffer=CHUNK,
            )
            return stream, idx, name
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("Mikrofon acilamadi: " + " | ".join(errors[:5]))


def record_once(pa, number: int) -> tuple[bytes, str]:
    print()
    print(f"[{number}/{EXAMPLES}] Hazirlan...")
    time.sleep(0.7)
    try:
        import winsound
        winsound.Beep(880, 90)
    except Exception:
        pass

    stream, _, name = open_mic(pa)
    chunks = []
    try:
        print("SIMDI sadece:  HEY")
        for _ in range(int(RATE / CHUNK * RECORD_SECONDS)):
            chunks.append(stream.read(CHUNK, exception_on_overflow=False))
    finally:
        try:
            stream.stop_stream()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    return b"".join(chunks), name


def main() -> int:
    print("=" * 64)
    print(" RIP V6 - YEREL HEY SES KALIBRASYONU")
    print("=" * 64)
    print()
    print("Google STT wake icin kullanilmaz.")
    print("5 adet yerel ses kalibi olusturulacak; ham ses kaydi saklanmaz.")
    print("Profil:", PROFILE_FILE)
    print()
    input("RIP tamamen kapaliysa Enter'a bas...")

    pa = pyaudio.PyAudio()
    templates = []
    mic_name = ""

    try:
        attempt = 1
        while len(templates) < EXAMPLES and attempt <= 10:
            pcm, mic_name = record_once(pa, len(templates) + 1)
            level = pcm_rms(pcm)
            try:
                feat = extract_features(pcm, RATE)
            except Exception as exc:
                print(f"[TEKRAR] Ornek kullanilamadi: {exc} (RMS {level:.0f})")
                attempt += 1
                continue

            duration = len(pcm) / 2 / RATE
            print(
                f"[OK] Ornek alindi · RMS {level:.0f} · "
                f"pencere {duration:.1f} sn · ozellik {len(feat)} kare"
            )
            templates.append(feat)
            attempt += 1
    finally:
        pa.terminate()

    if len(templates) < 4:
        print("[HATA] Yeterli temiz ornek alinamadi.")
        return 2

    threshold, _pairs = derive_threshold(templates)
    nearest = []
    for i, a in enumerate(templates):
        ds = [dtw_distance(a, b) for j, b in enumerate(templates) if i != j]
        nearest.append(min(ds))

    quality = "iyi"
    if max(nearest) > 0.72:
        quality = "daginik"
    elif max(nearest) > 0.52:
        quality = "orta"

    save_profile(
        templates,
        threshold,
        metadata={
            "microphone": mic_name,
            "quality": quality,
            "nearest_template_scores": [round(float(x), 4) for x in nearest],
            "profile_version": "6.2.1",
        },
    )

    print()
    print("=" * 64)
    print(" [OK] HEY PROFILI HAZIR")
    print("=" * 64)
    print("Mikrofon :", mic_name)
    print("Kalite   :", quality)
    print("Esik     :", f"{threshold:.3f}")
    print("Ornekler :", ", ".join(f"{x:.3f}" for x in nearest))
    print()
    print("Simdi BASLAT.bat ile RIP'i ac.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
