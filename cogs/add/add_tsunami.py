"""海嘯通知加入流程的沿海分區選擇。"""

import discord
from modules.database import get_all_settings, save_all_settings
from cogs.add.add_roles import RoleSetupView
from modules.tsunami import REGIONS, ALL_REGIONS


class TsunamiRegionJoinSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=ALL_REGIONS, description="接收所有沿海分區的海嘯通知")]
        options += [discord.SelectOption(label=name, description=desc) for name, desc in REGIONS.items()]
        super().__init__(placeholder="選擇海嘯通知分區（可多選）", options=options, min_values=1, max_values=7)

    async def callback(self, interaction):
        settings = get_all_settings()
        alerts = settings.setdefault(str(interaction.guild_id), {}).setdefault("tsunami_alerts", {})
        regions = [ALL_REGIONS] if ALL_REGIONS in self.values else self.values
        for region in regions:
            previous = alerts.get(region, {})
            alerts[region] = {"channel_id": interaction.channel_id,
                              "include_information": previous.get("include_information", False),
                              "detailed_format": region == ALL_REGIONS or previous.get("detailed_format", False)}
        save_all_settings(settings)
        if ALL_REGIONS not in regions:
            await interaction.response.edit_message(
                content=f"📍 已設定 **{'、'.join(regions)}**。請選擇海嘯通知格式：\n簡易：報文摘要與訂閱分區預估。\n詳細：地震資訊、完整報文、各區預估與潮位站實測。",
                view=TsunamiFormatJoinView(str(interaction.guild_id), regions, interaction.user.id))
            return
        await interaction.response.edit_message(
            content=f"✅ 已將 **{'、'.join(regions)}** 的海嘯通知設定至此頻道，固定使用詳細格式。\n可使用 `/設定` 調整海嘯資訊推播類型。\n可繼續設定通知標記身分組：",
            view=RoleSetupView("tsunami"))


class TsunamiFormatJoinView(discord.ui.View):
    def __init__(self, guild_id, regions, author_id):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.regions = regions
        self.author_id = author_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 此選單只能由原指令使用者操作。", ephemeral=True)
            return False
        return True

    async def set_format(self, interaction, detailed):
        settings = get_all_settings()
        alerts = settings.get(self.guild_id, {}).get("tsunami_alerts", {})
        for region in self.regions:
            if region in alerts:
                alerts[region]["detailed_format"] = region == ALL_REGIONS or detailed
        save_all_settings(settings)
        await interaction.response.edit_message(
            content=f"✅ 海嘯通知已設定為{'詳細' if detailed else '簡易'}格式。\n可使用 `/設定` 調整格式及海嘯資訊推播類型。\n可繼續設定通知標記身分組：",
            view=RoleSetupView("tsunami"))

    @discord.ui.button(label="簡易通知", style=discord.ButtonStyle.secondary)
    async def simple(self, interaction, button):
        await self.set_format(interaction, False)

    @discord.ui.button(label="詳細通知", style=discord.ButtonStyle.secondary)
    async def detailed(self, interaction, button):
        await self.set_format(interaction, True)


async def setup(bot):
    pass
