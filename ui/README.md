# Kế hoạch phát triển Giao diện Người dùng (UI Plan) - Trợ lý Học tập

Tài liệu này trình bày kiến trúc giao diện, chuẩn kết nối client đồng bộ/bất đồng bộ và giao thức giao tiếp giữa tầng Giao diện Người dùng (UI) và Backend FastAPI (`http://localhost:8000`) cho dự án Trợ lý Học tập (Assistant-Team06).

---

## 1. Mục tiêu và Định hướng Công nghệ

- **Giao diện Web chính thức của ứng dụng:**
  - Nằm tại `static/index.html` (và các tài sản tĩnh đi kèm trong `static/`), được phục vụ trực tiếp qua FastAPI tại `http://localhost:8000`.
  - Hỗ trợ đầy đủ các tính năng: Hỏi đáp RAG có trích dẫn nguồn, Tra cứu tài liệu, Luyện tập trắc nghiệm và Quản lý Thời khóa biểu từ ảnh.
  - Sử dụng giao thức Server-Sent Events (SSE) qua endpoint `POST /api/chat/stream` để hiển thị hiệu ứng trả lời thời gian thực.
- **Giao diện Mẫu / Adapter (Streamlit `ui/app.py`):**
  - Đóng vai trò là adapter mẫu và công cụ kiểm thử tích hợp client sync cho sinh viên / kiểm thử viên.
  - Sử dụng endpoint đồng bộ `POST /api/chat` với đầy đủ schema payload và cơ chế xử lý lỗi chặt chẽ (fail-closed, không sinh phản hồi/nguồn giả).

---

## 2. Kiến trúc Kết nối UI ↔ Backend

```
+-------------------------------------------------------------+
|                      Giao diện UI                           |
|  - UI chính   : Web Client (static/index.html)              |
|  - UI Adapter : Streamlit Client (ui/app.py)                |
+-------------------------------------------------------------+
                               |
               HTTP REST /     |  Server-Sent Events (SSE)
               Client Sync     |  (/api/chat/stream)
                               v
+-------------------------------------------------------------+
|                     FastAPI Backend                         |
|                 (http://localhost:8000)                     |
|                                                             |
|  - /api/chat & /api/chat/stream    : Hỏi đáp học tập (RAG)  |
|  - /api/sessions                   : Quản lý phiên hội thoại|
|  - /api/documents                  : Tra cứu tài liệu & PDF |
|  - /api/practice                   : Luyện tập & ôn tập     |
|  - /api/timetable                  : Thời khóa biểu OCR     |
+-------------------------------------------------------------+
```

---

## 3. Đặc tả Giao thức Gọi Backend từ UI

### 3.1. Giao thức Streaming cho Giao diện Chính (`static/index.html`)
Khi người dùng nhập câu hỏi vào ô input box, Web UI chính gửi yêu cầu SSE đến endpoint:
- **Endpoint:** `POST /api/chat/stream`
- **Khung truyền:** mỗi sự kiện là một dòng `data: {"type":"token","content":"..."}` và một dòng trống (`\n\n`); loại nằm trong JSON, không phải `event: chunk`.
- **Các loại sự kiện chính (không phải danh sách đóng):** `token` (văn bản câu trả lời), `citations` (danh sách trích dẫn nguồn), `retrieval_trace` (vết truy xuất), `done` (kết thúc lượt hội thoại), `error` (thông báo sự cố).

### 3.2. Giao thức Đồng bộ Client Sync (`ui/app.py`)
Phục vụ adapter Streamlit và các client kiểm thử đồng bộ:
- **Endpoint:** `POST /api/chat`
- **Headers:** `Content-Type: application/json`
- **Ví dụ request minh họa (không phải dữ liệu corpus):**
  ```json
  {
    "query": "Mô hình OSI gồm những tầng nào?",
    "session_id": null,
    "chat_history": [
      {"role": "user", "content": "Chào bạn"},
      {"role": "assistant", "content": "Xin chào! Bạn muốn tìm hiểu nội dung gì?"}
    ],
    "answer_mode": "quick",
    "processing_level": "auto",
    "request_id": "550e8400-e29b-41d4-a716-446655440000",
    "save_history": true
  }
  ```
  **Chi tiết các trường trong payload:**
  - `query` *(bắt buộc)*: Chuỗi câu hỏi học tập (1–4000 ký tự, tuân thủ chính sách lọc ký tự điều khiển Unicode).
  - `session_id` *(tùy chọn)*: ID phiên hội thoại (UUID/chuỗi an toàn). Nếu `null`, backend sẽ tạo phiên mới khi hoàn tất.
  - `chat_history` *(tùy chọn)*: Danh sách tối đa 32 tin nhắn trước đó, chỉ gồm `role` (`user` hoặc `assistant`) và `content`.
  - `answer_mode` *(mặc định: `"quick"`)*: Chế độ trả lời hợp lệ gồm `"quick"` (ngắn gọn, cốt lõi), `"steps"` (từng bước) và `"compare"` (so sánh khái niệm). Lưu ý: Không gửi `"standard"` vì không thuộc schema backend.
  - `processing_level` *(mặc định: `"auto"`)*: Mức độ xử lý gồm `"auto"` (tự động điều phối), `"standard"` (thông thường) và `"careful"` (kiểm tra kỹ và đối chiếu nhận định với nguồn).
  - `request_id` *(tùy chọn nhưng khuyến nghị)*: UUID định danh duy nhất cho từng lượt hỏi, dùng để chống lặp và phục vụ cơ chế thử lại (idempotent retry).
  - `save_history` *(mặc định: `true`)*: Ghi nhận lượt hỏi đáp vào cơ sở dữ liệu `history.db`.

- **Ví dụ response minh họa, không chứng nhận tài liệu/trang đã được kiểm định:**
  ```json
  {
    "answer": "Mô hình OSI gồm 7 tầng: Vật lý, Liên kết dữ liệu, Mạng, Giao vận, Phiên, Trình diễn và Ứng dụng [S1].",
    "citations": [
      {
        "id": "S1",
        "book_title": "Giáo trình Mạng máy tính",
        "filename": "mang_may_tinh.pdf",
        "page_num": 42,
        "chunk_index": 2,
        "snippet": "Mô hình OSI gồm 7 tầng: Vật lý, Liên kết dữ liệu, Mạng, Giao vận, Phiên, Trình diễn, Ứng dụng.",
        "snippet_vi": "",
        "score": 0.892,
        "pdf_url": "/api/documents/1/pdf#page=42",
        "page_quality": {
          "needs_review": false
        }
      }
    ],
    "session_id": "sess_20261016_abc123",
    "turn_id": 1,
    "turn_index": 1,
    "model": "qwen2.5:3b"
  }
  ```

### 3.3. Quy chuẩn Hiển thị Trích dẫn (Citations) & Liên kết PDF
- **Điểm truy xuất (Score):** Trường `score` thể hiện điểm số tương đồng của bộ tìm kiếm kết hợp (Hybrid Searcher: BM25/FTS5 + Vector Embedding). Đây là điểm xếp hạng tài liệu nội bộ, **tuyệt đối không được gọi là tỷ lệ phần trăm (%) hay độ tin cậy/xác suất (confidence)** của câu trả lời.
- **Liên kết PDF an toàn:** Chỉ chấp nhận URL nội bộ có định dạng tương đối `/api/documents/<id>/pdf#page=<page_num>` với ID và trang là số nguyên dương; adapter ghép đường dẫn này với `BACKEND_URL`, không dùng origin cổng Streamlit. Chặn toàn bộ URL bên ngoài (`http://`, `https://`), protocol-relative (`//`), lệnh thực thi (`javascript:`) hoặc thủ thuật duyệt thư mục (`../`).
- **Chất lượng trang (`page_quality`):** Nếu `page_quality.needs_review` là `true` hoặc có cảnh báo chất lượng bản quét/OCR, UI hiển thị thông báo màu vàng để người học đối chiếu lại với trang PDF gốc. Không có cảnh báo không đồng nghĩa trang đã được duyệt. Tên và đoạn nguồn hiển thị nguyên văn dạng plain text để không tạo liên kết từ nội dung tài liệu.

### 3.4. Xử lý Lỗi, Trạng thái Pending & Cơ chế Thử lại (Idempotent Retry)
- **Không sinh phản hồi hoặc nguồn giả (No Fake Fallback):** Khi mất kết nối hoặc backend trả mã lỗi, UI tuyệt đối không tự bịa ra câu trả lời hay trích dẫn giả lập. Mọi lượt lỗi phải trả về trạng thái thất bại rõ ràng với `citations: []`.
- **Bảo mật thông tin lỗi:** Không hiển thị raw response body hoặc chi tiết exception nội bộ từ backend/database ra màn hình người dùng.
- **Xử lý các mã lỗi HTTP thường gặp:**
  - `HTTP 409 Conflict` (`DialogueBusyError`): Hệ thống đang bận xử lý yêu cầu khác. UI giữ câu hỏi trong lượt chờ, khóa ô nhập và cho phép thử lại hoặc bỏ lượt chờ.
  - `HTTP 422 Unprocessable Entity`: Dữ liệu đầu vào vi phạm chuẩn bảo mật (độ dài, ký tự lạ hoặc format).
  - `HTTP 503 Service Unavailable` (`GPUBusyError`): Tài nguyên GPU/mô hình đang bận.
  - Phản hồi có cờ `is_truncated: true`, `error` hoặc `response_kind: "technical_error" / "generation_failed"`: Được xử lý như lượt thất bại, không lưu thành công.
- **Quản lý trạng thái Pending:** Khi một yêu cầu đang được gửi đi hoặc đang ở trạng thái lỗi chưa giải quyết, ô nhập liệu sẽ bị khóa (`disabled`), không cho phép người dùng gửi lượt hỏi mới đè lên. Người dùng có thể:
  1. Bấm **🔄 Thử lại** để gửi lại đúng `request_id`, `query` và `chat_history` ban đầu. Do giữ nguyên `request_id`, backend chống ghi trùng lượt hội thoại đã hoàn tất. Giữ `save_history: true` để replay bền vững; đây không phải cam kết mô hình chỉ chạy một lần khi mọi sự cố xảy ra.
  2. Bấm **✖ Hủy yêu cầu** để bỏ lượt chờ và câu hỏi chưa có câu trả lời khỏi ngữ cảnh giao diện. Thao tác này không dừng xử lý hoặc xóa lịch sử đã ghi ở backend. Sau timeout, server có thể vẫn hoàn tất; thử lại cùng ID giúp lấy lại kết quả.
  3. Bấm **➕ Cuộc trò chuyện mới** để xóa toàn bộ hội thoại và reset hoàn toàn trạng thái pending.

---

## 4. Hướng dẫn Khởi chạy và Kiểm thử

### Yêu cầu môi trường
- Python 3.12 (cùng phiên bản CI)
- Backend web: làm theo README của hệ điều hành. Adapter cần thêm `python -m pip install -r ui/requirements.txt`; kiểm thử cần `requirements-dev.txt`.

### Các bước khởi chạy
1. **Khởi động Backend:**
   ```bash
   python scripts/dev.py demo
   python scripts/dev.py serve --profile demo
   ```
2. **Truy cập Giao diện Chính (Web UI):**
   - Mở trình duyệt tại: `http://localhost:8000` hoặc `http://localhost:8000/static/index.html`.
3. **Khởi chạy Giao diện Mẫu / Adapter (Streamlit):**
   ```bash
   streamlit run ui/app.py --server.port 8501
   ```
4. **Kiểm thử tích hợp UI:**
   ```bash
   python -m pytest tests/test_help.py tests/test_streamlit_integration.py tests/test_streamlit_flow.py
   ```

`BACKEND_URL` mặc định là `http://localhost:8000`; đặt biến môi trường này nếu backend dùng địa chỉ khác. Adapter giới hạn `chat_history` ở 32 tin gần nhất, cắt mỗi nội dung còn tối đa 4000 ký tự và chọn một đoạn đuôi không quá 32000 bytes UTF-8. Lịch sử hiển thị vẫn giữ nguyên. Khi có session ID, backend dùng lịch sử đã lưu làm nguồn chính; payload client không ghi đè lịch sử đó.
