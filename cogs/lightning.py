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
import logging

logger = logging.getLogger(__name__)

class LightningView(discord.ui.View):
    def __init__(self, bot, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    async def fetch_and_draw_lightning_map(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                api_key = json.load(f).get('CWA_API_KEY', '')
        except Exception:
            api_key = ''
        url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/O-A0039-001?Authorization={api_key}&downloadType=WEB&format=KMZ"
        
        try:
            async with self.bot.session.get(url) as resp:
                logger.info(f"🌐 [資料抓取] 閃電 KMZ: {url} -> HTTP 狀態碼: {resp.status}")
                if resp.status == 200:
                    kmz_bytes = await resp.read()
                    def process_kmz():
                        with zipfile.ZipFile(io.BytesIO(kmz_bytes)) as z:
                            kml_filename = [f for f in z.namelist() if f.endswith('.kml')][0]
                            kml_data = z.read(kml_filename)
                        return self.generate_lightning_map(kml_data)
                    return await asyncio.to_thread(process_kmz)
                else:
                    logger.error(f"❌ 抓取 KMZ 閃電資料失敗，HTTP 狀態碼: {resp.status}")
        except Exception as e:
            logger.error(f"❌ 抓取或處理 KMZ 閃電資料發生錯誤: {e!r}")
            
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
        matsu_x_list = []
        matsu_y_list = []
        kinmen_x_list = []
        kinmen_y_list = []
        penghu_x_list = []
        penghu_y_list = []

        for geom in topo['objects']['towns']['geometries']:
            props = geom.get('properties', {})
            county = props.get('COUNTYNAME', '')
            
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
            lines.append({'is_main': is_main, 'county': county, 'coords': geom_lines})

        # 取得本島的 Bounding Box 以做為真實經緯度轉換的基準
        main_x = [pt[0] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
        main_y = [pt[1] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
        min_x, max_x = min(main_x), max(main_x)
        min_y, max_y = min(main_y), max(main_y)

        # 台灣本島大約地理範圍
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

        # 套用澎湖的平移量
        for item in lines:
            if item['county'] == '澎湖縣':
                for line in item['coords']:
                    for i in range(len(line)):
                        line[i] = (line[i][0] + penghu_offset_x, line[i][1] + penghu_offset_y)

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

        def lonlat_to_img(lon, lat):
            x = min_x + (lon - WGS_MIN_LON) / (WGS_MAX_LON - WGS_MIN_LON) * (max_x - min_x)
            my = merc_y(lat)
            my_max = merc_y(WGS_MAX_LAT)
            my_min = merc_y(WGS_MIN_LAT)
            y = min_y + (my_max - my) / (my_max - my_min) * (max_y - min_y)
            return map_to_img(x, y)

        # 畫布背景色
        img = Image.new('RGBA', (IMG_W, IMG_H), "#0f1113")
        draw = ImageDraw.Draw(img)
        
        for item in lines:
            fill_color = "#1a1d20"
            outline_color = "#292e33"
            for line in item['coords']:
                px_line = [map_to_img(pt[0], pt[1]) for pt in line]
                if len(px_line) >= 3:
                    draw.polygon(px_line, fill=fill_color, outline=outline_color)

        # 繪製縣市交界線 (稍微加粗)
        county_outline_color = "#3e454b"
        for geom_lines in county_lines:
            for line in geom_lines:
                px_line = [map_to_img(pt[0], pt[1]) for pt in line]
                if len(px_line) >= 2:
                    draw.line(px_line, fill=county_outline_color, width=2)

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
            logger.warning("⚠️ 找不到本地或系統內建的中文字體，已退回 Pillow 預設字體（預設字體無法放大且不支援中文）")
            font_title = ImageFont.load_default()
            font_time = ImageFont.load_default()

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
                draw.line((px-8, py, px+8, py), fill=color, width=2)
                draw.line((px, py-8, px, py+8), fill=color, width=2)
            else:
                draw.ellipse((px-4, py-4, px+4, py+4), fill=color)

        # 繪製左上角標題
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx != 0 or dy != 0:
                    draw.text((25 + dx, 25 + dy), " 閃電即時觀測圖", fill="#000000", font=font_title)
        draw.text((25, 25), " 閃電即時觀測圖", fill="#ffffff", font=font_title)

        # 繪製左下角觀測時間
        obs_time_lines = obs_time.replace(" ~ ", " ~ ")
        time_text = f"Generated by Saiu-Bot\n觀測時間 {obs_time_lines}"
        if hasattr(draw, 'multiline_textbbox'):
            text_bbox = draw.multiline_textbbox((0, 0), time_text, font=font_time)
            text_h = text_bbox[3] - text_bbox[1]
        else:
            _, text_h = draw.textsize(time_text, font=font_time)
            
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if dx != 0 or dy != 0:
                    draw.multiline_text((25 + dx, IMG_H - text_h - 25 + dy), time_text, fill="#000000", font=font_time)
        draw.multiline_text((25, IMG_H - text_h - 25), time_text, fill="#cccccc", font=font_time)

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

    @app_commands.command(name="閃電", description="⚡ 顯示最新的即時打雷、雷擊觀測圖 Lightning")
    async def lightning_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        view = LightningView(self.bot, interaction.user.id)
        content, embed, file = await view.build_embed()
        
        if file:
            await interaction.followup.send(content=content, embed=embed, file=file, view=view)
        else:
            await interaction.followup.send(content=content, embed=embed, view=view)

    async def refresh_message(self, interaction: discord.Interaction, message: discord.Message, cmd_name: str):
        await interaction.response.defer(ephemeral=True)
        view = LightningView(self.bot, interaction.user.id)

        content, embed, file = await view.build_embed()
        
        if file:
            await message.edit(content=content, embed=embed, attachments=[file], view=view)
        else:
            await message.edit(content=content, embed=embed, view=view)
            
        await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)

async def setup(bot):
    await bot.add_cog(LightningCog(bot))