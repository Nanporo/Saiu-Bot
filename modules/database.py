import sqlite3
import json
import os
import shutil
import logging
import asyncio
import copy
from datetime import datetime

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
_CACHE_LOADED = False

ALERT_TYPES = [
    "cbs_alerts", "eew_alerts", "eq_alerts", "rain_alerts", "flood_alerts",
    "temp_alerts", "typhoon_alerts", "suspension_alerts", "aqi_alerts"
]

def get_connection():
    """取得同步 sqlite3 連線"""
    return sqlite3.connect(DB_PATH, timeout=10.0)

def _load_cache_from_db():
    """從 SQLite (優先從正規化表) 讀取所有資料並放入記憶體快取"""
    global _GUILD_SETTINGS_CACHE, _CACHE_LOADED
    conn = get_connection()
    try:
        c = conn.cursor()
        
        # 檢查新表 guilds 是否存在
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='guilds';")
        has_new_schema = c.fetchone() is not None
        
        new_cache = {}
        if has_new_schema:
            # 從正規化資料表還原完整的 dict 快取結構 (確保與所有既存 Cog 100% 相容)
            c.execute('SELECT guild_id, allow_all_users_settings, eew_authorized, auto_push, target_channel_id FROM guilds')
            guild_rows = c.fetchall()
            for gid, allow_all, eew_auth, auto_push, target_ch in guild_rows:
                g_dict = {
                    "allow_all_users_settings": bool(allow_all),
                    "eew_authorized": bool(eew_auth),
                    "auto_push": bool(auto_push),
                }
                if target_ch:
                    g_dict["target_channel_id"] = target_ch
                new_cache[str(gid)] = g_dict
            
            # 讀取各頻道告警訂閱
            c.execute('SELECT guild_id, alert_type, channel_id, locations, min_magnitude, min_intensity, threshold, extra_params, enabled FROM alert_subscriptions WHERE enabled = 1')
            sub_rows = c.fetchall()
            for gid, a_type, ch_id, locs, min_mag, min_int, thresh, extra_json, enabled in sub_rows:
                gid_str = str(gid)
                if gid_str not in new_cache:
                    new_cache[gid_str] = {}
                
                extra = {}
                if extra_json:
                    try:
                        extra = json.loads(extra_json)
                    except json.JSONDecodeError:
                        extra = {}
                
                alert_dict = copy.deepcopy(extra)
                if ch_id:
                    alert_dict["channel_id"] = ch_id
                if locs:
                    alert_dict["locations"] = locs.split(",") if "," in locs else locs
                if min_mag is not None:
                    alert_dict["min_magnitude"] = min_mag
                if min_int:
                    alert_dict["min_intensity"] = min_int
                if thresh is not None:
                    alert_dict["threshold"] = thresh
                alert_dict["enabled"] = bool(enabled)
                
                new_cache[gid_str][a_type] = alert_dict
        else:
            # 舊版硬碟相容讀取
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='guild_settings';")
            if c.fetchone() is not None:
                c.execute('SELECT guild_id, settings FROM guild_settings')
                for gid, settings_json in c.fetchall():
                    try:
                        new_cache[str(gid)] = json.loads(settings_json)
                    except json.JSONDecodeError:
                        new_cache[str(gid)] = {}

        _GUILD_SETTINGS_CACHE = new_cache
        _CACHE_LOADED = True
        logger.info(f"⚡ [資料庫快取] 已將 {len(_GUILD_SETTINGS_CACHE)} 個伺服器設定載入至記憶體快取。")
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
                
                # 轉移告警訂閱
                for a_type in ALERT_TYPES:
                    if a_type in s_dict and s_dict[a_type]:
                        val = s_dict[a_type]
                        if isinstance(val, dict):
                            ch_id = str(val.get("channel_id", "")) if val.get("channel_id") else None
                            locs = val.get("locations", val.get("custom_locations"))
                            if isinstance(locs, list):
                                locs_str = ",".join(map(str, locs))
                            elif locs:
                                locs_str = str(locs)
                            else:
                                locs_str = None

                            min_mag = val.get("min_magnitude")
                            min_int = str(val.get("min_intensity")) if val.get("min_intensity") else None
                            thresh = val.get("threshold")
                            enabled = 1 if val.get("enabled", True) else 0

                            # 收集其他參數作為 extra_params
                            known_keys = {"channel_id", "locations", "custom_locations", "min_magnitude", "min_intensity", "threshold", "enabled"}
                            extra = {k: v for k, v in val.items() if k not in known_keys}
                            extra_json = json.dumps(extra, ensure_ascii=False) if extra else None

                            c.execute('''
                                INSERT INTO alert_subscriptions
                                (guild_id, channel_id, alert_type, locations, min_magnitude, min_intensity, threshold, extra_params, enabled)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (str(gid), ch_id, a_type, locs_str, min_mag, min_int, thresh, extra_json, enabled))
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
    
    # 清除舊訂閱並重新寫入 alert_subscriptions
    c.execute('DELETE FROM alert_subscriptions WHERE guild_id = ?', (guild_id_str,))
    for a_type in ALERT_TYPES:
        if a_type in settings and settings[a_type]:
            val = settings[a_type]
            if isinstance(val, dict):
                ch_id = str(val.get("channel_id", "")) if val.get("channel_id") else None
                locs = val.get("locations", val.get("custom_locations"))
                if isinstance(locs, list):
                    locs_str = ",".join(map(str, locs))
                elif locs:
                    locs_str = str(locs)
                else:
                    locs_str = None

                min_mag = val.get("min_magnitude")
                min_int = str(val.get("min_intensity")) if val.get("min_intensity") else None
                thresh = val.get("threshold")
                enabled = 1 if val.get("enabled", True) else 0

                known_keys = {"channel_id", "locations", "custom_locations", "min_magnitude", "min_intensity", "threshold", "enabled"}
                extra = {k: v for k, v in val.items() if k not in known_keys}
                extra_json = json.dumps(extra, ensure_ascii=False) if extra else None

                c.execute('''
                    INSERT INTO alert_subscriptions
                    (guild_id, channel_id, alert_type, locations, min_magnitude, min_intensity, threshold, extra_params, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (guild_id_str, ch_id, a_type, locs_str, min_mag, min_int, thresh, extra_json, enabled))
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