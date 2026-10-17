# Rule dự án cho Antigravity

Đối chiếu tài liệu chính thức ngày 16/10/2026:
[Rules](https://antigravity.google/docs/rules/) và
[Workflows](https://www.antigravity.google/docs/ide/workflows/).
Rule là ràng buộc thường trực; workflow là chuỗi bước gọi khi cần. Không biến
rule thành bản sao toàn bộ hồ sơ hoặc một prompt giao việc dài.

## File chuẩn

Dùng `.agents/rules/study-agent.md` ở gốc project, YAML `trigger: always_on`
và mô tả ngắn. Antigravity hiện đọc cả `.agents/rules/` và đường dẫn cũ
`.agent/rules/`; không đặt hai bản cùng nội dung. Không dùng `alwaysOn`:
giá trị trigger sai có thể khiến rule bị bỏ qua. `AGENTS.md` là Markdown thường,
không cần frontmatter; giữ hướng dẫn host cục bộ trong file đó.

Rule chung được chia sẻ riêng bằng ngoại lệ chính xác trong `.gitignore`;
các cấu hình/công cụ cá nhân khác trong `.agents/` tiếp tục giữ local.
Nội dung rule dẫn tới README, quy tắc, DoD và tài liệu kiến trúc thay vì chép lại.
Không chứa đường dẫn máy, tài khoản liên hệ hoặc lựa chọn model thực thi.

## Kiểm tra khi mở project

1. Mở đúng checkout, kiểm thư mục gốc và nhánh.
2. Mở phần Customizations/Rules của phiên bản Antigravity đang dùng; xác nhận
   rule project xuất hiện, đúng đường dẫn và chế độ luôn áp dụng.
3. Trong một chat mới, kiểm agent có thể nêu đúng mục tiêu QA học tập, phân biệt
   `static/` với `ui/`, và nêu cách giữ một writer trước khi sửa. Đây là kiểm
   hành vi có quan sát; file tồn tại hoặc lời agent xác nhận chưa đủ chứng minh
   rule luôn được nạp trong mọi phiên.
4. Mỗi gói việc/sửa/tiếp tục dùng chat mới trong đúng project, giữ chat cũ làm
   bằng chứng. Xác nhận lượt trước kết thúc/trả quyền trước khi giao cùng file.

Trong lượt YC217 đã kiểm cấu trúc file và nội dung. Kiểm tự động nạp và hành vi
trong chat mới **NOT_RUN** vì đang có lượt corpus hoạt động; không gửi gói trùng
hoặc làm gián đoạn writer đó. Không đổi cấu hình toàn cục để cài rule project.
