"""Quality-filtered weather summaries from the current receiver snapshot."""

import math
import statistics
import time

from modules.adsb_store import normalize_hex, safe_float


LAYERS = ((0, 10000, "低於 10,000 ft"), (10000, 20000, "10,000–20,000 ft"),
          (20000, 30000, "20,000–30,000 ft"), (30000, 60001, "30,000–60,000 ft"))
# CAA eAIP AD 2.12, AIRAC 05/26 (2026-10-29), true bearings.
# https://ais.caa.gov.tw/eaip/AIRAC%20AIP%20AMDT%2005-26_2026_10_29/
# Tainan lists the civil-use runway only (18L/36R).
WEATHER_AIRPORTS = {
    "RCKU": {"name": "嘉義", "iata": "CYI", "runways": (("18", 179.35), ("36", 359.35))},
    "RCNN": {"name": "臺南", "iata": "TNN", "runways": (("18L", 178.72), ("36R", 358.72))},
    "RCKH": {"name": "高雄", "iata": "KHH", "runways": (("09", 91.90), ("27", 271.93))},
    "RCQC": {"name": "馬公", "iata": "MZG", "runways": (("02", 18.22), ("20", 198.22))},
}


def bounded(value, lower, upper):
    value = safe_float(value)
    return value if value is not None and lower <= value <= upper else None


def weather_layers(data, now=None):
    """One fresh sample per aircraft; layers use pressure altitude, not terrain height."""
    now = time.time() if now is None else now
    timestamp = safe_float((data or {}).get("now"))
    groups = [{"label": label, "winds": [], "temperatures": []} for _, _, label in LAYERS]
    if timestamp is None or not -15 <= now - timestamp <= 120:
        return groups
    used = set()
    for raw in data.get("aircraft", []) or []:
        hex_code = normalize_hex(raw.get("hex"))
        age, pos_age = bounded(raw.get("seen"), 0, 60), bounded(raw.get("seen_pos"), 0, 60)
        altitude = bounded(raw.get("alt_baro"), 0, 60000)
        if (not hex_code or hex_code in used or age is None or pos_age is None or altitude is None
                or bounded(raw.get("lat"), -90, 90) is None
                or bounded(raw.get("lon"), -180, 180) is None
                or raw.get("type") not in {None, "adsb_icao", "adsb_other", "adsr_icao", "adsr_other"}
                or {"lat", "lon"}.intersection(raw.get("mlat") or [])
                or {"lat", "lon"}.intersection(raw.get("tisb") or [])):
            continue
        used.add(hex_code)
        group = next(group for group, (low, high, _) in zip(groups, LAYERS) if low <= altitude < high)
        speed, direction = bounded(raw.get("ws"), 0, 250), bounded(raw.get("wd"), 0, 360)
        if speed is not None and direction is not None:
            group["winds"].append((speed, direction % 360))
        temperature, mach = bounded(raw.get("oat"), -90, 50), bounded(raw.get("mach"), 0.5, 1.5)
        if temperature is not None and mach is not None:
            group["temperatures"].append(temperature)
    return groups


def layer_text(group):
    winds, temperatures = group["winds"], group["temperatures"]
    lines = []
    if len(winds) >= 2:
        east = statistics.mean(speed * math.sin(math.radians(direction)) for speed, direction in winds)
        north = statistics.mean(speed * math.cos(math.radians(direction)) for speed, direction in winds)
        vector_speed = math.hypot(east, north)
        direction = (math.degrees(math.atan2(east, north)) + 360) % 360
        wind = f"{round(direction) % 360:03d}° / {vector_speed:.0f} kt" if vector_speed >= 1 else "合成風低於 1 kt"
        lines.append(f"風場：**{wind}** · {len(winds)} 架")
    else:
        lines.append(f"風場：資料不足（{len(winds)} 架）")
    if len(temperatures) >= 2:
        lines.append(f"氣溫：**{statistics.median(temperatures):.1f} °C** · {len(temperatures)} 架")
        lines.append(f"範圍：{min(temperatures):.1f}～{max(temperatures):.1f} °C")
    else:
        lines.append(f"氣溫：資料不足（{len(temperatures)} 架）")
    return "\n".join(lines)


def metar_is_fresh(data, now=None):
    now = time.time() if now is None else now
    observed = safe_float((data or {}).get("obsTime"))
    return observed is not None and -300 <= now - observed <= 7200


def runway_wind(data, airport="RCKU"):
    """Project METAR true wind onto each runway true bearing, including gusts."""
    speed = bounded(data.get("wspd"), 0, 200)
    direction = bounded(data.get("wdir"), 0, 360)
    if speed is None:
        return "風速資料不足，無法計算。"
    if speed == 0:
        return "靜風 · 各跑道逆風／側風 **0 kt**"
    if direction is None:
        return "風向不定或缺值，無法計算固定方向的風分量。"
    gust = bounded(data.get("wgst"), speed, 200)
    lines = []
    for name, bearing in WEATHER_AIRPORTS[airport]["runways"]:
        angle = math.radians(direction - bearing)
        head, cross = speed * math.cos(angle), abs(speed * math.sin(angle))
        label = "逆風" if head >= 0 else "順風"
        line = f"`{name}` {label} **{abs(head):.0f}**／側風 **{cross:.0f} kt**"
        if gust is not None and gust > speed:
            line += f"（陣風 {abs(gust * math.cos(angle)):.0f}／{abs(gust * math.sin(angle)):.0f}）"
        lines.append(line)
    return "\n".join(lines)
