import discord
from discord.ext import commands
from discord import app_commands
import io
import asyncio
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timezone, timedelta
import json
import zipfile
import xml.etree.ElementTree as ET
import math
import os

class LightningView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot

    async def fetch_and_draw_lightning_map(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                api_key = json.load(f).get('CWA_API_KEY', '')
        except Exception:
            api_key = ''
        url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/O-A0039-001?Authorization={api_key}&downloadType=WEB&format=KMZ"
        
        try:
            async with self.bot.session.get(url) as resp:
                if resp.status == 200:
                    kmz_bytes = await resp.read()
                    def process_kmz():
                        with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as z:
                            kml_filename = [f for f in z.namelist() if f.endswith('.kml')][0]
                            kml_data = z.read(kml_filename)
                        return self.generate_lightning_map(kml_data)
                    return await asyncio.to_thread(process_kmz)
                else:
                    print(f"❌ 抓取 KMZ 閃電資料失敗，HTTP 狀態碼: {resp.status}")
        except Exception as e:
            print(f"❌ 抓取或處理 KMZ 閃電資料發生錯誤: {e}")
            
        return None, "未知時間", 0, 0

    def generate_lightning_map(self, kml_data):
        with open('maps/towns-mercator-10t.json', 'r', encoding='utf-8') as f:
            topo = json.load(f)

        scale = topo['transform']['scale']
        translate = topo['transform']['translate']
        arcs = topo['arcs']

        # 解碼 TopoJSON 的 arcs
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
        for geom in topo['objects']['towns']['geometries']:
            props = geom.get('properties', {})
            county = props.get('COUNTYNAME', '')
            is_main = county not in ['澎湖縣', '金門縣', '連江縣']
            
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
            
            lines.append({'is_main': is_main, 'county': county, 'coords': geom_lines})

        # 解析縣市邊界 (counties)
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
                county_lines.append(geom_lines)

        # 取得本島的 Bounding Box 以做為真實經緯度轉換的基準
        main_x = [pt[0] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
        main_y = [pt[1] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
        min_x, max_x = min(main_x), max(main_x)
        min_y, max_y = min(main_y), max(main_y)

        # 取得所有縣市的 Bounding Box 用於繪製畫布尺寸
        all_x = [pt[0] for item in lines for line in item['coords'] for pt in line]
        all_y = [pt[1] for item in lines for line in item['coords'] for pt in line]
        img_min_x, img_max_x = min(all_x), max(all_x)
        img_min_y, img_max_y = min(all_y), max(all_y)

        IMG_W = 800
        pad = 40
        scale_factor = (IMG_W - 2 * pad) / (img_max_x - img_min_x)
        IMG_H = int((img_max_y - img_min_y) * scale_factor) + 2 * pad

        def map_to_img(x, y):
            px = pad + (x - img_min_x) * scale_factor
            py = pad + (y - img_min_y) * scale_factor
            return px, py

        # 台灣本島大約地理範圍
        WGS_MIN_LON, WGS_MAX_LON = 120.036, 122.001
        WGS_MIN_LAT, WGS_MAX_LAT = 21.896, 25.300

        def lonlat_to_img(lon, lat):
            x = min_x + (lon - WGS_MIN_LON) / (WGS_MAX_LON - WGS_MIN_LON) * (max_x - min_x)
            def merc_y(lat_deg):
                return math.log(math.tan(math.pi/4 + lat_deg * math.pi/360))
            my = merc_y(lat)
            my_max = merc_y(WGS_MAX_LAT)
            my_min = merc_y(WGS_MIN_LAT)
            y = min_y + (my_max - my) / (my_max - my_min) * (max_y - min_y)
            return map_to_img(x, y)

        # 畫布背景色
        img = Image.new('RGBA', (IMG_W, IMG_H), "#0f1113")
        draw = ImageDraw.Draw(img)
        
        # 紀錄外島邊界以繪製外框
        island_bboxes = {c: [float('inf'), float('inf'), float('-inf'), float('-inf')] for c in ['澎湖縣', '金門縣', '連江縣']}

        for item in lines:
            fill_color = "#1a1d20"
            outline_color = "#292e33"
            county = item['county']
            for line in item['coords']:
                px_line = [map_to_img(pt[0], pt[1]) for pt in line]
                if len(px_line) >= 3:
                    draw.polygon(px_line, fill=fill_color, outline=outline_color)
                if county in island_bboxes:
                    for px, py in px_line:
                        island_bboxes[county][0] = min(island_bboxes[county][0], px)
                        island_bboxes[county][1] = min(island_bboxes[county][1], py)
                        island_bboxes[county][2] = max(island_bboxes[county][2], px)
                        island_bboxes[county][3] = max(island_bboxes[county][3], py)

        # 繪製縣市交界線 (稍微加粗)
        county_outline_color = "#3e454b"
        for geom_lines in county_lines:
            for line in geom_lines:
                px_line = [map_to_img(pt[0], pt[1]) for pt in line]
                if len(px_line) >= 2:
                    draw.line(px_line, fill=county_outline_color, width=2)

        # 繪製外島方框
        box_outline = "#3e454b"
        for c, bbox in island_bboxes.items():
            if bbox[0] != float('inf'):
                pad_b = 8
                draw.rectangle([bbox[0]-pad_b, bbox[1]-pad_b, bbox[2]+pad_b, bbox[3]+pad_b], outline=box_outline, width=2)
                
        # 外島大約真實地理範圍 (min_lon, max_lon, min_lat, max_lat)
        ISLAND_WGS_BBOX = {
            '澎湖縣': (119.30, 119.80, 23.10, 23.90),
            '金門縣': (118.15, 118.55, 24.30, 24.60),
            '連江縣': (119.90, 120.50, 25.90, 26.50)
        }
        
        # 使用地理範圍的中心作為平移基準點
        ISLAND_WGS_CENTER = {
            c: ((bbox[0] + bbox[1]) / 2, (bbox[2] + bbox[3]) / 2)
            for c, bbox in ISLAND_WGS_BBOX.items()
        }
        
        # 取得圖片上外島框的中心座標
        island_centers = {}
        for c, bbox in island_bboxes.items():
            if bbox[0] != float('inf'):
                island_centers[c] = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)

        root = ET.fromstring(kml_data)
        ns = {'kml': 'http://www.opengis.net/kml/2.2'}
        
        cg_count = 0
        cc_count = 0
        obs_time = "未知時間"
        
        doc_name_el = root.find('.//kml:Document/kml:name', ns)
        if doc_name_el is not None and '中央氣象署閃電資料:' in doc_name_el.text:
            obs_time = doc_name_el.text.split('中央氣象署閃電資料:')[1].strip()

        # 準備繪製文字的字體 (優先嘗試本地 Noto Sans TC，其次為 macOS/Windows 內建字體)
        font_paths = [
            "fonts/Noto_Sans_TC/NotoSansTC-Regular.ttf", # 本地 Noto Sans TC 字體
            "/System/Library/Fonts/PingFang.ttc",  # macOS 絕對路徑
            "PingFang.ttc",                        # macOS 相對路徑
            "C:\\Windows\\Fonts\\msjh.ttc",        # Windows 絕對路徑
            "msjh.ttc"                             # Windows 相對路徑
        ]
        font_title = None
        font_time = None
        for path in font_paths:
            try:
                font_title = ImageFont.truetype(path, 36)  # 讓標題約佔據圖片寬度 1/3
                font_time = ImageFont.truetype(path, 20)   # 觀測時間縮小一半
                break
            except Exception:
                continue
                
        if font_title is None:
            print("⚠️ 找不到本地或系統內建的中文字體，已退回 Pillow 預設字體（預設字體無法放大且不支援中文）")
            font_title = ImageFont.load_default()
            font_time = ImageFont.load_default()

        # 繪製左上角標題
        draw.text((25, 25), " 閃電即時觀測圖", fill="#ffffff", font=font_title)

        # 繪製左下角觀測時間
        obs_time_lines = obs_time.replace(" ~ ", " ~ ")
        time_text = f"觀測時間 {obs_time_lines}"
        if hasattr(draw, 'multiline_textbbox'):
            text_bbox = draw.multiline_textbbox((0, 0), time_text, font=font_time)
            text_h = text_bbox[3] - text_bbox[1]
        else:
            _, text_h = draw.textsize(time_text, font=font_time)
            
        draw.multiline_text((25, IMG_H - text_h - 25), time_text, fill="#cccccc", font=font_time)

        strikes = []

        for pm in root.findall('.//kml:Placemark', ns):
            name_el = pm.find('kml:name', ns)
            coord_el = pm.find('.//kml:coordinates', ns)
            if name_el is None or coord_el is None:
                continue
            
            try:
                parts = coord_el.text.strip().split(',')
                lon, lat = float(parts[0]), float(parts[1])
            except (ValueError, IndexError):
                continue
                
            # 判斷是否落在外島的真實經緯度範圍內
            target_island = None
            for c, (min_lon, max_lon, min_lat, max_lat) in ISLAND_WGS_BBOX.items():
                if min_lon <= lon <= max_lon and min_lat <= lat <= max_lat:
                    target_island = c
                    break
                    
            if target_island and target_island in island_centers:
                # 該閃電在外島，需加上平移量
                c_lon, c_lat = ISLAND_WGS_CENTER[target_island]
                px_c, py_c = lonlat_to_img(c_lon, c_lat)
                px_raw, py_raw = lonlat_to_img(lon, lat)
                cx, cy = island_centers[target_island]
                px = cx + (px_raw - px_c)
                py = cy + (py_raw - py_c)
            else:
                # 台灣本島或其他區域
                px, py = lonlat_to_img(lon, lat)
            
            # 若座標落於圖像周圍才繪製，以防偏遠座標扭曲顯示
            if -100 <= px <= IMG_W + 100 and -100 <= py <= IMG_H + 100:
                is_cg = "對地" in name_el.text
                if is_cg:
                    cg_count += 1
                else:
                    cc_count += 1

                time_el = pm.find('.//kml:TimeStamp/kml:when', ns)
                t = None
                if time_el is not None:
                    try:
                        t = datetime.strptime(time_el.text.strip(), "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass
                        
                strikes.append({'type': 'cg' if is_cg else 'cc', 'x': px, 'y': py, 'time': t})

        def get_color(strike_time, ref_time):
            diff = (ref_time - strike_time).total_seconds() / 60.0
            if diff <= 5: return (255, 50, 50, 255)      # 0-5分 (紅)
            elif diff <= 10: return (255, 215, 0, 255)   # 5-10分 (黃)
            elif diff <= 30: return (50, 255, 50, 255)   # 10-30分 (綠)
            else: return (50, 150, 255, 255)             # 30-60分 (藍)

        valid_strikes = [s for s in strikes if s['time'] is not None]

        latest_time = max((s['time'] for s in valid_strikes), default=datetime.now(timezone.utc))
        for s in valid_strikes:
            color = get_color(s['time'], latest_time)
            px, py = s['x'], s['y']
            if s['type'] == 'cg':
                draw.line((px-6, py, px+6, py), fill=color, width=2)
                draw.line((px, py-6, px, py+6), fill=color, width=2)
            else:
                draw.ellipse((px-4, py-4, px+4, py+4), fill=color)

        output = io.BytesIO()
        img.save(output, format='PNG')
        output.seek(0)
        return output, obs_time, cg_count, cc_count

    async def build_embed(self):
        img_bytes, obs_time, cg_count, cc_count = await self.fetch_and_draw_lightning_map()
        
        content = "⚡ 即時閃電查詢"
        embed_desc = f"觀測時間：{obs_time}"
        filename = "lightning_map.png"
            
        embed = discord.Embed(
            title="",
            description=embed_desc,
            color=0xf1c40f
        )
        
        if img_bytes:
            file = discord.File(img_bytes, filename=filename)
            embed.set_image(url=f"attachment://{filename}")
            
            embed.add_field(name="⚡ 對地閃電 `+`", value=f"{cg_count} 筆", inline=True)
            embed.add_field(name="☁️ 雲間閃電 `o`", value=f"{cc_count} 筆", inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=True)
            embed.add_field(
                name="圖例", 
                value="🔴 ` 0 ~  5 分鐘`　🟡 ` 5 ~ 10 分鐘`\n🟢 `10 ~ 30 分鐘`　🔵 `30 ~ 60 分鐘`", 
                inline=False
            )
        else:
            embed.description += "\n\n❌ **目前無法取得即時閃電資料**"
            file = None
            
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return content, embed, file

class LightningCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="即時閃電", description="顯示最新的即時閃電觀測圖")
    async def lightning_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        view = LightningView(self.bot)
        content, embed, file = await view.build_embed()
        
        if file:
            await interaction.followup.send(content=content, embed=embed, file=file, view=view)
        else:
            await interaction.followup.send(content=content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(LightningCog(bot))