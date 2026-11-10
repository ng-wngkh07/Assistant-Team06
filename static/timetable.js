/**
 * Timetable vision extraction, editable draft review, manual entry, and ICS export.
 * Images and schedules are stored by the application server in the current account scope.
 */
document.addEventListener("DOMContentLoaded", () => {
    const storageScope = document.querySelector?.('meta[name="study-account-scope"]')?.content || "local";
    // Elements
    const dropzone = document.getElementById("timetable-dropzone");
    const fileInput = document.getElementById("timetable-file-input");
    const previewContainer = document.getElementById("timetable-preview-container");
    const previewImg = document.getElementById("timetable-preview-img");
    const filenameEl = document.getElementById("timetable-filename");
    const imageMetaEl = document.getElementById("timetable-image-meta");
    const reuploadBtn = document.getElementById("btn-reupload-image");

    const loadingBox = document.getElementById("timetable-extract-loading");
    const extractStatus = document.getElementById("timetable-extract-status");

    const draftCard = document.getElementById("timetable-draft-card");
    const draftTableBody = document.getElementById("draft-table-body");
    const uncertaintiesBox = document.getElementById("timetable-uncertainties");
    const addDraftRowBtn = document.getElementById("btn-add-draft-row");
    const discardDraftBtn = document.getElementById("btn-discard-draft");
    const confirmDraftBtn = document.getElementById("btn-confirm-draft");
    const confirmStatus = document.getElementById("draft-confirm-status");
    const checkReplaceExisting = document.getElementById("check-replace-existing");

    const confirmedTableBody = document.getElementById("confirmed-table-body");
    const btnToggleManual = document.getElementById("btn-toggle-manual");
    const formManual = document.getElementById("form-manual-entry");
    const btnCancelManual = document.getElementById("btn-cancel-manual");
    const manualStatus = document.getElementById("manual-status");
    const btnClearConfirmed = document.getElementById("btn-clear-confirmed");
    const step1NextBanner = document.getElementById("step1-next-banner");

    const formExportIcs = document.getElementById("form-export-ics");
    const exportStatus = document.getElementById("export-status");
    const exportIncludePlanner = document.getElementById("export-include-planner");

    const btnApplyPeriodMap = document.getElementById("btn-apply-period-map");
    const mapPeriodCode = document.getElementById("map-period-code");
    const mapStartTime = document.getElementById("map-start-time");
    const mapEndTime = document.getElementById("map-end-time");

    // -------------------------------------------------------------
    // Step 2: Weekly Routines & Preferences Elements
    // -------------------------------------------------------------
    const routineTableBody = document.getElementById("routine-table-body");
    const btnAddRoutineRow = document.getElementById("btn-add-routine-row");
    const btnAddStudyWindowRow = document.getElementById("btn-add-study-window-row");
    const btnLoadSampleRoutines = document.getElementById("btn-load-sample-routines");
    const btnSaveRoutinesBulk = document.getElementById("btn-save-routines-bulk");
    const routineSaveStatus = document.getElementById("routine-save-status");

    const preferencesForm = document.getElementById("planner-preferences-form");
    const prefEarliestTime = document.getElementById("pref-earliest-time");
    const prefLatestTime = document.getElementById("pref-latest-time");
    const prefMaxDailyMinutes = document.getElementById("pref-max-daily-minutes");
    const prefMaxDailyHint = document.getElementById("pref-max-daily-hint");
    const prefSessionMinutes = document.getElementById("pref-session-minutes");
    const prefAiNotes = document.getElementById("pref-ai-notes");
    const prefSaveStatus = document.getElementById("pref-save-status");

    // -------------------------------------------------------------
    // Step 3: Tasks & Deadlines Elements
    // -------------------------------------------------------------
    const courseDatalist = document.getElementById("planner-course-datalist");
    const formAddTask = document.getElementById("form-add-task");
    const taskEditId = document.getElementById("task-edit-id");
    const taskTitle = document.getElementById("task-title");
    const taskCourse = document.getElementById("task-course");
    const taskDeadlineWeekday = document.getElementById("task-deadline-weekday");
    const taskDeadlineDate = document.getElementById("task-deadline-date");
    const taskDeadlineTime = document.getElementById("task-deadline-time");
    const taskEstimatedMinutes = document.getElementById("task-estimated-minutes");
    const taskPriority = document.getElementById("task-priority");
    const taskSplittable = document.getElementById("task-splittable");
    const btnSubmitTask = document.getElementById("btn-submit-task");
    const btnCancelTaskEdit = document.getElementById("btn-cancel-task-edit");
    const taskFormStatus = document.getElementById("task-form-status");
    const tasksTableBody = document.getElementById("tasks-table-body");

    // -------------------------------------------------------------
    // Step 4: AI Schedule Generation & Draft Review Elements
    // -------------------------------------------------------------
    const btnGenerateStudyPlan = document.getElementById("btn-generate-study-plan");
    const btnRescheduleIncomplete = document.getElementById("btn-reschedule-incomplete");
    const plannerGenerateStatus = document.getElementById("planner-generate-status");
    const plannerExplanationBox = document.getElementById("planner-explanation-box");
    const plannerExplanationText = document.getElementById("planner-explanation-text");
    const plannerWarningsBox = document.getElementById("planner-warnings-box");
    const plannerSuggestionsBox = document.getElementById("planner-suggestions-box");
    const plannerDraftCard = document.getElementById("planner-draft-card");
    const plannerDraftTableBody = document.getElementById("planner-draft-table-body");
    const btnAddPlanDraftRow = document.getElementById("btn-add-plan-draft-row");
    const btnDiscardStudyPlan = document.getElementById("btn-discard-study-plan");
    const btnConfirmStudyPlan = document.getElementById("btn-confirm-study-plan");
    const plannerConfirmStatus = document.getElementById("planner-confirm-status");

    // -------------------------------------------------------------
    // Step 5: Master Combined Schedule Elements
    // -------------------------------------------------------------
    const sumClassCount = document.getElementById("sum-class-count");
    const sumRoutineCount = document.getElementById("sum-routine-count");
    const sumWindowHours = document.getElementById("sum-window-hours");
    const sumStudyProgress = document.getElementById("sum-study-progress");
    const scheduleFilterDay = document.getElementById("schedule-filter-day");
    const scheduleFilterCategory = document.getElementById("schedule-filter-category");
    const scheduleShowWindows = document.getElementById("schedule-show-windows");
    const btnToggleAddManualSession = document.getElementById("btn-toggle-add-manual-session");
    const btnRescheduleFromMaster = document.getElementById("btn-reschedule-from-master");
    const formManualStudySession = document.getElementById("form-manual-study-session");
    const manualSessionFormTitle = document.getElementById("manual-session-form-title");
    const manualSessionEditId = document.getElementById("manual-session-edit-id");
    const manualSessionWeekday = document.getElementById("manual-session-weekday");
    const manualSessionTitle = document.getElementById("manual-session-title");
    const manualSessionCourse = document.getElementById("manual-session-course");
    const manualSessionStart = document.getElementById("manual-session-start");
    const manualSessionEnd = document.getElementById("manual-session-end");
    const btnCancelManualSession = document.getElementById("btn-cancel-manual-session");
    const manualSessionStatus = document.getElementById("manual-session-status");
    const masterScheduleTableBody = document.getElementById("master-schedule-table-body");

    let currentDraftId = null;
    let baseUncertainties = [];
    let extracting = false;
    let confirmedEntriesCount = 0;
    let editingClassEntryId = null;
    let currentPlanDraftId = null;
    let plannerState = {
        confirmed_classes: [],
        routines: [],
        preferences: {},
        tasks: [],
        study_sessions: [],
        active_draft: null,
        combined_schedule: [],
        study_windows: [],
        summary: {},
        known_courses: []
    };

    function isLeapYear(year) {
        return (year % 4 === 0 && year % 100 !== 0) || (year % 400 === 0);
    }

    function daysInMonth(month, year) {
        if ([1, 3, 5, 7, 8, 10, 12].includes(month)) return 31;
        if ([4, 6, 9, 11].includes(month)) return 30;
        if (month === 2) return isLeapYear(year) ? 29 : 28;
        return 0;
    }

    function parseDMY(dateStr) {
        if (!dateStr || typeof dateStr !== "string") {
            return { valid: false, error: "Vui lòng nhập ngày tháng." };
        }
        const cleaned = dateStr.trim();
        const match = cleaned.match(/^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})$/);
        if (!match) {
            return { valid: false, error: "Định dạng ngày không hợp lệ. Vui lòng nhập DD/MM/YYYY (ví dụ: 07/03/2026)." };
        }
        const day = parseInt(match[1], 10);
        const month = parseInt(match[2], 10);
        const year = parseInt(match[3], 10);

        if (year < 1900 || year > 2100) {
            return { valid: false, error: `Năm ${year} không hợp lệ (phải từ 1900 đến 2100).` };
        }
        if (month < 1 || month > 12) {
            return { valid: false, error: `Tháng ${month} không hợp lệ (phải từ 01 đến 12).` };
        }
        const maxDays = daysInMonth(month, year);
        if (day < 1 || day > maxDays) {
            if (month === 2 && day === 29 && !isLeapYear(year)) {
                return { valid: false, error: `Năm ${year} không phải năm nhuận (tháng 2 chỉ có 28 ngày).` };
            }
            return { valid: false, error: `Ngày ${day} không hợp lệ (tháng ${month} năm ${year} chỉ có ${maxDays} ngày).` };
        }

        const dd = String(day).padStart(2, "0");
        const mm = String(month).padStart(2, "0");
        const yyyy = String(year);
        return {
            valid: true,
            day,
            month,
            year,
            formatted: `${dd}/${mm}/${yyyy}`,
            isoDate: `${yyyy}-${mm}-${dd}`
        };
    }

    function formatDMY(dateObj) {
        if (!(dateObj instanceof Date) || isNaN(dateObj.getTime())) return "";
        const dd = String(dateObj.getDate()).padStart(2, "0");
        const mm = String(dateObj.getMonth() + 1).padStart(2, "0");
        const yyyy = dateObj.getFullYear();
        return `${dd}/${mm}/${yyyy}`;
    }

    function isoToDMY(isoStr) {
        if (!isoStr || typeof isoStr !== "string") return "";
        const parts = isoStr.split("-");
        if (parts.length === 3) {
            return `${parts[2].padStart(2, "0")}/${parts[1].padStart(2, "0")}/${parts[0]}`;
        }
        return isoStr;
    }

    const WEEKDAY_NAMES = {
        1: "Thứ 2",
        2: "Thứ 3",
        3: "Thứ 4",
        4: "Thứ 5",
        5: "Thứ 6",
        6: "Thứ 7",
        7: "Chủ nhật"
    };

    // Load initial confirmed schedule, restore any active draft, and load planner state
    loadConfirmedSchedule();
    restoreDraftLocally();
    loadFullPlannerState();

    // -------------------------------------------------------------
    // Drag & Drop / File Selection
    // -------------------------------------------------------------
    if (dropzone && fileInput) {
        dropzone.addEventListener("click", () => fileInput.click());

        dropzone.addEventListener("dragover", (e) => {
            e.preventDefault();
            dropzone.classList.add("dragover");
        });

        dropzone.addEventListener("dragleave", () => {
            dropzone.classList.remove("dragover");
        });

        dropzone.addEventListener("drop", (e) => {
            e.preventDefault();
            dropzone.classList.remove("dragover");
            if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                handleFile(e.dataTransfer.files[0]);
            }
        });

        fileInput.addEventListener("change", () => {
            if (fileInput.files && fileInput.files.length > 0) {
                handleFile(fileInput.files[0]);
            }
            fileInput.value = ""; // allow picking the same image again
        });
    }

    if (reuploadBtn && fileInput) {
        reuploadBtn.addEventListener("click", () => {
            fileInput.value = "";
            fileInput.click();
        });
    }

    function handleFile(file) {
        if (!file) return;
        if (extracting) return;
        const validTypes = ["image/png", "image/jpeg", "image/webp"];
        if (!validTypes.includes(file.type)) {
            alert("Định dạng tệp không được hỗ trợ. Vui lòng chọn ảnh PNG, JPEG hoặc WebP.");
            return;
        }
        if (file.size > 15 * 1024 * 1024) {
            alert("Kích thước tệp quá lớn (> 15MB). Vui lòng chọn ảnh dung lượng nhỏ hơn.");
            return;
        }

        extracting = true;
        const reader = new FileReader();
        reader.onerror = () => {
            extracting = false;
            alert("Không thể đọc tệp ảnh đã chọn.");
        };
        reader.onload = (e) => {
            const dataUrl = e.target.result;
            showImagePreview(file.name, dataUrl, file.size);
            extractTimetableFromImage(file.name, dataUrl);
        };
        reader.readAsDataURL(file);
    }

    function showImagePreview(name, dataUrl, sizeBytes) {
        filenameEl.textContent = name;
        previewImg.src = dataUrl;
        const kb = Math.round(sizeBytes / 1024);
        imageMetaEl.textContent = `Dung lượng: ${kb} KB | Trạng thái: Sẵn sàng phân tích`;
        dropzone.style.display = "none";
        previewContainer.style.display = "block";
    }

    // -------------------------------------------------------------
    // VLM Extraction
    // -------------------------------------------------------------
    async function extractTimetableFromImage(filename, base64Data) {
        loadingBox.style.display = "block";
        extractStatus.textContent = "Đang phân tích hình ảnh bằng mô hình thị giác cục bộ...";
        draftCard.style.display = "none";

        try {
            const resp = await fetch("/api/timetable/extract", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    filename: filename,
                    image_base64: base64Data
                })
            });

            const data = await resp.json();
            if (!resp.ok) {
                const detail = data.detail || "Lỗi khi trích xuất thời khóa biểu";
                throw new Error(detail);
            }

            currentDraftId = data.draft_id;
            renderDraft(data);
        } catch (err) {
            loadingBox.style.display = "none";
            alert(`Không thể trích xuất ảnh: ${err.message}`);
        } finally {
            loadingBox.style.display = "none";
            extracting = false;
        }
    }

    // -------------------------------------------------------------
    // Render Draft
    // -------------------------------------------------------------
    function renderDraft(data) {
        draftTableBody.replaceChildren();
        const entries = data.entries || [];
        const uncertainties = data.uncertainties || [];
        baseUncertainties = Array.isArray(uncertainties) ? [...uncertainties] : [];

        // Render rows (retain partial_entries when full scheduled entries are absent — YC-06)
        const rowsToRender = entries.length > 0
            ? entries
            : (Array.isArray(data.partial_entries) && data.partial_entries.length > 0 ? data.partial_entries : []);
        if (rowsToRender.length === 0) {
            addEmptyDraftRow();
        } else {
            rowsToRender.forEach(e => appendDraftRow(e));
        }

        refreshDraftLiveWarnings();
        saveDraftStateLocally();
        confirmStatus.textContent = "";

        if (checkReplaceExisting) {
            const replaceParent = checkReplaceExisting.closest("label");
            let hint = replaceParent ? replaceParent.querySelector(".replace-hint") : null;
            if (confirmedEntriesCount > 0) {
                if (!hint && replaceParent) {
                    hint = document.createElement("span");
                    hint.className = "replace-hint";
                    hint.style.cssText = "display: block; font-size: 0.78rem; color: #b45309; margin-top: 4px;";
                    replaceParent.appendChild(hint);
                }
                if (hint) {
                    hint.textContent = `(Hiện đã lưu ${confirmedEntriesCount} môn. Đánh dấu để thay thế toàn bộ lịch cũ; bỏ chọn để ghép thêm)`;
                }
            } else if (hint) {
                hint.textContent = "";
            }
        }

        draftCard.style.display = "block";
        draftCard.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function refreshDraftLiveWarnings() {
        if (!uncertaintiesBox || !draftTableBody) return;
        const liveWarnings = [...baseUncertainties];
        const rows = Array.from(draftTableBody.querySelectorAll("tr"));
        const timedByDay = {};

        rows.forEach((tr, idx) => {
            tr.classList.remove("timetable-conflict-row");
            const w = tr.querySelector(".draft-weekday")?.value || "";
            const course = (tr.querySelector(".draft-course")?.value || "").trim();
            const start = (tr.querySelector(".draft-start")?.value || "").trim();
            const end = (tr.querySelector(".draft-end")?.value || "").trim();

            if (!w || !course || !start || !end) {
                liveWarnings.push(`Dòng ${idx + 1} (${course || "Chưa có tên môn"}): Còn thiếu thông tin thứ hoặc giờ bắt đầu/kết thúc.`);
                tr.classList.add("timetable-conflict-row");
                return;
            }
            if (end <= start) {
                liveWarnings.push(`Dòng ${idx + 1} (${course}): Giờ kết thúc (${end}) phải lớn hơn giờ bắt đầu (${start}).`);
                tr.classList.add("timetable-conflict-row");
                return;
            }
            if (!timedByDay[w]) timedByDay[w] = [];
            timedByDay[w].push({ idx, tr, course, start, end });
        });

        Object.keys(timedByDay).forEach(w => {
            const list = timedByDay[w];
            const dayName = WEEKDAY_NAMES[w] || `Thứ ${w}`;
            for (let i = 0; i < list.length; i++) {
                for (let j = i + 1; j < list.length; j++) {
                    const a = list[i];
                    const b = list[j];
                    if (a.start < b.end && b.start < a.end) {
                        const msg = `Trùng giờ trên bản nháp (${dayName}): '${a.course}' (${a.start}-${a.end}) và '${b.course}' (${b.start}-${b.end}).`;
                        if (!liveWarnings.includes(msg)) liveWarnings.push(msg);
                        a.tr.classList.add("timetable-conflict-row");
                        b.tr.classList.add("timetable-conflict-row");
                    }
                }
            }
        });

        const uniqueWarnings = Array.from(new Set(liveWarnings));
        if (uniqueWarnings.length > 0) {
            uncertaintiesBox.style.display = "block";
            uncertaintiesBox.innerHTML = `<strong>⚠️ Lưu ý cần kiểm tra lại trên ảnh / bản nháp:</strong><ul style="margin: 6px 0 0 18px; padding: 0;">` +
                uniqueWarnings.map(u => `<li>${escapeHtml(u)}</li>`).join("") +
                `</ul>`;
        } else {
            uncertaintiesBox.style.display = "none";
            uncertaintiesBox.innerHTML = "";
        }
    }

    function saveDraftStateLocally() {
        if (!currentDraftId) return;
        try {
            const rows = [];
            draftTableBody.querySelectorAll("tr").forEach(tr => {
                const w = tr.querySelector(".draft-weekday").value;
                rows.push({
                    weekday: w ? parseInt(w, 10) : null,
                    course: tr.querySelector(".draft-course").value,
                    period: tr.querySelector(".draft-period").value,
                    start_time: tr.querySelector(".draft-start").value,
                    end_time: tr.querySelector(".draft-end").value,
                    room: tr.querySelector(".draft-room").value
                });
            });
            localStorage.setItem((storageScope === "local" ? "timetable_active_draft_id" : `timetable_active_draft_id-${storageScope}`), currentDraftId);
            localStorage.setItem((storageScope === "local" ? "timetable_active_draft_state" : `timetable_active_draft_state-${storageScope}`), JSON.stringify(rows));
        } catch (e) {
            console.warn("Failed to persist draft state locally:", e);
        }
    }

    function clearDraftStateLocally() {
        try {
            localStorage.removeItem((storageScope === "local" ? "timetable_active_draft_id" : `timetable_active_draft_id-${storageScope}`));
            localStorage.removeItem((storageScope === "local" ? "timetable_active_draft_state" : `timetable_active_draft_state-${storageScope}`));
        } catch (e) {}
    }

    async function restoreDraftLocally() {
        try {
            const localId = localStorage.getItem((storageScope === "local" ? "timetable_active_draft_id" : `timetable_active_draft_id-${storageScope}`));
            const linkedId = new URLSearchParams(window.location.search).get("timetable_draft");
            const savedId = linkedId || localId;
            if (!savedId) return;

            const resp = await fetch(`/api/timetable/drafts/${savedId}`);
            if (!resp.ok) {
                clearDraftStateLocally();
                return;
            }
            const data = await resp.json();
            // A saved draft may already have been confirmed before a interrupted reload.
            if (data.status === "confirmed") {
                clearDraftStateLocally();
                return;
            }
            if (extracting) return;
            currentDraftId = savedId;

            // Restore preview image
            if (data.image_hash) {
                filenameEl.textContent = data.filename || "Lịch học đã lưu";
                previewImg.src = `/api/timetable/images/${data.image_hash}`;
                imageMetaEl.textContent = `Bản nháp đã tải lại (${savedId.substring(0, 8)}...) | Trạng thái: Đang chỉnh sửa`;
                dropzone.style.display = "none";
                previewContainer.style.display = "block";
            }

            // Prefer local user edits if available, otherwise original extracted entries
            let entriesToRender = data.entries || [];
            const savedStateStr = localId === savedId ? localStorage.getItem((storageScope === "local" ? "timetable_active_draft_state" : `timetable_active_draft_state-${storageScope}`)) : null;
            if (savedStateStr) {
                try {
                    const parsedState = JSON.parse(savedStateStr);
                    if (Array.isArray(parsedState)) {
                        entriesToRender = parsedState;
                    }
                } catch (e) {}
            }

            renderDraft({
                entries: entriesToRender,
                uncertainties: data.uncertainties || []
            });
        } catch (err) {
            console.warn("Failed to restore draft locally:", err);
        }
    }

    function appendDraftRow(item = {}) {
        const tr = document.createElement("tr");

        const weekdayVal = (item.weekday !== null && item.weekday !== undefined && item.weekday !== "") ? parseInt(item.weekday, 10) : "";
        const courseVal = item.course || "";
        const periodVal = item.period || "";
        const startVal = item.start_time || "";
        const endVal = item.end_time || "";
        const roomVal = item.room || "";

        tr.innerHTML = `
            <td>
                <select class="form-input draft-weekday ${weekdayVal === "" ? "draft-field-missing" : ""}" style="padding: 4px;" title="${weekdayVal === "" ? "Còn thiếu thứ học — vui lòng chọn thứ" : ""}">
                    <option value="" ${weekdayVal === "" ? "selected" : ""}>-- Chọn thứ --</option>
                    <option value="1" ${weekdayVal === 1 ? "selected" : ""}>Thứ 2</option>
                    <option value="2" ${weekdayVal === 2 ? "selected" : ""}>Thứ 3</option>
                    <option value="3" ${weekdayVal === 3 ? "selected" : ""}>Thứ 4</option>
                    <option value="4" ${weekdayVal === 4 ? "selected" : ""}>Thứ 5</option>
                    <option value="5" ${weekdayVal === 5 ? "selected" : ""}>Thứ 6</option>
                    <option value="6" ${weekdayVal === 6 ? "selected" : ""}>Thứ 7</option>
                    <option value="7" ${weekdayVal === 7 ? "selected" : ""}>Chủ nhật</option>
                </select>
            </td>
            <td>
                <input type="text" class="form-input draft-course ${!courseVal.trim() ? "draft-field-missing" : ""}" required value="${escapeHtml(courseVal)}" placeholder="Tên môn học (cần điền)">
            </td>
            <td>
                <input type="text" class="form-input draft-period" value="${escapeHtml(periodVal)}" placeholder="Tiết (1-3)">
            </td>
            <td>
                <input type="time" class="form-input draft-start ${!startVal ? "draft-field-missing" : ""}" required value="${escapeHtml(startVal)}" title="${!startVal ? "Còn thiếu giờ bắt đầu — vui lòng nhập giờ" : ""}">
            </td>
            <td>
                <input type="time" class="form-input draft-end ${!endVal ? "draft-field-missing" : ""}" required value="${escapeHtml(endVal)}" title="${!endVal ? "Còn thiếu giờ kết thúc — vui lòng nhập giờ" : ""}">
            </td>
            <td>
                <input type="text" class="form-input draft-room" value="${escapeHtml(roomVal)}" placeholder="Phòng">
            </td>
            <td style="text-align: center;">
                <button type="button" class="btn btn-sm btn-outline btn-del-row" style="padding: 2px 6px; color: #dc2626;" title="Xóa dòng">&times;</button>
            </td>
        `;

        const syncMissingHighlight = () => {
            const wEl = tr.querySelector(".draft-weekday");
            const cEl = tr.querySelector(".draft-course");
            const sEl = tr.querySelector(".draft-start");
            const eEl = tr.querySelector(".draft-end");
            if (wEl) wEl.classList.toggle("draft-field-missing", !wEl.value);
            if (cEl) cEl.classList.toggle("draft-field-missing", !cEl.value.trim());
            if (sEl) sEl.classList.toggle("draft-field-missing", !sEl.value);
            if (eEl) eEl.classList.toggle("draft-field-missing", !eEl.value);
            refreshDraftLiveWarnings();
            saveDraftStateLocally();
        };

        tr.addEventListener("input", syncMissingHighlight);
        tr.addEventListener("change", syncMissingHighlight);

        tr.querySelector(".btn-del-row").addEventListener("click", () => {
            tr.remove();
            refreshDraftLiveWarnings();
            saveDraftStateLocally();
        });

        draftTableBody.appendChild(tr);
    }

    function addEmptyDraftRow() {
        appendDraftRow({
            weekday: null,
            course: "",
            period: "",
            start_time: "",
            end_time: "",
            room: ""
        });
        refreshDraftLiveWarnings();
        saveDraftStateLocally();
    }

    if (addDraftRowBtn) {
        addDraftRowBtn.addEventListener("click", () => addEmptyDraftRow());
    }

    if (discardDraftBtn) {
        discardDraftBtn.addEventListener("click", () => {
            if (confirm("Bạn có chắc chắn muốn hủy bản nháp này?")) {
                draftCard.style.display = "none";
                currentDraftId = null;
                clearDraftStateLocally();
            }
        });
    }

    // -------------------------------------------------------------
    // Period Mapping Tool (User-Supplied Only)
    // -------------------------------------------------------------

    if (btnApplyPeriodMap) {
        btnApplyPeriodMap.addEventListener("click", () => {
            const targetPeriod = (mapPeriodCode.value || "").trim().toLowerCase();
            const sTime = mapStartTime.value;
            const eTime = mapEndTime.value;

            if (!targetPeriod || !sTime || !eTime) {
                confirmStatus.textContent = "Vui lòng nhập tiết, giờ bắt đầu và giờ kết thúc.";
                confirmStatus.style.color = "#dc2626";
                return;
            }
            if (eTime <= sTime) {
                confirmStatus.textContent = "Giờ kết thúc phải sau giờ bắt đầu.";
                confirmStatus.style.color = "#dc2626";
                return;
            }

            let count = 0;
            draftTableBody.querySelectorAll("tr").forEach(tr => {
                const periodInput = tr.querySelector(".draft-period");
                const startInput = tr.querySelector(".draft-start");
                const endInput = tr.querySelector(".draft-end");

                if ((periodInput.value || "").trim().toLowerCase() === targetPeriod) {
                    startInput.value = sTime;
                    endInput.value = eTime;
                    count++;
                }
            });
            saveDraftStateLocally();
            confirmStatus.textContent = `Đã cập nhật giờ cho ${count} môn có tiết "${targetPeriod}".`;
            confirmStatus.style.color = count ? "#16a34a" : "#dc2626";
        });
    }

    // -------------------------------------------------------------
    // Confirm Draft
    // -------------------------------------------------------------
    if (confirmDraftBtn) {
        confirmDraftBtn.addEventListener("click", async () => {
            if (!currentDraftId) {
                alert("Không có bản nháp nào đang mở.");
                return;
            }

            const rows = draftTableBody.querySelectorAll("tr");
            const entries = [];
            const errors = [];

            // Clear previous highlight
            rows.forEach(r => r.classList.remove("timetable-conflict-row"));

            rows.forEach((tr, idx) => {
                const weekdayRaw = tr.querySelector(".draft-weekday").value;
                const course = tr.querySelector(".draft-course").value.trim();
                const period = tr.querySelector(".draft-period").value.trim() || null;
                const startTime = tr.querySelector(".draft-start").value.trim();
                const endTime = tr.querySelector(".draft-end").value.trim();
                const room = tr.querySelector(".draft-room").value.trim() || null;

                if (!weekdayRaw || isNaN(parseInt(weekdayRaw, 10))) {
                    errors.push(`Dòng ${idx + 1} (${course || 'môn học'}): Vui lòng chọn thứ trong tuần.`);
                    tr.classList.add("timetable-conflict-row");
                    return;
                }
                const weekday = parseInt(weekdayRaw, 10);

                if (!course) {
                    errors.push(`Dòng ${idx + 1}: Vui lòng nhập tên môn học.`);
                    tr.classList.add("timetable-conflict-row");
                    return;
                }
                if (!startTime || !endTime) {
                    errors.push(`Dòng ${idx + 1} (${course}): Vui lòng nhập đầy đủ giờ bắt đầu và kết thúc.`);
                    tr.classList.add("timetable-conflict-row");
                    return;
                }
                if (endTime <= startTime) {
                    errors.push(`Dòng ${idx + 1} (${course}): Giờ kết thúc (${endTime}) phải sau giờ bắt đầu (${startTime}).`);
                    tr.classList.add("timetable-conflict-row");
                    return;
                }

                entries.push({
                    weekday,
                    course,
                    start_time: startTime,
                    end_time: endTime,
                    room,
                    period
                });
            });

            if (errors.length) {
                confirmStatus.textContent = errors.join(" ");
                confirmStatus.style.color = "#dc2626";
                return;
            }
            if (entries.length === 0) {
                alert("Danh sách môn học trống.");
                return;
            }

            confirmStatus.textContent = "Đang lưu lịch học...";
            confirmStatus.style.color = "var(--text-secondary)";
            confirmDraftBtn.disabled = true;

            try {
                const resp = await fetch("/api/timetable/confirm", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        draft_id: currentDraftId,
                        entries: entries,
                        replace_existing: checkReplaceExisting.checked,
                        clear_existing: checkReplaceExisting.checked
                    })
                });

                const data = await resp.json();
                if (!resp.ok) {
                    const detail = data.detail || "Không thể xác nhận lịch học";
                    if (resp.status === 409) {
                        confirmStatus.textContent = `⚠️ Xung đột lịch: ${detail}`;
                        confirmStatus.style.color = "#dc2626";
                    } else {
                        confirmStatus.textContent = `❌ Lỗi: ${detail}`;
                        confirmStatus.style.color = "#dc2626";
                    }
                    return;
                }

                confirmStatus.textContent = `✅ Đã lưu thành công ${data.saved_entries} buổi học vào lịch của bạn!`;
                confirmStatus.style.color = "#16a34a";
                draftCard.style.display = "none";
                currentDraftId = null;
                clearDraftStateLocally();
                await loadConfirmedSchedule();
                await loadFullPlannerState();
                if (step1NextBanner) {
                    step1NextBanner.style.display = "flex";
                }
            } catch (err) {
                confirmStatus.textContent = `❌ Lỗi mạng: ${err.message}`;
                confirmStatus.style.color = "#dc2626";
            } finally {
                confirmDraftBtn.disabled = false;
            }
        });
    }

    // -------------------------------------------------------------
    // Confirmed Schedule Table & Manual Entry
    // -------------------------------------------------------------
    async function loadConfirmedSchedule() {
        try {
            const resp = await fetch("/api/timetable");
            if (!resp.ok) throw new Error("Không thể tải lịch học");
            const data = await resp.json();
            const entries = data.entries || [];
            renderConfirmedTable(entries);
            if (step1NextBanner) {
                step1NextBanner.style.display = entries.length > 0 ? "flex" : "none";
            }
        } catch (err) {
            confirmedTableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color: #dc2626;">Lỗi tải lịch: ${escapeHtml(err.message)}</td></tr>`;
        }
    }

    function renderConfirmedTable(entries) {
        confirmedEntriesCount = entries ? entries.length : 0;
        confirmedTableBody.replaceChildren();
        if (entries.length === 0) {
            confirmedTableBody.innerHTML = `<tr><td colspan="5" style="text-align:center;" class="text-muted">Chưa có môn học nào được lưu. Hãy tải ảnh lịch hoặc nhập tay.</td></tr>`;
            return;
        }

        entries.forEach(e => {
            const tr = document.createElement("tr");
            const dayName = WEEKDAY_NAMES[e.weekday] || `Thứ ${e.weekday}`;
            const timeStr = `${e.start_time} - ${e.end_time}`;
            const roomStr = e.room || "—";

            tr.innerHTML = `
                <td><strong>${escapeHtml(dayName)}</strong></td>
                <td>${escapeHtml(e.course)}</td>
                <td><code>${escapeHtml(timeStr)}</code></td>
                <td>${escapeHtml(roomStr)}</td>
                <td style="text-align: center; white-space: nowrap;">
                    <button type="button" class="btn btn-sm btn-outline btn-edit-class" style="padding: 1px 6px; margin-right: 4px;" title="Sửa buổi học">✏️</button>
                    <button type="button" class="btn btn-sm btn-outline btn-del-class" style="padding: 1px 6px; color: #dc2626;" title="Xóa môn">&times;</button>
                </td>
            `;

            tr.querySelector(".btn-edit-class").addEventListener("click", () => {
                editingClassEntryId = e.id;
                document.getElementById("manual-weekday").value = String(e.weekday);
                document.getElementById("manual-course").value = e.course;
                document.getElementById("manual-start").value = e.start_time;
                document.getElementById("manual-end").value = e.end_time;
                document.getElementById("manual-room").value = e.room || "";
                formManual.style.display = "block";
                manualStatus.textContent = `Đang sửa môn "${e.course}" (${dayName})...`;
                manualStatus.style.color = "var(--text-secondary)";
            });

            tr.querySelector(".btn-del-class").addEventListener("click", async () => {
                if (confirm(`Bạn có chắc muốn xóa môn "${e.course}" (${dayName})?`)) {
                    await deleteEntry(e.id);
                }
            });

            confirmedTableBody.appendChild(tr);
        });
    }

    async function deleteEntry(entryId) {
        try {
            const resp = await fetch(`/api/timetable/entries/${entryId}`, { method: "DELETE" });
            if (resp.ok) {
                await loadConfirmedSchedule();
                await loadFullPlannerState();
            } else {
                alert("Không thể xóa môn học.");
            }
        } catch (e) {
            alert(`Lỗi: ${e.message}`);
        }
    }

    if (btnClearConfirmed) {
        btnClearConfirmed.addEventListener("click", async () => {
            if (confirm("Bạn có chắc chắn muốn xóa TOÀN BỘ lịch học đã lưu không?")) {
                try {
                    const resp = await fetch("/api/timetable", { method: "DELETE" });
                    if (resp.ok) {
                        await loadConfirmedSchedule();
                        await loadFullPlannerState();
                    }
                } catch (e) {
                    alert(`Lỗi: ${e.message}`);
                }
            }
        });
    }

    if (btnToggleManual && formManual) {
        btnToggleManual.addEventListener("click", () => {
            editingClassEntryId = null;
            formManual.reset();
            manualStatus.textContent = "";
            formManual.style.display = formManual.style.display === "none" ? "block" : "none";
        });
    }

    if (btnCancelManual && formManual) {
        btnCancelManual.addEventListener("click", () => {
            editingClassEntryId = null;
            formManual.style.display = "none";
            manualStatus.textContent = "";
        });
    }

    if (formManual) {
        formManual.addEventListener("submit", async (e) => {
            e.preventDefault();
            const weekday = parseInt(document.getElementById("manual-weekday").value, 10);
            const course = document.getElementById("manual-course").value.trim();
            const start = document.getElementById("manual-start").value.trim();
            const end = document.getElementById("manual-end").value.trim();
            const room = document.getElementById("manual-room").value.trim() || null;

            if (end <= start) {
                manualStatus.textContent = "❌ Giờ kết thúc phải lớn hơn giờ bắt đầu.";
                manualStatus.style.color = "#dc2626";
                return;
            }

            try {
                const isEdit = editingClassEntryId !== null;
                const url = isEdit ? `/api/timetable/entries/${editingClassEntryId}` : "/api/timetable/manual";
                const method = isEdit ? "PUT" : "POST";
                const resp = await fetch(url, {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        weekday: weekday,
                        course: course,
                        start_time: start,
                        end_time: end,
                        room: room
                    })
                });
                const data = await resp.json();
                if (!resp.ok) {
                    manualStatus.textContent = `⚠️ ${data.detail || "Không thể lưu môn học"}`;
                    manualStatus.style.color = "#dc2626";
                    return;
                }

                manualStatus.textContent = isEdit ? "✅ Đã cập nhật môn học thành công!" : "✅ Đã thêm môn học thành công!";
                manualStatus.style.color = "#16a34a";
                editingClassEntryId = null;
                formManual.reset();
                await loadConfirmedSchedule();
                await loadFullPlannerState();
            } catch (err) {
                manualStatus.textContent = `❌ Lỗi mạng: ${err.message}`;
                manualStatus.style.color = "#dc2626";
            }
        });
    }

    // -------------------------------------------------------------
    // ICS Export & DD/MM/YYYY Date Controls
    // -------------------------------------------------------------
    const inputStartDate = document.getElementById("export-start-date");
    const inputEndDate = document.getElementById("export-end-date");
    const pickerStartDate = document.getElementById("export-start-date-picker");
    const pickerEndDate = document.getElementById("export-end-date-picker");

    // Initialize sensible semester defaults if empty
    function initExportDates() {
        if (inputStartDate && !inputStartDate.value) {
            const today = new Date();
            inputStartDate.value = formatDMY(today);
            if (pickerStartDate) pickerStartDate.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
        }
        if (inputEndDate && !inputEndDate.value) {
            const endDate = new Date();
            endDate.setDate(endDate.getDate() + 16 * 7); // +16 weeks (1 semester)
            inputEndDate.value = formatDMY(endDate);
            if (pickerEndDate) pickerEndDate.value = `${endDate.getFullYear()}-${String(endDate.getMonth() + 1).padStart(2, "0")}-${String(endDate.getDate()).padStart(2, "0")}`;
        }
    }
    initExportDates();

    // Toggle button for native date pickers
    document.querySelectorAll(".btn-picker-toggle").forEach(btn => {
        btn.addEventListener("click", () => {
            const targetId = btn.getAttribute("data-for");
            const picker = document.getElementById(`${targetId}-picker`);
            const textInput = document.getElementById(targetId);
            if (picker) {
                if (textInput && textInput.value) {
                    const parsed = parseDMY(textInput.value);
                    if (parsed.valid) {
                        picker.value = parsed.isoDate;
                    }
                }
                if (typeof picker.showPicker === "function") {
                    picker.showPicker();
                } else {
                    picker.focus();
                    picker.click();
                }
            }
        });
    });

    if (pickerStartDate && inputStartDate) {
        pickerStartDate.addEventListener("change", () => {
            if (pickerStartDate.value) {
                inputStartDate.value = isoToDMY(pickerStartDate.value);
            }
        });
    }

    if (pickerEndDate && inputEndDate) {
        pickerEndDate.addEventListener("change", () => {
            if (pickerEndDate.value) {
                inputEndDate.value = isoToDMY(pickerEndDate.value);
            }
        });
    }

    // Auto-normalize date format on blur without interfering with typing
    if (inputStartDate) {
        inputStartDate.addEventListener("blur", () => {
            if (inputStartDate.value.trim()) {
                const p = parseDMY(inputStartDate.value);
                if (p.valid) {
                    inputStartDate.value = p.formatted;
                    if (pickerStartDate) pickerStartDate.value = p.isoDate;
                }
            }
        });
    }

    if (inputEndDate) {
        inputEndDate.addEventListener("blur", () => {
            if (inputEndDate.value.trim()) {
                const p = parseDMY(inputEndDate.value);
                if (p.valid) {
                    inputEndDate.value = p.formatted;
                    if (pickerEndDate) pickerEndDate.value = p.isoDate;
                }
            }
        });
    }

    if (formExportIcs) {
        formExportIcs.addEventListener("submit", async (e) => {
            e.preventDefault();

            if (currentDraftId && draftCard && draftCard.style.display !== "none" && draftTableBody.querySelectorAll("tr").length > 0) {
                exportStatus.textContent = "⚠️ Bạn đang có bản nháp thời khóa biểu chưa xác nhận trên màn hình. Vui lòng nhấn 'Xác nhận & Lưu vào lịch học' trước khi tải tệp .ics để lịch xuất chứa đầy đủ các môn mới nhất.";
                exportStatus.style.color = "#dc2626";
                return;
            }

            if (confirmedEntriesCount === 0 && !exportIncludePlanner?.checked) {
                exportStatus.textContent = "⚠️ Thời khóa biểu hiện chưa có môn học nào để xuất. Vui lòng thêm môn học thủ công hoặc tải ảnh thời khóa biểu trước.";
                exportStatus.style.color = "#dc2626";
                return;
            }

            const sRaw = (document.getElementById("export-start-date")?.value || "").trim();
            const eRaw = (document.getElementById("export-end-date")?.value || "").trim();

            if (!sRaw || !eRaw) {
                exportStatus.textContent = "❌ Vui lòng nhập đầy đủ ngày bắt đầu và kết thúc kỳ học theo định dạng DD/MM/YYYY.";
                exportStatus.style.color = "#dc2626";
                return;
            }

            const sParsed = parseDMY(sRaw);
            if (!sParsed.valid) {
                exportStatus.textContent = `❌ Ngày bắt đầu không hợp lệ: ${sParsed.error}`;
                exportStatus.style.color = "#dc2626";
                return;
            }

            const eParsed = parseDMY(eRaw);
            if (!eParsed.valid) {
                exportStatus.textContent = `❌ Ngày kết thúc không hợp lệ: ${eParsed.error}`;
                exportStatus.style.color = "#dc2626";
                return;
            }

            if (eParsed.isoDate < sParsed.isoDate) {
                exportStatus.textContent = "❌ Ngày kết thúc kỳ học phải sau ngày bắt đầu.";
                exportStatus.style.color = "#dc2626";
                return;
            }

            exportStatus.textContent = "Đang tạo tệp lịch .ics...";
            exportStatus.style.color = "var(--text-secondary)";

            try {
                const includePlanner = exportIncludePlanner && exportIncludePlanner.checked ? "true" : "false";
                const url = `/api/timetable/export.ics?start_date=${encodeURIComponent(sParsed.isoDate)}&end_date=${encodeURIComponent(eParsed.isoDate)}&include_planner=${includePlanner}&reminder_minutes=${encodeURIComponent(document.getElementById("export-reminder").value)}`;
                const resp = await fetch(url);
                if (!resp.ok) {
                    const errJson = await resp.json().catch(() => ({}));
                    throw new Error(errJson.detail || "Không thể xuất file iCalendar");
                }

                const blob = await resp.blob();
                if (blob.size === 0) {
                    throw new Error("Tệp xuất bị rỗng từ máy chủ.");
                }

                const downloadUrl = window.URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = downloadUrl;
                const fileStart = sParsed.formatted.replace(/\//g, "-");
                const fileEnd = eParsed.formatted.replace(/\//g, "-");
                a.download = `timetable_${fileStart}_${fileEnd}.ics`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(downloadUrl);

                exportStatus.textContent = `✅ Đã tạo tệp lịch .ics thành công (${sParsed.formatted} - ${eParsed.formatted}). Bạn có thể nhập vào ứng dụng lịch và kiểm tra lời nhắc.`;
                exportStatus.style.color = "#16a34a";
            } catch (err) {
                exportStatus.textContent = `❌ ${err.message}`;
                exportStatus.style.color = "#dc2626";
            }
        });
    }

    // =============================================================
    // PERSONAL AI STUDY SCHEDULE ASSISTANT (STEPS 2 - 5)
    // =============================================================

    async function loadFullPlannerState() {
        try {
            const resp = await fetch("/api/timetable/planner/state");
            if (!resp.ok) return;
            const data = await resp.json();
            plannerState = data;

            renderCourseDatalist(data.known_courses || []);
            renderRoutinesTable(data.routines || []);
            renderPreferencesForm(data.preferences || {});
            renderTasksTable(data.tasks || []);
            if (data.active_draft) {
                renderPlanDraft(data.active_draft);
            }
            renderMasterSchedule();
        } catch (err) {
            console.warn("Could not load planner state:", err);
        }
    }

    function renderCourseDatalist(courses) {
        if (!courseDatalist) return;
        courseDatalist.replaceChildren();
        courses.forEach(c => {
            const opt = document.createElement("option");
            opt.value = c;
            courseDatalist.appendChild(opt);
        });
    }

    // -------------------------------------------------------------
    // Step 2: Weekly Routines Table & Preferences
    // -------------------------------------------------------------
    function renderRoutinesTable(routines) {
        if (!routineTableBody) return;
        routineTableBody.replaceChildren();
        if (!routines || routines.length === 0) {
            routineTableBody.innerHTML = `<tr><td colspan="7" style="text-align: center;" class="text-muted">Chưa có hoạt động sinh hoạt hoặc khung giờ có thể học nào. Bấm "＋ Thêm hoạt động", "＋ Thêm khung giờ có thể học" hoặc "✨ Điền mẫu gợi ý".</td></tr>`;
            return;
        }
        routines.forEach(r => appendRoutineRow(r));
    }

    function appendRoutineRow(item = {}) {
        if (!routineTableBody) return;
        const emptyCell = routineTableBody.querySelector("td[colspan]");
        if (emptyCell) routineTableBody.replaceChildren();

        const tr = document.createElement("tr");
        const wVal = item.weekday || 1;
        const stVal = item.start_time || "18:00";
        const etVal = item.end_time || "20:00";
        const titleVal = item.title || "";
        const actVal = item.activity_type || "other";
        const flexVal = item.flexibility || (actVal === "study_window" ? "study_window" : (actVal === "rest" ? "protected" : "fixed"));

        tr.innerHTML = `
            <td>
                <select class="form-input routine-weekday" style="padding: 4px;">
                    <option value="1" ${wVal === 1 ? "selected" : ""}>Thứ 2</option>
                    <option value="2" ${wVal === 2 ? "selected" : ""}>Thứ 3</option>
                    <option value="3" ${wVal === 3 ? "selected" : ""}>Thứ 4</option>
                    <option value="4" ${wVal === 4 ? "selected" : ""}>Thứ 5</option>
                    <option value="5" ${wVal === 5 ? "selected" : ""}>Thứ 6</option>
                    <option value="6" ${wVal === 6 ? "selected" : ""}>Thứ 7</option>
                    <option value="7" ${wVal === 7 ? "selected" : ""}>Chủ nhật</option>
                </select>
            </td>
            <td>
                <input type="time" class="form-input routine-start" required value="${escapeHtml(stVal)}">
            </td>
            <td>
                <input type="time" class="form-input routine-end" required value="${escapeHtml(etVal)}">
            </td>
            <td>
                <input type="text" class="form-input routine-title" required value="${escapeHtml(titleVal)}" placeholder="VD: Tập gym, Nghỉ trưa, Tự học buổi tối...">
            </td>
            <td>
                <select class="form-input routine-type" style="padding: 4px;">
                    <option value="work" ${actVal === "work" ? "selected" : ""}>💼 Đi làm</option>
                    <option value="extra_class" ${actVal === "extra_class" ? "selected" : ""}>📒 Học thêm</option>
                    <option value="gym" ${actVal === "gym" ? "selected" : ""}>🏋️ Tập gym / thể dục</option>
                    <option value="sport" ${actVal === "sport" ? "selected" : ""}>⚽ Chơi thể thao</option>
                    <option value="personal" ${actVal === "personal" ? "selected" : ""}>🎉 Đi chơi / cá nhân</option>
                    <option value="rest" ${actVal === "rest" ? "selected" : ""}>😴 Nghỉ ngơi, ăn uống</option>
                    <option value="study_window" ${actVal === "study_window" ? "selected" : ""}>📖 Khung giờ có thể học</option>
                    <option value="other" ${actVal === "other" ? "selected" : ""}>📌 Hoạt động khác</option>
                </select>
            </td>
            <td>
                <select class="form-input routine-flex" style="padding: 4px;">
                    <option value="fixed" ${flexVal === "fixed" ? "selected" : ""}>🔒 Cố định (Không tự đổi)</option>
                    <option value="protected" ${flexVal === "protected" ? "selected" : ""}>🛡️ Cần giữ (Bảo vệ nghỉ ngơi)</option>
                    <option value="flexible" ${flexVal === "flexible" ? "selected" : ""}>🔄 Có thể đổi giờ (Đề xuất)</option>
                    <option value="study_window" ${flexVal === "study_window" ? "selected" : ""}>📖 Có thể học (Xếp tự học)</option>
                </select>
            </td>
            <td style="text-align: center;">
                <button type="button" class="btn btn-sm btn-outline btn-del-routine" style="padding: 2px 6px; color: #dc2626;" title="Xóa dòng">&times;</button>
            </td>
        `;

        const typeSelect = tr.querySelector(".routine-type");
        const flexSelect = tr.querySelector(".routine-flex");
        const titleInput = tr.querySelector(".routine-title");

        typeSelect.addEventListener("change", () => {
            if (typeSelect.value === "study_window") {
                flexSelect.value = "study_window";
                if (!titleInput.value.trim()) titleInput.value = "Khung giờ có thể học";
            } else if (typeSelect.value === "rest") {
                flexSelect.value = "protected";
                if (!titleInput.value.trim()) titleInput.value = "Nghỉ ngơi / Ăn uống";
            } else if (flexSelect.value === "study_window") {
                flexSelect.value = "fixed";
            }
        });

        flexSelect.addEventListener("change", () => {
            if (flexSelect.value === "study_window") {
                typeSelect.value = "study_window";
                if (!titleInput.value.trim()) titleInput.value = "Khung giờ có thể học";
            } else if (typeSelect.value === "study_window") {
                typeSelect.value = "other";
            }
        });

        tr.querySelector(".btn-del-routine").addEventListener("click", () => {
            tr.remove();
            if (routineTableBody.querySelectorAll("tr").length === 0) {
                renderRoutinesTable([]);
            }
        });

        routineTableBody.appendChild(tr);
    }

    if (btnAddRoutineRow) {
        btnAddRoutineRow.addEventListener("click", () => {
            appendRoutineRow({
                weekday: 2,
                start_time: "17:30",
                end_time: "19:00",
                title: "Tập gym",
                activity_type: "gym",
                flexibility: "fixed"
            });
        });
    }

    if (btnAddStudyWindowRow) {
        btnAddStudyWindowRow.addEventListener("click", () => {
            appendRoutineRow({
                weekday: 1,
                start_time: "19:00",
                end_time: "22:00",
                title: "Khung giờ có thể tự học tối",
                activity_type: "study_window",
                flexibility: "study_window"
            });
        });
    }

    if (btnLoadSampleRoutines) {
        btnLoadSampleRoutines.addEventListener("click", () => {
            const samples = [
                { weekday: 1, start_time: "12:00", end_time: "13:30", title: "Ăn trưa & Nghỉ trưa", activity_type: "rest", flexibility: "protected" },
                { weekday: 2, start_time: "12:00", end_time: "13:30", title: "Ăn trưa & Nghỉ trưa", activity_type: "rest", flexibility: "protected" },
                { weekday: 3, start_time: "12:00", end_time: "13:30", title: "Ăn trưa & Nghỉ trưa", activity_type: "rest", flexibility: "protected" },
                { weekday: 2, start_time: "17:30", end_time: "19:00", title: "Tập gym", activity_type: "gym", flexibility: "fixed" },
                { weekday: 4, start_time: "17:30", end_time: "19:00", title: "Đá bóng / Thể thao", activity_type: "sport", flexibility: "fixed" },
                { weekday: 6, start_time: "19:00", end_time: "21:30", title: "Đi chơi cuối tuần", activity_type: "personal", flexibility: "flexible" },
                { weekday: 1, start_time: "19:00", end_time: "22:00", title: "Khung giờ tự học tối Thứ 2", activity_type: "study_window", flexibility: "study_window" },
                { weekday: 2, start_time: "19:30", end_time: "22:00", title: "Khung giờ tự học tối Thứ 3", activity_type: "study_window", flexibility: "study_window" },
                { weekday: 3, start_time: "14:00", end_time: "17:00", title: "Khung giờ tự học chiều Thứ 4", activity_type: "study_window", flexibility: "study_window" },
                { weekday: 3, start_time: "19:00", end_time: "22:00", title: "Khung giờ tự học tối Thứ 4", activity_type: "study_window", flexibility: "study_window" },
                { weekday: 4, start_time: "19:30", end_time: "22:00", title: "Khung giờ tự học tối Thứ 5", activity_type: "study_window", flexibility: "study_window" },
                { weekday: 5, start_time: "19:00", end_time: "22:00", title: "Khung giờ tự học tối Thứ 6", activity_type: "study_window", flexibility: "study_window" },
                { weekday: 6, start_time: "08:30", end_time: "11:30", title: "Khung giờ tự học sáng Thứ 7", activity_type: "study_window", flexibility: "study_window" }
            ];
            routineTableBody.replaceChildren();
            samples.forEach(s => appendRoutineRow(s));
            routineSaveStatus.textContent = "💡 Đã điền mẫu lịch sinh hoạt & khung giờ học. Hãy chỉnh sửa theo ý bạn rồi bấm '💾 Lưu bảng lịch sinh hoạt'.";
            routineSaveStatus.style.color = "#f59e0b";
        });
    }

    async function saveRoutinesBulkFromDOM() {
        if (!routineTableBody) return false;
        const rows = Array.from(routineTableBody.querySelectorAll("tr")).filter(
            tr => !tr.querySelector("td[colspan]")
        );
        const items = [];
        const errors = [];

        rows.forEach((tr, idx) => {
            tr.classList.remove("timetable-conflict-row");
            const weekday = parseInt(tr.querySelector(".routine-weekday").value, 10);
            const start_time = tr.querySelector(".routine-start").value.trim();
            const end_time = tr.querySelector(".routine-end").value.trim();
            const title = tr.querySelector(".routine-title").value.trim();
            const activity_type = tr.querySelector(".routine-type").value;
            const flexibility = tr.querySelector(".routine-flex").value;

            if (!title) {
                errors.push(`Dòng ${idx + 1}: Vui lòng nhập tên hoạt động.`);
                tr.classList.add("timetable-conflict-row");
                return;
            }
            if (!start_time || !end_time || end_time <= start_time) {
                errors.push(`Dòng ${idx + 1} (${title}): Giờ kết thúc phải lớn hơn giờ bắt đầu.`);
                tr.classList.add("timetable-conflict-row");
                return;
            }
            items.push({
                weekday,
                start_time,
                end_time,
                title,
                activity_type,
                flexibility,
                is_recurring: true
            });
        });

        if (errors.length > 0) {
            routineSaveStatus.textContent = `❌ ${errors.join(" ")}`;
            routineSaveStatus.style.color = "#dc2626";
            return false;
        }

        try {
            const resp = await fetch("/api/timetable/planner/routines/bulk", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ routines: items })
            });
            const data = await resp.json();
            if (!resp.ok) {
                routineSaveStatus.textContent = `❌ Lỗi: ${data.detail || "Không thể lưu lịch sinh hoạt"}`;
                routineSaveStatus.style.color = "#dc2626";
                return false;
            }
            if (data.warnings && data.warnings.length > 0) {
                routineSaveStatus.textContent = `✅ Đã lưu ${items.length} mục. ⚠️ Lưu ý: ${data.warnings.join(" | ")}`;
                routineSaveStatus.style.color = "#f59e0b";
            } else {
                routineSaveStatus.textContent = `✅ Đã lưu thành công ${items.length} dòng lịch sinh hoạt & khung giờ học hằng tuần!`;
                routineSaveStatus.style.color = "#16a34a";
            }
            await loadFullPlannerState();
            return true;
        } catch (err) {
            routineSaveStatus.textContent = `❌ Lỗi mạng: ${err.message}`;
            routineSaveStatus.style.color = "#dc2626";
            return false;
        }
    }

    if (btnSaveRoutinesBulk) {
        btnSaveRoutinesBulk.addEventListener("click", () => saveRoutinesBulkFromDOM());
    }

    function renderPreferencesForm(prefs) {
        if (!preferencesForm) return;
        const weekInput = document.getElementById("pref-week-start");
        if (weekInput && prefs.week_start_date) weekInput.value = prefs.week_start_date;
        else if (weekInput && !weekInput.value) {
            const now = new Date();
            weekInput.value = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;
        }
        if (prefEarliestTime && prefs.earliest_study_time) prefEarliestTime.value = prefs.earliest_study_time;
        if (prefLatestTime && prefs.latest_study_time) prefLatestTime.value = prefs.latest_study_time;
        if (prefMaxDailyMinutes && prefs.max_daily_study_minutes) {
            prefMaxDailyMinutes.value = prefs.max_daily_study_minutes;
            updateDailyMinutesHint();
        }
        if (prefSessionMinutes && prefs.preferred_session_minutes) {
            prefSessionMinutes.value = prefs.preferred_session_minutes;
        }
        if (prefAiNotes && prefs.ai_notes !== undefined) {
            prefAiNotes.value = prefs.ai_notes || "";
        }
    }

    function updateDailyMinutesHint() {
        if (!prefMaxDailyMinutes || !prefMaxDailyHint) return;
        const mins = parseInt(prefMaxDailyMinutes.value || "240", 10);
        if (!isNaN(mins)) {
            prefMaxDailyHint.textContent = `${mins} phút = ${(mins / 60).toFixed(1)} giờ/ngày`;
        }
    }

    if (prefMaxDailyMinutes) {
        prefMaxDailyMinutes.addEventListener("input", updateDailyMinutesHint);
    }

    async function savePreferences() {
            if (!preferencesForm.reportValidity()) return false;
            try {
                const resp = await fetch("/api/timetable/planner/preferences", {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        earliest_study_time: prefEarliestTime.value,
                        latest_study_time: prefLatestTime.value,
                        max_daily_study_minutes: parseInt(prefMaxDailyMinutes.value, 10),
                        preferred_session_minutes: parseInt(prefSessionMinutes.value, 10),
                        ai_notes: prefAiNotes.value.trim(),
                        week_start_date: document.getElementById("pref-week-start").value
                    })
                });
                const data = await resp.json();
                if (!resp.ok) {
                    prefSaveStatus.textContent = `❌ ${data.detail || "Không thể lưu giới hạn cá nhân"}`;
                    prefSaveStatus.style.color = "#dc2626";
                    return false;
                }
                prefSaveStatus.textContent = "✅ Đã lưu giới hạn cá nhân & sở thích học tập!";
                prefSaveStatus.style.color = "#16a34a";
                return true;
            } catch (err) {
                prefSaveStatus.textContent = `❌ Lỗi mạng: ${err.message}`;
                prefSaveStatus.style.color = "#dc2626";
                return false;
            }
    }
    preferencesForm?.addEventListener("submit", async e => { e.preventDefault(); await savePreferences(); });
    window.StudyPlanner = {
        async savePersonal() {
            if (!preferencesForm.reportValidity()) return false;
            if (!await savePreferences()) return false;
            return await saveRoutinesBulkFromDOM();
        },
        hasUnconfirmedDraft() {
            return !!(currentDraftId && draftCard && draftCard.style.display !== "none" && draftTableBody.querySelectorAll("tr").length);
        }
    };

    // -------------------------------------------------------------
    // Step 3: Study Tasks & Deadlines
    // -------------------------------------------------------------
    if (taskDeadlineDate && taskDeadlineWeekday) {
        taskDeadlineDate.addEventListener("change", () => {
            const val = taskDeadlineDate.value;
            if (!val) return;
            const parts = val.split("-");
            if (parts.length === 3) {
                const dt = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
                const jsDay = dt.getDay();
                const isoDay = jsDay === 0 ? 7 : jsDay;
                taskDeadlineWeekday.value = String(isoDay);
            }
        });
    }

    if (btnCancelTaskEdit) {
        btnCancelTaskEdit.addEventListener("click", () => {
            taskEditId.value = "";
            formAddTask.reset();
            taskDeadlineWeekday.value = "5";
            taskDeadlineTime.value = "23:59";
            taskPriority.value = "medium";
            taskSplittable.checked = true;
            btnSubmitTask.textContent = "＋ Thêm bài tập vào danh sách";
            btnCancelTaskEdit.style.display = "none";
            taskFormStatus.textContent = "";
        });
    }

    if (formAddTask) {
        formAddTask.addEventListener("submit", async (e) => {
            e.preventDefault();
            const editId = taskEditId.value.trim();
            const estRaw = taskEstimatedMinutes.value.trim();
            const payload = {
                title: taskTitle.value.trim(),
                course: taskCourse.value.trim(),
                deadline_weekday: parseInt(taskDeadlineWeekday.value, 10),
                deadline_date: taskDeadlineDate.value ? taskDeadlineDate.value.trim() : null,
                deadline_time: taskDeadlineTime.value.trim() || "23:59",
                estimated_minutes: estRaw ? parseInt(estRaw, 10) : null,
                priority: taskPriority.value,
                splittable: taskSplittable.checked
            };

            try {
                const url = editId ? `/api/timetable/planner/tasks/${editId}` : "/api/timetable/planner/tasks";
                const method = editId ? "PUT" : "POST";
                const resp = await fetch(url, {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await resp.json();
                if (!resp.ok) {
                    taskFormStatus.textContent = `❌ ${data.detail || "Không thể lưu bài tập"}`;
                    taskFormStatus.style.color = "#dc2626";
                    return;
                }

                if (!payload.estimated_minutes) {
                    taskFormStatus.textContent = "⚠️ Đã lưu bài tập, nhưng bạn chưa nhập thời lượng ước tính. Hãy nhập số phút dự kiến trước khi tạo lịch.";
                    taskFormStatus.style.color = "#f59e0b";
                } else {
                    taskFormStatus.textContent = editId ? "✅ Đã cập nhật bài tập!" : "✅ Đã thêm bài tập & deadline!";
                    taskFormStatus.style.color = "#16a34a";
                }

                taskEditId.value = "";
                formAddTask.reset();
                taskDeadlineWeekday.value = "5";
                taskDeadlineTime.value = "23:59";
                taskPriority.value = "medium";
                taskSplittable.checked = true;
                btnSubmitTask.textContent = "＋ Thêm bài tập vào danh sách";
                btnCancelTaskEdit.style.display = "none";
                await loadFullPlannerState();
            } catch (err) {
                taskFormStatus.textContent = `❌ Lỗi mạng: ${err.message}`;
                taskFormStatus.style.color = "#dc2626";
            }
        });
    }

    function renderTasksTable(tasks) {
        if (!tasksTableBody) return;
        tasksTableBody.replaceChildren();
        if (!tasks || tasks.length === 0) {
            tasksTableBody.innerHTML = `<tr><td colspan="7" style="text-align: center;" class="text-muted">Chưa có bài tập hoặc deadline nào. Hãy thêm bài tập ở biểu mẫu phía trên.</td></tr>`;
            return;
        }

        tasks.forEach(t => {
            const tr = document.createElement("tr");
            if (t.completed) tr.classList.add("master-row-completed");

            const priorityBadge = t.priority === "high"
                ? `<span class="sched-badge" style="background:rgba(239,68,68,0.16);color:#fca5a5;">🔴 Cao</span>`
                : (t.priority === "low"
                    ? `<span class="sched-badge" style="background:rgba(16,185,129,0.16);color:#6ee7b7;">🟢 Thấp</span>`
                    : `<span class="sched-badge" style="background:rgba(245,158,11,0.16);color:#fcd34d;">🟡 Trung bình</span>`);

            let durationHtml = "";
            if (t.missing_estimate) {
                durationHtml = `
                    <div style="display:flex; gap:4px; align-items:center;">
                        <input type="number" class="form-input inline-est-input" min="15" max="10000" step="15" placeholder="Nhập phút..." style="width:95px; min-height:28px; padding:2px 6px; font-size:0.78rem; border-color:#f59e0b;">
                        <button type="button" class="btn btn-sm btn-primary btn-save-inline-est" style="padding:2px 8px; min-height:28px; font-size:0.75rem;">Lưu</button>
                    </div>
                    <small style="color:#fcd34d; font-size:0.72rem;">⚠️ Cần nhập thời lượng</small>
                `;
            } else {
                const schedStr = t.scheduled_minutes ? ` (Đã xếp: ${t.scheduled_minutes}p)` : "";
                durationHtml = `<strong>${t.estimated_minutes} phút</strong><br><small class="text-muted">${(t.estimated_minutes / 60).toFixed(1)}h${escapeHtml(schedStr)}</small>`;
            }

            tr.innerHTML = `
                <td style="text-align: center;">
                    <input type="checkbox" class="task-complete-checkbox" ${t.completed ? "checked" : ""} title="Đánh dấu hoàn thành bài tập">
                </td>
                <td>
                    <strong class="master-item-title">${escapeHtml(t.title)}</strong>
                    ${t.splittable ? `<br><small class="text-muted">Chia nhiều phiên</small>` : `<br><small class="text-muted">Học 1 phiên liền</small>`}
                </td>
                <td>${escapeHtml(t.course)}</td>
                <td><code>${escapeHtml(t.deadline_display)}</code></td>
                <td>${durationHtml}</td>
                <td>${priorityBadge}</td>
                <td style="text-align: center; white-space: nowrap;">
                    <button type="button" class="btn btn-sm btn-outline btn-edit-task" style="padding: 2px 6px;" title="Sửa bài tập">✏️</button>
                    <button type="button" class="btn btn-sm btn-outline btn-del-task" style="padding: 2px 6px; color: #dc2626;" title="Xóa bài tập">&times;</button>
                </td>
            `;

            tr.querySelector(".task-complete-checkbox").addEventListener("change", async (ev) => {
                await fetch(`/api/timetable/planner/tasks/${t.id}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ completed: ev.target.checked })
                });
                await loadFullPlannerState();
            });

            const btnInlineEst = tr.querySelector(".btn-save-inline-est");
            if (btnInlineEst) {
                btnInlineEst.addEventListener("click", async () => {
                    const val = parseInt(tr.querySelector(".inline-est-input").value, 10);
                    if (!val || val <= 0) {
                        alert("Vui lòng nhập thời lượng ước tính hợp lệ (ví dụ: 60, 90, 120 phút).");
                        return;
                    }
                    await fetch(`/api/timetable/planner/tasks/${t.id}`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ estimated_minutes: val })
                    });
                    await loadFullPlannerState();
                });
            }

            tr.querySelector(".btn-edit-task").addEventListener("click", () => {
                taskEditId.value = String(t.id);
                taskTitle.value = t.title;
                taskCourse.value = t.course;
                taskDeadlineWeekday.value = String(t.deadline_weekday);
                taskDeadlineDate.value = t.deadline_date || "";
                taskDeadlineTime.value = t.deadline_time || "23:59";
                taskEstimatedMinutes.value = t.estimated_minutes || "";
                taskPriority.value = t.priority || "medium";
                taskSplittable.checked = Boolean(t.splittable);
                btnSubmitTask.textContent = "💾 Cập nhật bài tập";
                btnCancelTaskEdit.style.display = "inline-flex";
                formAddTask.scrollIntoView({ behavior: "smooth", block: "center" });
            });

            tr.querySelector(".btn-del-task").addEventListener("click", async () => {
                if (confirm(`Xóa bài tập "${t.title}"?`)) {
                    await fetch(`/api/timetable/planner/tasks/${t.id}`, { method: "DELETE" });
                    await loadFullPlannerState();
                }
            });

            tasksTableBody.appendChild(tr);
        });
    }

    // -------------------------------------------------------------
    // Step 4: AI Study Plan Generation, Warnings, Suggestions & Draft Confirmation
    // -------------------------------------------------------------
    async function triggerGeneratePlan(rescheduleOnly = false) {
        const domRoutineRows = routineTableBody ? Array.from(routineTableBody.querySelectorAll("tr")).filter(tr => !tr.querySelector("td[colspan]")) : [];
        if (domRoutineRows.length > 0 && (!plannerState.routines || plannerState.routines.length !== domRoutineRows.length)) {
            const ok = await saveRoutinesBulkFromDOM();
            if (!ok) return;
        }

        const missingTasks = (plannerState.tasks || []).filter(t => !t.completed && t.missing_estimate);
        for (const mt of missingTasks) {
            const ans = prompt(
                `Bài tập "${mt.title}" (${mt.course}, hạn ${mt.deadline_display}) chưa có thời gian ước tính hoàn thành.\n\nVui lòng nhập số phút dự kiến cần để hoàn thành (VD: 60, 90, 120):`,
                "90"
            );
            if (ans !== null && ans.trim() !== "") {
                const parsed = parseInt(ans.trim(), 10);
                if (!isNaN(parsed) && parsed > 0) {
                    await fetch(`/api/timetable/planner/tasks/${mt.id}`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ estimated_minutes: parsed })
                    });
                }
            }
        }

        plannerGenerateStatus.textContent = rescheduleOnly
            ? "⏳ bộ xếp lịch đang sắp xếp lại các phiên học chưa hoàn thành (giữ nguyên lịch cố định & đã khóa)..."
            : "⏳ bộ xếp lịch đang phân tích thời khóa biểu, lịch sinh hoạt, khung giờ học và deadline...";
        plannerGenerateStatus.style.color = "var(--text-secondary)";

        try {
            const aiNotesVal = prefAiNotes ? prefAiNotes.value.trim() : undefined;
            const resp = await fetch("/api/timetable/planner/generate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    reschedule_only: rescheduleOnly,
                    ai_notes: aiNotesVal
                })
            });
            const data = await resp.json();
            if (!resp.ok) {
                plannerGenerateStatus.textContent = `❌ ${data.detail || "Không thể tạo kế hoạch học tập"}`;
                plannerGenerateStatus.style.color = "#dc2626";
                return;
            }

            plannerGenerateStatus.textContent = "✅ Đã tạo bản nháp kế hoạch học tập! Hãy kiểm tra giải thích, cảnh báo (nếu có) và xác nhận lưu bên dưới.";
            plannerGenerateStatus.style.color = "#16a34a";

            await loadFullPlannerState();
            if (data.draft) {
                renderPlanDraft(data.draft);
                document.getElementById("planner-step-generate")?.scrollIntoView({ behavior: "smooth", block: "start" });
            }
        } catch (err) {
            plannerGenerateStatus.textContent = `❌ Lỗi mạng: ${err.message}`;
            plannerGenerateStatus.style.color = "#dc2626";
        }
    }

    if (btnGenerateStudyPlan) {
        btnGenerateStudyPlan.addEventListener("click", () => triggerGeneratePlan(false));
    }

    if (btnRescheduleIncomplete) {
        btnRescheduleIncomplete.addEventListener("click", () => triggerGeneratePlan(true));
    }

    if (btnRescheduleFromMaster) {
        btnRescheduleFromMaster.addEventListener("click", () => triggerGeneratePlan(true));
    }

    function renderPlanDraft(draft) {
        if (!draft || draft.status !== "draft") {
            if (plannerDraftCard) plannerDraftCard.style.display = "none";
            return;
        }
        currentPlanDraftId = draft.plan_id;

        if (plannerExplanationBox && plannerExplanationText) {
            if (draft.explanation) {
                plannerExplanationBox.style.display = "block";
                plannerExplanationText.textContent = draft.explanation;
            } else {
                plannerExplanationBox.style.display = "none";
            }
        }

        if (plannerWarningsBox) {
            const warnings = draft.warnings || [];
            const unscheduled = draft.unscheduled_tasks || [];
            if (warnings.length > 0 || unscheduled.length > 0) {
                plannerWarningsBox.style.display = "block";
                let html = `<strong>⚠️ Cảnh báo lịch học &amp; Khối lượng chưa xếp đủ chỗ:</strong>`;
                if (unscheduled.length > 0) {
                    html += `<ul style="margin: 6px 0 8px 18px; padding: 0;">`;
                    unscheduled.forEach(u => {
                        html += `<li><strong>${escapeHtml(u.title)} (${escapeHtml(u.course)})</strong> — Hạn: ${escapeHtml(u.deadline_display)}: Đã xếp <strong>${u.scheduled_minutes}/${u.estimated_minutes} phút</strong>, còn thiếu <strong style="color:#fca5a5;">${u.missing_minutes} phút</strong>. <em>(Lý do: ${escapeHtml(u.reason)})</em></li>`;
                    });
                    html += `</ul>`;
                }
                const extraWarnings = warnings.filter(w => !unscheduled.some(u => w.includes(u.title)));
                if (extraWarnings.length > 0) {
                    html += `<ul style="margin: 4px 0 0 18px; padding: 0;">` +
                        extraWarnings.map(w => `<li>${escapeHtml(w)}</li>`).join("") +
                        `</ul>`;
                }
                plannerWarningsBox.innerHTML = html;
            } else {
                plannerWarningsBox.style.display = "none";
                plannerWarningsBox.innerHTML = "";
            }
        }

        if (plannerSuggestionsBox) {
            const suggestions = draft.suggestions || [];
            if (suggestions.length > 0) {
                plannerSuggestionsBox.style.display = "block";
                plannerSuggestionsBox.innerHTML = `<strong>💡 Gợi ý các lựa chọn để bạn quyết định xử lý phần việc còn thiếu:</strong>`;
                suggestions.forEach(sug => {
                    const card = document.createElement("div");
                    card.className = "suggestion-option-card";
                    card.innerHTML = `
                        <div style="flex: 1 1 280px;">
                            <strong style="font-size: 0.85rem; color: var(--text-primary);">${escapeHtml(sug.title)}</strong>
                            <p class="text-muted" style="font-size: 0.78rem; margin: 2px 0 0 0;">${escapeHtml(sug.description)}</p>
                        </div>
                    `;
                    if (sug.type === "increase_daily_limit" || sug.type === "add_study_window" || sug.type === "convert_flexible_to_study_window") {
                        const applyBtn = document.createElement("button");
                        applyBtn.type = "button";
                        applyBtn.className = "btn btn-sm btn-primary";
                        applyBtn.textContent = "Đồng ý & Áp dụng gợi ý này";
                        applyBtn.addEventListener("click", async () => {
                            applyBtn.disabled = true;
                            const resp = await fetch("/api/timetable/planner/apply-suggestion", {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(sug)
                            });
                            if (resp.ok) {
                                await loadFullPlannerState();
                                await triggerGeneratePlan(false);
                            } else {
                                applyBtn.disabled = false;
                                alert("Không thể áp dụng gợi ý.");
                            }
                        });
                        card.appendChild(applyBtn);
                    }
                    plannerSuggestionsBox.appendChild(card);
                });
            } else {
                plannerSuggestionsBox.style.display = "none";
                plannerSuggestionsBox.innerHTML = "";
            }
        }

        if (plannerDraftCard && plannerDraftTableBody) {
            plannerDraftTableBody.replaceChildren();
            const sessions = draft.sessions || [];
            if (sessions.length === 0) {
                plannerDraftTableBody.innerHTML = `<tr><td colspan="8" style="text-align: center;" class="text-muted">Chưa có phiên tự học mới nào được tạo trong bản nháp này. Hãy kiểm tra lại Khung giờ có thể học hoặc Bài tập.</td></tr>`;
            } else {
                sessions.forEach(s => appendPlanDraftRow(s));
            }
            plannerConfirmStatus.textContent = "";
            plannerDraftCard.style.display = "block";
        }
    }

    function appendPlanDraftRow(item = {}) {
        if (!plannerDraftTableBody) return;
        const emptyCell = plannerDraftTableBody.querySelector("td[colspan]");
        if (emptyCell) plannerDraftTableBody.replaceChildren();

        const tr = document.createElement("tr");
        tr.dataset.taskId = item.task_id !== null && item.task_id !== undefined ? String(item.task_id) : "";
        tr.dataset.sessionIndex = String(item.session_index || 1);
        tr.dataset.totalSessions = String(item.total_sessions || 1);
        tr.dataset.notes = item.notes || "";

        const wVal = item.weekday || 1;
        const stVal = item.start_time || "19:00";
        const etVal = item.end_time || "20:00";
        const titleVal = item.title || "Tự học";
        const courseVal = item.course || "";
        const prioVal = item.priority || "medium";
        const lockedVal = Boolean(item.locked);

        tr.innerHTML = `
            <td>
                <select class="form-input plan-draft-weekday" style="padding: 4px;">
                    <option value="1" ${wVal === 1 ? "selected" : ""}>Thứ 2</option>
                    <option value="2" ${wVal === 2 ? "selected" : ""}>Thứ 3</option>
                    <option value="3" ${wVal === 3 ? "selected" : ""}>Thứ 4</option>
                    <option value="4" ${wVal === 4 ? "selected" : ""}>Thứ 5</option>
                    <option value="5" ${wVal === 5 ? "selected" : ""}>Thứ 6</option>
                    <option value="6" ${wVal === 6 ? "selected" : ""}>Thứ 7</option>
                    <option value="7" ${wVal === 7 ? "selected" : ""}>Chủ nhật</option>
                </select>
            </td>
            <td>
                <input type="time" class="form-input plan-draft-start" required value="${escapeHtml(stVal)}">
            </td>
            <td>
                <input type="time" class="form-input plan-draft-end" required value="${escapeHtml(etVal)}">
            </td>
            <td>
                <input type="text" class="form-input plan-draft-title" required value="${escapeHtml(titleVal)}">
            </td>
            <td>
                <input type="text" class="form-input plan-draft-course" list="planner-course-datalist" required value="${escapeHtml(courseVal)}">
            </td>
            <td>
                <select class="form-input plan-draft-priority" style="padding: 4px;">
                    <option value="high" ${prioVal === "high" ? "selected" : ""}>Cao</option>
                    <option value="medium" ${prioVal === "medium" ? "selected" : ""}>Trung bình</option>
                    <option value="low" ${prioVal === "low" ? "selected" : ""}>Thấp</option>
                </select>
            </td>
            <td style="text-align: center;">
                <input type="checkbox" class="plan-draft-locked" ${lockedVal ? "checked" : ""} title="Khóa cố định phiên học này">
            </td>
            <td style="text-align: center;">
                <button type="button" class="btn btn-sm btn-outline btn-del-plan-row" style="padding: 2px 6px; color: #dc2626;" title="Xóa phiên">&times;</button>
            </td>
        `;

        tr.querySelector(".btn-del-plan-row").addEventListener("click", () => {
            tr.remove();
        });

        plannerDraftTableBody.appendChild(tr);
    }

    if (btnAddPlanDraftRow) {
        btnAddPlanDraftRow.addEventListener("click", () => {
            appendPlanDraftRow({
                weekday: 1,
                start_time: "19:00",
                end_time: "20:00",
                title: "Tự học bổ sung",
                course: (plannerState.known_courses && plannerState.known_courses[0]) || "Tự chọn",
                priority: "medium",
                locked: true
            });
        });
    }

    if (btnDiscardStudyPlan) {
        btnDiscardStudyPlan.addEventListener("click", async () => {
            if (!currentPlanDraftId) return;
            await fetch(`/api/timetable/planner/drafts/${currentPlanDraftId}`, { method: "DELETE" });
            currentPlanDraftId = null;
            plannerDraftCard.style.display = "none";
            plannerExplanationBox.style.display = "none";
            plannerWarningsBox.style.display = "none";
            plannerSuggestionsBox.style.display = "none";
            plannerGenerateStatus.textContent = "Đã hủy bản nháp kế hoạch tự học.";
        });
    }

    if (btnConfirmStudyPlan) {
        btnConfirmStudyPlan.addEventListener("click", async () => {
            if (!currentPlanDraftId) return;
            const rows = Array.from(plannerDraftTableBody.querySelectorAll("tr")).filter(
                tr => !tr.querySelector("td[colspan]")
            );
            const sessions = [];
            for (let i = 0; i < rows.length; i++) {
                const tr = rows[i];
                const weekday = parseInt(tr.querySelector(".plan-draft-weekday").value, 10);
                const start_time = tr.querySelector(".plan-draft-start").value.trim();
                const end_time = tr.querySelector(".plan-draft-end").value.trim();
                const title = tr.querySelector(".plan-draft-title").value.trim();
                const course = tr.querySelector(".plan-draft-course").value.trim();
                const priority = tr.querySelector(".plan-draft-priority").value;
                const locked = tr.querySelector(".plan-draft-locked").checked;
                const tidRaw = tr.dataset.taskId;

                if (!title || !course || !start_time || !end_time || end_time <= start_time) {
                    plannerConfirmStatus.textContent = `❌ Dòng ${i + 1}: Vui lòng kiểm tra tên phiên, môn học và giờ kết thúc lớn hơn giờ bắt đầu.`;
                    plannerConfirmStatus.style.color = "#dc2626";
                    return;
                }

                sessions.push({
                    task_id: tidRaw ? parseInt(tidRaw, 10) : null,
                    weekday,
                    start_time,
                    end_time,
                    title,
                    course,
                    priority,
                    locked,
                    source: "ai_assistant",
                    session_index: parseInt(tr.dataset.sessionIndex || "1", 10),
                    total_sessions: parseInt(tr.dataset.totalSessions || "1", 10),
                    notes: tr.dataset.notes || ""
                });
            }

            try {
                const resp = await fetch("/api/timetable/planner/confirm", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        plan_id: currentPlanDraftId,
                        sessions
                    })
                });
                const data = await resp.json();
                if (!resp.ok) {
                    plannerConfirmStatus.textContent = `❌ ${data.detail || "Không thể xác nhận lịch học tập"}`;
                    plannerConfirmStatus.style.color = "#dc2626";
                    return;
                }

                plannerConfirmStatus.textContent = `✅ Đã xác nhận và lưu ${data.saved_count} phiên tự học vào Lịch tuần tổng hợp!`;
                plannerConfirmStatus.style.color = "#16a34a";
                currentPlanDraftId = null;
                plannerDraftCard.style.display = "none";
                await loadFullPlannerState();
                document.getElementById("planner-step-master-schedule")?.scrollIntoView({ behavior: "smooth", block: "start" });
            } catch (err) {
                plannerConfirmStatus.textContent = `❌ Lỗi mạng: ${err.message}`;
                plannerConfirmStatus.style.color = "#dc2626";
            }
        });
    }

    // -------------------------------------------------------------
    // Step 5: Master Combined Weekly/Daily Schedule & Session Controls
    // -------------------------------------------------------------
    if (scheduleFilterDay) scheduleFilterDay.addEventListener("change", renderMasterSchedule);
    if (scheduleFilterCategory) scheduleFilterCategory.addEventListener("change", renderMasterSchedule);
    if (scheduleShowWindows) scheduleShowWindows.addEventListener("change", renderMasterSchedule);

    if (btnToggleAddManualSession && formManualStudySession) {
        btnToggleAddManualSession.addEventListener("click", () => {
            manualSessionEditId.value = "";
            manualSessionFormTitle.textContent = "Thêm phiên tự học thủ công (sẽ tự động khóa giờ):";
            formManualStudySession.reset();
            manualSessionStatus.textContent = "";
            formManualStudySession.style.display = formManualStudySession.style.display === "none" ? "block" : "none";
        });
    }

    if (btnCancelManualSession && formManualStudySession) {
        btnCancelManualSession.addEventListener("click", () => {
            manualSessionEditId.value = "";
            formManualStudySession.style.display = "none";
            manualSessionStatus.textContent = "";
        });
    }

    if (formManualStudySession) {
        formManualStudySession.addEventListener("submit", async (e) => {
            e.preventDefault();
            const editId = manualSessionEditId.value.trim();
            const payload = {
                weekday: parseInt(manualSessionWeekday.value, 10),
                title: manualSessionTitle.value.trim(),
                course: manualSessionCourse.value.trim(),
                start_time: manualSessionStart.value.trim(),
                end_time: manualSessionEnd.value.trim(),
                locked: true
            };
            if (payload.end_time <= payload.start_time) {
                manualSessionStatus.textContent = "❌ Giờ kết thúc phải lớn hơn giờ bắt đầu.";
                manualSessionStatus.style.color = "#dc2626";
                return;
            }

            try {
                const url = editId ? `/api/timetable/planner/sessions/${editId}` : "/api/timetable/planner/sessions";
                const method = editId ? "PUT" : "POST";
                const resp = await fetch(url, {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await resp.json();
                if (!resp.ok) {
                    manualSessionStatus.textContent = `❌ ${data.detail || "Không thể lưu phiên tự học"}`;
                    manualSessionStatus.style.color = "#dc2626";
                    return;
                }
                manualSessionStatus.textContent = "✅ Đã lưu phiên tự học!";
                manualSessionStatus.style.color = "#16a34a";
                manualSessionEditId.value = "";
                formManualStudySession.style.display = "none";
                await loadFullPlannerState();
            } catch (err) {
                manualSessionStatus.textContent = `❌ Lỗi mạng: ${err.message}`;
                manualSessionStatus.style.color = "#dc2626";
            }
        });
    }

    function renderMasterSchedule() {
        if (!masterScheduleTableBody) return;
        const summary = plannerState.summary || {};

        if (sumClassCount) {
            const h = ((summary.class_minutes || 0) / 60).toFixed(1);
            sumClassCount.textContent = `${summary.class_count || 0} buổi (${h} giờ)`;
        }
        if (sumRoutineCount) {
            const h = ((summary.routine_minutes || 0) / 60).toFixed(1);
            sumRoutineCount.textContent = `${summary.routine_count || 0} mục (${h} giờ)`;
        }
        if (sumWindowHours) {
            const h = ((summary.study_window_minutes || 0) / 60).toFixed(1);
            sumWindowHours.textContent = `${summary.study_window_count || 0} khung (${h} giờ)`;
        }
        if (sumStudyProgress) {
            const done = summary.completed_study_minutes || 0;
            const total = summary.study_minutes || 0;
            sumStudyProgress.textContent = `${done}/${total} phút hoàn thành (${summary.study_session_count || 0} phiên)`;
        }

        const dayFilter = scheduleFilterDay ? scheduleFilterDay.value : "all";
        const catFilter = scheduleFilterCategory ? scheduleFilterCategory.value : "all";
        const showWin = scheduleShowWindows ? scheduleShowWindows.checked : false;

        let allItems = [...(plannerState.combined_schedule || [])];
        if (showWin && plannerState.study_windows) {
            plannerState.study_windows.forEach(w => {
                allItems.push({
                    uid: w.uid,
                    item_id: w.item_id,
                    category: "study_window",
                    category_label: "Khung giờ có thể học",
                    source_badge: "Khung giờ mở",
                    weekday: w.weekday,
                    weekday_label: w.weekday_label,
                    start_time: w.start_time,
                    end_time: w.end_time,
                    duration_minutes: w.duration_minutes,
                    title: w.title,
                    course: "—",
                    flexibility: "study_window",
                    flexibility_label: "Cho phép xếp lịch tự học",
                    completed: false,
                    locked: false
                });
            });
            allItems.sort((a, b) => (a.weekday - b.weekday) || a.start_time.localeCompare(b.start_time));
        }

        const filtered = allItems.filter(item => {
            if (dayFilter !== "all" && String(item.weekday) !== dayFilter) return false;
            if (catFilter !== "all" && item.category !== catFilter) return false;
            return true;
        });

        masterScheduleTableBody.replaceChildren();
        if (filtered.length === 0) {
            masterScheduleTableBody.innerHTML = `<tr><td colspan="7" style="text-align: center;" class="text-muted">Chưa có mục lịch nào khớp bộ lọc. Hãy xác nhận thời khóa biểu OCR, nhập lịch sinh hoạt và bấm "🤖 Tạo lịch học cá nhân".</td></tr>`;
            return;
        }

        filtered.forEach(item => {
            const tr = document.createElement("tr");
            if (item.completed) tr.classList.add("master-row-completed");

            let badgeHtml = "";
            if (item.category === "class") {
                badgeHtml = `<span class="sched-badge badge-class">🏫 Lớp học (${escapeHtml(item.source_badge)})</span>`;
            } else if (item.category === "rest") {
                badgeHtml = `<span class="sched-badge badge-rest">😴 Nghỉ ngơi (${escapeHtml(item.source_badge)})</span>`;
            } else if (item.category === "routine") {
                badgeHtml = `<span class="sched-badge badge-routine">🏃 ${escapeHtml(item.category_label)}</span>`;
            } else if (item.category === "study_window") {
                badgeHtml = `<span class="sched-badge badge-window">📖 Khung giờ có thể học</span>`;
            } else if (item.category === "study_session") {
                badgeHtml = item.completed
                    ? `<span class="sched-badge badge-study-done">✅ Tự học (Đã xong)</span>`
                    : `<span class="sched-badge badge-study-ai">🤖 ${escapeHtml(item.source_badge)}</span>`;
            }

            const courseOrRoom = item.category === "class"
                ? (item.room ? `Phòng: ${escapeHtml(item.room)}` : "Lớp cố định")
                : (item.course ? escapeHtml(item.course) : "—");

            let statusText = item.flexibility_label || "";
            if (item.category === "study_session") {
                const parts = [];
                parts.push(item.completed ? "Đã hoàn thành" : "Chưa hoàn thành");
                if (item.locked) parts.push("🔒 Đã khóa giờ");
                statusText = parts.join(" · ");
            }

            const pdfBadgeHtml = item.pdf_url
                ? `<div style="margin-top: 5px;"><a href="${escapeHtml(item.pdf_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-outline" style="padding: 2px 10px; min-height: 24px; font-size: 11px;">📄 Tải PDF Bài tập + Đáp án cuối trang${item.classified_topic ? ` (${escapeHtml(item.classified_topic)})` : ""}</a></div>`
                : "";

            tr.innerHTML = `
                <td><strong>${escapeHtml(item.weekday_label)}</strong></td>
                <td><code>${escapeHtml(item.start_time)} - ${escapeHtml(item.end_time)}</code><br><small class="text-muted">${item.duration_minutes} phút</small></td>
                <td>${badgeHtml}</td>
                <td><strong class="master-item-title">${escapeHtml(item.title)}</strong>${item.notes ? `<br><small class="text-muted">${escapeHtml(item.notes)}</small>` : ""}${pdfBadgeHtml}</td>
                <td>${courseOrRoom}</td>
                <td><small>${escapeHtml(statusText)}</small></td>
                <td class="master-actions-cell" style="text-align: center; white-space: nowrap;"></td>
            `;

            const actionCell = tr.querySelector(".master-actions-cell");
            if (item.category === "study_session") {
                const btnDone = document.createElement("button");
                btnDone.type = "button";
                btnDone.className = "btn btn-sm btn-outline";
                btnDone.style.padding = "2px 6px";
                btnDone.style.marginRight = "4px";
                btnDone.title = item.completed ? "Đánh dấu chưa xong" : "Đánh dấu đã hoàn thành";
                btnDone.textContent = item.completed ? "↩" : "✓ Xong";
                btnDone.addEventListener("click", async () => {
                    await fetch(`/api/timetable/planner/sessions/${item.item_id}`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ completed: !item.completed })
                    });
                    await loadFullPlannerState();
                });

                const btnLock = document.createElement("button");
                btnLock.type = "button";
                btnLock.className = "btn btn-sm btn-outline";
                btnLock.style.padding = "2px 6px";
                btnLock.style.marginRight = "4px";
                btnLock.title = item.locked ? "Mở khóa giờ" : "Khóa cố định giờ này";
                btnLock.textContent = item.locked ? "🔓" : "🔒";
                btnLock.addEventListener("click", async () => {
                    await fetch(`/api/timetable/planner/sessions/${item.item_id}`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ locked: !item.locked })
                    });
                    await loadFullPlannerState();
                });

                const btnEdit = document.createElement("button");
                btnEdit.type = "button";
                btnEdit.className = "btn btn-sm btn-outline";
                btnEdit.style.padding = "2px 6px";
                btnEdit.style.marginRight = "4px";
                btnEdit.title = "Sửa giờ / nội dung phiên học";
                btnEdit.textContent = "✏️";
                btnEdit.addEventListener("click", () => {
                    manualSessionEditId.value = String(item.item_id);
                    manualSessionFormTitle.textContent = `Chỉnh sửa phiên học "${item.title}":`;
                    manualSessionWeekday.value = String(item.weekday);
                    manualSessionTitle.value = item.title;
                    manualSessionCourse.value = item.course || "";
                    manualSessionStart.value = item.start_time;
                    manualSessionEnd.value = item.end_time;
                    manualSessionStatus.textContent = "";
                    formManualStudySession.style.display = "block";
                    formManualStudySession.scrollIntoView({ behavior: "smooth", block: "center" });
                });

                const btnDel = document.createElement("button");
                btnDel.type = "button";
                btnDel.className = "btn btn-sm btn-outline";
                btnDel.style.padding = "2px 6px";
                btnDel.style.color = "#dc2626";
                btnDel.title = "Xóa phiên học";
                btnDel.innerHTML = "&times;";
                btnDel.addEventListener("click", async () => {
                    if (confirm(`Xóa phiên tự học "${item.title}"?`)) {
                        await fetch(`/api/timetable/planner/sessions/${item.item_id}`, { method: "DELETE" });
                        await loadFullPlannerState();
                    }
                });

                actionCell.append(btnDone, btnLock, btnEdit, btnDel);
            } else if (item.category === "routine" || item.category === "rest" || item.category === "study_window") {
                const btnDel = document.createElement("button");
                btnDel.type = "button";
                btnDel.className = "btn btn-sm btn-outline";
                btnDel.style.padding = "2px 6px";
                btnDel.style.color = "#dc2626";
                btnDel.title = "Xóa mục sinh hoạt này";
                btnDel.innerHTML = "&times;";
                btnDel.addEventListener("click", async () => {
                    if (confirm(`Xóa mục "${item.title}" (${item.weekday_label})?`)) {
                        await fetch(`/api/timetable/planner/routines/${item.item_id}`, { method: "DELETE" });
                        await loadFullPlannerState();
                    }
                });
                actionCell.appendChild(btnDel);
            } else if (item.category === "class") {
                const spanFixed = document.createElement("span");
                spanFixed.className = "text-muted";
                spanFixed.style.fontSize = "0.75rem";
                spanFixed.textContent = "🔒 Cố định (Bước 1)";
                actionCell.appendChild(spanFixed);
            }

            masterScheduleTableBody.appendChild(tr);
        });
    }

    function escapeHtml(str) {
        if (!str) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    window.addEventListener("arym:planner-refresh", () => {
        loadFullPlannerState();
    });
});
