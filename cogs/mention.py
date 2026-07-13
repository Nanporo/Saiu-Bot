import discord
from discord.ext import commands
import random
import logging

logger = logging.getLogger(__name__)

class MentionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.responses = [
            "有什麼可以幫忙的？",
            "我在這裡！",
            "嗨！我是小裁雨！"
        ]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 忽略機器人本身的訊息或其他機器人的訊息
        if message.author.bot:
            return
            
        # 檢查機器人是否被提及 (排除 @everyone / @here)
        if self.bot.user in message.mentions and not message.mention_everyone:
            # 判斷是不是單純「回覆」機器人的訊息 (回覆時開啟了 ping，但文字內容沒有打出 @機器人)
            # 只有當訊息內容明確包含 <@機器人ID> 或是 <@!機器人ID> 時，才當作是主動 at
            if f"<@{self.bot.user.id}>" not in message.content and f"<@!{self.bot.user.id}>" not in message.content:
                return

            reply = random.choice(self.responses)
            text = f"{reply}\n> 可以使用 `/幫助` 或是 `/關於` 指令來了解更多資訊！"
            
            try:
                await message.reply(text)
            except discord.HTTPException:
                pass

async def setup(bot):
    await bot.add_cog(MentionCog(bot))
