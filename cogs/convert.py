import discord
from discord.ext import commands
from discord import app_commands
import logging
import math

logger = logging.getLogger(__name__)

class CalculatorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="換算", description="🧮 氣象單位換算器 (風速、溫度、氣壓) Convert")
    @app_commands.describe(value="要換算的數值", unit="該數值的單位")
    @app_commands.choices(unit=[
        app_commands.Choice(name="💨 風速 m/s (公尺/秒)", value="ms"),
        app_commands.Choice(name="💨 風速 km/h (公里/小時)", value="kmh"),
        app_commands.Choice(name="💨 風速 kts (節)", value="kts"),
        app_commands.Choice(name="💨 風速 蒲氏風級", value="bft"),
        app_commands.Choice(name="🌡️ 溫度 °C (攝氏)", value="c"),
        app_commands.Choice(name="🌡️ 溫度 °F (華氏)", value="f"),
        app_commands.Choice(name="🌡️ 溫度 K (絕對溫度)", value="k"),
        app_commands.Choice(name="📉 氣壓 hPa (百帕)", value="hpa"),
        app_commands.Choice(name="📉 氣壓 inHg (英吋汞柱)", value="inhg"),
        app_commands.Choice(name="📉 氣壓 mmHg (毫米汞柱)", value="mmhg"),
        app_commands.Choice(name="📉 氣壓 atm (標準大氣壓)", value="atm")
    ])
    async def convert(self, interaction: discord.Interaction, value: float, unit: app_commands.Choice[str]):
        if math.isnan(value):
            await interaction.response.send_message("⚠️ 請輸入有效的數字！", ephemeral=True)
            return

        uv = unit.value
        
        if uv in ["ms", "kmh", "kts", "bft"]:
            if value < 0:
                await interaction.response.send_message("⚠️ 風速不能為負數！請輸入大於或等於 0 的數值。", ephemeral=True)
                return
            if uv == "bft" and value >= 18:
                await interaction.response.send_message("⚠️ 蒲氏風級最高只到 17 級！請輸入小於 18 的數值。", ephemeral=True)
                return
            ms = 0.0
            if uv == "ms": ms = value
            elif uv == "kts": ms = value * 0.514444
            elif uv == "kmh": ms = value / 3.6
            elif uv == "bft": ms = 0.836 * (value ** 1.5)

            if ms > 10000:
                await interaction.response.send_message("⚠️ 輸入的風速太誇張了！你家在海王星上面嗎？（海王星最高風速約為 600 m/s）", ephemeral=True)
                return

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
            await interaction.response.send_message(content="💨 風速換算結果", embed=embed)

        elif uv in ["c", "f", "k"]:
            if uv == "c" and value < -273.15:
                await interaction.response.send_message("⚠️ 溫度不能低於絕對零度 (-273.15 °C)！", ephemeral=True)
                return
            elif uv == "f" and value < -459.67:
                await interaction.response.send_message("⚠️ 溫度不能低於絕對零度 (-459.67 °F)！", ephemeral=True)
                return
            elif uv == "k" and value < 0:
                await interaction.response.send_message("⚠️ 溫度不能低於絕對零度 (0 K)，你的凍結威力太強了！", ephemeral=True)
                return

            c = 0.0
            if uv == "c": c = value
            elif uv == "f": c = (value - 32) * 5/9
            elif uv == "k": c = value - 273.15

            if c > 20000000:
                await interaction.response.send_message("⚠️ 輸入的溫度太高了！你正在太陽裡面核聚變嗎？（太陽核心溫度約一千五百萬度）", ephemeral=True)
                return

            f = c * 9/5 + 32
            k = c + 273.15

            description = (
                f"攝氏　　 `{c:.2f}°C`\n"
                f"華氏　　 `{f:.2f}°F`\n"
                f"絕對溫度 `{k:.2f}K`"
            )
            embed = discord.Embed(description=description, color=0x41809b)
            await interaction.response.send_message(content="🌡️ 溫度換算結果", embed=embed)

        elif uv in ["hpa", "inhg", "mmhg", "atm"]:
            if value < 0:
                await interaction.response.send_message("⚠️ 氣壓不能為負數！請輸入大於或等於 0 的數值。", ephemeral=True)
                return
            hpa = 0.0
            if uv == "hpa": hpa = value
            elif uv == "inhg": hpa = value * 33.8639
            elif uv == "mmhg": hpa = value * 1.33322
            elif uv == "atm": hpa = value * 1013.25

            if hpa > 100000000000:
                await interaction.response.send_message("⚠️ 輸入的氣壓太大了！你住在木星核心裡面嗎？（約一千億百帕）", ephemeral=True)
                return

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
