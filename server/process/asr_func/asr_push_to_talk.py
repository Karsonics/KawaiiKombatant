import os
import yaml
import numpy as np
from typing import Optional

from utils.logging import logger


_TRANSCRIBER = None


def _load_config():
    config_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "configs", "asr_config.yaml"
    )
    cfg = {"model": {"size": "base", "device": None, "language": "en"},
           "audio": {"sample_rate": 16000, "channels": 1}}
    if os.path.exists(config_path):
        with open(config_path) as f:
            loaded = yaml.safe_load(f) or {}
            if "model" in loaded:
                cfg["model"].update(loaded["model"])
            if "audio" in loaded:
                cfg["audio"].update(loaded["audio"])
    else:
        logger.warning("asr_config.yaml not found — using defaults")
    return cfg


CONFIG = _load_config()


def _get_transcriber():
    global _TRANSCRIBER
    if _TRANSCRIBER is not None:
        return _TRANSCRIBER
    try:
        from voice.asr import WhisperTranscriber
        _TRANSCRIBER = WhisperTranscriber(
            model_size=CONFIG["model"]["size"],
            device=CONFIG["model"]["device"],
        )
        logger.info("Server ASR initialized (model: %s, device: %s)",
                     CONFIG["model"]["size"], CONFIG["model"]["device"] or "auto")
        return _TRANSCRIBER
    except Exception as e:
        logger.error("Failed to initialize ASR: %s", e)
        return None


def transcribe(
    audio: np.ndarray,
    language: Optional[str] = None,
) -> str:
    if len(audio) == 0:
        return ""
    transcriber = _get_transcriber()
    if transcriber is None:
        raise RuntimeError("ASR model not available")
    return transcriber.transcribe(
        audio,
        language=language or CONFIG["model"]["language"],
    )


def transcribe_bytes(
    audio_bytes: bytes,
    samplerate: int = 16000,
    language: Optional[str] = None,
) -> str:
    if not audio_bytes:
        return ""
    audio = np.frombuffer(audio_bytes, dtype=np.float32).flatten()
    if samplerate != 16000:
        try:
            import librosa
            audio = librosa.resample(
                audio, orig_sr=samplerate, target_sr=16000
            )
        except ImportError:
            logger.warning("librosa not available — assuming 16kHz input")
    return transcribe(audio, language=language)


def is_available() -> bool:
    return _get_transcriber() is not None
