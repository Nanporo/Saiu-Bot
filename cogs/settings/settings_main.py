import discord
from cogs.settings.settings_utils import load_settings
from cogs.settings.settings_bot import BotSettingsView
from cogs.settings.settings_rain import RainAlertSettingsView
from cogs.settings.settings_temp import TempAlertSettingsView
from cogs.settings.settings_eq import EqAlertSettingsView
from cogs.settings.settings_typhoon import TyphoonAlertSettingsView
from cogs.settings.settings_suspension import SuspensionAlertSettingsView
from cogs.settings.settings_cbs import CBSAlertSettingsView
from cogs.settings.settings_flood import FloodAlertSettingsView
from cogs.settings.settings_eew import EewAlertSettingsView
from cogs.settings.settings_aqi import AqiAlertSettingsView
from cogs.settings.settings_traffic import TrafficAlertSettingsView

class SettingsView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = str(guild_id)
        self.all_settings = load_settings()
        self.settings = self.all_settings.setdefault(self.guild_id, {"auto_push": False, "target_channel_ids": []})

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="`⚙️` 伺服器設定", description="請從下方選單選擇要調整的項目。", color=0x41809b)
        
        bot_status = "`🟢` 已開啟" if self.settings.get("auto_push") or self.settings.get("allow_all_users_settings") or self.settings.get("allow_all_users_join") else "`🔴` 未設定"
        rain_status = "`🟢` 已啟用" if ('rain_alerts' in self.settings or 'rain_alert' in self.settings) else "`🔴` 已停用"
        flood_status = "`🟢` 已啟用" if 'flood_alerts' in self.settings else "`🔴` 已停用"
        temp_status = "`🟢` 已啟用" if 'temp_alerts' in self.settings else "`🔴` 已停用"
        eq_status = "`🟢` 已啟用" if 'eq_alerts' in self.settings else "`🔴` 已停用"
        typhoon_status = "`🟢` 已啟用" if ('typhoon_alerts' in self.settings or 'typhoon_alert' in self.settings) else "`🔴` 已停用"
        suspension_status = "`🟢` 已啟用" if ('suspension_alerts' in self.settings or 'suspension_alert' in self.settings) else "`🔴` 已停用"
        cbs_status = "`🟢` 已啟用" if self.settings.get("cbs_alerts") else "`🔴` 已停用"
        eew_status = "`🟢` 已啟用" if 'eew_alerts' in self.settings else "`🔴` 已停用"
        if not self.settings.get("eew_authorized", False):
            eew_status = "`🚫` 未許可"
        aqi_status = "`🟢` 已啟用" if 'aqi_alerts' in self.settings else "`🔴` 已停用"
        traffic_status = "`🟢` 已啟用" if 'traffic_alerts' in self.settings else "`🔴` 已停用"
        
        embed.add_field(name="🤖 機器人設定　", value=f"{bot_status}", inline=True)
        embed.add_field(name="⚠️ 災防告警　　", value=f"{cbs_status}", inline=True)
        embed.add_field(name="🚨 強震即時警報", value=f"{eew_status}", inline=True)
        embed.add_field(name="🌧️ 降雨預警　　", value=f"{rain_status}", inline=True)
        embed.add_field(name="💧 淹水預警　　", value=f"{flood_status}", inline=True)
        embed.add_field(name="🌡️ 氣溫預警　　", value=f"{temp_status}", inline=True)
        embed.add_field(name="🏚️ 地震通知　　", value=f"{eq_status}", inline=True)
        embed.add_field(name="🌀 颱風侵襲機率", value=f"{typhoon_status}", inline=True)
        embed.add_field(name="🎒 停班停課通知", value=f"{suspension_status}", inline=True)
        embed.add_field(name="😷 空氣品質預警", value=f"{aqi_status}", inline=True)
        embed.add_field(name="🚄 交通狀況通知", value=f"{traffic_status}", inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        return embed

    @discord.ui.select(
        placeholder="請選擇要設定的項目",
        max_values=1,
        options=[
            discord.SelectOption(label="機器人設定", value="bot", description="指令權限、AI提及對話、系統廣播", emoji="🤖"),
            discord.SelectOption(label="災防告警設定", value="cbs", description="", emoji="⚠️"),
            discord.SelectOption(label="強震即時警報", value="eew", description="", emoji="🚨"),
            discord.SelectOption(label="降雨預警設定", value="rain", description="", emoji="🌧️"),
            discord.SelectOption(label="淹水預警設定", value="flood", description="", emoji="💧"),
            discord.SelectOption(label="氣溫預警設定", value="temp", description="", emoji="🌡️"),
            discord.SelectOption(label="地震通知設定", value="eq", description="", emoji="🏚️"),
            discord.SelectOption(label="颱風侵襲機率設定", value="typhoon", description="", emoji="🌀"),
            discord.SelectOption(label="停班停課通知設定", value="suspension", description="", emoji="🎒"),
            discord.SelectOption(label="空氣品質預警設定", value="aqi", description="", emoji="😷"),
            discord.SelectOption(label="交通狀況通知設定", value="traffic", description="", emoji="🚄")
        ],
        row=0
    )
    async def select_category(self, interaction: discord.Interaction, select: discord.ui.Select):
        if select.values[0] == "bot" and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 只有伺服器管理員才能進入「機器人設定」！", ephemeral=True)
            return
            
        if select.values[0] == "eew" and not self.settings.get("eew_authorized", False):
            await interaction.response.send_message("❌ 此伺服器尚未獲得強震即時警報許可，無法進入設定。\n請先填寫表單申請許可：https://forms.gle/Q63jK9gSNpbJHaZz7", ephemeral=True)
            return
            
        views = {"bot": BotSettingsView, "rain": RainAlertSettingsView, "temp": TempAlertSettingsView, "eq": EqAlertSettingsView, "typhoon": TyphoonAlertSettingsView, "suspension": SuspensionAlertSettingsView, "cbs": CBSAlertSettingsView, "flood": FloodAlertSettingsView, "eew": EewAlertSettingsView, "aqi": AqiAlertSettingsView, "traffic": TrafficAlertSettingsView}
        view = views[select.values[0]](self.guild_id)
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    @discord.ui.button(label="關閉", emoji="❌", style=discord.ButtonStyle.secondary, row=1)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        
        try:
            await interaction.message.delete()
        except discord.NotFound:
            try:
                await interaction.delete_original_response()
            except Exception:
                pass
        self.stop()
        
async def setup(bot):
    pass