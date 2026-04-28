"""Application-wide logging: env LOG_LEVEL, stderr, third-party noise reduction."""

from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    """
    Idempotent-friendly setup for API workers. Call after load_dotenv() so LOG_LEVEL applies.

    Uvicorn may install handlers first; ``force=True`` ensures one predictable stream format.
    """
    raw = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, raw, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO

    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, force=True)

    # Chatty HTTP clients / AWS SDK at WARNING unless user runs DEBUG
    if level > logging.DEBUG:
        for name in (
            "httpx",
            "httpcore",
            "botocore",
            "boto3",
            "urllib3",
        ):
            logging.getLogger(name).setLevel(logging.WARNING)
