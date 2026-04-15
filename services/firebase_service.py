import os
import firebase_admin
from firebase_admin import credentials, firestore

_db = None

def init_firebase():
    global _db
    try:
        if not firebase_admin._apps:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            key_path = os.path.join(base_dir, "..", "firebase_key.json")
            key_path = os.path.abspath(key_path)
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred)

        _db = firestore.client()
        print("✅ Firebase connected")
    except Exception as e:
        print(f"❌ Firebase Init Error: {e}")
        _db = None


def update_violation(device_id: str, text: str, mode: str, reason: str):
    if not _db or not device_id:
        print(f"❌ No DB or device_id: db={_db}, device_id={device_id}")
        return

    try:
        print(f"🔥 UPDATING FIRESTORE for device: {device_id}")
        _db.collection("devices").document(device_id).set({
            "last_violation": text[:200],
            "mode": mode,
            "reason": reason,
            "timestamp": firestore.SERVER_TIMESTAMP,
            "text_length": len(text)
        }, merge=True)
        print("✅ FIRESTORE UPDATED SUCCESSFULLY")

    except Exception as e:
        print(f"❌ Firebase Update Error: {e}")
        import traceback
        traceback.print_exc()
