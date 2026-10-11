document.addEventListener("DOMContentLoaded", () => {
    const qa = document.getElementById("qa-panel");
    const tools = document.getElementById("learning-tools-panel");
    const qaButton = document.getElementById("mode-qa");
    const toolsButton = document.getElementById("mode-tools");
    const query = document.getElementById("document-query");
    const book = document.getElementById("document-book");
    const method = document.getElementById("document-method");
    const status = document.getElementById("document-search-status");
    const results = document.getElementById("document-results");
    const form = document.getElementById("document-search-form");
    let revision = 0;

    function setToolTab(tab) {
        const tabs = ["practice", "review", "examples"];
        if (!tabs.includes(tab)) tab = "practice";
        for (const name of tabs) {
            const button = document.getElementById(`btn-tool-tab-${name}`);
            const panel = document.getElementById(`tool-tab-${name}`);
            if (button) {
                const active = name === tab;
                button.classList.toggle("active", active);
                button.setAttribute("aria-selected", String(active));
                button.setAttribute("tabindex", active ? "0" : "-1");
            }
            if (panel) panel.hidden = name !== tab;
        }
    }

    function setMode(mode) {
        const timetable = document.getElementById("timetable-panel");
        const timetableBtn = document.getElementById("mode-timetable");
        if (timetable) timetable.hidden = mode !== "timetable";
        if (timetableBtn) {
            timetableBtn.classList.toggle("active", mode === "timetable");
            timetableBtn.setAttribute("aria-pressed", String(mode === "timetable"));
        }
        const isTools = mode === "tools";
        const isQa = mode === "qa";
        if (qa) qa.hidden = !isQa;
        if (tools) tools.hidden = !isTools;
        if (qaButton) {
            qaButton.classList.toggle("active", isQa);
            qaButton.setAttribute("aria-pressed", String(isQa));
        }
        if (toolsButton) {
            toolsButton.classList.toggle("active", isTools);
            toolsButton.setAttribute("aria-pressed", String(isTools));
        }
        const clearChat = document.getElementById("btn-clear-chat");
        if (clearChat) clearChat.hidden = !isQa;
    }
    qaButton.addEventListener("click", () => setMode("qa"));
    toolsButton.addEventListener("click", () => setMode("tools"));
    for (const name of ["practice", "review", "examples"]) {
        const button = document.getElementById(`btn-tool-tab-${name}`);
        if (button) button.addEventListener("click", () => setToolTab(name));
    }
    const ttBtn = document.getElementById("mode-timetable");
    if (ttBtn) ttBtn.addEventListener("click", () => setMode("timetable"));
    const sidebarTtBtn = document.getElementById("btn-open-timetable");
    if (sidebarTtBtn) sidebarTtBtn.addEventListener("click", () => setMode("timetable"));
    document.addEventListener("app-show-qa", () => setMode("qa"));
    document.addEventListener("app-set-mode", (e) => setMode(e.detail));
    document.addEventListener("app-open-tool-tab", (e) => {
        setMode("tools");
        setToolTab(e.detail);
    });

    async function loadBooks() {
        try {
            const response = await fetch("/api/documents");
            if (!response.ok) throw new Error("Không tải được danh sách tài liệu.");
            const data = await response.json();
            for (const doc of data.documents || []) {
                const option = document.createElement("option");
                option.value = doc.id;
                option.textContent = doc.clean_title || doc.filename;
                book.appendChild(option);
            }
        } catch (error) {
            if (revision === 0) status.textContent = `${error.message} Bạn vẫn có thể thử tìm trong toàn bộ thư viện.`;
        }
    }
    loadBooks();

    function invalidateSearch() {
        revision += 1;
        results.replaceChildren();
        status.textContent = "Nhập hoặc cập nhật từ khóa, rồi bấm Tìm tài liệu.";
    }
    query.addEventListener("input", invalidateSearch);
    book.addEventListener("change", invalidateSearch);
    method.addEventListener("change", invalidateSearch);

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const text = query.value.trim();
        const epoch = ++revision;
        results.replaceChildren();
        if (!text) {
            status.textContent = "Nhập từ khóa cần tìm.";
            query.focus();
            return;
        }
        const params = new URLSearchParams({q: text, limit: "10", method: method.value});
        if (book.value) params.set("document_id", book.value);
        status.textContent = "Đang tìm trong thư viện…";
        try {
            const response = await fetch(`/api/documents/search?${params}`);
            const data = await response.json();
            if (epoch !== revision) return;
            if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Không thể tra cứu. Hãy kiểm tra từ khóa và thử lại.");
            const items = data.results || [];
            const count = items.length ? `Tìm thấy ${items.length} trích đoạn (${data.method === "hybrid" ? "ngữ nghĩa và từ khóa" : "từ khóa"}).` : "Không tìm thấy trích đoạn phù hợp. Thử từ khóa khác hoặc từ gốc trong sách.";
            status.textContent = `${count} ${data.notice || ""}`.trim();
            for (const item of items) {
                const card = document.createElement("article");
                card.className = "document-result";
                const heading = document.createElement("h3");
                heading.textContent = item.book_title || item.filename;
                const page = document.createElement("p");
                page.className = "document-page";
                const hasPdf = Boolean(item.pdf_url);
                const sourceUnit = hasPdf ? "trang" : "phần";
                page.textContent = hasPdf ? `Trang PDF ${item.page_num}` : `Phần ${item.page_num}`;
                const passage = document.createElement("div");
                if (item.page_image_url) {
                    const originalImage = document.createElement("img");
                    originalImage.src = `/api/documents/${Number(item.doc_id)}/pages/${Number(item.page_num)}/image`;
                    originalImage.alt = `Ảnh trang gốc ${item.page_num} — ${item.book_title || item.filename}`;
                    originalImage.loading = "lazy"; originalImage.className = "source-page-image";
                    if (hasPdf) {
                        const imageLink = document.createElement("a");
                        imageLink.href = `/api/documents/${Number(item.doc_id)}/pdf#page=${Number(item.page_num)}`;
                        imageLink.target = "_blank"; imageLink.rel = "noopener noreferrer";
                        imageLink.appendChild(originalImage); passage.appendChild(imageLink);
                    } else {
                        passage.appendChild(originalImage);
                    }
                }
                const extracted = document.createElement("details");
                extracted.open = !item.page_image_url;
                const summary = document.createElement("summary");
                summary.textContent = "Bản chữ dùng để tìm kiếm (có thể lỗi định dạng)";
                const rawText = document.createElement("p");
                rawText.className = "document-passage"; rawText.textContent = item.text;
                extracted.append(summary, rawText); passage.appendChild(extracted);
                const actions = document.createElement("div");
                actions.className = "document-result-actions";
                if (hasPdf) {
                    const pdf = document.createElement("a");
                    pdf.className = "btn btn-outline btn-sm";
                    // Construct from numeric IDs, never a returned arbitrary URL.
                    pdf.href = `/api/documents/${Number(item.doc_id)}/pdf#page=${Number(item.page_num)}`;
                    pdf.target = "_blank";
                    pdf.rel = "noopener noreferrer";
                    pdf.textContent = "Mở PDF đúng trang ↗";
                    actions.appendChild(pdf);
                }
                const ask = document.createElement("button");
                ask.type = "button";
                ask.className = "btn btn-secondary btn-sm";
                ask.textContent = `Hỏi về ${sourceUnit} này`;
                ask.addEventListener("click", () => {
                    const input = document.getElementById("user-input");
                    const draft = `Hãy giải thích nội dung ${sourceUnit} ${item.page_num} trong tài liệu «${item.book_title || item.filename}».`;
                    document.dispatchEvent(new CustomEvent("app-source-selected", {detail:{doc_id:Number(item.doc_id), page_num:Number(item.page_num), chunk_id:Number(item.chunk_id), title:item.book_title || item.filename}}));
                    input.value = input.value.trim() ? `${input.value}\n\n${draft}` : draft;
                    setMode("qa");
                    input.dispatchEvent(new Event("input"));
                    input.focus();
                });
                actions.appendChild(ask);
                const useInQa = document.createElement("button");
                useInQa.type = "button";
                useInQa.className = "btn btn-outline btn-sm";
                useInQa.textContent = "Dùng trang làm phạm vi QA";
                useInQa.title = "Giữ nguyên câu hỏi trong ô nhập và truy xuất lại bằng chứng khi bạn gửi.";
                useInQa.addEventListener("click", () => {
                    document.dispatchEvent(new CustomEvent("app-source-selected", {detail:{
                        doc_id:Number(item.doc_id), page_num:Number(item.page_num), chunk_id:Number(item.chunk_id),
                        title:item.book_title || item.filename,
                    }}));
                    setMode("qa");
                    document.getElementById("user-input")?.focus();
                });
                actions.appendChild(useInQa);
                const practice = document.createElement("button");
                practice.type = "button";
                practice.className = "btn btn-outline btn-sm";
                practice.textContent = "Tạo câu hỏi từ đoạn này";
                practice.addEventListener("click", () => {
                    document.dispatchEvent(new CustomEvent("app-source-selected", {detail:{
                        doc_id:Number(item.doc_id), page_num:Number(item.page_num), chunk_id:Number(item.chunk_id),
                        title:item.book_title || item.filename,
                    }}));
                    document.dispatchEvent(new CustomEvent("app-open-tool-tab", {detail:"practice"}));
                    document.dispatchEvent(new CustomEvent("app-start-practice"));
                });
                actions.appendChild(practice);
                card.append(heading, page, passage, actions);
                results.appendChild(card);
            }
        } catch (error) {
            if (epoch !== revision) return;
            status.textContent = `Tra cứu chưa thành công: ${error.message}`;
        }
    });
});
