import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import uuid
import urllib.parse
import json
import re
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

def clean_html(text: str) -> str:
    if not text:
        return ""
        
    def replace_a(match):
        url = match.group(1).strip("'\"")
        link_text = match.group(2).strip()
        if not link_text:
            link_text = "點此查看"
        return f"[{link_text}]({url})"

    # 將 HTML 超連結 <a href=...>...</a> 轉為 Markdown 語法 [內容](網址)
    text = re.sub(r'<a\s+[^>]*href=[\'"]?([^\s\'">]+)[\'"]?[^>]*>(.*?)</a>', replace_a, text, flags=re.IGNORECASE | re.DOTALL)
    # 將 <br> 換行標籤轉為 \n
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    # 將 <b> 或 <strong> 轉為 Discord 粗體 **
    text = re.sub(r'</?(?:b|strong)>', '**', text, flags=re.IGNORECASE)
    # 清除其餘殘留的 HTML 標籤
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()

class YunBotCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def fetch_yunbot_response(self, question: str):
        user_uuid = str(uuid.uuid4())
        msg_uuid = str(uuid.uuid4())
        
        host = "https://chatbot.service.iisigroup.com/CWAbot-proxy"
        sse_url = f"{host}/sse/connect/{user_uuid}/300000"
        encoded_q = urllib.parse.quote(question)
        say_url = f"{host}/webhooks/iisichatroom/conversations/{user_uuid}/say?message={encoded_q}&uuid={msg_uuid}&isVoice=false&language=zh-TW"
        
        answer_parts = []
        reference_files = []
        errors = []
        
        session = self.bot.session if getattr(self.bot, 'session', None) and not self.bot.session.closed else aiohttp.ClientSession()

        async def listen_sse():
            try:
                async with session.get(sse_url, timeout=aiohttp.ClientTimeout(total=10.0, sock_read=5.0)) as resp:
                    if resp.status != 200:
                        errors.append(f"SSE HTTP {resp.status}")
                        return
                        
                    async for line_bytes in resp.content:
                        line = line_bytes.decode('utf-8', errors='ignore').strip()
                        if not line or not line.startswith("data:"):
                            continue
                            
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                            
                        try:
                            items = json.loads(data_str)
                            if isinstance(items, list):
                                for item in items:
                                    msg = item.get("message", {})
                                    msg_type = msg.get("type")
                                    if msg_type in ("text", "rag"):
                                        txt = msg.get("text", "")
                                        if txt and (not answer_parts or txt not in answer_parts[-1]):
                                            answer_parts.append(txt)
                                    elif msg_type == "rag_file":
                                        files = msg.get("files", [])
                                        if isinstance(files, list):
                                            reference_files.extend(files)
                        except Exception:
                            pass
            except asyncio.TimeoutError:
                errors.append("SSE Timeout")
            except aiohttp.ClientError as e:
                errors.append(f"SSE ClientError: {e!r}")
            except (asyncio.CancelledError, Exception) as e:
                errors.append(f"SSE Exception: {e!r}")

        sse_task = asyncio.create_task(listen_sse())
        await asyncio.sleep(0.2)
        
        try:
            async with session.get(say_url, timeout=aiohttp.ClientTimeout(total=8.0)) as resp:
                if resp.status != 200:
                    errors.append(f"Say HTTP {resp.status}")
        except Exception as e:
            errors.append(f"Say Exception: {e!r}")

        try:
            await asyncio.wait_for(asyncio.shield(sse_task), timeout=10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
            pass

        full_answer = clean_html("".join(answer_parts))

        # Fallback to longPollLog if SSE didn't return text
        if not full_answer:
            try:
                poll_url = f"{host}/webhooks/iisichatroom/conversations/{user_uuid}/longPollLog?nocache={int(asyncio.get_event_loop().time()*1000)}"
                async with session.get(poll_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        data = json.loads(text)
                        if isinstance(data, list):
                            for msg_item in data:
                                if msg_item.get("username") == "bot":
                                    msg = msg_item.get("message", {})
                                    txt = msg.get("text", "")
                                    if txt:
                                        full_answer = clean_html(txt)
                                        break
                    else:
                        errors.append(f"LongPoll HTTP {resp.status}")
            except Exception as e:
                errors.append(f"LongPoll Exception: {e!r}")

        return full_answer, reference_files, errors

    @app_commands.command(name="雲寶", description="🤖 詢問中央氣象署「雲寶 AI 氣象小幫手」")
    @app_commands.describe(提問="請輸入要詢問雲寶的問題，例如：明天台北會下雨嗎？")
    async def yunbot_command(self, interaction: discord.Interaction, 提問: str):
        await interaction.response.defer()
        
        answer, files, errors = await self.fetch_yunbot_response(提問)
        
        if not answer:
            err_details = ", ".join(errors) if errors else "未傳回文字回應 (無回應內容)"
            logger.error(f"❌ [雲寶] 無法取得回應 | 提問: {提問} | 錯誤代碼/原因: {err_details}")
            answer = "⚠️ 抱歉，暫時無法取得雲寶的回應，請稍後再試。"
            
        content = "<:yunbot:1532298313854881852> 雲寶問天氣"
        
        embed = discord.Embed(
            color=0x182987
        )
        embed.add_field(name="用戶提問", value=提問, inline=False)
        embed.add_field(name="雲寶回答", value=answer, inline=False)
        
        if files:
            links = []
            for f in files:
                title = f.get("title", "相關頁面")
                url = f.get("url", "")
                if url:
                    links.append(f"[{title}]({url})")
                else:
                    links.append(title)
            if links:
                embed.add_field(name="🔗 參考來源", value="\n".join(links), inline=False)
        
        now = datetime.now(timezone(timedelta(hours=8)))
        current_time = now.strftime("%m-%d %H:%M")
        embed.set_footer(
            text=f"雲寶問天氣 Powered by iNana • 查詢時間 {current_time}",
            icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png"
        )
        
        await interaction.followup.send(content=content, embed=embed)

async def setup(bot):
    await bot.add_cog(YunBotCog(bot))
