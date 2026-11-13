"""Unit tests for the OpenAI-compatible provider without a live server."""

from types import SimpleNamespace

from server.llm.openai_compatible import OpenAICompatibleProvider


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        return self.response


class FakeClient:
    def __init__(self, response):
        self.completions = FakeCompletions(response)
        self.chat = SimpleNamespace(completions=self.completions)


def test_stream_chat_yields_text_and_ignores_empty_chunks():
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=" Kuro"))]),
    ]
    client = FakeClient(chunks)
    provider = OpenAICompatibleProvider(base_url="http://localhost:11434/v1", api_key="ollama", client=client)

    assert list(provider.stream_chat(model="qwen3.5:9b", messages=[{"role": "user", "content": "Hi"}])) == ["Hello", " Kuro"]
    assert client.completions.request["stream"] is True
    assert client.completions.request["model"] == "qwen3.5:9b"


def test_stream_chat_passes_supported_generation_options():
    client = FakeClient([])
    provider = OpenAICompatibleProvider(base_url="http://localhost:11434/v1", api_key="ollama", client=client)

    list(provider.stream_chat(
        model="gemma4:e4b",
        messages=[{"role": "user", "content": "Hi"}],
        options={"temperature": 0.4, "top_p": 0.85, "max_tokens": 128, "extra_body": {"think": False}},
    ))

    assert client.completions.request["temperature"] == 0.4
    assert client.completions.request["top_p"] == 0.85
    assert client.completions.request["max_tokens"] == 128
    assert client.completions.request["extra_body"] == {"think": False}


def test_complete_chat_returns_assistant_text():
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Hello there."))])
    client = FakeClient(response)
    provider = OpenAICompatibleProvider(base_url="http://localhost:11434/v1", api_key="ollama", client=client)

    result = provider.complete_chat(
        model="qwen3.5:4b",
        messages=[{"role": "user", "content": "Say hello"}],
        options={"temperature": 0.2},
    )

    assert result == "Hello there."
    assert client.completions.request["temperature"] == 0.2
