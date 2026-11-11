from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from server.process.storage import markdown_memory
from server.process.storage.markdown_memory import (
    InvalidMemoryError,
    MarkdownMemoryStore,
    MemoryConflictError,
    MemoryNotFoundError,
    VaultError,
)


@pytest.fixture
def store(tmp_path: Path) -> MarkdownMemoryStore:
    result = MarkdownMemoryStore(tmp_path / "KuroMemory")
    result.initialize_vault()
    return result


def test_vault_initialization_creates_expected_structure(store, tmp_path):
    store.initialize_user("default_user")
    root = tmp_path / "KuroMemory"
    assert (root / "README.md").is_file()
    assert (root / "users" / "default_user" / "profile.md").is_file()
    for directory in ("facts", "preferences", "experiences", "temporary"):
        assert (root / "users" / "default_user" / directory).is_dir()


def test_path_is_required_and_vault_is_not_created_implicitly(tmp_path):
    with pytest.raises(VaultError):
        MarkdownMemoryStore("")
    store = MarkdownMemoryStore(tmp_path / "not-created")
    with pytest.raises(VaultError):
        store.create_memory("u", "fact", "text")
    assert not (tmp_path / "not-created").exists()


def test_create_read_list_and_stable_ids(store):
    created = store.create_memory("default_user", "fact", "Kuro is local-first")
    assert created.id.startswith("mem-")
    assert len(created.id) == 36
    assert store.get_memory("default_user", created.id) == created
    second = store.create_memory("default_user", "fact", "Second fact")
    assert second.id != created.id
    items = store.list_memories("default_user")
    assert len(items) == 2
    assert len({item.id for item in items}) == 2


def test_concurrent_store_instances_serialize_vault_writes(store):
    second_store = MarkdownMemoryStore(store.vault_path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda index: (store if index % 2 else second_store).create_memory(
                    "u", "fact", f"Synthetic fact {index}"
                ),
                range(16),
            )
        )
    assert len({item.id for item in results}) == 16
    assert len(store.list_memories("u")) == 16


def test_user_and_type_filters_are_isolated(store):
    store.create_memory("alice", "fact", "A fact")
    store.create_memory("alice", "preference", "A preference")
    store.create_memory("bob", "fact", "Other user's fact")
    assert [m.content for m in store.list_memories("alice", memory_type="fact")] == [
        "A fact"
    ]
    assert [m.content for m in store.list_memories("bob")] == ["Other user's fact"]


def test_filters_epistemic_status_status_and_tags(store):
    store.create_memory(
        "u",
        "experience",
        "A synthetic experience",
        epistemic_status="inferred",
        tags=["work", "important"],
    )
    store.create_memory("u", "experience", "Another item", status="archived")
    result = store.list_memories(
        "u", epistemic_status="inferred", active=True, tags=["work"]
    )
    assert [item.content for item in result] == ["A synthetic experience"]
    assert store.list_memories("u", active=False)[0].status == "archived"


def test_update_writes_new_version_and_rejects_stale_record(store):
    original = store.create_memory("u", "fact", "Original")
    externally_loaded = store.get_memory("u", original.id)
    updated = store.update_memory(original, content="Corrected")
    assert updated.content == "Corrected"
    assert updated.version != original.version
    with pytest.raises(MemoryConflictError):
        store.update_memory(externally_loaded, content="Stale overwrite")
    assert store.get_memory("u", original.id).content == "Corrected"


def test_update_type_moves_memory_to_matching_directory(store):
    record = store.create_memory("u", "fact", "Reclassify me")
    updated = store.update_memory(record, memory_type="experience")
    user_dir = store.vault_path / "users" / "u"
    assert not (user_dir / "facts" / f"{record.id}.md").exists()
    assert (user_dir / "experiences" / f"{record.id}.md").is_file()
    assert updated.type == "experience"


def test_external_edit_is_detected_before_update(store):
    record = store.create_memory("u", "fact", "Original")
    path = store.vault_path / "users" / "u" / "facts" / f"{record.id}.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("Original", "Manual edit"),
        encoding="utf-8",
    )
    with pytest.raises(MemoryConflictError):
        store.update_memory(record, content="Kuro edit")
    assert "Manual edit" in path.read_text(encoding="utf-8")


def test_delete_requires_current_version(store):
    record = store.create_memory("u", "fact", "Delete me")
    with pytest.raises(MemoryConflictError):
        store.delete_memory("u", record.id, expected_version="stale")
    store.delete_memory("u", record.id, expected_version=record.version)
    with pytest.raises(MemoryNotFoundError):
        store.get_memory("u", record.id)
    assert store.create_memory("u", "fact", "New item").id != record.id


def test_duplicate_id_is_rejected(store):
    store.create_memory("u", "fact", "Existing", memory_id="mem-000123")
    with pytest.raises(MemoryConflictError):
        store.create_memory("u", "preference", "Duplicate", memory_id="mem-000123")


@pytest.mark.parametrize("user_id", ["../outside", "a/b", "..", "", "a\\b"])
def test_invalid_user_ids_are_rejected(store, user_id):
    with pytest.raises(InvalidMemoryError):
        store.create_memory(user_id, "fact", "No escape")


def test_malformed_frontmatter_is_skipped_without_rewrite(store):
    store.initialize_user("u")
    bad = store.vault_path / "users" / "u" / "facts" / "mem-000001.md"
    original = "---\nthis: [is: invalid\n---\nsecret synthetic body\n"
    bad.write_text(original, encoding="utf-8")
    assert store.list_memories("u") == []
    assert store.diagnostics == ["Skipped invalid memory file: mem-000001.md"]
    assert bad.read_text(encoding="utf-8") == original


def test_unknown_metadata_is_skipped_and_preserved(store):
    record = store.create_memory("u", "fact", "Synthetic")
    path = store.vault_path / "users" / "u" / "facts" / f"{record.id}.md"
    original = path.read_text(encoding="utf-8").replace(
        "status: active", "status: active\nextra: value"
    )
    path.write_text(original, encoding="utf-8")
    assert store.list_memories("u") == []
    assert path.read_text(encoding="utf-8") == original


def test_atomic_write_failure_preserves_existing_file(store, monkeypatch):
    record = store.create_memory("u", "fact", "Preserve me")
    path = store.vault_path / "users" / "u" / "facts" / f"{record.id}.md"
    original = path.read_bytes()

    def fail_replace(*args, **kwargs):
        raise PermissionError("synthetic failure")

    monkeypatch.setattr(markdown_memory.os, "replace", fail_replace)
    with pytest.raises(VaultError):
        store.update_memory(record, content="Must not replace")
    assert path.read_bytes() == original


def test_missing_user_vault_and_unreadable_vault_are_reported(tmp_path):
    store = MarkdownMemoryStore(tmp_path / "missing")
    with pytest.raises(VaultError):
        store.list_memories("u")
    store.initialize_vault()
    with pytest.raises(VaultError):
        store.list_memories("u")


def test_read_only_write_failure_is_reported(store, monkeypatch):
    def fail_temp_file(*args, **kwargs):
        raise PermissionError("synthetic read-only vault")

    monkeypatch.setattr(markdown_memory.tempfile, "NamedTemporaryFile", fail_temp_file)
    with pytest.raises(VaultError):
        store.create_memory("u", "fact", "Must not be reported as saved")


def test_duplicate_frontmatter_keys_are_not_silently_accepted(store):
    record = store.create_memory("u", "fact", "Synthetic")
    path = store.vault_path / "users" / "u" / "facts" / f"{record.id}.md"
    text = path.read_text(encoding="utf-8").replace(
        "status: active", "status: active\nstatus: archived"
    )
    path.write_text(text, encoding="utf-8")
    assert store.list_memories("u") == []


def test_duplicate_memory_ids_are_skipped_during_listing(store):
    record = store.create_memory("u", "fact", "Synthetic")
    duplicate = (
        store.vault_path
        / "users"
        / "u"
        / "preferences"
        / f"{record.id}.md"
    )
    duplicate.write_bytes(
        (store.vault_path / "users" / "u" / "facts" / f"{record.id}.md").read_bytes()
    )
    assert store.list_memories("u") == []
    assert store.diagnostics == [
        f"Skipped duplicate memory ID {record.id} in files: "
        f"facts/{record.id}.md, preferences/{record.id}.md"
    ]


@pytest.mark.parametrize(
    ("memory_type", "directory"),
    [
        ("preference", "preferences"),
        ("experience", "experiences"),
        ("temporary", "temporary"),
    ],
)
def test_duplicate_ids_are_detected_across_managed_directories(
    store, memory_type, directory
):
    original = store.create_memory("u", "fact", "Synthetic original")
    duplicate = store.create_memory("u", memory_type, "Synthetic duplicate")
    duplicate_path = (
        store.vault_path
        / "users"
        / "u"
        / directory
        / f"{duplicate.id}.md"
    )
    text = duplicate_path.read_text(encoding="utf-8").replace(
        f"id: {duplicate.id}", f"id: {original.id}"
    )
    duplicate_path.unlink()
    duplicate_path.with_name(f"{original.id}.md").write_text(
        text, encoding="utf-8"
    )

    assert store.list_memories("u") == []
    assert len(store.diagnostics) == 1
    assert f"Skipped duplicate memory ID {original.id} in files:" in store.diagnostics[0]
    assert f"facts/{original.id}.md" in store.diagnostics[0]
    assert f"{directory}/{original.id}.md" in store.diagnostics[0]


def test_search_returns_relevant_active_long_term_records_only(store):
    preference = store.create_memory(
        "u", "preference", "The user prefers concise answers", tags=("style",)
    )
    unrelated = store.create_memory("u", "fact", "The user's bicycle is red")
    store.create_memory("u", "temporary", "Concise answers for today")
    archived = store.create_memory(
        "u", "experience", "A concise answer worked well", status="archived"
    )

    found = store.search_memories("u", "please answer with concise answers")

    assert [record.id for record in found] == [preference.id]
    assert unrelated.id not in {record.id for record in found}
    assert archived.id not in {record.id for record in found}


def test_search_respects_result_and_character_limits(store):
    first = store.create_memory("u", "fact", "Python is the user's main language")
    store.create_memory("u", "experience", "User built a Python tool")

    found = store.search_memories(
        "u", "Python", limit=1, max_chars=len(first.content) + 80
    )

    assert len(found) == 1
    assert len(found[0].content) + 80 <= len(first.content) + 80


def test_search_rejects_invalid_limit(store):
    with pytest.raises(ValueError):
        store.search_memories("u", "query", limit=0)
