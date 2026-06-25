import json
import os
import logging

logger = logging.getLogger(__name__)

CACHE_FILE = 'data/alarm_cache.json'

def load_cache():
    """載入暫存快取。若檔案不存在或損毀則回傳空字典。"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"⚠️ [快取管理] 載入快取失敗: {e}")
    return {}

def save_cache(data):
    """將各模組的暫存狀態儲存為 JSON 檔案。"""
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logger.info("💾 [快取管理] 已成功將推播暫存狀態寫入硬碟。")
    except Exception as e:
        logger.error(f"⚠️ [快取管理] 儲存快取失敗: {e}")

def backup_all_caches(bot):
    """遍歷所有已載入的 Cog，並呼叫其 save_state 方法收集暫存資料。"""
    data = load_cache()  # 先讀取現有快取，避免未載入的 Cog 資料被覆寫
    
    for cog_name, cog in bot.cogs.items():
        if hasattr(cog, 'save_state') and callable(getattr(cog, 'save_state')):
            try:
                state = cog.save_state()
                if isinstance(state, dict):
                    data.update(state)
            except Exception as e:
                logger.error(f"⚠️ [快取管理] 收集 {cog_name} 快取時發生錯誤: {e}")
                
    save_cache(data)
