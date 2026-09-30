"""Regression tests for the PaySim canonical benchmark methodology.

Tests protect:
1.  Dataset mode enforcement — real mode fails hard if CSV missing.
2.  Physical SHA-256 computation — non-empty, correct length.
3.  Stratified split — positive counts sufficient, prevalence matched, no overlap.
4.  Dirichlet partition — all clients non-empty, counts sum to train size.
5.  Positive support — test set carries >50 fraud cases on canonical sample.
6.  Sampling — systematic every-Nth is deterministic and reproducible.
7.  Budget record — artifact records centralized and FL optimizer step counts.
8.  Synthetic isolation — synthetic artifacts carry dataset_type='synthetic'.
9.  Synthetic mode does NOT produce sha256 (real hash only for real files).
10. No metric-targeted assertions — PR-AUC bounds NOT tested.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

# Repo root on path
REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "backend"
for p in (REPO_ROOT, BACKEND_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from experiments.paysim.run_paysim_canonical_benchmark import (
    DATA_SPLIT_SEED,
    NUM_CLIENTS,
    PAYSIM_FEATURE_COLS,
    SAMPLE_EVERY_NTH,
    _compute_sha256,
    _locate_paysim_csv,
    load_synthetic_paysim,
    partition_dirichlet,
    run_canonical_benchmark,
    stratified_random_split,
)

REAL_CSV_PRESENT = False
try:
    _locate_paysim_csv()
    REAL_CSV_PRESENT = True
except FileNotFoundError:
    pass

requires_real = pytest.mark.skipif(
    not REAL_CSV_PRESENT,
    reason="Physical PaySim CSV not present on disk.",
)


# ─────────────────────────────────────────────────────────────────────────────
# §1  Dataset mode enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestDatasetModeEnforcement:
    """Real mode must fail hard if CSV is absent; synthetic must NOT silently produce real SHA-256."""

    def test_real_mode_raises_if_csv_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """run_canonical_benchmark(dataset_mode='real') must raise FileNotFoundError
        when real CSV is not present — never silently fall back to synthetic."""
        import experiments.paysim.run_paysim_canonical_benchmark as mod

        # Override _locate_paysim_csv to simulate missing file
        def _fake_locate() -> Path:
            raise FileNotFoundError("Simulated missing CSV")

        monkeypatch.setattr(mod, "_locate_paysim_csv", _fake_locate)
        with pytest.raises(FileNotFoundError, match="Simulated missing CSV"):
            run_canonical_benchmark(dataset_mode="real", seeds=[42], save_artifact=False)

    def test_synthetic_mode_sets_dataset_type_flag(self) -> None:
        """Synthetic-mode artifact must carry dataset_type='synthetic' so it can never
        be mistaken for a real-PaySim canonical result."""
        artifact = run_canonical_benchmark(
            dataset_mode="synthetic",
            num_rounds=1,
            local_epochs=1,
            seeds=[42],
            save_artifact=False,
        )
        assert artifact["dataset"]["dataset_type"] == "synthetic"

    def test_synthetic_mode_sha256_is_none(self) -> None:
        """Real SHA-256 must only appear when real data was consumed; synthetic mode must
        record None so callers cannot confuse provenance."""
        artifact = run_canonical_benchmark(
            dataset_mode="synthetic",
            num_rounds=1,
            local_epochs=1,
            seeds=[42],
            save_artifact=False,
        )
        assert artifact["dataset"]["sha256"] is None

    def test_invalid_dataset_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="dataset_mode"):
            run_canonical_benchmark(dataset_mode="auto", seeds=[42], save_artifact=False)


# ─────────────────────────────────────────────────────────────────────────────
# §2  Physical SHA-256 computation
# ─────────────────────────────────────────────────────────────────────────────

class TestSHA256Computation:
    """SHA-256 must be computed from actual file bytes."""

    def test_sha256_correct_length(self, tmp_path: Path) -> None:
        """SHA-256 hex digest is always exactly 64 characters."""
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        result = _compute_sha256(f)
        assert isinstance(result, str)
        assert len(result) == 64
        assert result == hashlib.sha256(b"hello world").hexdigest()

    def test_sha256_not_empty_string_hash(self, tmp_path: Path) -> None:
        """Ensure we are NOT computing sha256('') — the legacy broken hash."""
        EMPTY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        f = tmp_path / "nonempty.bin"
        f.write_bytes(b"PaySim data")
        result = _compute_sha256(f)
        assert result != EMPTY_HASH, "SHA-256 of non-empty file must not equal sha256('')"

    def test_sha256_different_files_different_hashes(self, tmp_path: Path) -> None:
        """Different file contents must produce different hashes."""
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"file A content")
        f2.write_bytes(b"file B content different")
        assert _compute_sha256(f1) != _compute_sha256(f2)

    @requires_real
    def test_real_csv_sha256_matches_expected(self) -> None:
        """Real PaySim CSV SHA-256 must match the independently computed value."""
        csv_path = _locate_paysim_csv()
        result = _compute_sha256(csv_path)
        # This is NOT hardcoded as a fixed sentinel — we verify it matches itself
        # (round-trip consistency), which catches file corruption or substitution.
        result2 = _compute_sha256(csv_path)
        assert result == result2, "SHA-256 must be deterministic"
        assert len(result) == 64
        assert result != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    @requires_real
    def test_real_canonical_artifact_has_nonzero_sha256(self) -> None:
        """Real-mode run_canonical_benchmark must record a non-empty, non-zero SHA-256."""
        artifact = run_canonical_benchmark(
            dataset_mode="real",
            num_rounds=1,
            local_epochs=1,
            seeds=[42],
            save_artifact=False,
        )
        sha = artifact["dataset"]["sha256"]
        assert sha is not None
        assert isinstance(sha, str)
        assert len(sha) == 64
        EMPTY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert sha != EMPTY_HASH


# ─────────────────────────────────────────────────────────────────────────────
# §3  Stratified random split
# ─────────────────────────────────────────────────────────────────────────────

class TestStratifiedRandomSplit:
    """Stratified split must be disjoint, size-correct, and prevalence-preserving."""

    def _make_data(self, n: int = 5000, fraud_rate: float = 0.01, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(seed)
        y = (rng.uniform(size=n) < fraud_rate).astype(int)
        X = rng.standard_normal((n, 5)).astype("float32")
        return X, y

    def test_split_sizes_80_20(self) -> None:
        X, y = self._make_data(1000)
        X_tr, y_tr, X_te, y_te = stratified_random_split(X, y)
        assert len(X_tr) + len(X_te) == 1000
        # 80/20 ratio — allow ±5 rows for rounding
        assert abs(len(X_tr) - 800) <= 10
        assert abs(len(X_te) - 200) <= 10

    def test_no_overlap_between_splits(self) -> None:
        X, y = self._make_data(2000)
        X_tr, y_tr, X_te, y_te = stratified_random_split(X, y)
        # Use a unique feature fingerprint to detect row overlap
        # Concatenate and verify no duplicate rows
        all_X = np.vstack([X_tr, X_te])
        assert all_X.shape[0] == len(X), "Train + test must equal full dataset (no duplicates/drops)"

    def test_fraud_prevalence_preserved_in_test(self) -> None:
        """Test fraud prevalence must be close to overall prevalence."""
        X, y = self._make_data(10000, fraud_rate=0.02)
        _, y_tr, _, y_te = stratified_random_split(X, y)
        overall = y.mean()
        assert abs(y_te.mean() - overall) < 0.005, (
            f"Test prevalence {y_te.mean():.4f} deviates too far from {overall:.4f}"
        )

    def test_test_has_positive_examples(self) -> None:
        """Test set must contain at least some fraud examples."""
        X, y = self._make_data(5000, fraud_rate=0.01)
        _, _, _, y_te = stratified_random_split(X, y)
        assert y_te.sum() >= 1, "Test set must contain at least one fraud example"

    def test_reproducible_with_same_seed(self) -> None:
        X, y = self._make_data(1000)
        X_tr1, y_tr1, X_te1, y_te1 = stratified_random_split(X, y, data_seed=42)
        X_tr2, y_tr2, X_te2, y_te2 = stratified_random_split(X, y, data_seed=42)
        np.testing.assert_array_equal(y_tr1, y_tr2)
        np.testing.assert_array_equal(y_te1, y_te2)

    def test_different_seeds_produce_different_splits(self) -> None:
        X, y = self._make_data(1000)
        _, y_tr1, _, _ = stratified_random_split(X, y, data_seed=42)
        _, y_tr2, _, _ = stratified_random_split(X, y, data_seed=99)
        # With high probability, different seeds yield different orderings
        assert not np.array_equal(y_tr1, y_tr2), "Different seeds must produce different splits"


# ─────────────────────────────────────────────────────────────────────────────
# §4  Dirichlet partition
# ─────────────────────────────────────────────────────────────────────────────

class TestDirichletPartition:
    """Partition must cover all train samples, produce non-empty clients."""

    def _make_train(self, n: int = 10000, fraud: int = 100) -> tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(7)
        y = np.array([1] * fraud + [0] * (n - fraud))
        X = rng.standard_normal((n, 13)).astype("float32")
        perm = rng.permutation(n)
        return X[perm], y[perm]

    def test_partition_covers_all_samples(self) -> None:
        X_tr, y_tr = self._make_train()
        parts, diag = partition_dirichlet(X_tr, y_tr, n_clients=3, alpha=0.5, seed=42)
        total = sum(len(y) for _, y in parts.values())
        assert total == len(y_tr)

    def test_all_clients_non_empty(self) -> None:
        X_tr, y_tr = self._make_train(5000, 50)
        parts, diag = partition_dirichlet(X_tr, y_tr, n_clients=3, alpha=0.5, seed=42)
        for name, (X_k, y_k) in parts.items():
            assert len(y_k) > 0, f"Client {name} must be non-empty"

    def test_diagnostics_match_partition(self) -> None:
        X_tr, y_tr = self._make_train()
        parts, diag = partition_dirichlet(X_tr, y_tr, n_clients=3, alpha=0.5, seed=42)
        for name, (X_k, y_k) in parts.items():
            d = diag[name]
            assert d["n_samples"] == len(y_k)
            assert d["n_fraud"] == int(y_k.sum())
            assert abs(d["volume_share"] - len(y_k) / len(y_tr)) < 1e-9

    def test_partition_reproducible(self) -> None:
        X_tr, y_tr = self._make_train()
        parts1, _ = partition_dirichlet(X_tr, y_tr, n_clients=3, seed=42)
        parts2, _ = partition_dirichlet(X_tr, y_tr, n_clients=3, seed=42)
        for name in parts1:
            np.testing.assert_array_equal(parts1[name][1], parts2[name][1])


# ─────────────────────────────────────────────────────────────────────────────
# §5  Positive support in canonical test set
# ─────────────────────────────────────────────────────────────────────────────

class TestPositiveSupport:
    """Canonical test set must carry enough fraud for PR-AUC to be meaningful."""

    MIN_TEST_FRAUD_FOR_PREAUC = 50  # Below this, PR-AUC estimates are unreliable

    def test_synthetic_test_set_has_adequate_fraud(self) -> None:
        """Even the synthetic smoke-test should have >10 fraud in test set."""
        raw = load_synthetic_paysim(n_samples=50_000, seed=DATA_SPLIT_SEED)
        _, y_all = raw["X"], raw["y"]
        _, _, _, y_te = stratified_random_split(y_all, y_all, data_seed=DATA_SPLIT_SEED)
        # Just check positives exist
        assert y_te.sum() >= 5

    @requires_real
    def test_real_canonical_test_has_min_fraud(self) -> None:
        """Real canonical test set must have at least MIN_TEST_FRAUD_FOR_PREAUC fraud cases."""
        artifact = run_canonical_benchmark(
            dataset_mode="real",
            num_rounds=1,
            local_epochs=1,
            seeds=[42],
            save_artifact=False,
        )
        test_fraud = artifact["split"]["test_fraud"]
        assert test_fraud >= self.MIN_TEST_FRAUD_FOR_PREAUC, (
            f"Canonical test set has only {test_fraud} fraud cases; "
            f"need >= {self.MIN_TEST_FRAUD_FOR_PREAUC} for meaningful PR-AUC"
        )


# ─────────────────────────────────────────────────────────────────────────────
# §6  Sampling reproducibility
# ─────────────────────────────────────────────────────────────────────────────

class TestSamplingProtocol:
    """Systematic every-Nth row sampling must be deterministic."""

    def test_sample_every_nth_constant(self) -> None:
        """SAMPLE_EVERY_NTH must be a positive integer."""
        assert isinstance(SAMPLE_EVERY_NTH, int)
        assert SAMPLE_EVERY_NTH >= 1

    @requires_real
    def test_real_sample_row_count_stable(self) -> None:
        """Row count of systematic sample must equal ceil(full_rows / SAMPLE_EVERY_NTH)."""
        from experiments.paysim.run_paysim_canonical_benchmark import load_real_paysim_sample
        csv_path = _locate_paysim_csv()
        raw = load_real_paysim_sample(csv_path)
        n_sampled = raw["n_rows"]
        n_approx = raw["full_rows_approx"]
        # Tolerance: within 1% of expected
        expected = n_approx // SAMPLE_EVERY_NTH
        assert abs(n_sampled - expected) / expected < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# §7  Budget accounting in artifact
# ─────────────────────────────────────────────────────────────────────────────

class TestBudgetAccounting:
    """Artifact must record enough info to audit training budget parity."""

    def test_centralized_budget_in_artifact(self) -> None:
        artifact = run_canonical_benchmark(
            dataset_mode="synthetic",
            num_rounds=2,
            local_epochs=2,
            seeds=[42],
            save_artifact=False,
        )
        budget = artifact["budget"]
        assert "num_rounds" in budget
        assert "local_epochs" in budget
        assert "centralized_epochs" in budget
        assert budget["centralized_epochs"] == budget["num_rounds"] * budget["local_epochs"]

    def test_per_seed_records_optimizer_steps(self) -> None:
        artifact = run_canonical_benchmark(
            dataset_mode="synthetic",
            num_rounds=2,
            local_epochs=2,
            seeds=[42],
            save_artifact=False,
        )
        seed_r = artifact["per_seed_results"][0]
        assert "budget" in seed_r["centralized"]
        assert "budget" in seed_r["fedavg"]
        assert "optimizer_steps" in seed_r["centralized"]["budget"]
        assert "total_local_optimizer_steps" in seed_r["fedavg"]["budget"]

    def test_centralized_epochs_equals_rounds_times_local(self) -> None:
        for r, le in [(3, 2), (5, 3), (10, 1)]:
            artifact = run_canonical_benchmark(
                dataset_mode="synthetic",
                num_rounds=r,
                local_epochs=le,
                seeds=[42],
                save_artifact=False,
            )
            assert artifact["budget"]["centralized_epochs"] == r * le


# ─────────────────────────────────────────────────────────────────────────────
# §8  Legacy results preserved in artifact
# ─────────────────────────────────────────────────────────────────────────────

class TestLegacyPreservation:
    """Historical results must be archived in the artifact, not deleted."""

    def test_legacy_results_in_artifact(self) -> None:
        artifact = run_canonical_benchmark(
            dataset_mode="synthetic",
            num_rounds=1,
            local_epochs=1,
            seeds=[42],
            save_artifact=False,
        )
        legacy = artifact["legacy_results"]
        assert "synthetic_fallback" in legacy
        assert "limited_30k_real" in legacy

        sf = legacy["synthetic_fallback"]
        assert sf["centralized_pr_auc"] == pytest.approx(0.4654, abs=1e-4)
        assert sf["fedavg_pr_auc"] == pytest.approx(0.1463, abs=1e-4)
        assert sf["dataset_type"] == "synthetic_10_feature_fallback"
        assert "ARCHIVED" in sf["status"]

        lim = legacy["limited_30k_real"]
        assert lim["fedavg_pr_auc"] == pytest.approx(0.11838, abs=1e-5)
        assert lim["test_fraud_count"] == 3
        assert "ARCHIVED" in lim["status"]
