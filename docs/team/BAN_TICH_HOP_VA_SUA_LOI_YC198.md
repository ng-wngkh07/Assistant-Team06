# Bản tích hợp và sửa lỗi YC-198

Ngày 12/10/2026, múi giờ Asia/Ho_Chi_Minh. Bản tích hợp từ main `af4933a44706aaa46662c67450dcbfd6535d527c`, trên branch `codex/fix/shared/yc198-reviewed-changes-tv6`; tiếp nối YC-193–197. Codex giữ kiểm thử/review/Git; Antigravity sửa bảy file được giao trong checkout cách ly. Reviewer nhóm TV1–TV6 cần kiểm phần thuộc vai trò trước khi TV4 quyết định merge.

## Thay đổi ứng dụng

- Tích hợp giao diện và công cụ học theo chủ đề, phân biệt nguồn đề/đáp án, chỉ mở lời giải sau lựa chọn; liên kết PDF/trang thay ảnh nguồn. Ô nhập giữ bản nháp và chặn gửi Enter lúc ghép chữ; bố cục tìm bài vừa màn hình nhỏ.
- QA có mức xử lý auto/standard/careful, chọn nguồn phù hợp câu so sánh; giới hạn hai lời gọi khi kiểm chứng. Cổng auditor yêu cầu câu trả lời JSON hoàn chỉnh, receipt `stop` riêng của lời gọi, trích dẫn nguyên văn trong nguồn hiện tại và bao phủ đúng nhận định. Receipt của lượt tạo đáp án không được dùng lại để cấp chứng nhận.
- Chặn dữ liệu nhập không hợp lệ, role client không được phép và dung lượng yêu cầu vượt giới hạn. Escape nội dung tài liệu/hội thoại trước khi ghép prompt; kết quả thất bại/cắt đoạn không được lưu như lượt hỏi đáp hoàn tất.
- Lịch sử lấy lượt gốc liên quan trong cùng phiên với giới hạn; tóm tắt chỉ transcript đã hoàn tất. Ngữ cảnh MLX mặc định 8K; tùy chọn local bị chặn theo giới hạn model và exact input/output tokenizer trước nạp weights. Launcher nhận `MLX_CONTEXT_TOKENS` từ `.env`; 16K đã có phép đo trước đó, 32K chưa nghiệm thu.
- Tìm bài tập dùng Unicode/casefold và chuẩn hóa khoảng trắng trước giới hạn kết quả. Giữ nguyên nội dung nguồn và locator. Thay đổi phiên bản bốn asset đã sửa để trình duyệt tải lại.
- Dùng chung hàm dựng prompt tạo câu hỏi giữa runtime và bộ chuẩn bị dữ liệu sáu nhiệm vụ cũ. Script dùng đường dẫn cấu hình/tệp nhãn local được chỉ định rõ, không nhúng bộ nhãn hoặc trích đoạn riêng, không tự tạo nhãn khi thiếu đầu vào. Mặc định chỉ tạo bản nháp; không tự huấn luyện hoặc cấp quyền nghiệm thu.
- CI Windows/macOS nhận thêm các test tổng hợp mới và chạy cả ba file kiểm giao diện. Fixture MLX có config/tokenizer giả lập đầy đủ; test câu rỗng theo hợp đồng admission 422. Giữ cổng fail-closed trong mã sản phẩm.

## Phạm vi kiểm chứng

So sánh full suite trên bản sao main và bản tích hợp không chứa corpus/model/hồ sơ cá nhân: main 54 FAIL, 565 PASS, 3 SKIP; tích hợp ban đầu 58 FAIL, 764 PASS, 15 SKIP. Bốn lỗi mới được phân loại: fixture tokenizer/config, mã trả về câu rỗng, prompt tạo câu hỏi và nguyên tắc đa lĩnh vực. Các test mới còn tái hiện thiếu receipt auditor, cache asset và launcher cấu hình ngữ cảnh.

Kết quả cuối trên bản bàn giao: **440 PASS / 12 SKIP** trong bộ chức năng portable; **20/20 Node PASS**; hồi quy YC-198 **15/15 PASS**. Bộ toàn repo chạy với quyền đọc tiến trình host: **788 PASS / 49 FAIL / 15 SKIP**, không có ID lỗi mới so với baseline main. Hai lỗi fixture QA cũ đã sửa; ba test điều phối tiến trình đạt khi có quyền host, không tính là sửa mã sản phẩm.

49 lỗi còn lại cần tài sản hoặc môi trường local: 8 registry/holdout canonical, 1 ledger review riêng, 1 legacy model readiness, 2 curator v10/v11, 22 curation/corpus gates, 3 PDF thật, 1 tokenizer 7B, 1 RAG citation, 2 bảng lịch OCR riêng, 4 search corpus, 4 vận hành/model/audit local. Không bỏ hoặc sửa expected của các test để che giới hạn; full suite chưa đạt. Danh sách ID/log đầy đủ giữ trong bằng chứng local YC-198.

Sau Antigravity terminal/trả quyền, Codex review và bổ sung chặn thiếu/JSON holdout rỗng trước tạo đầu ra, bốn hồi quy đạt. Input holdout phải chọn rõ qua `HOLDOUT_DEFS_PATH` hoặc `FEATURE_HOLDOUT_DEFS_PATH`; annotation chọn `ANNOTATION_DEFS_PATH` và đầu ra đã review qua `ANNOTATIONS_PATH`. Chỉ bản nháp có thể được tạo, không cấp quyền học tự động.

Smoke giao diện bản tích hợp trên DB demo riêng: chọn Kiểm tra kỹ, tìm đúng một bài Đại số tuyến tính ở trang 2, mở locator PDF và chuyển nguồn sang Hỏi đáp; nhập tay lịch 08:00–09:00 và tải ICS có VEVENT/lặp tuần/Asia_Ho_Chi_Minh đạt. QA model demo `qwen2.5:3b` chưa có, nên không tuyên bố sinh đáp án thật đạt trong smoke này. Không đổi model/provider để lách kiểm tra. `pip check` và whitespace diff đạt. CI GitHub sẽ ghi theo head thực sau push; cài Windows/model thật trên máy sạch vẫn chưa chạy.

## Giới hạn và bàn giao

Huấn luyện trial02 YC-196 hoàn tất nhưng đánh giá nội dung chưa đạt: bộ mới 18 ca chỉ 2 đạt, 2 một phần, 14 không đạt; auditor trích quote sai và vẫn có chứng nhận sai. Adapter giữ **DO_NOT_PROMOTE**, chưa kích hoạt. Sửa cổng mã không chứng minh model đã học đúng. Bộ chuẩn bị sáu nhiệm vụ cũ không thay bộ đóng băng 11 nhiệm vụ dùng ở v28; thay runtime phải capture/review/hash/token/split và xét quyền học lại.

Không đưa PDF/corpus thật, DB/vector, weights/adapter, bộ nhãn/dataset hoặc cấu hình cá nhân lên GitHub. OCR/VLM ảnh thật, bộ gõ hệ điều hành, context 32K và cài Windows đầy đủ/model thật trên máy sạch còn NOT_RUN trong lượt này. CI DemoOnly không thay các nghiệm thu đó.

Rollback: bỏ branch/đóng PR trước merge, hoặc revert commit tích hợp sau khi nhóm đã merge. Không cần migration corpus/model; không tự đổi adapter registry hay provider. Checkout chính và runtime đã đóng băng trước học được giữ để đối chiếu.

## Công bố GitHub

Đã push commit mã `fd3d777` lên branch riêng và tạo [PR nháp #12](https://github.com/ng-wngkh07/Study-Agent/pull/12). Readback remote SHA/head/branch đúng. `gitleaks` quét commit tích hợp không có rò rỉ; cổng lịch sử và diff whitespace đạt. CI history đã PASS, jobs Windows/macOS đang chạy tại thời điểm ghi; kết quả mới nhất xem checks của PR. Main chưa merge, adapter chưa kích hoạt.
