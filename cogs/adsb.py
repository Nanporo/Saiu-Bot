import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
import json
import io
import math
import asyncio
from PIL import Image, ImageDraw, ImageFont

class AdsbView(discord.ui.View):
    def __init__(self, bot, api_url, show_map=False):
        super().__init__(timeout=300)
        self.bot = bot
        self.api_url = api_url
        self.show_map = show_map
        self.mode = "simple"
        
        self.update_buttons()

    def update_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label in ["顯示地圖", "隱藏地圖"]:
                    child.label = "隱藏地圖" if self.show_map else "顯示地圖"
                elif child.label == "精簡":
                    child.disabled = (self.mode == "simple")
                    child.style = discord.ButtonStyle.secondary if self.mode == "simple" else discord.ButtonStyle.secondary
                elif child.label == "詳細":
                    child.disabled = (self.mode == "detail")
                    child.style = discord.ButtonStyle.secondary if self.mode == "detail" else discord.ButtonStyle.secondary

    async def fetch_data(self):
        try:
            async with self.bot.session.get(self.api_url) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            print(f"❌ 發生錯誤，無法讀取 ADS-B 資料：{e}")
        return None

    def draw_map(self, data):
        aircrafts = data.get("aircraft", [])
        # 讀取地形資料
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

        # 取得各行政區與縣市的線段
        lines = []
        county_lines = []
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

        # 台灣本島 TopoJSON 邊界用於轉換比例
        main_x = [pt[0] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
        main_y = [pt[1] for item in lines if item['is_main'] for line in item['coords'] for pt in line]
        min_x, max_x = min(main_x), max(main_x)
        min_y, max_y = min(main_y), max(main_y)

        # 設定自定義視角：台灣西南方 (大約北到台中，西到金門西側，南到恆春往南100km，東到台東市)
        CANVAS_MIN_LON, CANVAS_MAX_LON = 118.0, 121.2
        CANVAS_MIN_LAT, CANVAS_MAX_LAT = 21.0, 24.5

        def merc_y(lat_deg):
            return math.log(math.tan(math.pi/4 + lat_deg * math.pi/360))

        IMG_W = 800
        lon_range_rad = math.radians(CANVAS_MAX_LON) - math.radians(CANVAS_MIN_LON)
        c_my_max = merc_y(CANVAS_MAX_LAT)
        c_my_min = merc_y(CANVAS_MIN_LAT)
        IMG_H = int(IMG_W * (c_my_max - c_my_min) / lon_range_rad)

        # 地圖座標轉換函數 (從 topojson 到圖像像素)
        def topo_to_img(x, y):
            lon_deg = 120.036 + (x - min_x) / (max_x - min_x) * (122.001 - 120.036)
            my_max, my_min = merc_y(25.300), merc_y(21.896)
            my = my_max - (y - min_y) / (max_y - min_y) * (my_max - my_min)
            px = (lon_deg - CANVAS_MIN_LON) / (CANVAS_MAX_LON - CANVAS_MIN_LON) * IMG_W
            py = (c_my_max - my) / (c_my_max - c_my_min) * IMG_H
            return px, py

        # WGS84 真實經緯度轉換為圖像像素
        def wgs_to_img(lon, lat):
            px = (lon - CANVAS_MIN_LON) / (CANVAS_MAX_LON - CANVAS_MIN_LON) * IMG_W
            py = (c_my_max - merc_y(lat)) / (c_my_max - c_my_min) * IMG_H
            return px, py

        # 計算澎湖與金門的原本位置 (用來偏移或隱藏)
        penghu_px_list = []
        penghu_py_list = []
        kinmen_px_list = []
        kinmen_py_list = []
        for item in lines:
            if item['county'] == '澎湖縣':
                for line in item['coords']:
                    for pt in line:
                        px, py = topo_to_img(pt[0], pt[1])
                        penghu_px_list.append(px)
                        penghu_py_list.append(py)
            elif item['county'] == '金門縣':
                for line in item['coords']:
                    for pt in line:
                        px, py = topo_to_img(pt[0], pt[1])
                        kinmen_px_list.append(px)
                        kinmen_py_list.append(py)
        
        penghu_offset_x = 0
        penghu_offset_y = 0
        if penghu_px_list:
            fake_cx = (min(penghu_px_list) + max(penghu_px_list)) / 2
            fake_cy = (min(penghu_py_list) + max(penghu_py_list)) / 2
            real_cx, real_cy = wgs_to_img(119.5664, 23.5711)
            penghu_offset_x = real_cx - fake_cx
            penghu_offset_y = real_cy - fake_cy

        # 初始化畫布
        img = Image.new('RGBA', (IMG_W, IMG_H), "#0f1113")
        draw = ImageDraw.Draw(img)

        # 畫台灣地圖與外島
        for item in lines:
            county = item['county']
            if county == '金門縣':
                continue
                
            for line in item['coords']:
                px_line = []
                for pt in line:
                    px, py = topo_to_img(pt[0], pt[1])
                    if county == '澎湖縣':
                        px += penghu_offset_x
                        py += penghu_offset_y
                    px_line.append((px, py))
                    
                if len(px_line) >= 3:
                    draw.polygon(px_line, fill="#1a1d20", outline="#292e33")
                    
        # 畫縣市邊界線
        for geom_lines in county_lines:
            for line in geom_lines:
                px_line = []
                is_kinmen = False
                for pt in line:
                    px, py = topo_to_img(pt[0], pt[1])
                    if kinmen_px_list and min(kinmen_px_list)-5 <= px <= max(kinmen_px_list)+5 and min(kinmen_py_list)-5 <= py <= max(kinmen_py_list)+5:
                        is_kinmen = True
                        break
                    if penghu_px_list and min(penghu_px_list)-5 <= px <= max(penghu_px_list)+5 and min(penghu_py_list)-5 <= py <= max(penghu_py_list)+5:
                        px += penghu_offset_x
                        py += penghu_offset_y
                    px_line.append((px, py))
                    
                if not is_kinmen and len(px_line) >= 2:
                    draw.line(px_line, fill="#3e454b", width=2)

        # 設定中文字體
        font_paths = [
            "fonts/Noto_Sans_TC/NotoSansTC-Regular.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "PingFang.ttc",
            "C:\\Windows\\Fonts\\msjh.ttc",
            "msjh.ttc"
        ]
        font = None
        for path in font_paths:
            try:
                font = ImageFont.truetype(path, 26)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()
            
        # 產生飛機多邊形的函數
        def get_airplane_polygon(x, y, size, angle_deg):
            pts = [
                (0, -size * 2.0),           # 機鼻
                (size * 0.15, -size * 1.5), # 右側機鼻
                (size * 0.25, -size * 0.5), # 右側機身前段
                (size * 1.6, size * 0.4),   # 右翼前緣翼尖
                (size * 1.4, size * 0.7),   # 右翼後緣翼尖
                (size * 0.25, size * 0.6),  # 右側機身後段 (翼根)
                (size * 0.25, size * 1.5),  # 右側機身尾段
                (size * 0.8, size * 1.9),   # 右水平尾翼翼尖
                (size * 0.6, size * 2.1),   # 右水平尾翼後緣
                (0, size * 2.0),            # 尾部中心
                (-size * 0.6, size * 2.1),  # 左水平尾翼後緣
                (-size * 0.8, size * 1.9),  # 左水平尾翼翼尖
                (-size * 0.25, size * 1.5), # 左側機身尾段
                (-size * 0.25, size * 0.6), # 左側機身後段 (翼根)
                (-size * 1.4, size * 0.7),  # 左翼後緣翼尖
                (-size * 1.6, size * 0.4),  # 左翼前緣翼尖
                (-size * 0.25, -size * 0.5),# 左側機身前段
                (-size * 0.15, -size * 1.5),# 左側機鼻
            ]
            angle_rad = math.radians(angle_deg)
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)
            
            rotated_pts = []
            for px, py in pts:
                rx = px * cos_a - py * sin_a
                ry = px * sin_a + py * cos_a
                rotated_pts.append((x + rx, y + ry))
            return rotated_pts

        def get_alt_color(alt):
            if alt == "ground" or alt is None:
                return "#A9A9A9"
            try:
                alt_val = int(alt)
                if alt_val < 1000: return "#FF3B30"       # 紅色
                elif alt_val < 4000: return "#FFCC00"     # 黃色
                elif alt_val < 10000: return "#34C759"     # 綠色
                elif alt_val < 2000: return "#5AC8FA"     # 天藍色
                elif alt_val < 30000: return "#5555FF"    # 深藍色
                else: return "#AF52DE"                    # 紫色
            except ValueError:
                return "#A9A9A9"

        # 將飛機繪製於地圖上
        for ac in aircrafts:
            lon = ac.get("lon")
            lat = ac.get("lat")
            flight = ac.get("flight", "").strip()
            hex_code = ac.get("hex", "???")
            squawk = ac.get("squawk")
            altitude = ac.get("alt_baro")
            
            heading = ac.get("track")
            if heading is None:
                heading = ac.get("true_heading")
            if heading is None:
                heading = ac.get("mag_heading", 0)
                
            display_text = flight or hex_code
            
            if lon and lat:
                px, py = wgs_to_img(lon, lat)
                # 過濾並只畫出在畫布範圍附近的飛機 (容許些微溢出，Pillow 會做裁剪)
                if -50 <= px <= IMG_W + 50 and -50 <= py <= IMG_H + 50:
                    color = get_alt_color(altitude)
                    outline_color = "#FF0000" if squawk in ["7500", "7600", "7700"] else "#FFFFFF"
                    # 將飛機放大至 10
                    plane_poly = get_airplane_polygon(px, py, 10, heading)
                    draw.polygon(plane_poly, fill=color, outline=outline_color)
                    
                    # 繪製加上黑色邊框的文字以增加可讀性
                    for dx in range(-2, 3):
                        for dy in range(-2, 3):
                            if dx != 0 or dy != 0:
                                draw.text((px + 15 + dx, py - 15 + dy), display_text, fill="#000000", font=font)
                    draw.text((px + 15, py - 15), display_text, fill="#ffffff", font=font)

        # 左下角加入偵測時間
        api_time = datetime.fromtimestamp(data.get("now", 0), tz=timezone.utc).astimezone(timezone(timedelta(hours=8)))
        time_str = f"T-RCNN39 - 偵測時間 {api_time.strftime('%Y-%m-%d %H:%M:%S')}"
        draw.text((15, IMG_H - 45), time_str, fill="#ffffff", font=font)

        output = io.BytesIO()
        img.save(output, format='PNG')
        output.seek(0)
        return output

    async def build_embed(self):
        data = await self.fetch_data()
        if not data:
            return "❌ 無法讀取 ADS-B 資料。", discord.Embed(description="無法連接至 ADS-B API", color=0xFF0000), None

        aircrafts = data.get("aircraft", [])
        tracked_aircrafts = [ac for ac in aircrafts if ac.get("flight") and ac.get("r_dst")]
        tracked_aircrafts.sort(key=lambda x: x.get("r_dst", float('inf')))

        if not tracked_aircrafts:
            return "📡 目前接收範圍內沒有偵測到飛機。", discord.Embed(description="目前接收範圍內沒有偵測到飛機。", color=0x007FFF), None

        try:
            with open('maps/callsign_country.json', 'r', encoding='utf-8') as f:
                callsign_map = json.load(f)
        except Exception:
            callsign_map = {}

        emergency_detected = any(ac.get("squawk") in ["7700", "7600", "7500"] for ac in tracked_aircrafts)

        message_content = "✈️ 附近飛機動態"
        embed = discord.Embed(
            title="",
            color=0xFF0000 if emergency_detected else 0x007FFF
        )

        special_squawks = {
            "7500": ("🏴‍☠️", "非法干擾"),
            "7600": ("📻", "通訊失效"),
            "7700": ("🚨", "緊急情況"),
            "1200": ("🛩️", "目視飛行 (VFR)"),
            "2000": ("✈️", "無指定 IFR"),
            "7777": ("🪖", "軍事攔截"),
            "0000": ("🪖", "軍事預留")
        }

        if self.mode == "simple":
            limit = 9
            lines = []
            for ac in tracked_aircrafts[:limit]:
                flight = ac.get("flight", "N/A").strip()
                flight_display = flight or "N/A"

                prefix_emoji = "✈️"  # 預設為飛機 emoji
                has_flag = False
                if flight:
                    prefix = flight[:3].upper()
                    if prefix in callsign_map:
                        prefix_emoji = callsign_map[prefix]
                        has_flag = True
                
                distance = ac.get("r_dst", "N/A")
                try:
                    dist_km = float(distance) * 1.852
                    dist_str = f"{dist_km:.1f} km"
                except (ValueError, TypeError):
                    dist_str = "N/A"

                squawk = ac.get("squawk")
                desc_str = ""
                if squawk in special_squawks:
                    squawk_icon, desc = special_squawks[squawk]
                    prefix_emoji = squawk_icon  # 緊急狀態圖示優先於國旗或飛機
                    if squawk in ["7500", "7600", "7700"]:
                        desc_str = f" ({desc})"

                lines.append(f"{prefix_emoji} `{flight_display}` 距離 `{dist_str}`{desc_str}")
            
            embed.description = f"目前偵測到 `{len(tracked_aircrafts)}` 架已知的飛機\n\n" + "\n".join(lines)
        else:
            limit = 6
            embed.description = f"目前偵測到 `{len(tracked_aircrafts)}` 架已知的飛機"
            for ac in tracked_aircrafts[:limit]:
                flight = ac.get("flight", "N/A").strip()
                flight_display = flight or "N/A"
                
                prefix_emoji = "✈️"
                if flight:
                    prefix = flight[:3].upper()
                    if prefix in callsign_map:
                        prefix_emoji = callsign_map[prefix]
                        
                altitude = ac.get("alt_baro", "N/A")
                speed = ac.get("gs", "N/A")
                distance = ac.get("r_dst", "N/A")
                squawk = ac.get("squawk")

                try:
                    dist_km = float(distance) * 1.852
                    dist_str = f"{dist_km:.1f} km"
                except (ValueError, TypeError):
                    dist_str = "N/A"

                squawk_display = f"應答機 `{squawk}`\n" if squawk else ""
                if squawk in special_squawks:
                    squawk_icon, desc = special_squawks[squawk]
                    prefix_emoji = squawk_icon
                    if squawk in ["7500", "7600", "7700"]:
                        field_name = f"{prefix_emoji} {flight_display} ({desc})"
                    else:
                        field_name = f"{prefix_emoji} {flight_display}"
                        squawk_display = f"應答機 `{squawk}` ({desc})\n"
                else:
                    field_name = f"{prefix_emoji} {flight_display}"

                field_value = (
                    f"{squawk_display}"
                    f"高度 `{altitude} ft`\n"
                    f"地速 `{speed} kts`\n"
                    f"距離 `{dist_str}`"
                )
                embed.add_field(name=field_name, value=field_value, inline=True)

            displayed_count = len(tracked_aircrafts[:limit])
            if displayed_count % 3 != 0:
                for _ in range(3 - (displayed_count % 3)):
                    embed.add_field(name="\u200b", value="\u200b", inline=True)

        api_time = datetime.fromtimestamp(data.get("now", 0), tz=timezone.utc)
        api_time_tw = api_time.astimezone(timezone(timedelta(hours=8)))
        footer_text = f"T-RCNN39 • 資料時間 {api_time_tw.strftime('%Y-%m-%d %H:%M:%S')}"
        embed.set_footer(text=footer_text)

        file = None
        if self.show_map:
            img_bytes = await asyncio.to_thread(self.draw_map, data)
            file = discord.File(img_bytes, filename="adsb_map.png")
            embed.set_image(url="attachment://adsb_map.png")

        return message_content, embed, file

    @discord.ui.button(label="顯示地圖", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_map(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.show_map = not self.show_map
        
        self.update_buttons()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="精簡", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def btn_simple(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.mode = "simple"
        self.update_buttons()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="詳細", style=discord.ButtonStyle.secondary, row=0)
    async def btn_detail(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.mode = "detail"
        self.update_buttons()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

class AdsbCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_url = config.get("ADSB_API_URL", "")
        except Exception:
            self.api_url = ""

    @app_commands.command(name="附近飛機", description="查詢台灣西南方開啟了 ADS-B 訊號的飛機")
    @app_commands.describe(顯示地圖="是否顯示地圖 (預設不顯示)")
    @app_commands.choices(顯示地圖=[
        app_commands.Choice(name="顯示", value="yes"),
        app_commands.Choice(name="不顯示", value="no")
    ])
    async def adsb_command(self, interaction: discord.Interaction, 顯示地圖: app_commands.Choice[str] = None):
        await interaction.response.defer()

        if not self.api_url:
            await interaction.followup.send("⚠️ 尚未在 `config.json` 中設定 `ADSB_API_URL`，此功能目前已停用。")
            return

        show_map = 顯示地圖 and 顯示地圖.value == "yes"
        view = AdsbView(self.bot, self.api_url, show_map)
        content, embed, file = await view.build_embed()
        
        if file:
            await interaction.followup.send(content=content, embed=embed, file=file, view=view)
        else:
            await interaction.followup.send(content=content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(AdsbCog(bot))