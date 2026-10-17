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
        extractStatus.textContent = "Đang phân tích hình ảnh bằng mô hình thị giác Qwen2.5-VL 3B...";
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

        // Render rows
        if (entries.length === 0) {
            addEmptyDraftRow();
        } else {
            entries.forEach(e => appendDraftRow(e));
        }

        saveDraftStateLocally();
        confirmStatus.textContent = "";
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
                <select class="form-input draft-weekday" style="padding: 4px;">
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
                <input type="text" class="form-input draft-course" required value="${escapeHtml(courseVal)}" placeholder="Tên môn học">
            </td>
            <td>
                <input type="text" class="form-input draft-period" value="${escapeHtml(periodVal)}" placeholder="Tiết (1-3)">
            </td>
            <td>
                <input type="time" class="form-input draft-start" required value="${escapeHtml(startVal)}">
            </td>
            <td>
                <input type="time" class="form-input draft-end" required value="${escapeHtml(endVal)}">
            </td>
            <td>
                <input type="text" class="form-input draft-room" value="${escapeHtml(roomVal)}" placeholder="Phòng">
            </td>
            <td style="text-align: center;">
                <button type="button" class="btn btn-sm btn-outline btn-del-row" style="padding: 2px 6px; color: #dc2626;" title="Xóa dòng">&times;</button>
            </td>
        `;

        tr.addEventListener("input", saveDraftStateLocally);
        tr.addEventListener("change", saveDraftStateLocally);

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
                        replace_existing: checkReplaceExisting.checked
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
    // ICS Export
    // -------------------------------------------------------------
    if (formExportIcs) {
        formExportIcs.addEventListener("submit", async (e) => {
            e.preventDefault();
            const sDate = document.getElementById("export-start-date").value;
            const eDate = document.getElementById("export-end-date").value;

            if (!sDate || !eDate) {
                exportStatus.textContent = "❌ Vui lòng nhập đầy đủ ngày bắt đầu và kết thúc kỳ học.";
                exportStatus.style.color = "#dc2626";
                return;
            }
            if (eDate < sDate) {
                exportStatus.textContent = "❌ Ngày kết thúc kỳ học phải sau ngày bắt đầu.";
                exportStatus.style.color = "#dc2626";
                return;
            }

            exportStatus.textContent = "Đang tạo tệp lịch .ics...";
            exportStatus.style.color = "var(--text-secondary)";

            try {
                const url = `/api/timetable/export.ics?start_date=${encodeURIComponent(sDate)}&end_date=${encodeURIComponent(eDate)}`;
                const resp = await fetch(url);
                if (!resp.ok) {
                    const errJson = await resp.json();
                    throw new Error(errJson.detail || "Không thể xuất file iCalendar");
                }

                const blob = await resp.blob();
                const downloadUrl = window.URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = downloadUrl;
                a.download = `timetable_${sDate}_${eDate}.ics`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(downloadUrl);

                exportStatus.textContent = "✅ Đã tạo tệp lịch .ics. Kiểm tra mục tải xuống để mở và thêm vào ứng dụng Lịch.";
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
