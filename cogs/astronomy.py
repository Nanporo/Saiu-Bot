import discord
from discord.ext import commands
from discord import app_commands
import json
import logging
from datetime import datetime, timezone, timedelta
from modules.cache import async_cache
from modules.location_matcher import match_location, get_town_autocomplete

logger = logging.getLogger(__name__)

def get_moon_phase(lunar_date_str):
    if not lunar_date_str or lunar_date_str == "未知":
        return ("🌑", "未知")
    try:
        day = int(lunar_date_str.split("-")[2])
        if day in [1, 2, 29, 30]:
            return ("🌑", "朔月")
        elif 3 <= day <= 6:
            return ("🌒", "娥眉月")
        elif 7 <= day <= 8:
            return ("🌓", "上弦月")
        elif 9 <= day <= 13:
            return ("🌔", "盈凸月")
        elif 14 <= day <= 17:
            return ("🌕", "滿月")
        elif 18 <= day <= 21:
            return ("🌖", "虧凸月")
        elif 22 <= day <= 23:
            return ("🌗", "下弦月")
        elif 24 <= day <= 28:
            return ("🌘", "殘月")
        else:
            return ("🌑", "未知")
    except Exception:
        return ("🌑", "未知")

def calculate_daylight(sunrise, sunset):
    try:
        if not sunrise or not sunset or sunrise == "未知" or sunset == "未知":
            return "未知"
        fmt = "%H:%M"
        t1 = datetime.strptime(sunrise, fmt)
        t2 = datetime.strptime(sunset, fmt)
        diff = t2 - t1
        hours, remainder = divmod(diff.seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{hours} 小時 {minutes} 分鐘"
    except Exception:
        return "未知"

class AstronomyView(discord.ui.View):
    def __init__(self, cog, county_name: str, town_name: str, full_name: str, author_id: int, has_tide: bool, moon_emoji: str, initial_mode="overview"):
        super().__init__(timeout=300)
        self.cog = cog
        self.county_name = county_name
        self.town_name = town_name
        self.full_name = full_name
        self.author_id = author_id
        self.has_tide = has_tide
        self.moon_emoji = moon_emoji
        self.day_offset = 0
        self.mode = initial_mode
        self.max_days = 3
        
        self.page_labels = ["今天", "明天", "後天"]

        self.select = discord.ui.Select(
            placeholder="選擇要查看的資訊...",
            options=[],
            row=0
        )
        self.select.callback = self.select_callback

        self.prev_btn = discord.ui.Button(emoji="⬅️", style=discord.ButtonStyle.primary, row=1, disabled=True)
        self.prev_btn.callback = self.prev_page

        self.reset_btn = discord.ui.Button(label="回今天", style=discord.ButtonStyle.secondary, emoji="↩️", row=2)
        self.reset_btn.callback = self.reset_page

        self.page_btn = discord.ui.Button(label=self.page_labels[self.day_offset], style=discord.ButtonStyle.secondary, row=1, disabled=True)

        self.close_btn = discord.ui.Button(label="關閉", style=discord.ButtonStyle.secondary, emoji="❌", row=2)
        self.close_btn.callback = self.close_view

        self.next_btn = discord.ui.Button(emoji="➡️", style=discord.ButtonStyle.primary, row=1)
        self.next_btn.callback = self.next_page

        self.update_components()
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    def update_components(self):
        self.clear_items()
        
        options = [
            discord.SelectOption(label="概覽", value="overview", emoji="🔭", default=(self.mode == "overview")),
            discord.SelectOption(label="太陽與曙暮光", value="sun", emoji="☀️", default=(self.mode == "sun"))
        ]
        if self.has_tide:
            options.append(discord.SelectOption(label="潮汐預報", value="tide", emoji="🌊", default=(self.mode == "tide")))
        options.append(discord.SelectOption(label="月相與月球", value="moon", emoji=self.moon_emoji, default=(self.mode == "moon")))
        options.append(discord.SelectOption(label="行星動態", value="planet", emoji="🪐", default=(self.mode == "planet")))
        self.select.options = options
        
        self.add_item(self.select)
        
        self.reset_btn.disabled = (self.day_offset == 0)
        self.prev_btn.disabled = (self.day_offset == 0)
        self.next_btn.disabled = (self.day_offset == self.max_days - 1)
        self.page_btn.label = self.page_labels[self.day_offset]
        
        self.add_item(self.prev_btn)
        self.add_item(self.reset_btn)
        self.add_item(self.page_btn)
        self.add_item(self.close_btn)
        self.add_item(self.next_btn)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.mode = self.select.values[0]
        
        embed, self.has_tide, self.moon_emoji = await self.cog.build_astronomy_embed(self.county_name, self.town_name, self.full_name, self.day_offset, self.mode)
        self.update_components()
        await interaction.edit_original_response(content="🔭 天文資訊查詢", embed=embed, view=self)

    async def prev_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.day_offset -= 1
        embed, self.has_tide, self.moon_emoji = await self.cog.build_astronomy_embed(self.county_name, self.town_name, self.full_name, self.day_offset, self.mode)
        self.update_components()
        await interaction.edit_original_response(content="🔭 天文資訊查詢", embed=embed, view=self)

    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.day_offset += 1
        embed, self.has_tide, self.moon_emoji = await self.cog.build_astronomy_embed(self.county_name, self.town_name, self.full_name, self.day_offset, self.mode)
        self.update_components()
        await interaction.edit_original_response(content="🔭 天文資訊查詢", embed=embed, view=self)

    async def reset_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.day_offset = 0
        embed, self.has_tide, self.moon_emoji = await self.cog.build_astronomy_embed(self.county_name, self.town_name, self.full_name, self.day_offset, self.mode)
        self.update_components()
        await interaction.edit_original_response(content="🔭 天文資訊查詢", embed=embed, view=self)

    async def close_view(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()

class AstronomyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            self.api_key = config.get('CWA_API_KEY')
        except Exception:
            self.api_key = None

    @async_cache(ttl_seconds=3600)
    async def fetch_api(self, url):
        try:
            async with self.bot.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            logger.error(f"❌ 抓取資料失敗: {e}")
        return None

    @async_cache(ttl_seconds=3600)
    async def fetch_text(self, url):
        try:
            async with self.bot.session.get(url) as response:
                if response.status == 200:
                    return await response.text()
        except Exception as e:
            logger.error(f"❌ 抓取資料失敗: {e}")
        return None

    async def build_astronomy_embed(self, county_name: str, town_name: str, full_name: str, day_offset: int, mode: str = "overview") -> tuple[discord.Embed, bool, str]:
        now_tw = datetime.now(timezone(timedelta(hours=8)))
        target_date = now_tw + timedelta(days=day_offset)
        date_str = target_date.strftime("%Y-%m-%d")
        next_date_str = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")
        year_str = target_date.strftime("%Y")
        month_str = target_date.strftime("%m")

        sun_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/A-B0062-001?Authorization={self.api_key}&timeFrom={date_str}&timeTo={next_date_str}"
        moon_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/A-B0063-001?Authorization={self.api_key}&timeFrom={date_str}&timeTo={next_date_str}"
        tide_url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-A0021-001?Authorization={self.api_key}&Date={date_str}"

        sun_data = await self.fetch_api(sun_url)
        moon_data = await self.fetch_api(moon_url)
        tide_data = await self.fetch_api(tide_url)

        sun_info = {}
        if sun_data:
            locations = sun_data.get('records', {}).get('locations', {}).get('location', [])
            loc_data = next((loc for loc in locations if loc.get('CountyName') == county_name), None)
            if loc_data and loc_data.get('time'):
                sun_info = loc_data['time'][0]

        moon_info = {}
        if moon_data:
            locations = moon_data.get('records', {}).get('locations', {}).get('location', [])
            loc_data = next((loc for loc in locations if loc.get('CountyName') == county_name), None)
            if loc_data and loc_data.get('time'):
                moon_info = loc_data['time'][0]

        lunar_date = "未知"
        tide_station = ""
        moon_phase = ("🌑", "未知")
        tide_range = "未知"
        tide_events = []

        if tide_data:
            forecasts = tide_data.get('records', {}).get('TideForecasts', [])
            
            county_tides = [f for f in forecasts if f.get('Location', {}).get('LocationName', '').startswith(full_name)]
            
            today_tide_any = None
            for f in forecasts:
                daily_list = f.get('Location', {}).get('TimePeriods', {}).get('Daily', [])
                if daily_list:
                     today_tide_any = next((d for d in daily_list if d.get('Date') == date_str), None)
                     if today_tide_any:
                         break
            if today_tide_any:
                lunar_date = today_tide_any.get('LunarDate', '未知')
                moon_phase = get_moon_phase(lunar_date)

            if county_tides:
                t_loc = county_tides[0]
                tide_station = t_loc.get('Location', {}).get('LocationName', '')
                daily_list = t_loc.get('Location', {}).get('TimePeriods', {}).get('Daily', [])
                today_tide = next((d for d in daily_list if d.get('Date') == date_str), None)
                if today_tide:
                    tide_range = today_tide.get('TideRange', '未知')
                    tide_events = today_tide.get('Time', [])

        day_label = "今天" if day_offset == 0 else ("明天" if day_offset == 1 else "後天")
        
        mode_titles = {
            "overview": "天文資訊",
            "sun": "太陽與曙暮光",
            "tide": "潮汐預報",
            "moon": "月相與月球",
            "planet": "行星動態"
        }
        mode_title = mode_titles.get(mode, "天文資訊")

        embed = discord.Embed(
            title="",
            description=f"**{full_name}** 的{mode_title}\n\n",
            color=0x9b59b6
        )
        embed.add_field(name=f"{day_label} <t:{int(target_date.timestamp())}:D> (農曆 {lunar_date})", value=" ", inline=False)

        if mode == "overview":
            sunrise = sun_info.get('SunRiseTime', '未知')
            sunset = sun_info.get('SunSetTime', '未知')
            daylight = calculate_daylight(sunrise, sunset)
            sun_transit = sun_info.get('SunTransitTime', '未知')
            moonrise = moon_info.get('MoonRiseTime', '未知')
            moonset = moon_info.get('MoonSetTime', '未知')
            moon_transit = moon_info.get('MoonTransitTime', '未知')

            tide_result = "無資料"
            if not tide_station:
                tide_result = "無靠海測站"
            elif tide_events:
                tide_strs = []
                for evt in tide_events:
                    tide_type = evt.get('Tide', '')
                    dt_str = evt.get('DateTime', '')
                    try:
                        dt = datetime.fromisoformat(dt_str)
                        tide_strs.append(f"{tide_type} `{dt.strftime('%H:%M')}`")
                    except: pass
                if tide_strs:
                    formatted_tides = [" ".join(tide_strs[i:i+2]) for i in range(0, len(tide_strs), 2)]
                    tide_result = "\n".join(formatted_tides)
                else:
                    tide_result = "今日無變化"
            
            embed.add_field(name="🌅 日出", value=sunrise, inline=True)
            embed.add_field(name="🌇 日落", value=sunset, inline=True)
            
            emoji, text = moon_phase
            embed.add_field(name=f"{emoji} 月相", value=text, inline=True)

            day_url = f"https://www.cwa.gov.tw/Data/js/astronomy/astronomy_day_{year_str}.js"
            day_text = await self.fetch_text(day_url)
            day_events = "無特殊天象"
            if day_text:
                import re
                match = re.search(fr"'{date_str}':{{[^}}]*'st':{{'C':'(.*?)'", day_text)
                if match:
                    event = match.group(1)
                    if event and event != '-':
                        day_events = event.replace('；', '\n')
            embed.add_field(name="✨ 星象曆", value=day_events, inline=True)

            embed.add_field(name=f"🌊 潮汐" if tide_station else "🌊 潮汐", value=tide_result, inline=True)

            intro_url = f"https://www.cwa.gov.tw/Data/js/astronomy/astronomy_month_intro_{year_str}.js"
            intro_text = await self.fetch_text(intro_url)
            intro_content = "無資料"
            if intro_text:
                import re
                match = re.search(fr"'{month_str}':{{[^}}]*'content':'(.*?)'}}", intro_text)
                if match:
                    raw_html = match.group(1)
                    raw_html = raw_html.replace("<br>", "\n").replace("&nbsp;", " ").replace(r'\"', '"')
                    intro_content = re.sub(r'<[^>]+>', '', raw_html).strip()
                    intro_content = "\n".join(line.strip() for line in intro_content.split("\n"))
            
            if intro_content and intro_content != "無資料":
                embed.add_field(name=f"", value=f"```\n{intro_content}\n```", inline=False)

        elif mode == "sun":
            dawn = sun_info.get('BeginCivilTwilightTime', '未知')
            dusk = sun_info.get('EndCivilTwilightTime', '未知')
            sunrise = sun_info.get('SunRiseTime', '未知')
            sunrise_az = sun_info.get('SunRiseAZ', '未知')
            sunset = sun_info.get('SunSetTime', '未知')
            sunset_az = sun_info.get('SunSetAZ', '未知')
            transit = sun_info.get('SunTransitTime', '未知')
            transit_alt = sun_info.get('SunTransitAlt', '未知')

            embed.add_field(name="🌅 黎明 (民用曙光)", value=f"{dawn}", inline=True)
            embed.add_field(name="🌄 日出", value=f"{sunrise}\n方位角 {sunrise_az}°", inline=True)
            embed.add_field(name="🌞 日正午", value=f"{transit}\n仰角 {transit_alt}", inline=True)
            embed.add_field(name="🌇 日落", value=f"{sunset}\n方位角 {sunset_az}°", inline=True)
            embed.add_field(name="🌆 黃昏 (民用暮光)", value=f"{dusk}", inline=True)
            embed.add_field(name="⏱️ 日照時間", value=calculate_daylight(sunrise, sunset), inline=True)

        elif mode == "tide":
            if not tide_station:
                embed.description += "\n\n❌ 此縣市無海岸測站，無法提供潮汐資料。"
            else:
                embed.add_field(name="📍 測站名稱", value=tide_station, inline=True)
                embed.add_field(name="🌊 潮汐等級", value=f"{tide_range}潮" if tide_range != '未知' else "未知", inline=True)
                
                if tide_events:
                    details = []
                    for evt in tide_events:
                        tide_type = evt.get('Tide', '')
                        dt_str = evt.get('DateTime', '')
                        hgt = evt.get('TideHeights', {}).get('AboveTWVD', '未知')
                        try:
                            dt = datetime.fromisoformat(dt_str)
                            details.append(f"**{tide_type}** `{dt.strftime('%H:%M')}` (潮高 {hgt} cm)")
                        except: pass
                    embed.add_field(name="⏳ 潮汐時間", value="\n".join(details), inline=False)
                else:
                    embed.add_field(name="⏳ 潮汐時間", value="無當日詳細潮汐", inline=False)

        elif mode == "moon":
            moonrise = moon_info.get('MoonRiseTime', '未知')
            moonrise_az = moon_info.get('MoonRiseAZ', '未知')
            moonset = moon_info.get('MoonSetTime', '未知')
            moonset_az = moon_info.get('MoonSetAZ', '未知')
            transit = moon_info.get('MoonTransitTime', '未知')
            transit_alt = moon_info.get('MoonTransitAlt', '未知')

            emoji, text = moon_phase
            embed.add_field(name=f"{emoji} 月相", value=f"{text}", inline=False)
            def get_time_val(t_str):
                return t_str if t_str and t_str not in ['未知', '未發生'] else '24:00'

            moon_events = [
                ("🌛 月出", moonrise if moonrise else '未發生', f"方位角 {moonrise_az}°" if moonrise else ''),
                ("🌜 月落", moonset if moonset else '未發生', f"方位角 {moonset_az}°" if moonset else ''),
                ("🌌 月過中天", transit if transit else '未發生', f"仰角 {transit_alt}" if transit else '')
            ]
            moon_events.sort(key=lambda x: get_time_val(x[1]))

            for name, time_val, extra in moon_events:
                val = f"{time_val}\n{extra}" if extra else time_val
                embed.add_field(name=name, value=val, inline=True)

        elif mode == "planet":
            planet_url = f"https://www.cwa.gov.tw/Data/js/astronomy/astronomy_planet_states_{year_str}.js"
            planet_text = await self.fetch_text(planet_url)
            has_data = False
            if planet_text:
                import re
                match = re.search(fr"'{month_str}':\[(.*?)\](?:,|}})", planet_text)
                if match:
                    month_data = match.group(1)
                    items = re.findall(r"\'planet\':\'(.*?)\'\s*,\s*\'content\':\'(.*?)\'", month_data)
                    for planet_name, content in items:
                        planet_name = planet_name.strip()
                        embed.add_field(name=f"{planet_name}", value=content, inline=False)
                        has_data = True
            
            if not has_data:
                embed.description += "\n\n❌ 目前無法取得當月行星動態資料。"

        current_time = now_tw.strftime("%m-%d %H:%M")
        embed.set_footer(text=f"中央氣象署 • 查詢時間 {current_time}", icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png")
        
        return embed, bool(tide_station), moon_phase[0]

    @app_commands.command(name="天文資訊", description="🔭 查詢指定鄉鎮市區當日及未來的天文與潮汐資訊 Astronomy")
    @app_commands.describe(
        鄉鎮市區="請輸入縣市與鄉鎮市區（例如：臺北市信義區，若只輸入縣市則預設為市政府所在地）",
        mode="選擇要直接查看的資料"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="概覽", value="overview"),
        app_commands.Choice(name="太陽與曙暮光", value="sun"),
        app_commands.Choice(name="潮汐預報", value="tide"),
        app_commands.Choice(name="月相與月球", value="moon"),
        app_commands.Choice(name="行星動態", value="planet")
    ])
    async def astronomy_command(self, interaction: discord.Interaction, 鄉鎮市區: str, mode: str = "overview"):
        if not self.api_key:
            await interaction.response.send_message("⚠️ 未設定 API Key，無法查詢資料。", ephemeral=True)
            return

        loc_val, error_msg = match_location(鄉鎮市區)
        if error_msg:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

        county_name = loc_val[:3]
        town_name = loc_val[3:]

        await interaction.response.defer()
        
        embed, has_tide, moon_emoji = await self.build_astronomy_embed(county_name, town_name, loc_val, 0, mode)
        view = AstronomyView(self, county_name, town_name, loc_val, interaction.user.id, has_tide, moon_emoji, mode)

        await interaction.followup.send(content="🔭 天文資訊查詢", embed=embed, view=view)

    @astronomy_command.autocomplete("鄉鎮市區")
    async def astronomy_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        choices = get_town_autocomplete(current)
        return [app_commands.Choice(name=c, value=c) for c in choices]

async def setup(bot):
    await bot.add_cog(AstronomyCog(bot))
