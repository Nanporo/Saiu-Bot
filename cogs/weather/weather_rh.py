from datetime import datetime, timezone, timedelta
from discord.ui import Container, TextDisplay, Separator
from cogs.weather.weather_utils import get_day_and_period, get_rh_icon

def build_rh(elements, county_name, town_name):
    daily_data = {0: {"name": "", "lines": []}, 1: {"name": "", "lines": []}, 2: {"name": "", "lines": []}}
    rh = elements.get("平均相對濕度", {}).get("Time", [])
    td = elements.get("平均露點溫度", {}).get("Time", [])
    for i, t_data in enumerate(rh):
        st = t_data.get("StartTime")
        delta_days, day_name, period = get_day_and_period(st)
        if 0 <= delta_days <= 2:
            daily_data[delta_days]["name"] = day_name
            val_rh = t_data.get("ElementValue", [{}])[0].get("RelativeHumidity", "?")
            val_td = td[i].get("ElementValue", [{}])[0].get("DewPoint", "?") if i < len(td) else "?"
            rh_icon = get_rh_icon(val_rh)
            daily_data[delta_days]["lines"].append(f"{period}　`{rh_icon}`　相對濕度 **{val_rh}%**  •  露點 **{val_td} °C**")
            
    message_content = "💧 鄉鎮天氣預報查詢"

    # 卡片 1 (Hero Tile)
    hero_tile = Container(
        TextDisplay(
            f"**{county_name}{town_name}** 未來 3 天相對濕度\n"
            f"-# 未來 72 小時濕度與露點溫度趨勢"
        ),
        accent_color=0x1abc9c
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