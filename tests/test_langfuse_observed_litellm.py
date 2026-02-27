from __future__ import annotations

import asyncio

from opentelemetry.trace import StatusCode

from obslogpy.langfuse.litellm import (
    DEFAULT_TRACE_UUID,
    build_trace_headers,
    observed_instrumented_acompletion,
)


class _FakeSpan:
    def __init__(self) -> None:
        self.attrs: dict[str, object] = {}
        self.exceptions: list[Exception] = []
        self.status: tuple[object, str | None] | None = None

    def set_attribute(self, key: str, value: object) -> None:
        self.attrs[key] = value

    def record_exception(self, err: Exception) -> None:
        self.exceptions.append(err)

    def set_status(self, code: object, description: str | None = None) -> None:
        self.status = (code, description)


class _FakeCtx:
    def __init__(self, span: _FakeSpan) -> None:
        self._span = span

    def __enter__(self) -> _FakeSpan:
        return self._span

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeTracer:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.last_span = _FakeSpan()

    def start_as_current_span(self, name: str):
        self.started.append(name)
        self.last_span = _FakeSpan()
        return _FakeCtx(self.last_span)


def test_build_trace_headers() -> None:
    headers = build_trace_headers(user_id="u-1", session_id="s-1")
    assert headers == {
        "X-User-ID": "u-1",
        "X-UUID": DEFAULT_TRACE_UUID,
        "X-Session-ID": "s-1",
    }


def test_observed_instrumented_acompletion_success(monkeypatch) -> None:
    from obslogpy.langfuse import litellm as litellm_mod

    fake_tracer = _FakeTracer()
    monkeypatch.setattr(litellm_mod.trace, "get_tracer", lambda _name: fake_tracer)

    async def fake_instrumented(**_kwargs):
        return {"choices": [{"message": {"content": "hello"}}]}

    monkeypatch.setattr(litellm_mod, "instrumented_acompletion", fake_instrumented)

    resp = asyncio.run(
        observed_instrumented_acompletion(
            tracer_name="mail-mvp/llm/email-write",
            span_name="EmailWriteClient.custom_email_acompletion",
            generation_name="EmailWriteClient.generate_body_custom",
            model="test-model",
            base_url="http://example.test",
            api_key="secret",
            messages=[{"role": "user", "content": "hi"}],
            user_id="u-1",
            session_id="s-1",
            request_payload={
                "model": "test-model",
                "messages": [{"role": "user", "content": "hi"}],
            },
            timeout=3,
        )
    )

    assert resp["choices"][0]["message"]["content"] == "hello"
    assert fake_tracer.started == ["EmailWriteClient.custom_email_acompletion"]
    assert fake_tracer.last_span.attrs["llm.model"] == "test-model"
    assert fake_tracer.last_span.attrs["app.user_id"] == "u-1"
    assert fake_tracer.last_span.attrs["app.session_id"] == "s-1"
    assert "http_request_body_preview" in fake_tracer.last_span.attrs
    assert "http_response_body_preview" in fake_tracer.last_span.attrs
    assert fake_tracer.last_span.attrs["llm.output_length"] == 5


def test_observed_instrumented_acompletion_error(monkeypatch) -> None:
    from obslogpy.langfuse import litellm as litellm_mod

    fake_tracer = _FakeTracer()
    monkeypatch.setattr(litellm_mod.trace, "get_tracer", lambda _name: fake_tracer)

    async def fake_instrumented(**_kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(litellm_mod, "instrumented_acompletion", fake_instrumented)

    try:
        asyncio.run(
            observed_instrumented_acompletion(
                tracer_name="mail-mvp/llm/raw-search",
                span_name="RawSearchClient.fetch",
                generation_name="RawSearchClient.fetch",
                model="test-model",
                messages=[{"role": "user", "content": "hi"}],
            )
        )
        raise AssertionError("should have raised ValueError")
    except ValueError:
        pass

    assert len(fake_tracer.last_span.exceptions) == 1
    assert fake_tracer.last_span.status is not None
    assert fake_tracer.last_span.status[0] == StatusCode.ERROR
