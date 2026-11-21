"""Structured logging (structlog -> JSON on stdout).

* Context (request_id, investigation_id, job_id) is bound via contextvars, so every
  log line inside a request or job carries it without passing loggers around.
* A redaction processor masks values whose *key* looks sensitive, as a last line of
  defence. The primary rule is still: never log secrets, prompts or customer PII.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from .tracing import add_trace_ids

_SENSITIVE_KEY = re.compile(r"pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential", re.I)
_REDACTED = "***"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (_REDACTED if _SENSITIVE_KEY.search(str(k)) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]
    return value


def redact_sensitive(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if key == "event":
            continue
        event_dict[key] = _REDACTED if _SENSITIVE_KEY.search(key) else _redact(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive,
        add_trace_ids,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping().get(level.upper(), 20)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    # Route stdlib loggers (uvicorn, sqlalchemy, neo4j) through a plain handler at the same level.
    logging.basicConfig(level=level.upper(), stream=sys.stdout, format="%(message)s", force=True)
    for noisy in ("neo4j", "sqlalchemy.engine", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
