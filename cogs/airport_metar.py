import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
import os
import csv
import logging

logger = logging.getLogger(__name__)

AIRPORT_INFO = {
    "RCSS": {"name": "臺北/松山機場", "iata": "TSA"},
    "RCTP": {"name": "臺北/桃園機場", "iata": "TPE"},
    "RCMQ": {"name": "臺中/清泉崗機場", "iata": "RMQ"},
    "RCKU": {"name": "嘉義機場", "iata": "CYI"},
    "RCNN": {"name": "臺南機場", "iata": "TNN"},
    "RCKH": {"name": "高雄/小港機場", "iata": "KHH"},
    "RCKW": {"name": "恆春機場", "iata": "HCN"},
    "RCYU": {"name": "花蓮機場", "iata": "HUN"},
    "RCFN": {"name": "臺東機場", "iata": "TTT"},
    "RCLY": {"name": "蘭嶼機場", "iata": "KYD"},
    "RCGI": {"name": "綠島機場", "iata": "GNI"},
    "RCQC": {"name": "澎湖/馬公機場", "iata": "MZG"},
    "RCCM": {"name": "七美機場", "iata": "CMJ"},
    "RCWA": {"name": "望安機場", "iata": "WOT"},
    "RCBS": {"name": "金門/尚義機場", "iata": "KNH"},
    "RCFG": {"name": "馬祖/南竿機場", "iata": "LZN"},
    "RCMT": {"name": "馬祖/北竿機場", "iata": "MFK"}
}

# 讀取全球機場資料庫
GLOBAL_IATA_TO_ICAO = {}
GLOBAL_ICAO_INFO = {}

csv_path = os.path.join(os.path.dirname(__file__), '..', 'maps', 'iata-icao.csv')
try:
    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            iata = row.get("iata", "").strip()
            icao = row.get("icao", "").strip()
            name = row.get("airport", "").strip()
            
            if icao:
                GLOBAL_ICAO_INFO[icao] = {"name": name, "iata": iata}
                if iata and len(iata) == 3:
                    GLOBAL_IATA_TO_ICAO[iata] = icao
except Exception as e:
    logger.warning(f"⚠️ [警告] 無法讀取全球機場資料庫: {e}")

class AirportView(discord.ui.View):
    def __init__(self, bot, current_icao="RCSS"):
        super().__init__(timeout=300)
        self.bot = bot
        self.current_icao = current_icao
        
        # 更新下拉選單的預設狀態
        for option in self.children[0].options:
            option.default = option.value == self.current_icao

    async def fetch_metar(self, icao):
        # 使用 NOAA Aviation Weather API 獲取標準 METAR 資料
        url = f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json"
        try:
            async with self.bot.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and len(data) > 0:
                        return data[0]
        except Exception as e:
            logger.error(f"❌ 獲取 METAR 失敗: {e}")
        return None

    async def build_embed(self, icao):
        airport = AIRPORT_INFO.get(icao) or GLOBAL_ICAO_INFO.get(icao)
        data = await self.fetch_metar(icao)
        
        if not data:
            title = f"{airport['name']} ({airport['iata']})" if airport else f"ICAO: {icao}"
            embed = discord.Embed(
                title=title, 
                description="❌ 無資料或是沒有該機場。", 
                color=0xff3846
            )
            return "✈️ 機場天氣資料", embed

        raw_ob = data.get("rawOb", "")
        temp = data.get("temp", "未知")
        wdir = data.get("wdir", "未知")
        wspd = data.get("wspd", "未知")
        
        # 轉換觀察時間為 Discord 時間戳
        obs_time = data.get("obsTime")
        discord_timestamp = "未知"
        if obs_time:
            if isinstance(obs_time, int):
                discord_timestamp = f"<t:{obs_time}:t>"
            else:
                try:
                    # API 回傳格式可能為 "2024-05-31 07:30:00" (UTC)
                    dt_utc = datetime.strptime(str(obs_time), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    discord_timestamp = f"<t:{int(dt_utc.timestamp())}:t>"
                except ValueError:
                    discord_timestamp = str(obs_time)

        # 解析能見度 (臺灣 METAR 使用公尺，例如 9999 代表 10 公里以上)
        visib_str = "未知"
        words = raw_ob.split()
        for word in words:
            if word == "9999":
                visib_str = "10 公里以上"
                break
            elif len(word) == 4 and word.isdigit():
                visib_str = f"{int(word)} 公尺"
                break

        # 天氣狀況判斷
        wx_string = data.get("wxString", "")
        clouds_data = data.get("clouds", [])
        weather_emoji = "☀️"
        weather_desc = "晴朗"

        if wx_string:
            if any(x in wx_string for x in ["RA", "DZ", "SH"]):
                weather_emoji = "🌧️"
                weather_desc = "降雨"
            elif "TS" in wx_string:
                weather_emoji = "⛈️"
                weather_desc = "雷雨"
            elif any(x in wx_string for x in ["FG", "BR", "HZ"]):
                weather_emoji = "🌫️"
                weather_desc = "霧/薄霧/霾"
            else:
                weather_emoji = "☁️"
                weather_desc = wx_string
        elif clouds_data and any(c.get("cover") in ["BKN", "OVC"] for c in clouds_data):
            weather_emoji = "☁️"
            weather_desc = "多雲/陰天"

        # 解析雲冪
        if not clouds_data:
            cloud_display = "無"
        else:
            cloud_str = []
            for c in clouds_data:
                cover = c.get("cover")
                base = c.get("base")
                
                if cover in ["CLR", "SKC", "CAVOK"]:
                    continue
                elif cover == "FEW": cover_tw = "少雲(FEW)"
                elif cover == "SCT": cover_tw = "疏雲(SCT)"
                elif cover == "BKN": cover_tw = "裂雲(BKN)"
                elif cover == "OVC": cover_tw = "密雲(OVC)"
                else: cover_tw = cover
                
                if base:
                    cloud_str.append(f"{cover_tw} {base} 呎")
                else:
                    cloud_str.append(cover_tw)
                    
            cloud_display = "、".join(cloud_str) if cloud_str else "無"

        airport_name_api = data.get("name") or icao
        title_str = f"**{airport['name']} ({airport['iata']})**" if airport else f"**{airport_name_api} ({icao})**"

        embed = discord.Embed(
            title="",
            description=f"{title_str} {discord_timestamp} 的觀測資料",
            color=0x3498db
        )
        
        embed.add_field(name="🌡️ 溫度", value=f"{temp} °C", inline=True)
        embed.add_field(name=f"{weather_emoji} 天氣", value=f"{weather_desc}", inline=True)
        embed.add_field(name="👁️ 能見度", value=f"{visib_str}", inline=True)

        embed.add_field(name="🧭 風向", value=f"{wdir}", inline=True)
        embed.add_field(name="💨 風速", value=f"{wspd} 浬/時", inline=True)
        embed.add_field(name="☁️ 雲冪", value=f"{cloud_display}", inline=True)

        embed.add_field(name="", value=f"```text\n{raw_ob}\n```", inline=False)
        
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"NOAA METAR • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/NOAA.png")

        return "✈️ 機場天氣資料", embed

    @discord.ui.select(
        placeholder="選擇要查詢的機場",
        options=[
            discord.SelectOption(label="臺北松山機場 (TSA)", value="RCSS"),
            discord.SelectOption(label="桃園國際機場 (TPE)", value="RCTP"),
            discord.SelectOption(label="臺中清泉崗機場 (RMQ)", value="RCMQ"),
            discord.SelectOption(label="臺南機場 (TNN)", value="RCNN"),
            discord.SelectOption(label="高雄小港機場 (KHH)", value="RCKH"),
            discord.SelectOption(label="澎湖馬公機場 (MZG)", value="RCQC"),
            discord.SelectOption(label="金門尚義機場 (KNH)", value="RCBS"),
            discord.SelectOption(label="馬祖南竿機場 (LZN)", value="RCFG")
        ]
    )
    async def select_airport(self, interaction: discord.Interaction, select: discord.ui.Select):
        # 預先延遲回應，避免 API 請求超過 3 秒導致 10062 Unknown interaction 錯誤
        await interaction.response.defer()
        
        self.current_icao = select.values[0]
        
        # 重新構建下拉選單的預設選項
        for option in select.options:
            option.default = option.value == self.current_icao
            
        content, embed = await self.build_embed(self.current_icao)
        await interaction.edit_original_response(content=content, embed=embed, view=self)

class AirportCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="機場天氣", description="✈️ 查詢全球各機場的最新 METAR 天氣資料")
    @app_commands.describe(機場="可輸入機場名稱、IATA 或是任意 4 碼 ICAO 代碼 (預設為桃園機場)")
    async def airport_command(self, interaction: discord.Interaction, 機場: str = None):
        await interaction.response.defer()
        
        target_icao = "RCTP"  # 預設為桃園機場
        if 機場:
            keyword = 機場.upper().strip().replace("台", "臺")
            matched_icao = None
            
            # 1. 精確比對 ICAO 或 IATA 代碼
            for icao, info in AIRPORT_INFO.items():
                if keyword == icao or keyword == info["iata"]:
                    matched_icao = icao
                    break
                    
            # 2. 模糊比對機場名稱
            if not matched_icao:
                for icao, info in AIRPORT_INFO.items():
                    if keyword in info["name"].upper():
                        matched_icao = icao
                        break
                        
            # 3. 搜尋全球機場資料庫 (精確比對 IATA 或 ICAO)
            if not matched_icao:
                if len(keyword) == 3 and keyword in GLOBAL_IATA_TO_ICAO:
                    matched_icao = GLOBAL_IATA_TO_ICAO[keyword]
                elif len(keyword) == 4 and keyword in GLOBAL_ICAO_INFO:
                    matched_icao = keyword
                    
            # 4. 全球機場模糊搜尋名稱 (如果輸入的字串長度大於 2)
            if not matched_icao and len(keyword) > 2:
                for icao, info in GLOBAL_ICAO_INFO.items():
                    if keyword in info["name"].upper():
                        matched_icao = icao
                        break
                        
            if matched_icao:
                target_icao = matched_icao
            elif len(keyword) == 4 and keyword.isascii() and keyword.isalpha():
                target_icao = keyword
            else:
                await interaction.followup.send(f"❌ 找不到與「{機場}」相符的機場，請重新確認輸入的名稱或 4 碼 ICAO 代碼。", ephemeral=True)
                return

        view = AirportView(self.bot, current_icao=target_icao)
        content, embed = await view.build_embed(target_icao)
        
        await interaction.followup.send(content=content, embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(AirportCog(bot))