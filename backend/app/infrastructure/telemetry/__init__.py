"""Telemetry infrastructure package for OpenTelemetry distributed tracing and metrics."""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable  # noqa: TC003
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

# OpenTelemetry imports with graceful fallback
try:
    from opentelemetry import trace

    OPENTELEMETRY_AVAILABLE = True
except ImportError:  # pragma: no cover
    OPENTELEMETRY_AVAILABLE = False
    trace = None  # type: ignore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Telemetry Registry for Metric Exposition & Tracking
# ---------------------------------------------------------------------------


class TelemetryRegistry:
    """Thread-safe Prometheus metrics registry and OpenTelemetry tracer wrapper."""

    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._counter_labels: dict[str, dict[str, float]] = {}
        self._gauges: dict[str, float] = {}
        self._gauge_labels: dict[str, dict[str, float]] = {}
        self._histograms: dict[str, list[float]] = {}
        self._histogram_labels: dict[str, dict[str, list[float]]] = {}

        self._metric_help = {
            "cfi_fl_round_duration_seconds": "Duration of federated learning training rounds in seconds.",
            "cfi_fl_round_participants": "Number of participating bank nodes in current FL round.",
            "cfi_dp_epsilon_consumed_total": "Cumulative differential privacy epsilon budget consumed.",
            "cfi_spectral_anomalies_detected_total": "Total number of spectral anomalies detected by Byzantine defense.",
            "cfi_grpc_request_duration_seconds": "Latency of gRPC API requests in seconds.",
            "cfi_hsm_signing_duration_seconds": "Latency of Hardware Security Module (HSM) digital signing operations.",
            "cfi_node_heartbeat_timestamp": "Unix timestamp of the last received node heartbeat.",
        }

    def get_tracer(self, name: str = "cfi-platform") -> Any:
        """Return OpenTelemetry tracer or a lightweight fallback context manager."""
        if OPENTELEMETRY_AVAILABLE and trace is not None:
            return trace.get_tracer(name)
        return DummyTracer()

    def record_fl_round(self, duration_seconds: float, participant_count: int) -> None:
        """Record FL round duration and active participant count."""
        self._histograms.setdefault("cfi_fl_round_duration_seconds", []).append(duration_seconds)
        self._gauges["cfi_fl_round_participants"] = float(participant_count)

    def record_dp_epsilon(self, bank_id: str, epsilon: float) -> None:
        """Increment cumulative DP epsilon consumed for a specific bank node."""
        key = f'bank_id="{bank_id}"'
        labels = self._counter_labels.setdefault("cfi_dp_epsilon_consumed_total", {})
        labels[key] = labels.get(key, 0.0) + epsilon

    def record_spectral_anomaly(self, bank_id: str, anomaly_type: str = "poisoning") -> None:
        """Increment spectral anomaly detection count."""
        key = f'bank_id="{bank_id}",anomaly_type="{anomaly_type}"'
        labels = self._counter_labels.setdefault("cfi_spectral_anomalies_detected_total", {})
        labels[key] = labels.get(key, 0.0) + 1.0

    def record_grpc_latency(self, method: str, duration_seconds: float, status: str = "OK") -> None:
        """Record gRPC request latency with method and status labels."""
        key = f'method="{method}",status="{status}"'
        labels = self._histogram_labels.setdefault("cfi_grpc_request_duration_seconds", {})
        labels.setdefault(key, []).append(duration_seconds)

    def record_hsm_signing(self, key_type: str, duration_seconds: float) -> None:
        """Record HSM key signing latency."""
        key = f'key_type="{key_type}"'
        labels = self._histogram_labels.setdefault("cfi_hsm_signing_duration_seconds", {})
        labels.setdefault(key, []).append(duration_seconds)

    def record_node_heartbeat(self, bank_id: str, timestamp: float | None = None) -> None:
        """Record node heartbeat timestamp."""
        ts = timestamp if timestamp is not None else time.time()
        key = f'bank_id="{bank_id}"'
        labels = self._gauge_labels.setdefault("cfi_node_heartbeat_timestamp", {})
        labels[key] = ts

    def get_prometheus_metrics_text(self) -> str:
        """Render registered metrics in standard Prometheus exposition text format."""
        lines: list[str] = []

        # 1. Gauges
        for metric_name, value in self._gauges.items():
            lines.append(f"# HELP {metric_name} {self._metric_help.get(metric_name, '')}")
            lines.append(f"# TYPE {metric_name} gauge")
            lines.append(f"{metric_name} {value:.6f}")

        for metric_name, labels_dict in self._gauge_labels.items():
            lines.append(f"# HELP {metric_name} {self._metric_help.get(metric_name, '')}")
            lines.append(f"# TYPE {metric_name} gauge")
            for label_str, val in labels_dict.items():
                lines.append(f"{metric_name}{{{label_str}}} {val:.6f}")

        # 2. Counters
        for metric_name, labels_dict in self._counter_labels.items():
            lines.append(f"# HELP {metric_name} {self._metric_help.get(metric_name, '')}")
            lines.append(f"# TYPE {metric_name} counter")
            for label_str, val in labels_dict.items():
                lines.append(f"{metric_name}{{{label_str}}} {val:.6f}")

        # 3. Histograms
        for metric_name, values in self._histograms.items():
            if not values:
                continue
            lines.append(f"# HELP {metric_name} {self._metric_help.get(metric_name, '')}")
            lines.append(f"# TYPE {metric_name} summary")
            count = len(values)
            total_sum = sum(values)
            lines.append(f"{metric_name}_count {count}")
            lines.append(f"{metric_name}_sum {total_sum:.6f}")

        for metric_name, hist_labels_dict in self._histogram_labels.items():
            if not hist_labels_dict:
                continue
            lines.append(f"# HELP {metric_name} {self._metric_help.get(metric_name, '')}")
            lines.append(f"# TYPE {metric_name} summary")
            for label_str, hist_values in hist_labels_dict.items():
                count = len(hist_values)
                total_sum = sum(hist_values)
                lines.append(f"{metric_name}_count{{{label_str}}} {count}")
                lines.append(f"{metric_name}_sum{{{label_str}}} {total_sum:.6f}")

        lines.append("")
        return "\n".join(lines)

    def get_prometheus_metrics_bytes(self) -> bytes:
        """Render Prometheus metrics as UTF-8 encoded bytes."""
        return self.get_prometheus_metrics_text().encode("utf-8")


class DummySpan:
    """Fallback dummy OpenTelemetry span context manager."""

    def __enter__(self) -> DummySpan:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass


class DummyTracer:
    """Fallback dummy OpenTelemetry tracer."""

    def start_as_current_span(self, name: str, **kwargs: Any) -> DummySpan:
        return DummySpan()


# Global Singleton Registry Instance
telemetry = TelemetryRegistry()


def track_grpc_latency(method_name: str) -> Callable[..., Any]:
    """Decorator to measure and record gRPC handler execution duration."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            status = "OK"
            try:
                result = func(*args, **kwargs)
                return result
            except Exception:
                status = "ERROR"
                raise
            finally:
                duration = time.time() - start_time
                telemetry.record_grpc_latency(
                    method=method_name, duration_seconds=duration, status=status
                )

        return wrapper

    return decorator


def track_fl_round(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to measure FL round duration and record telemetry."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.time()
        result = func(*args, **kwargs)
        duration = time.time() - start_time
        participant_count = 0
        if isinstance(result, dict) and "participants" in result:
            participant_count = len(result["participants"])
        elif isinstance(result, (list, tuple)):
            participant_count = len(result)
        telemetry.record_fl_round(duration_seconds=duration, participant_count=participant_count)
        return result

    return wrapper


# ---------------------------------------------------------------------------
# Existing Legacy / FastAPI NoOp Handles
# ---------------------------------------------------------------------------


class _NoOpCounter:
    def add(self, amount: int | float, attributes: dict | None = None) -> None:
        pass


class _NoOpHistogram:
    def record(self, value: float, attributes: dict | None = None) -> None:
        pass

    def observe(self, value: float, attributes: dict | None = None) -> None:
        pass


class _NoOpUpDownCounter:
    def add(self, amount: int | float, attributes: dict | None = None) -> None:
        pass


class _NoOpGauge:
    def set(self, value: float, attributes: dict | None = None) -> None:
        pass

    def record(self, value: float, attributes: dict | None = None) -> None:
        pass


# Public metric handles
simulation_rounds_total: Any = _NoOpCounter()
simulation_duration_seconds: Any = _NoOpHistogram()
active_simulations: Any = _NoOpUpDownCounter()
alerts_generated_total: Any = _NoOpCounter()
http_request_duration_seconds: Any = _NoOpHistogram()

cfi_concept_drift_psi: Any = _NoOpGauge()
cfi_feature_drift_ks_stat: Any = _NoOpGauge()
cfi_model_brier_score: Any = _NoOpGauge()
cfi_model_ece: Any = _NoOpGauge()
cfi_active_alerts_count: Any = _NoOpGauge()
cfi_active_clients_count: Any = _NoOpGauge()
cfi_privacy_epsilon_consumed: Any = _NoOpGauge()
cfi_mia_attack_success_rate: Any = _NoOpGauge()
cfi_inference_latency_ms: Any = _NoOpHistogram()
cfi_dlg_gradient_leakage_score: Any = _NoOpGauge()


class JSONLogFormatter(logging.Formatter):
    """Structured JSON formatter for Loki log aggregation via Promtail."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        log_obj = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": getattr(record, "service", "cfi-backend"),
            "tenant_id": getattr(record, "tenant_id", "system"),
            "trace_id": getattr(record, "trace_id", "0" * 32),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def setup_telemetry(app: FastAPI) -> None:
    """Initialise OpenTelemetry tracing + metrics and attach to app."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.otel_enabled:
        logger.info("OpenTelemetry disabled (OTEL_ENABLED=false)")
        return

    try:
        import os

        from opentelemetry import metrics as otel_metrics
        from opentelemetry import trace as otel_trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from prometheus_client import make_asgi_app as make_prometheus_app

        try:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            has_push_metrics = True
        except ImportError:
            has_push_metrics = False
    except ImportError as exc:
        logger.warning("OpenTelemetry packages not installed — skipping instrumentation: %s", exc)
        return

    service_name = settings.otel_service_name
    resource = Resource.create({"service.name": service_name})

    endpoint = settings.otel_exporter_otlp_endpoint
    is_secure = endpoint.startswith("https://") if endpoint else False

    headers = {}
    headers_str = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
    if headers_str:
        for item in headers_str.split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                headers[k.strip()] = v.strip()

    tracer_provider = TracerProvider(resource=resource)
    otlp_exporter = OTLPSpanExporter(
        endpoint=endpoint,
        insecure=not is_secure,
        headers=headers,
    )
    tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    otel_trace.set_tracer_provider(tracer_provider)

    from opentelemetry.sdk.metrics.export import MetricReader

    metric_readers: list[MetricReader] = []
    use_push_metrics = has_push_metrics and (
        is_secure or os.environ.get("OTEL_METRICS_EXPORTER") == "otlp"
    )

    if use_push_metrics:
        otlp_metric_exporter = OTLPMetricExporter(
            endpoint=endpoint,
            insecure=not is_secure,
            headers=headers,
        )
        metric_readers.append(
            PeriodicExportingMetricReader(otlp_metric_exporter, export_interval_millis=15000)
        )
        logger.info("OpenTelemetry metrics push exporter enabled")
    else:
        metric_readers.append(PrometheusMetricReader())
        logger.info("OpenTelemetry metrics pull exporter (Prometheus scrape) enabled")

    meter_provider = MeterProvider(resource=resource, metric_readers=metric_readers)
    otel_metrics.set_meter_provider(meter_provider)

    meter = otel_metrics.get_meter("cfi.metrics", version="0.2.0")

    global simulation_rounds_total, simulation_duration_seconds  # noqa: PLW0603
    global active_simulations, alerts_generated_total, http_request_duration_seconds  # noqa: PLW0603
    global cfi_concept_drift_psi, cfi_feature_drift_ks_stat  # noqa: PLW0603
    global cfi_model_brier_score, cfi_model_ece, cfi_active_alerts_count  # noqa: PLW0603
    global cfi_active_clients_count  # noqa: PLW0603

    simulation_rounds_total = meter.create_counter(
        name="simulation_rounds_total",
        description="Total number of FL training rounds completed",
        unit="1",
    )
    simulation_duration_seconds = meter.create_histogram(
        name="simulation_duration_seconds",
        description="Duration of each FL training round",
        unit="s",
    )
    active_simulations = meter.create_up_down_counter(
        name="active_simulations",
        description="Number of currently running simulations",
        unit="1",
    )
    alerts_generated_total = meter.create_counter(
        name="alerts_generated_total",
        description="Total number of fraud alerts generated",
        unit="1",
    )
    http_request_duration_seconds = meter.create_histogram(
        name="http_request_duration_seconds",
        description="HTTP request latency by route",
        unit="s",
    )
    cfi_concept_drift_psi = meter.create_gauge(
        name="cfi_concept_drift_psi",
        description="Population Stability Index for model concept drift",
        unit="1",
    )
    cfi_feature_drift_ks_stat = meter.create_gauge(
        name="cfi_feature_drift_ks_stat",
        description="Max Kolmogorov-Smirnov test statistic across features",
        unit="1",
    )
    cfi_model_brier_score = meter.create_gauge(
        name="cfi_model_brier_score",
        description="Brier score for model probability calibration",
        unit="1",
    )
    cfi_model_ece = meter.create_gauge(
        name="cfi_model_ece",
        description="Expected Calibration Error (ECE)",
        unit="1",
    )
    cfi_active_alerts_count = meter.create_gauge(
        name="cfi_active_alerts_count",
        description="Number of active firing Alertmanager alerts",
        unit="1",
    )
    cfi_active_clients_count = meter.create_gauge(
        name="cfi_active_clients_count",
        description="Number of dynamically registered active FL client nodes",
        unit="1",
    )

    FastAPIInstrumentor.instrument_app(app)

    has_prometheus = any(isinstance(r, PrometheusMetricReader) for r in metric_readers)
    if has_prometheus:
        prometheus_asgi = make_prometheus_app()
        app.mount("/metrics", prometheus_asgi)
        logger.info(
            "OpenTelemetry enabled (Pull Mode) — traces → %s, metrics → /metrics",
            settings.otel_exporter_otlp_endpoint,
        )
    else:
        logger.info(
            "OpenTelemetry enabled (Push Mode) — metrics and traces pushing to OTLP endpoint: %s",
            settings.otel_exporter_otlp_endpoint,
        )
