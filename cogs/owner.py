import discord
from discord.ext import commands
from discord import app_commands
import sys
import os
import json
from modules.database import get_all_settings
from modules.ownercheck import is_owner

try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    OWNER_SERVER_ID = int(config.get('OWNER_SERVER_ID', 0))
except Exception:
    OWNER_SERVER_ID = 0

class OwnerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="關機", description="（限擁有者）關閉 BOT")
    @app_commands.guilds(OWNER_SERVER_ID)
    async def shutdown(self, interaction: discord.Interaction):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return
            
        await interaction.response.send_message("🛑 正在關閉機器人...", ephemeral=True)
        print("🛑 收到關閉指令，機器人正在關閉...")
        await self.bot.close()

    @app_commands.command(name="重啟", description="（限擁有者）重新啟動機器人")
    @app_commands.guilds(OWNER_SERVER_ID)
    async def restart(self, interaction: discord.Interaction):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return

        await interaction.response.send_message("🔄 正在重新啟動機器人...", ephemeral=True)
        print("🔄 收到重啟指令，機器人正在重新啟動...")
        await self.bot.close()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    @app_commands.command(name="退出", description="（限擁有者）強制退出指定的伺服器")
    @app_commands.describe(guild_id="伺服器 ID")
    @app_commands.guilds(OWNER_SERVER_ID)
    async def leave_guild(self, interaction: discord.Interaction, guild_id: str):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return
            
        try:
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                await interaction.response.send_message(f"❌ 找不到 ID 為 `{guild_id}` 的伺服器，可能機器人不在該伺服器內。", ephemeral=True)
                return
                
            await guild.leave()
            await interaction.response.send_message(f"✅ 已成功退出伺服器：**{guild.name}** (`{guild.id}`)", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ 伺服器 ID 格式錯誤，必須為數字。", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 發生錯誤：{e}", ephemeral=True)

    @app_commands.command(name="廣播", description="（限擁有者）對所有已開啟自動推送的伺服器發送系統廣播")
    @app_commands.describe(message="廣播內容支援 Markdown，可輸入 \\n 來換行")
    @app_commands.guilds(OWNER_SERVER_ID)
    async def broadcast(self, interaction: discord.Interaction, message: str):
        if not is_owner(interaction.user.id):
            await interaction.response.send_message("❌ 你沒有權限使用此指令。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            guild_settings = get_all_settings()
        except Exception:
            await interaction.followup.send("❌ 讀取資料庫失敗，無法廣播。")
            return

        # 支援輸入 \n 轉換成實際換行
        formatted_message = message.replace('\\n', '\n')
        message_content = "📢 頻道廣播"

        embed = discord.Embed(
            title="",
            description=formatted_message,
            color=0x2a9683,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Kuuchi | 機器人擁有者廣播", icon_url="https://avatars.githubusercontent.com/u/15816531?v=4")

        success_count = 0
        fail_count = 0
        
        for guild_id_str, settings in guild_settings.items():
            if not settings.get("auto_push", False):
                continue
                
            channel_ids = settings.get("target_channel_ids", [])
            for c_id in channel_ids:
                channel = self.bot.get_channel(int(c_id))
                if channel:
                    try:
                        await channel.send(content=message_content, embed=embed)
                        success_count += 1
                    except Exception:
                        fail_count += 1
                else:
                    fail_count += 1

        await interaction.followup.send(f"✅ 廣播完成！共成功發送至 {success_count} 個頻道，失敗 {fail_count} 個頻道。")

async def setup(bot):
    await bot.add_cog(OwnerCog(bot))