"""CLI runner for the Architectural Component Factorial Ablation Matrix (Graph x DP x SecAgg x CrossBank)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from experiments.ablations.factorial_runner import (  # noqa: E402
    FactorialAblationRunner,
    FactorialConfig,
    serialize_ablation_artifacts,
)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the component factorial ablation sweep."""
    parser = argparse.ArgumentParser(
        description="Run 16-Configuration Architectural Component Factorial Ablation Matrix",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--n-samples",
        type=int,
        default=8000,
        help="Total synthetic consortium transactions",
    )
    parser.add_argument(
        "--n-clients",
        type=int,
        default=5,
        help="Number of participating consortium bank institutions",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=5,
        help="Federation communication rounds per configuration",
    )
    parser.add_argument(
        "--local-epochs",
        type=int,
        default=2,
        help="Number of local training epochs per client per round",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=32,
        help="Mini-batch size for client local SGD",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=0.02,
        help="Client local learning rate",
    )
    parser.add_argument(
        "--dp-sigma",
        type=float,
        default=1.0,
        help="DP-SGD Gaussian noise multiplier",
    )
    parser.add_argument(
        "--dp-clip",
        type=float,
        default=1.0,
        help="DP-SGD L2 gradient clipping threshold",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic pseudo-random seed",
    )
    parser.add_argument(
        "--base-dir",
        type=str,
        default=str(_PROJECT_ROOT),
        help="Base repository root directory",
    )
    return parser.parse_args()


def main() -> None:
    """Execute the factorial ablation runner."""
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    config = FactorialConfig(
        n_samples=args.n_samples,
        n_clients=args.n_clients,
        rounds=args.rounds,
        local_epochs=args.local_epochs,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        dp_sigma=args.dp_sigma,
        dp_clip_norm=args.dp_clip,
        seed=args.seed,
    )

    base_dir = Path(args.base_dir)
    runner = FactorialAblationRunner(config)
    suite = runner.run_full_suite()

    paths = serialize_ablation_artifacts(suite, base_dir)
    logging.info("Factorial ablation suite completed successfully:")
    for k, v in paths.items():
        logging.info("  - %s: %s", k, v)


if __name__ == "__main__":
    main()
