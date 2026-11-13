from dataclasses import replace
from datetime import datetime, timezone

from server.kuro_engine import KuroEngine
from server.memory_commands import handle_memory_command
from server.process.storage.markdown_memory import MarkdownMemoryStore, VaultError
from server.process.storage.markdown_memory_consolidation import (
    find_consolidation_candidates,
)


class FakeLegacyMemory:
    memory = {}


class FakeLogger:
    def warning(self, message, *args):
        pass


def make_engine(tmp_path):
    engine = KuroEngine.__new__(KuroEngine)
    engine.user_id = "review_user"
    engine.user_memory = FakeLegacyMemory()
    engine.markdown_memory = MarkdownMemoryStore(tmp_path / "vault")
    engine.markdown_memory.initialize_vault()
    engine.markdown_memory.initialize_user(engine.user_id)
    return engine


def test_duplicate_preferences_are_grouped_with_an_explanation(tmp_path):
    engine = make_engine(tmp_path)
    store = engine.markdown_memory
    store.create_memory(engine.user_id, "preference", "I prefer Python.")
    store.create_memory(
        engine.user_id,
        "preference",
        "Python is my preferred programming language.",
    )

    result = engine.get_memory_review()
    candidate = result["candidates"][0]

    assert result["complete"] is True
    assert candidate["relationship"] == "duplicate"
    assert len(candidate["record_ids"]) == 2
    assert "same key terms" in candidate["reason"]


def test_related_topic_memories_are_not_called_conflicts(tmp_path):
    engine = make_engine(tmp_path)
    engine.markdown_memory.create_memory(
        engine.user_id, "fact", "I study electronics engineering."
    )
    engine.markdown_memory.create_memory(
        engine.user_id, "fact", "I am learning digital electronics."
    )

    candidates = engine.get_memory_review()["candidates"]

    assert len(candidates) == 1
    assert candidates[0]["relationship"] == "related"
    assert "electronics" in candidates[0]["reason"]
    assert candidates[0]["potentially_outdated"] is False


def test_different_preference_options_for_same_context_are_flagged(tmp_path):
    engine = make_engine(tmp_path)
    store = engine.markdown_memory
    earlier = store.create_memory(
        engine.user_id,
        "preference",
        "I prefer Python for backend development.",
    )
    later = store.create_memory(
        engine.user_id,
        "preference",
        "I now prefer Java for backend development.",
    )
    records = [
        replace(earlier, updated="2025-01-01T00:00:00+00:00"),
        replace(later, updated="2025-02-01T00:00:00+00:00"),
    ]

    candidates = find_consolidation_candidates(records)

    assert len(candidates) == 1
    assert candidates[0]["relationship"] == "potential_correction"
    assert candidates[0]["potentially_outdated"] is True
    assert "differing preference options" in candidates[0]["reason"]


def test_conflicting_preferences_without_correction_language_remain_conflicts(
    tmp_path,
):
    engine = make_engine(tmp_path)
    store = engine.markdown_memory
    store.create_memory(
        engine.user_id, "preference", "I prefer Python for backend development."
    )
    store.create_memory(
        engine.user_id, "preference", "I prefer Java for backend development."
    )

    candidates = engine.get_memory_review()["candidates"]

    assert len(candidates) == 1
    assert candidates[0]["relationship"] == "potential_conflict"
    assert "review timing and context manually" in candidates[0]["reason"]


def test_expired_record_is_flagged_as_potentially_outdated(tmp_path):
    engine = make_engine(tmp_path)
    first = engine.markdown_memory.create_memory(
        engine.user_id, "fact", "I study electronics engineering."
    )
    second = engine.markdown_memory.create_memory(
        engine.user_id, "fact", "I am learning digital electronics."
    )
    now = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)
    records = [
        replace(first, expires="2026-04-05T11:59:59Z"),
        second,
    ]

    candidates = find_consolidation_candidates(records, now=now)

    assert candidates[0]["relationship"] == "related"
    assert candidates[0]["potentially_outdated"] is True


def test_unrelated_or_different_context_preferences_are_not_grouped(tmp_path):
    engine = make_engine(tmp_path)
    store = engine.markdown_memory
    tea = store.create_memory(engine.user_id, "preference", "I prefer tea.")
    python = store.create_memory(engine.user_id, "preference", "I prefer Python.")
    backend = store.create_memory(
        engine.user_id,
        "preference",
        "I prefer Java for backend development.",
    )
    data_science = store.create_memory(
        engine.user_id,
        "preference",
        "I prefer Python for data science.",
    )

    candidates = find_consolidation_candidates([tea, python, backend, data_science])

    assert all(set(item["record_ids"]) != {tea.id, python.id} for item in candidates)
    assert all(
        not (
            set(item["record_ids"]) == {backend.id, data_science.id}
            and item["relationship"] == "potential_conflict"
        )
        for item in candidates
    )


def test_empty_review_is_complete_and_command_is_available(tmp_path):
    engine = make_engine(tmp_path)

    result = handle_memory_command(
        engine, "memory_review", logger=FakeLogger()
    )

    assert result["type"] == "command_result"
    assert result["command"] == "memory_review"
    assert result["data"]["available"] is True
    assert result["data"]["complete"] is True
    assert result["data"]["candidate_count"] == 0


def test_malformed_and_duplicate_files_mark_review_incomplete(tmp_path):
    engine = make_engine(tmp_path)
    store = engine.markdown_memory
    store.create_memory(engine.user_id, "fact", "I study electronics engineering.")
    duplicate_record = store.create_memory(
        engine.user_id, "fact", "I enjoy digital electronics."
    )
    duplicate_path = (
        store.vault_path
        / "users"
        / engine.user_id
        / "preferences"
        / f"{duplicate_record.id}.md"
    )
    source_path = (
        store.vault_path
        / "users"
        / engine.user_id
        / "facts"
        / f"{duplicate_record.id}.md"
    )
    duplicate_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
    malformed_path = (
        store.vault_path
        / "users"
        / engine.user_id
        / "facts"
        / "mem-malformed.md"
    )
    malformed_path.write_text("malformed frontmatter", encoding="utf-8")

    review = engine.get_memory_review()

    assert review["available"] is True
    assert review["complete"] is False
    assert "incomplete" in review["warning"]
    assert review["candidate_count"] == 0


def test_store_failure_is_safe_and_unavailable(tmp_path):
    engine = make_engine(tmp_path)

    class BrokenStore:
        diagnostics = []

        def list_memories(self, user_id):
            raise VaultError("private vault path or content")

    engine.markdown_memory = BrokenStore()
    result = engine.get_memory_review()

    assert result["available"] is False
    assert result["complete"] is False
    assert result["candidate_count"] == 0
    assert "private vault path" not in result["warning"]


def test_review_is_repeatable_and_preserves_original_records(tmp_path):
    engine = make_engine(tmp_path)
    store = engine.markdown_memory
    first = store.create_memory(
        engine.user_id, "fact", "I study electronics engineering."
    )
    second = store.create_memory(engine.user_id, "fact", "I learn digital electronics.")
    before = store.list_memories(engine.user_id)

    first_review = engine.get_memory_review()
    second_review = engine.get_memory_review()
    after = store.list_memories(engine.user_id)

    assert first_review == second_review
    assert first_review["candidate_count"] == 1
    assert [(record.id, record.content, record.status) for record in before] == [
        (record.id, record.content, record.status) for record in after
    ]
    assert {record.id for record in after} == {first.id, second.id}
