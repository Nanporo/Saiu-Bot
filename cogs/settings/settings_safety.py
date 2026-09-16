import discord
from cogs.settings.settings_utils import load_settings, save_settings, SpecificMentionRoleSelect, ClearMentionRoleButton

class TargetChannelSelectForSafety(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            channel_types=[discord.ChannelType.text],
            placeholder="選擇平安通報發送頻道",
            min_values=1,
            max_values=1,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        ch_id = self.values[0].id
        view.settings['safety_alerts'] = {'channel_id': ch_id}
        view.all_settings[view.guild_id] = view.settings
        save_settings(view.all_settings)

        new_view = SafetyAlertSettingsView(view.guild_id)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class DisableSafetyAlertButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="關閉平安通報", emoji="🗑️", row=2)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.settings.pop('safety_alerts', None)
        view.all_settings[view.guild_id] = view.settings
        save_settings(view.all_settings)

        new_view = SafetyAlertSettingsView(view.guild_id)
        await interaction.response.edit_message(embed=new_view.build_embed(), view=new_view)

class SafetyAlertSettingsView(discord.ui.View):
    def __init__(self, guild_id: str, target_loc: str = None):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.target_loc = target_loc
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {})

        self.add_item(TargetChannelSelectForSafety())
        self.add_item(SpecificMentionRoleSelect("safety_mention_role_id", placeholder="選擇通報自動標記身分組", row=1))

        back_btn = discord.ui.Button(label="返回", style=discord.ButtonStyle.secondary, emoji="↩️", row=2)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

        if 'safety_alerts' in self.settings:
            self.add_item(DisableSafetyAlertButton())

        if self.settings.get("safety_mention_role_id"):
            self.add_item(ClearMentionRoleButton("safety_mention_role_id", row=2))

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="`🏡` 平安通報設定",
            description="管理當前伺服器的重大災害平安通報發送頻道與設定。",
            color=0x4cd4af
        )
        is_enabled = 'safety_alerts' in self.settings
        status_str = "`🟢` 已啟用" if is_enabled else "`🔴` 已停用"

        alerts = self.settings.get('safety_alerts', {})
        ch_id = alerts.get('channel_id') if isinstance(alerts, dict) else alerts
        ch_status = f"<#{ch_id}>" if ch_id else "⚠️ 未設定"

        role_id = self.settings.get('safety_mention_role_id')
        role_status = f"<@&{role_id}>" if role_id else "⚠️ 未設定"

        embed.add_field(name="狀態", value=status_str, inline=True)
        embed.add_field(name="發送頻道", value=ch_status, inline=True)
        embed.add_field(name="標記身分組", value=role_status, inline=True)
        embed.add_field(
            name="說明",
            value="當發生規模 >= 6.3 且最大震度 >= 5弱之強震或其它大規模災害時，將會於指定頻道發送平安通報統計面板，供伺服器成員即時回報現況。",
            inline=False
        )
        return embed

    async def back_callback(self, interaction: discord.Interaction):
        from cogs.settings.settings_main import SettingsView
        view = SettingsView(int(self.guild_id))
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

async def setup(bot):
    pass
