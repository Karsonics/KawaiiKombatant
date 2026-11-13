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

`llm.summarization_model` is an optional model setting for per-session conversation summaries. It is independent of `llm.extraction_model` when explicitly set. For older configuration files, it defaults to `llm.extraction_model`, then `llm.model`; personal-information extraction continues to use `llm.extraction_model`. Session summaries are stored in MySQL by session ID and are loaded only for that session. Summary failures do not prevent an ordinary conversation turn.

Summary checkpoints are stored with the cumulative summary in the existing
`session_summaries.summary` text field as a versioned JSON envelope. The
checkpoint is the last summarized transcript message ID; older plain-text rows
are read as summaries with checkpoint zero. Once the configured threshold of
new completed user/assistant messages is reached, the summarizer receives the
previous summary and only the new completed segment. Empty output, provider
failure, a failed write, or a compare-and-set conflict leaves the previously
stored summary/checkpoint intact. A process-wide, reference-counted per-session
reentrant lock covers the full `process_message()` turn, including user and
assistant transcript writes and summary processing. Idle locks are removed;
different sessions do not block one another. The built-in API uses one worker
by default. Turn serialization is process-local, so multiple Python processes
must not process the same session concurrently. MySQL summary writes compare
the exact raw TEXT value byte-for-byte, including legacy plain-text rows, and
reject older checkpoints. Missing rows use insert-only initialization; an
existing SQL NULL row is updated only while it remains NULL. Thus summary CAS
protects multi-process writes, but it does not serialize the conversation
turns themselves across processes. A losing writer does not report success;
its transcript exchanges remain beyond the stored checkpoint and can be picked
up by a later summary attempt.
Queries sort by timestamp and then the existing auto-increment message ID, so
equal timestamps have stable chronological order. There is no schema migration.
The model can still omit details when it writes a cumulative summary; checkpoint
tracking prevents repeated transcript slices but cannot guarantee semantic
retention by the model.

`UserMemory.flush()` retains its dirty state unless persistence succeeds. A
failed connection or write returns a safe failure result, logs no memory
values, and leaves the change available for a later flush. The engine continues
without a database using its existing in-memory fallback, but unsaved changes
in that fallback are process-local and are lost on exit. There are no automatic
retry loops or background workers.

### Markdown long-term memory

Set `memory.vault_path` in `configs/bot_config.yaml` to an explicit absolute
path before enabling the Markdown vault. No default or fallback path is used.
At startup Kuro initializes the configured vault and its one
effective `chat.user_id`; if the setting is omitted, the existing
`default_user` fallback is used. Explicitly supplied invalid user IDs fail
configuration validation. If the vault path is absent, blank, relative, or
inaccessible, chat starts in degraded mode and reports that long-term Markdown
memory is unavailable without including the configured path in the status. The
server health response includes `long_term_memory` status.

During each turn the engine retrieves a bounded set of relevant active facts,
preferences, and experiences from the configured user's Markdown files. Matching
is deterministic and local. Retrieval failures do not block chat. When a vault
is available, Kuro also asks `llm.extraction_model` to propose durable memories
from the current user message. The capture layer requires exact quoted content,
strict JSON fields, confidence of at least 0.85, and local sensitive-data checks;
it skips duplicates and possible conflicts and never updates an existing note.
Set `memory.capture_enabled: false` to disable writes while keeping retrieval.
Extraction/storage failures do not block chat. Because candidate generation
uses the configured LLM provider, user messages may be sent to a remote service.
Session summaries and transcripts remain in MySQL, and no legacy MySQL import is
performed. Set `memory.enabled: false` to disable the vault.
`memory.retrieval_limit` defaults to 5 (maximum 20) and
`memory.max_context_chars` defaults to 4000 (maximum 12000). Obsidian is
optional, and the storage component does not support cross-process write
coordination. Never use an engineering documentation directory or a personal
vault containing unrelated notes as the memory path.

`memory.max_reference_context_chars` sets the overall character budget for the
single untrusted reference block sent with a chat request. It defaults to 6000
characters and accepts values from 384 through 20000; missing values use the
default, and invalid values also fall back to it with a content-free warning.
The budget includes the block wrapper, section labels, and separators. The
engine allocates space in this order: relevant Markdown memories, the current
session summary, then legacy `UserMemory`. It keeps complete records/sections
when possible. If content does not fit, the final included section is clipped
at a line or word boundary and receives a visible truncation marker; lower
priority sections may be omitted. Truncation affects only prompt text and never
changes stored memory or summaries. The configured character/personality system
prompt, transcript messages, and their contents are outside this budget. This
is a character count, not a model-token limit. Before budgeting, the engine
escapes case-insensitive `<untrusted_reference>`-like opening and closing tags
in every source, including variants with whitespace around the tag name or
slash. Escaped text remains readable but cannot create another structural
reference boundary; the escaped characters count toward the same total limit.

An `expires` timestamp is an exclusive UTC cutoff: an active memory is eligible
for retrieval only while the current UTC time is earlier than that timestamp.
`expires: null` means no expiration. Expiration only filters retrieval; it does
not delete, archive, or rewrite the Markdown file. Markdown timestamps must be
ISO-8601 UTC (`Z` or a zero UTC offset). Malformed records are skipped and
reported through store diagnostics rather than being treated as unexpired.

`last_confirmed` records the time the user explicitly reviewed and confirmed a
memory. Kuro does not refresh it when a memory is retrieved or merely mentioned.
Use `confirm_memory <id>` in the CLI or WebSocket command and explicitly confirm
the request; this updates the timestamp and epistemic status using the current
record version. Storage callers can clear nullable `source_session`,
`last_confirmed`, or `expires` fields by explicitly passing `None` to
`update_memory()`; omitting those arguments preserves their current values.
Changing a record's lifecycle status through `update_memory()` requires
`confirmed=True`; storage callers should obtain explicit user approval first.

Capture compares new candidates with active records using deterministic lexical
rules. Exact/near duplicates, possible preference conflicts, and explicit
correction candidates are not saved over existing records. `memory_review`
shows relevant stored IDs, candidate text, and an explanation. Correction cues
are heuristic; a statement that looks like a correction is only a
`potential_correction`, while contradictions without clear cue words remain
`potential_conflict`. Additive preferences for a different context can be saved
as separate records. Pending candidates exist only in the running engine and
are replaced by a later capture or lost on restart. Review is manual; it does
not update, archive, supersede, or delete records. Existing clear/delete
commands still require explicit confirmation.

Use the `memory_review` CLI or WebSocket command to scan the configured Markdown
vault for deterministic pairwise candidates: likely duplicates, related topic
records, and potentially conflicting preferences. Expired timestamps, temporal
wording, and archived/superseded statuses can mark a candidate as potentially outdated. This
is a lexical review aid, not semantic truth detection: related records are not
assumed to conflict. It displays the candidate records and explanation without
changing IDs, contents, or lifecycle status. If malformed or duplicate files
are skipped, the response marks the review incomplete. It is separate from
normal retrieval and does not call an LLM.

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
