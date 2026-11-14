document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("timetable-panel");
    const progress = document.getElementById("planner-wizard-progress");
    if (!root || !progress) return;
    const definitions = [
        ["Lịch học", "Tải ảnh, kiểm tra kết quả và lưu lịch. Bạn cũng có thể nhập tay hoặc tiếp tục khi chưa có lịch học.", ["timetable-step-ocr"]],
        ["Thời gian cá nhân", "Thêm giờ đi làm, thể thao và nghỉ ngơi. Bộ xếp lịch tự tính giờ rảnh ban ngày, hoặc bạn có thể thêm khung giờ ưu tiên tự học (tùy chọn).", ["planner-step-routines"]],
        ["Kế hoạch & deadline", "Xem lịch hiện tại, thêm deadline có ngày cụ thể và thời gian cần làm. Tạo đề xuất, chỉnh sửa rồi xác nhận các buổi tự học.", ["planner-step-master-schedule", "planner-step-tasks", "planner-weekly-lessons-card", "planner-step-generate"]],
        ["Xuất lịch", "Chọn khoảng ngày và thời gian nhắc. Nhập tệp .ics vào ứng dụng lịch, kiểm tra deadline và cho phép thông báo.", []]
    ];
    let current = 0;
    const screens = definitions.map(([title, hint, ids], index) => {
        const screen = document.createElement("section");
        screen.id = `planner-screen-${index+1}`;
        screen.className = "planner-wizard-screen";
        const heading = document.createElement("h3");
        heading.tabIndex = -1;
        heading.textContent = `Bước ${index+1}: ${title}`;
        const help = document.createElement("p");
        help.className = "planner-step-hint";
        help.textContent = hint;
        screen.append(heading, help);
        ids.forEach(id => { const element = document.getElementById(id); if (element) screen.append(element); });
        root.append(screen);
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = `${index+1}. ${title}`;
        button.setAttribute("aria-controls", screen.id);
        button.addEventListener("click", () => { if (index < current) show(index); });
        progress.append(button);
        return screen;
    });
    screens[3].append(document.querySelector(".timetable-export-card"));
    const weekday = document.getElementById("task-deadline-weekday");
    if (weekday) weekday.parentElement.hidden = true;
    const preferenceBox = document.querySelector(".planner-preferences-box");
    if (preferenceBox) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "Giới hạn học mỗi ngày và tùy chọn nâng cao";
        preferenceBox.before(details);
        details.append(summary, preferenceBox);
        const week = document.getElementById("pref-week-start")?.parentElement;
        if (week) details.before(week);
    }
    const legend = document.querySelector(".planner-rule-legend");
    if (legend) {
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "Các loại thời gian có ý nghĩa gì?";
        legend.before(details);
        details.append(summary, legend);
    }
    const footer = document.createElement("div");
    footer.className = "planner-wizard-footer";
    const back = document.createElement("button");
    back.type = "button"; back.className = "btn btn-secondary"; back.textContent = "Quay lại";
    const next = document.createElement("button");
    next.type = "button"; next.className = "btn btn-primary";
    const status = document.createElement("p");
    status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    footer.append(back, status, next); root.append(footer);
    function show(index, focus=true) {
        current = index;
        screens.forEach((screen, i) => { screen.hidden = i !== index; });
        Array.from(progress.children).forEach((button,i) => {
            button.disabled = i > current;
            button.setAttribute("aria-current", i === current ? "step" : "false");
        });
        back.hidden = index === 0;
        next.hidden = index === 3;
        next.textContent = index === 1 ? "Lưu & tiếp tục" : "Tiếp tục";
        status.textContent = "";
        if (focus) { screens[index].querySelector("h3").focus(); root.scrollIntoView({block:"start", behavior:"smooth"}); }
    }
    async function advance() {
        if (current === 0 && window.StudyPlanner?.hasUnconfirmedDraft()) {
            status.textContent = "Hãy kiểm tra và xác nhận bản nháp từ ảnh, hoặc hủy bản nháp trước khi tiếp tục.";
            return;
        }
        next.disabled = true;
        try {
            if (current === 1 && !await window.StudyPlanner.savePersonal()) {
                status.textContent = "Chưa thể lưu. Hãy kiểm tra các ô thời gian và thông báo trong bước này.";
                const invalid = screens[1].querySelector(":invalid");
                if (invalid) { const details = invalid.closest("details"); if (details) details.open = true; invalid.focus(); }
                return;
            }
            if (current === 2 && document.getElementById("planner-draft-card")?.style.display !== "none") {
                status.textContent = "Bạn có đề xuất tự học chưa xác nhận. Hãy xác nhận hoặc hủy đề xuất trước khi xuất lịch.";
                return;
            }
            show(Math.min(3,current+1));
        } catch (error) { status.textContent = `Chưa thể tiếp tục: ${error.message}`; }
        finally { next.disabled = false; }
    }
    back.addEventListener("click", () => show(Math.max(0,current-1)));
    next.addEventListener("click", advance);
    document.getElementById("btn-go-step2")?.addEventListener("click", event => { event.preventDefault(); advance(); });
    show(0,false);
});
