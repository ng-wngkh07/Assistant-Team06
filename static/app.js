document.addEventListener("DOMContentLoaded", () => {
    let selectedSourcePage = null;
    let selectedSourceMode = "page";
    const sourceScope = document.getElementById("qa-source-scope");
    const sourceLabel = document.getElementById("qa-source-label");
    const sourceClearButton = document.getElementById("qa-source-clear");
    function updateSourceScopeLabel() {
        if (!selectedSourcePage || !sourceScope || !sourceLabel) return;
        const range = selectedSourceMode === "document" ? "toàn tài liệu" : `trang ${selectedSourcePage.page_num}`;
        sourceScope.hidden = false;
        sourceLabel.textContent = `Nguồn đang chọn: ${selectedSourcePage.title} · ${range}. `;
        if (sourceClearButton) sourceClearButton.textContent = "Tìm trong toàn bộ thư viện";
    }
    function clearSourceScope() {
        selectedSourcePage = null;
        selectedSourceMode = "page";
        if (sourceScope) sourceScope.hidden = true;
        document.dispatchEvent(new Event("app-source-cleared"));
    }
    document.addEventListener("app-source-selected", event => {
        selectedSourcePage = event.detail;
        selectedSourceMode = "page";
        updateSourceScopeLabel();
    });
    document.addEventListener("app-source-cleared", () => {
        selectedSourcePage = null;
        selectedSourceMode = "page";
        if (sourceScope) sourceScope.hidden = true;
    });
    document.getElementById("qa-source-clear")?.addEventListener("click", clearSourceScope);
    document.addEventListener("app-show-qa", clearSourceScope);
    // DOM Elements
    const chatForm = document.getElementById("chat-form");
    const userInput = document.getElementById("user-input");
    const btnSend = document.getElementById("btn-send");
    const btnClear = document.getElementById("btn-clear-chat");
    const btnClearHistory = document.getElementById("btn-clear-history");
    const btnNewChatSidebar = document.getElementById("btn-new-chat-sidebar");
    const inputSessionSearch = document.getElementById("input-session-search");
    const messagesContainer = document.getElementById("messages-container");
    const historyList = document.getElementById("history-list");
    const sessionBar = document.getElementById("session-bar");
    const sessionActiveTitle = document.getElementById("session-active-title");
    const btnRenameSession = document.getElementById("btn-rename-session");
    const btnSummarizeSession = document.getElementById("btn-summarize-session");
    const sessionSummaryBox = document.getElementById("session-summary-box");
    const sessionSummaryText = document.getElementById("session-summary-text");
    const btnCloseSummary = document.getElementById("btn-close-summary");
    const btnResumeLastQuestion = document.getElementById("btn-resume-last-question");
    const resumeSourceList = document.getElementById("resume-source-list");
    const recentSourcesWrapper = document.getElementById("recent-sources-wrapper");
    const btnRecentSourcesToggle = document.getElementById("btn-recent-sources-toggle");
    const recentSourcesMenu = document.getElementById("recent-sources-menu");
    const recentSourcesDivider = document.getElementById("recent-sources-divider");
    const resumeSourceHeader = document.getElementById("resume-source-header");
    const processingLevelSelect = document.getElementById("processing-level");

    function closeRecentSourcesMenu() {
        if (recentSourcesMenu) recentSourcesMenu.hidden = true;
        if (btnRecentSourcesToggle) {
            btnRecentSourcesToggle.setAttribute("aria-expanded", "false");
            btnRecentSourcesToggle.classList.remove("active");
        }
    }

    function toggleRecentSourcesMenu() {
        if (!recentSourcesMenu) return;
        const willOpen = recentSourcesMenu.hidden;
        recentSourcesMenu.hidden = !willOpen;
        if (btnRecentSourcesToggle) {
            btnRecentSourcesToggle.setAttribute("aria-expanded", String(willOpen));
            if (willOpen) btnRecentSourcesToggle.classList.add("active");
            else btnRecentSourcesToggle.classList.remove("active");
        }
    }

    function renderResumeControls(turn, fallbackCitations = []) {
        const previousCitations = latestRestorableTurn?.citations || [];
        latestRestorableTurn = turn && turn.question ? {
            ...turn,
            citations: turn.citations?.length ? turn.citations : (fallbackCitations.length ? fallbackCitations : previousCitations),
        } : null;

        const hasRestorableTurn = Boolean(latestRestorableTurn);
        if (btnResumeLastQuestion) {
            btnResumeLastQuestion.hidden = !hasRestorableTurn;
            btnResumeLastQuestion.textContent = "↩ Mở câu hỏi gần nhất";
        }

        const citations = (latestRestorableTurn?.citations || []).filter(c => c && c.page_num && (c.doc_id || c.filename));
        const unique = [];
        const seen = new Set();
        for (const citation of citations) {
            const key = `${citation.doc_id || citation.filename}:${citation.page_num}`;
            if (!seen.has(key)) { seen.add(key); unique.push(citation); }
        }
        const hasSources = unique.length > 0;

        if (resumeSourceHeader) {
            resumeSourceHeader.hidden = !hasSources;
        }
        if (recentSourcesDivider) {
            recentSourcesDivider.hidden = !(hasRestorableTurn && hasSources);
        }

        if (resumeSourceList) {
            resumeSourceList.replaceChildren();
            if (hasSources) {
                for (const citation of unique) {
                    const button = document.createElement("button");
                    button.type = "button";
                    button.className = "recent-menu-source-item";
                    button.title = "Giới hạn lượt tìm kiếm mới vào trang này; nội dung và citation sẽ được truy xuất lại.";

                    const iconSpan = document.createElement("span");
                    iconSpan.className = "source-item-icon";
                    iconSpan.textContent = "📄";

                    const titleSpan = document.createElement("span");
                    titleSpan.className = "source-item-title";
                    titleSpan.textContent = citation.book_title || citation.filename || "Tài liệu";

                    const pageSpan = document.createElement("span");
                    pageSpan.className = "source-item-page";
                    pageSpan.textContent = `tr. ${citation.page_num}`;

                    button.append(iconSpan, titleSpan, pageSpan);
                    button.addEventListener("click", () => {
                        closeRecentSourcesMenu();
                        selectPreviousSource(citation);
                    });
                    resumeSourceList.appendChild(button);
                }
                resumeSourceList.hidden = false;
            } else {
                resumeSourceList.hidden = true;
            }
        }

        if (recentSourcesWrapper) {
            const shouldShowWrapper = hasRestorableTurn || hasSources;
            recentSourcesWrapper.hidden = !shouldShowWrapper;
            if (!shouldShowWrapper) {
                closeRecentSourcesMenu();
            }
        }
    }

    async function selectPreviousSource(citation) {
        if (isGenerating) return;
        const page = Number(citation.page_num);
        let docId = Number(citation.doc_id);
        if ((!Number.isInteger(docId) || docId < 1) && citation.filename && Number.isInteger(page) && page > 0) {
            try {
                const params = new URLSearchParams({ filename: citation.filename, page_num: String(page) });
                const response = await fetch(`/api/documents/source-preview?${params}`);
                if (response.ok) docId = Number((await response.json()).doc_id);
            } catch (_) {}
        }
        if (!Number.isInteger(docId) || docId < 1 || !Number.isInteger(page) || page < 1) {
            chatStatus.textContent = "Không thể khôi phục phạm vi nguồn này. Hãy chọn lại tài liệu trong thư viện.";
            return;
        }
        const title = citation.book_title || citation.filename || "Tài liệu trước";
        document.dispatchEvent(new CustomEvent("app-source-selected", {
            detail: { doc_id: docId, page_num: page, title }
        }));
        chatStatus.textContent = "Đã chọn lại trang nguồn. Khi gửi câu hỏi mới, hệ thống sẽ truy xuất lại bằng chứng cho lượt đó.";
    }

    // Stats
    const statDocs = document.getElementById("stat-docs");
    const statPages = document.getElementById("stat-pages");
    const statChunks = document.getElementById("stat-chunks");
    const statScanned = document.getElementById("stat-scanned");
    const ollamaStatus = document.getElementById("ollama-status");
    let modelReadyLabel = ""; // set by fetchOllamaModels, reused by the status poll

    // Modal elements
    const kbModal = document.getElementById("kb-modal");
    const btnOpenKb = document.getElementById("btn-open-kb");
    const btnCloseKb = document.getElementById("btn-close-kb");
    const kbTableBody = document.getElementById("kb-table-body");
    const btnReindex = document.getElementById("btn-reindex");
    const reindexModal = document.getElementById("reindex-modal");
    const reindexMsg = document.getElementById("reindex-msg");
    const reindexSpinner = document.getElementById("reindex-spinner");
    const btnCloseReindex = document.getElementById("btn-close-reindex");

    // State
    let currentSessionId = null;
    let sessionsApiSupported = null;
    let searchTimeout = null;
    let isGenerating = false;
    const chatStatus = document.createElement("p");
    chatStatus.id = "chat-request-status";
    chatStatus.setAttribute("role", "status");
    chatStatus.setAttribute("aria-live", "polite");
    chatStatus.style.cssText = "font-size:0.85rem; margin:8px 0; color:var(--text-muted);";
    chatForm.insertAdjacentElement("afterend", chatStatus);
    const initialWelcomeHTML = messagesContainer ? messagesContainer.innerHTML : "";
    let embeddingCoverage = 0;
    let clientChatHistory = [];
    let latestRestorableTurn = null;
    let viewRevision = 0;
    let isLoadingSession = true;
    btnSend.disabled = true;

    // Initialize
    fetchKnowledgeBaseStatus();
    fetchOllamaModels();
    loadSessions().then(() => {
        const savedSessionId = sessionStorage.getItem("active_session_id");
        if (savedSessionId && sessionsApiSupported) {
            return openSession(savedSessionId, { autoRestore: true }).catch(() => {
                sessionStorage.removeItem("active_session_id");
                currentSessionId = null;
            });
        }
    }).finally(() => {
        isLoadingSession = false;
        btnSend.disabled = isGenerating;
    });
    setInterval(fetchKnowledgeBaseStatus, 5000);

    // Event Listeners
    userInput.addEventListener("input", () => {
        userInput.style.height = "auto";
        userInput.style.height = Math.min(userInput.scrollHeight, 140) + "px";
    });

    let isComposing = false;
    userInput.addEventListener("compositionstart", () => {
        isComposing = true;
    });
    userInput.addEventListener("compositionend", () => {
        isComposing = false;
    });
    userInput.addEventListener("blur", () => {
        isComposing = false;
    });

    userInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            if (e.shiftKey) {
                return;
            }
            if (isComposing || e.isComposing || e.keyCode === 229) {
                return;
            }
            e.preventDefault();
            chatForm.dispatchEvent(new Event("submit"));
        }
    });

    // New Chat buttons
    if (btnClear) {
        btnClear.addEventListener("click", () => {
            if (isGenerating) {
                chatStatus.textContent = "Đang tạo câu trả lời, vui lòng chờ hoàn tất trước khi mở cuộc trò chuyện mới.";
                return;
            }
            startNewChat();
        });
    }
    if (btnNewChatSidebar) {
        btnNewChatSidebar.addEventListener("click", () => {
            if (isGenerating) {
                chatStatus.textContent = "Đang tạo câu trả lời, vui lòng chờ hoàn tất trước khi mở cuộc trò chuyện mới.";
                return;
            }
            startNewChat();
        });
    }

    if (btnRenameSession) {
        btnRenameSession.addEventListener("click", () => {
            if (currentSessionId) {
                renameSessionPrompt(currentSessionId, sessionActiveTitle.textContent);
            }
        });
    }

    if (sessionActiveTitle) {
        sessionActiveTitle.addEventListener("click", () => {
            if (currentSessionId) {
                renameSessionPrompt(currentSessionId, sessionActiveTitle.textContent);
            }
        });
    }

    if (btnSummarizeSession) {
        btnSummarizeSession.addEventListener("click", summarizeCurrentSession);
    }

    if (btnResumeLastQuestion) {
        btnResumeLastQuestion.addEventListener("click", () => {
            closeRecentSourcesMenu();
            if (isGenerating || !latestRestorableTurn) return;
            const draft = userInput.value.trim();
            userInput.value = draft && draft !== latestRestorableTurn.question
                ? `${draft}\n${latestRestorableTurn.question}`
                : latestRestorableTurn.question;
            userInput.dispatchEvent(new Event("input"));
            userInput.focus();
            chatStatus.textContent = "Câu hỏi gần nhất đã được đưa vào ô nhập; bấm Gửi nếu muốn chạy lại với nguồn và chế độ hiện tại.";
        });
    }

    if (btnRecentSourcesToggle) {
        btnRecentSourcesToggle.addEventListener("click", (e) => {
            e.stopPropagation();
            toggleRecentSourcesMenu();
        });
    }

    document.addEventListener("click", (e) => {
        if (recentSourcesWrapper && !recentSourcesWrapper.contains(e.target)) {
            closeRecentSourcesMenu();
        }
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            closeRecentSourcesMenu();
        }
    });

    if (processingLevelSelect) {
        try {
            const savedLevel = localStorage.getItem("qa_processing_level");
            if (["auto", "standard", "careful"].includes(savedLevel)) processingLevelSelect.value = savedLevel;
        } catch (_) {}
        processingLevelSelect.addEventListener("change", () => {
            try { localStorage.setItem("qa_processing_level", processingLevelSelect.value); } catch (_) {}
        });
    }

    if (btnCloseSummary) {
        btnCloseSummary.addEventListener("click", () => {
            sessionSummaryBox.style.display = "none";
        });
    }

    if (inputSessionSearch) {
        inputSessionSearch.addEventListener("input", (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                loadSessions(e.target.value.trim());
            }, 300);
        });
    }

    btnClearHistory.addEventListener("click", async () => {
        if (isGenerating) { chatStatus.textContent = "Chờ câu trả lời hoàn tất trước khi xóa lịch sử."; return; }
        if (!confirm("Xóa toàn bộ các cuộc trò chuyện và lịch sử tra cứu đã lưu trên máy này?")) return;
        try {
            if (sessionsApiSupported) {
                const deletion = await fetch("/api/sessions", { method: "DELETE" });
                if (!deletion.ok) throw new Error("Chưa xóa lịch sử. Có thể một yêu cầu khác đang xử lý.");
            } else {
                const deletion = await fetch("/api/history", { method: "DELETE" });
                if (!deletion.ok) throw new Error("Chưa xóa lịch sử. Có thể một yêu cầu khác đang xử lý.");
            }
            startNewChat();
            loadSessions();
        } catch (e) {
            console.error("Clear history error:", e);
        }
    });

    // Modal open/close
    btnOpenKb.addEventListener("click", () => {
        kbModal.classList.add("active");
        renderKbTable();
    });

    btnCloseKb.addEventListener("click", () => {
        kbModal.classList.remove("active");
    });

    kbModal.addEventListener("click", (e) => {
        if (e.target === kbModal) kbModal.classList.remove("active");
    });

    btnCloseReindex.addEventListener("click", () => {
        if (!btnCloseReindex.disabled) reindexModal.classList.remove("active");
    });

    btnReindex.addEventListener("click", async () => {
        if (btnReindex.disabled) return;
        if (!confirm("Quá trình tái lập chỉ mục sẽ quét lại toàn bộ PDF và bổ sung embedding. Bạn có muốn tiếp tục?")) return;
        btnReindex.disabled = true;
        btnCloseReindex.disabled = true;
        reindexSpinner.style.display = "";
        reindexModal.classList.add("active");
        reindexMsg.textContent = "Đang gửi yêu cầu lập chỉ mục tới máy chủ...";

        try {
            const res = await fetch("/api/index", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ force: false, ocr: true })
            });
            const data = await res.json();
            if (res.ok) {
                reindexMsg.textContent = `✅ Thành công! Đã xử lý ${data.report.total_files} tài liệu, tạo ${data.report.total_chunks_created} đoạn chỉ mục.`;
                setTimeout(() => {
                    reindexModal.classList.remove("active");
                    fetchKnowledgeBaseStatus();
                }, 2000);
            } else {
                reindexMsg.textContent = `❌ Lỗi: ${data.detail || 'Không xác định'}`;
            }
        } catch (err) {
            reindexMsg.textContent = `❌ Lỗi kết nối: ${err.message}`;
        } finally {
            reindexSpinner.style.display = "none";
            btnCloseReindex.disabled = false;
            btnReindex.disabled = false;
        }
    });

    function submitRecoveryQuery(query, nextScope, verifiedScope = {}) {
        if (isGenerating) return;
        if (nextScope === "library") {
            clearSourceScope();
        } else if ((nextScope === "document" || nextScope === "page") && verifiedScope.document_id) {
            const current = selectedSourcePage;
            selectedSourcePage = {
                doc_id: Number(verifiedScope.document_id),
                page_num: Number(verifiedScope.page_num || current?.page_num || 1),
                title: current?.doc_id === Number(verifiedScope.document_id) ? current.title : `Tài liệu ${verifiedScope.document_id}`,
            };
            selectedSourceMode = nextScope;
            updateSourceScopeLabel();
        }
        const draft = userInput.value;
        userInput.value = query;
        userInput.dispatchEvent(new Event("input"));
        chatForm.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        if (draft && draft.trim() && draft.trim() !== query) {
            userInput.value = draft;
            userInput.dispatchEvent(new Event("input"));
        }
    }

    function openSourceLookupWithQuestion(question) {
        if (!userInput.value.trim()) userInput.value = question;
        else if (userInput.value.trim() !== question) userInput.value = `${userInput.value.trim()}\n${question}`;
        userInput.dispatchEvent(new Event("input"));
        document.dispatchEvent(new CustomEvent("app-open-tool-tab", { detail: "practice" }));
        const lookup = document.getElementById("document-query");
        if (lookup && !lookup.value.trim()) lookup.value = question.slice(0, 500);
        lookup?.focus();
        chatStatus.textContent = "Tìm và chọn nguồn để giới hạn lượt truy xuất mới. Câu hỏi gốc vẫn được giữ trong ô nhập và chưa được gửi lại.";
    }

    function addRecoveryActions(assistantMsgEl, query, searchScope, options = {}) {
        if (!assistantMsgEl || assistantMsgEl.querySelector(".qa-recovery-actions")) return;
        const actions = document.createElement("div");
        actions.className = "qa-recovery-actions";
        const kind = searchScope?.kind || "library";
        const addButton = (label, handler) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "btn btn-outline btn-sm";
            button.textContent = label;
            button.addEventListener("click", handler);
            actions.appendChild(button);
        };
        const focusQuestionForEdit = () => {
            if (!userInput.value.trim()) userInput.value = query;
            else if (userInput.value.trim() !== query) userInput.value = `${userInput.value.trim()}\n${query}`;
            userInput.dispatchEvent(new Event("input"));
            userInput.focus();
            chatStatus.textContent = "Hãy chỉnh câu hỏi trong ô nhập rồi bấm Gửi.";
        };
        const suggested = Array.isArray(options.suggestedActions) ? options.suggestedActions : [];
        if (suggested.length) {
            for (const item of suggested) {
                const handler = {
                    retry: () => submitRecoveryQuery(query, kind, searchScope || {}),
                    expand_to_document: () => submitRecoveryQuery(query, "document", searchScope),
                    expand_library: () => submitRecoveryQuery(query, "library", searchScope),
                    select_other_document: () => openSourceLookupWithQuestion(query),
                    select_other_page: () => openSourceLookupWithQuestion(query),
                    rephrase_query: focusQuestionForEdit,
                    focus_single_concept: focusQuestionForEdit,
                }[item.action] || (() => openSourceLookupWithQuestion(query));
                addButton(item.label || "Tiếp tục", handler);
            }
        } else if (options.technical) {
            addButton("Thử lại câu hỏi", () => submitRecoveryQuery(query, kind, searchScope || {}));
            addButton("Sửa câu hỏi rồi thử lại", focusQuestionForEdit);
            addButton("Chọn tài liệu hoặc trang khác", () => openSourceLookupWithQuestion(query));
        } else {
            if (kind === "page") {
                addButton("Mở rộng ra toàn tài liệu", () => submitRecoveryQuery(query, "document", searchScope));
                addButton("Tìm trong toàn bộ thư viện", () => submitRecoveryQuery(query, "library", searchScope));
            } else if (kind === "document") {
                addButton("Mở rộng ra toàn bộ thư viện", () => submitRecoveryQuery(query, "library", searchScope));
            }
            addButton("Sửa câu hỏi rồi thử lại", () => {
                focusQuestionForEdit();
            });
            addButton("Chọn trang hoặc tài liệu khác", () => openSourceLookupWithQuestion(query));
        }
        assistantMsgEl.querySelector(".message-body")?.appendChild(actions);
    }

    // Chat form submit
    chatForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (isComposing) return;
        const query = userInput.value.trim();
        if (isGenerating) {
            chatStatus.textContent = "Đang xử lý câu hỏi trước. Câu mới được giữ trong ô nhập; bạn gửi lại sau khi câu trước hoàn tất.";
            return;
        }
        if (!query || isLoadingSession) return;
        if (query.length > 4000) {
            chatStatus.textContent = `Câu hỏi dài ${query.length.toLocaleString("vi-VN")} ký tự, vượt quá giới hạn 4.000 ký tự. Vui lòng rút ngắn câu hỏi trước khi gửi.`;
            userInput.focus();
            return;
        }
        chatStatus.textContent = "";
        viewRevision += 1;

        // Snapshot target session ID and generate unique request ID
        const targetSessionId = currentSessionId;
        const requestId = "req_" + Date.now() + "_" + Math.random().toString(36).substring(2, 8);

        // Reset input
        userInput.value = "";
        userInput.style.height = "auto";

        // Remove welcome card and exit welcome-mode
        const welcomeCard = messagesContainer.querySelector(".welcome-card");
        if (welcomeCard) welcomeCard.remove();
        const qaPanel = document.getElementById("qa-panel");
        if (qaPanel) qaPanel.classList.remove("welcome-mode");

        // Append User Message
        appendMessage("user", query);

        // Prepare Assistant Message Placeholder
        const assistantMsgEl = appendMessage("assistant", "");
        const contentDiv = assistantMsgEl.querySelector(".msg-content");
        contentDiv.innerHTML = '<span class="typing-indicator">Đang tra cứu thư viện và soạn câu trả lời...</span>';

        isGenerating = true;
        chatStatus.textContent = "Đang xử lý một câu hỏi. Bạn có thể soạn câu tiếp theo; câu đó chưa được gửi.";
        btnSend.disabled = true;
        if (btnClear) {
            btnClear.disabled = true;
            btnClear.title = "Đang tạo câu trả lời, vui lòng chờ hoàn tất trước khi mở cuộc trò chuyện mới.";
        }
        if (btnNewChatSidebar) {
            btnNewChatSidebar.disabled = true;
            btnNewChatSidebar.title = "Đang tạo câu trả lời, vui lòng chờ hoàn tất trước khi mở cuộc trò chuyện mới.";
        }

        let accumulatedAnswer = "";
        let completionReceived = false;
        let streamFailed = false;
        let finalCitations = [];
        let responseKind = "";
        let responseScope = {};
        let historyWasTrimmed = false;
        let passagesWereTrimmed = false;

        try {
            const reqBody = {
                query: query,
                temperature: 0.2,
                processing_level: processingLevelSelect?.value || "auto",
                session_id: targetSessionId,
                request_id: requestId
            };
            if (selectedSourcePage) {
                reqBody.source_document_id = selectedSourcePage.doc_id;
                if (selectedSourceMode === "page") reqBody.source_page_num = selectedSourcePage.page_num;
            }
            if (!sessionsApiSupported && clientChatHistory.length > 0) {
                reqBody.chat_history = clientChatHistory.slice(-6);
            }

            const response = await fetch("/api/chat/stream", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(reqBody)
            });

            if (!response.ok) {
                if (response.status === 503) {
                    let errMsg = "GPU hiện đang bận huấn luyện mô hình MLX LoRA. Vui lòng thử lại sau ít phút.";
                    try {
                        const errData = await response.json();
                        if (errData.detail) errMsg = errData.detail;
                    } catch (_) {}
                    throw new Error(errMsg);
                }
                if (response.status === 422) {
                    let errMsg = "Câu hỏi vượt quá giới hạn 4.000 ký tự hoặc dữ liệu không hợp lệ. Vui lòng kiểm tra lại.";
                    try {
                        const errData = await response.json();
                        if (typeof errData.detail === "string") errMsg = errData.detail;
                        else if (Array.isArray(errData.detail) && errData.detail[0]?.msg) errMsg = errData.detail[0].msg;
                    } catch (_) {}
                    throw new Error(errMsg);
                }
                let detail = `Mã lỗi máy chủ: ${response.status}`;
                try { const data = await response.json(); if (typeof data.detail === "string") detail = data.detail; } catch (_) {}
                throw new Error(detail);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");
            let buffer = "";

            function handleEvent(event) {
                if (event.type === "token") {
                    if (accumulatedAnswer === "") {
                        contentDiv.innerHTML = "";
                    }
                    accumulatedAnswer += event.content;
                    contentDiv.innerHTML = renderMarkdown(accumulatedAnswer);
                    scrollToBottom();
                } else if (event.type === "citations") {
                    finalCitations = event.citations || [];
                } else if (event.type === "crisis") {
                    accumulatedAnswer = event.content;
                    assistantMsgEl.querySelector(".message-body")?.classList.add("crisis-box");
                    contentDiv.innerHTML = renderMarkdown(accumulatedAnswer);
                } else if (event.type === "context") {
                    if (event.history_turns_omitted || event.store_history_turns_omitted) historyWasTrimmed = true;
                    if (event.passages_omitted) passagesWereTrimmed = true;
                } else if (event.type === "done") {
                    completionReceived = true;
                    responseKind = event.response_kind || "";
                    responseScope = event.search_scope || {};
                    if (event.cached_replay && event.answer) {
                        accumulatedAnswer = event.answer;
                        contentDiv.innerHTML = renderMarkdown(accumulatedAnswer);
                    }
                    if (!finalCitations.length && event.citations) {
                        finalCitations = event.citations;
                    }
                    if (event.is_truncated) {
                        const truncNotice = document.createElement("div");
                        truncNotice.className = "truncation-warning";
                        truncNotice.style.cssText = "margin-top: 8px; font-size: 0.85rem; color: #b45309; background: #fef3c7; border: 1px solid #fde68a; border-radius: 6px; padding: 4px 8px;";
                        truncNotice.textContent = "⚠️ Câu trả lời đạt giới hạn token tối đa nên có thể chưa hoàn tất trọn vẹn.";
                        assistantMsgEl.querySelector(".message-body")?.appendChild(truncNotice);
                    }
                    if (finalCitations && finalCitations.length > 0) {
                        renderCitations(assistantMsgEl, finalCitations);
                    }
                    if (event.resolved_processing_level) {
                        const metaBox = document.createElement("div");
                        metaBox.className = "processing-meta text-muted";
                        metaBox.style.cssText = "margin-top: 6px; font-size: 0.8rem; color: var(--text-muted, #6b7280);";
                        const levelLabel = event.resolved_processing_level === "careful" ? "Kiểm tra kỹ" : "Thông thường";
                        let statusText = "";
                        if (event.check_status === "verified") {
                            statusText = " · Đã qua đối chiếu tự động";
                        } else if (event.check_status === "partially_verified") {
                            statusText = " · Đối chiếu một phần";
                        } else if (event.check_status === "unverified") {
                            statusText = " · Chưa xác minh";
                        }
                        const callsText = event.generation_calls ? ` (${event.generation_calls} lượt xử lý)` : "";
                        metaBox.textContent = `Mức xử lý: ${levelLabel}${statusText}${callsText}`;
                        assistantMsgEl.querySelector(".message-body")?.appendChild(metaBox);
                    }
                    if (responseKind === "insufficient_evidence") {
                        addRecoveryActions(assistantMsgEl, query, responseScope, {
                            suggestedActions: event.suggested_actions || [],
                        });
                    }
                    if (historyWasTrimmed || passagesWereTrimmed) {
                        const notice = document.createElement("div");
                        notice.className = "context-trim-notice";
                        notice.textContent = historyWasTrimmed
                            ? "Một phần lịch sử hội thoại đã không được đưa vào ngữ cảnh của lượt này. Nêu lại ý cần tiếp tục nếu thiếu thông tin; citation từ lượt trước không được dùng làm bằng chứng mới."
                            : "Một số đoạn truy xuất không vừa cửa sổ ngữ cảnh. Câu trả lời dùng các đoạn đã được đưa vào và kiểm tra trong lượt này.";
                        assistantMsgEl.querySelector(".message-body")?.appendChild(notice);
                    }
                    // Update session ID if newly created
                    if (event.session_id) {
                        currentSessionId = event.session_id;
                        sessionStorage.setItem("active_session_id", event.session_id);
                        if (sessionActiveTitle) sessionActiveTitle.textContent = event.session_title || "Cuộc trò chuyện";
                        if (sessionBar) sessionBar.style.display = "flex";
                        loadSessions(inputSessionSearch ? inputSessionSearch.value.trim() : "");
                    } else if (!sessionsApiSupported) {
                        clientChatHistory.push({ role: "user", content: query });
                        clientChatHistory.push({ role: "assistant", content: accumulatedAnswer });
                    }
                    try {
                        renderResumeControls({ question: query, citations: finalCitations });
                    } catch (rcErr) {
                        console.error("renderResumeControls error:", rcErr);
                    }
                } else if (event.type === "error") {
                    streamFailed = true;
                    const prefix = event.gpu_busy ? "⚠️" : "❌ Lỗi:";
                    accumulatedAnswer += `\n\n${prefix} ${event.content}`;
                    contentDiv.innerHTML = renderMarkdown(accumulatedAnswer);
                    responseKind = event.response_kind || "technical_error";
                    responseScope = event.search_scope || {};
                    addRecoveryActions(assistantMsgEl, query, responseScope, {
                        technical: true,
                        suggestedActions: event.suggested_actions || [],
                    });
                }
            }

            function processRawLine(line) {
                if (!line.startsWith("data: ")) return;
                const jsonStr = line.replace(/^data: /, "").trim();
                if (!jsonStr) return;
                let event;
                try {
                    event = JSON.parse(jsonStr);
                } catch (pe) {
                    console.error("Parse event error:", pe);
                    return;
                }
                try {
                    handleEvent(event);
                } catch (handlerErr) {
                    console.error("Event handler error:", handlerErr);
                    if (!completionReceived) {
                        streamFailed = true;
                    }
                }
            }

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n\n");
                buffer = lines.pop();

                for (const line of lines) {
                    processRawLine(line);
                }
            }

            buffer += decoder.decode();
            if (buffer.trim()) {
                const remainingLines = buffer.split("\n\n");
                for (const line of remainingLines) {
                    processRawLine(line);
                }
            }

            if (!completionReceived && !streamFailed) {
                throw new Error("Kết nối kết thúc trước khi câu trả lời hoàn tất. Kết quả chưa được xác nhận; hãy mở lại phiên để kiểm tra trước khi gửi lại.");
            }
        } catch (err) {
            if (!completionReceived) {
                const isBusy = err.message.includes("GPU") || err.message.includes("bận");
                const prefix = isBusy ? "⚠️" : "❌";
                const errorText = document.createElement("p");
                errorText.className = isBusy ? "text-warning" : "text-danger";
                errorText.textContent = `${prefix} ${err.message}`;
                contentDiv.replaceChildren(errorText);
                const failedScope = responseScope.kind ? responseScope : (selectedSourcePage ? {
                    kind: selectedSourceMode,
                    document_id: selectedSourcePage.doc_id,
                    ...(selectedSourceMode === "page" ? { page_num: selectedSourcePage.page_num } : {}),
                } : { kind: "library" });
                addRecoveryActions(assistantMsgEl, query, failedScope, { technical: true });
            } else {
                console.error("Post-completion stream error:", err);
            }
        } finally {
            isGenerating = false;
            if (!completionReceived) {
                if (userInput.value.trim()) {
                    chatStatus.textContent = "Lượt xử lý gặp lỗi. Câu bạn đang soạn được giữ nguyên; dùng các nút bên dưới câu trả lời để thử lại hoặc sửa câu trước.";
                } else {
                    chatStatus.textContent = "Lượt xử lý gặp lỗi. Dùng các nút bên dưới câu trả lời để thử lại, sửa câu hỏi hoặc chọn nguồn khác.";
                }
            } else if (userInput.value.trim()) {
                chatStatus.textContent = "Câu trước đã hoàn tất. Câu bạn đang soạn chưa được gửi.";
            } else {
                chatStatus.textContent = "";
            }
            btnSend.disabled = false;
            if (btnClear) {
                btnClear.disabled = false;
                btnClear.title = "Bắt đầu cuộc trò chuyện mới";
            }
            if (btnNewChatSidebar) {
                btnNewChatSidebar.disabled = false;
                btnNewChatSidebar.title = "Bắt đầu cuộc trò chuyện mới";
            }
            userInput.focus();
        }
    });

    // =========================================================================
    // Session & History Handlers
    // =========================================================================

    let currentSessionOffset = 0;
    async function loadSessions(searchQuery = "", offset = 0, append = false) {
        // Capability detection on first load
        if (sessionsApiSupported === null) {
            try {
                const testRes = await fetch("/api/sessions?limit=1");
                sessionsApiSupported = testRes.ok;
            } catch (_) {
                sessionsApiSupported = false;
            }
        }

        if (!sessionsApiSupported) {
            return fetchLegacyHistory();
        }

        try {
            const limit = 50;
            const url = searchQuery
                ? `/api/sessions/search?q=${encodeURIComponent(searchQuery)}&limit=${limit}&offset=${offset}`
                : `/api/sessions?limit=${limit}&offset=${offset}`;
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            const items = data.items || [];

            if (!append) {
                historyList.replaceChildren();
                currentSessionOffset = 0;
            }
            currentSessionOffset = offset + items.length;

            if (!append && !items.length) {
                historyList.textContent = searchQuery ? "Không tìm thấy cuộc trò chuyện phù hợp." : "Chưa có cuộc trò chuyện nào.";
                return;
            }

            const oldMoreBtn = document.getElementById("btn-load-more-sessions");
            if (oldMoreBtn) oldMoreBtn.remove();

            let hasLegacyDivider = false;
            items.forEach((s) => {
                if (s.is_legacy && !hasLegacyDivider && !searchQuery) {
                    const divider = document.createElement("div");
                    divider.className = "legacy-history-divider";
                    divider.textContent = "📜 Lịch sử cũ (đơn lượt)";
                    historyList.appendChild(divider);
                    hasLegacyDivider = true;
                }

                const itemEl = document.createElement("div");
                itemEl.className = "session-item" + (s.id === currentSessionId ? " active" : "");
                itemEl.dataset.sessionId = s.id;

                const infoBtn = document.createElement("button");
                infoBtn.className = "session-info";

                const titleSpan = document.createElement("span");
                titleSpan.className = "session-title-text";
                titleSpan.textContent = s.title || "Cuộc trò chuyện";
                titleSpan.title = s.title;

                const metaDiv = document.createElement("div");
                metaDiv.className = "session-meta";

                const badge = document.createElement("span");
                badge.className = "session-badge";
                badge.textContent = `${s.turn_count || 1} lượt`;

                const timeSpan = document.createElement("span");
                timeSpan.textContent = formatTime(s.updated_at);

                metaDiv.append(badge, timeSpan);
                infoBtn.append(titleSpan, metaDiv);

                const actionsDiv = document.createElement("div");
                actionsDiv.className = "session-actions";

                const btnRen = document.createElement("button");
                btnRen.className = "session-action-btn";
                btnRen.textContent = "✏️";
                btnRen.title = "Đổi tên cuộc trò chuyện";
                btnRen.onclick = (e) => {
                    e.stopPropagation();
                    renameSessionPrompt(s.id, s.title);
                };

                const btnDel = document.createElement("button");
                btnDel.className = "session-action-btn btn-del";
                btnDel.textContent = "×";
                btnDel.title = "Xóa cuộc trò chuyện";
                btnDel.onclick = (e) => {
                    e.stopPropagation();
                    deleteSession(s.id);
                };

                actionsDiv.append(btnRen, btnDel);
                itemEl.append(infoBtn, actionsDiv);

                infoBtn.onclick = () => openSession(s.id);
                historyList.appendChild(itemEl);
            });

            if (data.total > currentSessionOffset) {
                const moreBtn = document.createElement("button");
                moreBtn.id = "btn-load-more-sessions";
                moreBtn.className = "btn btn-sm";
                moreBtn.style.cssText = "width: 100%; margin: 8px 0; font-size: 0.78rem; background: var(--bg-card);";
                moreBtn.textContent = `Tải thêm (${currentSessionOffset}/${data.total})...`;
                moreBtn.onclick = () => loadSessions(searchQuery, currentSessionOffset, true);
                historyList.appendChild(moreBtn);
            }
        } catch (err) {
            console.error("Lỗi tải danh sách phiên:", err);
            fetchLegacyHistory();
        }
    }

    async function openSession(sessionId, options = {}) {
        const autoRestore = Boolean(options && options.autoRestore);
        if (!autoRestore) {
            document.dispatchEvent(new Event("app-show-qa"));
        }
        if (isGenerating) {
            alert("Đang tạo câu trả lời, vui lòng chờ hoàn tất trước khi chuyển cuộc trò chuyện.");
            return;
        }
        const draftText = userInput.value;
        const requestedView = ++viewRevision;
        isLoadingSession = true;
        btnSend.disabled = true;
        try {
            const res = await fetch(`/api/sessions/${sessionId}`);
            if (requestedView !== viewRevision) return;
            if (!res.ok) {
                if (res.status === 404) {
                    sessionStorage.removeItem("active_session_id");
                    currentSessionId = null;
                    if (!autoRestore) {
                        startNewChat();
                    } else {
                        startNewChat({ autoRestore: true });
                    }
                }
                throw new Error("Không thể tải cuộc trò chuyện");
            }
            const data = await res.json();
            if (requestedView !== viewRevision || isGenerating) return;

            currentSessionId = data.id;
            sessionStorage.setItem("active_session_id", data.id);
            sessionActiveTitle.textContent = data.title || "Cuộc trò chuyện";
            sessionBar.style.display = "flex";

            if (data.summary) {
                sessionSummaryText.textContent = "Tóm tắt dựa trên ngữ cảnh có giới hạn: " + data.summary;
                sessionSummaryBox.style.display = "block";
            } else {
                sessionSummaryBox.style.display = "none";
            }

            // Render all turns in order
            messagesContainer.innerHTML = "";
            const qaPanel = document.getElementById("qa-panel");
            if (qaPanel) qaPanel.classList.remove("welcome-mode");
            const turns = data.turns || [];
            const latestCitedTurn = [...turns].reverse().find(turn => Array.isArray(turn.citations) && turn.citations.length);
            renderResumeControls(turns.length ? turns[turns.length - 1] : null, latestCitedTurn?.citations || []);
            turns.forEach(turn => {
                appendMessage("user", turn.question);
                const asstEl = appendMessage("assistant", turn.answer);
                if (turn.citations && turn.citations.length > 0) {
                    renderCitations(asstEl, turn.citations);
                }
            });

            // Update active state in sidebar
            document.querySelectorAll(".session-item").forEach(el => {
                el.classList.toggle("active", el.dataset.sessionId === currentSessionId);
            });
            scrollToBottom();
            if (autoRestore && draftText && !userInput.value) {
                userInput.value = draftText;
            }
        } catch (err) {
            console.error("Open session error:", err);
        } finally {
            if (requestedView === viewRevision) {
                isLoadingSession = false;
                btnSend.disabled = isGenerating;
            }
        }
    }

    function startNewChat(options = {}) {
        const autoRestore = Boolean(options && options.autoRestore);
        if (!autoRestore) {
            document.dispatchEvent(new Event("app-show-qa"));
        }
        if (isGenerating) {
            chatStatus.textContent = "Đang tạo câu trả lời, vui lòng chờ hoàn tất trước khi mở cuộc trò chuyện mới.";
            return;
        }
        viewRevision += 1;
        isLoadingSession = false;
        btnSend.disabled = false;
        currentSessionId = null;
        sessionStorage.removeItem("active_session_id");
        clientChatHistory = [];
        renderResumeControls(null);
        clearSourceScope();
        if (sessionActiveTitle) sessionActiveTitle.textContent = "Cuộc trò chuyện";
        if (sessionBar) sessionBar.style.display = "none";
        if (sessionSummaryBox) sessionSummaryBox.style.display = "none";
        if (sessionSummaryText) sessionSummaryText.textContent = "";
        const qaPanel = document.getElementById("qa-panel");
        if (qaPanel) qaPanel.classList.add("welcome-mode");
        if (messagesContainer) {
            messagesContainer.innerHTML = initialWelcomeHTML || `
                <div class="message assistant welcome-card">
                    <div class="message-avatar">
                        <img src="/static/logo.jpg" alt="BaMiTiKa AI" class="hero-avatar-img">
                    </div>
                    <div class="message-body">
                        <h3>Xin chào! Bạn muốn học gì hôm nay?</h3>
                    </div>
                </div>
            `;
        }
        document.querySelectorAll(".session-item").forEach(el => el.classList.remove("active"));
        userInput.value = "";
        userInput.style.height = "auto";
        chatStatus.textContent = "";
        if (!autoRestore) {
            userInput.focus();
        }
    }

    async function renameSessionPrompt(sid, oldTitle) {
        const newTitle = await new Promise((resolve) => {
            const dialog = document.createElement("dialog");
            dialog.className = "session-rename-dialog";
            dialog.innerHTML = '<form method="dialog"><label for="session-rename-input">Tên cuộc trò chuyện</label><input id="session-rename-input" maxlength="120" required><div><button value="cancel" formnovalidate>Hủy</button><button value="save">Lưu tên</button></div></form>';
            const input = dialog.querySelector("input");
            input.value = oldTitle || "";
            dialog.addEventListener("close", () => {
                resolve(dialog.returnValue === "save" ? input.value : null);
                dialog.remove();
            }, { once: true });
            document.body.appendChild(dialog);
            dialog.showModal();
            input.focus();
            input.select();
        });
        if (!newTitle || !newTitle.trim() || newTitle.trim() === oldTitle) return;
        try {
            const res = await fetch(`/api/sessions/${sid}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: newTitle.trim() })
            });
            if (res.ok) {
                if (currentSessionId === sid) {
                    sessionActiveTitle.textContent = newTitle.trim();
                }
                loadSessions(inputSessionSearch ? inputSessionSearch.value.trim() : "");
            }
        } catch (err) {
            console.error("Rename session error:", err);
        }
    }

    async function deleteSession(sid) {
        if (isGenerating) { chatStatus.textContent = "Chờ câu trả lời hoàn tất trước khi xóa cuộc trò chuyện."; return; }
        if (!confirm("Xóa cuộc trò chuyện này và toàn bộ các lượt hỏi đáp bên trong?")) return;
        try {
            const res = await fetch(`/api/sessions/${sid}`, { method: "DELETE" });
            if (res.ok) {
                if (currentSessionId === sid) {
                    sessionStorage.removeItem("active_session_id");
                    startNewChat();
                }
                loadSessions(inputSessionSearch ? inputSessionSearch.value.trim() : "");
            }
        } catch (err) {
            console.error("Delete session error:", err);
        }
    }

    async function summarizeCurrentSession() {
        if (!currentSessionId || isGenerating || isLoadingSession) return;
        const summarySessionId = currentSessionId;
        const summaryView = viewRevision;
        btnSummarizeSession.disabled = true;
        btnSummarizeSession.textContent = "⏳ Đang tóm tắt...";
        try {
            const res = await fetch(`/api/sessions/${summarySessionId}/summarize`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({})
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            if (summarySessionId !== currentSessionId || summaryView !== viewRevision) return;
            if (data.status === "stale_revision") {
                alert("Phiên trò chuyện đã có cập nhật mới trong khi đang tóm tắt. Vui lòng bấm tóm tắt lại.");
            } else if (data.status === "gpu_busy") {
                alert("GPU hiện đang bận. Nội dung phiên vẫn được lưu; bạn có thể bấm Tóm tắt lại sau khi GPU sẵn sàng.");
            } else if (data.status === "failed") {
                alert("Tóm tắt chưa thành công: " + (data.reason || data.error || "Lỗi mô hình"));
            } else {
                if (data.summary) {
                    sessionSummaryText.textContent = "Tóm tắt dựa trên ngữ cảnh có giới hạn: " + data.summary;
                    sessionSummaryBox.style.display = "block";
                }
                if (data.title) {
                    sessionActiveTitle.textContent = data.title;
                }
            }
            loadSessions(inputSessionSearch ? inputSessionSearch.value.trim() : "");
        } catch (err) {
            alert("Không thể tóm tắt: " + err.message);
        } finally {
            btnSummarizeSession.disabled = false;
            btnSummarizeSession.textContent = "📝 Tóm tắt chủ đề";
        }
    }

    async function fetchLegacyHistory() {
        try {
            const response = await fetch("/api/history");
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const { items } = await response.json();
            historyList.replaceChildren();
            if (!items || !items.length) {
                historyList.textContent = "Chưa có lượt tra cứu nào.";
                return;
            }
            items.forEach((saved) => {
                const row = document.createElement("div");
                row.className = "history-item";
                row.dataset.historyId = saved.id;
                const open = document.createElement("button");
                open.className = "history-open";
                open.textContent = saved.question;
                open.title = `${new Date(saved.created_at).toLocaleString("vi-VN")} · ${saved.model}`;
                const remove = document.createElement("button");
                remove.className = "history-delete";
                remove.textContent = "×";
                remove.title = "Xóa lượt tra cứu này";
                remove.onclick = async (e) => {
                    e.stopPropagation();
                    const res = await fetch(`/api/history/${saved.id}`, { method: "DELETE" });
                    if (res.ok) fetchLegacyHistory();
                };
                open.onclick = async () => {
                    if (isGenerating) return;
                    const requestedView = ++viewRevision;
                    const res = await fetch(`/api/history/${saved.id}`);
                    if (!res.ok) return;
                    const data = await res.json();
                    if (requestedView !== viewRevision || isGenerating) return;
                    currentSessionId = null;
                    sessionStorage.removeItem("active_session_id");
                    clientChatHistory = [
                        { role: "user", content: data.question },
                        { role: "assistant", content: data.answer }
                    ];
                    sessionBar.style.display = "none";
                    sessionSummaryBox.style.display = "none";
                    messagesContainer.innerHTML = "";
                    appendMessage("user", data.question);
                    const asst = appendMessage("assistant", data.answer);
                    if (data.citations) renderCitations(asst, data.citations);
                };
                row.append(open, remove);
                historyList.appendChild(row);
            });
        } catch (_) {
            historyList.textContent = "Chưa tải được lịch sử.";
        }
    }

    function formatTime(isoStr) {
        if (!isoStr) return "";
        try {
            const d = new Date(isoStr);
            const now = new Date();
            const diffMin = Math.floor((now - d) / 60000);
            if (diffMin < 1) return "Vừa xong";
            if (diffMin < 60) return `${diffMin}p trước`;
            const diffHour = Math.floor(diffMin / 60);
            if (diffHour < 24) return `${diffHour}h trước`;
            return d.toLocaleDateString("vi-VN", { day: "numeric", month: "numeric" });
        } catch (_) {
            return "";
        }
    }

    function renderCitations(assistantMsgEl, citations) {
        if (!citations || !citations.length) return;
        const msgBody = assistantMsgEl.querySelector(".message-body");
        if (!msgBody) return;
        const citBox = document.createElement("div");
        citBox.className = "citations-block";
        const heading = document.createElement("strong");
        heading.className = "citations-heading";
        heading.textContent = "📚 Đối chiếu với tài liệu gốc:";
        citBox.appendChild(heading);
        citations.forEach((c, index) => {
            const detail = document.createElement("details");
            detail.className = "citation-detail";
            detail.open = index === 0;
            const summary = document.createElement("summary");
            summary.className = "citation-summary";
            const page = Number(c.page_num || c.page);
            const isPdf = Boolean(c.filename && c.filename.toLowerCase().endsWith(".pdf"));
            const unitLabel = isPdf ? "trang" : "phần";
            summary.textContent = `[${c.id || 'S'}] ${c.book_title || c.filename || c.book || ''} · ${unitLabel} ${page || '?'}`;
            detail.appendChild(summary);
            if (c.page_quality?.needs_review) {
                const notice = document.createElement("p");
                notice.className = "source-review-notice context-trim-notice";
                notice.textContent = "Trang nguồn này chưa được duyệt đầy đủ. Cần đối chiếu với tài liệu gốc, nhất là hình, bảng và công thức.";
                detail.appendChild(notice);
            }
            if (Array.isArray(c.claims) && c.claims.length > 0) {
                const claimsContainer = document.createElement("div");
                claimsContainer.className = "citation-claims-block";
                const claimsHeading = document.createElement("div");
                claimsHeading.className = "citation-claims-heading";
                claimsHeading.textContent = "Nhận định gắn nguồn:";
                claimsContainer.appendChild(claimsHeading);
                const claimsList = document.createElement("ul");
                claimsList.className = "citation-claims-list";
                c.claims.forEach(claim => {
                    if (typeof claim === "string" && claim.trim()) {
                        const li = document.createElement("li");
                        li.textContent = claim.trim();
                        claimsList.appendChild(li);
                    }
                });
                claimsContainer.appendChild(claimsList);
                detail.appendChild(claimsContainer);
            }
            if (typeof c.snippet === "string" && c.snippet.trim()) {
                const quote = document.createElement("blockquote");
                quote.className = "citation-snippet";
                quote.textContent = c.snippet.trim();
                detail.appendChild(quote);
            }
            async function attachOriginalPage() {
                let id = Number(c.doc_id);
                if (!Number.isInteger(page) || page < 1) return;
                if ((!Number.isInteger(id) || id < 1) && c.filename) {
                    try {
                        const params = new URLSearchParams({filename:c.filename,page_num:String(page)});
                        const response = await fetch(`/api/documents/source-preview?${params}`);
                        if (!response.ok) return;
                        id = Number((await response.json()).doc_id);
                    } catch (_) { return; }
                }
                if (!Number.isInteger(id) || id < 1) return;

                if (isPdf) {
                    const link = document.createElement("a");
                    link.className = "citation-preview-link";
                    link.href = `/api/documents/${id}/pdf#page=${page}`;
                    link.target = "_blank";
                    link.rel = "noopener noreferrer";
                    link.textContent = `📄 Mở trang PDF ${page} để đối chiếu`;
                    detail.appendChild(link);
                } else {
                    const textNotice = document.createElement("p");
                    textNotice.className = "citation-text-fallback";
                    textNotice.textContent = "📝 Trích đoạn từ tệp văn bản / Markdown nguồn (không có bản scan ảnh trang).";
                    detail.appendChild(textNotice);
                }
            }
            attachOriginalPage();
            citBox.appendChild(detail);
        });
        msgBody.appendChild(citBox);
    }

    function appendMessage(role, text) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `message ${role}`;

        const avatar = role === "user" ? "👤" : "🧠";
        msgDiv.innerHTML = `
            <div class="message-avatar">${avatar}</div>
            <div class="message-body">
                <div class="msg-content">${text ? renderMarkdown(text) : ""}</div>
            </div>
        `;
        messagesContainer.appendChild(msgDiv);
        scrollToBottom();
        return msgDiv;
    }

    async function fetchKnowledgeBaseStatus() {
        try {
            const res = await fetch("/api/status");
            if (!res.ok) {
                if (ollamaStatus) {
                    ollamaStatus.textContent = `🔴 Máy chủ web phản hồi lỗi HTTP ${res.status}`;
                    ollamaStatus.className = "status-indicator disconnected";
                }
                return;
            }
            const data = await res.json();
            const kb = data.knowledge_base || {};

            // Update GPU / Training banner
            const trainingBanner = document.getElementById("training-banner");
            const trainingBannerText = document.getElementById("training-banner-text");
            if (data.gpu && data.gpu.busy) {
                if (trainingBanner) {
                    trainingBanner.style.display = "flex";
                    if (data.gpu.message && trainingBannerText) {
                        trainingBannerText.textContent = data.gpu.message;
                    }
                }
                if (ollamaStatus) {
                    ollamaStatus.textContent = "🟡 Đang huấn luyện MLX LoRA (GPU bận - Web hoạt động, suy luận tạm hoãn)";
                    ollamaStatus.className = "status-indicator";
                }
            } else {
                if (trainingBanner) {
                    trainingBanner.style.display = "none";
                }
                if (ollamaStatus) {
                    if (data.inference_ready) {
                        ollamaStatus.textContent = modelReadyLabel || "🟢 Mô hình hỏi đáp sẵn sàng";
                        ollamaStatus.className = "status-indicator connected";
                    } else {
                        ollamaStatus.textContent = `🔴 ${data.service_message || "Mô hình hỏi đáp chưa sẵn sàng"}`;
                        ollamaStatus.className = "status-indicator disconnected";
                    }
                }
            }

            statDocs.textContent = kb.total_documents || 0;
            btnOpenKb.textContent = `Xem chi tiết ${kb.total_documents || 0} tài liệu`;
            statPages.textContent = kb.total_indexed_pages || 0;
            statChunks.textContent = kb.total_chunks || 0;
            statScanned.textContent = kb.scanned_documents || 0;
            embeddingCoverage = kb.total_chunks ? (kb.embedded_chunks || 0) / kb.total_chunks : 0;

            window.kbData = kb.documents || [];
        } catch (e) {
            console.error("Status fetch error:", e);
            if (ollamaStatus) {
                ollamaStatus.textContent = "🔴 Máy chủ web backend không phản hồi (Port 8000)";
                ollamaStatus.className = "status-indicator disconnected";
            }
        }
    }

    async function fetchOllamaModels() {
        try {
            const res = await fetch("/api/models");
            if (!res.ok) throw new Error("Không đọc được trạng thái mô hình");
            const data = await res.json();
            const name = data.default_model;
            if (!name) throw new Error("Chưa có cấu hình mô hình văn bản");
            const usesMlx = data.default_model_backend === "mlx";
            const adapted = data.model_mode !== "unadapted_base";
            if (ollamaStatus) {
                if (data.default_model_available) {
                    modelReadyLabel = usesMlx
                        ? (adapted ? "🟢 Mô hình đã kiểm định sẵn sàng" : "🟢 Mô hình gốc sẵn sàng")
                        : "🟢 Mô hình phát triển Ollama sẵn sàng";
                    ollamaStatus.textContent = modelReadyLabel;
                    ollamaStatus.className = "status-indicator connected";
                } else {
                    ollamaStatus.textContent = "🟡 Mô hình văn bản chưa sẵn sàng";
                    ollamaStatus.className = "status-indicator";
                }
            }
        } catch (error) {
            if (ollamaStatus) {
                ollamaStatus.textContent = "🔴 Chưa đọc được trạng thái mô hình";
                ollamaStatus.className = "status-indicator disconnected";
            }
            console.error("Model status error", error);
        }
    }
    const fetchModels = fetchOllamaModels;

    function renderKbTable() {
        const docs = window.kbData || [];
        if (docs.length === 0) {
            kbTableBody.innerHTML = '<tr><td colspan="5" style="text-align:center;">Chưa có dữ liệu chỉ mục. Hãy nhấn "Tái lập chỉ mục".</td></tr>';
            return;
        }

        let html = "";
        docs.forEach((d, idx) => {
            let statusBadge = `<span class="badge-status indexed">Đã lập chỉ mục</span>`;
            if (d.is_scanned) {
                const captured = d.ocr_captured_pages || 0;
                const reviewed = d.ocr_reviewed_pages || 0;
                const label = captured ? `OCR ${captured}/${d.total_pages} trang · đã duyệt nội dung ${reviewed} trang` : "Scan · chưa có bản OCR";
                statusBadge = `<span class="badge-status scanned" title="Bản OCR thô cần đối chiếu; phần đã duyệt có thể chỉ bao phủ một đoạn trên trang">${label}</span>`;
            } else if (d.status === "error") {
                statusBadge = `<span class="badge-status error" title="${escapeHtml(d.error_message || '')}">Lỗi trích xuất</span>`;
            }

            html += `
                <tr>
                    <td>${idx + 1}</td>
                    <td>
                        <strong>${escapeHtml(d.clean_title)}</strong>
                        <div class="text-sm text-muted">${escapeHtml(d.filename)}</div>
                    </td>
                    <td>${d.total_pages || 0}</td>
                    <td>${d.extracted_pages_count || 0}</td>
                    <td>${statusBadge}</td>
                </tr>
            `;
        });
        kbTableBody.innerHTML = html;
    }

    function scrollToBottom() {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    function escapeHtml(str) {
        if (!str) return "";
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function renderMarkdown(md) {
        if (!md) return "";
        let text = escapeHtml(md);

        // Bold
        text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        // Italic
        text = text.replace(/\*(.*?)\*/g, '<em>$1</em>');
        // Inline code
        text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
        // Block headers
        text = text.replace(/^### (.*$)/gim, '<h4>$1</h4>');
        text = text.replace(/^## (.*$)/gim, '<h3>$1</h3>');
        text = text.replace(/^# (.*$)/gim, '<h2>$1</h2>');
        // Bullet points
        text = text.replace(/^\s*•\s+(.*$)/gim, '<li>$1</li>');
        text = text.replace(/^\s*\*\s+(.*$)/gim, '<li>$1</li>');
        text = text.replace(/^\s*-\s+(.*$)/gim, '<li>$1</li>');
        // Numbered list
        text = text.replace(/^\s*(\d+)\.\s+(.*$)/gim, '<li value="$1">$2</li>');

        // Line breaks
        text = text.replace(/\n\n+/g, '</p><p>');
        text = text.replace(/\n/g, '<br/>');

        return `<p>${text}</p>`;
    }

    // OCR Review Elements & Handlers
    const btnOpenOcrReview = document.getElementById("btn-open-ocr-review");
    const ocrModal = document.getElementById("ocr-modal");
    const btnCloseOcrModal = document.getElementById("btn-close-ocr-modal");
    const btnRefreshOcrList = document.getElementById("btn-refresh-ocr-list");
    const ocrTableBody = document.getElementById("ocr-table-body");
    const ocrEditorPane = document.getElementById("ocr-editor-pane");
    const ocrEditorDocinfo = document.getElementById("ocr-editor-docinfo");
    const ocrEditorText = document.getElementById("ocr-editor-text");
    const ocrEditorReviewer = document.getElementById("ocr-editor-reviewer");
    const ocrEditorNotes = document.getElementById("ocr-editor-notes");
    const btnCancelOcrEdit = document.getElementById("btn-cancel-ocr-edit");
    const btnSaveOcrReview = document.getElementById("btn-save-ocr-review");
    const ocrReviewStatus = document.getElementById("ocr-review-status");

    let currentOcrKey = null;

    if (btnOpenOcrReview && ocrModal) {
        btnOpenOcrReview.addEventListener("click", () => {
            ocrModal.classList.add("active");
            ocrModal.style.display = "flex";
            loadOcrReviews();
            populateOcrDocDropdown();
        });
        if (btnCloseOcrModal) {
            btnCloseOcrModal.addEventListener("click", () => {
                ocrModal.classList.remove("active");
                ocrModal.style.display = "none";
            });
        }
        window.addEventListener("click", (e) => {
            if (e.target === ocrModal) {
                ocrModal.classList.remove("active");
                ocrModal.style.display = "none";
            }
        });
        window.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && ocrModal.classList.contains("active")) {
                ocrModal.classList.remove("active");
                ocrModal.style.display = "none";
            }
        });
        if (btnRefreshOcrList) {
            btnRefreshOcrList.addEventListener("click", loadOcrReviews);
        }
        if (btnCancelOcrEdit) {
            btnCancelOcrEdit.addEventListener("click", () => {
                ocrEditorPane.style.display = "none";
                currentOcrKey = null;
            });
        }
        if (btnSaveOcrReview) {
            btnSaveOcrReview.addEventListener("click", saveOcrReview);
        }
    }

    async function loadOcrReviews() {
        if (!ocrTableBody) return;
        ocrTableBody.innerHTML = '<tr><td colspan="5" style="text-align: center;">Đang tải danh sách...</td></tr>';
        if (ocrEditorPane) ocrEditorPane.style.display = "none";
        try {
            const res = await fetch("/api/ocr/reviews");
            if (!res.ok) throw new Error("Lỗi tải danh sách OCR");
            const data = await res.json();
            const records = data.records || [];
            if (records.length === 0) {
                ocrTableBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">Chưa có bản ghi OCR nào trong bộ nhớ tạm.</td></tr>';
                return;
            }
            let html = "";
            records.forEach(r => {
                const statusBadge = r.is_verified
                    ? '<span class="badge" style="background:#e6f4ea;color:#137333;">Đã xác minh</span>'
                    : '<span class="badge" style="background:#fef7e0;color:#b06000;" title="Bản chép cần đối chiếu trang gốc trước khi dùng để huấn luyện">Cần đối chiếu trang gốc</span>';
                html += `
                    <tr>
                        <td><strong>${escapeHtml(r.source_relpath || "N/A")}</strong></td>
                        <td>Trang ${r.page_num || 1}</td>
                        <td><code>${escapeHtml(r.backend || "")}</code></td>
                        <td>${statusBadge}</td>
                        <td>
                            <button class="btn btn-sm btn-outline btn-review-ocr" data-key="${escapeHtml(r.cache_key)}">
                                ${r.is_verified ? "Xem lại" : "✏️ Soát lỗi & Duyệt"}
                            </button>
                        </td>
                    </tr>
                `;
            });
            ocrTableBody.innerHTML = html;
            ocrTableBody.querySelectorAll(".btn-review-ocr").forEach(btn => {
                btn.addEventListener("click", () => {
                    const key = btn.dataset.key;
                    const rec = records.find(item => item.cache_key === key);
                    if (rec) openOcrEditor(rec);
                });
            });
        } catch (e) {
            ocrTableBody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: red;">${escapeHtml(e.message)}</td></tr>`;
        }
    }

    function openOcrEditor(rec) {
        currentOcrKey = rec.cache_key;
        if (ocrEditorDocinfo) {
            ocrEditorDocinfo.textContent = `Tài liệu: ${rec.source_relpath || "N/A"} - Trang ${rec.page_num || 1} (Hash: ${rec.source_hash ? rec.source_hash.slice(0, 12) + "..." : "N/A"})`;
        }
        const ocrPreviewImg = document.getElementById("ocr-preview-img");
        if (ocrPreviewImg && rec.source_relpath) {
            ocrPreviewImg.src = `/api/ocr/page-image?filename=${encodeURIComponent(rec.source_relpath)}&page_num=${rec.page_num || 1}`;
        }
        if (ocrEditorText) ocrEditorText.value = rec.text || rec.raw_text || "";
        if (ocrEditorReviewer) ocrEditorReviewer.value = rec.verified_by || "";
        if (ocrEditorNotes) ocrEditorNotes.value = rec.verification_notes || "";
        if (ocrReviewStatus) ocrReviewStatus.textContent = "";
        if (ocrEditorPane) {
            ocrEditorPane.style.display = "block";
            ocrEditorText.focus();
        }
    }

    async function populateOcrDocDropdown() {
        const select = document.getElementById("ocr-run-filename");
        if (!select) return;
        try {
            const res = await fetch("/api/status");
            if (!res.ok) return;
            const data = await res.json();
            const docs = (data.knowledge_base && data.knowledge_base.documents) || [];
            select.innerHTML = "";
            docs.forEach(d => {
                const opt = document.createElement("option");
                opt.value = d.filename;
                opt.textContent = `${d.clean_title || d.filename} (${d.filename})`;
                select.appendChild(opt);
            });
        } catch (err) {
            console.error("Lỗi nạp danh sách tài liệu OCR:", err);
        }
    }

    const ocrRunForm = document.getElementById("ocr-run-page-form");
    const ocrRunStatusMsg = document.getElementById("ocr-run-status-msg");
    if (ocrRunForm) {
        ocrRunForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const filename = document.getElementById("ocr-run-filename").value;
            const pageNum = parseInt(document.getElementById("ocr-run-page").value || "1", 10);
            const btnRun = document.getElementById("btn-run-ocr");
            if (btnRun) btnRun.disabled = true;
            if (ocrRunStatusMsg) ocrRunStatusMsg.textContent = "⏳ Đang chạy OCR bằng qwen2.5vl:3b...";
            try {
                const res = await fetch("/api/ocr/run-page", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        filename: filename,
                        page_num: pageNum,
                        backend: "ollama_vision",
                    }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || "Lỗi chạy OCR");
                if (ocrRunStatusMsg) ocrRunStatusMsg.textContent = "✅ Chạy OCR hoàn tất!";
                loadOcrReviews();
                if (data.record) openOcrEditor(data.record);
            } catch (err) {
                if (ocrRunStatusMsg) ocrRunStatusMsg.textContent = `❌ ${err.message}`;
            } finally {
                if (btnRun) btnRun.disabled = false;
            }
        });
    }

    async function saveOcrReview() {
        if (!currentOcrKey || !ocrEditorText || !ocrEditorReviewer) return;
        const text = ocrEditorText.value.trim();
        const reviewer = ocrEditorReviewer.value.trim();
        const notes = (ocrEditorNotes ? ocrEditorNotes.value : "").trim();
        if (!text) {
            if (ocrReviewStatus) {
                ocrReviewStatus.textContent = "❌ Văn bản OCR không được để trống.";
                ocrReviewStatus.style.color = "red";
            }
            return;
        }
        if (!reviewer) {
            if (ocrReviewStatus) {
                ocrReviewStatus.textContent = "❌ Vui lòng nhập tên người thẩm định.";
                ocrReviewStatus.style.color = "red";
            }
            return;
        }
        if (ocrReviewStatus) {
            ocrReviewStatus.textContent = "Đang lưu xác minh...";
            ocrReviewStatus.style.color = "var(--text-muted)";
        }
        btnSaveOcrReview.disabled = true;
        try {
            const res = await fetch("/api/ocr/review", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    cache_key: currentOcrKey,
                    corrected_text: text,
                    reviewer_name: reviewer,
                    notes: notes,
                })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Lỗi lưu xác minh");
            if (ocrReviewStatus) {
                const indexed = data.index_update && data.index_update.status === "success";
                ocrReviewStatus.textContent = indexed ? "✅ Đã lưu bản duyệt và cập nhật tra cứu." : `⚠️ Đã lưu bản duyệt; chưa cập nhật tra cứu: ${(data.index_update && data.index_update.error) || "chưa tìm thấy tài liệu trong chỉ mục"}`;
                ocrReviewStatus.style.color = indexed ? "green" : "var(--text-muted)";
            }
            setTimeout(() => {
                loadOcrReviews();
            }, 1000);
        } catch (e) {
            if (ocrReviewStatus) {
                ocrReviewStatus.textContent = `❌ ${e.message}`;
                ocrReviewStatus.style.color = "red";
            }
        } finally {
            btnSaveOcrReview.disabled = false;
        }
    }
});
