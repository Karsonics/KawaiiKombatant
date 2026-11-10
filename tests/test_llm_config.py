"""Fast, service-free tests for Kuro's LLM configuration helpers."""

from server.kuro_engine import _resolve_llm_config, _strip_think_tags


def test_resolve_ollama_config_uses_expected_defaults():
    resolved = _resolve_llm_config(
        {
            "llm": {
                "provider": "ollama",
                "model": "qwen3.5:9b",
            }
        }
    )

    assert resolved == {
        "provider": "ollama",
        "base_url": "http://localhost:11434/v1",
        "api_key": "ollama",
        "model": "qwen3.5:9b",
        "extraction_model": "qwen3.5:9b",
        "temperature": None,
        "top_p": None,
        "max_tokens": None,
        "extra_body": {},
    }


def test_resolve_legacy_ollama_config():
    resolved = _resolve_llm_config(
        {
            "ollama": {
                "base_url": "http://127.0.0.1:11434/v1",
                "api_key": "ollama",
                "model": "gemma4:e4b",
            }
        }
    )

    assert resolved["provider"] == "ollama"
    assert resolved["model"] == "gemma4:e4b"
    assert resolved["extraction_model"] == "gemma4:e4b"


def test_resolve_separate_extraction_model():
    resolved = _resolve_llm_config(
        {
            "llm": {
                "provider": "custom",
                "base_url": "http://localhost:9000/v1",
                "api_key": "test-key",
                "model": "main-model",
                "extraction_model": "small-model",
            }
        }
    )

    assert resolved["model"] == "main-model"
    assert resolved["extraction_model"] == "small-model"
    assert resolved["base_url"] == "http://localhost:9000/v1"
    assert resolved["temperature"] is None
    assert resolved["extra_body"] == {}


def test_strip_think_tags_preserves_visible_answer():
    assert _strip_think_tags(
        "Let me think.\n<think>private reasoning</think>\nThe answer is 42."
    ) == "Let me think.\n\nThe answer is 42."


def test_strip_think_tags_handles_multiple_blocks():
    assert _strip_think_tags(
        "<think>first</think>Hello<think>second</think>!"
    ) == "Hello!"


def test_resolve_generation_options():
    resolved = _resolve_llm_config(
        {
            "llm": {
                "provider": "ollama",
                "model": "test-model",
                "temperature": 0.4,
                "top_p": 0.85,
                "max_tokens": 256,
                "extra_body": {"think": False},
            }
        }
    )

    assert resolved["temperature"] == 0.4
    assert resolved["top_p"] == 0.85
    assert resolved["max_tokens"] == 256
    assert resolved["extra_body"] == {"think": False}
