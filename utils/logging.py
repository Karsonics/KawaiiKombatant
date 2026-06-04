import json
import logging
from logging.handlers import RotatingFileHandler
import sys
from typing import Optional


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            default=str,
        )


def _use_json() -> bool:
    return "--log-json" in sys.argv


def setup_logger(name: str = "kawaii", level: Optional[int] = None) -> logging.Logger:
    logger = logging.getLogger(name)

    if level is None:
        level = (
            logging.DEBUG
            if "-v" in sys.argv or "--verbose" in sys.argv
            else logging.INFO
        )

    logger.setLevel(level)

    if logger.handlers:
        return logger

    if _use_json():
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    fh = RotatingFileHandler("kawaii.log", maxBytes=5 * 1024 * 1024, backupCount=3)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


logger = setup_logger()
