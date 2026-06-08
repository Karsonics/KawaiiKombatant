import json
import re
import uuid
import os
import threading
from datetime import datetime
from typing import Generator

import yaml
from openai import OpenAI

from server.process.storage.mysql_storage import MySQLConversationStorage
from server.process.storage.user_memory import UserMemory
from server.process.tts_func import (
    init_tts,
    speak,
    check_tts_available,
    set_voice as tts_set_voice,
)
from utils.logging import logger

_CONTEXT_PATTERN = re.compile(r"from (.+?)(?:\.|$|,)", re.IGNORECASE)

_CHARACTER_PATTERNS = [
    re.compile(r"i (?:really )?like (\w+) from (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(r"i love (\w+) from (.+?)(?:\.|$|,)", re.IGNORECASE),
    re.compile(
        r"(\w+) from (.+?) is (?:my favorite|great|amazing|the best)", re.IGNORECASE
    ),
    re.compile(
        r"favorite (?:character|person) is (\w+)(?: from (.+?))?(?:\.|$|,)",
        re.IGNORECASE,
    ),
]

_MEDIA_PATTERNS = [
    re.compile(
        r"(?:watching|reading|love|like) (.+?)(?:anime|manga|series|show)",
        re.IGNORECASE,
    ),
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

_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')
_THINK_TAG_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think_tags(text: str) -> str:
    return _THINK_TAG_PATTERN.sub("", text).strip()


_PROVIDER_DEFAULTS = {
    "ollama": {"base_url": "http://localhost:11434/v1", "api_key": "ollama"},
    "openai": {"base_url": "https://api.openai.com/v1", "api_key": None},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key": None},
    "custom": {"base_url": None, "api_key": None},
}


def _resolve_llm_config(config: dict) -> dict:
    if "llm" in config:
        llm_cfg = config["llm"]
    elif "ollama" in config:
        llm_cfg = {**config["ollama"], "provider": "ollama"}
    else:
        llm_cfg = {"provider": "ollama"}

    provider = llm_cfg.get("provider", "ollama")
    defaults = _PROVIDER_DEFAULTS.get(provider, _PROVIDER_DEFAULTS["custom"])

    resolved = {
        "provider": provider,
        "base_url": llm_cfg.get("base_url") or defaults["base_url"],
        "api_key": llm_cfg.get("api_key") or defaults["api_key"],
        "model": llm_cfg.get("model", ""),
        "extraction_model": llm_cfg.get("extraction_model") or llm_cfg.get("model", ""),
    }
    return resolved


def _validate_configs(config: dict) -> tuple[list, list]:
    errors = []
    warnings = []

    has_llm = "llm" in config
    has_ollama = "ollama" in config

    if not has_llm and not has_ollama:
        errors.append("bot_config.yaml: missing 'llm' or 'ollama' section")
    elif has_llm:
        llm = config["llm"]
        provider = llm.get("provider", "ollama")
        if provider not in _PROVIDER_DEFAULTS:
            errors.append(
                f"bot_config.yaml: llm.provider '{provider}' unknown (use: {', '.join(_PROVIDER_DEFAULTS)})"
            )
        if provider == "custom" and not llm.get("base_url"):
            errors.append(
                "bot_config.yaml: llm.base_url is required for provider 'custom'"
            )
        if provider in ("openai", "openrouter") and not llm.get("api_key"):
            errors.append(
                f"bot_config.yaml: llm.api_key is required for provider '{provider}'"
            )
        if not llm.get("model"):
            errors.append("bot_config.yaml: llm.model is required")
    else:
        if "base_url" not in config.get("ollama", {}):
            errors.append("bot_config.yaml: ollama.base_url is required")
        if "model" not in config.get("ollama", {}):
            errors.append("bot_config.yaml: ollama.model is required")

    if "chat" not in config:
        errors.append("bot_config.yaml: missing 'chat' section")
    if "character" not in config:
        errors.append("bot_config.yaml: missing 'character' section")
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
                        errors.append(
                            f"database_config.yaml: mysql.{field} is required"
                        )

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
    def add_message(self, *args, **kwargs):
        return 0

    def get_recent_context(self, *args, **kwargs):
        return []

    def get_conversation_history(self, *args, **kwargs):
        return []

    def get_all_sessions(self, *args, **kwargs):
        return []

    def update_character_state(self, *args, **kwargs):
        pass

    def get_character_state(self, *args, **kwargs):
        return None

    def close(self):
        pass


class KuroEngine:
    def __init__(
        self,
        config_path: str = "configs/bot_config.yaml",
        dry_run: bool = False,
        voice_override: str | None = None,
    ) -> None:
        config_path = (
            os.path.join(os.path.dirname(__file__), "..", config_path)
            if not os.path.isabs(config_path)
            else config_path
        )

        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.dry_run = dry_run

        errors, warnings = _validate_configs(self.config)
        if errors:
            logger.error("Config validation FAILED:")
            for e in errors:
                logger.error("  - %s", e)
            raise RuntimeError("Configuration validation failed")

        for w in warnings:
            logger.warning("  - %s", w)

        llm_cfg = _resolve_llm_config(self.config)

        self.model_name = llm_cfg["model"]
        self.extraction_model_name = llm_cfg["extraction_model"]

        if dry_run:
            logger.info("DRY RUN — skipping LLM and database initialization")
            self.client = None
            self.extraction_client = None
            self.storage = _NullStorage()
            self.user_memory = UserMemory(
                self.storage,
                user_id=self.config.get("chat", {}).get("user_id", "default_user"),
            )
            self.db_available = False
            self.tts_enabled = False
        else:
            self.client = OpenAI(
                base_url=llm_cfg["base_url"],
                api_key=llm_cfg["api_key"],
            )

            if llm_cfg["extraction_model"] != llm_cfg["model"]:
                self.extraction_client = OpenAI(
                    base_url=llm_cfg["base_url"],
                    api_key=llm_cfg["api_key"],
                )
            else:
                self.extraction_client = self.client

            db_config_path = os.path.join(
                os.path.dirname(__file__), "..", "configs", "database_config.yaml"
            )
            try:
                self.storage = MySQLConversationStorage(db_config_path)
                self.user_memory = UserMemory(
                    self.storage,
                    user_id=self.config.get("chat", {}).get("user_id", "default_user"),
                )
                self.db_available = True
            except Exception as e:
                logger.warning(
                    "Database unavailable (%s) — running without persistence", e
                )
                self.storage = _NullStorage()
                self.user_memory = UserMemory(
                    self.storage,
                    user_id=self.config.get("chat", {}).get("user_id", "default_user"),
                )
                self.db_available = False

            if self.config.get("tts", {}).get("enabled", True):
                init_tts(self.config)
                self.tts_enabled = check_tts_available()
            else:
                self.tts_enabled = False

        self.context_length = self.config.get("chat", {}).get("context_length", 15)
        self.character_name = self.config.get("character", {}).get("name", "Kuro")
        self.base_system_prompt = self.config.get("character", {}).get(
            "system_prompt", ""
        )
        self.user_id = self.config.get("chat", {}).get("user_id", "default_user")

        self._tts_lock = threading.Lock()

        if voice_override and not dry_run:
            tts_set_voice(voice_override)

        logger.info(
            "KuroEngine initialized (dry_run=%s, TTS: %s)",
            dry_run,
            getattr(self, "tts_enabled", False),
        )

    # ------------------------------------------------------------------ #
    #  Session management                                                 #
    # ------------------------------------------------------------------ #

    def create_session(self) -> str:
        session_id = str(uuid.uuid4())
        self.storage.add_message(
            session_id=session_id,
            role="system",
            content=self.base_system_prompt,
            metadata={
                "character": self.character_name,
                "personality": self.config["character"]["personality"],
            },
        )
        self.storage.update_character_state(
            session_id=session_id,
            mood=self.user_memory.get_mood(),
            emotion_level=self.user_memory.get_emotion(),
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
                    result.append(
                        {
                            "session_id": session_id,
                            "preview": last_msg["content"][:50],
                            "timestamp": str(last_msg["timestamp"]),
                            "role": last_msg["role"],
                        }
                    )
        return result

    def get_session_count(self) -> int:
        return len(self.storage.get_all_sessions())

    # ------------------------------------------------------------------ #
    #  Core interaction                                                    #
    # ------------------------------------------------------------------ #

    def _stream_response(self, messages: list[dict]) -> Generator[str, None, None]:
        buffer = ""
        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            buffer += delta
            parts = _SENTENCE_SPLIT.split(buffer)
            for sentence in parts[:-1]:
                if sentence.strip():
                    yield sentence.strip()
            buffer = parts[-1]
        if buffer.strip():
            yield buffer.strip()

    def process_message(self, user_input: str, session_id: str, sentence_callback=None) -> dict:
        if not self.dry_run:
            self.storage.add_message(
                session_id=session_id,
                role="user",
                content=user_input,
                metadata={"timestamp": datetime.now().isoformat()},
            )

        if self.dry_run:
            logger.info("[DRY RUN] Would call LLM with: %s", user_input[:80])
            kuro_reply = f"[DRY RUN] Kuro would respond to: {user_input[:60]}"
            mood, emotion = "neutral", 0.5
        else:
            recent = self.storage.get_recent_context(session_id, self.context_length)
            messages = [{"role": m["role"], "content": m["content"]} for m in recent]
            memory_context = self.user_memory.build_context_prompt()
            summary = self.user_memory.get_summary()
            if memory_context:
                messages.insert(0, {"role": "system", "content": memory_context})
            if summary:
                messages.insert(0, {"role": "system", "content": f"[Conversation Summary: {summary}]"})

            try:
                kuro_reply_parts = []
                mood, emotion = "neutral", 0.5
                for i, sentence in enumerate(self._stream_response(messages)):
                    cleaned = _strip_think_tags(sentence)
                    if not cleaned:
                        continue
                    if i == 0:
                        mood, emotion = self._compute_character_state(user_input, cleaned)
                    if self.tts_enabled:
                        try:
                            with self._tts_lock:
                                speak(cleaned, mood=mood)
                        except Exception as e:
                            logger.error("TTS error: %s", e)
                    kuro_reply_parts.append(cleaned)
                    if sentence_callback:
                        sentence_callback(cleaned)
                kuro_reply = " ".join(kuro_reply_parts)
                mood, emotion = self._compute_character_state(user_input, kuro_reply)
            except Exception as e:
                logger.error("LLM error: %s", e)
                raise

        self.user_memory.set_mood(mood, emotion)
        self._extract_user_info(user_input, kuro_reply)
        self.user_memory.flush()

        if not self.dry_run:
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
            self._maybe_summarize(session_id)

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

    def _maybe_summarize(self, session_id: str) -> None:
        threshold = self.config.get("chat", {}).get("summarization_threshold", 20)
        if threshold <= 0:
            return
        count = self.storage.get_message_count(session_id)
        if count < threshold:
            return
        half = count // 2
        history = self.storage.get_conversation_history(session_id, limit=half)
        history = [m for m in history if m["role"] != "system"]
        if not history:
            return
        text = "\n".join(f"{m['role']}: {m['content']}" for m in history)
        try:
            response = self.client.chat.completions.create(
                model=self.extraction_model_name or self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "Summarize this conversation concisely in 2-3 sentences. Focus on key facts, decisions, and emotional tone.",
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0.3,
            )
            summary = response.choices[0].message.content.strip()
            summary = _strip_think_tags(summary)
            self.user_memory.set_summary(summary)
            logger.info(
                "Conversation summarized (%d messages): %s", count, summary[:80]
            )
        except Exception as e:
            logger.warning("Summarization failed: %s", e)

    def close(self) -> None:
        self.storage.close()
        logger.info("KuroEngine closed")

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _extract_user_info(self, user_input: str, ai_response: str) -> None:
        extraction_enabled = self.config.get("llm", {}).get("extraction_enabled", True)
        if extraction_enabled and self.extraction_client is not None:
            if self._extract_user_info_llm(user_input):
                return
        self._extract_user_info_regex(user_input)

    _EXTRACTION_PROMPT = (
        "Extract any personal information the user shared in this message. "
        "Return ONLY valid JSON with any of these fields that apply:\n"
        '{"name": "...", "favorite_food": "...", "favorite_color": "...",\n'
        ' "favorite_character": "...", "favorite_character_from": "...",\n'
        ' "hobbies": ["..."], "media_interests": ["..."], "other_facts": ["..."]}\n'
        "If nothing to extract, return {}\n\nMessage: {text}"
    )

    def _extract_user_info_llm(self, user_input: str) -> bool:
        try:
            response = self.extraction_client.chat.completions.create(
                model=self.extraction_model_name,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a data extraction assistant. Extract structured info from user messages. Return only JSON.",
                    },
                    {
                        "role": "user",
                        "content": self._EXTRACTION_PROMPT.format(text=user_input),
                    },
                ],
                temperature=0.1,
            )
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
        except Exception:
            return False

        if not data or not isinstance(data, dict):
            return False

        found = False
        if data.get("name"):
            self.user_memory.add_personal_info("name", data["name"])
            logger.info("LLM extracted: name=%s", data["name"])
            found = True
        if data.get("favorite_food"):
            self.user_memory.add_preference("favorite_food", data["favorite_food"])
            logger.info("LLM extracted: favorite_food=%s", data["favorite_food"])
            found = True
        if data.get("favorite_color"):
            self.user_memory.add_preference("favorite_color", data["favorite_color"])
            logger.info("LLM extracted: favorite_color=%s", data["favorite_color"])
            found = True
        if data.get("favorite_character"):
            source = data.get("favorite_character_from", "unknown")
            ctx = f"from {source}" if source and source != "unknown" else None
            self.user_memory.add_preference(
                "favorite_character", data["favorite_character"], ctx
            )
            logger.info(
                "LLM extracted: favorite_character=%s", data["favorite_character"]
            )
            found = True
        for hobby in data.get("hobbies", []):
            self.user_memory.add_fact(f"Enjoys {hobby}")
            logger.info("LLM extracted: hobby=%s", hobby)
            found = True
        for media in data.get("media_interests", []):
            self.user_memory.add_fact(f"Interested in {media}")
            logger.info("LLM extracted: media=%s", media)
            found = True
        for fact in data.get("other_facts", []):
            self.user_memory.add_fact(fact)
            logger.info("LLM extracted: fact=%s", fact)
            found = True

        return found

    def _extract_user_info_regex(self, user_input: str) -> None:
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
                self.user_memory.add_preference(
                    "favorite_character", name, f"from {source}"
                )
                logger.info("Remembered: likes %s from %s", name, source)
                break

        for pattern in _MEDIA_PATTERNS:
            match = pattern.search(user_lower)
            if match:
                media = match.group(1).strip()
                media_type = (
                    "anime"
                    if "anime" in user_lower
                    else "manga" if "manga" in user_lower else "series"
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
                    self.user_memory.add_preference(
                        f"favorite_{match.group(1)}", match.group(2)
                    )
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
    def _compute_character_state(
        user_input: str, ai_response: str
    ) -> tuple[str, float]:
        resp_lower = ai_response.lower()

        tsundere_denial = {"baka", "hmph", "it's not like", "don't get", "whatever", "shut up", "stupid", "idiot"}
        happy_signals = {"hehe", "~", "happy", "glad", "nice", "wag", "tail", "ears", "fun", "cute"}
        user_anger = {"hate", "angry", "mad", "annoyed", "terrible", "awful"}

        if any(w in resp_lower for w in tsundere_denial):
            return "annoyed", 0.6
        if any(w in resp_lower for w in happy_signals):
            return "happy", 0.8
        if "?" in resp_lower:
            return "curious", 0.6
        if "!" in resp_lower:
            return "excited", 0.7

        if any(w in user_input.lower() for w in user_anger):
            return "annoyed", 0.4

        return "neutral", 0.5
