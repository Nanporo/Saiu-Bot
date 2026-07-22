import discord
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)

class Status(commands.Cog):
    """機器人 Discord 狀態 (Presence) 管理模組"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _update_presence(self):
        try:
            activity = discord.CustomActivity(name="Discord 天氣小助手")
            await self.bot.change_presence(activity=activity)
            logger.info("🤖 [狀態] 已將機器人 Discord 狀態設定為：「Discord 天氣小助手」")
        except Exception as e:
            logger.error(f"❌ [狀態] 設定狀態時發生錯誤: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        await self._update_presence()

    async def cog_load(self):
        if self.bot.is_ready():
            await self._update_presence()

async def setup(bot: commands.Bot):
    await bot.add_cog(Status(bot))
