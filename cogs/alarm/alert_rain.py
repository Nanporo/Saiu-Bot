import io
import math
import asyncio
from PIL import Image, ImageDraw, ImageFont
import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import json
import re
import os
import time
from datetime import datetime, timezone, timedelta
from geopy.geocoders import Nominatim
from modules.town_mapping import load_town_mapping
from modules.cwa_api import fetch_current_rainfall
from modules.database import get_all_settings
from modules.cache_manager import load_cache
import logging

logger = logging.getLogger(__name__)

# 這個模組會自動預警1小時後即將有雨的區域，手動的是 cogs/rain_manual.py

class RainForecastCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.geolocator = Nominatim(user_agent="Saiu-Bot-Rain-Alert")
        cache = load_cache()
        self.alert_status = cache.get("rain_status", {})  # 紀錄伺服器目前是否已發送過預警
        self.thunderstorm_notified_ids = set(cache.get("thunderstorm_notified_ids", []))  # 已通知過的大雷雨訊息 ID
        self.thunderstorm_suppress_until = {}  # 大雷雨期間抑制降雨預警：{"guild_id_loc_name": timestamp}
        self.latest_rain_data = []  # 供手動查詢使用的快取資料
        self.town_mapping = load_town_mapping()
        self.town_grid_masks = self.load_grid_masks()
        self.last_rain_status = None
        self.last_thunderstorm_status = None
        self.check_rain_loop.start()
        self.check_thunderstorm_loop.start()

    def load_grid_masks(self):
        """載入鄉鎮 GeoJSON 精準網格遮罩對照表"""
        try:
            with open('maps/town_grid_masks.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"⚠️ 無法載入 maps/town_grid_masks.json: {e}")
            return {}

    def save_state(self):
        return {
            "rain_status": self.alert_status,
            "thunderstorm_notified_ids": list(self.thunderstorm_notified_ids)
        }

    def get_api_key(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f).get('CWA_API_KEY', '')
        except Exception:
            return ''

    def cog_unload(self):
        self.check_rain_loop.cancel()
        self.check_thunderstorm_loop.cancel()

    async def get_location_grid(self, location: str):
        """[共用模組] 將地名轉換為 QPESUMS 的網格 X, Y"""
        # 統一處理「台」與「臺」，避免查詢差異
        location = location.replace("台", "臺")

        lat, lon = None, None

        # 比對字典：優先嘗試從字典中尋找完全符合的組合 (支援「屏東九如」、「臺南永康」等)
        if location in self.town_mapping:
            matches = self.town_mapping[location]
            if len(matches) == 1:
                location = matches[0][0]  # 唯一配對，自動補全全名
                lat = matches[0][1]
                lon = matches[0][2]
            else:
                options = "、".join([m[0] for m in matches])
                return None, f"❌ 「{location}」有符合多個地點 ({options})，請提供更完整的名稱。"

        if not lat or not lon:
            # 若不在字典或無坐標，交由 Nominatim 查詢 (如：僅輸入高雄市)
            if "縣" not in location and "市" not in location:
                return None, "❌ 為了精準定位，請提供包含「縣市」的完整名稱（例如：臺北市信義區、高雄市）。"
            try:
                loc_data = self.geolocator.geocode(f"臺灣 {location}", timeout=10.0)
            except Exception:
                return None, "⚠️ 定位服務目前無回應或發生錯誤，請稍後再試。"

            if not loc_data:
                return None, f"❌ 找不到「{location}」的座標，請嘗試提供更完整的名稱（如：臺中市大安區）。"
            lat, lon = loc_data.latitude, loc_data.longitude

        # 將經緯度轉換為 QPESUMS 的網格 X, Y (解析度 0.0125，左下角起始點 117.975, 19.975)
        grid_x = int(round((lon - 117.975) / 0.0125))
        grid_y = int(round((lat - 19.975) / 0.0125))

        if not (0 <= grid_x < 441 and 0 <= grid_y < 561):
            return None, "❌ 該地點似乎不在台灣的雷達網格預報範圍內。"

        return (grid_x, grid_y), location

    def _get_max_rain(self, values, grid_x, grid_y, radius=3, loc_name=None):
        """取得指定鄉鎮（GeoJSON 精準網格遮罩）或指定網格及其周邊的最大降雨量"""
        if loc_name:
            loc_name_clean = loc_name.replace("台", "臺")
            if loc_name_clean in self.town_grid_masks:
                indices = self.town_grid_masks[loc_name_clean]
                max_val = 0.0
                for idx in indices:
                    if idx < len(values):
                        v = values[idx].strip()
                        if v:
                            try:
                                val = float(v)
                                if val > max_val and val >= 0.0:
                                    max_val = val
                            except ValueError:
                                pass
                return max_val

        # 保底：若無 loc_name 或不在遮罩表中，採用周邊網格 (預設 7x7) 掃描
        max_val = 0.0
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                nx = grid_x + dx
                ny = grid_y + dy
                if 0 <= nx < 441 and 0 <= ny < 561:
                    idx = ny * 441 + nx
                    if idx < len(values):
                        v = values[idx].strip()
                        if v:
                            try:
                                val = float(v)
                                if val > max_val and val >= 0.0:
                                    max_val = val
                            except ValueError:
                                pass
        return max_val

    def _get_actual_rain_str(self, current_rainfall_data, loc_name: str) -> str:
        """取得今日實測累積雨量字串"""
        actual_rain = 0.0
        if current_rainfall_data:
            for st in current_rainfall_data:
                geo_info = st.get('GeoInfo', {})
                if f"{geo_info.get('CountyName', '')}{geo_info.get('TownName', '')}" == loc_name:
                    try:
                        val = float(st.get('RainfallElement', {}).get('Now', {}).get('Precipitation', '-99'))
                        if val > actual_rain:
                            actual_rain = val
                    except ValueError:
                        pass
        
        if actual_rain > 0:
            actual_icon = "💧"
            if actual_rain >= 350.0:
                actual_icon = "🟣"
            elif actual_rain >= 200.0:
                actual_icon = "🔴"
            elif actual_rain >= 100.0:
                actual_icon = "🟠"
            elif actual_rain >= 40.0:
                actual_icon = "🟡"
            return f"\n今日實測累積雨量：`{actual_icon} {actual_rain} mm`"
        return f"\n今日實測累積雨量：`無資料或尚無降雨`"

    async def fetch_rain_value(self, grid_x: int, grid_y: int, loc_name: str = None):
        """[共用模組] 抓取指定地點/網格的降雨量，優先使用快取與精準遮罩"""
        if self.latest_rain_data:
            return self._get_max_rain(self.latest_rain_data, grid_x, grid_y, loc_name=loc_name), None

        api_key = self.get_api_key()
        if not api_key:
            return None, "⚠️ 未設定 API Key，無法查詢資料。"

        url = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-B0046-001?downloadType=WEB&format=JSON"
        headers = {"Authorization": api_key}
        try:
            async with self.bot.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    dataset = data['cwaopendata']['dataset']
                    values = dataset['contents']['content'].split(',')
                    self.latest_rain_data = values
                    return self._get_max_rain(values, grid_x, grid_y, loc_name=loc_name), None
                return None, "⚠️ 獲取資料失敗"
        except Exception as e:
            return None, str(e)

    async def generate_rain_map(self):
        """[共用模組] 繪製並生成 1小時網格降雨視覺化地圖 (PNG BytesIO)"""
        if not self.latest_rain_data:
            await self.fetch_rain_value(0, 0)
        
        if not self.latest_rain_data:
            return None
            
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, render_rain_grid_map_pil, self.latest_rain_data, self.town_mapping)

    @tasks.loop(minutes=9.0)
    async def check_rain_loop(self):
        api_key = self.get_api_key()
        if not api_key:
            return

        try:
            settings = get_all_settings()
        except Exception:
            return

        url = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-B0046-001?downloadType=WEB&format=JSON"
        headers = {"Authorization": api_key}
        try:
            async with self.bot.session.get(url, headers=headers) as response:
                if response.status == 200:
                    if self.last_rain_status not in (None, 200):
                        logger.info("✅ [雨量預警] API 已恢復正常連線 (狀態碼: 200)")
                    self.last_rain_status = 200
                    data = await response.json(content_type=None)
                    dataset = data['cwaopendata']['dataset']
                    values = dataset['contents']['content'].split(',')
                    self.latest_rain_data = values  # 更新快取
                    
                    current_rainfall_data = None
                    fetched_rainfall = False

                    for guild_id, d in settings.items():
                        global_silent = d.get('global_silent', False)
                        alerts = d.get('rain_alerts', {})
                            
                        for loc_name, alert_info in alerts.items():
                            # 略過因早期 bug 造成的損壞資料 (缺少 grid_x / 格式不為 dict)
                            if not isinstance(alert_info, dict) or 'grid_x' not in alert_info:
                                continue

                            status_key = f"{guild_id}_{loc_name}"

                            rain_val = self._get_max_rain(values, alert_info['grid_x'], alert_info['grid_y'], loc_name=loc_name)
                            
                            min_rainfall = float(alert_info.get('min_rainfall', 1.0))
                            
                            current_threshold = 0.0
                            icon = "💧"
                            if rain_val >= 350.0:
                                current_threshold = 350.0
                                icon = "🟣"
                            elif rain_val >= 200.0:
                                current_threshold = 200.0
                                icon = "🔴"
                            elif rain_val >= 100.0:
                                current_threshold = 100.0
                                icon = "🟠"
                            elif rain_val >= 40.0:
                                current_threshold = 40.0
                                icon = "🟡"
                            elif rain_val >= 20.0:
                                current_threshold = 20.0
                                icon = "💧"
                            elif rain_val >= 1.0:
                                current_threshold = 1.0
                                icon = "💧"

                            if rain_val < min_rainfall:
                                current_threshold = 0.0

                            # 檢查是否在大雷雨即時訊息的抑制期間內
                            suppress_key = f"{guild_id}_{loc_name}"
                            suppress_until = self.thunderstorm_suppress_until.get(suppress_key, 0.0)
                            if time.time() < suppress_until:
                                # 在大雷雨期間，如果預估雨量超過80mm，就使用較大的數值做記錄；否則強制鎖定在80mm
                                record_threshold = max(80.0, current_threshold)
                                self.alert_status[status_key] = {
                                    "threshold": record_threshold,
                                    "cooldown_until": suppress_until
                                }
                                continue

                            feels_like = ""
                            if current_threshold > 0.0:
                                if rain_val >= 10.0:
                                    feels_like = "大雨"
                                elif rain_val >= 2.5:
                                    feels_like = "中雨"
                                elif rain_val >= 1.0:
                                    feels_like = "小雨"
                                else:
                                    feels_like = "毛毛雨"

                            prev_data = self.alert_status.get(status_key, {})
                            if isinstance(prev_data, (float, int)):
                                prev_threshold = float(prev_data)
                                cooldown_until = 0.0
                            elif isinstance(prev_data, bool):
                                prev_threshold = 1.0 if prev_data else 0.0
                                cooldown_until = 0.0
                            else:
                                prev_threshold = prev_data.get('threshold', 0.0)
                                cooldown_until = prev_data.get('cooldown_until', 0.0)

                            current_time = time.time()
                            is_cooling_down = current_time < cooldown_until

                            in_quiet_hours = False
                            if 'notify_hours' in alert_info:
                                now_tw = datetime.now(timezone(timedelta(hours=8)))
                                if now_tw.hour not in alert_info['notify_hours']:
                                    in_quiet_hours = True
                            else:
                                # 向下相容舊版的起訖時間設定
                                notify_start = alert_info.get('notify_start', '00:00')
                                notify_end = alert_info.get('notify_end', '23:59')
                                if notify_start != '00:00' or notify_end != '23:59':
                                    now_tw = datetime.now(timezone(timedelta(hours=8)))
                                    current_hm = now_tw.strftime('%H:%M')
                                    if notify_start <= notify_end:
                                        if not (notify_start <= current_hm <= notify_end):
                                            in_quiet_hours = True
                                    else:
                                        if not (current_hm >= notify_start or current_hm <= notify_end):
                                            in_quiet_hours = True

                            # 冷卻期內除非雨勢變大，否則進行任何門檻調整動作
                            if is_cooling_down and not (current_threshold > prev_threshold):
                                self.alert_status[status_key] = {
                                    "threshold": prev_threshold,
                                    "cooldown_until": cooldown_until
                                }
                                continue

                            # 處理通知邏輯
                            if current_threshold > 0.0:
                                if prev_threshold == 0.0:
                                    # 1. 即將開始下雨：發送「降雨預警」，並進入冷卻
                                    if not in_quiet_hours:
                                        channel = self.bot.get_channel(alert_info['channel_id'])
                                        if channel:
                                            if not fetched_rainfall:
                                                try:
                                                    current_rainfall_data = await fetch_current_rainfall(self.bot.session, api_key)
                                                except Exception as e:
                                                    logger.warning(f"⚠️ [降雨預報] 獲取實測雨量失敗: {e!r}")
                                                fetched_rainfall = True

                                            actual_rain_str = self._get_actual_rain_str(current_rainfall_data, loc_name)
                                            message_content = "🌧️ 降雨預警通知"
                                            mention_role_id = d.get('rain_mention_role_id')
                                            if mention_role_id:
                                                message_content += f" <@&{mention_role_id}>"
                                            embed = discord.Embed(
                                                title="",
                                                description=f"**{loc_name}** 未來 1 小時內預測將有降雨發生！\n預估累積雨量：`{icon} {rain_val} mm ({feels_like})`{actual_rain_str}",
                                                color=discord.Color.blue()
                                            )
                                            if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                                                logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                                            else:
                                                await channel.send(content=message_content, embed=embed, silent=global_silent)
                                            guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                                            logger.info(f"📢 [降雨預報] 已發送預警 至 {guild_name} ({channel.name}) - {loc_name} (預估雨量: {rain_val} mm)")
                                    
                                    cooldown_seconds = alert_info.get('cooldown_time', 7200)
                                    self.alert_status[status_key] = {
                                        "threshold": current_threshold,
                                        "cooldown_until": current_time + cooldown_seconds
                                    }

                                elif current_threshold > prev_threshold:
                                    # 2. 雨勢變大：發送「雨勢變大通知」，並重新計算冷卻
                                    if not in_quiet_hours:
                                        channel = self.bot.get_channel(alert_info['channel_id'])
                                        if channel:
                                            if not fetched_rainfall:
                                                try:
                                                    current_rainfall_data = await fetch_current_rainfall(self.bot.session, api_key)
                                                except Exception as e:
                                                    logger.warning(f"⚠️ [降雨預報] 獲取實測雨量失敗: {e!r}")
                                                fetched_rainfall = True

                                            actual_rain_str = self._get_actual_rain_str(current_rainfall_data, loc_name)
                                            message_content = "🌧️ 雨勢變大通知"
                                            mention_role_id = d.get('rain_mention_role_id')
                                            if mention_role_id:
                                                message_content += f" <@&{mention_role_id}>"
                                            embed = discord.Embed(
                                                title="",
                                                description=f"**{loc_name}** 未來 1 小時內的預測雨勢將進一步增強！\n預估累積雨量：`{icon} {rain_val} mm ({feels_like})`{actual_rain_str}",
                                                color=discord.Color.orange()
                                            )
                                            if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                                                logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                                            else:
                                                await channel.send(content=message_content, embed=embed, silent=global_silent)
                                            guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                                            logger.info(f"📢 [降雨預報] 已發送變大通知 至 {guild_name} ({channel.name}) - {loc_name} (預估雨量: {rain_val} mm)")
                                    
                                    cooldown_seconds = alert_info.get('cooldown_time', 7200)
                                    self.alert_status[status_key] = {
                                        "threshold": current_threshold,
                                        "cooldown_until": current_time + cooldown_seconds
                                    }

                                else:
                                    # 3. 雨勢變小但還在下：不動作
                                    self.alert_status[status_key] = {
                                        "threshold": prev_threshold,  # 保持最高門檻，避免上下震盪觸發變大通知
                                        "cooldown_until": cooldown_until
                                    }

                            else:
                                # 雨停了 (current_threshold == 0.0)
                                if prev_threshold > 0.0:
                                    # 5. 雨停了，且已過冷卻（冷卻內的已在上方 continue）：發送降雨趨緩通知
                                    if not in_quiet_hours:
                                        channel = self.bot.get_channel(alert_info['channel_id'])
                                        if channel:
                                            message_content = "🌤️ 降雨趨緩通知"
                                            embed = discord.Embed(
                                                title="",
                                                description=f"**{loc_name}** 未來 1 小時內的雨勢預計將會趨緩或停止！",
                                                color=discord.Color.green()
                                            )
                                            if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                                                logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                                            else:
                                                await channel.send(content=message_content, embed=embed, silent=True)
                                            guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                                            logger.info(f"📢 [降雨預報] 已發送趨緩通知 至 {guild_name} ({channel.name}) - {loc_name}")
                                    
                                    self.alert_status[status_key] = {
                                        "threshold": 0.0,
                                        "cooldown_until": 0.0  # 解除冷卻
                                    }
                                else:
                                    # 原本就沒下雨，且現在也沒下雨
                                    self.alert_status[status_key] = {
                                        "threshold": 0.0,
                                        "cooldown_until": cooldown_until
                                    }

        except Exception as e:
            logger.error(f"⚠️ [降雨預報] 檢查時發生錯誤: {e!r}")

    @check_rain_loop.before_loop
    async def before_check_rain(self):
        await self.bot.wait_until_ready()

    # ==================== 大雷雨即時訊息 ====================

    def parse_warning_js(self, text: str) -> list:
        """解析 Warning_Content.js，提取 WarnContent_W33 陣列"""
        # 用正則表達式提取 WarnContent_W33 = [...]; 的內容
        match = re.search(r'var\s+WarnContent_W33\s*=\s*(\[.*?\]);', text, re.DOTALL)
        if not match:
            return []

        raw = match.group(1)

        # 將 JS 物件語法轉為合法 JSON
        # 1. 單引號 → 雙引號
        raw = raw.replace("'", '"')
        # 2. 處理無引號的 key（如 Town:50）→ "Town":50
        raw = re.sub(r'(?<=[{,])\s*(\w+)\s*:', r' "\1":', raw)
        # 3. 移除尾端逗號 (trailing commas)
        raw = re.sub(r',\s*([}\]])', r'\1', raw)

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ [大雷雨] 解析 WarnContent_W33 JSON 失敗: {e}")
            return []

    def _match_thunderstorm_area(self, warn_areas: list, loc_name: str) -> bool:
        """檢查大雷雨警戒區域是否包含指定地點"""
        for area in warn_areas:
            county = area.get('County', '')
            towns_str = area.get('Town', '')

            # 若使用者設定的地點名稱是「縣市+鄉鎮」格式（如「臺北市信義區」）
            if loc_name.startswith(county):
                # 取出鄉鎮部分
                town_part = loc_name[len(county):]
                if not town_part:
                    # 僅設定縣市（如「臺北市」），只要縣市匹配就算
                    return True
                # 鄉鎮比對：Town 欄位以頓號分隔
                towns = [t.strip() for t in towns_str.split('、') if t.strip()]
                if town_part in towns:
                    return True
        return False

    async def fetch_active_thunderstorms(self) -> list:
        """獲取當前有效的地區大雷雨即時訊息列表 (供 status.py 連動與補抓)"""
        if not self.bot.session:
            return []
        url = "https://www.cwa.gov.tw/Data/js/warn/Warning_Content.js"
        try:
            async with self.bot.session.get(url) as response:
                if response.status != 200:
                    return []
                text = await response.text()
        except Exception:
            return []

        warnings = self.parse_warning_js(text)
        if not warnings:
            return []

        now_tw = datetime.now(timezone(timedelta(hours=8)))
        active_alerts = []

        for warn in warnings:
            warn_areas = warn.get('WarnArea', [])
            title = warn.get('Title', '')
            desc_main = warn.get('Description', {}).get('Main', '')

            end_time_match = re.search(r'持續時間至(\d+)時(\d+)分', title)
            end_timestamp = 0.0

            if end_time_match:
                end_h = int(end_time_match.group(1))
                end_m = int(end_time_match.group(2))
                date_match = re.search(r'(\d+)年(\d+)月(\d+)日', desc_main)
                if date_match:
                    roc_year = int(date_match.group(1))
                    month = int(date_match.group(2))
                    day = int(date_match.group(3))
                    year = roc_year + 1911
                    try:
                        end_dt = now_tw.replace(year=year, month=month, day=day, hour=end_h, minute=end_m, second=0, microsecond=0)
                        start_time_match = re.search(r'(\d+)時(\d+)分氣象署發布', desc_main)
                        if start_time_match and end_h < int(start_time_match.group(1)):
                            end_dt += timedelta(days=1)
                        end_timestamp = end_dt.timestamp()
                    except ValueError:
                        pass
                if not end_timestamp:
                    end_dt = now_tw.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
                    if end_dt < now_tw:
                        end_dt += timedelta(days=1)
                    end_timestamp = end_dt.timestamp()

            if end_timestamp > 0 and now_tw.timestamp() > end_timestamp:
                continue

            counties = []
            for area in warn_areas:
                c = area.get('County', '').strip()
                if c and c not in counties:
                    counties.append(c)

            if counties:
                counties_str = "、".join(counties)
                active_alerts.append({
                    "text": f"大雷雨即時訊息：{counties_str}",
                    "end_timestamp": end_timestamp
                })

        return active_alerts

    @tasks.loop(minutes=5.0)
    async def check_thunderstorm_loop(self):
        """定期檢查大雷雨即時訊息並發送通知"""
        try:
            settings = get_all_settings()
        except Exception:
            return

        # 檢查是否有任何伺服器啟用了大雷雨通知
        has_any = False
        for guild_id, d in settings.items():
            if d.get('thunderstorm_alert') and d.get('rain_alerts'):
                has_any = True
                break

        # 更新 status.py (若有伺服器開啟功能)
        status_cog = self.bot.get_cog("Status")
        if status_cog and hasattr(status_cog, "update_thunderstorm_alerts"):
            if has_any:
                active_alerts = await self.fetch_active_thunderstorms()
                status_cog.update_thunderstorm_alerts(active_alerts)
            else:
                status_cog.update_thunderstorm_alerts([])

        if not has_any:
            return

        # 抓取 JS 檔案
        url = "https://www.cwa.gov.tw/Data/js/warn/Warning_Content.js"
        try:
            async with self.bot.session.get(url) as response:
                if response.status != 200:
                    if self.last_thunderstorm_status != response.status:
                        logger.warning(f"🌐 [大雷雨] 抓取 Warning_Content.js 狀態碼: {response.status}")
                        self.last_thunderstorm_status = response.status
                    return
                if self.last_thunderstorm_status not in (None, 200):
                    logger.info("✅ [大雷雨] 抓取 Warning_Content.js 已恢復正常連線 (狀態碼: 200)")
                self.last_thunderstorm_status = 200
                text = await response.text()
        except Exception as e:
            err_str = f"EXC_{type(e).__name__}"
            if self.last_thunderstorm_status != err_str:
                logger.warning(f"⚠️ [大雷雨] 抓取 Warning_Content.js 失敗: {e!r}")
                self.last_thunderstorm_status = err_str
            return

        warnings = self.parse_warning_js(text)
        if not warnings:
            # 若陣列為空，清理過期的已通知 ID（保留最近 6 小時內的避免重啟後重發）
            now = time.time()
            # 無法判斷過期時間，僅在有資料時才清理
            return

        now_tw = datetime.now(timezone(timedelta(hours=8)))

        for warn in warnings:
            warn_id = warn.get('ID', '')
            if not warn_id:
                continue

            warn_areas = warn.get('WarnArea', [])
            title = warn.get('Title', '')
            desc_main = warn.get('Description', {}).get('Main', '')
            desc_land = warn.get('Description', {}).get('Land', '')
            instruction_land = warn.get('Instruction', {}).get('Land', '')
            img_file = warn.get('ImgFile', '')

            # === 解析大雷雨過期時間 ===
            end_time_match = re.search(r'持續時間至(\d+)時(\d+)分', title)
            end_time_str = ""
            end_timestamp = 0.0

            if end_time_match:
                end_h = int(end_time_match.group(1))
                end_m = int(end_time_match.group(2))
                end_time_str = f"{end_h}時{end_m}分"
                
                # 嘗試從 Description.Main 提取精確日期，例如：115年07月19日16時51分...
                date_match = re.search(r'(\d+)年(\d+)月(\d+)日', desc_main)
                
                if date_match:
                    roc_year = int(date_match.group(1))
                    month = int(date_match.group(2))
                    day = int(date_match.group(3))
                    year = roc_year + 1911
                    
                    try:
                        end_dt = now_tw.replace(year=year, month=month, day=day, hour=end_h, minute=end_m, second=0, microsecond=0)
                        
                        # 處理結束時間跨午夜：如果結束時分小於發布時分，則結束時間是隔天
                        start_time_match = re.search(r'(\d+)時(\d+)分氣象署發布', desc_main)
                        if start_time_match:
                            start_h = int(start_time_match.group(1))
                            if end_h < start_h:
                                end_dt += timedelta(days=1)
                                
                        end_timestamp = end_dt.timestamp()
                    except ValueError:
                        pass
                        
                if not end_timestamp:
                    # 備案邏輯 (相容舊版或解析失敗)
                    end_dt = now_tw.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
                    if end_dt < now_tw:
                        end_dt += timedelta(days=1)
                    end_timestamp = end_dt.timestamp()

            # 如果該則大雷雨即時訊息已過期，就完全忽略不發送 (避免重啟後洗版)
            if end_timestamp > 0 and now_tw.timestamp() > end_timestamp:
                continue

            for guild_id, d in settings.items():
                if not d.get('thunderstorm_alert'):
                    continue

                global_silent = d.get('global_silent', False)
                alerts = d.get('rain_alerts', {})
                if not alerts:
                    continue

                # 收集該伺服器中匹配到的地點及其詳細資訊
                matched_locations = []  # [(loc_name, alert_info)]
                for loc_name, alert_info in alerts.items():
                    if not isinstance(alert_info, dict) or 'channel_id' not in alert_info:
                        continue
                    if self._match_thunderstorm_area(warn_areas, loc_name):
                        matched_locations.append((loc_name, alert_info))

                if not matched_locations:
                    continue

                # 使用 guild_id + warn_id 作為唯一鍵，避免跨伺服器混淆
                notify_key = f"{guild_id}_{warn_id}"
                if notify_key in self.thunderstorm_notified_ids:
                    continue

                # (過期時間已在迴圈外解析完畢，此處直接使用 end_timestamp 和 end_time_str)

                # 取得實測雨量資料（整個伺服器只抓一次）
                api_key = self.get_api_key()
                current_rainfall_data = None
                if api_key:
                    try:
                        current_rainfall_data = await fetch_current_rainfall(self.bot.session, api_key)
                    except Exception as e:
                        logger.warning(f"⚠️ [大雷雨] 獲取實測雨量失敗: {e!r}")

                # 發送通知：每個匹配地點各發一則
                for loc_name, alert_info in matched_locations:
                    channel = self.bot.get_channel(alert_info['channel_id'])
                    if not channel:
                        continue

                    message_content = "⛈️ 大雷雨即時訊息"
                    mention_role_id = d.get('rain_mention_role_id')
                    if mention_role_id:
                        message_content += f" <@&{mention_role_id}>"

                    # 預估累積雨量（使用 QPESUMS 網格資料）
                    rain_val_str = ""
                    if self.latest_rain_data and 'grid_x' in alert_info:
                        rain_val = self._get_max_rain(self.latest_rain_data, alert_info['grid_x'], alert_info['grid_y'], loc_name=loc_name)
                        if rain_val > 0:
                            icon = "💧"
                            if rain_val >= 350.0:
                                icon = "🟣"
                            elif rain_val >= 200.0:
                                icon = "🔴"
                            elif rain_val >= 100.0:
                                icon = "🟠"
                            elif rain_val >= 40.0:
                                icon = "🟡"
                            rain_val_str = f"\n預估累積雨量：`{icon} {rain_val} mm`"
                        else:
                            rain_val_str = "\n預估累積雨量：`無資料`"
                    else:
                        rain_val_str = "\n預估累積雨量：`無資料`"

                    # 今日實測累積雨量
                    actual_rain_str = ""
                    if current_rainfall_data:
                        actual_rain = 0.0
                        for st in current_rainfall_data:
                            geo_info = st.get('GeoInfo', {})
                            if f"{geo_info.get('CountyName', '')}{geo_info.get('TownName', '')}" == loc_name:
                                try:
                                    val = float(st.get('RainfallElement', {}).get('Now', {}).get('Precipitation', '-99'))
                                    if val > actual_rain:
                                        actual_rain = val
                                except ValueError:
                                    pass
                        if actual_rain > 0:
                            actual_icon = "💧"
                            if actual_rain >= 350.0:
                                actual_icon = "🟣"
                            elif actual_rain >= 200.0:
                                actual_icon = "🔴"
                            elif actual_rain >= 100.0:
                                actual_icon = "🟠"
                            elif actual_rain >= 40.0:
                                actual_icon = "🟡"
                            actual_rain_str = f"\n今日實測累積雨量：`{actual_icon} {actual_rain} mm`"
                        else:
                            actual_rain_str = "\n今日實測累積雨量：`無資料或尚無降雨`"
                    else:
                        actual_rain_str = "\n今日實測累積雨量：`無資料`"

                    # 組合描述
                    desc_text = f"**{loc_name}** 將有短延時強降雨發生！"
                    if end_time_str:
                        desc_text += f"持續時間至{end_time_str}。"
                    desc_text += rain_val_str + actual_rain_str

                    embed = discord.Embed(
                        title="",
                        description=desc_text,
                        color=0xFFCC00
                    )

                    # 嵌入雷達回波 GIF
                    if img_file:
                        img_url = f"https://www.cwa.gov.tw/Data/warning/w33/{img_file}"
                        embed.set_image(url=img_url)

                    try:
                        if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                            logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                        else:
                            await channel.send(content=message_content, embed=embed, silent=global_silent)
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.info(f"📢 [大雷雨] 已發送大雷雨即時訊息至 {guild_name} ({channel.name}) - {loc_name} (有效至 {end_time_str})")
                    except Exception as e:
                        logger.warning(f"⚠️ [大雷雨] 發送通知失敗: {e!r}")

                    # 註冊降雨預警抑制：在大雷雨持續時間內不再發送降雨預警
                    if end_timestamp > 0:
                        suppress_key = f"{guild_id}_{loc_name}"
                        self.thunderstorm_suppress_until[suppress_key] = end_timestamp
                        logger.info(f"🔇 [大雷雨] 已抑制 {loc_name} 的降雨預警至 {end_time_str}")

                self.thunderstorm_notified_ids.add(notify_key)

        # 清理過期的已通知 ID（只保留目前仍存在於 WarnContent_W33 的 ID）
        active_ids = set()
        for warn in warnings:
            wid = warn.get('ID', '')
            if wid:
                for guild_id in settings:
                    active_ids.add(f"{guild_id}_{wid}")
        # 移除已不在活躍列表中的 ID
        self.thunderstorm_notified_ids = self.thunderstorm_notified_ids & active_ids

    @check_thunderstorm_loop.before_loop
    async def before_check_thunderstorm(self):
        await self.bot.wait_until_ready()

def render_rain_grid_map_pil(values: list, town_mapping: dict = None) -> io.BytesIO:
    """
    將 441x561 降雨預報網格資料渲染為地圖圖片 (風格完全比照 lightning.py)
    """
    with open('maps/towns-mercator-10t.json', 'r', encoding='utf-8') as f:
        topo = json.load(f)

    scale = topo['transform']['scale']
    translate = topo['transform']['translate']
    arcs = topo['arcs']

    # 1. 解碼 TopoJSON arcs
    decoded_arcs = []
    for arc in arcs:
        x, y = 0, 0
        decoded = []
        for point in arc:
            x += point[0]
            y += point[1]
            decoded.append((x * scale[0] + translate[0], y * scale[1] + translate[1]))
        decoded_arcs.append(decoded)

    lines = []
    kinmen_x_list, kinmen_y_list = [], []
    matsu_x_list, matsu_y_list = [], []
    penghu_x_list, penghu_y_list = [], []

    for geom in topo['objects']['towns']['geometries']:
        props = geom.get('properties', {})
        county = props.get('COUNTYNAME', '')

        geom_lines = []
        if geom['type'] == 'Polygon':
            for ring in geom['arcs']:
                line = []
                for arc_idx in ring:
                    arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                    line.extend(arc)
                geom_lines.append(line)
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['arcs']:
                for ring in poly:
                    line = []
                    for arc_idx in ring:
                        arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                        line.extend(arc)
                    geom_lines.append(line)

        if county == '金門縣':
            for line in geom_lines:
                for pt in line:
                    kinmen_x_list.append(pt[0]); kinmen_y_list.append(pt[1])
            continue
        elif county == '連江縣':
            for line in geom_lines:
                for pt in line:
                    matsu_x_list.append(pt[0]); matsu_y_list.append(pt[1])
            continue
        elif county == '澎湖縣':
            for line in geom_lines:
                for pt in line:
                    penghu_x_list.append(pt[0]); penghu_y_list.append(pt[1])

        is_main = (county != '澎湖縣')
        lines.append({'is_main': is_main, 'county': county, 'coords': geom_lines})

    # 取得本島 Bounding Box
    main_x = [pt[0] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
    main_y = [pt[1] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
    min_x, max_x = min(main_x), max(main_x)
    min_y, max_y = min(main_y), max(main_y)

    WGS_MIN_LON, WGS_MAX_LON = 120.036, 122.001
    WGS_MIN_LAT, WGS_MAX_LAT = 21.896, 25.300

    def merc_y(lat_deg):
        return math.log(math.tan(math.pi/4 + lat_deg * math.pi/360))

    penghu_offset_x = 0
    penghu_offset_y = 0
    if penghu_x_list:
        fake_cx = (min(penghu_x_list) + max(penghu_x_list)) / 2
        fake_cy = (min(penghu_y_list) + max(penghu_y_list)) / 2

        real_cx = min_x + (119.508 - WGS_MIN_LON) / (WGS_MAX_LON - WGS_MIN_LON) * (max_x - min_x)
        my = merc_y(23.479)
        my_max = merc_y(WGS_MAX_LAT)
        my_min = merc_y(WGS_MIN_LAT)
        real_cy = min_y + (my_max - my) / (my_max - my_min) * (max_y - min_y)

        penghu_offset_x = real_cx - fake_cx
        penghu_offset_y = real_cy - fake_cy

    for item in lines:
        if item['county'] == '澎湖縣':
            for line in item['coords']:
                for i in range(len(line)):
                    line[i] = (line[i][0] + penghu_offset_x, line[i][1] + penghu_offset_y)

    # 縣市界線
    county_lines = []
    if 'counties' in topo['objects']:
        for geom in topo['objects']['counties']['geometries']:
            geom_lines = []
            if geom['type'] == 'Polygon':
                for ring in geom['arcs']:
                    line = []
                    for arc_idx in ring:
                        arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                        line.extend(arc)
                    geom_lines.append(line)
            elif geom['type'] == 'MultiPolygon':
                for poly in geom['arcs']:
                    for ring in poly:
                        line = []
                        for arc_idx in ring:
                            arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                            line.extend(arc)
                        geom_lines.append(line)

            filtered = []
            for line in geom_lines:
                if not line: continue
                pt = line[0]
                is_kinmen = kinmen_x_list and (min(kinmen_x_list)-5 <= pt[0] <= max(kinmen_x_list)+5) and (min(kinmen_y_list)-5 <= pt[1] <= max(kinmen_y_list)+5)
                is_matsu = matsu_x_list and (min(matsu_x_list)-5 <= pt[0] <= max(matsu_x_list)+5) and (min(matsu_y_list)-5 <= pt[1] <= max(matsu_y_list)+5)
                if is_kinmen or is_matsu:
                    continue
                is_penghu = penghu_x_list and (min(penghu_x_list)-5 <= pt[0] <= max(penghu_x_list)+5) and (min(penghu_y_list)-5 <= pt[1] <= max(penghu_y_list)+5)
                if is_penghu:
                    filtered.append([(p[0] + penghu_offset_x, p[1] + penghu_offset_y) for p in line])
                else:
                    filtered.append(line)
            if filtered:
                county_lines.append(filtered)

    all_x = [pt[0] for item in lines for line in item['coords'] for pt in line]
    all_y = [pt[1] for item in lines for line in item['coords'] for pt in line]
    img_min_x, img_max_x = min(all_x), max(all_x)
    img_min_y, img_max_y = min(all_y), max(all_y)

    IMG_W = 820
    pad = 40
    scale_factor = (IMG_W - 2 * pad) / (img_max_x - img_min_x)
    IMG_H = int((img_max_y - img_min_y) * scale_factor) + 2 * pad

    def map_to_img(x, y):
        px = pad + (x - img_min_x) * scale_factor
        py = pad + (y - img_min_y) * scale_factor
        return px, py

    def lonlat_to_img(lon, lat):
        x = min_x + (lon - WGS_MIN_LON) / (WGS_MAX_LON - WGS_MIN_LON) * (max_x - min_x)
        my = merc_y(lat)
        my_max = merc_y(WGS_MAX_LAT)
        my_min = merc_y(WGS_MIN_LAT)
        y = min_y + (my_max - my) / (my_max - my_min) * (max_y - min_y)
        if 119.2 <= lon <= 119.8 and 23.1 <= lat <= 23.8:
            x += penghu_offset_x
            y += penghu_offset_y
        return map_to_img(x, y)

    # 建立背景 (風格比照 lightning.py: #0f1113)
    img = Image.new('RGBA', (IMG_W, IMG_H), "#0f1113")
    draw = ImageDraw.Draw(img)

    # 繪製鄉鎮陸地 (fill: #1a1d20, outline: #292e33)
    for item in lines:
        fill_color = "#1a1d20"
        outline_color = "#292e33"
        for line in item['coords']:
            px_line = [map_to_img(pt[0], pt[1]) for pt in line]
            if len(px_line) >= 3:
                draw.polygon(px_line, fill=fill_color, outline=outline_color)

    # 縣市交界線 (county_outline_color: #3e454b, width=2)
    county_outline_color = "#3e454b"
    for geom_lines in county_lines:
        for line in geom_lines:
            px_line = [map_to_img(pt[0], pt[1]) for pt in line]
            if len(px_line) >= 2:
                draw.line(px_line, fill=county_outline_color, width=2)

    # 2. 繪製降雨網格色塊 (層疊於底圖之上)
    grid_layer = Image.new('RGBA', (IMG_W, IMG_H), (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid_layer)

    GRID_W, GRID_H = 441, 561
    rain_count = 0
    max_val = 0.0

    if values:
        for idx, v in enumerate(values):
            if not v: continue
            v_str = str(v).strip()
            if not v_str: continue
            try:
                val = float(v_str)
                if val >= 0.5:
                    gx = idx % GRID_W
                    gy = idx // GRID_W
                    lon = 117.975 + gx * 0.0125
                    lat = 19.975 + gy * 0.0125

                    if val > max_val: max_val = val
                    rain_count += 1

                    if val >= 200.0: color = (156, 39, 176, 230)
                    elif val >= 100.0: color = (233, 30, 99, 220)
                    elif val >= 70.0: color = (200, 0, 0, 210)
                    elif val >= 40.0: color = (255, 152, 0, 200)
                    elif val >= 20.0: color = (255, 235, 59, 190)
                    elif val >= 10.0: color = (0, 230, 118, 180)
                    elif val >= 6.0: color = (0, 119, 255, 170)
                    elif val >= 2.0: color = (0, 160, 240, 160)
                    else: color = (121, 201, 250, 150)

                    px1, py1 = lonlat_to_img(lon, lat + 0.0125)
                    px2, py2 = lonlat_to_img(lon + 0.0125, lat)

                    if -50 <= px1 <= IMG_W + 50 and -50 <= py1 <= IMG_H + 50:
                        grid_draw.rectangle([min(px1, px2), min(py1, py2), max(px1, px2) + 1.2, max(py1, py2) + 1.2], fill=color)
            except ValueError:
                pass

    img = Image.alpha_composite(img, grid_layer)
    draw = ImageDraw.Draw(img)

    # 重新在網格上疊加縣市界線以利識別
    for geom_lines in county_lines:
        for line in geom_lines:
            px_line = [map_to_img(pt[0], pt[1]) for pt in line]
            if len(px_line) >= 2:
                draw.line(px_line, fill=county_outline_color, width=2)

    # 3. 載入字體 (完全比照 lightning.py)
    font_paths = [
        "fonts/Noto_Sans_TC/NotoSansTC-Regular.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "PingFang.ttc",
        "C:\\Windows\\Fonts\\msjh.ttc",
        "msjh.ttc"
    ]
    font_title = None
    font_time = None
    font_legend = None
    for path in font_paths:
        try:
            font_title = ImageFont.truetype(path, 36)
            font_time = ImageFont.truetype(path, 20)
            font_legend = ImageFont.truetype(path, 15)
            break
        except Exception:
            continue

    if font_title is None:
        font_title = ImageFont.load_default()
        font_time = ImageFont.load_default()
        font_legend = ImageFont.load_default()

    # 4. 繪製左上角標題 (帶 2px 黑色外框影陰)
    title_str = " 1小時網格降雨預報圖"
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            if dx != 0 or dy != 0:
                draw.text((25 + dx, 25 + dy), title_str, fill="#000000", font=font_title)
    draw.text((25, 25), title_str, fill="#ffffff", font=font_title)

    # 5. 繪製左下角時間與 Bot 標籤 (帶 2px 黑色外框影陰)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    time_text = f"Generated by Saiu-Bot\n資料時間 {now_str}"
    if hasattr(draw, 'multiline_textbbox'):
        text_bbox = draw.multiline_textbbox((0, 0), time_text, font=font_time)
        text_h = text_bbox[3] - text_bbox[1]
    else:
        _, text_h = draw.textsize(time_text, font=font_time)

    for dx in range(-2, 3):
        for dy in range(-2, 3):
            if dx != 0 or dy != 0:
                draw.multiline_text((25 + dx, IMG_H - text_h - 25 + dy), time_text, fill="#000000", font=font_time)
    draw.multiline_text((25, IMG_H - text_h - 25), time_text, fill="#cccccc", font=font_time)

    # 6. 繪製右下角降雨強度圖例 Legend
    legends = [
        ("≥ 200.0", (156, 39, 176)),
        ("100 - 200", (233, 30, 99)),
        ("70 - 100", (200, 0, 0)),
        ("40 - 70", (255, 152, 0)),
        ("20 - 40", (255, 235, 59)),
        ("10 - 20", (0, 230, 118)),
        ("6 - 10", (0, 119, 255)),
        ("2 - 6", (0, 160, 240)),
        ("0.5 - 2", (121, 201, 250)),
    ]

    leg_w, leg_h = 160, len(legends) * 22 + 30
    leg_x = IMG_W - leg_w - 25
    leg_y = IMG_H - leg_h - 25

    # 圖例半透明背景框
    draw.rectangle([leg_x, leg_y, leg_x + leg_w, leg_y + leg_h], fill=(15, 17, 19, 200), outline="#292e33")
    draw.text((leg_x + 10, leg_y + 8), "圖例 (mm/h)", fill="#ffffff", font=font_legend)

    ly = leg_y + 30
    for label, col in legends:
        draw.rectangle([leg_x + 10, ly + 2, leg_x + 24, ly + 14], fill=col, outline="#ffffff")
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                if dx != 0 or dy != 0:
                    draw.text((leg_x + 32 + dx, ly + dy), label, fill="#000000", font=font_legend)
        draw.text((leg_x + 32, ly), label, fill="#cccccc", font=font_legend)
        ly += 22

    output = io.BytesIO()
    img.convert('RGB').save(output, format='PNG')
    output.seek(0)
    return output

async def setup(bot):
    await bot.add_cog(RainForecastCog(bot))