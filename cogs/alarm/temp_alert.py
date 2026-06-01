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

                    # 兼容可能損壞的資料格式
                    ch_id = alert_info.get('channel_id') if isinstance(alert_info, dict) else alert_info
                    if not isinstance(ch_id, int): continue

                    channel = self.bot.get_channel(ch_id)
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