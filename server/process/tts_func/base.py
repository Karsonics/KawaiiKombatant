from abc import ABC, abstractmethod


class TTSBackend(ABC):

    @abstractmethod
    def speak(self, text: str, mood: str = None, lang: str = None) -> bool:
        ...

    @abstractmethod
    def check_available(self) -> bool:
        ...

    @abstractmethod
    def set_voice(self, name_or_path: str) -> bool:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...
