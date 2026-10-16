"""Trang giao diện mẫu (Placeholder / Adapter Page) cho Trợ lý Học tập.

Hỗ trợ giao diện Streamlit với khung trò chuyện, ô nhập liệu (input box),
hiển thị tin nhắn và trích dẫn nguồn thực tế từ backend FastAPI.
Giao diện chính của hệ thống là Web UI tại static/index.html.
"""

import json
import os
import re
import sys
import uuid
from typing import Any, Dict, List, Optional
import requests

try:
    import streamlit as st
except ImportError:
    st = None


BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
SAFE_PDF_REGEX = re.compile(r"^/api/documents/([1-9]\d*)/pdf#page=([1-9]\d*)$")


def check_backend_health() -> bool:
    """Kiểm tra trạng thái sẵn sàng của backend FastAPI."""
    try:
        res = requests.get(f"{BACKEND_URL}/api/health", timeout=2)
        return res.status_code == 200
    except Exception:
        return False


def _sanitize_display_text(val: Any) -> str:
    """Chuyển đổi giá trị sang văn bản thuần, giữ nguyên văn bản gốc."""
    if val is None:
        return ""
    return str(val)


def _prepare_history_for_api(messages: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    """Chuẩn bị lịch sử trò chuyện gửi lên backend: tối đa 32 tin nhắn, <= 4000 ký tự/tin nhắn, tổng <= 32000 bytes."""
    if not messages or not isinstance(messages, list):
        return []
    valid_msgs = [
        {"role": m["role"], "content": str(m.get("content", ""))[:4000]}
        for m in messages
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]
    selected: List[Dict[str, str]] = []
    total_bytes = 0
    for m in reversed(valid_msgs[-32:]):
        b = len(m["content"].encode("utf-8"))
        if total_bytes + b <= 32000:
            selected.append(m)
            total_bytes += b
        else:
            break
    selected.reverse()
    return selected


def call_chat_api(
    query: str,
    session_id: Optional[str] = None,
    chat_history: Optional[List[Dict[str, str]]] = None,
    answer_mode: str = "quick",
    processing_level: str = "auto",
    request_id: Optional[str] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Gọi API backend /api/chat theo phương thức đồng bộ đúng schema.

    Không sử dụng mock hay trích dẫn giả khi lỗi kết nối.
    Trả về cấu trúc an toàn không rò rỉ chi tiết nhạy cảm từ backend.
    """
    valid_modes = {"quick", "steps", "compare", "summary", "step_by_step", "comparison", "normal"}
    if answer_mode not in valid_modes:
        answer_mode = "quick"

    valid_levels = {"auto", "standard", "careful"}
    if processing_level not in valid_levels:
        processing_level = "auto"

    if not request_id:
        request_id = str(uuid.uuid4())

    payload = {
        "query": query,
        "session_id": session_id,
        "chat_history": _prepare_history_for_api(chat_history),
        "answer_mode": answer_mode,
        "processing_level": processing_level,
        "request_id": request_id,
        "save_history": True,
    }

    try:
        response = requests.post(
            f"{BACKEND_URL}/api/chat",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        return {
            "ok": False,
            "error": "Hết thời gian chờ phản hồi từ máy chủ backend (Timeout).",
            "citations": [],
            "session_id": session_id,
        }
    except requests.exceptions.RequestException:
        return {
            "ok": False,
            "error": "Không thể kết nối đến máy chủ backend. Vui lòng kiểm tra dịch vụ.",
            "citations": [],
            "session_id": session_id,
        }

    status = response.status_code
    if status == 200:
        try:
            data = response.json()
        except Exception:
            return {
                "ok": False,
                "error": "Phản hồi từ máy chủ không đúng định dạng JSON hợp lệ.",
                "citations": [],
                "session_id": session_id,
            }

        # Kiểm tra tính toàn vẹn của hợp đồng dữ liệu
        if not isinstance(data, dict):
            return {
                "ok": False,
                "error": "Dữ liệu phản hồi từ máy chủ không hợp lệ (không phải đối tượng JSON).",
                "citations": [],
                "session_id": session_id,
            }

        answer = data.get("answer")
        citations = data.get("citations")
        if (
            not isinstance(answer, str)
            or not answer.strip()
            or not isinstance(citations, list)
            or any(not isinstance(c, dict) for c in citations)
        ):
            return {
                "ok": False,
                "error": "Cấu trúc dữ liệu phản hồi từ máy chủ không đúng chuẩn hợp đồng API.",
                "citations": [],
                "session_id": session_id,
            }

        # Không xem các lượt xử lý bị ngắt hoặc lỗi kỹ thuật là thành công
        if (
            data.get("is_truncated")
            or data.get("error")
            or data.get("response_kind") in {"generation_failed", "technical_error"}
        ):
            return {
                "ok": False,
                "error": "Lượt xử lý chưa hoàn tất hoặc gặp lỗi từ mô hình.",
                "citations": [],
                "session_id": session_id,
            }

        data["ok"] = True
        return data

    elif status == 409:
        return {
            "ok": False,
            "error": "⚠️ Hệ thống đang bận xử lý một yêu cầu khác. Vui lòng thử lại sau vài giây.",
            "citations": [],
            "session_id": session_id,
        }
    elif status == 422:
        return {
            "ok": False,
            "error": "Dữ liệu yêu cầu không hợp lệ theo quy chuẩn bảo mật và kiểm định đầu vào.",
            "citations": [],
            "session_id": session_id,
        }
    elif status in (500, 502, 503, 504):
        return {
            "ok": False,
            "error": "Máy chủ đang quá tải hoặc gặp sự cố tài nguyên tính toán. Vui lòng thử lại.",
            "citations": [],
            "session_id": session_id,
        }
    else:
        return {
            "ok": False,
            "error": f"Máy chủ trả về mã trạng thái không mong muốn (HTTP {status}).",
            "citations": [],
            "session_id": session_id,
        }


def render_citations(citations: Any) -> None:
    """Hiển thị các trích dẫn nguồn thực tế từ backend bên dưới câu trả lời."""
    if not citations or not isinstance(citations, list):
        return

    valid_citations = [c for c in citations if isinstance(c, dict)]
    if not valid_citations:
        return

    with st.expander(f"📚 Đối chiếu với tài liệu gốc ({len(valid_citations)} nguồn)", expanded=True):
        for cit in valid_citations:
            source_id = str(cit.get("id") or "S")
            title_raw = cit.get("book_title") or cit.get("filename") or "Tài liệu tham khảo"
            title = str(title_raw)
            page = cit.get("page_num")
            page_str = str(page) if page is not None else "?"

            score = cit.get("score")
            score_text = ""
            if isinstance(score, (int, float)):
                # Không gọi retrieval score là confidence hoặc phần trăm xác suất
                score_text = f" · Điểm truy xuất: {score:.3f}"

            # Hiển thị title/source_id nguyên văn bằng st.text (plain text), không regex sửa nội dung
            st.text(f"[{source_id}] {title} · Trang {page_str}{score_text}")

            page_quality = cit.get("page_quality")
            if isinstance(page_quality, dict):
                needs_review = bool(page_quality.get("needs_review") or page_quality.get("status") == "needs_review")
                if needs_review:
                    warnings = page_quality.get("warnings") or page_quality.get("quality_warnings")
                    warn_text = ""
                    if isinstance(warnings, list) and warnings:
                        warn_text = "; ".join(str(w) for w in warnings if w is not None)
                    elif isinstance(warnings, str) and warnings.strip():
                        warn_text = warnings.strip()
                    st.warning("⚠️ Trang nguồn cần lưu ý kiểm tra chất lượng OCR")
                    if warn_text:
                        st.text(warn_text)

            snippet = cit.get("snippet")
            if snippet is not None and str(snippet):
                st.text(str(snippet))

            pdf_url = cit.get("pdf_url")
            if isinstance(pdf_url, str):
                m = SAFE_PDF_REGEX.match(pdf_url.strip())
                if m:
                    clean_url = pdf_url.strip()
                    doc_id, pdf_page = m.groups()
                    full_url = f"{BACKEND_URL.rstrip('/')}{clean_url}"
                    st.markdown(f"[📖 Mở trang {pdf_page} (PDF)]({full_url})")


def main() -> None:
    """Hàm dựng giao diện chính của Streamlit Placeholder/Adapter."""
    if st is None:
        print("Vui lòng cài đặt streamlit: pip install streamlit")
        sys.exit(1)

    st.set_page_config(
        page_title="Trợ lý Học tập - Team 06",
        page_icon="🎓",
        layout="wide",
    )

    # 1. Khởi tạo trạng thái phiên làm việc (Session state)
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
    if "pending_request" not in st.session_state:
        st.session_state.pending_request = None
    if "last_error" not in st.session_state:
        st.session_state.last_error = None

    # 2. Sidebar quản lý phiên, chế độ và trạng thái
    with st.sidebar:
        st.title("🎓 Trợ lý Học tập")
        st.caption("Dự án Trợ lý Học tập Team 06 (UI Adapter)")

        is_online = check_backend_health()
        if is_online:
            st.success("🟢 Backend: Đã kết nối")
        else:
            st.warning("🟡 Backend: Ngoại tuyến (Chưa kết nối dịch vụ)")

        st.divider()
        if st.button("➕ Cuộc trò chuyện mới", use_container_width=True):
            st.session_state.messages = [
                {
                    "role": "assistant",
                    "content": "Xin chào! Tôi là Trợ lý Học tập của Team 06. Bạn có thắc mắc gì về bài học hoặc tài liệu hôm nay không?",
                    "citations": [],
                }
            ]
            st.session_state.session_id = None
            st.session_state.pending_request = None
            st.session_state.last_error = None
            st.rerun()

        st.subheader("Chế độ trả lời")
        mode_map = {
            "quick": "quick (Trả lời nhanh, cốt lõi)",
            "steps": "steps (Giải thích từng bước)",
            "compare": "compare (So sánh khái niệm)",
        }
        selected_mode = st.selectbox(
            "Chọn chế độ trả lời:",
            options=list(mode_map.keys()),
            format_func=lambda k: mode_map[k],
            index=0,
        )

        st.subheader("Mức xử lý")
        level_map = {
            "auto": "auto (Tự động điều phối)",
            "standard": "standard (Thông thường)",
            "careful": "careful (Kiểm tra kỹ đối chiếu nguồn)",
        }
        selected_level = st.selectbox(
            "Chọn mức xử lý:",
            options=list(level_map.keys()),
            format_func=lambda k: level_map[k],
            index=0,
        )

        st.divider()
        st.info("💡 **Gợi ý câu hỏi:**\n- Mô hình OSI gồm những tầng nào?\n- Sự khác biệt giữa TCP và UDP?\n- Quy định thi kết thúc học phần?")

    # 3. Khu vực hiển thị tin nhắn hội thoại
    st.header("💬 Phòng Hỏi đáp Học tập")
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("citations"):
                render_citations(msg["citations"])

    # 4. Khu vực xử lý lỗi và nút Thử lại (Retry)
    if st.session_state.last_error:
        st.error(f"❌ {st.session_state.last_error.get('message', 'Đã xảy ra lỗi khi gửi yêu cầu.')}")
        col_retry, col_cancel = st.columns([1, 1])
        with col_retry:
            if st.button("🔄 Thử lại yêu cầu", use_container_width=True):
                pending = st.session_state.pending_request
                if pending:
                    with st.spinner("Đang thử lại với máy chủ backend..."):
                        response_data = call_chat_api(
                            query=pending["query"],
                            session_id=pending.get("session_id"),
                            chat_history=pending.get("chat_history"),
                            answer_mode=pending.get("answer_mode", "quick"),
                            processing_level=pending.get("processing_level", "auto"),
                            request_id=pending.get("request_id"),
                        )
                        if response_data.get("ok"):
                            st.session_state.session_id = response_data.get("session_id", st.session_state.session_id)
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": response_data.get("answer", ""),
                                "citations": response_data.get("citations", []),
                            })
                            st.session_state.pending_request = None
                            st.session_state.last_error = None
                            st.rerun()
                        else:
                            st.session_state.last_error = {
                                "message": response_data.get("error", "Thử lại thất bại.")
                            }
                            st.rerun()
        with col_cancel:
            if st.button("✖ Hủy yêu cầu", use_container_width=True):
                pending = st.session_state.pending_request
                if (
                    pending
                    and st.session_state.messages
                    and st.session_state.messages[-1].get("role") == "user"
                    and st.session_state.messages[-1].get("content") == pending.get("query")
                ):
                    st.session_state.messages.pop()
                st.session_state.pending_request = None
                st.session_state.last_error = None
                st.rerun()

        st.caption(
            "Lưu ý: Nút 'Hủy yêu cầu' chỉ bỏ lượt chờ trên giao diện và loại bỏ câu hỏi chưa có câu trả lời khỏi ngữ cảnh, "
            "không đảm bảo dừng xử lý trên máy chủ hoặc xóa lịch sử backend. Sau khi timeout, server có thể vẫn hoàn tất xử lý; "
            "sử dụng 'Thử lại yêu cầu' với cùng ID sẽ giúp lấy lại kết quả mà không tạo lượt trùng lặp."
        )

    # 5. Ô nhập liệu (Input Box) ở dưới cùng
    has_pending = st.session_state.pending_request is not None
    if has_pending:
        st.warning("⚠️ Đang có một lượt hỏi chưa hoàn tất hoặc gặp sự cố kết nối. Vui lòng bấm 'Thử lại' hoặc 'Hủy yêu cầu' trước khi gửi câu hỏi mới.")

    user_prompt = st.chat_input("Nhập câu hỏi học tập của bạn tại đây...", disabled=has_pending)
    if user_prompt and not has_pending:
        req_id = str(uuid.uuid4())
        history_for_api = _prepare_history_for_api(st.session_state.messages)

        # Đánh dấu pending để chống gửi trùng hoặc gửi lượt mới khi chưa xong
        st.session_state.pending_request = {
            "request_id": req_id,
            "query": user_prompt,
            "chat_history": history_for_api,
            "answer_mode": selected_mode,
            "processing_level": selected_level,
            "session_id": st.session_state.session_id,
        }

        # Hiển thị tin nhắn người dùng
        st.session_state.messages.append({"role": "user", "content": user_prompt})

        with st.chat_message("user"):
            st.markdown(user_prompt)

        with st.chat_message("assistant"):
            with st.spinner("Đang tra cứu tài liệu và xử lý câu trả lời..."):
                response_data = call_chat_api(
                    query=user_prompt,
                    session_id=st.session_state.session_id,
                    chat_history=history_for_api,
                    answer_mode=selected_mode,
                    processing_level=selected_level,
                    request_id=req_id,
                )

                if response_data.get("ok"):
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
                    st.session_state.pending_request = None
                    st.session_state.last_error = None
                    st.rerun()
                else:
                    st.session_state.last_error = {
                        "message": response_data.get("error", "Lỗi phản hồi từ máy chủ.")
                    }
                    st.rerun()


if __name__ == "__main__":
    if "streamlit" in sys.modules:
        main()
    else:
        print("Trang giao diện adapter đã sẵn sàng. Chạy lệnh: streamlit run ui/app.py")
