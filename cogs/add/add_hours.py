"""加入通知流程共用的通知時段設定。"""

import discord
from modules.database import get_all_settings, save_all_settings
from cogs.add.add_roles import RoleSetupView


class NotifyHoursSetupView(discord.ui.View):
    """在設定預警地點後，引導用戶選擇允許通知的時段 (00:00~24:00 多選)"""
    def __init__(self, guild_id: str, alert_type: str, loc_val: str):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.alert_type = alert_type
        self.loc_val = loc_val

        options = []
        for i in range(24):
            options.append(discord.SelectOption(
                label=f"{i:02d}:00 ~ {(i+1)%24:02d}:00",
                value=str(i),
                default=True
            ))

        self.select = discord.ui.Select(
            placeholder="請選擇允許通知的時段 (可多選 0~24 小時)...",
            options=options,
            min_values=0,
            max_values=24,
            row=0
        )
        self.select.callback = self.select_callback
        self.add_item(self.select)

        skip_btn = discord.ui.Button(label="使用預設時段 (24小時全時段通知)", style=discord.ButtonStyle.secondary, row=1)
        skip_btn.callback = self.skip_callback
        self.add_item(skip_btn)

    async def proceed_to_next(self, interaction: discord.Interaction, selected_hours: list):
        settings = get_all_settings()
        alert_key = f"{self.alert_type}_alerts"
        if self.guild_id in settings and alert_key in settings[self.guild_id]:
            alerts = settings[self.guild_id][alert_key]
            if self.loc_val in alerts:
                if isinstance(alerts[self.loc_val], dict):
                    alerts[self.loc_val]['notify_hours'] = selected_hours
                else:
                    alerts[self.loc_val] = {
                        'channel_id': alerts[self.loc_val],
                        'notify_hours': selected_hours
                    }
                save_all_settings(settings)

        if len(selected_hours) == 24:
            hours_text = "24 小時全時段"
        elif len(selected_hours) == 0:
            hours_text = "皆不通知"
        else:
            hours_text = f"{len(selected_hours)} 個小時時段"

        msg = interaction.message.content + f"\n\n✅ 允許通知時段已設定為：**{hours_text}**"

        if self.alert_type == "rain":
            thunderstorm_status = settings.get(self.guild_id, {}).get('thunderstorm_alert', False)
            if thunderstorm_status:
                msg += "\n\n⛈️ 大雷雨即時訊息：**已開啟**"
                view = RoleSetupView(self.alert_type)
                msg += "\n\n💡 **是否要設定標記身分組？**\n如果您希望在預警時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："
            else:
                msg += "\n\n⛈️ **是否要開啟大雷雨即時訊息？**\n開啟後，當氣象署發布大雷雨即時訊息且影響區域包含您設定的地點時，將自動發送通知至降雨預警頻道。"
                from cogs.add.add_rain import ThunderstormToggleView
                view = ThunderstormToggleView(self.guild_id, self.alert_type)
        else:
            view = RoleSetupView(self.alert_type)
            msg += "\n\n💡 **是否要設定標記身分組？**\n如果您希望在預警時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："

        await interaction.response.edit_message(content=msg, view=view)

    async def select_callback(self, interaction: discord.Interaction):
        hours = [int(v) for v in self.select.values]
        await self.proceed_to_next(interaction, hours)

    async def skip_callback(self, interaction: discord.Interaction):
        await self.proceed_to_next(interaction, list(range(24)))


async def setup(bot):
    pass
