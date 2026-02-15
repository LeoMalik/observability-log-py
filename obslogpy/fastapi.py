from __future__ import annotations

import time
from typing import Callable

from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind, StatusCode
from starlette.concurrency import iterate_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .logging import build_body_preview, log_json


class TraceAccessLogMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        logger,
        trace_header_name: str = "X-Trace-Id",
        enable_response_body_preview: bool = False,
        response_body_preview_max_bytes: int = 2048,
        response_body_preview_paths: list[str] | None = None,
        response_body_preview_redact_keys: list[str] | None = None,
    ):
        super().__init__(app)
        self._logger = logger
        self._trace_header_name = trace_header_name
        self._enable_response_body_preview = enable_response_body_preview
        self._response_body_preview_max_bytes = max(response_body_preview_max_bytes, 1)
        self._response_body_preview_paths = [path.strip() for path in (response_body_preview_paths or []) if path and path.strip()]
        self._response_body_preview_redact_keys = response_body_preview_redact_keys or []

    async def dispatch(self, request: Request, call_next: Callable[[Request], Response]) -> Response:
        tracer = trace.get_tracer("observability-log-py/fastapi")
        start = time.perf_counter()
        should_capture_body = self._should_capture_path(request.url.path)
        request_body = await self._extract_request_body(request) if should_capture_body else None
        current_span = trace.get_current_span()
        current_ctx = current_span.get_span_context() if current_span else None
        # When auto instrumentation is enabled, reuse current server span to avoid duplicate server spans.
        if current_ctx and current_ctx.is_valid:
            try:
                response = await call_next(request)
            except Exception as err:
                current_span.record_exception(err)
                current_span.set_status(StatusCode.ERROR, str(err))
                raise
            return await self._finalize_response(current_span, request, response, start, request_body)

        parent_ctx = propagate.extract(request.headers)
        with tracer.start_as_current_span(
            f"{request.method} {request.url.path}",
            context=parent_ctx,
            kind=SpanKind.SERVER,
        ) as span:
            try:
                response = await call_next(request)
            except Exception as err:
                span.record_exception(err)
                span.set_status(StatusCode.ERROR, str(err))
                raise
            return await self._finalize_response(span, request, response, start, request_body)

    async def _finalize_response(
        self,
        span,
        request: Request,
        response: Response,
        start: float,
        request_body: bytes | None,
    ) -> Response:
        duration_ms = (time.perf_counter() - start) * 1000
        span_context = span.get_span_context()
        if span_context and span_context.is_valid:
            response.headers[self._trace_header_name] = format(span_context.trace_id, "032x")

        if response.status_code >= 500:
            span.set_status(StatusCode.ERROR)
        else:
            span.set_status(StatusCode.OK)
        span.set_attribute("http.method", request.method)
        span.set_attribute("http.target", request.url.path)
        span.set_attribute("http.status_code", response.status_code)
        span.set_attribute("http.server_duration_ms", round(duration_ms, 3))

        fields = {
            "http_method": request.method,
            "http_path": request.url.path,
            "http_status": response.status_code,
            "duration_ms": round(duration_ms, 3),
            "user_agent": request.headers.get("user-agent", ""),
        }
        if self._should_capture_path(request.url.path):
            request_preview, request_truncated, request_size = build_body_preview(
                request_body,
                max_bytes=self._response_body_preview_max_bytes,
                redact_keys=self._response_body_preview_redact_keys,
            )
            if request_size > 0:
                fields["http_request_body_size"] = request_size
                span.set_attribute("http_request_body_size", request_size)
            if request_preview:
                fields["http_request_body_preview"] = request_preview
                span.set_attribute("http_request_body_preview", request_preview)
            if request_truncated:
                fields["http_request_body_preview_truncated"] = True
                span.set_attribute("http_request_body_preview_truncated", True)

            body = await self._extract_response_body(response)
            preview, truncated, size = build_body_preview(
                body,
                max_bytes=self._response_body_preview_max_bytes,
                redact_keys=self._response_body_preview_redact_keys,
            )
            if size > 0:
                fields["http_response_body_size"] = size
                span.set_attribute("http_response_body_size", size)
            if preview:
                fields["http_response_body_preview"] = preview
                span.set_attribute("http_response_body_preview", preview)
            if truncated:
                fields["http_response_body_preview_truncated"] = True
                span.set_attribute("http_response_body_preview_truncated", True)

        log_json(
            self._logger,
            "http.request",
            "incoming request handled",
            fields=fields,
        )
        return response

    def _should_capture_path(self, path: str) -> bool:
        if not self._enable_response_body_preview:
            return False
        if not self._response_body_preview_paths:
            return True
        return any(path == allowed or path.startswith(allowed) for allowed in self._response_body_preview_paths)

    async def _extract_request_body(self, request: Request) -> bytes | None:
        try:
            body = await request.body()
        except Exception:
            return None
        return body if body else None

    async def _extract_response_body(self, response: Response) -> bytes | None:
        body = getattr(response, "body", None)
        if body:
            return body if isinstance(body, bytes) else str(body).encode("utf-8", errors="replace")

        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is None:
            return None

        chunks: list[bytes] = []
        async for chunk in body_iterator:
            if isinstance(chunk, bytes):
                chunks.append(chunk)
            else:
                chunks.append(str(chunk).encode("utf-8", errors="replace"))
        merged = b"".join(chunks)
        response.body_iterator = iterate_in_threadpool(iter([merged]))
        return merged

