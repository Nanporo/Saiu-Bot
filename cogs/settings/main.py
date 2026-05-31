import discord
from cogs.settings.utils import load_settings
from cogs.settings.broadcast_view import BroadcastSettingsView
from cogs.settings.rain_view import RainAlertSettingsView
from cogs.settings.temp_view import TempAlertSettingsView
from cogs.settings.eq_view import EqAlertSettingsView
from cogs.settings.typhoon_view import TyphoonAlertSettingsView

class SettingsView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {"auto_push": False, "target_channel_ids": []})

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="`⚙️` 伺服器設定", description="請從下方選單選擇要調整的項目。\n(點擊選單進入設定)", color=0x41809b)
        
        auto_push_status = "`🟢`已啟用" if self.settings.get("auto_push") else "`🔴`已停用"
        rain_status = "`🟢`已啟用" if ('rain_alerts' in self.settings or 'rain_alert' in self.settings) else "`🔴`已停用"
        temp_status = "`🟢`已啟用" if 'temp_alerts' in self.settings else "`🔴`已停用"
        eq_status = "`🟢`已啟用" if 'eq_alerts' in self.settings else "`🔴`已停用"
        typhoon_status = "`🟢`已啟用" if ('typhoon_alerts' in self.settings or 'typhoon_alert' in self.settings) else "`🔴`已停用"
        
        embed.add_field(name="📢 系統廣播", value=f"{auto_push_status}", inline=True)
        embed.add_field(name="🌧️ 降雨預警", value=f"{rain_status}", inline=True)
        embed.add_field(name="🌡️ 氣溫預警", value=f"{temp_status}", inline=True)
        embed.add_field(name="🏚️ 地震通知", value=f"{eq_status}", inline=True)
        embed.add_field(name="🌀 颱風侵襲機率", value=f"{typhoon_status}", inline=True)
        return embed

    @discord.ui.select(
        placeholder="請選擇要設定的項目",
        max_values=1,
        options=[
            discord.SelectOption(label="系統廣播設定", value="broadcast", description="設定接收擁有者廣播的頻道", emoji="📢"),
            discord.SelectOption(label="降雨預警設定", value="rain", description="管理降雨預警的發送頻道與狀態", emoji="🌧️"),
            discord.SelectOption(label="氣溫預警設定", value="temp", description="管理氣溫預警的發送頻道與狀態", emoji="🌡️"),
            discord.SelectOption(label="地震通知設定", value="eq", description="管理地震通知的發送頻道與狀態", emoji="🏚️"),
            discord.SelectOption(label="颱風侵襲機率設定", value="typhoon", description="管理颱風侵襲機率通知的發送頻道與狀態", emoji="🌀")
        ],
        row=0
    )
    async def select_category(self, interaction: discord.Interaction, select: discord.ui.Select):
        views = {"broadcast": BroadcastSettingsView, "rain": RainAlertSettingsView, "temp": TempAlertSettingsView, "eq": EqAlertSettingsView, "typhoon": TyphoonAlertSettingsView}
        view = views[select.values[0]](self.guild_id)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="完成", style=discord.ButtonStyle.success, row=1)
    async def close_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ **設定面板已關閉**", view=None)
        self.stop()
        
async def setup(bot):
    pass