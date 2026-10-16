# Kế hoạch phát triển Giao diện Người dùng (UI Plan) - Trợ lý Học tập

Tài liệu này trình bày kế hoạch kiến trúc, thiết kế luồng tương tác và giao thức kết nối giữa tầng Giao diện Người dùng (UI) và tầng Dịch vụ Xử lý Trung tâm (Backend) cho dự án Trợ lý Học tập (Assistant-Team06).

---

## 1. Mục tiêu và Định hướng Công nghệ

- **Giai đoạn hiện tại (Lab 2):**
  - Xây dựng bản thiết kế kiến trúc UI và trang mẫu thử nghiệm (placeholder page) với khung hội thoại và ô nhập liệu (`input box`).
  - Đặc tả chuẩn kết nối API giữa Frontend và Backend FastAPI (`http://localhost:8000`).
  - Chuẩn bị nền tảng giao diện để dễ dàng tích hợp Streamlit hoặc giao diện Web tùy biến.
- **Giai đoạn tiếp theo:**
  - Triển khai ứng dụng Streamlit hoàn chỉnh (`streamlit run ui/app.py`).
  - Hỗ trợ đầy đủ ba chế độ: Tra cứu tài liệu, Hỏi đáp RAG có trích dẫn nguồn, và Quản lý Thời khóa biểu từ ảnh.

---

## 2. Kiến trúc Kết nối UI ↔ Backend

Ứng dụng UI hoạt động như một Single Page Application (SPA) hoặc Streamlit App giao tiếp với FastAPI Backend thông qua giao thức HTTP REST API và Server-Sent Events (SSE) để truyền dữ liệu thời gian thực (streaming).

```
+-------------------------------------------------------------+
|                      Giao diện UI                           |
|       (Streamlit / Web HTML5 + Vanilla JS / CSS)            |
+-------------------------------------------------------------+
                              |
               HTTP REST API  |  Server-Sent Events (SSE)
                              v
+-------------------------------------------------------------+
|                     FastAPI Backend                         |
|                 (http://localhost:8000)                     |
|                                                             |
|  - /api/chat & /api/chat/stream    : Xử lý hỏi đáp RAG      |
|  - /api/sessions                   : Quản lý phiên hội thoại|
|  - /api/documents                  : Tra cứu tài liệu       |
|  - /api/timetable                  : Thời khóa biểu OCR     |
+-------------------------------------------------------------+
```

---

## 3. Đặc tả Giao thức Gọi Backend từ UI

### 3.1. Gửi câu hỏi và nhận câu trả lời dạng Streaming (Khuyến nghị)
Khi người dùng nhập câu hỏi vào ô input box và nhấn gửi, UI khởi tạo kết nối SSE đến endpoint stream:

- **Endpoint:** `POST /api/chat/stream`
- **Headers:** `Content-Type: application/json`
- **Request Body (JSON):**
  ```json
  {
    "query": "Giao thức TCP hoạt động như thế nào?",
    "session_id": "sess_20261016_abc123",
    "answer_mode": "standard",
    "save_history": true,
    "isolate_context": false
  }
  ```
- **Xử lý sự kiện từ Server (SSE Events):**
  - `event: chunk`: Nhận từng phần văn bản của câu trả lời để hiển thị hiệu ứng gõ chữ (typing effect).
  - `event: citations`: Nhận danh sách các nguồn trích dẫn (`citations`) kèm số trang, điểm liên quan và đường dẫn PDF.
  - `event: done`: Báo hiệu lượt trả lời đã hoàn tất, trả về ID của phiên (`session_id`) và lượt hội thoại (`turn_id`).
  - `event: error`: Thông báo lỗi kỹ thuật nếu server gặp sự cố.

### 3.2. Gửi câu hỏi và nhận kết quả đồng bộ (Fallback)
Dành cho các trường hợp client không hỗ trợ streaming hoặc thực thi kiểm thử:

- **Endpoint:** `POST /api/chat`
- **Headers:** `Content-Type: application/json`
- **Response (JSON):**
  ```json
  {
    "answer": "Giao thức TCP là giao thức hướng kết nối...",
    "citations": [
      {
        "id": "S1",
        "book_title": "Giáo trình Mạng máy tính",
        "filename": "mang_may_tinh.pdf",
        "page_num": 112,
        "snippet": "TCP cung cấp dịch vụ truyền dữ liệu tin cậy...",
        "score": 0.892,
        "pdf_url": "/api/documents/1/pdf#page=112"
      }
    ],
    "session_id": "sess_20261016_abc123",
    "turn_id": 1,
    "model": "qwen2.5-7b-instruct"
  }
  ```

### 3.3. Quản lý Phiên hội thoại (Session Lifecycle)
- **Lấy danh sách các phiên cũ:** `GET /api/sessions?limit=50&offset=0`
- **Tạo phiên hội thoại mới:** `POST /api/sessions` (Body: `{ "title": "Phiên ôn tập Mạng máy tính" }`)
- **Tải chi tiết lịch sử một phiên:** `GET /api/sessions/{session_id}`
- **Xóa phiên hội thoại:** `DELETE /api/sessions/{session_id}`

### 3.4. Xử lý Trạng thái và Ngoại lệ (Error & Concurrency Handling)
- **HTTP 409 Conflict (`DialogueBusyError`):** Xảy ra khi một lượt xử lý khác đang chiếm dụng tài nguyên hội thoại. UI hiển thị thông báo trạng thái "Trợ lý đang bận xử lý, vui lòng chờ trong giây lát..." và giữ nguyên nội dung trong ô input.
- **HTTP 503 Service Unavailable:** Xảy ra khi tài nguyên GPU/VLM đang bận xử lý tác vụ nặng. UI cung cấp nút bấm "Thử lại".
- **Bảo toàn dữ liệu nhập:** Nếu xảy ra lỗi mạng hoặc backend từ chối, văn bản người dùng đang gõ trong input box không bị xóa mất.

---

## 4. Hướng dẫn Khởi chạy Trang Placeholder

### Yêu cầu môi trường
- Python 3.10+
- Thư viện: `streamlit`, `requests`

### Các bước khởi chạy
1. Khởi động Backend (tại thư mục gốc của repository):
   ```powershell
   python run.py
   # Backend sẽ lắng nghe tại http://localhost:8000
   ```
2. Khởi động UI Placeholder:
   ```powershell
   streamlit run ui/app.py --server.port 8501
   ```
3. Mở trình duyệt tại địa chỉ: `http://localhost:8501`.
