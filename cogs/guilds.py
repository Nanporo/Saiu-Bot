import discord
from discord.ext import commands
from discord import app_commands
import json
from modules.database import get_all_settings
from modules.ownercheck import is_owner

try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    OWNER_SERVER_ID = int(config.get('OWNER_SERVER_ID', 0))
except Exception:
    OWNER_SERVER_ID = 0

class GuildsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="伺服器列表", description="（限擁有者）顯示機器人加入的伺服器列表與狀態")
    @app_commands.guilds(OWNER_SERVER_ID)
    async def guilds_command(self, interaction: discord.Interaction):
        # 權限檢查
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guilds = self.bot.guilds
        # 依照伺服器人數由大到小排序
        sorted_guilds = sorted(guilds, key=lambda g: g.member_count, reverse=True)
        total_members = sum(g.member_count for g in guilds)
        
        # 讀取設定檔統計活躍狀態
        try:
            guild_settings = get_all_settings()
        except Exception:
            guild_settings = {}
            
        active_broadcast_count = sum(1 for g in guilds if str(g.id) in guild_settings and guild_settings[str(g.id)].get("auto_push", False))
        active_rain_count = sum(1 for g in guilds if str(g.id) in guild_settings and ("rain_alert" in guild_settings[str(g.id)] or "rain_alerts" in guild_settings[str(g.id)]))
        active_temp_count = sum(1 for g in guilds if str(g.id) in guild_settings and "temp_alerts" in guild_settings[str(g.id)])
        active_eq_count = sum(1 for g in guilds if str(g.id) in guild_settings and "eq_alerts" in guild_settings[str(g.id)])
        active_typhoon_count = sum(1 for g in guilds if str(g.id) in guild_settings and ("typhoon_alerts" in guild_settings[str(g.id)] or "typhoon_alert" in guild_settings[str(g.id)]))
        active_suspension_count = sum(1 for g in guilds if str(g.id) in guild_settings and ("suspension_alerts" in guild_settings[str(g.id)] or "suspension_alert" in guild_settings[str(g.id)]))

        message_content = "🤖 機器人伺服器狀態"

        embed = discord.Embed(
            title="",
            description=f"`{len(guilds)}` 群組數\n`{total_members}` 面向使用者數\n`{active_broadcast_count}` 個伺服器接收廣播\n`{active_rain_count}` 個伺服器開啟降雨預警\n`{active_temp_count}` 個伺服器開啟氣溫預警\n`{active_eq_count}` 個伺服器開啟地震通知\n`{active_typhoon_count}` 個伺服器開啟颱風機率\n`{active_suspension_count}` 個伺服器開啟停班課通知",
            color=0x41809b
        )

        # 避免超過 Embed 上限，僅顯示前 10 大伺服器
        display_count = 10
        for i, guild in enumerate(sorted_guilds[:display_count]):
            g_settings = guild_settings.get(str(guild.id), {})
            marks = ""
            if g_settings.get("auto_push", False):
                marks += "📢 "
            if "rain_alert" in g_settings or "rain_alerts" in g_settings:
                marks += "🌧️"
            if "temp_alerts" in g_settings:
                marks += "🌡️"
            if "eq_alerts" in g_settings:
                marks += "🏚️"
            if "typhoon_alerts" in g_settings or "typhoon_alert" in g_settings:
                marks += "🌀"
            if "suspension_alerts" in g_settings or "suspension_alert" in g_settings:
                marks += "🎒"
                
            embed.add_field(
                name=f"{i+1} : {guild.name} {marks}".strip(),
                value=f"ID: `{guild.id}`\n人數: {guild.member_count} 人",
                inline=False
            )

        embed.set_footer(text=f"隱藏了其他 {max(0, len(guilds) - display_count)} 個伺服器..." if len(guilds) > display_count else "已列出所有伺服器。")
        await interaction.followup.send(content=message_content, embed=embed)

async def setup(bot):
    await bot.add_cog(GuildsCog(bot))