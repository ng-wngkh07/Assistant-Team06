# Rà soát bí mật và ranh giới tài sản Git

- Dự án: Assistant-Team06; báo cáo của #29, hiệu chỉnh YC217 ngày 16/10/2026.
- Hiệu chỉnh: báo cáo cũ có trên máy nhưng bị ignore, PR #31 chỉ có nhật ký.
  Main sau PR #33 cho phép Markdown ở `docs/`; file này hiện có trong thay đổi
  chờ review. Chưa commit/push trong lượt YC217.

## Phạm vi và kết quả có bằng chứng

| Kiểm tra | Kết quả | Giới hạn |
| --- | --- | --- |
| Gitleaks Git history trên main `d0d1ae0` và tất cả refs local | Không phát hiện bí mật theo rule của công cụ; 50 commit có diff được quét (HEAD có 77 commit gồm merge), khoảng 3,44 MB | Không phải chứng nhận an toàn toàn hệ thống hoặc phát hiện mọi loại bí mật |
| Cây file dự kiến chia sẻ, gồm thay đổi chưa commit | Không phát hiện bí mật; snapshot 315 file, khoảng 3,11 MB | Không quét dữ liệu riêng ngoài cây công khai |
| `.env`, private keys, DB, vector, model và hồ sơ local | 23 ca Git boundary PASS | Không được dùng `git add -f` để vượt ranh giới |
| `.env.example` và file chia sẻ trong diff | Chỉ cấu hình mẫu; không thêm credential thật | Kiểm lại staged/PR trước mỗi lần xuất bản |
| Attribution tác giả trong Git/LICENSE | Giữ nguyên theo quy tắc dự án | Tên/email attribution công khai không được báo là hoàn toàn không có PII |
| Rule Antigravity và ví dụ Lab 2 | Ngoại lệ đúng đường dẫn được review | Không mở toàn `data/` hoặc `.agents/` |

Công cụ: `gitleaks git --redact`; log/report chi tiết giữ local và không đưa
credential vào báo cáo. Kết quả quét bản thay đổi hiện tại được ghi trong
[báo cáo rà soát YC217](team/RA_SOAT_REPO_YC217.md) khi hoàn tất.

## Duy trì

Đọc [Secrets](../CONTRIBUTING.md#quản-lý-bí-mật-và-dữ-liệu-nhạy-cảm-secrets),
kiểm file mới bị ignore, staged diff và nội dung PR. Nếu phát hiện credential,
thu hồi ngay tại nhà cung cấp và phối hợp owner xử lý; không tự viết lại lịch sử
Git chung. Không coi việc đóng #29 là bằng chứng quét mọi commit phát sinh sau đó.


## Cập nhật YC-218 — bản sửa PR #35

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
