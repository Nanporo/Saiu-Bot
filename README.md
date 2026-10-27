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
pip install discord.py aiohttp aiosqlite geopy pillow feedparser beautifulsoup4 python-dotenv certifi
```

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

在 `/附近飛機` 的模式選單選擇「ACARS 最近報文」，可查看接收介面狀態與最近 100 筆報文，每頁 8 筆，時間以台灣時間顯示。此頁面包含空文字的協定訊息；原文超過 300 字時標示截斷，解碼器修正資訊會保留。

機器人執行主機需能連線至 `ACARS_API_BASE_URL` 的內網位址。資料在進入 ACARS 頁面、重新整理或 AI 查詢時透過 HTTP 取得，使用既有共用 session，正常查詢共用 30 秒快取；翻頁維持同一份資料，既有訊息重新整理功能會重新取得資料。沒有 WebSocket、額外背景輪詢或自動推播，也不把最近 100 筆當成完整 24 小時紀錄。

最近報文與狀態 API 獨立處理失敗，單一資料來源不可用時仍顯示另一來源。若網站後端重新啟動，最近報文時間會同時參考歷史資料，不把重啟後計數視為每日累計。此頁不改動原有 ADS-B 飛機列表、詳細資料或地圖。

提及小裁雨時，也可直接詢問 ACARS／ADS-B 知識、解讀報文，或要求查詢實際資料。AI 的 `get_adsb_data` 工具支援即時空中飛機、最近 24 小時歷史、貨機、特殊機與總覽，並可依航班呼號、機身註冊號或 ICAO hex 查詢；`get_acars_messages` 可依航班或機身註冊號搜尋最近 100 筆 ACARS 報文。兩者預設回傳 5 筆、最多 20 筆，Groq 與 Gemini 均可使用。查詢沿用已載入的 `AdsbCog` 與既有接收站設定，結果僅涵蓋本站接收範圍。

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
│   ├── add/            # 加入指令相關模組
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
