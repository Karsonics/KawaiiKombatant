"""Service-free tests for session-scoped conversation summaries."""

from server.kuro_engine import KuroEngine, _resolve_llm_config
from server.process.storage.mysql_storage import MySQLConversationStorage


class FakeUserMemory:
    def __init__(self):
        self.preferences = {"favorite_color": "blue"}
        self.mood = "neutral"
        self.emotion = 0.5
        self.flush_count = 0

    def build_context_prompt(self):
        return "User preference: favorite_color=blue"

    def get_mood(self):
        return self.mood

    def get_emotion(self):
        return self.emotion

    def set_mood(self, mood, emotion):
        self.mood, self.emotion = mood, emotion

    def flush(self):
        self.flush_count += 1

    def add_fact(self, _fact):
        pass

    def add_preference(self, *_args):
        pass

    def add_personal_info(self, *_args):
        pass


class FakeStorage:
    def __init__(self):
        self.summaries = {}
        self.messages = {}

    def add_message(self, session_id, role, content, metadata=None):
        self.messages.setdefault(session_id, []).append(
            {"role": role, "content": content}
        )
        return len(self.messages[session_id])

    def get_recent_context(self, session_id, _limit):
        return self.messages.get(session_id, [])[-10:]

    def get_session_summary(self, session_id):
        return self.summaries.get(session_id)

    def set_session_summary(self, session_id, summary):
        self.summaries[session_id] = summary

    def get_message_count(self, session_id):
        return len(self.messages.get(session_id, []))

    def get_conversation_history(self, session_id, limit=None):
        records = self.messages.get(session_id, [])
        return records[-limit:] if limit else records

    def update_character_state(self, *_args, **_kwargs):
        pass


class FakeProvider:
    def __init__(self, summaries=None, fail=False):
        self.summaries = iter(summaries or ["session summary"])
        self.fail = fail
        self.completion_models = []

    def complete_chat(self, model, **_kwargs):
        self.completion_models.append(model)
        if self.fail:
            raise RuntimeError("synthetic provider failure")
        return next(self.summaries)

    def stream_chat(self, model, **_kwargs):
        yield "Kuro reply."


class FakeCursor:
    def __init__(self, result=None):
        self.result = result
        self.executions = []

    def execute(self, query, params=None):
        self.executions.append((query, params))

    def fetchone(self):
        return self.result

    def close(self):
        pass


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, **_kwargs):
        return self.fake_cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def make_engine(*, provider=None, threshold=4, extraction_enabled=False):
    engine = KuroEngine.__new__(KuroEngine)
    engine.config = {
        "chat": {"summarization_threshold": threshold},
        "llm": {"extraction_enabled": extraction_enabled},
        "character": {"personality": "synthetic"},
    }
    engine.dry_run = False
    engine.model_name = "chat-model"
    engine.extraction_model_name = "extract-model"
    engine.summarization_model_name = "summary-model"
    engine.context_length = 10
    engine.base_system_prompt = "Synthetic system prompt"
    engine.character_name = "Kuro"
    engine.storage = FakeStorage()
    engine.user_memory = FakeUserMemory()
    engine.llm_provider = provider or FakeProvider()
    engine.tts_enabled = False
    engine._tts_lock = None
    engine._stream_response = lambda _messages: iter(["Kuro reply."])
    return engine


def test_two_sessions_keep_independent_summaries_and_resume_own_context():
    provider = FakeProvider(["summary A", "summary B"])
    engine = make_engine(provider=provider)
    engine.storage.messages["session-a"] = [
        {"role": "user", "content": "A history"},
        {"role": "assistant", "content": "A reply"},
        {"role": "user", "content": "more A"},
        {"role": "assistant", "content": "more A reply"},
    ]
    engine.storage.messages["session-b"] = [
        {"role": "user", "content": "B history"},
        {"role": "assistant", "content": "B reply"},
        {"role": "user", "content": "more B"},
        {"role": "assistant", "content": "more B reply"},
    ]

    engine._maybe_summarize("session-a")
    engine._maybe_summarize("session-b")

    assert engine.storage.summaries == {
        "session-a": "summary A",
        "session-b": "summary B",
    }

    contexts = []

    def capture(messages):
        contexts.append(messages)
        return iter(["Kuro reply."])

    engine._stream_response = capture
    engine.process_message("resume A", "session-a")
    engine.process_message("resume B", "session-b")
    serialized = [str(context) for context in contexts]
    assert "summary A" in serialized[0] and "summary B" not in serialized[0]
    assert "summary B" in serialized[1] and "summary A" not in serialized[1]


def test_new_session_without_summary_does_not_inherit_another_session():
    engine = make_engine()
    engine.storage.summaries["old-session"] = "private old context"
    contexts = []
    engine._stream_response = lambda messages: (
        contexts.append(messages) or iter(["reply"])
    )

    new_session_id = engine.create_session()
    engine.process_message("hello", new_session_id)

    assert new_session_id != "old-session"
    assert "private old context" not in str(contexts[0])


def test_summary_failure_does_not_prevent_chat_or_change_user_preferences():
    engine = make_engine(provider=FakeProvider(fail=True))
    engine.config["chat"]["summarization_threshold"] = 1
    result = engine.process_message("hello", "session")

    assert result["text"] == "Kuro reply."
    assert "session" not in engine.storage.summaries
    assert engine.user_memory.preferences == {"favorite_color": "blue"}


def test_summary_model_is_independent_and_legacy_config_fallback_is_preserved():
    configured = _resolve_llm_config(
        {
            "llm": {
                "model": "chat-model",
                "extraction_model": "extract-model",
                "summarization_model": "summary-model",
            }
        }
    )
    assert configured["extraction_model"] == "extract-model"
    assert configured["summarization_model"] == "summary-model"

    legacy = _resolve_llm_config(
        {"llm": {"model": "chat", "extraction_model": "extract"}}
    )
    assert legacy["summarization_model"] == "extract"

    provider = FakeProvider()
    engine = make_engine(provider=provider)
    engine.config["chat"]["summarization_threshold"] = 2
    engine.storage.messages["session"] = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "reply"},
    ]
    engine._maybe_summarize("session")
    assert provider.completion_models == ["summary-model"]


def test_mysql_storage_reads_and_upserts_summaries_by_session():
    storage = MySQLConversationStorage.__new__(MySQLConversationStorage)
    storage.config = {"tables": {"session_summaries": "session_summaries"}}

    read_cursor = FakeCursor(result=("saved summary",))
    storage._get_connection = lambda: FakeConnection(read_cursor)
    assert storage.get_session_summary("session-a") == "saved summary"
    assert read_cursor.executions[0][1] == ("session-a",)

    write_cursor = FakeCursor()
    write_connection = FakeConnection(write_cursor)
    storage._get_connection = lambda: write_connection
    storage.set_session_summary("session-a", "updated summary")
    query, params = write_cursor.executions[0]
    assert "ON DUPLICATE KEY UPDATE" in query
    assert params == ("session-a", "updated summary")
    assert write_connection.commits == 1


def test_clear_session_also_deletes_its_summary():
    storage = MySQLConversationStorage.__new__(MySQLConversationStorage)
    storage.config = {
        "tables": {
            "conversations": "conversations",
            "character_states": "character_states",
            "session_summaries": "session_summaries",
        }
    }
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    storage._get_connection = lambda: connection

    storage.clear_session("session-a")

    assert len(cursor.executions) == 3
    assert "DELETE FROM session_summaries" in cursor.executions[2][0]
    assert all(params == ("session-a",) for _, params in cursor.executions)
    assert connection.commits == 1
