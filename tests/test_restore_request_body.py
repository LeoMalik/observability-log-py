"""Tests for the Langfuse middleware body-reading fix.

The original ``LangfuseTraceMiddleware.dispatch`` called
``_restore_request_body`` after reading the request body.  This replaced
``request._receive`` with a closure that returns ``http.request`` on its
first call.  However, Starlette's ``_CachedRequest.wrapped_receive``
(used by ``BaseHTTPMiddleware``) calls ``self.receive()`` → ``self._receive()``
in the *consumed* state — expecting only ``http.disconnect``.  The patched
``_receive`` returned ``http.request`` instead, which crashed with::

    RuntimeError: Unexpected message received: http.request

The fix: remove the ``_restore_request_body`` call entirely.
``_CachedRequest`` already caches the body from ``request.body()`` and
replays it to downstream middleware via ``wrapped_receive`` automatically.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from obslogpy.langfuse.fastapi import (
    LangfuseTraceMiddleware,
    _restore_request_body,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SpanCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeLangfuse:
    """Minimal Langfuse stub that records calls but performs no I/O."""

    def __init__(self):
        self.started_spans: list[dict] = []

    def start_as_current_span(self, **kwargs):
        self.started_spans.append(kwargs)
        return _SpanCtx()

    def update_current_span(self, **kwargs):
        return None

    def flush(self):
        return None


class PassthroughMiddleware(BaseHTTPMiddleware):
    """A no-op ``BaseHTTPMiddleware`` used to stack an extra layer."""

    async def dispatch(self, request: Request, call_next):
        return await call_next(request)


@contextmanager
def _noop_preserve_span(parent_span=None) -> Iterator[None]:
    yield


def _patch_langfuse_middleware(monkeypatch):
    """Apply all necessary monkeypatches for LangfuseTraceMiddleware tests."""
    from obslogpy.langfuse import fastapi as mod

    fake = _FakeLangfuse()
    monkeypatch.setattr(mod, "get_langfuse", lambda *a, **kw: fake)
    monkeypatch.setattr(mod, "_current_otel_trace_id", lambda: "a" * 32)
    monkeypatch.setattr(mod, "langfuse_flush_at_request_end", lambda *a, **kw: False)
    monkeypatch.setattr(mod, "set_langfuse_trace_attributes", lambda **kw: None)
    monkeypatch.setattr(mod, "preserve_otel_parent_span", _noop_preserve_span)
    return fake


# ---------------------------------------------------------------------------
# Unit tests – _restore_request_body (standalone utility)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restore_body_first_call_returns_body():
    """First receive() should return the replayed body."""
    async def _original_receive():
        return {"type": "http.disconnect"}

    scope = {"type": "http", "method": "POST", "path": "/t"}
    request = Request(scope, receive=_original_receive)

    _restore_request_body(request, b'{"key": "value"}')

    msg = await request._receive()
    assert msg["type"] == "http.request"
    assert msg["body"] == b'{"key": "value"}'


@pytest.mark.asyncio
async def test_restore_body_second_call_delegates():
    """After body is consumed, receive() must delegate to the original."""
    async def _original_receive():
        return {"type": "http.disconnect"}

    scope = {"type": "http", "method": "POST", "path": "/t"}
    request = Request(scope, receive=_original_receive)

    _restore_request_body(request, b"body")

    await request._receive()  # consume body
    msg = await request._receive()  # delegate
    assert msg["type"] == "http.disconnect"


# ---------------------------------------------------------------------------
# Integration tests – POST body readable through stacked middleware
# ---------------------------------------------------------------------------

def test_body_readable_with_two_middleware_layers(monkeypatch):
    """Langfuse + passthrough – POST body is readable by the endpoint."""
    _patch_langfuse_middleware(monkeypatch)

    app = FastAPI()
    app.add_middleware(PassthroughMiddleware)
    app.add_middleware(LangfuseTraceMiddleware)

    @app.post("/echo")
    async def echo(request: Request):
        body = await request.body()
        return {"body": body.decode()}

    client = TestClient(app)
    resp = client.post(
        "/echo",
        json={"msg": "hello"},
        headers={"X-Session-Id": "s1"},
    )

    assert resp.status_code == 200
    assert "msg" in resp.json()["body"]


# ---------------------------------------------------------------------------
# Integration tests – StreamingResponse through stacked middleware
# This is the scenario that triggered the original RuntimeError.
# ---------------------------------------------------------------------------

def test_streaming_with_two_middleware_layers(monkeypatch):
    """Langfuse + passthrough + StreamingResponse must not crash."""
    _patch_langfuse_middleware(monkeypatch)

    app = FastAPI()
    app.add_middleware(PassthroughMiddleware)
    app.add_middleware(LangfuseTraceMiddleware)

    async def _sse():
        for i in range(3):
            yield f"data: event-{i}\n\n"

    @app.post("/stream")
    async def stream(request: Request):
        return StreamingResponse(_sse(), media_type="text/event-stream")

    client = TestClient(app)
    resp = client.post(
        "/stream",
        json={"user_id": "u1"},
        headers={"X-Session-Id": "s1"},
    )

    assert resp.status_code == 200
    assert "event-0" in resp.text
    assert "event-2" in resp.text


def test_streaming_with_three_middleware_layers(monkeypatch):
    """Three BaseHTTPMiddleware layers + StreamingResponse."""
    _patch_langfuse_middleware(monkeypatch)

    app = FastAPI()
    app.add_middleware(PassthroughMiddleware)
    app.add_middleware(PassthroughMiddleware)
    app.add_middleware(LangfuseTraceMiddleware)

    async def _sse():
        for i in range(5):
            yield f"data: chunk-{i}\n\n"

    @app.post("/deep")
    async def deep(request: Request):
        return StreamingResponse(_sse(), media_type="text/event-stream")

    client = TestClient(app)
    resp = client.post(
        "/deep",
        json={"user_id": "u2"},
        headers={"X-Session-Id": "s2"},
    )

    assert resp.status_code == 200
    assert "chunk-4" in resp.text


def test_langfuse_plus_trace_access_log_middleware(monkeypatch):
    """Real-world combo: LangfuseTraceMiddleware + TraceAccessLogMiddleware."""
    from obslogpy.fastapi import TraceAccessLogMiddleware

    _patch_langfuse_middleware(monkeypatch)

    app = FastAPI()
    logger = logging.getLogger("test-obs")
    app.add_middleware(
        TraceAccessLogMiddleware,
        logger=logger,
        enable_response_body_preview=False,
    )
    app.add_middleware(LangfuseTraceMiddleware)

    async def _sse():
        yield "data: hello\n\n"
        yield "data: world\n\n"

    @app.post("/sse")
    async def sse(request: Request):
        return StreamingResponse(_sse(), media_type="text/event-stream")

    client = TestClient(app)
    resp = client.post(
        "/sse",
        json={"user_id": "u3"},
        headers={"X-Session-Id": "s3"},
    )

    assert resp.status_code == 200
    assert "hello" in resp.text
    assert "world" in resp.text


# ---------------------------------------------------------------------------
# GET + StreamingResponse (no body reading path)
# ---------------------------------------------------------------------------

def test_get_streaming_with_stacked_middleware(monkeypatch):
    """GET + StreamingResponse with stacked middleware."""
    _patch_langfuse_middleware(monkeypatch)

    app = FastAPI()
    app.add_middleware(PassthroughMiddleware)
    app.add_middleware(LangfuseTraceMiddleware)

    async def _gen():
        yield "data: ok\n\n"

    @app.get("/events")
    async def events():
        return StreamingResponse(_gen(), media_type="text/event-stream")

    client = TestClient(app)
    resp = client.get("/events")
    assert resp.status_code == 200
    assert "ok" in resp.text


# ---------------------------------------------------------------------------
# Langfuse disabled (no-op path)
# ---------------------------------------------------------------------------

def test_langfuse_disabled_noop(monkeypatch):
    """When Langfuse client is None, middleware is a pass-through."""
    from obslogpy.langfuse import fastapi as mod

    monkeypatch.setattr(mod, "get_langfuse", lambda *a, **kw: None)

    app = FastAPI()
    app.add_middleware(PassthroughMiddleware)
    app.add_middleware(LangfuseTraceMiddleware)

    async def _gen():
        yield "data: noop\n\n"

    @app.post("/noop")
    async def noop(request: Request):
        return StreamingResponse(_gen(), media_type="text/event-stream")

    client = TestClient(app)
    resp = client.post("/noop", json={"x": 1})
    assert resp.status_code == 200
    assert "noop" in resp.text
