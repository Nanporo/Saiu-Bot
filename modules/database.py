import sqlite3
import json
import os
import logging
import asyncio

logger = logging.getLogger(__name__)

DB_PATH = 'guild_settings.db'

def get_connection():
    return sqlite3.connect(DB_PATH, timeout=10.0)

def init_db():
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute('PRAGMA journal_mode=WAL;')
        c.execute('''
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY,
                settings TEXT
            )
        ''')
        conn.commit()
    finally:
        conn.close()

def migrate_from_json():
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
                logger.info("🔄 [資料庫] 原設定檔已重新命名為 guild_settings.json.bak")
            except Exception as e:
                logger.error(f"⚠️ [資料庫] 重新命名舊設定檔失敗: {e}")
    finally:
        conn.close()

def get_all_settings():
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute('SELECT guild_id, settings FROM guild_settings')
        rows = c.fetchall()
    finally:
        conn.close()
    
    settings = {}
    for row in rows:
        guild_id, settings_json = row
        try:
            settings[guild_id] = json.loads(settings_json)
        except json.JSONDecodeError:
            settings[guild_id] = {}
    return settings

def get_guild_settings(guild_id):
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute('SELECT settings FROM guild_settings WHERE guild_id = ?', (str(guild_id),))
        row = c.fetchone()
    finally:
        conn.close()
        
    if row and row[0]:
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return {}
    return {}

def update_guild_settings(guild_id, settings):
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO guild_settings (guild_id, settings) VALUES (?, ?)', (str(guild_id), json.dumps(settings, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()

def delete_guild_settings(guild_id):
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute('DELETE FROM guild_settings WHERE guild_id = ?', (str(guild_id),))
        conn.commit()
    finally:
        conn.close()

def save_all_settings(all_settings):
    conn = get_connection()
    try:
        c = conn.cursor()
        for guild_id, settings in all_settings.items():
            c.execute('INSERT OR REPLACE INTO guild_settings (guild_id, settings) VALUES (?, ?)', (str(guild_id), json.dumps(settings, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()

# ================= 非同步 (Async Non-blocking) 操作包裹 =================
async def async_get_all_settings():
    return await asyncio.to_thread(get_all_settings)

async def async_get_guild_settings(guild_id):
    return await asyncio.to_thread(get_guild_settings, guild_id)

async def async_update_guild_settings(guild_id, settings):
    return await asyncio.to_thread(update_guild_settings, guild_id, settings)

async def async_delete_guild_settings(guild_id):
    return await asyncio.to_thread(delete_guild_settings, guild_id)

async def async_save_all_settings(all_settings):
    return await asyncio.to_thread(save_all_settings, all_settings)