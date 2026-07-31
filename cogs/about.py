import discord
from discord.ext import commands
from discord import app_commands
import logging

logger = logging.getLogger(__name__)

class AboutView(discord.ui.View):
    def __init__(self, author_id: int, version: str):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.current_page = 0
        
        embed1 = discord.Embed(
            title="你好！", 
            colour=0x41809b
        )
        embed1.add_field(
            name="",
            value="我是一個天氣小助手，如果有天氣變化或最新的氣象資訊，我會通知你！\n您可以使用 `/幫助` 指令來獲取使用方式。如果您遇到任何技術上的問題或是錯誤，請聯絡機器人作者。",
            inline=False
        )
        embed1.add_field(
            name="資料來源",
            value="* 中央氣象署\n* NOAA \n* 行政院人事行政總處 \n* 環境部\n* 台灣電力公司\n* IATA - ICAO 對照表 https://github.com/ip2location/ip2location-iata-icao \n* 台灣行政區域地圖 https://github.com/dkaoster/taiwan-atlas \n* 頭像來自於 miHoYo 的原神角色「行秋」，很可愛。",
            inline=False
        )
        embed1.add_field(
            name="機器人 License",
            value="GNU Affero General Public License",
            inline=False
        )
        embed1.add_field(
            name="版本",
            value=version,
            inline=False
        )
        embed1.set_footer(text="作者 Kuuchi (kuuchi) • XQ TEAM", icon_url="https://avatars.githubusercontent.com/u/15816531?v=4")

        embed2 = discord.Embed(
            title="關於預報與警報產品", 
            description="預報資料僅供參考，我們不保證服務不會中斷、不會出錯，或完全符合您的需求。因使用本機器人而導致的任何直接、間接、附帶或衍生性損害，我們概不負責。",
            colour=0x41809b
        )
        embed2.add_field(
            name="關於未來1小時雷達定量降雨預報",
            value="利用雷達回波外延法，並依據回波與雨量關係式所預估之未來1小時格點化雨量，使用此預報產品時須瞭解外延法應用之極限，請謹慎使用。",
            inline=False
        )
        embed2.add_field(
            name="關於定量降水預報",
            value="定量降水預報產品技術仍在發展階段，使用此預報產品時，須瞭解其極限，對於颱風及梅雨帶來的大量降水有較高的準確度，至於小範圍的對流降雨則準確度較低，請謹慎使用。",
            inline=False
        )
        embed2.add_field(
            name="關於強震即時警報",
            value="強震即時警報（地震速報）是利用少數測站偵測到的地震波，預估地震的震央及規模，並在數秒內發布警報，以爭取避險時間。由於時間上的緊迫性，與實際狀況可能會有誤差，請謹慎使用。",
            inline=False
        )
        embed2.add_field(
            name="關於附近飛機功能",
            value="ADS-B 資料為我們自行架設的接收機所收集，實際能收到的飛機位置可能因為地形、氣候、建築物等因素而有所影響，對資料準確性不負任何責任。",
            inline=False
        )
        embed2.set_footer(text="作者 Kuuchi (kuuchi) • XQ TEAM", icon_url="https://avatars.githubusercontent.com/u/15816531?v=4")

        self.pages = [embed1, embed2]
        
        self.prev_btn = discord.ui.Button(emoji="⬅️", style=discord.ButtonStyle.primary, row=1)
        self.prev_btn.callback = self.prev_page
        
        self.page_indicator = discord.ui.Button(label="", style=discord.ButtonStyle.secondary, disabled=True, row=1)
        
        self.next_btn = discord.ui.Button(emoji="➡️", style=discord.ButtonStyle.primary, row=1)
        self.next_btn.callback = self.next_page
        
        self.url_buttons = [
            discord.ui.Button(label="官方網站", emoji="🌐", url="https://nanporo.github.io/Saiu-Bot/", style=discord.ButtonStyle.link, row=0),
            discord.ui.Button(label="服務條款", emoji="📜", url="https://nanporo.github.io/Saiu-Bot/terms.html", style=discord.ButtonStyle.link, row=0),
            discord.ui.Button(label="隱私權政策", emoji="🔒", url="https://nanporo.github.io/Saiu-Bot/privacy.html", style=discord.ButtonStyle.link, row=0)
        ]
        
        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def update_components(self):
        self.clear_items()
        
        for btn in self.url_buttons:
            self.add_item(btn)
            
        self.prev_btn.disabled = self.current_page == 0
        self.next_btn.disabled = self.current_page == len(self.pages) - 1
        self.page_indicator.label = f"第 {self.current_page + 1} / {len(self.pages)} 頁"
        
        self.add_item(self.prev_btn)
        self.add_item(self.page_indicator)
        self.add_item(self.next_btn)

    async def prev_page(self, interaction: discord.Interaction):
        self.current_page -= 1
        self.update_components()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    async def next_page(self, interaction: discord.Interaction):
        self.current_page += 1
        self.update_components()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

class AboutCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.version = "3.4.1"
        self.ready_printed = False

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.ready_printed:
            logger.info(f"🤖 Saiu 當前版本: {self.version}")
            self.ready_printed = True

    @app_commands.command(name="關於", description="ℹ️ 顯示關於 小裁雨 的資訊 About")
    async def about_command(self, interaction: discord.Interaction):
        message_content = "ℹ️ 關於 小裁雨"
        view = AboutView(interaction.user.id, self.version)
        await interaction.response.send_message(content=message_content, embed=view.pages[0], view=view)

async def setup(bot):
    await bot.add_cog(AboutCog(bot))