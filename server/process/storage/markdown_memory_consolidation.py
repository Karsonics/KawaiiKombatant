"""Deterministic, non-destructive review of Markdown memory relationships."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from itertools import combinations
from typing import Iterable

from server.process.storage.markdown_memory import MemoryRecord


_TOKEN_PATTERN = re.compile(r"[a-z0-9+#]+", re.IGNORECASE)
_STOP_WORDS = {
    "a", "about", "am", "an", "and", "are", "as", "at", "be", "been",
    "being", "but", "by", "for", "from", "i", "in", "into", "is", "it",
    "me", "my", "of", "on", "or", "our", "the", "their", "then", "these",
    "they", "this", "those", "to", "us", "was", "we", "were", "with", "you",
    "your", "currently", "now", "today", "prefer", "preferred", "prefers",
    "like", "likes", "liked", "love", "loves", "enjoy", "enjoys", "enjoyed",
    "study", "studies", "studied", "learning", "learn", "learned", "programming",
    "language", "development", "engineering", "personal", "memory", "user",
    "always", "city", "everything", "good", "great", "home", "important", "make",
    "never", "people", "person", "project", "really", "something", "think", "thing",
    "time", "used", "want", "work", "using",
}
_TEMPORAL_CUE = re.compile(
    r"\b(?:used to|previously|formerly|no longer|back then|now|currently|"
    r"these days|today)\b",
    re.IGNORECASE,
)
_EXPLICIT_CORRECTION_CUE = re.compile(
    r"\b(?:actually|correction|i meant|i changed my mind|i no longer|"
    r"i do not anymore|i don't anymore|instead|rather than|i used to|"
    r"i now prefer|i now like|i now want)\b",
    re.IGNORECASE,
)
_PREFERENCE_CONTEXT = re.compile(
    r"\bprefer(?:s|red)?\s+(?P<choice>[^.!?,]+?)\s+"
    r"(?:for|when|in|as|over|to)\s+(?P<context>[^.!?]+)",
    re.IGNORECASE,
)
_PREFERENCE_CHOICE = re.compile(
    r"\bprefer(?:s|red)?\s+(?P<choice>[^.!?,]+)", re.IGNORECASE
)
_FRAMED_PREFERENCE = re.compile(
    r"\b(?P<choice>[A-Za-z0-9+#-]+)\s+is\s+my\s+preferred\b",
    re.IGNORECASE,
)
_FAVORITE_PREFERENCE = re.compile(
    r"\b(?:my\s+)?favou?rite\s+(?P<context>[A-Za-z0-9_-]+)\s+is\s+"
    r"(?P<choice>[^.!?]+)",
    re.IGNORECASE,
)


def _terms(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_PATTERN.findall(text.casefold())
        if len(token) > 2 and token not in _STOP_WORDS
    }


def _preference(record: MemoryRecord) -> tuple[set[str], set[str]] | None:
    if record.type != "preference":
        return None
    match = _FAVORITE_PREFERENCE.search(record.content)
    if match:
        choice = _terms(match.group("choice"))
        context = _terms(match.group("context"))
        return (choice, context) if choice else None
    match = _PREFERENCE_CONTEXT.search(record.content)
    if match:
        choice = _terms(match.group("choice"))
        context = _terms(match.group("context"))
        return (choice, context) if choice else None
    match = _FRAMED_PREFERENCE.search(record.content)
    if match:
        choice = _terms(match.group("choice"))
        return (choice, set()) if choice else None
    match = _PREFERENCE_CHOICE.search(record.content)
    if match:
        choice = _terms(match.group("choice"))
        return (choice, set()) if choice else None
    return None


def _similarity(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _updated_at(record: MemoryRecord) -> datetime | None:
    try:
        value = record.updated.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (AttributeError, ValueError):
        return None


def _record_expired(record: MemoryRecord, now: datetime) -> bool:
    if record.expires is None:
        return False
    try:
        expires = datetime.fromisoformat(record.expires.replace("Z", "+00:00"))
        if expires.tzinfo is None or expires.utcoffset() != timezone.utc.utcoffset(expires):
            return True
        return expires.astimezone(timezone.utc) <= now
    except (AttributeError, TypeError, ValueError):
        return True


def _outdated_hint(
    left: MemoryRecord,
    right: MemoryRecord,
    relationship: str,
    now: datetime,
) -> bool:
    if left.status in {"archived", "superseded"} or right.status in {
        "archived", "superseded"
    }:
        return True
    if _record_expired(left, now) or _record_expired(right, now):
        return True
    if relationship not in {
        "duplicate",
        "potential_conflict",
        "potential_correction",
    }:
        return False
    left_time, right_time = _updated_at(left), _updated_at(right)
    if left_time and right_time and left_time != right_time:
        newer = left if left_time > right_time else right
        if _TEMPORAL_CUE.search(newer.content):
            return True
    return bool(
        _TEMPORAL_CUE.search(left.content)
        or _TEMPORAL_CUE.search(right.content)
    )


def classify_memory_relationship(
    left: MemoryRecord, right: MemoryRecord
) -> tuple[str, str] | None:
    left_terms, right_terms = _terms(left.content), _terms(right.content)
    similarity = _similarity(left_terms, right_terms)
    left_normalized = " ".join(_TOKEN_PATTERN.findall(left.content.casefold()))
    right_normalized = " ".join(_TOKEN_PATTERN.findall(right.content.casefold()))
    if left_normalized and left_normalized == right_normalized:
        return "duplicate", "Both records contain the same normalized text."
    if left.type == right.type and left_terms and left_terms == right_terms:
        terms = ", ".join(sorted(left_terms))
        return "duplicate", f"Both records reduce to the same key terms: {terms}."
    if (
        left.type == right.type
        and len(left_terms & right_terms) >= 2
        and similarity >= 0.8
    ):
        return (
            "duplicate",
            f"The records have high normalized term overlap ({similarity:.2f}).",
        )

    left_preference, right_preference = _preference(left), _preference(right)
    if left_preference and right_preference:
        left_choice, left_context = left_preference
        right_choice, right_context = right_preference
        contexts_match = bool(
            left_context and right_context and left_context & right_context
        )
        if left_choice != right_choice and contexts_match:
            relationship = (
                "potential_correction"
                if _EXPLICIT_CORRECTION_CUE.search(left.content)
                or _EXPLICIT_CORRECTION_CUE.search(right.content)
                else "potential_conflict"
            )
            choices = ", ".join(
                sorted((" ".join(sorted(left_choice)), " ".join(sorted(right_choice))))
            )
            shared_context = ", ".join(sorted(left_context & right_context))
            context_text = (
                f" for overlapping context ({shared_context})"
                if shared_context
                else ""
            )
            return (
                relationship,
                (
                    "Explicit correction wording accompanies differing preference "
                    "options "
                    if relationship == "potential_correction"
                    else "The preference options differ "
                )
                + f"({choices}){context_text}; review timing and context manually.",
            )

    if (
        left.type == right.type
        and len(left_terms & right_terms) >= 1
        and (
            _EXPLICIT_CORRECTION_CUE.search(left.content)
            or _EXPLICIT_CORRECTION_CUE.search(right.content)
        )
    ):
        return (
            "potential_correction",
            "Explicit correction wording and shared subject terms may refer to "
            "the same memory; verify intent manually.",
        )

    shared = {
        term for term in left_terms & right_terms if len(term) >= 5
    }
    if shared:
        shared_text = ", ".join(sorted(shared))
        return (
            "related",
            f"The records share topic term(s): {shared_text}, but no duplicate "
            "or conflict rule matched.",
        )
    return None


def find_consolidation_candidates(
    records: Iterable[MemoryRecord],
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Return deterministic pairwise review candidates without changing records."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    ordered = sorted(records, key=lambda record: record.id)
    candidates = []
    for left, right in combinations(ordered, 2):
        relationship = classify_memory_relationship(left, right)
        if relationship is None:
            continue
        kind, reason = relationship
        potentially_outdated = _outdated_hint(left, right, kind, now)
        if potentially_outdated:
            reason += (
                " At least one record may be outdated based on its lifecycle "
                "status, expiry, or explicit time wording."
            )
        candidates.append(
            {
                "relationship": kind,
                "record_ids": [left.id, right.id],
                "reason": reason,
                "potentially_outdated": potentially_outdated,
                "records": [
                    {
                        "id": record.id,
                        "type": record.type,
                        "status": record.status,
                        "content": record.content,
                        "tags": list(record.tags),
                    }
                    for record in (left, right)
                ],
            }
        )
    return candidates
