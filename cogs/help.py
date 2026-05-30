import discord
from discord.ext import commands
from discord import app_commands

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.current_page = 0
        
        # 建立三個 Embed 分頁
        embed_general = discord.Embed(title="一般指令", color=0x41809b, description="任何人都可以使用的基本指令")
        embed_general.add_field(name="/今日雨量", value="☔ 查詢今日台灣各測站的累積雨量排行", inline=False)
        embed_general.add_field(name="/今日氣溫", value="🌡️ 查詢今日台灣各測站的最高溫或最低溫排行", inline=False)
        embed_general.add_field(name="/今日氣象記錄", value="🏆 查詢今日綜合氣象記錄看板", inline=False)
        embed_general.add_field(name="/查詢降雨預報", value="🌧️ 手動查詢指定地點未來 1 小時內的降雨預測", inline=False)
        embed_general.add_field(name="/雷達回波圖", value="📡 顯示最新的雷達回波圖", inline=False)
        embed_general.add_field(name="/幫助", value="🛠️ 使用幫助", inline=False)
        embed_general.add_field(name="/關於", value="ℹ️ 關於 Saiu 的資訊", inline=False)
        
        embed_admin = discord.Embed(title="管理員指令", color=0xff3846, description="需要管理員權限才能使用的指令")
        embed_admin.add_field(name="/設定", value="⚙️ 顯示或修改機器人的設定", inline=False)
        
        embed_owner = discord.Embed(title="擁有者指令", color=0x9b59b6, description="僅限機器人擁有者使用的指令")
        embed_owner.add_field(name="/伺服器列表", value="🤖 顯示機器人加入的伺服器列表與狀態", inline=False)
        embed_owner.add_field(name="/退出", value="🚪 強制退出指定的伺服器", inline=False)
        embed_owner.add_field(name="/廣播", value="📢 對所有已開啟自動推送的伺服器發送系統廣播", inline=False)
        embed_owner.add_field(name="/關閉", value="🛑 關閉 BOT", inline=False)
        embed_owner.add_field(name="/重啟", value="🔄 重新啟動機器人", inline=False)
        embed_owner.add_field(name="/資料", value="📊 測試並顯示各氣象模組的最新數據狀況", inline=False)

        self.pages = [embed_general, embed_admin, embed_owner]
        self.update_buttons()

    def update_buttons(self):
        # 若在第一頁則禁用上一頁，在最後一頁則禁用下一頁
        self.children[0].disabled = self.current_page == 0
        self.children[2].disabled = self.current_page == len(self.pages) - 1
        # 更新頁碼指示器
        self.children[1].label = f"第 {self.current_page + 1} / {len(self.pages)} 頁"

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary, row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="第 1 / 3 頁", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass # 這個按鈕只作為文字顯示用，永遠被禁用

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="幫助", description="顯示 Saiu 的使用幫助與可用指令清單")
    async def help_command(self, interaction: discord.Interaction):
        message_content = "🛠️ Saiu 使用幫助"
        view = HelpView()
        await interaction.response.send_message(content=message_content, embed=view.pages[0], view=view)

async def setup(bot):
    bot.remove_command("help") # 移除 discord.py 預設的 help 指令
    await bot.add_cog(HelpCog(bot))