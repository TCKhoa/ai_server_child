import asyncio
import time
import random

# ================= CONFIG =================
MAX_CONCURRENT = 2  # Giới hạn 2 request song song để tránh 503
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 8
TIMEOUT = 8  # Timeout ngắn để tránh treo slot

COOLDOWN_503 = 10  # Seconds to wait if we hit 503

# ================= STATE =================
semaphore = asyncio.Semaphore(MAX_CONCURRENT)
last_503_time = 0
circuit_open = False

# ================= CIRCUIT BREAKER =================
def is_in_cooldown():
    return time.time() - last_503_time < COOLDOWN_503

def open_circuit():
    global circuit_open
    circuit_open = True

def close_circuit():
    global circuit_open
    circuit_open = False

# ================= RETRY ENGINE =================
async def _execute_with_retry(func, args):
    global last_503_time
    
    delay = INITIAL_BACKOFF
    
    for attempt in range(1, MAX_RETRIES + 1):
        if is_in_cooldown():
            await asyncio.sleep(1)
            continue
            
        try:
            # Thực hiện call AI với timeout
            result = await asyncio.wait_for(
                func(*args),
                timeout=TIMEOUT
            )
            
            # Nếu thành công -> reset circuit nếu đang mở nhầm
            if circuit_open:
                close_circuit()
                
            return result
            
        except asyncio.TimeoutError:
            error = "timeout"
        except Exception as e:
            error = str(e)
            
            # Phát hiện 503 / UNAVAILABLE
            if "503" in error or "UNAVAILABLE" in error:
                last_503_time = time.time()
                open_circuit()
                print(f"🔥 Gemini 503 detected (Attempt {attempt}) -> Circuit Opened")
                
                if attempt < MAX_RETRIES:
                    # Exponential backoff: 1s, 2s, 4s...
                    sleep_time = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                    await asyncio.sleep(sleep_time)
                    continue
        
        print(f"⚠️ Gemini attempt {attempt} failed: {error}")
        
        if attempt == MAX_RETRIES:
            return ("WARNING", f"gemini_error:{error}")
            
        await asyncio.sleep(delay)
        delay *= 2

    return ("WARNING", "unknown_error")

# ================= PUBLIC API =================
async def run_gemini_task(func, *args):
    """
    Hàm public duy nhất để gọi Gemini.
    Sử dụng Semaphore để giới hạn concurrency thay vì Queue.
    """
    if is_in_cooldown() or circuit_open:
        # Nếu đang cooldown, trả về WARNING để dùng fallback Rule Engine
        return ("WARNING", "circuit_open_503")

    try:
        # Wait for slot in semaphore
        async with semaphore:
            return await _execute_with_retry(func, args)
            
    except Exception as e:
        print(f"❌ Gemini task error: {e}")
        return ("WARNING", f"task_error:{str(e)}")

def start_worker():
    """Hàm dummy để giữ tương thích với main.py cũ"""
    print("✅ Gemini Semaphore system ready (No more workers needed)")