"""Adapter for Ollama and other OpenAI-compatible chat-completions APIs."""

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from openai import OpenAI


class OpenAICompatibleProvider:
    """Keep OpenAI SDK details and request construction out of KuroEngine."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout: float = 120.0,
        client: Any | None = None,
    ) -> None:
        self._client = client or OpenAI(
            base_url=base_url,
            api_key=api_key or "not-required",
            timeout=timeout,
        )

    @staticmethod
    def _request_options(options: Mapping[str, Any] | None) -> dict[str, Any]:
        options = options or {}
        request: dict[str, Any] = {}
        for key in ("temperature", "top_p", "max_tokens"):
            value = options.get(key)
            if value is not None:
                request[key] = value
        extra_body = options.get("extra_body")
        if extra_body:
            request["extra_body"] = dict(extra_body)
        return request

    def stream_chat(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        options: Mapping[str, Any] | None = None,
    ) -> Iterator[str]:
        request = {
            "model": model,
            "messages": list(messages),
            "stream": True,
            **self._request_options(options),
        }
        stream = self._client.chat.completions.create(**request)
        for chunk in stream:
            if not chunk.choices:
                continue
            content = getattr(chunk.choices[0].delta, "content", None)
            if content:
                yield content

    def complete_chat(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        options: Mapping[str, Any] | None = None,
    ) -> str:
        request = {
            "model": model,
            "messages": list(messages),
            **self._request_options(options),
        }
        response = self._client.chat.completions.create(**request)
        content = response.choices[0].message.content
        return content or ""

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
