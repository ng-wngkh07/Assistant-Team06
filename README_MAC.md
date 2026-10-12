# Cài và chạy Agent Học Tập trên Mac

Hướng dẫn áp dụng cho clone mới. Giữ môi trường/model của máy đang chạy; không tạo lại .venv/.train-venv hay cài nâng cấp vào job đang hoạt động. [README chính](README.md) mô tả kiến trúc và ba chức năng; [CONTRIBUTING](CONTRIBUTING.md) hướng dẫn phát triển.

## Web và demo: Python 3.12

Cài Git và Python **3.12** từ [Python macOS](https://www.python.org/downloads/macos/) hoặc công cụ quản lý môi trường bạn đang dùng. Kiểm `git --version`, `python3.12 --version` trước khi làm. Bộ web/CI dùng Python 3.12; không dùng `python3` bất kỳ rồi mặc định đã tương thích.

```bash
git clone https://github.com/ng-wngkh07/Study-Agent.git
cd Study-Agent
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python scripts/dev.py demo
.venv/bin/python scripts/dev.py serve --profile demo
```

Mở `http://127.0.0.1:8000`, dừng Ctrl+C. Demo tạo index riêng tại `data/team-demo`; tra cứu FTS chạy không model. QA/đọc ảnh thật cần phần Ollama bên dưới. Người chỉ chạy web có thể cài `requirements.txt`; người phát triển cài `requirements-dev.txt` để có pytest/httpx.

## Ollama và nguồn riêng

1. Cài [Ollama macOS](https://ollama.com/download/mac), mở ứng dụng. Nếu không dùng ứng dụng nền và chưa có server, chạy `ollama serve` trong terminal khác; không khởi chạy server thứ hai khi cổng đã có dịch vụ.
2. Chuẩn bị model phát triển từ manifest hiện tại:

```bash
ollama pull qwen2.5:3b
ollama pull bge-m3
ollama pull qwen2.5vl:3b
ollama list
```

Đây là model Ollama để phát triển, khác runtime/model MLX riêng. Tải model cần nhiều GB dung lượng; kiểm dung lượng trước khi tải. Tham khảo [Ollama Quickstart](https://docs.ollama.com/quickstart).

3. Nếu chưa có `.env`, sao chép `.env.example` thành `.env`; giữ cấu hình riêng khi file đã tồn tại. Với clone phát triển độc lập, sửa các dòng đường dẫn để tránh dùng corpus chính:

```dotenv
AGENT_SRC_DIR=src
AGENT_DATA_DIR=data/team-local
HISTORY_DB_PATH=data/team-local/history.db
TIMETABLE_DB_PATH=data/team-local/timetable.db
TIMETABLE_IMAGES_DIR=data/team-local/timetable_images
OLLAMA_BASE_URL=http://localhost:11434
DEFAULT_CHAT_MODEL=qwen2.5:3b
DEFAULT_EMBED_MODEL=bge-m3
OLLAMA_AUX_CHAT_MODEL=qwen2.5vl:3b
HOST=127.0.0.1
PORT=8000
```

Launcher chỉ nhận biến trong `.env.example` và các đường dẫn history/timetable trên; biến process env đã có được ưu tiên. Đường dẫn tương đối tính từ gốc repo. Chỉ riêng profile demo ghi đè đường dẫn về dữ liệu tổng hợp. `run.py` chạy trực tiếp không tự nạp `.env`; hướng dẫn này dùng launcher và nút UI để giữ cấu hình nhất quán.

4. Nhận gói nguồn có version/hash/quyền từ nhóm, đối chiếu trước khi đặt PDF/DOCX/TXT/MD/ảnh vào `src/` của clone riêng. Có thể chép nguồn tổng hợp để thử quy trình, nhưng không coi là dữ liệu thật đã được duyệt. Không copy riêng DB có đường dẫn máy khác rồi mặc định hoạt động.
5. Kiểm môi trường và chạy:

```bash
.venv/bin/python scripts/dev.py check --profile local --require-model
.venv/bin/python scripts/dev.py serve --profile local
```

6. Trên web chọn **Đồng bộ thư viện**, đợi tác vụ xong rồi kiểm nguồn/trang/đoạn và tra cứu một cụm có trong nguồn. `serve --profile local` không tự index. Vector cần model embedding; OCR/model lỗi phải xem thông báo/log và xử lý, không đoán nội dung. Thử QA có trích dẫn và đối chiếu nguồn. Các thao tác trên clone riêng không thay việc TV4 duyệt/hợp nhất corpus chính.

## Runtime MLX riêng

Chỉ dành cho người điều phối trên **Mac Apple Silicon**, Python **3.13 native arm64**, macOS **14 trở lên** theo [MLX](https://ml-explore.github.io/mlx/build/html/install.html). Gói wheel có thể yêu cầu OS cao hơn; nếu pip không tìm thấy wheel, kiểm OS/kiến trúc và metadata gói, không tự chuyển sang Rosetta. Profile này không hỗ trợ Windows/Intel; các marker sẽ bỏ qua gói trên nền tảng khác.

Trên clone chuẩn bị mới, sau khi đã có Python 3.13:

```bash
python3.13 -m venv .train-venv
.train-venv/bin/python -m pip install -r requirements-mlx.txt
.train-venv/bin/python -m pip check
.train-venv/bin/python -c "from importlib.metadata import version; print({n: version(n) for n in ('mlx', 'mlx-lm', 'transformers')})"
```

Runtime tách khỏi `.venv` web. Profile khai MLX, MLX-LM và Transformers cho runtime và JSONL local đã duyệt của dự án; constraints cố định bộ phiên bản, không tự cấp quyền training. Model nền nằm ở `data/models/qwen2.5-3b-4bit`, cần config/tokenizer/weights đầy đủ; model/revision theo `app/config.py`, tải/nhận qua gói có hash riêng. Registry chọn adapter đã được duyệt; không tự sửa registry hoặc bật adapter để làm cho demo chạy.

Mac dùng mặc định model MLX khi không ghi đè `DEFAULT_CHAT_MODEL`; `.env.example` chọn Ollama để phát triển. Chọn backend theo môi trường dự án đã thống nhất, không đổi model đang hoạt động chỉ để chạy test. Có thư viện/model không đồng nghĩa corpus sẵn sàng: TV4 chỉ training sau audit live `complete: true`, provenance/review/split/tokenizer đạt và đánh giá độc lập. Không chạy training theo việc cài môi trường này.

## OCR tùy chọn

VLM dùng Ollama `qwen2.5vl:3b`. Các luồng OCR bảng trên Mac có thể dùng Apple Vision helper ở `data/runtime/apple_vision_ocr`; nó không được commit. Trên clone riêng khi đã cài Xcode Command Line Tools và có `swiftc`:

```bash
mkdir -p data/runtime
swiftc scripts/apple_vision_ocr.swift -o data/runtime/apple_vision_ocr
```

Tesseract là backend tùy chọn; chuẩn bị binary và dữ liệu ngôn ngữ `vie`, `eng` từ [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html), kiểm `tesseract --list-langs`. Không tự thay VLM bằng OCR khác rồi báo là kết quả model. Luôn xem/chỉnh nội dung khó đọc trước lưu/review.

## Kiểm thử và xử lý lỗi

Cài [Node 22](https://nodejs.org/en/download) nếu đóng góp UI. Chạy từ gốc repo:

```bash
.venv/bin/python -m pytest -c pytest.windows.ini
node --test tests/chat_input_ui.test.cjs tests/document_lookup_ui.test.cjs tests/practice_ui.test.cjs
```

| Hiện tượng | Kiểm tra/cách xử lý |
| --- | --- |
| Import lỗi/không có pytest | Dùng đúng Python .venv, cài lại profile dev theo constraints, chạy pip check; không cài vào Python toàn máy |
| Cổng web 8000 đã dùng | Dừng đúng server của mình hoặc chọn PORT khác trong .env; không kill dịch vụ khác |
| Ollama/model chưa sẵn sàng | Kiểm endpoint 11434, ollama list và scripts/dev.py check --profile local --require-model; FTS/demo không cần model |
| Không tìm được nguồn mới | Kiểm đường dẫn src/data, đồng bộ thư viện, xem lỗi extraction/index và test một cụm thực có trong nguồn |
| File .env báo Invalid setting | Chỉ dùng biến launcher hỗ trợ, giá trị không rỗng, không chép đường dẫn/token vào Git |
| Thiếu model/runtime MLX | Kiểm .train-venv và gói model/provenance; không bật adapter chưa duyệt hoặc bỏ gate |

Ghi lỗi terminal, lệnh, OS/Python/profile, commit và cách tái hiện khi báo lỗi; che bí mật. Feature/CI không thay QA/VLM thật, bộ cài full Windows hoặc nghiệm thu toàn bộ corpus/training.
