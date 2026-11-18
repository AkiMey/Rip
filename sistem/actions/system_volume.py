from __future__ import annotations

import os
from typing import Any


class SystemVolume:
    """Windows master volume using the real Core Audio endpoint via Pycaw."""

    @staticmethod
    def _endpoint():
        if os.name != "nt":
            raise OSError("Windows ses kontrolü yalnızca Windows'ta kullanılabilir.")

        from pycaw.pycaw import AudioUtilities

        device = AudioUtilities.GetSpeakers()
        endpoint = device.EndpointVolume
        if endpoint is None:
            raise RuntimeError("Varsayılan Windows ses çıkışı bulunamadı.")
        return endpoint

    def get_state(self) -> dict[str, Any]:
        try:
            endpoint = self._endpoint()
            scalar = float(endpoint.GetMasterVolumeLevelScalar())
            volume = max(0, min(100, int(round(scalar * 100.0))))
            return {
                "available": True,
                "volume": volume,
                "muted": bool(endpoint.GetMute()),
                "error": "",
            }
        except Exception as exc:
            return {
                "available": False,
                "volume": 0,
                "muted": False,
                "error": str(exc),
            }

    def set_percent(self, value: int | float) -> tuple[bool, str]:
        target = max(0, min(100, int(round(float(value)))))
        try:
            endpoint = self._endpoint()
            endpoint.SetMasterVolumeLevelScalar(target / 100.0, None)
            if target > 0:
                endpoint.SetMute(0, None)

            actual = max(
                0,
                min(
                    100,
                    int(round(float(endpoint.GetMasterVolumeLevelScalar()) * 100.0)),
                ),
            )
            return True, f"Ses seviyesi yüzde {actual}."
        except Exception as exc:
            return False, f"Ses seviyesi değiştirilemedi: {exc}"

    def change_percent(self, delta: int | float) -> tuple[bool, str]:
        state = self.get_state()
        if not state["available"]:
            return False, f"Windows ses seviyesi okunamadı: {state['error']}"

        current = int(state["volume"])
        return self.set_percent(current + int(round(float(delta))))

    def set_muted(self, muted: bool) -> tuple[bool, str]:
        try:
            endpoint = self._endpoint()
            endpoint.SetMute(1 if muted else 0, None)
            return (
                True,
                "Ses sessize alındı." if muted else "Ses yeniden açıldı.",
            )
        except Exception as exc:
            return False, f"Sessiz modu değiştirilemedi: {exc}"

    def toggle_mute(self) -> tuple[bool, str]:
        state = self.get_state()
        if not state["available"]:
            return False, f"Windows sessiz durumu okunamadı: {state['error']}"
        return self.set_muted(not bool(state["muted"]))


_VOLUME = SystemVolume()


def control_volume(action: str = "get", value: int | float | None = None) -> str:
    """
    Gemini/UI ortak ses kontrolü.

    action:
      get/status
      set
      change
      up / down
      mute / unmute / toggle_mute
    """
    key = str(action or "get").strip().lower().replace("-", "_")

    if key in {"get", "status", "query"}:
        state = _VOLUME.get_state()
        if not state["available"]:
            return f"Windows ses seviyesi okunamadı: {state['error']}"
        suffix = " (sessiz)" if state["muted"] else ""
        return f"Ses seviyesi yüzde {state['volume']}{suffix}."

    if key in {"set", "set_percent", "percent"}:
        if value is None:
            return "Ayarlanacak ses yüzdesi belirtilmedi."
        return _VOLUME.set_percent(value)[1]

    if key in {"change", "adjust"}:
        if value is None:
            return "Ses değişim miktarı belirtilmedi."
        return _VOLUME.change_percent(value)[1]

    if key in {"up", "increase", "artir", "arttır"}:
        amount = 10 if value is None else abs(float(value))
        return _VOLUME.change_percent(amount)[1]

    if key in {"down", "decrease", "kis", "kıs"}:
        amount = 10 if value is None else abs(float(value))
        return _VOLUME.change_percent(-amount)[1]

    if key in {"mute", "mute_on"}:
        return _VOLUME.set_muted(True)[1]

    if key in {"unmute", "mute_off"}:
        return _VOLUME.set_muted(False)[1]

    if key in {"toggle_mute", "toggle"}:
        return _VOLUME.toggle_mute()[1]

    return f"Bilinmeyen ses kontrolü: {action}"
