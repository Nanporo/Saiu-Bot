"""交通通知加入流程的運具選擇。"""

import discord
from modules.database import get_all_settings, save_all_settings
from cogs.add.add_roles import RoleSetupView
from cogs.settings.settings_traffic import get_available_traffic_modes, MODE_NAMES


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


async def setup(bot):
    pass
