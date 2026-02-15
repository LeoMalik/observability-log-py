import json
import os
from datetime import datetime, timezone
from logging import Logger
from typing import Any

from opentelemetry import trace

DEFAULT_REDACT_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_token",
    "api_key",
}


def _trace_fields() -> dict[str, str]:
    span = trace.get_current_span()
    if span is None:
        return {}
    span_context = span.get_span_context()
    if not span_context or not span_context.is_valid:
        return {}
    return {
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
    }


def build_payload(
    method_name: str,
    detail: str,
    *,
    level: str = "info",
    application_name: str | None = None,
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "application_name": application_name or os.getenv("OTEL_SERVICE_NAME", "mail-mvp"),
        "method_name": method_name,
        "detail": detail,
        "time": datetime.now(timezone.utc).isoformat(),
        "level": level,
    }
    payload.update(_trace_fields())
    if fields:
        payload.update(fields)
    return payload


def build_body_preview(
    body: bytes | str | None,
    *,
    max_bytes: int = 2048,
    redact_keys: set[str] | list[str] | None = None,
) -> tuple[str, bool, int]:
    if body is None:
        return "", False, 0

    if isinstance(body, bytes):
        raw = body
    else:
        raw = body.encode("utf-8", errors="replace")

    body_size = len(raw)
    if body_size == 0:
        return "", False, 0

    sanitized = _sanitize_body(raw, redact_keys)
    limit = max(max_bytes, 1)
    truncated = len(sanitized) > limit
    if truncated:
        sanitized = sanitized[:limit]
    return sanitized.decode("utf-8", errors="replace"), truncated, body_size


def log_json(
    logger: Logger,
    method_name: str,
    detail: str,
    *,
    level: str = "info",
    application_name: str | None = None,
    fields: dict[str, Any] | None = None,
) -> None:
    payload = build_payload(
        method_name,
        detail,
        level=level,
        application_name=application_name,
        fields=fields,
    )
    line = json.dumps(payload, ensure_ascii=False, default=str)
    level_name = level.lower()
    if level_name == "error":
        logger.error(line)
    elif level_name in ("warn", "warning"):
        logger.warning(line)
    elif level_name == "debug":
        logger.debug(line)
    else:
        logger.info(line)


def _sanitize_body(raw: bytes, redact_keys: set[str] | list[str] | None) -> bytes:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        return raw

    keys = set(k.lower().strip() for k in (redact_keys or DEFAULT_REDACT_KEYS) if k)
    _sanitize_value(obj, keys)
    try:
        return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
    except Exception:
        return raw


def _sanitize_value(value: Any, redact_keys: set[str]) -> None:
    if isinstance(value, dict):
        for key in list(value.keys()):
            normalized = str(key).lower().strip()
            if _should_redact(normalized, redact_keys):
                value[key] = "***"
                continue
            _sanitize_value(value[key], redact_keys)
    elif isinstance(value, list):
        for item in value:
            _sanitize_value(item, redact_keys)


def _should_redact(key: str, redact_keys: set[str]) -> bool:
    if not key:
        return False
    if key in redact_keys:
        return True
    return any(candidate in key for candidate in redact_keys)

