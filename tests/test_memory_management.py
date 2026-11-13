import asyncio
import sys
from types import SimpleNamespace

import pytest

from bot_main import run_direct, run_ws
from server.kuro_engine import KuroEngine, MemoryClearError
from server.memory_commands import handle_memory_command
from server.process.storage.user_memory import UserMemory
from server.process.storage.markdown_memory import (
    MarkdownMemoryStore,
    InvalidMemoryError,
    MemoryNotFoundError,
    VaultError,
)


class FakeLegacyMemory:
    def __init__(self, memory=None, error=None):
        self.memory = memory or {}
        self.error = error
        self.clear_calls = 0

    def clear_memory(self):
        self.clear_calls += 1
        if self.error:
            raise self.error
        self.memory = {}


class FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *args):
        self.warnings.append((message, args))


def make_engine(tmp_path, memory=None, markdown=True):
    engine = KuroEngine.__new__(KuroEngine)
    engine.user_id = "default_user"
    engine.user_memory = FakeLegacyMemory(memory)
    if markdown:
        engine.markdown_memory = MarkdownMemoryStore(tmp_path / "vault")
        engine.markdown_memory.initialize_vault()
        engine.markdown_memory.initialize_user(engine.user_id)
    else:
        engine.markdown_memory = None
    return engine


def test_status_has_empty_counts_and_store_availability(tmp_path):
    engine = make_engine(tmp_path, {"preferences": {}, "important_facts": []})
    status = engine.get_memory_status()
    assert status["legacy"]["total"] == 0
    assert status["legacy"]["available"] is True
    assert status["markdown"]["total"] == 0
    assert status["markdown"]["categories"]["fact"] == {
        "active": 0, "archived": 0, "superseded": 0
    }


def test_status_counts_markdown_by_type_and_lifecycle(tmp_path):
    engine = make_engine(tmp_path, {"preferences": {"food": "tea"}})
    store = engine.markdown_memory
    store.create_memory(engine.user_id, "fact", "Synthetic active")
    store.create_memory(engine.user_id, "fact", "Synthetic archived", status="archived")
    store.create_memory(engine.user_id, "preference", "Synthetic superseded", status="superseded")
    status = engine.get_memory_status()
    assert status["legacy"]["total"] == 1
    assert status["markdown"]["categories"]["fact"] == {
        "active": 1,
        "archived": 1,
        "superseded": 0,
    }
    assert status["markdown"]["categories"]["preference"]["superseded"] == 1


def test_status_reports_unavailable_store_and_listing_failure(tmp_path):
    engine = make_engine(tmp_path, markdown=False)
    assert engine.get_memory_status()["markdown"]["available"] is False

    class BrokenStore:
        def list_memories(self, user_id):
            raise VaultError("synthetic failure")

        def get_memory(self, user_id, memory_id):
            raise VaultError("synthetic failure")

    engine.markdown_memory = BrokenStore()
    status = engine.get_memory_status()
    assert status["markdown"]["available"] is False
    assert status["markdown"]["total"] == 0


def test_status_warns_when_malformed_markdown_files_make_counts_incomplete(tmp_path):
    engine = make_engine(tmp_path)
    store = engine.markdown_memory
    store.create_memory(engine.user_id, "fact", "Synthetic valid record")
    malformed = (
        store.vault_path
        / "users"
        / engine.user_id
        / "facts"
        / "mem-malformed.md"
    )
    malformed.write_text("not valid frontmatter", encoding="utf-8")

    status = engine.get_memory_status()["markdown"]

    assert status["available"] is True
    assert status["categories"]["fact"]["active"] == 1
    assert "counts may be incomplete" in status["warning"]


def test_status_warns_when_duplicate_ids_are_skipped(tmp_path):
    engine = make_engine(tmp_path)
    store = engine.markdown_memory
    record = store.create_memory(engine.user_id, "fact", "Synthetic duplicate")
    duplicate = (
        store.vault_path
        / "users"
        / engine.user_id
        / "preferences"
        / f"{record.id}.md"
    )
    duplicate.write_text(
        (store.vault_path / "users" / engine.user_id / "facts" / f"{record.id}.md")
        .read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    status = engine.get_memory_status()["markdown"]

    assert status["available"] is True
    assert status["categories"]["fact"]["active"] == 0
    assert "counts may be incomplete" in status["warning"]


def test_clear_legacy_only_preserves_markdown(tmp_path):
    engine = make_engine(tmp_path, {"preferences": {"food": "tea"}})
    record = engine.markdown_memory.create_memory(engine.user_id, "fact", "Synthetic")
    assert engine.clear_memory() == "legacy memory cleared"
    assert engine.user_memory.clear_calls == 1
    assert engine.markdown_memory.get_memory(engine.user_id, record.id) == record


def test_clear_markdown_and_both_scopes(tmp_path):
    engine = make_engine(tmp_path, {"preferences": {"food": "tea"}})
    store = engine.markdown_memory
    store.create_memory(engine.user_id, "fact", "Synthetic one")
    store.create_memory(
        engine.user_id, "preference", "Synthetic two", status="archived"
    )
    with pytest.raises(PermissionError, match="requires confirmation"):
        engine.clear_memory("markdown")
    assert store.list_memories(engine.user_id)
    assert (
        engine.clear_memory("markdown", confirmed=True)
        == "2 Markdown memories deleted"
    )
    assert store.list_memories(engine.user_id) == []
    assert engine.user_memory.clear_calls == 0

    store.create_memory(engine.user_id, "fact", "Synthetic three")
    with pytest.raises(PermissionError, match="requires confirmation"):
        engine.clear_memory("both")
    assert store.list_memories(engine.user_id)
    assert engine.user_memory.clear_calls == 0
    assert (
        engine.clear_memory("both", confirmed=True)
        == "1 Markdown memories deleted; legacy memory cleared"
    )
    assert engine.user_memory.clear_calls == 1


@pytest.mark.parametrize(
    "confirmed", [False, None, 0, 1, "yes", "true", [], {}]
)
@pytest.mark.parametrize("scope", ["markdown", "both"])
def test_bulk_clear_requires_actual_true_confirmation(tmp_path, scope, confirmed):
    engine = make_engine(tmp_path)
    record = engine.markdown_memory.create_memory(engine.user_id, "fact", "Synthetic")

    with pytest.raises(PermissionError, match="requires confirmation"):
        engine.clear_memory(scope, confirmed=confirmed)

    assert engine.markdown_memory.get_memory(engine.user_id, record.id) == record
    assert engine.user_memory.clear_calls == 0


def test_clear_rejects_invalid_scope_and_unavailable_store(tmp_path):
    engine = make_engine(tmp_path, markdown=False)
    with pytest.raises(ValueError):
        engine.clear_memory("everything")
    with pytest.raises(RuntimeError, match="unavailable"):
        engine.clear_memory("markdown", confirmed=True)


def test_combined_clear_listing_failure_reports_legacy_not_attempted(tmp_path):
    engine = make_engine(tmp_path)

    class ListingFailure:
        def list_memories(self, user_id):
            raise VaultError("private listing detail")

    engine.markdown_memory = ListingFailure()
    result = handle_memory_command(
        engine,
        "clear_memory",
        scope="both",
        confirmed=True,
        logger=FakeLogger(),
    )

    assert result["type"] == "error"
    assert "before deletion began" in result["data"]
    assert "legacy memory clearing was not attempted" in result["data"]
    assert "private listing detail" not in result["data"]
    assert engine.user_memory.clear_calls == 0


def test_combined_clear_diagnostics_report_legacy_not_attempted(tmp_path):
    engine = make_engine(tmp_path)
    store = engine.markdown_memory
    valid = store.create_memory(engine.user_id, "fact", "Synthetic valid record")
    invalid = (
        store.vault_path
        / "users"
        / engine.user_id
        / "facts"
        / "mem-invalid.md"
    )
    invalid.write_text("malformed metadata", encoding="utf-8")

    result = handle_memory_command(
        engine,
        "clear_memory",
        scope="both",
        confirmed=True,
        logger=FakeLogger(),
    )

    assert result["type"] == "error"
    assert "before deletion began" in result["data"]
    assert "legacy memory clearing was not attempted" in result["data"]
    assert engine.user_memory.clear_calls == 0
    assert store.get_memory(engine.user_id, valid.id) == valid


def test_combined_clear_unavailable_store_reports_legacy_not_attempted(tmp_path):
    engine = make_engine(tmp_path, markdown=False)
    result = handle_memory_command(
        engine,
        "clear_memory",
        scope="both",
        confirmed=True,
        logger=FakeLogger(),
    )

    assert result["type"] == "error"
    assert "before deletion began" in result["data"]
    assert "legacy memory clearing was not attempted" in result["data"]
    assert engine.user_memory.clear_calls == 0


def test_clear_markdown_refuses_to_partially_clear_invalid_vault(tmp_path):
    engine = make_engine(tmp_path)
    store = engine.markdown_memory
    valid = store.create_memory(engine.user_id, "fact", "Synthetic valid record")
    invalid = (
        store.vault_path
        / "users"
        / engine.user_id
        / "facts"
        / "mem-000999.md"
    )
    invalid.write_text("not valid frontmatter", encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid or duplicate"):
        engine.clear_memory("markdown", confirmed=True)
    assert store.get_memory(engine.user_id, valid.id) == valid


def test_partial_bulk_delete_reports_completed_count_without_exception_details(
    tmp_path,
):
    engine = make_engine(tmp_path)
    store = engine.markdown_memory
    for index in range(3):
        store.create_memory(engine.user_id, "fact", f"Synthetic {index}")

    class FailAfterOneDelete:
        diagnostics = []

        def __init__(self):
            self.delete_calls = 0

        def list_memories(self, user_id):
            return store.list_memories(user_id)

        def delete_memory(
            self, user_id, memory_id, *, expected_version, confirmed=False
        ):
            self.delete_calls += 1
            if self.delete_calls == 2:
                raise OSError("private storage detail")
            return store.delete_memory(
                user_id,
                memory_id,
                expected_version=expected_version,
                confirmed=confirmed,
            )

    engine.markdown_memory = FailAfterOneDelete()
    result = handle_memory_command(
        engine,
        "clear_memory",
        scope="markdown",
        confirmed=True,
        logger=FakeLogger(),
    )

    assert result["type"] == "error"
    assert "1 of 3 records" in result["data"]
    assert "private storage detail" not in result["data"]
    assert len(store.list_memories(engine.user_id)) == 2


def test_combined_clear_reports_markdown_complete_when_legacy_clear_fails(tmp_path):
    engine = make_engine(tmp_path)
    engine.markdown_memory.create_memory(engine.user_id, "fact", "Synthetic")
    engine.user_memory.error = RuntimeError("private database detail")

    result = handle_memory_command(
        engine,
        "clear_memory",
        scope="both",
        confirmed=True,
        logger=FakeLogger(),
    )

    assert result["type"] == "error"
    assert "Markdown clearing completed (1 records deleted)" in result["data"]
    assert "legacy memory clearing failed" in result["data"]
    assert "private database detail" not in result["data"]
    assert engine.markdown_memory.list_memories(engine.user_id) == []


def test_clear_and_delete_store_failures_propagate_without_claiming_success(tmp_path):
    engine = make_engine(tmp_path)

    class BrokenStore:
        def list_memories(self, user_id):
            raise VaultError("synthetic failure")

        def get_memory(self, user_id, memory_id):
            raise VaultError("synthetic failure")

    engine.markdown_memory = BrokenStore()
    with pytest.raises(VaultError):
        engine.clear_memory("markdown", confirmed=True)
    with pytest.raises(VaultError):
        engine.delete_markdown_memory("mem-000001", confirmed=True)


def test_delete_uses_stable_id_and_missing_id_is_reported(tmp_path):
    engine = make_engine(tmp_path)
    record = engine.markdown_memory.create_memory(engine.user_id, "fact", "Synthetic")
    with pytest.raises(PermissionError, match="requires confirmation"):
        engine.delete_markdown_memory(record.id)
    assert engine.markdown_memory.get_memory(engine.user_id, record.id) == record

    assert engine.delete_markdown_memory(record.id, confirmed=True) == (
        f"Markdown memory {record.id} deleted"
    )
    assert engine.markdown_memory.list_memories(engine.user_id) == []
    with pytest.raises(MemoryNotFoundError):
        engine.delete_markdown_memory(record.id, confirmed=True)
    with pytest.raises(InvalidMemoryError):
        engine.delete_markdown_memory("../invalid", confirmed=True)


def test_explicit_memory_confirmation_updates_metadata_with_version_safety(tmp_path):
    engine = make_engine(tmp_path)
    record = engine.markdown_memory.create_memory(
        engine.user_id, "fact", "Synthetic inferred fact", epistemic_status="inferred"
    )

    with pytest.raises(PermissionError):
        engine.confirm_markdown_memory(record.id)
    assert engine.markdown_memory.get_memory(engine.user_id, record.id) == record

    result = engine.confirm_markdown_memory(record.id, confirmed=True)
    confirmed = engine.markdown_memory.get_memory(engine.user_id, record.id)

    assert result == f"Markdown memory {record.id} confirmed"
    assert confirmed.epistemic_status == "confirmed"
    assert confirmed.last_confirmed is not None
    assert confirmed.version != record.version


def test_confirm_memory_command_requires_confirmation_before_store_access(tmp_path):
    engine = make_engine(tmp_path)
    record = engine.markdown_memory.create_memory(
        engine.user_id, "fact", "Synthetic fact"
    )
    response = handle_memory_command(
        engine,
        "confirm_memory",
        memory_id=record.id,
        logger=FakeLogger(),
    )

    assert response["data"]["confirmation_required"] is True
    assert engine.markdown_memory.get_memory(engine.user_id, record.id) == record

    confirmed = handle_memory_command(
        engine,
        "confirm_memory",
        memory_id=record.id,
        confirmed=True,
        logger=FakeLogger(),
    )

    assert confirmed == {
        "type": "command_result",
        "command": "confirm_memory",
        "data": f"Markdown memory {record.id} confirmed",
    }
    assert engine.markdown_memory.get_memory(engine.user_id, record.id).last_confirmed


def test_command_confirmation_prevents_mutation_until_explicitly_confirmed(tmp_path):
    engine = make_engine(tmp_path, {"preferences": {"food": "tea"}})
    logger = FakeLogger()
    status = handle_memory_command(engine, "memory_status", logger=logger)
    assert status["type"] == "command_result"
    assert status["data"]["legacy"]["total"] == 1
    result = handle_memory_command(
        engine, "clear_memory", scope="both", logger=logger
    )
    assert result["data"]["confirmation_required"] is True
    assert engine.user_memory.clear_calls == 0
    confirmed = handle_memory_command(
        engine, "clear_memory", scope="legacy", confirmed=True, logger=logger
    )
    assert confirmed == {
        "type": "command_result",
        "command": "clear_memory",
        "data": "legacy memory cleared",
    }
    assert engine.user_memory.clear_calls == 1


def test_delete_command_confirmation_and_failure_handling(tmp_path):
    engine = make_engine(tmp_path)
    record = engine.markdown_memory.create_memory(engine.user_id, "fact", "Synthetic")
    logger = FakeLogger()
    pending = handle_memory_command(
        engine, "delete_memory", memory_id=record.id, logger=logger
    )
    assert pending["data"]["confirmation_required"] is True
    assert engine.markdown_memory.get_memory(engine.user_id, record.id)
    deleted = handle_memory_command(
        engine, "delete_memory", memory_id=record.id, confirmed=True, logger=logger
    )
    assert deleted == {
        "type": "command_result",
        "command": "delete_memory",
        "data": f"Markdown memory {record.id} deleted",
    }
    assert handle_memory_command(engine, "delete_memory", logger=logger)["type"] == "error"

    failure = handle_memory_command(
        engine, "delete_memory", memory_id=record.id, confirmed=True, logger=logger
    )
    assert failure["type"] == "error"
    assert logger.warnings


def test_clear_command_failure_is_reported(tmp_path):
    engine = make_engine(tmp_path)
    engine.user_memory.error = RuntimeError("synthetic failure")
    result = handle_memory_command(
        engine, "clear_memory", confirmed=True, logger=FakeLogger()
    )
    assert result == {"type": "error", "data": "Unable to clear the requested memory."}


def test_legacy_clear_does_not_claim_success_when_database_is_unavailable(tmp_path):
    class UnavailableStorage:
        def _get_connection(self):
            raise OSError("synthetic database outage")

    engine = make_engine(tmp_path)
    legacy_memory = UserMemory.__new__(UserMemory)
    legacy_memory.storage = UnavailableStorage()
    legacy_memory.user_id = engine.user_id
    legacy_memory.memory = {"preferences": {"favorite_color": "blue"}}
    legacy_memory._dirty = True
    engine.user_memory = legacy_memory

    result = handle_memory_command(
        engine, "clear_memory", confirmed=True, logger=FakeLogger()
    )

    assert result == {
        "type": "error",
        "data": "Unable to clear the requested memory.",
    }
    assert legacy_memory.memory == {"preferences": {"favorite_color": "blue"}}
    assert legacy_memory._dirty is True


def test_direct_cli_skips_blank_and_whitespace_input(monkeypatch):
    class FakeEngine:
        closed = False
        process_calls = 0

        def display_memory(self):
            pass

        def close(self):
            self.closed = True

        def process_message(self, *args):
            self.process_calls += 1

    engine = FakeEngine()
    monkeypatch.setattr("server.kuro_engine.KuroEngine", lambda **kwargs: engine)
    monkeypatch.setattr("bot_main._pick_session", lambda current_engine: "session-id")
    inputs = iter(["", " \t ", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    run_direct()

    assert engine.closed is True
    assert engine.process_calls == 0


def test_direct_cli_reports_partial_clear_failure(monkeypatch, capsys):
    class FakeEngine:
        def display_memory(self):
            pass

        def close(self):
            pass

        def clear_memory(self, **kwargs):
            raise MemoryClearError("Markdown clearing stopped after deleting 1 of 2 records.")

    monkeypatch.setattr("server.kuro_engine.KuroEngine", lambda **kwargs: FakeEngine())
    monkeypatch.setattr("bot_main._pick_session", lambda current_engine: "session-id")
    inputs = iter(["clear_memory markdown", "yes", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    run_direct()

    assert "stopped after deleting 1 of 2 records" in capsys.readouterr().out


def test_websocket_cli_skips_blank_and_whitespace_without_sending(monkeypatch):
    class FakeWebSocket:
        sent = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, message):
            self.sent.append(message)

    websocket = FakeWebSocket()

    async def connect(_uri):
        return websocket

    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(connect=connect))
    inputs = iter(["", " \t ", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

    asyncio.run(run_ws())

    assert websocket.sent == []
