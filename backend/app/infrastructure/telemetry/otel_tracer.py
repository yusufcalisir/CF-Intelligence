"""OpenTelemetry Distributed Tracing & W3C Trace Context Propagation Engine.

Instruments end-to-end request flows across central coordinator services
and remote bank daemons with W3C trace context headers (traceparent, tracestate).
"""

from __future__ import annotations

import contextlib
import logging
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)


@dataclass
class OTelSpanContext:
    """Encapsulates OpenTelemetry span context metadata."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    start_time: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    status: str = "OK"


class OpenTelemetryTracer:
    """Manages W3C Trace Context propagation, span lifecycle, and metric exporters."""

    def __init__(self, service_name: str = "cfi-platform") -> None:
        self.service_name = service_name

    def generate_trace_id(self) -> str:
        """Generates a 32-character hex trace ID."""
        return f"{random.getrandbits(128):032x}"

    def generate_span_id(self) -> str:
        """Generates a 16-character hex span ID."""
        return f"{random.getrandbits(64):016x}"

    def inject_w3c_trace_context(
        self, headers: dict[str, str] | None = None, trace_id: str | None = None
    ) -> dict[str, str]:
        """Injects W3C Trace Context headers (traceparent, tracestate) into a header dict."""
        out_headers = dict(headers) if headers else {}
        t_id = trace_id or self.generate_trace_id()
        s_id = self.generate_span_id()

        # W3C traceparent format: 00-{trace_id}-{span_id}-01
        traceparent = f"00-{t_id}-{s_id}-01"
        out_headers["traceparent"] = traceparent
        out_headers["tracestate"] = f"cfi={self.service_name}"
        return out_headers

    def extract_w3c_trace_context(self, headers: dict[str, str]) -> tuple[str, str]:
        """Extracts traceparent and tracestate from W3C headers."""
        traceparent = headers.get("traceparent", "")
        _tracestate = headers.get("tracestate", "")

        if traceparent and traceparent.startswith("00-") and len(traceparent.split("-")) == 4:
            parts = traceparent.split("-")
            if len(parts[1]) == 32 and len(parts[2]) == 16:
                trace_id = parts[1]
                span_id = parts[2]
                return trace_id, span_id

        # Fallback if header missing or malformed
        return self.generate_trace_id(), self.generate_span_id()

    @contextlib.contextmanager
    def trace_span(
        self, span_name: str, attributes: dict[str, Any] | None = None
    ) -> Generator[OTelSpanContext, None, None]:
        """Context manager creating an OpenTelemetry trace span."""
        t_id = self.generate_trace_id()
        s_id = self.generate_span_id()
        span_ctx = OTelSpanContext(
            name=span_name,
            trace_id=t_id,
            span_id=s_id,
            attributes=attributes or {},
            start_time=time.time(),
        )

        logger.debug("OTel Span STARTED: %s (trace_id=%s, span_id=%s)", span_name, t_id, s_id)
        try:
            yield span_ctx
            span_ctx.status = "OK"
        except Exception as err:
            span_ctx.status = "ERROR"
            span_ctx.attributes["error.type"] = err.__class__.__name__
            span_ctx.attributes["error.message"] = str(err)
            logger.error("OTel Span FAILED: %s (%s)", span_name, err)
            raise
        finally:
            span_ctx.duration_ms = (time.time() - span_ctx.start_time) * 1000.0
            logger.debug(
                "OTel Span COMPLETED: %s in %.2fms (status=%s)",
                span_name,
                span_ctx.duration_ms,
                span_ctx.status,
            )

    # ── Stage-Specific Span Helpers ──────────────────────────────

    def ingest_transaction_span(self, bank_id: str, count: int) -> OTelSpanContext:
        """Stage 1: Bank Ingestion Connector trace span."""
        with self.trace_span(
            "ingest_transaction", {"bank_id": bank_id, "transaction_count": count}
        ) as span:
            return span

    def feature_store_span(self, bank_id: str, feature_count: int) -> OTelSpanContext:
        """Stage 2: Streaming Feature Store rolling aggregations trace span."""
        with self.trace_span(
            "feature_store_aggregation", {"bank_id": bank_id, "feature_count": feature_count}
        ) as span:
            return span

    def local_trainer_span(self, bank_id: str, round_id: int, epochs: int) -> OTelSpanContext:
        """Stage 3: Local PyTorch Trainer & Opacus DP-SGD trace span."""
        with self.trace_span(
            "local_pytorch_training",
            {"bank_id": bank_id, "fl_round_id": round_id, "epochs": epochs},
        ) as span:
            return span

    def grpc_transmit_span(self, bank_id: str, payload_size_bytes: int) -> OTelSpanContext:
        """Stage 4: Outbound gRPC mTLS payload transmission trace span."""
        with self.trace_span(
            "grpc_mtls_transmit",
            {"bank_id": bank_id, "payload_bytes": payload_size_bytes},
        ) as span:
            return span

    def central_aggregation_span(self, round_id: int, client_count: int) -> OTelSpanContext:
        """Stage 5: Central Coordinator Krum/Median parameter aggregation trace span."""
        with self.trace_span(
            "central_parameter_aggregation",
            {"fl_round_id": round_id, "participating_clients": client_count},
        ) as span:
            return span

    def model_registry_span(self, version_tag: str) -> OTelSpanContext:
        """Stage 6: Model Registry version manifest save trace span."""
        with self.trace_span("model_registry_save", {"model_version": version_tag}) as span:
            return span

    # ── Prometheus Telemetry Recorders ───────────────────────────

    def record_hardware_telemetry(
        self, cpu_percent: float, ram_mb: float, gpu_memory_mb: float | None = None
    ) -> dict[str, Any]:
        """Records hardware utilization telemetry for Prometheus scrapes."""
        data = {
            "cpu_percent": cpu_percent,
            "ram_mb": ram_mb,
            "gpu_memory_mb": gpu_memory_mb or 0.0,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        }
        logger.debug("Recorded hardware telemetry: CPU=%.1f%%, RAM=%.1fMB", cpu_percent, ram_mb)
        return data

    def record_training_telemetry(
        self, round_id: int, loss: float, comm_latency_ms: float, dp_epsilon: float
    ) -> dict[str, Any]:
        """Records FL training metrics for Prometheus scrapes."""
        data = {
            "round_id": round_id,
            "loss": loss,
            "comm_latency_ms": comm_latency_ms,
            "dp_epsilon": dp_epsilon,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        }
        logger.debug(
            "Recorded training telemetry: Round %d, Loss=%.4f, CommLatency=%.1fms, DP Epsilon=%.2f",
            round_id,
            loss,
            comm_latency_ms,
            dp_epsilon,
        )
        return data
