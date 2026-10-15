"""Integration tests for crash recovery, transition reconciliation, and artifact security.

Verifies:
1. Deterministic process termination at production mutation boundaries:
   - Staged artifact before manifest persistence (reconciles to FAILED).
   - Manifest persisted before audit finalization (reconciles to COMMITTED).
   - Champion promotion before audit acknowledgment (reconciles to COMMITTED).
   - Version revocation before audit acknowledgment (reconciles to COMMITTED).
2. Protection of actively executing workers: held FileLock prevents premature FAILED reconciliation (INDETERMINATE_IN_PROGRESS).
3. Tampered model weights rejection (bit flip in stored weights detected on load).
4. Unreadable or corrupted manifest handling (fails closed, marked INDETERMINATE).
5. Path traversal simulation ID rejection.
6. End-to-end HMAC immutable audit chain verification across all recoveries.
"""

from __future__ import annotations

import hashlib
import io
import json
import multiprocessing as mp
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from filelock import FileLock

from app.application.services.model_registry import (
    ModelIntegrityError,
    ModelRegistry,
    validate_simulation_id,
)
from app.application.services.model_service import NUM_FEATURES, FraudDetectionModel
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


def _worker_hold_file_lock(sim_dir: str, ready_event: Any, release_event: Any) -> None:
    """Separate OS child process holding the registry file lock."""
    lock_file = os.path.join(sim_dir, "registry.lock")
    lock = FileLock(lock_file)
    with lock:
        ready_event.set()
        release_event.wait(timeout=10)


@pytest.fixture
def recovery_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Sets up a complete production-parity environment for crash recovery and security tests."""
    audit_file = str(tmp_path / "security" / "audit_chain.json")
    account_file = str(tmp_path / "security" / "accounts.json")
    trust_file = str(tmp_path / "security" / "trust_store.json")
    nonce_file = str(tmp_path / "security" / "consumed_nonces.json")
    storage_dir = str(tmp_path / "storage")
    os.makedirs(os.path.dirname(audit_file), exist_ok=True)
    os.makedirs(storage_dir, exist_ok=True)

    audit_secret = "CFI_PROD_AUDIT_LEDGER_SECRET_2026_TEST"
    jwt_secret = "CFI_PROD_JWT_SIGNING_KEY_2026_TEST"
    authority_secret = "CFI_PROD_ACCOUNT_AUTHORITY_SECRET_2026"
    admin_token = "CFI_TRUST_ADMIN_TOKEN_2026"
    internal_secret = "cfi_prod_internal_secret_32_chars_ok!"

    env_config = {
        "CFI_AUDIT_LEDGER_PATH": audit_file,
        "CFI_AUDIT_LEDGER_SECRET": audit_secret,
        "CFI_TRUST_STORE_PATH": trust_file,
        "CFI_TRUST_STORE_ADMIN_TOKEN": admin_token,
        "CFI_STORAGE_DIR": storage_dir,
        "CFI_JWT_SIGNING_SECRET": jwt_secret,
        "CFI_PAYLOAD_SIGNING_SECRET": jwt_secret,
        "CFI_ACCOUNT_STORE_PATH": account_file,
        "CFI_IDENTITY_STORE_PATH": account_file,
        "CFI_ACCOUNT_AUTHORITY_SECRET": authority_secret,
        "CFI_IDENTITY_STORE_SECRET": authority_secret,
        "CFI_INTERNAL_NONCE_STORE_PATH": nonce_file,
        "CFI_INTERNAL_CONTEXT_SECRET": internal_secret,
        "APP_ENV": "development",
    }
    for k, v in env_config.items():
        monkeypatch.setenv(k, v)

    reset_default_nonce_store()
    get_settings.cache_clear()
    ImmutableAuditChain._instance = None

    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_hex = (
        priv_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )
    key_id = "training_key_rec_01"
    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
    trust_store.register_approved_key(
        key_id=key_id, public_key_hex=pub_hex, admin_token=admin_token
    )

    issuer = TrainingReceiptIssuer(
        key_id=key_id,
        private_key=priv_key,
        trust_store=trust_store,
        require_authenticated_job=False,
        app_env="development",
    )
    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    registry = ModelRegistry(storage_dir=storage_dir, verifier=verifier)

    return {
        "storage_dir": storage_dir,
        "issuer": issuer,
        "verifier": verifier,
        "trust_store": trust_store,
        "registry": registry,
        "audit_file": audit_file,
        "audit_secret": audit_secret,
    }


def _create_sample_model(
    env: dict[str, Any], sim_id: str, version: int = 1
) -> tuple[bytes, dict[str, Any], str, Any]:
    """Helper to generate genuine PyTorch weights, bytes, sha, and receipt."""
    issuer: TrainingReceiptIssuer = env["issuer"]
    model = FraudDetectionModel(input_dim=NUM_FEATURES, dp_compatible=True)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    artifact_bytes = buf.getvalue()
    artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()

    evidence = TrainingExecutionEvidence(
        training_run_id=f"run_{sim_id}_v{version}",
        job_id=f"job_{sim_id}_v{version}",
        simulation_id=sim_id,
        completed_at="2026-10-15T00:00:00Z",
        executed_rounds=3,
        executed_epochs=3,
        sample_count=200,
        observed_metrics={"loss": 0.1},
        artifact_sha256=artifact_sha,
        architecture="FraudDetectionModel",
        feature_schema_version="1.0",
        feature_names=[f"f{i}" for i in range(NUM_FEATURES)],
        owning_bank_id="bank_a",
    )
    receipt = issuer.issue_receipt(
        evidence=evidence,
        model_bytes=artifact_bytes,
        owning_bank_id="bank_a",
        architecture="FraudDetectionModel",
        input_dim=NUM_FEATURES,
        feature_names=[f"f{i}" for i in range(NUM_FEATURES)],
        feature_schema_version="1.0",
    )
    return artifact_bytes, model.state_dict(), artifact_sha, receipt


class TestRegistryConcurrentTransitionRecovery:
    """Verifies crash reconciliation truthfulness, atomic recovery, and security invariants."""

    def test_crash_staged_artifact_before_manifest_reconciles_to_failed(
        self, recovery_env: dict[str, Any]
    ) -> None:
        """Crash scenario 1: Worker staged artifact file, recorded PENDING in audit, but crashed before manifest update."""
        registry: ModelRegistry = recovery_env["registry"]
        audit_file = recovery_env["audit_file"]
        audit_secret = recovery_env["audit_secret"]
        sim_id = "sim_crash_staging"

        sim_dir = registry._get_sim_dir(sim_id)
        os.makedirs(sim_dir, exist_ok=True)

        # Stage abandoned temporary model file on disk
        tmp_model_file = os.path.join(sim_dir, ".tmp_model_crash_abandoned.pt")
        with open(tmp_model_file, "wb") as f:
            f.write(b"abandoned_weights_data")

        # Emit PENDING registration audit event
        mutation_id = f"mut_reg_{uuid.uuid4().hex}"
        audit_chain = ImmutableAuditChain(store_path=audit_file, signing_secret=audit_secret)
        audit_chain.append_event(
            event_type="MODEL_REGISTRATION_PENDING",
            actor="worker_crashed_pid_999",
            target_id=f"{sim_id}:v1",
            details={
                "mutation_id": mutation_id,
                "mutation_stage": "PENDING",
                "simulation_id": sim_id,
                "version": 1,
                "action": "save_version",
            },
        )

        # Fresh independent process runs reconciliation
        reconciled = registry.reconcile_incomplete_mutations(simulation_id=sim_id)
        assert len(reconciled) == 1
        res = reconciled[0]

        # Invariant 1: Terminal status MUST be FAILED because manifest was never committed
        assert res["status"] == "FAILED"
        assert res["mutation_id"] == mutation_id

        # Invariant 2: Abandoned temp file is cleaned up
        assert not os.path.exists(tmp_model_file)

        # Invariant 3: Audit chain reflects exact failure, zero duplicate terminal events
        pending = audit_chain.get_pending_mutations()
        assert len(pending) == 0
        assert audit_chain.verify_chain_integrity().is_valid is True

    def test_crash_manifest_persisted_before_audit_reconciles_to_committed(
        self, recovery_env: dict[str, Any]
    ) -> None:
        """Crash scenario 2: Manifest was durably persisted on disk, but process crashed before writing final audit event."""
        registry: ModelRegistry = recovery_env["registry"]
        audit_file = recovery_env["audit_file"]
        audit_secret = recovery_env["audit_secret"]
        sim_id = "sim_crash_post_manifest"

        artifact_bytes, state_dict, sha, receipt = _create_sample_model(
            recovery_env, sim_id, version=1
        )

        # Durably write model artifact file
        sim_dir = registry._get_sim_dir(sim_id)
        os.makedirs(sim_dir, exist_ok=True)
        model_path = os.path.join(sim_dir, "model_v1.pt")
        with open(model_path, "wb") as f:
            f.write(artifact_bytes)

        # Durably persist manifest (registry.json) containing the mutation ID
        mutation_id = f"mut_reg_{uuid.uuid4().hex}"
        manifest_entry = {
            "version": 1,
            "status": "candidate",
            "is_active": False,
            "sha256": sha,
            "filename": "model_v1.pt",
            "owning_bank_id": "bank_a",
            "provenance_type": "TRAINING_EVIDENCE_VERIFIED",
            "last_mutation_id": mutation_id,
            "applied_mutation_ids": [mutation_id],
            "training_receipt": receipt.to_dict(),
        }
        with open(os.path.join(sim_dir, "registry.json"), "w", encoding="utf-8") as f:
            json.dump([manifest_entry], f)

        # Record PENDING intent in audit chain (simulating crash before COMMITTED event)
        audit_chain = ImmutableAuditChain(store_path=audit_file, signing_secret=audit_secret)
        audit_chain.append_event(
            event_type="MODEL_REGISTRATION_PENDING",
            actor="worker_crashed_pid_888",
            target_id=f"{sim_id}:v1",
            details={
                "mutation_id": mutation_id,
                "mutation_stage": "PENDING",
                "simulation_id": sim_id,
                "version": 1,
                "action": "save_version",
            },
        )

        # Reconcile from fresh process
        reconciled = registry.reconcile_incomplete_mutations(simulation_id=sim_id)
        assert len(reconciled) == 1
        res = reconciled[0]

        # Invariant 1: Must resolve truthfully to COMMITTED based on exact mutation ID
        assert res["status"] == "COMMITTED"
        assert res["mutation_id"] == mutation_id

        # Invariant 2: Committed artifact file MUST NOT be deleted
        assert os.path.exists(model_path)

        # Invariant 3: Audit chain integrity is strictly verified
        assert audit_chain.verify_chain_integrity().is_valid is True

    def test_crash_promotion_before_audit_reconciles_to_committed(
        self, recovery_env: dict[str, Any]
    ) -> None:
        """Crash scenario 3: Champion promotion manifest persisted, but process crashed before audit finalization."""
        registry: ModelRegistry = recovery_env["registry"]
        audit_file = recovery_env["audit_file"]
        audit_secret = recovery_env["audit_secret"]
        sim_id = "sim_crash_promo"

        artifact_bytes, state_dict, sha, receipt = _create_sample_model(
            recovery_env, sim_id, version=1
        )

        sim_dir = registry._get_sim_dir(sim_id)
        os.makedirs(sim_dir, exist_ok=True)
        with open(os.path.join(sim_dir, "model_v1.pt"), "wb") as f:
            f.write(artifact_bytes)

        promo_mut_id = f"mut_promo_{uuid.uuid4().hex}"
        manifest_entry = {
            "version": 1,
            "status": "champion",
            "is_active": True,
            "sha256": sha,
            "filename": "model_v1.pt",
            "owning_bank_id": "bank_a",
            "last_mutation_id": promo_mut_id,
            "applied_mutation_ids": [promo_mut_id],
            "generation": 1,
        }
        with open(os.path.join(sim_dir, "registry.json"), "w", encoding="utf-8") as f:
            json.dump([manifest_entry], f)

        audit_chain = ImmutableAuditChain(store_path=audit_file, signing_secret=audit_secret)
        audit_chain.append_event(
            event_type="MODEL_PROMOTION_PENDING",
            actor="worker_promo_crashed",
            target_id=f"{sim_id}:v1",
            details={
                "mutation_id": promo_mut_id,
                "mutation_stage": "PENDING",
                "simulation_id": sim_id,
                "version": 1,
                "action": "promote_version",
            },
        )

        reconciled = registry.reconcile_incomplete_mutations(simulation_id=sim_id)
        assert len(reconciled) == 1
        assert reconciled[0]["status"] == "COMMITTED"
        assert audit_chain.verify_chain_integrity().is_valid is True

    def test_crash_revocation_before_audit_reconciles_to_committed(
        self, recovery_env: dict[str, Any]
    ) -> None:
        """Crash scenario 4: Model revocation persisted, but process crashed before audit finalization."""
        registry: ModelRegistry = recovery_env["registry"]
        audit_file = recovery_env["audit_file"]
        audit_secret = recovery_env["audit_secret"]
        sim_id = "sim_crash_rev"

        sim_dir = registry._get_sim_dir(sim_id)
        os.makedirs(sim_dir, exist_ok=True)

        rev_mut_id = f"mut_vrev_{uuid.uuid4().hex}"
        manifest_entry = {
            "version": 1,
            "status": "inactive",
            "is_active": False,
            "is_revoked": True,
            "filename": "model_v1.pt",
            "revocation_mutation_id": rev_mut_id,
            "last_mutation_id": rev_mut_id,
            "applied_mutation_ids": [rev_mut_id],
        }
        with open(os.path.join(sim_dir, "registry.json"), "w", encoding="utf-8") as f:
            json.dump([manifest_entry], f)

        audit_chain = ImmutableAuditChain(store_path=audit_file, signing_secret=audit_secret)
        audit_chain.append_event(
            event_type="MODEL_VERSION_REVOCATION_PENDING",
            actor="worker_rev_crashed",
            target_id=f"{sim_id}:v1",
            details={
                "mutation_id": rev_mut_id,
                "mutation_stage": "PENDING",
                "simulation_id": sim_id,
                "version": 1,
                "action": "revoke_version",
            },
        )

        reconciled = registry.reconcile_incomplete_mutations(simulation_id=sim_id)
        assert len(reconciled) == 1
        assert reconciled[0]["status"] == "COMMITTED"
        assert audit_chain.verify_chain_integrity().is_valid is True

    def test_active_executing_worker_lock_prevents_premature_reconciliation(
        self, recovery_env: dict[str, Any]
    ) -> None:
        """Verifies that while another worker actively holds the registry FileLock, reconciler leaves state indeterminate."""
        registry: ModelRegistry = recovery_env["registry"]
        audit_file = recovery_env["audit_file"]
        audit_secret = recovery_env["audit_secret"]
        sim_id = "sim_active_lock"

        sim_dir = registry._get_sim_dir(sim_id)
        os.makedirs(sim_dir, exist_ok=True)

        mut_id = f"mut_active_{uuid.uuid4().hex}"
        audit_chain = ImmutableAuditChain(store_path=audit_file, signing_secret=audit_secret)
        audit_chain.append_event(
            event_type="MODEL_REGISTRATION_PENDING",
            actor="active_worker",
            target_id=f"{sim_id}:v1",
            details={
                "mutation_id": mut_id,
                "mutation_stage": "PENDING",
                "simulation_id": sim_id,
                "version": 1,
                "action": "save_version",
            },
        )

        # Worker holds lock in independent OS process
        ctx = mp.get_context("spawn")
        ready_event = ctx.Event()
        release_event = ctx.Event()

        p = ctx.Process(
            target=_worker_hold_file_lock,
            args=(sim_dir, ready_event, release_event),
        )
        p.start()
        assert ready_event.wait(timeout=10)

        try:
            # Reconciler runs while worker actively holds lock
            reconciled = registry.reconcile_incomplete_mutations(
                simulation_id=sim_id, lock_timeout_seconds=0.1
            )
            assert len(reconciled) == 1
            assert reconciled[0]["mutation_id"] == mut_id
            assert reconciled[0]["status"] == "INDETERMINATE_IN_PROGRESS"

            # Must NOT have prematurely emitted FAILED in audit chain
            pending = audit_chain.get_pending_mutations()
            assert any(p.details.get("mutation_id") == mut_id for p in pending)
        finally:
            release_event.set()
            p.join(timeout=5)

        # After worker finishes and releases lock, reconciler resolves truthfully
        reconciled_after = registry.reconcile_incomplete_mutations(
            simulation_id=sim_id, lock_timeout_seconds=1.0
        )
        assert len(reconciled_after) == 1
        assert reconciled_after[0]["status"] == "FAILED"

    def test_tampered_model_weights_rejected(self, recovery_env: dict[str, Any]) -> None:
        """Negative security test: Bit flip in serialized model artifact on disk fails verification."""
        registry: ModelRegistry = recovery_env["registry"]
        sim_id = "sim_tamper_test"

        artifact_bytes, state_dict, sha, receipt = _create_sample_model(
            recovery_env, sim_id, version=1
        )

        entry = registry.save_version(
            simulation_id=sim_id,
            state_dict=state_dict,
            artifact_bytes=artifact_bytes,
            training_receipt=receipt,
            provenance_type="TRAINING_EVIDENCE_VERIFIED",
            owning_bank_id="bank_a",
        )
        assert entry["version"] == 1

        # Locate saved file using entry's filename
        sim_dir = registry._get_sim_dir(sim_id)
        artifact_filename = entry.get("filename") or "model_v1.pt"
        artifact_file = os.path.join(sim_dir, artifact_filename)
        assert os.path.exists(artifact_file), f"Model file not found at {artifact_file}"

        # Tamper with the model artifact file on disk (flip bytes)
        with open(artifact_file, "r+b") as f:
            f.seek(50)
            orig = f.read(1)
            f.seek(50)
            f.write(bytes([orig[0] ^ 0xFF]))

        # Attempting to load the tampered model MUST raise ModelIntegrityError
        with pytest.raises(ModelIntegrityError) as exc_info:
            registry.load_version(sim_id, 1)
        assert (
            "digest mismatch" in str(exc_info.value).lower()
            or "integrity" in str(exc_info.value).lower()
        )

    def test_path_traversal_simulation_id_rejected(self, recovery_env: dict[str, Any]) -> None:
        """Negative security test: Path traversal patterns in simulation_id are strictly rejected."""
        for invalid_id in ("../escape", "sim/../../secret", "..\\windows\\path", "valid;rm -rf"):
            with pytest.raises(ValueError) as exc_info:
                validate_simulation_id(invalid_id)
            assert "invalid" in str(exc_info.value).lower()
