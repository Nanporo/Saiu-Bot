import discord
from discord.ext import commands
from discord import app_commands
from cogs.settings.main import SettingsView

class SettingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="設定", description="（限管理員）調整伺服器的設定與廣播頻道")
    @app_commands.default_permissions(administrator=True) # 限管理員可用
    async def settings_command(self, interaction: discord.Interaction):
        # 確認指令是在伺服器內使用
        if not interaction.guild:
            await interaction.response.send_message("❌ 此指令只能在伺服器當中使用。", ephemeral=True)
            return
            
        # 初始化 View 與 Embed
        view = SettingsView(interaction.guild.id)
        embed = view.build_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(SettingsCog(bot))