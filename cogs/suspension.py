import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta

# 引用警報模組的共用變數
from cogs.alarm.alert_suspension import COUNTIES

class SuspensionManualCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="停班停課", description="🎒 手動查詢人事行政總處的停班停課資訊 Suspension")
    @app_commands.describe(county="選擇要查詢的縣市 (未選擇則列出目前有停班課的縣市)")
    @app_commands.choices(county=[app_commands.Choice(name=c, value=c) for c in COUNTIES])
    async def suspension_query(self, interaction: discord.Interaction, county: app_commands.Choice[str] = None):
        await interaction.response.defer()
        
        # 呼叫 SuspensionAlertCog 的邏輯，節省爬蟲維護成本
        alert_cog = self.bot.get_cog("SuspensionAlertCog")
        if not alert_cog:
            await interaction.followup.send("❌ 停班停課警報模組尚未載入，無法查詢。")
            return
            
        data = await alert_cog.fetch_data()
        if data is None:
            await interaction.followup.send("❌ 目前無法取得人事行政總處的資料，請稍後再試。")
            return
            
        content = "🎒 停班停課資訊"
        embed = discord.Embed(title="", color=0x3498db)
        
        if county:
            c_name = county.value
            info = next((v for k, v in data.items() if c_name.replace("臺", "台") in k.replace("臺", "台")), None)
                    
            if info:
                is_normal = alert_cog.is_normal_status(info)
                embed.color = 0x2ecc71 if is_normal else 0xe74c3c
                embed.description = f"**{c_name}** 最新宣布：\n\n{info}"
            else:
                embed.description = f"❌ 找不到 **{c_name}** 的資料。"
        else:
            suspended = [(c, i) for c, i in data.items() if not alert_cog.is_normal_status(i)]
                    
            if suspended:
                embed.color = 0xe74c3c
                embed.description = "目前有發布**停班停課**或**特殊狀況**的縣市如下："
                for c, i in suspended:
                    display_info = i if len(i) < 200 else i[:197] + "..."
                    embed.add_field(name=c, value=display_info, inline=False)
            else:
                embed.color = 0x2ecc71
                embed.description = "✅ **目前全台各縣市皆為「照常上班、照常上課」。**"
                
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        embed.set_footer(text=f"行政院人事行政總處 • 查詢時間 {current_time}")
        await interaction.followup.send(content=content, embed=embed)

async def setup(bot):
    await bot.add_cog(SuspensionManualCog(bot))