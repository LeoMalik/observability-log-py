from __future__ import annotations

import functools
import inspect
import time
from collections.abc import Mapping
from typing import Any, Callable, ParamSpec, TypeVar, cast

from opentelemetry import trace

from .span import SpanOps

P = ParamSpec("P")
R = TypeVar("R")


def traced_step(
    name: str | None = None,
    *,
    attrs: Mapping[str, Any] | None = None,
    tracer_name: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorate a sync/async function with an OTel span and duration attr.

    Args:
        name: Span name. Defaults to "{module}.{qualname}".
        attrs: Optional static attributes attached to every invocation.
        tracer_name: Optional tracer name. Defaults to function module.
    """

    static_attrs = dict(attrs or {})

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        span_name = name or f"{func.__module__}.{func.__qualname__}"
        resolved_tracer_name = tracer_name or func.__module__
        tracer = trace.get_tracer(resolved_tracer_name)

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                start = time.perf_counter()
                with tracer.start_as_current_span(span_name) as span:
                    if static_attrs:
                        SpanOps(span).attrs(static_attrs)
                    try:
                        result = await cast(Any, func)(*args, **kwargs)
                    except Exception as err:
                        SpanOps(span).duration_ms((time.perf_counter() - start) * 1000).error(err)
                        raise
                    SpanOps(span).duration_ms((time.perf_counter() - start) * 1000).ok()
                    return result

            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            start = time.perf_counter()
            with tracer.start_as_current_span(span_name) as span:
                if static_attrs:
                    SpanOps(span).attrs(static_attrs)
                try:
                    result = func(*args, **kwargs)
                except Exception as err:
                    SpanOps(span).duration_ms((time.perf_counter() - start) * 1000).error(err)
                    raise
                SpanOps(span).duration_ms((time.perf_counter() - start) * 1000).ok()
                return result

        return sync_wrapper

    return decorator
