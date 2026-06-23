from cogs.weather.weather_utils import get_day_and_period

def build_rh(embed, elements):
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
            daily_data[delta_days]["lines"].append(f"> **{period}**\n> 濕度 {val_rh} %\n> 露點 {val_td} °C")
            
    for d in [0, 1, 2]:
        if daily_data[d]["lines"]:
            embed.add_field(name=daily_data[d]["name"], value="\n\n".join(daily_data[d]["lines"]), inline=True)

async def setup(bot):
    pass