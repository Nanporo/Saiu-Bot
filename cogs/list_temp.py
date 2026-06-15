import discord
from discord.ext import commands
from discord import app_commands
import json
from datetime import datetime, timezone, timedelta
import logging
from modules.cache import async_cache

logger = logging.getLogger(__name__)

class TempView(discord.ui.View):
    def __init__(self, bot, api_key, results, is_high, is_today, show_high_altitude, show_image=False, image_url=None):
        super().__init__(timeout=300)
        self.bot = bot
        self.api_key = api_key
        self.results = results
        self.is_high = is_high
        self.is_today = is_today
        self.show_high_altitude = show_high_altitude
        self.show_details = False
        self.show_image = show_image
        self.image_url = image_url

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label == "顯示氣溫圖":
                    if self.show_image:
                        child.label = "隱藏氣溫圖"
                elif child.label == "隱藏高海拔":
                    if not self.show_high_altitude:
                        child.label = "包含高海拔"

    def build_embed(self):
        if self.is_today:
            message_content = "🌡️ 今日最高溫測站排行" if self.is_high else "❄️ 今日最低溫測站排行"
        else:
            message_content = "🌡️ 現在高溫排行" if self.is_high else "❄️ 現在低溫排行"
            
        if not self.show_high_altitude:
            message_content += " (排除高海拔地區)"
        embed = discord.Embed(color=0xff3846 if self.is_high else 0x3498db)
        
        lines = []
        display_results = []
        for r in self.results:
            if not self.show_high_altitude and r.get('altitude', 0) > 1500:
                continue
            display_results.append(r)
            if len(display_results) >= 10:
                break
                
        for i, r in enumerate(display_results):
            # 決定高低溫燈號
            temp_val = r['temp_sort']
            icon = "⚪️"
            if temp_val != 999.0 and temp_val != -999.0:
                if self.is_high:
                    if temp_val >= 38.0:
                        icon = "🔴"
                    elif temp_val >= 36.0:
                        icon = "🟠"
                    elif temp_val >= 32.0:
                        icon = "🟡"
                else:
                    if temp_val <= 6.0:
                        icon = "🟣"
                    elif temp_val <= 12.0:
                        icon = "🔵"
                    elif temp_val <= 16.0:
                        icon = "🟢"

            num_emoji = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'][i]
            if i < 3:
                rank_str = ['`🥇`', '`🥈`', '`🥉`'][i]
                line = f"{num_emoji} `{icon} {r['temp_display']}` **{r['county']}{r['town']}** {rank_str}"
            else:
                line = f"{num_emoji} `{icon} {r['temp_display']}` **{r['county']}{r['town']}**"

            if self.show_details:
                line += f"\n>  {r['station']} | 海拔 {r['altitude']}m\n>  {r['time']}"
            lines.append(line)
        
        embed.description = "\n".join(lines)
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")

        if self.show_image and self.image_url:
            embed.set_image(url=self.image_url)
            
        return message_content, embed

    @discord.ui.button(label="顯示詳細資訊", style=discord.ButtonStyle.primary)
    async def toggle_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.show_details = not self.show_details
        if self.show_details:
            button.label = "隱藏詳細資訊"
            button.style = discord.ButtonStyle.secondary
        else:
            button.label = "顯示詳細資訊"
            button.style = discord.ButtonStyle.primary
            
        content, embed = self.build_embed()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

    @discord.ui.button(label="顯示氣溫圖", style=discord.ButtonStyle.secondary)
    async def toggle_image(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.show_image = not self.show_image
        
        if self.show_image:
            button.label = "隱藏氣溫圖"
            if not self.image_url:
                timestamp = (int(datetime.now().timestamp()) // 300) * 300
                self.image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0038-001.jpg?t={timestamp}"
            content, embed = self.build_embed()
            await interaction.response.edit_message(content=content, embed=embed, view=self)
        else:
            button.label = "顯示氣溫圖"
            content, embed = self.build_embed()
            await interaction.response.edit_message(content=content, embed=embed, view=self)

    @discord.ui.button(label="隱藏高海拔", style=discord.ButtonStyle.secondary)
    async def toggle_altitude(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.show_high_altitude = not self.show_high_altitude
        if self.show_high_altitude:
            button.label = "隱藏高海拔"
        else:
            button.label = "包含高海拔"
            
        content, embed = self.build_embed()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

class TempCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @async_cache(ttl_seconds=300)
    async def fetch_temp_data(self):
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?Authorization={self.api_key}&WeatherElement=AirTemperature,DailyHigh,DailyLow"
        try:
            async with self.bot.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            logger.error(f"❌ 抓取氣溫排行資料失敗: {e}")
        return None

    @app_commands.command(name="氣溫排行", description="🌡️ 查詢台灣各測站的現在溫度或今日極端溫排行")
    @app_commands.describe(
        temp_type="選擇查詢現在氣溫或今日極端溫",
        高海拔="是否包含高海拔測站",
        氣溫圖="是否顯示氣溫分布圖"
    )
    @app_commands.choices(temp_type=[
        app_commands.Choice(name="現在最高溫", value="now_high"),
        app_commands.Choice(name="現在最低溫", value="now_low"),
        app_commands.Choice(name="今日最高溫", value="today_high"),
        app_commands.Choice(name="今日最低溫", value="today_low")
    ], 高海拔=[
        app_commands.Choice(name="是", value="yes"),
        app_commands.Choice(name="否", value="no")
    ], 氣溫圖=[
        app_commands.Choice(name="顯示", value="yes"),
        app_commands.Choice(name="不顯示", value="no")
    ])
    async def temp_command(self, interaction: discord.Interaction, temp_type: app_commands.Choice[str], 高海拔: app_commands.Choice[str] = None, 氣溫圖: app_commands.Choice[str] = None):
        if not self.api_key:
            await interaction.response.send_message("⚠️ 未設定 API Key，無法查詢資料。", ephemeral=True)
            return

        # 避免 API 回應過慢導致超時報錯
        await interaction.response.defer()
        
        try:
            data = await self.fetch_temp_data()
            if not data:
                await interaction.followup.send("⚠️ API 請求失敗或無法獲取資料。")
                return

            stations = data.get('records', {}).get('Station', [])
            
            if not stations:
                self.fetch_temp_data.invalidate_all()  # API 異常時清除快取，強制下次重新抓取
                await interaction.followup.send("⚠️ 找不到有效的溫度資料。")
                return

            # 預設包含高海拔測站
            show_high_altitude = True
            if 高海拔 and 高海拔.value == 'no':
                show_high_altitude = False

            is_today = temp_type.value in ["today_high", "today_low"]
            is_high = temp_type.value in ["now_high", "today_high"]
            results = []
            for st in stations:
                station_name = st.get('StationName', '未知')
                geo_info = st.get('GeoInfo', {})
                county = geo_info.get('CountyName', '')
                town = geo_info.get('TownName', '')
                altitude_str = geo_info.get('StationAltitude', '0')

                try:
                    altitude = float(altitude_str)
                except ValueError:
                    altitude = 0.0

                weather = st.get('WeatherElement', {})
                
                if temp_type.value == "today_high":
                    daily_high = weather.get('DailyHigh') or weather.get('DailyExtreme', {}).get('DailyHigh') or {}
                    temp_info = daily_high.get('TemperatureInfo') or {}
                    temp_str = temp_info.get('AirTemperature', '-99')
                    time_str = temp_info.get('Occurred_at', {}).get('DateTime', '')
                elif temp_type.value == "today_low":
                    daily_low = weather.get('DailyLow') or weather.get('DailyExtreme', {}).get('DailyLow') or {}
                    temp_info = daily_low.get('TemperatureInfo') or {}
                    temp_str = temp_info.get('AirTemperature', '-99')
                    time_str = temp_info.get('Occurred_at', {}).get('DateTime', '')
                else:
                    temp_str = weather.get('AirTemperature', '-99')
                    time_str = st.get('ObsTime', {}).get('DateTime', '')

                try:
                    temp_val = float(temp_str)
                except (ValueError, TypeError):
                    continue

                # 處理氣象署資料的無效值 -99 或 -99.0
                if temp_val <= -90.0:
                    continue
                else:
                    temp_display = f"{temp_val} °C"
                    temp_sort = temp_val
                    
                # 處理測得溫度的時間轉換為 Discord 時間戳
                try:
                    if not time_str or time_str == "-99":
                        time_format = "未知"
                    else:
                        try:
                            dt = datetime.fromisoformat(time_str)
                        except ValueError:
                            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                        time_format = f"<t:{int(dt.timestamp())}:t>"
                except Exception:
                    time_format = "未知"

                results.append({
                    "station": station_name,
                    "county": county,
                    "town": town,
                    "altitude": altitude,
                    "temp_display": temp_display,
                    "temp_sort": temp_sort,
                    "time": time_format
                })

            if not results:
                self.fetch_temp_data.invalidate_all()  # API 無極端值時清除快取，強制下次重新抓取
                await interaction.followup.send("⚠️ 找不到有效的溫度資料。")
                return

            results.sort(key=lambda x: x['temp_sort'], reverse=is_high)
            
            show_image_initial = (氣溫圖.value == "yes") if 氣溫圖 else False
            image_url = None

            if show_image_initial:
                timestamp = (int(datetime.now().timestamp()) // 300) * 300
                image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0038-001.jpg?t={timestamp}"

            view = TempView(self.bot, self.api_key, results, is_high, is_today, show_high_altitude, show_image_initial, image_url)
            content, embed = view.build_embed()

            await interaction.followup.send(content=content, embed=embed, view=view)

        except Exception as e:
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{e}")
            logger.error(f"❌ /氣溫排行 發生未預期的錯誤：{e}")

async def setup(bot):
    await bot.add_cog(TempCog(bot))
