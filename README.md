# NOA

如果您遇到任何問題，請聯絡機器人作者。

## 自行部署 / Self-Hosting

### 環境需求 (Prerequisites)

- `python 3.12`
- `git`

### 安裝步驟 (Installation)

**複製本專案 (Clone the repository)**
```bash
git clone https://github.com/Nanporo/Saiu-Bot.git
cd NOA-Bot
```

**安裝所需套件 (Install dependencies)**
```bash
pip install discord.py geopy
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

## 免責聲明 / Disclaimer

機器人所獲取之資料僅作為參考以及學習用途，機器人作者不負擔任何責任。

## 授權 / License

GNU Affero General Public License
