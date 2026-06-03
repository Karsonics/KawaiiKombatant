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
ASYNC_PLAYBACK = CONFIG['output']['async_playback']


def speak(text: str, lang: str = None) -> bool:
    if not _TTS_DEPS:
        logger.debug("TTS deps missing, skipping audio playback")
        return False
    if not text.strip():
        return False

    if lang is None:
        lang = DEFAULT_LANG

    try:
        params = {
            "text": text,
            "text_lang": lang,
            "ref_audio_path": REF_AUDIO_PATH,
            "prompt_text": REF_AUDIO_TEXT,
            "prompt_lang": lang,
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
        sd.play(data, samplerate)
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
