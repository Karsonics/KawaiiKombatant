import os
import threading
import tempfile

import requests
import yaml

from server.process.tts_func.base import TTSBackend
from utils.logging import logger

_TTS_DEPS = True
try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    _TTS_DEPS = False


class GPTSovitsBackend(TTSBackend):

    def __init__(self, config_path: str = None) -> None:
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "..",
                "configs", "sovits_config.yaml",
            )
        with open(config_path, "r") as f:
            self._cfg = yaml.safe_load(f)

        api = self._cfg["api"]
        self._api_url = f"{api['base_url']}{api['endpoint']}"
        self._timeout = api.get("timeout", 60)

        ref = self._cfg["reference"]
        self._base_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        ref_path = ref["audio_path"]
        if not os.path.isabs(ref_path):
            ref_path = os.path.abspath(os.path.join(self._base_dir, ref_path))
        self._default_ref_audio = ref_path
        self._default_ref_text = ref["audio_text"]
        self._default_lang = ref["language"]

        inf = self._cfg["inference"]
        self._speed = inf["speed_factor"]
        self._top_k = inf["top_k"]
        self._top_p = inf["top_p"]
        self._temperature = inf["temperature"]

        out = self._cfg["output"]
        self._target_sr = out["sampling_rate"]
        self._audio_device = out.get("device", None)
        self._async_playback = out["async_playback"]

        self._mood_config = self._cfg.get("moods", {})
        self._voice_override: dict | None = None
        self._voices = self._cfg.get("voices", {})
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "GPT-SoVITS"

    # ── voice selection ──────────────────────────────────────────────────

    def set_voice(self, name_or_path: str) -> bool:
        if name_or_path in self._voices:
            v = self._voices[name_or_path]
            path = v.get("audio_path", self._default_ref_audio)
            if not os.path.isabs(path):
                path = os.path.abspath(os.path.join(self._base_dir, path))
            self._voice_override = {
                "audio_path": path,
                "audio_text": v.get("audio_text", self._default_ref_text),
                "language": v.get("language", self._default_lang),
            }
            logger.info("GPT-SoVITS voice set to '%s' (%s)", name_or_path, path)
            return True
        if os.path.exists(name_or_path):
            self._voice_override = {
                "audio_path": os.path.abspath(name_or_path),
                "audio_text": self._default_ref_text,
                "language": self._default_lang,
            }
            logger.info("GPT-SoVITS voice set to custom: %s", name_or_path)
            return True
        logger.warning("Voice '%s' not found", name_or_path)
        return False

    # ── speak ────────────────────────────────────────────────────────────

    def speak(self, text: str, mood: str = None, lang: str = None) -> bool:
        if not _TTS_DEPS:
            logger.debug("TTS deps missing, skipping audio playback")
            return False
        if not text.strip():
            return False

        if lang is None:
            lang = self._default_lang

        # resolve reference audio
        if self._voice_override:
            ref_path = self._voice_override["audio_path"]
            ref_text = self._voice_override["audio_text"]
            ref_lang = self._voice_override["language"]
            speed = self._speed
            temp = self._temperature
        else:
            mood_cfg = self._mood_config.get(mood, {}) if mood else {}
            mood_ref = mood_cfg.get("ref_audio")
            mood_ref_text = mood_cfg.get("ref_text")
            speed = mood_cfg.get("speed_factor", self._speed)
            temp = mood_cfg.get("temperature", self._temperature)

            if mood_ref and mood_ref_text:
                if not os.path.isabs(mood_ref):
                    mood_ref = os.path.abspath(
                        os.path.join(self._base_dir, mood_ref)
                    )
                if os.path.exists(mood_ref):
                    ref_path = mood_ref
                    ref_text = mood_ref_text
                    ref_lang = lang
                    logger.debug("TTS mood '%s' → ref: %s", mood, os.path.basename(mood_ref))
                else:
                    logger.warning("TTS mood '%s' ref not found: %s", mood, mood_ref)
                    ref_path = self._default_ref_audio
                    ref_text = self._default_ref_text
                    ref_lang = lang
            else:
                ref_path = self._default_ref_audio
                ref_text = self._default_ref_text
                ref_lang = lang
                if mood:
                    logger.debug("TTS mood '%s' — speed=%.2f temp=%.2f", mood, speed, temp)

        try:
            params = {
                "text": text,
                "text_lang": ref_lang,
                "ref_audio_path": ref_path,
                "prompt_text": ref_text,
                "prompt_lang": ref_lang,
                "media_type": "wav",
                "streaming_mode": "false",
                "speed_factor": speed,
                "top_k": self._top_k,
                "top_p": self._top_p,
                "temperature": temp,
            }
            response = requests.get(
                self._api_url, params=params, timeout=self._timeout
            )
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

            data, samplerate = sf.read(tmp_path)

            if samplerate != self._target_sr:
                import librosa
                data = librosa.resample(
                    data.T if data.ndim > 1 else data,
                    orig_sr=samplerate,
                    target_sr=self._target_sr,
                )
                if data.ndim > 1:
                    data = data.T
                samplerate = self._target_sr

            if self._async_playback:
                threading.Thread(
                    target=self._play_audio,
                    args=(data.copy(), samplerate, tmp_path),
                    daemon=True,
                ).start()
            else:
                self._play_audio(data, samplerate, tmp_path)
                os.unlink(tmp_path)

            return True

        except requests.exceptions.ConnectionError:
            logger.warning("GPT-SoVITS not reachable on %s", self._api_url)
            return False
        except Exception as e:
            logger.error("TTS error: %s", e)
            return False

    def check_available(self) -> bool:
        try:
            base = self._cfg["api"]["base_url"]
            requests.get(base, timeout=3)
            return True
        except Exception:
            return False

    # ── internal ─────────────────────────────────────────────────────────

    @staticmethod
    def _play_audio(data, samplerate, tmp_path):
        if not _TTS_DEPS:
            return
        try:
            sd.play(data, samplerate)
            sd.wait()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
