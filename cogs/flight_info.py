import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import ssl
import time
import logging
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# 全台機場 IATA 與名稱對照表
CAA_AIRPORTS = {
    "TSA": "臺北/松山機場",
    "TPE": "臺灣/桃園國際機場",
    "KHH": "高雄/小港國際機場",
    "RMQ": "臺中/清泉崗機場",
    "TNN": "臺南機場",
    "HUN": "花蓮機場",
    "CYI": "嘉義機場",
    "HCN": "恆春機場",
    "TTT": "臺東機場",
    "GNI": "綠島機場",
    "KYD": "蘭嶼機場",
    "MZG": "澎湖/馬公機場",
    "WOT": "望安機場",
    "CMJ": "七美機場",
    "KNH": "金門/尚義機場",
    "MFK": "馬祖/北竿機場",
    "LZN": "馬祖/南竿機場"
}

# ICAO 到 IATA 快速對照
ICAO_TO_IATA = {
    "RCSS": "TSA",
    "RCTP": "TPE",
    "RCKH": "KHH",
    "RCMQ": "RMQ",
    "RCNN": "TNN",
    "RCYU": "HUN",
    "RCKU": "CYI",
    "RCKW": "HCN",
    "RCFN": "TTT",
    "RCGI": "GNI",
    "RCLY": "KYD",
    "RCQC": "MZG",
    "RCWA": "WOT",
    "RCCM": "CMJ",
    "RCBS": "KNH",
    "RCMT": "MFK",
    "RCFG": "LZN"
}

# 快取機制 (TTL: 60 秒)
_flight_cache = {}

class CAAHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = {}
        self.in_table = False
        self.table_rows = []
        self.current_row = []
        self.current_cell = ""
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == "input":
            name = attr_dict.get("name") or attr_dict.get("id")
            val = attr_dict.get("value", "")
            if name:
                self.inputs[name] = val
                self.inputs[attr_dict.get("id", "")] = val

        if tag == "table" and "timetable" in attr_dict.get("class", ""):
            self.in_table = True

        if self.in_table:
            if tag == "tr":
                self.current_row = []
            elif tag in ("th", "td"):
                self.in_cell = True
                self.current_cell = ""

    def handle_endtag(self, tag):
        if tag == "table" and self.in_table:
            self.in_table = False
        if self.in_table:
            if tag == "tr":
                if self.current_row:
                    self.table_rows.append(self.current_row)
                self.current_row = []
            elif tag in ("th", "td"):
                self.in_cell = False
                clean_text = self.current_cell.replace("\r", "").replace("\n", " ").strip()
                clean_text = " ".join(clean_text.split())
                self.current_row.append(clean_text)
                self.current_cell = ""

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell += data


def is_flight_after_cutoff(flight: dict, cutoff_dt: datetime, current_dt: datetime) -> bool:
    """
    判斷航班時間是否在 cutoff_dt (目前時間 - 1 小時) 之後
    """
    time_str = flight.get("actual_time") or flight.get("scheduled_time") or ""
    time_str = time_str.strip()
    
    if not time_str or ":" not in time_str:
        return True
        
    try:
        parts = time_str.split(":")
        f_hour = int(parts[0])
        f_minute = int(parts[1])
        
        f_dt = current_dt.replace(hour=f_hour, minute=f_minute, second=0, microsecond=0)
        
        # 處理跨夜情境 (例如現在凌晨 01:00，cutoff 為昨天 23:00)
        if current_dt.hour < 4 and f_hour >= 20:
            f_dt -= timedelta(days=1)
        elif current_dt.hour >= 20 and f_hour < 4:
            f_dt += timedelta(days=1)
            
        return f_dt >= cutoff_dt
    except Exception:
        return True


def clean_location_name(loc: str) -> str:
    if not loc:
        return ""
    return loc.replace("一國際機場", "").replace("國際機場", "").replace("國際", "").replace("機場", "").strip()


async def fetch_caa_flights(airport_code: str, line_type: str = "1", aord: str = "D") -> list:
    cache_key = f"{airport_code}_{line_type}_{aord}"
    now = time.time()
    
    if cache_key in _flight_cache:
        cached_time, cached_data = _flight_cache[cache_key]
        if now - cached_time < 60:
            print(f"[CAA Cache Hit] 使用快取的班機資料 ({cache_key})，剩餘快取時間: {int(60 - (now - cached_time))} 秒")
            return cached_data

    url = "https://www.caa.gov.tw/ImmediateFlight.aspx?a=270&lang=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            # 1. GET 初始 ViewState 參數
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                print(f"[CAA HTTP GET] Status: {resp.status} ({airport_code}, line={line_type}, aord={aord})")
                if resp.status != 200:
                    logger.error(f"❌ CAA 網站 GET 失敗: Status {resp.status}")
                    return []
                html = await resp.text()
                parser = CAAHTMLParser()
                parser.feed(html)

                viewstate_val = parser.inputs.get("__VIEWSTATE", "")
                viewstate_gen_val = parser.inputs.get("__VIEWSTATEGENERATOR", "")
                eventval_val = parser.inputs.get("__EVENTVALIDATION", "")

            # 2. 直接 POST btnSearch 進行查詢 (不使用 EventTarget 以免觸發 ASP.NET 重置為國內線)
            payload = {
                "__VIEWSTATE": viewstate_val,
                "__VIEWSTATEGENERATOR": viewstate_gen_val,
                "__EVENTVALIDATION": eventval_val,
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "ctl00$ContentPlaceHolder1$ddlAirport": airport_code,
                "ctl00$ContentPlaceHolder1$rdolSelectLine": line_type,
                "ctl00$ContentPlaceHolder1$rdolSelectAorD": aord,
                "ctl00$ContentPlaceHolder1$btnSearch": "查詢"
            }

            async with session.post(url, data=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                print(f"[CAA HTTP POST] Status: {resp.status}")
                if resp.status != 200:
                    logger.error(f"❌ CAA 網站 POST 失敗: Status {resp.status}")
                    return []
                post_html = await resp.text()
                parser3 = CAAHTMLParser()
                parser3.feed(post_html)

                # 取得台灣目前時間 (UTC+8) 與往前推 1 小時的時間門檻
                tz_tw = timezone(timedelta(hours=8))
                now_tw = datetime.now(tz_tw)
                cutoff_tw = now_tw - timedelta(hours=1)

                ap_raw_name = CAA_AIRPORTS.get(airport_code, "").split("/")[0]
                current_ap_clean = clean_location_name(ap_raw_name)

                flights = []
                for row in parser3.table_rows:
                    if not row or "表定" in row[0]:
                        continue
                    if len(row) >= 6:
                        loc_cleaned = clean_location_name(row[3])
                        # 當查詢國際線時，若回傳的地點與查詢的機場相同（民航局端將國內線強制返回之現象），予以過濾
                        if line_type == "2" and current_ap_clean and (loc_cleaned == current_ap_clean or current_ap_clean in loc_cleaned):
                            continue

                        item = {
                            "scheduled_time": row[0],
                            "actual_time": row[1],
                            "flight_no": row[2],
                            "location": loc_cleaned,
                            "terminal": row[4],
                            "status": row[5]
                        }
                        if is_flight_after_cutoff(item, cutoff_tw, now_tw):
                            flights.append(item)
                
                _flight_cache[cache_key] = (now, flights)
                return flights

    except Exception as e:
        logger.error(f"❌ CAA 班機資料抓取異常: {e!r}")
        return []


# 載入呼號與國家/地區 Flag 對照表
CALLSIGN_COUNTRY_MAP = {}
try:
    import json
    with open('maps/callsign_country.json', 'r', encoding='utf-8') as f:
        CALLSIGN_COUNTRY_MAP = json.load(f)
except Exception as e:
    logger.warning(f"⚠️ 無法讀取 maps/callsign_country.json: {e!r}")

IATA_TO_ICAO = {
    "AE": "MDA", "B7": "UIA", "BR": "EVA", "CI": "CAL", "JX": "SJX", "IT": "TTW", "FE": "FEA",
    "NH": "ANA", "JL": "JAL", "MM": "APJ", "JW": "VNL", "CX": "CPA", "UO": "HKE", "HX": "CRK",
    "KE": "KAL", "OZ": "AAR", "7C": "JJA", "LJ": "JNA", "TW": "TWB", "BX": "ABL", "CA": "CCA",
    "MU": "CES", "CZ": "CSN", "MF": "CXA", "ZH": "CSZ", "HO": "DKH", "9C": "CQH", "SQ": "SIA",
    "TR": "TGW", "MH": "MAS", "AK": "AXM", "TG": "THA", "VZ": "TVJ", "VJ": "VJC", "VN": "HVN",
    "PR": "PAL", "5J": "CEB", "UA": "UAL", "DL": "DAL", "AA": "AAL", "EK": "UAE", "QR": "QTR",
    "TK": "THY", "KL": "KLM", "QF": "QFA", "NZ": "ANZ", "AS": "ASA", "EY": "ETD", "5Y": "GTI",
    "CV": "CLX", "FX": "FDX", "5X": "UPS"
}

AIRLINE_NAME_MAP = {
    "立榮": "立榮航空",
    "華信": "華信航空",
    "長榮": "長榮航空",
    "中華": "中華航空",
    "星宇": "星宇航空",
    "德安": "德安航空",
    "遠東": "遠東航空",
    "臺灣虎": "臺灣虎航",
    "台灣虎": "台灣虎航",
    "中國國際": "中國國際航空",
    "哥倫比亞": "哥倫比亞航空",
    "捷星日本": "捷星日本航空",
    "泰亞洲": "泰亞洲航空",
    "泰國獅子": "泰國獅子航空",
    "馬來西亞": "馬來西亞航空",
    "聯邦": "聯邦快遞",
    "香港快運": "香港快運",
    "大韓": "大韓航空",
    "韓亞": "韓亞航空",
    "日本": "日本航空",
    "全日空": "全日空航空",
    "新加坡": "新加坡航空",
    "越捷": "越捷航空",
    "越南": "越南航空",
    "泰國": "泰國航空",
    "土耳其": "土耳其航空",
    "阿聯酋": "阿聯酋航空",
    "國泰": "國泰航空",
    "吉祥": "吉祥航空",
    "春秋": "春秋航空",
    "海南": "海南航空",
    "深圳": "深圳航空",
    "廈門": "廈門航空",
    "四川": "四川航空",
    "山東": "山東航空",
    "上海": "上海航空",
    "捷星": "捷星航空",
    "香港": "香港航空",
    "阿特拉斯": "阿特拉斯航空",
    "聯合": "聯合航空",
}

def clean_airline_name(name: str) -> str:
    name = name.strip()
    if not name:
        return ""
    if name in AIRLINE_NAME_MAP:
        return AIRLINE_NAME_MAP[name]
    if not any(name.endswith(suffix) for suffix in ["航空", "航", "快遞", "Air", "Airlines", "Express", "Cargo"]):
        return name + "航空"
    return name

def parse_flight_no(flight_no_str: str):
    flight_no_str = flight_no_str.strip()
    if "/" in flight_no_str:
        parts = flight_no_str.split("/")
        airline_name = clean_airline_name(parts[0])
        flight_code = parts[1].strip()
    else:
        airline_name = ""
        flight_code = flight_no_str

    if not airline_name:
        airline_name = "未知公司"

    return airline_name, flight_code

def get_flight_flag(flight_code: str) -> str:
    code_upper = flight_code.upper().strip()
    if not code_upper:
        return "✈️"
    
    iata2 = code_upper[:2]
    if iata2 in CALLSIGN_COUNTRY_MAP:
        return CALLSIGN_COUNTRY_MAP[iata2]

    icao3 = IATA_TO_ICAO.get(iata2)
    if icao3 and icao3 in CALLSIGN_COUNTRY_MAP:
        return CALLSIGN_COUNTRY_MAP[icao3]
        
    icao_prefix = code_upper[:3]
    if icao_prefix in CALLSIGN_COUNTRY_MAP:
        return CALLSIGN_COUNTRY_MAP[icao_prefix]

    return "✈️"

def time_to_discord_timestamp(time_str: str, base_dt: datetime) -> str:
    time_str = (time_str or "").strip()
    if not time_str or ":" not in time_str:
        return "`無`"
    try:
        parts = time_str.split(":")
        h = int(parts[0])
        m = int(parts[1])
        f_dt = base_dt.replace(hour=h, minute=m, second=0, microsecond=0)
        
        if base_dt.hour < 4 and h >= 20:
            f_dt -= timedelta(days=1)
        elif base_dt.hour >= 20 and h < 4:
            f_dt += timedelta(days=1)
            
        ts = int(f_dt.timestamp())
        return f"<t:{ts}:t>"
    except Exception:
        return f"`{time_str}`"


def build_flight_embed(airport_code: str, line_type: str, aord: str, flights: list, detail_mode: bool = False) -> discord.Embed:
    airport_name = CAA_AIRPORTS.get(airport_code, airport_code)
    line_str = "國內線" if line_type == "1" else "國際及兩岸"
    aord_str = "🛫 離站" if aord == "D" else "🛬 到站"

    embed = discord.Embed(
        title=f"{airport_name} 即時動態",
        color=0x00A8FF
    )

    if not flights:
        embed.description = f"**類別**：{line_str} | **方向**：{aord_str}\n\n⚠️ 目前無相符或近 1 小時內的班機資訊。"
        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")
        embed.set_footer(text=f"交通部民用航空局 • 查詢時間 {current_time}")
        return embed

    # 只顯示前 12 筆航班
    display_flights = flights[:12]
    now_tw = datetime.now(timezone(timedelta(hours=8)))
    desc_lines = [f"**類別**：{line_str} | **方向**：{aord_str}\n"]

    for item in display_flights:
        status_raw = item["status"]
        if "準時" in status_raw or "抵達" in status_raw or "起飛" in status_raw:
            status_emoji = "🟢"
        elif "延誤" in status_raw or "變更" in status_raw:
            status_emoji = "🟡"
        elif "取消" in status_raw:
            status_emoji = "🔴"
        else:
            status_emoji = "✈️"

        airline_name, flight_code = parse_flight_no(item['flight_no'])
        flag = get_flight_flag(flight_code)
        term_info = f" T{item['terminal']}" if item['terminal'] and item['terminal'] != "-" else ""
        name_display = f"**{airline_name}** " if airline_name else ""

        sched_ts = time_to_discord_timestamp(item['scheduled_time'], now_tw)
        act_ts = time_to_discord_timestamp(item['actual_time'], now_tw) if item['actual_time'] else sched_ts

        if not detail_mode:
            # 預設顯示 (單行)
            line = f"{flag} {name_display}`{flight_code}` {item['location']} {sched_ts} `{status_emoji}`"
            desc_lines.append(line)
        else:
            # 顯示詳細資訊 (雙行)
            line1 = f"{flag} {name_display}`{flight_code}`{term_info} → {item['location']}"
            line2 = f"表定 {sched_ts} | 預計 {act_ts} | 狀態 `{status_emoji} {status_raw}`"
            desc_lines.append(f"{line1}\n{line2}")

    embed.description = "\n".join(desc_lines).strip()
    current_time = now_tw.strftime("%m-%d %H:%M:%S")
    embed.set_footer(text=f"交通部民用航空局 • 查詢時間 {current_time}")
    return embed


def build_single_flight_detail_embed(airport_code: str, flight: dict, line_type: str = "1", aord: str = "D") -> discord.Embed:
    airport_name = CAA_AIRPORTS.get(airport_code, airport_code)
    status_raw = flight["status"]
    if "準時" in status_raw or "抵達" in status_raw or "起飛" in status_raw:
        status_emoji = "🟢"
    elif "延誤" in status_raw or "變更" in status_raw:
        status_emoji = "🟡"
    elif "取消" in status_raw:
        status_emoji = "🔴"
    else:
        status_emoji = "✈️"

    loc_label = "到達地點" if aord == "D" else "出發地點"
    now_tw = datetime.now(timezone(timedelta(hours=8)))
    
    airline_name, flight_code = parse_flight_no(flight['flight_no'])
    flag = get_flight_flag(flight_code)

    sched_ts = time_to_discord_timestamp(flight['scheduled_time'], now_tw)
    act_ts = time_to_discord_timestamp(flight['actual_time'], now_tw) if flight['actual_time'] else sched_ts

    title_no = f"{airline_name} {flight_code}" if airline_name and airline_name != "未知公司" else flight['flight_no']
    embed = discord.Embed(
        title=f"{flag} {title_no} 航班詳細動態",
        color=0x3498db
    )
    embed.add_field(name="查詢機場", value=airport_name, inline=True)
    embed.add_field(name=f"{loc_label}", value=flight['location'], inline=True)
    embed.add_field(name="航廈", value=flight['terminal'] if flight['terminal'] and flight['terminal'] != "-" else "無", inline=True)
    
    embed.add_field(name="表定時間", value=sched_ts, inline=True)
    embed.add_field(name="預計時間", value=act_ts, inline=True)
    embed.add_field(name="當前狀態", value=f"`{status_emoji}` {status_raw}", inline=True)

    current_time = now_tw.strftime("%m-%d %H:%M:%S")
    embed.set_footer(text=f"交通部民用航空局 • 查詢時間 {current_time}")
    return embed


class FlightView(discord.ui.View):
    def __init__(self, bot, author_id: int, airport_code: str = "TPE", line_type: str = "2", aord: str = "D"):
        super().__init__(timeout=300)
        self.bot = bot
        self.author_id = author_id
        self.airport_code = airport_code
        self.line_type = line_type
        self.aord = aord
        self.flights = None
        self.selected_index = 0
        self.view_mode = "simple"  # "simple", "detail", "single_detail"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def update_flight_select(self):
        """更新下拉選單為該 12 筆航班"""
        display_flights = (self.flights or [])[:12]
        options = []

        if not display_flights:
            options.append(discord.SelectOption(label="目前無可選航班", value="none", default=True))
            self.select_flight.options = options
            self.select_flight.disabled = True
            return

        self.select_flight.disabled = False
        for idx, f in enumerate(display_flights):
            airline_name, flight_code = parse_flight_no(f['flight_no'])
            flag = get_flight_flag(flight_code)
            
            label_name = f"{airline_name} {flight_code}" if airline_name else flight_code
            label = f"{label_name} → {f['location']}"
            desc = f"表定 {f['scheduled_time']} | 狀態: {f['status']}"
            
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(idx),
                    description=desc[:100],
                    emoji=flag if len(flag) <= 4 else None,
                    default=(idx == self.selected_index and self.view_mode == "single_detail")
                )
            )
        self.select_flight.options = options

    async def update_view(self, interaction: discord.Interaction):
        if self.flights is None and self.view_mode in ["simple", "detail"]:
            self.flights = await fetch_caa_flights(self.airport_code, self.line_type, self.aord)

        self.update_flight_select()

        if self.view_mode == "single_detail" and self.flights and self.selected_index < len(self.flights):
            flight = self.flights[self.selected_index]
            embed = build_single_flight_detail_embed(self.airport_code, flight, self.line_type, self.aord)
            self.btn_detail.label = "顯示詳細資訊"
            self.btn_detail.style = discord.ButtonStyle.primary
            self.btn_back.disabled = False
        elif self.view_mode == "detail":
            embed = build_flight_embed(self.airport_code, self.line_type, self.aord, self.flights or [], detail_mode=True)
            self.btn_detail.label = "隱藏詳細資訊"
            self.btn_detail.style = discord.ButtonStyle.secondary
            self.btn_back.disabled = False
        else:
            self.view_mode = "simple"
            embed = build_flight_embed(self.airport_code, self.line_type, self.aord, self.flights or [], detail_mode=False)
            self.btn_detail.label = "顯示詳細資訊"
            self.btn_detail.style = discord.ButtonStyle.primary
            self.btn_back.disabled = True

        await interaction.edit_original_response(content="✈️ 即時航班資訊", embed=embed, view=self)

    @discord.ui.select(placeholder="選擇航班檢視詳細資訊...", row=0, options=[discord.SelectOption(label="載入中...", value="none")])
    async def select_flight(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        if select.values[0] == "none":
            return
        self.selected_index = int(select.values[0])
        self.view_mode = "single_detail"
        await self.update_view(interaction)

    @discord.ui.button(label="離站", emoji="🛫", style=discord.ButtonStyle.secondary, row=1)
    async def btn_dep(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.aord = "D"
        self.view_mode = "simple"
        self.selected_index = 0
        self.flights = None
        await self.update_view(interaction)

    @discord.ui.button(label="到站", emoji="🛬", style=discord.ButtonStyle.secondary, row=1)
    async def btn_arr(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.aord = "A"
        self.view_mode = "simple"
        self.selected_index = 0
        self.flights = None
        await self.update_view(interaction)

    @discord.ui.button(label="國內線", emoji="🇹🇼", style=discord.ButtonStyle.secondary, row=1)
    async def btn_domestic(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.line_type = "1"
        self.view_mode = "simple"
        self.selected_index = 0
        self.flights = None
        await self.update_view(interaction)

    @discord.ui.button(label="國際線", emoji="🌐", style=discord.ButtonStyle.secondary, row=1)
    async def btn_intl(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.line_type = "2"
        self.view_mode = "simple"
        self.selected_index = 0
        self.flights = None
        await self.update_view(interaction)

    @discord.ui.button(label="返回", emoji="↩️", style=discord.ButtonStyle.gray, disabled=True, row=2)
    async def btn_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.view_mode = "simple"
        await self.update_view(interaction)

    @discord.ui.button(label="顯示詳細資訊", style=discord.ButtonStyle.primary, row=2)
    async def btn_detail(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.view_mode == "detail":
            self.view_mode = "simple"
        else:
            self.view_mode = "detail"
        await self.update_view(interaction)

class FlightCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="即時航班", description="✈️ 查詢臺灣各機場的班機即時離到站動態與詳細資訊")
    @app_commands.describe(
        機場="選擇或輸入要查詢的台灣機場 (預設為桃園國際 TPE)",
        航班編號="直接輸入航班編號查詢詳細資訊 (如 AE361, B78751)",
        離到站="選擇離站 (出發) 或到站 (抵達)",
        航線="選擇國內線或國際及兩岸航線 (預設依機場自動選擇)"
    )
    @app_commands.choices(
        離到站=[
            app_commands.Choice(name="🛫 離站 (Departure)", value="D"),
            app_commands.Choice(name="🛬 到站 (Arrival)", value="A"),
        ],
        航線=[
            app_commands.Choice(name="🇹🇼 國內線", value="1"),
            app_commands.Choice(name="🌐 國際及兩岸", value="2"),
        ]
    )
    async def flight_command(
        self,
        interaction: discord.Interaction,
        機場: str = "TPE",
        航班編號: str = None,
        離到站: str = "D",
        航線: str = None
    ):
        await interaction.response.defer()

        target_code = "TPE"
        keyword = 機場.upper().strip()
        
        if keyword in CAA_AIRPORTS:
            target_code = keyword
        else:
            for code, name in CAA_AIRPORTS.items():
                if keyword in code or keyword in name:
                    target_code = code
                    break

        if 航線 is None:
            航線 = "2" if target_code in ("TPE", "TSA", "KHH", "RMQ") else "1"

        view = FlightView(self.bot, interaction.user.id, airport_code=target_code, line_type=航線, aord=離到站)
        view.flights = await fetch_caa_flights(target_code, 航線, 離到站)

        # 若使用者直接指定航班編號進行查詢
        if 航班編號:
            query_no = 航班編號.upper().replace(" ", "").replace("-", "")
            target_idx = None
            for idx, f in enumerate(view.flights):
                clean_no = f["flight_no"].upper().replace(" ", "").replace("-", "")
                if query_no in clean_no:
                    target_idx = idx
                    break

            if target_idx is not None:
                view.selected_index = target_idx
                view.view_mode = "detail"
                view.update_flight_select()
                embed = build_single_flight_detail_embed(target_code, view.flights[target_idx], 航線, 離到站)
                await interaction.followup.send(content="✈️ 即時航班資訊", embed=embed, view=view)
                return
            else:
                # 找不到指定的航班編號
                view.update_flight_select()
                embed = build_flight_embed(target_code, 航線, 離到站, view.flights)
                await interaction.followup.send(content=f"⚠️ 在此列表中找不到航班編號「{航班編號}」，已顯示最新航班列表。", embed=embed, view=view)
                return

        view.update_flight_select()
        embed = build_flight_embed(target_code, 航線, 離到站, view.flights)
        await interaction.followup.send(content="✈️ 即時航班資訊", embed=embed, view=view)

    @flight_command.autocomplete("機場")
    async def flight_airport_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        current = current.upper().strip().replace("台", "臺")
        matched = []
        for code, name in CAA_AIRPORTS.items():
            if not current or current in code or current in name.upper():
                matched.append(app_commands.Choice(name=f"{name} ({code})", value=code))
            if len(matched) >= 25:
                break
        return matched

async def setup(bot):
    await bot.add_cog(FlightCog(bot))
