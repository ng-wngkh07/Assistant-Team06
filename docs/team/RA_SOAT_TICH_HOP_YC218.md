# Rà soát tích hợp các issue Lab 2 và PR #35

Ngày 16/10/2026; YC-218. Baseline main `e91e03a`, PR #35 `7601045`.
Phạm vi: sửa tồn đọng của #18, #19, #23, #28, #29 đã merge và hoàn thiện
phần help #21 đang chờ duyệt. QA học tập là chức năng chính; tra cứu văn phòng
là ví dụ demo. Các việc #20, #22, #24–27 vẫn giao cho thành viên phụ trách.

| Vấn đề đã đối chiếu | Thay đổi | Cách kiểm chứng |
| --- | --- | --- |
| PR #35 xóa log #23 và không có log mới | Giữ đủ 61 mục main, thêm LOG của TV2 và YC-218; sửa bộ đếm | Validator lịch sử, kiểm ID duy nhất |
| Adapter gửi answer_mode ngoài schema | Dùng quick/steps/compare và mức xử lý hợp lệ | Payload qua ChatRequest và gọi help API thực bằng TestClient |
| Mất mạng sinh câu trả lời/nguồn giả | Trạng thái lỗi riêng; không thêm assistant hoặc session giả | HTTP 409/422/5xx, timeout, JSON sai và lượt xử lý lỗi |
| Thử lại có nguy cơ đổi ngữ cảnh/ghi trùng | Giữ request ID, history, modes và session của lượt chờ | Thao tác widget Streamlit và replay API/history store |
| Hội thoại dài vượt giới hạn backend | Bounded client history: 32 tin, 4000 ký tự/tin, 32000 bytes; giữ nguyên feed | AppTest với nhiều lượt, câu trả lời dài và Unicode |
| Hủy để lại câu hỏi lỗi trong ngữ cảnh | Bỏ đúng câu hỏi đang chờ; giữ lượt hoàn tất và session | Streamlit AppTest |
| Hiển thị nguồn sai origin PDF hoặc biến điểm thành độ tin cậy | URL PDF chỉ chấp nhận ID/trang dương, dùng origin backend; score là số xếp hạng | Ca URL độc hại, null, trang 0 và cảnh báo OCR |
| Nội dung nguồn bị sửa khi render | Plain text giữ nguyên title/ID/snippet; chỉ PDF là action do ứng dụng tạo | Chuỗi Markdown/autolink trong nguồn |
| Help dùng luồng xác định nhưng metadata ghi đã gọi mô hình | generation_calls = 0; không retrieval/translation/model | Sentinel chặn mọi truy cập các dịch vụ ngoài |
| Tài liệu #18/#19/#23 không khớp runtime | Tách UI chính và adapter; cập nhật SSE, cài phụ thuộc, cache/lock/schema/đường dẫn | Đối chiếu owning source và liên kết local |
| #29 ghi có báo cáo nhưng file chưa vào Git; DoD được viện dẫn nhưng thiếu | Bổ sung báo cáo và ngoại lệ chính xác cho DoD | Ranh giới Git và Gitleaks |
| Một test dữ liệu cần DB cá nhân | Fixture SQLite/nguồn tổng hợp, có control hợp lệ và ca fail closed | Suite portable không phụ thuộc corpus riêng |

## Kiểm chứng

Codex kiểm độc lập trên macOS/Python 3.14.8 sau khi bên triển khai trả quyền ghi:

- `python -m pytest -c pytest.windows.ini -q -rs`: **578 PASS, 12 SKIP**.
  Các ca skip yêu cầu dataset/report/runtime riêng, không phải lỗi được bỏ qua để merge.
- `python -m pytest tests/test_streamlit_flow.py -q`: **5 PASS**, Streamlit 1.66.0
  trong môi trường riêng. Mock tại HTTP; widget và session state thực thi thật.
- Bốn tập Node Web UI: **27 PASS**; `static/` không bị thay đổi trong bản sửa này.
- Demo: **29/29 tài liệu tổng hợp**, 95 trang/chunk, không embedding/OCR/model thật.
- Kiểm môi trường/FTS/PDF, pip check, validator lịch sử và diff check: PASS.
- Parse Python: **204 file PASS**; 61 ID lịch sử main được giữ, 63 ID duy nhất sau sửa.
- **25 ca ranh giới Git PASS**; Gitleaks history/all refs (52 commit có diff) và
  cây 313 file công khai không phát hiện secret theo rule của công cụ.
- Liên kết local trong tài liệu đã sửa: không có đường dẫn thiếu.

CI Python 3.12 Windows/macOS và job UI Ubuntu được đối chiếu trên
[PR #35](https://github.com/ng-wngkh07/Assistant-Team06/pull/35) sau push.
Log chi tiết giữ local; không đưa dữ liệu cá nhân hoặc nội dung corpus riêng vào PR.
Bộ môi trường local thiếu model mặc định cho profile demo; lệnh `check` không dùng
`--require-model` nên PASS ở đây chỉ xác nhận môi trường portable, không phải QA readiness.

## Ranh giới nghiệm thu

CI/mock không chứng minh chất lượng câu trả lời từ mô hình thật. Chưa nghiệm thu
QA với bộ benchmark #26/#27 hoặc bộ cài đầy đủ trên máy Windows mới. Không chạy
huấn luyện, không kích hoạt adapter, không sửa PDF nguồn. Chủ repo review và merge;
không giảm protection để xử lý thông báo merge bị chặn của collaborator.

Sau merge cần cập nhật main và chạy kiểm tra tích hợp trên revision đã merge trước
khi ghi hoàn tất trên main. Chưa coi 12 issue đã xong khi còn các issue mở.
