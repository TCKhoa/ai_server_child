import json
import os
import requests

from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud.firestore_v1.base_query import FieldFilter

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_CREDENTIALS = None
FCM_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")


# =====================================================
# LOAD CONFIG
# =====================================================
def load_fcm_project_id():
    global FCM_PROJECT_ID

    if FCM_PROJECT_ID:
        return FCM_PROJECT_ID

    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        key_path = os.path.join(base_dir, "firebase_key.json")

        with open(key_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
            FCM_PROJECT_ID = payload.get("project_id")

        print(f"✅ FCM Project ID: {FCM_PROJECT_ID}")
        return FCM_PROJECT_ID

    except Exception as e:
        print(f"❌ Load project id error: {e}")
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

        print("✅ FCM Credentials loaded")
        return FCM_CREDENTIALS

    except Exception as e:
        print(f"❌ Load credentials error: {e}")
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
        print(f"❌ Get access token error: {e}")
        return None


# =====================================================
# TOKEN HELPERS
# =====================================================
def get_fcm_tokens_for_device(device_id: str, db=None):
    """
    Fallback: lấy token của child device
    """
    if not db or not device_id:
        return []

    try:
        doc = db.collection("devices").document(device_id).get()

        if not doc.exists:
            return []

        data = doc.to_dict()

        tokens = data.get("fcm_tokens", []) or []
        single = data.get("fcm_token")

        if single and single not in tokens:
            tokens.append(single)

        tokens = list(set([t.strip() for t in tokens if t]))

        print(f"📱 Device tokens found: {len(tokens)}")
        return tokens

    except Exception as e:
        print(f"❌ Get device tokens error: {e}")
        return []


def get_parent_tokens_by_child(child_id: str, db=None):
    """
    🔥 CHUẨN:
    Tìm tất cả user role=parent có childId = child_id
    rồi gom toàn bộ token
    """
    if not db or not child_id:
        return []

    try:
        print(f"🔍 Looking for parents of child_id: {child_id}")

        docs = (
            db.collection("users")
            .where(filter=FieldFilter("role", "==", "parent"))
            .where(filter=FieldFilter("childId", "==", child_id))
            .stream()
        )

        tokens = []
        parent_count = 0

        for doc in docs:
            parent_count += 1
            data = doc.to_dict()

            print(f"👤 Parent found: {doc.id}")

            single = data.get("fcm_token")
            multi = data.get("fcm_tokens", [])

            if single:
                tokens.append(single)

            if isinstance(multi, list):
                tokens.extend(multi)

        tokens = list(set([t.strip() for t in tokens if t and isinstance(t, str)]))

        print(f"👨‍👩‍👧 Parents found: {parent_count}")
        print(f"📲 Parent tokens found: {len(tokens)}")

        return tokens

    except Exception as e:
        print(f"❌ Get parent tokens error: {e}")
        return []


def remove_invalid_token(token: str, db=None):
    """
    🔥 Xóa token chết khỏi users + devices
    """
    if not db or not token:
        return

    try:
        # USERS
        users = db.collection("users").stream()

        for doc in users:
            data = doc.to_dict()
            arr = data.get("fcm_tokens", [])

            updated = False

            if data.get("fcm_token") == token:
                db.collection("users").document(doc.id).update({
                    "fcm_token": ""
                })
                updated = True

            if token in arr:
                arr.remove(token)
                db.collection("users").document(doc.id).update({
                    "fcm_tokens": arr
                })
                updated = True

            if updated:
                print(f"🗑 Removed dead token from user {doc.id}")

        # DEVICES
        devices = db.collection("devices").stream()

        for doc in devices:
            data = doc.to_dict()
            arr = data.get("fcm_tokens", [])

            updated = False

            if data.get("fcm_token") == token:
                db.collection("devices").document(doc.id).update({
                    "fcm_token": ""
                })
                updated = True

            if token in arr:
                arr.remove(token)
                db.collection("devices").document(doc.id).update({
                    "fcm_tokens": arr
                })
                updated = True

            if updated:
                print(f"🗑 Removed dead token from device {doc.id}")

    except Exception as e:
        print(f"❌ Remove token error: {e}")


# =====================================================
# SEND CORE
# =====================================================
def send_fcm(token: str, payload: dict, db=None):
    access_token = get_access_token()
    project_id = load_fcm_project_id()

    if not access_token or not project_id:
        print("❌ Missing FCM config")
        return False

    url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        res = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=10,
        )

        if res.status_code not in (200, 201):
            print(f"❌ FCM ERROR {res.status_code}: {res.text}")

            try:
                error_json = res.json()
                error_status = error_json.get("error", {}).get("status", "")
            except:
                error_status = ""

            if error_status in ["UNREGISTERED", "NOT_FOUND"]:
                print(f"🔥 DEAD TOKEN: {token[:15]}...")
                remove_invalid_token(token, db)

            return False

        print(f"✅ Sent to {token[:12]}...")
        return True

    except Exception as e:
        print(f"❌ FCM request error: {e}")
        return False


# =====================================================
# ALERT TYPES
# =====================================================
def send_lock_alert(token, title, body, data=None, db=None):
    data = data or {}
    mode = data.get("type", "LOCK")

    payload = {
        "message": {
            "token": token,
            "android": {
                "priority": "HIGH",
            },
            "data": {
                "title": title,
                "body": body,
                "riskLevel": mode,
                **{str(k): str(v) for k, v in data.items()},
            },
        }
    }

    return send_fcm(token, payload, db)


def send_warning_alert(token, title, body, data=None, db=None):
    data = data or {}
    mode = data.get("type", "WARNING")

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
                    "channel_id": "warning_channel_v2",
                    "sound": "default",
                },
            },
            "data": {
                "title": title,
                "body": body,
                "riskLevel": mode,
                **{str(k): str(v) for k, v in data.items()},
            },
        }
    }

    return send_fcm(token, payload, db)


# =====================================================
# MULTI SEND
# =====================================================
async def send_fcm_to_tokens(tokens, title, body, data=None, db=None):
    import asyncio

    if not tokens:
        print("⚠️ No tokens")
        return

    mode = (data or {}).get("type", "WARNING")

    tasks = []

    for token in tokens:
        if mode in ["LOCK", "BLOCK"]:
            tasks.append(
                asyncio.to_thread(
                    send_lock_alert,
                    token,
                    title,
                    body,
                    data,
                    db,
                )
            )
        else:
            tasks.append(
                asyncio.to_thread(
                    send_warning_alert,
                    token,
                    title,
                    body,
                    data,
                    db,
                )
            )

    await asyncio.gather(*tasks)


# =====================================================
# MAIN ENTRY
# =====================================================
async def send_violation_push_notification(
    device_id: str,
    mode: str,
    child_name: str,
    device_name: str,
    reason: str,
    violated_value: str,
    db=None,
    message_id: str = None,
    child_id: str = None,
):
    """
    Ưu tiên gửi cho phụ huynh.
    Nếu không có child_id thì fallback child device.
    """

    if child_id:
        tokens = get_parent_tokens_by_child(child_id, db)
        print(f"📡 Sending FCM to parents of {child_id} (Found {len(tokens)} tokens)")
    else:
        tokens = get_fcm_tokens_for_device(device_id, db)
        print(f"⚠️ child_id missing -> fallback child device tokens")

    if not tokens:
        print(f"⚠️ No tokens found (child_id={child_id}, device={device_id})")
        return

    title = (
        "🔒 CẢNH BÁO NGHIÊM TRỌNG"
        if mode in ["LOCK", "BLOCK"]
        else "⚠️ Cảnh báo"
    )

    body = f"{child_name} ({device_name}): {reason}"

    data = {
        "type": mode,
        "child_name": child_name,
        "device_name": device_name,
        "reason": reason,
        "violated_value": violated_value or "",
        "device_id": device_id,
        "child_id": child_id or "",
        "platform": platform or "web",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if message_id:
        data["messageId"] = message_id

    await send_fcm_to_tokens(tokens, title, body, data, db)