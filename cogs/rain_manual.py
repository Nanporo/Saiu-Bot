import discord
from discord.ext import commands
from discord import app_commands

# 這個模組是手動查詢未來1小時該地區是否有降雨，自動的是 cogs/alarm/rain_forecast.py

class RainManualCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="降雨預警", description="手動查詢指定地點未來 1 小時內的降雨預測")
    @app_commands.describe(location="請輸入縣市與鄉鎮市區（例如：臺北市信義區）")
    async def query_rain(self, interaction: discord.Interaction, location: str):
        await interaction.response.defer()

        # 呼叫 RainForecastCog 中共用的邏輯來節省維護成本
        rain_cog = self.bot.get_cog("RainForecastCog")
        if not rain_cog:
            await interaction.followup.send("❌ 降雨預報模組尚未載入，無法查詢。")
            return

        grid_data, msg_or_loc = await rain_cog.get_location_grid(location)
        if not grid_data:
            await interaction.followup.send(msg_or_loc)
            return

        grid_x, grid_y = grid_data
        location_name = msg_or_loc

        rain_val, err = await rain_cog.fetch_rain_value(grid_x, grid_y)
        if err:
            await interaction.followup.send(f"❌ 查詢失敗：{err}")
            return

        icon = "💧"
        if rain_val >= 350.0:
            icon = "🟣"
        elif rain_val >= 200.0:
            icon = "🔴"
        elif rain_val >= 100.0:
            icon = "🟠"
        elif rain_val >= 40.0:
            icon = "🟡"

        # 預設為 0.5mm
        if rain_val >= 0.5:
            if rain_val >= 10.0:
                feels_like = "大雨"
            elif rain_val >= 2.5:
                feels_like = "中雨"
            elif rain_val > 0.5:
                feels_like = "小雨"
            else:
                feels_like = "毛毛雨"

            content = "🌧️ 降雨預報查詢"
            embed = discord.Embed(
                title="",
                description=f"**{location_name}** 未來 1 小時內預測將有**降雨**發生！\n預估累積雨量：`{icon} {rain_val} mm ({feels_like})`",
                color=discord.Color.blue()
            )
        else:
            content = "🌤️ 降雨預報查詢"
            embed = discord.Embed(
                title="",
                description=f"**{location_name}** 未來 1 小時內預測**無顯著降雨**！\n預估累積雨量：`{icon} 0.0 mm`",
                color=discord.Color.green()
            )
        
        await interaction.followup.send(content=content, embed=embed)

async def setup(bot):
    await bot.add_cog(RainManualCog(bot))