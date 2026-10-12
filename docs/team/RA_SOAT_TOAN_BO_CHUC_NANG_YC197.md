# Tái kiểm chức năng và kết quả huấn luyện YC-197

Cập nhật ngày 12/10/2026 (Asia/Ho_Chi_Minh). Codex kiểm thử và review; Antigravity sửa mã sản phẩm/huấn luyện theo quyền ghi riêng. Baseline local `main` tại `af4933a44706aaa46662c67450dcbfd6535d527c`.

## Kết quả thực thi và chất lượng

Lượt `run-20261011-172153-7db8ea` hoàn thành 264/264 bước lúc 00:47:33, trong 1.519 giây, return code 0. Dữ liệu v28 giữ nguyên 396 mẫu của v27: 264 train, 132 valid, 11 nhiệm vụ, 6 môn, 256–892 token; chuỗi học tối đa 1.024 token. Model Qwen2.5-3B-Instruct 4-bit đúng revision đã khóa. Không kích hoạt adapter.

Validation loss đầu 1,158, bước40 0,387, cuối 0,614; loss tăng về cuối không chứng minh chất lượng tăng. MLX đỉnh 3,391 GiB, RAM khả dụng thấp nhất 1,72 GiB, swap tăng 353,44 MiB; monitor không abort. Dữ liệu/binding sau học và cấu trúc adapter hữu hạn đạt. Checkpoint40/80 giống từng byte với trial01, xác nhận đối chứng cùng dữ liệu/cấu hình đầu. Adapter cuối SHA256 `e947add0693d834c9b6d8c7bbdb49d4d65b339d717680ae0f8f39b952ced3634`.

| Đối chứng thủ công trên đầu ra thật | Base đạt/một phần/không đạt | Trial02 đạt/một phần/không đạt | Kết luận |
|---|---:|---:|---|
| 18 ca phát triển đã quan sát từ trial01 | 0/4/14 | 1/2/15 | Chưa đạt; không dùng làm nghiệm thu tổng quát hóa |
| 18 ca mới từ 6 PDF khác hash | 0/5/13 | 2/2/14 | Chưa đạt; QA mục tiêu2/3, auditor0/6 |

Bộ mới đóng băng sau khi trial02 bắt đầu nhưng trước xem đầu ra candidate. Sáu nhóm nguồn nội dung tách khỏi train/valid và bộ cũ; chưa chứng minh tách toàn họ nguồn. Chỉ 3 loại nhiệm vụ QA/auditor, chưa đủ để nghiệm thu cả 11 loại. Metadata protocol kế thừa từ bộ cũ đã được hiệu chỉnh đường dẫn/ngày và `frozen_before_training:false`; ngưỡng chấp nhận giữ nguyên.

Hai câu mới đạt đầy đủ là định nghĩa ma trận con và tâm lý học. Model trả sai constructor có trả `void`; định nghĩa học tập bị thu hẹp thành ghi nhớ; tóm ý Guthrie mất mức độ chắc chắn. Cả 12 auditor mới không giữ quote liên tục nguyên văn, và 3/6 nhận định âm bị chứng nhận sai. Auditor bộ cũ sửa được verdict âm6/6 nhưng quote vẫn sai. Việc học hết một lượt dữ liệu không đủ chữa lỗi. Ngưỡng mỗi nhóm tối thiểu80%, tăng5điểm phần trăm, không hồi quy vẫn chưa đạt. Base không có retention đã đạt nên không thể dùng số hồi quy0 để kết luận bảo toàn năng lực.

## Bao phủ chức năng

| Phần | Bằng chứng hiện tại | Trạng thái và giới hạn |
|---|---|---|
| Tra cứu tài liệu/locator/PDF, provenance và admission | Kiểm root toàn repo, public PDF29tài liệu/95chunk, selected doc20/trang2 | Hợp đồng đạt; không thay PDF/corpus thật |
| QA, hội thoại, tóm tắt, history guard, streaming/API | Kiểm root/snapshot, Node, chọn nguồn→QA và nhập tiếng Việt trên UI | Hợp đồng đạt; đầu ra model có lỗi nội dung như bảng trên |
| Sinh câu hỏi, kiểm câu hỏi, giải và kiểm lời giải | Kiểm feature/public nguồn có đáp án/chưa có đáp án/lý thuyết, kiểm runtime binding396mẫu | Luồng/code đạt trong test; không suy chất lượng mọi câu sinh từ unit test |
| Trích xuất bài tập nguyên văn | 4 ca RED→GREEN root+snapshot; tìm UI doc20/trang2 trả1đoạn, chuyển nguồn sang QA | Đã sửa lỗi SQL Unicode; giữ source/page/chunk/PDF locator |
| Lịch thủ công/xuất ICS | UI thêm lịch thử trên DB riêng, xuất ICS đúng RRULE/WEEKLY/TZID | Đạt; OCR ảnh thật/VLM không được tái nghiệm thu trong lượt LoRA này |
| Bảo mật nguồn/prompt, ngân sách, GPU và training gates | Snapshot boundary/security, full root, kiểm dataset/model/token/preflight/monitor | Đạt hợp đồng kiểm thử; không đảm bảo model luôn giữ mọi chỉ dẫn |
| Ngữ cảnh 8K/16K/32K | Đo MLX8K/16K cùng template trước học | 8K/16K3/3 tìm thông tin tổng hợp;32K chưa đo; mặc định8K |
| Responsive/IME | DOM và screenshot390px tìm lỗi form; Node composition guards | Bố cục320/390/768/1280pxđã kiểm từngcontrolđạt; IME hệ điều hành thật chưa kiểm |
| Windows | Suite portability trên Python3.12 local, README/setup/CI đã có | Chưa chạy lại cài full model trên máy Windows sạch |

## Kiểm thử và hai lỗi phát hiện

Root sau sửa bài tập: **659 passed,12 skipped** trong71giây; ca runtime binding bị skip vì lượt này chưa đặt biến chọn dataset, được chạy riêng sau đó **17 passed** với v28 thật. Snapshot feature: **398 passed,1 skipped**; runtime binding riêng **17 passed** giải quyết cùng ca skip. Các skip khác liên quan hồ sơ/candidate riêng chưa được chọn, không được tính là PASS. Node **20 passed** sau sửa CSS (20/20 trước sửa cũng đạt).

Baseline full suite có hai lỗi harness: fixture final answer thiếu citation, oracle test phụ thuộc tệp tạm không còn. Đã sửa fixture có citation theo metadata và oracle tính tay độc lập. Các lỗi đọc tiến trình/bộ nhớ trong sandbox được phân biệt bằng lượt host có quyền đọc; không thay ngưỡng hoặc bỏ gate. Test runtime binding hỗ trợ manifest release được duyệt và đối chiếu toàn396mẫu/11nhiệm vụ với serialized train/valid thực.

Lỗi1: `LOWER` của SQLite không chuyển chữ hoa tiếng Việt có dấu, `LIKE` với dấu cách ASCII bỏ NBSP/tab. Antigravity sửa bộ lọc trong `app/practice.py` root và snapshot: predicate Unicode/casefold trước limit2000dòng khớp. Codex review diff và test thêm hơn2000dòng không phải bài trước dòng cần tìm; source trả lại nguyên văn, locator đúng. Có backup để phục hồi. Không chép toàn file snapshot đè root.

Lỗi2: grid form nâng cao yêu cầu180+90+90px cùng gaps, ô Đến trang vượt viewport390px (right409). `scrollWidth` bằng390 không phát hiện được vì overflow bị ẩn. Đã sửa7dòng CSSsnapshot, Codex review diff và kiểm thật cả5control ở320/390/768/1280px đều nằm trong màn hình; click tìm bài ở390px trả1đoạn đúng. Lượt đọc CSS cũ do cache được giữ làm bằng chứng chưa đạt; kiểm cuối dùng origin thử mới8772/CSShash846e3e… và ảnh exercise-mobile-fixed.jpg. Không thayCSS/rootUIđang phục vụ. Khi tích hợp/phát hành cần đổi phiên bản asset để tránh cache cũ.

## Quy trình, dữ liệu và giới hạn

Chu trình phân tích → test → triển khai → review → chẩn đoán → sửa → kiểm lại áp dụng. Git commit/push/triển khai: N/A vì chưa yêu cầu. Không đổi provider/model, không sửa PDF hoặc corpus chính, không chạy writer/GPU cạnh tranh. Snapshot UI/prompt vẫn là bản thử; root mới tích hợp validator/sanitizer và sửa bài tập. Bản code freeze02 từng dùng serialize/huấn luyện được giữ nguyên, CSS sau đó không được gán ngược thành runtime đã duyệt của dataset.

Hồ sơ raw/đầu ra/chấm theo ca/hash nằm local dưới `data/evaluation/full-function-yc197/` và `data/evaluation/reasoning-security-yc195/holdout-runtime-v1/`, `holdout-fresh-v2/`. Các tệp dữ liệu/model/adapter không đưa vào Git. Kiểm bảo toàn sau lượt xác nhận DB corpus, DB lịch và toàn model nền giữ hash với mốc YC193. DB hội thoại khác hash so với mốc cũ, chưa đủ bằng chứng quy nguyên nhân; UI thử dùng DB riêng và không dùng hội thoại người dùng làm nhãn học. Không ghi nội dung hội thoại vào hồ sơ chung.

Không tự chạy một trial03 cùng cấu hình để lặp lại lỗi. Lượt mới cần chẩn đoán được kiểm bằng probe, đóng băng lại runtime/data và đánh giá có nguồn trước/sau. Chất lượng hiện tại vẫn là `REJECTED_QUALITY / DO_NOT_PROMOTE`.

## Probe đủ 11 loại lời gọi

Cả base và adapter đã chạy MLX thật cùng11đầu vào/decoding trên một đoạn Đại số ở splitvalid v28. Đây là chẩn đoán đủ loại lời gọi, không phải heldout độc lập vì validationloss đã xem nguồn này. Candidate chạy106,24giây/11stop. Codex đọc toàn bộ11đầu ra mỗi bên: base3đạt/3mộtphần/5khôngđạt; candidate3đạt/1mộtphần/7khôngđạt. Candidate cải thiện lời giải nhưng hồi quy kiểm lời giải dương (`verified:false` thaytrue), làm sai điều kiện không đồng thời bằng0, sinh câu hỏi tiếng Anh và quote vẫn không nguyên văn. Không tính điểm này như chất lượng11nhiệm vụ trên toàncorpus. Bằng chứng local `runtime-11-task-probe/diagnostic-decision.json` và chấm từng ca.

Kiểm đầu bị cổng RAM từ chối tại3,39GiB; đóng máy chủ/tab thử riêng rồi đo3,73GiB mới chạy. Giữ nguyên ngưỡng3,5GiB và log từ chối, không tắt cổng. Bản code đã review sau sửa được lưu local ở `data/evaluation/full-function-yc197/runtime-reviewed/` cùng manifest hash; bản freeze dùng huấn luyện không bị ghi đè.
