# 🎯 Quick Reference - Lý Do Quét Phổ Biến

## Mã Lý Do → Ý Nghĩa → Cách Sửa

### 🔴 LOCK (Khóa Thiết Bị)

| Mã | Ý Nghĩa | Ví Dụ | Sửa Bằng |
|----|---------|-------|---------|
| `rule:sex` | Từ cấm trực tiếp | Nội dung chứa "sex" | Xóa từ khỏi `BAD_WORDS` `[main.py:113]` |
| `rule:porn` | Từ cấm trực tiếp | Nội dung chứa "porn" | Xóa từ khỏi `BAD_WORDS` `[main.py:113]` |
| `rule:suicide` | Từ cấm trực tiếp | Nội dung chứa "suicide" | Xóa từ khỏi `BAD_WORDS` `[main.py:113]` |
| `nsfw:0.85` | Ảnh nội dung 18+ | Ảnh người lớn | Tăng ngưỡng từ 0.7 → 0.9 `[main.py:478]` |
| `violence:0.75` | Ảnh bạo lực | Ảnh chiến đấu | Tăng ngưỡng hoặc loại ảnh `[main.py:482]` |
| `ai:sexual content:0.82` | AI phát hiện tình dục | Text mô tả tình dục | Tăng ngưỡng AI từ 0.75 → 0.80 `[main.py:330]` |

---

### 🟡 BLOCK (Chặn App)

| Mã | Ý Nghĩa | Ví Dụ | Sửa Bằng |
|----|---------|-------|---------|
| `fuzzy:sex:87` | Từ cấm viết sai | "sexx", "s3x" | Tăng fuzzy threshold từ 85 → 90 `[main.py:186]` |
| `fuzzy:porn:88` | Từ cấm viết sai | "p0rn", "p*rn" | Tăng fuzzy threshold từ 85 → 90 `[main.py:186]` |
| `ai:violence:0.82` | AI phát hiện bạo lực | "đánh nhau" | Tăng ngưỡng AI từ 0.75 → 0.80 `[main.py:330]` |
| `ai:bullying:0.79` | AI phát hiện lạm dụng | "tao đánh mày" | Tăng ngưỡng AI từ 0.75 → 0.80 `[main.py:330]` |

---

### 🟡 WARNING (Cảnh Báo)

| Mã | Ý Nghĩa | Ví Dụ | Sửa Bằng |
|----|---------|-------|---------|
| `ai:sexual content:0.68` | AI 60-75% tình dục | Text mơ hồ | Tăng ngưỡng từ 0.60 → 0.70 `[main.py:328]` |
| `ai:violence:0.65` | AI 60-75% bạo lực | Text mơ hồ | Tăng ngưỡng từ 0.60 → 0.70 `[main.py:328]` |

---

### ✅ SAFE (An Toàn)

| Mã | Ý Nghĩa | Lý Do |
|----|---------|------|
| `system_text` | Text hệ thống | Từ "settings", "chrome" → bỏ qua |
| `invalid_text` | Text không hợp lệ | Quá nhiều ký tự đặc biệt/số → bỏ qua |
| `too_short` | Text quá ngắn | Dưới 2 từ → bỏ qua |
| `ai:safe:0.95` | AI phát hiện an toàn | Độ tin cậy cao → OK |

---

## 🚨 Tình Huống Phổ Biến

### ❌ False Positive: "hello" → BLOCK ❌

```bash
Logs: fuzzy:hell:88

Giải pháp: Tăng fuzzy từ 85 → 90
Vị trí: main.py dòng 186
   if score > 90:  # thay từ 85
```

---

### ❌ False Positive: "hello world" → BLOCK ❌

```bash
Logs: ai:violence:0.82

Giải pháp: Tăng ngưỡng AI từ 0.75 → 0.80
Vị trí: main.py dòng 330
   if score > 0.80:  # thay từ 0.75
```

---

### ❌ False Negative: Nội dung xấu → SAFE ✅ ❌

```bash
Logs: ai:safe:0.60

Giải pháp 1: Giảm ngưỡng AI từ 0.75 → 0.70
Vị trí: main.py dòng 330

Giải pháp 2: Thêm từ vào BAD_WORDS
Vị trí: main.py dòng 113
   "new_bad_word"
```

---

### ⚔️ Ảnh bạo lực → SAFE ✅ ❌

```bash
Logs: violence:0.45

Giải pháp: Giảm ngưỡng bạo lực từ 0.6 → 0.5
Vị trí: main.py dòng 482
   and violence_conf > 0.5:  # thay từ 0.6
```

---

## 📍 Nhanh Tìm Vị TRÍ CẤU HÌNH

```python
# main.py

# Line 113: BAD_WORDS - Danh sách từ cấm
BAD_WORDS = [...  

# Line 125: CANDIDATE_LABELS - Nhãn phân loại AI
CANDIDATE_LABELS = [...]

# Line 186: Fuzzy threshold
if score > 85:

# Line 330: AI threshold (BLOCK)
if score > 0.75:

# Line 328: AI threshold (WARNING)  
elif score > 0.60:

# Line 478: NSFW threshold
if nsfw_conf > 0.7:

# Line 482: Violence threshold
and violence_conf > 0.6:
```

---

## 🔧 Công Cụ Debug Nhanh

### Cách 1: Xem logs với emoji
```bash
grep "🔴\|🟡\|✅" logs.txt
```

### Cách 2: Kiểm tra lý do cụ thể
```bash
grep "rule:sex\|ai:violence" logs.txt
```

### Cách 3: Đếm vi phạm
```bash
grep "LOCK\|BLOCK\|WARNING" logs.txt | wc -l
```

---

## 📱 Kiểm Tra Firebase

**Đường dẫn:** `Collections > devices > [device_id]`

**Các field quan trọng:**
- `mode` - SAFE/WARNING/BLOCK/LOCK
- `reason` - Mã lý do (rule:sex)
- `reason_text` - Mô tả (🔴 LỌC TỪ CẤM...)
- `check_type` - Loại (rule/ai/fuzzy)
- `last_violation` - Nội dung
- `timestamp` - Khi nào

---

## 🎯 Quy Trình Fix Bug

1. **Xem logs** → tìm 🔴 hoặc ❌
2. **Copy mã** → ví dụ `fuzzy:sex:87`
3. **Tìm vị trí** → dùng grep/search
4. **Sửa giá trị** → tăng/giảm threshold
5. **Test lại** → xem logs mới
6. **Confirm Firebase** → kiểm tra device collection

---

## 💡 Priority Sửa

| Priority | Vấn Đề | Thêm Thời Gian | Cách Sửa |
|----------|--------|---------------|---------| 
| 🔴 Cao | LOCK sai | 5 phút | Tăng ngưỡng `[main.py:330]` |
| 🟡 Trung | BLOCK sai | 10 phút | Tăng fuzzy `[main.py:186]` |
| 🟢 Thấp | SAFE sai | 15 phút | Thêm từ `[main.py:113]` |

---

**💾 Lưu file này để reference nhanh! 📌**
