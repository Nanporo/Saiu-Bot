import discord
from discord.ext import commands, tasks
import json
import datetime
import logging
import asyncio
import aiohttp
from modules.config import get_config

logger = logging.getLogger(__name__)

class DiscordLoggingHandler(logging.Handler):
    """將 Logging 訊息轉發至 Discord 的 Handler"""
    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    def emit(self, record):
        try:
            log_entry = self.format(record)
            
            cmd_prefixes = ["[指令]", "[私訊]", "[提及]", "[被at]", "[查詢此地天氣]", "[小裁雨 AI", "[AI 備援]"]
            push_prefixes = ["[空品預警]", "[CBS預警]", "[EEW 警報]", "[地震通知]", "[淹水預警]", "[降雨預報]", "[停班停課]", "[氣溫預警]", "[颱風通知]", "[大雷雨]", "[CBS演習預告]"]
            
            if any(p in log_entry for p in cmd_prefixes):
                self.cog.buffer_cmd.append(log_entry + '\n')
            elif any(p in log_entry for p in push_prefixes):
                self.cog.buffer_push.append(log_entry + '\n')
            else:
                self.cog.buffer_main.append(log_entry + '\n')
        except Exception:
            self.handleError(record)

class ConsoleOutputCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.buffer_main = []
        self.buffer_cmd = []
        self.buffer_push = []
        self.root_logger = logging.getLogger()
        
        # 讀取 Webhook URL 設定
        config = get_config()
        self.webhook_url = config.get('CONSOLE_WEBHOOK_URL')
        self.webhook_cmd_url = config.get('CONSOLE_COMMAND_WEBHOOK_URL') or self.webhook_url
        self.webhook_push_url = config.get('CONSOLE_PUSH_WEBHOOK_URL') or self.webhook_url
            
        if self.webhook_url or self.webhook_cmd_url or self.webhook_push_url:
            self.discord_handler = DiscordLoggingHandler(self)
            formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%H:%M:%S')
            self.discord_handler.setFormatter(formatter)
            self.discord_handler.setLevel(logging.INFO)
            self.root_logger.addHandler(self.discord_handler)

            self.send_console_task.start()
        else:
            logger.warning("未設定 CONSOLE_WEBHOOK_URL，Console 轉發功能已停用。")

    def cog_unload(self):
        if self.webhook_url or self.webhook_cmd_url or self.webhook_push_url:
            self.root_logger.removeHandler(self.discord_handler)
            self.send_console_task.cancel()

    @tasks.loop(seconds=3)
    async def send_console_task(self):
        try:
            await self.bot.wait_until_ready()
            
            async def send_buffer(webhook_url, buffer):
                if not buffer or not webhook_url:
                    return

                text_to_send = "".join(buffer)
                buffer.clear()
                
                session = getattr(self.bot, 'session', None)
                close_session = False
                if session is None or session.closed:
                    session = aiohttp.ClientSession()
                    close_session = True

                try:
                    webhook = discord.Webhook.from_url(webhook_url, session=session)
                    max_length = 1980
                    for i in range(0, len(text_to_send), max_length):
                        chunk = text_to_send[i:i+max_length]
                        if chunk.strip():
                            await webhook.send(f"```text\n{chunk}\n```")
                except Exception as ex:
                    print(f"❌ Webhook 發送失敗 ({webhook_url}): {ex}")
                finally:
                    if close_session:
                        await session.close()

            await asyncio.gather(
                send_buffer(self.webhook_url, self.buffer_main),
                send_buffer(self.webhook_cmd_url, self.buffer_cmd),
                send_buffer(self.webhook_push_url, self.buffer_push)
            )
        except Exception as e:
            # 發生錯誤時直接輸出到終端機避免無窮迴圈
            print(f"❌ send_console_task 發生錯誤: {e}")

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command):
        user = interaction.user
        guild = interaction.guild.name if interaction.guild else "私人訊息"
        
        options = interaction.data.get('options', [])
        params_str = ""
        if options:
            try:
                params = []
                for opt in options:
                    if 'value' in opt:
                        params.append(f"{opt['name']}={opt['value']}")
                    elif 'options' in opt: # 處理子指令(Subcommand)的情況
                        for sub_opt in opt['options']:
                            if 'value' in sub_opt:
                                params.append(f"{sub_opt['name']}={sub_opt['value']}")
                if params:
                    params_str = f" 參數: ({', '.join(params)})"
            except Exception:
                pass
                
        logger.info(f"🔄 [指令] {user} 於 {guild} 使用了斜線指令：/{command.name}{params_str}")

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        user = ctx.author
        guild = ctx.guild.name if ctx.guild else "私人訊息"
        logger.info(f"🔄 [指令] {user} 於 {guild} 使用了傳統指令：{ctx.message.content}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
            
        is_dm = message.guild is None
        if is_dm:
            logger.info(f"💭 [私訊] {message.author} 傳送了私訊：{message.clean_content}")
            
        # 檢查機器人是否被提及 (排除 @everyone / @here)
        if self.bot.user in message.mentions and not message.mention_everyone:
            guild_name = message.guild.name if message.guild else "私訊"
            
            # 判斷是「明確 at」還是單純「回覆」
            if f"<@{self.bot.user.id}>" in message.content or f"<@!{self.bot.user.id}>" in message.content:
                logger.info(f"💬 [被at] {message.author} 於 {guild_name} at 了機器人：{message.clean_content}")
            else:
                logger.info(f"💬 [提及] {message.author} 於 {guild_name} 回覆/提及了機器人：{message.clean_content}")

async def setup(bot):
    await bot.add_cog(ConsoleOutputCog(bot))