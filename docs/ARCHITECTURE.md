# Kuro Architecture

Status: proposed target architecture
Scope: incremental refactor of the existing Python application; not a rewrite.

## Goals
- Keep Kuro local-first and usable on a 16 GB GPU while games are running.
- Keep LLM, ASR, TTS, memory, avatar and tools independently replaceable.
- Keep normal conversation fast; tool execution must be explicit and permission-controlled.
- Make each subsystem testable without requiring every local service.
- Preserve the current FastAPI/WebSocket UI, Ollama-compatible API, MySQL persistence and VTube Studio integration during migration.

## Current implementation (observed)
- KuroEngine currently coordinates provider calls, history, memory extraction, mood calculation, summarization and TTS.
- An initial LLM boundary already exists in `server/llm/base.py` and `server/llm/openai_compatible.py`; KuroEngine uses this adapter. This is a first extraction, not yet a fully tested provider lifecycle or capability abstraction.
- LLM streaming is synchronous and is run through an executor by the WebSocket API.
- TTS synthesis/playback is invoked inline while the engine consumes LLM sentences. The TTS manager and engine use thread locks.
- The FastAPI module constructs a global KuroEngine during import.
- Memory is persisted through MySQL; user facts/preferences are currently stored as structured JSON and extracted with LLM/regex helpers.
- The API handles text and push-to-talk audio input and broadcasts mood updates.

These are migration targets, not reasons to discard the existing implementation.

## Target: modular monolith
Keep one deployable Python application initially. Use explicit interfaces and dependency injection before considering separate processes.

~~~mermaid
flowchart TD
    Client[Web UI / Microphone] --> API[FastAPI + WebSocket]
    API --> Conversation[Conversation Orchestrator]
    API --> ASR[ASR Provider]
    ASR --> Conversation
    Conversation --> Context[Prompt + Context Builder]
    Context --> Memory[Memory Service]
    Context --> Agent[Agent Runtime]
    Agent --> LLM[LLM Provider]
    Agent --> Tools[Permissioned Tool Registry]
    Tools --> Desktop[Desktop / Game Adapters]
    Agent --> Events[Conversation Events]
    Events --> UI[WebSocket Event Publisher]
    Events --> Speech[Speech Pipeline]
    Speech --> TTS[TTS Provider]
    Events --> Emotion[Emotion / Avatar Controller]
    Emotion --> VTS[VTube Studio Adapter]
    Memory --> Storage[Storage Interface]
    Storage --> MySQL[(MySQL)]
~~~

## Proposed package boundaries
~~~text
server/
  api/
    app.py                 # FastAPI application factory and lifespan
    routes.py              # HTTP and WebSocket transport
    websocket_events.py    # typed client/server event serialization
  core/
    config.py              # validated application configuration
    events.py              # internal event types
    orchestrator.py        # conversation use-case coordination
    session_manager.py     # session lifecycle and per-session state
  llm/
    base.py                # provider protocol
    openai_compatible.py   # Ollama and compatible endpoints
    options.py             # model settings and capability flags
  agent/
    runtime.py             # bounded tool-call loop
    registry.py            # tool definitions and dispatch
    permissions.py         # allow/deny/confirm policies
  memory/
    service.py             # memory use cases and context assembly
    extraction.py          # structured fact extraction
  storage/
    base.py                # persistence protocol
    mysql.py               # existing MySQL implementation, migrated gradually
  voice/
    asr/
    tts/
      base.py
      manager.py
      queue.py              # bounded speech queue and cancellation
  avatar/
    controller.py          # mood/expression mapping
    vtube_studio.py        # VTube Studio adapter
  tools/
    desktop/
    games/
~~~

The package layout is a target, not a demand to move every file immediately. Existing modules can be wrapped by adapters and moved only when tests protect their behavior.

## Runtime rules
1. The API layer validates input and translates it into application commands. It does not own business logic.
2. The orchestrator owns one conversation turn and coordinates context, agent execution, persistence and output events.
3. The LLM provider returns normalized text/tool-call events. Provider-specific payloads stay inside the provider adapter.
4. The agent runtime may execute only registered tools. Tools have schemas, timeouts, permission levels and audit logs.
5. TTS consumes complete sentence/segment events from a bounded queue. Speech synthesis must not block LLM token consumption. Playback order is preserved; cancellation clears pending segments.
6. ASR and TTS are replaceable providers. CPU/GPU placement and model residency are configuration, not hard-coded assumptions.
7. Each session has isolated conversation state. Shared user memory and provider clients must be concurrency-safe.
8. The avatar receives emotion/control events; it does not depend directly on the LLM implementation.

## Resource profiles
- Companion: small local model, short context, TTS enabled, no continuous vision.
- Gaming: smaller model or unloaded LLM when needed, CPU-first voice services where practical, bounded queues, no continuous screen capture unless explicitly enabled.
- Agent: tool execution enabled only for approved tools; destructive or externally visible actions require confirmation.

The runtime should report actual memory use and service status. It must not promise that a model or TTS backend fits in VRAM based only on parameter count.

## Migration plan
### Phase 0 — Baseline and safety
- Keep work on kuro/test-readiness; do not change main.
- Make tests independent of Ollama, MySQL, audio devices and GPU.
- Add CI and record baseline behavior/latency.
- Validate configuration at startup and expose a safe health/status endpoint.

### Phase 1 — Extract LLM provider
- Introduce an LLM provider protocol and one OpenAI-compatible implementation.
- Move request construction, model options, streaming and response normalization out of KuroEngine.
- Add mocked tests for streaming, errors, options and provider-specific extras.
- Add an opt-in live Ollama smoke test.

### Phase 2 — Separate orchestration and memory
- Move prompt/context construction and session turn handling into an orchestrator.
- Define a storage protocol and wrap the existing MySQL storage.
- Make user memory per-user/session-safe and test persistence with fakes.
- Keep current schema and data; migrations must be explicit.

### Phase 3 — Decouple speech and avatar
- Introduce typed response/sentence/mood events.
- Add a bounded asynchronous TTS queue with ordered playback, cancellation and backpressure.
- Keep existing GPT-SoVITS/F5-TTS behind adapters.
- Add CPU-capable TTS provider only after a standalone benchmark.
- Move VTube Studio commands behind an avatar adapter.

### Phase 4 — Agent and tools
- Add tool schemas, registry, dispatcher, timeout and permission policy.
- Add a bounded tool-call loop with maximum steps and clear error handling.
- Start with harmless read-only tools; require confirmation for file writes, shell commands, purchases, messages and game actions.
- Keep normal companion chat separate from agent mode.

### Phase 5 — Gaming and resource manager
- Add explicit companion/gaming/agent profiles.
- Measure GPU VRAM, system RAM, CPU usage, latency and tokens/sec.
- Add service start/stop and model unload controls where supported.
- Add game adapters individually; no arbitrary unrestricted computer control.

## Testing strategy
- Unit tests: fake LLM, fake storage, fake TTS, fake ASR, fake avatar and fake tools.
- Contract tests: every provider/adapter must satisfy its interface.
- Integration tests: local Ollama and local TTS, explicitly opted in.
- Hardware tests: run manually on the target PC and record GPU/CPU/memory metrics.
- End-to-end tests: text chat first, then voice, then tools, then game integration.

## Non-goals for the first refactor
- No microservices or message broker.
- No replacement of MySQL without measured need.
- No full framework rewrite.
- No unrestricted autonomous shell or desktop control.
- No vector database until current memory retrieval has been measured and shown insufficient.

## Definition of the first usable milestone
A user can start the API without import-time side effects, send a text message, receive a streamed response from a configurable Ollama model, persist the turn, and run the same path in automated tests with fake dependencies. TTS can be disabled without changing the conversation path.
