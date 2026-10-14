# Rà soát giao diện và tiếp tục RAG — YC-206

Cập nhật 2026-10-14T21:39:10.580450+07:00. Main local đồng bộ `099c769` (PR13/14), bảo toàn thay đổi local và backup.

Đã sửa năm lỗi: thiếu `resumeSourceList` làm chat mới/mở phiên lỗi; tự chèn lại câu đã gửi sau lỗi; danh sách ôn tập không cập nhật ngay; hộp đồng bộ thư viện không đóng được sau lỗi; thiếu cảnh báo trang nguồn chưa được duyệt cạnh trích dẫn. Giao diện dùng asset `yc206-ui04`, bài tập `yc206-ui02`.

27 kiểm thử JavaScript và24kiểm thử consumer nguồn/lịch sử đạt; bộ Python portable sau tích hợp backend đạt496, bỏ qua12ca cần dữ liệu riêng. Kiểm thử UI dùng kho tổng hợp riêng, model/Vision/OCR mô phỏng; mô phỏng không chứng minh chất lượng mô hình. Chat mới và mở phiên đã lưu đã kiểm thêm trên ứng dụng đang chạy với dữ liệu thật.

| Luồng | Trạng thái | Bằng chứng/giới hạn |
| --- | --- | --- |
| Chat mới, mở phiên đã lưu, xóa bản nháp, giữ lịch sử | PASS | Giao diện thật localhost8000 và kho thử8776; lifecycle RED→GREEN |
| Gửi Enter, trạng thái chờ, khóa gửi/chat mới khi xử lý, giữ bản nháp tiếp theo | PASS | Browser + Node; IME/ShiftEnter được mô phỏng |
| Lỗi gửi, thử lại, sửa câu, chọn nguồn khác, không tự đưa câu cũ trở lại | PASS | Browser API/SSE thật; sinh câu trả lời mô phỏng |
| Tìm phiên, đổi tên, hủy đổi tên, tóm tắt, đóng tóm tắt, dùng lại câu cuối | PASS | Kho thử tách biệt; tóm tắt mô hình mô phỏng |
| Xóa hết lịch sử và xóa lịch | PASS | Xác nhận trên kho thử; công cụ có timeout sau confirm, tab mới đọc lại dữ liệu rỗng |
| Thu gọn/mở sidebar, chuyển QA/Công cụ/Lịch, mở/đóng thư viện | PASS | Browser thật |
| Chi tiết thư viện, mở/đóng bảng, trạng thái số tài liệu/trang/chunk | PASS | Một PDF tổng hợp3trang |
| Đồng bộ thư viện khi lỗi, đóng hộp lỗi và tiếp tục thao tác | PASS | Endpoint indexing thật trả lỗi thử; spinner dừng và nút đóng hoạt động |
| OCR chọn tài liệu/trang, nhận diện, xem/sửa/duyệt/hủy/refresh/đóng | PASS_UI_ONLY | OCR mô phỏng; cache/provenance/store/routes thật trong kho riêng, không đưa vào dữ liệu học |
| Tạo câu hỏi, validation chủ đề trống, chọn đáp án sai/đúng/chưa chắc, kết quả, đóng, nguồn→QA | PASS_UI_ONLY | Sinh câu/đáp án mô phỏng, UI thật |
| Ôn tập, lưu câu, cập nhật ngay danh sách, tự kiểm tra lại, xóa mục/xóa hết | PASS | Một lỗi stale-list được tái hiện và sửa; browser + Node |
| Bài tập mẫu, chỉ đáp án/giải chi tiết, nguồn trang, bộ lọc tài liệu/trang, không có bài | PASS_UI_ONLY | Bài mẫu mô phỏng, route/validation/source binding thật |
| Trích đoạn nâng cao, tìm từ khóa, tạo bài từ trích đoạn, dùng nguồn trong QA | PASS | FTS3trang thật, model mô phỏng |
| Lịch nhập tay/lưu/xóa, ảnh upload/xem trước/tải lại/bỏ nháp, thêm/xóa dòng nháp | PASS_UI_ONLY | Vision mô phỏng; tách ảnh/draft/store thật |
| Ánh xạ tiết→giờ, xác nhận/lưu lịch, xuất ICS | PASS | ICS thực có VEVENT, RRULE weekly và Asia/Ho_Chi_Minh |
| Thay toàn bộ lịch có sẵn, mở PDF, số câu/độ khó/lọc tài liệu và nguồn đang chọn | PASS_UI_ONLY | Đã kiểm thủ công trên kho thử; không suy chất lượng Vision hoặc sinh bài |
| Xóa một phiên riêng; cảnh báo trang nguồn chưa duyệt; chat mới sau gửi | PASS_UI_ONLY | Browser và DB kho thử; ảnh source-review-warning.png; câu cũ không còn trong ô nhập |
| Mobile390px và Windows/IME hệ điều hành | NOT_RUN | Override viewport công cụ không đổi DOM1280px; Windows máy thật chưa có |

## RAG và điều kiện huấn luyện

Theo quyết định YC206, hoàn tất dữ liệu/RAG đã sửa rồi chỉ học khi nguồn/dataset/runtime/tài nguyên/quality/retention đạt. Hai lượt học cũ đã kết thúc nhưng bị từ chối chất lượng; không có trainer đang tạm dừng để resume. Tiến trình chuẩn bị ứng viên cũ không phù hợp dữ liệu sửa.

Chỉ mục thử v5 đã xử lý157PDF/22.711trang, bảo toàn2.071bản review nguồn, kiểm toàn hash PDF và tạo lại20.628vector. Có52.608chunk/vector ràng buộc văn bản/model; integrity SQLite/FTS đạt. Chỉ mục vẫn ở kho thử, chưa thay kho đang phục vụ. Cổng nguồn hiện `complete:false`:7.916trang trong phạm vi chờ review và2review partial, giữ đúng13ngoại lệ YC188. Những trang này không tự được miễn hoặc coi là nguồn học đã duyệt.

Đã sửa chung: bảo toàn literal/code và dấu toán; nhận diện/giải mã VNI có kiểm soát; lưu block/vị trí/raw/provenance và cờ hình/bảng/công thức; migration có backup và vô hiệu vector stale; ưu tiên đoạn định nghĩa, giảm mục lục/nhiễu; packing toàn đoạn theo tokenizer thật trong8.192token; repair tối đa một lần với mã nguồn thực đã admitted; phân biệt trích dẫn thiếu/sai. Bản dịch máy chưa duyệt vẫn có thể hiển thị để đối chiếu nhưng không được dùng làm căn cứ trong prompt trả lời.

Baseline mô hình thật0/7 tạo được câu trả lời. V5 trước citation05 đạt6/7 về loại phản hồi, nhưng kiểm nội dung chỉ2đạt/2một phần/3không đạt. Không coi6/7là đạt chất lượng. Đối chứng bỏ bản dịch máy trên OOP/tâm lý cải thiện hai phản hồi, vẫn thiếu ý; bộ đã xem là chẩn đoán, không phải holdout kín. Ba câu thật sau citation05 đều tạo phản hồi nhưng đạo hàm còn thiếu điều kiện, OOP chép câu hướng dẫn, tâm lý vẫn nhầm stimulus/response. Mức cải thiện chưa ổn định. Citation06 đã được kiểm trên mô hình thật: OOP có mã nguồn hợp lệ, không còn chép câu mẫu hoặc `[S#]`; câu vẫn chưa diễn giải đầy đủ mọi khía cạnh đóng gói. Chất lượng chung chưa nghiệm thu. Adapter active:null; hai trial cũ đều DO_NOT_PROMOTE.

Theo YC207, sửa và kiểm chứng → commit/push nhánh/PR GitHub → chỉ học tiếp khi mọi cổng đạt. Đợt này xuất bản mã/test/hồ sơ để review, không công bố tài liệu gốc, DB/vector/weights hoặc dữ liệu người dùng. Không merge main hoặc khởi chạy học khi nguồn/chất lượng chưa đạt.

Bằng chứng local: `data/evaluation/ui-audit-yc206/` (matrix/log/ảnh); `data/evaluation/rag-repair-yc205/` (source/migration/tokens101PASS trước citation05, citation05 độc lập80PASS, readiness/realQA/manual review). File nguồn và dữ liệu người dùng không được công bố. GitHub: đã push `fc47329` và mở [PR15](https://github.com/ng-wngkh07/Study-Agent/pull/15) nháp; chưa merge. CI đầu có hai lỗi harness do thiếu config mô hình private. Đã tái hiện và sửa bằng metadata ngữ cảnh8192 trong thư mục thử, giữ nguyên kiểm giới hạn và production guard; portable kiểm lại496PASS12SKIP. CI head tiếp theo đang chờ, chưa báo đạt.

Kiểm cuối:496PASS/12SKIP sau cách ly khóa dialogue trong fixture (giữ cơ chế khóa thật ở thư mục tạm); không còn để kiểm API mô phỏng tranh khóa inference đang chạy. Node27PASS tái sử dụng vì ba asset không thay sau lượt kiểm. Consumer24PASS. 32file dùng chung đã quét bí mật và whitespace trước push; PR để draft do nguồn và chất lượng mô hình chưa đạt toàn bộ.
