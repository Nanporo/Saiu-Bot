import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import json
import re
from datetime import datetime, timedelta, timezone
from modules.database import get_all_settings
from modules.cache_manager import load_cache
import logging
from cogs.list_eq import build_eq_embed, get_eq_color, format_intensity
import math
from modules.location_matcher import town_mapping_cache

def haversine_dist(lat1, lon1, lat2, lon2):
    R = 6371.0 # 地球半徑(公里)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

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
        
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            for ds in datasets:
                url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{ds}?Authorization={api_key}&limit=10&format=JSON"
                try:
                    async with session.get(url, ssl=False) as response:
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
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, ssl=False) as response:
                    if response.status != 200:
                        return
                    data = await response.json(content_type=None)
        except Exception as e:
            logger.warning(f"⚠️ [地震通知] 抓取資料失敗: {type(e).__name__} {e}")
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

        # 檢查地震發生時間
        origin_time_str = eq.get("EarthquakeInfo", {}).get("OriginTime")
        if origin_time_str:
            try:
                origin_time = datetime.fromisoformat(origin_time_str)
                now = datetime.now(origin_time.tzinfo)
                if (now - origin_time) > timedelta(days=7):
                    logger.info(f"⚠️ [地震通知] 忽略超過 7 天的地震報告 (發生時間: {origin_time_str}, 標識符: {identifier})")
                    return
            except Exception as e:
                logger.warning(f"⚠️ [地震通知] 解析地震時間失敗: {type(e).__name__} {e}")

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
                match = re.search(r'(\d+)(強|弱)?', str(intensity_str))
                if match:
                    base_val = float(match.group(1))
                    if match.group(2) == "強":
                        val = base_val + 0.5
                    else:
                        val = base_val
                    fullname = f"{county_name}{town_name}"
                    eq_intensities[fullname] = max(eq_intensities.get(fullname, 0.0), val)

        # 檢查各伺服器的通知條件
        for guild_id, d in settings.items():
            global_silent = d.get('global_silent', False)
            for loc_name, alert_info in d.get('eq_alerts', {}).items():
                # 兼容舊版可能損壞的資料格式 (僅存有 int 頻道的狀況)
                if isinstance(alert_info, dict):
                    min_mag = alert_info.get('min_magnitude', 5.5)
                    min_int = alert_info.get('min_intensity', 3)
                    channel_id = alert_info.get('channel_id')
                    detailed_format = alert_info.get('detailed_format', False)
                else:
                    min_mag, min_int, channel_id = 5.5, 3, alert_info
                    detailed_format = False

                if loc_name == "全台接收":
                    if mag < min_mag:
                        continue
                        
                    max_eq_int = max(eq_intensities.values()) if eq_intensities else 0
                    if max_eq_int < min_int:
                        continue

                    channel = self.bot.get_channel(int(channel_id))
                    if channel:
                        adapted_eq = dict(eq)
                        if "EarthquakeInfo" in adapted_eq and "Magnitude" in adapted_eq:
                            adapted_eq["EarthquakeInfo"] = dict(adapted_eq["EarthquakeInfo"])
                            adapted_eq["EarthquakeInfo"]["EarthquakeMagnitude"] = adapted_eq["Magnitude"]
                        if "Web" in adapted_eq and not adapted_eq.get("ReportImageURI"):
                            adapted_eq["ReportImageURI"] = adapted_eq.get("Web", "")
                            
                        try:
                            embed = build_eq_embed(adapted_eq)
                            embed.set_footer(text="中央氣象署", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
                        except Exception as e:
                            logger.error(f"構建全台接收 embed 失敗: {e}")
                            continue

                        content = "🏚️ 地震通知"
                        mention_role_id = d.get('eq_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"
                        self.bot.loop.create_task(channel.send(content=content, embed=embed, silent=global_silent))
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.info(f"📢 [地震通知] 已發送預警至 {guild_name} ({channel.name}) - 全台接收")
                    continue

                if mag < min_mag:
                    continue
                    
                loc_intensity = 0.0
                nearest_msg = ""
                if loc_name in eq_intensities:
                    loc_intensity = eq_intensities[loc_name]
                else:
                    # 嘗試利用經緯度尋找最近的地震測站鄉鎮
                    matches = town_mapping_cache.get(loc_name, [])
                    target_lat = target_lon = None
                    for m in matches:
                        if m[0] == loc_name and m[1] is not None and m[2] is not None:
                            target_lat, target_lon = m[1], m[2]
                            break
                            
                    if target_lat and target_lon:
                        min_dist = float('inf')
                        nearest_town = None
                        nearest_intensity = 0.0
                        
                        for eq_loc, eq_int in eq_intensities.items():
                            eq_matches = town_mapping_cache.get(eq_loc, [])
                            for m in eq_matches:
                                if m[0] == eq_loc and m[1] is not None and m[2] is not None:
                                    s_lat, s_lon = m[1], m[2]
                                    dist = haversine_dist(target_lat, target_lon, s_lat, s_lon)
                                    # 加上 20 公里的合理範圍限制
                                    if dist < min_dist and dist <= 20.0:
                                        min_dist = dist
                                        nearest_town = eq_loc
                                        nearest_intensity = eq_int
                                    break
                                    
                        if nearest_town:
                            loc_intensity = nearest_intensity
                            nearest_msg = f" (鄰近地區：{nearest_town})"
                
                if loc_intensity >= min_int:
                    channel = self.bot.get_channel(int(channel_id))
                    if channel:
                        content = "🏚️ 地震通知"
                        mention_role_id = d.get('eq_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"
                        
                        if detailed_format:
                            adapted_eq = dict(eq)
                            if "EarthquakeInfo" in adapted_eq and "Magnitude" in adapted_eq:
                                adapted_eq["EarthquakeInfo"] = dict(adapted_eq["EarthquakeInfo"])
                                adapted_eq["EarthquakeInfo"]["EarthquakeMagnitude"] = adapted_eq["Magnitude"]
                            if "Web" in adapted_eq and not adapted_eq.get("ReportImageURI"):
                                adapted_eq["ReportImageURI"] = adapted_eq.get("Web", "")
                            
                            try:
                                embed = build_eq_embed(adapted_eq)
                                embed.set_footer(text="中央氣象署", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
                            except Exception as e:
                                logger.error(f"構建詳細格式 embed 失敗: {e}")
                                # 若失敗則退回簡易格式
                                embed_color = get_eq_color(mag, loc_intensity)
                                embed = discord.Embed(
                                    title="", 
                                    description=f"剛才發生了規模{mag}的地震。\n**{loc_name}**{nearest_msg} 震度{format_intensity(loc_intensity)}級。", 
                                    color=embed_color
                                )
                        else:
                            embed_color = get_eq_color(mag, loc_intensity)
                            embed = discord.Embed(
                                title="", 
                                description=f"剛才發生了規模{mag}的地震。\n**{loc_name}**{nearest_msg} 震度{format_intensity(loc_intensity)}級。", 
                                color=embed_color
                            )
                            
                        self.bot.loop.create_task(channel.send(content=content, embed=embed, silent=global_silent))
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.info(f"📢 [地震通知] 已發送預警至 {guild_name} ({channel.name}) - {loc_name}{nearest_msg}")

    @check_eq_loop.before_loop
    async def before_check_eq(self):
        await self.bot.wait_until_ready()
        api_key = self.get_api_key()
        if not api_key: return
            
        url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/E-A0015-005?Authorization={api_key}&downloadType=WEB&format=JSON"
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, ssl=False) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        identifier = data.get("cwaopendata", {}).get("identifier")
                        if identifier:
                            self.processed_eqs.add(identifier)
        except Exception:
            pass

async def setup(bot):
    await bot.add_cog(EarthquakeAlertCog(bot))