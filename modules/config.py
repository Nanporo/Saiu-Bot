import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 嘗試匯入 dotenv，若未安裝則不引發錯誤
try:
    from dotenv import load_dotenv
    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False

BASE_DIR = Path(__file__).resolve().parent.parent

class Config:
    """
    統一 Config 管理器
    支援讀取 .env 與 config.json 檔案，並兼具屬性存取與字典模式存取 (.get() / ['KEY'])
    優先順序：系統環境變數 / .env > config.json > 預設值
    """
    _instance = None

    def __init__(self):
        self._data = {}
        self._initial_loaded = False
        self.load(verbose=True)
        self._initial_loaded = True

    def load(self, verbose: bool = False):
        """載入設定檔"""
        sources = []

        # 1. 優先嘗試透過 dotenv 或手動解析 .env 檔案
        env_path = BASE_DIR / '.env'
        if env_path.exists():
            if _DOTENV_AVAILABLE:
                load_dotenv(dotenv_path=env_path, override=True)
                sources.append(".env")
            else:
                try:
                    with open(env_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith('#'):
                                continue
                            if '=' in line:
                                k, v = line.split('=', 1)
                                k_str = k.strip()
                                v_str = v.strip().strip('"\'')
                                if k_str:
                                    os.environ[k_str] = v_str
                    sources.append(".env (手動解析)")
                except Exception as e:
                    logger.error(f"⚠️ [Config] 手動讀取 .env 失敗: {e}")

        # 2. 讀取 config.json (若存在)
        json_data = {}
        json_path = BASE_DIR / 'config.json'
        if json_path.exists():
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)
                sources.append("config.json")
            except Exception as e:
                logger.error(f"⚠️ [Config] 讀取 config.json 失敗: {e}")

        if verbose or not getattr(self, '_initial_loaded', False):
            if sources:
                logger.info(f"⚙️ [Config] 設定檔來源: {', '.join(sources)}")
            else:
                logger.warning("⚠️ [Config] 未找到 .env 或 config.json 設定檔")

        # 3. 欄位定義與解析
        keys = [
            'DISCORD_TOKEN',
            'CWA_API_KEY',
            'MOENV_API_KEY',
            'ADSB_API_URL',
            'CWA_EEW_AUTH',
            'GEMINI_API_KEY',
            'SAIU_SYSTEM_INSTRUCTION',
            'OWNER_ID',
            'OWNER_SERVER_ID',
            'CONSOLE_ID',
            'CONSOLE_COMMAND_ID',
            'CONSOLE_PUSH_ID',
            'CONSOLE_WEBHOOK_URL',
            'CONSOLE_COMMAND_WEBHOOK_URL',
            'CONSOLE_PUSH_WEBHOOK_URL'
        ]

        data = {}
        for key in keys:
            # 優先檢查環境變數
            env_val = os.getenv(key)
            if env_val is not None and env_val.strip() != "":
                # 轉成 int 若該 Key 屬於 ID 類型
                if 'ID' in key:
                    try:
                        data[key] = int(env_val)
                    except ValueError:
                        data[key] = env_val
                else:
                    data[key] = env_val
            elif key in json_data:
                json_val = json_data[key]
                if 'ID' in key and json_val is not None:
                    try:
                        data[key] = int(json_val)
                    except (ValueError, TypeError):
                        data[key] = 0
                else:
                    data[key] = json_val if json_val is not None else ""
            else:
                data[key] = 0 if 'ID' in key else ""

        # 保留 json_data 中未明確列出的其餘欄位
        for k, v in json_data.items():
            if k not in data:
                data[k] = v

        self._data = data

    def get(self, key: str, default=None):
        """字典風格相容存取介面"""
        return self._data.get(key, default)

    def reload(self, verbose: bool = False):
        """重新載入設定檔"""
        self.load(verbose=verbose)
        if verbose:
            logger.info("🔄 [Config] 設定檔已成功重新載入。")

    def __getitem__(self, item: str):
        return self._data[item]

    def __contains__(self, item: str):
        return item in self._data

    def __getattr__(self, item: str):
        if item in self._data:
            return self._data[item]
        raise AttributeError(f"'Config' 物件無此屬性: '{item}'")

    def __repr__(self):
        # 隱藏 Token, 只顯示鍵名
        masked = {k: ("***" if "TOKEN" in k or "KEY" in k else v) for k, v in self._data.items()}
        return f"<Config {masked}>"

def get_config() -> Config:
    """取得 Config 單例」"""
    if Config._instance is None:
        Config._instance = Config()
    return Config._instance
