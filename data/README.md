# Quản lý nguồn dữ liệu và phân công sở hữu (Data Sources & Owners)

Thư mục `data/` chứa các cơ sở dữ liệu SQLite cục bộ, cấu hình adapter, bộ nhớ đệm và các file khóa runtime phục vụ vận hành của ứng dụng **Study Agent**.

Theo quy chuẩn dự án tại [README_QUY_TAC.md](../README_QUY_TAC.md) và chính sách bảo mật, toàn bộ dữ liệu thực tế (database, vector embedding, lịch sử hội thoại, ảnh lịch cá nhân) **không được commit lên Git** và đã được loại trừ qua `.gitignore`. File `data/README.md` này có nhiệm vụ tài liệu hóa nguồn gốc dữ liệu, phân công trách nhiệm sở hữu và chu kỳ cập nhật của từng file/thư mục.

---

## 1. Bảng tổng hợp nguồn dữ liệu, chủ sở hữu và tần suất cập nhật

| Tệp / Thư mục | Mục đích & Nội dung | Nguồn dữ liệu (Data Source) | Vai trò phụ trách (Owner) | Tần suất cập nhật (Update Frequency) | Chính sách Git & Lưu trữ |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `knowledge_base.db` | CSDL SQLite chính lưu trữ danh mục tài liệu (`documents`), các đoạn trích văn bản (`chunks`), vector embedding và chỉ mục toàn văn FTS5 (`chunks_fts`), kèm thông tin kiểm định chất lượng (`document_source_quality`). | Trích xuất và lập chỉ mục tự động từ tài liệu PDF/Markdown trong `src/` (hoặc `tests/fixtures/documents/`) thông qua pipeline `app/indexer.py` và `app/pdf_extractor.py`. | **TV4** (Dữ liệu, chỉ mục chính & Huấn luyện) | Khi chạy lệnh lập chỉ mục (`dev.py index`) hoặc khi người dùng kích hoạt **Đồng bộ thư viện** trên UI. | Bị loại trừ bởi `.gitignore`, không commit lên Git. Lưu trữ bền vững tại local. |
| `history.db` | Lưu trữ lịch sử phiên làm việc (`sessions`), các lượt hỏi đáp (`session_turns`), câu hỏi, câu trả lời, trích dẫn bằng chứng (`citations_json`) và chế độ trả lời. | Sinh ra từ các tương tác trực tiếp của người học qua API `/api/chat` và quản lý bởi `app/history.py`. | **TV2** (QA & Hội thoại) | Thời gian thực (Real-time): Cập nhật sau mỗi lượt hoàn thành câu trả lời của Agent. | Bị loại trừ bởi `.gitignore`, không commit lên Git. Lưu trữ cục bộ theo máy người dùng. |
| `timetable.db` | Lưu bản nháp thời khóa biểu nhận diện từ ảnh (`timetable_drafts`), lịch học chính thức đã xác nhận (`timetable_confirmed`) và cache phản hồi VLM (`timetable_vlm_cache`). | Ảnh thời khóa biểu do người dùng tải lên, được phân tích bởi mô hình thị giác thông qua `app/timetable_vision.py` và `app/timetable_store.py`. | **TV3** (Thời khóa biểu từ ảnh) | Theo nhu cầu (On-demand): Cập nhật khi người dùng tải ảnh và xác nhận lưu thời khóa biểu. | Bị loại trừ bởi `.gitignore`, không commit lên Git. Dữ liệu lịch học cá nhân được bảo mật cục bộ. |
| `translations.db` | CSDL SQLite lưu bộ nhớ đệm các cặp thuật ngữ song ngữ Anh - Việt để hỗ trợ chuẩn hóa câu truy vấn và đối chiếu tài liệu. | Trích xuất và tích lũy tự động từ quá trình chuẩn hóa truy vấn của `app/bilingual.py` và `app/query_normalizer.py`. | **TV1** (Tra cứu) & **TV2** (QA) | Bán tĩnh (Semi-static): Cập nhật khi hệ thống phát hiện và dịch thuật ngữ mới. | Bị loại trừ bởi `.gitignore`. Được khởi tạo tự động nếu chưa tồn tại. |
| `adapters/active_registry.json` | Tệp cấu hình JSON đăng ký và quản lý các adapter mô hình đã huấn luyện (LoRA/MLX) đang được kích hoạt. | Xuất ra từ quy trình huấn luyện và kiểm định chất lượng candidate thông qua `app/active_registry.py`. | **TV4** (Huấn luyện & Điều phối phát hành) | Thấp / Theo phiên bản (Release-driven): Cập nhật khi có phiên bản adapter mới được nghiệm thu đạt chuẩn. | Metadata được phép theo dõi; trọng số model (weights) lưu trữ riêng. |
| `runtime/` | Chứa các file khóa tiến trình (`dialogue.lock`, `gpu.lock`) nhằm điều phối GPU và chống tranh chấp giữa suy luận (inference) và huấn luyện/indexing. | Tạo động bởi `app/file_lock.py`, `app/gpu_lock.py` và `app/dialogue.py`. | **TV4** (Tài nguyên GPU) & **TV5** (Hệ thống & Môi trường) | Tạm thời (Ephemeral): Tự động tạo khi tác vụ bắt đầu và giải phóng khi tác vụ kết thúc. | Bị loại trừ bởi `.gitignore`, không commit lên Git. |
| `ocr_cache/` | Thư mục bộ nhớ đệm lưu kết quả nhận diện chữ từng trang tài liệu quét (OCR text cache) để tăng tốc độ và tránh gọi lại VLM. | Đầu ra từ module nhận diện OCR thị giác `app/vision_ocr.py`. | **TV3** & **TV4** | Khi có trang tài liệu dạng ảnh mới được quét và thẩm định OCR. | Bị loại trừ bởi `.gitignore`, lưu trữ tạm thời trên máy cục bộ. |
| `team-demo/` | Thư mục dữ liệu độc lập, chứa bản sao cô lập của các cơ sở dữ liệu (`knowledge_base.db`, `history.db`,...) phục vụ chạy demo và kiểm thử. | Dựng tự động từ 29 tài liệu kiểm thử công khai trong `tests/fixtures/documents/` thông qua `setup_windows.ps1 -DemoOnly` hoặc `scripts/dev.py demo`. | **TV5** (Môi trường) & **TV6** (Kiểm thử & Nghiệm thu) | Khi thiết lập ban đầu hoặc khi người dùng reset lại môi trường demo. | Dữ liệu sinh ra không commit; kịch bản sinh dữ liệu nằm trong test suite. |

---

## 2. Chi tiết cấu trúc và lược đồ (Schema) của các CSDL

### 2.1. `knowledge_base.db` (Kho tri thức & Chỉ mục)
- **Bảng `documents`:** Quản lý metadata của file nguồn (`filename`, `clean_title`, `filepath`, `file_size`, `file_hash`, `total_pages`, `extracted_pages_count`, `is_scanned`, `status`, `pipeline_version`, `updated_at`).
- **Bảng `chunks`:** Lưu trữ từng đoạn văn bản được cắt nhỏ (`doc_id`, `book_title`, `filename`, `page_num`, `chunk_index`, `text`, `embedding`, `created_at`).
- **Bảng ảo FTS5 `chunks_fts`:** Chỉ mục toàn văn tiếng Việt tối ưu hóa tra cứu từ khóa cho `book_title` và `text`.
- **Bảng `document_source_quality`:** Nhật ký kiểm định chất lượng trang và cảnh báo OCR (`needs_review`, `quality_warnings`, `raw_char_count`, `raw_capture_hash`).

### 2.2. `history.db` (Lịch sử hội thoại)
- **Bảng `sessions`:** Thông tin phiên hỏi đáp (`id`, `title`, `title_mode`, `summary`, `created_at`, `updated_at`).
- **Bảng `session_turns`:** Chi tiết từng lượt hỏi đáp (`session_id`, `turn_index`, `question`, `answer`, `citations_json`, `model`, `answer_mode`, `search_scope_json`, `created_at`).
- **Bảng `searches`:** Lưu trữ các lượt tra cứu nhanh độc lập (legacy mode).

### 2.3. `timetable.db` (Thời khóa biểu)
- **Bảng `timetable_drafts`:** Lưu kết quả phân tích thô từ ảnh (`draft_id`, `image_hash`, `raw_model_output`, `entries_json`, `uncertainties_json`, `status`).
- **Bảng `timetable_confirmed`:** Lịch học chính thức sau khi người dùng xác nhận (`draft_id`, `course`, `weekday`, `start_time`, `end_time`, `room`).
- **Bảng `timetable_vlm_cache`:** Cache kết quả phân tích theo `image_hash` để tiết kiệm tài nguyên khi tải lại cùng một ảnh.

---

## 3. Nguyên tắc quản trị và an toàn dữ liệu

1. **Nguyên tắc người ghi duy nhất (Single Writer Rule):** Thành viên **TV4** là đầu mối duy nhất phụ trách hợp nhất chỉ mục và cơ sở dữ liệu chính của dự án. Không cho phép nhiều tiến trình hoặc nhiều thành viên ghi đè cơ sở dữ liệu đồng thời.
2. **Quyền riêng tư & Bảo mật:** Dữ liệu học tập cá nhân, câu hỏi người dùng, ảnh thời khóa biểu và các file cơ sở dữ liệu `.db` phải luôn được giữ an toàn trên máy cục bộ, tuyệt đối không được đưa lên repository công khai.
3. **Cô lập môi trường Demo:** Khi chạy thử nghiệm tính năng hoặc CI/CD, hệ thống luôn sử dụng thư mục cô lập `data/team-demo/` để không làm sai lệch hay ghi đè vào dữ liệu nghiên cứu thật trong `data/`.
