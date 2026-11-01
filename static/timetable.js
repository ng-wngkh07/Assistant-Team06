/**
 * Timetable vision extraction, editable draft review, manual entry, and ICS export.
 * Strict client-side privacy: images and personal schedule remain local to this device.
 */
document.addEventListener("DOMContentLoaded", () => {
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

    const formExportIcs = document.getElementById("form-export-ics");
    const exportStatus = document.getElementById("export-status");

    const btnApplyPeriodMap = document.getElementById("btn-apply-period-map");
    const mapPeriodCode = document.getElementById("map-period-code");
    const mapStartTime = document.getElementById("map-start-time");
    const mapEndTime = document.getElementById("map-end-time");

    let currentDraftId = null;
    let extracting = false;
    let confirmedEntriesCount = 0;

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

    // Load initial confirmed schedule and restore any active draft
    loadConfirmedSchedule();
    restoreDraftLocally();

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

        // Uncertainties banner
        if (uncertainties.length > 0) {
            uncertaintiesBox.style.display = "block";
            uncertaintiesBox.innerHTML = `<strong>⚠️ Lưu ý cần kiểm tra lại trên ảnh:</strong><ul style="margin: 6px 0 0 18px; padding: 0;">` +
                uncertainties.map(u => `<li>${escapeHtml(u)}</li>`).join("") +
                `</ul>`;
        } else {
            uncertaintiesBox.style.display = "none";
            uncertaintiesBox.innerHTML = "";
        }

        // Render rows (retain partial_entries when full scheduled entries are absent — YC-06)
        const rowsToRender = entries.length > 0
            ? entries
            : (Array.isArray(data.partial_entries) && data.partial_entries.length > 0 ? data.partial_entries : []);
        if (rowsToRender.length === 0) {
            addEmptyDraftRow();
        } else {
            rowsToRender.forEach(e => appendDraftRow(e));
        }

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
            localStorage.setItem("timetable_active_draft_id", currentDraftId);
            localStorage.setItem("timetable_active_draft_state", JSON.stringify(rows));
        } catch (e) {
            console.warn("Failed to persist draft state locally:", e);
        }
    }

    function clearDraftStateLocally() {
        try {
            localStorage.removeItem("timetable_active_draft_id");
            localStorage.removeItem("timetable_active_draft_state");
        } catch (e) {}
    }

    async function restoreDraftLocally() {
        try {
            const localId = localStorage.getItem("timetable_active_draft_id");
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
            const savedStateStr = localId === savedId ? localStorage.getItem("timetable_active_draft_state") : null;
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
            saveDraftStateLocally();
        };

        tr.addEventListener("input", syncMissingHighlight);
        tr.addEventListener("change", syncMissingHighlight);

        tr.querySelector(".btn-del-row").addEventListener("click", () => {
            tr.remove();
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
                loadConfirmedSchedule();
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
            renderConfirmedTable(data.entries || []);
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
                <td style="text-align: center;">
                    <button type="button" class="btn btn-sm btn-outline" style="padding: 1px 6px; color: #dc2626;" title="Xóa môn">&times;</button>
                </td>
            `;

            tr.querySelector("button").addEventListener("click", async () => {
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
                loadConfirmedSchedule();
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
                        loadConfirmedSchedule();
                    }
                } catch (e) {
                    alert(`Lỗi: ${e.message}`);
                }
            }
        });
    }

    if (btnToggleManual && formManual) {
        btnToggleManual.addEventListener("click", () => {
            formManual.style.display = formManual.style.display === "none" ? "block" : "none";
        });
    }

    if (btnCancelManual && formManual) {
        btnCancelManual.addEventListener("click", () => {
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
                const resp = await fetch("/api/timetable/manual", {
                    method: "POST",
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
                    manualStatus.textContent = `⚠️ ${data.detail || "Không thể thêm môn"}`;
                    manualStatus.style.color = "#dc2626";
                    return;
                }

                manualStatus.textContent = "✅ Đã thêm môn học thành công!";
                manualStatus.style.color = "#16a34a";
                formManual.reset();
                loadConfirmedSchedule();
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

            if (confirmedEntriesCount === 0) {
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
                const url = `/api/timetable/export.ics?start_date=${encodeURIComponent(sParsed.isoDate)}&end_date=${encodeURIComponent(eParsed.isoDate)}`;
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

                exportStatus.textContent = `✅ Đã tạo tệp lịch .ics thành công (${sParsed.formatted} - ${eParsed.formatted}). Bạn có thể mở và đồng bộ vào Google Calendar hoặc Apple Calendar.`;
                exportStatus.style.color = "#16a34a";
            } catch (err) {
                exportStatus.textContent = `❌ ${err.message}`;
                exportStatus.style.color = "#dc2626";
            }
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
});
