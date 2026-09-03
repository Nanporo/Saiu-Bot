"""
自動推送模組管理模組 (Push Alert Module Manager)
定義所有自動推送模組的鍵值、名稱、完整擴充套件路徑、圖標與說明。
"""

from typing import Dict, List, Optional

PUSH_MODULES: List[Dict[str, str]] = [
    {
        "key": "alert_cbs",
        "name": "災防告警",
        "extension": "cogs.alarm.alert_cbs",
        "cog_name": "CBSAlertCog",
        "emoji": "⚠️"
    },
    {
        "key": "alert_eew",
        "name": "強震即時警報",
        "extension": "cogs.alarm.alert_eew",
        "cog_name": "EEWAlertCog",
        "emoji": "🚨"
    },
    {
        "key": "alert_rain",
        "name": "降雨預警",
        "extension": "cogs.alarm.alert_rain",
        "cog_name": "RainForecastCog",
        "emoji": "🌧️"
    },
    {
        "key": "alert_flood",
        "name": "淹水預警",
        "extension": "cogs.alarm.alert_flood",
        "cog_name": "FloodForecastCog",
        "emoji": "💧"
    },
    {
        "key": "alert_temp",
        "name": "氣溫預警",
        "extension": "cogs.alarm.alert_temp",
        "cog_name": "TempAlertCog",
        "emoji": "🌡️"
    },
    {
        "key": "alert_eq",
        "name": "地震通知",
        "extension": "cogs.alarm.alert_eq",
        "cog_name": "EarthquakeAlertCog",
        "emoji": "🏚️"
    },
    {
        "key": "alert_typhoon",
        "name": "颱風侵襲機率",
        "extension": "cogs.alarm.alert_typhoon",
        "cog_name": "TyphoonAlarmCog",
        "emoji": "🌀"
    },
    {
        "key": "alert_suspension",
        "name": "停班停課通知",
        "extension": "cogs.alarm.alert_suspension",
        "cog_name": "SuspensionAlertCog",
        "emoji": "🎒"
    },
    {
        "key": "alert_aqi",
        "name": "空氣品質預警",
        "extension": "cogs.alarm.alert_aqi",
        "cog_name": "AqiAlertCog",
        "emoji": "😷"
    },
    {
        "key": "alert_traffic",
        "name": "交通狀況通知",
        "extension": "cogs.alarm.alert_traffic",
        "cog_name": "TrafficAlertCog",
        "emoji": "🚄"
    },
]

# 依 key 建立索引
PUSH_MODULE_DICT: Dict[str, Dict[str, str]] = {m["key"]: m for m in PUSH_MODULES}

# 依 extension 建立索引
EXTENSION_TO_MODULE: Dict[str, Dict[str, str]] = {m["extension"]: m for m in PUSH_MODULES}

# 伺服器設定與加入指令的類別代碼對照表
CATEGORY_TO_MODULE_KEY: Dict[str, str] = {
    "cbs": "alert_cbs",
    "eew": "alert_eew",
    "rain": "alert_rain",
    "flood": "alert_flood",
    "temp": "alert_temp",
    "eq": "alert_eq",
    "earthquake": "alert_eq",
    "typhoon": "alert_typhoon",
    "suspension": "alert_suspension",
    "aqi": "alert_aqi",
    "traffic": "alert_traffic"
}

def is_push_module_extension(extension_name: str) -> bool:
    """檢查指定 extension 名稱是否為自動推送模組"""
    return extension_name in EXTENSION_TO_MODULE

def get_module_by_extension(extension_name: str) -> Optional[Dict[str, str]]:
    """根據 extension 名稱取得模組設定"""
    return EXTENSION_TO_MODULE.get(extension_name)

def get_module_key_by_category(category: str) -> Optional[str]:
    """根據設定類別代號取得對應的模組 key"""
    return CATEGORY_TO_MODULE_KEY.get(category)
