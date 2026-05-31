import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
from datetime import datetime, timezone, timedelta
from modules.cwa_api import fetch_current_temperatures

class TempAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.alert_status = {}  # 紀錄伺服器某地區當日是否已發送過預警
        self.check_temp_loop.start()

    def get_api_key(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f).get('CWA_API_KEY', '')
        except Exception:
            return ''

    def cog_unload(self):
        self.check_temp_loop.cancel()

    @app_commands.command(name="加入氣溫預警", description="在此頻道設定本地鄉鎮市區，當高於33度或低於12度時通知")
    @app_commands.describe(location="請輸入縣市與鄉鎮市區（例如：臺北市信義區）")
    @app_commands.default_permissions(manage_guild=True)
    async def set_temp_alert(self, interaction: discord.Interaction, location: str):
        await interaction.response.defer(ephemeral=True)

        # 統一處理「台」與「臺」
        location = location.replace("台", "臺")

        if "縣" not in location and "市" not in location:
            await interaction.followup.send("❌ 為了精準定位，請提供包含「縣市」與「鄉鎮市區」的完整名稱（例如：臺北市信義區）。")
            return

        settings_path = 'guild_settings.json'
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception:
            settings = {}

        guild_id = str(interaction.guild_id)
        if guild_id not in settings:
            settings[guild_id] = {}

        if 'temp_alerts' not in settings[guild_id]:
            settings[guild_id]['temp_alerts'] = {}
            
        if len(settings[guild_id]['temp_alerts']) >= 10:
            await interaction.followup.send("❌ 每個伺服器最多只能設定 10 個氣溫預警地點。")
            return

        settings[guild_id]['temp_alerts'][location] = {'channel_id': interaction.channel_id}

        with open(settings_path, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)

        await interaction.followup.send(f"✅ 已成功將氣溫預警地點加入：**{location}**！\n未來當該地氣溫超過 33°C 或低於 12°C 時，每日將會自動通知此頻道一次。")

    @tasks.loop(minutes=15.0)
    async def check_temp_loop(self):
        api_key = self.get_api_key()
        if not api_key: return

        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except Exception: return

        # 若沒有伺服器設定氣溫預警，則不呼叫 API
        has_temp_alerts = any('temp_alerts' in d and d['temp_alerts'] for d in settings.values())
        if not has_temp_alerts: return

        # 取得全台鄉鎮市區氣溫資料
        town_temps = await fetch_current_temperatures(self.bot.session, api_key)
        if not town_temps: return

        # 當前日期字串 (用於每日只發送一次的紀錄重置)
        today_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

        # 清理舊日期的狀態，避免記憶體不斷增加
        for k in [k for k in self.alert_status if not k.endswith(today_str)]:
            del self.alert_status[k]

        for guild_id, d in settings.items():
            for loc_name, alert_info in d.get('temp_alerts', {}).items():
                if loc_name in town_temps:
                    max_temp = max(town_temps[loc_name])
                    min_temp = min(town_temps[loc_name])

                    status_key_high = f"{guild_id}_{loc_name}_high_{today_str}"
                    status_key_low = f"{guild_id}_{loc_name}_low_{today_str}"

                    channel = self.bot.get_channel(alert_info['channel_id'])
                    if not channel: continue

                    if max_temp >= 33.0 and not self.alert_status.get(status_key_high, False):
                        content = "🌡️ 高溫預警通知"
                        embed = discord.Embed(title="", description=f"**{loc_name}** 當前最高氣溫：`🔴 {max_temp} °C`\n請注意防曬並多補充水分。", color=discord.Color.red())
                        await channel.send(content=content, embed=embed)
                        self.alert_status[status_key_high] = True

                    if min_temp <= 12.0 and not self.alert_status.get(status_key_low, False):
                        content = "❄️ 低溫預警通知"
                        embed = discord.Embed(title="", description=f"**{loc_name}** 當前最低氣溫：`🔵 {min_temp} °C`\n請注意保暖，慎防寒害。", color=discord.Color.blue())
                        await channel.send(content=content, embed=embed)
                        self.alert_status[status_key_low] = True

    @check_temp_loop.before_loop
    async def before_check_temp(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(TempAlertCog(bot))