from __future__ import annotations

from collections.abc import Iterator
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from obslogpy.fastapi import TraceAccessLogMiddleware


class _SpanContext:
    def __init__(self, *, is_valid: bool, trace_id: int = 0) -> None:
        self.is_valid = is_valid
        self.trace_id = trace_id


class _Span:
    def __init__(self, *, valid: bool = True) -> None:
        self._ctx = _SpanContext(
            is_valid=valid,
            trace_id=int("0123456789abcdef0123456789abcdef", 16),
        )
        self.attrs: dict[str, object] = {}
        self.status: tuple[object, str | None] | None = None
        self.exceptions: list[Exception] = []

    def get_span_context(self) -> _SpanContext:
        return self._ctx

    def set_attribute(self, key: str, value: object) -> None:
        self.attrs[key] = value

    def set_status(self, code: object, description: str | None = None) -> None:
        self.status = (code, description)

    def record_exception(self, err: Exception) -> None:
        self.exceptions.append(err)


class _SpanCtx:
    def __init__(self, span: _Span) -> None:
        self._span = span

    def __enter__(self) -> _Span:
        return self._span

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _Tracer:
    def __init__(self) -> None:
        self.last_span: _Span | None = None

    def start_as_current_span(self, _name: str, **_kwargs) -> _SpanCtx:
        self.last_span = _Span(valid=True)
        return _SpanCtx(self.last_span)


def test_preview_attrs_written_to_span_even_without_active_current_span(monkeypatch) -> None:
    # Simulate "no active span" during access-log emission; middleware should still
    # write preview attrs to the request span it just created.
    from obslogpy import fastapi as middleware_mod

    tracer = _Tracer()

    def fake_current_span() -> _Span:
        return _Span(valid=False)

    monkeypatch.setattr(middleware_mod.trace, "get_tracer", lambda _name: tracer)
    monkeypatch.setattr(middleware_mod.trace, "get_current_span", fake_current_span)

    app = FastAPI()
    app.add_middleware(
        TraceAccessLogMiddleware,
        logger=logging.getLogger("test-obs-preview"),
        enable_response_body_preview=True,
        response_body_preview_max_bytes=256,
    )

    @app.post("/echo")
    async def echo(payload: dict):
        return {"ok": payload.get("x")}

    client = TestClient(app)
    resp = client.post("/echo", json={"x": 123})

    assert resp.status_code == 200
    assert tracer.last_span is not None
    assert "http_request_body_preview" in tracer.last_span.attrs
    assert "http_response_body_preview" in tracer.last_span.attrs
    assert "http.server_duration_ms" in tracer.last_span.attrs


def test_streaming_response_capture_is_capped(monkeypatch) -> None:
    from obslogpy import fastapi as middleware_mod

    seen_lengths: list[int] = []

    def fake_preview(body, **_kwargs):
        seen_lengths.append(len(body or b""))
        return "", False, len(body or b"")

    monkeypatch.setattr(middleware_mod, "build_body_preview", fake_preview)

    app = FastAPI()
    app.add_middleware(
        TraceAccessLogMiddleware,
        logger=logging.getLogger("test-obs-stream"),
        enable_response_body_preview=True,
        response_body_preview_max_bytes=32,
    )

    @app.post("/events")
    async def events(_payload: dict):
        async def generate() -> Iterator[bytes]:
            for _ in range(100):
                yield b"x" * 1024

        return StreamingResponse(generate(), media_type="text/event-stream")

    client = TestClient(app)
    resp = client.post("/events", json={"x": 1})

    assert resp.status_code == 200
    assert len(seen_lengths) >= 2
    response_body_len = seen_lengths[1]
    assert response_body_len <= 33
