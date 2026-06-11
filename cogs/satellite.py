import discord
from discord.ext import commands
from discord import app_commands
import re
import io
import asyncio
from PIL import Image
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)

# 對應氣象署的衛星雲圖產品代號
SAT_TYPES = {
    "EA_TRGB": {"name": "東亞 - 真實色", "code": "LCC_TRGB_2750"},
    "EA_CR": {"name": "東亞 - 色調強化", "code": "LCC_IR1_MB_2750"},
    "EA_IR_GRAY": {"name": "東亞 - 紅外線黑白", "code": "LCC_IR1_Gray_2750"},
    "TW_TRGB": {"name": "臺灣 - 真實色", "code": "TWI_TRGB_1350"},
    "TW_CR": {"name": "臺灣 - 色調強化", "code": "TWI_IR1_MB_800"},
    "TW_IR_GRAY": {"name": "臺灣 - 紅外線黑白", "code": "TWI_IR1_Gray_800"}
}

class SatelliteView(discord.ui.View):
    def __init__(self, bot, current_type="EA_TRGB"):
        super().__init__(timeout=300)
        self.bot = bot
        self.current_type = current_type
        
        # 根據目前狀態，更新下拉選單的預設選項
        for option in self.children[0].options:
            option.default = option.value == self.current_type

    async def fetch_latest_satellite_image(self, sat_code):
        # 目前時間 (UTC+8)
        now = datetime.now(timezone(timedelta(hours=8)))
        
        # 從 10 分鐘前開始找，並向下取整到 10 的倍數分
        # 例如 17:57 -> 17:47 -> 17:40
        start_time = now - timedelta(minutes=10)
        minute = (start_time.minute // 10) * 10
        check_time = start_time.replace(minute=minute, second=0, microsecond=0)
        
        max_attempts = 12  # 最多往前找 12 次 (2 小時)
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
            "Referer": "https://www.cwa.gov.tw/V8/C/W/OBS_Sat.html"
        }
        
        for _ in range(max_attempts):
            time_str = check_time.strftime("%Y-%m-%d-%H-%M")
            image_url = f"https://www.cwa.gov.tw/Data/satellite/{sat_code}/{sat_code}-{time_str}.jpg"
            
            try:
                async with self.bot.session.get(image_url, headers=headers) as response:
                    logger.info(f"🌐 [圖片抓取] 衛星雲圖: {image_url} -> HTTP 狀態碼: {response.status}")
                    # 氣象署若無該圖片會回傳 404，加上 User-Agent 避免 403 被擋
                    if response.status == 200:
                        image_bytes = await response.read()
                        discord_time = f"<t:{int(check_time.timestamp())}:f>"
                        return image_bytes, discord_time, image_url
            except Exception as e:
                logger.error(f"❌ 抓取衛星雲圖 {time_str} 發生錯誤: {e}")
                
            # 若找不到，往前推 10 分鐘繼續找 (datetime 會自動處理跨日期邏輯)
            check_time -= timedelta(minutes=10)

        return None, "未知時間", None

    async def build_embed(self):
        sat_info = SAT_TYPES.get(self.current_type)
        sat_code = sat_info["code"]
        
        image_bytes, obs_time, _ = await self.fetch_latest_satellite_image(sat_code)
        
        embed = discord.Embed(
            title="",
            description=f"**{sat_info['name']}** 衛星雲圖\n觀測時間：{obs_time}",
            color=0x3498db
        )
        
        file = None
        if image_bytes:
            file = discord.File(io.BytesIO(image_bytes), filename="satellite.png")
            embed.set_image(url="attachment://satellite.png")
        else:
            embed.description += "\n\n❌ **目前無法取得該衛星雲圖資料**"
            
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return "🛰️ 衛星雲圖查詢", embed, file

    @discord.ui.select(
        placeholder="選擇要顯示的衛星雲圖類型",
        options=[
            discord.SelectOption(label="東亞 - 真實色", value="EA_TRGB", emoji="🌏"),
            discord.SelectOption(label="東亞 - 色調強化", value="EA_CR", emoji="🌏"),
            discord.SelectOption(label="東亞 - 紅外線黑白", value="EA_IR_GRAY", emoji="🌏"),
            discord.SelectOption(label="臺灣 - 真實色", value="TW_TRGB", emoji="🇹🇼"),
            discord.SelectOption(label="臺灣 - 色調強化", value="TW_CR", emoji="🇹🇼"),
            discord.SelectOption(label="臺灣 - 紅外線黑白", value="TW_IR_GRAY", emoji="🇹🇼")
        ]
    )
    async def select_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        self.current_type = select.values[0]
        
        for option in select.options:
            option.default = option.value == self.current_type
            
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label == "靜態圖片":
                child.label = "動態圖片"
                
        content, embed, file = await self.build_embed()
        # 切換選項時清除先前的 GIF 附件
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

    async def build_animation_embed(self):
        sat_info = SAT_TYPES.get(self.current_type)
        sat_code = sat_info["code"]
        
        image_bytes, obs_time, image_url = await self.fetch_latest_satellite_image(sat_code)
        if not image_bytes or not image_url:
            return None, "❌ 目前無法取得該衛星雲圖資料，無法生成動態圖片。"
            
        # 解析最新一張圖片的時間
        match = re.search(r'-(\d{4}-\d{2}-\d{2}-\d{2}-\d{2})\.jpg', image_url)
        if not match:
            return None, "❌ 解析圖片時間失敗。"
            
        latest_time_str = match.group(1)
        latest_time = datetime.strptime(latest_time_str, "%Y-%m-%d-%H-%M")
        
        # 產生過去 10 張的網址 (含最新的一張)
        urls = []
        for i in range(10):
            t = latest_time - timedelta(minutes=10 * i)
            time_str = t.strftime("%Y-%m-%d-%H-%M")
            url = f"https://www.cwa.gov.tw/Data/satellite/{sat_code}/{sat_code}-{time_str}.jpg"
            urls.append(url)
        urls.reverse() # 將時間反轉為從舊到新，這樣 GIF 才會正向播放
        
        images = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
            "Referer": "https://www.cwa.gov.tw/V8/C/W/OBS_Sat.html"
        }
        
        async def fetch_image(url):
            try:
                async with self.bot.session.get(url, headers=headers) as resp:
                    logger.info(f"🌐 [圖片抓取] 衛星雲圖(動態): {url} -> HTTP 狀態碼: {resp.status}")
                    if resp.status == 200:
                        return await resp.read()
            except Exception:
                pass
            return None
            
        # 利用 asyncio 併發同時下載 10 張圖片以節省時間
        results = await asyncio.gather(*(fetch_image(url) for url in urls))
        
        for res in results:
            if res:
                try:
                    img = Image.open(io.BytesIO(res)).convert('RGB')
                    # 縮小圖片以避免 GIF 檔案超過 Discord 限制 (保持長寬比)
                    img.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
                    images.append(img)
                except Exception:
                    pass
                    
        if not images:
            return None, "❌ 圖片下載失敗。"
            
        gif_bytes = io.BytesIO()
        # 將 10 張圖片合成 GIF，每張停留 400 毫秒，最後一張多停留 4000 毫秒，無限循環
        durations = [400] * (len(images) - 1) + [4000]
        images[0].save(gif_bytes, format='GIF', save_all=True, append_images=images[1:], duration=durations, loop=0)
        gif_bytes.seek(0)
        
        file = discord.File(gif_bytes, filename="satellite.gif")
        
        embed = discord.Embed(
            title="",
            description=f"**{sat_info['name']}** 動態衛星雲圖\n(過去 100 分鐘)\n最後觀測時間：{obs_time}",
            color=0x3498db
        )
        embed.set_image(url="attachment://satellite.gif")
        
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return file, embed

    @discord.ui.button(label="動態圖片", style=discord.ButtonStyle.secondary)
    async def toggle_animation(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        if button.label == "靜態圖片":
            button.label = "動態圖片"
            content, embed, file = await self.build_embed()
            await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])
            return

        result = await self.build_animation_embed()
        if not result[0]:
            await interaction.followup.send(result[1], ephemeral=True)
            return
            
        file, embed = result
        button.label = "靜態圖片"
        await interaction.edit_original_response(content="🛰️ 衛星雲圖動態播放", embed=embed, view=self, attachments=[file])

class SatelliteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="衛星雲圖", description="顯示最新的衛星雲圖")
    @app_commands.describe(動態圖片="選擇是否顯示動態圖片")
    @app_commands.choices(動態圖片=[
        app_commands.Choice(name="啟用", value=1),
        app_commands.Choice(name="不啟用", value=0)
    ])
    async def satellite_command(self, interaction: discord.Interaction, 動態圖片: app_commands.Choice[int] = None):
        await interaction.response.defer()
        
        view = SatelliteView(self.bot, current_type="EA_TRGB")
        
        if 動態圖片 and 動態圖片.value == 1:
            result = await view.build_animation_embed()
            if not result[0]:
                content, embed, file = await view.build_embed()
                if file:
                    await interaction.followup.send(content=content, embed=embed, view=view, file=file)
                else:
                    await interaction.followup.send(content=content, embed=embed, view=view)
                await interaction.followup.send(result[1], ephemeral=True)
            else:
                file, embed = result
                for child in view.children:
                    if isinstance(child, discord.ui.Button) and child.label == "動態圖片":
                        child.label = "靜態圖片"
                await interaction.followup.send(content="🛰️ 衛星雲圖動態播放", embed=embed, view=view, file=file)
        else:
            content, embed, file = await view.build_embed()
            if file:
                await interaction.followup.send(content=content, embed=embed, view=view, file=file)
            else:
                await interaction.followup.send(content=content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(SatelliteCog(bot))
