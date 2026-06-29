import aiohttp
import asyncio
import time
import logging

logger = logging.getLogger(__name__)

# 全域快取存放
_cache = {}

async def _fetch(url: str, session_kwargs: dict, response_type: str, max_retries: int = 1, retry_delay: int = 5, cache_ttl: int = 120):
    current_time = time.time()
    
    # 檢查快取
    if url in _cache:
        cached_data = _cache[url]
        if current_time < cached_data['expire_at']:
            logger.info(f"使用快取: {url}")
            return cached_data['data']
        else:
            del _cache[url]
            
    # 沒有快取或已過期，發送請求
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        for attempt in range(max_retries + 1):
            try:
                async with session.get(url, **session_kwargs) as resp:
                    if resp.status not in [200, 202]:
                        logger.warning(f"HTTP 狀態異常 {resp.status} for {url} (嘗試 {attempt + 1}/{max_retries + 1})")
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
                logger.error(f"請求發生錯誤: {e} for {url} (嘗試 {attempt + 1}/{max_retries + 1})")
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                else:
                    raise e
                    
async def fetch_json(url: str, headers: dict = None, max_retries: int = 1, retry_delay: int = 5, cache_ttl: int = 120):
    session_kwargs = {'timeout': 10}
    if headers:
        session_kwargs['headers'] = headers
    return await _fetch(url, session_kwargs, 'json', max_retries, retry_delay, cache_ttl)

async def fetch_text(url: str, headers: dict = None, max_retries: int = 1, retry_delay: int = 5, cache_ttl: int = 120):
    session_kwargs = {'timeout': 10}
    if headers:
        session_kwargs['headers'] = headers
    return await _fetch(url, session_kwargs, 'text', max_retries, retry_delay, cache_ttl)
