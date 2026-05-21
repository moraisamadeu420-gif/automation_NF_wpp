"""
app/core/logging.py
Structured logging configuration using loguru.
"""
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.core.config import settings


def configure_logging() -> None:
    logger.remove()

    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | <cyan>{name}:{line}</cyan> | {message}",
        level=settings.log_level,
        colorize=True,
        backtrace=settings.debug,
        diagnose=settings.debug,
    )

    log_file = Path(settings.logs_dir) / f"nfse_{datetime.now().strftime('%Y%m%d')}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger.add(
        str(log_file),
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} | {message}",
        level="INFO",
        rotation="00:00",
        retention="7 days",
        encoding="utf-8",
        backtrace=True,
        diagnose=False,
    )

    logger.debug("Logging configured — file: {}", log_file)
