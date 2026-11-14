/* Bố cục giao diện: thu gọn / mở thanh bên, ngăn kéo trên điện thoại,
   và ẩn màn hình chào khi cuộc trò chuyện đã bắt đầu.
   File này chỉ xử lý giao diện, không đụng tới logic hỏi đáp. */
(function () {
    "use strict";
    const body = document.body;
    const spatialActive =
        Boolean(window.ARYMSpatialLayout) ||
        document.documentElement?.getAttribute("data-spatial-layout") === "bottom-dock";

    if (spatialActive) {
        // Bottom-dock Liquid Glass layout delegates all drawer state & events to spatial-layout.js.
        body?.classList.remove("sidebar-collapsed");
    } else {
        const mobileQuery = window.matchMedia("(max-width: 900px)");
        const STORE_KEY = "sidebar_collapsed";
        const sidebar = document.getElementById("sidebar");

        function isMobile() { return mobileQuery.matches; }

        function readCollapsed() {
            try { return localStorage.getItem(STORE_KEY) === "1"; } catch (e) { return false; }
        }
        function saveCollapsed(value) {
            try { localStorage.setItem(STORE_KEY, value ? "1" : "0"); } catch (e) { /* bỏ qua */ }
        }

        function closeSidebar() {
            body.classList.remove("sidebar-open");
            sidebar?.classList.remove("drawer-open", "open");
            if (!isMobile()) {
                body.classList.add("sidebar-collapsed");
                saveCollapsed(true);
            }
        }
        function openSidebar() {
            sidebar?.classList.add("drawer-open", "open");
            sidebar?.classList.remove("collapsed");
            if (isMobile()) {
                body.classList.add("sidebar-open");
            } else {
                body.classList.remove("sidebar-collapsed");
                saveCollapsed(false);
            }
        }

        if (readCollapsed() && !isMobile()) body.classList.add("sidebar-collapsed");

        document.getElementById("btn-collapse-sidebar")?.addEventListener("click", closeSidebar);
        document.getElementById("btn-open-sidebar")?.addEventListener("click", openSidebar);
        document.getElementById("sidebar-backdrop")?.addEventListener("click", () => {
            body.classList.remove("sidebar-open");
            sidebar?.classList.remove("drawer-open", "open");
        });
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape") {
                body.classList.remove("sidebar-open");
                sidebar?.classList.remove("drawer-open", "open");
            }
        });

        // Trên điện thoại: chọn chế độ / cuộc trò chuyện / chat mới xong thì tự đóng ngăn kéo.
        document.getElementById("sidebar")?.addEventListener("click", (e) => {
            if (!isMobile()) return;
            if (e.target.closest(".mode-button, #btn-new-chat-sidebar, .session-info, .history-open, #btn-open-timetable")) {
                body.classList.remove("sidebar-open");
                sidebar?.classList.remove("drawer-open", "open");
            }
        });
        mobileQuery.addEventListener?.("change", () => {
            body.classList.remove("sidebar-open");
            sidebar?.classList.remove("drawer-open", "open");
        });
    }

    // Ẩn màn hình chào khi đã có tin nhắn thật trong khung chat.
    const container = document.getElementById("messages-container");
    if (container) {
        const sync = () => {
            const hasChat = !!container.querySelector(":scope > .message:not(.welcome-card)");
            container.classList.toggle("has-chat", hasChat);
        };
        new MutationObserver(sync).observe(container, { childList: true });
        sync();
    }
})();
