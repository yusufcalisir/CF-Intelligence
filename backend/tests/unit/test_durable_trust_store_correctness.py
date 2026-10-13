"""Durable Trust Store Correctness Suite (TRUST-01 to TRUST-12).

Verifies that:
- TRUST-01: Authorized key registration persists across process restarts.
- TRUST-02: Unauthorized registration or trust modification is rejected.
- TRUST-03: Revocation is durably persisted and cannot be bypassed.
- TRUST-04: Existing verifiers reject a newly revoked key without restarting.
- TRUST-05: Multiple independent processes observe consistent authoritative trust.
- TRUST-06: Concurrent registration and revocation cannot lose updates or resurrect revoked keys.
- TRUST-07: Concurrent trust operations do not deadlock.
- TRUST-08: Malformed or corrupted trust persistence fails closed.
- TRUST-09: Crash during persistence cannot produce a partially trusted or permissive state.
- TRUST-10: Recovery and reload preserve revocation and authorization.
- TRUST-11: Unauthorized administrative operations cannot modify trusted security state.
- TRUST-12: Genuine valid signatures continue to verify with active trusted keys, while revoked or untrusted signatures are rejected.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import uuid
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingProvenanceSecurityError,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings


def _generate_ed25519_key() -> tuple[ed25519.Ed25519PrivateKey, str, str]:
    """Generate raw Ed25519 key pair with hex string public key."""
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key()
    pub_hex = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = f"key_{uuid.uuid4().hex[:8]}"
    return priv, pub_hex, kid


# ---------------------------------------------------------------------------
# Multiprocessing Worker Top-Level Entry Points (for Windows spawn)
# ---------------------------------------------------------------------------


def _mp_register_keys_worker(
    trust_path: str,
    admin_token: str | None,
    key_prefix: str,
    count: int,
    out_q: mp.Queue[Any],
) -> None:
    """Multiprocessing worker registering unique keys concurrently."""
    try:
        store = DurableTrustStore(trust_file_path=trust_path, admin_token=admin_token)
        registered = []
        for i in range(count):
            _, pub_hex, _ = _generate_ed25519_key()
            kid = f"{key_prefix}_{i}"
            store.register_approved_key(kid, pub_hex, admin_token=admin_token)
            registered.append(kid)
        out_q.put(("SUCCESS", registered))
    except Exception as exc:
        out_q.put(("ERROR", str(exc)))


def _mp_revoke_key_worker(
    trust_path: str,
    admin_token: str | None,
    key_id: str,
    reason: str,
    out_q: mp.Queue[Any],
) -> None:
    """Multiprocessing worker revoking a key."""
    try:
        store = DurableTrustStore(trust_file_path=trust_path, admin_token=admin_token)
        store.revoke_key(key_id, reason=reason, admin_token=admin_token)
        out_q.put(("SUCCESS", key_id))
    except Exception as exc:
        out_q.put(("ERROR", str(exc)))


def _mp_verify_receipt_worker(
    trust_path: str,
    receipt_dict: dict[str, Any],
    artifact_sha: str,
    out_q: mp.Queue[Any],
) -> None:
    """Multiprocessing worker verifying a receipt using an independent verifier instance."""
    try:
        store = DurableTrustStore(trust_file_path=trust_path)
        verifier = TrainingReceiptVerifier(trust_store=store)
        is_valid, reason = verifier.verify_receipt(
            receipt_dict,
            candidate_artifact_sha256=artifact_sha,
        )
        out_q.put(("SUCCESS", is_valid, reason))
    except Exception as exc:
        out_q.put(("ERROR", str(exc)))


def _mp_contention_worker(
    trust_path: str,
    admin_token: str | None,
    worker_id: int,
    iterations: int,
    out_q: mp.Queue[Any],
) -> None:
    """Multiprocessing worker executing rapid mixed read/write/reload contention."""
    try:
        store = DurableTrustStore(trust_file_path=trust_path, admin_token=admin_token)
        for i in range(iterations):
            _, pub_hex, _ = _generate_ed25519_key()
            kid = f"contend_w{worker_id}_k{i}"
            store.register_approved_key(kid, pub_hex, admin_token=admin_token)
            assert store.is_key_revoked(kid) is False
            store.reload()
            keys = store.get_approved_keys()
            assert kid in keys
            if i % 3 == 0:
                store.retire_key(kid, reason="rotation", admin_token=admin_token)
        out_q.put(("SUCCESS", worker_id))
    except Exception as exc:
        out_q.put(("ERROR", str(exc)))


# ---------------------------------------------------------------------------
# Test Suite: TRUST-01 through TRUST-12
# ---------------------------------------------------------------------------


class TestDurableTrustStoreCorrectness:
    """Comprehensive security test suite verifying DurableTrustStore correctness."""

    def test_trust_01_authorized_registration_persists_across_process_restarts(
        self, tmp_path: Path
    ) -> None:
        """TRUST-01: Authorized key registration persists across process restarts."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_01"
        _, pub_hex, kid = _generate_ed25519_key()

        store1 = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)
        store1.register_approved_key(kid, pub_hex, admin_token=admin_tok)
        assert store1.get_approved_keys()[kid] == pub_hex

        # Simulate independent process restart by constructing brand new store pointing to same disk file
        store2 = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)
        loaded_keys = store2.get_approved_keys()
        assert kid in loaded_keys
        assert loaded_keys[kid] == pub_hex
        assert store2.is_key_revoked(kid) is False

    def test_trust_02_unauthorized_registration_and_trust_modification_rejected(
        self, tmp_path: Path
    ) -> None:
        """TRUST-02: Unauthorized registration or trust modification is rejected."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_02"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)
        _, pub_hex, kid = _generate_ed25519_key()

        # Missing token
        with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized administrative operation"):
            store.register_approved_key(kid, pub_hex, admin_token=None)

        # Invalid token
        with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized administrative operation"):
            store.register_approved_key(kid, pub_hex, admin_token="wrong_token")

        assert kid not in store.get_approved_keys()

        # Authorized registration succeeds
        store.register_approved_key(kid, pub_hex, admin_token=admin_tok)
        assert kid in store.get_approved_keys()

        # Unauthorized revocation rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized administrative operation"):
            store.revoke_key(kid, admin_token="wrong_token")
        assert store.is_key_revoked(kid) is False

        # Unauthorized clear rejected
        with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized administrative operation"):
            store.clear(admin_token="wrong_token")
        assert kid in store.get_approved_keys()

    def test_trust_03_revocation_is_durably_persisted_and_cannot_be_bypassed(
        self, tmp_path: Path
    ) -> None:
        """TRUST-03: Revocation is durably persisted and cannot be bypassed."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_03"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)
        _, pub_hex, kid = _generate_ed25519_key()

        store.register_approved_key(kid, pub_hex, admin_token=admin_tok)
        store.revoke_key(kid, reason="compromise_detected", admin_token=admin_tok)

        assert store.is_key_revoked(kid) is True
        assert kid not in store.get_approved_keys()

        # Re-registering revoked key is rejected
        with pytest.raises(ValueError, match="because it has been revoked due to compromise"):
            store.register_approved_key(kid, pub_hex, admin_token=admin_tok)

        # Re-opening in new store instance preserves revocation
        store_reloaded = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)
        assert store_reloaded.is_key_revoked(kid) is True
        assert kid not in store_reloaded.get_approved_keys()

    def test_trust_04_existing_verifiers_reject_newly_revoked_key_without_restarting(
        self, tmp_path: Path
    ) -> None:
        """TRUST-04: Existing verifiers reject a newly revoked key without restarting."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_04"

        # 1. Setup durable trust store with key A
        priv, pub_hex, kid = _generate_ed25519_key()
        admin_store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)
        admin_store.register_approved_key(kid, pub_hex, admin_token=admin_tok)

        # 2. Worker instantiates verifier instance
        worker_store = DurableTrustStore(trust_file_path=store_path)
        worker_verifier = TrainingReceiptVerifier(trust_store=worker_store)

        # Create genuine receipt signed by key A
        issuer = TrainingReceiptIssuer(private_key=priv, key_id=kid, app_env="development")
        dummy_sha = hashlib.sha256(b"dummy_model_bytes_trust_04").hexdigest()
        receipt = issuer.issue_receipt(
            training_run_id="run_trust_04",
            job_id="job_trust_04",
            artifact_sha256=dummy_sha,
            simulation_id="sim_trust_04",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            metrics={"loss": 0.05},
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="WORKER",
        )

        # 3. Worker successfully verifies before revocation
        is_ok, reason = worker_verifier.verify_receipt(
            receipt,
            candidate_artifact_sha256=dummy_sha,
        )
        assert is_ok is True, f"Initial verification failed: {reason}"
        assert worker_verifier.is_key_trusted(kid) is True

        # 4. Administrator revokes key A in the durable store
        admin_store.revoke_key(kid, reason="key_compromised", admin_token=admin_tok)

        # 5. Original worker attempts verification using EXISTING verifier instance (NO restart)
        is_ok_after, reason_after = worker_verifier.verify_receipt(
            receipt,
            candidate_artifact_sha256=dummy_sha,
        )
        assert is_ok_after is False
        assert "has been explicitly revoked" in reason_after
        assert worker_verifier.is_key_trusted(kid) is False

        # 6. Another independent process verifier also rejects
        ctx = mp.get_context("spawn")
        out_q = ctx.Queue()
        proc = ctx.Process(
            target=_mp_verify_receipt_worker,
            args=(store_path, receipt.to_dict(), dummy_sha, out_q),
        )
        proc.start()
        proc.join(timeout=30)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
        assert not proc.is_alive()

        res_type, res_ok, res_reason = out_q.get(timeout=5)
        assert res_type == "SUCCESS"
        assert res_ok is False
        assert "has been explicitly revoked" in res_reason

    def test_trust_05_multiple_independent_processes_observe_consistent_authoritative_trust(
        self, tmp_path: Path
    ) -> None:
        """TRUST-05: Multiple independent processes observe consistent authoritative trust."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_05"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)

        ctx = mp.get_context("spawn")
        num_workers = 3
        keys_per_worker = 5
        procs = []
        queues = []

        for w in range(num_workers):
            q = ctx.Queue()
            p = ctx.Process(
                target=_mp_register_keys_worker,
                args=(store_path, admin_tok, f"proc_{w}", keys_per_worker, q),
            )
            procs.append(p)
            queues.append(q)
            p.start()

        all_registered = []
        for p, q in zip(procs, queues, strict=False):
            p.join(timeout=30)
            if p.is_alive():
                p.terminate()
                p.join(timeout=5)
            assert not p.is_alive()
            status, data = q.get(timeout=5)
            assert status == "SUCCESS", f"Worker failed: {data}"
            all_registered.extend(data)

        # Verify main process sees all keys without omissions
        store.reload()
        approved = store.get_approved_keys()
        for kid in all_registered:
            assert kid in approved

    def test_trust_06_concurrent_registration_and_revocation_prevent_lost_updates_and_resurrection(
        self, tmp_path: Path
    ) -> None:
        """TRUST-06: Concurrent registration and revocation cannot lose updates or resurrect revoked keys."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_06"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)

        # Pre-register target key to be revoked
        _, pub_hex, target_kid = _generate_ed25519_key()
        store.register_approved_key(target_kid, pub_hex, admin_token=admin_tok)

        ctx = mp.get_context("spawn")
        q_reg1 = ctx.Queue()
        q_reg2 = ctx.Queue()
        q_rev = ctx.Queue()

        p_reg1 = ctx.Process(
            target=_mp_register_keys_worker,
            args=(store_path, admin_tok, "batch_a", 10, q_reg1),
        )
        p_reg2 = ctx.Process(
            target=_mp_register_keys_worker,
            args=(store_path, admin_tok, "batch_b", 10, q_reg2),
        )
        p_rev = ctx.Process(
            target=_mp_revoke_key_worker,
            args=(store_path, admin_tok, target_kid, "compromise", q_rev),
        )

        p_reg1.start()
        p_reg2.start()
        p_rev.start()

        for p in (p_reg1, p_reg2, p_rev):
            p.join(timeout=30)
            if p.is_alive():
                p.terminate()
                p.join(timeout=5)
            assert not p.is_alive()

        res_a = q_reg1.get(timeout=2)
        res_b = q_reg2.get(timeout=2)
        res_rev = q_rev.get(timeout=2)

        assert res_a[0] == "SUCCESS"
        assert res_b[0] == "SUCCESS"
        assert res_rev[0] == "SUCCESS"

        # Force fresh reload from disk
        store.reload()
        approved = store.get_approved_keys()

        # All 20 registered keys must exist (zero lost updates)
        for kid in res_a[1]:
            assert kid in approved
        for kid in res_b[1]:
            assert kid in approved

        # Target key must remain revoked (no resurrection by concurrent writes)
        assert target_kid not in approved
        assert store.is_key_revoked(target_kid) is True

    def test_trust_07_concurrent_trust_operations_do_not_deadlock(
        self, tmp_path: Path
    ) -> None:
        """TRUST-07: Concurrent trust operations do not deadlock under heavy multi-process contention."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_07"
        DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)

        ctx = mp.get_context("spawn")
        num_workers = 4
        iterations = 15
        procs = []
        queues = []

        for w in range(num_workers):
            q = ctx.Queue()
            p = ctx.Process(
                target=_mp_contention_worker,
                args=(store_path, admin_tok, w, iterations, q),
            )
            procs.append(p)
            queues.append(q)
            p.start()

        for p, q in zip(procs, queues, strict=False):
            p.join(timeout=25)
            assert not p.is_alive(), "Worker deadlocked or timed out"
            status, res = q.get(timeout=2)
            assert status == "SUCCESS", f"Contention worker failed: {res}"

    def test_trust_08_malformed_or_corrupted_trust_persistence_fails_closed(
        self, tmp_path: Path
    ) -> None:
        """TRUST-08: Malformed or corrupted trust persistence fails closed immediately."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_08"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)
        priv, pub_hex, kid = _generate_ed25519_key()
        store.register_approved_key(kid, pub_hex, admin_token=admin_tok)

        # 1. Empty file (0 bytes)
        Path(store_path).write_bytes(b"")
        with pytest.raises(TrainingProvenanceSecurityError, match="is corrupted.*empty"):
            store.reload()
        with pytest.raises(TrainingProvenanceSecurityError, match="corrupted state"):
            store.is_key_revoked(kid)
        with pytest.raises(TrainingProvenanceSecurityError, match="corrupted state"):
            store.get_approved_keys()

        # 2. Corrupted JSON (syntax error)
        Path(store_path).write_text('{"approved_keys": { invalid json ', encoding="utf-8")
        with pytest.raises(TrainingProvenanceSecurityError, match="is corrupted.*invalid JSON"):
            store.reload()

        # 3. Tampered payload with digest mismatch
        tampered_container = {
            "schema_version": "1.0",
            "data": {
                "approved_keys": {kid: {"public_key_hex": pub_hex}},
                "revoked_keys": {},
                "retired_keys": {},
            },
            "integrity_sha256": "00" * 32,  # Invalid checksum
        }
        Path(store_path).write_text(json.dumps(tampered_container), encoding="utf-8")
        with pytest.raises(TrainingProvenanceSecurityError, match="integrity check.*checksum mismatch"):
            store.reload()

        # 4. Malformed schema (wrong types)
        bad_schema = {
            "approved_keys": "not_a_dictionary",
            "revoked_keys": {},
            "retired_keys": {},
        }
        Path(store_path).write_text(json.dumps(bad_schema), encoding="utf-8")
        with pytest.raises(TrainingProvenanceSecurityError, match="must be a dictionary"):
            store.reload()

        # 5. Verifier observing corrupted store fails closed
        issuer = TrainingReceiptIssuer(private_key=priv, key_id=kid, app_env="development")
        dummy_sha = hashlib.sha256(b"dummy_08").hexdigest()
        receipt = issuer.issue_receipt(
            training_run_id="run_08",
            job_id="job_08",
            artifact_sha256=dummy_sha,
            simulation_id="sim_08",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            metrics={"loss": 0.05},
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="WORKER",
        )
        verifier = TrainingReceiptVerifier(trust_store=store)
        is_ok, reason = verifier.verify_receipt(
            receipt,
            candidate_artifact_sha256=dummy_sha,
        )
        assert is_ok is False
        assert "Trust store corrupted or unavailable" in reason
        assert verifier.is_key_trusted(kid) is False

    def test_trust_09_crash_during_persistence_cannot_produce_partially_trusted_or_permissive_state(
        self, tmp_path: Path
    ) -> None:
        """TRUST-09: Crash during persistence leaves the existing authoritative file intact."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_09"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)
        _, pub_hex1, kid1 = _generate_ed25519_key()
        store.register_approved_key(kid1, pub_hex1, admin_token=admin_tok)

        # Simulate simulated crash / interrupted write: write half-written .tmp file
        fake_tmp = f"{store_path}.9999.interrupted.tmp"
        Path(fake_tmp).write_text('{"partial": "crash', encoding="utf-8")

        # The authoritative store file is completely untouched
        store2 = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)
        assert kid1 in store2.get_approved_keys()
        assert store2.get_approved_keys()[kid1] == pub_hex1

    def test_trust_10_recovery_and_reload_preserve_revocation_and_authorization(
        self, tmp_path: Path
    ) -> None:
        """TRUST-10: Recovery and reload preserve revocation and authorization."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_10"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)

        _, pub_hex_a, kid_a = _generate_ed25519_key()
        _, pub_hex_b, kid_b = _generate_ed25519_key()
        _, pub_hex_c, kid_c = _generate_ed25519_key()

        store.register_approved_key(kid_a, pub_hex_a, admin_token=admin_tok)
        store.register_approved_key(kid_b, pub_hex_b, admin_token=admin_tok)
        store.register_approved_key(kid_c, pub_hex_c, admin_token=admin_tok)

        store.revoke_key(kid_b, reason="compromise", admin_token=admin_tok)
        store.retire_key(kid_c, reason="lifecycle", admin_token=admin_tok)

        # Force reload from disk
        recovered_store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)
        assert kid_a in recovered_store.get_approved_keys()
        assert kid_b not in recovered_store.get_approved_keys()
        assert recovered_store.is_key_revoked(kid_b) is True
        is_ret, _ = recovered_store.is_key_retired(kid_c)
        assert is_ret is True

    def test_trust_11_unauthorized_administrative_operations_cannot_modify_security_state(
        self, tmp_path: Path
    ) -> None:
        """TRUST-11: Unauthorized administrative operations cannot modify trusted security state."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_11"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)

        _, pub_hex_a, kid_a = _generate_ed25519_key()
        store.register_approved_key(kid_a, pub_hex_a, admin_token=admin_tok)

        # Rotate key without admin token rejected
        _, pub_hex_new, kid_new = _generate_ed25519_key()
        with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized administrative operation"):
            store.rotate_key(kid_a, kid_new, pub_hex_new, admin_token=None)

        # Key substitution without authorization rejected
        _, pub_hex_bad, _ = _generate_ed25519_key()
        with pytest.raises(ValueError, match="Unauthorized public key substitution rejected"):
            store.register_approved_key(kid_a, pub_hex_bad, authorized_replacement=False, admin_token=admin_tok)

        # Authorized rotation succeeds
        store.rotate_key(kid_a, kid_new, pub_hex_new, admin_token=admin_tok)
        approved = store.get_approved_keys()
        assert kid_new in approved
        is_ret, _ = store.is_key_retired(kid_a)
        assert is_ret is True

    def test_trust_12_genuine_signatures_verify_with_active_keys_and_rejected_when_revoked(
        self, tmp_path: Path
    ) -> None:
        """TRUST-12: Genuine signatures verify with active trusted keys, and fail closed when revoked."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_trust_12"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)

        priv_active, pub_active, kid_active = _generate_ed25519_key()
        priv_revoked, pub_revoked, kid_revoked = _generate_ed25519_key()
        priv_unknown, _, kid_unknown = _generate_ed25519_key()

        store.register_approved_key(kid_active, pub_active, admin_token=admin_tok)
        store.register_approved_key(kid_revoked, pub_revoked, admin_token=admin_tok)
        store.revoke_key(kid_revoked, reason="compromise", admin_token=admin_tok)

        verifier = TrainingReceiptVerifier(trust_store=store)

        # 1. Genuine receipt with active trusted key -> VERIFIED
        issuer_active = TrainingReceiptIssuer(private_key=priv_active, key_id=kid_active, app_env="development")
        sha_model = hashlib.sha256(b"authentic_model_bytes_12").hexdigest()
        receipt_active = issuer_active.issue_receipt(
            training_run_id="run_12_active",
            job_id="job_12_active",
            artifact_sha256=sha_model,
            simulation_id="sim_12",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=200,
            metrics={"loss": 0.01},
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="WORKER",
        )
        ok1, reason1 = verifier.verify_receipt(receipt_active, candidate_artifact_sha256=sha_model)
        assert ok1 is True, f"Active key verification failed: {reason1}"

        # 2. Receipt with revoked key -> REJECTED
        issuer_rev = TrainingReceiptIssuer(private_key=priv_revoked, key_id=kid_revoked, app_env="development")
        receipt_rev = issuer_rev.issue_receipt(
            training_run_id="run_12_rev",
            job_id="job_12_rev",
            artifact_sha256=sha_model,
            simulation_id="sim_12",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=200,
            metrics={"loss": 0.01},
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="WORKER",
        )
        ok2, reason2 = verifier.verify_receipt(receipt_rev, candidate_artifact_sha256=sha_model)
        assert ok2 is False
        assert "has been explicitly revoked" in reason2

        # 3. Receipt with unknown / untrusted key -> REJECTED
        issuer_unk = TrainingReceiptIssuer(private_key=priv_unknown, key_id=kid_unknown, app_env="development")
        receipt_unk = issuer_unk.issue_receipt(
            training_run_id="run_12_unk",
            job_id="job_12_unk",
            artifact_sha256=sha_model,
            simulation_id="sim_12",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=200,
            metrics={"loss": 0.01},
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="WORKER",
        )
        ok3, reason3 = verifier.verify_receipt(receipt_unk, candidate_artifact_sha256=sha_model)
        assert ok3 is False
        assert "not present in trusted public key registry" in reason3

        # 4. Tampered signature on valid receipt -> REJECTED
        tampered_dict = receipt_active.to_dict()
        tampered_dict["signature"] = "11" * 64
        ok4, reason4 = verifier.verify_receipt(tampered_dict, candidate_artifact_sha256=sha_model)
        assert ok4 is False
        assert "Cryptographic signature mismatch" in reason4

    def test_trust_integration_revoked_key_blocks_model_registry_admission(
        self, tmp_path: Path
    ) -> None:
        """Integration regression: Revoking signing key in DurableTrustStore causes ModelRegistry admission to fail closed."""
        trust_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_reg"
        store = DurableTrustStore(trust_file_path=trust_path, admin_token=admin_tok)

        priv, pub_hex, kid = _generate_ed25519_key()
        store.register_approved_key(kid, pub_hex, admin_token=admin_tok)

        verifier = TrainingReceiptVerifier(trust_store=store)
        registry = ModelRegistry(storage_dir=str(tmp_path / "registry"), verifier=verifier)

        model_svc = ModelService(get_settings())
        model = model_svc.create_model(input_dim=NUM_FEATURES)
        raw_bytes = b"model_weights_checkpoint_bytes_reg"
        sha_model = hashlib.sha256(raw_bytes).hexdigest()

        issuer = TrainingReceiptIssuer(private_key=priv, key_id=kid, app_env="development")
        receipt = issuer.issue_receipt(
            training_run_id="run_reg_01",
            job_id="job_reg_01",
            artifact_sha256=sha_model,
            simulation_id="sim_reg_01",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=300,
            metrics={"loss": 0.015},
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="EXECUTOR",
        )

        # First admission before revocation: valid and eligible
        entry1 = registry.save_version(
            simulation_id="sim_reg_01",
            state_dict=model.state_dict(),
            metrics={"loss": 0.015},
            is_promoted=False,
            provenance_type="TRAINING_RECORDED",
            training_run_id="run_reg_01",
            training_receipt=receipt.to_dict(),
            training_evidence={"loss": 0.015, "rounds": 2, "sample_count": 300},
            artifact_bytes=raw_bytes,
        )
        assert entry1["is_production_eligible"] is True
        assert entry1["provenance_type"] == "TRAINING_RECORDED"

        # Now revoke the signing key in the durable trust store
        store.revoke_key(kid, reason="compromise", admin_token=admin_tok)

        # Subsequent admission attempt with the revoked key receipt: MUST FAIL CLOSED
        entry2 = registry.save_version(
            simulation_id="sim_reg_01",
            state_dict=model.state_dict(),
            metrics={"loss": 0.015},
            is_promoted=False,
            provenance_type="TRAINING_RECORDED",
            training_run_id="run_reg_01",
            training_receipt=receipt.to_dict(),
            training_evidence={"loss": 0.015, "rounds": 2, "sample_count": 300},
            artifact_bytes=raw_bytes,
        )
        assert entry2["is_production_eligible"] is False
        assert entry2["provenance_type"] == "UNVERIFIED"
        assert "INVALID_RECEIPT" in entry2["attestation_status"]
        assert "has been explicitly revoked" in entry2["attestation_status"]


# ---------------------------------------------------------------------------
# Focused Adversarial Suite: Authorization & Integrity Boundary Enforcement
# ---------------------------------------------------------------------------


class TestDurableTrustStoreAuthorizationAndIntegrityAdversarial:
    """Adversarial security test suite targeting administrative authorization and cryptographic integrity."""

    def test_adv_01_missing_expected_admin_credential_with_attacker_supplied_token_rejected(
        self, tmp_path: Path
    ) -> None:
        """Adversarial 1: Missing expected admin credential + attacker-supplied nonempty token -> REJECTED."""
        store_path = str(tmp_path / "trust_store.json")
        _, pub_hex, kid = _generate_ed25519_key()

        # Disk-backed store initialized with NO configured admin credential
        store = DurableTrustStore(trust_file_path=store_path, admin_token=None)

        # Attacker attempts mutations with arbitrary non-empty token
        attacker_tok = "attacker_arbitrary_string_xyz_999"

        with pytest.raises(TrainingProvenanceSecurityError, match="Administrative authority is unconfigured"):
            store.register_approved_key(kid, pub_hex, admin_token=attacker_tok)

        with pytest.raises(TrainingProvenanceSecurityError, match="Administrative authority is unconfigured"):
            store.revoke_key(kid, admin_token=attacker_tok)

        with pytest.raises(TrainingProvenanceSecurityError, match="Administrative authority is unconfigured"):
            store.retire_key(kid, admin_token=attacker_tok)

        with pytest.raises(TrainingProvenanceSecurityError, match="Administrative authority is unconfigured"):
            store.rotate_key(kid, "new_kid", pub_hex, admin_token=attacker_tok)

        with pytest.raises(TrainingProvenanceSecurityError, match="Administrative authority is unconfigured"):
            store.clear(admin_token=attacker_tok)

        # Confirm zero changes reached disk
        assert not Path(store_path).exists() or Path(store_path).stat().st_size == 0

    def test_adv_02_wrong_admin_credential_against_configured_authority_rejected(
        self, tmp_path: Path
    ) -> None:
        """Adversarial 2: Wrong admin credential against configured authority -> REJECTED."""
        store_path = str(tmp_path / "trust_store.json")
        configured_admin = "strong_authorized_secret_12345"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=configured_admin)
        _, pub_hex, kid = _generate_ed25519_key()

        # Attacker supplies wrong credentials
        for bad_tok in ("wrong_token", "admin", "12345", "attacker_tok", ""):
            with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized administrative operation"):
                store.register_approved_key(kid, pub_hex, admin_token=bad_tok)

        with pytest.raises(TrainingProvenanceSecurityError, match="Unauthorized administrative operation"):
            store.register_approved_key(kid, pub_hex, admin_token=None)

        assert kid not in store.get_approved_keys()

    def test_adv_03_genuine_authorized_mutation_persists_correctly(
        self, tmp_path: Path
    ) -> None:
        """Adversarial 3: Genuine authorized mutation -> persists correctly with signature and HMAC."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "genuine_admin_secret_03"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)

        _, pub_hex_a, kid_a = _generate_ed25519_key()
        _, pub_hex_b, kid_b = _generate_ed25519_key()

        # Authorized registration
        store.register_approved_key(kid_a, pub_hex_a, admin_token=admin_tok)
        store.register_approved_key(kid_b, pub_hex_b, admin_token=admin_tok)
        store.revoke_key(kid_b, reason="compromise", admin_token=admin_tok)

        # Inspect raw persisted container on disk
        with open(store_path, encoding="utf-8") as f:
            raw_container = json.load(f)

        assert raw_container["schema_version"] == "2.0"
        assert "integrity_sha256" in raw_container
        assert "integrity_hmac_sha256" in raw_container
        assert "authority_public_key_hex" in raw_container
        assert "authority_signature" in raw_container
        assert Path(f"{store_path}.authority_pub").exists()

        # Reload in a clean independent store instance with same admin token
        reloaded = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)
        assert kid_a in reloaded.get_approved_keys()
        assert kid_b not in reloaded.get_approved_keys()
        assert reloaded.is_key_revoked(kid_b) is True

    def test_adv_04_modified_approved_key_with_recomputed_unkeyed_sha256_rejected(
        self, tmp_path: Path
    ) -> None:
        """Adversarial 4: Modified approved key with recomputed unkeyed SHA-256 cannot become trusted."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_adv_04"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)

        _, pub_legit, kid_legit = _generate_ed25519_key()
        store.register_approved_key(kid_legit, pub_legit, admin_token=admin_tok)

        # Verifier confirms key is trusted before attack
        worker_store = DurableTrustStore(trust_file_path=store_path)
        verifier = TrainingReceiptVerifier(trust_store=worker_store)
        assert verifier.is_key_trusted(kid_legit) is True

        # ATTACK: Attacker with write access to file replaces public key and recomputes unkeyed SHA-256
        _, attacker_pub, _ = _generate_ed25519_key()
        with open(store_path, encoding="utf-8") as f:
            container = json.load(f)

        container["data"]["approved_keys"][kid_legit]["public_key_hex"] = attacker_pub
        tampered_canonical = json.dumps(container["data"], sort_keys=True).encode("utf-8")
        container["integrity_sha256"] = hashlib.sha256(tampered_canonical).hexdigest()

        # Attacker writes tampered container (matching SHA-256 checksum)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(container, f)

        # Worker reload MUST FAIL CLOSED: checksum matches, but signature verification fails!
        with pytest.raises(TrainingProvenanceSecurityError, match="failed cryptographic signature verification|HMAC"):
            worker_store.reload()

        # Verifier must reject and refuse trust
        assert verifier.is_key_trusted(kid_legit) is False

    def test_adv_05_removed_revocation_record_with_recomputed_checksum_cannot_resurrect_trust(
        self, tmp_path: Path
    ) -> None:
        """Adversarial 5: Removed revocation record with recomputed checksum cannot resurrect trust."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_adv_05"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)

        _, pub_rev, kid_rev = _generate_ed25519_key()
        store.register_approved_key(kid_rev, pub_rev, admin_token=admin_tok)
        store.revoke_key(kid_rev, reason="compromise", admin_token=admin_tok)

        # Worker confirms revoked
        worker_store = DurableTrustStore(trust_file_path=store_path)
        assert worker_store.is_key_revoked(kid_rev) is True

        # ATTACK: Attacker removes key from revoked_keys, re-adds to approved_keys, and recomputes SHA-256
        with open(store_path, encoding="utf-8") as f:
            container = json.load(f)

        container["data"]["revoked_keys"].pop(kid_rev, None)
        container["data"]["approved_keys"][kid_rev] = {"public_key_hex": pub_rev}
        tampered_canonical = json.dumps(container["data"], sort_keys=True).encode("utf-8")
        container["integrity_sha256"] = hashlib.sha256(tampered_canonical).hexdigest()

        with open(store_path, "w", encoding="utf-8") as f:
            json.dump(container, f)

        # Worker reload MUST FAIL CLOSED: signature check fails
        with pytest.raises(TrainingProvenanceSecurityError, match="failed cryptographic signature verification|HMAC"):
            worker_store.reload()

        # Verifier rejects the revoked key
        verifier = TrainingReceiptVerifier(trust_store=worker_store)
        assert verifier.is_key_trusted(kid_rev) is False

    def test_adv_06_legacy_unprotected_trust_state_file_in_production_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adversarial 6: Legacy unprotected trust-state file in production fails closed."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("CFI_APP_ENV", "production")
        from app.config import get_settings
        get_settings.cache_clear()

        store_path = str(tmp_path / "legacy_trust_store.json")
        _, pub_hex, kid = _generate_ed25519_key()

        # Legacy format: unauthenticated plain JSON container without authority_signature or HMAC
        legacy_content = {
            "schema_version": "1.0",
            "data": {
                "approved_keys": {kid: {"public_key_hex": pub_hex}},
                "revoked_keys": {},
                "retired_keys": {},
            },
            "integrity_sha256": hashlib.sha256(
                json.dumps(
                    {
                        "approved_keys": {kid: {"public_key_hex": pub_hex}},
                        "revoked_keys": {},
                        "retired_keys": {},
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
        }
        Path(store_path).write_text(json.dumps(legacy_content), encoding="utf-8")

        # Loading in production must fail closed
        with pytest.raises(
            TrainingProvenanceSecurityError,
            match="unauthenticated persistence format lacking cryptographic authentication",
        ):
            store = DurableTrustStore(trust_file_path=store_path)
            store.reload()

        # Reset settings cache
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("CFI_APP_ENV", raising=False)
        get_settings.cache_clear()

    def test_adv_07_existing_verifier_and_independent_process_honor_durable_revocation(
        self, tmp_path: Path
    ) -> None:
        """Adversarial 7: Existing verifier instance and independent process honor durable revocation."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_adv_07"
        admin_store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)

        priv, pub_hex, kid = _generate_ed25519_key()
        admin_store.register_approved_key(kid, pub_hex, admin_token=admin_tok)

        worker_store = DurableTrustStore(trust_file_path=store_path)
        verifier = TrainingReceiptVerifier(trust_store=worker_store)

        issuer = TrainingReceiptIssuer(private_key=priv, key_id=kid, app_env="development")
        dummy_sha = hashlib.sha256(b"artifact_adv_07").hexdigest()
        receipt = issuer.issue_receipt(
            training_run_id="run_adv_07",
            job_id="job_adv_07",
            artifact_sha256=dummy_sha,
            simulation_id="sim_adv_07",
            executed_rounds=1,
            executed_epochs=1,
            sample_count=100,
            metrics={"loss": 0.02},
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="WORKER",
        )

        ok1, reason1 = verifier.verify_receipt(receipt, candidate_artifact_sha256=dummy_sha)
        assert ok1 is True, f"Initial verification failed: {reason1}"

        # Administrator revokes key
        admin_store.revoke_key(kid, reason="compromise", admin_token=admin_tok)

        # Existing verifier instance rejects without restarting
        ok2, reason2 = verifier.verify_receipt(receipt, candidate_artifact_sha256=dummy_sha)
        assert ok2 is False
        assert "has been explicitly revoked" in reason2

        # Independent spawned process also rejects
        ctx = mp.get_context("spawn")
        out_q = ctx.Queue()
        p = ctx.Process(
            target=_mp_verify_receipt_worker,
            args=(store_path, receipt.to_dict(), dummy_sha, out_q),
        )
        p.start()
        p.join(timeout=30)
        if p.is_alive():
            p.terminate()
            p.join(timeout=5)
        assert not p.is_alive()
        status, res_ok, res_reason = out_q.get(timeout=5)
        assert status == "SUCCESS"
        assert res_ok is False
        assert "has been explicitly revoked" in res_reason

    def test_adv_08_genuine_ed25519_receipt_verification_and_model_registry_admission(
        self, tmp_path: Path
    ) -> None:
        """Adversarial 8: Genuine Ed25519 verification and Model Registry admission succeed for active keys."""
        store_path = str(tmp_path / "trust_store.json")
        admin_tok = "admin_secret_adv_08"
        store = DurableTrustStore(trust_file_path=store_path, admin_token=admin_tok)

        priv, pub_hex, kid = _generate_ed25519_key()
        store.register_approved_key(kid, pub_hex, admin_token=admin_tok)

        verifier = TrainingReceiptVerifier(trust_store=store)
        registry = ModelRegistry(storage_dir=str(tmp_path / "registry"), verifier=verifier)

        model_svc = ModelService(get_settings())
        model = model_svc.create_model(input_dim=NUM_FEATURES)
        raw_bytes = b"weights_adv_08"
        sha_model = hashlib.sha256(raw_bytes).hexdigest()

        issuer = TrainingReceiptIssuer(private_key=priv, key_id=kid, app_env="development")
        receipt = issuer.issue_receipt(
            training_run_id="run_adv_08",
            job_id="job_adv_08",
            artifact_sha256=sha_model,
            simulation_id="sim_adv_08",
            executed_rounds=2,
            executed_epochs=2,
            sample_count=200,
            metrics={"loss": 0.01},
            architecture="FraudDetectionNN",
            feature_schema_version="v1.0",
            feature_names=list(FEATURE_NAMES),
            producer_id="EXECUTOR",
        )

        entry = registry.save_version(
            simulation_id="sim_adv_08",
            state_dict=model.state_dict(),
            metrics={"loss": 0.01},
            is_promoted=False,
            provenance_type="TRAINING_RECORDED",
            training_run_id="run_adv_08",
            training_receipt=receipt.to_dict(),
            training_evidence={"loss": 0.01, "rounds": 2, "sample_count": 200},
            artifact_bytes=raw_bytes,
        )
        assert entry["is_production_eligible"] is True
        assert entry["provenance_type"] == "TRAINING_RECORDED"
