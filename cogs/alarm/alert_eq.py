import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from modules.database import get_all_settings, is_push_module_enabled
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
        self.last_major_quake = cache.get('last_major_quake', None)
        self.last_sig_status = None
        self.last_small_status = None
        self.background_tasks = set()
        self.check_eq_loop.start()

    def save_state(self):
        state = {"eq_processed": list(self.processed_eqs)}
        if hasattr(self, 'last_major_quake') and self.last_major_quake:
            state["last_major_quake"] = self.last_major_quake
        return state

    def get_api_key(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f).get('CWA_API_KEY', '')
        except Exception:
            return ''

    def cog_unload(self):
        self.check_eq_loop.cancel()
        for task in self.background_tasks:
            task.cancel()

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

    def _extract_eq_county(self, eq) -> str:
        """從地震報告中提取縣市名稱或海域名稱（例如：臺灣東部海域、花蓮、嘉義等）"""
        info = eq.get("EarthquakeInfo", {})
        epicenter_raw = str(info.get("Epicenter", {}).get("Location", "")).strip()
        
        county_candidates = [
            "花蓮", "嘉義", "宜蘭", "台東", "臺東", "台中", "臺中", "南投",
            "台南", "臺南", "高雄", "屏東", "彰化", "雲林", "苗栗", "新竹",
            "桃園", "新北", "台北", "臺北", "基隆", "澎湖", "金門", "連江", "馬祖"
        ]

        # 1. 優先檢查 (位於...) 括號內的詳細地點 (例如：位於臺灣東部海域、位於花蓮縣壽豐鄉)
        match = re.search(r'[（\(]\s*位於\s*(.*?)\s*[）\)]', epicenter_raw)
        if match:
            target = match.group(1).strip()
            # 若括號內包含海域名稱（如：臺灣東部海域、臺灣海峽、花蓮近海等）
            if any(term in target for term in ["海域", "海峽", "近海"]):
                cleaned_sea = target.replace("縣", "").replace("市", "") if ("縣" in target or "市" in target) else target
                return cleaned_sea

            # 若括號內為陸地鄉鎮（如：花蓮縣壽豐鄉），提取縣市名
            for c in county_candidates:
                if c in target:
                    return c.replace("臺", "台")

        # 2. 若無括號或未匹配，檢查整個 epicenter_raw 是否包含海域關鍵字
        sea_candidates = [
            "臺灣東部海域", "台灣東部海域",
            "臺灣東北部海域", "台灣東北部海域",
            "臺灣東南部海域", "台灣東南部海域",
            "臺灣北部海域", "台灣北部海域",
            "臺灣南部海域", "台灣南部海域",
            "臺灣西南部海域", "台灣西南部海域",
            "臺灣海峽", "台灣海峽"
        ]
        for s in sea_candidates:
            if s in epicenter_raw:
                return s

        # 3. 檢查 epicenter_raw 是否包含縣市名
        for c in county_candidates:
            if c in epicenter_raw:
                return c.replace("臺", "台")

        # 4. 降級備用：由陸上最大震度縣市補位
        intensity_data = eq.get("Intensity", {}).get("ShakingArea", [])
        for area in intensity_data:
            c_name = area.get("CountyName", "")
            if c_name:
                c_short = c_name.replace("縣", "").replace("市", "").replace("臺", "台")
                if c_short:
                    return c_short

        # 5. 最終保底：台灣
        return "台灣"

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

    def _is_match_eq(self, item, origin_time_str, issue_time_str, eq_no):
        """比對輪詢到的地震項目是否與目標地震為同一場"""
        item_origin = item.get("EarthquakeInfo", {}).get("OriginTime", "")
        item_no = str(item.get("EarthquakeNo", "")).strip()
        target_no = str(eq_no).strip() if eq_no is not None else ""

        # 1. 顯著地震編號優先比對（排除以 000 結尾的小區域代碼）
        if target_no and not target_no.endswith("000") and item_no and not item_no.endswith("000"):
            if item_no == target_no:
                return True

        # 2. OriginTime 完全比對
        if origin_time_str and item_origin and item_origin == origin_time_str:
            return True

        # 3. OriginTime 前 16 碼 (YYYY-MM-DD HH:MM) 比對（容許秒數微調）
        if origin_time_str and item_origin and len(origin_time_str) >= 16 and len(item_origin) >= 16:
            if item_origin[:16] == origin_time_str[:16]:
                return True

        # 4. IssueTime 完全比對
        item_issue = item.get("IssueTime", "")
        if issue_time_str and item_issue and item_issue == issue_time_str:
            return True

        return False

    async def _check_image_valid(self, url: str) -> bool:
        """異步檢查圖片 URL 是否已在伺服器上生成且可正常讀取 (HTTP 200)"""
        if not url or not isinstance(url, str) or not url.strip().startswith("http"):
            return False
        if not getattr(self.bot, 'session', None) or self.bot.session.closed:
            return False
        clean_url = url.strip()
        try:
            # 優先使用 HEAD 請求極速確認圖片存在
            async with self.bot.session.head(clean_url, timeout=aiohttp.ClientTimeout(total=3.0), allow_redirects=True) as resp:
                if resp.status == 200:
                    content_length = resp.headers.get("Content-Length")
                    if content_length and int(content_length) < 1000:
                        return False
                    return True
                if resp.status == 405:  # 若伺服器不支援 HEAD，退回 GET
                    async with self.bot.session.get(clean_url, timeout=aiohttp.ClientTimeout(total=3.0)) as get_resp:
                        return get_resp.status == 200
                return False
        except Exception:
            return False

    async def _fetch_report_image_quick(self, dataset_id, origin_time_str, issue_time_str, eq_no, api_key, max_retries=3, delay=1.5):
        """
        發送前快速重試抓取圖片網址（每次間隔 1.5 秒，最多重試 3 次，約 4.5 秒）。
        若能在極短時間內取得圖片且確認圖片可訪問 (HTTP 200)，便可讓通知在第一時間連同地圖一起發出。
        """
        if not api_key or not getattr(self.bot, 'session', None) or self.bot.session.closed:
            return ""
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{dataset_id}?limit=5&format=JSON"
        headers = {"Authorization": api_key}
        for _ in range(max_retries):
            await asyncio.sleep(delay)
            try:
                async with self.bot.session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        for item in data.get("records", {}).get("Earthquake", []):
                            if self._is_match_eq(item, origin_time_str, issue_time_str, eq_no):
                                img = item.get("ReportImageURI")
                                if img and isinstance(img, str) and img.strip().startswith("http"):
                                    cand_url = img.strip()
                                    if await self._check_image_valid(cand_url):
                                        return cand_url
                                # 找到目標地震但圖片尚未就緒，結束當前 records 檢查，等待下一輪重試
                                break
            except Exception:
                pass
        return ""

    async def _poll_and_update_report_image(self, dataset_id, origin_time_str, issue_time_str, eq_no, api_key, sent_detailed_items):
        """當地震報告發布時若圖片未生成，背景漸進式輪詢 CWA API 並在確認圖片生成完成後自動更新已發送的詳細 Embed"""
        if not sent_detailed_items or not getattr(self.bot, 'session', None) or self.bot.session.closed:
            return
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{dataset_id}?limit=5&format=JSON"
        headers = {"Authorization": api_key}
        # 總共輪詢約 7 分鐘：前 1 分鐘密集，之後漸進拉長
        delays = [5, 5, 8, 10, 10, 15, 15, 20, 20, 30, 30, 30, 30, 45, 45, 60, 60]
        for attempt, delay in enumerate(delays, 1):
            await asyncio.sleep(delay)
            try:
                async with self.bot.session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json(content_type=None)
                        for item in data.get("records", {}).get("Earthquake", []):
                            if self._is_match_eq(item, origin_time_str, issue_time_str, eq_no):
                                img = item.get("ReportImageURI")
                                if img and isinstance(img, str) and img.strip().startswith("http"):
                                    cand_url = img.strip()
                                    # 關鍵：必須確認圖片在靜態伺服器上真正生成完畢 (HTTP 200)
                                    if await self._check_image_valid(cand_url):
                                        cache_buster = int(datetime.now().timestamp())
                                        img_url_with_cache = f"{cand_url}?t={cache_buster}"
                                        updated_count = 0
                                        for msg, embed in sent_detailed_items:
                                            try:
                                                embed.set_image(url=img_url_with_cache)
                                                await msg.edit(embed=embed)
                                                updated_count += 1
                                            except Exception as e:
                                                logger.warning(f"⚠️ [地震通知] 更新圖片至 Discord 訊息失敗: {e!r}")
                                        logger.info(f"🖼️ [地震通知] 已成功輪詢補上地震報告圖片 (第 {attempt}/{len(delays)} 次嘗試, 成功更新 {updated_count}/{len(sent_detailed_items)} 則訊息, OriginTime: {origin_time_str}, URL: {cand_url})")
                                        return
                                # 圖片尚未生成完成，跳出內層迴圈等待下一輪嘗試
                                break
            except Exception as e:
                logger.debug(f"輪詢地震報告圖片失敗 ({attempt}/{len(delays)}): {e!r}")
        logger.warning(f"⚠️ [地震通知] 輪詢逾時，未能取得地震報告圖片 (OriginTime: {origin_time_str}, EqNo: {eq_no})")

    async def _process_and_notify(self, eq, eq_intensities, mag, settings, is_sig=False, dataset_id="E-A0015-001", api_key=""):
        """根據地震資料與各伺服器設定發送通知"""
        # 解析震央位置簡稱（提取「位於...」括號內文字，例如「花蓮縣豐濱鄉」）
        epicenter = eq.get("EarthquakeInfo", {}).get("Epicenter", {}).get("Location", "")
        epi_match = re.search(r'位於(.*?)[）\)]', epicenter)
        loc_display = epi_match.group(1).strip() if epi_match else (epicenter[:15] if epicenter else "未知地點")

        status_cog = self.bot.get_cog("Status")
        if status_cog and hasattr(status_cog, "set_eq_report"):
            max_eq_int = max(eq_intensities.values()) if eq_intensities else 0.0
            areas = eq.get("Intensity", {}).get("ShakingArea", [])
            for area in areas:
                int_str = area.get("AreaIntensity") or area.get("AreaDesc", "")
                match = re.search(r'(\d+)(強|弱)?', str(int_str))
                if match:
                    base_val = float(match.group(1))
                    val = base_val + 0.5 if match.group(2) == "強" else base_val
                    if val > max_eq_int:
                        max_eq_int = val

            status_cog.set_eq_report(loc_display, mag, max_intensity=max_eq_int)

        def create_view():
            if is_sig:
                eq_no = eq.get("EarthquakeNo")
                if eq_no:
                    v = discord.ui.View()
                    v.add_item(discord.ui.Button(
                        label="TWERG 體感回報",
                        emoji="📝", 
                        url=f"https://twerg.org/dyfi?eq={eq_no}"
                    ))
                    return v
            return None

        sent_tasks = []

        async def send_to_channel(ch, cnt, emb, v, sil, is_detailed_format):
            try:
                if v:
                    m = await ch.send(content=cnt, embed=emb, view=v, silent=sil)
                else:
                    m = await ch.send(content=cnt, embed=emb, silent=sil)
                return (m, emb, is_detailed_format)
            except Exception as ex:
                logger.error(f"發送地震通知失敗 ({ch.name}): {ex!r}")
                return None

        for guild_id, d in settings.items():
            global_silent = d.get('global_silent', False)
            eq_alerts = d.get('eq_alerts', {})
            if not isinstance(eq_alerts, dict):
                continue

            for loc_name, alert_info in eq_alerts.items():
                if isinstance(alert_info, dict):
                    min_mag = alert_info.get('min_magnitude', 5.5)
                    min_int = alert_info.get('min_intensity', 3)
                    channel_id = alert_info.get('channel_id')
                    detailed_format = alert_info.get('detailed_format', False)
                elif isinstance(alert_info, (int, str)) and not isinstance(alert_info, bool) and str(alert_info).isdigit():
                    channel_id = alert_info
                    min_mag = 5.5
                    min_int = 3
                    detailed_format = False
                else:
                    continue

                if not channel_id:
                    continue

                try:
                    ch_id_int = int(channel_id)
                except (ValueError, TypeError):
                    continue

                if loc_name == "全台接收":
                    if mag < min_mag:
                        continue
                    max_eq_int = max(eq_intensities.values()) if eq_intensities else 0
                    if max_eq_int < min_int:
                        continue
                    channel = self.bot.get_channel(ch_id_int)
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
                            view = create_view()
                            sent_tasks.append(self.bot.loop.create_task(send_to_channel(channel, content, embed, view, global_silent, True)))
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.debug(f"📢 [地震通知] 已發送預警至 {guild_name} ({channel.name}) - 全台接收 (規模{float(mag):.1f})")
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
                    channel = self.bot.get_channel(ch_id_int)
                    if channel:
                        content = "🏚️ 地震通知"
                        mention_role_id = d.get('eq_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"

                        embed = None
                        is_detailed = False
                        if detailed_format:
                            try:
                                embed = build_eq_embed(eq)
                                recv_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
                                embed.set_footer(text=f"中央氣象署 • 接收時間 {recv_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
                                is_detailed = True
                            except Exception as e:
                                logger.error(f"構建詳細格式 embed 失敗: {e!r}")
                                embed = None

                        if not embed:
                            embed_color = get_eq_color(mag, loc_intensity)
                            display_int = format_intensity(loc_intensity)
                            suffix = "" if "弱" in display_int or "強" in display_int else "級"
                            embed = discord.Embed(
                                title="",
                                description=f"剛才發生了規模{float(mag):.1f}的地震。\n**{loc_name}**{nearest_msg} 震度{display_int}{suffix}。",
                                color=embed_color
                            )
                            recv_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
                            embed.set_footer(text=f"中央氣象署 • 接收時間 {recv_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
                            is_detailed = False

                        if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                            logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                        else:
                            view = create_view()
                            sent_tasks.append(self.bot.loop.create_task(send_to_channel(channel, content, embed, view, global_silent, is_detailed)))
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.debug(f"📢 [地震通知] 已發送預警至 {guild_name} ({channel.name}) - {loc_name} (規模{float(mag):.1f})")

        if sent_tasks:
            results = await asyncio.gather(*sent_tasks, return_exceptions=True)
            sent_items = [r for r in results if isinstance(r, tuple) and r is not None]
            sent_cnt = len(sent_items)
            if sent_cnt > 0:
                logger.info(f"📢 [地震通知] 廣播完成 (規模 {float(mag):.1f}) | 共發送 {sent_cnt} 個頻道")
            raw_img = eq.get("ReportImageURI")
            has_image = bool(raw_img and isinstance(raw_img, str) and raw_img.strip().startswith("http"))
            origin_time_str = eq.get("EarthquakeInfo", {}).get("OriginTime", "")
            issue_time_str = eq.get("IssueTime", "")
            eq_no = eq.get("EarthquakeNo", "")
            detailed_items = [(msg, emb) for msg, emb, is_det in sent_items if is_det]
            if detailed_items and not has_image and api_key and (origin_time_str or issue_time_str or eq_no):
                task = self.bot.loop.create_task(
                    self._poll_and_update_report_image(dataset_id, origin_time_str, issue_time_str, eq_no, api_key, detailed_items)
                )
                self.background_tasks.add(task)
                task.add_done_callback(self.background_tasks.discard)

    @tasks.loop(seconds=15.0)
    async def check_eq_loop(self):
        if self.bot.is_closed() or not getattr(self.bot, 'session', None) or self.bot.session.closed or self.bot.is_abnormal_grace_period():
            return
            
        api_key = self.get_api_key()
        if not api_key: return

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
                    data = await response.json(content_type=None)
                    for eq in data.get("records", {}).get("Earthquake", []):
                        issue_time = eq.get("IssueTime", "")
                        origin_time_str = eq.get("EarthquakeInfo", {}).get("OriginTime", "")
                        eq_no = str(eq.get("EarthquakeNo", "")).strip()

                        # 去重檢查鍵：包含發布時間、發震時間與編號
                        sig_keys = []
                        if issue_time:
                            sig_keys.append(f"sig_{issue_time}")
                        if origin_time_str:
                            sig_keys.append(f"sig_time_{origin_time_str}")
                        if eq_no and not eq_no.endswith("000"):
                            sig_keys.append(f"sig_no_{eq_no}")

                        if not sig_keys or any(k in self.processed_eqs for k in sig_keys):
                            continue

                        # 時效檢查
                        if origin_time_str:
                            try:
                                origin_time = datetime.fromisoformat(origin_time_str)
                                now = datetime.now(origin_time.tzinfo)
                                if (now - origin_time) > timedelta(days=1):
                                    logger.info(f"⏭️ [地震通知] 略過超過 1 天的顯著地震報告 (OriginTime: {origin_time_str})")
                                    for k in sig_keys:
                                        self.processed_eqs.add(k)
                                    continue
                            except Exception:
                                pass

                        for k in sig_keys:
                            self.processed_eqs.add(k)
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

                        # 檢查第一時間取得的圖片是否有效且已生成 (HTTP 200)
                        raw_img = eq.get("ReportImageURI")
                        valid_img = ""
                        if raw_img and isinstance(raw_img, str) and raw_img.strip().startswith("http"):
                            if await self._check_image_valid(raw_img.strip()):
                                valid_img = raw_img.strip()

                        # 若尚未生成或無法讀取，先快速重試幾次（嘗試與文字一同發出）
                        if not valid_img:
                            quick_img = await self._fetch_report_image_quick("E-A0015-001", origin_time_str, issue_time, eq.get("EarthquakeNo"), api_key)
                            if quick_img:
                                valid_img = quick_img
                                logger.info(f"🖼️ [地震通知] 第一時間已成功獲取顯著地震地圖圖片 (OriginTime: {origin_time_str}, URL: {quick_img})")

                        # 將驗證後的圖片網址更新進 eq（若無效則清空，避免 Discord 載入 404 並確保啟動後續輪詢補圖）
                        eq["ReportImageURI"] = valid_img

                        await self._process_and_notify(eq, eq_intensities, mag, settings, is_sig=True, dataset_id="E-A0015-001", api_key=api_key)

                        # 檢查是否達到平安通報自動觸發門檻 (規模 >= 6.3 且 最大震度 >= 5弱 / 5.0)
                        intensity_data = eq.get("Intensity", {}).get("ShakingArea", [])
                        max_int_val = 0.0
                        for area in intensity_data:
                            intensity = area.get("AreaIntensity", "")
                            if intensity:
                                match = re.search(r'(\d+)(強|弱)?', str(intensity))
                                if match:
                                    base_val = float(match.group(1))
                                    val = base_val + 0.5 if match.group(2) == "強" else base_val
                                    if val > max_int_val:
                                        max_int_val = val

                        if mag >= 6.3 and max_int_val >= 5.0:
                            should_trigger = False
                            if not is_push_module_enabled("alert_safety"):
                                logger.info(f"ℹ️ [平安通報] 偵測到強震 (規模 {mag}，最大震度 {max_int_val})，但平安通報自動偵測開關已停用，略過自動發起。")
                            else:
                                now_utc = datetime.now(timezone.utc)
                                should_trigger = True
                                if self.last_major_quake:
                                    try:
                                        last_time = datetime.fromisoformat(self.last_major_quake["time"])
                                        last_mag = float(self.last_major_quake["mag"])
                                        if (now_utc - last_time) < timedelta(hours=72):
                                            if mag <= last_mag:
                                                should_trigger = False
                                                logger.info(f"ℹ️ [平安通報] 偵測到強震 (規模 {mag})，但 72 小時內已發生過規模 {last_mag} 之地震，判定為餘震略過自動發起。")
                                            else:
                                                logger.info(f"🚨 [平安通報] 偵測到更大強震 (規模 {mag} > 前次 {last_mag})，將更新主震並發起通報！")
                                    except Exception as e:
                                        logger.warning(f"⚠️ 比對前次強震記錄發生錯誤: {e}")

                            if should_trigger:
                                self.last_major_quake = {
                                    "time": now_utc.isoformat(),
                                    "mag": mag
                                }
                                county = self._extract_eq_county(eq)
                                origin_year = datetime.now().year
                                if origin_time_str:
                                    try:
                                        origin_year = datetime.fromisoformat(origin_time_str).year
                                    except Exception:
                                        pass
                                event_title = f"{origin_year}年{county}地震"
                                event_desc = f"剛才{county}發生了規模{mag}的地震。"

                                try:
                                    from cogs.alarm.alert_safe import broadcast_safety_checkin
                                    self.bot.loop.create_task(
                                        broadcast_safety_checkin(
                                            self.bot,
                                            title=event_title,
                                            description=event_desc,
                                            hours=48,
                                            created_by="自動強震系統"
                                        )
                                    )
                                    logger.info(f"🚨 [平安通報] 已自動觸發平安通報廣播：{event_title}（規模 {mag}，最大震度 {max_int_val}）")
                                except Exception as e:
                                    logger.error(f"❌ 自動觸發平安通報廣播失敗: {e}")

                        # 只處理最新一筆
                        break
                else:
                    if self.last_sig_status != response.status:
                        logger.warning(f"🌐 [地震通知] 顯著有感地震: API 狀態碼: {response.status}")
                        self.last_sig_status = response.status
        except Exception as e:
            if self.bot.is_closed() or not getattr(self.bot, 'session', None) or self.bot.session.closed:
                return
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
                    data = await response.json(content_type=None)
                    for eq in data.get("records", {}).get("Earthquake", []):
                        issue_time = eq.get("IssueTime", "")
                        origin_time_str = eq.get("EarthquakeInfo", {}).get("OriginTime", "")

                        # 去重檢查鍵：包含發布時間與發震時間
                        small_keys = []
                        if issue_time:
                            small_keys.append(f"small_{issue_time}")
                        if origin_time_str:
                            small_keys.append(f"small_time_{origin_time_str}")

                        if not small_keys or any(k in self.processed_eqs for k in small_keys):
                            continue

                        # 時效檢查
                        if origin_time_str:
                            try:
                                origin_time = datetime.fromisoformat(origin_time_str)
                                now = datetime.now(origin_time.tzinfo)
                                if (now - origin_time) > timedelta(days=1):
                                    logger.info(f"⏭️ [地震通知] 略過超過 1 天的小區域地震報告 (OriginTime: {origin_time_str})")
                                    for k in small_keys:
                                        self.processed_eqs.add(k)
                                    continue
                            except Exception:
                                pass

                        for k in small_keys:
                            self.processed_eqs.add(k)
                        if len(self.processed_eqs) > 200:
                            self.processed_eqs.pop()

                        mag_val = eq.get("EarthquakeInfo", {}).get("EarthquakeMagnitude", {}).get("MagnitudeValue", "0")
                        try:
                            mag = float(mag_val)
                        except (ValueError, TypeError):
                            mag = 0.0

                        # 小區域地震直接使用測站資料 + 20km 匹配
                        eq_intensities = self._parse_rest_intensities(eq)

                        # 檢查第一時間取得的圖片是否有效且已生成 (HTTP 200)
                        raw_img = eq.get("ReportImageURI")
                        valid_img = ""
                        if raw_img and isinstance(raw_img, str) and raw_img.strip().startswith("http"):
                            if await self._check_image_valid(raw_img.strip()):
                                valid_img = raw_img.strip()

                        # 若尚未生成或無法讀取，先快速重試幾次（嘗試與文字一同發出）
                        if not valid_img:
                            quick_img = await self._fetch_report_image_quick("E-A0016-001", origin_time_str, issue_time, eq.get("EarthquakeNo"), api_key)
                            if quick_img:
                                valid_img = quick_img
                                logger.info(f"🖼️ [地震通知] 第一時間已成功獲取小區域地震地圖圖片 (OriginTime: {origin_time_str}, URL: {quick_img})")

                        # 將驗證後的圖片網址更新進 eq（若無效則清空，避免 Discord 載入 404 並確保啟動後續輪詢補圖）
                        eq["ReportImageURI"] = valid_img

                        await self._process_and_notify(eq, eq_intensities, mag, settings, is_sig=False, dataset_id="E-A0016-001", api_key=api_key)
                        # 只處理最新一筆
                        break
                else:
                    if self.last_small_status != response.status:
                        logger.warning(f"🌐 [地震通知] 小區域地震: API 狀態碼: {response.status}")
                        self.last_small_status = response.status
        except Exception as e:
            if self.bot.is_closed() or not getattr(self.bot, 'session', None) or self.bot.session.closed:
                return
            err_str = f"EXC_{type(e).__name__}"
            if self.last_small_status != err_str:
                logger.warning(f"⚠️ [地震通知] 小區域地震檢查失敗: {type(e).__name__} {e!r}")
                self.last_small_status = err_str

    @check_eq_loop.before_loop
    async def before_check_eq(self):
        await self.bot.wait_until_ready()
        api_key = self.get_api_key()
        if not api_key or not self.bot.session: return

        # 初始化：記錄當前最新顯著有感地震，避免啟動時重複通知
        sig_url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001?limit=1&format=JSON"
        headers = {"Authorization": api_key}
        try:
            async with self.bot.session.get(sig_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    eqs = data.get("records", {}).get("Earthquake", [])
                    if eqs:
                        issue_time = eqs[0].get("IssueTime", "")
                        origin_time_str = eqs[0].get("EarthquakeInfo", {}).get("OriginTime", "")
                        eq_no = str(eqs[0].get("EarthquakeNo", "")).strip()
                        if issue_time:
                            self.processed_eqs.add(f"sig_{issue_time}")
                        if origin_time_str:
                            self.processed_eqs.add(f"sig_time_{origin_time_str}")
                        if eq_no and not eq_no.endswith("000"):
                            self.processed_eqs.add(f"sig_no_{eq_no}")
        except Exception:
            pass

        # 初始化：記錄當前最新小區域地震
        small_url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0016-001?limit=1&format=JSON"
        try:
            async with self.bot.session.get(small_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    eqs = data.get("records", {}).get("Earthquake", [])
                    if eqs:
                        issue_time = eqs[0].get("IssueTime", "")
                        origin_time_str = eqs[0].get("EarthquakeInfo", {}).get("OriginTime", "")
                        if issue_time:
                            self.processed_eqs.add(f"small_{issue_time}")
                        if origin_time_str:
                            self.processed_eqs.add(f"small_time_{origin_time_str}")
        except Exception:
            pass

async def setup(bot):
    await bot.add_cog(EarthquakeAlertCog(bot))