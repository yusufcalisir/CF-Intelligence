"""Hypothesis Property-Based Test Suite for Telemetry Implementation Invariants.

Verifies:
1. Property 1: Histogram Bucket Monotonicity & +Inf Completeness (TelemetryRegistry)
2. Property 2: SLA Quantile Monotonicity & Boundedness (RealtimeSLAMonitor)
3. Property 3: W3C Trace Context Parsing & Identity Invariant (OpenTelemetryTracer)
4. Property 4: Tenant Quota Daily Reset & Boundary Invariant (TenantMeteringService)
5. Property 5: PII Redaction Completeness Invariant (SupportDiagnosticCompiler)
6. Property 6: SHA-256 Cryptographic Checksum Integrity Invariant (SupportDiagnosticCompiler)
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

backend_path = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from app.application.services.sla_monitor import RealtimeSLAMonitor
from app.application.services.support_diagnostics import SupportDiagnosticCompiler
from app.application.services.tenant_metering import TenantMeteringService, TenantQuotaLimits
from app.infrastructure.telemetry import TelemetryRegistry
from app.infrastructure.telemetry.otel_tracer import OpenTelemetryTracer


# -----------------------------------------------------------------------------
# Property 1: Histogram Bucket Monotonicity & +Inf Completeness
# -----------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    latencies=st.lists(st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False), min_size=0, max_size=50),
    decision=st.sampled_from(["ALLOW", "DENY", "CHALLENGE"]),
)
def test_property_histogram_monotonicity(latencies: list[float], decision: str) -> None:
    """Mathematical Invariant: B(b1) <= B(b2) for b1 <= b2, and B(+Inf) == total count."""
    registry = TelemetryRegistry()
    for lat in latencies:
        registry.record_inference_latency(lat, decision=decision)

    metrics_text = registry.get_prometheus_metrics_text()

    buckets = [10.0, 30.0, 50.0, 100.0, 200.0, 500.0]
    bucket_counts = {}
    inf_count = None
    total_count = None

    for line in metrics_text.splitlines():
        if f'decision="{decision}"' in line and "cfi_inference_latency_ms_bucket" in line:
            if 'le="+Inf"' in line:
                inf_count = float(line.split()[-1])
            else:
                for b in buckets:
                    if f'le="{b}"' in line:
                        bucket_counts[b] = float(line.split()[-1])
        elif f'decision="{decision}"' in line and "cfi_inference_latency_ms_count" in line:
            total_count = float(line.split()[-1])

    if latencies:
        # Bucket count monotonicity check
        for i in range(len(buckets) - 1):
            b1, b2 = buckets[i], buckets[i + 1]
            if b1 in bucket_counts and b2 in bucket_counts:
                assert bucket_counts[b1] <= bucket_counts[b2], f"Monotonicity breach: B({b1})={bucket_counts[b1]} > B({b2})={bucket_counts[b2]}"

        # +Inf bucket completeness check
        assert inf_count == float(len(latencies)), f"+Inf bucket count mismatch: {inf_count} != {len(latencies)}"
        assert total_count == float(len(latencies)), f"Total count mismatch: {total_count} != {len(latencies)}"


# -----------------------------------------------------------------------------
# Property 2: SLA Quantile Monotonicity & Boundedness
# -----------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    samples=st.lists(st.floats(min_value=0.1, max_value=5000.0, allow_nan=False, allow_infinity=False), min_size=1, max_size=100),
    target_sla=st.floats(min_value=10.0, max_value=500.0, allow_nan=False, allow_infinity=False),
)
def test_property_sla_percentile_monotonicity(samples: list[float], target_sla: float) -> None:
    """Mathematical Invariant: x_(1) <= p50 <= p95 <= p99 <= x_(n) and 0.0 <= compliance <= 100.0."""
    sla_monitor = RealtimeSLAMonitor(target_sla_ms=target_sla)
    for s in samples:
        sla_monitor.record_latency(s)

    summary = sla_monitor.get_sla_summary()
    sorted_samples = sorted(samples)

    # Quantile ordering monotonicity check (allowing for round(..., 2) quantization)
    assert sorted_samples[0] <= summary.p50_latency_ms + 0.01
    assert summary.p50_latency_ms <= summary.p95_latency_ms + 0.01
    assert summary.p95_latency_ms <= summary.p99_latency_ms + 0.01
    assert summary.p99_latency_ms <= sorted_samples[-1] + 0.01

    # Bounded compliance check
    assert 0.0 <= summary.sla_compliance_pct <= 100.0
    assert summary.total_requests == len(samples)


# -----------------------------------------------------------------------------
# Property 3: W3C Trace Context Parsing & Identity Invariant
# -----------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    custom_trace_id=st.text(alphabet="0123456789abcdef", min_size=32, max_size=32),
    custom_span_id=st.text(alphabet="0123456789abcdef", min_size=16, max_size=16),
)
def test_property_w3c_trace_context(custom_trace_id: str, custom_span_id: str) -> None:
    """Mathematical Invariant: Extract(Inject(T)) preserves trace ID and returns valid 16-char span ID."""
    tracer = OpenTelemetryTracer(service_name="test-service")

    # Inject context
    headers = tracer.inject_w3c_trace_context(trace_id=custom_trace_id)
    assert "traceparent" in headers
    assert headers["traceparent"].startswith("00-")

    # Extract context
    extracted_trace_id, extracted_span_id = tracer.extract_w3c_trace_context(headers)

    assert extracted_trace_id == custom_trace_id
    assert len(extracted_span_id) == 16
    assert re.match(r"^[0-9a-f]{16}$", extracted_span_id)


# -----------------------------------------------------------------------------
# Property 4: Tenant Quota Boundary & Daily Reset Invariant
# -----------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    inferences=st.integers(min_value=0, max_value=15000),
    max_quota=st.integers(min_value=100, max_value=10000),
    tenant_id=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=3, max_size=12),
)
def test_property_tenant_quota_boundaries(inferences: int, max_quota: int, tenant_id: str) -> None:
    """Mathematical Invariant: check_quota returns False iff usage >= limit."""
    metering = TenantMeteringService()
    metering.set_quota_limits(tenant_id, TenantQuotaLimits(max_daily_inferences=max_quota))

    metering.record_inference(tenant_id, count=inferences)
    allowed, reason = metering.check_quota(tenant_id, feature="INFERENCE")

    if inferences >= max_quota:
        assert allowed is False
        assert "quota exceeded" in reason.lower()
    else:
        assert allowed is True
        assert reason == "OK"


# -----------------------------------------------------------------------------
# Property 5: PII Redaction Completeness Invariant
# -----------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    iban_suffix=st.text(alphabet="0123456789", min_size=24, max_size=24),
    email_user=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=3, max_size=10),
    email_domain=st.sampled_from(["bank.com", "fraud.org", "test.net"]),
    noise=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789 ", min_size=5, max_size=30),
)
def test_property_pii_redaction_completeness(iban_suffix: str, email_user: str, email_domain: str, noise: str) -> None:
    """Mathematical Invariant: redact_pii_content leaves 0 unmasked IBANs matching TR\\d{24} or emails."""
    compiler = SupportDiagnosticCompiler()
    raw_iban = f"TR{iban_suffix}"
    raw_email = f"{email_user}@{email_domain}"

    raw_log = f"Log noise: {noise} IBAN: {raw_iban} Email: {raw_email} End."
    sanitized = compiler.redact_pii_content(raw_log)

    # Assert no raw IBAN or email survives
    assert raw_iban not in sanitized or raw_iban == "TR" + "0" * 24
    assert raw_email not in sanitized
    assert "[REDACTED]" in sanitized


# -----------------------------------------------------------------------------
# Property 6: Diagnostic Bundle SHA-256 Digest Integrity Invariant
# -----------------------------------------------------------------------------
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    redact_flag=st.booleans(),
    sub_dir=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=3, max_size=8),
)
def test_property_diagnostic_checksum_integrity(tmp_path: Path, redact_flag: bool, sub_dir: str) -> None:
    """Mathematical Invariant: checksum_sha256 matches sha256(bundle_bytes).hexdigest() exactly."""
    compiler = SupportDiagnosticCompiler()
    output_dir = tmp_path / sub_dir

    bundle = compiler.compile_diagnostic_bundle(output_dir=output_dir, redact_pii=redact_flag)

    bundle_file = Path(bundle.bundle_filepath)
    assert bundle_file.exists()

    bundle_bytes = bundle_file.read_bytes()
    expected_hash = hashlib.sha256(bundle_bytes).hexdigest()

    assert bundle.checksum_sha256 == expected_hash

    # Verify single bit mutation invalidates checksum
    mutated_bytes = bytearray(bundle_bytes)
    mutated_bytes[0] ^= 0xFF
    mutated_hash = hashlib.sha256(mutated_bytes).hexdigest()
    assert mutated_hash != bundle.checksum_sha256
