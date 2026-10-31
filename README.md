# 小裁雨 (Saiu)

如果您遇到任何問題，請聯絡機器人作者。

[功能介紹](https://nanporo.github.io/Saiu-Bot/)（包含邀請網址）

---


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
python -m pip install discord.py aiohttp aiosqlite geopy pillow feedparser beautifulsoup4 python-dotenv certifi numpy scipy
```

請在啟動機器人的同一個 Python 環境安裝套件。介面使用 `LayoutView`、`Container` 等 Components v2 元件，需要 discord.py 2.6 以上，建議 2.7.1。

FM 廣播透過 NumPy 解調音訊，並使用 SciPy 自動解析電臺發送的 RDS（電臺名稱、廣播文字、PI、PTY、TP、TA）。未安裝 SciPy 時仍可播放音訊；尚未解出有效 RDS 或資料超過 60 秒未更新時，播放訊息顯示「尚未接收到 RDS」。實際解析結果取決於電臺是否發送及收訊品質。

### 設定檔 (Configuration)

複製 `.env.example` 為 `.env`，或複製 `config.json.example` 為 `config.json`，並調整以下欄位。

```
"DISCORD_TOKEN": Discord 機器人 Token
"CWA_API_KEY": 中央氣象署 (CWA) 開放資料平台 API 授權碼
"ADSB_API_URL": ADSB 飛機追蹤的 JSON 資料來源網址 (例如 tar1090 的 aircraft.json)
"ACARS_API_BASE_URL": ACARS 內網 API 基底位址（例如 http://192.0.2.10:8080，未設定時不發出請求）
"OWNER_ID": 自己的 Discord 使用者 ID，用於執行擁有者限定指令 (如 /關機、/重啟)
"OWNER_SERVER_ID": 擁有者測試/管理用的伺服器 ID，擁有者指令將註冊在此伺服器
"CONSOLE_WEBHOOK_URL": 記錄控制台/系統通知的 Discord Webhook 網址
"CONSOLE_COMMAND_WEBHOOK_URL": 記錄機器人指令的 Discord Webhook 網址
"CONSOLE_PUSH_WEBHOOK_URL": 記錄機器人警報/推播的 Discord Webhook 網址
"MOENV_API_KEY": 環境部的 API 授權碼
```

### ACARS 最近報文

在 `/附近飛機` 的模式選單選擇「ACARS 最近報文」，可查看接收介面狀態與最近 100 筆報文，每頁 8 筆，時間以台灣時間顯示。頁面包含空文字的協定訊息；原文超過 300 字時標示截斷，解碼器修正資訊會保留。

提及小裁雨時，也可直接詢問 ACARS／ADS-B 知識、解讀報文，或要求查詢實際資料。

### 海嘯通知

使用 `/加入` 選擇「海嘯通知」，可訂閱北部、東北、東部、東南、西南、海峽沿海地區（可多選），或「全台接收」。

### 執行 (Run the bot)
```bash
python bot.py
```

## 專案架構 / Project Structure

```
Saiu-Bot/
├── bot.py              # 機器人主程式
├── .env.example # 設定檔範例
├── cogs/               # 指令模組
│   ├── add/            # 加入指令與依功能拆分的設定流程
│   ├── alarm/          # 自動提醒相關模組
│   ├── settings/       # 設定相關指令模組
│   └── weather/        # 天氣預報模組
├── data/               # 快取與暫存資料 (自動產生)
├── fonts/              # 字型檔
├── maps/               # 地圖相關資源
├── modules/            # 非指令模組
├── photos/             # 圖片資源
└── README.md
```


`cogs/add/add_command.py` 負責 `/加入` 指令與權限，`add_view.py` 負責選擇流程；表單、分區選單與後續步驟拆分至 `add_earthquake.py`、`add_town.py`、`add_rain.py`、`add_cbs.py`、`add_county.py`、`add_traffic.py`、`add_safety.py`、`add_tsunami.py`。共用的身分組與通知時段分別放在 `add_roles.py`、`add_hours.py`，`add_modals.py` 保留舊匯入路徑相容性。


## 特別感謝 / Special Thanks
`xtw.littlecat` 提供斜線指令、交通資訊顯示方面的建議

`chr800a` 提供機場輸入方面的建議

`yemmin_0` 提供停班停課資訊獲取的改進建議

IATA - ICAO 對照表來源 https://github.com/ip2location/ip2location-iata-icao

台灣行政區地圖 https://github.com/dkaoster/taiwan-atlas

地震走時計算模組 https://github.com/ExpTechTW/eq-travel-time

本專案內含之字體採用 Google 的 [Noto Sans TC](https://fonts.google.com/noto/specimen/Noto+Sans+TC)，採用 [SIL Open Font License, Version 1.1](https://scripts.sil.org/OFL) 授權。

## 免責聲明 / Disclaimer

機器人所獲取之資料僅作為參考以及學習用途，機器人作者不負擔任何責任。

## 授權 / License

GNU Affero General Public License

VDL2 於 2026-10-30 對齊 136.975 MHz 測試五分鐘仍為零筆後，已恢復使用 ACARS。測試與還原記錄見 [接收站部署記錄](deploy/vdl2/README.md)。
