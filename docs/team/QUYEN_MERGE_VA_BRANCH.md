# Quyền merge và vòng đời branch

Repo chính: [Assistant-Team06](https://github.com/ng-wngkh07/Assistant-Team06).
Owner quản lý settings; thành viên tiếp tục tạo nhánh, PR và review.

## Chỉ owner merge main

Ngày 16/10/2026 đã bật ruleset
[Only repository owner can merge main](https://github.com/ng-wngkh07/Assistant-Team06/rules/25241854):

- Target duy nhất `refs/heads/main`, enforcement `active`.
- Rule `Restrict updates`.
- Bypass duy nhất user owner `ng-wngkh07`, chế độ `For pull requests only`.
- Giữ nguyên branch protection riêng: CI Windows/Mac/history, nhánh cập nhật,
  xử lý hội thoại, áp dụng cả admin, cấm force-push/xóa main.

Không gộp CI vào ruleset có owner bypass; nếu gộp, bypass có thể bỏ qua cả CI.
Tách ruleset quyền cập nhật và protection chất lượng giúp owner vẫn phải đáp ứng
checks hiện có. `CODEOWNERS` yêu cầu người review, không tự giới hạn người merge.

Đã đọc lại API: rule có hiệu lực trên main, chỉ một bypass actor, protection
trước/sau không đổi. Chưa thử thao tác merge bằng tài khoản thành viên khác;
không tuyên bố đã kiểm hành vi bằng tài khoản đó. Owner vẫn có quyền sửa settings.

Để dựng lại: Settings → Rules → Rulesets → New branch ruleset → chọn main →
Restrict updates → thêm **đúng user owner** vào bypass, chọn **For pull requests only**
→ Active. Giữ protection/chất lượng ở lớp riêng. Tài liệu API và khả năng chọn
user hiện tại: [GitHub REST](https://docs.github.com/en/rest/repos/rules),
[GitHub changelog](https://github.blog/changelog/2026-05-07-repository-rulesets-user-bypass-and-branch-renaming/).

## Branch sau merge

Không bắt buộc xóa branch để repo chạy đúng. Nên xóa nhánh công việc sau khi
PR đã merge, kiểm main đạt và không còn PR/việc khác dùng nhánh. Commit đã merge
vẫn ở lịch sử main; PR và review vẫn còn. Squash/rebase có thể làm Git không
nhận nhánh local là đã merge dù PR đã merge: kiểm PR, không dùng `-D` máy móc.

Owner có thể bật Settings → General → Pull Requests → Automatically delete head
branches cho các PR tiếp theo. YC217 chưa bật tùy chọn này và chưa xóa branch
nào, vì các nhánh đang được thành viên sử dụng cần kiểm riêng.

Trên máy thành viên, sau khi giữ/cất việc local và xác nhận PR đã merge:

```bash
git fetch origin --prune
git switch main
git pull --ff-only origin main
git branch -d <nhanh-da-merge>
```

`--prune` dọn tham chiếu remote đã mất; không tự xóa nhánh local.
Không xóa main, nhánh PR đang mở hoặc nhánh còn commit chưa bàn giao.
Xem [hướng dẫn GitHub](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-the-automatic-deletion-of-branches).
