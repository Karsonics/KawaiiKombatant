# Architecture

Status: current implementation plus explicitly labeled design direction. Verify behavior in code before relying on this note.

## Current implementation

Kuro is a single Python application with CLI, API, voice, storage, and avatar modules:

- `bot_main.py` selects direct CLI, WebSocket CLI, voice client, or avatar client modes.
- `server/kuro_api.py` serves the web UI and FastAPI/WebSocket endpoints. It constructs a module-level `KuroEngine` during import. Synchronous engine work is run through an executor in the WebSocket path.
- `server/kuro_engine.py` coordinates LLM generation, conversation history, memory extraction, mood calculation, summarization, and TTS. The engine streams text from the provider but invokes TTS inline as sentences are processed.
- `server/llm/base.py` defines a small provider protocol; `openai_compatible.py` adapts OpenAI-compatible chat completions for configured providers such as Ollama, OpenAI, OpenRouter, and custom endpoints. Provider streaming yields text fragments.
- `server/process/storage/mysql_storage.py` stores conversations, session state, and user preference JSON in MySQL. The engine falls back to in-memory/no-persistence behavior when database initialization fails.
- `server/process/storage/markdown_memory.py` provides an independent Markdown memory storage component with per-user files, validated YAML frontmatter, and CRUD/filter operations. It is not connected to `KuroEngine`; the current runtime still uses MySQL-backed `UserMemory`.
- `server/process/tts_func/` contains a manager and GPT-SoVITS/F5-TTS implementations. F5-TTS may be unavailable depending on its separate service/dependencies. `voice/` and `server/process/asr_func/` implement microphone, VAD, Whisper transcription, and voice-client paths.
- `avatar/` connects to VTube Studio and maps mood updates to configured hotkeys/idle actions.

The code has some interfaces and adapters, but orchestration and several subsystem concerns remain in `KuroEngine`. Importing the API module has initialization side effects. The existing arrangement should not be mistaken for a fully decoupled architecture.

## Runtime and persistence notes

- LLM configuration is read from `configs/bot_config.yaml`; a legacy `ollama` section is supported by the engine. The provider adapter uses the OpenAI SDK protocol.
- MySQL credentials/settings are expected in the ignored local file `configs/database_config.yaml`; no tracked template for it is present. Without usable database access, the engine logs a warning and continues without persistence.
- ASR settings are in `configs/asr_config.yaml`; VTube Studio settings are in `configs/vtube_config.yaml`. TTS provider routing is configured in `bot_config.yaml`; GPT-SoVITS details and voice references are in `sovits_config.yaml`.
- WebSocket and CLI behavior, message formats, and configuration are observable contracts. Inspect callers and tests before changing them.

## Standalone Markdown memory storage — implemented, not integrated

- The standalone `MarkdownMemoryStore` requires an explicit vault path and can initialize `README.md` plus `users/<user_id>/` directories containing `profile.md`, `facts/`, `preferences/`, `experiences/`, and `temporary/`.
- Each memory is an individual Markdown file with validated YAML frontmatter. The storage API supports create, get, list/filter, update, and delete; list skips malformed files and all parseable records sharing a duplicate ID anywhere in the user's memory directories, with filename-only diagnostics.
- Markdown files are the source of truth for records managed by this standalone component. MySQL remains the current live runtime's store for transcripts, session state, and the existing JSON-backed `UserMemory` until integration and migration are separately approved.
- Writes use a same-directory temporary file, flush and `fsync`, then `os.replace`. Updates and deletes require the version token returned by a read, so stale edits are rejected. A process-local lock serializes writes in one process; cross-process locking is not implemented or guaranteed.
- This component does not extract memories, expire them, migrate MySQL data, or affect conversation summaries. The live application continues to use `UserMemory` and MySQL until a separately approved integration and migration.
- Current-state caveat: `KuroEngine._maybe_summarize` still writes a session-triggered summary into the MySQL-backed global `UserMemory.conversation_summary`. The intended separation of summaries from long-term personal memory is not yet implemented in the live runtime.

## Proposed design direction — not implemented contracts

These are design ideas for future, separately scoped work; they are not current capabilities or requirements to refactor immediately:

- Integrate the Markdown memory store with runtime only through a separately approved migration and integration plan; keep session summaries and operational state in MySQL.
- Gradually separate API transport, conversation orchestration/context, storage, memory, speech events, and avatar control behind explicit interfaces.
- Consider an application factory/lifespan so API initialization does not occur at import time.
- Consider decoupling synthesis/playback from LLM token processing with a bounded asynchronous TTS queue, preserving order and defining cancellation/backpressure behavior. There is no such queue currently.
- If agent tools are introduced, use a bounded dispatcher with narrow schemas, permissions, timeouts, and audit behavior; no agent tool runtime is currently present.
- Evaluate session isolation and shared state concurrency as behavior evolves. Do not assume the current global API engine or user memory is session-isolated.
- Measure actual CPU, RAM, GPU/VRAM, and latency before adding model/resource management behavior.

Any proposal that changes public behavior, persistence/schema, deployment, or subsystem boundaries should state its evidence and trade-offs before implementation.

## Unresolved questions

- What is the supported database bootstrap/configuration procedure? The credential-bearing config is ignored and no template is tracked.
- Which TTS and ASR service versions and platform combinations are supported? Requirements list general packages, while some backend runtimes/services are external or optional.
- What API/WebSocket compatibility guarantees and session-isolation guarantees are intended beyond current observed behavior?
- Which items in the separate Obsidian knowledge base represent accepted decisions versus exploratory plans? Obsidian was not accessible during this repository documentation update.
