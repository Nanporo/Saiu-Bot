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

OWNER_GUILDS = [discord.Object(id=OWNER_SERVER_ID)] if OWNER_SERVER_ID else []

class GuildsView(discord.ui.View):
    def __init__(self, pages):
        super().__init__(timeout=300)
        self.pages = pages
        self.current_page = 0
        self.update_buttons()

    def update_buttons(self):
        # 若在第一頁則禁用上一頁，在最後一頁則禁用下一頁
        self.children[0].disabled = self.current_page == 0
        self.children[2].disabled = self.current_page == len(self.pages) - 1
        # 更新頁碼指示器
        self.children[1].label = f"第 {self.current_page + 1} / {len(self.pages)} 頁"

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.primary, row=0)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="第 1 / 3 頁", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.primary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)


class GuildsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="伺服器列表", description="（限擁有者）顯示機器人加入的伺服器列表與狀態")
    @app_commands.guilds(*OWNER_GUILDS)
    async def guilds_command(self, interaction: discord.Interaction):
        # 權限檢查
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        guilds = self.bot.guilds
        
        # 讀取設定檔統計活躍狀態
        try:
            guild_settings = get_all_settings()
        except Exception:
            guild_settings = {}
            
        total_members = sum(g.member_count for g in guilds)
        active_broadcast_count = sum(1 for g in guilds if str(g.id) in guild_settings and guild_settings[str(g.id)].get("auto_push", False))
        active_rain_count = sum(1 for g in guilds if str(g.id) in guild_settings and ("rain_alert" in guild_settings[str(g.id)] or "rain_alerts" in guild_settings[str(g.id)]))
        active_temp_count = sum(1 for g in guilds if str(g.id) in guild_settings and "temp_alerts" in guild_settings[str(g.id)])
        active_eq_count = sum(1 for g in guilds if str(g.id) in guild_settings and "eq_alerts" in guild_settings[str(g.id)])
        active_typhoon_count = sum(1 for g in guilds if str(g.id) in guild_settings and ("typhoon_alerts" in guild_settings[str(g.id)] or "typhoon_alert" in guild_settings[str(g.id)]))
        active_suspension_count = sum(1 for g in guilds if str(g.id) in guild_settings and ("suspension_alerts" in guild_settings[str(g.id)] or "suspension_alert" in guild_settings[str(g.id)]))

        # 第一頁：機器人狀態與統計
        embed_stats = discord.Embed(
            title="機器人狀態與統計",
            description=f"`{len(guilds)}` 群組數\n`{total_members}` 面向使用者數\n`{active_broadcast_count}` 個伺服器接收廣播\n`{active_rain_count}` 個伺服器開啟降雨預警\n`{active_temp_count}` 個伺服器開啟氣溫預警\n`{active_eq_count}` 個伺服器開啟地震通知\n`{active_typhoon_count}` 個伺服器開啟颱風機率\n`{active_suspension_count}` 個伺服器開啟停班課通知",
            color=0x41809b
        )

        display_count = 10

        # 第二頁：人數最多的前 10 個伺服器
        sorted_by_members = sorted(guilds, key=lambda g: g.member_count, reverse=True)
        embed_top_members = discord.Embed(
            title="前 10 大伺服器 (依人數)",
            color=0x2ecc71
        )
        for i, guild in enumerate(sorted_by_members[:display_count]):
            g_settings = guild_settings.get(str(guild.id), {})
            marks = ""
            if g_settings.get("auto_push", False): marks += "📢 "
            if "rain_alert" in g_settings or "rain_alerts" in g_settings: marks += "🌧️"
            if "temp_alerts" in g_settings: marks += "🌡️"
            if "eq_alerts" in g_settings: marks += "🏚️"
            if "typhoon_alerts" in g_settings or "typhoon_alert" in g_settings: marks += "🌀"
            if "suspension_alerts" in g_settings or "suspension_alert" in g_settings: marks += "🎒"
                
            embed_top_members.add_field(
                name=f"{i+1} : {guild.name} {marks}".strip(),
                value=f"ID: `{guild.id}`\n人數: {guild.member_count} 人",
                inline=False
            )

        # 第三頁：最新加入的 10 個伺服器
        sorted_by_joined = sorted(guilds, key=lambda g: g.me.joined_at if g.me and g.me.joined_at else discord.utils.utcnow(), reverse=True)
        embed_latest_joined = discord.Embed(
            title="最新加入的 10 個伺服器",
            color=0xf39c12
        )
        for i, guild in enumerate(sorted_by_joined[:display_count]):
            g_settings = guild_settings.get(str(guild.id), {})
            marks = ""
            if g_settings.get("auto_push", False): marks += "📢 "
            if "rain_alert" in g_settings or "rain_alerts" in g_settings: marks += "🌧️"
            if "temp_alerts" in g_settings: marks += "🌡️"
            if "eq_alerts" in g_settings: marks += "🏚️"
            if "typhoon_alerts" in g_settings or "typhoon_alert" in g_settings: marks += "🌀"
            if "suspension_alerts" in g_settings or "suspension_alert" in g_settings: marks += "🎒"
            
            joined_time = guild.me.joined_at.strftime('%Y-%m-%d %H:%M') if guild.me and guild.me.joined_at else "未知"
            embed_latest_joined.add_field(
                name=f"{i+1} : {guild.name} {marks}".strip(),
                value=f"ID: `{guild.id}`\n人數: {guild.member_count} 人\n加入時間: {joined_time}",
                inline=False
            )

        pages = [embed_stats, embed_top_members, embed_latest_joined]
        view = GuildsView(pages)
        message_content = "🤖 機器人伺服器狀態"
        await interaction.followup.send(content=message_content, embed=pages[0], view=view)

async def setup(bot):
    await bot.add_cog(GuildsCog(bot))