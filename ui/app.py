"""Trang giao diện mẫu (Placeholder Page) cho Trợ lý Học tập.

Hỗ trợ giao diện Streamlit với khung trò chuyện, ô nhập liệu (input box),
hiển thị tin nhắn và trích dẫn nguồn (citations).
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional
import requests

try:
    import streamlit as st
except ImportError:
    st = None


BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


def check_backend_health() -> bool:
    """Kiểm tra trạng thái sẵn sàng của backend FastAPI."""
    try:
        res = requests.get(f"{BACKEND_URL}/api/health", timeout=2)
        return res.status_code == 200
    except Exception:
        return False


def call_chat_api(
    query: str,
    session_id: Optional[str] = None,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Gọi API backend /api/chat để xử lý câu hỏi của người dùng.

    Nếu backend không phản hồi, tự động trả về phản hồi mô phỏng (fallback).
    """
    payload = {
        "query": query,
        "session_id": session_id,
        "chat_history": chat_history or [],
        "answer_mode": "standard",
        "save_history": True,
    }

    try:
        response = requests.post(
            f"{BACKEND_URL}/api/chat",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 409:
            return {
                "answer": "⚠️ Hệ thống đang bận xử lý yêu cầu trước đó. Vui lòng thử lại sau vài giây.",
                "citations": [],
            }
        else:
            return {
                "answer": f"❌ Lỗi từ máy chủ ({response.status_code}): {response.text}",
                "citations": [],
            }
    except requests.exceptions.RequestException:
        # Chế độ mô phỏng khi chưa bật backend
        return {
            "answer": (
                f"*(Chế độ Placeholder - Backend chưa kết nối)*\n\n"
                f"Tôi đã nhận được câu hỏi: **\"{query}\"**.\n\n"
                "Khi kết nối với hệ thống RAG thực tế, câu trả lời sẽ được tổng hợp "
                "từ giáo trình, tài liệu môn học và đính kèm số trang trích dẫn chính xác."
            ),
            "citations": [
                {
                    "id": "S1",
                    "book_title": "Giáo trình Mạng máy tính",
                    "filename": "mang_may_tinh.pdf",
                    "page_num": 42,
                    "snippet": "Mô hình OSI gồm 7 tầng: Vật lý, Liên kết dữ liệu, Mạng, Giao vận, Phiên, Trình diễn, Ứng dụng.",
                    "score": 0.95,
                    "pdf_url": "/api/documents/1/pdf#page=42",
                }
            ],
            "session_id": session_id or "demo_session",
        }


def render_citations(citations: List[Dict[str, Any]]) -> None:
    """Hiển thị các trích dẫn nguồn bên dưới câu trả lời."""
    if not citations:
        return

    with st.expander(f"📚 Đối chiếu với tài liệu gốc ({len(citations)} nguồn)", expanded=True):
        for cit in citations:
            source_id = cit.get("id", "S")
            title = cit.get("book_title") or cit.get("filename") or "Tài liệu tham khảo"
            page = cit.get("page_num", "?")
            snippet = cit.get("snippet", "").strip()
            score = cit.get("score")

            score_text = f" · Độ khớp: {score:.0%}" if isinstance(score, (int, float)) else ""
            st.markdown(f"**[{source_id}] {title} · Trang {page}**{score_text}")

            if snippet:
                st.caption(f'> "{snippet}"')

            pdf_url = cit.get("pdf_url")
            if pdf_url:
                st.markdown(f"[📖 Mở tài liệu gốc PDF]({BACKEND_URL}{pdf_url})")


def main() -> None:
    """Hàm dựng giao diện chính của Streamlit Placeholder."""
    if st is None:
        print("Vui lòng cài đặt streamlit: pip install streamlit")
        sys.exit(1)

    st.set_page_config(
        page_title="Trợ lý Học tập - Team 06",
        page_icon="🎓",
        layout="wide",
    )

    # 1. Sidebar quản lý phiên & trạng thái
    with st.sidebar:
        st.title("🎓 Trợ lý Học tập")
        st.caption("Dự án Trợ lý Học tập Team 06")

        is_online = check_backend_health()
        if is_online:
            st.success("🟢 Backend: Đã kết nối")
        else:
            st.warning("🟡 Backend: Ngoại tuyến (Đang dùng mô phỏng)")

        st.divider()
        if st.button("➕ Cuộc trò chuyện mới", use_container_width=True):
            st.session_state.messages = []
            st.session_state.session_id = None
            st.rerun()

        st.subheader("Chế độ trả lời")
        answer_mode = st.selectbox(
            "Chọn chế độ:",
            ["standard (Tiêu chuẩn)", "compare (So sánh khái niệm)", "summary (Tóm tắt cốt lõi)"],
        )

        st.divider()
        st.info("💡 **Gợi ý câu hỏi:**\n- Mô hình OSI gồm những tầng nào?\n- Sự khác biệt giữa TCP và UDP?\n- Quy định thi kết thúc học phần?")

    # 2. Khởi tạo trạng thái phiên làm việc (Session state)
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": "Xin chào! Tôi là Trợ lý Học tập của Team 06. Bạn có thắc mắc gì về bài học hoặc tài liệu hôm nay không?",
                "citations": [],
            }
        ]
    if "session_id" not in st.session_state:
        st.session_state.session_id = None

    # 3. Khu vực hiển thị tin nhắn hội thoại
    st.header("💬 Phòng Hỏi đáp Học tập")
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("citations"):
                render_citations(msg["citations"])

    # 4. Ô nhập liệu (Input Box) ở dưới cùng
    user_prompt = st.chat_input("Nhập câu hỏi học tập của bạn tại đây...")
    if user_prompt:
        # Hiển thị tin nhắn người dùng ngay lập tức
        st.session_state.messages.append({"role": "user", "content": user_prompt})
        with st.chat_message("user"):
            st.markdown(user_prompt)

        # Xử lý và nhận câu trả lời từ backend
        with st.chat_message("assistant"):
            with st.spinner("Đang tra cứu tài liệu và suy luận câu trả lời..."):
                history_for_api = [
                    {"role": m["role"], "content": m["content"]}
                    for m in st.session_state.messages[:-1]
                ]
                response_data = call_chat_api(
                    query=user_prompt,
                    session_id=st.session_state.session_id,
                    chat_history=history_for_api,
                )

                answer_text = response_data.get("answer", "Không nhận được câu trả lời.")
                citations = response_data.get("citations", [])
                st.session_state.session_id = response_data.get("session_id", st.session_state.session_id)

                st.markdown(answer_text)
                if citations:
                    render_citations(citations)

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": answer_text,
                    "citations": citations,
                })


if __name__ == "__main__":
    if "streamlit" in sys.modules:
        main()
    else:
        print("Trang giao diện đã sẵn sàng. Chạy lệnh: streamlit run ui/app.py")
