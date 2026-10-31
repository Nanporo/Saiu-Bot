"""平安通報的加入流程。"""

import discord
from modules.database import get_all_settings, save_all_settings
from cogs.add.add_roles import RoleSetupView


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


async def setup(bot):
    pass
