import discord
from modules.tsunami import ALL_REGIONS, uses_detailed_format
from cogs.settings.settings_utils import load_settings, save_settings, SpecificMentionRoleSelect


class TsunamiTargetSelect(discord.ui.Select):
    def __init__(self, alerts, target):
        super().__init__(placeholder="選擇要調整的海嘯通知分區", options=[
            discord.SelectOption(label=r, default=r == target) for r in alerts])

    async def callback(self, interaction):
        view = TsunamiAlertSettingsView(self.view.guild_id, self.values[0])
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


class TsunamiChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(placeholder="選擇通知頻道", channel_types=[discord.ChannelType.text])

    async def callback(self, interaction):
        await self.view.update_subscription(interaction, channel_id=self.values[0].id)


class TsunamiTypeSelect(discord.ui.Select):
    def __init__(self, include_information):
        super().__init__(placeholder="選擇接收的海嘯通知類型", options=[
            discord.SelectOption(label="顯示所有類型", value="all", default=include_information),
            discord.SelectOption(label="排除海嘯消息", value="warnings", default=not include_information,
                                 description="海嘯消息及其解除通知將不會通知")])

    async def callback(self, interaction):
        await self.view.update_subscription(interaction, include_information=self.values[0] == "all")


class TsunamiFormatButton(discord.ui.Button):
    def __init__(self, region, subscription):
        detailed = uses_detailed_format(region, subscription)
        label = "格式：詳細（固定）" if region == ALL_REGIONS else ("格式：詳細" if detailed else "格式：簡易")
        super().__init__(label=label, style=discord.ButtonStyle.success if detailed else discord.ButtonStyle.secondary,
                         emoji="🖼️", disabled=region == ALL_REGIONS, row=4)

    async def callback(self, interaction):
        settings = load_settings()
        sub = settings.get(self.view.guild_id, {}).get("tsunami_alerts", {}).get(self.view.target_loc)
        if sub is not None:
            sub["detailed_format"] = (True if self.view.target_loc == ALL_REGIONS else
                                       not sub.get("detailed_format", False))
            save_settings(settings)
        await self.view.refresh(interaction, self.view.target_loc)


class TsunamiAlertSettingsView(discord.ui.View):
    def __init__(self, guild_id, target_loc=None):
        super().__init__(timeout=300)
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()
        self.settings = self.all_settings.get(self.guild_id, {})
        alerts = self.settings.get("tsunami_alerts", {})
        self.target_loc = target_loc if target_loc in alerts else None
        if alerts:
            self.add_item(TsunamiTargetSelect(alerts, self.target_loc))
        if self.target_loc:
            self.add_item(TsunamiChannelSelect())
            self.add_item(TsunamiTypeSelect(alerts[self.target_loc].get("include_information", False)))
        self.add_item(SpecificMentionRoleSelect("tsunami_mention_role_id"))
        back = discord.ui.Button(label="返回", emoji="↩️")
        back.callback = self.back_callback
        self.add_item(back)
        if self.target_loc:
            self.add_item(TsunamiFormatButton(self.target_loc, alerts[self.target_loc]))
            remove = discord.ui.Button(label="解除此分區通知", style=discord.ButtonStyle.danger, row=4)
            remove.callback = self.remove_callback
            self.add_item(remove)

    async def update_subscription(self, interaction, **changes):
        # Read fresh settings so an old menu does not overwrite concurrent edits.
        settings = load_settings()
        sub = settings.get(self.guild_id, {}).get("tsunami_alerts", {}).get(self.target_loc)
        if sub is not None:
            sub.update(changes)
            if self.target_loc == ALL_REGIONS:
                sub["detailed_format"] = True
            save_settings(settings)
        await self.refresh(interaction, self.target_loc)

    async def refresh(self, interaction, target=None):
        view = self.__class__(self.guild_id, target)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    async def remove_callback(self, interaction):
        settings = load_settings()
        guild = settings.get(self.guild_id, {})
        alerts = guild.get("tsunami_alerts", {})
        alerts.pop(self.target_loc, None)
        if not alerts:
            guild.pop("tsunami_alerts", None)
        save_settings(settings)
        await self.refresh(interaction)

    async def back_callback(self, interaction):
        if self.target_loc:
            await self.refresh(interaction)
        else:
            from cogs.settings.settings_main import SettingsView
            view = SettingsView(self.guild_id)
            await interaction.response.edit_message(embed=view.build_embed(), view=view)

    def build_embed(self):
        embed = discord.Embed(title="`🌊` 海嘯通知設定", color=0x41809b,
            description="選擇分區以調整頻道、通知類型及顯示格式。\n全台接收固定只能使用詳細格式。\n後續更新與解除報未列分區時，依海嘯編號沿用該事件分區。")
        role = self.settings.get("tsunami_mention_role_id")
        embed.add_field(name="自動標記身分組", value=f"<@&{role}>" if role else "未設定", inline=False)
        alerts = self.settings.get("tsunami_alerts", {})
        for region, sub in alerts.items():
            mode = "所有類型" if sub.get("include_information", False) else "排除海嘯消息"
            fmt = "詳細（固定）" if region == ALL_REGIONS else ("詳細" if uses_detailed_format(region, sub) else "簡易")
            embed.add_field(name=f"📍 {region}", value=f"發送至：<#{sub['channel_id']}>\n類型：{mode}\n格式：{fmt}", inline=True)
        if not alerts:
            embed.add_field(name="尚未啟用", value="請使用 `/加入` 選擇「海嘯通知」。")
        return embed


async def setup(bot):
    pass
