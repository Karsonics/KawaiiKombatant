import numpy as np
from typing import Optional

from utils.logging import logger


class VoiceActivityDetector:
    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold
        self._model = None
        self._get_speech_timestamps = None
        self._ready = False

    def _lazy_load(self) -> None:
        if self._ready:
            return
        try:
            from silero_vad import load_silero_vad, get_speech_timestamps
            self._model = load_silero_vad()
            self._get_speech_timestamps = get_speech_timestamps
            self._ready = True
            logger.info("Silero VAD model loaded")
        except ImportError:
            logger.warning("silero_vad not installed — falling back to energy-based VAD")
            self._ready = False

    def has_speech(
        self, audio: np.ndarray, samplerate: int = 16000
    ) -> bool:
        if len(audio) == 0:
            return False
        self._lazy_load()
        if self._ready:
            segments = self._get_speech_timestamps(
                audio, self._model, threshold=self.threshold, sampling_rate=samplerate
            )
            return len(segments) > 0

        rms = np.sqrt(np.mean(audio ** 2))
        return rms > 0.02

    def get_speech_segments(
        self, audio: np.ndarray, samplerate: int = 16000
    ) -> list[dict]:
        self._lazy_load()
        if self._ready:
            return self._get_speech_timestamps(
                audio, self._model, threshold=self.threshold, sampling_rate=samplerate
            )
        rms = np.sqrt(np.mean(audio ** 2))
        if rms > 0.02:
            return [{"start": 0, "end": len(audio)}]
        return []
