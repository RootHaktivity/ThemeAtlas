import logging
import os
import sys
from datetime import datetime

LOG_DIR = os.path.expanduser("~/.local/share/themeatlas/logs")
LOG_FILE = os.path.join(LOG_DIR, f"themeatlas-{datetime.now().strftime('%Y%m%d')}.log")

_RESET = "\033[0m"
_LEVEL_COLORS = {
    logging.DEBUG:    "\033[36m",   # Cyan
    logging.INFO:     "\033[32m",   # Green
    logging.WARNING:  "\033[33m",   # Yellow
    logging.ERROR:    "\033[31m",   # Red
    logging.CRITICAL: "\033[35m",   # Magenta
}


class _ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelno, _RESET)
        record = logging.makeLogRecord(record.__dict__)
        record.levelname = f"{color}{record.levelname}{_RESET}"
        return super().format(record)


def get_logger(name: str = "themeatlas") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # Console handler – INFO and above, coloured
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(_ColorFormatter("[%(levelname)s] %(message)s"))

    # File handler – DEBUG and above, plain text
    os.makedirs(LOG_DIR, exist_ok=True)
    file_h = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    logger.addHandler(console)
    logger.addHandler(file_h)
    return logger
