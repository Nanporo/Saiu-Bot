"""CWA E-A0014-001 report parsing and subscription matching."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import re
from urllib.parse import urlsplit, urljoin

REGIONS = {
    "北部沿海地區": "基隆市、新北市",
    "東北沿海地區": "宜蘭縣頭城鎮至蘇澳鎮",
    "東部沿海地區": "宜蘭縣南澳鄉至臺東縣長濱鄉",
    "東南沿海地區": "臺東縣成功鎮至屏東縣滿州鄉",
    "西南沿海地區": "臺南市至屏東縣恆春鎮",
    "海峽沿海地區": "桃園市至嘉義縣以及澎湖、金門、連江",
}
ALL_REGIONS = "全台接收"
# CWA usually keeps ReportType="海嘯消息" for its final cancellation bulletin.
# Also recognize explicit cancellation types so they follow the same preference.
INFO_TYPES = {"海嘯資訊", "海嘯消息", "海嘯資訊解除", "海嘯消息解除"}
TAIPEI = timezone(timedelta(hours=8))


def as_list(value):
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []


def reports_from_payload(payload):
    if str(payload.get("success", "true")).lower() != "true":
        raise ValueError("CWA returned an unsuccessful response")
    records = payload.get("records")
    if not isinstance(records, dict) or "Tsunami" not in records:
        raise ValueError("Missing CWA Tsunami records")
    return [r for r in as_list(records["Tsunami"]) if isinstance(r, dict)]


def warning_areas(report):
    return [a for a in as_list(report.get("TsunamiWave", {}).get("WarningArea")) if isinstance(a, dict)]


def report_image_url(report):
    """Use an API image URI, or the full report GIF linked by its official Web page."""
    def official_image(url):
        try:
            parsed = urlsplit(url)
            return (parsed.scheme == "https" and parsed.hostname == "scweb.cwa.gov.tw"
                    and not parsed.username and parsed.port in (None, 443)
                    and parsed.path.lower().endswith((".gif", ".png", ".jpg", ".jpeg")))
        except (ValueError, TypeError):
            return False

    for field in ("ReportImageURI", "ReportImageURL"):
        url = report.get(field)
        if isinstance(url, str) and official_image(url):
            return url
    web = report.get("Web")
    if not isinstance(web, str):
        return None
    try:
        parsed = urlsplit(web)
        if parsed.scheme != "https" or parsed.hostname != "scweb.cwa.gov.tw" or parsed.username or parsed.port not in (None, 443):
            return None
        match = re.fullmatch(r"/webdata/OLDTSU/(?P<bulletin>\d{8,})/Tsu(?P=bulletin)\.htm", parsed.path, re.IGNORECASE)
        if match:
            # Full report image; the *TSU.jpg and *EQ.jpg files are partial maps.
            return urljoin(web, f"TSU{match['bulletin']}.gif")
    except ValueError:
        pass
    return None


def uses_detailed_format(region, subscription):
    return region == ALL_REGIONS or bool(subscription.get("detailed_format", False))


def event_key(report):
    # TsunamiNo is stable even when earthquake origin time/magnitude change.
    value = report.get("TsunamiNo")
    return str(value).strip() if value is not None and str(value).strip() else None


def affected_regions(report):
    return {str(a.get("AreaName", "")).strip() for a in warning_areas(report)} & REGIONS.keys()


def matches_subscription(report, region, subscription, *, event_regions=None):
    kind = str(report.get("ReportType") or "").strip()
    if not subscription.get("include_information", False) and kind in INFO_TYPES:
        return False
    if region == ALL_REGIONS:
        return True
    if region not in REGIONS:
        return False
    areas = set(event_regions) if event_regions is not None else affected_regions(report)
    if areas:
        return region in areas
    # Informational PTWC messages have no Taiwan warning area. Warnings with
    # unknown scope wait for their event's first report instead of broadcasting
    # to unrelated regional subscriptions.
    return kind in INFO_TYPES


def is_cancellation_report(report):
    kind = str(report.get("ReportType") or "").strip()
    if "海嘯" in kind and "解除" in kind:
        return True
    # PTWC cancellation messages may retain the ordinary 海嘯消息 type.
    content = str(report.get("ReportContent") or "")
    return kind in INFO_TYPES and "確認解除太平洋地區的海嘯威脅" in content


def report_key(report):
    # Include the payload so an official correction to the same report is delivered.
    return hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def parse_time(value):
    try:
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=TAIPEI) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None


def is_current(report, now=None):
    now = now or datetime.now(timezone.utc)
    issued = parse_time(report.get("IssueTime"))
    end = parse_time(report.get("ValidTime", {}).get("EndTime"))
    return bool(issued and issued <= now and now - issued <= timedelta(days=1) and (not end or now <= end))
