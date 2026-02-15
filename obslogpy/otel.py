from __future__ import annotations

import logging
import os
from logging import Logger

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .logging import log_json

_OTEL_INITIALIZED = False


def _parse_otlp_endpoint(raw: str) -> tuple[str, bool]:
    endpoint = (raw or "").strip()
    if not endpoint:
        return "signoz-otel-collector:4317", True
    if endpoint.startswith("http://"):
        return endpoint[len("http://") :], True
    if endpoint.startswith("https://"):
        return endpoint[len("https://") :], False
    return endpoint, True


def configure_logging(
    logger_name: str = "app",
    *,
    level: int = logging.INFO,
    include_correlation: bool | None = None,
) -> logging.Logger:
    base_log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_format = base_log_format
    enable_log_correlation = (
        include_correlation
        if include_correlation is not None
        else os.getenv("OTEL_PYTHON_LOG_CORRELATION", "false").lower() == "true"
    )
    if enable_log_correlation:
        try:
            from opentelemetry.instrumentation.logging import LoggingInstrumentor

            instrumentor = LoggingInstrumentor()
            if not instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.instrument(set_logging_format=False)
            log_format = f"{base_log_format} trace_id=%(otelTraceID)s span_id=%(otelSpanID)s"
        except Exception:
            log_format = base_log_format

    logging.basicConfig(level=level, format=log_format)
    return logging.getLogger(logger_name)


def init_otel(service_name: str, logger: Logger | None = None) -> None:
    global _OTEL_INITIALIZED
    if _OTEL_INITIALIZED:
        return

    current_provider = trace.get_tracer_provider()
    if isinstance(current_provider, TracerProvider):
        _OTEL_INITIALIZED = True
        if logger:
            log_json(
                logger,
                "observability.init_otel",
                "otel tracer provider already initialized, skipping setup",
            )
        return

    endpoint, insecure = _parse_otlp_endpoint(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""))
    resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", service_name)})

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _OTEL_INITIALIZED = True

    if logger:
        log_json(
            logger,
            "observability.init_otel",
            "initialized otel tracer provider",
            fields={"otlp_endpoint": endpoint, "otlp_insecure": insecure},
        )
