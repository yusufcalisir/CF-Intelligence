"""IEEE-CIS Identity Join, Temporal Splitting (TransactionDT) & Non-IID Bank Partitioning.

This module provides production-grade ingestion, identity join, temporal splitting,
and federated client partitioning for the IEEE-CIS Fraud Detection (Vesta Corporation) benchmark:
1. Ingests raw transaction records (train_transaction.csv) and joins identity metadata (train_identity.csv).
2. Performs categorical encoding (ProductCD, card4, card6, DeviceType, M1-M9, id_12) and feature engineering.
3. Enforces strict temporal train/test split along the 'TransactionDT' axis (zero future lookahead leakage).
4. Partitions training samples across K simulated banking institutions (Bank A, Bank B, Bank C)
   governed by a Dirichlet distribution with concentration parameter alpha in {0.1, 0.5, 1.0} or card attributes.
5. Computes distribution diagnostics (sample count, fraud prevalence, KL divergence, TVD).
6. Interfaces directly with ComparativeBenchmarkEngine and federated optimization harnesses.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Ensure backend directory is in python search path
_backend_dir = str(Path(__file__).resolve().parents[2] / "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import numpy as np  # noqa: E402

from app.application.services.dataloader import load_ieee_cis  # noqa: E402

logger = logging.getLogger(__name__)


class IEEECISPartitioner:
    """Ingests IEEE-CIS transactions, enforces temporal splitting, and partitions data across federated banks."""

    def __init__(
        self,
        alpha: float = 0.5,
        num_clients: int = 3,
        client_names: list[str] | None = None,
        seed: int = 42,
        min_samples_per_client: int = 10,
        test_ratio: float = 0.20,
        partition_strategy: str = "dirichlet",
    ) -> None:
        """Initialize IEEECISPartitioner.

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
        partition_strategy : str
            Partitioning method: 'dirichlet' for label distribution skew, or 'card_brand' for institutional network split.
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
        self.partition_strategy = str(partition_strategy)

        # State storage
        self.raw_data: dict[str, Any] | None = None
        self.feature_names: list[str] = []
        self.X_all: np.ndarray | None = None
        self.y_all: np.ndarray | None = None
        self.dt_all: np.ndarray | None = None

        # Temporal split state
        self.X_train: np.ndarray | None = None
        self.y_train: np.ndarray | None = None
        self.dt_train: np.ndarray | None = None
        self.X_test: np.ndarray | None = None
        self.y_test: np.ndarray | None = None
        self.dt_test: np.ndarray | None = None
        self.temporal_cutoff_dt: float | None = None

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
        join_identity: bool = True,
    ) -> IEEECISPartitioner:
        """Ingest IEEE-CIS transactions via dataloader.

        Parameters
        ----------
        path : Path | str | None
            Custom directory containing IEEE-CIS dataset files.
        nrows : int | None
            Maximum rows to read.
        require_real : bool
            If True, strictly requires physical dataset files without synthetic fallback.
        all_rows : bool
            If True, overrides nrows to load the entire dataset.
        n_mock_txns : int
            Number of transactions if falling back to synthetic generator.
        join_identity : bool
            Whether to perform left join with train_identity.csv.
        """
        p = Path(path) if path is not None else None
        logger.info(
            "[IEEECISPartitioner] Loading IEEE-CIS data (nrows=%s, all_rows=%s, require_real=%s, join_identity=%s)",
            nrows,
            all_rows,
            require_real,
            join_identity,
        )
        data = load_ieee_cis(
            path=p,
            nrows=nrows,
            require_real=require_real,
            all_rows=all_rows,
            n_mock_txns=n_mock_txns,
            join_identity=join_identity,
            rng=self.rng,
        )
        self.set_data(
            X=data["X"],
            y=data["y"],
            feature_names=data.get("feature_names"),
            transaction_dt=data.get("transaction_dt"),
            source=data.get("source", "unknown"),
        )
        return self

    def set_data(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: list[str] | None = None,
        transaction_dt: np.ndarray | None = None,
        source: str = "custom",
    ) -> IEEECISPartitioner:
        """Directly supply pre-loaded features, labels, and timestamps."""
        X_arr = np.asarray(X, dtype=np.float32)
        y_arr = np.asarray(y, dtype=int)
        if len(X_arr) != len(y_arr):
            raise ValueError(f"Length mismatch: X has {len(X_arr)} rows, y has {len(y_arr)} rows")
        if len(X_arr) < (self.num_clients * self.min_samples_per_client):
            raise ValueError(
                f"Dataset size ({len(X_arr)}) is insufficient for {self.num_clients} clients "
                f"with min_samples_per_client={self.min_samples_per_client}"
            )

        self.feature_names = list(feature_names) if feature_names else [f"feat_{i}" for i in range(X_arr.shape[1])]
        self.X_all = X_arr
        self.y_all = y_arr

        if transaction_dt is not None:
            self.dt_all = np.asarray(transaction_dt, dtype=np.float64)
        elif "TransactionDT" in self.feature_names:
            dt_idx = self.feature_names.index("TransactionDT")
            self.dt_all = self.X_all[:, dt_idx].astype(np.float64)
        else:
            # Fallback to linear synthetic second timestamps starting from day 1
            self.dt_all = np.linspace(86400, 86400 * 180, num=len(X_arr), dtype=np.float64)

        self.raw_data = {
            "X": self.X_all,
            "y": self.y_all,
            "feature_names": self.feature_names,
            "source": source,
            "fraud_ratio": float(np.mean(self.y_all)) if len(self.y_all) > 0 else 0.0,
            "transaction_dt": self.dt_all,
        }

        # Apply strict temporal split
        self._apply_temporal_split()
        return self

    def _apply_temporal_split(self) -> None:
        """Enforce strict chronological past-to-future separation along the 'TransactionDT' axis.

        The earliest (1 - test_ratio) fraction of data is allocated to the training pool,
        and the latest test_ratio fraction is sequestered as the untouched global test set.
        Guarantees zero future lookahead leakage: max(train_dt) <= min(test_dt).
        """
        assert self.X_all is not None and self.y_all is not None and self.dt_all is not None

        # Sort chronologically by TransactionDT if not strictly monotonic
        if not np.all(self.dt_all[:-1] <= self.dt_all[1:]):
            logger.info("[IEEECISPartitioner] Sorting dataset chronologically by TransactionDT")
            sort_idx = np.argsort(self.dt_all, kind="stable")
            self.X_all = self.X_all[sort_idx]
            self.y_all = self.y_all[sort_idx]
            self.dt_all = self.dt_all[sort_idx]

        n_total = len(self.X_all)
        n_train = int(n_total * (1.0 - self.test_ratio))
        if n_train <= 0 or n_train >= n_total:
            raise ValueError(f"Invalid split sizing: n_total={n_total}, n_train={n_train}")

        self.X_train = self.X_all[:n_train]
        self.y_train = self.y_all[:n_train]
        self.dt_train = self.dt_all[:n_train]

        self.X_test = self.X_all[n_train:]
        self.y_test = self.y_all[n_train:]
        self.dt_test = self.dt_all[n_train:]

        train_max_dt = float(np.max(self.dt_train))
        test_min_dt = float(np.min(self.dt_test))
        self.temporal_cutoff_dt = train_max_dt

        # Mathematical zero-leakage invariant assertion
        if train_max_dt > test_min_dt:
            raise AssertionError(
                f"Zero temporal leakage violation! Maximum train TransactionDT ({train_max_dt}) "
                f"exceeds minimum test TransactionDT ({test_min_dt})."
            )

        logger.info(
            "[IEEECISPartitioner] Temporal split complete: Train=%d txns (dt <= %.1f), Test=%d txns (dt >= %.1f)",
            len(self.X_train),
            train_max_dt,
            len(self.X_test),
            test_min_dt,
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
                logger.warning("[IEEECISPartitioner] Client %s received 0 samples", self.client_names[k])

        all_allocated_arr = np.array(all_allocated, dtype=int)
        if len(all_allocated_arr) != n_train:
            raise AssertionError(
                f"Sample conservation violated: partitioned {len(all_allocated_arr)} samples, expected {n_train}"
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

        self.diagnostics = self.compute_distribution_diagnostics()
        return self.client_partitions

    def partition_by_card_brand(self) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Partition training set based on payment card network brand indicators.

        Uses one-hot columns (e.g. card4_visa, card4_mastercard, card4_discover, card4_american_express)
        to simulate institutional domain heterogeneity across major card issuers.
        """
        if self.X_train is None or self.y_train is None:
            raise RuntimeError("Data not loaded. Call load_data() or set_data() before partitioning.")

        n_train = len(self.y_train)
        client_indices: list[list[int]] = [[] for _ in range(self.num_clients)]

        # Check for card brand feature columns
        brand_cols = [c for c in self.feature_names if c.startswith("card4_")]
        if not brand_cols:
            logger.info("[IEEECISPartitioner] card4 columns not found, falling back to Dirichlet partitioning")
            return self.partition_dirichlet()

        # Assign transactions to brand based on argmax of one-hot card4 columns
        brand_indices = [self.feature_names.index(col) for col in brand_cols]
        brand_matrix = self.X_train[:, brand_indices]

        # For rows with all zeros (missing card4), assign round-robin
        for i in range(n_train):
            row_brands = brand_matrix[i]
            if np.max(row_brands) > 0.5:
                top_brand = int(np.argmax(row_brands))
                target_client = top_brand % self.num_clients
            else:
                target_client = i % self.num_clients
            client_indices[target_client].append(i)

        # Build client datasets
        self.client_partitions = {}
        self.client_indices = {}
        for k, name in enumerate(self.client_names):
            k_idx = np.array(sorted(client_indices[k]), dtype=int)
            self.client_indices[name] = k_idx
            self.client_partitions[name] = (self.X_train[k_idx], self.y_train[k_idx])

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
            "temporal_cutoff_dt": self.temporal_cutoff_dt,
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
            "dataset": "IEEE-CIS",
            "feature_dim": len(self.feature_names),
            "feature_names_sample": self.feature_names[:25],
            "temporal_split": {
                "test_ratio": self.test_ratio,
                "train_samples": len(self.X_train) if self.X_train is not None else 0,
                "test_samples": len(self.X_test) if self.X_test is not None else 0,
                "temporal_cutoff_dt": self.temporal_cutoff_dt,
            },
            "partition_diagnostics": self.diagnostics,
        }

    def save_summary(self, path: Path | str) -> Path:
        """Serialize partitioning summary and diagnostics to a JSON artifact."""
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        summary = self.get_summary()

        def _json_serializable(val: Any) -> Any:
            if isinstance(val, (np.integer, np.int64, np.int32)):
                return int(val)
            if isinstance(val, (np.floating, np.float64, np.float32)):
                return float(val)
            if isinstance(val, np.ndarray):
                return val.tolist()
            return str(val)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=_json_serializable)
        logger.info("[IEEECISPartitioner] Summary persisted to %s", out_path)
        return out_path


def main() -> None:
    """CLI entrypoint for IEEE-CIS identity join, temporal splitting, and client partitioning."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="IEEE-CIS Dataset Ingestion & Non-IID Dirichlet Partitioner")
    parser.add_argument("--data-dir", type=str, default=None, help="Directory containing IEEE-CIS CSV files")
    parser.add_argument("--nrows", type=int, default=20_000, help="Number of rows to load (default: 20000)")
    parser.add_argument("--all-rows", action="store_true", help="Load full dataset")
    parser.add_argument("--require-real", action="store_true", help="Forbid synthetic fallback")
    parser.add_argument("--alpha", type=float, default=0.5, help="Dirichlet concentration parameter (default: 0.5)")
    parser.add_argument("--num-clients", type=int, default=3, help="Number of bank clients (default: 3)")
    parser.add_argument("--test-ratio", type=float, default=0.20, help="Ratio for future test split (default: 0.20)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument(
        "--output-json",
        type=str,
        default="experiments/ieee_cis/partition_summary.json",
        help="Path to save partitioning diagnostics JSON",
    )
    args = parser.parse_args()

    partitioner = IEEECISPartitioner(
        alpha=args.alpha,
        num_clients=args.num_clients,
        seed=args.seed,
        test_ratio=args.test_ratio,
    )
    partitioner.load_data(
        path=args.data_dir,
        nrows=None if args.all_rows else args.nrows,
        require_real=args.require_real,
        all_rows=args.all_rows,
    )
    partitioner.partition_dirichlet()

    # Save summary
    out_file = partitioner.save_summary(args.output_json)
    print(f"\n[IEEE-CIS Partitioner] Partitioning complete across {args.num_clients} banks.")
    print(f"Summary JSON written to: {out_file}")
    diag = partitioner.diagnostics
    print(f"Train samples: {diag['total_train_samples']}, Test samples: {diag['total_test_samples']}")
    print(f"Global fraud ratio: {diag['global_fraud_ratio']:.4f}")
    for client, stats in diag["client_statistics"].items():
        print(
            f"  {client}: {stats['num_samples']} samples ({stats['sample_share']*100:.1f}%), "
            f"fraud_ratio={stats['fraud_ratio']:.4f}, KL={stats['kl_divergence']:.4f}, TVD={stats['total_variation_distance']:.4f}"
        )


if __name__ == "__main__":
    main()
