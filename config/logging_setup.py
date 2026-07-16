"""
Logging setup - console + file output.

Mirrors daily-briefing-agent/config/logging_setup.py: a single configured
logger shared by every module, writing to console + outputs/logs/.
"""

import logging
import os
from datetime import datetime

_CONFIGURED = False
LOG_DIR = os.path.join("outputs", "logs")


def setup_logging(to_file: bool = True) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("shopify_ops")

    if _CONFIGURED:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    if to_file:
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = os.path.join(LOG_DIR, f"run_{stamp}.log")
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
        logger.info("Logging to file: %s", path)

    _CONFIGURED = True
    return logger


def get_logger() -> logging.Logger:
    if not _CONFIGURED:
        return setup_logging()
    return logging.getLogger("shopify_ops")
