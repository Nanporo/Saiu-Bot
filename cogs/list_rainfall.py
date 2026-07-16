import discord
from discord.ext import commands
from discord import app_commands
import json
import io
from datetime import datetime, timezone, timedelta
import logging
from modules.cache import async_cache

logger = logging.getLogger(__name__)

class RainfallView(discord.ui.View):
    def __init__(self, bot, api_key, results, author_id: int, initial_map_type="none"):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.bot = bot
        self.api_key = api_key
        self.results = results
        self.show_details = False
        self.current_map_type = initial_map_type

        for option in self.children[-1].options:
            option.default = option.value == self.current_map_type

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    @async_cache(ttl_seconds=300)
    async def fetch_rainfall_map(self, map_type):
        now = datetime.now(timezone(timedelta(hours=8)))
        start_time = now
        check_time = start_time.replace(minute=0, second=0, microsecond=0)
        
        max_attempts = 12
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
            "Referer": "https://www.cwa.gov.tw/V8/C/W/OBS_Rainfall.html"
        }
        
        for _ in range(max_attempts):
            time_str = check_time.strftime("%Y-%m-%d_%H00")
            image_url = f"https://www.cwa.gov.tw/Data/rainfall/rain_town/{time_str}.{map_type}.png"
            
            try:
                async with self.bot.session.get(image_url, headers=headers) as response:
                    logger.info(f"🔍 [抓取狀態] 正在檢查雨量圖: {image_url}")
                    if response.status == 200:
                        logger.info(f"⬇️ [抓取狀態] 準備下載雨量圖: {image_url}")
                        image_bytes = await response.read()
                        logger.info(f"✅ [抓取狀態] 下載成功 ({len(image_bytes)/1024:.1f} KB)")
                        discord_time = f"<t:{int(check_time.timestamp())}:f>"
                        return image_bytes, discord_time, image_url
            except Exception as e:
                logger.error(f"❌ [抓取狀態] 抓取雨量圖 {time_str} 發生錯誤: {e!r}")
                
            check_time -= timedelta(hours=1)

        return None, "未知時間", None

    async def build_embed(self):
        message_content = "☔ 今日累積雨量測站排行"
        embed = discord.Embed(color=0x3498db)
        
        lines = []
        for i, r in enumerate(self.results[:10]):
            # 決定降雨量特報燈號
            precip_val = r['precip']
            icon = "💧"
            if precip_val >= 500.0:
                icon = "🟣"
            elif precip_val >= 350.0:
                icon = "🔴"
            elif precip_val >= 200.0:
                icon = "🟠"
            elif precip_val >= 80.0:
                icon = "🟡"

            num_emoji = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'][i]
            if i < 3:
                rank_str = ['`🥇`', '`🥈`', '`🥉`'][i]
                line = f"{num_emoji} `{icon} {precip_val} mm` **{r['county']}{r['town']}** {rank_str}"
            else:
                line = f"{num_emoji} `{icon} {precip_val} mm` **{r['county']}{r['town']}**"

            if self.show_details:
                line += f"\n>  {r['station']}"
            lines.append(line)
        
        embed.description = "\n".join(lines)
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")

        file = None
        if self.current_map_type != "none":
            if self.current_map_type in ["O-A0040-001", "O-A0040-002"]:
                timestamp = (int(datetime.now().timestamp()) // 300) * 300
                product_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/{self.current_map_type}.jpg?t={timestamp}"
                embed.set_image(url=product_url)
            else:
                image_bytes, obs_time, _ = await self.fetch_rainfall_map(self.current_map_type)
                if image_bytes:
                    file = discord.File(io.BytesIO(image_bytes), filename="rainfall_map.png")
                    embed.set_image(url="attachment://rainfall_map.png")
                else:
                    embed.description += "\n\n❌ **目前無法取得該雨量分布圖資料**"
            
        return message_content, embed, file

    def update_buttons(self):
        for child in self.children:
            if getattr(child, "label", None) in ["顯示詳細資訊", "隱藏詳細資訊"]:
                if self.show_details:
                    child.label = "隱藏詳細資訊"
                    child.style = discord.ButtonStyle.secondary
                else:
                    child.label = "顯示詳細資訊"
                    child.style = discord.ButtonStyle.primary

    @discord.ui.button(label="顯示詳細資訊", style=discord.ButtonStyle.primary, row=1)
    async def toggle_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.show_details = not self.show_details
        if self.show_details:
            button.label = "隱藏詳細資訊"
            button.style = discord.ButtonStyle.secondary
        else:
            button.label = "顯示詳細資訊"
            button.style = discord.ButtonStyle.primary
            
        content, embed, file = await self.build_embed()
        await interaction.response.edit_message(content=content, embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.select(
        placeholder="選擇要顯示的雨量圖",
        row=0,
        options=[
            discord.SelectOption(label="不顯示圖片", value="none"),
            discord.SelectOption(label="日累計雨量圖", value="O-A0040-002"),
            discord.SelectOption(label="日累計雨量圖 (大間距)", value="O-A0040-001"),
            discord.SelectOption(label="當日累計雨量鄉鎮分布圖", value="rain_1d"),
            discord.SelectOption(label="當日最大短延時雨量鄉鎮分布圖", value="rain_max_short")
        ]
    )
    async def select_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        self.current_map_type = select.values[0]
        
        for option in select.options:
            option.default = option.value == self.current_map_type
            
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

class RainfallCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @async_cache(ttl_seconds=300)
    async def fetch_rainfall_data(self):
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001?Authorization={self.api_key}&RainfallElement=Now"
        try:
            async with self.bot.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            logger.error(f"❌ 抓取雨量資料失敗: {e!r}")
        return None

    @app_commands.command(name="雨量排行", description="🌧️ 查詢今日台灣各測站的累積降雨列表 Rainfall")
    @app_commands.describe(
        雨量圖="選擇要顯示的雨量分布圖（預設不顯示）"
    )
    @app_commands.choices(雨量圖=[
        app_commands.Choice(name="不顯示圖片", value="none"),
        app_commands.Choice(name="日累計雨量圖", value="O-A0040-002"),
        app_commands.Choice(name="日累計雨量圖 (大間距)", value="O-A0040-001"),
        app_commands.Choice(name="當日累計雨量鄉鎮分布圖", value="rain_1d"),
        app_commands.Choice(name="當日最大短延時雨量鄉鎮分布圖", value="rain_max_short")
    ])
    async def rainfall_command(self, interaction: discord.Interaction, 雨量圖: app_commands.Choice[str] = None):
        if not self.api_key:
            await interaction.response.send_message("⚠️ 未設定 API Key，無法查詢資料。", ephemeral=True)
            return

        # 避免 API 回應過慢導致超時報錯
        await interaction.response.defer()

        map_type_val = 雨量圖.value if 雨量圖 else "none"

        try:
            data = await self.fetch_rainfall_data()
            if not data:
                await interaction.followup.send("⚠️ API 請求失敗或無法獲取資料。")
                return

            stations = data.get('records', {}).get('Station', [])

            results = []
            for st in stations:
                station_name = st.get('StationName', '未知')
                geo_info = st.get('GeoInfo', {})
                county = geo_info.get('CountyName', '')
                town = geo_info.get('TownName', '')
                
                precip_info = st.get('RainfallElement', {}).get('Now', {})
                precip_str = precip_info.get('Precipitation', '-99')

                try:
                    precip_val = float(precip_str)
                except ValueError:
                    continue

                # 排除氣象署資料的無效值（例如 -99.0 或 -998.0）與無雨量的測站 (0.0)
                if precip_val <= 0.0:
                    continue
                    
                results.append({
                    "station": station_name,
                    "county": county,
                    "town": town,
                    "precip": precip_val
                })

            if not results:
                await interaction.followup.send("⚠️ 目前尚無大於 0.0 mm 的雨量資料。")
                return

            results.sort(key=lambda x: x['precip'], reverse=True)
            
            view = RainfallView(self.bot, self.api_key, results, interaction.user.id, map_type_val)
            content, embed, file = await view.build_embed()
            await interaction.followup.send(content=content, embed=embed, view=view, file=file if file else discord.utils.MISSING)

        except Exception as e:
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{e!r}")
            logger.error(f"❌ /今日雨量排行 發生未預期的錯誤：{e!r}")

    async def refresh_message(self, interaction: discord.Interaction, message: discord.Message, cmd_name: str):
        await interaction.response.defer(ephemeral=True)
        data = await self.fetch_rainfall_data()
        if not data:
            await interaction.followup.send("❌ 無法獲取新資料。", ephemeral=True)
            return
        stations = data.get('records', {}).get('Station', [])
        results = []
        for st in stations:
            station_name = st.get('StationName', '未知')
            geo_info = st.get('GeoInfo', {})
            county = geo_info.get('CountyName', '')
            town = geo_info.get('TownName', '')
            precip_info = st.get('RainfallElement', {}).get('Now', {})
            precip_str = precip_info.get('Precipitation', '-99')
            try: precip_val = float(precip_str)
            except ValueError: continue
            if precip_val <= 0.0: continue
            results.append({"station": station_name, "county": county, "town": town, "precip": precip_val})
        if not results:
            await interaction.followup.send("⚠️ 目前尚無大於 0.0 mm 的雨量資料。", ephemeral=True)
            return
        results.sort(key=lambda x: x['precip'], reverse=True)
        
        map_type_val = "none"
        show_details = False
        for row in message.components:
            for child in row.children:
                if getattr(child, "type", None) == discord.ComponentType.select:
                    for opt in child.options:
                        if opt.default: map_type_val = opt.value
                elif getattr(child, "type", None) == discord.ComponentType.button:
                    if child.label == "隱藏詳細資訊": show_details = True
                    
        view = RainfallView(self.bot, self.api_key, results, interaction.user.id, map_type_val)
        view.show_details = show_details
        view.update_buttons()
        content, embed, file = await view.build_embed()
        await message.edit(content=content, embed=embed, view=view, attachments=[file] if file else [])
        await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)

async def setup(bot):
    await bot.add_cog(RainfallCog(bot))
