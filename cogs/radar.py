from modules.cache import async_cache
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
        
        for option in self.children[0].options:
            option.default = option.value == self.area

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    @async_cache(ttl_seconds=120)
    async def fetch_radar_data(self):
        js_url = "https://www.cwa.gov.tw/Data/js/obs_img/Observe_radar.js"
        try:
            async with self.bot.session.get(js_url, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.text()
        except Exception as e:
            logger.error(f"❌ 抓取 Observe_radar.js 發生錯誤: {e!r}")
        return None

    @async_cache(ttl_seconds=300)
    async def fetch_latest_radar_image(self, area):
        if area in ["large", "small"]:
            js_text = await self.fetch_radar_data()
            if not js_text:
                return None, "未知時間", None
                
            prefix = "CV1_3600_" if area == "large" else "CV1_TW_3600_"
            
            import re
            pattern = fr"0:\{{['\"]?img['\"]?:\s*['\"]({prefix}\d{{12}}\.png)['\"].*?['\"]text['\"]:\s*['\"](.*?)['\"]"
            match = re.search(pattern, js_text)
            
            if match:
                image_path = match.group(1)
                time_text = match.group(2)
                
                try:
                    dt = datetime.strptime(time_text, "%Y/%m/%d %H:%M")
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                    discord_time = f"<t:{int(dt.timestamp())}:f>"
                except Exception:
                    discord_time = time_text
                    
                image_url = f"https://www.cwa.gov.tw/Data/radar/{image_path}"
                
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Referer": "https://www.cwa.gov.tw/V8/C/W/OBS_Radar.html"
                }
                try:
                    async with self.bot.session.get(image_url, headers=headers) as response:
                        logger.info(f"🔍 [抓取狀態] 正在檢查雷達回波圖: {image_url}")
                        if response.status == 200:
                            logger.info(f"✅ [抓取狀態] 下載成功")
                            image_bytes = await response.read()
                            return image_bytes, discord_time, image_url
                except Exception as e:
                    logger.error(f"❌ 抓取雷達圖錯誤: {e!r}")
                    
            return None, "未知時間", None
        else:
            js_url = "https://www.cwa.gov.tw/Data/js/obs_img/Observe_radar_rain.js"
            try:
                async with self.bot.session.get(js_url, timeout=10) as resp:
                    if resp.status == 200:
                        js_text = await resp.text()
                        
                        area_map = {"shulin": "Area0", "nantun": "Area1", "linyuan": "Area2"}
                        area_key = area_map[area]
                        
                        import re
                        img_pattern = re.compile(r'"img":\'([^\']+)\',\s*\'text\':\'([^\']+)\'')
                        
                        area_start = js_text.find(f"'{area_key}'")
                        if area_start != -1:
                            area_end = js_text.find("'Area", area_start + 10)
                            if area_end == -1: area_end = len(js_text)
                            
                            area_text = js_text[area_start:area_end]
                            matches = img_pattern.findall(area_text)
                            if matches:
                                latest_img_path, time_text = matches[0]
                                image_url = f"https://www.cwa.gov.tw/Data/radar_rain/{latest_img_path}"
                                
                                async with self.bot.session.get(image_url) as response:
                                    logger.info(f"🔍 [抓取狀態] 正在檢查區域雷達回波圖: {image_url}")
                                    if response.status == 200:
                                        logger.info(f"⬇️ [抓取狀態] 準備下載區域雷達回波圖: {image_url}")
                                        image_bytes = await response.read()
                                        logger.info(f"✅ [抓取狀態] 下載成功 ({len(image_bytes)/1024:.1f} KB)")
                                        
                                        try:
                                            dt = datetime.strptime(time_text, "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))
                                            discord_time = f"<t:{int(dt.timestamp())}:f>"
                                            return image_bytes, discord_time, dt
                                        except Exception:
                                            discord_time = f"{time_text}"
                                            return image_bytes, discord_time, None
            except Exception as e:
                logger.error(f"❌ [抓取狀態] 抓取區域雷達回波圖發生錯誤: {e!r}")
            return None, "未知時間", None

    @async_cache(ttl_seconds=300)
    async def fetch_animation_image(self, url):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
            "Referer": "https://www.cwa.gov.tw/V8/C/W/OBS_Radar.html"
        }
        try:
            async with self.bot.session.get(url, headers=headers) as resp:
                logger.info(f"🔍 [抓取狀態] 正在檢查雷達回波圖(動態): {url}")
                if resp.status == 200:
                    logger.info(f"⬇️ [抓取狀態] 準備下載雷達回波圖(動態): {url}")
                    data = await resp.read()
                    logger.info(f"✅ [抓取狀態] 下載成功 ({len(data)/1024:.1f} KB)")
                    return data
        except Exception:
            pass
        return None

    async def build_embed(self):
        image_bytes, obs_time, _ = await self.fetch_latest_radar_image(self.area)
        
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
        await interaction.response.defer()
        
        self.area = select.values[0]
        for option in select.options:
            option.default = option.value == self.area
            
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label == "靜態圖片":
                child.label = "動態圖片"
                
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

    async def build_animation_embed(self):
        urls = []
        if self.area in ["large", "small"]:
            js_text = await self.fetch_radar_data()
            if not js_text:
                return None, "❌ 無法取得雷達回波資料清單。"
                
            prefix = "CV1_3600_" if self.area == "large" else "CV1_TW_3600_"
            import re
            pattern = fr"\{{['\"]?img['\"]?:\s*['\"]({prefix}\d{{12}}\.png)['\"].*?['\"]text['\"]:\s*['\"](.*?)['\"]"
            matches = re.findall(pattern, js_text)
            if not matches:
                return None, "❌ 找不到相關的雷達回波圖檔案清單。"
                
            time_text = matches[0][1]
            try:
                dt = datetime.strptime(time_text, "%Y/%m/%d %H:%M").replace(tzinfo=timezone(timedelta(hours=8)))
                obs_time = f"<t:{int(dt.timestamp())}:f>"
            except Exception:
                obs_time = time_text
                
            matches = matches[:10]
            for img_path, _ in matches:
                urls.append(f"https://www.cwa.gov.tw/Data/radar/{img_path}")
        else:
            js_url = "https://www.cwa.gov.tw/Data/js/obs_img/Observe_radar_rain.js"
            try:
                async with self.bot.session.get(js_url, timeout=10) as resp:
                    if resp.status != 200:
                        return None, "❌ 無法取得區域雷達站資料列表。"
                    js_text = await resp.text()
            except Exception as e:
                logger.error(f"❌ 抓取區域雷達站 JS 發生錯誤: {e!r}")
                return None, "❌ 抓取區域雷達站資料發生錯誤。"
                
            area_map = {
                "shulin": "Area0",
                "nantun": "Area1",
                "linyuan": "Area2"
            }
            area_key = area_map[self.area]
            
            import re
            img_pattern = re.compile(r'"img":\'([^\']+)\',\s*\'text\':\'([^\']+)\'')
            
            area_start = js_text.find(f"'{area_key}'")
            if area_start == -1:
                return None, "❌ 找不到區域雷達站圖片。"
                
            area_end = js_text.find("'Area", area_start + 10)
            if area_end == -1:
                area_end = len(js_text)
                
            area_text = js_text[area_start:area_end]
            matches = img_pattern.findall(area_text)
            
            if not matches:
                return None, "❌ 找不到區域雷達站圖片路徑。"
                
            time_text = matches[0][1]
            try:
                dt = datetime.strptime(time_text, "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))
                obs_time = f"<t:{int(dt.timestamp())}:f>"
            except Exception:
                obs_time = f"{time_text}"
                
            for img_path, _ in matches[:10]:
                urls.append(f"https://www.cwa.gov.tw/Data/radar_rain/{img_path}")
                
        urls.reverse()
        
        images = []
        
        results = await asyncio.gather(*(self.fetch_animation_image(url) for url in urls))
        
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
        
        name_map = {
            "large": "台灣海域", 
            "small": "台灣本島",
            "shulin": "樹林雷達站 (北部)",
            "nantun": "南屯雷達站 (中部)",
            "linyuan": "林園雷達站 (南部)"
        }
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

    @app_commands.command(name="雷達回波", description="📡 顯示最新的雷達回波圖 Radar")
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

    async def refresh_message(self, interaction: discord.Interaction, message: discord.Message, cmd_name: str):
        await interaction.response.defer(ephemeral=True)
        area = "small"
        is_anim = False
        if message.embeds:
            desc = message.embeds[0].description or ""
            if "動態" in desc: is_anim = True
            name_map = {"台灣海域": "large", "台灣本島": "small", "樹林": "shulin", "南屯": "nantun", "林園": "linyuan"}
            for k, v in name_map.items():
                if k in desc:
                    area = v
                    break
        view = RadarView(self.bot, interaction.user.id, area)
        if is_anim:
            file, embed = await view.build_animation_embed()
            for child in view.children:
                if getattr(child, 'label', '') == "動態圖片": child.label = "靜態圖片"
            if embed: await message.edit(embed=embed, view=view, attachments=[file] if file else [])
        else:
            content, embed, file = await view.build_embed()
            await message.edit(content=content, embed=embed, view=view, attachments=[file] if file else [])
        await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)

async def setup(bot):
    await bot.add_cog(RadarCog(bot))