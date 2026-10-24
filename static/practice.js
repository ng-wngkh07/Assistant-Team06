document.addEventListener("DOMContentLoaded", () => {
    const progressKey = "study-agent-review-v1";
    const practiceButton = document.getElementById("btn-practice-current");
    const clearScopeButton = document.getElementById("btn-clear-practice-scope");
    const scopeLabel = document.getElementById("practice-scope-label");
    const reviewCount = document.getElementById("review-count");
    const practiceDialog = document.getElementById("practice-dialog");
    const practiceBody = document.getElementById("practice-body");
    const practiceSubtitle = document.getElementById("practice-dialog-subtitle");
    const reviewList = document.getElementById("review-list");
    const examplesForm = document.getElementById("practice-examples-form");
    const examplesDocument = document.getElementById("practice-examples-document");
    const examplesPageFrom = document.getElementById("practice-examples-page-from");
    const examplesPageTo = document.getElementById("practice-examples-page-to");
    const examplesTopic = document.getElementById("practice-examples-topic");
    const examplesStatus = document.getElementById("practice-examples-status");
    const examplesResults = document.getElementById("practice-examples-results");
    const learningTopic = document.getElementById("learning-topic");
    const learningCount = document.getElementById("learning-count");
    const learningDifficulty = document.getElementById("learning-difficulty");
    const btnLearningGenerate = document.getElementById("btn-learning-generate");
    const learningTopicList = document.getElementById("learning-topic-list");
    const learningGenerateStatus = document.getElementById("learning-generate-status");

    const reviewTopic = document.getElementById("review-topic");
    const btnReviewGenerate = document.getElementById("btn-review-generate");
    const reviewGenerateStatus = document.getElementById("review-generate-status");

    const exerciseTopic = document.getElementById("exercise-topic");
    const exerciseSolveMissing = document.getElementById("exercise-solve-missing");
    const btnLearningExercises = document.getElementById("btn-learning-exercises");
    const learningExerciseStatus = document.getElementById("learning-exercise-status");
    const learningExerciseResults = document.getElementById("learning-exercise-results");
    let selectedScope = null;
    let practiceSession = null;
    let questionIndex = 0;
    let quizResults = [];
    let requestPending = false;
    let reviewItems = readProgress();
    let documentsById = new Map();
    let examplesRevision = 0;

    document.addEventListener("app-source-selected", event => {
        const detail = event.detail || {};
        const docId = Number(detail.doc_id);
        const pageNum = Number(detail.page_num);
        const chunkId = Number(detail.chunk_id);
        selectedScope = Number.isInteger(docId) && docId > 0 && Number.isInteger(pageNum) && pageNum > 0
            ? { doc_id: docId, page_num: pageNum, source_chunk_id: Number.isInteger(chunkId) && chunkId > 0 ? chunkId : null,
                title: String(detail.title || "Tài liệu") }
            : null;
        updateSelectedScope();
    });
    document.addEventListener("app-source-cleared", clearSelectedScope);
    document.addEventListener("app-show-qa", clearSelectedScope);
    practiceButton?.addEventListener("click", startPractice);
    document.addEventListener("app-start-practice", startPractice);
    clearScopeButton?.addEventListener("click", () => document.dispatchEvent(new CustomEvent("app-source-cleared")));
    btnLearningGenerate?.addEventListener("click", startLearningGenerate);
    btnReviewGenerate?.addEventListener("click", startReviewGenerate);
    btnLearningExercises?.addEventListener("click", searchLearningExercises);

    function updateSelectedScope() {
        if (practiceButton) practiceButton.disabled = !selectedScope;
        if (clearScopeButton) clearScopeButton.disabled = !selectedScope;
        if (scopeLabel) scopeLabel.textContent = selectedScope
            ? `Nguồn đã chọn: ${selectedScope.title} · trang ${selectedScope.page_num}${selectedScope.source_chunk_id ? " · đoạn cụ thể" : ""}. Bộ câu hỏi giới hạn tối đa 3 câu.`
            : "Tìm và chọn một trích đoạn bên dưới, hoặc chọn một trang từ phần Hỏi đáp.";
    }

    function clearSelectedScope() {
        selectedScope = null;
        updateSelectedScope();
    }

    function readProgress() {
        try {
            const value = JSON.parse(localStorage.getItem(progressKey) || "[]");
            return Array.isArray(value) ? value.filter(item =>
                item && typeof item === "object" &&
                typeof item.id === "string" && typeof item.question === "string" &&
                Array.isArray(item.choices) && item.choices.length === 4 &&
                item.choices.every(choice => choice && ["A", "B", "C", "D"].includes(choice.label) && typeof choice.text === "string") &&
                ["A", "B", "C", "D"].includes(item.correct_answer) &&
                item.source && typeof item.source === "object" &&
                typeof item.source.title === "string" && Number.isFinite(Number(item.source.page_num))
            ) : [];
        } catch (_) {
            return [];
        }
    }

    function persistProgress() {
        updateReviewCount();
        try {
            localStorage.setItem(progressKey, JSON.stringify(reviewItems));
            return true;
        } catch (_) {
            return false;
        }
    }

    function updateReviewCount() {
        if (reviewCount) reviewCount.textContent = String(reviewItems.length);
    }

    async function postJson(url, body) {
        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        let result = {};
        try { result = await response.json(); } catch (_) {}
        if (!response.ok) {
            const detail = typeof result.detail === "string" ? result.detail
                : Array.isArray(result.detail) ? result.detail.map(item => item?.msg).filter(Boolean).join(" ") : "";
            throw new Error(detail || `Yêu cầu thất bại (${response.status}).`);
        }
        return result;
    }

    document.getElementById("btn-close-practice")?.addEventListener("click", () => practiceDialog.close());
    practiceDialog?.addEventListener("click", event => {
        if (event.target === practiceDialog) practiceDialog.close();
    });

    async function startPractice() {
        if (!selectedScope || requestPending) return;
        const scope = { ...selectedScope };
        practiceDialog.showModal();
        practiceSubtitle.textContent = `${scope.title} · trang ${scope.page_num}`;
        practiceSession = null;
        quizResults = [];
        showMessage("Đang tạo tối đa 3 câu từ nguồn đã chọn…");
        requestPending = true;
        practiceButton.disabled = true;
        try {
            const request = {
                source_document_id: scope.doc_id,
                source_page_num: scope.page_num,
                count: 3,
            };
            if (scope.source_chunk_id) request.source_chunk_id = scope.source_chunk_id;
            practiceSession = await postJson("/api/practice/generate", request);
            if (practiceSession) practiceSession.topic = scope.title;
            questionIndex = 0;
            renderQuestion();
        } catch (error) {
            showMessage(error.message, true);
        } finally {
            requestPending = false;
            practiceButton.disabled = !selectedScope;
        }
    }

    function showMessage(message, isError = false) {
        practiceBody.replaceChildren();
        const p = document.createElement("p");
        p.className = isError ? "practice-error" : "text-muted";
        p.setAttribute("role", isError ? "alert" : "status");
        p.textContent = message;
        practiceBody.appendChild(p);
    }

    function renderQuestion() {
        if (!practiceSession || questionIndex >= practiceSession.questions.length) {
            renderCompletion();
            return;
        }
        const question = practiceSession.questions[questionIndex];
        practiceBody.replaceChildren();

        const modeBadge = document.createElement("span");
        modeBadge.className = "practice-mode-badge text-muted";
        modeBadge.style.cssText = "display: inline-block; font-size: 0.78rem; margin-bottom: 6px; padding: 2px 8px; border-radius: 4px; background: rgba(59, 130, 246, 0.08); color: var(--primary, #2563eb);";
        modeBadge.textContent = "🧠 Mức xử lý: Kiểm tra kỹ (đối chiếu 2 bước qua nguồn)";
        practiceBody.appendChild(modeBadge);

        const count = document.createElement("p");
        count.className = "practice-progress text-muted";
        count.textContent = `Câu ${questionIndex + 1}/${practiceSession.questions.length}`;
        const prompt = document.createElement("h3");
        prompt.className = "practice-question";
        prompt.textContent = question.question;
        const source = document.createElement("p");
        source.className = "practice-source-label";
        source.textContent = `Nguồn: ${question.source.title} · trang ${question.source.page_num}`;
        const choices = document.createElement("fieldset");
        choices.className = "practice-choices";
        const legend = document.createElement("legend");
        legend.textContent = "Chọn một đáp án";
        choices.appendChild(legend);
        question.choices.forEach(choice => {
            const label = document.createElement("label");
            label.className = "practice-choice";
            const input = document.createElement("input");
            input.type = "radio";
            input.name = "practice-answer";
            input.value = choice.label;
            const text = document.createElement("span");
            text.textContent = `${choice.label}. ${choice.text}`;
            label.append(input, text);
            choices.appendChild(label);
        });
        const actions = document.createElement("div");
        actions.className = "study-dialog-actions";
        const submit = makeButton("Trả lời", "btn btn-primary", async () => {
            const selected = practiceBody.querySelector('input[name="practice-answer"]:checked');
            if (selected) await submitAnswer(question, selected.value);
        });
        submit.disabled = true;
        choices.addEventListener("change", () => { submit.disabled = false; });
        const unsure = makeButton("Chưa chắc", "btn btn-outline", () => submitAnswer(question, null));
        actions.append(unsure, submit);
        practiceBody.append(count, prompt, source, makeSourceCard(question.source, false), choices, actions);
    }

    async function submitAnswer(question, answer) {
        if (requestPending || !practiceSession) return;
        requestPending = true;
        practiceBody.querySelectorAll("button, input").forEach(element => { element.disabled = true; });
        try {
            const feedback = await postJson("/api/practice/answer", {
                practice_id: practiceSession.practice_id,
                question_id: question.id,
                answer,
            });
            quizResults.push(feedback.is_correct);
            const entry = makeReviewEntry(question, answer, feedback, practiceSession.practice_id);
            let autoSaved = false;
            if (!feedback.is_correct) autoSaved = saveReviewEntry(entry);
            renderFeedback(question, answer, feedback, entry, autoSaved);
        } catch (error) {
            showMessage(error.message, true);
        } finally {
            requestPending = false;
        }
    }

    function makeReviewEntry(question, submitted, feedback, practiceId) {
        const submittedChoice = question.choices.find(choice => choice.label === submitted);
        const topic = question.topic || (practiceSession ? practiceSession.topic : "") || (selectedScope ? selectedScope.title : "");
        const entry = {
            id: `${practiceId}:${question.id}`,
            question: question.question,
            choices: question.choices,
            submitted_answer: submitted,
            submitted_answer_text: submittedChoice ? submittedChoice.text : "Chưa chắc",
            correct_answer: feedback.correct_answer,
            correct_choice: feedback.correct_choice,
            explanation: feedback.explanation,
            source: feedback.source,
            saved_at: new Date().toISOString(),
            reviewed_at: null,
            last_review_correct: null,
        };
        if (topic) entry.topic = topic;
        return entry;
    }

    function saveReviewEntry(entry) {
        if (!reviewItems.some(item => item.id === entry.id)) reviewItems.unshift(entry);
        reviewItems = reviewItems.slice(0, 100);
        const saved = persistProgress();
        renderReviewList();
        return saved && reviewItems.some(item => item.id === entry.id);
    }

    function renderFeedback(question, submitted, feedback, entry, autoSaved) {
        practiceBody.replaceChildren();
        const result = document.createElement("p");
        result.className = feedback.is_correct ? "practice-result correct" : "practice-result incorrect";
        result.textContent = feedback.is_correct
            ? "Chính xác."
            : submitted === null ? "Đã ghi nhận: bạn chưa chắc câu này." : "Chưa chính xác.";
        const prompt = document.createElement("h3");
        prompt.className = "practice-question";
        prompt.textContent = question.question;
        const answer = document.createElement("p");
        answer.className = "practice-answer-key";
        answer.textContent = `Đáp án: ${feedback.correct_answer}. ${feedback.correct_choice}`;
        const explanation = document.createElement("p");
        explanation.className = "practice-explanation";
        explanation.textContent = feedback.explanation;
        const sourceCard = makeSourceCard(feedback.source, true);
        const actions = document.createElement("div");
        actions.className = "study-dialog-actions";
        if (autoSaved) {
            const saved = document.createElement("span");
            saved.className = "practice-saved-status";
            saved.textContent = "Đã lưu vào phần Ôn tập trên trình duyệt này.";
            actions.appendChild(saved);
        } else if (!reviewItems.some(item => item.id === entry.id)) {
            actions.appendChild(makeButton("Lưu câu này để ôn lại", "btn btn-outline", () => {
                if (saveReviewEntry(entry)) {
                    actions.querySelector("button")?.remove();
                    const saved = document.createElement("span");
                    saved.className = "practice-saved-status";
                    saved.textContent = "Đã lưu vào phần Ôn tập trên trình duyệt này.";
                    actions.prepend(saved);
                } else {
                    const warning = document.createElement("span");
                    warning.className = "practice-error";
                    warning.textContent = "Không lưu được. Hãy kiểm tra quyền lưu trong trình duyệt.";
                    actions.prepend(warning);
                }
            }));
        }
        const next = makeButton(
            questionIndex + 1 < practiceSession.questions.length ? "Câu tiếp" : "Xem kết quả",
            "btn btn-primary",
            () => { questionIndex += 1; renderQuestion(); }
        );
        actions.appendChild(next);
        practiceBody.append(result, prompt, answer, explanation, sourceCard, actions);
    }

    function makeSourceCard(source, includeEvidence) {
        const card = document.createElement("div");
        card.className = "practice-evidence";
        const heading = document.createElement("strong");
        heading.textContent = `Nguồn: ${source.title} · trang ${source.page_num}`;
        card.appendChild(heading);
        if (includeEvidence && source.snippet) {
            const quote = document.createElement("blockquote");
            quote.textContent = source.snippet;
            card.appendChild(quote);
        }
        if (source.pdf_url) {
            const link = safeLocalLink(source.pdf_url, "Mở trang nguồn trong PDF");
            if (link) card.appendChild(link);
        }
        if (source.doc_id) {
            card.appendChild(makeButton("Mở nguồn trong Hỏi đáp", "btn btn-outline btn-sm", () => {
                const detail = { doc_id: Number(source.doc_id), page_num: Number(source.page_num), chunk_id: Number(source.chunk_id), title: source.title };
                document.dispatchEvent(new CustomEvent("app-source-selected", { detail }));
                practiceDialog.close();
                document.dispatchEvent(new CustomEvent("app-set-mode", { detail: "qa" }));
            }));
        }
        return card;
    }

    function safeLocalLink(path, label) {
        try {
            const url = new URL(path, window.location.origin);
            if (url.origin !== window.location.origin) return null;
            const link = document.createElement("a");
            link.href = url.href;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = label;
            return link;
        } catch (_) {
            return null;
        }
    }

    function renderCompletion() {
        practiceBody.replaceChildren();
        const correct = quizResults.filter(Boolean).length;
        const total = quizResults.length;
        const heading = document.createElement("h3");
        heading.textContent = "Hoàn thành lượt luyện tập";
        const summary = document.createElement("p");
        summary.textContent = `Bạn trả lời đúng ${correct}/${total} câu.`;
        const review = makeButton("Mở tab Ôn tập", "btn btn-outline", () => {
            practiceDialog.close();
            openReview();
        });
        const close = makeButton("Đóng", "btn btn-primary", () => practiceDialog.close());
        const actions = document.createElement("div");
        actions.className = "study-dialog-actions";
        actions.append(review, close);
        practiceBody.append(heading, summary, actions);
    }

    document.getElementById("btn-clear-review")?.addEventListener("click", () => {
        if (!window.confirm("Xóa toàn bộ danh sách và tiến độ ôn lại trên trình duyệt này?")) return;
        reviewItems = [];
        let cleared = false;
        try {
            localStorage.removeItem(progressKey);
            cleared = true;
        } catch (_) {}
        updateReviewCount();
        renderReviewList();
        if (!cleared) {
            const warning = document.createElement("p");
            warning.className = "practice-error";
            warning.textContent = "Không xóa được dữ liệu lưu trong trình duyệt. Hãy kiểm tra quyền lưu của trang.";
            reviewList.prepend(warning);
        }
    });

    function openReview() {
        document.dispatchEvent(new CustomEvent("app-open-tool-tab", { detail: "review" }));
        renderReviewList();
    }

    function renderReviewList() {
        reviewList.replaceChildren();
        if (!reviewItems.length) {
            const empty = document.createElement("p");
            empty.className = "text-muted";
            empty.textContent = "Chưa có câu nào cần ôn tập. Câu trả lời sai hoặc “Chưa chắc” được lưu tự động.";
            reviewList.appendChild(empty);
            return;
        }
        reviewItems.forEach(item => reviewList.appendChild(renderReviewItem(item)));
    }

    function renderReviewItem(item) {
        const card = document.createElement("article");
        card.className = "review-item";
        const heading = document.createElement("h3");
        heading.textContent = item.question;
        card.appendChild(heading);
        if (item.self_check) {
            const choices = document.createElement("fieldset");
            choices.className = "practice-choices";
            const legend = document.createElement("legend");
            legend.textContent = "Tự trả lời trước khi xem lời giải";
            choices.appendChild(legend);
            item.choices.forEach(choice => {
                const label = document.createElement("label");
                label.className = "practice-choice";
                const input = document.createElement("input");
                input.type = "radio";
                input.name = `review-${item.id}`;
                input.value = choice.label;
                const text = document.createElement("span");
                text.textContent = `${choice.label}. ${choice.text}`;
                label.append(input, text);
                choices.appendChild(label);
            });
            const check = makeButton("Kiểm tra", "btn btn-primary btn-sm", () => {
                const selected = choices.querySelector("input:checked");
                if (!selected) return;
                item.last_review_correct = selected.value === item.correct_answer;
                item.reviewed_at = new Date().toISOString();
                item.self_check = false;
                persistProgress();
                renderReviewList();
            });
            const cancel = makeButton("Để sau", "btn btn-outline btn-sm", () => {
                item.self_check = false;
                renderReviewList();
            });
            card.append(choices, check, cancel);
            return card;
        }

        const status = document.createElement("p");
        status.className = item.reviewed_at ? "review-status reviewed" : "review-status";
        status.textContent = item.reviewed_at
            ? item.last_review_correct ? "Đã ôn lại · trả lời đúng" : "Đã ôn lại · cần xem lại lần nữa"
            : `Câu trả lời trước: ${item.submitted_answer
                ? `${item.submitted_answer}. ${item.submitted_answer_text || item.choices.find(choice => choice.label === item.submitted_answer)?.text || ""}`
                : "Chưa chắc"}`;
        const answer = document.createElement("p");
        answer.textContent = `Đáp án: ${item.correct_answer}. ${item.correct_choice}`;
        const explanation = document.createElement("p");
        explanation.textContent = item.explanation;
        card.append(status, answer, explanation, makeSourceCard(item.source, true));
        const actions = document.createElement("div");
        actions.className = "study-dialog-actions";
        actions.append(
            makeButton("Tự kiểm tra lại", "btn btn-primary btn-sm", () => {
                item.self_check = true;
                renderReviewList();
            }),
            makeButton("Xóa mục", "btn btn-outline btn-sm", () => {
                reviewItems = reviewItems.filter(entry => entry.id !== item.id);
                persistProgress();
                renderReviewList();
            })
        );
        card.appendChild(actions);
        return card;
    }

    async function loadExampleDocuments() {
        if (!examplesDocument) return;
        try {
            const response = await fetch("/api/documents");
            const data = await response.json();
            if (!response.ok) throw new Error("Không tải được danh sách tài liệu.");
            documentsById = new Map((data.documents || []).map(item => [String(item.id), item]));
            examplesDocument.replaceChildren();
            const placeholder = document.createElement("option");
            placeholder.value = "";
            placeholder.textContent = documentsById.size ? "Chọn tài liệu" : "Chưa có tài liệu đã lập chỉ mục";
            examplesDocument.appendChild(placeholder);
            for (const item of documentsById.values()) {
                const option = document.createElement("option");
                option.value = String(item.id);
                option.textContent = item.clean_title || item.filename;
                examplesDocument.appendChild(option);
            }
        } catch (error) {
            examplesDocument.replaceChildren();
            const option = document.createElement("option");
            option.value = "";
            option.textContent = "Không tải được tài liệu";
            examplesDocument.appendChild(option);
            examplesStatus.textContent = error.message;
        }
    }

    examplesDocument?.addEventListener("change", () => {
        const selected = documentsById.get(examplesDocument.value);
        const totalPages = Number(selected?.total_pages) || 1;
        examplesPageFrom.max = String(totalPages);
        examplesPageTo.max = String(totalPages);
        examplesPageTo.value = String(totalPages);
        examplesResults.replaceChildren();
        examplesStatus.textContent = selected
            ? `Tài liệu có ${totalPages} trang. Chọn khoảng trang để tìm nội dung được nhận diện là bài tập.`
            : "Chọn tài liệu và khoảng trang để tìm bài tập có sẵn.";
    });
    examplesForm?.addEventListener("submit", async event => {
        event.preventDefault();
        const docId = Number(examplesDocument.value);
        const from = Number(examplesPageFrom.value);
        const to = Number(examplesPageTo.value);
        const doc = documentsById.get(examplesDocument.value);
        const epoch = ++examplesRevision;
        examplesResults.replaceChildren();
        if (!doc || !Number.isInteger(from) || !Number.isInteger(to) || from < 1 || to < from || to > Number(doc.total_pages)) {
            examplesStatus.textContent = "Chọn tài liệu và khoảng trang hợp lệ.";
            return;
        }
        const params = new URLSearchParams({ document_id: String(docId), page_from: String(from), page_to: String(to), limit: "20" });
        if (examplesTopic.value.trim()) params.set("topic", examplesTopic.value.trim());
        examplesStatus.textContent = "Đang tìm bài tập có sẵn trong tài liệu…";
        try {
            const response = await fetch(`/api/practice/examples?${params}`);
            const data = await response.json();
            if (epoch !== examplesRevision) return;
            if (!response.ok) throw new Error(data.detail || "Không tìm được bài tập từ tài liệu.");
            const items = data.items || [];
            examplesStatus.textContent = items.length
                ? `Tìm thấy ${items.length} đoạn có dấu hiệu bài tập trong ${doc.clean_title || doc.filename}. Đây là nội dung trích từ nguồn, không phải câu hỏi AI tự sinh.`
                : "Không tìm thấy đoạn có dấu hiệu bài tập trong phạm vi này. Hãy thử thêm trang hoặc bỏ lọc chủ đề.";
            for (const item of items) examplesResults.appendChild(renderExample(item));
        } catch (error) {
            if (epoch === examplesRevision) examplesStatus.textContent = error.message;
        }
    });

    function renderExample(item) {
        const card = document.createElement("article");
        card.className = "document-result source-exercise-card";
        const heading = document.createElement("h3");
        heading.textContent = item.title || item.filename || "Bài tập trích từ tài liệu";
        const label = document.createElement("p");
        label.className = "document-page source-origin-label";
        label.textContent = `Bài tập trích từ nguồn · trang ${item.page_num}`;
        const text = document.createElement("div");
        text.className = "document-passage";
        text.textContent = item.text || "";
        const actions = document.createElement("div");
        actions.className = "document-result-actions";
        if (item.pdf_url) {
            const link = safeLocalLink(item.pdf_url, "Đối chiếu trang gốc trong PDF");
            if (link) actions.appendChild(link);
        }
        actions.appendChild(makeButton("Chọn trang này trong Hỏi đáp", "btn btn-outline btn-sm", () => {
            document.dispatchEvent(new CustomEvent("app-source-selected", { detail: {
                doc_id: Number(item.doc_id), page_num: Number(item.page_num), chunk_id: Number(item.chunk_id), title: item.title,
            }}));
            document.dispatchEvent(new CustomEvent("app-set-mode", { detail: "qa" }));
        }));
        card.append(heading, label, text);
        if (item.needs_source_check) {
            const warning = document.createElement("p");
            warning.className = "source-extraction-warning";
            warning.textContent = item.source_warning || "OCR hoặc công thức có thể chưa rõ; hãy đối chiếu trang gốc.";
            card.appendChild(warning);
        }
        card.appendChild(actions);
        return card;
    }

    function makeButton(text, className, onClick) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = className;
        button.textContent = text;
        button.addEventListener("click", onClick);
        return button;
    }

    async function loadLearningTopics() {
        if (!learningTopicList) return;
        try {
            const response = await fetch("/api/learning/topics");
            if (!response.ok) return;
            const data = await response.json();
            const topics = data.topics || [];
            learningTopicList.replaceChildren();
            for (const t of topics) {
                const opt = document.createElement("option");
                opt.value = t.label || t.id;
                learningTopicList.appendChild(opt);
            }
        } catch (_) {}
    }

    async function startLearningGenerate() {
        const topic = (learningTopic?.value || "").trim();
        if (!topic) {
            if (learningGenerateStatus) learningGenerateStatus.textContent = "Vui lòng nhập hoặc chọn chủ đề câu hỏi.";
            return;
        }
        const rawCount = parseInt(learningCount?.value, 10);
        const count = Number.isInteger(rawCount) && rawCount >= 1 && rawCount <= 3 ? rawCount : 1;
        const difficulty = ["easy", "medium", "hard"].includes(learningDifficulty?.value) ? learningDifficulty.value : "medium";
        if (requestPending) return;
        requestPending = true;
        if (btnLearningGenerate) btnLearningGenerate.disabled = true;
        practiceDialog.showModal();
        practiceSubtitle.textContent = `Chủ đề: ${topic} · ${difficulty === "easy" ? "Dễ" : difficulty === "hard" ? "Khó" : "Trung bình"}`;
        practiceSession = null;
        quizResults = [];
        showMessage(`Đang tạo ${count} câu hỏi từ tài liệu về «${topic}»…`);
        try {
            const body = {
                topic,
                count,
                difficulty,
                purpose: "practice",
                weak_topics: [],
            };
            practiceSession = await postJson("/api/learning/generate", body);
            if (practiceSession) practiceSession.topic = topic;
            questionIndex = 0;
            renderQuestion();
        } catch (error) {
            showMessage(error.message, true);
        } finally {
            requestPending = false;
            if (btnLearningGenerate) btnLearningGenerate.disabled = false;
        }
    }

    async function startReviewGenerate() {
        let topic = (reviewTopic?.value || "").trim();
        if (!topic) {
            const savedWithTopic = reviewItems.find(item => item.topic && typeof item.topic === "string" && item.topic.trim());
            if (savedWithTopic) topic = savedWithTopic.topic.trim();
        }
        if (!topic) {
            if (reviewGenerateStatus) reviewGenerateStatus.textContent = "Vui lòng nhập chủ đề cần ôn tập.";
            return;
        }
        const weakTopics = Array.from(new Set(
            reviewItems
                .map(item => item.topic)
                .filter(t => typeof t === "string" && t.trim().length > 0)
        )).slice(0, 20);

        if (requestPending) return;
        requestPending = true;
        if (btnReviewGenerate) btnReviewGenerate.disabled = true;
        practiceDialog.showModal();
        practiceSubtitle.textContent = `Ôn tập: ${topic}`;
        practiceSession = null;
        quizResults = [];
        showMessage(`Đang tạo câu hỏi ôn tập về «${topic}»…`);
        try {
            const body = {
                topic,
                count: 1,
                difficulty: "medium",
                purpose: "review",
                weak_topics: weakTopics,
            };
            practiceSession = await postJson("/api/learning/generate", body);
            if (practiceSession) practiceSession.topic = topic;
            questionIndex = 0;
            renderQuestion();
        } catch (error) {
            showMessage(error.message, true);
        } finally {
            requestPending = false;
            if (btnReviewGenerate) btnReviewGenerate.disabled = false;
        }
    }

    async function searchLearningExercises() {
        const topic = (exerciseTopic?.value || "").trim();
        if (!topic) {
            if (learningExerciseStatus) learningExerciseStatus.textContent = "Vui lòng nhập chủ đề bài tập.";
            return;
        }
        const solveMissing = !!(exerciseSolveMissing && exerciseSolveMissing.checked);
        if (requestPending) return;
        requestPending = true;
        if (btnLearningExercises) btnLearningExercises.disabled = true;
        if (learningExerciseStatus) learningExerciseStatus.textContent = `Đang tìm bài tập cho chủ đề «${topic}»…`;
        learningExerciseResults?.replaceChildren();
        try {
            const body = {
                topic,
                limit: 20,
                solve_missing: solveMissing,
            };
            const data = await postJson("/api/learning/exercises", body);
            const items = data.items || [];
            if (!items.length) {
                if (learningExerciseStatus) learningExerciseStatus.textContent = `Không tìm thấy bài tập nào cho chủ đề «${topic}» trong thư viện tài liệu.`;
            } else {
                if (learningExerciseStatus) learningExerciseStatus.textContent = `Tìm thấy ${items.length} bài tập cho chủ đề «${topic}».`;
                for (const item of items) {
                    learningExerciseResults.appendChild(renderLearningExercise(item));
                }
            }
        } catch (error) {
            if (learningExerciseStatus) learningExerciseStatus.textContent = error.message;
        } finally {
            requestPending = false;
            if (btnLearningExercises) btnLearningExercises.disabled = false;
        }
    }

    function cleanThoughts(str) {
        if (!str || typeof str !== "string") return str;
        return str.replace(/<\s*(?:think|analysis|reasoning)\b[^>]*>.*?<\s*\/\s*(?:think|analysis|reasoning)\s*>/gis, "")
                  .replace(/^.*?<\s*\/\s*(?:think|analysis|reasoning)\s*>/gis, "").trim();
    }

    function renderLearningExercise(item) {
        const card = document.createElement("article");
        card.className = "document-result learning-exercise-card";

        const qHeading = document.createElement("h3");
        qHeading.className = "exercise-question-title";
        qHeading.textContent = (cleanThoughts(item.question) || "").replace(/^\s*#{1,6}\s+/gm, "") || "Bài tập";
        card.appendChild(qHeading);

        if (item.source) {
            const isPdf = Boolean(
                (item.source.filename && item.source.filename.toLowerCase().endsWith(".pdf")) ||
                (item.source.pdf_url && typeof item.source.pdf_url === "string" && item.source.pdf_url.toLowerCase().includes(".pdf"))
            );
            const docId = Number(item.source.doc_id);
            const pageNum = Number(item.source.page_num);
            const title = item.source.title || item.source.filename || "Tài liệu";

            const srcInfo = document.createElement("p");
            srcInfo.className = "document-page text-muted";
            if (isPdf) {
                srcInfo.textContent = `Nguồn: ${title} · trang ${pageNum}`;
            } else {
                srcInfo.textContent = `Nguồn: ${title} · đoạn ${pageNum}`;
            }
            card.appendChild(srcInfo);

            if (isPdf && Number.isInteger(docId) && docId > 0 && Number.isInteger(pageNum) && pageNum > 0) {
                const link = document.createElement("a");
                link.href = `/api/documents/${docId}/pdf#page=${pageNum}`;
                link.target = "_blank";
                link.rel = "noopener noreferrer";
                link.textContent = "Mở trang câu hỏi trong PDF";
                card.appendChild(link);
            } else if (!isPdf && item.source.snippet) {
                const snippetEl = document.createElement("div");
                snippetEl.className = "document-passage";
                snippetEl.textContent = item.source.snippet;
                card.appendChild(snippetEl);
            }
        }

        const answerSection = document.createElement("div");
        answerSection.className = "exercise-answer-section";

        if (item.answer) {
            const cleanedAnswer = cleanThoughts(item.answer);
            let bareAnswer = cleanedAnswer;
            let detailedSolution = cleanThoughts(item.solution) || null;

            const ansMatch = cleanedAnswer.match(/(?:^|\n)\s*(?:đáp án|kết quả|final answer)\s*:\s*([\s\S]+)$/i);
            if (ansMatch) {
                bareAnswer = ansMatch[1].trim();
                const prefix = cleanedAnswer.slice(0, ansMatch.index).trim();
                if (prefix) {
                    detailedSolution = prefix;
                }
            } else if (cleanedAnswer.includes("\n") || cleanedAnswer.length > 80) {
                detailedSolution = cleanedAnswer;
            }

            const toggleContainer = document.createElement("div");
            toggleContainer.className = "exercise-view-toggle";
            if (toggleContainer.style) toggleContainer.style.cssText = "margin: 6px 0; display: flex; gap: 6px;";

            const btnBare = document.createElement("button");
            btnBare.type = "button";
            btnBare.className = "btn btn-outline btn-sm";
            btnBare.textContent = "Chỉ đáp án";

            const btnDetailed = document.createElement("button");
            btnDetailed.type = "button";
            btnDetailed.className = "btn btn-outline btn-sm";
            btnDetailed.textContent = "Giải chi tiết";

            toggleContainer.append(btnBare, btnDetailed);
            answerSection.appendChild(toggleContainer);

            const contentContainer = document.createElement("div");
            contentContainer.className = "exercise-view-content";
            const answerSourceContainer = document.createElement("div");
            answerSourceContainer.className = "exercise-answer-source";
            function showAnswerSource() {
                if (answerSourceContainer.parentNode !== answerSection) {
                    answerSection.appendChild(answerSourceContainer);
                }
            }

            function renderBareView() {
                btnBare.className = "btn btn-outline btn-sm active";
                btnDetailed.className = "btn btn-outline btn-sm";
                contentContainer.replaceChildren();
                showAnswerSource();

                const ansTitle = document.createElement("strong");
                ansTitle.className = "exercise-answer-label";
                if (item.answer_status === "source_document") {
                    ansTitle.textContent = "Đáp án từ tài liệu: ";
                } else if (item.answer_status === "model_verified") {
                    ansTitle.textContent = "Đáp án (Lời giải AI đã qua kiểm tra mô hình - sàng lọc tự động, không bảo đảm tuyệt đối): ";
                } else if (item.answer_status === "unverified") {
                    ansTitle.textContent = "Đáp án (Chưa xác minh): ";
                } else {
                    ansTitle.textContent = "Đáp án: ";
                }
                const ansText = document.createElement("span");
                ansText.className = "exercise-answer-text";
                ansText.textContent = bareAnswer;
                contentContainer.append(ansTitle, ansText);
            }

            function renderDetailedView() {
                btnBare.className = "btn btn-outline btn-sm";
                btnDetailed.className = "btn btn-outline btn-sm active";
                contentContainer.replaceChildren();
                showAnswerSource();

                if (detailedSolution) {
                    const solTitle = document.createElement("strong");
                    solTitle.className = "exercise-solution-label";
                    solTitle.textContent = item.answer_status === "source_document"
                        ? "Lời giải từ tài liệu: "
                        : item.answer_status === "model_verified"
                            ? "Lời giải AI đã qua đối chiếu tự động: "
                            : "Lời giải chưa xác minh: ";
                    const solText = document.createElement("div");
                    solText.className = "exercise-solution-text";
                    if (solText.style) solText.style.whiteSpace = "pre-wrap";
                    solText.textContent = detailedSolution;
                    contentContainer.append(solTitle, solText);
                } else {
                    const notice = document.createElement("p");
                    notice.className = "text-muted exercise-no-detailed-solution";
                    notice.textContent = "Chưa có lời giải chi tiết cho bài tập này.";
                    contentContainer.appendChild(notice);
                }
            }

            btnBare.addEventListener("click", renderBareView);
            btnDetailed.addEventListener("click", renderDetailedView);

            const viewNotice = document.createElement("p");
            viewNotice.textContent = "Chọn cách xem đáp án trước khi mở lời giải.";
            contentContainer.appendChild(viewNotice);
            answerSection.appendChild(contentContainer);

            if (item.answer_source) {
                const isPdfAnswer = Boolean(
                    (item.answer_source.filename && item.answer_source.filename.toLowerCase().endsWith(".pdf")) ||
                    (item.answer_source.pdf_url && typeof item.answer_source.pdf_url === "string" && item.answer_source.pdf_url.toLowerCase().includes(".pdf"))
                );
                const ansDocId = Number(item.answer_source.doc_id);
                const ansPageNum = Number(item.answer_source.page_num);
                const ansTitleText = item.answer_source.title || item.answer_source.filename || "Tài liệu";

                if (isPdfAnswer && Number.isInteger(ansDocId) && ansDocId > 0 && Number.isInteger(ansPageNum) && ansPageNum > 0) {
                    const ansLink = document.createElement("a");
                    ansLink.href = `/api/documents/${ansDocId}/pdf#page=${ansPageNum}`;
                    ansLink.target = "_blank";
                    ansLink.rel = "noopener noreferrer";
                    ansLink.textContent = "Mở trang đáp án trong PDF";
                    answerSourceContainer.appendChild(ansLink);
                } else if (!isPdfAnswer) {
                    const ansTextSource = document.createElement("p");
                    ansTextSource.className = "document-page text-muted";
                    ansTextSource.textContent = `Nguồn đáp án: ${ansTitleText} · đoạn ${ansPageNum}`;
                    answerSourceContainer.appendChild(ansTextSource);
                    if (item.answer_source.snippet) {
                        const ansSnippet = document.createElement("div");
                        ansSnippet.className = "document-passage";
                        ansSnippet.textContent = item.answer_source.snippet;
                        answerSourceContainer.appendChild(ansSnippet);
                    }
                }
            }
        } else if (item.answer_status === "unverified") {
            const unverifiedNotice = document.createElement("p");
            unverifiedNotice.className = "text-warning exercise-unverified-notice";
            unverifiedNotice.textContent = "Lời giải AI chưa được xác minh (không đạt kiểm định với tài liệu).";
            answerSection.appendChild(unverifiedNotice);
        } else {
            const noAns = document.createElement("p");
            noAns.className = "text-muted exercise-no-answer";
            noAns.textContent = "Tài liệu nguồn không kèm đáp án cho bài tập này.";
            answerSection.appendChild(noAns);
        }

        card.appendChild(answerSection);
        return card;
    }

    updateSelectedScope();
    updateReviewCount();
    renderReviewList();
    loadExampleDocuments();
    loadLearningTopics();
});
