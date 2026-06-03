# KawaiiKombatant

An AI-powered character chatbot with TTS (text-to-speech), ASR (speech recognition),
and conversation memory. Powered by Ollama + GPT-SoVITS + MySQL.

## Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Make sure MySQL, Ollama, and GPT-SoVITS API are running

# 3. Run the bot
python bot_main.py
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
| `bot_config.yaml` | LLM model, character prompt, chat settings |
| `sovits_config.yaml` | TTS API endpoint, voice reference audio |
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

### Changing the Voice

Edit `configs/sovits_config.yaml`:

```yaml
reference:
  audio_path: "./assets/voices/your_reference.wav"
  audio_text: "Words spoken in the reference audio"
  language: "en"
```

## Commands

While the bot is running:

- `exit` — Save and exit
- `history` — Show full conversation history
- `memory` — Display stored user memories
- `clear_memory` — Clear all stored user data

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
├── bot_main.py                  # Main entry point
├── configs/                     # YAML configuration files
│   ├── bot_config.yaml
│   ├── sovits_config.yaml
│   ├── database_config.yaml
│   ├── character_config.yaml
│   └── presets/
├── server/process/
│   ├── storage/                 # Database & memory
│   │   ├── mysql_storage.py
│   │   └── user_memory.py
│   ├── tts_func/               # TTS client (GPT-SoVITS)
│   │   └── sovits_amd.py
│   └── asr_func/               # Speech recognition (stub)
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
python -m pytest tests/ -v
# Or individually:
python tests/test_extract_user_info.py
python tests/test_llm.py
```

## License

MIT
