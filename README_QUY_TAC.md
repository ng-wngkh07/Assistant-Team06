# Quy tắc quản lý và thực hiện dự án

**Dự án:** Agent Học Tập. Quy tắc áp dụng cho mã nguồn, test, cấu hình, tài liệu và gói dữ liệu/model. Phân công/đầu ra/nghiệm thu theo [tài liệu nhóm](docs/team/PHAN_CONG_6_NGUOI.md); thao tác đóng góp theo [CONTRIBUTING](CONTRIBUTING.md).

## Trách nhiệm và thông tin thành viên

TV1 tra cứu, TV2 QA, TV3 lịch ảnh, TV4 dữ liệu/chỉ mục chính/huấn luyện và điều phối phát hành, TV5 UI/môi trường chung, TV6 kiểm thử/nghiệm thu/tài liệu. TV4 dùng Mac; các vai trò còn lại dùng Windows.

Phân công, giao việc, Issue, PR, ví dụ branch và log thành viên dùng mã TV1-TV6. Bảng ánh xạ tên thật/email/thông tin liên hệ quản lý riêng. Tên/email tác giả trong LICENSE và attribution Git được giữ nguyên để bảo toàn quyền tác giả. Không công khai khóa/token hoặc dữ liệu người dùng.

## Kiểm soát phạm vi và thay đổi

Trước sửa, đọc nhật ký theo chức năng/file/lỗi, đối chiếu main và hành vi đã nghiệm thu. Mỗi đầu việc có chủ trì, phạm vi, đầu ra và tiêu chí; giữ các sửa lỗi trước đó.

Nếu sửa phần chung hoặc ngoài phạm vi:

1. Ghi Issue/PR: lỗi tái hiện, expected/actual, file/chức năng ảnh hưởng.
2. Chốt giải pháp, tương thích/migration, rủi ro, test và rollback.
3. Thống nhất người ghi và thứ tự tích hợp với chủ trì liên quan và TV4.
4. Kiểm lại phần ảnh hưởng; thay đổi chung phải smoke tra cứu, QA và lịch.
5. Chưa đủ bằng chứng thì ghi đang làm/chờ kiểm/NOT_RUN; không tắt test để che lỗi.

Phần chung gồm server/config, searcher/indexer, khóa file, điều hướng/CSS và hợp đồng API. Không ghi đè cả file khi xử lý xung đột. Cấu hình riêng qua `.env`; không commit đường dẫn máy/token/settings cá nhân. Nếu cấu hình local cần để tái hiện, ghi điều kiện và cập nhật mẫu khi được thống nhất.

## Branch, review và tích hợp

Dùng `<loai>/<phamvi>/<ma-viec>-<ma-vai-tro>`; chữ thường không dấu, không khoảng trắng. Loại: feature/fix/refactor/test/docs/chore; phạm vi: lookup/qa/timetable/training/shared. Ví dụ `fix/lookup/12-sai-trang-tv1`, `test/shared/21-hoi-quy-tv6`. Một branch/PR cho một đầu việc, cập nhật main trước tạo và trước merge.

Vòng thực hiện: nhận việc/chốt tiêu chí → tái hiện → sửa/test → nhật ký/PR → review → xử lý xung đột/kiểm lại → merge → smoke tích hợp. Theo quy trình nhóm cần reviewer khác tác giả và chủ trì phần bị ảnh hưởng; TV4 điều phối merge/phát hành, TV6 kiểm tích hợp.

Bảo vệ main tại lần kiểm 09/10/2026: CI Windows/Mac/history, nhánh cập nhật, hội thoại xử lý; áp dụng cả admin, cấm force-push/xóa; không bắt buộc approval. Review chéo là quy trình nhóm; người có quyền ghi vẫn có thể merge khi thỏa điều kiện GitHub. Kiểm Settings/PR trước merge. Không push trực tiếp main, force-push nhánh chung, reset việc người khác hoặc thay bảo vệ để vượt lỗi.

## Nhật ký và hồ sơ

`LICH_SU_DU_AN.md` ở gốc là nhật ký chung, cập nhật cùng PR. Thành viên dùng ID `LOG-YYYYMMDD-chucnang-maviec-tvN`, ghi ngày/giờ Asia/Ho_Chi_Minh, mã vai trò/tác giả thay đổi, loại, trước/sau, file, ảnh hưởng, kiểm chứng và giới hạn. Giữ ID/ngày của mục cũ; hiệu chỉnh có lý do/thời điểm và bảo toàn các thay đổi có ý nghĩa.

Ghi lịch sử ứng dụng, không chép prompt/hội thoại/log tool/metadata bàn giao cá nhân. Tác giả mỗi PR tự ghi log; TV6 quản lý chất lượng hồ sơ. Hai PR cùng thêm mục phải giữ cả hai và xử lý ID trùng. PASS chỉ khi đã chạy và có bằng chứng; FAIL/NOT_RUN/N/A có lý do. Hồ sơ nội bộ giữ riêng.

## Tài sản Git và bảo mật

Git chứa app/static/script dùng chung/test tổng hợp/dependency/config mẫu/CI/README/docs nhóm, nhật ký thay đổi phần mềm, tài liệu kỹ thuật công khai và PDF phân công. Nguồn thật, ảnh/lịch/hội thoại người dùng, DB/vector/weights/adapter/dataset, .env, venv/cache/log, hồ sơ nội bộ và bảng ánh xạ thành viên quản lý riêng.

Tuân thủ [.gitignore](.gitignore), kiểm staged và không dùng `git add -f`. Gói nguồn/model riêng có version/hash/provenance/quyền và vị trí tải kiểm soát; không merge SQLite/vector/weights giữa branch.

## Nguồn, model và nghiệm thu

- Xử lý nội dung cục bộ; không sửa PDF nguồn hoặc đoán trang/ô mờ. Người dùng xem/chỉnh/xác nhận lịch trước lưu; lịch/hội thoại không tự vào tập học.
- TV4 giữ một writer/job cho corpus/index/vector/training chính; xác nhận kết thúc và bàn giao trước đổi bên ghi, không chạy GPU cạnh tranh.
- Trước học: audit hiện hành `complete: true` cho phạm vi nguồn đã được duyệt, provenance/hash nguồn-trang-chunk-vector, dedup/split/tokenizer/tài nguyên đạt. Mặc định phải OCR review toàn bộ. Theo YC-188, chỉ 13 trang chưa đọc đầy đủ trong manifest được người dùng duyệt và ràng buộc hash được loại khỏi dữ liệu học/kiểm tra; giữ PDF và trạng thái chưa hoàn chỉnh. Audit phải nêu `complete_all_sources`, số trang toàn nguồn/trong phạm vi/loại trừ; nguồn hoặc manifest đổi phải kiểm lại. Không dùng ngoại lệ để bỏ lỗi chỉ mục/vector/provenance. Demo/test xanh/số vector không cấp quyền học.
- Theo YC-207: khi phát hiện lỗi, hoàn tất sửa và kiểm chứng rồi commit/push mã, test và hồ sơ dùng chung qua nhánh/PR GitHub trước khi huấn luyện tiếp. Không push trực tiếp main; giữ dữ liệu/model riêng và mọi cổng nguồn/dataset/runtime/tài nguyên/chất lượng. Push thành công không thay nghiệm thu chất lượng hoặc cấp quyền kích hoạt adapter.
- Theo YC-214: chỉ push bản sửa, kiểm thử và hồ sơ thay đổi phần mềm. Không đưa dữ liệu hoặc thông tin huấn luyện vào file mới, nội dung commit, mô tả PR hoặc bình luận GitHub; hồ sơ chi tiết giữ local. Rà cả nội dung văn bản, không chỉ phần mở rộng file. Không tự viết lại lịch sử Git đã công bố.
- Candidate cần TV6 phối hợp TV1-TV3 đánh giá nguồn/holdout/retention độc lập; lưu revision/run ID/hash/rollback. Không kích hoạt chỉ vì loss giảm.
- Không đổi provider/model/cấu hình toàn cục của người khác. Ollama phát triển không tự có chất lượng tương đương MLX/adapter.
- Kiểm trên môi trường mục tiêu, ghi phần chưa chạy; test giả lập không chứng minh chất lượng suy luận/OCR thật. Ứng dụng hỗ trợ học tập, không chẩn đoán/kê đơn.

## Quản lý tài liệu

TV6 giữ các hướng dẫn khớp mã và kiểm liên kết; chủ trì xác nhận phần của mình. Sửa phân công cập nhật Markdown và [PDF](output/pdf/PHAN_CHIA_NHIEM_VU_DO_AN_NHOM.pdf) trong cùng PR. Đề xuất chưa giao triển khai được ghi riêng trong kế hoạch, không coi là tính năng đã hoàn thành.
