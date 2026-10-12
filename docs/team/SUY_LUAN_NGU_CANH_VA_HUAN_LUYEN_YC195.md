# Mức xử lý, ngữ cảnh và huấn luyện — YC-195

Ngày 11/10/2026. Tài liệu này thay thiết kế ba kiểu trình bày quick/steps/compare. Những thay đổi đang kiểm thử phải được phân biệt với phần đã phát hành.

## Cách dùng trong sản phẩm

| Lựa chọn | Hành vi | Giới hạn |
| --- | --- | --- |
| Tự động | Chọn quy trình theo câu hỏi; định nghĩa dùng lượt trả lời thông thường, so sánh/tổng hợp dùng thêm đối chiếu nguồn. | Bộ định tuyến hiện tại dùng quy tắc, chưa phải bộ phân loại độ khó được huấn luyện. |
| Thông thường | Một câu trả lời đầy đủ, bám nguồn; tự so sánh nếu câu hỏi yêu cầu. | Tối đa 900 token đầu ra; không bảo đảm đúng chỉ vì có trích dẫn. |
| Kiểm tra kỹ | Sinh bản nháp rồi đối chiếu các nhận định với chính nguồn đã được nhận vào lượt hiện tại. | Tối đa 1.100 token bản nháp và 600 token kiểm tra; không có vòng tự sửa vô hạn. |

Ở phần bài tập, người học chọn **Chỉ đáp án** hoặc **Giải chi tiết** trước khi mở kết quả. Đề và đáp án có thể nằm ở hai trang khác nhau trong cùng tài liệu. Một đáp án ngắn trong sách không đủ để gọi phần giải thích do AI tạo là lời giải của sách. Nguồn hiển thị bằng đường dẫn tài liệu và số trang.

Các lựa chọn trên điều chỉnh quy trình ứng dụng. Qwen2.5-3B-Instruct hiện tại không cung cấp tham số reasoning effort trong đường gọi của dự án. [OpenAI](https://developers.openai.com/api/docs/guides/reasoning), [Google](https://ai.google.dev/gemini-api/docs/thinking) và [Anthropic](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost) cung cấp điều khiển xử lý trên các model hỗ trợ; tên và giá trị phụ thuộc model. Không thể tái tạo năng lực đó chỉ bằng đổi lời nhắc hoặc kéo dài câu trả lời.

## Ngữ cảnh và nguồn

Giữ cửa sổ vận hành 8.192 token. [Model card Qwen](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct) nêu dung lượng danh nghĩa 32.768 token, nhưng đó không phải cam kết về RAM, tốc độ hoặc độ đúng của ứng dụng này. Cổng cuối dùng tokenizer và chat template để kiểm tổng đầu vào cộng dự phòng đầu ra trước khi nạp weights; lớp ứng dụng còn có ngân sách bảo thủ theo byte.

Lưu toàn bộ lịch sử trong kho, đưa vào prompt một lượng có giới hạn. Phương án đang triển khai giữ các cặp hỏi/đáp gần nhất và truy xuất lượt gốc liên quan trong cùng phiên; không tạo ký ức mới bằng AI. Lịch sử và bản tóm tắt là dữ liệu về ý định, không phải bằng chứng sách. Khi hỏi lại chủ đề, ứng dụng vẫn truy xuất tài liệu hiện tại. [Lost in the Middle](https://arxiv.org/abs/2307.03172) là lý do cần kiểm cả vị trí thông tin trong ngữ cảnh thay vì chỉ tăng cửa sổ.

Nguồn, câu hỏi và lịch sử phải được đặt trong vùng dữ liệu đã bảo vệ ranh giới. Mã trích dẫn phải thuộc nguồn được nhận, quote phải liên tục, và nhận định kiểm tra phải khớp chính bản nháp, giữ toán tử, số, điều kiện và định danh. Đối chiếu bằng cùng một model vẫn có thể sai về ngữ nghĩa; nhãn giao diện chỉ nói **đã qua đối chiếu tự động**. [OWASP](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) khuyến nghị nhiều lớp phòng vệ; escape không ngăn mọi chỉ dẫn gây hại diễn đạt bằng ngôn ngữ tự nhiên.

## Dữ liệu và học bổ sung

Áp dụng vòng đánh giá → cải thiện truy xuất/prompt → học bổ sung nếu lỗi thuộc hành vi model → đánh giá lại. [Qwen2.5 Technical Report](https://arxiv.org/html/2412.15115v2) cung cấp bối cảnh SFT và học từ phản hồi ở quy mô lớn; dự án ưu tiên LoRA trên dữ liệu nhỏ đã duyệt, không tái hiện tiền huấn luyện. [DPO](https://arxiv.org/abs/2305.18290) chỉ xem xét sau khi có cặp tốt/xấu được kiểm nội dung; không thưởng độ dài hay định dạng để thay độ đúng.

Mỗi phiên học phải cố định mã, model, tokenizer, nguồn, dữ liệu, prompt thực, decoding và bộ đánh giá bằng phiên bản/hash. Chia train/valid/holdout theo họ tài liệu; giữ tài liệu đánh giá tách khỏi train. Bộ mẫu chức năng công khai có 27 tài liệu tự biên soạn CC0, 9 chủ đề × lý thuyết/đề/đáp án; không dùng làm dữ liệu huấn luyện hoặc holdout chất lượng. Thay UI thuần túy không tự đòi hỏi học lại; thay nguồn hoặc hợp đồng nhiệm vụ phải kiểm dữ liệu dẫn xuất.

Nhãn cần phủ QA có/thiếu nguồn, tạo câu hỏi từ lý thuyết, kiểm đáp án đúng/sai, giải bài và kiểm lời giải, đối chiếu nhận định, tóm tắt đúng lịch sử. Quy tắc đầu vào rỗng, kích thước body, khóa đồng thời và bảo vệ đường dẫn do code thực hiện; không cần dạy model để xử lý những lỗi đó. Dữ liệu nháp hoặc schema đủ trường chưa có nghĩa là đủ nhãn học.

## Kiểm chứng và nghiệm thu

Đo trên cùng câu hỏi/nguồn: nhận định đúng, chi tiết bịa, bỏ sót điều kiện, từ chối sai, độ trễ, số lần gọi, token và RAM đỉnh. Kiểm K=4/6/8/10 theo toàn pipeline; mặc định hiện tại 6, tổng hợp 10, chưa tuyên bố tối ưu. Chỉ tăng ngữ cảnh sau kiểm nguồn ở đầu/giữa/cuối, nhiều lượt đổi chủ đề và bộ nhớ thực.

Ca biên bắt buộc gồm rỗng/ký tự vô hình/chỉ ký hiệu, Unicode lỗi, công thức hợp lệ, câu/lịch sử/body quá dài, giả role, nguồn chứa chỉ dẫn, quote sai hoặc thiếu, JSON/fence không kín, đầu ra bị cắt, gửi trùng và đồng thời. Yêu cầu lỗi phải bị chặn trước model, không thành lượt học hoặc lịch sử thành công. Dịch vụ local hiện có giới hạn đồng thời; chưa coi đó là rate limit cho triển khai công khai.

Trạng thái tại lúc lập tài liệu: mã mới đang kiểm thử trong snapshot cách ly; main đã có bộ tài liệu kiểm thử qua PR11. Corpus đạt phạm vi đã duyệt gồm 13 ngoại lệ, nhưng bộ dữ liệu mới chưa được duyệt để học. Huấn luyện và kích hoạt adapter chưa chạy. Bằng chứng và kết quả model thật phải được cập nhật trước nghiệm thu.
