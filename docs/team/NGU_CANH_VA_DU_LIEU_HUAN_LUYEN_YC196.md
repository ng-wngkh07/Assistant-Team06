# Ngữ cảnh và dữ liệu huấn luyện — YC-196

Cập nhật 11/10/2026. Model ứng dụng: Qwen2.5-3B-Instruct 4-bit, revision `4f83f8f146fdf28b512a06562b671d7af4fab457`. Bản gốc, tokenizer và PDF nguồn được giữ nguyên.

## Chọn phương pháp theo vấn đề

| Phương pháp | Tác dụng | Áp dụng ở dự án |
|---|---|---|
| Tăng cửa sổ trong khả năng gốc | Cho phép model đọc nhiều token trong một lần gọi | Đo 8K và 16K; tối đa gốc 32.768, kiểm cả đầu vào và phần dự phòng đầu ra |
| RAG chọn đoạn liên quan | Đưa tài liệu cần thiết vào từng câu hỏi | Giữ provenance trang/đoạn/mã nguồn và giới hạn phần trích |
| Tìm lại lượt gốc cùng phiên | Đỡ mất chủ đề cũ khi lịch sử gần không chứa nội dung đó | Chọn lượt hỏi–đáp gốc liên quan; không dùng mã nguồn cũ làm chứng cứ hiện tại |
| Tóm tắt phiên | Giúp định hướng chủ đề với ít token hơn | Bản tóm tắt chỉ nội dung hội thoại, có thể thiếu chi tiết; không thay bằng chứng sách |
| KV cache/rotating cache | Giảm tính lại hoặc giới hạn bộ nhớ khi sinh | Không tự bảo đảm nhớ phần đã loại khỏi cache |
| RoPE scaling/YaRN và continued pretraining | Có thể mở rộng ngoài cửa sổ gốc khi kiến trúc/runtime/dữ liệu phù hợp | Chưa áp dụng; tài liệu Qwen liệt kê mở rộng YaRN cho 7B/14B/32B/72B, không phải cam kết cho 3B |
| LoRA học nhiệm vụ | Học cách trả lời, sinh/kiểm câu hỏi và giữ điều kiện nguồn | Không tự tăng cửa sổ vị trí; độ dài mẫu học phải được kiểm bằng tokenizer thực tế |

[Model card Qwen2.5-3B](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct) công bố ngữ cảnh 32.768 token. Không lấy giới thiệu 128K của cả họ model hoặc `model_max_length` trong tokenizer để suy giới hạn cho model này. [Tài liệu Qwen về mở rộng ngữ cảnh](https://qwen.readthedocs.io/en/stable/getting_started/concepts.html) nêu các kích thước được hỗ trợ cho YaRN. [MLX-LM LoRA](https://github.com/ml-explore/mlx-lm/blob/main/examples/lora_config.yaml) tách `max_seq_length` khi học khỏi giới hạn ngữ cảnh khi suy luận; [trainer](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/tuner/trainer.py) có thể cắt mẫu vượt độ dài cấu hình, nên dự án phải kiểm trước và không cho cắt âm thầm.

## Phép đo thực tế trên máy 16 GiB

Bản base không adapter, cùng tokenizer và template, giải mã temperature 0, dành 32 token trả lời. Thông tin cần tìm được đặt ở đầu, giữa và cuối đoạn nhiễu tiếng Việt. Đây là kiểm khả năng tìm thông tin tổng hợp, chưa phải đánh giá độ đúng trên corpus.

| Cửa sổ yêu cầu | Token đầu vào thực | Đúng | Thời gian mỗi câu | MLX đỉnh |
|---|---:|---:|---:|---:|
| 8.192 | 7.064 | 3/3 | 41,57–42,93 giây | 2,424 GiB |
| 16.384 | 14.207 | 3/3 | 93,88–97,70 giây | 2,700 GiB |

16K có thể dùng như lựa chọn local sau khi cổng ứng dụng được kiểm. Giữ cấu hình mặc định 8K để tương thích; 32K chưa đo trên máy hiện tại. Lịch sử được đưa vào theo cặp nguyên vẹn và ngân sách byte bảo thủ; ở ranh giới model, tokenizer phải xác nhận `input_tokens + max_tokens <= min(requested_window, native_window)` trước khi nạp trọng số. Cấu hình gốc không xác minh được hoặc ngân sách không dương phải bị từ chối.

## Chuẩn bị dữ liệu

Đợt này rà lại 36 đoạn ở 6 môn và 45 bài tập nháp. Các đoạn khớp PDF, hash nguồn, trang được duyệt, chunk/vector và chuỗi trích liên tục. Nguồn train/validation/holdout tách biệt. Audit corpus hiện tại đạt `complete:true` theo đúng 13 ngoại lệ YC-188; `complete_all_sources:false` vẫn được giữ rõ.

Bản mới cần dùng lời gọi thật của runtime cho 11 nhiệm vụ: hỏi đáp có căn cứ, thiếu bằng chứng, sinh câu hỏi, kiểm câu hỏi đúng/sai, tóm tắt phiên, kiểm nhận định đúng/sai, giải bài và kiểm lời giải đúng/sai. Các cổng admission, bảo mật đầu vào, chọn nguồn, ngân sách và quản lý GPU được kiểm bằng mã; không biến chúng thành khả năng được đảm bảo bằng fine-tuning. VLM dùng model khác, ngoài đợt LoRA văn bản này.

Đã sửa bản nhãn mới để tóm tắt không thêm ý ngoài transcript, không mang mã nguồn cũ thành bằng chứng. Các bài dẫn xuất được ghi rõ nguồn gốc, không nhận là bài nguyên văn trong PDF. V20 và các bản bị từ chối trước đó được giữ nguyên. Chưa duyệt huấn luyện chỉ từ bản nhãn: còn phải đối chiếu đầu vào/đầu ra đã serialize, độ dài token, cân bằng môn/nguồn, baseline cùng exact base và tiêu chí đánh giá trước/sau.

## Giới hạn tài nguyên học

Lần đo trước học: RAM vật lý 16 GiB, khả dụng 4,25 GiB, swap đang dùng khoảng 3.196 MiB. Cổng hiện tại của dự án cho tối đa 1.024 token mỗi mẫu, batch 1, tối đa 4 lớp LoRA và gradient checkpointing. Cửa sổ suy luận 16K không phải bằng chứng cho khả năng học mẫu 16K. Nếu dữ liệu đúng runtime vượt 1.024 token, phải giữ mẫu đầy đủ và ghi cổng chưa đạt; không rút điều kiện nguồn, sửa số đo hoặc bỏ cổng để chạy được.

Huấn luyện thật chỉ được bắt đầu sau khi mọi cổng đạt. Adapter thử nghiệm lưu ở output mới, có ledger/checkpoint riêng và so sánh cặp với base; không tự kích hoạt adapter, commit/push hoặc đổi model/provider.

## Dữ liệu và mốc so sánh hiện hành

Cập nhật 11/10/2026 23:50 (Asia/Ho_Chi_Minh). Bộ v26 capture đúng đầu vào của 11 tác vụ; v27 là bản đóng gói để xét lượt học thử, gồm 264 mẫu học và 132 mẫu kiểm tra từ 18 nhóm nguồn nội dung tách nhau. Đã sửa nhãn kiểm chứng âm thành nhận định đầy đủ, giữ điều kiện trong đáp án, trích đoạn liên tục cho auditor và tóm tắt đúng hai lượt hội thoại. Đây là bộ thử sửa hành vi, chưa đủ quy mô nghiệm thu huấn luyện toàn corpus.

Đếm bằng tokenizer và chat template của model thật: 396/396 mẫu dài 256–892 token, không cắt nguồn hoặc đáp án ở giới hạn học 1.024 token. Ngữ cảnh suy luận 16K và giới hạn chuỗi học 1.024 là hai cấu hình khác nhau; tăng ngữ cảnh suy luận không tự tạo khả năng học với chuỗi dài hơn trên máy 16 GB.

Mốc base đã sinh đủ 18 đầu ra từ 6 đoạn giữ riêng thuộc 3 tài liệu không dùng để học. Codex đọc từng đầu ra đối chiếu nguồn: 0 đạt đầy đủ hợp đồng runtime, 4 đạt một phần nội dung và 14 không đạt. Thiếu citation, bỏ điều kiện, trích dẫn không nguyên văn, kiểm sai nhận định và một câu lặp đến giới hạn token là các lỗi đã tái hiện. Phép đo này dùng prompt gọn trong snapshot, chưa đủ căn cứ thay prompt đang phục vụ. Không suy kết quả này thành độ chính xác toàn model/corpus.

Bộ so sánh cặp giữ nguyên 18 đầu vào và cấu hình giải mã, gồm 9 ca mục tiêu và 9 ca giữ lại. Mỗi nhóm lỗi phải đạt ít nhất 80% và tăng ít nhất 5 điểm phần trăm; không chấp nhận hồi quy trên ca đã đúng. Vì base trong bộ prompt gọn chưa có ca đạt đầy đủ ở nhóm giữ lại, kết quả nhóm này không chứng minh bảo toàn toàn bộ năng lực cũ. Huấn luyện và kích hoạt chỉ được ghi hoàn tất khi có hồ sơ chạy và kiểm chứng thật; không dùng loss hoặc test xanh thay đánh giá nội dung.

## Thực thi lượt học thử

Lượt `run-20261011-165538-85ce94` bắt đầu 11/10/2026 23:56:02 (Asia/Ho_Chi_Minh), dữ liệu v27, model Qwen2.5-3B-Instruct 4-bit đúng revision đã khóa. Cấu hình: 80 bước, batch 1, 4 lớp LoRA, learning rate 0,00002, chuỗi tối đa 1.024 token, gradient checkpointing và chỉ học phần đáp án. Log xác nhận 1,663 triệu tham số được học (0,054% tổng tham số).

Môi trường ứng dụng hiện có điều phối audit PDF và khóa GPU; môi trường `.train-venv` chạy token audit chính xác và tiến trình MLX. Không cài thêm thư viện hoặc trộn thư viện giữa phiên bản Python. Cổng nguồn, dữ liệu, model và tài nguyên được chạy lại trước lượt học, cùng monitor dừng có kiểm soát khi áp lực RAM/swap vượt ngưỡng. Chưa coi tiến trình đang chạy là huấn luyện hoàn tất hoặc chất lượng đã cải thiện.

## Kết quả lượt thử đầu và đối chứng

Lượt 80 bước hoàn tất lúc 12/10/2026 00:07:27, return code 0, 685 giây; loss xác thực cuối 0,458, MLX đỉnh 3,332 GiB, RAM khả dụng thấp nhất 1,75 GiB, swap tăng 1.029,44 MiB, monitor không dừng. Adapter hữu hạn và đúng cấu trúc; dữ liệu/PDF/model nền giữ nguyên hash.

So sánh cùng 18 đầu vào và giải mã: adapter sinh đủ 18 câu kết thúc stop, nhưng review nội dung cho 0 đạt, 3 một phần và 15 không đạt. Hai nhóm lỗi đều chưa đạt ngưỡng 80%; không kích hoạt. Năm trong sáu nhận định âm bị chứng nhận supported sai; các quote thường bị nối dòng hoặc viết lại. Không suy chất lượng tăng từ thời gian sinh ngắn hơn hoặc mất lỗi lặp.

Kiểm thêm sáu mẫu train cho 1 đạt, 1 một phần và 4 không đạt; cả hai nhận định âm vẫn sai. Theo iterate_batches của runtime, 80 bước batch 1 chưa đi qua đủ 264 mẫu. Lượt đối chứng mới dùng cùng bytes dữ liệu, nhãn, base và tham số, chỉ tăng lên 264 bước để kiểm giả thuyết thiếu lượt học. Loss xác thực tăng từ bước 40 đến 80 cũng là dấu hiệu cần theo dõi; chưa xác nhận tăng bước sẽ chữa lỗi. Bộ 18 đã xem kết quả được coi là bộ chẩn đoán phát triển, không dùng riêng để nghiệm thu tổng quát hóa hoặc kích hoạt model.

## Kết quả lượt đối chứng 264 bước

Cập nhật 12/10/2026. Lượt264bước hoàn tất00:47:33,1.519giây,returncode0; validationloss cuối0,614. Kiểm artifact/binding/nguồn/base sau học đạt. Bộ phát triển18cho1đạt/2mộtphần/15khôngđạt; bộ mới18từ6PDFcho2đạt/2mộtphần/14khôngđạt. QA mục tiêu2/3, auditor0/6 ở bộ mới, dưới ngưỡng80%; không kích hoạt. Model vẫn trả sai constructor trảvoid, thay mức chắc chắn và nối/viết lại quote. Học đủ một lượt chưa đủ sửa hành vi; không suy từ loss hay hết bước. Bộ mới đóng băng sau học bắt đầu/trước xem output, tách theo hashPDF và chỉ QA/auditor, không chứng minh độc lập toàn họ nguồn hoặc toàn11nhiệm vụ. Xem [báo cáo YC197](RA_SOAT_TOAN_BO_CHUC_NANG_YC197.md) về chức năng, sửa lỗi và giới hạn còn lại.
