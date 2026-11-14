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

FM 廣播透過 NumPy 解調音訊，並使用 SciPy 自動解析電臺發送的 RDS（電臺名稱、廣播文字、PI、PTY、TP、TA）。未安裝 SciPy 時仍可播放音訊。尚未解出有效 RDS 或資料超過 60 秒未更新時，播放訊息顯示「尚未解出有效 RDS」；如果收到 CRC 正確但 PI 不可用的資料（例如 `0000`），則會明確顯示識別碼不可用，不把零值當作有效電臺資訊。電臺識別須在 5 秒內取得兩組一致的有效 PI 才會顯示。RDS 另以固定 50 ms 區塊緩衝最多 8 秒，避免零碎 TCP 封包造成頻繁重新同步。實際解析結果取決於電臺是否發送及收訊品質。

### 設定檔 (Configuration)

複製 `.env.example` 為 `.env`，或複製 `config.json.example` 為 `config.json`，並調整以下欄位。

```
"DISCORD_TOKEN": Discord 機器人 Token
"CWA_API_KEY": 中央氣象署 (CWA) 開放資料平台 API 授權碼
"ADSB_API_URL": ADSB 飛機追蹤的 JSON 資料來源網址 (例如 tar1090 的 aircraft.json)
"OWNER_ID": 自己的 Discord 使用者 ID，用於執行擁有者限定指令 (如 /關機、/重啟)
"OWNER_SERVER_ID": 擁有者測試/管理用的伺服器 ID，擁有者指令將註冊在此伺服器
"CONSOLE_WEBHOOK_URL": 記錄控制台/系統通知的 Discord Webhook 網址
"CONSOLE_COMMAND_WEBHOOK_URL": 記錄機器人指令的 Discord Webhook 網址
"CONSOLE_PUSH_WEBHOOK_URL": 記錄機器人警報/推播的 Discord Webhook 網址
"MOENV_API_KEY": 環境部的 API 授權碼
```

### FM 廣播接收站

設定 `RTL_TCP_HOST=192.168.68.130` 與 `RTL_TCP_PORT=1234`。接收站以 `rtl_tcp` 提供 IQ 串流；`cogs/fm_radio.py` 控制頻率及 2.4 MHz 取樣率，在機器人端解調 FM、輸出 48 kHz 音訊並解析 RDS。

針對 Wi-Fi 接收站，`FM_RADIO_BUFFER_MS` 預設為 `3000`（3 秒，可設 200～10000 ms）。音訊不足時會輸出靜音並自動增加重新緩衝時間，最多 10 秒；播放端不等待網路資料，避免延遲後集中追趕播放。接收緩衝最多保留 15 秒音訊以吸收突發資料，換臺或重新連線時清除舊音訊。修改設定後需重新啟動機器人。

接收站服務設定與驗證方式見 [RTL-TCP 部署說明](deploy/rtl-tcp/README.md)。

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
