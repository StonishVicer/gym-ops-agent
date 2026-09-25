"""Structured JSON logging to stderr, with secrets and image data redacted.

Every record passes through `RedactingFilter` before it is formatted, so neither the
API key, an `Authorization` header, nor base64 image data can reach a log line, even
if a future call site logs a request by mistake. Third-party loggers that can echo
request details at DEBUG (`anthropic`, `httpx2`) are capped at WARNING.
"""

import json
import logging
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Final

REDACTED: Final = "[REDACTED]"

_PATTERNS: Final = (
    re.compile(r"sk-or-[A-Za-z0-9_\-]+"),  # OpenRouter keys
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),  # Anthropic keys
    re.compile(r"(?i)(bearer\s+)[^\s\"',}]+"),
    re.compile(r"(?i)(x-api-key[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
    re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"),  # base64 blobs (image data)
)

# Attributes every LogRecord has; anything else came from `extra=` and is emitted.
_STANDARD_ATTRS: Final = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}

NOISY_LOGGERS: Final = ("anthropic", "httpx2", "httpx", "httpcore")


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(
            lambda m: (m.group(1) if m.groups() else "") + REDACTED,
            text,
        )
    return text


def _redact_value(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {k: _redact_value(v, secrets) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value(v, secrets) for v in value]
    return value


class RedactingFilter(logging.Filter):
    """Scrub known secrets and secret-shaped strings from message, args and extras."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = tuple(s for s in secrets if s)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self.secrets)
        record.args = None
        for key, value in list(record.__dict__.items()):
            if key not in _STANDARD_ATTRS:
                setattr(record, key, _redact_value(value, self.secrets))
        if record.exc_info:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, default=str, ensure_ascii=False)


class StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever `sys.stderr` is at emit time, not at construction time."""

    @property
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, _value: Any) -> None:
        pass


def configure_logging(secrets: Iterable[str] = (), level: int = logging.INFO) -> logging.Handler:
    """Install one JSON stderr handler on the root logger; returns it for tests/teardown."""
    handler = StderrHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter(secrets))
    root = logging.getLogger()
    for old in [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]:
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(level)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler
