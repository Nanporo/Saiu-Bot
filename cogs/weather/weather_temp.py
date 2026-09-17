from datetime import datetime, timezone, timedelta
from discord.ui import Container, TextDisplay, Separator
from cogs.weather.weather_utils import get_day_and_period, get_temp_icon

def build_temp(elements, wx_dict, county_name, town_name):
    daily_data = {0: {"name": "", "lines": []}, 1: {"name": "", "lines": []}, 2: {"name": "", "lines": []}}
    avg_t = elements.get("平均溫度", {}).get("Time", [])
    max_t = elements.get("最高溫度", {}).get("Time", [])
    min_t = elements.get("最低溫度", {}).get("Time", [])
    for i, t_data in enumerate(avg_t):
        st = t_data.get("StartTime")
        delta_days, day_name, period = get_day_and_period(st)
        if 0 <= delta_days <= 2:
            daily_data[delta_days]["name"] = day_name
            icon = wx_dict.get(st, "☁️")
            val_max = max_t[i].get("ElementValue", [{}])[0].get("MaxTemperature", "?") if i < len(max_t) else "?"
            val_min = min_t[i].get("ElementValue", [{}])[0].get("MinTemperature", "?") if i < len(min_t) else "?"
            temp_icon = get_temp_icon(val_max)
            daily_data[delta_days]["lines"].append(f"{icon} {period}　`{temp_icon}`　**{val_min} ~ {val_max} °C**")
            
    message_content = "🌡️ 鄉鎮天氣預報查詢"

    # 卡片 1 (Hero Tile)
    hero_tile = Container(
        TextDisplay(
            f"**{county_name}{town_name}** 未來 3 天氣溫趨勢\n"
            f"-# 未來 72 小時逐時段預報"
        ),
        accent_color=0xe67e22
    )

    # 卡片 2 (Detail Tile)
    items = []
    for d in [0, 1, 2]:
        if daily_data[d]["lines"]:
            day_title = daily_data[d]["name"].replace("**", "")
            lines_text = "\n".join(daily_data[d]["lines"])
            day_block = f"### {day_title}\n{lines_text}"
            if items:
                items.append(Separator())
            items.append(TextDisplay(day_block))

    current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    if items:
        items.append(Separator())
    items.append(TextDisplay(f"-# 中央氣象署 • 查詢時間 {current_time}"))

    detail_tile = Container(*items, accent_color=0x2b2d31)
    return message_content, hero_tile, detail_tile

async def setup(bot):
    pass