"""Structured JSON logging."""

from __future__ import annotations

import logging
import re

import structlog

from caretrace.config import get_settings

_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_NAME_HINT = re.compile(r"\b(patient|member)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?", re.IGNORECASE)


def redact_phi(value: object) -> object:
    """Redact SSN-like patterns and simple patient-name labels from log values."""
    if not isinstance(value, str):
        return value
    text = _SSN.sub("[REDACTED-SSN]", value)
    return _NAME_HINT.sub(lambda m: m.group(1) + " [REDACTED-NAME]", text)


def _redact_processor(
    _logger: object, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    return {key: redact_phi(val) for key, val in event_dict.items()}


def setup_logging() -> None:
    """Configure structlog to emit JSON logs on stderr."""
    settings = get_settings()
    logging.basicConfig(
        format="%(message)s", level=getattr(logging, settings.log_level.upper(), logging.INFO)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structured logger."""
    setup_logging()
    return structlog.get_logger(name)
