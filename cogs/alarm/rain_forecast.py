import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import json
import os
import time
from geopy.geocoders import Nominatim
from modules.town_mapping import load_town_mapping
from modules.cwa_api import fetch_current_rainfall

# 這個模組會自動預警1小時後即將有雨的區域，手動的是 cogs/rain_manual.py

class RainForecastCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.geolocator = Nominatim(user_agent="Saiu-Bot-Rain-Alert")
        self.alert_status = {}  # 紀錄伺服器目前是否已發送過預警 (避免每 10 分鐘重複洗版)
        self.latest_rain_data = []  # 供手動查詢使用的快取資料
        self.town_mapping = load_town_mapping()
        self.check_rain_loop.start()

    def get_api_key(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f).get('CWA_API_KEY', '')
        except Exception:
            return ''

    def cog_unload(self):
        self.check_rain_loop.cancel()

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

    def _get_max_rain(self, values, grid_x, grid_y, radius=3):
        """取得指定網格及其周邊 (預設 7x7，約半徑4公里) 的最大降雨量，解決單一網格無雨但該鄉鎮其他區域有雨的誤差"""
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

    async def fetch_rain_value(self, grid_x: int, grid_y: int):
        """[共用模組] 抓取指定網格及其周邊的降雨量，優先使用快取"""
        if self.latest_rain_data:
            return self._get_max_rain(self.latest_rain_data, grid_x, grid_y), None

        api_key = self.get_api_key()
        if not api_key:
            return None, "⚠️ 未設定 API Key，無法查詢資料。"

        url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-B0046-001?Authorization={api_key}&downloadType=WEB&format=JSON"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        dataset = data['cwaopendata']['dataset']
                        values = dataset['contents']['content'].split(',')
                        self.latest_rain_data = values
                        return self._get_max_rain(values, grid_x, grid_y), None
                    return None, "⚠️ 獲取資料失敗"
        except Exception as e:
            return None, str(e)

    @tasks.loop(minutes=10.0)
    async def check_rain_loop(self):
        api_key = self.get_api_key()
        if not api_key:
            return

        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception:
            return

        url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-B0046-001?Authorization={api_key}&downloadType=WEB&format=JSON"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        dataset = data['cwaopendata']['dataset']
                        values = dataset['contents']['content'].split(',')
                        self.latest_rain_data = values  # 更新快取
                        
                        current_rainfall_data = None
                        fetched_rainfall = False

                        for guild_id, d in settings.items():
                            alerts = d.get('rain_alerts', {})
                            # 確保迴圈內也能兼容舊設定檔
                            if 'rain_alert' in d:
                                alerts[d['rain_alert']['location_name']] = {
                                    'channel_id': d['rain_alert']['channel_id'],
                                    'grid_x': d['rain_alert']['grid_x'],
                                    'grid_y': d['rain_alert']['grid_y']
                                }
                                
                            for loc_name, alert_info in alerts.items():
                                # 略過因早期 bug 造成的損壞資料 (缺少 grid_x / 格式不為 dict)
                                if not isinstance(alert_info, dict) or 'grid_x' not in alert_info:
                                    continue

                                status_key = f"{guild_id}_{loc_name}"
                                rain_val = self._get_max_rain(values, alert_info['grid_x'], alert_info['grid_y'])
                                
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
                                elif rain_val >= 0.5:
                                    current_threshold = 0.5
                                    icon = "💧"

                                feels_like = ""
                                if current_threshold > 0.0:
                                    if rain_val >= 10.0:
                                        feels_like = "大雨"
                                    elif rain_val >= 2.5:
                                        feels_like = "中雨"
                                    elif rain_val > 0.5:
                                        feels_like = "小雨"
                                    else:
                                        feels_like = "毛毛雨"

                                prev_data = self.alert_status.get(status_key, {})
                                if isinstance(prev_data, (float, int)):
                                    prev_threshold = float(prev_data)
                                    cooldown_until = 0.0
                                elif isinstance(prev_data, bool):
                                    prev_threshold = 0.5 if prev_data else 0.0
                                    cooldown_until = 0.0
                                else:
                                    prev_threshold = prev_data.get('threshold', 0.0)
                                    cooldown_until = prev_data.get('cooldown_until', 0.0)

                                current_time = time.time()
                                if current_time < cooldown_until:
                                    continue

                                if current_threshold > 0.0:
                                    if prev_threshold == 0.0:
                                        # 第一次觸發下雨通知
                                        channel = self.bot.get_channel(alert_info['channel_id'])
                                        if channel:
                                            message_content = "🌧️ 降雨預警通知"
                                            embed = discord.Embed(
                                                title="",
                                                description=f"**{loc_name}** 未來 1 小時內預測將有降雨發生！\n預估累積雨量：`{icon} {rain_val} mm ({feels_like})`",
                                                color=discord.Color.blue()
                                            )
                                            await channel.send(content=message_content, embed=embed)
                                            guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                                            print(f"📢 [降雨預報] 已發送 {message_content} 至 {guild_name} ({channel.name}) - {loc_name}")
                                        self.alert_status[status_key] = {
                                            "threshold": current_threshold,
                                            "cooldown_until": current_time + 7200
                                        }
                                    elif current_threshold > prev_threshold:
                                        # 雨勢跨越更高門檻，發送雨勢變大通知
                                        channel = self.bot.get_channel(alert_info['channel_id'])
                                        if channel:
                                            if not fetched_rainfall:
                                                try:
                                                    current_rainfall_data = await fetch_current_rainfall(self.bot.session, api_key)
                                                except Exception as e:
                                                    print(f"⚠️ [降雨預報] 獲取實測雨量失敗: {e}")
                                                fetched_rainfall = True

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
                                            
                                            actual_rain_str = f"💧 {actual_rain} mm" if actual_rain > 0 else "無資料或尚無降雨"

                                            message_content = "🌧️ 雨勢變大通知"
                                            embed = discord.Embed(
                                                title="",
                                                description=f"**{loc_name}** 未來 1 小時內的預測雨勢將進一步增強！\n預估累積雨量：`{icon} {rain_val} mm ({feels_like})`\n今日實測累積雨量：`{actual_rain_str}`",
                                                color=discord.Color.orange()
                                            )
                                            await channel.send(content=message_content, embed=embed)
                                            guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                                            print(f"📢 [降雨預報] 已發送 {message_content} 至 {guild_name} ({channel.name}) - {loc_name}")
                                        self.alert_status[status_key] = {
                                            "threshold": current_threshold,
                                            "cooldown_until": 0.0
                                        }
                                    # 若 current_threshold <= prev_threshold 則不做任何事
                                    else:
                                        self.alert_status[status_key] = {
                                            "threshold": prev_threshold,
                                            "cooldown_until": cooldown_until
                                        }
                                else:
                                    # 雨勢小於 0.5，若先前有通知過則發送趨緩通知
                                    if prev_threshold > 0.0:
                                        channel = self.bot.get_channel(alert_info['channel_id'])
                                        if channel:
                                            message_content = "🌤️ 降雨趨緩通知"
                                            embed = discord.Embed(
                                                title="",
                                                description=f"**{loc_name}** 未來 1 小時內的雨勢預計將會趨緩或停止！",
                                                color=discord.Color.green()
                                            )
                                            await channel.send(content=message_content, embed=embed)
                                            guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                                            print(f"📢 [降雨預報] 已發送 {message_content} 至 {guild_name} ({channel.name}) - {loc_name}")
                                        self.alert_status[status_key] = {
                                            "threshold": 0.0,
                                            "cooldown_until": 0.0
                                        }

        except Exception as e:
            print(f"⚠️ [降雨預報] 檢查時發生錯誤: {e}")

    @check_rain_loop.before_loop
    async def before_check_rain(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(RainForecastCog(bot))