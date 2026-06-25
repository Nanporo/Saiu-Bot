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

class TyphoonAlarmCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cache = load_cache()
        self.last_prob_time = cache.get("typhoon_prob")
        self.last_warn_time = cache.get("typhoon_warn")
        self.typhoon_alarm_task.start()

    def save_state(self):
        return {
            "typhoon_prob": self.last_prob_time,
            "typhoon_warn": self.last_warn_time
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

        has_alerts = any('typhoon_alerts' in d or 'typhoon_alert' in d for d in settings.values())
        if not has_alerts:
            return
            
        polygons, valid_time = await fetch_typhoon_data(self.bot.session)
        warning_data = await fetch_typhoon_warning(self.bot.session)
        
        warn_time = warning_data['effective'] if warning_data else None
        
        prob_updated = (valid_time and valid_time != getattr(self, 'last_prob_time', None))
        warn_updated = (warn_time and warn_time != getattr(self, 'last_warn_time', None))
        
        if not prob_updated and not warn_updated:
            return
            
        if prob_updated: self.last_prob_time = valid_time
        if warn_updated: self.last_warn_time = warn_time
        
        results = get_typhoon_probabilities(polygons) if polygons else []
        
        for guild_id, d in settings.items():
            global_silent = d.get('global_silent', False)
            alerts = d.get('typhoon_alerts', {})
            if 'typhoon_alert' in d:
                alerts[d['typhoon_alert'].get('location_name', '臺北市')] = {'channel_id': d['typhoon_alert']['channel_id']}
                
            for loc_name, alert_info in alerts.items():
                channel = self.bot.get_channel(int(alert_info['channel_id']))
                if not channel: continue
                
                # 判斷是否正處於警報影響區域
                is_warned = warning_data and loc_name in warning_data['areas']
                
                if is_warned and warn_updated:
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

                    embed = discord.Embed(
                        title=f"⚠️ {warning_data['headline']}",
                        description=f"**【颱風警報】**\n**{loc_name}** 已發布颱風警報！\n\n發布時間：{warn_time_display}",
                        color=0xff3846
                    )
                    areas_str = "、".join(warning_data['areas']) or "全台 (請參考警報內容)"
                    embed.add_field(name="警戒區域", value=areas_str, inline=False)
                    
                    desc = warning_data['description'] or "無詳細內容"
                    if len(desc) > 1024:
                        desc = desc[:1020] + "..."
                    embed.add_field(name="警報內容", value=desc, inline=False)
                    
                    self.bot.loop.create_task(channel.send(content="🌀 颱風通知", embed=embed, silent=global_silent))
                    guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                    logger.info(f"📢 [颱風通知] 已發送颱風警報至 {guild_name} ({channel.name}) - {loc_name}")
                    continue # 發布了警報，直接跳過後續的機率判斷
                    
                if is_warned:
                    # 目前有警報但無需更新，不發機率通知以免打擾
                    continue
                    
                if prob_updated:
                    loc_prob = next((r['prob'] for r in results if r['county'] == loc_name), 0)
                    if loc_prob >= 75:
                        content = "🌀 颱風通知"
                        embed = discord.Embed(
                            title="", 
                            description=f"**{loc_name}** 的暴風圈侵襲機率已達 `🔴 {loc_prob}%` 以上！\n請關注颱風消息並提早做好防颱準備。", 
                            color=discord.Color.red()
                        )
                        self.bot.loop.create_task(channel.send(content=content, embed=embed, silent=global_silent))
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.info(f"📢 [颱風通知] 已發送侵襲機率至 {guild_name} ({channel.name}) - {loc_name}")

    @typhoon_alarm_task.before_loop
    async def before_task(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(TyphoonAlarmCog(bot))