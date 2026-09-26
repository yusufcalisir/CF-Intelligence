"""European Credit Card Extreme Imbalance Federated Benchmark Runner.

Orchestrates multi-bank federated training, extreme imbalance robustness,
isolated banking silo comparison (highlighting near-zero positive client collapse),
precision-recall curve generation, and publication-grade empirical artifact serialization.
"""

# ruff: noqa: E402
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure repository root and backend are in sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from experiments.credit_card.run_creditcard_benchmark import run_creditcard_benchmark

logger = logging.getLogger("benchmarks.runners.run_creditcard_benchmark")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Run European Credit Card Extreme Imbalance Federated Benchmark")
    parser.add_argument("--nrows", type=int, default=None, help="Number of rows to load (None for all rows)")
    parser.add_argument("--all-rows", action="store_true", help="Load entire 284,807 transactions")
    parser.add_argument("--rounds", type=int, default=5, help="Number of FL communication rounds")
    parser.add_argument("--local-epochs", type=int, default=2, help="Local epochs per round")
    parser.add_argument("--batch-size", type=int, default=64, help="Mini-batch size")
    parser.add_argument("--lr", type=float, default=0.002, help="Learning rate")
    parser.add_argument("--skew-mode", type=str, default="extreme_skew", choices=["extreme_skew", "dirichlet"], help="Partitioning mode")
    parser.add_argument("--alpha", type=float, default=0.5, help="Dirichlet concentration alpha (if mode=dirichlet)")
    parser.add_argument("--num-clients", type=int, default=3, help="Number of bank clients")
    parser.add_argument("--fedprox-mu", type=float, default=0.01, help="FedProx proximal parameter mu")
    parser.add_argument("--test-ratio", type=float, default=0.20, help="Untouched test set ratio")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--require-real", action="store_true", help="Require real physical dataset file")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory")

    args = parser.parse_args()
    results = run_creditcard_benchmark(
        nrows=args.nrows,
        all_rows=args.all_rows,
        rounds=args.rounds,
        local_epochs=args.local_epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        skew_mode=args.skew_mode,
        alpha=args.alpha,
        num_clients=args.num_clients,
        fedprox_mu=args.fedprox_mu,
        test_ratio=args.test_ratio,
        seed=args.seed,
        require_real=args.require_real,
        output_dir=args.output_dir,
    )
    logger.info("Benchmark complete. Artifacts written to: %s", results["paths"])


if __name__ == "__main__":
    main()
