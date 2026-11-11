from pathlib import Path

import pytest

from server import kuro_engine
from server.process.storage.markdown_memory import MarkdownMemoryStore, VaultError
from server.process.storage.markdown_memory_startup import (
    initialize_markdown_memory,
    validate_user_id,
)


def config(vault_path=None, user_id="default_user"):
    memory = {} if vault_path is None else {"vault_path": str(vault_path)}
    return {"memory": memory, "chat": {"user_id": user_id}}


@pytest.mark.parametrize("user_id", ["default_user", "a-1", "U_2"])
def test_configured_single_user_id_accepts_store_identifier_contract(user_id):
    assert validate_user_id(user_id)


@pytest.mark.parametrize("user_id", [None, "", "../other", "has space", "x" * 65])
def test_configured_single_user_id_rejects_invalid_values(user_id):
    assert not validate_user_id(user_id)


def test_config_validation_rejects_invalid_configured_user_id(monkeypatch):
    monkeypatch.setattr(kuro_engine.os.path, "exists", lambda _path: False)
    errors, _ = kuro_engine._validate_configs(
        {
            "llm": {"provider": "ollama", "model": "local"},
            "chat": {"user_id": "../other"},
            "character": {"system_prompt": "prompt"},
        }
    )
    assert any("chat.user_id" in error for error in errors)


def test_missing_blank_and_relative_paths_degrade_without_constructing_store(tmp_path):
    class ForbiddenFactory:
        def __init__(self, _path):
            pytest.fail("invalid configuration must not construct a store")

    for cfg in (config(), config("  "), config("relative/vault")):
        result = initialize_markdown_memory(cfg, store_factory=ForbiddenFactory)
        assert result.store is None
        assert not result.available
        assert "unavailable" in result.status
    assert list(tmp_path.iterdir()) == []


def test_valid_vault_initializes_only_the_explicit_path(tmp_path):
    vault = tmp_path / "chosen-vault"
    result = initialize_markdown_memory(
        config(vault), store_factory=MarkdownMemoryStore
    )
    assert result.available
    assert result.store is not None
    assert (vault / "README.md").is_file()
    assert (vault / "users" / "default_user" / "profile.md").is_file()


def test_unavailable_vault_degrades_without_exposing_path(tmp_path):
    blocked = tmp_path / "file-not-directory"
    blocked.write_text("synthetic", encoding="utf-8")
    result = initialize_markdown_memory(config(blocked))
    assert result.store is None
    assert not result.available
    assert str(tmp_path) not in result.status


def test_expanduser_failure_degrades_without_exposing_path(tmp_path, monkeypatch):
    vault_path = str(tmp_path / "private-vault")

    def fail_expanduser(_path):
        raise RuntimeError(f"could not resolve {vault_path}")

    monkeypatch.setattr(Path, "expanduser", fail_expanduser)
    result = initialize_markdown_memory(config(vault_path))

    assert result.store is None
    assert not result.available
    assert result.status == "unavailable: configured vault cannot be initialized"
    assert vault_path not in result.status


def test_user_directory_symlink_outside_vault_degrades_without_external_writes(
    tmp_path,
):
    vault = tmp_path / "vault"
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    users = vault / "users"
    users.mkdir(parents=True)
    user_link = users / "default_user"
    try:
        user_link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink creation unavailable: {exc}")

    result = initialize_markdown_memory(config(vault))

    assert result.store is None
    assert not result.available
    assert result.status == "unavailable: configured vault cannot be initialized"
    assert list(outside.iterdir()) == [marker]
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_store_initialization_failure_degrades_with_fake(tmp_path):
    class FailingStore:
        def __init__(self, _path):
            pass

        def initialize_vault(self):
            raise VaultError("private path omitted")

    result = initialize_markdown_memory(
        config(tmp_path / "vault"), store_factory=FailingStore
    )
    assert result.store is None
    assert not result.available
    assert "private path omitted" not in result.status


def test_engine_starts_when_markdown_memory_is_degraded(tmp_path, monkeypatch):
    config_path = tmp_path / "bot.yaml"
    config_path.write_text(
        """llm:\n  provider: ollama\n  model: local\nchat: {}\ncharacter:\n  name: Kuro\n  personality: calm\n  system_prompt: prompt\ntts:\n  enabled: false\nmemory:\n  vault_path: ''\n""",
        encoding="utf-8",
    )
    monkeypatch.setattr(kuro_engine.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(
        kuro_engine,
        "initialize_markdown_memory",
        lambda _config, **_kwargs: type(
            "Result",
            (),
            {
                "store": None,
                "available": False,
                "status": "unavailable: vault path is not configured",
            },
        )(),
    )
    class FakeProvider:
        def close(self):
            pass

    monkeypatch.setattr(
        kuro_engine, "OpenAICompatibleProvider", lambda **_kwargs: FakeProvider()
    )

    class NullStorage:
        def __init__(self, *_args, **_kwargs):
            raise OSError("no database in unit test")

    monkeypatch.setattr(kuro_engine, "MySQLConversationStorage", NullStorage)
    engine = kuro_engine.KuroEngine(config_path=str(config_path))
    assert engine.markdown_memory is None
    assert engine.markdown_memory_status.startswith("unavailable")
    assert engine.user_id == "default_user"
    engine.close()


def test_engine_uses_effective_default_user_for_markdown_memory(tmp_path, monkeypatch):
    vault = tmp_path / "explicit-vault"
    config_path = tmp_path / "bot.yaml"
    config_path.write_text(
        f"""llm:\n  provider: ollama\n  model: local\nchat: {{}}\ncharacter:\n  name: Kuro\n  personality: calm\n  system_prompt: prompt\ntts:\n  enabled: false\nmemory:\n  vault_path: {vault.as_posix()}\n""",
        encoding="utf-8",
    )
    monkeypatch.setattr(kuro_engine.os.path, "exists", lambda _path: False)

    class FakeProvider:
        def close(self):
            pass

    monkeypatch.setattr(
        kuro_engine, "OpenAICompatibleProvider", lambda **_kwargs: FakeProvider()
    )

    class NullStorage:
        def __init__(self, *_args, **_kwargs):
            raise OSError("no database in unit test")

    monkeypatch.setattr(kuro_engine, "MySQLConversationStorage", NullStorage)
    engine = kuro_engine.KuroEngine(config_path=str(config_path))

    assert engine.user_id == "default_user"
    assert engine.markdown_memory is not None
    assert engine.markdown_memory.vault_path == vault
    assert (vault / "users" / "default_user" / "profile.md").is_file()
    assert "user_id" not in engine.config["chat"]
    engine.close()
