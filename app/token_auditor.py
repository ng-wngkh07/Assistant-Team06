"""Exact dataset token auditor using base model tokenizer and chat template.

Replaces heuristic character/word counts with exact BPE tokenization.
Fails closed if tokenizer or schema is unavailable.
Rejects malformed JSON, empty lines, missing assistant targets, and sequence length violations.
"""

import os
import sys
import json
import math
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from app.config import BASE_DIR, MLX_MODEL_DIR

logger = logging.getLogger(__name__)


def _run_exact_audit_local(
    dataset_dir: Path,
    model_dir: Path,
    max_seq_length: int = 1024,
) -> Dict[str, Any]:
    """Execute exact audit locally using transformers.AutoTokenizer."""
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(f"transformers không khả dụng trong môi trường hiện tại: {exc}")

    if not model_dir.exists():
        raise FileNotFoundError(f"Thư mục mô hình không tồn tại: {model_dir}")

    try:
        tokenizer = AutoTokenizer.from_pretrained(
            str(model_dir),
            trust_remote_code=False,
            local_files_only=True,
        )
    except Exception as exc:
        raise RuntimeError(f"Không thể khởi tạo AutoTokenizer từ {model_dir}: {exc}")

    train_file = dataset_dir / "train.jsonl"
    valid_file = dataset_dir / "valid.jsonl"
    if not train_file.exists() or not valid_file.exists():
        raise FileNotFoundError(f"train.jsonl hoặc valid.jsonl không tồn tại trong {dataset_dir}")

    violations = []
    total_samples = 0
    valid_samples = 0
    total_tokens = 0
    max_observed_tokens = 0
    min_observed_tokens = 999999
    max_target_tokens = 0

    file_samples = {"train.jsonl": 0, "valid.jsonl": 0}

    for file_path in [train_file, valid_file]:
        raw_content = file_path.read_text(encoding="utf-8")
        lines = raw_content.splitlines()

        for line_no, line in enumerate(lines, 1):
            line_str = line.strip()
            if not line_str:
                violations.append({
                    "file": file_path.name,
                    "line": line_no,
                    "code": "EMPTY_LINE",
                    "error": "Dòng dữ liệu trống không được phép trong tập huấn luyện.",
                })
                continue

            total_samples += 1

            try:
                row = json.loads(line_str)
            except Exception as e:
                violations.append({
                    "file": file_path.name,
                    "line": line_no,
                    "code": "MALFORMED_JSON",
                    "error": f"JSON không hợp lệ: {e}",
                })
                continue

            if not isinstance(row, dict):
                violations.append({
                    "file": file_path.name,
                    "line": line_no,
                    "code": "INVALID_TYPE",
                    "error": f"Dòng dữ liệu phải là dict, nhận được {type(row).__name__}",
                })
                continue

            # Schema handling
            messages = []
            assistant_content = ""

            if "messages" in row:
                raw_msgs = row["messages"]
                if not isinstance(raw_msgs, list) or len(raw_msgs) == 0:
                    violations.append({
                        "file": file_path.name,
                        "line": line_no,
                        "code": "EMPTY_MESSAGES",
                        "error": "Trường 'messages' rỗng hoặc không phải list.",
                    })
                    continue

                # In masked-prompt training, the sequence MUST end with an assistant turn
                if raw_msgs[-1].get("role") != "assistant":
                    violations.append({
                        "file": file_path.name,
                        "line": line_no,
                        "code": "FINAL_MESSAGE_NOT_ASSISTANT",
                        "error": "Mẫu dữ liệu cho masked-prompt phải kết thúc bằng tin nhắn của assistant.",
                    })
                    continue

                msg_valid = True
                for idx, m in enumerate(raw_msgs):
                    if not isinstance(m, dict) or "role" not in m or "content" not in m:
                        violations.append({
                            "file": file_path.name,
                            "line": line_no,
                            "code": "INVALID_MESSAGE_ITEM",
                            "error": f"Mục tin nhắn thứ {idx} thiếu 'role' hoặc 'content'.",
                        })
                        msg_valid = False
                        break
                    if not isinstance(m.get("content"), str):
                        violations.append({
                            "file": file_path.name,
                            "line": line_no,
                            "code": "NON_STRING_CONTENT",
                            "error": f"Nội dung tin nhắn thứ {idx} phải là chuỗi ký tự (str), không ép kiểu từ {type(m.get('content')).__name__}.",
                        })
                        msg_valid = False
                        break
                    content_str = m.get("content", "").strip()
                    if not content_str:
                        violations.append({
                            "file": file_path.name,
                            "line": line_no,
                            "code": "EMPTY_CONTENT",
                            "error": f"Tin nhắn vai trò '{m.get('role')}' có nội dung rỗng.",
                        })
                        msg_valid = False
                        break
                    if m.get("role") == "assistant":
                        assistant_content = content_str

                if not msg_valid:
                    continue

                if not assistant_content:
                    violations.append({
                        "file": file_path.name,
                        "line": line_no,
                        "code": "MISSING_ASSISTANT_TARGET",
                        "error": "Thiếu mục tiêu phản hồi của assistant để tính loss huấn luyện.",
                    })
                    continue

                messages = raw_msgs

            elif "prompt" in row and "completion" in row:
                if not isinstance(row["prompt"], str) or not isinstance(row["completion"], str):
                    violations.append({
                        "file": file_path.name,
                        "line": line_no,
                        "code": "NON_STRING_TARGET",
                        "error": "Trường 'prompt' và 'completion' phải là chuỗi ký tự (str), không ép kiểu từ None/non-str.",
                    })
                    continue
                prompt_str = row["prompt"].strip()
                comp_str = row["completion"].strip()
                if not prompt_str or not comp_str:
                    violations.append({
                        "file": file_path.name,
                        "line": line_no,
                        "code": "EMPTY_PROMPT_COMPLETION",
                        "error": "Trường 'prompt' hoặc 'completion' rỗng.",
                    })
                    continue
                assistant_content = comp_str
                messages = [
                    {"role": "user", "content": prompt_str},
                    {"role": "assistant", "content": comp_str},
                ]
            else:
                violations.append({
                    "file": file_path.name,
                    "line": line_no,
                    "code": "UNSUPPORTED_SCHEMA",
                    "error": "Không tìm thấy trường 'messages' hoặc cặp ('prompt', 'completion').",
                })
                continue

            # Exact chat template application
            try:
                encoded = tokenizer.apply_chat_template(messages, tokenize=True)
                if hasattr(encoded, "input_ids"):
                    input_ids = encoded.input_ids
                elif hasattr(encoded, "__getitem__") and "input_ids" in encoded:
                    input_ids = encoded["input_ids"]
                elif isinstance(encoded, (list, tuple)):
                    input_ids = encoded
                else:
                    input_ids = list(encoded)
                sample_token_count = len(input_ids)
            except Exception as e:
                violations.append({
                    "file": file_path.name,
                    "line": line_no,
                    "code": "CHAT_TEMPLATE_ERROR",
                    "error": f"Lỗi áp dụng chat template: {e}",
                })
                continue

            # Target token count
            target_ids = tokenizer.encode(assistant_content, add_special_tokens=False)
            target_token_count = len(target_ids)
            if target_token_count == 0:
                violations.append({
                    "file": file_path.name,
                    "line": line_no,
                    "code": "ZERO_TARGET_TOKENS",
                    "error": "Nội dung assistant encode thành 0 token.",
                })
                continue

            valid_samples += 1
            file_samples[file_path.name] += 1
            total_tokens += sample_token_count
            if sample_token_count > max_observed_tokens:
                max_observed_tokens = sample_token_count
            if sample_token_count < min_observed_tokens:
                min_observed_tokens = sample_token_count
            if target_token_count > max_target_tokens:
                max_target_tokens = target_token_count

            if sample_token_count > max_seq_length:
                violations.append({
                    "file": file_path.name,
                    "line": line_no,
                    "code": "MAX_SEQ_LENGTH_EXCEEDED",
                    "tokens": sample_token_count,
                    "limit": max_seq_length,
                    "error": f"Độ dài mẫu ({sample_token_count} tokens) vượt trần an toàn ({max_seq_length}).",
                })

    if file_samples["train.jsonl"] == 0:
        violations.append({
            "file": "train.jsonl",
            "line": 0,
            "code": "EMPTY_TRAINING_SPLIT",
            "error": "Tập dữ liệu huấn luyện train.jsonl không chứa mẫu hợp lệ nào.",
        })
    if file_samples["valid.jsonl"] == 0:
        violations.append({
            "file": "valid.jsonl",
            "line": 0,
            "code": "EMPTY_VALIDATION_SPLIT",
            "error": "Tập dữ liệu kiểm định valid.jsonl không chứa mẫu hợp lệ nào.",
        })

    avg_tokens = (total_tokens / valid_samples) if valid_samples > 0 else 0.0

    return {
        "status": "SUCCESS",
        "tokenizer": "exact_base_chat_template",
        "total_samples": total_samples,
        "valid_samples": valid_samples,
        "total_tokens": total_tokens,
        "avg_tokens": round(avg_tokens, 1),
        "max_observed_tokens": max_observed_tokens,
        "min_observed_tokens": min_observed_tokens if min_observed_tokens < 999999 else 0,
        "max_target_tokens": max_target_tokens,
        "max_seq_length": max_seq_length,
        "has_violations": len(violations) > 0,
        "violations_count": len(violations),
        "violations": violations[:20],
    }


def audit_dataset_exact(
    dataset_dir: Path,
    model_dir: Optional[Path] = None,
    max_seq_length: int = 1024,
) -> Dict[str, Any]:
    """Audit dataset tokens using exact tokenizer, delegating to .train-venv if needed.

    Fail-closed: returns error report with has_violations=True if tokenizer or schema is unavailable.
    """
    model_dir = model_dir or MLX_MODEL_DIR

    # 1. Attempt local execution if transformers is available
    try:
        import transformers
        return _run_exact_audit_local(dataset_dir, model_dir, max_seq_length)
    except (ImportError, ModuleNotFoundError):
        pass

    # 2. Delegate to .train-venv where transformers and mlx-lm are installed
    train_py = BASE_DIR / ".train-venv" / "bin" / "python"
    if not train_py.exists():
        return {
            "status": "FAIL_CLOSED",
            "error": "Không tìm thấy môi trường .train-venv chứa transformers để chạy token audit chính xác.",
            "has_violations": True,
            "violations_count": 1,
            "violations": [{"code": "ENVIRONMENT_MISSING", "error": "Thiếu .train-venv"}],
        }

    cmd = [
        str(train_py),
        "-m", "app.token_auditor",
        "--dataset", str(dataset_dir),
        "--model", str(model_dir),
        "--max-seq-length", str(max_seq_length),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=60,
        )
        if proc.returncode != 0:
            return {
                "status": "FAIL_CLOSED",
                "error": f"Lỗi thực thi token audit qua .train-venv (code {proc.returncode}): {proc.stderr[:300]}",
                "has_violations": True,
                "violations_count": 1,
                "violations": [{"code": "SUBPROCESS_ERROR", "error": proc.stderr[:300]}],
            }
        return json.loads(proc.stdout.strip())
    except Exception as exc:
        return {
            "status": "FAIL_CLOSED",
            "error": f"Ngoại lệ khi gọi token auditor qua .train-venv: {exc}",
            "has_violations": True,
            "violations_count": 1,
            "violations": [{"code": "AUDIT_EXCEPTION", "error": str(exc)}],
        }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Exact Dataset Token Auditor")
    parser.add_argument("--dataset", type=Path, required=True, help="Path to dataset directory")
    parser.add_argument("--model", type=Path, default=MLX_MODEL_DIR, help="Path to base model directory")
    parser.add_argument("--max-seq-length", type=int, default=1024, help="Max sequence length limit")
    args = parser.parse_args()

    try:
        report = _run_exact_audit_local(args.dataset, args.model, args.max_seq_length)
        print(json.dumps(report, ensure_ascii=False))
        sys.exit(0)
    except Exception as e:
        err_res = {
            "status": "FAIL_CLOSED",
            "error": str(e),
            "has_violations": True,
            "violations_count": 1,
            "violations": [{"code": "CLI_ERROR", "error": str(e)}],
        }
        print(json.dumps(err_res, ensure_ascii=False))
        sys.exit(1)


def audit_context_truncation(
    dataset_path: Path,
    max_seq_length: int = 1024,
    model_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """Audit dataset jsonl files for prompt + target token lengths using exact tokenizer.

    Fails closed if records are malformed JSON/schema, if valid.jsonl is missing,
    or if target tokens are truncated beyond threshold.
    """
    res = audit_dataset_exact(dataset_path, model_dir, max_seq_length)

    train_file = dataset_path / "train.jsonl"
    valid_file = dataset_path / "valid.jsonl"
    files_ok = train_file.exists() and valid_file.exists()

    violations = res.get("violations", [])
    train_malformed = sum(
        1 for v in violations
        if v.get("file") == "train.jsonl" and v.get("code") in ("MALFORMED_JSON", "INVALID_TYPE", "EMPTY_MESSAGES", "INVALID_MESSAGE_ITEM", "EMPTY_LINE", "UNSUPPORTED_SCHEMA")
    )
    valid_malformed = sum(
        1 for v in violations
        if v.get("file") == "valid.jsonl" and v.get("code") in ("MALFORMED_JSON", "INVALID_TYPE", "EMPTY_MESSAGES", "INVALID_MESSAGE_ITEM", "EMPTY_LINE", "UNSUPPORTED_SCHEMA")
    )

    res["acceptable"] = bool(files_ok and (not res.get("has_violations", False)) and res.get("status") == "SUCCESS")
    res["train"] = {"malformed_samples": train_malformed, "total_samples": res.get("valid_samples", 0)}
    res["valid"] = {"malformed_samples": valid_malformed, "total_samples": res.get("valid_samples", 0)}
    return res
