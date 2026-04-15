import asyncio
import time
from datetime import datetime
from utils import simplify_reason, firestore_timestamp_to_iso, build_alert_message
from fcm_service import send_violation_push_notification
from google.cloud.firestore import SERVER_TIMESTAMP


async def broadcast_violation(device_id: str, data: dict, db=None, connected_clients=None):
    """Broadcast violation qua WebSocket"""
    if connected_clients is None:
        connected_clients = {}
    
    print(f"📢 Broadcasting violation to {device_id}: {data.get('violated_value', 'N/A')[:50]}...")
    ws = connected_clients.get(device_id)

    if not ws:
        print("❌ No WS client")
        # Fallback: save to realtime_queue for later processing
        if db:
            try:
                db.collection("realtime_queue").add(data)
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
        if db:
            try:
                db.collection("realtime_queue").add(data)
                print("📋 Saved to realtime_queue (send failed)")
            except Exception as queue_e:
                print(f"❌ Failed to save to realtime_queue: {queue_e}")


def update_violation(
    device_id: str,
    text: str,
    mode: str,
    reason: str,
    content_type: str = "TEXT",
    violated_value: str = None,
    platform: str = "web",
    db=None,
    connected_clients=None,
):
    """Update violation vào Firebase"""
    if connected_clients is None:
        connected_clients = {}
    
    if not db or not device_id:
        print(f"❌ No DB or device_id: db={db}, device_id={device_id}")
        return
    
    try:
        print(f"🔥 UPDATING FIRESTORE for device: {device_id}")
        short_reason = simplify_reason(reason)
        if violated_value is None:
            violated_value = text

        # Save to devices (quick access for last violation)
        db.collection("devices").document(device_id).set({
            "last_violation": text[:200],
            "mode": mode,
            "reason": reason,
            "reason_text": short_reason,
            "content_type": content_type,
            "platform": platform,
            "timestamp": SERVER_TIMESTAMP,
            "text_length": len(text),
            "check_type": reason.split(":")[0] if ":" in reason else reason
        }, merge=True)

        # Get device info
        device_info_doc = db.collection("devices").document(device_id).get()
        device_info = device_info_doc.to_dict() if device_info_doc.exists else {}

        device_name = device_info.get("deviceName", device_info.get("device_name", "Unknown Device"))
        child_name = device_info.get("childName", "Unknown User")

        # If we have childId but no childName -> fetch from users
        child_id = device_info.get("childId")
        if child_id and child_name == "Unknown User":
            try:
                user_doc = db.collection("users").document(child_id).get()
                if user_doc.exists:
                    child_name = user_doc.to_dict().get("name", "Unknown User")
            except Exception as e:
                print(f"❌ Get user error: {e}")

        print(f"📱 Device Name: {device_name}")
        print(f"👤 Child Name: {child_name}")

        # Build alert message
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
            "message": alert_message,
            "reason": short_reason,
            "raw_reason": reason,
            "content_type": content_type,
            "violated_value": violated_value,
            "violation_reason": short_reason,
            "platform": platform,
            "timestamp": SERVER_TIMESTAMP,
            "isRead": False
        })

        # Broadcast violation event
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
            "messageId": f"{device_id}-{int(time.time() * 1000)}-{text[:20].replace(' ', '_')}"
        }

        try:
            asyncio.create_task(broadcast_violation(device_id, violation_data, db, connected_clients))
        except Exception as e:
            print(f"❌ Broadcast task error: {e}")

        # Always send FCM for critical events, even if WebSocket is connected.
        # Client must deduplicate based on messageId.
        if mode in ["BLOCK", "LOCK", "WARNING"]:
            print(f"📡 Sending FCM for critical event for {device_id}")
            try:
                asyncio.create_task(send_violation_push_notification(
                    device_id=device_id,
                    mode=mode,
                    child_name=child_name,
                    device_name=device_name,
                    reason=short_reason,
                    violated_value=violated_value,
                    db=db,
                    message_id=violation_data["messageId"],
                    child_id=child_id
                ))
            except Exception as e:
                print(f"❌ FCM push notification task error: {e}")

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


def format_usage(item: dict):
    """Format usage item"""
    from utils import map_app_name
    
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
    """Format violation item"""
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
