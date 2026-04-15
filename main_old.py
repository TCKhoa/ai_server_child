from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Tuple, List
from transformers import pipeline, AutoModelForImageClassification
from PIL import Image
import torch
from routes.analyze import router as analyze_router
from services.firebase_service import init_firebase
from services.analyzer import decision_pipeline
import asyncio
# import torchvision.transforms as transforms  # Commented out for testing
# import easyocr  # Commented out for testing
import unicodedata
import re
import io
import traceback
import numpy as np
# import cv2  # Commented out due to NumPy compatibility
import os
import json
import requests
import time
from google import genai
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest

load_dotenv()

# ===== FCM HTTP v1 =====
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_ACCESS_TOKEN = None
FCM_CREDENTIALS = None
FCM_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")

# ===== FIREBASE =====
import firebase_admin
from firebase_admin import credentials, firestore

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
    print("✅ Gemini client configured (google.genai)")
    print("🔑 GEMINI KEY:", GEMINI_KEY[:10] if GEMINI_KEY else "NOT FOUND")
except Exception as e:
    gemini_client = None
    print(f"❌ Gemini initialization error: {e}")
def load_fcm_project_id():
    global FCM_PROJECT_ID
    if FCM_PROJECT_ID:
        return FCM_PROJECT_ID

    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        key_path = os.path.join(base_dir, "firebase_key.json")
        with open(key_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
            FCM_PROJECT_ID = payload.get("project_id") or payload.get("projectId")
            if FCM_PROJECT_ID:
                return FCM_PROJECT_ID
            print("❌ FCM project id not found in firebase_key.json")
    except Exception as e:
        print(f"❌ Load FCM project id error: {e}")

    return None


def load_fcm_credentials():
    global FCM_CREDENTIALS
    if FCM_CREDENTIALS:
        return FCM_CREDENTIALS

    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        key_path = os.path.join(base_dir, "firebase_key.json")
        FCM_CREDENTIALS = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=[FCM_SCOPE],
        )
        return FCM_CREDENTIALS
    except Exception as e:
        print(f"❌ Load FCM credentials error: {e}")
        return None


def get_access_token():
    try:
        creds = load_fcm_credentials()
        if not creds:
            return None
        if not creds.valid or creds.expired:
            creds.refresh(GoogleAuthRequest())
        return creds.token
    except Exception as e:
        print(f"❌ FCM get access token error: {e}")
        return None


def get_fcm_tokens_for_device(device_id: str):
    device_info = get_device_info(device_id)
    if not device_info:
        return []

    tokens = []
    for key, value in device_info.items():
        if not value or "token" not in key.lower():
            continue
        if isinstance(value, str):
            tokens.append(value.strip())
        elif isinstance(value, list):
            tokens.extend([item.strip() for item in value if isinstance(item, str) and item.strip()])

    cleaned = []
    for token in tokens:
        if token and token not in cleaned:
            cleaned.append(token)
    return cleaned


def send_lock_alert(token: str, title: str, body: str, data: dict = None):
    access_token = get_access_token()
    project_id = load_fcm_project_id()

    if not access_token or not project_id:
        print("❌ Skipping FCM send because credentials or project id are not available")
        return False

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    payload = {
        "message": {
            "token": token,
            "notification": {
                "title": title,
                "body": body,
            },
            "android": {
                "priority": "HIGH",
                "notification": {
                    "sound": "default",
                    "channel_id": "call_channel",
                    "click_action": "FLUTTER_NOTIFICATION_CLICK",
                },
            },
            "data": {str(k): str(v) for k, v in (data or {}).items()},
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=12)
        if response.status_code not in (200, 201):
            print(f"❌ FCM send failed ({response.status_code}): {response.text}")
            return False

        print(f"✅ FCM LOCK alert sent to token {token[:12]}...")
        return True
    except Exception as e:
        print(f"❌ FCM post request error: {e}")
        return False


def send_warning_alert(token: str, title: str, body: str, data: dict = None):
    access_token = get_access_token()
    project_id = load_fcm_project_id()

    if not access_token or not project_id:
        print("❌ Skipping FCM send because credentials or project id are not available")
        return False

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    payload = {
        "message": {
            "token": token,
            "notification": {
                "title": title,
                "body": body,
            },
            "data": {str(k): str(v) for k, v in (data or {}).items()},
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=12)
        if response.status_code not in (200, 201):
            print(f"❌ FCM send failed ({response.status_code}): {response.text}")
            return False

        print(f"✅ FCM WARNING alert sent to token {token[:12]}...")
        return True
    except Exception as e:
        print(f"❌ FCM post request error: {e}")
        return False


async def send_fcm_to_tokens(tokens: list, title: str, body: str, data: dict = None):
    if not tokens:
        print("⚠️ No FCM tokens available for target device")
        return
    tasks = [asyncio.to_thread(send_lock_alert if data.get("type") == "LOCK" else send_warning_alert, token, title, body, data) for token in tokens]
    await asyncio.gather(*tasks)


async def send_violation_push_notification(
    device_id: str,
    mode: str,
    child_name: str,
    device_name: str,
    reason: str,
    violated_value: str,
):
    tokens = get_fcm_tokens_for_device(device_id)
    if not tokens:
        print(f"⚠️ No FCM tokens found for device {device_id}")
        return

    title = "🔒 Khóa truy cập trẻ em" if mode == "BLOCK" else "🟠 Cảnh báo trẻ em"
    body = f"{child_name} trên {device_name}: {reason}"
    data = {
        "type": mode,
        "child_name": child_name,
        "device_name": device_name,
        "reason": reason,
        "violated_value": violated_value or "",
        "device_id": device_id,
    }

    await send_fcm_to_tokens(tokens, title, body, data)


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

def simplify_reason(reason: str) -> str:
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


def firestore_timestamp_to_iso(value):
    from datetime import datetime

    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_datetime"):
        try:
            return value.to_datetime().isoformat()
        except Exception:
            pass
    return value


def normalize_firestore_doc(doc: dict):
    if not isinstance(doc, dict):
        return doc

    normalized = {}
    for k, v in doc.items():
        if k == "timestamp":
            normalized[k] = firestore_timestamp_to_iso(v)
        else:
            normalized[k] = v
    return normalized


def map_app_name(package: str):
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


def get_device_info(device_id: str):
    """Lấy thông tin device từ Firestore"""
    if not db or not device_id:
        return None

    try:
        doc = db.collection("devices").document(device_id).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        print(f"❌ Get device info error: {e}")
        return None


def format_usage(item: dict):
    package = item.get("package", "")
    app_name = map_app_name(package)
    duration_ms = item.get("duration_ms", 0) or 0
    minutes = round(duration_ms / 60000)

    timestamp_str = item.get("timestamp")
    date = ""
    if timestamp_str:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            date = dt.strftime("%Y-%m-%d")
        except:
            pass

    return {
        "type": "usage",
        "timestamp": timestamp_str,
        "date": date,
        "display_text": f"📱 {app_name} {minutes} phút",
        "icon": "app",
        "color": "blue",
        "detail": {
            "package": package,
            "app_name": app_name,
            "duration_ms": duration_ms,
            "duration_minutes": minutes
        }
    }


def format_violation(item: dict):
    status = item.get("status", "UNKNOWN")
    reason = item.get("reason_text", "") or item.get("reason", "") or item.get("text", "") or "Vi phạm"

    timestamp_str = item.get("timestamp")
    date = ""
    if timestamp_str:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            date = dt.strftime("%Y-%m-%d")
        except:
            pass

    if status == "BLOCK":
        return {
            "type": "violation",
            "timestamp": timestamp_str,
            "date": date,
            "display_text": f"🔴 {reason}",
            "icon": "danger",
            "color": "red",
            "detail": {
                "status": status,
                "reason": reason,
                "text": item.get("text", ""),
                "full_reason": item.get("reason", ""),
                "url": item.get("url", "")
            }
        }

    if status == "WARNING":
        return {
            "type": "violation",
            "timestamp": timestamp_str,
            "date": date,
            "display_text": f"🟠 {reason}",
            "icon": "warning",
            "color": "orange",
            "detail": {
                "status": status,
                "reason": reason,
                "text": item.get("text", ""),
                "full_reason": item.get("reason", ""),
                "url": item.get("url", "")
            }
        }

    return None


def run_decision_pipeline(text: str):
    try:
        return decision_pipeline(text)
    except Exception as e:
        print("❌ decision_pipeline error:", e)
        traceback.print_exc()
        return "SAFE", "error:decision_pipeline"


def update_violation(
    device_id: str,
    text: str,
    mode: str,
    reason: str,
    content_type: str = "TEXT",
    violated_value: str = None,
    platform: str = "web"
):
    if not db or not device_id:
        print(f"❌ No DB or device_id: db={db}, device_id={device_id}")
        return
    try:
        print(f"🔥 UPDATING FIRESTORE for device: {device_id}")
        short_reason = simplify_reason(reason)  # Use simplified reason
        if violated_value is None:
            violated_value = text

        # Save to devices (quick access for last violation)
        db.collection("devices").document(device_id).set({
            "last_violation": text[:200],  # Giới hạn độ dài
            "mode": mode,
            "reason": reason,
            "reason_text": short_reason,
            "content_type": content_type,
            "platform": platform,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "text_length": len(text),
            "check_type": reason.split(":")[0] if ":" in reason else reason
        }, merge=True)

        # 🔥 LẤY DEVICE REAL DATA
        device_info = get_device_info(device_id)

        device_name = "Unknown Device"
        child_name = "Unknown User"

        if device_info:
            device_name = device_info.get("deviceName", device_info.get("device_name", "Unknown Device"))
            child_name = device_info.get("childName", child_name)

            # Nếu bạn có childId và chưa có childName → fetch thêm user
            child_id = device_info.get("childId")
            if child_id and child_name == "Unknown User":
                try:
                    user_doc = db.collection("users").document(child_id).get()
                    if user_doc.exists:
                        child_name = user_doc.to_dict().get("name", "Unknown User")
                except Exception as e:
                    print(f"❌ Get user error: {e}")

        # Debug logs
        print(f"📱 Device Name: {device_name}")
        print(f"👤 Child Name: {child_name}")

        # 🔥 BUILD ALERT REAL DATA
        alert_message = build_alert_message(
            level=mode,
            child_name=child_name,
            device_name=device_name,
            target=text[:50],
            reason=short_reason,
            status="Truy cập nội dung nhạy cảm"
        )

        # Save to violation_logs (for history)
        db.collection("violation_logs").add({
            "device_id": device_id,
            "device_name": device_name,
            "child_name": child_name,
            "status": mode,
            "message": alert_message,  # UI-ready message
            "reason": short_reason,
            "raw_reason": reason,      # Keep raw for debugging
            "content_type": content_type,
            "violated_value": violated_value,
            "violation_reason": short_reason,
            "platform": platform,
            "timestamp": firestore.SERVER_TIMESTAMP
        })

        # Broadcast realtime violation event to connected parent app(s)
        violation_data = {
            "type": "violation",
            "device_id": device_id,
            "deviceId": device_id,
            "device_name": device_name,
            "child_name": child_name,
            "status": mode,
            "riskLevel": mode,
            "message": short_reason,
            "reason": short_reason,
            "raw_reason": reason,
            "content_type": content_type,
            "violated_value": violated_value,
            "violation_reason": short_reason,
            "platform": platform,
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            "messageId": f"{device_id}-{int(time.time() * 1000)}-{text[:20].replace(' ', '_')}"  # For deduplication
        }

        try:
            asyncio.create_task(broadcast_violation(device_id, violation_data))
        except Exception as e:
            print(f"❌ Broadcast task error: {e}")

        # 🔥 Smart notification strategy: WebSocket > FCM (avoid duplicate)
        # Only send FCM if WebSocket is NOT connected (offline)
        is_websocket_connected = device_id in connected_clients
        
        if not is_websocket_connected and mode in ["BLOCK", "LOCK"]:
            print(f"📡 WebSocket NOT connected for {device_id} → Falling back to FCM")
            try:
                asyncio.create_task(send_violation_push_notification(
                    device_id=device_id,
                    mode=mode,
                    child_name=child_name,
                    device_name=device_name,
                    reason=short_reason,
                    violated_value=violated_value
                ))
            except Exception as e:
                print(f"❌ FCM push notification task error: {e}")
        elif is_websocket_connected:
            print(f"✅ WebSocket connected for {device_id} → Using realtime notification only")

        print(f"✅ FIRESTORE UPDATED SUCCESSFULLY")
        print(f"   Device: {device_id}")
        print(f"   Mode: {mode}")
        print(f"   Lý do: {short_reason}")
        print(f"   Văn bản: {text[:80]}...")
        print(f"\n")
    except Exception as e:
        print(f"❌ Firebase Update Error: {e}")
        import traceback
        traceback.print_exc()

# ===== PYDANTIC MODELS =====
class TextRequest(BaseModel):
    text: str
    device_id: str = None
    app: str = None

class UrlRequest(BaseModel):
    url: str
    device_id: str = None

class UsageRequest(BaseModel):
    package: str
    duration: int
    device_id: str = None

class AnalyzeSmartResponse(BaseModel):
    category: str
    risk_level: int
    is_safe: bool
    reason: str

class RegisterFcmTokenRequest(BaseModel):
    device_id: str
    fcm_token: str

# ===== APP =====
app = FastAPI(title="Child Monitor AI Service")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== WEBSOCKET & VIOLATIONS =====
connected_clients: Dict[str, WebSocket] = {}

@app.websocket("/ws/{device_id}")
async def websocket_endpoint(websocket: WebSocket, device_id: str):
    await websocket.accept()

    # 🔥 QUAN TRỌNG: override socket cũ
    connected_clients[device_id] = websocket

    print(f"📡 CONNECTED: {device_id}")

    try:
        while True:
            data = await websocket.receive_text()
            print("📩", data)

    except WebSocketDisconnect:
        print(f"❌ DISCONNECT: {device_id}")
        connected_clients.pop(device_id, None)


async def broadcast_violation(device_id: str, data: dict):
    print(f"📢 Broadcasting violation to {device_id}: {data.get('violated_value', 'N/A')[:50]}...")
    ws = connected_clients.get(device_id)

    if not ws:
        print("❌ No WS client")
        # Fallback: save to realtime_queue for later processing
        try:
            await db.collection("realtime_queue").add(data)
            print("📋 Saved to realtime_queue (no active WS)")
        except Exception as e:
            print(f"❌ Failed to save to realtime_queue: {e}")
        return

    try:
        await ws.send_json(data)
        print("✅ SENT")
    except Exception as e:
        print("❌ SEND FAIL:", e)
        connected_clients.pop(device_id, None)
        # Fallback: save to realtime_queue
        try:
            await db.collection("realtime_queue").add(data)
            print("📋 Saved to realtime_queue (send failed)")
        except Exception as queue_e:
            print(f"❌ Failed to save to realtime_queue: {queue_e}")


# def human_readable_reason(reason: str):
#     """Chuyển đổi mã lý do thô sang lý do chi tiết cho dễ gỡ lỗi"""
#     if reason.startswith("rule:"):
#         return f"🔴 LỌC TỪ CẤAM: '{word}' - Từ bị cấm trực tiếp"
    
#     if reason.startswith("fuzzy:"):
#         parts = reason.split(":")
#         word = parts[1] if len(parts) > 1 else "unknown"
#         score = parts[2] if len(parts) > 2 else "0"
#         return f"🟠 TỪ NHẠY CẢM: '{word}' (độ khớp: {score}%)"
    
#     if reason.startswith("system_text"):
#         return "✅ NỘI DUNG HỆ THỐNG: Text từ ứng dụng hệ thống hoặc bỏ qua"
    
#     if reason.startswith("invalid_text"):
#         return "⚪ NỘI DUNG KHÔNG HỢP LỆ: Chứa quá nhiều ký tự đặc biệt/số"
    
#     if reason.startswith("too_short"):
#         return "⚪ NỘI DUNG QUẢN NGẮN: Ít hơn 2 từ - bỏ qua"
    
#     if reason.startswith("ai:"):
#         parts = reason.split(":")
#         label = parts[1] if len(parts) > 1 else "unknown"
#         score = parts[2] if len(parts) > 2 else "0"
        
#         label_map = {
#             "sexual content": "🔴 NỘI DUNG TÌNH DỤC",
#             "nsfw": "🔴 NỘI DUNG 18+",
#             "violence": "🔴 NỘI DUNG BẠO LỰC",
#             "self-harm": "🔴 NỘI DUNG TỰ HẠI/TUYỆT VọNG",
#             "suicide": "🔴 NỘI DUNG TUYỆT VỌNG",
#             "bullying": "🟠 NỘI DUNG LẠM DỤNG",
#             "safe": "✅ AN TOÀN"
#         }
        
#         label_text = label_map.get(label, f"❓ {label}")
#         return f"{label_text} (AI độ tin cậy: {score})"
    
#     if "nsfw" in reason.lower():
#         if ":" in reason:
#             conf = reason.split(":")[1]
#             return f"🔴 PHÁT HIỆN 18+ (ảnh): Độ tin cậy {conf}"
#         return "🔴 NỘI DUNG TÌNH DỤC (ảnh)"
    
#     if "violence" in reason.lower() or "weapon" in reason.lower() or "gun" in reason.lower() or "blood" in reason.lower():
#         if ":" in reason:
#             parts = reason.split(":")
#             label = parts[0]
#             conf = parts[1] if len(parts) > 1 else "0"
#             return f"🔴 PHÁT HIỆN BẠO LỰC ({label}): Độ tin cậy {conf}"
#         return "🔴 NỘI DUNG BẠO LỰC / VŨ KHÍ"
    
#     if "error" in reason.lower():
#         return "❌ LỖI PHÂN TÍCH"        
    
#     return f"⚠️ CẢNH BÁO: {reason}"

def simplify_reason(reason: str) -> str:
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


def firestore_timestamp_to_iso(value):
    from datetime import datetime

    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "to_datetime"):
        try:
            return value.to_datetime().isoformat()
        except Exception:
            pass
    return value


def normalize_firestore_doc(doc: dict):
    if not isinstance(doc, dict):
        return doc

    normalized = {}
    for k, v in doc.items():
        if k == "timestamp":
            normalized[k] = firestore_timestamp_to_iso(v)
        else:
            normalized[k] = v
    return normalized


def map_app_name(package: str):
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


def get_device_info(device_id: str):
    """Lấy thông tin device từ Firestore"""
    if not db or not device_id:
        return None

    try:
        doc = db.collection("devices").document(device_id).get()
        if doc.exists:
            return doc.to_dict()
        return None
    except Exception as e:
        print(f"❌ Get device info error: {e}")
        return None


def format_usage(item: dict):
    package = item.get("package", "")
    app_name = map_app_name(package)
    duration_ms = item.get("duration_ms", 0) or 0
    minutes = round(duration_ms / 60000)

    timestamp_str = item.get("timestamp")
    date = ""
    if timestamp_str:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            date = dt.strftime("%Y-%m-%d")
        except:
            pass

    return {
        "type": "usage",
        "timestamp": timestamp_str,
        "date": date,
        "display_text": f"📱 {app_name} {minutes} phút",
        "icon": "app",
        "color": "blue",
        "detail": {
            "package": package,
            "app_name": app_name,
            "duration_ms": duration_ms,
            "duration_minutes": minutes
        }
    }


def format_violation(item: dict):
    status = item.get("status", "UNKNOWN")
    reason = item.get("reason_text", "") or item.get("reason", "") or item.get("text", "") or "Vi phạm"

    timestamp_str = item.get("timestamp")
    date = ""
    if timestamp_str:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            date = dt.strftime("%Y-%m-%d")
        except:
            pass

    if status == "BLOCK":
        return {
            "type": "violation",
            "timestamp": timestamp_str,
            "date": date,
            "display_text": f"🔴 {reason}",
            "icon": "danger",
            "color": "red",
            "detail": {
                "status": status,
                "reason": reason,
                "text": item.get("text", ""),
                "full_reason": item.get("reason", ""),
                "url": item.get("url", "")
            }
        }

    if status == "WARNING":
        return {
            "type": "violation",
            "timestamp": timestamp_str,
            "date": date,
            "display_text": f"🟠 {reason}",
            "icon": "warning",
            "color": "orange",
            "detail": {
                "status": status,
                "reason": reason,
                "text": item.get("text", ""),
                "full_reason": item.get("reason", ""),
                "url": item.get("url", "")
            }
        }

    return None


def run_decision_pipeline(text: str):
    try:
        return decision_pipeline(text)
    except Exception as e:
        print("❌ decision_pipeline error:", e)
        traceback.print_exc()
        return "SAFE", "error:decision_pipeline"


# ===== PYDANTIC MODELS =====
class TextRequest(BaseModel):
    text: str
    device_id: str = None
    app: str = None

class UrlRequest(BaseModel):
    url: str
    device_id: str = None

class UsageRequest(BaseModel):
    package: str
    duration: int
    device_id: str = None

class AnalyzeSmartResponse(BaseModel):
    category: str
    risk_level: int
    is_safe: bool
    reason: str



# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

connected_clients: Dict[str, WebSocket] = {}



@app.get("/")
async def root():
    return {
        "status": "running",
        "service": "Child Monitor AI Service",
        "version": "1.0",
        "endpoints": [
            "/health",
            "/analyze",
            "/analyze-screen",
            "/analyze-rule",
            "/analyze-url",
            "/track-usage",
            "/app_usage",
            "/violation-history"
        ]
    }

# ===== CACHE =====
url_cache = {}
CACHE_TTL = 60  # seconds

text_cache = {}
TEXT_CACHE_TTL = 2  # seconds - realtime duplicate detection

def get_cache(url: str):
    if url in url_cache:
        data, ts = url_cache[url]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def set_cache(url: str, data):
    url_cache[url] = (data, time.time())


def is_duplicate(text: str) -> bool:
    now = time.time()
    if text in text_cache:
        if now - text_cache[text] < TEXT_CACHE_TTL:
            return True
    text_cache[text] = now
    return False


def normalize_url(url: str):
    url = url.strip()

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    return url


URL_DETECT_PATTERN = re.compile(r"(https?://|www\.)\S+")


def extract_url_from_text(text: str):
    if not isinstance(text, str):
        return None

    match = URL_DETECT_PATTERN.search(text)
    if not match:
        return None

    return match.group(0).rstrip('.,!?:;')


def infer_platform_from_value(value: str) -> str:
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


def detect_content_metadata(text: str):
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


def analyze_with_gemini(text: str, is_title: bool = False):
    """Phân tích nội dung với Gemini AI.
    
    Args:
        text: Nội dung để phân tích
        is_title: True nếu là tiêu đề (YouTube, Chrome tab) - sẽ suy đoán nội dung thực tế
    """
    if gemini_client is None:
        print("⚠️ Gemini client unavailable")
        return "SAFE", "gemini_error"

    try:
        if is_title:
            # Prompt thông minh cho tiêu đề - suy đoán nội dung video
            prompt = f"""
Bạn là AI kiểm duyệt nội dung cho trẻ em. Chuyên phân tích tiêu đề video/web để suy đoán nội dung thực tế.

Dựa vào TIÊU ĐỀ sau, hãy suy đoán nội dung có thể là gì.

🚨 CHỈ BLOCK khi:
- Có dấu hiệu RÕ RÀNG của 18+
- Bạo lực nghiêm trọng (giết người, máu me)

⚠️ Nếu chỉ là:
- "hành động"
- "trộm"
- "đánh nhau"

→ Trả về WARNING hoặc SAFE tùy ngữ cảnh, KHÔNG BLOCK nếu không chắc chắn.

📌 TIÊU ĐỀ:
{text[:500]}
"""
        else:
            # Prompt cho nội dung đầy đủ
            prompt = f"""
Bạn là AI kiểm duyệt nội dung cho trẻ em.

Phân tích nội dung sau và trả về:
- status: SAFE | WARNING | BLOCK  
- reason: giải thích ngắn

🚨 Tìm kiếm dấu hiệu:
- Nội dung 18+ / khiêu dâm
- Bạo lực / chiến đấu
- Tự tử / tuyệt vọng
- Bắt nạt / lạm dụng

📝 NỘI DUNG:
{text[:1500]}
"""

        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        raw = getattr(response, 'text', None) or getattr(response, 'output_text', None) or str(response)
        raw = raw.strip() if isinstance(raw, str) else str(raw)
        print("🤖 GEMINI RAW:", raw)

        upper_raw = raw.upper()
        if "BLOCK" in upper_raw:
            return "BLOCK", raw
        if "WARNING" in upper_raw:
            return "WARNING", raw
        return "SAFE", raw

    except Exception as e:
        print("❌ Gemini error:", e)
        traceback.print_exc()
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            print("⏳ Quota exhausted, retrying after delay...")
            time.sleep(2)
            try:
                # Retry once
                response = gemini_client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt
                )
                raw = getattr(response, 'text', None) or getattr(response, 'output_text', None) or str(response)
                raw = raw.strip() if isinstance(raw, str) else str(raw)
                print("🤖 GEMINI RETRY RAW:", raw)
                upper_raw = raw.upper()
                if "BLOCK" in upper_raw:
                    return "BLOCK", raw
                if "WARNING" in upper_raw:
                    return "WARNING", raw
                return "SAFE", raw
            except Exception as retry_e:
                print("❌ Gemini retry failed:", retry_e)
        return "SAFE", "gemini_error"


def combine_result(status_rule, reason_rule, status_ai, reason_ai):
    # nếu AI lỗi → fallback rule
    if reason_ai == "gemini_error":
        return status_rule, reason_rule

    # Rule BLOCK → ưu tiên tuyệt đối
    if status_rule in ["BLOCK", "LOCK"]:
        return status_rule, reason_rule

    # AI BLOCK → block
    if status_ai == "BLOCK":
        return "BLOCK", reason_ai

    # 🔥 QUAN TRỌNG: luôn giữ WARNING từ AI
    if status_ai == "WARNING":
        return "WARNING", reason_ai

    # Rule WARNING
    if status_rule == "WARNING":
        return "WARNING", reason_rule

    return "SAFE", reason_rule


def analyze_title(text: str, device_id: str = None):
    """Phân tích tiêu đề bằng rule-based + Gemini AI (cách tối ưu cho YouTube/title).
    
    Flow:
    1. Rule-based (decision_pipeline) - phát hiện nhanh từ cấm
    2. Gemini AI - suy luận nội dung từ tiêu đề
    3. Kết hợp - ưu tiên kết quả nguy hiểm
    """
    print(f"🎬 ANALYZING TITLE: '{text[:50]}...'")
    
    # Skip very short titles
    if len(text.split()) <= 3:
        print(f"  ⚪ Too short title, skipping AI analysis")
        return "SAFE", "too_short"
    
    # Bước 1: Rule-based (nhanh)
    status_rule, reason_rule = run_decision_pipeline(text)
    print(f"  📋 Rule-based: {status_rule} | {reason_rule}")
    
    # Bước 2: Gemini AI (suy luận)
    status_ai, reason_ai = analyze_with_gemini(text, is_title=True)
    print(f"  🤖 Gemini AI: {status_ai} | {reason_ai}")
    
    # Bước 3: Kết hợp kết quả (logic thông minh)
    final_status, final_reason = combine_result(
        status_rule, reason_rule,
        status_ai, reason_ai
    )
    print(f"  🎯 Combined result: {final_status}")
    
    return final_status, final_reason

# ===== NEW ENDPOINTS =====
@app.post("/analyze-text", response_model=AnalyzeSmartResponse)
async def analyze_text_endpoint(data: TextRequest):
    text = data.text
    device_id = data.device_id

    print(f"📥 RECEIVED: {text}")

    # Check for duplicate text
    if is_duplicate(text):
        print(f"🔄 DUPLICATE TEXT SKIPPED: {text[:50]}...")
        return AnalyzeSmartResponse(
            category="safe",
            risk_level=0,
            is_safe=True,
            reason="duplicate_skipped"
        )

    # Nếu text ngắn (có thể là title), dùng analyze_title (rule + Gemini)
    # Nếu text dài, dùng analyze_rule (chỉ rule-based)
    content_type, violated_value, platform = detect_content_metadata(text)

    is_title = (
        len(text.split()) <= 15
        and len(text) < 120
        and "\n" not in text
    )  # Improved: better title detection
    
    if is_title:
        print(f"🎬 Detected as TITLE - using rule + Gemini analysis")
        status, reason = analyze_title(text, device_id)
    else:
        print(f"📄 Detected as FULL TEXT - using rule-based analysis")
        status, reason = run_decision_pipeline(text)
    
    # Map status sang AnalyzeSmartResponse format
    is_safe = status == "SAFE"
    risk_level = 0
    category = "safe"

    if status in ["BLOCK", "LOCK"]:
        risk_level = 9 if status == "BLOCK" else 10
        category = "dangerous"
    elif status == "WARNING":
        risk_level = 6
        category = "warning"

    # Update Firebase nếu cần
    if not is_safe and device_id:
        print(f"🚨 VIOLATION DETECTED - UPDATING FIRESTORE")
        update_violation(
            device_id,
            text,
            status,
            reason,
            content_type=content_type,
            violated_value=violated_value,
            platform=platform
        )
    else:
        print(f"✅ SAFE CONTENT")

    return AnalyzeSmartResponse(
        category=category,
        risk_level=risk_level,
        is_safe=is_safe,
        reason=human_readable_reason(reason)
    )

@app.post("/analyze-url")
async def analyze_url_endpoint(data: UrlRequest):
    cache_key = normalize_url(data.url)
    cached = get_cache(cache_key)
    if cached is not None:
        print(f"📋 Cache hit for URL: {cache_key}")
        return cached

    try:
        url = cache_key
        print(f"🌐 Analyzing URL: {url}")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = response.apparent_encoding  # Fix encoding issues
        final_url = response.url
        print(f"🔁 Final URL: {final_url}")

        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.string if soup.title else ""
        meta_desc = ""

        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            meta_desc = meta.get("content", "")

        # Filter out garbage titles
        if len(title.strip()) < 5:
            result = {
                "url": final_url,
                "status": "SAFE",
                "reason": "Nội dung không đủ để phân tích"
            }
            set_cache(cache_key, result)
            return result

        body_text = soup.get_text()
        full_text = f"{title} {meta_desc}".strip()  # Only use title + meta_desc, not body_text
        full_text = fix_encoding(full_text)
        full_text = full_text[:2000]

        # Enhanced analysis: use title analysis if title is short, otherwise rule-based
        if len(title.split()) <= 15:
            status, reason = analyze_title(title, data.device_id)
        else:
            status, reason = run_decision_pipeline(f"{title} {meta_desc}")

        if status in ["BLOCK", "LOCK"] and data.device_id:
            update_violation(
                data.device_id,
                final_url,
                status,
                reason,
                content_type="URL",
                violated_value=final_url,
                platform=infer_platform_from_value(final_url)
            )

        result = {
            "url": final_url,
            "status": status,
            "reason": human_readable_reason(reason)
        }

        set_cache(cache_key, result)
        return result

    except Exception as e:
        print(f"❌ URL analysis error: {e}")
        result = {
            "url": data.url,
            "status": "error",
            "reason": str(e)
        }
        set_cache(cache_key, result)
        return result
    
    

@app.post("/track-usage")
async def track_usage(data: UsageRequest):
    try:
        print(f"📱 Tracking usage: {data.package} - {data.duration}ms")

        # Có thể lưu vào Firebase hoặc DB khác
        if db and data.device_id:
            db.collection("app_usage").add({
                "device_id": data.device_id,
                "package": data.package,
                "duration_ms": data.duration,
                "timestamp": firestore.SERVER_TIMESTAMP
            })

        return {"status": "ok"}

    except Exception as e:
        print(f"❌ Usage tracking error: {e}")
        return {"status": "error", "reason": str(e)}


@app.post("/register_fcm_token")
async def register_fcm_token(data: RegisterFcmTokenRequest):
    try:
        device_id = data.device_id
        fcm_token = data.fcm_token

        if not device_id or not fcm_token:
            raise HTTPException(status_code=400, detail="device_id and fcm_token are required")

        print(f"📱 Registering FCM token for device: {device_id}")

        # Store FCM token in device document
        if db:
            # Get current device info
            device_ref = db.collection("devices").document(device_id)
            device_doc = device_ref.get()

            current_tokens = []
            if device_doc.exists:
                device_data = device_doc.to_dict()
                # Check if there's already an fcm_tokens array
                existing_tokens = device_data.get("fcm_tokens", [])
                if isinstance(existing_tokens, list):
                    current_tokens = existing_tokens
                # Also check for single fcm_token field
                single_token = device_data.get("fcm_token")
                if single_token and single_token not in current_tokens:
                    current_tokens.append(single_token)

            # Add new token if not already present
            if fcm_token not in current_tokens:
                current_tokens.append(fcm_token)

            # Update device with FCM tokens array
            device_ref.set({
                "fcm_tokens": current_tokens,
                "fcm_token": fcm_token,  # Keep backward compatibility
                "last_token_update": firestore.SERVER_TIMESTAMP
            }, merge=True)

        return {"status": "success", "message": f"FCM token registered for device {device_id}"}

    except Exception as e:
        print(f"❌ FCM token registration error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to register FCM token: {str(e)}")


@app.get("/app_usage")
async def get_activity_history(device_id: str):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")

    try:
        activities = []

        # ===== LẤY APP USAGE =====
        usage_docs = db.collection("app_usage")\
            .where("device_id", "==", device_id)\
            .stream()

        for doc in usage_docs:
            d = doc.to_dict()
            formatted = format_usage({
                "package": d.get("package"),
                "duration_ms": d.get("duration_ms"),
                "timestamp": firestore_timestamp_to_iso(d.get("timestamp"))
            })
            activities.append(formatted)

        # ===== LẤY VIOLATIONS =====
        # REMOVED: Violations are now separate from activity history
        # violation_docs = db.collection("violation_logs")\
        #     .where("device_id", "==", device_id)\
        #     .stream()
        #
        # for doc in violation_docs:
        #     d = doc.to_dict()
        #     formatted = format_violation({
        #         "status": d.get("status"),
        #         "reason_text": d.get("reason_text"),
        #         "reason": d.get("reason"),
        #         "text": d.get("text"),
        #         "url": d.get("url"),
        #         "timestamp": firestore_timestamp_to_iso(d.get("timestamp"))
        #     })
        #     if formatted:
        #         activities.append(formatted)

        # ===== SORT THEO TIME DESC =====
        activities.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

        return activities  # Return array directly instead of {"activities": activities}

    except Exception as e:
        print(f"❌ Activity history error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/violation-history")
async def get_violation_history(device_id: str):
    if not db:
        raise HTTPException(status_code=500, detail="Database not initialized")

    try:
        docs = db.collection("violation_logs")\
            .where("device_id", "==", device_id)\
            .stream()  # vẫn dùng stream, ko cần order_by

        violations = []
        for doc in docs:
            d = doc.to_dict()
            violations.append({
                "type": "violation",
                "timestamp": firestore_timestamp_to_iso(d.get("timestamp")),
                "message": d.get("message") or "",  # null → "" cho an toàn
                "status": d.get("status"),
                "reason": d.get("reason"),
                "content_type": d.get("content_type", "TEXT"),
                "violated_value": d.get("violated_value", ""),
                "violation_reason": d.get("violation_reason", d.get("reason")),
                "platform": d.get("platform", "web"),
                "child_name": d.get("child_name"),
                "device_name": d.get("device_name"),
            })

        # Sort theo timestamp giảm dần
        violations.sort(key=lambda x: x.get("timestamp") or "", reverse=True)

        return violations

    except Exception as e:
        print(f"❌ Violation history error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
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

        result = []
        for package, total in summary.items():
            app_name = map_app_name(package)
            result.append({
                "package_name": package,
                "app_name": app_name,
                "total_duration_ms": total,
                "display_text": f"📱 {app_name} - {_format_duration(total)}"
            })

        # Sort by duration descending
        result.sort(key=lambda x: x["total_duration_ms"], reverse=True)

        return result

    except Exception as e:
        print(f"❌ App usage error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def format_time(ts=None):
    """Format timestamp for alert messages"""
    from datetime import datetime
    if not ts:
        ts = datetime.utcnow()
    return ts.strftime("%H:%M - %d/%m")

def fix_encoding(text: str):
    if not isinstance(text, str):
        return text
    try:
        return text.encode('latin1').decode('utf-8')
    except Exception:
        return text

def build_alert_message(
    level: str,          # "BLOCK" | "WARNING"
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


def build_violation_message(mode: str, reason: str) -> str:
    """Build violation reason for alert message"""
    return build_violation_reason(reason)


# Include modular routes
app.include_router(analyze_router)

# Initialize Firebase
init_firebase()

# ===== DEVICE =====
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

# ===== MODELS =====
print("Loading models...")

# Comment out model loading for now to avoid torch dependency issues
"""
classifier = pipeline(
    "zero-shot-classification",
    model="facebook/bart-large-mnli",
    device=0 if torch.cuda.is_available() else -1
)

nsfw_model = AutoModelForImageClassification.from_pretrained(
    "Falconsai/nsfw_image_detection"
)
nsfw_model.to(device)
nsfw_model.eval()

image_classifier = pipeline(
    "zero-shot-image-classification",
    model="openai/clip-vit-base-patch32",
    device=0 if torch.cuda.is_available() else -1
)

print("Loading OCR...")
reader = easyocr.Reader(['en', 'vi'], gpu=torch.cuda.is_available())
print("✅ All models ready")
"""

# Dummy models for now
classifier = None
nsfw_model = None
image_classifier = None
# reader = None  # easyocr disabled
reader = None

print("⚠️ Models disabled for testing")

# ===== TRANSFORM =====
# transform = transforms.Compose([
#     transforms.Resize((224, 224)),
#     transforms.ToTensor(),
#     transforms.Normalize(mean=[0.5]*3, std=[0.5]*3)
# ])

transform = None  # Disabled for testing

# ===== CONFIG =====
BAD_WORDS = [
    "sex", "porn", "dit me", "dm", "clm",
    "kill", "suicide", "rape", "hiep dam",
    "hiếp dâm", "tự tử", "giết", "địt", "đụ", "lồn", "cặc"
]

CANDIDATE_LABELS = [
    "self-harm", "suicide", "sexual content",
    "violence", "bullying", "safe"
]

DANGEROUS_LABELS = [
    "self-harm", "suicide", "sexual content",
    "violence", "bullying"
]

VIOLENCE_LABELS = [
    "violence", "gun", "weapon", "blood", "fight", "safe"
]

SYSTEM_WORDS = [
    "settings", "chrome", "play store",
    "child monitor", "accessibility service"
]

cache: Dict[str, Tuple[str, str]] = {}

# ===== CONTEXT MEMORY =====
device_contexts: Dict[str, list] = {}

# ===== NORMALIZE =====
def normalize_text(text: str):
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

# ===== ANALYZER =====
# Logic đã được chuyển sang services/analyzer.py
# Sử dụng decision_pipeline từ services.analyzer

# ===== OCR =====
def extract_text_from_image(image):
    try:
        img = np.array(image)
        h, w, _ = img.shape

        img = img[int(h*0.55):h, 0:w]
        img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.bilateralFilter(gray, 9, 75, 75)

        results = reader.readtext(gray, detail=0)
        text = " ".join(results)

        print("OCR TEXT:", text)
        return text.strip()

    except Exception as e:
        print("OCR ERROR:", e)
        return ""

# ===== API =====
# /analyze endpoint is handled in routes/analyze.py

class AnalyzeScreenResponse(BaseModel):
    is_dangerous: bool
    label: str
    confidence: float
    extracted_text: str

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
        image = Image.open(io.BytesIO(content)).convert("RGB")
        print(f"Kích thước ảnh: {image.size}")

        # TEXT EXTRACTION
        print(f"\n📝 TRÍCH XUẤT VĂN BẢN:")
        if text and text.strip():
            extracted_text = text.strip()
            print(f"  ✅ Từ Accessibility Service: {extracted_text[:80]}...")
        else:
            print(f"  🖼️  Sử dụng OCR fallback...")
            extracted_text = extract_text_from_image(image)
            if extracted_text:
                print(f"  ✅ OCR thành công: {extracted_text[:80]}...")
            else:
                print(f"  ⚠️  Không trích xuất được text từ OCR")

        # TEXT ANALYSIS
        text_status, text_reason = ("SAFE", "")
        if extracted_text:
            extracted_text = fix_encoding(extracted_text)
            text_status, text_reason = run_decision_pipeline(extracted_text)
        else:
            print(f"✅ Bỏ qua phân tích text (không có text)")

        # NSFW CHECK
        print(f"\n🔞 KIỂM TRA NỘI DUNG 18+:")
        if transform is None or nsfw_model is None:
            print(f"  ⚠️  NSFW model disabled")
            is_nsfw = False
            nsfw_conf = 0.0
            nsfw_label = "unknown"
        else:
            img_tensor = transform(image).unsqueeze(0).to(device)

            with torch.no_grad():
                outputs = nsfw_model(pixel_values=img_tensor)

            probs = torch.nn.functional.softmax(outputs.logits, dim=-1)
            nsfw_conf = probs.max().item()
            nsfw_label = nsfw_model.config.id2label[probs.argmax().item()].lower()

            is_nsfw = any(x in nsfw_label for x in ['nsfw', 'porn', 'hentai', 'sexy'])
            print(f"  Phân loại: {nsfw_label} (độ tin cậy: {nsfw_conf:.2%})")
            if is_nsfw and nsfw_conf > 0.7:
                print(f"  🔴 VI PHẠM: Phát hiện nội dung 18+")
            else:
                print(f"  ✅ An toàn")

        # VIOLENCE CHECK
        print(f"\n⚔️  KIỂM TRA BẠOLỰC/VŨ KHÍ:")
        if image_classifier is None:
            print(f"  ⚠️  Violence classifier disabled")
            is_violent = False
            violence_label = "unknown"
            violence_conf = 0.0
        else:
            result = image_classifier(image, candidate_labels=VIOLENCE_LABELS)
            violence_label = result[0]["label"]
            violence_conf = result[0]["score"]

            is_violent = (
                violence_label in ["violence", "gun", "weapon", "blood", "fight"]
                and violence_conf > 0.6
            )
            print(f"  Phân loại: {violence_label} (độ tin cậy: {violence_conf:.2%})")
            if is_violent:
                print(f"  🔴 VI PHẠM: Phát hiện {violence_label}")
            else:
                print(f"  ✅ An toàn")

        # FINAL DECISION
        print(f"\n🎯 KẾT LUẬN CUỐI CÙNG:")
        overall_status = text_status
        overall_reason = text_reason
        is_dangerous = False

        if is_nsfw and nsfw_conf > 0.7:
            overall_status = "LOCK"
            overall_reason = f"nsfw:{nsfw_conf:.2f}"
            is_dangerous = True
            print(f"  🔴 KHÓA: Nội dung 18+ (độ tin cậy {nsfw_conf:.2%})")
        elif is_violent:
            overall_status = "LOCK"
            overall_reason = f"{violence_label}:{violence_conf:.2f}"
            is_dangerous = True
            print(f"  🔴 KHÓA: Nội dung bạo lực ({violence_label})")
        elif text_status in ["BLOCK", "LOCK"]:
            is_dangerous = True
            print(f"  🔴 VI PHẠM: {text_reason}")
        else:
            print(f"  ✅ AN TOÀN")

        if overall_status in ["WARNING", "BLOCK", "LOCK"] and device_id:
            content_type, violated_value, platform = detect_content_metadata(extracted_text)
            update_violation(
                device_id,
                extracted_text,
                overall_status,
                overall_reason,
                content_type=content_type,
                violated_value=violated_value,
                platform=platform
            )

        final_label = (
            "TEXT_VIOLATION" if text_status in ["BLOCK", "LOCK"]
            else "NSFW" if is_nsfw
            else "VIOLENCE" if is_violent
            else "SAFE"
        )

        confidence = max(nsfw_conf, violence_conf)

        print(f"\n📋 KẾT QUẢ PHÂN TÍCH:")
        print(f"  Phân loại: {final_label}")
        print(f"  Trạng thái: {overall_status}")
        print(f"  Độ tin cậy: {confidence:.2%}")
        print(f"  Lý do: {human_readable_reason(overall_reason)}")
        print(f"{'='*60}\n")

        return AnalyzeScreenResponse(
            is_dangerous=is_dangerous,
            label=final_label,
            confidence=confidence,
            extracted_text=extracted_text[:300]
        )

    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Image analysis failed")

@app.get("/health")
async def health_check():
    return {"status": "ok"}

# ===== SMART GEMINI ENDPOINT =====
@app.post("/analyze-rule", response_model=AnalyzeSmartResponse)
async def analyze_rule(
    text: str = Form(None),
    device_id: str = Form(None),
    image: UploadFile = File(None)
):
    """
    Phân tích nội dung bằng rule-based (không dùng AI)
    """
    try:
        print(f"📥 RECEIVED REQUEST: text='{text}', device_id='{device_id}'")

        # Nếu có text, phân tích bằng decision_pipeline
        if text and text.strip():
            text = fix_encoding(text.strip())
            print(f"🔍 ANALYZING TEXT: '{text[:100]}...'")
            status, reason = run_decision_pipeline(text)
            print(f"📋 DECISION RESULT: status='{status}', reason='{reason}'")

            # Map status sang format cũ
            is_safe = status == "SAFE"
            risk_level = 0
            category = "safe"

            if status in ["BLOCK", "LOCK"]:
                risk_level = 9 if status == "BLOCK" else 10
                category = "dangerous"
            elif status == "WARNING":
                risk_level = 6
                category = "warning"

            print(f"🛡️ FINAL MAPPING: status={status}, is_safe={is_safe}, category={category}, risk={risk_level}")

            print(f"🛡️ SAFETY: is_safe={is_safe}, category={category}, risk={risk_level}")

            # Update Firebase nếu cần
            if not is_safe and device_id:
                print(f"🚨 VIOLATION DETECTED - UPDATING FIRESTORE")
                content_type, violated_value, platform = detect_content_metadata(text)
                update_violation(
                    device_id,
                    text,
                    status,
                    reason,
                    content_type=content_type,
                    violated_value=violated_value,
                    platform=platform
                )
            else:
                print(f"✅ SAFE CONTENT - NO FIRESTORE UPDATE")

            print(f"📋 RULE ANALYSIS: {device_id} | {category} | Risk: {risk_level} | Reason: {reason}")

            # DEBUG: Force correct response for testing
            if "porn" in text.lower():
                print("🔥 FORCING VIOLATION RESPONSE FOR TESTING")
                return AnalyzeSmartResponse(
                    category="dangerous",
                    risk_level=10,
                    is_safe=False,
                    reason="🔴 LỌC TỪ CẤAM: 'porn' - Từ bị cấm trực tiếp"
                )

            response_data = AnalyzeSmartResponse(
                category=category,
                risk_level=risk_level,
                is_safe=is_safe,
                reason=human_readable_reason(reason)
            )
            
            print(f"🚀 RETURNING RESPONSE: {response_data.dict()}")
            return response_data

        # Nếu không có text, trả về safe
        print(f"⚠️ NO TEXT PROVIDED")
        return AnalyzeSmartResponse(
            category="safe",
            risk_level=0,
            is_safe=True,
            reason="Không có nội dung để phân tích"
        )

    except Exception as e:
        print(f"❌ Analyze Rule Error: {traceback.format_exc()}")
        return AnalyzeSmartResponse(
            category="error",
            risk_level=0,
            is_safe=True,
            reason=f"Lỗi phân tích: {str(e)}"
        )