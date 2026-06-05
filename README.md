# 小裁雨 (Saiu)

如果您遇到任何問題，請聯絡機器人作者。

## 自行部署 / Self-Hosting

### 環境需求 (Prerequisites)

- `python 3.12`
- `git`

### 安裝步驟 (Installation)

**複製本專案 (Clone the repository)**
```bash
git clone https://github.com/Nanporo/Saiu-Bot.git
cd Saiu-Bot
```

**安裝所需套件 (Install dependencies)**
```bash
pip install discord.py aiohttp beautifulsoup4 geopy pillow
```

### 設定檔 (Configuration)

把 `config.json.example` 重新命名為 `config.json` 並調整

```
"DISCORD_TOKEN": Discord 機器人 Token
"OWNER_ID": 自己的 Discord 使用者 ID，用於執行擁有者限定指令
```

### 執行 (Run the bot)
```bash
python bot.py
```

## 特別感謝 / Special Thanks
IATA - ICAO 對照表來源 https://github.com/ip2location/ip2location-iata-icao

台灣行政區地圖 https://github.com/dkaoster/taiwan-atlas

本專案內含之字體採用 Google 的 [Noto Sans TC](https://fonts.google.com/noto/specimen/Noto+Sans+TC)，採用 [SIL Open Font License, Version 1.1](https://scripts.sil.org/OFL) 授權。

## 免責聲明 / Disclaimer

機器人所獲取之資料僅作為參考以及學習用途，機器人作者不負擔任何責任。

## 授權 / License

GNU Affero General Public License
