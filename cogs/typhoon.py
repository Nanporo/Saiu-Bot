import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import zipfile
import io
import asyncio
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timezone, timedelta
import logging


logger = logging.getLogger(__name__)

# 讀取設定檔
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    CWA_API_KEY = config.get('CWA_API_KEY', '')
except Exception:
    CWA_API_KEY = ''

# 定義台灣各縣市的大致中心座標 (經度, 緯度)
TAIWAN_CITIES = {
    "基隆市": (121.7419, 25.1276),
    "臺北市": (121.5654, 25.0329),
    "新北市": (121.4628, 25.0112),
    "桃園市": (121.3009, 24.9936),
    "新竹縣": (121.0177, 24.8270),
    "新竹市": (120.9675, 24.8138),
    "苗栗縣": (120.8151, 24.5601),
    "臺中市": (120.6736, 24.1477),
    "彰化縣": (120.5393, 24.0517),
    "南投縣": (120.9718, 23.8387),
    "雲林縣": (120.4690, 23.7092),
    "嘉義縣": (120.5750, 23.4518),
    "嘉義市": (120.4491, 23.4800),
    "臺南市": (120.2093, 22.9997),
    "高雄市": (120.3120, 22.6272),
    "屏東縣": (120.4879, 22.6714),
    "宜蘭縣": (121.7535, 24.7000),
    "花蓮縣": (121.5757, 23.9871),
    "臺東縣": (121.1444, 22.7583),
    "澎湖縣": (119.5664, 23.5711),
    "金門縣": (118.3206, 24.4327),
    "連江縣": (119.9362, 26.1505)
}

# 射線法判斷點是否在多邊形內
def point_in_polygon(x, y, polygon):
    n = len(polygon)
    inside = False
    if n == 0: return False
    p1x, p1y = polygon[0]
    for i in range(n + 1):
        p2x, p2y = polygon[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xints = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xints:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside

# 解析 KML 內容
def parse_kml(kml_content):
    root = ET.fromstring(kml_content)
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    
    desc_elem = root.find('.//kml:Folder/kml:description', ns)
    valid_time = desc_elem.text.strip() if desc_elem is not None else "未知時間"
    
    polygons_by_prob = {}
    for placemark in root.findall('.//kml:Placemark', ns):
        name_elem = placemark.find('kml:name', ns)
        name = name_elem.text if name_elem is not None else "0%"
        poly_elem = placemark.find('.//kml:Polygon', ns)
        if poly_elem is None: continue
        
        outer_elem = poly_elem.find('.//kml:outerBoundaryIs/kml:LinearRing/kml:coordinates', ns)
        if outer_elem is None: continue
        
        outer_coords = []
        for line in outer_elem.text.strip().split():
            parts = line.split(',')
            if len(parts) >= 2:
                outer_coords.append((float(parts[0]), float(parts[1])))
        
        inners = []
        for inner_elem in poly_elem.findall('.//kml:innerBoundaryIs/kml:LinearRing/kml:coordinates', ns):
            inner_coords = []
            for line in inner_elem.text.strip().split():
                parts = line.split(',')
                if len(parts) >= 2:
                    inner_coords.append((float(parts[0]), float(parts[1])))
            inners.append(inner_coords)
            
        if name not in polygons_by_prob:
            polygons_by_prob[name] = []
        polygons_by_prob[name].append({'outer': outer_coords, 'inners': inners})
        
    return polygons_by_prob, valid_time

# 獲取並處理氣象署的 KMZ 檔案
async def fetch_typhoon_data(session):
    url = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-003?downloadType=WEB&format=KMZ"
    headers = {"Authorization": CWA_API_KEY}
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200: return None, None
            data = await resp.read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            kml_name = next((f for f in z.namelist() if f.endswith('.kml')), None)
            if not kml_name: return None, None
            kml_content = z.read(kml_name)
        return parse_kml(kml_content)
    except Exception as e:
        logger.warning(f"⚠️ [警告] 獲取颱風侵襲機率失敗: {e!r}")
        return None, None

# 獲取颱風路徑圖
async def fetch_typhoon_image(session):
    now = datetime.now(timezone(timedelta(hours=8)))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": "https://www.cwa.gov.tw/V8/C/P/Typhoon/TY_WARN.html"
    }

    from modules.cache_manager import load_cache, save_cache
    cache = load_cache()
    cached_ty_url = cache.get("typhoon_image_url")

    async def check_url(time_str, offset):
        url = f"https://www.cwa.gov.tw/Data/typhoon/TY_NEWS/PTA_{time_str}-{offset}_zhtw.png"
        try:
            async with session.head(url, headers=headers, timeout=3) as resp:
                if resp.status == 200:
                    logger.info(f"🌐 [圖片測試] 找到可用颱風路徑圖: {url}")
                    return offset, url
        except Exception:
            pass
        return -1, None

    check_time = now
    for _ in range(8):  # 往回找最多 48 小時
        hour = (check_time.hour // 6) * 6
        dt = check_time.replace(hour=hour, minute=0, second=0, microsecond=0)
        time_str = dt.strftime("%Y%m%d%H%M")
        
        # 如果這個時間點剛好與我們快取紀錄的網址吻合，直接嘗試下載快取的最佳倍數網址
        if cached_ty_url and f"PTA_{time_str}-" in cached_ty_url:
            logger.info(f"⬇️ [抓取狀態] 發現快取符合的颱風路徑圖: {cached_ty_url}")
            try:
                async with session.get(cached_ty_url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if len(data) > 1000:
                            logger.info(f"✅ [抓取狀態] 快取颱風路徑圖下載成功 ({len(data)/1024:.1f} KB)")
                            return data, cached_ty_url
            except Exception as e:
                logger.error(f"❌ [抓取狀態] 快取颱風路徑圖下載失敗: {e!r}")
                pass
        
        logger.info(f"🔍 [抓取狀態] 正在非同步檢查颱風時間點: {time_str}")
        offsets = list(range(120, -1, -12))
        results = await asyncio.gather(*(check_url(time_str, o) for o in offsets))
        
        valid_results = [r for r in results if r[0] != -1]
        if valid_results:
            valid_results.sort(key=lambda x: x[0], reverse=True)
            best_offset, best_url = valid_results[0]
            
            logger.info(f"⬇️ [抓取狀態] 準備下載最高倍數 ({best_offset}) 的路徑圖: {best_url}")
            try:
                async with session.get(best_url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if len(data) > 1000:
                            logger.info(f"✅ [抓取狀態] 颱風路徑圖下載成功 ({len(data)/1024:.1f} KB)")
                            cache["typhoon_image_url"] = best_url
                            save_cache(cache)
                            return data, best_url
            except Exception as e:
                logger.error(f"❌ [抓取狀態] 颱風路徑圖下載失敗: {e!r}")
                pass
                
        check_time -= timedelta(hours=6)
        
    logger.info("⚠️ [抓取狀態] 掃描完成，未在 48 小時內找到任何颱風路徑圖。")
        
    return None, None

# 獲取颱風警報 (CAP)
async def fetch_typhoon_warning(session):
    url = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-001?downloadType=WEB&format=CAP"
    headers = {"Authorization": CWA_API_KEY}
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200: return None
            xml_content = await resp.read()
            
        root = ET.fromstring(xml_content)
        ns = {'cap': 'urn:oasis:names:tc:emergency:cap:1.2'}
        
        msg_type = root.find('.//cap:msgType', ns)
        if msg_type is not None and msg_type.text == 'Cancel':
            return None
            
        info = root.find('.//cap:info', ns)
        if info is None: return None
        
        headline = info.find('cap:headline', ns)
        headline_text = headline.text if headline is not None else ""
        if "解除" in headline_text:
            return None
            
        effective = info.find('cap:effective', ns)
        effective_time = effective.text if effective is not None else "未知時間"
        
        areas = []
        for area in info.findall('cap:area', ns):
            area_desc = area.find('cap:areaDesc', ns)
            if area_desc is not None and area_desc.text:
                areas.append(area_desc.text)
                
        description = info.find('cap:description', ns)
        desc_text = ""
        if description is not None:
            sections = description.findall('.//cap:section', ns)
            if sections:
                desc_text = "\n\n".join([f"**{s.get('title', '')}**\n{s.text}" for s in sections if s.text])
            else:
                desc_text = "".join(description.itertext()).strip()
                
        return {
            "headline": headline_text,
            "effective": effective_time,
            "areas": areas,
            "description": desc_text
        }
    except Exception as e:
        logger.warning(f"⚠️ [警告] 獲取颱風警報失敗: {e!r}")
        return None
# 獲取海水表面溫度與海洋熱潛勢
async def fetch_sea_images(session):
    now = datetime.now(timezone(timedelta(hours=8)))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    
    from modules.cache_manager import load_cache, save_cache
    cache = load_cache()

    async def get_sst():
        cached_sst_date = cache.get("sst_latest_date")
        for i in range(7):
            dt = now - timedelta(days=i)
            date_str = dt.strftime("%Y%m%d")
            url = f"https://www.cwa.gov.tw/Data/mursst/mursst_{date_str}_WWW-contour.png"
            
            if date_str == cached_sst_date:
                logger.info(f"🌊 [海洋] 使用快取的海水表面溫度圖片日期: {date_str}")
                return url, dt
                
            try:
                async with session.head(url, headers=headers, timeout=3) as resp:
                    if resp.status == 200:
                        logger.info(f"🌊 [海洋] 成功找到新海水表面溫度圖片: {date_str}")
                        cache["sst_latest_date"] = date_str
                        save_cache(cache)
                        return url, dt
            except Exception:
                pass
        logger.warning("⚠️ [警告] 無法找到近期海水表面溫度圖片")
        return None

    async def get_tchp():
        cached_tchp_date = cache.get("tchp_latest_date")
        for i in range(7):
            dt = now - timedelta(days=i)
            date_str = dt.strftime("%Y-%m-%d")
            url = f"https://www.cwa.gov.tw/Data/TCHP/{date_str}_TCHP_ostia.png"
            
            if date_str == cached_tchp_date:
                logger.info(f"🌊 [海洋] 使用快取的海洋熱潛勢圖片日期: {date_str}")
                return url, dt
                
            try:
                async with session.head(url, headers=headers, timeout=3) as resp:
                    if resp.status == 200:
                        logger.info(f"🌊 [海洋] 成功找到新海洋熱潛勢圖片: {date_str}")
                        cache["tchp_latest_date"] = date_str
                        save_cache(cache)
                        return url, dt
            except Exception:
                pass
        logger.warning("⚠️ [警告] 無法找到近期海洋熱潛勢圖片")
        return None

    sst_res, tchp_res = await asyncio.gather(get_sst(), get_tchp())
    sst_url, sst_dt = sst_res if sst_res else (None, None)
    tchp_url, tchp_dt = tchp_res if tchp_res else (None, None)
    
    async def download(url):
        if not url: return None
        try:
            cache_buster = f"?T={now.strftime('%Y%m%d%H')}-0"
            async with session.get(url + cache_buster, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception:
            pass
        return None
        
    sst_bytes, tchp_bytes = await asyncio.gather(download(sst_url), download(tchp_url))
    
    sst_time = f"<t:{int(sst_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())}:f>" if sst_dt else "未知時間"
    tchp_time = f"<t:{int(tchp_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())}:f>" if tchp_dt else "未知時間"
    
    return (sst_bytes, sst_time), (tchp_bytes, tchp_time)


# 解析各縣市侵襲機率
def get_typhoon_probabilities(polygons_by_prob):
    def extract_num(s):
        return int(''.join(filter(str.isdigit, s))) if any(c.isdigit() for c in s) else 0

    sorted_probs = sorted(polygons_by_prob.keys(), key=extract_num, reverse=True)
    
    results = []
    has_typhoon = False
    for city, (lon, lat) in TAIWAN_CITIES.items():
        prob_found = 0
        for prob in sorted_probs:
            for poly in polygons_by_prob[prob]:
                if point_in_polygon(lon, lat, poly['outer']):
                    in_hole = any(point_in_polygon(lon, lat, inner) for inner in poly['inners'])
                    if not in_hole:
                        prob_found = extract_num(prob)
                        break
            if prob_found > 0:
                has_typhoon = True
                break
        
        results.append({"county": city, "prob": prob_found})

    if not has_typhoon:
        return []

    # 不再依機率排序，維持 TAIWAN_CITIES 的地理順序
    return results


async def fetch_typhoon_overview(session):
    url = "https://www.cwa.gov.tw/Data/js/typhoon/TY_NEWS-Data.js"
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return []
            js_content = await response.text()
    except Exception as e:
        logger.warning(f"⚠️ [警告] 獲取颱風概覽失敗: {e!r}")
        return []

    import re
    typhoons = []
    
    match = re.search(r"TY_LIST_1\['C'\]\s*=\s*(.*?);", js_content, re.DOTALL)
    if not match: return []
    html_raw = match.group(1)
    html = re.sub(r"\'\s*\+\s*\'", "", html_raw)
    html = html.replace("''+", "").replace("'", "")

    # 解析 TY_LIST_2 來獲取發佈時間
    times_list = []
    match2 = re.search(r"TY_LIST_2\['C'\]\s*=\s*(.*?);", js_content, re.DOTALL)
    if match2:
        html2_raw = match2.group(1)
        html2 = re.sub(r"\'\s*\+\s*\'", "", html2_raw).replace("''+", "").replace("'", "")
        panels2 = re.split(r'<div class="panel panel-default"', html2)
        for p2 in panels2[1:]:
            time_str_parsed = "未知時間"
            t_match2 = re.search(r'<span class="now">現況</span>\s*<p>(.*?)</p>', p2)
            if t_match2:
                time_str = t_match2.group(1).strip()
                try:
                    from datetime import datetime, timezone, timedelta
                    dt = datetime.strptime(time_str, "%Y年%m月%d日%H時").replace(tzinfo=timezone(timedelta(hours=8)))
                    time_str_parsed = f"<t:{int(dt.timestamp())}:f>"
                except Exception:
                    time_str_parsed = time_str
            times_list.append(time_str_parsed)

    
    panels = re.split(r'<div class="panel panel-default"', html)
    for idx, panel in enumerate(panels[1:]):
        name_match = re.search(r'id=\"(.*?)\"', panel)
        intl_name = name_match.group(1) if name_match else ""
        
        title_match = re.search(r'<h4 class="panel-title">(.*?)</h4>', panel, re.DOTALL)
        title_text = re.sub(r'<[^>]+>', ' ', title_match.group(1)).strip() if title_match else ""
        
        name = ""
        number = ""
        td_number = ""
        is_td = False
        
        m_name = re.search(r'颱風\s*([^\s\(\)（）]+)', title_text)
        if m_name and "熱帶性低氣壓" not in title_text: 
            name = m_name.group(1)
        else:
            is_td = True
            m_td = re.search(r'原\s*([^\s\(\)（）]+)\s*颱風', title_text)
            if m_td:
                name = m_td.group(1)
            else:
                m_td2 = re.search(r'熱帶性低氣壓\s*([^\s\(\)（）]+)', title_text)
                if m_td2:
                    val = m_td2.group(1)
                    if not re.match(r'TD\d+', val, re.IGNORECASE):
                        name = val

        m_num = re.search(r'編號第\s*(\d+)\s*號', title_text)
        if m_num: number = m_num.group(1)
        
        m_td_num = re.search(r'(TD\d+)', title_text, re.IGNORECASE)
        if m_td_num: td_number = m_td_num.group(1).upper()
        
        m_type = re.search(r'((?:輕度|中度|強烈)?颱風|輕颱|中颱|強颱)', title_text)
        ty_type = m_type.group(1).strip() if m_type else "颱風"
        
        
        body_match = re.search(r'<div class="panel-body">(.*?)</div>', panel, re.DOTALL)
        if not body_match: continue
        
        body_html = body_match.group(1)
        typhoon_time = times_list[idx] if idx < len(times_list) else "未知時間"
        
        text = re.sub(r'<[^>]+>', ' ', body_html).strip()
        
        items = {}
        m_center = re.search(r'中心位置在(北緯\s*[\d\.]+\s*度.*?東經\s*[\d\.]+\s*度)', text)
        if m_center: items["center"] = m_center.group(1).replace('，', ',').strip()
        m_dir = re.search(r'向(\S+)進行', text)
        if m_dir: items["direction"] = m_dir.group(1)
        m_spd = re.search(r'每小時(\d+)公里速度', text)
        if m_spd: items["speed"] = m_spd.group(1) + " 公里"
        m_pres = re.search(r'中心氣壓\s*(\d+)\s*百帕', text)
        if m_pres: items["pressure"] = m_pres.group(1) + " 百帕"
        def format_wind(v_str):
            v = int(v_str)
            scale = round((v / 0.836) ** (2/3))
            if scale > 17:
                return f"17級以上 `{v} m/s`"
            return f"{scale}級 `{v} m/s`"

        m_wind = re.search(r'最大風速每秒\s*(\d+)\s*公尺', text)
        if m_wind: items["max_wind"] = format_wind(m_wind.group(1))
        m_gust = re.search(r'最大陣風每秒\s*(\d+)\s*公尺', text)
        if m_gust: items["max_gust"] = format_wind(m_gust.group(1))
        m_r7 = re.search(r'七級風平均暴風半徑\s*(\d+)\s*公里', text)
        if m_r7: items["radius_7"] = m_r7.group(1) + " 公里"
        m_r10 = re.search(r'十級風平均暴風半徑\s*(\d+)\s*公里', text)
        if m_r10: items["radius_10"] = m_r10.group(1) + " 公里"
        
        typhoons.append({
            "name": name,
            "number": number,
            "ty_type": ty_type,
            "td_number": td_number,
            "is_td": is_td,
            "intl_name": intl_name,
            "time": typhoon_time,
            "items": items
        })
    return typhoons


def build_overview_embed(typhoon):
    embed = discord.Embed(title="", color=0xe74c3c)
    
    number_str = f"第 {typhoon.get('number', '')} 號" if typhoon.get('number', '') else ""
    td_number = typhoon.get('td_number', '')
    is_td = typhoon.get('is_td', False)
    name_str = f"{typhoon.get('name', '')}" if typhoon.get('name', '') else "未知"
    intl_str = f"({typhoon.get('intl_name', '')})" if typhoon.get('intl_name', '') else ""
    ty_type = typhoon.get('ty_type', '颱風')
    
    if is_td:
        if typhoon.get('name', '') and typhoon.get('name', '') != "未知":
            desc = f"**熱帶性低氣壓 {td_number} (原{name_str}颱風)**\n"
        else:
            desc = f"**熱帶性低氣壓 {td_number}**\n"
        desc += f"發佈時間：{typhoon.get('time', '未知時間')}\n"
    else:
        if number_str:
            title_line = f"{number_str} {ty_type} {name_str} {intl_str}".strip()
        else:
            title_line = f"{ty_type} {name_str} {intl_str}".strip()
        desc = f"**{title_line}**\n發佈時間：{typhoon.get('time', '未知時間')}\n"
        
    embed.description = desc
    
    items = typhoon['items']
    if "center" in items: embed.add_field(name="📍 中心位置", value=items["center"].replace(',', '\n'), inline=True)
    if "direction" in items:
        dir_arrows = {
            "北": "↑", "北北東": "↗", "東北": "↗", "東北東": "↗",
            "東": "→", "東南東": "↘", "東南": "↘", "南南東": "↘",
            "南": "↓", "南南西": "↙", "西南": "↙", "西南西": "↙",
            "西": "←", "西北西": "↖", "西北": "↖", "北北西": "↖"
        }
        arrow = dir_arrows.get(items["direction"].replace('風', '').strip(), "")
        display_dir = f"{items['direction']} {arrow}".strip()
        embed.add_field(name="🧭 移動方向", value=display_dir, inline=True)
    if "speed" in items: embed.add_field(name="🛰️ 移動時速", value=items["speed"], inline=True)
    if "pressure" in items: embed.add_field(name="🎈 中心氣壓", value=items["pressure"], inline=True)
    if "max_wind" in items: embed.add_field(name="💨 近中心風速", value=items["max_wind"], inline=True)
    if "max_gust" in items: embed.add_field(name="🌪️ 最大陣風", value=items["max_gust"], inline=True)
    if "radius_7" in items: embed.add_field(name="📏 七級風平均暴風半徑", value=items["radius_7"], inline=True)
    if "radius_10" in items: embed.add_field(name="📏 十級風平均暴風半徑", value=items["radius_10"], inline=True)
    
    current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
    
    return embed

def build_prob_embed(results, valid_time_display):
    embed = discord.Embed(title="", color=0xe74c3c)
    embed.description = f"**全台各縣市** 颱風暴風圈侵襲機率\n發佈時間：{valid_time_display}\n\n"
    
    if not results:
        embed.description += "✅ **目前無颱風暴風圈侵襲台灣的機率。**"
    else:
        regions = {
            "北部": ["基隆市", "臺北市", "新北市", "桃園市", "新竹縣", "新竹市"],
            "中部": ["苗栗縣", "臺中市", "彰化縣", "南投縣", "雲林縣"],
            "南部": ["嘉義縣", "嘉義市", "臺南市", "高雄市", "屏東縣"],
            "東部": ["宜蘭縣", "花蓮縣", "臺東縣"],
            "外島": ["澎湖縣", "金門縣", "連江縣"]
        }
        
        prob_dict = {r['county']: r['prob'] for r in results}
        
        for region_name, cities in regions.items():
            lines = []
            for city in cities:
                prob_val = prob_dict.get(city, 0)
                if prob_val >= 75: icon = "🔴"
                elif prob_val >= 50: icon = "🟠"
                elif prob_val >= 25: icon = "🟡"
                elif prob_val > 0: icon = "⚪"
                else: icon = "⚪"
                
                lines.append(f"`{icon} {str(prob_val).rjust(3)}%` **{city}**")
            
            if lines:
                embed.add_field(name=f"**{region_name}**", value="\n".join(lines), inline=True)
        
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        
    current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
    return embed


class TyphoonView(discord.ui.View):
    def __init__(self, bot, author_id: int, image_bytes, typhoons, results, valid_time_display, sst_data=None, tchp_data=None, initial_mode="overview"):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.bot = bot
        self.image_bytes = image_bytes
        self.typhoons = typhoons
        self.results = results
        self.valid_time_display = valid_time_display
        self.sst_bytes, self.sst_time = sst_data if sst_data else (None, "未知時間")
        self.tchp_bytes, self.tchp_time = tchp_data if tchp_data else (None, "未知時間")
        
        self.current_typhoon_idx = 0
        self.current_view_mode = initial_mode
        
        if len(self.typhoons) > 1:
            typhoon_options = []
            for i, t in enumerate(self.typhoons):
                name = t.get("name", "未知")
                num = t.get("number", "")
                td_num = t.get("td_number", "")
                is_td = t.get("is_td", False)
                
                if is_td:
                    if name != "未知" and name:
                        label = f"{td_num} 原{name}颱風".strip()
                    else:
                        label = f"熱帶性低氣壓 {td_num}".strip()
                else:
                    ty_type = t.get("ty_type", "颱風")
                    label = f"第{num}號 {ty_type} {name}" if num else f"{ty_type} {name}"
                typhoon_options.append(discord.SelectOption(label=label, value=str(i), default=(i==0)))
                
            self.typhoon_select = discord.ui.Select(placeholder="選擇颱風", options=typhoon_options, min_values=1, max_values=1)
            self.typhoon_select.callback = self.typhoon_select_callback
            self.add_item(self.typhoon_select)
        else:
            self.typhoon_select = None
            
        view_options = [
            discord.SelectOption(label="概覽", emoji="🌀", value="overview", default=(initial_mode=="overview")),
            discord.SelectOption(label="暴風圈侵襲機率", emoji="🌀", value="prob_map", default=(initial_mode=="prob_map")),
            discord.SelectOption(label="海水表面溫度", emoji="🌡️", value="sst_map", default=(initial_mode=="sst_map")),
            discord.SelectOption(label="海洋熱潛勢", emoji="🌊", value="tchp_map", default=(initial_mode=="tchp_map"))
        ]
        
        self.view_select = discord.ui.Select(placeholder="選擇顯示模式", options=view_options, min_values=1, max_values=1)
        self.view_select.callback = self.view_select_callback
        self.add_item(self.view_select)
        
        if self.typhoon_select:
            self.typhoon_select.disabled = self.current_view_mode in ["prob_map", "sst_map", "tchp_map"]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

        
    def _build_message_content(self):
        if self.current_view_mode == "overview":
            if self.typhoons:
                embed = build_overview_embed(self.typhoons[self.current_typhoon_idx])
            else:
                embed = discord.Embed(title="颱風概覽", description="目前無活躍颱風或無法取得資料。", color=0xe74c3c)
            
            file = None
            if self.image_bytes:
                file = discord.File(io.BytesIO(self.image_bytes), filename="typhoon.png")
                embed.set_image(url="attachment://typhoon.png")
            else:
                embed.set_image(url=None)
        elif self.current_view_mode == "prob_map":
            embed = build_prob_embed(self.results, self.valid_time_display)
            file = None
            embed.set_image(url=None)
        elif self.current_view_mode == "sst_map":
            embed = discord.Embed(title="", color=0x3498db)
            embed.description = f"**海水表面溫度**\n資料時間：{self.sst_time}"
            file = None
            if self.sst_bytes:
                file = discord.File(io.BytesIO(self.sst_bytes), filename="sst.png")
                embed.set_image(url="attachment://sst.png")
            else:
                embed.description += "\n\n無法取得最新的海水表面溫度圖。"
            
            current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
            embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        elif self.current_view_mode == "tchp_map":
            embed = discord.Embed(title="", color=0xe67e22)
            embed.description = f"**海洋熱潛勢**\n資料時間：{self.tchp_time}"
            file = None
            if self.tchp_bytes:
                file = discord.File(io.BytesIO(self.tchp_bytes), filename="tchp.png")
                embed.set_image(url="attachment://tchp.png")
            else:
                embed.description += "\n\n無法取得最新的海洋熱潛勢圖。"
                
            current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
            embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        return embed, file

    async def update_message(self, interaction: discord.Interaction):
        embed, file = self._build_message_content()
        await interaction.response.edit_message(embed=embed, view=self, attachments=[file] if file else [])

    async def typhoon_select_callback(self, interaction: discord.Interaction):
        self.current_typhoon_idx = int(self.typhoon_select.values[0])
        for opt in self.typhoon_select.options:
            opt.default = (opt.value == str(self.current_typhoon_idx))
        await self.update_message(interaction)

    async def view_select_callback(self, interaction: discord.Interaction):
        self.current_view_mode = self.view_select.values[0]
        for opt in self.view_select.options:
            opt.default = (opt.value == self.current_view_mode)
            
        # 如果切換到暴風圈機率、海水表面溫度、海洋熱潛勢，因為是全區資料，所以不允許切換颱風
        if self.typhoon_select:
            self.typhoon_select.disabled = self.current_view_mode in ["prob_map", "sst_map", "tchp_map"]
            
        await self.update_message(interaction)


class TyphoonCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="颱風動態", description="🌀 查詢最新的颱風動態與暴風圈侵襲機率 Typhoon")
    @app_commands.describe(mode="選擇要直接查看的資料")
    @app_commands.choices(mode=[
        app_commands.Choice(name="颱風概覽", value="overview"),
        app_commands.Choice(name="暴風圈侵襲機率", value="prob_map"),
        app_commands.Choice(name="海水表面溫度", value="sst_map"),
        app_commands.Choice(name="海洋熱潛勢", value="tchp_map"),
    ])
    async def typhoon_prob_command(self, interaction: discord.Interaction, mode: str = "overview"):
        await interaction.response.defer()
        
        polygons_task = fetch_typhoon_data(self.bot.session)
        image_task = fetch_typhoon_image(self.bot.session)
        overview_task = fetch_typhoon_overview(self.bot.session)
        sea_task = fetch_sea_images(self.bot.session)
        
        # 並行抓取所有資料
        (polygons, valid_time), (image_bytes, image_url), typhoons, (sst_data, tchp_data) = await asyncio.gather(
            polygons_task, image_task, overview_task, sea_task
        )
        
        results = get_typhoon_probabilities(polygons) if polygons else []
        
        # 將 valid_time 轉為 Discord Timestamp 供原本的 prob 畫面使用
        try:
            try:
                dt = datetime.fromisoformat(valid_time)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
            except ValueError:
                dt = datetime.strptime(valid_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))
            valid_time_display = f"<t:{int(dt.timestamp())}:f>"
            valid_time_image_display = dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            valid_time_display = valid_time if valid_time != "未知時間" else "尚未發布"
            valid_time_image_display = valid_time_display
        
        view = TyphoonView(self.bot, interaction.user.id, image_bytes, typhoons, results, valid_time_display, sst_data, tchp_data, mode)
        embed, file = view._build_message_content()
            
        if file:
            await interaction.followup.send(content="🌀 颱風動態查詢", embed=embed, file=file, view=view)
        else:
            await interaction.followup.send(content="🌀 颱風動態查詢", embed=embed, view=view)

    async def refresh_message(self, interaction: discord.Interaction, message: discord.Message, cmd_name: str):
        await interaction.response.defer(ephemeral=True)
        mode = "overview"
        for row in message.components:
            for child in row.children:
                if getattr(child, "type", None) == discord.ComponentType.select:
                    if child.placeholder and "切換資料" in child.placeholder:
                        for opt in child.options:
                            if opt.default:
                                mode = opt.value
        
        polygons_task = fetch_typhoon_data(self.bot.session)
        image_task = fetch_typhoon_image(self.bot.session)
        overview_task = fetch_typhoon_overview(self.bot.session)
        sea_task = fetch_sea_images(self.bot.session)
        
        (polygons, valid_time), (image_bytes, image_url), typhoons, (sst_data, tchp_data) = await asyncio.gather(
            polygons_task, image_task, overview_task, sea_task
        )
        
        results = get_typhoon_probabilities(polygons) if polygons else []
        
        try:
            try:
                dt = datetime.fromisoformat(valid_time)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
            except ValueError:
                dt = datetime.strptime(valid_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=8)))
            valid_time_display = f"<t:{int(dt.timestamp())}:f>"
        except ValueError:
            valid_time_display = valid_time if valid_time != "未知時間" else "尚未發布"
        
        view = TyphoonView(self.bot, interaction.user.id, image_bytes, typhoons, results, valid_time_display, sst_data, tchp_data, mode)
        
        # 嘗試保留原先選定的颱風選項 (如果有)
        for row in message.components:
            for child in row.children:
                if getattr(child, "type", None) == discord.ComponentType.select:
                    if child.placeholder and "選擇颱風" in child.placeholder:
                        for opt in child.options:
                            if opt.default:
                                try:
                                    view.current_typhoon_idx = int(opt.value)
                                    for v_opt in view.typhoon_select.options:
                                        v_opt.default = (v_opt.value == str(view.current_typhoon_idx))
                                except Exception:
                                    pass

        embed, file = view._build_message_content()
            
        if file:
            await message.edit(content="🌀 颱風動態查詢", embed=embed, attachments=[file], view=view)
        else:
            await message.edit(content="🌀 颱風動態查詢", embed=embed, view=view)
            
        await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)

async def setup(bot):
    await bot.add_cog(TyphoonCog(bot))
