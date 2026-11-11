"""Tests for the user-facing combined memory command output."""

from types import SimpleNamespace

from server.kuro_engine import KuroEngine


class FakeMarkdownMemory:
    def __init__(self, records=None, error=None):
        self.records = records or []
        self.error = error

    def list_memories(self, user_id, *, active):
        assert user_id == "default_user"
        assert active is True
        if self.error:
            raise self.error
        return self.records


def make_engine(legacy_memory, markdown_memory):
    engine = KuroEngine.__new__(KuroEngine)
    engine.user_id = "default_user"
    engine.user_memory = SimpleNamespace(memory=legacy_memory)
    engine.markdown_memory = markdown_memory
    return engine


def record(memory_type, content, tags=()):
    return SimpleNamespace(type=memory_type, content=content, tags=tags)


def test_memory_command_displays_legacy_and_markdown_sections():
    engine = make_engine(
        {
            "personal_info": {"name": "Kuro"},
            "preferences": {"favorite_color": "blue"},
            "detailed_preferences": {
                "examples": [{"value": "type hints", "context": "Python"}]
            },
            "important_facts": [{"fact": "Uses Python"}],
            "topics_discussed": ["coding"],
        },
        FakeMarkdownMemory(
            [
                record("fact", "I own a bicycle.", ("bicycle",)),
                record("preference", "I prefer type hints.", ("python",)),
                record("experience", "I completed a marathon.", ("running",)),
            ]
        ),
    )

    output = engine.get_formatted_memory()

    assert "Legacy User Memory" in output
    assert "Personal Information" in output and "name: Kuro" in output
    assert "Preferences" in output and "favorite_color: blue" in output
    assert "Detailed Preferences" in output and "type hints - Python" in output
    assert "Important Facts" in output and "Uses Python" in output
    assert "Topics Discussed: coding" in output
    assert "Markdown Long-Term Memory (Obsidian)" in output
    assert "Fact: I own a bicycle. [tags: bicycle]" in output
    assert "Preference: I prefer type hints. [tags: python]" in output
    assert "Experience: I completed a marathon. [tags: running]" in output


def test_memory_command_shows_markdown_when_legacy_memory_is_empty():
    engine = make_engine(
        {}, FakeMarkdownMemory([record("fact", "I own a bicycle.")])
    )

    output = engine.get_formatted_memory()

    assert "No legacy user memory records." in output
    assert "Fact: I own a bicycle." in output


def test_memory_command_shows_markdown_empty_state():
    engine = make_engine({}, FakeMarkdownMemory())

    output = engine.get_formatted_memory()

    assert "No legacy user memory records." in output
    assert "No active Markdown long-term memories." in output


def test_memory_command_reports_unavailable_markdown_store():
    engine = make_engine({}, None)

    output = engine.get_formatted_memory()

    assert "Markdown memory store is not initialized" in output


def test_memory_command_handles_retrieval_error_without_logging_memory_data(caplog):
    secret_content = "Synthetic private memory content"
    engine = make_engine(
        {}, FakeMarkdownMemory(error=RuntimeError(secret_content))
    )

    output = engine.get_formatted_memory()

    assert "Unable to retrieve Markdown long-term memories." in output
    assert "Markdown memory retrieval failed" in caplog.text
    assert secret_content not in caplog.text
