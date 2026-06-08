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


class F5TTSBackend(TTSBackend):

    def __init__(self, config_path: str = None) -> None:
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "..",
                "configs", "sovits_config.yaml",
            )
        with open(config_path, "r") as f:
            self._cfg = yaml.safe_load(f)

        self._base_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )

        f5 = self._cfg.get("f5_tts", {})
        self._api_url = f5.get("base_url", "http://localhost:5050/v1/tts")
        self._timeout = f5.get("timeout", 120)
        self._nfe_steps = f5.get("nfe_steps", 32)

        ref = self._cfg["reference"]
        ref_path = ref["audio_path"]
        if not os.path.isabs(ref_path):
            ref_path = os.path.abspath(os.path.join(self._base_dir, ref_path))
        self._default_ref_audio = ref_path
        self._default_ref_text = ref["audio_text"]
        self._default_lang = ref["language"]

        out = self._cfg["output"]
        self._target_sr = out["sampling_rate"]
        self._async_playback = out["async_playback"]

        self._mood_config = self._cfg.get("moods", {})
        self._voice_override: dict | None = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "F5-TTS"

    def set_voice(self, name_or_path: str) -> bool:
        # For F5-TTS, voice is determined by ref_audio. Store as override.
        voices = self._cfg.get("voices", {})
        if name_or_path in voices:
            v = voices[name_or_path]
            path = v.get("audio_path", self._default_ref_audio)
            if not os.path.isabs(path):
                path = os.path.abspath(os.path.join(self._base_dir, path))
            self._voice_override = {
                "audio_path": path,
                "audio_text": v.get("audio_text", self._default_ref_text),
            }
            logger.info("F5-TTS voice set to '%s'", name_or_path)
            return True
        if os.path.exists(name_or_path):
            self._voice_override = {
                "audio_path": os.path.abspath(name_or_path),
                "audio_text": self._default_ref_text,
            }
            return True
        return False

    def speak(self, text: str, mood: str = None, lang: str = None) -> bool:
        if not _TTS_DEPS:
            logger.debug("TTS deps missing, skipping audio playback")
            return False
        if not text.strip():
            return False

        # resolve reference audio
        if self._voice_override:
            ref_path = self._voice_override["audio_path"]
            ref_text = self._voice_override["audio_text"]
        else:
            mood_cfg = self._mood_config.get(mood, {}) if mood else {}
            mood_ref = mood_cfg.get("ref_audio")
            mood_ref_text = mood_cfg.get("ref_text")

            if mood_ref and mood_ref_text:
                if not os.path.isabs(mood_ref):
                    mood_ref = os.path.abspath(
                        os.path.join(self._base_dir, mood_ref)
                    )
                if os.path.exists(mood_ref):
                    ref_path = mood_ref
                    ref_text = mood_ref_text
                    logger.debug("F5-TTS mood '%s' → ref: %s", mood, os.path.basename(mood_ref))
                else:
                    ref_path = self._default_ref_audio
                    ref_text = self._default_ref_text
            else:
                ref_path = self._default_ref_audio
                ref_text = self._default_ref_text

        try:
            payload = {
                "text": text,
                "ref_audio_path": ref_path,
                "ref_text": ref_text,
                "nfe_steps": self._nfe_steps,
                "speed": 1.0,
            }
            response = requests.post(
                self._api_url, json=payload, timeout=self._timeout
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
            logger.warning("F5-TTS not reachable on %s", self._api_url)
            return False
        except Exception as e:
            logger.error("F5-TTS error: %s", e)
            return False

    def check_available(self) -> bool:
        try:
            base = self._cfg.get("f5_tts", {}).get("base_url", "http://localhost:5050")
            requests.get(base.replace("/v1/tts", "/health"), timeout=3)
            return True
        except Exception:
            return False

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
