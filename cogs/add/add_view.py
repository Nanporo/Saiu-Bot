import discord
from modules.database import get_all_settings, save_all_settings
from cogs.add.add_modals import EqModal, EewModal, TownModal

COUNTIES = [
    "基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣",
    "臺中市", "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣", "臺南市",
    "高雄市", "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣"
]

class CountySelect(discord.ui.Select):
    def __init__(self, alert_type):
        self.alert_type = alert_type
        
        county_list = list(COUNTIES)
        if alert_type in ["suspension", "typhoon"]:
            county_list.insert(0, "全台接收")
            
            
        options = [discord.SelectOption(label=c, value=c) for c in county_list]
        super().__init__(placeholder="請選擇要通知的縣市...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        county = self.values[0]
        guild_id = str(interaction.guild_id)
        channel_id = interaction.channel_id
        
        settings = get_all_settings()
            
        if guild_id not in settings:
            settings[guild_id] = {}
            
        if self.alert_type == "suspension":
            alerts = settings[guild_id].setdefault('suspension_alerts', {})
            if len(alerts) >= 24 and county not in alerts:
                await interaction.response.edit_message(content="❌ 本伺服器已達到最多 24 個停班課通知地點的上限！", view=None)
                return
            alerts[county] = channel_id
            msg = f"✅ 已成功將 **{county}** 的停班課推播設定至此頻道！"
            
        elif self.alert_type == "typhoon":
            alerts = settings[guild_id].setdefault('typhoon_alerts', {})
            if len(alerts) >= 10:
                await interaction.response.edit_message(content="❌ 每個伺服器最多只能設定 10 個颱風通知地點。", view=None)
                return
            alerts[county] = {'channel_id': channel_id}
            if county == "全台接收":
                msg = f"✅ 已成功設定！未來當發布任何**颱風警報**時，將會自動通知此頻道。"
            else:
                msg = f"✅ 已成功設定！未來當發布 **{county}** 的颱風暴風圈侵襲機率達 75% 以上時，將會自動通知此頻道。"

        save_all_settings(settings)
            
        await interaction.response.edit_message(content=msg, view=None)

class TownSetupButton(discord.ui.Button):
    def __init__(self, alert_type):
        label = "點此輸入地點 (可填寫: 全台接收)" if alert_type in ["cbs", "eew"] else "點此輸入鄉鎮市區名稱"
        super().__init__(label=label, style=discord.ButtonStyle.primary, emoji="✍️")
        self.alert_type = alert_type

    async def callback(self, interaction: discord.Interaction):
        if self.alert_type == "eew":
            await interaction.response.send_modal(EewModal())
        else:
            await interaction.response.send_modal(TownModal(self.alert_type))

class EqSetupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="點此輸入地點與觸發條件（可填寫：全台接收）", style=discord.ButtonStyle.primary, emoji="✍️")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(EqModal())

class AlertSetupView(discord.ui.View):
    def __init__(self, alert_type, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        if alert_type in ["suspension", "typhoon"]:
            self.add_item(CountySelect(alert_type))
        elif alert_type == "earthquake":
            self.add_item(EqSetupButton())
        elif alert_type in ["rain", "temp", "cbs", "flood", "eew", "aqi"]:
            # 因為台灣鄉鎮市區高達368個，超過下拉選單的25個選項限制，改以「按鈕開啟填寫彈窗」實作
            self.add_item(TownSetupButton(alert_type))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

async def setup(bot):
    pass
