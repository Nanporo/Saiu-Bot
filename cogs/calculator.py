import discord
from discord.ext import commands
from discord import app_commands
import logging

logger = logging.getLogger(__name__)

class CalculatorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    calc_group = app_commands.Group(name="換算", description="氣象單位換算器")

    @calc_group.command(name="風速", description="風速單位換算器 (m/s, kts, km/h, 風級)")
    @app_commands.describe(value="要換算的數值", unit="該數值的單位")
    @app_commands.choices(unit=[
        app_commands.Choice(name="m/s (公尺/秒)", value="ms"),
        app_commands.Choice(name="km/h (公里/小時)", value="kmh"),
        app_commands.Choice(name="kts (節)", value="kts"),
        app_commands.Choice(name="蒲氏風級", value="bft")
    ])
    async def convert_wind(self, interaction: discord.Interaction, value: float, unit: app_commands.Choice[str]):
        if value < 0:
            await interaction.response.send_message("⚠️ 風速不能為負數！請輸入大於或等於 0 的數值。", ephemeral=True)
            return

        ms = 0.0
        if unit.value == "ms":
            ms = value
        elif unit.value == "kts":
            ms = value * 0.514444
        elif unit.value == "kmh":
            ms = value / 3.6
        elif unit.value == "bft":
            ms = 0.836 * (value ** 1.5)

        kts = ms / 0.514444
        kmh = ms * 3.6
        bft = (ms / 0.836) ** (2/3) if ms > 0 else 0

        description = (
            f"公尺每秒　 `{ms:.2f} m/s`\n"
            f"公里每小時 `{kmh:.2f} km/h`\n"
            f"節　　　　 `{kts:.2f} kts`\n"
            f"蒲氏風級　 `{bft:.0f} 級`"
        )
        embed = discord.Embed(description=description, color=0x41809b)
        
        await interaction.response.send_message(content="🌬️ 風速換算結果", embed=embed)

    @calc_group.command(name="溫度", description="溫度單位換算器 (Celsius, Fahrenheit, Kelvin)")
    @app_commands.describe(value="要換算的數值", unit="該數值的單位")
    @app_commands.choices(unit=[
        app_commands.Choice(name="°C (攝氏)", value="c"),
        app_commands.Choice(name="°F (華氏)", value="f"),
        app_commands.Choice(name="K (絕對溫度)", value="k")
    ])
    async def convert_temp(self, interaction: discord.Interaction, value: float, unit: app_commands.Choice[str]):
        # 檢查是否低於絕對零度
        if unit.value == "c" and value < -273.15:
            await interaction.response.send_message("⚠️ 溫度不能低於絕對零度 (-273.15 °C)！", ephemeral=True)
            return
        elif unit.value == "f" and value < -459.67:
            await interaction.response.send_message("⚠️ 溫度不能低於絕對零度 (-459.67 °F)！", ephemeral=True)
            return
        elif unit.value == "k" and value < 0:
            await interaction.response.send_message("⚠️ 溫度不能低於絕對零度 (0 K)！", ephemeral=True)
            return

        c = 0.0
        if unit.value == "c":
            c = value
        elif unit.value == "f":
            c = (value - 32) * 5/9
        elif unit.value == "k":
            c = value - 273.15

        f = c * 9/5 + 32
        k = c + 273.15

        description = (
            f"攝氏　　 `{c:.2f}°C`\n"
            f"華氏　　 `{f:.2f}°F`\n"
            f"絕對溫度 `{k:.2f}K`"
        )
        embed = discord.Embed(description=description, color=0x41809b)
        
        await interaction.response.send_message(content="🌡️ 溫度換算結果", embed=embed)

    @calc_group.command(name="氣壓", description="氣壓單位換算器 (hPa, inHg, mmHg, atm)")
    @app_commands.describe(value="要換算的數值", unit="該數值的單位")
    @app_commands.choices(unit=[
        app_commands.Choice(name="hPa (百帕)", value="hpa"),
        app_commands.Choice(name="inHg (英吋汞柱)", value="inhg"),
        app_commands.Choice(name="mmHg (毫米汞柱)", value="mmhg"),
        app_commands.Choice(name="atm (標準大氣壓)", value="atm")
    ])
    async def convert_pressure(self, interaction: discord.Interaction, value: float, unit: app_commands.Choice[str]):
        if value < 0:
            await interaction.response.send_message("⚠️ 氣壓不能為負數！請輸入大於或等於 0 的數值。", ephemeral=True)
            return

        hpa = 0.0
        if unit.value == "hpa":
            hpa = value
        elif unit.value == "inhg":
            hpa = value * 33.8639
        elif unit.value == "mmhg":
            hpa = value * 1.33322
        elif unit.value == "atm":
            hpa = value * 1013.25

        inhg = hpa / 33.8639
        mmhg = hpa / 1.33322
        atm = hpa / 1013.25

        description = (
            f"百帕　　　 `{hpa:.2f} hPa`\n"
            f"英吋汞柱　 `{inhg:.2f} inHg`\n"
            f"毫米汞柱　 `{mmhg:.2f} mmHg`\n"
            f"標準大氣壓 `{atm:.4f} atm`"
        )
        embed = discord.Embed(description=description, color=0x41809b)
        
        await interaction.response.send_message(content="📉 氣壓換算結果", embed=embed)

async def setup(bot):
    await bot.add_cog(CalculatorCog(bot))
