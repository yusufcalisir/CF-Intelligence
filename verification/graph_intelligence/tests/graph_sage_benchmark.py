# -*- coding: utf-8 -*-
"""GraphSAGE / FedGNN Comprehensive Benchmark.

Measures:
  1. Graph construction time (N nodes, E edges)
  2. Single forward pass (embedding generation) latency
  3. Per-layer message passing runtime
  4. Federated aggregation runtime
  5. Memory usage via tracemalloc
  6. Scalability:
       a. Increasing N (nodes)
       b. Increasing E (edges / density)
       c. Increasing input feature dimension
       d. Increasing embedding dimension
       e. Increasing K (federated clients)

All timings use time.perf_counter() for sub-millisecond resolution.
Memory is measured with tracemalloc peak snapshot.

Theoretical complexity reference:
  - Forward pass (1 layer):  O(N * d_in * d_out + E * d_in)
  - L-layer GraphSAGE:       O(L * N * d_in * d_out + L * E * d_in)
  - FedAvg aggregation:      O(K * P)  where K=clients, P=parameters
  - Graph construction:      O(N + E)
"""

from __future__ import annotations

import gc
import sys
import time
import tracemalloc
from typing import Any

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import torch

backend_path = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(backend_path))

from app.application.services.fl_engine import AggregationMethod, FederatedLearningEngine  # noqa: E402
from app.application.services.graph_embedding_model import (  # noqa: E402
    NODE_FEATURE_DIM,
    GraphSAGELayer,
    GraphSAGEModel,
)
from app.config import get_settings  # noqa: E402
from app.domain.value_objects import ModelWeights  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Benchmark helpers
# ─────────────────────────────────────────────────────────────────────────────

REPEATS = 5  # number of timed repetitions (best-of)


def time_fn(fn, *args, repeats: int = REPEATS, **kwargs) -> dict[str, float]:
    """Time a callable, return stats in milliseconds."""
    times = []
    for _ in range(repeats):
        gc.collect()
        t0 = time.perf_counter()
        fn(*args, **kwargs)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000.0)
    return {
        "mean_ms": float(np.mean(times)),
        "min_ms": float(np.min(times)),
        "max_ms": float(np.max(times)),
        "std_ms": float(np.std(times)),
    }


def peak_memory_bytes(fn, *args, **kwargs) -> int:
    """Measure peak memory allocated during fn() using tracemalloc."""
    gc.collect()
    tracemalloc.start()
    fn(*args, **kwargs)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


def make_random_graph(
    N: int,
    avg_degree: int = 5,
    feature_dim: int = NODE_FEATURE_DIM,
    seed: int = 42,
) -> tuple[torch.Tensor, list[list[int]]]:
    """Generate a random N-node graph with given average degree."""
    rng = np.random.default_rng(seed)
    features = torch.tensor(rng.random((N, feature_dim), dtype=np.float32))
    adj: list[list[int]] = [[] for _ in range(N)]
    for i in range(N):
        k = max(0, int(rng.poisson(avg_degree)))
        if k == 0 or N <= 1:
            continue
        pool = [j for j in range(N) if j != i]
        neighbors = rng.choice(pool, size=min(k, len(pool)), replace=False).tolist()
        adj[i] = neighbors
    return features, adj


def make_model_weights(model: GraphSAGEModel) -> ModelWeights:
    return model.to_model_weights()


def build_fl_engine() -> FederatedLearningEngine:
    return FederatedLearningEngine(
        settings=get_settings(),
        model_service=MagicMock(),
        privacy_service=MagicMock(),
    )


def hr(title: str, width: int = 88) -> None:
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def row(
    label: str, mean_ms: float, min_ms: float, std_ms: float, note: str = ""
) -> None:
    s = f"  {label:<40}  mean={mean_ms:8.3f}ms  min={min_ms:7.3f}ms  std={std_ms:6.3f}ms"
    if note:
        s += f"  [{note}]"
    print(s)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Graph construction time
# ─────────────────────────────────────────────────────────────────────────────


def bench_graph_construction():
    hr("BENCHMARK 1 - Graph Construction Time  (scaling with N, avg_degree=5)")
    print(
        f"  {'N nodes':<12}  {'mean ms':>10}  {'min ms':>9}  {'std ms':>8}  {'nodes/ms':>10}"
    )
    print(f"  {'-' * 12}  {'-' * 10}  {'-' * 9}  {'-' * 8}  {'-' * 10}")
    results = {}
    for N in [100, 500, 1_000, 2_500, 5_000, 10_000]:
        t = time_fn(make_random_graph, N, 5)
        throughput = N / t["mean_ms"]
        print(
            f"  {N:<12,d}  {t['mean_ms']:>10.3f}  {t['min_ms']:>9.3f}  {t['std_ms']:>8.3f}  {throughput:>10.1f}"
        )
        results[N] = t
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 2. Embedding generation latency (full forward pass)
# ─────────────────────────────────────────────────────────────────────────────


def bench_embedding_latency():
    hr("BENCHMARK 2 - Embedding Generation Latency  (full forward pass, eval mode)")
    model = GraphSAGEModel(
        input_dim=NODE_FEATURE_DIM, hidden_dim=128, embedding_dim=64, num_layers=2
    )
    model.eval()
    print(
        f"  {'N nodes':<12}  {'mean ms':>10}  {'min ms':>9}  {'std ms':>8}  {'nodes/ms':>10}"
    )
    print(f"  {'-' * 12}  {'-' * 10}  {'-' * 9}  {'-' * 8}  {'-' * 10}")
    results = {}
    for N in [100, 500, 1_000, 2_500, 5_000, 10_000]:
        features, adj = make_random_graph(N)

        def fwd():
            with torch.no_grad():
                model.get_embeddings(features, adj)

        t = time_fn(fwd)
        throughput = N / t["mean_ms"]
        print(
            f"  {N:<12,d}  {t['mean_ms']:>10.3f}  {t['min_ms']:>9.3f}  {t['std_ms']:>8.3f}  {throughput:>10.1f}"
        )
        results[N] = t
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 3. Per-layer message passing runtime
# ─────────────────────────────────────────────────────────────────────────────


def bench_message_passing():
    hr("BENCHMARK 3 - Per-Layer Message Passing Runtime  (GraphSAGELayer forward)")
    print(
        f"\n  {'N':>7}  {'avg_deg':>8}  {'mean ms':>10}  {'min ms':>9}  {'E (edges)':>12}  {'ms/edge (us)':>14}"
    )
    print(f"  {'-' * 7}  {'-' * 8}  {'-' * 10}  {'-' * 9}  {'-' * 12}  {'-' * 14}")
    results = {}
    layer = GraphSAGELayer(in_dim=NODE_FEATURE_DIM, out_dim=128)
    for N in [500, 1_000, 2_500, 5_000]:
        for avg_deg in [2, 5, 10, 20]:
            features, adj = make_random_graph(N, avg_degree=avg_deg)
            E = sum(len(a) for a in adj)  # total directed edges

            def fwd():
                with torch.no_grad():
                    layer(features, adj)

            t = time_fn(fwd)
            us_per_edge = (t["mean_ms"] * 1000.0) / max(E, 1)
            print(
                f"  {N:>7,d}  {avg_deg:>8d}  {t['mean_ms']:>10.3f}  {t['min_ms']:>9.3f}  {E:>12,d}  {us_per_edge:>14.4f}"
            )
            results[(N, avg_deg)] = {"timing": t, "edges": E}
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 4. Federated aggregation runtime (FedAvg)
# ─────────────────────────────────────────────────────────────────────────────


def bench_federated_aggregation():
    hr("BENCHMARK 4 - Federated Aggregation Runtime  (FedAvg, scaling K clients)")
    fl_engine = build_fl_engine()
    base_model = GraphSAGEModel(
        input_dim=NODE_FEATURE_DIM, hidden_dim=128, embedding_dim=64, num_layers=2
    )
    base_weights = make_model_weights(base_model)
    P = base_weights.num_parameters
    print(f"  Model parameters: {P:,d}")
    print(
        f"\n  {'K clients':<12}  {'mean ms':>10}  {'min ms':>9}  {'std ms':>8}  {'P * K ops':>14}"
    )
    print(f"  {'-' * 12}  {'-' * 10}  {'-' * 9}  {'-' * 8}  {'-' * 14}")
    results = {}
    for K in [2, 4, 8, 16, 32, 64]:
        rng = np.random.default_rng(42)
        client_weights = []
        for _ in range(K):
            noised = [w + rng.normal(0, 0.01) for w in base_weights.flat_weights]
            client_weights.append(
                ModelWeights(
                    layer_shapes=base_weights.layer_shapes, flat_weights=noised
                )
            )
        samples = [100] * K

        def agg():
            fl_engine.aggregate_graph_parameters(
                client_weights, samples, method=AggregationMethod.FED_AVG_WEIGHTED
            )

        t = time_fn(agg)
        print(
            f"  {K:<12,d}  {t['mean_ms']:>10.3f}  {t['min_ms']:>9.3f}  {t['std_ms']:>8.3f}  {P * K:>14,d}"
        )
        results[K] = t
    return results, P


# ─────────────────────────────────────────────────────────────────────────────
# 5. Memory usage (forward pass)
# ─────────────────────────────────────────────────────────────────────────────


def bench_memory_usage():
    hr("BENCHMARK 5 - Memory Usage  (peak during forward pass)")
    model = GraphSAGEModel(
        input_dim=NODE_FEATURE_DIM, hidden_dim=128, embedding_dim=64, num_layers=2
    )
    model.eval()
    print(f"  {'N nodes':<12}  {'peak KB':>10}  {'MB/node (bytes)':>18}")
    print(f"  {'-' * 12}  {'-' * 10}  {'-' * 18}")
    results = {}
    for N in [100, 500, 1_000, 2_500, 5_000, 10_000]:
        features, adj = make_random_graph(N)

        def fwd():
            with torch.no_grad():
                model.get_embeddings(features, adj)

        peak = peak_memory_bytes(fwd)
        peak_kb = peak / 1024.0
        bytes_per_node = peak / N
        print(f"  {N:<12,d}  {peak_kb:>10.1f}  {bytes_per_node:>18.1f}")
        results[N] = {"peak_bytes": peak, "peak_kb": peak_kb}
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 6a. Scalability: feature dimension
# ─────────────────────────────────────────────────────────────────────────────


def bench_feature_dim_scaling():
    hr("BENCHMARK 6a - Feature Dimension Scaling  (N=1000, fixed)")
    N = 1_000
    print(f"  {'d_in':<10}  {'mean ms':>10}  {'min ms':>9}  {'std ms':>8}")
    print(f"  {'-' * 10}  {'-' * 10}  {'-' * 9}  {'-' * 8}")
    results = {}
    for d_in in [8, 12, 32, 64, 128, 256]:
        features, adj = make_random_graph(N, feature_dim=d_in)
        model = GraphSAGEModel(
            input_dim=d_in, hidden_dim=128, embedding_dim=64, num_layers=2
        )
        model.eval()

        def fwd():
            with torch.no_grad():
                model.get_embeddings(features, adj)

        t = time_fn(fwd)
        print(
            f"  {d_in:<10d}  {t['mean_ms']:>10.3f}  {t['min_ms']:>9.3f}  {t['std_ms']:>8.3f}"
        )
        results[d_in] = t
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 6b. Scalability: embedding dimension
# ─────────────────────────────────────────────────────────────────────────────


def bench_embedding_dim_scaling():
    hr("BENCHMARK 6b - Embedding Dimension Scaling  (N=1000, d_in=12 fixed)")
    N = 1_000
    features, adj = make_random_graph(N)
    print(
        f"  {'d_emb':<10}  {'mean ms':>10}  {'min ms':>9}  {'std ms':>8}  {'params':>10}"
    )
    print(f"  {'-' * 10}  {'-' * 10}  {'-' * 9}  {'-' * 8}  {'-' * 10}")
    results = {}
    for d_emb in [16, 32, 64, 128, 256, 512]:
        model = GraphSAGEModel(
            input_dim=NODE_FEATURE_DIM,
            hidden_dim=128,
            embedding_dim=d_emb,
            num_layers=2,
        )
        model.eval()
        P = sum(p.numel() for p in model.parameters())

        def fwd():
            with torch.no_grad():
                model.get_embeddings(features, adj)

        t = time_fn(fwd)
        print(
            f"  {d_emb:<10d}  {t['mean_ms']:>10.3f}  {t['min_ms']:>9.3f}  {t['std_ms']:>8.3f}  {P:>10,d}"
        )
        results[d_emb] = {"timing": t, "params": P}
    return results


# -----------------------------------------------------------------------------
# 6c. Scalability: edge density
# -----------------------------------------------------------------------------


def bench_edge_density_scaling():
    hr("BENCHMARK 6c - Edge Density Scaling  (N=2000 fixed)")
    N = 2_000
    model = GraphSAGEModel(
        input_dim=NODE_FEATURE_DIM, hidden_dim=128, embedding_dim=64, num_layers=2
    )
    model.eval()
    print(
        f"  {'avg_degree':<12}  {'E (edges)':>12}  {'mean ms':>10}  {'min ms':>9}  {'std ms':>8}  {'ms/Kedge':>10}"
    )
    print(f"  {'-' * 12}  {'-' * 12}  {'-' * 10}  {'-' * 9}  {'-' * 8}  {'-' * 10}")
    results = {}
    for avg_deg in [1, 2, 5, 10, 20, 50]:
        features, adj = make_random_graph(N, avg_degree=avg_deg)
        E = sum(len(a) for a in adj)

        def fwd():
            with torch.no_grad():
                model.get_embeddings(features, adj)

        t = time_fn(fwd)
        ms_per_Ke = t["mean_ms"] / max(E / 1000.0, 0.001)
        print(
            f"  {avg_deg:<12d}  {E:>12,d}  {t['mean_ms']:>10.3f}  {t['min_ms']:>9.3f}  {t['std_ms']:>8.3f}  {ms_per_Ke:>10.3f}"
        )
        results[avg_deg] = {"timing": t, "E": E}
    return results


# -----------------------------------------------------------------------------
# Complexity regression helper
# -----------------------------------------------------------------------------


def fit_power_law(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Fit y = a * x^b via log-linear regression. Returns (a, b)."""
    log_x = np.log(np.array(xs, dtype=float))
    log_y = np.log(np.array(ys, dtype=float))
    b, log_a = np.polyfit(log_x, log_y, 1)
    return float(np.exp(log_a)), float(b)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main():
    print("=" * 88)
    print("  FEDERATED GRAPHSAGE (FedGNN): COMPREHENSIVE BENCHMARK")
    print("=" * 88)

    results: dict[str, Any] = {}

    # 1 - Graph construction
    results["construction"] = bench_graph_construction()

    # 2 - Embedding latency
    results["embedding"] = bench_embedding_latency()

    # 3 - Message passing
    results["message_passing"] = bench_message_passing()

    # 4 - Federated aggregation
    results["aggregation"], P = bench_federated_aggregation()

    # 5 - Memory
    results["memory"] = bench_memory_usage()

    # 6a - Feature dim
    results["feature_dim"] = bench_feature_dim_scaling()

    # 6b - Embedding dim
    results["embedding_dim"] = bench_embedding_dim_scaling()

    # 6c - Edge density
    results["edge_density"] = bench_edge_density_scaling()

    # ─────────────────────────────────────────────────────────────────────────
    # Complexity analysis
    # ─────────────────────────────────────────────────────────────────────────

    hr("COMPLEXITY ANALYSIS - Observed vs Theoretical")

    # Node scaling: embedding latency
    emb_data = results["embedding"]
    Ns = sorted(emb_data.keys())
    emb_times = [emb_data[N]["mean_ms"] for N in Ns]
    a_emb, b_emb = fit_power_law(Ns, emb_times)
    print("\n  Embedding latency vs N (nodes):")
    print(f"    Observed power law: T ~ {a_emb:.4e} * N^{b_emb:.3f}")
    print("    Theoretical:        T = O(N)  ->  exponent ~ 1.0")
    print(
        f"    Verdict:            exponent={b_emb:.3f} - {'LINEAR [OK]' if 0.85 <= b_emb <= 1.15 else 'SUPER-LINEAR [!]'}"
    )

    # Client scaling: aggregation
    agg_data = results["aggregation"]
    Ks = sorted(agg_data.keys())
    agg_times = [agg_data[K]["mean_ms"] for K in Ks]
    a_agg, b_agg = fit_power_law(Ks, agg_times)
    print("\n  FedAvg aggregation vs K (clients):")
    print(f"    Observed power law: T ~ {a_agg:.4e} * K^{b_agg:.3f}")
    print("    Theoretical:        T = O(K * P)  ->  exponent ~ 1.0")
    print(
        f"    Verdict:            exponent={b_agg:.3f} - {'LINEAR [OK]' if 0.85 <= b_agg <= 1.15 else 'SUPER-LINEAR [!]'}"
    )

    # Feature dim scaling
    fd_data = results["feature_dim"]
    d_ins = sorted(fd_data.keys())
    fd_times = [fd_data[d]["mean_ms"] for d in d_ins]
    a_fd, b_fd = fit_power_law(d_ins, fd_times)
    print("\n  Forward pass vs d_in (feature dimension):")
    print(f"    Observed power law: T ~ {a_fd:.4e} * d_in^{b_fd:.3f}")
    print("    Theoretical:        T = O(d_in * d_out)  ->  exponent ~ 1.0")
    print(
        f"    Verdict:            exponent={b_fd:.3f} - {'LINEAR [OK]' if 0.85 <= b_fd <= 1.15 else 'SUPER-LINEAR [!]'}"
    )

    # Edge density scaling
    ed_data = results["edge_density"]
    degs = sorted(ed_data.keys())
    ed_times = [ed_data[d]["timing"]["mean_ms"] for d in degs]
    a_ed, b_ed = fit_power_law(degs, ed_times)
    print("\n  Forward pass vs avg_degree (edge density, N fixed):")
    print(f"    Observed power law: T ~ {a_ed:.4e} * degree^{b_ed:.3f}")
    print("    Theoretical:        T = O(E)  ->  exponent ~ 1.0 in degree")
    print(
        "    NOTE: num_sample=10 caps neighborhood; sub-linear expected for high degree"
    )
    print(f"    Verdict:            exponent={b_ed:.3f}")

    # Memory scaling
    mem_data = results["memory"]
    Ns_m = sorted(mem_data.keys())
    mem_vals = [mem_data[N]["peak_bytes"] for N in Ns_m]
    a_mem, b_mem = fit_power_law(Ns_m, mem_vals)
    print("\n  Peak memory vs N (nodes):")
    print(f"    Observed power law: M ~ {a_mem:.4e} * N^{b_mem:.3f}")
    print("    Theoretical:        M = O(N * d)  ->  exponent ~ 1.0")
    print(
        f"    Verdict:            exponent={b_mem:.3f} - {'LINEAR [OK]' if 0.85 <= b_mem <= 1.15 else 'SUPER-LINEAR [!]'}"
    )

    print("\n" + "=" * 88)
    print("  BENCHMARK COMPLETE")
    print("=" * 88)

    return results


if __name__ == "__main__":
    main()
