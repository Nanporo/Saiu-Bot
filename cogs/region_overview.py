"""縣市氣象與生活資訊摘要，沿用各模組的資料來源。"""

import asyncio
import logging
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from cogs.alarm.alert_suspension import COUNTIES, is_no_announcement_status
from cogs.alarm.alert_traffic import is_location_affected
from cogs.settings.settings_traffic import get_available_traffic_modes
from modules.ai_tools import _fetch_current_cbs_alerts, _get_suspension_status


logger = logging.getLogger(__name__)
MESSAGE_CONTENT = "[📍](https://discord.com/assets/0da00b0fec31ced4.svg) {county} 地區總覽"
NO_DATA = "無資料"


def resolve_county(value):
    value = value.strip().replace("台", "臺")
    value = {"馬祖": "連江縣"}.get(value, value)
    matches = [county for county in COUNTIES if value in (county, county[:-1])]
    return matches[0] if len(matches) == 1 else None


def number(value, minimum=None):
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(result) or result in (-99, -999, -990):
        return None
    return result if minimum is None or result >= minimum else None


def compact(value, limit=180):
    value = discord.utils.escape_markdown(re.sub(r"\s+", " ", str(value)).strip())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def weather_emoji(weather):
    if "雷" in weather or "雨" in weather:
        return "🌧️"
    if "晴時多雲" in weather or "多雲時晴" in weather:
        return "🌤️"
    if "晴" in weather:
        hour = datetime.now(timezone(timedelta(hours=8))).hour
        return "🌙" if hour >= 18 or hour < 6 else "☀️"
    if "雲" in weather or "陰" in weather:
        return "☁️"
    return "🌡️"


class RegionOverviewView(discord.ui.View):
    def __init__(self, cog, county, author_id):
        super().__init__(timeout=300)
        self.cog, self.county, self.author_id = cog, county, author_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="重新整理", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction, button):
        await interaction.response.defer()
        embed = await self.cog.build_embed(self.county)
        await interaction.edit_original_response(content=MESSAGE_CONTENT.format(county=self.county), embed=embed, view=self)

    @discord.ui.button(label="關閉", emoji="❌", style=discord.ButtonStyle.secondary)
    async def close(self, interaction, button):
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()


class RegionOverviewCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def weather_summary(self, county):
        cog = self.bot.get_cog("NowWeatherCog")
        if not cog or not cog.api_key:
            return NO_DATA
        data = await cog.fetch_now_weather()
        stations = (data or {}).get("records", {}).get("Station", [])
        stations = [station for station in stations
                    if station.get("GeoInfo", {}).get("CountyName", "").replace("台", "臺") == county]
        temperatures = [
            number(station.get("WeatherElement", {}).get("AirTemperature"))
            for station in stations
        ]
        temperatures = [value for value in temperatures if value is not None]
        if not temperatures:
            return NO_DATA
        # 以縣市內最多測站回報的天氣現象決定圖示。
        conditions = Counter(
            str(station.get("WeatherElement", {}).get("Weather") or "").strip()
            for station in stations
        )
        for missing in ("", "-99", "-99.0", "-999", "-999.0", "-990", "-990.0"):
            conditions.pop(missing, None)
        weather = conditions.most_common(1)[0][0] if conditions else ""
        return (
            f"{weather_emoji(weather)} 現在溫度",
            f"最高 {max(temperatures):g} °C\n最低 {min(temperatures):g} °C",
            weather,
        )

    async def rain_summary(self, county):
        cog = self.bot.get_cog("RainForecastCog")
        if not cog:
            return NO_DATA
        indices = {
            index for town, mask in cog.town_grid_masks.items()
            if town.replace("台", "臺").startswith(county) for index in mask
        }
        if not indices:
            return NO_DATA
        # 只抓取一次全臺格點資料；使用縣市內全部鄉鎮遮罩，避免以單點代表全縣市。
        _, error = await cog.fetch_rain_value(0, 0)
        if error:
            return NO_DATA
        values = [number(cog.latest_rain_data[index], 0)
                  for index in indices if 0 <= index < len(cog.latest_rain_data)]
        values = [value for value in values if value is not None]
        return f"最大預估 {max(values):g} mm" if values else NO_DATA

    async def aqi_summary(self, county):
        cog = self.bot.get_cog("AqiCog")
        if not cog:
            return NO_DATA
        records = await cog.fetch_aqi_data()
        values = [number(item.get("aqi"), 0) for item in records or []
                  if item.get("county", "").replace("台", "臺") == county]
        values = [value for value in values if value is not None]
        if not values:
            return NO_DATA
        worst = max(values)
        emoji = "🟢" if worst <= 50 else "🟡" if worst <= 100 else "🟠" if worst <= 150 else "🔴" if worst <= 200 else "🟣" if worst <= 300 else "🟤"
        return f"{emoji} 空氣品質", f"AQI {min(values):g}～{worst:g}"

    async def alert_summary(self, county):
        alerts, error = await _fetch_current_cbs_alerts(self.bot, county)
        if error:
            return NO_DATA
        topics = list(dict.fromkeys(compact(item["topic"], 70) for item in alerts))
        return "\n".join(topics[:3]) + ("\n…" if len(topics) > 3 else "") if topics else "目前無有效告警"

    async def suspension_summary(self, county):
        data = await _get_suspension_status(self.bot, county)
        if data.get("error"):
            return NO_DATA
        announcement = data["result"]["announcement"]
        if is_no_announcement_status(announcement):
            return "無公告或尚未公告"
        return compact(announcement, 300)

    async def traffic_summary(self, county):
        modes = get_available_traffic_modes(county)
        if not modes:
            return "無軌道運具"
        cog = self.bot.get_cog("TrafficCog")
        if not cog:
            return NO_DATA
        thsr, tra, metro = await cog._fetch_all_traffic_data()
        thsr, tra, metro = thsr or {}, tra or {}, metro or {}
        metro = {key.lower(): value for key, value in metro.items()}
        lines = []
        for mode in modes:
            code = mode["code"]
            data = thsr if code == "thsr" else tra if code == "tra" else metro.get(code, {})
            unavailable = (not data or data.get("error") or data.get("status_level", 0) < 0)
            if code == "thsr":
                status = data.get("status_text", "")
                unavailable = unavailable or not status or "無法取得" in status
            if unavailable:
                state = NO_DATA
            elif code in ("thsr", "tra"):
                affected = is_location_affected(county, thsr, tra, {}, [code])
                state = "🟡 有營運異動" if affected else "未見本地異動"
            else:
                state = "🟡 有營運異動" if data.get("has_issue") else compact(data.get("status_text") or NO_DATA, 40)
            lines.append(f"{mode['label']}：{state}")
        return "\n".join(lines)

    async def build_embed(self, county):
        sources = (
            ("🌡️ 現在溫度", self.weather_summary),
            ("☔️ 1 小時降雨", self.rain_summary),
            ("⚪ 空氣品質", self.aqi_summary),
            ("⚠️ 有效告警", self.alert_summary),
            ("🚄 交通狀況", self.traffic_summary),
            ("🎒 停班停課", self.suspension_summary),
        )

        async def fetch(function):
            return await asyncio.wait_for(function(county), timeout=20)

        results = await asyncio.gather(*(fetch(function) for _, function in sources), return_exceptions=True)
        embed = discord.Embed(title="", description=f"**{county}**", color=0x41809b)
        for (name, _), result in zip(sources, results):
            if isinstance(result, BaseException):
                logger.warning("⚠️ [地區總覽] %s %s：%r", county, name, result)
                result = NO_DATA
            if isinstance(result, tuple):
                if len(result) == 3:
                    name, result, weather = result
                    embed.description = f"**{county}{' ' + weather if weather else ''}**"
                else:
                    name, result = result
            embed.add_field(name=name, value=result, inline=name not in ("🚄 交通狀況", "🎒 停班停課"))
        now = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
        embed.set_footer(text=f"查詢時間 {now}")
        return embed

    @app_commands.command(name="地區總覽", description="📍 查詢縣市區域氣象、告警與生活資訊 Region Overview")
    @app_commands.describe(縣市="請選擇縣市")
    async def region_overview(self, interaction: discord.Interaction, 縣市: str):
        county = resolve_county(縣市)
        if not county:
            await interaction.response.send_message("❌ 請選擇完整縣市名稱。", ephemeral=True)
            return
        await interaction.response.defer()
        embed = await self.build_embed(county)
        await interaction.followup.send(content=MESSAGE_CONTENT.format(county=county), embed=embed,
                                        view=RegionOverviewView(self, county, interaction.user.id))

    @region_overview.autocomplete("縣市")
    async def county_autocomplete(self, interaction: discord.Interaction, current: str):
        query = current.strip().replace("台", "臺")
        if query == "馬祖":
            query = "連江"
        return [app_commands.Choice(name=county, value=county) for county in COUNTIES if query in county]

    async def refresh_message(self, interaction, message, cmd_name):
        description = (message.embeds[0].description or "").strip("*") if message.embeds else ""
        county = resolve_county(description.split()[0]) if description else None
        if not county:
            await interaction.response.send_message("❌ 找不到縣市。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        embed = await self.build_embed(county)
        await message.edit(content=MESSAGE_CONTENT.format(county=county), embed=embed,
                           view=RegionOverviewView(self, county, interaction.user.id))
        await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)


async def setup(bot):
    await bot.add_cog(RegionOverviewCog(bot))
