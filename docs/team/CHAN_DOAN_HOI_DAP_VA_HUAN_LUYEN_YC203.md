# Chẩn đoán hỏi đáp và huấn luyện — YC-203

Ngày 12/10/2026, Codex; mã đang chạy ở main `f0180249913cbaac8a5338c8f47869a56a226a0c` sau đồng bộ YC-202. Phạm vi: truy nguồn và tái hiện hai câu “đạo hàm là gì”, “lý thuyết đạo hàm”; kiểm dữ liệu và kết quả huấn luyện; đề xuất thay đổi. Chưa triển khai bản sửa, học thêm hoặc kích hoạt adapter.

## Kết luận có bằng chứng

Nguồn có kiến thức để trả lời. Lỗi nằm ở nhiều tầng: mất công thức khi trích PDF, xếp nguồn không đúng, cắt ngữ cảnh bằng ký tự/byte, mô hình không tuân thủ trích dẫn, và thông báo thất bại che mất nguyên nhân. Huấn luyện trước chưa khắc phục được các hành vi này; ứng dụng vẫn chạy mô hình nền vì các adapter bị từ chối chất lượng.

Bằng chứng riêng được lưu tại `data/evaluation/qa-diagnosis-yc203/`. Nội dung nguồn/prompt/raw output giữ local, không đưa vào Git. Thử trực tiếp agent cùng mã/config/model/retrieval hiện hành trên phiên mới, không lưu lượt thử vào lịch sử người dùng; không thể khôi phục chính xác lịch sử phiên trong ảnh. Hai baseline dùng temperature 0,2, context 8.192, đầu ra tối đa 900 token, mức standard. Khóa dialogue và GPU được giữ trong mỗi phép inference; dịch vụ người dùng vẫn hoạt động.

## 1. Nguồn gốc và chất lượng chỉ mục

SQLite mở chỉ đọc: 157 tài liệu, 52.612 chunk, kiểm nhanh cấu trúc DB đạt. Điều này chỉ chứng minh cấu trúc, không chứng minh nội dung đầy đủ/đúng.

- PDF `src/HCMUS_Vi_tich_phan_1_1B_Ung_dung_giai_bai_tap.pdf`, doc44, trang10: đã render toàn trang và xem trực tiếp. Công thức hiển thị rõ `f′(x₀) = lim_(h→0) [f(x₀+h)−f(x₀)]/h`, ý nghĩa hệ số góc tiếp tuyến và tốc độ biến thiên tức thời. SHA256 nguồn `ea0b41f20e67d9063e57694198b461d59f752e240693a2c32e6546d3b3b28a59` khớp DB. Chunk85523/85524 không có công thức định nghĩa hiển thị trên PDF; bảng/công thức còn bị làm phẳng. Mục3.1 bắt đầu ở ký tự858, ý nghĩa hình học bắt đầu899 trong chunk1.199 ký tự. Lấy đầu900 ký tự sẽ gần như bỏ toàn bộ nội dung giải thích này dù chunk được truy xuất.
- Metadata `corpus_source_pages` của trang này vẫn `text_verified`, không có review_path/review_sha256. Nhãn này không đủ bảo đảm công thức đã được giữ. Phát hiện mới cần mở lại kiểm chất lượng phần công thức trước khi tái dùng làm dữ liệu học; không suy cả corpus sai từ một trang.
- PDF viết tay `src/VTP1 (2).pdf`, doc45, trang1: đã xem toàn trang. Bản viết tay có dấu tương đương giữa khả vi/liên tục và thiếu dấu âm đạo hàm cot; chunk101592 giữ nguyên nội dung rồi thêm một phần hiệu đính Codex riêng có nhãn. SHA256 `48fc9085fa360c0021e64723827997c98b79ee1e88867eeaf5f157c7bacef63e` khớp DB. Không sửa PDF hoặc xóa ghi chép. Cần lưu nguyên văn và hiệu đính thành hai trường/đối tượng có provenance riêng; không để mô hình xem mọi câu trong chunk như các khẳng định toán học cùng độ tin cậy.

Ảnh render phục vụ chẩn đoán ở `/private/tmp/qa-yc203/`; bản ghi chunk và metadata ở `source-page10.json`, `source-review-metadata.json`. Công thức phục hồi chỉ được dùng trong phép đối chứng local, chưa ghi vào DB hay tập approved.

## 2. Tái hiện truy xuất và câu trả lời gốc

| Câu hỏi | Sáu chunk hybrid theo thứ tự | Nguồn được đưa vào prompt | Kết quả |
| --- | --- | --- | --- |
| đạo hàm là gì | 85505,101592,85504,101593,63307,63285 | S1–S4; bỏ2 | generation_failed, không có trích dẫn |
| lý thuyết đạo hàm | 85505,97849,85529,63193,101595,63563 | S1–S4; bỏ2 | generation_failed, không có trích dẫn |

Chunk85505 và85504 là mục lục của doc44/trang3. Chunk63307/63285/63193/63563 thuộc tài liệu lập trình về “hàm”. Trang10 chứa định nghĩa không nằm trong top6 của cả hai câu. Câu thứ hai còn đưa mục lục lập trình vào bốn nguồn đã nhận. Mô hình lấy số trang ghi trong mục lục để mô tả nội dung, dù chưa nhận trang đó.

Nguyên nhân trong `app/searcher.py`: FTS ghép các từ bằng OR và prefix, nên “đạo”/“hàm” riêng cũng khớp; câu “là gì” nhận boost cho “định nghĩa” trong chunk kể cả mục lục. Bộ nhận diện mục lục không nhận ra các chunk có header này. RRF kết hợp hạng, giới hạn đa dạng theo sách và cổng lexical/semantic chưa xác minh rằng đoạn thực sự chứa câu trả lời. FTS-only còn thấy tài liệu đạo đức do chữ “đạo”; đó là diagnostic bổ sung, không nhầm với kết quả hybrid thật.

Đầu ra gốc thứ nhất nêu công thức thiếu ký hiệu giới hạn đầy đủ và diễn đạt “sai số sai số”; thứ hai chỉ tóm tắt các mục/tài liệu. Cả hai hoàn tất `stop`, không bị cắt đầu ra, không có lỗi inference. Cả hai thiếu `[S#]`. `app/rag_agent.py` loại toàn bộ câu trả lời khi không có mã nguồn hợp lệ, rồi trả cùng thông báo “chưa đủ thông tin”. Trong hai ca này, nguyên nhân trực tiếp của thông báo là **thiếu trích dẫn trong đầu ra**, không phải không tìm được bất kỳ tài liệu nào. Không nên bỏ cổng này hoặc tự gắn nguồn cho nội dung chưa kiểm.

Hai câu tiếng Việt hợp lệ qua validator hiện hành. Ảnh không cho thấy câu đã gây lỗi emoji trước đó, nên chưa có căn cứ quy lỗi validator cho hai câu này.

## 3. Ngữ cảnh bị loại quá sớm

`app/rag_agent.py` lấy đầu tối đa900 ký tự/chunk; `app/dialogue.py` dùng số byte UTF-8 làm dự toán token rồi chọn nguồn tuần tự. `app/mlx_infer.py` đã có guard tokenizer thật trước khi tải trọng số.

| Câu hỏi | Dự toán hiện hành | Input tokenizer thật đã nhận | Input tái dựng đủ6 đoạn | Đủ6 +900 output |
| --- | ---: | ---: | ---: | ---: |
| đạo hàm là gì | 7.271 | 2.259 | 2.750 | 3.650 <8.192 |
| lý thuyết đạo hàm | 6.699 | 1.940 | 2.762 | 3.662 <8.192 |

Đếm đúng template `tokenize=False` rồi `encode` như runtime, từ tokenizer local cùng model. Bản đủ6 là tái dựng offline các đoạn900 ký tự với cùng câu hỏi/mode; không phải thử sinh đã nghiệm thu. Nó cho thấy thông báo “không vừa cửa sổ” ở hai ca này do dự toán quá bảo thủ. Thêm đủ6 nguồn vẫn chưa giải quyết được xếp hạng sai; giữ nguồn tốt trước rồi mới tối ưu token.

## 4. Phép đối chứng bằng mô hình hiện hành

Không đổi weights/adapter/context/temperature. Thay kết quả tìm bằng một đoạn liên quan của doc44/trang10, bỏ phần L'Hôpital phía trước và thêm đúng công thức đã xem trên PDF, chạy lại cả hai câu với pipeline/system prompt hiện hành:

- Hai đầu ra đã nêu đúng công thức/ý nghĩa ở mức cơ bản, nhưng vẫn không có `[S1]`; ứng dụng tiếp tục trả fallback cho cả hai.
- Giữ chính đoạn nguồn đó, dùng hướng dẫn ngắn và cụ thể yêu cầu trả2–4 câu kèm `[S1]`: đầu ra có công thức giới hạn, ý nghĩa hình học/ứng dụng và hai mã `[S1]`. Không cắt đầu ra. Mã được đặt đầu đoạn thay vì cuối nhận định, còn cần kiểm định trình bày/grounding đầy đủ.

Phép đầu tiên thay cả lựa chọn đoạn và phục hồi công thức, không tách riêng hiệu quả của hai yếu tố. Phép hướng dẫn ngắn giữ nguồn đối chứng đó, cho thấy hướng dẫn có ảnh hưởng đến việc trích dẫn. Tổng5 đầu ra là diagnostic, không có lặp nhiều seed, không có holdout kín mới và không phải nghiệm thu sản phẩm. Không khẳng định prompt ngắn giải quyết toàn QA hoặc mọi lỗi bảo mật. Evidence `baseline-*.json`, `selected-*.json`, `concise-prompt.json` giữ cả prompt/raw/context.

## 5. Kiểm lại quá trình huấn luyện

Registry live `status:unadapted_base`, `active_adapter_id:null`, không có adapter accepted. Model Qwen2.5-3B-Instruct-4bit local; đồng bộ Git không tự đưa adapter chưa đạt vào vận hành.

| Bộ/lượt | Dữ liệu thực | Phạm vi và kết quả đã đánh giá |
| --- | --- | --- |
| v27/trial01 | 264train/132valid,396 dòng,36 ngữ cảnh,11 nhiệm vụ;80 bước | Bộ development18:0PASS,3partial,15FAIL; bị từ chối |
| v28/trial02 | Cùng bytes/dataset hash v27;264 bước;LoRA4 lớp,rank8,LR2e-5,maxseq1024 | Development1/18PASS; bộ fresh2/18PASS; DO_NOT_PROMOTE |
| v31/YC200 | 64train/24valid,88 dòng;16exercise_solve và72practice_verify;128 bước | 8 bài Phelps×2 dạng đầu ra: candidate6/16PASS; đúng cả hai2/8; giữ3 ca cũ2/3,1 hồi quy nghiêm trọng; DO_NOT_PROMOTE |

Đếm lại manifest: v27/v28 có24 mẫu grounded_qa train trên sáu môn, chỉ4 mẫu calculus. Bốn câu calculus lần lượt về họ hàm, liên tục/khả vi, so sánh tích phân và tiêu chuẩn căn; không có câu định nghĩa đạo hàm cơ bản. Câu đạo hàm được tìm thấy ở grounded_qa nằm valid và là bài tính cụ thể. Các mẫu còn lại không phải264 câu QA độc lập. V31 không có nhiệm vụ grounded_qa, không phải bộ sửa lỗi định nghĩa có nguồn.

Dataset hash v27/v28 cùng `7d70b71624a54c4cff0e7afe3d7d04ada63e9bfe52959b8ee76a095e39aa16d0`; v31 `76137ee908d9f8950dc19aa710b364d37830cd7056d9231e598ab787a4634dcb`. Hash adaptertrial02 `e947add0…`, pilot `f387b446…` được đọc lại và khớp hồ sơ đánh giá. Không có source_context_id hoặc PDFhash chung train/valid; v31 còn có source_family disjoint theo metadata. V28 thiếu khóa source_family, nên hashPDF khác chưa chứng minh toàn họ nguồn độc lập.

Audit token đóng băng v28 ghi256–892 token, không vượt1.024/không mất target do clipping. Hồ sơ integrity sau học giữ base/dataset, quá trình kết thúc đủ bước. Không có bằng chứng lỗi cắt mẫu là nguyên nhân ở hai trial này. “approved_for_training:false/training_started:false” trong bản exact-token audit là snapshot trước gate, không phải phủ nhận ledger đã học; ready vẫn unreviewed_not_promoted, quyết định chất lượng mới là hồ sơ chốt. Không lấy completion/loss/artifact finite thay nghiệm thu. Kết quả bài tập YC200 chỉ có holdout tâm lý, chưa chứng minh chất lượng sáu môn.

Kết luận huấn luyện: dữ liệu QA ít, phạm vi khác lỗi báo cáo; tăng80→264 bước trên cùng dữ liệu không đạt yêu cầu; pilot bài tập còn lỗi placeholder, điều kiện và verifier. Cần sửa pipeline/source và làm dataset chẩn đoán mới trước một trial mới, giữ adapter bị từ chối ngoài runtime.

## 6. Thứ tự thay đổi đề xuất

1. **Mở lại kiểm nguồn toán học có công thức.** Tách nội dung/tiêu đề/mục lục/formula/table; phục hồi LaTeX từ ảnh khi text layer mất, review người và hash trang/span trước duyệt. Tách nguyên văn với hiệu đính/giải thích; chỉ mục truy xuất biết loại/độ tin cậy. Bắt đầu doc44/trang10 và doc45/trang1, rồi audit mẫu đại diện và mở rộng theo lỗi; chưa chỉ định toàn corpus approved từ nhãn text_verified.
2. **Sửa chọn nguồn trước sinh.** Ưu tiên cụm “đạo hàm”, bỏ từ hỏi thông dụng cho scoring nhưng giữ câu gốc; giảm điểm mục lục/header/đoạn chỉ nhắc tên; lấy thêm ứng viên rồi xếp lại theo khả năng trả lời và điều kiện. Không loại cứng mọi tài liệu khác môn khi câu liên ngành. Giữ mapping chunk–trang–span; đo Recall@K của đoạn chứa đáp án và độ đúng nguồn trước đo chất lượng model. Hybrid rồi reranking là kiến trúc được mô tả trong [tài liệu Qdrant](https://qdrant.tech/documentation/tutorials-basics/reranking-hybrid-search/); đây là phương pháp tham khảo, không đề xuất bắt buộc chuyển DB hay dùng dịch vụ cloud. Với16GB, thử scorer quy tắc nhẹ trước, chỉ benchmark reranker nhỏ nếu cần.
3. **Đóng gói ngữ cảnh bằng tokenizer thật.** Giữ8.192 và reserve output, chọn đoạn theo relevance/ranh giới mục; không cắt đầu900 ký tự làm mất định nghĩa cuối đoạn. Giữ escape an toàn nhưng tránh escape lặp làm biến dạng công thức. Kiểm input+output+template margin trước weights; metadata UI báo tokenizer thật và đoạn/ý bị bỏ, không chỉ byteestimate.
4. **Rút gọn hợp đồng QA và sửa thông báo thất bại.** Đưa chỉ dẫn trích nguồn sát câu hỏi, thử format câu trả lời/citation rõ và hữu hạn trên bộ test; giữ nguồn/lịch sử là dữ liệu chống injection. Nếu thiếu trích dẫn, phân loại riêng `citation_missing`; có thể thử sửa có giới hạn bằng chính nguồn đã nhận, rồi verify claim/source. Không tự thêm[S1] vào đáp án chưa grounded, không bỏ cổng; UI phân biệt thiếu nguồn, thiếucitation, truncation, context và inferenceerror để người dùng không bị yêu cầu hỏi rõ hơn một câu đã rõ.
5. **Huấn luyện hướng vào lỗi sau khi các tầng trên đạt.** Dataset groundedQA bao phủ định nghĩa/điều kiện/công thức/câu ngắn và dài/sáu môn, có nhiều câu paraphrase và nhiều nguồn liên quan+lẫn nhiễu; giữ S1/S2 đa dạng. Thêm ví dụ từ chối đúng, nguồn viết sai có hiệu đính, giữ domainconditions. Trọng tâm QA tách khỏi bài tập/verifier nếu xung đột qua đo retention; chưa kết luận cần nhiều adapter. Tất cả biến thể/cùng bài/cùng họnguồn phải cùng split; holdout kín mới trước output, không biến các ca đã nhìn ở YC203 thành holdout. Mẫu học serialize đúng runtime và exacttoken toàn target, không cắt. LoRA4lớp/rank8 chỉ là cấu hình hiện tại; đổi rank/LR/layers hoặc model chỉ sau thí nghiệm một biến và benchmark tài nguyên, không phỏng đoán đó là nguyên nhân chính.
6. **Nghiệm thu độc lập trước activation.** Đóng băng bộ QA và retention mọi chức năng, nhãn/source đã review; đo riêng truy xuất đúng đoạn, đủ công thức, factual/conditions, valid+groundedcitations, trả lời thiếu nguồn đúng, injection, latency/RAM và hồi quy. Đề xuất ban đầu bộQA ít nhất10 ca/môn cộng caâm và nhiều cách diễn đạt, chốt ngưỡng trước output; mục tiêu cho nhóm câu cơ bản có nguồn ≥90% đúng đầy đủ, không hồi quy nghiêm trọng, không gắn nguồn bịa. Đây là ngưỡng đề xuất chưa thay cổng nghiệm thu hiện hành; chỉ activate khi đầy đủ gate nguồn/runtime/dataset/resource và phê duyệt.

## 7. Trạng thái và giới hạn

Phân tích/tái hiện/model đối chứng/review nguồn và hồ sơ học đã làm. Triển khai/handoff sửa mã/training/activation N/A trong lượt này vì yêu cầu chẩn đoán và phương án; chưa coi lỗi sản phẩm đã sửa. Chưa audit trực tiếp toàn157PDF, chưa rerun các holdout cũ hay nghiệm thu multi-seed/Windows/nguồn khác. Lỗi emoji trong ảnh chưa có originalinput để tái hiện. Hash corpusDB/historyDB/registry giữ nguyên qua cả phép thử; cache dịch tách riêng. Không sửa sourcePDF/model/provider, commit/push hoặc mở job nền. Dịch vụ cuối lượt healthy/inference_ready, GPUidle.
