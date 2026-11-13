"""Service-free tests for session-scoped conversation summaries."""

import json
import threading

import pytest
from mysql.connector import Error as MySQLError

from server.kuro_engine import KuroEngine, _SESSION_LOCKS, _resolve_llm_config
from server.process.storage.mysql_storage import MySQLConversationStorage
from server.process.storage.markdown_memory import MarkdownMemoryStore
from server.process.storage.user_memory import UserMemory


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
        self.next_id = 1

    def add_message(self, session_id, role, content, metadata=None):
        self.messages.setdefault(session_id, []).append(
            {
                "id": self.next_id,
                "role": role,
                "content": content,
                "timestamp": self.next_id,
            }
        )
        self.next_id += 1
        return len(self.messages[session_id])

    def get_recent_context(self, session_id, _limit):
        return self.messages.get(session_id, [])[-10:]

    def get_session_summary(self, session_id):
        return self.summaries.get(session_id, {}).get("summary")

    def get_session_summary_state(self, session_id):
        return self.summaries.get(
            session_id, {"summary": None, "last_message_id": 0}
        ).copy()

    def set_session_summary(self, session_id, summary):
        self.summaries[session_id] = {"summary": summary, "last_message_id": 0}

    def set_session_summary_state(self, session_id, summary, last_message_id):
        self.summaries[session_id] = {
            "summary": summary,
            "last_message_id": last_message_id,
        }

    def get_message_count(self, session_id):
        return len(self.messages.get(session_id, []))

    def get_conversation_history(self, session_id, limit=None):
        records = self.messages.get(session_id, [])
        return records[-limit:] if limit else records

    def get_messages_after(self, session_id, message_id):
        records = [
            record
            for record in self.messages.get(session_id, [])
            if record["id"] > message_id
        ]
        return sorted(records, key=lambda record: (record["timestamp"], record["id"]))

    def update_character_state(self, *_args, **_kwargs):
        pass


class ConcurrentSummaryStorage(FakeStorage):
    """Thread-safe storage fake with the same compare-and-set contract as MySQL."""

    def __init__(self):
        super().__init__()
        self.summary_guard = threading.Lock()
        self.message_guard = threading.Lock()
        self.raw_summaries = {}

    def add_message(self, *args, **kwargs):
        with self.message_guard:
            return super().add_message(*args, **kwargs)

    def get_session_summary_state(self, session_id):
        with self.summary_guard:
            state = self.summaries.get(
                session_id, {"summary": None, "last_message_id": 0}
            ).copy()
            state["storage_value"] = self.raw_summaries.get(session_id)
            state["row_exists"] = session_id in self.raw_summaries
            return state

    def compare_and_set_session_summary_state(
        self,
        session_id,
        summary,
        last_message_id,
        expected_storage_value,
        *,
        expected_row_exists=None,
    ):
        with self.summary_guard:
            if expected_row_exists is None:
                expected_row_exists = expected_storage_value is not None
            if (session_id in self.raw_summaries) != expected_row_exists:
                return False
            if self.raw_summaries.get(session_id) != expected_storage_value:
                return False
            raw = json.dumps(
                {
                    "format": "kuro-session-summary-v1",
                    "summary": summary,
                    "last_message_id": last_message_id,
                },
                separators=(",", ":"),
            )
            self.raw_summaries[session_id] = raw
            self.summaries[session_id] = {
                "summary": summary,
                "last_message_id": last_message_id,
            }
            return True


class ControlledSummaryProvider:
    def __init__(self, *, release=None, barrier=None, conflict_contents=()):
        self.release = release
        self.barrier = barrier
        self.conflict_contents = conflict_contents
        self.call_count = 0
        self.messages = []
        self.guard = threading.Lock()
        self.first_call_started = threading.Event()

    def complete_chat(self, model, **kwargs):
        with self.guard:
            index = self.call_count
            self.call_count += 1
            self.messages.append(kwargs["messages"])
        self.first_call_started.set()
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        if self.release is not None:
            assert self.release.wait(timeout=5)
        return f"synthetic summary {index + 1}"


class FakeProvider:
    def __init__(self, summaries=None, fail=False):
        self.summaries = iter(summaries or ["session summary"])
        self.fail = fail
        self.completion_models = []
        self.completion_messages = []

    def complete_chat(self, model, **kwargs):
        self.completion_models.append(model)
        self.completion_messages.append(kwargs.get("messages", []))
        if self.fail:
            raise RuntimeError("synthetic provider failure")
        return next(self.summaries)

    def stream_chat(self, model, **_kwargs):
        yield "Kuro reply."


class FakeCursor:
    def __init__(self, result=None):
        self.result = result
        self.executions = []
        self.rows = []
        self.rowcount = 1

    def execute(self, query, params=None):
        self.executions.append((query, params))

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.rows

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


class DuplicateKeyError(MySQLError):
    def __init__(self):
        super().__init__("duplicate key")
        self.errno = 1062


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
    for session_id, prefix in (("session-a", "A"), ("session-b", "B")):
        engine.storage.add_message(session_id, "user", f"{prefix} history")
        engine.storage.add_message(session_id, "assistant", f"{prefix} reply")
        engine.storage.add_message(session_id, "user", f"more {prefix}")
        engine.storage.add_message(session_id, "assistant", f"more {prefix} reply")

    engine._maybe_summarize("session-a")
    engine._maybe_summarize("session-b")

    assert engine.storage.summaries == {
        "session-a": {"summary": "summary A", "last_message_id": 4},
        "session-b": {"summary": "summary B", "last_message_id": 8},
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
    engine.storage.summaries["old-session"] = {
        "summary": "private old context",
        "last_message_id": 1,
    }
    contexts = []
    engine._stream_response = lambda messages: (
        contexts.append(messages) or iter(["reply"])
    )

    new_session_id = engine.create_session()
    engine.process_message("hello", new_session_id)

    assert new_session_id != "old-session"
    assert "private old context" not in str(contexts[0])


def test_markdown_memories_are_recalled_across_distinct_sessions(tmp_path):
    vault = tmp_path / "vault"
    store = MarkdownMemoryStore(vault)
    store.initialize_vault()
    store.initialize_user("default_user")
    store.create_memory(
        "default_user", "preference", "The user prefers concise Python examples"
    )
    store.create_memory("default_user", "fact", "The user's bicycle is red")

    engine = make_engine()
    engine.user_id = "default_user"
    engine.markdown_memory = store
    engine.config["memory"] = {"enabled": True}
    contexts = []
    engine._stream_response = lambda messages: (
        contexts.append(messages) or iter(["Kuro reply."])
    )

    engine.process_message("Show me a Python example", "session-one")
    engine.process_message("Can you give another Python example?", "session-two")

    assert "concise Python examples" in str(contexts[0])
    assert "concise Python examples" in str(contexts[1])
    assert "bicycle is red" not in str(contexts[0])
    assert "bicycle is red" not in str(contexts[1])
    assert "untrusted reference data" in str(contexts[1])


def test_markdown_retrieval_failure_does_not_block_chat():
    class BrokenMemory:
        def search_memories(self, *_args, **_kwargs):
            raise OSError("synthetic vault read failure")

    engine = make_engine()
    engine.user_id = "default_user"
    engine.markdown_memory = BrokenMemory()

    result = engine.process_message("hello", "session")

    assert result["text"] == "Kuro reply."


def test_summary_failure_does_not_prevent_chat_or_change_user_preferences():
    engine = make_engine(provider=FakeProvider(fail=True))
    engine.config["chat"]["summarization_threshold"] = 1
    result = engine.process_message("hello", "session")

    assert result["text"] == "Kuro reply."
    assert "session" not in engine.storage.summaries
    assert engine.user_memory.preferences == {"favorite_color": "blue"}


def test_consecutive_summary_cycles_include_previous_summary_and_only_new_segment():
    provider = FakeProvider(["older key fact", "older key fact; new decision"])
    engine = make_engine(provider=provider, threshold=2)
    engine.storage.add_message("session", "user", "Old important context")
    engine.storage.add_message("session", "assistant", "Old response")

    engine._maybe_summarize("session")
    assert engine.storage.get_session_summary_state("session") == {
        "summary": "older key fact",
        "last_message_id": 2,
    }

    engine.storage.add_message("session", "user", "New decision context")
    engine.storage.add_message("session", "assistant", "New response")
    engine._maybe_summarize("session")

    second_input = provider.completion_messages[1][1]["content"]
    assert "older key fact" in second_input
    assert "New decision context" in second_input
    assert "Old important context" not in second_input
    assert engine.storage.get_session_summary_state("session") == {
        "summary": "older key fact; new decision",
        "last_message_id": 4,
    }

    engine.config["chat"]["summarization_threshold"] = 100
    resumed_context = []
    engine._stream_response = lambda messages: (
        resumed_context.append(messages) or iter(["resume reply"])
    )
    engine.process_message("Resume this session", "session")
    assert "older key fact; new decision" in str(resumed_context[0])


def test_failed_summary_keeps_previous_summary_and_checkpoint():
    engine = make_engine(provider=FakeProvider(fail=True), threshold=2)
    engine.storage.set_session_summary_state("session", "valid previous summary", 2)
    engine.storage.add_message("session", "user", "New topic")
    engine.storage.add_message("session", "assistant", "New reply")
    before = engine.storage.get_session_summary_state("session")

    engine._maybe_summarize("session")

    assert engine.storage.get_session_summary_state("session") == before


def test_failed_summary_persistence_keeps_previous_summary_and_checkpoint():
    engine = make_engine(provider=FakeProvider(["replacement"]), threshold=2)
    engine.storage.set_session_summary_state("session", "still valid", 2)
    engine.storage.add_message("session", "user", "New topic")
    engine.storage.add_message("session", "assistant", "New response")
    before = engine.storage.get_session_summary_state("session")

    def fail_persistence(*_args):
        raise OSError("synthetic summary write failure")

    engine.storage.set_session_summary_state = fail_persistence
    engine._maybe_summarize("session")

    assert engine.storage.get_session_summary_state("session") == before


def test_empty_or_incomplete_transcript_does_not_call_summarizer():
    provider = FakeProvider(["should not be used"])
    engine = make_engine(provider=provider, threshold=1)
    engine.storage.add_message("empty", "system", "system only")
    engine.storage.add_message("incomplete", "user", "unfinished turn")

    engine._maybe_summarize("empty")
    engine._maybe_summarize("incomplete")

    assert provider.completion_messages == []
    assert engine.storage.summaries == {}


def test_engine_keeps_chat_available_when_legacy_memory_flush_fails():
    engine = make_engine()

    class BrokenLegacyStorage:
        def _get_connection(self):
            raise OSError("synthetic persistence failure")

    memory = UserMemory.__new__(UserMemory)
    memory.storage = BrokenLegacyStorage()
    memory.user_id = "default_user"
    memory.memory = {
        "personal_info": {},
        "preferences": {},
        "important_facts": [],
        "detailed_preferences": {},
        "conversation_summary": [],
        "topics_discussed": [],
        "last_mood": "neutral",
        "last_emotion": 0.5,
    }
    memory._dirty = False
    engine.user_memory = memory
    result = engine.process_message("hello", "session")

    assert result["text"] == "Kuro reply."
    assert memory._dirty is True


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
    engine.storage.add_message("session", "user", "hello")
    engine.storage.add_message("session", "assistant", "reply")
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


def test_mysql_summary_checkpoint_is_backward_compatible_and_stored_in_text_column():
    storage = MySQLConversationStorage.__new__(MySQLConversationStorage)
    storage.config = {"tables": {"session_summaries": "session_summaries"}}
    old_cursor = FakeCursor(result=("legacy plain summary",))
    storage._get_connection = lambda: FakeConnection(old_cursor)
    assert storage.get_session_summary_state("old") == {
        "summary": "legacy plain summary",
        "last_message_id": 0,
        "storage_value": "legacy plain summary",
        "row_exists": True,
    }

    new_cursor = FakeCursor(
        result=(
            json.dumps(
                {
                    "format": "kuro-session-summary-v1",
                    "summary": "cumulative",
                    "last_message_id": 42,
                }
            ),
        )
    )
    storage._get_connection = lambda: FakeConnection(new_cursor)
    assert storage.get_session_summary_state("new") == {
        "summary": "cumulative",
        "last_message_id": 42,
        "storage_value": json.dumps(
            {
                "format": "kuro-session-summary-v1",
                "summary": "cumulative",
                "last_message_id": 42,
            }
        ),
        "row_exists": True,
    }

    saved_cursor = FakeCursor()
    saved_connection = FakeConnection(saved_cursor)
    storage._get_connection = lambda: saved_connection
    storage.set_session_summary_state("new", "next summary", 47)
    envelope = json.loads(saved_cursor.executions[0][1][1])
    assert envelope["summary"] == "next summary"
    assert envelope["last_message_id"] == 47
    assert saved_connection.commits == 1


def test_mysql_summary_compare_and_set_rejects_older_checkpoint_without_writing():
    storage = MySQLConversationStorage.__new__(MySQLConversationStorage)
    storage.config = {"tables": {"session_summaries": "session_summaries"}}
    previous = json.dumps(
        {
            "format": "kuro-session-summary-v1",
            "summary": "current",
            "last_message_id": 12,
        }
    )
    cursor = FakeCursor()
    cursor.rowcount = 0
    connection = FakeConnection(cursor)
    storage._get_connection = lambda: connection

    assert not storage.compare_and_set_session_summary_state(
        "session", "stale candidate", 10, previous
    )
    assert cursor.executions == []


def test_mysql_summary_compare_and_set_reports_row_changed_since_read():
    storage = MySQLConversationStorage.__new__(MySQLConversationStorage)
    storage.config = {"tables": {"session_summaries": "session_summaries"}}
    previous = json.dumps(
        {
            "format": "kuro-session-summary-v1",
            "summary": "old",
            "last_message_id": 2,
        }
    )
    cursor = FakeCursor()
    cursor.rowcount = 0
    connection = FakeConnection(cursor)
    storage._get_connection = lambda: connection

    assert not storage.compare_and_set_session_summary_state(
        "session", "candidate", 4, previous
    )
    assert "WHERE session_id = %s AND BINARY summary = BINARY %s" in cursor.executions[0][0]
    assert connection.commits == 1


def test_mysql_summary_compare_and_set_treats_concurrent_initial_insert_as_conflict():
    class DuplicateInsertCursor(FakeCursor):
        def execute(self, query, params=None):
            self.executions.append((query, params))
            raise DuplicateKeyError()

    storage = MySQLConversationStorage.__new__(MySQLConversationStorage)
    storage.config = {"tables": {"session_summaries": "session_summaries"}}
    cursor = DuplicateInsertCursor()
    connection = FakeConnection(cursor)
    storage._get_connection = lambda: connection

    assert not storage.compare_and_set_session_summary_state(
        "initial", "candidate", 2, None
    )
    assert connection.rollbacks == 1


def test_mysql_summary_compare_and_set_uses_expected_row_value():
    storage = MySQLConversationStorage.__new__(MySQLConversationStorage)
    storage.config = {"tables": {"session_summaries": "session_summaries"}}
    previous = "legacy plain summary"
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    storage._get_connection = lambda: connection

    assert storage.compare_and_set_session_summary_state(
        "session", "upgraded summary", 3, previous
    )
    query, params = cursor.executions[0]
    assert "WHERE session_id = %s AND BINARY summary = BINARY %s" in query
    assert params[1:] == ("session", previous)
    assert json.loads(params[0])["last_message_id"] == 3
    assert connection.commits == 1


@pytest.mark.parametrize(
    ("expected", "current"),
    [
        ("Summary", "summary"),
        ("résumé", "resume"),
        ("summary", "summary "),
    ],
)
def test_mysql_summary_cas_compares_case_accent_and_trailing_space_exactly(
    expected, current
):
    class ExactCompareCursor(FakeCursor):
        def execute(self, query, params=None):
            super().execute(query, params)
            self.rowcount = int(params[2] == current)

    storage = MySQLConversationStorage.__new__(MySQLConversationStorage)
    storage.config = {"tables": {"session_summaries": "session_summaries"}}
    cursor = ExactCompareCursor()
    connection = FakeConnection(cursor)
    storage._get_connection = lambda: connection

    assert not storage.compare_and_set_session_summary_state(
        "session", "candidate", 3, expected
    )
    assert "BINARY summary = BINARY %s" in cursor.executions[0][0]


def test_mysql_summary_state_distinguishes_missing_row_from_null_row():
    storage = MySQLConversationStorage.__new__(MySQLConversationStorage)
    storage.config = {"tables": {"session_summaries": "session_summaries"}}

    missing_read = FakeCursor(result=None)
    storage._get_connection = lambda: FakeConnection(missing_read)
    missing = storage.get_session_summary_state("missing")
    assert missing == {
        "summary": None,
        "last_message_id": 0,
        "storage_value": None,
        "row_exists": False,
    }

    insert_cursor = FakeCursor()
    storage._get_connection = lambda: FakeConnection(insert_cursor)
    assert storage.compare_and_set_session_summary_state(
        "missing",
        "first summary",
        2,
        missing["storage_value"],
        expected_row_exists=missing["row_exists"],
    )
    assert insert_cursor.executions[0][0].startswith("INSERT INTO")

    null_read = FakeCursor(result=(None,))
    storage._get_connection = lambda: FakeConnection(null_read)
    existing_null = storage.get_session_summary_state("null-row")
    assert existing_null["summary"] is None
    assert existing_null["storage_value"] is None
    assert existing_null["row_exists"] is True

    null_update = FakeCursor()
    storage._get_connection = lambda: FakeConnection(null_update)
    assert storage.compare_and_set_session_summary_state(
        "null-row",
        "repaired summary",
        2,
        existing_null["storage_value"],
        expected_row_exists=existing_null["row_exists"],
    )
    assert "WHERE session_id = %s AND summary IS NULL" in null_update.executions[0][0]


def test_mysql_summary_null_row_update_conflict_is_not_reported_as_saved():
    storage = MySQLConversationStorage.__new__(MySQLConversationStorage)
    storage.config = {"tables": {"session_summaries": "session_summaries"}}
    cursor = FakeCursor()
    cursor.rowcount = 0
    connection = FakeConnection(cursor)
    storage._get_connection = lambda: connection

    assert not storage.compare_and_set_session_summary_state(
        "null-row", "candidate", 2, None, expected_row_exists=True
    )
    assert "summary IS NULL" in cursor.executions[0][0]


def test_same_session_summary_attempts_are_serialized_without_losing_progress():
    engine = make_engine(threshold=2)
    engine.storage = ConcurrentSummaryStorage()
    engine.storage.add_message("same", "user", "first question")
    engine.storage.add_message("same", "assistant", "first answer")
    release = threading.Event()
    provider = ControlledSummaryProvider(release=release)
    engine.llm_provider = provider
    first = threading.Thread(target=engine._maybe_summarize, args=("same",))
    second_started = threading.Event()

    def second_attempt():
        second_started.set()
        engine._maybe_summarize("same")

    second = threading.Thread(target=second_attempt)
    first.start()
    assert provider.first_call_started.wait(timeout=5)
    second.start()
    assert second_started.wait(timeout=5)
    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive() and not second.is_alive()
    assert provider.call_count == 1
    assert engine.storage.get_session_summary_state("same")["last_message_id"] == 2


def test_concurrent_process_message_turns_do_not_interleave():
    engine = make_engine(threshold=100)
    engine.storage = ConcurrentSummaryStorage()
    engine._compute_character_state = lambda *_args: ("neutral", 0.5)
    engine._tts_lock = threading.Lock()
    first_turn_started = threading.Event()
    second_lock_attempted = threading.Event()
    release_first_turn = threading.Event()
    original_lock = engine._session_operation_lock

    def observe_lock_attempt(session_id):
        if threading.current_thread().name == "turn-b":
            second_lock_attempted.set()
        return original_lock(session_id)

    engine._session_operation_lock = observe_lock_attempt

    def stream(messages):
        current_user = messages[-1]["content"]
        if current_user == "TURN_A":
            first_turn_started.set()
            assert second_lock_attempted.wait(timeout=5)
            assert release_first_turn.wait(timeout=5)
        yield "Reply for " + current_user

    engine._stream_response = stream
    errors = []

    def run_turn(content):
        try:
            engine.process_message(content, "shared-session")
        except Exception as exc:  # surfaced in the main test thread below
            errors.append(exc)

    first = threading.Thread(target=run_turn, args=("TURN_A",), name="turn-a")
    second = threading.Thread(target=run_turn, args=("TURN_B",), name="turn-b")
    first.start()
    assert first_turn_started.wait(timeout=5)
    second.start()
    assert second_lock_attempted.wait(timeout=5)
    release_first_turn.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive() and not second.is_alive()
    assert not errors
    assert [
        (message["role"], message["content"])
        for message in engine.storage.messages["shared-session"]
    ] == [
        ("user", "TURN_A"),
        ("assistant", "Reply for TURN_A"),
        ("user", "TURN_B"),
        ("assistant", "Reply for TURN_B"),
    ]


def test_different_sessions_process_turns_independently():
    engine = make_engine(threshold=100)
    engine.storage = ConcurrentSummaryStorage()
    engine._compute_character_state = lambda *_args: ("neutral", 0.5)
    engine._tts_lock = threading.Lock()
    both_streaming = threading.Barrier(2)

    def stream(messages):
        both_streaming.wait(timeout=5)
        yield "Independent reply"

    engine._stream_response = stream
    errors = []

    def run_turn(session_id):
        try:
            engine.process_message(f"Question {session_id}", session_id)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=run_turn, args=(session_id,))
        for session_id in ("left-session", "right-session")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=6)

    assert all(not thread.is_alive() for thread in threads)
    assert not errors
    assert len(engine.storage.messages["left-session"]) == 2
    assert len(engine.storage.messages["right-session"]) == 2


def test_turn_lock_is_released_after_generation_exception():
    engine = make_engine(threshold=100)
    engine.storage = ConcurrentSummaryStorage()
    engine._compute_character_state = lambda *_args: ("neutral", 0.5)
    engine._tts_lock = threading.Lock()
    should_fail = True

    def stream(_messages):
        nonlocal should_fail
        if should_fail:
            should_fail = False
            raise RuntimeError("synthetic generation failure")
        yield "Recovery reply"

    engine._stream_response = stream
    with pytest.raises(RuntimeError, match="synthetic generation failure"):
        engine.process_message("failing user turn", "retry-session")

    result = engine.process_message("next user turn", "retry-session")

    assert result["text"] == "Recovery reply"
    assert [message["role"] for message in engine.storage.messages["retry-session"]] == [
        "user",
        "user",
        "assistant",
    ]
    assert "retry-session" not in _SESSION_LOCKS


def test_different_sessions_can_summarize_independently():
    engine = make_engine(threshold=2)
    engine.storage = ConcurrentSummaryStorage()
    for session_id in ("left", "right"):
        engine.storage.add_message(session_id, "user", f"{session_id} question")
        engine.storage.add_message(session_id, "assistant", f"{session_id} answer")
    provider = ControlledSummaryProvider(barrier=threading.Barrier(2))
    engine.llm_provider = provider
    threads = [
        threading.Thread(target=engine._maybe_summarize, args=(session_id,))
        for session_id in ("left", "right")
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=6)

    assert all(not thread.is_alive() for thread in threads)
    assert provider.call_count == 2
    assert engine.storage.get_session_summary_state("left")["last_message_id"] == 2
    assert engine.storage.get_session_summary_state("right")["last_message_id"] == 4


def test_initial_summary_creation_has_one_compare_and_set_winner():
    storage = ConcurrentSummaryStorage()
    barrier = threading.Barrier(2)
    results = []

    def create(summary):
        barrier.wait(timeout=5)
        results.append(
            storage.compare_and_set_session_summary_state("initial", summary, 2, None)
        )

    threads = [
        threading.Thread(target=create, args=(f"summary {index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=6)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results) == [False, True]


def test_conflict_defers_remaining_exchanges_without_claiming_success(caplog):
    engine = make_engine(threshold=2)
    storage = ConcurrentSummaryStorage()
    engine.storage = storage
    storage.add_message("session", "user", "already summarized question")
    storage.add_message("session", "assistant", "already summarized answer")
    storage.add_message("session", "user", "remaining question")
    storage.add_message("session", "assistant", "remaining answer")
    original_cas = storage.compare_and_set_session_summary_state
    first = True

    def conflict_once(
        session_id,
        summary,
        checkpoint,
        expected,
        *,
        expected_row_exists=None,
    ):
        nonlocal first
        if first:
            first = False
            assert original_cas(session_id, "concurrent winner", 2, expected)
            return False
        return original_cas(
            session_id,
            summary,
            checkpoint,
            expected,
            expected_row_exists=expected_row_exists,
        )

    storage.compare_and_set_session_summary_state = conflict_once
    provider = ControlledSummaryProvider()
    engine.llm_provider = provider

    engine._maybe_summarize("session")

    assert "changed concurrently" in caplog.text
    assert "checkpoint updated" not in caplog.text
    assert storage.get_session_summary_state("session")["last_message_id"] == 2

    engine._maybe_summarize("session")

    assert provider.call_count == 2
    assert "remaining question" in provider.messages[1][1]["content"]
    assert "remaining answer" in provider.messages[1][1]["content"]
    assert "already summarized question" not in provider.messages[1][1]["content"]
    assert storage.get_session_summary_state("session")["last_message_id"] == 4


def test_equal_timestamp_transcript_queries_use_id_as_stable_ordering_key():
    storage = MySQLConversationStorage.__new__(MySQLConversationStorage)
    storage.config = {"tables": {"conversations": "conversations"}}

    recent_cursor = FakeCursor()
    recent_cursor.rows = [
        {"id": 9, "role": "assistant", "content": "second", "timestamp": "t", "metadata": None},
        {"id": 8, "role": "user", "content": "first", "timestamp": "t", "metadata": None},
    ]
    storage._get_connection = lambda: FakeConnection(recent_cursor)
    recent = storage.get_conversation_history("session", limit=2)
    assert [message["id"] for message in recent] == [8, 9]
    assert "ORDER BY timestamp DESC, id DESC" in recent_cursor.executions[0][0]

    history_cursor = FakeCursor()
    history_cursor.rows = [
        {"id": 8, "role": "user", "content": "first", "timestamp": "t", "metadata": None},
        {"id": 9, "role": "assistant", "content": "second", "timestamp": "t", "metadata": None},
    ]
    storage._get_connection = lambda: FakeConnection(history_cursor)
    history = storage.get_conversation_history("session")
    assert [message["id"] for message in history] == [8, 9]
    assert "ORDER BY timestamp ASC, id ASC" in history_cursor.executions[0][0]

    checkpoint_cursor = FakeCursor()
    checkpoint_cursor.rows = history_cursor.rows
    storage._get_connection = lambda: FakeConnection(checkpoint_cursor)
    storage.get_messages_after("session", 7)
    assert "id > %s" in checkpoint_cursor.executions[0][0]
    assert "ORDER BY timestamp ASC, id ASC" in checkpoint_cursor.executions[0][0]


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
