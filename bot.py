import discord
from discord.ext import commands
import json
import sys
import os
import aiohttp
from datetime import timezone, timedelta

# ================= 讀取設定檔 =================
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    DISCORD_TOKEN = config['DISCORD_TOKEN']
    
except FileNotFoundError:
    print("❌ 錯誤：找不到 config.json 檔案！請確保它與 bot.py 放在同一個資料夾。")
    sys.exit()
except KeyError as e:
    print(f"❌ 錯誤：config.json 缺少必要設定值 {e}！")
    sys.exit()
except Exception as e:
    print(f"❌ 讀取 config.json 發生未知錯誤：{e}")
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
        # 自動載入 cogs/ 資料夾下的所有 .py 檔案
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and not filename.startswith(('_', '.')):
                extension_name = f'cogs.{filename[:-3]}'
                try:
                    await self.load_extension(extension_name)
                    print(f"🔄 [模組] {extension_name} 載入完成")
                except Exception as e:
                    print(f"❌ 載入模組 {extension_name} 時發生錯誤: {e}")
        # ========================================================

    async def on_ready(self):
        print('====================================')
        print(f'✅ 機器人已成功登入為: {self.user.name} (ID: {self.user.id})')
        print(f'✅ 目前時間: {discord.utils.utcnow().astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")}')
        print('====================================')
        
        if not self.synced_guilds:
            self.synced_guilds = True
            try:
                with open('guild_settings.json', 'r', encoding='utf-8') as f:
                    guild_settings = json.load(f)
            except Exception:
                guild_settings = {}

            changed = False
            for guild in self.guilds:
                guild_id_str = str(guild.id)
                if guild_id_str not in guild_settings:
                    guild_settings[guild_id_str] = {}
                    changed = True
                
                try:
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                    print(f"🔄 [指令] 斜線指令已瞬間同步至伺服器：{guild.name} ({guild.id})")
                except Exception as e:
                    print(f"⚠️ [警告] 同步至伺服器 {guild.name} 失敗: {e}")
            
            if changed:
                with open('guild_settings.json', 'w', encoding='utf-8') as f:
                    json.dump(guild_settings, f, ensure_ascii=False, indent=4)

    async def on_guild_join(self, guild):
        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                guild_settings = json.load(f)
        except Exception:
            guild_settings = {}

        guild_id_str = str(guild.id)
        if guild_id_str not in guild_settings:
            guild_settings[guild_id_str] = {}
            with open('guild_settings.json', 'w', encoding='utf-8') as f:
                json.dump(guild_settings, f, ensure_ascii=False, indent=4)
            print(f"🆕 [群組] 機器人加入了新伺服器：{guild.name} ({guild.id})，已記錄至設定檔。")

        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"🔄 [指令] 斜線指令已瞬間同步至新伺服器：{guild.name} ({guild.id})")
        except Exception as e:
            print(f"⚠️ [警告] 同步至新伺服器 {guild.name} 失敗: {e}")

    async def on_guild_remove(self, guild):
        print(f"🚪 [群組] 機器人離開或被踢出了伺服器：{guild.name} ({guild.id})")
        try:
            with open('guild_settings.json', 'r', encoding='utf-8') as f:
                guild_settings = json.load(f)
        except Exception:
            guild_settings = {}

        guild_id_str = str(guild.id)
        if guild_id_str in guild_settings:
            del guild_settings[guild_id_str]
            with open('guild_settings.json', 'w', encoding='utf-8') as f:
                json.dump(guild_settings, f, ensure_ascii=False, indent=4)
            print(f"🗑️ [系統] 已將伺服器 {guild.name} ({guild.id}) 的相關設定從記錄中清理。")

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

# 原神，啟動！
bot = MyBot()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)