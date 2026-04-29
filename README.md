# KawaiiKombatant

An AI-powered character chatbot with TTS (text-to-speech), ASR (speech recognition), and conversation memory.

## Features

- **LLM Dialogue** - Powered by Ollama (local) or OpenAI-compatible APIs
- **Voice Output** - GPT-SoVITS for text-to-speech
- **Speech Recognition** - Faster-Whisper support
- **Conversation Memory** - MySQL-backed user memory across sessions
- **YAML Config** - Full configuration via config files

## Requirements

- Python 3.10+
- MySQL database
- Ollama (for local LLM)
- GPT-SoVITS API server running on port 9880

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Configure your settings (see Configuration section below)

# Start GPT-SoVITS API (Terminal 1)
conda activate GPTSoVits
cd /path/to/GPT-SoVITS
PYTORCH_ROCM_ARCH=gfx1200 PYTHONPATH=/path/to/GPT-SoVITS python api_v2.py -a 0.0.0.0 -p 9880

# Run the bot (Terminal 2)
cd /path/to/KawaiiKombatant
python aichat_with_full_memory.py
```

## Configuration

All settings are in `configs/`:

| File | Purpose |
|------|---------|
| `bot_config.yaml` | LLM model, character, chat settings |
| `sovits_config.yaml` | TTS voice, speed, audio settings |
| `database_config.yaml` | MySQL connection |
| `character_config.yaml` | Personality presets |

### Changing the Character

Edit `configs/bot_config.yaml`:

```yaml
character:
  name: "YourCharacter"
  personality: "tsundere"  # or any custom personality
  system_prompt: |
    Your custom system prompt here...
```

### Changing the Voice

Edit `configs/sovits_config.yaml`:

```yaml
reference:
  audio_path: "/path/to/your/reference.wav"
  audio_text: "Words spoken in the reference"
  language: "en"
```

## Commands

While running the bot:

- `exit` - Save and exit
- `history` - Show conversation history
- `memory` - Display stored user memories
- `clear_memory` - Clear all user data

## Project Structure

```
KawaiiKombatant/
├── aichat_with_full_memory.py   # Main bot entry
├── configs/                     # Configuration files
│   ├── bot_config.yaml
│   ├── sovits_config.yaml
│   ├── database_config.yaml
│   └── character_config.yaml
├── server/process/
│   ├── storage/                 # Database & memory
│   │   ├── mysql_storage.py
│   │   └── user_memory.py
│   ├── tts_func/               # TTS client
│   │   └── sovits_amd.py
│   └── asr_func/               # Speech recognition
├── utils/                       # Utilities
│   └── session_manager.py
└── tests/                       # Test files
```

## Troubleshooting

**TTS not working**
- Ensure GPT-SoVITS API is running on port 9880
- Check reference audio is 3-10 seconds

**Ollama not connecting**
- Run `ollama serve` in a separate terminal
- Check model name in `bot_config.yaml`

## License

MIT