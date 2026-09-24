import asyncio
import time
import logging
import json
import re
from datetime import datetime, timezone, timedelta
import aiohttp

from modules.location_matcher import match_location, DEFAULT_TOWN_MAPPING
from modules.config import get_config

logger = logging.getLogger(__name__)

TAIPEI_TZ = timezone(timedelta(hours=8))


def _clean_val(val, default="未知"):
    if val is None or val == "" or str(val) in ["-99", "-99.0", "-999", "-999.0", "-990", "-990.0"]:
        return default
    return str(val).strip()


class AICache:
    """
    小裁雨 AI 專用全域快取中心 (AI Data Cache)
    負責快取全台即時氣象觀測、預報、地震、警特報、颱風與空氣品質，
    讓 AI 能夠以毫秒級延遲調用豐富的多維度資料，同時避免對中央氣象署 API 造成頻繁請求與超額。
    """
    def __init__(self):
        self._lock = asyncio.Lock()
        
        # 1. 全台即時觀測快取 (O-A0001-001)
        self._observations_data = None
        self._observations_time = 0.0
        self._observations_ttl = 300.0  # 5 分鐘
        self._obs_by_location = {}      # "縣市鄉鎮" -> [測站]
        self._obs_by_station_name = {}  # "測站名" -> 測站
        self._obs_by_county = {}        # "縣市" -> [測站]
        self._extreme_records = {}      # 極端高低溫與累積雨量

        # 2. 鄉鎮預報快取 (F-D0047-093)
        self._forecasts_cache = {}      # "縣市_鄉鎮" -> (data, timestamp)
        self._forecasts_ttl = 1800.0    # 30 分鐘

        # 3. 地震快取 (E-A0015-001 / E-A0016-001)
        self._earthquake_data = None
        self._earthquake_time = 0.0
        self._earthquake_ttl = 120.0    # 2 分鐘

        # 4. 氣象特報/警報快取 (W-C0033-001 / W-C0033-002)
        self._warnings_data = None
        self._warnings_time = 0.0
        self._warnings_ttl = 300.0      # 5 分鐘

        # 5. 颱風狀態快取 (W-C0034-001)
        self._typhoon_data = None
        self._typhoon_time = 0.0
        self._typhoon_ttl = 600.0       # 10 分鐘

        # 6. 空氣品質快取 (MOENV)
        self._aqi_data = None
        self._aqi_time = 0.0
        self._aqi_ttl = 1200.0          # 20 分鐘

    # ================= 1. 全台即時氣象觀測快取 =================

    async def _fetch_and_index_observations(self, session: aiohttp.ClientSession, api_key: str) -> bool:
        """從 CWA O-A0001-001 下載全台所有測站觀測資料並建立快速索引"""
        if not api_key:
            return False

        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001"
        headers = {"Authorization": api_key}
        
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status != 200:
                    logger.warning(f"⚠️ [AI 快取] 獲取 O-A0001-001 失敗，狀態碼: {resp.status}")
                    return False
                data = await resp.json()
        except Exception as e:
            logger.error(f"❌ [AI 快取] 請求 O-A0001-001 發生異常: {e!r}")
            return False

        stations = data.get("records", {}).get("Station", [])
        if not stations:
            return False

        by_location = {}
        by_station = {}
        by_county = {}

        max_temp = -999.0
        max_temp_item = None
        min_temp = 999.0
        min_temp_item = None
        max_rain = -1.0
        max_rain_item = None

        for st in stations:
            geo = st.get("GeoInfo", {})
            county = geo.get("CountyName", "")
            town = geo.get("TownName", "")
            st_name = st.get("StationName", "")

            if county and town:
                key = f"{county}{town}"
                by_location.setdefault(key, []).append(st)

            if county:
                by_county.setdefault(county, []).append(st)

            if st_name:
                by_station[st_name] = st

            we = st.get("WeatherElement", {})
            
            # 極端最高溫
            daily_high = we.get("DailyExtreme", {}).get("DailyHigh", {}) or we.get("DailyHigh", {}) or {}
            t_high_raw = daily_high.get("TemperatureInfo", {}).get("AirTemperature")
            t_high_time = daily_high.get("TemperatureInfo", {}).get("Occurred_at", {}).get("DateTime", "")
            if t_high_raw is not None and str(t_high_raw) not in ["-99", "-99.0", "-999"]:
                try:
                    val = float(t_high_raw)
                    if val > max_temp:
                        max_temp = val
                        max_temp_item = {
                            "station": st_name,
                            "location": f"{county}{town}",
                            "temp": val,
                            "time": t_high_time
                        }
                except (ValueError, TypeError):
                    pass

            # 極端最低溫
            daily_low = we.get("DailyExtreme", {}).get("DailyLow", {}) or we.get("DailyLow", {}) or {}
            t_low_raw = daily_low.get("TemperatureInfo", {}).get("AirTemperature")
            t_low_time = daily_low.get("TemperatureInfo", {}).get("Occurred_at", {}).get("DateTime", "")
            if t_low_raw is not None and str(t_low_raw) not in ["-99", "-99.0", "-999"]:
                try:
                    val = float(t_low_raw)
                    if val < min_temp and val > -60:
                        min_temp = val
                        min_temp_item = {
                            "station": st_name,
                            "location": f"{county}{town}",
                            "temp": val,
                            "time": t_low_time
                        }
                except (ValueError, TypeError):
                    pass

            # 極端本日累積雨量
            precip = we.get("Now", {}).get("Precipitation")
            if precip is None or str(precip) in ["-99", "-99.0"]:
                precip = we.get("Precipitation")
            if precip is not None and str(precip) not in ["-99", "-99.0", "-999"]:
                try:
                    p_val = float(precip)
                    if p_val > max_rain:
                        max_rain = p_val
                        max_rain_item = {
                            "station": st_name,
                            "location": f"{county}{town}",
                            "rain": p_val
                        }
                except (ValueError, TypeError):
                    pass

        self._observations_data = stations
        self._observations_time = time.time()
        self._obs_by_location = by_location
        self._obs_by_station_name = by_station
        self._obs_by_county = by_county
        self._extreme_records = {
            "highest_temp": max_temp_item,
            "lowest_temp": min_temp_item,
            "max_rain": max_rain_item
        }

        logger.info(f"💾 [AI 快取] 成功快取全台 {len(stations)} 個測站觀測資料，覆蓋 {len(by_location)} 個鄉鎮市區。")
        return True

    async def get_current_weather(self, session: aiohttp.ClientSession, location_input: str, api_key: str = None) -> dict:
        """查詢指定鄉鎮市區的即時天氣（優先從記憶體快取命中）"""
        if not api_key:
            cfg = get_config()
            api_key = cfg.get("CWA_API_KEY")

        now = time.time()
        if not self._observations_data or (now - self._observations_time >= self._observations_ttl):
            async with self._lock:
                if not self._observations_data or (time.time() - self._observations_time >= self._observations_ttl):
                    await self._fetch_and_index_observations(session, api_key)

        loc_val, error_msg = match_location(location_input)
        if error_msg:
            return {"error": error_msg}

        county_name = loc_val[:3]
        town_name = loc_val[3:]
        full_key = f"{county_name}{town_name}"

        # 1. 精準鄉鎮測站查找
        matched_stations = self._obs_by_location.get(full_key, [])
        if not matched_stations:
            # 2. 若該鄉鎮無專屬自動測站，退回使用同縣市的代表測站
            matched_stations = self._obs_by_county.get(county_name, [])

        if not matched_stations:
            return {"查詢地點": full_key, "狀態": "查無該區域之有效觀測測站資料"}

        st = matched_stations[0]
        st_name = st.get("StationName", "測站")
        we = st.get("WeatherElement", {})
        obs_time = st.get("ObsTime", {}).get("DateTime", "")

        precip = we.get("Now", {}).get("Precipitation")
        if precip is None or str(precip) == "-99":
            precip = we.get("Precipitation")

        daily_extreme = we.get("DailyExtreme", {}) or {}
        daily_high = daily_extreme.get("DailyHigh", {}) or we.get("DailyHigh", {}) or {}
        daily_low = daily_extreme.get("DailyLow", {}) or we.get("DailyLow", {}) or {}
        high_temp = daily_high.get("TemperatureInfo", {}).get("AirTemperature")
        low_temp = daily_low.get("TemperatureInfo", {}).get("AirTemperature")

        return {
            "查詢地點": full_key,
            "代表測站": st_name,
            "觀測時間": obs_time,
            "天氣現象": _clean_val(we.get("Weather"), "良好"),
            "目前氣溫": f"{_clean_val(we.get('AirTemperature'))} °C",
            "今日最高溫": f"{_clean_val(high_temp)} °C" if high_temp else "暫無",
            "今日最低溫": f"{_clean_val(low_temp)} °C" if low_temp else "暫無",
            "相對濕度": f"{_clean_val(we.get('RelativeHumidity'))} %",
            "本日累積降雨": f"{_clean_val(precip, '0.0')} mm",
            "風向風速": f"{_clean_val(we.get('WindSpeed'))} m/s",
            "資料來源": "本機快取 (CWA O-A0001-001)"
        }

    async def get_extreme_climate(self, session: aiohttp.ClientSession, api_key: str = None) -> dict:
        """查詢今日全台極端氣溫與降雨紀錄（最高溫、最低溫、最大降雨測站）"""
        if not api_key:
            cfg = get_config()
            api_key = cfg.get("CWA_API_KEY")

        now = time.time()
        if not self._observations_data or (now - self._observations_time >= self._observations_ttl):
            async with self._lock:
                if not self._observations_data or (time.time() - self._observations_time >= self._observations_ttl):
                    await self._fetch_and_index_observations(session, api_key)

        if not self._extreme_records:
            return {"狀態": "目前尚無極端氣象資料可供查詢"}

        hi = self._extreme_records.get("highest_temp")
        lo = self._extreme_records.get("lowest_temp")
        rain = self._extreme_records.get("max_rain")

        res = {"統計時間": datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")}
        if hi:
            res["今日全台最高溫"] = f"{hi['temp']} °C (測站: {hi['station']}，位於 {hi['location']})"
        if lo:
            res["今日全台最低溫"] = f"{lo['temp']} °C (測站: {lo['station']}，位於 {lo['location']})"
        if rain:
            res["今日全台最大累積雨量"] = f"{rain['rain']} mm (測站: {rain['station']}，位於 {rain['location']})"

        return res

    # ================= 2. 鄉鎮預報快取 =================

    async def get_weather_forecast(self, session: aiohttp.ClientSession, location_input: str, api_key: str = None) -> dict:
        """查詢指定鄉鎮市區之未來天氣預報（按地點獨立快取 30 分鐘）"""
        if not api_key:
            cfg = get_config()
            api_key = cfg.get("CWA_API_KEY")

        loc_val, error_msg = match_location(location_input)
        if error_msg:
            return {"error": error_msg}

        county_name = loc_val[:3]
        town_name = loc_val[3:]
        cache_key = f"{county_name}_{town_name}"

        now = time.time()
        if cache_key in self._forecasts_cache:
            data, ts = self._forecasts_cache[cache_key]
            if now - ts < self._forecasts_ttl:
                return data

        from modules.ai_tools import COUNTY_LOCATION_ID
        location_id = COUNTY_LOCATION_ID.get(county_name)
        if not location_id:
            return {"error": f"找不到縣市代碼：{county_name}"}

        import urllib.parse
        encoded_town = urllib.parse.quote(town_name)
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-093?locationId={location_id}&LocationName={encoded_town}&ElementName="
        headers = {"Authorization": api_key}

        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return {"error": f"氣象署預報 API 回傳 HTTP {resp.status}"}
                data = await resp.json()
        except Exception as e:
            logger.error(f"❌ [AI 快取] 預報請求失敗: {e!r}")
            return {"error": f"無法連線至氣象署預報 API: {e!r}"}

        locations_list = data.get("records", {}).get("Locations", [])
        target_location = None
        for locs in locations_list:
            if locs.get("LocationsName") == county_name:
                for loc in locs.get("Location", []):
                    if loc.get("LocationName") == town_name:
                        target_location = loc
                        break
            if target_location:
                break

        if not target_location:
            return {"error": f"找不到 {county_name}{town_name} 的預報資料"}

        elements = {we.get("ElementName"): we for we in target_location.get("WeatherElement", [])}
        wx_elem = elements.get("天氣現象", {}).get("Time", [])
        if not wx_elem:
            return {"error": f"{county_name}{town_name} 目前無可用時段之預報資料"}

        forecast_periods = []
        for t_data in wx_elem[:3]:
            st = t_data.get("StartTime", "")
            et = t_data.get("EndTime", "")
            wx_val = t_data.get("ElementValue", [{}])[0].get("Weather", "未知")

            pop_val = "0%"
            pop_times = elements.get("12小時降雨機率", {}).get("Time", [])
            for p in pop_times:
                if p.get("StartTime") == st:
                    vals = p.get("ElementValue", [{}])
                    if vals:
                        pop_val = f"{vals[0].get('ProbabilityOfPrecipitation', '0')}%"
                    break

            temp_val = "未知"
            temp_times = elements.get("平均溫度", {}).get("Time", [])
            for t in temp_times:
                if t.get("StartTime") == st:
                    vals = t.get("ElementValue", [{}])
                    if vals:
                        temp_val = f"{vals[0].get('Temperature', '未知')}°C"
                    break

            desc_val = ""
            desc_times = elements.get("天氣預報綜合描述", {}).get("Time", [])
            for d in desc_times:
                if d.get("StartTime") == st:
                    vals = d.get("ElementValue", [{}])
                    if vals:
                        desc_val = vals[0].get("WeatherDescription", "")
                    break

            forecast_periods.append({
                "時段": f"{st} 至 {et}",
                "天氣狀況": wx_val,
                "平均氣溫": temp_val,
                "降雨機率": pop_val,
                "天氣描述": desc_val
            })

        result = {
            "地點": f"{county_name}{town_name}",
            "預報時段清單": forecast_periods
        }
        self._forecasts_cache[cache_key] = (result, time.time())
        return result

    # ================= 3. 最新地震快取 =================

    async def get_latest_earthquake(self, session: aiohttp.ClientSession, api_key: str = None) -> dict:
        """查詢最新有感地震報告（快取 2 分鐘）"""
        if not api_key:
            cfg = get_config()
            api_key = cfg.get("CWA_API_KEY")

        now = time.time()
        if self._earthquake_data and (now - self._earthquake_time < self._earthquake_ttl):
            return self._earthquake_data

        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001?limit=1&format=JSON"
        headers = {"Authorization": api_key} if api_key else {}

        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return {"error": f"氣象署地震 API 回傳 HTTP {resp.status}"}
                data = await resp.json()
        except Exception as e:
            logger.error(f"❌ [AI 快取] 地震資料請求失敗: {e!r}")
            return {"error": f"無法連線至氣象署地震 API: {e!r}"}

        records = data.get("records", {}).get("Earthquake", [])
        if not records:
            res = {"地震報告": "近期無顯著有感地震報告"}
            self._earthquake_data = res
            self._earthquake_time = time.time()
            return res

        eq = records[0]
        eq_info = eq.get("EarthquakeInfo", {})
        origin_time = eq_info.get("OriginTime", "未知時間")
        epicenter = eq_info.get("Epicenter", {}).get("Location", "未知地點")
        depth = eq_info.get("FocalDepth", "未知")
        mag = eq_info.get("EarthquakeMagnitude", {}).get("MagnitudeValue", "未知")
        report_content = eq.get("ReportContent", "")

        shaking_summary = []
        for area in eq.get("Intensity", {}).get("ShakingArea", []):
            area_desc = area.get("AreaDesc", "")
            max_int = area.get("AreaIntensity", "")
            if area_desc and max_int:
                shaking_summary.append(f"{area_desc}: {max_int}")
            if len(shaking_summary) >= 6:
                break

        res = {
            "地震時間": origin_time,
            "震央位置": epicenter,
            "芮氏規模": mag,
            "震源深度": f"{depth} 公里",
            "報告內文": report_content,
            "各地顯著震度": "、".join(shaking_summary) if shaking_summary else "無詳細震度資訊"
        }
        self._earthquake_data = res
        self._earthquake_time = time.time()
        return res

    # ================= 4. 氣象特報/警報快取 =================

    async def get_weather_warnings(self, session: aiohttp.ClientSession, location_input: str = None, api_key: str = None) -> dict:
        """查詢全台或特定地點之即時氣象特報（大雨、豪雨、強風、低溫、濃霧、高溫資訊）"""
        if not api_key:
            cfg = get_config()
            api_key = cfg.get("CWA_API_KEY")

        now = time.time()
        if not self._warnings_data or (now - self._warnings_time >= self._warnings_ttl):
            async with self._lock:
                if not self._warnings_data or (time.time() - self._warnings_time >= self._warnings_ttl):
                    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0033-001"
                    headers = {"Authorization": api_key} if api_key else {}
                    try:
                        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                locations = data.get("records", {}).get("location", [])
                                active_warnings = {}
                                for loc in locations:
                                    loc_name = loc.get("locationName", "")
                                    hazards = loc.get("hazardConditions", {}).get("hazards", [])
                                    if hazards:
                                        h_list = []
                                        for h in hazards:
                                            info = h.get("info", {})
                                            h_name = info.get("phenomena", "") or info.get("hazardCondition", "")
                                            sign = info.get("significance", "")
                                            full_h = f"{h_name}{sign}".strip()
                                            if full_h:
                                                h_list.append(full_h)
                                        if h_list:
                                            active_warnings[loc_name] = h_list
                                self._warnings_data = active_warnings
                                self._warnings_time = time.time()
                            else:
                                self._warnings_data = {}
                    except Exception as e:
                        logger.error(f"❌ [AI 快取] 抓取氣象特報失敗: {e!r}")
                        self._warnings_data = {}

        warnings = self._warnings_data or {}

        # 針對特定地點查詢
        if location_input and location_input.strip() and location_input.strip() not in ["台灣", "臺灣", "全台", "全部"]:
            loc_val, _ = match_location(location_input)
            county = (loc_val[:3] if loc_val else location_input).replace("台", "臺")
            if county in warnings:
                return {
                    "地點": county,
                    "發布警特報": "、".join(warnings[county]),
                    "狀態": "警戒中，請留意天氣變化"
                }
            else:
                return {
                    "地點": county,
                    "發布警特報": "目前無任何氣象警特報",
                    "狀態": "安全良好"
                }

        # 查詢全台警特報概況
        if not warnings:
            return {"全台警特報狀態": "目前全台灣各地區均無發布任何氣象警特報。"}

        summary = []
        for c, hazards in warnings.items():
            summary.append(f"{c}: {'、'.join(hazards)}")

        return {
            "全台發布特報清單": summary,
            "發布縣市數量": len(warnings)
        }

    # ================= 5. 颱風狀態快取 =================

    async def get_typhoon_status(self, session: aiohttp.ClientSession, api_key: str = None) -> dict:
        """查詢目前是否有颱風或氣象署是否發布颱風警報（快取 10 分鐘）"""
        now = time.time()
        if self._typhoon_data and (now - self._typhoon_time < self._typhoon_ttl):
            return self._typhoon_data

        try:
            from cogs.typhoon import fetch_typhoon_warning
            warn_info = await fetch_typhoon_warning(session)
        except Exception as e:
            warn_info = None

        if warn_info:
            res = {
                "是否有颱風警報": "發布中",
                "警報標題": warn_info.get("headline", ""),
                "發布時間": warn_info.get("effective", ""),
                "警戒區域": "、".join(warn_info.get("areas", [])),
                "警報內容摘要": warn_info.get("description", "")[:200]
            }
        else:
            res = {
                "是否有颱風警報": "無",
                "說明": "目前中央氣象署未發布任何颱風警報，西北太平洋海面目前無威脅臺灣之颱風警戒。"
            }

        self._typhoon_data = res
        self._typhoon_time = time.time()
        return res

    # ================= 6. 空氣品質快取 =================

    async def get_air_quality(self, session: aiohttp.ClientSession, location_input: str = None, api_key: str = None) -> dict:
        """查詢指定縣市或全台之空氣品質 AQI 與 PM2.5（快取 20 分鐘）"""
        if not api_key:
            cfg = get_config()
            api_key = cfg.get("MOENV_API_KEY")

        now = time.time()
        if not self._aqi_data or (now - self._aqi_time >= self._aqi_ttl):
            if api_key:
                url = "https://data.moenv.gov.tw/api/v2/aqx_p_432"
                params = {"api_key": api_key}
                try:
                    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            self._aqi_data = data.get("records", [])
                            self._aqi_time = time.time()
                except Exception as e:
                    logger.warning(f"⚠️ [AI 快取] 抓取環境部 AQI 失敗: {e!r}")

        records = self._aqi_data or []
        if not records:
            return {"狀態": "目前空氣品質資料來源連線中或維護中，一般情況下臺灣各區空氣品質良好。"}

        target_county = None
        if location_input:
            loc_val, _ = match_location(location_input)
            target_county = (loc_val[:3] if loc_val else location_input).replace("臺", "台")

        matched = []
        for r in records:
            c = r.get("county", "").replace("臺", "台")
            if not target_county or target_county in c or c in target_county:
                matched.append({
                    "測站": r.get("sitename"),
                    "縣市": r.get("county"),
                    "AQI指標": r.get("aqi"),
                    "品質狀態": r.get("status"),
                    "PM2.5": f"{r.get('pm2.5', '未知')} μg/m3"
                })

        if matched:
            first = matched[0]
            return {
                "地點": first["縣市"],
                "代表測站": first["測站"],
                "AQI空氣品質指標": first["AQI指標"],
                "空氣品質狀態": first["品質狀態"],
                "細懸浮微粒PM2.5": first["PM2.5"],
                "同縣市測站數量": len(matched)
            }

        return {"狀態": f"查無 {location_input} 之空品測站資料"}

    # ================= 7. 即時情境摘要 (Prompt Context Injection) =================

    async def get_realtime_summary(self, session: aiohttp.ClientSession, api_key: str = None) -> str:
        """
        產出供 System Prompt / 動態情境注入的精簡 1~2 句話摘要。
        讓 AI 在使用者發言的第一秒，就具備全台重大警報、極端溫與地震感知。
        """
        parts = []

        # 特報摘要
        warnings_info = await self.get_weather_warnings(session, api_key=api_key)
        if warnings_info.get("全台發布特報清單"):
            warn_items = warnings_info["全台發布特報清單"][:3]
            parts.append(f"目前全台發布氣象特報：{'；'.join(warn_items)}")
        else:
            parts.append("目前全台無發布氣象特報")

        # 極端溫摘要
        extreme_info = await self.get_extreme_climate(session, api_key=api_key)
        hi = extreme_info.get("今日全台最高溫")
        if hi:
            parts.append(f"今日全台最高溫：{hi}")

        # 颱風警報狀態
        ty_info = await self.get_typhoon_status(session, api_key=api_key)
        if ty_info.get("是否有颱風警報") == "發布中":
            parts.append(f"【⚠️ 颱風警報發布中】：{ty_info.get('警報標題')}")

        return " | ".join(parts)


class ConversationCache:
    """
    小裁雨 AI 多輪對話歷史快取中心
    維護各頻道或私訊獨立的歷史對話對列，具備自動過期（TTL）與對話輪數限制，
    支援 OpenAI / Groq 格式與 Google Gemini 格式轉換。
    """
    def __init__(self, max_turns: int = 8, ttl_seconds: float = 900.0):
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self.history = {}       # channel_id -> list of {"role": "user"|"assistant", "content": ..., "timestamp": ...}
        self.last_active = {}   # channel_id -> float timestamp

    def _cleanup_expired(self, channel_id: int):
        now = time.time()
        last = self.last_active.get(channel_id, 0)
        if now - last >= self.ttl_seconds:
            self.history.pop(channel_id, None)
            self.last_active.pop(channel_id, None)

    def add_user_message(self, channel_id: int, content: str, author_name: str = ""):
        self._cleanup_expired(channel_id)
        if channel_id not in self.history:
            self.history[channel_id] = []

        now = time.time()
        self.last_active[channel_id] = now
        self.history[channel_id].append({
            "role": "user",
            "content": content,
            "author": author_name,
            "timestamp": now
        })
        if len(self.history[channel_id]) > self.max_turns * 2:
            self.history[channel_id] = self.history[channel_id][-self.max_turns * 2:]

    def add_assistant_message(self, channel_id: int, content: str):
        if channel_id not in self.history:
            self.history[channel_id] = []

        now = time.time()
        self.last_active[channel_id] = now
        self.history[channel_id].append({
            "role": "assistant",
            "content": content,
            "timestamp": now
        })
        if len(self.history[channel_id]) > self.max_turns * 2:
            self.history[channel_id] = self.history[channel_id][-self.max_turns * 2:]

    def get_history(self, channel_id: int) -> list:
        self._cleanup_expired(channel_id)
        return list(self.history.get(channel_id, []))

    def clear_channel(self, channel_id: int):
        self.history.pop(channel_id, None)
        self.last_active.pop(channel_id, None)

    def format_for_groq(self, channel_id: int, current_user_prompt: str, system_instruction: str = None) -> list:
        """格式化為 Groq / OpenAI 標準 messages 格式"""
        self._cleanup_expired(channel_id)
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        # 加入歷史對話（排除當前這一輪）
        hist = self.history.get(channel_id, [])
        for item in hist:
            role = item["role"]
            content = item["content"]
            messages.append({"role": role, "content": content})

        # 加入當前使用者最新輸入
        messages.append({"role": "user", "content": current_user_prompt})
        return messages

    def format_for_gemini(self, channel_id: int, current_user_prompt: str) -> list:
        """格式化為 Google Gemini contents 格式"""
        self._cleanup_expired(channel_id)
        contents = []

        hist = self.history.get(channel_id, [])
        for item in hist:
            role = "user" if item["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": item["content"]}]
            })

        contents.append({
            "role": "user",
            "parts": [{"text": current_user_prompt}]
        })
        return contents


# 單例快取實例
ai_cache = AICache()
conversation_cache = ConversationCache(max_turns=8, ttl_seconds=900.0)
