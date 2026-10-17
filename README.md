# Study Agent — Agent Học Tập

Ứng dụng học tập chạy cục bộ: **tra cứu tài liệu**, **hỏi đáp có nguồn (QA)** và **thời khóa biểu từ ảnh**. Mã nguồn dùng Python/FastAPI, SQLite FTS5, Ollama và giao diện web. TV4 quản lý chỉ mục chính và môi trường MLX riêng trên Mac.

## Chọn môi trường

| Nhu cầu | Môi trường | Hướng dẫn |
| --- | --- | --- |
| Demo tra cứu, sửa code, kiểm thử với nguồn tổng hợp | Python 3.12; Windows 10/11 64 bit hoặc Mac | [Windows](README_WINDOWS.md), [Mac](README_MAC.md) |
| QA, embedding và đọc ảnh thật cho phát triển | Môi trường web + Ollama và model tương ứng | [Windows](README_WINDOWS.md#nguồn-thật-và-ocr), [Mac](README_MAC.md#ollama-và-nguồn-riêng) |
| Runtime MLX riêng của người điều phối | Mac Apple Silicon, môi trường tách khỏi web | [MLX Mac](README_MAC.md#runtime-mlx-riêng) |

Internet cần cho lần cài thư viện/công cụ/model; tra cứu FTS và test mock không cần Ollama. RAM/dung lượng cần theo model và corpus; repo chưa có số tối thiểu đo được để cam kết cho mọi máy. Linux và Mac Intel/MLX chưa thuộc phạm vi nghiệm thu.

## Bắt đầu trên Windows

Nếu đã có Git:

```powershell
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1
```

Mở `http://127.0.0.1:8000`; dừng bằng Ctrl+C. Bộ cài chuẩn bị Python 3.12, Git, Ollama và ba model từ manifest. Chưa có Git: dùng quy trình ZIP trong [README_WINDOWS.md](README_WINDOWS.md). Để chỉ thử giao diện và tra cứu, thay lệnh setup bằng `powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1 -DemoOnly`. Mặc định start dùng profile demo. Nếu gọi `.ps1` từ CMD khiến file mở trong trình soạn thảo, dùng đầy đủ lệnh `powershell ... -File` ở trên.

## Bắt đầu demo trên Mac

Cài Python 3.12 và Git trước theo [hướng dẫn Mac](README_MAC.md). Trên clone mới:

```bash
git clone https://github.com/ng-wngkh07/Assistant-Team06.git
cd Assistant-Team06
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/dev.py serve --profile demo
```

Mở `http://127.0.0.1:8000`. Demo dựng FTS từ `tests/fixtures/documents` vào `data/team-demo`, không chạm corpus riêng. Có thể tra cứu ngay; QA/đọc ảnh thật cần Ollama/model. [Hướng dẫn Mac](README_MAC.md) có cấu hình .env, nguồn riêng, MLX và OCR.

Thử gõ `help` hoặc `/help` trong ô chat sau khi mở ứng dụng để xem chức năng.
Lệnh này trả trợ giúp xác định, không gọi mô hình hay tìm nguồn; không phải câu
trả lời học thuật có trích dẫn. Nếu server đang bận, chờ lượt hiện tại kết thúc rồi thử lại.

## Tài liệu sau khi clone và cập nhật mã

Bản demo có **29 tài liệu tổng hợp**: hai mẫu ban đầu và [27 tài liệu thuộc 9 chủ đề](tests/fixtures/PUBLIC_CORPUS.md). Chạy với `--profile demo` (Windows: `-Profile demo`) để dựng thư viện từ các file đã có trong repo. Git không chứa thư viện sách riêng, DB/vector hoặc model của máy điều phối.

Profile `local` đọc nguồn và dữ liệu theo `.env`, **không tự lập chỉ mục khi khởi động**. Nhận nguồn được phép chia sẻ, kiểm đường dẫn rồi dùng **Đồng bộ thư viện**; đợi hoàn tất và thử tìm một cụm xuất hiện trong nguồn. Không dùng DB mang đường dẫn máy khác để thay bước này.

Khi cập nhật từ GitHub, dừng đúng server của mình, kiểm `git status` và giữ/cất thay đổi đang làm trước khi chuyển nhánh. Với checkout sạch muốn chạy bản main:

```bash
git fetch origin
git switch main
git pull --ff-only origin main
```

Khởi động lại bằng launcher của nền tảng, tải lại trang trình duyệt. `git fetch` chỉ tải thông tin remote; PR chưa merge phải được kiểm trên đúng nhánh PR. Xem [CONTRIBUTING](CONTRIBUTING.md#branch-và-thay-đổi) nếu đang tiếp tục PR hiện hữu.

## Sử dụng ba chức năng

1. **Tra cứu:** mở chế độ tra cứu, tìm từ xuất hiện trong tài liệu, ví dụ `học tập` ở demo. Kết quả có tên nguồn, trang/phần và trích đoạn; PDF có nút mở trang, Markdown hiện phần văn bản.
2. **QA:** khi model QA đã sẵn sàng, hỏi nội dung từ nguồn đã lập chỉ mục. Đối chiếu câu trả lời với trích dẫn; thiếu nguồn hoặc model phải xử lý theo thông báo. Test mock không chứng minh độ chính xác model.
3. **Lịch ảnh:** chọn ảnh thời khóa biểu, đọc bản nháp, chỉnh thông tin chưa rõ và xác nhận trước khi lưu/xuất lịch. Có ảnh tổng hợp ở `tests/fixtures/timetables`; không dùng lịch cá nhân làm dữ liệu training.
4. **Trợ giúp (`help`):** gõ `help` (hoặc `/help`, `trợ giúp`) trong ô chat để xem ngay danh sách các chức năng bot hỗ trợ và mẹo tương tác nhanh mà không cần gọi mô hình.

Trong QA, chọn mức xử lý **Tự động**, **Thông thường** hoặc **Kiểm tra kỹ**. Tự động chọn theo câu hỏi; Kiểm tra kỹ thêm bước đối chiếu nhận định với nguồn, có thể trả trạng thái chưa kiểm chứng khi model không hoàn tất hoặc thiếu căn cứ. So sánh được xác định từ nội dung câu hỏi. Mức xử lý là quy trình ứng dụng, không bảo đảm model luôn trả đúng.

Trong công cụ học, nhập/chọn chủ đề để tạo tối đa ba câu hỏi từ lý thuyết, tìm bài tập/đáp án có sẵn hoặc tạo câu ôn tập. Nguồn đề và nguồn đáp án được hiển thị riêng; chọn cách xem trước khi mở đáp án/lời giải. Câu sai hoặc “Chưa chắc” được lưu để ôn trên trình duyệt hiện tại. Khi chưa đủ căn cứ, QA nêu trạng thái và phạm vi đã tìm; các nút mở rộng giữ câu hỏi và gửi lại với phạm vi mới. Với phiên dài, ứng dụng báo khi một phần lịch sử không nằm trong ngữ cảnh lượt hiện tại. Chọn trang từ trích dẫn chỉ đặt phạm vi tìm kiếm, hệ thống truy xuất và kiểm citation mới cho lượt tiếp theo. Nút **Cuộc trò chuyện mới** đưa về màn hình bắt đầu, xóa bản nháp và phạm vi nguồn đang chọn; các phiên đã lưu vẫn còn trong danh sách. Khi lượt trước đang trả lời, ứng dụng yêu cầu đợi hoàn tất. Câu hỏi đã gửi được xóa khỏi ô nhập; khi gửi lỗi, bản nháp mới đang gõ được giữ nguyên và giao diện báo cách khôi phục câu hỏi.

Trong phiên có lịch sử, menu nguồn gần nhất nằm bên phải **Tóm tắt chủ đề**; chọn nguồn sẽ truy xuất lại cho câu hỏi mới. Menu đóng khi chọn mục, bấm ra ngoài hoặc nhấn Escape. Cửa sổ thẩm định OCR hiển thị giữa màn hình, cho phép kiểm/chỉnh bản nhận diện trước khi xác nhận.

Xem [nhật ký thay đổi](LICH_SU_DU_AN.md) để đối chiếu bản sửa giao diện và các giới hạn đã kiểm chứng.

Luồng tài liệu: nguồn → trích xuất/OCR → đoạn văn → FTS/vector → truy xuất → QA có nguồn. Luồng lịch: ảnh → bản nháp → người dùng xác nhận/chỉnh → lưu/xuất. Khi thêm nguồn riêng, dùng profile local và **Đồng bộ thư viện** theo hướng dẫn nền tảng; profile local không tự lập chỉ mục lúc khởi động.

## Cấu trúc và cấu hình

| Phần | Nội dung |
| --- | --- |
| app/, run.py | API, trích xuất, tìm kiếm, QA, OCR/lịch, index và cổng training |
| static/ | Giao diện ba chức năng |
| scripts/ | Launcher chung, kiểm lịch sử, tiện ích theo nền tảng |
| tests/, tests/fixtures/ | Test và nguồn tổng hợp |
| requirements.txt | Phụ thuộc web runtime |
| requirements-dev.txt, requirements-windows.txt | Runtime + công cụ test; wrapper Windows giữ tương thích installer |
| constraints-web-py312.txt | Bộ phiên bản runtime/test cố định cho Python 3.12 |
| requirements-mlx.txt, constraints-mlx-py313.txt | Runtime MLX riêng Mac/Python 3.13 |
| requirements-windows-tools.json, *.ps1 | Manifest công cụ/model Windows và setup/start/test |
| .github/ | Workflow CI và mẫu PR |
| docs/team/, output/pdf/ | Tài liệu nhóm và PDF phân công |
| corpus/ | Quy ước bàn giao riêng; không chứa thư viện thật hoặc importer tự động |
| data/ | CSDL và tệp runtime; quy ước nguồn và quyền sở hữu xem tại [data/README.md](data/README.md) |

`.env.example` mô tả các biến launcher đọc; `.env` không commit. `src/` là nguồn thật; `data/` giữ DB, vector, model, lịch/hội thoại/cache. Theo YC-214, chỉ công bố bản sửa, kiểm thử tổng hợp và tài liệu phần mềm; dữ liệu và thông tin huấn luyện giữ local, kể cả trong commit, mô tả PR và bình luận. Bàn giao riêng ở nơi được kiểm soát; không merge SQLite/weights qua Git và không dùng `git add -f` vượt ignore. [Corpus](corpus/README.md) mô tả ranh giới này.

API tự mô tả tại `http://127.0.0.1:8000/docs`. Kiểm trạng thái qua `scripts/dev.py check --profile demo` hoặc `--profile local`; thêm `--require-model` nếu cần lệnh thất bại khi model QA chưa sẵn sàng. Lệnh check không chứng nhận chất lượng model hay toàn corpus. Lỗi runtime hiện tại hiển thị trong terminal đang chạy ứng dụng.

## Kiểm thử và đóng góp

### Bộ câu hỏi benchmark

`eval/cases.csv` chứa 15 câu hỏi tự biên soạn với đáp án tham chiếu và nhãn
`normal`, `edge` hoặc `out-of-scope` (gồm 3 ca edge và 2 ca ngoài phạm vi).
Các ca kiểm tra nội dung và cách xử lý đầu vào thiếu/mơ hồ hoặc không thể biết;
chúng không dùng tài liệu cá nhân hay corpus demo làm holdout và không phải
chứng nhận chất lượng QA trên dữ liệu thật. Kiểm tra cấu trúc CSV bằng:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_eval_cases.py
```

Trên Mac:

```bash
.venv/bin/python -m pytest -q tests/test_eval_cases.py
```

### Benchmark accuracy

Từ thư mục gốc repo, chạy benchmark mẫu bằng Python của môi trường dự án:

```powershell
.\.venv\Scripts\python.exe eval\run.py
```

Trên Mac dùng:

```bash
.venv/bin/python eval/run.py
```

Runner đọc `eval/cases.csv` và in accuracy theo so khớp chính xác giữa hai cột
`expected` và `predicted` (bỏ khoảng trắng ở đầu/cuối). File CSV chứa prediction
đã có sẵn; lệnh này không gọi ứng dụng, model hay Ollama. Thay cột `predicted`
bằng đầu ra đã chấm/chuẩn hóa của hệ thống bạn muốn đo để đánh giá hệ thống đó.
Có thể truyền đường dẫn CSV khác làm đối số cho script.

Kiểm thử tổng quát trên Mac:

```bash
.venv/bin/python -m pip check
.venv/bin/python -m pytest -c pytest.windows.ini
node --test tests/chat_input_ui.test.cjs tests/document_lookup_ui.test.cjs tests/practice_ui.test.cjs tests/chat_lifecycle_ui_yc206.test.cjs
```

Node 22 dùng cho test giao diện, không cần để chạy web; cài từ [Node.js](https://nodejs.org/en/download). Windows dùng `test_windows.ps1` và lệnh Node tương tự. CI [Team feature checks](https://github.com/ng-wngkh07/Assistant-Team06/actions/workflows/team-checks.yml) kiểm Python 3.12 Windows/Mac, demo, feature tests, test giao diện; Windows thêm PowerShell/installer DemoOnly. PR kiểm nhật ký mới. Job `ui_adapter` trên Ubuntu kiểm luồng Streamlit với phụ thuộc riêng ở `ui/requirements.txt`; không cần Streamlit để dùng Web UI chính.

Đọc [CONTRIBUTING.md](CONTRIBUTING.md) để báo lỗi, tạo branch/PR và review; [quy tắc](README_QUY_TAC.md), [phân công 6 người](docs/team/PHAN_CONG_6_NGUOI.md), [PDF](output/pdf/PHAN_CHIA_NHIEM_VU_DO_AN_NHOM.pdf) và [nhật ký](LICH_SU_DU_AN.md) ghi trách nhiệm/lịch sử. Đối chiếu checks và chính sách hiện hành trên PR trước khi merge. Nhóm vẫn cần review chéo và TV4 điều phối tích hợp; CI xanh không thay review hoặc nghiệm thu chức năng thật.

## Giới hạn và giấy phép

CI/demo không nghiệm thu bộ cài full trên máy Windows mới, QA/VLM thật, corpus readiness hoặc training. MLX không cài trong môi trường Windows. Chỉ TV4 ghi corpus/index/vector chính và training; cần audit live `complete: true`, review/provenance toàn nguồn và đánh giá candidate độc lập trước kích hoạt. Các bước đã/chưa chạy ghi trong nhật ký và báo cáo nhóm.

Mã nguồn theo [MIT](LICENSE). Giấy phép mã không cấp quyền chia sẻ sách, ảnh cá nhân hoặc model của bên thứ ba; kiểm quyền và giấy phép riêng của từng nguồn/model.
