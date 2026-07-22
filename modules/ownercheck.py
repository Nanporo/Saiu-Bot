import json
import logging
from typing import Union, List

logger = logging.getLogger(__name__)

def get_owner_ids() -> List[int]:
    """
    動態讀取 config.json 中的 OWNER_ID。
    支援單一 ID (int / str) 或 ID 列表 ([int, str])，並統一轉為 int 格式。
    """
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        raw_owner = config.get('OWNER_ID')
        if raw_owner is None:
            return []
        
        if isinstance(raw_owner, list):
            owner_ids = []
            for oid in raw_owner:
                try:
                    owner_ids.append(int(oid))
                except (ValueError, TypeError):
                    pass
            return owner_ids
        else:
            try:
                return [int(raw_owner)]
            except (ValueError, TypeError):
                return []
    except Exception as e:
        logger.warning(f"⚠️ 讀取 OWNER_ID 時發生錯誤: {e}")
        return []

def get_owner_id() -> Union[int, None]:
    """取得主要擁有者的 int ID"""
    ids = get_owner_ids()
    return ids[0] if ids else None

def is_owner(user_id: Union[int, str]) -> bool:
    """
    檢查指定 user_id 是否為機器人擁有者。
    具備強類型轉換 (str / int 自動相容)，並於調用時動態讀取最新設定。
    """
    if user_id is None:
        return False
    try:
        target_id = int(user_id)
    except (ValueError, TypeError):
        return False
        
    owner_ids = get_owner_ids()
    return target_id in owner_ids

# 相容性全域變數
OWNER_ID = get_owner_id()