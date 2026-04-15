# ⚡ QUICK START - API Key Setup & Run

## 🔥 30 GIÂY - LÀM NGAY

### 1. Lấy API Key
- Mở: https://makersuite.google.com/app/apikeys
- Click "Create API key"
- Copy key

### 2. Set API Key (Chọn 1)

#### Cách A: PowerShell (Nhanh nhất)
```powershell
$env:GEMINI_API_KEY = "paste_your_key_here"
```

#### Cách B: .env file (Recommend)
```bash
# Tạo file: C:\Users\MSI\Desktop\ChildMonitorSystem\ai_service\.env
GEMINI_API_KEY=paste_your_key_here
```

Thêm code này vào [main.py](main.py) (line 1):
```python
from dotenv import load_dotenv
load_dotenv()
```

Install dotenv:
```bash
pip install python-dotenv
```

### 3. Run Server
```bash
cd C:\Users\MSI\Desktop\ChildMonitorSystem\ai_service
uvicorn main:app --reload
```

### 4. Test
```bash
curl -X POST "http://localhost:8000/analyze-smart" \
  -F "text=hello world" \
  -F "device_id=test"
```

---

## ✅ Expected Output
```json
{
  "category": "safe",
  "risk_level": 1,
  "is_safe": true,
  "reason": "Nội dung an toàn"
}
```

---

## ❌ Troubleshoot

| Lỗi | Giải Pháp |
|-----|----------|
| `ModuleNotFoundError: google` | `pip install google-generativeai` |
| `GEMINI_API_KEY not set` | Set biến môi trường hoặc tạo .env |
| `Invalid API Key` | Lấy key mới |
| Crash khi start | Xem build logs |

---

**✅ Ready to go!** 🚀
