import discord
from discord.ext import commands
from discord import app_commands
import logging

from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow, Button

logger = logging.getLogger(__name__)

class AboutView(LayoutView):
    def __init__(self, author_id: int, version: str):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.version = version
        self.current_page = 0
        
        self.prev_btn = Button(emoji="⬅️", style=discord.ButtonStyle.primary)
        self.prev_btn.callback = self.prev_page
        
        self.page_indicator = Button(label="", style=discord.ButtonStyle.secondary, disabled=True)
        
        self.next_btn = Button(emoji="➡️", style=discord.ButtonStyle.primary)
        self.next_btn.callback = self.next_page
        
        self.url_buttons = [
            Button(label="官方網站", emoji="🌐", url="https://nanporo.github.io/Saiu-Bot/", style=discord.ButtonStyle.link),
            Button(label="服務條款", emoji="📜", url="https://nanporo.github.io/Saiu-Bot/terms.html", style=discord.ButtonStyle.link),
            Button(label="隱私權政策", emoji="🔒", url="https://nanporo.github.io/Saiu-Bot/privacy.html", style=discord.ButtonStyle.link)
        ]
        
        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def build_page_container(self, page_index: int) -> Container:
        if page_index == 0:
            return Container(
                TextDisplay(
                    "## 你好！\n"
                    "我是一個天氣小助手，如果有天氣變化或最新的氣象資訊，我會通知你！\n"
                    "您可以使用 `/幫助` 指令來獲取使用方式。如果您遇到任何技術上的問題或是錯誤，請聯絡機器人作者。"
                ),
                Separator(),
                TextDisplay(
                    "### 資料來源\n"
                    "* 中央氣象署\n"
                    "* NOAA\n"
                    "* 行政院人事行政總處\n"
                    "* 環境部\n"
                    "* 台灣電力公司\n"
                    "* IATA - ICAO 對照表 [GitHub](https://github.com/ip2location/ip2location-iata-icao)\n"
                    "* 台灣行政區域地圖 [GitHub](https://github.com/dkaoster/taiwan-atlas)\n"
                    "* 頭像來自於 miHoYo 的原神角色「行秋」，很可愛。"
                ),
                Separator(),
                TextDisplay(
                    f"**機器人 License**：GNU Affero General Public License\n"
                    f"**版本**：`{self.version}`\n\n"
                    "-# 作者 Kuuchi (kuuchi) • XQ TEAM"
                ),
                accent_color=0x41809b
            )
        else:
            return Container(
                TextDisplay(
                    "## 關於預報與警報產品\n"
                    "預報資料僅供參考，我們不保證服務不會中斷、不會出錯，或完全符合您的需求。因使用本機器人而導致的任何直接、間接、附帶或衍生性損害，我們概不負責。"
                ),
                Separator(),
                TextDisplay(
                    "### 產品注意事項\n"
                    "* **未來 1 小時雷達定量降雨預報**：利用雷達回波外延法，並依據回波與雨量關係式所預估之未來 1 小時格點化雨量，使用此預報產品時須瞭解外延法應用之極限，請謹慎使用。\n"
                    "* **定量降水預報**：定量降水預報產品技術仍在發展階段，對於颱風及梅雨帶來的大量降水有較高的準確度，至於小範圍的對流降雨則準確度較低，請謹慎使用。\n"
                    "* **強震即時警報**：強震即時警報（地震速報）是利用少數測站偵測到的地震波，預估地震的震央及規模，在數秒內發布警報以爭取避險時間。由於時間上的緊迫性，與實際狀況可能會有誤差。\n"
                    "* **附近飛機功能**：ADS-B 資料為自行架設之接收機所收集，可能因地形、氣候、建築物等因素影響，對資料準確性不負任何責任。"
                ),
                Separator(),
                TextDisplay("-# 作者 Kuuchi (kuuchi) • XQ TEAM"),
                accent_color=0x41809b
            )

    def update_components(self):
        self.clear_items()
        
        # 0. 頂部標題
        self.add_item(TextDisplay("ℹ️ 關於 小裁雨"))
        
        # 1. 內容容器卡片
        container = self.build_page_container(self.current_page)
        self.add_item(container)
        
        # 2. 外部連結列
        url_row = ActionRow(*self.url_buttons)
        self.add_item(url_row)
        
        # 3. 分頁控制列
        self.prev_btn.disabled = (self.current_page == 0)
        self.next_btn.disabled = (self.current_page == 1)
        self.page_indicator.label = f"第 {self.current_page + 1} / 2 頁"
        nav_row = ActionRow(self.prev_btn, self.page_indicator, self.next_btn)
        self.add_item(nav_row)

    async def prev_page(self, interaction: discord.Interaction):
        self.current_page = max(0, self.current_page - 1)
        self.update_components()
        await interaction.response.edit_message(view=self, embed=None)

    async def next_page(self, interaction: discord.Interaction):
        self.current_page = min(1, self.current_page + 1)
        self.update_components()
        await interaction.response.edit_message(view=self, embed=None)

class AboutCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.version = "4.1"
        self.ready_printed = False

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.ready_printed:
            logger.info(f"🤖 Saiu 當前版本: {self.version}")
            self.ready_printed = True

    @app_commands.command(name="關於", description="ℹ️ 顯示關於 小裁雨 的資訊 About")
    async def about_command(self, interaction: discord.Interaction):
        view = AboutView(interaction.user.id, self.version)
        await interaction.response.send_message(view=view)

async def setup(bot):
    await bot.add_cog(AboutCog(bot))