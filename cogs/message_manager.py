import discord
from discord.ext import commands
from discord import app_commands
import logging

logger = logging.getLogger(__name__)

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
        self.bot.tree.add_command(self.ctx_menu_bookmark)
        self.bot.tree.add_command(self.ctx_menu_refresh)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.ctx_menu_delete.name, type=self.ctx_menu_delete.type)
        self.bot.tree.remove_command(self.ctx_menu_weather.name, type=self.ctx_menu_weather.type)
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

    async def delete_message(self, interaction: discord.Interaction, message: discord.Message):
        # 檢查是否為機器人的訊息
        if message.author.id != self.bot.user.id:
            await interaction.response.send_message("❌ 這不是我的訊息，我無法刪除。", ephemeral=True)
            return

        # 嘗試找出觸發該訊息的用戶
        target_user_id = None

        if hasattr(message, "interaction_metadata") and message.interaction_metadata:
            target_user_id = message.interaction_metadata.user.id
        elif message.interaction:
            target_user_id = message.interaction.user.id
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
        if hasattr(message, "interaction_metadata") and message.interaction_metadata:
            target_user_id = message.interaction_metadata.user.id
            cmd_name = message.interaction_metadata.name
        elif message.interaction:
            target_user_id = message.interaction.user.id
            cmd_name = message.interaction.name
            
        if not target_user_id:
            await interaction.response.send_message("❌ 無法確定此訊息的原呼叫者，或它不是由斜線指令產生。", ephemeral=True)
            return
            
        if target_user_id != interaction.user.id:
            await interaction.response.send_message("❌ 只有此指令的原呼叫者才能重新整理資料。", ephemeral=True)
            return

        # 根據指令名稱進行路由更新
        command_cog_map = {
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
            "淹水查詢": "FloodManualCog",
            "閃電": "LightningCog",
            "天文資訊": "AstronomyCog",
            "太空天氣": "IonosphereCog",
            "機場天氣": "AirportCog",
            "附近飛機": "AdsbCog",
            "今日氣象記錄": "TodayRecordCog",
            "台電發電": "TaipowerCog"
        }
        
        cog_name = command_cog_map.get(cmd_name)
        if cog_name:
            cog = self.bot.get_cog(cog_name)
            if cog and hasattr(cog, "refresh_message"):
                class MessageWrapper:
                    def __init__(self, msg):
                        self._msg = msg
                        
                    def __getattr__(self, name):
                        return getattr(self._msg, name)
                        
                    async def edit(self, **kwargs):
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
