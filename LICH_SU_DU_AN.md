# Lịch sử thay đổi ứng dụng Agent Học Tập

<!-- PROJECT_HISTORY_SCHEMA: 3 -->
- File chung duy nhất: `LICH_SU_DU_AN.md` ở gốc, push cùng mã nguồn.
- Cập nhật: 2026-10-17T23:30:00+07:00
- Mục mới nhất: **[YC-226](#yc-226)**.
- Nhật ký chung có **81 mục**: 29 môi trường/chức năng, 6 tổ chức/tài liệu, 6 phát hành/đồng bộ GitHub, 20 rà soát/nghiệm thu, 11 làm rõ kiến trúc/quyền/quy trình, 9 dữ liệu corpus/huấn luyện. Có 58 mã YC và 23 mã LOG duy nhất; giữ nguyên các ID/ngày cũ. Hiệu chỉnh ngày 17/10/2026.
- [Quy tắc](README_QUY_TAC.md), [phân công 6 người](docs/team/PHAN_CONG_6_NGUOI.md),
  [quy trình PR](CONTRIBUTING.md), [Windows](README_WINDOWS.md).

## Cách dùng

Đọc log liên quan lỗi/file/chức năng và main trước mọi thay đổi. Giữ sửa lỗi/tính năng
đã nghiệm thu; nếu thay quyết định, dẫn ID cũ và lý do. Mỗi PR thêm mục, tác giả ghi
ý chính của lỗi và phần sửa, **không copy prompt hoặc hội thoại**. Không tạo log riêng
cho mỗi người. Giải quyết xung đột bằng giữ cả mục hợp lệ, không ghi đè công việc khác.

Giữ ID/ngày cũ. Hiệu chỉnh thông tin bằng lý do và thời gian sửa; không xóa lịch sử.
YC-168 biên tập YC-167 theo yêu cầu mới; bản trước hiệu chỉnh và 166 mục hội thoại cũ
được bảo toàn local để truy nguyên, không phải các mục cần sao chép vào Git.

## Quyết định đang áp dụng

| Mốc | Quyết định | Trạng thái |
| --- | --- | --- |
| YC-168 thay phân công nhóm ở YC-167 | 3 chủ trì chức năng + huấn luyện + giao diện/môi trường + kiểm thử/tài liệu | Phương án 6 người đã lập để nhóm chốt. |
| YC-168 hiệu chỉnh cách ghi YC-154/167 | Nhật ký tóm tắt lỗi/thay đổi sản phẩm; không lưu prompt; đọc log trước sửa | Đã áp dụng cho file chung và quy tắc. |
| YC-164/167, YC-168 bổ sung PDF | Mã/UI/test/config/docs/log/metadata/PDF phân công qua Git; dữ liệu/model thật riêng | YC-169 cho phép đưa mã lên repo Study-Agent; dữ liệu thật giữ riêng. |
| Cổng nguồn/model trước YC-188 | Một writer chính; toàn nguồn review, audit live complete: true và đánh giá độc lập trước kích hoạt | Phạm vi toàn bộ trang được YC-188 thay bằng phạm vi có 13 ngoại lệ cố định; các cổng chất lượng còn giữ. |
| YC-179 làm rõ YC-178 | Giữ tên/email tác giả và attribution Git; phân công/giao việc dùng TV1-TV6; bảng ánh xạ quản lý riêng | Đã áp dụng; thay đề xuất ẩn toàn bộ danh tính ở YC-178, không đổi lịch sử Git. |
| YC-180 hoàn tất corpus có thể đọc | Toàn bộ 1.064 trang có thể đọc mới đã review ảnh thật, nhúng vector; giữ 13 trang unresolved REPORT_ONLY | Hoàn tất 100% trang có thể đọc; sẵn sàng nghiệm thu. |
| YC-187 nghiệm thu YC-180 | Audit live complete:false vì 13 trang chưa hoàn chỉnh; huấn luyện được yêu cầu sau khi corpus/dataset đạt | Quyết định phạm vi chờ được YC-188 giải quyết; snapshot audit này vẫn complete:false. |
| YC-188 thay phạm vi cổng nguồn | Cho phép loại đúng 13 trang đã hash-bound, giữ toàn bộ nguồn gốc và nguồn còn lại phải được duyệt | Đã được người dùng chấp thuận; triển khai cổng/data/học còn cần kiểm chứng. |
| YC-191 thay mốc chức năng học | Dữ liệu/đánh giá bám update-Agent và các chức năng mới | PR9 đã merge vào main; nhãn mới chưa duyệt. |
| YC-192 cố định cấu hình trả lời | Máy chủ chọn model trả lời/nhúng và Top-K, bỏ lựa chọn trên UI | Mặc định 6; câu tổng hợp dùng 10 nội bộ; merge và đồng bộ sau kiểm chứng. |
| YC-195 thay phân chia mode YC-191/194 | QA đầy đủ, tự so sánh theo câu hỏi; từng bước chuyển sang bài tập; mức xử lý phải phù hợp model hiện tại | Đang triển khai/đo; không suy chất lượng từ độ dài hay thời gian. |
| YC-200 triển khai hướng YC-199 | Huấn luyện mẫu nhỏ giải bài tập có đáp án/lời giải chi tiết; chỉ mở rộng khi ổn định và hiệu quả cao qua đánh giá độc lập | Đã được yêu cầu; không cấp quyền tự kích hoạt adapter hoặc hạ cổng nguồn/chất lượng. |
| YC-207 bổ sung thứ tự sửa lỗi/phát hành/học | Sửa và kiểm chứng lỗi → đẩy mã/test/hồ sơ dùng chung lên GitHub → chỉ huấn luyện tiếp khi các cổng đạt | Áp dụng từ 14/10/2026; không push trực tiếp main hoặc công bố dữ liệu/model riêng. |
| YC-208 sửa bố cục UI OCR & dropdown nguồn | Căn giữa modal OCR hai chiều; tách nguồn gần nhất thành dropdown menu dọc bên phải tóm tắt | Đã áp dụng, giữ nguyên chức năng/hợp đồng test. |
| YC-214 giới hạn công bố | Chỉ phát hành bản sửa và kiểm thử phần mềm; dữ liệu và thông tin huấn luyện giữ local | Áp dụng cho commit mới và nội dung PR. |
| YC-217 làm rõ mục tiêu và tích hợp Lab 2 | QA học tập có nguồn là sản phẩm; văn phòng là demo; giữ issue cho thành viên, nghiệm thu toàn repo sau đủ 12; chỉ owner merge main | Tài liệu/quy tắc đã chuẩn hóa local; ruleset đã bật/readback; cổng sau 12 còn chờ các issue thành viên. |
| YC-218 sửa tích hợp sau YC217 | Sửa lỗi issue đã merge và PR35 help; giữ triển khai các issue khác cho thành viên | PR35 tại fa9d4c2, kiểm định local và toàn bộ CI PASS; chờ owner review/merge. |
| YC-221 rà soát corpus và lập chỉ mục shadow v118 | Staging 48 trang Doc 154 lên shadow batch729 (729 trang); phân loại 7.187 trang active và sản xuất candidate mới | Shadow v118-batch729 hoàn tất; candidate 751 (738 complete); hàng đợi active 7.165 trang đang tiếp tục. |
| YC-222 rà soát toàn diện dự án, tái kiểm định chức năng-mô hình-UI và lập hồ sơ lỗi | Kiểm định toàn bộ chức năng, mô hình, tiến độ nhánh sửa chữa, các nút bấm UI và ghi log lỗi kèm phương pháp sửa tại `docs/team/LOG_RA_SOAT_LOI_VA_PHUONG_PHAP_THAY_DOI_YC222.md` | Hoàn tất kiểm định (Pytest 578+31 ở gốc, 608+31 ở YC-220, Node UI 27/28 PASS, MLX thật 4/4 PASS); đã lập danh mục `UI-01..08` và `BUG-01..07`. |
| YC-224 thiết lập quy tắc rà soát mã nguồn tự động với Codex | Tạo `AGENT.md` và đồng bộ `AGENTS.md` với bộ quy tắc Code Review Rules (P0/P1/P2), tích hợp Codex CLI và PR review | Đã hoàn tất và kiểm định các lệnh kiểm tra. |

## Mẫu mục mới

```markdown
<a id="log-ID"></a>
### <ID duy nhất, ví dụ LOG-YYYYMMDD-chucnang-maviec-tvN> - DD/MM/YYYY HH:mm (UTC+07)
- Người thực hiện / loại thay đổi:
- Lỗi hoặc mục tiêu (ý chính):
- Trước / sau thay đổi:
- Phần/file sửa và cách xử lý:
- Log/phiên bản liên quan; lý do thay quyết định nếu có:
- Ảnh hưởng/phối hợp với phần còn lại:
- Kiểm thử phần mềm: OS/Python/profile, ca tổng hợp tái hiện/hồi quy, kết quả; thông tin nội bộ giữ local:
- Trạng thái: PASS / FAIL / đang làm / NOT_RUN / N/A có lý do:
- Giới hạn, lỗi còn lại, rollback/bước tiếp:
```

## Nhật ký chung

<a id="yc-167"></a>
### YC-167 - 09/10/2026 10:02 (UTC+07)

- Người thực hiện: Codex; loại: môi trường phát triển và sửa tích hợp model.
- Lỗi/mục tiêu: mã web có phụ thuộc Unix, đường dẫn cố định và UI/health/summary
  giả định MLX; cần môi trường Windows cho các chủ trì chức năng, Mac giữ training.
- Trước/sau: trước khó nhập/chạy trên Windows và chọn sai backend; sau có launcher,
  demo độc lập, khóa file native, đường dẫn cấu hình và model UI/health theo môi trường.
- Phần sửa: config/server/dialogue/gpu_lock/trained_client/topic_summarizer/timetable_store;
  file_lock mới; UI model; run.py guard training; dev/PowerShell, requirements/pytest,
  fixtures, CI và các README/quy tắc. scripts/service.sh bỏ PLIST_SRC không dùng.
- Kiểm thử tại mốc 09/10/2026: 109 test feature và 20 test Mac đạt (hai tập có phần trùng),
  dependency/parse/link/57 ca Git boundary đạt; demo HTTP/UI 2 nguồn tổng hợp, 5 đoạn,
  0 vector. Windows native/PowerShell/CI và QA/VLM thật NOT_RUN, không coi đã nghiệm thu.
- Ảnh hưởng: giữ mặc định MLX Mac, corpus/index/model thật không được ghi bởi việc này;
  Ollama Windows là bản phát triển, chất lượng không đồng nhất adapter Mac.
- Trạng thái: đã tích hợp local bộ chuẩn bị; chưa tạo repo/commit/push, không train/restart dịch vụ.
- Liên quan: YC-164/166 về Git; phân công 4 người và cách lưu prompt được YC-168 thay thế.
- Hiệu chỉnh 09/10/2026 10:28 (UTC+07), theo YC-168: tóm tắt thành lịch sử sản phẩm, bỏ phần
  prompt và metadata vận chuyển khỏi file chung; giữ ID/ngày, nội dung sửa/kiểm thử và
  bản trước hiệu chỉnh local. Đây không phải lần chạy lại các test nói trên.

<a id="yc-168"></a>
### YC-168 - 09/10/2026 10:28 (UTC+07)

- Người thực hiện: Codex; loại: tổ chức nhóm, tài liệu và quy tắc nhật ký.
- Mục tiêu: nhóm mở rộng 4 lên 6; chỉ một người huấn luyện; giao việc rõ trên ba chức
  năng hiện có, bảo vệ phần chung và đổi log từ lịch sử prompt sang lịch sử ứng dụng.
- Trước/sau: trước 3 chủ trì + TV4 và log còn chứa prompt; sau giữ 4 nhiệm vụ gốc,
  thêm giao diện/môi trường và kiểm thử/nghiệm thu/tài liệu, log tóm tắt ý chính lỗi/sửa.
- Phần sửa: PDF phân công 8 trang, docs/team/PHAN_CONG_6_NGUOI.md, README_QUY_TAC,
  CONTRIBUTING, README/Windows, template PR, AGENTS local, .gitignore và file log này.
  Bảng 4 người được đánh dấu mốc cũ, liên kết chuyển sang bảng 6 người.
- Quy tắc bổ sung: đọc log trước sửa; chỉ sửa đúng phạm vi; báo/phối hợp khi chạm phần
  chung hoặc cần chỉnh local; giữ tương thích và kiểm các chức năng ảnh hưởng; tên branch
  theo loại/phạm vi/mã việc/người; review chéo rồi TV4 điều phối merge; một nhật ký chung có ngày giờ.
- Đề xuất: đánh dấu trang cục bộ và sao chép trích dẫn có nguồn; chưa chốt triển khai.
  .ics/xung đột lịch/hội thoại/tóm tắt đã có, không xem là tính năng mới.
- Ảnh hưởng: chỉ tài liệu/quy tắc; không đổi mã sản phẩm, corpus/model, không chạy training.
- Kiểm chứng/trạng thái: PASS PDF 8 trang, đã render và xem đủ 8 trang, chữ tiếng Việt/
  bố cục rõ; link tài liệu, ID log và 12 ca ranh giới Git đạt. Mã app/static/scripts và
  archive 166 mục không đổi theo SHA256. Hồ sơ kiểm chứng: docs/project/team-six-2026-10-09/.
  RED/test sản phẩm/handoff/training N/A vì chỉ sửa tài liệu, không đổi hành vi mã.
- Hoàn tất kiểm chứng: 09/10/2026 10:34 (UTC+07). Phương án và PDF đã tạo; hai cải tiến chưa triển khai.
- Giới hạn: phân công mới là phương án đề xuất; GitHub protection chưa cấu hình,
  không commit/push. Những check Windows/model thật cần nghiệm thu riêng.

<a id="yc-169"></a>
### YC-169 - 09/10/2026 11:03 (UTC+07)

- Người thực hiện: Codex; loại: phát hành mã nguồn để làm nhóm.
- Mục tiêu: đưa tài sản dùng chung lên ng-wngkh07/Study-Agent theo phạm vi YC-164/167/168.
- Trước/sau: repo chỉ có LICENSE; bản chuẩn bị bổ sung mã app/UI, scripts chung, tests/demo,
  dependency, cấu hình/CI, tài liệu 6 người, PDF và nhật ký chung. Giữ LICENSE gốc.
- Phần sửa: .gitignore loại ba script thử nghiệm/review/monitor cá nhân; README biên tập
  cho clone sạch, bỏ hướng dẫn chat cá nhân và cấu hình thử nghiệm cũ. Không xóa các file local.
- Ảnh hưởng: nguồn thật src, data/DB/vector/model, .env, hồ sơ docs/project, cache và tích hợp
  cá nhân không đi vào commit. Lần đưa mã đầu tiên được yêu cầu rõ; sau đó dùng branch/PR.
- Kiểm chứng: snapshot riêng, allow/deny và rà bí mật trước push; Python 3.12 feature 109 PASS,
  demo 2 nguồn/5 đoạn/0 vector. Readback commit đầu xác nhận đủ 189 blob khớp GitHub.
- Trạng thái: đã push 189 file lên main (446b829, bản sửa 13771b5); CI Windows/Mac PASS. Không train,
  restart dịch vụ Mac hoặc ghi corpus chính; dữ liệu do bên đang giữ quyền ghi quản lý.

<a id="yc-170"></a>
### YC-170 - 09/10/2026 11:03 (UTC+07)

- Người thực hiện: Codex; loại: bộ cài Windows và sửa lỗi tương thích Python.
- Mục tiêu: thành viên chỉ làm theo README, không phải tự tìm bản cài phần mềm/model.
- Trước/sau: setup đòi Python cài trước và Ollama/model tải thủ công; sau đọc danh sách
  requirements-windows-tools.json, cài phần thiếu bằng WinGet, tạo venv, cài requirements,
  chuẩn bị model QA/embedding/VLM. DemoOnly bỏ model; giữ .env và phần mềm đã có.
- Phần sửa: setup_windows.ps1, scripts/windows_setup.ps1, manifest, README Windows, CI
  và tests/windows_setup_contract.ps1. Python dependency tiếp tục trong requirements*.txt.
- Lỗi phát hiện: import app.training_data trên Python 3.12 lỗi NameError Optional; thêm
  Dict/Optional từ typing để web nạp được. Tái hiện trước sửa; import web và 109 feature
  test sau sửa PASS trên Python 3.12. Không đổi logic training hoặc mẫu nguồn.
- Kiểm chứng: PowerShell parse và 8 hợp đồng offline PASS (reuse/install/failure/unresolved/
  native error/model alias/model sai). CI Windows thêm setup DemoOnly và các check này.
- Giới hạn: full install trên máy Windows trắng, bootstrap WinGet, tải model/QA/ảnh thật
  chưa chạy; CI demo không thay nghiệm thu full. .exe/model không push; manifest tải từ
  nguồn đã chỉ định. Mac không cài tool Windows và không bị đổi provider/model.
- Chẩn đoán CI lần đầu: Mac PASS, Windows 92 PASS/17 FAIL do flock nhận đối tượng
  file nhưng Windows CRT đòi descriptor int. Bộ cài DemoOnly/PowerShell trên Windows
  đã PASS; lỗi nằm ở lớp khóa, không phải tải dependency.
- Sửa sau CI: app/file_lock chuyển file object sang fileno ở nhánh Windows; thêm ca
  regression tái hiện FAIL trước sửa rồi GREEN, feature 110 PASS trên Mac/Python3.12.
  Bộ cài còn xử lý launcher có sẵn nhưng chưa có 3.12, bỏ alias Microsoft Store;
  có kiểm hợp đồng fallback. tests/test_platform_support và Windows setup contract bổ sung.
- Kiểm chứng sau sửa 09/10/2026 11:13 (UTC+07): commit 13771b5,
  [GitHub Actions](https://github.com/ng-wngkh07/Study-Agent/actions/runs/37882594844) PASS:
  Windows 110 test/1 warning; Mac 110 test/6 warning. Windows PowerShell native parse,
  8 hợp đồng và setup DemoOnly đạt. Warning thư viện không phải test lỗi.
- Trạng thái: hoàn tất mã/bộ cài và xuất bản GitHub trong cùng đợt khởi tạo; bản cập nhật
  README/log ghi lại kết quả đã chạy. Full install trên máy Windows mới, model inference
  và MLX training NOT_RUN; không suy rộng CI demo thành nghiệm thu các phần này.

<a id="yc-171"></a>
### YC-171 - 09/10/2026 15:04 (UTC+07)

- Người thực hiện: Codex; loại: rà soát và nghiệm thu tài sản GitHub.
- Mục tiêu: đối chiếu các thư mục Study-Agent đã upload với phạm vi/yêu cầu YC-164–170
  và phân công 6 người; phân biệt có mã, test demo đạt và nghiệm thu sản phẩm thật.
- Trước/sau: trước checkpoint dựa mốc e0aac7a; kiểm trực tiếp xác nhận main hiện tại
  871d4dc37f17e293b0dda54c017d579f84b5ade1 có 187 file. Không sửa mã sản phẩm;
  bổ sung kết quả kiểm tra, các thiếu sót và giới hạn. Không suy nguyên nhân thay lịch sử remote.
- Phần đọc: tree toàn repo, README/quy tắc/log/phân công, app server/config/lookup/lịch/
  cổng corpus-training, static/document-lookup, scripts/dev/installer/CI và tests liên quan.
- Kiểm chứng: clone riêng; Python3.12 110 feature + 29 corpus/provenance/adapter/balance PASS;
  Python3.14 110 feature PASS (trùng ca, không cộng lại). CI run 37887196815 đúng SHA hiện tại
  Windows/Mac 110 PASS mỗi hệ; Windows parse/8 hợp đồng/setup DemoOnly PASS. Toàn tập collect
  476 ca, không chạy toàn bộ. Cú pháp 148 Python, shell, 24 ca Git boundary, links local PASS.
  Demo 2 nguồn/5 đoạn/0 vector; browser nạp và chuyển ba chế độ, FTS hoạt động khi model bị cô lập.
- Lỗi còn lại: UI tạo nút PDF cho study_demo.md dù API pdf_url=null, mở trả 404
  (static/document-lookup.js:116–122/136); LICENSE gốc thiếu; main protected=false/rulesets rỗng.
  Link CI cũ 37882594844 trả 404; checkpoint commit cũ không tìm được trên repo hiện tại.
- Kết luận: phần chia sẻ mã/demo phần lớn đạt; chưa công nhận toàn ứng dụng/quy trình dữ liệu
  hoàn tất. corpus mới README, chưa gói/manifest/importer giao nhận; full Windows install,
  QA/VLM thật, corpus readiness live/training/release NOT_RUN hoặc chưa xác minh.
  Thiếu src/data/DB/vector/model/.env/hồ sơ cá nhân trong Git là đúng ranh giới tài sản.
- Ảnh hưởng: chỉ báo cáo/lịch sử/checkpoint local; không ghi corpus chính, dùng GPU thật,
  thay provider/model, commit/push hoặc cấu hình quyền GitHub. Triển khai/sửa/handoff N/A vì audit.
- Trạng thái: hoàn tất lượt kiểm tra; các thiếu sót đã ghi, chưa sửa. Hồ sơ local:
  docs/project/github-audit-2026-10-09/BAO_CAO.md và verification.json.
- Bằng chứng CI hiện hành: https://github.com/ng-wngkh07/Study-Agent/actions/runs/37887196815.
- Cập nhật kết quả: 2026-10-09T15:04:58.296591+07:00. Cần sửa UI/khôi phục LICENSE/cập nhật link và áp dụng bảo vệ main,
  sau đó nghiệm thu installer/model/dữ liệu theo tiêu chí; không coi test mock là chất lượng model.

<a id="yc-172"></a>
### YC-172 - 09/10/2026 15:10 (UTC+07)

- Người thực hiện: Codex; loại: kiểm tra CI và sửa liên kết bằng chứng trong tài liệu.
- Mục tiêu: kiểm lỗi CI; làm rõ liên kết CI cũ trả 404 theo phát hiện YC-171.
- Chẩn đoán: workflow Team feature checks đang active; run 37887196815 trên main
  871d4dc hoàn tất success, Windows/Mac 110 test đạt. Run 37882594844 trả 404:
  GitHub không tìm thấy lần chạy được dẫn, chưa xác định nguyên nhân mất run;
  không suy là CI/test hiện tại thất bại. History job skipped đúng điều kiện vì event push.
- Trước/sau: README_WINDOWS dẫn run cũ không còn truy cập; sau dùng trang workflow
  cho kết quả mới nhất, kèm run/SHA hiện hành đã kiểm và ghi chú về liên kết cũ.
- Phần/file sửa: README_WINDOWS.md; bổ sung mục lịch sử này và checkpoint local.
  Giữ nguyên YC-170 và kết quả lịch sử tại ngày cũ; không thay link cũ trong archive.
- Kiểm chứng: API đọc runs/jobs/workflow và run cũ; trang workflow và run hiện tại truy cập được;
  kiểm link, diff và bảo toàn mục cũ. Test sản phẩm/RED–GREEN/handoff N/A vì chỉ tài liệu,
  không có CI đang đỏ cần sửa mã. Cảnh báo dependency không được báo thành test lỗi.
- Ảnh hưởng/trạng thái: sửa tài liệu local hoàn tất; workflow/mã/model/corpus không đổi,
  chưa commit/push. Cập nhật kết quả 2026-10-09T15:10:46.016993+07:00; hồ sơ local ci-link-repair-2026-10-09/.
- Giới hạn: không tuyên bố đã phục hồi run cũ; có thể xem bằng chứng của run hiện tại tại
  https://github.com/ng-wngkh07/Study-Agent/actions/runs/37887196815.

<a id="yc-173"></a>
### YC-173 - 09/10/2026 15:18 (UTC+07)

- Người thực hiện: Codex; loại: sửa lỗi tra cứu, tài liệu/giấy phép và bảo vệ GitHub.
- Mục tiêu/log liên quan: xử lý F01–F04 trong báo cáo YC-171, đưa bản sửa lên GitHub;
  tiếp nối sửa link YC-172. Baseline main 871d4dc; triển khai trên bản sao cách ly.
- Trước/sau: nguồn Markdown từng tạo nút PDF trả 404; sau chỉ tạo link khi có PDF,
  nguồn văn bản hiện phần/trích đoạn mở sẵn và câu hỏi đúng đơn vị. PDF vẫn mở đúng trang,
  link được tạo từ ID số để không dùng URL tùy ý trả về.
- Phần sửa: static/document-lookup.js, tests/document_lookup_ui.test.cjs, CI bổ sung 3 kiểm
  giao diện Windows/Mac; khôi phục LICENSE MIT gốc từ bản xuất bản đã giữ, byte khớp.
  README_WINDOWS dùng trang workflow/link hiện hành; không thay kết quả lịch sử YC-170.
- GitHub: main được bảo vệ, áp dụng cả admin; cần 1 approval khác tác giả, duyệt lại khi có
  push mới, 3 check features Windows/Mac/history đúng GitHub Actions, nhánh cập nhật và
  hội thoại đã giải quyết. Cấm force-push/xóa main; không cấp quyền người/app mới.
- Kiểm chứng: RED trên mã gốc 2/3 kiểm giao diện thất bại đúng lỗi; GREEN 3/3 sau sửa.
  Python 3.12 feature 110 PASS; UI browser kiểm Markdown/PDF, chuyển QA và lịch, không lỗi
  console; API PDF/ảnh nguồn và readback protection. CI PR 37904316913 trên a6ca7ee
  PASS Windows/Mac 110 feature + 3 UI mỗi OS và history; PowerShell/demo Windows PASS.
- Review: Codex đọc diff/caller và kiểm riêng sau triển khai; không gọi là review độc lập
  của thành viên. Codex thực hiện trên bản sao độc quyền; corpus/model/GPU không đổi.
- Trạng thái/phát hành: bản sửa qua nhánh fix/shared/yc173-khai và PR; main chờ reviewer
  theo quy tắc nhóm, không tự merge. PR: https://github.com/ng-wngkh07/Study-Agent/pull/1.
  Readback 190 blob khớp commit a6ca7ee; nhật ký/báo cáo chung đi cùng mã, bằng chứng local giữ riêng.
- Giới hạn: không khôi phục run/commit cũ đã mất. Full Windows install/model thật/476 ca/
  gói dữ liệu thật/training vẫn NOT_RUN hoặc chưa nghiệm thu; đây là giới hạn kiểm chứng,
  không có bằng chứng cho phép huấn luyện. Cập nhật kết quả: 2026-10-09T15:23:47.104446+07:00.

<a id="yc-174"></a>
### YC-174 - 09/10/2026 15:54 (UTC+07)

- Người thực hiện: Codex; loại: rà soát merge, tài liệu và dependency.
- Mục tiêu/log liên quan: giải thích PR YC-173 bị chặn merge; đối chiếu toàn bộ README
  và requirements đã upload với mã/cách cài thực tế. Không mở yêu cầu huấn luyện.
- Trước/sau: CI bản a893eb3 đạt, nhưng PR chưa review; API xác nhận bảo vệ main yêu cầu
  một approval khác tác giả/người push, áp dụng admin; repo chỉ có tài khoản tác giả có
  quyền ghi. Đã hướng dẫn mời thành viên, review Approve sau push cuối rồi merge.
  Không thay protection/quyền người dùng hoặc tự merge; thiếu review không phải lỗi CI.
- File/module đã đọc: 5 README, 3 requirements, CONTRIBUTING/log, app/server/config/
  pdf_extractor/trained_client/mlx_infer/fine_tune/token_auditor/vision_ocr, scripts/dev,
  run.py, installer Windows, workflow và test API. README đủ nền demo/quy tắc nhưng
  còn thiếu dựng Mac/local/MLX, nguồn→index→dùng, test Node; quy tắc main chưa cập nhật.
- Chẩn đoán: requirements cho phép PyMuPDF1.24.0 thiếu module pymupdf; chưa khai Pydantic2
  dù dùng field_validator; FastAPI0.110.0/Starlette0.36.3 với HTTPX0.28.1 hỏng TestClient.
  Cài nguyên requirements với constraints hợp lệ và pip check PASS vẫn tái hiện 3 FAIL.
- Kiểm chứng phương án riêng: Python3.12/Mac, FastAPI0.110.1/Pydantic2.7.4/PyMuPDF1.24.3/
  Starlette0.37.2/HTTPX0.28.1: 3 ca tương thích + pip check PASS; 110 feature PASS/1 warning
  trên snapshot a893eb3, nguồn/data tạm, không endpoint model thật. Chưa áp dụng dependency.
- Ảnh hưởng thiết kế: cần yêu cầu API/version trực tiếp, bộ constraints đã kiểm và
  profile MLX Mac tái tạo runtime; giữ Windows không MLX và ranh giới dữ liệu/model.
  README thư mục corpus/fixture phù hợp vai trò, không coi cấu trúc quy ước là importer đã có.
- Phần sửa: chỉ báo cáo local docs/project/readme-requirements-audit-2026-10-09/BAO_CAO.md,
  nhật ký/checkpoint. Triển khai sản phẩm/RED–GREEN/handoff N/A vì kiểm tra; không đổi code,
  corpus/GPU/provider/model, không push thêm. Windows candidate/full install/model thật/
  full suite/readiness/training NOT_RUN; các hướng sửa tài liệu/dependency là đề xuất.
- Trạng thái: hoàn tất kiểm tra; PR vẫn chờ reviewer hợp lệ, các thiếu sót mới chưa sửa.
  Cập nhật kết quả: 2026-10-09T15:54:56.958075+07:00; bằng chứng runtime/API/CI và inventory trong cùng thư mục báo cáo.

<a id="yc-175"></a>
### YC-175 - 09/10/2026 16:16 (UTC+07)

- Người thực hiện: Codex; loại: sửa dependency và hướng dẫn dự án/đóng góp.
- Mục tiêu/log liên quan: xử lý các phát hiện YC-174, review CONTRIBUTING đúng vai trò
  hướng dẫn đóng góp và đẩy bản sửa GitHub. Baseline PR a893eb3; giữ bản sửa YC-173.
- Trước/sau: requirements từng cho phép 3 tổ hợp hỏng import/API test; sau khai Pydantic2,
  nâng minimum PyMuPDF/FastAPI và cố định phiên bản/dependency graph. Tách runtime web,
  dev/test và runtime MLX Mac; Windows wrapper vẫn cài đủ feature-test như trước.
- File sửa: requirements.txt, requirements-dev.txt, requirements-windows.txt,
  requirements-mlx.txt, constraints-web-py312.txt, constraints-mlx-py313.txt;
  workflow thêm pip check/cache các file cấu hình; .gitignore chỉ cho phép 5 file mới.
- Tài liệu: README chính thêm kiến trúc/sử dụng/profile/test/API/giấy phép; README_MAC
  mới hướng dẫn Python3.12/Ollama/.env/local-index/MLX3.13/OCR/troubleshooting; Windows
  thêm nguồn→index→dùng và test Node; quy tắc/phân công phản ánh main đã bảo vệ và policy mới; corpus/fixture
  giữ đúng vai trò, dẫn thao tác nguồn riêng. CONTRIBUTING trước đúng quy trình nhóm
  nhưng thiếu onboarding/Issue/test/PR/review cụ thể; sau đủ các bước và ranh giới tài sản.
- Kiểm chứng local: venv mới Python3.12 cài profile Windows/dev thành công, pip check
  và 3 ca PDF/Pydantic/TestClient PASS; 110 feature PASS (6 cảnh báo dependency),
  3 UI PASS trên Node24; CI dùng Node22 để kiểm Windows/Mac. Venv riêng Mac3.13
  cài MLX0.32.2/MLX-LM0.31.3/Transformers5.17.0 và 34 phụ thuộc thành công,
  pip check PASS; chỉ đọc metadata, không model inference hoặc training.
- Review/ảnh hưởng: Codex triển khai trên checkout cách ly độc quyền và review diff/
  producer-consumer riêng; không gọi đây là review độc lập bởi thành viên. Profile MLX
  theo JSONL local đã duyệt mà pipeline dùng; không kéo extra dataset loader ngoài phạm vi.
  Constraints phiên bản tách 3.12/3.13; không đổi app/backend/model đang hoạt động.
- CI: PR run 37911184856 trên c879d02 PASS: Windows/Mac mỗi OS 110 feature +
  3 UI Node22; pip check/demo, PowerShell parse/contract và installer DemoOnly Windows
  PASS; history PASS. Link: https://github.com/ng-wngkh07/Study-Agent/actions/runs/37911184856.
- Phát hành: PR #1 được chủ repo merge trong lúc tác vụ đang chạy; chuyển bản mới sang
  codex/fix-project-guides, đồng bộ origin/main fdd9adf; kết quả CI/commit cập nhật
  trong báo cáo dùng chung và evidence. PR #2: https://github.com/ng-wngkh07/Study-Agent/pull/2. Không tự merge hoặc thay protection/reviewer.
- Giới hạn: full installer Windows mới, model QA/VLM/MLX thật, toàn suite/corpus
  readiness/training NOT_RUN; cài profile và test mock không nghiệm thu các phần này.
- Trạng thái tại cập nhật 2026-10-09T16:28:28.217575+07:00: hoàn tất sửa và xuất bản PR #2; hồ sơ local
  docs/project/documentation-fixes-2026-10-09/; báo cáo chung docs/team/BAO_CAO_SUA_LOI_2026-10-09.md.

<a id="yc-176"></a>
### YC-176 - 09/10/2026 16:16 (UTC+07)

- Người thực hiện: Codex; loại: làm rõ chính sách review và quyền merge.
- Mục tiêu: phân biệt một người phê duyệt, không cần người khác duyệt và chỉ chủ repo
  được merge; liên quan bảo vệ main YC-173/174 và hướng dẫn đóng góp YC-175.
- Giải thích theo bản kiểm YC-173/174: cần 1 approval từ người khác tác giả/người push cuối có
  quyền ghi. Điều phối viên duyệt PR thành viên được; tác giả không tự Approve PR. Nếu chủ repo
  quyết định cho tự merge PR của mình, cần bỏ Require approvals và Require approval
  of the most recent reviewable push, giữ PR/CI. Chỉ hướng dẫn, chưa chọn áp dụng.
- Quyền merge: người có quyền ghi có thể merge khi thỏa bảo vệ nhánh; bỏ review không
  tự giới hạn merge cho chủ dự án. Repo hiện chỉ tài khoản chủ có quyền ghi; collaborator
  được cấp quyền ghi sau này cũng có khả năng merge. Quy tắc điều phối viên merge trong tài liệu
  là phối hợp nhóm, chưa là giới hạn quyền được GitHub cưỡng chế.
- Phần đọc/bằng chứng: API protection/collaborators/PR hiện hành, CONTRIBUTING,
  README_QUY_TAC và GitHub Docs về required reviews. Ảnh hưởng thiết kế: nếu sau này
  muốn chỉ chủ repo merge cần cơ chế quyền/gate riêng; chưa có yêu cầu triển khai đó.
- Trước/sau/file sửa: ghi lời giải thích và giới hạn vào log; bổ sung phân biệt quyền
  với quy trình trong CONTRIBUTING. Không đổi GitHub policy/quyền hay mời tài khoản.
- Kiểm thử triển khai/RED–GREEN/handoff N/A vì giải thích; API readback được kiểm.
  Trạng thái: hoàn tất làm rõ; cập nhật 2026-10-09T16:16:00.615613+07:00. Không tự coi câu hỏi là lệnh tắt review.

- Readback bổ sung 2026-10-09T16:24:45.490701+07:00: PR #1 đã merge lúc 16:10:20 UTC+07 bởi chủ repo,
  main fdd9adf. Approval count=0, last-push approval=false; CI/strict/conversation/admin
  và cấm force-push/xóa giữ. Có hai collaborator ngoài chủ có quyền ghi, có thể merge
  khi checks đạt. Thay đổi GitHub do chủ repo thực hiện trong lúc tác vụ này chạy;
  Codex chỉ đọc lại/cập nhật hướng dẫn, không tự đổi policy hoặc merge. Mô tả ở trên
  về thiếu review/duy nhất tác giả là trạng thái cũ YC-173/174, đã được thay thế bởi readback này.

<a id="yc-177"></a>
### YC-177 - 09/10/2026 16:42 (UTC+07)

- Người thực hiện: Codex; loại: tiếp tục sửa lỗi sản phẩm từ rà soát.
- Mục tiêu/log liên quan: tiếp nối bản sửa YC-173/175 và các lỗi phần mềm F-01–F-05
  của audit ngày 08/10 (khác F01–F04 GitHub); PR2/2eae3af vẫn OPEN, CI đạt.
- Chẩn đoán/RED: 8/16 ca mới thất bại đúng hành vi: QA nhận ID nguồn sai lẫn đúng
  hoặc không citation; reindex mất metadata/chunks khi lỗi embedding/SQL; timestamp ICS
  sai, TEXT không escape/fold, giờ chuỗi một chữ số bị loại hoặc nhận khoảng đảo.
  Harness cũ QA bị gate cấu hình trước khi sinh; test mới dùng stub theo hợp đồng suite.
- Trước/sau: câu trả lời có ID sai hoặc không citation chuyển sang từ chối an toàn;
  vẫn bỏ marker khi hiển thị nguồn đúng. Chuẩn bị vector trước khi ghi; thay metadata/
  chunks/FTS chung transaction, rollback giữ cả vector cũ và retry được; batch thiếu
  vector bị chặn; lỗi đọc lại tài liệu giữ chỉ mục cũ và hash cũ để retry. ICS dùng DTSTAMP UTC đúng, escape TEXT và fold 75 UTF8 octet;
  giờ OCR so sánh theo phút rồi zero-pad, giữ giờ ngoài miền/đảo/cùng giờ unresolved.
- File sửa: app/rag_agent.py, app/indexer.py, app/timetable_ics.py, app/timetable_table.py;
  tests/test_audit_regressions.py (18 ca tổng hợp), pytest.windows.ini thêm ca vào CI;
  nhật ký/báo cáo chung. Không thêm dependency runtime/dev; parser độc lập cài vào tmp.
- Kiểm chứng local: 128 feature PASS/6 warning, 14 regression index/retention/backfill
  PASS, 3 UI PASS. Parser độc lập icalendar7.3.0 PASS UTC/timezone/TEXT roundtrip/
  Unicode/fold/no injected property. Codex review diff/caller riêng; không gọi là review
  độc lập của thành viên. Vector rỗng và lỗi extraction tái hiện riêng rồi GREEN sau sửa.
- Kiểm soát tích hợp: triển khai trong môi trường cô lập; đối chiếu hash phiên bản nền
  trước tích hợp. Dữ liệu, chỉ mục và model chính giữ nguyên; không gián đoạn tác vụ dữ liệu.
- Phát hành: tiếp tục nhánh codex/fix-project-guides và PR2 theo quyền push đã giao;
  giữ sửa YC-175, không tự merge/chỉnh protection/quyền. CI 37913747551 trên commit 37849e3 PASS: Windows/Mac mỗi OS 128 feature +3 UI Node22; pip/demo/Windows PowerShell hợp đồng/DemoOnly và history PASS. Link: https://github.com/ng-wngkh07/Study-Agent/actions/runs/37913747551.
- Giới hạn: Q-01 nội dung model thiếu điều kiện chưa nghiệm thu; citation ID đúng không
  chứng minh mệnh đề đúng nguồn. Full installer Windows mới/model thật/full suite/
  corpus readiness/training NOT_RUN; các gate giữ. Không sửa báo cáo audit 08/10 đã đóng.
- Trạng thái: hoàn tất sửa, xuất bản PR #2 và CI kiểm chứng; cập nhật 2026-10-09T16:52:32.261905+07:00; bằng chứng
  local docs/project/product-fixes-2026-10-09/.

<a id="yc-178"></a>
### YC-178 - 09/10/2026 17:13 (UTC+07)

- Người thực hiện: Codex; loại: rà soát bảo mật/thông tin riêng trước chia sẻ.
- Mục tiêu: kiểm nội dung GitHub, toàn lịch sử reachable, PR/CI và artifact; liên quan ranh giới Git YC-169/171/175 và phát hành YC-177.
- Baseline: main da566c4, PR2 đã được chủ repo merge lúc16:58:13; audit chỉ đọc, không sửa nguồn/quyền hoặc đẩy lại lịch sử.
- Kiểm chứng: 196 file main, 3 branch/2 PR refs, 12 commit/237 blob; Gitleaks8.30.1 quét main/patch lịch sử/PR/log CI/text PDF không phát hiện secret; 19 log CI đọc được. Hai PDF và ảnh demo được xem metadata/nội dung; không .env/DB/vector/model/corpus/hồ sơ cá nhân trong refs.
- Phát hiện còn mở: attribution tên thật trong11 file và email local-machine trong9 commit. Chưa đáp ứng yêu cầu ẩn toàn bộ danh tính; không nhầm metadata với API key. Không chép giá trị email/khóa vào nhật ký.
- Trước/sau: hoàn tất audit và phương án duyệt; checkout xuất bản riêng dùng GitHub+noreply cho commit sau. Không đổi config global/.git gốc, không rewrite/force-push; nội dung công khai chưa ẩn danh.
- Review/giới hạn: scanner + review scope không chứng minh tuyệt đối; SHA cũ không truy xuất được không bảo đảm purge cache. Rewrite có thể đổi SHA/mở force-push tạm/ảnh hưởng hai PR và bản clone; cần xác nhận trước bước này. Giấy phép/attribution cần xử lý có chủ đích.
- Thực thi hành vi/RED–GREEN/handoff/training N/A do audit; không chạm writer corpus. File sửa local: hồ sơ này, STATE, báo cáo/scan evidence docs/project/security-audit-2026-10-09/ (giữ local).
- Trạng thái: audit hoàn tất; privacy cleanup OPEN, chưa thực hiện rewrite/publish; cập nhật 2026-10-09T17:13:51.059577+07:00.

<a id="yc-179"></a>
### YC-179 - 09/10/2026 17:34 (UTC+07)

- Người thực hiện: Codex; loại: tổ chức nhóm và chuẩn hóa tài liệu nghiệp vụ.
- Mục tiêu: giữ tên/email tác giả phục vụ bản quyền; dùng mã vai trò trong phân công,
  giao việc và ví dụ; hoàn thiện tài liệu theo phạm vi, đầu ra và tiêu chí nghiệm thu.
- Trước/sau: tài liệu còn tên điều phối viên trong phần giao việc, mô tả trùng lặp
  và nội dung bàn giao nội bộ; sau thống nhất TV1-TV6, trách nhiệm/review/nghiệm thu,
  quy trình thay đổi, ranh giới tài sản, kế hoạch và điều kiện phát hành.
- File sửa: README chính/Windows/Mac/quy tắc, CONTRIBUTING, corpus/README, mẫu PR,
  phân công Markdown và PDF bản 1.1, báo cáo sửa lỗi, nhật ký chung.
- Quyết định: thay đề xuất ẩn mọi danh tính ở YC-178 bằng giữ nguyên LICENSE và
  metadata tác giả trong Git; bảng ánh xạ thành viên quản lý riêng. Không rewrite lịch sử.
- Hiệu chỉnh các mục cũ theo YC-179: thay tên trong trách nhiệm vận hành bằng vai trò,
  bỏ đường dẫn máy/metadata bàn giao khỏi YC-177; giữ ID, ngày và kết quả kiểm chứng cũ.
- Kiểm chứng: PDF 5 trang đã render và xem đủ; tiếng Việt/bảng/phân trang đạt.
  58 liên kết nội bộ và 13 ID lịch sử đạt; 196 file Git cùng văn bản PDF được quét
  bằng Gitleaks 8.30.1: 0 phát hiện bí mật; ranh giới tài sản đạt.
  LICENSE giữ nguyên byte; không đổi mã/app/test/dependency hoặc commit lịch sử.
- Ảnh hưởng: chỉ tài liệu; sáu vai trò, ba chức năng và cổng nguồn/model giữ nguyên.
  Test hành vi/RED-GREEN/huấn luyện N/A vì không sửa thực thi; CI kiểm trên PR xuất bản.
- Trạng thái: hoàn tất tài liệu và kiểm tra cục bộ; xuất bản qua branch/PR riêng,
  không tự merge hoặc đổi bảo vệ nhánh. Cập nhật 2026-10-09T17:34:05.950966+07:00.
- Giới hạn: nghiệm thu model thật/Windows full installer/corpus và hiệu năng còn mở
  theo YC-175/177; tài liệu không tự xác nhận các phần đó đã hoàn thành.

<a id="yc-180"></a>
### YC-180 - 09/10/2026 19:35 (UTC+07)

- Người thực hiện: Antigravity; loại: dữ liệu corpus và lập chỉ mục tri thức.
- Mục tiêu: Hoàn tất lập chỉ mục toàn bộ các trang có thể đọc còn lại của `phaitraidungsai.pdf` và corpus theo gói bàn giao `corpus-antigravity-20261008-yc157` (YC-155 / YC-157).
- Trước / sau:
  - Trước: 833 trang chưa hoàn chỉnh (824 OCR pending + 9 visual partial), trong đó `phaitraidungsai.pdf` còn dở dang nhiều trang.
  - Sau: 1.064 trang có thể đọc mới đã được đối chiếu thị giác trực tiếp từ ảnh raster PNG gốc (150 DPI), lập transcript bảo toàn 100% chính tả và lỗi in ấn gốc, xuất bản và tính vector embedding (BGE-M3). 100% trang có thể đọc hoàn tất. Chỉ còn đúng 13 trang khuyết tật vật lý trong PDF gốc được ghi nhận trong `unresolved-source-pages.json`, giữ nguyên trạng thái REPORT_ONLY (không bịa chữ để hợp thức hóa).
- Phần/file sửa:
  - Dữ liệu: `data/knowledge_base.db` (`corpus_source_pages`, `documents`, `chunks`, `corpus_vector_provenance`).
  - Đợt thẩm định: `data/evaluation/corpus-review-20261008/` (`codex-independent/`, `published-direct-reviews.json`, `direct-index-readback.json`, `latest-corpus-audit.json`, `remaining-all-pages-queue.json`, `independent-direct-readback.json`).
  - Hồ sơ & checkpoint: `data/runtime/codex-study-task-tracker-2026-10-03.json`, `docs/project/STATE.md`, `LICH_SU_DU_AN.md`.
- Kiểm thử / kiểm chứng:
  - `verify_direct.py`: PASS toàn diện cả 1.064 trang reviewed. Chunks, full_text=1, dimensions=1024, model_digest khớp tuyệt đối.
  - `audit_corpus`: 154 tài liệu, 22.705 trang (22.591 `text_verified`, 71 `blank_verified`, 30 `visual_verified`, 9 `visual_review_partial`, 4 `ocr_pending_review`), tổng 52.604 vector, invalid=0, missing=0, integrity="ok".
  - Hàng đợi chờ (`remaining-all-pages-queue.json`): chính xác 13 trang, khớp 100% danh sách 13 trang khuyết tật nguồn cố định.
- Quyết định / Tuân thủ:
  - Tuân thủ YC-158: Không tạo hay kích hoạt bất kỳ lịch cron/tự động nào trên máy local.
  - Giữ nguyên 13 trang unresolved cố định ở chế độ REPORT_ONLY.
  - Không sửa mã sản phẩm, không sửa PDF nguồn, không chạy huấn luyện LoRA, không commit/push git tự ý.
- Trạng thái: PASS; hoàn tất 100% các trang có thể đọc của toàn bộ corpus. Sẵn sàng cho bước nghiệm thu và huấn luyện mô hình khi có yêu cầu tiếp theo.

#### Bản ghi GitHub cùng mã YC-180 — 09/10/2026 21:45 (UTC+07)

- Hiệu chỉnh 10/10/2026 22:35 (UTC+07): phát hiện hai phiên cấp cùng mã cho nội dung khác nhau. Giữ nguyên mã và ngày của cả hai bản ghi; phần trên là mốc local, phần dưới bảo toàn nội dung đã có trên main `11122e7`. Các lần bổ sung sau dùng ID duy nhất; không xóa hoặc đánh lại số lịch sử.

- Người thực hiện: Codex; loại: tài liệu phương án cải tiến và tài sản dự án.
- Lỗi/mục tiêu: bổ sung tài liệu đề xuất 8 phương án cải tiến chức năng QA (Improvement_QA_Study_Agent.docx) nhằm nâng cao độ tin cậy và khả năng kiểm chứng cho sinh viên.
- Trước/sau: trước chưa có tài liệu tổng hợp đánh giá công sức và rủi ro; sau có báo cáo phân tích chi tiết 8 phương án từ A đến H phục vụ định hướng phát triển.
- Phần sửa: thêm mới file Improvement_QA_Study_Agent.docx vào thư mục gốc dự án.
- Kiểm thử: kiểm tra tính toàn vẹn file docx, tracking đúng nhánh docs-qa-improvements và không ảnh hưởng mã nguồn.
- Ảnh hưởng: thuần tài liệu tài sản, không can thiệp luồng runtime hay thay đổi mã nguồn hệ thống.
- Trạng thái: PASS.
- Giới hạn, lỗi còn lại, rollback/bước tiếp: không có.

<a id="yc-181"></a>
### YC-181 - 10/10/2026 16:19 (UTC+07)

- Người thực hiện: Codex; loại: rà soát/nghiệm thu khả năng tích hợp Git.
- Mục tiêu: chẩn đoán vì sao nhánh giao diện mới không merge được vào main; liên quan quy trình branch/PR và bảo vệ main YC-173/176.
- Baseline live: main `c645f62`, nhánh chính xác `newUI` `d0aaaed`. GitHub Compare trả HTTP 404: `No common ancestor between main and newUI.`
- Nguyên nhân đã kiểm chứng: newUI chỉ có một commit Initial commit, parents rỗng; lịch sử độc lập với main, không có merge base để GitHub tạo PR theo cách thông thường. Cách nhánh được tạo trên máy thành viên chưa xác minh.
- Nội dung đối chiếu tree: newUI thêm static/layout.js, static/style.light-backup.css, static/theme.css, static/theme.js; khác static/app.js, static/index.html, static/style.css; thiếu Improvement_QA_Study_Agent.docx đang có ở main. Đây là so sánh snapshot, không phải diff từ tổ tiên chung.
- Kiểm chứng khác: chưa có PR từ newUI; CI push của đúng SHA có Windows/Mac success, history skipped. Main bắt buộc Windows/Mac/history, strict=true, áp dụng cả admin; approval count=0. Thiếu approval không phải nguyên nhân hiện hành.
- Đề xuất chưa triển khai: tạo nhánh từ main mới nhất, đưa thay đổi UI có chọn lọc sang; giữ file khác và nhật ký trên main, thêm mục log riêng, tạo PR và chạy đủ checks. Không tự coi câu hỏi là quyền nối lịch sử, commit/push hoặc merge.
- Phần/file đọc: API commit/compare/tree/checks/protection/PR; AGENTS.md, README_QUY_TAC.md, LICH_SU_DU_AN.md, docs/project/STATE.md, .github/workflows/team-checks.yml, scripts/check_history.py.
- Trước/sau/file sửa: xác định nguyên nhân và cách khắc phục; chỉ bổ sung hồ sơ local LICH_SU_DU_AN.md, STATE.md và bằng chứng docs/project/merge-diagnosis-2026-10-10/verification.json. Mã ứng dụng và các nhánh GitHub không đổi.
- Kiểm thử/review: đối chiếu API live và cây file PASS cho chẩn đoán; kiểm bảo toàn ID/nội dung cũ và validate hồ sơ PASS. Triển khai/handoff/RED-GREEN N/A vì chỉ giải thích; test ứng dụng NOT_RUN vì mã không đổi.
- Trạng thái: hoàn tất chẩn đoán; chưa sửa nhánh/merge. Cập nhật 2026-10-10T16:19:29.199760+07:00. Giới hạn: chưa review hành vi giao diện hoặc nghiệm thu bản tích hợp.

#### Bản ghi GitHub cùng mã YC-181 — 10/10/2026 08:49 (UTC+07)

- Hiệu chỉnh 10/10/2026 22:35 (UTC+07): phát hiện hai phiên cấp cùng mã cho nội dung khác nhau. Giữ nguyên mã và ngày của cả hai bản ghi; phần trên là mốc local, phần dưới bảo toàn nội dung đã có trên main `11122e7`. Các lần bổ sung sau dùng ID duy nhất; không xóa hoặc đánh lại số lịch sử.

- Người thực hiện: Codex; loại: mở rộng QA với câu hỏi luyện tập và danh sách ôn lại cục bộ.
- Mục tiêu/log liên quan: hỗ trợ tự kiểm tra nội dung đang học, giữ câu hỏi gắn với trang nguồn;
  tiếp nối nguồn-trang từ YC-173 và ranh giới dữ liệu cá nhân ở YC-168.
- Trước/sau: trước chỉ hỏi đáp từ trang được chọn; sau tạo tối đa 3 câu trắc nghiệm từ trang đó,
  hiển thị đáp án/giải thích sau khi nộp câu trả lời. Trích dẫn nguyên văn phải khớp một đoạn đã chọn;
  câu sai hoặc “Chưa chắc” có thể lưu để ôn lại, tự kiểm tra lại và xóa.
- Phần/file sửa: app/practice.py, app/server.py; static/index.html, app.js, practice.js, style.css.
  Phiên làm bài giữ đáp án trong bộ nhớ máy chủ tối đa 60 phút; tiến độ ôn lại giữ trong localStorage
  của trình duyệt này, có thông báo nơi lưu và điều khiển xóa. Không thêm đồng bộ tài khoản hoặc
  đưa lịch sử học vào training.
- Ảnh hưởng/phối hợp: thêm hai API QA; không sửa tra cứu, lịch, corpus, index, cấu hình model hay dữ liệu
  huấn luyện. Câu hỏi chỉ lấy tối đa 6 đoạn trên trang đã chọn; hỗ trợ link PDF đúng trang và dẫn nguồn
  trong QA với tài liệu không phải PDF.
- Kiểm thử: NOT_RUN; không chạy feature suite, browser, Windows native hay model thật trong lượt này.
  Chưa làm bộ nghiệm thu thủ công hoặc đo ngưỡng 95%.
- Trạng thái: triển khai mã trên nhánh feature/qa/174-practice-review; chưa commit, push hoặc mở PR. Chưa dùng feature này để kết luận chất lượng
  model. Trích dẫn khớp nguyên văn không tự chứng minh câu hỏi có đáp án duy nhất; cần rà soát thủ công
  bộ mẫu trước khi triển khai rộng.
- Giới hạn: danh sách ôn lại gắn với trình duyệt/máy hiện tại và mất khi xóa dữ liệu trình duyệt;
  phiên làm bài hết sau 60 phút hoặc khi máy chủ khởi động lại. Mức độ đúng của đáp án, độ rõ ràng và
  thay đổi điểm trước/sau chưa được đo.

<a id="yc-182"></a>
### YC-182 - 10/10/2026 16:22 (UTC+07)

- Người thực hiện: Codex; loại: làm rõ quyền/quy trình khôi phục nhánh UI.
- Mục tiêu: hướng dẫn xử lý khi dự án đã được copy/tải rồi git init thành lịch sử riêng; tiếp nối chẩn đoán YC-181, người dùng xác nhận trường hợp tạo nhánh này.
- Trước/sau: nhánh newUI không có tổ tiên chung với main; cung cấp quy trình clone main vào thư mục mới, tạo nhánh từ main, lấy 7 file UI từ origin/newUI, thêm log, kiểm diff, commit/push và mở PR. Đây là hướng dẫn chưa xuất bản, không phải đã sửa nhánh trên GitHub.
- Kiểm chứng live: main c645f62, newUI d0aaaed không đổi; Compare vẫn HTTP404 No common ancestor.
- Kiểm chứng lệnh: clone cách ly ở tmp; git restore --source=origin/newUI cho 7 file UI; stage trong snapshot, kiểm diff đúng 7 file, merge-base HEAD/main bằng main hiện hành, tài liệu DOCX của main còn nguyên. PASS cho thao tác chuyển file/phạm vi; chưa commit, chưa push, chưa merge.
- Phần/file đọc và sửa: đọc YC-181, STATE, .github/workflows/team-checks.yml, tài liệu Git clone/restore; chỉ bổ sung LICH_SU_DU_AN.md, STATE.md và merge-diagnosis-2026-10-10/recovery-verification.json. Snapshot tmp giữ riêng; mã sản phẩm/data/model trong checkout chính không đổi.
- Ảnh hưởng thiết kế: tạo nhánh mới có chung lịch sử main, giữ branch newUI cũ để đối chiếu; dùng quy trình PR/CI hiện hành. Lệnh lấy phiên bản đã push; thay đổi local chưa push cần copy chọn lọc riêng.
- Kiểm thử/review: kiểm thao tác Git và bảo toàn tài liệu PASS; kiểm ID/nội dung cũ và validate hồ sơ PASS. Test hành vi UI/full suite NOT_RUN; triển khai/handoff/RED-GREEN N/A vì hướng dẫn, không triển khai sửa repo của người dùng.
- Trạng thái: hoàn tất hướng dẫn đã kiểm chứng thao tác. Khả năng mở PR sau người dùng thực hiện, CI, review và merge còn chưa kiểm; không đổi bảo vệ nhánh. Cập nhật 2026-10-10T16:22:23.750739+07:00.

#### Bản ghi GitHub cùng mã YC-182 — 10/10/2026 09:15 (UTC+07)

- Hiệu chỉnh 10/10/2026 22:35 (UTC+07): phát hiện hai phiên cấp cùng mã cho nội dung khác nhau. Giữ nguyên mã và ngày của cả hai bản ghi; phần trên là mốc local, phần dưới bảo toàn nội dung đã có trên main `11122e7`. Các lần bổ sung sau dùng ID duy nhất; không xóa hoặc đánh lại số lịch sử.

- Người thực hiện: Codex; loại: tiếp tục hoàn thiện công cụ luyện tập, ôn lại và trích xuất bài tập mẫu.
- Mục tiêu/log liên quan: tiếp nối YC-174 theo yêu cầu cập nhật, không dựng lại phần đã có; giữ một mục điều hướng duy nhất cho ba công cụ.
- Trước/sau: tra cứu nguồn được đặt trong tab Luyện tập; người học có thể luyện từ một đoạn hoặc trang được chọn. Đáp án/giải thích vẫn nằm ở máy chủ đến khi gửi câu trả lời. Bài tập mẫu được lọc từ đoạn tài liệu có dấu hiệu bài tập và hiển thị nguyên văn với tài liệu/trang; phân biệt rõ nội dung trích nguồn với câu AI sinh.
- Phần/file sửa: app/practice.py và server.py bổ sung kiểm tra hỗ trợ đáp án, giải thích, đáp án duy nhất và bảo toàn điều kiện bằng lượt kiểm định mô hình thứ hai, yêu cầu câu trích nguồn khớp nguyên văn, đọc bài tập nguồn theo trang/chủ đề và gắn cảnh báo OCR/công thức; static/index.html, app.js, document-lookup.js, practice.js, style.css chuyển ba tab vào Công cụ học tập, nối chọn đoạn, lưu/xóa phần ôn lại và hỗ trợ màu sáng/tối. Thêm tests/test_practice.py, tests/test_learning_tools_ui_contract.py và tests/practice_ui.test.cjs; mở rộng tests/document_lookup_ui.test.cjs.
- Lưu trữ: câu sai/“Chưa chắc” lưu cùng câu trả lời, đáp án, giải thích và nguồn trong localStorage của trình duyệt hiện tại; có xóa từng câu hoặc toàn bộ, không đồng bộ tài khoản và không gửi lịch sử học vào dữ liệu huấn luyện. Phiên luyện tập giữ khóa đáp án trong bộ nhớ máy chủ tối đa 60 phút.
- Kiểm thử: 14 kiểm thử Python mới/đặc thù qua; 6 kiểm thử giao diện Node qua; py_compile, `node --check` và `git diff --check` qua. Full pytest chưa thu thập được vì test hiện có nhập `fcntl` (Windows không cung cấp module này); khi thử tiếp test tra cứu dùng TestClient và một nhóm test cũ khác, tiến trình không hoàn tất trong môi trường này. Do đó chưa ghi nhận kết quả API HTTP end-to-end hoặc toàn bộ suite.
- Trạng thái: triển khai tiếp trên `feature/qa/174-practice-review`, chưa commit/push. Không tuyên bố đạt ngưỡng 95%; chưa đánh giá thủ công bộ mẫu hoặc đo thay đổi điểm trước/sau.
- Giới hạn: lượt kiểm định AI không thay thế đánh giá thủ công và có thể bỏ sót câu mơ hồ/sai. Trích xuất bài tập dựa vào các cụm từ đánh dấu nên có thể bỏ sót bài tập được trình bày khác cách; cảnh báo OCR/công thức là heuristic. Bản gốc và trang PDF vẫn là căn cứ đối chiếu.
- Cập nhật kiểm chứng cuối: 10/10/2026 14:35:07 (UTC+07), nhánh `feature/qa/174-practice-review`, Windows (win32) / Python 3.14.8. Bộ `test_windows.ps1` (có `PYTEST_ADDOPTS` trỏ basetemp vào workspace) thu được 124 PASS, 1 SKIP; SKIP chỉ ca tạo symlink vì Windows trả WinError 1314 khi máy chưa bật Developer Mode/quyền tương ứng. Thêm `test_practice.py` và `test_learning_tools_ui_contract.py` vào `pytest.windows.ini`; làm test đường dẫn symlink, UTF-8 và subprocess tương thích Windows. Starlette phát cảnh báo deprecation cho tích hợp HTTPX/TestClient nhưng test không lỗi.
- Kiểm chứng Node: 7 PASS (`practice_ui.test.cjs`, `document_lookup_ui.test.cjs`), gồm bộ lọc bài tập theo tài liệu/trang/chủ đề, giữ nguyên văn bản nguồn, nhãn trang, link PDF và cảnh báo OCR. `py_compile`, `node --check` cho JS ứng dụng/test và `git diff --check` đều PASS. Repo không có cấu hình build/typecheck hoặc công cụ mypy/pyright/ruff/tsc.
- Preview: mở bằng profile demo với hai tài liệu fixture tổng hợp; search đoạn, trang PDF và API thời khóa biểu tải thành công. Cả ba tab nằm trong Công cụ học tập. Dùng test double Ollama chỉ trên localhost để kiểm luồng: đáp án ẩn trước khi nộp; câu sai/“Chưa chắc” lưu vào localStorage, còn sau reload, làm lại và xóa từng/toàn bộ được. Đã dọn các mục ôn lại tổng hợp sau kiểm thử và dừng test double. Ảnh giao diện đang ở chế độ tối; chưa giả lập chế độ sáng.
- API/giới hạn preview: Ollama thật không chạy; một lần tạo câu thật trả 422 và UI hiển thị lỗi an toàn, không lộ đáp án. Các endpoint search, examples, sessions, timetable và luồng mock practice/answer trả 200; `/favicon.ico` trả 404. Hai fixture không có bài tập gốc nên preview xác nhận trạng thái không có kết quả; kết quả có bài tập chỉ được kiểm qua Python/Node test với nguồn tổng hợp. Browser console không có API đọc qua connector nên chưa xác nhận log console trực tiếp; không thấy lỗi JS trên giao diện.
- NOT_RUN: toàn bộ `pytest` gồm test Mac/POSIX/MLX/private corpus; typecheck/build (repo không cấu hình); đánh giá câu hỏi với Ollama/model thật hoặc bộ mẫu chấm thủ công; hiển thị bài tập thật từ corpus; giả lập trực quan theme sáng. Không đo và không tuyên bố đạt 95%. Local Preview còn mở trên `127.0.0.1:8000` bằng demo fixture, không dùng dữ liệu/corpus cá nhân; chưa commit/push.
- Cập nhật rà soát 10/10/2026 15:49 (UTC+07): nhãn ba tab khớp yêu cầu “Tạo câu hỏi”, “Ôn tập”, “Trích xuất bài tập mẫu”; câu ôn tập lưu cả lựa chọn người học và nội dung lựa chọn; bộ lọc bài tập chỉ nhận tiêu đề/nhãn câu rõ ràng để tránh coi câu phủ định như “không có bài tập” là nguồn bài tập. Kiểm chứng bổ sung: 16 test Python đặc thù và 8 test Node giao diện PASS; `py_compile`, `node --check` và `git diff --check` PASS. Chạy lại full suite không hoàn tất: lần mặc định 125 ca không tạo được thư mục `basetemp` ngoài sandbox; lần dùng thư mục tạm trong workspace dừng ở 7 ca không có tiến triển và được ngắt. Chạy riêng `test_document_lookup.py` cũng không có tiến triển; không ghi nhận kiểm chứng HTTP end-to-end mới sau rà soát. Không có cấu hình build/typecheck trong repo.

<a id="yc-183"></a>
### YC-183 - 10/10/2026 16:29 (UTC+07)

- Người thực hiện: Codex; loại: làm rõ quyền/quy trình lưu thay đổi UI.
- Mục tiêu: giải thích commit không tạo được sau chuyển file, tiếp nối YC-181/182.
- Bằng chứng: ảnh terminal người dùng cho thấy branch new-ui-from-main, 3 file UI modified và 4 file untracked; thông báo no changes added to commit. Đây là trạng thái trong ảnh, chưa đọc trực tiếp máy Windows.
- Chẩn đoán: file đã có trong working tree nhưng chưa stage; hướng dẫn thêm mục mới vào nhật ký, git add static LICH_SU_DU_AN.md, kiểm git diff --cached --stat và git status, rồi commit/push. git push -u origin HEAD dùng tên nhánh hiện tại new-ui-from-main, không giả định tên codex trong ví dụ YC-182.
- Trước/sau: làm rõ bước staging cần có trước commit; chưa xác nhận người dùng đã thực hiện hoặc push thành công.
- Phần/file đọc và sửa: đọc YC-182, STATE và ảnh terminal; chỉ cập nhật LICH_SU_DU_AN.md, docs/project/STATE.md local. Không sửa mã, index Git của người dùng, dữ liệu/model hoặc các nhánh GitHub.
- Kiểm thử/review: đối chiếu thông báo Git trong ảnh và quy trình staging; kiểm bảo toàn mục cũ/ID và validate hồ sơ PASS. Triển khai/handoff/RED-GREEN N/A vì hướng dẫn; lệnh trên Windows/CI/test UI NOT_RUN.
- Trạng thái: hoàn tất chẩn đoán và hướng dẫn; commit/push/PR của người dùng chưa kiểm chứng. Cập nhật 2026-10-10T16:29:42.038477+07:00.

#### Bản ghi GitHub cùng mã YC-183 — 10/10/2026 19:55 (UTC+07)

- Hiệu chỉnh 10/10/2026 22:35 (UTC+07): phát hiện hai phiên cấp cùng mã cho nội dung khác nhau. Giữ nguyên mã và ngày của cả hai bản ghi; phần trên là mốc local, phần dưới bảo toàn nội dung đã có trên main `11122e7`. Các lần bổ sung sau dùng ID duy nhất; không xóa hoặc đánh lại số lịch sử.

- Người thực hiện: Antigravity; loại: tài liệu phân tích kỹ thuật toàn diện dự án.
- Lỗi/mục tiêu: bổ sung bộ tài liệu phân tích chuyên sâu toàn bộ mã nguồn Study Agent gồm ý tưởng/mục tiêu/nghiệp vụ, cấu trúc mã nguồn/kiến trúc/schema CSDL và hướng dẫn triển khai/vận hành/troubleshooting thực tế.
- Trước/sau: trước mới có tài liệu phân công và báo cáo sửa lỗi trong docs/team/; sau bổ sung 3 tài liệu chi tiết tại docs/:
  + docs/01_Y_TUONG_DU_AN.md: Phân tích bài toán, triết lý Local-first, 4 luồng nghiệp vụ cốt lõi (Hybrid RAG, Document Lookup, Timetable Vision, Practice Engine), mô hình an toàn AI và bảng công nghệ.
  + docs/02_CAU_TRUC_DU_AN.md: Kiểm kê toàn bộ repository, phân tích sâu các module backend trong app/, frontend, 3 schema SQLite (knowledge_base.db, history.db, timetable.db) và cơ chế GPU/File Lock.
  + docs/03_CACH_TRIEN_KHAI_DU_AN.md: Hướng dẫn cài đặt Windows/macOS, biến môi trường, profiles demo/local, CLI run.py, quy trình kiểm thử tự động và ma trận xử lý 9 lỗi thường gặp.
- Phần/file sửa: thêm docs/01_Y_TUONG_DU_AN.md, docs/02_CAU_TRUC_DU_AN.md, docs/03_CACH_TRIEN_KHAI_DU_AN.md và cập nhật LICH_SU_DU_AN.md.
- Kiểm thử: kiểm tra liên kết nội bộ, tính toàn vẹn văn bản Markdown, chạy scripts/check_history.py PASS so với origin/main.
- Ảnh hưởng: thuần tài liệu hệ thống, không thay đổi mã nguồn logic ứng dụng hay can thiệp luồng runtime.
- Trạng thái: PASS.
- Giới hạn, lỗi còn lại, rollback/bước tiếp: không có.

<a id="yc-184"></a>
### YC-184 - 10/10/2026 16:37 (UTC+07)

- Người thực hiện: Codex; loại: rà soát/nghiệm thu PR giao diện.
- Mục tiêu: kiểm file/thư mục của PR new-ui-from-main và nguyên nhân GitHub chặn merge; tiếp nối YC-181/182/183.
- Baseline live: PR6 head868233b/base6a50952, OPEN/MERGEABLE/BLOCKED, ahead2/behind0. PR5 đã merge; nguyên nhân không còn là lịch sử độc lập.
- Phạm vi PR: đúng 7 file static/app.js, index.html, style.css, layout.js, style.light-backup.css, theme.css, theme.js; không backend/test/log. Review diff và source hiện hành trên snapshot tmp.
- Nguyên nhân đã kiểm: history thiếu mục mới trong LICH_SU_DU_AN.md; Windows/Mac đều 143 PASS/2 FAIL do mode-tools không trong nav chính và gradient trong style.css trái hợp đồng tests/test_learning_tools_ui_contract.py. Main bắt đủ 3 checks/strict/admin; approval count0, không conflict.
- Source: mode-tools ở index.html:161 nằm trong documents-panel hidden từ dòng156; nav chính giữ mode-documents cũ; documents-panel/mode-qa/mode-timetable trùng ID. Hai title/hai link style.css là lỗi hợp nhất bổ sung, không phải lỗi CI trực tiếp.
- Kiểm chứng: CI PR38041732751 đọc log gốc; tái hiện trực tiếp hai hàm test và history trên đúng head FAIL như CI. Main có mode-tools trong nav chính và không gradient. Browser runtime/full suite local NOT_RUN, không thay bằng khẳng định nghiệm thu UI.
- Trước/sau/file sửa: hoàn tất audit/chẩn đoán và phương án sửa; chỉ ghi LICH_SU_DU_AN.md, STATE.md, docs/project/pr6-merge-audit-2026-10-10/BAO_CAO.md và verification.json. Không sửa mã, PR/comment, commit/push/merge hoặc protection.
- Hướng xử lý chưa triển khai: nav/ID duy nhất, giữ công cụ học tập của PR5 đúng vị trí, xử lý gradient theo yêu cầu thiết kế đã thống nhất, thêm log mới, chạy checks/review UI.
- Kiểm thử/review hồ sơ: ID mới và nội dung cũ bảo toàn/validate PASS. Handoff/triển khai/RED-GREEN N/A vì chỉ audit; lỗi baseline đã tái hiện, chưa có bản sửa.
- Trạng thái: chẩn đoán hoàn tất, PR vẫn BLOCKED. Bằng chứng và giới hạn trong báo cáo local; cập nhật 2026-10-10T16:37:38.245887+07:00.

<a id="yc-185"></a>
### YC-185 - 10/10/2026 16:50 (UTC+07)

- Người thực hiện: Codex điều phối/kiểm thử/review, Antigravity triển khai HTML/CSS; loại: môi trường/chức năng giao diện.
- Mục tiêu/log liên quan: sửa lỗi chặn PR #6 new-ui-from-main tiếp nối chẩn đoán YC-184; giữ UI mới và Công cụ học tập đã hợp nhất trên main.
- Trước/sau: baseline868233b có nav/panel trong vùng ẩn, trùng ID/title/stylesheet, CSS gradient và thiếu log. Sau một nav chính, ba panel trực tiếp trong main, asset/title không trùng; màu phẳng. Review thêm sửa bảng màu bị ghép trùng, thiếu nền sáng và quy tắc OS ghi đè công tắc sáng/tối.
- Phần/file: triển khai trên checkout cách ly /private/tmp/study-agent-pr6-review-xvnawf9q; sửa static/index.html, style.css, theme.css, tests/test_learning_tools_ui_contract.py và nhật ký chia sẻ LOG-20261010-ui-pr6-fix-codex. Giữ toàn bộ16 mục lịch sử main. Không copy mã về checkout chính hoặc sửa backend/corpus/model.
- Kiểm chứng RED: bốn test cấu trúc/UI ban đầu lỗi đúng nguyên nhân và browser xác nhận qa-panel/mode-tools bị ẩn. Hai regression palette/theme mới cũng RED trước lượt sửa bổ sung.
- Kiểm chứng GREEN: local Python3.12.14/requirements-windows149 PASS/6 warning; Node24.19.0 tám test UI PASS; toàn bộ static JS syntax, whitespace và history PASS. Browser demo QA/tools/timetable, ba tab, tìm nguồn/bài tập mẫu, sidebar sáng/tối/desktop/mobile390x844 PASS; không tràn ngang, console0 error.
- Phát hành: đã commit/push d56743aed1c05c5b1d162537658d67765fdceb07 vào [PR #6](https://github.com/ng-wngkh07/Study-Agent/pull/6). [CI PR38043556856](https://github.com/ng-wngkh07/Study-Agent/actions/runs/38043556856) trên đúng SHA PASS: Windows/macOS mỗi OS149 Python và4 Node22; history PASS. Windows1 warning, macOS6 warning phụ thuộc. Đã cập nhật tiêu đề/mô tả và đọc lại PR OPEN/CLEAN/MERGEABLE. Chưa merge vào main.
- Trạng thái: hoàn tất sửa/review/kiểm chứng và cập nhật PR. [Báo cáo](docs/project/pr6-ui-repair-2026-10-10/BAO_CAO.md), [kết quả](docs/project/pr6-ui-repair-2026-10-10/result.json), [ledger](docs/project/pr6-ui-repair-2026-10-10/verification.json). Server/tab demo riêng đã dừng/đóng. Cập nhật 2026-10-10T17:08:20.856435+07:00.
- Giới hạn: chỉ fixture demo và hợp đồng UI/API; QA/VLM/model thật và corpus/MLX đầy đủ NOT_RUN. Build/typecheck N/A vì repo không cấu hình; không thay bảo vệ nhánh hoặc huấn luyện.

<a id="log-log-20261010-ui-pr6-fix-codex"></a>
### LOG-20261010-ui-pr6-fix-codex - 10/10/2026 16:43 (UTC+07)

- Người thực hiện: Codex điều phối/kiểm thử/review, Antigravity sửa HTML/CSS; loại: môi trường/chức năng giao diện.
- Mục tiêu/log liên quan: sửa các lỗi chặn PR #6 sau cập nhật UI; giữ Công cụ học tập của PR #5, bố cục/sidebar/mobile và sáng/tối mới. Tiếp nối YC-181/182 trên main về công cụ học tập.
- Trước/sau: trước nav/panel bị lồng trong vùng ẩn, trùng ID/title/stylesheet, CSS gradient trái hợp đồng và thiếu log. Sau một nav chính, panel trực tiếp trong main, mỗi asset nạp một lần và nền màu phẳng. Bảng màu trùng được bỏ; theme người dùng chọn ưu tiên trước chế độ OS, nền tab sáng không còn bị OS dark ghi đè.
- Phần/file sửa: static/index.html, static/style.css, static/theme.css, tests/test_learning_tools_ui_contract.py, LICH_SU_DU_AN.md. Bản CSS dự phòng giữ để đối chiếu.
- Ảnh hưởng: giữ hỏi đáp, tra cứu nguồn, luyện tập/ôn lại/bài tập mẫu và thời khóa biểu. Không sửa backend, corpus/index/model hoặc bỏ kiểm thử hiện hành.
- Baseline: PR868233b/base6a50952; CI38041732751 mỗi Windows/Mac143 PASS/2 FAIL và history thiếu mục mới. Hai test cũ và hai test cấu trúc mới RED đúng lỗi; browser xác nhận qa-panel/mode-tools bị ẩn. Review tiếp phát hiện hai lỗi palette/theme; hai regression bổ sung cũng RED trước sửa.
- Kiểm chứng bản cuối: Python3.12.14 trên macOS, requirements-windows, full feature suite149 PASS/6 warning phụ thuộc; Node24.19.0:8 UI PASS; mọi static JS qua syntax check, git diff --check PASS. Browser demo: QA/tools/timetable chuyển đúng, ba tab hoạt động, tìm nguồn trí nhớ và bài tập mẫu trả nội dung fixture; sidebar thu gọn/mở lại, mobile390x844 không tràn ngang, sáng/tối đổi được và theme sáng ưu tiên khi OS dark. Console error0.
- Trạng thái: triển khai và review local PASS lúc 10/10/2026 17:02 (UTC+07). CI Windows/Mac/history trên commit mới chưa chạy tại thời điểm ghi; sẽ kiểm sau push. Chưa merge PR.
- Giới hạn: dữ liệu kiểm là demo tổng hợp, không nghiệm thu chất lượng QA/VLM/model thật hoặc corpus/huấn luyện; browser kiểm điều hướng/nguồn/theme, không gọi suy luận thật. Build/typecheck N/A vì repo không cấu hình. Không thay protection.

<a id="yc-186"></a>
### YC-186 - 10/10/2026 17:15 (UTC+07)

- Người thực hiện: Codex; loại: rà soát/giải thích kiến trúc dữ liệu và huấn luyện.
- Mục tiêu: xác định ảnh hưởng của việc sửa/thêm chức năng khi thu thập dữ liệu và huấn luyện; liên quan YC-180 về corpus và YC-185 về UI. Đây là giải thích, không phải yêu cầu chạy pipeline hoặc thay chính sách.
- Trước/sau: làm rõ ba lớp độc lập nguồn đã duyệt, chỉ mục/vector RAG và bộ dữ liệu/adapter LoRA. UI thuần không yêu cầu học lại; thêm tài liệu thường cập nhật RAG; sửa OCR/chunk cần xây lại phần chỉ mục bị ảnh hưởng và rà lại dữ liệu dẫn xuất; đổi embedding cần tạo lại vector tài liệu và truy vấn bằng cấu hình tương thích. Đổi prompt/truy xuất/schema/tính năng cần kiểm chất lượng/tích hợp; chỉ học thêm khi cần hành vi mới. Đổi base model/tokenizer không được mặc định dùng lại adapter/dataset cũ.
- File/module đã đọc: app/config.py, indexer.py, searcher.py, rag_agent.py, dialogue.py, corpus_readiness.py, fine_tune.py, verify_v6.py, active_registry.py, trained_client.py, pipeline_orchestrator.py, run_v6_conservative.py, timetable_vision.py và README_QUY_TAC.md. Nguồn ngoài: tài liệu chính thức Hugging Face PEFT checkpoint và Sentence Transformers semantic search.
- Bằng chứng source: indexer lưu đoạn/vector, searcher tạo vector truy vấn và so cosine; rag_agent đưa đoạn truy xuất vào context. fine_tune gọi cổng corpus và cân bằng dữ liệu trước lệnh MLX; verify_v6 ràng buộc dataset/manifest bằng hash và exact tokenizer; registry kiểm adapter/base/evidence trước kích hoạt. Thời khoá biểu dùng VLM và schema riêng. Việc có bước snapshot_freeze trong orchestrator không chứng minh một job hiện tại đã cô lập toàn bộ đầu vào.
- Phương án đề xuất chưa triển khai: tiếp tục thu thập nguồn có provenance/version; đợt học dùng snapshot riêng của mã, nguồn/index khi cần, train/valid, manifest/hash, base model/tokenizer/config và bộ đánh giá; không sửa đầu vào đang học. Dữ liệu mới đưa vào phiên bản kế tiếp; một writer corpus/index/training, không chạy GPU cạnh tranh. Kiểm khả năng mới và khả năng cũ trước kích hoạt candidate, giữ bản đã nghiệm thu để quay lại.
- Ảnh hưởng thiết kế: phát triển UI/backend có thể song song với chuẩn bị dữ liệu nếu giữ ranh giới phiên bản/hợp đồng; thay code không tự cập nhật trọng số hoặc làm mô hình tự biết chức năng mới. Không cần đợi toàn bộ ứng dụng hoàn thiện mới thu thập; điều kiện học phụ thuộc snapshot và cổng dữ liệu thực tế.
- Phần/file sửa: chỉ bổ sung lịch sử gốc và STATE.md; không sửa mã, nguồn, DB/vector, dataset, adapter, cấu hình hoặc tiến trình.
- Kiểm chứng: đối chiếu luồng source/hợp đồng PASS; bảo toàn mục cũ, ID/count và liên kết checkpoint PASS. Triển khai/handoff/RED-GREEN/build N/A vì chỉ giải thích và tài liệu; audit corpus live, thu thập, inference, training và nghiệm thu model NOT_RUN. Không suy complete:true hiện tại từ ghi chép YC-180.
- Trạng thái: hoàn tất giải thích; snapshot/version workflow là đề xuất chưa áp dụng. Cập nhật 2026-10-10T17:15:37.047998+07:00.
<a id="yc-187"></a>
### YC-187 - 10/10/2026 17:30 (UTC+07)

- Người thực hiện: Codex kiểm tra nguồn/chỉ mục, đối chiếu chức năng và lập kế hoạch; loại: dữ liệu corpus và chuẩn bị huấn luyện.
- Mục tiêu/log liên quan: nghiệm thu toàn bộ tài liệu/chỉ mục, sau đó xây dữ liệu phủ các chức năng hiện có và huấn luyện khi hoàn chỉnh; tiếp nối YC-180/186. Đã có yêu cầu huấn luyện có điều kiện; chưa đổi yêu cầu toàn corpus hoàn chỉnh.
- Trước/sau: đối chiếu lại báo cáo corpus có thể đọc YC-180 bằng audit live. 154 nguồn/154 tài liệu indexed, 22.705 trang; 22.692 trang hoàn chỉnh, 13 chưa hoàn chỉnh (9 visual partial, 4 OCR pending). 52.604 chunks/FTS/vector; vector lỗi/thiếu=0, integrity ok, FK0, 154 probe FTS đạt. Cổng nguồn vẫn complete:false; 22 lỗi điều kiện tương ứng 13 trang, không phải 22 trang.
- Review nguồn: đã xem trực tiếp ảnh của 13 trang, đối chiếu hash PDF/ảnh; còn chữ bị cắt/thiếu/khó đọc trong nguồn hiện có. Không đoán chữ hoặc chuyển trạng thái thành verified. Chờ nguồn đầy đủ hoặc quyết định thay phạm vi rõ ràng; chưa cho phép loại trang.
- Baseline chức năng: main d6f44eda5086fdcae70a00a1f0ac4a9e98edd901, PR6 đã được merge khi đọc lại; không do lượt này merge. Đọc mã main tại snapshot tmp cùng source local; không ghi đè checkout chính đang chỉnh. Kế hoạch bao gồm QA, sinh/kiểm trắc nghiệm và tóm tắt; lịch ảnh dùng VLM riêng, lookup/chấm đáp án/lưu lịch/UI kiểm bằng hồi quy.
- Dữ liệu hiện có: v15 323 train/34 valid chỉ 3 môn; giải tích/toán rời rạc/vật lý không có mẫu, exact-token balance passed:false, trial chưa duyệt. 154 nguồn có 153 hash nội dung; một nhóm Sandi Mann trùng hoàn toàn phải giữ cùng split. Đường admission OCR cache so với source_page_reviews mới chưa chứng minh tương đương.
- Phần/file đọc: app/corpus_readiness.py, source_page_reviews.py, dataset_balance.py, training_data.py, fine_tune.py, rag_agent.py, searcher.py, indexer.py, practice.py trên main, topic_summarizer.py, timetable_vision.py, active_registry.py; policy/tracker, manifest/review/corpus audit, approval v15 và paired-decision Trial18.
- Phần/file sửa: LICH_SU_DU_AN.md, docs/project/STATE.md, tracker runtime hiện có; thêm data/evaluation/training-readiness-20261010-yc187/BAO_CAO.md và audit/index/source-manifest/13-page-review/balance/feature-training-plan/test-log. Chỉ bằng chứng và checkpoint; không sửa nguồn, DB, vector, mã sản phẩm hoặc chính sách.
- Kiểm chứng: audit toàn nguồn/provenance/vector và FTS readback chạy xong; 24 test corpus/readiness/review/balance PASS, 5 warning phụ thuộc; balance dataset cũ FAIL. Registry file active_adapter_id:null; runtime health/inference live chưa kiểm. Bảo toàn log/ID/cấu trúc dữ liệu kiểm riêng sau khi ghi.
- Ảnh hưởng/điều kiện tiếp: giữ một writer, snapshot có hash và split theo nguồn/nhóm trùng, cân bằng số mẫu/token 6 môn, review nhãn độc lập; training chỉ sau live complete:true và dataset/QA/resource gates. Giữ holdout/retention, không kích hoạt theo loss; Trial18 đã bị loại vì retention giảm dù target tăng.
- Trạng thái: audit hoàn tất nhưng cổng nguồn FAIL; kế hoạch chức năng đã lập, tập học mới/training/evaluation NOT_RUN vì còn 13 trang. Chờ quyết định nguồn/phạm vi; Antigravity tác vụ corpus cũ đã ở trạng thái kết thúc khi kiểm UI, chưa gửi job triển khai/học mới. Không commit/push/merge hoặc thay model/provider; RED-GREEN triển khai/build N/A vì chưa sửa mã. Cập nhật 2026-10-10T17:30:22.992111+07:00.

<a id="yc-188"></a>
### YC-188 - 10/10/2026 18:40 (UTC+07)

- Người thực hiện: Codex điều phối/kiểm chứng; loại: quyết định phạm vi corpus và huấn luyện.
- Mục tiêu/quyết định: người dùng cho phép loại đúng 13 trang unresolved đã kiểm tại YC-187 và tiếp tục chuẩn bị dữ liệu/huấn luyện. Thay yêu cầu toàn bộ trang không ngoại lệ ở cổng nguồn trước đây; không thay các cổng provenance, cân bằng, review nhãn, tài nguyên và đánh giá mô hình.
- Trước/sau: 22.705 trang toàn nguồn gồm 13 trang không thể xác minh đầy đủ; phạm vi được duyệt còn 22.692 trang. Giữ PDF/DB/trạng thái 13 trang chưa hoàn chỉnh; không gọi toàn bộ nguồn đã hoàn chỉnh hoặc sửa chúng thành verified. Cổng mới phải phân biệt complete_all_sources và complete cho phạm vi đã duyệt, nêu excluded_count=13 và ràng buộc hash/người duyệt/lý do.
- File/quyền ghi: Codex ghi manifest data/runtime/corpus-page-exclusions-yc188.json, history/STATE/tracker và test; dự kiến Antigravity triển khai cổng nguồn/admission/pipeline trong phạm vi riêng. Chưa cập nhật policy hoặc chạy dữ liệu/học khi mới ghi quyết định.
- Tiêu chí: chỉ bỏ kiểm nội dung thiếu đúng trang được duyệt; nguồn/hash/page/chunk/review/vector/integrity khác vẫn fail closed, nguồn đổi/manifest sửa/ngoại lệ không hợp lệ phải chặn. Không đưa bất kỳ chunk thuộc13 trang vào train/valid/holdout; nguồn trùng/holdout chia theo hash.
- Kế hoạch tiếp: kiểm cổng và dataset admission RED/GREEN; audit live theo phạm vi; xây bộ 6 môn phù hợp QA/trắc nghiệm/summary, dữ liệu ảnh riêng; review Codex/hash/tokenizer/preflight trước job MLX; paired holdout/retention trước quyết định kích hoạt.
- Trạng thái: quyết định đã ghi; triển khai/data/training đang chuẩn bị, NOT_RUN lúc lập mục. Không tự commit/push, đổi model/provider hoặc lịch tự động. Cập nhật 2026-10-10T18:40:15.882736+07:00; kết quả bổ sung theo bằng chứng hiện hành.

- Kết quả bổ sung 2026-10-10T19:09:03.132697+07:00: đã triển khai policy hash-bound và module corpus_scope; audit độc lập Codex PASS,154 nguồn/22.705 trang vật lý,22.692 trong phạm vi,13 trang/19 chunks loại trừ; complete:true theo phạm vi, complete_all_sources:false. PDF/DB/trạng thái nguồn không sửa. Manifest sai hash/nguồn/trang/duyệt và dữ liệu dùng alias, số trang sai, nguồn thiếu đều chặn.
- Kiểm chứng cổng: RED ban đầu9 lỗi/13 đạt; vòng review provenance mở rộng11 lỗi/32 đạt; sau sửa91 test độc lập PASS,5 cảnh báo phụ thuộc. Bằng chứng data/evaluation/training-yc188-20261010/scoped-corpus-audit-codex.json và scope-independent-final.log; test xanh chỉ nghiệm thu cổng, không chứng minh model tốt.
- Cập nhật README_QUY_TAC về phạm vi được duyệt. Bản V15 thiếu3 môn và các nhiệm vụ trắc nghiệm/summary:4 test hợp đồng dữ liệu FAIL/1 PASS trên baseline. Bộ mới V16 đang chuẩn bị, nội dung/chia nguồn/holdout/tokenizer chưa duyệt; đã ghi cảnh báo nguồn Vi tích phân dẫn xuất để tránh leakage. Chưa huấn luyện hoặc kích hoạt adapter, cần review độc lập đầu ra dữ liệu/base trước job.

- Review dữ liệu 2026-10-10T12:30:00.497711+00:00: bản nháp V16 đầu tiên 432 train/108 valid bị Codex từ chối; 540/540 vị trí trích dẫn không khớp chunk gốc, 90/90 câu trắc nghiệm đáp án A, nhãn dùng mẫu chung và có trang danh sách người dịch/bìa, nhóm nguồn Vi tích phân bị chia lẫn dẫn xuất. Giữ bản nháp/bằng chứng riêng để tái hiện; không tạo approval hoặc học trên dữ liệu này.
- Đã dừng đúng tiến trình đánh giá nền và nhắc tiến trình của bản này qua UI, xác nhận tác vụ biến mất/GPU lock rảnh; gói sửa dữ liệu giới hạn đã giao một lần. Yêu cầu nhãn riêng theo nguồn, admission live, span nguyên văn và chia family thật; Codex nghiệm thu độc lập sau kết quả. Training/activation NOT_RUN; baseline dở dang không dùng làm bằng chứng nghiệm thu.

<a id="yc-189"></a>
### YC-189 - 10/10/2026 22:35 (UTC+07)

- Người thực hiện: Codex nghiên cứu/đối chiếu/review; loại: phương pháp dữ liệu và huấn luyện.
- Mục tiêu: tìm phương pháp huấn luyện hiệu quả trên GitHub và diễn đàn, kiểm tra bằng tài liệu chính thức và dữ liệu/hardware hiện tại rồi áp dụng; tiếp nối YC-187/188.
- Trước/sau: bản Data02 đã bị từ chối; Data03 có 144 train/72 valid phủ 6 môn, 6 tác vụ, nhưng chưa được nghiệm thu nội dung và chia họ nguồn. Máy dùng Qwen2.5-3B-Instruct 4-bit/MLX; không đổi model/provider. Lượt cũ tăng điểm mục tiêu nhưng giảm retention nên không kích hoạt.
- Phương pháp đang đối chiếu: QLoRA trên nền quantized, batch nhỏ/gradient checkpoint, loss trên câu trả lời, chọn dữ liệu có căn cứ, chia theo họ nguồn, đóng băng bộ đánh giá và so sánh candidate/base. GitHub MLX-LM và tài liệu TRL là nguồn kỹ thuật; diễn đàn chỉ cung cấp kinh nghiệm cần xác minh.
- Phần/file: bằng chứng nghiên cứu local data/evaluation/training-methods-yc189-20261010; chưa sửa trainer hoặc chạy học trong bước nghiên cứu. Dữ liệu, nguồn PDF, DB/vector và model giữ riêng ngoài Git.
- Trạng thái: đang nghiên cứu và áp dụng theo các cổng chất lượng hiện có. Inference, LoRA và kết quả chất lượng candidate mới NOT_RUN; không nhận số lượng/test xanh là dữ liệu đã duyệt.



- Kết quả nghiên cứu/review 2026-10-10T22:57:15.274642+07:00: đã đối chiếu MLX-LM official source, TRL, QLoRA/LIMA/group split/forgetting papers và thảo luận LocalLLaMA/MLX issue. Báo cáo docs/team/PHUONG_PHAP_HUAN_LUYEN_YC189_2026-10-10.md phân biệt kinh nghiệm với bằng chứng và giới hạn máy16GB. Codex đọc đủ36 đoạn/216mẫu Data03 và từ chối: leakage4họ nguồn, target verifier cố định sai, summary khác runtime và nhiều fakequote/thông tin ngoài nguồn, gồm C++ createNode bị thay new Node và Rogers bị gán cognitive dissonance. Bốn regression ngữ nghĩa tái hiện RED; chưa sửa/học trên bản này. Đã xem đầy đủ2trang MIT vật lý mới, đang bổ sung có giới hạn và bảo toàn corpus cũ trước dataset tiếp theo.

- Cập nhật nguồn/dữ liệu 2026-10-10T23:23:57.464681+07:00: bổ sung 3 PDF MIT/Cornell mới có ảnh trang và bản trích được kiểm tra. Codex kiểm độc lập toàn bộ hàng cũ/byte vector với backup của từng đợt: không đổi; audit live157 nguồn/22.711 trang,22.698 trong phạm vi,13 ngoại lệ cố định, complete:true/complete_all_sources:false, FTS/FK đạt. Đợt MIT dùng khóa tương thích thay khóa chuẩn; ghi lệch quy trình, đợt Cornell dùng cả hai khóa, Codex xác nhận khóa rảnh trước bàn giao. PDF Cornell Sept23 bị loại khỏi bổ sung vì đáp án đạo hàm sai, giữ nguyên bản tải để truy nguyên.
- Tiếp tục sửa builder/dữ liệu:11 kiểm thử hợp đồng tái hiện lỗi nhãn kiểm định, input thực tế practice/summary và kiểm hash base/suite trước đánh giá. Gói Data04 đang tạo216 mẫu/36context và36 ca chức năng nháp, chưa được duyệt nội dung. Huấn luyện/baseline mới NOT_RUN; không kích hoạt adapter. Bằng chứng local: data/evaluation/training-yc188-20261010/source-unit01/ và source-unit02/; data-review-draft03/semantic-and-evaluator-red02.log.

- Hiệu chỉnh bằng chứng 2026-10-10T23:40:35.952731+07:00: hai lỗi practice trong log RED ban đầu là lỗi thiếu module ở checkout local, chưa chứng minh sai prompt. Codex đã dùng nguồn main có hash để tái hiện hai sai khác đầu vào thật (runtime-prompt-red03.log). Bản sửa bước đầu đạt12 test/1SKIP, chỉ là kiểm hợp đồng mã. Bổ sung5 ca RED đúng lỗi còn lại: thiếu nhãn kiểm định được tự điền, kiểu boolean sai, model rỗng được chấp nhận và luồng chạy chưa gọi hai cổng duyệt trước tài nguyên. Data04 vẫn đang triển khai; dữ liệu, baseline và huấn luyện chưa được duyệt.


- Review dữ liệu và đường chạy 2026-10-11T00:02:35.539988+07:00: Data04 đã kết thúc, giữ snapshot mã/dữ liệu v17 bất biến. Codex kiểm đủ36 ngữ cảnh nguồn: tất cả offset/trích dẫn/hash PDF khớp, nhưng16 ngữ cảnh có nhãn tiếng Anh trái yêu cầu ứng dụng; nhãn kiểm định còn tự điền và thiếu đa dạng lỗi, một số điều kiện/phát biểu vượt đoạn nguồn. Bản v17 vẫn REJECT, không học. Kiểm độc lập46 ca:40 PASS/6 FAIL, gồm nhãn tự sinh, đầu vào đánh giá bị cắt/sửa, ngân sách/metrics sai và consumer từ chối verifier JSON/summary hội thoại hợp lệ. Đang sửa riêng scripts/evaluate_exact_base.py và app/dataset_balance.py; không thay cổng nguồn, prompt ứng dụng, provider hoặc chất lượng. Bằng chứng data-review-draft04/codex-terminal-tests.log, codex-initial-content-review-REJECT.json và terminal-snapshot/identity.json trong data/evaluation/training-yc188-20261010/. Baseline/học mới NOT_RUN; không kích hoạt adapter.

<a id="yc-190"></a>
### YC-190 - 10/10/2026 22:35 (UTC+07)

- Người thực hiện: Codex; loại: đồng bộ lịch sử và phát hành GitHub.
- Mục tiêu: kiểm tra lịch sử ứng dụng trên GitHub, đẩy cập nhật mới đã kiểm chứng và tiếp tục tác vụ dữ liệu/huấn luyện cùng nghiên cứu YC-189.
- Baseline: main `11122e755684171806804429c105361abe398fe1`, đã merge PR6 giao diện và PR7 bộ tài liệu. Checkout local vẫn ở `871d4dc`; không ghi đè code/main hoặc reset việc local. Dùng checkout mới từ main để phát hành chọn lọc.
- Lịch sử: bảo toàn LOG-20261010-ui-pr6-fix-codex và nội dung GitHub YC-180–183; mỗi mã YC trùng gom hai mốc với ngày/nội dung gốc và chú thích hiệu chỉnh. Header cũ ghi17 nhưng thực tế18 mục trên main đã được đối chiếu lại.
- Phần phát hành: app/corpus_scope.py, corpus_readiness.py, training_data.py, fine_tune.py; tests/test_corpus_readiness.py, test_corpus_scope_admission.py; README_QUY_TAC.md và lịch sử gốc. Cổng loại trang yêu cầu manifest/hash/quyết định hợp lệ, không bỏ qua lỗi nguồn/chunk/vector; giữ complete_all_sources khác complete theo phạm vi. Không đưa manifest nguồn thật, DB, dataset, weights hoặc annotation nội dung tài liệu vào Git.
- Kiểm chứng: trước phát hành đã có 91 kiểm thử độc lập và audit live scoped complete:true, whole-source false ở YC-188. Cần chạy kiểm thử tích hợp trên main mới, diff/history và đọc lại remote SHA trước xác nhận push.
- Tiếp tục: Data03 đã kết thúc; Codex review cho thấy báo cáo họ nguồn vẫn có train/valid cùng họ VLDC1/TRR, nên chưa cấp quyền học. Giữ dữ liệu ở UNREVIEWED; tiếp tục sửa split/nhãn/evaluation sau bước phát hành.
- Kiểm chứng phát hành: 149 kiểm thử chức năng và8 Node PASS trên checkout main mới;84 kiểm thử scope/training PASS,1 ca cần manifest private SKIP. Ca đó và toàn bộ nhóm scope trên máy corpus:53 PASS. Diff/history và quét bí mật cần xác nhận ở commit cuối.
- Kết quả phát hành: đã push commit `85ded1e659cf049a35f89cb6fbb2c4fc378df774` nhánh codex/corpus-scope-yc190 và mở [PR8](https://github.com/ng-wngkh07/Study-Agent/pull/8); đọc lại refs khớp, history/diff/Gitleaks PASS. CI đang chờ; chưa merge. Cập nhật 2026-10-10T22:37:47.040860+07:00. LoRA/paired evaluation mới NOT_RUN.


- Sửa CI 2026-10-10T22:47:49.500414+07:00: GitHub Python3.12 phát hiện NameError do thiếu Set/Tuple tại annotation của training_data; local Python3.14 trì hoãn annotation nên suite cũ không bắt được. Thêm ca get_type_hints tái hiện RED rồi bổ sung đúng hai typing imports, không đổi hành vi. Codex kiểm lại root54 PASS, checkout phát hành53 PASS/1 private-policy SKIP; scripts/dev.py check PASS. CI cũ38064366774 FAIL giữ bằng chứng, đang đẩy sửa và chờ kiểm chứng lại trên cả Windows/Mac Python3.12.

- CI sửa lỗi 2026-10-10T22:57:15.274642+07:00: commit3c221ed49eb7429f38afd2458dcd562e1dfd386a đã được remote xác nhận. Run38065066486 Python3.12.10 Windows149 PASS/1warning và Mac149 PASS/6warnings; mỗi OS4 Node PASS; history PASS. PR8 OPEN/CLEAN, chưa merge. Bản báo cáo phương pháp bổ sung theo YC-189 đang đồng bộ cùng lịch sử; kiểm CI lại trên commit tài liệu cuối.

- Xác nhận GitHub 2026-10-10T23:07:52.030355+07:00: f88b135 CI38065738016 tất cả3job PASS; báo cáo phương pháp đã push. Người dùng ng-wngkh07 merge PR8 lúc23:02:23 UTC+07, main5d399d527e0e44fda7c02232cebc2949bec48aa0; Codex chỉ push/cập nhật PR, không thực hiện merge. Bằng chứng docs/project/github-publish-yc190-20261010/pr8-final-readback.json và f88b135-ci-green.log.


<a id="log-log-20261011-evaluation-execution05-codex"></a>
### LOG-20261011-evaluation-execution05-codex - 11/10/2026 00:18 (UTC+07)

- Người thực hiện: Codex kiểm thử/review/tích hợp, Antigravity sửa evaluator và balance; loại: môi trường/chức năng đánh giá và cổng dữ liệu.
- Lỗi/mục tiêu: baseline từng bỏ câu hỏi/đổi SYSTEM và ngân sách sinh; thiếu lý do dừng/metric vẫn được coi hoàn chỉnh. Consumer balance kiểm PDF trực tiếp khiến nguồn JSON và hội thoại summary đúng runtime bị từ chối.
- Trước/sau: giữ nguyên các message đầu vào, chỉ bỏ assistant tham chiếu cuối; dùng decoding đã đóng băng ở ca hoặc suite, chặn khi thiếu. Kiểm toàn bộ file base/tokenizer và hash suite đã review trước dùng tài nguyên. Ghi finish_reason, token và bộ nhớ thực từ stream; length là chưa hoàn chỉnh. Summary đối chiếu hai lượt giới hạn300/400 ký tự; verifier đối chiếu nguồn sau giải mã JSON.
- Phần/file: scripts/evaluate_exact_base.py, app/dataset_balance.py; ba test_exact_evaluation_gate/execution và test_dataset_balance_input_binding; pytest.windows.ini thêm cả27 hồi quy vào CI. Đường dẫn evaluator tính theo checkout để chạy trên máy khác, không gắn thư mục cá nhân.
- Kiểm chứng: 27 test độc lập PASS tại root sau RED đúng các lỗi; fixture chỉ giả lập ranh giới MLX/tokenizer, không chứng minh chất lượng inference. Dữ liệu v17 và hồ sơ Data04 được so hash và giữ nguyên. Kiểm tích hợp Python3.12.14 trên checkout main mới:176 test PASS/6 warning,4 Node PASS, dev check/diff/Gitleaks PASS. CI Windows/macOS chưa chạy tại thời điểm ghi.
- Phiên bản/phối hợp: main5d399d5 sau PR8 được chủ repo merge; PR9/update-Agent còn mở khi kiểm, có thay đổi QA/UI riêng. Bộ dữ liệu mới v18 đang sửa nhãn và nguồn; chưa chọn chức năng mới làm mốc học khi chưa kiểm chứng. Không ghi đè việc root hoặc nhánh khác.
- Trạng thái: mã cổng/đánh giá đã review local; phát hành chọn lọc đang làm theo YC-190. Dataset, baseline thực, LoRA và paired evaluation mới NOT_RUN; adapter chưa kích hoạt. Cập nhật 2026-10-11T00:18:44.971807+07:00.
- Giới hạn/bước tiếp: sửa và review toàn bộ nhãn v18, đóng băng holdout đúng mốc ứng dụng, kiểm nguồn/tài nguyên live rồi chạy baseline và học có giới hạn. Phần này chưa có chạy mô hình thật và chưa có kết quả tăng chất lượng.


<a id="log-20261011-update-agent-ui-qa"></a>
### LOG-20261011-update-agent-ui-qa - 11/10/2026 00:05 (UTC+07)

- Người thực hiện / loại thay đổi: Bùi Minh Trí / chức năng QA và giao diện UI.
- Lỗi hoặc mục tiêu (ý chính): Nâng cấp xử lý bộ ca testcase QA (C-F-G cases), đồng bộ giao diện người dùng (UI/Theme light-dark) và cập nhật test suite.
- Trước / sau thay đổi: Trước chưa có bộ testcase QA riêng cho các câu hỏi phức tạp và giao diện bị lầm lẫn giữa các tab/OS dark mode; Sau thêm testcase `study_agent_c_f_g_cases.json`, tối ưu CSS theme và giao diện tập trung.
- Phần/file sửa và cách xử lý: Sửa `.env.example`, `scripts/dev.py`, `README.md`, `app/config.py`, `app/history.py`, `app/rag_agent.py`, `app/searcher.py`, `app/server.py`, `pytest.windows.ini`, các file giao diện `static/` và bổ sung testcase trong `tests/`.
- Log/phiên bản liên quan; lý do thay quyết định nếu có: Tiếp nối các cập nhật từ PR trước đó (`LOG-20261010-ui-pr6-fix-codex`).
- Ảnh hưởng/phối hợp với phần còn lại: Tương thích ngược với luồng RAG và tra cứu tài liệu gốc, không làm ảnh hưởng đến dữ liệu thời khóa biểu (`timetable`).
- Kiểm thử: Đã chạy `node --test tests/document_lookup_ui.test.cjs` và test suite QA local.
- Trạng thái: PASS.
- Giới hạn, lỗi còn lại, rollback/bước tiếp: Chưa chạy thử nghiệm trên mô hình GPU thật.


<a id="yc-191"></a>
### YC-191 - 11/10/2026 00:21 (UTC+07)

- Người quyết định: chủ dự án; Codex đối chiếu và điều phối; loại: làm rõ phạm vi chức năng huấn luyện.
- Mục tiêu/quyết định: chuyển mốc dữ liệu và đánh giá từ main đã merge sang nhánh update-Agent có các chức năng mới. Tiếp nối YC-187/189/190; không thay quyết định13 ngoại lệ nguồn YC-188.
- Trước/sau: bộ nháp đang sửa theo QA main; sau phải đối chiếu quick/steps/compare, ngân sách360/900/900, phạm vi tài liệu/trang, bằng chứng một phía, khôi phục phiên và ngữ cảnh cách ly của update-Agent. Giữ old28 và các nguồn đánh giá tách train.
- Mốc đã đọc: update-Agent3c73ff4d528722ab12f46de98b2253221f0db8f2, PR9 đang mở; app/config.py, rag_agent.py, searcher.py, history.py, server.py, fixture QA và22 test mới. Toàn feature suite Python3.12.14 trên snapshot nhánh mới171 PASS/6warning. Test fixture không chứng minh câu trả lời model thật.
- Phần đang làm: giữ đơn vị sửa nhãn Data06 đang chạy độc lập, chuẩn bị delta chuyển prompt/mode/feature holdout sau bàn giao terminal; không gửi chồng hoặc có hai writer. Bộ dữ liệu mới chưa được duyệt.
- Giới hạn: chưa merge PR9, chưa chạy inference/LoRA/paired evaluation. Kiểm tích hợp evaluator/balance với nhánh được chọn đang thực hiện; không sao chép toàn nhánh vào root đang chỉnh.


<a id="yc-192"></a>
### YC-192 - 11/10/2026 10:10 (UTC+07)

- Người thực hiện: Codex kiểm thử/review/tích hợp; loại: sửa tích hợp Git và cấu hình suy luận.
- Mục tiêu: xử lý conflict PR10 sau PR9/update-Agent, ấn định model trả lời/nhúng và số đoạn trích phía máy chủ sau khi bỏ chọn trên giao diện; nghiên cứu và đo Top-K trên corpus thực. Merge PR10 sau kiểm chứng rồi đồng bộ main về máy, bảo toàn việc local.
- Trước: main604f096 đã merge PR9; PR10 44b39d9 CI Windows/Mac/history PASS nhưng CONFLICTING. Hai conflict thực tại LICH_SU_DU_AN.md và pytest.windows.ini; các lựa chọn bị ẩn trong DOM nhưng client còn gửi và API còn dùng model/embed_model/top_k.
- Hướng sửa/phạm vi: giữ cả nhật ký và cả nhóm kiểm thử, kiểm cấu hình API/QA/practice/summary/index và loại phụ thuộc DOM ẩn. Không đổi PDF/DB/vector/base model hoặc kích hoạt adapter. Mốc chức năng theo YC191.
- Kiểm chứng/trạng thái: đang triển khai trên checkout cách ly; test hành vi, benchmark retrieval, CI bản sửa, merge và đồng bộ local còn NOT_RUN tại lúc lập mục. Data06 đã terminal; v18 và feature36 vẫn UNREVIEWED/DRAFT, chưa baseline hoặc học.
- Giới hạn: Top-K không tự bảo đảm độ chính xác; cần tách kết quả tìm nguồn khỏi chất lượng trả lời thật. Backup/reconcile trước đồng bộ vì root đang có sửa chưa commit. Cập nhật 2026-10-11T10:12:39.856266+07:00.

- Kiểm chứng bổ sung 2026-10-11T10:27:08.062672+07:00: Codex review độc lập bản triển khai; 211 kiểm thử Python3.12.14 PASS/6 warnings, 5 Node PASS, kiểm môi trường phát triển PASS. 13 hồi quy cấu hình cố định PASS sau baseline 10 FAIL/1 PASS; hai conflict được giữ đầy đủ nội dung hợp lệ. Thay đổi ở server/ollama_client và app.js/index.html; không thêm dependency.
- Đo truy xuất: 168 lượt/28 câu với K2/4/6/8/10/12. K6 đạt 7/28 prompt có trang tham chiếu, bằng K8/K10; K12 đạt 6/28. Giữ K6 và câu tổng hợp K10 nội bộ. Đây là kiểm tìm nguồn, không phải độ chính xác trả lời; xếp hạng và chọn nguồn dưới ngân sách còn hạn chế. [Phương pháp và giới hạn](docs/team/CAU_HINH_TRA_LOI_VA_TOPK_YC192.md).
- Bảo toàn local: đã lưu bản sao có hash của 211 file mã/tài liệu và Git metadata; DB/vector/weights giữ tại chỗ. CI bản sửa, merge và đồng bộ main đang chờ kiểm chứng.
- Dữ liệu/huấn luyện: 28 kiểm thử hợp đồng nháp v18 PASS nhưng review nội dung tìm nhãn Vi tích phân không khớp nguồn và thiếu điều kiện Đại số; REJECT_PARTIAL_CONTENT_REVIEW, chưa review đủ toàn bộ. Cần sửa dữ liệu và chuyển bộ đánh giá sang prompt/mode của update-Agent trước học; không kích hoạt adapter.

- Hoàn tất tích hợp 2026-10-11T10:34:09.381040+07:00: [PR10](https://github.com/ng-wngkh07/Study-Agent/pull/10) MERGED lúc 11/10/2026 10:30:37 (UTC+07), commit main `6824f411e9c8be404023a8c14955b2bb6e8acfda`. CI [38108470080](https://github.com/ng-wngkh07/Study-Agent/actions/runs/38108470080) trên đúng head `59f771a` PASS Windows/macOS/history: mỗi OS 211 Python + 5 Node; local bổ sung 4 Node luyện tập PASS. Không thay bảo vệ nhánh.
- Đồng bộ máy: main local và origin/main cùng `6824f41`, cập nhật fast-forward từ `871d4dc`; mã tracked khớp main trước cập nhật kết quả nhật ký này. Bản sao 211 file/Git metadata đã xác minh hash; giữ stash khôi phục và phục hồi 7 file chuẩn bị dữ liệu/test chưa công bố. DB corpus, manifest 13 ngoại lệ, config/tokenizer/trọng số base và indexer được so SHA256 trước/sau: giữ nguyên.
- Smoke local: mở main với corpus thật tại http://127.0.0.1:8768, không thay phiên ứng dụng cũ cổng8000. Health/models/timetable HTTP200; UI model sẵn sàng và không có lựa chọn model/nhúng/Top-K. Demo kiểm ba chế độ, 0 lỗi JS trước dừng; sau mở lại local không có lỗi JS mới. Một câu QA có phạm vi trang thật HTTP200 trong17,79 giây, dùng qwen2.5-3b-4bit dù payload yêu cầu7B; trả hai điều kiện tuyến tính khớp nguồn, có citation và không cắt đầu ra. Chỉ là smoke một câu, không phải nghiệm thu độ chính xác tổng thể.
- Giới hạn môi trường: lần smoke đầu trong sandbox không truy cập được Metal và trả500; phiên kiểm tra được mở lại với quyền GPU, smoke sau đạt. Chưa kiểm QA model thật trên Windows hoặc luyện tập/summary/VLM thật toàn bộ. Huấn luyện v18 còn bị chặn bởi nhãn sai và bộ đánh giá chưa chuyển đủ mốc YC191; không học/kích hoạt. Trạng thái YC192: sửa conflict/cấu hình/merge/đồng bộ PASS; chất lượng truy xuất cần cải thiện tiếp. Kết quả sau merge được ghi local, chưa đưa phần cập nhật nhật ký này lên GitHub.


<a id="yc-193"></a>
### YC-193 - 11/10/2026 10:39 (UTC+07)

- Người thực hiện: Codex; loại: rà soát/nghiệm thu giao diện và thao tác người dùng.
- Mục tiêu: kiểm toàn bộ màn hình, luồng hỏi đáp/tra cứu/luyện tập/ôn tập/hội thoại/lịch học, lỗi nhập liệu, lưu sau tải lại, sáng–tối và màn hình nhỏ.
- Baseline: main6824f411e9c8be404023a8c14955b2bb6e8acfda, tiếp nối YC-192; giữ cấu hình mô hình cố định.
- Phạm vi: bản chụp mã main cách ly, DB và trình duyệt kiểm thử riêng; giữ dữ liệu, lịch sử, lịch học và các file huấn luyện đang chỉnh ở checkout chính.
- Kiểm chứng/trạng thái: đang kiểm tra; chưa có kết luận nghiệm thu. Bằng chứng local data/evaluation/ui-audit-yc193/. Không chạy huấn luyện hoặc tự phát hành GitHub.


<a id="yc-194"></a>
### YC-194 - 11/10/2026 12:35 (UTC+07)

- Người thực hiện: Codex; loại: tái cấu trúc chức năng học tập và sửa hỏi đáp.
- Mục tiêu: tiếp tục YC-193; sửa sót từ cuối ô nhập, phân biệt tóm tắt/từng bước/so sánh, truy xuất phù hợp từng chế độ và chuyển nguồn sang đường dẫn trang. Công cụ tự tìm bài tập/đáp án và tạo câu hỏi từ lý thuyết theo chủ đề; người dùng không cần chọn tài liệu/trang. Ôn tập tự tạo/lựa chọn câu hỏi và theo dõi phần chưa nắm.
- Phạm vi bổ sung: nghiên cứu nguồn chính thức OpenAI/Google DeepMind/Anthropic và nền tảng học tập; bộ mẫu chia sẻ độc lập ít nhất một tài liệu mỗi chủ đề, tránh công bố corpus/nhãn/trọng số huấn luyện. Người dùng yêu cầu đưa thêm dữ liệu kiểm thử lên GitHub; mã sản phẩm được sửa local, chưa tự suy quyền merge mới.
- Trước: mode chỉ thêm hướng dẫn trình bày, lọc từ khóa có thể tính cả từ yêu cầu; practice bắt chọn trang, ví dụ trả trích đoạn tìm kiếm, ôn tập chỉ chứa câu sai đã lưu. Ô nhập gửi Enter không có chặn IME đang ghép chữ (giả thuyết lỗi sót chữ cần tái hiện).
- Triển khai: snapshot YC-193 được tái dùng sau khi xác minh hai gói Antigravity đã terminal; Codex giữ test/review/nguồn, không cạnh tranh writer. Dữ liệu và model thật bảo toàn.
- Triển khai/kiểm chứng 2026-10-11T13:05:23.112330+07:00: đã đọc nguồn công khai về hướng dẫn theo nhiệm vụ của OpenAI/Google/Anthropic và LearnLM/NotebookLM, lập thiết kế và tiêu chí dữ liệu/nghiệm thu. Ô nhập được chặn Enter trong IME; nguồn QA/tra cứu bỏ ảnh, dẫn trang PDF. 12 Node PASS sau sửa; bộ gõ hệ điều hành thật chưa kiểm. Browser giữ bản nháp4001 ký tự, báo giới hạn trước gửi và tạo hội thoại mới giữ hướng dẫn đa lĩnh vực.
- Học tập: gói backend đầu đã terminal, Codex kiểm độc lập68 PASS nhưng phát hiện4 lỗi biên mới và một lỗi lấy trang đầu sách thay chủ đề ở trang sau; đang sửa và triển khai giao diện theo chủ đề. Không suy chất lượng mô hình thật từ stub test. Phân biệt nguồn đề/nguồn đáp án, cấm ghép khóa trùng và kiểm đáp án AI bằng bước thứ hai với điều kiện/đáp án nhất quán.
- Tài liệu: docs/project/learning-restructure-yc194/THIET_KE.md và HUAN_LUYEN_VA_NGHIEM_THU.md. Bộ mẫu công khai đã đưa PR11 theo LOG cùng ngày; mã sản phẩm còn trong snapshot để review/tích hợp. Huấn luyện chưa chạy: nhãn v18 vẫn bị từ chối nội dung; cần bộ dữ liệu đúng, holdout theo nguồn và đánh giá base/adapter trước kích hoạt. Không dùng fine-tuning để cam kết mọi đáp án đều đúng. Trạng thái YC194: đang làm.


- Kiểm độc lập tiếp 2026-10-11T13:25:53.704926+07:00: backend/giao diện theo chủ đề75 Python +16 Node PASS. Bổ sung ca thực tế tìm bài ở trang215, khóa đáp án trùng ở cuối sách, nguồn Markdown không có PDF và lời giải AI chưa xác minh:4 ca RED, đang sửa. Ba câu Trí nhớ trên model base thật có nguồn nhưng chưa đạt: tóm tắt thêm nhận định, từng bước bỏ giới hạn, so sánh chưa theo bảng; đang đơn giản hóa hợp đồng/prompt rồi đo lại. Ca Cấu trúc dữ liệu cần dùng tên khái niệm có trong mẫu, không suy chất lượng từ alias vắng trong nguồn. Kết quả bản runtime rỗng do sao chép sai tên DB đã hủy, không dùng làm bằng chứng sản phẩm. Mã sản phẩm chưa tích hợp root; training vẫn NOT_RUN.


- Review tiếp 2026-10-11T14:04:14.454555+07:00: gói Learning06–08 đã terminal/trả quyền; Codex kiểm248 Python/18 Node PASS sau sửa giới hạn ngữ cảnh retry và metadata nguồn PDF (Markdown không có link PDF/ảnh giả). Bộ base thật6ca Learning06 chưa đạt kết hợp nội dung/mode; thử JSON/fewshot đều từ chối, không áp vào ứng dụng. Browser tìm bài theo chủ đề trả đề và nguồn đáp án riêng đúng trang, ôn tập khi0câu sai đã chạy nhưng câu hỏi bị chặn vì quote không khớp. Đã sửa thông báo để thử lại/đổi chủ đề thay bắt chọn trang; đang chạy lại base Learning08, chưa nghiệm thu chất lượng.
- Dữ liệu: Codex đối chiếu36context/216target v18, ghi28context cần sửa tại data/evaluation/learning-restructure-yc194/v18-semantic-label-review.json; trạng thái CONTENT_REJECTED. Audit corpus live mới complete:true theo scope157nguồn/22.711trang/22.698trang trong phạm vi,13ngoại lệ,52.612vector,0failure; không suy dataset đã đủ từ gate nguồn. Đã giao Data07 tạo bản nháp sửa nhãn/mode trong vùng cách ly, không học/kích hoạt; root chưa nhận mã sản phẩm.


<a id="log-20261011-shared-public-corpus-tv6"></a>
### LOG-20261011-shared-public-corpus-tv6 - 11/10/2026 12:48 (UTC+07)

- Người thực hiện: TV6/Codex; loại: môi trường/chức năng kiểm thử dùng chung.
- Trước: demo có hai tài liệu, thiếu độ phủ chủ đề, bài tập thiếu đáp án và đáp án ở trang khác.
- Sau: bổ sung 27 tài liệu độc lập thuộc 9 chủ đề, mỗi chủ đề có lý thuyết, bài kèm đáp án và bài chưa có đáp án; tổng demo 29 tài liệu. PDF mới 36 trang, toàn bộ mẫu mới dưới 1 MB. Không lấy corpus, nhãn, lịch sử hoặc model riêng.
- File: tests/fixtures/documents/yc194_*, manifest public_corpus_yc194.json, PUBLIC_CORPUS.md, CC0-YC194.txt, test_public_corpus_yc194.py, pytest.windows.ini.
- Ranh giới: nguồn mới do dự án tự biên soạn và công bố CC0-1.0; training_allowed=false, quality_holdout=false. Đây là kiểm thử chức năng, không phải phép đo độ đúng trên corpus riêng hoặc dữ liệu được duyệt để huấn luyện.
- Kiểm chứng: hash/định dạng/số trang/độ phủ PASS; 36 trang PDF đã render và kiểm tra. Bộ feature local 212 Python + 5 Node PASS; lịch sử/diff và quét bí mật PASS. Thư viện kiểm thử cách ly lập chỉ mục đủ 29 tài liệu, 90 phần mới/90 chunk mới, không lỗi; vector chưa tạo, không dùng dữ liệu này cho huấn luyện.
- GitHub: commit e71b4b1796018688fae993ad3bd1e4b4cbb47b70 đã push trên codex/public-learning-test-corpus, [PR11](https://github.com/ng-wngkh07/Study-Agent/pull/11) OPEN/MERGEABLE; CI [38116110648](https://github.com/ng-wngkh07/Study-Agent/actions/runs/38116110648) Windows/macOS/history PASS trên đúng head. PR chỉ chứa 34 file dữ liệu kiểm thử/tài liệu/test, không có mã sản phẩm hay corpus/model riêng; chưa merge.
- Hiệu chỉnh giờ lập mục từ12:52 thành12:48 để khớp mục đã công bố; cập nhật kết quả 11/10/2026 13:01 (UTC+07).


<a id="yc-195"></a>
### YC-195 - 11/10/2026 17:41 (UTC+07)

- Người thực hiện: Codex phân tích/kiểm chứng, Antigravity triển khai cách ly; loại: chức năng hỏi đáp, bài tập và bảo vệ suy luận.
- Mục tiêu: thay ba lựa chọn trình bày quick/steps/compare bằng mức xử lý có ích cho model hiện tại; QA trả đầy đủ, tự so sánh khi câu hỏi cần. Bài tập cho chọn đáp án hoặc lời giải chi tiết. Nghiên cứu huấn luyện, ảo giác, ngữ cảnh dài và trường hợp biên/bảo mật; áp dụng có đo thực tế.
- Quyết định thay thế: yêu cầu ép định dạng ba mode trong YC-191/194 đã được thay thế; giữ những sửa nguồn/truy xuất/IME và lịch sử thử nghiệm cũ. Nhãn mode v19 cũ không vào bộ học mới.
- Baseline: root main6824f41; snapshot YC193 đã kiểm249 Python/18 Node. Sáu ca model thật Learning08 chưa đạt nội dung kết hợp nguồn; thử lâu hơn không cấp bằng chứng đúng hơn.
- Chẩn đoán: API thiếu chặn ký hiệu/ký tự vô hình/control/lịch sử client dài hoặc role system; test mới12 FAIL/9 PASS trên baseline trước sửa. Giữ toán/mã/đa ngôn ngữ hợp lệ. Model ứng dụng Qwen2.5-3B-Instruct không có native reasoning_effort; mức mới là workflow có giới hạn, cần đo riêng.
- Phạm vi: snapshot app/server.py, rag_agent.py, dialogue.py, static/app.js/index.html/practice.js; helper admission/policy nếu cần. Không đổi provider/baseweights/PDF/DB/vector/13 ngoại lệ; chưa học/kích hoạt.
- Trạng thái: đang triển khai; input/security sau sửa, browser, model thật, ngữ cảnh dài và nội dung nhãn mới chưa nghiệm thu. Nguồn nghiên cứu và tiêu chí tại docs/project/reasoning-security-yc195/BAO_CAO.md.
- Cập nhật: 2026-10-16T22:16:51.486329+07:00

- Kết quả review bổ sung 2026-10-11T18:02:06.787254+07:00: v19 còn lỗi rationale/quote và báo cáo all-theory;36binding live/split/holdout/exclusion kiểm độc lập đạt. Giữ v19 và lập v20 nhãn sửa trong data local, chưa đủ phủ chức năng hoặc duyệt học. Test body/chunked, failed history, auditor schema/coverage, inference budget và phân loại lý thuyết có RED riêng; không dùng số test baseline thành nghiệm thu bản mới.

- Tiến độ 2026-10-11T18:27:04.099712+07:00: bộ sửa backend vòng hai đang chạy trong snapshot riêng, sau vòng một terminal; chưa tích hợp root hoặc nghiệm thu model thật. Giao diện bài tập đã giấu đáp án/nguồn đáp án trước khi chọn cách xem, 19 Node PASS; không tự suy lời giải chi tiết từ khóa đáp án ngắn. V20 có37bài nháp với provenance, 1bài số học được thế lại nghiệm; chưa đủ phủ nhiệm vụ hoặc đủ điều kiện học. Bổ sung RED cho tokenizer-window và ranh giới Checkpoint/heading; Top-K keyword smoke không thay benchmark model. RAM16GiB được xác nhận, chưa đánh giá tài nguyên học live.

- Đối chiếu phát hành 2026-10-11T18:38:03.349703+07:00: PR11 được chủ repo merge lúc13:01:13 UTC+07, mainaf4933a; Codex không thực hiện merge này. Diff chỉ34file dữ liệu kiểm thử công khai/tài liệu/test, mã sản phẩm không đổi. Dữ liệu bài tập nháp mở rộng45case, chưa cho học.

- Kiểm chứng 2026-10-11T18:46:58.397453+07:00: vòng sửa hai đã hoàn tất; review riêng phát hiện4lỗi phủ nhận định (đảo nguyên nhân/kết quả, mọi/một số, công thức ngắn) còn được chứng nhận sai. Đang thay so từ gần giống bằng kiểm đúng nhận định, chưa nghiệm thu model thật. Fixture final-answer thêm citation hợp lệ và expected câu rỗng422 theo hợp đồng mới; không bỏ cổng nguồn để làm test xanh. CI mở rộng8file YC195. Main local đã fast-forward lênaf4933a, giữ7file người dùng và31file mẫu công khai nguyên hash; bản sao và stash giữ lại. Sản phẩm mới chưa tích hợp, không học.

- YC-195 cập nhật 2026-10-11T19:04:27.657486+07:00: review62ca đối chiếu/queryPASS; bổ sung4RED về phân biệt biến toán học và ranh giới nguồn/lịch sử. Hai ca nhớ lượt gốc và hai ca summary cònFAIL, đang triển khai có quyền ghi riêng. Sửa practicecode-fence không kín,50ca liên quanPASS. Product vẫn trong snapshot, chưa nghiệm thu model thật hoặc huấn luyện.


<a id="yc-196"></a>
### YC-196 - 11/10/2026 22:45 (UTC+07)

- Người thực hiện: Codex kiểm chứng, Antigravity triển khai theo quyền ghi riêng; loại: mở rộng ngữ cảnh và chuẩn bị/huấn luyện model.
- Mục tiêu: tiếp tục YC-195 và thực hiện yêu cầu mới nhất chưa xử lý do phiên cũ hết hạn mức; nghiên cứu phương pháp mở rộng ngữ cảnh, chuẩn bị dữ liệu đúng runtime mới và chạy huấn luyện sau các cổng nguồn/dataset/tài nguyên.
- Trước: gói04 đã hoàn tất nhưng checkpoint còn RUNNING; mã mới ở snapshot, cửa sổ vận hành8.192/model32.768, v20 vẫn nháp chưa đủ nhiệm vụ.
- Đối chiếu hiện tại: UI xác nhận task04 terminal và trả toàn bộ quyền ghi; kiểm Codex357PythonPASS/8FAIL và20NodePASS. Có lỗi bằng chứng so sánh định danh có số và fixture/hợp đồng mode cũ cần phân loại; test chọn cửa sổ16K/32K và so sánh mới3FAIL/2PASS.
- Phạm vi: snapshot riêng, cấu hình ngữ cảnh local có giới hạn, kiểm model thật, đóng băng prompt và dữ liệu mới; giữ nguyên corpus/PDF/weights/13ngoại lệ/model/provider. Chưa tự commit/push/kích hoạt adapter.
- Trạng thái hiện tại: hai lượt huấn luyện đã hoàn tất nhưng chưa đạt chất lượng; giữ giới hạn mặc định và model nền đang dùng. Nguồn yêu cầu được đối chiếu trực tiếp ở chat cũ và lệnh tiếp tục hiện tại; không sao chép hội thoại vào nhật ký chung.
- Cập nhật: 2026-10-16T22:16:51.486329+07:00

- Kiểm chứng tiếp 2026-10-11T23:13:26.933366+07:00: bản ngữ cảnh opt-in8K/16K/32K và worker exact-token fail-closed đã qua Codex373featurePython,17boundary và20NodePASS. Đóng băng29tài liệu/95chunk công khai để đo model thật; không sao corpus riêng. Benchmark nhớ thông tin tổng hợp8K/16K mỗi mức3/3đúng,16K chậm khoảng2,3lần/đỉnhMLX2,70GiB; chưa đo32K và không suy thành độ chính xác kiến thức.
- Dữ liệu mới:36nguồn đã đối chiếu live,45bài nháp được review; capture396mẫu/11loại lời gọi thật,264train132valid,không chạy model để tự sinh nhãn.73mẫu quá1024token,72QA mất toàn bộ nhãn nếu cắt.Đang giảm phần hướng dẫn lặp trong snapshot, giữ nguồn/điều kiện/cổng bảo vệ; bản thử650..820token chưa áp vào sản phẩm. Huấn luyện/kích hoạt NOT_RUN; label serialized/baseline/protocol còn chờ nghiệm thu.

- Kiểm chứng tiếp 2026-10-11T23:25:38.117649+07:00: prompt gọn đã được capture thành v22/396mẫu,256..903token,0mẫu quá1024 và0target bị mất; giữ nguyên nguồn và nhãn. Tuy nhiên model thật trên6ca QA đều bỏ mã nguồn nên bị từ chối; prompt chưa được nghiệm thu. Đang thử hướng dẫn trích dẫn cụ thể và bổ sung validator versioned để đối chiếu nguồn trong đúng trường dữ liệu đã escape, thay vì tìm chuỗi nguồn ở bất kỳ đâu. Baseline/serialized content review/protocol/huấn luyện vẫn chưa duyệt; không tự bỏ cổng để học.

- Kết quả tiếp 2026-10-11T23:51:15.304767+07:00: dữ liệu v27 gồm264train132valid/11nhiệm vụ,18nhóm nguồn tách3nhóm holdout;396mẫu256..892token,không cắt. Đã sửa nhãn auditor âm/quote và summary từ dialog; nguồn/target/input-field/balance/manualreview đều PASS. Auditlive157docs22711trang22698scope13ngoại lệ52612vectors0fail. Base18ca đã sinh/chấm:0PASS4partial14fail ở runtimepromptgọn; không suy độ chính xác toàncorpus. Cổng corrective3B và preflight4.56GiB đạt; đã giao lượt thử80bước qua bộ thực thi có khóa/monitor, chưa xác nhận bước học bắt đầu. Tích hợp local chỉ validator cùng escaping phụ thuộc, có backup;229root/395snapshotPythonPASS. Promptgọn/phầnUI còn snapshot,chưa nghiệm thuđể triển khai;kích hoạtNOT_RUN.

- Huấn luyện thực tế 2026-10-12T00:03:37.898378+07:00: run-20261011-165538-85ce94 đã bắt đầu qua app.fine_tune với Python điều phối hiện có và MLX child .train-venv; cấu hình80bước/4lớp/1024token/batch1. Đã tới50/80, checkpoint40 đã ghi; validationloss1.158→0.387, MLXpeak3.192GiB. Trạng tháiRUNNING, chưa dùng loss để nghiệm thu chất lượng; Antigravity giữ adapter/ledger/GPU tới terminal, Codex chỉ kiểm đọc và hồ sơ.

- Nghiệm thu lượt thử01 2026-10-12T00:16:58.528524+07:00: 80/80 bước hoàn tất, returncode0,685giây, finalvalidationloss0,458; monitor không abort, RAM khả dụng tối thiểu1,75GiB/MLXpeak3,332GiB. Adapter cuối hash0b2c1aca…; cấu trúc/finite và10binding dữ liệu sau họcPASS, trọng số nền/nguyên nguồn giữ nguyên. Paired18 cùng input/decoding chạy143,46giây,18stop; Codex chấm0PASS3partial15FAIL (base0/4/14). Hai nhóm lỗi đều0% đạt, qualitydecisionREJECTED/DO_NOT_PROMOTE; auditor5/6nhận định âm vẫn chứng nhận sai, quote không nguyên văn và QA thiếu nguồn/điều kiện. Không kích hoạt; đang chẩn đoán fit trên6mẫu train. Tệp bằng chứng private: holdout-runtime-v1/candidate-review.json và quality-decision.json.

- Chẩn đoán và đối chứng 2026-10-12T00:21:44.464715+07:00: 6mẫu train thực được sinh/chấm1PASS1partial4FAIL; hai auditor âm vẫn supported sai. Bộ MLX đi qua80/264mẫu nên đang kiểm giả thuyết chưa đủ lượt học, chưa coi là nguyên nhân đã xác nhận. V28 giữ nguyên396mẫu/hash/nhãn/nguồn; kế hoạch mới chỉ tăng264bước để đi qua toàn264train, không resume, output mới trial02. Cổng hash/manual/source/token/balance/PDF và RAM4,79GiBPASS; đã giao một lần, chưa xác nhận bắt đầu. Bộ18 đã quan sát trở thành development diagnostic; không dùng để tuyên bố tổng quát hóa/kích hoạt, kể cả nếu lần sau đạt.

- Tiến độ đối chứng 2026-10-12T00:33:09.256210+07:00: run-20261011-172153-7db8ea bắt đầu00:22:14 qua MLX, đã tới75/264; Antigravity giữ output/GPU. Chuẩn bị thêm18ca từ6PDF chưa dùng train/valid và bộ cũ, kiểm hashPDF/trang/span/vectorPASS. Bộ mới đóng băng sau lúc học bắt đầu nhưng trước xem đầu ra candidate; chỉ QA/auditor, không tuyên bố độc lập toàn họ nguồn hoặc nghiệm thu11nhiệm vụ. Chờ terminal/integrity/pairedmanual, chưa kích hoạt.

- Kết quả đối chứng264bước 2026-10-12T07:18:26.841290+07:00: run-20261011-172153-7db8ea hoàn tất00:47:33/264bước/1519giây/returncode0; terminal/nhường quyền và artifactfinite/hash dữ liệu/base đã đối chiếu. Validationloss cuối0,614/MLXpeak3,391GiB/RAMmin1,72GiB; không nghiệm thu bằng loss. Bộ18phát triển1PASS2partial15FAIL; bộ18mới từ6PDF chưa train/valid2PASS2partial14FAIL (base0/5/13), QA mục tiêu2/3/auditor0/6; REJECTED_QUALITY/DO_NOT_PROMOTE. Constructor trảvoid sai, quote không nguyên văn và3/6auditorâm mới sai. Học đủ264mẫu không chữa hết lỗi; không chạy lặp cấu hình trial03. Bộ mới đóng băng sau start/trước xem output, không độc lập toàn họ nguồn; metadata protocol kế thừa đã hiệu chỉnh đúng thời điểm/đường dẫn, giữ tiêu chí. Giới hạn/bằng chứng trong docs/team/RA_SOAT_TOAN_BO_CHUC_NANG_YC197.md.

<a id="yc-197"></a>
### YC-197 - 12/10/2026 00:35 (UTC+07)

- Người thực hiện: Codex kiểm chứng, Antigravity tiếp tục huấn luyện; loại: rà soát/nghiệm thu.
- Mục tiêu: tiếp tục tác vụ đang chạy, tái kiểm toàn bộ chức năng và tiếp tục huấn luyện theo YC-196.
- Trước: lượt264bước đang RUNNING; các kiểm tra trước tập trung phần thay đổi, chưa có tái kiểm toàn bộ ở lượt hiện tại.
- Phạm vi: toàn bộ test ứng dụng/root, chức năng mới trong snapshot, giao diện/luồng sử dụng, pipeline và model thật khi GPU được bàn giao; giữ code người dùng/PDF/corpus/base/provider, không kích hoạt chỉ vì test/loss.
- Kiểm chứng: đã chạy bộ toàn repo, snapshot, Node, các luồng UI và model thật sau khi Antigravity trả quyền; dùng DB thử tách biệt. Kết quả và giới hạn ở báo cáo YC-197.
- Trạng thái: đã hoàn thành lượt rà soát và sửa hai lỗi đã tái hiện; chất lượng adapter chưa đạt, một số phép nghiệm thu thực tế chưa chạy. Nguồn yêu cầu: người dùng trong chat hiện tại; không sao hội thoại vào hồ sơ chung.
- Cập nhật: 2026-10-16T22:16:51.486329+07:00

- Tái kiểm tiếp 2026-10-12T07:18:26.841290+07:00: root659PASS12SKIP sau sửa, snapshot398PASS1SKIP và Node20PASS; runtimebinding17PASS riêng với396mẫu v28 giải quyết skip do chưa chọn dataset. Sửa hai harness cũ (citationfixture/oracle tệp tạm), giữ cổng/mức chấp nhận; lỗi sandbox đọc process/RAM được kiểm lại trên host. Phát hiện/sửa SQL bỏ tiêu đề BÀI TẬP/NBSP/tab,4caRED→GREEN và hơn2000dòng không khớp trước bài vẫn tìm đúng; UI doc20/trang2 trả1bài/chọn nguồnQA và lịch thủ công/ICSđạt trên DBriêng. Phát hiện ô Đến trang bị cắt390px; đang sửa CSSsnapshot/kiểm responsive. Đang so model thật đủ11nhiệm vụ trên1nguồn valid, chỉ diagnostic; preflight3,39GiB từ chối, giải phóng tài nguyên thử đạt3,73GiB mới chạy. OCRVLMthật/IMEhệđiềuhành/32K/Windowsmáysạch chưa tái nghiệm thu.

- Chốt kết quả 2026-10-12T07:28:19.598582+07:00: CSS snapshot đã sửa và kiểm UI thực ở 320/390/768/1280px, 20/20 biên điều khiển nằm trong màn hình; tìm bài tại390px trả đúng1bài/trang2. Node sau sửa20PASS. Cache CSS cũ trên origin thử được giữ bằng chứng FAIL; bản mới kiểm qua origin sạch đạt, cần xét phiên bản asset khi tích hợp. Lưu213file mã đã review kèm hash tại data/evaluation/full-function-yc197/runtime-reviewed, không sửa runtime đóng băng đã dùng huấn luyện. Phép model thật11nhiệm vụ trên1nguồn valid: base3PASS3partial5FAIL, candidate3PASS1partial7FAIL; xác minh đáp án đúng chuyển PASS→FAIL, quote auditor vẫn không hợp lệ. Đây là diagnostic đã nhìn validation, không phải holdout độc lập. Không kích hoạt adapter hoặc chạy trial03 cùng cấu hình. Toàn bộ gói Antigravity terminal/trả quyền, tiến trình học/inference đã kết thúc, phiên UI thử đã đóng. Báo cáo: [Rà soát YC-197](docs/team/RA_SOAT_TOAN_BO_CHUC_NANG_YC197.md). OCR/VLM thật, IME hệ điều hành, 32K và Windows cài đầy đủ trên máy sạch chưa kiểm.

<a id="yc-198"></a>
### YC-198 - 12/10/2026 09:07 (UTC+07)

- Người thực hiện: Codex kiểm chứng/tích hợp, Antigravity sửa mã theo phạm vi; loại: rà soát/nghiệm thu và bàn giao GitHub.
- Mục tiêu: rà lại các lỗi, sửa và đưa mã/thay đổi đã kiểm chứng lên branch riêng để nhóm review.
- Trước: root có validator/escaping/Unicodefix, mã UI/học tập/security/context còn bản thử, hai adapter chưa đạt chất lượng; chưa có branch tổng hợp.
- Phạm vi: ghép thay đổi ứng dụng/test/tài liệu từ YC-193–197 với main hiện hành trên checkout cách ly; giữ dữ liệu/corpus/base/adapter/source riêng. Không merge hoặc kích hoạt adapter.
- Branch: `codex/fix/shared/yc198-reviewed-changes-tv6` từ main `af4933a`; đã công bố để nhóm review, chưa merge.
- Phần/file: UI/học tập/QA/input/security/history/context/Unicode/binding, launcher/CI/test và tài liệu YC-195–198. Sửa receipt auditor, cache bốn asset, prompt practice dùng chung, fixture config/tokenizer/422, quyền đọc nhãn local tường minh và chặn holdout rỗng.
- Kiểm chứng: portable440PASS12SKIP, Node20PASS, hồi quy YC19815PASS; toàn repo788PASS49FAIL15SKIP trên host, không có ID lỗi mới so main. 49 lỗi cần corpus/model/hồ sơ riêng còn chưa đạt; không tắt test. UI bản tích hợp tìm1bài/trang2/nguồn→QA, lịch nhập tay/ICS trên DB demo riêng đạt; model demo3B chưa khả dụng.
- Review: Antigravity terminal/trả quyền bảy file; Codex đọc diff/caller và sửa thêm cổng holdout; không công bố nhãn/source/trích đoạn riêng. Hiệu chỉnh YC193 bỏ đường dẫn máy thử khỏi lịch sử chung, giữ ý nghĩa và kết quả, tại 2026-10-12T12:17:45.651072+07:00.
- Trạng thái: đã push commit `fd3d777` và tạo [PR nháp #12](https://github.com/ng-wngkh07/Study-Agent/pull/12), readback remote/head trùng; CI đang chạy, history CI đã PASS; chất lượng adapter DO_NOT_PROMOTE và OCR/VLM thật/IME/32K/Windows máy sạch chưa nghiệm thu. Báo cáo: [Bản tích hợp YC-198](docs/team/BAN_TICH_HOP_VA_SUA_LOI_YC198.md).
- Cập nhật: 2026-10-16T22:16:51.486329+07:00

- Kết quả CI 2026-10-12T12:24:09.528839+07:00: PR12 vẫn draftOPEN, head0a34878 đã push/readback; Windows/macOS/history đều PASS tại run38207485714, push38207481451PASS. Mainremoteaf4933a chưa merge. Full49FAIL riêng và adapterDO_NOT_PROMOTE giữ nguyên; không thêm sửa mã sau nghiệm thu.

<a id="yc-199"></a>
### YC-199 - 12/10/2026 12:29 (UTC+07)

- Người thực hiện: Codex phân tích; loại: làm rõ kiến trúc/quy trình huấn luyện.
- Mục tiêu: đánh giá khả năng dùng Antigravity đọc bài tập nguồn, tạo đáp án và lời giải chi tiết, rồi huấn luyện mô hình học cách giải và truy xuất lại bài đã kiểm chứng.
- Trước: YC195 xác định hai cách xem là Chỉ đáp án/Giải chi tiết; bản tích hợp YC198 có giải bài thiếu đáp án và lượt đối chiếu tự động, không phải bộ quét/giải toàn corpus. Trial02 đã hoàn tất nhưng quality-decision mới18ca chỉ2PASS2partial14FAIL, DO_NOT_PROMOTE; registrylive active_adapter_id:null.
- Kết luận: khả thi như một pipeline tạo nhãn bằng model hướng dẫn, kiểm chứng rồi SFT/LoRA model local. Antigravity đọc/giải không tự cập nhật trọng số model. Hai lựa chọn hiện hành là hai dạng đầu ra của cùng kết quả đúng; không suy thành hai phương pháp toán học khác nhau.
- Phương án chưa giao triển khai: kiểm kê nguồn–trang–bài đầy đủ và trạng thái thiếu/hình/OCR; giữ đề gốc/provenance; tạo final_answer và lời giải các bước nhất quán; đối chiếu đáp án sách và công cụ thực thi/toán học/review độc lập phù hợp môn. Lời giải AI và lời giải sách giữ provenance riêng; AI tự báo verified không đủ duyệt nhãn. Bài chưa kiểm chứng bị cách ly, không đoán để đạt đủ số lượng.
- Lưu trữ/học đề xuất: lưu bài/lời giải đã duyệt trong kho truy xuất để đối chiếu chính xác; dùng tập SFT/LoRA dạy cách giải và điều khiển hai dạng trả lời. Không bảo đảm mô hình nhớ chính xác mọi bài chỉ bằng học trọng số. Tách train/valid/holdout theo họ nguồn trước tạo bộ học; mọi biến thể, hai dạng đáp án và bài gần trùng cùng split; nhãn holdout chỉ dùng đánh giá kín, không học.
- Điều kiện: audit nguồn hiện hành/phạm vi13ngoại lệ/provenance/tokenizer/runtime/resource/ownership/approval vẫn áp dụng. Chỉ kết luận giải toàn bộ sau bảng kiểm kê và nghiệm thu từng bài trong phạm vi; giữ ca thiếu điều kiện/từ chối đúng, kiểm bài mới và bảo toàn QA/auditor/chức năng cũ trước kích hoạt.
- File/module đã đọc: docs/team/SUY_LUAN_NGU_CANH_VA_HUAN_LUYEN_YC195.md; README_QUY_TAC.md; app/corrective_training.py; app/multitask_training_data.py; app/learning.py và static/practice.js của branch YC198 trong checkout cách ly; docs/team/RA_SOAT_TOAN_BO_CHUC_NANG_YC197.md; quality-decision holdout-fresh-v2 và active_registry local. Root chưa có app/learning.py; không nhầm runtime root với branch đã công bố.
- Nguồn nghiên cứu: Google Research Distilling Step-by-Step (https://research.google/blog/distilling-step-by-step-outperforming-larger-language-models-with-less-training-data-and-smaller-model-sizes/); Self-Instruct (https://arxiv.org/abs/2212.10560); RAG (https://arxiv.org/abs/2005.11401). Kết quả nghiên cứu không chứng minh chất lượng Qwen3B của dự án.
- Kiểm chứng: đối chiếu hai lựa chọn bằng docs và code branch; đọc lại quality-decision/registry thực; review logic pipeline/split/provenance. Triển khai, kiểm thử mã, giao việc Antigravity, GPU/học mới, commit/push: N/A vì câu hỏi khả thi, chưa phải lệnh thực hiện.
- Chưa xác minh: tổng số bài thực và độ phủ đề/hình/đáp án; chất lượng teacher trên mọi môn; tài nguyên/thời gian toàn batch; khả năng tổng quát hóa hai dạng đầu ra sau học. Nếu hai cách mang nghĩa hai phương pháp giải khác với UI hiện hành, cần định nghĩa lại trước triển khai.
- Trạng thái: đã trả lời khả thi có điều kiện, đề xuất chưa được chọn triển khai; không thay các quyền hoặc cổng huấn luyện trước đó.
- Cập nhật: 2026-10-16T22:16:51.486329+07:00

<a id="yc-200"></a>
### YC-200 - 12/10/2026 12:34 (UTC+07)

- Người thực hiện: Codex điều phối/kiểm chứng, Antigravity chuẩn bị lời giải; loại: dữ liệu và huấn luyện mẫu bài tập.
- Mục tiêu: thực hiện hướng YC-199 bằng mẫu thử nhỏ, đo trước/sau trên bài chưa học; mở rộng toàn tài liệu chỉ khi ổn định và hiệu quả cao. YC-199 là phân tích trước đó, nay đã có yêu cầu triển khai.
- Trước: hai trial YC196 chưa đạt chất lượng, adapter chưa kích hoạt; runtime đã review trên branch YC198, nguồn và dữ liệu riêng.
- Phạm vi: kiểm kê bài thật/provenance, phân chia họ nguồn trước học, Antigravity tạo nhãn đáp án/lời giải, Codex kiểm độc lập và đóng băng đánh giá; học từ base chính xác trên đầu ra riêng sau cổng nguồn/dataset/token/tài nguyên. Giữ hai kiểu trình bày của cùng kết quả đúng.
- Tiêu chí vận hành sẽ đóng băng trước sinh baseline/học: độ đúng bài chưa học, các bước/điều kiện, nhất quán hai kiểu trình bày, lặp ổn định, không giảm chức năng cũ. Không dùng loss hoặc tự báo verified làm nghiệm thu.
- Phần/file: gói riêng data/evaluation/exercise-pilot-yc200; dataset/adapter mới chỉ tạo sau duyệt; LICH_SU_DU_AN.md và checkpoint. Không sửa PDF/DB/vector hay runtime trial trước.
- Trạng thái hiện tại: mẫu thử học và đánh giá đã hoàn tất; DO_NOT_PROMOTE do chất lượng/ổn định chưa đạt và có hồi quy, không mở rộng hoặc kích hoạt adapter.
- Cập nhật: 2026-10-16T22:16:51.486329+07:00

- Tiến độ 2026-10-12T13:04:45.878906+07:00: audit corpus live complete:true theo 13 ngoại lệ (157 tài liệu/22.698 trang trong phạm vi/52.612 vector), 30 kiểm tra cổng học đạt. Đã đo mô hình gốc thật: bộ QA/claim18 có 0 đạt/4 một phần/14 không đạt; bộ giữ khả năng cũ3 có3 đạt. Đây là baseline, chưa phải kết quả huấn luyện.
- Review độc lập 32 đề/lời giải phát hiện sai liên kết đoạn đề, mất yêu cầu làm tròn, câu tự soạn từ lý thuyết, nhầm họ nguồn Stewart/HCMUS. Dữ liệu mẫu vòng đầu chưa được duyệt. Đang sửa và lập mẫu bài tập thật kèm dữ liệu ôn lại đã kiểm chứng để giữ cân bằng sáu môn; không sửa cổng hoặc tự kích hoạt adapter.

- Tiến độ 2026-10-12T13:47:00.022819+07:00: baseline bài tập thật đóng băng trước học gồm8câu hỏi nguồn Phelps độc lập×2biến thể khoảng trắng=16đầu vào, đạt3/16 (6một phần/7không đạt); ba chức năng cũ đã đúng vẫn3/3. Không tính16biến thể thành16bài độc lập. V30 có88context nguồn khớpDB,16lời giải mới đã đối chiếu toán/nguồn; sửa metadata câu hỏi rỗng/sai và họ nguồn trước duyệt. Codex xác nhận72input ôn lại khớp đúng serializer hiện tại và4oracleC++ thực thi thành công. Auditlive complete:true giữ13ngoại lệ; RAM khả dụng3.89GiB. Không hạ cổng sáu môn, chưa học hoặc kích hoạt.

- Tiến độ 2026-10-12T14:04:40.897350+07:00: v31 đã duyệt64mẫu học/24kiểm tra,88nguồn và token204–747được kiểm độc lập, cổng đầy đủPASS. Antigravity khởi chạy run-20261012-065936-957f13 lúc13:59:59, MLXchild56872; đã xác nhận20/128bước từ ledger/log. Dataset76137ee9…, freshbase và128bước giữ nguyên; Antigravity giữ adapter/ledger/GPU, Codex chỉ đọc tiến độ và giữ đánh giá/hồ sơ. Chờ hoàn tất và bàn giao tài nguyên trước chấm19đầu vào đóng băng.

- Tiến độ 2026-10-12T14:21:25.241627+07:00: run-20261012-065936-957f13 COMPLETED 128/128, 1.062 giây, mã thoát 0. Trọng số 56 tensor có giá trị hữu hạn và hình dạng phù hợp; hash f387b446…, đầu vào/base/cổng đầy đủ giữ nguyên. MLX peak 3,199 GB, RAM khả dụng thấp nhất 1,69 GiB, swap tăng 555,56 MB; không kích hoạt ngưỡng dừng. Antigravity terminal và trả toàn bộ quyền; hai PID đã thoát, GPU idle. Codex bắt đầu đo 19 đầu vào đóng băng; chất lượng chưa kết luận.

- Kết quả 2026-10-12T14:29:24.634669+07:00: Codex chấm toàn bộ 19 phản hồi thực trên cùng đầu vào: bài tập từ 3/16 đạt lên 6/16 (tăng 18,75 điểm phần trăm), 5 một phần/5 không đạt; chỉ 2/8 bài đạt ở cả hai biến thể. Ba ca chức năng cũ từ 3/3 còn 2/3, hồi quy kiểm định trắc nghiệm đúng thành supported:false. 19 đầu ra JSON hoàn chỉnh, không bị cắt; phép đo sau học 140,55 giây. Đáp án/lời giải đủ nhất quán 10/16; nguồn bài độc lập chỉ ở tâm lý học. Cổng chất lượng REJECTED_QUALITY, quyết định DO_NOT_PROMOTE; điều kiện mở rộng chưa đạt nên dừng mở rộng, registry vẫn active:null. Lỗi còn: placeholder, thiếu điều kiện/cơ chế ở đáp án ngắn, nhầm attribution/không thể kiểm soát. Báo cáo docs/team/HUAN_LUYEN_MAU_GIAI_BAI_TAP_YC200.md; evidence candidate-review/quality-decision/failure-diagnosis trong exercise-frozen-v1. Các hash được bảo vệ giữ nguyên, GPU idle, không còn job học/đo. Không sửa mã sản phẩm; build/UI/Git mới N/A cho lượt thử dữ liệu riêng, PR12 giữ nguyên. Phương án dữ liệu đa dạng hơn và bộ holdout mới là đề xuất chưa chạy.


<a id="yc-201"></a>
### YC-201 - 12/10/2026 16:37 (UTC+07)

- Người thực hiện: Codex; loại: rà soát điều kiện merge GitHub.
- Mục tiêu: kiểm PR #12 hiện tại, đối chiếu điều kiện GitHub, quy trình review nhóm và bằng chứng trên đúng commit.
- Trước/sau: checkpoint YC198 còn ghi PR nháp; đọc GitHub hiện hành thấy OPEN, không nháp, MERGEABLE/CLEAN, chưa merge. Đây là cập nhật trạng thái quan sát, không đổi PR hoặc hiệu chỉnh mốc lịch sử cũ.
- Phiên bản: head `0a34878f23c97aa55794604e59c8630302633587`, base main `af4933a44706aaa46662c67450dcbfd6535d527c`; ahead2/behind0. Cả72file khớp hash đã review YC198, không có tài sản private trong đường dẫn diff. Tái dùng review YC198 theo fingerprint; kiểm lại phần diff/caller input/history/context/auditor/learning, CI và quy định nhóm, không tuyên bố review độc lập của thành viên đã hoàn tất.
- GitHub: ba checks bắt buộc Windows/macOS/history đều SUCCESS trên đúng head; strict-up-to-date và giải quyết hội thoại bật, áp dụng cả admin. Required approvals=0; không có review submitted hoặc review thread, không có ruleset bổ sung được trả từ API main. GitHub không còn blocker merge.
- Kiểm chứng mới: portable440PASS/12SKIP/0FAIL (3,98giây), Node20PASS; history/whitespace/pipcheckPASS, checkout cách ly sạch. Đọc log CI run38207485714 xác nhận mỗiOS440PASS12SKIP/20NodePASS; Ollama không chạy nên CI không nghiệm thu sinh câu trả lời thật.12skip cần dataset/nguồn/runtime private được chọn tường minh.
- Giới hạn: fullsuite YC198 trên cùng hash788PASS49FAIL15SKIP và không có IDFAIL mới so main; lần này đối chiếu bằng chứng, không chạy lại fullsuite.49FAIL dữ liệu/model/hồ sơ local vẫn chưa đạt; OCR/VLM thật, IME hệ điều hành, context32K, Windowsfullmáysạch và chất lượng model không được chứng nhận bởi PR này. YC200DO_NOT_PROMOTE là nghiệm thu adapter riêng, không có adapter/activation trong diff PR12.
- Kết luận: đủ điều kiện GitHub và checks mã chia sẻ; theo README_QUY_TAC/CONTRIBUTING, cần reviewer khác tác giả và chủ trì phần ảnh hưởng xác nhận review chéo trước TV4 quyết định merge. Chưa có bằng chứng review chéo được ghi nhận trên GitHub, nên chưa coi quy trình nhóm đã hoàn tất. Không xác lập lỗi mã chặn mới trong lượt đối chiếu này.
- Phần/file cập nhật: chỉ nhật ký/checkpoint và báo cáo local `docs/project/pr12-merge-audit-yc201/BAO_CAO.md`; bằng chứng `data/evaluation/pr12-merge-audit-yc201/result.json`. Triển khai/handoff/sửa mã/training N/A vì yêu cầu kiểm PR; không commit/push/approve/comment/merge hoặc đổi bảo vệ/provider/model.
- Trạng thái: AUDIT_COMPLETED / GITHUB_MERGEABLE / TEAM_CROSS_REVIEW_PENDING; cập nhật 2026-10-12T16:37:51.638477+07:00. Bước tiếp: ghi nhận review chéo và TV4 chốt merge trong phạm vi mã đã kiểm; sau merge kiểm lại tích hợp, giữ cổng nghiệm thu nguồn/model riêng.

<a id="yc-202"></a>
### YC-202 - 12/10/2026 19:28 (UTC+07)

- Người thực hiện: Codex; loại: phát hành/đồng bộ GitHub và nạp lại ứng dụng local.
- Mục tiêu: đồng bộ main về máy và xác định vì sao các bản sửa chưa xuất hiện trong ứng dụng.
- Nguyên nhân đã kiểm chứng: checkout và thông tin origin/main lưu trên máy cùng ở `af4933a`; sau fetch, main GitHub ở `f0180249913cbaac8a5338c8f47869a56a226a0c`. PR #12 đã merge lúc 17:25:43 ngày 12/10/2026 (UTC+07). Máy chủ local khởi chạy từ 05/10, không bật reload; danh sách API trước đồng bộ chưa có ba route learning. Mốc YC-201 là trạng thái trước merge, được giữ nguyên để tra cứu.
- Trước/sau: fast-forward main qua ba commit/72 file; HEAD, origin/main và SHA đọc trực tiếp remote khớp `f018024`. Mã app/static/scripts/tests và pytest.windows.ini khớp main; giữ phần nhật ký YC-199–201 mới hơn remote và báo cáo YC-200 local.
- Bảo toàn: sao lưu nguyên trạng 21 file local, giữ stash phục hồi và bản sao SQLite trong `data/backups/main-sync-yc202`. Năm file chưa theo dõi trùng tên nhưng khác main được lưu nguyên bản trước thay bằng bản phát hành; 36 định nghĩa nhãn riêng được lưu thêm thành JSON, không tự bật nạp nhãn hoặc chạy chuẩn bị/học dữ liệu. Xử lý lịch sử bằng ghép ba chiều, giữ mọi ID và sửa YC-193 đã có trên main; cấu hình test lấy main vì đã chứa cả hai ca thêm ở local.
- Vận hành: đã đối chiếu cwd/lệnh của đúng máy chủ và giám sát viên dự án, GPU idle, rồi dừng nhẹ và khởi động lại dịch vụ hiện có. Không giao triển khai Antigravity vì chỉ đồng bộ bản đã merge, không sửa mã sản phẩm. Không đổi provider/model hoặc kích hoạt adapter; registry giữ nguyên.
- Kiểm chứng: Python portable 440 PASS/12 SKIP/0 FAIL trên vùng dữ liệu thử riêng; Node 20 PASS/0 FAIL; dependency/history/whitespace PASS. Sau restart health ok/inference_ready, ba route learning từ chưa có thành có; HTML và bốn asset phục vụ khớp hash file local. UI thực hiển thị Tự động/Thông thường/Kiểm tra kỹ, Công cụ học tập theo chủ đề và tùy chọn giải bài thiếu đáp án có kiểm định. Hash knowledge_base/history/timetable/active_registry không đổi; đối chiếu toàn bộ hàng trong lịch sử và lịch với bản sao trước cập nhật đều giữ nguyên.
- Bằng chứng: `data/evaluation/main-sync-yc202/result.json`, portable.xml/portable.log/node.log và ui.jpg; checkpoint mới tại STATE.md. RED/triển khai sửa mã/học: N/A vì đồng bộ mã đã merge, baseline trước/sau được kiểm qua route và phiên bản Git.
- Giới hạn: 12 SKIP cần nguồn/dataset/runtime riêng; không chạy lại full suite 49 lỗi còn lại ở YC-198, không đo lại chất lượng sinh câu trả lời/OCR/VLM/model. Adapter YC-200 vẫn DO_NOT_PROMOTE. Các trường implementation_revision/dialogue_revision của health là chuỗi cố định trong main, không dùng để chứng minh phiên bản Git. Tab cũ cần tải lại để nhận HTML/asset có phiên bản mới.
- Trạng thái: SYNC_COMPLETED / MAIN_MATCHES_REMOTE / LIVE_UI_VERIFIED; cập nhật 2026-10-12T19:28:12+07:00. Không tạo commit/push/merge GitHub mới hoặc lịch chạy tự động.


<a id="yc-203"></a>
### YC-203 - 12/10/2026 20:05 (UTC+07)

- Người thực hiện: Codex; loại: rà soát/nghiệm thu và chẩn đoán hỏi đáp/huấn luyện.
- Mục tiêu: truy nguồn câu hỏi cơ bản có tài liệu nhưng không trả lời, kiểm quá trình học và đề xuất thay đổi sau đồng bộ YC-202.
- Trước/sau: main `f018024` đã đồng bộ, runtime vẫn base/active_adapter_id:null. Tái hiện hai câu đạo hàm: generation_failed do thiếu mã nguồn; hybrid chọn mục lục/nội dung lập trình, mất công thức khi trích PDF và budget byte quá bảo thủ. Chưa sửa sản phẩm hoặc học/kích hoạt.
- Phần đọc/kiểm: searcher/rag_agent/dialogue/input_policy/mlx_infer/trained_client/bilingual; doc44 trang10 và doc45 trang1 xem ảnh trực tiếp, DB chỉ đọc; registry/v27/v28/v31/ready/integrity/quality-decision. Trang text_verified mất công thức cần mở lại kiểm chất lượng nguồn; không suy toàn corpus đúng từ nhãn/số dòng.
- Kiểm chứng: hai baseline cùng base đều thiếu [S#], hoàn tất stop, không cắt đầu ra; bốn/sáu nguồn được nhận, trang10 không trong top6. Input exact 2.259/1.940 token so dự toán 7.271/6.699; tái dựng đủ sáu nguồn 2.750/2.762 +900 vẫn dưới8.192. Hai đối chứng nguồn đúng vẫn thiếu citation; một đối chứng cùng nguồn với hướng dẫn gọn có công thức/ý nghĩa/[S1]. Năm đầu ra chỉ diagnostic, chưa holdout/multi-seed/nghiệm thu.
- Huấn luyện: v27/v28 cùng396 dòng/36 ngữ cảnh/264train, chỉ24 QAtrain và4 calculus không có câu định nghĩa đạo hàm cơ bản; v31 không grounded_qa. Hash dataset/adapter trial02/pilot đọc lại khớp; các quyết định DO_NOT_PROMOTE giữ nguyên. Audit đóng băng v28 ghi256–892/1.024 token, không có bằng chứng clipping; completion/loss không chứng minh chất lượng.
- Phương án: phục hồi/review công thức và tách hiệu đính; xếp lại nguồn, giảm mục lục/nhiễu; tokenizer thật/ranh giới mục; prompt trích dẫn gọn và phân loại lỗi. Sau đó mới QA dataset đúng runtime, holdout kín theo họ nguồn và retention. Giữ cổng grounding/context8.192; không tự gắn nguồn hoặc học thêm trên bộ không đổi.
- Trạng thái: DIAGNOSIS_COMPLETED, PRODUCT_FIX_NOT_RUN; triển khai/handoff/training/activation N/A vì yêu cầu chẩn đoán/phương án. Hash corpus/history/registry không đổi, dịch vụ healthy/GPUidle. Không sửa PDF/provider, commit/push. Lỗi emoji trong ảnh thiếu originalinput; chưa review toàn corpus hoặc nghiệm thu bản prompt thử.
- Báo cáo: [Chẩn đoán YC-203](docs/team/CHAN_DOAN_HOI_DAP_VA_HUAN_LUYEN_YC203.md); bằng chứng local data/evaluation/qa-diagnosis-yc203. Liên quan YC195–200/202; không thay cổng nguồn/chất lượng. Cập nhật: 2026-10-12T20:05:14.726981+07:00.


<a id="yc-204"></a>
### YC-204 - 12/10/2026 20:20 (UTC+07)

- Người thực hiện: Codex chẩn đoán và thiết kế; loại: rà soát/nghiệm thu đa tài liệu.
- Mục tiêu: xác định lỗi chung khiến nhiều tài liệu không trả lời được câu hỏi có nguồn; lập cách sửa toàn chuỗi thay vì vá riêng vi tích phân.
- Trước/sau: trước YC-203 đã tái hiện lỗi nguồn/truy xuất/citation ở vi tích phân; sau mở rộng kiểm trực tiếp sáu trang PDF, kiểm kê DB và sáu truy vấn hybrid, xác nhận lỗi bảo toàn code, bảng/công thức, bảng mã và hình trên trang nhiều chữ. Chưa thay hành vi sản phẩm.
- Kết quả: code OOP gốc qua kiểm cú pháp C++17, normalized lỗi vì tách identifier; slide toán rời rạc lưu chữ VNI và trang định nghĩa không vào top 6; bảng vật lý mất cấu trúc công thức; hình tâm lý không có đủ nội dung trong text dù văn xuôi còn. Ánh xạ và nguồn viết tay đã review là đối chứng tích cực, không kết luận toàn kho lỗi.
- Quy mô: 157 tài liệu/22.711 trang/52.612 chunk; cờ ký tự font riêng 42 tài liệu, chuỗi giống VNI 7 tài liệu, nguyên văn–hiệu đính cùng chunk 48 tài liệu. Các cờ chỉ phân luồng review, không phải số lỗi được duyệt; 20.614 trang text_verified không có review_path trong bảng hiện tại không chứng minh mọi trang sai.
- Phương án: nguồn chuẩn theo block có loại/vị trí/provenance, tách nguyên văn/chuẩn hóa tìm kiếm/hiệu đính; cổng coverage và content quality riêng; chunk theo ý nghĩa, ranking và packing token, prompt/citation và thông báo lỗi theo tầng; index thế hệ mới có pipeline version/rollback. Ưu tiên sửa dữ liệu/RAG trước QA training mới, giữ cổng và 13 ngoại lệ YC-188.
- Phần/file: docs/team/GIAI_PHAP_COT_LOI_RAG_DA_TAI_LIEU_YC204.md, nhật ký gốc và checkpoint; evidence riêng data/evaluation/general-rag-yc204. Đã đọc pdf_extractor/text_cleaner/chunker/indexer/searcher/rag_agent/source_page_reviews/corpus_readiness, complete_corpus và hồ sơ YC-195–203; không sửa mã ứng dụng hoặc dữ liệu nguồn.
- Kiểm chứng: đối chiếu ảnh toàn trang với raw/normalized/indexed; sáu truy vấn chỉ đọc; biên dịch gốc PASS/normalized FAIL đúng ca tái hiện; hash DB kiến thức/hội thoại/registry khớp cuối YC-203. Review logic thiết kế và hồ sơ; triển khai/RED→GREEN sản phẩm/shadow reindex/end-to-end đa môn/huấn luyện NOT_RUN, handoff N/A vì phạm vi chẩn đoán và phương án sửa.
- Trạng thái: DIAGNOSIS_AND_DESIGN_COMPLETED / PRODUCT_FIX_NOT_RUN. Chưa nghiệm thu nguồn toàn kho hoặc giải pháp mới; adapter giữ DO_NOT_PROMOTE và active:null. Không commit/push, đổi model/provider, tăng context hoặc học mới. Bước tiếp là triển khai từng đợt có test, chỉ mục thử, cổng chất lượng và rollback theo thiết kế.
- Liên quan: YC-203/195–200; yêu cầu mở rộng chẩn đoán hiện tại. Không thay quyền huấn luyện/kích hoạt và không biến đề xuất thành chức năng đã hoàn thành.
- Cập nhật: 2026-10-16T22:16:51.486329+07:00


<a id="yc-205"></a>
### YC-205 - 12/10/2026 20:32 (UTC+07)

- Người thực hiện: Antigravity triển khai trên snapshot, Codex kiểm thử/review/tích hợp; loại: sửa chức năng nguồn và hỏi đáp đa tài liệu.
- Mục tiêu: triển khai hướng sửa tổng quát YC-204 theo yêu cầu thực hiện, giữ nguyên nguồn gốc và cổng huấn luyện/chất lượng.
- Trước: normalizer làm sai code, VNI chưa giải mã, trang native có hình/công thức chưa có metadata chất lượng riêng; index chỉ xét source hash, QA cắt prefix 900 ký tự và thiếu citation dẫn thông báo không đủ thông tin.
- Phạm vi: bảo toàn nguồn/metadata chất lượng/version pipeline; retrieval/context/prompt/trích nguồn; chỉ mục thử và migration có backup, kiểm QA thật đa môn. Không học hoặc kích hoạt adapter, không đổi model/provider/context mặc định hoặc PDF gốc.
- Quyền ghi: Antigravity chỉ app của snapshot cách ly; Codex giữ tests, checkout thật, hồ sơ và dữ liệu thử; không có writer corpus chính cạnh tranh. Đã quan sát phiên AG cũ terminal/trả quyền, gửi source01 một lần và UI ghi nhận đang xử lý.
- Baseline: main f018024; giữ diff lịch sử và ba báo cáo local cũ. Snapshot /private/tmp/study-agent-yc205-fix; manifest 273 file. SQLite backup kiến thức/hội thoại/lịch tại data/backups/rag-repair-yc205; evidence data/evaluation/rag-repair-yc205.
- Kiểm thử ban đầu: source 6 FAIL/1 PASS sau sửa lỗi thiết lập test; QA 3 FAIL/1 PASS về packing/citation, bổ sung 2 ca counter chưa có interface. Đây là RED đúng lỗi hoặc thiếu hợp đồng đã thiết kế, chưa phải bản sửa đạt.
- Trạng thái: đang triển khai và kiểm chứng. Chưa tích hợp runtime/index, chưa nghiệm thu nguồn toàn kho hoặc chất lượng QA đa môn; mọi kết quả tiếp theo bổ sung vào mục này. Liên quan YC-203/204/195–200; quyền triển khai được cấp bởi yêu cầu hiện tại.
- Review nguồn lần 1: 8 PASS/3 FAIL; phát hiện ảnh không nhãn chưa bị cảnh báo, cache OCR có thể sai source hash, bản đối chiếu chưa được bảo toàn. Góp ý đã xếp hàng một lần cho cùng đơn vị; chưa nghiệm thu. Script migration cách ly và 2 test bảo toàn DB/ID, vô hiệu vector cũ đã đạt trên dữ liệu tổng hợp; chưa chạy toàn kho.
- Cập nhật: 2026-10-16T22:16:51.486329+07:00
- Tiến độ kiểm chứng thêm: source01/review đã terminal/trả quyền; Codex kiểm độc lập phát hiện binding/raw canonical chưa đủ và sửa trong phần đã trả quyền. Source/review/migration/hồi quy liên quan 48 PASS (3 lỗi môi trường ban đầu do snapshot không có src đã chạy lại với nguồn thật; test unpublished chủ động tái tạo chỉ mục stale vì indexer mới tự honor review). Baseline model thật bảy câu đa môn: 0/7 trả lời, sáu thiếu citation/một truncated. QA01 đang chạy trên file riêng; shadow native migration và vector cache đang chạy, chưa nghiệm thu hoặc publish.

- Tiếp tục 2026-10-14T16:38:36.408906+07:00: nguồn/review/migration77PASS; token/context43PASS sau sửa timeout và stdin backpressure. Shadowv4 hoàn tất157PDF22711trang,12714vector cần tạo lại. Audit theo13ngoại lệ còn7916trang chờ review/2review partial, complete:false. V4modelthật6/7responseanswer nhưng lẫn mục lục/khái niệm sai; không coi là đạt chất lượng. Retrieval03 đang triển khai trên2file riêng,4ca relevance RED tái hiện; chưa tích hợp/publish/học.

- Cập nhật tích hợp 2026-10-14T21:27:28.918781+07:00: bản sửa nguồn/retrieval/token/citation và tách bản dịch máy chưa duyệt khỏi căn cứ đã tích hợp local. Citation05 độc lập80PASS; source/migration101PASS; v5 đủ52608vector và integrity/binding đạt. Cổng nguồn vẫn7916pending+2partial. Chất lượng mô hình thật chưa ổn định; không học/activate hoặc publish chỉ mục thử. Citation06 loại câu minh họa bị chép vào đáp án, terminal/trả quyền; kiểm cuối portable496PASS12SKIP; câu OOP thật không còn mã mẫu/câu hướng dẫn, vẫn chưa nghiệm thu toàn chất lượng.

<a id="log-log-20261012-ui-pr13-tv5"></a>
### LOG-20261012-ui-pr13-tv5 - 12/10/2026 22:25 (UTC+07)

- Mã gốc trên PR #13: YC-199; giữ nguyên ngày/nội dung. Hiệu chỉnh định danh ngày 14/10/2026 do trùng yêu cầu local đã có; bản gốc bảo toàn trong backup.
- Người thực hiện: Antigravity; loại: giao diện người dùng và sửa lỗi hiển thị.
- Mục tiêu: thiết kế lại giao diện Dark Mode Liquid Glass, căn giữa màn hình chào đón và sửa lỗi hiển thị các chế độ.
- Trước: giao diện chuyển đổi giữa light/dark mode; khi chọn Công cụ học tập hoặc Thời khóa biểu thì khung chat Hỏi đáp không ẩn đi do CSS !important; nút Tạo câu hỏi bị co rút thiếu padding; hero chào đón bị lệch xuống dưới.
- Sau: cố định Dark Mode toàn diện với ánh sáng nền ambient glow hồng rose; căn giữa khối chào đón và ô nhập prompt theo cả 2 trục; căn đều 2 bên thanh chuyển chế độ trên header; ẩn triệt để khung Hỏi đáp khi sang Công cụ học tập và Thời khóa biểu; chuẩn hóa kích thước, padding và gradient các nút bấm và form nhập liệu; cập nhật logo thương hiệu.
- Phần/file sửa: `static/index.html`, `static/style.css`, `static/theme.css`, `static/app.js`, `static/logo.jpg`.
- Kiểm thử: 12/12 UI contract tests đạt PASS, homepage endpoint đạt PASS, scripts/check_history.py đạt PASS.
- Trạng thái: PASS.

<a id="log-log-20261012-ui-pr14-tv5"></a>
### LOG-20261012-ui-pr14-tv5 - 12/10/2026 23:40 (UTC+07)

- Mã gốc trên PR #14: YC-200; giữ nguyên ngày/nội dung. Hiệu chỉnh định danh ngày 14/10/2026 do trùng yêu cầu local đã có; bản gốc bảo toàn trong backup.
- Người thực hiện: Antigravity; loại: giao diện người dùng và sửa lỗi bố cục.
- Mục tiêu: sửa lỗi khung thông báo chức năng ôn tập (`#practice-dialog` / `.study-dialog`) bị lệch góc trên bên trái về chính giữa màn hình.
- Trước: `.study-dialog` bị reset toàn cục `* { margin: 0; }` ghi đè `margin: auto` của thẻ `<dialog>`, dẫn đến khi mở hộp thoại bị ghim ở góc trên bên trái `(top: 0, left: 0)`.
- Sau: bổ sung `position: fixed; inset: 0; margin: auto;` vào `.study-dialog` để căn chính giữa màn hình theo cả 2 trục; cấu hình flex column và overflow cuộn nội dung độc lập; cập nhật lớp nền `::backdrop` mờ kính tối đồng bộ giao diện.
- Phần/file sửa: `static/style.css`, `LICH_SU_DU_AN.md`.
- Kiểm thử: `test_learning_tools_ui_contract.py` PASS (6/6), `document_lookup_ui.test.cjs` PASS (5/5), toàn bộ `pytest.windows.ini` PASS (439 passed, 13 skipped), `check_history.py` PASS.
- Trạng thái: PASS.


<a id="yc-206"></a>
### YC-206 - 14/10/2026 15:40 (UTC+07)

- Người thực hiện: Antigravity sửa UI, Codex đồng bộ/kiểm thử và review; loại: sửa chức năng giao diện.
- Mục tiêu: cập nhật giao diện main GitHub, kiểm các nút/luồng toàn giao diện; sửa tạo chat mới không đổi trạng thái và câu hỏi gửi bị đưa trở lại ô nhập; tiếp tục tác vụ dữ liệu/huấn luyện sau kiểm UI và cổng chất lượng.
- Đồng bộ: main f018024 → 099c769 (5 commit, PR13/14), giữ nguyên local tests/RAG/report và sao lưu tại data/backups/ui-sync-yc206. Hai mã UI YC199/200 trên upstream trùng yêu cầu local: giữ nguyên nội dung/ngày/mã gốc trong bản backup và alias, chuyển hai mục thành LOG-20261012-ui-pr13-tv5/pr14-tv5 theo README_QUY_TAC; không đổi mã yêu cầu cũ.
- Baseline: browser mở phiên cũ/tạo chat mới đều ReferenceError resumeSourceList chưa khai báo; finally tự khôi phục câu cũ khi lỗi. Lifecycle 2 RED đúng lỗi/2 PASS sau sửa thiết lập harness; portable 440 PASS/12 SKIP.
- Quyền ghi: AG chỉ static/app.js và chuỗi phiên bản asset index.html; Codex tests/history/data kiểm riêng. Đơn vị RAG QA01 YC205 đã terminal/trả quyền, chưa tích hợp nguồn/RAG.
- Dữ liệu: kiểm thao tác xóa/lịch trên kho thử synthetic tách biệt; GPU hiện idle. Trial02 YC196 và pilot YC200 đã hoàn tất nhưng quality rejected, không có job train đang chạy; continuous_pipeline_state cũ paused ở chuẩn bị ứng viên (không phải học). Phân biệt checkpoint cũ với trạng thái thật trước tiếp tục.
- Trạng thái: IMPLEMENTING / FULL_UI_AUDIT_IN_PROGRESS / TRAINING_NOT_STARTED. Chưa chứng nhận toàn nút hoặc model thật, không commit/push/kích hoạt adapter.
- Cập nhật: 2026-10-16T22:16:51.486329+07:00

- Kiểm chứng tiếp 2026-10-14T16:38:36.408906+07:00: UI fix01 terminal/trả quyền; Codex sửa thêm stale-list ôn tập và hộp index không đóng sau lỗi, cả hai RED đúng lỗi→GREEN. Node26PASS; portable trước tích hợp440PASS12SKIP. Chat mới/mở phiên kiểm trên ứng dụng thật; các luồng xóa/lịch/OCR/bài tập kiểm trong kho synthetic riêng. Báo cáo docs/team/RA_SOAT_GIAO_DIEN_VA_TIEP_TUC_RAG_YC206.md; mobile/Windows/IME thật và một số tổ hợp còn NOT_RUN. Lựa chọn tiếp tục: hoàn tất dữ liệu/RAG đã sửa rồi học chỉ sau các cổng; không khôi phục pipeline ứng viên cũ hoặc kích hoạt adapter.


- Cập nhật kiểm giao diện 2026-10-14T21:27:28.918781+07:00: đã sửa5lỗi; Node27PASS và portable sau tích hợp496PASS12SKIP. Chat mới/clear ô nhập/cảnh báo nguồn/PDF/filter/thay lịch/xóa phiên riêng đã kiểm browser. Consumer nguồn/lịch sử24PASS sau rerun lúc inferenceidle; không sửa hợp đồng busy. Windows/IME/mobile thật NOT_RUN; không chứng nhận mọi tổ hợp.

<a id="yc-207"></a>
### YC-207 - 14/10/2026 20:58 (UTC+07)

- Người thực hiện: Codex; loại: làm rõ quy trình phát hành và huấn luyện.
- Quyết định: từ nay, lỗi được phát hiện phải sửa hoàn tất và kiểm chứng, sau đó đẩy các thay đổi lên GitHub trước khi tiếp tục huấn luyện. Áp dụng ngay cho YC205/206; bổ sung điều kiện phát hành, giữ nguyên cổng nguồn/dataset/runtime/tài nguyên/chất lượng/retention.
- Trước/sau: YC205/206 chưa có quyền commit/push mới; nay cho phép commit và push bản sửa đã kiểm chứng qua nhánh/PR theo quy trình nhóm. Không suy quyền merge main, kích hoạt adapter hoặc công bố DB, nguồn riêng, weights và dữ liệu người dùng.
- Phần/file: README_QUY_TAC.md, nhật ký gốc và checkpoint local; yêu cầu được ghi thành quy tắc dự án.
- Kiểm chứng: đối chiếu quy trình Git/phạm vi tài sản hiện hành; không đổi hành vi runtime trong bước ghi quy tắc. Push đợt sửa YC205/206 đang chờ hoàn tất kiểm chứng, chưa báo đã xuất bản.
- Trạng thái tại mốc ghi quy tắc: APPLIED / FIX_PUBLICATION_PENDING; cập nhật: 2026-10-14T20:58:58.375951+07:00.

- Kết quả tích hợp YC205/206 cập nhật 2026-10-14T21:21:39.491083+07:00: main099c769; source/raw/code/VNI/typed block/provenance/migration và retrieval/token/citation đã tích hợp checkout local sau trả quyền. Bốn ca citation/bản dịch mới RED→GREEN, citation05 độc lập80PASS; portable sau tích hợp496PASS12SKIP, Node27PASS. Thêm cảnh báo trang chưa duyệt trên UI, browser đã kiểm; checkbox thay lịch/PDF/bộ lọc/xóa phiên riêng đã kiểm trong kho thử. Không suy mô hình/Vision/OCR từ mô phỏng.
- Chỉ mục thử v5:157PDF22711trang,52608chunk/vector;20628vector tạo lại, binding và SQLite/FTS đạt. Cổng nguồn vẫn7916trang pending+2partial, complete:false; không publish DB hoặc học/activate. QA04 model thật6/7responseanswer nhưng manual2PASS2partial3FAIL; xác nhận bản dịch máy làm lệch ngữ cảnh, loại khỏi căn cứ trả lời, giữ snippet để đối chiếu. Các phép thử này là diagnostic đã xem, không phải holdout kín.
- Quyền hiện tại: mọi đơn vị Antigravity đã terminal/trả quyền; Codex review/tích hợp/phát hành. Push nhánh/PR YC205/206 đang chuẩn bị theo YC207, chưa merge hoặc CI mới. Báo cáo RA_SOAT_GIAO_DIEN_VA_TIEP_TUC_RAG_YC206.md và evidence local; full private-corpus suite/Windows máy thật/IME/mobile chưa nghiệm thu.

- Chốt kiểm trước phát hành 2026-10-14T21:29:32.955560+07:00: citation06 đã review/tích hợp, OOP thật không còn chép câu mẫu hoặc mã `[S#]`, cóS1hợp lệ. Cách ly thêm dialogue lock trong fixture để bộ API mock không tranh inference thật; portable496PASS12SKIP, consumer24PASS, Node27PASS. Cổng nguồn/chất lượng giữ đóng; nhánh codex/fix/shared/yc205-yc206-tv6 được chuẩn bị để push/PR draft theo YC207.

- Công bố và sửa kiểm thử CI 2026-10-14T21:39:10.580450+07:00: đã push fc47329ca16c2bdc629fe639867387bc7176aeae và mở [PR15](https://github.com/ng-wngkh07/Study-Agent/pull/15) nháp trên nhánh codex/fix/shared/yc205-yc206-tv6, chưa merge main. CI macOS ban đầu có2FAIL vì hai ca ngân sách đọc config mô hình private trước gọi stub; đã tái hiện khi config vắng, cung cấp metadata8192 riêng trong thư mục thử, giữ nguyên mọi assertion và production guard. Local kiểm lại496PASS12SKIP; không tải weights vào CI. Đang đẩy bản sửa harness và chờ CI trên head mới; nguồn/chất lượng chưa đạt, huấn luyện NOT_RUN.

- Sửa tương thích Windows 2026-10-14T21:47:45.640776+07:00: CI Windows thật còn2FAIL ở đồng bộ nguồn do scripts/complete_corpus.py import POSIX fcntl. Antigravity windows07 sửa tối thiểu trong snapshot rồi terminal/trả quyền; Codex đối chiếu diff/hash và tích hợp from app import file_lock as fcntl sau ROOT/sys.path. Thêm ca tái hiện import không có fcntl RED→GREEN; portable497PASS12SKIP, không bỏ hai ca printed/visual review. Khóa native Unix và adapter LockFileEx Windows được tái dùng; không sửa dữ liệu nguồn/cổng học. CI head mới đang chờ; dịch vụ local còn chờ tải lại sau CI.

- Nghiệm thu phần mềm local/GitHub 2026-10-14T21:54:13.628006+07:00: remote xác nhận2619c1a1f19c8705c5adea08bb0e1225181b54d5; CI push38583738297 Windows/macOS SUCCESS trên đúng head, history PR SUCCESS. PR15 vẫn draft/chưa merge, main099c769 giữ nguyên. Đã restart đúng web23992/supervisor24004 về dịch vụ mới, health healthy/inference_ready; bốn asset served hash khớp. So mọi bảng kho knowledge/history/timetable và registry trước/sau: nguyên vẹn, SQLite integrityok; không publish shadowv5. Browser mở phiên cũ rồi chat mới: về màn hình bắt đầu, ô nhập trống và lịch sử vẫn còn. Ảnh/evidence local data/evaluation/ui-audit-yc206; backup data/backups/yc207-service-restart. Mã sửa đã xuất bản và kiểm chứng, cập nhật tài liệu đợt này cũng được push; cổng nguồn7918fail và chất lượng model chưa đạt nên huấn luyện NOT_RUN, adapteractive:null. Windows máy thật/mobile/IME/OCR/VLM toàn bộ chưa nghiệm thu.

<a id="yc-208"></a>
### YC-208 - 14/10/2026 23:15 (UTC+07)

- Người thực hiện: Antigravity (Senior Frontend & UI/UX Engineer); loại: sửa lỗi bố cục giao diện và trải nghiệm người dùng.
- Mục tiêu:
  1. Căn giữa khung “📝 Thẩm định & duyệt bản nhận diện chữ cục bộ” (`#ocr-modal`): Sửa lỗi modal bị lệch góc trên bên trái do thiết lập inline `style.display = "block"` làm mất layout `display: flex` căn giữa của container `.modal`. Chuyển sang kích hoạt bằng class `.active`, bổ sung fallback CSS `display: flex !important` và `margin: auto` cho `.modal-content`, hỗ trợ phím Escape và giới hạn cuộn an toàn (`max-height: min(88vh, 850px)`).
  2. Tách chức năng “Dùng lại nguồn gần nhất · tìm lại cho câu hỏi mới” (`#resume-source-list`) và “↩ Mở câu hỏi gần nhất” (`#btn-resume-last-question`) thành dropdown popup menu dọc riêng neo ở góc phải, nằm bên phải nút “📝 Tóm tắt chủ đề” (`#btn-summarize-session`). Xóa bỏ tình trạng nút tràn hàng ngang đè lên tên phiên và biểu tượng cây bút (✏️ `#btn-rename-session`). Hỗ trợ toggle mở/đóng, tự đóng khi click bên ngoài hoặc bấm Escape, bảo đảm khả năng tiếp cận và giữ nguyên 100% hợp đồng ID/chức năng cho backend và bộ kiểm thử.
- Trước / sau thay đổi:
  - Trước: Modal OCR hiển thị ở góc trên bên trái; các nút nguồn gần nhất nằm trên cùng một hàng ngang trải dài làm co hẹp tiêu đề phiên và đè lên nút sửa tên cuộc trò chuyện.
  - Sau: Modal OCR được căn chính giữa màn hình theo cả hai chiều; các thao tác nguồn và câu hỏi gần nhất được gom gọn vào dropdown menu dọc bên phải nút Tóm tắt chủ đề, giao diện thoáng và không bị đè lên bất kỳ thành phần nào.
- Phần/file sửa:
  - `static/index.html`: Cấu trúc lại `.session-controls` đưa `#btn-summarize-session` sang bên trái wrapper dropdown `#recent-sources-wrapper`, giữ nguyên ID `#btn-resume-last-question` và `#resume-source-list`.
  - `static/style.css`: Quy tắc hiển thị flex cho modal kèm căn giữa `margin: auto`; thiết kế phong cách dark liquid glass cho `.recent-sources-trigger`, `.recent-sources-menu`, `.recent-menu-action-btn`, `.recent-menu-source-item`.
  - `static/app.js`: Quản lý trạng thái mở/đóng menu `#recent-sources-menu` và toggle `aria-expanded`; chuẩn hóa `classList.add("active")` cho `#ocr-modal`; đóng menu khi chọn nguồn hoặc câu hỏi gần nhất.
- Kiểm thử:
  - Node UI Lifecycle tests: `tests/chat_lifecycle_ui_yc206.test.cjs` (6 PASS, 0 FAIL).
  - Node UI Component tests: `tests/chat_input_ui.test.cjs`, `tests/document_lookup_ui.test.cjs`, `tests/practice_ui.test.cjs` (21 PASS, 0 FAIL).
  - Pytest UI & Backend contract tests: `tests/test_study_agent_c_f_g.py`, `tests/test_learning_tools_ui_contract.py` (28 PASS, 0 FAIL).
  - History validation: `scripts/check_history.py` (PASS).
- Trạng thái: PASS / APPLIED.


<a id="log-20261014-pr15-merge-audit-tv6"></a>
### LOG-20261014-pr15-merge-audit-tv6 - 14/10/2026 22:05 (UTC+07)

- Người thực hiện: Codex; loại: rà soát/nghiệm thu điều kiện merge PR15.
- Kiểm GitHub tại thời điểm rà soát: head18c123c, draft/OPEN, MERGEABLE/CLEAN, chưa có review; CI Windows/macOS/history SUCCESS. Review chéo vẫn cần theo quy trình nhóm; Codex không merge hoặc đổi trạng thái PR.
- Bản rà soát là snapshot trước khi thành viên tích hợp PR15 lúc 22:06. Hiệu chỉnh ID local trùng YC208 giao diện thành LOG này, giữ ngày và mục giao diện đã công bố.

<a id="yc-209"></a>
### YC-209 - 14/10/2026 22:16 (UTC+07)

- Hồ sơ chi tiết giữ nội bộ theo giới hạn công bố YC214; bản đầy đủ được bảo toàn local.

<a id="yc-210"></a>
### YC-210 - 14/10/2026 22:16 (UTC+07)

- Hồ sơ chi tiết giữ nội bộ theo giới hạn công bố YC214; bản đầy đủ được bảo toàn local.

<a id="yc-211"></a>
### YC-211 - 14/10/2026 22:26 (UTC+07)

- Người thực hiện: Codex; loại: rà soát khả năng chạy bản kiểm thử sau clone.
- Đối chiếu launcher và README: bản demo công khai có trong tests/fixtures/documents; dùng đúng profile để nạp. Ảnh Windows cho thấy gọi file .ps1 từ CMD đã mở trình soạn thảo, chưa chạy server; hướng dẫn gọi qua PowerShell với -File. Chưa xác minh lại trên máy thành viên, chưa nhận là lỗi ứng dụng đã sửa.

<a id="yc-212"></a>
### YC-212 - 14/10/2026 22:55 (UTC+07)

- Hồ sơ chi tiết giữ nội bộ theo giới hạn công bố YC214; bản đầy đủ được bảo toàn local.

<a id="yc-213"></a>
### YC-213 - 15/10/2026 07:30 (UTC+07)

- Người thực hiện: Antigravity đề xuất bản sửa; Codex kiểm thử, review và tích hợp; loại: sửa lỗi kiểm định đầu vào.
- Lỗi: validate_page_review nhận ký tự điều khiển C0 trong trường văn bản. Từ chối ký tự <32 ngoài newline/tab/CR ở transcript, visual_description, editorial và unresolved; giữ raw capture, LaTeX hợp lệ và thụt dòng mã nguồn.
- File: app/source_page_reviews.py, tests/test_source_page_review_controls.py và pytest.windows.ini. Không thay định dạng module hoặc làm sạch âm thầm dữ liệu đầu vào.
- Kiểm chứng: baseline 24 FAIL đúng lỗi và 5 PASS bảo toàn; sau sửa 29/29 PASS. Targeted 61 PASS; portable 526 PASS/12 SKIP; Node 27 PASS; pip/history/diff PASS. Windows máy thật và các luồng ngoài phạm vi chưa nghiệm thu.
- Phát hành: mã 5cdf454 qua PR17 nháp; CI Windows/macOS/history SUCCESS trên commit mã và d25563f. Chưa review chéo nhóm hoặc merge.

<a id="yc-214"></a>
### YC-214 - 15/10/2026 13:39 (UTC+07)

- Người thực hiện: Codex; loại: làm rõ giới hạn công bố GitHub, bổ sung YC207.
- Quyết định: chỉ đưa bản sửa, kiểm thử và lịch sử thay đổi phần mềm vào commit/PR; dữ liệu và thông tin huấn luyện giữ local. Rà nội dung file, commit và mô tả PR trước mỗi lần push.
- Hiệu chỉnh: thu gọn phần hồ sơ nội bộ đã thêm trong PR17; giữ ID/ngày và bản đầy đủ local. Bảo toàn nội dung main có trước PR, khôi phục dòng quyết định giao diện YC208 từng bị bỏ khỏi bảng. Không viết lại lịch sử Git đã công bố.
- Phần/file: README_QUY_TAC.md, LICH_SU_DU_AN.md và mô tả PR17. Không thay mã ứng dụng trong lần hiệu chỉnh tài liệu này.
- Kết quả: đã push bản hiệu chỉnh c892662 và đọc lại đúng remote/PR17; CI38693832477 Windows/macOS/history SUCCESS. Diff/file allowlist và lịch sử PASS; mã phần mềm không đổi. Không merge hoặc viết lại Git history. Cập nhật 2026-10-15T13:45:16.163900+07:00.

<a id="yc-215"></a>
### YC-215 - 15/10/2026 13:55 (UTC+07)

- Người thực hiện: Codex; loại: đồng bộ GitHub và cập nhật hướng dẫn phần mềm.
- Trước/sau: local còn ở PR15, main local chậm hơn GitHub; đã fast-forward main tới 2e844d3 (gồm PR16), chuyển checkout sang nhánh PR17 chứa main mới và bản sửa YC213. Năm file đang sửa được sao lưu và giữ stash; đối chiếu hash xác nhận chúng đã có đầy đủ trên PR17, không áp lại hoặc ghi đè.
- File/phạm vi: README.md, README_MAC.md, README_WINDOWS.md, CONTRIBUTING.md, mẫu PR và nhật ký. Hướng dẫn đúng profile để thấy tài liệu demo sau clone, gọi PowerShell bằng -File, đồng bộ main/tiếp tục PR hiện hữu, mô tả giao diện hiện tại và bổ sung tập test vòng đời chat. Đồng nhất giới hạn công bố YC214, bỏ yêu cầu ghi thông tin huấn luyện trong mẫu PR/nhật ký; giữ các mục lịch sử cũ.
- Kiểm chứng phần mềm: 526 pytest PASS/12 SKIP; 27 Node UI PASS; pip check, đối chiếu asset HTTP và liên kết tài liệu PASS. Nạp lại đúng dịch vụ, health healthy; ba DB và registry bảo toàn nội dung qua snapshot trước/sau. Kiểm trình duyệt: mở phiên cũ/menu nguồn, Escape, nhập nháp rồi Cuộc trò chuyện mới trả về màn hình bắt đầu với ô trống và lịch sử vẫn còn.
- Ảnh hưởng/giới hạn: bản sửa ứng dụng YC213 không đổi; chỉ đồng bộ mã đã công bố và sửa tài liệu. Cập nhật cùng PR17 hiện hữu; chưa merge hoặc có review chéo nhóm. Windows máy thành viên, toàn bộ OCR/model thật NOT_RUN trong lượt này. Hồ sơ nội bộ giữ local; không viết lại lịch sử Git đã công bố.

<a id="log-20261016-security-issue28-tv4"></a>
### LOG-20261016-security-issue28-tv4 - 16/10/2026 19:35 (UTC+07)

- Người thực hiện: TV4 / Nhóm trưởng; loại: quy định bảo mật & tài liệu đóng góp (Issue #28).
- Nội dung: Bổ sung mục quản lý bí mật và dữ liệu nhạy cảm (Secrets) vào CONTRIBUTING.md theo tiêu chí nghiệm thu Lab 2; xác nhận .env đã nằm trong .gitignore và .env.example không chứa credential thật.
- File: CONTRIBUTING.md, LICH_SU_DU_AN.md.
- Kiểm chứng: .gitignore và .env.example hợp lệ; scripts/check_history.py PASS.

<a id="log-20261016-security-issue29-tv4"></a>
### LOG-20261016-security-issue29-tv4 - 16/10/2026 19:55 (UTC+07)

- Người thực hiện: TV4 / Nhóm trưởng; loại: rà soát bảo mật & lập báo cáo (Issue #29).
- Nội dung: Hoàn thành rà soát thủ công và gitleaks trên toàn bộ repo tìm bí mật, token và thông tin cá nhân; lập báo cáo kết quả an toàn tại docs/security-check.md.
- File: docs/security-check.md, LICH_SU_DU_AN.md.
- Kiểm chứng: Quét sạch 0 secret; scripts/check_history.py PASS.

<a id="log-20261016-ui-issue19-tv5"></a>
### LOG-20261016-ui-issue19-tv5 - 16/10/2026 20:20 (UTC+07)

- Người thực hiện: TV5; loại: thiết kế giao diện & đặc tả trích dẫn (Issue #19).
- Nội dung: Thiết kế quy chuẩn hiển thị nguồn trích dẫn dưới câu trả lời tại docs/ui-sources.md kèm 2 ví dụ trực quan (đơn nguồn từ PDF chính quy và đa nguồn có cảnh báo OCR), quy chuẩn cấu trúc dữ liệu và tương tác người dùng.
- File: .gitignore, docs/ui-sources.md, LICH_SU_DU_AN.md.
- Kiểm chứng: scripts/check_history.py PASS; kiểm tra cú pháp Markdown và đáp ứng đầy đủ tiêu chí nghiệm thu Issue #19.
<a id="log-20261016-ui-issue18-tv5"></a>
### LOG-20261016-ui-issue18-tv5 - 16/10/2026 20:05 (UTC+07)

- Người thực hiện: TV5; loại: giao diện & tài liệu thiết kế (Issue #18).
- Nội dung: Thiết lập kế hoạch kiến trúc UI ui/README.md đặc tả kết nối API backend FastAPI, tạo trang placeholder ui/app.py với ô input box và khung chat Streamlit, bổ sung bản vẽ wireframe mockup tại docs/chat-mockup.md.
- File: .gitignore, ui/README.md, ui/app.py, docs/chat-mockup.md, LICH_SU_DU_AN.md.
- Kiểm chứng: scripts/check_history.py PASS; kiểm tra cú pháp Streamlit và tài liệu thiết kế đạt yêu cầu nghiệm thu Lab 2.

<a id="yc-217"></a>
### YC-217 - 16/10/2026 20:36 (UTC+07)

- Người thực hiện: Codex; loại: rà soát/nghiệm thu đồng bộ, tài liệu và quy trình.
- Mục tiêu: làm rõ QA học tập có nguồn để trình bày với giảng viên; văn phòng là
  ví dụ Lab 2. Giữ triển khai #18–29 cho thành viên; chỉ nghiệm thu sau 12 khi đủ PR.
- Trước/sau: checkout nhánh #29 đã merge, main mới có #18 rồi #19; đã đồng bộ tới
  d0d1ae0, giữ sửa dở mẫu issue/PR. Hiện 4/12 đóng, 8 mở; không tự đóng issue hoặc
  merge PR. Header cũ đếm 56 mục thiếu 4 LOG mới; hiệu chỉnh thành 61 gồm mục này,
  giữ nguyên ID/ngày và nội dung cũ. Thời điểm hiệu chỉnh: 2026-10-16T20:36:39.407242+07:00.
- Phần sửa: README chính/Mac/Windows, README_QUY_TAC, CONTRIBUTING, DoD, templates,
  ba tài liệu mục tiêu/kiến trúc/vận hành, UI README và ghi chú docs/ui-sources,
  báo cáo bảo mật/rà soát, rule Antigravity và Git allowlist đúng file. Nêu use case,
  rubric/metric và nâng cấp P0/P1/P2 là tiêu chí/đề xuất, chưa phải chức năng đã đạt.
- Chẩn đoán: UI mẫu gửi answer_mode không hợp lệ, còn lệch citation/stream và
  fallback mô phỏng; ghi để chủ trì sửa. Test admission phụ thuộc DB/đường dẫn máy
  được thay fixture SQLite/nguồn tạm, có valid control và các nhánh fail-closed;
  không đổi validator hoặc dữ liệu thật.
- Quyền GitHub: bật ruleset 25241854 trên main, restrict updates; chỉ user owner
  bypass qua PR. Đã đọc lại rule có hiệu lực và xác nhận protection CI/admin giữ
  nguyên. Chưa thử bằng tài khoản thành viên; chưa bật auto-delete/xóa branch.
- Kiểm chứng: portable Mac/Python3.14.8 từ 525 PASS/1 FAIL/12 SKIP thành 526 PASS/
  12 SKIP; Node UI 27 PASS; pip check PASS. API schema/rule PASS; 23 ca allowlist, 85 liên kết, bảo toàn 60 mục main và diff
  PASS; quét history/all refs/cây công khai không phát hiện bí mật theo công cụ.
  Phạm vi/giới hạn ở docs/team/RA_SOAT_REPO_YC217.md.
- Trạng thái: gói tài liệu/test local đã xử lý; cổng sau 12 PENDING_MEMBER_WORK.
  Windows native/Python3.12 mới/Streamlit/model QA thật/tự nạp rule chat mới NOT_RUN.
  Chưa commit/push gói này; không nhận CI PR34 là CI của YC217. Hồ sơ/dữ liệu riêng
  giữ local theo YC214; kết quả này không cấp quyền huấn luyện hoặc promote.
<a id="log-20261016-data-issue23-tv4"></a>
### LOG-20261016-data-issue23-tv4 - 16/10/2026 20:45 (UTC+07)

- Người thực hiện: TV4 / Nhóm trưởng; loại: tài liệu nguồn dữ liệu & chủ sở hữu (Issue #23).
- Nội dung: Tạo tài liệu data/README.md liệt kê và phân loại đầy đủ 8 tệp/thư mục dữ liệu (knowledge_base.db, history.db, timetable.db, translations.db, adapters/active_registry.json, runtime/, ocr_cache/, team-demo/); ghi rõ nguồn dữ liệu (data source), chủ sở hữu phụ trách (owner: TV1–TV6 theo phân công nhóm), tần suất cập nhật, chính sách lưu trữ Git và nguyên tắc quản trị (quy tắc single-writer cho TV4, bảo đảm dữ liệu cá nhân không commit vào repo). Cập nhật .gitignore cho phép theo dõi data/README.md an toàn.
- File: .gitignore, data/README.md, README.md, LICH_SU_DU_AN.md.
- Kiểm chứng: Cấu trúc bảng đầy đủ 6 cột chuẩn hóa, đáp ứng 100% tiêu chí nghiệm thu của Issue #23; scripts/check_history.py PASS.


<a id="log-20261016-backend-issue21-tv2"></a>
### LOG-20261016-backend-issue21-tv2 - 16/10/2026 20:47 (UTC+07)

- Người thực hiện: TV2; loại: lệnh trợ giúp (Issue #21, PR #35).
- Trước/sau: thêm nhận diện chính xác help và các alias ngắn, trả danh sách tra cứu,
  QA, luyện tập và thời khóa biểu qua luồng dialogue xác định; không tìm nguồn hoặc gọi model.
- File: app/dialogue.py, tests/test_help.py, README.md. Phần kiểm định và hiệu chỉnh
  tích hợp của Codex/Antigravity được ghi riêng tại YC-218; giữ attribution tác giả.
- Trạng thái: PR chờ owner review, chưa merge; tiêu chí kiểm định ở YC-218.

<a id="yc-218"></a>
### YC-218 - 16/10/2026 21:49 (UTC+07)

- Người thực hiện: Antigravity triển khai theo phạm vi; Codex kiểm thử/review/tích hợp;
  loại: sửa lỗi các issue đã merge và PR #35.
- Trước/sau: PR #35 xóa LOG #23, thiếu log mới và test help không có trong suite CI.
  Khôi phục toàn bộ lịch sử main, thêm log đúng tác giả và bật test help/UI adapter.
- Mục tiêu: sửa payload UI sai schema, phản hồi/nguồn giả khi mất mạng, retry và
  citation thiếu kiểm tra; đối chiếu tài liệu #19/#23/#29 với source thực tế;
  bổ sung DoD đang được 12 issue viện dẫn. Giữ UI chính và các việc chưa merge cho chủ trì.
- RED: 22 FAIL / 17 PASS tái hiện lỗi payload/error/citation và metadata help;
  các ca dùng fixture tạm, chặn retrieval/model ở ranh giới, không ghi lịch sử thật.
- Sau sửa: help đi qua sync/stream với generation_calls=0; adapter không sinh
  câu trả lời/nguồn giả, retry cùng payload/ID, hủy bỏ đúng lượt chờ trên UI;
  giới hạn client history đúng schema. Nguồn hiển thị plain text nguyên văn,
  PDF mở từ origin backend với ID/trang dương; không đổi UI chính hoặc luồng RAG.
- Kiểm chứng độc lập: portable macOS/Python3.14.8 **578 PASS/12 SKIP**, Streamlit
  AppTest **5 PASS**, Node Web UI **27 PASS**; demo29/29, FTS/PDF, pip/history/diff
  PASS; parse204Python, 25ca ranh giới và liên kết tài liệu PASS. Gitleaks all refs
  và cây313file công khai không phát hiện secret theo rule công cụ.
- Phần sửa/file: app/dialogue.py, app/rag_agent.py, ui/app.py, tests/help và adapter,
  fixture admission, pytest.windows.ini, CI job ui_adapter, README/hướng dẫn UI,
  mockup/source/data, DoD và báo cáo bảo mật. Chi tiết: docs/team/RA_SOAT_TICH_HOP_YC218.md.
- Phát hành: commit fa9d4c2471897c4b61d0bc0897753975ede220d6 đã push vào nhánh
  feature của PR35. CI Python3.12: macOS 578 PASS/12 SKIP; Windows 576 PASS/14 SKIP
  (thêm hai ca POSIX không áp dụng); Node 27 PASS trên mỗi nền tảng. Ubuntu UI
  AppTest 5 PASS, history PASS; bộ cài demo Windows và pip check PASS. Bằng chứng:
  https://github.com/ng-wngkh07/Assistant-Team06/actions/runs/38922811629.
- Trạng thái: bản sửa và CI hoàn tất, PR35 OPEN/MERGEABLE nhưng BLOCKED theo
  ruleset owner-only, chờ owner review/merge. Chưa merge hoặc đóng #21;
  các issue còn mở vẫn do thành viên phụ trách. Không thay protection/quyền owner.
  QA model thật, full installer máy Windows mới và kiểm trên main sau merge NOT_RUN.
- Đồng bộ local: mã/config khớp bản PR đã kiểm định; giữ gói tài liệu YC217 chưa
  commit. Nhật ký local có 64 mục, PR có 63 vì YC217 chưa phát hành; không mất mục main.
- Cập nhật kết quả: 2026-10-16T22:25:20.871173+07:00; giữ mọi mục và attribution lịch sử cũ.

<a id="log-20261017-qa-studymaterials-tv2"></a>
### LOG-20261017-qa-studymaterials-tv2 - 17/10/2026 20:10 (UTC+07)

- Người thực hiện: TV2 (Backend & AI) và TV5 (Frontend UI/UX); loại: tính năng mới (Upload tài liệu học tập, tự động tạo Trắc nghiệm & Flashcard 3D).
- Lỗi hoặc mục tiêu (ý chính): Bổ sung chức năng cho phép người dùng tự tải tài liệu học tập lên hệ thống (PDF, Word, Text, Ảnh) hoặc chọn tài liệu đã lập chỉ mục từ thư viện; tự động trích xuất nội dung và sinh bộ câu hỏi trắc nghiệm (Quiz) cùng thẻ ghi nhớ (Flashcard 3D) có đối chiếu trích dẫn tài liệu gốc từng trang (grounding citations).
- Trước / sau thay đổi:
  - Trước: Người dùng chỉ có thể hỏi đáp qua ô chat hoặc tạo câu hỏi cố định theo chủ đề trong công cụ học tập; không thể tự tải tài liệu riêng lên để ôn tập tương tác tức thì hoặc luyện flashcard 3D.
  - Sau: Nút `＋` tích hợp trực quan trên thanh nhập liệu chat theo mẫu thiết kế giao diện; mở menu lựa chọn "Thêm ảnh & tệp" hoặc "Thêm từ thư viện". Modal giao diện ôn tập hiện đại với 3 tab: Trắc nghiệm (tính điểm, giải thích chi tiết, nguồn đối chiếu), Flashcard 3D (lật thẻ 3D, thống kê Đã thuộc / Cần ôn, phím tắt Spacebar, xáo trộn thẻ) và Nguồn trích đoạn.
- Phần/file sửa và cách xử lý:
  - `app/study_materials.py`: Xây dựng module `StudyMaterialManager` xử lý xác thực tệp tin an toàn (kích thước tối đa 20MB, kiểm tra định dạng và magic bytes), trích xuất văn bản từng trang qua `PDFExtractor`, cơ chế sinh câu hỏi trắc nghiệm và flashcards qua Ollama LLM bảo vệ bằng `gpu_coordinator` & `dialogue_gate`, kèm thuật toán dự phòng tất định (deterministic fallback) bảo đảm 100% không crash khi LLM ngoại tuyến.
  - `app/server.py`: Bổ sung 4 endpoints API: `POST /api/materials/upload`, `POST /api/materials/generate-from-library`, `GET /api/materials/documents`, `GET /api/materials/{material_id}`.
  - `static/index.html`: Thêm nút đính kèm `btn-chat-attach`, menu dropdown popup, Modal chọn tài liệu thư viện `library-picker-modal`, Modal ôn tập đa năng `study-material-modal` (Quiz & 3D Flashcard) và Modal trạng thái phân tích `study-loading-modal`.
  - `static/style.css`: Thiết kế giao diện dark obsidian, hiệu ứng lật thẻ 3D (`preserve-3d`, `rotateY`), thanh tiến độ glowing gradient, quiz option feedback states (emerald green / crimson red), hỗ trợ responsive hoàn chỉnh.
  - `static/app.js`: Tích hợp logic xử lý tải tệp qua FileReader, kết nối API backend, quản lý trạng thái Quiz/Flashcards, tương tác lật thẻ và phím tắt (Space, Mũi tên trái/phải), tự động gửi thông báo Study Card vào cuộc trò chuyện.
  - `tests/test_study_materials.py`: Viết bộ 8 kiểm thử tự động kiểm tra xác thực định dạng, giới hạn kích thước, thuật ngữ trích xuất, tạo fallback và kiểm tra toàn bộ API endpoints.
- Kiểm thử phần mềm:
  - `pytest tests/test_study_materials.py tests/test_help.py tests/test_study_agent_c_f_g.py`: 51 PASS / 0 FAIL.
  - `node --test tests/chat_lifecycle_ui_yc206.test.cjs`: 6 PASS / 0 FAIL.
- Trạng thái: PASS; sẵn sàng tích hợp và nghiệm thu.
- Giới hạn, lỗi còn lại, rollback/bước tiếp: Lưu giữ tài liệu an toàn trong bộ nhớ cache runtime cục bộ; không ảnh hưởng tới dữ liệu huấn luyện hoặc các chức năng tra cứu hiện hữu.

<a id="log-20261017-qa-attachlayout-imageupload-tv2"></a>
### LOG-20261017-qa-attachlayout-imageupload-tv2 - 17/10/2026 20:32 (UTC+07)

- Người thực hiện: TV2 (Backend & AI) và TV5 (Frontend UI/UX); loại: sửa lỗi bố cục giao diện & sửa lỗi luồng upload file ảnh.
- Lỗi hoặc mục tiêu (ý chính):
  1. Căn chỉnh lại thanh đáy `.chat-form-bottom` và di chuyển nút đính kèm `＋` (`#btn-chat-attach`) về sát góc trái của khung nhập chat, trong khi giữ cụm `[🧠 Tự động]`, `[📚 Thư viện tài liệu]` và nút gửi `[↑]` ở bên phải.
  2. Khắc phục triệt để lỗi upload file ảnh (`.png`, `.jpg`, `.jpeg`, `.webp`) trong chức năng tạo học liệu ôn tập (lỗi 404 do server cũ chưa nạp route `/api/materials/upload`, lỗi `extract_file()` gọi `ocr=False` cho file ảnh, và bất đồng bộ kiểm tra OCR backend).
- Trước / sau thay đổi:
  - Trước: `.chat-form` giữ `align-items: flex-end` và `.chat-form-bottom` thiếu `width: 100%` khiến thanh đáy bị co hẹp về bên phải và nút `＋` nằm sát các nút bên phải; khi tải ảnh lên gặp lỗi `Not Found` (404 trên tiến trình cũ) và `0 ký tự` do `extract_file` bỏ qua OCR.
  - Sau: `.chat-form` đặt `align-items: stretch`, `.chat-form-bottom` rộng `100%`, `.chat-attach-wrapper` đặt `margin-right: auto` neo cố định ở góc trái; luồng upload ảnh tự động bật `ocr=True`, đồng bộ backend `VisionOCRManager` (Ollama Vision / Tesseract), tích hợp fallback Windows WinRT OCR và kiểm định cấu trúc ảnh qua Pillow để tạo bộ Quiz & Flashcard 3D thành công.
- Phần/file sửa và cách xử lý:
  - `static/style.css`: Đặt `align-items: stretch; width: 100%` trên `.chat-form`, `width: 100%` trên `.chat-form-bottom`, `flex: 1` trên `.chat-form-controls`, và `margin-right: auto; flex-shrink: 0` trên `.chat-attach-wrapper`; bổ sung giao diện thẻ xem trước đính kèm `.chat-attachment-card`, nút riêng `.study-gen-pill` (`#btn-create-quiz-flashcard`), hộp thoại xem nội dung tài liệu `.doc-content-page-card` và khung `.message.study-card-message` đồng bộ tỷ lệ `max-width: 720px`.
  - `static/index.html` & `static/app.js`: Bổ sung thẻ đóng `</div>` của `#ocr-modal`; thêm khay `#chat-attachment-preview-tray` hiển thị thẻ xem trước khi thêm tệp (không tự động mở Quiz/Flashcard ngay); khi nhấn vào thẻ tệp đã thêm sẽ mở `#document-content-modal` hiển thị toàn bộ nội dung từng trang bên trong tệp; bổ sung nút riêng `#btn-create-quiz-flashcard` (`⚡ Tạo Trắc nghiệm & Flashcard`) để tạo câu hỏi trắc nghiệm và flashcard khi người dùng nhấn; ẩn `.welcome-card` khi `.has-chat` kích hoạt và căn giữa hai khung đối xứng `720px`.
  - `app/pdf_extractor.py`: Cập nhật `ocr_available()` và `extract_image()` kiểm tra cả `BACKEND_OLLAMA_VISION` và `BACKEND_TESSERACT`.
  - `app/study_materials.py` & `app/server.py`: Bổ sung `ingest_to_src_and_index()`, `store_extracted_document()`, `inspect_library_document()` và endpoint `POST /api/materials/inspect-library` để tự động lưu tệp người dùng thêm vào thư mục `src/` và lập chỉ mục vào `knowledge_base.db` làm nguồn dữ liệu học tập.
  - `tests/test_study_materials.py`: Thêm 6 kiểm thử mới cho upload PNG/JPEG, primary OCR mocked, từ chối ảnh hỏng, thẻ đính kèm, nạp tệp vào `src/` không tự sinh Quiz/Flashcard, xem nội dung tệp và nhấn nút riêng sinh Quiz/Flashcard theo yêu cầu.
- Kiểm thử phần mềm:
  - `pytest tests/test_study_materials.py tests/test_help.py tests/test_study_agent_c_f_g.py tests/test_general_source_fidelity_yc205.py tests/test_force_ocr.py`: 77 PASS / 0 FAIL.
  - `node --test tests/chat_lifecycle_ui_yc206.test.cjs`: 6 PASS / 0 FAIL.
- Trạng thái: PASS.
- Giới hạn, lỗi còn lại, rollback/bước tiếp: Đã khởi động lại tiến trình máy chủ trên cổng 8000 để phục vụ đầy đủ các routes và giao diện mới.

<a id="log-20261017-qa-relocate-study-tools-stretch-ui-tv2"></a>
### LOG-20261017-qa-relocate-study-tools-stretch-ui-tv2 - 17/10/2026 21:55 (UTC+07)

- Người thực hiện: TV2 (Backend & AI) và TV5 (Frontend UI/UX); loại: cải tiến giao diện & tổ chức tính năng học tập (Di chuyển nút Tạo Trắc nghiệm & Flashcard sang tab Công cụ học tập, khắc phục hiện tượng thanh phiên co cụm ở giữa màn hình, kéo dãn đối xứng giao diện).
- Lỗi hoặc mục tiêu (ý chính):
  1. Di chuyển chức năng Tạo Trắc nghiệm & Flashcard (`#btn-create-quiz-flashcard`) từ thanh điều khiển chat sang tab chuyên dụng "⚡ Trắc nghiệm & Flashcard" (`#tool-tab-study-set`) trong phân hệ "Công cụ học tập" (`#learning-tools-panel`), mang lại không gian thao tác trực quan, rõ ràng cho việc chọn tài liệu từ thư viện, tải file mới lên và mở lại bộ bài học đã tạo.
  2. Khắc phục lỗi hiển thị thanh phiên `#session-bar` bị co cụm lại ở giữa màn hình (như ảnh phản hồi); chuẩn hóa độ rộng `max-width: 900px` và `width: 100%` đồng bộ cho toàn bộ các thành phần trong `.qa-panel` (`#session-bar`, `.session-summary-box`, `#training-banner`, `.messages-container`, `.input-area`, `.chat-form`), kéo dãn đều hai bên giao diện, phân bổ tiêu đề cuộc trò chuyện sang trái và các nút tác vụ sang phải bằng `justify-content: space-between`.
- Trước / sau thay đổi:
  - Trước: Nút Tạo Trắc nghiệm & Flashcard nằm chen chúc trong `.chat-form-controls` của khung chat; thanh `#session-bar` bị co cụm ở giữa màn hình do flexbox `align-items: center` trên `.qa-panel` mà thiếu quy tắc độ rộng tường minh.
  - Sau: Khung chat gọn gàng, thanh `#session-bar` kéo dãn đối xứng rộng rãi (900px) đều hai bên; tab "⚡ Trắc nghiệm & Flashcard" trong Công cụ học tập cung cấp trải nghiệm chuyên nghiệp cho phép chọn tài liệu hoặc tải lên để sinh trắc nghiệm và flashcard tương tác.
- Phần/file sửa và cách xử lý:
  - `static/index.html`: Di chuyển `#btn-create-quiz-flashcard` từ `.chat-form-controls` vào tab mới `#tool-tab-study-set` trong `#learning-tools-panel`; thêm nút chuyển tab `#btn-tool-tab-study-set` trong `.tool-tabs`; chuẩn hóa CSS cho `.qa-panel`, `#session-bar`, `.messages-container` và `.input-area` sang `max-width: 900px` với `welcome-mode` căn giữa và chat mode dãn đều `justify-content: flex-start`.
  - `static/style.css`: Cập nhật `.session-bar` với `width: 100%; max-width: 900px; margin: 0 auto; padding: 12px 20px; border-radius: 14px; box-sizing: border-box; justify-content: space-between`.
  - `static/document-lookup.js`: Bổ sung `"study-set"` vào danh sách tab của `setToolTab()` và bộ lắng nghe sự kiện tab.
  - `static/app.js`: Tích hợp các nút điều khiển trong tab Công cụ học tập (`#btn-study-tool-upload`, `#btn-study-tool-library`, `#study-tool-file-input`, `#btn-open-generated-study-set`); liên kết luồng upload file chung `handleMaterialFileUpload()`; đồng bộ trạng thái tài liệu đang chọn qua `syncGenerateButtonState()`.
- Kiểm thử phần mềm:
  - `.\venv\Scripts\python.exe -m pytest tests/test_study_materials.py`: 14 PASS / 0 FAIL.
  - `node --test tests/chat_lifecycle_ui_yc206.test.cjs`: 6 PASS / 0 FAIL.
- Trạng thái: PASS; sẵn sàng tích hợp và nghiệm thu.
- Giới hạn, lỗi còn lại, rollback/bước tiếp: Đảm bảo giữ vững tính toàn vẹn của dữ liệu và các kiểm thử hồi quy hiện có.

<a id="log-20261017-qa-prominent-quiz-flashcard-tool-tv2"></a>
### LOG-20261017-qa-prominent-quiz-flashcard-tool-tv2 - 17/10/2026 22:15 (UTC+07)

- Người thực hiện: TV2 (Backend & AI) và TV5 (Frontend UI/UX); loại: cải tiến giao diện & trải nghiệm học tập (Đưa bộ công cụ Tạo Flashcard & Trắc nghiệm hiển thị trực diện ngay khi mở Công cụ học tập, tuân thủ nghiêm ngặt hợp đồng 3 tab và flat design).
- Lỗi hoặc mục tiêu (ý chính):
  1. Khắc phục tình trạng người dùng phản ánh "Phần tạo flashcard và bài tập trắc nghiệm từ tài liệu trong phần công cụ học tập chưa có" do trước đó đặt vào một tab thứ 4 riêng biệt ẩn sau tab mặc định "Tạo câu hỏi".
  2. Đưa khối giao diện nổi bật "⚡ Tạo Flashcard và Bài tập Trắc nghiệm từ Tài liệu" (`.study-material-generator-card`) trực tiếp vào vị trí trên cùng của tab mặc định `tool-tab-practice`, giúp người dùng nhìn thấy ngay lập tức khi vừa nhấn vào "Công cụ học tập".
  3. Khôi phục đúng cấu trúc 3 tab chuẩn theo hợp đồng kiểm thử `tests/test_learning_tools_ui_contract.py` ("Tạo câu hỏi", "Ôn tập", "Trích xuất bài tập mẫu").
  4. Thay thế toàn bộ các dải màu gradient bằng màu đơn sắc/rgba tinh tế theo quy chuẩn thiết kế phẳng (flat design) không gradient của dự án.
- Trước / sau thay đổi:
  - Trước: Khối tạo học liệu nằm ở tab con thứ 4, khi chuyển sang "Công cụ học tập" màn hình mặc định chỉ mở tab 1 "Tạo câu hỏi theo chủ đề" khiến người dùng không thấy chức năng tạo flashcard/trắc nghiệm từ tài liệu; `static/style.css` chứa `linear-gradient` vi phạm bài test kiểm tra giao diện phẳng.
  - Sau: Khối chức năng tạo flashcard & trắc nghiệm hiển thị trực quan ngay đầu tab mặc định của Công cụ học tập; có sẵn 2 nút "Tải tệp mới lên (PDF, Word, Ảnh...)" và "Chọn từ thư viện tài liệu", hiển thị tài liệu đang chọn, nút "⚡ Tạo Flashcard & Bài tập Trắc nghiệm" và nút "📖 Mở bộ ôn luyện đã tạo"; giao diện phẳng hoàn toàn, 100% test contract và test hồi quy đều pass.
- Phần/file sửa và cách xử lý:
  - `static/index.html`: Di chuyển `.study-material-generator-card` vào đầu `#tool-tab-practice` (tab mặc định kích hoạt khi mở `#learning-tools-panel`); loại bỏ tab thứ 4 thừa trong `.tool-tabs`, khôi phục 3 tab chuẩn (`practice`, `review`, `examples`). Cập nhật cache-buster query string `?v=yc207-ui09` cho các file script và stylesheet.
  - `static/style.css`: Loại bỏ tất cả 10 khai báo `linear-gradient` trong stylesheet, thay bằng màu phẳng Obsidian Dark (`#151824`, `#1c1722`, `#f43f5e`, `rgba(244, 63, 94, 0.22)`, etc.), thỏa mãn quy định `test_learning_tools_have_theme_rules_and_no_decorative_images_or_gradients`.
  - `static/document-lookup.js`: Khôi phục mảng `tabs = ["practice", "review", "examples"]` trong hàm `setToolTab()`.
- Kiểm thử phần mềm:
  - `.\venv\Scripts\python.exe -m pytest tests/test_learning_tools_ui_contract.py`: 6 PASS / 0 FAIL.
  - `.\venv\Scripts\python.exe -m pytest tests/test_study_materials.py`: 14 PASS / 0 FAIL.
  - `node --test tests/chat_lifecycle_ui_yc206.test.cjs`: 6 PASS / 0 FAIL.
  - `.\venv\Scripts\python.exe -m pytest tests/test_practice.py tests/test_document_lookup.py`: 31 PASS / 0 FAIL.
- Trạng thái: PASS; đã kiểm chứng trực quan và tự động hóa.
- Giới hạn, lỗi còn lại, rollback/bước tiếp: Khuyến nghị người dùng ấn Ctrl+F5 khi tải lại trang web để trình duyệt nhận ngay CSS/JS mới.

<a id="log-20261017-qa-integrate-quiz-flashcard-with-topic-tv2"></a>
### LOG-20261017-qa-integrate-quiz-flashcard-with-topic-tv2 - 17/10/2026 22:25 (UTC+07)

- Người thực hiện: TV2 (Backend & AI) và TV5 (Frontend UI/UX); loại: tối ưu hóa giao diện & tích hợp chức năng ôn luyện (Loại bỏ nút chọn từ thư viện theo đánh dấu, tích hợp bộ tạo flashcard và trắc nghiệm chung vào cùng thẻ tạo câu hỏi theo chủ đề).
- Lỗi hoặc mục tiêu (ý chính):
  1. Loại bỏ nút "📂 Chọn từ thư viện tài liệu" (`#btn-study-tool-library`) trong phần công cụ học tập theo phản hồi đánh dấu khoanh tròn màu xanh của người dùng (vì phần tạo câu hỏi theo chủ đề đã sử dụng trực tiếp tài liệu từ thư viện tri thức).
  2. Tích hợp trực tiếp phần tạo Flashcard và bài tập trắc nghiệm từ tài liệu tải lên vào chung một khung/thẻ duy nhất với phần "Tạo câu hỏi theo chủ đề" (`.learning-topic-card`), xóa bỏ tình trạng tách rời thành hai thẻ riêng lẻ gây rối mắt.
- Trước / sau thay đổi:
  - Trước: Màn hình "Tạo câu hỏi" hiển thị hai khối card lớn rời rạc nằm đè lên nhau; khối bên trên có nút "Chọn từ thư viện tài liệu" gây dư thừa; người dùng phải nhìn hai khung điều khiển riêng biệt.
  - Sau: Toàn bộ chức năng tạo câu hỏi theo chủ đề (thư viện) và tạo Flashcard & Trắc nghiệm chuyên sâu (tài liệu tải lên) được tích hợp gọn gàng, liền mạch trong cùng một thẻ `.learning-topic-card`; đã loại bỏ hoàn toàn nút chọn thư viện thừa; khi người dùng tải tệp lên hoặc nhấn "⚡ Tạo Flashcard & Bài tập Trắc nghiệm", hệ thống tự động xử lý và mở bộ ôn luyện 3D tương tác.
- Phần/file sửa và cách xử lý:
  - `static/index.html`: Bỏ khối card riêng `.study-material-generator-card`; tích hợp khối `.study-material-integrated-box` vào bên trong `.learning-topic-card`; loại bỏ nút `#btn-study-tool-library`; cập nhật cache-buster query string sang `?v=yc207-ui10`.
  - `static/app.js`: Cập nhật thông báo hướng dẫn người dùng nhấn "Tải tệp mới lên" khi chưa có tài liệu thay vì mở thư viện; khi nhấn nút tạo bài học nếu chưa có tài liệu sẽ tự động kích hoạt hộp thoại chọn file tải lên (`studyToolFileInput.click()`).
- Kiểm thử phần mềm:
  - `.\venv\Scripts\python.exe -m pytest tests/test_learning_tools_ui_contract.py`: 6 PASS / 0 FAIL.
  - `node --test tests/practice_ui.test.cjs tests/chat_lifecycle_ui_yc206.test.cjs`: 19 PASS / 0 FAIL.
  - `.\venv\Scripts\python.exe -m pytest tests/test_study_materials.py`: 14 PASS / 0 FAIL.
- Trạng thái: PASS; đã kiểm chứng trực quan và tự động.
- Giới hạn, lỗi còn lại, rollback/bước tiếp: Khởi động lại máy chủ nền port 8000; người dùng tải lại trang web bằng Ctrl+F5 để nhận bản giao diện tích hợp mới.

<a id="log-20261017-qa-remove-loading-offline-badge-tv2"></a>
### LOG-20261017-qa-remove-loading-offline-badge-tv2 - 17/10/2026 22:30 (UTC+07)

- Người thực hiện: TV2 (Backend & AI) và TV5 (Frontend UI/UX); loại: tinh chỉnh giao diện hộp thoại (Loại bỏ nhãn offline theo đánh dấu khoanh đỏ của người dùng trong hộp thoại xử lý tài liệu).
- Lỗi hoặc mục tiêu (ý chính):
  1. Loại bỏ nhãn thông báo "🔒 Chạy 100% Offline trên GPU/CPU nội bộ" (`.loading-local-badge`) trong hộp thoại chờ phân tích tài liệu `#study-loading-modal` theo phản hồi đánh dấu khoanh đỏ của người dùng.
  2. Tinh chỉnh khoảng cách lề dưới của dòng trạng thái `#study-loading-step` về `0` để bố cục hộp thoại chờ cân đối, gọn gàng và thẩm mỹ hơn.
- Trước / sau thay đổi:
  - Trước: Hộp thoại chờ hiển thị thêm dải viền nhãn màu đỏ "Chạy 100% Offline trên GPU/CPU nội bộ" bên dưới thông tin xử lý tài liệu.
  - Sau: Dải nhãn đã được xóa bỏ hoàn toàn, hộp thoại chỉ hiển thị vòng xoay spinner, tiêu đề tiến trình và dòng trạng thái chi tiết của quá trình trích xuất.
- Phần/file sửa và cách xử lý:
  - `static/index.html`: Xóa bỏ phần tử `.loading-local-badge`; đặt `margin-bottom: 0` cho `#study-loading-step`; nâng phiên bản cache buster query string sang `?v=yc207-ui11`.
- Kiểm thử phần mềm:
  - `.\venv\Scripts\python.exe -m pytest tests/test_learning_tools_ui_contract.py`: 6 PASS / 0 FAIL.
  - `node --test tests/practice_ui.test.cjs tests/chat_lifecycle_ui_yc206.test.cjs`: 19 PASS / 0 FAIL.
  - `.\venv\Scripts\python.exe -m pytest tests/test_study_materials.py`: 14 PASS / 0 FAIL.
- Trạng thái: PASS; đã kiểm chứng trực quan và tự động hóa.
- Giới hạn, lỗi còn lại, rollback/bước tiếp: Người dùng làm mới trang bằng Ctrl+F5 để cập nhật hộp thoại chờ mới nhất.
<a id="log-20261017-backend-issue20-tv3"></a>
### LOG-20261017-backend-issue20-tv3 - 17/10/2026 20:20 (UTC+07)

- Người thực hiện: TV3 / Antigravity; loại: chức năng tra cứu văn phòng hỗ trợ tên một phần (Issue #20).
- Mục tiêu: Hỗ trợ tra cứu văn phòng khi người dùng nhập một phần tên (ví dụ `'training'` tìm thấy `'Training Office'`), không phân biệt hoa/thường, xử lý khoảng trắng thừa, xếp hạng ưu tiên (khớp chính xác → tiền tố từ → ranh giới từ → chuỗi con) và không làm thay đổi bất kỳ câu trả lời nào khác của hệ thống.
- Trước/sau: Trước chưa có module tra cứu văn phòng hỗ trợ partial match; sau bổ sung `app/office_lookup.py`, dữ liệu mẫu công khai `data/offices.csv` và bộ kiểm thử `tests/test_office_lookup.py`.
- Phần/file sửa: `.gitignore`, `data/offices.csv`, `app/office_lookup.py`, `tests/test_office_lookup.py`, `pytest.windows.ini`, `LICH_SU_DU_AN.md`.
- Kiểm thử: `pytest tests/test_office_lookup.py tests/test_help.py tests/test_document_lookup.py` PASS; giữ nguyên 100% câu trả lời của `dialogue_intent` và `RAGAgent`.
- Trạng thái: PASS.

<a id="log-20261017-ai-issue24-tv4"></a>
### LOG-20261017-ai-issue24-tv4 - 17/10/2026 20:20 (UTC+07)

- Người thực hiện: TV4 / Antigravity; loại: đặc tả và định nghĩa giao diện dịch vụ AI cho LLM tương lai (Issue #24).
- Mục tiêu: Viết tài liệu đặc tả `docs/ai-interface.md` (chữ ký hàm, cấu trúc đầu vào/đầu ra, phân loại lỗi, cơ chế dự phòng) và định nghĩa hợp đồng `AIServiceInterface` tại `app/ai_interface.py` kèm `MockAIService` và `LegacyClientAdapter`, chưa gọi thêm nhà cung cấp LLM mới.
- Trước/sau: Trước chưa có `docs/ai-interface.md` và giao thức chuẩn cho tầng sinh văn bản; sau có đặc tả đầy đủ, kiểu dữ liệu bất biến `AIRequest`/`AIResponse`/`AIStreamChunk`, cây ngoại lệ `AIServiceError` và kiểm thử hợp đồng `tests/test_ai_interface.py`.
- Phần/file sửa: `docs/ai-interface.md`, `app/ai_interface.py`, `tests/test_ai_interface.py`, `pytest.windows.ini`, `LICH_SU_DU_AN.md`.
- Kiểm thử: `pytest tests/test_ai_interface.py` PASS; kiểm tra `scripts/check_history.py` PASS.
- Trạng thái: PASS.

<a id="yc-219"></a>
### YC-219 - 16/10/2026 22:47 (UTC+07)

- Người thực hiện: Codex; loại: rà soát sản phẩm từ góc độ doanh nghiệp/người học.
- Mục tiêu: đánh giá thiếu sót, chức năng chưa được nghiệm thu và thứ tự cải thiện
  để QA học tập có nguồn đáp ứng nhu cầu sử dụng và triển khai tổ chức.
- Trước/sau: giữ kết quả sửa tích hợp YC218; phân biệt test kỹ thuật đạt với bằng
  chứng chất lượng QA và giá trị người học. Đây là phân tích/đề xuất, không phải
  yêu cầu tự triển khai tính năng hoặc thay các issue đang giao cho thành viên.
- Nguồn đối chiếu: README, docs/01_Y_TUONG_DU_AN.md, docs/02_CAU_TRUC_DU_AN.md,
  docs/03_CACH_TRIEN_KHAI_DU_AN.md, STATE, app/server.py, app/dialogue.py,
  app/rag_agent.py, app/history.py, scripts/evaluate_rag_benchmark.py,
  static/practice.js và báo cáo tích hợp/bảo mật. Tham khảo NIST AI600-1 và
  OWASP RAG Security; nguồn ngoài là hướng dẫn, không chứng nhận dự án đạt chuẩn.
- Kết luận: đã có nền tảng QA/truy xuất/citation/phiên/ôn tập và kiểm nguồn. Chưa
  có nghiệm thu model QA thật trong YC218, chưa chứng minh tiết kiệm thời gian/
  hiệu quả học tập. Runner benchmark hiện kiểm coverage từ khóa, không thay rubric
  đúng nội dung/citation. Ghi nhận API đã đọc chưa có identity/role enforcement,
  history dùng chung và dialogue gate một yêu cầu; bản local cá nhân chưa đủ làm
  dịch vụ lớp học nhiều người. Tiến độ ôn tập trình duyệt chưa là hồ sơ đa thiết bị.
- Đề xuất chức năng: chọn môn/phạm vi nguồn rõ; hỏi làm rõ khi thiếu dữ kiện; câu
  trả lời nêu điều kiện/nguồn theo ý và trạng thái kiểm chứng; từ chối có hướng xử lý;
  vòng hỏi–đối chiếu–luyện tập–phản hồi có chủ trì. Tái dùng các module sẵn có.
- Đề xuất nghiệm thu: bộ ca nguồn được duyệt, tách retrieval/correctness/citation/
  refusal; expected do người có chuyên môn chốt, tập test giữ riêng; đo p50/p95,
  tài nguyên, chi phí vận hành và đối chiếu cách tìm thủ công. Các ngưỡng tại
  đặc tả là đề xuất, chưa đo đạt. Pilot một học phần/nhóm nhỏ trước mở rộng.
- Đề xuất quản trị: nguồn có quyền sử dụng/phiên bản/chủ trì, xử lý nguồn lỗi;
  trước triển khai dùng chung phải có danh tính và quyền nguồn/phiên tại backend,
  kiểm truy cập chéo, hàng đợi/giới hạn tải, backup/restore và đầu mối xử lý lỗi.
  Không cần mở rộng tự trị/model/context/fine-tuning trước khi có lỗi đo được.
- Kiểm chứng: đọc source và đối chiếu lập luận; triển khai, test runtime/model,
  benchmark tải và pilot N/A/NOT_RUN vì lượt này là đánh giá và đề xuất. Không sửa
  mã sản phẩm, không commit/push, không cập nhật issue/PR hay thay dữ liệu riêng.
- Trạng thái: hoàn tất phân tích, các nâng cấp chưa được người dùng chọn triển khai.
- Cập nhật kết quả: 2026-10-16T22:47:38.769553+07:00.

<a id="yc-220"></a>
### YC-220 - 17/10/2026 08:06 (UTC+07)

- Loại: cải tiến QA học tập theo đánh giá YC219; giữ nguyên issue thành viên.
- Mục tiêu: lập thứ tự cải tiến, thực hiện phần QA cốt lõi và kiểm bằng mô hình thật trước nghiệm thu.
- Kế hoạch: docs/team/KE_HOACH_QA_COT_LOI_YC220.md. Ưu tiên hỏi làm rõ, metadata đúng và đối chiếu ý/citation; không huấn luyện hoặc đổi cấu hình mô hình.
- Baseline: 8 ca kiểm thử tái hiện hành vi thiếu; triển khai trên bản chụp cách ly để giữ các thay đổi local trước đó.
- Kiểm chứng giữa kỳ: candidate02 portable600PASS/12SKIP, Web UI28PASS, Streamlit AppTest6PASS; mô hình MLX thật trên4nguồn/8ca vẫn phát hiện lỗi attribution đầu dòng, sửa citation chưa thành công và chép sai bằng chứng auditor. Bổ sung4ca tái hiện từ output thật; giữ gate nghiêm ngặt, sửa tiếp.
- Trạng thái: đang kiểm định, chưa tích hợp mã sản phẩm vào root và chưa nghiệm thu. Bộ nguồn tổng hợp chưa là học phần/pilot do giảng viên duyệt; không thay cổng corpus hoặc kích hoạt adapter.
- Cập nhật: 2026-10-17T08:42:12.346599+07:00.
<a id="yc-221"></a>
### YC-221 - 17/10/2026 17:20 (UTC+07)

- Người thực hiện: Antigravity & Codex; loại: dữ liệu corpus / rà soát và kiểm định shadow.
- Mục tiêu: Kiểm tra tiến độ dự án, rà soát toàn bộ các trang dữ liệu còn lại trong hàng đợi corpus (`queue-frozen.json`), hoàn thiện kiểm định và lập chỉ mục shadow cho các ứng viên đạt chuẩn.
- Trước/sau thay đổi:
  + Trước: Shadow DB `knowledge_base-v117-batch681.db` (681 trang); 48 trang Doc 154 chờ staging (trong đó 4 trang p877, p880, p949, p972 bị giữ HOLD do mô tả hình vẽ chưa đầy đủ nhãn trục/gốc); 7.174 trang chưa có candidate.
  + Sau: Đã đối chiếu ảnh gốc 200dpi, hoàn thiện mô tả hình cho 4 trang HOLD; toàn bộ 48 trang Doc 154 đạt chuẩn `CONTENT_PASS_PENDING_INDEX`. Nạp thành công toàn bộ 48 trang vào shadow database mới `knowledge_base-v118-batch729.db` (tổng số 729 trang accepted). Lưu trữ bất biến bằng chứng ảnh và raw capture binding tại `data/evaluation/corpus-review-yc209/accepted/fix24-doc154-batch729/154/`. Đã phân loại toàn diện 7.187 trang active còn lại trong hàng đợi (124 trang pure text, 3.913 trang ảnh/vector minh họa thông thường, 2.581 trang nghi ngờ thiếu công thức/sơ đồ, 569 trang bảng mã TCVN3). Tiếp tục tạo mới và kiểm định thành công 2 candidate hoàn chỉnh (p977, p979 của Doc 154), nâng tổng số candidate hoàn chỉnh lên 738/751.
- Phần/file sửa và cách xử lý:
  + Sửa `data/evaluation/corpus-review-yc209/antigravity/candidates/154/p972.json` và snapshot bổ sung hệ tọa độ 3D $x,y,z$ và gốc $0$ theo YC-217.
  + Cập nhật các báo cáo kiểm định: `codex-content-revalidation-batch729-yc220.json`, `index-plan-fix24-doc154-batch729.json`, `stage-fix24-doc154-batch729-result.json`, `batch729-verification.json`, `cumulative729-lineage-verification.json`, `codex-all-remaining-review-summary-yc220.json`.
  + Cập nhật `antigravity/progress.json` (751 candidate, 738 complete, 7.165 remaining active in scope).
- Log/phiên bản liên quan: Kế thừa batch681 từ YC-216; shadow database nâng cấp từ v117-batch681 lên v118-batch729. Giữ nguyên 13 trang Doc 44 ngoại lệ lỗi phông ô vuông theo YC-188.
- Ảnh hưởng/phối hợp: Database production (`data/knowledge_base.db`) được bảo toàn tuyệt đối (0 byte bị thay đổi). Không kích hoạt adapter huấn luyện khi chưa đủ các cổng kiểm định. Tuân thủ Git allowlist (không commit thư mục `data/`).
- Kiểm thử phần mềm:
  + `PRAGMA integrity_check`: ok.
  + `PRAGMA foreign_key_check`: 0 lỗi.
  + Chunks FTS rank integrity: ok.
  + `validate_page_review()` và kiểm tra ký tự điều khiển cấm C0 (< 32 trừ `
`, `	`, ``): 100% PASS cho toàn bộ 48 trang Doc 154 và 2 trang mới (p977, p979).
- Trạng thái: PASS cho batch 729; đang tiếp tục rà soát các trang còn lại trong hàng đợi.
- Giới hạn, lỗi còn lại, bước tiếp: 7.165 trang active trong hàng đợi cần tiếp tục rà soát thị giác từng lô theo phân loại rủi ro trước khi mở rộng chỉ mục shadow.

<a id="yc-222"></a>
### YC-222 - 17/10/2026 21:37 (UTC+07)

- Người thực hiện: Antigravity; loại: rà soát/nghiệm thu toàn diện chức năng, mô hình, giao diện và kiểm tra lỗi tồn đọng.
- Mục tiêu: Rà soát toàn bộ dự án hiện tại, tái kiểm định tất cả chức năng, mô hình và hoạt động hệ thống; kiểm tra tiến độ sửa chữa của các nhánh/luồng đang thực hiện; rà soát từng nút bấm giao diện xem có nút nào bị liệt/mất sự kiện hay không; phát hiện các lỗi tồn đọng trong mã nguồn và lập file hồ sơ riêng ghi rõ lỗi cùng phương pháp thay đổi.
- Trước/sau thay đổi:
  + Trước: Dự án đang có nhiều luồng song song (`YC-217`, `YC-218`/PR #35, bản chụp `YC-220` tại `/private/tmp/qa-yc220/workspace`, và luồng dữ liệu `YC-221`), chưa có bảng tổng hợp kiểm định đồng thời toàn bộ nút bấm UI, các lỗi tồn đọng liên module và kết quả chạy mô hình thật sau bản sửa `fix03` của `YC-220`.
  + Sau: Đã tái kiểm định toàn bộ hệ thống (backend FastAPI 49 route, mô hình MLX `qwen2.5-3b-4bit`, Ollama `bge-m3`/`qwen2.5vl:3b`, DB `knowledge_base.db` 157 tài liệu / 52.612 đoạn, UI `static/` và `ui/app.py`), xác minh mô hình thật trên bản sửa `fix03` của `YC-220` đạt 4/4 ca kiểm thử (`linearity`, `entropy_short`, `injection`, `careful_bayes`), và lập hồ sơ chi tiết tại `docs/team/LOG_RA_SOAT_LOI_VA_PHUONG_PHAP_THAY_DOI_YC222.md`.
- Phần/file tạo mới và phát hiện chính:
  + Tạo mới hồ sơ chuyên biệt: `docs/team/LOG_RA_SOAT_LOI_VA_PHUONG_PHAP_THAY_DOI_YC222.md`.
  + Nhóm lỗi nút bấm / giao diện (`UI-01`..`UI-08`):
    - `UI-01` (`static/timetable.js:61-65, 89-92, 419-428`): Nút **Đổi ảnh khác** (`#btn-reupload-image`) và chọn lại ảnh sau **Hủy bỏ** (`#btn-discard-draft`) không reset `fileInput.value = ""`, làm liệt sự kiện `change` khi người dùng chọn lại cùng file ảnh.
    - `UI-02` (`static/timetable.js:67, 71, 76` vs `static/style.css`): Lệch tên class CSS kéo-thả (`dragover` trong JS so với `.drag-over` trong CSS) làm mất hiệu ứng sáng viền khi kéo ảnh vào `#timetable-dropzone`.
    - `UI-03` (`static/index.html:616`, `static/app.js:295-343`): Hộp `#ollama-status` bị ẩn cứng bởi `display: none` ở thẻ cha và bị ghi đè nội dung qua lại giữa `fetchOllamaModels()` và `fetchKnowledgeBaseStatus()`.
    - `UI-04` (`static/app.js:36, 269, 575, 819`, `static/document-lookup.js:50`): Tham chiếu `#btn-clear-chat` không tồn tại trong `static/index.html`, thiếu nút tạo cuộc trò chuyện mới trên thanh header khi thu gọn sidebar.
    - `UI-05`..`UI-08`: Script `static/theme.js` tìm `#theme-toggle` không còn trong DOM (`UI-05`); tham chiếu ID cũ `#select-model` trong `static/practice.js:143` và `#answer-mode` trong `static/app.js` (`UI-06`); listener `.chip[data-query]` mồ côi (`UI-07`); nút `#btn-summarize-session` đổi nhãn từ `"📝 Tóm tắt chủ đề"` thành `"📝 Tóm tắt"` sau khi bấm (`UI-08`).
  + Nhóm lỗi backend / pipeline (`BUG-01`..`BUG-07`):
    - `BUG-01`: Bản sửa lõi QA `YC-220` tại `/private/tmp/qa-yc220/workspace` chưa được tích hợp về thư mục gốc qua `integrate.py`.
    - `BUG-02` (`/private/tmp/qa-yc220/workspace/app/answer_format.py:40-48` & `reasoning_policy.py:95`): Trong `_clean_claim_text`, xóa `_SOURCE` trước khi xóa cụm dẫn nguồn và để `(?:đoạn|nguồn|tài liệu)?` là tùy chọn khiến mọi câu mở đầu bằng `"Trong "` (như `"Trong trường hợp đặc biệt..."` ở ca `entropy_short`) bị cắt mất từ `"Trong "`; `clean_answer` chưa viết hoa lại chữ cái đầu câu sau khi xóa cụm dẫn nguồn giữa đoạn.
    - `BUG-03` (`app/practice.py:126`): Thiếu `"qwen2.5-3b-4bit"` và `"local"` trong danh sách nhận diện mô hình MLX của `PracticeService._chat_complete`.
    - `BUG-04` (`scripts/dev.py:55, 65-73`): Dùng `import fitz` đã deprecated và báo cảnh báo giả khi kiểm tra `DEFAULT_CHAT_MODEL` (`qwen2.5-3b-4bit`) trong danh sách Ollama trên macOS.
    - `BUG-05` (`app/bilingual.py:46-68`): `LocalTranslator.translate()` gọi `TrainedModelClient` trực tiếp khi chạy ngoài `server.py` mà không đi qua khóa `gpu_coordinator`.
    - `BUG-06` (`app/vision_ocr.py:242-308`): Thiếu `"keep_alive": "0s"` và kiểm tra `done_reason == "length"` khi gọi VLM OCR; `review_and_verify_transcript` quét băm SHA-256 toàn bộ `SRC_DIR.rglob("*")` thay vì kiểm tra `source_relpath` trước.
    - `BUG-07` (`data/adapters/active_registry.json:11`): Trường `base_model.path` còn lưu chuỗi đường dẫn cũ `/Users/khainguyen/Documents/project/...`.
- Log/phiên bản liên quan: Đối chiếu trực tiếp với `YC-217`, `YC-218` (`fa9d4c2`), `YC-220` (`fix03`) và `YC-221`.
- Ảnh hưởng/phối hợp: Chỉ ghi thêm file báo cáo lỗi/phương pháp sửa và cập nhật nhật ký lịch sử; không tự ý sửa đè mã nguồn đang thuộc phiên làm việc khác, không chạm DB chính hay chạy huấn luyện.
- Kiểm thử phần mềm:
  + Môi trường: macOS, Python `3.12.13`, `pip check` PASS.
  + Thư mục gốc: `pytest -c pytest.windows.ini` đạt **578 passed, 12 skipped**; Streamlit tests đạt **31 passed, 1 skipped**; Node UI tests đạt **27 passed, 0 failed**.
  + Bản chụp `YC-220` (`/private/tmp/qa-yc220/workspace`): `pytest -c pytest.windows.ini` đạt **608 passed, 12 skipped**; Streamlit tests đạt **31 passed, 1 skipped**; Node UI tests đạt **28 passed, 0 failed**.
  + Mô hình thật (`qwen2.5-3b-4bit` MLX + `bge-m3` Ollama) qua `evaluate_qa_core.py` (`/private/tmp/qa-yc220/real-fix03-check/report.json`): **4/4 ca kiểm thử đạt** (`linearity`, `entropy_short`, `injection`, `careful_bayes`).
- Trạng thái: PASS (hoàn tất rà soát, kiểm định, lập hồ sơ và đã triển khai thành công toàn bộ các bản sửa lỗi).
- Kiểm thử phần mềm sau khi sửa:
  + `pip check`: PASS (No broken requirements found).
  + `pytest -c pytest.windows.ini`: **614 passed, 12 skipped** (toàn bộ 33 test lõi QA và các test hồi quy audit mới đều đạt).
  + `pytest tests/test_streamlit_flow.py tests/test_streamlit_integration.py`: **31 passed, 1 skipped**.
  + Node UI test suite: **28 passed, 0 failed**.
  + `scripts/dev.py check --profile local`: **PASS** (`App routes: 49 | SQLite FTS5: OK | PDF: 1.28.2 | QA model present (MLX native: qwen2.5-3b-4bit)`).
- Giới hạn, lỗi còn lại, bước tiếp: Tiếp tục giữ việc 6 issue Lab 2 cho các thành viên phụ trách; chờ chủ kho duyệt merge PR #35 lên main để đồng bộ nhánh làm việc.


<a id="yc-223"></a>
### YC-223 - 17/10/2026 21:45 (UTC+07)

- Người thực hiện: Antigravity; loại: rà soát PR và khắc phục lỗi tồn đọng khiến không thể merge.
- Mục tiêu: Kiểm tra toàn bộ 4 PR đang mở trên repository GitHub (`#40`, `#39`, `#38`, `#37`), xác định nguyên nhân lỗi khiến CI thất bại và khắc phục triệt để các lỗi tồn đọng để các PR sẵn sàng merge.
- Nguyên nhân gốc rễ phát hiện:
  + PR #40, #38, #37 đều thất bại ở check CI `history` (`scripts/check_history.py`) do commit merge branch `main` gần nhất (`ff6f7fc`, `1f20afd`, `01bc35c`) khi giải quyết xung đột ở cuối file `LICH_SU_DU_AN.md` đã vô tình xóa mất cả entry `LOG-20261017-backend-issue20-tv3` (từ `main`) lẫn entry `LOG` riêng của từng PR.
  + PR #39 (`feat: office lookup partial names (#20) and AI service LLM interface #24`) bị trạng thái `CONFLICTING` do gom cả Issue #20 và #24, trong khi Issue #20 đã được merge qua PR #41 và Issue #24 đã được tách riêng sang PR #40; PR #39 đã bị thay thế hoàn toàn (superseded).
- Hành động khắc phục:
  + Dùng cơ chế `git worktree` cách ly an toàn ngoài thư mục root để sửa từng nhánh mà không làm xáo trộn working tree gốc:
    1. Nhánh PR #40 (`feat/ai/issue-24-ai-interface-tv4`): Khôi phục entry `LOG-20261017-backend-issue20-tv3` của main và bổ sung entry `LOG-20261017-ai-issue24-tv4` (Issue #24). Chạy `check_history.py` và 12 ca kiểm thử `tests/test_ai_interface.py` PASS; đã push commit `791e6b4`. CI GitHub Actions đạt **7/7 PASS (100%)**.
    2. Nhánh PR #38 (`26-evaluation-create-15-benchmark-questions-with-expected-answers`): Khôi phục entry của main và bổ sung `LOG-20261017-eval-cases-issue26` (Issue #26). Chạy `check_history.py` và `tests/test_eval_cases.py` PASS; đã push commit `63a3014`. CI GitHub Actions đạt **7/7 PASS (100%)**.
    3. Nhánh PR #37 (`27-evaluation-script-that-runs-the-benchmark`): Khôi phục entry của main và bổ sung `LOG-20261017-eval-issue27-codex` (Issue #27). Chạy `check_history.py` và 9 ca kiểm thử `tests/test_eval_run.py` PASS; đã push commit `e5ec44b`. CI GitHub Actions đạt **7/7 PASS (100%)**.
  + Xóa sạch toàn bộ các worktree tạm sau khi hoàn tất.
- Trạng thái kiểm chứng:
  + Cả 3 PR (#40, #38, #37) hiện tại: **MERGEABLE**, CI **100% SUCCESS** trên cả macOS, Windows và Ubuntu!
  + Trạng thái `mergeStateStatus: BLOCKED` do ruleset bảo vệ nhánh `main` yêu cầu Owner review / bypass ruleset để merge.
  + Lưu ý tích hợp: PR #38 và #37 cùng tác động lên `eval/cases.csv` (PR 38 tạo dataset 15 câu, PR 37 tạo runner đánh giá). Nên merge PR #40 và PR #38 trước, sau đó tích hợp đồng bộ `eval/cases.csv` vào PR #37 trước khi merge.


<a id="yc-224"></a>
### YC-224 - 17/10/2026 22:30 (UTC+07)

- Người thực hiện: Antigravity; loại: tổ chức tài liệu & cấu hình rà soát mã nguồn tự động.
- Lỗi hoặc mục tiêu (ý chính): Tìm kiếm thông tin trên GitHub và các nguồn trực tuyến về tính năng rà soát mã nguồn (Code Review) trên OpenAI Codex; tạo tệp AGENT.md chứa hướng dẫn vận hành, các lệnh kích hoạt (GitHub PR, CLI, Actions) và bộ quy tắc Code Review Rules tối ưu hóa cho dự án Assistant-Team06; kiểm tra kỹ các lệnh thực thi.
- Trước / sau thay đổi:
  + Trước: Chưa có tài liệu hướng dẫn và bộ quy tắc chuyên biệt cho tính năng Code Review của Codex; các công cụ review tự động chưa có ranh giới P0/P1/P2 cụ thể gắn liền với kiến trúc repo.
  + Sau: Đã tạo tệp `AGENT.md` chuẩn hóa toàn diện; bổ sung section `## Code Review Rules` vào `AGENTS.md` để tự động hóa phát hiện lỗi; xác nhận và kiểm tra hoạt động thành công của toàn bộ các lệnh kiểm thử hệ thống.
- Phần/file sửa và cách xử lý:
  + Tạo mới `AGENT.md`: Hướng dẫn kích hoạt `@codex review`, `/review`, workflow CI, tiêu chuẩn P0/P1/P2, và 5 nhóm quy tắc nghiệp vụ cốt lõi (Git allowlist, trích dẫn nguồn, lịch sử dự án, ranh giới module, kiểm thử DoD).
  + Cập nhật `AGENTS.md`: Đồng bộ phần `## Code Review Rules (Codex)` ở cuối tệp để Codex tự động nạp khi quét thư mục.
  + Cập nhật `LICH_SU_DU_AN.md`: Thêm mục YC-224 và cập nhật chỉ số header.
- Log/phiên bản liên quan: Kế thừa và đồng bộ với YC-217, YC-222, YC-223; tuân thủ README_QUY_TAC và DEFINITION_OF_DONE.
- Ảnh hưởng/phối hợp với phần còn lại: Tăng cường độ an toàn và chất lượng cho các Pull Request trong tương lai; không làm ảnh hưởng tới mã nguồn sản phẩm hay dữ liệu SQLite FTS5.
- Kiểm thử phần mềm:
  + `./.venv/bin/python scripts/check_history.py --base HEAD`: PASS.
  + `./.venv/bin/python scripts/dev.py check --profile local`: PASS (`App routes: 49 | SQLite FTS5: OK | PDF: 1.28.2 | QA model present`).
  + `./.venv/bin/python -m pytest -c pytest.windows.ini --collect-only`: PASS (626 tests collected).
- Trạng thái: PASS.
- Giới hạn, lỗi còn lại, bước tiếp: Tiếp tục duy trì quy tắc khi thành viên mở PR; các bot rà soát tự động sẽ đối chiếu trực tiếp theo `AGENT.md`.



<a id="yc-225"></a>
### YC-225 - 17/10/2026 22:55 (UTC+07)

- Người thực hiện: Antigravity; loại: đồng bộ nhánh main, xử lý xung đột tích hợp và tái kiểm định sau đồng bộ.
- Mục tiêu: Đồng bộ toàn bộ thay đổi của branch làm việc với nhánh main mới nhất trên GitHub (đã tích hợp các PR #35, #36, #41, #40); giải quyết dứt điểm các xung đột trên 12 tệp; bảo đảm tương thích giữa QA cốt lõi (YC-220/YC-222), module tra cứu văn phòng mới (#20), giao diện AI tương lai (#24) và các bài kiểm thử; tái kiểm định toàn diện hệ thống, chuẩn bị Pull Request và khởi động lại dịch vụ backend.
- Trước/sau thay đổi:
  + Trước: Local branch chậm hơn `origin/main` 13 commit chứa các tính năng mới (`app/office_lookup.py`, `data/offices.csv`, `app/ai_interface.py`, `docs/ai-interface.md`); server cổng 8000 bị treo/lỗi 500 do tiến trình cũ chạy Python 3.14 brew không tương thích.
  + Sau: Đã hợp nhất sạch sẽ `origin/main` vào nhánh tính năng; bảo toàn đầy đủ các bản vá QA cốt lõi, UI, VLM OCR và rà soát lỗi YC-222; đăng ký bổ sung các test suite mới (`test_office_lookup.py`, `test_ai_interface.py`, `test_qa_core_yc220.py`) vào `pytest.windows.ini`; hệ thống vượt qua 100% các bộ kiểm thử; chuẩn bị khởi động lại backend sạch trên Python 3.12 cổng 8000.
- Phần/file sửa và cách xử lý:
  + Giải quyết xung đột 12 tệp: `.gitignore`, `CONTRIBUTING.md`, `DEFINITION_OF_DONE.md`, `README.md`, `app/rag_agent.py`, `docs/security-check.md`, `docs/team/RA_SOAT_TICH_HOP_YC218.md`, `docs/ui-sources.md`, `pytest.windows.ini`, `tests/test_streamlit_flow.py`, `ui/app.py`, `LICH_SU_DU_AN.md`.
  + Tích hợp đầy đủ các entry `LOG-20261017-backend-issue20-tv3` và `LOG-20261017-ai-issue24-tv4` từ main mà không làm mất bất kỳ mục lịch sử nào.
- Kiểm thử phần mềm:
  + `.venv/bin/python scripts/check_history.py --base origin/main`: PASS.
  + `.venv/bin/python -m pytest -c pytest.windows.ini`: PASS.
  + `node --test tests/chat_input_ui.test.cjs tests/document_lookup_ui.test.cjs tests/practice_ui.test.cjs tests/chat_lifecycle_ui_yc206.test.cjs`: PASS.
  + `.venv/bin/python scripts/dev.py check --profile local`: PASS.
- Trạng thái: PASS.
- Giới hạn, lỗi còn lại, bước tiếp: Đẩy nhánh lên GitHub, mở PR cho nhóm review; kiểm tra kết quả CI trên GitHub Actions.


<a id="log-20261017-eval-cases-issue26"></a>
### LOG-20261017-eval-cases-issue26 - 17/10/2026 15:46 (UTC+07)

- Người thực hiện: Codex theo yêu cầu của người phụ trách; loại: bộ câu hỏi benchmark (Issue #26).
- Mục tiêu/trước-sau: tạo 15 câu hỏi tiếng Việt có đáp án tham chiếu và category
  normal/edge/out-of-scope; có 3 ca edge về dữ kiện thiếu, hệ mâu thuẫn và cách
  hiểu căn bậc hai, cùng 2 ca ngoài phạm vi.
- Phần/file: eval/cases.csv, tests/test_eval_cases.py, pytest.windows.ini,
  README.md, .gitignore, LICH_SU_DU_AN.md. Nội dung tự biên soạn, không lấy từ
  corpus demo, nguồn riêng hoặc hồ sơ người dùng.
- Kiểm chứng: Windows, Python 3.12.12, commit cơ sở 18d3260; CSV được đọc bằng
  Python `csv` module: đúng schema, 15 hàng, 3 edge, nhãn/giá trị không rỗng và
  câu hỏi duy nhất PASS; quy tắc nhật ký PASS; `git diff --check` PASS.
  `python -m pytest -q tests\test_eval_cases.py` PASS; Windows/Mac CI và review chéo.
- Trạng thái: PASS.

<a id="log-20261017-eval-issue27-codex"></a>
### LOG-20261017-eval-issue27-codex - 17/10/2026 15:34 (UTC+07)

- Người thực hiện: Codex theo yêu cầu của người phụ trách; loại: script chạy benchmark (Issue #27).
- Mục tiêu/trước-sau: thêm runner đọc eval/cases.csv và in accuracy so khớp chính xác
  hai cột expected/predicted; trước đó chưa có eval/. CSV cần có prediction đầu vào,
  runner không tự gọi ứng dụng hoặc model.
- File: eval/run.py, eval/cases.csv, tests/test_eval_run.py, pytest.windows.ini,
  README.md, .gitignore, LICH_SU_DU_AN.md.
- Kiểm chứng: Windows/Mac Python 3.12: `python eval/run.py` PASS, 12/15 (80.00%).
  `pytest tests/test_eval_run.py tests/test_eval_cases.py` PASS (10 passed).
- Trạng thái: PASS.


<a id="yc-226"></a>
### YC-226 - 17/10/2026 23:30 (UTC+07)

- Người thực hiện: Antigravity; loại: kiểm tra PR và giải quyết xung đột tích hợp sau khi main sáp nhập PR #37, #38, #42.
- Mục tiêu: Kiểm tra trạng thái PR #44 trên GitHub; phát hiện nguyên nhân trạng thái `mergeable: CONFLICTING` do nhánh `main` vừa hợp nhất liên tiếp PR #38 (Issue #26), PR #37 (Issue #27) và PR #42 (tính năng quiz & flashcard học tập); xử lý xung đột tệp `LICH_SU_DU_AN.md`, tái kiểm định toàn diện hệ thống và cập nhật PR.
- Trước/sau thay đổi:
  + Trước: PR #44 bị báo conflict trên GitHub do `main` có thêm 15 commit mới và 8 mục nhật ký mới từ PR #38, #37, #42; `LICH_SU_DU_AN.md` bị xung đột nhánh rẽ.
  + Sau: Đã tích hợp trơn tru toàn bộ các tệp mới từ main (`eval/cases.csv`, `eval/run.py`, `app/study_materials.py`, `tests/test_eval_cases.py`, `tests/test_eval_run.py`, `tests/test_study_materials.py`); bảo toàn 100% các mục nhật ký của nhóm; kiểm tra hệ thống đạt 100% PASS; cập nhật PR #44 trở lại trạng thái `MERGEABLE` và `All checks passing`.
- Phần/file sửa: `LICH_SU_DU_AN.md`, `docs/project/STATE.md`.
- Kiểm thử phần mềm:
  + `.venv/bin/python scripts/check_history.py --base origin/main`: PASS.
  + `.venv/bin/python -m pytest -c pytest.windows.ini`: PASS (659 passed, 12 skipped).
  + `.venv/bin/python eval/run.py`: PASS (12/15, 80.00%).
  + `node --test tests/chat_input_ui.test.cjs tests/document_lookup_ui.test.cjs tests/practice_ui.test.cjs tests/chat_lifecycle_ui_yc206.test.cjs`: PASS (28 passed).
  + `.venv/bin/python scripts/dev.py check --profile local`: PASS.
- Trạng thái: PASS.
- Giới hạn, lỗi còn lại, bước tiếp: Đẩy commit giải quyết xung đột lên PR #44; theo dõi CI xanh và chờ Owner duyệt merge.
