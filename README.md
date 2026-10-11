# Study Agent — Agent Học Tập

Ứng dụng học tập chạy cục bộ: **tra cứu tài liệu**, **hỏi đáp có nguồn (QA)** và **thời khóa biểu từ ảnh**. Mã nguồn dùng Python/FastAPI, SQLite FTS5, Ollama và giao diện web. TV4 quản lý chỉ mục chính và môi trường MLX riêng trên Mac.

## Chọn môi trường

| Nhu cầu | Môi trường | Hướng dẫn |
| --- | --- | --- |
| Demo tra cứu, sửa code, kiểm thử với nguồn tổng hợp | Python 3.12; Windows 10/11 64 bit hoặc Mac | [Windows](README_WINDOWS.md), [Mac](README_MAC.md) |
| QA, embedding và đọc ảnh thật cho phát triển | Môi trường web + Ollama và model tương ứng | [Windows](README_WINDOWS.md#nguồn-thật-và-ocr), [Mac](README_MAC.md#ollama-và-nguồn-riêng) |
| Model nền/adapter MLX và chuẩn bị training | Mac Apple Silicon, Python 3.13 riêng trong .train-venv | [MLX Mac](README_MAC.md#runtime-mlx-riêng) |

Internet cần cho lần cài thư viện/công cụ/model; tra cứu FTS và test mock không cần Ollama. RAM/dung lượng cần theo model và corpus; repo chưa có số tối thiểu đo được để cam kết cho mọi máy. Linux và Mac Intel/MLX chưa thuộc phạm vi nghiệm thu.

## Bắt đầu trên Windows

Nếu đã có Git:

```powershell
git clone https://github.com/ng-wngkh07/Study-Agent.git
cd Study-Agent
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_windows.ps1
```

Mở `http://127.0.0.1:8000`; dừng bằng Ctrl+C. Bộ cài chuẩn bị Python 3.12, Git, Ollama và ba model từ manifest. Chưa có Git: dùng quy trình ZIP trong [README_WINDOWS.md](README_WINDOWS.md). Dùng `setup_windows.ps1 -DemoOnly` cho demo/test nhẹ, không tải Ollama/model.

## Bắt đầu demo trên Mac

Cài Python 3.12 và Git trước theo [hướng dẫn Mac](README_MAC.md). Trên clone mới:

```bash
git clone https://github.com/ng-wngkh07/Study-Agent.git
cd Study-Agent
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/dev.py serve --profile demo
```

Mở `http://127.0.0.1:8000`. Demo dựng FTS từ `tests/fixtures/documents` vào `data/team-demo`, không chạm corpus riêng. Có thể tra cứu ngay; QA/đọc ảnh thật cần Ollama/model. [Hướng dẫn Mac](README_MAC.md) có cấu hình .env, nguồn riêng, MLX và OCR.

## Sử dụng ba chức năng

1. **Tra cứu:** mở chế độ tra cứu, tìm từ xuất hiện trong tài liệu, ví dụ `học tập` ở demo. Kết quả có tên nguồn, trang/phần và trích đoạn; PDF có nút mở trang, Markdown hiện phần văn bản.
2. **QA:** khi model QA đã sẵn sàng, hỏi nội dung từ nguồn đã lập chỉ mục. Đối chiếu câu trả lời với trích dẫn; thiếu nguồn hoặc model phải xử lý theo thông báo. Test mock không chứng minh độ chính xác model.
3. **Lịch ảnh:** chọn ảnh thời khóa biểu, đọc bản nháp, chỉnh thông tin chưa rõ và xác nhận trước khi lưu/xuất lịch. Có ảnh tổng hợp ở `tests/fixtures/timetables`; không dùng lịch cá nhân làm dữ liệu training.

Trong QA, chọn **Tóm tắt nhanh**, **Giải thích từng bước** hoặc **So sánh khái niệm** trước khi gửi. Lựa chọn áp dụng cho câu hỏi tiếp theo và được giữ đến khi đổi; giới hạn token cấu hình qua `ANSWER_QUICK_MAX_TOKENS`, `ANSWER_STEPS_MAX_TOKENS` và `ANSWER_COMPARE_MAX_TOKENS`. Khi chưa đủ căn cứ, phản hồi nêu trạng thái và phạm vi đã tìm; các nút mở rộng phạm vi sẽ giữ câu hỏi và gửi lại với phạm vi mới. Với phiên dài, ứng dụng báo khi một phần lịch sử không nằm trong ngữ cảnh lượt hiện tại. Có thể đưa câu hỏi gần nhất vào ô nhập hoặc chọn một trang đã được trích dẫn; việc chọn lại trang chỉ đặt phạm vi tìm kiếm, hệ thống vẫn truy xuất và kiểm tra citation mới cho câu hỏi hiện tại. Nút **Mới** bắt đầu chủ đề không kèm lịch sử phiên trước.

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
| corpus/ | Quy ước metadata bàn giao; chưa có importer tự động |

`.env.example` mô tả các biến launcher đọc; `.env` không commit. `src/` là nguồn thật; `data/` giữ DB, vector, model, lịch/hội thoại/cache. Bàn giao nguồn/model qua gói có version/hash ở kho được kiểm soát; không merge SQLite/weights qua Git và không dùng `git add -f` vượt ignore. [Corpus](corpus/README.md) mô tả ranh giới này.

API tự mô tả tại `http://127.0.0.1:8000/docs`. Kiểm trạng thái qua `scripts/dev.py check --profile demo` hoặc `--profile local`; thêm `--require-model` nếu cần lệnh thất bại khi model QA chưa sẵn sàng. Lệnh check không chứng nhận chất lượng model hay toàn corpus. Lỗi runtime hiện tại hiển thị trong terminal đang chạy ứng dụng.

## Kiểm thử và đóng góp

Trên Mac:

```bash
.venv/bin/python -m pip check
.venv/bin/python -m pytest -c pytest.windows.ini
node --test tests/document_lookup_ui.test.cjs
```

Node 22 dùng cho test giao diện, không cần để chạy web; cài từ [Node.js](https://nodejs.org/en/download). Windows dùng `test_windows.ps1` và lệnh Node tương tự. CI [Team feature checks](https://github.com/ng-wngkh07/Study-Agent/actions/workflows/team-checks.yml) kiểm Python 3.12 Windows/Mac, demo, feature tests, test giao diện; Windows thêm PowerShell/installer DemoOnly. PR kiểm nhật ký mới.

Đọc [CONTRIBUTING.md](CONTRIBUTING.md) để báo lỗi, tạo branch/PR và review; [quy tắc](README_QUY_TAC.md), [phân công 6 người](docs/team/PHAN_CONG_6_NGUOI.md), [PDF](output/pdf/PHAN_CHIA_NHIEM_VU_DO_AN_NHOM.pdf) và [nhật ký](LICH_SU_DU_AN.md) ghi trách nhiệm/lịch sử. Main cần các checks bắt buộc; cấu hình kiểm tại YC-176 không bắt buộc approval. Nhóm vẫn review chéo và TV4 merge theo quy trình; người có quyền ghi cũng có khả năng merge trên GitHub.

## Giới hạn và giấy phép

CI/demo không nghiệm thu bộ cài full trên máy Windows mới, QA/VLM thật, corpus readiness hoặc training. MLX không cài trong môi trường Windows. Chỉ TV4 ghi corpus/index/vector chính và training; cần audit live `complete: true`, review/provenance toàn nguồn và đánh giá candidate độc lập trước kích hoạt. Các bước đã/chưa chạy ghi trong nhật ký và báo cáo nhóm.

Mã nguồn theo [MIT](LICENSE). Giấy phép mã không cấp quyền chia sẻ sách, ảnh cá nhân hoặc model của bên thứ ba; kiểm quyền và giấy phép riêng của từng nguồn/model.
