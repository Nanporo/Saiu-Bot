import discord
from discord.ext import commands
import aiohttp
import asyncio
import re
import random
import logging
import os
from datetime import datetime, timezone, timedelta
from modules.config import get_config

logger = logging.getLogger(__name__)

class MentionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.responses = [
            "有什麼可以幫忙的？",
            "我在這裡！",
            "嗨！我是小裁雨！"
        ]

    async def fetch_groq_response(self, user_prompt: str, api_key_str: str, system_instruction: str = None) -> str:
        # 支援多組 API Key (以逗點或分號分隔) 進行備援輪替
        keys = [k.strip() for k in re.split(r'[,;]', api_key_str) if k.strip()]
        if not keys:
            return None

        # 優先模型順序 (Groq 平台)
        models = [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "deepseek-r1-distill-llama-70b"
        ]

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": user_prompt or "嗨！"})

        session = self.bot.session if getattr(self.bot, 'session', None) and not self.bot.session.closed else aiohttp.ClientSession()

        quota_exceeded = False
        for key in keys:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            }
            key_quota_hit = False
            for model in models:
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.7,
                    "max_tokens": 1200
                }
                req_url = "https://api.groq.com/openai/v1/chat/completions"
                try:
                    async with session.post(req_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            choices = data.get("choices", [])
                            if choices:
                                content = choices[0].get("message", {}).get("content", "")
                                if content:
                                    return content.strip()
                        elif resp.status == 429:
                            quota_exceeded = True
                            key_quota_hit = True
                            err_text = await resp.text()
                            key_display = f"...{key[-4:]}" if len(key) >= 4 else "key"
                            logger.warning(f"🌐 Groq API [{model}] (Key: {key_display}) 返回狀態碼 429 (頻率限制): {err_text[:150]}")
                            break
                        elif resp.status == 404:
                            err_text = await resp.text()
                            logger.warning(f"🌐 Groq API [{model}] 返回狀態碼 404 (模型不存在): {err_text[:150]}")
                        else:
                            err_text = await resp.text()
                            logger.warning(f"🌐 Groq API [{model}] 返回狀態碼 {resp.status}: {err_text[:150]}")
                except Exception as e:
                    logger.error(f"❌ Groq API [{model}] 呼叫失敗: {e!r}")
            if key_quota_hit:
                continue

        if quota_exceeded:
            return "QUOTA_EXCEEDED"
        return None

    async def fetch_gemini_response(self, user_prompt: str, api_key_str: str, system_instruction: str = None) -> str:
        # 支援多組 API Key (以逗點或分號分隔) 進行備援輪替
        keys = [k.strip() for k in re.split(r'[,;]', api_key_str) if k.strip()]
        if not keys:
            return None

        # 優先順序：Gemini 2.0 Flash -> 2.0 Flash Lite -> 1.5 Flash Latest -> Flash Latest
        models = [
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-flash-latest",
            "gemini-flash-latest"
        ]

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt or "嗨！"}]
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 1200
            }
        }

        if system_instruction:
            payload["system_instruction"] = {
                "parts": [{"text": system_instruction}]
            }

        session = self.bot.session if getattr(self.bot, 'session', None) and not self.bot.session.closed else aiohttp.ClientSession()

        quota_exceeded = False
        for key in keys:
            key_quota_hit = False
            for model in models:
                req_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                try:
                    async with session.post(req_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                if parts and "text" in parts[0]:
                                    return parts[0]["text"].strip()
                        elif resp.status == 429:
                            quota_exceeded = True
                            key_quota_hit = True
                            err_text = await resp.text()
                            key_display = f"...{key[-4:]}" if len(key) >= 4 else "key"
                            logger.warning(f"🌐 Gemini API [{model}] (Key: {key_display}) 返回狀態碼 429 (配額上限/頻率限制): {err_text[:150]}")
                            break  # 此 Key 已達配額限制，跳出 model 迴圈嘗試下一個 Key
                        elif resp.status == 404:
                            err_text = await resp.text()
                            logger.warning(f"🌐 Gemini API [{model}] 返回狀態碼 404 (模型不存在或已被棄用): {err_text[:150]}")
                        else:
                            err_text = await resp.text()
                            logger.warning(f"🌐 Gemini API [{model}] 返回狀態碼 {resp.status}: {err_text[:150]}")
                except Exception as e:
                    logger.error(f"❌ Gemini API [{model}] 呼叫失敗: {e!r}")
            if key_quota_hit:
                continue

        if quota_exceeded:
            return "QUOTA_EXCEEDED"
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 忽略機器人本身的訊息或其他機器人的訊息
        if message.author.bot:
            return

        # 排除 @everyone / @here
        if message.mention_everyone:
            return

        # 檢查機器人是否被個人提及 (排除單純身分組 @role 標記或訊息回覆 ping)
        if self.bot.user in message.mentions:
            # 判斷是不是單純「回覆」機器人的訊息或 @身分組 (只有當訊息內容明確包含 <@機器人ID> 或是 <@!機器人ID> 時，才當作是主動 at 機器人)
            if f"<@{self.bot.user.id}>" not in message.content and f"<@!{self.bot.user.id}>" not in message.content:
                return

            config = get_config()
            config.reload()
            groq_key = config.get('GROQ_API_KEY') or os.getenv('GROQ_API_KEY')
            gemini_key = config.get('GEMINI_API_KEY') or os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
            sys_instruction = config.get('SAIU_SYSTEM_INSTRUCTION') or os.getenv('SAIU_SYSTEM_INSTRUCTION')

            # 擷取使用者輸入的純文字，並過濾掉 @機器人 與 @身分組 (<@&ID>) 標記
            user_prompt = re.sub(rf'<@!?{self.bot.user.id}>', '', message.content)
            user_prompt = re.sub(r'<@&\d+>', '', user_prompt).strip()

            # 構建動態情境資訊 (Dynamic Context)
            now = datetime.now(timezone(timedelta(hours=8)))
            current_time = now.strftime("%Y-%m-%d %H:%M:%S")
            author_name = message.author.display_name
            guild_name = message.guild.name if message.guild else "私訊"
            channel_name = getattr(message.channel, "name", "私訊")

            ref_text = ""
            if message.reference and message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
                ref_msg = message.reference.resolved
                ref_author = ref_msg.author.display_name
                ref_content = ref_msg.content.strip()
                if ref_content:
                    ref_text = f"\n[回覆的上一條訊息 (由 {ref_author} 發送)]: \"{ref_content}\""

            dynamic_prompt = (
                f"[當前即時情境]\n"
                f"- 時間：{current_time}\n"
                f"- 對話使用者：{author_name}\n"
                f"- 頻道：{guild_name} / {channel_name}"
                f"{ref_text}\n\n"
                f"[使用者輸入]: {user_prompt if user_prompt else '（向你打招呼）'}"
            )

            if groq_key or gemini_key:
                try:
                    async with message.channel.typing():
                        ai_reply = None
                        provider_used = None

                        # 1. 優先嘗試使用 Groq API
                        if groq_key:
                            ai_reply = await self.fetch_groq_response(dynamic_prompt, groq_key, system_instruction=sys_instruction)
                            if ai_reply and ai_reply != "QUOTA_EXCEEDED":
                                provider_used = "Groq"

                        # 2. 若未設定 Groq Key 或 Groq 失敗/超額，退回使用 Gemini API
                        if not ai_reply or ai_reply == "QUOTA_EXCEEDED":
                            if gemini_key:
                                logger.info("🔄 [AI 備援] 嘗試切換/使用 Gemini API 回應...")
                                g_reply = await self.fetch_gemini_response(dynamic_prompt, gemini_key, system_instruction=sys_instruction)
                                if g_reply and g_reply != "QUOTA_EXCEEDED":
                                    ai_reply = g_reply
                                    provider_used = "Gemini"
                                elif g_reply == "QUOTA_EXCEEDED" and (ai_reply == "QUOTA_EXCEEDED" or not groq_key):
                                    ai_reply = "QUOTA_EXCEEDED"

                        if ai_reply == "QUOTA_EXCEEDED":
                            reply = random.choice(self.responses)
                            text = f"{reply}\n> ⚠️ AI 聊天功能目前用量已達上限 (429 Rate Limit / Quota Exceeded)，請稍後再試！"
                            await message.reply(text)
                            return
                        elif ai_reply:
                            logger.info(f"💬 [小裁雨 AI ({provider_used})] 於 {guild_name} ({channel_name}) 回應 {author_name}: {ai_reply}")
                            disclaimer = "\n-# ⚠️ AI 回覆可能會出錯，資料應以氣象署為準。"
                            await message.reply(f"{ai_reply}{disclaimer}")
                            return
                except Exception as e:
                    logger.error(f"❌ 小裁雨 AI 回覆失敗: {e!r}")

            # 備用：無 API Key 或 AI 呼叫失敗時的回應
            reply = random.choice(self.responses)
            if not groq_key and not gemini_key:
                text = f"{reply}\n> 聊天功能目前已關閉！"
            else:
                text = f"{reply}\n> 可以使用 `/幫助` 或是 `/關於` 指令來了解更多資訊！"
            
            try:
                await message.reply(text)
            except discord.HTTPException:
                pass

async def setup(bot):
    await bot.add_cog(MentionCog(bot))
