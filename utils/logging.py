import logging
import sys
from typing import Optional


def setup_logger(name: str = "kawaii", level: Optional[int] = None) -> logging.Logger:
    logger = logging.getLogger(name)

    if level is None:
        level = logging.DEBUG if "-v" in sys.argv or "--verbose" in sys.argv else logging.INFO

    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler("kawaii.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


logger = setup_logger()
