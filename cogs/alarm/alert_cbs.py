import discord
from discord.ext import commands, tasks
import aiohttp
import json
from datetime import datetime, timezone, timedelta
from modules.database import get_all_settings
import logging

logger = logging.getLogger(__name__)

class CBSAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.processed_ids = set()
        self.first_run_done = False
        self.check_cbs_loop.start()

    def cog_unload(self):
        self.check_cbs_loop.cancel()

    @tasks.loop(seconds=15.0)
    async def check_cbs_loop(self):
        try:
            settings = get_all_settings()
        except Exception as e:
            logger.error(f"Failed to load settings: {e}")
            return

        # 若沒有任何伺服器設定 CBS 預警，則不呼叫 API
        has_cbs_alerts = any('cbs_alerts' in d and d['cbs_alerts'] for d in settings.values())
        if not has_cbs_alerts:
            # 即使沒人設定，也要把 first_run_done 設為 True，避免之後有人設定時舊訊息被推播
            self.first_run_done = True
            return

        now = datetime.now(timezone(timedelta(hours=8)))
        yyyymm = now.strftime("%Y%m")
        url = f"https://cbs.tw/public/upload/files/json/{yyyymm}.json"
        
        try:
            async with self.bot.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return
                text = await resp.text()
                data = json.loads(text)
        except Exception as e:
            logger.error(f"Failed to fetch CBS JSON: {e}")
            return

        if not data.get("success"):
            return
            
        cbs_data = data.get("data", {})
        target_alerts = {"thunderstorm", "earthquakeew", "hurricfrcwnd", "flood", "roadclose", "debrisflow", "reservoirdis", "barrierlake", "airraidalert", "tsunami", "nuclear", "emergalert", "systemtest"}
        new_alerts = []
        
        for date_str, time_dict in cbs_data.items():
            for time_str, json_dict in time_dict.items():
                for json_id, alert_info in json_dict.items():
                    if json_id in self.processed_ids:
                        continue
                    
                    self.processed_ids.add(json_id)
                    
                    alert_type = alert_info.get("alertType")
                    if alert_type not in target_alerts:
                        continue
                        
                    new_alerts.append(alert_info)

        # 避免機器人剛啟動時把當月所有歷史告警都推播出去
        if not self.first_run_done:
            self.first_run_done = True
            return

        # 依照發布時間由舊到新排序，確保推播的順序符合時間軸
        new_alerts.sort(key=lambda x: x.get("release_time", ""))

        for alert in new_alerts:
            alert_type = alert.get("alertType")
            topic = alert.get("topic", "災防告警")
            area_text = alert.get("area_text", "")
            cmam_text = alert.get("CMAMtext", "")
            sender_name = alert.get("sender_name", "")
            release_time = alert.get("release_time", "")
            expires = alert.get("expires", "")
            
            emoji = "⚠️"
            if alert_type == "thunderstorm": emoji = "🌩️"
            elif alert_type == "earthquakeew": emoji = "🏚️"
            elif alert_type == "hurricfrcwnd": emoji = "🌀"
            elif alert_type == "flood": emoji = "🌊"
            elif alert_type == "roadclose": emoji = "⛔"
            elif alert_type == "debrisflow": emoji = "⛰️"
            elif alert_type == "reservoirdis": emoji = "🚰"
            elif alert_type == "barrierlake": emoji = "🏞️"
            elif alert_type == "airraidalert": emoji = "🚀"
            elif alert_type == "tsunami": emoji = "🌊"
            elif alert_type == "nuclear": emoji = "☢️"
            elif alert_type == "emergalert": emoji = "🚨"
            elif alert_type == "systemtest": emoji = "📯"
            
            embed = discord.Embed(
                title=f"{emoji} {topic}",
                color=0xfcd200
            )
            
            formatted_area = area_text.replace(",", "\n").replace("，", "\n")
            if formatted_area:
                embed.add_field(name="影響區域", value=formatted_area, inline=False)
                
            if cmam_text:
                embed.add_field(name="內文", value=f"```text\n{cmam_text}\n```", inline=False)
                
            if expires:
                expires_str = expires.replace("T", " ").replace("+08:00", "")
                embed.set_footer(text=f"發布單位 {sender_name}\n發布時間 {release_time}\n失效時間 {expires_str}")
            else:
                embed.set_footer(text=f"發布單位 {sender_name}\n發布時間 {release_time}")
            
            # 準備用於配對的合併字串，統一將「臺」替換為「台」
            combined_text = f"{area_text} {topic} {sender_name} {cmam_text}".replace("臺", "台")
            
            is_test = alert_type == "systemtest" or any(kw in topic or kw in cmam_text for kw in ["測試", "演練", "演習", "TEST", "test"])
            is_mountain = "山區暴雨" in topic or "山區" in cmam_text or "山區" in area_text
            
            for guild_id, d in settings.items():
                global_silent = d.get('global_silent', False)
                cbs_alerts = d.get('cbs_alerts', {})
                
                # 兼容舊版 list 格式
                if isinstance(cbs_alerts, list):
                    temp_dict = {}
                    if cbs_alerts:
                        temp_dict["全台接收"] = {"channel_id": cbs_alerts[0]}
                    cbs_alerts = temp_dict
                    
                for loc_name, alert_info in cbs_alerts.items():
                    # 匹配邏輯
                    is_match = False
                    if loc_name == "全台接收":
                        is_match = True
                    elif alert_type == "earthquakeew":
                        # 地震速報會明確給出縣市，直接比對
                        is_match = loc_name.replace("臺", "台") in area_text.replace("臺", "台")
                    else:
                        is_match = loc_name.replace("臺", "台") in combined_text
                        
                    if not is_match:
                        continue
                        
                    # 檢查進階過濾選項
                    receive_test = alert_info.get("receive_test", False) if isinstance(alert_info, dict) else False
                    receive_mountain = alert_info.get("receive_mountain", False) if isinstance(alert_info, dict) else False
                    allowed_types = alert_info.get("allowed_types", []) if isinstance(alert_info, dict) else []
                    
                    if is_test and not receive_test:
                        continue
                    if is_mountain and not receive_mountain:
                        continue
                    if allowed_types and alert_type not in allowed_types:
                        continue
                        
                    ch_id = alert_info.get("channel_id") if isinstance(alert_info, dict) else alert_info
                    channel = self.bot.get_channel(ch_id)
                    if not channel: continue
                    
                    try:
                        await channel.send(content="⚠️ **災防告警**", embed=embed, silent=global_silent)
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.info(f"📢 [CBS預警] 已發送至 {guild_name} ({channel.name}) - {topic} (配對: {loc_name})")
                    except Exception as e:
                        logger.error(f"Failed to send CBS alert to {ch_id}: {e}")

    @check_cbs_loop.before_loop
    async def before_check_cbs(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(CBSAlertCog(bot))
