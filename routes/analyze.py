from fastapi import APIRouter
from models.schemas import AnalyzeRequest, AnalyzeResponse
from services.analyzer import analyze_text, analyze_url
from services.firebase_service import update_violation

router = APIRouter()

@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_content(request: AnalyzeRequest):
    print("TYPE:", request.type)

    try:
        # 1) URL path (VPN/App) - very fast rule-based
        if request.type == "url":
            result = analyze_url(request.text)
            status = result["status"]
            reason = result["reason"]
            level = result["level"]

        # 2) Content path (accessibility text) - model + pipeline
        else:
            status, reason = analyze_text(request.text)
            level = status

        # production: chỉ ghi violation khi có mức WARNING/BLOCK/LOCK
        if request.device_id and status in ["WARNING", "BLOCK", "LOCK"]:
            update_violation(request.device_id, request.text, status, reason)

        return AnalyzeResponse(status=status, reason=reason, level=level)

    except Exception as e:
        print("🔥 ERROR:", e)
        return AnalyzeResponse(status="SAFE", reason="Server fallback", level="SAFE")

