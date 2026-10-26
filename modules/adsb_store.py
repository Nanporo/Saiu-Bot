"""ADS-B normalization, classification, airport lookup, and short-term storage."""

from __future__ import annotations

import asyncio
import csv
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Mapping


DEFAULT_DB_PATH = Path("data/adsb_history.db")
HISTORY_SECONDS = 24 * 60 * 60
RETENTION_SECONDS = 48 * 60 * 60
ENCOUNTER_GAP_SECONDS = 15 * 60

EMERGENCY_SQUAWKS = {"7500", "7600", "7700"}

# Conservative local aliases. Unknown operators fall back to readsb's ownOp.
OPERATOR_ALIASES = {
    "CAL": "中華航空", "CCA": "中國國航", "CES": "中國東航", "CHH": "海南航空",
    "CPA": "國泰航空", "CRK": "香港航空", "EVA": "長榮航空", "HDA": "華信航空",
    "JAL": "日本航空", "JNA": "日本航空", "KAL": "大韓航空", "MDA": "華信航空",
    "SJX": "星宇航空", "ANA": "全日空", "APJ": "樂桃航空", "JJP": "濟州航空",
    "TTW": "德威航空", "UIA": "立榮航空", "VNL": "台灣虎航",
    "FDX": "FedEx", "UPS": "UPS", "CLX": "盧森堡貨運", "GTI": "Atlas Air",
    "KAL": "大韓航空", "CKS": "Kalitta Air", "PAC": "Polar Air Cargo",
    "VJT": "VistaJet", "TAG": "TAG Aviation",
}

CARGO_PREFIXES = {
    "ABW", "AHK", "BOX", "CALC", "CKS", "CLX", "FDX", "GTI",
    "ICV", "KALC", "KYE", "MNB", "NCA", "PAC", "QAC", "SRR", "TAY",
    "TPA", "UPS",
}
CARGO_OPERATOR_WORDS = ("cargo", "freight", "fedex", "parcel", "貨運", "貨機")
BUSINESS_TYPE_PREFIXES = (
    "BE", "C25", "C50", "C51", "C52", "C55", "C56", "C65", "C68",
    "CL", "E35", "E45", "F2TH", "F900", "FA", "GLEX", "GLF", "H25",
    "LJ", "PC24",
)

# Broad ICAO allocation blocks sufficient for country-level display/counting.
# More specific blocks precede larger regional blocks where they overlap.
ICAO_COUNTRY_RANGES = (
    (0x899000, 0x8993FF, "TW", "台灣"),
    (0x789000, 0x789FFF, "HK", "香港"),
    (0x780000, 0x7BFFFF, "CN", "中國"),
    (0x840000, 0x87FFFF, "JP", "日本"),
    (0x718000, 0x71BFFF, "KR", "韓國"),
    (0x720000, 0x727FFF, "KP", "朝鮮"),
    (0x750000, 0x757FFF, "MY", "馬來西亞"),
    (0x758000, 0x75FFFF, "PH", "菲律賓"),
    (0x768000, 0x76FFFF, "SG", "新加坡"),
    (0x760000, 0x767FFF, "PK", "巴基斯坦"),
    (0x770000, 0x777FFF, "LK", "斯里蘭卡"),
    (0x880000, 0x887FFF, "TH", "泰國"),
    (0x888000, 0x88FFFF, "VN", "越南"),
    (0x8A0000, 0x8A7FFF, "ID", "印尼"),
    (0x800000, 0x83FFFF, "IN", "印度"),
    (0x7C0000, 0x7FFFFF, "AU", "澳洲"),
    (0xC80000, 0xC87FFF, "NZ", "紐西蘭"),
    (0xA00000, 0xAFFFFF, "US", "美國"),
    (0xC00000, 0xC3FFFF, "CA", "加拿大"),
    (0x400000, 0x43FFFF, "GB", "英國"),
    (0x3C0000, 0x3FFFFF, "DE", "德國"),
    (0x380000, 0x3BFFFF, "FR", "法國"),
    (0x300000, 0x33FFFF, "IT", "義大利"),
    (0x340000, 0x37FFFF, "ES", "西班牙"),
    (0x440000, 0x447FFF, "AT", "奧地利"),
    (0x448000, 0x44FFFF, "BE", "比利時"),
    (0x4B0000, 0x4B7FFF, "CH", "瑞士"),
    (0x450000, 0x457FFF, "BG", "保加利亞"),
    (0x458000, 0x45FFFF, "DK", "丹麥"),
    (0x460000, 0x467FFF, "FI", "芬蘭"),
    (0x468000, 0x46FFFF, "GR", "希臘"),
    (0x470000, 0x477FFF, "HU", "匈牙利"),
    (0x478000, 0x47FFFF, "NO", "挪威"),
    (0x480000, 0x487FFF, "NL", "荷蘭"),
    (0x488000, 0x48FFFF, "PL", "波蘭"),
    (0x490000, 0x497FFF, "PT", "葡萄牙"),
    (0x498000, 0x49FFFF, "CZ", "捷克"),
    (0x4A0000, 0x4A7FFF, "RO", "羅馬尼亞"),
    (0x4A8000, 0x4AFFFF, "SE", "瑞典"),
    (0x4B8000, 0x4BFFFF, "TR", "土耳其"),
    (0x4CA000, 0x4CAFFF, "IE", "愛爾蘭"),
    (0x4D0000, 0x4D07FF, "LU", "盧森堡"),
    (0x4D2000, 0x4D27FF, "MT", "馬爾他"),
    (0x738000, 0x73FFFF, "IL", "以色列"),
    (0x896000, 0x896FFF, "AE", "阿聯酋"),
    (0x100000, 0x1FFFFF, "RU", "俄羅斯"),
    (0x500000, 0x507FFF, "UA", "烏克蘭"),
    (0xE00000, 0xE3FFFF, "AR", "阿根廷"),
    (0xE40000, 0xE7FFFF, "BR", "巴西"),
)


def safe_float(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def normalize_hex(value) -> str:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"[0-9a-f]{6}", text) else ""


def normalize_text(value) -> str:
    return str(value or "").strip().upper()


def country_for_hex(hex_code: str) -> tuple[str, str]:
    normalized = normalize_hex(hex_code)
    if not normalized:
        return "", ""
    value = int(normalized, 16)
    for start, end, code, name in ICAO_COUNTRY_RANGES:
        if start <= value <= end:
            return code, name
    return "", ""


def operator_alias(flight: str, own_operator: str = "") -> str:
    prefix = re.match(r"[A-Z]+", normalize_text(flight))
    if prefix:
        letters = prefix.group(0)
        for length in (4, 3, 2):
            alias = OPERATOR_ALIASES.get(letters[:length])
            if alias:
                return alias
    return str(own_operator or "").strip()


def classify_aircraft(raw: Mapping) -> dict:
    flight = normalize_text(raw.get("flight"))
    operator = str(raw.get("ownOp") or raw.get("operator") or "").strip()
    type_code = normalize_text(raw.get("t") or raw.get("type_code"))
    type_desc = str(raw.get("desc") or raw.get("type_desc") or "").strip()
    category = normalize_text(raw.get("category"))
    db_flags = int(safe_float(raw.get("dbFlags") or raw.get("db_flags"), 0) or 0)
    squawk = normalize_text(raw.get("squawk"))
    emergency = normalize_text(raw.get("emergency"))

    cargo = False
    prefix = re.match(r"[A-Z]+", flight)
    if prefix:
        letters = prefix.group(0)
        cargo = any(letters.startswith(item) for item in CARGO_PREFIXES)
    operator_lower = operator.lower()
    cargo = cargo or any(word in operator_lower for word in CARGO_OPERATOR_WORDS)

    labels = []
    if db_flags & 1:
        labels.append("軍機")
    elif db_flags & 2:
        labels.append("特殊關注")
    if squawk in EMERGENCY_SQUAWKS or emergency not in {"", "NONE", "NOEMERGENCY"}:
        labels.append("緊急狀態")
    if category == "C1" or type_desc.upper().startswith("H") or "HELICOPTER" in type_desc.upper():
        labels.append("直升機")
    if type_code.startswith(BUSINESS_TYPE_PREFIXES):
        labels.append("商務機")

    # Cargo alone is intentionally not treated as special.
    labels = list(dict.fromkeys(labels))
    return {
        "is_cargo": cargo,
        "is_special": bool(labels),
        "special_labels": labels,
    }


def normalize_aircraft(raw: Mapping, api_now: float | None = None) -> dict | None:
    hex_code = normalize_hex(raw.get("hex"))
    if not hex_code:
        return None
    now = safe_float(api_now, time.time())
    seen = max(0.0, safe_float(raw.get("seen"), 0.0) or 0.0)
    observed_at = now - seen
    altitude_raw = raw.get("alt_baro")
    altitude = None if altitude_raw == "ground" else safe_float(altitude_raw)
    speed = safe_float(raw.get("gs"))
    distance_nm = safe_float(raw.get("r_dst"))
    distance_km = distance_nm * 1.852 if distance_nm is not None else None

    lat = safe_float(raw.get("lat"))
    lon = safe_float(raw.get("lon"))
    seen_pos = safe_float(raw.get("seen_pos"), 0.0)
    if lat is None or lon is None or (seen_pos is not None and seen_pos > 120):
        lat = lon = None

    flight = normalize_text(raw.get("flight"))
    registration = normalize_text(raw.get("r") or raw.get("registration"))
    country_code, country = country_for_hex(hex_code)
    classification = classify_aircraft(raw)
    special_labels = classification["special_labels"]
    return {
        "hex": hex_code,
        "flight": flight,
        "registration": registration,
        "display_name": flight or registration or hex_code.upper(),
        "type_code": normalize_text(raw.get("t") or raw.get("type_code")),
        "type_desc": str(raw.get("desc") or raw.get("type_desc") or "").strip(),
        "operator": str(raw.get("ownOp") or raw.get("operator") or "").strip(),
        "operator_alias": operator_alias(flight, raw.get("ownOp") or raw.get("operator") or ""),
        "db_flags": int(safe_float(raw.get("dbFlags") or raw.get("db_flags"), 0) or 0),
        "country_code": country_code,
        "country": country,
        "is_cargo": int(classification["is_cargo"]),
        "is_special": int(classification["is_special"]),
        "special_labels": ",".join(special_labels),
        "observed_at": observed_at,
        "altitude": altitude,
        "on_ground": int(altitude_raw == "ground"),
        "speed": speed,
        "distance_km": distance_km,
        "lat": lat,
        "lon": lon,
        "track": safe_float(raw.get("track")),
        "vertical_rate": safe_float(raw.get("baro_rate") if raw.get("baro_rate") is not None else raw.get("geom_rate")),
        "squawk": normalize_text(raw.get("squawk")),
        "emergency": normalize_text(raw.get("emergency")),
    }


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def direction_8(degrees: float | None) -> str:
    if degrees is None:
        return "方向未知"
    names = ("北", "東北", "東", "東南", "南", "西南", "西", "西北")
    return names[int((degrees + 22.5) // 45) % 8]


class AirportIndex:
    SHORT_NAMES = {
        "TSA": "松機", "TPE": "桃機", "RMQ": "中機", "CYI": "嘉機",
        "TNN": "南機", "KHH": "小港", "HCN": "恆春", "HUN": "花機",
        "TTT": "東機", "KYD": "蘭嶼", "GNI": "綠島", "MZG": "馬公",
        "CMJ": "七美", "WOT": "望安", "KNH": "金門", "LZN": "南竿",
        "MFK": "北竿",
    }

    def __init__(self, csv_path: str | os.PathLike = "maps/iata-icao.csv"):
        self.airports = []
        try:
            with open(csv_path, encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    icao = normalize_text(row.get("icao"))
                    if not icao.startswith("RC"):
                        continue
                    lat = safe_float(row.get("latitude"))
                    lon = safe_float(row.get("longitude"))
                    if lat is None or lon is None:
                        continue
                    iata = normalize_text(row.get("iata"))
                    label = self.SHORT_NAMES.get(iata, iata or icao)
                    self.airports.append((label, str(row.get("airport") or "").strip(), lat, lon))
        except OSError:
            self.airports = []

    def nearest(self, lat, lon, limit=2):
        if safe_float(lat) is None or safe_float(lon) is None:
            return []
        matches = []
        for code, name, airport_lat, airport_lon in self.airports:
            distance = haversine_km(airport_lat, airport_lon, float(lat), float(lon))
            bearing = bearing_degrees(airport_lat, airport_lon, float(lat), float(lon))
            matches.append({"code": code, "name": name, "distance_km": distance, "direction": direction_8(bearing)})
        return sorted(matches, key=lambda item: item["distance_km"])[:limit]


class AdsbHistoryStore:
    def __init__(self, db_path: str | os.PathLike = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._write_lock = threading.Lock()

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_sync(self):
        with closing(self._connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS adsb_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS aircraft_encounters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hex TEXT NOT NULL,
                    flight TEXT NOT NULL DEFAULT '',
                    registration TEXT NOT NULL DEFAULT '',
                    type_code TEXT NOT NULL DEFAULT '',
                    type_desc TEXT NOT NULL DEFAULT '',
                    operator TEXT NOT NULL DEFAULT '',
                    operator_alias TEXT NOT NULL DEFAULT '',
                    db_flags INTEGER NOT NULL DEFAULT 0,
                    country_code TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT '',
                    is_cargo INTEGER NOT NULL DEFAULT 0,
                    is_special INTEGER NOT NULL DEFAULT 0,
                    special_labels TEXT NOT NULL DEFAULT '',
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    max_altitude REAL,
                    max_speed REAL,
                    max_distance_km REAL,
                    last_lat REAL,
                    last_lon REAL,
                    last_altitude REAL,
                    last_speed REAL,
                    last_track REAL,
                    last_vertical_rate REAL,
                    last_distance_km REAL,
                    on_ground INTEGER NOT NULL DEFAULT 0,
                    squawk TEXT NOT NULL DEFAULT '',
                    emergency TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_adsb_encounters_hex_last
                    ON aircraft_encounters(hex, last_seen DESC);
                CREATE INDEX IF NOT EXISTS idx_adsb_encounters_lookup
                    ON aircraft_encounters(flight, registration, last_seen DESC);
                CREATE TABLE IF NOT EXISTS aircraft_samples (
                    sample_time INTEGER NOT NULL,
                    hex TEXT NOT NULL,
                    flight TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT '',
                    altitude REAL,
                    speed REAL,
                    distance_km REAL,
                    is_cargo INTEGER NOT NULL DEFAULT 0,
                    is_special INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(sample_time, hex)
                );
                CREATE INDEX IF NOT EXISTS idx_adsb_samples_time
                    ON aircraft_samples(sample_time);
                """
            )

    async def initialize(self):
        await asyncio.to_thread(self._initialize_sync)

    @staticmethod
    def _coalesce(new_value, old_value):
        return old_value if new_value in (None, "") else new_value

    def _sample_sync(self, data: Mapping):
        api_now = safe_float(data.get("now"), time.time())
        normalized = []
        for raw in data.get("aircraft", []) or []:
            item = normalize_aircraft(raw, api_now)
            if item and api_now - item["observed_at"] <= 120:
                normalized.append(item)

        with closing(self._connect()) as conn, conn:
            first = conn.execute("SELECT value FROM adsb_meta WHERE key='first_sample_at'").fetchone()
            if first is None:
                conn.execute("INSERT INTO adsb_meta(key, value) VALUES('first_sample_at', ?)", (str(api_now),))
            conn.execute(
                "INSERT INTO adsb_meta(key, value) VALUES('last_sample_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(api_now),),
            )

            for item in normalized:
                latest = conn.execute(
                    "SELECT * FROM aircraft_encounters WHERE hex=? ORDER BY last_seen DESC LIMIT 1",
                    (item["hex"],),
                ).fetchone()
                new_encounter = latest is None or item["observed_at"] - latest["last_seen"] > ENCOUNTER_GAP_SECONDS
                if latest is not None and latest["flight"] and item["flight"] and latest["flight"] != item["flight"]:
                    new_encounter = True

                # readsb fields are optional. Preserve stable airframe metadata when
                # a later snapshot temporarily omits the aircraft database fields.
                if latest is not None:
                    for key in (
                        "registration", "type_code", "type_desc", "operator",
                        "operator_alias", "country_code", "country",
                    ):
                        if not item[key]:
                            item[key] = latest[key]
                    item["db_flags"] |= latest["db_flags"]
                    item["is_cargo"] = max(item["is_cargo"], latest["is_cargo"])
                    item["is_special"] = max(item["is_special"], latest["is_special"])
                    labels = [label for label in (latest["special_labels"], item["special_labels"]) if label]
                    item["special_labels"] = ",".join(dict.fromkeys(",".join(labels).split(",")))

                if new_encounter:
                    conn.execute(
                        """
                        INSERT INTO aircraft_encounters (
                            hex, flight, registration, type_code, type_desc, operator,
                            operator_alias, db_flags, country_code, country, is_cargo,
                            is_special, special_labels, first_seen, last_seen,
                            max_altitude, max_speed, max_distance_km, last_lat, last_lon,
                            last_altitude, last_speed, last_track, last_vertical_rate,
                            last_distance_km, on_ground, squawk, emergency
                        ) VALUES (
                            :hex, :flight, :registration, :type_code, :type_desc, :operator,
                            :operator_alias, :db_flags, :country_code, :country, :is_cargo,
                            :is_special, :special_labels, :observed_at, :observed_at,
                            :altitude, :speed, :distance_km, :lat, :lon, :altitude,
                            :speed, :track, :vertical_rate, :distance_km, :on_ground,
                            :squawk, :emergency
                        )
                        """,
                        item,
                    )
                else:
                    merged = dict(item)
                    for key in (
                        "flight", "registration", "type_code", "type_desc", "operator",
                        "operator_alias", "country_code", "country", "special_labels",
                        "lat", "lon", "altitude", "speed", "track", "vertical_rate",
                        "distance_km", "squawk", "emergency",
                    ):
                        old_key = "last_distance_km" if key == "distance_km" else f"last_{key}"
                        if key in {"flight", "registration", "type_code", "type_desc", "operator", "operator_alias", "country_code", "country", "special_labels", "squawk", "emergency"}:
                            old_key = key
                        merged[key] = self._coalesce(merged[key], latest[old_key])
                    merged["id"] = latest["id"]
                    merged["max_altitude"] = max(v for v in (latest["max_altitude"], item["altitude"]) if v is not None) if latest["max_altitude"] is not None or item["altitude"] is not None else None
                    merged["max_speed"] = max(v for v in (latest["max_speed"], item["speed"]) if v is not None) if latest["max_speed"] is not None or item["speed"] is not None else None
                    merged["max_distance_km"] = max(v for v in (latest["max_distance_km"], item["distance_km"]) if v is not None) if latest["max_distance_km"] is not None or item["distance_km"] is not None else None
                    conn.execute(
                        """
                        UPDATE aircraft_encounters SET
                            flight=:flight, registration=:registration, type_code=:type_code,
                            type_desc=:type_desc, operator=:operator,
                            operator_alias=:operator_alias, db_flags=:db_flags,
                            country_code=:country_code, country=:country,
                            is_cargo=:is_cargo, is_special=:is_special,
                            special_labels=:special_labels, last_seen=:observed_at,
                            max_altitude=:max_altitude, max_speed=:max_speed,
                            max_distance_km=:max_distance_km, last_lat=:lat,
                            last_lon=:lon, last_altitude=:altitude, last_speed=:speed,
                            last_track=:track, last_vertical_rate=:vertical_rate,
                            last_distance_km=:distance_km, on_ground=:on_ground,
                            squawk=:squawk, emergency=:emergency
                        WHERE id=:id
                        """,
                        merged,
                    )

                sample_time = int(item["observed_at"] // 60) * 60
                conn.execute(
                    """
                    INSERT INTO aircraft_samples (
                        sample_time, hex, flight, country, altitude, speed,
                        distance_km, is_cargo, is_special
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sample_time, hex) DO UPDATE SET
                        flight=excluded.flight, country=excluded.country,
                        altitude=COALESCE(excluded.altitude, aircraft_samples.altitude),
                        speed=COALESCE(excluded.speed, aircraft_samples.speed),
                        distance_km=COALESCE(excluded.distance_km, aircraft_samples.distance_km),
                        is_cargo=excluded.is_cargo, is_special=excluded.is_special
                    """,
                    (
                        sample_time, item["hex"], item["flight"], item["country"],
                        item["altitude"], item["speed"], item["distance_km"],
                        item["is_cargo"], item["is_special"],
                    ),
                )

            cutoff = api_now - RETENTION_SECONDS
            conn.execute("DELETE FROM aircraft_samples WHERE sample_time < ?", (cutoff,))
            conn.execute("DELETE FROM aircraft_encounters WHERE last_seen < ?", (cutoff,))
        return normalized

    async def sample(self, data: Mapping):
        return await asyncio.to_thread(self._sample_locked_sync, data)

    def _sample_locked_sync(self, data: Mapping):
        with self._write_lock:
            return self._sample_sync(data)

    def _coverage_sync(self, now: float):
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT value FROM adsb_meta WHERE key='first_sample_at'").fetchone()
        return max(0.0, now - float(row[0])) if row else 0.0

    async def coverage_seconds(self, now: float | None = None):
        return await asyncio.to_thread(self._coverage_sync, now or time.time())

    def _recent_encounters_sync(self, now: float, kind: str | None = None):
        cutoff = now - HISTORY_SECONDS
        where = "last_seen >= ?"
        params = [cutoff]
        if kind in {"cargo", "special"}:
            where += f" AND is_{kind}=1"
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM aircraft_encounters WHERE {where} ORDER BY last_seen DESC",
                params,
            ).fetchall()
        result, seen = [], set()
        for row in rows:
            if row["hex"] in seen:
                continue
            seen.add(row["hex"])
            result.append(dict(row))
        return result

    async def recent_encounters(self, kind: str | None = None, now: float | None = None):
        return await asyncio.to_thread(self._recent_encounters_sync, now or time.time(), kind)

    def _summary_sync(self, now: float):
        cutoff = now - HISTORY_SECONDS
        with closing(self._connect()) as conn:
            counts = conn.execute(
                "SELECT COUNT(DISTINCT hex), COUNT(DISTINCT NULLIF(country, '')) "
                "FROM aircraft_samples WHERE sample_time >= ?",
                (cutoff,),
            ).fetchone()
            extrema = {}
            for key, column, order in (
                ("lowest", "altitude", "ASC"),
                ("highest", "altitude", "DESC"),
                ("fastest", "speed", "DESC"),
                ("farthest", "distance_km", "DESC"),
            ):
                row = conn.execute(
                    f"SELECT hex, flight, {column} AS value FROM aircraft_samples "
                    f"WHERE sample_time >= ? AND {column} IS NOT NULL ORDER BY {column} {order} LIMIT 1",
                    (cutoff,),
                ).fetchone()
                extrema[key] = dict(row) if row else None
        return {"aircraft_count": counts[0], "country_count": counts[1], **extrema}

    async def summary(self, now: float | None = None):
        return await asyncio.to_thread(self._summary_sync, now or time.time())

    def _lookup_sync(self, query: str, now: float):
        target = normalize_text(query).replace(" ", "")
        cutoff = now - HISTORY_SECONDS
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM aircraft_encounters
                WHERE last_seen >= ? AND (
                    UPPER(hex)=? OR UPPER(flight)=? OR UPPER(registration)=?
                ) ORDER BY last_seen DESC
                """,
                (cutoff, target, target, target),
            ).fetchall()
            by_hex = {}
            for row in rows:
                by_hex.setdefault(row["hex"], dict(row))
            for hex_code, item in by_hex.items():
                encounters = conn.execute(
                    "SELECT COUNT(*), MIN(first_seen), MAX(last_seen) FROM aircraft_encounters "
                    "WHERE hex=? AND last_seen>=?",
                    (hex_code, cutoff),
                ).fetchone()
                extrema = conn.execute(
                    "SELECT MAX(altitude), MAX(speed), MAX(distance_km) FROM aircraft_samples "
                    "WHERE hex=? AND sample_time>=?",
                    (hex_code, cutoff),
                ).fetchone()
                item.update({
                    "encounter_count": encounters[0],
                    "period_first_seen": encounters[1],
                    "period_last_seen": encounters[2],
                    "period_max_altitude": extrema[0],
                    "period_max_speed": extrema[1],
                    "period_max_distance_km": extrema[2],
                })
        return list(by_hex.values())

    async def lookup(self, query: str, now: float | None = None):
        return await asyncio.to_thread(self._lookup_sync, query, now or time.time())

    def _autocomplete_sync(self, current: str, now: float):
        needle = normalize_text(current)
        cutoff = now - HISTORY_SECONDS
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT hex, flight, registration, operator_alias, last_seen FROM aircraft_encounters "
                "WHERE last_seen>=? ORDER BY last_seen DESC",
                (cutoff,),
            ).fetchall()
        suggestions, used = [], set()
        for row in rows:
            values = [row["flight"], row["registration"], row["hex"].upper()]
            if needle and not any(needle in normalize_text(value) for value in values):
                continue
            value = next((v for v in values if v), "")
            key = (value, row["hex"])
            if not value or key in used:
                continue
            used.add(key)
            suffix = f" · {row['operator_alias']}" if row["operator_alias"] else ""
            suggestions.append((f"{value}{suffix} · {row['hex'].upper()}", value))
            if len(suggestions) == 25:
                break
        return suggestions

    async def autocomplete(self, current: str, now: float | None = None):
        return await asyncio.to_thread(self._autocomplete_sync, current, now or time.time())


def live_aircraft(data: Mapping | None) -> list[dict]:
    if not data:
        return []
    now = safe_float(data.get("now"), time.time())
    result = []
    for raw in data.get("aircraft", []) or []:
        item = normalize_aircraft(raw, now)
        if not item or item["on_ground"] or item["lat"] is None or item["lon"] is None:
            continue
        if now - item["observed_at"] > 60:
            continue
        result.append(item)
    return sorted(result, key=lambda item: item["distance_km"] if item["distance_km"] is not None else math.inf)
