from cogs.weather.weather_utils import get_day_and_period

def build_pop(embed, elements, wx_dict):
    daily_data = {0: {"name": "", "lines": []}, 1: {"name": "", "lines": []}, 2: {"name": "", "lines": []}}
    pop = elements.get("12小時降雨機率", {}).get("Time", [])
    for i, t_data in enumerate(pop):
        st = t_data.get("StartTime")
        delta_days, day_name, period = get_day_and_period(st)
        if 0 <= delta_days <= 2:
            daily_data[delta_days]["name"] = day_name
            icon = wx_dict.get(st, "☁️")
            
            val_pop_str = t_data.get("ElementValue", [{}])[0].get("ProbabilityOfPrecipitation", "").strip()
            pop_val = int(val_pop_str) if val_pop_str.isdigit() else 0
            bar_length = 6
            filled = int(pop_val / 100 * bar_length + 0.5)
            bar = "🟦" * filled + "⬛" * (bar_length - filled)
            daily_data[delta_days]["lines"].append(f"{icon} {period} {bar} {pop_val}%")
            
    desc_lines = [embed.description.strip(), ""]
    for d in [0, 1, 2]:
        if daily_data[d]["lines"]:
            desc_lines.append(daily_data[d]["name"])
            desc_lines.extend(daily_data[d]["lines"])
            desc_lines.append("")
    embed.description = "\n".join(desc_lines).strip()

async def setup(bot):
    pass