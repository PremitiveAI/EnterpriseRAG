"""Structured logging with PII redaction (spec §39, docs/security/pii-handling.md).

Redaction runs as a logging *filter*, not at call sites. Relying on every
developer to remember to mask fails the first time someone adds a debug line;
the filter runs on every record regardless of how the value arrived.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

from config.settings import settings

# Correlation ids, set by RequestIDMiddleware and by Celery tasks.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

# --- PII patterns ------------------------------------------------------- #
# Extend this list when a document type with a new identifier format is added.
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Aadhaar: 12 digits, optionally spaced or hyphenated in groups of four.
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "aadhaar"),
    # PAN: five letters, four digits, one letter.
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "pan"),
]

_PII_KEYS = {
    "password", "token", "access_token", "refresh_token", "secret", "api_key",
    "authorization", "jwt_secret", "gemini_api_key",
    "pan", "pan_number", "aadhaar", "aadhaar_number", "aadhar_number",
    "date_of_birth", "dob", "father_name",
}

_REDACTED = "<redacted>"


def mask_identifier(value: str, keep: int = 4) -> str:
    """``123456789012`` -> ``********9012``."""
    if not value:
        return ""
    keep = min(keep, len(value))
    return "*" * (len(value) - keep) + value[len(value) - keep:]


def scrub_text(text: str) -> str:
    """Mask any identifier pattern appearing in free text."""
    for pattern, _label in _PII_PATTERNS:
        text = pattern.sub(lambda m: mask_identifier(m.group(0)), text)
    return text


def scrub_value(key: str, value: Any) -> Any:
    """Redact by key name, then by pattern."""
    if key.lower() in _PII_KEYS:
        return _REDACTED
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {k: scrub_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_value(key, v) for v in value]
    return value


class PIIRedactionFilter(logging.Filter):
    """Scrubs the message, the arguments and any structured extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub_text(record.msg)

        if isinstance(record.args, dict):
            record.args = {k: scrub_value(k, v) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(scrub_value("", a) for a in record.args)

        # Tracebacks can carry a repr containing an identifier.
        if record.exc_text:
            record.exc_text = scrub_text(record.exc_text)

        for key in list(vars(record)):
            if key not in _RESERVED:
                setattr(record, key, scrub_value(key, getattr(record, key)))

        return True


_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {
    "message", "asctime", "taskName",
}


class JSONFormatter(logging.Formatter):
    """One JSON object per line, with the fields §39 asks for."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if (rid := request_id_var.get()) is not None:
            payload["request_id"] = rid
        if (uid := user_id_var.get()) is not None:
            payload["user_id"] = uid

        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = scrub_text(self.formatException(record.exc_info))

        return json.dumps(payload, default=str)


def configure_logging() -> None:
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter: logging.Formatter = (
        JSONFormatter()
        if settings.LOG_JSON
        else logging.Formatter("%(asctime)s %(levelname)-8s %(name)s :: %(message)s")
    )
    pii_filter = PIIRedactionFilter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(pii_filter)

    app_file = RotatingFileHandler(
        settings.LOG_DIR / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    app_file.setFormatter(formatter)
    app_file.addFilter(pii_filter)

    error_file = RotatingFileHandler(
        settings.LOG_DIR / "errors.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    error_file.setLevel(logging.ERROR)
    error_file.setFormatter(formatter)
    error_file.addFilter(pii_filter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.LOG_LEVEL)
    for handler in (console, app_file, error_file):
        root.addHandler(handler)

    # SQLAlchemy echo is controlled by DB_ECHO, not by the root level.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.DB_ECHO else logging.WARNING
    )
    logging.getLogger("uvicorn.access").handlers.clear()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
