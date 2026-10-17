# Điều kiện hoàn thành công việc

Áp dụng cho issue/PR của Assistant-Team06. Issue #18–29 là bài tập Lab 2;
hoàn tất chúng chưa đồng nghĩa sản phẩm QA đã được nghiệm thu.

- Có mục tiêu, người phụ trách, tiêu chí quan sát được và phạm vi file rõ ràng.
- Đọc nhật ký và hợp đồng hiện tại; giữ thay đổi của thành viên khác.
- Thay đổi hành vi có ca tái hiện và ca hồi quy; tài liệu chỉ cần đối chiếu nguồn,
  đường dẫn, lệnh và trạng thái, không thêm test hình thức.
- Chạy checks phù hợp theo [CONTRIBUTING](CONTRIBUTING.md); ghi đúng OS, Python,
  commit, PASS/FAIL/SKIP/NOT_RUN và lý do. Không báo mock là model thật.
- Tài liệu và ví dụ API khớp mã đang chạy. Mô phỏng phải ghi rõ, không sinh nguồn giả
  để trình bày như câu trả lời đã kiểm chứng. Không thêm UI/LLM thứ hai khi chưa chốt thiết kế.
- Có mục mới trong [nhật ký chung](LICH_SU_DU_AN.md), không xóa/đánh lại ID cũ.
- Không công bố bí mật, nguồn riêng, dữ liệu/thông tin huấn luyện, DB hoặc weights.
  Kiểm `git status --short --untracked-files=all`, `git diff --cached` và
  `git check-ignore -v <file>` để chắc đầu ra không bị bỏ sót.
- Có reviewer khác tác giả, xử lý góp ý và kiểm lại commit sau cùng.
- Owner merge sau CI/review; kiểm trên main sau merge rồi mới đóng tiêu chí tích hợp.
  Bản thiết kế được duyệt không tự trở thành chức năng đã chạy.

Nếu thiếu một mục bắt buộc, giữ trạng thái đang làm hoặc ghi blocker cụ thể.
Tiêu chí sản phẩm ở [đặc tả](docs/01_Y_TUONG_DU_AN.md) và [báo cáo rà soát](docs/team/RA_SOAT_REPO_YC217.md); các việc chưa merge ở #20–27 vẫn do chủ trì phụ trách, không tự coi CI xanh là nghiệm thu QA thật.
