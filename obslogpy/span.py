from __future__ import annotations

from typing import Any, Mapping

from opentelemetry.trace import StatusCode


class SpanAttrKeys:
    DURATION_MS = "duration_ms"
    ERROR_CODE = "error_code"
    ERROR_MESSAGE = "error_message"
    DEPENDENCY_TYPE = "dependency.type"
    DEPENDENCY_NAME = "dependency.name"
    DEPENDENCY_WEBSITE = "dependency.website"
    DEPENDENCY_DURATION_MS = "dependency.duration_ms"


def set_span_attrs(span, attrs: Mapping[str, Any]) -> None:
    for key, value in attrs.items():
        if value is None:
            continue
        span.set_attribute(key, value)


def set_dependency_http_attrs(
    span,
    *,
    name: str,
    website: str | None = None,
    duration_ms: float | None = None,
) -> None:
    payload: dict[str, Any] = {
        SpanAttrKeys.DEPENDENCY_TYPE: "http",
        SpanAttrKeys.DEPENDENCY_NAME: name,
    }
    if website:
        payload[SpanAttrKeys.DEPENDENCY_WEBSITE] = website
    if duration_ms is not None:
        payload[SpanAttrKeys.DEPENDENCY_DURATION_MS] = round(float(duration_ms), 3)
    set_span_attrs(span, payload)


class SpanOps:
    def __init__(self, span) -> None:
        self._span = span

    def attrs(self, attrs: Mapping[str, Any]) -> SpanOps:
        set_span_attrs(self._span, attrs)
        return self

    def duration_ms(self, value: float, *, key: str = SpanAttrKeys.DURATION_MS) -> SpanOps:
        self._span.set_attribute(key, round(float(value), 3))
        return self

    def ok(self) -> SpanOps:
        self._span.set_status(StatusCode.OK)
        return self

    def error(
        self,
        err: Exception | str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> SpanOps:
        if isinstance(err, Exception):
            self._span.record_exception(err)
            default_message = str(err)
        else:
            default_message = str(err)
        self._span.set_status(StatusCode.ERROR, default_message)
        payload: dict[str, Any] = {}
        if error_code:
            payload[SpanAttrKeys.ERROR_CODE] = error_code
        resolved_message = error_message or default_message
        if resolved_message:
            payload[SpanAttrKeys.ERROR_MESSAGE] = resolved_message
        if payload:
            set_span_attrs(self._span, payload)
        return self
