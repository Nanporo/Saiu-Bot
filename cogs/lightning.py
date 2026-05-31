import discord
from discord.ext import commands
from discord import app_commands
import io
import asyncio
from PIL import Image
from datetime import datetime, timezone, timedelta

class LightningView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot

    async def fetch_latest_lightning_image(self):
        # 目前時間 (UTC+8)
        now = datetime.now(timezone(timedelta(hours=8)))
        
        # 考慮到氣象署產生圖片需要時間，稍微扣掉 2 分鐘，再取 5 的倍數分
        start_time = now - timedelta(minutes=2)
        minute = (start_time.minute // 5) * 5
        check_time = start_time.replace(minute=minute, second=0, microsecond=0)
        
        max_attempts = 12  # 最多往前找 1 小時 (12 * 5 = 60分鐘)
        
        for _ in range(max_attempts):
            time_str = check_time.strftime("%Y%m%d%H%M00")
            image_url = f"https://www.cwa.gov.tw/Data/lightning/{time_str}_lgtl.jpg"
            
            try:
                async with self.bot.session.get(image_url) as response:
                    # 氣象署若無該圖片會回傳 404，或是回傳的 Content-Type 不為 image
                    if response.status == 200 and 'image' in response.headers.get('Content-Type', ''):
                        discord_time = f"<t:{int(check_time.timestamp())}:f>"
                        return image_url, discord_time, check_time
            except Exception as e:
                print(f"❌ 抓取即時閃電 {time_str} 發生錯誤: {e}")
                
            # 若找不到，往前推 5 分鐘繼續找
            check_time -= timedelta(minutes=5)

        return None, "未知時間", None

    async def build_embed(self):
        image_url, obs_time, _ = await self.fetch_latest_lightning_image()
        
        embed = discord.Embed(
            title="",
            description=f"**臺灣** 即時閃電觀測圖\n觀測時間：{obs_time}",
            color=0xf1c40f
        )
        
        if image_url:
            embed.set_image(url=image_url)
        else:
            embed.description += "\n\n❌ **目前無法取得即時閃電資料**"
            
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return "⚡ 即時閃電查詢", embed

    @discord.ui.button(label="動態圖片", style=discord.ButtonStyle.secondary)
    async def toggle_animation(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        if button.label == "靜態圖片":
            button.label = "動態圖片"
            content, embed = await self.build_embed()
            await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[])
            return
        
        image_url, obs_time, latest_time = await self.fetch_latest_lightning_image()
        if not image_url or not latest_time:
            await interaction.followup.send("❌ 目前無法取得閃電圖資料，無法生成動態圖片。", ephemeral=True)
            return
            
        # 產生過去 10 張的網址 (含最新的一張)，閃電圖每 5 分鐘一張
        urls = []
        for i in range(10):
            t = latest_time - timedelta(minutes=5 * i)
            time_str = t.strftime("%Y%m%d%H%M00")
            url = f"https://www.cwa.gov.tw/Data/lightning/{time_str}_lgtl.jpg"
            urls.append(url)
        urls.reverse() # 將時間反轉為從舊到新，這樣 GIF 才會正向播放
        
        images = []
        async def fetch_image(url):
            try:
                async with self.bot.session.get(url) as resp:
                    if resp.status == 200 and 'image' in resp.headers.get('Content-Type', ''):
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
                    img.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
                    images.append(img)
                except Exception:
                    pass
                    
        if not images:
            await interaction.followup.send("❌ 圖片下載失敗。", ephemeral=True)
            return
            
        gif_bytes = io.BytesIO()
        # 將 10 張圖片合成 GIF，每張停留 400 毫秒，最後一張多停留 4000 毫秒
        durations = [400] * (len(images) - 1) + [4000]
        images[0].save(gif_bytes, format='GIF', save_all=True, append_images=images[1:], duration=durations, loop=0)
        gif_bytes.seek(0)
        
        file = discord.File(gif_bytes, filename="lightning.gif")
        
        embed = discord.Embed(
            title="",
            description=f"**臺灣** 動態即時閃電圖\n(過去 50 分鐘)\n最後觀測時間：{obs_time}",
            color=0xf1c40f
        )
        embed.set_image(url="attachment://lightning.gif")
        
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        button.label = "靜態圖片"
        await interaction.edit_original_response(content="⚡ 即時閃電動態播放", embed=embed, view=self, attachments=[file])

class LightningCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="即時閃電", description="顯示最新的即時閃電觀測圖")
    async def lightning_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        view = LightningView(self.bot)
        content, embed = await view.build_embed()
        
        await interaction.followup.send(content=content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(LightningCog(bot))