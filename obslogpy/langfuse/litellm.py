from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace

from .client import get_langfuse, open_observation
from .config import LangfuseSettings
from .context import preserve_otel_parent_span

logger = logging.getLogger(__name__)

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
