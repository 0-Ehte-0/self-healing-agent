import os

from demo_api.config import Settings
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_telemetry(app: FastAPI, settings: Settings) -> None:
    # Do not start background exporters during test execution or when disabled
    if (
        os.getenv("PYTEST_CURRENT_TEST") is not None
        or os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true"
        or settings.environment == "test"
    ):
        return

    resource = Resource.create(
        attributes={
            "service.name": settings.app_name,
            "environment": settings.environment,
        }
    )

    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        insecure=True,
    )
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
