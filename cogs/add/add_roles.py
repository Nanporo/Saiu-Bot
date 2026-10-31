"""加入通知流程共用的身分組設定。"""

import discord
from modules.database import get_all_settings, save_all_settings


PREFIX_MAP = {
    "cbs": "cbs",
    "eew": "eew",
    "rain": "rain",
    "temp": "temp",
    "tsunami": "tsunami",
    "earthquake": "eq",
    "typhoon": "typhoon",
    "suspension": "suspension",
    "aqi": "aqi",
    "traffic": "traffic",
    "safety": "safety"
}


class RoleSetupView(discord.ui.View):
    def __init__(self, alert_type):
        super().__init__(timeout=120)
        self.alert_type = alert_type

        self.select = discord.ui.RoleSelect(placeholder="請選擇要標記的身分組 (可留空)", min_values=1, max_values=1, row=0)
        self.select.callback = self.role_callback
        self.add_item(self.select)

        skip_btn = discord.ui.Button(label="不需要標記 (留空)", style=discord.ButtonStyle.secondary, row=1)
        skip_btn.callback = self.skip_callback
        self.add_item(skip_btn)

    async def role_callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        settings = get_all_settings()
        if guild_id not in settings:
            settings[guild_id] = {}

        prefix = PREFIX_MAP.get(self.alert_type, self.alert_type)
        role_id = self.select.values[0].id
        settings[guild_id][f"{prefix}_mention_role_id"] = role_id
        save_all_settings(settings)

        await interaction.response.edit_message(content=interaction.message.content + f"\n\n✅ 已成功將此項目的標記身分組設定為 <@&{role_id}>！\n您隨時可使用 `/設定` 指令進行變更。", view=None)

    async def skip_callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content=interaction.message.content + "\n\n✅ 已留空 (不標記任何身分組)。\n您隨時可使用 `/設定` 指令進行變更。", view=None)


async def setup(bot):
    pass
