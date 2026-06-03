import re
import uuid
import os
import threading
from datetime import datetime
from typing import Optional

import yaml
from openai import OpenAI

from server.process.storage.mysql_storage import MySQLConversationStorage
from server.process.storage.user_memory import UserMemory
from server.process.tts_func.sovits_amd import speak, check_api_available
from utils.logging import logger


_CONTEXT_PATTERN = re.compile(r"from (.+?)(?:\.|$|,)", re.IGNORECASE)

_CHARACTER_PATTERNS = [
    re.compile(r"i (?:really )?like (\w+) from (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"i love (\w+) from (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"(\w+) from (.+?) is (?:my favorite|great|amazing|the best)", re.IGNORECASE),
    re.compile(r"favorite (?:character|person) is (\w+)(?: from (.+?))?(?:\.|$|,)", re.IGNORECASE),
]

_MEDIA_PATTERNS = [
    re.compile(r"(?:watching|reading|love|like) (.+?)(?:anime|manga|series|show)", re.IGNORECASE),
    re.compile(r"have you (?:seen|read|watched) (.+?)\?", re.IGNORECASE),
]

_FAVORITE_PATTERNS = [
    re.compile(r"(?:my )?favorite (?:color|colour) is (\w+)", re.IGNORECASE),
    re.compile(r"(?:my )?favorite food is (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"(?:my )?favorite (.+?) is (.+?)(?:\.|$|,)", re.IGNORECASE),
]

_NAME_PATTERNS = [
    re.compile(r"(?:my )?name is (\w+)", re.IGNORECASE),
    re.compile(r"call me (\w+)", re.IGNORECASE),
]

_HOBBY_PATTERNS = [
    re.compile(r"i (?:like to|love to|enjoy) (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"my hobby is (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"i'm into (.+?)(?:\.|$|,)", re.IGNORECASE),
]

_THINK_TAG_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think_tags(text: str) -> str:
    return _THINK_TAG_PATTERN.sub("", text).strip()


def _validate_configs(config: dict) -> tuple[list, list]:
    errors = []
    warnings = []

    required_bot = ["ollama", "chat", "character"]
    for key in required_bot:
        if key not in config:
            errors.append(f"bot_config.yaml: missing '{key}' section")

    if "base_url" not in config.get("ollama", {}):
        errors.append("bot_config.yaml: ollama.base_url is required")
    if "model" not in config.get("ollama", {}):
        errors.append("bot_config.yaml: ollama.model is required")
    if "system_prompt" not in config.get("character", {}):
        errors.append("bot_config.yaml: character.system_prompt is required")

    db_config_path = os.path.join(
        os.path.dirname(__file__), "..", "configs", "database_config.yaml"
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
        os.path.dirname(__file__), "..", "configs", "sovits_config.yaml"
    )
    if os.path.exists(tts_config_path):
        with open(tts_config_path) as f:
            tts_cfg = yaml.safe_load(f)
            if "api" not in tts_cfg or "base_url" not in tts_cfg.get("api", {}):
                warnings.append("sovits_config.yaml: api.base_url not set")

    return errors, warnings


class _NullStorage:
    def add_message(self, *args, **kwargs): return 0
    def get_recent_context(self, *args, **kwargs): return []
    def get_conversation_history(self, *args, **kwargs): return []
    def get_all_sessions(self, *args, **kwargs): return []
    def update_character_state(self, *args, **kwargs): pass
    def get_character_state(self, *args, **kwargs): return None
    def close(self): pass


class KuroEngine:
    def __init__(self, config_path: str = "configs/bot_config.yaml") -> None:
        config_path = os.path.join(
            os.path.dirname(__file__), "..", config_path
        ) if not os.path.isabs(config_path) else config_path

        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        errors, warnings = _validate_configs(self.config)
        if errors:
            logger.error("Config validation FAILED:")
            for e in errors:
                logger.error("  - %s", e)
            raise RuntimeError("Configuration validation failed")

        for w in warnings:
            logger.warning("  - %s", w)

        self.client = OpenAI(
            base_url=self.config["ollama"]["base_url"],
            api_key=self.config["ollama"]["api_key"],
        )

        self.model_name = self.config["ollama"]["model"]
        self.context_length = self.config["chat"]["context_length"]
        self.character_name = self.config["character"]["name"]
        self.base_system_prompt = self.config["character"]["system_prompt"]
        self.user_id = self.config["chat"]["user_id"]

        self._tts_lock = threading.Lock()

        db_config_path = os.path.join(
            os.path.dirname(__file__), "..", "configs", "database_config.yaml"
        )
        try:
            self.storage = MySQLConversationStorage(db_config_path)
            self.user_memory = UserMemory(self.storage, user_id=self.user_id)
            self.db_available = True
        except Exception as e:
            logger.warning("Database unavailable (%s) — running without persistence", e)
            self.storage = _NullStorage()
            self.user_memory = UserMemory(self.storage, user_id=self.user_id)
            self.db_available = False

        if self.config.get("tts", {}).get("enabled", True):
            self.tts_enabled = check_api_available()
        else:
            self.tts_enabled = False

        logger.info("KuroEngine initialized (TTS: %s)", "on" if self.tts_enabled else "off")

    # ------------------------------------------------------------------ #
    #  Session management                                                 #
    # ------------------------------------------------------------------ #

    def create_session(self) -> str:
        session_id = str(uuid.uuid4())
        memory_context = self.user_memory.build_context_prompt()
        full_prompt = self.base_system_prompt + "\n" + memory_context
        self.storage.add_message(
            session_id=session_id,
            role="system",
            content=full_prompt,
            metadata={
                "character": self.character_name,
                "personality": self.config["character"]["personality"],
            },
        )
        self.storage.update_character_state(
            session_id=session_id,
            mood="neutral",
            emotion_level=0.5,
            context_summary="New session",
        )
        logger.info("Created session %s", session_id[:8])
        return session_id

    def session_exists(self, session_id: str) -> bool:
        return session_id in self.storage.get_all_sessions()

    def get_recent_sessions(self, limit: int = 5) -> list[dict]:
        sessions = self.storage.get_all_sessions()
        result = []
        for session_id in sessions[-limit:]:
            history = self.storage.get_conversation_history(session_id, limit=5)
            if history:
                last_msg = next(
                    (m for m in reversed(history) if m["role"] != "system"), None
                )
                if last_msg:
                    result.append({
                        "session_id": session_id,
                        "preview": last_msg["content"][:50],
                        "timestamp": str(last_msg["timestamp"]),
                        "role": last_msg["role"],
                    })
        return result

    def get_session_count(self) -> int:
        return len(self.storage.get_all_sessions())

    # ------------------------------------------------------------------ #
    #  Core interaction                                                    #
    # ------------------------------------------------------------------ #

    def process_message(self, user_input: str, session_id: str) -> dict:
        self.storage.add_message(
            session_id=session_id,
            role="user",
            content=user_input,
            metadata={"timestamp": datetime.now().isoformat()},
        )

        recent = self.storage.get_recent_context(session_id, self.context_length)
        messages = [{"role": m["role"], "content": m["content"]} for m in recent]

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
            )
            raw_reply = response.choices[0].message.content
            kuro_reply = _strip_think_tags(raw_reply)
        except Exception as e:
            logger.error("Ollama error: %s", e)
            raise

        if self.tts_enabled:
            try:
                with self._tts_lock:
                    speak(kuro_reply)
            except Exception as e:
                logger.error("TTS error: %s", e)

        self._extract_user_info(user_input, kuro_reply)
        mood, emotion = self._compute_character_state(user_input, kuro_reply)

        self.storage.add_message(
            session_id=session_id,
            role="assistant",
            content=kuro_reply,
            metadata={
                "model": self.model_name,
                "timestamp": datetime.now().isoformat(),
            },
        )
        self.storage.update_character_state(
            session_id=session_id,
            mood=mood,
            emotion_level=emotion,
            context_summary=f"Topic: {user_input[:50]}...",
        )

        return {
            "text": kuro_reply,
            "mood": mood,
            "emotion": emotion,
            "session_id": session_id,
        }

    # ------------------------------------------------------------------ #
    #  Data queries                                                        #
    # ------------------------------------------------------------------ #

    def get_history(self, session_id: str) -> str:
        history = self.storage.get_conversation_history(session_id)
        lines = []
        for msg in history:
            if msg["role"] != "system":
                lines.append(f"{msg['role'].upper()}: {msg['content']}")
        return "\n".join(lines) if lines else "(empty)"

    def get_memory_summary(self) -> str:
        return self.user_memory.get_memory_summary()

    def get_formatted_memory(self) -> str:
        lines = []
        mem = self.user_memory.memory

        lines.append(f"User ID: {self.user_id}\n")
        if mem.get("personal_info"):
            lines.append("Personal Information:")
            for k, v in mem["personal_info"].items():
                lines.append(f"  - {k}: {v}")

        if mem.get("preferences"):
            lines.append("\nPreferences:")
            for k, v in mem["preferences"].items():
                lines.append(f"  - {k}: {v}")

        if mem.get("detailed_preferences"):
            lines.append("\nDetailed Preferences:")
            for category, prefs in mem["detailed_preferences"].items():
                for pref in prefs:
                    ctx = f" - {pref['context']}" if pref.get("context") else ""
                    lines.append(f"  {category}: {pref['value']}{ctx}")

        if mem.get("important_facts"):
            lines.append("\nImportant Facts:")
            for entry in mem["important_facts"][-15:]:
                fact = entry["fact"] if isinstance(entry, dict) else entry
                lines.append(f"  - {fact}")

        if mem.get("topics_discussed"):
            topics = mem["topics_discussed"][-10:]
            lines.append(f"\nTopics: {', '.join(topics)}")

        return "\n".join(lines) if len(lines) > 1 else "No stored memories."

    def display_memory(self) -> None:
        print(self.get_formatted_memory())

    def clear_memory(self) -> None:
        self.user_memory.clear_memory()
        logger.info("User memory cleared")

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        self.storage.close()
        logger.info("KuroEngine closed")

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _extract_user_info(self, user_input: str, ai_response: str) -> None:
        user_lower = user_input.lower()

        context_match = _CONTEXT_PATTERN.search(user_lower)
        extra_context = context_match.group(1) if context_match else None

        for pattern in _CHARACTER_PATTERNS:
            match = pattern.search(user_lower)
            if match:
                name = match.group(1).capitalize()
                source = (
                    match.group(2).strip()
                    if len(match.groups()) >= 2 and match.group(2)
                    else extra_context or "source unknown"
                )
                self.user_memory.add_preference("favorite_character", name, f"from {source}")
                logger.info("Remembered: likes %s from %s", name, source)
                break

        for pattern in _MEDIA_PATTERNS:
            match = pattern.search(user_lower)
            if match:
                media = match.group(1).strip()
                media_type = (
                    "anime" if "anime" in user_lower
                    else "manga" if "manga" in user_lower
                    else "series"
                )
                self.user_memory.add_fact(f"Interested in {media} ({media_type})")
                logger.info("Remembered: interested in %s", media)
                break

        for pattern in _FAVORITE_PATTERNS:
            match = pattern.search(user_lower)
            if match:
                if "color" in user_lower or "colour" in user_lower:
                    self.user_memory.add_preference("favorite_color", match.group(1))
                elif "food" in user_lower:
                    self.user_memory.add_preference("favorite_food", match.group(1))
                elif len(match.groups()) >= 2:
                    self.user_memory.add_preference(f"favorite_{match.group(1)}", match.group(2))
                break

        for pattern in _NAME_PATTERNS:
            match = pattern.search(user_lower)
            if match and "name" in user_lower:
                name = match.group(1).capitalize()
                self.user_memory.add_personal_info("name", name)
                logger.info("Remembered: name is %s", name)
                break

        for pattern in _HOBBY_PATTERNS:
            match = pattern.search(user_lower)
            if match:
                self.user_memory.add_fact(f"Enjoys {match.group(1).strip()}")
                break

    @staticmethod
    def _compute_character_state(user_input: str, ai_response: str) -> tuple[str, float]:
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
