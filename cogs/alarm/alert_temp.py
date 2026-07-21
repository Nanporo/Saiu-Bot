import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
from datetime import datetime, timezone, timedelta
from modules.cwa_api import fetch_current_temperatures
from modules.database import get_all_settings
from modules.cache_manager import load_cache
import logging

logger = logging.getLogger(__name__)

class TempAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cache = load_cache()
        self.alert_status = cache.get("temp_status", {})  # 紀錄伺服器某地區當日是否已發送過預警
        self.check_temp_loop.start()

    def save_state(self):
        return {"temp_status": self.alert_status}

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
            settings = get_all_settings()
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
            global_silent = d.get('global_silent', False)
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

                    current_high_level = int(self.alert_status.get(status_key_high, 0))
                    high_level = 0
                    if max_temp >= 38.0: high_level = 3
                    elif max_temp >= 36.0: high_level = 2
                    elif max_temp >= 33.0: high_level = 1

                    if high_level > current_high_level:
                        icon = "🔴" if max_temp >= 38.0 else "🟠" if max_temp >= 36.0 else "🟡"
                        content = "🌡️ 高溫持續通知" if current_high_level > 0 else "🌡️ 高溫預警通知"
                        mention_role_id = d.get('temp_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"
                        embed = discord.Embed(title="", description=f"**{loc_name}** 當前最高氣溫：`{icon} {max_temp} °C`\n請注意防曬並多補充水分。", color=discord.Color.red())
                        if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                            logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                        else:
                            await channel.send(content=content, embed=embed, silent=global_silent)
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.info(f"📢 [氣溫預警] 已發送 {content} 至 {guild_name} ({channel.name}) - {loc_name}")
                        self.alert_status[status_key_high] = high_level

                    current_low_level = int(self.alert_status.get(status_key_low, 0))
                    low_level = 0
                    if min_temp <= 6.0: low_level = 3
                    elif min_temp <= 9.0: low_level = 2
                    elif min_temp <= 12.0: low_level = 1

                    if low_level > current_low_level:
                        icon = "🟣" if min_temp <= 6.0 else "🔵" if min_temp <= 12.0 else "🟢"
                        content = "❄️ 低溫持續通知" if current_low_level > 0 else "❄️ 低溫預警通知"
                        mention_role_id = d.get('temp_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"
                        embed = discord.Embed(title="", description=f"**{loc_name}** 當前最低氣溫：`{icon} {min_temp} °C`\n請注意保暖，慎防寒害。", color=discord.Color.blue())
                        if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                            logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                        else:
                            await channel.send(content=content, embed=embed, silent=global_silent)
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.info(f"📢 [氣溫預警] 已發送 {content} 至 {guild_name} ({channel.name}) - {loc_name}")
                        self.alert_status[status_key_low] = low_level

    @check_temp_loop.before_loop
    async def before_check_temp(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(TempAlertCog(bot))