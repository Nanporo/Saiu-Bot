from cogs.weather.weather_utils import get_day_and_period, get_temp_icon

def build_temp(embed, elements, wx_dict):
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
            daily_data[delta_days]["lines"].append(f"{period} `{temp_icon} {val_min} ~ {val_max} °C`")
            
    desc_lines = [embed.description.strip(), ""]
    for d in [0, 1, 2]:
        if daily_data[d]["lines"]:
            desc_lines.append(daily_data[d]["name"])
            desc_lines.extend(daily_data[d]["lines"])
            desc_lines.append("")
    embed.description = "\n".join(desc_lines).strip()

async def setup(bot):
    pass