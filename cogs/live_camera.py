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
        self.current_idx = 0
        
        options = []
        for i, m in enumerate(matches[:25]):
            source_tag = "[環境部] " if m['source'] == 'moenv' else "[監視器] "
            label = f"{source_tag}{m['name']}"[:100]
            options.append(discord.SelectOption(label=label, value=str(i)))
            
        self.select = discord.ui.Select(placeholder="請選擇要查看的攝影機", options=options, row=0)
        self.select.callback = self.select_callback
        
        self.prev_btn = discord.ui.Button(emoji="⬅️", style=discord.ButtonStyle.primary, row=1)
        self.prev_btn.callback = self.prev_page
        
        self.page_indicator = discord.ui.Button(label="", style=discord.ButtonStyle.secondary, disabled=True, row=1)
        
        self.next_btn = discord.ui.Button(emoji="➡️", style=discord.ButtonStyle.primary, row=1)
        self.next_btn.callback = self.next_page
        
        self.update_components()

    def update_components(self):
        self.clear_items()
        for i, opt in enumerate(self.select.options):
            opt.default = (i == self.current_idx)
            
        self.prev_btn.disabled = (self.current_idx == 0)
        self.next_btn.disabled = (self.current_idx == len(self.matches) - 1)
        self.page_indicator.label = f"{self.current_idx + 1} / {len(self.matches)}"
        
        self.add_item(self.select)
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
        m = self.matches[self.current_idx]
        
        header_desc = f"🔍 找到 {len(self.matches)} 筆符合「{self.location}」的攝影機，預設顯示第一筆影像。\n(請從下方選單或按鈕切換其他攝影機)\n\n"
        if self.messages:
            header_desc = f"⚠️ {' | '.join(self.messages)}\n\n" + header_desc

        if m['source'] == 'moenv':
            cog = interaction.client.get_cog("LiveCameraCog")
            embed = await cog.get_site_embed(m['site_name'])
            if embed:
                embed.description = header_desc + (embed.description or "")
                await interaction.edit_original_response(content="📷 即時影像", embed=embed, view=self, attachments=[])
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
                await interaction.edit_original_response(content="📷 即時影像", embed=embed, view=self, attachments=[file])
            else:
                await interaction.edit_original_response(content="📷 即時影像", embed=embed, view=self, attachments=[])

    async def select_callback(self, interaction: discord.Interaction):
        self.current_idx = int(self.select.values[0])
        await self._update_message(interaction)

    async def prev_page(self, interaction: discord.Interaction):
        if self.current_idx > 0:
            self.current_idx -= 1
        await self._update_message(interaction)

    async def next_page(self, interaction: discord.Interaction):
        if self.current_idx < len(self.matches) - 1:
            self.current_idx += 1
        await self._update_message(interaction)

class LiveCameraCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="即時影像", description="📷 獲取即時影像與測站資訊")
    @app_commands.describe(
        location="輸入要查詢的鄉鎮市區或測站名稱 (例如：平鎮)",
        source="選擇影像來源 (未選擇時預設搜尋所有來源)"
    )
    @app_commands.choices(source=[
        app_commands.Choice(name="環境部空氣品質測站", value="moenv_airtw"),
        app_commands.Choice(name="台灣即時影像監視器 (twipcam)", value="twipcam"),
    ])
    async def live_camera(self, interaction: discord.Interaction, location: str, source: app_commands.Choice[str] = None):
        await interaction.response.defer()
        
        source_val = source.value if source else "all"
        
        all_matches = []
        messages = []
        
        if source_val in ["moenv_airtw", "all"]:
            m, msg = await self._get_moenv_matches(location, allow_fallback=(source_val != "all"))
            all_matches.extend(m)
            if msg: messages.append(msg)
            
        if source_val in ["twipcam", "all"]:
            m, msg = await self._get_twipcam_matches(location)
            all_matches.extend(m)
            if msg: messages.append(msg)
            
        if not all_matches:
            await interaction.followup.send(content=f"❌ 找不到包含「{location}」的任何監視器或測站影像，且無法定位該鄉鎮市區。")
            return
            
        all_matches = all_matches[:25]
        
        m = all_matches[0]
        
        header_desc = ""
        if len(all_matches) > 1:
            header_desc = f"🔍 找到 {len(all_matches)} 筆符合「{location}」的攝影機，預設顯示第一筆影像。\n(請從下方選單或按鈕切換其他攝影機)\n\n"
            
        if messages:
            header_desc = f"⚠️ {' | '.join(messages)}\n\n" + header_desc

        if m['source'] == 'moenv':
            embed = await self.get_site_embed(m['site_name'])
            if embed:
                embed.description = header_desc + (embed.description or "")
                
                kwargs = {'content': "📷 即時影像", 'embed': embed}
                if len(all_matches) > 1: kwargs['view'] = CameraView(all_matches, interaction.user.id, location, messages)
                await interaction.followup.send(**kwargs)
            else:
                await interaction.followup.send(content="❌ 無法取得影像。")
        else:
            sel_name = m['name']
            sel_url = m['video_url']
            source_title = "twipcam 監視器影像"
            color = 0x2ecc71
            
            embed = discord.Embed(title=f"{source_title} - {sel_name}", color=color)
            
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
            if len(all_matches) > 1: kwargs['view'] = CameraView(all_matches, interaction.user.id, location, messages)
            
            await interaction.followup.send(**kwargs)

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
                        embed.set_footer(text=f"環境部 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/moenv.png")
                        return embed
        except Exception as e:
            logger.error(f"取得測站 {site_name} 影像失敗: {e}")
        return None

    async def _get_moenv_matches(self, location: str, allow_fallback: bool = True):
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
                    
                matched_sites = [s["Name"] for s in sites if location in s["Name"]]
                location_msg = None
                
                if not matched_sites and allow_fallback:
                    mapping = load_town_mapping()
                    target_loc_info = mapping.get(location)
                    target_lat = target_lon = None
                    if target_loc_info:
                        for fullname, lat, lon in target_loc_info:
                            if lat and lon:
                                target_lat, target_lon = lat, lon
                                break
                                
                    if target_lat and target_lon:
                        min_dist = float('inf')
                        closest_site = None
                        for s in sites:
                            site_name = s["Name"]
                            site_info = mapping.get(site_name)
                            if site_info:
                                for fullname, lat, lon in site_info:
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


    async def _get_twipcam_matches(self, location: str):
        url = "https://www.twipcam.com/api/v1/cam-list.json"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with self.bot.session.get(url, headers=headers, timeout=15) as resp:
                if resp.status != 200: return [], None
                cctvs = await resp.json()
                
                matched = []
                for c in cctvs:
                    name = c.get('name', '')
                    if location in name:
                        matched.append((name, c.get("cam_url")))
                        
                location_msg = None
                if not matched:
                    mapping = load_town_mapping()
                    target_loc_info = mapping.get(location)
                    target_lat = target_lon = None
                    if target_loc_info:
                        for fullname, lat, lon in target_loc_info:
                            if lat and lon:
                                target_lat, target_lon = lat, lon
                                break
                    if target_lat and target_lon:
                        cam_distances = []
                        for c in cctvs:
                            clat = c.get('lat')
                            clon = c.get('lon')
                            if clat is not None and clon is not None:
                                try:
                                    dist = haversine_dist(target_lat, target_lon, float(clat), float(clon))
                                    cam_distances.append((dist, c))
                                except ValueError:
                                    pass
                        cam_distances.sort(key=lambda x: x[0])
                        top_cams = cam_distances[:3]
                        if top_cams:
                            for dist, c in top_cams:
                                name = f"{c.get('name', '')} [距離 {dist:.1f}km]"
                                matched.append((name, c.get("cam_url")))
                            location_msg = f"[twipcam] 顯示最近的攝影機"
                            
                results = []
                for name, url in matched:
                    results.append({'source': 'twipcam', 'name': name, 'video_url': url})
                return results, location_msg
        except Exception as e:
            logger.error(f"twipcam影像錯誤: {e}")
            return [], None

async def setup(bot):
    await bot.add_cog(LiveCameraCog(bot))
