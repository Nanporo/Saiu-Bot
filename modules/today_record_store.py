"""SQLite storage for sampled daily weather records."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping


TAIPEI_TZ = timezone(timedelta(hours=8))
DEFAULT_DB_PATH = Path("data/today_records.db")
RETENTION_DAYS = 14
logger = logging.getLogger(__name__)


async def cache_list_module_records(bot, source_name, data):
    """Let a list_*.py command update today's record DB without coupling imports."""
    cog = bot.get_cog("TodayRecordCog")
    if not cog or not data:
        return
    try:
        await cog.record_list_data(source_name, data)
    except Exception:
        # 極值暫存失敗不應該讓原本的排行指令失敗。
        logger.exception("❌ [今日記錄] %s 排行資料寫入失敗", source_name)


def parse_observed_at(value, *, default: datetime | None = None) -> datetime:
    """Parse an API timestamp and return a timezone-aware Taipei datetime."""
    if isinstance(value, datetime):
        parsed = value
    elif value:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    elif default is not None:
        parsed = default
    else:
        parsed = datetime.now(TAIPEI_TZ)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI_TZ)
    return parsed.astimezone(TAIPEI_TZ)


class TodayRecordStore:
    """Keep one maximum/minimum record per metric and local calendar day."""

    def __init__(self, db_path: str | os.PathLike = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _initialize_sync(self):
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS daily_records (
                        record_date TEXT NOT NULL,
                        metric TEXT NOT NULL,
                        direction TEXT NOT NULL CHECK(direction IN ('max', 'min')),
                        value REAL NOT NULL,
                        county TEXT NOT NULL DEFAULT '',
                        town TEXT NOT NULL DEFAULT '',
                        station TEXT NOT NULL DEFAULT '',
                        observed_at TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (record_date, metric)
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_daily_records_date "
                    "ON daily_records(record_date)"
                )

    async def initialize(self):
        await asyncio.to_thread(self._initialize_sync)

    def _upsert_sync(self, records: Iterable[Mapping], today: date):
        now_iso = datetime.now(TAIPEI_TZ).isoformat()
        cutoff = (today - timedelta(days=RETENTION_DAYS - 1)).isoformat()
        rows = []
        for record in records:
            direction = record.get("direction", "max")
            if direction not in {"max", "min"}:
                raise ValueError(f"Unsupported record direction: {direction}")
            observed = parse_observed_at(record.get("observed_at"))
            rows.append(
                (
                    observed.date().isoformat(),
                    str(record["metric"]),
                    direction,
                    float(record["value"]),
                    str(record.get("county") or ""),
                    str(record.get("town") or ""),
                    str(record.get("station") or ""),
                    observed.isoformat(),
                    str(record.get("source") or ""),
                    now_iso,
                )
            )

        with closing(self._connect()) as conn:
            with conn:
                conn.execute("PRAGMA journal_mode=WAL")
                if rows:
                    conn.executemany(
                        """
                        INSERT INTO daily_records (
                            record_date, metric, direction, value, county, town,
                            station, observed_at, source, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(record_date, metric) DO UPDATE SET
                            direction = excluded.direction,
                            value = excluded.value,
                            county = excluded.county,
                            town = excluded.town,
                            station = excluded.station,
                            observed_at = excluded.observed_at,
                            source = excluded.source,
                            updated_at = excluded.updated_at
                        WHERE
                            (excluded.direction = 'max' AND excluded.value > daily_records.value)
                            OR
                            (
                                excluded.direction = 'min'
                                AND (
                                    excluded.value < daily_records.value
                                    OR (
                                        excluded.value = daily_records.value
                                        AND excluded.observed_at < daily_records.observed_at
                                    )
                                )
                            )
                        """,
                        rows,
                    )
                conn.execute("DELETE FROM daily_records WHERE record_date < ?", (cutoff,))
                conn.execute(
                    """
                    DELETE FROM daily_records
                    WHERE record_date NOT IN (
                        SELECT record_date FROM (
                            SELECT DISTINCT record_date
                            FROM daily_records
                            ORDER BY record_date DESC
                            LIMIT ?
                        )
                    )
                    """,
                    (RETENTION_DAYS,),
                )

    async def upsert(self, records: Iterable[Mapping], *, today: date | None = None):
        local_today = today or datetime.now(TAIPEI_TZ).date()
        await asyncio.to_thread(self._upsert_sync, list(records), local_today)

    def _get_day_sync(self, record_date: date):
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT record_date, metric, direction, value, county, town,
                       station, observed_at, source, updated_at
                FROM daily_records
                WHERE record_date = ?
                ORDER BY metric
                """,
                (record_date.isoformat(),),
            ).fetchall()
        return {row["metric"]: dict(row) for row in rows}

    async def get_day(self, record_date: date | None = None):
        target = record_date or datetime.now(TAIPEI_TZ).date()
        return await asyncio.to_thread(self._get_day_sync, target)

    def _delete_metrics_sync(self, metrics):
        placeholders = ",".join("?" for _ in metrics)
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    f"DELETE FROM daily_records WHERE metric IN ({placeholders})",
                    tuple(metrics),
                )

    async def delete_metrics(self, *metrics):
        if metrics:
            await asyncio.to_thread(self._delete_metrics_sync, metrics)
