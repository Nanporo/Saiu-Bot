import json

def load_town_mapping():
    """讀取本地地圖資料，建立鄉鎮市區全名與簡稱的對照表，並包含中心點經緯度"""
    mapping = {}
    
    def add_to_mapping(c, t, lat=None, lon=None):
        c = c.replace('台', '臺')
        t = t.replace('台', '臺')
        fullname = f"{c}{t}"
        
        c_short = c[:-1] if len(c) >= 3 and c[-1] in ['縣', '市'] else c
        t_short = t[:-1] if len(t) >= 3 and t[-1] in ['區', '鄉', '鎮', '市'] else t
        
        combinations = [t, fullname]
        if c_short != c:
            combinations.append(f"{c_short}{t}")
        if t_short != t:
            combinations.append(t_short)
            combinations.append(f"{c}{t_short}")
        if c_short != c and t_short != t:
            combinations.append(f"{c_short}{t_short}")
            
        for combo in combinations:
            if combo not in mapping:
                mapping[combo] = []
            if not any(item[0] == fullname for item in mapping[combo]):
                mapping[combo].append((fullname, lat, lon))

    # 1. 解析 locations.json
    try:
        with open('maps/locations.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            for current_county, towns in data.get("towns", {}).items():
                for town_name, town_data in towns.items():
                    if len(town_data) >= 3:
                        lat = float(town_data[1])
                        lon = float(town_data[2])
                        add_to_mapping(current_county, town_name, lat, lon)
    except Exception as e:
        print(f"載入 locations.json 失敗: {e}")
        
    # 2. 如果檔案讀不到，使用內建的易混淆清單 Fallback 保底
    if not mapping:
        mapping = {
            "信義": [("臺北市信義區", None, None), ("南投縣信義鄉", None, None)],
            "仁愛": [("基隆市仁愛區", None, None), ("南投縣仁愛鄉", None, None)],
            "中正": [("臺北市中正區", None, None), ("基隆市中正區", None, None)],
            "中山": [("臺北市中山區", None, None), ("基隆市中山區", None, None)],
            "大安": [("臺北市大安區", None, None), ("臺中市大安區", None, None)],
            "東區": [("新竹市東區", None, None), ("臺中市東區", None, None), ("臺南市東區", None, None), ("嘉義市東區", None, None)],
            "西區": [("新竹市西區", None, None), ("臺中市西區", None, None), ("嘉義市西區", None, None)],
            "南區": [("臺中市南區", None, None), ("臺南市南區", None, None)],
            "北區": [("新竹市北區", None, None), ("臺中市北區", None, None), ("臺南市北區", None, None)]
        }
        
    return mapping