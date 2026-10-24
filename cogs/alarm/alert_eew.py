import discord
from discord.ext import commands, tasks
import aiohttp
import json
import logging
from datetime import datetime, timezone, timedelta
import asyncio
import os

from modules.database import get_all_settings
from modules.location_matcher import town_mapping_cache
from modules.cache_manager import load_cache
from modules.discord_utils import safe_channel_send
from cogs.list_eq import get_eq_color
import modules.travel_time as tt
import math
import uuid
import io
from PIL import Image, ImageDraw, ImageFont
TOPO_LOCAL = 'maps/towns-mercator-10t.json'
TOPO_CACHE = None
BASE_MAP_IMAGE = None
PREPROCESSED_MAP_META = None
FONTS_CACHE = None

COUNTY_ORDER = {
    '基隆市': 1, '臺北市': 2, '台北市': 2, '新北市': 3, '桃園市': 4, 
    '新竹縣': 5, '新竹市': 6, '苗栗縣': 7, '臺中市': 8, '台中市': 8,
    '彰化縣': 9, '南投縣': 10, '雲林縣': 11, '嘉義縣': 12, '嘉義市': 13, 
    '臺南市': 14, '台南市': 14, '高雄市': 15, '屏東縣': 16, 
    '宜蘭縣': 17, '花蓮縣': 18, '臺東縣': 19, '台東縣': 19, 
    '澎湖縣': 20, '金門縣': 21, '連江縣': 22, '馬祖': 22
}

def get_county_order(loc_name: str) -> int:
    for county, order in COUNTY_ORDER.items():
        if loc_name.startswith(county):
            return order
    return 999

def format_fullwidth_grade(display_grade) -> str:
    s = str(display_grade).translate(str.maketrans('0123456789', '０１２３４５６７８９'))
    if "弱" not in s and "強" not in s and not s.endswith("級"):
        s += "級"
    return s

def normalize_county_name(name: str) -> str:
    if not name:
        return ""
    parts = str(name).strip().split()
    first = parts[0] if parts else str(name)
    first = first.replace('台北市', '臺北市').replace('台中市', '臺中市').replace('台南市', '臺南市').replace('台東縣', '臺東縣').replace('台東市', '臺東縣')
    for county in COUNTY_ORDER.keys():
        if first.startswith(county):
            return county.replace('台北市', '臺北市').replace('台中市', '臺中市').replace('台南市', '臺南市').replace('台東市', '臺東縣')
    return first

SHINDO_LOOKUP = {
    '0': (0, '0'), '0.0': (0, '0'),
    '1': (1, '1'), '1.0': (1, '1'),
    '2': (2, '2'), '2.0': (2, '2'),
    '3': (3, '3'), '3.0': (3, '3'),
    '4': (4, '4'), '4.0': (4, '4'),
    '5-': (5, '5弱'), '5.0': (5, '5弱'), '5弱': (5, '5弱'),
    '5+': (6, '5強'), '5.5': (6, '5強'), '5強': (6, '5強'),
    '6-': (7, '6弱'), '6.0': (7, '6弱'), '6弱': (7, '6弱'),
    '6+': (8, '6強'), '6.5': (8, '6強'), '6強': (8, '6強'),
    '7': (9, '7'), '7.0': (9, '7'), '7級': (9, '7'),
}

def parse_shindo_value(val):
    s = str(val).strip()
    if s in SHINDO_LOOKUP:
        return SHINDO_LOOKUP[s]
    try:
        f = float(s)
        if f <= 0: return (0, '0')
        elif f < 1.5: return (1, '1')
        elif f < 2.5: return (2, '2')
        elif f < 3.5: return (3, '3')
        elif f < 4.5: return (4, '4')
        elif f < 5.25: return (5, '5弱')
        elif f < 5.75: return (6, '5強')
        elif f < 6.25: return (7, '6弱')
        elif f < 6.75: return (8, '6強')
        else: return (9, '7')
    except (ValueError, TypeError):
        return (0, '0')


def get_mag_emoji(mag) -> str:
    try:
        m = float(mag)
        if m <= 0:
            return "❔"
        elif m < 4.0:
            return "⚪"
        elif m < 5.0:
            return "🟢"
        elif m < 5.6:
            return "🟡"
        elif m < 6.3:
            return "🟠"
        elif m < 6.6:
            return "🔴"
        elif m < 7.5:
            return "🟣"
        else:
            return "🛑"
    except (ValueError, TypeError):
        return "❔"

COLOR_RANK = {
    "⚪": 0,
    "🟢": 1,
    "🟡": 2,
    "🟠": 3,
    "🔴": 4,
    "🟣": 5,
    "🛑": 6
}
RANK_TO_EMOJI = {v: k for k, v in COLOR_RANK.items()}

def get_depth_emoji(depth, mag=None) -> str:
    try:
        d = float(depth)
        if d < 0:
            raw_emoji = "❔"
        elif d < 30:
            raw_emoji = "🔴"
        elif d < 70:
            raw_emoji = "🟠"
        elif d < 150:
            raw_emoji = "🟡"
        elif d < 300:
            raw_emoji = "🟢"
        else:
            raw_emoji = "⚪"
    except (ValueError, TypeError):
        return "❔"

    if mag is not None and raw_emoji in COLOR_RANK:
        mag_emoji = get_mag_emoji(mag)
        if mag_emoji in COLOR_RANK:
            d_rank = COLOR_RANK[raw_emoji]
            m_rank = COLOR_RANK[mag_emoji]
            final_rank = min(d_rank, m_rank)
            return RANK_TO_EMOJI[final_rank]

    return raw_emoji

def get_intensity_emoji(display_grade) -> str:
    s = str(display_grade).strip()
    if s in ('0', '0.0', '0級'):
        return "⚫"
    elif s in ('1', '1.0', '1級'):
        return "⚪"
    elif s in ('2', '2.0', '2級'):
        return "🔵"
    elif s in ('3', '3.0', '3級'):
        return "🟢"
    elif s in ('4', '4.0', '4級'):
        return "🟡"
    elif s in ('5弱', '5-', '5.0', '5.5'):
        return "🟠"
    elif s in ('5強', '5+'):
        return "🟤"
    elif s in ('6弱', '6-'):
        return "🔴"
    elif s in ('6強', '6+'):
        return "🟣"
    elif s in ('7', '7.0', '7級'):
        return "🛑"
    else:
        return "⚫"



def get_fonts():
    global FONTS_CACHE
    if FONTS_CACHE is not None:
        return FONTS_CACHE
    
    font_paths = [
        "fonts/Noto_Sans_TC/NotoSansTC-Regular.ttf",
        "fonts\\Noto_Sans_TC\\NotoSansTC-Regular.ttf",
        "PingFang.ttc",
        "C:\\Windows\\Fonts\\msjh.ttc",
        "msjh.ttc"
    ]
    font_paths_bold = [
        "fonts/Noto_Sans_TC/NotoSansTC-Bold.ttf",
        "fonts\\Noto_Sans_TC\\NotoSansTC-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "Arial Bold.ttf",
        "C:\\Windows\\Fonts\\msjhbd.ttc",
        "msjhbd.ttc"
    ]
    font_title = font_time = font_intensity = font_legend = font_legend_title = None
    for path in font_paths:
        try:
            font_title = ImageFont.truetype(path, 48)
            font_time = ImageFont.truetype(path, 26)
            font_legend = ImageFont.truetype(path, 20)
            font_legend_title = ImageFont.truetype(path, 22)
            break
        except Exception:
            continue
            
    for path in font_paths_bold + font_paths:
        try:
            font_intensity = ImageFont.truetype(path, 15)
            break
        except Exception:
            continue

    if font_title is None:
        font_title = ImageFont.load_default()
        font_time = ImageFont.load_default()
        font_legend = ImageFont.load_default()
        font_legend_title = ImageFont.load_default()
    if font_intensity is None:
        font_intensity = ImageFont.load_default()
        
    FONTS_CACHE = {
        'title': font_title,
        'time': font_time,
        'legend': font_legend,
        'legend_title': font_legend_title,
        'intensity': font_intensity
    }
    return FONTS_CACHE

def init_map_cache():
    global TOPO_CACHE, BASE_MAP_IMAGE, PREPROCESSED_MAP_META
    if BASE_MAP_IMAGE is not None:
        return

    with open(TOPO_LOCAL, 'r', encoding='utf-8') as f:
        topo = json.load(f)
    TOPO_CACHE = topo

    scale = topo['transform']['scale']
    translate = topo['transform']['translate']
    arcs = topo['arcs']

    decoded_arcs = []
    for arc in arcs:
        x, y = 0, 0
        decoded = []
        for point in arc:
            x += point[0]
            y += point[1]
            decoded.append((x * scale[0] + translate[0], y * scale[1] + translate[1]))
        decoded_arcs.append(decoded)

    lines = []
    matsu_x_list, matsu_y_list = [], []
    kinmen_x_list, kinmen_y_list = [], []
    penghu_x_list, penghu_y_list = [], []

    for geom in topo['objects']['towns']['geometries']:
        props = geom.get('properties', {})
        county = props.get('COUNTYNAME', '')
        town = props.get('TOWNNAME', '')
        
        geom_lines = []
        if geom['type'] == 'Polygon':
            for ring in geom['arcs']:
                line = []
                for arc_idx in ring:
                    arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                    line.extend(arc)
                geom_lines.append(line)
        elif geom['type'] == 'MultiPolygon':
            for poly in geom['arcs']:
                for ring in poly:
                    line = []
                    for arc_idx in ring:
                        arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                        line.extend(arc)
                    geom_lines.append(line)
        
        if county == '金門縣':
            for line in geom_lines:
                for pt in line:
                    kinmen_x_list.append(pt[0])
                    kinmen_y_list.append(pt[1])
            continue
        elif county == '連江縣':
            for line in geom_lines:
                for pt in line:
                    matsu_x_list.append(pt[0])
                    matsu_y_list.append(pt[1])
            continue
        elif county == '澎湖縣':
            for line in geom_lines:
                for pt in line:
                    penghu_x_list.append(pt[0])
                    penghu_y_list.append(pt[1])
        
        is_main = county != '澎湖縣'
        
        orig_x_pts = [p[0] for line in geom_lines for p in line]
        orig_y_pts = [p[1] for line in geom_lines for p in line]
        cx = sum(orig_x_pts) / len(orig_x_pts) if orig_x_pts else 0
        cy = sum(orig_y_pts) / len(orig_y_pts) if orig_y_pts else 0

        lines.append({
            'is_main': is_main, 
            'county': county, 
            'town': town,
            'coords': geom_lines,
            'orig_cx': cx,
            'orig_cy': cy
        })

    main_x = [pt[0] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
    main_y = [pt[1] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
    min_x, max_x = min(main_x), max(main_x)
    min_y, max_y = min(main_y), max(main_y)

    WGS_MIN_LON, WGS_MAX_LON = 120.036, 122.001
    WGS_MIN_LAT, WGS_MAX_LAT = 21.896, 25.300

    def merc_y(lat_deg):
        return math.log(math.tan(math.pi/4 + lat_deg * math.pi/360))

    penghu_offset_x = 0
    penghu_offset_y = 0
    if penghu_x_list:
        fake_cx = (min(penghu_x_list) + max(penghu_x_list)) / 2
        fake_cy = (min(penghu_y_list) + max(penghu_y_list)) / 2
        
        real_cx = min_x + (119.508 - WGS_MIN_LON) / (WGS_MAX_LON - WGS_MIN_LON) * (max_x - min_x)
        my = merc_y(23.479)
        my_max = merc_y(WGS_MAX_LAT)
        my_min = merc_y(WGS_MIN_LAT)
        real_cy = min_y + (my_max - my) / (my_max - my_min) * (max_y - min_y)
        
        penghu_offset_x = real_cx - fake_cx
        penghu_offset_y = real_cy - fake_cy

    for item in lines:
        if item['county'] == '澎湖縣':
            for line in item['coords']:
                for i in range(len(line)):
                    line[i] = (line[i][0] + penghu_offset_x, line[i][1] + penghu_offset_y)

    county_lines = []
    if 'counties' in topo['objects']:
        for geom in topo['objects']['counties']['geometries']:
            c_name = geom.get('properties', {}).get('COUNTYNAME', '')
            if c_name in ['金門縣', '連江縣']:
                continue
                
            geom_lines = []
            if geom['type'] == 'Polygon':
                for ring in geom['arcs']:
                    line = []
                    for arc_idx in ring:
                        arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                        line.extend(arc)
                    geom_lines.append(line)
            elif geom['type'] == 'MultiPolygon':
                for poly in geom['arcs']:
                    for ring in poly:
                        line = []
                        for arc_idx in ring:
                            arc = decoded_arcs[~arc_idx][::-1] if arc_idx < 0 else decoded_arcs[arc_idx]
                            line.extend(arc)
                        geom_lines.append(line)
                        
            filtered_geom_lines = []
            for line in geom_lines:
                if not line: continue
                
                if c_name == '澎湖縣':
                    moved_line = [(p[0] + penghu_offset_x, p[1] + penghu_offset_y) for p in line]
                    filtered_geom_lines.append(moved_line)
                else:
                    filtered_geom_lines.append(line)
                    
            if filtered_geom_lines:
                county_lines.append(filtered_geom_lines)

    all_x = [pt[0] for item in lines for line in item['coords'] for pt in line]
    all_y = [pt[1] for item in lines for line in item['coords'] for pt in line]
    img_min_x, img_max_x = min(all_x), max(all_x)
    img_min_y, img_max_y = min(all_y), max(all_y)

    IMG_W = 920
    pad_left = 40
    pad_right = 160
    pad_top = 40
    pad_bottom = 40
    scale_factor = (IMG_W - pad_left - pad_right) / (img_max_x - img_min_x)
    IMG_H = int((img_max_y - img_min_y) * scale_factor) + pad_top + pad_bottom

    def map_to_img(x, y):
        px = pad_left + (x - img_min_x) * scale_factor
        py = pad_top + (y - img_min_y) * scale_factor
        return px, py

    OVERSAMPLE = 3
    img_os = Image.new('RGBA', (IMG_W * OVERSAMPLE, IMG_H * OVERSAMPLE), "#0f1113")
    draw_os = ImageDraw.Draw(img_os)
    
    for item in lines:
        fill_color = "#30363d"
        outline_color = "#4a535e"
        for line in item['coords']:
            px_line = [(map_to_img(pt[0], pt[1])[0] * OVERSAMPLE, map_to_img(pt[0], pt[1])[1] * OVERSAMPLE) for pt in line]
            if len(px_line) >= 3:
                draw_os.polygon(px_line, fill=fill_color, outline=outline_color)

    county_outline_color = "#687585"
    for geom_lines in county_lines:
        for line in geom_lines:
            px_line = [(map_to_img(pt[0], pt[1])[0] * OVERSAMPLE, map_to_img(pt[0], pt[1])[1] * OVERSAMPLE) for pt in line]
            if len(px_line) >= 2:
                draw_os.line(px_line, fill=county_outline_color, width=2 * OVERSAMPLE)
                
    base_img = img_os.resize((IMG_W, IMG_H), Image.LANCZOS)

    BASE_MAP_IMAGE = base_img
    PREPROCESSED_MAP_META = {
        'lines': lines,
        'IMG_W': IMG_W,
        'IMG_H': IMG_H,
        'min_x': min_x,
        'max_x': max_x,
        'min_y': min_y,
        'max_y': max_y,
        'WGS_MIN_LON': WGS_MIN_LON,
        'WGS_MAX_LON': WGS_MAX_LON,
        'WGS_MIN_LAT': WGS_MIN_LAT,
        'WGS_MAX_LAT': WGS_MAX_LAT,
        'pad_left': pad_left,
        'pad_top': pad_top,
        'scale_factor': scale_factor,
        'img_min_x': img_min_x,
        'img_min_y': img_min_y,
    }

def load_topo():
    init_map_cache()
    return TOPO_CACHE

CDI_MAP = [
    (0.35, '#4b5563', '0',   '無感'),
    (1.10, '#6cbb6c', '1',   '微震'),
    (1.90, '#00AAFF', '2',   '輕震'),
    (2.80, '#0041FF', '3',   '弱震'),
    (3.70, '#FAE696', '4',   '中震'),
    (4.35, '#FFE600', '5弱', '強震'),
    (4.85, '#FF9900', '5強', '強震'),
    (5.55, '#FF2800', '6弱', '烈震'),
    (6.30, '#A50021', '6強', '烈震'),
    (9.00, '#B40068', '7',   '劇震'),
]

def cdi_style(cdi):
    for maxc, col, grade, label in CDI_MAP:
        if cdi < maxc:
            return col, grade, label
    return CDI_MAP[-1][1], CDI_MAP[-1][2], CDI_MAP[-1][3]

logger = logging.getLogger(__name__)

# UTC+8 時區
TPE_TZ = timezone(timedelta(hours=8))

def distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

VS30_GRID_CACHE = None

def load_vs30_grid():
    global VS30_GRID_CACHE
    if VS30_GRID_CACHE is not None:
        return
    VS30_GRID_CACHE = {}
    filepath = 'data/ncree_vs30.csv'
    if os.path.exists(filepath):
        try:
            import csv
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    glon = round(float(row['lon']), 2)
                    glat = round(float(row['lat']), 2)
                    VS30_GRID_CACHE[(glon, glat)] = float(row['vs30'])
            logger.info("✅ [EEW資料] 成功載入高解析度 Vs30 網格資料")
        except Exception as e:
            logger.error(f"❌ [EEW資料] 載入 Vs30 網格資料失敗：{e!r}")

def get_vs30(county, town, lon=None, lat=None):
    if lon is not None and lat is not None and VS30_GRID_CACHE:
        r_lon = round(lon, 2)
        r_lat = round(lat, 2)
        if (r_lon, r_lat) in VS30_GRID_CACHE:
            return VS30_GRID_CACHE[(r_lon, r_lat)]
        for dlon in [-0.01, 0, 0.01]:
            for dlat in [-0.01, 0, 0.01]:
                if (round(r_lon+dlon, 2), round(r_lat+dlat, 2)) in VS30_GRID_CACHE:
                    return VS30_GRID_CACHE[(round(r_lon+dlon, 2), round(r_lat+dlat, 2))]
                    
    # 概略估計台灣各鄉鎮 Vs30
    county = county.replace('臺', '台')
    
    if county == '台北市':
        return 220
    if county == '新北市':
        if town in ['板橋區', '三重區', '蘆洲區', '新莊區', '中和區', '永和區', '五股區', '泰山區']: return 220
        if town in ['林口區']: return 350
        return 450
    if county == '桃園市':
        if town in ['復興區']: return 600
        return 380
    if county == '新竹市': return 350
    if county == '新竹縣':
        if town in ['尖石鄉', '五峰鄉']: return 650
        return 400
    if county == '苗栗縣':
        if town in ['泰安鄉', '南庄鄉', '獅潭鄉']: return 600
        return 400
    if county == '台中市':
        if town in ['和平區']: return 700
        return 320
    if county == '彰化縣': return 220
    if county == '雲林縣': return 220
    if county == '嘉義市': return 250
    if county == '嘉義縣':
        if town in ['阿里山鄉', '大埔鄉', '梅山鄉', '番路鄉', '竹崎鄉']: return 600
        return 250
    if county == '台南市':
        if town in ['楠西區', '南化區', '白河區', '東山區']: return 500
        return 220
    if county == '高雄市':
        if town in ['桃源區', '那瑪夏區', '茂林區', '甲仙區', '六龜區']: return 600
        return 280
    if county == '屏東縣':
        if town in ['三地門鄉', '霧台鄉', '瑪家鄉', '泰武鄉', '來義鄉', '春日鄉', '獅子鄉', '牡丹鄉']: return 600
        return 280
    if county == '宜蘭縣':
        if town in ['宜蘭市', '羅東鎮', '五結鄉', '壯圍鄉', '冬山鄉', '礁溪鄉']: return 200
        if town in ['南澳鄉', '大同鄉']: return 600
        return 300
    if county == '花蓮縣':
        if town in ['秀林鄉', '萬榮鄉', '卓溪鄉']: return 700
        return 380 # 花東縱谷
    if county == '台東縣':
        if town in ['海端鄉', '延平鄉', '金峰鄉', '達仁鄉']: return 700
        return 380
    if county == '南投縣':
        if town in ['仁愛鄉', '信義鄉']: return 700
        if town in ['南投市', '草屯鎮', '名間鄉', '竹山鎮']: return 350
        return 500
    if county == '基隆市': return 450
    if county == '澎湖縣': return 450
    if county == '金門縣': return 500
    if county == '連江縣': return 500
    return 400

def simulate_gm(mag, depth, lon, lat, fault_type, target_lon, target_lat, is_subduction, vs30, site_factor=None):
    D = distance(lat, lon, target_lat, target_lon)
    R = math.sqrt(D**2 + depth**2)
    R = max(R, 3.0)
    
    # 基本的 GMPE (改良版地動預測方程式)
    if is_subduction:
        # 隱沒帶地震震度較小，但衰減較慢
        log_pga = 0.00 + 0.55 * mag - 0.003 * R - 1.1 * math.log10(R)
        log_pgv = -1.00 + 0.60 * mag - 0.002 * R - 1.1 * math.log10(R)
    else:
        # 淺層地殼地震
        log_pga = 0.20 + 0.58 * mag - 0.005 * R - 1.2 * math.log10(R)
        log_pgv = -1.10 + 0.62 * mag - 0.004 * R - 1.2 * math.log10(R)
        
    if fault_type == '逆斷層':
        log_pga += 0.15
        log_pgv += 0.15
    elif fault_type == '正斷層':
        log_pga -= 0.10
        log_pgv -= 0.10
        
    pga_rock = 10 ** log_pga
    pgv_rock = 10 ** log_pgv
    
    if site_factor is not None and site_factor > 0:
        amp_pga = site_factor
        amp_pgv = site_factor
    else:
        # 依據 Vs30 進行非線性場址放大 (Non-linear NEHRP approximation)
        if pga_rock < 100:
            amp_pga = (vs30 / 760.0) ** -0.35
            amp_pgv = (vs30 / 760.0) ** -0.65
        else:
            non_linear_factor = max(0.1, 1.0 - (pga_rock - 100) / 1000.0)
            amp_pga = ((vs30 / 760.0) ** -0.35) * non_linear_factor
            amp_pgv = ((vs30 / 760.0) ** -0.65) * non_linear_factor
        
        # 限制放大倍率，避免軟弱地盤無限放大
        amp_pga = min(max(amp_pga, 0.5), 3.0)
        amp_pgv = min(max(amp_pgv, 0.5), 4.0)
    
    return pga_rock * amp_pga, pgv_rock * amp_pgv

def get_epicenter_name(lon, lat, original_name):
    if not town_mapping_cache:
        return original_name
        
    min_dist = float('inf')
    nearest_town = None
    angles = []
    
    for _, items in town_mapping_cache.items():
        for item in items:
            fullname, t_lat, t_lon = item[0], item[1], item[2]
            if t_lat is not None and t_lon is not None:
                # 等距圓柱投影估算 (1度約111公里)
                dx = (lon - t_lon) * 111 * math.cos(math.radians((lat + t_lat) / 2))
                dy = (lat - t_lat) * 111
                dist = math.hypot(dx, dy)
                
                # 計算震央朝向鄉鎮的向量 (t_lon - lon)
                dx_t = (t_lon - lon) * 111 * math.cos(math.radians((lat + t_lat) / 2))
                dy_t = (t_lat - lat) * 111
                angle = math.degrees(math.atan2(dy_t, dx_t))
                angles.append(angle)
                
                if dist < min_dist:
                    min_dist = dist
                    nearest_town = fullname
                    
    if nearest_town is None:
        return original_name
        
    angles.sort()
    max_gap = 0
    if len(angles) > 1:
        for i in range(len(angles)):
            gap = angles[i] - angles[i-1]
            if gap < 0:
                gap += 360
            if gap > max_gap:
                max_gap = gap
                
    # 若最大視角空隙 > 160 度，且距離鄉鎮中心大於 5 公里，則判定為海上
    is_sea = (max_gap > 160)
    
    if is_sea and min_dist > 5:
        if min_dist <= 30:
            return f"{nearest_town[:3]}近海"
        else:
            # 以台灣地理中心 (埔里, 120.98, 23.97) 為基準計算方位
            dx_c = lon - 120.98
            dy_c = lat - 23.97
            angle_c = math.degrees(math.atan2(dy_c, dx_c))
            dirs = ["東部", "東北部", "北部", "西北部", "西部", "西南部", "南部", "東南部"]
            idx = round(angle_c / 45) % 8
            return f"台灣{dirs[idx]}海域"
            
    return nearest_town

def calc_cdi(pga, pgv):
    # 根據 2020 CWA 新制震度分級
    # 將離散級數轉換成 CDI_MAP 所需要的連續數值
    if pga < 0.8:
        # 0級: CDI 0 ~ 0.35
        return (pga / 0.8) * 0.35
    elif pga < 2.5:
        # 1級: CDI 0.35 ~ 1.10
        return 0.35 + (pga - 0.8) / (2.5 - 0.8) * (1.10 - 0.35)
    elif pga < 8.0:
        # 2級: CDI 1.10 ~ 1.90
        return 1.10 + (pga - 2.5) / (8.0 - 2.5) * (1.90 - 1.10)
    elif pga < 25.0:
        # 3級: CDI 1.90 ~ 2.80
        return 1.90 + (pga - 8.0) / (25.0 - 8.0) * (2.80 - 1.90)
    elif pga < 80.0:
        # 4級: CDI 2.80 ~ 3.70
        return 2.80 + (pga - 25.0) / (80.0 - 55.0) * (3.70 - 2.80)
    else:
        # 當 PGA >= 80 時，改看 PGV
        if pgv < 15.0:
            # 雖然 PGA>=80，但 PGV 不大，判定為 4級 (給 3.25~3.70)
            base_cdi = 3.25
            ratio = pgv / 15.0
            return base_cdi + ratio * (3.70 - base_cdi)
        elif pgv < 30.0:
            # 5弱: CDI 3.70 ~ 4.35
            return 3.70 + (pgv - 15.0) / (30.0 - 15.0) * (4.35 - 3.70)
        elif pgv < 50.0:
            # 5強: CDI 4.35 ~ 4.85
            return 4.35 + (pgv - 30.0) / (50.0 - 30.0) * (4.85 - 4.35)
        elif pgv < 80.0:
            # 6弱: CDI 4.85 ~ 5.55
            return 4.85 + (pgv - 50.0) / (80.0 - 50.0) * (5.55 - 4.85)
        elif pgv < 140.0:
            # 6強: CDI 5.55 ~ 6.30
            return 5.55 + (pgv - 80.0) / (140.0 - 80.0) * (6.30 - 5.55)
        else:
            # 7級: CDI 6.30 ~ 7.00
            val = 6.30 + (pgv - 140.0) / (250.0 - 140.0) * (7.00 - 6.30)
            return min(val, 7.0)

def render_emulator_map_pil(
    mag,
    depth,
    lon,
    lat,
    fault_type,
    msg_no=1,
    origin_time_str=None,
    show_report_number=True,
):
    init_map_cache()
    fonts = get_fonts()

    meta = PREPROCESSED_MAP_META
    lines = meta['lines']
    IMG_W = meta['IMG_W']
    IMG_H = meta['IMG_H']
    min_x, max_x = meta['min_x'], meta['max_x']
    min_y, max_y = meta['min_y'], meta['max_y']
    WGS_MIN_LON, WGS_MAX_LON = meta['WGS_MIN_LON'], meta['WGS_MAX_LON']
    WGS_MIN_LAT, WGS_MAX_LAT = meta['WGS_MIN_LAT'], meta['WGS_MAX_LAT']
    pad_left, pad_top = meta['pad_left'], meta['pad_top']
    scale_factor = meta['scale_factor']
    img_min_x, img_min_y = meta['img_min_x'], meta['img_min_y']

    def merc_y(lat_deg):
        return math.log(math.tan(math.pi/4 + lat_deg * math.pi/360))

    def map_to_img(x, y):
        px = pad_left + (x - img_min_x) * scale_factor
        py = pad_top + (y - img_min_y) * scale_factor
        return px, py

    def lonlat_to_img(lon, lat):
        x = min_x + (lon - WGS_MIN_LON) / (WGS_MAX_LON - WGS_MIN_LON) * (max_x - min_x)
        my = merc_y(lat)
        my_max = merc_y(WGS_MAX_LAT)
        my_min = merc_y(WGS_MIN_LAT)
        y = min_y + (my_max - my) / (my_max - my_min) * (max_y - min_y)
        return map_to_img(x, y)

    img = BASE_MAP_IMAGE.copy()
    draw = ImageDraw.Draw(img)

    font_title = fonts['title']
    font_time = fonts['time']
    font_intensity = fonts['intensity']
    font_legend = fonts['legend']
    font_legend_title = fonts['legend_title']

    # 繪製左上角標題
    draw.text((25, 25), " 強震即時警報", fill="#ffffff", font=font_title)
    summary_text = f"規模 {float(mag):.1f}  |  深度 {depth}km"
    if show_report_number:
        summary_text += f"\n第 {msg_no} 報"
    draw.multiline_text((35, 90), summary_text, fill="#cccccc", font=font_time, spacing=8)

    # 自動判斷隱沒帶
    is_subduction = False
    if depth > 35 and ((lat > 23.5 and lon > 121.5) or (lat < 23.0 and lon < 121.5) or depth > 45):
        is_subduction = True

    # 繪製左下角參數與時間
    if origin_time_str:
        time_display = origin_time_str
    else:
        time_display = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    time_text = f"Saiu-Bot EEW\n經度 {lon} | 緯度 {lat}\n時間 {time_display}\n地圖震度僅供參考"
    
    if hasattr(draw, 'multiline_textbbox'):
        text_bbox = draw.multiline_textbbox((0, 0), time_text, font=font_time)
        text_h = text_bbox[3] - text_bbox[1]
    else:
        _, text_h = draw.textsize(time_text, font=font_time)
        
    draw.multiline_text((25, IMG_H - text_h - 25), time_text, fill="#cccccc", font=font_time)

    def draw_aa_circle(target_img, cx, cy, r, fill, outline, width=1):
        scale = 4
        hr = int(r * scale)
        hw = int(width * scale)
        size = (hr + hw + 2) * 2
        
        c_img = Image.new('RGBA', (size, size), (0,0,0,0))
        c_draw = ImageDraw.Draw(c_img)
        c_draw.ellipse((hw, hw, size-hw-1, size-hw-1), fill=fill, outline=outline, width=hw)
        
        target_size = int(size / scale)
        c_img = c_img.resize((target_size, target_size), Image.LANCZOS)
        
        paste_x = int(cx - target_size / 2)
        paste_y = int(cy - target_size / 2)
        target_img.paste(c_img, (paste_x, paste_y), mask=c_img)

    # 計算各鄉鎮震度並排序
    town_results = []
    
    load_vs30_grid()
    
    for item in lines:
        county = item['county']
        town = item['town']
        fullname = f"{county}{town}"
        
        # 優先使用 town_mapping_cache (鄉鎮市區公所/人口中心) 的準確座標與地盤係數
        t_lat, t_lon, t_site = None, None, None
        if town_mapping_cache and town in town_mapping_cache:
            for mapped_item in town_mapping_cache[town]:
                mapped_name = mapped_item[0]
                if mapped_name == fullname and mapped_item[1] is not None and mapped_item[2] is not None:
                    t_lat, t_lon = mapped_item[1], mapped_item[2]
                    if len(mapped_item) > 3:
                        t_site = mapped_item[3]
                    break
                    
        # 如果找不到，退回使用多邊形幾何中心 (Polygon Centroid)
        if t_lat is None or t_lon is None:
            cx, cy = item['orig_cx'], item['orig_cy']
            t_lon = WGS_MIN_LON + (cx - min_x) / (max_x - min_x) * (WGS_MAX_LON - WGS_MIN_LON) if max_x > min_x else 0
            try:
                my_max = merc_y(WGS_MAX_LAT)
                my_min = merc_y(WGS_MIN_LAT)
                merc_y_lat = my_max - (cy - min_y) * (my_max - my_min) / (max_y - min_y)
                t_lat = (math.atan(math.exp(merc_y_lat)) - math.pi/4) * 360 / math.pi
            except Exception:
                t_lat = lat
                
        px, py = lonlat_to_img(t_lon, t_lat)
        
        vs30 = get_vs30(county, town, t_lon, t_lat)
        pga, pgv = simulate_gm(mag, depth, lon, lat, "逆斷層", t_lon, t_lat, is_subduction, vs30, site_factor=t_site)
        cdi = calc_cdi(pga, pgv)
        
        if cdi >= 0.35:
            town_results.append({
                'px': px, 'py': py, 'cdi': cdi
            })
            
    # 讓震度大的圓點顯示在最上層
    town_results.sort(key=lambda x: x['cdi'])
    
    # 繪製各鄉鎮震度
    for res in town_results:
        px, py, cdi = res['px'], res['py'], res['cdi']
        col, grade, label = cdi_style(cdi)
        rpx = 12
        draw_aa_circle(img, px, py, rpx, fill=col, outline='white', width=1)
        
        grade_str = str(grade).replace('弱', '-').replace('強', '+')
        
        # 依照背景色亮度決定文字顏色
        h = col.lstrip('#')
        r, g, b = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        text_col = 'white' if lum < 128 else '#1a1a1a'
        
        draw.text((px, py - 1), grade_str, fill=text_col, font=font_intensity, anchor="mm")

    # 繪製震央
    epx, epy = lonlat_to_img(lon, lat)
    cross_size = 14
    border_extend = 2
    # 白色外框 (稍微延長以包覆末端)
    draw.line((epx - cross_size - border_extend, epy - cross_size - border_extend, epx + cross_size + border_extend, epy + cross_size + border_extend), fill="#ffffff", width=10)
    draw.line((epx - cross_size - border_extend, epy + cross_size + border_extend, epx + cross_size + border_extend, epy - cross_size - border_extend), fill="#ffffff", width=10)
    # 紅色內部 (加粗)
    draw.line((epx - cross_size, epy - cross_size, epx + cross_size, epy + cross_size), fill="#ff3333", width=8)
    draw.line((epx - cross_size, epy + cross_size, epx + cross_size, epy - cross_size), fill="#ff3333", width=8)

    # 繪製右下角圖例
    leg = [(col, grade, label) for maxc, col, grade, label in CDI_MAP if maxc > 0.35]
    leg_w = 110
    leg_h = len(leg) * 30 + 50
    leg_x = IMG_W - leg_w - 20
    leg_y = IMG_H - leg_h - 40
    
    draw.rectangle((leg_x, leg_y, leg_x + leg_w, leg_y + leg_h), fill=(13, 14, 17, 230), outline='#292e33', width=1)
    
    if hasattr(draw, 'textbbox'):
        title_bbox = draw.textbbox((0, 0), "震度", font=font_legend_title)
        title_w = title_bbox[2] - title_bbox[0]
    else:
        title_w, _ = draw.textsize("震度", font=font_legend_title)
        
    draw.text((leg_x + (leg_w - title_w)/2, leg_y + 12), "震度", fill="#aaaaaa", font=font_legend_title)
    
    for i, (col, grade, label) in enumerate(leg):
        # 下框線到最後一個圓點的間距，與上框線到標題的間距一致。
        iy = leg_y + leg_h - 36 - i * 30
        draw.ellipse((leg_x + 20, iy + 4, leg_x + 36, iy + 20), fill=col, outline='#404040', width=1)
        draw.text((leg_x + 50, iy + 12), str(grade), fill="#e5e5e5", font=font_legend, anchor="lm")

    output = io.BytesIO()
    img.save(output, format='PNG')
    output.seek(0)
    
    return output

class EEWAlertCog(commands.Cog):
    EVENT_MATCH_MAX_TIME_SECONDS = 15
    EVENT_MATCH_MAX_DISTANCE_KM = 80

    def __init__(self, bot):
        self.bot = bot
        self.config = {}
        self.api_url = ""
        self.rf_api_key = ""
        self.experimental_stream_url = ""
        self.diagnostic_webhook_url = ""
        self.experimental_stream_task = None
        self.stream_connection_failed = False
        self.stream_has_connected = False
        self.rptes_history = []
        self.pending_quick_reports = set()
        cache = load_cache()
        self.sent_alerts = cache.get("eew_sent_alerts", {})
        self.stream_events = cache.get("eew_stream_events", {})
        self.cwa_event_links = cache.get("eew_cwa_event_links", {})
        if not isinstance(self.stream_events, dict):
            self.stream_events = {}
        if not isinstance(self.cwa_event_links, dict):
            self.cwa_event_links = {}
        self.event_processing_locks = {}
        self.diagnostic_locks = {}
        
        # 預先初始化地圖快取與字型
        init_map_cache()
        get_fonts()

        self.load_config()
        self.api_polling_until = 0
        self.last_api_time = 0
        self.rptes_polling_until = 0
        self.last_rptes_time = 0
        self.eew_loop.start()
        self.rptes_loop.start()
        if self.experimental_stream_url:
            self.experimental_stream_task = asyncio.create_task(
                self.listen_experimental_stream()
            )

    def save_state(self):
        return {
            "eew_sent_alerts": self.sent_alerts,
            "eew_stream_events": self.stream_events,
            "eew_cwa_event_links": self.cwa_event_links,
        }

    def load_config(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                self.config = json.load(f)
                self.api_url = self.config.get("CWA_EEW_AUTH", "")
                self.rf_api_key = self.config.get("RF_API_KEY", "") or os.getenv("RF_API_KEY", "")
                self.experimental_stream_url = (
                    os.getenv("EEW_EXPERIMENTAL_STREAM_URL", "")
                    or self.config.get("EEW_EXPERIMENTAL_STREAM_URL", "")
                )
                self.diagnostic_webhook_url = (
                    os.getenv("EEW_DIAGNOSTIC_WEBHOOK_URL", "")
                    or self.config.get("EEW_DIAGNOSTIC_WEBHOOK_URL", "")
                )
        except Exception as e:
            logger.error(f"❌ [EEW設定] 無法讀取 config.json：{e!r}")

        if not self.rf_api_key and os.path.exists(".env"):
            try:
                with open(".env", "r", encoding="utf-8") as env_f:
                    for line in env_f:
                        line = line.strip()
                        if line.startswith("RF_API_KEY="):
                            self.rf_api_key = line.split("=", 1)[1].strip('\'"')
                            break
            except Exception:
                pass

    def cog_unload(self):
        self.eew_loop.cancel()
        self.rptes_loop.cancel()
        if self.experimental_stream_task:
            self.experimental_stream_task.cancel()

    @staticmethod
    def _parse_eew_time(value):
        if value is None or value == "":
            return None
        try:
            numeric = float(value)
            if numeric > 10_000_000_000:
                numeric /= 1000
            return datetime.fromtimestamp(numeric, TPE_TZ)
        except (TypeError, ValueError, OSError):
            pass

        text = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=TPE_TZ)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(text)
            return parsed.replace(tzinfo=TPE_TZ) if parsed.tzinfo is None else parsed.astimezone(TPE_TZ)
        except ValueError:
            return None

    @staticmethod
    def _epicenter_distance_km(lat1, lon1, lat2, lon2):
        avg_lat = math.radians((lat1 + lat2) / 2)
        dx = (lon1 - lon2) * 111 * math.cos(avg_lat)
        dy = (lat1 - lat2) * 111
        return math.hypot(dx, dy)

    @staticmethod
    def _cwa_event_id(alert):
        identifier = str(alert.get("identifier") or "")
        msg_no = int(alert.get("msgNo", 1) or 1)
        suffix = f"{msg_no:02d}"
        return identifier[:-len(suffix)] if identifier.endswith(suffix) else identifier

    def _find_stream_event(self, origin_ts, lat, lon, external_identifier=None):
        now_ts = datetime.now(TPE_TZ).timestamp()
        candidates = []
        for event_id, event in self.stream_events.items():
            if now_ts - float(event.get("last_received", 0)) > 600:
                continue
            if external_identifier and event.get("external_identifier") == external_identifier:
                return event_id, 0.0, 0.0
            try:
                time_delta = abs(float(event["origin_ts"]) - origin_ts)
                distance = self._epicenter_distance_km(
                    float(event["latitude"]),
                    float(event["longitude"]),
                    lat,
                    lon,
                )
            except (KeyError, TypeError, ValueError):
                continue
            if (
                time_delta <= self.EVENT_MATCH_MAX_TIME_SECONDS
                and distance <= self.EVENT_MATCH_MAX_DISTANCE_KM
            ):
                score = (
                    time_delta / self.EVENT_MATCH_MAX_TIME_SECONDS
                    + distance / self.EVENT_MATCH_MAX_DISTANCE_KM
                )
                candidates.append((score, event_id, time_delta, distance))
        if not candidates:
            return None, None, None
        _, event_id, time_delta, distance = min(candidates)
        return event_id, time_delta, distance

    @staticmethod
    def _append_diagnostic(event, section, line, limit):
        entries = event.setdefault(section, [])
        entries.append(line)
        del entries[:-limit]

    @staticmethod
    def _diagnostic_field(entries, empty_text="尚無資料"):
        text = "\n".join(entries) if entries else empty_text
        if len(text) > 990:
            text = text[-990:]
        fence = chr(96) * 3
        return f"{fence}text\n{text}\n{fence}"

    async def _refresh_diagnostic_webhook(self, event_id):
        if not self.diagnostic_webhook_url or event_id not in self.stream_events:
            return
        session = getattr(self.bot, "session", None)
        if session is None or session.closed:
            return

        lock = self.diagnostic_locks.setdefault(event_id, asyncio.Lock())
        async with lock:
            event = self.stream_events.get(event_id)
            if not event:
                return
            embed = {
                "title": "地震速報發送結果",
                "description": f"編號 **{event.get('event_code', event_id[-12:])}**",
                "color": 0xF1C40F,
                "fields": [
                    {
                        "name": "Messages",
                        "value": self._diagnostic_field(event.get("messages", [])),
                        "inline": False,
                    },
                    {
                        "name": "Received Log",
                        "value": self._diagnostic_field(event.get("received_logs", [])),
                        "inline": False,
                    },
                    {
                        "name": "Event Matching",
                        "value": self._diagnostic_field(event.get("matching_logs", [])),
                        "inline": False,
                    },
                    {
                        "name": "Latest Payloads",
                        "value": self._diagnostic_field([
                            f"Stream: {event.get('latest_stream_payload', 'n/a')}",
                            f"CWA: {event.get('latest_cwa_payload', 'n/a')}",
                        ]),
                        "inline": False,
                    },
                    {
                        "name": "Delivery",
                        "value": self._diagnostic_field(event.get("delivery_logs", [])),
                        "inline": False,
                    },
                ],
                "footer": {"text": "EEW diagnostic • 時間為 Asia/Taipei"},
                "timestamp": datetime.now(TPE_TZ).isoformat(),
            }
            payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
            base_url = self.diagnostic_webhook_url.rstrip("/")
            message_id = event.get("webhook_message_id")
            try:
                if message_id:
                    async with session.patch(
                        f"{base_url}/messages/{message_id}", json=payload, timeout=10
                    ) as response:
                        if response.status == 404:
                            event.pop("webhook_message_id", None)
                        elif response.status in (200, 204):
                            return
                        else:
                            return
                async with session.post(
                    base_url, params={"wait": "true"}, json=payload, timeout=10
                ) as response:
                    if response.status in (200, 201):
                        result = await response.json(content_type=None)
                        if result.get("id"):
                            event["webhook_message_id"] = str(result["id"])
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass

    async def _handle_stream_message(self, raw_data):
        try:
            if isinstance(raw_data, bytes):
                raw_data = raw_data.decode("utf-8")
            if raw_data == "pong":
                return
            payload = json.loads(raw_data)
            if not isinstance(payload, dict):
                return

            origin_time = self._parse_eew_time(payload.get("originTime"))
            lat = float(payload.get("epicenterLat", payload.get("latitude")))
            lon = float(payload.get("epicenterLon", payload.get("longitude")))
            depth = float(payload.get("depth"))
            magnitude = float(payload.get("magnitude"))
            if origin_time is None:
                return
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return

        received = datetime.now(TPE_TZ)
        origin_ts = origin_time.timestamp()
        external_identifier = str(payload.get("identifier") or "") or None
        event_id, time_delta, distance = self._find_stream_event(
            origin_ts, lat, lon, external_identifier
        )
        is_new_event = event_id is None
        if is_new_event:
            event_id = f"realtime-{uuid.uuid4().hex}"
            event_code = origin_time.strftime("%Y%m%d-%H%M%S") + f"-{event_id[-4:].upper()}"
            self.stream_events[event_id] = {
                "event_code": event_code,
                "origin_ts": origin_ts,
                "latitude": lat,
                "longitude": lon,
                "magnitude": magnitude,
                "depth": depth,
                "external_identifier": external_identifier,
                "first_received": received.timestamp(),
                "last_received": received.timestamp(),
                "stream_sequence": 0,
                "messages": [],
                "received_logs": [],
                "matching_logs": [],
                "delivery_logs": [],
            }
            time_delta = 0.0
            distance = 0.0

        event = self.stream_events[event_id]
        fingerprint = json.dumps(
            [
                int(origin_ts * 1000),
                round(lat, 4),
                round(lon, 4),
                round(depth, 1),
                round(magnitude, 1),
            ],
            separators=(",", ":"),
        )
        recv_text = received.strftime("%H:%M:%S.%f")[:-3]
        if event.get("last_fingerprint") == fingerprint:
            self._append_diagnostic(
                event, "received_logs", f"[{recv_text}] [Stream] duplicate ignored", 18
            )
            event["last_received"] = received.timestamp()
            await self._refresh_diagnostic_webhook(event_id)
            return

        event["stream_sequence"] = int(event.get("stream_sequence", 0)) + 1
        sequence = event["stream_sequence"]
        event.update({
            "origin_ts": origin_ts,
            "latitude": lat,
            "longitude": lon,
            "magnitude": magnitude,
            "depth": depth,
            "last_received": received.timestamp(),
            "last_fingerprint": fingerprint,
            "latest_stream_payload": json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            )[:440],
        })

        message_time = self._parse_eew_time(payload.get("sendTime")) or origin_time
        self._append_diagnostic(
            event,
            "messages",
            f"🟢 S{sequence}.[{message_time.strftime('%H:%M:%S')}] "
            f"{lat:.2f}N {lon:.2f}E M{magnitude:.1f} {depth:g}km",
            14,
        )
        send_time = self._parse_eew_time(payload.get("sendTime"))
        transport = (
            f"{(received - send_time).total_seconds():.3f}s" if send_time else "n/a"
        )
        age = (received - origin_time).total_seconds()
        self._append_diagnostic(
            event,
            "received_logs",
            f"[{recv_text}] [Stream] S{sequence} transport={transport} age={age:.3f}s",
            18,
        )
        match_action = (
            f"new event (limits dt<={self.EVENT_MATCH_MAX_TIME_SECONDS}s "
            f"dist<={self.EVENT_MATCH_MAX_DISTANCE_KM}km)"
            if is_new_event
            else f"matched dt={time_delta:.2f}s dist={distance:.1f}km"
        )
        self._append_diagnostic(
            event, "matching_logs", f"[{recv_text}] Stream {match_action}", 12
        )
        await self._refresh_diagnostic_webhook(event_id)

        loop_time = asyncio.get_running_loop().time()
        self.api_polling_until = max(self.api_polling_until, loop_time + 120)
        self.rptes_polling_until = max(self.rptes_polling_until, loop_time + 330)
        self.last_api_time = 0
        self.last_rptes_time = 0

        normalized_alert = {
            "identifier": event_id,
            "msgNo": sequence,
            "originTime": origin_time.strftime("%Y-%m-%d %H:%M:%S"),
            "epicenterLat": lat,
            "epicenterLon": lon,
            "depth": depth,
            "magnitudeValue": magnitude,
            "locationDesc": [],
            "_event_id_override": event_id,
            "_source": "stream",
            "_hide_report_number": True,
            "_stream_fingerprint": fingerprint,
        }
        await self.process_eew_data([normalized_alert])

    def _coalesce_latest_cwa_alerts(self, alerts):
        latest_by_event = {}
        passthrough = []
        skipped_by_event = {}

        for index, alert in enumerate(alerts if isinstance(alerts, list) else []):
            if not isinstance(alert, dict):
                continue
            if alert.get("_source", "cwa") != "cwa":
                passthrough.append(alert)
                continue

            event_id = self._resolve_alert_event_id(alert) or f"unknown:{index}"
            msg_no = int(alert.get("msgNo", 1) or 1)
            current = latest_by_event.get(event_id)
            if current is None:
                latest_by_event[event_id] = alert
                continue

            current_msg_no = int(current.get("msgNo", 1) or 1)
            if msg_no >= current_msg_no:
                skipped_by_event.setdefault(event_id, []).append(current_msg_no)
                latest_by_event[event_id] = alert
            else:
                skipped_by_event.setdefault(event_id, []).append(msg_no)

        return passthrough + list(latest_by_event.values()), skipped_by_event

    async def _prepare_cwa_alerts(self, alerts):
        alerts, skipped_by_event = self._coalesce_latest_cwa_alerts(alerts)
        prepared = []
        for original in alerts:
            if not isinstance(original, dict):
                continue
            alert = dict(original)
            cwa_event_id = self._cwa_event_id(alert)
            linked_event_id = self.cwa_event_links.get(cwa_event_id)
            origin_time = self._parse_eew_time(alert.get("originTime"))
            try:
                lat = float(alert.get("epicenterLat"))
                lon = float(alert.get("epicenterLon"))
            except (TypeError, ValueError):
                lat = lon = None

            time_delta = distance = None
            if origin_time and lat is not None and lon is not None:
                if linked_event_id and linked_event_id in self.stream_events:
                    linked_event = self.stream_events[linked_event_id]
                    time_delta = abs(
                        float(linked_event.get("origin_ts", origin_time.timestamp()))
                        - origin_time.timestamp()
                    )
                    distance = self._epicenter_distance_km(
                        float(linked_event.get("latitude", lat)),
                        float(linked_event.get("longitude", lon)),
                        lat,
                        lon,
                    )
                elif not linked_event_id:
                    linked_event_id, time_delta, distance = self._find_stream_event(
                        origin_time.timestamp(), lat, lon
                    )
                    if linked_event_id:
                        self.cwa_event_links[cwa_event_id] = linked_event_id

            if linked_event_id and linked_event_id in self.stream_events:
                alert["_event_id_override"] = linked_event_id
                alert["_source"] = "cwa"
                event = self.stream_events[linked_event_id]
                event["latest_cwa_payload"] = json.dumps(
                    original, ensure_ascii=False, separators=(",", ":")
                )[:440]
                msg_no = int(alert.get("msgNo", 1) or 1)
                log_key = f"{cwa_event_id}:{msg_no}"
                logged = event.setdefault("logged_cwa_reports", [])
                if log_key not in logged:
                    logged.append(log_key)
                    del logged[:-20]
                    received = datetime.now(TPE_TZ)
                    recv_text = received.strftime("%H:%M:%S.%f")[:-3]
                    mag = float(alert.get("magnitudeValue", 0) or 0)
                    depth = float(alert.get("depth", 0) or 0)
                    origin_label = origin_time.strftime("%H:%M:%S") if origin_time else "--:--:--"
                    if lat is not None and lon is not None:
                        coordinate_label = f"{lat:.2f}N {lon:.2f}E"
                    else:
                        coordinate_label = "coordinate=n/a"
                    self._append_diagnostic(
                        event,
                        "messages",
                        f"🔵 C{msg_no}.[{origin_label}] "
                        f"{coordinate_label} M{mag:.1f} {depth:g}km",
                        14,
                    )
                    lag = received.timestamp() - float(
                        event.get("first_received", received.timestamp())
                    )
                    self._append_diagnostic(
                        event,
                        "received_logs",
                        f"[{recv_text}] [CWA] No.{msg_no} after-stream={lag:.3f}s",
                        18,
                    )
                    skipped_reports = sorted(set(skipped_by_event.get(cwa_event_id, [])))
                    if skipped_reports:
                        skipped_text = ",".join(str(number) for number in skipped_reports)
                        self._append_diagnostic(
                            event,
                            "matching_logs",
                            f"[{recv_text}] CWA coalesced skipped={skipped_text} "
                            f"kept={msg_no}",
                            12,
                        )
                    if time_delta is not None:
                        self._append_diagnostic(
                            event,
                            "matching_logs",
                            f"[{recv_text}] CWA linked dt={time_delta:.2f}s "
                            f"dist={distance:.1f}km id={cwa_event_id}",
                            12,
                        )
                    await self._refresh_diagnostic_webhook(linked_event_id)
            prepared.append(alert)
        return prepared

    @staticmethod
    def _stream_error_summary(error):
        if isinstance(error, aiohttp.ClientConnectorError):
            return "服務未啟動或拒絕連線"
        if isinstance(error, aiohttp.WSServerHandshakeError):
            return f"握手失敗（HTTP {error.status}）"
        if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
            return "連線逾時"
        return type(error).__name__

    async def listen_experimental_stream(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            session = getattr(self.bot, "session", None)
            if session is None or session.closed:
                await asyncio.sleep(5)
                continue

            ping_task = None
            try:
                async with session.ws_connect(
                    self.experimental_stream_url,
                    autoping=True,
                    timeout=aiohttp.ClientWSTimeout(ws_receive=60),
                ) as websocket:
                    if self.stream_connection_failed:
                        logger.info("✅ [EEW資料流] 已恢復連線")
                    elif not self.stream_has_connected:
                        logger.info("🔌 [EEW資料流] 已連線")
                    self.stream_connection_failed = False
                    self.stream_has_connected = True

                    async def send_text_pings():
                        while True:
                            await asyncio.sleep(30)
                            await websocket.send_str("ping")

                    ping_task = asyncio.create_task(send_text_pings())
                    async for message in websocket:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_stream_message(message.data)
                        elif message.type == aiohttp.WSMsgType.BINARY:
                            await self._handle_stream_message(message.data)
                        elif message.type == aiohttp.WSMsgType.ERROR:
                            raise websocket.exception() or RuntimeError(
                                "WebSocket 資料流發生錯誤"
                            )
                    if not self.bot.is_closed():
                        raise ConnectionError(
                            f"連線已關閉（code={websocket.close_code}）"
                        )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not self.stream_connection_failed:
                    logger.warning(
                        "⚠️ [EEW資料流] 連線中斷，將在背景持續重試："
                        f"{self._stream_error_summary(e)}"
                    )
                self.stream_connection_failed = True
            finally:
                if ping_task:
                    ping_task.cancel()
                    await asyncio.gather(ping_task, return_exceptions=True)

            if not self.bot.is_closed():
                await asyncio.sleep(5)

    @tasks.loop(seconds=1.0)
    async def rptes_loop(self):
        now = asyncio.get_running_loop().time()
        if now < self.rptes_polling_until:
            if now - self.last_rptes_time >= 30.0:
                self.last_rptes_time = now
                await self.poll_rptes_api()

    async def poll_rptes_api(self):
        if not self.rf_api_key or not self.bot.session:
            return
        url = f"https://rptes.com/api/RFEQ/pga?api_key={self.rf_api_key}"
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            async with self.bot.session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    raw = await resp.json()
                    st_list = raw.get("data", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                    
                    county_max = {}
                    now_ts = datetime.now(TPE_TZ).timestamp()
                    
                    for st in st_list:
                        if not isinstance(st, dict):
                            continue
                        cname = st.get("cname", "")
                        county = normalize_county_name(cname)
                        if not county and town_mapping_cache:
                            try:
                                st_lat, st_lon = float(st.get("lat")), float(st.get("lon"))
                                min_d = float('inf')
                                for _, items in town_mapping_cache.items():
                                    for item in items:
                                        fn, t_lat, t_lon = item[0], item[1], item[2]
                                        if t_lat is not None and t_lon is not None:
                                            d = (t_lat - st_lat)**2 + (t_lon - st_lon)**2
                                            if d < min_d:
                                                min_d = d
                                                county = normalize_county_name(fn)
                            except Exception:
                                pass
                                
                        if not county:
                            continue
                            
                        shindo_15 = st.get("shindo_15", 0)
                        rank, display_grade = parse_shindo_value(shindo_15)
                        if rank > 0:
                            if county not in county_max or rank > county_max[county][0]:
                                county_max[county] = (rank, display_grade)
                                
                    self.rptes_history.append((now_ts, county_max))
                    # 震度速報僅使用地震發生後兩分鐘內的觀測資料。
                    self.rptes_history = [entry for entry in self.rptes_history if now_ts - entry[0] <= 120]
        except Exception as e:
            logger.debug(f"RPTeS API 輪詢錯誤: {e!r}")

    @rptes_loop.before_loop
    async def before_rptes_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=1.0)
    async def eew_loop(self):
        if os.path.exists("alert.txt"):
            try:
                os.remove("alert.txt")
                loop_time = asyncio.get_running_loop().time()
                self.api_polling_until = loop_time + 120.0
                self.rptes_polling_until = loop_time + 330.0
                self.last_rptes_time = 0
                logger.info("🚨 [EEW警報] 偵測到 alert.txt，已啟動快速輪詢")
            except Exception as e:
                logger.error(f"❌ [EEW警報] 無法處理 alert.txt：{e!r}")
                
        now = asyncio.get_running_loop().time()
        if now < self.api_polling_until:
            if now - self.last_api_time >= 1.0:
                self.last_api_time = now
                await self.poll_api()

    async def poll_api(self):
        if not self.api_url or not self.bot.session:
            return
            
        try:
            async with self.bot.session.get(self.api_url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("success") and "data" in data:
                        alerts = await self._prepare_cwa_alerts(data["data"])
                        await self.process_eew_data(alerts)
        except Exception:
            pass

    @eew_loop.before_loop
    async def before_eew_loop(self):
        await self.bot.wait_until_ready()

    async def broadcast_image(
        self,
        event_id,
        revision,
        msg_no,
        mag,
        depth,
        lon,
        lat,
        fault_type,
        channels_needing_image,
        origin_time_str=None,
        show_report_number=True,
    ):
        try:
            await asyncio.sleep(5)
            
            current_revision = self.sent_alerts.get(event_id, {}).get("revision", 0)
            if revision < current_revision:
                return
                
            bytes_io = await asyncio.to_thread(
                render_emulator_map_pil,
                mag,
                depth,
                lon,
                lat,
                fault_type,
                msg_no,
                origin_time_str,
                show_report_number,
            )
            
            # 繪圖完成後再次檢查防競爭
            current_revision = self.sent_alerts.get(event_id, {}).get("revision", 0)
            if revision < current_revision:
                return
                
            # 取第一個頻道當圖床
            first_channel, first_msg_id, first_embed = channels_needing_image[0]
            file = discord.File(bytes_io, filename="emulator.png")
            first_embed.set_image(url="attachment://emulator.png")
            
            image_url = None
            try:
                first_msg = first_channel.get_partial_message(first_msg_id)
                await first_msg.edit(embed=first_embed, attachments=[file])
                
                # 重新 fetch 以取得真實 URL
                first_msg = await first_channel.fetch_message(first_msg_id)
                if first_msg.embeds and first_msg.embeds[0].image:
                    image_url = first_msg.embeds[0].image.url
                elif first_msg.attachments:
                    image_url = first_msg.attachments[0].url
            except Exception as e:
                logger.error(f"❌ [EEW警報] 上傳第一張圖片失敗：{e!r}")
                
            if not image_url or len(channels_needing_image) <= 1:
                return
                
            # 併發更新其他頻道的 Embed
            update_tasks = []
            for channel, msg_id, embed in channels_needing_image[1:]:
                embed.set_image(url=image_url)
                update_tasks.append(self.update_embed_only(channel, msg_id, embed))
                
            if update_tasks:
                await asyncio.gather(*update_tasks, return_exceptions=True)
                
        except Exception as e:
            logger.error(f"❌ [EEW警報] 圖片廣播失敗：{e!r}")

    async def update_embed_only(self, channel, msg_id, embed):
        try:
            msg = channel.get_partial_message(msg_id)
            await msg.edit(embed=embed)
        except Exception:
            pass

    async def send_or_edit_text(self, event_id, channel_id, content, embed, is_img_enabled):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception as e:
                logger.warning(f"⚠️ [EEW警報] 無法取得頻道 {channel_id}：{e!r}")
                return None
            
        msg_id = self.sent_alerts[event_id]["channel_msg_map"].get(channel_id)
        if msg_id:
            try:
                msg = channel.get_partial_message(msg_id)
                await msg.edit(content=content, embed=embed)
                guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                logger.debug(f"📢 [EEW警報] 已更新預警至 {guild_name} ({channel.name})")
                return (channel, msg_id, embed, is_img_enabled, "edit")
            except discord.NotFound:
                pass
            except Exception as e:
                logger.warning(f"⚠️ [EEW警報] 更新訊息至頻道 {channel_id} 失敗：{e!r}")
                return None
                
        try:
            if hasattr(self.bot, 'is_abnormal_grace_period') and self.bot.is_abnormal_grace_period():
                logger.info(f"⏭️ [系統] 異常啟動期間，略過發送通知至 {channel.name}")
                return None
            else:
                msg = await safe_channel_send(channel, context="EEW警報", content=content, embed=embed)
                if msg is None:
                    return None
            self.sent_alerts[event_id]["channel_msg_map"][channel_id] = msg.id
            guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
            logger.debug(f"📢 [EEW警報] 已發送預警至 {guild_name} ({channel.name})")
            return (channel, msg.id, embed, is_img_enabled, "send")
        except Exception as e:
            logger.warning(f"⚠️ [EEW警報] 發送訊息至頻道 {channel_id} 失敗：{e!r}")
            return None

    def _resolve_alert_event_id(self, alert):
        override = alert.get("_event_id_override")
        if override:
            return str(override)
        return self._cwa_event_id(alert)

    async def process_eew_data(self, alerts_data):
        if not isinstance(alerts_data, list):
            return
        valid_alerts = [alert for alert in alerts_data if isinstance(alert, dict)]
        valid_alerts, _ = self._coalesce_latest_cwa_alerts(valid_alerts)
        for alert in sorted(valid_alerts, key=lambda item: item.get("msgNo", 1)):
            event_id = self._resolve_alert_event_id(alert)
            if not event_id:
                continue
            lock = self.event_processing_locks.setdefault(event_id, asyncio.Lock())
            async with lock:
                await self._process_eew_data_locked([alert])

    async def _process_eew_data_locked(self, alerts_data):
        now = datetime.now(TPE_TZ)
        settings = get_all_settings()
        
        # 確保 alerts_data 依發報報數 (msgNo) 升冪排序處理
        if isinstance(alerts_data, list):
            alerts_data = sorted(alerts_data, key=lambda x: x.get("msgNo", 1))
            
        # 清理超過 2 小時的舊 alert 紀錄，避免記憶體與快取無限增大
        expired_events = []
        for eid, info in self.sent_alerts.items():
            last_time = info.get("last_updated", 0)
            if now.timestamp() - last_time > 7200:
                expired_events.append(eid)
        for eid in expired_events:
            del self.sent_alerts[eid]
            self.stream_events.pop(eid, None)
            self.diagnostic_locks.pop(eid, None)
        if expired_events:
            expired_set = set(expired_events)
            self.cwa_event_links = {
                cwa_id: linked_id
                for cwa_id, linked_id in self.cwa_event_links.items()
                if linked_id not in expired_set
            }

        for alert in alerts_data:
            msg_no = int(alert.get("msgNo", 1) or 1)
            event_id = self._resolve_alert_event_id(alert)
            source = alert.get("_source", "cwa")
            hide_report_number = bool(alert.get("_hide_report_number", False))
            stream_fingerprint = alert.get("_stream_fingerprint")
            state = self.sent_alerts.get(event_id)
            processing_started = asyncio.get_running_loop().time()

            if state:
                if source == "stream":
                    if stream_fingerprint and stream_fingerprint == state.get("stream_fingerprint"):
                        continue
                else:
                    legacy_cwa_msg_no = 0 if state.get("stream_only") else state.get("msgNo", 0)
                    last_cwa_msg_no = state.get("cwa_msg_no", legacy_cwa_msg_no)
                    if msg_no <= last_cwa_msg_no:
                        continue
                
            origin_time_str = alert.get("originTime")
            if not origin_time_str:
                continue
                
            try:
                origin_time = datetime.strptime(origin_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TPE_TZ)
                # 若發報時間已經超過 300 秒，則直接靜默忽略舊資料，不中斷 120 秒的輪詢
                if (now - origin_time).total_seconds() > 300:
                    continue
            except Exception:
                pass
                
            mag = alert.get("magnitudeValue", 0.0)
            depth = alert.get("depth", 0.0)
            lon = alert.get("epicenterLon", 0.0)
            lat = alert.get("epicenterLat", 0.0)
            
            is_subduction = False
            if depth > 35 and ((lat > 23.5 and lon > 121.5) or (lat < 23.0 and lon < 121.5) or depth > 45):
                is_subduction = True
            
            loc_intensity_cache = {}

            if event_id not in self.sent_alerts:
                nearest_town_for_all = "臺北市"
                if town_mapping_cache:
                    min_dist = float('inf')
                    for _, items in town_mapping_cache.items():
                        for item in items:
                            fullname, t_lat, t_lon = item[0], item[1], item[2]
                            if t_lat is not None and t_lon is not None:
                                dx = (lon - t_lon) * 111 * math.cos(math.radians((lat + t_lat) / 2))
                                dy = (lat - t_lat) * 111
                                dist = math.hypot(dx, dy)
                                if dist < min_dist:
                                    min_dist = dist
                                    nearest_town_for_all = fullname
                                    
                self.sent_alerts[event_id] = {
                    "msgNo": msg_no if source == "cwa" else 0,
                    "cwa_msg_no": msg_no if source == "cwa" else 0,
                    "stream_fingerprint": stream_fingerprint,
                    "stream_only": source == "stream",
                    "revision": 1,
                    "channel_msg_map": {},
                    "nearest_town_for_all": nearest_town_for_all,
                    "last_updated": now.timestamp()
                }
            else:
                event_state = self.sent_alerts[event_id]
                event_state["revision"] = int(event_state.get("revision", 0)) + 1
                event_state["last_updated"] = now.timestamp()
                if source == "cwa":
                    event_state["msgNo"] = msg_no
                    event_state["cwa_msg_no"] = msg_no
                    event_state["stream_only"] = False
                else:
                    event_state["stream_fingerprint"] = stream_fingerprint

            update_revision = self.sent_alerts[event_id]["revision"]

            # 1. 預先收集所有授權伺服器訂閱的不重複區域
            unique_locations = set()
            for guild_id_str, g_settings in settings.items():
                if not g_settings.get("eew_authorized", False):
                    continue
                eew_alerts = g_settings.get("eew_alerts", {})
                if not isinstance(eew_alerts, dict):
                    continue
                for original_loc in eew_alerts.keys():
                    if original_loc == "全台接收":
                        loc = self.sent_alerts[event_id].get("nearest_town_for_all", "臺北市")
                    else:
                        loc = original_loc
                    unique_locations.add(loc)

            # 連動 status.py：若有授權伺服器開啟 EEW 則更新機器人狀態持續 3 分鐘
            if unique_locations:
                status_cog = self.bot.get_cog("Status")
                if status_cog and hasattr(status_cog, "set_eew_alert"):
                    nearest_town = self.sent_alerts[event_id].get("nearest_town_for_all", "臺北市")
                    status_cog.set_eew_alert(nearest_town, mag)

            # 2. 預先批次計算不重複區域的震度與 S 波抵達時間
            load_vs30_grid()
            for loc in unique_locations:
                if len(loc) >= 6:
                    county = loc[:3]
                    town = loc[3:]
                else:
                    county = loc
                    town = ""
                    
                target_lon, target_lat = lon, lat
                t_site = None
                if town_mapping_cache and loc in town_mapping_cache:
                    for item in town_mapping_cache[loc]:
                        if item[0] == loc and item[1] is not None and item[2] is not None:
                            target_lat, target_lon = item[1], item[2]
                            if len(item) > 3:
                                t_site = item[3]
                            break
                            
                vs30 = get_vs30(county, town, target_lon, target_lat)
                pga, pgv = simulate_gm(mag, depth, lon, lat, "逆斷層", target_lon, target_lat, is_subduction, vs30, site_factor=t_site)
                cdi = calc_cdi(pga, pgv)
                
                if cdi < 0.35: int_grade = 0
                elif cdi < 1.10: int_grade = 1
                elif cdi < 1.90: int_grade = 2
                elif cdi < 2.80: int_grade = 3
                elif cdi < 3.70: int_grade = 4
                elif cdi < 4.85: int_grade = 5 
                elif cdi < 6.30: int_grade = 6 
                else: int_grade = 7
                
                col, display_grade, _ = cdi_style(cdi)
                
                dx = (lon - target_lon) * 111 * math.cos(math.radians((lat + target_lat) / 2))
                dy = (lat - target_lat) * 111
                epi_dist_km = math.hypot(dx, dy)
                
                s_wave_travel_time = tt.travel_times(depth_km=depth, epicentral_km=epi_dist_km)['S']
                s_wave_arrival_time = origin_time + timedelta(seconds=s_wave_travel_time)
                s_ts = int(s_wave_arrival_time.timestamp())
                
                loc_intensity_cache[loc] = (int_grade, display_grade, col, s_ts)

            text_dispatch_tasks = []

            for guild_id_str, g_settings in settings.items():
                if not g_settings.get("eew_authorized", False):
                    continue
                eew_alerts = g_settings.get("eew_alerts", {})
                if not isinstance(eew_alerts, dict):
                    continue
                is_img_enabled = g_settings.get("eew_image_enabled", False)
                
                # 先將同一個頻道的地區群組起來
                channel_groups = {}
                for loc, loc_data in eew_alerts.items():
                    if isinstance(loc_data, dict):
                        channel_id = loc_data.get("channel_id")
                    elif isinstance(loc_data, (int, str)) and not isinstance(loc_data, bool) and str(loc_data).isdigit():
                        channel_id = loc_data
                    else:
                        continue

                    if channel_id:
                        channel_groups.setdefault(channel_id, []).append((loc, loc_data))
                
                for channel_id, locs in channel_groups.items():
                    valid_locs = []
                    max_int_grade = -1
                    max_col = (255, 255, 255)
                    min_s_ts = float('inf')
                    
                    is_already_notified = channel_id in self.sent_alerts[event_id]["channel_msg_map"]
                    
                    for original_loc, loc_data in locs:
                        loc = original_loc
                        display_loc = original_loc
                        if loc == "全台接收":
                            loc = self.sent_alerts[event_id].get("nearest_town_for_all", "臺北市")
                            display_loc = "全台接收"
                            
                        min_mag = loc_data.get("min_magnitude", 4.5)
                        min_mag = max(4.5, min_mag)
                        min_int = loc_data.get("min_intensity", 3)
                        
                        if not is_already_notified:
                            if mag < min_mag:
                                continue
                                
                            if loc not in loc_intensity_cache:
                                continue
                                
                            int_grade, display_grade, col, s_ts = loc_intensity_cache[loc]
                            if int_grade < min_int:
                                continue
                        else:
                            if loc not in loc_intensity_cache:
                                continue
                            int_grade, display_grade, col, s_ts = loc_intensity_cache[loc]
                            
                        valid_locs.append((display_loc, int_grade, display_grade, col, s_ts))
                        if s_ts < min_s_ts:
                            min_s_ts = s_ts
                            
                    if not valid_locs:
                        if is_already_notified:
                            # 若已發送過該事件通知，但後續報數無有效符合項（如震度微幅調降），強制帶入近地資料更新報數
                            nearest_town_for_all = self.sent_alerts[event_id].get("nearest_town_for_all", "臺北市")
                            if nearest_town_for_all in loc_intensity_cache:
                                int_grade, display_grade, col, s_ts = loc_intensity_cache[nearest_town_for_all]
                                valid_locs.append((nearest_town_for_all, int_grade, display_grade, col, s_ts))
                                min_s_ts = s_ts
                        if not valid_locs:
                            continue
                        
                    # 依據規模與這群區域中的最大震度計算 Embed 顏色
                    GRADE_TO_FLOAT = {
                        '0': 0.0, '1': 1.0, '2': 2.0, '3': 3.0, '4': 4.0,
                        '5弱': 5.0, '5強': 5.5, '6弱': 6.0, '6強': 6.5, '7': 7.0
                    }
                    max_int_val = 0.0
                    for v_loc, v_int_grade, v_display, v_col, v_ts in valid_locs:
                        val = GRADE_TO_FLOAT.get(str(v_display), 0.0)
                        if val > max_int_val:
                            max_int_val = val
                            
                    embed_color = get_eq_color(mag, max_int_val)
                    embed = discord.Embed(description="", color=embed_color)
                    mag_emoji = get_mag_emoji(mag)
                    depth_emoji = get_depth_emoji(depth, mag)

                    embed.add_field(name=f"{mag_emoji} 規模", value=f"{float(mag):.1f}", inline=True)
                    embed.add_field(name=f"{depth_emoji} 深度", value=f"{depth} 公里", inline=True)
                    
                    if len(valid_locs) == 1:
                        d_loc, _, display_grade, _, s_ts = valid_locs[0]
                        full_grade = format_fullwidth_grade(display_grade)
                        if d_loc == "全台接收":
                            nearest_town_for_all = self.sent_alerts[event_id].get("nearest_town_for_all", "臺北市")
                            content = f"🚨 地震速報 規模 {float(mag):.1f}\n預估**{full_grade}** ({nearest_town_for_all})"
                        else:
                            content = f"🚨 地震速報 規模 {float(mag):.1f}\n預估**{full_grade}** ({d_loc})"
                        embed.add_field(name="⚠️ 抵達", value=f"<t:{s_ts}:R>", inline=True)
                    else:
                        content = f"🚨 地震速報 規模 {float(mag):.1f}"
                        embed.add_field(name="⚠️ 抵達 (最快)", value=f"<t:{min_s_ts}:R>", inline=True)
                        
                    mention_role_id = g_settings.get('eew_mention_role_id')
                    if mention_role_id:
                        content += f" <@&{mention_role_id}>"
                        
                    ts = int(origin_time.timestamp())
                    embed.add_field(name="發生時間", value=f"<t:{ts}:f>", inline=True)
                    
                    loc_desc = alert.get("locationDesc", [])
                    epi_name_api = loc_desc[0] if loc_desc else "未知"
                    epi_name = get_epicenter_name(lon, lat, epi_name_api)
                    embed.add_field(name="震央", value=epi_name, inline=True)
                    
                    if len(valid_locs) > 1:
                        loc_strings = []
                        def get_eew_sort_key(item_with_idx):
                            idx, (d_loc, _, _, _, _) = item_with_idx
                            if d_loc == "全台接收":
                                return (0, 0, idx)
                            return (1, get_county_order(d_loc), idx)
                        
                        sorted_valid_locs = [item for _, item in sorted(enumerate(valid_locs), key=get_eew_sort_key)]
                        for d_loc, _, display_grade, _, s_ts in sorted_valid_locs:
                            full_grade = format_fullwidth_grade(display_grade)
                            int_emoji = get_intensity_emoji(display_grade)
                            if d_loc == "全台接收":
                                nearest_town_for_all = self.sent_alerts[event_id].get("nearest_town_for_all", "臺北市")
                                loc_strings.append(f"`{int_emoji}` **{full_grade} {nearest_town_for_all}** | <t:{s_ts}:R> (震央最近區域)")
                            else:
                                loc_strings.append(f"`{int_emoji}` **{full_grade} {d_loc}** | <t:{s_ts}:R>")
                        embed.add_field(name="預估震度", value="\n".join(loc_strings), inline=False)

                    if hide_report_number:
                        embed.set_footer(text=f"地震速報 • 接收時間 {now.strftime('%H:%M:%S')}")
                    else:
                        embed.set_footer(
                            text=f"中央氣象署 • 接收時間 {now.strftime('%H:%M:%S')} (第 {msg_no} 報)",
                            icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png",
                        )
                    
                    text_dispatch_tasks.append(
                        (max_int_val, min_s_ts, self.send_or_edit_text(event_id, channel_id, content, embed, is_img_enabled))
                    )
            
            if not text_dispatch_tasks:
                if event_id in self.stream_events:
                    diagnostic_event = self.stream_events[event_id]
                    recv_text = datetime.now(TPE_TZ).strftime("%H:%M:%S.%f")[:-3]
                    self._append_diagnostic(
                        diagnostic_event,
                        "delivery_logs",
                        f"[{recv_text}] [{source}] no eligible channel "
                        f"locations={len(unique_locations)}",
                        14,
                    )
                    await self._refresh_diagnostic_webhook(event_id)
                continue
                
            # 依據「最高預估震度（由大到小）」與「S波最快抵達時間（由小到大）」進行優先級排序
            text_dispatch_tasks.sort(key=lambda item: (-item[0], item[1]))
            tasks = [item[2] for item in text_dispatch_tasks]
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            channels_needing_image = []
            sent_cnt = 0
            updated_cnt = 0
            for res in results:
                if isinstance(res, tuple) and res is not None:
                    channel, msg_id, embed, img_enabled, action = res
                    if action == "send":
                        sent_cnt += 1
                    elif action == "edit":
                        updated_cnt += 1
                    if img_enabled:
                        channels_needing_image.append((channel, msg_id, embed))
                        
            if sent_cnt > 0 or updated_cnt > 0:
                report_label = f"第 {msg_no} 報" if not hide_report_number else "即時更新"
                logger.info(
                    f"📢 [EEW警報] {report_label}廣播完成 (規模 {float(mag):.1f}) | "
                    f"新發送 {sent_cnt} 個頻道 | 更新 {updated_cnt} 個頻道"
                )

            if event_id in self.stream_events:
                diagnostic_event = self.stream_events[event_id]
                recv_text = datetime.now(TPE_TZ).strftime("%H:%M:%S.%f")[:-3]
                elapsed_ms = (
                    asyncio.get_running_loop().time() - processing_started
                ) * 1000
                failures = sum(isinstance(result, Exception) or result is None for result in results)
                self._append_diagnostic(
                    diagnostic_event,
                    "delivery_logs",
                    f"[{recv_text}] [{source}] sent={sent_cnt} edited={updated_cnt} "
                    f"failed={failures} targets={len(tasks)} process={elapsed_ms:.1f}ms",
                    14,
                )
                await self._refresh_diagnostic_webhook(event_id)

            if channels_needing_image:
                asyncio.create_task(
                    self.broadcast_image(
                        event_id,
                        update_revision,
                        msg_no,
                        mag,
                        depth,
                        lon,
                        lat,
                        "逆斷層",
                        channels_needing_image,
                        origin_time_str,
                        not hide_report_number,
                    )
                )

            # 收集開了 EEW 且開了震度速報且達到發送門檻的頻道
            quick_report_channels = set()
            for guild_id_str, g_settings in settings.items():
                if not g_settings.get("eew_authorized", False):
                    continue
                if not g_settings.get("eew_quick_report_enabled", False):
                    continue
                eew_alerts = g_settings.get("eew_alerts", {})
                if not isinstance(eew_alerts, dict):
                    continue
                for loc, loc_data in eew_alerts.items():
                    ch_id = loc_data.get("channel_id") if isinstance(loc_data, dict) else (loc_data if isinstance(loc_data, (int, str)) and str(loc_data).isdigit() else None)
                    if ch_id and int(ch_id) in self.sent_alerts[event_id]["channel_msg_map"]:
                        quick_report_channels.add(int(ch_id))

            if quick_report_channels and event_id not in self.pending_quick_reports:
                self.pending_quick_reports.add(event_id)
                asyncio.create_task(self.schedule_quick_report(event_id, origin_time, list(quick_report_channels)))

    async def schedule_quick_report(self, event_id, origin_time, target_channel_ids):
        try:
            # 在 EEW 發布的 2 分鐘 (120 秒) 後推送震度速報。
            await asyncio.sleep(120)
            
            event_ts = origin_time.timestamp()
            now_ts = datetime.now(TPE_TZ).timestamp()
            
            window_snapshots = [
                county_max for snap_ts, county_max in self.rptes_history
                if event_ts <= snap_ts <= event_ts + 120
            ]
            
            if not window_snapshots and self.rf_api_key and self.bot.session:
                try:
                    url = f"https://rptes.com/api/RFEQ/pga?api_key={self.rf_api_key}"
                    async with self.bot.session.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5) as resp:
                        if resp.status == 200:
                            raw = await resp.json()
                            st_list = raw.get("data", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                            c_max = {}
                            for st in st_list:
                                if isinstance(st, dict):
                                    county = normalize_county_name(st.get("cname", ""))
                                    if county:
                                        r, dg = parse_shindo_value(st.get("shindo_15", 0))
                                        if r > 0 and (county not in c_max or r > c_max[county][0]):
                                            c_max[county] = (r, dg)
                            window_snapshots.append(c_max)
                except Exception:
                    pass

            summary_county_max = {}
            overall_max_rank = 0
            
            for snap in window_snapshots:
                for county, (rank, display_grade) in snap.items():
                    if rank > overall_max_rank:
                        overall_max_rank = rank
                    if county not in summary_county_max or rank > summary_county_max[county][0]:
                        summary_county_max[county] = (rank, display_grade)
                        
            # 如果有 4 級以上 (rank >= 4)，以兩分鐘內資料統計出 3 級以上的縣市最大震度。
            if overall_max_rank < 4:
                logger.info(f"ℹ️ [震度速報] 事件 {event_id} 2 分鐘內未記錄到 4 級以上震度 (最高 rank {overall_max_rank})，略過發送。")
                return
                
            filtered_counties = {
                c: info for c, info in summary_county_max.items() if info[0] >= 3
            }
            
            if not filtered_counties:
                return
                
            rank_groups = {}
            for county, (rank, display_grade) in filtered_counties.items():
                rank_groups.setdefault(rank, []).append(county)
                
            sorted_ranks = sorted(rank_groups.keys(), reverse=True)
            
            content = "🏘️ 震度速報 (RPTeS)"
            embed = discord.Embed(color=0xE67E22)
            
            ts_int = int(origin_time.timestamp())
            embed.description = f"<t:{ts_int}:f> 發生了震度４級以上的地震。"
            
            for r in sorted_ranks:
                counties = rank_groups[r]
                sorted_counties = sorted(counties, key=get_county_order)
                counties_str = "、".join(sorted_counties)
                
                sample_county = counties[0]
                sample_grade = filtered_counties[sample_county][1]
                fullwidth_intensity = format_fullwidth_grade(sample_grade)
                
                embed.add_field(name=f"震度{fullwidth_intensity}", value=counties_str, inline=False)
                
            embed.set_footer(text="RPTeS 震度速報", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/rptes_logo.png")

            sent_cnt = 0
            for ch_id in target_channel_ids:
                try:
                    channel = self.bot.get_channel(ch_id)
                    if not channel:
                        channel = await self.bot.fetch_channel(ch_id)
                    if channel:
                        msg = await safe_channel_send(channel, context="震度速報", content=content, embed=embed)
                        if msg is not None:
                            sent_cnt += 1
                        guild_name = channel.guild.name if getattr(channel, "guild", None) else "未知伺服器"
                        logger.debug(f"🏠 [震度速報] 已發送震度速報至 {guild_name} ({channel.name})")
                except Exception as e:
                    logger.warning(f"⚠️ [震度速報] 發送震度速報至頻道 {ch_id} 失敗: {e!r}")

            if sent_cnt > 0:
                logger.info(f"🏠 [震度速報] 廣播完成 | 共發送 {sent_cnt} 個頻道")
                    
        except Exception as e:
            logger.error(f"❌ [震度速報] 處理事件 {event_id} 速報失敗: {e!r}")
        finally:
            self.pending_quick_reports.discard(event_id)

async def setup(bot):
    await bot.add_cog(EEWAlertCog(bot))
