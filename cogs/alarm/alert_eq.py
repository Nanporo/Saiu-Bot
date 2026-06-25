import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import json
import re
from modules.database import get_all_settings
from modules.cache_manager import load_cache
import logging

logger = logging.getLogger(__name__)

class EarthquakeAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cache = load_cache()
        self.processed_eqs = set(cache.get('eq_processed', []))
        self.check_eq_loop.start()

    def save_state(self):
        return {"eq_processed": list(self.processed_eqs)}

    def get_api_key(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f).get('CWA_API_KEY', '')
        except Exception:
            return ''

    def cog_unload(self):
        self.check_eq_loop.cancel()

    # 保留此函式供 earthquake_list.py 呼叫最新地震列表使用
    async def fetch_earthquakes(self):
        api_key = self.get_api_key()
        if not api_key: return []

        eqs = []
        datasets = ["E-A0015-001", "E-A0016-001"]
        
        async with aiohttp.ClientSession() as session:
            for ds in datasets:
                url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{ds}?Authorization={api_key}&limit=10&format=JSON"
                try:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            records = data.get("records", {}).get("Earthquake", [])
                            eqs.extend(records)
                except Exception:
                    pass
        return eqs

    @tasks.loop(minutes=1.0)
    async def check_eq_loop(self):
        api_key = self.get_api_key()
        if not api_key: return

        try:
            settings = get_all_settings()
        except Exception: 
            return

        has_alerts = any('eq_alerts' in d and d['eq_alerts'] for d in settings.values())
        if not has_alerts: return

        url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/E-A0015-005?Authorization={api_key}&downloadType=WEB&format=JSON"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return
                    data = await response.json(content_type=None)
        except Exception as e:
            logger.warning(f"⚠️ [地震通知] 抓取資料失敗: {e}")
            return

        cwa = data.get("cwaopendata", {})
        identifier = cwa.get("identifier")
        
        if not identifier or identifier in self.processed_eqs:
            return
            
        self.processed_eqs.add(identifier)
        # 避免記憶體無限增長
        if len(self.processed_eqs) > 100:
            self.processed_eqs.pop()

        eq = cwa.get("Earthquake", {})
        mag_val = eq.get("Magnitude", {}).get("MagnitudeValue", "0")
        try: 
            mag = float(mag_val)
        except ValueError: 
            mag = 0.0

        # 解析各鄉鎮的震度
        eq_intensities = {}
        for c in eq.get("Intensity", {}).get("County", []):
            county_name = c.get("CountyName", "")
            for t in c.get("Town", []):
                town_name = t.get("TownName", "")
                intensity_str = t.get("StationIntensity", "0級")
                match = re.search(r'\d+', str(intensity_str))
                if match:
                    fullname = f"{county_name}{town_name}"
                    eq_intensities[fullname] = max(eq_intensities.get(fullname, 0), int(match.group()))

        # 檢查各伺服器的通知條件
        for guild_id, d in settings.items():
            global_silent = d.get('global_silent', False)
            for loc_name, alert_info in d.get('eq_alerts', {}).items():
                # 兼容舊版可能損壞的資料格式 (僅存有 int 頻道的狀況)
                if isinstance(alert_info, dict):
                    min_mag = alert_info.get('min_magnitude', 5.5)
                    min_int = alert_info.get('min_intensity', 3)
                    channel_id = alert_info.get('channel_id')
                else:
                    min_mag, min_int, channel_id = 5.5, 3, alert_info

                if mag < min_mag:
                    continue
                    
                loc_intensity = eq_intensities.get(loc_name, 0)
                
                if loc_intensity >= min_int:
                    channel = self.bot.get_channel(int(channel_id))
                    if channel:
                        content = "🏚️ 地震通知"
                        embed = discord.Embed(
                            title="", 
                            description=f"剛才發生了規模{mag}的地震。\n**{loc_name} **震度{loc_intensity}級。", 
                            color=0xff3846
                        )
                        self.bot.loop.create_task(channel.send(content=content, embed=embed, silent=global_silent))
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.info(f"📢 [地震通知] 已發送預警至 {guild_name} ({channel.name}) - {loc_name}")

    @check_eq_loop.before_loop
    async def before_check_eq(self):
        await self.bot.wait_until_ready()
        api_key = self.get_api_key()
        if not api_key: return
            
        url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/E-A0015-005?Authorization={api_key}&downloadType=WEB&format=JSON"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        identifier = data.get("cwaopendata", {}).get("identifier")
                        if identifier:
                            self.processed_eqs.add(identifier)
        except Exception:
            pass

async def setup(bot):
    await bot.add_cog(EarthquakeAlertCog(bot))