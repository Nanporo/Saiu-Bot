import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import zipfile
import io
import xml.etree.ElementTree as ET
import json
from datetime import datetime, timezone, timedelta

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
    url = f"https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/W-C0034-003?Authorization={CWA_API_KEY}&downloadType=WEB&format=KMZ"
    try:
        async with session.get(url) as resp:
            if resp.status != 200: return None, None
            data = await resp.read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            kml_name = next((f for f in z.namelist() if f.endswith('.kml')), None)
            if not kml_name: return None, None
            kml_content = z.read(kml_name)
        return parse_kml(kml_content)
    except Exception as e:
        print(f"⚠️ [警告] 獲取颱風侵襲機率失敗: {e}")
        return None, None

# 解析各縣市侵襲機率
def get_typhoon_probabilities(polygons_by_prob):
    def extract_num(s):
        return int(''.join(filter(str.isdigit, s))) if any(c.isdigit() for c in s) else 0

    sorted_probs = sorted(polygons_by_prob.keys(), key=extract_num, reverse=True)
    
    results = []
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
                break
        if prob_found > 0:
            results.append({"county": city, "prob": prob_found})

    results.sort(key=lambda x: x["prob"], reverse=True)
    return results

class TyphoonCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="颱風侵襲機率", description="查詢台灣各縣市的颱風暴風圈侵襲機率")
    async def typhoon_prob_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        polygons, valid_time = await fetch_typhoon_data(self.bot.session)
        if not polygons:
            await interaction.followup.send("❌ 目前無法取得氣象署的颱風暴風圈侵襲機率資料。")
            return
            
        results = get_typhoon_probabilities(polygons)
        
        embed = discord.Embed(title="", color=0xe74c3c)
        
        if not results:
            embed.description = f"**全台各縣市** 颱風暴風圈侵襲機率\n發布時間：{valid_time}\n\n✅ **目前無颱風暴風圈侵襲台灣的機率。**"
        else:
            lines = []
            for i, r in enumerate(results):
                prob_val = r['prob']
                icon = "🌀"
                if prob_val >= 75:
                    icon = "🔴"
                elif prob_val >= 50:
                    icon = "🟠"
                elif prob_val >= 25:
                    icon = "🟡"
                    
                if i < 10:
                    num_emoji = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟'][i]
                else:
                    num_emoji = f"`{i+1}.`"
                    
                rank_str = ""
                if i == 0: rank_str = " `🥇`"
                elif i == 1: rank_str = " `🥈`"
                elif i == 2: rank_str = " `🥉`"
                
                lines.append(f"{num_emoji} `{icon} {prob_val}%` **{r['county']}**{rank_str}")
                
            embed.description = f"**全台各縣市** 颱風暴風圈侵襲機率\n發布時間：{valid_time}\n\n" + "\n".join(lines)
            
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")

        await interaction.followup.send(content="🌀 颱風侵襲機率查詢", embed=embed)

async def setup(bot):
    await bot.add_cog(TyphoonCog(bot))