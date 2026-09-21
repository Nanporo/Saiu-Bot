import sqlite3
import json
import os
import shutil
import logging
import asyncio
import copy
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# 嘗試匯入 aiosqlite，若未安裝提供保底提示
try:
    import aiosqlite
    _AIOSQLITE_AVAILABLE = True
except ImportError:
    _AIOSQLITE_AVAILABLE = False

DB_PATH = 'guild_settings.db'

# ================= 全域記憶體快取 (In-Memory Cache) =================
_GUILD_SETTINGS_CACHE = {}
_MODULE_SWITCHES_CACHE = {}
_CACHE_LOADED = False

ALERT_TYPES = [
    "cbs_alerts", "eew_alerts", "eq_alerts", "rain_alerts", "flood_alerts",
    "temp_alerts", "typhoon_alerts", "suspension_alerts", "aqi_alerts", "traffic_alerts",
    "safety_alerts"
]

def get_connection():
    """取得同步 sqlite3 連線"""
    return sqlite3.connect(DB_PATH, timeout=10.0)

def _build_sub_dict(ch_id, min_mag, min_int, thresh, extra_json):
    """從正規化表的一列資料建立告警設定字典，並修正型態 (TEXT→int)"""
    extra = {}
    if extra_json:
        try:
            extra = json.loads(extra_json)
        except json.JSONDecodeError:
            extra = {}
    alert_dict = copy.deepcopy(extra)
    if ch_id is not None:
        try:
            alert_dict["channel_id"] = int(ch_id)
        except (ValueError, TypeError):
            alert_dict["channel_id"] = ch_id
    if min_mag is not None:
        alert_dict["min_magnitude"] = min_mag
    if min_int is not None:
        try:
            alert_dict["min_intensity"] = int(min_int)
        except (ValueError, TypeError):
            alert_dict["min_intensity"] = min_int
    if thresh is not None:
        alert_dict["threshold"] = thresh
    return alert_dict

def _load_cache_from_db():
    """從 SQLite 讀取所有資料並放入記憶體快取"""
    global _GUILD_SETTINGS_CACHE, _MODULE_SWITCHES_CACHE, _CACHE_LOADED
    conn = get_connection()
    try:
        c = conn.cursor()
        new_cache = {}
        loaded = False

        # 優先從 guild_settings JSON 表讀取 (保有最完整的設定資料，包含 global_silent 等欄位)
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='guild_settings';")
        if c.fetchone() is not None:
            c.execute('SELECT guild_id, settings FROM guild_settings')
            rows = c.fetchall()
            if rows:
                for gid, settings_json in rows:
                    try:
                        new_cache[str(gid)] = json.loads(settings_json)
                    except json.JSONDecodeError:
                        new_cache[str(gid)] = {}
                loaded = True

        # 退回方案：若 JSON 表無資料，嘗試從正規化表重建
        if not loaded:
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='guilds';")
            if c.fetchone() is not None:
                c.execute('SELECT guild_id, allow_all_users_settings, eew_authorized, auto_push, target_channel_id FROM guilds')
                for gid, allow_all, eew_auth, auto_push, target_ch in c.fetchall():
                    g_dict = {
                        "allow_all_users_settings": bool(allow_all),
                        "eew_authorized": bool(eew_auth),
                        "auto_push": bool(auto_push),
                    }
                    if target_ch:
                        g_dict["target_channel_id"] = target_ch
                    new_cache[str(gid)] = g_dict

                c.execute('SELECT guild_id, alert_type, channel_id, locations, min_magnitude, min_intensity, threshold, extra_params, enabled FROM alert_subscriptions WHERE enabled = 1')
                sub_rows = c.fetchall()

                # 依 (guild_id, alert_type) 分組，正確重建多地點告警的巢狀字典結構
                grouped = {}
                for row in sub_rows:
                    key = (str(row[0]), row[1])
                    grouped.setdefault(key, []).append(row)

                for (gid_str, a_type), rows in grouped.items():
                    if gid_str not in new_cache:
                        new_cache[gid_str] = {}

                    has_location = any(row[3] for row in rows)

                    if has_location:
                        # 多地點告警：每列代表一個地點，重建為 {地點名: {設定dict}}
                        nested = {}
                        for row in rows:
                            _, _, ch_id, loc, min_mag, min_int, thresh, extra_json, _ = row
                            nested[loc or "unknown"] = _build_sub_dict(ch_id, min_mag, min_int, thresh, extra_json)
                        new_cache[gid_str][a_type] = nested
                    else:
                        row = rows[0]
                        _, _, ch_id, _, min_mag, min_int, thresh, extra_json, _ = row
                        alert_dict = _build_sub_dict(ch_id, min_mag, min_int, thresh, extra_json)

                        # 偵測舊格式：完整巢狀字典被序列化在 extra_params 中
                        is_legacy = alert_dict and any(isinstance(v, dict) for v in alert_dict.values())
                        if is_legacy:
                            new_cache[gid_str][a_type] = {k: v for k, v in alert_dict.items() if isinstance(v, dict)}
                        elif ch_id is None and not extra_json:
                            new_cache[gid_str][a_type] = True
                        else:
                            new_cache[gid_str][a_type] = alert_dict

        # 讀取自動推送模組開關設定
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='module_switches';")
        new_module_switches = {}
        if c.fetchone() is not None:
            c.execute('SELECT module_name, enabled FROM module_switches')
            for mod_name, enabled in c.fetchall():
                new_module_switches[str(mod_name)] = bool(enabled)

        _GUILD_SETTINGS_CACHE = new_cache
        _MODULE_SWITCHES_CACHE = new_module_switches
        _CACHE_LOADED = True
        logger.info(f"⚡ [資料庫快取] 已將 {len(_GUILD_SETTINGS_CACHE)} 個伺服器設定與 {len(_MODULE_SWITCHES_CACHE)} 個模組開關載入至記憶體快取。")
    finally:
        conn.close()

def _ensure_cache_loaded():
    """確保快取已初始化"""
    if not _CACHE_LOADED:
        _load_cache_from_db()

def create_schema_tables(conn):
    """建立正規化資料表與索引"""
    c = conn.cursor()
    c.execute('PRAGMA journal_mode=WAL;')
    
    # 1. 舊版相容表
    c.execute('''
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id TEXT PRIMARY KEY,
            settings TEXT
        )
    ''')
    
    # 2. 伺服器主要設定表
    c.execute('''
        CREATE TABLE IF NOT EXISTS guilds (
            guild_id TEXT PRIMARY KEY,
            allow_all_users_settings BOOLEAN DEFAULT 0,
            eew_authorized BOOLEAN DEFAULT 0,
            auto_push BOOLEAN DEFAULT 0,
            target_channel_id TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 3. 告警與預警推播訂閱表
    c.execute('''
        CREATE TABLE IF NOT EXISTS alert_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            channel_id TEXT,
            alert_type TEXT NOT NULL,
            locations TEXT,
            min_magnitude REAL,
            min_intensity TEXT,
            threshold REAL,
            extra_params TEXT,
            enabled BOOLEAN DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
        )
    ''')
    
    # 4. 高速 SQL 索引
    c.execute('CREATE INDEX IF NOT EXISTS idx_alert_lookup ON alert_subscriptions (alert_type, enabled);')
    c.execute('CREATE INDEX IF NOT EXISTS idx_guild_alerts ON alert_subscriptions (guild_id, alert_type);')

    # 5. 自動推送模組開關控制表
    c.execute('''
        CREATE TABLE IF NOT EXISTS module_switches (
            module_name TEXT PRIMARY KEY,
            enabled BOOLEAN DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 6. 平安通報主表
    c.execute('''
        CREATE TABLE IF NOT EXISTS safety_checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            closed_at TIMESTAMP,
            privacy_purged INTEGER DEFAULT 0,
            created_by TEXT,
            responses_json TEXT DEFAULT '{}'
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_safety_status ON safety_checkins (status);')
    c.execute('CREATE INDEX IF NOT EXISTS idx_safety_created ON safety_checkins (created_at);')
    c.execute('CREATE INDEX IF NOT EXISTS idx_safety_purged ON safety_checkins (privacy_purged);')

    # 動態檢查舊表是否有缺少欄位並自動補齊
    c.execute("PRAGMA table_info(safety_checkins);")
    existing_cols = {row[1] for row in c.fetchall()}
    if existing_cols:
        if "closed_at" not in existing_cols:
            try:
                c.execute("ALTER TABLE safety_checkins ADD COLUMN closed_at TIMESTAMP;")
            except Exception:
                pass
        if "privacy_purged" not in existing_cols:
            try:
                c.execute("ALTER TABLE safety_checkins ADD COLUMN privacy_purged INTEGER DEFAULT 0;")
            except Exception:
                pass

    # 7. 平安通報各伺服器推播訊息關聯表
    c.execute('''
        CREATE TABLE IF NOT EXISTS safety_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checkin_id INTEGER NOT NULL,
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (checkin_id) REFERENCES safety_checkins(id) ON DELETE CASCADE
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_safety_messages_checkin ON safety_messages (checkin_id);')
    c.execute('CREATE INDEX IF NOT EXISTS idx_safety_messages_guild ON safety_messages (guild_id);')
    conn.commit()

def check_and_migrate_schema():
    """
    自動感應與安全資料庫遷移機制：
    若偵測到舊版 JSON 資料庫，自動進行全備份，並將 JSON 解析注入正規化關聯表中。
    本函數具備等冪性（Idempotent），可重複呼叫。
    """
    if not os.path.exists(DB_PATH):
        return

    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        
        # 檢查舊表 guild_settings 是否有資料需要轉移
        c.execute("SELECT COUNT(*) FROM guild_settings")
        old_count = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM guilds")
        new_count = c.fetchone()[0]
        
        if old_count > 0 and new_count == 0:
            logger.info("🔄 [資料庫遷移] 偵測到舊版 JSON 結構 DB，準備進行自動安全備份與正規化遷移...")
            
            # 1. 建立自動時間戳記備份
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak_path = f"{DB_PATH}.bak_{timestamp}"
            try:
                shutil.copy2(DB_PATH, bak_path)
                logger.info(f"💾 [資料庫遷移] 已成功建立舊 DB 時間戳記備份: {bak_path}")
            except Exception as e:
                logger.error(f"⚠️ [資料庫遷移] 備份失敗: {e}")

            # 2. 讀取舊表 JSON 資料進行解構並轉填至新表
            c.execute("SELECT guild_id, settings FROM guild_settings")
            rows = c.fetchall()
            
            migrated_guilds = 0
            migrated_alerts = 0
            
            for gid, settings_json in rows:
                try:
                    s_dict = json.loads(settings_json)
                except Exception:
                    continue
                
                allow_all = 1 if s_dict.get("allow_all_users_settings") else 0
                eew_auth = 1 if s_dict.get("eew_authorized") else 0
                auto_push = 1 if s_dict.get("auto_push") else 0
                target_ch = s_dict.get("target_channel_id", s_dict.get("target_channel_ids"))
                if isinstance(target_ch, list) and target_ch:
                    target_ch = str(target_ch[0])
                elif target_ch:
                    target_ch = str(target_ch)
                else:
                    target_ch = None

                c.execute('''
                    INSERT OR REPLACE INTO guilds 
                    (guild_id, allow_all_users_settings, eew_authorized, auto_push, target_channel_id)
                    VALUES (?, ?, ?, ?, ?)
                ''', (str(gid), allow_all, eew_auth, auto_push, target_ch))
                migrated_guilds += 1
                
                # 轉移告警訂閱 (正確處理多地點巢狀字典)
                for a_type in ALERT_TYPES:
                    if a_type in s_dict and s_dict[a_type]:
                        val = s_dict[a_type]
                        if isinstance(val, dict):
                            # 偵測是否為多地點巢狀字典 (如 {"全台接收": {...}, "臺北市信義區": {...}})
                            is_nested = any(isinstance(v, dict) for v in val.values())
                            if is_nested:
                                for loc_name, loc_settings in val.items():
                                    if not isinstance(loc_settings, dict):
                                        continue
                                    _write_single_subscription(c, str(gid), a_type, loc_name, loc_settings)
                                    migrated_alerts += 1
                            else:
                                _write_single_subscription(c, str(gid), a_type, None, val)
                                migrated_alerts += 1
                        elif isinstance(val, bool) and val:
                            c.execute('''
                                INSERT INTO alert_subscriptions
                                (guild_id, alert_type, enabled)
                                VALUES (?, ?, 1)
                            ''', (str(gid), a_type))
                            migrated_alerts += 1

            conn.commit()
            logger.info(f"🎉 [資料庫遷移] 自動正規化遷移成功！共轉換 {migrated_guilds} 個伺服器與 {migrated_alerts} 筆告警訂閱。")
    except Exception as e:
        logger.error(f"❌ [資料庫遷移] 自動遷移失敗: {e!r}")
    finally:
        conn.close()

def init_db():
    """同步初始化資料庫"""
    conn = get_connection()
    try:
        create_schema_tables(conn)
    finally:
        conn.close()
        
    migrate_from_json()
    check_and_migrate_schema()
    _load_cache_from_db()

def migrate_from_json():
    """相容舊版 json 檔案遷移"""
    if not os.path.exists('guild_settings.json'):
        return
    try:
        with open('guild_settings.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return
    if not data:
        return

    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM guild_settings')
        if c.fetchone()[0] == 0:
            for guild_id, settings in data.items():
                c.execute('INSERT INTO guild_settings (guild_id, settings) VALUES (?, ?)', (str(guild_id), json.dumps(settings, ensure_ascii=False)))
            conn.commit()
            logger.info("🔄 [資料庫] 成功從 guild_settings.json 遷移資料至 SQLite。")
            try:
                os.rename('guild_settings.json', 'guild_settings.json.bak')
            except Exception as e:
                logger.error(f"⚠️ [資料庫] 重新命名舊設定檔失敗: {e}")
    finally:
        conn.close()

def _write_single_subscription(c, guild_id_str, a_type, loc_name, sub_dict):
    """將單一訂閱設定寫入 alert_subscriptions 表"""
    ch_id = str(sub_dict.get("channel_id", "")) if sub_dict.get("channel_id") else None
    min_mag = sub_dict.get("min_magnitude")
    min_int = str(sub_dict.get("min_intensity")) if sub_dict.get("min_intensity") is not None else None
    thresh = sub_dict.get("threshold")
    enabled = 1 if sub_dict.get("enabled", True) else 0

    known_keys = {"channel_id", "locations", "custom_locations", "min_magnitude", "min_intensity", "threshold", "enabled"}
    extra = {k: v for k, v in sub_dict.items() if k not in known_keys}
    extra_json = json.dumps(extra, ensure_ascii=False) if extra else None

    c.execute('''
        INSERT INTO alert_subscriptions
        (guild_id, channel_id, alert_type, locations, min_magnitude, min_intensity, threshold, extra_params, enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (guild_id_str, ch_id, a_type, loc_name, min_mag, min_int, thresh, extra_json, enabled))

def _write_guild_to_db(conn, guild_id_str, settings):
    """寫入資料庫輔助函式（同步雙寫至新正規化表與舊 JSON 表）"""
    c = conn.cursor()
    # 寫入舊表相容
    c.execute('INSERT OR REPLACE INTO guild_settings (guild_id, settings) VALUES (?, ?)', (guild_id_str, json.dumps(settings, ensure_ascii=False)))
    
    # 寫入正規化 guilds 表
    allow_all = 1 if settings.get("allow_all_users_settings") else 0
    eew_auth = 1 if settings.get("eew_authorized") else 0
    auto_push = 1 if settings.get("auto_push") else 0
    target_ch = settings.get("target_channel_id")
    
    c.execute('''
        INSERT OR REPLACE INTO guilds (guild_id, allow_all_users_settings, eew_authorized, auto_push, target_channel_id, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (guild_id_str, allow_all, eew_auth, auto_push, target_ch))
    
    # 清除舊訂閱並重新寫入 alert_subscriptions (正確處理多地點告警)
    c.execute('DELETE FROM alert_subscriptions WHERE guild_id = ?', (guild_id_str,))
    for a_type in ALERT_TYPES:
        if a_type in settings and settings[a_type]:
            val = settings[a_type]
            if isinstance(val, dict):
                # 偵測是否為多地點巢狀字典 (如 {"全台接收": {...}, "臺北市信義區": {...}})
                is_nested = any(isinstance(v, dict) for v in val.values())
                if is_nested:
                    for loc_name, loc_settings in val.items():
                        if not isinstance(loc_settings, dict):
                            continue
                        _write_single_subscription(c, guild_id_str, a_type, loc_name, loc_settings)
                else:
                    # 扁平 dict (單一訂閱，無地點巢狀)
                    _write_single_subscription(c, guild_id_str, a_type, None, val)
            elif isinstance(val, bool) and val:
                c.execute('''
                    INSERT INTO alert_subscriptions (guild_id, alert_type, enabled) VALUES (?, ?, 1)
                ''', (guild_id_str, a_type))

# ================= 同步 API (0ms 快取讀取 + Write-Through 同步寫入) =================
def get_all_settings():
    _ensure_cache_loaded()
    return copy.deepcopy(_GUILD_SETTINGS_CACHE)

def get_guild_settings(guild_id):
    _ensure_cache_loaded()
    guild_id_str = str(guild_id)
    if guild_id_str in _GUILD_SETTINGS_CACHE:
        return copy.deepcopy(_GUILD_SETTINGS_CACHE[guild_id_str])
    return {}

def update_guild_settings(guild_id, settings):
    _ensure_cache_loaded()
    guild_id_str = str(guild_id)
    _GUILD_SETTINGS_CACHE[guild_id_str] = copy.deepcopy(settings)
    
    conn = get_connection()
    try:
        _write_guild_to_db(conn, guild_id_str, settings)
        conn.commit()
    finally:
        conn.close()

def delete_guild_settings(guild_id):
    _ensure_cache_loaded()
    guild_id_str = str(guild_id)
    _GUILD_SETTINGS_CACHE.pop(guild_id_str, None)
    
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute('DELETE FROM guild_settings WHERE guild_id = ?', (guild_id_str,))
        c.execute('DELETE FROM guilds WHERE guild_id = ?', (guild_id_str,))
        c.execute('DELETE FROM alert_subscriptions WHERE guild_id = ?', (guild_id_str,))
        conn.commit()
    finally:
        conn.close()

def save_all_settings(all_settings):
    global _GUILD_SETTINGS_CACHE
    _ensure_cache_loaded()
    _GUILD_SETTINGS_CACHE = copy.deepcopy(all_settings)
    
    conn = get_connection()
    try:
        for guild_id, settings in all_settings.items():
            _write_guild_to_db(conn, str(guild_id), settings)
        conn.commit()
    finally:
        conn.close()

def reload_db_cache():
    _load_cache_from_db()

# ================= 自動推送模組開關 (Module Switches) 操作介面 =================
def get_all_module_switches():
    """取得所有自動推送模組的開關狀態字典 (copy)"""
    _ensure_cache_loaded()
    return copy.deepcopy(_MODULE_SWITCHES_CACHE)

def is_push_module_enabled(module_key_or_ext: str) -> bool:
    """
    檢查指定自動推送模組是否啟用。
    支援傳入模組代號 (如 alert_flood) 或 extension 名稱 (如 cogs.alarm.alert_flood)。
    預設為 True (啟用)。
    """
    _ensure_cache_loaded()
    if not module_key_or_ext:
        return True

    key = str(module_key_or_ext).strip()
    if key.startswith("cogs.alarm."):
        key = key.split(".")[-1]
    elif key.startswith("cogs."):
        key = key.split(".")[-1]

    # 若未在資料庫中明確設為 False，預設一律為 True (啟用)
    return _MODULE_SWITCHES_CACHE.get(key, True)

def set_module_switch(module_name: str, enabled: bool) -> None:
    """設定單一自動推送模組的開關狀態，並寫入資料庫"""
    _ensure_cache_loaded()
    key = str(module_name).strip()
    if key.startswith("cogs.alarm."):
        key = key.split(".")[-1]

    _MODULE_SWITCHES_CACHE[key] = bool(enabled)

    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO module_switches (module_name, enabled, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (key, 1 if enabled else 0))
        conn.commit()
    finally:
        conn.close()

def set_multiple_module_switches(switches: dict) -> None:
    """批次設定多個自動推送模組的開關狀態，並寫入資料庫"""
    _ensure_cache_loaded()
    conn = get_connection()
    try:
        c = conn.cursor()
        for mod, enabled in switches.items():
            key = str(mod).strip()
            if key.startswith("cogs.alarm."):
                key = key.split(".")[-1]
            _MODULE_SWITCHES_CACHE[key] = bool(enabled)
            c.execute('''
                INSERT OR REPLACE INTO module_switches (module_name, enabled, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (key, 1 if enabled else 0))
        conn.commit()
    finally:
        conn.close()

# ================= 原生 aiosqlite 非同步 (Async Non-blocking) 操作介面 =================

async def async_init_db():
    """原生非同步初始化資料庫、自動感知遷移與載入快取"""
    if not _AIOSQLITE_AVAILABLE:
        return await asyncio.to_thread(init_db)

    async with aiosqlite.connect(DB_PATH, timeout=10.0) as db:
        await db.execute('PRAGMA journal_mode=WAL;')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY,
                settings TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS guilds (
                guild_id TEXT PRIMARY KEY,
                allow_all_users_settings BOOLEAN DEFAULT 0,
                eew_authorized BOOLEAN DEFAULT 0,
                auto_push BOOLEAN DEFAULT 0,
                target_channel_id TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS alert_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                channel_id TEXT,
                alert_type TEXT NOT NULL,
                locations TEXT,
                min_magnitude REAL,
                min_intensity TEXT,
                threshold REAL,
                extra_params TEXT,
                enabled BOOLEAN DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_alert_lookup ON alert_subscriptions (alert_type, enabled);')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_guild_alerts ON alert_subscriptions (guild_id, alert_type);')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS module_switches (
                module_name TEXT PRIMARY KEY,
                enabled BOOLEAN DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS safety_checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                closed_at TIMESTAMP,
                privacy_purged INTEGER DEFAULT 0,
                created_by TEXT,
                responses_json TEXT DEFAULT '{}'
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_safety_status ON safety_checkins (status);')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_safety_created ON safety_checkins (created_at);')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_safety_purged ON safety_checkins (privacy_purged);')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS safety_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checkin_id INTEGER NOT NULL,
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (checkin_id) REFERENCES safety_checkins(id) ON DELETE CASCADE
            )
        ''')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_safety_messages_checkin ON safety_messages (checkin_id);')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_safety_messages_guild ON safety_messages (guild_id);')
        await db.commit()

    migrate_from_json()
    check_and_migrate_schema()
    await async_load_cache_from_db()

async def async_load_cache_from_db():
    """非同步載入快取"""
    return await asyncio.to_thread(_load_cache_from_db)

async def async_get_all_settings():
    if not _CACHE_LOADED:
        await async_load_cache_from_db()
    return copy.deepcopy(_GUILD_SETTINGS_CACHE)

async def async_get_guild_settings(guild_id):
    if not _CACHE_LOADED:
        await async_load_cache_from_db()
    guild_id_str = str(guild_id)
    if guild_id_str in _GUILD_SETTINGS_CACHE:
        return copy.deepcopy(_GUILD_SETTINGS_CACHE[guild_id_str])
    return {}

async def async_update_guild_settings(guild_id, settings):
    if not _CACHE_LOADED:
        await async_load_cache_from_db()
    guild_id_str = str(guild_id)
    _GUILD_SETTINGS_CACHE[guild_id_str] = copy.deepcopy(settings)
    return await asyncio.to_thread(update_guild_settings, guild_id, settings)

async def async_delete_guild_settings(guild_id):
    if not _CACHE_LOADED:
        await async_load_cache_from_db()
    guild_id_str = str(guild_id)
    _GUILD_SETTINGS_CACHE.pop(guild_id_str, None)
    return await asyncio.to_thread(delete_guild_settings, guild_id)

async def async_save_all_settings(all_settings):
    global _GUILD_SETTINGS_CACHE
    if not _CACHE_LOADED:
        await async_load_cache_from_db()
    _GUILD_SETTINGS_CACHE = copy.deepcopy(all_settings)
    return await asyncio.to_thread(save_all_settings, all_settings)

# ================= 平安通報資料庫操作 =================
def create_safety_checkin(title: str, description: str, expires_at, created_by: str = None) -> int:
    """建立新的平安通報事件，回傳 checkin_id"""
    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        exp_str = expires_at.isoformat() if isinstance(expires_at, datetime) else str(expires_at)
        c.execute('''
            INSERT INTO safety_checkins (title, description, status, expires_at, created_by, responses_json)
            VALUES (?, ?, 'active', ?, ?, '{}')
        ''', (title, description, exp_str, created_by))
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()

def record_safety_message(checkin_id: int, guild_id, channel_id, message_id):
    """記錄平安通報在特定伺服器發送的訊息 ID"""
    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        c.execute('''
            INSERT INTO safety_messages (checkin_id, guild_id, channel_id, message_id)
            VALUES (?, ?, ?, ?)
        ''', (checkin_id, str(guild_id), str(channel_id), str(message_id)))
        conn.commit()
    finally:
        conn.close()

def _parse_dt(val) -> datetime | None:
    """輔助解析資料庫時間欄位為 UTC timezone-aware datetime"""
    if not val:
        return None
    if isinstance(val, datetime):
        return val.astimezone(timezone.utc) if val.tzinfo else val.replace(tzinfo=timezone.utc)
    val_str = str(val).strip()
    try:
        dt = datetime.fromisoformat(val_str)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        try:
            dt = datetime.strptime(val_str, "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

def get_safety_checkin(checkin_id: int) -> dict | None:
    """取得特定 ID 的平安通報資料"""
    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        c.execute('SELECT id, title, description, status, created_at, expires_at, created_by, responses_json, closed_at, privacy_purged FROM safety_checkins WHERE id = ?', (checkin_id,))
        row = c.fetchone()
        if not row:
            return None
        try:
            responses = json.loads(row[7]) if row[7] else {}
        except Exception:
            responses = {}
        return {
            "id": row[0],
            "title": row[1],
            "description": row[2],
            "status": row[3],
            "created_at": row[4],
            "expires_at": row[5],
            "created_by": row[6],
            "responses": responses,
            "closed_at": row[8],
            "privacy_purged": bool(row[9])
        }
    finally:
        conn.close()

def get_safety_checkin_by_title(title: str) -> dict | None:
    """依標題搜尋最近一筆平安通報資料"""
    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        c.execute('SELECT id, title, description, status, created_at, expires_at, created_by, responses_json, closed_at, privacy_purged FROM safety_checkins WHERE title = ? ORDER BY id DESC LIMIT 1', (title,))
        row = c.fetchone()
        if not row:
            return None
        try:
            responses = json.loads(row[7]) if row[7] else {}
        except Exception:
            responses = {}
        return {
            "id": row[0],
            "title": row[1],
            "description": row[2],
            "status": row[3],
            "created_at": row[4],
            "expires_at": row[5],
            "created_by": row[6],
            "responses": responses,
            "closed_at": row[8],
            "privacy_purged": bool(row[9])
        }
    finally:
        conn.close()

def get_active_safety_checkins() -> list[dict]:
    """取得所有進行中 (active) 的平安通報"""
    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        c.execute('SELECT id, title, description, status, created_at, expires_at, created_by, responses_json, closed_at, privacy_purged FROM safety_checkins WHERE status = "active" ORDER BY id DESC')
        results = []
        for row in c.fetchall():
            try:
                responses = json.loads(row[7]) if row[7] else {}
            except Exception:
                responses = {}
            results.append({
                "id": row[0],
                "title": row[1],
                "description": row[2],
                "status": row[3],
                "created_at": row[4],
                "expires_at": row[5],
                "created_by": row[6],
                "responses": responses,
                "closed_at": row[8],
                "privacy_purged": bool(row[9])
            })
        return results
    finally:
        conn.close()

def get_recent_safety_checkins(days: int = 365) -> list[dict]:
    """取得最近指定天數 (預設 365 天) 內的所有平安通報"""
    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        c.execute(f"SELECT id, title, description, status, created_at, expires_at, created_by, responses_json, closed_at, privacy_purged FROM safety_checkins WHERE created_at >= datetime('now', '-{int(days)} days') ORDER BY id DESC")
        results = []
        for row in c.fetchall():
            try:
                responses = json.loads(row[7]) if row[7] else {}
            except Exception:
                responses = {}
            results.append({
                "id": row[0],
                "title": row[1],
                "description": row[2],
                "status": row[3],
                "created_at": row[4],
                "expires_at": row[5],
                "created_by": row[6],
                "responses": responses,
                "closed_at": row[8],
                "privacy_purged": bool(row[9])
            })
        return results
    finally:
        conn.close()

def update_safety_checkin_response(checkin_id: int, user_id, guild_id, status: str, info: dict = None) -> dict:
    """更新使用者在特定平安通報中的回報狀態，回傳更新後的完整 responses 字典"""
    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        c.execute('SELECT responses_json FROM safety_checkins WHERE id = ?', (checkin_id,))
        row = c.fetchone()
        if not row:
            return {}
        try:
            responses = json.loads(row[0]) if row[0] else {}
        except Exception:
            responses = {}
        
        responses[str(user_id)] = {
            "guild_id": str(guild_id),
            "status": status,  # "safe", "affected", "help"
            "info": info or {},
            "timestamp": datetime.now().isoformat()
        }
        
        c.execute('UPDATE safety_checkins SET responses_json = ? WHERE id = ?', (json.dumps(responses, ensure_ascii=False), checkin_id))
        conn.commit()
        return responses
    finally:
        conn.close()

def close_safety_checkin(checkin_id: int) -> bool:
    """結束指定 ID 的平安通報，並記錄結束時間戳記"""
    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        now_str = datetime.now(timezone.utc).isoformat()
        c.execute('UPDATE safety_checkins SET status = "closed", closed_at = COALESCE(closed_at, ?) WHERE id = ?', (now_str, checkin_id))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()

def close_safety_checkin_by_title(title: str) -> bool:
    """依標題結束進行中的平安通報，並記錄結束時間戳記"""
    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        now_str = datetime.now(timezone.utc).isoformat()
        c.execute('UPDATE safety_checkins SET status = "closed", closed_at = COALESCE(closed_at, ?) WHERE title = ? AND status = "active"', (now_str, title))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()

def purge_expired_safety_privacy(days: int = 7) -> int:
    """
    在事件結束指定天數（預設 7 天）後，移除「地點 (location)」、「聯絡方式 (contact)」記錄，
    只保留「狀況 (situation)」資料以確保隱私。
    回傳成功完成隱私清理的通報數量。
    """
    now = datetime.now(timezone.utc)
    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        c.execute('''
            SELECT id, status, expires_at, closed_at, responses_json 
            FROM safety_checkins 
            WHERE privacy_purged = 0
        ''')
        rows = c.fetchall()
        purged_count = 0
        
        for cid, status, exp_str, closed_str, resp_json in rows:
            end_dt = _parse_dt(closed_str) or _parse_dt(exp_str)
            if not end_dt:
                continue
                
            # 必須在事件結束滿 days 天後
            if now < end_dt + timedelta(days=days):
                continue
                
            try:
                responses = json.loads(resp_json) if resp_json else {}
            except Exception:
                responses = {}
                
            for uid, user_data in responses.items():
                if not isinstance(user_data, dict):
                    continue
                info = user_data.get("info")
                if isinstance(info, dict):
                    new_info = {}
                    if "situation" in info and info["situation"]:
                        new_info["situation"] = info["situation"]
                    elif "situation" in info:
                        new_info["situation"] = "需要協助"
                    user_data["info"] = new_info
                user_data.pop("location", None)
                user_data.pop("contact", None)

            new_resp_json = json.dumps(responses, ensure_ascii=False)
            
            if status != "closed":
                c.execute('''
                    UPDATE safety_checkins 
                    SET responses_json = ?, privacy_purged = 1, status = 'closed', closed_at = COALESCE(closed_at, ?)
                    WHERE id = ?
                ''', (new_resp_json, end_dt.isoformat(), cid))
            else:
                c.execute('''
                    UPDATE safety_checkins 
                    SET responses_json = ?, privacy_purged = 1 
                    WHERE id = ?
                ''', (new_resp_json, cid))
                
            purged_count += 1
            
        conn.commit()
        return purged_count
    except Exception as e:
        logger.error(f"❌ 執行平安通報隱私資料清理失敗: {e}")
        return 0
    finally:
        conn.close()

def get_safety_messages(checkin_id: int) -> list[dict]:
    """取得指定平安通報推播到各伺服器的訊息資訊"""
    conn = get_connection()
    try:
        c = conn.cursor()
        create_schema_tables(conn)
        c.execute('SELECT guild_id, channel_id, message_id FROM safety_messages WHERE checkin_id = ?', (checkin_id,))
        return [{"guild_id": int(r[0]), "channel_id": int(r[1]), "message_id": int(r[2])} for r in c.fetchall()]
    finally:
        conn.close()