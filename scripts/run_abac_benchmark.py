"""Reproducible ABAC Engine Throughput and Decision Latency Benchmark Suite.

Executes sustained access decision evaluations across representative banking tenant,
shift-hour, and supervisory clearance policies to measure:
- Decision throughput (requests/sec)
- Mean and p99 decision latency (ms)
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

# Add backend to sys.path
backend_path = Path(__file__).resolve().parent.parent / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from app.infrastructure.security.abac_engine import ABACEngine, ABACResource
from app.infrastructure.security.oidc_authenticator import UserClaims


def run_abac_benchmark(
    iterations: int = 50000,
    warmup: int = 5000,
) -> dict[str, float]:
    """Measures in-memory ABAC policy decision throughput and latency percentiles."""
    engine = ABACEngine()
    user = UserClaims(
        sub="usr-bench-01",
        username="investigator_alpha",
        bank_id="bank_a",
        roles=["analyst"],
        clearance_level=2,
        shift_hours="00:00-23:59",
    )
    resource = ABACResource(
        resource_type="alert",
        resource_id="alt-bench-101",
        bank_id="bank_a",
        amount=5000.0,
    )

    # 1. Warmup
    for _ in range(warmup):
        engine.evaluate_access(user, resource, action="read")

    # 2. Benchmark runs (3 rounds to ensure stability)
    latencies_us: list[float] = []
    rounds_throughput: list[float] = []

    for round_idx in range(1, 4):
        t0 = time.perf_counter()
        for _ in range(iterations):
            t_req_start = time.perf_counter()
            res = engine.evaluate_access(user, resource, action="read")
            latencies_us.append((time.perf_counter() - t_req_start) * 1e6)
        elapsed = time.perf_counter() - t0
        round_throughput = iterations / elapsed
        rounds_throughput.append(round_throughput)
        print(f"Round {round_idx}: {iterations:,} evaluations in {elapsed:.4f}s -> {round_throughput:,.0f} req/s")

    mean_throughput = statistics.mean(rounds_throughput)
    mean_latency_ms = (statistics.mean(latencies_us)) / 1000.0
    p99_latency_ms = (statistics.quantiles(latencies_us, n=100)[98]) / 1000.0

    print("=" * 60)
    print(f"ABAC Benchmark Result:")
    print(f"  Throughput:  {mean_throughput:,.0f} req/s")
    print(f"  Mean Latency: {mean_latency_ms:.4f} ms")
    print(f"  p99 Latency:  {p99_latency_ms:.4f} ms")
    print("=" * 60)

    assert mean_throughput >= 5000.0, f"ABAC throughput {mean_throughput} below target 5,000 req/s"
    return {
        "mean_throughput": mean_throughput,
        "mean_latency_ms": mean_latency_ms,
        "p99_latency_ms": p99_latency_ms,
    }


if __name__ == "__main__":
    run_abac_benchmark()
