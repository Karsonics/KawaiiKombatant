import asyncio
from pathlib import Path

from server.api_routes import create_health_handler, register_api_routes


class FakeApp:
    def __init__(self):
        self.mounts = {}
        self.routes = {}

    def mount(self, path, app, name):
        self.mounts[path] = {"app": app, "name": name}

    def get(self, path):
        return self._route_decorator("GET", path)

    def websocket(self, path):
        return self._route_decorator("WEBSOCKET", path)

    def _route_decorator(self, method, path):
        def register(handler):
            self.routes[(method, path)] = handler
            return handler

        return register


class FakeLogger:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def warning(self, message, *args):
        self.warnings.append((message, args))

    def info(self, message, *args):
        self.infos.append((message, args))


class FakeHealthEngine:
    def __init__(self, memory_status, vault_path):
        self.markdown_memory_status = memory_status
        self.markdown_memory = object()
        self.vault_path = vault_path
        self.memory_contents = "private memory text"
        self.tts_enabled = True

    def get_session_count(self):
        return 3


def test_registered_health_endpoint_reports_available_memory_privately(tmp_path):
    vault_path = "C:/private/KuroMemory"
    engine = FakeHealthEngine("available", vault_path)
    health_handler = create_health_handler(lambda: engine)
    app, _, handlers = _register(tmp_path / "missing-web", health_handler)

    assert app.routes[("GET", "/health")] is health_handler
    assert handlers["health"] is health_handler
    payload = asyncio.run(app.routes[("GET", "/health")]())

    assert payload == {
        "status": "ok",
        "sessions": 3,
        "tts_enabled": True,
        "long_term_memory": "available",
    }
    assert vault_path not in repr(payload)
    assert engine.memory_contents not in repr(payload)


def test_registered_health_endpoint_reports_unavailable_memory_privately(tmp_path):
    vault_path = "C:/private/KuroMemory"
    engine = FakeHealthEngine(
        "unavailable: configured vault cannot be initialized", vault_path
    )
    health_handler = create_health_handler(lambda: engine)
    app, _, handlers = _register(tmp_path / "missing-web", health_handler)

    assert app.routes[("GET", "/health")] is health_handler
    assert handlers["health"] is health_handler
    payload = asyncio.run(app.routes[("GET", "/health")]())

    assert payload == {
        "status": "ok",
        "sessions": 3,
        "tts_enabled": True,
        "long_term_memory": "unavailable: configured vault cannot be initialized",
    }
    assert vault_path not in repr(payload)
    assert engine.memory_contents not in repr(payload)


def _register(web_dir: Path, health_handler=None):
    app = FakeApp()
    logger = FakeLogger()
    handlers = {
        "root": object(),
        "settings": object(),
        "health": object(),
        "websocket": object(),
    }
    if health_handler is not None:
        handlers["health"] = health_handler

    register_api_routes(
        app,
        web_dir,
        static_files_factory=lambda **kwargs: kwargs,
        logger=logger,
        root_handler=handlers["root"],
        settings_handler=handlers["settings"],
        health_handler=handlers["health"],
        websocket_handler=handlers["websocket"],
    )
    return app, logger, handlers


def _assert_core_routes(app, handlers):
    assert app.routes[("GET", "/health")] is handlers["health"]
    assert app.routes[("WEBSOCKET", "/ws")] is handlers["websocket"]


def test_routes_register_without_web_directory(tmp_path):
    app, logger, handlers = _register(tmp_path / "web")

    _assert_core_routes(app, handlers)
    assert app.mounts == {}
    assert ("GET", "/") not in app.routes
    assert ("GET", "/settings") not in app.routes
    assert logger.warnings


def test_routes_register_with_web_but_without_models_or_settings(tmp_path):
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("index", encoding="utf-8")

    app, logger, handlers = _register(web_dir)

    _assert_core_routes(app, handlers)
    assert set(app.mounts) == {"/static"}
    assert app.routes[("GET", "/")] is handlers["root"]
    assert ("GET", "/settings") not in app.routes
    assert logger.warnings


def test_routes_register_all_present_web_assets(tmp_path):
    web_dir = tmp_path / "web"
    (web_dir / "models").mkdir(parents=True)
    (web_dir / "index.html").write_text("index", encoding="utf-8")
    (web_dir / "settings.html").write_text("settings", encoding="utf-8")

    app, logger, handlers = _register(web_dir)

    _assert_core_routes(app, handlers)
    assert set(app.mounts) == {"/static", "/models"}
    assert app.routes[("GET", "/")] is handlers["root"]
    assert app.routes[("GET", "/settings")] is handlers["settings"]
    assert logger.warnings == []


def test_routes_skip_assets_with_invalid_file_types(tmp_path):
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "models").write_text("not a directory", encoding="utf-8")
    (web_dir / "index.html").mkdir()
    (web_dir / "settings.html").mkdir()

    app, logger, handlers = _register(web_dir)

    _assert_core_routes(app, handlers)
    assert set(app.mounts) == {"/static"}
    assert ("GET", "/") not in app.routes
    assert ("GET", "/settings") not in app.routes
    assert logger.warnings


def test_web_path_that_is_a_file_is_not_mounted(tmp_path):
    web_path = tmp_path / "web"
    web_path.write_text("not a directory", encoding="utf-8")

    app, logger, handlers = _register(web_path)

    _assert_core_routes(app, handlers)
    assert app.mounts == {}
    assert ("GET", "/") not in app.routes
    assert ("GET", "/settings") not in app.routes
    assert logger.warnings
