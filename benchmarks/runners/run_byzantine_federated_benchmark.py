"""CLI Entry Point for the Canonical Byzantine Federated Robustness Benchmark.

Usage:
  # 1. Print execution manifest and configuration hash:
  python benchmarks/runners/run_byzantine_federated_benchmark.py --manifest

  # 2. Run fast validation smoke test (does not touch canonical artifacts):
  python benchmarks/runners/run_byzantine_federated_benchmark.py --smoke

  # 3. Canonical execution (Phase 3 only; requires clean git status and all 3 seeds):
  python benchmarks/runners/run_byzantine_federated_benchmark.py --canonical
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root and backend to sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from benchmarks.byzantine.config import (
    CANONICAL_SEEDS,
    AggregatorConfig,
    AttackConfig,
    ByzantineAggregatorType,
    ByzantineAttackType,
    ByzantineBenchmarkConfig,
    DatasetConfig,
    FederationConfig,
    TrainingConfig,
    generate_execution_manifest,
)
from benchmarks.byzantine.runner import ByzantineFederatedRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("run_byzantine_federated_benchmark")


def build_default_config(
    is_smoke: bool = False,
    is_canonical: bool = False,
    rounds: int | None = None,
    clients: int = 12,
    byzantine: int = 2,
    seeds: list[int] | None = None,
) -> ByzantineBenchmarkConfig:
    """Constructs benchmark configuration matching requested execution profile."""
    n_seeds = seeds or ([42] if is_smoke else CANONICAL_SEEDS)
    fl_rounds = rounds or (1 if is_smoke else 10)
    use_mock = is_smoke

    attacks = [
        AttackConfig(attack_type=ByzantineAttackType.SIGN_FLIP, scale=3.0),
        AttackConfig(attack_type=ByzantineAttackType.GAUSSIAN_NOISE, noise_std=1.0),
        AttackConfig(attack_type=ByzantineAttackType.ALIE, alie_z_max=1.0),
    ]

    aggregators = [
        AggregatorConfig(aggregator_type=ByzantineAggregatorType.FEDAVG),
        AggregatorConfig(aggregator_type=ByzantineAggregatorType.COORDINATE_MEDIAN),
        AggregatorConfig(aggregator_type=ByzantineAggregatorType.TRIMMED_MEAN, trimmed_mean_beta=0.20),
        AggregatorConfig(aggregator_type=ByzantineAggregatorType.KRUM, krum_f=byzantine),
        AggregatorConfig(aggregator_type=ByzantineAggregatorType.MULTI_KRUM, krum_f=byzantine, multi_krum_m=clients - byzantine),
        AggregatorConfig(aggregator_type=ByzantineAggregatorType.BULYAN, bulyan_f=byzantine),
    ]

    return ByzantineBenchmarkConfig(
        benchmark_id="byzantine_federated_canonical",
        schema_version="2.0.0",
        seeds=n_seeds,
        dataset=DatasetConfig(
            dataset_name="credit_card",
            use_mock=use_mock,
            n_mock_samples=1000 if is_smoke else 2000,
        ),
        federation=FederationConfig(
            n_clients=clients,
            f_byzantine=byzantine,
            min_samples_per_client=20 if is_smoke else 50,
        ),
        training=TrainingConfig(
            rounds=fl_rounds,
            local_epochs=1,
            batch_size=64,
            learning_rate=0.005,
        ),
        attacks=attacks,
        aggregators=aggregators,
        is_canonical=is_canonical,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Byzantine Federated Robustness Benchmark Runner")
    parser.add_argument("--manifest", action="store_true", help="Print execution manifest and exit.")
    parser.add_argument("--smoke", action="store_true", help="Run lightweight verification smoke test.")
    parser.add_argument("--canonical", action="store_true", help="Execute full canonical benchmark (Phase 3 only).")
    parser.add_argument("--rounds", type=int, default=None, help="Number of FL communication rounds.")
    parser.add_argument("--clients", type=int, default=12, help="Consortium client count (n).")
    parser.add_argument("--byzantine", type=int, default=2, help="Malicious client count (f).")
    parser.add_argument("--output", type=str, default=None, help="Custom output JSON path.")

    args = parser.parse_args()

    config = build_default_config(
        is_smoke=args.smoke,
        is_canonical=args.canonical,
        rounds=args.rounds,
        clients=args.clients,
        byzantine=args.byzantine,
    )

    if args.manifest:
        manifest = generate_execution_manifest(config, repo_root=REPO_ROOT)
        print("\n" + "=" * 80)
        print("  BYZANTINE BENCHMARK EXECUTION MANIFEST")
        print("=" * 80)
        print(json.dumps(manifest, indent=2))
        return

    runner = ByzantineFederatedRunner(config=config, repo_root=REPO_ROOT)

    if args.smoke:
        print("\n[+] RUNNING NON-CANONICAL VERIFICATION SMOKE TEST...")
        artifact = runner.run_full_suite(is_canonical=False, is_smoke=True)
        out_path = Path(args.output) if args.output else REPO_ROOT / "experiments" / "byzantine" / "smoke" / "byzantine_smoke_test.json"
        runner.save_artifact_atomically(artifact, out_path, is_canonical=False)
        print(f"\n[+] Smoke test completed successfully. Artifact saved to: {out_path}")
        print(f"    - Status: {artifact.status}")
        print(f"    - Conditions evaluated: {len(artifact.per_seed_results)}")
        return

    if args.canonical:
        print("\n[+] RUNNING CANONICAL MULTI-SEED BYZANTINE BENCHMARK...")
        artifact = runner.run_full_suite(is_canonical=True, is_smoke=False)
        out_path = Path(args.output) if args.output else REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_federated_canonical.json"
        runner.save_artifact_atomically(artifact, out_path, is_canonical=True)
        print(f"\n[+] Canonical benchmark completed successfully. Artifact saved to: {out_path}")
        return

    print("No action specified. Run with --manifest, --smoke, or --canonical.")


if __name__ == "__main__":
    main()
