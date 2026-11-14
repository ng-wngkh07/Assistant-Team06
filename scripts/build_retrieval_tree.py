#!/usr/bin/env python3
"""Build persistent hierarchical retrieval tree sidecar database.

Usage:
    python scripts/build_retrieval_tree.py --source-db data/knowledge_base.db --output data/retrieval_tree.db
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.retrieval_tree import build_tree


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build persistent hierarchical retrieval tree sidecar database from a source SQLite knowledge base."
    )
    parser.add_argument(
        "--source-db",
        required=True,
        type=Path,
        help="Path to source SQLite knowledge base (e.g., knowledge_base.db).",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path to new destination sidecar database (e.g., retrieval_tree.db). Must not exist.",
    )
    args = parser.parse_args()

    try:
        stats = build_tree(args.source_db, args.output)
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"Error building retrieval tree: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
