"""
本模組負責從台灣災防告警系統 (cbs.tw) 定期抓取官方細胞廣播訊息。

一、 警報種類分類方法
1. 警報類型代碼 (alertType) 與代碼演進相容：
   系統支援 CBS 自 2024 年至今發布的 20+ 種告警類型，並針對官方系統跨年度的命名變更
   進行自動相容映射：
   - 防空警報 / 萬安演習：2024 年代碼為 `airraidalert`，2025+ 年簡化為 `airraid` (🚀)
   - 森林火災警戒：2024 年代碼為 `forestfire`，2025+ 年變更為 `wildfire` (🔥)
   - 暴潮特報：`stormsurge` / `surge` (🌊)
   - 系統演習測試：`systemtest` / `drill` (📢)
   - 地震速報：`earthquakeew` (🏚️)
   - 大雷雨即時訊息：`thunderstorm` (🌩️)
   - 土石流及大規模崩塌警戒：`debrisflow` (⛰️)
   - 水庫放流警戒：`reservoirdis` (🚰)
   - 堰塞湖警戒：`barrierlake` (🏞️)
   - 疏散避難：`evacuation` (🏃)
   - 道路封閉：`roadclose` (⛔)
   - 颱風強風告警：`hurricfrcwnd` (🌀)
   - 海嘯警報：`tsunami` (🌊)
   - 緊急避難：`emergalert` (🚨)
   - 水庫放水：`electric` (⚡)
   - 核子事故演練：`nuclear` (☢️)
   - 空氣品質指標：`airquality` (😷)
   - 通訊中斷：`commdisrupt` (📶)
   - 天氣警特報：低溫 `coldsurge` (❄️)、濃霧 `fog` (🌫️)、強風 `gale` (💨)、
               豪雨 `rainstorm` (🌧️)、火山 `volcano` (🌋)、暴雨 `flashflood` (🌊)

2. KML 座標解析與時效跳過策略
   - 緊急/縣市級告警直接跳過 KML (`skip_kml_types`)：
     地震速報 (`earthquakeew`)、防空演習 (`airraid`/`airraidalert`)、海嘯警報 (`tsunami`)、
     低溫特報 (`coldsurge`)、濃霧特報 (`fog`)、強風特報 (`gale`) 等，由於有高時效要求
     或涵蓋整個縣市，直接跳過 KML 查詢。
   - KML 查詢時機：
     - 鄉鎮市區局部告警（包含 `(共N個...` 或 `等共N鄉鎮區`）：官方文字未列齊具體鄉鎮，調用
       KML 邊界與國土測繪中心座標查出具體的鄉鎮市區。
     - 僅有代號（如水庫放水「發布區域1」）或溪流名稱（如山區暴雨「屏東縣沙漠溪」）：調用
       KML 補充實際受影響鄉鎮。
     - 全縣市發布（`is_county_wide_issuance == True`）：官方已明確指定全縣市，嚴禁調用
       KML 展開為全縣市鄉鎮。

二、 視覺排版優化 (`format_alert_areas`)
依照 5 大排版規則進行排版：

1. 原始文字處理
   - 去除行政代碼與系統標籤：過濾 `(6800500)`、`(63)`、`Test_Geocode`、`地震速報廣播範圍` 等。
   - 解構括號鄉鎮組合：如 `彰化縣(二林鎮 埤頭鄉 芳苑鄉等共6鄉鎮區)` 或 `嘉義市(東區 西區)`，
     自動拆解出具體鄉鎮並補上所屬縣市名。若為純統整 `(共N個鄉鎮)` 則保留所屬縣市。
   - 解構空白分隔清單：如 `宜蘭縣南澳鄉 花蓮縣秀林鄉`，依空格分割為獨立受影響行政區。
   - 大分區與沿海標籤過濾：自動剔除 `北部`、`中部`、`南部`、`東部` 以及 `*沿海地區`（如 `東北沿海地區`）。
   - 離島簡稱轉換：如 `金門` -> `金門縣`、`澎湖` -> `澎湖縣`、`馬祖` -> `連江縣`。
   - 空品測站轉換：如 `頭份測站` -> `苗栗縣頭份市`，透過鄉鎮反查字典自動對齊行政區。

2. 排版規則
   - 【規則 1】過濾分區名：
     不顯示方位名（如「北部」），只保留具體縣市與鄉鎮市區。
   - 【規則 2】純縣市名（多縣市）排版：
     當發布範圍全為純縣市名時，每行最多顯示 4 個縣市並附帶頓號 `、`。
     若涵蓋全台 22 縣市，自動簡化為「全台灣所有縣市」。
     範例：
       臺北市、新北市、新竹市、新竹縣、
       苗栗縣、臺中市、南投縣、彰化縣、
       雲林縣、嘉義市、嘉義縣、臺南市、
       高雄市、屏東縣、宜蘭縣、花蓮縣、
       臺東縣
   - 【規則 3】包含鄉鎮市區之排版與縮排對齊：
     格式為 `**縣市名**：鄉鎮1、鄉鎮2...`。
     每行最多顯示 4 個鄉鎮市區，換行時以 4 個全形空格（`　　　　`）精準對齊首行冒號後。
     單一鄉鎮發布時亦依同規則標註所屬縣市（如 `**高雄市**：六龜區`）。
     範例：
       **花蓮縣**：鳳林鎮、萬榮鄉、光復鄉、壽豐鄉、
       　　　　秀林鄉、吉安鄉
   - 【規則 4】全區縣市 + 鄉鎮縣市組合排版：
     若告警同時包含 A 縣市全區與 B 縣市部分鄉鎮，全區縣市自動標註為「所有行政區」。
     範例：
       **嘉義縣**：所有行政區
       **臺南市**：東山區、白河區
   - 【規則 5】單一縣市全區發布：
     僅單一縣市全區受影響時，直接顯示純縣市名（如 `宜蘭縣`）。


三、 全區辨識與訂閱地區精確配對
1. 全區發布辨識 (`is_county_wide_issuance`)：
   - 排除系統佔位符與分區名後，檢驗所有項目是否皆為合法縣市名且不帶任何括號。
   - 確保如 0403 花蓮大地震等重大速報被正確識別為全區廣播，避免誤觸鄉鎮展開機制。

2. 智慧訂閱配對 (`is_location_matched`)：
   - 支援「全台接收」、全縣市廣播、特定縣市訂閱與個別鄉鎮市區精確訂閱。
   - 防止如 `屏東縣(共6個鄉鎮)` 或 `彰化縣(二林鎮...)` 因字串包含縣市名而誤判定為全縣市發布。
=============================================================================
"""

import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import json
import re
import ssl
import xml.etree.ElementTree as ET
from collections import OrderedDict
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

TAIWAN_COUNTIES = {
    "基隆市", "台北市", "新北市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "台中市",
    "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣", "台南市", "高雄市", "屏東縣",
    "宜蘭縣", "花蓮縣", "台東縣", "澎湖縣", "金門縣", "連江縣"
}

TAIWAN_REGIONS = {"北部", "中部", "南部", "東部", "東北部", "東南部", "中南部"}

COUNTY_ALIASES = {
    "金門": "金門縣",
    "澎湖": "澎湖縣",
    "馬祖": "連江縣",
}

def is_county_wide_issuance(area_text: str) -> bool:
    """判斷是否為全縣市發布（如「發布區域1,臺東縣」），即排除系統代號與大分區標籤後皆為純縣市名稱且無 (共N個)"""
    if not area_text:
        return False
    # 先去除行政代碼如 (6800500) 或 (65)
    clean = re.sub(r'\s*\(\d+\)', '', area_text)
    # 如果還包含括號 (如 (共4個) 或 (東區 西區) 或 (等共N...))，代表非全區發布
    if '(' in clean or '（' in clean:
        return False
    clean = clean.replace("臺", "台")
    tokens = [t.strip() for t in re.split(r'[,，、\s]+', clean) if t.strip()]
    meaningful = [t for t in tokens if not re.match(r'^(?:發布區域\d*|特定區域|Test_Geocode|地震速報廣播範圍|北部|中部|南部|東部|東北部|東南部|中南部|.*沿海地區)$', t) and t.lower() != 'none']
    if not meaningful:
        return False
    for t in meaningful:
        t_clean = re.sub(r'及其沿海$', '', t).strip()
        t_clean = re.sub(r'沿海$', '', t_clean).strip()
        t_clean = COUNTY_ALIASES.get(t_clean, t_clean)
        if t_clean not in TAIWAN_COUNTIES:
            return False
    return True

def format_alert_areas(areas_list: list[str], town_mapping: dict = None) -> str:
    """依照視覺微調排版規範格式化影響區域"""
    expanded_items = []
    for raw in areas_list:
        if not raw:
            continue
        raw = re.sub(r'\s*\(\d+\)$', '', raw).strip()
        if not raw:
            continue
        # 處理括號格式：如 彰化縣(二林鎮 埤頭鄉 芳苑鄉等共6鄉鎮區) 或 嘉義市(東區 西區) 或 屏東縣(共6個鄉鎮)
        m = re.match(r'^([\u4e00-\u9fa5]{2,3}(?:縣|市))\((.*?)\)$', raw)
        if m:
            county = m.group(1)
            inner = m.group(2).strip()
            if re.match(r'^(?:等?共\d+個?.*)$', inner):
                expanded_items.append(county)
            else:
                inner_clean = re.sub(r'等?共\d+[^)]*$', '', inner).strip()
                sub_towns = [tw.strip() for tw in inner_clean.split() if tw.strip()]
                if sub_towns:
                    for tw in sub_towns:
                        expanded_items.append(f"{county}{tw}")
                else:
                    expanded_items.append(county)
            continue
        
        # 處理空格分隔之多個縣市或鄉鎮：如 '宜蘭縣蘇澳鎮 宜蘭縣南澳鄉 花蓮縣秀林鄉'
        if ' ' in raw:
            parts = [p.strip() for p in raw.split() if p.strip()]
            expanded_items.extend(parts)
        else:
            expanded_items.append(raw)

    filtered = []
    for a in expanded_items:
        a_clean = re.sub(r'\(共\d+個[^)]*\)', '', a).strip()
        a_clean = re.sub(r'\s*\(\d+\)', '', a_clean).strip()
        if not a_clean or a_clean.lower() == 'none':
            continue
        if re.match(r'^(?:發布區域\d*|特定區域|Test_Geocode|地震速報廣播範圍)$', a_clean):
            continue
        if a_clean in TAIWAN_REGIONS or a_clean.endswith('沿海地區'):
            continue
            
        # 離島簡稱正規化（如「金門」->「金門縣」、「馬祖」->「連江縣」）
        if a_clean in COUNTY_ALIASES:
            a_clean = COUNTY_ALIASES[a_clean]

        # 空品測站正規化（如「頭份測站」->「苗栗縣頭份市」）
        if a_clean.endswith('測站') and len(a_clean) > 2:
            st_name = a_clean[:-2]
            if town_mapping and st_name in town_mapping:
                combos = town_mapping[st_name]
                if combos:
                    a_clean = combos[0][0]

        if a_clean not in filtered:
            filtered.append(a_clean)

    if not filtered:
        return "特定區域"

    county_map = OrderedDict()
    other_items = []

    for item in filtered:
        clean_item = item.replace('臺', '台')
        # 1. 純縣市
        if clean_item in TAIWAN_COUNTIES:
            c = item
            if c not in county_map:
                county_map[c] = {'all': True, 'towns': []}
            else:
                county_map[c]['all'] = True
        # 2. 縣市 + 鄉鎮市區/溪流
        elif len(item) > 3 and item[:3].replace('臺', '台') in TAIWAN_COUNTIES:
            c = item[:3]
            sub = item[3:]
            if c not in county_map:
                county_map[c] = {'all': False, 'towns': []}
            if sub and sub not in county_map[c]['towns']:
                county_map[c]['towns'].append(sub)
        else:
            # 嘗試由 town_mapping 解析所屬縣市
            matched_c = None
            if town_mapping and item in town_mapping:
                combos = town_mapping[item]
                for fullname, *_ in combos:
                    fn_c = fullname[:3]
                    if fn_c in county_map:
                        matched_c = fn_c
                        break
                if not matched_c and len(combos) == 1:
                    matched_c = combos[0][0][:3]
            if matched_c:
                if matched_c not in county_map:
                    county_map[matched_c] = {'all': False, 'towns': []}
                if item not in county_map[matched_c]['towns']:
                    county_map[matched_c]['towns'].append(item)
            else:
                other_items.append(item)

    # 規則 5：單一一個縣市全區的狀況
    if len(county_map) == 1 and not other_items:
        c, info = next(iter(county_map.items()))
        if info['all'] and not info['towns']:
            return c

    # 規則 2：如果只有「純縣市名」（且無鄉鎮、無其他項目）
    all_pure_counties = bool(county_map) and not other_items and all(info['all'] and not info['towns'] for info in county_map.values())
    if all_pure_counties:
        counties = list(county_map.keys())
        if len(counties) == 22:
            return "全台灣所有縣市"
        lines = []
        for i in range(0, len(counties), 4):
            chunk = counties[i:i+4]
            line = "、".join(chunk)
            if i + 4 < len(counties):
                line += "、"
            lines.append(line)
        return "\n".join(lines)

    # 規則 3 & 4：包含鄉鎮市區，或 A 縣市全區 + B 縣市鄉鎮
    result_blocks = []
    for c, info in county_map.items():
        if info['all'] and not info['towns']:
            result_blocks.append(f"**{c}**：所有行政區")
        elif info['towns']:
            towns = info['towns']
            indent = "　" * (len(c) + 1)
            lines = []
            for i in range(0, len(towns), 4):
                chunk = towns[i:i+4]
                line = "、".join(chunk)
                if i + 4 < len(towns):
                    line += "、"
                if i == 0:
                    lines.append(f"**{c}**：{line}")
                else:
                    lines.append(f"{indent}{line}")
            result_blocks.append("\n".join(lines))
        else:
            result_blocks.append(c)

    if other_items:
        result_blocks.append("、".join(other_items))

    return "\n".join(result_blocks)

def is_location_matched(loc_name: str, area_text: str, combined_text: str, alert_type: str) -> bool:
    loc_name_clean = loc_name.replace("臺", "台")
    area_text_clean = area_text.replace("臺", "台") if area_text else ""
    combined_text_clean = combined_text.replace("臺", "台") if combined_text else ""
    
    if loc_name_clean == "全台接收":
        return True
        
    # 若為全國性或全台告警，所有訂閱地區皆匹配
    if "全台" in area_text_clean or "全國" in area_text_clean:
        return True
    if sum(1 for c in TAIWAN_COUNTIES if c in area_text_clean) >= 20:
        return True
        
    county = loc_name_clean[:3]
    town = loc_name_clean[3:]
    
    # 1. 判斷是否為縣市級的警報 (area_text 中只有縣市，沒有特別指定到鄉鎮)
    # 若為雷雨即時訊息或局部鄉鎮告警 (共N個)，不可因統整名稱觸發 is_county_wide
    is_county_wide = False
    if alert_type != "thunderstorm":
        tokens = [t.strip() for t in re.split(r'[,，、\s]+', area_text_clean) if t.strip()]
        for t in tokens:
            if '(' in t or '（' in t:
                continue
            # 去除結尾可能的行政代碼或 (共X個市區) 等字眼
            t_clean = re.sub(r'\s*\(\d+\)', '', t).strip()
            t_clean = re.sub(r'及其沿海$', '', t_clean).strip()
            t_clean = re.sub(r'沿海$', '', t_clean).strip()
            t_clean = COUNTY_ALIASES.get(t_clean, t_clean)
            
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
        self.town_mapping = {}
        try:
            self.town_mapping = load_town_mapping()
            for combos in self.town_mapping.values():
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
        
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            async with self.bot.session.get(kml_url, headers=headers, timeout=aiohttp.ClientTimeout(total=4)) as resp:
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
                
            rings = []
            for elem in coords_elements:
                if not elem.text:
                    continue
                block_pts = []
                for token in elem.text.strip().split():
                    parts = token.split(',')
                    if len(parts) >= 2:
                        try:
                            lon, lat = float(parts[0]), float(parts[1])
                            block_pts.append((lon, lat))
                        except ValueError:
                            pass
                if not block_pts:
                    continue

                # 移除重複的閉合點（最後一點與第一點相同時）
                ring = block_pts[:-1] if len(block_pts) > 1 and block_pts[0] == block_pts[-1] else block_pts
                if ring and ring not in rings:
                    rings.append(ring)

            if not rings:
                return []

            sample_pts = []

            # 第一階段：優先收集每一個獨立多邊形（如各島嶼、不同區域）的幾何中心點
            for ring in rings:
                avg_lon = sum(p[0] for p in ring) / len(ring)
                avg_lat = sum(p[1] for p in ring) / len(ring)
                pt = (avg_lon, avg_lat)
                if pt not in sample_pts:
                    sample_pts.append(pt)
                if len(sample_pts) >= 16:
                    break

            # 第二階段：若取樣點未滿 16 點，依序從各多邊形均勻補充代表頂點
            if len(sample_pts) < 16:
                for ring in rings:
                    n = len(ring)
                    if n <= 1:
                        continue
                    step = max(1, n // 4)
                    for i in range(0, n, step):
                        pt = ring[i]
                        if pt not in sample_pts:
                            sample_pts.append(pt)
                        if len(sample_pts) >= 16:
                            break
                    if len(sample_pts) >= 16:
                        break

            # 第三階段：若仍有餘裕，補充各邊中點
            if len(sample_pts) < 16:
                for ring in rings:
                    n = len(ring)
                    for i in range(n):
                        p1 = ring[i]
                        p2 = ring[(i + 1) % n]
                        mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
                        if mid not in sample_pts:
                            sample_pts.append(mid)
                        if len(sample_pts) >= 16:
                            break
                    if len(sample_pts) >= 16:
                        break
                    
            if not sample_pts:
                return []
                
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            
            async def query_point(lon, lat):
                nlsc_url = f"https://api.nlsc.gov.tw/other/TownVillagePointQuery/{lon:.6f}/{lat:.6f}"
                try:
                    async with self.bot.session.get(nlsc_url, headers=headers, ssl=ssl_ctx, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        if resp.status == 200:
                            nlsc_xml = await resp.text()
                            n_root = ET.fromstring(nlsc_xml)
                            cty = n_root.findtext('ctyName', '').strip()
                            town = n_root.findtext('townName', '').strip()
                            if cty and town:
                                return f"{cty}{town}"
                except Exception as e:
                    logger.debug(f"NLSC query failed for ({lon}, {lat}): {e!r}")
                return None

            # 平行查詢前 16 個取樣點
            results = await asyncio.gather(*(query_point(lon, lat) for lon, lat in sample_pts[:16]))
            towns = []
            for t in results:
                if t and t not in towns:
                    towns.append(t)
                    
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
            
            is_test = alert_type == "systemtest" or any(kw in topic or kw in cmam_text for kw in ["測試", "演練", "演習", "TEST", "test"])
            
            # 檢查 area_text 中包含的縣市與鄉鎮市區數量
            area_text_clean = area_text.replace("臺", "台")
            matched_counties = [c for c in TAIWAN_COUNTIES if c in area_text_clean]
            is_multi_county = len(matched_counties) >= 2
            has_specific_town = any(town in area_text for town in self.valid_towns) if hasattr(self, 'valid_towns') else False
            
            is_thunderstorm = alert_type == "thunderstorm" or "雷雨" in topic
            is_county_wide = is_county_wide_issuance(area_text)
            
            # 判斷是否需要透過 KML 補充或解析鄉鎮市區：
            # 1. 若為全縣市發布（如「發布區域1,臺東縣」），官方已明確指定整個縣市，不透過 KML 展開成所有個別鄉鎮
            # 2. 局部統整型告警（包含「(共N個...」）：官方未列出具體鄉鎮，需調用 KML 精確替換為具體鄉鎮
            # 3. 官方文字僅有代號（如水庫放流「發布區域1」）或有具體鄉鎮列舉伴隨代號（如核安演習補充石門區）需調用 KML 補充
            # 4. 原文字無具體鄉鎮且非多縣市/測試廣域告警（例如山區暴雨「屏東縣沙漠溪」補充鄉鎮）
            needs_kml_supplement = not is_county_wide and bool("發布區域" in area_text or re.search(r'\(.*共\d+', area_text))
            needs_kml_river = not is_county_wide and not has_specific_town and not is_multi_county and not is_test
            
            # 廣域/全國性告警、極高時效警報跳過 KML
            skip_kml_types = {"earthquakeew", "airraidalert", "airraid", "tsunami", "commdisrupt", "systemtest", "coldsurge", "fog", "gale", "volcano"}
            if page_key and alert_type not in skip_kml_types and not is_county_wide and (is_thunderstorm or needs_kml_supplement or needs_kml_river):
                kml_towns = await self.fetch_locations_from_kml(page_key)
                if kml_towns:
                    has_gong = bool(re.search(r'\(.*共\d+', area_text))
                    if is_thunderstorm or has_gong:
                        # 局部統整型告警（如雷雨即時訊息「臺南市(共4個鄉鎮)」），改以 KML 解析出之具體鄉鎮作為影響區域
                        area_text = "、".join(kml_towns)
                    elif not matched_counties:
                        area_text = "、".join(kml_towns)
                    else:
                        area_text = f"{area_text}、{'、'.join(kml_towns)}"
                    logger.info(f"📍 [CBS預警] 成功經由 KML 精確定位/補充: {area_text} ({page_key})")
            
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
            elif alert_type in ("airraidalert", "airraid"): emoji = "🚀"
            elif alert_type == "barrierlake": emoji = "🏞️"
            elif alert_type == "commdisrupt": emoji = "📶"
            elif alert_type == "debrisflow": emoji = "⛰️"
            elif alert_type == "earthquakeew": emoji = "🏚️"
            elif alert_type == "electric": emoji = "⚡"
            elif alert_type == "emergalert": emoji = "🚨"
            elif alert_type == "evacuation": emoji = "🏃"
            elif alert_type == "flood": emoji = "🌊"
            elif alert_type in ("forestfire", "wildfire"): emoji = "🔥"
            elif alert_type == "hurricfrcwnd": emoji = "🌀"
            elif alert_type == "nuclear": emoji = "☢️"
            elif alert_type == "reservoirdis": emoji = "🚰"
            elif alert_type == "roadclose": emoji = "⛔"
            elif alert_type in ("stormsurge", "surge"): emoji = "🌊"
            elif alert_type in ("systemtest", "drill"): emoji = "📢"
            elif alert_type == "thunderstorm": emoji = "🌩️"
            elif alert_type == "tsunami": emoji = "🌊"
            elif alert_type == "largesurf": emoji = "🌊"
            elif alert_type == "coldsurge": emoji = "❄️"
            elif alert_type == "fog": emoji = "🌫️"
            elif alert_type == "gale": emoji = "💨"
            elif alert_type == "rainstorm": emoji = "🌧️"
            elif alert_type == "volcano": emoji = "🌋"
            elif alert_type == "flashflood": emoji = "🌊"
            
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
                    
            # 若已有具體行政區或溪流名稱，移除籠統的「特定區域」標籤
            if len(areas) > 1 and "特定區域" in areas:
                areas.remove("特定區域")
                    
            if cmam_text and hasattr(self, 'valid_towns') and not is_test and not is_thunderstorm and len(areas) < 10:
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
                            
            formatted_area = format_alert_areas(areas, getattr(self, 'town_mapping', None))
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
            
            # 過濾掉名稱中包含「X山區」的行政區（如中山區、岡山區等），避免誤判為山區警報
            mountain_district_pattern = r'(?:中山|松山|文山|泰山|金山|龜山|香山|東山|鼓山|鳳山|岡山|旗山)區'
            clean_cmam_text = re.sub(mountain_district_pattern, '', cmam_text)
            clean_area_text = re.sub(mountain_district_pattern, '', area_text)
            clean_topic = re.sub(mountain_district_pattern, '', topic)
            is_mountain = "山區暴雨" in topic or "山區" in clean_cmam_text or "山區" in clean_area_text or "山區" in clean_topic
            
            sent_cnt = 0
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
                            sent_cnt += 1
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.debug(f"📢 [CBS預警] 已發送至 {guild_name} ({channel.name}) - {topic} (配對: {loc_name})")
                    except Exception as e:
                        logger.error(f"Failed to send CBS alert to {ch_id}: {e!r}")

            if sent_cnt > 0:
                logger.info(f"📢 [CBS預警] 廣播完成 ({topic}) | 共發送 {sent_cnt} 個頻道")

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

            sent_cnt = 0
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
                            sent_cnt += 1
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.debug(f"📢 [CBS演習預告] 已發送至 {guild_name} ({channel.name}) - {title} (配對: {loc_name})")
                    except Exception as e:
                        logger.error(f"Failed to send CBS news alert to {ch_id}: {e!r}")
            
            if sent_cnt > 0:
                logger.info(f"📢 [CBS演習預告] 廣播完成 ({title}) | 共發送 {sent_cnt} 個頻道")

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
