# Đóng góp cho Agent Học Tập

Tài liệu này hướng dẫn báo lỗi, chuẩn bị môi trường, sửa mã/viết test, gửi PR và review.
Cài/sử dụng ứng dụng theo [README](README.md), [Windows](README_WINDOWS.md) hoặc
[Mac](README_MAC.md). Đọc [quy tắc](README_QUY_TAC.md), [phân công](docs/team/PHAN_CONG_6_NGUOI.md)
và [nhật ký chung](LICH_SU_DU_AN.md) trước thay đổi; quy tắc cụ thể của dự án vẫn áp dụng.

## Trách nhiệm và phối hợp

TV1 tra cứu, TV2 QA, TV3 lịch từ ảnh chịu UI/API/test theo chức năng. TV4 (Mac)
hợp nhất corpus/index/vector chính và training; TV5 giao diện/môi trường chung;
TV6 kiểm thử/nghiệm thu/tài liệu. Năm người còn lại Windows. Mỗi vai trò có đầu ra và tiêu chí nghiệm thu trong bản phân công.

Chốt Issue, chủ trì, tiêu chí và phần chung bị ảnh hưởng. Đọc log theo lỗi/file/chức năng
và main mới nhất để giữ các sửa lỗi đã nghiệm thu. Sửa phần chung/ngoài phạm vi phải báo
và thống nhất giải pháp/test/tương thích/migration/rollback với người bị ảnh hưởng.
Giữ một người ghi module chung tại một thời điểm hoặc chia PR độc lập rõ ràng.

## Báo lỗi và đề xuất

Tạo Issue với:

- Commit/branch, OS và kiến trúc, Python và profile demo/local.
- Các bước tái hiện tối thiểu, điều mong đợi và kết quả thực tế.
- Log/ảnh lỗi đã che bí mật và nội dung nguồn riêng; ghi test đã/chưa chạy, lỗi có tái hiện với demo không. Ưu tiên ví dụ tổng hợp tối thiểu.
- Phạm vi ảnh hưởng và log/Issue cũ liên quan; không tự ghi dữ liệu cá nhân vào Issue.

Đề xuất chức năng ghi mục tiêu, đầu vào/đầu ra, tiêu chí kiểm chứng và ảnh hưởng; chốt
phạm vi với chủ trì trước làm rộng. Không suy chất lượng QA/VLM từ test mock. Dữ liệu, thông tin huấn luyện và hồ sơ nội bộ không đưa vào Issue/PR; phối hợp riêng với TV4 khi cần điều tra phần đó.

## Chuẩn bị môi trường đóng góp

Clone theo README. Mac dùng Python3.12 + `requirements-dev.txt`; Windows dùng
`setup_windows.ps1 -DemoOnly` cho test nhẹ hoặc full cho model thật. Wrapper
`requirements-windows.txt` include profile dev; `requirements.txt` chỉ là web runtime.
Node22 dành cho test giao diện, cài theo hướng dẫn nền tảng. MLX có profile riêng
Python3.13/Apple Silicon, chỉ dùng khi được giao phần đó; không cài vào venv web/Windows.

`.env` riêng có thể trỏ nguồn/data/index thử nhưng không commit. Kiểm process env vì nó
ưu tiên hơn .env; `scripts/dev.py` và launcher Windows nạp .env, bare `run.py` không tự nạp.
Dùng demo hoặc clone/data riêng cho test, không ghi corpus/index chính hay tải/chạy model
của máy đang có writer khác. Chỉ công bố điều kiện tái hiện phần mềm đã loại thông tin nội bộ; cấu hình riêng trao đổi qua kênh được kiểm soát.

## Branch và thay đổi

Sau khi nhận việc, từ checkout sạch đã giữ/cất thay đổi local:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c fix/qa/12-sua-trich-dan-tv2
```

Đổi tên nhánh theo việc của mình: `<loai>/<phamvi>/<ma-viec>-<ma-vai-tro>`;
loại feature/fix/refactor/test/docs/chore, phạm vi lookup/qa/timetable/training/shared.
Không sao chép nguyên tên ví dụ cho nhiều việc. PR độc lập từ main; khi tiếp tục PR
hiện hữu, làm trên đúng nhánh đã nhận, không tạo bản trùng. Không reset/hard checkout
để làm mất sửa local hoặc force-push nhánh chung.

Sau khi fetch, nhánh local đang chạy vẫn giữ nguyên cho đến khi bạn chủ động chuyển/cập nhật. Với PR hiện hữu, dùng `git switch <nhanh-PR>` và `git pull --ff-only origin <nhanh-PR>` khi checkout sạch. Nếu cần đưa main mới vào nhánh PR, merge `origin/main`, giải quyết xung đột và kiểm lại; không viết lại lịch sử chung. Khởi động lại đúng server và tải lại trình duyệt sau cập nhật UI/API.

Tái hiện lỗi, giữ ca kiểm bảo vệ hành vi đúng, sửa phần nhỏ đủ tiêu chí; đọc diff/caller
và kiểm hồi quy. Giữ phong cách module hiện có, không thêm thư viện/đổi format toàn repo
khi chưa cần. Phần chung phải smoke cả tra cứu, QA và lịch. Không tắt test để che lỗi.

## Kiểm thử trước PR

Mac từ gốc repo:

```bash
.venv/bin/python -m pip check
.venv/bin/python -m pytest -c pytest.windows.ini
node --test tests/chat_input_ui.test.cjs tests/document_lookup_ui.test.cjs tests/practice_ui.test.cjs tests/chat_lifecycle_ui_yc206.test.cjs
```

Windows:

```powershell
.venv\Scripts\python.exe -m pip check
.\test_windows.ps1
node --test tests/chat_input_ui.test.cjs tests/document_lookup_ui.test.cjs tests/practice_ui.test.cjs tests/chat_lifecycle_ui_yc206.test.cjs
```

Feature suite dùng nguồn/data tạm/mock; không cần corpus/model cá nhân. Chạy thêm test
liên quan phần sửa. Nếu sửa environment/installer, kiểm setup/PowerShell trên Windows;
Mac PASS không được ghi thành Windows PASS. Nếu sửa UI chung, smoke ba chế độ.
Model/ảnh thật, bộ cài full máy mới, POSIX/MLX/corpus tests có nghiệm thu riêng; ghi NOT_RUN
khi chưa chạy. Xem [Team feature checks](https://github.com/ng-wngkh07/Study-Agent/actions/workflows/team-checks.yml).

Nâng dependency: sửa profile đúng và constraints tương ứng, giải quyết trong venv mới,
kiểm pip check/import/runtime và CI Windows/Mac. Constraints web/Python3.12 không dùng
cho .train-venv/Python3.13. Không freeze môi trường cá nhân vào repo mà chưa loại gói
không liên quan/đường dẫn private và kiểm dependency graph. Nâng MLX phải review thêm
API/tokenizer/cổng đánh giá; cài thư viện không phải quyền training/promote.

## Nhật ký, commit và PR

1. Thêm mục vào **LICH_SU_DU_AN.md ở gốc**: ID duy nhất, ngày/giờ Asia/Ho_Chi_Minh,
   người/loại, lỗi/mục tiêu, trước/sau, file, ảnh hưởng, log liên quan, checks và giới hạn.
   Thành viên dùng mẫu `LOG-YYYYMMDD-chucnang-maviec-tvN` trong file; giữ ID/ngày cũ,
   không copy prompt, không tạo log riêng hay ghi vào archive local docs/project. Nhật ký công khai chỉ ghi thay đổi phần mềm; thông tin huấn luyện giữ local theo YC-214.
2. Kiểm `git diff`, `git status` và `git diff --check`. Stage đúng file thay đổi,
   kiểm `git diff --cached`; không `git add -f`. Commit mô tả kết quả cụ thể.
3. `git push -u origin <nhanh-cua-ban>` rồi mở PR vào main theo mẫu của repo:
   vấn đề/trước-sau, phạm vi/ảnh hưởng, Issue/log, OS/Python/profile, kiểm đã/chưa chạy,
   phối hợp và rollback. Không đính kèm bí mật, nguồn riêng hoặc dữ liệu/thông tin huấn luyện, kể cả trong nội dung commit và bình luận. Tiếp tục PR hiện hữu bằng cách push đúng nhánh đó.
4. Cập nhật nhánh từ origin/main trước merge, xử lý xung đột cùng chủ trì. Với log, giữ
   cả mục hợp lệ và sửa ID trùng; không ghi đè cả file. Kiểm lại sau giải quyết xung đột.

## Review và merge

Workflow hiện tại chạy `features (windows-latest)`, `features (macos-latest)` và
`history` cho PR. Kiểm kết quả trên đúng commit mới nhất, trạng thái nhánh, hội thoại
và chính sách bảo vệ hiện hành trên GitHub trước merge. Không suy điều kiện merge
từ một lần kiểm cũ hoặc từ chữ MERGEABLE.

Nhóm review chéo theo quy trình, kể cả khi GitHub không bắt buộc approval. Reviewer khác tác giả đọc
Files changed và bằng chứng → Review changes → Approve → Submit review. Commit mới
sau review cần được đối chiếu lại; đủ checks không có nghĩa đã được nhóm duyệt. [GitHub hướng dẫn approval](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews).

Chủ repo quản lý quyền tham gia qua **Settings → Collaborators** theo trách nhiệm đã chốt.
Tài liệu phân công, Issue, PR và ví dụ giao việc dùng mã TV1-TV6; bảng ánh xạ tên thật/
email/tài khoản liên hệ được quản lý riêng. Thông tin tác giả và bản quyền trong LICENSE
và attribution Git được giữ nguyên.

TV4 điều phối merge theo quy trình nhóm. Quyền thao tác thực tế phụ thuộc cấu hình GitHub;
quy trình này không tự thay đổi quyền tài khoản hoặc chính sách bảo vệ nhánh.

Reviewer kiểm đúng tiêu chí, test/giới hạn, log, ranh giới tài sản và ảnh hưởng; phần chung
cần review chủ trì bị ảnh hưởng. TV4 merge theo quy trình nhóm sau checks/review hợp lệ. TV6 kiểm tích hợp;
nhóm đồng bộ main bằng fast-forward khi checkout sạch. Không tự tắt bảo vệ review,
push trực tiếp main, force-push hoặc xoá main để vượt blocker. Training là đợt riêng,
không tự chạy vì PR mã đã merge.

## Tài sản được chia sẻ

Git chứa app/UI/scripts/tests tổng hợp, requirements/constraints/config mẫu/CI/docs nhóm,
nhật ký thay đổi phần mềm và PDF phân công được phép. Dữ liệu và thông tin huấn luyện
không công bố trên GitHub theo YC-214; rà cả nội dung văn bản, tên/nội dung commit,
mô tả PR và bình luận trước khi push, không chỉ rà phần mở rộng file. `.gitignore` dùng allowlist: file
chia sẻ mới ở gốc phải thêm ngoại lệ chính xác và kiểm lại, không mở rộng cho data bí mật.
Nguồn thật `src/`, toàn `data/`, DB/vector/weights, .env, venv/cache, docs/project và
cấu hình/tích hợp cá nhân giao riêng hoặc giữ local. Không merge SQLite/weights qua Git.

Bàn giao nguồn/model có version/hash/quyền và provenance theo [corpus/README.md](corpus/README.md).
Một writer chính hợp nhất corpus/training; readiness live `complete: true`, OCR review,
nguồn-trang-chunk-vector, dedup/split/tokenizer/tài nguyên phải đạt. Đánh giá candidate độc lập,
giữ kiến thức cũ và rollback trước kích hoạt; loss/test xanh chưa đủ. Giấy phép [MIT](LICENSE)
cho mã không thay quyền nguồn/model. Hai cải tiến nhỏ trong phân công còn là đề xuất.

## Quản lý bí mật và dữ liệu nhạy cảm (Secrets)

1. **Không commit bí mật:** Tuyệt đối không commit API keys, mật khẩu, access token hoặc thông tin bí mật cá nhân vào Git (bao gồm cả code, commit message, PR description và comment).
2. **Cấu hình môi trường qua `.env`:**
   - Sao chép file `.env.example` thành `.env` để cấu hình cục bộ: `cp .env.example .env`.
   - File `.env` đã được liệt kê trong `.gitignore` và không bao giờ được thêm vào Git (`git add`).
3. **Xử lý sự cố rò rỉ:** Nếu phát hiện bí mật bị commit nhầm:
   - Ngay lập tức thu hồi/revoke token/key trên trang quản trị nhà cung cấp dịch vụ.
   - Báo ngay cho nhóm trưởng (TV4) và người quản lý tài liệu (TV6) để loại bỏ commit khỏi lịch sử Git.
