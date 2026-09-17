from datetime import datetime, timezone, timedelta
from discord.ui import Container, TextDisplay, Separator
from cogs.weather.weather_utils import get_day_and_period, get_uvi_icon

def build_uvi(elements, county_name, town_name):
    daily_data = {0: {"name": "", "lines": []}, 1: {"name": "", "lines": []}, 2: {"name": "", "lines": []}}
    has_data = False
    uvi = elements.get("紫外線指數", {}).get("Time", [])
    for i, t_data in enumerate(uvi):
        st = t_data.get("StartTime")
        delta_days, day_name, period = get_day_and_period(st)
        if 0 <= delta_days <= 2:
            daily_data[delta_days]["name"] = day_name
            val_dict = t_data.get("ElementValue", [{}])[0]
            uv_idx = val_dict.get('UVIndex', '?')
            exp_level = val_dict.get('UVExposureLevel', '?')
            icon = get_uvi_icon(uv_idx)
            daily_data[delta_days]["lines"].append(f"{period}　`{icon}`　**{exp_level}** ({uv_idx})")
            has_data = True

    message_content = "☀️ 鄉鎮天氣預報查詢"

    # 卡片 1 (Hero Tile)
    hero_tile = Container(
        TextDisplay(
            f"**{county_name}{town_name}** 未來 3 天紫外線預報\n"
            f"-# 紫外線指數與曝曬級別"
        ),
        accent_color=0xf39c12
    )

    # 卡片 2 (Detail Tile)
    items = []
    if not has_data:
        items.append(TextDisplay("無紫外線預報資料。"))
    else:
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