from __future__ import annotations

import asyncio

import pytest


class _DummyObservation:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class _DummyCtx:
    def __init__(self, observation: _DummyObservation) -> None:
        self._observation = observation

    def __enter__(self) -> _DummyObservation:
        return self._observation

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _DummyLangfuse:
    def __init__(self) -> None:
        self.observation_kwargs: dict | None = None
        self.observation = _DummyObservation()

    def start_as_current_observation(self, **kwargs):
        self.observation_kwargs = kwargs
        return _DummyCtx(self.observation)


def test_instrumented_acompletion_success(monkeypatch):
    from obslogpy.langfuse import litellm as lf_litellm

    dummy = _DummyLangfuse()
    monkeypatch.setattr(lf_litellm, "get_langfuse", lambda *_args, **_kwargs: dummy)

    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return {
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }

    monkeypatch.setattr(lf_litellm, "acompletion", fake_acompletion)

    resp = asyncio.run(
        lf_litellm.instrumented_acompletion(
            name="unit.test",
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            base_url="http://example.test",
            api_key="should-not-leak",
            max_completion_tokens=12,
            timeout=3,
        )
    )

    assert resp["choices"][0]["message"]["content"] == "hello"
    assert dummy.observation_kwargs is not None
    assert dummy.observation_kwargs["as_type"] == "generation"
    assert dummy.observation_kwargs["name"] == "unit.test"
    assert dummy.observation_kwargs["model"] == "test-model"
    assert dummy.observation_kwargs["input"]["messages"] == [{"role": "user", "content": "hi"}]
    assert "should-not-leak" not in str(dummy.observation_kwargs)

    assert captured["api_key"] == "should-not-leak"
    assert captured["model"] == "test-model"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]

    assert dummy.observation.updates, "should update observation with output/usage"
    merged_updates = {k: v for d in dummy.observation.updates for k, v in d.items()}
    assert "output" in merged_updates
    assert "usage_details" in merged_updates


def test_instrumented_acompletion_error(monkeypatch):
    from obslogpy.langfuse import litellm as lf_litellm

    dummy = _DummyLangfuse()
    monkeypatch.setattr(lf_litellm, "get_langfuse", lambda *_args, **_kwargs: dummy)

    async def fake_acompletion(**kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(lf_litellm, "acompletion", fake_acompletion)

    with pytest.raises(ValueError):
        asyncio.run(
            lf_litellm.instrumented_acompletion(
                name="unit.test.error",
                model="test-model",
                messages=[{"role": "user", "content": "hi"}],
                base_url="http://example.test",
                api_key="should-not-leak",
                max_completion_tokens=12,
                timeout=3,
            )
        )

    assert dummy.observation.updates, "should update observation with error info"
    merged_updates = {k: v for d in dummy.observation.updates for k, v in d.items()}
    assert merged_updates.get("level") == "ERROR"
    assert "boom" in (merged_updates.get("status_message") or "")
    assert "should-not-leak" not in str(dummy.observation.updates)
