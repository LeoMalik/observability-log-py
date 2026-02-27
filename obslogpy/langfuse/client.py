from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator

from .config import LangfuseSettings

logger = logging.getLogger(__name__)

try:
    from langfuse import Langfuse, propagate_attributes as _propagate_attributes  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    Langfuse = None  # type: ignore[assignment]
    _propagate_attributes = None  # type: ignore[assignment]


@lru_cache(maxsize=1)
def _cached_default_settings() -> LangfuseSettings:
    return LangfuseSettings.from_env()


@lru_cache(maxsize=8)
def _cached_client(settings: LangfuseSettings):
    if not settings.is_configured_for_tracing():
        return None
    if Langfuse is None:
        logger.warning("Langfuse SDK not installed; tracing disabled")
        return None

    try:
        from opentelemetry.sdk.trace import TracerProvider
    except Exception:
        logger.warning("opentelemetry-sdk not installed; Langfuse tracing disabled")
        return None

    tracer_provider = TracerProvider()
    return Langfuse(
        public_key=settings.public_key,
        secret_key=settings.secret_key,
        base_url=settings.host,
        tracing_enabled=True,
        tracer_provider=tracer_provider,
    )


def get_langfuse(settings: LangfuseSettings | None = None):
    resolved = settings or _cached_default_settings()
    return _cached_client(resolved)


def langfuse_flush_at_request_end(settings: LangfuseSettings | None = None) -> bool:
    resolved = settings or _cached_default_settings()
    return bool(resolved.flush_at_request_end)


def set_langfuse_trace_attributes(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    settings: LangfuseSettings | None = None,
) -> None:
    langfuse = get_langfuse(settings)
    if langfuse is None:
        return

    if session_id and _propagate_attributes is not None:
        try:
            _propagate_attributes(session_id=session_id)
        except Exception:
            logger.warning("Langfuse propagate_attributes failed", exc_info=True)

    if user_id or session_id:
        try:
            payload: dict[str, str] = {}
            if user_id:
                payload["user_id"] = user_id
            if session_id:
                payload["session_id"] = session_id
            langfuse.update_current_trace(**payload)
        except Exception:
            logger.warning("Langfuse update_current_trace failed", exc_info=True)


@contextmanager
def open_span(
    *,
    name: str,
    trace_context: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    langfuse: Any | None = None,
    settings: LangfuseSettings | None = None,
) -> Iterator[Any | None]:
    """Open a Langfuse span when tracing is available, otherwise no-op."""
    langfuse = langfuse or get_langfuse(settings)
    if langfuse is None:
        yield None
        return

    with langfuse.start_as_current_span(
        name=name,
        trace_context=trace_context,
        metadata=metadata,
    ) as span:
        yield span


@contextmanager
def open_observation(
    *,
    as_type: str,
    name: str,
    model: str | None = None,
    input: dict[str, Any] | None = None,
    model_parameters: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    langfuse: Any | None = None,
    settings: LangfuseSettings | None = None,
) -> Iterator[Any | None]:
    """Open a Langfuse observation when tracing is available, otherwise no-op."""
    langfuse = langfuse or get_langfuse(settings)
    if langfuse is None:
        yield None
        return

    with langfuse.start_as_current_observation(
        as_type=as_type,
        name=name,
        model=model,
        input=input,
        model_parameters=model_parameters,
        metadata=metadata,
    ) as observation:
        yield observation
