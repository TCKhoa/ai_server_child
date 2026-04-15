# 🔧 FIX GEMINI ERROR - Chi Tiết

## 🚨 Lỗi Ban Đầu

```python
# ❌ SAI - ở dòng đầu file
response = client.models.generate_content(
    model="gemini-1.5-flash",
    contents=prompt  # ← prompt chưa định nghĩa!
)
text = response.text

# ❌ Kết quả:
# NameError: name 'prompt' is not defined
# Server crash khi start
```

---

## ✅ Nguyên Nhân & Giải Pháp

| Vấn Đề | Nguyên Nhân | Giải Pháp |
|--------|-----------|----------|
| **NameError: prompt** | `prompt` gọi ở global scope | Chuyển vào endpoint `/analyze-smart` |
| **Gọi Gemini khi start** | Bad architecture | Chỉ gọi khi request đến API |
| **SDK bị trộn** | `genai.Client` + `GenerativeModel` | Dùng `genai.Client.models.generate_content()` |
| **Hardcode API key** | Không an toàn | Dùng `os.getenv("GEMINI_API_KEY")` |

---

## 📝 Thay Đổi Chi Tiết

### 1️⃣ Import & Setup (Line 20-26)

#### ❌ Trước:
```python
from google import genai

client = genai.Client(api_key='AIzaSyD82Mf9f3IutzZrayz1VFKblAHTDyE1xeA')

response = client.models.generate_content(...)  # ← NameError!
```

#### ✅ Sau:
```python
from google import genai

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # Từ .env hoặc environment
if not GEMINI_API_KEY:
    print("⚠️  GEMINI_API_KEY chưa được set")

genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
```

**Lợi ích:**
- ✅ Không crash khi start nếu thiếu API key
- ✅ API key an toàn (không hardcode)
- ✅ Dễ config nhiều environment

---

### 2️⃣ Endpoint `/analyze-smart` (Line 554-616)

#### ❌ Trước:
```python
@app.post("/analyze-smart")
async def analyze_smart(...):
    prompt = f"..."
    
    model = genai.GenerativeModel("gemini-1.5-flash", ...)
    response = model.generate_content(inputs)  # ← API bị trộn
```

#### ✅ Sau:
```python
@app.post("/analyze-smart")
async def analyze_smart(...):
    if not genai_client:
        return error response  # ← Graceful handling
    
    prompt = f"..."
    
    contents = [prompt, image_optional]
    
    response = genai_client.models.generate_content(  # ← Đúng SDK
        model="gemini-1.5-flash",
        contents=contents,
        config={
            "response_mime_type": "application/json",
            "temperature": 0.1
        }
    )
```

**Lợi ích:**
- ✅ Chỉ gọi Gemini khi có request (không lãng phí)
- ✅ SDK đúng: `Client.models.generate_content()`
- ✅ Hỗ trợ text + image cho đúng

---

## 🚀 CÁCH RUN LẠI

### Step 1️⃣: Set API Key

**Cách 1 - PowerShell:**
```powershell
$env:GEMINI_API_KEY = "your_api_key_here"
```

**Cách 2 - CMD:**
```cmd
set GEMINI_API_KEY=your_api_key_here
```

**Cách 3 - .env file (khuyên dùng):**
```bash
# Tạo file .env trong thư mục ai_service/
# Nội dung:
GEMINI_API_KEY=your_api_key_here
```

Sau đó install python-dotenv:
```bash
pip install python-dotenv
```

Thêm vào đầu main.py:
```python
from dotenv import load_dotenv
load_dotenv()
```

---

### Step 2️⃣: Run Server

```bash
cd c:\Users\MSI\Desktop\ChildMonitorSystem\ai_service

# Nếu dùng PowerShell:
$env:GEMINI_API_KEY = "your_key"
uvicorn main:app --reload

# Nếu dùng .env:
uvicorn main:app --reload
```

Nếu thành công, sẽ thấy:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
✅ Firebase connected
✅ All models ready
```

---

### Step 3️⃣: Test API

#### Test `/analyze-smart`:
```bash
curl -X POST "http://localhost:8000/analyze-smart" \
  -H "Content-Type: multipart/form-data" \
  -F "text=This is safe content" \
  -F "device_id=test_device"
```

**Response (nếu OK):**
```json
{
  "category": "safe",
  "risk_level": 1,
  "is_safe": true,
  "reason": "Nội dung bình thường"
}
```

---

## 🔍 Troubleshooting

### ❌ `ModuleNotFoundError: No module named 'google'`
```bash
pip install google-generativeai
```

### ❌ `Lỗi: GEMINI_API_KEY chưa được set`
```bash
# Chắc chắn set biến môi trường
echo %GEMINI_API_KEY%  # Kiểm tra xem có không
```

### ❌ `Invalid API Key`
Lấy key mới từ: https://makersuite.google.com/app/apikeys

### ❌ `Connection refused to Gemini`
```bash
# Kiểm tra internet
# Kiểm tra firewall
# Thử VPN nếu cần
```

---

## 📊 So Sánh Trước/Sau

| Aspect | Trước ❌ | Sau ✅ |
|--------|---------|--------|
| **Start Server** | Crash (NameError) | OK |
| **Gemini Call** | Global (lãng phí) | On-demand (efficient) |
| **API Key** | Hardcode (unsafe) | Biến môi trường (safe) |
| **SDK Usage** | Trộn (sai) | Đúng (Client.models) |
| **Handle Error** | Crash | Graceful error return |

---

## 🎯 Chức Năng `/analyze-smart`

**Purpose:** Phân tích nội dung nâng cao bằng Gemini AI

**Input:**
- `text` - Văn bản cần phân tích (optional)
- `device_id` - ID thiết bị (optional)
- `image` - Ảnh cần phân tích (optional)

**Output:**
```json
{
  "category": "safe|nsfw|violence|self-harm|bad_words|unknown",
  "risk_level": 1-10,
  "is_safe": true/false,
  "reason": "Giải thích"
}
```

**Context:** Lưu 5 hành vi gần nhất của device → Gemini xem xét context

**Example:**
```bash
# Text analysis
curl -X POST "http://localhost:8000/analyze-smart" \
  -F "text=I want to sleep" \
  -F "device_id=child_001"

# Image analysis
curl -X POST "http://localhost:8000/analyze-smart" \
  -F "image=@screenshot.jpg" \
  -F "device_id=child_001"
```

---

## 💡 Tips

1. **Cache Gemini responses:** Để tránh lãng phí credits
2. **Rate limit:** Thêm delay giữa requests
3. **Fallback:** Nếu Gemini down → dùng local models
4. **Cost control:** ~$0.075 per 1M input tokens

---

## 📚 Tài Liệu Tham Khảo

- [Gemini API Docs](https://ai.google.dev/docs)
- [SDK Python](https://github.com/google-ai-python/google-generativeai-python)
- [API Key Setup](https://makersuite.google.com/app/apikeys)

---

**✅ Fix hoàn tất! Server sẽ hoạt động bình thường now.** 🚀
