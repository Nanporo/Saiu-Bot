import discord
from discord.ext import commands, tasks
import logging
import aiohttp

from modules.config import get_config

logger = logging.getLogger(__name__)

class HealthCheckCog(commands.Cog):
    """Healthchecks.io 在線狀態監測模組"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        config = get_config()
        self.ping_url = config.get("HEALTHCHECK_URL", "")

        if self.ping_url:
            logger.info(f"⚙️ [Healthcheck] 已載入 Ping 網址: {self.ping_url}")
            self.healthcheck_task.start()
        else:
            logger.warning("⚠️ [Healthcheck] 未設定 HEALTHCHECK_URL，Healthchecks.io 偵測功能已停用。")

    def cog_unload(self):
        if self.ping_url and self.healthcheck_task.is_running():
            self.healthcheck_task.cancel()

    @tasks.loop(seconds=60.0)
    async def healthcheck_task(self):
        try:
            session = getattr(self.bot, 'session', None)
            close_session = False
            if session is None or session.closed:
                session = aiohttp.ClientSession()
                close_session = True

            try:
                async with session.get(self.ping_url, timeout=10) as resp:
                    if resp.status == 200:
                        logger.debug("🟢 [Healthcheck] Healthchecks.io Ping 成功")
                    else:
                        logger.warning(f"⚠️ [Healthcheck] Ping 回應狀態碼異常: {resp.status}")
            except Exception as ex:
                logger.error(f"❌ [Healthcheck] Ping 發送失敗: {ex}")
            finally:
                if close_session:
                    await session.close()
        except Exception as e:
            logger.error(f"❌ [Healthcheck] healthcheck_task 發生錯誤: {e}")

    @healthcheck_task.before_loop
    async def before_healthcheck(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(HealthCheckCog(bot))
