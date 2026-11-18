from __future__ import annotations

import asyncio
import os
from typing import Any


def _status_name(value: Any) -> str:
    name = getattr(value, "name", None)
    if name:
        return str(name).upper()

    text = str(value or "").strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.upper() or "UNKNOWN"


def _seconds(value: Any) -> float:
    """WinRT timedelta / numeric değerini güvenli saniyeye çevirir."""
    if value is None:
        return 0.0
    try:
        total = getattr(value, "total_seconds", None)
        if callable(total):
            return max(0.0, float(total()))
    except Exception:
        pass
    try:
        return max(0.0, float(value))
    except Exception:
        return 0.0


def _friendly_source(source: str) -> str:
    raw = str(source or "").strip()
    low = raw.lower()

    mapping = (
        ("spotify", "SPOTIFY"),
        ("youtube", "YOUTUBE"),
        ("opera", "OPERA GX"),
        ("chrome", "CHROME"),
        ("msedge", "EDGE"),
        ("firefox", "FIREFOX"),
        ("vlc", "VLC"),
        ("music", "MUSIC"),
    )
    for needle, label in mapping:
        if needle in low:
            return label

    if not raw:
        return "SYSTEM MEDIA"

    clean = raw.replace("!", " / ").replace("_", " ")
    return clean[-34:].upper()


async def _thumbnail_bytes(reference: Any) -> bytes:
    if reference is None:
        return b""

    stream = None
    input_stream = None
    reader = None

    try:
        from winrt.windows.storage.streams import DataReader

        stream = await reference.open_read_async()
        size = min(int(getattr(stream, "size", 0) or 0), 8 * 1024 * 1024)
        if size <= 0:
            return b""

        input_stream = stream.get_input_stream_at(0)
        reader = DataReader(input_stream)

        loaded = int(await reader.load_async(size))
        if loaded <= 0:
            return b""

        buffer = reader.read_buffer(loaded)

        try:
            return bytes(memoryview(buffer))
        except Exception:
            try:
                return bytes(buffer)
            except Exception:
                return b""
    except Exception:
        return b""
    finally:
        for obj in (reader, input_stream, stream):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass


class NowPlayingReader:
    """
    Live Windows Global System Media Transport Controls reader.

    V1.1 difference:
    - The manager is cached on one persistent asyncio loop.
    - Every poll asks the current session for FRESH properties.
    - If Windows exposes more than one media session, a PLAYING session wins.
    """

    def __init__(self) -> None:
        self._manager = None

    async def _ensure_manager(self):
        if self._manager is not None:
            return self._manager

        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        )

        self._manager = await MediaManager.request_async()
        return self._manager

    @staticmethod
    def _playback_status(session: Any) -> str:
        try:
            info = session.get_playback_info()
            return _status_name(getattr(info, "playback_status", None))
        except Exception:
            return "UNKNOWN"

    @classmethod
    def _choose_session(cls, manager: Any):
        """
        Prefer the Windows current session, but if it is stale/paused while another
        session is actively playing, use the playing one.
        """
        try:
            current = manager.get_current_session()
        except Exception:
            current = None

        sessions = []
        try:
            sessions = list(manager.get_sessions())
        except Exception:
            sessions = []

        if current is not None:
            current_status = cls._playback_status(current)
            if "PLAYING" in current_status:
                return current

        for session in sessions:
            if "PLAYING" in cls._playback_status(session):
                return session

        if current is not None:
            return current

        return sessions[0] if sessions else None

    async def read_async(self) -> dict[str, Any]:
        if os.name != "nt":
            return {
                "available": False,
                "active": False,
                "title": "",
                "artist": "",
                "album": "",
                "source": "WINDOWS ONLY",
                "status": "OFFLINE",
                "thumbnail": b"",
                "error": "Windows dışında medya oturumu okunmuyor.",
            }

        try:
            manager = await self._ensure_manager()
            session = self._choose_session(manager)
        except Exception as exc:
            # Manager may become invalid after Explorer/audio-session changes.
            # Drop it so the next poll requests a fresh one.
            self._manager = None
            return {
                "available": False,
                "active": False,
                "title": "",
                "artist": "",
                "album": "",
                "source": "MEDIA BACKEND OFFLINE",
                "status": "OFFLINE",
                "thumbnail": b"",
                "error": str(exc),
            }

        if session is None:
            return {
                "available": True,
                "active": False,
                "title": "",
                "artist": "",
                "album": "",
                "source": "SYSTEM MEDIA",
                "status": "IDLE",
                "thumbnail": b"",
                "error": "",
            }

        try:
            # IMPORTANT: fetch this every poll; do not cache media properties.
            props = await session.try_get_media_properties_async()
        except Exception as exc:
            props = None
            prop_error = str(exc)
        else:
            prop_error = ""

        try:
            playback = session.get_playback_info()
        except Exception:
            playback = None

        title = str(getattr(props, "title", "") or "").strip()
        artist = str(getattr(props, "artist", "") or "").strip()
        album = str(getattr(props, "album_title", "") or "").strip()

        try:
            source_raw = str(session.source_app_user_model_id or "")
        except Exception:
            source_raw = ""

        thumbnail = b""
        try:
            thumbnail = await _thumbnail_bytes(getattr(props, "thumbnail", None))
        except Exception:
            thumbnail = b""

        position_seconds = 0.0
        duration_seconds = 0.0
        try:
            timeline = session.get_timeline_properties()
            start = _seconds(getattr(timeline, "start_time", None))
            end = _seconds(getattr(timeline, "end_time", None))
            position = _seconds(getattr(timeline, "position", None))
            # Windows timeline position normalde mutlak oturum konumudur.
            position_seconds = max(0.0, position - start if position >= start else position)
            duration_seconds = max(0.0, end - start)
        except Exception:
            pass

        return {
            "available": True,
            "active": True,
            "title": title or "Bilinmeyen medya",
            "artist": artist,
            "album": album,
            "source": _friendly_source(source_raw),
            "status": _status_name(getattr(playback, "playback_status", None)),
            "thumbnail": thumbnail,
            "position_seconds": position_seconds,
            "duration_seconds": duration_seconds,
            "error": prop_error,
        }

    # Legacy/single-shot API kept for compatibility with tests or other callers.
    def read(self) -> dict[str, Any]:
        try:
            return asyncio.run(self.read_async())
        except Exception as exc:
            self._manager = None
            return {
                "available": False,
                "active": False,
                "title": "",
                "artist": "",
                "album": "",
                "source": "MEDIA BACKEND OFFLINE",
                "status": "OFFLINE",
                "thumbnail": b"",
                "error": str(exc),
            }
