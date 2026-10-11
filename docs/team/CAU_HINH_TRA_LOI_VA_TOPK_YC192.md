# Cấu hình trả lời cố định và kiểm tra Top-K — YC192

Mốc kiểm tra: main 604f096 sau PR 9/update-Agent, tích hợp PR 10.

Giao diện không chọn model/embedding/Top-K. API dùng cấu hình máy chủ cho hỏi đáp thường/stream, tạo và kiểm trắc nghiệm, tóm tắt phiên và lập chỉ mục. Mac dùng `qwen2.5-3b-4bit` (MLX, Qwen2.5-3B-Instruct 4-bit); Windows dùng `qwen2.5:3b` (Ollama, cần kiểm chất lượng riêng). Nhúng mặc định `bge-m3`, cùng model với vector corpus. Cấu hình deployment vẫn do người vận hành quản lý; payload trình duyệt không thay model. Không đổi âm thầm sang 7B hay một không gian vector khác khi model cần dùng thiếu.

## Số đoạn trích

Giữ chính sách hiện tại: mặc định 6 cho câu hỏi thông thường; câu hỏi tổng hợp truy xuất theo khía cạnh và dùng 10 nội bộ khi mặc định 6. Đây là số ứng viên truy xuất, không bảo đảm cả 6/10 đoạn đều được đưa vào model. Nguồn trùng được loại; ngân sách ngữ cảnh, khoảng trang/tài liệu và kiểm bằng chứng vẫn áp dụng.

Codex đo 168 lượt trên 28 câu hỏi có nguồn/trang tham chiếu, với BGE-M3 và corpus thật, K yêu cầu 2/4/6/8/10/12. Semantic kết quả top 36 của cùng query được cache và cắt prefix; FTS tính lại theo K. Không dịch nguồn; ranh giới sinh model được capture, không sinh câu trả lời. Các nguồn tham chiếu được đối chiếu hash PDF trong DB. Token tính bằng tokenizer/template base thật, gồm SYSTEM đầy đủ.

| K yêu cầu | Có trang đúng trong ứng viên | Có trang đúng trong prompt thực | Token prompt trung bình |
| --- | ---: | ---: | ---: |
| 2 | 12/28 | 6/28 | 1636.9 |
| 4 | 12/28 | 6/28 | 1667.9 |
| 6 | 14/28 | 7/28 | 1664.3 |
| 8 | 15/28 | 7/28 | 1675.4 |
| 10 | 15/28 | 7/28 | 1675.9 |
| 12 | 18/28 | 6/28 | 1674.4 |

K 6 là mức nhỏ nhất trong phép đo này đạt cùng số prompt có trang đúng với K 8/K 10. K 12 tăng số ứng viên có trang đúng nhưng không tăng số prompt thực có trang đúng. Do đó chưa có căn cứ tăng mặc định 6; nút thắt hiện còn ở xếp hạng và chọn nguồn dưới ngân sách ngữ cảnh.

Giới hạn: bộ 28 chỉ phủ 3 nhóm nguồn cũ; không đại diện đủ 6 môn hoặc mọi kiểu hỏi. Câu hỏi rộng tự dùng tối thiểu 10 nên K yêu cầu không luôn là K hiệu lực. Kết quả ứng viên có thể bao gồm các khía cạnh trước cắt; chỉ tiêu prompt được đọc từ các thẻ nguồn thực đã giữ. Có trang đúng không chứng minh câu/điều kiện cần thiết sống sót giới hạn 900 ký tự mỗi đoạn. Không dùng số này làm độ chính xác trả lời, không coi đây là bộ nghiệm thu độc lập sau tối ưu. RAM 16 GB, lúc kiểm còn 4.19 GB khả dụng; token prompt tối đa 1887 trong đường không dịch; giữ hạn ngữ cảnh 8192 và đầu ra 360/900/900. Chưa có so sánh chất lượng câu trả lời thật cho mọi K.

## Nguồn đối chiếu

- [Lost in the Middle](https://arxiv.org/abs/2307.03172): mô hình có thể dùng ngữ cảnh kém khi thông tin cần thiết nằm giữa; thêm nguồn không tự bảo đảm đáp án tốt hơn.
- [LlamaIndex Vector Retriever](https://developers.llamaindex.ai/python/framework-api-reference/retrievers/vector/): topK là số kết quả tìm kiếm; cần đánh giá theo tác vụ/corpus, không quy định một K tối ưu chung.
- [FlagEmbedding/BGE-M3](https://github.com/FlagOpen/FlagEmbedding): nhúng đa ngôn ngữ và các phương pháp retrieval; repo hiện dùng dense vectors và FTS/RRF, chưa bổ sung reranker.
- [Thảo luận về chất lượng retrieval](https://www.reddit.com/r/LocalLLaMA/comments/1kien12): kinh nghiệm kiểm thủ công nguồn đúng và re-ranking; chỉ tham khảo, không thay số đo hoặc chứng minh tác dụng trên dự án.

Bằng chứng chi tiết và prompt/corpus thực lưu riêng ngoài Git. Không xuất bản nguồn, DB/vector, nhãn/dataset hoặc weights. Bản dữ liệu v18 vẫn chưa được duyệt; kiểm hợp đồng 28 PASS nhưng review tìm nhãn Vi tích phân không khớp nguồn và thiếu điều kiện Đại số. Chưa huấn luyện/kích hoạt adapter.
