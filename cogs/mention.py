import discord
from discord.ext import commands
import aiohttp
import asyncio
import re
import random
import logging
import os
import time
import json
from datetime import datetime, timezone, timedelta
from modules.config import get_config
from cogs.settings.settings_utils import load_settings
from modules.ai_cache import ai_cache, conversation_cache

try:
    from modules.ai_tools import AI_TOOLS_SCHEMA, GEMINI_TOOLS_SCHEMA, execute_tool
except ImportError:
    AI_TOOLS_SCHEMA = []
    GEMINI_TOOLS_SCHEMA = []
    async def execute_tool(bot, tool_name, args):
        return "{}"

try:
    from modules.prompt import get_system_instruction
except ImportError:
    def get_system_instruction() -> str:
        config = get_config()
        instruction = config.get('SAIU_SYSTEM_INSTRUCTION') or os.getenv('SAIU_SYSTEM_INSTRUCTION')
        if instruction and instruction.strip():
            return instruction.strip()
        return (
            "你是「小裁雨 (Saiu)」，一個親切、溫柔且專業的 Discord 天氣與生活防災小助手。你熟悉臺灣的地理、氣候與災防知識。"
            "說話語氣自然、溫柔且真誠，只能使用繁體中文，請完全不要使用任何表情符號 (Emoji)。"
            "當使用者詢問天氣、氣溫、降雨或地震時，你擁有直接查詢真實氣象署資料庫的工具，必須主動調用工具查詢並直接回答，絕不能只回覆請使用斜線指令。"
        )

logger = logging.getLogger(__name__)

class MentionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.responses = [
            "有什麼可以幫忙的？",
            "我在這裡！",
            "嗨！我是小裁雨！"
        ]
        self.user_cooldowns = {}
        self.channel_last_bot_messages = {}

    async def fetch_groq_response(self, messages_or_prompt, api_key_str: str, system_instruction: str = None) -> str:
        # 支援多組 API Key (以逗點或分號分隔) 進行備援輪替
        keys = [k.strip() for k in re.split(r'[,;]', api_key_str) if k.strip()]
        if not keys:
            return None

        # 優先模型順序 (Groq 平台目前有效模型)
        models = [
            "openai/gpt-oss-20b",
            "openai/gpt-oss-120b",
            "llama-3.1-8b-instant",
            "llama-3.3-70b-versatile"
        ]

        if isinstance(messages_or_prompt, list):
            messages = [dict(m) for m in messages_or_prompt]
            if system_instruction and not any(m.get("role") == "system" for m in messages):
                messages.insert(0, {"role": "system", "content": system_instruction})
        else:
            messages = []
            if system_instruction:
                messages.append({"role": "system", "content": system_instruction})
            messages.append({"role": "user", "content": messages_or_prompt or "嗨！"})

        session = self.bot.session if getattr(self.bot, 'session', None) and not self.bot.session.closed else aiohttp.ClientSession()

        quota_exceeded = False
        for key in keys:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            }
            key_quota_hit = False
            for model in models:
                req_url = "https://api.groq.com/openai/v1/chat/completions"
                current_messages = [dict(m) for m in messages]
                use_tools = bool(AI_TOOLS_SCHEMA)

                # 最多允許 2 輪對話（1 次工具調用 + 1 次最終答覆，或直接答覆）
                for turn in range(2):
                    payload = {
                        "model": model,
                        "messages": current_messages,
                        "temperature": 0.7,
                        "max_tokens": 800
                    }
                    # 第 0 輪提供 tools；第 1 輪模型接收 tool 結果產出最終自然語言答覆時不傳遞 tools 規格，
                    # 可省下數千 token 的 overhead，避免觸發 Groq 的 TPM (8000/20000) 頻率限制 (429)
                    if use_tools and turn == 0:
                        payload["tools"] = AI_TOOLS_SCHEMA
                        payload["tool_choice"] = "auto"

                    try:
                        async with session.post(req_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                choices = data.get("choices", [])
                                if not choices:
                                    break
                                msg = choices[0].get("message", {})
                                tool_calls = msg.get("tool_calls")

                                # 1. 模型決定調用工具 (第一輪)
                                if tool_calls and turn == 0:
                                    current_messages.append(msg)
                                    for tc in tool_calls:
                                        tc_id = tc.get("id")
                                        fn = tc.get("function", {})
                                        fn_name = fn.get("name")
                                        fn_args_raw = fn.get("arguments", "{}")
                                        try:
                                            fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else (fn_args_raw or {})
                                        except Exception:
                                            fn_args = {}

                                        logger.info(f"🛠️ [Groq Tool Calling] 模型 {model} 呼叫工具: {fn_name}({fn_args})")
                                        tool_output = await execute_tool(self.bot, fn_name, fn_args)
                                        current_messages.append({
                                            "role": "tool",
                                            "tool_call_id": tc_id,
                                            "name": fn_name,
                                            "content": tool_output
                                        })
                                    # 繼續下一輪迴圈，讓模型根據工具資料產出最終自然語言答覆
                                    continue

                                # 2. 無工具調用，或已取得工具結果後的最終回覆
                                content = msg.get("content", "")
                                if content:
                                    return content.strip()
                                break

                            elif resp.status == 400 and use_tools and turn == 0:
                                # 模型可能不支援 tools 參數，自動降級為純文字重試
                                err_text = await resp.text()
                                logger.warning(f"🌐 Groq API [{model}] 可能不支援 tools，降級為純文字重試: {err_text[:120]}")
                                use_tools = False
                                payload.pop("tools", None)
                                payload.pop("tool_choice", None)
                                async with session.post(req_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as fallback_resp:
                                    if fallback_resp.status == 200:
                                        fallback_data = await fallback_resp.json()
                                        fb_choices = fallback_data.get("choices", [])
                                        if fb_choices:
                                            fb_content = fb_choices[0].get("message", {}).get("content", "")
                                            if fb_content:
                                                return fb_content.strip()
                                break

                            elif resp.status == 429:
                                quota_exceeded = True
                                err_text = await resp.text()
                                key_display = f"...{key[-4:]}" if len(key) >= 4 else "key"
                                logger.warning(f"🌐 Groq API [{model}] (Key: {key_display}) 返回狀態碼 429 (頻率限制): {err_text[:150]}")
                                if "day" in err_text.lower() or "daily" in err_text.lower():
                                    key_quota_hit = True
                                    break
                                break
                            elif resp.status == 404:
                                err_text = await resp.text()
                                logger.warning(f"🌐 Groq API [{model}] 返回狀態碼 404 (模型不存在): {err_text[:150]}")
                                break
                            else:
                                err_text = await resp.text()
                                logger.warning(f"🌐 Groq API [{model}] 返回狀態碼 {resp.status}: {err_text[:150]}")
                                break

                    except Exception as e:
                        logger.error(f"❌ Groq API [{model}] 呼叫失敗: {e!r}")
                        break

            if key_quota_hit:
                continue

        if quota_exceeded:
            return "QUOTA_EXCEEDED"
        return None

    async def fetch_gemini_response(self, contents_or_prompt, api_key_str: str, system_instruction: str = None) -> str:
        # 支援多組 API Key (以逗點或分號分隔) 進行備援輪替
        keys = [k.strip() for k in re.split(r'[,;]', api_key_str) if k.strip()]
        if not keys:
            return None

        # 優先順序：Gemini 3.5 Flash Lite -> 3.5 Flash -> Flash Lite Latest -> 3.6 Flash -> Flash Latest
        models = [
            "gemini-3.5-flash-lite",
            "gemini-3.5-flash",
            "gemini-flash-lite-latest",
            "gemini-3.6-flash",
            "gemini-flash-latest"
        ]

        if isinstance(contents_or_prompt, list):
            base_contents = [dict(c) for c in contents_or_prompt]
        else:
            base_contents = [
                {
                    "role": "user",
                    "parts": [{"text": contents_or_prompt or "嗨！"}]
                }
            ]

        session = self.bot.session if getattr(self.bot, 'session', None) and not self.bot.session.closed else aiohttp.ClientSession()

        quota_exceeded = False
        for key in keys:
            key_quota_hit = False
            for model in models:
                req_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                current_contents = [dict(c) for c in base_contents]
                use_tools = bool(GEMINI_TOOLS_SCHEMA)

                # 最多允許 2 輪對話（1 次工具調用 + 1 次最終答覆，或直接答覆）
                for turn in range(2):
                    payload = {
                        "contents": current_contents,
                        "generationConfig": {
                            "temperature": 0.7,
                            "maxOutputTokens": 800
                        }
                    }

                    if system_instruction:
                        payload["system_instruction"] = {
                            "parts": [{"text": system_instruction}]
                        }

                    # 第 0 輪提供 tools；第 1 輪模型接收 tool 結果產出最終自然語言答覆，不傳 tools
                    if use_tools and turn == 0:
                        payload["tools"] = GEMINI_TOOLS_SCHEMA

                    try:
                        async with session.post(req_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                candidates = data.get("candidates", [])
                                if not candidates:
                                    break
                                candidate = candidates[0]
                                content_obj = candidate.get("content", {})
                                parts = content_obj.get("parts", [])

                                # 檢查是否有 Tool Call (functionCall)
                                fn_call_part = next((p.get("functionCall") for p in parts if "functionCall" in p), None)

                                if fn_call_part and turn == 0:
                                    current_contents.append(content_obj)
                                    fn_name = fn_call_part.get("name")
                                    fn_args = fn_call_part.get("args", {})

                                    logger.info(f"🛠️ [Gemini Tool Calling] 模型 {model} 呼叫工具: {fn_name}({fn_args})")
                                    tool_output = await execute_tool(self.bot, fn_name, fn_args)
                                    try:
                                        output_obj = json.loads(tool_output) if isinstance(tool_output, str) else tool_output
                                    except Exception:
                                        output_obj = {"result": tool_output}

                                    current_contents.append({
                                        "role": "user",
                                        "parts": [{
                                            "functionResponse": {
                                                "name": fn_name,
                                                "response": {"output": output_obj}
                                            }
                                        }]
                                    })
                                    continue

                                # 最終文字回覆
                                for p in parts:
                                    if "text" in p and p["text"]:
                                        return p["text"].strip()
                                break

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
                                break  # 模型不存在，嘗試下一個模型
                            elif resp.status == 503:
                                err_text = await resp.text()
                                logger.warning(f"🌐 Gemini API [{model}] 返回狀態碼 503 (高負載/忙線中): {err_text[:150]}")
                                break  # 模型暫時過載，嘗試下一個模型
                            else:
                                err_text = await resp.text()
                                logger.warning(f"🌐 Gemini API [{model}] 返回狀態碼 {resp.status}: {err_text[:150]}")
                                break
                    except Exception as e:
                        logger.error(f"❌ Gemini API [{model}] 呼叫失敗: {e!r}")
                        break

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

        is_dm = message.guild is None

        if is_dm:
            # 私訊頻道：無需 @ 機器人 (若有 @ 機器人也會自動過濾標記)
            user_prompt = re.sub(rf'^\s*<@!?{self.bot.user.id}>', '', message.content).strip()
        else:
            # 群組頻道：檢查機器人是否被個人提及
            if self.bot.user not in message.mentions:
                return

            # 檢查伺服器設定是否開啟 AI 提及對話回應功能
            guild_id = str(message.guild.id)
            all_settings = load_settings()
            guild_settings = all_settings.get(guild_id, {})
            if not guild_settings.get("ai_mention_enabled", True):
                return

            # 1. 訊息開頭必須是 @機器人 (支援 <@ID> 與 <@!ID>)
            match = re.match(rf'^\s*<@!?{self.bot.user.id}>', message.content)
            if not match:
                return

            # 2. 提及機器人後的內容不能包含其他使用者標記 (<@ID>) 或身分組標記 (<@&ID>)
            rest_content = message.content[match.end():]
            if re.search(r'<@!?\d+>|<@&\d+>', rest_content):
                return

            # 擷取使用者輸入的純文字
            user_prompt = rest_content.strip()

        # 防刷版冷卻機制 (每個人每 10 秒只能觸發一次 AI 對話)
        now_ts = time.time()
        last_ts = self.user_cooldowns.get(message.author.id, 0)
        if now_ts - last_ts < 10:
            try:
                await message.add_reaction("😴")
            except discord.HTTPException:
                pass
            return

        self.user_cooldowns[message.author.id] = now_ts
        if len(self.user_cooldowns) > 100:
            self.user_cooldowns = {uid: ts for uid, ts in self.user_cooldowns.items() if now_ts - ts < 60}

        config = get_config()
        config.reload()
        groq_key = config.get('GROQ_API_KEY') or os.getenv('GROQ_API_KEY')
        gemini_key = config.get('GEMINI_API_KEY') or os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
        cwa_key = config.get('CWA_API_KEY') or os.getenv('CWA_API_KEY')
        sys_instruction = get_system_instruction()

        session = self.bot.session if getattr(self.bot, 'session', None) and not self.bot.session.closed else aiohttp.ClientSession()

        # 1. 獲取本地快取之即時氣象情境摘要 (Context Summary)
        realtime_summary = ""
        try:
            realtime_summary = await ai_cache.get_realtime_summary(session, api_key=cwa_key)
        except Exception as e:
            logger.debug(f"⚠️ [AI 即時摘要] 獲取失敗: {e}")

        # 2. 構建動態情境資訊 (Dynamic Context)
        now = datetime.now(timezone(timedelta(hours=8)))
        current_time = now.strftime("%Y-%m-%d %H:%M:%S")
        author_name = message.author.display_name
        guild_name = message.guild.name if message.guild else "私訊"
        channel_name = getattr(message.channel, "name", "私訊")

        context_lines = [
            "[當前即時情境]",
            f"- 時間：{current_time}",
            f"- 對話使用者：{author_name}",
            f"- 頻道：{guild_name} / {channel_name}"
        ]
        if realtime_summary:
            context_lines.append(f"- 即時全台氣象感知：{realtime_summary}")

        ref_text = ""
        if message.reference and message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
            ref_msg = message.reference.resolved
            ref_author = ref_msg.author.display_name
            ref_content = ref_msg.content.strip()
            if ref_content:
                ref_text = f"[回覆的上一條訊息 (由 {ref_author} 發送)]: \"{ref_content}\"\n"

        full_system_instruction = (
            f"{sys_instruction}\n\n"
            f"{chr(10).join(context_lines)}\n\n"
            f"[核心指示]\n"
            f"1. 當使用者詢問特定地點的天氣、氣溫、降雨、預報、地震、空氣品質、特報或颱風時，請務必主動調用工具取得真實數據並直接回答，嚴禁只回覆「請使用斜線指令」。\n"
            f"2. 若使用者回覆地名（例如承接前文確認的「台北市信義區」）或延伸問題（如「那明天呢？」），請結合多輪對話歷史脈絡直接回答。"
        )

        current_turn_input = f"{ref_text}{user_prompt if user_prompt else '（向你打招呼）'}".strip()

        # 3. 透過多輪對話快取格式化 Groq 與 Gemini 的請求內容
        groq_messages = conversation_cache.format_for_groq(
            message.channel.id,
            current_user_prompt=current_turn_input,
            system_instruction=full_system_instruction
        )

        gemini_contents = conversation_cache.format_for_gemini(
            message.channel.id,
            current_user_prompt=current_turn_input
        )

        if groq_key or gemini_key:
            try:
                async with message.channel.typing():
                    ai_reply = None
                    provider_used = None

                    # 1. 優先嘗試使用 Groq API（支援 Tool Calling）
                    if groq_key:
                        ai_reply = await self.fetch_groq_response(groq_messages, groq_key)
                        if ai_reply and ai_reply != "QUOTA_EXCEEDED":
                            provider_used = "Groq"

                    # 2. 若未設定 Groq Key 或 Groq 失敗/超額，退回使用 Gemini API
                    if not ai_reply or ai_reply == "QUOTA_EXCEEDED":
                        if gemini_key:
                            logger.info("🔄 [AI 備援] 嘗試切換/使用 Gemini API 回應...")
                            g_reply = await self.fetch_gemini_response(gemini_contents, gemini_key, system_instruction=full_system_instruction)
                            if g_reply and g_reply != "QUOTA_EXCEEDED":
                                ai_reply = g_reply
                                provider_used = "Gemini"
                            elif g_reply == "QUOTA_EXCEEDED" and (ai_reply == "QUOTA_EXCEEDED" or not groq_key):
                                ai_reply = "QUOTA_EXCEEDED"

                    if ai_reply == "QUOTA_EXCEEDED":
                        reply = random.choice(self.responses)
                        text = f"{reply}\n> -# AI 聊天功能目前用量已達上限，請稍後再試！"
                        await message.reply(text, mention_author=False)
                        return
                    elif ai_reply:
                        # 4. 將本輪對話寫入多輪對話歷史快取中心
                        conversation_cache.add_user_message(message.channel.id, user_prompt, author_name)
                        conversation_cache.add_assistant_message(message.channel.id, ai_reply)

                        self.channel_last_bot_messages[message.channel.id] = {
                            "user_prompt": user_prompt,
                            "bot_content": ai_reply,
                            "timestamp": time.time()
                        }
                        logger.info(f"💬 [小裁雨 AI ({provider_used})] 於 {guild_name} ({channel_name}) 回應 {author_name}: {ai_reply}")
                        disclaimer = "\n> -# AI 可能會出錯，氣象資料應以氣象署為準。"
                        await message.reply(f"{ai_reply}{disclaimer}", mention_author=False)
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
            await message.reply(text, mention_author=False)
        except discord.HTTPException:
            pass

async def setup(bot):
    await bot.add_cog(MentionCog(bot))
