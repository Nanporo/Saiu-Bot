"""On-demand HTTP snapshots for the receiver's read-only ACARS page."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone


logger = logging.getLogger(__name__)
DEFAULT_ACARS_API_BASE_URL = ""
CACHE_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 5


def valid_timestamp(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            return None
        datetime.fromtimestamp(value, timezone.utc)
        return value
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _record_id(value):
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return 0


@dataclass(frozen=True)
class AcarsSnapshot:
    # None means the endpoint failed; an empty tuple is a successful empty result.
    messages: tuple[dict, ...] | None
    status: dict | None
    fetched_at: float

    @property
    def latest_message_at(self):
        candidates = [valid_timestamp((self.status or {}).get("last_acars_at"))]
        candidates.extend(valid_timestamp(row.get("timestamp")) for row in self.messages or ())
        return max((value for value in candidates if value is not None), default=None)


class AcarsClient:
    def __init__(self, base_url=DEFAULT_ACARS_API_BASE_URL, *, clock=time.monotonic, wall_clock=time.time):
        self.base_url = str(base_url or DEFAULT_ACARS_API_BASE_URL).strip().rstrip("/")
        self._clock, self._wall_clock = clock, wall_clock
        self._snapshot = None
        self._expires_at = 0.0
        self._inflight = None

    async def fetch(self, session, *, force=False):
        # Assign the shared task before yielding; concurrent normal/forced reads
        # join that same refresh. Cancelling one view doesn't cancel other readers.
        if self._inflight is not None and not self._inflight.done():
            return await asyncio.shield(self._inflight)
        if not force and self._snapshot is not None and self._clock() < self._expires_at:
            return self._snapshot
        self._inflight = asyncio.create_task(self._fetch(session))
        return await asyncio.shield(self._inflight)

    def cancel(self):
        if self._inflight is not None and not self._inflight.done():
            self._inflight.cancel()

    async def _request(self, session, path, expected_type):
        if session is None or not self.base_url:
            return None
        try:
            async with session.get(self.base_url + path, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status}")
                payload = await response.json()
            if not isinstance(payload, expected_type):
                raise ValueError("unexpected JSON shape")
            if expected_type is dict and not isinstance(payload.get("acars_running"), bool):
                raise ValueError("missing receiver status")
            return payload
        except Exception as exc:
            logger.warning("[ACARS] 無法取得 %s：%s", path, type(exc).__name__)
            return None

    async def _fetch(self, session):
        messages, status = await asyncio.gather(
            self._request(session, "/api/history/acars?limit=100", list),
            self._request(session, "/api/status", dict),
        )
        if messages is not None:
            messages = tuple(sorted(
                (dict(row) for row in messages if isinstance(row, dict)),
                key=lambda row: (valid_timestamp(row.get("timestamp")) or 0, _record_id(row.get("id"))),
                reverse=True,
            )[:100])
        self._snapshot = AcarsSnapshot(messages, status, self._wall_clock())
        self._expires_at = self._clock() + CACHE_SECONDS
        return self._snapshot
