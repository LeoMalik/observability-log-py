from __future__ import annotations

import pytest

from obslogpy.langfuse.fastapi import (
    extract_trace_attrs_from_body,
    resolve_langfuse_session_id,
    resolve_langfuse_trace_id,
)


@pytest.mark.parametrize(
    "x_trace_id,otel_trace_id,expected_trace_id,expected_upstream_raw",
    [
        (
            "0123456789abcdef0123456789abcdef",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "0123456789abcdef0123456789abcdef",
            None,
        ),
        (
            "0123456789ABCDEF0123456789ABCDEF",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "0123456789abcdef0123456789abcdef",
            None,
        ),
        (
            None,
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            None,
        ),
        (
            "not-a-valid-trace-id",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "not-a-valid-trace-id",
        ),
        (
            "not-a-valid-trace-id",
            None,
            None,
            "not-a-valid-trace-id",
        ),
    ],
)
def test_resolve_langfuse_trace_id(
    x_trace_id: str | None,
    otel_trace_id: str | None,
    expected_trace_id: str | None,
    expected_upstream_raw: str | None,
) -> None:
    trace_id, upstream_raw = resolve_langfuse_trace_id(
        x_trace_id=x_trace_id,
        otel_trace_id=otel_trace_id,
    )

    assert trace_id == expected_trace_id
    assert upstream_raw == expected_upstream_raw


@pytest.mark.parametrize(
    "raw,expected,expected_raw",
    [
        ("session_001", "session_001", None),
        ("  abc  ", "abc", None),
        (None, None, None),
        ("x" * 201, None, "x" * 201),
        ("中文", None, "中文"),
    ],
)
def test_resolve_langfuse_session_id(raw: str | None, expected: str | None, expected_raw: str | None) -> None:
    session_id, upstream_raw = resolve_langfuse_session_id(raw)
    assert session_id == expected
    assert upstream_raw == expected_raw


def test_extract_trace_attrs_from_body_json() -> None:
    body = b'{"user_id": 123, "sessionId": "abc-123"}'
    assert extract_trace_attrs_from_body(body, "application/json") == ("123", "abc-123")


def test_extract_trace_attrs_from_body_non_json() -> None:
    body = b"user_id=123"
    assert extract_trace_attrs_from_body(body, "application/x-www-form-urlencoded") == (None, None)
