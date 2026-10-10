"""
Unit tests verifying training provenance and receipt authority trust boundaries.

Threat Model & Invariants Tested:
1. DEFECT 1: Caller-supplied numeric metadata (loss, epochs, run_id) cannot establish
   trusted production provenance. Attempting to pass provenance_type="TRAINING_RECORDED"
   with fabricated metrics must leave the model UNVERIFIED and non-promotable in production.
2. DEFECT 2: Training receipt authority separation. Issuance requires the private key
   (TrainingReceiptIssuer), while ModelRegistry / serving only hold the public key
   (TrainingReceiptVerifier). General application secrets or symmetric keys cannot forge
   production-valid receipts.
3. Cryptographic binding: Receipts are strictly bound to the exact artifact SHA-256,
   simulation scope, feature schema, and cannot be replayed or tampered with.
"""

import hashlib
import io
import tempfile
from unittest.mock import patch

import pytest
import torch

from app.application.services.model_registry import (
    ModelRegistry,
)
from app.application.services.training_provenance_service import (
    TrainingReceiptIssuer,
    get_default_training_issuer,
    get_default_training_verifier,
)
from app.config import get_settings
from app.domain.model_serving_errors import ModelIntegrityError


def _create_mock_checkpoint() -> tuple[dict, bytes, str]:
    """Helper creating a genuine PyTorch state_dict and serialized bytes."""
    state_dict = {
        "fc.weight": torch.randn(2, 10, dtype=torch.float32),
        "fc.bias": torch.zeros(2, dtype=torch.float32),
    }
    buf = io.BytesIO()
    torch.save(state_dict, buf)
    raw_bytes = buf.getvalue()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    return state_dict, raw_bytes, sha256


class TestTrainingProvenanceEnforcement:
    """Tests for Defect 1: Eliminating numeric metadata provenance bypasses."""

    def test_forged_numeric_evidence_cannot_establish_production_provenance(self):
        """
        Threat: An attacker provides fabricated run IDs, low loss, and plausible epoch counts
        claiming provenance_type='TRAINING_RECORDED'.
        Invariant: The registry must refuse to classify the model as verified or production-eligible.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            state_dict, raw_bytes, sha256 = _create_mock_checkpoint()

            with patch.object(get_settings(), "app_env", "production"):
                metadata = registry.save_version(
                    simulation_id="sim_prov_test",
                    state_dict=state_dict,
                    metrics={"loss": 0.0012, "epochs": 100},
                    is_promoted=False,
                    provenance_type="TRAINING_RECORDED",
                    training_run_id="fabricated_run_001",
                    training_evidence={
                        "loss": 0.0012,
                        "epochs": 100,
                        "batch_size": 128,
                        "val_loss": 0.0015,
                    },
                    artifact_bytes=raw_bytes,
                )

                assert metadata["provenance_type"] == "UNVERIFIED"
                assert metadata["is_production_eligible"] is False

                # Direct promotion attempt must fail closed
                with pytest.raises(ModelIntegrityError, match="Production admission rejected"):
                    registry.promote_version("sim_prov_test", metadata["version"])

    def test_untrained_random_model_with_recorded_evidence_cannot_bypass_admission(self):
        """
        Threat: Caller tries save_version(is_promoted=True) with fabricated metadata.
        Invariant: Production admission check must reject activation immediately.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            state_dict, raw_bytes, _ = _create_mock_checkpoint()

            with patch.object(get_settings(), "app_env", "production"), \
                 pytest.raises(ModelIntegrityError):
                registry.save_version(
                        simulation_id="sim_prov_test",
                        state_dict=state_dict,
                        metrics={"loss": 0.01, "epochs": 50},
                        is_promoted=True,
                        provenance_type="TRAINING_RECORDED",
                        training_run_id="fake_run_999",
                        training_evidence={"loss": 0.01, "epochs": 50},
                        artifact_bytes=raw_bytes,
                    )


class TestAsymmetricReceiptAuthoritySeparation:
    """Tests for Defect 2: Asymmetric Ed25519 cryptographic authority separation."""

    def test_serving_verifier_cannot_issue_valid_receipts(self):
        """
        Threat: Serving client tries to sign a receipt using its own unprivileged keys.
        Invariant: Verifier only trusts public keys registered in the approved authority list.
        """
        unauthorized_issuer = TrainingReceiptIssuer(key_id="rogue-worker-01")
        official_verifier = get_default_training_verifier()

        _, _, sha256 = _create_mock_checkpoint()
        unauthorized_receipt = unauthorized_issuer.issue_receipt(
            training_run_id="rogue_run_001",
            job_id="rogue_job_001",
            artifact_sha256=sha256,
            architecture="fraud_detector_mlp",
            feature_names=["f1", "f2"],
            metrics={"final_loss": 0.05},
        )

        # Verifier must reject unknown key_id
        is_valid, reason = official_verifier.verify(unauthorized_receipt, expected_sha256=sha256)
        assert is_valid is False
        assert "not present in trusted" in reason

    def test_tampered_artifact_bytes_fails_receipt_verification(self):
        """
        Threat: Attacker uses a valid receipt issued for artifact A to authorize artifact B.
        Invariant: Cryptographic digest mismatch must reject the receipt.
        """
        issuer = get_default_training_issuer()
        verifier = get_default_training_verifier()

        _, _, sha256_a = _create_mock_checkpoint()
        _, _, sha256_b = _create_mock_checkpoint()

        receipt = issuer.issue_receipt(
            training_run_id="legit_run_001",
            job_id="legit_job_001",
            artifact_sha256=sha256_a,
            architecture="fraud_detector_mlp",
            feature_names=["f1", "f2"],
            metrics={"loss": 0.02},
        )

        is_valid, reason = verifier.verify(receipt, expected_sha256=sha256_b)
        assert is_valid is False
        assert "mismatch" in reason.lower()

    def test_tampered_signature_fails_verification(self):
        """
        Threat: Attacker modifies the signature bytes in an otherwise well-formed receipt.
        Invariant: Asymmetric signature verification must fail.
        """
        issuer = get_default_training_issuer()
        verifier = get_default_training_verifier()
        _, _, sha256 = _create_mock_checkpoint()

        receipt = issuer.issue_receipt(
            training_run_id="run_tamper_sig",
            job_id="job_tamper_sig",
            artifact_sha256=sha256,
            architecture="fraud_detector_mlp",
            feature_names=["f1", "f2"],
            metrics={"loss": 0.02},
        )

        d = receipt.to_dict()
        d["signature"] = "ff" * 64
        is_valid, reason = verifier.verify(d, expected_sha256=sha256)
        assert is_valid is False
        assert "signature" in reason.lower()

    def test_public_key_inside_receipt_is_not_trusted_as_authority(self):
        """
        Threat: Attacker generates a fresh Ed25519 keypair, signs a receipt, embeds their public key,
        hoping the verifier will trust the self-declared public key.
        Invariant: The verifier must only look up keys from its internal approved keystore.
        """
        ephemeral_issuer = TrainingReceiptIssuer(key_id="self-declared-key")
        verifier = get_default_training_verifier()
        _, _, sha256 = _create_mock_checkpoint()

        receipt = ephemeral_issuer.issue_receipt(
            training_run_id="self_signed_run",
            job_id="self_signed_job",
            artifact_sha256=sha256,
            architecture="fraud_detector_mlp",
            feature_names=["f1", "f2"],
            metrics={"loss": 0.05},
        )

        is_valid, reason = verifier.verify(receipt, expected_sha256=sha256)
        assert is_valid is False
        assert "not present in trusted" in reason
