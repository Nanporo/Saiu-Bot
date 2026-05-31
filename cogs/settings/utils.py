import json
import os

SETTINGS_FILE = 'guild_settings.json'

def load_settings():
    """讀取伺服器設定檔"""
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(data):
    """寫入伺服器設定檔"""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

async def setup(bot):
    pass