import asyncio
import io
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, Select
from PIL import Image, ImageDraw, ImageFont

from modules.adsb_store import AirportIndex, AdsbHistoryStore, EMERGENCY_SQUAWKS, HISTORY_SECONDS, direction_8, live_aircraft, safe_float
from modules.config import get_config


logger = logging.getLogger(__name__)
TAIPEI_TZ = timezone(timedelta(hours=8))
PAGE_SIZE = 8
MODE_LABELS = {"live": "即時空中", "summary": "24 小時總覽", "cargo": "24 小時貨機", "special": "24 小時特殊機"}
MESSAGE_TITLE = "✈️ 小裁雨 ADS-B 飛機觀測"
SQUAWK_LABELS = {"7500": "非法干擾", "7600": "通訊失效", "7700": "緊急情況"}


def _number(value, decimals=0):
    value = safe_float(value)
    if value is None:
        return "未知"
    return f"{value:.{decimals}f}" if decimals else f"{value:.0f}"


def _name(item):
    return item.get("flight") or item.get("registration") or str(item.get("hex", "未知")).upper()


def _suffix(item):
    operator = item.get("operator_alias") or item.get("operator") or ""
    labels = [x for x in str(item.get("special_labels") or "").split(",") if x]
    if item.get("is_cargo"):
        labels.insert(0, "貨機")
    values = list(dict.fromkeys(([operator] if operator else []) + labels))
    return f"（{'、'.join(values)}）" if values else ""


def _flag(item):
    code = str(item.get("country_code") or "").upper()
    if len(code) == 2 and code.isalpha():
        return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in code)
    return "🌐"


def _vertical(rate):
    rate = safe_float(rate)
    if rate is not None and rate > 256:
        return "🛫", "上升"
    if rate is not None and rate < -256:
        return "🛬", "下降"
    return "➡️", "平飛"


def _coverage(seconds):
    if seconds >= HISTORY_SECONDS:
        return "已累積完整 24 小時"
    if seconds >= 3600:
        return f"資料收集中，已累積 {seconds / 3600:.1f} 小時"
    return f"資料收集中，已累積 {max(1, int(seconds // 60))} 分鐘"


def _walk_components(items):
    for item in items:
        yield item
        children = getattr(item, "children", None) or getattr(item, "components", None)
        if children:
            yield from _walk_components(children)


def draw_adsb_map(data):
    """Draw the receiver's existing south-west Taiwan map."""
    with open("maps/towns-mercator-10t.json", encoding="utf-8") as handle:
        topo = json.load(handle)
    scale, translate = topo["transform"]["scale"], topo["transform"]["translate"]
    decoded = []
    for arc in topo["arcs"]:
        x = y = 0
        points = []
        for dx, dy in arc:
            x, y = x + dx, y + dy
            points.append((x * scale[0] + translate[0], y * scale[1] + translate[1]))
        decoded.append(points)

    shapes = []
    for geometry in topo["objects"]["towns"]["geometries"]:
        county = geometry.get("properties", {}).get("COUNTYNAME", "")
        if county in {"金門縣", "連江縣"}:
            continue
        rings = geometry["arcs"] if geometry["type"] == "Polygon" else [ring for polygon in geometry["arcs"] for ring in polygon]
        for ring in rings:
            line = []
            for index in ring:
                line.extend(decoded[~index][::-1] if index < 0 else decoded[index])
            if line:
                shapes.append((county, line))

    mainland = [p for county, line in shapes if county != "澎湖縣" for p in line]
    min_x, max_x = min(p[0] for p in mainland), max(p[0] for p in mainland)
    min_y, max_y = min(p[1] for p in mainland), max(p[1] for p in mainland)
    min_lon, max_lon, min_lat, max_lat = 118.0, 121.2, 21.0, 24.5

    def merc_y(lat):
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

    width = 800
    canvas_y_max, canvas_y_min = merc_y(max_lat), merc_y(min_lat)
    height = int(width * (canvas_y_max - canvas_y_min) / math.radians(max_lon - min_lon))

    def wgs_pixel(lon, lat):
        return ((lon - min_lon) / (max_lon - min_lon) * width, (canvas_y_max - merc_y(lat)) / (canvas_y_max - canvas_y_min) * height)

    def topo_pixel(x, y):
        lon = 120.036 + (x - min_x) / (max_x - min_x) * (122.001 - 120.036)
        source_max, source_min = merc_y(25.300), merc_y(21.896)
        projected_y = source_max - (y - min_y) / (max_y - min_y) * (source_max - source_min)
        return ((lon - min_lon) / (max_lon - min_lon) * width, (canvas_y_max - projected_y) / (canvas_y_max - canvas_y_min) * height)

    penghu = [topo_pixel(*p) for county, line in shapes if county == "澎湖縣" for p in line]
    offset = (0, 0)
    if penghu:
        fake = ((min(p[0] for p in penghu) + max(p[0] for p in penghu)) / 2, (min(p[1] for p in penghu) + max(p[1] for p in penghu)) / 2)
        real = wgs_pixel(119.508, 23.479)
        offset = real[0] - fake[0], real[1] - fake[1]

    image = Image.new("RGBA", (width, height), "#0f1113")
    draw = ImageDraw.Draw(image)
    for county, line in shapes:
        points = [topo_pixel(*p) for p in line]
        if county == "澎湖縣":
            points = [(x + offset[0], y + offset[1]) for x, y in points]
        if len(points) >= 3:
            draw.polygon(points, fill="#1a1d20", outline="#3e454b")

    font = None
    for path in ("fonts/Noto_Sans_TC/NotoSansTC-Regular.ttf", "/System/Library/Fonts/PingFang.ttc", "C:\\Windows\\Fonts\\msjh.ttc"):
        try:
            font = ImageFont.truetype(path, 24)
            break
        except OSError:
            pass
    font = font or ImageFont.load_default()

    def color(altitude):
        altitude = safe_float(altitude)
        if altitude is None: return "#A9A9A9"
        if altitude < 1000: return "#FF3B30"
        if altitude < 4000: return "#FFCC00"
        if altitude < 10000: return "#34C759"
        if altitude < 20000: return "#5AC8FA"
        if altitude < 30000: return "#5555FF"
        return "#AF52DE"

    def plane_icon(fill, outline, heading):
        """Render a supersampled aircraft silhouette and downscale for smooth edges."""
        antialias = 4
        icon_size = 52
        center = icon_size * antialias / 2
        # Nose points upward. The tapered wings, fuselage and tail are deliberately
        # symmetric so the icon remains readable at any track angle.
        silhouette = [
            (0, -20), (2, -16), (3, -7), (16, 2), (17, 5),
            (5, 4), (4, 12), (9, 16), (8, 19), (2, 17),
            (0, 21), (-2, 17), (-8, 19), (-9, 16), (-4, 12),
            (-5, 4), (-17, 5), (-16, 2), (-3, -7), (-2, -16),
        ]
        angle = math.radians(safe_float(heading, 0) or 0)

        def transform(points, offset_x=0, offset_y=0):
            transformed = []
            for point_x, point_y in points:
                rotated_x = point_x * math.cos(angle) - point_y * math.sin(angle)
                rotated_y = point_x * math.sin(angle) + point_y * math.cos(angle)
                transformed.append((
                    center + (rotated_x + offset_x) * antialias,
                    center + (rotated_y + offset_y) * antialias,
                ))
            return transformed

        high_res = Image.new("RGBA", (icon_size * antialias, icon_size * antialias), (0, 0, 0, 0))
        icon_draw = ImageDraw.Draw(high_res)
        icon_draw.polygon(transform(silhouette, 1.5, 2), fill=(0, 0, 0, 120))
        icon_draw.polygon(
            transform(silhouette),
            fill=fill,
            outline=outline,
            width=2 * antialias,
        )
        # A subtle fuselage highlight keeps the aircraft shape legible on dark fills.
        icon_draw.line(
            transform([(0, -15), (0, 15)]),
            fill=(255, 255, 255, 105),
            width=antialias,
        )
        return high_res.resize((icon_size, icon_size), Image.Resampling.LANCZOS)

    for aircraft in data.get("aircraft", []) or []:
        lon, lat = safe_float(aircraft.get("lon")), safe_float(aircraft.get("lat"))
        if lon is None or lat is None:
            continue
        x, y = wgs_pixel(lon, lat)
        if not (-50 <= x <= width + 50 and -50 <= y <= height + 50):
            continue
        label = str(aircraft.get("flight") or aircraft.get("r") or aircraft.get("hex") or "?").strip()
        outline = "#FF0000" if str(aircraft.get("squawk")) in EMERGENCY_SQUAWKS else "#FFFFFF"
        icon = plane_icon(color(aircraft.get("alt_baro")), outline, aircraft.get("track"))
        image.paste(icon, (round(x - icon.width / 2), round(y - icon.height / 2)), icon)
        draw.text((x + 20, y - 15), label, fill="#FFFFFF", stroke_width=2, stroke_fill="#000000", font=font)

    observed = datetime.fromtimestamp(safe_float(data.get("now"), time.time()), tz=timezone.utc).astimezone(TAIPEI_TZ)
    draw.text((15, height - 45), f"T-RCNN39 • 資料時間 {observed:%Y-%m-%d %H:%M:%S}", fill="#FFFFFF", stroke_width=2, stroke_fill="#000000", font=font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output


class AdsbView(discord.ui.View):
    def __init__(self, cog, author_id, *, show_map=False, mode="live", page=0, selected_hex="", lookup_query="", previous_mode="live"):
        super().__init__(timeout=300)
        self.cog, self.author_id = cog, author_id
        self.mode = mode if mode in {*MODE_LABELS, "detail", "candidates"} else "live"
        self.show_map = bool(show_map and self.mode == "live")
        self.page, self.selected_hex = max(0, page), selected_hex.lower()
        self.lookup_query = str(lookup_query or "").strip()
        self.previous_mode = previous_mode if previous_mode in MODE_LABELS else "live"
        self.data = None
        self.live_items, self.history_items, self.detail_items = [], [], []
        self.summary_data, self.coverage, self.map_bytes = {}, 0, None
        self.embed = discord.Embed()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ 這個按鈕/選單只能由原指令使用者操作！", ephemeral=True)
            return False
        return True

    async def load(self, *, query=None, fetch_live=True):
        if fetch_live:
            self.data = await self.cog.fetch_data()
            if self.data:
                await self.cog.store.sample(self.data)
        self.live_items = live_aircraft(self.data)
        self.coverage = await self.cog.store.coverage_seconds()
        if query:
            self.lookup_query = str(query).strip()
            self.detail_items = await self.cog.store.lookup(query)
            if len(self.detail_items) == 1:
                self.mode, self.selected_hex = "detail", self.detail_items[0]["hex"]
            elif len(self.detail_items) > 1:
                self.mode = "candidates"
            else:
                self.mode, self.selected_hex = "detail", ""
        await self._load_mode()
        await self._rebuild()

    async def _load_mode(self):
        if self.mode == "summary":
            self.summary_data = await self.cog.store.summary()
            self.history_items = await self.cog.store.recent_encounters("special")
        elif self.mode in {"cargo", "special"}:
            self.history_items = await self.cog.store.recent_encounters(self.mode)
        elif self.mode == "detail" and self.selected_hex:
            self.detail_items = await self.cog.store.lookup(self.selected_hex)

    def _state_id(self):
        query = self.lookup_query.replace(":", "")[:32] or "-"
        return f"adsb:state:{self.mode}:{self.page}:{int(self.show_map)}:{self.selected_hex or '-'}:{query}:{self.previous_mode}"

    def _footer(self):
        data_time = "接收站離線"
        if self.data and safe_float(self.data.get("now")) is not None:
            observed = datetime.fromtimestamp(float(self.data["now"]), tz=timezone.utc).astimezone(TAIPEI_TZ)
            data_time = f"資料 {observed:%H:%M:%S}"
        return (
            f"T-RCNN39 接收站 • {_coverage(self.coverage)}\n"
            f"{data_time} • 查詢 {datetime.now(TAIPEI_TZ):%m-%d %H:%M}"
        )

    def _location(self, item, historical=False, limit=2):
        lat_key, lon_key = ("last_lat", "last_lon") if historical else ("lat", "lon")
        nearest = self.cog.airports.nearest(item.get(lat_key), item.get(lon_key), limit)
        if nearest:
            return "、".join(f"{a['code']}{a['direction']}{a['distance_km']:.0f}km" for a in nearest)
        distance = safe_float(item.get("last_distance_km" if historical else "distance_km"))
        return f"接收站 {distance:.1f}km" if distance is not None else "位置未知"

    def _live_line(self, item):
        icon, state = _vertical(item.get("vertical_rate"))
        details = _suffix(item).strip("（）")
        operator = f"{details}　" if details else ""
        return (
            f"{icon} {_flag(item)} `{_name(item)}` {operator}{_number(item.get('altitude'))} ft\n"
            f"> {state} {_number(item.get('speed'))} kt · 往{direction_8(item.get('track'))} · "
            f"{self._location(item, limit=1)}"
        )

    def _history_line(self, item):
        icon = "📦" if item.get("is_cargo") else "✨"
        return f"{icon} {_flag(item)} `{_name(item)}`{_suffix(item)}\n> <t:{int(item['last_seen'])}:R> · {self._location(item, True)}"

    def _page_items(self):
        items = self.live_items if self.mode == "live" else self.history_items
        max_page = max(0, (len(items) - 1) // PAGE_SIZE)
        self.page = min(self.page, max_page)
        start = self.page * PAGE_SIZE
        return items[start:start + PAGE_SIZE], max_page

    def _body(self):
        if self.mode == "live":
            rows, _ = self._page_items()
            if not self.data:
                return "**即時空中飛機**\n⚠️ 目前無法連接 ADS-B 接收站，可切換至 24 小時歷史頁查看已保存資料。", 0x3498DB
            if not self.live_items:
                return "**即時空中飛機**\n目前接收範圍內沒有具有效位置的空中飛機。", 0x3498DB
            return f"**即時空中飛機 · {len(self.live_items)} 架**\n\n" + "\n".join(self._live_line(x) for x in rows), 0x3498DB
        if self.mode == "summary":
            lines = ["**24 小時飛機觀測**", f"**{self.summary_data.get('aircraft_count', 0)} 架**航空器 · **{self.summary_data.get('country_count', 0)} 國**"]
            if self.history_items:
                item = self.history_items[0]
                lines.append(f"✨ 最近特殊機　`{_name(item)}` <t:{int(item['last_seen'])}:R>")
            if self.live_items:
                lowest = min((x for x in self.live_items if x.get("altitude") is not None), key=lambda x: x["altitude"], default=None)
                fastest = max((x for x in self.live_items if x.get("speed") is not None), key=lambda x: x["speed"], default=None)
                if lowest: lines.append(f"⬇️ 即時最低　`{_name(lowest)}` {_number(lowest['altitude'])} ft")
                if fastest: lines.append(f"💨 即時最快　`{_name(fastest)}` {_number(fastest['speed'])} kt")
            highest, farthest = self.summary_data.get("highest"), self.summary_data.get("farthest")
            if highest: lines.append(f"⬆️ 期間最高　`{highest['flight'] or highest['hex'].upper()}` {_number(highest['value'])} ft")
            if farthest: lines.append(f"📡 期間最遠　`{farthest['flight'] or farthest['hex'].upper()}` {_number(farthest['value'])} km")
            return "\n".join(lines), 0x1ABC9C
        if self.mode in {"cargo", "special"}:
            rows, _ = self._page_items()
            title = "24 小時貨機" if self.mode == "cargo" else "24 小時特殊機"
            lines = [f"**{title}** · {len(self.history_items)} 架"] + [self._history_line(x) for x in rows]
            if not rows: lines.append("目前尚無找到符合分類的紀錄。")
            return "\n\n".join(lines), 0xF39C12 if self.mode == "cargo" else 0x9B59B6
        if self.mode == "candidates":
            lines = ["**找到多架符合的飛機**", "請從下方選單選擇 ICAO hex，以免把同一呼號的不同機體混在一起。"] + [self._history_line(x) for x in self.detail_items[:10]]
            return "\n\n".join(lines), 0xF1C40F
        if not self.detail_items:
            return "**飛機詳細資料**\n找不到最近 24 小時內完全符合的呼號、註冊號或 ICAO hex。", 0xE74C3C
        item = next((x for x in self.detail_items if x["hex"] == self.selected_hex), self.detail_items[0])
        icon, state = _vertical(item.get("last_vertical_rate"))
        squawk = item.get("squawk") or "未知"
        emergency = SQUAWK_LABELS.get(squawk) or item.get("emergency")
        type_text = " / ".join(x for x in (item.get("type_code"), item.get("type_desc")) if x) or "機型未知"
        lines = [
            f"## `{_name(item)}`{_suffix(item)}",
            f"🌏 {_flag(item)} **{item.get('country') or '國籍未知'}** · `{item['hex'].upper()}` · {item.get('registration') or '註冊號未知'}",
            f"🛩️ {type_text}",
            f"⏱️ 最後出現 <t:{int(item['last_seen'])}:R> · 24h 見過 **{item.get('encounter_count', 1)} 次**",
            f"📈 最高 **{_number(item.get('period_max_altitude'))} ft** · 最快 **{_number(item.get('period_max_speed'))} kt**",
            f"📍 {self._location(item, True)}",
            f"{icon} {state} {_number(item.get('last_altitude'))} ft · {_number(item.get('last_speed'))} kt · 往{direction_8(item.get('last_track'))}",
            f"📻 應答機 `{squawk}`" + (f" · **{emergency}**" if emergency else ""),
        ]
        return "\n".join(lines), 0xE74C3C if squawk in EMERGENCY_SQUAWKS else 0x3498DB

    def _apply_structured_fields(self, embed):
        """Use Embed fields for the non-live information pages."""
        if self.mode == "summary":
            embed.description = "**24 小時飛機觀測**"
            embed.add_field(
                name="📊 觀測統計",
                value=(
                    f"航空器：**{self.summary_data.get('aircraft_count', 0)} 架**\n"
                    f"已知國籍：**{self.summary_data.get('country_count', 0)} 國**"
                ),
                inline=True,
            )
            fastest = max((item for item in self.live_items if item.get("speed") is not None), key=lambda item: item["speed"], default=None)
            farthest_live = max((item for item in self.live_items if item.get("distance_km") is not None), key=lambda item: item["distance_km"], default=None)
            embed.add_field(
                name="💨 即時最快",
                value=(
                    f"{_flag(fastest)} `{_name(fastest)}`\n速度：**{_number(fastest['speed'])} kt**"
                    if fastest else "尚無資料"
                ),
                inline=True,
            )
            embed.add_field(
                name="📡 即時最遠",
                value=(
                    f"{_flag(farthest_live)} `{_name(farthest_live)}`\n距離：**{_number(farthest_live['distance_km'])} km**"
                    if farthest_live else "尚無資料"
                ),
                inline=True,
            )
            lowest = self.summary_data.get("lowest")
            highest = self.summary_data.get("highest")
            embed.add_field(
                name="⬇️ 期間最低",
                value=(
                    f"`{lowest['flight'] or lowest['hex'].upper()}`\n高度：**{_number(lowest['value'])} ft**"
                    if lowest else "尚無資料"
                ),
                inline=True,
            )
            embed.add_field(
                name="⬆️ 期間最高",
                value=(
                    f"`{highest['flight'] or highest['hex'].upper()}`\n高度：**{_number(highest['value'])} ft**"
                    if highest else "尚無資料"
                ),
                inline=True,
            )
            return

        if self.mode in {"cargo", "special"}:
            rows, _ = self._page_items()
            title = "24 小時貨機" if self.mode == "cargo" else "24 小時特殊機"
            embed.description = f"**{title} · {len(self.history_items)} 架**"
            if not rows:
                embed.add_field(name="目前尚無資料", value="尚未找到符合分類的飛機紀錄。", inline=False)
                return
            for item in rows:
                icon = "📦" if item.get("is_cargo") else "✨"
                embed.add_field(
                    name=f"{icon} {_flag(item)} `{_name(item)}`{_suffix(item)}",
                    value=(
                        f"最後出現：<t:{int(item['last_seen'])}:R>\n"
                        f"> {self._location(item, True)}"
                    ),
                    inline=False,
                )
            return

        if self.mode != "detail" or not self.detail_items:
            return
        item = next((row for row in self.detail_items if row["hex"] == self.selected_hex), self.detail_items[0])
        icon, state = _vertical(item.get("last_vertical_rate"))
        squawk = item.get("squawk") or "未知"
        emergency = SQUAWK_LABELS.get(squawk) or item.get("emergency")
        type_text = " / ".join(value for value in (item.get("type_code"), item.get("type_desc")) if value) or "機型未知"
        embed.description = f"## {_flag(item)} `{_name(item)}`{_suffix(item)}"
        embed.add_field(
            name="🌏 航空器資料",
            value=(
                f"國籍：**{item.get('country') or '未知'}**\n"
                f"ICAO：`{item['hex'].upper()}`\n"
                f"機型：{type_text}\n"
                f"註冊號：{item.get('registration') or '未知'}"
            ),
            inline=True,
        )
        embed.add_field(
            name="⏱️ 24 小時紀錄",
            value=(
                f"最後出現：<t:{int(item['last_seen'])}:R>\n"
                f"發現次數：**{item.get('encounter_count', 1)} 次**\n"
                f"最高高度：**{_number(item.get('period_max_altitude'))} ft**\n"
                f"最快速度：**{_number(item.get('period_max_speed'))} kt**"
            ),
            inline=True,
        )
        embed.add_field(
            name="📻 應答機",
            value=f"`{squawk}`" + (f"\n**{emergency}**" if emergency else ""),
            inline=True,
        )
        embed.add_field(
            name="📍 最後位置狀態",
            value=(
                f"{self._location(item, True)}\n"
                f"{state} · {_number(item.get('last_altitude'))} ft\n"
                f"{_number(item.get('last_speed'))} kt · 往{direction_8(item.get('last_track'))}"
            ),
            inline=False,
        )

    def _mode_select(self):
        values = (("live", "即時空中", "📡"), ("summary", "24 小時總覽", "📊"), ("cargo", "24 小時貨機", "📦"), ("special", "24 小時特殊機", "✨"))
        select = Select(custom_id=self._state_id(), placeholder="切換飛機查詢頁面…", row=0, options=[discord.SelectOption(label=label, value=value, emoji=emoji, default=self.mode == value) for value, label, emoji in values])
        async def callback(interaction):
            await interaction.response.defer()
            self.mode, self.page = select.values[0], 0
            self.selected_hex = self.lookup_query = ""
            self.show_map = self.show_map and self.mode == "live"
            await self._load_mode(); await self._rebuild(); await self._edit(interaction)
        select.callback = callback
        return select

    def _aircraft_select(self, rows):
        if not rows: return None
        options = [discord.SelectOption(label=f"{_name(x)} · {x['hex'].upper()}"[:100], description=(x.get("operator_alias") or x.get("country") or "查看詳細資料")[:100], value=x["hex"]) for x in rows[:25]]
        select = Select(custom_id="adsb:aircraft", placeholder="查看飛機詳細資料…", row=2, options=options)
        async def callback(interaction):
            await interaction.response.defer()
            self.previous_mode = self.mode if self.mode in MODE_LABELS else "live"
            self.selected_hex = select.values[0]
            self.lookup_query = self.selected_hex
            self.detail_items = await self.cog.store.lookup(self.selected_hex)
            self.mode, self.show_map = "detail", False
            await self._rebuild(); await self._edit(interaction)
        select.callback = callback
        return select

    def _nav(self, max_page):
        buttons = []
        if self.mode in {"live", "cargo", "special"} and max_page > 0:
            previous, indicator, following = Button(label="上一頁", emoji="⬅️", disabled=self.page == 0, row=3), Button(label=f"{self.page + 1} / {max_page + 1}", disabled=True, row=3), Button(label="下一頁", emoji="➡️", disabled=self.page >= max_page, row=3)
            async def previous_callback(interaction):
                await interaction.response.defer(); self.page = max(0, self.page - 1); await self._rebuild(); await self._edit(interaction)
            async def next_callback(interaction):
                await interaction.response.defer(); self.page = min(max_page, self.page + 1); await self._rebuild(); await self._edit(interaction)
            previous.callback, following.callback = previous_callback, next_callback
            buttons.extend((previous, indicator, following))
        if self.mode == "live":
            map_button = Button(label="隱藏地圖" if self.show_map else "顯示地圖", emoji="🗺️", row=4)
            async def map_callback(interaction):
                await interaction.response.defer(); self.show_map = not self.show_map; await self._rebuild(); await self._edit(interaction)
            map_button.callback = map_callback; buttons.append(map_button)
        elif self.mode in {"detail", "candidates"}:
            back = Button(label="返回列表", emoji="↩️", row=4)
            async def back_callback(interaction):
                await interaction.response.defer(); self.mode, self.page = self.previous_mode, 0; self.selected_hex = self.lookup_query = ""; await self._load_mode(); await self._rebuild(); await self._edit(interaction)
            back.callback = back_callback; buttons.append(back)
        close = Button(label="關閉", style=discord.ButtonStyle.secondary, emoji="❌", row=4)
        async def close_callback(interaction):
            await interaction.response.defer()
            await interaction.delete_original_response()
            self.stop()
        close.callback = close_callback; buttons.append(close)
        return buttons

    async def _rebuild(self):
        self.clear_items(); self.map_bytes = None
        body, color = self._body()
        self.embed = discord.Embed(description=body, color=color)
        self._apply_structured_fields(self.embed)
        self.embed.set_footer(text=self._footer())
        self.add_item(self._mode_select())
        rows, max_page = ([], 0)
        if self.mode in {"live", "cargo", "special"}: rows, max_page = self._page_items()
        elif self.mode == "candidates": rows = self.detail_items[:25]
        aircraft_select = self._aircraft_select(rows)
        if aircraft_select: self.add_item(aircraft_select)
        for button in self._nav(max_page):
            self.add_item(button)
        if self.mode == "live" and self.show_map and self.data:
            self.map_bytes = await asyncio.to_thread(draw_adsb_map, self.data)
            self.embed.set_image(url="attachment://adsb_map.png")

    def file(self):
        if not self.map_bytes: return None
        self.map_bytes.seek(0)
        return discord.File(self.map_bytes, filename="adsb_map.png")

    async def _edit(self, interaction):
        file = self.file()
        await interaction.edit_original_response(content=MESSAGE_TITLE, embed=self.embed, view=self, attachments=[file] if file else [])


class AdsbCog(commands.Cog):
    def __init__(self, bot):
        self.bot, self.api_url = bot, get_config().get("ADSB_API_URL", "")
        self.store, self.airports, self._poll_failed = AdsbHistoryStore(), AirportIndex(), False

    async def cog_load(self):
        await self.store.initialize()
        if self.api_url: self.collect_adsb_loop.start()

    def cog_unload(self):
        if self.collect_adsb_loop.is_running(): self.collect_adsb_loop.cancel()

    async def fetch_data(self):
        if not self.api_url or not self.bot.session: return None
        try:
            async with self.bot.session.get(self.api_url, timeout=10) as response:
                if response.status == 200:
                    payload = await response.json()
                    return payload if isinstance(payload, dict) else None
                logger.warning("⚠️ [ADS-B] 接收站回傳 HTTP %s", response.status)
        except Exception as exc:
            logger.warning("⚠️ [ADS-B] 無法讀取接收站資料：%r", exc)
        return None

    @tasks.loop(seconds=60.0)
    async def collect_adsb_loop(self):
        data = await self.fetch_data()
        if not data:
            if not self._poll_failed: logger.warning("⚠️ [ADS-B] 背景取樣失敗，將於下一週期重試")
            self._poll_failed = True; return
        await self.store.sample(data)
        if self._poll_failed: logger.info("✅ [ADS-B] 背景取樣已恢復正常")
        self._poll_failed = False

    @collect_adsb_loop.before_loop
    async def before_collect_adsb(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="附近飛機", description="✈️ 查詢即時與最近 24 小時的 ADS-B 飛機觀測")
    @app_commands.describe(查詢="航班呼號、註冊號或 6 位 ICAO hex（選填）", 顯示地圖="是否在即時頁顯示地圖（預設不顯示）")
    @app_commands.choices(顯示地圖=[app_commands.Choice(name="顯示", value="yes"), app_commands.Choice(name="不顯示", value="no")])
    async def adsb_command(self, interaction: discord.Interaction, 查詢: str | None = None, 顯示地圖: app_commands.Choice[str] | None = None):
        if not self.api_url:
            await interaction.response.send_message("⚠️ 尚未設定 `ADSB_API_URL`，此功能目前已停用。", ephemeral=True); return
        await interaction.response.defer()
        view = AdsbView(self, interaction.user.id, show_map=bool(顯示地圖 and 顯示地圖.value == "yes" and not 查詢))
        await view.load(query=查詢)
        file = view.file()
        if file: await interaction.followup.send(content=MESSAGE_TITLE, embed=view.embed, view=view, file=file)
        else: await interaction.followup.send(content=MESSAGE_TITLE, embed=view.embed, view=view)

    @adsb_command.autocomplete("查詢")
    async def adsb_query_autocomplete(self, interaction, current):
        return [app_commands.Choice(name=name[:100], value=value[:100]) for name, value in await self.store.autocomplete(current)]

    async def refresh_message(self, interaction, message, cmd_name):
        if not self.api_url:
            await interaction.response.send_message("⚠️ 尚未設定 `ADSB_API_URL`，此功能目前已停用。", ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        if getattr(getattr(message, "flags", None), "components_v2", False):
            await interaction.followup.send("⚠️ 這是改版前的飛機訊息，請重新執行 `/附近飛機`。", ephemeral=True)
            return
        mode, page, show_map, selected_hex, lookup_query, previous_mode = "live", 0, False, "", "", "live"
        for component in _walk_components(message.components):
            custom_id = getattr(component, "custom_id", "") or ""
            if custom_id.startswith("adsb:state:"):
                parts = custom_id.split(":")
                if len(parts) == 8:
                    mode, page, show_map = parts[2], int(parts[3]) if parts[3].isdigit() else 0, parts[4] == "1"
                    selected_hex = "" if parts[5] == "-" else parts[5]
                    lookup_query = "" if parts[6] == "-" else parts[6]
                    previous_mode = parts[7]
                break
        view = AdsbView(
            self, interaction.user.id, show_map=show_map, mode=mode, page=page,
            selected_hex=selected_hex, lookup_query=lookup_query, previous_mode=previous_mode,
        )
        refresh_query = lookup_query if mode == "candidates" else (selected_hex if mode == "detail" else None)
        await view.load(query=refresh_query)
        file = view.file()
        await message.edit(content=MESSAGE_TITLE, embed=view.embed, view=view, attachments=[file] if file else [])
        await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdsbCog(bot))
