import traceback
import asyncio
import re

from services.analyzer import decision_pipeline
from utils import human_readable_reason, fix_encoding
from gemini_queue import run_gemini_task

gemini_client = None


# ==============================
# INIT
# ==============================
def set_gemini_client(client):
    global gemini_client
    gemini_client = client


# ==============================
# RULE PIPELINE
# ==============================
def run_decision_pipeline(text: str):
    try:
        return decision_pipeline(text)
    except Exception as e:
        print("❌ decision_pipeline error:", e)
        traceback.print_exc()
        return "SAFE", "error:decision_pipeline"


# ==============================
# PARSE OUTPUT (ROBUST)
# ==============================
def parse_status(raw: str):
    if not raw:
        return "SAFE"

    raw = raw.upper()

    match = re.search(r"\b(SAFE|WARNING|BLOCK)\b", raw)
    if match:
        return match.group(1)

    cleaned = raw.strip()
    if cleaned in ["SAFE", "WARNING", "BLOCK"]:
        return cleaned

    return "SAFE"


# ==============================
# GEMINI CORE CALL
# ==============================
async def _call_gemini(text: str, is_title: bool):

    if gemini_client is None:
        return "SAFE", "gemini_error"

    MODEL = "gemini-2.0-flash"  # Flash model is better for 503s and latency


    try:
        # 🔥 prompt gọn hơn → giảm latency + giảm 503
        prompt = (
            "You are a content moderation system.\n"
            "Return ONLY one word: SAFE, WARNING, or BLOCK.\n\n"
            "RULES:\n"
            "- BLOCK: explicit violence, 18+\n"
            "- WARNING: suspicious or unclear violence\n"
            "- SAFE: normal content\n\n"
            "If unsure → WARNING\n\n"
            f"TEXT:\n{text[:1000]}"
        )

        response = gemini_client.models.generate_content(
            model=MODEL,
            contents=prompt
        )

        raw = getattr(response, "text", None) or str(response)
        raw = raw.strip()

        print("🤖 GEMINI RAW:", raw)

        return parse_status(raw), raw

    except Exception as e:
        print("❌ Gemini error:", e)
        return "SAFE", "gemini_error"


# ==============================
# WRAPPER (QUEUE ONLY)
# ==============================
async def analyze_with_gemini(text: str, is_title: bool = False):

    if gemini_client is None:
        return "SAFE", "gemini_error"

    # ❗ KHÔNG retry ở đây
    # retry + anti-spike nằm trong worker
    return await run_gemini_task(_call_gemini, text, is_title)


# ==============================
# COMBINE LOGIC
# ==============================
def combine_result(status_rule, reason_rule, status_ai, reason_ai):

    if reason_ai == "gemini_error":
        return status_rule, reason_rule

    if status_rule in ["BLOCK", "LOCK"]:
        return status_rule, reason_rule

    if status_ai == "BLOCK":
        return "WARNING", reason_ai

    if status_ai == "WARNING":
        return "WARNING", reason_ai

    if status_rule == "WARNING":
        return "WARNING", reason_rule

    return "SAFE", reason_rule


async def analyze_title(text: str, device_id: str = None):
    """Phân tích tiêu đề hoặc text ngắn"""
    print(f"🎬 ANALYZING TITLE: '{text[:60]}...'")
    text = fix_encoding(text.strip())
    return await analyze_deep_content(text, is_title=True)

async def analyze_deep_content(text: str, is_title: bool = False, force_rules_only: bool = False):
    """
    Phân tích nội dung sâu (tiêu đề + body).
    Tuân thủ ngưỡng risk mới:
    - Risk < 0.25: SAFE
    - Risk 0.25 - 0.65: CALL AI
    - Risk > 0.65: BLOCK (Rule)
    """
    # 1. Rule Scan
    status_rule, reason_rule = run_decision_pipeline(text)
    
    # Extract risk score from reason if exists (format: "risk:0.XX")
    risk_score = 0.0
    if "risk:" in reason_rule:
        try:
            risk_score = float(reason_rule.split("risk:")[1])
        except: pass
    
    # 2. Decision based on Risk or Force Flag
    if status_rule in ["BLOCK", "LOCK"] or risk_score >= 0.65 or force_rules_only:
        if force_rules_only:
            print(f"📋 Force Rules Only (Whitelist Group B) -> Skipping AI")
        else:
            print(f"📋 Rule found high risk ({risk_score}) -> BLOCK")
        return status_rule, reason_rule

    if risk_score < 0.25 and status_rule == "SAFE":
        print(f"✅ Rule found low risk ({risk_score}) -> SAFE")
        return "SAFE", reason_rule

    # 3. Call AI for medium risk
    print(f"🤖 Medium risk ({risk_score}) -> Calling Gemini...")
    try:
        status_ai, reason_ai = await analyze_with_gemini(text, is_title)
        
        # 4. Merge results
        final_status, final_reason = combine_result(
            status_rule, reason_rule,
            status_ai, reason_ai
        )
        return final_status, final_reason
        
    except Exception as e:
        print(f"❌ AI Analysis failed, falling back to Rules: {e}")
        return status_rule, reason_rule



# ==============================
# DETECT TITLE
# ==============================
def is_title_content(text: str) -> bool:
    return (
        len(text.split()) <= 15
        and len(text) < 120
        and "\n" not in text
    )