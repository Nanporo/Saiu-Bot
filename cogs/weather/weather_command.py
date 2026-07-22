import discord
from discord.ext import commands
from discord import app_commands
import json
import logging
from datetime import datetime, timezone, timedelta
from modules.location_matcher import match_location

from cogs.weather.weather_main import build_overview
from cogs.weather.weather_temp import build_temp
from cogs.weather.weather_pop import build_pop
from cogs.weather.weather_rh import build_rh
from cogs.weather.weather_wind import build_wind
from cogs.weather.weather_uvi import build_uvi

COUNTY_LOCATION_ID = {
    "宜蘭縣": "F-D0047-003",
    "桃園市": "F-D0047-007",
    "新竹縣": "F-D0047-011",
    "苗栗縣": "F-D0047-015",
    "彰化縣": "F-D0047-019",
    "南投縣": "F-D0047-023",
    "雲林縣": "F-D0047-027",
    "嘉義縣": "F-D0047-031",
    "屏東縣": "F-D0047-035",
    "臺東縣": "F-D0047-039",
    "花蓮縣": "F-D0047-043",
    "澎湖縣": "F-D0047-047",
    "基隆市": "F-D0047-051",
    "新竹市": "F-D0047-055",
    "嘉義市": "F-D0047-059",
    "臺北市": "F-D0047-063",
    "高雄市": "F-D0047-067",
    "新北市": "F-D0047-071",
    "臺中市": "F-D0047-075",
    "臺南市": "F-D0047-079",
    "連江縣": "F-D0047-083",
    "金門縣": "F-D0047-087"
}

logger = logging.getLogger(__name__)

class WeatherView(discord.ui.View):
    def __init__(self, target_location, county_name, town_name, author_id: int, initial_mode="overview"):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.target_location = target_location
        self.county_name = county_name
        self.town_name = town_name
        self.mode = initial_mode
        self.overview_page = 0
        
        self.page_labels = []
        now_date = datetime.now(timezone(timedelta(hours=8))).date()
        times = []
        for we in self.target_location.get("WeatherElement", []):
            if we.get("ElementName") == "天氣現象":
                times = we.get("Time", [])
                break
                
        self.max_pages = min(4, len(times)) if times else 1
        for i in range(self.max_pages):
            if times and i < len(times):
                try:
                    st = times[i].get("StartTime")
                    st_dt = datetime.fromisoformat(st)
                    delta_days = (st_dt.date() - now_date).days
                    period_name = "白天" if st_dt.hour == 6 else ("晚上" if st_dt.hour == 18 else "")
                    
                    if delta_days == 0: day_str = "今天"
                    elif delta_days == 1: day_str = "明天"
                    elif delta_days == 2: day_str = "後天"
                    else: day_str = st_dt.strftime("%m-%d")
                    self.page_labels.append(f"{day_str}{period_name}")
                except Exception:
                    self.page_labels.append(f"第 {i+1} 頁")
            else:
                self.page_labels.append(f"第 {i+1} 頁")

        # 手動加入下拉選單與按鈕，以便後續動態隱藏/顯示
        self.select = discord.ui.Select(
            placeholder="選擇要查看的資訊...",
            options=[
                discord.SelectOption(label="概覽", value="overview", emoji="🌤️", default=(initial_mode=="overview")),
                discord.SelectOption(label="氣溫", value="temp", emoji="🌡️", default=(initial_mode=="temp")),
                discord.SelectOption(label="降雨機率", value="pop", emoji="☔", default=(initial_mode=="pop")),
                discord.SelectOption(label="濕度", value="rh", emoji="💧", default=(initial_mode=="rh")),
                discord.SelectOption(label="風向風速", value="wind", emoji="💨", default=(initial_mode=="wind")),
                discord.SelectOption(label="紫外線", value="uvi", emoji="☀️", default=(initial_mode=="uvi"))
            ],
            row=0
        )
        self.select.callback = self.select_callback

        self.prev_btn = discord.ui.Button(emoji="⬅️", style=discord.ButtonStyle.primary, row=1, disabled=True)
        self.prev_btn.callback = self.prev_page

        self.reset_btn = discord.ui.Button(label="回現在", style=discord.ButtonStyle.secondary, emoji="↩️", row=2)
        self.reset_btn.callback = self.reset_page

        self.page_btn = discord.ui.Button(label=self.page_labels[self.overview_page], style=discord.ButtonStyle.secondary, row=1, disabled=True)

        self.close_btn = discord.ui.Button(label="關閉", style=discord.ButtonStyle.secondary, emoji="❌", row=2)
        self.close_btn.callback = self.close_view

        self.next_btn = discord.ui.Button(emoji="➡️", style=discord.ButtonStyle.primary, row=1)
        self.next_btn.callback = self.next_page

        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def update_components(self):
        self.clear_items()
        self.add_item(self.select)
        
        if self.mode == "overview":
            self.reset_btn.disabled = (self.overview_page == 0)
            self.prev_btn.disabled = (self.overview_page == 0)
            self.next_btn.disabled = (self.overview_page == self.max_pages - 1)
            self.page_btn.label = self.page_labels[self.overview_page]
            

            self.add_item(self.prev_btn)
            self.add_item(self.reset_btn)
            self.add_item(self.page_btn)
            self.add_item(self.close_btn)
            self.add_item(self.next_btn)
        else:
            self.add_item(self.close_btn)

    async def prev_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.overview_page -= 1
        self.update_components()
        content, embed = self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self)

    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.overview_page += 1
        self.update_components()
        content, embed = self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self)

    async def reset_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.overview_page = 0
        self.update_components()
        content, embed = self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self)

    async def close_view(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.mode = self.select.values[0]
        for option in self.select.options:
            option.default = (option.value == self.mode)
        
        self.update_components()
        content, embed = self.build_embed()
        await interaction.edit_original_response(content=content, embed=embed, view=self)

    def get_elements_dict(self):
        elements = {}
        for we in self.target_location.get("WeatherElement", []):
            elements[we.get("ElementName")] = we
        return elements

    def build_embed(self):
        elements = self.get_elements_dict()
        
        embed_color = 0x3498db
        message_content = "🌤️ 鄉鎮天氣預報查詢"
        
        wx_elem = elements.get("天氣現象", {}).get("Time", [])
        wx_idx = min(self.overview_page, len(wx_elem) - 1) if wx_elem and self.mode == "overview" else 0
        wx_first = wx_elem[wx_idx].get("ElementValue", [{}])[0].get("Weather", "") if wx_elem else ""
        
        if "雨" in wx_first:
            embed_color = 0x2980b9
            message_content = "🌧️ 鄉鎮天氣預報查詢"
        elif "晴" in wx_first:
            embed_color = 0xf1c40f
            message_content = "☀️ 鄉鎮天氣預報查詢"
        elif "雲" in wx_first or "陰" in wx_first:
            embed_color = 0x95a5a6
            message_content = "☁️ 鄉鎮天氣預報查詢"

        embed = discord.Embed(title="", color=embed_color)
        
        if self.mode == "overview":
            build_overview(embed, self.target_location, self.overview_page, self.county_name, self.town_name)
        else:
            embed.description = f"**{self.county_name}{self.town_name}** 的未來 3 天預報趨勢\n"
            
            # 預先抓取天氣現象，作為各時段 Emoji 標示依據
            wx_dict = {}
            for w_data in elements.get("天氣現象", {}).get("Time", []):
                st = w_data.get("StartTime")
                wx_val = w_data.get("ElementValue", [{}])[0].get("Weather", "")
                wx_dict[st] = "🌧️" if "雨" in wx_val else ("☀️" if "晴" in wx_val else "☁️")
            
            if self.mode == "temp":
                build_temp(embed, elements, wx_dict)
            elif self.mode == "pop":
                build_pop(embed, elements, wx_dict)
            elif self.mode == "rh":
                build_rh(embed, elements)
            elif self.mode == "wind":
                build_wind(embed, elements)
            elif self.mode == "uvi":
                build_uvi(embed, elements)

        current_time = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        return message_content, embed

class WeatherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @app_commands.command(name="天氣預報", description="🌤️ 查詢全臺灣各鄉鎮市區的未來天氣預報 Weather Forecast")
    @app_commands.describe(
        鄉鎮市區="請輸入縣市與鄉鎮市區（例如：臺北市信義區，若只輸入縣市則預設為市政府所在地）",
        mode="選擇要直接查看的資料"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="概覽", value="overview"),
        app_commands.Choice(name="氣溫", value="temp"),
        app_commands.Choice(name="降雨機率", value="pop"),
        app_commands.Choice(name="濕度", value="rh"),
        app_commands.Choice(name="風向風速", value="wind"),
        app_commands.Choice(name="紫外線", value="uvi")
    ])
    async def weather_command(self, interaction: discord.Interaction, 鄉鎮市區: str, mode: str = "overview"):
        await interaction.response.defer()

        if not self.api_key:
            await interaction.followup.send("⚠️ 未設定 API Key，無法查詢資料。", ephemeral=True)
            return

        # 使用與其他模組相同的驗證邏輯，將輸入轉換為標準的 "縣市+鄉鎮市區" 格式
        loc_val, error_msg = match_location(鄉鎮市區)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return

        county_name = loc_val[:3]
        town_name = loc_val[3:]

        location_id = COUNTY_LOCATION_ID.get(county_name)
        if not location_id:
            await interaction.followup.send(f"❌ 找不到對應的縣市代碼：{county_name}", ephemeral=True)
            return

        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-093?locationId={location_id}&LocationName={town_name}&ElementName="
        headers = {"Authorization": self.api_key}

        try:
            async with self.bot.session.get(url, headers=headers) as response:
                logger.info(f"🌐 [資料抓取] 鄉鎮天氣預報: {url} -> HTTP 狀態碼: {response.status}")
                if response.status != 200:
                    await interaction.followup.send(f"❌ API 請求失敗，狀態碼：{response.status}", ephemeral=True)
                    return
                data = await response.json()
        except Exception as e:
            logger.error(f"❌ 查詢天氣預報失敗: {e!r}")
            await interaction.followup.send(f"❌ 發生錯誤：{e!r}", ephemeral=True)
            return

        records = data.get("records", {})
        locations_list = records.get("Locations", [])
        
        target_location = None
        for locs in locations_list:
            # 確保縣市名稱也相符 (避免跨縣市同名鄉鎮，例如雙北、基隆都有信義區等情況)
            if locs.get("LocationsName") == county_name:
                for loc in locs.get("Location", []):
                    if loc.get("LocationName") == town_name:
                        target_location = loc
                        break
            if target_location:
                break

        if not target_location:
            await interaction.followup.send(f"❌ 找不到 **{county_name}{town_name}** 的預報資料。", ephemeral=True)
            return

        view = WeatherView(target_location, county_name, town_name, interaction.user.id, mode)
        content, embed = view.build_embed()
        await interaction.followup.send(content=content, embed=embed, view=view)

    @weather_command.autocomplete("鄉鎮市區")
    async def weather_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        from modules.location_matcher import get_town_autocomplete
        choices = get_town_autocomplete(current)
        return [app_commands.Choice(name=c, value=c) for c in choices]

    async def refresh_message(self, interaction: discord.Interaction, message: discord.Message, cmd_name: str):
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
                await interaction.response.defer(ephemeral=True)
                county_name = found_loc[:3]
                town_name = found_loc[3:]
                
                mode = "overview"
                for row in message.components:
                    for child in row.children:
                        if getattr(child, "type", None) == discord.ComponentType.select:
                            for opt in child.options:
                                if opt.default:
                                    mode = opt.value
                                    break
                
                location_id = COUNTY_LOCATION_ID.get(county_name)
                url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-093?locationId={location_id}&LocationName={town_name}&ElementName="
                headers = {"Authorization": self.api_key}
                try:
                    async with self.bot.session.get(url, headers=headers) as response:
                        if response.status != 200:
                            await interaction.followup.send(f"❌ API 請求失敗，狀態碼：{response.status}", ephemeral=True)
                            return
                        data = await response.json()
                except Exception as e:
                    await interaction.followup.send(f"❌ 發生錯誤：{e!r}", ephemeral=True)
                    return

                records = data.get("records", {})
                locations_list = records.get("Locations", [])
                
                target_location = None
                for locs in locations_list:
                    if locs.get("LocationsName") == county_name:
                        for loc in locs.get("Location", []):
                            if loc.get("LocationName") == town_name:
                                target_location = loc
                                break
                    if target_location:
                        break

                if target_location:
                    view = WeatherView(target_location, county_name, town_name, interaction.user.id, mode)
                    content, embed = view.build_embed()
                    await message.edit(content=content, embed=embed, view=view)
                    await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)
                    return
        await interaction.response.send_message("❌ 無法從這則天氣預報訊息中提取出地點以重新查詢。", ephemeral=True)

async def setup(bot):
    await bot.add_cog(WeatherCog(bot))
