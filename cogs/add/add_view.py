import discord
from modules.database import get_all_settings, save_all_settings
from cogs.add.add_modals import EqModal, EewModal, TownModal, RoleSetupView
from cogs.settings.settings_traffic import (
    NO_TRAIN_COUNTIES,
    get_available_traffic_modes,
    MODE_NAMES
)

COUNTIES = [
    "基隆市", "臺北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣",
    "臺中市", "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣", "臺南市",
    "高雄市", "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣"
]

class CountySelect(discord.ui.Select):
    def __init__(self, alert_type):
        self.alert_type = alert_type
        
        if alert_type == "traffic":
            # 交通狀況排除澎湖、金門、連江，並在首位加上「全台接收」
            county_list = [c for c in COUNTIES if c not in NO_TRAIN_COUNTIES]
            county_list.insert(0, "全台接收")
        elif alert_type in ["suspension", "typhoon"]:
            county_list = list(COUNTIES)
            county_list.insert(0, "全台接收")
        else:
            county_list = list(COUNTIES)
            
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
            if len(alerts) >= 20 and county not in alerts:
                await interaction.response.edit_message(content="❌ 本伺服器已達到最多 20 個停班課通知地點的上限！", view=None)
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

        elif self.alert_type == "traffic":
            # 交通狀況：進入第二步驟，讓使用者選擇該縣市支援的推播交通方式
            view = TrafficModeJoinView(
                county=county,
                channel_id=channel_id,
                author_id=interaction.user.id
            )
            modes = get_available_traffic_modes(county)
            modes_str = "、".join([m["label"] for m in modes])
            content = (
                f"⚙️ **設定 交通狀況通知**\n"
                f"📍 已選擇地點：**{county}**（支援運具：**{modes_str}**）\n\n"
                f"請透過下方選單選擇**要接收推播的交通方式** (可多選)："
            )
            await interaction.response.edit_message(content=content, view=view)
            return

        save_all_settings(settings)
            
        view = RoleSetupView(self.alert_type)
        msg += "\n\n💡 **是否要設定標記身分組？**\n如果您希望在預警時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："
        await interaction.response.edit_message(content=msg, view=view)

class TrafficModeSelectForJoin(discord.ui.Select):
    def __init__(self, county: str):
        self.county = county
        available_modes = get_available_traffic_modes(county)
        options = []
        for m in available_modes:
            options.append(discord.SelectOption(
                label=m["label"],
                value=m["code"],
                default=True
            ))
        super().__init__(
            placeholder=f"選擇推播的交通方式 ({county})",
            options=options,
            min_values=1,
            max_values=len(available_modes)
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        selected_modes = self.values
        guild_id = str(interaction.guild_id)
        channel_id = view.channel_id
        county = view.county

        settings = get_all_settings()
        if guild_id not in settings:
            settings[guild_id] = {}

        alerts = settings[guild_id].setdefault('traffic_alerts', {})
        alerts[county] = {
            'channel_id': channel_id,
            'enabled_modes': selected_modes
        }
        save_all_settings(settings)

        modes_str = "、".join([MODE_NAMES.get(m, m) for m in selected_modes])
        next_view = RoleSetupView("traffic")
        msg = (
            f"✅ 已成功將 **{county}** 的交通營運狀況推播設定至此頻道！\n"
            f"• 推播運具：**{modes_str}**\n\n"
            f"💡 **是否要設定標記身分組？**\n"
            f"如果您希望在異動時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："
        )
        await interaction.response.edit_message(content=msg, view=next_view)

class AcceptAllTrafficModesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="全部接收 (預設)", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        modes = get_available_traffic_modes(view.county)
        selected_modes = [m["code"] for m in modes]
        guild_id = str(interaction.guild_id)
        channel_id = view.channel_id
        county = view.county

        settings = get_all_settings()
        if guild_id not in settings:
            settings[guild_id] = {}

        alerts = settings[guild_id].setdefault('traffic_alerts', {})
        alerts[county] = {
            'channel_id': channel_id,
            'enabled_modes': selected_modes
        }
        save_all_settings(settings)

        modes_str = "、".join([m["label"] for m in modes])
        next_view = RoleSetupView("traffic")
        msg = (
            f"✅ 已成功將 **{county}** 的交通營運狀況推播設定至此頻道！\n"
            f"• 推播運具：**{modes_str}**\n\n"
            f"💡 **是否要設定標記身分組？**\n"
            f"如果您希望在異動時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："
        )
        await interaction.response.edit_message(content=msg, view=next_view)

class TrafficModeJoinView(discord.ui.View):
    def __init__(self, county: str, channel_id: int, author_id: int):
        super().__init__(timeout=180)
        self.county = county
        self.channel_id = channel_id
        self.author_id = author_id
        self.add_item(TrafficModeSelectForJoin(county))
        self.add_item(AcceptAllTrafficModesButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

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

class SafetySetupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="確認在此頻道開啟平安通報", style=discord.ButtonStyle.success, emoji="🏡")

    async def callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        channel_id = interaction.channel_id
        settings = get_all_settings()
        if guild_id not in settings:
            settings[guild_id] = {}
        settings[guild_id]['safety_alerts'] = {'channel_id': channel_id}
        save_all_settings(settings)
        view = RoleSetupView("safety")
        msg = f"✅ 已成功將 **平安通報** 推播設定至 <#{channel_id}>！\n\n💡 **是否要設定標記身分組？**\n如果您希望在平安通報時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："
        await interaction.response.edit_message(content=msg, view=view)

class AlertSetupView(discord.ui.View):
    def __init__(self, alert_type, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        if alert_type in ["suspension", "typhoon", "traffic"]:
            self.add_item(CountySelect(alert_type))
        elif alert_type == "earthquake":
            self.add_item(EqSetupButton())
        elif alert_type == "safety":
            self.add_item(SafetySetupButton())
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
