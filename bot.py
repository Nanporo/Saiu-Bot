import ssl
import certifi

# ================= 修正 Windows SSL 憑證載入錯誤 =================
orig_load_default_certs = ssl.SSLContext.load_default_certs
def load_default_certs_patched(self, purpose=ssl.Purpose.SERVER_AUTH):
    try:
        orig_load_default_certs(self, purpose)
    except ssl.SSLError as e:
        if '[ASN1: NOT_ENOUGH_DATA]' in str(e):
            print("Ignored ASN1 NOT_ENOUGH_DATA SSL error, using certifi instead.")
            self.load_verify_locations(certifi.where())
        else:
            raise
ssl.SSLContext.load_default_certs = load_default_certs_patched
# ==============================================================

import discord
from discord.ext import commands
import json
import sys
import os
import aiohttp
from datetime import timezone, timedelta
from modules.database import init_db, migrate_from_json, get_all_settings, get_guild_settings, update_guild_settings, delete_guild_settings
import logging
import logging.handlers

# 確保 log 資料夾存在
if not os.path.exists('log'):
    os.makedirs('log')

# ================= 設定 Logging =================
logger = logging.getLogger('bot')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.handlers.TimedRotatingFileHandler(os.path.join('log', 'bot.log'), when='midnight', interval=1, backupCount=7, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
# 抑制 discord.py 內部產生過多的 INFO 訊息
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)
# ============================================

# ================= 讀取設定檔 =================
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    DISCORD_TOKEN = config['DISCORD_TOKEN']
    
except FileNotFoundError:
    logger.critical("❌ 錯誤：找不到 config.json 檔案！請確保它與 bot.py 放在同一個資料夾。")
    sys.exit()
except KeyError as e:
    logger.critical(f"❌ 錯誤：config.json 缺少必要設定值 {e}！")
    sys.exit()
except Exception as e:
    logger.critical(f"❌ 讀取 config.json 發生未知錯誤：{e}")
    sys.exit()
# ============================================

class MyBot(commands.Bot):
    def __init__(self):
        # 宣告 Intents
        intents = discord.Intents.default()
        
        super().__init__(command_prefix='!', intents=intents)
        self.session = None
        self.synced_guilds = False

    async def setup_hook(self):       
        connector = aiohttp.TCPConnector(ssl=False)
        self.session = aiohttp.ClientSession(connector=connector)
        
        init_db()
        migrate_from_json()

        # ================= 檢查關機標記 =================
        flag_path = "data/clean_shutdown.flag"
        cache_path = "data/alarm_cache.json"
        if os.path.exists(flag_path):
            logger.info("✅ 偵測到上次為正常關閉。")
            try:
                os.remove(flag_path)
            except Exception as e:
                logger.error(f"⚠️ 刪除正常關閉標記失敗: {e}")
        else:
            logger.warning("⚠️ 偵測到上次為異常關閉 (或首次啟動)，將清除舊有快取資料以避免錯誤推播。")
            if os.path.exists(cache_path):
                try:
                    os.remove(cache_path)
                    logger.info("🗑️ 已清除異常關閉遺留的 alarm_cache.json。")
                except Exception as e:
                    logger.error(f"⚠️ 清除快取資料失敗: {e}")
        # ===============================================

        # ================= 清除全域指令避免重複 =================
        #print("🔄 [指令] 準備清除全域指令，避免與伺服器專屬指令重複顯示...")
        #try:
        #    self.tree.clear_commands(guild=None)
        #    await self.tree.sync()
        #    print("🔄 [指令] 舊有全域指令已清除完成。")
        #except Exception as e:
        #    print(f"❌ 全域指令清除發生錯誤: {e}")
        # ========================================================

        # ================= 載入所有模組 (Cogs) =================
        # 自動載入 cogs/ 資料夾下的所有 .py 檔案 (包含子資料夾)
        for root, dirs, files in os.walk('./cogs'):
            # 排除隱藏資料夾與特殊資料夾 (如 __pycache__)
            dirs[:] = [d for d in dirs if not d.startswith(('.', '_'))]
            for filename in files:
                if filename.endswith('.py') and not filename.startswith(('_', '.')):
                    rel_path = os.path.relpath(root, '.')
                    module_dir = rel_path.replace(os.sep, '.')
                    extension_name = f'{module_dir}.{filename[:-3]}'
                    try:
                        await self.load_extension(extension_name)
                        logger.info(f"🔄 [模組] {extension_name} 載入完成")
                    except Exception as e:
                        logger.error(f"❌ 載入模組 {extension_name} 時發生錯誤: {e}")
        # ========================================================

    async def on_ready(self):
        logger.info('====================================')
        logger.info(f'✅ 機器人已成功登入為: {self.user.name} (ID: {self.user.id})')
        logger.info(f'✅ 目前時間: {discord.utils.utcnow().astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")}')
        logger.info('====================================')
        
        if not self.synced_guilds:
            self.synced_guilds = True
            guild_settings = get_all_settings()

            success_count = 0
            fail_count = 0
            for guild in self.guilds:
                guild_id_str = str(guild.id)
                if guild_id_str not in guild_settings:
                    update_guild_settings(guild_id_str, {})
                
                try:
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    success_count += 1
                except Exception as e:
                    logger.warning(f"⚠️ [警告] 同步至伺服器 {guild.name} 失敗: {e}")
                    fail_count += 1
            
            logger.info(f"🔄 [指令] 斜線指令同步完畢 (成功: {success_count} 個伺服器, 失敗: {fail_count} 個伺服器)")

    async def on_guild_join(self, guild):
        guild_id_str = str(guild.id)
        settings = get_guild_settings(guild_id_str)
        if not settings:
            update_guild_settings(guild_id_str, {})
            logger.info(f"🆕 [群組] 機器人加入了新伺服器：{guild.name} ({guild.id})，已記錄至設定檔。")

        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"🔄 [指令] 斜線指令已同步至新伺服器：{guild.name} ({guild.id})")
        except Exception as e:
            logger.warning(f"⚠️ [警告] 同步至新伺服器 {guild.name} 失敗: {e}")

    async def on_guild_remove(self, guild):
        logger.info(f"🚪 [群組] 機器人離開或被踢出了伺服器：{guild.name} ({guild.id})")
        guild_id_str = str(guild.id)
        delete_guild_settings(guild_id_str)
        logger.info(f"🗑️ [系統] 已將伺服器 {guild.name} ({guild.id}) 的相關設定從記錄中清理。")

    async def close(self):
        from modules.cache_manager import backup_all_caches
        
        logger.info("🛑 機器人準備關閉，正在進行快取備份與收尾工作...")
        try:
            backup_all_caches(self)
        except Exception as e:
            logger.error(f"❌ 關閉時備份快取失敗: {e}")
            
        try:
            os.makedirs("data", exist_ok=True)
            with open("data/clean_shutdown.flag", "w", encoding="utf-8") as f:
                f.write("clean")
            logger.info("✅ 已寫入正常關閉標記。")
        except Exception as e:
            logger.error(f"❌ 寫入正常關閉標記失敗: {e}")

        if self.session:
            await self.session.close()
        await super().close()

# 原神，啟動！
bot = MyBot()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)