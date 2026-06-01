from modules.town_mapping import load_town_mapping

# 建立地名快取字典
town_mapping_cache = load_town_mapping()

def match_location(location: str):
    """
    比對並補全地名，返回 (配對成功後的全名, 錯誤訊息)
    若成功，第二個參數為 None
    若失敗，第一個參數為 None，第二個參數為錯誤訊息
    """
    loc_val = location.replace("台", "臺").strip()
    
    if loc_val in town_mapping_cache:
        matches = town_mapping_cache[loc_val]
        if len(matches) == 1:
            return matches[0][0], None
        else:
            options = "、".join([m[0] for m in matches])
            return None, f"❌ 「{loc_val}」有符合多個地點 ({options})，請提供更完整的名稱。"
            
    if "縣" not in loc_val and "市" not in loc_val:
        return None, "❌ 找不到該地點，請提供包含「縣市」與「鄉鎮市區」的完整名稱（例如：臺北市信義區）。"
        
    return loc_val, None