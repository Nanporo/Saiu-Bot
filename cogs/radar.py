import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta

class RadarView(discord.ui.View):
    def __init__(self, area="small"):
        super().__init__(timeout=300)
        self.area = area
        # 根據目前狀態，更新下拉選單的預設選項
        for option in self.children[0].options:
            option.default = option.value == self.area

    def build_embed(self):
        # 產生時間戳記附加在網址後方，避免 Discord 快取
        timestamp = (int(datetime.now().timestamp()) // 600) * 600
        
        if self.area == "large":
            image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0058-001.png?t={timestamp}"
            message = "📡 最新雷達回波圖 (台灣海域)"
        elif self.area == "shulin":
            image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0084-001.png?t={timestamp}"
            message = "📡 最新雷達回波圖 (樹林雷達站)"
        elif self.area == "nantun":
            image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0084-002.png?t={timestamp}"
            message = "📡 最新雷達回波圖 (南屯雷達站)"
        elif self.area == "linyuan":
            image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0084-003.png?t={timestamp}"
            message = "📡 最新雷達回波圖 (林園雷達站)"
        else: # 預設為近距離
            image_url = f"https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0058-003.png?t={timestamp}"
            message = "📡 最新雷達回波圖 (台灣本島)"

        content = message
        embed = discord.Embed(title="", color=0x3498db)
        embed.set_image(url=image_url)
        
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/TWERG-Bot/main/photos/cwa_logo.png")
        return content, embed

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
        self.area = select.values[0]
        for option in select.options:
            option.default = option.value == self.area
        content, embed = self.build_embed()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

class RadarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="雷達回波", description="📡 顯示最新的雷達回波圖")
    @app_commands.describe(範圍="選擇要顯示的雷達回波圖範圍")
    @app_commands.choices(範圍=[
        app_commands.Choice(name="台灣海域", value="large"),
        app_commands.Choice(name="台灣本島", value="small"),
        app_commands.Choice(name="樹林(北部)", value="shulin"),
        app_commands.Choice(name="南屯(中部)", value="nantun"),
        app_commands.Choice(name="林園(南部)", value="linyuan")
    ])
    async def radar_command(self, interaction: discord.Interaction, 範圍: app_commands.Choice[str] = None):
        await interaction.response.defer()
        
        area = 範圍.value if 範圍 else "small"
        view = RadarView(area)
        content, embed = view.build_embed()
        
        await interaction.followup.send(content=content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(RadarCog(bot))