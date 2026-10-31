"""降雨門檻與大雷雨加入流程。"""

import discord
from modules.database import get_all_settings, save_all_settings
from cogs.add.add_roles import RoleSetupView
from cogs.add.add_hours import NotifyHoursSetupView


class ThunderstormToggleView(discord.ui.View):
    """在設定降雨預警後，詢問是否開啟大雷雨即時訊息"""
    def __init__(self, guild_id: str, alert_type: str):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.alert_type = alert_type

    @discord.ui.button(label="開啟大雷雨即時訊息", style=discord.ButtonStyle.success, emoji="⛈️")
    async def enable_thunderstorm(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = get_all_settings()
        if self.guild_id not in settings:
            settings[self.guild_id] = {}
        settings[self.guild_id]['thunderstorm_alert'] = True
        save_all_settings(settings)

        msg = interaction.message.content + "\n\n✅ 已開啟 **大雷雨即時訊息**！當氣象署發布大雷雨即時訊息且影響區域包含您設定的地點時，將自動通知。"
        view = RoleSetupView(self.alert_type)
        msg += "\n\n💡 **是否要設定標記身分組？**\n如果您希望在預警時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："
        await interaction.response.edit_message(content=msg, view=view)

    @discord.ui.button(label="不需要", style=discord.ButtonStyle.secondary)
    async def skip_thunderstorm(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg = interaction.message.content
        view = RoleSetupView(self.alert_type)
        msg += "\n\n💡 **是否要設定標記身分組？**\n如果您希望在預警時自動標記特定身分組，請在下方選單設定 (若不需要可點選留空)："
        await interaction.response.edit_message(content=msg, view=view)


class MinRainfallSetupView(discord.ui.View):
    """在設定降雨預警後，引導用戶選擇最低預警雨量門檻"""
    def __init__(self, guild_id: str, loc_val: str):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.loc_val = loc_val

        options = [
            discord.SelectOption(label="1.0 mm (預設，微量/小雨即通知)", value="1.0", emoji="💧"),
            discord.SelectOption(label="5.0 mm (微幅降雨通知)", value="5.0", emoji="💧"),
            discord.SelectOption(label="10.0 mm (小雨/中雨通知)", value="10.0", emoji="🌧️"),
            discord.SelectOption(label="20.0 mm (累積強降雨通知)", value="20.0", emoji="🌧️"),
            discord.SelectOption(label="40.0 mm (大雨等級通知)", value="40.0", emoji="🟡"),
            discord.SelectOption(label="100.0 mm (豪雨等級通知)", value="100.0", emoji="🟠"),
            discord.SelectOption(label="200.0 mm (大豪雨等級通知)", value="200.0", emoji="🔴"),
            discord.SelectOption(label="350.0 mm (超大豪雨等級通知)", value="350.0", emoji="🟣"),
        ]
        self.select = discord.ui.Select(placeholder="請選擇最低預警雨量門檻...", options=options, min_values=1, max_values=1, row=0)
        self.select.callback = self.select_callback
        self.add_item(self.select)

        skip_btn = discord.ui.Button(label="使用預設門檻 (1.0 mm)", style=discord.ButtonStyle.secondary, row=1)
        skip_btn.callback = self.skip_callback
        self.add_item(skip_btn)

    async def proceed_to_next(self, interaction: discord.Interaction, min_rain: float):
        settings = get_all_settings()
        if self.guild_id in settings and 'rain_alerts' in settings[self.guild_id]:
            if self.loc_val in settings[self.guild_id]['rain_alerts']:
                if isinstance(settings[self.guild_id]['rain_alerts'][self.loc_val], dict):
                    settings[self.guild_id]['rain_alerts'][self.loc_val]['min_rainfall'] = min_rain
                    save_all_settings(settings)

        msg = interaction.message.content + f"\n\n✅ 最低預警雨量門檻已設定為 **{min_rain} mm**！"
        msg += "\n\n⏰ **請選擇允許通知的時段：**\n請在下方選單勾選允許發送預警的時段（預設為 24 小時全時段）："
        view = NotifyHoursSetupView(self.guild_id, "rain", self.loc_val)
        await interaction.response.edit_message(content=msg, view=view)

    async def select_callback(self, interaction: discord.Interaction):
        val = float(self.select.values[0])
        await self.proceed_to_next(interaction, val)

    async def skip_callback(self, interaction: discord.Interaction):
        await self.proceed_to_next(interaction, 1.0)


async def setup(bot):
    pass
