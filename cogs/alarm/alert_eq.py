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
        self.last_sig_status = None
        self.last_small_status = None
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

    # 保留此函式供 list_eq.py 呼叫最新地震列表使用
    async def fetch_earthquakes(self):
        api_key = self.get_api_key()
        if not api_key or not self.bot.session: return []

        eqs = []
        datasets = ["E-A0015-001", "E-A0016-001"]
        
        for ds in datasets:
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{ds}?limit=10&format=JSON"
            headers = {"Authorization": api_key}
            try:
                async with self.bot.session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        records = data.get("records", {}).get("Earthquake", [])
                        eqs.extend(records)
            except Exception:
                pass
        return eqs

    def _parse_rest_intensities(self, eq):
        """
        從 REST API 格式 (E-A0015-001 / E-A0016-001) 的 ShakingArea.EqStation 解析震度字典。
        鍵為「縣市名+測站名」，值為震度浮點數。
        因為測站名不等於鄉鎮名，此結果主要供 20km 匹配用。
        """
        eq_intensities = {}
        for area in eq.get("Intensity", {}).get("ShakingArea", []):
            if "最大震度" in area.get("AreaDesc", ""):
                continue
            county = area.get("CountyName", "")
            for station in area.get("EqStation", []):
                station_name = station.get("StationName", "")
                intensity_str = station.get("SeismicIntensity", "0級")
                match = re.search(r'(\d+)(強|弱)?', str(intensity_str))
                if match:
                    base_val = float(match.group(1))
                    val = base_val + 0.5 if match.group(2) == "強" else base_val
                    fullname = f"{county}{station_name}"
                    eq_intensities[fullname] = max(eq_intensities.get(fullname, 0.0), val)
        return eq_intensities

    async def _fetch_005_town_intensities(self, api_key, origin_time_str, mag_str):
        """
        向 E-A0015-005 (FileAPI) 取鄉鎮級震度資料。
        以 OriginTime 與規模字串完全相符確認是同一場地震。
        回傳「縣市名+鄉鎮名」→震度浮點數的字典，或 None（未取得）。
        """
        if not self.bot.session: return None
        url = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/E-A0015-005?downloadType=WEB&format=JSON"
        headers = {"Authorization": api_key}
        try:
            async with self.bot.session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                fileapi_eq = data.get("cwaopendata", {}).get("Earthquake", {})

                # 比對 OriginTime
                fileapi_time = fileapi_eq.get("OriginTime", "")
                if fileapi_time != origin_time_str:
                    return None

                # 比對規模（字串完全相符）
                fileapi_mag_str = str(fileapi_eq.get("Magnitude", {}).get("MagnitudeValue", ""))
                if fileapi_mag_str != mag_str:
                    return None

                # 解析鄉鎮級震度
                eq_intensities = {}
                for county in fileapi_eq.get("Intensity", {}).get("County", []):
                    county_name = county.get("CountyName", "")
                    for town in county.get("Town", []):
                        town_name = town.get("TownName", "")
                        intensity_str = town.get("StationIntensity", "0級")
                        match = re.search(r'(\d+)(強|弱)?', str(intensity_str))
                        if match:
                            base_val = float(match.group(1))
                            val = base_val + 0.5 if match.group(2) == "強" else base_val
                            fullname = f"{county_name}{town_name}"
                            eq_intensities[fullname] = max(eq_intensities.get(fullname, 0.0), val)
                return eq_intensities if eq_intensities else None
        except Exception:
            return None

    async def _process_and_notify(self, eq, eq_intensities, mag, settings):
        """根據地震資料與各伺服器設定發送通知"""
        # 解析震央位置簡稱（提取「位於...」括號內文字，例如「花蓮縣豐濱鄉」）
        epicenter = eq.get("EarthquakeInfo", {}).get("Epicenter", {}).get("Location", "")
        epi_match = re.search(r'位於(.*?)[）\)]', epicenter)
        loc_display = epi_match.group(1).strip() if epi_match else (epicenter[:15] if epicenter else "未知地點")

        status_cog = self.bot.get_cog("Status")
        if status_cog and hasattr(status_cog, "set_eq_report"):
            status_cog.set_eq_report(loc_display, mag)

        for guild_id, d in settings.items():
            global_silent = d.get('global_silent', False)
            for loc_name, alert_info in d.get('eq_alerts', {}).items():
                min_mag = alert_info.get('min_magnitude', 5.5)
                min_int = alert_info.get('min_intensity', 3)
                channel_id = alert_info.get('channel_id')
                detailed_format = alert_info.get('detailed_format', False)

                if loc_name == "全台接收":
                    if mag < min_mag:
                        continue
                    max_eq_int = max(eq_intensities.values()) if eq_intensities else 0
                    if max_eq_int < min_int:
                        continue
                    channel = self.bot.get_channel(int(channel_id))
                    if channel:
                        content = "🏚️ 地震通知"
                        mention_role_id = d.get('eq_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"
                        try:
                            embed = build_eq_embed(eq)
                            recv_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
                            embed.set_footer(text=f"中央氣象署 • 接收時間 {recv_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
                        except Exception as e:
                            logger.error(f"構建全台接收 embed 失敗: {e!r}")
                            continue
                        if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                            logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                        else:
                            self.bot.loop.create_task(channel.send(content=content, embed=embed, silent=global_silent))
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.info(f"📢 [地震通知] 已發送預警至 {guild_name} ({channel.name}) - 全台接收 (規模{mag})")
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
                            try:
                                embed = build_eq_embed(eq)
                                recv_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
                                embed.set_footer(text=f"中央氣象署 • 接收時間 {recv_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
                            except Exception as e:
                                logger.error(f"構建詳細格式 embed 失敗: {e!r}")
                                # 若失敗則退回簡易格式
                                embed_color = get_eq_color(mag, loc_intensity)
                                display_int = format_intensity(loc_intensity)
                                suffix = "" if "弱" in display_int or "強" in display_int else "級"
                                embed = discord.Embed(
                                    title="",
                                    description=f"剛才發生了規模{mag}的地震。\n**{loc_name}**{nearest_msg} 震度{display_int}{suffix}。",
                                    color=embed_color
                                )
                        else:
                            embed_color = get_eq_color(mag, loc_intensity)
                if mag < min_mag: continue

                max_int = self._calc_location_max_intensity(loc_name, eq_intensities)
                if max_int < min_int: continue

                channel = self.bot.get_channel(int(channel_id))
                if not channel: continue

                try:
                    role_id = alert_info.get('ping_role_id')
                    ping_str = f"<@&{role_id}> " if role_id and not global_silent else ""

                    if detailed_format:
                        embed = self._build_detailed_embed(eq, loc_name, max_int)
                        await channel.send(content=f"{ping_str}地震報告", embed=embed)
                    else:
                        embed = self._build_simple_embed(eq, loc_name, max_int)
                        await channel.send(content=f"{ping_str}地震預警", embed=embed)

                except Exception as e:
                    logger.error(f"❌ [地震通知] 傳送至頻道 {channel_id} 失敗: {e}")

    @tasks.loop(seconds=15.0)
    async def check_eq_loop(self):
        if self.bot.is_abnormal_grace_period():
            return
            
        api_key = self.get_api_key()
        if not api_key or not self.bot.session: return

        try:
            settings = get_all_settings()
        except Exception:
            return

        has_alerts = any('eq_alerts' in d and d['eq_alerts'] for d in settings.values())
        if not has_alerts: return

        # ===== 顯著有感地震：E-A0015-001 主要偵測 =====
        sig_url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001?limit=3&format=JSON"
        headers = {"Authorization": api_key}
        try:
            async with self.bot.session.get(sig_url, headers=headers) as response:
                if response.status == 200:
                    if self.last_sig_status not in (None, 200):
                        logger.info("✅ [地震通知] 顯著有感地震 API 已恢復正常連線 (狀態碼: 200)")
                    self.last_sig_status = 200
                    data = await response.json()
                    for eq in data.get("records", {}).get("Earthquake", []):
                        issue_time = eq.get("IssueTime", "")
                        sig_key = f"sig_{issue_time}"
                        if not issue_time or sig_key in self.processed_eqs:
                            continue

                        # 時效檢查
                        origin_time_str = eq.get("EarthquakeInfo", {}).get("OriginTime", "")
                        if origin_time_str:
                            try:
                                origin_time = datetime.fromisoformat(origin_time_str)
                                now = datetime.now(origin_time.tzinfo)
                                if (now - origin_time) > timedelta(days=1):
                                    logger.info(f"⏭️ [地震通知] 略過超過 1 天的顯著地震報告 (OriginTime: {origin_time_str})")
                                    self.processed_eqs.add(sig_key)
                                    continue
                            except Exception:
                                pass

                        self.processed_eqs.add(sig_key)
                        if len(self.processed_eqs) > 200:
                            self.processed_eqs.pop()

                        mag_val = eq.get("EarthquakeInfo", {}).get("EarthquakeMagnitude", {}).get("MagnitudeValue", "0")
                        try:
                            mag = float(mag_val)
                        except (ValueError, TypeError):
                            mag = 0.0

                        # 嘗試從 E-A0015-005 取鄉鎮級精確震度（比對 OriginTime + 規模字串）
                        eq_intensities = await self._fetch_005_town_intensities(api_key, origin_time_str, mag_val)

                        if eq_intensities:
                            logger.info(f"✅ [地震通知] 已從 E-A0015-005 取得鄉鎮震度 (OriginTime: {origin_time_str})")
                        else:
                            # E-A0015-005 無對應資料，退回測站資料 + 20km 匹配
                            logger.info(f"ℹ️ [地震通知] E-A0015-005 無對應資料，改用測站資料+20km匹配 (OriginTime: {origin_time_str})")
                            eq_intensities = self._parse_rest_intensities(eq)

                        await self._process_and_notify(eq, eq_intensities, mag, settings)
                        # 只處理最新一筆
                        break
                else:
                    if self.last_sig_status != response.status:
                        logger.warning(f"🌐 [地震通知] 顯著有感地震: API 狀態碼: {response.status}")
                        self.last_sig_status = response.status
        except Exception as e:
            err_str = f"EXC_{type(e).__name__}"
            if self.last_sig_status != err_str:
                logger.warning(f"⚠️ [地震通知] 顯著有感地震檢查失敗: {type(e).__name__} {e!r}")
                self.last_sig_status = err_str

        # ===== 小區域地震：E-A0016-001（不查 E-A0015-005，直接用測站資料 + 20km 匹配）=====
        small_url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0016-001?limit=3&format=JSON"
        try:
            async with self.bot.session.get(small_url, headers=headers) as response:
                if response.status == 200:
                    if self.last_small_status not in (None, 200):
                        logger.info("✅ [地震通知] 小區域地震 API 已恢復正常連線 (狀態碼: 200)")
                    self.last_small_status = 200
                    data = await response.json()
                    for eq in data.get("records", {}).get("Earthquake", []):
                        issue_time = eq.get("IssueTime", "")
                        small_key = f"small_{issue_time}"
                        if not issue_time or small_key in self.processed_eqs:
                            continue

                        # 時效檢查
                        origin_time_str = eq.get("EarthquakeInfo", {}).get("OriginTime", "")
                        if origin_time_str:
                            try:
                                origin_time = datetime.fromisoformat(origin_time_str)
                                now = datetime.now(origin_time.tzinfo)
                                if (now - origin_time) > timedelta(days=1):
                                    logger.info(f"⏭️ [地震通知] 略過超過 1 天的小區域地震報告 (OriginTime: {origin_time_str})")
                                    self.processed_eqs.add(small_key)
                                    continue
                            except Exception:
                                pass

                        self.processed_eqs.add(small_key)
                        if len(self.processed_eqs) > 200:
                            self.processed_eqs.pop()

                        mag_val = eq.get("EarthquakeInfo", {}).get("EarthquakeMagnitude", {}).get("MagnitudeValue", "0")
                        try:
                            mag = float(mag_val)
                        except (ValueError, TypeError):
                            mag = 0.0

                        # 小區域地震直接使用測站資料 + 20km 匹配
                        eq_intensities = self._parse_rest_intensities(eq)
                        await self._process_and_notify(eq, eq_intensities, mag, settings)
                        # 只處理最新一筆
                        break
                else:
                    if self.last_small_status != response.status:
                        logger.warning(f"🌐 [地震通知] 小區域地震: API 狀態碼: {response.status}")
                        self.last_small_status = response.status
        except Exception as e:
            err_str = f"EXC_{type(e).__name__}"
            if self.last_small_status != err_str:
                logger.warning(f"⚠️ [地震通知] 小區域地震檢查失敗: {type(e).__name__} {e!r}")
                self.last_small_status = err_str

    @check_eq_loop.before_loop
    async def before_check_eq(self):
        await self.bot.wait_until_ready()
        api_key = self.get_api_key()
        if not api_key or not self.bot.session: return

        # 初始化：記錄當前最新顯著有感地震 IssueTime，避免啟動時重複通知
        sig_url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001?limit=1&format=JSON"
        headers = {"Authorization": api_key}
        try:
            async with self.bot.session.get(sig_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    eqs = data.get("records", {}).get("Earthquake", [])
                    if eqs:
                        issue_time = eqs[0].get("IssueTime", "")
                        if issue_time:
                            self.processed_eqs.add(f"sig_{issue_time}")
        except Exception:
            pass

        # 初始化：記錄當前最新小區域地震 IssueTime
        small_url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0016-001?limit=1&format=JSON"
        try:
            async with self.bot.session.get(small_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    eqs = data.get("records", {}).get("Earthquake", [])
                    if eqs:
                        issue_time = eqs[0].get("IssueTime", "")
                        if issue_time:
                            self.processed_eqs.add(f"small_{issue_time}")
        except Exception:
            pass

async def setup(bot):
    await bot.add_cog(EarthquakeAlertCog(bot))