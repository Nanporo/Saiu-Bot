import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
import io
import asyncio
from PIL import Image
import logging

logger = logging.getLogger(__name__)

class RadarView(discord.ui.View):
    def __init__(self, bot, author_id: int, area="small"):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.bot = bot
        self.area = area
        
        # 根據目前狀態，更新下拉選單的預設選項
        for option in self.children[0].options:
            option.default = option.value == self.area

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    async def fetch_latest_radar_image(self):
        now = datetime.now(timezone(timedelta(hours=8)))
        
        if self.area in ["large", "small"]:
            # 從 10 分鐘前開始找，並向下取整到 10 的倍數分
            start_time = now - timedelta(minutes=10)
            minute = (start_time.minute // 10) * 10
            check_time = start_time.replace(minute=minute, second=0, microsecond=0)
            
            max_attempts = 12  # 最多往前找 12 次 (2 小時)
            prefix = "CV1_3600_" if self.area == "large" else "CV1_TW_3600_"
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
                "Referer": "https://www.cwa.gov.tw/V8/C/W/OBS_Radar.html"
            }
            
            for _ in range(max_attempts):
                time_str = check_time.strftime("%Y%m%d%H%M")
                image_url = f"https://www.cwa.gov.tw/Data/radar/{prefix}{time_str}.png"
                
                try:
                    async with self.bot.session.get(image_url, headers=headers) as response:
                        logger.info(f"🌐 [圖片抓取] 雷達回波: {image_url} -> HTTP 狀態碼: {response.status}")
                        # 檢查圖片是否存在
                        if response.status == 200:
                            image_bytes = await response.read()
                            discord_time = f"<t:{int(check_time.timestamp())}:f>"
                            return image_bytes, discord_time, check_time
                except Exception as e:
                    logger.error(f"❌ 抓取雷達回波圖 {time_str} 發生錯誤: {e}")
                    
                # 往前推 10 分鐘繼續找
                check_time -= timedelta(minutes=10)
                
            return None, "未知時間", None
        else:
            # 區域雷達站使用原有的 API AWS 連結與 timestamp 避免快取
            timestamp = (int(now.timestamp()) // 600) * 600
            if self.area == "shulin":
                image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0084-001.png?t={timestamp}"
            elif self.area == "nantun":
                image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0084-002.png?t={timestamp}"
            else:
                image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0084-003.png?t={timestamp}"
                
            try:
                async with self.bot.session.get(image_url) as response:
                    if response.status == 200:
                        image_bytes = await response.read()
                        discord_time = f"<t:{int(now.timestamp())}:f> (大約)"
                        return image_bytes, discord_time, None
            except Exception:
                pass
            return None, "未知時間", None

    async def build_embed(self):
        image_bytes, obs_time, _ = await self.fetch_latest_radar_image()
        
        name_map = {
            "large": "台灣海域",
            "small": "台灣本島",
            "shulin": "樹林雷達站 (北部)",
            "nantun": "南屯雷達站 (中部)",
            "linyuan": "林園雷達站 (南部)"
        }
        
        embed = discord.Embed(title="", color=0x3498db)
        embed.description = f"**{name_map.get(self.area)}** 最新雷達回波圖\n觀測時間：{obs_time}"
        
        file = None
        if image_bytes:
            file = discord.File(io.BytesIO(image_bytes), filename="radar.png")
            embed.set_image(url="attachment://radar.png")
        else:
            embed.description += "\n\n❌ **目前無法取得該雷達回波圖資料**"
        
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return "📡 雷達回波查詢", embed, file

    @discord.ui.select(
        placeholder="選擇要顯示的雷達回波圖範圍",
        options=[
            discord.SelectOption(label="台灣大範圍", value="large",),
            discord.SelectOption(label="台灣近距離", value="small",),
            discord.SelectOption(label="樹林雷達(北部)", value="shulin", emoji="📡"),
            discord.SelectOption(label="南屯雷達(中部)", value="nantun", emoji="📡"),
            discord.SelectOption(label="林園雷達(南部)", value="linyuan", emoji="📡")
        ]
    )
    async def select_area(self, interaction: discord.Interaction, select: discord.ui.Select):
        # 改用 defer，因為現在 fetch 圖片需要一小段時間，避免逾時
        await interaction.response.defer()
        
        self.area = select.values[0]
        for option in select.options:
            option.default = option.value == self.area
            
        # 如果當前是動態圖片模式，切換地區時將按鈕文字重置回靜態
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label == "靜態圖片":
                child.label = "動態圖片"
                
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

    async def build_animation_embed(self):
        if self.area not in ["large", "small"]:
            return None, "❌ 區域雷達站（樹林、南屯、林園）目前不支援動態圖片功能。"
            
        image_bytes, obs_time, latest_time = await self.fetch_latest_radar_image()
        if not image_bytes or not latest_time:
            return None, "❌ 目前無法取得雷達回波資料，無法生成動態圖片。"
            
        # 產生過去 10 張雷達圖的網址 (含最新的一張)
        prefix = "CV1_3600_" if self.area == "large" else "CV1_TW_3600_"
        urls = []
        for i in range(10):
            t = latest_time - timedelta(minutes=10 * i)
            time_str = t.strftime("%Y%m%d%H%M")
            url = f"https://www.cwa.gov.tw/Data/radar/{prefix}{time_str}.png"
            urls.append(url)
        urls.reverse() # 將時間反轉為從舊到新，這樣 GIF 才會正向播放
        
        images = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
            "Referer": "https://www.cwa.gov.tw/V8/C/W/OBS_Radar.html"
        }
        
        async def fetch_image(url):
            try:
                async with self.bot.session.get(url, headers=headers) as resp:
                    logger.info(f"🌐 [圖片抓取] 雷達回波(動態): {url} -> HTTP 狀態碼: {resp.status}")
                    if resp.status == 200:
                        return await resp.read()
            except Exception:
                pass
            return None
            
        results = await asyncio.gather(*(fetch_image(url) for url in urls))
        
        for res in results:
            if res:
                try:
                    img = Image.open(io.BytesIO(res)).convert('RGB')
                    # 稍微縮放避免 GIF 大小超過 Discord 限制
                    img.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
                    images.append(img)
                except Exception:
                    pass
                    
        if not images:
            return None, "❌ 圖片下載失敗。"
            
        gif_bytes = io.BytesIO()
        # 將 10 張圖片合成 GIF，每張停留 400 毫秒，最後一張停留 4000 毫秒 (4秒) 
        durations = [400] * (len(images) - 1) + [4000]
        images[0].save(gif_bytes, format='GIF', save_all=True, append_images=images[1:], duration=durations, loop=0)
        gif_bytes.seek(0)
        
        file = discord.File(gif_bytes, filename="radar.gif")
        
        name_map = {"large": "台灣海域", "small": "台灣本島"}
        embed = discord.Embed(
            title="",
            description=f"**{name_map.get(self.area)}** 動態雷達回波圖\n(過去 100 分鐘)\n最後觀測時間：{obs_time}",
            color=0x3498db
        )
        embed.set_image(url="attachment://radar.gif")
        
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return file, embed

    @discord.ui.button(label="動態圖片", style=discord.ButtonStyle.secondary)
    async def toggle_animation(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        if self.area not in ["large", "small"]:
            await interaction.followup.send("❌ 區域雷達站（樹林、南屯、林園）目前不支援動態圖片功能。", ephemeral=True)
            return
            
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
        await interaction.edit_original_response(content="📡 雷達回波動態播放", embed=embed, view=self, attachments=[file])

class RadarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="雷達回波", description="📡 顯示最新的雷達回波圖")
    @app_commands.describe(範圍="選擇要顯示的雷達回波圖範圍", 動態圖片="選擇是否顯示動態圖片")
    @app_commands.choices(範圍=[
        app_commands.Choice(name="台灣海域", value="large"),
        app_commands.Choice(name="台灣本島", value="small"),
        app_commands.Choice(name="樹林(北部)", value="shulin"),
        app_commands.Choice(name="南屯(中部)", value="nantun"),
        app_commands.Choice(name="林園(南部)", value="linyuan")
    ], 動態圖片=[
        app_commands.Choice(name="啟用", value=1),
        app_commands.Choice(name="不啟用", value=0)
    ])
    async def radar_command(self, interaction: discord.Interaction, 範圍: app_commands.Choice[str] = None, 動態圖片: app_commands.Choice[int] = None):
        await interaction.response.defer()
        
        area = 範圍.value if 範圍 else "small"
        view = RadarView(self.bot, interaction.user.id, area=area)
        
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
                await interaction.followup.send(content="📡 雷達回波動態播放", embed=embed, view=view, file=file)
        else:
            content, embed, file = await view.build_embed()
            if file:
                await interaction.followup.send(content=content, embed=embed, view=view, file=file)
            else:
                await interaction.followup.send(content=content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(RadarCog(bot))