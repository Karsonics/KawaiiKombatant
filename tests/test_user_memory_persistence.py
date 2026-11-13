"""Service-free reliability tests for legacy UserMemory persistence."""

import json

from server.process.storage.user_memory import UserMemory


class FakeCursor:
    def __init__(self, fail=False):
        self.fail = fail
        self.executions = []

    def execute(self, query, params):
        self.executions.append((query, params))
        if self.fail:
            raise OSError("synthetic database write failure")

    def close(self):
        pass


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class SequenceStorage:
    config = {"tables": {"user_preferences": "user_preferences"}}

    def __init__(self, connections):
        self.connections = iter(connections)

    def _get_connection(self):
        item = next(self.connections)
        if isinstance(item, Exception):
            raise item
        return item


def make_memory(storage):
    memory = UserMemory.__new__(UserMemory)
    memory.storage = storage
    memory.user_id = "test-user"
    memory.memory = {
        "personal_info": {"name": "Synthetic private name"},
        "preferences": {},
        "important_facts": [],
        "detailed_preferences": {},
        "conversation_summary": [],
        "topics_discussed": [],
        "last_mood": "neutral",
        "last_emotion": 0.5,
    }
    memory._dirty = True
    return memory


def test_connection_failure_keeps_changes_dirty_and_logs_no_memory_values(caplog):
    memory = make_memory(SequenceStorage([OSError("connection refused")]))

    assert memory.flush() is False

    assert memory._dirty is True
    assert "pending changes remain dirty" in caplog.text
    assert "Synthetic private name" not in caplog.text


def test_save_failure_keeps_changes_dirty_and_successful_retry_clears_them():
    failed_cursor = FakeCursor(fail=True)
    failed_connection = FakeConnection(failed_cursor)
    saved_cursor = FakeCursor()
    saved_connection = FakeConnection(saved_cursor)
    memory = make_memory(
        SequenceStorage([failed_connection, saved_connection])
    )

    assert memory.flush() is False
    assert memory._dirty is True
    assert failed_connection.rollbacks == 1

    assert memory.flush() is True
    assert memory._dirty is False
    assert saved_connection.commits == 1
    persisted = json.loads(saved_cursor.executions[0][1][1])
    assert persisted["personal_info"]["name"] == "Synthetic private name"


def test_successful_direct_save_clears_dirty_state():
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    memory = make_memory(SequenceStorage([connection]))

    assert memory.save_memory() is True
    assert memory._dirty is False
    assert connection.commits == 1
