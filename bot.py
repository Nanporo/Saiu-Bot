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

# ================= 設定 Logging =================
logger = logging.getLogger('bot')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.handlers.TimedRotatingFileHandler('bot.log', when='midnight', interval=1, backupCount=7, encoding='utf-8'),
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
        # 必須開啟這項權限，傳統指令 (如 *push) 才能運作
        intents.message_content = True 
        
        # 將傳統指令前綴設定為 *xq
        super().__init__(command_prefix='*xq', intents=intents)
        self.session = None
        self.synced_guilds = False

    async def setup_hook(self):       
        self.session = aiohttp.ClientSession()
        
        init_db()
        migrate_from_json()

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
                
                try:
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    logger.info(f"🔄 [指令] 斜線指令已同步至伺服器：{guild.name} ({guild.id})")
                except Exception as e:
                    logger.warning(f"⚠️ [警告] 同步至伺服器 {guild.name} 失敗: {e}")

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
        if self.session:
            await self.session.close()
        await super().close()

# 原神，啟動！
bot = MyBot()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)