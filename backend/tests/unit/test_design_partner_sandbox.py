"""Unit tests for Design Partner Pilot Service and API Router."""

import pandas as pd

from app.application.services.design_partner_service import DesignPartnerPilotService


def test_hmac_sha256_type_salted_hashing():
    pilot = DesignPartnerPilotService(hmac_secret_salt=b"test-salt-123")
    hash1 = pilot.hash_pii_identifier("12345678901", entity_type="TCKN")
    hash2 = pilot.hash_pii_identifier("12345678901", entity_type="TCKN")
    hash_diff_type = pilot.hash_pii_identifier("12345678901", entity_type="IBAN")

    assert hash1 == hash2
    assert hash1 != hash_diff_type
    assert len(hash1) == 64  # SHA-256 hex string


def test_scan_for_raw_pii_detects_violations():
    pilot = DesignPartnerPilotService()

    clean_df = pd.DataFrame({
        "tx_id": ["tx_1", "tx_2"],
        "amount": [100.50, 450.00],
        "hashed_account": ["a1b2c3d4e5f6", "f6e5d4c3b2a1"],
    })
    res_clean = pilot.scan_for_raw_pii(clean_df)
    assert res_clean.clean is True
    assert len(res_clean.violations_detected) == 0

    dirty_df = pd.DataFrame({
        "tx_id": ["tx_1", "tx_2"],
        "credit_card": ["4532-1234-5678-9012", "5412 3456 7890 1234"],
        "email": ["fraudster@example.com", "innocent@bank.com"],
    })
    res_dirty = pilot.scan_for_raw_pii(dirty_df)
    assert res_dirty.clean is False
    assert len(res_dirty.violations_detected) >= 2


def test_pilot_readiness_checklist_generation():
    pilot = DesignPartnerPilotService()
    checklist = pilot.generate_pilot_readiness_checklist(partner_name="Fintech Alpha", jurisdiction="EU")

    assert checklist.partner_name == "Fintech Alpha"
    assert checklist.overall_readiness_score > 90.0
    assert checklist.status == "APPROVED_FOR_PILOT"
    assert len(checklist.compliance_items) >= 5


def test_evaluate_real_benchmark_paysim_and_ieee():
    pilot = DesignPartnerPilotService()
    res = pilot.evaluate_real_benchmark(dataset_name="paysim", n_samples=3000)

    assert "performance_comparison" in res
    assert "distribution_fidelity" in res
    assert "multi_threshold_confusion_matrices" in res
    assert "bank_partitions" in res
    assert res["performance_comparison"]["federated_advantage"]["net_daily_economic_benefit_dollars"] > 0
