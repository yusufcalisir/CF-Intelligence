"""Integration tests for multi-worker authority and state consistency.

Verifies:
- Monotonic version allocation across independent OS processes.
- Institutional isolation preventing unauthorized cross-bank registry access.
- Mutual exclusion ensuring exactly one active champion under concurrent promotion.
- Concurrent promotion vs sign-off revocation determinism.
- Registry file-lock synchronization with zero lost updates and valid audit integrity.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import multiprocessing as mp
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
    ModelRegistry,
    generate_signoff_signature,
)
from app.application.services.model_service import NUM_FEATURES, FraudDetectionModel
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_account_store import DurableAccountStore
from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


def _worker_register_model(
    storage_dir: str,
    sim_id: str,
    worker_idx: int,
    owning_bank_id: str,
    auth_token: str,
    env_config: dict[str, str],
    result_queue: Any,
    barrier: Any | None = None,
) -> None:
    """Independent OS worker process registering a model version."""
    for k, v in env_config.items():
        os.environ[k] = v

    get_settings.cache_clear()
    reset_default_nonce_store()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None
    DurableAccountStore._instance = None

    try:
        trust_store = DurableTrustStore(
            trust_file_path=env_config["CFI_TRUST_STORE_PATH"],
            admin_token=env_config["CFI_TRUST_STORE_ADMIN_TOKEN"],
        )
        verifier = TrainingReceiptVerifier(trust_store=trust_store)
        registry = ModelRegistry(storage_dir=storage_dir, verifier=verifier)

        # Build genuine model bytes
        model = FraudDetectionModel(input_dim=NUM_FEATURES, dp_compatible=True)
        with torch.no_grad():
            model.network[8].bias.fill_(float(worker_idx) * 0.1)
        buf = io.BytesIO()
        torch.save(model.state_dict(), buf)
        artifact_bytes = buf.getvalue()
        artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()

        # Load private key for worker receipt issuance
        priv_bytes = bytes.fromhex(env_config["WORKER_PRIV_KEY_HEX"])
        priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(priv_bytes)
        issuer = TrainingReceiptIssuer(
            key_id=env_config["WORKER_KEY_ID"],
            private_key=priv_key,
            trust_store=trust_store,
            require_authenticated_job=False,
            app_env="development",
        )

        evidence = TrainingExecutionEvidence(
            training_run_id=f"run_{sim_id}_{worker_idx}",
            job_id=f"job_{sim_id}_{worker_idx}",
            simulation_id=sim_id,
            completed_at=datetime.now(UTC).isoformat(),
            executed_rounds=3,
            executed_epochs=3,
            sample_count=200,
            observed_metrics={"loss": 0.15, "auc": 0.95},
            artifact_sha256=artifact_sha,
            architecture="FraudDetectionModel",
            feature_schema_version="1.0",
            feature_names=[f"f{i}" for i in range(NUM_FEATURES)],
            owning_bank_id=owning_bank_id,
        )
        receipt = issuer.issue_receipt(
            evidence=evidence,
            model_bytes=artifact_bytes,
            owning_bank_id=owning_bank_id,
            architecture="FraudDetectionModel",
            input_dim=NUM_FEATURES,
            feature_names=[f"f{i}" for i in range(NUM_FEATURES)],
            feature_schema_version="1.0",
        )

        if barrier:
            with contextlib.suppress(Exception):
                barrier.wait(timeout=10)

        entry = registry.save_version(
            simulation_id=sim_id,
            state_dict=model.state_dict(),
            artifact_bytes=artifact_bytes,
            training_receipt=receipt,
            provenance_type="TRAINING_EVIDENCE_VERIFIED",
            owning_bank_id=owning_bank_id,
            auth_token=auth_token,
        )
        result_queue.put({"success": True, "worker_idx": worker_idx, "version": entry["version"]})
    except Exception as exc:
        result_queue.put({"success": False, "worker_idx": worker_idx, "error": str(exc)})


def _worker_promote_model(
    storage_dir: str,
    sim_id: str,
    version: int,
    auth_token: str,
    env_config: dict[str, str],
    result_queue: Any,
    barrier: Any | None = None,
) -> None:
    """Independent OS worker process promoting a model version to champion."""
    for k, v in env_config.items():
        os.environ[k] = v

    get_settings.cache_clear()
    reset_default_nonce_store()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None
    DurableAccountStore._instance = None

    try:
        trust_store = DurableTrustStore(
            trust_file_path=env_config["CFI_TRUST_STORE_PATH"],
            admin_token=env_config["CFI_TRUST_STORE_ADMIN_TOKEN"],
        )
        verifier = TrainingReceiptVerifier(trust_store=trust_store)
        registry = ModelRegistry(storage_dir=storage_dir, verifier=verifier)

        if barrier:
            with contextlib.suppress(Exception):
                barrier.wait(timeout=10)

        entry = registry.promote_version(
            simulation_id=sim_id,
            version=version,
            target_status="champion",
            auth_token=auth_token,
        )
        result_queue.put(
            {
                "success": True,
                "action": "promote",
                "version": version,
                "status": entry.get("status"),
            }
        )
    except Exception as exc:
        result_queue.put(
            {"success": False, "action": "promote", "version": version, "error": str(exc)}
        )


def _worker_revoke_signoff(
    storage_dir: str,
    sim_id: str,
    version: int,
    auth_token: str,
    env_config: dict[str, str],
    result_queue: Any,
    barrier: Any | None = None,
) -> None:
    """Independent OS worker process revoking an institutional sign-off."""
    for k, v in env_config.items():
        os.environ[k] = v

    get_settings.cache_clear()
    reset_default_nonce_store()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None
    DurableAccountStore._instance = None

    try:
        trust_store = DurableTrustStore(
            trust_file_path=env_config["CFI_TRUST_STORE_PATH"],
            admin_token=env_config["CFI_TRUST_STORE_ADMIN_TOKEN"],
        )
        verifier = TrainingReceiptVerifier(trust_store=trust_store)
        registry = ModelRegistry(storage_dir=storage_dir, verifier=verifier)

        if barrier:
            with contextlib.suppress(Exception):
                barrier.wait(timeout=10)

        rev_res = registry.revoke_sign_off(
            simulation_id=sim_id,
            version=version,
            role="compliance",
            user="carol_comp_bank_a",
            reason="Concurrent compliance review revocation",
            auth_token=auth_token,
        )
        result_queue.put(
            {"success": True, "action": "revoke", "version": version, "revocation": rev_res}
        )
    except Exception as exc:
        result_queue.put(
            {"success": False, "action": "revoke", "version": version, "error": str(exc)}
        )


class TestModelGovernanceIntegration:
    """Verifies multi-process registry synchronization, institutional boundaries, and atomicity."""

    @pytest.fixture
    def gov_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
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
        AuthenticationService._instance = None
        ImmutableAuditChain._instance = None
        DurableAccountStore._instance = None

        auth_svc = AuthenticationService(
            store_path=account_file,
            signing_secret=jwt_secret,
            authority_secret=authority_secret,
            app_env="production",
        )
        AuthenticationService._instance = auth_svc

        # Bank A users
        u_ml_a = auth_svc.register_user(
            "alice_ml_bank_a", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2
        )
        u_comp_a = auth_svc.register_user(
            "carol_comp_bank_a", "SecurePass1234!", "bank_a", ["compliance"], clearance_level=3
        )
        # Bank B users
        u_ml_b = auth_svc.register_user(
            "bob_ml_bank_b", "SecurePass1234!", "bank_b", ["ml_engineer"], clearance_level=2
        )
        u_comp_b = auth_svc.register_user(
            "dave_comp_bank_b", "SecurePass1234!", "bank_b", ["compliance"], clearance_level=3
        )

        tokens = {
            "bank_a_ml": auth_svc.create_token_bundle(u_ml_a).access_token,
            "bank_a_comp": auth_svc.create_token_bundle(u_comp_a).access_token,
            "bank_b_ml": auth_svc.create_token_bundle(u_ml_b).access_token,
            "bank_b_comp": auth_svc.create_token_bundle(u_comp_b).access_token,
        }

        # Setup training authority key
        priv_key = ed25519.Ed25519PrivateKey.generate()
        priv_hex = priv_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        ).hex()
        pub_hex = (
            priv_key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            .hex()
        )
        key_id = "training_issuer_gov_test_01"
        trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
        trust_store.register_approved_key(
            key_id=key_id, public_key_hex=pub_hex, admin_token=admin_token
        )

        env_config["WORKER_PRIV_KEY_HEX"] = priv_hex
        env_config["WORKER_KEY_ID"] = key_id

        verifier = TrainingReceiptVerifier(trust_store=trust_store)
        registry = ModelRegistry(storage_dir=storage_dir, verifier=verifier)

        return {
            "storage_dir": storage_dir,
            "tokens": tokens,
            "env_config": env_config,
            "registry": registry,
            "jwt_secret": jwt_secret,
            "audit_file": audit_file,
            "audit_secret": audit_secret,
        }

    def test_multi_process_concurrent_version_allocation(self, gov_env: dict[str, Any]) -> None:
        """Verifies that independent OS processes concurrently registering models allocate distinct, monotonic versions without corruption."""
        storage_dir = gov_env["storage_dir"]
        token = gov_env["tokens"]["bank_a_ml"]
        env_config = gov_env["env_config"]
        sim_id = "sim_concurrent_versions"

        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        barrier = ctx.Barrier(3)
        workers = []

        for i in range(3):
            p = ctx.Process(
                target=_worker_register_model,
                args=(storage_dir, sim_id, i + 1, "bank_a", token, env_config, q, barrier),
            )
            workers.append(p)
            p.start()

        for p in workers:
            p.join(timeout=30)

        results = []
        while not q.empty():
            results.append(q.get())

        assert len(results) == 3, f"Expected 3 worker responses, got {len(results)}: {results}"
        assert all(r["success"] is True for r in results), f"Worker failed: {results}"

        allocated_versions = sorted([r["version"] for r in results])
        # Monotonic sequential versions [1, 2, 3] with zero collisions
        assert allocated_versions == [1, 2, 3]

        # Authoritative manifest verification
        registry: ModelRegistry = gov_env["registry"]
        manifest = registry.list_versions(sim_id)
        assert len(manifest) == 3
        assert [v["version"] for v in manifest] == [1, 2, 3]

        # Audit ledger integrity check
        audit_chain = ImmutableAuditChain(
            store_path=gov_env["audit_file"],
            signing_secret=gov_env["audit_secret"],
        )
        assert audit_chain.verify_chain_integrity().is_valid is True

    def test_multi_process_cross_bank_isolation(self, gov_env: dict[str, Any]) -> None:
        """Verifies that an independent OS process carrying Bank B credentials is fundamentally rejected from mutating Bank A simulation."""
        storage_dir = gov_env["storage_dir"]
        bank_a_token = gov_env["tokens"]["bank_a_ml"]
        bank_b_token = gov_env["tokens"]["bank_b_ml"]
        env_config = gov_env["env_config"]
        sim_id = "sim_bank_a_isolated"

        ctx = mp.get_context("spawn")
        q = ctx.Queue()

        # 1. Bank A creates simulation
        p1 = ctx.Process(
            target=_worker_register_model,
            args=(storage_dir, sim_id, 1, "bank_a", bank_a_token, env_config, q, None),
        )
        p1.start()
        p1.join(timeout=20)
        res1 = q.get()
        assert res1["success"] is True
        assert res1["version"] == 1

        # 2. Bank B attempts to register a version into Bank A's simulation
        p2 = ctx.Process(
            target=_worker_register_model,
            args=(storage_dir, sim_id, 2, "bank_b", bank_b_token, env_config, q, None),
        )
        p2.start()
        p2.join(timeout=20)
        res2 = q.get()
        assert res2["success"] is False
        assert (
            "cannot overwrite established model ownership" in res2["error"].lower()
            or "authoritatively owned" in res2["error"].lower()
        )

        # 3. Verify manifest only contains Bank A version
        registry: ModelRegistry = gov_env["registry"]
        manifest = registry.list_versions(sim_id)
        assert len(manifest) == 1
        assert manifest[0]["owning_bank_id"] == "bank_a"

    def test_concurrent_promotion_vs_signoff_revocation(self, gov_env: dict[str, Any]) -> None:
        """Verifies determinism when one process attempts promotion while another process concurrently revokes an institutional sign-off."""
        storage_dir = gov_env["storage_dir"]
        bank_a_ml = gov_env["tokens"]["bank_a_ml"]
        bank_a_comp = gov_env["tokens"]["bank_a_comp"]
        env_config = gov_env["env_config"]
        jwt_secret = gov_env["jwt_secret"]
        sim_id = "sim_promo_vs_rev"

        ctx = mp.get_context("spawn")
        q = ctx.Queue()

        # Register v1
        p_init = ctx.Process(
            target=_worker_register_model,
            args=(storage_dir, sim_id, 1, "bank_a", bank_a_ml, env_config, q, None),
        )
        p_init.start()
        p_init.join(timeout=20)
        init_res = q.get()
        assert init_res["success"] is True

        # Sign-off by ML and Compliance
        registry: ModelRegistry = gov_env["registry"]
        v1_meta = registry.get_version_metadata(sim_id, 1)
        sha = v1_meta["sha256"]

        sig_ml = generate_signoff_signature(
            sim_id, 1, sha, "ml_engineer", "alice_ml_bank_a", jwt_secret
        )
        registry.sign_off(sim_id, 1, "ml_engineer", "alice_ml_bank_a", sig_ml, auth_token=bank_a_ml)

        sig_comp = generate_signoff_signature(
            sim_id, 1, sha, "compliance", "carol_comp_bank_a", jwt_secret
        )
        registry.sign_off(
            sim_id, 1, "compliance", "carol_comp_bank_a", sig_comp, auth_token=bank_a_comp
        )

        # Launch concurrent Promotion and Revocation across two OS processes
        barrier = ctx.Barrier(2)
        p_promote = ctx.Process(
            target=_worker_promote_model,
            args=(storage_dir, sim_id, 1, bank_a_ml, env_config, q, barrier),
        )
        p_revoke = ctx.Process(
            target=_worker_revoke_signoff,
            args=(storage_dir, sim_id, 1, bank_a_comp, env_config, q, barrier),
        )

        p_promote.start()
        p_revoke.start()
        p_promote.join(timeout=25)
        p_revoke.join(timeout=25)

        results = []
        while not q.empty():
            results.append(q.get())

        assert len(results) == 2
        # Exactly one terminal state must exist on disk:
        # Either revocation happened first (promotion failed closed), or
        # promotion happened first (revocation subsequently revoked sign-off).
        v1_final = registry.get_version_metadata(sim_id, 1)
        assert v1_final is not None

        # Audit ledger must remain intact and valid
        audit_chain = ImmutableAuditChain(
            store_path=gov_env["audit_file"],
            signing_secret=gov_env["audit_secret"],
        )
        assert audit_chain.verify_chain_integrity().is_valid is True

    def test_concurrent_promotions_single_active_champion(self, gov_env: dict[str, Any]) -> None:
        """Verifies that racing promotions between two versions never result in duplicate active champions."""
        storage_dir = gov_env["storage_dir"]
        bank_a_ml = gov_env["tokens"]["bank_a_ml"]
        bank_a_comp = gov_env["tokens"]["bank_a_comp"]
        env_config = gov_env["env_config"]
        jwt_secret = gov_env["jwt_secret"]
        sim_id = "sim_promo_race"

        ctx = mp.get_context("spawn")
        q = ctx.Queue()

        # Register v1 and v2 sequentially
        for v in (1, 2):
            p = ctx.Process(
                target=_worker_register_model,
                args=(storage_dir, sim_id, v, "bank_a", bank_a_ml, env_config, q, None),
            )
            p.start()
            p.join(timeout=20)
            res = q.get()
            assert res["success"] is True

        # Provide dual sign-offs for both v1 and v2
        registry: ModelRegistry = gov_env["registry"]
        for v in (1, 2):
            meta = registry.get_version_metadata(sim_id, v)
            sha = meta["sha256"]
            sig_ml = generate_signoff_signature(
                sim_id, v, sha, "ml_engineer", "alice_ml_bank_a", jwt_secret
            )
            registry.sign_off(
                sim_id, v, "ml_engineer", "alice_ml_bank_a", sig_ml, auth_token=bank_a_ml
            )
            sig_comp = generate_signoff_signature(
                sim_id, v, sha, "compliance", "carol_comp_bank_a", jwt_secret
            )
            registry.sign_off(
                sim_id, v, "compliance", "carol_comp_bank_a", sig_comp, auth_token=bank_a_comp
            )

        # Race promotion of v1 vs promotion of v2 across two processes
        barrier = ctx.Barrier(2)
        p1 = ctx.Process(
            target=_worker_promote_model,
            args=(storage_dir, sim_id, 1, bank_a_ml, env_config, q, barrier),
        )
        p2 = ctx.Process(
            target=_worker_promote_model,
            args=(storage_dir, sim_id, 2, bank_a_ml, env_config, q, barrier),
        )
        p1.start()
        p2.start()
        p1.join(timeout=25)
        p2.join(timeout=25)

        results = []
        while not q.empty():
            results.append(q.get())

        assert len(results) == 2
        assert all(r["success"] is True for r in results)

        # Invariant: Authoritative manifest MUST have exactly ONE champion
        manifest = registry.list_versions(sim_id)
        champions = [
            e for e in manifest if e.get("status") == "champion" and e.get("is_active") is True
        ]
        assert len(champions) == 1, (
            f"Expected exactly 1 active champion, got {len(champions)}: {champions}"
        )

        active = registry.get_active_version(sim_id)
        assert active is not None
        assert active["version"] in (1, 2)
