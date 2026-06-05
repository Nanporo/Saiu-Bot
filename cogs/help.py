import discord
from discord.ext import commands
from discord import app_commands

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.current_page = 0
        
        # 建立 Embed 分頁
        embed_obs = discord.Embed(title="天氣觀測指令", color=0x3498db, description="顯示各類即時氣象觀測資料")
        embed_obs.add_field(name="/衛星雲圖", value="🛰️ 顯示最新的衛星雲圖", inline=False)
        embed_obs.add_field(name="/雷達回波", value="📡 顯示最新的雷達回波圖", inline=False)
        embed_obs.add_field(name="/即時閃電", value="⚡ 顯示最新的即時閃電觀測圖", inline=False)
        embed_obs.add_field(name="/機場天氣", value="✈️ 查詢臺灣各機場的最新 METAR 天氣資料", inline=False)
        embed_obs.add_field(name="/電離層電波吸收", value="🌌 顯示最新的電離層 D 區電波吸收預測圖", inline=False)

        embed_forecast = discord.Embed(title="預報與統計指令", color=0x2ecc71, description="提供天氣預測與今日氣象統計")
        embed_forecast.add_field(name="/降雨預警", value="🌧️ 查詢指定地點未來 1 小時內的降雨預測", inline=False)
        embed_forecast.add_field(name="/今日雨量", value="☔ 查詢今日台灣各測站的累積雨量排行", inline=False)
        embed_forecast.add_field(name="/氣溫排行", value="🌡️ 查詢台灣各測站的現在氣溫或今日極端溫排行", inline=False)
        embed_forecast.add_field(name="/今日氣象記錄", value="🏆 查詢今日綜合氣象記錄看板", inline=False)
        embed_forecast.add_field(name="/定量降水預報", value="🌧️ 顯示最新的定量降水預報圖 (QPF)", inline=False)
        embed_forecast.add_field(name="/颱風侵襲機率", value="🌀 查詢台灣各縣市的暴風圈侵襲機率", inline=False)

        embed_disaster = discord.Embed(title="災防與民生指令", color=0xe74c3c, description="地震、停班課等生活防災資訊")
        embed_disaster.add_field(name="/地震列表", value="🏚️ 查詢最新 10 筆地震報告", inline=False)
        embed_disaster.add_field(name="/台電發電", value="💡 查詢各能源別即時發電量小計", inline=False)
        embed_disaster.add_field(name="/停班停課", value="🎒 手動查詢人事行政總處的停班停課資訊", inline=False)
        
        embed_settings = discord.Embed(title="伺服器設定指令", color=0xf39c12, description="伺服器自動推播與管理設定")
        embed_settings.add_field(name="/加入", value="⚙️ 在此頻道設定各類自動推播 (包含降雨、氣溫等，預設需管理員權限)", inline=False)
        embed_settings.add_field(name="/設定", value="⚙️ 顯示或修改伺服器的各種預警與廣播設定 (預設需管理員權限)", inline=False)
        
        embed_owner = discord.Embed(title="擁有者指令", color=0x2a9683, description="僅限機器人擁有者使用的指令")
        embed_owner.add_field(name="/關機", value="🛑 關閉 BOT", inline=False)
        embed_owner.add_field(name="/重啟", value="🔄 重新啟動機器人", inline=False)
        embed_owner.add_field(name="/退出", value="🚪 強制退出指定的伺服器", inline=False)
        embed_owner.add_field(name="/資料", value="📊 測試並顯示各氣象模組的最新數據狀況", inline=False)
        embed_owner.add_field(name="/廣播", value="📢 對所有已開啟自動推送的伺服器發送系統廣播", inline=False)
        embed_owner.add_field(name="/伺服器列表", value="🤖 顯示機器人加入的伺服器列表與狀態", inline=False)

        self.pages = [embed_obs, embed_forecast, embed_disaster, embed_settings, embed_owner]
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

    @discord.ui.button(label="第 1 / 5 頁", style=discord.ButtonStyle.secondary, disabled=True, row=0)
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