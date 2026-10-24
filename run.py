#!/usr/bin/env python3
import sys
import argparse
from pathlib import Path
import uvicorn

from app.config import SERVER_HOST, SERVER_PORT, DEFAULT_CHAT_MODEL, DEFAULT_EMBED_MODEL
from app.indexer import KnowledgeIndexer
from app.searcher import HybridSearcher
from app.rag_agent import PsychologyAgent
from app.ollama_client import OllamaClient
from app.trained_client import TrainedModelClient

def cmd_index(args):
    """Run PDF indexing CLI."""
    indexer = KnowledgeIndexer()
    print("=" * 60)
    print("🚀 BẮT ĐẦU LẬP CHỈ MỤC THƯ VIỆN SÁCH TÂM LÝ HỌC (./src)")
    print(f"   Chế độ ghi đè (force): {args.force}")
    print(f"   Mô hình Embedding: {args.embed_model or 'Chỉ dùng Full-Text (FTS5 BM25)'}")
    print("=" * 60)

    def on_progress(current, total, filename, msg):
        clean_name = filename.replace("\n", " ")[:35]
        print(f"[{current:02d}/{total:02d}] {clean_name:<35} | {msg}")

    report = indexer.index_all(
        force=args.force,
        ocr=args.ocr,
        embed_model=args.embed_model if args.embed_model != "none" else None,
        progress_callback=on_progress,
        force_ocr_files=set(args.force_ocr_file),
    )

    print("\n" + "=" * 60)
    print("📊 BÁO CÁO KẾT QUẢ LẬP CHỈ MỤC:")
    print(f"   • Tổng số file PDF phát hiện: {report['total_files']}")
    print(f"   • Số file đã lập chỉ mục mới: {report['indexed_files']}")
    print(f"   • Số file bỏ qua (đã đồng bộ): {report['skipped_files']}")
    print(f"   • Tổng số trang chữ trích xuất: {report['total_pages_indexed']}")
    print(f"   • Tổng số đoạn (chunks) chỉ mục: {report['total_chunks_created']}")
    print(f"   • Embedding Vector khả dụng: {'Có' if report['embedding_enabled'] else 'Không (Dùng FTS5 BM25)'}")
    
    if report["scanned_files"]:
        print("\n⚠️ CẢNH BÁO: Phát hiện các file PDF scan dạng ảnh (Chưa có OCR):")
        for sf in report["scanned_files"]:
            print(f"   - {sf}")

    if report["error_files"]:
        print("\n❌ CÁC FILE LỖI:")
        for ef in report["error_files"]:
            print(f"   - {ef['filename']}: {ef['error']}")
    print("=" * 60)

def cmd_embed(args):
    """Backfill or generate embeddings for existing chunks."""
    indexer = KnowledgeIndexer()
    print("=" * 60)
    print("🔮 BỔ SUNG VECTOR EMBEDDING CHO CSDL CHỈ MỤC")
    print(f"   Mô hình: {args.embed_model}")
    print(f"   Giới hạn: {args.limit if args.limit > 0 else 'Toàn bộ đoạn chưa có vector'}")
    print("=" * 60)

    if sys.platform == "darwin":
        from app.native_embedding import NativeEmbeddingClient
        if args.embed_model not in {"bge-m3", "bge-m3-mlx-fp16", "bge-m3-mlx", "native-bge"}:
            raise ValueError("macOS chỉ hỗ trợ embedding BGE-M3 qua MLX cục bộ")
        if not NativeEmbeddingClient.available():
            print("❌ Mô hình embedding MLX cục bộ chưa sẵn sàng. Kiểm tra hướng dẫn macOS và trọng số đã cài.")
            return
        resolved_model = "bge-m3-mlx-fp16"
    else:
        resolved_model = indexer.ollama.find_best_embed_model(args.embed_model) or args.embed_model
        test_vec = indexer.ollama.get_embedding("test", model=resolved_model)
        if test_vec is None:
            print(f"❌ Mô hình embedding '{resolved_model}' chưa chạy được trong Ollama. Hãy chạy: ollama pull {resolved_model}")
            return

    def on_progress(cur, total, msg):
        print(f"[{cur:05d}/{total:05d}] {msg}")

    count = indexer.backfill_missing_embeddings(
        embed_model=resolved_model,
        limit=args.limit,
        progress_callback=on_progress
    )
    print("=" * 60)
    print(f"✅ Hoàn thành! Đã bổ sung thành công vector embedding cho {count} đoạn chỉ mục.")
    print("=" * 60)

def cmd_status(args):
    """Show current knowledge base status."""
    indexer = KnowledgeIndexer()
    status = indexer.get_status()
    print("=" * 60)
    print("📚 TRẠNG THÁI HỆ THỐNG HỌC TẬP CỤC BỘ")
    print("=" * 60)
    if sys.platform == "darwin":
        from app.native_embedding import NativeEmbeddingClient
        from app.native_vision import NativeVisionClient
        print(f"• QA MLX {DEFAULT_CHAT_MODEL}: {'🟢 sẵn sàng' if TrainedModelClient.available(DEFAULT_CHAT_MODEL) else '🔴 chưa sẵn sàng'}")
        print(f"• Thị giác MLX: {'🟢 sẵn sàng' if NativeVisionClient.available() else '🔴 chưa sẵn sàng'}")
        print(f"• Embedding MLX BGE-M3: {'🟢 sẵn sàng' if NativeEmbeddingClient.available() else '🔴 chưa sẵn sàng'}")
        print("• Trạng thái model gốc và adapter được báo riêng tại /api/models.")
    else:
        ollama = OllamaClient()
        ollama_ok = ollama.check_health()
        models = ollama.list_models() if ollama_ok else []
        print(f"• Máy chủ Ollama cục bộ: {'🟢 đang chạy' if ollama_ok else '🔴 chưa kết nối'}")
        if models:
            print("• Các mô hình cục bộ: " + ", ".join(m.get("name", "") for m in models))

    print(f"\n• Tổng tài liệu trong CSDL: {status['total_documents']}")
    print(f"• Tài liệu scan chưa OCR: {status['scanned_documents']}")
    print(f"• Tổng số trang đã trích xuất: {status['total_indexed_pages']}")
    print(f"• Tổng số đoạn chỉ mục (FTS5): {status['total_chunks']}")
    print(f"• Số đoạn đã có Vector Embedding: {status.get('embedded_chunks', 0)}")
    
    print("\nChi tiết từng tài liệu:")
    for d in status["documents"]:
        state_icon = "🟢" if d["status"] == "indexed" else ("🟡 Scan" if d["is_scanned"] else "🔴 Lỗi")
        print(f" {state_icon} {d['clean_title'][:40]:<40} | {d['extracted_pages_count']}/{d['total_pages']} trang")
    print("=" * 60)

def cmd_query(args):
    """Run CLI query against indexed books with real-time streaming."""
    agent = PsychologyAgent()
    print(f"\n🔍 Câu hỏi: {args.question}")
    print("Đang tra cứu thư viện...\n")

    print("-" * 60)
    print("💬 CÂU TRẢ LỜI:")
    
    for event in agent.process_query_stream(
        query=args.question,
        model=args.model,
        embed_model=args.embed_model if args.embed_model != "none" else None,
        top_k=args.top_k
    ):
        if event["type"] in ("token", "crisis"):
            sys.stdout.write(event["content"])
            sys.stdout.flush()
        elif event["type"] == "error":
            print(f"\n❌ Lỗi: {event['content']}")

    print("\n" + "-" * 60)

    print("=" * 60)

def cmd_serve(args):
    """Start local web server."""
    print("=" * 60)
    print(f"🚀 KHỞI ĐỘNG MÁY CHỦ WEB NỘI BỘ TẠI http://{args.host}:{args.port}")
    print("   Giao diện tiếng Việt • Chạy 100% Cục bộ & Miễn phí")
    print("=" * 60)
    reload_options = {"reload_dirs": [str(Path(__file__).resolve().parent / "app")]} if args.reload else {}
    uvicorn.run("app.server:app", host=args.host, port=args.port, reload=args.reload, **reload_options)

def cmd_prepare_train(args):
    from app.training_data import prepare
    from app.config import DB_PATH, BASE_DIR
    summary = prepare(DB_PATH, args.output or BASE_DIR / "data" / "training" / "v1", args.pairs_per_book, args.limit_books, args.teacher_model, args.only_file, args.cross_book)
    print(summary)

def cmd_train(args):
    from app.fine_tune import train
    from app.config import BASE_DIR
    model_dir = getattr(args, "model_dir", None)
    train(
        data_dir=args.data or BASE_DIR / "data" / "training" / "v1",
        iterations=args.iters,
        num_layers=args.num_layers,
        adapter_dir=args.adapter_dir,
        model_dir=model_dir,
        learning_rate=args.learning_rate,
        max_seq_length=args.max_seq_length,
    )

def cmd_audit_train(args):
    from app.training_audit import audit
    from app.config import BASE_DIR
    print(audit(args.data or BASE_DIR / "data" / "training" / "v1", args.model))

def main():
    parser = argparse.ArgumentParser(description="Local Academic QA & Study Agent CLI")
    subparsers = parser.add_subparsers(dest="command", help="Lệnh thực hiện")

    # Index command
    p_index = subparsers.add_parser("index", help="Lập chỉ mục hoặc tái lập chỉ mục toàn bộ sách từ ./src")
    p_index.add_argument("--force", action="store_true", help="Bắt buộc lập chỉ mục lại toàn bộ")
    p_index.add_argument("--ocr", action="store_true", help="Dùng Tesseract tại máy cho PDF scan (có thể mất nhiều thời gian)")
    p_index.add_argument("--force-ocr-file", action="append", default=[], help="OCR toàn bộ một PDF có lớp chữ bị lỗi; dùng đúng tên file, có thể lặp lại")
    p_index.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL, help="Mô hình embedding cục bộ (hoặc 'none')")

    # Embed command
    p_embed = subparsers.add_parser("embed", help="Bổ sung vector embedding cho các đoạn chỉ mục chưa có vector")
    p_embed.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL, help="Mô hình embedding (mặc định: bge-m3)")
    p_embed.add_argument("--limit", type=int, default=0, help="Giới hạn số đoạn bổ sung (0: không giới hạn)")

    # Status command
    subparsers.add_parser("status", help="Kiểm tra trạng thái CSDL và Ollama")

    # Query command
    p_query = subparsers.add_parser("query", help="Tra cứu câu hỏi qua terminal")
    p_query.add_argument("question", type=str, help="Nội dung câu hỏi")
    p_query.add_argument("--model", default=DEFAULT_CHAT_MODEL, help="Mô hình QA cục bộ")
    p_query.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL, help="Mô hình embedding")
    p_query.add_argument("--top-k", type=int, default=6, help="Số đoạn trích lấy ra")

    # Serve command
    p_serve = subparsers.add_parser("serve", help="Khởi chạy ứng dụng Web trên localhost")
    p_serve.add_argument("--host", default=SERVER_HOST, help="Địa chỉ host (mặc định: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=SERVER_PORT, help="Cổng cổng (mặc định: 8000)")
    p_serve.add_argument("--reload", action="store_true", help="Tự động reload khi code thay đổi")

    p_prepare = subparsers.add_parser("prepare-train", help="Tạo ví dụ phân tích song ngữ có nguồn từ sách")
    p_prepare.add_argument("--pairs-per-book", type=int, default=1)
    p_prepare.add_argument("--limit-books", type=int, default=0)
    p_prepare.add_argument("--teacher-model", default=DEFAULT_CHAT_MODEL, help="Mô hình giáo viên cục bộ (mặc định theo QA đã chọn)")
    p_prepare.add_argument("--output", type=Path, help="Thư mục dữ liệu mới; mặc định data/training/v1")
    p_prepare.add_argument("--only-file", action="append", default=[])
    p_prepare.add_argument("--cross-book", action="store_true", help="Thêm ví dụ tổng hợp hai sách khác ngôn ngữ")

    p_train = subparsers.add_parser("train", help="Fine-tune LoRA trên Apple Silicon bằng MLX")
    p_train.add_argument("--data", type=Path, help="Thư mục dữ liệu đã duyệt")
    p_train.add_argument("--iters", type=int, default=80)
    p_train.add_argument("--num-layers", type=int, default=4)
    p_train.add_argument("--adapter-dir", type=Path, help="Thư mục adapter; dùng thư mục riêng cho từng thử nghiệm")
    p_train.add_argument("--model-dir", type=Path, default=None, help="Đường dẫn thư mục model MLX base")
    p_train.add_argument("--learning-rate", type=float, default=0.00002)
    p_train.add_argument("--max-seq-length", type=int, default=1024)

    p_audit = subparsers.add_parser("audit-train", help="Lọc ví dụ huấn luyện bằng kiểm tra nguồn cục bộ")
    p_audit.add_argument("--data", type=Path, help="Thư mục dữ liệu cần kiểm tra")
    p_audit.add_argument("--model", default=DEFAULT_CHAT_MODEL, help="Mô hình kiểm tra cục bộ (mặc định theo QA đã chọn)")

    p_plan = subparsers.add_parser("train-plan", help="Print a nonexecuting candidate training draft")
    p_plan.add_argument("--model-dir", type=Path, required=True)
    p_plan.add_argument("--data", type=Path, required=True)
    p_plan.add_argument("--adapter-dir", type=Path, required=True)
    p_plan.add_argument("--profile", default="qwen35-9b-m1-16gb")

    args = parser.parse_args()
    if sys.platform == "win32" and args.command in {"train", "prepare-train", "audit-train"}:
        parser.error("Các lệnh huấn luyện/chọn dataset MLX dành cho máy Mac của người phụ trách. Windows dùng serve/query và kiểm thử tính năng.")
    if args.command == "train-plan":
        import json
        from app.training_profiles import build_training_plan
        try:
            plan = build_training_plan(args.model_dir, args.data, args.adapter_dir, args.profile)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    elif args.command == "index":
        cmd_index(args)
    elif args.command == "embed":
        cmd_embed(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "query":
        cmd_query(args)
    elif args.command == "serve":
        cmd_serve(args)
    elif args.command == "prepare-train":
        cmd_prepare_train(args)
    elif args.command == "train":
        cmd_train(args)
    elif args.command == "audit-train":
        cmd_audit_train(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
