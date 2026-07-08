import discord
from discord.ext import commands
from discord import app_commands

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
            
        logger.info(f"🔍 [查詢此地天氣] 成功萃取出地名: {found_locs[0]}")

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
            await interaction.response.send_message("這不是我的訊息，我無法刪除。", ephemeral=True)
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
            await interaction.response.send_message("無法確定此訊息的呼叫者，無法刪除。", ephemeral=True)
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
        if cmd_name == "雷達回波":
            radar_cog = self.bot.get_cog("RadarCog")
            if radar_cog:
                await interaction.response.defer(ephemeral=True)
                area = "small"
                is_anim = False
                if message.embeds:
                    desc = message.embeds[0].description or ""
                    if "動態" in desc: is_anim = True
                    name_map = {"台灣海域": "large", "台灣本島": "small", "樹林": "shulin", "南屯": "nantun", "林園": "linyuan"}
                    for k, v in name_map.items():
                        if k in desc:
                            area = v
                            break
                from cogs.radar import RadarView
                view = RadarView(self.bot, interaction.user.id, area)
                if is_anim:
                    file, embed = await view.build_animation_embed()
                    for child in view.children:
                        if getattr(child, 'label', '') == "動態圖片": child.label = "靜態圖片"
                    if embed: await message.edit(embed=embed, view=view, attachments=[file] if file else [])
                else:
                    content, embed, file = await view.build_embed()
                    await message.edit(content=content, embed=embed, view=view, attachments=[file] if file else [])
                await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)
            else:
                await interaction.response.send_message("❌ 找不到模組。", ephemeral=True)

        elif cmd_name == "衛星雲圖":
            sat_cog = self.bot.get_cog("SatelliteCog")
            if sat_cog:
                await interaction.response.defer(ephemeral=True)
                curr_type = "EA_TRGB"
                is_anim = False
                if message.embeds:
                    desc = message.embeds[0].description or ""
                    if "動態" in desc: is_anim = True
                    from cogs.satellite import SAT_TYPES
                    for k, v in SAT_TYPES.items():
                        if v['name'] in desc:
                            curr_type = k
                            break
                from cogs.satellite import SatelliteView
                view = SatelliteView(self.bot, interaction.user.id, curr_type)
                if is_anim:
                    file, embed = await view.build_animation_embed()
                    for child in view.children:
                        if getattr(child, 'label', '') == "動態圖片": child.label = "靜態圖片"
                    if embed: await message.edit(embed=embed, view=view, attachments=[file] if file else [])
                else:
                    content, embed, file = await view.build_embed()
                    await message.edit(content=content, embed=embed, view=view, attachments=[file] if file else [])
                await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)
            else:
                await interaction.response.send_message("❌ 找不到模組。", ephemeral=True)
                
        elif cmd_name in ["現在天氣", "查詢此地天氣"]:
            if message.embeds:
                title = (message.embeds[0].description or "") + (message.embeds[0].title or "")
                from modules.location_matcher import town_mapping_cache, DEFAULT_TOWN_MAPPING
                keys = list(town_mapping_cache.keys()) + list(DEFAULT_TOWN_MAPPING.keys())
                keys = list(set(keys))
                keys.sort(key=len, reverse=True)
                found_loc = None
                for key in keys:
                    if key.replace("台", "臺") in title.replace("台", "臺"):
                        found_loc = key
                        break
                
                if found_loc:
                    nw_cog = self.bot.get_cog("NowWeatherCog")
                    if nw_cog:
                        await interaction.response.defer(ephemeral=True)
                        county_name = found_loc[:3]
                        town_name = found_loc[3:]
                        data = await nw_cog.fetch_now_weather()
                        if data:
                            target_stations = [st for st in data.get("records", {}).get("Station", []) if st.get("GeoInfo", {}).get("CountyName") == county_name and st.get("GeoInfo", {}).get("TownName") == town_name]
                            if target_stations:
                                from cogs.now_weather import NowWeatherView
                                view = NowWeatherView(target_stations, county_name, town_name, interaction.user.id)
                                content, embed = view.build_embed(target_stations[0])
                                await message.edit(content=content, embed=embed, view=view if len(target_stations) > 1 else None)
                                await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)
                                return
            await interaction.response.send_message("❌ 無法從這則天氣訊息中萃取出地點以重新查詢。", ephemeral=True)
            
        elif cmd_name == "空氣品質":
            if message.embeds:
                title = (message.embeds[0].title or "") + (message.embeds[0].description or "")
                from modules.location_matcher import town_mapping_cache, DEFAULT_TOWN_MAPPING
                keys = list(town_mapping_cache.keys()) + list(DEFAULT_TOWN_MAPPING.keys())
                keys = list(set(keys))
                keys.sort(key=len, reverse=True)
                found_loc = None
                for key in keys:
                    if key.replace("台", "臺") in title.replace("台", "臺"):
                        found_loc = key
                        break
                if found_loc:
                    aqi_cog = self.bot.get_cog("AqiCog")
                    if aqi_cog:
                        await interaction.response.defer(ephemeral=True)
                        error_msg, embed = await aqi_cog.get_aqi_embed(found_loc)
                        if error_msg:
                            await interaction.followup.send(error_msg, ephemeral=True)
                        else:
                            await message.edit(embed=embed)
                            await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)
                        return
            await interaction.response.send_message("❌ 無法從這則空氣品質訊息中萃取出地點以重新查詢。", ephemeral=True)

        else:
            await interaction.response.send_message(f"❌ 「{cmd_name}」這個指令目前不支援重新整理喔！\n(支援列表：雷達回波、衛星雲圖、現在天氣、空氣品質)", ephemeral=True)

async def setup(bot):
    await bot.add_cog(MessageManager(bot))
