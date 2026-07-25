import discord
from discord.ext import commands
from discord import app_commands

class HelpView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.current_page = 0
        
        # 建立 Embed 分頁
        embed_obs = discord.Embed(title="天氣觀測指令", color=0x3498db, description="顯示各類即時氣象觀測資料。")
        embed_obs.add_field(name="🌤️ /現在天氣", value="顯示即時天氣觀測資料", inline=False)
        embed_obs.add_field(name="⚡ /閃電", value="最新的閃電觀測圖", inline=False)
        embed_obs.add_field(name="🛰️ /衛星雲圖", value="最新的衛星雲圖", inline=False)
        embed_obs.add_field(name="📡 /雷達回波", value="最新的雷達回波圖", inline=False)
        embed_obs.add_field(name="🍃 /空氣品質", value="最新的空氣品質資料", inline=False)
        embed_obs.add_field(name="🌌 /太空天氣", value="最新的太空天氣概覽", inline=False)
        embed_obs.add_field(name="🌘 /天文資訊", value="查詢潮汐、日月運轉資訊", inline=False)
        embed_obs.add_field(name="📷 /即時影像", value="獲取即時影像與測站資訊", inline=False)
        embed_obs.add_field(name="✈️ /機場天氣", value="各機場最新的 METAR 天氣資料", inline=False)
        embed_obs.add_field(name="📊 /氣候監測", value="查詢台灣最新的氣候監測與聖嬰指標", inline=False)

        embed_forecast = discord.Embed(title="預報與統計指令", color=0x2ecc71, description="提供天氣預測與今日氣象統計。")
        embed_forecast.add_field(name="🌤️ /天氣預報", value="指定地點未來 36 小時的天氣預報", inline=False)
        embed_forecast.add_field(name="🧮 /換算", value="氣象單位換算器 (風速、氣溫、氣壓)", inline=False)
        embed_forecast.add_field(name="🎈 /氣壓排行", value="最新的氣壓觀測資料", inline=False)
        embed_forecast.add_field(name="☔ /雨量排行", value="今日台灣各測站的累積雨量排行", inline=False)
        embed_forecast.add_field(name="🌧️ /降雨預警", value="指定地點未來 1 小時內的降雨預測", inline=False)
        embed_forecast.add_field(name="🌡️ /氣溫排行", value="台灣各測站的現在氣溫或今日極端溫排行", inline=False)
        embed_forecast.add_field(name="💨 /風力排行", value="台灣各測站的現在風速排行與最新觀測圖", inline=False)
        embed_forecast.add_field(name="🌀 /颱風動態", value="台灣各縣市的暴風圈侵襲機率與颱風最新路徑圖", inline=False)
        embed_forecast.add_field(name="🏆 /今日氣象記錄", value="今日綜合氣象記錄看板", inline=False)
        embed_forecast.add_field(name="🌧️ /定量降水預報", value="最新的定量降水預報圖", inline=False)
        embed_forecast.add_field(name="💧 /相對濕度排行", value="台灣各測站的即時相對濕度排行與分布圖", inline=False)
        embed_forecast.add_field(name="⏳ /空氣品質排行", value="台灣各測站的空氣品質排行榜列表與分布圖", inline=False)
        
        embed_disaster = discord.Embed(title="災防與民生指令", color=0xe74c3c, description="地震、停班課等生活防災資訊。")
        embed_disaster.add_field(name="🏚️ /地震列表", value="最新 10 筆地震報告", inline=False)
        embed_disaster.add_field(name="🚄 /交通狀況", value="全台高鐵、台鐵即時營運狀況與異動通報", inline=False)
        embed_disaster.add_field(name="💡 /台電發電", value="現在各能源別即時發電量小計", inline=False)
        embed_disaster.add_field(name="💧 /淹水查詢", value="查詢指定地區目前的積淹水深度", inline=False)
        embed_disaster.add_field(name="🎒 /停班停課", value="查詢人事行政總處的停班停課資訊", inline=False)
        embed_disaster.add_field(name="✈️ /附近飛機", value="現在台灣西南方飛機的 ADS-B 訊號", inline=False)
        embed_disaster.add_field(name="📰 /氣象新聞", value="獲取公視最新的氣象、天災、水情相關新聞", inline=False)
        embed_disaster.add_field(name="🌋 /大屯火山監測", value="查詢大屯火山觀測站即時觀測資料", inline=False)
        
        embed_settings = discord.Embed(title="伺服器設定指令", color=0xf39c12, description="伺服器自動推播與管理設定。")
        embed_settings.add_field(name="🔗 /邀請", value="獲取邀請機器人的網址", inline=False)
        embed_settings.add_field(name="💭 /問題回報", value="表單回報、GitHub Issue 與 EEW 許可申請", inline=False)
        embed_settings.add_field(name="⚙️ /加入", value="在此頻道設定各類自動推播\n(預設需管理員權限)", inline=False)
        embed_settings.add_field(name="⚙️ /設定", value="顯示或修改伺服器的各種預警與廣播設定\n(預設需管理員權限)", inline=False)

        embed_context_menu = discord.Embed(title="右鍵選單指令", color=0x9b59b6, description="對訊息右鍵、長按 ➡️ 應用程式 即可使用的快捷指令。")
        embed_context_menu.add_field(name="🗑️ 刪除訊息", value="刪除由自己呼叫出來的機器人訊息", inline=False)
        embed_context_menu.add_field(name="📌 收藏此訊息", value="將機器人的氣象警報或訊息私訊備份給自己", inline=False)
        embed_context_menu.add_field(name="🌤️ 查詢此地天氣", value="從聊天訊息中自動提取地名，並查詢當地天氣", inline=False)
        embed_context_menu.add_field(name="🔄 重新整理資料", value="重新獲取該訊息的最新資料\n(限 24 小時內，且僅限原呼叫者可用)", inline=False)

        self.pages = [
            {"label": "天氣觀測指令", "emoji": "🛰️", "embed": embed_obs},
            {"label": "預報與統計指令", "emoji": "📊", "embed": embed_forecast},
            {"label": "災防與民生指令", "emoji": "🚨", "embed": embed_disaster},
            {"label": "伺服器設定指令", "emoji": "⚙️", "embed": embed_settings},
            {"label": "右鍵選單指令", "emoji": "🖱️", "embed": embed_context_menu},
        ]
        
        options = []
        for i, page in enumerate(self.pages):
            options.append(discord.SelectOption(
                label=page["label"], 
                value=str(i), 
                emoji=page["emoji"],
                default=(i == self.current_page)
            ))

        self.select = discord.ui.Select(placeholder="選擇要查看的指令類別...", options=options, row=0)
        self.select.callback = self.select_callback
        
        self.prev_btn = discord.ui.Button(emoji="⬅️", style=discord.ButtonStyle.primary, row=1)
        self.prev_btn.callback = self.prev_page
        
        self.page_indicator = discord.ui.Button(label="", style=discord.ButtonStyle.secondary, disabled=True, row=1)
        
        self.next_btn = discord.ui.Button(emoji="➡️", style=discord.ButtonStyle.primary, row=1)
        self.next_btn.callback = self.next_page
        
        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def update_components(self):
        self.clear_items()
        
        for i, opt in enumerate(self.select.options):
            opt.default = (i == self.current_page)
        
        self.prev_btn.disabled = self.current_page == 0
        self.next_btn.disabled = self.current_page == len(self.pages) - 1
        self.page_indicator.label = f"第 {self.current_page + 1} / {len(self.pages)} 頁"
        
        self.add_item(self.select)
        self.add_item(self.prev_btn)
        self.add_item(self.page_indicator)
        self.add_item(self.next_btn)

    async def select_callback(self, interaction: discord.Interaction):
        self.current_page = int(self.select.values[0])
        self.update_components()
        await interaction.response.edit_message(embed=self.pages[self.current_page]["embed"], view=self)

    async def prev_page(self, interaction: discord.Interaction):
        self.current_page -= 1
        self.update_components()
        await interaction.response.edit_message(embed=self.pages[self.current_page]["embed"], view=self)

    async def next_page(self, interaction: discord.Interaction):
        self.current_page += 1
        self.update_components()
        await interaction.response.edit_message(embed=self.pages[self.current_page]["embed"], view=self)

class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="幫助", description="🛠️ 顯示小裁雨的可用指令清單 Help")
    async def help_command(self, interaction: discord.Interaction):
        message_content = "🛠️ Saiu 使用幫助"
        view = HelpView(interaction.user.id)
        await interaction.response.send_message(content=message_content, embed=view.pages[0]["embed"], view=view)

async def setup(bot):
    bot.remove_command("help") # 移除 discord.py 預設的 help 指令
    await bot.add_cog(HelpCog(bot))