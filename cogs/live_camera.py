import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import logging
import json
import math
import io
from datetime import datetime, timezone, timedelta
from modules.town_mapping import load_town_mapping
from modules.location_matcher import match_location, get_town_autocomplete, DEFAULT_TOWN_MAPPING

logger = logging.getLogger(__name__)

def haversine_dist(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

async def extract_jpeg_from_mjpeg(url: str, session: aiohttp.ClientSession) -> discord.File | None:
    try:
        async with session.get(url, ssl=False, timeout=10) as resp:
            content_type = resp.headers.get('Content-Type', '')
            if 'image/jpeg' in content_type.lower():
                data = await resp.read()
                return discord.File(io.BytesIO(data), filename="camera.jpg")
                
            chunk = b''
            for _ in range(10): # read up to ~640KB
                c = await resp.content.read(65536)
                if not c:
                    break
                chunk += c
                start = chunk.find(b'\xff\xd8')
                end = chunk.find(b'\xff\xd9', start)
                if start != -1 and end != -1:
                    img_bytes = chunk[start:end+2]
                    return discord.File(io.BytesIO(img_bytes), filename="camera.jpg")
    except Exception as e:
        logger.error(f"提取 MJPEG 失敗 {url}: {e}")
    return None

class CameraView(discord.ui.View):
    def __init__(self, matches, author_id, location, messages):
        super().__init__(timeout=180)
        self.matches = matches
        self.author_id = author_id
        self.location = location
        self.messages = messages
        self.current_idx = 'overview'
        self.current_page = 0
        self.total_pages = math.ceil(len(self.matches) / 24) if self.matches else 1
        
        self.page_select = discord.ui.Select(placeholder="請選擇頁數", row=0)
        self.page_select.callback = self.page_select_callback
        
        self.select = discord.ui.Select(placeholder="請選擇要查看的攝影機", row=1)
        self.select.callback = self.select_callback
        
        self.prev_btn = discord.ui.Button(emoji="⬅️", style=discord.ButtonStyle.primary, row=2)
        self.prev_btn.callback = self.prev_page
        
        self.page_indicator = discord.ui.Button(label="", style=discord.ButtonStyle.secondary, disabled=True, row=2)
        
        self.next_btn = discord.ui.Button(emoji="➡️", style=discord.ButtonStyle.primary, row=2)
        self.next_btn.callback = self.next_page
        
        self.update_components()

    async def page_select_callback(self, interaction: discord.Interaction):
        self.current_page = int(self.page_select.values[0])
        self.current_idx = 'overview'
        await self._update_message(interaction)

    def update_components(self):
        self.clear_items()
        
        if self.total_pages > 1:
            page_options = []
            for p in range(self.total_pages):
                start = p * 24 + 1
                end = min((p + 1) * 24, len(self.matches))
                page_options.append(discord.SelectOption(label=f"第 {p+1} 頁 ({start}-{end})", value=str(p), default=(p == self.current_page)))
            self.page_select.options = page_options
            self.add_item(self.page_select)
            
        cam_options = [discord.SelectOption(label=f"概覽 (第 {self.current_page + 1} 頁前4筆)", value="overview", default=(self.current_idx == 'overview'))]
        start_idx = self.current_page * 24
        end_idx = min(start_idx + 24, len(self.matches))
        for i in range(start_idx, end_idx):
            m = self.matches[i]
            label = f"{m['name']}"[:100]
            cam_options.append(discord.SelectOption(label=label, value=str(i), default=(self.current_idx != 'overview' and self.current_idx == i)))
        self.select.options = cam_options
        self.add_item(self.select)
        
        curr_val = start_idx - 1 if self.current_idx == 'overview' else self.current_idx
        self.prev_btn.disabled = (curr_val < start_idx)
        self.next_btn.disabled = (curr_val >= end_idx - 1)
        
        if self.current_idx == 'overview':
            self.page_indicator.label = f"概覽 (第 {self.current_page + 1} 頁)"
        else:
            local_idx = self.current_idx - start_idx
            self.page_indicator.label = f"{local_idx + 1} / {end_idx - start_idx}"
        
        self.add_item(self.prev_btn)
        self.add_item(self.page_indicator)
        self.add_item(self.next_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    async def _update_message(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.update_components()
        
        header_desc = f"🔍 找到 {len(self.matches)} 筆符合「{self.location}」的攝影機。\n(請從下方選單或按鈕切換其他攝影機)\n\n"
        if self.messages:
            header_desc = f"⚠️ {' | '.join(self.messages)}\n\n" + header_desc

        if self.current_idx == 'overview':
            embeds = []
            attachments = []
            cog = interaction.client.get_cog("LiveCameraCog")
            start_idx = self.current_page * 24
            for idx, m in enumerate(self.matches[start_idx:start_idx+4]):
                if m['source'] == 'moenv':
                    embed = await cog.get_site_embed(m['site_name'])
                    if embed:
                        embed.url = "https://github.com/Nanporo/Saiu-Bot"
                        embed.description = (header_desc if idx == 0 else "") + (embed.description or "")
                        embeds.append(embed)
                else:
                    sel_name = m['name']
                    sel_url = m['video_url']
                    embed = discord.Embed(title=f"twipcam 監視器影像 - {sel_name}", color=0x2ecc71, url="https://github.com/Nanporo/Saiu-Bot")
                    file = await extract_jpeg_from_mjpeg(sel_url, interaction.client.session)
                    if file:
                        file.filename = f"camera_{idx}.jpg"
                        embed.set_image(url=f"attachment://{file.filename}")
                        attachments.append(file)
                    else:
                        embed.set_image(url=sel_url)
                    desc = f"[點此觀看完整動態影像]({sel_url})"
                    embed.description = (header_desc if idx == 0 else "") + desc
                    if m['source'] == 'twipcam':
                        embed.set_footer(text="台灣即時影像監視器 (twipcam)")
                    embeds.append(embed)
            
            await interaction.edit_original_response(content="📷 即時影像", embeds=embeds, view=self, attachments=attachments)
            return

        m = self.matches[self.current_idx]
        if m['source'] == 'moenv':
            cog = interaction.client.get_cog("LiveCameraCog")
            embed = await cog.get_site_embed(m['site_name'])
            if embed:
                embed.description = header_desc + (embed.description or "")
                await interaction.edit_original_response(content="📷 即時影像", embeds=[embed], view=self, attachments=[])
            else:
                await interaction.followup.send(content="❌ 無法取得影像。", ephemeral=True)
        else:
            sel_name = m['name']
            sel_url = m['video_url']
            source_title = "twipcam 監視器影像"
            color = 0x2ecc71
            
            embed = discord.Embed(title=f"{source_title} - {sel_name}", color=color)
            file = await extract_jpeg_from_mjpeg(sel_url, interaction.client.session)
            if file:
                embed.set_image(url="attachment://camera.jpg")
            else:
                embed.set_image(url=sel_url)
                
            desc = f"[點此觀看完整動態影像]({sel_url})"
            embed.description = header_desc + desc
            if m['source'] == 'twipcam':
                embed.set_footer(text="台灣即時影像監視器 (twipcam)")
            
            if file:
                await interaction.edit_original_response(content="📷 即時影像", embeds=[embed], view=self, attachments=[file])
            else:
                await interaction.edit_original_response(content="📷 即時影像", embeds=[embed], view=self, attachments=[])

    async def select_callback(self, interaction: discord.Interaction):
        val = self.select.values[0]
        self.current_idx = 'overview' if val == 'overview' else int(val)
        await self._update_message(interaction)

    async def prev_page(self, interaction: discord.Interaction):
        start_idx = self.current_page * 24
        if self.current_idx != 'overview':
            if self.current_idx == start_idx:
                self.current_idx = 'overview'
            else:
                self.current_idx -= 1
        await self._update_message(interaction)

    async def next_page(self, interaction: discord.Interaction):
        start_idx = self.current_page * 24
        end_idx = min(start_idx + 24, len(self.matches))
        if self.current_idx == 'overview':
            if end_idx > start_idx:
                self.current_idx = start_idx
        elif self.current_idx < end_idx - 1:
            self.current_idx += 1
        await self._update_message(interaction)

class LiveCameraCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="即時影像", description="📷 獲取即時影像與測站資訊 Live Cameras")
    @app_commands.describe(
        location="輸入要查詢的鄉鎮市區或測站名稱 (例如：平鎮)",
        source="選擇影像來源 (未選擇時預設搜尋所有來源)",
        road="輸入要篩選的路段名稱 (例如：中山路) [可選]"
    )
    @app_commands.choices(source=[
        app_commands.Choice(name="環境部空氣品質測站", value="moenv_airtw"),
        app_commands.Choice(name="台灣即時影像監視器 (twipcam)", value="twipcam"),
    ])
    async def live_camera(self, interaction: discord.Interaction, location: str, source: app_commands.Choice[str] = None, road: str = None):
        loc_val, error_msg = match_location(location)
        if error_msg:
            # 不阻擋查詢，將 loc_val 設回原本輸入（處理了台臺轉換）
            loc_val = location.replace("台", "臺")
            
        await interaction.response.defer()
        
        query = location.replace("台", "臺")
        source_val = source.value if source else "all"
        
        all_matches = []
        messages = []
        
        if source_val in ["moenv_airtw", "all"]:
            m, msg = await self._get_moenv_matches(query, loc_val, road=road, allow_fallback=(source_val != "all"))
            all_matches.extend(m)
            if msg: messages.append(msg)
            
        if source_val in ["twipcam", "all"]:
            m, msg = await self._get_twipcam_matches(query, loc_val, road=road)
            all_matches.extend(m)
            if msg: messages.append(msg)
            
        if not all_matches:
            await interaction.followup.send(content=f"❌ 找不到包含「{location}」的任何監視器或測站影像，且無法定位該鄉鎮市區。")
            return
            
        all_matches = all_matches[:600]
        
        if len(all_matches) > 1:
            view = CameraView(all_matches, interaction.user.id, location, messages)
            
            header_desc = f"🔍 找到 {len(all_matches)} 筆符合「{location}」的攝影機。\n(請從下方選單或按鈕切換其他攝影機)\n\n"
            if messages:
                header_desc = f"⚠️ {' | '.join(messages)}\n\n" + header_desc
                
            embeds = []
            attachments = []
            for idx, m in enumerate(all_matches[:4]):
                if m['source'] == 'moenv':
                    embed = await self.get_site_embed(m['site_name'])
                    if embed:
                        embed.url = "https://github.com/Nanporo/Saiu-Bot"
                        embed.description = (header_desc if idx == 0 else "") + (embed.description or "")
                        embeds.append(embed)
                else:
                    sel_name = m['name']
                    sel_url = m['video_url']
                    embed = discord.Embed(title=f"twipcam 監視器影像 - {sel_name}", color=0x2ecc71, url="https://github.com/Nanporo/Saiu-Bot")
                    file = await extract_jpeg_from_mjpeg(sel_url, self.bot.session)
                    if file:
                        file.filename = f"camera_{idx}.jpg"
                        embed.set_image(url=f"attachment://{file.filename}")
                        attachments.append(file)
                    else:
                        embed.set_image(url=sel_url)
                    desc = f"[點此觀看完整動態影像]({sel_url})"
                    embed.description = (header_desc if idx == 0 else "") + desc
                    if m['source'] == 'twipcam':
                        embed.set_footer(text="台灣即時影像監視器 (twipcam)")
                    embeds.append(embed)
                    
            await interaction.followup.send(content="📷 即時影像", embeds=embeds, view=view, files=attachments)
        else:
            m = all_matches[0]
            header_desc = ""
            if messages:
                header_desc = f"⚠️ {' | '.join(messages)}\n\n"

            if m['source'] == 'moenv':
                embed = await self.get_site_embed(m['site_name'])
                if embed:
                    embed.description = header_desc + (embed.description or "")
                    await interaction.followup.send(content="📷 即時影像", embed=embed)
                else:
                    await interaction.followup.send(content="❌ 無法取得影像。")
            else:
                sel_name = m['name']
                sel_url = m['video_url']
                embed = discord.Embed(title=f"twipcam 監視器影像 - {sel_name}", color=0x2ecc71)
                
                file = await extract_jpeg_from_mjpeg(sel_url, self.bot.session)
                if file:
                    embed.set_image(url="attachment://camera.jpg")
                else:
                    embed.set_image(url=sel_url)
                    
                desc = f"[點此觀看完整動態影像]({sel_url})"
                embed.description = header_desc + desc
                if m['source'] == 'twipcam':
                    embed.set_footer(text="台灣即時影像監視器 (twipcam)")
                
                kwargs = {'content': "📷 即時影像", 'embed': embed}
                if file: kwargs['file'] = file
                
                await interaction.followup.send(**kwargs)

    @live_camera.autocomplete('location')
    async def location_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return [app_commands.Choice(name=t, value=t) for t in get_town_autocomplete(current)]

    async def get_site_embed(self, site_name: str) -> discord.Embed:
        url = "https://airtw.moenv.gov.tw/ajax.aspx"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
        }
        data = {"Target": "SitePhoto_24h", "SiteName": site_name}
        try:
            async with self.bot.session.post(url, data=data, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    info = json.loads(text)
                    pics = info.get("Site_Pic", "").split(",")
                    if pics and pics[0]:
                        pic_url = pics[0].replace("../../../", "https://airtw.moenv.gov.tw/")
                        embed = discord.Embed(title=f"環境部空氣品質測站影像 - {site_name}", color=0x3498db)
                        embed.set_image(url=pic_url)
                        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
                        embed.set_footer(text=f"環境部 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/moenv_logo.png")
                        return embed
        except Exception as e:
            logger.error(f"取得測站 {site_name} 影像失敗: {e}")
        return None

    async def _get_moenv_matches(self, query: str, loc_val: str, road: str = None, allow_fallback: bool = True):
        url = "https://airtw.moenv.gov.tw/ajax.aspx"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
        }
        
        try:
            async with self.bot.session.post(url, data={"Target": "SitePhoto_Site"}, headers=headers, timeout=15) as resp:
                if resp.status != 200: return [], None
                text = await resp.text()
                try:
                    sites = json.loads(text)
                except: return [], None
                    
                matched_sites = []
                for s in sites:
                    name = s["Name"]
                    if query in name or name in query:
                        if road and road not in name:
                            continue
                            
                        # 若環境部測站名稱剛好是縣市名 (例如「臺南」)，則只有當 loc_val 等於該縣市的預設鄉鎮時才配對
                        # 避免搜尋「臺南市安南區」時，因為「臺南」包含在搜尋詞內而誤配對到「臺南」測站
                        if name in DEFAULT_TOWN_MAPPING:
                            if loc_val != DEFAULT_TOWN_MAPPING[name]:
                                continue
                                
                        matched_sites.append(name)
                        
                location_msg = None
                
                if not matched_sites and allow_fallback:
                    mapping = load_town_mapping()
                    target_loc_info = mapping.get(loc_val)
                    target_lat = target_lon = None
                    if target_loc_info:
                        for item in target_loc_info:
                            fullname, lat, lon = item[0], item[1], item[2]
                            if lat and lon:
                                target_lat, target_lon = lat, lon
                                break
                                
                    if target_lat and target_lon:
                        min_dist = float('inf')
                        closest_site = None
                        for s in sites:
                            site_name = s["Name"]
                            if road and road not in site_name:
                                continue
                            site_info = mapping.get(site_name)
                            if site_info:
                                for item in site_info:
                                    fullname, lat, lon = item[0], item[1], item[2]
                                    if lat and lon:
                                        dist = haversine_dist(target_lat, target_lon, lat, lon)
                                        if dist < min_dist:
                                            min_dist = dist
                                            closest_site = site_name
                                        break
                        if closest_site:
                            matched_sites = [closest_site]
                            location_msg = f"[環境部] 為您顯示距離最近測站約 {min_dist:.1f}km"
                            
                results = []
                for s in matched_sites:
                    results.append({'source': 'moenv', 'name': s, 'site_name': s})
                return results, location_msg
        except Exception as e:
            logger.error(f"環境部影像錯誤: {e}")
            return [], None


    async def _get_twipcam_matches(self, query: str, loc_val: str, road: str = None):
        url = "https://www.twipcam.com/api/v1/cam-list.json"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with self.bot.session.get(url, headers=headers, timeout=15) as resp:
                if resp.status != 200: return [], None
                cctvs = await resp.json()
                
                COUNTIES = ["基隆", "臺北", "新北", "桃園", "新竹", "苗栗", "臺中", "彰化", "南投", "雲林", "嘉義", "臺南", "高雄", "屏東", "宜蘭", "花蓮", "臺東", "澎湖", "金門", "連江", "馬祖"]
                
                # 從 loc_val 提取縣市，因為 loc_val 可能是經過 match_location 解析後的全名（如「臺南市安南區」）
                user_county = next((c for c in COUNTIES if c in loc_val), None)
                
                search_term = query
                if user_county:
                    # 嘗試把縣市名稱從 query 中拔除，讓搜尋更寬鬆（例如 query = "台南安南" -> search_term = "安南"）
                    temp = search_term.replace(user_county + "市", "").replace(user_county + "縣", "").replace(user_county, "").strip()
                    if temp:
                        search_term = temp
                
                matched = []
                for c in cctvs:
                    name = c.get('name', '')
                    name_clean = name.replace("台", "臺")
                    
                    if search_term in name or name in search_term:
                        if road and road not in name:
                            continue
                        
                        if user_county:
                            # 檢查是否包含其他縣市名稱但沒有包含目標縣市
                            conflict = [cty for cty in COUNTIES if cty != user_county and cty in name_clean]
                            if conflict and user_county not in name_clean:
                                continue
                                
                        matched.append((name, c.get("cam_url")))
                            
                results = []
                for name, url_cam in matched:
                    results.append({'source': 'twipcam', 'name': name, 'video_url': url_cam})
                return results, None
        except Exception as e:
            logger.error(f"twipcam影像錯誤: {e}")
            return [], None



async def setup(bot):
    await bot.add_cog(LiveCameraCog(bot))
