"""Active Registry for MLX LoRA adapters and unadapted base model.

Ensures that only semantically reviewed and accepted adapters can be activated,
preventing legacy ready.json from automatically promoting adapters.
The current product uses the unadapted Qwen3.5 9B base. Historical adapter
records remain auditable and are never automatically applied to that base.
"""

import json
import hashlib
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from app.config import DATA_DIR, MLX_MODEL_DIR, TRAINING_MODEL_REPO, TRAINING_MODEL_REVISION

logger = logging.getLogger(__name__)

REGISTRY_FILE = DATA_DIR / "adapters" / "active_registry.json"

_FILE_HASH_CACHE: Dict[Tuple[str, int, int, int, int, int], str] = {}


def get_file_sha256_cached(path: Path) -> str:
    """Chunked SHA256 computation cached by file identity, size, mtime, and ctime."""
    p = Path(path).resolve()
    stat = p.stat()
    key = (str(p), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if key in _FILE_HASH_CACHE:
        return _FILE_HASH_CACHE[key]

    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    digest = h.hexdigest()
    _FILE_HASH_CACHE[key] = digest
    return digest


def _extract_case_mapping(data: Any) -> Dict[str, dict]:
    """Extract mapping of case_id -> case_dict from list or dict structure."""
    if not isinstance(data, dict):
        return {}
    raw_cases = data.get("cases") or data.get("test_cases") or data.get("case_records") or data.get("reproduction_cases")
    if isinstance(raw_cases, list):
        return {str(c.get("id")): c for c in raw_cases if isinstance(c, dict) and c.get("id")}
    elif isinstance(raw_cases, dict):
        return {str(k): v for k, v in raw_cases.items() if isinstance(v, dict)}
    return {}


def _is_valid_acceptance_dossier(ev_data, ev_dir, adapter_dir=None, base_model_dir=None) -> bool:
    from app.study_acceptance import assess_dossier
    if adapter_dir is None or base_model_dir is None:
        return False
    try:
        assess_dossier(ev_data, adapter_dir, base_model_dir, get_file_sha256_cached)
        return True
    except (ValueError, KeyError, TypeError, OSError, AttributeError):
        return False


def _is_valid_evidence_payload(
    ev_data: Any,
    ev_dir: Optional[Path] = None,
    adapter_dir: Optional[Path] = None,
    base_model_dir: Optional[Path] = None,
) -> bool:
    """Check that evidence is a valid codex-study-acceptance-v1 dossier."""
    if not isinstance(ev_data, dict) or not ev_data:
        return False
    if ev_dir is None:
        ev_dir = Path.cwd()
    if ev_data.get("schema") == "codex-study-acceptance-v1":
        return _is_valid_acceptance_dossier(ev_data, ev_dir, adapter_dir=adapter_dir, base_model_dir=base_model_dir)
    return False


def validate_adapter_acceptance(
    adapter_dir: Path,
    semantic_acceptance: Dict[str, Any],
    base_model_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Single shared validator for adapter promotion and runtime resolution.
    
    Verifies:
    1. Structured semantic acceptance record with Codex review.
    2. Actual adapter weights exist on disk and match bound hash.
    3. Base model weights exist and match bound hash.
    4. Actual evidence file exists on disk, parses as valid paired evaluation payload,
       and gates confirm promotion.
    """
    if not semantic_acceptance or not isinstance(semantic_acceptance, dict):
        raise ValueError("Không thể kích hoạt/promote: Thiếu hồ sơ semantic acceptance có cấu trúc.")

    if not semantic_acceptance.get("semantically_accepted") or not semantic_acceptance.get("reviewed_by_codex"):
        raise ValueError("Chặn promotion: Adapter phải được Codex review và phê duyệt ngữ nghĩa (reviewed_by_codex=True).")

    # 1. Verify adapter weights on disk
    adapter_dir = Path(adapter_dir)
    weights_file = adapter_dir / "adapters.safetensors"
    if not weights_file.exists():
        raise FileNotFoundError(f"Không tìm thấy trọng số adapters.safetensors tại {adapter_dir}")
    actual_adapter_hash = get_file_sha256_cached(weights_file)

    if semantic_acceptance.get("adapter_sha256") and semantic_acceptance["adapter_sha256"] != actual_adapter_hash:
        raise ValueError("Mã băm trọng số adapter không khớp với bằng chứng nghiệm thu.")

    # 2. Base model directory
    base_dir = Path(base_model_dir) if base_model_dir is not None else MLX_MODEL_DIR

    # 3. Evidence validation
    evidence = semantic_acceptance.get("evidence")
    evidence_path = semantic_acceptance.get("evidence_path")

    if isinstance(evidence, str):
        ev_p = Path(evidence)
        if not ev_p.is_file():
            raise ValueError("Bằng chứng không hợp lệ: Chuỗi 'evidence' không phải đường dẫn tệp dossier/bằng chứng hợp lệ trên đĩa.")
        evidence_path = str(ev_p)
    elif isinstance(evidence, dict):
        if not _is_valid_evidence_payload(evidence, ev_dir=adapter_dir, adapter_dir=adapter_dir, base_model_dir=base_dir):
            raise ValueError("Hồ sơ bằng chứng (dict) thiếu dữ liệu so sánh cặp thực tế hoặc chưa đạt gates.")

    if not evidence_path:
        raise ValueError("Chặn promotion: Thiếu đường dẫn tệp bằng chứng thực (evidence_path) trên đĩa.")

    ev_path = Path(evidence_path)
    if not ev_path.is_file():
        raise FileNotFoundError(f"Tệp bằng chứng '{evidence_path}' không tồn tại.")

    try:
        ev_data = json.loads(ev_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ValueError(f"Tệp bằng chứng '{evidence_path}' không phải JSON hợp lệ.")

    if not _is_valid_evidence_payload(ev_data, ev_dir=ev_path.parent, adapter_dir=adapter_dir, base_model_dir=base_dir):
        raise ValueError(f"Tệp bằng chứng '{evidence_path}' thiếu dữ liệu so sánh cặp thực tế hoặc chưa đạt gates.")

    actual_ev_hash = get_file_sha256_cached(ev_path)
    dossier_sha256 = semantic_acceptance.get("dossier_sha256") or semantic_acceptance.get("evaluation_hash")
    if dossier_sha256 and dossier_sha256 != actual_ev_hash:
        raise ValueError("Mã băm tệp bằng chứng không khớp với dossier_sha256 đã đăng ký.")

    protocol_sha256 = semantic_acceptance.get("protocol_sha256") or semantic_acceptance.get("protocol_hash")
    if protocol_sha256:
        if isinstance(ev_data, dict) and "bindings" in ev_data and "protocol" in ev_data["bindings"]:
            bound_p_hash = ev_data["bindings"]["protocol"].get("sha256")
            if bound_p_hash != protocol_sha256:
                raise ValueError("Mã băm protocol trong semantic acceptance không khớp với protocol binding trong bằng chứng.")

    # 3. Verify base model weights on disk
    base_dir = Path(base_model_dir) if base_model_dir is not None else MLX_MODEL_DIR
    base_weights = base_dir / "model.safetensors"
    if not base_weights.exists():
        raise FileNotFoundError(f"Không tìm thấy trọng số base model tại {base_dir}")
    base_sha256 = get_file_sha256_cached(base_weights)
    if semantic_acceptance.get("base_sha256") and semantic_acceptance["base_sha256"] != base_sha256:
        raise ValueError("Mã băm base model không khớp với bằng chứng nghiệm thu.")

    return {
        "adapter_dir": str(adapter_dir),
        "adapter_sha256": actual_adapter_hash,
        "base_sha256": base_sha256,
        "evidence_path": str(ev_path),
        "evidence_sha256": actual_ev_hash,
        "dossier_sha256": dossier_sha256 or actual_ev_hash,
        "protocol_sha256": semantic_acceptance.get("protocol_sha256") or semantic_acceptance.get("protocol_hash"),
        "review_sha256": semantic_acceptance.get("review_sha256") or semantic_acceptance.get("review_hash"),
        "decision_hash": semantic_acceptance.get("decision_hash") or semantic_acceptance.get("approval_hash"),
        "scores": semantic_acceptance.get("scores", {}),
    }


class ActiveAdapterRegistry:
    """Manages active adapter state and semantic promotion gates."""

    def __init__(self, registry_path: Path = REGISTRY_FILE):
        self.registry_path = registry_path
        self._ensure_registry()

    def _ensure_registry(self) -> None:
        if not self.registry_path.exists():
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            initial = {
                "active_adapter_id": None,
                "adapter_dir": None,
                "status": "unadapted_base",
                "base_model": {
                    "repo": TRAINING_MODEL_REPO,
                    "revision": TRAINING_MODEL_REVISION,
                    "path": str(MLX_MODEL_DIR),
                },
                "accepted_adapters": {},
            }
            self.registry_path.write_text(json.dumps(initial, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_registry(self) -> Dict[str, Any]:
        self._ensure_registry()
        try:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Lỗi đọc active_registry.json: {e}")
            return {
                "active_adapter_id": None,
                "adapter_dir": None,
                "status": "unadapted_base",
                "base_model": {
                    "repo": TRAINING_MODEL_REPO,
                    "revision": TRAINING_MODEL_REVISION,
                    "path": str(MLX_MODEL_DIR),
                },
                "accepted_adapters": {},
            }

    def save_registry(self, data: Dict[str, Any]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.registry_path.with_suffix(".tmp")
        temp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(self.registry_path)

    def get_active_adapter_dir(self) -> Optional[Path]:
        """Return Path to active adapter directory ONLY if semantically accepted, else None (unadapted base).
        
        Strict verification via shared validator:
        - Rejects flags-only / unbound stored flags (must have bound hashes & evidence file).
        - Verifies exact weights on disk exist and have not mutated.
        - Verifies evidence file exists and contains valid evaluated case records.
        """
        reg = self.load_registry()
        active_id = reg.get("active_adapter_id")
        if not active_id:
            return None

        accepted = reg.get("accepted_adapters", {}).get(active_id)
        if not accepted or not isinstance(accepted, dict):
            return None

        adapter_dir_str = reg.get("adapter_dir") or accepted.get("adapter_dir")
        if not adapter_dir_str:
            return None

        p = Path(adapter_dir_str)
        try:
            validate_adapter_acceptance(p, accepted, MLX_MODEL_DIR)
            return p
        except Exception as e:
            logger.warning(f"Adapter '{active_id}' không vượt qua thẩm định cấu trúc ({e}); dùng unadapted base model.")
            return None

    def get_active_info(self) -> Dict[str, Any]:
        """Return public status of active model/adapter for health and model endpoints."""
        reg = self.load_registry()
        adapter_path = self.get_active_adapter_dir()
        is_adapted = adapter_path is not None
        active_id = reg.get("active_adapter_id") if is_adapted else None

        return {
            "mode": "adapted" if is_adapted else "unadapted_base",
            "base_model": {
                "repo": TRAINING_MODEL_REPO,
                "revision": TRAINING_MODEL_REVISION,
                "path": str(MLX_MODEL_DIR),
                "is_ready": (MLX_MODEL_DIR / "config.json").exists(),
            },
            "active_adapter": {
                "id": active_id,
                "path": str(adapter_path) if adapter_path else None,
                "semantically_accepted": is_adapted,
            } if is_adapted else None,
        }

    def promote_adapter(
        self,
        trial_id: str,
        adapter_dir: Path,
        semantic_acceptance: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Promote an adapter to active registry ONLY with explicit semantic acceptance.
        
        Validated via shared validator validate_adapter_acceptance.
        """
        validated = validate_adapter_acceptance(adapter_dir, semantic_acceptance, MLX_MODEL_DIR)

        reg = self.load_registry()
        reg["accepted_adapters"][trial_id] = {
            "trial_id": trial_id,
            "adapter_dir": str(adapter_dir),
            "adapter_sha256": validated["adapter_sha256"],
            "base_sha256": validated["base_sha256"],
            "semantically_accepted": True,
            "reviewed_by_codex": True,
            "reviewed_by": semantic_acceptance.get("reviewer", "Codex"),
            "evidence_path": validated["evidence_path"],
            "dossier_sha256": validated["dossier_sha256"],
            "protocol_sha256": validated["protocol_sha256"],
            "review_sha256": validated["review_sha256"],
            "decision_hash": validated["decision_hash"],
            "approval_hash": validated["decision_hash"],
            "scores": semantic_acceptance.get("scores", {}),
            "promoted_at": semantic_acceptance.get("promoted_at"),
        }
        reg["active_adapter_id"] = trial_id
        reg["adapter_dir"] = str(adapter_dir)
        reg["status"] = "adapted"
        self.save_registry(reg)

        logger.info(f"Đã kích hoạt adapter '{trial_id}' thành công sau khi nghiệm thu ngữ nghĩa và xác thực mã băm.")
        return reg


active_registry = ActiveAdapterRegistry()
