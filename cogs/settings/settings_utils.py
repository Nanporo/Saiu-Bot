from modules.database import get_all_settings, save_all_settings

def load_settings():
    """載入設定檔，直接讀取新版 SQLite 資料庫"""
    return get_all_settings()

def save_settings(data):
    """保存設定檔，直接寫入新版 SQLite 資料庫"""
    save_all_settings(data)

async def setup(bot):
    pass