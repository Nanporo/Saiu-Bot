import discord
from discord.ext import commands
from discord import app_commands
from modules.database import get_all_settings
from cogs.add.add_view import AlertSetupView

def load_guild_settings():
    return get_all_settings()

class SettingsJoinCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="加入", description="⚙️ 在此頻道設定各種自動預警與推播通知 Add")
    @app_commands.describe(alert_type="請選擇要設定的通知類型")
    @app_commands.choices(alert_type=[
        app_commands.Choice(name="⚠️ 災防告警", value="cbs"),
        app_commands.Choice(name="🚨 強震即時警報", value="eew"),
        app_commands.Choice(name="🌧️ 降雨預警", value="rain"),
        app_commands.Choice(name="🌡️ 氣溫預警", value="temp"),
        app_commands.Choice(name="💧 淹水預警", value="flood"),
        app_commands.Choice(name="🏚️ 地震報告通知", value="earthquake"),
        app_commands.Choice(name="🌀 颱風侵襲機率", value="typhoon"),
        app_commands.Choice(name="🎒 停班停課通知", value="suspension"),
        app_commands.Choice(name="😷 空氣品質預警", value="aqi"),
        app_commands.Choice(name="🚄 交通狀況通知", value="traffic")
    ])
    async def join_alert_command(self, interaction: discord.Interaction, alert_type: app_commands.Choice[str]):
        # 確認指令是在伺服器內使用
        if not interaction.guild:
            await interaction.response.send_message("❌ 此指令只能在伺服器當中使用。", ephemeral=True)
            return
            
        settings = load_guild_settings().get(str(interaction.guild.id), {})
        allow_all = settings.get("allow_all_users_join", False)
        
        # 檢查權限
        if not allow_all and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 只有伺服器管理員可以使用此指令，或者請管理員在「機器人設定」中開放權限。", ephemeral=True)
            return

        val = alert_type.value
        from modules.database import is_push_module_enabled
        from modules.module_manager import get_module_key_by_category

        mod_key = get_module_key_by_category(val)
        if mod_key and not is_push_module_enabled(mod_key):
            await interaction.response.send_message(f"❌ 「{alert_type.name}」功能目前已被機器人擁有者暫時關閉維護中，無法進行設定。", ephemeral=True)
            return

        if val == "eew" and not settings.get("eew_authorized", False):
            await interaction.response.send_message("❌ 本伺服器尚未獲得強震即時警報推播許可。\n您可以透過 `/問題回報` 指令的表單申請推播。", ephemeral=True)
            return

        view = AlertSetupView(val, interaction.user.id)
        
        content = f"⚙️ **設定 {alert_type.name}**\n請透過下方的介面完成通知設定："
        await interaction.response.send_message(content=content, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(SettingsJoinCog(bot))
