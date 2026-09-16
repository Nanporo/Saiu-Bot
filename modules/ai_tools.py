import json
import logging
import aiohttp
from datetime import datetime, timezone, timedelta
from modules.location_matcher import match_location
from modules.config import get_config

logger = logging.getLogger(__name__)

# 縣市與預報代碼對照表
COUNTY_LOCATION_ID = {
    "宜蘭縣": "F-D0047-003",
    "桃園市": "F-D0047-007",
    "新竹縣": "F-D0047-011",
    "苗栗縣": "F-D0047-015",
    "彰化縣": "F-D0047-019",
    "南投縣": "F-D0047-023",
    "雲林縣": "F-D0047-027",
    "嘉義縣": "F-D0047-031",
    "屏東縣": "F-D0047-035",
    "臺東縣": "F-D0047-039",
    "花蓮縣": "F-D0047-043",
    "澎湖縣": "F-D0047-047",
    "基隆市": "F-D0047-051",
    "新竹市": "F-D0047-055",
    "嘉義市": "F-D0047-059",
    "臺北市": "F-D0047-063",
    "高雄市": "F-D0047-067",
    "新北市": "F-D0047-071",
    "臺中市": "F-D0047-075",
    "臺南市": "F-D0047-079",
    "連江縣": "F-D0047-083",
    "金門縣": "F-D0047-087"
}

# ================= Groq / OpenAI 相容 Tools 規格 =================
AI_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "取得台灣指定地點的「現在即時」天氣觀測資料（氣溫、天氣狀況、濕度、累積降雨等）。當使用者詢問某地現在幾度、目前天氣、會不會冷，或是回答指定地點名稱時，必須調用此工具。嚴禁回覆指令叫使用者自己查。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "台灣的縣市或鄉鎮市區名稱，例如：台北、信義區、台中、高雄市苓雅區"
                    }
                },
                "required": ["location"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_forecast",
            "description": "取得台灣指定地點的「未來天氣預報」（包含降雨機率、未來幾天或今日/明日氣溫區間、天氣現象、綜合天氣描述）。當使用者詢問明天、後天、週末、未來會不會下雨、天氣如何時，必須調用此工具。嚴禁回覆指令叫使用者自己查。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "台灣的縣市或鄉鎮市區名稱，例如：台北、板橋、台中、台南、花蓮"
                    }
                },
                "required": ["location"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_latest_earthquake",
            "description": "取得台灣中央氣象署發布的最新一起顯著有感地震報告（包含發生時間、芮氏規模、震央位置、震源深度與各地最大震度）。當使用者詢問剛剛或最近是否有地震時必須調用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "可選填，地區名稱（例如：台灣）"
                    }
                }
            }
        }
    }
]


# ================= 內部資料庫與 CWA 查詢實作 =================

def _clean_val(val, default="未知"):
    if val is None or val == "" or str(val) in ["-99", "-99.0", "-999", "-999.0", "-990", "-990.0"]:
        return default
    return str(val).strip()

async def fetch_current_weather(session: aiohttp.ClientSession, api_key: str, location_input: str) -> dict:
    """查詢即時天氣觀測資料"""
    if not api_key:
        return {"error": "系統未設定 CWA_API_KEY，無法取得觀測資料。"}

    loc_val, error_msg = match_location(location_input)
    if error_msg:
        return {"error": error_msg}

    county_name = loc_val[:3]
    town_name = loc_val[3:]

    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001"
    headers = {"Authorization": api_key}

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return {"error": f"氣象署觀測 API 回傳 HTTP {resp.status}"}
            data = await resp.json()
    except Exception as e:
        logger.error(f"❌ [AI Tool] fetch_current_weather 失敗: {e!r}")
        return {"error": f"無法連線至氣象署觀測 API: {e!r}"}

    stations = data.get("records", {}).get("Station", [])
    target_stations = []
    for st in stations:
        geo = st.get("GeoInfo", {})
        if geo.get("CountyName") == county_name and geo.get("TownName") == town_name:
            target_stations.append(st)

    if not target_stations:
        return {"地點": f"{county_name}{town_name}", "狀態": "查無該鄉鎮之專屬自動測站資料"}

    st = target_stations[0]
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
        "查詢地點": f"{county_name}{town_name}",
        "代表測站": st_name,
        "觀測時間": obs_time,
        "天氣現象": _clean_val(we.get("Weather"), "良好"),
        "目前氣溫": f"{_clean_val(we.get('AirTemperature'))} °C",
        "今日最高溫": f"{_clean_val(high_temp)} °C" if high_temp else "暫無",
        "今日最低溫": f"{_clean_val(low_temp)} °C" if low_temp else "暫無",
        "相對濕度": f"{_clean_val(we.get('RelativeHumidity'))} %",
        "本日累積降雨": f"{_clean_val(precip, '0.0')} mm",
        "風向風速": f"{_clean_val(we.get('WindSpeed'))} m/s"
    }

async def fetch_weather_forecast(session: aiohttp.ClientSession, api_key: str, location_input: str) -> dict:
    """查詢未來鄉鎮天氣預報"""
    if not api_key:
        return {"error": "系統未設定 CWA_API_KEY，無法取得預報資料。"}

    loc_val, error_msg = match_location(location_input)
    if error_msg:
        return {"error": error_msg}

    county_name = loc_val[:3]
    town_name = loc_val[3:]
    location_id = COUNTY_LOCATION_ID.get(county_name)
    if not location_id:
        return {"error": f"找不到縣市代碼：{county_name}"}

    url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-093?locationId={location_id}&LocationName={town_name}&ElementName="
    headers = {"Authorization": api_key}

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return {"error": f"氣象署預報 API 回傳 HTTP {resp.status}"}
            data = await resp.json()
    except Exception as e:
        logger.error(f"❌ [AI Tool] fetch_weather_forecast 失敗: {e!r}")
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

        # 降雨機率
        pop_val = "0%"
        pop_times = elements.get("12小時降雨機率", {}).get("Time", [])
        for p in pop_times:
            if p.get("StartTime") == st:
                vals = p.get("ElementValue", [{}])
                if vals:
                    pop_val = f"{vals[0].get('ProbabilityOfPrecipitation', '0')}%"
                break

        # 氣溫
        temp_val = "未知"
        temp_times = elements.get("平均溫度", {}).get("Time", [])
        for t in temp_times:
            if t.get("StartTime") == st:
                vals = t.get("ElementValue", [{}])
                if vals:
                    temp_val = f"{vals[0].get('Temperature', '未知')}°C"
                break

        # 天氣描述
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

    return {
        "地點": f"{county_name}{town_name}",
        "預報時段清單": forecast_periods
    }

async def fetch_latest_earthquake(session: aiohttp.ClientSession, api_key: str) -> dict:
    """查詢最新有感地震資訊"""
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001?limit=1&format=JSON"
    headers = {"Authorization": api_key} if api_key else {}

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status != 200:
                return {"error": f"氣象署地震 API 回傳 HTTP {resp.status}"}
            data = await resp.json()
    except Exception as e:
        logger.error(f"❌ [AI Tool] fetch_latest_earthquake 失敗: {e!r}")
        return {"error": f"無法連線至氣象署地震 API: {e!r}"}

    records = data.get("records", {}).get("Earthquake", [])
    if not records:
        return {"地震報告": "近期無顯著有感地震報告"}

    eq = records[0]
    eq_info = eq.get("EarthquakeInfo", {})
    origin_time = eq_info.get("OriginTime", "未知時間")
    epicenter = eq_info.get("Epicenter", {}).get("Location", "未知地點")
    depth = eq_info.get("FocalDepth", "未知")
    mag = eq_info.get("EarthquakeMagnitude", {}).get("MagnitudeValue", "未知")
    report_content = eq.get("ReportContent", "")

    # 各地最大震度精簡摘要
    shaking_summary = []
    for area in eq.get("Intensity", {}).get("ShakingArea", []):
        area_desc = area.get("AreaDesc", "")
        max_int = area.get("AreaIntensity", "")
        if area_desc and max_int:
            shaking_summary.append(f"{area_desc}: {max_int}")
        if len(shaking_summary) >= 6:
            break

    return {
        "地震時間": origin_time,
        "震央位置": epicenter,
        "芮氏規模": mag,
        "震源深度": f"{depth} 公里",
        "報告內文": report_content,
        "各地顯著震度": "、".join(shaking_summary) if shaking_summary else "無詳細震度資訊"
    }


# ================= 統一執行器 =================

async def execute_tool(bot, tool_name: str, arguments: dict, cwa_api_key: str = None) -> str:
    """執行特定 Tool 並回傳 JSON 字串"""
    session = bot.session if getattr(bot, 'session', None) and not bot.session.closed else aiohttp.ClientSession()
    should_close_session = (session != getattr(bot, 'session', None))

    if not cwa_api_key:
        cfg = getattr(bot, 'config', None) or get_config()
        cwa_api_key = cfg.get('CWA_API_KEY')

    try:
        if tool_name == "get_current_weather":
            loc = arguments.get("location", "臺北市信義區")
            res = await fetch_current_weather(session, cwa_api_key, loc)
            return json.dumps(res, ensure_ascii=False)

        elif tool_name == "get_weather_forecast":
            loc = arguments.get("location", "臺北市信義區")
            res = await fetch_weather_forecast(session, cwa_api_key, loc)
            return json.dumps(res, ensure_ascii=False)

        elif tool_name == "get_latest_earthquake":
            res = await fetch_latest_earthquake(session, cwa_api_key)
            return json.dumps(res, ensure_ascii=False)

        else:
            return json.dumps({"error": f"未知工具：{tool_name}"}, ensure_ascii=False)

    except Exception as e:
        logger.error(f"❌ [AI Tool] 執行工具 {tool_name} 異常: {e!r}")
        return json.dumps({"error": f"執行工具失敗: {e!r}"}, ensure_ascii=False)

    finally:
        if should_close_session and not session.closed:
            await session.close()
