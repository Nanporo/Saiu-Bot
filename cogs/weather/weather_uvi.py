from cogs.weather.weather_utils import get_day_and_period, get_uvi_icon

def build_uvi(embed, elements):
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
            icon = get_uvi_icon(uv_idx)
            daily_data[delta_days]["lines"].append(f"{period} `{icon} {uv_idx} {val_dict.get('UVExposureLevel', '?')}`")
            has_data = True

    if not has_data:
        embed.description += "\n無紫外線預報資料"
    else:
        desc_lines = [embed.description.strip(), ""]
        for d in [0, 1, 2]:
            if daily_data[d]["lines"]:
                desc_lines.append(daily_data[d]["name"])
                desc_lines.extend(daily_data[d]["lines"])
                desc_lines.append("")
        embed.description = "\n".join(desc_lines).strip()

async def setup(bot):
    pass