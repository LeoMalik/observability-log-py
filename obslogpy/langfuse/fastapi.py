from __future__ import annotations

import json
import logging
import re
from typing import Callable

from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .client import (
    get_langfuse,
    langfuse_flush_at_request_end,
    open_span,
    set_langfuse_trace_attributes,
)
from .config import LangfuseSettings
from .context import preserve_otel_parent_span

logger = logging.getLogger(__name__)

_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_WRITE_METHODS = {"POST", "PUT", "PATCH"}
_MAX_SESSION_ID_LENGTH = 200


def resolve_langfuse_trace_id(
    *,
    x_trace_id: str | None,
    otel_trace_id: str | None,
) -> tuple[str | None, str | None]:
    upstream_raw = (x_trace_id or "").strip()
    if upstream_raw:
        candidate = upstream_raw.lower()
        if _TRACE_ID_PATTERN.fullmatch(candidate):
            return candidate, None
        return (otel_trace_id.strip().lower() if otel_trace_id else None), upstream_raw

    if not otel_trace_id:
        return None, None
    return otel_trace_id.strip().lower(), None


def resolve_langfuse_session_id(x_session_id: str | None) -> tuple[str | None, str | None]:
    upstream_raw = (x_session_id or "").strip()
    if not upstream_raw:
        return None, None
    if len(upstream_raw) <= _MAX_SESSION_ID_LENGTH and upstream_raw.isascii():
        return upstream_raw, None
    return None, upstream_raw


def extract_trace_attrs_from_body(body: bytes, content_type: str | None) -> tuple[str | None, str | None]:
    if not body:
        return None, None
    if "application/json" not in (content_type or "").lower():
        return None, None
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(payload, dict):
        return None, None

    user_id = payload.get("user_id")
    session_id = payload.get("session_id")
    if session_id is None:
        session_id = payload.get("sessionId")

    resolved_user_id = str(user_id) if user_id is not None else None
    resolved_session_id = str(session_id) if session_id is not None else None
    return resolved_user_id, resolved_session_id


def extract_user_id_from_body(body: bytes, content_type: str | None) -> str | None:
    """Compatibility shim for existing callers/tests."""
    user_id, _ = extract_trace_attrs_from_body(body, content_type)
    return user_id


def _restore_request_body(request: Request, body: bytes) -> None:
    sent = False

    async def receive() -> dict:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive  # type: ignore[attr-defined]


def _current_otel_trace_id() -> str | None:
    span = trace.get_current_span()
    if span is None:
        return None
    ctx = span.get_span_context()
    if not ctx or not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")


class LangfuseTraceMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        trace_header: str = "X-Trace-Id",
        session_header: str = "X-Session-Id",
        settings: LangfuseSettings | None = None,
    ) -> None:
        super().__init__(app)
        self._trace_header = trace_header
        self._session_header = session_header
        self._settings = settings

    async def dispatch(self, request: Request, call_next: Callable[[Request], Response]) -> Response:
        langfuse = get_langfuse(self._settings)
        if langfuse is None:
            return await call_next(request)

        otel_parent_span = trace.get_current_span()
        x_trace_id = request.headers.get(self._trace_header)
        otel_trace_id = _current_otel_trace_id()
        trace_id, upstream_raw = resolve_langfuse_trace_id(
            x_trace_id=x_trace_id,
            otel_trace_id=otel_trace_id,
        )
        session_id, upstream_session_raw = resolve_langfuse_session_id(
            request.headers.get(self._session_header)
        )

        user_id: str | None = None
        body_session_id: str | None = None
        if request.method in _WRITE_METHODS:
            body = await request.body()
            _restore_request_body(request, body)
            user_id, body_session_id = extract_trace_attrs_from_body(
                body, request.headers.get("content-type")
            )

        if not session_id and body_session_id:
            session_id, upstream_session_raw = resolve_langfuse_session_id(body_session_id)

        if not trace_id:
            return await call_next(request)

        metadata: dict = {
            "http.method": request.method,
            "http.path": request.url.path,
        }
        if upstream_raw:
            metadata["upstream_trace_id_raw"] = upstream_raw
        if session_id:
            metadata["session_id"] = session_id
        if upstream_session_raw:
            metadata["upstream_session_id_raw"] = upstream_session_raw

        flush_at_end = langfuse_flush_at_request_end(self._settings)

        with open_span(
            name=f"{request.method} {request.url.path}",
            trace_context={"trace_id": trace_id},
            metadata=metadata,
            langfuse=langfuse,
            settings=self._settings,
        ):
            set_langfuse_trace_attributes(
                user_id=user_id,
                session_id=session_id,
                settings=self._settings,
            )
            try:
                with preserve_otel_parent_span(otel_parent_span):
                    response = await call_next(request)
                return response
            except Exception as err:
                try:
                    langfuse.update_current_span(
                        level="ERROR",
                        status_message=str(err),
                        metadata={"exception.type": type(err).__name__},
                    )
                except Exception:
                    logger.warning("Langfuse update_current_span failed", exc_info=True)
                raise
            finally:
                if flush_at_end:
                    try:
                        langfuse.flush()
                    except Exception:
                        logger.warning("Langfuse flush failed", exc_info=True)


def add_langfuse_tracing(
    app,
    *,
    settings: LangfuseSettings | None = None,
    trace_header: str = "X-Trace-Id",
    session_header: str = "X-Session-Id",
) -> bool:
    """One-liner API for business apps to enable Langfuse request tracing."""
    resolved_settings = settings or LangfuseSettings.from_env()
    if not resolved_settings.is_configured_for_tracing():
        return False

    app.add_middleware(
        LangfuseTraceMiddleware,
        trace_header=trace_header,
        session_header=session_header,
        settings=resolved_settings,
    )
    return True
