from datetime import datetime, timezone, timedelta

def format_time(start, end):
    try:
        st_dt = datetime.fromisoformat(start)
        en_dt = datetime.fromisoformat(end)
        now_date = datetime.now(timezone(timedelta(hours=8))).date()
        
        delta_days = (st_dt.date() - now_date).days
        day_str = ""
        if delta_days == 0: day_str = "今天 "
        elif delta_days == 1: day_str = "明天 "
        elif delta_days == 2: day_str = "後天 "
        
        if st_dt.date() == en_dt.date():
            return f"{day_str}`{st_dt.strftime('%m-%d %H:%M')} ~ {en_dt.strftime('%H:%M')}`"
        else:
            return f"{day_str}`{st_dt.strftime('%m-%d %H:%M')} ~ {en_dt.strftime('%m-%d %H:%M')}`"
    except Exception:
        return f"`{start} ~ {end}`"

def get_day_and_period(start):
    try:
        st_dt = datetime.fromisoformat(start)
        now_date = datetime.now(timezone(timedelta(hours=8))).date()
        delta_days = (st_dt.date() - now_date).days
        
        if st_dt.hour == 6: period = "白天"
        elif st_dt.hour == 18: period = "晚上"
        else: period = st_dt.strftime('%H:%M')
            
        day_str = ""
        if delta_days == 0: day_str = "今天"
        elif delta_days == 1: day_str = "明天"
        elif delta_days == 2: day_str = "後天"
        else: day_str = st_dt.strftime("%m-%d")
        
        date_str = st_dt.strftime("%m/%d")
        return delta_days, f"⎯⎯⎯ {day_str} ({date_str}) ⎯⎯⎯", period
    except Exception:
        return -1, "未知", "未知"

def get_temp_icon(temp_str):
    try:
        val = float(temp_str)
        if val >= 38.0: return "🔴"
        elif val >= 36.0: return "🟠"
        elif val >= 32.0: return "🟡"
        elif val <= 6.0: return "🟣"
        elif val <= 12.0: return "🔵"
        elif val <= 16.0: return "🟢"
        return "⚪"
    except (ValueError, TypeError):
        return "⚪"

def get_uvi_icon(uvi_str):
    try:
        val = float(uvi_str)
        if val >= 11: return "🟣"
        elif val >= 8: return "🔴"
        elif val >= 6: return "🟠"
        elif val >= 3: return "🟡"
        return "🟢"
    except (ValueError, TypeError):
        return "⚪"

def get_wind_arrow(wind_dir):
    if not wind_dir: return ""
    if "東北" in wind_dir: return "↙"
    if "東南" in wind_dir: return "↖"
    if "西北" in wind_dir: return "↘"
    if "西南" in wind_dir: return "↗"
    if "東" in wind_dir: return "←"
    if "南" in wind_dir: return "↑"
    if "西" in wind_dir: return "→"
    if "北" in wind_dir: return "↓"
    return ""

async def setup(bot):
    pass