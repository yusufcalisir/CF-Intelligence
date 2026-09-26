"""Master Chart Generator CLI.

Wrapper script to generate all publication-grade figures for benchmarks and experiments.
Outputs:
  - docs/figures/*.png (Benchmark comparative analyses)
  - experiments/results/*/plots/*.png (Per-experiment evaluation plots)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.runners.generate_charts import (
    generate_auc_comparison,
    generate_byzantine_resilience,
    generate_fl_convergence,
    generate_latency_concurrency,
    generate_privacy_utility,
    setup_style,
)
from benchmarks.runners.plot_publication_figures import plot_all_experiment_figures


def generate_all_benchmark_charts(results_dir: Path, output_dir: Path) -> None:
    """Generate all canonical benchmark comparison figures in docs/figures/."""
    setup_style()
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[*] Generating benchmark figures from {results_dir} -> {output_dir}")

    generate_auc_comparison(results_dir, output_dir)
    generate_fl_convergence(results_dir, output_dir)
    generate_privacy_utility(results_dir, output_dir)
    generate_byzantine_resilience(results_dir, output_dir)
    generate_latency_concurrency(results_dir, output_dir)
    print("[+] All benchmark figures successfully generated.")


def generate_all_experiment_plots(experiments_root: Path) -> None:
    """Scan experiments/results/ and generate plots for any unplotted results.json."""
    if not experiments_root.exists():
        return

    import json
    for res_file in experiments_root.glob("**/results.json"):
        plot_dir = res_file.parent / "plots"
        try:
            with open(res_file, encoding="utf-8") as f:
                data = json.load(f)
            plots = plot_all_experiment_figures(data, plot_dir)
            if plots:
                print(f"[+] Generated {len(plots)} plots for experiment: {res_file.parent.name}")
        except Exception as e:
            print(f"[!] Warning: Failed to generate plots for {res_file}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Generate publication-grade figures")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=REPO_ROOT / "benchmarks" / "results" / "raw",
        help="Path to raw benchmark results directory",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "docs" / "figures",
        help="Path to output directory for publication figures",
    )
    parser.add_argument(
        "--include-experiments",
        action="store_true",
        help="Also generate plots for all experiment runs in experiments/results",
    )
    args = parser.parse_args()

    generate_all_benchmark_charts(args.results_dir, args.output_dir)

    if args.include_experiments:
        exp_root = REPO_ROOT / "experiments" / "results"
        generate_all_experiment_plots(exp_root)


if __name__ == "__main__":
    main()
