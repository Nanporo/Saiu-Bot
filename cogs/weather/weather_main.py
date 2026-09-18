from datetime import datetime, timezone, timedelta
from discord.ui import Container, TextDisplay, Separator
from cogs.weather.weather_utils import format_time, get_temp_icon, get_uvi_icon, get_wind_arrow, get_day_and_period

def build_overview(target_location, overview_page, county_name, town_name):
    elements = {}
    for we in target_location.get("WeatherElement", []):
        elements[we.get("ElementName")] = we

    wx_elem = elements.get("天氣現象", {}).get("Time", [])
    
    if not wx_elem or overview_page >= len(wx_elem):
        empty_tile = Container(
            TextDisplay(f"**{county_name}{town_name}** 的天氣預報\n找不到對應時段的預報資料。"),
            accent_color=0xe74c3c
        )
        return "🌤️ 鄉鎮天氣預報查詢", empty_tile, None

    target_idx = overview_page
    t_data = wx_elem[target_idx]
    st = t_data.get("StartTime")
    et = t_data.get("EndTime")
    _, _, period = get_day_and_period(st)
    if period not in ["白天", "中午", "晚上"]:
        period = ""

    parsed = {}
    for name, we in elements.items():
        times = we.get("Time", [])
        val_dict = None
        for t in times:
            if t.get("StartTime") == st:
                vals = t.get("ElementValue", [])
                if vals:
                    val_dict = vals[0]
                break
        
        if not val_dict:
            continue
            
        if name == "平均溫度":
            t_str = val_dict.get('Temperature')
            parsed['T'] = f"{t_str} °C"
        elif name == "風速":
            parsed['WindSpeed'] = f"{val_dict.get('BeaufortScale')}級 `{val_dict.get('WindSpeed')} m/s`"
        elif name == "風向":
            w_dir = val_dict.get('WindDirection', '')
            parsed['WindDirection'] = f"{w_dir} {get_wind_arrow(w_dir)}".strip()
        elif name == "12小時降雨機率":
            pop = val_dict.get('ProbabilityOfPrecipitation', '')
            parsed['PoP12h'] = f"{pop} %" if pop.strip() and pop != "-" else "0 %"
        elif name == "天氣現象":
            parsed['Wx'] = val_dict.get('Weather')
        elif name == "紫外線指數":
            u_str = val_dict.get('UVIndex')
            exp_level = val_dict.get('UVExposureLevel', '')
            parsed['UVI'] = f"`{get_uvi_icon(u_str)}` {exp_level} ({u_str})" if u_str else "-"
        elif name == "平均相對濕度":
            parsed['RH'] = f"{val_dict.get('RelativeHumidity')} %"
        elif name == "天氣預報綜合描述":
            parsed['WeatherDescription'] = val_dict.get('WeatherDescription')

    wx_val = parsed.get("Wx", "未知天氣")
    temp_val = parsed.get("T", "未知氣溫")
    pop_val = parsed.get("PoP12h", "未知")
    rh_val = parsed.get("RH", "未知")
    wind_dir = parsed.get("WindDirection", "未知")
    wind_spd = parsed.get("WindSpeed", "未知")
    uvi_val = parsed.get("UVI", "-")
    weather_desc = parsed.get("WeatherDescription", "無詳細天氣描述")

    time_range_str = format_time(st, et, period)
    current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")

    # 判斷是否為晚上（優先依預報時段判斷，其次依時段開始時間或當前時間）
    is_night = False
    if period == "晚上":
        is_night = True
    elif period in ["白天", "中午"]:
        is_night = False
    elif st:
        try:
            st_hour = datetime.fromisoformat(st).hour
            is_night = (st_hour >= 18 or st_hour < 6)
        except Exception:
            pass
    if not is_night and not period:
        now_hour = datetime.now(timezone(timedelta(hours=8))).hour
        is_night = (now_hour >= 18 or now_hour < 6)

    # 依天氣現象動態決定強調色與頂部標題
    message_content = "🌤️ 鄉鎮天氣預報查詢"
    embed_color = 0x3498db
    if "雷" in wx_val:
        embed_color = 0x8e44ad
        message_content = "🌧️ 鄉鎮天氣預報查詢"
    elif "雨" in wx_val:
        embed_color = 0x2980b9
        message_content = "🌧️ 鄉鎮天氣預報查詢"
    elif "晴時多雲" in wx_val or "多雲時晴" in wx_val:
        embed_color = 0xf1c40f
        message_content = "⛅️ 鄉鎮天氣預報查詢"
    elif "晴" in wx_val:
        embed_color = 0xf1c40f
        message_content = "🌙 鄉鎮天氣預報查詢" if is_night else "☀️ 鄉鎮天氣預報查詢"
    elif "雲" in wx_val or "陰" in wx_val:
        embed_color = 0x95a5a6
        message_content = "☁️ 鄉鎮天氣預報查詢"

    # -------------------------------------------------------------
    # 卡片 1 (Hero Tile): 行政區、大字天氣現象與溫度、降雨與濕度重點、時段
    # -------------------------------------------------------------
    hero_main_text = (
        f"**{county_name}{town_name}**\n"
        f"# {wx_val}　{temp_val}\n"
        f"☔ 降雨機率 **{pop_val}**  •  💧 相對濕度 **{rh_val}**"
    )
    time_text = f"-# 預報時段 {time_range_str}"

    hero_tile = Container(
        TextDisplay(hero_main_text),
        Separator(),
        TextDisplay(time_text),
        accent_color=embed_color
    )

    # -------------------------------------------------------------
    # 卡片 2 (Detail Tile): 風力與環境、氣象概述、頁尾查詢時間
    # -------------------------------------------------------------
    env_lines = [
        f"🧭 風向　：**{wind_dir}**",
        f"💨 風速　：**{wind_spd}**",
        f"☀️ 紫外線：**{uvi_val}**"
    ]
    env_block = "**風力與環境**\n" + "\n".join(env_lines)
    desc_block = f"```{weather_desc}```"
    footer_text = f"-# 中央氣象署 • 查詢時間 {current_time}"

    detail_tile = Container(
        TextDisplay(env_block),
        Separator(),
        TextDisplay(desc_block),
        Separator(),
        TextDisplay(footer_text),
        accent_color=0x2b2d31
    )

    return message_content, hero_tile, detail_tile

async def setup(bot):
    pass