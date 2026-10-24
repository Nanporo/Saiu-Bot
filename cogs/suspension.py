import asyncio
import io
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

# 只共用資料解析結果；自動告警模組不會建立或發送地圖。
from cogs.alarm.alert_suspension import (
    COUNTIES,
    get_status_emoji,
    is_no_announcement_status,
)

logger = logging.getLogger(__name__)

TAIPEI_TZ = timezone(timedelta(hours=8))
TOPO_PATH = Path(__file__).resolve().parent.parent / "maps" / "towns-mercator-10t.json"
FONT_PATH = Path(__file__).resolve().parent.parent / "fonts" / "Noto_Sans_TC" / "NotoSansTC-Regular.ttf"

STATUS_COLORS = {
    "normal": "#3498db",
    "suspended": "#e74c3c",
    "partial": "#f1c40f",
}
TOWN_BORDER_COLOR = "#c4cbd2"
COUNTY_BORDER_COLOR = "#aeb8c2"

_LOCAL_DETAIL_WORDS = (
    "村", "里", "學校", "國小", "小學", "國中", "中學", "高中", "高職",
    "農工", "高商", "大學", "專科", "幼兒園", "分校", "托兒所", "進修部",
)


def _normalize_place_name(name: str) -> str:
    return str(name or "").replace("台", "臺").replace(" ", "")


def _current_day_text(text: str, query_time: datetime) -> str:
    """只取得公告中屬於查詢當日的段落。

    人事總處有時會在同一行同時列出今日與明日，地圖不應讓明日
    狀態覆蓋今日顏色。未帶日期標記的文字視為當日公告。
    """
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return ""

    marker_pattern = re.compile(r"今天|今日|明天|明日|\d{1,2}月\d{1,2}日")
    markers = list(marker_pattern.finditer(compact))
    if not markers:
        return compact

    current_date_marker = f"{query_time.month}月{query_time.day}日"
    selected = []
    for index, match in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(compact)
        marker = match.group(0)
        if marker in ("今天", "今日", current_date_marker):
            selected.append(compact[match.start():end])
    return "".join(selected)


def _classify_current_status(text: str, query_time: datetime) -> str | None:
    """將單段公告分為藍、紅、黃三種地圖狀態。"""
    if is_no_announcement_status(text):
        return "normal"

    current = _current_day_text(text, query_time)
    if not current:
        return None

    normal_patterns = (
        r"未達停止上班及上課標準",
        r"未達停止上班(?:及|、|與)上課標準",
        r"(?:正常|照常)上班(?:、|及|與)(?:正常|照常)?上課",
    )
    if any(re.search(pattern, current) for pattern in normal_patterns):
        return "normal"

    stopped_work = bool(re.search(r"停止上班", current))
    stopped_school = bool(re.search(r"停止上課", current))
    reaches_stop_standard = bool(
        re.search(r"已達停止上班(?:及|、|與)上課標準", current)
    )

    if reaches_stop_standard or (stopped_work and stopped_school):
        return "suspended"
    if stopped_work or stopped_school:
        return "partial"
    return "partial"


def _decode_topology_arcs(topo: dict) -> list[list[tuple[float, float]]]:
    """解碼 TopoJSON 共用邊界線段。"""
    scale = topo["transform"]["scale"]
    translate = topo["transform"]["translate"]
    decoded_arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        decoded = []
        for dx, dy in arc:
            x += dx
            y += dy
            decoded.append((x * scale[0] + translate[0], y * scale[1] + translate[1]))
        decoded_arcs.append(decoded)
    return decoded_arcs


def _decode_geometry_rings(
    geometry: dict,
    decoded_arcs: list[list[tuple[float, float]]],
) -> list[list[tuple[float, float]]]:
    rings = []
    arc_groups = (
        geometry["arcs"]
        if geometry["type"] == "Polygon"
        else [ring for polygon in geometry["arcs"] for ring in polygon]
    )
    for ring in arc_groups:
        points = []
        for arc_index in ring:
            arc = (
                decoded_arcs[~arc_index][::-1]
                if arc_index < 0
                else decoded_arcs[arc_index]
            )
            points.extend(arc)
        if points:
            rings.append(points)
    return rings


def _decode_topology_towns(topo: dict) -> list[dict]:
    """解碼現有 TopoJSON 的所有鄉鎮市區，包含金門與連江幾何。"""
    decoded_arcs = _decode_topology_arcs(topo)

    towns = []
    for geometry in topo["objects"]["towns"]["geometries"]:
        properties = geometry.get("properties", {})
        towns.append({
            "county": _normalize_place_name(properties.get("COUNTYNAME", "")),
            "town": _normalize_place_name(properties.get("TOWNNAME", "")),
            "rings": _decode_geometry_rings(geometry, decoded_arcs),
        })
    return towns


def _decode_topology_counties(topo: dict) -> list[dict]:
    """解碼縣市邊界，供地圖在鄉鎮塊面上另行疊加粗線。"""
    decoded_arcs = _decode_topology_arcs(topo)
    return [
        {
            "county": _normalize_place_name(
                geometry.get("properties", {}).get("COUNTYNAME", "")
            ),
            "rings": _decode_geometry_rings(geometry, decoded_arcs),
        }
        for geometry in topo["objects"]["counties"]["geometries"]
    ]


def _relocate_penghu_to_geographic_position(towns: list[dict], counties: list[dict]):
    """將 TopoJSON 中為了嵌圖而下移的澎湖，搬回臺灣海峽的真實相對位置。"""
    excluded = {"澎湖縣", "金門縣", "連江縣"}
    main_points = [
        point
        for item in towns
        if item["county"] not in excluded
        for ring in item["rings"]
        for point in ring
    ]
    penghu_points = [
        point
        for item in towns
        if item["county"] == "澎湖縣"
        for ring in item["rings"]
        for point in ring
    ]
    if not main_points or not penghu_points:
        return

    min_x = min(x for x, _ in main_points)
    max_x = max(x for x, _ in main_points)
    min_y = min(y for _, y in main_points)
    max_y = max(y for _, y in main_points)
    source_cx = (min(x for x, _ in penghu_points) + max(x for x, _ in penghu_points)) / 2
    source_cy = (min(y for _, y in penghu_points) + max(y for _, y in penghu_points)) / 2

    wgs_min_lon, wgs_max_lon = 120.036, 122.001
    wgs_min_lat, wgs_max_lat = 21.896, 25.300
    target_cx = min_x + (119.508 - wgs_min_lon) / (wgs_max_lon - wgs_min_lon) * (max_x - min_x)

    def mercator_y(latitude: float) -> float:
        return math.log(math.tan(math.pi / 4 + latitude * math.pi / 360))

    mercator_max = mercator_y(wgs_max_lat)
    mercator_min = mercator_y(wgs_min_lat)
    target_mercator = mercator_y(23.479)
    target_cy = min_y + (mercator_max - target_mercator) / (mercator_max - mercator_min) * (max_y - min_y)
    offset_x = target_cx - source_cx
    offset_y = target_cy - source_cy

    for collection in (towns, counties):
        for item in collection:
            if item["county"] != "澎湖縣":
                continue
            item["rings"] = [
                [(x + offset_x, y + offset_y) for x, y in ring]
                for ring in item["rings"]
            ]


def _build_town_statuses(
    data: dict[str, str],
    towns: list[dict],
    query_time: datetime,
) -> dict[tuple[str, str], str]:
    """將縣市公告展開為鄉鎮市區狀態，並讓局部公告覆蓋縣市狀態。"""
    county_towns: dict[str, list[str]] = {}
    for item in towns:
        county_towns.setdefault(item["county"], []).append(item["town"])

    statuses = {
        (item["county"], item["town"]): "normal"
        for item in towns
    }

    # 先處理縣市層級，再處理名稱較長的鄉鎮／學校範圍，
    # 使局部公告不受原始 HTML 列順序影響。
    sorted_announcements = sorted(
        data.items(),
        key=lambda item: len(_normalize_place_name(item[0])),
    )
    for raw_county, raw_info in sorted_announcements:
        normalized_scope = _normalize_place_name(raw_county)
        county = next(
            (
                known_county
                for known_county in county_towns
                if normalized_scope.startswith(known_county)
            ),
            normalized_scope,
        )
        known_towns = county_towns.get(county, [])
        if not known_towns:
            continue
        scope_towns = [town for town in known_towns if town and town in normalized_scope]

        info = str(raw_info or "")
        if is_no_announcement_status(info):
            continue

        lines = [line.strip() for line in info.splitlines() if line.strip()] or [info]
        county_status = None
        overrides: dict[str, str] = {}
        unresolved_local_notice = False

        for line in lines:
            status = _classify_current_status(line, query_time)
            if status is None:
                continue

            target = re.split(r"[:：]", line, maxsplit=1)[0]
            normalized_target = _normalize_place_name(target)
            matched_towns = [
                town
                for town in known_towns
                if town and (
                    town in normalized_target
                    or (len(town) >= 3 and town[:-1] in normalized_target)
                )
            ]
            if not matched_towns:
                matched_towns = scope_towns

            # 先移除已辨識的鄉鎮名，避免「里港鄉」的「里」被誤當成
            # 村里層級公告。學校關鍵字可能出現在冒號後，故另外檢查全行。
            residual_target = normalized_target
            for town in matched_towns:
                residual_target = residual_target.replace(town, "")
            school_words = _LOCAL_DETAIL_WORDS[2:]
            is_local_detail = any(word in residual_target for word in _LOCAL_DETAIL_WORDS)
            is_local_detail = is_local_detail or any(word in line for word in school_words)
            is_local_detail = is_local_detail or bool(
                re.search(r"(?:部分地區|下列地區|除.+外)", line)
            )

            if matched_towns:
                town_status = "partial" if is_local_detail and status != "normal" else status
                for town in matched_towns:
                    overrides[town] = town_status
            elif is_local_detail:
                # 無法從現有鄉鎮幾何資料定位學校／村里時，以全縣市黃色
                # 保守呈現，避免誤標為正常。
                unresolved_local_notice = True
            else:
                county_status = status

        if county_status:
            for town in known_towns:
                statuses[(county, town)] = county_status
        if unresolved_local_notice:
            for town in known_towns:
                statuses[(county, town)] = "partial"
        for town, status in overrides.items():
            statuses[(county, town)] = status

    return statuses


def _load_font(size: int):
    candidates = (
        FONT_PATH,
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("C:/Windows/Fonts/msjh.ttc"),
    )
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def render_suspension_map(data: dict[str, str], query_time: datetime) -> io.BytesIO:
    """以鄉鎮市區塊面繪製當日停班停課地圖。

    此渲染器獨立使用完整 TopoJSON，故金門、連江只會在這張地圖顯示，
    不會改變其他地圖模組既有的排除行為。
    """
    with TOPO_PATH.open("r", encoding="utf-8") as file:
        topo = json.load(file)
    towns = _decode_topology_towns(topo)
    counties = _decode_topology_counties(topo)
    _relocate_penghu_to_geographic_position(towns, counties)

    statuses = _build_town_statuses(data, towns, query_time)
    all_points = [point for item in towns for ring in item["rings"] for point in ring]
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)

    image_width = 920
    image_height = 1060
    # 比照 EEW 920x1060 地圖，地圖幾乎佔滿整張圖的高度；
    # 資訊與圖例放進兩側留白，不再額外壓縮臺灣本體。
    map_top = 36
    map_bottom = 1030
    side_padding = 28
    scale = min(
        (image_width - side_padding * 2) / (max_x - min_x),
        (map_bottom - map_top) / (max_y - min_y),
    )
    rendered_map_width = (max_x - min_x) * scale
    map_left = (image_width - rendered_map_width) / 2

    def map_to_image(x: float, y: float) -> tuple[float, float]:
        return (
            map_left + (x - min_x) * scale,
            map_top + (y - min_y) * scale,
        )

    image = Image.new("RGB", (image_width, image_height), "#0f1113")
    draw = ImageDraw.Draw(image)
    title_font = _load_font(48)
    footer_font = _load_font(26)
    legend_title_font = _load_font(22)
    legend_font = _load_font(20)

    draw.text((25, 25), "臺灣停班停課狀況", fill="#ffffff", font=title_font)

    for item in towns:
        status = statuses.get((item["county"], item["town"]), "normal")
        fill_color = STATUS_COLORS[status]
        for ring in item["rings"]:
            pixels = [map_to_image(x, y) for x, y in ring]
            if len(pixels) >= 3:
                # 鄉鎮界使用較柔和的淺灰白色，維持 1px。
                draw.polygon(
                    pixels,
                    fill=fill_color,
                    outline=TOWN_BORDER_COLOR,
                    width=1,
                )

    # 縣市界使用較亮的灰白色，以 2px 與鄉鎮界明確區分。
    for county in counties:
        for ring in county["rings"]:
            pixels = [map_to_image(x, y) for x, y in ring]
            if len(pixels) >= 2:
                draw.line(pixels, fill=COUNTY_BORDER_COLOR, width=2)

    info_text = (
        "Generated by Saiu-Bot\n"
        f"查詢時間 {query_time.strftime('%Y-%m-%d %H-%M:%S')}\n"
        "僅供示意，請依照政府機關公告為準"
    )
    info_bbox = draw.multiline_textbbox((0, 0), info_text, font=footer_font, spacing=4)
    info_height = info_bbox[3] - info_bbox[1]
    draw.multiline_text(
        (25, image_height - info_height - 25),
        info_text,
        fill="#cccccc",
        font=footer_font,
        spacing=4,
    )

    legend_items = (
        ("上班上課", STATUS_COLORS["normal"]),
        ("停班止課", STATUS_COLORS["suspended"]),
        ("其它", STATUS_COLORS["partial"]),
    )
    # 比照 EEW：方形背景框、圓形色點與右下角縱向圖例。
    legend_width = 145
    legend_height = 140
    legend_x = image_width - legend_width - 20
    legend_y = image_height - legend_height - 20
    draw.rectangle(
        (legend_x, legend_y, legend_x + legend_width, legend_y + legend_height),
        fill="#0d0e11",
        outline="#292e33",
        width=1,
    )
    title_bbox = draw.textbbox((0, 0), "狀態", font=legend_title_font)
    title_width = title_bbox[2] - title_bbox[0]
    draw.text(
        (legend_x + (legend_width - title_width) / 2, legend_y + 10),
        "狀態",
        fill="#aaaaaa",
        font=legend_title_font,
    )
    for index, (label, color) in enumerate(legend_items):
        row_y = legend_y + 48 + index * 29
        draw.ellipse(
            (legend_x + 18, row_y, legend_x + 34, row_y + 16),
            fill=color,
            outline="#404040",
            width=1,
        )
        draw.text(
            (legend_x + 47, row_y + 8),
            label,
            fill="#e5e5e5",
            font=legend_font,
            anchor="lm",
        )

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


class SuspensionView(discord.ui.View):
    def __init__(
        self,
        data: dict[str, str],
        county: str | None,
        author_id: int,
        show_map: bool,
        query_time: datetime,
    ):
        super().__init__(timeout=300)
        self.data = data
        self.county = county
        self.author_id = author_id
        self.show_map = show_map
        self.query_time = query_time
        self.map_bytes = None
        self.update_button()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ 這個按鈕只能由原指令使用者操作！",
                ephemeral=True,
            )
            return False
        return True

    def update_button(self):
        self.toggle_map.label = "隱藏地圖" if self.show_map else "顯示地圖"
        self.toggle_map.style = (
            discord.ButtonStyle.secondary if self.show_map else discord.ButtonStyle.secondary
        )

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(title="", color=0x3498DB)

        if self.county:
            info = next(
                (
                    value
                    for key, value in self.data.items()
                    if _normalize_place_name(self.county) in _normalize_place_name(key)
                ),
                None,
            )
            if info:
                status_emoji = get_status_emoji(info)
                status = _classify_current_status(info, self.query_time)
                embed.color = 0x2ECC71 if status == "normal" else 0xE74C3C
                embed.description = f"`{status_emoji}` **{self.county}** 最新宣布：\n\n{info}"
            else:
                embed.description = f"❌ 找不到 **{self.county}** 的資料。"
        else:
            suspended = [
                (county, info)
                for county, info in self.data.items()
                if _classify_current_status(info, self.query_time) != "normal"
                and not is_no_announcement_status(info)
            ]
            if suspended:
                embed.color = 0xE74C3C
                embed.description = "目前有發布**停班停課**或**特殊狀況**的縣市如下："
                for county, info in suspended:
                    display_info = info if len(info) < 200 else info[:197] + "..."
                    embed.add_field(
                        name=f"`{get_status_emoji(info)}` {county}",
                        value=display_info,
                        inline=False,
                    )
            else:
                embed.color = 0x2ECC71
                embed.description = "✅ **目前全臺各縣市皆為「照常上班、照常上課」。**"

        current_time = self.query_time.strftime("%Y-%m-%d %H:%M")
        embed.set_footer(
            text=f"行政院人事行政總處 • 查詢時間 {current_time}",
            icon_url="https://raw.githubusercontent.com/Nanporo/Saiu-Bot/main/photos/dgpa_logo.png",
        )
        if self.show_map:
            embed.set_image(url="attachment://suspension_map.png")
        return embed

    async def build_response(self):
        embed = self.build_embed()
        file = None
        if self.show_map:
            if self.map_bytes is None:
                buffer = await asyncio.to_thread(
                    render_suspension_map,
                    self.data,
                    self.query_time,
                )
                self.map_bytes = buffer.getvalue()
            file = discord.File(io.BytesIO(self.map_bytes), filename="suspension_map.png")
        return "🎒 停班停課資訊", embed, file

    @discord.ui.button(label="顯示地圖", style=discord.ButtonStyle.primary)
    async def toggle_map(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.show_map = not self.show_map
        self.update_button()
        content, embed, file = await self.build_response()
        await interaction.edit_original_response(
            content=content,
            embed=embed,
            view=self,
            attachments=[file] if file else [],
        )


class SuspensionManualCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="停班停課", description="🎒 手動查詢人事行政總處的停班停課資訊 Suspension")
    @app_commands.describe(
        縣市="選擇要查詢的縣市 (未選擇則列出目前有停班課的縣市)",
        顯示地圖="是否顯示當日停班停課地圖 (預設不顯示)",
    )
    @app_commands.choices(
        縣市=[app_commands.Choice(name=county, value=county) for county in COUNTIES],
        顯示地圖=[
            app_commands.Choice(name="顯示", value="yes"),
            app_commands.Choice(name="不顯示", value="no"),
        ],
    )
    async def suspension_query(
        self,
        interaction: discord.Interaction,
        縣市: app_commands.Choice[str] = None,
        顯示地圖: app_commands.Choice[str] = None,
    ):
        await interaction.response.defer()

        alert_cog = self.bot.get_cog("SuspensionAlertCog")
        if not alert_cog:
            await interaction.followup.send("❌ 停班停課警報模組尚未載入，無法查詢。")
            return

        data = await alert_cog.fetch_data()
        if data is None:
            await interaction.followup.send("❌ 目前無法取得人事行政總處的資料，請稍後再試。")
            return

        query_time = datetime.now(TAIPEI_TZ)
        view = SuspensionView(
            data=data,
            county=縣市.value if 縣市 else None,
            author_id=interaction.user.id,
            show_map=顯示地圖 is not None and 顯示地圖.value == "yes",
            query_time=query_time,
        )
        content, embed, file = await view.build_response()
        await interaction.followup.send(
            content=content,
            embed=embed,
            view=view,
            file=file if file else discord.utils.MISSING,
        )


async def setup(bot):
    await bot.add_cog(SuspensionManualCog(bot))
