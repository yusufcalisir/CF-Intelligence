"""PaySim Preprocessing, Feature Engineering & Non-IID Dirichlet Client Partitioning.

This module provides production-grade ingestion and federated client partitioning
for the PaySim Mobile Money Fraud benchmark dataset:
1. Ingests raw or preprocessed PaySim logs (PS_20174392719_1491204439457_log.csv).
2. Performs strict temporal train/test split along the 'step' axis (zero future leakage).
3. Partitions training samples across K simulated banking institutions (Bank A, Bank B, Bank C)
   governed by a Dirichlet distribution with concentration parameter alpha in {0.1, 0.5, 1.0}.
4. Computes distribution diagnostics (sample count, fraud prevalence, KL divergence, TVD).
5. Interfaces directly with ComparativeBenchmarkEngine and federated optimization harnesses.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.application.services.dataloader import PAYSIM_FEATURE_COLS, load_paysim

logger = logging.getLogger(__name__)


class PaySimPartitioner:
    """Ingests PaySim transactions and partitions training data across federated banking clients."""

    def __init__(
        self,
        alpha: float = 0.5,
        num_clients: int = 3,
        client_names: list[str] | None = None,
        seed: int = 42,
        min_samples_per_client: int = 10,
        test_ratio: float = 0.20,
    ) -> None:
        """Initialize PaySimPartitioner.

        Parameters
        ----------
        alpha : float
            Dirichlet concentration parameter governing non-IID label and quantity skew.
            Lower alpha (e.g. 0.1) induces extreme heterogeneity across clients.
            Higher alpha (e.g. 1.0) approaches IID uniform allocation.
        num_clients : int
            Number of simulated banking institutions (default: 3).
        client_names : list[str] | None
            Explicit identifiers for client banks. Defaults to ['bank_a', 'bank_b', 'bank_c'].
        seed : int
            Deterministic random seed for reproducibility.
        min_samples_per_client : int
            Minimum total transactions allocated to any single banking institution.
        test_ratio : float
            Proportion of chronologically latest transactions reserved for the untouched global test set.
        """
        if alpha <= 0:
            raise ValueError(f"Dirichlet concentration parameter alpha must be > 0, got {alpha}")
        if num_clients < 2:
            raise ValueError(f"num_clients must be at least 2 for federated partitioning, got {num_clients}")
        if not (0.0 < test_ratio < 1.0):
            raise ValueError(f"test_ratio must be between 0 and 1, got {test_ratio}")

        self.alpha = float(alpha)
        self.num_clients = int(num_clients)
        if client_names is not None:
            if len(client_names) != self.num_clients:
                raise ValueError(
                    f"Length of client_names ({len(client_names)}) does not match num_clients ({self.num_clients})"
                )
            self.client_names = list(client_names)
        else:
            default_labels = ["bank_a", "bank_b", "bank_c", "bank_d", "bank_e", "bank_f"]
            if self.num_clients <= len(default_labels):
                self.client_names = default_labels[: self.num_clients]
            else:
                self.client_names = [f"bank_{i}" for i in range(self.num_clients)]

        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.min_samples_per_client = int(min_samples_per_client)
        self.test_ratio = float(test_ratio)

        # State storage
        self.raw_data: dict[str, Any] | None = None
        self.feature_names: list[str] = list(PAYSIM_FEATURE_COLS)
        self.X_all: np.ndarray | None = None
        self.y_all: np.ndarray | None = None
        self.steps_all: np.ndarray | None = None

        # Temporal split state
        self.X_train: np.ndarray | None = None
        self.y_train: np.ndarray | None = None
        self.steps_train: np.ndarray | None = None
        self.X_test: np.ndarray | None = None
        self.y_test: np.ndarray | None = None
        self.steps_test: np.ndarray | None = None
        self.temporal_cutoff_step: float | None = None

        # Federated client partition state
        self.client_partitions: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.client_indices: dict[str, np.ndarray] = {}
        self.diagnostics: dict[str, Any] = {}

    def load_data(
        self,
        path: Path | str | None = None,
        nrows: int | None = None,
        require_real: bool = False,
        all_rows: bool = False,
        n_mock_txns: int = 10_000,
    ) -> PaySimPartitioner:
        """Ingest PaySim transactions via dataloader.

        Parameters
        ----------
        path : Path | str | None
            Custom directory containing PaySim dataset files.
        nrows : int | None
            Maximum rows to read. If None and all_rows is True, reads all 6.36M records.
        require_real : bool
            If True, strictly requires physical dataset files without synthetic fallback.
        all_rows : bool
            If True, overrides nrows to load the entire dataset.
        n_mock_txns : int
            Number of transactions if falling back to synthetic generator.
        """
        p = Path(path) if path is not None else None
        logger.info(
            "[PaySimPartitioner] Loading PaySim data (nrows=%s, all_rows=%s, require_real=%s)",
            nrows,
            all_rows,
            require_real,
        )
        data = load_paysim(
            path=p,
            nrows=nrows,
            require_real=require_real,
            all_rows=all_rows,
            n_mock_txns=n_mock_txns,
            rng=self.rng,
        )
        self.set_data(
            X=data["X"],
            y=data["y"],
            feature_names=data.get("feature_names"),
            source=data.get("source", "unknown"),
        )
        return self

    def set_data(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: list[str] | None = None,
        source: str = "custom",
    ) -> PaySimPartitioner:
        """Directly supply pre-loaded features and labels."""
        X_arr = np.asarray(X, dtype=np.float32)
        y_arr = np.asarray(y, dtype=int)
        if len(X_arr) != len(y_arr):
            raise ValueError(f"Length mismatch: X has {len(X_arr)} rows, y has {len(y_arr)} rows")
        if len(X_arr) < (self.num_clients * self.min_samples_per_client):
            raise ValueError(
                f"Dataset size ({len(X_arr)}) is insufficient for {self.num_clients} clients "
                f"with min_samples_per_client={self.min_samples_per_client}"
            )

        self.feature_names = list(feature_names) if feature_names else list(PAYSIM_FEATURE_COLS)
        self.X_all = X_arr
        self.y_all = y_arr
        # Step is column 0 if feature_names has 'step'
        time_idx = 0
        if "step" in self.feature_names:
            time_idx = self.feature_names.index("step")
        self.steps_all = self.X_all[:, time_idx]

        self.raw_data = {
            "X": self.X_all,
            "y": self.y_all,
            "feature_names": self.feature_names,
            "source": source,
            "fraud_ratio": float(np.mean(self.y_all)) if len(self.y_all) > 0 else 0.0,
        }

        # Apply strict temporal split
        self._apply_temporal_split()
        return self

    def _apply_temporal_split(self) -> None:
        """Enforce strict chronological past-to-future separation along the 'step' axis.

        The earliest (1 - test_ratio) fraction of data is allocated to the training pool,
        and the latest test_ratio fraction is sequestered as the untouched global test set.
        """
        assert self.X_all is not None and self.y_all is not None and self.steps_all is not None

        # Sort chronologically by step if not strictly monotonic
        if not np.all(self.steps_all[:-1] <= self.steps_all[1:]):
            logger.info("[PaySimPartitioner] Sorting dataset chronologically by step")
            sort_idx = np.argsort(self.steps_all, kind="stable")
            self.X_all = self.X_all[sort_idx]
            self.y_all = self.y_all[sort_idx]
            self.steps_all = self.steps_all[sort_idx]

        n_total = len(self.X_all)
        n_train = int(n_total * (1.0 - self.test_ratio))
        if n_train <= 0 or n_train >= n_total:
            raise ValueError(f"Invalid split sizing: n_total={n_total}, n_train={n_train}")

        self.X_train = self.X_all[:n_train]
        self.y_train = self.y_all[:n_train]
        self.steps_train = self.steps_all[:n_train]

        self.X_test = self.X_all[n_train:]
        self.y_test = self.y_all[n_train:]
        self.steps_test = self.steps_all[n_train:]

        train_max_step = float(np.max(self.steps_train))
        test_min_step = float(np.min(self.steps_test))
        self.temporal_cutoff_step = train_max_step

        # Mathematical zero-leakage invariant assertion
        if train_max_step > test_min_step:
            raise AssertionError(
                f"Zero temporal leakage violation! Maximum train step ({train_max_step}) "
                f"exceeds minimum test step ({test_min_step})."
            )

        logger.info(
            "[PaySimPartitioner] Temporal split complete: Train=%d txns (steps <= %.1f), Test=%d txns (steps >= %.1f)",
            len(self.X_train),
            train_max_step,
            len(self.X_test),
            test_min_step,
        )

    def partition_dirichlet(self, alpha: float | None = None) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Partition training set across K simulated banks using Dirichlet distribution.

        Parameters
        ----------
        alpha : float | None
            Override Dirichlet concentration parameter alpha. If None, uses self.alpha.

        Returns
        -------
        dict[str, tuple[np.ndarray, np.ndarray]]
            Mapping of client_id -> (X_k, y_k).
        """
        if self.X_train is None or self.y_train is None:
            raise RuntimeError("Data not loaded. Call load_data() or set_data() before partitioning.")

        if alpha is not None:
            if alpha <= 0:
                raise ValueError(f"alpha must be > 0, got {alpha}")
            self.alpha = float(alpha)

        n_train = len(self.y_train)
        idx_train = np.arange(n_train)
        client_indices: list[list[int]] = [[] for _ in range(self.num_clients)]

        # Group indices by class to model label distribution skew
        classes = np.unique(self.y_train)
        for c in classes:
            c_indices = idx_train[self.y_train == c].copy()
            self.rng.shuffle(c_indices)
            n_c = len(c_indices)

            # Sample Dirichlet proportions for class c across K banks: q ~ Dir(alpha * 1_K)
            proportions = self.rng.dirichlet(np.repeat(self.alpha, self.num_clients))

            # Convert proportions to integer counts
            counts = np.floor(proportions * n_c).astype(int)
            remainder = n_c - np.sum(counts)
            if remainder > 0:
                # Distribute remainder to clients with highest fractional remainder
                fractional = (proportions * n_c) - counts
                top_indices = np.argsort(fractional)[::-1][:remainder]
                for idx in top_indices:
                    counts[idx] += 1

            start_idx = 0
            for k in range(self.num_clients):
                cnt = counts[k]
                if cnt > 0:
                    client_indices[k].extend(c_indices[start_idx : start_idx + cnt])
                    start_idx += cnt

        # Ensure minimum samples per client
        for k in range(self.num_clients):
            if len(client_indices[k]) < self.min_samples_per_client and n_train >= (
                self.num_clients * self.min_samples_per_client
            ):
                deficit = self.min_samples_per_client - len(client_indices[k])
                # Find client with largest surplus
                donor_k = int(np.argmax([len(indices) for indices in client_indices]))
                if len(client_indices[donor_k]) > (self.min_samples_per_client + deficit):
                    transferred = client_indices[donor_k][-deficit:]
                    client_indices[donor_k] = client_indices[donor_k][:-deficit]
                    client_indices[k].extend(transferred)

        # Validate partition invariants
        all_allocated = []
        for k, indices in enumerate(client_indices):
            all_allocated.extend(indices)
            if len(indices) == 0:
                logger.warning("[PaySimPartitioner] Client %s received 0 samples", self.client_names[k])

        # Exact conservation & non-overlap checks
        all_allocated_arr = np.array(all_allocated, dtype=int)
        if len(all_allocated_arr) != n_train:
            raise AssertionError(
                f"Sample conservation violated: partitioned {len(all_allocated_arr)} samples, "
                f"expected {n_train}"
            )
        if len(np.unique(all_allocated_arr)) != n_train:
            raise AssertionError("Partition overlap detected: duplicate sample indices assigned across clients.")

        # Build client datasets
        self.client_partitions = {}
        self.client_indices = {}
        for k, name in enumerate(self.client_names):
            k_idx = np.array(sorted(client_indices[k]), dtype=int)
            self.client_indices[name] = k_idx
            self.client_partitions[name] = (self.X_train[k_idx], self.y_train[k_idx])

        # Compute diagnostics
        self.diagnostics = self.compute_distribution_diagnostics()
        return self.client_partitions

    def compute_distribution_diagnostics(self) -> dict[str, Any]:
        """Compute statistical skew metrics, class balance, and KL divergence across clients."""
        if not self.client_partitions or self.y_train is None:
            return {}

        n_train = len(self.y_train)
        global_fraud_count = int(np.sum(self.y_train))
        global_fraud_ratio = float(global_fraud_count / n_train) if n_train > 0 else 0.0
        p_global = np.array([1.0 - global_fraud_ratio, global_fraud_ratio], dtype=float)

        client_stats: dict[str, dict[str, Any]] = {}
        kl_divergences: list[float] = []
        tvds: list[float] = []
        fraud_ratios: list[float] = []
        sample_counts: list[int] = []

        eps = 1e-12
        for name, (_, y_k) in self.client_partitions.items():
            n_k = len(y_k)
            sample_counts.append(n_k)
            n_fraud_k = int(np.sum(y_k))
            n_legit_k = n_k - n_fraud_k
            r_k = float(n_fraud_k / n_k) if n_k > 0 else 0.0
            fraud_ratios.append(r_k)

            p_k = np.array([1.0 - r_k, r_k], dtype=float)

            # Kullback-Leibler Divergence: D_KL(P_k || P_global)
            kl = float(np.sum(p_k * np.log((p_k + eps) / (p_global + eps))))
            kl_divergences.append(max(0.0, kl))

            # Total Variation Distance: TVD(P_k, P_global) = 0.5 * sum |P_k - P_global|
            tvd = float(0.5 * np.sum(np.abs(p_k - p_global)))
            tvds.append(tvd)

            client_stats[name] = {
                "num_samples": n_k,
                "sample_share": float(n_k / n_train) if n_train > 0 else 0.0,
                "num_legit": n_legit_k,
                "num_fraud": n_fraud_k,
                "fraud_ratio": r_k,
                "kl_divergence": max(0.0, kl),
                "total_variation_distance": tvd,
            }

        return {
            "alpha": self.alpha,
            "num_clients": self.num_clients,
            "seed": self.seed,
            "total_train_samples": n_train,
            "global_fraud_count": global_fraud_count,
            "global_fraud_ratio": global_fraud_ratio,
            "temporal_cutoff_step": self.temporal_cutoff_step,
            "total_test_samples": len(self.X_test) if self.X_test is not None else 0,
            "client_statistics": client_stats,
            "consortium_metrics": {
                "mean_kl_divergence": float(np.mean(kl_divergences)),
                "max_kl_divergence": float(np.max(kl_divergences)),
                "mean_tvd": float(np.mean(tvds)),
                "fraud_ratio_std": float(np.std(fraud_ratios)),
                "sample_count_std": float(np.std(sample_counts)),
            },
        }

    def partition_multi_alpha(
        self,
        alphas: list[float] | None = None,
    ) -> dict[float, dict[str, Any]]:
        """Run and compare partitioning across multiple concentration parameters.

        Parameters
        ----------
        alphas : list[float] | None
            List of concentration parameters (default: [0.1, 0.5, 1.0]).

        Returns
        -------
        dict[float, dict[str, Any]]
            Mapping of alpha -> diagnostics report.
        """
        if alphas is None:
            alphas = [0.1, 0.5, 1.0]

        multi_reports: dict[float, dict[str, Any]] = {}
        original_seed = self.seed
        for alpha in alphas:
            # Reset seed so comparison across alphas is deterministic from same baseline
            self.rng = np.random.default_rng(original_seed)
            self.partition_dirichlet(alpha=alpha)
            multi_reports[alpha] = self.compute_distribution_diagnostics()

        return multi_reports

    def get_bank_train_partitions(self) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Return client training partitions in ComparativeBenchmarkEngine format."""
        if not self.client_partitions:
            raise RuntimeError("Partitions not generated. Call partition_dirichlet() first.")
        return self.client_partitions

    def get_global_test(self) -> tuple[np.ndarray, np.ndarray]:
        """Return the untouched future test set (X_test, y_test)."""
        if self.X_test is None or self.y_test is None:
            raise RuntimeError("Test split not generated. Call load_data() or set_data() first.")
        return self.X_test, self.y_test

    def get_summary(self) -> dict[str, Any]:
        """Return unified summary of dataset, temporal split, and Dirichlet partitions."""
        return {
            "dataset": "PaySim",
            "feature_dim": len(self.feature_names),
            "feature_names": self.feature_names,
            "temporal_split": {
                "test_ratio": self.test_ratio,
                "train_samples": len(self.X_train) if self.X_train is not None else 0,
                "test_samples": len(self.X_test) if self.X_test is not None else 0,
                "temporal_cutoff_step": self.temporal_cutoff_step,
            },
            "partition_diagnostics": self.diagnostics,
        }

    def save_partitions(
        self,
        output_dir: Path | str,
        export_parquet: bool = True,
    ) -> Path:
        """Export partition indices, summary JSON, and client Parquet files.

        Parameters
        ----------
        output_dir : Path | str
            Target directory for exported partition artifacts.
        export_parquet : bool
            If True, exports individual client training sets and global test set as Parquet.

        Returns
        -------
        Path
            Absolute path to output directory.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # 1. Summary JSON
        summary = self.get_summary()
        summary_path = out / "partition_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        logger.info("[PaySimPartitioner] Saved partition summary to %s", summary_path)

        # 2. Indices NPZ
        indices_path = out / "partition_indices.npz"
        np.savez_compressed(
            indices_path,
            **self.client_indices,
            test_indices=np.arange(
                len(self.X_train) if self.X_train is not None else 0,
                len(self.X_all) if self.X_all is not None else 0,
            ),
        )

        # 3. Client Parquet files
        if export_parquet and self.X_train is not None and self.X_test is not None and self.y_test is not None:
            for name, (X_k, y_k) in self.client_partitions.items():
                df_k = pd.DataFrame(X_k, columns=self.feature_names)
                df_k["isFraud"] = y_k
                parquet_path = out / f"{name}_train.parquet"
                df_k.to_parquet(parquet_path, index=False)

            # Global test Parquet
            df_test = pd.DataFrame(self.X_test, columns=self.feature_names)
            df_test["isFraud"] = self.y_test
            df_test.to_parquet(out / "global_test.parquet", index=False)
            logger.info("[PaySimPartitioner] Exported Parquet datasets to %s", out)

        return out


def main() -> None:
    """CLI entrypoint for PaySim partitioning."""
    parser = argparse.ArgumentParser(description="PaySim Non-IID Dirichlet Client Partitioner")
    parser.add_argument("--alpha", type=float, default=0.5, help="Dirichlet concentration parameter")
    parser.add_argument("--num-clients", type=int, default=3, help="Number of simulated banking institutions")
    parser.add_argument("--nrows", type=int, default=100_000, help="Number of transactions to load")
    parser.add_argument("--all-rows", action="store_true", help="Load full 6.36M transaction dataset")
    parser.add_argument("--multi-alpha", action="store_true", help="Run multi-alpha comparison (0.1, 0.5, 1.0)")
    parser.add_argument("--output-dir", type=str, default="experiments/paysim/partitions", help="Artifact directory")
    parser.add_argument("--export-parquet", action="store_true", help="Export client and test Parquet files")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    partitioner = PaySimPartitioner(alpha=args.alpha, num_clients=args.num_clients)
    partitioner.load_data(nrows=None if args.all_rows else args.nrows, all_rows=args.all_rows)

    if args.multi_alpha:
        logger.info("[CLI] Running multi-alpha partitioning (0.1, 0.5, 1.0)...")
        reports = partitioner.partition_multi_alpha([0.1, 0.5, 1.0])
        print("\n" + "=" * 70)
        print("PaySim Multi-Alpha Non-IID Dirichlet Comparison")
        print("=" * 70)
        for alpha, rep in reports.items():
            cons = rep["consortium_metrics"]
            print(f"\n--- Alpha = {alpha} ---")
            print(
                f"Mean KL Divergence: {cons['mean_kl_divergence']:.4f} | Max KL: {cons['max_kl_divergence']:.4f} | Fraud Ratio Std: {cons['fraud_ratio_std']:.6f}"
            )
            for b_name, b_stat in rep["client_statistics"].items():
                print(
                    f"  {b_name:8s}: {b_stat['num_samples']:6d} txns ({b_stat['sample_share']*100:5.1f}%) | "
                    f"Fraud: {b_stat['num_fraud']:4d} ({b_stat['fraud_ratio']*100:6.3f}%) | "
                    f"KL: {b_stat['kl_divergence']:.4f}"
                )
    else:
        partitioner.partition_dirichlet()
        out_dir = partitioner.save_partitions(args.output_dir, export_parquet=args.export_parquet)
        diag = partitioner.diagnostics
        print("\n" + "=" * 70)
        print(f"PaySim Partitioning Complete (Alpha = {args.alpha}, Clients = {args.num_clients})")
        print(f"Artifacts exported to: {out_dir}")
        print("=" * 70)
        for b_name, b_stat in diag["client_statistics"].items():
            print(
                f"{b_name:8s}: {b_stat['num_samples']:6d} txns ({b_stat['sample_share']*100:5.1f}%) | "
                f"Fraud: {b_stat['num_fraud']:4d} ({b_stat['fraud_ratio']*100:6.3f}%) | "
                f"KL: {b_stat['kl_divergence']:.4f}"
            )


if __name__ == "__main__":
    main()
