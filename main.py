from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Form, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
import asyncio
import time
from io import BytesIO
from PIL import Image
import numpy as np
from urllib.parse import urlparse
from datetime import datetime, timezone

def is_valid_url(url: str):
    try:
        result = urlparse(url)
        return result.scheme in ("http", "https") and result.netloc != ""
    except:
        return False

# ===== IMPORTS TỪ CÁC MODULE =====
from schemas import (
    TextRequest, UrlRequest, UsageRequest, AnalyzeSmartResponse, 
    RegisterFcmTokenRequest, AnalyzeScreenResponse,
    MarkReadRequest, HeartbeatRequest
)
from cache_utils import get_cache, set_cache, is_duplicate_text
from utils import (
    normalize_url, fix_encoding, extract_url_from_text, 
    infer_platform_from_value, detect_content_metadata,
    human_readable_reason, simplify_reason, _format_duration
)
from analyzers import (
    run_decision_pipeline, analyze_title, is_title_content,
    analyze_deep_content, set_gemini_client
)
from crawler_utils import fetch_and_clean_content
from services.analyzer import check_whitelist

from gemini_queue import start_worker
from violations import (
    update_violation, format_usage, format_violation
)
from websocket_handlers import websocket_endpoint, get_connected_clients
from fcm_service import get_fcm_tokens_for_device
from services.firebase_service import init_firebase
from routes.analyze import router as analyze_router

load_dotenv()

# ===== FIREBASE & GEMINI =====
import firebase_admin
from firebase_admin import credentials, firestore
from bs4 import BeautifulSoup
from google import genai
import requests

try:
    if not firebase_admin._apps:
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        cred = credentials.Certificate(os.path.join(BASE_DIR, "firebase_key.json"))
        firebase_admin.initialize_app(cred)

    db = firestore.client()
    print("✅ Firebase connected")
except Exception as e:
    print(f"❌ Firebase Init Error: {e}")
    db = None

GEMINI_KEY = os.getenv("GEMINI_KEY", "YOUR_GEMINI_API_KEY")
gemini_client = None
try:
    gemini_client = genai.Client(api_key=GEMINI_KEY)
    set_gemini_client(gemini_client)
    print("✅ Gemini client configured (google.genai)")
    print("🔑 GEMINI KEY:", GEMINI_KEY[:10] if GEMINI_KEY else "NOT FOUND")
except Exception as e:
    gemini_client = None
    print(f"❌ Gemini initialization error: {e}")

# ===== APP SETUP =====
app = FastAPI(title="Child Monitor AI Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include modular routes
app.include_router(analyze_router)

# Initialize Firebase
init_firebase()

# ===== 🔥 START GEMINI QUEUE WORKER =====
@app.on_event("startup")
async def startup_event():
    print("🚀 Starting Gemini queue worker...")
    start_worker()

# ===== MODELS PLACEHOLDER (Disabled for now) =====
classifier = None
nsfw_model = None
image_classifier = None
reader = None
transform = None

print("⚠️ Models disabled for testing")

# ===== CONSTANTS =====
BAD_WORDS = [
    "sex", "porn", "dit me", "dm", "clm",
    "kill", "suicide", "rape", "hiep dam",
    "hiếp dâm", "tự tử", "giết", "địt", "đụ", "lồn", "cặc"
]

VIOLENCE_LABELS = [
    "violence", "gun", "weapon", "blood", "fight", "safe"
]

# ===== ROOT ENDPOINT =====
@app.get("/")
async def root():
    return {
        "status": "running",
        "service": "Child Monitor AI Service",
        "version": "1.0",
        "endpoints": [
            "/health",
            "/analyze",
            "/analyze-text",
            "/analyze-screen",
            "/analyze-rule",
            "/analyze-url",
            "/track-usage",
            "/app_usage",
            "/app-usage-summary",
            "/violation-history",
            "/register_fcm_token",
            "/ws/{device_id}"
        ]
    }


# ===== WEBSOCKET =====
@app.websocket("/ws/{device_id}")
async def ws_endpoint(websocket: WebSocket, device_id: str):
    connected_clients = get_connected_clients()
    await websocket_endpoint(websocket, device_id)


# ===== TEXT ANALYSIS ENDPOINT =====
@app.post("/analyze-text", response_model=AnalyzeSmartResponse)
async def analyze_text_endpoint(data: TextRequest):
    text = data.text
    device_id = data.device_id

    print(f"📥 RECEIVED: {text}")

    # Check for duplicate text
    if is_duplicate_text(text):
        print(f"🔄 DUPLICATE TEXT SKIPPED: {text[:50]}...")
        return AnalyzeSmartResponse(
            category="safe",
            risk_level=0,
            is_safe=True,
            reason="duplicate_skipped"
        )

    # Detect content type
    content_type, violated_value, platform = detect_content_metadata(text)

    # Analyze based on content type
    if is_title_content(text):
        print(f"🎬 Detected as TITLE - using rule + Gemini analysis")
        status, reason = await analyze_title(text, device_id)
    else:
        print(f"📄 Detected as FULL TEXT - using rule-based analysis")
        status, reason = run_decision_pipeline(text)
    
    # Map status to response format
    is_safe = status == "SAFE"
    risk_level = 0
    category = "safe"

    if status in ["BLOCK", "LOCK"]:
        risk_level = 9 if status == "BLOCK" else 10
        category = "dangerous"
    elif status == "WARNING":
        risk_level = 6
        category = "warning"

    # Update Firestore if violation detected
    if not is_safe and device_id:
        print(f"🚨 VIOLATION DETECTED - UPDATING FIRESTORE")
        connected_clients = get_connected_clients()
        update_violation(
            device_id,
            text,
            status,
            reason,
            content_type=content_type,
            violated_value=violated_value,
            platform=platform,
            db=db,
            connected_clients=connected_clients
        )
    else:
        print(f"✅ SAFE CONTENT")

    return AnalyzeSmartResponse(
        category=category,
        risk_level=risk_level,
        is_safe=is_safe,
        reason=human_readable_reason(reason)
    )


# ===== URL ANALYSIS ENDPOINT (PRO VERSION) =====
@app.post("/analyze-url")
async def analyze_url_endpoint(data: UrlRequest):
    url_orig = data.url
    device_id = data.device_id
    
    # 1. Normalize & Cache Check
    url_norm = normalize_url(url_orig)
    cached = get_cache(url_norm)
    if cached:
        print(f"📋 Cache HIT for: {url_norm}")
        return cached

    try:
        # 2. Whitelist Check (Two-Tier)
        wl_status = check_whitelist(url_norm)
        
        if wl_status == "STATIC":
            print(f"✅ Whitelist Group A (Static) -> SAFE: {url_norm}")
            result = {"url": url_norm, "status": "SAFE", "reason": "Whitelist (Safe)"}
            set_cache(url_norm, result)
            return result
        
        # 3. Fetch & Clean Content
        print(f"🌐 Pro-Crawling: {url_norm}")
        title, body_text, final_url = await fetch_and_clean_content(url_norm)
        
        # 4. Content check
        if not title and not body_text:
            print(f"⚠️ Content too thin for: {url_norm}")
            result = {"url": final_url, "status": "SAFE", "reason": "Nội dung không đủ để phân tích"}
            set_cache(url_norm, result)
            return result

        # 5. Pipeline Analysis
        # Force rules only if Whitelist Group B (Trusted Dynamic)
        force_rules = (wl_status == "DYNAMIC")
        
        combined_text = f"{title}\n{body_text}".strip()
        status, reason = await analyze_deep_content(
            combined_text, 
            is_title=False, 
            force_rules_only=force_rules
        )
        
        # 6. Violation Reporting
        if status in ["BLOCK", "LOCK", "WARNING"] and device_id:
            print(f"🚨 VIOLATION: {status} | {reason}")
            connected_clients = get_connected_clients()
            update_violation(
                device_id,
                final_url,
                status,
                reason,
                content_type="URL",
                violated_value=final_url,
                platform=infer_platform_from_value(final_url),
                db=db,
                connected_clients=connected_clients
            )
            
        result = {
            "url": final_url,
            "status": status,
            "reason": human_readable_reason(reason)
        }
        
        # Save to Cache (24h)
        set_cache(url_norm, result)
        if final_url != url_norm:
            set_cache(final_url, result)
            
        return result

    except Exception as e:
        print(f"❌ URL Analysis Pipeline Error: {e}")
        return {
            "url": url_orig,
            "status": "error",
            "reason": str(e)
        }


    except Exception as e:
        print(f"❌ URL analysis error: {e}")
        result = {
            "url": data.url,
            "status": "error",
            "reason": str(e)
        }
        set_cache(cache_key, result)
        return result


# ===== TRACK USAGE ENDPOINT =====
@app.post("/track-usage")
async def track_usage(data: UsageRequest):
    try:
        print(f"📱 Tracking usage: {data.package} - {data.duration}ms")

        if db and data.device_id:
            now = datetime.now(timezone.utc)
            date_key = now.strftime("%Y-%m-%d")
            minutes_added = round(data.duration / 60000, 2)

            # 1. Add log to app_usage
            db.collection("app_usage").add({
                "device_id": data.device_id,
                "package": data.package,
                "duration_ms": data.duration,
                "timestamp": firestore.SERVER_TIMESTAMP,
                "date_key": date_key
            })

            # 2. Update Device Summary (Today)
            from utils import map_app_name
            app_name = map_app_name(data.package)
            
            device_ref = db.collection("devices").document(data.device_id)
            
            # Use a transaction or simply update with increment
            # We'll maintain 'usage_today_minutes' and a 'usage_today_map' to find top_app
            device_doc = device_ref.get()
            if device_doc.exists:
                d = device_doc.to_dict()
                
                # Check if it's a new day
                last_reset = d.get("usage_today_date", "")
                if last_reset != date_key:
                    # New day -> Reset summary
                    device_ref.update({
                        "usage_today_minutes": minutes_added,
                        "usage_today_top_app": app_name,
                        "usage_today_date": date_key,
                        "usage_today_map": { data.package: data.duration }
                    })
                else:
                    # Same day -> Accumulate
                    usage_map = d.get("usage_today_map", {})
                    usage_map[data.package] = usage_map.get(data.package, 0) + data.duration
                    
                    # Find new top app
                    top_pkg = max(usage_map, key=usage_map.get)
                    top_app_name = map_app_name(top_pkg)

                    device_ref.update({
                        "usage_today_minutes": firestore.Increment(minutes_added),
                        "usage_today_top_app": top_app_name,
                        "usage_today_map": usage_map
                    })

        return {"status": "ok"}

    except Exception as e:
        print(f"❌ Usage tracking error: {e}")
        return {"status": "error", "reason": str(e)}


# ===== FCM TOKEN REGISTRATION =====
@app.post("/register_fcm_token")
async def register_fcm_token(data: RegisterFcmTokenRequest):
    try:
        device_id = data.device_id
        fcm_token = data.fcm_token
        child_id = data.child_id

        if not device_id or not fcm_token:
            raise HTTPException(status_code=400, detail="device_id and fcm_token are required")

        print(f"📱 Registering FCM token for device: {device_id}")
        if child_id:
            print(f"👤 Linked to Child ID: {child_id}")
        print(f"🆔 Token: {fcm_token[:20]}...")

        if db:
            device_ref = db.collection("devices").document(device_id)
            device_doc = device_ref.get()

            current_tokens = []
            if device_doc.exists:
                device_data = device_doc.to_dict()
                existing_tokens = device_data.get("fcm_tokens", [])
                if isinstance(existing_tokens, list):
                    current_tokens = existing_tokens
                single_token = device_data.get("fcm_token")
                if single_token and single_token not in current_tokens:
                    current_tokens.append(single_token)

            if fcm_token not in current_tokens:
                current_tokens.append(fcm_token)

            update_data = {
                "fcm_tokens": current_tokens,
                "fcm_token": fcm_token,
                "last_token_update": firestore.SERVER_TIMESTAMP
            }
            if child_id:
                update_data["childId"] = child_id

            device_ref.set(update_data, merge=True)

        return {"status": "success", "message": f"FCM token registered for device {device_id}"}

    except Exception as e:
        print(f"❌ FCM token registration error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to register FCM token: {str(e)}")


# ===== APP USAGE HISTORY =====
@app.get("/app_usage")
async def get_activity_history(device_id: str = None, device_ids: str = None, include_violations: bool = False):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")

    try:
        all_activities = []
        device_id_list = []

        if device_ids:
            device_id_list = [d.strip() for d in device_ids.split(",") if d.strip()]
        elif device_id:
            device_id_list = [device_id]
        
        if not device_id_list:
            raise HTTPException(status_code=400, detail="Must provide device_id or device_ids")

        # 1. Fetch from app_usage
        usage_docs = db.collection("app_usage")\
            .where("device_id", "in", device_id_list[:10])\
            .order_by("timestamp", direction=firestore.Query.DESCENDING)\
            .limit(50)\
            .stream()

        for doc in usage_docs:
            d = doc.to_dict()
            formatted = format_usage({
                "package": d.get("package"),
                "duration_ms": d.get("duration_ms"),
                "timestamp": d.get("timestamp").isoformat() if hasattr(d.get("timestamp"), "isoformat") else d.get("timestamp"),
                "device_id": d.get("device_id")
            })
            all_activities.append(formatted)

        # 2. Fetch from violation_logs (unified feed) - ONLY IF REQUESTED
        if include_violations:
            violation_docs = db.collection("violation_logs")\
                .where("device_id", "in", device_id_list[:10])\
                .order_by("timestamp", direction=firestore.Query.DESCENDING)\
                .limit(50)\
                .stream()

            for doc in violation_docs:
                d = doc.to_dict()
                formatted = format_violation({
                    "status": d.get("status"),
                    "reason_text": d.get("reason"),
                    "text": d.get("message"),
                    "timestamp": d.get("timestamp").isoformat() if hasattr(d.get("timestamp"), "isoformat") else d.get("timestamp"),
                    "url": d.get("violated_value") if d.get("content_type") == "URL" else "",
                    "device_id": d.get("device_id")
                })
                if formatted:
                    all_activities.append(formatted)

        # Sort by timestamp descending
        all_activities.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

        return all_activities[:100]

    except Exception as e:
        print(f"❌ Activity history error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        print(f"❌ Activity history error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===== VIOLATION HISTORY =====
@app.get("/violation-history")
async def get_violation_history(device_id: str):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")

    try:
        docs = db.collection("violation_logs")\
            .where("device_id", "==", device_id)\
            .stream()

        violations = []
        for doc in docs:
            d = doc.to_dict()
            violations.append({
                "type": "violation",
                "timestamp": d.get("timestamp"),
                "message": d.get("message") or "",
                "status": d.get("status"),
                "reason": d.get("reason"),
                "content_type": d.get("content_type", "TEXT"),
                "violated_value": d.get("violated_value", ""),
                "violation_reason": d.get("violation_reason", d.get("reason")),
                "platform": d.get("platform", "web"),
                "child_name": d.get("child_name"),
                "device_name": d.get("device_name"),
            })

        violations.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

        return violations

    except Exception as e:
        print(f"❌ Violation history error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===== PARENT AGGREGATION HUB =====
@app.get("/parent/dashboard")
async def get_parent_dashboard(parent_id: str):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        # Get children
        children_docs = db.collection("parents").document(parent_id).collection("children").stream()
        child_ids = [doc.id for doc in children_docs]
        
        total_devices = 0
        online_count = 0
        high_risk_count = 0
        violations_today = 0
        unread_count = 0 # 🔥 NEW
        
        if not child_ids:
            return {
                "total_devices": 0,
                "online_count": 0,
                "high_risk_count": 0,
                "violations_today": 0,
                "unread_count": 0
            }
            
        # Get devices for those children
        devices = db.collection("devices").where("childId", "in", child_ids[:10]).stream()
        device_ids = []
        
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        for dev in devices:
            total_devices += 1
            d = dev.to_dict()
            device_ids.append(dev.id)
            
            # Online status
            status = d.get("status", "").lower()
            if status == "online":
                online_count += 1
                
            # High risk
            mode = d.get("mode", "")
            if mode in ["BLOCK", "LOCK"]:
                high_risk_count += 1
                
        # Count violations
        if device_ids:
            # 1. Total violations TODAY
            v_today_docs = db.collection("violation_logs")\
                .where("device_id", "in", device_ids[:10])\
                .stream()
            
            for v in v_today_docs:
                v_data = v.to_dict()
                ts = v_data.get("timestamp")
                if ts:
                    try:
                        if hasattr(ts, "timestamp"):
                            v_time = datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)
                        elif isinstance(ts, str):
                            v_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        else: continue
                            
                        if v_time >= start_of_today:
                            violations_today += 1
                    except: pass

            # 2. Total UNREAD (All time)
            v_unread_docs = db.collection("violation_logs")\
                .where("device_id", "in", device_ids[:10])\
                .where("isRead", "==", False)\
                .stream()
            
            for _ in v_unread_docs:
                unread_count += 1
        
        return {
            "total_devices": total_devices,
            "online_count": online_count,
            "high_risk_count": high_risk_count,
            "violations_today": violations_today,
            "unread_count": unread_count
        }
    except Exception as e:
        print(f"❌ Parent dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/parent/alerts")
async def get_parent_alerts(parent_id: str, limit: int = 30, offset: int = 0):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        from datetime import datetime, timezone
        children_docs = db.collection("parents").document(parent_id).collection("children").stream()
        child_ids = [doc.id for doc in children_docs]
        
        if not child_ids:
            return []
            
        devices = db.collection("devices").where("childId", "in", child_ids[:10]).stream()
        device_ids = [dev.id for dev in devices]
        
        if not device_ids:
            return []
            
        # Get violation logs for these devices with pagination
        query = db.collection("violation_logs")\
            .where("device_id", "in", device_ids[:10])\
            .order_by("timestamp", direction=firestore.Query.DESCENDING)\
            .limit(limit)\
            .offset(offset)
            
        violation_docs = query.stream()
        
        violations = []
        for doc in violation_docs:
            d = doc.to_dict()
            status = d.get("status", "UNKNOWN")
            reason = d.get("violation_reason", d.get("reason", "Vi phạm"))
            ts = d.get("timestamp")
            
            ts_str = ""
            if ts:
                if hasattr(ts, "timestamp"):
                    ts_str = datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc).isoformat()
                elif isinstance(ts, str):
                    ts_str = ts
            
            alert = {
                "id": doc.id,
                "timestamp": ts_str,
                "title": f"{'Cảnh báo nghiêm trọng' if status in ['BLOCK', 'LOCK'] else 'Cảnh báo'}",
                "body": f"Phát hiện {reason}",
                "riskLevel": status,
                "isRead": d.get("isRead", False),
                "raw": {
                    "device_name": d.get("device_name"),
                    "child_name": d.get("child_name"),
                    "platform": d.get("platform", "web"),
                    "violated_value": d.get("violated_value", d.get("message", "")),
                    "reason": reason,
                    "device_id": d.get("device_id")
                }
            }
            violations.append(alert)
            
        return violations
    except Exception as e:
        print(f"❌ Parent alerts error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/parent/alerts/read")
async def mark_alerts_read(data: MarkReadRequest):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        if data.mark_all:
            # Get parent's children -> devices
            children_docs = db.collection("parents").document(data.parent_id).collection("children").stream()
            child_ids = [doc.id for doc in children_docs]
            if not child_ids:
                return {"status": "ok", "updated": 0}
                
            devices = db.collection("devices").where("childId", "in", child_ids[:10]).stream()
            device_ids = [dev.id for dev in devices]
            
            if not device_ids:
                return {"status": "ok", "updated": 0}
                
            # Batch update all unread for these devices
            batch = db.batch()
            unread_docs = db.collection("violation_logs")\
                .where("device_id", "in", device_ids[:10])\
                .where("isRead", "==", False)\
                .limit(500)\
                .stream()
                
            count = 0
            for doc in unread_docs:
                batch.update(doc.reference, {"isRead": True})
                count += 1
            
            if count > 0:
                batch.commit()
            return {"status": "ok", "updated": count}
        
        elif data.alert_ids:
            batch = db.batch()
            for aid in data.alert_ids:
                ref = db.collection("violation_logs").document(aid)
                batch.update(ref, {"isRead": True})
            batch.commit()
            return {"status": "ok", "updated": len(data.alert_ids)}
            
        return {"status": "ok", "updated": 0}
    except Exception as e:
        print(f"❌ Mark read error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/device/heartbeat")
async def device_heartbeat(data: HeartbeatRequest):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")
    
    try:
        db.collection("devices").document(data.device_id).set({
            "status": data.status,
            "lastOnline": firestore.SERVER_TIMESTAMP
        }, merge=True)
        return {"status": "ok"}
    except Exception as e:
        print(f"❌ Heartbeat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ===== STATISTICS ENDPOINT =====
@app.get("/parent/statistics")
async def get_parent_statistics(parent_id: str):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")

    try:
        # 1. Get children & devices
        children_docs = db.collection("parents").document(parent_id).collection("children").stream()
        child_ids = [doc.id for doc in children_docs]
        if not child_ids:
            return {"total_internet_minutes": 0, "top_apps": [], "daily_usage": {}}

        devices = db.collection("devices").where("childId", "in", child_ids[:10]).stream()
        device_ids = [dev.id for dev in devices]
        if not device_ids:
            return {"total_internet_minutes": 0, "top_apps": [], "daily_usage": {}}

        # 2. Define internet apps (as requested by user)
        internet_packages = [
            "com.android.chrome",
            "com.google.android.youtube",
            "com.zhiliaoapp.musically", # TikTok
            "com.facebook.katana",
            "com.facebook.orca",
            "com.instagram.android"
        ]

        # 3. Query usage (last 7 days)
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        
        usage_docs = db.collection("app_usage")\
            .where("device_id", "in", device_ids[:10])\
            .where("timestamp", ">=", week_ago)\
            .stream()

        app_durations = {} # pkg -> total_ms
        daily_internet_ms = {} # date_str -> ms

        for doc in usage_docs:
            d = doc.to_dict()
            pkg = d.get("package", "unknown")
            ms = d.get("duration_ms", 0) or 0
            ts = d.get("timestamp")

            # Global app stats
            app_durations[pkg] = app_durations.get(pkg, 0) + ms

            # Internet specific stats per day
            if any(p in pkg.lower() for p in internet_packages) or "browser" in pkg.lower():
                if ts:
                    # Convert ts to date str
                    if hasattr(ts, "timestamp"):
                        dt = datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)
                    elif isinstance(ts, str):
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    else:
                        continue
                    
                    date_str = dt.strftime("%a") # Mon, Tue...
                    daily_internet_ms[date_str] = daily_internet_ms.get(date_str, 0) + ms

        # 4. Format Results
        from utils import map_app_name
        top_apps = []
        for pkg, ms in sorted(app_durations.items(), key=lambda x: x[1], reverse=True)[:5]:
            top_apps.append({
                "package": pkg,
                "app_name": map_app_name(pkg),
                "duration_minutes": round(ms / 60000, 1),
                "percentage": 0 # UI can calculate relative to total if needed
            })

        total_internet_ms = sum(daily_internet_ms.values())
        
        # Ensure all 7 days are present in daily_usage for the chart
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        final_daily = {day: round(daily_internet_ms.get(day, 0) / 60000, 1) for day in days}

        return {
            "total_internet_minutes": round(total_internet_ms / 60000, 1),
            "top_apps": top_apps,
            "daily_usage": final_daily
        }

    except Exception as e:
        print(f"❌ Statistics error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ===== APP USAGE SUMMARY =====
@app.get("/app-usage-summary")
async def get_app_usage_summary(device_id: str):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")

    try:
        usage_docs = db.collection("app_usage")\
            .where("device_id", "==", device_id)\
            .stream()

        summary = {}

        for doc in usage_docs:
            d = doc.to_dict()
            package = d.get("package")
            duration = d.get("duration_ms", 0) or 0

            if package not in summary:
                summary[package] = 0
            summary[package] += duration

        from utils import map_app_name
        
        result = []
        for package, total in summary.items():
            app_name = map_app_name(package)
            result.append({
                "package_name": package,
                "app_name": app_name,
                "total_duration_ms": total,
                "display_text": f"📱 {app_name} - {_format_duration(total)}"
            })

        result.sort(key=lambda x: x["total_duration_ms"], reverse=True)

        return result

    except Exception as e:
        print(f"❌ App usage error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===== RULE-BASED ANALYSIS =====
@app.post("/analyze-rule", response_model=AnalyzeSmartResponse)
async def analyze_rule(
    text: str = Form(None),
    device_id: str = Form(None),
    image: UploadFile = File(None)
):
    """Phân tích nội dung bằng rule-based (không dùng AI)"""
    try:
        print(f"📥 RECEIVED REQUEST: text='{text}', device_id='{device_id}'")

        if text and text.strip():
            text = fix_encoding(text.strip())
            print(f"🔍 ANALYZING TEXT: '{text[:100]}...'")
            status, reason = run_decision_pipeline(text)
            print(f"📋 DECISION RESULT: status='{status}', reason='{reason}'")

            is_safe = status == "SAFE"
            risk_level = 0
            category = "safe"

            if status in ["BLOCK", "LOCK", "WARNING"]:
                risk_level = 9 if status == "BLOCK" else 10
                category = "dangerous"
            elif status == "WARNING":
                risk_level = 6
                category = "warning"

            if status in ["BLOCK", "LOCK", "WARNING"] and device_id:
                print(f"🚨 VIOLATION DETECTED - UPDATING FIRESTORE")
                content_type, violated_value, platform = detect_content_metadata(text)
                connected_clients = get_connected_clients()
                update_violation(
                    device_id,
                    text,
                    status,
                    reason,
                    content_type=content_type,
                    violated_value=violated_value,
                    platform=platform,
                    db=db,
                    connected_clients=connected_clients
                )

            return AnalyzeSmartResponse(
                category=category,
                risk_level=risk_level,
                is_safe=is_safe,
                reason=human_readable_reason(reason)
            )

        print(f"⚠️ NO TEXT PROVIDED")
        return AnalyzeSmartResponse(
            category="safe",
            risk_level=0,
            is_safe=True,
            reason="Không có nội dung để phân tích"
        )

    except Exception as e:
        print(f"❌ Analyze Rule Error: {e}")
        import traceback
        traceback.print_exc()
        return AnalyzeSmartResponse(
            category="error",
            risk_level=0,
            is_safe=True,
            reason=f"Lỗi phân tích: {str(e)}"
        )


# ===== SCREEN ANALYSIS (Placeholder - Models disabled) =====
@app.post("/analyze-screen", response_model=AnalyzeScreenResponse)
async def analyze_screen(
    file: UploadFile = File(...),
    text: str = Form(None),
    device_id: str = Form(None),
    x_api_key: str = Header(None)
):
    if x_api_key != "SECRET_KEY":
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        print(f"\n{'='*60}")
        print(f"📸 PHÂN TÍCH ẢNH CHỤP MÀN HÌNH")
        print(f"Device ID: {device_id}")
        
        content = await file.read()
        image = Image.open(BytesIO(content)).convert("RGB")
        print(f"Kích thước ảnh: {image.size}")

        # For now, just analyze text if provided
        extracted_text = text or ""
        
        if extracted_text:
            extracted_text = fix_encoding(extracted_text.strip())
            status, reason = run_decision_pipeline(extracted_text)
        else:
            status = "SAFE"
            reason = "no_text"

        is_dangerous = status != "SAFE"

        if is_dangerous and device_id:
            content_type, violated_value, platform = detect_content_metadata(extracted_text)
            connected_clients = get_connected_clients()
            update_violation(
                device_id,
                extracted_text,
                status,
                reason,
                content_type=content_type,
                violated_value=violated_value,
                platform=platform,
                db=db,
                connected_clients=connected_clients
            )

        print(f"{'='*60}\n")

        return AnalyzeScreenResponse(
            is_dangerous=is_dangerous,
            label="SAFE" if not is_dangerous else "VIOLATION",
            confidence=0.0,
            extracted_text=extracted_text[:300]
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Image analysis failed")


# ===== HEALTH CHECK =====
@app.get("/health")
async def health_check():
    return {"status": "ok"}
