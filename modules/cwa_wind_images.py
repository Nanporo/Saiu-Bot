"""Download wind images listed in the CWA website's JavaScript data."""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from aiohttp import ClientResponseError, ClientTimeout


logger = logging.getLogger(__name__)
WIND_PAGE_URL = "https://www.cwa.gov.tw/V8/C/W/WindSpeed/WindSpeed_Img.html"
WIND_DATA_URL = "https://www.cwa.gov.tw/Data/js/windspeed/windspeed_img.js"
WIND_IMAGE_BASE = "https://www.cwa.gov.tw/Data/windspeed/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": WIND_PAGE_URL,
    "Accept-Language": "zh-TW,zh;q=0.9",
}
IMAGE_HEADERS = {
    **HEADERS,
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}
# WindSpeedImg contains img values without an extension. The site's
# function-windspeed-img.js appends '.png' under /Data/windspeed/.
IMAGE_ENTRY = re.compile(
    r'''["']img["']\s*:\s*["'](?P<time>\d{4}-\d{2}-\d{2}_\d{4})'''
    r'''\.(?P<type>GWD2?)(?:\.png)?["']'''
)


def image_candidates(source, wind_type):
    """Read published img entries; ignore other wind products and invalid times."""
    candidates = {}
    wanted = "GWD2" if wind_type == "gust" else "GWD"
    for match in IMAGE_ENTRY.finditer(source):
        if match['type'] != wanted:
            continue
        try:
            observed = datetime.strptime(match['time'], "%Y-%m-%d_%H%M").replace(
                tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            continue
        url = f"{WIND_IMAGE_BASE}{match['time']}.{wanted}.png"
        candidates[url] = observed
    return candidates


async def fetch_latest_wind_image(session, wind_type):
    try:
        return await asyncio.wait_for(_fetch_latest_wind_image(session, wind_type), timeout=45)
    except asyncio.TimeoutError:
        logger.warning("⚠️ [風力觀測圖] 取得逾時 (%s)", wind_type)
        return None, "未知時間"


async def _fetch_latest_wind_image(session, wind_type):
    try:
        async with session.get(WIND_DATA_URL, headers=HEADERS, timeout=ClientTimeout(total=15)) as response:
            response.raise_for_status()
            source = await response.text()
        candidates = image_candidates(source, wind_type)
    except ClientResponseError as exc:
        logger.error("❌ [風力觀測圖] 讀取清單失敗 (HTTP %s, %s)", exc.status, wind_type)
        logger.debug("風力觀測清單 HTTP 錯誤詳情", exc_info=True)
        return None, "未知時間"
    except Exception as exc:
        logger.error("❌ [風力觀測圖] 讀取清單失敗 (%s, %s)", type(exc).__name__, wind_type)
        logger.debug("風力觀測清單錯誤詳情", exc_info=True)
        return None, "未知時間"

    if not candidates:
        logger.warning("⚠️ [風力觀測圖] 清單無可用圖片 (%s)", wind_type)
        return None, "未知時間"

    latest = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
    failure_reason = "未知原因"
    for url, observed in latest[:4]:
        try:
            async with session.get(url, headers=IMAGE_HEADERS, timeout=ClientTimeout(total=15)) as response:
                response.raise_for_status()
                image = await response.read()
                # A WAF/error HTML page can also return 200. Only attach PNG data.
                if image.startswith(b"\x89PNG\r\n\x1a\n"):
                    logger.info(
                        "💨 [風力觀測圖] 下載成功 (%s, %.1f KB)",
                        wind_type, len(image) / 1024,
                    )
                    return image, f"<t:{int(observed.timestamp())}:f>"
                failure_reason = "非 PNG 回應"
                logger.debug("風力觀測圖回應不是 PNG: %s", url)
        except ClientResponseError as exc:
            failure_reason = f"HTTP {exc.status}"
            logger.debug("風力觀測圖下載失敗 (%s): %s", failure_reason, url)
        except Exception as exc:
            failure_reason = type(exc).__name__
            logger.debug("無法下載已公布的風力觀測圖 %s: %r", url, exc)
    logger.warning("⚠️ [風力觀測圖] 圖片下載失敗 (%s, %s)", wind_type, failure_reason)
    return None, "未知時間"
