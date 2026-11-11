# Security and privacy

Kuro processes user conversation and voice data through local components and configurable services. Some supported LLM providers can be remote, so “local-first” does not mean all data stays on the machine. This document records repository-visible handling guidance; it is not a complete threat model or a claim that all data paths have been audited.

## Sensitive data

Treat these as sensitive unless the user says otherwise:

- Conversation history and session-keyed summaries stored by the MySQL backend.
- User memory/preferences, including personal information extracted from messages.
- Markdown files in any explicitly selected personal memory vault (the standalone storage component is not yet integrated into runtime).
- Microphone recordings, audio buffers, and transcripts.
- LLM/TTS configuration, endpoint credentials, local machine details, and generated logs.

Use only the data necessary for the task. Do not place real conversations, transcripts, memory, credentials, or personal data in tests, documentation, commits, issue text, or generated reports. Before configuring or using a remote LLM/TTS endpoint, account for the fact that conversation text or audio may be sent to that service; follow the user's authorization and the provider's data handling requirements.

## Credentials and configuration

- Never commit API keys, passwords, tokens, certificates, private URLs, database dumps, or local credentials.
- `.gitignore` excludes `configs/database_config.yaml`, `configs/api_config.yaml`, `.env`, and several credential-like paths. These are local secret-bearing paths; do not add or inspect their contents unless needed and authorized.
- `configs/database_config.yaml` is expected by the MySQL storage, but there is no tracked template. Do not create a credential template with real values or invent secret setup instructions.
- Use the established configuration mechanism or environment variables where supported. Verify how a setting is read before documenting it; do not assume every component supports environment overrides.

## Logs and output

The logger writes to `kawaii.log` with rotation (5 MB, three backups) and to standard output. Runtime exceptions or service responses may include sensitive content even when a logging call appears generic. Avoid logging prompts, model replies, transcripts, user memory, credentials, or full configuration objects. Review command output and logs before sharing them; redact sensitive values.

Session summaries are derived from conversation transcripts and remain sensitive
conversation data. They are stored in MySQL by session ID, included in prompt
context only when that same session is processed, and deleted if the storage
`clear_session` method is used. Do not treat them as user-level profile memory
or copy them into diagnostics.

## Safe development and testing

- The built-in `python -m server.kuro_api` entry point listens on `127.0.0.1` by default. Its `--host` option accepts IP literals only and rejects `localhost` as well as non-loopback addresses. Non-loopback binding is rejected because the API currently has no authentication or authorization. This limits network exposure but does not authenticate local processes or users.
- This enforcement applies only to the built-in startup entry point. Launching the ASGI app through another server command, such as `uvicorn server.kuro_api:app --host 0.0.0.0`, bypasses argument validation and can expose unauthenticated routes. Do not use alternate startup commands with non-loopback bind addresses.
- For remote use, keep the API on loopback and use an SSH port-forward, or a same-machine TLS reverse proxy that authenticates both HTTP and WebSocket routes. A VPN can protect access to that proxy, but a VPN alone cannot reach a loopback-only listener. Direct remote listening requires a separately designed authentication and transport-security change; do not bypass the loopback restriction.
- Inspect scripts before running them. Service checks and benchmarks may contact local/remote systems, use hardware, write files, or reveal secrets. `tests/api_key_test.py` can write `configs/api_config.yaml` and print part of a key; it is not a routine test.
- Do not run destructive database commands, migrations against real user data, or commands that delete user files without explicit approval.
- Treat a Markdown memory vault as sensitive personal data. Choose a private location separate from the engineering repository/documentation vault, restrict filesystem permissions, and protect backups. Never place real memory contents in tests, logs, or reports.
- Automatic Markdown memory capture uses exact quoted message text, a strict candidate schema, a confidence threshold, and local sensitive-data patterns before persistence. The checks are conservative safeguards, not a complete sensitivity classifier; they can miss sensitive content. Disable capture with `memory.capture_enabled: false` when automatic writes are not wanted. Candidate generation uses the configured LLM provider and may send user messages to a remote endpoint.
- The standalone Markdown store requires an explicit path and validates user IDs before constructing paths. It validates frontmatter, skips malformed files without rewriting them, writes through a same-directory temporary file plus atomic replacement, and rejects updates/deletes when the previously read version is stale. These controls do not provide cross-process locking; concurrent Kuro processes sharing a vault are unsupported.
- A failed Markdown write raises an error and must not be described as saved. Automatic capture never updates or deletes existing Markdown files; duplicate or possible conflict candidates are skipped. The backend does not migrate MySQL data.
- Keep validation and authorization controls intact. Treat user text, model responses, and external API responses as untrusted.
- Do not add unrestricted shell execution or arbitrary code execution as an agent feature. Any future tool integration should be narrowly scoped, validated, permission-controlled, time-limited, and auditable.
- Do not weaken authentication or security controls merely to make a test pass.

## Known gaps

- The repository does not document database retention, backup, deletion, or encryption practices. Confirm these with the project owner before making claims or changing them.
- Logging redaction and sensitive-data handling are not defined as a tested contract in this repository.
- The broader Obsidian security/privacy guidance was not accessible during this documentation update and should be manually compared before considering this document complete.
