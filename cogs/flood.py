import discord
from discord.ext import commands
from discord import app_commands
from modules.location_matcher import match_location

class FloodManualCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="淹水查詢", description="💧 查詢指定地區目前的積淹水深度")
    @app_commands.describe(location="請輸入縣市與鄉鎮市區（例如：花蓮縣卓溪鄉）")
    async def query_flood(self, interaction: discord.Interaction, location: str):
        loc_val, error_msg = match_location(location)
        if error_msg:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        flood_cog = self.bot.get_cog("FloodForecastCog")
        if not flood_cog:
            await interaction.response.send_message("❌ 淹水預警模組尚未載入，無法查詢。", ephemeral=True)
            return

        await interaction.response.defer()

        # 如果背景尚未抓取資料，先手動觸發抓取
        if not flood_cog.latest_flood_data:
            await flood_cog.fetch_all_stations()

        if not flood_cog.latest_flood_data:
            await interaction.followup.send("❌ 無法取得水利署淹水感測器資料，請稍後再試。", ephemeral=True)
            return

        found, max_depth, max_station_name = flood_cog.get_max_depth(loc_val, flood_cog.latest_flood_data)

        if not found:
            await interaction.followup.send(f"ℹ️ **{loc_val}** 查無淹水感測器，或資料暫時無法獲取。")
            return

        icon = "💧"
        color = discord.Color.green()
        
        if max_depth >= 50.0:
            icon = "🔴"
            color = discord.Color.red()
        elif max_depth >= 30.0:
            icon = "🟠"
            color = discord.Color.orange()
        elif max_depth >= 10.0:
            icon = "🟡"
            color = discord.Color.gold()
        elif max_depth >= 2.0:
            icon = "💧"
            color = discord.Color.blue()

        if max_depth >= 2.0:
            content = "🌊 積淹水查詢結果"
            embed = discord.Embed(
                title="",
                description=f"**{loc_val}** 目前有積淹水情況！\n最深測站：{max_station_name}\n淹水深度：`{icon} {max_depth} cm`",
                color=color
            )
        else:
            content = "✅ 積淹水查詢結果"
            embed = discord.Embed(
                title="",
                description=f"**{loc_val}** 目前測站顯示**無積淹水**。\n淹水深度：`{icon} 0.0 cm`",
                color=color
            )
        
        embed.set_footer(text="資料來源 • 經濟部水利署")
        await interaction.followup.send(content=content, embed=embed)

    @query_flood.autocomplete("location")
    async def query_flood_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        from modules.location_matcher import get_town_autocomplete
        choices = get_town_autocomplete(current)
        return [app_commands.Choice(name=c, value=c) for c in choices]

async def setup(bot):
    await bot.add_cog(FloodManualCog(bot))
