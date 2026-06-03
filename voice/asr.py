from __future__ import annotations

import numpy as np
from typing import Optional

from utils.logging import logger


class WhisperTranscriber:
    def __init__(self, model_size: str = "base", device: Optional[str] = None) -> None:
        self.model_size = model_size
        self._device = device
        self._model = None

    def _lazy_load(self) -> None:
        if self._model is not None:
            return
        try:
            import whisper
            self._model = whisper.load_model(
                self.model_size,
                device=self._device,
            )
            logger.info("Whisper model '%s' loaded (device: %s)", self.model_size, self._device or "auto")
        except ImportError:
            raise RuntimeError(
                "openai-whisper not installed. Install with: pip install openai-whisper"
            )

    def transcribe(
        self,
        audio: np.ndarray,
        language: Optional[str] = "en",
        task: str = "transcribe",
    ) -> str:
        self._lazy_load()
        result = self._model.transcribe(
            audio,
            language=language,
            task=task,
        )
        text = result.get("text", "").strip()
        if text:
            logger.info("ASR: %s", text[:80])
        return text
