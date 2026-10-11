# Bộ tài liệu kiểm thử công khai YC-194

Bổ sung **27 tài liệu / 9 chủ đề**, 36 trang PDF, khoảng 0,76 MB. Cùng hai mẫu cũ, bản demo có 29 tài liệu. Số lượng được chọn để phủ tình huống và giữ repo nhẹ; không phải chứng nhận đủ dữ liệu để đo chất lượng mô hình.

Các tài liệu mới do dự án tự biên soạn, không lấy từ sách, PDF riêng hoặc dữ liệu huấn luyện. Nội dung mới được công bố theo CC0-1.0. Manifest `public_corpus_yc194.json` ghi tên, chủ đề, kiểu, số trang, dung lượng và SHA-256. Giấy phép của hai mẫu cũ không được thay đổi bởi mục này.

| Chủ đề | Nội dung kiểm thử |
| --- | --- |
| Đại số tuyến tính | Tuyến tính, phép cộng và nhân vô hướng |
| Vi tích phân | Đạo hàm, điều kiện và tốc độ thay đổi |
| Toán rời rạc | Tập hợp, phép giao và logic |
| Lập trình hướng đối tượng | Đóng gói, kế thừa, đa hình |
| Cấu trúc dữ liệu | Stack/LIFO và queue/FIFO |
| Vật lý đại cương | Vận tốc và chuyển động đều |
| Tâm lý học nhận thức | Trí nhớ làm việc và trí nhớ dài hạn |
| Lý thuyết trò chơi | Người chơi, chiến lược, kết quả |
| Thói quen và học tập | Tín hiệu, hành vi và phản hồi |

Mỗi chủ đề có ba kiểu:

- `*_theory.pdf`: lý thuyết một trang, không có đề bài.
- `*_worked.pdf`: lý thuyết trang 1, Bài 1 trang 2, đáp án Bài 1 trang 3.
- `*_unsolved.md`: lý thuyết và Bài 2; không có đáp án từ tài liệu.

## Chạy kiểm thử

Dùng quy trình demo của README: `python scripts/dev.py demo`, sau đó `python scripts/dev.py serve --profile demo`. Profile này dùng `data/team-demo` riêng. Không trỏ profile demo vào thư mục sách/DB/weights riêng. Trên máy có model khác, dùng hướng dẫn cài đặt và cấu hình đã có trong README; tài liệu mẫu không chứa model.

Kiểm tra checksum/cấu trúc: `python -m pytest -q tests/test_public_corpus_yc194.py`.

Các ca nghiệm thu:

1. Thư viện sau lập chỉ mục có đủ tài liệu mới và không đọc corpus riêng.
2. Hỏi cùng chủ đề bằng tóm tắt và giải thích: nội dung đúng, điều kiện giữ nguyên, cách trình bày và mục đích khác rõ. So sánh stack/queue hoặc hai loại trí nhớ phải có căn cứ cả hai phía; chỉ một khái niệm thì hỏi lại.
3. Luyện tập theo chủ đề phải tự chọn nguồn. Tài liệu lý thuyết không bị coi là đề bài. Bài 1 phải đi với đáp án trang 3 của cùng tài liệu, không phải Bài 2 hoặc tài liệu khác.
4. Bài 2 hiển thị thiếu đáp án nguồn. Nếu yêu cầu mô hình giải, phải phân biệt lời giải mô hình với đáp án có sẵn và ghi kết quả kiểm chứng.
5. Tạo câu hỏi từ lý thuyết: đáp án ẩn trước khi trả lời, chấm đúng/sai sau khi gửi, giải thích có căn cứ; ôn tập hoạt động ngay cả khi chưa có câu sai cũ.
6. Nguồn PDF chỉ mở đúng trang qua đường dẫn; Markdown có phần/trích đoạn, không có đường dẫn PDF giả và không tải ảnh xem trước trong hỏi đáp/tra cứu.
7. Thử chủ đề không có nguồn, câu hỏi thiếu điều kiện, model bận, JSON sai và câu có hai đáp án đúng: báo rõ hoặc loại câu, không chấp nhận bằng mọi giá.

Không dùng bộ mẫu công khai này để huấn luyện hoặc làm holdout đánh giá chất lượng. `training_allowed=false` và `quality_holdout=false` trong manifest là ranh giới dữ liệu; bài kiểm thử chức năng thành công không chứng minh độ đúng trên sách thật, tốc độ toàn corpus hoặc chất lượng adapter.
