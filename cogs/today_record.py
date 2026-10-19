import asyncio
import logging
import math
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Container, LayoutView, Separator, TextDisplay

from modules.today_record_store import TAIPEI_TZ, TodayRecordStore, parse_observed_at


logger = logging.getLogger(__name__)

LIST_MODULE_SOURCES = {
    "temperature": ("TempCog", "fetch_temp_data"),
    "rainfall": ("RainfallCog", "fetch_rainfall_data"),
    "humidity": ("RelativeHumidityCog", "fetch_rh_data"),
    "pressure": ("AirPressureCog", "fetch_airpressure_data"),
    "wind": ("WindCog", "fetch_wind_data"),
}

RECORD_GROUPS = (
    (
        "🌡️ **溫度記錄**",
        (
            ("temperature_high", "最高溫", "°C"),
            ("temperature_low", "最低溫", "°C"),
        ),
    ),
    (
        "☔ **雨量記錄**",
        (
            ("rainfall", "最大累積雨量", "mm"),
            ("rainfall_low", "最低累積雨量", "mm"),
        ),
    ),
    (
        "💧 **相對濕度記錄**",
        (
            ("relative_humidity", "最高相對濕度", "%"),
            ("relative_humidity_low", "最低相對濕度", "%"),
        ),
    ),
    (
        "🎈 **氣壓記錄**",
        (
            ("air_pressure", "最高氣壓", "hPa"),
            ("air_pressure_low", "最低氣壓", "hPa"),
        ),
    ),
    (
        "💨 **風力記錄**",
        (
            ("average_wind", "最大平均風", "m/s"),
            ("gust", "最大陣風　", "m/s"),
            ("average_wind_low", "最低平均風", "m/s"),
            ("gust_low", "最低陣風　", "m/s"),
        ),
    ),
)


class TodayRecordView(LayoutView):
    """Discord Components v2 的今日氣象紀錄單卡片。"""

    def __init__(self, records):
        super().__init__(timeout=None)
        self.records = records
        self.add_item(TextDisplay("🏆 今日氣象觀測之最"))
        self.add_item(self._build_container())

    @staticmethod
    def _format_value(value, unit):
        value = float(value)
        number = (
            str(int(value))
            if unit in {"%", ""} and value.is_integer()
            else f"{value:g}"
        )
        return f"{number} {unit}".rstrip()

    @staticmethod
    def _format_time(record):
        try:
            timestamp = int(parse_observed_at(record["observed_at"]).timestamp())
            return f"<t:{timestamp}:t>"
        except (TypeError, ValueError):
            return "時間未知"

    @staticmethod
    def _format_location(record):
        location = f"{record['county']}{record['town']}" or "位置未知"
        return f"{location}（{record['station']}）"

    def _format_record(self, metric, label, unit):
        record = self.records.get(metric)
        if not record:
            return f"**尚無資料**\n> -# {label}・時間未知"

        return (
            f"**{self._format_value(record['value'], unit)}** - "
            f"{self._format_location(record)}\n"
            f"> -# {label}・{self._format_time(record)}"
        )

    @staticmethod
    def _beaufort_level(speed):
        limits = (
            0.3, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8,
            24.5, 28.5, 32.7, 37.0, 41.5, 46.2, 51.0, 56.1,
        )
        for level, limit in enumerate(limits):
            if speed < limit:
                return level
        return 17

    @staticmethod
    def _fullwidth_number(value):
        return str(value).translate(str.maketrans("0123456789", "０１２３４５６７８９"))

    def _format_wind_record(self, metric, label, unit, align_two_digits):
        record = self.records.get(metric)
        if not record:
            return "**尚無資料**", f"> -# {label}・時間未知"

        speed = float(record["value"])
        level = self._beaufort_level(speed)
        level_text = self._fullwidth_number(level)
        if align_two_digits and level < 10:
            level_text = f"　{level_text}"

        value_line = (
            f"**{level_text}級｜{self._format_value(speed, unit)}** - "
            f"{self._format_location(record)}"
        )
        detail_line = f"> -# {label}・{self._format_time(record)}"
        return value_line, detail_line

    def _build_wind_group_text(self, group_name, metrics):
        levels = [
            self._beaufort_level(float(self.records[metric]["value"]))
            for metric, _, _ in metrics
            if metric in self.records
        ]
        align_two_digits = any(level >= 10 for level in levels)
        sections = []

        for metric_group in (metrics[:2], metrics[2:]):
            values = []
            details = []
            for metric, label, unit in metric_group:
                value_line, detail_line = self._format_wind_record(
                    metric, label, unit, align_two_digits
                )
                values.append(value_line)
                details.append(detail_line)
            sections.append("\n".join(values + details))

        return f"{group_name}\n" + "\n".join(sections)

    def _build_group_text(self, group_name, metrics):
        if metrics[0][0] == "average_wind":
            return self._build_wind_group_text(group_name, metrics)

        lines = [
            self._format_record(metric, label, unit)
            for metric, label, unit in metrics
        ]
        return f"{group_name}\n" + "\n".join(lines)

    def _build_container(self):
        current_time = datetime.now(TAIPEI_TZ).strftime("%m-%d %H:%M")
        components = []

        for index, (group_name, metrics) in enumerate(RECORD_GROUPS):
            if index:
                components.append(Separator())

            group_text = self._build_group_text(group_name, metrics)
            components.append(TextDisplay(group_text))

        components.append(Separator())
        components.append(
            TextDisplay(f"-# 中央氣象署 • 查詢時間 {current_time}")
        )

        return Container(*components, accent_color=0x1ABC9C)


class TodayRecordCog(commands.Cog):
    # -------------------------------------------------------------
    # Cog 生命週期
    # -------------------------------------------------------------
    def __init__(self, bot):
        self.bot = bot
        self.store = TodayRecordStore()
        self._collect_lock = asyncio.Lock()

    async def cog_load(self):
        await self.store.initialize()
        await self.store.delete_metrics("aqi")
        self.collect_records_loop.start()

    def cog_unload(self):
        self.collect_records_loop.cancel()

    # -------------------------------------------------------------
    # 共用的極值挑選邏輯
    # -------------------------------------------------------------
    @staticmethod
    def _station_record(station, metric, value, observed_at, *, direction="max"):
        geo = station.get("GeoInfo", {})
        return {
            "metric": metric,
            "direction": direction,
            "value": value,
            "county": geo.get("CountyName", ""),
            "town": geo.get("TownName", ""),
            "station": station.get("StationName", "未知"),
            "observed_at": observed_at,
            "source": "CWA",
        }

    @staticmethod
    def _valid_float(value, predicate):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and predicate(number) else None

    def _pick_station_extreme(
        self,
        stations,
        metric,
        value_getter,
        time_getter,
        *,
        direction="max",
        predicate=lambda value: True,
    ):
        candidates = []
        today = datetime.now(TAIPEI_TZ).date()
        for station in stations:
            value = self._valid_float(value_getter(station), predicate)
            if value is None:
                continue
            time_value = time_getter(station)
            if not time_value:
                continue
            try:
                observed = parse_observed_at(time_value)
            except (TypeError, ValueError):
                continue
            if observed.date() != today:
                continue
            candidates.append((value, station, observed))
        if not candidates:
            return None
        # 最低值相同時取較早的觀測時間，特別用於 0.1 mm 雨量同值。
        if direction == "min":
            selected = min(candidates, key=lambda item: (item[0], item[2]))
        else:
            selected = max(candidates, key=lambda item: item[0])
        return self._station_record(
            selected[1], metric, selected[0], selected[2], direction=direction
        )

    async def _fetch_list_module_data(self):
        """共用各 list_*.py Cog 已有的快取 API 方法。"""
        names = []
        calls = []
        for name, (cog_name, method_name) in LIST_MODULE_SOURCES.items():
            cog = self.bot.get_cog(cog_name)
            method = getattr(cog, method_name, None) if cog else None
            if method:
                names.append(name)
                calls.append(method())

        results = await asyncio.gather(*calls, return_exceptions=True)
        data = {}
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                logger.warning("⚠️ [今日記錄] %s 資料取得失敗: %r", name, result)
            elif result:
                data[name] = result
        return data

    # -------------------------------------------------------------
    # CWA 欄位讀取工具
    # -------------------------------------------------------------
    @staticmethod
    def _get_stations(data, source_name):
        """從指定來源的 API 回應取出測站清單。"""
        return data.get(source_name, {}).get("records", {}).get("Station", [])

    @staticmethod
    def _observation_time(station):
        return station.get("ObsTime", {}).get("DateTime")

    @staticmethod
    def _weather_value(station, field_name):
        return station.get("WeatherElement", {}).get(field_name)

    @staticmethod
    def _rainfall_value(station):
        return (
            station.get("RainfallElement", {})
            .get("Now", {})
            .get("Precipitation")
        )

    @staticmethod
    def _daily_temperature_info(station, extreme_name):
        weather = station.get("WeatherElement", {})
        extreme = (
            weather.get(extreme_name)
            or weather.get("DailyExtreme", {}).get(extreme_name)
            or {}
        )
        return extreme.get("TemperatureInfo", {})

    @classmethod
    def _daily_temperature_value(cls, station, extreme_name):
        return cls._daily_temperature_info(station, extreme_name).get(
            "AirTemperature"
        )

    @classmethod
    def _daily_temperature_time(cls, station, extreme_name):
        return (
            cls._daily_temperature_info(station, extreme_name)
            .get("Occurred_at", {})
            .get("DateTime")
        )

    @staticmethod
    def _gust_value(station):
        return (
            station.get("WeatherElement", {})
            .get("GustInfo", {})
            .get("PeakGustSpeed")
        )

    @staticmethod
    def _gust_time(station):
        return (
            station.get("WeatherElement", {})
            .get("GustInfo", {})
            .get("Occurred_at", {})
            .get("DateTime")
        )

    @staticmethod
    def _records_without_empty(*records):
        return [record for record in records if record]

    # -------------------------------------------------------------
    # 各類觀測資料解析
    # -------------------------------------------------------------
    def _extract_temperature_records(self, data):
        """氣溫 API 本身已提供當日最高與最低溫。"""
        stations = self._get_stations(data, "temperature")
        highest = self._pick_station_extreme(
            stations,
            "temperature_high",
            lambda station: self._daily_temperature_value(station, "DailyHigh"),
            lambda station: self._daily_temperature_time(station, "DailyHigh"),
            predicate=lambda value: value > -90,
        )
        lowest = self._pick_station_extreme(
            stations,
            "temperature_low",
            lambda station: self._daily_temperature_value(station, "DailyLow"),
            lambda station: self._daily_temperature_time(station, "DailyLow"),
            direction="min",
            predicate=lambda value: value > -90,
        )
        return self._records_without_empty(highest, lowest)

    def _extract_rainfall_records(self, data):
        """雨量排除 0 mm；最低雨量從 0.1 mm 起計。"""
        stations = self._get_stations(data, "rainfall")
        highest = self._pick_station_extreme(
            stations,
            "rainfall",
            self._rainfall_value,
            self._observation_time,
            predicate=lambda value: value > 0,
        )
        lowest = self._pick_station_extreme(
            stations,
            "rainfall_low",
            self._rainfall_value,
            self._observation_time,
            direction="min",
            predicate=lambda value: value >= 0.1,
        )
        return self._records_without_empty(highest, lowest)

    def _extract_humidity_records(self, data):
        stations = self._get_stations(data, "humidity")

        def get_humidity(station):
            return self._weather_value(station, "RelativeHumidity")

        def is_valid(value):
            return 0 <= value <= 100

        highest = self._pick_station_extreme(
            stations,
            "relative_humidity",
            get_humidity,
            self._observation_time,
            predicate=is_valid,
        )
        lowest = self._pick_station_extreme(
            stations,
            "relative_humidity_low",
            get_humidity,
            self._observation_time,
            direction="min",
            predicate=is_valid,
        )
        return self._records_without_empty(highest, lowest)

    def _extract_pressure_records(self, data):
        stations = self._get_stations(data, "pressure")

        def get_pressure(station):
            return self._weather_value(station, "AirPressure")

        def is_valid(value):
            return value > 0

        highest = self._pick_station_extreme(
            stations,
            "air_pressure",
            get_pressure,
            self._observation_time,
            predicate=is_valid,
        )
        lowest = self._pick_station_extreme(
            stations,
            "air_pressure_low",
            get_pressure,
            self._observation_time,
            direction="min",
            predicate=is_valid,
        )
        return self._records_without_empty(highest, lowest)

    def _extract_wind_records(self, data):
        stations = self._get_stations(data, "wind")
        average_wind = self._pick_station_extreme(
            stations,
            "average_wind",
            lambda station: self._weather_value(station, "WindSpeed"),
            self._observation_time,
            predicate=lambda value: value >= 0,
        )
        gust = self._pick_station_extreme(
            stations,
            "gust",
            self._gust_value,
            self._gust_time,
            predicate=lambda value: value >= 0,
        )
        average_wind_low = self._pick_station_extreme(
            stations,
            "average_wind_low",
            lambda station: self._weather_value(station, "WindSpeed"),
            self._observation_time,
            direction="min",
            predicate=lambda value: value >= 0,
        )
        gust_low = self._pick_station_extreme(
            stations,
            "gust_low",
            self._gust_value,
            self._gust_time,
            direction="min",
            predicate=lambda value: value >= 0,
        )
        return self._records_without_empty(
            average_wind,
            gust,
            average_wind_low,
            gust_low,
        )

    def _extract_cwa_records(self, data):
        """將各類測站資料整理成可寫入 SQLite 的當日極值。"""
        return (
            self._extract_temperature_records(data)
            + self._extract_rainfall_records(data)
            + self._extract_humidity_records(data)
            + self._extract_pressure_records(data)
            + self._extract_wind_records(data)
        )

    # -------------------------------------------------------------
    # 資料庫更新與每小時取樣
    # -------------------------------------------------------------
    async def collect_records(self):
        async with self._collect_lock:
            data = await self._fetch_list_module_data()
            records = self._extract_cwa_records(data)
            await self.store.upsert(records)
            return records

    async def record_list_data(self, source_name, data):
        """在 list_*.py 指令成功查詢時，將該模組資料順便寫入極值庫。"""
        if source_name not in LIST_MODULE_SOURCES:
            return
        async with self._collect_lock:
            records = self._extract_cwa_records({source_name: data})
            await self.store.upsert(records)

    @tasks.loop(hours=1.0)
    async def collect_records_loop(self):
        try:
            await self.collect_records()
        except Exception:
            logger.exception("❌ [今日記錄] 背景更新失敗")

    @collect_records_loop.before_loop
    async def before_collect_records(self):
        await self.bot.wait_until_ready()

    # -------------------------------------------------------------
    # Discord Components v2 畫面組裝
    # -------------------------------------------------------------
    async def _build_today_record_view(self):
        records = await self.store.get_day()
        if not records:
            return None
        return TodayRecordView(records)

    # -------------------------------------------------------------
    # Discord 指令與訊息重新整理
    # -------------------------------------------------------------
    @app_commands.command(
        name="今日氣象記錄",
        description="🏆 查詢今日綜合氣象記錄看板 Today Records",
    )
    async def today_record_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            view = await self._build_today_record_view()
            if not view:
                await interaction.followup.send(
                    "⚠️ 今日尚未完成取樣，請稍後再試。"
                )
                return
            await interaction.followup.send(view=view)
        except Exception as exc:
            await interaction.followup.send(f"❌ 發生未預期的錯誤：{exc!r}")
            logger.exception("❌ /今日氣象記錄 發生未預期的錯誤")

    async def refresh_message(
        self, interaction: discord.Interaction, message: discord.Message, cmd_name: str
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            view = await self._build_today_record_view()
            if not view:
                await interaction.followup.send(
                    "⚠️ 今日尚未完成取樣。", ephemeral=True
                )
                return
            await message.edit(content=None, embed=None, view=view)
            await interaction.followup.send("✅ 資料已重新整理！", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(
                f"❌ 發生未預期的錯誤：{exc!r}", ephemeral=True
            )
            logger.exception("❌ refresh_message (TodayRecordCog) 發生錯誤")


async def setup(bot):
    await bot.add_cog(TodayRecordCog(bot))
