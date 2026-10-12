"""Substantive annotations specification and loader for training-yc188-data06-content-repair.

Defines metadata and loader for 36 authentic, substantive contexts (24 train, 12 valid) across 6 domains:
- linear_algebra (4 train, 2 valid)
- calculus (4 train, 2 valid)
- discrete_math (4 train, 2 valid)
- programming (4 train, 2 valid)
- physics (4 train, 2 valid)
- psychology (4 train, 2 valid)

In public release clone, private definitions are not embedded or imported implicitly.
Reviewed definitions must be loaded explicitly via load_annotation_specs().
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

DOMAINS: List[str] = [
    "linear_algebra",
    "calculus",
    "discrete_math",
    "programming",
    "physics",
    "psychology",
]

EXPECTED_TOTAL_CONTEXTS = 36
EXPECTED_TRAIN_CONTEXTS = 24
EXPECTED_VALID_CONTEXTS = 12
CONTEXTS_PER_DOMAIN_TRAIN = 4
CONTEXTS_PER_DOMAIN_VALID = 2


def load_annotation_specs(path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """Load reviewed substantive annotation definitions from an explicit local path or env var."""
    target = (
        path
        or os.getenv("ANNOTATION_DEFS_PATH")
        or os.getenv("MULTITASK_ANNOTATION_DEFS")
        or os.getenv("ANNOTATION_DEFINITIONS_PATH")
    )
    if not target:
        return []
    target_path = Path(target).resolve()
    if not target_path.is_file():
        raise FileNotFoundError(f"Annotation definitions file not found: {target_path}")
    with open(target_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Annotation definitions in {target_path} must be a JSON list")
    return data


# Compatibility attribute: empty default list without implicit private definition import
ANNOTATION_SPECS: List[Dict[str, Any]] = []
