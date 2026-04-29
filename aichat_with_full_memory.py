from openai import OpenAI
from server.process.storage.mysql_storage import MySQLConversationStorage
from server.process.storage.user_memory import UserMemory
import uuid
from datetime import datetime
import re
import yaml
import os
from server.process.tts_func.sovits_amd import speak, check_api_available


def load_config():
    config_path = os.path.join(
        os.path.dirname(__file__), 
        "configs", "bot_config.yaml"
    )
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


CONFIG = load_config()

# ── Ollama client ────────────────────────────────────────────────────────────────
client = OpenAI(
    base_url=CONFIG['ollama']['base_url'],
    api_key=CONFIG['ollama']['api_key'],
)

MODEL_NAME     = CONFIG['ollama']['model']
CONTEXT_LENGTH = CONFIG['chat']['context_length']

# ── Storage ───────────────────────────────────────────────────────────────────
storage     = MySQLConversationStorage("configs/database_config.yaml")
user_memory = UserMemory(storage, user_id=CONFIG['chat']['user_id'])

if CONFIG.get('tts', {}).get('enabled', True):
    TTS_ENABLED = check_api_available()
else:
    TTS_ENABLED = False

print("✓ TTS ready" if TTS_ENABLED else "⚠ TTS unavailable - text only mode")

# ── Character prompt ──────────────────────────────────────────────────────────
CHARACTER_NAME = CONFIG['character']['name']
BASE_SYSTEM_PROMPT = CONFIG['character']['system_prompt']


# ── Helpers ───────────────────────────────────────────────────────────────────
def strip_think_tags(text: str) -> str:
    """Remove <think>…</think> blocks (safe even if model never emits them)."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def extract_user_info(user_input: str, ai_response: str):
    """Detect and persist user facts from the conversation."""
    user_lower = user_input.lower()

    context_match = re.search(r"from (.+?)(?:\.|$|,)", user_lower)
    extra_context = context_match.group(1) if context_match else None

    # Favourite character
    character_patterns = [
        r"i (?:really )?like (\w+)(?: from (.+?))?(?:\.|$|,)",
        r"i love (\w+)(?: from (.+?))?(?:\.|$|,)",
        r"(\w+) from (.+?) is (?:my favorite|great|amazing|the best)",
        r"favorite (?:character|person) is (\w+)(?: from (.+?))?(?:\.|$|,)",
    ]
    for pattern in character_patterns:
        match = re.search(pattern, user_lower)
        if match:
            name   = match.group(1).capitalize()
            source = (match.group(2).strip() if len(match.groups()) >= 2 and match.group(2)
                      else extra_context or "source unknown")
            user_memory.add_preference("favorite_character", name, f"from {source}")
            print(f"  [💾 Remembered: likes {name} from {source}]")
            break

    # Anime / media
    for pattern in [
        r"(?:watching|reading|love|like) (.+?)(?:anime|manga|series|show)",
        r"have you (?:seen|read|watched) (.+?)\?",
    ]:
        match = re.search(pattern, user_lower)
        if match:
            media      = match.group(1).strip()
            media_type = ("anime" if "anime" in user_lower
                          else "manga" if "manga" in user_lower else "series")
            user_memory.add_fact(f"Interested in {media} ({media_type})")
            print(f"  [💾 Remembered: interested in {media}]")
            break

    # Favourites (color, food, etc.)
    for pattern in [
        r"(?:my )?favorite (?:color|colour) is (\w+)",
        r"(?:my )?favorite food is (.+?)(?:\.|$|,)",
        r"(?:my )?favorite (.+?) is (.+?)(?:\.|$|,)",
    ]:
        match = re.search(pattern, user_lower)
        if match:
            if "color" in user_lower or "colour" in user_lower:
                user_memory.add_preference("favorite_color", match.group(1))
            elif "food" in user_lower:
                user_memory.add_preference("favorite_food", match.group(1))
            elif len(match.groups()) >= 2:
                user_memory.add_preference(f"favorite_{match.group(1)}", match.group(2))
            break

    # Name
    for pattern in [r"(?:my )?name is (\w+)", r"call me (\w+)"]:
        match = re.search(pattern, user_lower)
        if match and "name" in user_lower:
            name = match.group(1).capitalize()
            user_memory.add_personal_info("name", name)
            print(f"  [💾 Remembered: name is {name}]")
            break

    # Hobbies
    for pattern in [
        r"i (?:like to|love to|enjoy) (.+?)(?:\.|$|,)",
        r"my hobby is (.+?)(?:\.|$|,)",
        r"i'm into (.+?)(?:\.|$|,)",
    ]:
        match = re.search(pattern, user_lower)
        if match:
            user_memory.add_fact(f"Enjoys {match.group(1).strip()}")
            break


def list_recent_sessions():
    sessions = storage.get_all_sessions()
    if not sessions:
        return None
    print("\n=== Recent Sessions ===")
    for i, session_id in enumerate(sessions[-5:], 1):
        history = storage.get_conversation_history(session_id, limit=5)
        if history:
            last_msg = next(
                (m for m in reversed(history) if m["role"] != "system"), None
            )
            if last_msg:
                preview   = last_msg["content"][:50] + ("..." if len(last_msg["content"]) > 50 else "")
                role_icon = "👤" if last_msg["role"] == "user" else CHARACTER_NAME[0]
                print(f"{i}. [{session_id[:8]}...] {last_msg['timestamp'].strftime('%m/%d %H:%M')}")
                print(f"   {role_icon} {preview}")
    return sessions[-5:]


def start_or_resume_session():
    recent = list_recent_sessions()
    if recent:
        print("\n0. Start NEW session")
        choice = input("\nResume session (0 for new): ").strip()
        try:
            choice = int(choice)
            if choice == 0:
                return _new_session()
            elif 1 <= choice <= len(recent):
                sid = recent[choice - 1]
                print(f"\n✓ Resuming: {sid[:8]}...")
                return sid
        except ValueError:
            pass
        return _new_session()
    return _new_session()


def _new_session():
    sid            = str(uuid.uuid4())
    memory_context = user_memory.build_context_prompt()
    full_prompt    = BASE_SYSTEM_PROMPT + "\n" + memory_context
    storage.add_message(
        session_id=sid,
        role="system",
        content=full_prompt,
        metadata={"character": CHARACTER_NAME, "personality": CONFIG['character']['personality']},
    )
    storage.update_character_state(
        session_id=sid,
        mood="neutral",
        emotion_level=0.5,
        context_summary="New session",
    )
    print(f"\n✓ New session: {sid[:8]}...")
    return sid


# ── Main loop ─────────────────────────────────────────────────────────────────
user_memory.display_memory()
session_id = start_or_resume_session()

print("\nCommands: 'exit' | 'history' | 'memory' | 'clear_memory'\n")

while True:
    user_input = input("You: ").strip()
    if not user_input:
        continue

    # ── built-in commands ─────────────────────────────────────────────────────
    if user_input.lower() == "exit":
        print(f"Goodbye! 💾 Session saved: {session_id[:8]}...")
        storage.close()
        break

    if user_input.lower() == "history":
        history = storage.get_conversation_history(session_id)
        print("\n--- Conversation History ---")
        for msg in history:
            if msg["role"] != "system":
                print(f"\n{msg['role'].upper()}: {msg['content']}")
        print("\n--- End ---\n")
        continue

    if user_input.lower() == "memory":
        user_memory.display_memory()
        continue

    if user_input.lower() == "clear_memory":
        if input("Clear ALL user memory? (yes/no): ").lower() == "yes":
            user_memory.clear_memory()
            print("✓ Memory cleared")
        continue

    # ── save user message ─────────────────────────────────────────────────────
    storage.add_message(
        session_id=session_id,
        role="user",
        content=user_input,
        metadata={"timestamp": datetime.now().isoformat()},
    )

    # ── build context window ──────────────────────────────────────────────────
    recent  = storage.get_recent_context(session_id, CONTEXT_LENGTH)
    messages = [{"role": m["role"], "content": m["content"]} for m in recent]

    # ── call Ollama ───────────────────────────────────────────────────────────
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
        )
        raw_reply  = response.choices[0].message.content
        kuro_reply = strip_think_tags(raw_reply)   # safe for all models

        print(f"\n{CHARACTER_NAME}: {kuro_reply}\n")

        if TTS_ENABLED:
            speak(kuro_reply)

        extract_user_info(user_input, kuro_reply)

        storage.add_message(
            session_id=session_id,
            role="assistant",
            content=kuro_reply,
            metadata={"model": MODEL_NAME, "timestamp": datetime.now().isoformat()},
        )
        storage.update_character_state(
            session_id=session_id,
            mood="engaged",
            emotion_level=0.7,
            context_summary=f"Topic: {user_input[:50]}...",
        )

    except Exception as e:
        print(f"Error talking to Ollama: {e}")
        print("Make sure Ollama is running:  ollama serve")
        continue