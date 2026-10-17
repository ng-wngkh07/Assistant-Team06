# Chạy, kiểm thử và trình bày QA học tập

Cập nhật 16/10/2026 theo YC217. Hướng dẫn cài chi tiết có một nguồn chính:
[Windows](../README_WINDOWS.md), [Mac](../README_MAC.md).
Runtime web/CI chuẩn là Python 3.12; MLX là môi trường Mac riêng. Không nâng/cài
lại môi trường đang có job hoạt động. Chưa có benchmark phần cứng đủ để cam kết
RAM tối thiểu, độ trễ hoặc Windows full installer trên mọi máy.

## Chạy sản phẩm chính

Clone [Assistant-Team06](https://github.com/ng-wngkh07/Assistant-Team06), làm theo
README nền tảng rồi chạy từ gốc repo. Mac:

```bash
.venv/bin/python scripts/dev.py serve --profile demo
```

Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Profile demo
```

Mở `http://127.0.0.1:8000`. Demo tra cứu dùng nguồn tổng hợp có trong repo;
QA/ảnh thật cần model tương ứng theo README nền tảng. Nếu model thiếu, kiểm trạng
thái và xử lý thông báo; không lấy phản hồi mô phỏng thay kết quả QA thật.
`ui/app.py` là bản mẫu Lab 2, chưa thay cách chạy này và chưa thuộc dependency web.

Profile local đọc cấu hình riêng, không tự index khi mở server. Chỉ đồng bộ nguồn
khi có quyền ghi và nguồn đã đối chiếu. Không chạy `index` hoặc chuyển DB phục vụ
trong lúc người khác đang kiểm định corpus. Không bật training để kiểm demo.

## Kiểm thử phần mềm

Mac, trong môi trường đã chuẩn bị:

```bash
.venv/bin/python -m pip check
.venv/bin/python -m pytest -c pytest.windows.ini
node --test tests/chat_input_ui.test.cjs tests/document_lookup_ui.test.cjs tests/practice_ui.test.cjs tests/chat_lifecycle_ui_yc206.test.cjs
```

Windows dùng `test_windows.ps1` theo README. Node 22 dành cho test UI. `pytest -q`
chạy tập khác và có thể cần tài sản riêng; không dùng nó thay bộ portable rồi
mặc định mọi thành viên đều có nguồn/model. Xem [CONTRIBUTING](../CONTRIBUTING.md)
và [DoD](../DEFINITION_OF_DONE.md). Kiểm dependency, Git boundary, API và CI trên
commit cuối; một job xanh cũ không chứng nhận commit mới.

Nếu cần test phụ thuộc corpus/model, chỉ định đầu vào riêng, kiểm quyền ghi và ghi
NOT_RUN khi chưa chạy. Không dùng production DB làm fixture unit test mặc định.

## Nghiệm thu với giảng viên

1. Giới thiệu người dùng, vấn đề, phạm vi QA học tập và sơ đồ truy xuất.
2. Chọn nguồn công khai/tổng hợp, ghi phiên bản nguồn/model/mã đang chạy.
3. Hỏi câu một nguồn và câu tổng hợp; mở citation để đối chiếu nhận định/điều kiện.
4. Hỏi câu thiếu căn cứ; kiểm thông báo phạm vi và không có nguồn giả.
5. Thử phiên mới hoặc backend bận; kiểm bản nháp/lịch sử không lẫn.
6. Trình bày bảng đánh giá QA thật và các lỗi còn lại; phân biệt với test mock và
   benchmark văn phòng của Lab 2. Ngưỡng là [đề xuất cần đo](01_Y_TUONG_DU_AN.md).

Sau khi **cả 12 issue #18–29 hoàn tất và PR liên quan đã merge**, thực hiện toàn
bộ cổng tích hợp trong [báo cáo rà soát](team/RA_SOAT_REPO_YC217.md). Chưa chạy
runner `eval/run.py` từ hướng dẫn cho đến khi file đó và entrypoint của nhóm có
trong main. Không gắn nhãn hoàn thành chỉ từ trạng thái issue đã đóng.

## Rule và quyền GitHub

[Rule Antigravity](team/ANTIGRAVITY_PROJECT_RULE.md) quy định phạm vi, bằng chứng
và writer. [Quyền merge/branch](team/QUYEN_MERGE_VA_BRANCH.md) ghi cấu hình đã đọc
lại. Mỗi gói Antigravity mở chat mới, xác nhận lượt cũ kết thúc trước giao cùng
phạm vi. Tài liệu này không tạo lịch/tiến trình tiếp tục nền.
