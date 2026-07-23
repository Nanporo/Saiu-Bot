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
from datetime import datetime, timezone, timedelta
from modules.database import init_db, async_init_db, migrate_from_json, get_all_settings, get_guild_settings, update_guild_settings, delete_guild_settings
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

from modules.config import get_config

# ================= 讀取設定檔 =================
try:
    config = get_config()
    DISCORD_TOKEN = config.DISCORD_TOKEN
    OWNER_SERVER_ID = config.OWNER_SERVER_ID
    if not DISCORD_TOKEN:
        logger.critical("❌ 錯誤：未找到 DISCORD_TOKEN！請確保在 .env 或 config.json 中有設定。")
        sys.exit(1)
except Exception as e:
    logger.critical(f"❌ 讀取設定檔發生錯誤：{e}")
    sys.exit(1)
# ============================================

def _is_push_enabled(s: dict) -> bool:
    if not isinstance(s, dict):
        return False
    if s.get("target_channel_ids"):
        return True
    if s.get("auto_push"):
        return True
    alert_keys = [
        "rain_alerts", "temp_alerts", "eq_alerts", "typhoon_alerts",
        "suspension_alerts", "cbs_alerts", "aqi_alerts", "eew_alerts", "flood_alerts"
    ]
    for key in alert_keys:
        if s.get(key):
            return True
    return False


class MyBot(commands.Bot):
    def __init__(self):
        # 宣告 Intents
        intents = discord.Intents.default()
        
        super().__init__(command_prefix='!', intents=intents)
        self.config = get_config()
        self.session = None
        self.synced_guilds = False
        self.abnormal_startup = False
        self.startup_time = None

    def is_abnormal_grace_period(self):
        if self.abnormal_startup and self.startup_time:
            if datetime.now() - self.startup_time < timedelta(minutes=1):
                return True
        return False

    async def setup_hook(self):       
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        self.session = aiohttp.ClientSession(connector=connector)
        
        await async_init_db()

        # ================= 檢查關機標記 =================
        flag_path = "data/clean_shutdown.flag"
        cache_path = "data/alarm_cache.json"
        
        self.startup_time = datetime.now()
        if os.path.exists(flag_path):
            logger.info("✅ 偵測到上次為正常關閉。")
            self.abnormal_startup = False
            try:
                os.remove(flag_path)
            except Exception as e:
                logger.error(f"⚠️ 刪除正常關閉標記失敗: {e}")
        else:
            logger.warning("⚠️ 偵測到上次為異常關閉 (或首次啟動)，已進入 1 分鐘異常啟動寬限期，期間內將只紀錄不推播。")
            self.abnormal_startup = True
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

            for guild in self.guilds:
                guild_id_str = str(guild.id)
                if guild_id_str not in guild_settings:
                    update_guild_settings(guild_id_str, {})
                
                # 清除非擁有者伺服器先前留下的舊 Guild 層級指令（解決舊 copy_global_to 造成的重複顯示）
                if OWNER_SERVER_ID and guild.id != OWNER_SERVER_ID:
                    try:
                        self.tree.clear_commands(guild=guild)
                        await self.tree.sync(guild=guild)
                    except Exception:
                        pass

            total_guilds = len(self.guilds)
            push_guilds_count = sum(
                1 for g in self.guilds 
                if _is_push_enabled(guild_settings.get(str(g.id)))
            )
            logger.info(f"ℹ️ [指令] 已加入 {total_guilds} 個群組（共 {push_guilds_count} 個群組開啟自動推播）")

            # 全域斜線指令：標準單次全域同步
            try:
                synced_global = await self.tree.sync()
                logger.info(f"🔄 [指令] 全域斜線指令單次同步完成 (共 {len(synced_global)} 個全域指令)")
            except Exception as e:
                logger.error(f"❌ [指令] 全域斜線指令同步失敗: {e}")

            # 擁有者伺服器專屬指令同步 (不使用 copy_global_to 避免出現重複預覽)
            if OWNER_SERVER_ID:
                try:
                    owner_guild = discord.Object(id=OWNER_SERVER_ID)
                    synced_owner = await self.tree.sync(guild=owner_guild)
                    logger.info(f"🔄 [指令] 擁有者伺服器 (ID: {OWNER_SERVER_ID}) 專屬指令同步完成 (共 {len(synced_owner)} 個專屬指令)")
                except Exception as e:
                    logger.error(f"❌ [指令] 擁有者伺服器專屬指令同步失敗: {e}")

    async def on_guild_join(self, guild):
        guild_id_str = str(guild.id)
        settings = get_guild_settings(guild_id_str)
        if not settings:
            update_guild_settings(guild_id_str, {})
            logger.info(f"🆕 [群組] 機器人加入了新伺服器：{guild.name} ({guild.id})，已記錄至設定檔。")

        # 全域指令會自動在所有伺服器生效，無需 copy_global_to
        # 若加入的是擁有者伺服器，則同步擁有者專屬指令
        if OWNER_SERVER_ID and guild.id == OWNER_SERVER_ID:
            try:
                owner_guild = discord.Object(id=OWNER_SERVER_ID)
                await self.tree.sync(guild=owner_guild)
                logger.info(f"🔄 [指令] 專屬指令已同步至新加入的擁有者伺服器：{guild.name} ({guild.id})")
            except Exception as e:
                logger.warning(f"⚠️ [警告] 同步專屬指令至擁有者伺服器 {guild.name} 失敗: {e}")

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

        if self.session and not self.session.closed:
            await self.session.close()
        try:
            from modules.http_client import close_shared_session
            await close_shared_session()
        except Exception as e:
            logger.error(f"❌ 關閉共享 HTTP Session 失敗: {e}")
        await super().close()

# 原神，啟動！
bot = MyBot()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)