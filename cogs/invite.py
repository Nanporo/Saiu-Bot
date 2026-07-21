import discord
from discord.ext import commands
from discord import app_commands

class InviteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="邀請", description="🔗 獲取邀請機器人的網址")
    async def invite_command(self, interaction: discord.Interaction):
        invite_url = "https://discord.com/oauth2/authorize?client_id=843026816226951168"
        await interaction.response.send_message(f"邀請我加入伺服器：\n{invite_url}")

async def setup(bot):
    await bot.add_cog(InviteCog(bot))
