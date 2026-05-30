import discord
from discord.ext import commands
from discord import app_commands
import json
from modules.ownercheck import is_owner

class TestCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @app_commands.command(name="資料", description="（限擁有者）測試並顯示各氣象模組抓取到的前三筆數據狀況")
    async def test_data_command(self, interaction: discord.Interaction):
        # 權限檢查
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="📊 氣象資料抓取測試狀態",
            description="目前各氣象模組抓取到的最新數據（排序後的前三筆最高值）",
            color=0x9b59b6
        )

        # ================= 1. 降雨預報 =================
        rain_cog = self.bot.get_cog("RainForecastCog")
        if rain_cog and rain_cog.latest_rain_data:
            try:
                # 過濾空字串並轉為浮點數，同時保留網格索引
                values_with_index = []
                for idx, v in enumerate(rain_cog.latest_rain_data):
                    v_strip = v.strip()
                    if v_strip:
                        try:
                            val = float(v_strip)
                            if val >= 0.0:  # 排除 -99 等無效值
                                values_with_index.append((val, idx))
                        except ValueError:
                            pass
                            
                values_with_index.sort(key=lambda x: x[0], reverse=True)
                top_3 = values_with_index[:3]
                
                # 建立不重複的鄉鎮中心座標對照表
                unique_towns = {}
                for lst in rain_cog.town_mapping.values():
                    for fullname, lat, lon in lst:
                        if lat is not None and lon is not None:
                            unique_towns[fullname] = (lat, lon)
                            
                lines = []
                for i, (val, idx) in enumerate(top_3):
                    grid_x = idx % 441
                    grid_y = idx // 441
                    grid_lon = 117.975 + grid_x * 0.0125
                    grid_lat = 19.975 + grid_y * 0.0125
                    
                    nearest_town = "未知地區"
                    min_dist = float('inf')
                    for town_name, (t_lat, t_lon) in unique_towns.items():
                        # 計算該網格中心與所有鄉鎮市區中心的直線距離 (經緯度平方差)
                        dist = (t_lat - grid_lat)**2 + (t_lon - grid_lon)**2
                        if dist < min_dist:
                            min_dist = dist
                            nearest_town = town_name
                            
                    lines.append(f"`{i+1}.` {val} mm - **{nearest_town}** 附近")
                
                text = "\n".join(lines)
                embed.add_field(name="🌧️ 1小時網格降雨預報", value=text or "無資料", inline=False)
            except Exception as e:
                embed.add_field(name="🌧️ 1小時網格降雨預報", value=f"資料解析失敗: {e}", inline=False)
        else:
            embed.add_field(name="🌧️ 1小時網格降雨預報", value="尚未抓取或無快取資料", inline=False)

        if not self.api_key:
            embed.add_field(name="⚠️ 錯誤", value="未設定 API Key，無法查詢氣溫與累積雨量", inline=False)
            await interaction.followup.send(embed=embed)
            return

        # ================= 2. 今日氣溫 =================
        url_temp = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?Authorization={self.api_key}&WeatherElement=DailyHigh"
        try:
            async with self.bot.session.get(url_temp) as res:
                if res.status == 200:
                    data = await res.json()
                    stations = data.get('records', {}).get('Station', [])
                    temp_list = []
                    for st in stations:
                        st_name = st.get('StationName', '未知')
                        t_str = st.get('WeatherElement', {}).get('DailyExtreme', {}).get('DailyHigh', {}).get('TemperatureInfo', {}).get('AirTemperature', '-99')
                        try:
                            t_val = float(t_str)
                            if t_val > -90.0:
                                temp_list.append((st_name, t_val))
                        except ValueError:
                            pass
                    temp_list.sort(key=lambda x: x[1], reverse=True)
                    top_3_temp = temp_list[:3]
                    text = "\n".join([f"`{i+1}.` {t[0]} - {t[1]} °C" for i, t in enumerate(top_3_temp)])
                    embed.add_field(name="🌡️ 今日氣溫 (最高)", value=text or "無資料", inline=False)
                else:
                    embed.add_field(name="🌡️ 今日氣溫", value=f"API 請求失敗 ({res.status})", inline=False)
        except Exception as e:
            embed.add_field(name="🌡️ 今日氣溫", value=f"錯誤: {e}", inline=False)

        # ================= 3. 今日雨量 =================
        url_rain = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001?Authorization={self.api_key}&RainfallElement=Now"
        try:
            async with self.bot.session.get(url_rain) as res:
                if res.status == 200:
                    data = await res.json()
                    stations = data.get('records', {}).get('Station', [])
                    rain_list = []
                    for st in stations:
                        st_name = st.get('StationName', '未知')
                        r_str = st.get('RainfallElement', {}).get('Now', {}).get('Precipitation', '-99')
                        try:
                            r_val = float(r_str)
                            if r_val >= 0.0:
                                rain_list.append((st_name, r_val))
                        except ValueError:
                            pass
                    rain_list.sort(key=lambda x: x[1], reverse=True)
                    top_3_rain = rain_list[:3]
                    text = "\n".join([f"`{i+1}.` {r[0]} - {r[1]} mm" for i, r in enumerate(top_3_rain)])
                    embed.add_field(name="☔ 今日累積雨量 (最高)", value=text or "無資料", inline=False)
                else:
                    embed.add_field(name="☔ 今日累積雨量", value=f"API 請求失敗 ({res.status})", inline=False)
        except Exception as e:
            embed.add_field(name="☔ 今日累積雨量", value=f"錯誤: {e}", inline=False)

        # 發送結果
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(TestCog(bot))