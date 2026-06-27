import discord
from discord.ext import commands
from discord import app_commands
from cogs.settings.settings_main import SettingsView
import json
from modules.database import get_all_settings
from cogs.settings.settings_bot import BotSettingsView
from cogs.settings.settings_rain import RainAlertSettingsView
from cogs.settings.settings_temp import TempAlertSettingsView
from cogs.settings.settings_eq import EqAlertSettingsView
from cogs.settings.settings_typhoon import TyphoonAlertSettingsView
from cogs.settings.settings_suspension import SuspensionAlertSettingsView
from cogs.settings.settings_cbs import CBSAlertSettingsView

def load_guild_settings():
    return get_all_settings()

class SettingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="設定", description="⚙️ 調整伺服器的自動通知、機器人等相關設定")
    @app_commands.describe(category="可選擇要直接開啟的設定類別（選填）")
    @app_commands.choices(category=[
        app_commands.Choice(name="🤖 機器人設定", value="bot"),
        app_commands.Choice(name="🌧️ 降雨預警", value="rain"),
        app_commands.Choice(name="🌡️ 氣溫預警", value="temp"),
        app_commands.Choice(name="🏚️ 地震通知", value="eq"),
        app_commands.Choice(name="🌀 颱風侵襲機率", value="typhoon"),
        app_commands.Choice(name="🎒 停班停課通知", value="suspension"),
        app_commands.Choice(name="⚠️ 災防告警", value="cbs")
    ])
    async def settings_command(self, interaction: discord.Interaction, category: app_commands.Choice[str] = None):
        # 確認指令是在伺服器內使用
        if not interaction.guild:
            await interaction.response.send_message("❌ 此指令只能在伺服器當中使用。", ephemeral=True)
            return
            
        settings = load_guild_settings().get(str(interaction.guild.id), {})
        allow_all = settings.get("allow_all_users_settings", False)
        
        # 檢查權限
        if not allow_all and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 只有伺服器管理員可以使用此指令，或者請管理員在「機器人設定」中開放權限。", ephemeral=True)
            return
            
        guild_id = str(interaction.guild.id)
        if category:
            val = category.value
            views = {"bot": BotSettingsView, "rain": RainAlertSettingsView, "temp": TempAlertSettingsView, "eq": EqAlertSettingsView, "typhoon": TyphoonAlertSettingsView, "suspension": SuspensionAlertSettingsView, "cbs": CBSAlertSettingsView}
            view = views[val](guild_id)
            embed = view.build_embed()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            view = SettingsView(guild_id)
            embed = view.build_embed()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(SettingsCog(bot))