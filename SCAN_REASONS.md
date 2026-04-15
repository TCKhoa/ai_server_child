# 📋 Hướng Dẫn Lý Do Quét (Scan Reasons)

## 🎯 Tổng Quan
Hệ thống phân tích nội dung sử dụng nhiều bộ lọc khác nhau để phát hiện nội dung không phù hợp cho trẻ em. Tài liệu này giúp bạn hiểu rõ từng lý do quét.

---

## 📌 Các Loại Lý Do Quét

### 1️⃣ **RULE - Quy Tắc Từ Cấm** (LOCK)
- **Mã:** `rule:<từ>`
- **Ví dụ:** `rule:sex`, `rule:suicide`
- **Ý nghĩa:** Nội dung chứa từ bị cấm trực tiếp
- **Cách sửa:** Thêm/xóa từ khỏi danh sách `BAD_WORDS` trong [main.py](main.py#L113)

```python
BAD_WORDS = [
    "sex", "porn", "dit me", "dm", "clm",
    "kill", "suicide", "rape", ...
]
```

---

### 2️⃣ **FUZZY - Từ Nhạy Cảm Biến Thể** (BLOCK)
- **Mã:** `fuzzy:<từ>:<độ_khớp>`
- **Ví dụ:** `fuzzy:sex:87`
- **Ý nghĩa:** Từ bị cấm nhưng viết sai chữ (xóa l, 1en5, @, $)
- **Độ khớp:** 0-100%, tăng từng 1% khi từ khớp hơn
- **Cách sửa:** 
  - Tăng ngưỡng từ 85 lên 90+ nếu có false positive
  - Thêm từ vào `BAD_WORDS` nếu cần

---

### 3️⃣ **AI - Phân Loại AI** (WARNING/BLOCK/SAFE)
- **Mã:** `ai:<phân_loại>:<độ_tin_cậy>`
- **Ví dụ:** `ai:sexual content:0.82`
- **Phân loại có sẵn:**
  - `sexual content` - Nội dung tình dục
  - `violence` - Bạo lực
  - `self-harm` - Tự hại
  - `suicide` - Tuyệt vọng/Tự tử
  - `bullying` - Lạm dụng/Qu騷rôn cơm
  - `safe` - An toàn

- **Ngưỡng quyết định:**
  - **> 0.75:** BLOCK (khóa)
  - **0.60-0.75:** WARNING (cảnh báo)
  - **< 0.60:** SAFE (bỏ qua)

- **Cách sửa:** Điều chỉnh ngưỡng trong [decision_pipeline()](main.py#L200)

---

### 4️⃣ **NSFW - Nội Dung 18+ (Ảnh)** (LOCK)
- **Mã:** `nsfw:<độ_tin_cậy>`
- **Ví dụ:** `nsfw:0.89`
- **Ý nghĩa:** Ảnh được phát hiện là nội dung tình dục
- **Ngưỡng:** > 0.7 (70%)
- **Cách sửa:** Điều chỉnh model hoặc ngưỡng trong `analyze_screen()`

---

### 5️⃣ **VIOLENCE - Bạo Lực (Ảnh)** (LOCK)
- **Mã:** `violence:<độ_tin_cậy>`
- **Ví dụ:** `violence:0.75`
- **Biến thể:**
  - `gun:<độ_tin_cậy>` - Phát hiện súng
  - `weapon:<độ_tin_cậy>` - Phát hiện vũ khí
  - `blood:<độ_tin_cậy>` - Phát hiện máu
  - `fight:<độ_tin_cậy>` - Phát hiện chiến đấu
- **Cách sửa:** Điều chỉnh nhãn trong `VIOLENCE_LABELS` hoặc ngưỡng

---

## 🚨 Các Loại Trạng Thái

| Trạng Thái | Emoji | Ý Nghĩa | Hành Động |
|-----------|-------|---------|----------|
| **SAFE** | ✅ | Nội dung an toàn | Không làm gì |
| **WARNING** | 🟡 | Cảnh báo (AI tin cậy 60-75%) | Hiển thị cảnh báo |
| **BLOCK** | 🔴 | Chặn (AI tin cậy > 75% hoặc fuzzy match) | Quay lại/ Trở về trang chủ |
| **LOCK** | 🔴 | Khóa (từ cấm trực tiếp hoặc NSFW/Violence) | Khóa thiết bị |

---

## 🔍 Cách Gỡ Lỗi (Debug)

### 1. Kiểm tra logs trong terminal
```bash
# Nhìn các dòng chứa emoji để theo dõi flow:
✅ - Bước kiểm tra thành công
🟡 - Cảnh báo
🔴 - Vi phạm
❌ - Lỗi
```

### 2. Các bước kiểm tra (Flow):
```
1. ✅ System Text? (Bỏ qua text hệ thống)
2. ✅ Rule Check? (Kiểm tra từ cấm trực tiếp)
3. ✅ Fuzzy Check? (Kiểm tra từ viết sai)
4. ✅ Text Valid? (Kiểm tra valid text)
5. ✅ Text Length? (Kiểm tra độ dài)
6. 🤖 AI Classification? (Phân loại AI)
```

### 3. Ví dụ log thực tế:
```
============================================================
📝 PHÂN TÍCH NỘI DUNG
Văn bản gốc: hello world
Văn bản chuẩn hóa: hello world

🔍 BẮT ĐẦU KIỂM TRA:
  ✅ [System Text] Không có text hệ thống
  ✅ [Quy tắc từ cấm] Không phát hiện từ cấm trực tiếp
  ✅ [Fuzzy Matching] Không phát hiện từ nhạy cảm biến thể
  ✅ [Xác thực nội dung] Nội dung hợp lệ
  ✅ [Độ dài nội dung] Nội dung đủ dài (2 từ)
  🤖 Phân tích AI...
     Phân loại: safe (độ tin cậy: 95.50%)
     ✅ Phân loại an toàn: safe (95.50%)
✅ Kết quả: SAFE

============================================================
```

---

## 🛠️ Các Tệp Cấu Hình Chính

| Tệp | Dòng | Mục Đích |
|-----|------|---------|
| [main.py](main.py) | 113-122 | Danh sách `BAD_WORDS` |
| [main.py](main.py) | 125-128 | `CANDIDATE_LABELS` (phân loại AI) |
| [main.py](main.py) | 130-133 | `DANGEROUS_LABELS` (nhãn nguy hiểm) |
| [main.py](main.py) | 135-139 | `VIOLENCE_LABELS` (nhãn bạo lực) |
| [main.py](main.py) | 200-250 | Hàm `decision_pipeline()` (logic chính) |

---

## 🔧 Các Bước Điều Chỉnh Phổ Biến

### ❌ False Positive (Tích cực giả - quét nhầm)
**Vấn đề:** Nội dung an toàn bị quét
**Giải pháp:**
1. Tăng ngưỡng AI từ 0.75 lên 0.80
2. Tăng fuzzy threshold từ 85 lên 90
3. Xóa từ khỏi `BAD_WORDS` nếu không nguy hiểm

### ✅ False Negative (Âm tính giả - bỏ sót)
**Vấn đề:** Nội dung xấu không bị quét
**Giải pháp:**
1. Thêm từ mới vào `BAD_WORDS`
2. Giảm ngưỡng AI từ 0.75 xuống 0.70
3. Kiểm tra chuẩn hóa text có hợp lệ không

### 🔄 Không Nhất Quán (Inconsistent)
**Vấn đề:** Cùng nội dung được quét khác nhau
**Giải pháp:**
1. Kiểm tra cache ([main.py](main.py#L150))
2. Xóa cache nếu cần: `cache.clear()`

---

## 📊 Thống Kê Kiểm Tra

### Trong Firebase
Mỗi violation lưu:
- `last_violation` - Nội dung vi phạm
- `mode` - Trạng thái (SAFE/WARNING/BLOCK/LOCK)
- `reason` - Mã lý do (rule:sex, ai:violence:0.80)
- `reason_text` - Mô tả chi tiết bằng tiếng Việt
- `timestamp` - Thời gian phát hiện
- `check_type` - Loại kiểm tra (rule, fuzzy, ai, nsfw, violence)

---

## 💡 Mẹo Tối Ưu Hóa

1. **Giảm False Positive:** Tăng ngưỡng từ 0.75 → 0.80
2. **Tăng Sensitivity:** Giảm ngưỡng từ 0.75 → 0.70
3. **Tăng Tốc Độ:** Bật cache (mặc định bật)
4. **Giảm RAM:** Hạn chế số mô hình AI được load

---

## 📞 Liên Hệ & Hỗ Trợ

Nếu có vấn đề, kiểm tra:
1. Terminal logs (xem các emoji 🔴✅)
2. Firebase console → devices → [device_id]
3. Ghi chú chi tiết trong file này
