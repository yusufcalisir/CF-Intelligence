"""Unit and Contract Tests for Benchmark Evidence Integrity, Provenance & Mathematical Reconciliation.

Asserts all 12 Scientific Invariants (Phase 2):
1. No silent synthetic fallback when real dataset is unavailable.
2. Synthetic benchmarks cannot write to canonical artifact paths or claim REAL_DATA provenance.
3. Placeholders and unexecuted benchmarks cannot masquerade as measured zero performance.
4. Real IEEE-CIS remains truthfully NOT_EVALUATED without synthetic contamination.
5. All canonical aggregates reconcile mathematically with underlying per-seed measurements.
6. Byzantine defense retention ratios are mathematically derived, not hardcoded.
7. One-way evidence flow: Master benchmark matrix reflects Level 1 canonical evidence.
8. Non-IID Dirichlet alpha=0.5 synchronization between raw artifacts and reporting surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from benchmarks.canonical_registry import (
    CANONICAL_REGISTRY,
    ArtifactStatus,
    DatasetProvenanceType,
    resolve_canonical_artifact,
)
from benchmarks.provenance_schema import (
    CommunicationEligibility,
    EvidenceScope,
    validate_per_seed_aggregate,
    validate_retention_ratio,
)
from benchmarks.runners.run_fraud_benchmark import run_fraud_benchmark

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestDatasetProvenanceInvariants:
    """Invariant 1 & 4: Dataset missing loud failure, explicit synthetic mode, provenance typing."""

    def test_real_benchmark_fails_loudly_when_dataset_missing(self, tmp_path: Path):
        """Mandatory: missing real dataset MUST raise FileNotFoundError, never fall back to synthetic."""
        with pytest.raises(FileNotFoundError) as exc_info:
            run_fraud_benchmark(
                dataset_name="ieee_cis",
                dataset_mode="real",
                save_artifact=False,
            )
        assert "dataset_mode='real' requires preprocessed client partitions" in str(exc_info.value)

    def test_synthetic_execution_requires_explicit_mode_and_writes_smoke_artifact(self, tmp_path: Path):
        """Synthetic execution must be explicitly requested and write to _synthetic_smoke.json."""
        out_file = tmp_path / "custom_test.json"
        res = run_fraud_benchmark(
            dataset_name="paysim",
            dataset_mode="synthetic",
            rounds=1,
            save_artifact=True,
            output_path=out_file,
        )
        assert res["status"] == "SMOKE_TEST"
        assert res["is_canonical"] is False
        assert res["provenance_classification"] == "PROJECT_SYNTHETIC"

        with open(out_file, encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["status"] == "SMOKE_TEST"
        assert saved["is_canonical"] is False

    def test_canonical_artifacts_declare_dataset_provenance_and_hashes(self):
        """Every canonical artifact must declare dataset provenance and valid physical hashes."""
        # PaySim
        paysim_entry = CANONICAL_REGISTRY["paysim_canonical"]
        assert paysim_entry.provenance_type == DatasetProvenanceType.EXTERNALLY_SIMULATED
        assert paysim_entry.status == ArtifactStatus.CANONICAL
        paysim_data = resolve_canonical_artifact("paysim_canonical")
        assert paysim_data is not None
        assert paysim_data["dataset"]["sha256"] == "16910f90577b0d981bf8ff289714510bb89bc71bff7d3f220f024e287e4eea6b"

        # Credit Card
        cc_entry = CANONICAL_REGISTRY["credit_card_canonical"]
        assert cc_entry.provenance_type == DatasetProvenanceType.REAL_DATA
        assert cc_entry.status == ArtifactStatus.CANONICAL
        cc_data = resolve_canonical_artifact("credit_card_canonical")
        assert cc_data is not None
        assert cc_data["benchmark_metadata"]["sha256_hash"] == "76274b691b16a6c49d3f159c883398e03ccd6d1ee12d9d8ee38f4b4b98551a89"

        # Elliptic
        ell_entry = CANONICAL_REGISTRY["elliptic_canonical"]
        assert ell_entry.provenance_type == DatasetProvenanceType.REAL_DATA
        ell_data = resolve_canonical_artifact("elliptic_canonical")
        assert ell_data is not None
        assert "elliptic_cache.parquet" in ell_data["dataset_metadata"]["sha256_hashes"]


class TestArtifactLifecycleAndStatusInvariants:
    """Invariant 2, 6, 9: Placeholders, historical artifacts, and NOT_EVALUATED states."""

    def test_placeholder_cannot_be_canonical(self):
        """Unpopulated placeholder (0.0/0.5) must NOT have CANONICAL status."""
        cc_raw_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "fraud_benchmark_credit_card.json"
        with open(cc_raw_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("is_canonical") is False
        assert data.get("status") == "SUPERSEDED_PLACEHOLDER"
        assert "superseded_by" in data

    def test_historical_synthetic_fallbacks_are_quarantined(self):
        """Old synthetic fallback runs must be labeled SUPERSEDED or HISTORICAL."""
        # PaySim legacy fallback
        paysim_raw_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "fraud_benchmark_paysim.json"
        with open(paysim_raw_path, encoding="utf-8") as f:
            paysim_raw = json.load(f)
        assert paysim_raw.get("is_canonical") is False
        assert paysim_raw.get("status") == "SUPERSEDED"
        assert paysim_raw.get("dataset_type") == "PROJECT_SYNTHETIC"

        # IEEE-CIS legacy fallback
        ieee_raw_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "fraud_benchmark_ieee_cis.json"
        with open(ieee_raw_path, encoding="utf-8") as f:
            ieee_raw = json.load(f)
        assert ieee_raw.get("is_canonical") is False
        assert ieee_raw.get("status") == "HISTORICAL"
        assert ieee_raw.get("canonical_status_for_real_data") == "NOT_EVALUATED"

    def test_ieee_cis_real_is_canonical_and_reconciles(self):
        """Real Kaggle IEEE-CIS 590k must be classified CANONICAL in registry with verified artifact."""
        entry = CANONICAL_REGISTRY["ieee_cis_real"]
        assert entry.status == ArtifactStatus.CANONICAL
        assert entry.canonical_artifact_relpath == "experiments/ieee_cis/canonical_results.json"
        assert entry.is_external_communication_safe is True
        assert entry.mandatory_caveat is not None


class TestAggregateMathematicalReconciliation:
    """Invariant 5: Recomputing means, sample SDs, retention ratios from per-seed data."""

    def test_paysim_reconciles_mathematically(self):
        """PaySim aggregate mean and standard deviation must match per-seed runs."""
        data = resolve_canonical_artifact("paysim_canonical")
        assert data is not None
        per_seed = data["per_seed_results"]
        cent_vals = [s["centralized"]["pr_auc"] for s in per_seed]
        fed_vals = [s["fedavg"]["pr_auc"] for s in per_seed]

        cent_mean = data["aggregate"]["centralized_pr_auc"]["mean"]
        cent_std = data["aggregate"]["centralized_pr_auc"]["std"]
        fed_mean = data["aggregate"]["fedavg_pr_auc"]["mean"]
        fed_std = data["aggregate"]["fedavg_pr_auc"]["std"]

        # Validate within float tolerance
        validate_per_seed_aggregate(cent_vals, cent_mean, cent_std, label="PaySim Centralized")
        validate_per_seed_aggregate(fed_vals, fed_mean, fed_std, label="PaySim FedAvg")

        assert abs(cent_mean - 0.9545) < 0.001
        assert abs(fed_mean - 0.9545) < 0.001

    def test_credit_card_reconciles_mathematically(self):
        """Credit Card controlled aggregate mean and sample SD must match per-seed runs."""
        data = resolve_canonical_artifact("credit_card_canonical")
        assert data is not None
        per_seed = data["per_seed_results"]
        cent_vals = [per_seed[s]["centralized_equalized_10ep"]["pr_auc"] for s in ["42", "123", "456"]]
        fed_vals = [per_seed[s]["federated_fedavg"]["pr_auc"] for s in ["42", "123", "456"]]

        stats = data["aggregate_summary"]
        cent_mean = stats["centralized_equalized_10ep"]["pr_auc"]["mean"]
        cent_std = stats["centralized_equalized_10ep"]["pr_auc"]["std"]
        fed_mean = stats["federated_fedavg"]["pr_auc"]["mean"]
        fed_std = stats["federated_fedavg"]["pr_auc"]["std"]

        validate_per_seed_aggregate(cent_vals, cent_mean, cent_std, label="CC Centralized", ddof=1)
        validate_per_seed_aggregate(fed_vals, fed_mean, fed_std, label="CC FedAvg", ddof=1)

        assert abs(cent_mean - 0.8219) < 0.001
        assert abs(fed_mean - 0.8248) < 0.001

    def test_byzantine_retention_ratio_reconciles_mathematically(self):
        """Byzantine historical proxy must be quarantined and retention ratio verified on disk."""
        entry = CANONICAL_REGISTRY["byzantine_sign_inversion"]
        assert entry.status == ArtifactStatus.HISTORICAL
        assert entry.communication_eligibility == CommunicationEligibility.HISTORICAL_ONLY
        assert entry.canonical_artifact_relpath is None
        assert entry.is_external_communication_safe is False

        # Must reject canonical resolution of historical proxy
        with pytest.raises(ValueError, match="non-canonical status"):
            resolve_canonical_artifact("byzantine_sign_inversion")

        # Canonical benchmark promoted to CANONICAL status and resolves to disk artifact
        new_entry = CANONICAL_REGISTRY["byzantine_federated_canonical"]
        assert new_entry.status == ArtifactStatus.CANONICAL
        byz_canonical_data = resolve_canonical_artifact("byzantine_federated_canonical")
        assert byz_canonical_data is not None
        assert byz_canonical_data["status"] == "CANONICAL"

        # Verify historical artifact on disk remains numerically intact
        raw_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "byzantine_benchmark_sign_inversion.json"
        with open(raw_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["status"] == "HISTORICAL"
        assert data["is_canonical"] is False
        clean = data["honest_fedavg_pr_auc"]
        trimmed = data["trimmed_mean_pr_auc"]
        reported_ratio = data["trimmed_mean_retention_ratio"]

        computed = trimmed / clean
        assert abs(computed - reported_ratio) < 1e-3
        assert abs(computed - 0.9966) < 1e-3
        validate_retention_ratio(trimmed, clean, reported_ratio)

    def test_mathematical_validation_fails_on_discrepancy(self):
        """Validation function must raise ValueError on deliberate tampering."""
        with pytest.raises(ValueError):
            validate_per_seed_aggregate([0.80, 0.82, 0.84], reported_mean=0.99, reported_std=0.02)


class TestCanonicalResolutionAndMasterMatrix:
    """Invariant 7, 8, 10: Master benchmark matrix reflects canonical evidence without fake defaults."""

    def test_master_matrix_parity_with_canonical_evidence(self):
        """Master benchmark matrix must consume canonical metrics and strictly avoid fake defaults."""
        matrix_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "master_benchmark_matrix.json"
        assert matrix_path.exists(), "master_benchmark_matrix.json missing"

        with open(matrix_path, encoding="utf-8") as f:
            matrix = json.load(f)

        datasets = matrix["datasets"]

        # PaySim parity
        paysim_fa = datasets["paysim"]["paradigms"]["federated_fedavg"]["pr_auc"]
        assert abs(paysim_fa - 0.9545) < 0.005
        assert datasets["paysim"]["evaluated_samples"] == 636262
        assert datasets["paysim"]["real_vs_synthetic"] == "EXTERNALLY_SIMULATED"

        # Credit Card parity
        cc_fa = datasets["credit_card"]["paradigms"]["federated_fedavg"]["pr_auc"]
        assert abs(cc_fa - 0.8248) < 0.005
        assert datasets["credit_card"]["evaluated_samples"] == 284807
        assert datasets["credit_card"]["real_vs_synthetic"] == "REAL_DATA"

        # IEEE-CIS canonical empirical metrics derived from real execution
        ieee_fa = datasets["ieee_cis"]["paradigms"]["federated_fedavg"]["pr_auc"]
        ieee_cent = datasets["ieee_cis"]["paradigms"]["centralized_pooled"]["pr_auc"]
        assert ieee_fa is not None and abs(ieee_fa - 0.3895) < 0.005
        assert ieee_cent is not None and abs(ieee_cent - 0.4422) < 0.005
        assert datasets["ieee_cis"]["paradigms"]["federated_fedavg"]["status"] == "EVALUATED"
        assert datasets["ieee_cis"]["paradigms"]["centralized_pooled"]["status"] == "EVALUATED"
        assert datasets["ieee_cis"]["evaluated_samples"] == 590540
        assert datasets["ieee_cis"]["real_vs_synthetic"] == "REAL_DATA"

        # Elliptic negative finding preserved
        ell_cent = datasets["elliptic"]["paradigms"]["centralized_pooled"]["pr_auc"]
        assert abs(ell_cent - 0.3761) < 0.005
        assert datasets["elliptic"]["paradigms"]["federated_fedavg"]["status"] == "NOT_EVALUATED"


class TestNonIIDEvidenceSynchronization:
    """Invariant 12: Level 1 non-IID artifact authority."""

    def test_non_iid_alpha_0_5_raw_evidence_authority(self):
        """fl_comparison_alpha_0.5.json must be the authoritative Level 1 source (~0.2331)."""
        data = resolve_canonical_artifact("fl_non_iid_alpha_0_5")
        assert data is not None

        strats = data["strategies"]
        fedavg_pr = strats["fedavg"]["final_pr_auc"]
        fedprox_pr = strats["fedprox"]["final_pr_auc"]
        scaffold_pr = strats["scaffold"]["final_pr_auc"]

        assert abs(fedavg_pr - 0.2331) < 0.005
        assert abs(fedprox_pr - 0.2285) < 0.005
        assert abs(scaffold_pr - 0.2196) < 0.005

        # Discrepant uncommitted draft numbers (0.0757 / 0.0548) are rejected
        assert fedavg_pr > 0.15


class TestPhase2AdversarialDefectRegressions:
    """Permanent regression test suite enforcing remedies for all Phase 2.1 Adversarial Audit Attacks (A-J)."""

    def test_adversarial_attack_a_internal_generator_classification(self):
        """Attack A: Internal generators cannot masquerade as external benchmarks or real bank data."""
        synth_entry = CANONICAL_REGISTRY["synthaml_synthetic"]
        amlnet_entry = CANONICAL_REGISTRY["amlnet_synthetic"]

        assert synth_entry.provenance_type == DatasetProvenanceType.PROJECT_SYNTHETIC
        assert synth_entry.evidence_scope == EvidenceScope.PROJECT_SYNTHETIC_EXPERIMENT
        assert synth_entry.communication_eligibility == CommunicationEligibility.INTERNAL_ONLY
        assert not synth_entry.is_external_communication_safe
        assert synth_entry.mandatory_caveat is not None
        assert "scripts/generate_synthaml_dataset.py" in synth_entry.mandatory_caveat

        assert amlnet_entry.provenance_type == DatasetProvenanceType.PROJECT_SYNTHETIC
        assert amlnet_entry.evidence_scope == EvidenceScope.PROJECT_SYNTHETIC_EXPERIMENT
        assert amlnet_entry.communication_eligibility == CommunicationEligibility.INTERNAL_ONLY
        assert amlnet_entry.status == ArtifactStatus.EXPERIMENTAL

    def test_adversarial_attack_b_trivial_structuring_separability(self):
        """Attack B: AMLNet structuring rule separability is explicitly caveated and demoted to EXPERIMENTAL."""
        amlnet_entry = CANONICAL_REGISTRY["amlnet_synthetic"]
        assert amlnet_entry.status == ArtifactStatus.EXPERIMENTAL
        assert amlnet_entry.communication_eligibility == CommunicationEligibility.INTERNAL_ONLY
        assert amlnet_entry.mandatory_caveat is not None
        assert "structuring rule separability" in amlnet_entry.mandatory_caveat.lower()
        assert "near_thresh" in amlnet_entry.mandatory_caveat.lower()

        # Check raw results artifact notes structuring separability
        amlnet_path = REPO_ROOT / "experiments" / "amlnet" / "results.json"
        with open(amlnet_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("status") == "EXPERIMENTAL"
        assert "structuring" in data["dataset"].get("synthetic_separability_note", "").lower()

    def test_adversarial_attack_c_amlsim_smoke_run_quarantine(self):
        """Attack C: AMLSim canonical registry points to 1.32M canonical run, NOT 500-sample smoke run."""
        amlsim_entry = CANONICAL_REGISTRY["amlsim_canonical"]
        assert amlsim_entry.status == ArtifactStatus.CANONICAL
        assert amlsim_entry.canonical_artifact_relpath == "experiments/amlsim/results.json"
        assert "benchmarks/results/raw/fraud_benchmark_amlsim.json" in amlsim_entry.historical_artifacts_relpaths

        # Verify historical 500-sample smoke run is strictly quarantined
        smoke_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "fraud_benchmark_amlsim.json"
        with open(smoke_path, encoding="utf-8") as f:
            smoke_data = json.load(f)
        assert smoke_data.get("is_canonical", False) is False
        assert smoke_data.get("status") == "HISTORICAL"
        assert smoke_data.get("dataset_metrics", {}).get("total_transactions") == 500

    def test_adversarial_attack_d_amlsim_physical_and_logical_hash_isolation(self):
        """Attack D: AMLSim records physical parquet SHA-256 and isolates logical config hash."""
        amlsim_path = REPO_ROOT / "experiments" / "amlsim" / "results.json"
        with open(amlsim_path, encoding="utf-8") as f:
            data = json.load(f)
        ds = data["dataset"]
        expected_physical = "b3dc9b72f985e7247198f81df8d4db7c559d93dfd7d00fb4ec18a6b0b368647c"
        assert ds["physical_source_sha256"] == expected_physical
        assert "logical_configuration_hash" in ds
        assert ds["logical_configuration_hash"] != ds["physical_source_sha256"]
        assert ds["total_samples"] == 1323234
        assert ds["fraud_samples"] == 1719

    def test_adversarial_attack_e_ieee_cis_transactiondt_leakage_prevented(self):
        """Attack E: TransactionDT is strictly purged from predictive features and feature_names."""
        import pandas as pd
        from backend.app.application.services.dataloader import _process_ieee_cis_dataframe

        # Create dummy df with TransactionDT and isFraud
        df = pd.DataFrame({
            "TransactionDT": [1000, 2000, 3000],
            "isFraud": [0, 1, 0],
            "V1": [1.0, 2.0, 3.0],
            "TransactionAmt": [50.0, 100.0, 150.0],
        })
        processed = _process_ieee_cis_dataframe(df, "train")
        assert "TransactionDT" not in processed["feature_names"]
        assert processed["X"].shape[1] == len(processed["feature_names"])
        assert "transaction_dt" in processed  # Preserved strictly for chronological splitting

    def test_adversarial_attack_f_crossbank_scenario_7_support_transparency(self):
        """Attack F: Scenario 7 explicitly declares target_test_incidents=2 and simulated volume caveat."""
        cb_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "fraud_benchmark_crossbank.json"
        with open(cb_path, encoding="utf-8") as f:
            data = json.load(f)
        scen7 = data["scenarios"]["SCENARIO_7"]
        assert scen7["target_test_incidents"] == 2
        assert scen7["federated_detected_count"] == 2
        assert "2/2" in scen7["support_presentation"]
        assert "simulated" in scen7["volume_caveat"].lower()

    def test_adversarial_attack_g_reporting_surfaces_zero_target_metric_leakage(self):
        """Attack G: Engineering targets 0.8120 and 0.5890 must not be reported as measured numbers."""
        landing_path = REPO_ROOT / "frontend" / "src" / "pages" / "LandingPage.tsx"
        landing_text = landing_path.read_text(encoding="utf-8")
        assert "Centralized 0.8120" not in landing_text
        assert "baseline_centralized: 0.8120" not in landing_text

        router_path = REPO_ROOT / "backend" / "app" / "presentation" / "routers" / "dashboard.py"
        router_text = router_path.read_text(encoding="utf-8")
        assert "0.8120" not in router_text
        assert "0.5890" not in router_text

    def test_adversarial_attack_h_pydantic_schema_validators_reject_invalid_states(self):
        """Attack H: Pydantic models reject empty hashes, mismatched seeds, and NOT_EVALUATED metrics."""
        from benchmarks.provenance_schema import CanonicalBenchmarkArtifact, DatasetProvenance
        from pydantic import ValidationError

        # 1. Empty string SHA256 rejected for REAL_DATA
        with pytest.raises(ValidationError) as exc:
            DatasetProvenance(
                dataset_name="Test",
                dataset_type=DatasetProvenanceType.REAL_DATA,
                source_uri="backend/storage/datasets/test.csv",
                provenance_type=DatasetProvenanceType.REAL_DATA,
                evidence_scope=EvidenceScope.REAL_WORLD_BENCHMARK,
                communication_eligibility=CommunicationEligibility.SAFE,
                sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            )
        assert "Empty-string SHA-256" in str(exc.value)

        # 1b. Missing hash for REAL_DATA rejected
        with pytest.raises(ValidationError) as exc2:
            DatasetProvenance(
                dataset_name="Test",
                dataset_type=DatasetProvenanceType.REAL_DATA,
                source_uri="backend/storage/datasets/test.csv",
                provenance_type=DatasetProvenanceType.REAL_DATA,
                evidence_scope=EvidenceScope.REAL_WORLD_BENCHMARK,
                communication_eligibility=CommunicationEligibility.SAFE,
                sha256="",
            )
        assert "Physical dataset evidence required" in str(exc2.value)

        # 2. NOT_EVALUATED cannot contain measured performance
        prov = DatasetProvenance(
            dataset_name="Test Not Eval",
            dataset_type=DatasetProvenanceType.REAL_DATA,
            source_uri="backend/storage/datasets/test.csv",
            provenance_type=DatasetProvenanceType.REAL_DATA,
            evidence_scope=EvidenceScope.REAL_WORLD_BENCHMARK,
            communication_eligibility=CommunicationEligibility.INTERNAL_ONLY,
            sha256="1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        )
        with pytest.raises(ValidationError) as exc:
            CanonicalBenchmarkArtifact(
                benchmark_id="test_not_eval",
                status=ArtifactStatus.NOT_EVALUATED,
                provenance=prov,
                provenance_type=DatasetProvenanceType.REAL_DATA,
                evidence_scope=EvidenceScope.REAL_WORLD_BENCHMARK,
                communication_eligibility=CommunicationEligibility.INTERNAL_ONLY,
                aggregate_metrics={"pr_auc": {"mean": 0.85, "std": 0.01}},  # Invalid: must be empty
            )
        assert "cannot contain non-null measured performance" in str(exc.value)

    def test_adversarial_attack_i_resolve_canonical_artifact_rejects_historical(self):
        """Attack I: resolve_canonical_artifact rejects uncanonical or historical files."""
        from benchmarks.canonical_registry import CanonicalBenchmarkEntry, DatasetProvenanceType
        fake_entry = CanonicalBenchmarkEntry(
            benchmark_id="fake_historical",
            dataset_name="Fake Historical",
            provenance_type=DatasetProvenanceType.PROJECT_SYNTHETIC,
            status=ArtifactStatus.HISTORICAL,
            evidence_scope=EvidenceScope.PROJECT_SYNTHETIC_EXPERIMENT,
            communication_eligibility=CommunicationEligibility.INTERNAL_ONLY,
            canonical_artifact_relpath="benchmarks/results/raw/fraud_benchmark_ieee_cis.json",
        )
        orig = CANONICAL_REGISTRY.get("fake_historical")
        try:
            CANONICAL_REGISTRY["fake_historical"] = fake_entry
            with pytest.raises(ValueError, match="non-canonical status"):
                resolve_canonical_artifact("fake_historical")
        finally:
            if orig:
                CANONICAL_REGISTRY["fake_historical"] = orig
            else:
                CANONICAL_REGISTRY.pop("fake_historical", None)

    def test_adversarial_attack_j_master_matrix_zero_hardcoded_fallbacks(self):
        """Attack J: master_benchmark_matrix.json has dynamically derived metrics for all 8 datasets."""
        matrix_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "master_benchmark_matrix.json"
        with open(matrix_path, encoding="utf-8") as f:
            matrix = json.load(f)
        ds = matrix["datasets"]
        assert len(ds) == 8

        # Invariant 1: Un-evaluated paradigms must have None pr_auc
        for name, d in ds.items():
            paradigms = d.get("paradigms", {})
            for p_name, p_data in paradigms.items():
                status = p_data.get("status")
                pr_auc = p_data.get("pr_auc")
                if status in ("NOT_EVALUATED", "NOT_RUN"):
                    assert pr_auc is None

        # Invariant 2: Active benchmarks must have exact non-hardcoded measured metrics
        assert ds["paysim"]["paradigms"]["federated_fedavg"]["pr_auc"] is not None
        assert ds["credit_card"]["paradigms"]["federated_fedavg"]["pr_auc"] is not None
        assert ds["elliptic"]["paradigms"]["centralized_pooled"]["pr_auc"] is not None
        assert ds["amlsim"]["paradigms"]["federated_fedavg"]["pr_auc"] is not None
        assert ds["synthaml"]["paradigms"]["federated_fedavg"]["pr_auc"] is not None
        assert ds["amlnet"]["paradigms"]["federated_fedavg"]["pr_auc"] is not None
        assert ds["cross_bank"]["paradigms"]["federated_fedavg"]["pr_auc"] is not None

        # Invariant 3: IEEE-CIS must have evaluated canonical metrics
        assert ds["ieee_cis"]["paradigms"]["centralized_pooled"]["status"] == "EVALUATED"
        assert abs(ds["ieee_cis"]["paradigms"]["centralized_pooled"]["pr_auc"] - 0.4422) < 0.005
        assert ds["ieee_cis"]["paradigms"]["federated_fedavg"]["status"] == "EVALUATED"
        assert abs(ds["ieee_cis"]["paradigms"]["federated_fedavg"]["pr_auc"] - 0.3895) < 0.005

    def test_adversarial_original_attack_c_multiseed_empty_mismatched_rejection(self):
        """Original Attack C: Declaring multiple seeds with empty or count-mismatched per_seed_results must be rejected."""
        from benchmarks.provenance_schema import CanonicalBenchmarkArtifact, DatasetProvenance
        from pydantic import ValidationError

        prov = DatasetProvenance(
            dataset_name="PaySim MultiSeed Test",
            dataset_type=DatasetProvenanceType.EXTERNALLY_SIMULATED,
            source_uri="backend/storage/datasets/paysim/test.csv",
            provenance_type=DatasetProvenanceType.EXTERNALLY_SIMULATED,
            evidence_scope=EvidenceScope.EXTERNAL_SIMULATION_BENCHMARK,
            communication_eligibility=CommunicationEligibility.SAFE_WITH_CAVEAT,
            sha256="16910f90577b0d981bf8ff289714510bb89bc71bff7d3f220f024e287e4eea6b",
        )

        # 1. Multi-seed declared with empty per_seed_results
        with pytest.raises(ValidationError) as exc1:
            CanonicalBenchmarkArtifact(
                benchmark_id="paysim_empty_seeds",
                status=ArtifactStatus.CANONICAL,
                provenance=prov,
                provenance_type=DatasetProvenanceType.EXTERNALLY_SIMULATED,
                evidence_scope=EvidenceScope.EXTERNAL_SIMULATION_BENCHMARK,
                seeds=[42, 123, 456],
                per_seed_results=[],  # Adversarial attack: declared 3 seeds but zero results
            )
        assert "per_seed_results is empty" in str(exc1.value)

        # 2. Multi-seed declared with mismatched seed count (3 declared, 2 provided)
        with pytest.raises(ValidationError) as exc2:
            CanonicalBenchmarkArtifact(
                benchmark_id="paysim_count_mismatch",
                status=ArtifactStatus.CANONICAL,
                provenance=prov,
                provenance_type=DatasetProvenanceType.EXTERNALLY_SIMULATED,
                evidence_scope=EvidenceScope.EXTERNAL_SIMULATION_BENCHMARK,
                seeds=[42, 123, 456],
                per_seed_results=[
                    {"seed": 42, "fedavg_pr_auc": {"pr_auc": 0.9545}},
                    {"seed": 123, "fedavg_pr_auc": {"pr_auc": 0.9540}},
                ],  # Adversarial attack: only 2 entries for 3 declared seeds
            )
        assert "Multi-seed count mismatch" in str(exc2.value)

    def test_adversarial_original_attack_e_sample_standard_deviation_mismatch_rejection(self):
        """Original Attack E: Reported standard deviation deviating from recomputed sample std must be rejected."""
        from benchmarks.provenance_schema import CanonicalBenchmarkArtifact, DatasetProvenance
        from pydantic import ValidationError

        prov = DatasetProvenance(
            dataset_name="Credit Card Controlled Test",
            dataset_type=DatasetProvenanceType.REAL_DATA,
            source_uri="backend/storage/datasets/creditcard/creditcard.csv",
            provenance_type=DatasetProvenanceType.REAL_DATA,
            evidence_scope=EvidenceScope.REAL_WORLD_BENCHMARK,
            communication_eligibility=CommunicationEligibility.SAFE,
            sha256="9725f187a5342a690e82c5f118182fc60f64c6328ee6be1eb318c5e608de1bf3",
        )

        # True seeds: 0.8200, 0.8500, 0.8800 -> mean = 0.8500, sample std (ddof=1) = 0.0300
        # Adversarial attack: report std = 0.0050 to fabricate low variance
        with pytest.raises(ValidationError) as exc:
            CanonicalBenchmarkArtifact(
                benchmark_id="credit_card_fake_std",
                status=ArtifactStatus.CANONICAL,
                provenance=prov,
                provenance_type=DatasetProvenanceType.REAL_DATA,
                evidence_scope=EvidenceScope.REAL_WORLD_BENCHMARK,
                seeds=[42, 123, 456],
                per_seed_results=[
                    {"seed": 42, "pr_auc": 0.8200},
                    {"seed": 123, "pr_auc": 0.8500},
                    {"seed": 456, "pr_auc": 0.8800},
                ],
                aggregate_metrics={
                    "pr_auc": {"mean": 0.8500, "std": 0.0050}  # Fabricated std!
                },
            )
        assert "Mathematical validation failed" in str(exc.value)

    def test_reporting_surfaces_zero_fabricated_metrics_07850_and_05810(self):
        """Verify repository-wide that fabricated metrics 0.7850 and 0.5810 do not reach reporting surfaces."""
        surfaces = [
            REPO_ROOT / "backend" / "app" / "presentation" / "routers" / "dashboard.py",
            REPO_ROOT / "backend" / "app" / "application" / "schemas" / "dashboard.py",
            REPO_ROOT / "frontend" / "src" / "pages" / "LandingPage.tsx",
            REPO_ROOT / "README.md",
            REPO_ROOT / "docs" / "enterprise_benchmark_report.md",
            REPO_ROOT / "frontend" / "public" / "docs" / "enterprise_benchmark_report.md",
            REPO_ROOT / "benchmarks" / "results" / "raw" / "master_benchmark_matrix.json",
        ]
        for surface in surfaces:
            if surface.exists():
                text = surface.read_text(encoding="utf-8")
                assert "0.7850" not in text, f"Fabricated metric 0.7850 found in {surface.name}"
                assert "0.5810" not in text, f"Fabricated metric 0.5810 found in {surface.name}"

    def test_byzantine_atomic_scope_enforcement(self):
        """Verify Byzantine 99.7% retention cannot be claimed without explicit attack scope."""
        claim_path = REPO_ROOT / "benchmarks" / "claim_registry.json"
        with open(claim_path, encoding="utf-8") as f:
            data = json.load(f)
        byz_claim = next(c for c in data["claims"] if c["claim_id"] == "CLM-BYZ-TRIMMED")

        assert byz_claim["attack_type"] == "sign_inversion"
        assert byz_claim["malicious_fraction"] == 0.20
        assert byz_claim["client_count"] == 10
        assert byz_claim["malicious_client_count"] == 2
        assert "sign-inversion" in byz_claim["mandatory_scope"].lower()
        assert "atomic_claim_rendered" in byz_claim
        assert byz_claim.get("centralized_baseline_value") is None
        assert byz_claim["claim_classification"] == "REQUIRES_RE_EVALUATION"
        assert byz_claim["is_external_communication_safe"] is False

        # Ensure README rendering of 99.7% retention includes attack scope
        readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "99.7% of clean PR-AUC" in readme_text
        assert "under evaluated 20% sign-inversion attack" in readme_text


    def test_claims_registry_contains_non_iid_alpha_0_5(self):
        """Verify claim registry contains structured Non-IID Dirichlet alpha=0.5 claim."""
        claim_path = REPO_ROOT / "benchmarks" / "claim_registry.json"
        with open(claim_path, encoding="utf-8") as f:
            data = json.load(f)
        noniid = next((c for c in data["claims"] if c["claim_id"] == "CLM-FL-NONIID-ALPHA05"), None)
        assert noniid is not None, "CLM-FL-NONIID-ALPHA05 missing from claim_registry.json"
        assert noniid["stated_value"] == 0.2331
        assert noniid["empirical_measured_value"] == 0.2331
        assert noniid["algorithms_evaluated"]["fedavg"] == 0.2331
        assert noniid["algorithms_evaluated"]["fedprox"] == 0.2285
        assert noniid["algorithms_evaluated"]["scaffold"] == 0.2196
        assert noniid["dirichlet_alpha"] == 0.5


class TestIEEECISScientificSemanticsInvariants:
    """Invariant 13: Strict scientific semantics, non-causal epistemic framing, and evidence immutability."""

    def test_semantic_1_no_unsupported_temporal_drift_causal_assertions(self):
        """Test 1: IEEE wording must not assert that temporal drift caused the performance deficit."""
        surfaces = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "benchmarks" / "results" / "summary.md",
            REPO_ROOT / "docs" / "real_world_benchmarks.md",
            REPO_ROOT / "docs" / "enterprise_benchmark_report.md",
            REPO_ROOT / "benchmarks" / "claim_registry.json",
        ]
        banned_phrases = [
            "temporal concept drift caused",
            "concept drift caused the performance drop",
            "out-of-time evaluation caused the deficit",
            "chronological evaluation introduces severe concept drift",
            "concept drift explains the lower",
        ]
        for surface in surfaces:
            text = surface.read_text(encoding="utf-8").lower()
            for phrase in banned_phrases:
                assert phrase not in text, f"Unsupported causal phrase '{phrase}' found in {surface.name}"

    def test_semantic_2_no_unsupported_non_iid_causal_assertions(self):
        """Test 2: IEEE wording must not assert that non-IID heterogeneity caused the FedAvg gap."""
        surfaces = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "benchmarks" / "results" / "summary.md",
            REPO_ROOT / "docs" / "real_world_benchmarks.md",
            REPO_ROOT / "docs" / "enterprise_benchmark_report.md",
            REPO_ROOT / "benchmarks" / "claim_registry.json",
        ]
        banned_phrases = [
            "dirichlet skew caused the fedavg deficit",
            "client heterogeneity caused the 0.0527 gap",
            "label skew explains the federation gap",
            "non-iid partitioning causes model divergence",
        ]
        for surface in surfaces:
            text = surface.read_text(encoding="utf-8").lower()
            for phrase in banned_phrases:
                assert phrase not in text, f"Unsupported causal phrase '{phrase}' found in {surface.name}"

    def test_semantic_3_no_zero_bits_leakage_claim(self):
        """Test 3: No IEEE reporting surface may claim literal '0 bits' or 'zero bits' of leakage."""
        surfaces = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "benchmarks" / "results" / "summary.md",
            REPO_ROOT / "docs" / "real_world_benchmarks.md",
            REPO_ROOT / "docs" / "enterprise_benchmark_report.md",
        ]
        for surface in surfaces:
            text = surface.read_text(encoding="utf-8").lower()
            assert "0 bits leaked" not in text, f"Literal '0 bits leaked' claim found in {surface.name}"
            assert "zero bits leaked" not in text, f"Literal 'zero bits leaked' claim found in {surface.name}"

    def test_semantic_4_fp_budget_not_labeled_as_total_alerts(self):
        """Test 4: False positive budget (~114) must not be labeled as total alerts (which equals TP + FP ~917)."""
        claim_path = REPO_ROOT / "benchmarks" / "claim_registry.json"
        with open(claim_path, encoding="utf-8") as f:
            data = json.load(f)
        claim = next(c for c in data["claims"] if c["claim_id"] == "CLM-IEEE-RECALL-FPR")
        assert claim.get("approx_fp") == 114
        assert claim.get("approx_tp") == 803
        assert claim.get("approx_total_alerts") == 917
        assert "114 total alerts" not in claim.get("notes", "").lower()

    def test_semantic_5_federation_described_as_simulated_partition(self):
        """Test 5: IEEE federation must be described as simulated client partitioning, not three real banks."""
        claim_path = REPO_ROOT / "benchmarks" / "claim_registry.json"
        with open(claim_path, encoding="utf-8") as f:
            data = json.load(f)
        claim = next(c for c in data["claims"] if c["claim_id"] == "CLM-IEEE-FED-PRAUC")
        caveat = claim.get("mandatory_caveat", "").lower()
        assert "simulated" in caveat
        assert "competition data" in caveat

    def test_semantic_6_historical_synthetic_smoke_values_retained_as_non_comparable(self):
        """Test 6: Historical 0.7811 / 0.7554 values must retain synthetic/historical classification."""
        claim_path = REPO_ROOT / "benchmarks" / "claim_registry.json"
        with open(claim_path, encoding="utf-8") as f:
            data = json.load(f)
        claim = next(c for c in data["claims"] if c["claim_id"] == "CLM-IEEE-FED-PRAUC")
        legacy = claim.get("legacy_artifact", {})
        assert legacy.get("centralized_pr_auc") == 0.7811
        assert legacy.get("fedavg_pr_auc") == 0.7554
        assert "synthetic" in legacy.get("status", "").lower()

    def test_semantic_7_engineering_targets_remain_separated_from_measured(self):
        """Test 7: 0.8120 / 0.5890 must remain engineering targets, never measured canonical values."""
        claim_path = REPO_ROOT / "benchmarks" / "claim_registry.json"
        with open(claim_path, encoding="utf-8") as f:
            data = json.load(f)
        c_prauc = next(c for c in data["claims"] if c["claim_id"] == "CLM-IEEE-FED-PRAUC")
        c_recall = next(c for c in data["claims"] if c["claim_id"] == "CLM-IEEE-RECALL-FPR")

        assert c_prauc["stated_value"] == 0.812
        assert c_prauc["empirical_measured_value"] == 0.389172
        assert c_prauc["claim_classification"] == "VERIFIED_MEASURED"

        assert c_recall["stated_value"] == 0.589
        assert c_recall["empirical_measured_value"] == 0.196194
        assert c_recall["claim_classification"] == "VERIFIED_MEASURED"

    def test_semantic_8_primary_metric_resolves_to_average_precision(self):
        """Test 8: Primary IEEE metric semantics must explicitly resolve to sklearn average_precision_score."""
        claim_path = REPO_ROOT / "benchmarks" / "claim_registry.json"
        with open(claim_path, encoding="utf-8") as f:
            data = json.load(f)
        c_prauc = next(c for c in data["claims"] if c["claim_id"] == "CLM-IEEE-FED-PRAUC")
        assert "average_precision_score" in c_prauc.get("metric_definition", "")

    def test_semantic_9_canonical_artifact_immutability(self):
        """Test 9: Canonical IEEE measured values and disk SHA-256 must remain unchanged."""
        import hashlib
        artifact_path = REPO_ROOT / "experiments" / "ieee_cis" / "canonical_results.json"
        assert artifact_path.exists()
        raw_bytes = artifact_path.read_bytes()
        # Normalize CRLF to LF to ensure cross-platform hash determinism across Windows and Linux CI checkouts
        normalized_bytes = raw_bytes.replace(b"\r\n", b"\n")
        expected_sha = "1fcf344adc8251ec59f983c0eecc72eafb8d4ffde0ce4d76ee2ad0604360d0a6"
        actual_sha = hashlib.sha256(normalized_bytes).hexdigest()
        assert actual_sha == expected_sha, f"Canonical artifact SHA changed: {actual_sha} != {expected_sha}"

        data = json.loads(raw_bytes.decode("utf-8"))
        agg = data.get("aggregate") or data.get("aggregate_metrics")
        assert pytest.approx(agg["centralized_pr_auc"]["mean"], abs=1e-5) == 0.439861
        assert pytest.approx(agg["fedavg_pr_auc"]["mean"], abs=1e-5) == 0.389172
        assert pytest.approx(agg["delta_pr_auc"]["mean"], abs=1e-5) == -0.050689
        assert pytest.approx(agg["centralized_roc_auc"]["mean"], abs=1e-5) == 0.849038
        assert pytest.approx(agg["fedavg_roc_auc"]["mean"], abs=1e-5) == 0.835596
        assert pytest.approx(agg["centralized_recall_at_01_fpr"]["mean"], abs=1e-5) == 0.199967
        assert pytest.approx(agg["fedavg_recall_at_01_fpr"]["mean"], abs=1e-5) == 0.196194

    def test_semantic_10_canonical_registry_resolution_and_immutability(self):
        """Test 10: Future experiment metadata cannot silently overwrite canonical evidence."""
        entry = CANONICAL_REGISTRY["ieee_cis_real"]
        assert entry.status == ArtifactStatus.CANONICAL
        assert entry.canonical_artifact_relpath == "experiments/ieee_cis/canonical_results.json"
        assert entry.communication_eligibility == CommunicationEligibility.SAFE_WITH_CAVEAT

        # Both ieee_cis_real and ieee_cis_canonical resolve identically
        d1 = resolve_canonical_artifact("ieee_cis_real")
        d2 = resolve_canonical_artifact("ieee_cis_canonical")
        assert d1 is not None and d2 is not None
        assert d1["benchmark_id"] == d2["benchmark_id"] == "ieee_cis_canonical"


class TestCrossBankV2CanonicalEvidenceBinding:
    """Rigorous scientific-invariant test suite enforcing CrossBank v2 Level 1 raw binding."""

    def test_crossbank_v2_artifact_identity_and_seeds(self):
        """CrossBank v2 artifact identity, byte count, and exact canonical seeds."""
        import hashlib
        artifact_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "crossbank_v2_canonical.json"
        assert artifact_path.exists()
        raw_bytes = artifact_path.read_bytes()
        # Normalize CRLF to LF to ensure cross-platform hash and byte length determinism across Windows and Linux CI checkouts
        normalized_bytes = raw_bytes.replace(b"\r\n", b"\n")
        assert len(normalized_bytes) == 313965
        assert len(raw_bytes) in (313965, 322468)
        assert hashlib.sha256(normalized_bytes).hexdigest() == "81e3b39dabfeda0e92012f14d652ddce2e4edc2fb14ca16dd8b94391e2b1c4f6"

        data = json.loads(normalized_bytes.decode("utf-8"))
        assert data["canonical_seeds"] == [42, 123, 456, 789, 2025]
        assert 101112 not in data["canonical_seeds"]
        assert data["protocol_version"] == "2.1.0"
        assert data["execution_commit_sha"] == "2f64a02b65caa0b35588ec8c016290d1f2ac6e61"

    def test_crossbank_v2_condition_and_metric_reconciliation(self):
        """CrossBank v2 condition metrics mechanically match exact Level 1 raw values and registry."""
        import math
        artifact_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "crossbank_v2_canonical.json"
        with open(artifact_path, encoding="utf-8") as f:
            data = json.load(f)

        seeds = data["canonical_seeds"]
        per_seed = data["per_seed_results"]
        agg = data["aggregate_results"]

        def _mean_std(vals: list[float]) -> tuple[float, float]:
            n = len(vals)
            m = sum(vals) / n
            s = math.sqrt(sum((x - m) ** 2 for x in vals) / (n - 1)) if n > 1 else 0.0
            return m, s

        # Independently reconstruct all 6 conditions from per-seed results
        for cond in data["condition_ids"]:
            ap_vals = [per_seed[str(s)]["conditions"][cond]["overall_metrics"]["average_precision"] for s in seeds]
            roc_vals = [per_seed[str(s)]["conditions"][cond]["overall_metrics"]["roc_auc"] for s in seeds]
            m_ap, s_ap = _mean_std(ap_vals)
            m_roc, s_roc = _mean_std(roc_vals)

            validate_per_seed_aggregate(ap_vals, agg[cond]["average_precision"]["mean"], agg[cond]["average_precision"]["std"], label=f"{cond} AP", ddof=1)
            validate_per_seed_aggregate(roc_vals, agg[cond]["roc_auc"]["mean"], agg[cond]["roc_auc"]["std"], label=f"{cond} ROC", ddof=1)

            assert pytest.approx(m_ap, abs=1e-4) == agg[cond]["average_precision"]["mean"]
            assert pytest.approx(s_ap, abs=1e-4) == agg[cond]["average_precision"]["std"]
            assert pytest.approx(m_roc, abs=1e-4) == agg[cond]["roc_auc"]["mean"]
            assert pytest.approx(s_roc, abs=1e-4) == agg[cond]["roc_auc"]["std"]

        # Mechanical check of key canonical estimands
        fed_m_ap, _ = _mean_std([per_seed[str(s)]["conditions"]["COND_FEDERATED_FEDAVG_LOCAL_FEATS"]["overall_metrics"]["average_precision"] for s in seeds])
        cen_m_ap, _ = _mean_std([per_seed[str(s)]["conditions"]["COND_CENTRALIZED_POOLED"]["overall_metrics"]["average_precision"] for s in seeds])
        iso_m_ap, _ = _mean_std([per_seed[str(s)]["conditions"]["COND_LOCAL_ISOLATED"]["overall_metrics"]["average_precision"] for s in seeds])
        oracle_m_ap, _ = _mean_std([per_seed[str(s)]["conditions"]["COND_FEDERATED_CONSORTIUM_SIGNAL"]["overall_metrics"]["average_precision"] for s in seeds])

        assert pytest.approx(fed_m_ap, abs=1e-4) == 0.1779
        assert pytest.approx(cen_m_ap, abs=1e-4) == 0.1454
        assert pytest.approx(iso_m_ap, abs=1e-4) == 0.1656
        assert pytest.approx(oracle_m_ap, abs=1e-4) == 0.8140  # Rejects stale 0.4437

        # Paired Q2 & Q3 deltas
        q2_ap = [per_seed[str(s)]["conditions"]["COND_FEDERATED_FEDAVG_LOCAL_FEATS"]["overall_metrics"]["average_precision"] -
                 per_seed[str(s)]["conditions"]["COND_LOCAL_ISOLATED"]["overall_metrics"]["average_precision"] for s in seeds]
        m_q2_ap, _ = _mean_std(q2_ap)
        assert pytest.approx(m_q2_ap, abs=1e-4) == 0.0123

        # Registry synchronization check
        reg_path = REPO_ROOT / "benchmarks" / "results" / "canonical_evidence_registry.json"
        if reg_path.exists():
            with open(reg_path, encoding="utf-8") as f:
                reg_data = json.load(f)
            cb_claim = next((b for b in reg_data.get("benchmarks", []) if b.get("claim_id") == "CLM-CROSSBANK-V2-CANONICAL"), None)
            assert cb_claim is not None
            assert cb_claim["seeds"] == seeds
            assert pytest.approx(cb_claim["value"]["fedavg_local_ap_mean"], abs=1e-4) == fed_m_ap
            assert pytest.approx(cb_claim["value"]["consortium_oracle_ap_mean"], abs=1e-4) == oracle_m_ap


    def test_crossbank_v2_scenario_7_and_bank_c_invariants(self):
        """CrossBank v2 Scenario 7 negative result and Bank C zero-positive training invariant."""
        artifact_path = REPO_ROOT / "benchmarks" / "results" / "raw" / "crossbank_v2_canonical.json"
        with open(artifact_path, encoding="utf-8") as f:
            data = json.load(f)

        per_seed = data["per_seed_results"]
        expected_seeds = [42, 123, 456, 789, 2025]

        # Invariant 1: Bank C training fraud is strictly 0 across all 5 seeds
        for s in expected_seeds:
            split = per_seed[str(s)]["split_summary"]
            assert split["bank_c_train_pos"] == 0
            assert split["bank_c_scenario7_train_pos"] == 0

        # Invariant 2: Bank C low-FPR operational detection is 0 across all 5 seeds
        for s in expected_seeds:
            bc = per_seed[str(s)]["conditions"]["COND_FEDERATED_FEDAVG_LOCAL_FEATS"]["per_bank_metrics"]["bank_c"]
            assert bc["confusion_matrix"]["tp"] == 0
            assert bc["recall_at_validation_fpr"] == 0.0

        # Invariant 3: Scenario 7 realistic detection is exactly 5/133 (3.76% recall)
        total_s7 = 0
        detected_s7 = 0
        for s in expected_seeds:
            s7 = per_seed[str(s)]["conditions"]["COND_FEDERATED_FEDAVG_LOCAL_FEATS"]["scenario_metrics"]["SCENARIO_7"]
            total_s7 += s7["test_incident_count"]
            detected_s7 += s7["detected_incident_count"]

        assert total_s7 == 133
        assert detected_s7 == 5
        assert pytest.approx(detected_s7 / total_s7 * 100, abs=0.01) == 3.76
