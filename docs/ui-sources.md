# Thiết kế Quy chuẩn Hiển thị Nguồn Trích dẫn (UI Sources & Citations Design)

Tài liệu này đặc tả quy chuẩn thiết kế giao diện, cấu trúc dữ liệu và trải nghiệm người dùng (UX) khi hiển thị các nguồn trích dẫn (`citations / sources`) bên dưới câu trả lời của Trợ lý Học tập, theo tiêu chí nghiệm thu của **Issue #19 (Lab 2)**.

---

## 1. Tầm quan trọng của Trích dẫn Nguồn trong Trợ lý Học tập

Trong hệ thống Hỏi đáp Học tập dựa trên kỹ thuật RAG (Retrieval-Augmented Generation):
- **Minh bạch và Chống ảo giác (Anti-Hallucination):** Người học cần biết chính xác thông tin được suy ra từ giáo trình, quy chế hay bài giảng nào.
- **Khả năng kiểm chứng trực tiếp (Verifiability):** Sinh viên có thể bấm trực tiếp để mở đúng trang PDF gốc nhằm đọc sâu hơn hoặc xem bảng biểu, sơ đồ minh họa.
- **Cảnh báo độ tin cậy:** Phân biệt rõ các trang đã được duyệt hoàn toàn với các trang nhận dạng chữ viết (OCR) chưa hoàn chỉnh.

---

## 2. Cấu trúc Dữ liệu Nguồn Trích dẫn (Data Contract)

Dữ liệu nguồn được Backend FastAPI trả về trong trường `citations` của mỗi câu trả lời (`/api/chat` hoặc sự kiện `citations` trong `/api/chat/stream`):

```json
{
  "id": "S1",
  "book_title": "Giáo trình Mạng máy tính",
  "filename": "mang_may_tinh.pdf",
  "page_num": 148,
  "chunk_index": 3,
  "snippet": "Quá trình thiết lập kết nối TCP sử dụng cơ chế bắt tay 3 bước (three-way handshake): Bước 1 Client gửi gói SYN, Bước 2 Server gửi gói SYN-ACK, Bước 3 Client gửi gói ACK xác nhận...",
  "snippet_vi": "",
  "score": 0.945,
  "pdf_url": "/api/documents/1/pdf#page=148",
  "page_quality": {
    "needs_review": false,
    "ocr_confidence": 0.98
  }
}
```

---

## 3. Quy chuẩn Thiết kế Giao diện (UI/UX Specifications)

1. **Vị trí hiển thị:** Nằm ngay dưới phần nội dung câu trả lời của Trợ lý, phân cách bằng khoảng đệm và đường phân cách mờ.
2. **Khối chứa (Container):** 
   - Đặt trong khối accordion có thể gập/mở (`<details>` trong HTML hoặc `st.expander` trong Streamlit).
   - Tiêu đề khối: `📚 Đối chiếu với tài liệu gốc (N nguồn):`
   - Nguồn có điểm liên quan cao nhất (`[S1]`) mặc định mở sẵn để người dùng đối chiếu tức thì; các nguồn tiếp theo có thể thu gọn để giao diện gọn gàng.
3. **Thành phần của mỗi thẻ trích dẫn:**
   - **Thẻ định danh (Citation Tag):** `[S1]`, `[S2]` tương ứng với các ký hiệu chú thích trong thân bài viết.
   - **Tên tài liệu & Vị trí:** Tên tài liệu in đậm kèm số trang (ví dụ: `Giáo trình Mạng máy tính · Trang 148`).
   - **Chỉ số tương đồng (Score Badge):** Hiển thị độ khớp thông tin (ví dụ: `Độ khớp: 95%`).
   - **Đoạn trích dẫn (Snippet Block):** Đặt trong thẻ trích dẫn (`blockquote`) với đường kẻ viền trái màu nổi bật, thể hiện đúng ngữ cảnh đoạn văn trong sách.
   - **Hộp cảnh báo OCR (nếu `needs_review == true`):** Dải thông báo màu vàng cam nhắc nhở sinh viên đối chiếu công thức, hình ảnh và bảng biểu trong file gốc.
   - **Nút hành động (Action Button):** Nút bấm `[📖 Mở trang X (PDF)]` để kích hoạt popup xem trang PDF hoặc mở tab mới tại đúng trang.

---

## 4. Hai Ví dụ Minh họa Trực quan Cụ thể (2 Detailed Mockup Examples)

### 📌 Ví dụ 1: Câu trả lời định nghĩa có nguồn PDF chính thức (Đơn nguồn, Độ khớp cao)

- **Câu hỏi của người dùng:**  
  *"Cơ chế bắt tay 3 bước (Three-way handshake) trong giao thức TCP diễn ra như thế nào?"*

- **Giao diện hiển thị trên màn hình:**

```
+--------------------------------------------------------------------------------------------------+
| 🤖 Trợ lý Học tập                                                                                |
|                                                                                                  |
| Quá trình thiết lập kết nối TCP sử dụng cơ chế bắt tay 3 bước (Three-way Handshake) nhằm đảm bảo |
| cả hai phía gửi và nhận đều sẵn sàng đồng bộ số thứ tự tuần tự (Sequence Number) [S1]:          |
|                                                                                                  |
| 1. Bước 1 (SYN): Bên yêu cầu kết nối (Client) gửi gói tin TCP có cờ SYN = 1 và chọn ngẫu nhiên   |
|    một số thứ tự ban đầu (seq = x).                                                             |
| 2. Bước 2 (SYN-ACK): Bên nhận (Server) phản hồi gói tin có cả cờ SYN = 1 và ACK = 1, xác nhận   |
|    số thứ tự của client (ack = x + 1) và khởi tạo số thứ tự của server (seq = y).               |
| 3. Bước 3 (ACK): Client gửi lại gói tin xác nhận ACK = 1 với số thứ tự xác nhận (ack = y + 1).    |
|    Sau bước này, kết nối TCP được thiết lập thành công và dữ liệu có thể bắt đầu truyền đi.      |
|                                                                                                  |
| ▼ 📚 Đối chiếu với tài liệu gốc (1 nguồn tham khảo):                                             |
| +----------------------------------------------------------------------------------------------+ |
| | [S1] Giáo trình Mạng máy tính · Trang 148                   [Độ khớp: 96%] [Đã xác thực OCR] | |
| |                                                                                              | |
| | > "Quá trình thiết lập kết nối TCP sử dụng cơ chế bắt tay 3 bước (three-way handshake).       | |
| | > Phía khởi tạo gửi phân đoạn SYN với số thứ tự ngẫu nhiên ban đầu. Phía chấp nhận gửi lại   | |
| | > SYN-ACK xác nhận, và cuối cùng phía khởi tạo gửi phân đoạn ACK hoàn tất đồng bộ hóa..."     | |
| |                                                                                              | |
| | [📖 Mở trang 148 (PDF)]    [🔍 Tra cứu thêm trong tài liệu này]                               | |
| +----------------------------------------------------------------------------------------------+ |
+--------------------------------------------------------------------------------------------------+
```

---

### 📌 Ví dụ 2: Câu trả lời tổng hợp đa nguồn kết hợp cảnh báo OCR chưa duyệt đầy đủ

- **Câu hỏi của người dùng:**  
  *"Quy định về việc đăng ký học cải thiện điểm và thời hạn nộp đơn hoãn thi như thế nào?"*

- **Giao diện hiển thị trên màn hình:**

```
+--------------------------------------------------------------------------------------------------+
| 🤖 Trợ lý Học tập                                                                                |
|                                                                                                  |
| Căn cứ theo các văn bản học vụ hiện hành của Nhà trường:                                         |
|                                                                                                  |
| 1. Về đăng ký học cải thiện [S1]: Sinh viên có học phần đạt điểm D, D+, C hoặc C+ được phép đăng |
|    ký học lại để cải thiện điểm trung bình chung tích lũy. Điểm cao nhất giữa các lần học sẽ     |
|    được tính vào điểm tích lũy chính thức.                                                       |
|                                                                                                  |
| 2. Về thời hạn nộp đơn hoãn thi [S2]: Sinh viên vì lý do bất khả kháng (ốm đau đột xuất, tai nạn)|
|    phải nộp đơn xin hoãn thi kèm minh chứng y tế hợp lệ cho Phòng Đào tạo chậm nhất trong vòng   |
|    03 ngày làm việc kể từ ngày thi của học phần đó.                                             |
|                                                                                                  |
| ▼ 📚 Đối chiếu với tài liệu gốc (2 nguồn tham khảo):                                             |
| +----------------------------------------------------------------------------------------------+ |
| | [S1] Quy chế Đào tạo Học chế Tín chỉ (2024) · Trang 28                     [Độ khớp: 92%]     | |
| |                                                                                              | |
| | > "Điều 18. Học lại và học cải thiện điểm: Sinh viên được quyền đăng ký học cải thiện điểm    | |
| | > đối với các học phần có điểm tổng kết từ D trở lên. Khi tính điểm tích lũy, hệ thống sẽ    | |
| | > lấy kết quả của lần học có điểm số cao nhất..."                                            | |
| |                                                                                              | |
| | [📖 Mở trang 28 (PDF)]                                                                       | |
| +----------------------------------------------------------------------------------------------+ |
| | [S2] Sổ tay Sinh viên & Hướng dẫn Học vụ · Trang 45                        [Độ khớp: 88%]     | |
| |                                                                                              | |
| | ⚠️ Trang nguồn này chưa được duyệt đầy đủ OCR.                                                | |
| | Vui lòng mở tài liệu gốc để đối chiếu lại mốc thời gian, biểu mẫu và quy trình chi tiết.     | |
| |                                                                                              | |
| | > "Thời hạn nộp đơn hoãn thi: Chậm nhất 03 ngày làm việc kể từ buổi thi. Hồ sơ gồm đơn xin    | |
| | > hoãn thi theo mẫu 02-ĐT và giấy chứng nhận của cơ sở y tế từ tuyến huyện trở lên..."        | |
| |                                                                                              | |
| | [📖 Mở trang 45 (PDF để đối chiếu)]                                                           | |
| +----------------------------------------------------------------------------------------------+ |
+--------------------------------------------------------------------------------------------------+
```

---

## 5. Xử lý Trường hợp Ngoại lệ (Edge Cases)

- **Trường hợp không tìm thấy tài liệu liên quan:**  
  Nếu mô hình không tìm thấy văn bản phù hợp hoặc câu hỏi nằm ngoài phạm vi tài liệu đã nạp, Trợ lý sẽ thông báo từ chối lịch sự: *"Xin lỗi, tài liệu học tập hiện tại không có dữ liệu để trả lời câu hỏi này. Bạn có thể chọn nguồn khác hoặc tra cứu trực tiếp qua mục Tra cứu tài liệu."* và ẩn hoàn toàn khối trích dẫn nguồn.
- **Tài liệu dạng văn bản không có PDF:**  
  Nếu tài liệu trích dẫn là file Markdown (`.md`) hoặc Text (`.txt`), nhãn `Trang X` được chuyển thành `Phần X` và nút bấm chuyển thành `[📄 Xem đoạn trích gốc]`.
