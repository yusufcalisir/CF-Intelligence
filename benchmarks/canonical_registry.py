"""Canonical Benchmark Evidence Registry.

Provides the single, authoritative, machine-readable registry linking benchmark IDs
to their canonical Level 1 disk artifacts, provenance classifications, and lifecycle statuses.
Prevents stale, synthetic, failed, or placeholder files from being promoted as canonical evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.provenance_schema import (
    ArtifactStatus,
    CommunicationEligibility,
    DatasetProvenanceType,
    EvidenceScope,
    verify_physical_source_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CanonicalBenchmarkEntry:
    """Metadata specification for a registered benchmark entry."""
    benchmark_id: str
    dataset_name: str
    provenance_type: DatasetProvenanceType
    status: ArtifactStatus
    evidence_scope: EvidenceScope
    communication_eligibility: CommunicationEligibility
    canonical_artifact_relpath: str | None
    historical_artifacts_relpaths: list[str] = field(default_factory=list)
    description: str = ""
    mandatory_caveat: str | None = None
    is_external_communication_safe: bool = False


# The Master Authoritative Registry of Canonical Evidence
CANONICAL_REGISTRY: dict[str, CanonicalBenchmarkEntry] = {
    "paysim_canonical": CanonicalBenchmarkEntry(
        benchmark_id="paysim_canonical",
        dataset_name="PaySim Mobile Money Fraud",
        provenance_type=DatasetProvenanceType.EXTERNALLY_SIMULATED,
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.EXTERNAL_SIMULATION_BENCHMARK,
        communication_eligibility=CommunicationEligibility.SAFE_WITH_CAVEAT,
        canonical_artifact_relpath="experiments/paysim/canonical_results.json",
        historical_artifacts_relpaths=[
            "benchmarks/results/raw/fraud_benchmark_paysim.json",
            "experiments/paysim/results.json",
        ],
        description="Canonical multi-seed benchmark on 10% systematic sample of physical PaySim CSV (636k rows, 817 fraud).",
        mandatory_caveat="PaySim is an agent-based financial simulation (Lopez-Rojas et al.), not raw banking PII.",
        is_external_communication_safe=True,
    ),
    "credit_card_canonical": CanonicalBenchmarkEntry(
        benchmark_id="credit_card_canonical",
        dataset_name="European Credit Card Fraud Detection",
        provenance_type=DatasetProvenanceType.REAL_DATA,
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.REAL_WORLD_BENCHMARK,
        communication_eligibility=CommunicationEligibility.SAFE,
        canonical_artifact_relpath="experiments/credit_card/multi_seed_controlled_results.json",
        historical_artifacts_relpaths=[
            "benchmarks/results/raw/fraud_benchmark_credit_card.json",
            "experiments/credit_card/results.json",
            "experiments/credit_card/legacy_unequal_budget/results_legacy_unequal_budget.json",
        ],
        description="Canonical multi-seed controlled budget-equalized benchmark on European Cardholders dataset (284k rows, 492 fraud).",
        mandatory_caveat="Evaluates extreme class imbalance skew across 3 banks; Bank C achieves collaborative rescue from near-zero fraud.",
        is_external_communication_safe=True,
    ),
    "elliptic_canonical": CanonicalBenchmarkEntry(
        benchmark_id="elliptic_canonical",
        dataset_name="Elliptic Bitcoin AML Graph",
        provenance_type=DatasetProvenanceType.REAL_DATA,
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.REAL_WORLD_BENCHMARK,
        communication_eligibility=CommunicationEligibility.SAFE_WITH_CAVEAT,
        canonical_artifact_relpath="benchmarks/results/raw/graphsage_elliptic_benchmark.json",
        historical_artifacts_relpaths=[
            "experiments/elliptic/synthetic/graphsage_elliptic_benchmark.json",
        ],
        description="Canonical inductive GraphSAGE benchmark on real Elliptic graph (203k nodes, 234k edges) with strict out-of-time temporal split.",
        mandatory_caveat="Graph modeling exhibits an architectural negative result on out-of-time test sets: Tabular MLP (0.5778) outperforms GraphSAGE (0.3761). Federated partition is NOT_EVALUATED.",
        is_external_communication_safe=True,
    ),
    "ieee_cis_real": CanonicalBenchmarkEntry(
        benchmark_id="ieee_cis_real",
        dataset_name="IEEE-CIS Fraud Detection (Real 590k)",
        provenance_type=DatasetProvenanceType.REAL_DATA,
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.REAL_WORLD_BENCHMARK,
        communication_eligibility=CommunicationEligibility.SAFE_WITH_CAVEAT,
        canonical_artifact_relpath="experiments/ieee_cis/canonical_results.json",
        historical_artifacts_relpaths=[
            "benchmarks/results/raw/fraud_benchmark_ieee_cis.json",
            "experiments/ieee_cis/results.json",
        ],
        description="Real Kaggle IEEE-CIS/Vesta dataset (590,540 transactions, 434 merged columns, 421 numeric features) evaluated on out-of-time chronological holdout (80/20) across 3 simulated bank clients under Dirichlet alpha=0.5 skew across seeds [42, 123, 456].",
        mandatory_caveat="Evaluated on 590,540 real IEEE-CIS transactions partitioned across 3 simulated bank clients under Dirichlet alpha=0.5 non-IID label skew. Evaluates standard FedAvg on a public competition dataset; does NOT represent a live multi-bank institutional deployment.",
        is_external_communication_safe=True,
    ),
    "amlsim_canonical": CanonicalBenchmarkEntry(
        benchmark_id="amlsim_canonical",
        dataset_name="IBM AMLSim Multi-Hop Banking Graph",
        provenance_type=DatasetProvenanceType.EXTERNALLY_SIMULATED,
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.EXTERNAL_SIMULATION_BENCHMARK,
        communication_eligibility=CommunicationEligibility.SAFE_WITH_CAVEAT,
        canonical_artifact_relpath="experiments/amlsim/results.json",
        historical_artifacts_relpaths=[
            "benchmarks/results/raw/fraud_benchmark_amlsim.json",
        ],
        description="IBM agent-based multi-hop transaction graph simulation (1.32M transactions, 1,719 fraud, GraphSAGE PR-AUC 0.6527).",
        mandatory_caveat="Agent-based multi-hop graph simulation. Evaluates Inductive GraphSAGE against tabular baselines on cycle and fan-in laundering structures.",
        is_external_communication_safe=True,
    ),
    "synthaml_synthetic": CanonicalBenchmarkEntry(
        benchmark_id="synthaml_synthetic",
        dataset_name="Danish Spar Nord Bank SynthAML",
        provenance_type=DatasetProvenanceType.PROJECT_SYNTHETIC,
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.PROJECT_SYNTHETIC_EXPERIMENT,
        communication_eligibility=CommunicationEligibility.INTERNAL_ONLY,
        canonical_artifact_relpath="benchmarks/results/raw/fraud_benchmark_synthaml.json",
        historical_artifacts_relpaths=["experiments/synthaml/results.json"],
        description="Synthetic SAR alert classification benchmark modeled on Spar Nord Bank operational alert profiles (5,000 alerts).",
        mandatory_caveat="Project-generated synthetic benchmark (scripts/generate_synthaml_dataset.py). High classification performance (canonical PR-AUC 0.9924; historical Run A: 0.9985) reflects synthetic copula separability, not real bank SAR logs.",
        is_external_communication_safe=False,
    ),
    "amlnet_synthetic": CanonicalBenchmarkEntry(
        benchmark_id="amlnet_synthetic",
        dataset_name="AUSTRAC AMLNet Benchmark",
        provenance_type=DatasetProvenanceType.PROJECT_SYNTHETIC,
        status=ArtifactStatus.EXPERIMENTAL,
        evidence_scope=EvidenceScope.PROJECT_SYNTHETIC_EXPERIMENT,
        communication_eligibility=CommunicationEligibility.INTERNAL_ONLY,
        canonical_artifact_relpath="experiments/amlnet/results.json",
        historical_artifacts_relpaths=["benchmarks/results/raw/fraud_benchmark_amlnet.json"],
        description="Internal synthetic generation exhibiting trivial structuring rule separability; raw JSON has 500 rows with 0 positives.",
        mandatory_caveat="Project-generated synthetic benchmark (scripts/generate_amlnet_dataset.py) exhibiting trivial structuring rule separability (amounts in [8500, 9950] AUD directly exposed by near_thresh feature). Not suitable for external real-world performance claims.",
        is_external_communication_safe=False,
    ),
    "cross_bank_consortium": CanonicalBenchmarkEntry(
        benchmark_id="cross_bank_consortium",
        dataset_name="CFI-CrossBank-01 7-Topology Consortium",
        provenance_type=DatasetProvenanceType.PROJECT_SYNTHETIC,
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.CONTROLLED_EXPERIMENT,
        communication_eligibility=CommunicationEligibility.SAFE_WITH_CAVEAT,
        canonical_artifact_relpath="benchmarks/results/raw/fraud_benchmark_crossbank.json",
        historical_artifacts_relpaths=["experiments/cross_bank/results.json"],
        description="Architectural proof-of-concept on 1,807 synthetic multi-bank transactions demonstrating multi-hop cycle recovery and zero-positive transfer.",
        mandatory_caveat="Safe to describe externally ONLY as an internal synthetic architectural proof-of-concept; must not be cited as live financial recoveries. Scenario 7 evaluates exactly N=2 synthetic test incidents (100% detection = 2/2 synthetic incidents; $639,701.40 simulated averted volume).",
        is_external_communication_safe=True,
    ),
    "crossbank_v2_canonical": CanonicalBenchmarkEntry(
        benchmark_id="crossbank_v2_canonical",
        dataset_name="CFI-CrossBank-02 Consortium Fraud Benchmark (Protocol 2.1.0)",
        provenance_type=DatasetProvenanceType.PROJECT_SYNTHETIC,
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.CONTROLLED_EXPERIMENT,
        communication_eligibility=CommunicationEligibility.SAFE_WITH_CAVEAT,
        canonical_artifact_relpath="benchmarks/results/raw/crossbank_v2_canonical.json",
        historical_artifacts_relpaths=[
            "benchmarks/results/raw/fraud_benchmark_crossbank.json",
            "benchmarks/results/raw/consortium_flagship_benchmark.json",
        ],
        description="Canonical multi-seed neural benchmark under protocol 2.1.0 across 5 canonical seeds [42, 123, 456, 789, 2025], 6 conditions, and 7 scenarios across 3 banks.",
        mandatory_caveat="Project-synthetic data with simulated federation. Real-world generalizability NOT_EVALUATED. Scenario 7 exhibits cold-start low-FPR failure (near-zero detection at FPR=0.001: 3.76% pooled recall; 5/133 detected). Q3 comparison carries provenance caveat (central baseline trained on local features). Oracle condition represents diagnostic upper bound ablation.",
        is_external_communication_safe=True,
    ),
    "dp_privacy_utility": CanonicalBenchmarkEntry(
        benchmark_id="dp_privacy_utility",
        dataset_name="DP-SGD Opacus Privacy-Utility Tradeoff",
        provenance_type=DatasetProvenanceType.PROJECT_SYNTHETIC,
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.CONTROLLED_EXPERIMENT,
        communication_eligibility=CommunicationEligibility.SAFE_WITH_CAVEAT,
        canonical_artifact_relpath="benchmarks/results/raw/dp_privacy_utility_tradeoff.json",
        historical_artifacts_relpaths=["experiments/dp_evaluation/dp_sweep_results.json"],
        description="PyTorch Opacus DP-SGD neural sweep with PRV accountant across noise multipliers sigma=0.0 to 3.0 (N=20,000).",
        mandatory_caveat="Demonstrates privacy-utility floor on synthetic banking data: PR-AUC collapses from 0.8965 to 0.3465 at epsilon=0.3497.",
        is_external_communication_safe=True,
    ),
    "byzantine_sign_inversion": CanonicalBenchmarkEntry(
        benchmark_id="byzantine_sign_inversion",
        dataset_name="Byzantine Fault Tolerance Sign-Inversion Attack (Historical Prototype)",
        provenance_type=DatasetProvenanceType.CONTROLLED_TESTBED,
        status=ArtifactStatus.HISTORICAL,
        evidence_scope=EvidenceScope.CONTROLLED_EXPERIMENT,
        communication_eligibility=CommunicationEligibility.HISTORICAL_ONLY,
        canonical_artifact_relpath=None,
        historical_artifacts_relpaths=[
            "benchmarks/results/raw/byzantine_benchmark_sign_inversion.json",
            "benchmarks/results/raw/byzantine_breakdown_analysis.json",
        ],
        description="Historical prototype evaluating sign-inversion attack on synthetic 10-feature Gaussian testbed (10 clients, 2 Byzantine). Superseded by byzantine_federated_canonical.",
        mandatory_caveat="HISTORICAL EVIDENCE ONLY: Evaluates algebraic single-seed Gaussian proxy with centroid leakage. Does not constitute valid canonical FL Byzantine robustness evidence. Re-evaluation pending under byzantine_federated_canonical.",
        is_external_communication_safe=False,
    ),
    "byzantine_federated_canonical": CanonicalBenchmarkEntry(
        benchmark_id="byzantine_federated_canonical",
        dataset_name="European Credit Card Fraud Detection (Byzantine Federated Robustness)",
        provenance_type=DatasetProvenanceType.REAL_DATA,
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.CONTROLLED_EXPERIMENT,
        communication_eligibility=CommunicationEligibility.SAFE_WITH_CAVEAT,
        canonical_artifact_relpath="benchmarks/results/raw/byzantine_federated_canonical.json",
        historical_artifacts_relpaths=[
            "benchmarks/results/raw/byzantine_benchmark_sign_inversion.json",
            "benchmarks/results/raw/byzantine_breakdown_analysis.json",
        ],
        description="Canonical multi-seed multi-round federated adversarial benchmark (n=12, f=2, 3 seeds: 42, 123, 456, 72 conditions) evaluating FedAvg, Coordinate Median, Trimmed Mean, Krum, Multi-Krum, and Bulyan under Scaled Sign Inversion (scale=3.0), Isotropic Gaussian Noise (sigma=1.0), and Omniscient ALIE (z=1.0) on real Credit Card Fraud data under Dirichlet non-IID partitions (alpha=0.5).",
        mandatory_caveat="Controlled laboratory federated learning simulation on real Credit Card Fraud transactions; client federation (12 nodes, Dirichlet alpha=0.5) and Byzantine adversaries (f=2) are simulated and do NOT represent live production banking rails. PROVENANCE CAVEAT: Machine-readable execution config hash (f3c89626...) diverged from definition hash (d0640c8e...) due to CLI metadata builder incompleteness; scientific intent and execution match 100% field-by-field.",
        is_external_communication_safe=True,
    ),

    "fl_non_iid_alpha_0_5": CanonicalBenchmarkEntry(
        benchmark_id="fl_non_iid_alpha_0_5",
        dataset_name="Non-IID Label Skew Benchmark (Dirichlet alpha=0.5)",
        provenance_type=DatasetProvenanceType.CONTROLLED_TESTBED,
        status=ArtifactStatus.CANONICAL,
        evidence_scope=EvidenceScope.CONTROLLED_EXPERIMENT,
        communication_eligibility=CommunicationEligibility.SAFE_WITH_CAVEAT,
        canonical_artifact_relpath="benchmarks/results/raw/fl_comparison_alpha_0.5.json",
        historical_artifacts_relpaths=[
            "benchmarks/results/raw/fl_comparison_alpha_0.1.json",
            "benchmarks/results/raw/fl_comparison_alpha_1.0.json",
        ],
        description="Multi-optimizer comparison under severe Dirichlet alpha=0.5 class imbalance skew (FedAvg: 0.2331, FedProx: 0.2285, SCAFFOLD: 0.2196).",
        mandatory_caveat="Measured on controlled local testbed. Earlier reported numbers (0.0757 / 0.0548) were from an uncommitted draft run.",
        is_external_communication_safe=True,
    ),
}

# Alias canonical ID to match both ieee_cis_real and ieee_cis_canonical
CANONICAL_REGISTRY["ieee_cis_canonical"] = CANONICAL_REGISTRY["ieee_cis_real"]


def get_canonical_registry() -> dict[str, CanonicalBenchmarkEntry]:
    """Retrieve the master canonical benchmark registry."""
    return CANONICAL_REGISTRY


def resolve_canonical_artifact(
    benchmark_id: str,
    validate_physical_hash: bool = False,
) -> dict[str, Any] | None:
    """Resolve, load, and validate the canonical JSON artifact for a given benchmark ID.

    Performs rigorous verification:
    1. Checks registry existence and lifecycle status.
    2. Verifies physical file exists on disk.
    3. Validates artifact status invariants.
    4. Rejects historical, placeholder, superseded, or smoke-test artifacts as canonical evidence.
    5. Validates physical dataset hash when requested.

    Returns None if the benchmark is NOT_EVALUATED or has no canonical artifact.
    Raises ValueError, FileNotFoundError, or ValidationError on any scientific integrity failure.
    """
    if benchmark_id not in CANONICAL_REGISTRY:
        raise KeyError(f"Benchmark '{benchmark_id}' not found in canonical registry.")

    entry = CANONICAL_REGISTRY[benchmark_id]
    if entry.status in (
        ArtifactStatus.SUPERSEDED,
        ArtifactStatus.SUPERSEDED_PLACEHOLDER,
        ArtifactStatus.HISTORICAL,
        ArtifactStatus.SMOKE_TEST,
        ArtifactStatus.PLACEHOLDER,
    ):
        raise ValueError(
            f"Cannot resolve canonical artifact: Benchmark '{benchmark_id}' has non-canonical status '{entry.status}'."
        )

    if entry.status == ArtifactStatus.NOT_EVALUATED or not entry.canonical_artifact_relpath:
        return None


    artifact_path = REPO_ROOT / entry.canonical_artifact_relpath
    if not artifact_path.exists():
        raise FileNotFoundError(f"Canonical artifact for '{benchmark_id}' missing at: {artifact_path}")

    with open(artifact_path, encoding="utf-8") as f:
        data = json.load(f)

    # Invariant: Verify artifact status is CANONICAL or EXPERIMENTAL (not placeholder or smoke test)
    artifact_status = data.get("status")
    if (
        artifact_status is not None
        and entry.status == ArtifactStatus.CANONICAL
        and artifact_status not in (
            ArtifactStatus.CANONICAL.value,
            "CANONICAL",
        )
    ):
        raise ValueError(
            f"Canonical resolution failed for '{benchmark_id}': artifact records status '{artifact_status}', "
            f"expected '{ArtifactStatus.CANONICAL}'."
        )

    # Invariant: Verify physical dataset hash if requested and available
    if validate_physical_hash:
        ds = data.get("dataset")
        if not isinstance(ds, dict):
            ds = data.get("dataset_metadata") if isinstance(data.get("dataset_metadata"), dict) else {}
        source_uri = ds.get("source_uri")
        expected_hash = (
            ds.get("physical_source_sha256")
            or ds.get("sha256")
            or ds.get("sha256_hash")
            or ds.get("sha256_hashes")
        )
        if source_uri and expected_hash:
            src_path = REPO_ROOT / source_uri
            if isinstance(expected_hash, dict):
                for fname, fhash in expected_hash.items():
                    sub_file = src_path / fname if src_path.is_dir() else src_path
                    if sub_file.exists():
                        status, comp_hash = verify_physical_source_hash(sub_file, fhash)
                        if status == "HASH_MISMATCH":
                            raise ValueError(
                                f"Physical dataset hash verification failed for '{benchmark_id}' ({fname}): "
                                f"file {sub_file} computed {comp_hash} != recorded {fhash}"
                            )
            elif isinstance(expected_hash, str):
                status, comp_hash = verify_physical_source_hash(src_path, expected_hash)
                if status == "HASH_MISMATCH":
                    raise ValueError(
                        f"Physical dataset hash verification failed for '{benchmark_id}': "
                        f"file {src_path} computed {comp_hash} != recorded {expected_hash}"
                    )

    return data
