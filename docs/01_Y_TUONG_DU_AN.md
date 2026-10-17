# Sản phẩm: QA học tập có nguồn

Cập nhật 16/10/2026 theo YC217. Tài liệu này thay mô tả mục tiêu cũ; lịch sử
thay đổi vẫn giữ trong Git và nhật ký. Đây là đặc tả và tiêu chí nghiệm thu,
không phải chứng nhận mọi tiêu chí đã đạt.

## Bài toán và người sử dụng

Sinh viên cần tìm và hiểu nội dung trong giáo trình/bài giảng đã được phép sử dụng.
Giảng viên cần kiểm được câu trả lời dựa vào đâu và nhận ra trường hợp hệ thống
chưa đủ căn cứ. Giá trị cần đo: tìm đúng bằng chứng, trả lời đúng phạm vi và giảm
thời gian đối chiếu nguồn so với cách mở tài liệu rồi tìm thủ công.

**QA** ở đây là Question Answering (hỏi đáp), khác với Quality Assurance (kiểm thử).
Sản phẩm chính là hỏi đáp học tập dựa trên kho tài liệu cục bộ. Tra cứu văn phòng
là ví dụ dữ liệu có cấu trúc của Lab 2; không đổi sản phẩm thành chatbot hành chính.
Không cần LLM cho tra cứu CSV, lệnh trợ giúp hoặc phép kiểm định dạng xác định.

## Hợp đồng chức năng

| ID | Tình huống | Kết quả cần quan sát | Bằng chứng nghiệm thu |
| --- | --- | --- | --- |
| QA-01 | Hỏi một khái niệm có trong tài liệu | Trả lời tiếng Việt, giữ giả thiết/điều kiện, dẫn đúng nguồn | Nhãn tham chiếu do người review đối chiếu trang gốc; kiểm câu trả lời thật |
| QA-02 | So sánh hai khái niệm/nhiều tài liệu | Nêu điểm chung/khác và phạm vi; gắn nguồn theo từng ý | Ca đa nguồn, hai nguồn đều thực sự hỗ trợ nhận định |
| QA-03 | Tài liệu không có câu trả lời | Nêu thiếu căn cứ/phạm vi đã tìm; không đoán | Câu ngoài phạm vi và câu gần chủ đề nhưng thiếu dữ kiện |
| QA-04 | Câu hỏi nối tiếp, chuyển chủ đề hoặc phiên mới | Dùng ngữ cảnh phù hợp; không kéo nguồn cũ thành bằng chứng mới | Ca hai phiên, đổi chủ đề và hết ngân sách ngữ cảnh |
| QA-05 | Chọn trích dẫn | Mở đúng tài liệu/trang hoặc đoạn, thấy được nội dung dùng trả lời | Đối chiếu link, tên, trang PDF và số trang in nếu khác |
| QA-06 | Thiếu model, backend bận, nguồn lỗi | Thông báo trạng thái có thể xử lý; giữ bản nháp và không dựng đáp án giả | Ca lỗi mạng/model/409 và thử lại |
| QA-07 | Câu hỏi/lịch sử/nguồn chứa chỉ dẫn độc hại | Không coi dữ liệu tham khảo là chỉ dẫn hệ thống | Ca prompt injection và giới hạn payload |
| QA-08 | Ôn tập từ nội dung đã chọn | Phân biệt bài tập có sẵn và câu sinh; đáp án có bằng chứng | Review câu hỏi, tính duy nhất đáp án và điều kiện nguồn |

Câu trả lời hợp lệ cần nội dung, trạng thái, nguồn được dùng và giới hạn khi có.
Các tên trường thực tế lấy từ `app/server.py`, `app/rag_agent.py` và OpenAPI
`/docs`; không tự đặt một schema mới trong UI mẫu rồi coi là API đã triển khai.
Hệ thống có truy xuất, điều phối ngữ cảnh và kiểm nguồn; không tuyên bố tự trị
hoặc tự hoàn thiện tri thức chỉ vì tên gọi agent.

## Đã có, đang thiếu và ngoài phạm vi

- Đã có mã cho FastAPI, UI web `static/`, FTS/vector, QA, quản lý phiên, trích dẫn,
  công cụ học và lịch ảnh. Có mã/test không chứng minh mọi trường hợp model thật đạt.
- `ui/` mới là bản mẫu Lab 2, chưa thay UI chính. CSV văn phòng và bộ 15 câu Lab 2
  chưa phải bằng chứng độ chính xác QA học thuật.
- Lịch ảnh là tiện ích phụ: người dùng phải xác nhận trước lưu/xuất. Giữ chức năng
  hiện có và kiểm hồi quy khi sửa phần chung; không mở rộng nó trước QA cốt lõi.
- Chưa chứng nhận không ảo giác, không rò rỉ tuyệt đối, chạy mượt trên mọi máy
  16 GB RAM, hay hiệu quả với toàn bộ môn học. Không dùng các câu này làm cam kết.
- Không tự bổ sung nội dung mờ/khuyết, sửa PDF gốc, bật adapter hoặc học từ lịch/
  hội thoại cá nhân. Fine-tuning không phải điều kiện để chứng minh MVP QA.

## Đo hiệu quả và trình bày với giảng viên

Nhóm cần chốt bộ nguồn công khai/tổng hợp, phiên bản và người chịu trách nhiệm.
Chia câu đánh giá theo nguồn, giữ tập kiểm tra riêng; người lập expected đối chiếu
trang gốc trước khi xem đầu ra. Tách đánh giá retrieval, trả lời và tính đúng của
citation. Mỗi ca ghi câu hỏi, nguồn/trang cần có, ý bắt buộc, điều kiện không được
bỏ, expected refusal và kết quả người review; không chấm QA chỉ bằng so chuỗi.

Các ngưỡng dưới đây là **đề xuất nghiệm thu MVP**, chưa có số đo đạt:

| Chỉ số | Ngưỡng đề xuất | Cách đo |
| --- | --- | --- |
| Recall@6 của retrieval | ≥ 90% ca có đáp án | Đo nguồn/trang vàng nằm trong 6 kết quả, tách ca đa nguồn |
| Đúng nội dung và giữ điều kiện | ≥ 85% ca có đáp án | Rubric người review, báo tử số/mẫu số và lỗi nghiêm trọng |
| Citation hỗ trợ đúng nhận định | ≥ 95% nhận định được trích | Mở trang gốc; link hợp lệ chưa đủ |
| Từ chối đúng khi thiếu căn cứ | ≥ 90% ca thiếu căn cứ | Báo cả từ chối nhầm trên ca có căn cứ |
| Nguồn/mã trích dẫn bịa | 0 ca trong tập nghiệm thu | Có một ca thì chưa đạt, không che bằng điểm trung bình |
| Độ trễ và tài nguyên | Đo trước khi chốt SLA | Ghi p50/p95, máy/model, cold/warm và số ca; chưa cam kết số giây |

Lab 2 yêu cầu ít nhất 15 câu, ≥3 ca biên. Sau Lab 2 nên mở rộng bộ QA tối thiểu
30 ca độc lập gồm một nguồn, nhiều nguồn, công thức, câu nối tiếp, thiếu căn cứ,
nguồn lỗi và injection. Số ca là đề xuất khởi đầu, không bảo đảm thống kê cho mọi
môn. Ghi seed/config/phiên bản nguồn/model và kết quả từng ca để chạy lại được.

Demo 5 bước: nêu vấn đề → mở nguồn và câu hỏi → xem câu trả lời/citation → thử
một câu thiếu căn cứ → xem báo cáo đánh giá và giới hạn. So sánh với tìm từ khóa
hoặc LLM không có truy xuất trên cùng câu hỏi trước khi tuyên bố có cải thiện.
Không dùng CSV văn phòng hoặc test mock để tuyên bố chất lượng QA thật.

## Thứ tự nâng cấp

1. **P0 — Hợp đồng và tích hợp:** hoàn tất issue của thành viên; sửa lệch schema UI,
   bảo đảm DoD/CI/đầu ra nằm trong Git; chạy lại trên main sau 12 issue.
2. **P1 — Chất lượng có thể đo:** benchmark QA có nhãn trang gốc, rubric citation/
   refusal, đánh giá model thật và phân tích lỗi theo retrieval/OCR/generation.
3. **P1 — Trải nghiệm bằng chứng:** hiển thị nguồn đã dùng, phạm vi tìm và trạng thái
   chưa kiểm chứng nhất quán; lỗi mạng/model không biến thành câu trả lời mô phỏng.
4. **P2 — Cải thiện theo lỗi đo được:** điều chỉnh chunk/retrieval hoặc câu trả lời
   khi có nhóm lỗi tái hiện; so A/B trên tập riêng, kiểm hồi quy kiến thức cũ.
5. **Sau các cổng:** chỉ cân nhắc fine-tuning khi nguồn/dataset/runtime/tài nguyên và
   đánh giá chất lượng đã đạt; không đổi model hoặc tăng context chỉ để demo đẹp.

Đây là lộ trình; YC217 chỉ cập nhật tài liệu/quy trình và test tính độc lập dữ liệu,
không nhận đã triển khai các nâng cấp P1/P2. Xem
[báo cáo rà soát](team/RA_SOAT_REPO_YC217.md).
