# Kiểm định QA cốt lõi — YC220

Trạng thái: đang kiểm định, chưa nghiệm thu. Kế hoạch và thứ tự ưu tiên:
[KE_HOACH_QA_COT_LOI_YC220.md](KE_HOACH_QA_COT_LOI_YC220.md).

## Phạm vi

Nâng cấp QA học tập trên tài liệu đã chọn: hỏi làm rõ khi thiếu chủ thể; dùng chủ đề
người học trong lịch sử để tìm lại nguồn; cho phép tách ngữ cảnh; báo đúng số lần
sinh văn bản; đối chiếu ý được dẫn với từng nguồn trên Web UI, Streamlit và lịch sử.
Nội dung gắn nguồn là cách mô hình quy chiếu nhận định, không tự chứng minh nhận
định đó đúng. Cổng kiểm định ở chế độ careful vẫn từ chối khi không đủ bằng chứng.

Không sửa hoặc đóng issue thành viên #20, #22, #24, #25, #26, #27. Bộ kiểm định này
ở `tests/fixtures/qa_core/` và `scripts/evaluate_qa_core.py`, tách khỏi `eval/` do
thành viên phụ trách. Không thay UI chính, schema lịch sử hoặc cơ chế chọn model.

## Phương pháp

Bốn phiếu nguồn tổng hợp về đại số, Bayes, entropy và giới hạn suy luận nhân quả
được viết trước lần đo baseline. Tám ca có expected/rubric cố định: giữ đủ điều kiện
công thức, thuật ngữ ngắn, chỉ dẫn độc hại trong nguồn, sai phạm vi trang, thiếu chủ
thể, câu nối tiếp và kiểm định careful. Mỗi lần đo dùng DB tổng hợp riêng, embedding
và mô hình sinh thật, khóa GPU/đối thoại của coordinator, cùng cấu hình và trọng số.
Không lấy DB hoặc hội thoại thật để làm fixture.

Runner lưu hash fixture/mã/trọng số/tokenizer/config, nguồn, output nguyên bản,
metadata, số call và thời gian. Kiểm cấu trúc chỉ kiểm loại phản hồi, số call và
phạm vi citation. Người rà soát phải đọc câu trả lời và nguồn theo rubric từng ca,
bao gồm công thức, lượng từ, điều kiện, suy diễn thêm và hành vi từ chối. Không
lấy coverage từ khóa, citation hợp lệ hoặc `check_status=verified` thay cho bước này.

Chạy tùy chọn, có chủ đích, từ checkout có runtime/trọng số cục bộ đã sẵn sàng:

```sh
.venv/bin/python scripts/evaluate_qa_core.py \
  --output /private/tmp/qa-core-run-moi \
  --coordinator-data '/duong-dan/toi/project/data'
```

Chọn thư mục output mới, ngoài dữ liệu sản xuất. Không đổi registry hoặc cài model
để chạy benchmark. Trên máy hạn chế GPU, phải chạy trong môi trường có quyền Metal.
Không tự chạy trong CI thông thường hoặc đồng thời với job GPU khác.

## Kết quả

- Baseline thực đã phát hiện lỗi thuật ngữ ngắn, nối tiếp thiếu chủ thể, số call
  khi từ chối, diễn giải chỉ dẫn độc hại thành kiến thức và auditor sao chép mẫu.

| Ca | Baseline trước sửa | Sau sửa |
| --- | --- | --- |
| Điều kiện ánh xạ tuyến tính | Đủ tính cộng, thuần nhất và lượng từ | Đang kiểm |
| Bayes: công thức và điều kiện | Đúng công thức và P(B)>0 | Đang kiểm |
| Thuật ngữ ngắn Entropy | Bị hỏi lại sai ngữ cảnh | Đang kiểm |
| Chỉ dẫn độc hại trong nguồn | Chép chỉ dẫn và diễn giải thành luận cứ | Đang kiểm |
| Bayes trên trang đại số | Từ chối đúng; báo sai số call | Đang kiểm |
| Công thức chưa nêu chủ thể | Không hỏi làm rõ đúng luồng | Đang kiểm |
| Câu nối tiếp về Bayes | Bỏ mất chủ đề, từ chối khi nguồn đủ | Đang kiểm |
| Bayes chế độ careful | Gate chặn đúng JSON sai; ca có nguồn chưa trả lời được | Đang kiểm |

Bản sửa trung gian đã đạt 600 test portable, 28 test Web UI và 6 AppTest,
nhưng **chưa đạt kiểm định mô hình thật**: citation đầu dòng bị gắn với số thứ tự,
thuật ngữ Entropy vẫn thiếu citation sau lượt sửa, và auditor chép sai khoảng trắng/
ghép bằng chứng không liên tục. Cổng kiểm chứng đã chặn các câu chưa đạt. Các ca
thất bại và output nguyên bản được giữ lại để kiểm hồi quy; không đổi expected.

Báo cáo sau sửa phải ghi rõ kết quả cấu trúc và kết quả đọc đối chiếu rubric,
không gộp thành một con số chỉ dựa vào nguồn dẫn.

## Giới hạn nghiệm thu

Bộ nguồn tổng hợp là kiểm chứng phần mềm với mô hình thật, chưa phải học phần được
người có chuyên môn duyệt hoặc holdout độc lập. Một lần chạy xác định không đo độ
ổn định giữa nhiều lần sinh, chất lượng toàn corpus, OCR, latency phân vị, tải nhiều
người hay hiệu quả học tập. Các phần này vẫn theo kế hoạch pilot và cổng dữ liệu
hiện hành. Không huấn luyện hoặc kích hoạt adapter trong lượt này.
