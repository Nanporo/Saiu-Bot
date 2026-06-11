import time
import inspect
from functools import wraps

def async_cache(ttl_seconds=300):
    """
    非同步快取裝飾器 (Async Cache Decorator)
    將 API 請求等耗時的非同步函式結果暫存於記憶體中。
    """
    def decorator(func):
        cache = {}
        sig = inspect.signature(func)
        
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 取得傳入的所有參數並與參數名稱綁定
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            args_dict = bound.arguments
            
            # 排除 self 或 cls，確保相同類別的不同實例能共用同一份快取
            args_dict.pop('self', None)
            args_dict.pop('cls', None)
            
            key = str(args_dict)
            now = time.time()
            
            # 清除已經過期的快取，避免未來參數不同時造成記憶體字典無限增長 (Memory Leak)
            expired_keys = [k for k, (v_data, v_time) in cache.items() if now - v_time >= ttl_seconds]
            for k in expired_keys:
                del cache[k]

            if key in cache:
                data, timestamp = cache[key]
                if now - timestamp < ttl_seconds:
                    return data
            
            result = await func(*args, **kwargs)
            if result is not None:
                cache[key] = (result, now)
            return result

        wrapper.invalidate_all = lambda: cache.clear()
        return wrapper
    return decorator