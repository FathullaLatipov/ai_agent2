"""Structured JSON logging via structlog, with stdlib bridging and correlation ids."""

import logging
import sys

import structlog

# Tool argument fields configured as sensitive are replaced with this marker.
REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = {"password", "token", "api_key", "secret", "authorization", "credential"}


def _redact_processor(_logger, _method, event_dict: dict) -> dict:
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def configure_logging(env: str = "development") -> None:
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if env == "development":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
