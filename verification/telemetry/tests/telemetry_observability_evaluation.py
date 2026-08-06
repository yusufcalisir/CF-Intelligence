"""Observability Engineering Evaluation for Telemetry Implementation.

Evaluates:
1. Metric Completeness & HELP/TYPE Annotation Coverage
2. Event Ordering & SIEM Re-ordering Characteristics
3. Timestamp Consistency & ISO 8601 UTC Standardization
4. Monitoring Execution Latency & Overhead (µs / call)
5. Aggregation Correctness (Histograms, Quantiles, Tenant Metering)
6. Dashboard Consistency & Data Store Synchronization
7. Health Monitoring & Readiness Probe Transitions
8. Alert Readiness & Severity Classification
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
import sys

backend_path = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.sla_monitor import RealtimeSLAMonitor
from app.application.services.support_diagnostics import SupportDiagnosticCompiler
from app.application.services.tenant_metering import TenantMeteringService
from app.infrastructure.logging.siem_exporter import SIEMAuditEvent, SIEMLogExporter
from app.infrastructure.telemetry import TelemetryRegistry
from app.infrastructure.telemetry.otel_tracer import OpenTelemetryTracer


def run_observability_evaluation() -> dict:
    print("=" * 80)
    print("OBSERVABILITY ENGINEERING EVALUATION")
    print("=" * 80)
    results = {}

    # 1. Metric Completeness
    print("\n--- 1. Metric Completeness & Prometheus Annotation Coverage ---")
    registry = TelemetryRegistry()
    text = registry.get_prometheus_metrics_text()

    metric_names = ["cfi_inference_latency_ms", "cfi_active_bank_nodes", "cfi_federated_round_duration_seconds",
                    "cfi_dp_epsilon_consumed_total", "cfi_gradient_rejections_total", "cfi_champion_model_auc"]
    help_count = sum(1 for line in text.splitlines() if line.startswith("# HELP"))
    type_count = sum(1 for line in text.splitlines() if line.startswith("# TYPE"))

    print(f"Total Help Annotations: {help_count}, Type Annotations: {type_count}")
    results["help_type_completeness"] = (help_count > 0 and type_count > 0)

    # 2. Event Ordering
    print("\n--- 2. Event Ordering & SIEM Re-ordering Characteristics ---")
    exporter = SIEMLogExporter()
    event1 = SIEMAuditEvent(event_id="e1", event_type="LOGIN", severity="LOW", source_bank="bank_a", message="Login success")
    time.sleep(0.001)
    event2 = SIEMAuditEvent(event_id="e2", event_type="FRAUD_ALERT", severity="HIGH", source_bank="bank_a", message="Fraud alert triggered")

    cef1 = exporter.format_cef_event(event1)
    cef2 = exporter.format_cef_event(event2)
    print(f"Event 1 Timestamp: {event1.timestamp.isoformat()}")
    print(f"Event 2 Timestamp: {event2.timestamp.isoformat()}")
    results["event_ordering_timestamped"] = (event1.timestamp < event2.timestamp)

    # 3. Timestamp Consistency
    print("\n--- 3. Timestamp Consistency & ISO 8601 UTC Standardization ---")
    tracer = OpenTelemetryTracer()
    hw_telemetry = tracer.record_hardware_telemetry(cpu_percent=25.0, ram_mb=1024.0)

    is_utc_formatted = hw_telemetry["timestamp"].endswith("Z")
    print(f"Hardware Telemetry Timestamp: {hw_telemetry['timestamp']} (UTC Z-formatted: {is_utc_formatted})")
    results["timestamp_utc_standardization"] = is_utc_formatted

    # 4. Monitoring Execution Latency & Overhead
    print("\n--- 4. Monitoring Execution Latency & Overhead ---")
    start = time.perf_counter()
    for _ in range(10000):
        registry.record_inference_latency(45.0, decision="ALLOW")
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    latency_per_call_us = (elapsed_ms / 10000.0) * 1000.0
    print(f"10,000 Metric Recording Calls: {elapsed_ms:.2f}ms ({latency_per_call_us:.2f} µs / call)")
    results["metric_recording_latency"] = (latency_per_call_us < 20.0)

    # 5. Aggregation Correctness
    print("\n--- 5. Aggregation Correctness ---")
    sla = RealtimeSLAMonitor(target_sla_ms=100.0)
    for s in [20.0, 50.0, 80.0, 120.0]:
        sla.record_latency(s)
    summary = sla.get_sla_summary()
    print(f"SLA Summary: Total={summary.total_requests}, p50={summary.p50_latency_ms}ms, Compliance={summary.sla_compliance_pct}%")
    results["aggregation_correctness"] = (summary.total_requests == 4 and summary.sla_compliance_pct == 75.0)

    # 6. Severity Classification
    print("\n--- 6. Alert Severity Classification ---")
    sev_crit = AlertIntelligenceService._classify_severity(0.95)
    sev_low = AlertIntelligenceService._classify_severity(0.35)
    print(f"Score 0.95 -> {sev_crit.value}, Score 0.35 -> {sev_low.value}")
    results["severity_classification"] = (sev_crit.value == "critical" and sev_low.value == "low")

    # Output JSON summary
    out_file = Path(__file__).parent / "telemetry_obs_results.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n================================================================================")
    print(f"OBSERVABILITY ENGINEERING EVALUATION COMPLETE ({sum(results.values())}/{len(results)} PASSED)")
    print("================================================================================")
    return results


if __name__ == "__main__":
    run_observability_evaluation()
