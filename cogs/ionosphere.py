import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
import io
import logging

logger = logging.getLogger(__name__)

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

    @app_commands.command(name="電離層電波吸收", description="顯示最新的電離層 D 區電波吸收預測圖")
    async def ionosphere_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
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
        
        if file:
            await interaction.followup.send(content="🌌 電離層電波吸收查詢", embed=embed, file=file)
        else:
            await interaction.followup.send(content="🌌 電離層電波吸收查詢", embed=embed)

async def setup(bot):
    await bot.add_cog(IonosphereCog(bot))