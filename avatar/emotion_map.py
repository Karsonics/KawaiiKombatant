import os
import random
import yaml
from typing import Optional

from utils.logging import logger


class MoodConfig:
    def __init__(self, config_path: str = "configs/vtube_config.yaml") -> None:
        self._config_path = config_path
        self._raw: dict = self._load_config(config_path)
        self._apply_env_overrides()

    @staticmethod
    def _load_config(path: str) -> dict:
        if not os.path.isabs(path):
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(base, path)
        with open(path) as f:
            return yaml.safe_load(f)

    def _apply_env_overrides(self) -> None:
        overrides = {
            ("vts", "host"): "VTS_HOST",
            ("vts", "port"): "VTS_PORT",
            ("kuro_api", "host"): "KAPI_HOST",
            ("kuro_api", "port"): "KAPI_PORT",
        }
        for (section, key), env in overrides.items():
            val = os.environ.get(env)
            if val is not None:
                if key == "port":
                    val = int(val)
                self._raw.setdefault(section, {})[key] = val

    @property
    def vts_host(self) -> str:
        return self._raw["vts"]["host"]

    @property
    def vts_port(self) -> int:
        return int(self._raw["vts"]["port"])

    @property
    def kapi_host(self) -> str:
        return self._raw["kuro_api"]["host"]

    @property
    def kapi_port(self) -> int:
        return int(self._raw["kuro_api"]["port"])

    @property
    def plugin_name(self) -> str:
        return self._raw["vts"]["plugin_name"]

    @property
    def plugin_developer(self) -> str:
        return self._raw["vts"]["plugin_developer"]

    @property
    def model_id(self) -> str:
        return self._raw.get("model_id", "")

    def get_expression(self, mood: str) -> str:
        expr = self._raw.get("expressions", {})
        default = expr.get("neutral", "Default")
        return expr.get(mood, default)

    def random_idle(self) -> Optional[str]:
        pool = self._raw.get("idle", {}).get("animations", [])
        if not pool:
            return None
        return random.choice(pool)

    @property
    def idle_interval(self) -> int:
        return int(self._raw.get("idle", {}).get("interval_seconds", 20))
