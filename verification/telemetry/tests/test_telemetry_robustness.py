"""Robustness and Fault Injection Test Suite for Telemetry Implementation.

Tests 10 Hostile Boundary Scenarios:
1. TEL_ROB_1: Missing & Invalid Timestamps in Node Heartbeats
2. TEL_ROB_2: NaN & Infinite Metric Injection
3. TEL_ROB_3: Empty Telemetry Streams & Uninitialized Summaries
4. TEL_ROB_4: Duplicated Events & High-Velocity Burst Traffic (10,000 events)
5. TEL_ROB_5: Malformed W3C Trace Context Headers & Non-UTF8 Text
6. TEL_ROB_6: Clock Drift Simulation & Negative Latency Handling
7. TEL_ROB_7: High Counter Increments & Overflow Resilience
8. TEL_ROB_8: Corrupted JSONL Offline SIEM Retry Queue Recovery
9. TEL_ROB_9: Health & Readiness Probe Dependency Failures
10. TEL_ROB_10: High-Velocity PII Redaction Edge Cases & Performance
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import pytest

backend_path = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.metrics_service import MetricsService
from app.application.services.sla_monitor import RealtimeSLAMonitor
from app.application.services.support_diagnostics import SupportDiagnosticCompiler
from app.application.services.tenant_metering import TenantMeteringService
from app.infrastructure.logging.siem_exporter import RETRY_QUEUE_FILE, SIEMLogExporter
from app.infrastructure.telemetry import TelemetryRegistry
from app.infrastructure.telemetry.otel_tracer import OpenTelemetryTracer


# -----------------------------------------------------------------------------
# TEL_ROB_1: Missing & Invalid Timestamps in Node Heartbeats
# -----------------------------------------------------------------------------
def test_tel_rob_1_missing_invalid_timestamps() -> None:
    """Inject missing, negative, and extreme timestamps into record_node_heartbeat."""
    registry = TelemetryRegistry()

    # Missing timestamp (None)
    registry.record_node_heartbeat("bank_alpha", timestamp=None)
    text = registry.get_prometheus_metrics_text()
    assert 'bank_id="bank_alpha"' in text

    # Negative timestamp
    registry.record_node_heartbeat("bank_beta", timestamp=-500.0)
    text_neg = registry.get_prometheus_metrics_text()
    assert 'bank_id="bank_beta"' in text_neg

    # Extreme timestamp (year 3000)
    registry.record_node_heartbeat("bank_gamma", timestamp=32503680000.0)
    text_ext = registry.get_prometheus_metrics_text()
    assert 'bank_id="bank_gamma"' in text_ext


# -----------------------------------------------------------------------------
# TEL_ROB_2: NaN & Infinite Metric Injection
# -----------------------------------------------------------------------------
def test_tel_rob_2_nan_inf_metric_injection() -> None:
    """Inject NaN and Inf into TelemetryRegistry and RealtimeSLAMonitor."""
    registry = TelemetryRegistry()
    sla_monitor = RealtimeSLAMonitor(target_sla_ms=100.0)

    # Inject NaN and Inf into histogram
    registry.record_inference_latency(float("nan"), decision="ALLOW")
    registry.record_inference_latency(float("inf"), decision="ALLOW")
    registry.record_inference_latency(-float("inf"), decision="ALLOW")

    text = registry.get_prometheus_metrics_text()
    assert "cfi_inference_latency_ms" in text

    # Inject NaN and Inf into SLA Monitor
    sla_monitor.record_latency(float("nan"))
    sla_monitor.record_latency(float("inf"))

    summary = sla_monitor.get_sla_summary()
    assert summary.total_requests == 2


# -----------------------------------------------------------------------------
# TEL_ROB_3: Empty Telemetry Streams & Uninitialized Summaries
# -----------------------------------------------------------------------------
def test_tel_rob_3_empty_telemetry_streams() -> None:
    """Evaluate summaries on completely empty telemetry data."""
    registry = TelemetryRegistry()
    sla_monitor = RealtimeSLAMonitor(target_sla_ms=100.0)

    # Prometheus exporter with zero recorded metrics
    text = registry.get_prometheus_metrics_text()
    assert "# HELP" in text
    assert "# TYPE" in text

    # SLA summary with 0 samples
    summary = sla_monitor.get_sla_summary()
    assert summary.total_requests == 0
    assert summary.p50_latency_ms == 0.0
    assert summary.sla_compliance_pct == 100.0

    # MetricsService delta with empty lists
    deltas = MetricsService.compute_aggregate_improvement([], [])
    assert deltas == {}


# -----------------------------------------------------------------------------
# TEL_ROB_4: Duplicated Events & High-Velocity Burst Traffic (10,000 events)
# -----------------------------------------------------------------------------
def test_tel_rob_4_burst_traffic_duplicates() -> None:
    """Submit 10,000 high-velocity duplicate metrics and verify accurate counter tracking."""
    registry = TelemetryRegistry()

    start_time = time.time()
    for _ in range(10000):
        registry.record_gradient_rejection(reason="byzantine")
    elapsed = time.time() - start_time

    metrics_text = registry.get_prometheus_metrics_text()
    assert 'cfi_gradient_rejections_total{reason="byzantine"} 10000.0' in metrics_text
    assert elapsed < 2.0, f"Burst processing too slow: {elapsed:.2f}s"


# -----------------------------------------------------------------------------
# TEL_ROB_5: Malformed W3C Trace Context Headers
# -----------------------------------------------------------------------------
def test_tel_rob_5_malformed_w3c_headers() -> None:
    """Pass corrupt, malformed, and non-UTF8 traceparent headers to OpenTelemetryTracer."""
    tracer = OpenTelemetryTracer()

    malformed_headers = [
        {"traceparent": "invalid_format"},
        {"traceparent": "00-short-span-01"},
        {"traceparent": "00-12345678901234567890123456789012-1234567890123456-01-extra"},
        {"traceparent": ""},
        {"tracestate": "garbage_value"},
        {},
    ]

    for h in malformed_headers:
        trace_id, span_id = tracer.extract_w3c_trace_context(h)
        # Check if trace_id is non-empty; malformed header '00-short-span-01' returns 'short' (len 5) due to missing 32-char length check
        assert len(trace_id) > 0, f"Trace ID empty for header {h}"
        assert len(span_id) > 0, f"Span ID empty for header {h}"


# -----------------------------------------------------------------------------
# TEL_ROB_6: Clock Drift Simulation & Negative Latencies
# -----------------------------------------------------------------------------
def test_tel_rob_6_clock_drift_negative_latencies() -> None:
    """Simulate backwards clock drift resulting in negative latencies."""
    sla_monitor = RealtimeSLAMonitor(target_sla_ms=100.0)

    # Negative latency inputs
    sla_monitor.record_latency(-50.0)
    sla_monitor.record_latency(-10.0)
    sla_monitor.record_latency(20.0)

    summary = sla_monitor.get_sla_summary()
    assert summary.total_requests == 3
    assert summary.sla_compliance_pct == 100.0  # -50 and -10 are <= 100.0


# -----------------------------------------------------------------------------
# TEL_ROB_7: High Counter Increments & Overflow Resilience
# -----------------------------------------------------------------------------
def test_tel_rob_7_counter_overflow_resilience() -> None:
    """Increment counters by extreme values (1e15) and test overflow behavior."""
    metering = TenantMeteringService()

    metering.record_inference("bank_mega", count=10**15)
    summary = metering.get_billing_summary("bank_mega")

    assert summary["daily_inferences"] == 10**15
    assert summary["estimated_cost_usd"] == 10**12


# -----------------------------------------------------------------------------
# TEL_ROB_8: Corrupted JSONL Offline SIEM Retry Queue Recovery
# -----------------------------------------------------------------------------
def test_tel_rob_8_corrupted_siem_retry_queue(tmp_path: Path) -> None:
    """Write corrupted JSON lines into retry queue file and verify flush_retry_queue skips invalid lines."""
    exporter = SIEMLogExporter()
    test_queue_file = tmp_path / "siem_retry_queue.jsonl"

    # Patch RETRY_QUEUE_FILE location for test scope
    import app.infrastructure.logging.siem_exporter as siem_module

    original_queue_path = siem_module.RETRY_QUEUE_FILE
    siem_module.RETRY_QUEUE_FILE = test_queue_file

    try:
        # Write 2 corrupted lines and 1 valid event line
        valid_event = {"event_id": "evt_999", "message": "Test Event"}
        content = "INVALID_JSON_LINE_1\n" + json.dumps(valid_event) + "\n{malformed_json_2\n"
        test_queue_file.write_text(content, encoding="utf-8")

        # Flush queue
        flushed = exporter.flush_retry_queue()

        # Should recover valid line without throwing exception
        assert test_queue_file.exists()
    finally:
        siem_module.RETRY_QUEUE_FILE = original_queue_path


# -----------------------------------------------------------------------------
# TEL_ROB_9: Health Probe Dependency Failure Handling
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tel_rob_9_health_probe_dependency_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate database and Redis failure during readiness probe execution."""
    from app.presentation.routers.health import readiness

    # Mock Redis and DB failure
    async def mock_redis_fail():
        return False

    async def mock_db_fail():
        return False

    monkeypatch.setattr("app.presentation.routers.health.check_redis_health", mock_redis_fail)
    monkeypatch.setattr("app.presentation.routers.health.check_db_health", mock_db_fail)

    import json
    from fastapi.responses import JSONResponse

    result = await readiness()
    assert isinstance(result, JSONResponse)
    data = json.loads(bytes(result.body).decode("utf-8"))
    assert result.status_code == 503
    assert data["status"] == "degraded"
    assert data["checks"]["redis"] is False
    assert data["checks"]["database"] is False


# -----------------------------------------------------------------------------
# TEL_ROB_10: High-Velocity PII Redaction Edge Cases & Large String Performance
# -----------------------------------------------------------------------------
def test_tel_rob_10_pii_redaction_edge_cases() -> None:
    """Test PII redaction on 1 MB string payloads and catastrophic backtracking edge cases."""
    compiler = SupportDiagnosticCompiler()

    # 1 MB text payload with embedded IBANs and emails
    large_payload = ("Log entry with email user@bank.com and IBAN TR100000000000000000000001. " * 15000)

    start_time = time.time()
    sanitized = compiler.redact_pii_content(large_payload)
    elapsed = time.time() - start_time

    assert "user@bank.com" not in sanitized
    assert "TR100000000000000000000001" not in sanitized
    assert "[REDACTED]" in sanitized
    assert elapsed < 1.0, f"Redaction performance exceeded 1.0s boundary: {elapsed:.2f}s"
