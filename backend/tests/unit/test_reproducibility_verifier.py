"""Unit tests for the master reproducibility verification engine.

Verifies the programmatic 38-item integrity sweep across all six canonical
architectural categories, ensuring reproducible benchmarks, cryptographic
soundness, and documentation-code parity.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.verify_reproducibility import REPO_ROOT, ReproducibilityVerifier, SweepItemResult


@pytest.fixture
def verifier() -> ReproducibilityVerifier:
    """Fixture providing a configured ReproducibilityVerifier instance."""
    return ReproducibilityVerifier(root=REPO_ROOT)


def test_verifier_initialization(verifier: ReproducibilityVerifier) -> None:
    """Verify clean initialization of the reproducibility verifier."""
    assert verifier.root == REPO_ROOT
    assert verifier.results == []
    custom_verifier = ReproducibilityVerifier(root=Path("."))
    assert custom_verifier.root == Path(".")


def test_category_1_dataset_integrity(verifier: ReproducibilityVerifier) -> None:
    """Verify Category 1: Empirical Dataset Integrity & Licensing (Items 1-8)."""
    results = verifier.verify_datasets()
    assert len(results) == 8
    for res in results:
        assert isinstance(res, SweepItemResult)
        assert res.category == "Empirical Datasets & Licensing"
        assert res.passed, f"Dataset check failed for {res.item_id}: {res.name} ({res.details})"
        assert res.item_id.startswith("ITEM-0")


def test_category_2_artifact_hierarchies(verifier: ReproducibilityVerifier) -> None:
    """Verify Category 2: Standardized 5-Artifact Experiment Hierarchy (Items 9-16)."""
    results = verifier.verify_artifact_hierarchies()
    assert len(results) == 8
    for res in results:
        assert isinstance(res, SweepItemResult)
        assert res.category == "Standardized Experiment Artifacts"
        assert res.passed, f"Artifact hierarchy check failed for {res.item_id}: {res.name}"


def test_category_3_benchmark_matrices(verifier: ReproducibilityVerifier) -> None:
    """Verify Category 3: Master Benchmark Matrices & Invariant Enforcement (Items 17-22)."""
    results = verifier.verify_benchmark_matrices()
    assert len(results) == 6
    item_ids = {r.item_id for r in results}
    assert item_ids == {"ITEM-17", "ITEM-18", "ITEM-19", "ITEM-20", "ITEM-21", "ITEM-22"}
    for res in results:
        assert res.passed, f"Benchmark matrix check failed for {res.item_id}: {res.name}"


def test_category_4_claims_and_governance(verifier: ReproducibilityVerifier) -> None:
    """Verify Category 4: Quantitative Claim Registry & Zero-Hyping Governance (Items 23-28)."""
    results = verifier.verify_claims_and_governance()
    assert len(results) == 6
    for res in results:
        assert res.passed, f"Claim governance check failed for {res.item_id}: {res.name}"


def test_category_5_cryptographic_invariants(verifier: ReproducibilityVerifier) -> None:
    """Verify Category 5: Cryptographic, Privacy & Multi-Tenant Invariants (Items 29-33)."""
    results = verifier.verify_cryptographic_and_privacy_invariants()
    assert len(results) == 5
    for res in results:
        assert res.passed, f"Cryptographic invariant check failed for {res.item_id}: {res.name}"


def test_category_6_test_suites_and_ci(verifier: ReproducibilityVerifier) -> None:
    """Verify Category 6: Code Quality, CI/CD & Automated Test Suites (Items 34-38)."""
    results = verifier.verify_test_suites_and_ci()
    assert len(results) == 5
    for res in results:
        assert res.passed, f"Test suite CI check failed for {res.item_id}: {res.name}"


def test_full_38_items_sweep_completeness(verifier: ReproducibilityVerifier) -> None:
    """Verify comprehensive 38-item execution with 100% pass rate and certification."""
    summary = verifier.run_all()
    assert summary["total_items"] == 38
    assert summary["passed_items"] == 38
    assert summary["failed_items"] == 0
    assert summary["pass_rate_pct"] == 100.0
    assert summary["certification_status"] == "CERTIFIED_REPRODUCIBLE"
    assert len(summary["items"]) == 38


def test_json_serialization_output(verifier: ReproducibilityVerifier) -> None:
    """Verify that execution summary is valid and serializable to JSON."""
    summary = verifier.run_all()
    json_str = json.dumps(summary)
    loaded = json.loads(json_str)
    assert loaded["certification_status"] == "CERTIFIED_REPRODUCIBLE"
    assert len(loaded["items"]) == 38
    assert "timestamp" in loaded


def test_cli_execution_flags(verifier: ReproducibilityVerifier, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify report rendering and print output formatting."""
    verifier.print_report(as_json=False, summary_only=True)
    captured = capsys.readouterr()
    assert "MASTER 38-ITEM PLATFORM INTEGRITY AUDIT" in captured.out
    assert "CERTIFIED_REPRODUCIBLE" in captured.out
    assert "Passed Items:         38 / 38 (100.0%)" in captured.out
