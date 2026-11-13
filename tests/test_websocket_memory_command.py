"""Service-free coverage for WebSocket memory command serialization."""

import importlib

from fastapi.testclient import TestClient

from server.kuro_engine import KuroEngine


def test_memory_status_command_is_dispatched_and_serialized(monkeypatch):
    # Importing the API module constructs its production engine. Keep that
    # import side effect inert so this test never initializes external services.
    monkeypatch.setattr(KuroEngine, "__init__", lambda self: None)
    api = importlib.import_module("server.kuro_api")

    class FakeEngine:
        def close(self):
            pass

        def get_memory_status(self):
            return {
                "legacy": {"available": True, "total": 2},
                "markdown": {"available": True, "total": 3},
            }

    monkeypatch.setattr(api, "engine", FakeEngine())

    with TestClient(api.app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "command", "command": "memory_status"})
            result = websocket.receive_json()

    assert result == {
        "type": "command_result",
        "command": "memory_status",
        "data": {
            "legacy": {"available": True, "total": 2},
            "markdown": {"available": True, "total": 3},
        },
    }
