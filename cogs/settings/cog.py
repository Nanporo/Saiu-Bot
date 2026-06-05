import discord
from discord.ext import commands
from discord import app_commands
from cogs.settings.main import SettingsView
import json

def load_guild_settings():
    try:
        with open('guild_settings.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

class SettingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="設定", description="調整伺服器的自動通知、機器人等相關設定")
    async def settings_command(self, interaction: discord.Interaction):
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
            
        # 初始化 View 與 Embed
        view = SettingsView(interaction.guild.id)
        embed = view.build_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(SettingsCog(bot))