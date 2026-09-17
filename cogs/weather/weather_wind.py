from datetime import datetime, timezone, timedelta
from discord.ui import Container, TextDisplay, Separator
from cogs.weather.weather_utils import get_day_and_period, get_wind_arrow, get_wind_icon

def build_wind(elements, county_name, town_name):
    daily_data = {0: {"name": "", "lines": []}, 1: {"name": "", "lines": []}, 2: {"name": "", "lines": []}}
    wind_speed = elements.get("風速", {}).get("Time", [])
    wind_dir = elements.get("風向", {}).get("Time", [])

    has_double_digit = False
    for t_data in wind_speed:
        st = t_data.get("StartTime")
        delta_days, _, _ = get_day_and_period(st)
        if 0 <= delta_days <= 2:
            val_dict = t_data.get("ElementValue", [{}])[0]
            bf_raw = str(val_dict.get('BeaufortScale', '')).strip()
            if bf_raw.isdigit() and int(bf_raw) >= 10:
                has_double_digit = True
                break

    trans = str.maketrans("0123456789", "０１２３４５６７８９")

    for i, t_data in enumerate(wind_speed):
        st = t_data.get("StartTime")
        delta_days, day_name, period = get_day_and_period(st)
        if 0 <= delta_days <= 2:
            daily_data[delta_days]["name"] = day_name
            val_dict = t_data.get("ElementValue", [{}])[0]
            bf_raw = str(val_dict.get('BeaufortScale', '?')).strip()
            if bf_raw.isdigit():
                val = int(bf_raw)
                if has_double_digit and val < 10:
                    bf = f"　{bf_raw.translate(trans)}"
                else:
                    bf = bf_raw.translate(trans)
            else:
                bf = bf_raw.translate(trans)
            ws = val_dict.get('WindSpeed', '?')
            val_wd = wind_dir[i].get("ElementValue", [{}])[0].get("WindDirection", "未知") if i < len(wind_dir) else "未知"
            icon = get_wind_arrow(val_wd)
            arrow = f" {icon}" if icon else ""
            w_icon = get_wind_icon(ws)
            daily_data[delta_days]["lines"].append(f"{period}　`{w_icon}`　**{bf}級** `{ws} m/s`  •  {val_wd}{arrow}")
            
    message_content = "💨 鄉鎮天氣預報查詢"

    # 卡片 1 (Hero Tile)
    hero_tile = Container(
        TextDisplay(
            f"**{county_name}{town_name}** 未來 3 天風向風速\n"
            f"-# 未來 72 小時風力與風向變化"
        ),
        accent_color=0x27ae60
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