"""Unit test suite for Centralized Backend TestDataFactory."""

import pytest
import numpy as np
from tests.factories.data_factory import TestDataFactory


def test_create_transaction_dict_defaults_and_overrides():
    txn = TestDataFactory.create_transaction_dict(amount=12500.0, is_fraud=1, bank_id="bank_b")
    assert txn["amount"] == 12500.0
    assert txn["is_fraud"] == 1
    assert txn["bank_id"] == "bank_b"
    assert txn["merchant_risk_score"] == 0.85
    assert txn["transaction_id"].startswith("TXN-")


def test_create_alert_dict():
    alert = TestDataFactory.create_alert_dict(severity="CRITICAL", composite_score=895.0)
    assert alert["severity"] == "CRITICAL"
    assert alert["composite_risk_score"] == 895.0
    assert len(alert["top_features"]) == 3
    assert len(alert["involved_entity_ids"]) == 2


def test_create_case_dict_lifecycle():
    open_case = TestDataFactory.create_case_dict(status="investigating")
    assert open_case["is_open"] is True
    assert open_case["closed_at"] is None

    closed_case = TestDataFactory.create_case_dict(status="closed_confirmed")
    assert closed_case["is_open"] is False
    assert closed_case["closed_at"] is not None


def test_create_evidence_dict_sha256():
    ev = TestDataFactory.create_evidence_dict(case_id="CASE-999", content="test_payload_123")
    assert ev["case_id"] == "CASE-999"
    assert len(ev["content_hash"]) == 64
    assert ev["evidence_type"] == "ledger_proof"


def test_create_rule_dict():
    rule = TestDataFactory.create_rule_dict(name="Block Velocity Surge")
    assert rule["name"] == "Block Velocity Surge"
    assert "and" in rule["condition"]
    assert len(rule["condition"]["and"]) == 2


def test_create_synthetic_weights_and_gradients():
    weights = TestDataFactory.create_synthetic_weights(seed=123)
    assert len(weights) == 3
    assert weights[0].shape == (16, 32)

    clean_grads = TestDataFactory.create_synthetic_gradient_update(is_byzantine=False, seed=123)
    byz_grads = TestDataFactory.create_synthetic_gradient_update(is_byzantine=True, seed=123)

    assert np.allclose(clean_grads[0], -0.1 * byz_grads[0])


def test_conftest_fixtures(data_factory, canonical_case, canonical_alert):
    assert data_factory is TestDataFactory
    assert canonical_case["id"] == "CASE-CANONICAL-PY-01"
    assert canonical_alert["id"] == "ALT-CANONICAL-PY-01"
