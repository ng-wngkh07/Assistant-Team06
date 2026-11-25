"""Authored learning concepts. Sources establish availability, never invent labels.

Keep aliases specific enough to identify a concept in a passage. Changes to this
catalog require source/content review; model output cannot extend it at runtime.
Re-exports from `app.topic_tree` for backward compatibility.
"""
from app.topic_tree import (
    CHILD_TO_PARENT,
    PATTERNS,
    TOPIC_TREE,
    TOPICS,
    dedicated_aliases_for,
    expand_topic_focus_terms,
    get_topic_tree_catalog,
    infer_query_topic_hierarchy,
    matches,
    normalized,
    resolve_topic,
    resolve_topic_hierarchy,
    resolve_topic_query,
)
from app.topic_router import TopicRouter

__all__ = [
    "CHILD_TO_PARENT",
    "PATTERNS",
    "TOPIC_TREE",
    "TOPICS",
    "TopicRouter",
    "dedicated_aliases_for",
    "expand_topic_focus_terms",
    "get_topic_tree_catalog",
    "infer_query_topic_hierarchy",
    "matches",
    "normalized",
    "resolve_topic",
    "resolve_topic_hierarchy",
    "resolve_topic_query",
]
