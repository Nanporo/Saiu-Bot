import discord
from discord.ext import commands, tasks
import json
import datetime
import logging
import asyncio

logger = logging.getLogger(__name__)

class DiscordLoggingHandler(logging.Handler):
    """將 Logging 訊息轉發至 Discord 的 Handler"""
    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    def emit(self, record):
        try:
            log_entry = self.format(record)
            
            # 判斷日誌屬於哪個分類
            cmd_prefixes = ["[指令]", "[私訊]", "[提及]", "[查詢此地天氣]"]
            push_prefixes = ["[空品預警]", "[CBS預警]", "[EEW 警報]", "[地震通知]", "[淹水預警]", "[降雨預報]", "[停班停課]", "[氣溫預警]", "[颱風通知]"]
            
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
        self.channel_id = None
        self.channel_cmd_id = None
        self.channel_push_id = None
        self.root_logger = logging.getLogger()
        
        # 從 config.json 讀取頻道 ID
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.channel_id = config.get('CONSOLE_ID')
            self.channel_cmd_id = config.get('CONSOLE_COMMAND_ID')
            self.channel_push_id = config.get('CONSOLE_PUSH_ID')
        except Exception:
            pass
            
        if self.channel_id:
            self.discord_handler = DiscordLoggingHandler(self)
            formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%H:%M:%S')
            self.discord_handler.setFormatter(formatter)
            self.discord_handler.setLevel(logging.INFO)
            self.root_logger.addHandler(self.discord_handler)

            self.send_console_task.start()
        else:
            logger.warning("未設定 CONSOLE_ID，Console 轉發功能已停用。")

    def cog_unload(self):
        if self.channel_id:
            self.root_logger.removeHandler(self.discord_handler)
            self.send_console_task.cancel()

    @tasks.loop(seconds=3)
    async def send_console_task(self):
        try:
            await self.bot.wait_until_ready()
            
            async def send_buffer(channel_id, buffer):
                if not buffer or not channel_id:
                    return
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    return

                text_to_send = "".join(buffer)
                buffer.clear()
                
                max_length = 1980
                for i in range(0, len(text_to_send), max_length):
                    chunk = text_to_send[i:i+max_length]
                    if chunk.strip():
                        await channel.send(f"```text\n{chunk}\n```")

            await asyncio.gather(
                send_buffer(self.channel_id, self.buffer_main),
                send_buffer(self.channel_cmd_id, self.buffer_cmd),
                send_buffer(self.channel_push_id, self.buffer_push)
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
                
        logger.info(f"[指令] {user} 於 {guild} 使用了斜線指令：/{command.name}{params_str}")

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        user = ctx.author
        guild = ctx.guild.name if ctx.guild else "私人訊息"
        logger.info(f"[指令] {user} 於 {guild} 使用了傳統指令：{ctx.message.content}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
            
        is_dm = message.guild is None
        if is_dm:
            logger.info(f"[私訊] {message.author} 傳送了私訊：{message.clean_content}")
            
        if self.bot.user in message.mentions:
            guild_name = message.guild.name if message.guild else "私訊"
            logger.info(f"[提及] {message.author} 於 {guild_name} 提及了機器人：{message.clean_content}")

async def setup(bot):
    await bot.add_cog(ConsoleOutputCog(bot))