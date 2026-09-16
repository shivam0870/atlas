"""Verify recording/export directly, then extract an actual request trace separately."""

import json
from pathlib import Path

from opentelemetry import trace

from atlas.config import settings
from atlas.telemetry import setup, tracer

settings.otel_enabled = True
setup()
with tracer.start_as_current_span("verification.export") as span:
    span.set_attribute("verification.kind", "local smoke")
    span.set_attribute("usage.api_cost_usd", 0.0)
    result = {
        "recording": span.is_recording(),
        "trace_id": format(span.get_span_context().trace_id, "032x"),
    }
result["flushed"] = trace.get_tracer_provider().force_flush(10000)
Path("artifacts/telemetry-export.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
trace.get_tracer_provider().shutdown()
