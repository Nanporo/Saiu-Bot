import json
import logging
import aiohttp
from modules.location_matcher import match_location
from modules.config import get_config
from modules.ai_cache import ai_cache

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
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_warnings",
            "description": "取得台灣目前中央氣象署發布的「即時氣象警特報」（包含大雨特報、豪雨特報、陸上強風特報、低溫特報、濃霧特報、高溫資訊）。當使用者詢問是否有特報、警報、會不會下大雨或吹強風時必須調用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "可選填，特定縣市名稱（例如：台北、新北、花蓮）或留空以查詢全台概況"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_air_quality",
            "description": "取得台灣指定縣市或地點的「空氣品質指標 (AQI)」與「PM2.5」細懸浮微粒資訊及健康建議。當使用者詢問空氣品質好不好、AQI、PM2.5、出門是否需要戴口罩時調用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "台灣的縣市或行政區名稱，例如：高雄、台北、台中、台南"
                    }
                },
                "required": ["location"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_extreme_climate",
            "description": "取得今日全台灣的「極端氣象紀錄排行」（包含今日全台最高溫測站、最低溫測站、本日累積雨量之最）。當使用者詢問今天全台哪裡最熱、哪裡最冷、哪裡雨下最大時調用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "description": "可選填，查詢類型（例如：最高溫、最低溫、降雨量、綜合）"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_typhoon_status",
            "description": "取得中央氣象署目前是否有發布「颱風警報」（海上或陸上警報）、最新颱風動態與警戒範圍。當使用者詢問是否有颱風、颱風會不會來、颱風警報狀態時調用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "可選填，詢問主題（例如：颱風動態、颱風警報）"
                    }
                }
            }
        }
    }
]


# ================= 內部查詢相容層（整合 AICache） =================

async def fetch_current_weather(session: aiohttp.ClientSession, api_key: str, location_input: str) -> dict:
    """查詢即時天氣觀測資料（透過 AICache 快取）"""
    return await ai_cache.get_current_weather(session, location_input, api_key=api_key)


async def fetch_weather_forecast(session: aiohttp.ClientSession, api_key: str, location_input: str) -> dict:
    """查詢未來鄉鎮天氣預報（透過 AICache 快取）"""
    return await ai_cache.get_weather_forecast(session, location_input, api_key=api_key)


async def fetch_latest_earthquake(session: aiohttp.ClientSession, api_key: str) -> dict:
    """查詢最新有感地震資訊（透過 AICache 快取）"""
    return await ai_cache.get_latest_earthquake(session, api_key=api_key)


# ================= 統一執行器 =================

async def execute_tool(bot, tool_name: str, arguments: dict, cwa_api_key: str = None) -> str:
    """執行特定 Tool 並回傳 JSON 字串（優先自 AI 快取中心獲取資料）"""
    session = bot.session if getattr(bot, 'session', None) and not bot.session.closed else aiohttp.ClientSession()
    should_close_session = (session != getattr(bot, 'session', None))

    if not cwa_api_key:
        cfg = getattr(bot, 'config', None) or get_config()
        cwa_api_key = cfg.get('CWA_API_KEY')

    try:
        if tool_name == "get_current_weather":
            loc = arguments.get("location", "臺北市信義區")
            res = await ai_cache.get_current_weather(session, loc, api_key=cwa_api_key)
            return json.dumps(res, ensure_ascii=False)

        elif tool_name == "get_weather_forecast":
            loc = arguments.get("location", "臺北市信義區")
            res = await ai_cache.get_weather_forecast(session, loc, api_key=cwa_api_key)
            return json.dumps(res, ensure_ascii=False)

        elif tool_name == "get_latest_earthquake":
            res = await ai_cache.get_latest_earthquake(session, api_key=cwa_api_key)
            return json.dumps(res, ensure_ascii=False)

        elif tool_name == "get_weather_warnings":
            loc = arguments.get("location", "")
            res = await ai_cache.get_weather_warnings(session, location_input=loc, api_key=cwa_api_key)
            return json.dumps(res, ensure_ascii=False)

        elif tool_name == "get_air_quality":
            loc = arguments.get("location", "高雄市")
            res = await ai_cache.get_air_quality(session, location_input=loc)
            return json.dumps(res, ensure_ascii=False)

        elif tool_name == "get_extreme_climate":
            res = await ai_cache.get_extreme_climate(session, api_key=cwa_api_key)
            return json.dumps(res, ensure_ascii=False)

        elif tool_name == "get_typhoon_status":
            res = await ai_cache.get_typhoon_status(session, api_key=cwa_api_key)
            return json.dumps(res, ensure_ascii=False)

        else:
            return json.dumps({"error": f"未知工具：{tool_name}"}, ensure_ascii=False)

    except Exception as e:
        logger.error(f"❌ [AI Tool] 執行工具 {tool_name} 異常: {e!r}")
        return json.dumps({"error": f"執行工具失敗: {e!r}"}, ensure_ascii=False)

    finally:
        if should_close_session and not session.closed:
            await session.close()
