"""Unit tests verifying training provenance and receipt authority trust boundaries.

Adversarial Verification Suite for Module 01-A (PROV-01 through PROV-16):
- PROV-01: Public-key-only verifier in production (no issuer, no private key).
- PROV-02: Missing verification key fails closed in production.
- PROV-03: Application payload secret cannot forge training provenance.
- PROV-04: Untrusted issuer key rejected.
- PROV-05: Tampered receipt rejected across all security-critical signed fields.
- PROV-06: Modified artifact bytes fail digest binding.
- PROV-07: Cross-simulation replay rejected.
- PROV-08: Incompatible feature schema / architecture / input dimension rejected.
- PROV-09: Missing receipt fields cannot be silently reconstructed during deserialization.
- PROV-10: Fabricated execution metadata cannot establish trusted execution.
- PROV-11: Legitimate local training produces verifiable receipt through trusted path.
- PROV-12: Legacy HMAC isolated to development (rejected in production).
- PROV-13: Key revocation invalidates receipts signed with compromised keys.
- PROV-14: Key rotation supports historical and new authorized keys.
- PROV-15: Issuer/verifier separation at import and initialization time.
- PROV-16: ModelRegistry preserves provenance trust level and rejects forged evidence.
"""

from __future__ import annotations

import copy
import hashlib
import io
import math
import os
import tempfile
from unittest.mock import patch

import numpy as np
import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    DEFAULT_TRAINING_KEY_ID,
    TrainingExecutionEvidence,
    TrainingExecutionReceipt,
    TrainingProvenanceConfigurationError,
    TrainingReceiptAuthority,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
    get_default_training_verifier,
    reset_default_training_issuer,
    reset_default_training_verifier,
)
from app.config import get_settings
from app.domain.model_serving_errors import ModelIntegrityError


def _create_mock_checkpoint() -> tuple[dict[str, torch.Tensor], bytes, str]:
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
        """Threat: An attacker provides fabricated run IDs, low loss, and plausible epoch counts

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
        """Threat: Caller tries save_version(is_promoted=True) with fabricated metadata.

        Invariant: Production admission check must reject activation immediately.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            state_dict, raw_bytes, _ = _create_mock_checkpoint()

            with patch.object(get_settings(), "app_env", "production"), pytest.raises(ModelIntegrityError):
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


class TestModule01AAdversarialProvenance:
    """Mandatory Adversarial Verification Suite: PROV-01 through PROV-16."""

    def setup_method(self):
        reset_default_training_verifier()
        reset_default_training_issuer()

    def teardown_method(self):
        reset_default_training_verifier()
        reset_default_training_issuer()

    def test_prov_01_public_key_only_verifier_in_production(self):
        """PROV-01: Public-key-only verifier in production.

        Verifier initializes with trusted public key, private key is completely absent,
        and TrainingReceiptIssuer is NEVER constructed during initialization or verification.
        """
        priv = ed25519.Ed25519PrivateKey.generate()
        pub = priv.public_key()
        pub_hex = pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()

        _, raw_bytes, sha256 = _create_mock_checkpoint()

        issuer = TrainingReceiptIssuer(private_key=priv, key_id="prod_authority_01")
        evidence = TrainingExecutionEvidence(
            training_run_id="run_prov01",
            job_id="job_prov01",
            simulation_id="sim_prov01",
            completed_at="2026-10-11T00:00:00Z",
            executed_rounds=5,
            executed_epochs=10,
            sample_count=2048,
            observed_metrics={"loss": 0.042},
            artifact_sha256=sha256,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=raw_bytes)

        # In production serving environment: only public key configured, private key absent
        env_overrides = {
            "APP_ENV": "production",
            "CFI_TRAINING_RECEIPT_PUBLIC_KEY": pub_hex,
            "CFI_TRAINING_RECEIPT_KEY_ID": "prod_authority_01",
        }
        with patch.dict(os.environ, env_overrides, clear=False), \
             patch.object(get_settings(), "app_env", "production"), \
             patch.object(get_settings(), "training_receipt_public_key", pub_hex), \
             patch.object(get_settings(), "training_receipt_key_id", "prod_authority_01"):

            # Ensure private key is completely unset
            os.environ.pop("CFI_TRAINING_RECEIPT_PRIVATE_KEY", None)

            # Assert that TrainingReceiptIssuer constructor is NEVER called during verifier operation
            with patch("app.application.services.training_provenance_service.TrainingReceiptIssuer.__init__") as mock_issuer:
                mock_issuer.side_effect = RuntimeError("SECURITY VIOLATION: Verifier must never construct TrainingReceiptIssuer!")
                reset_default_training_verifier()
                verifier = get_default_training_verifier()

                is_valid, msg = verifier.verify_receipt(
                    receipt,
                    candidate_artifact_sha256=sha256,
                    simulation_id="sim_prov01",
                )
                assert is_valid is True, f"Verification failed: {msg}"
                assert "verified successfully" in msg.lower()
                assert mock_issuer.call_count == 0

    def test_prov_02_missing_verification_key_in_production_fails_closed(self):
        """PROV-02: Missing verification key fails closed.

        In production, if no trusted public key is configured, verifier initialization
        must fail closed with a safe, actionable TrainingProvenanceConfigurationError.
        """
        env_overrides = {
            "APP_ENV": "production",
        }
        with patch.dict(os.environ, env_overrides, clear=False), \
             patch.object(get_settings(), "app_env", "production"), \
             patch.object(get_settings(), "training_receipt_public_key", ""):

            os.environ.pop("CFI_TRAINING_RECEIPT_PUBLIC_KEY", None)
            os.environ.pop("CFI_TRAINING_RECEIPT_PUBLIC_KEYS", None)

            reset_default_training_verifier()
            with pytest.raises(TrainingProvenanceConfigurationError, match="Trusted public key is mandatory"):
                TrainingReceiptVerifier()

    def test_prov_03_application_payload_signing_secret_cannot_forge_provenance(self):
        """PROV-03: Application payload secret cannot forge training provenance.

        Configuring only payload_signing_secret does NOT establish trusted provenance in production.
        """
        env_overrides = {
            "APP_ENV": "production",
        }
        with patch.dict(os.environ, env_overrides, clear=False), \
             patch.object(get_settings(), "app_env", "production"), \
             patch.object(get_settings(), "payload_signing_secret", "super_secret_app_key_2026"), \
             patch.object(get_settings(), "training_receipt_public_key", ""):

            os.environ.pop("CFI_TRAINING_RECEIPT_PUBLIC_KEY", None)

            # Verifier cannot establish trust from application secret
            with pytest.raises(TrainingProvenanceConfigurationError):
                TrainingReceiptVerifier()

            # Authority also fails closed when public trust anchor is missing in production
            with pytest.raises(TrainingProvenanceConfigurationError):
                TrainingReceiptAuthority(signing_secret="super_secret_app_key_2026")

        # Even when a public key is configured for the verifier, an attacker attempting
        # to forge a receipt using only payload_signing_secret (HMAC) must be strictly rejected
        trusted_pub = ed25519.Ed25519PrivateKey.generate().public_key()
        trusted_pub_hex = trusted_pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()

        with patch.dict(os.environ, {"APP_ENV": "production", "CFI_TRAINING_RECEIPT_PUBLIC_KEY": trusted_pub_hex}), \
             patch.object(get_settings(), "app_env", "production"), \
             patch.object(get_settings(), "payload_signing_secret", "super_secret_app_key_2026"), \
             patch.object(get_settings(), "training_receipt_public_key", trusted_pub_hex):

            reset_default_training_verifier()
            authority = TrainingReceiptAuthority(signing_secret="super_secret_app_key_2026")
            _, _, sha256 = _create_mock_checkpoint()
            crafted_receipt = {
                "receipt_id": "rcpt_forged_hmac",
                "training_run_id": "run_fake",
                "job_id": "job_fake",
                "model_artifact_sha256": sha256,
                "architecture": "FraudDetectionNN",
                "feature_schema_version": "v1.0",
                "input_dim": len(FEATURE_NAMES),
                "feature_names": list(FEATURE_NAMES),
                "simulation_id": "sim_prod",
                "status": "COMPLETED",
                "executed_rounds": 10,
                "executed_epochs": 10,
                "sample_count": 1000,
                "metrics": {"loss": 0.01},
                "producer_id": "ATTACKER",
                "schema_version": "1.0",
                "created_at": "2026-10-11T00:00:00Z",
                "signature": "abcd" * 16,
                "key_id": "app_secret_key",
                "signature_scheme": "HMAC-SHA256",
            }
            is_valid, reason = authority.verify_receipt(crafted_receipt, candidate_artifact_sha256=sha256)
            assert is_valid is False
            assert "production rejects untrusted" in reason.lower()

    def test_prov_04_untrusted_issuer_key_rejected(self):
        """PROV-04: Untrusted issuer key.

        Signing a receipt with an authorized-looking Ed25519 key not in the verifier's
        trust registry must be rejected.
        """
        trusted_priv = ed25519.Ed25519PrivateKey.generate()
        untrusted_priv = ed25519.Ed25519PrivateKey.generate()

        verifier = TrainingReceiptVerifier(
            trusted_keys={"trusted_issuer_01": trusted_priv.public_key()}
        )
        untrusted_issuer = TrainingReceiptIssuer(
            private_key=untrusted_priv, key_id="untrusted_rogue_issuer"
        )

        _, raw_bytes, sha256 = _create_mock_checkpoint()
        receipt = untrusted_issuer.issue_receipt(
            training_run_id="run_rogue",
            job_id="job_rogue",
            artifact_sha256=sha256,
            simulation_id="sim_rogue",
            executed_rounds=3,
            executed_epochs=3,
            sample_count=500,
            metrics={"loss": 0.05},
            architecture="FraudDetectionNN",
            feature_names=list(FEATURE_NAMES),
        )

        is_valid, reason = verifier.verify_receipt(receipt, candidate_artifact_sha256=sha256)
        assert is_valid is False
        assert "not present in trusted public key registry" in reason

    def test_prov_05_tampered_receipt_rejected(self):
        """PROV-05: Tampered receipt rejected.

        Modifying any security-critical signed field must fail Ed25519 cryptographic verification.
        """
        priv = ed25519.Ed25519PrivateKey.generate()
        verifier = TrainingReceiptVerifier(trusted_keys={"key_01": priv.public_key()})
        issuer = TrainingReceiptIssuer(private_key=priv, key_id="key_01")

        _, _, sha256 = _create_mock_checkpoint()
        receipt = issuer.issue_receipt(
            training_run_id="run_tamper",
            job_id="job_tamper",
            artifact_sha256=sha256,
            simulation_id="sim_tamper",
            executed_rounds=5,
            executed_epochs=5,
            sample_count=1000,
            metrics={"loss": 0.02},
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )

        critical_fields = [
            ("training_run_id", "run_altered"),
            ("job_id", "job_altered"),
            ("model_artifact_sha256", "0" * 64),
            ("architecture", "MaliciousBackdoorModel"),
            ("feature_schema_version", "v999.0"),
            ("simulation_id", "sim_replay_attempt"),
            ("status", "FAILED"),
            ("executed_rounds", 999),
            ("executed_epochs", 999),
            ("sample_count", 999999),
            ("metrics", {"loss": 0.000001}),
            ("producer_id", "ATTACKER_INJECTOR"),
            ("created_at", "1999-01-01T00:00:00Z"),
        ]

        for field_name, altered_value in critical_fields:
            tampered_dict = copy.deepcopy(receipt.to_dict())
            tampered_dict[field_name] = altered_value

            is_valid, reason = verifier.verify_receipt(tampered_dict)
            assert is_valid is False, f"Tampered field '{field_name}' unexpectedly passed verification!"
            assert "signature mismatch" in reason.lower() or "status is" in reason.lower()

    def test_prov_06_modified_artifact_bytes_fails_binding(self):
        """PROV-06: Modified artifact rejected.

        A valid receipt issued for artifact A must fail when presented with artifact B.
        """
        priv = ed25519.Ed25519PrivateKey.generate()
        verifier = TrainingReceiptVerifier(trusted_keys={"key_01": priv.public_key()})
        issuer = TrainingReceiptIssuer(private_key=priv, key_id="key_01")

        _, bytes_a, sha256_a = _create_mock_checkpoint()
        _, bytes_b, sha256_b = _create_mock_checkpoint()
        assert sha256_a != sha256_b

        receipt = issuer.issue_receipt(
            training_run_id="run_a",
            job_id="job_a",
            artifact_sha256=sha256_a,
            simulation_id="sim_art",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=200,
            metrics={"loss": 0.03},
            architecture="FraudDetectionNN",
            feature_names=list(FEATURE_NAMES),
        )

        is_valid, reason = verifier.verify_receipt(receipt, candidate_artifact_sha256=sha256_b)
        assert is_valid is False
        assert "artifact digest mismatch" in reason.lower()

        # Direct candidate bytes mismatch check
        is_valid_bytes, reason_bytes = verifier.verify_receipt(receipt, candidate_artifact_bytes=bytes_b)
        assert is_valid_bytes is False
        assert "artifact digest mismatch" in reason_bytes.lower()

    def test_prov_07_cross_simulation_replay_rejected(self):
        """PROV-07: Cross-simulation replay rejected.

        Reusing a valid receipt issued for simulation A in simulation B must be rejected.
        """
        priv = ed25519.Ed25519PrivateKey.generate()
        verifier = TrainingReceiptVerifier(trusted_keys={"key_01": priv.public_key()})
        issuer = TrainingReceiptIssuer(private_key=priv, key_id="key_01")

        _, _, sha256 = _create_mock_checkpoint()
        receipt = issuer.issue_receipt(
            training_run_id="run_scope",
            job_id="job_scope",
            artifact_sha256=sha256,
            simulation_id="sim_consortium_alpha",
            executed_rounds=4,
            executed_epochs=4,
            sample_count=800,
            metrics={"loss": 0.05},
            architecture="FraudDetectionNN",
            feature_names=list(FEATURE_NAMES),
        )

        is_valid, reason = verifier.verify_receipt(
            receipt,
            candidate_artifact_sha256=sha256,
            simulation_id="sim_consortium_beta",
        )
        assert is_valid is False
        assert "simulation scope mismatch" in reason.lower()

    def test_prov_08_wrong_feature_schema_rejected(self):
        """PROV-08: Wrong feature schema, architecture, and input dimension rejected.

        Verifies that candidates with incompatible schemas are rejected even with valid signatures.
        """
        priv = ed25519.Ed25519PrivateKey.generate()
        verifier = TrainingReceiptVerifier(trusted_keys={"key_01": priv.public_key()})
        issuer = TrainingReceiptIssuer(private_key=priv, key_id="key_01")

        _, _, sha256 = _create_mock_checkpoint()
        names = ["amount", "age", "tx_count", "risk_score"]
        receipt = issuer.issue_receipt(
            training_run_id="run_schema",
            job_id="job_schema",
            artifact_sha256=sha256,
            simulation_id="sim_schema",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=100,
            metrics={"loss": 0.05},
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            input_dim=len(names),
            feature_names=names,
        )

        # 1. Feature schema version mismatch
        ok, reason = verifier.verify_receipt(
            receipt, candidate_artifact_sha256=sha256, expected_feature_schema_version="v2.0"
        )
        assert ok is False
        assert "feature schema version mismatch" in reason.lower()

        # 2. Architecture mismatch
        ok, reason = verifier.verify_receipt(
            receipt, candidate_artifact_sha256=sha256, expected_architecture="TransformerFraudModel"
        )
        assert ok is False
        assert "model architecture mismatch" in reason.lower()

        # 3. Input dimension mismatch
        ok, reason = verifier.verify_receipt(
            receipt, candidate_artifact_sha256=sha256, expected_input_dim=15
        )
        assert ok is False
        assert "input dimension mismatch" in reason.lower()

        # 4. Feature ordering / names mismatch
        ok, reason = verifier.verify_receipt(
            receipt, candidate_artifact_sha256=sha256, expected_feature_names=["age", "amount", "tx_count", "risk_score"]
        )
        assert ok is False
        assert "feature names or ordering mismatch" in reason.lower()

    def test_prov_09_missing_receipt_fields_cannot_be_silently_reconstructed(self):
        """PROV-09: Missing receipt fields cannot be silently reconstructed.

        Removing mandatory signed fields must raise KeyError on deserialization
        and fail verification closed.
        """
        priv = ed25519.Ed25519PrivateKey.generate()
        verifier = TrainingReceiptVerifier(trusted_keys={"key_01": priv.public_key()})
        issuer = TrainingReceiptIssuer(private_key=priv, key_id="key_01")

        _, _, sha256 = _create_mock_checkpoint()
        receipt = issuer.issue_receipt(
            training_run_id="run_mandatory",
            job_id="job_mandatory",
            artifact_sha256=sha256,
            simulation_id="sim_mandatory",
            executed_rounds=3,
            executed_epochs=3,
            sample_count=500,
            metrics={"loss": 0.02},
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="AUTHORIZED_EXECUTOR",
        )

        mandatory_fields = [
            "status",
            "executed_rounds",
            "executed_epochs",
            "sample_count",
            "architecture",
            "input_dim",
            "feature_schema_version",
            "feature_names",
            "producer_id",
            "signature",
            "key_id",
        ]

        base_dict = receipt.to_dict()
        for field in mandatory_fields:
            corrupted = copy.deepcopy(base_dict)
            del corrupted[field]

            with pytest.raises(KeyError, match=f"Mandatory receipt fields missing.*{field}"):
                TrainingExecutionReceipt.from_dict(corrupted)

            ok, reason = verifier.verify_receipt(corrupted)
            assert ok is False
            assert "receipt format invalid" in reason.lower()

    def test_prov_10_fabricated_execution_metadata_rejected(self):
        """PROV-10: Fabricated execution metadata rejected.

        Providing an untrained model with fabricated metadata without a cryptographically
        valid receipt leaves the candidate UNVERIFIED and non-promotable.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            state_dict, raw_bytes, _ = _create_mock_checkpoint()

            with patch.object(get_settings(), "app_env", "production"):
                metadata = registry.save_version(
                    simulation_id="sim_prov10",
                    state_dict=state_dict,
                    metrics={"loss": 0.0001, "epochs": 500},
                    is_promoted=False,
                    provenance_type="TRAINING_RECORDED",
                    training_run_id="fake_unauthorized_run",
                    training_evidence={"loss": 0.0001, "epochs": 500, "sample_count": 1000000},
                    artifact_bytes=raw_bytes,
                )

                assert metadata["provenance_type"] == "UNVERIFIED"
                assert metadata["is_production_eligible"] is False
                assert "CALLER_NUMERIC_EVIDENCE_UNAUTHENTICATED" in metadata["attestation_status"]

            # Also verify that TrainingReceiptIssuer rejects production issuance with missing/unobserved work
            issuer = TrainingReceiptIssuer()
            with patch.object(get_settings(), "app_env", "production"), \
                 pytest.raises(ValueError, match="Production receipt issuance requires explicit executed_rounds"):
                issuer.issue_receipt(
                    training_run_id="run_prod_no_work",
                    model_bytes=raw_bytes,
                    sample_count=100,
                    metrics={"loss": 0.01},
                )

    def test_prov_11_legitimate_local_training_produces_verifiable_receipt(self):
        """PROV-11: Legitimate local training produces verifiable receipt through trusted path.

        Runs genuine optimization, records actual observed metrics and sample counts into
        TrainingExecutionEvidence, issues receipt, and verifies using public-key-only verifier.
        """
        settings = get_settings()
        model_svc = ModelService(settings)
        model = model_svc.create_model(dp_compatible=True)

        # Synthetic genuine dataset
        rng = np.random.default_rng(42)
        n_samples = 128
        X_train = rng.normal(size=(n_samples, NUM_FEATURES)).astype(np.float32)
        y_train = (X_train[:, 0] > 0).astype(np.float32)

        # Execute genuine local training with Adam
        trained_model, loss_history, _ = model_svc.train_local(
            model=model,
            X_train=X_train,
            y_train=y_train,
            epochs=3,
            batch_size=32,
            learning_rate=0.01,
        )

        assert len(loss_history) == 3
        assert loss_history[-1] < loss_history[0]  # Loss actually decreased through optimization
        assert math.isfinite(loss_history[-1])

        # Serialize actual trained weights
        buf = io.BytesIO()
        torch.save(trained_model.state_dict(), buf)
        trained_bytes = buf.getvalue()
        artifact_sha = hashlib.sha256(trained_bytes).hexdigest()

        # Capture observed execution evidence
        evidence = TrainingExecutionEvidence(
            training_run_id="run_genuine_local_01",
            job_id="job_genuine_local_01",
            simulation_id="sim_local_genuine",
            completed_at="2026-10-11T00:00:00Z",
            executed_rounds=1,
            executed_epochs=3,
            sample_count=n_samples,
            observed_metrics={"loss": float(loss_history[-1])},
            artifact_sha256=artifact_sha,
            architecture="FraudDetectionModel-GroupNorm",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="LOCAL_SILO_TRAINING_WORKER",
            status="COMPLETED",
        )

        priv = ed25519.Ed25519PrivateKey.generate()
        issuer = TrainingReceiptIssuer(private_key=priv, key_id="local_authority_key")
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=trained_bytes)

        # Verify with independent public-key-only verifier
        verifier = TrainingReceiptVerifier(
            trusted_keys={"local_authority_key": priv.public_key()}
        )
        ok, reason = verifier.verify_receipt(
            receipt,
            candidate_artifact_sha256=artifact_sha,
            simulation_id="sim_local_genuine",
            expected_architecture="FraudDetectionModel-GroupNorm",
            expected_input_dim=len(FEATURE_NAMES),
        )
        assert ok is True
        assert "verified successfully" in reason.lower()

    def test_prov_12_legacy_hmac_isolated_from_production(self):
        """PROV-12: Legacy HMAC isolation.

        Legacy HMAC receipts must be rejected in production.
        In development, legacy HMAC receipts may be verified for backward compatibility,
        labeled as dev-only.
        """
        secret = "dev_hmac_shared_secret_2026"
        auth = TrainingReceiptAuthority(signing_secret=secret)
        _, _, sha256 = _create_mock_checkpoint()

        receipt_dict = {
            "receipt_id": "rcpt_legacy_hmac",
            "training_run_id": "run_legacy",
            "job_id": "job_legacy",
            "model_artifact_sha256": sha256,
            "architecture": "FraudDetectionNN",
            "feature_schema_version": "v1.0",
            "input_dim": len(FEATURE_NAMES),
            "feature_names": list(FEATURE_NAMES),
            "simulation_id": "sim_legacy",
            "status": "COMPLETED",
            "executed_rounds": 1,
            "executed_epochs": 1,
            "sample_count": 100,
            "metrics": {"loss": 0.05},
            "producer_id": "LEGACY_ISSUER",
            "schema_version": "1.0",
            "created_at": "2026-10-11T00:00:00Z",
            "key_id": DEFAULT_TRAINING_KEY_ID,
            "signature": "",
            "signature_scheme": "HMAC-SHA256",
        }
        # Compute HMAC signature
        canonical = TrainingExecutionReceipt.from_dict({**receipt_dict, "signature": "temp"}).canonical_bytes()
        signing_key = auth._get_signing_key()
        import hmac as pyhmac
        receipt_dict["signature"] = pyhmac.new(signing_key, canonical, hashlib.sha256).hexdigest()

        # In production: MUST be rejected
        with patch.object(get_settings(), "app_env", "production"):
            ok_prod, reason_prod = auth.verify_receipt(receipt_dict, candidate_artifact_sha256=sha256)
            assert ok_prod is False
            assert "production rejects untrusted or non-asymmetric receipt" in reason_prod.lower()

        # In development: succeeds with explicit dev-only status
        with patch.object(get_settings(), "app_env", "development"):
            ok_dev, reason_dev = auth.verify_receipt(receipt_dict, candidate_artifact_sha256=sha256)
            assert ok_dev is True
            assert "dev only" in reason_dev.lower()

    def test_prov_13_key_revocation(self):
        """PROV-13: Key revocation.

        Revoking an issuer key ID causes previously verifiable receipts signed by that key
        to be rejected.
        """
        priv = ed25519.Ed25519PrivateKey.generate()
        verifier = TrainingReceiptVerifier(trusted_keys={"key_compromised": priv.public_key()})
        issuer = TrainingReceiptIssuer(private_key=priv, key_id="key_compromised")

        _, _, sha256 = _create_mock_checkpoint()
        receipt = issuer.issue_receipt(
            training_run_id="run_revoc",
            job_id="job_revoc",
            artifact_sha256=sha256,
            simulation_id="sim_revoc",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=100,
            metrics={"loss": 0.01},
            architecture="FraudDetectionNN",
            feature_names=list(FEATURE_NAMES),
        )

        # Before revocation: valid
        ok, reason = verifier.verify_receipt(receipt, candidate_artifact_sha256=sha256)
        assert ok is True

        # Revoke key
        verifier.revoke_key("key_compromised", reason="Security incident: private key leak")
        assert not verifier.is_key_trusted("key_compromised")

        # After revocation: rejected
        ok_after, reason_after = verifier.verify_receipt(receipt, candidate_artifact_sha256=sha256)
        assert ok_after is False
        assert "explicitly revoked" in reason_after.lower()

    def test_prov_14_key_rotation(self):
        """PROV-14: Key rotation.

        Configuring an old and new authorized public key allows both historical and
        new receipts to verify until retirement.
        """
        priv_v1 = ed25519.Ed25519PrivateKey.generate()
        priv_v2 = ed25519.Ed25519PrivateKey.generate()
        priv_untrusted = ed25519.Ed25519PrivateKey.generate()

        verifier = TrainingReceiptVerifier(
            trusted_keys={
                "key_v1": priv_v1.public_key(),
                "key_v2": priv_v2.public_key(),
            }
        )
        issuer_v1 = TrainingReceiptIssuer(private_key=priv_v1, key_id="key_v1")
        issuer_v2 = TrainingReceiptIssuer(private_key=priv_v2, key_id="key_v2")
        issuer_rogue = TrainingReceiptIssuer(private_key=priv_untrusted, key_id="key_v3")

        _, _, sha256 = _create_mock_checkpoint()

        rcpt_v1 = issuer_v1.issue_receipt(
            training_run_id="run_v1",
            job_id="job_v1",
            artifact_sha256=sha256,
            simulation_id="sim_rot",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            metrics={"loss": 0.01},
            architecture="FraudDetectionNN",
            feature_names=list(FEATURE_NAMES),
        )
        rcpt_v2 = issuer_v2.issue_receipt(
            training_run_id="run_v2",
            job_id="job_v2",
            artifact_sha256=sha256,
            simulation_id="sim_rot",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            metrics={"loss": 0.01},
            architecture="FraudDetectionNN",
            feature_names=list(FEATURE_NAMES),
        )
        rcpt_rogue = issuer_rogue.issue_receipt(
            training_run_id="run_v3",
            job_id="job_v3",
            artifact_sha256=sha256,
            simulation_id="sim_rot",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            metrics={"loss": 0.01},
            architecture="FraudDetectionNN",
            feature_names=list(FEATURE_NAMES),
        )

        ok1, _ = verifier.verify_receipt(rcpt_v1, candidate_artifact_sha256=sha256)
        ok2, _ = verifier.verify_receipt(rcpt_v2, candidate_artifact_sha256=sha256)
        ok3, reason3 = verifier.verify_receipt(rcpt_rogue, candidate_artifact_sha256=sha256)

        assert ok1 is True
        assert ok2 is True
        assert ok3 is False
        assert "not present in trusted public key registry" in reason3

    def test_prov_15_issuer_verifier_separation_at_import_and_init(self):
        """PROV-15: Issuer/verifier separation at import and initialization time.

        Asserts that get_default_training_verifier() does not access private keys or construct issuer.
        """
        pub = ed25519.Ed25519PrivateKey.generate().public_key()
        pub_hex = pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()

        with patch.dict(os.environ, {"CFI_TRAINING_RECEIPT_PUBLIC_KEY": pub_hex, "APP_ENV": "production"}), \
             patch.object(get_settings(), "app_env", "production"), \
             patch.object(get_settings(), "training_receipt_public_key", pub_hex):

            reset_default_training_verifier()

            with patch("app.application.services.training_provenance_service.TrainingReceiptIssuer") as mock_issuer:
                verifier = get_default_training_verifier()
                assert isinstance(verifier, TrainingReceiptVerifier)
                assert mock_issuer.call_count == 0

    def test_prov_16_registry_compatibility(self):
        """PROV-16: Registry compatibility.

        Registering a model candidate with a valid receipt preserves provenance trust.
        Forged numeric evidence or tampered artifact bytes remain untrusted.
        """
        priv = ed25519.Ed25519PrivateKey.generate()
        pub = priv.public_key()
        pub_hex = pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ).hex()

        issuer = TrainingReceiptIssuer(private_key=priv, key_id="reg_auth_01")
        state_dict, raw_bytes, sha256 = _create_mock_checkpoint()

        evidence = TrainingExecutionEvidence(
            training_run_id="run_reg_compat",
            job_id="job_reg_compat",
            simulation_id="sim_reg_compat",
            completed_at="2026-10-11T00:00:00Z",
            executed_rounds=3,
            executed_epochs=3,
            sample_count=600,
            observed_metrics={"loss": 0.04},
            artifact_sha256=sha256,
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
        )
        receipt = issuer.issue_receipt(evidence=evidence, model_bytes=raw_bytes)

        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)

            with patch.dict(os.environ, {"CFI_TRAINING_RECEIPT_PUBLIC_KEY": pub_hex, "CFI_TRAINING_RECEIPT_KEY_ID": "reg_auth_01", "APP_ENV": "production"}), \
                 patch.object(get_settings(), "app_env", "production"), \
                 patch.object(get_settings(), "training_receipt_public_key", pub_hex), \
                 patch.object(get_settings(), "training_receipt_key_id", "reg_auth_01"):

                reset_default_training_verifier()

                # Valid receipt registration
                entry = registry.save_version(
                    simulation_id="sim_reg_compat",
                    state_dict=state_dict,
                    metrics={"loss": 0.04},
                    is_promoted=False,
                    provenance_type="TRAINING_RECORDED",
                    training_run_id="run_reg_compat",
                    training_receipt=receipt,
                    artifact_bytes=raw_bytes,
                )

                assert entry["provenance_type"] == "TRAINING_RECORDED"
                assert entry["is_production_eligible"] is True
                assert entry["attestation_status"] == "RECEIPT_VERIFIED_TRAINING_AUTHORITY"
                assert entry["training_receipt"]["signature"] == receipt.signature

                # Tampered artifact bytes registration
                tampered_bytes = raw_bytes + b"_corrupted"
                entry_tampered = registry.save_version(
                    simulation_id="sim_reg_compat",
                    state_dict=state_dict,
                    metrics={"loss": 0.04},
                    is_promoted=False,
                    provenance_type="TRAINING_RECORDED",
                    training_run_id="run_reg_compat",
                    training_receipt=receipt,
                    artifact_bytes=tampered_bytes,
                )
                assert entry_tampered["provenance_type"] == "UNVERIFIED"
                assert entry_tampered["is_production_eligible"] is False
                assert "INVALID_RECEIPT" in entry_tampered["attestation_status"]
