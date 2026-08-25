import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import sys
from datetime import datetime, timezone, timedelta

# 引入在 cogs.typhoon 撰寫的共用邏輯
from cogs.typhoon import fetch_typhoon_data, get_typhoon_probabilities, fetch_typhoon_warning, TAIWAN_CITIES
from modules.database import get_all_settings
from modules.cache_manager import load_cache
import logging

logger = logging.getLogger(__name__)

COUNTY_ORDER = {
    '基隆市': 1, '臺北市': 2, '台北市': 2, '新北市': 3, '桃園市': 4, 
    '新竹縣': 5, '新竹市': 6, '苗栗縣': 7, '臺中市': 8, '台中市': 8,
    '彰化縣': 9, '南投縣': 10, '雲林縣': 11, '嘉義縣': 12, '嘉義市': 13, 
    '臺南市': 14, '台南市': 14, '高雄市': 15, '屏東縣': 16, 
    '宜蘭縣': 17, '花蓮縣': 18, '臺東縣': 19, '台東縣': 19, 
    '澎湖縣': 20, '金門縣': 21, '連江縣': 22, '馬祖': 22
}

class TyphoonAlarmCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cache = load_cache()
        self.last_prob_time = cache.get("typhoon_prob")
        self.last_warn_time = cache.get("typhoon_warn")
        self.warned_status = cache.get("typhoon_warned_status", {})
        self.prob_notified = cache.get("typhoon_prob_notified", {})
        self.typhoon_alarm_task.start()

    def save_state(self):
        return {
            "typhoon_prob": self.last_prob_time,
            "typhoon_warn": self.last_warn_time,
            "typhoon_warned_status": self.warned_status,
            "typhoon_prob_notified": self.prob_notified
        }
        
    def cog_unload(self):
        self.typhoon_alarm_task.cancel()
        

    # 每 2 小時檢查一次是否有更新
    @tasks.loop(hours=2)
    async def typhoon_alarm_task(self):
        try:
            settings = get_all_settings()
        except Exception:
            return

        has_alerts = any('typhoon_alerts' in d and d['typhoon_alerts'] for d in settings.values())
        status_cog = self.bot.get_cog("Status")

        if not has_alerts:
            if status_cog and hasattr(status_cog, "update_typhoon_alert"):
                status_cog.update_typhoon_alert(None)
            return
            
        polygons, valid_time = await fetch_typhoon_data(self.bot.session)
        warning_data = await fetch_typhoon_warning(self.bot.session)

        if status_cog and hasattr(status_cog, "update_typhoon_alert"):
            if warning_data:
                headline = warning_data.get("headline", "")
                ty_text = "海上陸上颱風警報發布中" if "陸上" in headline else "海上颱風警報發布中"
                status_cog.update_typhoon_alert(ty_text)
            else:
                status_cog.update_typhoon_alert(None)
        
        warn_time = warning_data['effective'] if warning_data else None
        
        prob_updated = (valid_time and valid_time != getattr(self, 'last_prob_time', None))
        warn_updated = (warn_time and warn_time != getattr(self, 'last_warn_time', None))
        
        if prob_updated: self.last_prob_time = valid_time
        if warn_updated: self.last_warn_time = warn_time
        
        results = get_typhoon_probabilities(polygons) if polygons else []
        
        sent_cnt = 0
        for guild_id, d in settings.items():
            global_silent = d.get('global_silent', False)
            typhoon_alerts = d.get('typhoon_alerts', {})
            if not isinstance(typhoon_alerts, dict):
                continue
            for loc_name, alert_info in typhoon_alerts.items():
                if isinstance(alert_info, dict):
                    channel_id = alert_info.get('channel_id')
                elif isinstance(alert_info, (int, str)) and not isinstance(alert_info, bool) and str(alert_info).isdigit():
                    channel_id = alert_info
                else:
                    continue
                if not channel_id:
                    continue
                try:
                    channel_id = int(channel_id)
                except (ValueError, TypeError):
                    continue
                channel = self.bot.get_channel(channel_id)
                if not channel: continue
                
                status_key = f"{guild_id}_{channel_id}_{loc_name}"
                is_warned = warning_data and (loc_name == "全台接收" or loc_name in warning_data['areas'])
                was_warned = self.warned_status.get(status_key, False)
                
                if is_warned and not was_warned:
                    self.warned_status[status_key] = True
                    warn_time_str = warning_data['effective']
                    try:
                        try:
                            dt = datetime.fromisoformat(warn_time_str)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                        except ValueError:
                            dt = datetime.strptime(warn_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))
                        warn_time_display = f"<t:{int(dt.timestamp())}:f>"
                    except ValueError:
                        warn_time_display = warn_time_str

                    desc_str = f"目前已發布颱風警報，請密切注意颱風動向！\n發佈時間：{warn_time_display}" if loc_name == "全台接收" else f"**{loc_name}** 已發布颱風警報！\n發佈時間：{warn_time_display}"
                    embed = discord.Embed(
                        title=f"{warning_data['headline']}",
                        description=desc_str,
                        color=0xff3846
                    )
                    sorted_areas = sorted(warning_data['areas'], key=lambda x: (0, COUNTY_ORDER[x]) if x in COUNTY_ORDER else (1, x))
                    areas_str = "、".join(sorted_areas) or "全台 (請參考警報內容)"
                    embed.add_field(name="警戒區域", value=areas_str, inline=False)
                    
                    content = "🌀 颱風通知"
                    mention_role_id = d.get('typhoon_mention_role_id')
                    if mention_role_id:
                        content += f" <@&{mention_role_id}>"
                    if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                        logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                    else:
                        self.bot.loop.create_task(channel.send(content=content, embed=embed, silent=global_silent))
                        sent_cnt += 1
                    guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                    logger.debug(f"📢 [颱風通知] 已發送颱風警報至 {guild_name} ({channel.name}) - {loc_name}")
                    continue
                    
                if not is_warned and was_warned:
                    self.warned_status[status_key] = False
                    desc_str = "目前颱風警報已解除或已脫離警戒範圍。" if loc_name == "全台接收" else f"**{loc_name}** 已脫離颱風警戒範圍或警報已解除。"
                    embed = discord.Embed(
                        title="解除颱風警報",
                        description=desc_str,
                        color=0x2ecc71
                    )
                    if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                        logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                    else:
                        self.bot.loop.create_task(channel.send(content="🌀 颱風通知", embed=embed, silent=global_silent))
                        sent_cnt += 1
                    guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                    logger.debug(f"📢 [颱風通知] 已發送解除警報至 {guild_name} ({channel.name}) - {loc_name}")
                    continue
                    
                if is_warned and was_warned:
                    # 目前有警報且已發布過，不發通知以免打擾
                    continue
                    
                if prob_updated:
                    if loc_name == "全台接收":
                        continue
                    loc_prob = next((r['prob'] for r in results if r['county'] == loc_name), 0)
                    threshold = int(alert_info.get('threshold', 70)) if isinstance(alert_info, dict) else 70
                    if loc_prob >= threshold:
                        last_notified = self.prob_notified.get(status_key, 0)
                        current_time = datetime.now().timestamp()
                        if current_time - last_notified >= 18 * 3600:
                            self.prob_notified[status_key] = current_time
                            
                            if loc_prob >= 75: icon = "🔴"
                            elif loc_prob >= 50: icon = "🟠"
                            elif loc_prob >= 25: icon = "🟡"
                            elif loc_prob > 0: icon = "⚪"
                            else: icon = "⚪"
                            
                            content = "🌀 颱風通知"
                            mention_role_id = d.get('typhoon_mention_role_id')
                            if mention_role_id:
                                content += f" <@&{mention_role_id}>"
                            embed = discord.Embed(
                                title="", 
                                description=f"**{loc_name}** 的暴風圈侵襲機率已達 `{icon} {loc_prob}%` 以上！\n請關注颱風消息並提早做好防颱準備。", 
                                color=discord.Color.red()
                            )
                            if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                                logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                            else:
                                self.bot.loop.create_task(channel.send(content=content, embed=embed, silent=global_silent))
                                sent_cnt += 1
                            guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                            logger.debug(f"📢 [颱風通知] 已發送侵襲機率至 {guild_name} ({channel.name}) - {loc_name} (機率 {loc_prob}%)")

        if sent_cnt > 0:
            logger.info(f"📢 [颱風通知] 廣播完成 | 共發送 {sent_cnt} 個頻道")

    @typhoon_alarm_task.before_loop
    async def before_task(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(TyphoonAlarmCog(bot))