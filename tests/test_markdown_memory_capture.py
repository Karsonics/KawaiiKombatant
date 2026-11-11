"""Deterministic tests for automatic Markdown memory capture safeguards."""

import json

from server.kuro_engine import KuroEngine
from server.process.storage.markdown_memory import MarkdownMemoryStore
from server.process.storage.markdown_memory_capture import (
    MarkdownMemoryCapture,
    parse_candidates,
)


class FakeProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.models = []

    def complete_chat(self, model, **_kwargs):
        self.models.append(model)
        return next(self.responses)


def response(memory_type, quote, *, confidence=0.99, tags=None):
    return json.dumps(
        {
            "memories": [
                {
                    "type": memory_type,
                    "content": quote,
                    "evidence": quote,
                    "confidence": confidence,
                    "tags": tags or [],
                }
            ]
        }
    )


def make_store(tmp_path):
    store = MarkdownMemoryStore(tmp_path / "vault")
    store.initialize_vault()
    store.initialize_user("default_user")
    return store


def test_capture_uses_extraction_model_and_persists_through_store(tmp_path):
    store = make_store(tmp_path)
    quote = "I completed my first marathon last year."
    provider = FakeProvider([response("experience", quote)])
    capture = MarkdownMemoryCapture(
        provider, "small-extract-model", store, "default_user"
    )

    assert capture.capture(quote, "session-1") == 1

    records = store.list_memories("default_user", memory_type="experience")
    assert provider.models == ["small-extract-model"]
    assert len(records) == 1
    assert records[0].content == quote
    assert records[0].source_session == "session-1"
    assert records[0].epistemic_status == "confirmed"


def test_duplicate_and_conflicting_preferences_are_skipped(tmp_path):
    store = make_store(tmp_path)
    provider = FakeProvider(
        [
            response("preference", "My favorite color is blue.", tags=["color"]),
            response("preference", "My favorite color is blue.", tags=["color"]),
            response("preference", "My favorite color is green.", tags=["color"]),
        ]
    )
    capture = MarkdownMemoryCapture(provider, "extract", store, "default_user")

    assert capture.capture("My favorite color is blue.") == 1
    assert capture.capture("My favorite color is blue.") == 0
    assert capture.capture("My favorite color is green.") == 0
    records = store.list_memories("default_user", memory_type="preference")
    assert [record.content for record in records] == ["My favorite color is blue."]


def test_rejects_uncertain_sensitive_and_malformed_candidates():
    user_message = "I enjoy hiking."
    assert parse_candidates(
        response("fact", user_message, confidence=0.6), user_message
    ) == []
    sensitive = "My medical diagnosis is asthma."
    assert parse_candidates(response("fact", sensitive), sensitive) == []
    assert parse_candidates(response("fact", "I have asthma."), "I have asthma.") == []
    assert parse_candidates('{"memories": [}', user_message) == []
    assert parse_candidates(
        response("fact", "I enjoy hiking.", tags=["safe", "../escape"]),
        user_message,
    ) == []
    assert parse_candidates(response("fact", "I own a boat."), user_message) == []
    hypothetical = "I would like to go hiking someday."
    assert parse_candidates(response("preference", hypothetical), hypothetical) == []
    third_person = "The user enjoys hiking."
    assert parse_candidates(response("fact", third_person), third_person) == []


def test_rejects_first_person_name_introductions_without_blocking_ordinary_statements():
    for introduction in ("I am Alex.", "I'm Alex.", "I’m Alex."):
        assert parse_candidates(response("fact", introduction), introduction) == []

    for existing_name_form in ("My name is Alex.", "Call me Alex."):
        assert (
            parse_candidates(response("fact", existing_name_form), existing_name_form)
            == []
        )

    ordinary_statement = "I am happy with the result."
    candidates = parse_candidates(
        response("fact", ordinary_statement), ordinary_statement
    )
    assert len(candidates) == 1
    assert candidates[0].content == ordinary_statement


def test_classification_prompt_defines_types_with_examples_and_confidence_floor():
    prompt = MarkdownMemoryCapture._SYSTEM_PROMPT

    assert "preference" in prompt
    assert "like, dislike, preference, choice, or opinion" in prompt
    assert '"I prefer Python examples to use type hints."' in prompt
    assert "fact for stable information directly stated" in prompt
    assert '"I own a bicycle."' in prompt
    assert "experience for a meaningful event" in prompt
    assert '"I completed my first marathon last year."' in prompt
    assert "exactly ONE" in prompt
    assert "character-for-character" in prompt
    assert "between 0.85 and 1.0" in prompt


def test_candidate_validation_accepts_single_types_and_exact_quotes_at_threshold():
    examples = [
        ("preference", "I prefer Python examples to use type hints."),
        ("fact", "I own a bicycle."),
        ("experience", "I completed my first marathon last year."),
    ]
    for memory_type, quote in examples:
        candidates = parse_candidates(
            response(memory_type, quote, confidence=0.85), quote
        )
        assert len(candidates) == 1
        assert candidates[0].type == memory_type
        assert candidates[0].content == quote

    quoted = "I prefer Python examples to use type hints."
    assert parse_candidates(
        response("preference", quoted, confidence=0.84), quoted
    ) == []
    assert parse_candidates(
        response(["preference", "fact"], quoted), quoted
    ) == []
    assert parse_candidates(
        response("preference", "I like Python examples with type hints.",), quoted
    ) == []


def test_capture_logs_counts_without_logging_candidate_content(tmp_path, caplog):
    store = make_store(tmp_path)
    quote = "I own a bicycle."
    invalid = response("fact", "I own a bicycle.", confidence=0.2)
    provider = FakeProvider(
        [
            json.dumps(
                {
                    "memories": [
                        json.loads(response("fact", quote))["memories"][0],
                        json.loads(invalid)["memories"][0],
                    ]
                }
            ),
            response("fact", quote),
        ]
    )
    capture = MarkdownMemoryCapture(provider, "extract", store, "default_user")

    assert capture.capture(quote) == 1
    assert capture.capture(quote) == 0

    assert "returned 2 candidate(s); 1 rejected by validation" in caplog.text
    assert "Skipped 1 duplicate or conflicting" in caplog.text
    assert "Saved 1 Markdown long-term memory record(s)" in caplog.text
    assert quote not in caplog.text


def test_engine_capture_failure_does_not_interrupt_chat(tmp_path, caplog):
    class BrokenProvider(FakeProvider):
        def complete_chat(self, *_args, **_kwargs):
            raise RuntimeError("synthetic extraction failure")

    class FakeMemory:
        def set_mood(self, *_args):
            pass

        def flush(self):
            pass

        def get_mood(self):
            return "neutral"

        def get_emotion(self):
            return 0.5

        def build_context_prompt(self):
            return ""

        def add_fact(self, *_args):
            pass

        def add_preference(self, *_args):
            pass

        def add_personal_info(self, *_args):
            pass

    class FakeStorage:
        def add_message(self, *_args, **_kwargs):
            pass

        def get_recent_context(self, *_args):
            return []

        def get_session_summary(self, *_args):
            return None

        def update_character_state(self, *_args, **_kwargs):
            pass

        def get_message_count(self, *_args):
            return 0

    engine = KuroEngine.__new__(KuroEngine)
    engine.config = {"llm": {"extraction_enabled": False}, "memory": {"enabled": True}}
    engine.dry_run = False
    engine.model_name = "chat"
    engine.extraction_model_name = "extract"
    engine.summarization_model_name = "summary"
    engine.context_length = 10
    engine.storage = FakeStorage()
    engine.user_memory = FakeMemory()
    engine.llm_provider = BrokenProvider([])
    engine.markdown_memory = make_store(tmp_path)
    engine.user_id = "default_user"
    engine.tts_enabled = False
    engine._stream_response = lambda _messages: iter(["Kuro reply."])

    result = engine.process_message("I enjoy hiking.", "session")

    assert result["text"] == "Kuro reply."
    assert engine.markdown_memory.list_memories("default_user") == []
    assert "Long-term Markdown memory capture failed" in caplog.text
    assert "I enjoy hiking." not in caplog.text
