import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import json
import os
import re
from geopy.geocoders import Nominatim

class RainForecastCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.geolocator = Nominatim(user_agent="Saiu-Bot-Rain-Alert")
        self.alert_status = {}  # 紀錄伺服器目前是否已發送過預警 (避免每 10 分鐘重複洗版)
        self.latest_rain_data = []  # 供手動查詢使用的快取資料
        self.town_mapping = self._load_town_mapping()
        self.check_rain_loop.start()

    def _load_town_mapping(self):
        """讀取本地地圖資料，建立鄉鎮市區全名與簡稱的對照表"""
        mapping = {}
        
        def add_to_mapping(c, t, lat=None, lon=None):
            c = c.replace('台', '臺')
            t = t.replace('台', '臺')
            fullname = f"{c}{t}"
            
            c_short = c[:-1] if len(c) >= 3 and c[-1] in ['縣', '市'] else c
            t_short = t[:-1] if len(t) >= 3 and t[-1] in ['區', '鄉', '鎮', '市'] else t
            
            combinations = [t, fullname]
            if c_short != c:
                combinations.append(f"{c_short}{t}")
            if t_short != t:
                combinations.append(t_short)
                combinations.append(f"{c}{t_short}")
            if c_short != c and t_short != t:
                combinations.append(f"{c_short}{t_short}")
                
            for combo in combinations:
                if combo not in mapping:
                    mapping[combo] = []
                if not any(item[0] == fullname for item in mapping[combo]):
                    mapping[combo].append((fullname, lat, lon))

        # 1. 解析 locations.js
        try:
            with open('maps/locations.js', 'r', encoding='utf-8') as f:
                current_county = None
                for line in f:
                    line = line.strip()
                    # 尋找縣市層級，例如: "基隆市": {
                    county_match = re.match(r'"([^"]+)"\s*:\s*\{', line)
                    if county_match:
                        current_county = county_match.group(1)
                        continue
                    
                    # 尋找鄉鎮層級，例如: "中正區": [1001701, 25.1407924, 121.7592534, ...],
                    town_match = re.match(r'"([^"]+)"\s*:\s*\[\s*\d+,\s*([0-9.]+),\s*([0-9.]+)', line)
                    if town_match and current_county:
                        lat = float(town_match.group(2))
                        lon = float(town_match.group(3))
                        add_to_mapping(current_county, town_match.group(1), lat, lon)
        except Exception:
            pass
            
        # 2. 如果檔案讀不到，使用內建的易混淆清單 Fallback 保底
        if not mapping:
            mapping = {
                "信義": [("臺北市信義區", None, None), ("南投縣信義鄉", None, None)],
                "仁愛": [("基隆市仁愛區", None, None), ("南投縣仁愛鄉", None, None)],
                "中正": [("臺北市中正區", None, None), ("基隆市中正區", None, None)],
                "中山": [("臺北市中山區", None, None), ("基隆市中山區", None, None)],
                "大安": [("臺北市大安區", None, None), ("臺中市大安區", None, None)],
                "東區": [("新竹市東區", None, None), ("臺中市東區", None, None), ("臺南市東區", None, None), ("嘉義市東區", None, None)],
                "西區": [("新竹市西區", None, None), ("臺中市西區", None, None), ("嘉義市西區", None, None)],
                "南區": [("臺中市南區", None, None), ("臺南市南區", None, None)],
                "北區": [("新竹市北區", None, None), ("臺中市北區", None, None), ("臺南市北區", None, None)]
            }
            
        return mapping

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

    def _get_max_rain(self, values, grid_x, grid_y, radius=2):
        """取得指定網格及其周邊 (預設 5x5) 的最大降雨量，解決單一網格無雨但該鄉鎮其他區域有雨的誤差"""
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

    @app_commands.command(name="設定降雨預警", description="設定本地鄉鎮市區，當未來1小時預測有雨時通知此頻道")
    @app_commands.describe(location="請輸入縣市與鄉鎮市區（例如：台北市信義區）")
    @app_commands.default_permissions(manage_guild=True)
    async def set_rain_alert(self, interaction: discord.Interaction, location: str):
        await interaction.response.defer(ephemeral=True)

        grid_data, msg_or_loc = await self.get_location_grid(location)
        if not grid_data:
            await interaction.followup.send(msg_or_loc)
            return

        grid_x, grid_y = grid_data
        location = msg_or_loc

        # 儲存至 guild_settings.json
        settings_path = 'guild_settings.json'
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception:
            settings = {}

        guild_id = str(interaction.guild_id)
        if guild_id not in settings:
            settings[guild_id] = {}

        # 兼容舊版設定，將舊設定轉移至新結構
        if 'rain_alert' in settings[guild_id]:
            old_alert = settings[guild_id].pop('rain_alert')
            settings[guild_id].setdefault('rain_alerts', {})[old_alert['location_name']] = {
                'channel_id': old_alert['channel_id'],
                'grid_x': old_alert['grid_x'],
                'grid_y': old_alert['grid_y']
            }

        if 'rain_alerts' not in settings[guild_id]:
            settings[guild_id]['rain_alerts'] = {}
            
        if len(settings[guild_id]['rain_alerts']) >= 10:
            await interaction.followup.send("❌ 每個伺服器最多只能設定 10 個降雨預警地點。")
            return

        settings[guild_id]['rain_alerts'][location] = {
            'channel_id': interaction.channel_id,
            'grid_x': grid_x,
            'grid_y': grid_y
        }

        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)

        await interaction.followup.send(f"✅ 已成功將降雨預警地點加入：**{location}**！\n未來一小時若預測有雨，將會自動通知此頻道。")

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
                                status_key = f"{guild_id}_{loc_name}"
                                rain_val = self._get_max_rain(values, alert_info['grid_x'], alert_info['grid_y'])
                                is_raining = rain_val > 0.0

                                # 若大於 0 代表即將下雨，且之前還沒發送過通知，則發送
                                if is_raining and not self.alert_status.get(status_key, False):
                                    channel = self.bot.get_channel(alert_info['channel_id'])
                                    if channel:
                                        icon = "💧"
                                        if rain_val >= 350.0:
                                            icon = "🟣"
                                        elif rain_val >= 200.0:
                                            icon = "🔴"
                                        elif rain_val >= 100.0:
                                            icon = "🟠"
                                        elif rain_val >= 40.0:
                                            icon = "🟡"

                                        message_content = "🌧️ 降雨預警通知"
                                        embed = discord.Embed(
                                            title="",
                                            description=f"**{loc_name}** 未來 1 小時內預測將有降雨發生！\n預估累積雨量：`{icon} {rain_val} mm`",
                                            color=discord.Color.blue()
                                        )
                                        await channel.send(content=message_content, embed=embed)
                                    self.alert_status[status_key] = True
                                # 雨停預測或是無雨時，若先前有發送過下雨預警，則發送趨緩通知並重置狀態
                                elif not is_raining:
                                    if self.alert_status.get(status_key, False):
                                        channel = self.bot.get_channel(alert_info['channel_id'])
                                        if channel:
                                            message_content = "🌤️ 降雨趨緩通知"
                                            embed = discord.Embed(
                                                title="",
                                                description=f"**{loc_name}** 未來 1 小時內的雨勢預計將會趨緩或停止！",
                                                color=discord.Color.green()
                                            )
                                            await channel.send(content=message_content, embed=embed)
                                        self.alert_status[status_key] = False

        except Exception as e:
            print(f"⚠️ [降雨預報] 檢查時發生錯誤: {e}")

    @check_rain_loop.before_loop
    async def before_check_rain(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(RainForecastCog(bot))