from cogs.weather.weather_utils import format_time, get_temp_icon, get_uvi_icon, get_wind_arrow

def build_overview(embed, target_location, overview_page, county_name, town_name):
    parsed = {}
    time_range_str = "未知時段"
    
    elements = {}
    for we in target_location.get("WeatherElement", []):
        elements[we.get("ElementName")] = we

    wx_elem = elements.get("天氣現象", {}).get("Time", [])
    if wx_elem:
        t_idx = min(overview_page * 2, len(wx_elem) - 1)
        st = wx_elem[t_idx].get("StartTime")
        et = wx_elem[t_idx].get("EndTime")
        time_range_str = format_time(st, et)

    for we in target_location.get("WeatherElement", []):
        name = we.get("ElementName")
        times = we.get("Time", [])
        if not times:
            continue
        
        target_idx = min(overview_page * 2, len(times) - 1)
        target_time = times[target_idx]
        vals = target_time.get("ElementValue", [])
        if not vals:
            continue
            
        val_dict = vals[0]
        if name == "平均溫度":
            t_str = val_dict.get('Temperature')
            parsed['T'] = f"`{get_temp_icon(t_str)}` {t_str} °C"
        elif name == "最高溫度":
            parsed['MaxT'] = f"{val_dict.get('MaxTemperature')} °C"
        elif name == "最低溫度":
            parsed['MinT'] = f"{val_dict.get('MinTemperature')} °C"
        elif name == "平均露點溫度":
            parsed['Td'] = f"{val_dict.get('DewPoint')} °C"
        elif name == "平均相對濕度":
            parsed['RH'] = f"{val_dict.get('RelativeHumidity')} %"
        elif name == "最高體感溫度":
            parsed['MaxAT'] = f"{val_dict.get('MaxApparentTemperature')} °C"
        elif name == "最低體感溫度":
            parsed['MinAT'] = f"{val_dict.get('MinApparentTemperature')} °C"
        elif name == "風速":
            parsed['WindSpeed'] = f"{val_dict.get('BeaufortScale')}級 `({val_dict.get('WindSpeed')} m/s)`"
        elif name == "風向":
            w_dir = val_dict.get('WindDirection', '')
            parsed['WindDirection'] = f"`{get_wind_arrow(w_dir)}` {w_dir}".strip()
        elif name == "12小時降雨機率":
            pop = val_dict.get('ProbabilityOfPrecipitation', '')
            parsed['PoP12h'] = f"{pop} %" if pop.strip() and pop != "-" else "0 %"
        elif name == "天氣現象":
            parsed['Wx'] = val_dict.get('Weather')
        elif name == "紫外線指數":
            u_str = val_dict.get('UVIndex')
            parsed['UVI'] = f"`{get_uvi_icon(u_str)}` {u_str} `({val_dict.get('UVExposureLevel')})`"
        elif name == "天氣預報綜合描述":
            parsed['WeatherDescription'] = val_dict.get('WeatherDescription')

    wx = parsed.get("Wx", "未知天氣")
    weather_desc = parsed.get("WeatherDescription", "無詳細天氣描述")

    embed.description = f"**{county_name}{town_name}** 的天氣預報\n{time_range_str}\n\n"
    
    embed.add_field(name="🌡️ 平均氣溫", value=f"{parsed.get('T', '未知')}", inline=True)
    embed.add_field(name="☔ 降雨機率", value=f"{parsed.get('PoP12h', '未知')}", inline=True)
    embed.add_field(name="💧 相對濕度", value=f"{parsed.get('RH', '未知')}", inline=True)
    embed.add_field(name="🧭 風向", value=f"{parsed.get('WindDirection', '未知')}", inline=True)
    embed.add_field(name="💨 風速", value=f"{parsed.get('WindSpeed', '未知')}", inline=True)
    embed.add_field(name="☀️ 紫外線", value=f"{parsed.get('UVI', '未知')}", inline=True)
    embed.add_field(name="", value=f"```{weather_desc}```", inline=False)

async def setup(bot):
    pass