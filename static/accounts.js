(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init = {}) => {
        const target = new URL(typeof input === "string" ? input : input.url, location.href);
        const method = (init.method || (typeof input === "object" && input.method) || "GET").toUpperCase();
        if (target.origin === location.origin && !["GET","HEAD","OPTIONS"].includes(method)) {
            const headers = new Headers(init.headers || (typeof input === "object" ? input.headers : undefined));
            headers.set("X-Study-Request", "1");
            init = {...init, headers};
        }
        return originalFetch(input, init);
    };
    document.addEventListener("DOMContentLoaded", async () => {
        const toolbar = document.createElement("div"); toolbar.className = "account-toolbar";
        const label = document.createElement("span");
        const login = document.createElement("button"); login.className = "btn btn-sm btn-outline"; login.textContent = "Đăng nhập / Tạo tài khoản";
        const replay = document.createElement("button"); replay.className = "btn btn-sm btn-outline"; replay.textContent = "Hướng dẫn chức năng";
        toolbar.append(label, replay, login); document.body.prepend(toolbar);
        const dialog = document.createElement("dialog"); dialog.className = "account-dialog";
        dialog.innerHTML = '<form id="account-form"><h2 id="account-title">Đăng nhập</h2><p>Lưu lịch và hướng dẫn theo tài khoản để tiếp tục trên thiết bị khác cùng truy cập máy chủ này.</p><label for="account-username">Tên tài khoản</label><input id="account-username" class="form-input" autocomplete="username" minlength="3" maxlength="100" pattern="[A-Za-z0-9_.@+\\-]+" required><label for="account-password">Mật khẩu (ít nhất 12 ký tự)</label><input id="account-password" class="form-input" type="password" autocomplete="current-password" minlength="12" maxlength="128" required><p id="account-status" role="status"></p><button class="btn btn-primary" type="submit">Đăng nhập</button> <button id="account-switch" class="btn btn-outline" type="button">Tạo tài khoản mới</button> <button id="account-close" class="btn btn-outline" type="button">Đóng</button></form>';
        document.body.append(dialog);
        const form = dialog.querySelector("form"), status = dialog.querySelector("#account-status");
        let registration = false, info = null, activeFeature = "qa", guideSequence = 0;
        const guides = {
            qa: ["Hỏi đáp tài liệu", "1. Chọn tài liệu hoặc nhập câu hỏi. 2. Đọc câu trả lời và mở trích dẫn để đối chiếu. 3. Khi nguồn chưa đủ, bổ sung thông tin thay vì xem câu trả lời là kết luận chắc chắn."],
            practice: ["Tạo câu hỏi", "1. Chọn nguồn và phạm vi. 2. Tạo bộ câu hỏi rồi làm bài. 3. Xem giải thích cùng đoạn nguồn sau khi trả lời."],
            review: ["Ôn tập", "1. Làm một bộ câu hỏi. 2. Mở Ôn tập để xem phần cần học lại. 3. Đối chiếu giải thích và nguồn trước lần luyện tiếp theo."],
            examples: ["Bài tập mẫu", "1. Chọn tài liệu và phần đọc. 2. Trích xuất bài tập mẫu. 3. Mở nguồn gốc để xem điều kiện và cách giải."],
            documents: ["Tìm nguồn", "1. Nhập từ khóa và chọn sách nếu cần. 2. Tìm đoạn liên quan. 3. Mở trang gốc để kiểm tra trước khi dùng làm nguồn luyện tập."],
            materials: ["Tài liệu cá nhân", "1. Chọn tệp được phép sử dụng. 2. Kiểm tra nội dung trích xuất. 3. Dùng tài liệu trong phiên học. Tệp tài khoản được lưu riêng; chức năng nhập vào thư viện chung cần quản trị cục bộ."],
            ocr: ["Kiểm tra OCR", "1. Chọn tài liệu và trang. 2. Đối chiếu kết quả với ảnh gốc. 3. Giữ trạng thái chưa duyệt nếu trang không rõ. Đây là công cụ quản trị thư viện cục bộ."],
            planner: ["Lập kế hoạch học tập", "1. Nhập ảnh hoặc lịch học bằng tay. 2. Thêm thời gian cá nhân. 3. Thêm deadline, xem và xác nhận lịch học đề xuất. 4. Xuất .ics, kiểm tra lời nhắc trong ứng dụng lịch."]
        };
        const banner = document.createElement("aside"); banner.className = "feature-guide"; banner.hidden = true;
        banner.setAttribute("aria-label", "Hướng dẫn lần đầu");
        toolbar.after(banner);
        async function guide(feature, force=false) {
            activeFeature = feature;
            const sequence = ++guideSequence;
            banner.hidden = true;
            try {
                let first = true;
                if (info?.account) {
                    const response = await fetch(`/api/account/features/${feature}`);
                    if (!response.ok) throw new Error("Không tải được hướng dẫn.");
                    first = (await response.json()).first_use;
                } else {
                    try { first = localStorage.getItem(`study-guide-local-${feature}`) !== "done"; } catch {}
                }
                if (sequence !== guideSequence || (!first && !force)) return;
                banner.replaceChildren();
                const title = document.createElement("strong"); title.textContent = guides[feature][0];
                const text = document.createElement("p"); text.textContent = guides[feature][1];
                const done = document.createElement("button"); done.className = "btn btn-sm btn-primary"; done.textContent = "Đã hiểu, bắt đầu";
                done.addEventListener("click", async () => {
                    done.disabled = true;
                    try {
                        if (info?.account) {
                            const response = await fetch(`/api/account/features/${feature}/guided`, {method:"POST"});
                            if (!response.ok) throw new Error("Chưa lưu được hướng dẫn. Vui lòng thử lại.");
                        } else { try { localStorage.setItem(`study-guide-local-${feature}`, "done"); } catch {} }
                        if (sequence === guideSequence) banner.hidden = true;
                    } catch (error) { text.textContent = error.message; }
                    finally { done.disabled = false; }
                });
                banner.append(title,text,done); banner.hidden = false;
                if (!force) {
                    if (info?.account) {
                        const response = await fetch(`/api/account/features/${feature}/guided`, {method:"POST"});
                        if (!response.ok) label.textContent = "Chưa lưu được lần xem hướng dẫn; hướng dẫn có thể hiện lại.";
                    } else { try { localStorage.setItem(`study-guide-local-${feature}`, "done"); } catch {} }
                }
            } catch (error) { label.textContent = error.message; }
        }
        replay.addEventListener("click", () => guide(activeFeature,true));
        const map = {"mode-qa":"qa", "mode-tools":"practice", "mode-timetable":"planner", "btn-open-timetable":"planner", "btn-tool-tab-practice":"practice", "btn-tool-tab-review":"review", "btn-tool-tab-examples":"examples", "btn-open-kb":"documents", "btn-study-tool-upload":"materials", "btn-attach-upload":"materials", "btn-open-ocr-review":"ocr"};
        document.addEventListener("click", event => { const control = event.target.closest("[id]"); if (control && map[control.id]) guide(map[control.id]); });
        document.getElementById("document-query")?.addEventListener("focus", () => guide("documents"));
        dialog.querySelector("#account-switch").addEventListener("click", () => {
            registration = !registration;
            dialog.querySelector("h2").textContent = registration ? "Tạo tài khoản" : "Đăng nhập";
            form.querySelector('[type="submit"]').textContent = registration ? "Tạo tài khoản" : "Đăng nhập";
            dialog.querySelector("#account-password").autocomplete = registration ? "new-password" : "current-password";
            dialog.querySelector("#account-switch").textContent = registration ? "Tôi đã có tài khoản" : "Tạo tài khoản mới";
            status.textContent = "";
        });
        dialog.querySelector("#account-close").addEventListener("click", () => dialog.close());
        login.addEventListener("click", async () => {
            if (info?.account) {
                const response = await fetch("/api/account/logout", {method:"POST"});
                if (response.ok) location.reload();
                else label.textContent = "Chưa đăng xuất được. Hãy thử lại.";
            } else dialog.showModal();
        });
        form.addEventListener("submit", async event => {
            event.preventDefault();
            const submit = form.querySelector('[type="submit"]'); submit.disabled = true;
            try {
                const response = await fetch(`/api/account/${registration ? "register" : "login"}`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({username:dialog.querySelector("#account-username").value, password:dialog.querySelector("#account-password").value})});
                const data = await response.json();
                if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Kiểm tra tên tài khoản và mật khẩu ít nhất 12 ký tự.");
                location.reload();
            } catch (error) { status.textContent = error.message; }
            finally { submit.disabled = false; }
        });
        try {
            const response = await fetch("/api/account/status");
            if (!response.ok) throw new Error("Không kiểm tra được trạng thái tài khoản.");
            info = await response.json();
            label.textContent = info.account ? info.account.username : "Chế độ cục bộ · đăng nhập để lưu theo tài khoản";
            login.textContent = info.account ? "Đăng xuất" : "Đăng nhập / Tạo tài khoản";
            dialog.querySelector("#account-switch").hidden = !info.registration_allowed;
            if (info.required && !info.account) dialog.showModal();
            else guide(activeFeature);
        } catch (error) { label.textContent = error.message; }
    });
})();
