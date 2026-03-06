from __future__ import annotations

import os
import time

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.trace import SpanKind, StatusCode

from .logging import DEFAULT_REDACT_KEYS, build_body_preview, log_json


class TraceAccessLogMiddleware:
    """Pure ASGI middleware for HTTP observability.

    Creates OpenTelemetry SERVER spans from incoming ``traceparent`` headers,
    injects an ``X-Trace-Id`` response header, emits structured access logs,
    and optionally captures request / response body previews.

    This implementation does **not** inherit from Starlette's
    ``BaseHTTPMiddleware``, which avoids the well-known issue where stacking
    multiple ``BaseHTTPMiddleware`` layers with ``StreamingResponse`` causes
    ``RuntimeError: Unexpected message received: http.request`` under real
    ASGI servers (uvicorn / hypercorn).
    """

    def __init__(
        self,
        app,
        logger=None,
        trace_header_name: str = "X-Trace-Id",
        enable_response_body_preview: bool = False,
        response_body_preview_max_bytes: int = 2048,
        response_body_preview_paths: list[str] | None = None,
        response_body_preview_redact_keys: list[str] | None = None,
    ):
        self.app = app
        self._logger = logger
        self._trace_header_name = trace_header_name
        self._enable_response_body_preview = enable_response_body_preview
        self._response_body_preview_max_bytes = max(response_body_preview_max_bytes, 1)
        self._response_body_preview_paths = [
            path.strip()
            for path in (response_body_preview_paths or [])
            if path and path.strip()
        ]
        self._response_body_preview_redact_keys = response_body_preview_redact_keys or []

    # ------------------------------------------------------------------
    # ASGI entry point
    # ------------------------------------------------------------------

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "/")

        # Build a plain dict of headers for propagate.extract & user-agent.
        headers_dict: dict[str, str] = {}
        user_agent = ""
        for raw_key, raw_val in scope.get("headers", []):
            name = raw_key.decode("latin-1").lower()
            value = raw_val.decode("latin-1")
            headers_dict[name] = value
            if name == "user-agent":
                user_agent = value

        should_capture = self._should_capture_path(path)
        start = time.perf_counter()

        # Optionally buffer request body for preview.
        request_body: bytes | None = None
        if should_capture:
            request_body, receive = await self._buffer_request_body(receive)

        # If there is already a valid span (e.g. from auto-instrumentation),
        # reuse it instead of creating a duplicate server span.
        current_span = trace.get_current_span()
        current_ctx = current_span.get_span_context() if current_span else None

        if current_ctx and current_ctx.is_valid:
            status_code, response_body = await self._traced_call(
                scope, receive, send, current_span, should_capture,
            )
        else:
            parent_ctx = propagate.extract(headers_dict)
            tracer = trace.get_tracer("observability-log-py/fastapi")
            with tracer.start_as_current_span(
                f"{method} {path}",
                context=parent_ctx,
                kind=SpanKind.SERVER,
            ) as span:
                status_code, response_body = await self._traced_call(
                    scope, receive, send, span, should_capture,
                )

        # Structured access log (always, regardless of body capture).
        duration_ms = round((time.perf_counter() - start) * 1000, 3)
        self._emit_access_log(
            method, path, status_code, duration_ms, user_agent,
            request_body, response_body, should_capture,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _traced_call(self, scope, receive, send, span, should_capture):
        """Run the downstream ASGI app, intercept response start/body."""
        status_code = 500
        response_body_chunks: list[bytes] = []
        trace_header_name_lower = self._trace_header_name.lower().encode("latin-1")

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
                # Inject X-Trace-Id header.
                span_ctx = span.get_span_context()
                if span_ctx and span_ctx.is_valid:
                    trace_id_bytes = format(span_ctx.trace_id, "032x").encode("latin-1")
                    headers = list(message.get("headers", []))
                    headers.append((trace_header_name_lower, trace_id_bytes))
                    message = {**message, "headers": headers}
            elif message["type"] == "http.response.body" and should_capture:
                body = message.get("body", b"")
                if body:
                    response_body_chunks.append(body)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as err:
            span.record_exception(err)
            span.set_status(StatusCode.ERROR, str(err))
            raise

        # Finalize span attributes.
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        start = scope.get("_obs_start")  # not used; duration computed by caller
        if status_code >= 500:
            span.set_status(StatusCode.ERROR)
        else:
            span.set_status(StatusCode.OK)
        span.set_attribute("http.method", method)
        span.set_attribute("http.target", path)
        span.set_attribute("http.status_code", status_code)

        merged_body = b"".join(response_body_chunks) if response_body_chunks else None
        return status_code, merged_body

    async def _buffer_request_body(self, receive):
        """Read all request body chunks and return (body, replay_receive)."""
        body_chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                if chunk:
                    body_chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break
            else:
                break

        full_body = b"".join(body_chunks)

        # Create a receive callable that replays the body once, then
        # delegates to the original receive for http.disconnect.
        body_sent = False

        async def replay_receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": full_body, "more_body": False}
            return await receive()

        return full_body, replay_receive

    def _should_capture_path(self, path: str) -> bool:
        if not self._enable_response_body_preview:
            return False
        if not self._response_body_preview_paths:
            return True
        return any(
            path == allowed or path.startswith(allowed)
            for allowed in self._response_body_preview_paths
        )

    def _emit_access_log(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        user_agent: str,
        request_body: bytes | None,
        response_body: bytes | None,
        should_capture: bool,
    ) -> None:
        if self._logger is None:
            return

        fields: dict = {
            "http_method": method,
            "http_path": path,
            "http_status": status_code,
            "duration_ms": duration_ms,
            "user_agent": user_agent,
        }

        span = trace.get_current_span()

        if should_capture:
            # Request body preview.
            req_preview, req_truncated, req_size = build_body_preview(
                request_body,
                max_bytes=self._response_body_preview_max_bytes,
                redact_keys=self._response_body_preview_redact_keys,
            )
            if req_size > 0:
                fields["http_request_body_size"] = req_size
                if span:
                    span.set_attribute("http_request_body_size", req_size)
            if req_preview:
                fields["http_request_body_preview"] = req_preview
                if span:
                    span.set_attribute("http_request_body_preview", req_preview)
            if req_truncated:
                fields["http_request_body_preview_truncated"] = True
                if span:
                    span.set_attribute("http_request_body_preview_truncated", True)

            # Response body preview.
            resp_preview, resp_truncated, resp_size = build_body_preview(
                response_body,
                max_bytes=self._response_body_preview_max_bytes,
                redact_keys=self._response_body_preview_redact_keys,
            )
            if resp_size > 0:
                fields["http_response_body_size"] = resp_size
                if span:
                    span.set_attribute("http_response_body_size", resp_size)
            if resp_preview:
                fields["http_response_body_preview"] = resp_preview
                if span:
                    span.set_attribute("http_response_body_preview", resp_preview)
            if resp_truncated:
                fields["http_response_body_preview_truncated"] = True
                if span:
                    span.set_attribute("http_response_body_preview_truncated", True)

        log_json(
            self._logger,
            "http.request",
            "incoming request handled",
            fields=fields,
        )


# ---------------------------------------------------------------------------
# Environment parsing helpers
# ---------------------------------------------------------------------------

def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw == "":
        return default
    return raw in ("1", "true", "yes", "on")


def _parse_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _parse_csv_env(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.getenv(name, "")
    if not raw and default is not None:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def add_fastapi_observability(app, logger, **overrides):
    trace_header_name = overrides.get(
        "trace_header_name",
        os.getenv("OBS_TRACE_HEADER_NAME", "X-Trace-Id"),
    )
    enable_response_body_preview = overrides.get(
        "enable_response_body_preview",
        _parse_bool_env("OBS_HTTP_BODY_PREVIEW_ENABLED", True),
    )
    response_body_preview_max_bytes = overrides.get(
        "response_body_preview_max_bytes",
        _parse_int_env("OBS_HTTP_BODY_PREVIEW_MAX_BYTES", 2048),
    )
    response_body_preview_paths = overrides.get(
        "response_body_preview_paths",
        _parse_csv_env("OBS_HTTP_BODY_PREVIEW_PATHS"),
    )
    response_body_preview_redact_keys = overrides.get(
        "response_body_preview_redact_keys",
        _parse_csv_env("OBS_HTTP_BODY_PREVIEW_REDACT_KEYS", sorted(DEFAULT_REDACT_KEYS)),
    )
    app.add_middleware(
        TraceAccessLogMiddleware,
        logger=logger,
        trace_header_name=trace_header_name,
        enable_response_body_preview=enable_response_body_preview,
        response_body_preview_max_bytes=response_body_preview_max_bytes,
        response_body_preview_paths=response_body_preview_paths,
        response_body_preview_redact_keys=response_body_preview_redact_keys,
    )
    return app
