import discord
from discord.ext import commands
from discord import app_commands

from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow, Button, Select

class HelpView(LayoutView):
    def __init__(self, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.current_page = 0
        
        self.pages_data = [
            {
                "label": "天氣觀測指令", "emoji": "🛰️", "color": 0x3498db,
                "title": "天氣觀測指令", "desc": "顯示各類即時氣象觀測資料。",
                "commands": [
                    ("🌤️ `/現在天氣`", "顯示即時天氣觀測資料"),
                    ("⚡ `/閃電`", "最新的閃電觀測圖"),
                    ("🛰️ `/衛星雲圖`", "最新的衛星雲圖"),
                    ("📡 `/雷達回波`", "最新的雷達回波圖"),
                    ("🍃 `/空氣品質`", "最新的空氣品質資料"),
                    ("🌌 `/太空天氣`", "最新的太空天氣概覽"),
                    ("🌘 `/天文資訊`", "查詢潮汐、日月運轉資訊"),
                    ("📷 `/即時影像`", "獲取即時影像與測站資訊"),
                    ("✈️ `/機場天氣`", "各機場最新的 METAR 天氣資料"),
                    ("📊 `/氣候監測`", "查詢台灣最新的氣候監測與聖嬰指標"),
                ]
            },
            {
                "label": "預報與統計指令", "emoji": "📊", "color": 0x2ecc71,
                "title": "預報與統計指令", "desc": "提供天氣預測與今日氣象統計。",
                "commands": [
                    ("🌤️ `/天氣預報`", "指定地點未來 36 小時的天氣預報"),
                    ("🧮 `/換算`", "氣象單位換算器 (風速、氣溫、氣壓)"),
                    ("🎈 `/氣壓排行`", "最新的氣壓觀測資料"),
                    ("☔ `/雨量排行`", "今日台灣各測站的累積雨量排行"),
                    ("🌧️ `/降雨預警`", "指定地點未來 1 小時內的降雨預測"),
                    ("🌡️ `/氣溫排行`", "台灣各測站的現在氣溫或今日極端溫排行"),
                    ("💨 `/風力排行`", "台灣各測站的現在風速排行與最新觀測圖"),
                    ("🌀 `/颱風動態`", "台灣各縣市的暴風圈侵襲機率與颱風最新路徑圖"),
                    ("🏆 `/今日氣象記錄`", "今日綜合氣象記錄看板"),
                    ("🌧️ `/定量降水預報`", "最新的定量降水預報圖"),
                    ("💧 `/相對濕度排行`", "台灣各測站的即時相對濕度排行與分布圖"),
                    ("⏳ `/空氣品質排行`", "台灣各測站的空氣品質排行榜列表與分布圖"),
                ]
            },
            {
                "label": "災防與民生指令", "emoji": "🚨", "color": 0xe74c3c,
                "title": "災防與民生指令", "desc": "地震、停班課等生活防災資訊。",
                "commands": [
                    ("🏚️ `/地震列表`", "最新 10 筆地震報告"),
                    ("🚄 `/交通狀況`", "全台高鐵、台鐵即時營運狀況與異動通報"),
                    ("💡 `/台電發電`", "現在各能源別即時發電量小計"),
                    ("💧 `/淹水查詢`", "查詢指定地區目前的積淹水深度"),
                    ("🎒 `/停班停課`", "查詢人事行政總處的停班停課資訊"),
                    ("✈️ `/附近飛機`", "現在台灣西南方飛機的 ADS-B 訊號"),
                    ("📰 `/氣象新聞`", "獲取公視最新的氣象、天災、水情相關新聞"),
                    ("🏡 `/平安通報記錄`", "查詢過去 1 年內伺服器的平安通報與回報名單"),
                ]
            },
            {
                "label": "伺服器設定指令", "emoji": "⚙️", "color": 0xf39c12,
                "title": "伺服器設定指令", "desc": "伺服器自動推播與管理設定。",
                "commands": [
                    ("🔗 `/邀請`", "獲取邀請機器人的網址"),
                    ("💭 `/問題回報`", "表單回報、GitHub Issue 與 EEW 許可申請"),
                    ("⚙️ `/加入`", "在此頻道設定各類自動推播 (預設需管理員權限)"),
                    ("⚙️ `/設定`", "顯示或修改伺服器的各種預警與廣播設定 (預設需管理員權限)"),
                ]
            },
            {
                "label": "右鍵選單指令", "emoji": "🖱️", "color": 0x9b59b6,
                "title": "右鍵選單指令", "desc": "對訊息右鍵、長按 ➡️ 應用程式 即可使用的快捷指令。",
                "commands": [
                    ("🗑️ `刪除訊息`", "刪除由自己呼叫出來的機器人訊息"),
                    ("📌 `收藏此訊息`", "將機器人的氣象警報或訊息私訊備份給自己"),
                    ("🌤️ `查詢此地天氣`", "從聊天訊息中自動提取地名，並查詢當地天氣"),
                    ("🔄 `重新整理資料`", "重新獲取該訊息的最新資料 (限 24 小時內，且僅限原呼叫者可用)"),
                ]
            }
        ]
        
        self.prev_btn = Button(emoji="⬅️", style=discord.ButtonStyle.primary)
        self.prev_btn.callback = self.prev_page
        
        self.page_indicator = Button(label="", style=discord.ButtonStyle.secondary, disabled=True)
        
        self.next_btn = Button(emoji="➡️", style=discord.ButtonStyle.primary)
        self.next_btn.callback = self.next_page
        
        options = []
        for i, page in enumerate(self.pages_data):
            options.append(discord.SelectOption(
                label=page["label"],
                value=str(i),
                emoji=page["emoji"],
                default=(i == self.current_page)
            ))
        self.select = Select(placeholder="選擇要查看的指令類別...", options=options)
        self.select.callback = self.select_callback
        
        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def build_page_container(self, page_index: int) -> Container:
        page = self.pages_data[page_index]
        cmd_lines = []
        for name, desc in page["commands"]:
            cmd_lines.append(f"{name}\n{desc}")
        cmd_text = "\n\n".join(cmd_lines)
        
        return Container(
            TextDisplay(f"{page['desc']}"),
            Separator(),
            TextDisplay(cmd_text),
            Separator(),
            TextDisplay("-# 💡 提示：輸入 `/` 即可查看所有自動補全選項與參數說明"),
            accent_color=page["color"]
        )

    def update_components(self):
        self.clear_items()
        
        # 0. 頂部標題
        self.add_item(TextDisplay("🛠️ 小裁雨 使用幫助"))
        
        # 1. 類別選擇下拉選單
        for i, opt in enumerate(self.select.options):
            opt.default = (i == self.current_page)
        select_row = ActionRow(self.select)
        self.add_item(select_row)
        
        # 2. 當前類別卡片 Container
        container = self.build_page_container(self.current_page)
        self.add_item(container)
        
        # 3. 換頁控制項
        self.prev_btn.disabled = (self.current_page == 0)
        self.next_btn.disabled = (self.current_page == len(self.pages_data) - 1)
        self.page_indicator.label = f"第 {self.current_page + 1} / {len(self.pages_data)} 頁"
        nav_row = ActionRow(self.prev_btn, self.page_indicator, self.next_btn)
        self.add_item(nav_row)

    async def select_callback(self, interaction: discord.Interaction):
        self.current_page = int(self.select.values[0])
        self.update_components()
        await interaction.response.edit_message(view=self, embed=None)

    async def prev_page(self, interaction: discord.Interaction):
        self.current_page = max(0, self.current_page - 1)
        self.update_components()
        await interaction.response.edit_message(view=self, embed=None)

    async def next_page(self, interaction: discord.Interaction):
        self.current_page = min(len(self.pages_data) - 1, self.current_page + 1)
        self.update_components()
        await interaction.response.edit_message(view=self, embed=None)

class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="幫助", description="🛠️ 顯示小裁雨的可用指令清單 Help")
    async def help_command(self, interaction: discord.Interaction):
        view = HelpView(interaction.user.id)
        await interaction.response.send_message(view=view)

async def setup(bot):
    bot.remove_command("help") # 移除 discord.py 預設的 help 指令
    await bot.add_cog(HelpCog(bot))