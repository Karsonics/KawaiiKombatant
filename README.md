# KawaiiKombatant

An AI-powered character chatbot with TTS (text-to-speech), ASR (speech recognition),
and conversation memory. Powered by Ollama + GPT-SoVITS + MySQL.

## Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Make sure MySQL, Ollama, and GPT-SoVITS API are running

# 3. Run the bot (standalone mode)
python bot_main.py

# Common flags:
python bot_main.py --dry-run       # Validate config, skip LLM/DB
python bot_main.py --voice kuro    # Select TTS voice preset
python bot_main.py --verbose       # Debug-level console logs
python bot_main.py --log-json      # JSON-formatted logs

# Or run as a WebSocket server (for multi-client setups)
python -m server.kuro_api
# Then connect with the CLI client:
python bot_main.py --ws
```

## Requirements

- **Python** 3.10+
- **MySQL** 8.x (for conversation & memory persistence)
- **Ollama** with a model pulled (default: `qwen2.5:14b`)
- **GPT-SoVITS API** server (optional, for TTS)

### Arch Linux

```bash
# Core dependencies
sudo pacman -S python python-pip mysql ollama

# ROCm GPU acceleration (if using AMD GPU)
sudo pacman -S python-pytorch-rocm ollama-rocm

# Audio libraries
sudo pacman -S portaudio pulseaudio-alsa  # or pipewire-pulse
```

## Configuration

All settings live in `configs/`:

| File | Purpose |
|------|---------|
| `bot_config.yaml` | LLM provider/model, character prompt, chat settings |
| `sovits_config.yaml` | TTS API endpoint, voice reference audio, emotion maps |
| `database_config.yaml` | MySQL connection (gitignored — contains credentials) |
| `character_config.yaml` | Personality presets (tsundere, kuudere, default) |
| `presets/` | Detailed personality profile files |

### Changing the Character

Edit `configs/bot_config.yaml`:

```yaml
character:
  name: "YourCharacter"
  personality: "tsundere"
  system_prompt: |
    Your custom system prompt here...
```

### LLM Backends

The `llm:` section in `bot_config.yaml` supports multiple providers:

```yaml
llm:
  provider: "ollama"          # "ollama" | "openai" | "openrouter" | "custom"
  base_url: "http://localhost:11434/v1"   # per-provider defaults exist
  api_key: "ollama"                       # or your API key
  model: "qwen2.5:14b"
  extraction_model: "qwen2.5:0.5b"        # smaller model for entity extraction
  extraction_enabled: true                # set false to use regex-only
```

The old `ollama:` config key is still supported as a fallback.

### Conversation Summarization

When a session exceeds `summarization_threshold` messages, Kuro automatically
summarizes older messages using the LLM and injects the summary as context.
This effectively doubles the context window without losing history.

```yaml
chat:
  context_length: 15
  summarization_threshold: 20   # summarize when ≥20 messages exist
```

Set to `0` to disable summarization.

### Character Mood Persistence

Kuro's mood and emotion level are saved to the `user_preferences` table after
every message. When a new session starts, the last known mood is restored
instead of resetting to neutral. This means Kuro remembers how your last
conversation ended.

### Changing the Voice

Edit `configs/sovits_config.yaml`:

```yaml
reference:
  audio_path: "./assets/voices/your_reference.wav"
  audio_text: "Words spoken in the reference audio"
  language: "en"
```

Voice presets can be defined and selected at startup:

```yaml
voices:
  kuro:
    audio_path: "./assets/voices/kuro.wav"
    audio_text: "Kuro's reference"
    language: "ja"
  megumi:
    audio_path: "./assets/voices/megumi.wav"
    audio_text: "Megumi's reference"
    language: "en"

output:
  device: null   # null = default device, or an integer device ID
```

Select a voice at startup:
```bash
python bot_main.py --voice kuro
python bot_main.py --voice /path/to/custom.wav   # direct path also works
```

Audio output device: set `output.device` to an integer device ID. List
available devices:
```bash
python -c "import sounddevice; print(sounddevice.query_devices())"
```

### Emotional TTS

Kuro's mood (happy, annoyed, curious, etc.) is passed to the TTS engine.
Configure mood-to-emotion-tag mappings in `sovits_config.yaml`:

```yaml
emotion:
  mood_map:
    happy: "[HAPPY]"
    annoyed: "[ANGRY]"
    sad: "[SAD]"
    excited: "[EXCITED]"
```

The tag is prepended to the TTS text so supported backends can adjust their
output. Unsupported backends (like GPT-SoVITS) log the mood and ignore it.

## Commands

While the bot is running:

- `exit` — Save and exit
- `history` — Show full conversation history
- `memory` — Display stored user memories
- `clear_memory` — Clear all stored user data

## Server Mode

Run the Kuro Engine as a WebSocket server that multiple clients can connect to:

```bash
# Start the server (default port: 8765)
python -m server.kuro_api

# With custom port
python -m server.kuro_api --port 9000

# Connect the CLI client
python bot_main.py --ws
python bot_main.py --ws --port 9000
```

### WebSocket Protocol

Clients send JSON messages to `ws://host:8765/ws`:

| Type | Direction | Example |
|------|-----------|---------|
| `message` | Client → Server | `{"type": "message", "data": "hello", "session_id": null}` |
| `command` | Client → Server | `{"type": "command", "command": "memory"}` |
| `done` | Server → Client | `{"type": "done", "data": {"text": "Hi!", "mood": "happy", "emotion": 0.7, "session_id": "..."}}` |
| `error` | Server → Client | `{"type": "error", "data": "Ollama unreachable"}` |
| `command_result` | Server → Client | `{"type": "command_result", "command": "history", "data": "..."}` |

Available commands: `history`, `memory`, `clear_memory`, `new_session`, `sessions`

## Avatar (VTube Studio)

Drive a Live2D avatar with Kuro's mood and emotions. Works with any VTube Studio-compatible model.

### Setup

1. **Install VTube Studio** on [Steam](https://store.steampowered.com/app/1325860/VTube_Studio/) (free)
2. **Load a Live2D model** in VTS (see model sources below)
3. **Enable the VTS API**: Settings → API → check "Start API"
4. **Define expression hotkeys** in VTS for each mood:
   - `Default`, `Happy`, `Annoyed`, `Curious`, `Excited`
   - Hotkeys → Add → name must match `configs/vtube_config.yaml`
5. **Run the avatar client** alongside KuroAPI:

```bash
# Terminal 1: KuroAPI
python -m server.kuro_api

# Terminal 2: Avatar client
python -m avatar.avatar_client

# Or via bot_main.py
python bot_main.py --avatar
```

### Config

Edit `configs/vtube_config.yaml`:

```yaml
# Mood → hotkey mapping (match what you defined in VTS)
expressions:
  neutral:  "Default"
  happy:    "Happy"
  annoyed:  "Annoyed"
  curious:  "Curious"
  excited:  "Excited"

# Auto-load a model by ID (find with --list-models)
model_id: ""

# Idle animations trigger periodically when Kuro is quiet
idle:
  interval_seconds: 20
  animations:
    - "IdleBlink"
    - "IdleLookAround"
```

### Finding model IDs

```bash
python -m avatar.avatar_client --list-models
```

### Env overrides

```bash
VTS_HOST=192.168.1.5 KAPI_PORT=9000 python -m avatar.avatar_client
```

### Free Live2D Models

| Model | Source | Notes |
|-------|--------|-------|
| Hiyori Momose | [live2d.com/en/learn/sample/](https://www.live2d.com/en/learn/sample/) | Official sample, Neuro-sama's original |
| Lisette | [shiralive2d.com](https://shiralive2d.com/live2d-sample-models/) | 3 expressions, VTS ready |
| Cat VTuber | [chycero.gumroad.com/l/freevt](https://chycero.gumroad.com/l/freevt) | Simple cat girl, 1.27 MB |
| 模之屋 | [aplaybox.com](https://www.aplaybox.com/) | Large free library |

## Voice Pipeline

Hold the spacebar to talk to Kuro hands-free:

```bash
# Start the server first
python -m server.kuro_api

# Voice client (push-to-talk, spacebar to record)
python bot_main.py --voice

# With options
python bot_main.py --voice --model tiny          # faster, less accurate
python bot_main.py --voice --model medium        # slower, more accurate
python bot_main.py --voice --host 192.168.1.5    # remote server
```

If `pynput` is not available (e.g., Wayland), the client falls back to CLI mode:
press Enter to start recording, Enter again to stop.

The voice pipeline runs entirely on your machine:
```
Mic → VAD (Silero, CPU) → ASR (Whisper, GPU) → text → KuroAPI → LLM → TTS
```

### Standalone voice client

```bash
python -m voice.voice_client --help
python -m voice.voice_client --model tiny
```

## Session Manager

The session manager utility lets you list, view, export, import, and clear sessions:

```bash
# List all sessions
python -m utils.session_manager list

# View a session
python -m utils.session_manager view -s <session-id>

# Export to text
python -m utils.session_manager export -s <session-id> -o chat.txt

# Export to JSON (re-importable)
python -m utils.session_manager export -s <session-id> -o chat.json -f json

# Import a JSON export as a new session
python -m utils.session_manager import -o chat.json

# Clear a session
python -m utils.session_manager clear -s <session-id>
```

## Project Structure

```
KawaiiKombatant/
├── bot_main.py                  # CLI frontend (direct or --ws mode)
├── configs/                     # YAML configuration files
│   ├── bot_config.yaml
│   ├── sovits_config.yaml
│   ├── database_config.yaml
│   ├── character_config.yaml
│   └── presets/
├── server/
│   ├── __init__.py
│   ├── kuro_engine.py           # Core engine (LLM, memory, TTS)
│   ├── kuro_api.py              # FastAPI + WebSocket server
│   └── process/
│       ├── storage/             # Database & memory
│       │   ├── mysql_storage.py
│       │   └── user_memory.py
│       ├── tts_func/            # TTS client (GPT-SoVITS)
│       │   └── sovits_amd.py
│       └── asr_func/            # Speech recognition (stub)
├── avatar/                      # VTube Studio avatar integration
│   ├── __init__.py
│   ├── emotion_map.py           # Mood → expression config loader
│   ├── vtube_controller.py      # VTS WebSocket handler
│   └── avatar_client.py         # Main avatar client
├── voice/                       # Voice pipeline
│   ├── __init__.py
│   ├── mic.py                   # Mic capture
│   ├── vad.py                   # Voice activity detection
│   ├── asr.py                   # Speech recognition
│   └── voice_client.py          # Push-to-talk client
├── utils/                       # Utilities
│   ├── __init__.py
│   ├── logging.py              # Structured logging
│   ├── retry.py                # Retry decorator
│   └── session_manager.py      # CLI session management
├── tests/                       # Test files
│   ├── test_llm.py
│   ├── test_extract_user_info.py
│   ├── test_gpu.py
│   └── ...
└── assets/voices/               # TTS reference audio
```

## Troubleshooting

**TTS not working**
- Ensure GPT-SoVITS API is running: `python api_v2.py -a 0.0.0.0 -p 9880`
- Check reference audio exists at the path in `sovits_config.yaml`
- Reference audio should be 3–10 seconds of clean speech

**Ollama not connecting**
- Run `ollama serve` in a separate terminal
- Verify model is pulled: `ollama list`
- Check model name in `bot_config.yaml` matches `ollama list`

**Database errors**
- Ensure MySQL is running: `systemctl status mysql`
- Verify credentials in `database_config.yaml` (this file is gitignored)
- Run `python tests/testConnection.py` to test connectivity

**Logs**
- A `kawaii.log` file is created in the project root with debug-level logs
- Pass `--verbose` or `-v` for verbose console output

## Running Tests

```bash
# Full suite (skipping GPU/LLM tests that need external services)
make test
# or
python -m pytest tests/ -v --ignore=tests/test_gpu.py --ignore=tests/test_llm.py

# Individual test files
python tests/test_extract_user_info.py
python tests/test_think_tags.py
```

## Development

### Makefile

```bash
make run         # Start the bot
make test        # Run tests
make lint        # Run ruff linter
make typecheck   # Run mypy type checker
make precommit   # Run all pre-commit hooks
make clean       # Remove __pycache__ and logs
```

### pre-commit Hooks

The project includes a `.pre-commit-config.yaml` that runs on every commit:

- **black** — auto-formats Python code
- **ruff** — lints for common issues
- **pytest** — runs the test suite

Install the hooks:

```bash
pip install pre-commit
pre-commit install
```

### Logging

- `kawaii.log` — rotating log file (5 MB max, 3 backups)
- `--verbose` / `-v` — debug-level console output
- `--log-json` — JSON-formatted logs instead of plaintext

### Dry-Run Mode

```bash
python bot_main.py --dry-run
```

Validates config files, runs entity extraction, and prints mock responses
without connecting to LLM or database. Useful for testing regex patterns and
config changes.

## License

MIT
