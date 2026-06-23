import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
import io
import logging
import json

logger = logging.getLogger(__name__)

class SpaceWeatherView(discord.ui.View):
    def __init__(self, cog, interaction):
        super().__init__(timeout=300)
        self.cog = cog
        self.original_interaction = interaction
        self.current_mode = "overview"
        
        # 設定預設選項
        for option in self.children[0].options:
            option.default = (option.value == self.current_mode)

    @discord.ui.select(
        placeholder="選擇太空天氣資訊",
        options=[
            discord.SelectOption(label="太空天氣概覽", value="overview", description="顯示目前影響與今日預報", emoji="🌌"),
            discord.SelectOption(label="電離層電波吸收", value="ionosphere", description="顯示 D 區電波吸收預測圖", emoji="📡"),
            discord.SelectOption(label="全球地磁擾動指數", value="kp_index", description="顯示全球地磁擾動指數 (Kp) 預測圖", emoji="📈")
        ]
    )
    async def select_mode(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        
        self.current_mode = select.values[0]
        
        for option in select.options:
            option.default = (option.value == self.current_mode)
            
        if self.current_mode == "overview":
            content, embed, file = await self.cog.build_overview_embed()
        elif self.current_mode == "kp_index":
            content, embed, file = await self.cog.build_kp_embed()
        else:
            content, embed, file = await self.cog.build_ionosphere_embed()
            
        if file:
            await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file])
        else:
            await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[])


class IonosphereCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def fetch_latest_ionosphere_image(self):
        # 太空天氣 (SWOO) 的檔案時間為 UTC
        now_utc = datetime.now(timezone.utc)
        
        # 扣除 2 分鐘後取 5 的倍數分
        start_time = now_utc - timedelta(minutes=2)
        minute = (start_time.minute // 5) * 5
        check_time_utc = start_time.replace(minute=minute, second=0, microsecond=0)
        
        max_attempts = 12  # 最多往前找 1 小時
        
        for _ in range(max_attempts):
            time_str = check_time_utc.strftime("%Y%m%d_%H%M")
            image_url = f"https://swoo.cwa.gov.tw/V2/img/Series/CWA_Drap_Nearby_{time_str}.png"
            
            try:
                async with self.bot.session.get(image_url) as response:
                    logger.info(f"🌐 [圖片抓取] 電離層電波吸收: {image_url} -> HTTP 狀態碼: {response.status}")
                    # 檢查圖片是否存在
                    if response.status == 200 and 'image' in response.headers.get('Content-Type', ''):
                        image_bytes = await response.read()
                        # 轉換為 Discord 時間戳顯示 (Discord 會自動轉回使用者的本地時間)
                        discord_time = f"<t:{int(check_time_utc.timestamp())}:f>"
                        return image_bytes, discord_time
            except Exception as e:
                logger.error(f"❌ 抓取電離層電波吸收 {time_str} 發生錯誤: {e}")
                
            # 若找不到，往前推 5 分鐘
            check_time_utc -= timedelta(minutes=5)

        return None, "未知時間"

    async def fetch_latest_kp_image(self):
        # Kp 指數的圖片檔名格式為 CWA_NOAA_Kp_YYYYMMDD.png (UTC 時間)
        now_utc = datetime.now(timezone.utc)
        
        for days_back in range(3):
            check_date = now_utc - timedelta(days=days_back)
            date_str = check_date.strftime("%Y%m%d")
            image_url = f"https://swoo.cwa.gov.tw/V2/img/Series/CWA_NOAA_Kp_{date_str}.png"
            
            try:
                async with self.bot.session.get(image_url) as response:
                    logger.info(f"🌐 [圖片抓取] 全球地磁擾動指數: {image_url} -> HTTP 狀態碼: {response.status}")
                    if response.status == 200 and 'image' in response.headers.get('Content-Type', ''):
                        image_bytes = await response.read()
                        discord_time = f"<t:{int(check_date.timestamp())}:D>"
                        return image_bytes, discord_time
            except Exception as e:
                logger.error(f"❌ 抓取全球地磁擾動指數 {date_str} 發生錯誤: {e}")

        return None, "未知時間"

    async def fetch_space_weather_info(self):
        url = "https://swoo.cwa.gov.tw/json/SWInfo.json"
        try:
            async with self.bot.session.get(url, timeout=10) as res:
                if res.status == 200:
                    # 使用 text() 取代 json() 避免 content-type 錯誤，再手動轉 json
                    text = await res.text()
                    data = json.loads(text)
                    return data
        except Exception as e:
            logger.error(f"❌ 抓取太空天氣概覽發生錯誤: {e}")
        return {}

    async def build_overview_embed(self):
        data = await self.fetch_space_weather_info()
        
        forecast = data.get("forecast", "無資料")
        
        twdi = data.get("TWDI", "無資料")
        dst = data.get("DST", "無資料")
        kidx = data.get("kidx", "無資料")
        ssn = data.get("ssn", "無資料")
        sfx = data.get("sfx", "無資料")
        sa = data.get("SA", "無資料")

        embed = discord.Embed(
            title="",
            color=0x9b59b6
        )
        
        embed.add_field(name="🇹🇼 臺灣地磁擾動", value=f"{twdi} nT", inline=True)
        embed.add_field(name="🌍 全球地磁擾動", value=f"{dst} nT", inline=True)
        embed.add_field(name="📈 全球地磁指數", value=f"{kidx}", inline=True)
        
        embed.add_field(name="🌞 太陽黑子數", value=f"{ssn}", inline=True)
        embed.add_field(name="☢️ 太陽輻射 (F10.7)", value=f"{sfx} sfu", inline=True)
        embed.add_field(name="💡 太陽活動狀態", value=f"{sa}", inline=True)


        embed.add_field(name="", value=f"```text\n{forecast}\n```", inline=False)
        
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return "🌌 太空天氣資訊", embed, None

    async def build_ionosphere_embed(self):
        image_bytes, obs_time = await self.fetch_latest_ionosphere_image()
        
        embed = discord.Embed(
            title="",
            description=f"**電離層電波吸收** (D-RAP)\n觀測時間：{obs_time}",
            color=0x9b59b6
        )
        
        file = None
        if image_bytes:
            file = discord.File(io.BytesIO(image_bytes), filename="ionosphere.png")
            embed.set_image(url="attachment://ionosphere.png")
        else:
            embed.description += "\n\n❌ **目前無法取得電離層電波吸收資料**"
            
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"NOAA / 中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/NOAA.png")
        
        return "📡 電離層電波吸收查詢", embed, file

    async def build_kp_embed(self):
        image_bytes, obs_time = await self.fetch_latest_kp_image()
        
        embed = discord.Embed(
            title="",
            description=f"**全球地磁擾動指數** (Kp Index)\n預報日期：{obs_time}",
            color=0x9b59b6
        )
        
        file = None
        if image_bytes:
            file = discord.File(io.BytesIO(image_bytes), filename="kp_index.png")
            embed.set_image(url="attachment://kp_index.png")
        else:
            embed.description += "\n\n❌ **目前無法取得全球地磁擾動指數資料**"
            
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"NOAA / 中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/NOAA.png")
        
        return "📈 全球地磁擾動指數查詢", embed, file

    @app_commands.command(name="太空天氣", description="🌌 查詢最新的太空天氣概覽")
    async def space_weather_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        view = SpaceWeatherView(self, interaction)
        content, embed, file = await self.build_overview_embed()
        
        if file:
            await interaction.followup.send(content=content, embed=embed, view=view, file=file)
        else:
            await interaction.followup.send(content=content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(IonosphereCog(bot))