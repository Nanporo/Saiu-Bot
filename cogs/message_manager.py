import discord
from discord.ext import commands
from discord import app_commands
import logging
import unicodedata

logger = logging.getLogger(__name__)

MAX_MESSAGE_CONVERSIONS = 4


# 可透過右鍵「重新整理資料」更新的手動指令。路由與識別規則放在同一處，
# 避免新增指令時只更新其中一邊。
REFRESH_COMMAND_COGS = {
    "雷達回波": "RadarCog",
    "衛星雲圖": "SatelliteCog",
    "現在天氣": "NowWeatherCog",
    "查詢此地天氣": "NowWeatherCog",
    "空氣品質": "AqiCog",
    "雨量排行": "RainfallCog",
    "氣溫排行": "TempCog",
    "風力排行": "WindCog",
    "地震列表": "EarthquakeListCog",
    "空氣品質排行": "ListAqiCog",
    "相對濕度排行": "RelativeHumidityCog",
    "氣壓排行": "AirPressureCog",
    "天氣預報": "WeatherCog",
    "降雨預警": "RainManualCog",
    "定量降水預報": "QPFCog",
    "颱風動態": "TyphoonCog",
    "閃電": "LightningCog",
    "天文資訊": "AstronomyCog",
    "太空天氣": "IonosphereCog",
    "機場天氣": "AirportCog",
    "附近飛機": "AdsbCog",
    "今日氣象記錄": "TodayRecordCog",
    "台電發電": "TaipowerCog",
    "氣象新聞": "NewsCog",
    "交通狀況": "TrafficCog",
}


# 舊訊息或 Discord 沒有回傳原始指令名時才使用。每組都包含該指令各個
# 頁籤/模式可能出現的實際文案，特定的指令必須排在寬鬆的指令之前。
REFRESH_INFERENCE_KEYWORDS = (
    ("空氣品質排行", ("空氣品質排行", "全台空氣品質", "AQI排行", "AQI 排行", "最高 AQI", "最低 AQI")),
    ("空氣品質", ("空氣品質", "AQI")),
    ("雨量排行", ("雨量排行", "累積雨量測站排行")),
    ("氣溫排行", ("氣溫排行", "測站氣溫排行", "現在高溫排行", "現在低溫排行", "今日最高溫測站排行", "今日最低溫測站排行")),
    ("風力排行", ("風力排行", "風速排行", "平均風排行", "陣風排行")),
    ("相對濕度排行", ("相對濕度排行", "濕度排行")),
    ("氣壓排行", ("氣壓排行",)),
    ("地震列表", ("地震列表", "地震報告列表", "最新 10 筆地震")),
    ("定量降水預報", ("定量降水預報", "QPF")),
    ("今日氣象記錄", ("今日氣象記錄", "今日氣象觀測之最", "氣象記錄看板")),
    ("雷達回波", ("雷達回波",)),
    ("衛星雲圖", ("衛星雲圖",)),
    ("天氣預報", ("天氣預報", "逐3小時天氣預報", "鄉鎮天氣預報")),
    ("降雨預警", ("降雨預警", "降雨預報")),
    ("颱風動態", ("颱風動態", "颱風概覽", "暴風圈侵襲機率", "海水表面溫度", "海洋熱潛勢")),
    ("太空天氣", ("太空天氣", "電離層電波吸收", "全球地磁擾動指數")),
    ("機場天氣", ("機場天氣", "METAR")),
    ("附近飛機", ("附近飛機", "ADS-B")),
    ("台電發電", ("台電發電", "台灣即時發電量", "即時發電量")),
    ("氣象新聞", ("氣象新聞", "氣象與災防新聞")),
    ("交通狀況", ("交通狀況", "軌道交通")),
    ("天文資訊", ("天文資訊", "天文資訊查詢", "潮汐資訊")),
    ("閃電", ("閃電", "雷擊觀測")),
    ("現在天氣", ("現在天氣", "目前天氣觀測", "即時天氣觀測")),
)


def _component_text(components):
    """擷取傳統 Components 與 Components v2 的可見文字。"""
    parts = []
    for component in components or ():
        if component is None:
            continue
        if isinstance(component, str):
            parts.append(component)
            continue
        for attr in ("content", "label", "placeholder", "description"):
            value = getattr(component, attr, None)
            if value:
                parts.append(str(value))
        for attr in ("children", "components", "items", "options"):
            children = getattr(component, attr, None)
            if children:
                parts.append(_component_text(children))
    return " ".join(parts)


def _message_search_text(message):
    parts = [getattr(message, "content", None) or ""]
    for embed in getattr(message, "embeds", ()) or ():
        parts.extend((
            getattr(embed, "title", None) or "",
            getattr(embed, "description", None) or "",
            getattr(getattr(embed, "author", None), "name", None) or "",
            getattr(getattr(embed, "footer", None), "text", None) or "",
        ))
        for field in getattr(embed, "fields", ()) or ():
            parts.extend((getattr(field, "name", None) or "", getattr(field, "value", None) or ""))
    parts.append(_component_text(getattr(message, "components", ())))
    return " ".join(str(part) for part in parts if part)


def infer_refresh_command(message):
    """優先使用 Discord 原始 interaction 名稱，無法取得時才以訊息文案推斷。"""
    metadata = getattr(message, "interaction_metadata", None)
    missing = object()
    legacy_interaction = getattr(message, "_interaction", missing)
    if legacy_interaction is missing:
        # 方便測試替身及舊版 discord.py；新版 Message 會先命中 _interaction。
        legacy_interaction = getattr(message, "interaction", None)

    for source in (metadata, legacy_interaction):
        command_name = getattr(source, "name", None)
        if command_name:
            command_name = str(command_name).lstrip("/").split()[0]
            if command_name in REFRESH_COMMAND_COGS:
                return command_name

    # 不讓大小寫、全半形或排版用空白造成偶發性匹配失敗。
    search_text = "".join(
        unicodedata.normalize("NFKC", _message_search_text(message)).upper().split()
    )
    for command_name, keywords in REFRESH_INFERENCE_KEYWORDS:
        if any(
            "".join(unicodedata.normalize("NFKC", keyword).upper().split()) in search_text
            for keyword in keywords
        ):
            return command_name
    return None


class MessageManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ctx_menu_delete = app_commands.ContextMenu(
            name='刪除訊息',
            callback=self.delete_message,
        )
        self.ctx_menu_weather = app_commands.ContextMenu(
            name='查詢此地天氣',
            callback=self.check_weather_menu,
        )
        self.ctx_menu_convert = app_commands.ContextMenu(
            name='換算訊息數值',
            callback=self.convert_message_values,
        )
        self.ctx_menu_bookmark = app_commands.ContextMenu(
            name='收藏此訊息',
            callback=self.bookmark_message,
        )
        self.ctx_menu_refresh = app_commands.ContextMenu(
            name='重新整理資料',
            callback=self.refresh_data,
        )
        self.bot.tree.add_command(self.ctx_menu_delete)
        self.bot.tree.add_command(self.ctx_menu_weather)
        self.bot.tree.add_command(self.ctx_menu_convert)
        self.bot.tree.add_command(self.ctx_menu_bookmark)
        self.bot.tree.add_command(self.ctx_menu_refresh)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.ctx_menu_delete.name, type=self.ctx_menu_delete.type)
        self.bot.tree.remove_command(self.ctx_menu_weather.name, type=self.ctx_menu_weather.type)
        self.bot.tree.remove_command(self.ctx_menu_convert.name, type=self.ctx_menu_convert.type)
        self.bot.tree.remove_command(self.ctx_menu_bookmark.name, type=self.ctx_menu_bookmark.type)
        self.bot.tree.remove_command(self.ctx_menu_refresh.name, type=self.ctx_menu_refresh.type)

    async def check_weather_menu(self, interaction: discord.Interaction, message: discord.Message):
        text = message.content
        if not text:
            # 嘗試檢查 embed 內的文字
            if message.embeds:
                text = " ".join([
                    (e.title or "") + " " + (e.description or "") 
                    for e in message.embeds
                ])
            
        if not text:
            await interaction.response.send_message("❌ 此訊息沒有文字內容可以分析。", ephemeral=True)
            return

        from modules.location_matcher import town_mapping_cache, DEFAULT_TOWN_MAPPING
        
        # 將地名清單按長度由長到短排序，優先比對長地名 (避免短名稱誤判)
        keys = list(town_mapping_cache.keys()) + list(DEFAULT_TOWN_MAPPING.keys())
        keys = list(set(keys)) # 移除重複
        keys.sort(key=len, reverse=True)
        
        # 額外將所有台轉換為臺，以利匹配
        text_for_search = text.replace("台", "臺")
        found_locs = []
        
        for key in keys:
            search_key = key.replace("台", "臺")
            if search_key in text_for_search:
                found_locs.append(key)
                # 從字串中移除已經找到的地名，避免「台北市信義區」又被「台北」重複配對
                text_for_search = text_for_search.replace(search_key, "")
                
        if not found_locs:
            await interaction.response.send_message("❌ 在這則訊息中找不到可辨識的台灣鄉鎮市區或縣市名稱。", ephemeral=True)
            return
            
        logger.info(f"🔍 [查詢此地天氣] 成功提取出地名: {found_locs[0]}")

        if len(found_locs) > 1:
            locs_str = "、".join(found_locs)
            await interaction.response.send_message(f"❌ 這則訊息中包含多個地點 ({locs_str})，請選擇只有一個地點的訊息來查詢。", ephemeral=True)
            return
            
        found_loc = found_locs[0]
            
        now_weather_cog = self.bot.get_cog("NowWeatherCog")
        if not now_weather_cog:
            await interaction.response.send_message("❌ 找不到天氣模組，無法查詢。", ephemeral=True)
            return
            
        # 直接呼叫現有的天氣指令回呼函數，並將地點帶入
        await now_weather_cog.now_weather_command.callback(now_weather_cog, interaction, found_loc)

    async def convert_message_values(self, interaction: discord.Interaction, message: discord.Message):
        from cogs.convert import ConversionError, build_conversion, extract_conversions

        text = _message_search_text(message)
        if not text.strip():
            await interaction.response.send_message("❌ 此訊息沒有文字內容可以換算。", ephemeral=True)
            return

        conversions = extract_conversions(text)
        if not conversions:
            await interaction.response.send_message(
                "❌ 找不到可換算的數值與單位。支援風速、溫度與氣壓單位，例如 `170 kts`。",
                ephemeral=True,
            )
            return

        valid_sections = []
        for start, end, unit in conversions:
            try:
                title, description = build_conversion(start, unit, end)
                valid_sections.append(f"**{title}**\n{description}")
            except ConversionError:
                # 右鍵換算只顯示合理的數值；不可能的值不佔 4 組名額。
                continue

        if not valid_sections:
            await interaction.response.send_message(
                "❌ 這則訊息中沒有可合理換算的數值。",
                ephemeral=True,
            )
            return

        sections = valid_sections[:MAX_MESSAGE_CONVERSIONS]
        if len(valid_sections) > MAX_MESSAGE_CONVERSIONS:
            omitted_count = len(valid_sections) - MAX_MESSAGE_CONVERSIONS
            sections.append(f"-# 每則訊息最多換算 4 組，剩餘 {omitted_count} 組請使用 `/換算` 指令查詢。")
        embed = discord.Embed(description="\n\n".join(sections), color=0x41809b)
        await interaction.response.send_message(embed=embed)

    async def delete_message(self, interaction: discord.Interaction, message: discord.Message):
        # 檢查是否為機器人的訊息
        if message.author.id != self.bot.user.id:
            await interaction.response.send_message("❌ 這不是我的訊息，我無法刪除。", ephemeral=True)
            return

        # 嘗試找出觸發該訊息的用戶
        target_user_id = None
        metadata = getattr(message, "interaction_metadata", None)

        if metadata and getattr(metadata, "user", None):
            target_user_id = metadata.user.id
        elif message.reference and message.reference.cached_message:
            target_user_id = message.reference.cached_message.author.id

        if target_user_id is None:
            await interaction.response.send_message("❌ 無法確定此訊息的呼叫者，無法刪除。", ephemeral=True)
            return

        # 檢查指令呼叫者是否為該訊息的擁有者
        if interaction.user.id == target_user_id:
            try:
                await message.delete()
                await interaction.response.send_message("✅ 訊息已刪除。", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("❌ 我沒有權限刪除該訊息。", ephemeral=True)
            except discord.HTTPException:
                await interaction.response.send_message("❌ 刪除訊息時發生錯誤。", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 您不是這個指令的呼叫者，無法刪除此訊息。", ephemeral=True)

    async def bookmark_message(self, interaction: discord.Interaction, message: discord.Message):
        if message.author.id != self.bot.user.id:
            await interaction.response.send_message("❌ 只能收藏由小裁雨發送的訊息。", ephemeral=True)
            return
            
        try:
            content = f"**📌 來自 {message.channel.mention} 的收藏訊息：**\n\n{message.content}"
            embeds = message.embeds
            
            attachment_links = []
            for a in message.attachments:
                attachment_links.append(f"[{a.filename}]({a.url})")
            
            if attachment_links:
                content += "\n\n**附件連結：**\n" + "\n".join(attachment_links)
                
            await interaction.user.send(content=content, embeds=embeds)
            await interaction.response.send_message("✅ 已將此訊息私訊給您！", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ 無法私訊給您，請檢查「隱私設定」是否已開啟「允許來自伺服器成員的私人訊息」。", ephemeral=True)

    async def refresh_data(self, interaction: discord.Interaction, message: discord.Message):
        import datetime
        now = discord.utils.utcnow()
        if now - message.created_at > datetime.timedelta(hours=24):
            await interaction.response.send_message("❌ 只能重新整理 24 小時內的資料，避免歷史資料混亂。", ephemeral=True)
            return
            
        if message.author.id != self.bot.user.id:
            await interaction.response.send_message("❌ 這不是我的訊息，無法重新整理。", ephemeral=True)
            return
            
        target_user_id = None
        cmd_name = None
        metadata = getattr(message, "interaction_metadata", None)

        if metadata and getattr(metadata, "user", None):
            target_user_id = metadata.user.id

        if not target_user_id and message.reference and message.reference.cached_message:
            target_user_id = message.reference.cached_message.author.id

        if not target_user_id:
            await interaction.response.send_message("❌ 無法確定此訊息的原呼叫者，或它不是由斜線指令產生。", ephemeral=True)
            return

        if target_user_id != interaction.user.id:
            await interaction.response.send_message("❌ 只有此指令的原呼叫者才能重新整理資料。", ephemeral=True)
            return

        if not cmd_name:
            cmd_name = infer_refresh_command(message)

        if not cmd_name:
            await interaction.response.send_message("❌ 無法識別此訊息對應的指令，無法重新整理。", ephemeral=True)
            return

        # 根據指令名稱進行路由更新
        cog_name = REFRESH_COMMAND_COGS.get(cmd_name)
        if cog_name:
            cog = self.bot.get_cog(cog_name)
            if cog and hasattr(cog, "refresh_message"):
                class MessageWrapper:
                    def __init__(self, msg):
                        self._msg = msg
                        
                    def __getattr__(self, name):
                        return getattr(self._msg, name)
                        
                    async def edit(self, **kwargs):
                        # Components v2 不能與傳統 content/embed 混用，也不加上重新整理後綴。
                        if isinstance(kwargs.get("view"), discord.ui.LayoutView):
                            kwargs["content"] = None
                            kwargs["embed"] = None
                            return await self._msg.edit(**kwargs)

                        if getattr(self._msg.flags, "components_v2", False):
                            kwargs.pop("content", None)
                            return await self._msg.edit(**kwargs)

                        suffix = "\n（已透過指令重新整理）"
                        if 'content' in kwargs:
                            c = kwargs['content']
                            if c is not None:
                                if not str(c).endswith(suffix):
                                    kwargs['content'] = str(c) + suffix
                            else:
                                kwargs['content'] = suffix.strip()
                        else:
                            c = self._msg.content or ""
                            if not c.endswith(suffix):
                                kwargs['content'] = c + suffix
                        return await self._msg.edit(**kwargs)

                wrapped_message = MessageWrapper(message)
                await cog.refresh_message(interaction, wrapped_message, cmd_name)
                return
            else:
                await interaction.response.send_message("❌ 找不到模組或該模組不支援重新整理。", ephemeral=True)
                return

        await interaction.response.send_message(f"❌ 「{cmd_name}」這個指令目前不支援重新整理喔！", ephemeral=True)

async def setup(bot):
    await bot.add_cog(MessageManager(bot))
