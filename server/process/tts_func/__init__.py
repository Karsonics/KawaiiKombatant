"""
Pluggable TTS Backend Manager
==============================
Selects and routes TTS requests to the active backend.

Config-driven (bot_config.yaml):
  tts:
    mode: "per_mood"       # "manual" | "auto" | "per_mood"
    manual_backend: "gpt_sovits"
    mood_routing:
      neutral:  "gpt_sovits"
      happy:    "f5_tts"
      annoyed:  "f5_tts"
      excited:  "f5_tts"
      curious:  "gpt_sovits"

Usage:
  from server.process.tts_func import init_tts, speak, check_tts_available

  init_tts(bot_config)
  speak("Hello!", mood="happy")
"""

import os
import threading
from typing import Optional

import yaml

from server.process.tts_func.base import TTSBackend
from utils.logging import logger

_backend_pool: dict[str, TTSBackend] = {}
_mood_map: dict[str, str] = {}
_default_backend: str = "gpt_sovits"
_mode: str = "manual"
_lock = threading.Lock()


def _load_tts_config():
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "configs", "bot_config.yaml"
    )
    if os.path.exists(path):
        with open(path) as f:
            cfg = yaml.safe_load(f)
        return cfg.get("tts", {})
    return {}


def init_tts(bot_config: Optional[dict] = None) -> None:
    global _mode, _default_backend, _mood_map, _backend_pool

    cfg = bot_config or _load_tts_config()
    # Accept either full bot_config dict or just the tts sub-dict
    if "tts" in cfg:
        cfg = cfg["tts"]
    _mode = cfg.get("mode", "manual")
    _default_backend = cfg.get("manual_backend", "gpt_sovits")
    _mood_map = cfg.get("mood_routing", {})

    from server.process.tts_func.gpt_sovits import GPTSovitsBackend
    _backend_pool["gpt_sovits"] = GPTSovitsBackend()

    # F5-TTS is optional — only init if enabled
    f5_cfg = cfg.get("backends", {}).get("f5_tts", {})
    if f5_cfg.get("enabled", False) or _mode in ("auto", "per_mood"):
        try:
            from server.process.tts_func.f5_tts import F5TTSBackend
            _backend_pool["f5_tts"] = F5TTSBackend()
            logger.info("F5-TTS backend registered")
        except Exception as e:
            logger.warning("F5-TTS backend unavailable: %s", e)

    logger.info(
        "TTS manager initialized (mode=%s, backends=%s)",
        _mode, list(_backend_pool.keys()),
    )


def _resolve_backend(mood: str = None) -> TTSBackend:
    mood = mood or "neutral"

    # Per-mood routing
    if _mode == "per_mood" and mood in _mood_map:
        target = _mood_map[mood]
        backend = _backend_pool.get(target)
        if backend and backend.check_available():
            return backend
        logger.debug("Mood '%s' → %s (down), falling back", mood, target)

    # Auto — pick first available by priority
    if _mode == "auto":
        for tag in ("f5_tts", "gpt_sovits"):
            backend = _backend_pool.get(tag)
            if backend and backend.check_available():
                return backend

    # Manual — use configured default
    backend = _backend_pool.get(_default_backend)
    if backend and backend.check_available():
        return backend

    # Ultimate fallback — any backend that's alive
    for tag, backend in _backend_pool.items():
        if backend.check_available():
            logger.warning("All preferred backends down, using %s", backend.name)
            return backend

    raise RuntimeError("No TTS backends available")


def speak(text: str, mood: str = None, lang: str = None) -> bool:
    with _lock:
        backend = _resolve_backend(mood)
        return backend.speak(text, mood=mood, lang=lang)


def check_tts_available() -> bool:
    try:
        _resolve_backend()
        return True
    except RuntimeError:
        return False


def set_voice(name_or_path: str) -> bool:
    for backend in _backend_pool.values():
        if backend.check_available():
            return backend.set_voice(name_or_path)
    return False


def get_active_backend(mood: str = None) -> Optional[TTSBackend]:
    try:
        return _resolve_backend(mood)
    except RuntimeError:
        return None
