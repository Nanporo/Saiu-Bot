import discord
from discord.ext import commands
from discord import app_commands

class AboutCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.version = "1.1.3"
        self.ready_printed = False

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.ready_printed:
            print(f"🤖 Saiu 當前版本: {self.version}")
            self.ready_printed = True

    @app_commands.command(name="關於", description="顯示關於 Saiu 的資訊")
    async def about_command(self, interaction: discord.Interaction):
        
        message_content = "ℹ️ 關於 Saiu"

        embed = discord.Embed(
            title="你好！", 
            colour=0x41809b
        )

        embed.add_field(
            name="",
            value="我是一個天氣小助手，如果有天氣變化或最新的氣象資訊，我會通知你！\n如果您遇到任何問題，請聯絡機器人作者。",
            inline=False
        )
        embed.add_field(
            name="資料來源",
            value="此機器人的氣象資料來源於中央氣象署、NCDR、NOAA。\nIATA - ICAO 對照表來自於 https://github.com/ip2location/ip2location-iata-icao 。\n台灣行政區域地圖來自於 https://github.com/dkaoster/taiwan-atlas 。\n頭像來自於 miHoYo 的原神角色「行秋」，很可愛。",
            inline=False
        )
        embed.add_field(
            name="關於未來1小時雷達定量降雨預報",
            value="利用雷達回波外延法，並依據回波與雨量關係式所預估之未來1小時格點化雨量，使用此預報產品時須瞭解外延法應用之極限，請謹慎使用。",
            inline=False
        )
        embed.add_field(
            name="License",
            value="GNU Affero General Public License",
            inline=False
        )
        embed.add_field(
            name="版本",
            value=self.version,
            inline=False
        )
        embed.set_footer(text="作者 Kuuchi (kuuchi) • Support by TWERG", icon_url="https://avatars.githubusercontent.com/u/15816531?v=4")
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="BOT 原始碼", emoji="<:Github:1503678487234613301>", url="https://github.com/Nanporo/Saiu-Bot/", style=discord.ButtonStyle.link))
        
        await interaction.response.send_message(content=message_content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(AboutCog(bot))