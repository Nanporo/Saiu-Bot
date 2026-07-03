import discord
from discord.ext import commands
from discord import app_commands
import json
from datetime import datetime, timezone, timedelta
import logging
from modules.cache import async_cache

logger = logging.getLogger(__name__)

class TempView(discord.ui.View):
    def __init__(self, bot, api_key, stations, temp_type_value, show_high_altitude, author_id: int, show_image=False, image_url=None):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.bot = bot
        self.api_key = api_key
        self.stations = stations
        self.temp_type_value = temp_type_value
        self.show_high_altitude = show_high_altitude
        self.show_details = False
        self.show_image = show_image
        self.image_url = image_url
        self.update_buttons()

    def update_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label in ["顯示詳細資訊", "隱藏詳細資訊"]:
                    child.label = "隱藏詳細資訊" if self.show_details else "顯示詳細資訊"
                    child.style = discord.ButtonStyle.secondary if self.show_details else discord.ButtonStyle.primary
                elif child.label in ["顯示氣溫圖", "隱藏氣溫圖"]:
                    child.label = "隱藏氣溫圖" if self.show_image else "顯示氣溫圖"
            elif isinstance(child, discord.ui.Select):
                val = f"{self.temp_type_value}_{'all' if self.show_high_altitude else 'no_high'}"
                for option in child.options:
                    option.default = (option.value == val)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def build_embed(self):
        is_today = self.temp_type_value in ["today_high", "today_low"]
        is_high = self.temp_type_value in ["now_high", "today_high"]

        results = []
        for st in self.stations:
            station_name = st.get('StationName', '未知')
            geo_info = st.get('GeoInfo', {})
            county = geo_info.get('CountyName', '')
            town = geo_info.get('TownName', '')
            altitude_str = geo_info.get('StationAltitude', '0')

            try:
                altitude = float(altitude_str)
            except ValueError:
                altitude = 0.0

            if not self.show_high_altitude and altitude > 1500:
                continue

            weather = st.get('WeatherElement', {})
            
            if self.temp_type_value == "today_high":
                daily_high = weather.get('DailyHigh') or weather.get('DailyExtreme', {}).get('DailyHigh') or {}
                temp_info = daily_high.get('TemperatureInfo') or {}
                temp_str = temp_info.get('AirTemperature', '-99')
                time_str = temp_info.get('Occurred_at', {}).get('DateTime', '')
            elif self.temp_type_value == "today_low":
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

            if temp_val <= -90.0:
                continue
            else:
                temp_display = f"{temp_val} °C"
                temp_sort = temp_val
                
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

        results.sort(key=lambda x: x['temp_sort'], reverse=is_high)
        display_results = results[:10]

        if is_today:
            message_content = "🌡️ 今日最高溫測站排行" if is_high else "❄️ 今日最低溫測站排行"
        else:
            message_content = "🌡️ 現在高溫排行" if is_high else "❄️ 現在低溫排行"
            
        if not self.show_high_altitude:
            message_content += " (排除高海拔地區)"
            
        embed = discord.Embed(color=0xff3846 if is_high else 0x3498db)
        
        lines = []
        for i, r in enumerate(display_results):
            temp_val = r['temp_sort']
            icon = "⚪️"
            if temp_val != 999.0 and temp_val != -999.0:
                if is_high:
                    if temp_val >= 38.0: icon = "🔴"
                    elif temp_val >= 36.0: icon = "🟠"
                    elif temp_val >= 32.0: icon = "🟡"
                else:
                    if temp_val <= 6.0: icon = "🟣"
                    elif temp_val <= 12.0: icon = "🔵"
                    elif temp_val <= 16.0: icon = "🟢"

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
        if not lines:
            embed.description = "目前尚無氣溫資料"
            
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")

        if self.show_image and self.image_url:
            embed.set_image(url=self.image_url)
            
        return message_content, embed

    @discord.ui.select(
        placeholder="選擇氣溫排行類型",
        options=[
            discord.SelectOption(label="現在最高溫", value="now_high_all", emoji="🥵"),
            discord.SelectOption(label="現在最高溫 (不含高海拔)", value="now_high_no_high"),
            discord.SelectOption(label="現在最低溫", value="now_low_all", emoji="🥶"),
            discord.SelectOption(label="現在最低溫 (不含高海拔)", value="now_low_no_high"),
            discord.SelectOption(label="今日最高溫", value="today_high_all", emoji="🌡️"),
            discord.SelectOption(label="今日最高溫 (不含高海拔)", value="today_high_no_high"),
            discord.SelectOption(label="今日最低溫", value="today_low_all", emoji="❄️"),
            discord.SelectOption(label="今日最低溫 (不含高海拔)", value="today_low_no_high")
        ],
        row=0
    )
    async def select_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        val = select.values[0]
        
        if val.startswith("now_high"): self.temp_type_value = "now_high"
        elif val.startswith("now_low"): self.temp_type_value = "now_low"
        elif val.startswith("today_high"): self.temp_type_value = "today_high"
        elif val.startswith("today_low"): self.temp_type_value = "today_low"
        
        self.show_high_altitude = val.endswith("all")
        self.update_buttons()
        content, embed = self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self)

    @discord.ui.button(label="顯示詳細資訊", style=discord.ButtonStyle.primary, row=1)
    async def toggle_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.show_details = not self.show_details
        self.update_buttons()
        content, embed = self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self)

    @discord.ui.button(label="顯示氣溫圖", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_image(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.show_image = not self.show_image
        if self.show_image and not self.image_url:
            timestamp = (int(datetime.now().timestamp()) // 300) * 300
            self.image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0038-001.jpg?t={timestamp}"
        self.update_buttons()
        content, embed = self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self)

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

    @app_commands.command(name="氣溫排行", description="🌡️ 查詢台灣各測站的現在溫度或今日極端溫列表 Temperature")
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

        await interaction.response.defer()
        
        try:
            data = await self.fetch_temp_data()
            if not data:
                await interaction.followup.send("⚠️ API 請求失敗或無法獲取資料。")
                return

            stations = data.get('records', {}).get('Station', [])
            
            if not stations:
                self.fetch_temp_data.invalidate_all()
                await interaction.followup.send("⚠️ 找不到有效的溫度資料。")
                return

            show_high_altitude = True
            if 高海拔 and 高海拔.value == 'no':
                show_high_altitude = False

            temp_type_value = temp_type.value

            show_image_initial = (氣溫圖.value == "yes") if 氣溫圖 else False
            image_url = None

            if show_image_initial:
                timestamp = (int(datetime.now().timestamp()) // 300) * 300
                image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0038-001.jpg?t={timestamp}"

            view = TempView(self.bot, self.api_key, stations, temp_type_value, show_high_altitude, interaction.user.id, show_image_initial, image_url)
            content, embed = view.build_embed()
            
            if not embed.description or embed.description == "目前尚無氣溫資料":
                self.fetch_temp_data.invalidate_all()

            await interaction.followup.send(content=content, embed=embed, view=view)

        except Exception as e:
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{e}")
            logger.error(f"❌ /氣溫排行 發生未預期的錯誤：{e}")

async def setup(bot):
    await bot.add_cog(TempCog(bot))
