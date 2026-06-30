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

class AqiRankView(discord.ui.View):
    def __init__(self, bot, data, author_id: int, is_high=True, show_image=False):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.bot = bot
        self.data = data
        self.stations = data if isinstance(data, list) else data.get("records", [])
        self.is_high = is_high
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
        from modules.town_mapping import load_town_mapping
        import math
        
        def haversine_dist(lat1, lon1, lat2, lon2):
            R = 6371.0
            lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            return R * c

        mapping = load_town_mapping()
        
        all_towns = []
        for v_list in mapping.values():
            for fullname, lat, lon in v_list:
                if lat and lon and fullname not in [t[0] for t in all_towns]:
                    all_towns.append((fullname, lat, lon))

        for st in self.stations:
            station_name = st.get('sitename', '未知')
            county = st.get('county', '')
            aqi_str = st.get('aqi', '')
            time_str = st.get('publishtime', '')
            status = st.get('status', '')
            lat_str = st.get('latitude')
            lon_str = st.get('longitude')

            try:
                aqi_val = int(aqi_str)
            except (ValueError, TypeError):
                continue

            try:
                if not time_str or time_str == "未知":
                    time_format = "未知"
                else:
                    fmt_str = time_str.replace("/", "-")
                    dt = datetime.strptime(fmt_str, "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                    time_format = f"<t:{int(dt.timestamp())}:t>"
            except Exception:
                time_format = "未知"

            town_name = ""
            if station_name in mapping:
                for fullname, t_lat, t_lon in mapping[station_name]:
                    if fullname.startswith(county):
                        town_name = fullname[len(county):]
                        break
            
            if not town_name and lat_str and lon_str:
                try:
                    s_lat, s_lon = float(lat_str), float(lon_str)
                    min_dist = float('inf')
                    best_town = ""
                    for fullname, t_lat, t_lon in all_towns:
                        if fullname.startswith(county) and t_lat and t_lon:
                            dist = haversine_dist(s_lat, s_lon, t_lat, t_lon)
                            if dist < min_dist:
                                min_dist = dist
                                best_town = fullname[len(county):]
                    if min_dist < 20:
                        town_name = best_town
                except ValueError:
                    pass
                    
            if not town_name:
                town_name = station_name

            self.parsed_results.append({
                "station": station_name,
                "town": town_name,
                "county": county,
                "aqi": aqi_val,
                "status": status,
                "time": time_format
            })

    def update_buttons(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label in ["顯示詳細資訊", "隱藏詳細資訊"]:
                    child.label = "隱藏詳細資訊" if self.show_details else "顯示詳細資訊"
                    child.style = discord.ButtonStyle.secondary if self.show_details else discord.ButtonStyle.primary
                elif child.label in ["顯示分布圖", "隱藏分布圖"]:
                    child.label = "隱藏分布圖" if self.show_image else "顯示分布圖"
            elif isinstance(child, discord.ui.Select):
                val = 'high' if self.is_high else 'low'
                for option in child.options:
                    option.default = (option.value == val)

    def generate_map(self, stations):
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
            logger.warning("⚠️ 找不到本地或系統內建的中文字體，已退回 Pillow 預設字體")
            font_title = ImageFont.load_default()
            font_time = ImageFont.load_default()
            font_legend = ImageFont.load_default()

        draw.text((25, 25), " 空氣品質 (AQI) 分布圖", fill="#ffffff", font=font_title)

        obs_time = "未知時間"
        discord_obs_time = "未知時間"
        if stations:
            obs_time_raw = stations[0].get("publishtime", "")
            if obs_time_raw:
                try:
                    fmt_str = obs_time_raw.replace("/", "-")
                    dt = datetime.strptime(fmt_str, "%Y-%m-%d %H:%M:%S")
                    obs_time = dt.strftime("%Y-%m-%d %H:%M")
                    discord_obs_time = f"<t:{int(dt.timestamp())}:t>"
                except Exception:
                    obs_time = obs_time_raw
                    discord_obs_time = obs_time_raw

        time_text = f"Generated by Saiu-Bot\n發布時間 {obs_time}"
        if hasattr(draw, 'multiline_textbbox'):
            text_bbox = draw.multiline_textbbox((0, 0), time_text, font=font_time)
            text_h = text_bbox[3] - text_bbox[1]
        else:
            _, text_h = draw.textsize(time_text, font=font_time)
            
        draw.multiline_text((25, IMG_H - text_h - 25), time_text, fill="#cccccc", font=font_time)

        def get_aqi_color(aqi_val):
            if aqi_val <= 50: return (0, 232, 0, 255)       # 良好 (綠)
            if aqi_val <= 100: return (255, 255, 0, 255)     # 普通 (黃)
            if aqi_val <= 150: return (255, 126, 0, 255)     # 對敏感族群不健康 (橘)
            if aqi_val <= 200: return (255, 0, 0, 255)       # 對所有族群不健康 (紅)
            if aqi_val <= 300: return (143, 63, 151, 255)    # 非常不健康 (紫)
            return (126, 0, 35, 255)                         # 危害 (褐紅)

        for st in stations:
            aqi_str = st.get("aqi")
            if aqi_str is None or str(aqi_str).strip() == "":
                continue
                
            try:
                aqi = int(aqi_str)
            except (ValueError, TypeError):
                continue
                
            lon_str = st.get("longitude")
            lat_str = st.get("latitude")
            
            if not lon_str or not lat_str or str(lon_str).strip() == "" or str(lat_str).strip() == "":
                continue
                
            try:
                lon, lat = float(lon_str), float(lat_str)
            except ValueError:
                continue
                
            px, py = lonlat_to_img(lon, lat)
            
            if -100 <= px <= IMG_W + 100 and -100 <= py <= IMG_H + 100:
                color = get_aqi_color(aqi)
                r_size = 7
                draw.ellipse((px - r_size, py - r_size, px + r_size, py + r_size), fill=color, outline="white", width=1)

        # 繪製圖例 (Legend)
        legend_labels = [
            ("良好", (0, 232, 0, 255)),
            ("普通", (255, 255, 0, 255)),
            ("對敏感族群不健康", (255, 126, 0, 255)),
            ("對所有族群不健康", (255, 0, 0, 255)),
            ("非常不健康", (126, 0, 35, 255)),
            ("危害", (143, 63, 151, 255))
        ]
        
        legend_x = IMG_W - 190
        legend_y = IMG_H - (len(legend_labels) * 30) - 40
        
        draw.rectangle((legend_x - 10, legend_y - 10, IMG_W - 20, legend_y + len(legend_labels) * 30 + 10), fill="#1a1d20", outline="#3e454b")
        
        for i, (label, color) in enumerate(legend_labels):
            y_pos = legend_y + i * 30
            draw.ellipse((legend_x, y_pos, legend_x + 12, y_pos + 12), fill=color, outline="white", width=1)
            draw.text((legend_x + 20, y_pos - 4), label, fill="white", font=font_legend)

        output = io.BytesIO()
        img.save(output, format='PNG')
        return output.getvalue(), discord_obs_time

    async def build_embed(self):
        display_results = list(self.parsed_results)
        display_results.sort(key=lambda x: x['aqi'], reverse=self.is_high)
        display_results = display_results[:10]

        message_content = "😷 最高 AQI 排行" if self.is_high else "🍃 最低 AQI 排行"

        embed = discord.Embed(color=0x3498db)
        
        lines = []
        for i, r in enumerate(display_results):
            aqi_val = r['aqi']
            icon = "🟢"
            if aqi_val > 300: icon = "🟤"
            elif aqi_val > 200: icon = "🟣"
            elif aqi_val > 150: icon = "🔴"
            elif aqi_val > 100: icon = "🟠"
            elif aqi_val > 50: icon = "🟡"

            num_emoji = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'][i]
            if i < 3:
                rank_str = ['`🥇`', '`🥈`', '`🥉`'][i]
                line = f"{num_emoji} `{icon} {aqi_val}` **{r['county']}{r['town']}** {rank_str}"
            else:
                line = f"{num_emoji} `{icon} {aqi_val}` **{r['county']}{r['town']}**"

            if self.show_details:
                line += f"\n>  {r['station']}測站 | {r['status']}"
            lines.append(line)
        
        embed.description = "\n".join(lines)
        if not lines:
            embed.description = "目前尚無 AQI 資料"

        file = None
        if self.show_image:
            if self.cached_image is None:
                img_bytes, obs_time = await asyncio.to_thread(self.generate_map, self.stations)
                self.cached_image = img_bytes
                self.cached_obs_time = obs_time
            
            if self.cached_image:
                file = discord.File(io.BytesIO(self.cached_image), filename="aqi_map.png")
                embed.set_image(url="attachment://aqi_map.png")
            else:
                embed.description += "\n\n❌ **目前無法取得空品分布圖**"
                
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"環境部 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/moenv.png")
        
        return message_content, embed, file

    @discord.ui.select(
        placeholder="選擇排行類型",
        options=[
            discord.SelectOption(label="最高 AQI (最差)", value="high"),
            discord.SelectOption(label="最低 AQI (最佳)", value="low")
        ],
        row=0
    )
    async def select_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        val = select.values[0]
        self.is_high = (val == "high")
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

    @discord.ui.button(label="顯示分布圖", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_image(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.show_image = not self.show_image
        self.update_buttons()
        content, embed, file = await self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self, attachments=[file] if file else [])


class ListAqiCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('MOENV_API_KEY')
        except Exception:
            self.api_key = None

    @async_cache(ttl_seconds=1800)
    async def fetch_aqi_data(self):
        if not self.api_key:
            return None
        url = f"https://data.moenv.gov.tw/api/v2/aqx_p_432?api_key={self.api_key}"
        try:
            async with self.bot.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, dict):
                        return data.get('records', [])
                    return data
        except Exception as e:
            logger.error(f"❌ 抓取 AQI 排行資料失敗: {e}")
        return None

    @app_commands.command(name="空氣品質排行", description="😷 查詢空氣品質 (AQI) 排行榜列表與分布圖")
    @app_commands.describe(
        排行類型="選擇查詢最高 (最差) 或最低 (最佳) AQI",
        分布圖="是否顯示分布圖"
    )
    @app_commands.choices(排行類型=[
        app_commands.Choice(name="最高 AQI (最差)", value="high"),
        app_commands.Choice(name="最低 AQI (最佳)", value="low")
    ], 分布圖=[
        app_commands.Choice(name="顯示", value="yes"),
        app_commands.Choice(name="不顯示", value="no")
    ])
    async def list_aqi_command(self, interaction: discord.Interaction, 排行類型: app_commands.Choice[str] = None, 分布圖: app_commands.Choice[str] = None):
        if not self.api_key:
            await interaction.response.send_message("⚠️ 未設定 API Key，無法查詢資料。", ephemeral=True)
            return

        await interaction.response.defer()
        
        try:
            data = await self.fetch_aqi_data()
            if not data:
                await interaction.followup.send("⚠️ API 請求失敗或無法獲取資料。")
                return

            is_high = True
            if 排行類型 and 排行類型.value == 'low':
                is_high = False
                
            show_image = False
            if 分布圖 and 分布圖.value == 'yes':
                show_image = True
                
            view = AqiRankView(self.bot, data, interaction.user.id, is_high=is_high, show_image=show_image)
            content, embed, file = await view.build_embed()
            
            if file:
                await interaction.followup.send(content=content, embed=embed, file=file, view=view)
            else:
                await interaction.followup.send(content=content, embed=embed, view=view)
                
        except Exception as e:
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{e}")
            logger.error(f"❌ /空氣品質排行 發生未預期的錯誤：{e}")

async def setup(bot):
    await bot.add_cog(ListAqiCog(bot))
