import time
from typing import Dict, Tuple, Any

# ===== URL CACHE =====
url_cache: Dict[str, Tuple[Any, float]] = {}
CACHE_TTL = 86400  # 24 hours


# ===== TEXT CACHE (Duplicate Detection) =====
text_cache: Dict[str, float] = {}
TEXT_CACHE_TTL = 2  # seconds - realtime duplicate detection


def get_cache(url: str):
    """Lấy cache từ URL"""
    if url in url_cache:
        data, ts = url_cache[url]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def set_cache(url: str, data):
    """Lưu cache cho URL"""
    url_cache[url] = (data, time.time())


def is_duplicate_text(text: str) -> bool:
    """Kiểm tra text có phải duplicate trong vòng 2 giây"""
    now = time.time()
    if text in text_cache:
        if now - text_cache[text] < TEXT_CACHE_TTL:
            return True
    text_cache[text] = now
    return False


def clear_cache():
    """Xóa tất cả cache"""
    global url_cache, text_cache
    url_cache.clear()
    text_cache.clear()
