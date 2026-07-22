import aiohttp
import asyncio
import time
import logging
import re
import ssl
import certifi

logger = logging.getLogger(__name__)

# 全域快取與共享 ClientSession 存放
_cache = {}
_shared_session = None

def sanitize_url(url: str) -> str:
    """過濾 URL 中包含的 API Key 等敏感資訊以防日誌洩漏"""
    if not url:
        return ""
    url = re.sub(r'([?&]Authorization=)[^&]+', r'\1***MASKED***', url, flags=re.IGNORECASE)
    url = re.sub(r'([?&]api_key=)[^&]+', r'\1***MASKED***', url, flags=re.IGNORECASE)
    return url

async def get_shared_session() -> aiohttp.ClientSession:
    global _shared_session
    if _shared_session is None or _shared_session.closed:
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        _shared_session = aiohttp.ClientSession(connector=connector)
    return _shared_session

async def close_shared_session():
    global _shared_session
    if _shared_session and not _shared_session.closed:
        await _shared_session.close()
        _shared_session = None

def _cleanup_expired_cache(current_time: float):
    expired_keys = [k for k, v in _cache.items() if current_time >= v['expire_at']]
    for k in expired_keys:
        del _cache[k]

async def _fetch(url: str, session_kwargs: dict, response_type: str, max_retries: int = 1, retry_delay: int = 5, cache_ttl: int = 120, session: aiohttp.ClientSession = None):
    current_time = time.time()
    _cleanup_expired_cache(current_time)
    
    clean_url = sanitize_url(url)
    # 檢查快取
    if url in _cache:
        cached_data = _cache[url]
        if current_time < cached_data['expire_at']:
            logger.info(f"使用快取: {clean_url}")
            return cached_data['data']
            
    # 沒有快取或已過期，發送請求
    http_session = session or await get_shared_session()
    for attempt in range(max_retries + 1):
        try:
            async with http_session.get(url, **session_kwargs) as resp:
                if resp.status not in [200, 202]:
                    logger.warning(f"HTTP 狀態異常 {resp.status} for {clean_url} (嘗試 {attempt + 1}/{max_retries + 1})")
                    if attempt < max_retries:
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        resp.raise_for_status()
                
                if response_type == 'json':
                    data = await resp.json(content_type=None)
                elif response_type == 'text':
                    data = await resp.text()
                else:
                    data = await resp.read()
                    
                # 存入快取
                _cache[url] = {
                    'data': data,
                    'expire_at': time.time() + cache_ttl
                }
                return data
        except Exception as e:
            logger.error(f"請求發生錯誤: {e} for {clean_url} (嘗試 {attempt + 1}/{max_retries + 1})")
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)
            else:
                raise e

async def fetch_json(url: str, headers: dict = None, max_retries: int = 1, retry_delay: int = 5, cache_ttl: int = 120, session: aiohttp.ClientSession = None):
    session_kwargs = {'timeout': 10}
    if headers:
        session_kwargs['headers'] = headers
    return await _fetch(url, session_kwargs, 'json', max_retries, retry_delay, cache_ttl, session=session)

async def fetch_text(url: str, headers: dict = None, max_retries: int = 1, retry_delay: int = 5, cache_ttl: int = 120, session: aiohttp.ClientSession = None):
    session_kwargs = {'timeout': 10}
    if headers:
        session_kwargs['headers'] = headers
    return await _fetch(url, session_kwargs, 'text', max_retries, retry_delay, cache_ttl, session=session)
