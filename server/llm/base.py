"""Provider contract for Kuro's text-generation pipeline."""

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Protocol


class LLMProvider(Protocol):
    """Small provider interface; provider-specific SDK objects stay internal."""

    def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        options: Mapping[str, Any] | None = None,
    ) -> Iterator[str]:
        """Yield text fragments in generation order."""
        ...

    def complete_chat(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        options: Mapping[str, Any] | None = None,
    ) -> str:
        """Return a complete assistant text response."""
        ...

    def close(self) -> None:
        """Release provider resources when supported."""
        ...
