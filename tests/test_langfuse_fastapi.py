from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from obslogpy.langfuse.config import LangfuseSettings
from obslogpy.langfuse.fastapi import LangfuseTraceMiddleware, add_langfuse_tracing


class _SpanContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeLangfuse:
    def __init__(self):
        self.started_spans: list[dict] = []

    def start_as_current_span(self, **kwargs):
        self.started_spans.append(kwargs)
        return _SpanContext()

    def update_current_span(self, **kwargs):
        return None

    def flush(self):
        return None


def test_add_langfuse_tracing_enabled_adds_middleware() -> None:
    app = FastAPI()
    add_langfuse_tracing(
        app,
        settings=LangfuseSettings(
            host="http://localhost:3000",
            public_key="pk",
            secret_key="sk",
            tracing_enabled=True,
            flush_at_request_end=True,
        ),
    )

    assert any(m.cls is LangfuseTraceMiddleware for m in app.user_middleware)


def test_add_langfuse_tracing_disabled_skips_middleware() -> None:
    app = FastAPI()
    add_langfuse_tracing(
        app,
        settings=LangfuseSettings(
            host="http://localhost:3000",
            public_key="pk",
            secret_key="sk",
            tracing_enabled=False,
            flush_at_request_end=True,
        ),
    )

    assert not any(m.cls is LangfuseTraceMiddleware for m in app.user_middleware)


def test_middleware_auto_sets_trace_attributes_and_keeps_body_readable(monkeypatch) -> None:
    from obslogpy.langfuse import fastapi as middleware_mod

    fake_langfuse = _FakeLangfuse()
    captured: list[tuple[str | None, str | None]] = []

    monkeypatch.setattr(middleware_mod, "get_langfuse", lambda *_args, **_kwargs: fake_langfuse)
    monkeypatch.setattr(middleware_mod, "_current_otel_trace_id", lambda: "a" * 32)
    monkeypatch.setattr(middleware_mod, "langfuse_flush_at_request_end", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        middleware_mod,
        "set_langfuse_trace_attributes",
        lambda *, user_id=None, session_id=None, **_kwargs: captured.append((user_id, session_id)),
    )

    app = FastAPI()
    app.add_middleware(LangfuseTraceMiddleware)

    @app.post("/echo")
    async def echo(req: dict):
        return {"user_id": req.get("user_id")}

    client = TestClient(app)
    response = client.post(
        "/echo",
        headers={"X-Session-Id": "campaign_42"},
        json={"user_id": 7, "message": "hello"},
    )

    assert response.status_code == 200
    assert response.json() == {"user_id": 7}
    assert captured == [("7", "campaign_42")]
    assert len(fake_langfuse.started_spans) == 1
    assert fake_langfuse.started_spans[0]["trace_context"]["trace_id"] == "a" * 32
