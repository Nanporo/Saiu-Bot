import discord
from discord.ext import commands
from discord import app_commands
import logging
import math
import re
import unicodedata

logger = logging.getLogger(__name__)


class ConversionError(ValueError):
    """可顯示給使用者的換算錯誤。"""


UNIT_ALIASES = {
    "ms": "ms", "m/s": "ms", "mps": "ms", "公尺/秒": "ms", "公尺每秒": "ms",
    "kmh": "kmh", "km/h": "kmh", "kph": "kmh", "公里/小時": "kmh", "公里每小時": "kmh",
    "mph": "mph", "mi/h": "mph", "mile/h": "mph", "英里/小時": "mph", "英里每小時": "mph",
    "kt": "kts", "kts": "kts", "knot": "kts", "knots": "kts",
    "kont": "kts", "konts": "kts", "節": "kts",
    "bft": "bft", "beaufort": "bft",
    "c": "c", "°c": "c", "℃": "c", "攝氏": "c",
    "f": "f", "°f": "f", "℉": "f", "華氏": "f",
    "k": "k", "°k": "k", "絕對溫度": "k",
    "hpa": "hpa", "百帕": "hpa", "inhg": "inhg", "英吋汞柱": "inhg",
    "mmhg": "mmhg", "毫米汞柱": "mmhg", "atm": "atm", "標準大氣壓": "atm",
}

_NUMBER_PATTERN = r"[+-]?(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
_UNIT_PATTERN = (
    r"(?:公里每小時|公尺每秒|英里每小時|標準大氣壓|英吋汞柱|毫米汞柱|絕對溫度|"
    r"beaufort|knots?|konts?|km\s*/\s*h|mi\s*/\s*h|mile\s*/\s*h|m\s*/\s*s|"
    r"kmh|kph|mph|mps|kts?|bft|hpa|inhg|mmhg|atm|公里\s*/\s*小時|公尺\s*/\s*秒|英里\s*/\s*小時|"
    r"°\s*[cfk]|℃|℉|攝氏|華氏|百帕|節)"
)
_RANGE_RE = re.compile(
    rf"(?P<start>{_NUMBER_PATTERN})\s*(?P<unit1>{_UNIT_PATTERN})?\s*"
    rf"(?:~|至|到|或|or|–|—|-)\s*"
    rf"(?P<end>{_NUMBER_PATTERN})\s*(?P<unit2>{_UNIT_PATTERN})",
    re.IGNORECASE,
)
_VALUE_RE = re.compile(rf"(?P<value>{_NUMBER_PATTERN})\s*(?P<unit>{_UNIT_PATTERN})", re.IGNORECASE)


def normalize_unit(unit: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", unit).lower()
    return UNIT_ALIASES.get(re.sub(r"\s+", "", normalized))


def extract_conversions(text: str) -> list[tuple[float, float | None, str]]:
    """從訊息擷取 (起始值, 結束值, 單位)，先辨識範圍再處理單值。"""
    normalized_text = unicodedata.normalize("NFKC", text)
    results = []
    occupied = []
    for match in _RANGE_RE.finditer(normalized_text):
        unit1 = normalize_unit(match.group("unit1")) if match.group("unit1") else None
        unit2 = normalize_unit(match.group("unit2"))
        if unit1 and unit1 != unit2:
            continue
        results.append((match.start(), (
            float(match.group("start").replace(",", "")),
            float(match.group("end").replace(",", "")),
            unit2,
        )))
        occupied.append(match.span())
    for match in _VALUE_RE.finditer(normalized_text):
        if any(start <= match.start() and match.end() <= end for start, end in occupied):
            continue
        results.append((match.start(), (
            float(match.group("value").replace(",", "")),
            None,
            normalize_unit(match.group("unit")),
        )))
    return [result for _, result in sorted(results, key=lambda item: item[0])]


def _range_text(start: float, end: float | None, digits: int, suffix: str = "") -> str:
    if end is None or math.isclose(start, end):
        return f"{start:.{digits}f}{suffix}"
    low, high = sorted((start, end))
    return f"{low:.{digits}f}～{high:.{digits}f}{suffix}"


def build_conversion(value: float, unit: str, end_value: float | None = None) -> tuple[str, str]:
    """產生與 `/換算` 相同格式的標題與內容，並可處理數值範圍。"""
    values = (value,) if end_value is None else (value, end_value)
    if any(not math.isfinite(item) for item in values):
        raise ConversionError("⚠️ 請輸入有效的數字！")

    if unit in {"ms", "kmh", "mph", "kts", "bft"}:
        if any(item < 0 for item in values):
            raise ConversionError("⚠️ 風速不能為負數！請輸入大於或等於 0 的數值。")
        if unit == "bft" and any(item >= 18 for item in values):
            raise ConversionError("⚠️ 蒲氏風級最高只到 17 級！請輸入小於 18 的數值。")

        def to_ms(item):
            if unit == "ms": return item
            if unit == "kts": return item * 0.514444
            if unit == "kmh": return item / 3.6
            if unit == "mph": return item * 0.44704
            return 0.836 * (item ** 1.5)

        ms_values = tuple(to_ms(item) for item in values)
        if any(item > 10000 for item in ms_values):
            raise ConversionError("⚠️ 輸入的風速太誇張了！你家在海王星上面嗎？（海王星最高風速約為 600 m/s）")
        kts = tuple(item / 0.514444 for item in ms_values)
        kmh = tuple(item * 3.6 for item in ms_values)
        mph = tuple(item / 0.44704 for item in ms_values)
        bft = tuple((item / 0.836) ** (2 / 3) if item > 0 else 0 for item in ms_values)
        rounded_bft = tuple(round(item) for item in bft)
        if all(item >= 18 for item in rounded_bft):
            bft_text = "17 級以上"
        elif len(rounded_bft) == 2 and max(rounded_bft) >= 18:
            bft_text = f"{min(rounded_bft):.0f} 級～17 級以上"
        else:
            bft_text = _range_text(bft[0], bft[1] if len(bft) == 2 else None, 0, " 級")
        description = (
            f"公尺每秒　 `{_range_text(ms_values[0], ms_values[1] if len(ms_values) == 2 else None, 2)} m/s`\n"
            f"公里每小時 `{_range_text(kmh[0], kmh[1] if len(kmh) == 2 else None, 2)} km/h`\n"
            f"英里每小時 `{_range_text(mph[0], mph[1] if len(mph) == 2 else None, 2)} mph`\n"
            f"節　　　　 `{_range_text(kts[0], kts[1] if len(kts) == 2 else None, 2)} kts`\n"
            f"蒲氏風級　 `{bft_text}`"
        )
        return "💨 風速換算結果", description

    if unit in {"c", "f", "k"}:
        limits = {"c": -273.15, "f": -459.67, "k": 0}
        if any(item < limits[unit] for item in values):
            labels = {"c": "-273.15 °C", "f": "-459.67 °F", "k": "0 K"}
            raise ConversionError(f"⚠️ 溫度不能低於絕對零度 ({labels[unit]})！")

        def to_c(item):
            if unit == "c": return item
            if unit == "f": return (item - 32) * 5 / 9
            return item - 273.15

        c_values = tuple(to_c(item) for item in values)
        if any(item > 20000000 for item in c_values):
            raise ConversionError("⚠️ 輸入的溫度太高了！你正在太陽裡面核聚變嗎？（太陽核心溫度約一千五百萬度）")
        f_values = tuple(item * 9 / 5 + 32 for item in c_values)
        k_values = tuple(item + 273.15 for item in c_values)
        description = (
            f"攝氏　　 `{_range_text(c_values[0], c_values[1] if len(c_values) == 2 else None, 2)}°C`\n"
            f"華氏　　 `{_range_text(f_values[0], f_values[1] if len(f_values) == 2 else None, 2)}°F`\n"
            f"絕對溫度 `{_range_text(k_values[0], k_values[1] if len(k_values) == 2 else None, 2)}K`"
        )
        return "🌡️ 溫度換算結果", description

    if unit in {"hpa", "inhg", "mmhg", "atm"}:
        if any(item < 0 for item in values):
            raise ConversionError("⚠️ 氣壓不能為負數！請輸入大於或等於 0 的數值。")

        def to_hpa(item):
            if unit == "hpa": return item
            if unit == "inhg": return item * 33.8639
            if unit == "mmhg": return item * 1.33322
            return item * 1013.25

        hpa_values = tuple(to_hpa(item) for item in values)
        if any(item > 100000000000 for item in hpa_values):
            raise ConversionError("⚠️ 輸入的氣壓太大了！你住在木星核心裡面嗎？（約一千億百帕）")
        inhg = tuple(item / 33.8639 for item in hpa_values)
        mmhg = tuple(item / 1.33322 for item in hpa_values)
        atm = tuple(item / 1013.25 for item in hpa_values)
        description = (
            f"百帕　　　 `{_range_text(hpa_values[0], hpa_values[1] if len(hpa_values) == 2 else None, 2)} hPa`\n"
            f"英吋汞柱　 `{_range_text(inhg[0], inhg[1] if len(inhg) == 2 else None, 2)} inHg`\n"
            f"毫米汞柱　 `{_range_text(mmhg[0], mmhg[1] if len(mmhg) == 2 else None, 2)} mmHg`\n"
            f"標準大氣壓 `{_range_text(atm[0], atm[1] if len(atm) == 2 else None, 4)} atm`"
        )
        return "📉 氣壓換算結果", description

    raise ConversionError("⚠️ 無法辨識這個單位。")

class CalculatorCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="換算", description="🧮 氣象單位換算器 (風速、溫度、氣壓) Convert")
    @app_commands.describe(value="要換算的數值", unit="該數值的單位")
    @app_commands.choices(unit=[
        app_commands.Choice(name="💨 風速 m/s (公尺/秒)", value="ms"),
        app_commands.Choice(name="💨 風速 km/h (公里/小時)", value="kmh"),
        app_commands.Choice(name="💨 風速 mph (英里/小時)", value="mph"),
        app_commands.Choice(name="💨 風速 kts (節)", value="kts"),
        app_commands.Choice(name="💨 風速 蒲氏風級", value="bft"),
        app_commands.Choice(name="🌡️ 溫度 °C (攝氏)", value="c"),
        app_commands.Choice(name="🌡️ 溫度 °F (華氏)", value="f"),
        app_commands.Choice(name="🌡️ 溫度 K (絕對溫度)", value="k"),
        app_commands.Choice(name="📉 氣壓 hPa (百帕)", value="hpa"),
        app_commands.Choice(name="📉 氣壓 inHg (英吋汞柱)", value="inhg"),
        app_commands.Choice(name="📉 氣壓 mmHg (毫米汞柱)", value="mmhg"),
        app_commands.Choice(name="📉 氣壓 atm (標準大氣壓)", value="atm")
    ])
    async def convert(self, interaction: discord.Interaction, value: float, unit: app_commands.Choice[str]):
        try:
            title, description = build_conversion(value, unit.value)
        except ConversionError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        embed = discord.Embed(description=description, color=0x41809b)
        await interaction.response.send_message(content=title, embed=embed)

async def setup(bot):
    await bot.add_cog(CalculatorCog(bot))
