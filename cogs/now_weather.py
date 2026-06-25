import discord
from discord.ext import commands
from discord import app_commands
import json
import logging
from datetime import datetime, timezone, timedelta
from modules.location_matcher import match_location
from modules.cache import async_cache

logger = logging.getLogger(__name__)

# 這是顯示觀測數據的指令，天氣「預報」的指令在 weather/ 底下

class NowWeatherView(discord.ui.View):
    def __init__(self, stations, county_name, town_name, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.stations = stations
        self.county_name = county_name
        self.town_name = town_name
        self.current_station_id = stations[0].get("StationId")
        
        if len(stations) > 1:
            options = []
            # Discord Select 選單最多 25 個選項
            for st in stations[:25]:
                st_name = st.get("StationName", "未知")
                st_id = st.get("StationId", "")
                is_default = st_id == self.current_station_id
                options.append(discord.SelectOption(label=f"測站：{st_name}", value=st_id, default=is_default))
                
            self.select = discord.ui.Select(placeholder="選擇其他測站...", options=options)
            self.select.callback = self.select_callback
            self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.current_station_id = self.select.values[0]
        
        for option in self.select.options:
            option.default = (option.value == self.current_station_id)
            
        selected_st = next((st for st in self.stations if st.get("StationId") == self.current_station_id), self.stations[0])
        content, embed = self.build_embed(selected_st)
        await interaction.edit_original_response(content=content, embed=embed, view=self)

    def format_val(self, val, unit=""):
        if val is None or val == "":
            return "未知"
            
        val_str = str(val).strip().upper()
        if val_str == "X":
            return "儀器故障"
        if val_str == "T":
            return "雨跡"
            
        try:
            f_val = float(val)
            if f_val in [-98.0, -980.0]:
                return "連續 6 小時無降水"
            elif f_val <= -90.0:
                return "缺值/異常"
            # :g 可以自動去除小數點後多餘的零
            return f"{f_val:g} {unit}".strip()
        except ValueError:
            return f"{val} {unit}".strip()

    def format_wind_direction(self, val):
        if val is None or val == "":
            return "未知"
            
        if str(val).strip().upper() == "X":
            return "儀器故障"
            
        try:
            f_val = float(val)
            if f_val == 990.0:
                return "風向不定"
            elif f_val <= -90.0:
                return "缺值/異常"
                
            dirs = [
                ("北風", "↓"), ("北北東風", "↙"), ("東北風", "↙"), ("東北東風", "↙"),
                ("東風", "←"), ("東南東風", "↖"), ("東南風", "↖"), ("南南東風", "↖"),
                ("南風", "↑"), ("南南西風", "↗"), ("西南風", "↗"), ("西西南風", "↗"),
                ("西風", "→"), ("西北西風", "↘"), ("西北風", "↘"), ("北北西風", "↘")
            ]
            idx = int((f_val + 11.25) / 22.5) % 16
            name, arrow = dirs[idx]
            
            return f"{f_val:g} 度\n{name} `{arrow}`"
        except ValueError:
            return f"{val} 度".strip()

    def build_embed(self, st):
        st_name = st.get("StationName", "未知")
        st_id = st.get("StationId", "")
        obs_time_str = st.get("ObsTime", {}).get("DateTime", "")
        
        geo_info = st.get("GeoInfo", {})
        altitude = geo_info.get("StationAltitude", "未知")
        
        we = st.get("WeatherElement", {})
        weather = we.get("Weather", "-99")
        
        # 降雨量可能位於 Now 內，也可能直接位於 WeatherElement 內
        precip = we.get("Now", {}).get("Precipitation", "-99")
        if precip == "-99" or precip is None:
            precip = we.get("Precipitation", "-99")
            
        wdir = we.get("WindDirection", "-99")
        wspd = we.get("WindSpeed", "-99")
        temp = we.get("AirTemperature", "-99")
        rh = we.get("RelativeHumidity", "-99")
        pres = we.get("AirPressure", "-99")
        uv = we.get("UVIndex", "-99")
        
        gust_info = we.get("GustInfo", {})
        peak_gust = gust_info.get("PeakGustSpeed", "-99")
        
        daily_extreme = we.get("DailyExtreme", {})
        daily_high = daily_extreme.get("DailyHigh", {}) or we.get("DailyHigh", {})
        daily_low = daily_extreme.get("DailyLow", {}) or we.get("DailyLow", {})
        
        high_temp = daily_high.get("TemperatureInfo", {}).get("AirTemperature", "-99")
        low_temp = daily_low.get("TemperatureInfo", {}).get("AirTemperature", "-99")

        # 整理觀測時間
        try:
            dt = datetime.fromisoformat(obs_time_str)
            obs_time_format = f"<t:{int(dt.timestamp())}:f>"
        except Exception:
            obs_time_format = obs_time_str if obs_time_str and str(obs_time_str) != "-99" else "未知時間"

        if str(weather) not in ["-99", "-99.0", "-999", "-999.0", "-990", "-990.0", ""]:
            desc_title = f"**{self.county_name}{self.town_name}** 現在{weather}"
        else:
            desc_title = f"**{self.county_name}{self.town_name}** 的即時天氣觀測"

        embed = discord.Embed(
            title="",
            description=f"{desc_title}\n測站：**{st_name}** (`{st_id}`) | 海拔 `{altitude} m`\n觀測時間：{obs_time_format}\n\n",
            color=0x1abc9c
        )
        
        embed.add_field(name="🌡️ 氣溫", value=self.format_val(temp, "°C"), inline=True)
        embed.add_field(name="📈 今日最高溫", value=self.format_val(high_temp, "°C"), inline=True)
        embed.add_field(name="📉 今日最低溫", value=self.format_val(low_temp, "°C"), inline=True)
        
        embed.add_field(name="☔ 降雨量 (本日)", value=self.format_val(precip, "mm"), inline=True)
        embed.add_field(name="💧 相對濕度", value=self.format_val(rh, "%"), inline=True)
        embed.add_field(name="🎈 氣壓", value=self.format_val(pres, "hPa"), inline=True)
        
        embed.add_field(name="🧭 風向", value=self.format_wind_direction(wdir), inline=True)
        embed.add_field(name="💨 風速", value=self.format_val(wspd, "m/s"), inline=True)
        embed.add_field(name="🌪️ 最大陣風", value=self.format_val(peak_gust, "m/s"), inline=True)

        if str(uv) not in ["-99", "-99.0", "-999", "-999.0", "-990", "-990.0", ""]:
            embed.add_field(name="☀️ 紫外線指數", value=self.format_val(uv, ""), inline=True)

        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return "🌤️ 即時天氣觀測查詢", embed

class NowWeatherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @async_cache(ttl_seconds=300)
    async def fetch_now_weather(self):
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?Authorization={self.api_key}"
        try:
            async with self.bot.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            logger.error(f"❌ 抓取現在天氣資料失敗: {e}")
        return None

    @app_commands.command(name="現在天氣", description="🌤️ 查詢指定鄉鎮市區的最新天氣觀測資料")
    @app_commands.describe(鄉鎮市區="請輸入縣市與鄉鎮市區（例如：臺北市信義區）")
    async def now_weather_command(self, interaction: discord.Interaction, 鄉鎮市區: str):
        if not self.api_key:
            await interaction.response.send_message("⚠️ 未設定 API Key，無法查詢資料。", ephemeral=True)
            return

        loc_val, error_msg = match_location(鄉鎮市區)
        if error_msg:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        await interaction.response.defer()

        county_name = loc_val[:3]
        town_name = loc_val[3:]

        try:
            data = await self.fetch_now_weather()
            if not data:
                await interaction.followup.send("❌ API 請求失敗或無法獲取資料。", ephemeral=True)
                return
        except Exception as e:
            logger.error(f"❌ 查詢現在天氣失敗: {e}")
            await interaction.followup.send(f"❌ 發生錯誤：{e}", ephemeral=True)
            return

        stations = data.get("records", {}).get("Station", [])
        
        target_stations = []
        for st in stations:
            geo_info = st.get("GeoInfo", {})
            if geo_info.get("CountyName") == county_name and geo_info.get("TownName") == town_name:
                target_stations.append(st)

        if not target_stations:
            await interaction.followup.send(f"❌ 找不到位於 **{county_name}{town_name}** 內的氣象觀測站資料。\n(註：部分鄉鎮可能未設立自動測站)", ephemeral=True)
            return

        view = NowWeatherView(target_stations, county_name, town_name, interaction.user.id)
        content, embed = view.build_embed(target_stations[0])
        
        if len(target_stations) > 1:
            await interaction.followup.send(content=content, embed=embed, view=view)
        else:
            # 如果只有一個測站，不顯示下拉選單
            await interaction.followup.send(content=content, embed=embed)

    @now_weather_command.autocomplete("鄉鎮市區")
    async def now_weather_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        from modules.location_matcher import get_town_autocomplete
        choices = get_town_autocomplete(current)
        return [app_commands.Choice(name=c, value=c) for c in choices]

async def setup(bot):
    await bot.add_cog(NowWeatherCog(bot))