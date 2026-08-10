import discord
from discord.ext import commands, tasks
import aiohttp
import json
import re
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta, time
from modules.town_mapping import load_town_mapping
from modules.database import get_all_settings
from modules.cache_manager import load_cache
import logging

logger = logging.getLogger(__name__)

TAIPEI_TZ = timezone(timedelta(hours=8))
NEWS_CHECK_TIMES = [
    time(hour=8, minute=0, second=0, tzinfo=TAIPEI_TZ),
    time(hour=20, minute=0, second=0, tzinfo=TAIPEI_TZ)
]

def is_location_matched(loc_name: str, area_text: str, combined_text: str, alert_type: str) -> bool:
    loc_name_clean = loc_name.replace("臺", "台")
    area_text_clean = area_text.replace("臺", "台") if area_text else ""
    combined_text_clean = combined_text.replace("臺", "台") if combined_text else ""
    
    if loc_name_clean == "全台接收":
        return True
        
    county = loc_name_clean[:3]
    town = loc_name_clean[3:]
    
    # 1. 判斷是否為縣市級的警報 (area_text 中只有縣市，沒有特別指定到鄉鎮)
    tokens = [t.strip() for t in re.split(r'[,，、]', area_text_clean)]
    is_county_wide = False
    for t in tokens:
        # 去除結尾可能的 (共X個市區) 等字眼
        t_clean = re.sub(r'\(共\d+個[^)]*\)', '', t).strip()
        # 去除 "及其沿海" 或 "沿海" 等後綴
        t_clean = re.sub(r'及其沿海$', '', t_clean).strip()
        t_clean = re.sub(r'沿海$', '', t_clean).strip()
        
        if t_clean == county:
            is_county_wide = True
            break
            
    if is_county_wide:
        return True
        
    # 如果使用者只訂閱了縣市級別 (例如 "台北市")，只要 combined_text 有包含該完整縣市名稱就可以
    if not town:
        return county in combined_text_clean
        
    # 2. 如果使用者有訂閱到鄉鎮，且非縣市級警報
    # 情況 2.1：縣市跟鄉鎮同時出現
    if county in combined_text_clean and town in combined_text_clean:
        return True
        
    # 情況 2.2：針對部分警報可能省略縣市只寫鄉鎮，但要過濾掉單字或常見區名(東區/西區等)
    # 保留完整字詞匹配，不刪除行政區後綴
    common_towns = {"東區", "西區", "南區", "北區", "中區", "中正區", "中山區", "大安區", "信義區", "仁愛區"}
    if town not in common_towns:
        if town in combined_text_clean:
            return True
            
    # 情況 2.3：保底的完整名稱比對 (例如 "新北市新店區")
    if loc_name_clean in combined_text_clean:
        return True
        
    return False

class CBSAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        cache = load_cache()
        self.processed_ids = set(cache.get("cbs_processed", []))
        self.first_run_done = cache.get("cbs_first_run", False)
        self.news_first_run_done = cache.get("cbs_news_first_run", False)
        self.last_status_code = None
        self.last_month_status_code = None
        
        # 預載所有鄉鎮市區名稱，用於從內文提取
        self.valid_towns = set()
        try:
            mapping = load_town_mapping()
            for combos in mapping.values():
                for fullname, *_ in combos:
                    if len(fullname) > 3:
                        self.valid_towns.add(fullname[3:])
        except Exception as e:
            logger.error(f"Failed to load towns for CBS: {e!r}")
            
        self.check_cbs_loop.start()
        self.check_cbs_news_loop.start()

    async def fetch_locations_from_kml(self, file_code: str) -> list[str]:
        """由 CBS 的 file_code 或 page_key 下載 KML 並以 NLSC 逆向地理查詢精確鄉鎮市區"""
        if not file_code or len(file_code) < 5 or not getattr(self.bot, 'session', None) or self.bot.session.closed:
            return []
        
        yymm = file_code[:4]
        urlkey = file_code[4:]
        kml_url = f"https://cbs.tw/public/upload/files/map/20{yymm}/{urlkey}.kml"
        
        try:
            async with self.bot.session.get(kml_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status != 200:
                    return []
                xml_text = await resp.text()
        except Exception as e:
            logger.debug(f"KML fetch failed for {file_code}: {e!r}")
            return []
            
        try:
            root = ET.fromstring(xml_text)
            ns = {'kml': 'http://www.opengis.net/kml/2.2'}
            coords_elements = root.findall('.//kml:coordinates', ns)
            if not coords_elements:
                coords_elements = root.findall('.//coordinates')
                
            pts = []
            for elem in coords_elements:
                if elem.text:
                    for token in elem.text.strip().split():
                        parts = token.split(',')
                        if len(parts) >= 2:
                            try:
                                lon, lat = float(parts[0]), float(parts[1])
                                pts.append((lon, lat))
                            except ValueError:
                                pass
            if not pts:
                return []
                
            avg_lon = sum(p[0] for p in pts) / len(pts)
            avg_lat = sum(p[1] for p in pts) / len(pts)
            
            sample_pts = [(avg_lon, avg_lat)]
            step = max(1, len(pts) // 4)
            for i in range(0, len(pts), step):
                if len(sample_pts) >= 5:
                    break
                if pts[i] not in sample_pts:
                    sample_pts.append(pts[i])
                    
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            
            towns = []
            for lon, lat in sample_pts:
                nlsc_url = f"https://api.nlsc.gov.tw/other/TownVillagePointQuery/{lon}/{lat}"
                try:
                    async with self.bot.session.get(nlsc_url, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        if resp.status == 200:
                            nlsc_xml = await resp.text()
                            n_root = ET.fromstring(nlsc_xml)
                            cty = n_root.findtext('ctyName', '').strip()
                            town = n_root.findtext('townName', '').strip()
                            if cty and town:
                                full = f"{cty}{town}"
                                if full not in towns:
                                    towns.append(full)
                except Exception as e:
                    logger.debug(f"NLSC query failed for ({lon}, {lat}): {e!r}")
                    
            return towns
        except Exception as e:
            logger.debug(f"KML parsing error for {file_code}: {e!r}")
            return []

    def save_state(self):
        return {
            "cbs_processed": list(self.processed_ids),
            "cbs_first_run": self.first_run_done,
            "cbs_news_first_run": self.news_first_run_done
        }

    def cog_unload(self):
        self.check_cbs_loop.cancel()
        self.check_cbs_news_loop.cancel()

    @tasks.loop(seconds=12.0)
    async def check_cbs_loop(self):
        if self.bot.is_closed() or not getattr(self.bot, 'session', None) or self.bot.session.closed:
            return

        try:
            settings = get_all_settings()
        except Exception as e:
            logger.error(f"Failed to load settings: {e!r}")
            return

        # 若沒有任何伺服器設定 CBS 預警，則不呼叫 API
        has_cbs_alerts = any('cbs_alerts' in d and d['cbs_alerts'] for d in settings.values())
        if not has_cbs_alerts:
            # 即使沒人設定，也要把 first_run_done 設為 True，避免之後有人設定時舊訊息被推播
            self.first_run_done = True
            self.news_first_run_done = True
            return

        now = datetime.now(timezone(timedelta(hours=8)))
        yyyymm = now.strftime("%Y%m")
        url = f"https://cbs.tw/public/upload/files/json/{yyyymm}.json"
        
        try:
            async with self.bot.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    if self.last_status_code != resp.status:
                        logger.warning(f"🌐 [爬蟲抓取] 災防告警: {url} -> 狀態碼: {resp.status}")
                        self.last_status_code = resp.status
                    return
                
                if self.last_status_code not in (None, 200):
                    logger.info(f"✅ [爬蟲抓取] 災防告警: {url} -> 已恢復正常連線 (狀態碼: 200)")
                self.last_status_code = 200
                
                # 若月初無警報，網址會轉至 /forbidden，導致 json 解析錯誤
                if "forbidden" in str(resp.url):
                    return
                    
                text = await resp.text()
                if not text.strip():
                    return
                data = json.loads(text)
        except json.JSONDecodeError:
            return
        except Exception as e:
            if self.bot.is_closed() or not getattr(self.bot, 'session', None) or self.bot.session.closed:
                return
            err_str = f"EXC_{type(e).__name__}"
            if self.last_status_code != err_str:
                logger.error(f"Failed to fetch CBS JSON: {e!r}")
                self.last_status_code = err_str
            return

        if not data.get("success"):
            return
            
        cbs_data = data.get("data", {})
        # 防止 API 在無資料時回傳空陣列 [] 導致 items() 報錯
        if not isinstance(cbs_data, dict):
            cbs_data = {}
            
        new_alerts = []
        
        # 換月邊界處理：如果是每個月的 1 號凌晨 1 點前，也順便抓上個月的資料，避免漏掉午夜 23:59:59 的警報
        if now.day == 1 and now.hour == 0:
            last_month = now - timedelta(days=1)
            last_yyyymm = last_month.strftime("%Y%m")
            last_url = f"https://cbs.tw/public/upload/files/json/{last_yyyymm}.json"
            try:
                async with self.bot.session.get(last_url, timeout=10) as resp:
                    if resp.status == 200:
                        if self.last_month_status_code not in (None, 200):
                            logger.info(f"✅ [爬蟲抓取] 災防告警: {last_url} -> 已恢復正常連線 (狀態碼: 200)")
                        self.last_month_status_code = 200
                        if "forbidden" not in str(resp.url):
                            text = await resp.text()
                            if text.strip():
                                last_data = json.loads(text)
                                if last_data.get("success") and isinstance(last_data.get("data"), dict):
                                    # 把上個月的資料合併進來
                                    cbs_data.update(last_data["data"])
                    else:
                        if self.last_month_status_code != resp.status:
                            logger.warning(f"🌐 [爬蟲抓取] 災防告警: {last_url} -> 狀態碼: {resp.status}")
                            self.last_month_status_code = resp.status
            except Exception:
                pass
        
        for date_str, time_dict in cbs_data.items():
            for time_str, json_dict in time_dict.items():
                for json_id, alert_info in json_dict.items():
                    if json_id in self.processed_ids:
                        continue
                    
                    self.processed_ids.add(json_id)
                        
                    new_alerts.append(alert_info)

        # 避免機器人剛啟動時把當月所有歷史告警都推播出去
        if not self.first_run_done:
            self.first_run_done = True
            return

        # 依照發佈時間由舊到新排序，確保推播的順序符合時間軸
        new_alerts.sort(key=lambda x: x.get("release_time", ""))

        for alert in new_alerts:
            page_key = alert.get("page_key") or alert.get("file_code") or ""
            alert_type = alert.get("alertType")
            topic = alert.get("topic") or "災防告警"
            if topic == "暴潮告警" or "暴潮" in topic:
                alert_type = "stormsurge"
            area_text = alert.get("area_text") or ""
            cmam_text = alert.get("CMAMtext") or ""
            sender_name = alert.get("sender_name") or ""
            release_time = alert.get("release_time") or ""
            expires = alert.get("expires") or ""
            
            # 嘗試透過 KML 下載與逆向地理查詢取得精確鄉鎮市區（地震速報、防空警報、海嘯警報、全國降速演習等廣域/極高時效警報跳過 KML）
            skip_kml_types = {"earthquakeew", "airraidalert", "tsunami", "commdisrupt"}
            if page_key and alert_type not in skip_kml_types:
                kml_towns = await self.fetch_locations_from_kml(page_key)
                if kml_towns:
                    area_text = "、".join(kml_towns)
                    logger.info(f"📍 [CBS預警] 成功經由 KML 精確定位: {area_text} ({page_key})")
            
            # 檢查是否過期太久 (超過 15 分鐘)
            if release_time:
                try:
                    rt = datetime.strptime(release_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))
                    if (now - rt).total_seconds() > 900:
                        logger.info(f"⚠️ [CBS預警] 警報已發布超過 15 分鐘，放棄推播: {topic} ({release_time})")
                        continue
                except ValueError:
                    pass
            
            emoji = "⚠️"
            if alert_type == "airquality": emoji = "😷"
            elif alert_type == "airraidalert": emoji = "🚀"
            elif alert_type == "barrierlake": emoji = "🏞️"
            elif alert_type == "commdisrupt": emoji = "📶"
            elif alert_type == "debrisflow": emoji = "⛰️"
            elif alert_type == "earthquakeew": emoji = "🏚️"
            elif alert_type == "electric": emoji = "⚡"
            elif alert_type == "emergalert": emoji = "🚨"
            elif alert_type == "evacuation": emoji = "🏃"
            elif alert_type == "flood": emoji = "🌊"
            elif alert_type == "forestfire": emoji = "🔥"
            elif alert_type == "hurricfrcwnd": emoji = "🌀"
            elif alert_type == "nuclear": emoji = "☢️"
            elif alert_type == "reservoirdis": emoji = "🚰"
            elif alert_type == "roadclose": emoji = "⛔"
            elif alert_type == "stormsurge": emoji = "🌊"
            elif alert_type == "systemtest": emoji = "📢"
            elif alert_type == "thunderstorm": emoji = "🌩️"
            elif alert_type == "tsunami": emoji = "🌊"
            elif alert_type == "largesurf": emoji = "🌊"
            
            embed = discord.Embed(
                title=f"{topic}",
                color=0xfcd200
            )
            embed.set_thumbnail(url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cbs.webp")
            
            formatted_area = re.sub(r'\(共\d+個[^)]*\)', '', area_text)
            formatted_area = re.sub(r'\s*\(\d+\)', '', formatted_area)
            formatted_area = re.sub(r'發布區域\d+', '特定區域', formatted_area)
            formatted_area = formatted_area.replace("地震速報廣播範圍", "")
            formatted_area = formatted_area.replace("Test_Geocode", "")
            
            # 去重複並以頓號連接
            areas = []
            for a in formatted_area.replace("、", ",").replace("，", ",").split(","):
                a = a.strip()
                if a and a not in areas:
                    areas.append(a)
                    
            if cmam_text and hasattr(self, 'valid_towns'):
                directional_districts = {"東區", "南區", "西區", "北區", "中區", "中西區"}
                exclude_keywords = {"分局", "工程", "養護", "工務", "管理處", "辦公室"}
                matches = re.finditer(r'([\u4e00-\u9fa5]{1,4}(?:鄉|鎮|市|區))', cmam_text)
                for match in matches:
                    m = match.group(1)
                    end_pos = match.end()
                    
                    after_text = cmam_text[end_pos:end_pos+6]
                    if any(after_text.startswith(kw) for kw in exclude_keywords):
                        continue
                        
                    for i in range(0, len(m) - 1):
                        candidate = m[i:]
                        if candidate in self.valid_towns:
                            if candidate in directional_districts:
                                match_start = end_pos - len(candidate)
                                if match_start == 0 or cmam_text[match_start - 1] not in ["市", "縣"]:
                                    break
                                    
                            candidate_normalized = candidate.replace("臺", "台")
                            if not any(candidate_normalized in a.replace("臺", "台") for a in areas):
                                areas.append(candidate)
                            break
                            
            formatted_area = "、".join(areas)
            if formatted_area:
                embed.add_field(name="影響區域", value=formatted_area, inline=False)
                
            if cmam_text:
                embed.add_field(name="", value=f"```text\n{cmam_text}\n```", inline=False)
                
            clean_sender_name = sender_name.split('@')[0] if '@' in sender_name else sender_name
            if expires:
                expires_str = expires.replace("T", " ").replace("+08:00", "")
                embed.set_footer(text=f"發布單位 {clean_sender_name}\n發佈時間 {release_time}\n失效時間 {expires_str}")
            else:
                embed.set_footer(text=f"發布單位 {clean_sender_name}\n發佈時間 {release_time}")
            
            # 準備用於配對的合併字串，統一將「臺」替換為「台」
            combined_text = f"{area_text} {topic} {sender_name} {cmam_text}".replace("臺", "台")
            
            is_test = alert_type == "systemtest" or any(kw in topic or kw in cmam_text for kw in ["測試", "演練", "演習", "TEST", "test"])
            
            # 過濾掉名稱中包含「X山區」的行政區（如中山區、岡山區等），避免誤判為山區警報
            mountain_district_pattern = r'(?:中山|松山|文山|泰山|金山|龜山|香山|東山|鼓山|鳳山|岡山|旗山)區'
            clean_cmam_text = re.sub(mountain_district_pattern, '', cmam_text)
            clean_area_text = re.sub(mountain_district_pattern, '', area_text)
            clean_topic = re.sub(mountain_district_pattern, '', topic)
            is_mountain = "山區暴雨" in topic or "山區" in clean_cmam_text or "山區" in clean_area_text or "山區" in clean_topic
            
            for guild_id, d in settings.items():
                global_silent = d.get('global_silent', False)
                cbs_alerts = d.get('cbs_alerts', {})
                if not isinstance(cbs_alerts, dict):
                    continue
                    
                for loc_name, alert_info in cbs_alerts.items():
                    # 匹配邏輯
                    if not is_location_matched(loc_name, area_text, combined_text, alert_type):
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
                        if not (is_test and receive_test and alert_type == "systemtest"):
                            continue
                        
                    ch_id = alert_info.get("channel_id") if isinstance(alert_info, dict) else alert_info
                    if not ch_id or isinstance(ch_id, bool):
                        continue
                    try:
                        ch_id_int = int(ch_id)
                    except (ValueError, TypeError):
                        continue
                    channel = self.bot.get_channel(ch_id_int)
                    if not channel: continue
                    
                    try:
                        content = f"{emoji} 災防告警"
                        mention_role_id = d.get('cbs_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"
                        if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                            logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                        else:
                            await channel.send(content=content, embed=embed, silent=global_silent)
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.info(f"📢 [CBS預警] 已發送至 {guild_name} ({channel.name}) - {topic} (配對: {loc_name})")
                    except Exception as e:
                        logger.error(f"Failed to send CBS alert to {ch_id}: {e!r}")

    async def check_cbs_news(self, settings):
        url = "https://cbs.tw/public/upload/files/json/news/newsRes.json"
        try:
            async with self.bot.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return
                text = await resp.text()
                if not text.strip():
                    return
                data = json.loads(text)
        except Exception:
            return

        if not data.get("success") or not isinstance(data.get("data"), list):
            return

        news_list = data["data"]

        # 首次執行時將現有新聞標記為已處理，避免機器人啟動時推播舊新聞
        if not self.news_first_run_done:
            for item in news_list:
                news_id = str(item.get("id"))
                if news_id:
                    self.processed_ids.add(f"news_{news_id}")
            self.news_first_run_done = True
            return

        for item in news_list:
            news_id = str(item.get("id"))
            if not news_id:
                continue
            key = f"news_{news_id}"
            if key in self.processed_ids:
                continue

            self.processed_ids.add(key)

            detail_url = f"https://cbs.tw/public/upload/files/json/news/news_{news_id}.json"
            title = item.get("title") or "災防告警演習預告"
            desc = ""
            start_time = item.get("start_time") or ""

            try:
                async with self.bot.session.get(detail_url, timeout=10) as resp:
                    if resp.status == 200:
                        detail_text = await resp.text()
                        if detail_text.strip():
                            detail_data = json.loads(detail_text)
                            if detail_data.get("success") and isinstance(detail_data.get("data"), dict):
                                info = detail_data["data"]
                                title = info.get("title") or title
                                desc = info.get("desc") or desc
                                start_time = info.get("start_time") or start_time
            except Exception:
                pass

            clean_desc = desc.replace("</p><p>", "\n").replace("<p>", "").replace("</p>", "\n")
            clean_desc = clean_desc.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
            clean_desc = re.sub(r'<[^>]+>', '', clean_desc).strip()

            embed = discord.Embed(
                title=title,
                color=0x00A2E8
            )
            embed.set_thumbnail(url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cbs_drill.png")
            if clean_desc:
                embed.add_field(name="演練內容", value=f"```text\n{clean_desc}\n```", inline=False)
            if start_time:
                embed.set_footer(text=f"發布日期 {start_time}")

            combined_text = f"{title} {clean_desc}".replace("臺", "台")
            is_national = "全台" in combined_text or "全國" in combined_text
            county_match = re.search(r'([\u4e00-\u9fa5]{2}[縣市])', combined_text)
            area_text = county_match.group(1) if county_match else ""
            if not area_text:
                is_national = True

            for guild_id, d in settings.items():
                global_silent = d.get('global_silent', False)
                cbs_alerts = d.get('cbs_alerts', {})
                if not isinstance(cbs_alerts, dict):
                    continue

                for loc_name, alert_info in cbs_alerts.items():
                    if not is_national and not is_location_matched(loc_name, area_text, combined_text, "systemtest"):
                        continue

                    receive_test = alert_info.get("receive_test", False) if isinstance(alert_info, dict) else False
                    allowed_types = alert_info.get("allowed_types", []) if isinstance(alert_info, dict) else []

                    # 檢查過濾條件：若指定了允許種類，需包含 "drillnews" 或 "systemtest"；若為全部接收模式，則需開啟 receive_test
                    if allowed_types and ("drillnews" not in allowed_types and "systemtest" not in allowed_types):
                        continue
                    if not allowed_types and not receive_test:
                        continue

                    ch_id = alert_info.get("channel_id") if isinstance(alert_info, dict) else alert_info
                    if not ch_id or isinstance(ch_id, bool):
                        continue
                    try:
                        ch_id_int = int(ch_id)
                    except (ValueError, TypeError):
                        continue
                    channel = self.bot.get_channel(ch_id_int)
                    if not channel:
                        continue

                    try:
                        content = "📋 災防告警演習預告"
                        mention_role_id = d.get('cbs_mention_role_id')
                        if mention_role_id:
                            content += f" <@&{mention_role_id}>"
                        if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                            logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通報至 {channel.name}")
                        else:
                            await channel.send(content=content, embed=embed, silent=global_silent)
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.info(f"📢 [CBS演習預告] 已發送至 {guild_name} ({channel.name}) - {title} (配對: {loc_name})")
                    except Exception as e:
                        logger.error(f"Failed to send CBS news alert to {ch_id}: {e!r}")

    @tasks.loop(time=NEWS_CHECK_TIMES)
    async def check_cbs_news_loop(self):
        if self.bot.is_closed() or not getattr(self.bot, 'session', None) or self.bot.session.closed:
            return

        try:
            settings = get_all_settings()
        except Exception as e:
            logger.error(f"Failed to load settings: {e!r}")
            return

        has_cbs_alerts = any('cbs_alerts' in d and d['cbs_alerts'] for d in settings.values())
        if not has_cbs_alerts:
            self.news_first_run_done = True
            return

        await self.check_cbs_news(settings)

    @check_cbs_news_loop.before_loop
    async def before_check_cbs_news(self):
        await self.bot.wait_until_ready()
        try:
            settings = get_all_settings()
            has_cbs_alerts = any('cbs_alerts' in d and d['cbs_alerts'] for d in settings.values())
            if has_cbs_alerts:
                await self.check_cbs_news(settings)
            else:
                self.news_first_run_done = True
        except Exception as e:
            logger.error(f"Failed initial CBS news check: {e!r}")

    @check_cbs_loop.before_loop
    async def before_check_cbs(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(CBSAlertCog(bot))
