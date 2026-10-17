# Quản lý nguồn dữ liệu và phân công sở hữu (Data Sources & Owners)

Thư mục `data/` chứa các cơ sở dữ liệu SQLite cục bộ, cấu hình adapter, bộ nhớ đệm và các file khóa runtime phục vụ vận hành của ứng dụng **Study Agent**.

Theo quy chuẩn dự án tại [README_QUY_TAC.md](../README_QUY_TAC.md) và chính sách bảo mật, toàn bộ dữ liệu thực tế (database, vector embedding, lịch sử hội thoại, ảnh lịch cá nhân) **không được commit lên Git** và đã được loại trừ qua `.gitignore` (`/data/*`, ngoại trừ duy nhất tệp tài liệu `!/data/README.md`). Tài liệu này quy định nguồn gốc dữ liệu, phân công trách nhiệm sở hữu và chu kỳ cập nhật cho từng thành phần.

---

## 1. Bảng tổng hợp nguồn dữ liệu, chủ sở hữu và tần suất cập nhật

| Tệp / Thư mục | Mục đích & Nội dung | Nguồn dữ liệu (Data Source) | Vai trò phụ trách (Owner) | Tần suất cập nhật (Update Frequency) | Chính sách Git & Lưu trữ |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `knowledge_base.db` | CSDL SQLite chính lưu trữ danh mục tài liệu, các đoạn trích văn bản (chunks), vector embedding, chỉ mục toàn văn FTS5 và đánh giá chất lượng trang nguồn. Xem owning source: `app/indexer.py`. | Trích xuất và lập chỉ mục tự động từ tài liệu trong `src/` (hoặc fixtures kiểm thử) thông qua `KnowledgeIndexer` (`app/indexer.py`, `app/pdf_extractor.py`). | **TV4** (Dữ liệu, chỉ mục chính & Huấn luyện) | Khi chạy lệnh lập chỉ mục tài liệu mới hoặc khi người dùng đồng bộ thư viện qua API/UI. Với demo: chạy `scripts/dev.py demo`. | Bị loại trừ bởi `.gitignore` (`/data/*`), không commit lên Git. Lưu trữ bền vững tại local. |
| `history.db` | Lưu trữ lịch sử phiên làm việc (`sessions`), chi tiết từng lượt hỏi đáp (`session_turns`), metadata di trú (`history_meta`) và lịch sử tra cứu cũ (`searches`). Trên POSIX, cố gắng đặt quyền file 0600 (owner read/write). Xem owning source: `app/history.py`. | Sinh ra từ các tương tác trực tiếp của người học qua API `/api/chat` và quản lý phiên bởi `HistoryStore` (`app/history.py`). | **TV2** (QA & Hội thoại) | Thời gian thực (Real-time): Cập nhật sau mỗi lượt hoàn thành câu trả lời của Agent. | Bị loại trừ bởi `.gitignore`, không commit lên Git. Dữ liệu hội thoại giữ local; quyền tệp phụ thuộc hệ điều hành. |
| `timetable.db` | Lưu bản nháp thời khóa biểu nhận diện từ ảnh (`timetable_drafts`), sự kiện xác nhận (`timetable_confirmation_events`), lịch học chính thức (`timetable_confirmed`), cache VLM (`timetable_vlm_cache`) và ảnh gốc tại `data/timetable_images/`. Xem owning source: `app/timetable_store.py`. | Ảnh thời khóa biểu do người dùng tải lên, được phân tích bởi mô hình thị giác thông qua `app/timetable_vision.py` và `app/timetable_store.py`. | **TV3** (Thời khóa biểu từ ảnh) | Theo nhu cầu (On-demand): Cập nhật khi người dùng tải ảnh và xác nhận lưu thời khóa biểu. | Bị loại trừ bởi `.gitignore`, không commit lên Git. Dữ liệu lịch học cá nhân được bảo mật cục bộ. |
| `translations.db` | CSDL SQLite lưu bộ nhớ đệm bản dịch tiếng Việt cho các đoạn trích tiếng Anh (`translations`: `cache_key`, `text_vi`) phục vụ hiển thị song ngữ nhanh. Xem owning source: `app/bilingual.py`. | Tự động dịch và lưu cache bởi `LocalTranslator` (`app/bilingual.py`) khi RAG truy xuất và dịch các đoạn nguồn tiếng Anh. | **TV1** (Tra cứu) & **TV2** (QA) | Bán tĩnh (Semi-static): Tự động ghi cache khi dịch các đoạn truy xuất mới. | Bị loại trừ bởi `.gitignore`. Được khởi tạo tự động khi chạy nếu chưa tồn tại. |
| `adapters/active_registry.json` | Tệp cấu hình JSON đăng ký và quản lý các adapter mô hình (LoRA/MLX) đã qua kiểm định dossier chất lượng; mặc định dùng mô hình nền unadapted base model. Xem owning source: `app/active_registry.py`. | Xuất ra từ quy trình huấn luyện và kiểm định chất lượng candidate thông qua `app/active_registry.py`. | **TV4** (Huấn luyện & Điều phối phát hành) | Thấp / Theo phiên bản (Release-driven): Cập nhật khi có phiên bản adapter mới được nghiệm thu đạt chuẩn. | Bị loại trừ bởi `.gitignore` (`/data/*`), KHÔNG commit lên Git. Là trạng thái cấu hình runtime cục bộ, không phải Git metadata công khai. |
| `runtime/` | Chứa các file khóa tiến trình (`dialogue.lock`, `gpu.lock`) nhằm điều phối GPU và chống tranh chấp giữa suy luận (inference) và huấn luyện/indexing. Xem owning source: `app/file_lock.py`, `app/gpu_lock.py`, `app/dialogue.py`. | Tạo động bởi `app/file_lock.py`, `app/gpu_lock.py` và `app/dialogue.py`. | **TV4** (Tài nguyên GPU) & **TV5** (Hệ thống & Môi trường) | Tạm thời (Ephemeral): Khóa được nhả khi tác vụ kết thúc; tệp khóa có thể còn tồn tại. Không xóa tệp khi tiến trình đang giữ khóa. | Bị loại trừ bởi `.gitignore`, không commit lên Git. |
| `ocr_cache/` | Thư mục bộ nhớ đệm lưu kết quả nhận diện chữ từng trang tài liệu quét (OCR text cache) theo source hash, số trang, backend, tùy chọn, model digest và phiên bản prompt. Xem owning source: `app/vision_ocr.py`. | Đầu ra từ module nhận diện OCR thị giác `app/vision_ocr.py`. | **TV3** (Thời khóa biểu/Thị giác) & **TV4** (Dữ liệu) | Khi có trang tài liệu dạng ảnh mới được quét và thẩm định OCR. | Bị loại trừ bởi `.gitignore`, lưu trữ tạm thời trên máy cục bộ. |
| `team-demo/` | Thư mục dữ liệu độc lập chứa bản sao cô lập của CSDL phục vụ chạy thử nghiệm cục bộ với profile demo của nhóm (`scripts/dev.py demo` hoặc `setup_windows.ps1 -DemoOnly`). Xem owning source: `scripts/dev.py`. | Dựng tự động từ các tài liệu fixture kiểm thử công khai trong `tests/fixtures/documents/` thông qua `scripts/dev.py demo`. | **TV5** (Môi trường) & **TV6** (Kiểm thử & Nghiệm thu) | Khi thiết lập ban đầu hoặc khi người dùng chạy lệnh `scripts/dev.py demo`. | Bị loại trừ bởi `.gitignore`, không commit lên Git. Lưu ý: Trong CI/CD, các bài test sử dụng fixture thư mục tạm thời (`tmp_path`) độc lập, không mặc định chạy trên `data/team-demo/`. |

---

## 2. Chi tiết cấu trúc và lược đồ (Schema) của các CSDL

Để tránh sai lệch khi mã nguồn nâng cấp, cấu trúc chi tiết được quy định và dẫn chiếu trực tiếp tại module sở hữu (owning source):

### 2.1. `knowledge_base.db` (Kho tri thức & Chỉ mục)
- **Owning source:** `app/indexer.py` (hàm `_init_db`).
- **Các bảng quản lý:**
  - `documents`: Quản lý metadata tệp nguồn (`filename`, `clean_title`, `filepath`, `file_size`, `file_hash`, `total_pages`, `extracted_pages_count`, `is_scanned`, `status`, `pipeline_version`, `updated_at`).
  - `chunks`: Lưu trữ từng đoạn văn bản trích xuất kèm embedding (`doc_id`, `book_title`, `filename`, `page_num`, `chunk_index`, `text`, `embedding`, `created_at`).
  - `chunks_fts`: Bảng ảo FTS5 hỗ trợ tìm kiếm toàn văn tiếng Việt cho `book_title` và `text`.
  - `document_source_quality`: Nhật ký kiểm định chất lượng trang và cảnh báo OCR (`needs_review`, `quality_warnings`, `raw_char_count`, `raw_capture_hash`).

### 2.2. `history.db` (Lịch sử hội thoại)
- **Owning source:** `app/history.py` (hàm `_init_db`).
- **Phân quyền tệp:** code cố gắng đặt `0600` trên POSIX; lỗi đổi quyền được bỏ qua. Windows cần quản lý ACL thư mục/tệp, không coi số `0600` là bảo đảm quyền trên mọi máy.
- **Các bảng quản lý:**
  - `sessions`: Thông tin phiên hỏi đáp (`id`, `title`, `title_mode`, `summary`, `summary_status`, `summarized_revision`, `created_at`, `updated_at`, `revision`, `is_legacy`).
  - `session_turns`: Từng lượt hỏi đáp được đánh chỉ mục (`session_id`, `turn_index`, `question`, `answer`, `citations_json`, `model`, `answer_mode`, `search_scope_json`, `request_id`, `legacy_search_id`, `created_at`).
  - `history_meta`: Lưu trữ cấu hình di trú và thông tin metadata hệ thống (`key`, `value`).
  - `searches`: Bảng lưu vết các lượt tra cứu nhanh độc lập (chế độ tương thích ngược legacy).

### 2.3. `timetable.db` (Thời khóa biểu)
- **Owning source:** `app/timetable_store.py` (hàm `init_schema`).
- **Thư mục ảnh liên kết:** `data/timetable_images/` (lưu ảnh gốc đặt tên theo sha256).
- **Các bảng quản lý:**
  - `timetable_drafts`: Lưu bản nháp phân tích từ ảnh (`draft_id`, `image_hash`, `filename`, `created_at`, `status`, `raw_model_output`, `entries_json`, `uncertainties_json`, `model_digest`, `prompt_version`, `options_json`, `confirmed_at`, `confirmed_entries_json`).
  - `timetable_confirmation_events`: Lịch sử các lần người dùng xác nhận lưu lịch (`id`, `draft_id`, `confirmed_at`, `entries_json`, `replaced_schedule_json`).
  - `timetable_confirmed`: Danh sách các môn học chính thức sau khi xác nhận (`id`, `draft_id`, `course`, `weekday`, `start_time`, `end_time`, `room`, `created_at`).
  - `timetable_vlm_cache`: Bộ nhớ đệm kết quả mô hình thị giác theo `cache_key` và `image_hash`.

### 2.4. `translations.db` (Bộ nhớ đệm bản dịch song ngữ)
- **Owning source:** `app/bilingual.py` (`LocalTranslator`).
- **Các bảng quản lý:**
  - `translations`: Lưu trữ bản dịch tiếng Việt cho từng đoạn trích (`cache_key TEXT PRIMARY KEY`, `text_vi TEXT NOT NULL`).

---

## 3. Nguyên tắc quản trị và an toàn dữ liệu

1. **Nguyên tắc người ghi duy nhất (Single Writer Rule):** Thành viên **TV4** là đầu mối duy nhất phụ trách hợp nhất chỉ mục và cơ sở dữ liệu chính của dự án. Không cho phép nhiều tiến trình hoặc nhiều thành viên ghi đè cơ sở dữ liệu đồng thời.
2. **Quyền riêng tư & Bảo mật:** Dữ liệu học tập cá nhân, câu hỏi người dùng, ảnh thời khóa biểu và các file cơ sở dữ liệu `.db` phải luôn được giữ an toàn trên máy cục bộ, tuyệt đối không được đưa lên repository công khai.
3. **Phân biệt môi trường Demo và Kiểm thử CI:**
   - **Môi trường Demo cục bộ (`profile=demo`):** Sử dụng thư mục cô lập `data/team-demo/` thông qua lệnh `python scripts/dev.py demo` với 29 tài liệu mẫu công khai từ `tests/fixtures/documents/`.
   - **Kiểm thử tự động (CI/test suite):** CI chạy bước smoke với profile demo, sau đó suite dùng fixture/thư mục tạm hoặc mock tại các ranh giới liên quan. Test kiểm định dữ liệu huấn luyện đã tách khỏi DB cá nhân; không cần corpus production để chạy bộ kiểm thử portable.

## 4. Đường dẫn cấu hình

Các tên trên là vị trí mặc định, không phải đường dẫn cố định: `AGENT_DATA_DIR` đổi thư mục dữ liệu; `HISTORY_DB_PATH`, `TIMETABLE_DB_PATH` và `TIMETABLE_IMAGES_DIR` có thể ghi đè vị trí tương ứng. Xem `app/config.py`, `app/history.py` và `app/timetable_store.py` trước khi thay đổi. Không trỏ kiểm thử vào DB production.
