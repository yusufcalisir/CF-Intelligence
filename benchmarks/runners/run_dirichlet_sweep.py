"""CLI runner for the Federated Learning Optimizer and Dirichlet Sensitivity Sweep."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from experiments.ablations.dirichlet_sweep import (  # noqa: E402
    DirichletSweepRunner,
    SweepConfig,
    serialize_sweep_artifacts,
)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the Dirichlet sweep."""
    parser = argparse.ArgumentParser(
        description="Run Federated Learning Optimizer & Dirichlet Sensitivity Sweep (FedAvg, FedProx, SCAFFOLD)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
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
        default=10,
        help="Number of global federation communication rounds",
    )
    parser.add_argument(
        "--local-epochs",
        type=int,
        default=2,
        help="Number of local client training epochs per round",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=0.02,
        help="Local learning rate for SGD",
    )
    parser.add_argument(
        "--fedprox-mu",
        type=float,
        default=0.01,
        help="FedProx proximal regularization coefficient mu",
    )
    parser.add_argument(
        "--alphas",
        type=float,
        nargs="+",
        default=[0.1, 0.5, 1.0],
        help="Dirichlet non-IID partition parameters to evaluate",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic pseudo-random seed",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=_PROJECT_ROOT,
        help="Base repository root directory",
    )
    return parser.parse_args()


def main() -> None:
    """Execute the Dirichlet sensitivity benchmark."""
    args = parse_args()
    config = SweepConfig(
        n_clients=args.n_clients,
        rounds=args.rounds,
        local_epochs=args.local_epochs,
        learning_rate=args.lr,
        fedprox_mu=args.fedprox_mu,
        alphas=args.alphas,
        seed=args.seed,
    )
    runner = DirichletSweepRunner(config)
    results = runner.run_full_sweep()
    serialize_sweep_artifacts(results, args.base_dir)
    print("\n[+] Dirichlet Sensitivity Sweep completed successfully.")
    print(f"    Total alpha configurations evaluated: {len(config.alphas)}")
    print(f"    Rounds per configuration: {config.rounds}")
    print(f"    Participating institutions: {config.n_clients}")


if __name__ == "__main__":
    main()
