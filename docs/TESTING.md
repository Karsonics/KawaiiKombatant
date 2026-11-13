# Testing

## Default service-free suite

The Markdown storage unit tests can also be run directly:

```bash
python -m pytest tests/test_markdown_memory.py -q
```

They use pytest temporary directories and synthetic content. They do not need MySQL, Ollama, GPU, audio services, Obsidian, or network access. They cover validation, CRUD/filtering, user isolation, malformed files, version conflicts, and simulated atomic-write failures. The suite does not prove cross-process safety or every operating-system permission behavior.
They also cover UTC expiry cutoff and exact-boundary behavior with an injected
clock, malformed expiry records, explicit clearing of nullable metadata,
confirmation timestamps, stale-version rejection, and preservation after a
simulated failed metadata write.

Memory management and command confirmation use temporary Markdown vaults and fakes:

```bash
python -m pytest tests/test_memory_management.py -q
```

These tests cover status counts, scoped clearing, stable-ID deletion,
confirmation metadata updates, unavailable stores, failures, and confirmation
safeguards.

Automatic capture uses fake extraction responses and a temporary Markdown vault:

```bash
python -m pytest tests/test_markdown_memory_capture.py -q
```

These tests cover extraction-model selection, persistence through the storage
API, exact-quote and confidence checks, sensitive/hypothetical rejection,
duplicate/conflict skipping, and non-fatal capture failures.
The same module also exercises the engine boundary: relevant active memories are
included as reference context, archived or unrelated records are omitted,
validated extraction is persisted with its session ID, and retrieval/capture
failures do not block a chat reply or expose underlying storage errors.
It additionally checks additive preferences, correction/conflict review
candidates, and that a pending correction leaves the original record unchanged.

Consolidation tests use temporary vaults to verify duplicate, related,
potential-conflict, and potential-correction classification. They ensure review
is non-destructive and that pending candidates are reviewable without being
persisted. These deterministic lexical tests do not establish semantic intent
understanding.

Startup-boundary tests use only temporary paths and fakes:

```bash
python -m pytest tests/test_markdown_memory_startup.py -q
```

Session summary tests use in-memory storage/provider fakes and do not connect to
MySQL or an LLM service:

```bash
python -m pytest tests/test_session_summaries.py -q
```

They cover session isolation and resumption, new-session isolation, consecutive
summary checkpoints, older summary context in later cycles, failed summary
preservation, incomplete transcripts, equal-timestamp ordering, and separate
summarization versus extraction model selection. Storage tests verify legacy
plain-text summary compatibility, compare-and-set conflict detection, older
checkpoint rejection, and concurrent initial creation through fakes; no MySQL
service is used. Threaded tests exercise same-session serialization, independent
sessions, and deferral of remaining transcript messages after a simulated
cross-process conflict.

`tests/test_user_memory_persistence.py` uses connection and cursor fakes to
verify that connection/write failures keep changes dirty, successful retries
clear dirty state, and logs omit memory values. An engine test verifies a
failed legacy-memory flush does not interrupt a chat response. These tests do
not connect to MySQL or another external service.

`tests/test_reference_context_budget.py` verifies the combined reference-block
budget, Markdown/summary/legacy priority, truncation markers and determinism,
invalid-setting fallback, preservation of the character prompt and current user
turn, unchanged Markdown retrieval settings, and the actual prompt assembled by
`process_message()`. Delimiter-hardening cases cover Markdown, summary, and
legacy sources, including nested, case-varied, and whitespace-varied tags. The
tested limit includes escaped text, wrappers, labels, and separators; tests use
fake storage and do not access MySQL or a personal vault.

Prompt construction and legacy extraction logging regressions are covered in
`tests/test_markdown_memory_capture.py`: the configured prompt remains present
after transcript-window eviction, persisted system rows are excluded, and
summary/memory data is labeled as untrusted user-role reference context. The
same tests check that extraction logs contain field names/counts but no user
values or raw message text.

The API health tests register the production async health handler with the
route-registration helper and invoke the registered callable using a fake app.
They verify handler behavior, not full FastAPI HTTP or ASGI dispatch.

The repository's standard test command is:

```bash
make test
```

It runs:

```bash
python -m pytest tests/ -x -q --ignore=tests/test_gpu.py --ignore=tests/test_llm.py --ignore=tests/testConnection.py
```

`pytest.ini` sets `tests/` as the test path, selects `test_*.py`, and adds `-ra`. `make test`, the local pytest pre-commit hook, and `.github/workflows/tests.yml` use the same exclusions. CI installs `requirements-test.txt`, uses Python 3.11 on Ubuntu, and runs the command on pushes and pull requests.

The selected tests are intended not to need a live Ollama server, MySQL server, GPU, or physical audio device. Unit tests use fakes/mocks for provider and ASR behavior. This is not full integration coverage: `tests/test_tts.py` and `tests/test_full_pipeline.py` are explicitly skipped placeholders.

## Checks requiring services or hardware

Run these only when their prerequisites and side effects are understood:

- `tests/test_llm.py` contacts Ollama at `http://localhost:11434/v1` and uses the model configured in that test file.
- `tests/test_gpu.py` imports PyTorch and requires a GPU/backend supported by that PyTorch installation for its availability assertion.
- `tests/testConnection.py` attempts MySQL access through `configs/database_config.yaml`. It is also excluded explicitly by the standard command; its filename does not match pytest's `test_*.py` discovery pattern.
- Voice, ASR, and TTS runtime checks may load Whisper/TTS models, use audio hardware, consume substantial resources, or require separately running services. Inspect the specific check before running it.
- `tests/bench_*.py` scripts are benchmarks, not part of the normal pytest suite; they may contact model services, run long workloads, and write result files.
- `tests/api_key_test.py` is a legacy script, not a pytest-discovered test. It can create `configs/api_config.yaml` and print part of an API key. Do not run it as a routine test.

## Other repository checks

- `make lint` runs `python -m ruff check .`.
- `make typecheck` runs `python -m mypy . --ignore-missing-imports --no-strict-optional`.
- `make precommit` runs all configured hooks over all files. The hooks include Black, Ruff with automatic fixes, and the service-free pytest command. Initial hook setup may require network access to fetch hook repositories; Ruff may modify code.

No test or check result should be reported unless that exact command was run. Record skipped checks and the service/device/dependency reason. Prefer the narrowest relevant deterministic tests for a change.
