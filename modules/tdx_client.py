import aiohttp
import asyncio
import time
import logging
import re
from datetime import datetime
from modules.config import get_config
from modules.http_client import get_shared_session

logger = logging.getLogger(__name__)

TDX_TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
TDX_BASE_URL = "https://tdx.transportdata.tw/api/basic"
THSR_ALERT_URL = f"{TDX_BASE_URL}/v2/Rail/THSR/AlertInfo"
TRA_ALERT_URL = f"{TDX_BASE_URL}/v3/Rail/TRA/Alert"

def _parse_tdx_time(t_str: str) -> str:
    """將 ISO8601 或其他格式時間轉換為 YYYY/MM/DD HH:MM 格式，以利後續 Discord 時間戳記解析"""
    if not t_str or t_str.startswith("0001"):
        return ""
    try:
        # 處理 ISO 8601，如 2026-09-25T18:07:04+08:00
        dt = datetime.fromisoformat(t_str)
        return dt.strftime("%Y/%m/%d %H:%M")
    except Exception:
        m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})[T\s](\d{1,2}):(\d{2})', t_str)
        if m:
            return f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d} {int(m.group(4)):02d}:{m.group(5)}"
        return t_str

METRO_CONFIG = {
    "TRTC": {
        "name": "臺北捷運",
        "icon": "<:trtc_logo:1553020193234624542>",
        "cities": ["臺北市", "新北市", "台北市", "新北市"]
    },
    "KRTC": {
        "name": "高雄捷運",
        "icon": "<:krtc_logo:1553020188167766027>",
        "cities": ["高雄市"]
    },
    "TYMC": {
        "name": "桃園捷運",
        "icon": "<:tymc_logo:1553020189983899748>",
        "cities": ["桃園市", "新北市", "臺北市", "台北市"]
    },
    "TMRT": {
        "name": "臺中捷運",
        "icon": "<:tmrt_logo:1553020191267356755>",
        "cities": ["臺中市", "台中市"]
    }
}

def parse_metro_alert(system_code: str, raw_data: dict) -> dict:
    meta = METRO_CONFIG.get(system_code, {"name": system_code, "icon": "🚇", "cities": []})
    name = meta["name"]
    icon = meta["icon"]

    if not raw_data or not isinstance(raw_data, dict):
        return {
            "code": system_code,
            "name": name,
            "icon": icon,
            "status_text": "無法取得狀態",
            "status_level": -1,
            "title": "",
            "desc": "",
            "update_time": "",
            "remarks": [],
            "cities": meta["cities"],
            "has_issue": False
        }

    alerts = raw_data.get("Alerts", [])
    abnormal_alerts = []
    normal_update_time = ""

    for alert in alerts:
        status = alert.get("Status")
        title = alert.get("Title", "")
        # Status: 0:'全線營運停止', 1:'全線營運正常', 2:'有異常狀況'
        if status == 1 or "正常" in title or "Normal" in title:
            normal_update_time = alert.get("PublishTime") or alert.get("UpdateTime") or ""
        else:
            abnormal_alerts.append(alert)

    if not abnormal_alerts:
        return {
            "code": system_code,
            "name": name,
            "icon": icon,
            "status_text": "正常營運",
            "status_level": 0,
            "title": "",
            "desc": "",
            "update_time": _parse_tdx_time(normal_update_time or raw_data.get("UpdateTime", "")),
            "remarks": [],
            "cities": meta["cities"],
            "has_issue": False
        }

    # 有異常狀況
    alert = abnormal_alerts[0]
    status = alert.get("Status")
    title = alert.get("Title", "").strip()

    if status == 0 or "中斷" in title or "停駛" in title or "暫停" in title:
        status_text = "營運中斷"
        status_level = 2
    else:
        status_text = "營運調整"
        status_level = 1

    update_time = _parse_tdx_time(alert.get("PublishTime") or alert.get("UpdateTime") or "")

    remarks = []
    scope = alert.get("Scope", {})
    if isinstance(scope, dict):
        sections = []
        for s in scope.get("LineSections", []):
            st1 = s.get("StartingStationName")
            st2 = s.get("EndingStationName")
            if st1 and st2:
                sections.append(f"{st1}至{st2}")
            elif s.get("Description"):
                sections.append(s.get("Description"))
        if sections:
            remarks.append(f"影響路段：{', '.join(sections)}")

    reason = alert.get("Reason", "").strip()
    if reason:
        remarks.append(f"發生原因：{reason}")

    desc = alert.get("Description", "").strip() or alert.get("Effect", "").strip()

    return {
        "code": system_code,
        "name": name,
        "icon": icon,
        "status_text": status_text,
        "status_level": status_level,
        "title": title,
        "desc": desc,
        "update_time": update_time,
        "remarks": remarks,
        "cities": meta["cities"],
        "has_issue": True
    }

class TDXClient:
    _instance = None

    def __init__(self):
        self._token = None
        self._token_expire_at = 0.0
        self._lock = asyncio.Lock()
        self._cache = {}
        self._metro_data = {}
        self._last_metro_fetch = {}

    @classmethod
    def get_instance(cls) -> "TDXClient":
        if cls._instance is None:
            cls._instance = TDXClient()
        return cls._instance

    async def get_token(self) -> str | None:
        """取得或刷新 TDX Access Token"""
        now = time.time()
        # 若快取 Token 且尚未過期（保留 300 秒緩衝期）
        if self._token and now < (self._token_expire_at - 300):
            return self._token

        config = get_config()
        client_id = config.get("TDX_CLIENT_ID")
        client_secret = config.get("TDX_CLIENT_SECRET")

        if not client_id or not client_secret:
            logger.warning("⚠️ [TDX] 未設定 TDX_CLIENT_ID 或 TDX_CLIENT_SECRET，無法存取 TDX API")
            return None

        async with self._lock:
            # 取得鎖後再次檢查，避免並行重複請求
            now = time.time()
            if self._token and now < (self._token_expire_at - 300):
                return self._token

            session = await get_shared_session()
            auth = aiohttp.BasicAuth(client_id, client_secret)
            data = {"grant_type": "client_credentials"}
            headers = {"Content-Type": "application/x-www-form-urlencoded"}

            try:
                async with session.post(TDX_TOKEN_URL, auth=auth, data=data, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        self._token = res.get("access_token")
                        expires_in = int(res.get("expires_in", 86400))
                        self._token_expire_at = now + expires_in
                        logger.info("✅ [TDX] 成功取得 Access Token")
                        return self._token
                    else:
                        err_text = await resp.text()
                        logger.error(f"❌ [TDX] 取得 Access Token 失敗: HTTP {resp.status} - {err_text}")
                        return None
            except Exception as e:
                logger.error(f"❌ [TDX] 請求 Token 發生例外錯誤: {e!r}")
                return None

    async def fetch_json(self, url: str, params: dict = None, cache_ttl: int = 60):
        """帶 Token 發送 GET 請求並快取結果"""
        now = time.time()
        # 檢查快取
        cache_key = url + str(sorted(params.items())) if params else url
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            if now < entry['expire_at']:
                return entry['data']
            else:
                del self._cache[cache_key]

        token = await self.get_token()
        if not token:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        req_params = {"$format": "JSON"}
        if params:
            req_params.update(params)

        session = await get_shared_session()
        try:
            async with session.get(url, headers=headers, params=req_params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    self._cache[cache_key] = {
                        'data': data,
                        'expire_at': now + cache_ttl
                    }
                    return data
                elif resp.status == 429:
                    logger.warning(f"⚠️ [TDX] 呼叫頻率過高 (HTTP 429 Too Many Requests): {url}")
                    return None
                else:
                    logger.warning(f"⚠️ [TDX] API 請求非 200: HTTP {resp.status} ({url})")
                    return None
        except Exception as e:
            logger.error(f"❌ [TDX] 請求 {url} 發生錯誤: {e!r}")
            return None

    async def fetch_thsrc(self) -> dict | None:
        """
        以 TDX 作為備援取得台灣高鐵營運狀況
        回傳與原有官網抓取相容之格式
        """
        data = await self.fetch_json(THSR_ALERT_URL, cache_ttl=60)
        if data is None or not isinstance(data, list):
            return None

        # 正常情況下回傳例如: [{"AlertID": "...", "Title": "全線營運正常(Normal)", "Status": "", ...}]
        abnormal_alerts = []
        normal_publish_time = ""

        for alert in data:
            title = alert.get("Title", "")
            status = alert.get("Status", "").strip()
            # 若 status 為空白且包含正常/Normal，則視為正常
            if not status and ("正常" in title or "Normal" in title):
                normal_publish_time = alert.get("PublishTime") or alert.get("UpdateTime") or ""
            else:
                abnormal_alerts.append(alert)

        if not abnormal_alerts:
            return {
                'status_text': "全線正常營運",
                'event_title': "",
                'update_time': _parse_tdx_time(normal_publish_time),
                'remarks': [],
                'desc': "",
                'is_backup': True
            }

        # 有異常狀況，取第一筆或整合
        alert = abnormal_alerts[0]
        title = alert.get("Title", "").strip()
        status_val = alert.get("Status", "").strip()

        if status_val == "X" or "停駛" in title or "中斷" in title or "暫停" in title:
            status_text = "營運中斷"
        elif status_val == "▲" or "延誤" in title or "調整" in title:
            status_text = "營運調整"
        else:
            status_text = title if title else "營運異常"

        update_time = _parse_tdx_time(alert.get("PublishTime") or alert.get("UpdateTime") or alert.get("OccuredTime") or "")

        remarks = []
        scope = alert.get("Scope", {})
        line_sections = scope.get("LineSections", []) if isinstance(scope, dict) else []
        sections = []
        for s in line_sections:
            start_st = s.get("StartingStationName")
            end_st = s.get("EndingStationName")
            if start_st and end_st:
                sections.append(f"{start_st}至{end_st}")
            elif s.get("Description"):
                sections.append(s.get("Description"))

        if sections:
            remarks.append(f"影響路段：{', '.join(sections)}")

        reason = alert.get("Reason", "").strip()
        if reason:
            remarks.append(f"發生原因：{reason}")

        desc = alert.get("Description", "").strip() or alert.get("Effect", "").strip()

        return {
            'status_text': status_text,
            'event_title': title,
            'update_time': update_time,
            'remarks': remarks,
            'desc': desc,
            'is_backup': True
        }

    async def fetch_trc(self) -> dict | None:
        """
        以 TDX 作為備援取得台灣鐵路營運狀況
        回傳與原有官網抓取相容之格式
        """
        data = await self.fetch_json(TRA_ALERT_URL, cache_ttl=60)
        if data is None or not isinstance(data, dict):
            return None

        alerts = data.get("Alerts", [])
        if not alerts:
            return {
                'items': [],
                'is_backup': True
            }

        items = []
        for alert in alerts:
            status = alert.get("Status")
            title = alert.get("Title", "")
            # Status: 0:'全線營運停止', 1:'全線營運正常', 2:'有異常狀況'
            if status == 1 or "全線營運正常" in title or "正常" in title:
                continue

            time_str = _parse_tdx_time(alert.get("PublishTime") or alert.get("UpdateTime") or alert.get("StartTime") or "")

            scope = alert.get("Scope", {})
            section_list = []
            if isinstance(scope, dict):
                for s in scope.get("LineSections", []):
                    st1 = s.get("StartingStationName", "")
                    st2 = s.get("EndingStationName", "")
                    if st1 and st2:
                        section_list.append(f"{st1}至{st2}")
                    elif s.get("Description"):
                        section_list.append(s.get("Description"))
                if not section_list:
                    for st in scope.get("Stations", []):
                        if st.get("StationName"):
                            section_list.append(st.get("StationName"))

            section = "、".join(section_list)

            content = alert.get("Description", "").strip() or alert.get("Effect", "").strip() or title
            reason = alert.get("Reason", "").strip()
            if reason and reason not in content:
                content += f" (原因：{reason})"

            end_time = _parse_tdx_time(alert.get("EndTime", ""))
            recover_raw = f"預計恢復：{end_time}" if end_time else ""

            line_names = []
            if isinstance(scope, dict):
                for line in scope.get("Lines", []):
                    ln = line.get("LineName")
                    if ln:
                        line_names.append(ln)

            prefix = f"台鐵 ({'、'.join(line_names)})" if line_names else "台鐵"

            items.append([time_str, section, content, recover_raw, prefix])

        return {
            'items': items,
            'is_backup': True
        }

    async def fetch_metro(self, system_code: str, cache_ttl: int = 180) -> dict:
        """
        取得單一捷運系統營運狀況（快取 180 秒）
        """
        now = time.time()
        # 若快取尚在有效期間內，直接返回快取的解析資料
        if system_code in self._metro_data and now < self._last_metro_fetch.get(system_code, 0) + cache_ttl:
            return self._metro_data[system_code]

        url = f"{TDX_BASE_URL}/v2/Rail/Metro/Alert/{system_code}"
        raw = await self.fetch_json(url, cache_ttl=cache_ttl)

        if raw is not None:
            parsed = parse_metro_alert(system_code, raw)
            self._metro_data[system_code] = parsed
            self._last_metro_fetch[system_code] = now
            return parsed

        # 若抓取失敗 (如 429 或網路異常)，若有歷史快取則繼續沿用舊狀態
        if system_code in self._metro_data:
            return self._metro_data[system_code]

        # 否則回傳無法取得狀態
        fallback = parse_metro_alert(system_code, None)
        self._metro_data[system_code] = fallback
        return fallback

    async def fetch_all_metro(self, stagger_delay: float = 1.0) -> dict[str, dict]:
        """
        取得所有支援的捷運系統營運狀況
        系統代碼：TRTC, KRTC, TYMC, KLRT, TRTCMG, TMRT
        為避免觸發 TDX Basic API 頻率限制 (每分鐘 5 次)，快取內直接取用，過期者微幅間隔抓取
        """
        now = time.time()
        results = {}
        for code in METRO_CONFIG.keys():
            # 檢查是否需要打網路 API
            is_cached = (code in self._metro_data and now < self._last_metro_fetch.get(code, 0) + 180)
            res = await self.fetch_metro(code)
            results[code] = res
            # 若為實際發起請求，短暫等待避免 rate limit burst
            if not is_cached:
                await asyncio.sleep(stagger_delay)

        return results

    def start_polling(self, interval: float = 30.0):
        """啟動捷運通報背景輪詢排程，每隔 interval 秒輪流更新一個系統，避開 API 速率限制"""
        if not hasattr(self, '_poll_task') or self._poll_task is None or self._poll_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._poll_task = loop.create_task(self._poll_loop(interval))
                logger.info("🚄 [TDX] 捷運即時狀態背景輪詢已啟動 (每 30 秒輪流更新)")
            except RuntimeError:
                pass

    async def _poll_loop(self, interval: float):
        codes = list(METRO_CONFIG.keys())
        idx = 0
        while True:
            try:
                code = codes[idx % len(codes)]
                idx += 1
                await self.fetch_metro(code, cache_ttl=120)
            except Exception as e:
                logger.debug(f"[TDX Poller] 輪詢 {code} 發生錯誤: {e}")
            await asyncio.sleep(interval)

async def fetch_tdx_thsrc() -> dict | None:
    return await TDXClient.get_instance().fetch_thsrc()

async def fetch_tdx_trc() -> dict | None:
    return await TDXClient.get_instance().fetch_trc()

async def fetch_all_metro_data(stagger_delay: float = 1.0) -> dict[str, dict]:
    return await TDXClient.get_instance().fetch_all_metro(stagger_delay=stagger_delay)

async def fetch_single_metro_data(code: str) -> dict:
    return await TDXClient.get_instance().fetch_metro(code)

