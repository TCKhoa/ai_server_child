import re
import unicodedata
from datetime import datetime
from typing import Tuple


def normalize_text(text: str):
    """Chuẩn hóa text cho phân tích"""
    text = text.lower()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join([c for c in text if not unicodedata.combining(c)])

    replace_map = {
        "1": "i", "!": "i", "3": "e", "4": "a",
        "@": "a", "0": "o", "$": "s", "5": "s"
    }

    for k, v in replace_map.items():
        text = text.replace(k, v)

    text = re.sub(r'[^\w\s]', ' ', text)
    return text.strip()


def map_app_name(package: str):
    """Map package name sang app name dễ đọc"""
    if not package:
        return package

    package = package.lower()
    mapping = {
        "com.android.chrome": "Chrome",
        "chrome": "Chrome",
        "com.zhiliaoapp.musically": "TikTok",
        "trill": "TikTok",
        "com.facebook.katana": "Facebook",
        "com.facebook.orca": "Facebook Messenger",
        "com.google.android.youtube": "YouTube",
        "youtube": "YouTube",
        "com.zing.zalo": "Zalo",
        "com.whatsapp": "WhatsApp",
        "com.instagram.android": "Instagram",
        "com.twitter.android": "Twitter",
        "com.snapchat.android": "Snapchat"
    }

    for key, name in mapping.items():
        if key in package:
            return name

    return package


def firestore_timestamp_to_iso(value):
    """Convert Firestore timestamp sang ISO format"""
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_datetime"):
        try:
            return value.to_datetime().isoformat()
        except Exception:
            pass
    return value


def normalize_firestore_doc(doc: dict):
    """Chuẩn hóa document từ Firestore"""
    if not isinstance(doc, dict):
        return doc

    normalized = {}
    for k, v in doc.items():
        if k == "timestamp":
            normalized[k] = firestore_timestamp_to_iso(v)
        else:
            normalized[k] = v
    return normalized


def fix_encoding(text: str):
    """Fix unicode encoding issues"""
    if not isinstance(text, str):
        return text
    try:
        return text.encode('latin1').decode('utf-8')
    except Exception:
        return text


URL_DETECT_PATTERN = re.compile(r"(https?://|www\.)\S+")


def extract_url_from_text(text: str):
    """Trích xuất URL từ text"""
    if not isinstance(text, str):
        return None

    match = URL_DETECT_PATTERN.search(text)
    if not match:
        return None

    return match.group(0).rstrip('.,!?:;')


def infer_platform_from_value(value: str) -> str:
    """Suy đoán platform từ URL"""
    if not isinstance(value, str):
        return "unknown"

    v = value.lower()
    if "youtube.com" in v or "youtu.be" in v:
        return "youtube"
    if "tiktok.com" in v or "vm.tiktok.com" in v:
        return "tiktok"
    if "facebook.com" in v or "fb.watch" in v:
        return "facebook"
    return "web"


def detect_content_metadata(text: str) -> Tuple[str, str, str]:
    """Detect content type, value, và platform từ text
    
    Returns: (content_type, violated_value, platform)
    """
    if not isinstance(text, str) or not text.strip():
        return "TEXT", "", "web"

    url = extract_url_from_text(text)
    if url:
        return "URL", url, infer_platform_from_value(url)

    is_title = (
        len(text.split()) <= 15
        and len(text) < 120
        and "\n" not in text
    )
    if is_title:
        return "YOUTUBE_TITLE", text, "youtube"

    return "TEXT", text, "web"


def normalize_url(url: str):
    """Chuẩn hóa URL"""
    url = url.strip()

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    return url


def format_time(ts=None):
    """Format timestamp cho alert messages"""
    if not ts:
        ts = datetime.utcnow()
    return ts.strftime("%H:%M - %d/%m")


def simplify_reason(reason: str) -> str:
    """Đơn giản hóa lý do vi phạm"""
    r = reason.lower()

    if "sex" in r or "porn" in r or "hiep dam" in r:
        return "🔴 Nội dung 18+"

    if "violence" in r or "kill" in r:
        return "🔴 Bạo lực"

    if "suicide" in r:
        return "🔴 Tự hại"

    if "quảng cáo" in r or "advertisement" in r:
        return "🟡 Trang ngoài / quảng cáo"

    return reason[:80]


def human_readable_reason(reason: str):
    """Chuyển đổi mã lý do thô sang lý do chi tiết cho dễ gỡ lỗi"""
    if reason.startswith("rule:"):
        word = reason.replace("rule:", "")
        return f"🔴 LỌC TỪ CẤAM: '{word}' - Từ bị cấm trực tiếp"
    
    if reason.startswith("fuzzy:"):
        parts = reason.split(":")
        word = parts[1] if len(parts) > 1 else "unknown"
        score = parts[2] if len(parts) > 2 else "0"
        return f"🟠 TỪ NHẠY CẢM: '{word}' (độ khớp: {score}%)"
    
    if reason.startswith("system_text"):
        return "✅ NỘI DUNG HỆ THỐNG: Text từ ứng dụng hệ thống hoặc bỏ qua"
    
    if reason.startswith("invalid_text"):
        return "⚪ NỘI DUNG KHÔNG HỢP LỆ: Chứa quá nhiều ký tự đặc biệt/số"
    
    if reason.startswith("too_short"):
        return "⚪ NỘI DUNG QUẢN NGẮN: Ít hơn 2 từ - bỏ qua"
    
    if reason.startswith("ai:"):
        parts = reason.split(":")
        label = parts[1] if len(parts) > 1 else "unknown"
        score = parts[2] if len(parts) > 2 else "0"
        
        label_map = {
            "sexual content": "🔴 NỘI DUNG TÌNH DỤC",
            "nsfw": "🔴 NỘI DUNG 18+",
            "violence": "🔴 NỘI DUNG BẠO LỰC",
            "self-harm": "🔴 NỘI DUNG TỰ HẠI/TUYỆT VọNG",
            "suicide": "🔴 NỘI DUNG TUYỆT VỌNG",
            "bullying": "🟠 NỘI DUNG LẠM DỤNG",
            "safe": "✅ AN TOÀN"
        }
        
        label_text = label_map.get(label, f"❓ {label}")
        return f"{label_text} (AI độ tin cậy: {score})"
    
    if "nsfw" in reason.lower():
        if ":" in reason:
            conf = reason.split(":")[1]
            return f"🔴 PHÁT HIỆN 18+ (ảnh): Độ tin cậy {conf}"
        return "🔴 NỘI DUNG TÌNH DỤC (ảnh)"
    
    if "violence" in reason.lower() or "weapon" in reason.lower() or "gun" in reason.lower() or "blood" in reason.lower():
        if ":" in reason:
            parts = reason.split(":")
            label = parts[0]
            conf = parts[1] if len(parts) > 1 else "0"
            return f"🔴 PHÁT HIỆN BẠO LỰC ({label}): Độ tin cậy {conf}"
        return "🔴 NỘI DUNG BẠO LỰC / VŨ KHÍ"
    
    if "error" in reason.lower():
        return "❌ LỖI PHÂN TÍCH"        
    
    return f"⚠️ CẢNH BÁO: {reason}"


def build_alert_message(
    level: str,
    child_name: str,
    device_name: str,
    target: str,
    reason: str,
    status: str = ""
):
    """Build formatted alert message for UI"""
    time_str = format_time()

    if level == "BLOCK":
        return f"""🔴 NGUY CẤP

[CẢNH BÁO TRUY CẬP CẤM]

👤 {child_name} | 📱 {device_name}

🛑 Đã chặn: {target}
🛡️ Lý do: {reason}

🕒 {time_str}"""

    if level == "WARNING":
        return f"""🟡 CẦN LƯU Ý

[NHẮC NHỞ]

👤 {child_name} | 📱 {device_name}

⚠️ {status}
⏳ Lý do: {reason}

🕒 {time_str}"""

    return ""


def _format_duration(ms: int):
    """Format milliseconds sang giờ:phút"""
    minutes = ms // 60000
    if minutes > 60:
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m"
    return f"{minutes}m"
