import discord
from discord.ext import commands, tasks
import sys
import json
import datetime

class ConsoleRedirector:
    """攔截標準輸出的類別，將輸出內容複製一份至緩衝區"""
    def __init__(self, original_stream, cog):
        self.original_stream = original_stream
        self.cog = cog
        self.start_of_line = True

    def write(self, text):
        if not text:
            return
            
        now = datetime.datetime.now().strftime("[%H:%M:%S]")
        lines = text.split('\n')
        formatted_text = ""
        
        for i, line in enumerate(lines):
            if i > 0:
                formatted_text += '\n'
                self.start_of_line = True
                
            if line:
                if self.start_of_line:
                    formatted_text += f"{now} {line}"
                    self.start_of_line = False
                else:
                    formatted_text += line

        # 保持原有的終端機輸出
        self.original_stream.write(formatted_text)
        # 將文字加入 Cog 的緩衝區
        self.cog.buffer.append(formatted_text)

    def flush(self):
        self.original_stream.flush()

class ConsoleOutputCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.buffer = []
        self.channel_id = None
        
        # 從 config.json 讀取 CONSOLE_ID
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.channel_id = config.get('CONSOLE_ID')
        except Exception:
            pass
            
        if self.channel_id:
            # 備份原始的輸出流
            self.original_stdout = sys.stdout
            self.original_stderr = sys.stderr
            
            # 重定向 stdout (一般 print) 與 stderr (錯誤輸出)
            sys.stdout = ConsoleRedirector(sys.stdout, self)
            sys.stderr = ConsoleRedirector(sys.stderr, self)
            
            self.send_console_task.start()
        else:
            print("⚠️ 未設定 CONSOLE_ID，Console 轉發功能已停用。")

    def cog_unload(self):
        # 卸載 Cog 時還原原始的輸出流，避免影響其他程式
        if self.channel_id:
            sys.stdout = self.original_stdout
            sys.stderr = self.original_stderr
            self.send_console_task.cancel()

    @tasks.loop(seconds=3)
    async def send_console_task(self):
        try:
            if not self.buffer:
                return
                
            await self.bot.wait_until_ready()
            
            channel = self.bot.get_channel(int(self.channel_id))
            if not channel:
                return

            text_to_send = "".join(self.buffer)
            self.buffer.clear()
            
            # Discord 訊息上限是 2000 字元，預留 ```text\n\n``` 空間分割字串
            max_length = 1980
            for i in range(0, len(text_to_send), max_length):
                chunk = text_to_send[i:i+max_length]
                if chunk.strip():
                    await channel.send(f"```text\n{chunk}\n```")
        except Exception as e:
            if hasattr(self, 'original_stderr'):
                self.original_stderr.write(f"❌ send_console_task 發生錯誤: {e}\n")

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
                
        print(f"⌨️ [指令] {user} 於 {guild} 使用了斜線指令：/{command.name}{params_str}")

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        user = ctx.author
        guild = ctx.guild.name if ctx.guild else "私人訊息"
        print(f"⌨️ [指令] {user} 於 {guild} 使用了傳統指令：{ctx.message.content}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
            
        is_dm = message.guild is None
        if is_dm:
            print(f"💬 [私訊] {message.author} 傳送了私訊：{message.clean_content}")
            
        if self.bot.user in message.mentions:
            guild_name = message.guild.name if message.guild else "私訊"
            print(f"🔔 [提及] {message.author} 於 {guild_name} 提及了機器人：{message.clean_content}")

async def setup(bot):
    await bot.add_cog(ConsoleOutputCog(bot))