# Rà soát đồng bộ và kế hoạch nghiệm thu QA học tập — YC217

> Mốc YC217 được giữ để truy nguyên. Các lỗi tích hợp đã được hiệu chỉnh tiếp trong [YC218](RA_SOAT_TICH_HOP_YC218.md), PR #35; dùng báo cáo mới khi đánh giá hiện trạng.

Thời điểm đối chiếu: 16/10/2026, khoảng 20:00–20:40 (UTC+07).
Baseline đầu `02e9547`; đã đồng bộ thêm PR #34 tới main `d0d1ae0`, repo `ng-wngkh07/Assistant-Team06`.
Nhánh làm tài liệu: `codex/docs/qa-project-review-yc217`.

Quyết định người dùng: giữ triển khai 12 issue cho các thành viên; sản phẩm là
QA học tập, tra cứu văn phòng là ví dụ demo. Đây là rà soát hiện tại và cổng
kiểm tương lai; **chưa phải nghiệm thu sau khi cả 12 issue hoàn tất**.

## Đồng bộ repo và thay đổi đã xử lý

- Repo đã đổi tên từ Study-Agent sang Assistant-Team06. Đã sửa URL clone, CI,
  tải ZIP và tên thư mục trong README chính/hai hướng dẫn nền tảng/CONTRIBUTING.
- Checkout cũ ở nhánh #29 đã merge, thiếu PR #33. Đã fetch và tạo nhánh tài liệu
  từ main; giữ mẫu issue/PR sửa dở, không reset/stash/xóa thay đổi của thành viên.
- #29 có log nhưng thiếu báo cáo trong Git do ignore. Đã bổ sung file báo cáo
  vào thay đổi chờ review, sửa kết luận tuyệt đối về an toàn/PII thành phạm vi
  có bằng chứng; attribution Git/LICENSE được giữ.
- `DEFINITION_OF_DONE.md` được 12 issue viện dẫn nhưng chưa tồn tại. Đã thêm,
  liên kết với quy trình, template PR và kiểm đầu ra bị ignore.
- Bổ sung ngoại lệ Git đúng file cho rule project, backlog Lab 2, ví dụ văn phòng
  và benchmark. DB/vector/weights/hồ sơ local tiếp tục bị chặn; không thêm dữ liệu
  văn phòng hoặc benchmark thay cho thành viên.
- Chuẩn hóa ba tài liệu mục tiêu/kiến trúc/vận hành. Bỏ cam kết không ảo giác,
  bảo mật tuyệt đối hoặc 16 GB chạy mượt khi chưa có số đo. Đưa use case, hợp đồng
  bằng chứng, rubric/metric đề xuất và lộ trình nâng cấp vào đặc tả QA.
- `ui/README.md` ghi đúng giới hạn UI mẫu và schema API hiện tại. Không đổi
  `ui/app.py` hoặc triển khai issue của thành viên. UI chính vẫn là `static/`.

## Ma trận 12 issue tại lần đối chiếu

| Issue | GitHub | Bằng chứng/điểm còn phải kiểm |
| --- | --- | --- |
| #18 UI placeholder | CLOSED, PR #33 merged | Có `ui/README.md`, `ui/app.py`, `docs/chat-mockup.md`; mockup chưa chứng nhận kết nối thật |
| #19 Hiển thị nguồn | CLOSED, PR #34 merged | Đã đọc đặc tả; bổ sung nhãn mockup và giới hạn score/schema. Thiết kế chưa chứng minh tích hợp chạy thật |
| #20 Partial office lookup | OPEN | Chưa có entrypoint lookup văn phòng ở main; cần chốt đường gọi và 2 ca hồi quy trước sửa |
| #21 Help | OPEN | Help phải phản ánh chức năng thực; không chỉ CSV văn phòng và không gọi LLM nếu không cần |
| #22 Thêm 6 văn phòng | OPEN | Chỉ nguồn công khai có đối chiếu; kiểm CSV/2 ca lookup và phạm vi Git đúng file |
| #23 Nguồn/người phụ trách | OPEN | `data/README.md`: nguồn, vai trò chịu trách nhiệm, ngày và tần suất cập nhật; không chép thông tin private |
| #24 AI interface | OPEN | Mô tả interface tương thích runtime đang có, input/output/error/fallback; không viết lại LLM service |
| #25 AI hay không | OPEN | Đủ 10+10 câu và lý do; phân biệt lookup xác định với tổng hợp có nguồn |
| #26 15 benchmark cases | OPEN | ≥15 ca, ≥3 edge; expected độc lập từ nguồn, có out-of-scope; CSV demo không thay bộ QA học thuật |
| #27 Benchmark runner | OPEN | Chạy đúng entrypoint theo README, báo từng ca/accuracy và exit code thất bại; không so expected với bản sao expected |
| #28 Secrets | CLOSED, PR #30 merged | `.env.example`, ignore và quy tắc Secrets có trong repo |
| #29 Review bảo mật | CLOSED, PR #31 merged | PR chỉ có log; báo cáo local được đưa vào gói YC217, cần xuất bản qua PR sau review |

Hiện 4/12 issue đã đóng, 8 còn mở. PR #34 được owner merge trong lúc rà soát,
CI Windows/Mac/history SUCCESS; đã fast-forward và giữ thay đổi local.
Không sửa trạng thái issue/PR, không nhận làm việc đang giao và không gửi bình luận
thay thành viên. Trạng thái live có thể thay đổi sau thời điểm báo cáo.

## Các lỗi tích hợp cần chủ trì xử lý

1. `ui/app.py` gửi `answer_mode="standard"`; `ChatRequest` không cho phép giá trị
   đó. Đã kiểm schema: request này bị từ chối; `answer_mode="quick"` và
   `processing_level="standard"` là hai trường khác nhau. Dropdown mẫu chưa truyền
   lựa chọn vào lời gọi. Chủ trì UI cần sửa và kiểm trên backend thật.
2. `docs/ui-sources.md` vừa merge có mockup chứa số trang/quy định chưa xác minh,
   điểm retrieval hiển thị như phần trăm tin cậy và trường OCR không bảo đảm tồn
   tại. Đã bổ sung nhãn phân biệt minh họa/thiết kế với hợp đồng và bằng chứng thật.
   UI mẫu dùng tên trường citation và định dạng stream mô tả chưa khớp hoàn toàn
   với backend. Phải đối chiếu `app/server.py`, `app/rag_agent.py`, OpenAPI và UI
   `static/app.js`; không đổi backend theo mockup khi chưa thống nhất hợp đồng.
3. UI mẫu fallback citation giả khi mất kết nối. Nhãn mô phỏng phải rõ; khi trình
   bày sản phẩm thật, lỗi model/mạng phải là trạng thái lỗi, không có nguồn giả.
4. Bộ portable có test dùng DB riêng mặc định và ID/đường dẫn cố định. Baseline
   có một lỗi do đường dẫn nguồn cũ; clone không có DB lại skip ca đó. Đã sửa
   **test harness** bằng SQLite trong bộ nhớ và nguồn tổng hợp tạm, kiểm trường
   hợp hợp lệ rồi kiểm từ chối document thiếu/page excluded/page pending/slice sai/
   quote ngắn/bounds sai/nguồn mất. Không sửa validator hoặc dữ liệu thật.
5. Các yêu cầu Lab 2 còn thiếu điểm nối backend→CSV→eval. Cần chủ trì chốt hàm/
   entrypoint dùng chung trước #20/#22/#27; không để mỗi component dựng một bot riêng.

## Quy trình được đề xuất áp dụng

Nhận issue và chốt expected → đọc hợp đồng/log → triển khai/test đúng phạm vi →
kiểm đầu ra có trong Git và cập nhật docs/log → reviewer khác tác giả → checks trên
head sau cùng → owner merge → smoke main → đóng tiêu chí tích hợp.

Giữ một writer cho phần chung/corpus. Mỗi gói Antigravity tạo chat mới sau khi
lượt cũ terminal/trả quyền; [rule project](ANTIGRAVITY_PROJECT_RULE.md) dẫn tới
các tài liệu chuẩn thay vì sao chép hồ sơ. [Quyền merge](QUYEN_MERGE_VA_BRANCH.md)
đã cấu hình/readback; chưa bật tự xóa nhánh và chưa xóa nhánh nào.

## Cổng rà soát sau khi đủ 12 issue

Chỉ chạy/đánh dấu cổng này trên main chứa đầy đủ PR được review; chưa đạt hiện tại.

1. Đọc lại #18–29, PR/diff và artifact trong clone sạch; issue CLOSED không thay
   kiểm file thực. Đối chiếu lại ma trận với commit main cuối và DoD.
2. Kiểm môi trường Python 3.12 mới, dependency và demo theo hai README nền tảng.
   Chạy portable suite/Node/CI Windows+Mac; chạy test module mới và benchmark
   `eval/run.py` theo hợp đồng của thành viên. Ghi riêng skip/NOT_RUN.
3. Kiểm xuyên luồng UI→API→nguồn→trả lời/citation; CSV partial/help không phá QA.
   Kiểm lỗi schema, mạng/model, 409, session mới, giữ nháp, link tài liệu/trang.
4. Chạy QA model thật trên nguồn công khai/tổng hợp: nội dung/điều kiện/citation,
   từ chối và câu nối tiếp theo QA-01–08. Chấm bằng rubric và người review, lưu
   cấu hình/phiên bản/lỗi từng ca; không dùng accuracy CSV làm chất lượng QA.
5. Smoke tra cứu, QA, ôn tập và lịch ảnh sau sửa chung; Windows full installer,
   OCR/VLM thật, mobile/IME chỉ PASS khi đã thao tác thực.
6. Quét Git history và cây file công khai, kiểm ignore, link, template, DoD và
   attribution. Đối chiếu ruleset/protection còn đúng, không tự merge thử để kiểm.
7. Tổng hợp lỗi theo mức độ, sửa và chạy lại phần ảnh hưởng. TV6 lập kết luận,
   owner quyết định tích hợp. Các nâng cấp P1/P2 ở đặc tả vẫn là đề xuất đến khi
   có đơn vị triển khai và kết quả nghiệm thu riêng.
8. Sau cổng phần mềm, tiếp tục kiểm định dữ liệu từ checkpoint local; không suy
   quyền học/promote từ CI xanh. Không công bố hồ sơ/dữ liệu huấn luyện lên GitHub.

Không tạo automation để đợi đủ issue trong lượt này. Cổng sau 12 issue còn
**PENDING_MEMBER_WORK**; người dùng có thể yêu cầu chạy lại khi nhóm hoàn tất.

## Kiểm chứng trong YC217

- Portable Python: baseline **525 PASS, 1 FAIL, 12 SKIP**; sau sửa harness
  **526 PASS, 12 SKIP**. Chạy trên Mac/Python 3.14.8 hiện có; đây không phải
  kiểm lại môi trường CI Python 3.12 hoặc Windows native.
- Node UI: **27 PASS** bằng Node runtime có sẵn; không thêm dependency sản phẩm.
- `pip check`: PASS. Gitleaks history và cây file công khai: không phát hiện bí mật
  theo rule công cụ; chi tiết phạm vi ở [báo cáo bảo mật](../security-check.md).
- Git boundary: 23 ca PASS. Liên kết nội bộ: 85 đường dẫn PASS. Lịch sử: 60 mục
  main được giữ nguyên, thêm YC217 thành 61 mục duy nhất; diff whitespace PASS.
  Rule: cấu trúc hợp lệ, 3.270 ký tự; tự nạp
  trong chat mới NOT_RUN. API schema: `standard` trong answer_mode bị từ chối,
  `quick` + processing_level `standard` được nhận.
- Bảo toàn ID/lịch sử và diff cuối được kiểm với bản main mới; không nhận CI của
  PR #34 là CI cho thay đổi YC217 chưa commit.
- CI mới/clone sạch Windows/full private suite/model QA thật/Streamlit thật:
  **NOT_RUN** trong lượt tài liệu. Không commit/push/merge, không thay model hoặc
  DB phục vụ; việc thành viên và tiến độ dữ liệu không được coi là đã hoàn tất.
