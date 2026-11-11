# Development

This note describes the tracked development setup. It does not replace the separate Obsidian knowledge base. Verify platform-specific requirements against the selected audio/LLM/TTS backends; the repository does not provide a complete platform matrix.

## Runtime and dependencies

- The README specifies Python 3.10 or later. CI currently uses Python 3.11 on Ubuntu.
- `requirements.txt` lists the application packages. `requirements-test.txt` is a smaller dependency list intended for the service-free tests.
- The app can use an OpenAI-compatible LLM endpoint; the checked-in `configs/bot_config.yaml` selects Ollama by default. TTS and voice paths may additionally depend on local or remote services, audio libraries/devices, and model availability.

Install from the repository root using the documented command:

```bash
python -m pip install -r requirements.txt
```

## Configuration

Tracked YAML settings are in `configs/`:

- `bot_config.yaml`: LLM endpoint/model, chat settings, character prompt, and TTS selection/routing.
- `sovits_config.yaml`: GPT-SoVITS endpoint, reference audio, voice presets, and F5-TTS endpoint settings.
- `asr_config.yaml`: Whisper model size/device/language and audio sample format.
- `vtube_config.yaml`: VTube Studio and Kuro API endpoints, expression hotkeys, model ID, and idle actions.
- `character_config.yaml` and `presets/`: character configuration/preset data.

The engine expects local MySQL settings at `configs/database_config.yaml`; this path is Git-ignored and the repository has no tracked sample/template. Configure it only from a trusted source. The exact database bootstrap and credential setup need project-owner documentation. If the database is unavailable, the engine currently logs a warning and continues without persistence.

### Standalone Markdown memory store

`server.process.storage.markdown_memory.MarkdownMemoryStore` is currently a standalone storage component, not used by the live chat engine. It has no implicit default vault path or repository configuration setting. Pass an explicit path outside the engineering repository, then call `initialize_vault()`; call `initialize_user(user_id)` to create that user's folders. Obsidian is optional: the files are ordinary Markdown and can be inspected or edited with any text editor. Kuro runtime memory remains in MySQL until a separately approved integration/migration.

Example against a deliberate development-only path:

```python
from server.process.storage.markdown_memory import MarkdownMemoryStore

store = MarkdownMemoryStore(r"D:\KuroMemory-dev")
store.initialize_vault()
store.initialize_user("default_user")
```

Choose and protect the path yourself; do not point it at `docs/` or a personal vault containing unrelated notes. The component does not support cross-process write coordination.

Do not put secrets into tracked files. Inspect `.gitignore` before adding local configuration, and do not share configuration contents or logs without checking them for credentials and user data.

## Running locally

From the repository root:

```bash
python bot_main.py
python bot_main.py --dry-run
python -m server.kuro_api
```

The built-in `python -m server.kuro_api` entry point binds to `127.0.0.1` by default. Its `--host` option accepts IP literals only, and only loopback addresses such as `127.0.0.1` or `::1`; the hostname `localhost` is rejected. The existing `--port` option remains available, for example `python -m server.kuro_api --host ::1 --port 8765`.

This loopback validation applies to the built-in startup entry point. Launching the ASGI app through another server command, such as `uvicorn server.kuro_api:app --host 0.0.0.0`, bypasses its argument validation and can expose unauthenticated routes. Do not use an alternate server command with a non-loopback bind address.

`KAPI_HOST` and `KAPI_PORT` configure the avatar client's API destination; they do not configure the server listener. The API server's `--host` and `--port` options control its listener.

The server exposes a WebSocket endpoint and serves the web UI when its files are present. For voice, avatar, and TTS usage, consult the relevant code/configuration and the README, then verify that the required audio device, local application, model, or service is available. Do not assume those optional services are running merely because the Python application starts.

For access from another device, keep Kuro bound to loopback and use an SSH port-forward, or a reverse proxy on the same machine that terminates TLS and authenticates HTTP and WebSocket connections. A VPN can protect access to that proxy, but a VPN alone cannot reach a service bound only to loopback. Do not expose the unauthenticated API by binding it to a LAN or wildcard address.

`--dry-run` is implemented to skip LLM and database initialization. It does not validate live service connectivity or prove the complete conversation pipeline works.

## Developer checks

- `make test`: service-free pytest subset; details and exclusions are in `TESTING.md`.
- `make lint`: Ruff check.
- `make typecheck`: mypy with repository options.
- `make precommit`: Black, Ruff (configured with `--fix`), and pytest hooks across files. Ruff can edit files; review the worktree before and after.

The Makefile also has `make run` and `make clean`. Inspect a command's effects before running it, especially cleanup or hooks that can modify files. Do not run cleanup against user data.

## Known setup gaps

- No database config template is tracked, so the exact safe MySQL bootstrap procedure is unresolved here.
- Optional ASR/TTS backends and VTube Studio require extra services, devices, models, or application-side setup; their supported versions/platform combinations are not fully specified in repository docs.
- README commands and descriptions may be stale. Confirm paths, options, and behavior in source before relying on them.
