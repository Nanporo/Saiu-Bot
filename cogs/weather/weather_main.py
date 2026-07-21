from cogs.weather.weather_utils import format_time, get_temp_icon, get_uvi_icon, get_wind_arrow, get_day_and_period, get_wind_icon, get_rh_icon

def build_overview(embed, target_location, overview_page, county_name, town_name):
    elements = {}
    for we in target_location.get("WeatherElement", []):
        elements[we.get("ElementName")] = we

    wx_elem = elements.get("天氣現象", {}).get("Time", [])
    
    if not wx_elem or overview_page >= len(wx_elem):
        embed.description = f"**{county_name}{town_name}** 的天氣預報\n找不到對應時段的資料\n\n"
        return

    target_idx = overview_page
    t_data = wx_elem[target_idx]
    st = t_data.get("StartTime")
    et = t_data.get("EndTime")
    _, _, period = get_day_and_period(st)
    if period not in ["白天", "晚上"]:
        period = ""
    embed.description = f"**{county_name}{town_name}** 的天氣預報\n\n"

    parsed = {}
    for name, we in elements.items():
        times = we.get("Time", [])
        val_dict = None
        for t in times:
            if t.get("StartTime") == st:
                vals = t.get("ElementValue", [])
                if vals:
                    val_dict = vals[0]
                break
        
        if not val_dict:
            continue
            
        if name == "平均溫度":
            t_str = val_dict.get('Temperature')
            parsed['T'] = f"{t_str} °C"
        elif name == "風速":
            parsed['WindSpeed'] = f"{val_dict.get('BeaufortScale')}級 `{val_dict.get('WindSpeed')} m/s`"
        elif name == "風向":
            w_dir = val_dict.get('WindDirection', '')
            parsed['WindDirection'] = f"{w_dir} {get_wind_arrow(w_dir)}".strip()
        elif name == "12小時降雨機率":
            pop = val_dict.get('ProbabilityOfPrecipitation', '')
            parsed['PoP12h'] = f"{pop} %" if pop.strip() and pop != "-" else "0 %"
        elif name == "天氣現象":
            parsed['Wx'] = val_dict.get('Weather')
        elif name == "紫外線指數":
            u_str = val_dict.get('UVIndex')
            parsed['UVI'] = f"`{get_uvi_icon(u_str)}` {u_str} {val_dict.get('UVExposureLevel')}"
        elif name == "平均相對濕度":
            parsed['RH'] = f"{val_dict.get('RelativeHumidity')} %"
        elif name == "天氣預報綜合描述":
            parsed['WeatherDescription'] = val_dict.get('WeatherDescription')

    weather_desc = parsed.get("WeatherDescription", "無詳細天氣描述")
    time_range_str = format_time(st, et, period)
    
    embed.add_field(name=time_range_str, value="", inline=False)
    embed.add_field(name="🌡️ 平均氣溫", value=f"{parsed.get('T', '未知')}", inline=True)
    embed.add_field(name="☔ 降雨機率", value=f"{parsed.get('PoP12h', '未知')}", inline=True)
    embed.add_field(name="💧 相對濕度", value=f"{parsed.get('RH', '未知')}", inline=True)
    embed.add_field(name="🧭 風向", value=f"{parsed.get('WindDirection', '未知')}", inline=True)
    embed.add_field(name="💨 風速", value=f"{parsed.get('WindSpeed', '未知')}", inline=True)
    embed.add_field(name="☀️ 紫外線", value=f"{parsed.get('UVI', '-')}", inline=True)
    embed.add_field(name="", value=f"```{weather_desc}```", inline=False)

async def setup(bot):
    pass