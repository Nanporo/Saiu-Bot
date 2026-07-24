import discord
from discord.ext import commands, tasks
import logging
import time
import asyncio
from modules.database import get_all_settings
from modules.cache_manager import load_cache
from modules.config import get_config
from modules.cwa_api import (
    fetch_daily_extreme_temperatures,
    fetch_current_rainfall,
    fetch_current_wind
)

logger = logging.getLogger(__name__)

def get_beaufort_scale(speed: float) -> str:
    if speed < 0.3: return "0"
    elif speed < 1.6: return "1"
    elif speed < 3.4: return "2"
    elif speed < 5.5: return "3"
    elif speed < 8.0: return "4"
    elif speed < 10.8: return "5"
    elif speed < 13.9: return "6"
    elif speed < 17.2: return "7"
    elif speed < 20.8: return "8"
    elif speed < 24.5: return "9"
    elif speed < 28.5: return "10"
    elif speed < 32.7: return "11"
    elif speed < 37.0: return "12"
    elif speed < 41.5: return "13"
    elif speed < 46.2: return "14"
    elif speed < 51.0: return "15"
    elif speed < 56.1: return "16"
    else: return "17"

class Status(commands.Cog):
    """機器人 Discord 狀態 (Presence) 管理模組"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.current_status_text = None
        self.current_priority = None

        now = time.time()
        cache = load_cache()

        # Priority 1: EEW (3 分鐘)
        eew_until = cache.get("status_eew_until", 0.0)
        eew_text = cache.get("status_eew_text")
        if eew_text and eew_until > now:
            self.eew_text = eew_text
            self.eew_until = eew_until
        else:
            self.eew_text = None
            self.eew_until = 0.0

        # Priority 2: 地震報告 (15 分鐘)
        eq_until = cache.get("status_eq_report_until", 0.0)
        eq_text = cache.get("status_eq_report_text")
        if eq_text and eq_until > now:
            self.eq_report_text = eq_text
            self.eq_report_until = eq_until
        else:
            self.eq_report_text = None
            self.eq_report_until = 0.0

        # Priority 3: 大雷雨即時訊息 (持續至結束，多報每 30 秒輪播)
        saved_thunderstorms = cache.get("status_active_thunderstorms", [])
        self.active_thunderstorms = [
            a for a in saved_thunderstorms
            if isinstance(a, dict) and (a.get("end_timestamp", 0) > now or a.get("end_timestamp", 0) == 0)
        ]
        self.thunderstorm_carousel_index = cache.get("status_thunderstorm_carousel_index", 0)
        self.last_carousel_time = 0.0

        # Priority 4: 颱風警報與風雨極值輪播
        self.typhoon_text = cache.get("status_typhoon_text")
        self.typhoon_records = {}
        self.typhoon_records_last_update = 0.0
        self.typhoon_carousel_index = 0
        self.last_typhoon_carousel_time = 0.0
        self.fetching_typhoon_records = False

        # Priority 5 (平時動態輪播) 快取與輪播計時器
        self.idle_records = {}
        self.idle_records_last_update = 0.0
        self.idle_carousel_index = 0
        self.last_idle_carousel_time = 0.0
        self.fetching_idle_records = False

        if self.eew_text or self.eq_report_text or self.active_thunderstorms or self.typhoon_text:
            logger.info(
                f"💾 [狀態] 已從快取復原機器人狀態 (EEW: {self.eew_text}, 地震報告: {self.eq_report_text}, "
                f"大雷雨: {len(self.active_thunderstorms)} 則, 颱風: {self.typhoon_text})"
            )

        self.status_loop.start()

    def save_state(self) -> dict:
        """將 Status 的當前狀態與到期時間戳儲存為字典供快取管理員寫入硬碟"""
        now = time.time()

        has_valid_eew = bool(self.eew_text and self.eew_until > now)
        has_valid_eq = bool(self.eq_report_text and self.eq_report_until > now)

        valid_thunderstorms = [
            a for a in self.active_thunderstorms
            if isinstance(a, dict) and (a.get("end_timestamp", 0) > now or a.get("end_timestamp", 0) == 0)
        ]

        return {
            "status_eew_text": self.eew_text if has_valid_eew else None,
            "status_eew_until": self.eew_until if has_valid_eew else 0.0,
            "status_eq_report_text": self.eq_report_text if has_valid_eq else None,
            "status_eq_report_until": self.eq_report_until if has_valid_eq else 0.0,
            "status_active_thunderstorms": valid_thunderstorms,
            "status_thunderstorm_carousel_index": self.thunderstorm_carousel_index,
            "status_typhoon_text": self.typhoon_text,
        }

    def cog_unload(self):
        self.status_loop.cancel()

    def set_eew_alert(self, location: str, mag: float, duration: int = 180):
        """設定 EEW 強震即時警報動態狀態 (優先級 1，預設 3 分鐘)"""
        self.eew_text = f"地震速報：{location} M{mag}"
        self.eew_until = time.time() + duration
        logger.info(f"🤖 [狀態] 收到 EEW 速報連動：「{self.eew_text}」(持續 {duration} 秒)")

    def set_eq_report(self, location: str, mag: float, duration: int = 900):
        """設定地震報告動態狀態 (優先級 2，預設 15 分鐘)"""
        self.eq_report_text = f"地震報告：{location} M{mag}"
        self.eq_report_until = time.time() + duration
        logger.info(f"🤖 [狀態] 收到地震報告連動：「{self.eq_report_text}」(持續 {duration} 秒)")

    def update_thunderstorm_alerts(self, alerts: list):
        """更新大雷雨即時訊息列表 (優先級 3)"""
        self.active_thunderstorms = alerts
        if self.thunderstorm_carousel_index >= len(alerts):
            self.thunderstorm_carousel_index = 0

    def update_typhoon_alert(self, text: str | None):
        """更新颱風警報狀態 (優先級 4)"""
        self.typhoon_text = text

    async def _update_typhoon_records(self):
        """非同步抓取 CWA 颱風警報期間之現在最大雨量、現在最大風速與現在最大陣風"""
        if self.fetching_typhoon_records:
            return
        self.fetching_typhoon_records = True
        try:
            config = get_config()
            api_key = config.get("CWA_API_KEY")
            if not api_key or not getattr(self.bot, "session", None):
                return

            stations_rain, stations_wind = await asyncio.gather(
                fetch_current_rainfall(self.bot.session, api_key),
                fetch_current_wind(self.bot.session, api_key),
                return_exceptions=True
            )

            records = {}

            # 現在最大雨量
            if isinstance(stations_rain, list):
                max_r, max_r_loc = -1.0, None
                for st in stations_rain:
                    st_name = st.get('StationName', '')
                    geo_info = st.get('GeoInfo', {})
                    county = geo_info.get('CountyName', '')
                    town = geo_info.get('TownName', '')
                    loc = f"{county}{town}" if (county or town) else st_name

                    try:
                        r = float(st.get('RainfallElement', {}).get('Now', {}).get('Precipitation', '-99'))
                        if r > 0.0 and r > max_r:
                            max_r = r
                            max_r_loc = loc
                    except (ValueError, TypeError):
                        pass

                if max_r_loc and max_r > 0.0:
                    records["max_rain"] = (max_r_loc, max_r)

            # 現在最大風速與現在最大陣風
            if isinstance(stations_wind, list):
                max_w, max_w_loc = -1.0, None
                max_g, max_g_loc = -1.0, None

                for st in stations_wind:
                    st_name = st.get('StationName', '')
                    geo_info = st.get('GeoInfo', {})
                    county = geo_info.get('CountyName', '')
                    town = geo_info.get('TownName', '')
                    loc = f"{county}{town}" if (county or town) else st_name

                    weather = st.get('WeatherElement', {})

                    # 平均風速
                    try:
                        w_val = float(weather.get('WindSpeed', '-99'))
                        if w_val >= 0.0 and w_val > max_w:
                            max_w = w_val
                            max_w_loc = loc
                    except (ValueError, TypeError):
                        pass

                    # 陣風
                    g_info = weather.get('GustInfo', {})
                    try:
                        g_val = float(g_info.get('PeakGustSpeed', '-99'))
                        if g_val >= 0.0 and g_val > max_g:
                            max_g = g_val
                            max_g_loc = loc
                    except (ValueError, TypeError):
                        pass

                if max_w_loc and max_w >= 0.0:
                    bft_w = get_beaufort_scale(max_w)
                    records["max_wind"] = (max_w_loc, max_w, bft_w)

                if max_g_loc and max_g >= 0.0:
                    bft_g = get_beaufort_scale(max_g)
                    records["max_gust"] = (max_g_loc, max_g, bft_g)

            self.typhoon_records = records
            self.typhoon_records_last_update = time.time()
            if records:
                logger.info(
                    f"🌀 [狀態] 已更新颱風即時風雨極值 (雨量: {records.get('max_rain')}, "
                    f"風速: {records.get('max_wind')}, 陣風: {records.get('max_gust')})"
                )
        except Exception as e:
            logger.error(f"❌ [狀態] 更新颱風風雨極值快取失敗: {e}")
        finally:
            self.fetching_typhoon_records = False

    async def _update_idle_records(self):
        """非同步抓取 CWA 今日極溫與極大雨量觀測資料作為平時動態狀態"""
        if self.fetching_idle_records:
            return
        self.fetching_idle_records = True
        try:
            config = get_config()
            api_key = config.get("CWA_API_KEY")
            if not api_key or not getattr(self.bot, "session", None):
                return

            stations_obs, stations_rain = await asyncio.gather(
                fetch_daily_extreme_temperatures(self.bot.session, api_key),
                fetch_current_rainfall(self.bot.session, api_key),
                return_exceptions=True
            )

            records = {}
            if isinstance(stations_obs, list):
                max_t, max_t_loc = -999.0, None
                min_t, min_t_loc = 999.0, None
                for st in stations_obs:
                    st_name = st.get('StationName', '')
                    geo_info = st.get('GeoInfo', {})
                    county = geo_info.get('CountyName', '')
                    town = geo_info.get('TownName', '')
                    loc = f"{county}{town}" if (county or town) else st_name

                    weather = st.get('WeatherElement', {})
                    daily_high = weather.get('DailyHigh') or weather.get('DailyExtreme', {}).get('DailyHigh') or {}
                    daily_low = weather.get('DailyLow') or weather.get('DailyExtreme', {}).get('DailyLow') or {}

                    try:
                        t_h = float(daily_high.get('TemperatureInfo', {}).get('AirTemperature', '-99'))
                        if -90.0 < t_h < 60.0 and t_h > max_t:
                            max_t = t_h
                            max_t_loc = loc
                    except (ValueError, TypeError):
                        pass

                    try:
                        t_l = float(daily_low.get('TemperatureInfo', {}).get('AirTemperature', '99'))
                        if -90.0 < t_l < 60.0 and t_l < min_t:
                            min_t = t_l
                            min_t_loc = loc
                    except (ValueError, TypeError):
                        pass

                if max_t_loc and max_t > -90.0:
                    records["max_temp"] = (max_t_loc, max_t)
                if min_t_loc and min_t < 60.0:
                    records["min_temp"] = (min_t_loc, min_t)

            if isinstance(stations_rain, list):
                max_r, max_r_loc = -1.0, None
                for st in stations_rain:
                    st_name = st.get('StationName', '')
                    geo_info = st.get('GeoInfo', {})
                    county = geo_info.get('CountyName', '')
                    town = geo_info.get('TownName', '')
                    loc = f"{county}{town}" if (county or town) else st_name

                    try:
                        r = float(st.get('RainfallElement', {}).get('Now', {}).get('Precipitation', '-99'))
                        if r > 0.0 and r > max_r:
                            max_r = r
                            max_r_loc = loc
                    except (ValueError, TypeError):
                        pass

                if max_r_loc and max_r > 0.0:
                    records["max_rain"] = (max_r_loc, max_r)

            self.idle_records = records
            self.idle_records_last_update = time.time()
            if records:
                logger.info(
                    f"📊 [狀態] 已更新平時氣象極值快取 (最高溫: {records.get('max_temp')}, "
                    f"最低溫: {records.get('min_temp')}, 最大雨量: {records.get('max_rain')})"
                )
        except Exception as e:
            logger.error(f"❌ [狀態] 更新平時極值快取失敗: {e}")
        finally:
            self.fetching_idle_records = False

    @tasks.loop(seconds=5.0)
    async def status_loop(self):
        now = time.time()

        try:
            settings = get_all_settings()
        except Exception:
            settings = {}

        target_status = None
        current_priority = None

        # 優先級 1：EEW (強震即時警報)
        has_eew_enabled = any(d.get("eew_authorized", False) and d.get("eew_alerts") for d in settings.values())
        if has_eew_enabled and self.eew_text and now < self.eew_until:
            target_status = self.eew_text
            current_priority = 1

        # 優先級 2：地震報告
        if not target_status:
            has_eq_enabled = any('eq_alerts' in d and d['eq_alerts'] for d in settings.values())
            if has_eq_enabled and self.eq_report_text and now < self.eq_report_until:
                target_status = self.eq_report_text
                current_priority = 2

        # 優先級 3：大雷雨即時訊息
        if not target_status:
            has_rain_enabled = any(d.get('thunderstorm_alert') and d.get('rain_alerts') for d in settings.values())
            if has_rain_enabled:
                valid_ts = [
                    a for a in self.active_thunderstorms
                    if a.get("end_timestamp", 0) > now or a.get("end_timestamp", 0) == 0
                ]
                if valid_ts:
                    current_priority = 3
                    if len(valid_ts) == 1:
                        target_status = valid_ts[0]["text"]
                    else:
                        # 多報輪播：每 30 秒自動更新切換
                        if now - self.last_carousel_time >= 30.0:
                            self.thunderstorm_carousel_index = (self.thunderstorm_carousel_index + 1) % len(valid_ts)
                            self.last_carousel_time = now
                        else:
                            if self.thunderstorm_carousel_index >= len(valid_ts):
                                self.thunderstorm_carousel_index = 0
                        target_status = valid_ts[self.thunderstorm_carousel_index]["text"]

        # 優先級 4：颱風警報 (警報發布中 + 現在風雨極值輪播)
        if not target_status:
            has_typhoon_enabled = any('typhoon_alerts' in d and d['typhoon_alerts'] for d in settings.values())
            if has_typhoon_enabled and self.typhoon_text:
                current_priority = 4
                if now - self.typhoon_records_last_update >= 300.0:
                    asyncio.create_task(self._update_typhoon_records())

                typhoon_items = [self.typhoon_text]

                if "max_rain" in self.typhoon_records:
                    loc, rain = self.typhoon_records["max_rain"]
                    typhoon_items.append(f"現在最大雨量：{loc} {rain}mm")

                if "max_wind" in self.typhoon_records:
                    loc, speed, bft = self.typhoon_records["max_wind"]
                    typhoon_items.append(f"現在最大風速：{loc} {bft}級 ({speed}m/s)")

                if "max_gust" in self.typhoon_records:
                    loc, gust, bft = self.typhoon_records["max_gust"]
                    typhoon_items.append(f"現在最大陣風：{loc} {bft}級 ({gust}m/s)")

                if len(typhoon_items) == 1:
                    target_status = typhoon_items[0]
                else:
                    if now - self.last_typhoon_carousel_time >= 30.0:
                        self.typhoon_carousel_index = (self.typhoon_carousel_index + 1) % len(typhoon_items)
                        self.last_typhoon_carousel_time = now
                    else:
                        if self.typhoon_carousel_index >= len(typhoon_items):
                            self.typhoon_carousel_index = 0
                    target_status = typhoon_items[self.typhoon_carousel_index]

        # 優先級 5：平時動態輪播 (極值觀測 / 預設)
        if not target_status:
            current_priority = 5
            if now - self.idle_records_last_update >= 600.0:
                asyncio.create_task(self._update_idle_records())

            idle_items = ["Discord 天氣小助手"]

            if "max_temp" in self.idle_records:
                loc, temp = self.idle_records["max_temp"]
                idle_items.append(f"今日最高溫：{loc} {temp}°C")

            if "min_temp" in self.idle_records:
                loc, temp = self.idle_records["min_temp"]
                idle_items.append(f"今日最低溫：{loc} {temp}°C")

            if "max_rain" in self.idle_records:
                loc, rain = self.idle_records["max_rain"]
                idle_items.append(f"今日最大雨量：{loc} {rain}mm")

            if now - self.last_idle_carousel_time >= 30.0:
                self.idle_carousel_index = (self.idle_carousel_index + 1) % len(idle_items)
                self.last_idle_carousel_time = now
            else:
                if self.idle_carousel_index >= len(idle_items):
                    self.idle_carousel_index = 0

            target_status = idle_items[self.idle_carousel_index]

        # 若優先級發生切換變更，輸出 console 日誌
        if current_priority != self.current_priority:
            priority_names = {
                1: "P1 (EEW 強震即時警報)",
                2: "P2 (地震報告)",
                3: "P3 (大雷雨即時訊息)",
                4: "P4 (颱風警報與風雨極值)",
                5: "P5 (平時動態狀態)"
            }
            p_name = priority_names.get(current_priority, f"P{current_priority}")
            logger.info(f"🤖 [狀態] 機器人狀態優先級切換至 【{p_name}】，當前狀態：「{target_status}」")
            self.current_priority = current_priority

        # 狀態發生改變時更新 Discord Presence (輪播期間靜默更新)
        if target_status != self.current_status_text:
            try:
                activity = discord.CustomActivity(name=target_status)
                await self.bot.change_presence(activity=activity)
                self.current_status_text = target_status
            except Exception as e:
                logger.error(f"❌ [狀態] 設定狀態時發生錯誤: {e}")

    @status_loop.before_loop
    async def before_status_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(Status(bot))

