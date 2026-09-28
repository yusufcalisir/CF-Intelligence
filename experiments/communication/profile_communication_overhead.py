"""Federated Communication Cost & Bandwidth Profiling Runner.

Quantifies:
1. Exact serialized wire payload size across data encodings (JSON, FP32, FP16, INT8, Top-K COO, Zstd)
2. Multi-round network consumption across federated aggregation protocols (FedAvg, FedProx, SCAFFOLD, SecAgg, CKKS)
3. Bandwidth scaling with parameter dimension d, client count K, and round count R
4. Serialization/deserialization latency and reconstruction fidelity (MAE, PSNR)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

import numpy as np

from app.infrastructure.security.compression_engine import (
    BandwidthBenchmarkSuiteResult,
    FederatedCommunicationProfiler,
    FederatedProtocol,
    ProtocolCommunicationProfile,
    SerializationFormat,
    SerializedPayloadProfile,
)

logger = logging.getLogger(__name__)


def generate_benchmark_weights(num_parameters: int = 1969, seed: int = 42) -> list[float]:
    """Generates synthetic weight tensor reflecting real neural network gradient distributions."""
    rng = np.random.default_rng(seed)
    # Mixture of Gaussian core weights and heavy-tailed sparse activations
    core = rng.normal(loc=0.0, scale=0.05, size=int(num_parameters * 0.90))
    sparse_peaks = rng.laplace(loc=0.0, scale=0.25, size=num_parameters - len(core))
    combined = np.concatenate([core, sparse_peaks])
    rng.shuffle(combined)
    return [float(x) for x in combined]


def format_communication_benchmark_markdown(result: BandwidthBenchmarkSuiteResult) -> str:
    """Formats benchmark results into an empirical Markdown table."""
    lines: list[str] = [
        f"### Federated Communication Cost & Bandwidth Profiling Report: `{result.model_name}`",
        f"- **Model Parameter Count ($d$)**: {result.num_parameters:,} parameters",
        f"- **Evaluation Timestamp**: `{result.timestamp}`",
        "",
        "#### 1. Wire Payload Size & Serialization Efficiency ($d = 1{,}969$ parameters)",
        "",
        r"| Serialization Format | Wire Size (Bytes) | vs FP32 Ratio | Bandwidth Savings | Ser Latency ($\mu s$) | Deser Latency ($\mu s$) | Recon MAE | Recon Max Error |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for fmt in SerializationFormat:
        p: SerializedPayloadProfile = result.serialization_profiles[fmt.value]
        lines.append(
            f"| `{p.format.value}` | **{p.wire_bytes:,} B** | {p.compression_ratio_vs_fp32:.4f} | "
            f"**{p.bandwidth_savings_pct:+.1f}%** | {p.serialization_time_us:.1f} | "
            f"{p.deserialization_time_us:.1f} | {p.reconstruction_mae:.6f} | {p.reconstruction_max_err:.6f} |"
        )

    lines.extend([
        "",
        "#### 2. Federated Protocol Multi-Round Communication Overhead ($K=3$ banks, $R=5$ rounds)",
        "",
        "| Protocol | Downstream / Round | Upstream (Total K) | 1-Round Wire | 5-Round Total | 5-Round Volume (MB) | Relative Overhead | Architectural Evaluation |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])

    for proto in FederatedProtocol:
        cp: ProtocolCommunicationProfile = result.protocol_profiles[proto.value]
        lines.append(
            f"| `{cp.protocol.value}` | {cp.bytes_server_downstream_per_round:,} B | "
            f"{cp.bytes_server_upstream_per_round:,} B | {cp.total_round_bytes:,} B | "
            f"{cp.total_multi_round_bytes:,} B | **{cp.total_multi_round_mb:.4f} MB** | "
            f"**{cp.relative_overhead_vs_fedavg:.2f}\\times** | {cp.bandwidth_efficiency_notes} |"
        )

    return "\n".join(lines)


def run_communication_benchmark(
    num_parameters: int = 1969,
    num_clients: int = 3,
    num_rounds: int = 5,
    output_dir: str | Path | None = None,
) -> BandwidthBenchmarkSuiteResult:
    """Runs end-to-end communication benchmarking and writes JSON results."""
    weights = generate_benchmark_weights(num_parameters=num_parameters, seed=42)

    profiler = FederatedCommunicationProfiler()
    suite_result = profiler.run_full_bandwidth_benchmark(
        weights=weights,
        num_clients=num_clients,
        num_rounds=num_rounds,
        model_name=f"FraudDetectionMLP_{num_parameters}params",
    )

    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        json_file = out_path / "federated_communication_profiles.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(suite_result.model_dump(), f, indent=2)
        logger.info("Serialized communication benchmark results to %s", json_file)

    return suite_result


if __name__ == "__main__":
    reconfigure_fn = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure_fn):
        reconfigure_fn(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Federated Communication Cost & Bandwidth Profiling")
    parser.add_argument("--params", type=int, default=1969, help="Number of neural network parameters")
    parser.add_argument("--clients", type=int, default=3, help="Number of participating client banks")
    parser.add_argument("--rounds", type=int, default=5, help="Number of federation rounds")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="benchmarks/results/raw",
        help="Directory to save raw JSON output",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    res = run_communication_benchmark(
        num_parameters=args.params,
        num_clients=args.clients,
        num_rounds=args.rounds,
        output_dir=args.output_dir,
    )
    md_report = format_communication_benchmark_markdown(res)
    print("\n" + md_report)
