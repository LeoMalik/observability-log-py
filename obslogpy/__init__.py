from .fastapi import TraceAccessLogMiddleware, add_fastapi_observability
from .logging import DEFAULT_REDACT_KEYS, build_body_preview, build_payload, log_json
from .otel import configure_logging, init_otel
from .span import SpanAttrKeys, SpanOps, set_dependency_http_attrs, set_span_attrs

__all__ = [
    "DEFAULT_REDACT_KEYS",
    "SpanAttrKeys",
    "SpanOps",
    "TraceAccessLogMiddleware",
    "add_fastapi_observability",
    "build_body_preview",
    "build_payload",
    "configure_logging",
    "init_otel",
    "langfuse",
    "log_json",
    "set_dependency_http_attrs",
    "set_span_attrs",
]


def __getattr__(name: str):
    if name == "langfuse":
        from importlib import import_module

        module = import_module(".langfuse", __name__)

        return module
    raise AttributeError(name)
