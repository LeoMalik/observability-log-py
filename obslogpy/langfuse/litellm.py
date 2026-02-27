from __future__ import annotations

import json
import logging
import time
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from .client import get_langfuse, open_observation
from .config import LangfuseSettings
from .context import preserve_otel_parent_span

logger = logging.getLogger(__name__)
DEFAULT_TRACE_UUID = "123e4567-e89b-12d3-a456-426614174000"

try:
    from litellm import acompletion
except Exception:  # pragma: no cover - optional dependency
    acompletion = None  # type: ignore[assignment]


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _extract_usage_details(resp: Any) -> dict[str, int] | None:
    if not isinstance(resp, dict):
        return None
    usage = resp.get("usage")
    if not isinstance(usage, dict):
        return None
    out: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        iv = _safe_int(usage.get(key))
        if iv is not None:
            out[key] = iv
    return out or None


def _extract_output(resp: Any) -> dict[str, Any]:
    if not isinstance(resp, dict):
        return {"raw": str(resp)}

    content = (
        resp.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    return {
        "content": content,
    }


def _extract_model_parameters(kwargs: dict[str, Any]) -> dict[str, Any]:
    allow = (
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "timeout",
        "presence_penalty",
        "frequency_penalty",
        "seed",
        "response_format",
        "extra_body",
    )
    out: dict[str, Any] = {}
    for key in allow:
        if key not in kwargs:
            continue
        value = kwargs.get(key)
        if value is None or isinstance(value, (bool, int, float, str, list, dict)):
            out[key] = value
        else:
            out[key] = str(value)
    return out


def _preview_json(value: object, max_bytes: int = 4096) -> tuple[str, bool, int]:
    encoded = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    if len(encoded) <= max_bytes:
        return encoded.decode("utf-8", errors="replace"), False, len(encoded)
    return encoded[:max_bytes].decode("utf-8", errors="replace"), True, len(encoded)


def _build_default_request_payload(
    *,
    model: str,
    messages: list[dict[str, Any]],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    for key, value in kwargs.items():
        if key == "api_key":
            continue
        if value is None:
            continue
        if isinstance(value, (bool, int, float, str, list, dict)):
            payload[key] = value
        else:
            payload[key] = str(value)
    return payload


def build_trace_headers(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    include_uuid: bool = True,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if user_id:
        headers["X-User-ID"] = user_id
        if include_uuid:
            headers["X-UUID"] = DEFAULT_TRACE_UUID
    if session_id:
        headers["X-Session-ID"] = session_id
    return headers


async def instrumented_acompletion(
    *,
    name: str,
    model: str,
    messages: list[dict[str, Any]],
    settings: LangfuseSettings | None = None,
    **kwargs: Any,
) -> Any:
    if acompletion is None:
        raise RuntimeError(
            "litellm is not installed. Install with: pip install 'observability-log-py[langfuse]'"
        )

    langfuse = get_langfuse(settings)
    if langfuse is None:
        return await acompletion(model=model, messages=messages, **kwargs)

    safe_kwargs = dict(kwargs)
    safe_kwargs.pop("api_key", None)

    model_parameters = _extract_model_parameters(safe_kwargs)
    metadata = {}
    if "base_url" in safe_kwargs:
        metadata["litellm.base_url"] = safe_kwargs.get("base_url")

    current_otel_span = trace.get_current_span()

    with open_observation(
        as_type="generation",
        name=name,
        model=model,
        input={"messages": messages},
        model_parameters=model_parameters or None,
        metadata=metadata or None,
        langfuse=langfuse,
        settings=settings,
    ) as generation:
        try:
            with preserve_otel_parent_span(current_otel_span):
                resp = await acompletion(model=model, messages=messages, **kwargs)
        except Exception as err:
            if generation is not None:
                try:
                    generation.update(level="ERROR", status_message=str(err))
                except Exception:
                    logger.warning("Langfuse generation.update failed", exc_info=True)
            raise

        usage_details = _extract_usage_details(resp)
        output = _extract_output(resp)
        if generation is not None:
            try:
                generation.update(
                    output=output,
                    usage_details=usage_details,
                )
            except Exception:
                logger.warning("Langfuse generation.update failed", exc_info=True)
        return resp


async def observed_instrumented_acompletion(
    *,
    tracer_name: str,
    span_name: str,
    generation_name: str,
    model: str,
    messages: list[dict[str, Any]],
    base_url: str | None = None,
    api_key: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    request_payload: dict[str, Any] | None = None,
    extra_span_attrs: dict[str, Any] | None = None,
    preview_max_bytes: int = 4096,
    settings: LangfuseSettings | None = None,
    **kwargs: Any,
) -> Any:
    """High-level wrapper that keeps business code free from tracing details."""
    tracer = trace.get_tracer(tracer_name)
    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute("llm.model", model)
        if user_id:
            span.set_attribute("app.user_id", user_id)
        if session_id:
            span.set_attribute("app.session_id", session_id)
        if extra_span_attrs:
            for key, value in extra_span_attrs.items():
                if value is not None:
                    span.set_attribute(key, value)

        effective_request_payload = request_payload or _build_default_request_payload(
            model=model,
            messages=messages,
            kwargs=kwargs,
        )
        if effective_request_payload is not None:
            req_preview, req_truncated, req_size = _preview_json(
                effective_request_payload,
                max_bytes=preview_max_bytes,
            )
            span.set_attribute("http_request_body_preview", req_preview)
            span.set_attribute("http_request_body_preview_truncated", req_truncated)
            span.set_attribute("http_request_body_size", req_size)

        call_kwargs = dict(kwargs)
        if base_url is not None:
            call_kwargs["base_url"] = base_url
        if api_key is not None:
            call_kwargs["api_key"] = api_key

        start = time.perf_counter()
        try:
            resp = await instrumented_acompletion(
                name=generation_name,
                model=model,
                messages=messages,
                settings=settings,
                **call_kwargs,
            )
        except Exception as err:
            span.record_exception(err)
            span.set_status(StatusCode.ERROR, str(err))
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        span.set_attribute("llm.duration_ms", round(duration_ms, 3))

        resp_preview, resp_truncated, resp_size = _preview_json(
            resp,
            max_bytes=preview_max_bytes,
        )
        span.set_attribute("http_response_body_preview", resp_preview)
        span.set_attribute("http_response_body_preview_truncated", resp_truncated)
        span.set_attribute("http_response_body_size", resp_size)

        content = (
            resp.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            if isinstance(resp, dict)
            else ""
        )
        if isinstance(content, str):
            span.set_attribute("llm.output_length", len(content))
        return resp
