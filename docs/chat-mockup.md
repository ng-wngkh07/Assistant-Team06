# Bản vẽ Thiết kế Giao diện Hội thoại (Chat Page Mockup)

Tài liệu này mô tả chi tiết bản vẽ cấu trúc wireframe và mockup trực quan của trang hội thoại học tập (`Chat Page`) kèm ô nhập liệu (`Input Box`) theo tiêu chí nghiệm thu của **Issue #18 (Lab 2)**.

> Đây là bản mẫu thiết kế, không phải ảnh chức năng đã nghiệm thu hoặc câu trả lời từ corpus thật. UI chính nằm tại `static/index.html`; adapter Streamlit ở `ui/app.py`. Cách bố trí/phím tắt dưới đây là mục tiêu thiết kế, kiểm riêng trên từng client.

---

## 1. Cấu trúc Tổng thể Giao diện (Layout Overview)

Giao diện trang hội thoại được chia làm 3 phân vùng chính:
1. **Thanh bên trái (Sidebar):** Quản lý các phiên hội thoại, tạo cuộc trò chuyện mới, lựa chọn chế độ trả lời và thông tin trạng thái backend.
2. **Khu vực hội thoại trung tâm (Chat Message Feed):** Hiển thị dòng thời gian các tin nhắn giữa người dùng và trợ lý, bao gồm câu hỏi, câu trả lời và khối trích dẫn nguồn có thể mở rộng/thu gọn.
3. **Thanh nhập liệu cố định ở đáy (Bottom Input Box):** Ô nhập liệu tự co giãn hỗ trợ gõ nhiều dòng (Shift+Enter), xử lý gõ bộ gõ tiếng Việt (IME) an toàn, cùng nút gửi và thông báo trạng thái xử lý.

---

## 2. Bản vẽ Wireframe Trực quan (ASCII UI Mockup)

```
+-------------------------------------------------------------------------------------------------------+
|  🎓 TRỢ LÝ HỌC TẬP - TEAM 06                                      [⚙️ Cài đặt]  [🌓 Đổi giao diện]  |
+----------------------+--------------------------------------------------------------------------------+
| [➕ Cuộc trò chuyện]  |                                                                                |
|                      |  [🧑 Người dùng]                                                                |
| ── Các phiên gần đây ─|  Mô hình OSI gồm những tầng nào và vai trò của tầng Transport là gì?          |
| • Ôn tập Mạng máy t. |                                                                                |
| • Bài tập Tuần 2     |  ----------------------------------------------------------------------------  |
| • Quy chế tín chỉ    |                                                                                |
|                      |  [🤖 Trợ lý Học tập]                                                            |
| ── Chế độ trả lời ─── |  Mô hình OSI (Open Systems Interconnection) gồm 7 tầng chính:                  |
| (•) Ngắn gọn       |  1. Tầng Vật lý (Physical)                                                     |
| ( ) So sánh          |  2. Tầng Liên kết dữ liệu (Data Link)                                          |
| ( ) Tóm tắt cốt lõi  |  3. Tầng Mạng (Network)                                                        |
|                      |  4. Tầng Giao vận (Transport): Đảm bảo việc truyền dữ liệu tin cậy giữa hai... |
| ── Trạng thái ────── |  5. Tầng Phiên (Session)                                                        |
| 🟢 Backend: Sẵn sàng |  6. Tầng Trình diễn (Presentation)                                             |
|                      |  7. Tầng Ứng dụng (Application)                                                |
|                      |                                                                                |
|                      |  ▼ 📚 Đối chiếu với tài liệu gốc:                                              |
|                      |  +--------------------------------------------------------------------------+  |
|                      |  | [S1] Giáo trình Mạng máy tính · Trang 42 · Điểm truy xuất: 0.95                   |  |
|                      |  | "Mô hình tham chiếu OSI chuẩn hóa các chức năng mạng thành 7 tầng phân biệt...|  |
|                      |  | [📖 Mở trang 42 (PDF)]                                                     |  |
|                      |  +--------------------------------------------------------------------------+  |
|                      |                                                                                |
+----------------------+--------------------------------------------------------------------------------+
|                      |  +----------------------------------------------------------------+ +-------+  |
| [Phiên: Mạng máy t.] |  | Nhập câu hỏi học tập của bạn tại đây... (Shift+Enter xuống dòng) | |  Gửi  |  |
|                      |  +----------------------------------------------------------------+ +-------+  |
|                      |   Trạng thái: Sẵn sàng nhận câu hỏi. Nhấn Enter để gửi.                         |
+----------------------+--------------------------------------------------------------------------------+
```

---

## 3. Đặc tả Thành phần Giao diện (Component Breakdown)

### 3.1. Ô Nhập liệu (Chat Input Box)
- **Vị trí:** Cố định ở cạnh dưới của màn hình để người dùng luôn có thể đặt câu hỏi mà không cần cuộn trang.
- **Tính năng nhập liệu:**
  - `Placeholder text`: *"Nhập câu hỏi học tập của bạn tại đây..."*
  - Tự động điều chỉnh độ cao theo độ dài văn bản (từ 1 đến 5 dòng).
  - Phím bấm:
    - `Enter`: Gửi câu hỏi.
    - `Shift + Enter`: Xuống dòng mới mà không gửi.
    - Bảo vệ bộ gõ tiếng Việt (IME Composition): Khi người dùng đang bỏ dấu tiếng Việt (Telex/VNI) qua phím Enter, giao diện chặn việc kích hoạt gửi nhầm trước khi kết thúc gõ từ.
- **Trạng thái gửi (Submission State):**
  - Khi đang gửi: Vô hiệu hóa nút gửi, hiển thị biểu tượng quay vòng (spinner) và trạng thái *"Đang tra cứu tài liệu và suy luận câu trả lời..."*.
  - Sau khi gửi: Tự động xóa nội dung ô nhập và đặt con trỏ chuột trở lại ô nhập liệu để người dùng có thể hỏi tiếp.

### 3.2. Bong bóng Tin nhắn (Message Bubbles)
- **Tin nhắn người dùng (User Message):** Căn lề phải, nền sáng nhẹ/nhám mờ, hiển thị câu hỏi người dùng vừa nhập.
- **Tin nhắn trợ lý (Assistant Message):** Căn lề trái, hỗ trợ hiển thị Markdown đầy đủ (in đậm, danh sách có thứ tự, bảng, khối code monospace).

### 3.3. Khối Nguồn Trích dẫn (Citation Sources Block)
- Nằm ngay dưới phần nội dung của trợ lý.
- Mặc định mở rộng để sinh viên kiểm chứng ngay thông tin nguồn.
- Hiển thị tên sách/tài liệu, số trang, điểm liên quan (score) và trích đoạn trực tiếp.

---

## 4. Khả năng Tương thích và Trải nghiệm Người dùng (UX)

- **Responsive Design:** Tự động ẩn Sidebar trên thiết bị di động (Mobile) thành menu trượt dạng hamburger. Ô input box tự động co giãn 100% chiều rộng.
- **Khả năng phục hồi khi lỗi:** Nếu máy chủ mất kết nối hoặc trả về lỗi 503/409, adapter giữ câu hỏi trong lượt chờ, khóa nhập lượt mới và cho thử lại cùng request ID. Hủy chỉ bỏ lượt chờ khỏi ngữ cảnh UI; không đảm bảo dừng server. Điểm truy xuất là số xếp hạng, không phải phần trăm xác suất đúng.
