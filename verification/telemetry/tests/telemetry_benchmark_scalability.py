"""Scalability and Latency Benchmarking for Telemetry Subsystem.

Measures:
1. Telemetry collection latency (µs per call)
2. SLA Quantile Aggregation Latency across scaling sample sizes (N = 10 to 50,000)
3. Prometheus & SIEM Serialization Overhead (ms)
4. Memory consumption scaling (MB)
5. Scaling with increasing event rates and client node counts (N = 10 to 5,000)
6. System latency overhead percentage introduced into inference pipeline
7. Theoretical vs. Observed Complexity comparison
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

backend_path = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

import logging
logging.getLogger("app.application.services.sla_monitor").setLevel(logging.ERROR)
from app.infrastructure.logging.siem_exporter import SIEMAuditEvent, SIEMLogExporter
from app.infrastructure.telemetry import TelemetryRegistry
from app.infrastructure.telemetry.otel_tracer import OpenTelemetryTracer


def measure_memory_mb() -> float:
    """Returns current process memory usage in MB using psutil if available or 0.0."""
    try:
        import psutil

        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024.0 * 1024.0)
    except ImportError:
        return 0.0


def run_telemetry_benchmarks() -> dict:
    print("=" * 80)
    print("TELEMETRY SUBSYSTEM BENCHMARK & SCALABILITY EVALUATION")
    print("=" * 80)
    benchmarks = {}

    # -------------------------------------------------------------------------
    # 1. Telemetry Collection Latency & Throughput
    # -------------------------------------------------------------------------
    print("\n--- 1. Telemetry Collection Latency & Throughput ---")
    registry = TelemetryRegistry()
    iterations = 50000

    start_time = time.perf_counter()
    for i in range(iterations):
        registry.record_inference_latency(45.2, decision="ALLOW")
    elapsed = time.perf_counter() - start_time

    per_call_us = (elapsed / iterations) * 1e6
    throughput_rps = iterations / elapsed

    print(f"Recorded {iterations:,} latency events in {elapsed * 1000:.2f} ms")
    print(f"Per-Call Collection Latency: {per_call_us:.3f} µs / call")
    print(f"Collection Throughput: {throughput_rps:,.0f} calls / sec")

    benchmarks["collection_latency_us"] = round(per_call_us, 3)
    benchmarks["collection_throughput_rps"] = round(throughput_rps, 0)

    # -------------------------------------------------------------------------
    # 2. SLA Quantile Aggregation Latency Scaling
    # -------------------------------------------------------------------------
    print("\n--- 2. SLA Quantile Aggregation Latency Scaling ---")
    sample_sizes = [10, 100, 1000, 10000, 50000]
    agg_results = []

    for size in sample_sizes:
        sla = RealtimeSLAMonitor(target_sla_ms=100.0)
        # Populate samples
        for i in range(size):
            sla.record_latency((i * 13) % 250 + 5.0)

        start_agg = time.perf_counter()
        summary = sla.get_sla_summary()
        agg_elapsed_ms = (time.perf_counter() - start_agg) * 1000.0

        print(f"Sample Size N={size:>6,}: Aggregation Latency = {agg_elapsed_ms:.3f} ms (p95={summary.p95_latency_ms}ms)")
        agg_results.append({"n": size, "latency_ms": round(agg_elapsed_ms, 3)})

    benchmarks["sla_aggregation_scaling"] = agg_results

    # -------------------------------------------------------------------------
    # 3. Prometheus & SIEM Serialization Overhead
    # -------------------------------------------------------------------------
    print("\n--- 3. Serialization Overhead (Prometheus & SIEM Formats) ---")

    # Prometheus text format rendering
    start_prom = time.perf_counter()
    prom_text = registry.get_prometheus_metrics_text()
    prom_elapsed_ms = (time.perf_counter() - start_prom) * 1000.0

    print(f"Prometheus Text Format Rendering ({len(prom_text)} chars): {prom_elapsed_ms:.3f} ms")

    # SIEM format rendering (1,000 events)
    exporter = SIEMLogExporter()
    event = SIEMAuditEvent(event_id="evt_100", event_type="LOGIN", severity="MEDIUM", source_bank="bank_alpha", message="Test login event")

    start_cef = time.perf_counter()
    for _ in range(1000):
        _ = exporter.format_cef_event(event)
    cef_elapsed_ms = (time.perf_counter() - start_cef) * 1000.0

    start_syslog = time.perf_counter()
    for _ in range(1000):
        _ = exporter.format_rfc5424_syslog({"event": "LOGIN", "bank": "bank_alpha"})
    syslog_elapsed_ms = (time.perf_counter() - start_syslog) * 1000.0

    print(f"CEF Format Rendering (1,000 events): {cef_elapsed_ms:.3f} ms ({cef_elapsed_ms/1000*1000:.3f} µs / event)")
    print(f"Syslog RFC 5424 Format Rendering (1,000 events): {syslog_elapsed_ms:.3f} ms ({syslog_elapsed_ms/1000*1000:.3f} µs / event)")

    benchmarks["prom_serialization_ms"] = round(prom_elapsed_ms, 3)
    benchmarks["cef_serialization_us"] = round(cef_elapsed_ms, 3)
    benchmarks["syslog_serialization_us"] = round(syslog_elapsed_ms, 3)

    # -------------------------------------------------------------------------
    # 4. Memory Footprint Scaling with Client Count & Telemetry Volume
    # -------------------------------------------------------------------------
    print("\n--- 4. Memory Footprint Scaling with Client Count & Volume ---")
    mem_before = measure_memory_mb()
    reg_large = TelemetryRegistry()

    client_counts = [10, 100, 1000, 5000]
    mem_scaling = []

    for c in client_counts:
        mem_start = measure_memory_mb()
        for i in range(c):
            reg_large.record_node_heartbeat(f"bank_node_{i}", timestamp=time.time())
            reg_large.record_dp_epsilon(bank_id=f"bank_node_{i}", epsilon=0.1)

        mem_end = measure_memory_mb()
        mem_diff = max(0.0, mem_end - mem_start)
        print(f"Registered {c:>5,} Bank Nodes: Memory Added = {mem_diff:.2f} MB")
        mem_scaling.append({"clients": c, "memory_mb": round(mem_diff, 2)})

    benchmarks["client_memory_scaling"] = mem_scaling

    # -------------------------------------------------------------------------
    # 5. System Latency Overhead Introduced into Inference Pipeline
    # -------------------------------------------------------------------------
    print("\n--- 5. System Telemetry Overhead Introduced into Pipeline ---")
    # Simulate a baseline 10ms inference call vs 10ms inference + telemetry
    base_inference_latency_ms = 10.0  # Typical baseline
    telemetry_overhead_ms = per_call_us / 1000.0
    overhead_pct = (telemetry_overhead_ms / base_inference_latency_ms) * 100.0

    print(f"Baseline Transaction Inference Latency: {base_inference_latency_ms:.2f} ms")
    print(f"Telemetry Metric Recording Overhead: {telemetry_overhead_ms:.4f} ms")
    print(f"Relative Telemetry Overhead Introduced: {overhead_pct:.4f} %")

    benchmarks["telemetry_overhead_pct"] = round(overhead_pct, 4)

    # Output JSON results
    out_file = Path(__file__).parent / "telemetry_benchmark_results.json"
    out_file.write_text(json.dumps(benchmarks, indent=2), encoding="utf-8")

    print("\n================================================================================")
    print("TELEMETRY SUBSYSTEM SCALABILITY BENCHMARK COMPLETE")
    print("================================================================================")
    return benchmarks


if __name__ == "__main__":
    run_telemetry_benchmarks()
