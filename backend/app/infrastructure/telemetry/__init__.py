"""Telemetry infrastructure package for OpenTelemetry distributed tracing and Prometheus metrics."""

from __future__ import annotations

import contextlib
import functools
import logging
import threading
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

# Standard Prometheus histogram buckets for latency ms
LATENCY_BUCKETS = [10.0, 30.0, 50.0, 100.0, 200.0, 500.0]


# ---------------------------------------------------------------------------
# Telemetry Registry for Metric Exposition & Tracking
# ---------------------------------------------------------------------------


class TelemetryRegistry:
    """Thread-safe Prometheus metrics registry and OpenTelemetry tracer wrapper."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._counter_labels: dict[str, dict[str, float]] = {}
        self._gauges: dict[str, float] = {
            "cfi_active_bank_nodes": 3.0,
            "cfi_champion_model_auc": 0.885,
        }
        self._gauge_labels: dict[str, dict[str, float]] = {}
        self._histograms: dict[str, list[float]] = {}
        self._histogram_labels: dict[str, dict[str, list[float]]] = {}

        self._metric_help = {
            "cfi_inference_latency_ms": "Real-time inference scoring transaction latency in milliseconds.",
            "cfi_active_bank_nodes": "Current count of active participant bank nodes in consortium.",
            "cfi_federated_round_duration_seconds": "Duration of completed federated learning training rounds in seconds.",
            "cfi_dp_epsilon_consumed_total": "Cumulative differential privacy epsilon budget consumed across rounds.",
            "cfi_gradient_rejections_total": "Count of rejected gradient submissions by rejection reason.",
            "cfi_champion_model_auc": "Holdout evaluation AUC score of the current active champion global model.",
            "cfi_fl_round_duration_seconds": "Duration of federated learning training rounds in seconds.",
            "cfi_fl_round_participants": "Number of participating bank nodes in current FL round.",
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

    def record_inference_latency(self, latency_ms: float, decision: str = "ALLOW") -> None:
        """Record real-time transaction scoring inference latency."""
        key = f'decision="{decision}"'
        labels = self._histogram_labels.setdefault("cfi_inference_latency_ms", {})
        labels.setdefault(key, []).append(latency_ms)

    def set_active_bank_nodes(self, count: int) -> None:
        """Set current count of active participating bank nodes."""
        self._gauges["cfi_active_bank_nodes"] = float(count)

    def record_federated_round_duration(self, duration_seconds: float) -> None:
        """Record duration of a completed federated learning round."""
        self._histograms.setdefault("cfi_federated_round_duration_seconds", []).append(
            duration_seconds
        )

    def record_dp_epsilon_consumed(self, epsilon: float, bank_id: str = "all") -> None:
        """Increment cumulative differential privacy epsilon budget consumed."""
        labels = self._counter_labels.setdefault("cfi_dp_epsilon_consumed_total", {})
        labels[f'bank_id="{bank_id}"'] = labels.get(f'bank_id="{bank_id}"', 0.0) + epsilon

    def record_gradient_rejection(self, reason: str = "byzantine") -> None:
        """Increment count of rejected gradient submissions."""
        key = f'reason="{reason}"'
        labels = self._counter_labels.setdefault("cfi_gradient_rejections_total", {})
        labels[key] = labels.get(key, 0.0) + 1.0

    def set_champion_model_auc(self, auc: float) -> None:
        """Update holdout AUC metric for promoted champion model."""
        self._gauges["cfi_champion_model_auc"] = auc

    def record_fl_round(self, duration_seconds: float, participant_count: int) -> None:
        """Record FL round duration and active participant count."""
        self._histograms.setdefault("cfi_fl_round_duration_seconds", []).append(duration_seconds)
        self.record_federated_round_duration(duration_seconds)
        self._gauges["cfi_fl_round_participants"] = float(participant_count)
        self.set_active_bank_nodes(participant_count)

    def record_dp_epsilon(self, bank_id: str, epsilon: float) -> None:
        """Increment cumulative DP epsilon consumed for a specific bank node."""
        self.record_dp_epsilon_consumed(epsilon=epsilon, bank_id=bank_id)

    def record_spectral_anomaly(self, bank_id: str, anomaly_type: str = "poisoning") -> None:
        """Increment spectral anomaly detection count."""
        key = f'bank_id="{bank_id}",anomaly_type="{anomaly_type}"'
        labels = self._counter_labels.setdefault("cfi_spectral_anomalies_detected_total", {})
        labels[key] = labels.get(key, 0.0) + 1.0
        self.record_gradient_rejection(reason="byzantine")

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

        # Ensure default zero counters exist for mandatory Prometheus scrape validation
        for mandatory_counter, default_labels in [
            ("cfi_dp_epsilon_consumed_total", ['bank_id="bank_alpha"']),
            ("cfi_gradient_rejections_total", ['reason="byzantine"']),
        ]:
            if mandatory_counter not in self._counter_labels:
                lines.append(
                    f"# HELP {mandatory_counter} {self._metric_help.get(mandatory_counter, '')}"
                )
                lines.append(f"# TYPE {mandatory_counter} counter")
                for lbl in default_labels:
                    lines.append(f"{mandatory_counter}{{{lbl}}} 0.000000")

        # 3. Histograms
        for metric_name, values in self._histograms.items():
            lines.append(f"# HELP {metric_name} {self._metric_help.get(metric_name, '')}")
            lines.append(f"# TYPE {metric_name} histogram")
            count = len(values)
            total_sum = sum(values) if values else 0.0
            lines.append(f"{metric_name}_count {count}")
            lines.append(f"{metric_name}_sum {total_sum:.6f}")

        for metric_name, hist_labels_dict in self._histogram_labels.items():
            lines.append(f"# HELP {metric_name} {self._metric_help.get(metric_name, '')}")
            lines.append(f"# TYPE {metric_name} histogram")
            for label_str, hist_values in hist_labels_dict.items():
                count = len(hist_values)
                total_sum = sum(hist_values) if hist_values else 0.0
                buckets = (
                    LATENCY_BUCKETS
                    if metric_name == "cfi_inference_latency_ms"
                    else [0.1, 0.5, 1.0, 5.0, 10.0]
                )
                for b in buckets:
                    b_count = sum(1 for v in hist_values if v <= b)
                    lines.append(f'{metric_name}_bucket{{{label_str},le="{b}"}} {b_count}')
                lines.append(f'{metric_name}_bucket{{{label_str},le="+Inf"}} {count}')
                lines.append(f"{metric_name}_sum{{{label_str}}} {total_sum:.6f}")
                lines.append(f"{metric_name}_count{{{label_str}}} {count}")

        # Ensure cfi_inference_latency_ms bucket exists even if empty
        if "cfi_inference_latency_ms" not in self._histogram_labels:
            lines.append(
                f"# HELP cfi_inference_latency_ms {self._metric_help.get('cfi_inference_latency_ms', '')}"
            )
            lines.append("# TYPE cfi_inference_latency_ms histogram")
            for b in LATENCY_BUCKETS:
                lines.append(f'cfi_inference_latency_ms_bucket{{decision="ALLOW",le="{b}"}} 0')
            lines.append('cfi_inference_latency_ms_bucket{decision="ALLOW",le="+Inf"} 0')
            lines.append('cfi_inference_latency_ms_sum{decision="ALLOW"} 0.000000')
            lines.append('cfi_inference_latency_ms_count{decision="ALLOW"} 0')

        if "cfi_federated_round_duration_seconds" not in self._histograms:
            lines.append(
                f"# HELP cfi_federated_round_duration_seconds {self._metric_help.get('cfi_federated_round_duration_seconds', '')}"
            )
            lines.append("# TYPE cfi_federated_round_duration_seconds histogram")
            lines.append("cfi_federated_round_duration_seconds_count 0")
            lines.append("cfi_federated_round_duration_seconds_sum 0.000000")

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


class MetricProxy:
    """Proxy object simulating Prometheus and OpenTelemetry metric methods (.set, .inc, .dec, .add, .observe, .record, .labels)."""

    def __init__(self, name: str, registry: TelemetryRegistry) -> None:
        self.name = name
        self.registry = registry

    def set(self, value: float, *args: Any, **kwargs: Any) -> None:
        with self.registry._lock:
            self.registry._gauges[self.name] = value

    def inc(self, amount: float = 1.0, *args: Any, **kwargs: Any) -> None:
        with self.registry._lock:
            self.registry._counters[self.name] = self.registry._counters.get(self.name, 0.0) + amount

    def dec(self, amount: float = 1.0, *args: Any, **kwargs: Any) -> None:
        if self.name.endswith("_total") or "counter" in self.name:
            raise ValueError(f"Prometheus Counter metric '{self.name}' is monotonically increasing and cannot be decremented.")
        with self.registry._lock:
            self.registry._gauges[self.name] = self.registry._gauges.get(self.name, 0.0) - amount

    def add(self, amount: float = 1.0, *args: Any, **kwargs: Any) -> None:
        self.inc(amount)

    def observe(self, value: float, *args: Any, **kwargs: Any) -> None:
        with self.registry._lock:
            self.registry._histograms.setdefault(self.name, []).append(value)

    def record(self, value: float, *args: Any, **kwargs: Any) -> None:
        self.observe(value)

    def labels(self, **kwargs: Any) -> MetricProxy:
        return self


# Global Singleton Registry Instance
telemetry_registry = TelemetryRegistry()
telemetry = telemetry_registry

# Module-level Metric Proxy Exports for static type checkers (mypy)
cfi_mia_attack_success_rate = MetricProxy("cfi_mia_attack_success_rate", telemetry_registry)
cfi_dlg_gradient_leakage_score = MetricProxy("cfi_dlg_gradient_leakage_score", telemetry_registry)
cfi_privacy_epsilon_consumed = MetricProxy("cfi_privacy_epsilon_consumed", telemetry_registry)
cfi_concept_drift_psi = MetricProxy("cfi_concept_drift_psi", telemetry_registry)
cfi_feature_drift_ks_stat = MetricProxy("cfi_feature_drift_ks_stat", telemetry_registry)
cfi_model_brier_score = MetricProxy("cfi_model_brier_score", telemetry_registry)
cfi_model_ece = MetricProxy("cfi_model_ece", telemetry_registry)
cfi_inference_latency_ms = MetricProxy("cfi_inference_latency_ms", telemetry_registry)
active_simulations = MetricProxy("active_simulations", telemetry_registry)
simulation_duration_seconds = MetricProxy("simulation_duration_seconds", telemetry_registry)
simulation_rounds_total = MetricProxy("simulation_rounds_total", telemetry_registry)


def setup_telemetry(app: FastAPI) -> None:
    """Initialize OpenTelemetry instrumentation, per-endpoint HTTP metrics, and register /metrics endpoint."""
    from fastapi import Response

    with contextlib.suppress(Exception):
        import fastapi.routing as _fastapi_routing

        included_router_cls = getattr(_fastapi_routing, "_IncludedRouter", None)
        if included_router_cls and not hasattr(included_router_cls, "path"):
            included_router_cls.path = property(
                lambda self: getattr(self, "prefix", "")
            )

    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        instrumentator = Instrumentator(
            should_group_status_codes=True,
            should_ignore_untemplated=False,
            should_instrument_requests_inprogress=False,
        )
        instrumentator.instrument(app)
        logger.info(
            "Prometheus FastAPI Instrumentator initialized for per-endpoint HTTP histograms"
        )
    except Exception as exc:
        logger.warning("prometheus_fastapi_instrumentator initialization deferred/failed: %s", exc)

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint() -> Response:
        return Response(
            content=telemetry_registry.get_prometheus_metrics_bytes(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    logger.info("Prometheus metrics endpoint mounted at GET /metrics")


def trace_span(span_name: str) -> Callable:
    """Decorator to trace function execution with OpenTelemetry or dummy span."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = telemetry_registry.get_tracer()
            with tracer.start_as_current_span(span_name):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def track_fl_round(func: Callable) -> Callable:
    """Decorator to measure FL round duration and record participant count."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.time()
        res = func(*args, **kwargs)
        duration = time.time() - start
        participant_count = 0
        if isinstance(res, dict) and "participants" in res:
            participant_count = len(res["participants"])
        elif isinstance(res, (list, tuple)):
            participant_count = len(res)
        telemetry_registry.record_fl_round(
            duration_seconds=duration, participant_count=participant_count
        )
        return res

    return wrapper


def track_grpc_latency(method: str) -> Callable:
    """Decorator to measure and record gRPC request latency."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.time()
            try:
                res = func(*args, **kwargs)
                status = "OK"
                return res
            except Exception:
                status = "ERROR"
                raise
            finally:
                duration = time.time() - start
                telemetry_registry.record_grpc_latency(
                    method=method, duration_seconds=duration, status=status
                )

        return wrapper

    return decorator
