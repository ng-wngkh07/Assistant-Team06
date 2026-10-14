# Mẫu thử huấn luyện giải bài tập — YC-200

Cập nhật 2026-10-12T14:29:24.634669+07:00. Mẫu thử đã huấn luyện và đo trước/sau thực tế. Tỷ lệ đạt tăng, nhưng chất lượng và độ ổn định chưa đủ, có hồi quy chức năng cũ; quyết định DO_NOT_PROMOTE, dừng mở rộng và giữ mô hình phục vụ hiện tại.

Mốc nguồn `complete:true` trong báo cáo này thuộc cổng trước sửa YC205. Audit pipeline mới ngày14/10/2026 là `complete:false`; không dùng mốc lịch sử này để cho phép huấn luyện mới.

## Dữ liệu đã duyệt

- 64 mẫu học: 16 bài giải mới ở đại số tuyến tính, vi tích phân, lập trình C++ và tâm lý học; 48 mẫu kiểm định ôn lại. 24 mẫu kiểm tra thuộc nguồn khác họ với nguồn học, gồm bốn mẫu mỗi môn trong sáu môn.
- 88/88 trích đoạn ngữ cảnh khớp tài liệu, trang và lát cắt hiện tại của cơ sở dữ liệu. Đề bài gốc được liên kết riêng, giữ đủ điều kiện và yêu cầu làm tròn. Hai câu C++ dùng ví dụ mã nguồn trên slide, câu hỏi truy vết do AI thêm đã được khai báo; không coi chúng là câu hỏi do tác giả viết.
- Codex đọc, kiểm toán và đối chiếu 16 lời giải mới bằng nguồn, ảnh PDF đã rà soát, phép tính độc lập và bốn chương trình C++ thực thi. 72 nhãn ôn lại chỉ tái sử dụng khi nội dung câu hỏi/đáp án/ngữ cảnh/hash nguồn khớp review cũ và 72 đầu vào khớp biên dịch vụ hiện tại.
- Chia nguồn theo họ tài liệu; Stewart/HCMUS, các bài đại số cùng khóa học và sách/slide Gaddis được giữ cùng một họ. Tổng có chín họ học, sáu họ kiểm tra; Phelps là họ đánh giá riêng, không nằm trong hai tập trên.
- Độ dài 204–747 token bằng tokenizer thật, không cắt nguồn/đáp án. Cổng cân bằng sáu môn không đổi và đạt. Hai môn vật lý/toán rời rạc có mẫu ôn kiểm định, chưa có bài giải mới.
- Kho nguồn hiện tại complete:true trong phạm vi 22.698 trang đã duyệt, 157 tài liệu, 52.612 vector; vẫn giữ 13 ngoại lệ đã được cho phép. Không sửa PDF, cơ sở dữ liệu hoặc vector.

## Phép đo đóng băng trước học

Tám bài thật trong một họ nguồn tâm lý học chưa dùng để học. Mỗi bài có hai đầu vào chỉ khác khoảng trắng, thành 16 lượt yêu cầu, cộng ba ca giữ chức năng cũ đã từng trả lời đúng. Không coi 16 lượt là 16 bài độc lập.

Mô hình gốc thật đạt **3/16 lượt bài tập (18,75%)**, sáu lượt đúng một phần và bảy lượt không đạt; ba ca chức năng cũ đạt **3/3**. Lỗi gồm đáp án mẫu chưa thay, thiếu điều kiện, giải thích không đủ căn cứ và JSON chưa hoàn chỉnh. Không dùng phản hồi mô phỏng của công cụ thu đầu vào để chấm chất lượng mô hình.

Tiêu chí giữ nguyên trước khi học: đạt ít nhất 90% lượt bài tập, tăng ít nhất 15 điểm phần trăm, không làm mất câu trước đây đúng, không cắt đầu ra, đáp án và lời giải nhất quán, các biến thể cùng bài đều trả lời đúng. Từng môn được đánh giá phải đạt ít nhất 80%. Đây là tiêu chí vận hành được chọn để thực hiện yêu cầu “ổn định và hiệu quả cao”, không phải các con số do người dùng nêu.

## Lượt học thực tế đã duyệt

Base `mlx-community/Qwen2.5-3B-Instruct-4bit`, revision `4f83f8f146fdf28b512a06562b671d7af4fab457`; học LoRA mới 128 bước, bốn lớp, tốc độ học 0,00002, độ dài 1024, một mẫu mỗi bước, lưu bộ nhớ bằng gradient checkpoint và chỉ học phần đáp án. Không tiếp tục adapter cũ đã bị từ chối.

Toàn bộ cổng nguồn, hash base/dữ liệu, review nhãn, token, chia nguồn, protocol và tài nguyên phải đạt trước khi chạy. Cổng corrective đầy đủ đã đạt; Antigravity thực hiện, Codex giữ đầu vào đã duyệt và đánh giá độc lập sau khi bàn giao GPU.

**Đã hoàn tất:** run-20261012-065936-957f13, 128/128 bước, bắt đầu 13:59:59 và kết thúc 14:17:40 ngày 12/10/2026 (giờ Việt Nam), thời gian ledger 1.062 giây, mã thoát 0. Loss validation từ 1,746 xuống 0,024; loss không được dùng để nghiệm thu. Trọng số cuối có 56 tensor hữu hạn, hình dạng tương thích; SHA-256 `f387b446be65767832603e6974f202a194cd1c3483cc6f487bab100129b8eb44`. MLX đỉnh 3,199 GB, RAM khả dụng thấp nhất 1,69 GiB, swap tăng 555,56 MB; monitor không kích hoạt dừng.

Antigravity đã kết thúc và trả toàn bộ quyền ghi/GPU; hai tiến trình đã thoát. Codex kiểm lại hash base, dữ liệu, approval và bảy tệp được bảo vệ. Mô hình phục vụ vẫn base, active_adapter_id:null.

## Kết quả độc lập sau học

Codex đọc toàn bộ 19 phản hồi thực, đối chiếu cùng nguồn, nhãn và điều kiện đã đóng băng. Cả 19 lượt kết thúc tự nhiên, JSON hợp lệ, không bị cắt; thời gian sinh tổng cộng 140,55 giây. Phép đo trước học và sau học giữ đúng đầu vào và cấu hình sinh.

| Chỉ tiêu | Trước học | Sau học | Yêu cầu |
| --- | --- | --- | --- |
| Lượt bài tập đạt đủ ý | 3/16 (18,75%) | 6/16 (37,50%) | Ít nhất 90% |
| Lượt đúng một phần / không đạt | 6 / 7 | 5 / 5 | Một phần không tính đạt |
| Mức tăng tỷ lệ đạt | — | +18,75 điểm phần trăm | Ít nhất +15 điểm |
| Bài đạt ở cả hai biến thể | 0/8 | 2/8 (25%) | Cả hai biến thể đều đúng |
| Giữ ca chức năng cũ đã đúng | 3/3 | 2/3 | Không hồi quy |
| Đáp án và lời giải giữ cùng nội dung cần thiết | — | 10/16 (62,50%) | 100% |
| Đầu ra bị cắt | 0 | 0 | 0 |

Hai biến thể chỉ khác khoảng trắng. Tám bài độc lập thuộc một họ nguồn tâm lý học chưa học; ba ca giữ chức năng cũ đã từng được quan sát. Kết quả này chưa chứng minh chất lượng sáu môn hoặc toàn kho tài liệu.

| Bài tâm lý học | Bản gốc sau học | Biến thể sau học | Lỗi/giới hạn chính |
| --- | --- | --- | --- |
| Đánh giá điểm mạnh cá nhân | Đạt | Không đạt | Biến thể cho lời khuyên thay cho nguyên nhân thiếu chuyên môn |
| Amiable skepticism | Đạt | Đạt | Có lỗi từ thừa nhỏ, vẫn giữ đúng ý bằng chứng |
| Quan hệ mind/brain | Một phần | Đạt | Bản gốc bỏ cơ chế sinh hóa trong đáp án ngắn |
| Nature/nurture | Đạt | Đạt | Giữ đóng góp và tương tác hai yếu tố |
| Ý nghĩa thống kê | Không đạt | Không đạt | Đáp án mẫu chưa thay và sai/thiếu điều kiện xác suất |
| Dual coding car/ride | Một phần | Một phần | Đáp án ngắn bỏ lý do mã hóa bằng cả ngôn từ và hình ảnh |
| Schachter–Singer | Không đạt | Một phần | Nhầm attribution hoặc thiếu thứ tự sinh lý và liên hệ hai lý thuyết |
| Hai cách coping | Không đạt | Một phần | Nhầm không thể kiểm soát với không lường trước; còn thiếu các điều kiện |

Ca hồi quy là kiểm định trắc nghiệm đại số: đáp án A được nguồn hỗ trợ nhưng candidate trả `supported:false`; hai ca kiểm định bài tập và tóm tắt còn đạt. Tóm tắt có từ “hệ” chưa chuẩn nhưng giữ công thức/điều kiện chính; không buộc nhắc lại b hằng số vì reference tóm tắt đã đóng băng không yêu cầu ý này.

## Quyết định và chẩn đoán

**DO_NOT_PROMOTE. Không kích hoạt bản thử và không mở rộng mẫu ra toàn bộ tài liệu.** Mức tăng vượt 15 điểm nhưng tỷ lệ đúng còn thấp, độ ổn định chưa đạt và mất một ca trước đây đúng.

Quan sát xác nhận lỗi ở hành vi trả lời: vẫn để placeholder, làm ngắn mất điều kiện, nhầm quan hệ lý thuyết và bác đáp án đúng. Đầu vào thực giữ nguyên, trọng số hợp lệ, không cắt đầu ra. Chưa có bằng chứng để quy nguyên nhân cho thiết lập huấn luyện hoặc lỗi truy xuất. 16 bài giải mới, trong đó bốn bài tâm lý khá đơn giản, chưa chứng minh độ bao phủ lời giải nhiều ý; loss thấp trên các mẫu kiểm định không chứng minh năng lực giải bài chưa học.

Hướng thử tiếp được đề xuất, chưa thực hiện: tăng đa dạng bài giải có điều kiện và ví dụ đáp án ngắn đầy đủ, kèm ca giữ khả năng kiểm định; nêu giả thuyết thay đổi rõ ràng và dùng bộ bài độc lập mới trước lượt học khác. Bộ holdout hiện tại đã lộ kết quả, chỉ dùng làm bộ chẩn đoán nếu chỉnh mô hình dựa trên những lỗi này. Không lặp lại lượt học không đổi hoặc dùng đáp án holdout để tuyên bố tổng quát hóa.

Bằng chứng local: `data/evaluation/exercise-pilot-yc200/codex/`, bộ đánh giá `exercise-frozen-v1/`, dữ liệu `data/training/v31_yc200_final_reviewed/`. Dữ liệu, lời giải nguồn và trọng số giữ riêng, không đưa vào GitHub. Branch/PR YC-198 giữ nguyên.
