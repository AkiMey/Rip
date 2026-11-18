"""RIPPERINIO local wake-word engine — V5.

This module detects the user's spoken "Hey" locally from a calibrated
acoustic template. It does NOT use cloud speech-to-text for the wake word.

The microphone stream is shared with Gemini Live; this class never opens a
second microphone device.
"""
from __future__ import annotations

import json
import math
import queue
import struct
import threading
import time
from functools import lru_cache
from pathlib import Path

from app_paths import data_path, resource_path
from typing import Iterable

import numpy as np

SYSTEM_DIR = Path(__file__).resolve().parent
PROFILE_FILE = data_path("wake", "wake_profile.npz")
PROFILE_INFO_FILE = data_path("wake", "wake_profile.json")


def _migrate_legacy_profile() -> None:
    """Move pre-V5 source-tree wake profiles into the writable data directory."""
    legacy_npz = resource_path("data", "wake_profile_v47.npz")
    legacy_json = resource_path("data", "wake_profile_v47.json")
    try:
        if not PROFILE_FILE.exists() and legacy_npz.is_file():
            PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
            PROFILE_FILE.write_bytes(legacy_npz.read_bytes())
        if not PROFILE_INFO_FILE.exists() and legacy_json.is_file():
            PROFILE_INFO_FILE.parent.mkdir(parents=True, exist_ok=True)
            PROFILE_INFO_FILE.write_bytes(legacy_json.read_bytes())
    except Exception:
        pass


_migrate_legacy_profile()

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
WAKE_PHRASE = "Hey"
WAKE_PHRASE_KEY = "hey"
PROFILE_FORMAT_VERSION = 3


def pcm_rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return 0.0
    x = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32)
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x * x) + 1e-9))


def _to_float_pcm(pcm: bytes) -> np.ndarray:
    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    x = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32) / 32768.0
    if x.size:
        x = x - float(np.mean(x))
    return x


def _frame_rms(x: np.ndarray, frame: int = 320, hop: int = 160) -> np.ndarray:
    if x.size < frame:
        if x.size == 0:
            return np.zeros(0, dtype=np.float32)
        return np.array([float(np.sqrt(np.mean(x*x) + 1e-10))], dtype=np.float32)
    count = 1 + (x.size - frame) // hop
    out = np.empty(count, dtype=np.float32)
    for i in range(count):
        f = x[i * hop:i * hop + frame]
        out[i] = float(np.sqrt(np.mean(f * f) + 1e-10))
    return out


def trim_voice(x: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Trim leading/trailing silence while keeping a small context margin."""
    if x.size < int(sample_rate * 0.20):
        return x
    frame = int(sample_rate * 0.020)
    hop = int(sample_rate * 0.010)
    energies = _frame_rms(x, frame, hop)
    if energies.size == 0:
        return x
    noise = float(np.percentile(energies, 25))
    peak = float(np.max(energies))
    # Works with quiet Realtek mics too: relative threshold dominates.
    threshold = max(noise * 2.0, peak * 0.16, 0.0018)
    active = np.flatnonzero(energies >= threshold)
    if active.size == 0:
        return x
    first = max(0, int(active[0]) - 5)
    last = min(len(energies) - 1, int(active[-1]) + 7)
    start = first * hop
    end = min(x.size, last * hop + frame)
    return x[start:end]


@lru_cache(maxsize=8)
def _mel_filterbank(sample_rate: int, nfft: int, n_mels: int = 26) -> np.ndarray:
    def hz_to_mel(hz: float) -> float:
        return 2595.0 * math.log10(1.0 + hz / 700.0)

    def mel_to_hz(mel: float) -> float:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    low_hz, high_hz = 80.0, min(7600.0, sample_rate / 2.0 - 50.0)
    mels = np.linspace(hz_to_mel(low_hz), hz_to_mel(high_hz), n_mels + 2)
    hz = np.array([mel_to_hz(float(m)) for m in mels], dtype=np.float32)
    bins = np.floor((nfft + 1) * hz / sample_rate).astype(int)
    bins = np.clip(bins, 0, nfft // 2)

    fb = np.zeros((n_mels, nfft // 2 + 1), dtype=np.float32)
    for m in range(1, n_mels + 1):
        left, center, right = int(bins[m - 1]), int(bins[m]), int(bins[m + 1])
        center = max(center, left + 1)
        right = max(right, center + 1)
        right = min(right, nfft // 2)
        for k in range(left, min(center, fb.shape[1])):
            fb[m - 1, k] = (k - left) / max(1, center - left)
        for k in range(center, min(right + 1, fb.shape[1])):
            fb[m - 1, k] = (right - k) / max(1, right - center)
    return fb


@lru_cache(maxsize=4)
def _dct_matrix(n_mels: int = 26, n_coeff: int = 13) -> np.ndarray:
    n = np.arange(n_mels, dtype=np.float32)
    k = np.arange(n_coeff, dtype=np.float32)[:, None]
    mat = np.cos(np.pi / n_mels * (n + 0.5) * k)
    mat[0] *= 1.0 / math.sqrt(2.0)
    mat *= math.sqrt(2.0 / n_mels)
    return mat.astype(np.float32)


def extract_features(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """MFCC + delta features for speaker-specific short wake matching."""
    x = trim_voice(_to_float_pcm(pcm), sample_rate)
    if x.size < int(sample_rate * 0.16):
        raise ValueError("Ses çok kısa; 'Hey' kelimesini net ve normal tonda söyle.")
    if x.size > int(sample_rate * 1.8):
        x = x[: int(sample_rate * 1.8)]

    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak < 0.0015:
        raise ValueError("Ses seviyesi çok düşük; mikrofona biraz daha yakın söyle.")
    # Per-utterance amplitude normalisation removes mic volume dependence.
    x = (x / max(peak, 1e-6) * 0.92).astype(np.float32)
    x = np.concatenate(([x[0]], x[1:] - 0.97 * x[:-1])).astype(np.float32)

    frame_len = int(sample_rate * 0.025)
    hop = int(sample_rate * 0.010)
    nfft = 512
    if x.size < frame_len:
        x = np.pad(x, (0, frame_len - x.size))
    count = 1 + int(math.ceil(max(0, x.size - frame_len) / hop))
    total = (count - 1) * hop + frame_len
    if x.size < total:
        x = np.pad(x, (0, total - x.size))

    idx = np.arange(frame_len)[None, :] + hop * np.arange(count)[:, None]
    frames = x[idx] * np.hamming(frame_len).astype(np.float32)[None, :]
    spec = np.fft.rfft(frames, n=nfft, axis=1)
    power = (np.abs(spec) ** 2 / nfft).astype(np.float32)
    mel = np.maximum(power @ _mel_filterbank(sample_rate, nfft).T, 1e-10)
    log_mel = np.log(mel)
    mfcc = log_mel @ _dct_matrix().T
    # Cepstral mean/variance normalisation reduces room/mic coloration.
    mean = np.mean(mfcc, axis=0, keepdims=True)
    std = np.std(mfcc, axis=0, keepdims=True) + 1e-5
    mfcc = (mfcc - mean) / std
    delta = np.gradient(mfcc, axis=0)
    feat = np.concatenate([mfcc, delta], axis=1).astype(np.float32)
    norms = np.linalg.norm(feat, axis=1, keepdims=True) + 1e-6
    feat /= norms
    return feat


def dtw_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Band-limited cosine DTW. Lower is more similar."""
    if a.ndim != 2 or b.ndim != 2 or len(a) == 0 or len(b) == 0:
        return 9.0
    n, m = len(a), len(b)
    band = max(abs(n - m) + 3, int(max(n, m) * 0.34))
    inf = 1e9
    prev = np.full(m + 1, inf, dtype=np.float32)
    cur = np.full(m + 1, inf, dtype=np.float32)
    prev[0] = 0.0
    for i in range(1, n + 1):
        cur.fill(inf)
        j0 = max(1, i - band)
        j1 = min(m, i + band)
        ai = a[i - 1]
        for j in range(j0, j1 + 1):
            # Inputs are row-normalised; cosine cost is stable under gain changes.
            cost = 1.0 - float(np.clip(np.dot(ai, b[j - 1]), -1.0, 1.0))
            cur[j] = cost + min(float(prev[j]), float(cur[j - 1]), float(prev[j - 1]))
        prev, cur = cur, prev
    return float(prev[m] / max(n, m))


def derive_threshold(templates: list[np.ndarray]) -> tuple[float, list[float]]:
    if len(templates) < 3:
        return 0.48, []
    matrix = []
    nearest = []
    for i, a in enumerate(templates):
        row = []
        for j, b in enumerate(templates):
            if i == j:
                continue
            row.append(dtw_distance(a, b))
        matrix.extend(row)
        nearest.append(min(row))
    med = float(np.median(nearest))
    worst_near = float(max(nearest))
    threshold = max(0.26, med * 1.95 + 0.035, worst_near * 1.65 + 0.035)
    threshold = min(0.92, threshold)
    return float(threshold), [float(x) for x in matrix]


def save_profile(templates: list[np.ndarray], threshold: float, metadata: dict | None = None) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    arrays = {f"template_{i}": np.asarray(t, dtype=np.float32) for i, t in enumerate(templates)}
    arrays["threshold"] = np.array([float(threshold)], dtype=np.float32)
    arrays["sample_rate"] = np.array([SAMPLE_RATE], dtype=np.int32)
    np.savez_compressed(PROFILE_FILE, **arrays)
    info = dict(metadata or {})
    info.update({
        "version": PROFILE_FORMAT_VERSION,
        "engine": "local-mfcc-dtw",
        "wake_phrase": WAKE_PHRASE,
        "templates": len(templates),
        "threshold": float(threshold),
        "stores_raw_audio": False,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    PROFILE_INFO_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def _profile_phrase_is_current() -> bool:
    """Reject old HEY RIP templates after switching the wake phrase to HEY.

    Acoustic templates are phrase-specific. Reusing a two-word HEY RIP profile
    for the one-word HEY trigger makes detection look broken even though the
    microphone loop is healthy, so stale profiles deliberately request a one-
    time recalibration instead of silently loading incompatible data.
    """
    try:
        info = json.loads(PROFILE_INFO_FILE.read_text(encoding="utf-8-sig"))
        phrase = str(info.get("wake_phrase") or "").strip().casefold()
        return phrase == WAKE_PHRASE_KEY
    except Exception:
        return False


def load_profile() -> tuple[list[np.ndarray], float]:
    if not PROFILE_FILE.is_file() or not _profile_phrase_is_current():
        return [], 0.0
    try:
        with np.load(PROFILE_FILE, allow_pickle=False) as z:
            names = sorted(
                (name for name in z.files if name.startswith("template_")),
                key=lambda s: int(s.rsplit("_", 1)[1]),
            )
            templates = [np.asarray(z[name], dtype=np.float32) for name in names]
            threshold = float(np.asarray(z["threshold"]).reshape(-1)[0])
        return templates, threshold
    except Exception:
        return [], 0.0


def score_features(features: np.ndarray, templates: list[np.ndarray], threshold: float) -> tuple[bool, float, float, list[float]]:
    if not templates:
        return False, 9.0, 9.0, []
    distances = sorted(float(dtw_distance(features, t)) for t in templates)
    best = distances[0]
    top_count = min(3, len(distances))
    top_mean = float(sum(distances[:top_count]) / top_count)
    # Require agreement from several templates, not just one lucky match.
    accepted = best <= threshold * 1.05 and top_mean <= threshold * 1.28
    return accepted, best, top_mean, distances


class WakeWordStream:
    """Streaming local wake detector fed from the main microphone loop."""

    def __init__(self, on_wake, sample_rate: int = SAMPLE_RATE, sample_width: int = SAMPLE_WIDTH, on_status=None):
        self.on_wake = on_wake
        self.on_status = on_status
        self.sample_rate = int(sample_rate)
        self.sample_width = int(sample_width)
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=96)
        self._running = False
        self._enabled = True
        self._thread: threading.Thread | None = None
        self._templates: list[np.ndarray] = []
        self._threshold = 0.0
        self._profile_mtime = 0.0
        self._last_status = ""
        self._last_wake = 0.0

    def _status(self, code: str, detail: str = "") -> None:
        key = f"{code}:{detail}"
        if key == self._last_status and code not in {"candidate"}:
            return
        self._last_status = key
        if callable(self.on_status):
            try:
                self.on_status(str(code), str(detail or ""))
            except Exception:
                pass

    def _reload_profile(self, force: bool = False) -> bool:
        try:
            mtime = PROFILE_FILE.stat().st_mtime if PROFILE_FILE.exists() else 0.0
        except Exception:
            mtime = 0.0
        if force or mtime != self._profile_mtime:
            self._profile_mtime = mtime
            self._templates, self._threshold = load_profile()
        return bool(self._templates and self._threshold > 0)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._reload_profile(force=True)
        self._thread = threading.Thread(target=self._loop, name="rip-local-wake", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self.clear()

    def clear(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def feed(self, pcm: bytes) -> None:
        if not self._running or not self._enabled or not pcm:
            return
        try:
            self._queue.put_nowait(bytes(pcm))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(bytes(pcm))
            except queue.Full:
                pass

    def _check_utterance(self, pcm: bytes) -> bool:
        try:
            features = extract_features(pcm, self.sample_rate)
            accepted, best, top_mean, _ = score_features(features, self._templates, self._threshold)
            self._status(
                "candidate",
                f"skor={best:.3f} ort={top_mean:.3f} esik={self._threshold:.3f}",
            )
            if accepted and time.monotonic() - self._last_wake > 1.2:
                self._last_wake = time.monotonic()
                self._status("wake", f"yerel skor {best:.3f}")
                try:
                    self.on_wake("")
                except Exception:
                    pass
                return True
        except Exception as exc:
            self._status("error", str(exc))
        return False

    def _loop(self) -> None:
        if self._reload_profile(force=True):
            self._status("ready", f"yerel profil · {len(self._templates)} ornek")
        else:
            self._status("needs_calibration", "HEY profili icin RIP_SES_KALIBRE.bat calistir")

        pre_roll: list[bytes] = []
        speech_chunks: list[bytes] = []
        active = False
        above_count = 0
        below_count = 0
        noise_floor = 35.0
        max_chunks = max(10, int(self.sample_rate / 1024 * 1.8))
        min_chunks = max(3, int(self.sample_rate / 1024 * 0.18))

        while self._running:
            if not self._enabled:
                time.sleep(0.06)
                continue
            if not self._reload_profile():
                self._status("needs_calibration", "HEY profili icin RIP_SES_KALIBRE.bat calistir")
                time.sleep(0.25)
                continue
            try:
                chunk = self._queue.get(timeout=0.35)
            except queue.Empty:
                continue

            level = pcm_rms(chunk)
            if not active:
                # Update floor only while likely not speaking.
                if level < max(110.0, noise_floor * 1.8):
                    noise_floor = 0.985 * noise_floor + 0.015 * max(4.0, level)
                pre_roll.append(chunk)
                if len(pre_roll) > 3:
                    pre_roll.pop(0)
                start_threshold = max(45.0, noise_floor * 1.65)
                if level >= start_threshold:
                    above_count += 1
                else:
                    above_count = 0
                if above_count >= 1:
                    active = True
                    speech_chunks = list(pre_roll)
                    below_count = 0
                continue

            speech_chunks.append(chunk)
            end_threshold = max(35.0, noise_floor * 1.25)
            if level <= end_threshold:
                below_count += 1
            else:
                below_count = 0

            enough = len(speech_chunks) >= min_chunks
            finished = enough and below_count >= 3
            too_long = len(speech_chunks) >= max_chunks
            if not finished and not too_long:
                continue

            pcm = b"".join(speech_chunks)
            active = False
            above_count = 0
            below_count = 0
            speech_chunks = []
            pre_roll = []

            if too_long:
                # Long sentences/background speech are not wake phrases.
                self._status("long_reject", "uzun konusma wake olarak yok sayildi")
                continue
            self._check_utterance(pcm)
