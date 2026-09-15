"""Unit tests for hardened security evaluators and mathematical label privacy guard (Stage 24 / Phase 43)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.domain.label_privacy_guard import (
    LabelPrivacyGuard,
    LabelPrivacyViolationError,
)
from app.domain.security_evaluator import (
    BackdoorDefenseEvaluator,
    ByzantineDefenseEvaluator,
    DLGEvaluator,
    MIAEvaluator,
    NetworkResilienceEvaluator,
)

# ==============================================================================
# 1. LabelPrivacyGuard Tests (Claim M-09, Randomized Response, PII Validation)
# ==============================================================================


def test_label_privacy_guard_calculate_sigma_analytical_parity() -> None:
    """Verifies analytical Gaussian noise scale calculation matches Dwork & Roth formula (Claim M-09)."""
    guard = LabelPrivacyGuard()
    epsilon = 1.0
    delta = 1e-5
    clip_norm = 1.0

    expected_sigma = (clip_norm * math.sqrt(2.0 * math.log(1.25 / delta))) / epsilon
    sigma = guard.calculate_sigma(epsilon=epsilon, delta=delta, clip_norm=clip_norm)

    assert sigma == round(expected_sigma, 6)
    assert sigma > 4.5  # Typical for (1.0, 1e-5) bound


def test_label_privacy_guard_calculate_sigma_invalid_parameters() -> None:
    """Verifies that calculate_sigma rejects non-positive or non-finite parameters."""
    guard = LabelPrivacyGuard()

    with pytest.raises(LabelPrivacyViolationError, match="epsilon must be strictly positive"):
        guard.calculate_sigma(epsilon=-0.5, delta=1e-5)

    with pytest.raises(LabelPrivacyViolationError, match="epsilon must be strictly positive"):
        guard.calculate_sigma(epsilon=0.0, delta=1e-5)

    with pytest.raises(LabelPrivacyViolationError, match="delta must be in range"):
        guard.calculate_sigma(epsilon=1.0, delta=0.0)

    with pytest.raises(LabelPrivacyViolationError, match="delta must be in range"):
        guard.calculate_sigma(epsilon=1.0, delta=1.5)

    with pytest.raises(LabelPrivacyViolationError, match="clipping norm must be strictly positive"):
        guard.calculate_sigma(epsilon=1.0, delta=1e-5, clip_norm=-1.0)


def test_label_privacy_guard_randomized_response_distribution() -> None:
    """Verifies local randomized response guarantees ε-DP label flipping probabilities."""
    guard = LabelPrivacyGuard()
    epsilon = 1.5
    expected_flip_prob = 1.0 / (1.0 + math.exp(epsilon))

    # Test seeded deterministic behavior
    res1 = guard.apply_randomized_response(label=1, epsilon=epsilon, seed=42)
    res2 = guard.apply_randomized_response(label=1, epsilon=epsilon, seed=42)
    assert res1 == res2

    # Statistical test across 10,000 trials
    trials = 10000
    flips = sum(
        guard.apply_randomized_response(label=1, epsilon=epsilon, seed=i) == 0
        for i in range(trials)
    )
    observed_flip_rate = flips / trials
    assert abs(observed_flip_rate - expected_flip_prob) < 0.02


def test_label_privacy_guard_randomized_response_invalid_inputs() -> None:
    """Verifies that randomized response rejects invalid label types or negative epsilon."""
    guard = LabelPrivacyGuard()

    with pytest.raises(LabelPrivacyViolationError, match="Binary label must be 0 or 1"):
        guard.apply_randomized_response(label=2, epsilon=1.0)

    with pytest.raises(LabelPrivacyViolationError, match="Epsilon must be strictly positive"):
        guard.apply_randomized_response(label=0, epsilon=-1.0)


def test_label_privacy_guard_delta_validation() -> None:
    """Verifies delta boundary checks in validate_gradient_privacy."""
    guard = LabelPrivacyGuard()

    # Valid
    guard.validate_gradient_privacy(epsilon=1.0, delta=1e-5)

    # Invalid delta
    with pytest.raises(LabelPrivacyViolationError, match="delta.*is invalid"):
        guard.validate_gradient_privacy(epsilon=1.0, delta=1.2)

    with pytest.raises(LabelPrivacyViolationError, match="delta.*is invalid"):
        guard.validate_gradient_privacy(epsilon=1.0, delta=0.0)


def test_label_privacy_guard_expanded_pii_detection() -> None:
    """Verifies detection of raw email, short tokens, and sensitive attribute keys."""
    guard = LabelPrivacyGuard()

    # Valid 64-char hash
    valid_hash = "a" * 64
    guard.validate_feedback_identifier(valid_hash)

    # Empty or short identifier
    with pytest.raises(LabelPrivacyViolationError, match="must be a non-empty string"):
        guard.validate_feedback_identifier("")

    with pytest.raises(LabelPrivacyViolationError, match="is too short"):
        guard.validate_feedback_identifier("short_token_123")

    # Cleartext Email (>= 32 chars)
    with pytest.raises(LabelPrivacyViolationError, match="Cleartext PII is strictly forbidden"):
        guard.validate_feedback_identifier("fraud_analyst_notification_alert@consortium-bank.com")

    # Forbidden attribute keys
    with pytest.raises(LabelPrivacyViolationError, match="Forbidden raw PII key 'password'"):
        guard.validate_feedback_identifier(valid_hash, raw_attributes={"password": "secret"})

    with pytest.raises(LabelPrivacyViolationError, match="Forbidden raw PII key 'account_number'"):
        guard.validate_feedback_identifier(valid_hash, raw_attributes={"account_number": "123456789"})

    with pytest.raises(LabelPrivacyViolationError, match="Forbidden raw PII key 'tax_id'"):
        guard.validate_feedback_identifier(valid_hash, raw_attributes={"tax_id": "999-00-1111"})


# ==============================================================================
# 2. MIAEvaluator Hardening Tests
# ==============================================================================


def test_mia_evaluator_advantage_clamping() -> None:
    """Verifies compute_advantage clamps out-of-range inputs and handles non-finite floats."""
    assert MIAEvaluator.compute_advantage(0.5) == 0.0
    assert MIAEvaluator.compute_advantage(1.0) == 1.0
    assert MIAEvaluator.compute_advantage(0.0) == 1.0
    assert MIAEvaluator.compute_advantage(float("nan")) == 0.0
    assert MIAEvaluator.compute_advantage(1.5) == 1.0


def test_mia_evaluator_empty_or_mismatched_dimensions() -> None:
    """Verifies evaluate_membership_inference raises ValueError on empty or mismatched arrays."""
    evaluator = MIAEvaluator(seed=42)

    with pytest.raises(ValueError, match="Input arrays must not be empty"):
        evaluator.evaluate_membership_inference(np.array([]), np.array([]), np.array([]))

    with pytest.raises(ValueError, match="Input dimension mismatch"):
        evaluator.evaluate_membership_inference(
            np.array([0, 1]), np.array([0.5]), np.array([True, False])
        )

    with pytest.raises(ValueError, match="epsilon must be strictly positive"):
        evaluator.evaluate_membership_inference(
            np.array([0, 1]), np.array([0.5, 0.5]), np.array([True, False]), epsilon=-1.0
        )

    with pytest.raises(ValueError, match="delta must be in range"):
        evaluator.evaluate_membership_inference(
            np.array([0, 1]), np.array([0.5, 0.5]), np.array([True, False]), delta=1.5
        )


# ==============================================================================
# 3. DLGEvaluator Hardening Tests
# ==============================================================================


def test_dlg_evaluator_pearson_correlation_robustness() -> None:
    """Verifies Pearson correlation handles empty, degenerate, or mismatched vectors."""
    assert DLGEvaluator.compute_pearson_correlation(np.array([1.0]), np.array([1.0])) == 0.0
    assert DLGEvaluator.compute_pearson_correlation(np.array([1.0, 2.0]), np.array([1.0])) == 0.0
    assert DLGEvaluator.compute_pearson_correlation(np.array([1.0, 1.0]), np.array([1.0, 1.0])) == 0.0
    assert DLGEvaluator.compute_pearson_correlation(
        np.array([float("nan"), 1.0]), np.array([1.0, 2.0])
    ) == 0.0


def test_dlg_evaluator_dimension_and_iteration_validation() -> None:
    """Verifies evaluate_gradient_leakage raises ValueError on invalid dimensions or iterations."""
    evaluator = DLGEvaluator(seed=42)

    with pytest.raises(ValueError, match="x_orig feature vector must not be empty"):
        evaluator.evaluate_gradient_leakage(np.array([]), np.array([]))

    with pytest.raises(ValueError, match="Gradient dimension.*must match"):
        evaluator.evaluate_gradient_leakage(np.array([1.0, 2.0]), np.array([1.0]))

    with pytest.raises(ValueError, match="num_iterations must be strictly positive"):
        evaluator.evaluate_gradient_leakage(np.array([1.0, 2.0]), np.array([1.0, 2.0]), num_iterations=0)


# ==============================================================================
# 4. BackdoorDefenseEvaluator Hardening Tests
# ==============================================================================


def test_backdoor_evaluator_input_validation() -> None:
    """Verifies evaluate_backdoor_defense validates dictionary non-emptiness, node presence, and dimensions."""
    evaluator = BackdoorDefenseEvaluator(seed=42)

    with pytest.raises(ValueError, match="node_updates dictionary cannot be empty"):
        evaluator.evaluate_backdoor_defense({}, malicious_node_id="bank_c")

    with pytest.raises(ValueError, match="malicious_node_id 'bank_c' not found"):
        evaluator.evaluate_backdoor_defense({"bank_a": [1.0, 2.0]}, malicious_node_id="bank_c")

    with pytest.raises(ValueError, match="must be a non-empty 1D numeric array"):
        evaluator.evaluate_backdoor_defense({"bank_c": []}, malicious_node_id="bank_c")

    with pytest.raises(ValueError, match="Dimension mismatch for node 'bank_c'"):
        evaluator.evaluate_backdoor_defense(
            {"bank_a": [1.0, 2.0], "bank_c": [1.0, 2.0, 3.0]}, malicious_node_id="bank_c"
        )


# ==============================================================================
# 5. Byzantine & Network Resilience Evaluator Hardening Tests
# ==============================================================================


def test_byzantine_evaluator_input_validation() -> None:
    """Verifies evaluate_byzantine_resilience checks total_nodes, f_byzantine, and attack_type."""
    evaluator = ByzantineDefenseEvaluator(seed=42)

    with pytest.raises(ValueError, match="total_nodes must be at least 2"):
        evaluator.evaluate_byzantine_resilience(total_nodes=1)

    with pytest.raises(ValueError, match="f_byzantine must be in range"):
        evaluator.evaluate_byzantine_resilience(f_byzantine=5, total_nodes=5)

    with pytest.raises(ValueError, match="Unsupported attack_type"):
        evaluator.evaluate_byzantine_resilience(attack_type="quantum_collapse")


def test_network_resilience_evaluator_input_validation() -> None:
    """Verifies evaluate_network_resilience checks total_nodes and quorum_threshold_pct."""
    evaluator = NetworkResilienceEvaluator(seed=42)

    with pytest.raises(ValueError, match="total_nodes must be at least 2"):
        evaluator.evaluate_network_resilience(total_nodes=1)

    with pytest.raises(ValueError, match="quorum_threshold_pct must be in range"):
        evaluator.evaluate_network_resilience(quorum_threshold_pct=1.5)

    with pytest.raises(ValueError, match="quorum_threshold_pct must be in range"):
        evaluator.evaluate_network_resilience(quorum_threshold_pct=0.0)
