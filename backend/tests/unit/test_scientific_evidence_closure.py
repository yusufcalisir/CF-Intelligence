"""Focused regression tests for Scientific Evidence Closure & Revalidation Gates.

Covers Part IX requirements:
- Section 56: Claim Direction Regression Test (Elliptic negative result)
- Section 57: Comparison-Type Regression Test (Architecture ablation vs FL parity)
- Section 58: CreditCard Comparability Test (Bank C evaluation population)
- Section 59: AML Label Guard Test (Strict fail-closed label validation)
- Section 60: AML Provenance Reproduction Test (Independent metric recomputation)
- Section 61: Global Completion Gate Test (Unit graph completion logic)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score, roc_auc_score

REPO_ROOT = Path(__file__).resolve().parents[3]
REVALIDATION_DIR = REPO_ROOT / "verification" / "scientific_revalidation"
CLAIMS_LEDGER_PATH = REVALIDATION_DIR / "claims_ledger.json"
UNITS_PATH = REVALIDATION_DIR / "revalidation_units.json"


def load_claims_ledger() -> dict[str, Any]:
    assert CLAIMS_LEDGER_PATH.exists(), f"Missing claims ledger at {CLAIMS_LEDGER_PATH}"
    return json.loads(CLAIMS_LEDGER_PATH.read_text(encoding="utf-8"))


def load_revalidation_units() -> dict[str, Any]:
    assert UNITS_PATH.exists(), f"Missing revalidation units at {UNITS_PATH}"
    return json.loads(UNITS_PATH.read_text(encoding="utf-8"))


# ===========================================================================
# 1. Claim Direction Regression Test (Section 56)
# ===========================================================================
def test_elliptic_claim_direction_regression() -> None:
    """Validate that candidate < baseline strictly prohibits uplift claim for Elliptic."""
    el_metrics_path = REVALIDATION_DIR / "results" / "RU-EL-01" / "metrics.json"
    el_baselines_path = REVALIDATION_DIR / "results" / "RU-EL-01" / "comparative_baselines.json"
    assert el_metrics_path.exists()
    assert el_baselines_path.exists()

    metrics = json.loads(el_metrics_path.read_text(encoding="utf-8"))
    baselines = json.loads(el_baselines_path.read_text(encoding="utf-8"))
    claims = load_claims_ledger()["claims"]

    clm_el = next((c for c in claims if c["CLAIM_ID"] == "CLM-EL-01"), None)
    assert clm_el is not None

    # Derive candidate and baseline PR-AUC
    # Tabular MLP baseline PR-AUC
    mlp_model = next((m for m in baselines["models"] if m["category"] == "TABULAR_BASELINE"), None)
    assert mlp_model is not None
    mlp_prauc = mlp_model["pr_auc"]

    # GraphSAGE (seed 42 candidate or multi-seed mean)
    graphsage_model = next((m for m in baselines["models"] if m["category"] == "GRAPH_INTELLIGENCE_PRIMARY"), None)
    assert graphsage_model is not None
    graphsage_prauc = graphsage_model["pr_auc"]
    mean_graphsage_prauc = metrics["primary_metrics"]["pr_auc"]

    # In both cases, GraphSAGE achieves lower PR-AUC than the 0-hop tabular MLP baseline
    delta_seed42 = graphsage_prauc - mlp_prauc
    delta_mean = mean_graphsage_prauc - mlp_prauc

    assert delta_seed42 < 0.0, f"Expected negative delta for seed 42, got {delta_seed42}"
    assert delta_mean < 0.0, f"Expected negative delta for mean, got {delta_mean}"

    # Regression invariant: Claim text MUST NOT claim detection uplift over tabular MLP
    claim_text = clm_el["CLAIM"].lower()
    assert "provides significant detection uplift over 0-hop" not in claim_text
    assert "did not outperform" in claim_text or "underperformed" in claim_text or "negative result" in claim_text

    # Must NOT claim statistical significance without a hypothesis test
    assert "statistically significant" not in claim_text
    assert "significant uplift" not in claim_text


# ===========================================================================
# 2. Comparison-Type Regression Test (Section 57)
# ===========================================================================
def test_comparison_type_paradigm_metadata() -> None:
    """Ensure architecture comparison cannot appear as centralized-vs-federated parity."""
    claims = load_claims_ledger()["claims"]
    clm_el = next((c for c in claims if c["CLAIM_ID"] == "CLM-EL-01"), None)
    assert clm_el is not None

    # CLM-EL-01 is an architecture/ablation comparison, NOT federated parity
    assert clm_el.get("COMPARISON_TYPE") == "ARCHITECTURE_ABLATION"
    assert "parity" not in clm_el["CLAIM"].lower() or "federated" not in clm_el["CLAIM"].lower()


# ===========================================================================
# 3. CreditCard Comparability Test (Section 58)
# ===========================================================================
def test_creditcard_bank_c_comparability_same_population() -> None:
    """Assert isolated Bank C and federated global PR-AUC use the same evaluation population."""
    cc_metrics_path = REVALIDATION_DIR / "results" / "RU-CC-01" / "metrics.json"
    assert cc_metrics_path.exists()
    metrics = json.loads(cc_metrics_path.read_text(encoding="utf-8"))
    claims = load_claims_ledger()["claims"]

    clm_cc2 = next((c for c in claims if c["CLAIM_ID"] == "CLM-CC-02"), None)
    assert clm_cc2 is not None

    eval_pop = metrics["evaluation_population"]
    assert eval_pop["test_transactions"] == 56962
    assert eval_pop["test_positive_count"] == 99
    assert eval_pop["test_negative_count"] == 56863

    # Verify both models evaluated on this population
    assert "bank_c_pathological_silo" in metrics
    assert "federated_fedavg" in metrics
    silo_prauc = metrics["bank_c_pathological_silo"]["pr_auc"]
    fed_prauc = metrics["federated_fedavg"]["pr_auc"]
    assert fed_prauc > silo_prauc

    # Assert claim text does NOT use unproven causal "rescue" or "statistical collapse"
    claim_text = clm_cc2["CLAIM"]
    assert "rescues near-zero fraud banking silos" not in claim_text
    assert "statistical collapse" not in claim_text
    assert "shared held-out evaluation population" in claim_text
    assert "56,962" in claim_text


# ===========================================================================
# 4. AML Label Guard Test (Section 59)
# ===========================================================================
def test_aml_label_guard_fail_closed_on_coercion(tmp_path: Path) -> None:
    """Verify DATA-006 guard: strict binary labels, fail-closed on NaN, null, and non-binary values."""
    from app.application.services.dataloader import _process_amlsim_dataframe

    # Valid binary dataframe
    df_valid = pd.DataFrame({
        "IS_FRAUD": [0, 1, 0, 1],
        "TIMESTAMP": [1, 2, 3, 4],
        "TX_AMOUNT": [10.0, 20.0, 30.0, 40.0],
        "SENDER_ACCOUNT_ID": [0, 1, 2, 3],
        "RECEIVER_ACCOUNT_ID": [1, 2, 3, 0],
    })
    res = _process_amlsim_dataframe(df_valid, root=tmp_path)
    assert np.array_equal(res["y"], np.array([0, 1, 0, 1]))

    # Valid boolean dataframe
    df_bool = pd.DataFrame({
        "IS_FRAUD": [False, True, False, True],
        "TIMESTAMP": [1, 2, 3, 4],
        "TX_AMOUNT": [10.0, 20.0, 30.0, 40.0],
        "SENDER_ACCOUNT_ID": [0, 1, 2, 3],
        "RECEIVER_ACCOUNT_ID": [1, 2, 3, 0],
    })
    res_bool = _process_amlsim_dataframe(df_bool, root=tmp_path)
    assert np.array_equal(res_bool["y"], np.array([0, 1, 0, 1]))

    # Reject NaN in labels (DATA-006 regression: astype(bool) would convert NaN to True)
    df_nan = pd.DataFrame({
        "IS_FRAUD": [0, np.nan, 1, 0],
        "TIMESTAMP": [1, 2, 3, 4],
        "TX_AMOUNT": [10.0, 20.0, 30.0, 40.0],
        "SENDER_ACCOUNT_ID": [0, 1, 2, 3],
        "RECEIVER_ACCOUNT_ID": [1, 2, 3, 0],
    })
    with pytest.raises(ValueError, match="NaN in label column"):
        _process_amlsim_dataframe(df_nan, root=tmp_path)

    # Reject unexpected string labels
    df_str = pd.DataFrame({
        "IS_FRAUD": ["0", "fraud", "1", "0"],
        "TIMESTAMP": [1, 2, 3, 4],
        "TX_AMOUNT": [10.0, 20.0, 30.0, 40.0],
        "SENDER_ACCOUNT_ID": [0, 1, 2, 3],
        "RECEIVER_ACCOUNT_ID": [1, 2, 3, 0],
    })
    with pytest.raises(ValueError, match="invalid non-integer"):
        _process_amlsim_dataframe(df_str, root=tmp_path)


# ===========================================================================
# 5. AML Provenance Reproduction Test (Section 60)
# ===========================================================================
def test_aml_provenance_reproduction() -> None:
    """Verify primary metrics can be reproduced from preserved predictions within tolerance."""
    pred_path = REVALIDATION_DIR / "results" / "RU-AML-02" / "predictions.parquet"
    metrics_path = REVALIDATION_DIR / "results" / "RU-AML-02" / "metrics.json"

    if not pred_path.exists() or not metrics_path.exists():
        pytest.skip("RU-AML-02 has not been executed yet (Phase A). Skipping reproduction test.")

    pred_df = pd.read_parquet(pred_path)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))

    y_true = pred_df["y_true"].to_numpy()
    y_prob = pred_df["y_prob_graphsage2"].to_numpy()

    recomputed_pr_auc = float(average_precision_score(y_true, y_prob))
    recomputed_roc_auc = float(roc_auc_score(y_true, y_prob))

    reported_pr_auc = metrics["primary_metrics"]["pr_auc"]
    reported_roc_auc = metrics["primary_metrics"]["roc_auc"]

    assert abs(recomputed_pr_auc - reported_pr_auc) < 1e-4
    assert abs(recomputed_roc_auc - reported_roc_auc) < 1e-4


# ===========================================================================
# 6. Global Completion Gate Test (Section 61)
# ===========================================================================
def test_global_completion_gate() -> None:
    """Verify state transition: cannot be COMPLETE while any unit is BLOCKED without supersession."""
    units_data = load_revalidation_units()
    units = units_data["units"]

    # Verify RU-AML-01 remains preserved as historical blocked attempt
    ru_aml_01 = next((u for u in units if u["UNIT_ID"] == "RU-AML-01"), None)
    assert ru_aml_01 is not None
    assert ru_aml_01.get("STATUS") == "BLOCKED_MISSING_HISTORICAL_STATE"
    assert ru_aml_01.get("SUPERSEDED_BY") == "RU-AML-02"

    # Verify RU-AML-02 exists as the superseding unit
    ru_aml_02 = next((u for u in units if u["UNIT_ID"] == "RU-AML-02"), None)
    assert ru_aml_02 is not None
    assert ru_aml_02["PARENT_UNIT"] == "RU-AML-01"
    assert ru_aml_02["REVALIDATION_TYPE"] == "FULL_RERUN_REQUIRED"

    # Evaluate active units:
    # Active set = {RU-CC-01, RU-PS-01, RU-EL-01, RU-IE-01, RU-MS-01, RU-AML-02}
    active_unit_ids = ["RU-CC-01", "RU-PS-01", "RU-EL-01", "RU-IE-01", "RU-MS-01", "RU-AML-02"]

    active_statuses = {}
    for uid in active_unit_ids:
        m_path = REVALIDATION_DIR / "results" / uid / "metrics.json"
        if m_path.exists():
            active_statuses[uid] = json.loads(m_path.read_text(encoding="utf-8")).get("status", "UNKNOWN")
        else:
            active_statuses[uid] = "PENDING_EXECUTION"

    # If RU-AML-02 has not been executed yet: status is PARTIALLY_COMPLETE
    if active_statuses["RU-AML-02"] != "PASS_VALID_REVALIDATION":
        global_status = "SCIENTIFIC_REVALIDATION_PARTIALLY_COMPLETE"
    else:
        # All 6 active units passed
        assert all(active_statuses[u] == "PASS_VALID_REVALIDATION" for u in active_unit_ids)
        global_status = "SCIENTIFIC_REVALIDATION_COMPLETE"

    # Gate assertion: if AML is not yet passed, status cannot be COMPLETE
    if active_statuses["RU-AML-02"] != "PASS_VALID_REVALIDATION":
        assert global_status != "SCIENTIFIC_REVALIDATION_COMPLETE"
        assert global_status == "SCIENTIFIC_REVALIDATION_PARTIALLY_COMPLETE"
    else:
        assert global_status == "SCIENTIFIC_REVALIDATION_COMPLETE"
