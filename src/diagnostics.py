"""Persistent, bounded diagnostics for Finder-launched app builds."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading

LOG_FILE = Path.home() / "Library" / "Logs" / "SpeakrFlow" / "app.log"
logger = logging.getLogger("speakrflow")


def configure_logging():
    if logger.handlers:
        return
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    def uncaught(exc_type, exc_value, exc_traceback):
        logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    def thread_uncaught(args):
        uncaught(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = uncaught
    threading.excepthook = thread_uncaught
    logger.info("Starting pid=%s executable=%s source=%s", os.getpid(), sys.executable, __file__)
