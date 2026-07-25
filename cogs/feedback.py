import discord
from discord.ext import commands
from discord import app_commands

class ReportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="GitHub 專案",
            url="https://github.com/Nanporo/Saiu-Bot",
            style=discord.ButtonStyle.link
        ))
        self.add_item(discord.ui.Button(
            label="小裁雨回報表單",
            url="https://forms.gle/6NvfWjYmus7tz9GZ6",
            style=discord.ButtonStyle.link
        ))
        self.add_item(discord.ui.Button(
            label="強震即時警報許可申請",
            url="https://forms.gle/Q63jK9gSNpbJHaZz7",
            style=discord.ButtonStyle.link
        ))

class ReportCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="問題回報", description="💭 問題回報、表單填寫與 EEW 許可申請 Feedback")
    async def report_command(self, interaction: discord.Interaction):
        content = "💭 問題回報"
        desc = "如果您遇到任何問題，可以填寫表單回報，或是在 GitHub 上提出 Issue。"
        embed = discord.Embed(description=desc, color=0x3498db)
        view = ReportView()
        await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(ReportCog(bot))
