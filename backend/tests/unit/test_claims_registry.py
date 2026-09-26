"""Unit Tests for Authoritative Quantitative Metric Claim Registry.

Verifies schema validity, mathematical bounds, artifact provenance, and exact numerical
reconciliation between the claim registry and raw empirical benchmark output JSONs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = REPO_ROOT / "benchmarks" / "claim_registry.json"
RAW_RESULTS_DIR = REPO_ROOT / "benchmarks" / "results" / "raw"


@pytest.fixture
def claim_registry() -> dict[str, Any]:
    """Loads and returns the authoritative claim registry JSON."""
    assert REGISTRY_PATH.exists(), f"Claim registry file not found at {REGISTRY_PATH}"
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data


def test_claim_registry_json_structure(claim_registry: dict[str, Any]) -> None:
    """Verifies top-level schema contract and metadata invariants."""
    assert "schema_version" in claim_registry
    assert "registry_name" in claim_registry
    assert "claims" in claim_registry
    assert "summary_metrics" in claim_registry
    assert "invariants" in claim_registry

    invariants = claim_registry["invariants"]
    assert invariants.get("zero_metric_shopping") is True
    assert invariants.get("unfavorable_result_preservation") is True
    assert invariants.get("verifiable_raw_provenance") is True

    claims = claim_registry["claims"]
    assert len(claims) >= 15
    assert claim_registry["summary_metrics"]["total_claims"] == len(claims)


def test_claims_field_schema_and_types(claim_registry: dict[str, Any]) -> None:
    """Verifies that every registered claim conforms to mandatory types and ID naming."""
    claims = claim_registry["claims"]
    seen_ids = set()

    valid_classifications = {
        "DESIGN_TARGET_VS_LOCAL_RUN",
        "EMPIRICAL_PARITY_VERIFIED",
        "EMPIRICAL_SUPERIOR_VERIFIED",
        "VERIFIED_MEASURED",
    }

    for claim in claims:
        claim_id = claim.get("claim_id")
        assert claim_id, "Missing claim_id"
        assert claim_id.startswith("CLM-"), f"Invalid claim_id format: {claim_id}"
        assert claim_id not in seen_ids, f"Duplicate claim_id found: {claim_id}"
        seen_ids.add(claim_id)

        assert "category" in claim
        assert "title" in claim
        assert "stated_value" in claim
        assert isinstance(claim["stated_value"], (int, float))
        assert "empirical_measured_value" in claim
        assert isinstance(claim["empirical_measured_value"], (int, float))
        assert "verification_status" in claim
        assert "claim_classification" in claim
        assert claim["claim_classification"] in valid_classifications, (
            f"Unknown classification: {claim['claim_classification']}"
        )
        assert "notes" in claim and len(claim["notes"]) > 10


def test_mathematical_bounds_of_claims(claim_registry: dict[str, Any]) -> None:
    """Verifies mathematical validity of AUC metrics, percentages, and latencies."""
    for claim in claim_registry["claims"]:
        unit = claim.get("stated_unit", "")
        stated = claim["stated_value"]
        empirical = claim["empirical_measured_value"]

        # PR-AUC / ROC-AUC must lie within [0.0, 1.0]
        if "AUC" in unit:
            assert 0.0 <= stated <= 1.0, f"Out of bounds stated AUC: {stated} in {claim['claim_id']}"
            assert 0.0 <= empirical <= 1.0, f"Out of bounds empirical AUC: {empirical} in {claim['claim_id']}"

        # Recall at FPR must lie within [0.0, 1.0]
        if "Recall" in unit:
            assert 0.0 <= stated <= 1.0, f"Out of bounds stated recall: {stated} in {claim['claim_id']}"
            assert 0.0 <= empirical <= 1.0, f"Out of bounds empirical recall: {empirical} in {claim['claim_id']}"

        # Latencies, throughputs, seconds must be positive
        if any(kw in unit for kw in ["ms", "req/s", "seconds", "param"]):
            assert stated > 0, f"Non-positive stated value: {stated} in {claim['claim_id']}"
            assert empirical > 0, f"Non-positive empirical value: {empirical} in {claim['claim_id']}"


def test_raw_artifacts_exist_and_reconcile_with_registry(claim_registry: dict[str, Any]) -> None:
    """Ensures raw benchmark JSON files exist and exact metrics match registry records."""
    claims_by_id = {c["claim_id"]: c for c in claim_registry["claims"]}

    # 1. PaySim Raw Reconciliation
    paysim_file = RAW_RESULTS_DIR / "fraud_benchmark_paysim.json"
    assert paysim_file.exists(), f"Missing PaySim raw artifact: {paysim_file}"
    with open(paysim_file, encoding="utf-8") as f:
        paysim_raw = json.load(f)
    paysim_fed_pr_auc = paysim_raw["federated_fedavg"]["pr_auc"]
    assert pytest.approx(claims_by_id["CLM-PAYSIM-FED-PRAUC"]["empirical_measured_value"], abs=1e-4) == paysim_fed_pr_auc

    # 2. IEEE-CIS Raw Reconciliation
    ieee_file = RAW_RESULTS_DIR / "fraud_benchmark_ieee_cis.json"
    assert ieee_file.exists(), f"Missing IEEE-CIS raw artifact: {ieee_file}"
    with open(ieee_file, encoding="utf-8") as f:
        ieee_raw = json.load(f)
    ieee_fed_pr_auc = ieee_raw["federated_fedavg"]["pr_auc"]
    assert pytest.approx(claims_by_id["CLM-IEEE-FED-PRAUC"]["empirical_measured_value"], abs=1e-4) == ieee_fed_pr_auc

    # 3. Elliptic GraphSAGE Raw Reconciliation
    elliptic_file = RAW_RESULTS_DIR / "graphsage_elliptic_benchmark.json"
    assert elliptic_file.exists(), f"Missing Elliptic raw artifact: {elliptic_file}"
    with open(elliptic_file, encoding="utf-8") as f:
        elliptic_raw = json.load(f)
    elliptic_pr_auc = elliptic_raw["metrics"]["pr_auc"]
    assert pytest.approx(claims_by_id["CLM-ELLIPTIC-PRAUC"]["empirical_measured_value"], abs=1e-4) == elliptic_pr_auc

    # 4. Differential Privacy Raw Reconciliation
    dp_file = RAW_RESULTS_DIR / "dp_privacy_utility_tradeoff.json"
    assert dp_file.exists(), f"Missing DP raw artifact: {dp_file}"
    with open(dp_file, encoding="utf-8") as f:
        dp_raw = json.load(f)
    # sigma = 3.0 point is first
    assert pytest.approx(claims_by_id["CLM-DP-SIGMA30"]["empirical_measured_value"], abs=1e-4) == dp_raw["tradeoff_points"][0]["pr_auc"]
    # sigma = 0.0 point is last
    assert pytest.approx(claims_by_id["CLM-DP-SIGMA00"]["empirical_measured_value"], abs=1e-4) == dp_raw["tradeoff_points"][-1]["pr_auc"]

    # 5. Byzantine Resilience Raw Reconciliation
    byz_file = RAW_RESULTS_DIR / "byzantine_benchmark_sign_inversion.json"
    assert byz_file.exists(), f"Missing Byzantine raw artifact: {byz_file}"
    with open(byz_file, encoding="utf-8") as f:
        byz_raw = json.load(f)
    results = byz_raw["results"]
    assert pytest.approx(claims_by_id["CLM-BYZ-TRIMMED"]["empirical_measured_value"], abs=1e-4) == results["Trimmed Mean (20% Coordinate Trim)"]["pr_auc"]
    assert pytest.approx(claims_by_id["CLM-BYZ-KRUM"]["empirical_measured_value"], abs=1e-4) == results["Krum (Blanchard et al.)"]["pr_auc"]
    assert pytest.approx(claims_by_id["CLM-BYZ-BULYAN"]["empirical_measured_value"], abs=1e-4) == results["Bulyan (Guerraoui et al.)"]["pr_auc"]

    # 6. Concurrency & Latency Raw Reconciliation
    lat_file = RAW_RESULTS_DIR / "latency_concurrency_benchmark.json"
    assert lat_file.exists(), f"Missing Latency raw artifact: {lat_file}"
    with open(lat_file, encoding="utf-8") as f:
        lat_raw = json.load(f)
    fast_path_latency = lat_raw["single_request_breakdown"]["fast_path_raw"]["total_request_latency_ms"]
    assert pytest.approx(claims_by_id["CLM-LATENCY-FASTPATH"]["empirical_measured_value"], abs=1e-2) == fast_path_latency
    # C=100 throughput
    c100_data = next(item for item in lat_raw["concurrency_scaling"] if item["concurrency"] == 100)
    assert pytest.approx(claims_by_id["CLM-GATEWAY-PEAK-THROUGHPUT"]["empirical_measured_value"], abs=1e-1) == c100_data["throughput_rps"]
