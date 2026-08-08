"""
Mathematical Formula Scalability & Latency Benchmark Engine
============================================================
Benchmarks execution throughput, numerical precision, and latency scaling
for core platform mathematical equations across parameter dimensions d in [100, 1000000].
"""

import time
import numpy as np


def benchmark_mathematical_formulas():
    results = []
    
    # 1. FedAvg Vector Aggregation Benchmark (d = 100, 10000, 1000000)
    for d in [100, 10000, 1000000]:
        w1 = np.random.randn(d)
        w2 = np.random.randn(d)
        w3 = np.random.randn(d)
        
        t0 = time.perf_counter()
        _ = 0.4 * w1 + 0.3 * w2 + 0.3 * w3
        elapsed = (time.perf_counter() - t0) * 1000.0  # ms
        
        throughput = d / (elapsed / 1000.0) if elapsed > 0 else 0
        results.append({
            "formula": "FedAvg Weighted Sum",
            "dimension_d": d,
            "latency_ms": round(elapsed, 4),
            "throughput_param_per_sec": int(throughput),
            "complexity": "O(d)"
        })

    # 2. L2 Vector Normalization Benchmark
    for d in [100, 10000, 1000000]:
        h = np.random.randn(d)
        
        t0 = time.perf_counter()
        _ = h / np.linalg.norm(h)
        elapsed = (time.perf_counter() - t0) * 1000.0
        
        throughput = d / (elapsed / 1000.0) if elapsed > 0 else 0
        results.append({
            "formula": "Unit-Sphere L2 Norm",
            "dimension_d": d,
            "latency_ms": round(elapsed, 4),
            "throughput_param_per_sec": int(throughput),
            "complexity": "O(d)"
        })
        
    return results


if __name__ == "__main__":
    res = benchmark_mathematical_formulas()
    print("=== Mathematical Formula Benchmark Results ===")
    for r in res:
        print(f"[{r['formula']}] d={r['dimension_d']:,} -> {r['latency_ms']} ms ({r['throughput_param_per_sec']:,} param/sec) [{r['complexity']}]")
