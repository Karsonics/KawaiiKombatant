"""Safe startup boundary for the optional Markdown memory vault."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from server.process.storage.markdown_memory import (
    InvalidMemoryError,
    MarkdownMemoryStore,
    VaultError,
)

_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class MarkdownMemoryStartup:
    store: MarkdownMemoryStore | None
    available: bool
    status: str


def validate_user_id(user_id: object) -> bool:
    """Return whether the configured single-user ID meets the store contract."""
    return isinstance(user_id, str) and bool(_USER_ID_RE.fullmatch(user_id))


def initialize_markdown_memory(
    config: dict,
    store_factory=MarkdownMemoryStore,
    *,
    effective_user_id: str | None = None,
) -> MarkdownMemoryStartup:
    """Initialize the explicitly configured vault, or report a safe degraded state."""
    memory_config = config.get("memory")
    vault_path = (
        memory_config.get("vault_path")
        if isinstance(memory_config, dict)
        else None
    )
    if not isinstance(vault_path, str) or not vault_path.strip():
        return MarkdownMemoryStartup(
            None, False, "unavailable: vault path is not configured"
        )

    try:
        path = Path(vault_path).expanduser()
    except (OSError, RuntimeError, ValueError):
        return MarkdownMemoryStartup(
            None, False, "unavailable: configured vault cannot be initialized"
        )
    if not path.is_absolute():
        return MarkdownMemoryStartup(
            None, False, "unavailable: vault path must be absolute"
        )

    chat_config = config.get("chat")
    user_id = effective_user_id
    if user_id is None:
        user_id = chat_config.get("user_id") if isinstance(chat_config, dict) else None
    if not validate_user_id(user_id):
        return MarkdownMemoryStartup(
            None, False, "unavailable: configured user ID is invalid"
        )

    try:
        store = store_factory(path)
        store.initialize_vault()
        store.initialize_user(user_id)
    except (OSError, InvalidMemoryError, VaultError, ValueError):
        return MarkdownMemoryStartup(
            None, False, "unavailable: configured vault cannot be initialized"
        )
    return MarkdownMemoryStartup(store, True, "available")
