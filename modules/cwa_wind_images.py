"""Read published wind image filenames from the CWA page's JavaScript."""

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

from aiohttp import ClientTimeout
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)
WIND_PAGE_URL = "https://www.cwa.gov.tw/V8/C/W/WindSpeed/WindSpeed_Img.html"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": WIND_PAGE_URL,
    "Accept-Language": "zh-TW,zh;q=0.9",
}
# Parse string data only; never execute JavaScript supplied by the remote site.
JS_STRING = re.compile(r'''(["'])((?:\\.|(?!\1)[^\\\r\n])*)\1''')
IMAGE_NAME = re.compile(r"(?P<time>\d{4}-\d{2}-\d{2}_\d{4})\.(?P<type>GWD2?)\.png$", re.I)


def script_strings(source):
    return [match.group(2).replace(r"\/", "/") for match in JS_STRING.finditer(source)]


def official_url(value, base):
    try:
        url = urljoin(base, value)
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme == "https" and parts.netloc == "www.cwa.gov.tw":
        return url
    return None


def image_candidates(source, base, image_base, wind_type):
    """Use filenames actually present in script data, including non-hourly times."""
    candidates = {}
    wanted = "GWD2" if wind_type == "gust" else "GWD"
    for value in script_strings(source):
        if ".gwd" not in value.lower():
            continue
        if not official_url(value, base):
            continue
        match = IMAGE_NAME.search(urlsplit(value).path)
        if not match or match['type'].upper() != wanted:
            continue
        try:
            observed = datetime.strptime(match['time'], "%Y-%m-%d_%H%M").replace(
                tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            continue
        # Data lists may contain a basename while the page supplies its directory.
        url = official_url(value, image_base if "/" not in value else base)
        if url:
            candidates[url] = observed
    return candidates


async def fetch_latest_wind_image(session, wind_type):
    try:
        return await asyncio.wait_for(_fetch_latest_wind_image(session, wind_type), timeout=45)
    except asyncio.TimeoutError:
        logger.warning("⚠️ [風力觀測圖] 取得逾時 (%s)", wind_type)
        return None, "未知時間"


async def _fetch_latest_wind_image(session, wind_type):
    async def read_text(url):
        async with session.get(url, headers=HEADERS, timeout=ClientTimeout(total=15)) as response:
            response.raise_for_status()
            return await response.text()

    try:
        page = await read_text(WIND_PAGE_URL)
        soup = BeautifulSoup(page, "html.parser")
        # Use the page's actual image directory to resolve basename-only data lists.
        image_base = WIND_PAGE_URL
        for img in soup.find_all("img", src=True):
            src = img['src']
            if "windspeed" in src.lower():
                image_base = official_url(src, WIND_PAGE_URL) or image_base
                break

        candidates = {}
        scripts = []
        for script in soup.find_all("script"):
            if script.get("src"):
                url = official_url(script['src'], WIND_PAGE_URL)
                if url:
                    scripts.append(url)
            else:
                scripts.append((script.get_text(), WIND_PAGE_URL))

        # Follow script data references as well as <script src>, without inventing
        # either timestamps or data-script filenames. Bound requests if markup changes.
        visited = set()
        for _ in range(40):
            if not scripts:
                break
            item = scripts.pop(0)
            if isinstance(item, tuple):
                source, base = item
            else:
                if item in visited:
                    continue
                visited.add(item)
                # Browser requests made by page scripts (e.g. $.getScript) resolve
                # relative paths against the document, not the script's directory.
                base = WIND_PAGE_URL
                try:
                    source = await read_text(item)
                except Exception as exc:
                    logger.debug("無法讀取風力觀測腳本 %s: %r", item, exc)
                    continue
            candidates.update(image_candidates(source, base, image_base, wind_type))
            for value in script_strings(source):
                url = official_url(value, base)
                if url and urlsplit(url).path.lower().endswith(".js") and url not in visited:
                    scripts.append(url)

        latest = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
        for url, observed in latest[:4]:
            try:
                async with session.get(url, headers=HEADERS, timeout=ClientTimeout(total=15)) as response:
                    response.raise_for_status()
                    image = await response.read()
                    # A WAF/error HTML page can also return 200. Only attach PNG data.
                    if image.startswith(b"\x89PNG\r\n\x1a\n"):
                        logger.info(
                            "💨 [風力觀測圖] 下載成功 (%s, %.1f KB)",
                            wind_type, len(image) / 1024,
                        )
                        return image, f"<t:{int(observed.timestamp())}:f>"
                    logger.debug("風力觀測圖回應不是 PNG: %s", url)
            except Exception as exc:
                logger.debug("無法下載已公布的風力觀測圖 %s: %r", url, exc)
        logger.warning("⚠️ [風力觀測圖] 無法取得圖片 (%s)", wind_type)
    except Exception as exc:
        logger.error("❌ [風力觀測圖] 讀取網頁失敗: %r", exc)
    return None, "未知時間"
