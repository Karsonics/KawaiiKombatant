import numpy as np
import sounddevice as sd
from typing import Optional

from utils.logging import logger


class MicRecorder:
    def __init__(
        self,
        samplerate: int = 16000,
        channels: int = 1,
        device: Optional[int] = None,
        blocksize: int = 1024,
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.device = device
        self.blocksize = blocksize
        self.frames: list[np.ndarray] = []
        self.stream: Optional[sd.InputStream] = None
        self._recording = False

    def start(self) -> None:
        self.frames = []
        self._recording = True
        self.stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            device=self.device,
            blocksize=self.blocksize,
            callback=self._callback,
        )
        self.stream.start()

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logger.warning("Mic input status: %s", status)
        if self._recording:
            self.frames.append(indata.copy())

    def stop(self) -> np.ndarray:
        self._recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.frames:
            audio = np.concatenate(self.frames, axis=0).flatten()
            return audio.astype(np.float32)
        return np.array([], dtype=np.float32)

    @property
    def is_recording(self) -> bool:
        return self._recording

    @staticmethod
    def list_devices() -> None:
        print(sd.query_devices())

    @staticmethod
    def default_input_device() -> Optional[int]:
        device = sd.query_devices(kind="input")
        return device["index"] if device else None
