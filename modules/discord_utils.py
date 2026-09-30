"""Discord API operations shared by background notification cogs."""

import logging

import discord


logger = logging.getLogger(__name__)


async def safe_channel_send(channel, *, context: str = "通知", **kwargs):
    """Send a message without letting a stale/deleted channel stop a task loop.

    A channel can disappear between ``bot.get_channel(...)`` and the REST call
    made by ``channel.send(...)``.  The caller receives ``None`` on expected
    delivery failures and a warning is logged with the channel ID.
    """
    if channel is None:
        return None

    try:
        return await channel.send(**kwargs)
    except discord.NotFound:
        logger.warning("⚠️ [%s] 頻道 %s 已不存在，略過推送。", context, channel.id)
    except discord.Forbidden:
        logger.warning("⚠️ [%s] 沒有權限傳送至頻道 %s，略過推送。", context, channel.id)
    except discord.HTTPException as exc:
        logger.warning("⚠️ [%s] 傳送至頻道 %s 失敗：%r", context, channel.id, exc)
    return None
