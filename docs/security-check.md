# Rà soát bí mật và tài sản Git — hiệu chỉnh Issue #29

Ngày 16/10/2026, YC-218: PR #31 đã ghi hoàn tất #29 nhưng báo cáo chưa nằm trong
Git do allowlist lúc đó. Bổ sung file này; giữ nguyên mục lịch sử gốc và attribution.

| Phạm vi | Kết quả | Giới hạn |
| --- | --- | --- |
| Gitleaks toàn bộ refs local, gồm PR35 tại `7601045` và main `e91e03a` | PASS, 52 commit có diff, khoảng 3,46 MB; không phát hiện secret theo rule công cụ | Không phải chứng nhận không có mọi bí mật hoặc PII |
| Cây file công khai dự kiến đưa vào PR | PASS, 313 file công khai, khoảng 3,27 MB; công cụ không phát hiện secret | Không quét corpus, dữ liệu cá nhân hoặc DB riêng |
| `.env.example`, diff và tài sản bị ignore | PASS, 25 ca ranh giới Git; diff chỉ chứa tài sản chia sẻ | Không dùng `git add -f` để vượt ranh giới |
| Attribution Git/LICENSE | Giữ tên/email tác giả vốn đã công khai theo quy tắc dự án | Không tuyên bố repo hoàn toàn không chứa thông tin cá nhân |

Công cụ: `gitleaks git --redact --log-opts=--all` và `gitleaks dir --redact`
trên snapshot chỉ chứa file được phép chia sẻ. Log chi tiết giữ local. Không đưa
secret, log dữ liệu riêng hoặc thông tin huấn luyện vào báo cáo công khai.

Duy trì: đọc [Secrets](../CONTRIBUTING.md#quản-lý-bí-mật-và-dữ-liệu-nhạy-cảm-secrets),
kiểm staged diff, nội dung PR và `git check-ignore -v <file>` trước mỗi lần push.
Nếu phát hiện credential, phối hợp owner thu hồi tại nhà cung cấp, không tự viết
lại Git history chung. Quét hiện tại không bảo đảm các commit tương lai cũng sạch.
