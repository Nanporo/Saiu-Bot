from cogs.weather.weather_utils import get_day_and_period, get_wind_arrow

def build_wind(embed, elements):
    daily_data = {0: {"name": "", "lines": []}, 1: {"name": "", "lines": []}, 2: {"name": "", "lines": []}}
    wind_speed = elements.get("風速", {}).get("Time", [])
    wind_dir = elements.get("風向", {}).get("Time", [])
    for i, t_data in enumerate(wind_speed):
        st = t_data.get("StartTime")
        delta_days, day_name, period = get_day_and_period(st)
        if 0 <= delta_days <= 2:
            daily_data[delta_days]["name"] = day_name
            val_dict = t_data.get("ElementValue", [{}])[0]
            val_ws = f"{val_dict.get('BeaufortScale')}級 `({val_dict.get('WindSpeed')} m/s)`"
            val_wd = wind_dir[i].get("ElementValue", [{}])[0].get("WindDirection", "未知") if i < len(wind_dir) else "未知"
            icon = get_wind_arrow(val_wd)
            arrow = f"{icon}" if icon else ""
            daily_data[delta_days]["lines"].append(f"**{period}**\n* {val_wd} `{arrow}`\n* {val_ws}")
            
    for d in [0, 1, 2]:
        if daily_data[d]["lines"]:
            embed.add_field(name=daily_data[d]["name"], value="\n\n".join(daily_data[d]["lines"]), inline=True)

async def setup(bot):
    pass