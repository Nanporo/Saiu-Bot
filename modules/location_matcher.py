from modules.town_mapping import load_town_mapping

# 針對只輸入縣市，自動預設為市政府/縣政府所在地的鄉鎮市區
DEFAULT_TOWN_MAPPING = {
    "基隆市": "基隆市中正區", "基隆": "基隆市中正區",
    "臺北市": "臺北市信義區", "臺北": "臺北市信義區", "台北市": "臺北市信義區", "台北": "臺北市信義區",
    "新北市": "新北市板橋區", "新北": "新北市板橋區",
    "桃園市": "桃園市桃園區", "桃園": "桃園市桃園區",
    "新竹縣": "新竹縣竹北市",
    "新竹市": "新竹市北區", "新竹": "新竹市北區",
    "苗栗縣": "苗栗縣苗栗市", "苗栗": "苗栗縣苗栗市",
    "臺中市": "臺中市西屯區", "臺中": "臺中市西屯區", "台中市": "臺中市西屯區", "台中": "臺中市西屯區",
    "彰化縣": "彰化縣彰化市", "彰化": "彰化縣彰化市",
    "南投縣": "南投縣南投市", "南投": "南投縣南投市",
    "雲林縣": "雲林縣斗六市", "雲林": "雲林縣斗六市",
    "嘉義縣": "嘉義縣太保市",
    "嘉義市": "嘉義市東區", "嘉義": "嘉義市東區",
    "臺南市": "臺南市安平區", "臺南": "臺南市安平區", "台南市": "臺南市安平區", "台南": "臺南市安平區",
    "高雄市": "高雄市苓雅區", "高雄": "高雄市苓雅區",
    "屏東縣": "屏東縣屏東市", "屏東": "屏東縣屏東市",
    "宜蘭縣": "宜蘭縣宜蘭市", "宜蘭": "宜蘭縣宜蘭市",
    "花蓮縣": "花蓮縣花蓮市", "花蓮": "花蓮縣花蓮市",
    "臺東縣": "臺東縣臺東市", "臺東": "臺東縣臺東市", "台東市": "臺東縣臺東市", "台東": "臺東縣臺東市",
    "澎湖縣": "澎湖縣馬公市", "澎湖": "澎湖縣馬公市",
    "金門縣": "金門縣金城鎮", "金門": "金門縣金城鎮",
    "連江縣": "連江縣南竿鄉", "連江": "連江縣南竿鄉", "馬祖": "連江縣南竿鄉"
}

# 建立地名快取字典
town_mapping_cache = load_town_mapping()

def match_location(location: str):
    """
    比對並補全地名，返回 (配對成功後的全名, 錯誤訊息)
    若成功，第二個參數為 None
    若失敗，第一個參數為 None，第二個參數為錯誤訊息
    """
    loc_val = location.replace("台", "臺").strip()
    
    if loc_val in DEFAULT_TOWN_MAPPING:
        loc_val = DEFAULT_TOWN_MAPPING[loc_val]
        
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

DEFAULT_AUTOCOMPLETE_TOWNS = [
    "基隆市中正區", "臺北市信義區", "新北市板橋區", "桃園市桃園區", "新竹市北區", "新竹縣竹北市", 
    "苗栗縣苗栗市", "臺中市西屯區", "彰化縣彰化市", "南投縣南投市", "雲林縣斗六市", "嘉義市東區",
    "嘉義縣太保市", "臺南市安平區", "高雄市苓雅區", "屏東縣屏東市", 
    "宜蘭縣宜蘭市", "花蓮縣花蓮市", "臺東縣臺東市", 
    "澎湖縣馬公市", "金門縣金城鎮", "連江縣南竿鄉"
]

def get_town_autocomplete(current: str) -> list[str]:
    if not current.strip():
        return DEFAULT_AUTOCOMPLETE_TOWNS
        
    query = current.replace("台", "臺").strip()
    matched = set()
    for key, items in town_mapping_cache.items():
        if query in key:
            for item in items:
                matched.add(item[0])
                
    return sorted(list(matched))[:25]
