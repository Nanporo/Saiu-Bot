import discord
from discord.ext import commands
from discord import app_commands
import io
import asyncio
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timezone, timedelta
import json
import math
import logging
from modules.cache import async_cache

logger = logging.getLogger(__name__)

class AirPressureView(discord.ui.View):
    def __init__(self, bot, data, author_id: int, is_high=False, show_high_altitude=True, show_image=False):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.bot = bot
        self.data = data
        self.stations = data.get("records", {}).get("Station", [])
        self.is_high = is_high
        self.show_high_altitude = show_high_altitude
        self.show_image = show_image
        self.show_details = False
        self.cached_image = None
        self.cached_obs_time = "未知時間"
        self.parsed_results = []
        self.parse_data()
        self.update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def parse_data(self):
        for st in self.stations:
            station_name = st.get('StationName', '未知')
            geo_info = st.get('GeoInfo', {})
            county = geo_info.get('CountyName', '')
            town = geo_info.get('TownName', '')
            altitude_str = geo_info.get('StationAltitude', '0')

            try:
                altitude = float(altitude_str)
            except ValueError:
                altitude = 0.0

            weather = st.get('WeatherElement', {})
            p_str = weather.get('AirPressure')
            time_str = st.get('ObsTime', {}).get('DateTime', '')

            if p_str is None or p_str == "" or str(p_str) in ["-99.0", "-999.0", "-99", "-999"]:
                continue

            try:
                p_val = float(p_str)
            except (ValueError, TypeError):
                continue

            if p_val <= 0.0:
                continue

            try:
                if not time_str or time_str == "-99":
                    time_format = "未知"
                else:
                    dt = datetime.fromisoformat(time_str)
                    time_format = f"<t:{int(dt.timestamp())}:t>"
            except Exception:
                time_format = "未知"

            self.parsed_results.append({
                "station": station_name,
                "county": county,
                "town": town,
                "altitude": altitude,
                "pressure": p_val,
                "time": time_format
            })

    def update_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label in ["顯示詳細資訊", "隱藏詳細資訊"]:
                    child.label = "隱藏詳細資訊" if self.show_details else "顯示詳細資訊"
                    child.style = discord.ButtonStyle.secondary if self.show_details else discord.ButtonStyle.primary
                elif child.label in ["顯示氣壓圖", "隱藏氣壓圖"]:
                    child.label = "隱藏氣壓圖" if self.show_image else "顯示氣壓圖"
            elif isinstance(child, discord.ui.Select):
                val = f"{'high' if self.is_high else 'low'}_{'all' if self.show_high_altitude else 'no_high'}"
                for option in child.options:
                    option.default = (option.value == val)

    def generate_map(self, data):
        with open('maps/towns-mercator-10t.json', 'r', encoding='utf-8') as f:
            topo = json.load(f)

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

        img = Image.new('RGBA', (IMG_W, IMG_H), "#0f1113")
        draw = ImageDraw.Draw(img)
        
        for item in lines:
            fill_color = "#1a1d20"
            outline_color = "#292e33"
            for line in item['coords']:
                px_line = [map_to_img(pt[0], pt[1]) for pt in line]
                if len(px_line) >= 3:
                    draw.polygon(px_line, fill=fill_color, outline=outline_color)

        county_outline_color = "#3e454b"
        for geom_lines in county_lines:
            for line in geom_lines:
                px_line = [map_to_img(pt[0], pt[1]) for pt in line]
                if len(px_line) >= 2:
                    draw.line(px_line, fill=county_outline_color, width=2)

        font_paths = [
            "fonts/Noto_Sans_TC/NotoSansTC-Regular.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "PingFang.ttc",
            "C:\\Windows\\Fonts\\msjh.ttc",
            "msjh.ttc"
        ]
        font_title = None
        font_time = None
        font_legend = None
        for path in font_paths:
            try:
                font_title = ImageFont.truetype(path, 36)
                font_time = ImageFont.truetype(path, 20)
                font_legend = ImageFont.truetype(path, 16)
                break
            except Exception:
                continue
                
        if font_title is None:
            logger.warning("⚠️ 找不到本地或系統內建的中文字體，已退回 Pillow 預設字體（預設字體無法放大且不支援中文）")
            font_title = ImageFont.load_default()
            font_time = ImageFont.load_default()
            font_legend = ImageFont.load_default()

        draw.text((25, 25), " 氣壓即時觀測圖", fill="#ffffff", font=font_title)

        obs_time = "未知時間"
        discord_obs_time = "未知時間"
        stations = data.get("records", {}).get("Station", [])
        if stations:
            obs_time_raw = stations[0].get("ObsTime", {}).get("DateTime", "")
            if obs_time_raw:
                try:
                    dt = datetime.fromisoformat(obs_time_raw)
                    obs_time = dt.strftime("%Y-%m-%d %H:%M")
                    discord_obs_time = f"<t:{int(dt.timestamp())}:t>"
                except Exception:
                    obs_time = obs_time_raw
                    discord_obs_time = obs_time_raw

        time_text = f"Generated by Saiu-Bot\n觀測時間 {obs_time}"
        if hasattr(draw, 'multiline_textbbox'):
            text_bbox = draw.multiline_textbbox((0, 0), time_text, font=font_time)
            text_h = text_bbox[3] - text_bbox[1]
        else:
            _, text_h = draw.textsize(time_text, font=font_time)
            
        draw.multiline_text((25, IMG_H - text_h - 25), time_text, fill="#cccccc", font=font_time)

        def get_pressure_color(p):
            min_p, max_p = 980.0, 1020.0
            mid_p = (min_p + max_p) / 2.0
            p = max(min_p, min(max_p, p))
            
            if p < mid_p:
                # 低壓：紅 (255, 50, 50) 到 中間：白 (255, 255, 255)
                ratio = (p - min_p) / (mid_p - min_p)
                r = 255
                g = int(50 + ratio * 205)
                b = int(50 + ratio * 205)
            else:
                # 中間：白 (255, 255, 255) 到 高壓：藍 (50, 50, 255)
                ratio = (p - mid_p) / (max_p - mid_p)
                r = int(255 - ratio * 205)
                g = int(255 - ratio * 205)
                b = 255
            return (r, g, b, 255)

        for st in stations:
            we = st.get("WeatherElement", {})
            p_str = we.get("AirPressure")
            if p_str is None or p_str == "" or str(p_str) in ["-99.0", "-999.0", "-99", "-999"]:
                continue
                
            try:
                pressure = float(p_str)
            except (ValueError, TypeError):
                continue
                
            geo = st.get("GeoInfo", {})
            altitude_str = geo.get("StationAltitude", "0")
            try:
                altitude = float(altitude_str)
            except ValueError:
                altitude = 0.0

            # 海拔高度設定
            if not self.show_high_altitude and altitude > 100:
                continue

            lon_str, lat_str = None, None
            for coord in geo.get("Coordinates", []):
                if coord.get("CoordinateName") == "WGS84":
                    lon_str = coord.get("StationLongitude")
                    lat_str = coord.get("StationLatitude")
                    break
            
            if not lon_str or not lat_str:
                continue
                
            try:
                lon, lat = float(lon_str), float(lat_str)
            except ValueError:
                continue
                
            px, py = lonlat_to_img(lon, lat)
            
            if -100 <= px <= IMG_W + 100 and -100 <= py <= IMG_H + 100:
                color = get_pressure_color(pressure)
                r_size = 6
                draw.ellipse((px - r_size, py - r_size, px + r_size, py + r_size), fill=color, outline="white", width=1)

        legend_w = 15
        legend_h = 200
        legend_x = IMG_W - 60
        legend_y = IMG_H - legend_h - 40
        
        for i in range(legend_h):
            ratio = 1 - (i / legend_h)
            p = 980.0 + ratio * (1020.0 - 980.0)
            color = get_pressure_color(p)
            draw.line((legend_x, legend_y + i, legend_x + legend_w, legend_y + i), fill=color)
            
        draw.rectangle((legend_x, legend_y, legend_x + legend_w, legend_y + legend_h), outline="white", width=1)
        
        text_1020 = "1020"
        text_1005 = "1005"
        text_980 = "980"
        if hasattr(draw, 'textbbox'):
            tw_1020 = draw.textbbox((0, 0), text_1020, font=font_legend)[2] - draw.textbbox((0, 0), text_1020, font=font_legend)[0]
            tw_1005 = draw.textbbox((0, 0), text_1005, font=font_legend)[2] - draw.textbbox((0, 0), text_1005, font=font_legend)[0]
            tw_980 = draw.textbbox((0, 0), text_980, font=font_legend)[2] - draw.textbbox((0, 0), text_980, font=font_legend)[0]
        else:
            tw_1020, _ = draw.textsize(text_1020, font=font_legend)
            tw_1005, _ = draw.textsize(text_1005, font=font_legend)
            tw_980, _ = draw.textsize(text_980, font=font_legend)
            
        draw.text((legend_x - tw_1020 - 10, legend_y - 5), text_1020, fill="white", font=font_legend)
        draw.text((legend_x - tw_1005 - 10, legend_y + legend_h // 2 - 10), text_1005, fill="white", font=font_legend)
        draw.text((legend_x - tw_980 - 10, legend_y + legend_h - 15), text_980, fill="white", font=font_legend)
        draw.text((legend_x - 15, legend_y - 25), "hPa", fill="white", font=font_legend)

        output = io.BytesIO()
        img.save(output, format='PNG')
        return output.getvalue(), discord_obs_time

    # 海拔高度設定
    async def build_embed(self):
        display_results = []
        for r in self.parsed_results:
            if not self.show_high_altitude and r['altitude'] > 100:
                continue
            display_results.append(r)

        display_results.sort(key=lambda x: x['pressure'], reverse=self.is_high)
        display_results = display_results[:10]

        message_content = "🎈 現在最高氣壓排行" if self.is_high else "🎈 現在最低氣壓排行"
        if not self.show_high_altitude:
            message_content += " (排除100m以上地區)"

        embed = discord.Embed(color=0x3498db)
        
        lines = []
        for i, r in enumerate(display_results):
            p_val = r['pressure']
            icon = "⚪"
            if p_val >= 1020: icon = "🟣"
            elif p_val >= 1010: icon = "🔵"
            elif p_val >= 1000: icon = "⚪️"
            elif p_val >= 990: icon = "🟡"
            else: icon = "🔴"

            num_emoji = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'][i]
            if i < 3:
                rank_str = ['`🥇`', '`🥈`', '`🥉`'][i]
                line = f"{num_emoji} `{icon} {p_val} hPa` **{r['county']}{r['town']}** {rank_str}"
            else:
                line = f"{num_emoji} `{icon} {p_val} hPa` **{r['county']}{r['town']}**"

            if self.show_details:
                line += f"\n>  {r['station']} | 海拔 {r['altitude']}m"
            lines.append(line)
        
        embed.description = "\n".join(lines)
        if not lines:
            embed.description = "目前尚無氣壓資料"

        file = None
        if self.show_image:
            if self.cached_image is None:
                img_bytes, obs_time = await asyncio.to_thread(self.generate_map, self.data)
                self.cached_image = img_bytes
                self.cached_obs_time = obs_time
            
            if self.cached_image:
                file = discord.File(io.BytesIO(self.cached_image), filename="airpressure_map.png")
                embed.set_image(url="attachment://airpressure_map.png")
            else:
                embed.description += "\n\n❌ **目前無法取得氣壓觀測圖**"
                
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return message_content, embed, file

    @discord.ui.select(
        placeholder="選擇氣壓排行類型",
        options=[
            discord.SelectOption(label="最高氣壓", value="high_all"),
            discord.SelectOption(label="最高氣壓 (不含100m以上)", value="high_no_high"),
            discord.SelectOption(label="最低氣壓", value="low_all"),
            discord.SelectOption(label="最低氣壓 (不含100m以上)", value="low_no_high")
        ],
        row=0
    )
    async def select_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        val = select.values[0]
        self.is_high = val.startswith("high")
        old_high_alt = self.show_high_altitude
        self.show_high_altitude = val.endswith("all")
        if old_high_alt != self.show_high_altitude:
            self.cached_image = None
        self.update_buttons()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="顯示詳細資訊", style=discord.ButtonStyle.primary, row=1)
    async def toggle_details(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.show_details = not self.show_details
        self.update_buttons()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

    @discord.ui.button(label="顯示氣壓圖", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_image(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.show_image = not self.show_image
        self.update_buttons()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])

class AirPressureCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @async_cache(ttl_seconds=300)
    async def fetch_airpressure_data(self):
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0001-001?Authorization={self.api_key}&WeatherElement=AirPressure"
        try:
            async with self.bot.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            logger.error(f"❌ 抓取氣壓資料失敗: {e}")
        return None

    @app_commands.command(name="氣壓排行", description="🎈 查詢台灣各測站的即時氣壓列表與氣壓分布圖 Air Pressure")
    @app_commands.describe(
        氣壓類型="選擇查詢最高氣壓或最低氣壓 (預設為最低氣壓)",
        高海拔="是否包含100m以上測站",
        氣壓圖="是否顯示氣壓分布圖"
    )
    @app_commands.choices(氣壓類型=[
        app_commands.Choice(name="最高氣壓", value="high"),
        app_commands.Choice(name="最低氣壓", value="low")
    ], 高海拔=[
        app_commands.Choice(name="是", value="yes"),
        app_commands.Choice(name="否", value="no")
    ], 氣壓圖=[
        app_commands.Choice(name="顯示", value="yes"),
        app_commands.Choice(name="不顯示", value="no")
    ])
    async def airpressure_command(self, interaction: discord.Interaction, 氣壓類型: app_commands.Choice[str] = None, 高海拔: app_commands.Choice[str] = None, 氣壓圖: app_commands.Choice[str] = None):
        if not self.api_key:
            await interaction.response.send_message("⚠️ 未設定 API Key，無法查詢資料。", ephemeral=True)
            return

        await interaction.response.defer()
        
        try:
            data = await self.fetch_airpressure_data()
            if not data:
                await interaction.followup.send("⚠️ API 請求失敗或無法獲取資料。")
                return

            stations = data.get('records', {}).get('Station', [])
            if not stations:
                self.fetch_airpressure_data.invalidate_all()
                await interaction.followup.send("⚠️ 找不到有效的氣壓資料。")
                return

            is_high = False
            if 氣壓類型 and 氣壓類型.value == 'high':
                is_high = True
                
            show_high_altitude = True
            if 高海拔 and 高海拔.value == 'no':
                show_high_altitude = False
                
            show_image = False
            if 氣壓圖 and 氣壓圖.value == 'yes':
                show_image = True
                
            view = AirPressureView(self.bot, data, interaction.user.id, is_high=is_high, show_high_altitude=show_high_altitude, show_image=show_image)
            content, embed, file = await view.build_embed()
            
            if file:
                await interaction.followup.send(content=content, embed=embed, file=file, view=view)
            else:
                await interaction.followup.send(content=content, embed=embed, view=view)
                
        except Exception as e:
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{e}")
            logger.error(f"❌ /氣壓 發生未預期的錯誤：{e}")

    async def refresh_message(self, interaction: discord.Interaction, message: discord.Message, cmd_name: str):
        await interaction.response.defer(ephemeral=True)
        data = await self.fetch_airpressure_data()
        if not data:
            await interaction.followup.send("❌ 無法獲取新資料。", ephemeral=True)
            return
            
        is_high = False
        show_high_altitude = True
        show_image = False
        show_details = False
        for row in message.components:
            for child in row.children:
                if getattr(child, "type", None) == discord.ComponentType.select:
                    for opt in child.options:
                        if opt.default:
                            val = opt.value
                            is_high = val.startswith("high")
                            show_high_altitude = val.endswith("all")
                elif getattr(child, "type", None) == discord.ComponentType.button:
                    if child.label == "隱藏氣壓圖": show_image = True
                    if child.label == "隱藏詳細資訊": show_details = True
                    
        view = AirPressureView(self.bot, data, interaction.user.id, is_high, show_high_altitude, show_image)
        view.show_details = show_details
        view.update_buttons()
        content, embed, file = await view.build_embed()
        await message.edit(content=content, embed=embed, view=view, attachments=[file] if file else [])
        await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AirPressureCog(bot))