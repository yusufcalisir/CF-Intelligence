"""Unit tests verifying cryptographic signature trust boundary for institutional ownership claims.

Tests:
CLAIM-01: Generic signed receipt + injected unsigned bank_id -> ownership binding rejected.
CLAIM-02: Original signature remains valid despite extra unsigned field, proving the
          authorization fix rejects the actual attack rather than merely an invalid signature.
CLAIM-03: Signed owning_bank_id="bank_a" cannot authorize Bank B.
CLAIM-04: Valid signed Bank A ownership successfully binds Bank A.
CLAIM-05: Valid signed consortium ownership successfully binds the correct consortium.
CLAIM-06: Unbound model saves with legitimate generic receipts succeed with verified training
          provenance without creating false institutional ownership.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from typing import Any

import pytest
import torch

from app.application.services.model_registry import ModelRegistry
from app.application.services.training_provenance_service import (
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.domain.model_serving_errors import ModelAuthorizationError

FEATURE_NAMES = ["amount", "hour", "device_trust", "velocity"]


def _make_dummy_model() -> tuple[dict[str, Any], bytes, str]:
    import hashlib

    model = torch.nn.Linear(len(FEATURE_NAMES), 1)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    raw = buf.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    return model.state_dict(), raw, sha


def _make_evidence(
    sim_id: str,
    sha256: str,
    owning_bank_id: str | None = None,
    consortium_id: str | None = None,
) -> TrainingExecutionEvidence:
    return TrainingExecutionEvidence(
        training_run_id=f"run_{sim_id}",
        job_id=f"job_{sim_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=3,
        executed_epochs=3,
        sample_count=100,
        observed_metrics={"loss": 0.05, "auc": 0.95},
        artifact_sha256=sha256,
        architecture="Linear",
        feature_schema_version="v1.0",
        feature_names=FEATURE_NAMES,
        owning_bank_id=owning_bank_id,
        consortium_id=consortium_id,
    )


@pytest.fixture
def temp_registry_dir():
    d = tempfile.mkdtemp(prefix="test_claim_auth_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def crypto_authority():
    issuer = TrainingReceiptIssuer()
    verifier = TrainingReceiptVerifier(trusted_keys={issuer.key_id: issuer.public_bytes_hex})
    return issuer, verifier


def test_claim_01_generic_receipt_with_injected_unsigned_bank_id_rejected(
    temp_registry_dir: str,
    crypto_authority: tuple[TrainingReceiptIssuer, TrainingReceiptVerifier],
):
    """CLAIM-01: An attacker injects unsigned bank_id='bank_b' into a genuine generic receipt.

    Both bind_simulation_ownership and save_version must reject the attempt fail-closed,
    and no institutional ownership record may be persisted.
    """
    issuer, verifier = crypto_authority
    registry = ModelRegistry(storage_dir=temp_registry_dir, verifier=verifier)
    state_dict, raw_bytes, sha256 = _make_dummy_model()

    sim_id = "sim_claim_01"
    evidence = _make_evidence(sim_id, sha256)  # No institutional claims
    genuine_receipt = issuer.issue_receipt(evidence=evidence, model_bytes=raw_bytes).to_dict()

    # Attacker injects unsigned bank_id
    tampered_receipt = dict(genuine_receipt)
    tampered_receipt["bank_id"] = "bank_b"

    # 1. Direct bind_simulation_ownership must fail
    with pytest.raises(ModelAuthorizationError) as exc_info:
        registry.bind_simulation_ownership(
            simulation_id=sim_id,
            owning_bank_id="bank_b",
            training_receipt=tampered_receipt,
        )
    assert "receipt_claim_mismatch" in str(exc_info.value)
    assert "receipt binds bank 'none'" in str(exc_info.value)

    # Verify no ownership.json was created
    ownership_file = os.path.join(temp_registry_dir, "registry", sim_id, "ownership.json")
    assert not os.path.exists(ownership_file)

    # 2. save_version claiming bank_b must fail upfront
    sim_id_save = "sim_claim_01_save"
    evidence_save = _make_evidence(sim_id_save, sha256)
    genuine_save_receipt = issuer.issue_receipt(evidence=evidence_save, model_bytes=raw_bytes).to_dict()
    tampered_save_receipt = dict(genuine_save_receipt)
    tampered_save_receipt["bank_id"] = "bank_b"

    with pytest.raises(ModelAuthorizationError) as exc_info2:
        registry.save_version(
            simulation_id=sim_id_save,
            state_dict=state_dict,
            artifact_bytes=raw_bytes,
            metrics={"loss": 0.05, "auc": 0.95},
            training_run_id=f"run_{sim_id_save}",
            training_evidence={"loss": 0.05, "epochs": 3, "sample_count": 100},
            training_receipt=tampered_save_receipt,
            owning_bank_id="bank_b",
        )
    assert "receipt_claim_mismatch" in str(exc_info2.value)

    # Verify no manifest, ownership record, or model files written for sim_id_save
    save_own_file = os.path.join(temp_registry_dir, "registry", sim_id_save, "ownership.json")
    assert not os.path.exists(save_own_file)
    save_manifest_file = os.path.join(temp_registry_dir, "registry", sim_id_save, "registry.json")
    assert not os.path.exists(save_manifest_file)
    ownership_state = registry.get_simulation_ownership(sim_id_save)
    assert ownership_state is None or ownership_state.get("is_unbound") is True


def test_claim_02_original_signature_remains_valid_despite_extra_unsigned_field(
    crypto_authority: tuple[TrainingReceiptIssuer, TrainingReceiptVerifier],
):
    """CLAIM-02: Demonstrates that the original Ed25519 signature is strictly valid

    despite the extra unsigned field. This proves the CLAIM-01 rejection is genuinely
    caused by trust boundary enforcement rather than a failed signature check.
    """
    issuer, verifier = crypto_authority
    _, raw_bytes, sha256 = _make_dummy_model()

    sim_id = "sim_claim_02"
    evidence = _make_evidence(sim_id, sha256)
    genuine_receipt = issuer.issue_receipt(evidence=evidence, model_bytes=raw_bytes).to_dict()

    # Tamper dictionary with extra unsigned fields
    tampered_receipt = dict(genuine_receipt)
    tampered_receipt["bank_id"] = "bank_b"
    tampered_receipt["owning_bank"] = "bank_b"
    tampered_receipt["institution"] = "bank_b"

    # Signature verification MUST SUCCEED because canonical_dict ignores extra fields
    is_valid, reason = verifier.verify_receipt(tampered_receipt, simulation_id=sim_id)
    assert is_valid is True, f"Expected signature to be valid, got: {reason}"
    assert "verified successfully" in reason.lower()


def test_claim_03_signed_bank_a_receipt_cannot_authorize_bank_b(
    temp_registry_dir: str,
    crypto_authority: tuple[TrainingReceiptIssuer, TrainingReceiptVerifier],
):
    """CLAIM-03: Receipt signed authoritatively for Bank A cannot authorize Bank B."""
    issuer, verifier = crypto_authority
    registry = ModelRegistry(storage_dir=temp_registry_dir, verifier=verifier)
    state_dict, raw_bytes, sha256 = _make_dummy_model()

    sim_id = "sim_claim_03"
    evidence = _make_evidence(sim_id, sha256, owning_bank_id="bank_a")
    bank_a_receipt = issuer.issue_receipt(
        evidence=evidence,
        model_bytes=raw_bytes,
        owning_bank_id="bank_a",
    ).to_dict()

    # Attempt to claim Bank B ownership using Bank A receipt
    with pytest.raises(ModelAuthorizationError) as exc_info:
        registry.bind_simulation_ownership(
            simulation_id=sim_id,
            owning_bank_id="bank_b",
            training_receipt=bank_a_receipt,
        )
    assert "receipt_claim_mismatch" in str(exc_info.value)
    assert "receipt binds bank 'bank_a'" in str(exc_info.value)
    assert "cannot authenticate claimed bank 'bank_b'" in str(exc_info.value)

    # Attempt save_version claiming Bank B with Bank A receipt
    with pytest.raises(ModelAuthorizationError) as exc_info2:
        registry.save_version(
            simulation_id=sim_id,
            state_dict=state_dict,
            artifact_bytes=raw_bytes,
            metrics={"loss": 0.05, "auc": 0.95},
            training_run_id=f"run_{sim_id}",
            training_evidence={"loss": 0.05, "epochs": 3, "sample_count": 100},
            training_receipt=bank_a_receipt,
            owning_bank_id="bank_b",
        )
    assert "receipt_claim_mismatch" in str(exc_info2.value)


def test_claim_04_valid_signed_bank_a_ownership_successfully_binds_bank_a(
    temp_registry_dir: str,
    crypto_authority: tuple[TrainingReceiptIssuer, TrainingReceiptVerifier],
):
    """CLAIM-04: Genuine signed Bank A receipt authoritatively binds Bank A ownership."""
    issuer, verifier = crypto_authority
    registry = ModelRegistry(storage_dir=temp_registry_dir, verifier=verifier)
    state_dict, raw_bytes, sha256 = _make_dummy_model()

    sim_id = "sim_claim_04"
    evidence = _make_evidence(sim_id, sha256, owning_bank_id="bank_a")
    bank_a_receipt = issuer.issue_receipt(
        evidence=evidence,
        model_bytes=raw_bytes,
        owning_bank_id="bank_a",
    ).to_dict()

    # 1. bind_simulation_ownership succeeds
    record = registry.bind_simulation_ownership(
        simulation_id=sim_id,
        owning_bank_id="bank_a",
        training_receipt=bank_a_receipt,
    )
    assert record["owning_bank_id"] == "bank_a"
    assert record["is_unbound"] is False
    assert "receipt:" in record["bound_by"]
    assert "(bank_a)" in record["bound_by"]

    # Assert persisted ownership.json
    ownership_path = os.path.join(temp_registry_dir, "registry", sim_id, "ownership.json")
    assert os.path.exists(ownership_path)
    with open(ownership_path, encoding="utf-8") as f:
        persisted = json.load(f)
    assert persisted["owning_bank_id"] == "bank_a"

    # 2. save_version for another simulation with signed Bank A receipt succeeds
    sim_id_save = "sim_claim_04_save"
    evidence_save = _make_evidence(sim_id_save, sha256, owning_bank_id="bank_a")
    bank_a_save_receipt = issuer.issue_receipt(
        evidence=evidence_save,
        model_bytes=raw_bytes,
        owning_bank_id="bank_a",
    ).to_dict()

    entry = registry.save_version(
        simulation_id=sim_id_save,
        state_dict=state_dict,
        artifact_bytes=raw_bytes,
        metrics={"loss": 0.05, "auc": 0.95},
        training_run_id=f"run_{sim_id_save}",
        training_evidence={"loss": 0.05, "epochs": 3, "sample_count": 100},
        training_receipt=bank_a_save_receipt,
        owning_bank_id="bank_a",
    )
    assert entry["owning_bank_id"] == "bank_a"
    assert entry["provenance_type"] == "TRAINING_RECORDED"
    assert entry["attestation_status"] == "RECEIPT_VERIFIED_TRAINING_AUTHORITY"

    ownership_save_path = os.path.join(temp_registry_dir, "registry", sim_id_save, "ownership.json")
    with open(ownership_save_path, encoding="utf-8") as f:
        persisted_save = json.load(f)
    assert persisted_save["owning_bank_id"] == "bank_a"


def test_claim_05_valid_signed_consortium_ownership_binds_correct_consortium(
    temp_registry_dir: str,
    crypto_authority: tuple[TrainingReceiptIssuer, TrainingReceiptVerifier],
):
    """CLAIM-05: Genuine signed consortium receipt authoritatively binds consortium ownership."""
    issuer, verifier = crypto_authority
    registry = ModelRegistry(storage_dir=temp_registry_dir, verifier=verifier)
    state_dict, raw_bytes, sha256 = _make_dummy_model()

    sim_id = "sim_claim_05"
    cons_id = "fraud_defense_consortium"
    evidence = _make_evidence(sim_id, sha256, consortium_id=cons_id)
    cons_receipt = issuer.issue_receipt(
        evidence=evidence,
        model_bytes=raw_bytes,
        consortium_id=cons_id,
    ).to_dict()

    record = registry.bind_simulation_ownership(
        simulation_id=sim_id,
        consortium_id=cons_id,
        training_receipt=cons_receipt,
    )
    assert record["consortium_id"] == cons_id
    assert record["is_unbound"] is False
    assert f"(consortium:{cons_id})" in record["bound_by"]

    ownership_path = os.path.join(temp_registry_dir, "registry", sim_id, "ownership.json")
    with open(ownership_path, encoding="utf-8") as f:
        persisted = json.load(f)
    assert persisted["consortium_id"] == cons_id

    # Mismatched consortium claim rejected
    sim_id_mismatch = "sim_claim_05_mismatch"
    evidence_mismatch = _make_evidence(sim_id_mismatch, sha256, consortium_id=cons_id)
    cons_receipt_mismatch = issuer.issue_receipt(
        evidence=evidence_mismatch,
        model_bytes=raw_bytes,
        consortium_id=cons_id,
    ).to_dict()

    with pytest.raises(ModelAuthorizationError) as exc_info:
        registry.bind_simulation_ownership(
            simulation_id=sim_id_mismatch,
            consortium_id="other_consortium",
            training_receipt=cons_receipt_mismatch,
        )
    assert "receipt_claim_mismatch" in str(exc_info.value)
    assert f"receipt binds consortium '{cons_id}'" in str(exc_info.value)


def test_claim_06_unassigned_receipt_saves_model_without_institutional_claim(
    temp_registry_dir: str,
    crypto_authority: tuple[TrainingReceiptIssuer, TrainingReceiptVerifier],
):
    """CLAIM-06: A genuine legacy / unassigned receipt (no institutional claims)

    successfully registers an unowned simulation model with verified training provenance,
    preserving full backward compatibility without granting false institutional ownership.
    """
    issuer, verifier = crypto_authority
    registry = ModelRegistry(storage_dir=temp_registry_dir, verifier=verifier)
    state_dict, raw_bytes, sha256 = _make_dummy_model()

    sim_id = "sim_claim_06_unbound"
    evidence = _make_evidence(sim_id, sha256)
    unassigned_receipt = issuer.issue_receipt(evidence=evidence, model_bytes=raw_bytes).to_dict()

    # Save model without requesting institutional ownership
    entry = registry.save_version(
        simulation_id=sim_id,
        state_dict=state_dict,
        artifact_bytes=raw_bytes,
        metrics={"loss": 0.05, "auc": 0.95},
        training_run_id=f"run_{sim_id}",
        training_evidence={"loss": 0.05, "epochs": 3, "sample_count": 100},
        training_receipt=unassigned_receipt,
        # No owning_bank_id or consortium_id
    )

    assert entry["provenance_type"] == "TRAINING_RECORDED"
    assert entry["attestation_status"] == "RECEIPT_VERIFIED_TRAINING_AUTHORITY"
    assert entry.get("owning_bank_id") is None
    assert entry.get("consortium_id") is None

    # Ownership remains unbound
    ownership = registry.get_simulation_ownership(sim_id)
    assert ownership.get("is_unbound") is True
    assert ownership.get("owning_bank_id") is None
