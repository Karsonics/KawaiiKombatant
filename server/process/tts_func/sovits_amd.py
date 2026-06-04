import requests
import tempfile
import os
import yaml
import threading

from utils.logging import logger

_TTS_DEPS = True
try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    _TTS_DEPS = False
    logger.warning("sounddevice/soundfile not available — TTS playback disabled")


def load_config():
    config_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "configs", "sovits_config.yaml"
    )
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f)

    ref_path = cfg['reference']['audio_path']
    if not os.path.isabs(ref_path):
        cfg['reference']['audio_path'] = os.path.abspath(
            os.path.join(os.path.dirname(config_path), "..", ref_path)
        )
    return cfg


CONFIG = load_config()

SOVITS_API_URL = f"{CONFIG['api']['base_url']}{CONFIG['api']['endpoint']}"
REF_AUDIO_PATH = CONFIG['reference']['audio_path']
REF_AUDIO_TEXT = CONFIG['reference']['audio_text']
DEFAULT_LANG = CONFIG['reference']['language']
SPEED_FACTOR = CONFIG['inference']['speed_factor']
TOP_K = CONFIG['inference']['top_k']
TOP_P = CONFIG['inference']['top_p']
TEMPERATURE = CONFIG['inference']['temperature']
TARGET_SR = CONFIG['output']['sampling_rate']
AUDIO_DEVICE = CONFIG['output'].get('device', None)
ASYNC_PLAYBACK = CONFIG['output']['async_playback']
EMOTION_MAP = CONFIG.get('emotion', {}).get('mood_map', {})

_VOICE_OVERRIDE: dict | None = None


def set_voice(name_or_path: str) -> bool:
    global _VOICE_OVERRIDE
    voices = CONFIG.get("voices", {})
    if name_or_path in voices:
        v = voices[name_or_path]
        path = v.get("audio_path", REF_AUDIO_PATH)
        _VOICE_OVERRIDE = {
            "audio_path": os.path.abspath(os.path.join(os.path.dirname(
                os.path.join(os.path.dirname(__file__), "..", "..", "..", "configs")
            ), path)) if not os.path.isabs(path) else path,
            "audio_text": v.get("audio_text", REF_AUDIO_TEXT),
            "language": v.get("language", DEFAULT_LANG),
        }
        logger.info("TTS voice set to preset '%s' (%s)", name_or_path, _VOICE_OVERRIDE["audio_path"])
        return True
    if os.path.exists(name_or_path):
        _VOICE_OVERRIDE = {
            "audio_path": os.path.abspath(name_or_path),
            "audio_text": REF_AUDIO_TEXT,
            "language": DEFAULT_LANG,
        }
        logger.info("TTS voice set to custom path: %s", _VOICE_OVERRIDE["audio_path"])
        return True
    logger.warning("Voice '%s' not found (no preset, file doesn't exist)", name_or_path)
    return False


def speak(text: str, lang: str = None, mood: str = None) -> bool:
    if not _TTS_DEPS:
        logger.debug("TTS deps missing, skipping audio playback")
        return False
    if not text.strip():
        return False

    if lang is None:
        lang = DEFAULT_LANG

    ref_path = _VOICE_OVERRIDE["audio_path"] if _VOICE_OVERRIDE else REF_AUDIO_PATH
    ref_text = _VOICE_OVERRIDE["audio_text"] if _VOICE_OVERRIDE else REF_AUDIO_TEXT
    ref_lang = _VOICE_OVERRIDE["language"] if _VOICE_OVERRIDE else lang

    if mood:
        emotion_tag = EMOTION_MAP.get(mood, "")
        if emotion_tag:
            text = f"[{emotion_tag}] {text}"
            logger.debug("TTS mood: %s → tag: %s", mood, emotion_tag)
        else:
            logger.debug("TTS mood: %s (no tag mapped)", mood)

    try:
        params = {
            "text": text,
            "text_lang": ref_lang,
            "ref_audio_path": ref_path,
            "prompt_text": ref_text,
            "prompt_lang": ref_lang,
            "media_type": "wav",
            "streaming_mode": "false",
            "speed_factor": SPEED_FACTOR,
            "top_k": TOP_K,
            "top_p": TOP_P,
            "temperature": TEMPERATURE,
        }

        response = requests.get(SOVITS_API_URL, params=params, timeout=CONFIG['api']['timeout'])
        response.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(response.content)
            tmp_path = tmp.name

        data, samplerate = sf.read(tmp_path)

        if samplerate != TARGET_SR:
            import librosa
            data = librosa.resample(data.T if data.ndim > 1 else data,
                                   orig_sr=samplerate, target_sr=TARGET_SR)
            if data.ndim > 1:
                data = data.T
            samplerate = TARGET_SR

        if ASYNC_PLAYBACK:
            threading.Thread(target=_play_audio, args=(data.copy(), samplerate, tmp_path), daemon=True).start()
        else:
            _play_audio(data, samplerate, tmp_path)
            os.unlink(tmp_path)

        return True

    except requests.exceptions.ConnectionError:
        logger.warning("GPT-SoVITS API not reachable - is api_v2.py running on port 9880?")
        return False
    except Exception as e:
        logger.error("TTS error: %s", e)
        return False


def _play_audio(data, samplerate, tmp_path):
    if not _TTS_DEPS:
        return
    try:
        sd.play(data, samplerate, device=AUDIO_DEVICE)
        sd.wait()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def check_api_available() -> bool:
    try:
        requests.get(CONFIG['api']['base_url'], timeout=3)
        return True
    except Exception:
        return False
