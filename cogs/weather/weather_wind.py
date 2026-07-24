from cogs.weather.weather_utils import get_day_and_period, get_wind_arrow, get_wind_icon

def build_wind(embed, elements):
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
            daily_data[delta_days]["lines"].append(f"{period} `{w_icon}` {bf}級 `{ws} m/s` | {val_wd}{arrow}")
            
    desc_lines = [embed.description.strip(), ""]
    for d in [0, 1, 2]:
        if daily_data[d]["lines"]:
            desc_lines.append(daily_data[d]["name"])
            desc_lines.extend(daily_data[d]["lines"])
            desc_lines.append("")
    embed.description = "\n".join(desc_lines).strip()

async def setup(bot):
    pass