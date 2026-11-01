"""Single-responsibility module for vector cosine similarity and Reciprocal Rank Fusion (RRF).

Shared between `app/searcher.py` and `app/document_lookup.py` to avoid duplicate
vector normalization and ranking math.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence
import numpy as np


def normalize_unit_vector(vec: Sequence[float]) -> np.ndarray | None:
    """Convert a sequence of floats to an L2-normalized float32 numpy vector."""
    arr = np.asarray(vec, dtype=np.float32)
    if arr.ndim != 1 or arr.size == 0 or not np.all(np.isfinite(arr)):
        return None
    norm = float(np.linalg.norm(arr))
    if norm <= 0.0 or not np.isfinite(norm):
        return None
    return arr / norm


def cosine_similarity_unit(query_unit: np.ndarray, candidate_vec: Sequence[float]) -> float | None:
    """Compute cosine similarity between a unit query vector and a candidate vector."""
    cand_unit = normalize_unit_vector(candidate_vec)
    if cand_unit is None or cand_unit.shape != query_unit.shape:
        return None
    score = float(np.dot(query_unit, cand_unit))
    if not np.isfinite(score):
        return None
    return score


def rrf_merge_scores(
    fts_ranked_ids: Sequence[int],
    semantic_ranked_ids: Sequence[int],
    fts_weight: float = 0.5,
    semantic_weight: float = 0.5,
    rrf_k: int = 60,
) -> Dict[int, float]:
    """Compute weighted Reciprocal Rank Fusion (RRF) scores across FTS and semantic rankings."""
    scores: Dict[int, float] = {}
    for rank, cid in enumerate(fts_ranked_ids, 1):
        scores[cid] = scores.get(cid, 0.0) + fts_weight * (1.0 / (rrf_k + rank))
    for rank, cid in enumerate(semantic_ranked_ids, 1):
        scores[cid] = scores.get(cid, 0.0) + semantic_weight * (1.0 / (rrf_k + rank))
    return scores
