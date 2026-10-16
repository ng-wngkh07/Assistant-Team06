# Phát triển Agent Học Tập trên Windows

Năm thành viên dùng Windows: ba chủ trì tra cứu/QA/lịch ảnh, hai người hỗ trợ giao diện-môi trường và kiểm thử-tài liệu. TV4 dùng Mac
để hợp nhất chỉ mục chính và huấn luyện. Đọc [quy tắc](README_QUY_TAC.md),
[phân công](docs/team/PHAN_CONG_6_NGUOI.md) và [nhật ký](LICH_SU_DU_AN.md).

## Cài lần đầu - không tự tìm bản cài

Máy Windows 10/11 64 bit dùng PowerShell. Có Internet cho lần cài; lần tải ba model cần nhiều GB dung lượng và thời gian tùy mạng. Bộ cài dùng WinGet với mã gói cố định và chấp nhận thỏa thuận của nguồn/gói; Windows có thể hiện yêu cầu quyền cài phần mềm.

Nếu đã có Git, clone theo README chính. Nếu chưa có Git:

1. Mở [Download ZIP](https://github.com/ng-wngkh07/Assistant-Team06/archive/refs/heads/main.zip), giải nén vào thư mục riêng.
2. Mở PowerShell tại thư mục vừa giải nén, chạy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 -DemoOnly
```

3. Script chuẩn bị Python/Git. Đóng rồi mở PowerShell để nhận PATH mới. Clone repo vào một thư mục mới để làm việc với branch (bản ZIP không có lịch sử Git):

```powershell
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1
```

Nếu đã clone, chỉ chạy hai lệnh setup/start cuối. `ExecutionPolicy Bypass` áp dụng cho tiến trình gọi này, không đổi chính sách toàn máy. Mở `http://127.0.0.1:8000`; dừng bằng Ctrl+C. Dùng `-Reload` với start khi cần. Không phải activate venv.

## Danh sách yêu cầu trong repo

| File | Vai trò |
| --- | --- |
| requirements.txt | Thư viện web runtime |
| requirements-dev.txt, requirements-windows.txt | Runtime và công cụ test, installer giữ wrapper Windows |
| constraints-web-py312.txt | Cố định bộ phiên bản Python 3.12 dùng trên Windows/Mac |
| requirements-windows-tools.json | Python 3.12, Git, Ollama qua WinGet; model QA, embedding, vision |
| setup_windows.ps1, scripts/windows_setup.ps1 | Đọc danh sách, kiểm công cụ có sẵn, cài phần thiếu, kiểm lỗi, dựng demo |
| start_windows.ps1, test_windows.ps1 | Chạy ứng dụng/kiểm thử bằng Python của .venv |

Mặc định setup đầy đủ: cài phần thiếu, chuẩn bị `qwen2.5:3b`, `bge-m3`, `qwen2.5vl:3b`, kiểm hiện diện model và môi trường. Nếu thiếu WinGet, script dùng Microsoft.WinGet.Client từ PowerShell Gallery để khôi phục/cài cho tài khoản hiện tại. Không tải file .exe hoặc model lên Git; danh sách/script tải trực tiếp từ nguồn gói. Cài lại tái dùng công cụ đã có, không tự nâng cấp toàn máy hay thay .env hiện có.

Chỉ sửa UI/test nhẹ: dùng `setup_windows.ps1 -DemoOnly` để bỏ Ollama/model. Demo dùng tài liệu tổng hợp ở tests/fixtures và DB riêng data/team-demo. FTS/test mock không cần model. QA/ảnh thật cần cài full và đánh giá riêng; file model có mặt chưa chứng minh chất lượng.

Cấu hình .env riêng trỏ endpoint/model khác sẽ được giữ nguyên. Bộ cài full báo dừng nếu lệch manifest để tránh tải/chạy nhầm; dùng DemoOnly hoặc thống nhất cấu hình với TV4. Nếu .venv cũ không phải Python 3.12, đổi tên sang .venv-backup rồi chạy lại. Có lỗi cài/mạng: giữ nội dung lỗi và gửi TV5; chạy lại setup sau xử lý, không tìm bản cài ngẫu nhiên.

## Nguồn thật và OCR

Ollama Windows là bản phát triển; không đồng nhất chất lượng với adapter MLX Mac. VLM đảm nhiệm đọc ảnh theo cấu hình mặc định. Tesseract `vie+eng` là công cụ OCR tùy chọn của các script corpus Mac, không phải yêu cầu bắt buộc của bộ cài Windows. Không đoán chữ mờ: xem/chỉnh bản nháp và xác nhận trước khi lưu lịch.

Nguồn riêng do bàn giao cần version/hash/quyền trước khi đặt vào `src/` của clone riêng. Nếu chưa có `.env`, sao chép `.env.example`; giữ file đã có. Sửa các đường dẫn để tách index thử của thành viên:

```dotenv
AGENT_SRC_DIR=src
AGENT_DATA_DIR=data/team-local
HISTORY_DB_PATH=data/team-local/history.db
TIMETABLE_DB_PATH=data/team-local/timetable.db
TIMETABLE_IMAGES_DIR=data/team-local/timetable_images
```

Giữ endpoint/model trong manifest cho bộ cài full; launcher đọc `.env`, biến process đã có được ưu tiên. Chạy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Profile local
```

Trên web chọn **Đồng bộ thư viện**, đợi xong, kiểm lỗi/nguồn/trang/đoạn rồi tìm một cụm có trong nguồn. Profile local không tự index khi start. QA có nguồn cần model QA và embedding đã chuẩn bị; ảnh lịch cần model vision. Đọc/chỉnh bản nháp trước khi xác nhận lưu. Không push nguồn/index thử; TV4 hợp nhất corpus chính sau review riêng. Snapshot DB chứa đường dẫn Mac cần chuẩn bị riêng, không mặc định sao chép sang Windows sẽ hoạt động.

API tự mô tả tại `http://127.0.0.1:8000/docs`. Kiểm môi trường bằng `.venv\Scripts\python.exe scripts/dev.py check --profile local --require-model`; lỗi ứng dụng hiện trong terminal chạy start.

Nguồn bộ cài: [Microsoft WinGet](https://learn.microsoft.com/en-us/windows/package-manager/winget/), [WinGet install](https://learn.microsoft.com/en-us/windows/package-manager/winget/install), [Ollama Windows](https://docs.ollama.com/windows).

## Kiểm thử và PR

Cài [Node 22](https://nodejs.org/en/download) nếu đóng góp UI; Node không cần cho chạy web. Từ gốc repo:

```powershell
.venv\Scripts\python.exe -m pip check
.\test_windows.ps1
node --test tests/chat_input_ui.test.cjs tests/document_lookup_ui.test.cjs tests/practice_ui.test.cjs tests/chat_lifecycle_ui_yc206.test.cjs
```

`test_windows.ps1` chạy tập Python; lệnh Node kiểm giao diện riêng. CI chạy cả hai. Bộ phiên bản Python được cố định bằng constraints; khi nâng thư viện phải cập nhật cùng kiểm chứng Windows/Mac. Không tự thêm MLX vào profile Windows.

Tập `pytest.windows.ini` kiểm tra các tính năng với dữ liệu tạm/mock, khóa native và
demo sạch; không cần corpus/model cá nhân. Các test cũ phụ thuộc thư viện sách,
OCR feedback local, giám sát tiến trình POSIX và thử nghiệm MLX vẫn chạy riêng trên
Mac; không được báo tập feature là toàn bộ test đã đạt. Xem kết quả mới nhất tại
[CI Windows và Mac](https://github.com/ng-wngkh07/Assistant-Team06/actions/workflows/team-checks.yml).
[Lần chạy ngày 09/10/2026 trên commit 871d4dc](https://github.com/ng-wngkh07/Assistant-Team06/actions/runs/37887196815)
đã đạt 110 test feature trên mỗi hệ điều hành, cùng kiểm bộ cài demo Windows.
Liên kết lần chạy cũ `37882594844` hiện trả 404; kết quả của lần chạy hiện tại
được kiểm riêng, không suy từ liên kết cũ hoặc coi 404 là test thất bại.

Mỗi PR phải thêm mục vào `LICH_SU_DU_AN.md`, ghi môi trường/lệnh đã chạy và phần
`NOT_RUN`. Khi sửa `app/server.py`, cấu hình, tìm kiếm hoặc JS/CSS chung, review chéo
và smoke cả ba chức năng. TV4 review PR thành viên/merge theo quy trình nhóm sau CI. Kiểm checks và chính sách hiện hành trên đúng PR/commit; CI không thay review chéo nhóm. Xem [CONTRIBUTING](CONTRIBUTING.md); không tự ghi lên main.

## Giới hạn hiện tại

Huấn luyện MLX và công cụ quản lý dịch vụ Mac không chạy trên Windows. Khóa GPU
Windows hỗ trợ shared/exclusive bằng LockFileEx để các lớp truy vấn có thể lồng nhau;
không hỗ trợ kế thừa descriptor huấn luyện
POSIX. Bộ này đã được kiểm chứng trên runner Windows thật và Mac trong CI ngày
09/10/2026; các test dùng dữ liệu tạm/mock. Chất lượng QA/đọc ảnh với model thật và
corpus của nhóm cần nghiệm thu riêng trên máy thành viên.

Tài liệu nền tảng: [Windows LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex),
[MLX](https://ml-explore.github.io/mlx/build/html/install.html).

## Kiểm bộ cài mới (YC-170)

CI Windows đã đạt kiểm cú pháp PowerShell, 8 ca kiểm hợp đồng và chạy setup DemoOnly bằng công cụ có sẵn trên runner. Nhánh full cài WinGet/phần mềm khi thiếu, tải model và QA/ảnh thật chưa chạy trên máy Windows mới; cần nghiệm thu trên máy thành viên. Không coi CI demo là kiểm toàn bộ bộ cài full.


## Xử lý lỗi thường gặp

| Hiện tượng | Cách kiểm tra |
| --- | --- |
| Python sai phiên bản/import lỗi | Dùng đúng .venv Python3.12, chạy pip check, kiểm log setup; không cài gói vào Python toàn máy |
| Cổng web 8000 đã dùng | Dừng đúng server của mình hoặc đổi PORT trong .env; không dừng dịch vụ của người khác |
| Ollama/model không sẵn sàng | Kiểm endpoint manifest/11434, ollama list và lệnh check; cài full thiếu model cần xử lý lỗi tải |
| Clone xong chưa thấy tài liệu | Dùng `-Profile demo` để nạp 29 tài liệu tổng hợp đã có trong repo; local cần nguồn riêng và Đồng bộ thư viện |
| Nguồn mới không xuất hiện | Kiểm src/data đúng clone, bấm Đồng bộ thư viện, xem lỗi index; không coi start local là đã index |
| File .ps1 mở trong trình soạn thảo | Gọi `powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1 -Profile demo` thay vì mở file từ CMD |
| .env Invalid setting | Dùng biến được .env.example/launcher hỗ trợ, giá trị không rỗng |
| Merge bị chặn dù CI đạt | Kiểm checks, nhánh cập nhật, hội thoại và policy hiện tại; approval chỉ là blocker khi bật yêu cầu duyệt |

Ghi commit, Windows/Python/profile, lệnh, cách tái hiện và lỗi terminal khi báo Issue; che token/dữ liệu cá nhân. Xem [hướng dẫn đóng góp](CONTRIBUTING.md).
