import discord
from discord.ext import commands
from discord import app_commands
import json
import logging
from datetime import datetime
from modules.cache import async_cache

logger = logging.getLogger(__name__)

import math
from modules.location_matcher import match_location, town_mapping_cache, get_town_autocomplete

def haversine_dist(lat1, lon1, lat2, lon2):
    R = 6371.0 # 地球半徑(公里)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

class AqiCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = self.get_api_key()
        self.sites = []

    def get_api_key(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f).get('MOENV_API_KEY', '')
        except Exception:
            return ''

    @async_cache(ttl_seconds=1800)
    async def fetch_aqi_data(self):
        if not self.api_key:
            return None
        url = "https://data.moenv.gov.tw/api/v2/aqx_p_432"
        params = {"api_key": self.api_key}
        try:
            async with self.bot.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, dict):
                        return data.get('records', [])
                    return data
        except Exception as e:
            logger.error(f"❌ 抓取 AQI 資料失敗: {e!r}")
        return None

    @async_cache(ttl_seconds=1800)
    async def fetch_weather_text(self):
        url = "https://airtw.moenv.gov.tw/"
        try:
            async with self.bot.session.get(url) as response:
                if response.status == 200:
                    html = await response.text()
                    import re
                    # 抓取 <figcaption class="weather"> 到第一個 <div (按鈕區) 或結尾之間的所有文字
                    match = re.search(r'<figcaption class="weather">\s*(.*?)\s*(?:<div|</figcaption>)', html, re.S)
                    if match:
                        text = match.group(1).strip()
                        # 清除可能殘留的 HTML 標籤
                        text = re.sub(r'<[^>]+>', '', text)
                        # 清除多餘的空白與換行
                        text = re.sub(r'\s+', ' ', text).strip()
                        return text
        except Exception as e:
            logger.error(f"❌ 抓取 airtw.moenv.gov.tw 失敗: {e!r}")
        return ""

    def get_aqi_color(self, aqi_val):
        if aqi_val <= 50: return discord.Color.green()
        if aqi_val <= 100: return discord.Color.gold()
        if aqi_val <= 150: return discord.Color.orange()
        if aqi_val <= 200: return discord.Color.red()
        if aqi_val <= 300: return discord.Color.purple()
        return discord.Color.dark_red()

    def get_aqi_emoji(self, aqi_val):
        if aqi_val <= 50: return "🟢"
        if aqi_val <= 100: return "🟡"
        if aqi_val <= 150: return "🟠"
        if aqi_val <= 200: return "🔴"
        if aqi_val <= 300: return "🟣"
        return "🟤"

    async def get_aqi_embed(self, 位置: str):
        records = await self.fetch_aqi_data()
        if not records:
            return "❌ 目前無法取得空氣品質資料，請稍後再試。", None

        target = next((r for r in records if r.get('sitename') == 位置), None)
        
        nearest_msg = ""
        # 如果不是直接命中測站名稱，嘗試使用 location_matcher 解析鄉鎮市區
        if not target:
            loc_val, error_msg = match_location(位置)
            if loc_val:
                # 取得該鄉鎮市區的經緯度
                matches = town_mapping_cache.get(loc_val, [])
                target_lat = None
                target_lon = None
                for m in matches:
                    if m[0] == loc_val and m[1] is not None and m[2] is not None:
                        target_lat = m[1]
                        target_lon = m[2]
                        break
                
                if target_lat and target_lon:
                    # 尋找距離最近的測站
                    min_dist = float('inf')
                    for r in records:
                        try:
                            s_lat = float(r.get('latitude', 0))
                            s_lon = float(r.get('longitude', 0))
                            dist = haversine_dist(target_lat, target_lon, s_lat, s_lon)
                            if dist < min_dist:
                                min_dist = dist
                                target = r
                        except ValueError:
                            continue
                    
                    if target:
                        nearest_msg = f"\n已自動匹配距離 {loc_val} 最近的測站"

        # 如果還是沒有，嘗試模糊匹配
        if not target:
            target = next((r for r in records if 位置 in r.get('sitename', '')), None)

        if not target:
            return f"❌ 找不到名為「{位置}」的測站或相應的鄉鎮市區資料。", None

        aqi_str = target.get('aqi', '')
        status_str = target.get('status', '')

        is_invalid = False
        if not aqi_str or str(aqi_str).strip() == "" or aqi_str == "無資料" or not status_str or status_str == "設備維護":
            is_invalid = True

        try:
            if is_invalid:
                aqi_val = 0
            else:
                aqi_val = int(aqi_str)
        except ValueError:
            aqi_val = 0
            is_invalid = True

        if is_invalid:
            status = "無資料"
            color = discord.Color.light_grey()
            emoji = "⚪"
            display_aqi = "無資料"
        else:
            status = status_str
            color = self.get_aqi_color(aqi_val)
            emoji = self.get_aqi_emoji(aqi_val)
            display_aqi = aqi_str

        county = target.get('county', '未知')
        sitename = target.get('sitename', '未知')
        publishtime_str = target.get('publishtime', '未知')
        publishtime_ts = "未知"
        try:
            if publishtime_str != "未知":
                fmt_str = publishtime_str.replace("/", "-")
                from datetime import datetime, timezone, timedelta
                dt = datetime.strptime(fmt_str, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                publishtime_ts = f"<t:{int(dt.timestamp())}:f>"
        except Exception:
            publishtime_ts = publishtime_str

        embed = discord.Embed(
            title="",
            description=f"**{county} {sitename}** 空氣品質{status}\n發佈時間：{publishtime_ts}",
            color=color
        )

        def get_pollutant_emoji(key, val):
            if val is None or val == "" or str(val).strip() == "": return ""
            try: v = float(val)
            except ValueError: return ""
            
            if key == 'pm2.5':
                if v <= 15: return "🟢 "
                if v <= 35: return "🟡 "
                if v <= 54: return "🟠 "
                if v <= 150: return "🔴 "
                if v <= 250: return "🟣 "
                return "🟤 "
            elif key == 'pm10':
                if v <= 50: return "🟢 "
                if v <= 100: return "🟡 "
                if v <= 254: return "🟠 "
                if v <= 354: return "🔴 "
                if v <= 424: return "🟣 "
                return "🟤 "
            elif key == 'o3':
                if v <= 54: return "🟢 "
                if v <= 70: return "🟡 "
                if v <= 85: return "🟠 "
                if v <= 105: return "🔴 "
                if v <= 200: return "🟣 "
                return "🟤 "
            elif key == 'co':
                if v <= 4.4: return "🟢 "
                if v <= 9.4: return "🟡 "
                if v <= 12.4: return "🟠 "
                if v <= 15.4: return "🔴 "
                if v <= 30.4: return "🟣 "
                return "🟤 "
            elif key == 'so2':
                if v <= 35: return "🟢 "
                if v <= 75: return "🟡 "
                if v <= 185: return "🟠 "
                if v <= 304: return "🔴 "
                if v <= 604: return "🟣 "
                return "🟤 "
            elif key == 'no2':
                if v <= 53: return "🟢 "
                if v <= 100: return "🟡 "
                if v <= 360: return "🟠 "
                if v <= 649: return "🔴 "
                if v <= 1249: return "🟣 "
                return "🟤 "
            return ""

        def format_val(val, unit):
            if val is None or val == "" or str(val).strip() == "":
                return "未知"
            return f"{val} {unit}".strip()

        embed.add_field(name=f"{emoji} AQI", value=display_aqi, inline=False)
        embed.add_field(name=f"{get_pollutant_emoji('pm2.5', target.get('pm2.5'))}PM2.5", value=format_val(target.get('pm2.5'), "μg/m³"), inline=True)
        embed.add_field(name=f"{get_pollutant_emoji('pm10', target.get('pm10'))}PM10", value=format_val(target.get('pm10'), "μg/m³"), inline=True)
        embed.add_field(name=f"{get_pollutant_emoji('o3', target.get('o3'))}臭氧 (O3)", value=format_val(target.get('o3'), "ppb"), inline=True)
        
        embed.add_field(name=f"{get_pollutant_emoji('co', target.get('co'))}一氧化碳 (CO)", value=format_val(target.get('co'), "ppm"), inline=True)
        embed.add_field(name=f"{get_pollutant_emoji('so2', target.get('so2'))}二氧化硫 (SO2)", value=format_val(target.get('so2'), "ppb"), inline=True)
        embed.add_field(name=f"{get_pollutant_emoji('no2', target.get('no2'))}二氧化氮 (NO2)", value=format_val(target.get('no2'), "ppb"), inline=True)

        weather_text = await self.fetch_weather_text()
        if weather_text:
            embed.add_field(name="", value=f"```{weather_text}```", inline=False)

        from datetime import datetime, timezone, timedelta
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"環境部 • 查詢時間 {current_time}{nearest_msg}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/moenv_logo.png")

        return None, embed

    @app_commands.command(name="空氣品質", description="🍃 查詢各測站或指定鄉鎮市區目前的空氣品質指標 AQI")
    @app_commands.describe(位置="請輸入測站名稱或鄉鎮市區（例如：板橋、信義區）")
    async def aqi_command(self, interaction: discord.Interaction, 位置: str):
        if not self.api_key:
            await interaction.response.send_message("⚠️ 未設定 MOENV API Key，無法查詢資料。", ephemeral=True)
            return

        await interaction.response.defer()
        
        error_msg, embed = await self.get_aqi_embed(位置)
        if error_msg:
            await interaction.followup.send(error_msg)
        else:
            await interaction.followup.send(content="🍃 空氣品質資訊", embed=embed)

    @aqi_command.autocomplete("位置")
    async def aqi_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        choices = []
        
        # 1. 加入測站名稱匹配
        records = await self.fetch_aqi_data()
        if records:
            for r in records:
                sitename = r.get('sitename', '')
                county = r.get('county', '')
                if current in sitename or current in county:
                    choices.append(app_commands.Choice(name=f"測站：{county} - {sitename}", value=sitename))
        
        # 2. 加入鄉鎮市區匹配
        towns = get_town_autocomplete(current)
        for t in towns:
            choices.append(app_commands.Choice(name=f"地區：{t}", value=t))
            
        # 限制最多回傳 25 筆
        return choices[:25]

    async def refresh_message(self, interaction: discord.Interaction, message: discord.Message, cmd_name: str):
        if message.embeds:
            title = (message.embeds[0].title or "") + (message.embeds[0].description or "")
            from modules.location_matcher import town_mapping_cache, DEFAULT_TOWN_MAPPING
            keys = list(town_mapping_cache.keys()) + list(DEFAULT_TOWN_MAPPING.keys())
            keys = list(set(keys))
            keys.sort(key=len, reverse=True)
            found_loc = None
            for key in keys:
                if key.replace("台", "臺") in title.replace("台", "臺"):
                    found_loc = key
                    break
            if found_loc:
                await interaction.response.defer(ephemeral=True)
                error_msg, embed = await self.get_aqi_embed(found_loc)
                if error_msg:
                    await interaction.followup.send(error_msg, ephemeral=True)
                else:
                    await message.edit(embed=embed)
                    await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)
                return
        await interaction.response.send_message("❌ 無法從這則空氣品質訊息中提取出地點以重新查詢。", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AqiCog(bot))
