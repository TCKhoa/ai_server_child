from config import CANDIDATE_LABELS, DANGEROUS_LABELS, VIOLENCE_LABELS
from services.text_utils import normalize_text, is_system_text, rule_check, fuzzy_check, is_valid_text

import re
from functools import lru_cache
from transformers import pipeline

TOXIC_MODEL = "unitary/toxic-bert"
MODEL_DISABLED = False

# ================= RULE-BASED FAST CHECK (0-5ms) =================
# ================= RULE-BASED FAST CHECK (0-5ms) =================
BLACKLIST = ["sex", "porn", "địt", "vl", "xxx", "đụ", "lồn", "cặc"]
VIOLENCE_KEYWORDS = [
    "đấu súng", "nổ súng", "xả súng", "bắn chết", 
    "chiến sự", "khủng bố", "rape", "hiep dam"
]
URL_BLACKLIST_DOMAINS = ["xnxx", "pornhub", "hentaiz"]

SUICIDE_PATTERNS = re.compile(r"(tự tử|kill myself|suicide)", re.I)
URL_DETECT_PATTERN = re.compile(r"(https?://|www\.)\S+")

# ================= TWO-TIER WHITELIST =================
# Group A: Truly Safe Static (Direct SAFE)
SAFE_DOMAINS_STATIC = [
    "google.com", "wikipedia.org", "moet.gov.vn", 
    "openai.com", "chinhphu.vn", "github.com"
]

# Group B: Trusted but Dynamic Content (Skip AI if low risk, else scan)
TRUSTED_DOMAINS_DYNAMIC = [
    "youtube.com", "facebook.com", "vnexpress.net", 
    "tuoitre.vn", "thanhnien.vn", "dantri.com.vn",
    "reddit.com", "hocmai.vn", "olm.vn", 
    "vioedu.vn", "hoc247.net", "loigiaihay.com"
]


def check_whitelist(url: str) -> str:
    """Kiểm tra URL có thuộc whitelist không. Trả về 'STATIC', 'DYNAMIC', hoặc 'NONE'"""
    if not url: return "NONE"
    url_lower = url.lower()
    
    for d in SAFE_DOMAINS_STATIC:
        if d in url_lower: return "STATIC"
        
    for d in TRUSTED_DOMAINS_DYNAMIC:
        if d in url_lower: return "DYNAMIC"
        
    return "NONE"


classifier = None

BAD_URL_KEYWORDS = ["sex", "porn", "xxx", "jav", "hentai", "bet", "casino"]

try:
    classifier = pipeline(
        "text-classification",
        model=TOXIC_MODEL,
        truncation=True,
        return_all_scores=False
    )
    print(f"✅ Loaded toxic model pipeline: {TOXIC_MODEL}")
except Exception as e:
        print(f"❌ Could not load toxic model pipeline ({TOXIC_MODEL}): {e}")
        classifier = None


def analyze_url(url: str):
    url_lower = url.lower()

    for kw in BAD_URL_KEYWORDS:
        if kw in url_lower:
            return {
                "status": "BLOCK",
                "reason": "bad_domain",
                "level": "BLOCK"
            }

    return {
        "status": "SAFE",
        "reason": "clean_domain",
        "level": "SAFE"
    }


def map_toxic_label(label: str, score: float):
    if label == "toxic" and score > 0.8:
        return "BLOCK"
    elif label == "toxic":
        return "WARNING"
    return "SAFE"

def human_readable_reason(reason: str):
    if reason.startswith("rule:"):
        word = reason.replace("rule:", "")
        return f"🔴 LỌC TỪ CẤAM: '{word}' - Từ bị cấm trực tiếp"
    if reason.startswith("risk:"):
        parts = reason.split(":")
        if len(parts) >= 2:
            return f"🟡 RISK SCORE: {parts[1]}"
        return "🟡 RISK SCORE"
    if reason.startswith("fuzzy:"):
        parts = reason.split(":")
        word = parts[1] if len(parts) > 1 else "unknown"
        score = parts[2] if len(parts) > 2 else "0"
        return f"🟠 TỪ NHẠY CẢM: '{word}' (độ khớp: {score}%)"
    if reason == 'system_text':
        return "✅ NỘI DUNG HỆ THỐNG: Text từ ứng dụng hệ thống hoặc bỏ qua"
    if reason == 'invalid_text':
        return "⚪ NỘI DUNG KHÔNG HỢP LỆ: Chứa quá nhiều ký tự đặc biệt/số"
    if reason == 'too_short':
        return "⚪ NỘI DUNG QUÁ NGẮN: Ít hơn 2 từ - bỏ qua"
    if reason.startswith("ai:"):
        parts = reason.split(":")
        label = parts[1] if len(parts) > 1 else "unknown"
        score = parts[2] if len(parts) > 2 else "0"
        return f"{label} (AI {score})"
    return f"⚠️ CẢNH BÁO: {reason}"


def log_check(check_name: str, result: bool, details: str = ""):
    status_emoji = "✅" if not result else "🔴"
    print(f"  {status_emoji} [{check_name}] {details if not result else f'⚠️ VI PHẠM: {details}'}")


def _run_toxic_model(text: str):
    if classifier is None:
        return "unknown", 0.0

    try:
        results = classifier(text)
        if not results:
            return "unknown", 0.0

        # unitary/toxic-bert returns label e.g. 'toxic' or 'non-toxic'
        result = results[0]
        pred_label = result.get('label', 'unknown').lower()
        score = float(result.get('score', 0.0))

        return pred_label, score
    except Exception as e:
        print(f"❌ Toxic model inference error: {e}")
        return "unknown", 0.0


def _extract_url(text: str):
    # न simple URL extractor
    m = URL_DETECT_PATTERN.search(text)
    if not m:
        return ""
    url = m.group(0)
    # remove trailing punctuation
    return url.rstrip('.,!?:;')


def rule_based_check(text: str):
    normalized = text.lower()

    # keyword blacklist
    for word in BLACKLIST:
        if word in normalized:
            return True, "rule:bad_word"

    # URL detection filter
    if "url:" in normalized or URL_DETECT_PATTERN.search(normalized):
        url = _extract_url(normalized)
        if url:
            for domain in URL_BLACKLIST_DOMAINS:
                if domain in url:
                    return True, "rule:block_url"

    # suicide and violence pattern
    if SUICIDE_PATTERNS.search(normalized):
        return True, "rule:suicide_pattern"

    return False, ""


def calculate_risk(text: str):
    score = 0.0
    normalized = text.lower()

    if any(word in normalized for word in BLACKLIST):
        score += 0.5

    if URL_DETECT_PATTERN.search(normalized):
        score += 0.3

    # all caps strong intensity
    if normalized.strip() and normalized.upper() == normalized and len(normalized) > 5:
        score += 0.1

    # spam repetition: same word repeated 3+ times
    for token in set(normalized.split()):
        count = normalized.split().count(token)
        if count >= 3:
            score += 0.2
            break

    # clamp
    return min(score, 1.0)


@lru_cache(maxsize=1024)
def analyze_text(text: str):
    if not text:
        return "SAFE", "Empty"

    if MODEL_DISABLED:
        return "SAFE", "Model disabled"

    try:
        return decision_pipeline(text)
    except Exception as e:
        print("❌ analyze_text error:", e)
        return "SAFE", "Fallback"


def decision_pipeline(text: str):
    print("\n" + "="*60)
    print("📝 PHÂN TÍCH NỘI DUNG")
    print(f"Văn bản gốc: {text[:100]}..." if len(text) > 100 else f"Văn bản gốc: {text}")

    normalized = normalize_text(text)
    print(f"Văn bản chuẩn hóa: {normalized[:100]}..." if len(normalized) > 100 else f"Văn bản chuẩn hóa: {normalized}")

    if is_system_text(normalized):
        log_check("System Text", False, "Nội dung từ ứng dụng hệ thống")
        return "SAFE", "system_text"

    # very short is safe
    if len(normalized.split()) < 2:
        log_check("Độ dài nội dung", False, "Nội dung quá ngắn (< 2 từ)")
        return "SAFE", "too_short"

    # rule-based direct high confidence
    rule_bad, rule_reason = rule_based_check(normalized)
    if rule_bad:
        log_check("Rule-based", True, f"Khớp quy tắc: {rule_reason}")
        return "LOCK", rule_reason

    # legacy rule check (từ cấm chi tiết)
    is_bad, word = rule_check(normalized)
    if is_bad:
        log_check("Quy tắc từ cấm", True, f"Nghi ngờ từ cấm: '{word}'")
        return "LOCK", f"rule:{word}"

    log_check("Quy tắc từ cấm", False, "Không phát hiện từ cấm trực tiếp")

    is_fuzzy, fuzzy_word, fuzzy_score = fuzzy_check(normalized)
    if is_fuzzy:
        log_check("Fuzzy Matching", True, f"Từ nhạy cảm biến thể: '{fuzzy_word}' (khớp {fuzzy_score}%)")
        return "BLOCK", f"fuzzy:{fuzzy_word}:{fuzzy_score}"

    log_check("Fuzzy Matching", False, "Không phát hiện từ nhạy cảm biến thể")

    if not is_valid_text(normalized):
        log_check("Xác thực nội dung", False, "Nội dung chứa quá nhiều ký tự đặc biệt")
        return "SAFE", "invalid_text"

    # Scoring layer
    risk_score = calculate_risk(normalized)
    
    # 🔥 NEW SCORING THRESHOLDS
    # 0.0 - 0.25: SAFE
    # 0.25 - 0.65: AI (Caller will decide)
    # 0.65+: BLOCK/WARNING
    
    if risk_score >= 0.7:
        log_check("Risk scoring", True, f"High risk: {risk_score:.2f}")
        return "BLOCK", f"risk:{risk_score:.2f}"

    if risk_score >= 0.4:
        log_check("Risk scoring", True, f"Medium-High risk: {risk_score:.2f}")
        # Keep old behavior for now if called directly, but main.py will use thresholds
        ai_label, ai_score = _run_toxic_model(normalized)
        ai_status = map_toxic_label(ai_label, ai_score)

        if ai_status == "BLOCK":
            return "BLOCK", f"ai:{ai_label}:{ai_score:.2f}"
        if ai_status == "WARNING":
            return "WARNING", f"ai:{ai_label}:{ai_score:.2f}"
            
    return "SAFE", f"risk:{risk_score:.2f}"

