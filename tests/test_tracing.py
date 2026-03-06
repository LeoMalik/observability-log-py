from __future__ import annotations

import asyncio

import pytest
from opentelemetry.trace import StatusCode

from obslogpy import traced_step


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


def test_traced_step_sync_success(monkeypatch) -> None:
    from obslogpy import tracing as tracing_mod

    fake_tracer = _FakeTracer()
    monkeypatch.setattr(tracing_mod.trace, "get_tracer", lambda _name: fake_tracer)

    @traced_step("step.sync", attrs={"biz.phase": "parse"})
    def _work(value: int) -> int:
        return value + 1

    assert _work(1) == 2
    assert fake_tracer.started == ["step.sync"]
    assert fake_tracer.last_span.attrs["biz.phase"] == "parse"
    assert "duration_ms" in fake_tracer.last_span.attrs
    assert fake_tracer.last_span.status is not None
    assert fake_tracer.last_span.status[0] == StatusCode.OK


def test_traced_step_sync_error(monkeypatch) -> None:
    from obslogpy import tracing as tracing_mod

    fake_tracer = _FakeTracer()
    monkeypatch.setattr(tracing_mod.trace, "get_tracer", lambda _name: fake_tracer)

    @traced_step("step.sync.error")
    def _work() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        _work()

    assert fake_tracer.started == ["step.sync.error"]
    assert "duration_ms" in fake_tracer.last_span.attrs
    assert len(fake_tracer.last_span.exceptions) == 1
    assert fake_tracer.last_span.status is not None
    assert fake_tracer.last_span.status[0] == StatusCode.ERROR


def test_traced_step_async_success(monkeypatch) -> None:
    from obslogpy import tracing as tracing_mod

    fake_tracer = _FakeTracer()
    monkeypatch.setattr(tracing_mod.trace, "get_tracer", lambda _name: fake_tracer)

    @traced_step("step.async")
    async def _work(value: int) -> int:
        return value * 2

    result = asyncio.run(_work(5))

    assert result == 10
    assert fake_tracer.started == ["step.async"]
    assert "duration_ms" in fake_tracer.last_span.attrs
    assert fake_tracer.last_span.status is not None
    assert fake_tracer.last_span.status[0] == StatusCode.OK
