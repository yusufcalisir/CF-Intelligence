"""MIA/DLG Privacy Defense Verification Test Suite.

Scientifically verifies that:
  1. Unprotected model is vulnerable to MIA (advantage > 0.10).
  2. Strong DP (sigma=2.0) reduces MIA advantage by >= 50%.
  3. Strong DP reduces DLG alarm rate to <= 5%.
  4. RDP epsilon bounds are correctly computed per noise multiplier.
  5. Edge cases (empty gradients, invalid inputs) raise ValueError.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "backend"))

from app.infrastructure.security.mia_auditor import (
    DLGResult,
    MIAAuditReport,
    MIAResult,
    MIAuditor,
    _rdp_to_epsilon,
    run_dlg_cosine_similarity_audit,
    run_mia_loss_threshold_audit,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RNG = np.random.default_rng(42)
N = 400
MODEL_DIM = 512


@pytest.fixture(scope="module")
def loss_arrays():
    member_losses = RNG.normal(0.18, 0.08, size=N).clip(0.01)
    nonmember_losses = RNG.normal(0.62, 0.10, size=N).clip(0.01)
    return member_losses, nonmember_losses


@pytest.fixture(scope="module")
def gradient_vectors():
    return [RNG.standard_normal(MODEL_DIM).astype(np.float64) for _ in range(30)]


@pytest.fixture(scope="module")
def full_audit_report(loss_arrays, gradient_vectors):
    member_losses, nonmember_losses = loss_arrays
    auditor = MIAuditor()
    return auditor.run_full_audit(
        member_losses=member_losses,
        nonmember_losses=nonmember_losses,
        gradients=gradient_vectors,
    )


# ---------------------------------------------------------------------------
# 1. RDP epsilon bound computation
# ---------------------------------------------------------------------------

class TestRDPEpsilonBound:
    def test_unprotected_returns_none(self):
        assert _rdp_to_epsilon(0.0) is None

    def test_moderate_dp_epsilon_in_range(self):
        eps = _rdp_to_epsilon(1.0)
        assert eps is not None
        assert 1.0 <= eps <= 3.0, f"Expected 1.0<=eps<=3.0, got {eps}"

    def test_strong_dp_epsilon_lower_than_moderate(self):
        eps_moderate = _rdp_to_epsilon(1.0)
        eps_strong = _rdp_to_epsilon(2.0)
        assert eps_moderate is not None and eps_strong is not None
        assert eps_strong < eps_moderate, "Stronger noise must yield lower epsilon"

    def test_epsilon_decreases_monotonically_with_sigma(self):
        sigmas = [0.5, 1.0, 1.5, 2.0, 3.0]
        epsilons = [_rdp_to_epsilon(s) for s in sigmas]
        for i in range(len(epsilons) - 1):
            assert epsilons[i] > epsilons[i + 1], f"Non-monotone at sigma={sigmas[i]}"

    def test_target_budget_at_sigma_1(self):
        eps = _rdp_to_epsilon(sigma=1.0, q=0.05, T=50, delta=1e-5)
        assert eps is not None and eps <= 2.0, f"sigma=1.0 should give eps<=2.0, got {eps}"


# ---------------------------------------------------------------------------
# 2. MIA loss-threshold oracle
# ---------------------------------------------------------------------------

class TestMIALossThresholdAudit:
    def test_unprotected_has_significant_advantage(self, loss_arrays):
        member_losses, nonmember_losses = loss_arrays
        result = run_mia_loss_threshold_audit(member_losses, nonmember_losses, sigma=0.0)
        assert isinstance(result, MIAResult)
        assert result.advantage > 0.10, f"Unprotected MIA advantage too low: {result.advantage}"

    def test_strong_dp_reduces_advantage(self, loss_arrays):
        member_losses, nonmember_losses = loss_arrays
        r0 = run_mia_loss_threshold_audit(member_losses, nonmember_losses, sigma=0.0)
        r2 = run_mia_loss_threshold_audit(member_losses, nonmember_losses, sigma=2.0)
        assert r2.advantage < r0.advantage, "Strong DP must reduce adversarial advantage"

    def test_epsilon_none_for_unprotected(self, loss_arrays):
        member_losses, nonmember_losses = loss_arrays
        result = run_mia_loss_threshold_audit(member_losses, nonmember_losses, sigma=0.0)
        assert result.epsilon_bound is None

    def test_epsilon_set_for_protected(self, loss_arrays):
        member_losses, nonmember_losses = loss_arrays
        result = run_mia_loss_threshold_audit(member_losses, nonmember_losses, sigma=1.0)
        assert result.epsilon_bound is not None and result.epsilon_bound > 0

    def test_attack_asr_bounded(self, loss_arrays):
        member_losses, nonmember_losses = loss_arrays
        result = run_mia_loss_threshold_audit(member_losses, nonmember_losses, sigma=0.0)
        assert 0.0 <= result.attack_asr <= 1.0
        assert -0.5 <= result.advantage <= 0.5

    def test_invalid_2d_input_raises(self):
        with pytest.raises(ValueError):
            run_mia_loss_threshold_audit(np.ones((4, 4)), np.ones(4), sigma=0.0)

    def test_empty_input_raises(self):
        with pytest.raises(ValueError):
            run_mia_loss_threshold_audit(np.array([]), np.ones(4), sigma=0.0)

    def test_sample_counts_recorded(self, loss_arrays):
        member_losses, nonmember_losses = loss_arrays
        result = run_mia_loss_threshold_audit(member_losses, nonmember_losses, sigma=0.0)
        assert result.samples_member == len(member_losses)
        assert result.samples_nonmember == len(nonmember_losses)


# ---------------------------------------------------------------------------
# 3. DLG cosine-similarity proxy
# ---------------------------------------------------------------------------

class TestDLGCosineSimilarityAudit:
    def test_unprotected_high_cosine_similarity(self, gradient_vectors):
        result = run_dlg_cosine_similarity_audit(gradient_vectors, sigma=0.0)
        assert isinstance(result, DLGResult)
        assert result.mean_cosine_similarity > 0.99,             f"Unprotected: expected ~1.0 cosine, got {result.mean_cosine_similarity}"

    def test_strong_dp_reduces_cosine_similarity(self, gradient_vectors):
        r0 = run_dlg_cosine_similarity_audit(gradient_vectors, sigma=0.0)
        r2 = run_dlg_cosine_similarity_audit(gradient_vectors, sigma=2.0)
        assert r2.mean_cosine_similarity < r0.mean_cosine_similarity

    def test_strong_dp_alarm_rate_low(self, gradient_vectors):
        result = run_dlg_cosine_similarity_audit(gradient_vectors, sigma=2.0)
        assert result.alarm_rate <= 0.05,             f"Strong DP alarm rate exceeds 5%: {result.alarm_rate:.4f}"

    def test_cosine_similarity_bounded(self, gradient_vectors):
        result = run_dlg_cosine_similarity_audit(gradient_vectors, sigma=1.0)
        assert -1.0 <= result.mean_cosine_similarity <= 1.0
        assert 0.0 <= result.alarm_rate <= 1.0

    def test_empty_gradients_raises(self):
        with pytest.raises(ValueError):
            run_dlg_cosine_similarity_audit([], sigma=0.0)

    def test_norm_ratio_unprotected_near_one(self, gradient_vectors):
        result = run_dlg_cosine_similarity_audit(gradient_vectors, sigma=0.0)
        assert abs(result.gradient_norm_ratio - 1.0) < 0.05,             f"No-noise norm ratio should be ~1.0, got {result.gradient_norm_ratio}"


# ---------------------------------------------------------------------------
# 4. Full audit report integration
# ---------------------------------------------------------------------------

class TestMIAAuditReport:
    def test_report_has_three_mia_results(self, full_audit_report):
        assert len(full_audit_report.mia_results) == 3

    def test_report_has_three_dlg_results(self, full_audit_report):
        assert len(full_audit_report.dlg_results) == 3

    def test_dp_mitigates_mia_true(self, full_audit_report):
        assert full_audit_report.dp_mitigates_mia,             "Strong DP must reduce MIA advantage by >=50%"

    def test_dp_mitigates_dlg_true(self, full_audit_report):
        assert full_audit_report.dp_mitigates_dlg,             "Strong DP must reduce DLG alarm rate to <=5%"

    def test_summary_dict_structure(self, full_audit_report):
        s = full_audit_report.summary()
        assert "dp_mitigates_mia" in s
        assert "dp_mitigates_dlg" in s
        assert len(s["mia_results"]) == 3
        assert len(s["dlg_results"]) == 3

    def test_mia_advantage_monotone_with_sigma(self, full_audit_report):
        advantages = [r.advantage for r in full_audit_report.mia_results]
        # Advantage should not increase as sigma increases
        assert advantages[0] >= advantages[-1],             f"Advantage should not increase with sigma: {advantages}"

    def test_dlg_alarm_rate_monotone_with_sigma(self, full_audit_report):
        alarm_rates = [r.alarm_rate for r in full_audit_report.dlg_results]
        assert alarm_rates[0] >= alarm_rates[-1],             f"DLG alarm rate should not increase with sigma: {alarm_rates}"

    def test_report_metadata(self, full_audit_report):
        assert full_audit_report.n_member_samples == N
        assert full_audit_report.n_nonmember_samples == N
        assert full_audit_report.model_dimension == MODEL_DIM

    def test_dp_mitigates_mia_false_when_insufficient_results(self):
        report = MIAAuditReport(
            n_member_samples=10, n_nonmember_samples=10, model_dimension=32
        )
        assert report.dp_mitigates_mia is False

    def test_dp_mitigates_dlg_false_when_no_results(self):
        report = MIAAuditReport(
            n_member_samples=10, n_nonmember_samples=10, model_dimension=32
        )
        assert report.dp_mitigates_dlg is False
