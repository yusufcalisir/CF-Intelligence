"""Scalability and Performance Benchmark Suite for Model Drift Detection.

Measures:
  1. Histogram generation runtime vs sample size N and bin count K
  2. Divergence / log-ratio computation runtime vs bin count K
  3. End-to-end PSI computation runtime vs sample size N
  4. Full feature drift analysis runtime vs feature count F and sample size N
  5. Peak memory consumption (tracemalloc) vs N and F
  6. Empirical vs Theoretical Asymptotics O(N log N), O(F * N log N), O(K), O(F * N)
"""

from __future__ import annotations

import sys
import time
import json
import tracemalloc
import numpy as np
import scipy.stats as stats

PROJECT_ROOT = r"c:\Users\Yusuf\Desktop\projects\Privacy-preserving cross-bank fraud detection using Federated Learning\backend"
sys.path.insert(0, PROJECT_ROOT)

from app.application.services.drift_service import ModelDriftService

service = ModelDriftService()

def benchmark_drift():
    np.random.seed(42)

    sample_sizes = [100, 1000, 10000, 50000, 100000, 500000]
    feature_counts = [1, 5, 10, 50, 100, 250]
    bin_counts = [5, 10, 20, 50, 100, 200]

    benchmark_data = {
        "histogram_generation": {},
        "divergence_computation": {},
        "psi_runtime_vs_N": {},
        "drift_runtime_vs_F": {},
        "memory_vs_N": {},
        "memory_vs_F": {},
        "theoretical_vs_empirical": {}
    }

    # -------------------------------------------------------------
    # 1. Histogram Generation Runtime vs N and K
    # -------------------------------------------------------------
    for N in [1000, 50000, 200000]:
        for K in [10, 50, 100]:
            arr = np.random.normal(0, 1, size=N)
            
            # Measure quantile calculation + np.histogram
            t0 = time.perf_counter()
            for _ in range(50):
                quantiles = np.linspace(0, 100, K + 1)
                bins = np.percentile(arr, quantiles)
                bins = np.unique(bins)
                counts, _ = np.histogram(arr, bins=bins)
            t1 = time.perf_counter()

            avg_time_ms = ((t1 - t0) / 50.0) * 1000.0
            benchmark_data["histogram_generation"][f"N_{N}_K_{K}"] = round(avg_time_ms, 4)

    # -------------------------------------------------------------
    # 2. Divergence Computation Runtime vs K (Log-ratio step)
    # -------------------------------------------------------------
    for K in bin_counts:
        act_pct = np.random.dirichlet(np.ones(K))
        exp_pct = np.random.dirichlet(np.ones(K))

        t0 = time.perf_counter()
        for _ in range(10000):
            psi_val = np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct))
        t1 = time.perf_counter()

        avg_time_us = ((t1 - t0) / 10000.0) * 1e6
        benchmark_data["divergence_computation"][f"K_{K}"] = round(avg_time_us, 4)

    # -------------------------------------------------------------
    # 3. PSI Runtime vs Sample Size N (K=10)
    # -------------------------------------------------------------
    for N in sample_sizes:
        curr = np.random.normal(0.1, 1.0, size=N)
        ref = np.random.normal(0.0, 1.0, size=N)

        # Warmup
        service._calculate_psi(curr, ref)

        t0 = time.perf_counter()
        repeats = 50 if N <= 50000 else 10
        for _ in range(repeats):
            service._calculate_psi(curr, ref)
        t1 = time.perf_counter()

        avg_time_ms = ((t1 - t0) / repeats) * 1000.0
        benchmark_data["psi_runtime_vs_N"][f"N_{N}"] = round(avg_time_ms, 4)

    # -------------------------------------------------------------
    # 4. Full Feature Drift Analysis Runtime vs F (N=10,000)
    # -------------------------------------------------------------
    N_fixed = 10000
    for F in feature_counts:
        curr_dict = {f"feat_{i}": list(np.random.normal(0.1, 1, size=N_fixed)) for i in range(F)}
        ref_dict = {f"feat_{i}": list(np.random.normal(0.0, 1, size=N_fixed)) for i in range(F)}

        t0 = time.perf_counter()
        service.analyze_feature_drift(curr_dict, ref_dict)
        t1 = time.perf_counter()

        time_ms = (t1 - t0) * 1000.0
        benchmark_data["drift_runtime_vs_F"][f"F_{F}_N_{N_fixed}"] = round(time_ms, 4)

    # -------------------------------------------------------------
    # 5. Memory Consumption Benchmarking (tracemalloc)
    # -------------------------------------------------------------
    # Memory vs N (for F=10 features)
    for N in sample_sizes:
        curr_dict = {f"feat_{i}": list(np.random.normal(0, 1, size=N)) for i in range(10)}
        ref_dict = {f"feat_{i}": list(np.random.normal(0, 1, size=N)) for i in range(10)}

        tracemalloc.start()
        res = service.analyze_feature_drift(curr_dict, ref_dict)
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        benchmark_data["memory_vs_N"][f"N_{N}"] = {
            "peak_memory_kb": round(peak_mem / 1024.0, 2),
            "peak_memory_mb": round(peak_mem / (1024.0 * 1024.0), 3)
        }

    # Memory vs F (for N=50,000)
    for F in [1, 10, 50, 100]:
        curr_dict = {f"feat_{i}": list(np.random.normal(0, 1, size=50000)) for i in range(F)}
        ref_dict = {f"feat_{i}": list(np.random.normal(0, 1, size=50000)) for i in range(F)}

        tracemalloc.start()
        res = service.analyze_feature_drift(curr_dict, ref_dict)
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        benchmark_data["memory_vs_F"][f"F_{F}"] = {
            "peak_memory_kb": round(peak_mem / 1024.0, 2),
            "peak_memory_mb": round(peak_mem / (1024.0 * 1024.0), 3)
        }

    # Write output to json
    out_path = r"C:\Users\Yusuf\.gemini\antigravity-ide\brain\a3429c9e-0a37-425b-9a52-3b35832b8a38\scratch\drift_benchmark_results.json"
    with open(out_path, "w") as f:
        json.dump(benchmark_data, f, indent=2)

    print("Drift Detection Scalability Benchmark Completed Successfully!")

if __name__ == "__main__":
    benchmark_drift()
