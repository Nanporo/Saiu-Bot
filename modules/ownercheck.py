import json

def get_owner_id():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config.get('OWNER_ID')
    except Exception as e:
        print(f"⚠️ 讀取 OWNER_ID 時發生錯誤: {e}")
        return None

OWNER_ID = get_owner_id()

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID