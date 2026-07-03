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
import math
import uuid
import io
from PIL import Image, ImageDraw, ImageFont
TOPO_LOCAL = 'maps/towns-mercator-10t.json'

def load_topo():
    with open(TOPO_LOCAL, 'r', encoding='utf-8') as f:
        return json.load(f)

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

def get_vs30(county, town):
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

def simulate_gm(mag, depth, lon, lat, fault_type, target_lon, target_lat, is_subduction, vs30):
    D = distance(lat, lon, target_lat, target_lon)
    R = math.sqrt(D**2 + depth**2)
    R = max(R, 3.0)
    
    # 基本的 GMPE (Ground Motion Prediction Equation) 架構
    if is_subduction:
        # 隱沒帶地震震度較小，但衰減較慢（有感範圍廣）
        log_pga = -0.25 + 0.6 * mag - 0.003 * R - 0.9 * math.log10(R)
        log_pgv = -1.60 + 0.65 * mag - 0.002 * R - 0.9 * math.log10(R)
    else:
        # 淺層地殼地震
        log_pga = -0.01 + 0.6 * mag - 0.005 * R - 1.0 * math.log10(R)
        log_pgv = -1.4 + 0.65 * mag - 0.004 * R - 1.0 * math.log10(R)
        
    if fault_type == '逆斷層':
        log_pga += 0.1
        log_pgv += 0.1
    elif fault_type == '正斷層':
        log_pga -= 0.05
        log_pgv -= 0.05
        
    pga_rock = 10 ** log_pga
    pgv_rock = 10 ** log_pgv
    
    # 依據 Vs30 進行場址放大 (簡單的 NERHP 公式)
    amp_pga = (vs30 / 760.0) ** -0.3
    amp_pgv = (vs30 / 760.0) ** -0.6
    
    # 限制放大倍率，避免軟弱地盤無限放大
    amp_pga = min(max(amp_pga, 0.5), 2.5)
    amp_pgv = min(max(amp_pgv, 0.5), 3.5)
    
    return pga_rock * amp_pga, pgv_rock * amp_pgv

def get_epicenter_name(lon, lat, original_name):
    if not town_mapping_cache:
        return original_name
        
    min_dist = float('inf')
    nearest_town = None
    nearest_dx = 0
    nearest_dy = 0
    
    for _, items in town_mapping_cache.items():
        for fullname, t_lat, t_lon in items:
            if t_lat is not None and t_lon is not None:
                # 等距圓柱投影估算 (1度約111公里)
                dx = (lon - t_lon) * 111 * math.cos(math.radians((lat + t_lat) / 2))
                dy = (lat - t_lat) * 111
                dist = math.hypot(dx, dy)
                if dist < min_dist:
                    min_dist = dist
                    nearest_town = fullname
                    nearest_dx = dx
                    nearest_dy = dy
                    
    if nearest_town is None:
        return original_name
        
    if min_dist < 5:
        return nearest_town
        
    # 計算方位角 (atan2: x正為東, y正為北)
    angle = math.degrees(math.atan2(nearest_dy, nearest_dx))
    dirs = ["東", "東北", "北", "西北", "西", "西南", "南", "東南"]
    idx = round(angle / 45) % 8
    direction = dirs[idx]
    
    return f"{nearest_town}{direction}方 {min_dist:.1f} 公里"

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
        return 2.80 + (pga - 25.0) / (80.0 - 25.0) * (3.70 - 2.80)
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

def render_emulator_map_pil(mag, depth, lon, lat, fault_type, msg_no=1, origin_time_str=None):
    topo = load_topo()
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
        
        # 為了計算震度距離，取這區塊原始經緯度的中心 (用轉換後的 X,Y 也可以反推)
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
        
        real_cx = min_x + (119.5664 - WGS_MIN_LON) / (WGS_MAX_LON - WGS_MIN_LON) * (max_x - min_x)
        my = merc_y(23.5711)
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
                pt = line[0]
                is_kinmen = kinmen_x_list and (min(kinmen_x_list)-5 <= pt[0] <= max(kinmen_x_list)+5) and (min(kinmen_y_list)-5 <= pt[1] <= max(kinmen_y_list)+5)
                is_matsu = matsu_x_list and (min(matsu_x_list)-5 <= pt[0] <= max(matsu_x_list)+5) and (min(matsu_y_list)-5 <= pt[1] <= max(matsu_y_list)+5)
                if is_kinmen or is_matsu:
                    continue
                    
                is_penghu = penghu_x_list and (min(penghu_x_list)-5 <= pt[0] <= max(penghu_x_list)+5) and (min(penghu_y_list)-5 <= pt[1] <= max(penghu_y_list)+5)
                if is_penghu:
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

    def lonlat_to_img(lon, lat):
        x = min_x + (lon - WGS_MIN_LON) / (WGS_MAX_LON - WGS_MIN_LON) * (max_x - min_x)
        my = merc_y(lat)
        my_max = merc_y(WGS_MAX_LAT)
        my_min = merc_y(WGS_MIN_LAT)
        y = min_y + (my_max - my) / (my_max - my_min) * (max_y - min_y)
        return map_to_img(x, y)
    
    def img_to_lonlat(x, y):
        mx = (x - pad_left) / scale_factor + img_min_x
        my = (y - pad_top) / scale_factor + img_min_y
        lon = (mx - min_x) / (max_x - min_x) * (WGS_MAX_LON - WGS_MIN_LON) + WGS_MIN_LON
        merc_y_lat = my_max - (my - min_y) * (merc_y(WGS_MAX_LAT) - merc_y(WGS_MIN_LAT)) / (max_y - min_y)
        lat = (math.atan(math.exp(merc_y_lat)) - math.pi/4) * 360 / math.pi
        return lon, lat

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
                
    img = img_os.resize((IMG_W, IMG_H), Image.LANCZOS)
    draw = ImageDraw.Draw(img)

    font_paths = [
        "fonts\\Noto_Sans_TC\\NotoSansTC-Regular.ttf",
        "PingFang.ttc",
        "C:\\Windows\\Fonts\\msjh.ttc",
        "msjh.ttc"
    ]
    font_title = font_time = font_intensity = font_legend = font_legend_title = font_watermark_1 = font_watermark_2 = None
    for path in font_paths:
        try:
            font_title = ImageFont.truetype(path, 48)
            font_time = ImageFont.truetype(path, 26)
            font_intensity = ImageFont.truetype(path, 14)
            font_legend = ImageFont.truetype(path, 20)
            font_legend_title = ImageFont.truetype(path, 22)
            break
        except Exception:
            continue
            
    if font_title is None:
        font_title = ImageFont.load_default()
        font_time = ImageFont.load_default()
        font_intensity = ImageFont.load_default()
        font_legend = ImageFont.load_default()
        font_legend_title = ImageFont.load_default()

    # 繪製左上角標題
    draw.text((25, 25), " 強震即時警報", fill="#ffffff", font=font_title)
    draw.multiline_text((35, 90), f"規模 {mag}  |  深度 {depth}km\n第 {msg_no} 報", fill="#cccccc", font=font_time, spacing=8)

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
    for item in lines:
        cx, cy = item['orig_cx'], item['orig_cy']
        px, py = map_to_img(cx, cy)
        if item['county'] == '澎湖縣':
            px, py = map_to_img(cx + penghu_offset_x, cy + penghu_offset_y)
        
        # 還原到 WGS84 經緯度進行距離計算
        my_max = merc_y(WGS_MAX_LAT)
        my_min = merc_y(WGS_MIN_LAT)
        t_lon = WGS_MIN_LON + (cx - min_x) / (max_x - min_x) * (WGS_MAX_LON - WGS_MIN_LON) if max_x > min_x else 0
        
        # 此處採用簡單反推
        try:
            merc_y_lat = my_max - (cy - min_y) * (my_max - my_min) / (max_y - min_y)
            t_lat = (math.atan(math.exp(merc_y_lat)) - math.pi/4) * 360 / math.pi
        except Exception:
            t_lat = lat
        
        vs30 = get_vs30(item['county'], item['town'])
        pga, pgv = simulate_gm(mag, depth, lon, lat, "逆斷層", t_lon, t_lat, is_subduction, vs30)
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
        text_col = '#1a1a1a' if cdi < 5.55 else 'white'
        draw.text((px, py), grade_str, fill=text_col, font=font_intensity, anchor="mm")

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
    leg_w = 160
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
        iy = leg_y + leg_h - 30 - i * 30
        draw.ellipse((leg_x + 20, iy + 4, leg_x + 36, iy + 20), fill=col, outline='#404040', width=1)
        draw.text((leg_x + 50, iy + 12), str(grade), fill="#e5e5e5", font=font_legend, anchor="lm")
        draw.text((leg_x + 100, iy + 12), str(label), fill="#888888", font=font_legend, anchor="lm")

    output = io.BytesIO()
    img.save(output, format='PNG')
    output.seek(0)
    
    # 產生檔名回傳
    out_file = f'emulator_{uuid.uuid4().hex[:8]}.png'
    with open(out_file, 'wb') as f:
        f.write(output.read())
        
    return out_file

class EEWAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = {}
        self.api_url = ""
        cache = load_cache()
        self.sent_alerts = cache.get("eew_sent_alerts", {})
        
        self.load_config()
        self.eew_loop.start()

    def save_state(self):
        return {"eew_sent_alerts": self.sent_alerts}

    def load_config(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                self.config = json.load(f)
                self.api_url = self.config.get("CWA_EEW_AUTH", "")
        except Exception as e:
            logger.error(f"無法讀取 config.json: {e}")

    def cog_unload(self):
        self.eew_loop.cancel()

    @tasks.loop(seconds=2.0)
    async def eew_loop(self):
        if not self.api_url:
            return
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.api_url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("success") and "data" in data:
                            await self.process_eew_data(data["data"])
        except Exception:
            pass 

    @eew_loop.before_loop
    async def before_eew_loop(self):
        await self.bot.wait_until_ready()

    async def broadcast_image(self, event_id, msg_no, mag, depth, lon, lat, fault_type, channels_needing_image, origin_time_str=None):
        try:
            await asyncio.sleep(10)
            
            current_msg_no = self.sent_alerts.get(event_id, {}).get("msgNo", 0)
            if msg_no < current_msg_no:
                return
                
            out_file = await asyncio.to_thread(render_emulator_map_pil, mag, depth, lon, lat, fault_type, msg_no, origin_time_str)
            
            # 繪圖完成後再次檢查防競爭
            current_msg_no = self.sent_alerts.get(event_id, {}).get("msgNo", 0)
            if msg_no < current_msg_no:
                if os.path.exists(out_file):
                    os.remove(out_file)
                return
                
            # 取第一個頻道當圖床
            first_channel, first_msg_id, first_embed = channels_needing_image[0]
            file = discord.File(out_file, filename="emulator.png")
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
                logger.error(f"上傳第一張圖片失敗: {e}")
                
            if os.path.exists(out_file):
                os.remove(out_file)
                
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
            logger.error(f"EEW Image Broadcast Error: {e}")

    async def update_embed_only(self, channel, msg_id, embed):
        try:
            msg = channel.get_partial_message(msg_id)
            await msg.edit(embed=embed)
        except Exception:
            pass

    async def send_or_edit_text(self, event_id, channel_id, content, embed, is_img_enabled):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return None
            
        msg_id = self.sent_alerts[event_id]["channel_msg_map"].get(channel_id)
        if msg_id:
            try:
                msg = channel.get_partial_message(msg_id)
                await msg.edit(content=content, embed=embed)
                return (channel, msg_id, embed, is_img_enabled)
            except discord.NotFound:
                pass
            except Exception:
                return None
                
        try:
            msg = await channel.send(content=content, embed=embed)
            self.sent_alerts[event_id]["channel_msg_map"][channel_id] = msg.id
            return (channel, msg.id, embed, is_img_enabled)
        except Exception:
            return None

    async def process_eew_data(self, alerts_data):
        now = datetime.now(TPE_TZ)
        settings = get_all_settings()
        
        for alert in alerts_data:
            identifier = alert.get("identifier")
            msg_no = alert.get("msgNo", 1)
            
            msg_no_str = f"{msg_no:02d}"
            if identifier.endswith(msg_no_str):
                event_id = identifier[:-len(msg_no_str)]
            else:
                event_id = identifier
            
            if event_id in self.sent_alerts and msg_no <= self.sent_alerts[event_id]["msgNo"]:
                continue
                
            origin_time_str = alert.get("originTime")
            if not origin_time_str:
                continue
                
            try:
                origin_time = datetime.strptime(origin_time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TPE_TZ)
                # "如果 API 裡面的發報時間已經超過60秒，則取消推送" 
                if (now - origin_time).total_seconds() > 60:
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
                self.sent_alerts[event_id] = {"msgNo": msg_no, "channel_msg_map": {}}
            else:
                self.sent_alerts[event_id]["msgNo"] = msg_no

            text_dispatch_tasks = []

            for guild_id_str, g_settings in settings.items():
                if not g_settings.get("eew_authorized", False):
                    continue
                eew_alerts = g_settings.get("eew_alerts", {})
                is_img_enabled = g_settings.get("eew_image_enabled", False)
                
                # 先將同一個頻道的地區群組起來
                channel_groups = {}
                for loc, loc_data in eew_alerts.items():
                    channel_id = loc_data.get("channel_id")
                    if channel_id:
                        channel_groups.setdefault(channel_id, []).append((loc, loc_data))
                
                for channel_id, locs in channel_groups.items():
                    valid_locs = []
                    max_int_grade = -1
                    max_col = (255, 255, 255)
                    min_s_ts = float('inf')
                    
                    for loc, loc_data in locs:
                        if len(loc) >= 6:
                            county = loc[:3]
                            town = loc[3:]
                        else:
                            county = loc
                            town = ""
                            
                        min_mag = loc_data.get("min_magnitude", 4.5)
                        min_mag = max(4.5, min_mag)
                        min_int = loc_data.get("min_intensity", 3)
                        
                        if mag < min_mag:
                            continue
                            
                        if loc not in loc_intensity_cache:
                            target_lon, target_lat = lon, lat
                            if loc in town_mapping_cache:
                                for item in town_mapping_cache[loc]:
                                    if item[0] == loc and item[1] is not None and item[2] is not None:
                                        target_lat, target_lon = item[1], item[2]
                                        break
                                        
                            vs30 = get_vs30(county, town)
                            pga, pgv = simulate_gm(mag, depth, lon, lat, "逆斷層", target_lon, target_lat, is_subduction, vs30)
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
                            
                            # 計算 S波 預估抵達時間
                            dx = (lon - target_lon) * 111 * math.cos(math.radians((lat + target_lat) / 2))
                            dy = (lat - target_lat) * 111
                            epi_dist_km = math.hypot(dx, dy)
                            hypo_dist_km = math.sqrt(epi_dist_km**2 + depth**2)
                            
                            s_wave_travel_time = hypo_dist_km / 3.5
                            s_wave_arrival_time = origin_time + timedelta(seconds=s_wave_travel_time)
                            s_ts = int(s_wave_arrival_time.timestamp())
                            
                            loc_intensity_cache[loc] = (int_grade, display_grade, col, s_ts)
                            
                        int_grade, display_grade, col, s_ts = loc_intensity_cache[loc]
                        if int_grade < min_int:
                            continue
                            
                        valid_locs.append((loc, int_grade, display_grade, col, s_ts))
                        if int_grade > max_int_grade:
                            max_int_grade = int_grade
                            max_col = col
                        if s_ts < min_s_ts:
                            min_s_ts = s_ts
                            
                    if not valid_locs:
                        continue
                        
                    embed_color = discord.Color.from_rgb(*max_col)
                    embed = discord.Embed(description="", color=embed_color)
                    embed.add_field(name="規模", value=str(mag), inline=True)
                    embed.add_field(name="深度", value=f"{depth} 公里", inline=True)
                    
                    if len(valid_locs) == 1:
                        loc, _, display_grade, _, s_ts = valid_locs[0]
                        suffix = "" if "弱" in str(display_grade) or "強" in str(display_grade) else " 級"
                        content = f"🚨 強震即時警報 規模 {mag}\n**{loc}** 預估震度 **{display_grade}**{suffix}"
                        embed.add_field(name="抵達", value=f"<t:{s_ts}:R>", inline=True)
                    else:
                        content = f"🚨 強震即時警報 規模 {mag}\n包含 **{len(valid_locs)}** 個預警區域"
                        embed.add_field(name="抵達 (最快)", value=f"<t:{min_s_ts}:R>", inline=True)
                        
                    ts = int(origin_time.timestamp())
                    embed.add_field(name="發生時間", value=f"<t:{ts}:f>", inline=True)
                    
                    loc_desc = alert.get("locationDesc", [])
                    epi_name_api = loc_desc[0] if loc_desc else "未知"
                    epi_name = get_epicenter_name(lon, lat, epi_name_api)
                    embed.add_field(name="震央", value=epi_name, inline=True)
                    
                    if len(valid_locs) > 1:
                        loc_strings = []
                        for loc, _, display_grade, _, s_ts in valid_locs:
                            suffix = "" if "弱" in str(display_grade) or "強" in str(display_grade) else " 級"
                            loc_strings.append(f"**{loc}**：{display_grade}{suffix} (<t:{s_ts}:R>)")
                        embed.add_field(name="預估震度區域", value="\n".join(loc_strings), inline=False)

                    embed.set_footer(text=f"中央氣象署 • 接收時間 {now.strftime('%H:%M:%S')} (第 {msg_no} 報)", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
                    
                    text_dispatch_tasks.append(
                        self.send_or_edit_text(event_id, channel_id, content, embed, is_img_enabled)
                    )
            
            if not text_dispatch_tasks:
                continue
                
            results = await asyncio.gather(*text_dispatch_tasks, return_exceptions=True)
            
            channels_needing_image = []
            for res in results:
                if isinstance(res, tuple) and res is not None:
                    channel, msg_id, embed, img_enabled = res
                    if img_enabled:
                        channels_needing_image.append((channel, msg_id, embed))
                        
            if channels_needing_image:
                asyncio.create_task(
                    self.broadcast_image(event_id, msg_no, mag, depth, lon, lat, "逆斷層", channels_needing_image, origin_time_str)
                )

async def setup(bot):
    await bot.add_cog(EEWAlertCog(bot))
