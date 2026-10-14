# Giải pháp cốt lõi cho hỏi đáp đa tài liệu — YC-204

Ngày kiểm tra: 12/10/2026. Mã ứng dụng: main `f018024`. Trạng thái: **đã chẩn đoán đa tài liệu và lập thiết kế sửa; chưa triển khai thay đổi sản phẩm**.

## Kết luận và phạm vi

Lỗi vượt phạm vi vi tích phân. Chuỗi hiện tại có thể làm biến dạng hoặc bỏ mất nội dung trước khi mô hình đọc, rồi coi sự đầy đủ về trang/chunk/vector là bằng chứng nguồn đã đủ chất lượng. Truy xuất và đóng gói tiếp tục làm giảm thông tin; mô hình gốc còn không ổn định về trích nguồn. Cần sửa các hợp đồng dữ liệu và kiểm chứng từng tầng, thay vì thêm ngoại lệ cho một từ khóa hoặc học thêm trên dữ liệu lỗi.

Đã đọc trực tiếp ảnh đầy đủ của sáu trang ở sáu PDF, đối chiếu text layer → normalizer → chunk trong DB; kiểm kê toàn DB 157 tài liệu; chạy sáu truy vấn hybrid trên DB chỉ đọc. Đây là kiểm tra truy xuất, **không phải sáu câu trả lời hoàn chỉnh từ mô hình**. Năm phép sinh thật của YC-203 được dùng làm bằng chứng bổ sung về prompt/citation; chưa có phép sinh đa môn mới hoặc holdout kín trong lượt này.

Bằng chứng riêng: `data/evaluation/general-rag-yc204/` gồm inventory, mẫu nguồn, manifest hash, truy xuất, phép biên dịch và kết quả bảo toàn dữ liệu. PDF, raw text và đầu ra riêng không đưa vào Git. ID/trang dưới đây là locator local, không phải chỉ số trang in trên sách.

## Các lỗi đã xác nhận

| Nguồn / trang PDF | Đối chiếu thực tế | Tầng gây lỗi / giới hạn kết luận |
| --- | --- | --- |
| doc43 / 5 — OOP | PDF/text gốc có `SinhVien`, `hoTen`, `setDiem`; chunk thành `Sinh Vien`, `ho Ten`, `set Diem`. Khối khai báo gốc qua kiểm cú pháp C++17; bản normalized lỗi cú pháp. | Quy tắc tách chữ thường–hoa áp dụng toàn văn và gộp khoảng trắng trong `text_cleaner`; không chỉ ảnh hưởng PDF toán. Phép kiểm là cú pháp khai báo, không chứng minh chương trình hoàn chỉnh chạy đúng. |
| doc140 / 4 — toán rời rạc | Ảnh đọc được “mệnh đề”; text/chunk lưu `Meänh ñeà`, `Ñònh nghóa` và nhiều chuỗi VNI. Với “mệnh đề là gì”, trang định nghĩa này không nằm trong top 6. | Giải mã font/bảng mã chưa đầy đủ; lượng ký tự vẫn đủ nên qua nhánh native. Chưa có đối chứng tái nhúng bản sửa để lượng hóa riêng tác động bảng mã lên thứ hạng. |
| doc132 / 11 — vật lý | Bảng tốc độ/vận tốc có phân số, đạo hàm, vector. Chunk giữ đoạn diễn giải nhưng công thức bị trải thành ký tự rời, ký hiệu font riêng và mất quan hệ hàng/cột. | Trích plain text mất cấu trúc; “có chữ” không chứng minh bảo toàn công thức. Truy vấn vận tốc vẫn tìm đúng trang đầu tiên: lỗi nguồn không đồng nghĩa mọi truy vấn của tài liệu thất bại. |
| doc28 / 557 — tâm lý học | Trang vừa có văn bản vừa có sơ đồ Figure 6.5. Nội dung nằm trong hình không có trong text/chunk, dù chú thích và đoạn văn bên dưới còn. | OCR chỉ tự xét trang ít chữ; nội dung hình trên trang nhiều chữ không được thu hồi. Không kết luận cả định nghĩa điều kiện hóa bị mất vì phần văn xuôi còn diễn giải liên quan. |
| YC-203, doc44 / 10 — vi tích phân | Công thức giới hạn đọc được trên ảnh, không có trong chunk; trang định nghĩa không vào top 6 cho hai câu hỏi thử. Nguồn được chọn lại vẫn thiếu citation ở prompt hiện hành; một đối chứng prompt gọn có citation và nội dung tốt hơn. | Có nhiều lỗi độc lập: nguồn, truy xuất, packing và sinh. Một đối chứng tốt chưa đủ nghiệm thu prompt mới. |

Hai đối chứng tích cực: truy vấn ánh xạ tuyến tính tìm trang định nghĩa doc64/6 ở hạng đầu; trang viết tay doc46/3 có bản review ràng buộc nguồn và nội dung trong chunk. Giải pháp phải giữ những đường xử lý đúng này.

## Quy mô cần kiểm tra, không phải số lỗi đã được duyệt

DB hiện có 157 tài liệu, 22.711 trang, 52.612 chunk. Trong 22.597 trang `text_verified`, có 20.614 trang không gắn `review_path` trong bảng này. Điều đó cho thấy cổng hiện tại không buộc mỗi trang native có hồ sơ review ảnh tại đây; không chứng minh cả 20.614 trang sai hoặc không tồn tại chứng cứ ở nơi khác.

| Chỉ báo để phân luồng review | Chunk | Tài liệu | Trang |
| --- | ---: | ---: | ---: |
| Ký tự vùng font riêng Unicode | 673 | 42 | 653 |
| Chuỗi giống VNI theo một số mẫu dò | 200 | 7 | 199 |
| Nguyên văn và hiệu đính Codex trong cùng chunk | 742 | 48 | 701 |
| Chunk dài hơn giới hạn prefix 900 ký tự hiện hành | 35.048 | 82 | 16.819 |

Các tập có thể giao nhau; không cộng để suy số tài liệu lỗi. Ký tự riêng có thể hợp lệ, chuỗi dò VNI có thể bỏ sót/nhận nhầm, và đoạn dài chưa chắc bị mất ý chính. Không dùng bộ dò này để tự đổi nhãn thành rejected/approved.

Sáu truy vấn: ánh xạ, vận tốc, đóng gói, điều kiện hóa tiếng Việt và classical conditioning đều tìm nguồn có liên quan trong top 6; truy vấn mệnh đề không tìm trang định nghĩa đã kiểm. Một số truy vấn có thêm đoạn khác môn. Do đó cần tăng độ chính xác xếp hạng, không loại cứng tài liệu theo môn hoặc kết luận toàn bộ bộ nhúng hỏng.

## Thiết kế sửa tổng quát

### 1. Một nguồn nội dung chuẩn, nhiều biểu diễn có mục đích

PDF gốc bất biến. Trích theo khối có vị trí và loại: văn xuôi, tiêu đề/mục lục, code, công thức, bảng, hình/chú thích. Mỗi khối có source hash, trang, bbox hoặc span, block ID, phương pháp trích, phiên bản bộ xử lý, hash nội dung và trạng thái review. Không gán bbox giả cho chunk cũ; khối chưa định vị phải được trích/kiểm lại.

Giữ riêng `raw_capture`, `verified_content`, `search_text` và `editorial_content`. Truy xuất có thể dùng biểu diễn chuẩn hóa và từ đồng nghĩa, nhưng bằng chứng trả lời lấy từ nội dung đã kiểm và trỏ lại vùng nguồn. Code giữ nguyên identifier/indentation; toán giữ biến, dấu âm, vector, cận và điều kiện; bảng giữ liên kết ô–hàng–cột. Mô tả hình/OCR và hiệu đính có provenance và độ tin cậy riêng, không tự mang quyền của nguyên văn.

Tái dùng PyMuPDF cho block/word có tọa độ và bảng; plain text đơn thuần có thể sai thứ tự đọc. [Tài liệu PyMuPDF](https://pymupdf.readthedocs.io/en/latest/recipes-text.html). Có thể thử Docling làm backend bổ sung cho code/công thức/hình trên bản thử, đo chất lượng và tài nguyên trước chọn; các chức năng enrichment không tự chứng minh nhận dạng đúng. [Tài liệu Docling](https://github.com/docling-project/docling/blob/main/docs/usage/enrichments.md).

`normalize_vietnamese_text` phải trở thành xử lý có loại nội dung: không áp quy tắc tách camelCase hoặc nối từ cho mọi block. Giải mã VNI/TCVN3 theo bằng chứng font/văn bản và đối chiếu ảnh; chưa chắc thì giữ raw và chuyển review, không tự thay ký tự theo phỏng đoán. Áp dụng nguyên tắc này cho PDF, DOCX và Markdown đang gọi chung normalizer.

### 2. Tách đầy đủ dữ liệu khỏi đúng nội dung

Giữ nguyên cổng hash nguồn–trang–review–chunk–vector hiện có. Thêm kết quả độc lập: `coverage_complete`, `content_quality_pass`, `retrieval_quality_pass`, `answer_quality_pass`. Nhãn `text_verified` cũ chỉ được diễn giải theo phép kiểm thực đã chạy; không tự nâng thành đã đối chiếu mọi công thức/hình.

Trang đủ chữ vẫn phải xét font bất thường, vùng hình/bảng/công thức và sai lệch giữa vùng nhìn thấy với các khối đã thu hồi. Các chỉ báo điều phối review; nghiệm thu nội dung cần ảnh nguồn và kiểm chuyên biệt. Phát hiện lỗi mới phải mở lại các nguồn liên quan trước dùng huấn luyện. Giữ 13 ngoại lệ YC-188 đúng manifest/hash; lỗi mới không tự trở thành ngoại lệ được duyệt. Báo cáo audit cũ `complete:true` vẫn là snapshot cổng cũ, không thay bằng chứng chất lượng mới.

### 3. Chunk theo ý nghĩa, giữ liên kết về nguồn

Thay cắt 1.200 ký tự/overlap 200 bằng khối hoặc mục có quan hệ cha–con. Không chẻ định nghĩa khỏi công thức/điều kiện, chữ khỏi bảng hay code giữa identifier. Khối lớn được tách theo biên hợp lệ và giữ locator của từng phần, liên kết continuation; không yêu cầu nhét cả chương vào một chunk.

Metadata phân biệt mục lục/header/body và nguyên văn/hiệu đính. Hiệu đính đã review có thể hỗ trợ trả lời nhưng phải được dẫn nhãn riêng; không tự biến thành câu sách nói. Giữ các bản review đã hash-bound, không trích lại rồi ghi đè nội dung đã được duyệt.

### 4. Truy xuất đủ rộng, xếp hạng theo bằng chứng trả lời

Giữ hybrid hiện có làm baseline. Sinh ứng viên bằng exact phrase/thuật ngữ, lexical và vector; dùng alias Việt–Anh đã kiểm, bỏ từ hỏi phổ biến ở nhánh lexical nhưng giữ ý định định nghĩa/so sánh/điều kiện. Xếp hạng lại theo câu hỏi và loại block; mục lục/header chỉ hỗ trợ tìm mục, không được chiếm nguồn trả lời khi đã có định nghĩa.

Bổ sung parent section hoặc khối lân cận khi cần công thức/điều kiện; giới hạn theo token, không lấy lân cận vô điều kiện. Đa dạng tài liệu phải phục vụ câu hỏi: câu định nghĩa cần nguồn chính tốt, câu đối chiếu mới cần nhiều nguồn. Benchmark reranker local trước lựa chọn; không buộc đổi DB hoặc dùng cloud. [Tài liệu hybrid/reranking của Qdrant](https://qdrant.tech/documentation/tutorials-basics/reranking-hybrid-search/).

### 5. Đóng gói token và trả lời có kiểm chứng

Chọn đoạn theo nội dung trả lời và đếm token bằng đúng chat template/tokenizer của runtime. Dành chỗ cho câu hỏi, lịch sử có giới hạn, hướng dẫn và đầu ra; giữ vận hành 8.192 token. Không cắt cứng 900 ký tự đầu đoạn. Đơn vị công thức–định nghĩa–điều kiện cần được đưa đủ hoặc báo thiếu nguồn. Citation chỉ ánh xạ các block thật sự đã đưa vào model.

Prompt ngắn, một hợp đồng trả lời rõ: chỉ nêu nhận định được nguồn hỗ trợ, trích ID hợp lệ, phân biệt giải thích/hiệu đính. Đo trên model 3B trước áp dụng. JSON/constrained output là một lựa chọn cần benchmark, không phải bảo đảm chất lượng.

Nếu thiếu citation: tối đa một lượt sửa có giới hạn, cung cấp nguồn đã chọn và yêu cầu ràng buộc; không tự gắn `[S1]` vào câu tùy ý. Nếu vẫn thất bại nhưng có đoạn nguyên văn đã review, phù hợp câu hỏi và đủ ý, có thể hiển thị trích dẫn nguồn với nhãn rõ; không tạo câu trả lời tổng hợp được chứng nhận giả. Nếu chưa có nguồn đủ chất lượng thì báo đúng lý do. Tách `no_relevant_source`, `source_unreviewed`, `context_limit`, `output_truncated`, `citation_missing`, `generation_error`; UI hiển thị thông điệp phù hợp và nguồn/trang để kiểm tra.

Có ID nguồn chưa chứng minh nhận định đúng. Kiểm từng nhận định quan trọng với đoạn chứng cứ/điều kiện; model auditor chỉ là hỗ trợ, không tự duyệt dữ liệu hoặc thay kiểm độc lập.

### 6. Huấn luyện sau khi đầu vào ổn định

Registry vẫn dùng base, adapter YC-196/200 giữ `DO_NOT_PROMOTE`. V27/v28 ít QA; v31 không có QA. Tăng bước/loss không khôi phục công thức hay code đã mất trước retrieval.

Nếu sau sửa nguồn/truy xuất/context/prompt còn lỗi sinh, tạo thử nghiệm QA có định nghĩa, điều kiện, công thức, ví dụ/phản ví dụ, bảng/hình và câu cần nhiều đoạn, cân bằng sáu môn. Trộn dữ liệu giữ các chức năng đã có. Nhãn phải đối chiếu nguồn; tách họ tài liệu và biến thể gần trùng trước sinh nhãn, đóng băng holdout trước xem đầu ra. Tập chẩn đoán đã xem ở YC-203/204 không còn là holdout kín. Chỉ học và kích hoạt sau các cổng nguồn/dataset/runtime/token/tài nguyên và nghiệm thu độc lập hiện hành; không thay đổi chúng bằng thiết kế này.

## Kế hoạch thay đổi có thể kiểm chứng

| Đợt / chủ trì đề xuất | Đầu ra | Điều kiện chuyển đợt |
| --- | --- | --- |
| A — TV4 + TV6 | Inventory toàn kho, manifest lỗi/rủi ro theo dạng nguồn, ca tái hiện gốc. YC-204 đã có inventory DB và sáu mẫu, chưa review toàn kho. | Tái hiện và xác định expected từ ảnh; không gán mọi cờ là lỗi. |
| B — TV4, TV1 | Block schema/normalizer có loại nội dung, quản lý chất lượng và chunk; fixture cho code/font/math/table/image. | Ca RED nguồn hiện tại → GREEN; raw nguồn không bị sửa; không ghi đè review cũ. |
| C — TV4 | Index thử tách DB đang dùng, manifest pipeline và diff theo nguồn. | Mọi nguồn trong phạm vi được kiểm theo cổng mới; sai lệch giải thích được, đủ binding/vector, rollback đã diễn tập. |
| D — TV1, TV2, TV5 | Ranking, token packing, prompt/citation, lý do lỗi và quan sát từng tầng. | Đo end-to-end cùng dữ liệu/model với baseline, không làm giảm nguồn đúng/chức năng cũ. |
| E — TV2, TV4, TV6 | Thử QA mới nếu lỗi còn thuộc mô hình sau các đối chứng. | Holdout mới, retention, chất lượng/tài nguyên đạt; quyết định chấp nhận độc lập. |

Thứ tự B→C→D có thể phát triển trên cùng bản thử, nhưng không đổi index đang phục vụ trước khi cả nguồn và đường truy xuất đã nghiệm thu. Khi triển khai, ưu tiên Antigravity theo Coding, chỉ một writer cho mỗi phạm vi; không khởi chạy người ghi corpus cạnh tranh. Đây là thiết kế, chưa gửi nhiệm vụ triển khai hoặc thông điệp ra ngoài.

## Migration và rollback

Indexer đang bỏ qua file không đổi dựa trên hash PDF/status; sửa extractor rồi chạy lại có thể vẫn dùng chunk cũ. Version mới phải ràng buộc cả source hash, extractor/normalizer/chunker/schema version, bộ nhúng digest và policy review. Đổi pipeline làm vô hiệu hóa đúng lớp dữ liệu phụ thuộc.

Tạo thế hệ index mới ở kho local riêng, giữ nguồn/review/raw và index cũ. Chỉ tái nhúng nội dung thay đổi; cache chỉ được tái dùng khi full-text hash + model digest + schema đúng. Không thao tác force-reindex trực tiếp production để “làm mới”. Review cũ không được kế thừa tùy ý sau sửa nội dung: thay đổi phải có chứng cứ mới hoặc binding review vẫn đúng.

Block ID ổn định theo nguồn/vùng/version, bảng ánh xạ locator cũ→mới; các citation/lịch sử cũ tiếp tục giải được hoặc trỏ thế hệ nguồn cũ. Chuyển thế hệ theo manifest nguyên tử hoặc transaction/snapshot phù hợp, khóa reader/writer và kiểm API sau chuyển; rollback quay đúng manifest/index cũ, không reset hội thoại/lịch/adapter. Với vector cache, metadata cần bảo toàn để cổng hiện tại còn kiểm được.

## Tiêu chí đánh giá độc lập trước phát hành

- **Nguồn:** identifier/code không đổi; khối code phù hợp qua kiểm cú pháp. Công thức kiểm biến/dấu/cận/vector/điều kiện theo ảnh; bảng kiểm đúng ô và tiêu đề; hình kiểm nội dung được phép mô tả và phần chưa chắc. Chỉ báo tự động không tự duyệt nguồn.
- **Retrieval:** oracle source/page/block do reviewer xác định trước. Đo Recall@6, thứ hạng block chứa đáp án, tỷ lệ mục lục/nhiễu và độ phủ điều kiện. Các ca đã xác nhận phải tìm đủ block bắt buộc, không chỉ tên tài liệu.
- **Context:** các block bắt buộc được đưa nguyên ý, không mất điều kiện/formula; đúng budget tokenizer; ID nguồn khớp block đã đưa. Đo số nguồn bị bỏ cùng lý do.
- **QA:** bộ mới có tối thiểu 10 câu/môn (60 câu) và thêm câu thiếu nguồn, mơ hồ, nhiễu, sai điều kiện, chỉ dẫn trong tài liệu. Đây là thiết kế tối thiểu, chưa phải bộ đã đóng băng hoặc nghiệm thu đủ đại diện. Đánh giá đúng nội dung, citation hỗ trợ từng ý và từ chối đúng; đo lặp với cấu hình thực. Chốt ngưỡng trước chạy, không chọn ngưỡng theo kết quả tốt nhất.
- **Tách nguyên nhân:** chạy cùng câu qua index hiện tại; oracle block hiện tại; block đã sửa từ ảnh; cùng block với prompt mới. Giữ model/config cố định để biết cải thiện thuộc nguồn, retrieval, packing hay sinh. Đối chứng oracle chỉ diagnostic, không coi thành trải nghiệm production.
- **Hồi quy:** QA/auditor, giải/kiểm bài tập, lịch sử phiên, lịch ảnh và trích nguồn cũ; kiểm cả nguồn từng tìm đúng. Đo thời gian, RAM/swap trên máy thật. Windows/native OCR-VLM chưa chạy thì ghi NOT_RUN.

## Kết quả quy trình lượt này

Phân tích, baseline lỗi, kiểm cú pháp, đối chiếu ảnh, truy xuất và review logic: đã thực hiện. Code gốc biên dịch PASS, bản normalized FAIL đúng lỗi tái hiện. Hash DB kiến thức/hội thoại/registry khớp cuối YC-203. Không sửa code ứng dụng, PDF, index hoặc model; không huấn luyện/kích hoạt. Triển khai, RED→GREEN sản phẩm, shadow reindex, full-source quality và end-to-end đa môn: **NOT_RUN**, vì lượt này tìm nguyên nhân và lập giải pháp/cách sửa. Handoff triển khai: **N/A** trong phạm vi phân tích này. Tài liệu/hồ sơ đã cập nhật; chưa commit/push.
