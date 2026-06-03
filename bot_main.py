from openai import OpenAI
from server.process.storage.mysql_storage import MySQLConversationStorage
from server.process.storage.user_memory import UserMemory
from utils.logging import logger
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

client = OpenAI(
    base_url=CONFIG['ollama']['base_url'],
    api_key=CONFIG['ollama']['api_key'],
)

MODEL_NAME     = CONFIG['ollama']['model']
CONTEXT_LENGTH = CONFIG['chat']['context_length']

storage     = MySQLConversationStorage("configs/database_config.yaml")
user_memory = UserMemory(storage, user_id=CONFIG['chat']['user_id'])

if CONFIG.get('tts', {}).get('enabled', True):
    TTS_ENABLED = check_api_available()
else:
    TTS_ENABLED = False

logger.info("TTS ready" if TTS_ENABLED else "TTS unavailable - text only mode")

CHARACTER_NAME = CONFIG['character']['name']
BASE_SYSTEM_PROMPT = CONFIG['character']['system_prompt']

CONTEXT_PATTERN = re.compile(r"from (.+?)(?:\.|$|,)", re.IGNORECASE)

CHARACTER_PATTERNS = [
    re.compile(r"i (?:really )?like (\w+) from (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"i love (\w+) from (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"(\w+) from (.+?) is (?:my favorite|great|amazing|the best)", re.IGNORECASE),
    re.compile(r"favorite (?:character|person) is (\w+)(?: from (.+?))?(?:\.|$|,)", re.IGNORECASE),
]

MEDIA_PATTERNS = [
    re.compile(r"(?:watching|reading|love|like) (.+?)(?:anime|manga|series|show)", re.IGNORECASE),
    re.compile(r"have you (?:seen|read|watched) (.+?)\?", re.IGNORECASE),
]

FAVORITE_PATTERNS = [
    re.compile(r"(?:my )?favorite (?:color|colour) is (\w+)", re.IGNORECASE),
    re.compile(r"(?:my )?favorite food is (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"(?:my )?favorite (.+?) is (.+?)(?:\.|$|,)", re.IGNORECASE),
]

NAME_PATTERNS = [
    re.compile(r"(?:my )?name is (\w+)", re.IGNORECASE),
    re.compile(r"call me (\w+)", re.IGNORECASE),
]

HOBBY_PATTERNS = [
    re.compile(r"i (?:like to|love to|enjoy) (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"my hobby is (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"i'm into (.+?)(?:\.|$|,)", re.IGNORECASE),
]

THINK_TAG_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think_tags(text: str) -> str:
    return THINK_TAG_PATTERN.sub("", text).strip()


def extract_user_info(user_input: str, ai_response: str):
    user_lower = user_input.lower()

    context_match = CONTEXT_PATTERN.search(user_lower)
    extra_context = context_match.group(1) if context_match else None

    for pattern in CHARACTER_PATTERNS:
        match = pattern.search(user_lower)
        if match:
            name   = match.group(1).capitalize()
            source = (match.group(2).strip() if len(match.groups()) >= 2 and match.group(2)
                      else extra_context or "source unknown")
            user_memory.add_preference("favorite_character", name, f"from {source}")
            logger.info("Remembered: likes %s from %s", name, source)
            break

    for pattern in MEDIA_PATTERNS:
        match = pattern.search(user_lower)
        if match:
            media      = match.group(1).strip()
            media_type = ("anime" if "anime" in user_lower
                          else "manga" if "manga" in user_lower else "series")
            user_memory.add_fact(f"Interested in {media} ({media_type})")
            logger.info("Remembered: interested in %s", media)
            break

    for pattern in FAVORITE_PATTERNS:
        match = pattern.search(user_lower)
        if match:
            if "color" in user_lower or "colour" in user_lower:
                user_memory.add_preference("favorite_color", match.group(1))
            elif "food" in user_lower:
                user_memory.add_preference("favorite_food", match.group(1))
            elif len(match.groups()) >= 2:
                user_memory.add_preference(f"favorite_{match.group(1)}", match.group(2))
            break

    for pattern in NAME_PATTERNS:
        match = pattern.search(user_lower)
        if match and "name" in user_lower:
            name = match.group(1).capitalize()
            user_memory.add_personal_info("name", name)
            logger.info("Remembered: name is %s", name)
            break

    for pattern in HOBBY_PATTERNS:
        match = pattern.search(user_lower)
        if match:
            user_memory.add_fact(f"Enjoys {match.group(1).strip()}")
            break


def compute_character_state(user_input: str, ai_response: str):
    user_lower = user_input.lower()

    positive_words = {"thanks", "love", "great", "happy", "fun", "amazing", "good",
                      "nice", "wonderful", "awesome", "cool", "best", "fantastic"}
    negative_words = {"hate", "sad", "angry", "bad", "terrible", "awful", "horrible",
                      "worst", "ugly", "stupid", "annoying"}
    
    words = set(user_lower.split())
    pos_score = len(words & positive_words)
    neg_score = len(words & negative_words)
    
    if user_lower.endswith("!"):
        mood = "excited"
        emotion = 0.8
    elif "?" in user_lower:
        mood = "curious"
        emotion = 0.6
    elif pos_score > neg_score:
        mood = "happy"
        emotion = 0.7 + (pos_score * 0.05)
    elif neg_score > pos_score:
        mood = "annoyed"
        emotion = 0.3 - (neg_score * 0.05)
    else:
        mood = "neutral"
        emotion = 0.5

    emotion = max(0.0, min(1.0, emotion))
    return mood, round(emotion, 2)


def validate_configs() -> list:
    warnings = []
    errors = []

    required_bot = ["ollama", "chat", "character"]
    for key in required_bot:
        if key not in CONFIG:
            errors.append(f"bot_config.yaml: missing '{key}' section")

    if "base_url" not in CONFIG.get("ollama", {}):
        errors.append("bot_config.yaml: ollama.base_url is required")
    if "model" not in CONFIG.get("ollama", {}):
        errors.append("bot_config.yaml: ollama.model is required")
    if "system_prompt" not in CONFIG.get("character", {}):
        errors.append("bot_config.yaml: character.system_prompt is required")

    db_config_path = os.path.join(
        os.path.dirname(__file__), "configs", "database_config.yaml"
    )
    if not os.path.exists(db_config_path):
        warnings.append("database_config.yaml not found - database will fail")
    else:
        with open(db_config_path) as f:
            db_cfg = yaml.safe_load(f)
            if "mysql" not in db_cfg:
                errors.append("database_config.yaml: missing 'mysql' section")
            else:
                for field in ["host", "database", "username"]:
                    if field not in db_cfg.get("mysql", {}):
                        errors.append(f"database_config.yaml: mysql.{field} is required")

    tts_config_path = os.path.join(
        os.path.dirname(__file__), "configs", "sovits_config.yaml"
    )
    if os.path.exists(tts_config_path):
        with open(tts_config_path) as f:
            tts_cfg = yaml.safe_load(f)
            if "api" not in tts_cfg or "base_url" not in tts_cfg.get("api", {}):
                warnings.append("sovits_config.yaml: api.base_url not set - TTS will fail")

    return errors, warnings


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
                role_icon = "U" if last_msg["role"] == "user" else CHARACTER_NAME[0]
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
                print(f"\nResuming: {sid[:8]}...")
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
    print(f"\nNew session: {sid[:8]}...")
    return sid


errors, warnings = validate_configs()
if errors:
    logger.error("Config validation FAILED:")
    for e in errors:
        logger.error("  - %s", e)
    logger.error("Fix configs before running.")
    exit(1)
if warnings:
    logger.warning("Config warnings:")
    for w in warnings:
        logger.warning("  - %s", w)

user_memory.display_memory()
session_id = start_or_resume_session()

print("\nCommands: 'exit' | 'history' | 'memory' | 'clear_memory'\n")

while True:
    user_input = input("You: ").strip()
    if not user_input:
        continue

    if user_input.lower() == "exit":
        print(f"Goodbye! Session saved: {session_id[:8]}...")
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
            print("Memory cleared")
        continue

    storage.add_message(
        session_id=session_id,
        role="user",
        content=user_input,
        metadata={"timestamp": datetime.now().isoformat()},
    )

    recent  = storage.get_recent_context(session_id, CONTEXT_LENGTH)
    messages = [{"role": m["role"], "content": m["content"]} for m in recent]

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
        )
        raw_reply  = response.choices[0].message.content
        kuro_reply = strip_think_tags(raw_reply)

        print(f"\n{CHARACTER_NAME}: {kuro_reply}\n")

        if TTS_ENABLED:
            try:
                speak(kuro_reply)
            except Exception as e:
                logger.error("TTS error: %s", e)

        extract_user_info(user_input, kuro_reply)

        mood, emotion = compute_character_state(user_input, kuro_reply)
        storage.add_message(
            session_id=session_id,
            role="assistant",
            content=kuro_reply,
            metadata={"model": MODEL_NAME, "timestamp": datetime.now().isoformat()},
        )
        storage.update_character_state(
            session_id=session_id,
            mood=mood,
            emotion_level=emotion,
            context_summary=f"Topic: {user_input[:50]}...",
        )

    except Exception as e:
        logger.error("Error talking to Ollama: %s", e)
        logger.error("Make sure Ollama is running: ollama serve")
        continue
