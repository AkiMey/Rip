"""
RIPPERINIO hava durumu.

Kullanıcının ana HUD konumu Atakum / Samsun olarak sabitlenebilir.
Atakum için Open-Meteo'nun koordinat tabanlı anlık verisi kullanılır;
başka bir yer açıkça sorulursa wttr.in geri dönüşü korunur.
"""

from __future__ import annotations

import os
import threading
import unicodedata

import requests

from actions.platform_utils import PLATFORM_LABEL

try:
    from app_config import get_app_config_value
except Exception:
    def get_app_config_value(key, default=None):
        return default


_detected_city = ""
_lock = threading.Lock()

REQUEST_TIMEOUT = 12
HEADERS = {"User-Agent": f"RIPPERINIO {PLATFORM_LABEL}"}

# Atakum ilçe merkezi yaklaşık koordinatları.
ATAKUM_LAT = 41.3322
ATAKUM_LON = 36.2704

_WMO_TR = {
    0: "Açık",
    1: "Çoğunlukla açık",
    2: "Parçalı bulutlu",
    3: "Kapalı",
    45: "Sisli",
    48: "Kırağılı sis",
    51: "Hafif çiseleme",
    53: "Çiseleme",
    55: "Yoğun çiseleme",
    56: "Hafif donan çiseleme",
    57: "Yoğun donan çiseleme",
    61: "Hafif yağmur",
    63: "Yağmurlu",
    65: "Kuvvetli yağmur",
    66: "Hafif donan yağmur",
    67: "Kuvvetli donan yağmur",
    71: "Hafif kar",
    73: "Karlı",
    75: "Yoğun kar",
    77: "Kar taneleri",
    80: "Hafif sağanak",
    81: "Sağanak yağmur",
    82: "Kuvvetli sağanak",
    85: "Hafif kar sağanağı",
    86: "Kuvvetli kar sağanağı",
    95: "Gök gürültülü",
    96: "Dolu ihtimalli gök gürültülü",
    99: "Kuvvetli dolulu gök gürültülü",
}

_DESC_TR = {
    "sunny": "Güneşli",
    "clear": "Açık",
    "partly cloudy": "Parçalı bulutlu",
    "cloudy": "Bulutlu",
    "overcast": "Kapalı",
    "mist": "Puslu",
    "fog": "Sisli",
    "patchy rain nearby": "Yakın çevrede yer yer yağmur",
    "patchy light drizzle": "Yer yer hafif çiseleme",
    "light drizzle": "Hafif çiseleme",
    "patchy light rain": "Yer yer hafif yağmur",
    "light rain": "Hafif yağmur",
    "moderate rain": "Yağmurlu",
    "heavy rain": "Kuvvetli yağmur",
    "light rain shower": "Hafif sağanak",
    "moderate or heavy rain shower": "Sağanak yağmur",
    "torrential rain shower": "Çok kuvvetli sağanak",
    "thundery outbreaks possible": "Gök gürültüsü ihtimali",
    "patchy light rain with thunder": "Yer yer gök gürültülü hafif yağmur",
    "moderate or heavy rain with thunder": "Gök gürültülü kuvvetli yağmur",
    "patchy snow nearby": "Yakın çevrede yer yer kar",
    "light snow": "Hafif kar",
    "moderate snow": "Karlı",
    "heavy snow": "Yoğun kar",
    "blizzard": "Tipi",
    "freezing fog": "Donan sis",
}


def _norm(text: str) -> str:
    raw = unicodedata.normalize("NFKD", str(text or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return raw.casefold().strip()


def _is_atakum(location: str) -> bool:
    low = _norm(location)
    return "atakum" in low or ("samsun" in low and "atakum" in _norm(configured_location()))


def _display_location(location: str) -> str:
    low = _norm(location)
    if "atakum" in low:
        return "Atakum / Samsun"
    if not location:
        return ""
    return str(location).strip()


def configured_location() -> str:
    value = str(get_app_config_value("weather_location", "") or "").strip()
    if value:
        return value
    return (os.environ.get("RIPPERINIO_WEATHER_LOCATION") or "").strip()


def detected_city() -> str:
    with _lock:
        return _detected_city


def current_location_label() -> str:
    value = configured_location()
    return _display_location(value) or detected_city() or "Konum aranıyor"


def _extract_city(payload: dict) -> str:
    try:
        area = (payload.get("nearest_area") or [{}])[0]
        name = ((area.get("areaName") or [{}])[0]).get("value", "").strip()
        region = ((area.get("region") or [{}])[0]).get("value", "").strip()
        return region or name
    except Exception:
        return ""


def _translate_desc(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    low = text.casefold()
    if low in _DESC_TR:
        return _DESC_TR[low]
    # Uzun açıklamalarda en özel ifadeyi önce yakala.
    for eng in sorted(_DESC_TR, key=len, reverse=True):
        if eng in low:
            return _DESC_TR[eng]
    return text[:1].upper() + text[1:]


def _atakum_weather_summary() -> str:
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": ATAKUM_LAT,
            "longitude": ATAKUM_LON,
            "current": (
                "temperature_2m,apparent_temperature,"
                "relative_humidity_2m,weather_code"
            ),
            "timezone": "Europe/Istanbul",
        },
        timeout=REQUEST_TIMEOUT,
        headers=HEADERS,
    )
    response.raise_for_status()
    current = response.json().get("current") or {}

    temp = current.get("temperature_2m")
    feels = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")
    code = current.get("weather_code")

    parts = []
    if temp is not None:
        parts.append(f"{round(float(temp))} derece")
    try:
        desc = _WMO_TR.get(int(code), "Hava durumu")
    except Exception:
        desc = "Hava durumu"
    parts.append(desc)
    if feels is not None and temp is not None and round(float(feels)) != round(float(temp)):
        parts.append(f"Hissedilen {round(float(feels))} derece")
    if humidity is not None:
        parts.append(f"Nem %{round(float(humidity))}")

    return "Atakum / Samsun için hava durumu: " + ", ".join(parts) + "."


def get_weather_summary(location: str | None = None) -> str:
    explicit = (location or "").strip()
    target = explicit or configured_location()

    if _is_atakum(target or "Atakum"):
        try:
            return _atakum_weather_summary()
        except Exception:
            # Open-Meteo geçici olarak ulaşılamazsa aşağıdaki wttr yedeğine düş.
            target = "Atakum,Samsun"

    url = f"https://wttr.in/{target}" if target else "https://wttr.in"
    try:
        response = requests.get(
            url,
            params={"format": "j1"},
            timeout=REQUEST_TIMEOUT,
            headers=HEADERS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        known = _display_location(target) or detected_city()
        if known:
            return f"{known} için hava durumu bilgisi şu anda alınamadı."
        return "Hava durumu bilgisi şu anda alınamadı."

    detected = _extract_city(payload)
    if not target and detected:
        with _lock:
            global _detected_city
            _detected_city = detected

    city = _display_location(target) or detected
    current = (payload.get("current_condition") or [{}])[0]
    temp_c = current.get("temp_C")
    feels_like = current.get("FeelsLikeC")
    weather_desc = ((current.get("weatherDesc") or [{}])[0]).get("value", "")
    humidity = current.get("humidity")

    parts = []
    if temp_c:
        parts.append(f"{temp_c} derece")
    if weather_desc:
        parts.append(_translate_desc(weather_desc))
    if feels_like and feels_like != temp_c:
        parts.append(f"Hissedilen {feels_like} derece")
    if humidity:
        parts.append(f"Nem %{humidity}")

    if not parts:
        return "Hava durumu bilgisi şu anda alınamadı."

    label = city or "Bulunduğun konum"
    return f"{label} için hava durumu: " + ", ".join(parts) + "."
