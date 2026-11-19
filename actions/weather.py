"""
Real-time weather, geocoding, timezone & dynamic location management for R.O.S.E.
Uses Open-Meteo (fast, reliable, no API key needed) with wttr.in fallback.
Default location: Lagos, Nigeria (persisted in config/api_keys.json).
"""

from __future__ import annotations

import datetime as dt
import os
import re
import requests

from app_config import load_app_config, save_app_config

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


_WMO_DESCRIPTIONS = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "freezing drizzle",
    57: "heavy freezing drizzle",
    61: "light rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "heavy freezing rain",
    71: "slight snow",
    73: "moderate snow",
    75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


def get_configured_location() -> dict:
    cfg = load_app_config()
    city = str(cfg.get("location") or os.environ.get("JARVIS_WEATHER_LOCATION") or "Lagos").strip() or "Lagos"
    display = str(cfg.get("location_display") or city).strip() or city
    tz = str(cfg.get("location_timezone") or "Africa/Lagos").strip() or "Africa/Lagos"
    try:
        utc_offset = int(cfg.get("location_utc_offset", 3600))
    except Exception:
        utc_offset = 3600
    try:
        lat = float(cfg.get("location_lat")) if cfg.get("location_lat") is not None else None
        lon = float(cfg.get("location_lon")) if cfg.get("location_lon") is not None else None
    except Exception:
        lat, lon = None, None

    return {
        "city": city,
        "display": display,
        "timezone": tz,
        "utc_offset": utc_offset,
        "lat": lat,
        "lon": lon,
    }


def get_location_datetime(loc: dict | None = None) -> dt.datetime:
    info = loc or get_configured_location()
    tz_name = str(info.get("timezone") or "").strip()
    if ZoneInfo and tz_name:
        try:
            return dt.datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    try:
        offset_sec = int(info.get("utc_offset", 3600))
    except Exception:
        offset_sec = 3600
    tz_obj = dt.timezone(dt.timedelta(seconds=offset_sec))
    return dt.datetime.now(dt.timezone.utc).astimezone(tz_obj)


def _format_utc_offset_label(offset_sec: int) -> str:
    sign = "+" if offset_sec >= 0 else "-"
    mins = abs(offset_sec) // 60
    hrs, rem_m = divmod(mins, 60)
    if rem_m:
        return f"UTC{sign}{hrs}:{rem_m:02d}"
    return f"UTC{sign}{hrs}"


_NEIGHBORHOOD_RE = re.compile(
    r"\b(phase|estate|zone|district|area|layout|quarter|extension|sector|plot|street|road)\b",
    re.IGNORECASE,
)


def _open_meteo_geocode(candidate: str) -> dict | None:
    try:
        resp = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": candidate, "count": 1, "language": "en", "format": "json"},
            timeout=7,
            headers={"User-Agent": "ROSE-Windows/2.0"},
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            return None
        r = results[0]
        city = str(r.get("name") or candidate).strip()
        country = str(r.get("country") or "").strip()
        admin1 = str(r.get("admin1") or "").strip()
        parts = [city]
        if admin1 and admin1.lower() != city.lower():
            parts.append(admin1)
        if country and country.lower() != city.lower():
            parts.append(country)
        return {
            "city": city,
            "display": ", ".join(parts),
            "admin1": admin1,
            "country": country,
            "lat": float(r["latitude"]),
            "lon": float(r["longitude"]),
            "timezone": str(r.get("timezone") or "UTC"),
        }
    except Exception:
        return None


def _nominatim_geocode(query: str) -> dict | None:
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "addressdetails": 1, "limit": 1},
            timeout=8,
            headers={"User-Agent": "ROSE-Windows/2.0 (desktop-assistant)"},
        )
        resp.raise_for_status()
        rows = resp.json() or []
        if not rows:
            return None
        top = rows[0]
        addr = top.get("address") or {}
        admin1 = str(addr.get("state") or addr.get("region") or "").strip()
        country = str(addr.get("country") or "").strip()
        city_hint = str(
            addr.get("suburb")
            or addr.get("neighbourhood")
            or addr.get("town")
            or addr.get("city")
            or ""
        ).strip()
        return {
            "city": city_hint or query,
            "admin1": admin1,
            "country": country,
            "lat": float(top["lat"]),
            "lon": float(top["lon"]),
            "display_name": str(top.get("display_name") or ""),
        }
    except Exception:
        return None


def _append_unique_part(parts: list[str], candidate: str) -> None:
    cand = (candidate or "").strip()
    if not cand:
        return
    cand_low = cand.lower()
    for existing in parts:
        ex_low = existing.lower()
        if cand_low == ex_low or cand_low in ex_low:
            return
        if "abuja" in ex_low and "abuja" in cand_low and "territory" in cand_low:
            parts_alias = "FCT"
            if parts_alias.lower() not in [p.lower() for p in parts]:
                parts.append(parts_alias)
            return
    parts.append(cand)


def _geocode_city(query: str) -> dict | None:
    raw = re.sub(r"\s+", " ", (query or "")).strip()
    if not raw:
        return None

    is_compound = ("," in raw) or bool(_NEIGHBORHOOD_RE.search(raw))
    exact_om = _open_meteo_geocode(raw)
    if exact_om and not is_compound:
        return exact_om

    comma_parts = [p.strip() for p in raw.split(",") if p.strip()]
    head = comma_parts[0] if comma_parts else raw
    base_head = _NEIGHBORHOOD_RE.split(head)[0].strip(" ,-")

    nom = _nominatim_geocode(raw)
    if not nom and exact_om and not is_compound:
        return exact_om

    om_hit = exact_om
    if not om_hit:
        candidates = []
        for cand in [head, base_head, *comma_parts[1:]]:
            c = (cand or "").strip()
            if c and c.lower() not in [x.lower() for x in candidates]:
                candidates.append(c)
        for cand in candidates:
            om_hit = _open_meteo_geocode(cand)
            if om_hit:
                break

    if not nom and not om_hit:
        return None

    lat = float(nom["lat"]) if nom else float(om_hit["lat"])
    lon = float(nom["lon"]) if nom else float(om_hit["lon"])

    display_city = head.title() if head.islower() else head
    if not display_city and om_hit:
        display_city = om_hit["city"]

    parts: list[str] = [display_city]
    for extra in comma_parts[1:]:
        _append_unique_part(parts, extra.title() if extra.islower() else extra)

    if nom:
        if nom.get("admin1"):
            _append_unique_part(parts, nom["admin1"])
        if nom.get("country"):
            _append_unique_part(parts, nom["country"])
    if om_hit:
        if om_hit.get("admin1"):
            _append_unique_part(parts, om_hit["admin1"])
        if om_hit.get("country"):
            _append_unique_part(parts, om_hit["country"])

    tz = (om_hit or {}).get("timezone") or "UTC"
    return {
        "city": display_city,
        "display": ", ".join(parts),
        "lat": lat,
        "lon": lon,
        "timezone": tz,
    }


def _fetch_open_meteo_weather(lat: float, lon: float, city_label: str) -> tuple[str | None, dict]:
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code",
                "timezone": "auto",
            },
            timeout=8,
            headers={"User-Agent": "ROSE-Windows/2.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        cur = data.get("current") or {}
        temp = cur.get("temperature_2m")
        feels = cur.get("apparent_temperature")
        humidity = cur.get("relative_humidity_2m")
        wmo = cur.get("weather_code")
        desc = _WMO_DESCRIPTIONS.get(int(wmo) if wmo is not None else -1, "")

        parts = []
        if temp is not None:
            parts.append(f"{round(float(temp))}°C")
        if desc:
            parts.append(desc)
        if feels is not None and round(float(feels)) != round(float(temp or feels)):
            parts.append(f"feels like {round(float(feels))}°C")
        if humidity is not None:
            parts.append(f"humidity {int(round(float(humidity)))}%")

        meta = {
            "timezone": str(data.get("timezone") or ""),
            "utc_offset": int(data.get("utc_offset_seconds", 0) or 0),
        }
        if not parts:
            return None, meta
        return f"Weather in {city_label}: " + ", ".join(parts) + ".", meta
    except Exception:
        return None, {}


def _fetch_wttr_weather(target: str) -> str | None:
    try:
        response = requests.get(
            f"https://wttr.in/{target}",
            params={"format": "j1"},
            timeout=9,
            headers={"User-Agent": "ROSE-Windows/2.0"},
        )
        response.raise_for_status()
        payload = response.json()
        current = (payload.get("current_condition") or [{}])[0]
        temp_c = current.get("temp_C")
        feels_like = current.get("FeelsLikeC")
        weather_desc = ((current.get("weatherDesc") or [{}])[0]).get("value", "")
        humidity = current.get("humidity")

        parts = []
        if temp_c:
            parts.append(f"{temp_c}°C")
        if weather_desc:
            parts.append(weather_desc.lower())
        if feels_like and feels_like != temp_c:
            parts.append(f"feels like {feels_like}°C")
        if humidity:
            parts.append(f"humidity {humidity}%")

        if not parts:
            return None
        return f"Weather in {target}: " + ", ".join(parts) + "."
    except Exception:
        return None


def get_weather_summary(location: str | None = None) -> str:
    loc_cfg = get_configured_location()
    target = (location or "").strip()

    if not target:
        target = loc_cfg["city"]
        lat, lon = loc_cfg.get("lat"), loc_cfg.get("lon")
        if lat is not None and lon is not None:
            summary, meta = _fetch_open_meteo_weather(lat, lon, target)
            if summary:
                # Keep utc_offset fresh (handles DST transitions automatically)
                if meta.get("timezone") and meta.get("utc_offset") != loc_cfg.get("utc_offset"):
                    save_app_config({
                        "location_timezone": meta["timezone"],
                        "location_utc_offset": meta["utc_offset"],
                    })
                return summary

    # Explicitly requested city OR saved location lacks coordinates
    geo = _geocode_city(target)
    if geo:
        summary, _ = _fetch_open_meteo_weather(geo["lat"], geo["lon"], geo["city"])
        if summary:
            return summary

    wttr = _fetch_wttr_weather(target)
    if wttr:
        return wttr

    return f"Weather information for {target} could not be retrieved right now."


def set_location(location: str) -> str:
    """
    Changes R.O.S.E's active location, resolves its exact timezone & coordinates,
    persists it in config/api_keys.json, and returns local time + current weather.
    """
    query = (location or "").strip()
    if not query:
        cur = get_configured_location()
        now_loc = get_location_datetime(cur)
        weather = get_weather_summary(cur["city"])
        return (
            f"Current location is {cur['display']} ({cur['timezone']}, "
            f"{now_loc.strftime('%I:%M %p')}). {weather}"
        )

    geo = _geocode_city(query)
    if geo:
        summary, meta = _fetch_open_meteo_weather(geo["lat"], geo["lon"], geo["city"])
        tz_name = meta.get("timezone") or geo.get("timezone") or "UTC"
        utc_offset = meta.get("utc_offset", 0)
        if not meta and ZoneInfo and tz_name:
            try:
                off = dt.datetime.now(ZoneInfo(tz_name)).utcoffset()
                if off is not None:
                    utc_offset = int(off.total_seconds())
            except Exception:
                pass

        updates = {
            "location": geo["city"],
            "location_display": geo["display"],
            "location_timezone": tz_name,
            "location_utc_offset": utc_offset,
            "location_lat": geo["lat"],
            "location_lon": geo["lon"],
        }
        save_app_config(updates)
        now_loc = get_location_datetime({
            "timezone": tz_name,
            "utc_offset": utc_offset,
        })
        offset_lbl = _format_utc_offset_label(utc_offset)
        weather_line = summary or f"Weather in {geo['city']} could not be retrieved right now."
        return (
            f"Location updated to {geo['display']} ({tz_name}, {offset_lbl}). "
            f"Local time is {now_loc.strftime('%A, %d %B %Y — %I:%M %p')}. "
            f"{weather_line}"
        )

    # Fallback: try wttr.in metadata if Open-Meteo geocoding didn't find it
    wttr = _fetch_wttr_weather(query)
    if wttr:
        save_app_config({
            "location": query.title(),
            "location_display": query.title(),
            "location_lat": None,
            "location_lon": None,
        })
        return f"Location updated to {query.title()}. {wttr}"

    return f"Could not find location '{query}'. Please specify the city name (for example, 'Lagos', 'Abuja', 'London', 'New York', or 'Tokyo')."


def _classify_wmo_icon(wmo: int | None, desc: str = "") -> str:
    if wmo is not None:
        if wmo in (0, 1):
            return "clear"
        if wmo == 2:
            return "partly_cloudy"
        if wmo in (3, 45, 48):
            return "cloudy"
        if wmo in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82):
            return "rain"
        if wmo in (71, 73, 75, 77, 85, 86):
            return "snow"
        if wmo in (95, 96, 99):
            return "storm"
    low = (desc or "").lower()
    if "thunder" in low or "storm" in low:
        return "storm"
    if "rain" in low or "drizzle" in low:
        return "rain"
    if "partly" in low:
        return "partly_cloudy"
    if "cloud" in low or "overcast" in low or "fog" in low:
        return "cloudy"
    return "clear"


def get_weather_details(location: str | None = None) -> dict:
    """Return structured weather info for the HUD card while preserving string summary."""
    loc_cfg = get_configured_location()
    target = (location or "").strip() or loc_cfg["city"]
    lat, lon = loc_cfg.get("lat"), loc_cfg.get("lon")
    if (lat is None or lon is None) or ((location or "").strip() and target.lower() != loc_cfg["city"].lower()):
        geo = _geocode_city(target)
        if geo:
            target = geo["city"]
            lat, lon = geo["lat"], geo["lon"]

    if lat is not None and lon is not None:
        try:
            resp = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code",
                    "timezone": "auto",
                },
                timeout=8,
                headers={"User-Agent": "ROSE-Windows/2.0"},
            )
            resp.raise_for_status()
            data = resp.json()
            cur = data.get("current") or {}
            temp = cur.get("temperature_2m")
            feels = cur.get("apparent_temperature")
            humidity = cur.get("relative_humidity_2m")
            wmo_raw = cur.get("weather_code")
            wmo = int(wmo_raw) if wmo_raw is not None else None
            desc = _WMO_DESCRIPTIONS.get(wmo if wmo is not None else -1, "") or "clear sky"
            return {
                "city": target,
                "temp_c": round(float(temp)) if temp is not None else None,
                "feels_like_c": round(float(feels)) if feels is not None else None,
                "humidity": int(round(float(humidity))) if humidity is not None else None,
                "condition": desc,
                "icon": _classify_wmo_icon(wmo, desc),
                "summary": get_weather_summary(location),
            }
        except Exception:
            pass

    summary = get_weather_summary(location)
    return {
        "city": target,
        "temp_c": None,
        "feels_like_c": None,
        "humidity": None,
        "condition": "",
        "icon": _classify_wmo_icon(None, summary),
        "summary": summary,
    }
