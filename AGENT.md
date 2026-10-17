# AGENT.md — Hướng dẫn và Quy tắc Rà soát Mã nguồn tự động với OpenAI Codex

Tài liệu này định nghĩa cấu hình, hướng dẫn vận hành và bộ quy tắc kiểm tra mã nguồn tự động (**Code Review Rules**) dành cho **OpenAI Codex** (Codex CLI, GitHub Codex App, và GitHub Actions) áp dụng riêng cho kho mã nguồn **Assistant-Team06 (Agent Học Tập)**.

---

## 1. Tổng quan về tính năng Code Review trên OpenAI Codex

OpenAI Codex cung cấp tính năng rà soát mã nguồn tự động ở cấp độ Pull Request và mã nguồn cục bộ, phân tích diff theo ngữ cảnh dự án thay vì chỉ kiểm tra cú pháp như các linter thông thường.

### 1.1 Cơ chế tìm kiếm và mức độ ưu tiên của tệp chỉ dẫn
Khi khởi chạy tác vụ rà soát mã nguồn, Codex quét tệp chỉ dẫn theo thứ tự ưu tiên giảm dần:
1. **Thư mục cục bộ / Subdirectory**: `AGENTS.override.md` > `AGENTS.md` (hoặc `AGENT.md`).
2. **Gốc dự án (Project Root)**: `AGENTS.override.md` > `AGENTS.md` > `AGENT.md`.
3. **Phạm vi toàn cục (Global Scope)**: `~/.codex/AGENTS.override.md` > `~/.codex/AGENTS.md`.

> [!NOTE]
> Codex ghép nối các chỉ dẫn từ gốc dự án đến thư mục thay đổi. Quy tắc tại thư mục con hoặc tệp chỉ định cụ thể hơn sẽ ghi đè quy tắc tổng quát khi xảy ra xung đột. Codex sẽ tìm kiếm và áp dụng trực tiếp phần `## Code Review Rules` trong quá trình phân tích diff.

### 1.2 Nguyên tắc rà soát cốt lõi của Codex
* **Tập trung vào lỗi thực tế (Actionable Defects)**: Chỉ báo cáo các lỗi cụ thể, có thể khắc phục được phát sinh trực tiếp từ diff thay đổi: tính đúng đắn (`correctness`), hiệu năng (`performance`), an toàn bảo mật (`security`), tranh chấp tài nguyên (`concurrency`), mất mát dữ liệu (`data loss`), và tương thích ngược (`compatibility`).
* **Hạn chế nhiễu (High-Signal Feedback)**: Không bình luận về sở thích phong cách trình bày (style/formatting) đã có linter quản lý; không phỏng đoán thiếu căn cứ; không báo cáo các lỗi tồn tại từ trước không thuộc phạm vi diff.
* **Quyền hạn chỉ đọc (Read-Only Review)**: Quá trình private review của Codex là bất biến (không tự ý sửa file, không commit, không push, không tự ý chạy code lạ ngoài môi trường kiểm thử cho phép).
* **Định danh phát hiện (Finding Reference)**: Mỗi nhận xét phát hiện lỗi phải chỉ rõ đường dẫn tệp tương đối, số dòng trong diff hunk, phía thay đổi (`right` cho code mới/sửa, `left` cho code bị xóa), kèm theo kịch bản lỗi cụ thể và trích dẫn quy tắc vi phạm trong tài liệu (`file:line`).

---

## 2. Các lệnh kích hoạt và Quy trình Rà soát Mã nguồn

### 2.1 Kích hoạt trực tiếp trên GitHub Pull Request
Khi mã nguồn được đẩy lên GitHub và mở Pull Request, các lệnh bình luận sau dùng để điều khiển Codex bot:

| Lệnh bình luận | Chức năng | Phạm vi kiểm tra |
| :--- | :--- | :--- |
| `@codex review` | Yêu cầu rà soát toàn diện diff của PR | Đúng đắn logic, tương thích ngược, regression, tuân thủ `## Code Review Rules`. |
| `@codex security review` | Rà soát chuyên sâu về an toàn thông tin | Rò rỉ thông tin nhạy cảm, SQL injection, bypass quyền hạn, path traversal, lộ bí mật. |

### 2.2 Rà soát cục bộ trên máy tính cá nhân (Codex CLI)
Codex CLI hỗ trợ kiểm tra mã nguồn trước khi commit hoặc trước khi tạo Pull Request:

1. **Khởi động Codex interactive**:
   ```bash
   codex
   ```
2. **Kích hoạt tính năng review trong giao diện CLI**:
   * Gõ lệnh: `/review`
   * Chọn một trong hai chế độ:
     * **Review uncommitted changes**: Rà soát các thay đổi chưa commit trên working tree (staged & unstaged diff).
     * **Review against a base branch**: So sánh nhánh tính năng hiện tại với nhánh chính (ví dụ: `main`).

3. **Chạy ở chế độ dòng lệnh tự động (Headless / Script mode)**:
   ```bash
   codex exec "Review the staged git diff for bugs, security risks, and project rule violations according to AGENT.md."
   ```

### 2.3 Tự động hóa qua GitHub Actions CI (`openai/codex-action`)
Workflow mẫu tích hợp kiểm tra tự động khi mở hoặc cập nhật PR:

```yaml
name: Codex Automated Code Review
on:
  pull_request:
    types: [opened, synchronize, ready_for_review]

permissions:
  contents: read
  pull-requests: write

jobs:
  codex-review:
    if: ${{ !github.event.pull_request.draft }}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Run Codex PR Review
        uses: openai/codex-action@v1
        with:
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

---

## 3. Phân cấp mức độ nghiêm trọng (Severity Priorities)

Codex phân loại phát hiện rà soát theo 3 mức độ ưu tiên chuẩn:

* **P0 — Critical (Chặn merge bắt buộc)**:
  * Lỗi nghiêm trọng gây crash hệ thống, deadlock hoặc hỏng dữ liệu.
  * Vi phạm bảo mật: lộ credentials/token, vi phạm Git allowlist, rò rỉ dữ liệu cá nhân/nội bộ.
  * Phá vỡ tính toàn vẹn của lịch sử dự án `LICH_SU_DU_AN.md`.
  * Trả lời bịa đặt căn cứ, giả mạo citation trích dẫn tài liệu học tập.
* **P1 — High (Yêu cầu giải trình hoặc sửa đổi)**:
  * Lỗi logic thuật toán, sai sót trong câu truy vấn cơ sở dữ liệu FTS5/Vector.
  * Race condition khi nhiều tiến trình/luồng cùng truy cập tài nguyên GPU/VRAM hoặc SQLite.
  * Phá vỡ hợp đồng API giữa frontend `static/` và backend `app/`.
  * Sửa đổi file expected trong test suite chỉ nhằm hợp thức hóa mã lỗi mà không có căn cứ.
* **P2 — Normal (Khuyến nghị cải thiện)**:
  * Xử lý ngoại lệ chưa bao quát các trường hợp biên (edge cases).
  * Thiếu log chẩn đoán lỗi ở những phân đoạn quan trọng.
  * Cải thiện hiệu năng xử lý chuỗi hoặc tối ưu tài nguyên bộ nhớ.

---

## 4. Code Review Rules — Bộ quy tắc dành riêng cho Project

Phần này chứa các ràng buộc bắt buộc mà Codex Code Review phải đối chiếu và kiểm tra trên từng diff của kho mã nguồn **Assistant-Team06**.

### 4.1 Quy tắc Dữ liệu & Git Allowlist (Độ ưu tiên: P0)
- **Cấm đưa dữ liệu thật vào Git**: Tuyệt đối không commit các tệp cơ sở dữ liệu SQLite (`data/knowledge_base.db`, `knowledge_base*.db`, `*.db-shm`, `*.db-wal`), vector nhúng, tệp trọng số mô hình lớn (model weights/checkpoints), hoặc tệp cấu hình bảo mật `.env`.
- **Bảo toàn nguồn gốc PDF**: Không được phép sửa đổi hoặc ghi đè các tệp PDF giáo trình gốc trong thư mục tài liệu.
- **Không ép thêm tệp cấm**: Cấm sử dụng `git add -f` đối với các tệp hoặc thư mục đã được khai báo trong `.gitignore`.

### 4.2 Tính xác thực Tri thức & Trích dẫn Nguồn (Độ ưu tiên: P0)
- **Nghiêm cấm bịa đặt Citation**: Mọi câu trả lời tri thức học tập bắt buộc phải gắn nguồn xác thực theo đúng hợp đồng (`document_id`, `page_number`, đoạn trích dẫn thực tế). Cấm tạo trích dẫn giả lập hoặc phỏng đoán nội dung không có trong tài liệu.
- **Giữ nguyên trạng thái chưa duyệt (Unresolved)**: Đối với các trang PDF bị lỗi scan, mờ chữ, công thức hình học chưa qua kiểm định, bắt buộc giữ nguyên trạng thái `unresolved` hoặc `HOLD_COMPLETENESS_NOT_INDEXED`. Không tự ý suy đoán ký tự hoặc công thức toán học.
- **Tách bạch Mock và Dữ liệu thật**: Các bài kiểm thử sử dụng mock/giả lập không được dùng để kết luận chất lượng trả lời của mô hình thật.

### 4.3 Tính toàn vẹn của Lịch sử Dự án `LICH_SU_DU_AN.md` (Độ ưu tiên: P0)
- **Bắt buộc cập nhật lịch sử khi đổi mã nguồn**: Mọi PR có thay đổi mã nguồn, kiểm thử hoặc cấu hình đều phải bổ sung một mục lịch sử mới vào cuối tệp `LICH_SU_DU_AN.md` ở thư mục gốc repo.
- **Định dạng ID chuẩn**: ID mục mới phải tuân thủ chuẩn schema v3:
  * `### YC-<số>` đối với các yêu cầu tổng thể.
  * `### LOG-YYYYMMDD-<chức năng>-<mã việc>-<vai trò>` đối với các PR của thành viên (ví dụ: `LOG-20261017-backend-issue20-tv3`).
- **Không xóa hoặc đánh lại số cũ**: Giữ nguyên toàn bộ các mục lịch sử đã có từ trước; không được phép xóa, viết đè hoặc thay đổi số thứ tự của các mốc lịch sử chung.
- **Nội dung súc tích, không chép prompt**: Nhật ký ghi tóm tắt lỗi/tính năng, nguyên nhân, trước/sau, phạm vi sửa, kết quả kiểm thử; tuyệt đối không sao chép nguyên văn prompt hội thoại hoặc thông tin trao đổi cá nhân.

### 4.4 Kiến trúc Hệ thống & Ranh giới Module (Độ ưu tiên: P1)
- **Giao diện người dùng chính thống**: Thư mục `static/` chứa toàn bộ mã giao diện web chính thức của sản phẩm. Thư mục `ui/` chỉ là bản mẫu Streamlit mockup phục vụ bài tập Lab 2. Không được phép chỉnh sửa `ui/` nhằm mục đích thay thế UI chính `static/`.
- **Một Backend duy nhất**: Toàn bộ logic nghiệp vụ, RAG agent, API chat và bóc tách dữ liệu tập trung tại `app/`. Không tạo thêm dịch vụ backend thứ hai gây phân mảnh kiến trúc.
- **Điều phối GPU / Tài nguyên độc quyền**: `LocalTranslator` và mô hình QA MLX phải được bao bọc qua bộ điều phối tài nguyên (`gpu_coordinator` hoặc cơ chế tương đương). Chỉ cho phép một tác vụ suy luận mô hình cục bộ chạy tại một thời điểm để ngăn chặn tràn VRAM và xung đột tiến trình trên macOS Apple Silicon.

### 4.5 Tiêu chuẩn Kiểm thử & Định nghĩa Hoàn tất (DoD) (Độ ưu tiên: P1)
- **Cấm sửa Expected để che giấu lỗi**: Không sửa đổi giá trị kỳ vọng trong test suite hoặc loại bỏ câu lệnh `assert` để làm cho bài test chuyển xanh một cách giả tạo khi mã nguồn vẫn sai.
- **Bảo toàn môi trường sản xuất**: Không sử dụng cơ sở dữ liệu production (`data/knowledge_base.db`) làm fixture kiểm thử mặc định; luôn sử dụng database mock hoặc sqlite in-memory cho unit tests.
- **Quyền hạn Writer độc quyền**: Mỗi phạm vi công việc chỉ do một bên (thành viên hoặc AI agent) nắm giữ quyền ghi tại một thời điểm. Xác nhận trạng thái terminal của tác vụ cũ trước khi tiếp nhận quyền ghi mới.

---

## 5. Danh mục các lệnh kiểm tra trước khi tạo / duyệt PR

Trước khi đưa ra kết luận review hoặc chấp thuận Pull Request, các lệnh sau đây phải được thực thi và xác nhận đạt kết quả:

### 5.1 Kiểm tra tính hợp lệ của Lịch sử Dự án
Kiểm tra xem PR có bổ sung mục nhật ký mới và giữ nguyên các mục cũ hay không:
```bash
# Trên Linux/macOS
./.venv/bin/python scripts/check_history.py --base <base-branch-or-commit>

# Trên Windows
.\.venv\Scripts\python.exe scripts\check_history.py --base <base-branch-or-commit>
```
*Kết quả kỳ vọng*: `History check passed.`

### 5.2 Kiểm tra tính toàn vẹn hệ thống và định tuyến ứng dụng
Xác nhận trạng thái router, cơ sở dữ liệu FTS5 và bộ đọc PDF:
```bash
# Chạy với profile kiểm tra cục bộ
./.venv/bin/python scripts/dev.py check --profile local
```
*Kết quả kỳ vọng*: `App routes: 49 | SQLite FTS5: OK | PDF: 1.28.2 | QA model present`

### 5.3 Chạy toàn bộ Test Suite lõi (Pytest)
```bash
# Thực thi bộ kiểm thử theo cấu hình chuẩn
./.venv/bin/python -m pytest -c pytest.windows.ini -q
```
*Kết quả kỳ vọng*: Toàn bộ các test lõi QA, API và hồi quy phải `PASSED` (cho phép skipped các bài test đặc thù khi thiếu tệp input riêng được chỉ định).

### 5.4 Kiểm tra giao diện người dùng (Node UI Tests)
```bash
node --test tests/chat_input_ui.test.cjs tests/document_lookup_ui.test.cjs tests/practice_ui.test.cjs tests/chat_lifecycle_ui_yc206.test.cjs
```
*Kết quả kỳ vọng*: Toàn bộ các kịch bản kiểm thử giao diện `PASSED`.

---

## 6. Mẫu phản hồi chuẩn của Codex Code Review

Khi đưa ra nhận xét trên GitHub PR hoặc phản hồi review cục bộ, Codex tuân theo cấu trúc sau:

```markdown
### 📝 Tóm tắt đánh giá
[Một câu súc tích tóm tắt kết luận review: PASS / REQUEST_CHANGES kèm lý do chính]

### 🔍 Chi tiết phát hiện lỗi (Findings)

#### [P0/P1/P2] Tiêu đề ngắn gọn của lỗi
- **Tệp**: `đường/dẫn/tệp.py` (Dòng: `L120-L135`) [Side: Right/Left]
- **Quy tắc vi phạm**: [AGENT.md#mục-liên-quan](file:///AGENT.md#L...)
- **Tình huống lỗi**: [Mô tả kịch bản cụ thể dẫn đến sự cố hoặc vi phạm hợp đồng]
- **Tác động (Impact)**: [Thấp / Trung bình / Cao — người dùng, luồng nghiệp vụ hoặc dữ liệu bị ảnh hưởng]
- **Giải pháp khắc phục đề xuất**:
  ```python
  # Đoạn mã gợi ý sửa chữa cụ thể
  ```

### 📋 Kết quả kiểm tra tự động (Verification Checklist)
- [ ] `scripts/check_history.py`: [PASS / FAIL]
- [ ] `scripts/dev.py check`: [PASS / FAIL]
- [ ] `pytest -c pytest.windows.ini`: [PASS / FAIL]
- [ ] Phù hợp quy định Git allowlist & phân quyền writer: [ĐẠT]
```
