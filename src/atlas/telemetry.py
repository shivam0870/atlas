import json
import logging
from datetime import UTC, datetime

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from atlas.config import settings

tracer = trace.get_tracer("atlas")
logger = logging.getLogger("atlas")
meter = metrics.get_meter("atlas")
requests = meter.create_counter("atlas.requests", unit="1")
latency = meter.create_histogram("atlas.query.duration", unit="s")
cache_hits = meter.create_counter("atlas.cache.hits", unit="1")
tokens = meter.create_counter("atlas.tokens", unit="1")
indexed = meter.create_counter("atlas.documents.indexed", unit="1")
queue_depth = meter.create_gauge("atlas.queue.depth", unit="1")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "time": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "event": record.getMessage(),
                **getattr(record, "fields", {}),
            }
        )


def setup():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    if settings.otel_enabled:
        provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint))
        )
        trace.set_tracer_provider(provider)
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=settings.otel_endpoint.replace("/v1/traces", "/v1/metrics")
            ),
            export_interval_millis=5000,
        )
        metrics.set_meter_provider(
            MeterProvider(
                resource=Resource.create({"service.name": settings.service_name}),
                metric_readers=[reader],
            )
        )
