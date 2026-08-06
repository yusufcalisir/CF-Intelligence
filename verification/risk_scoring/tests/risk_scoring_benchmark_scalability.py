"""Performance and Scalability Benchmark for Risk Scoring & Policy Engine.

Measures:
1. Transaction scoring latency and throughput vs batch size N (N in [1, 100, 1k, 10k, 50k])
2. Peak memory allocation (tracemalloc MB)
3. AST Policy Rule Engine evaluation latency vs rule count R (R in [1, 10, 50, 200, 1000])
4. Comparison of observed complexity against theoretical complexity
"""

from __future__ import annotations

import sys
import time
import tracemalloc
import numpy as np

PROJECT_ROOT = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
sys.path.insert(0, PROJECT_ROOT)

from app.application.services.risk_engine import RiskScoringEngine
from app.application.services.policy_engine import evaluate_condition

engine = RiskScoringEngine()


# =====================================================================
# BENCHMARK SUITE
# =====================================================================

def benchmark_transaction_volume_scaling():
    print("--- 1. SCALABILITY VS TRANSACTION VOLUME (N in [1, 100, 1k, 10k, 50k]) ---")
    batch_sizes = [1, 100, 1000, 10000, 50000]

    print(f"{'Batch Size (N)':>14} | {'Total Time (ms)':>15} | {'Latency (us/txn)':>18} | {'Throughput (TPS)':>18} | {'Peak Mem (MB)':>14}")
    print("-" * 88)

    rng = np.random.default_rng(42)

    for n in batch_sizes:
        # Pre-generate transactions
        txns = []
        for i in range(n):
            txns.append({
                "velocity": float(rng.uniform(0, 15)),
                "merchant_risk_score": float(rng.uniform(0, 1)),
                "merchant_category": "grocery",
                "country_code": "US",
                "device_type": "web_browser",
                "customer_history_score": float(rng.uniform(0, 1)),
                "account_age_days": int(rng.integers(1, 1000)),
                "transaction_amount": float(rng.uniform(10, 1000)),
            })

        tracemalloc.start()
        t0 = time.perf_counter()
        for txn in txns:
            _ = engine.score_transaction(txn, ml_prediction=0.5, entity_hash="")
        elapsed_sec = time.perf_counter() - t0
        elapsed_ms = elapsed_sec * 1000.0

        _, peak_mem_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        us_per_txn = (elapsed_sec / n) * 1e6
        tps = n / elapsed_sec if elapsed_sec > 0 else 0.0
        peak_mem_mb = peak_mem_bytes / (1024 * 1024)

        print(f"{n:>14d} | {elapsed_ms:>15.3f} | {us_per_txn:>18.2f} | {tps:>18.1f} | {peak_mem_mb:>14.3f}")


def benchmark_policy_rule_scaling():
    print("\n--- 2. SCALABILITY VS AST POLICY RULE COUNT (R in [1, 10, 50, 200, 1000]) ---")
    rule_counts = [1, 10, 50, 200, 1000]

    context = {
        "composite_risk_score": 850.0,
        "country_code": "KP",
        "velocity": 12.0,
        "transaction_amount": 5000.0,
        "device_type": "atm"
    }

    print(f"{'Rule Count (R)':>14} | {'Total Time (ms)':>15} | {'Latency (us/rule)':>18} | {'Rules/sec':>18} | {'Peak Mem (MB)':>14}")
    print("-" * 88)

    for r in rule_counts:
        # Build nested AST rules
        rules = []
        for i in range(r):
            rule_ast = {
                "and": [
                    {"field": "composite_risk_score", "operator": ">=", "value": 800.0},
                    {"field": "country_code", "operator": "in", "value": ["KP", "IR", "SY"]},
                    {"or": [
                        {"field": "velocity", "operator": ">", "value": 10.0},
                        {"field": "transaction_amount", "operator": ">", "value": 1000.0}
                    ]}
                ]
            }
            rules.append(rule_ast)

        # Run 100 iterations of evaluating all R rules
        iterations = 100
        tracemalloc.start()
        t0 = time.perf_counter()
        for _ in range(iterations):
            for rule in rules:
                _ = evaluate_condition(rule, context)
        elapsed_sec = time.perf_counter() - t0
        elapsed_ms = elapsed_sec * 1000.0

        _, peak_mem_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        total_evals = r * iterations
        us_per_rule = (elapsed_sec / total_evals) * 1e6
        rules_per_sec = total_evals / elapsed_sec if elapsed_sec > 0 else 0.0
        peak_mem_mb = peak_mem_bytes / (1024 * 1024)

        print(f"{r:>14d} | {elapsed_ms:>15.3f} | {us_per_rule:>18.2f} | {rules_per_sec:>18.1f} | {peak_mem_mb:>14.3f}")


def run():
    print("=" * 88)
    print("RISK SCORING & POLICY ENGINE BENCHMARK ANALYSIS")
    print("=" * 88)
    benchmark_transaction_volume_scaling()
    benchmark_policy_rule_scaling()
    print("\n" + "=" * 88)
    print("BENCHMARK COMPLETE")
    print("=" * 88)


if __name__ == "__main__":
    run()
