from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Span


@contextmanager
def preserve_otel_parent_span(parent_span: Span | None = None) -> Iterator[None]:
    """Force current OTel context to use the given parent span in this scope."""
    base_span = parent_span or trace.get_current_span()
    token = otel_context.attach(trace.set_span_in_context(base_span))
    try:
        yield
    finally:
        otel_context.detach(token)
