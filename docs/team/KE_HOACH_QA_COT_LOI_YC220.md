# Kế hoạch cải tiến QA học tập — YC220

Mục tiêu: người học đặt câu hỏi trong tài liệu, nhận câu trả lời giữ đủ điều kiện,
biết ý nào được gắn với nguồn nào, và nhận hướng xử lý khi thiếu căn cứ.
Đây là kế hoạch theo đầu ra, không thay issue đã giao hoặc coi toàn bộ 12 issue đã xong.

| Ưu tiên | Đầu ra / phạm vi | Chủ trì và phụ thuộc | Điều kiện nghiệm thu |
| --- | --- | --- | --- |
| P0 — lượt này | Bộ ca QA có nguồn và expected cố định; chạy mô hình thật trước/sau; tách truy xuất, nội dung, citation, từ chối | Codex kiểm định; dùng trọng số base hiện có, không huấn luyện | Lưu nguồn/hash, cấu hình, câu trả lời, latency và rubric từng ca; lỗi sinh nội dung không được che bằng điểm từ khóa |
| P1 — lượt này | Hỏi làm rõ trung tính khi thiếu chủ thể; giữ thuật ngữ ngắn hợp lệ; ngữ cảnh có giới hạn và lựa chọn tách chủ đề | Antigravity triển khai trong snapshot, Codex review | RED→GREEN; không truy xuất/sinh câu trả lời khi thiếu chủ thể; không lấy câu trả lời cũ làm chứng cứ |
| P1 — lượt này | Metadata không gọi mô hình đúng thực tế, kể cả thiếu nguồn/lỗi retrieval | Cùng gói QA; không thay bộ phân loại của thành viên | Số call thực khớp sync/stream; lý do/phạm vi/hướng xử lý không mất |
| P1 — lượt này | Đối chiếu các ý mà mô hình dẫn nguồn; hiển thị lại sau tải lịch sử/retry | Tái dùng citations JSON, Web UI và adapter Streamlit | Không lộ reasoning, không dựng HTML từ nguồn; mapping không được gọi là xác minh ngữ nghĩa; giữ gate citation/auditor |
| P2 — sau issue thành viên | Nghiệm thu trên main hợp nhất đủ 12 issue, đối chiếu benchmark nhóm và luồng tra cứu→QA→luyện tập | Chủ trì chức năng + TV kiểm thử; giữ #20/#22/#24/#25/#26/#27 | Kiểm CI đa nền tảng, ca tích hợp và model thật; owner review/merge theo ruleset đang có |
| P2 — pilot học tập | Tập câu hỏi từ học phần thật do giảng viên duyệt, holdout theo nguồn; đo câu trả lời và thao tác đối chiếu với tìm thủ công | Người phụ trách môn học chốt expected; corpus phải qua cổng nguồn | Recall@6 ≥90%, đúng nội dung/đủ điều kiện ≥85%, citation hỗ trợ ý ≥95%, từ chối đúng ≥90%, nguồn bịa=0; đo cả từ chối sai. Đây là mục tiêu đề xuất cho pilot, không phải thành tích hiện tại |
| P2 — trải nghiệm local | Đo thời gian truy xuất, tải model, token đầu và tổng thời gian; tối ưu phần chiếm nhiều nhất trước | Chủ trì runtime; sau bộ QA để phát hiện hồi quy | So sánh cùng câu hỏi/nguồn/model và cả lượt nguội/ấm; không đánh đổi nội dung/cổng nguồn để giảm latency |
| P3 — dùng trong tổ chức | Identity, phân quyền/tách dữ liệu, tải nhiều người, backup/restore và giám sát | Chốt triển khai nhiều người trước thiết kế | Ca cách ly người dùng/khôi phục; đo p50/p95 và tài nguyên để chốt SLO; chưa hứa ngưỡng khi chưa có phép đo |

Giới hạn lượt này: bộ nguồn tổng hợp công khai do người kiểm thử biên soạn dùng để
kiểm phần mềm với mô hình thật. Nó không thay tập học phần được giảng viên duyệt,
không là chứng nhận toàn corpus, hiệu quả học tập hay triển khai doanh nghiệp.
Mô hình và adapter đang dùng, cửa sổ 8192 và các cổng nguồn/chất lượng giữ nguyên.
Không tự commit/push/merge, đổi quyền GitHub hoặc xử lý thay issue thành viên.

## Các mốc bàn giao

1. **QA cốt lõi:** bàn giao mã/test, báo cáo trước–sau và ca API→lịch sử bằng
   mô hình thật. TV2/TV5 review phần QA/UI; TV6 đối chiếu bằng chứng; owner quyết
   định phát hành. Không tự sửa issue của các vai trò này.
2. **Tích hợp nhóm:** sau khi chủ trì hoàn thành các issue còn lại, TV4 cập nhật
   main; TV6 chạy lại CI, tra cứu, hỏi đáp, trợ giúp, luyện tập và thời khóa biểu.
   Lỗi chung lập gói sửa riêng, không lấy việc đóng issue làm bằng chứng nghiệm thu.
3. **Pilot một học phần:** TV1/TV4 phụ trách nguồn và phiên bản; giảng viên chốt
   câu hỏi/đáp án/rubric trước đo; TV6 giữ bộ kiểm riêng theo nguồn. Ghi cả câu
   trả lời sai, từ chối sai và nguồn không hỗ trợ ý. Đo người học tìm câu trả lời,
   mở đúng trang và tự kiểm điều kiện áp dụng so với cách tìm thủ công.
4. **Thử dùng chung:** chỉ sau pilot đạt mới chốt mô hình triển khai, quyền nguồn/
   phiên và mục tiêu tải. TV4/TV5 phụ trách runtime; TV6 kiểm truy cập chéo và phục
   hồi. Identity, backup và giới hạn tải là điều kiện mở dịch vụ cho nhiều người.

Luồng demo nghiệm thu: chọn tài liệu → hỏi công thức và điều kiện → đối chiếu ý
với trang nguồn → hỏi nối tiếp → tách ngữ cảnh → thử câu ngoài phạm vi để xem
hướng xử lý. Sau đó mở phần luyện tập hiện có; các nâng cấp luyện tập vẫn theo
phân công. Mỗi bước phải chạy trên nguồn học phần đã duyệt trước khi gọi là pilot.

## Theo dõi

- Baseline bất biến và snapshot triển khai tách khỏi cây đang có thay đổi.
- 8 ca RED đã tái hiện: hỏi mơ hồ, thuật ngữ ngắn, nối tiếp/tách ngữ cảnh,
  số lượt sinh khi không có nguồn/lỗi tìm kiếm, và đối chiếu ý với citation.
- Kết quả sau sửa và kiểm định thật: đang thực hiện; chưa nghiệm thu.
