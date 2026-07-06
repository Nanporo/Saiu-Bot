from modules.cache import async_cache
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
import io
import logging

logger = logging.getLogger(__name__)

class QPFView(discord.ui.View):
    def __init__(self, bot, author_id: int, product="6", future_time="06"):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.bot = bot
        self.product = product
        self.future_time = future_time
        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def update_components(self):
        select_time = None
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label == "6小時":
                    child.disabled = (self.product == "6")
                elif child.label == "12小時":
                    child.disabled = (self.product == "12")
                elif child.label == "極短期":
                    child.disabled = (self.product == "3")
            elif isinstance(child, discord.ui.Select):
                select_time = child
                
        if not select_time: return
        
        if self.product == "3":
            select_time.options = [
                discord.SelectOption(label="未來 0~3 小時", value="03"),
                discord.SelectOption(label="未來 3~6 小時", value="06"),
                discord.SelectOption(label="未來 6~9 小時", value="09"),
                discord.SelectOption(label="未來 9~12 小時", value="12")
            ]
        elif self.product == "6":
            select_time.options = [
                discord.SelectOption(label="未來 0~6 小時", value="06"),
                discord.SelectOption(label="未來 6~12 小時", value="12"),
                discord.SelectOption(label="未來 12~18 小時", value="18"),
                discord.SelectOption(label="未來 18~24 小時", value="24")
            ]
        else:
            select_time.options = [
                discord.SelectOption(label="未來 0~12 小時", value="12"),
                discord.SelectOption(label="未來 12~24 小時", value="24"),
                discord.SelectOption(label="未來 24~36 小時", value="36"),
                discord.SelectOption(label="未來 36~48 小時", value="48")
            ]
        
        for option in select_time.options:
            option.default = option.value == self.future_time

    @async_cache(ttl_seconds=300)
    async def fetch_image(self, product, future_time):
        now = datetime.now(timezone(timedelta(hours=8)))
        timestamp = f"{now.strftime('%Y%m%d%H')}-{now.minute // 10}"
        image_url = f"https://www.cwa.gov.tw/Data/fcst_img/QPF_ChFcstPrecip_{product}_{future_time}.png?T={timestamp}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
            "Referer": "https://www.cwa.gov.tw/V8/C/P/QPF.html"
        }
        
        try:
            async with self.bot.session.get(image_url, headers=headers) as response:
                logger.info(f"🔍 [抓取狀態] 正在檢查定量降水預報: {image_url}")
                if response.status == 200:
                    logger.info(f"⬇️ [抓取狀態] 準備下載定量降水預報: {image_url}")
                    image_bytes = await response.read()
                    logger.info(f"✅ [抓取狀態] 下載成功 ({len(image_bytes)/1024:.1f} KB)")
                    return image_bytes
        except Exception as e:
            logger.error(f"❌ [抓取狀態] 抓取定量降水預報發生錯誤: {e}")
            
        return None

    async def build_embed(self):
        image_bytes = await self.fetch_image(self.product, self.future_time)
        
        if self.product == "3":
            title = "極短期預報圖"
        else:
            title = f"{int(self.product)}小時預報圖"
            
        embed = discord.Embed(title=title, color=0x3498db)
        
        if self.product == "3":
            embed.description = "-# ⚠️ 請注意圖片時間，僅有陸上颱風警報或大規模、劇烈豪雨發生時才會更新。"
        
        file = None
        if image_bytes:
            file = discord.File(io.BytesIO(image_bytes), filename="qpf.png")
            embed.set_image(url="attachment://qpf.png")
        else:
            if embed.description:
                embed.description += "\n\n❌ **目前無法取得該預報圖資料**"
            else:
                embed.description = "❌ **目前無法取得該預報圖資料**"
            
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return "🌧️ 定量降水預報", embed, file

    @discord.ui.button(label="6小時", style=discord.ButtonStyle.secondary, row=0)
    async def btn_6hr(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.change_product(interaction, "6")

    @discord.ui.button(label="12小時", style=discord.ButtonStyle.secondary, row=0)
    async def btn_12hr(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.change_product(interaction, "12")

    @discord.ui.button(label="極短期", style=discord.ButtonStyle.secondary, row=0)
    async def btn_3hr(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.change_product(interaction, "3")

    async def change_product(self, interaction: discord.Interaction, new_product: str):
        await interaction.response.defer()
        self.product = new_product
        
        if self.product == "3":
            self.future_time = "03"
        elif self.product == "6":
            self.future_time = "06"
        else:
            self.future_time = "12"
                
        self.update_components()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.select(
        placeholder="選擇預測時間",
        options=[
            discord.SelectOption(label="未來 0~6 小時", value="06"),
            discord.SelectOption(label="未來 6~12 小時", value="12"),
            discord.SelectOption(label="未來 12~18 小時", value="18"),
            discord.SelectOption(label="未來 18~24 小時", value="24")
        ],
        row=1
    )
    async def select_time(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        self.future_time = select.values[0]
        
        self.update_components()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

class QPFCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="定量降水預報", description="🌧️ 顯示最新的定量降水預報圖 QPF")
    @app_commands.describe(mode="選擇要直接查看的資料")
    @app_commands.choices(mode=[
        app_commands.Choice(name="極短期", value="3"),
        app_commands.Choice(name="6小時", value="6"),
        app_commands.Choice(name="12小時", value="12")
    ])
    async def qpf_command(self, interaction: discord.Interaction, mode: str = "12"):
        await interaction.response.defer()
        
        future_time = "12" if mode == "12" else ("06" if mode == "6" else "03")
        view = QPFView(self.bot, interaction.user.id, product=mode, future_time=future_time)
        content, embed, file = await view.build_embed()
        
        if file:
            await interaction.followup.send(content=content, embed=embed, view=view, file=file)
        else:
            await interaction.followup.send(content=content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(QPFCog(bot))