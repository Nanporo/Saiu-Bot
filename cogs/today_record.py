import discord
from discord.ext import commands
from discord import app_commands
import json
import asyncio
from datetime import datetime, timezone, timedelta
from modules.cwa_api import fetch_daily_extreme_temperatures, fetch_current_rainfall

class TodayRecordCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @app_commands.command(name="今日氣象記錄", description="查詢今日綜合氣象記錄看板 (溫度、雨量之最)")
    async def today_record_command(self, interaction: discord.Interaction):
        if not self.api_key:
            await interaction.response.send_message("⚠️ 未設定 API Key，無法查詢資料。", ephemeral=True)
            return

        # 避免 API 回應過慢導致超時報錯
        await interaction.response.defer()

        try:
            # 使用 asyncio.gather 同時請求兩支 API 以節省時間
            stations_obs, stations_rain = await asyncio.gather(
                fetch_daily_extreme_temperatures(self.bot.session, self.api_key),
                fetch_current_rainfall(self.bot.session, self.api_key)
            )
            
            if not stations_obs and not stations_rain:
                await interaction.followup.send("⚠️ API 請求失敗或無資料，請稍後再試。")
                return

            # 初始極值
            max_temp = -999.0
            max_temp_st = None
            
            min_temp = 999.0
            min_temp_st = None
            
            # 解析局屬測站 (O-A0001-001) 尋找氣溫
            for st in stations_obs:
                st_name = st.get('StationName', '未知')
                geo_info = st.get('GeoInfo', {})
                county = geo_info.get('CountyName', '')
                town = geo_info.get('TownName', '')
                
                weather = st.get('WeatherElement', {})
                daily_high = weather.get('DailyExtreme', {}).get('DailyHigh', {})
                daily_low = weather.get('DailyExtreme', {}).get('DailyLow', {})
                
                # 1. 找最高溫
                temp_high_str = daily_high.get('TemperatureInfo', {}).get('AirTemperature', '-99')
                time_high_str = daily_high.get('TemperatureInfo', {}).get('Occurred_at', {}).get('DateTime', '')
                try:
                    t_h = float(temp_high_str)
                    if t_h > -90.0 and t_h > max_temp:
                        max_temp = t_h
                        try:
                            try:
                                dt = datetime.fromisoformat(time_high_str)
                            except ValueError:
                                dt = datetime.strptime(time_high_str, "%Y-%m-%d %H:%M:%S")
                            time_fmt = f"<t:{int(dt.timestamp())}:t>"
                        except Exception:
                            time_fmt = "時間未知"
                        max_temp_st = f"**{county}{town}** ({st_name})\n{time_fmt}"
                except ValueError:
                    pass

                # 2. 找最低溫
                temp_low_str = daily_low.get('TemperatureInfo', {}).get('AirTemperature', '99')
                time_low_str = daily_low.get('TemperatureInfo', {}).get('Occurred_at', {}).get('DateTime', '')
                try:
                    t_l = float(temp_low_str)
                    if t_l > -90.0 and t_l < min_temp:
                        min_temp = t_l
                        try:
                            try:
                                dt = datetime.fromisoformat(time_low_str)
                            except ValueError:
                                dt = datetime.strptime(time_low_str, "%Y-%m-%d %H:%M:%S")
                            time_fmt = f"<t:{int(dt.timestamp())}:t>"
                        except Exception:
                            time_fmt = "時間未知"
                        min_temp_st = f"**{county}{town}** ({st_name})\n{time_fmt}"
                except ValueError:
                    pass

            # 解析雨量測站 (O-A0002-001) 尋找累積雨量
            max_rain = -1.0
            max_rain_st = None

            for st in stations_rain:
                st_name = st.get('StationName', '未知')
                geo_info = st.get('GeoInfo', {})
                county = geo_info.get('CountyName', '')
                town = geo_info.get('TownName', '')
                
                precip_str = st.get('RainfallElement', {}).get('Now', {}).get('Precipitation', '-99')
                try:
                    r = float(precip_str)
                    if r > 0.0 and r > max_rain:
                        max_rain = r
                        max_rain_st = f"**{county}{town}** ({st_name})"
                except ValueError:
                    pass

            # 建立 Embed
            message_content = "🏆 今日氣象之最 (綜合記錄)"
            embed = discord.Embed(
                title="",
                description="目前全台各測站的今日極值觀測結果",
                color=0x1abc9c
            )
            
            if max_temp_st:
                embed.add_field(name="🌡️ 最高溫", value=f"`{max_temp} °C`\n{max_temp_st}", inline=True)
            else:
                embed.add_field(name="🌡️ 最高溫", value="無資料", inline=True)

            if min_temp_st:
                embed.add_field(name="❄️ 最低溫", value=f"`{min_temp} °C`\n{min_temp_st}", inline=True)
            else:
                embed.add_field(name="❄️ 最低溫", value="無資料", inline=True)

            # 空白欄位以保持排版整齊 (Discord Embed 每行 3 欄位)
            embed.add_field(name="\u200b", value="\u200b", inline=True)

            if max_rain_st:
                embed.add_field(name="☔ 最大累積雨量", value=f"`{max_rain} mm`\n{max_rain_st}", inline=True)
            else:
                embed.add_field(name="☔ 最大累積雨量", value="今日尚無顯著降雨", inline=True)

            current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
            embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/TWERG-Bot/main/photos/cwa_logo.png")

            await interaction.followup.send(content=message_content, embed=embed)

        except Exception as e:
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{e}")
            print(f"❌ /今日氣象記錄 發生未預期的錯誤：{e}")

async def setup(bot):
    await bot.add_cog(TodayRecordCog(bot))