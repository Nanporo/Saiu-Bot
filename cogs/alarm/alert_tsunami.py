import json
import logging
import math
import re
import unicodedata
from datetime import datetime

import aiohttp
import discord
from discord.ext import commands, tasks

from modules.cache_manager import load_cache, save_cache
from modules.database import get_all_settings, is_push_module_enabled
from modules.discord_utils import safe_channel_send
from cogs.alarm.alert_eew import get_mag_emoji, get_depth_emoji
from cogs.list_eq import get_clock_emoji
from modules.tsunami import (TAIPEI, affected_regions, as_list, event_key, is_cancellation_report, is_current, matches_subscription, parse_time,
                             report_image_url, report_key, reports_from_payload, uses_detailed_format, warning_areas)

logger = logging.getLogger(__name__)
API_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0014-001"


CWA_LOGO_URL = "https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/cwa_logo.png"


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _discord_time(value):
    dt = parse_time(value)
    return f"<t:{int(dt.timestamp())}:f>" if dt else "未提供"


def build_tsunami_embed(report, *, received_at=None):
    received_at = received_at or datetime.now(TAIPEI)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=TAIPEI)
    color = {"紅色": 0xe74c3c, "橙色": 0xe67e22, "黃色": 0xf1c40f, "綠色": 0x2ecc71}.get(report.get("ReportColor"), 0x3498db)
    embed = discord.Embed(color=color)
    report_no = unicodedata.normalize("NFKC", str(report.get("ReportNo", "")))
    match = re.search(r"\d+", report_no)
    report_no = f"第 {int(match.group())} 報" if match else "報次未提供"
    embed.set_footer(text=f"中央氣象署 • 接收時間 {received_at.astimezone(TAIPEI):%H:%M:%S} ({report_no})",
                     icon_url=CWA_LOGO_URL)

    def add_field(name, value, *, inline=False):
        # Discord allows 25 fields, 1024 characters per value and 6000 total.
        name = str(name)[:80]
        available = min(1024, 5900 - len(embed) - len(name))
        if len(embed.fields) < 25 and available > 0:
            embed.add_field(name=name, value=str(value)[:available] or "未提供", inline=inline)

    info = report.get("EarthquakeInfo") or {}
    epicenter = info.get("Epicenter") or {}
    mag = _number((info.get("EarthquakeMagnitude") or {}).get("MagnitudeValue"))
    depth = _number(info.get("FocalDepth"))
    mag_text = f"{mag:.1f}" if mag is not None else "未提供"
    depth_text = f"{depth:g}" if depth is not None else "未提供"
    add_field("📃 編號", f"{report.get('TsunamiNo', '未提供')}", inline=True)
    add_field(f"{get_mag_emoji(mag)} 規模", f"{mag_text}", inline=True)
    add_field(f"{get_depth_emoji(depth, mag)} 深度", f"{depth_text} 公里", inline=True)
    origin_time = parse_time(info.get("OriginTime"))
    clock = get_clock_emoji(origin_time.astimezone(TAIPEI)) if origin_time else "🕓"
    add_field(f"{clock} 發生時間", _discord_time(info.get("OriginTime")))
    location = str(epicenter.get("Location") or "未提供")[:200]
    location_text = location
    # The report follows the epicenter, as a code block. Split long reports to
    # preserve Discord's field limit while leaving space for wave information.
    content = str(report.get("ReportContent") or "未提供報文內容").translate(
        str.maketrans("０１２３４５６７８９﹒．（）　", "0123456789..() "))
    content = content.replace("```", "` ` `")
    if len(content) > 2200:
        content = content[:2199] + "…"
    first_length = 1024 - len(location_text) - 9
    add_field("📍 震央位置", f"{location_text}\n```\n{content[:first_length]}\n```", inline=True)
    remaining = content[first_length:]
    while remaining:
        add_field("報文內容（續）", f"```\n{remaining[:1016]}\n```")
        remaining = remaining[1016:]
    for area in warning_areas(report)[:6]:
        add_field(str(area.get("AreaName", "沿海分區"))[:50],
                  f"預估抵達：{_discord_time(area.get('ArrivalTime'))}\n最大預估波高：{str(area.get('WaveHeight', '未提供'))[:100]}")
    wave = report.get("TsunamiWave") or {}
    for station in as_list(wave.get("TsuStation"))[:10]:
        if not isinstance(station, dict):
            continue
        add_field(f"實測｜{str(station.get('StationName', '潮位站'))[:50]}",
                  f"實際抵達：{_discord_time(station.get('ArrivalTime'))}\n實測波高：{str(station.get('WaveHeight', '未提供'))[:100]}")
    image_url = report_image_url(report)
    if image_url:
        embed.set_image(url=image_url)
    return embed


def build_tsunami_simple_embed(report, regions, *, received_at=None):
    embed = build_tsunami_embed(report, received_at=received_at)
    embed.clear_fields()
    embed.set_image(url=None)
    content = str(report.get("ReportContent") or "未提供報文內容").translate(
        str.maketrans("０１２３４５６７８９﹒．（）　", "0123456789..() "))
    embed.description = content[:279] + "…" if len(content) > 280 else content
    selected = set(regions)
    for area in warning_areas(report):
        if area.get("AreaName") in selected:
            embed.add_field(name=str(area["AreaName"])[:80],
                value=f"預估抵達：{_discord_time(area.get('ArrivalTime'))}\n最大預估波高：{str(area.get('WaveHeight', '未提供'))[:100]}", inline=False)
    if not embed.fields:
        embed.add_field(name="通知區域", value="、".join(sorted(selected))[:1024] or "未提供", inline=False)
    return embed


class TsunamiAlertCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cache = load_cache()
        self.delivered = cache.get("tsunami_delivered", {})
        self.event_regions = cache.get("tsunami_event_regions", {})
        self.check_tsunami_loop.start()

    def cog_unload(self):
        self.check_tsunami_loop.cancel()

    def save_state(self):
        return {"tsunami_delivered": self.delivered, "tsunami_event_regions": self.event_regions}

    def persist_state(self):
        cache = load_cache()
        cache.update(self.save_state())
        save_cache(cache)

    def remember_event_regions(self, reports):
        before = dict(self.event_regions)
        # Include expired first reports when reconstructing a still-active event.
        # Later reports can expand the scope; preserve earlier recipients so
        # they also receive subsequent updates and cancellation notices.
        for report in sorted(reports, key=lambda r: str(r.get("IssueTime") or "")):
            key = event_key(report)
            areas = affected_regions(report)
            if key and areas:
                previous = self.event_regions.pop(key, [])
                self.event_regions[key] = sorted(set(previous) | areas)
        self.event_regions = dict(list(self.event_regions.items())[-500:])
        if self.event_regions != before:
            self.persist_state()

    async def fetch_reports(self):
        with open("config.json", encoding="utf-8") as f:
            key = json.load(f).get("CWA_API_KEY")
        if not key:
            return []
        async with self.bot.session.get(API_URL, params={"format": "JSON"},
                headers={"Authorization": key}, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status != 200:
                raise RuntimeError(f"CWA HTTP {response.status}")
            return reports_from_payload(await response.json(content_type=None))

    async def process_reports(self, reports):
        self.remember_event_regions(reports)
        current = sorted((r for r in reports if is_current(r)), key=lambda r: r["IssueTime"])
        # Keep receipts across temporarily incomplete API responses.
        self.delivered = dict(list(self.delivered.items())[-500:])
        settings = get_all_settings()
        received_at = datetime.now(TAIPEI)
        for report in current:
            embed = build_tsunami_embed(report, received_at=received_at)
            key = report_key(report)
            sent = set(self.delivered.get(key, []))
            for gid, guild_settings in settings.items():
                # Group overlapping regional subscriptions so each channel receives one copy.
                channel_options = {}
                for region, sub in guild_settings.get("tsunami_alerts", {}).items():
                    if not isinstance(sub, dict) or not sub.get("enabled", True) or not sub.get("channel_id"):
                        continue
                    if not matches_subscription(report, region, sub,
                            event_regions=self.event_regions.get(event_key(report))):
                        continue
                    options = channel_options.setdefault(str(sub["channel_id"]), {"detailed": False, "regions": []})
                    options["detailed"] |= uses_detailed_format(region, sub)
                    options["regions"].append(region)
                for channel_id, options in channel_options.items():
                    target = f"{gid}:{channel_id}"
                    if target in sent:
                        continue
                    channel = self.bot.get_channel(int(channel_id))
                    if channel is None or str(channel.guild.id) != str(gid):
                        continue
                    role_id = guild_settings.get("tsunami_mention_role_id")
                    label = {"海嘯消息": "🌊 海嘯消息", "海嘯警報": "🌊 海嘯警報"}.get(
                        report.get("ReportType"), "🌊 海嘯通知")
                    if is_cancellation_report(report):
                        label = "🌊 解除海嘯通知"
                    content = f"<@&{role_id}> {label}" if role_id else label
                    notification_embed = (embed.copy() if options["detailed"] else
                        build_tsunami_simple_embed(report, options["regions"], received_at=received_at))
                    message = await safe_channel_send(channel, context="海嘯通知", content=content,
                        embed=notification_embed, silent=guild_settings.get("global_silent", False),
                        allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=True))
                    if message is not None:
                        sent.add(target)
                        self.delivered[key] = sorted(sent)
                        self.persist_state()

    @tasks.loop(seconds=60)
    async def check_tsunami_loop(self):
        if not is_push_module_enabled("alert_tsunami") or not getattr(self.bot, "session", None) or self.bot.session.closed:
            return
        if getattr(self.bot, "is_abnormal_grace_period", lambda: False)():
            return
        try:
            await self.process_reports(await self.fetch_reports())
        except Exception as exc:
            # Do not log request URLs/headers, which may contain credentials.
            logger.warning("⚠️ [海嘯通知] 資料處理失敗：%s", type(exc).__name__)

    @check_tsunami_loop.before_loop
    async def before_check_tsunami(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(TsunamiAlertCog(bot))
