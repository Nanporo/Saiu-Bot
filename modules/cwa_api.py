import aiohttp
import logging

logger = logging.getLogger(__name__)

async def fetch_current_temperatures(session: aiohttp.ClientSession, api_key: str):
    """
    呼叫 O-A0001-001 API 抓取各測站當前氣溫。
    回傳字典：鄉鎮市區名稱 -> [氣溫列表]
    """
    url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?Authorization={api_key}&WeatherElement=AirTemperature"
    town_temps = {}
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                stations = data.get('records', {}).get('Station', [])
                
                for st in stations:
                    geo_info = st.get('GeoInfo', {})
                    county = geo_info.get('CountyName', '')
                    town = geo_info.get('TownName', '')
                    location_name = f"{county}{town}"

                    temp_val = st.get('WeatherElement', {}).get('AirTemperature', -99.0)
                    try:
                        temp_val = float(temp_val)
                    except ValueError:
                        continue

                    # 排除無效值 (-99.0 等)
                    if temp_val <= -90.0:
                        continue

                    town_temps.setdefault(location_name, []).append(temp_val)
    except Exception as e:
        logger.error(f"⚠️ [API] 抓取氣溫資料發生錯誤: {e}")
        
    return town_temps

async def fetch_daily_extreme_temperatures(session: aiohttp.ClientSession, api_key: str):
    """
    呼叫 O-A0001-001 API 抓取各測站今日最高溫與最低溫。
    回傳列表：包含各測站氣象資訊的字典列表
    """
    url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?Authorization={api_key}&WeatherElement=DailyHigh&WeatherElement=DailyLow"
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return data.get('records', {}).get('Station', [])
            else:
                logger.warning(f"⚠️ [API] 抓取極端氣溫資料失敗，狀態碼: {response.status}")
    except Exception as e:
        logger.error(f"⚠️ [API] 抓取極端氣溫資料發生錯誤: {e}")
        
    return []

async def fetch_current_rainfall(session: aiohttp.ClientSession, api_key: str):
    """
    呼叫 O-A0002-001 API 抓取各測站當前累積雨量。
    回傳列表：包含各測站雨量資訊的字典列表
    """
    url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001?Authorization={api_key}&RainfallElement=Now"
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return data.get('records', {}).get('Station', [])
            else:
                logger.warning(f"⚠️ [API] 抓取雨量資料失敗，狀態碼: {response.status}")
    except Exception as e:
        logger.error(f"⚠️ [API] 抓取雨量資料發生錯誤: {e}")
        
    return []