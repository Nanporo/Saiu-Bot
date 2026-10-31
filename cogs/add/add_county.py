"""停班停課、颱風與交通通知加入流程的縣市選擇。"""

import discord
from modules.database import get_all_settings, save_all_settings
from cogs.add.add_roles import RoleSetupView
from cogs.add.add_traffic import TrafficModeJoinView
from cogs.settings.settings_traffic import NO_TRAIN_COUNTIES, get_available_traffic_modes


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


async def setup(bot):
    pass
