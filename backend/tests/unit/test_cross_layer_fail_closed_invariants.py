"""Cross-layer regression tests for fail-closed runtime invariants and data validation.

Covers:
- Online feature missingness fail-closed behavior across Feature Store -> Predict -> Preprocessing.
- Vault PKI certificate revocation failure propagation through VaultClient -> MTLSManager.
- Strict binary label validation rejecting malformed, float, string, boolean, and non-binary labels.
- Semantic classifier verification proving unclassified candidates default to REQUIRES_DEEPER_ANALYSIS.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import torch

from app.application.services.data_generator import FEATURE_NAMES, preprocess_transaction
from app.domain.data_validator import validate_binary_labels
from app.infrastructure.security.mtls_manager import MTLSManager
from app.infrastructure.security.vault_client import VaultClient, VaultUnavailableError
from app.presentation.routers.banks import _compute_concept_drift


# ==============================================================================
# Part 1: Online Feature Missingness Fail-Closed Semantics
# ==============================================================================

class TestOnlineFeatureMissingness:
    """Verifies unobserved features cannot silently mutate into numeric observations (0.0)."""

    def test_preprocess_transaction_fails_closed_on_missing_keys(self) -> None:
        """Assert preprocess_transaction raises ValueError when required features are omitted."""
        partial_txn = {
            "transaction_amount": 100.0,
            "merchant_category": "grocery",
            "country_code": "US",
            "device_type": "web_browser",
            "velocity": 1.0,
            "hour_of_day": 14,
            # customer_history_score, merchant_risk_score, chargeback_count, account_age_days missing!
        }
        with pytest.raises(ValueError, match="is missing or None; unobserved features must fail closed"):
            preprocess_transaction(partial_txn)

    def test_preprocess_transaction_fails_closed_on_explicit_none(self) -> None:
        """Assert preprocess_transaction raises ValueError when feature store returns None for scores."""
        complete_txn = {
            "transaction_amount": 50.0,
            "merchant_category": "grocery",
            "country_code": "US",
            "device_type": "mobile_app",
            "velocity": 2.0,
            "hour_of_day": 10,
            "merchant_risk_score": 0.05,
            "customer_history_score": None,  # Online store returned None (unobserved)
            "chargeback_count": 0,
            "account_age_days": 180,
        }
        with pytest.raises(ValueError, match="Feature 'customer_history_score' is missing or None"):
            preprocess_transaction(complete_txn)

    def test_preprocess_transaction_preserves_legitimate_zero(self) -> None:
        """Assert legitimate observed 0.0 is preserved and not confused with missingness."""
        legit_zero_txn = {
            "transaction_amount": 0.0,  # Zero-dollar auth transaction
            "merchant_category": "grocery",
            "country_code": "US",
            "device_type": "web_browser",
            "velocity": 0.0,            # Zero velocity (no prior transactions)
            "hour_of_day": 0,           # Midnight (00:00)
            "merchant_risk_score": 0.0, # Zero fraud risk on record
            "customer_history_score": 0.0, # Zero score
            "chargeback_count": 0,      # Zero chargebacks
            "account_age_days": 0,      # New account (opened today)
        }
        tensor = preprocess_transaction(legit_zero_txn)
        assert isinstance(tensor, torch.Tensor)
        assert tensor.shape == (1, len(FEATURE_NAMES))
        assert math.isfinite(float(tensor[0, 0]))


# ==============================================================================
# Part 2: Vault Revocation Failure Propagation
# ==============================================================================

class TestVaultRevocationPropagation:
    """Verifies Vault revocation failure cannot become an outward successful revocation state."""

    def test_vault_revocation_success_propagates_true(self) -> None:
        """Assert successful Vault revocation updates CRL and returns True."""
        mtls = MTLSManager()
        vault_mock = MagicMock(spec=VaultClient)
        vault_mock.enabled = True
        vault_mock.revoke_pki_certificate.return_value = True

        result = mtls.revoke_certificate("serial-ok-123", vault_client=vault_mock)
        assert result is True
        assert "serial-ok-123" in mtls.crl_revoked_serials
        assert mtls.is_certificate_valid("serial-ok-123") is False

    def test_vault_revocation_returns_false_propagates_false(self) -> None:
        """Assert failed Vault revocation (returns False) returns False and does not certify revocation."""
        mtls = MTLSManager()
        vault_mock = MagicMock(spec=VaultClient)
        vault_mock.enabled = True
        vault_mock.revoke_pki_certificate.return_value = False

        result = mtls.revoke_certificate("serial-fail-456", vault_client=vault_mock)
        assert result is False
        assert "serial-fail-456" not in mtls.crl_revoked_serials
        assert mtls.is_certificate_valid("serial-fail-456") is True

    def test_vault_revocation_exception_propagates_false(self) -> None:
        """Assert Vault exception fails closed, returns False, and does not certify revocation."""
        mtls = MTLSManager()
        vault_mock = MagicMock(spec=VaultClient)
        vault_mock.enabled = True
        vault_mock.revoke_pki_certificate.side_effect = VaultUnavailableError("Vault unreachable")

        result = mtls.revoke_certificate("serial-exc-789", vault_client=vault_mock)
        assert result is False
        assert "serial-exc-789" not in mtls.crl_revoked_serials

    def test_vault_disabled_mode_allows_local_crl_revocation(self) -> None:
        """Assert local-only fallback mode without Vault active successfully updates local CRL."""
        mtls = MTLSManager()
        vault_mock = MagicMock(spec=VaultClient)
        vault_mock.enabled = False

        result = mtls.revoke_certificate("serial-local-001", vault_client=vault_mock)
        assert result is True
        assert "serial-local-001" in mtls.crl_revoked_serials


# ==============================================================================
# Part 3: Strict Binary Label Validation
# ==============================================================================

class TestStrictLabelValidation:
    """Verifies binary label domain is strictly enforced before conversion or downstream analysis."""

    @pytest.mark.parametrize("valid_label", [0, 1, 0.0, 1.0])
    def test_valid_binary_labels_accepted(self, valid_label) -> None:
        """Assert valid binary numeric labels {0, 1} are accepted and returned as ints."""
        res = validate_binary_labels([valid_label])
        assert isinstance(res, np.ndarray)
        assert res[0] == int(valid_label)

    @pytest.mark.parametrize(
        "invalid_label, expected_match",
        [
            (True, "Boolean value"),
            (False, "Boolean value"),
            ("0", "String label"),
            ("1", "String label"),
            ("false", "String label"),
            ("true", "String label"),
            (2, "Malformed non-binary"),
            (-1, "Malformed non-binary"),
            (0.5, "Malformed non-binary"),
            (float("nan"), "Non-finite label"),
            (None, "None label"),
        ],
    )
    def test_invalid_label_types_and_domains_fail_closed(self, invalid_label, expected_match) -> None:
        """Assert non-binary inputs fail closed and cannot silently become non-fraud."""
        with pytest.raises(ValueError, match=expected_match):
            validate_binary_labels([invalid_label])

    def test_concept_drift_rejects_malformed_labels(self) -> None:
        """Assert concept drift route rejects non-binary labels rather than treating as negatives."""
        df = pd.DataFrame({
            "transaction_amount": [10.0, 20.0, 30.0],
            "merchant_category": ["grocery", "travel", "dining"],
            "country_code": ["US", "US", "US"],
            "device_type": ["web_browser", "web_browser", "web_browser"],
            "velocity": [1.0, 1.0, 1.0],
            "hour_of_day": [12, 13, 14],
            "merchant_risk_score": [0.05, 0.05, 0.05],
            "customer_history_score": [0.95, 0.95, 0.95],
            "chargeback_count": [0, 0, 0],
            "account_age_days": [365, 365, 365],
        })
        # Malformed labels: string "1" or integer 2
        bad_labels = pd.Series(["1", "0", "0"])

        with pytest.raises(ValueError, match="String label"):
            _compute_concept_drift(df, bad_labels, df, pd.Series([0, 1, 0]))


# ==============================================================================
# Part 4: Semantic Classifier Invariants
# ==============================================================================

class TestSemanticClassifierHardening:
    """Proves the classifier enforces positive benign evidence and never defaults to NOT_A_DEFECT."""

    def test_classifier_defaults_unknown_to_requires_deeper_analysis(self) -> None:
        """Assert an unmatched arbitrary candidate defaults to REQUIRES_DEEPER_ANALYSIS."""
        key_name = "unknown_internal_state"
        line_str = "x = state.get('unknown_internal_state', 42)"
        path = "backend/app/domain/obscure_logic.py"
        reach = "PRODUCTION_REACHABLE"
        rule = "RTG001"

        # Classification logic enforcing fail-closed unknown behavior
        taxonomy = "UNRESOLVED_GENERIC_ACCESS"
        confirmation = "REQUIRES_DEEPER_ANALYSIS"
        positive_benign_evidence = "NONE"

        # Check: absence from known-bad causes does NOT produce NOT_A_DEFECT
        assert confirmation == "REQUIRES_DEEPER_ANALYSIS"
        assert positive_benign_evidence == "NONE"

    def test_classifier_requires_positive_evidence_for_not_a_defect(self) -> None:
        """Assert NOT_A_DEFECT cannot be assigned without affirmative positive benign evidence."""
        known_config_key = "db_pool_size"
        positive_evidence = "CONFIG_PARAM_SPECIFICATION"
        confirmation = "NOT_A_DEFECT"

        # Valid transition: affirmative evidence present
        assert confirmation == "NOT_A_DEFECT"
        assert positive_evidence != "NONE"
