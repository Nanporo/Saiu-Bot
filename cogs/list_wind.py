import discord
from discord.ext import commands
from discord import app_commands
import json
from datetime import datetime, timezone, timedelta
import io
import logging
from modules.cache import async_cache

logger = logging.getLogger(__name__)

def get_beaufort_scale(speed):
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

class WindView(discord.ui.View):
    def __init__(self, bot, stations, wind_type="avg", show_image=False):
        super().__init__(timeout=300)
        self.bot = bot
        self.stations = stations
        self.wind_type = wind_type
        self.show_details = False
        self.show_image = show_image
        self.cached_images = {"avg": None, "gust": None}
        self.cached_obs_times = {"avg": None, "gust": None}
        self.update_buttons()

    def update_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label in ["顯示詳細資訊", "隱藏詳細資訊"]:
                    child.label = "隱藏詳細資訊" if self.show_details else "顯示詳細資訊"
                    child.style = discord.ButtonStyle.secondary if self.show_details else discord.ButtonStyle.primary
                elif child.label in ["顯示觀測圖", "隱藏觀測圖"]:
                    child.label = "隱藏觀測圖" if self.show_image else "顯示觀測圖"
                elif child.label == "平均風":
                    child.disabled = (self.wind_type == "avg")
                elif child.label == "陣風":
                    child.disabled = (self.wind_type == "gust")

    async def fetch_latest_wind_image(self):
        # 目前時間 (UTC+8)
        now = datetime.now(timezone(timedelta(hours=8)))
        
        # 風力觀測圖固定為整點發布 (例如 14:00, 15:00)
        # 直接把時間切到當前小時的 00 分
        check_time = now.replace(minute=0, second=0, microsecond=0)
        
        max_attempts = 4  # 最多往前找 4 小時
        
        suffix = ".GWD.png" if self.wind_type == "avg" else ".GWD2.png"
        
        # 加入更完整的瀏覽器標頭，避免被防火牆 (WAF) 阻擋，並加入 Referer
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
            "Referer": "https://www.cwa.gov.tw/V8/C/W/OBS_Wind.html"
        }

        for _ in range(max_attempts):
            time_str = check_time.strftime("%Y-%m-%d_%H%M")
            image_url = f"https://www.cwa.gov.tw/Data/windspeed/{time_str}{suffix}"
            
            try:
                async with self.bot.session.get(image_url, headers=headers) as response:
                    logger.info(f"🌐 [圖片抓取] 風力觀測圖: {image_url} -> HTTP 狀態碼: {response.status}")
                    if response.status == 200:
                        image_bytes = await response.read()
                        discord_time = f"<t:{int(check_time.timestamp())}:f>"
                        return image_bytes, discord_time
            except Exception as e:
                logger.error(f"❌ 抓取風力觀測圖 {time_str} 發生錯誤: {e}")
                
            # 若找不到，往前推 1 小時繼續找
            check_time -= timedelta(hours=1)

        return None, "未知時間"

    async def build_embed(self):
        is_gust = self.wind_type == "gust"
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

            weather = st.get('WeatherElement', {})
            
            if is_gust:
                wind_info = weather.get('GustInfo', {})
                wind_str = wind_info.get('PeakGustSpeed', '-99')
                time_str = wind_info.get('Occurred_at', {}).get('DateTime', '')
            else:
                wind_str = weather.get('WindSpeed', '-99')
                time_str = st.get('ObsTime', {}).get('DateTime', '')

            try:
                wind_val = float(wind_str)
            except (ValueError, TypeError):
                continue

            if wind_val < 0.0:
                continue
            else:
                beaufort = get_beaufort_scale(wind_val)
                wind_display = f"{wind_val} m/s {beaufort}級"
                wind_sort = wind_val
                
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
                "wind_display": wind_display,
                "wind_sort": wind_sort,
                "time": time_format
            })

        results.sort(key=lambda x: x['wind_sort'], reverse=True)
        display_results = results[:10]

        message_content = "💨 現在前10名陣風排行" if is_gust else "💨 現在前10名平均風排行"
        embed = discord.Embed(color=0x3498db)
        
        lines = []
        for i, r in enumerate(display_results):
            wind_val = r['wind_sort']
            icon = "⚪"
            if wind_val >= 32.7: icon = "🟣"
            elif wind_val >= 24.5: icon = "🔴"
            elif wind_val >= 17.2: icon = "🟠"
            elif wind_val >= 10.8: icon = "🟡"
            else: icon = "🟢"

            num_emoji = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'][i]
            if i < 3:
                rank_str = ['`🥇`', '`🥈`', '`🥉`'][i]
                line = f"{num_emoji} `{icon} {r['wind_display']}` **{r['county']}{r['town']}** {rank_str}"
            else:
                line = f"{num_emoji} `{icon} {r['wind_display']}` **{r['county']}{r['town']}**"

            if self.show_details:
                line += f"\n>  {r['station']} | 海拔 {r['altitude']}m\n>  {r['time']}"
            lines.append(line)
        
        embed.description = "\n".join(lines)
        
        file = None
        if self.show_image:
            if self.cached_images[self.wind_type] is None:
                img_bytes, obs_time = await self.fetch_latest_wind_image()
                self.cached_images[self.wind_type] = img_bytes
                self.cached_obs_times[self.wind_type] = obs_time
                
            image_bytes = self.cached_images[self.wind_type]
            
            if image_bytes:
                file = discord.File(io.BytesIO(image_bytes), filename="wind.png")
                embed.set_image(url="attachment://wind.png")
            else:
                embed.description += "\n\n❌ **目前無法取得該風力觀測圖資料**"
            
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return message_content, embed, file

    @discord.ui.button(label="顯示詳細資訊", style=discord.ButtonStyle.primary, row=0)
    async def toggle_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.show_details = not self.show_details
        self.update_buttons()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="隱藏觀測圖", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_image(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.show_image = not self.show_image
        self.update_buttons()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="平均風", style=discord.ButtonStyle.secondary, row=0)
    async def btn_avg(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.wind_type = "avg"
        self.update_buttons()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="陣風", style=discord.ButtonStyle.secondary, row=0)
    async def btn_gust(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.wind_type = "gust"
        self.update_buttons()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])


class WindCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @async_cache(ttl_seconds=300)
    async def fetch_wind_data(self):
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?Authorization={self.api_key}&WeatherElement=WindSpeed,GustInfo"
        try:
            async with self.bot.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            logger.error(f"❌ 抓取風力排行資料失敗: {e}")
        return None

    @app_commands.command(name="風力排行", description="💨 查詢台灣各測站的現在風速列表與最新風力觀測圖")
    @app_commands.describe(
        風速類型="選擇要顯示平均風或陣風 (預設為平均風)",
        觀測圖="是否顯示觀測圖"
    )
    @app_commands.choices(風速類型=[
        app_commands.Choice(name="平均風", value="avg"),
        app_commands.Choice(name="陣風", value="gust")
    ], 觀測圖=[
        app_commands.Choice(name="顯示", value="yes"),
        app_commands.Choice(name="不顯示", value="no")
    ])
    async def wind_command(self, interaction: discord.Interaction, 風速類型: app_commands.Choice[str] = None, 觀測圖: app_commands.Choice[str] = None):
        if not self.api_key:
            await interaction.response.send_message("⚠️ 未設定 API Key，無法查詢資料。", ephemeral=True)
            return

        await interaction.response.defer()
        
        try:
            data = await self.fetch_wind_data()
            if not data:
                await interaction.followup.send("⚠️ API 請求失敗或無法獲取資料。")
                return

            stations = data.get('records', {}).get('Station', [])
            
            if not stations:
                self.fetch_wind_data.invalidate_all()
                await interaction.followup.send("⚠️ 找不到有效的風力資料。")
                return

            wind_type = 風速類型.value if 風速類型 else "avg"
            show_image_initial = 觀測圖 and 觀測圖.value == "yes"
            view = WindView(self.bot, stations, wind_type=wind_type, show_image=show_image_initial)
            content, embed, file = await view.build_embed()
            
            if file:
                await interaction.followup.send(content=content, embed=embed, view=view, file=file)
            else:
                await interaction.followup.send(content=content, embed=embed, view=view)

        except Exception as e:
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{e}")
            logger.error(f"❌ /風力排行 發生未預期的錯誤：{e}")

async def setup(bot):
    await bot.add_cog(WindCog(bot))