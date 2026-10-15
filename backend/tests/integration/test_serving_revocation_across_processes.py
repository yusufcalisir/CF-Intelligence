"""Integration tests verifying model, sign-off, and issuer key revocation across independent OS processes.

Verifies:
1. Serving Worker A (Process 1) loads and caches approved champion v1 in process-local memory.
2. Governance Worker B (Process 2) revokes the active model version in authoritative registry.
3. Without restarting Worker A, subsequent inference request immediately fails closed (HTTP 503).
4. Governance Worker B revokes compliance sign-off in authoritative registry; Worker A immediately rejects serving.
5. Governance Worker B revokes signing key in shared trust store; Worker A immediately evicts and halts serving.
6. Governance Worker B registers and promotes champion v2; Worker A automatically reloads and serves v2.

Every test uses real spawned OS processes (multiprocessing.get_context("spawn")), real filesystem state,
real Ed25519 cryptographic receipts, and real HMAC audit chains.
"""

from __future__ import annotations

import hashlib
import io
import multiprocessing as mp
import os
from pathlib import Path
from typing import Any

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import HTTPException

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
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_account_store import DurableAccountStore
from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


def _serving_worker_entrypoint(
    cmd_queue: mp.Queue,
    resp_queue: mp.Queue,
    env_vars: dict[str, str],
) -> None:
    """Entrypoint for Serving Worker A running in an independent spawned OS process."""
    try:
        for k, v in env_vars.items():
            os.environ[k] = v

        from app.config import get_settings

        get_settings.cache_clear()

        import app.presentation.routers.predict as predict_router
        from app.application.services.model_registry import ModelRegistry
        from app.application.services.training_provenance_service import (
            DurableTrustStore,
            TrainingReceiptVerifier,
        )
        from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store

        reset_default_nonce_store()
        predict_router.clear_all_serving_caches()

        trust_file = env_vars["CFI_TRUST_STORE_PATH"]
        admin_token = env_vars["CFI_TRUST_STORE_ADMIN_TOKEN"]
        trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
        verifier = TrainingReceiptVerifier(trust_store=trust_store)
        registry = ModelRegistry(storage_dir=env_vars["CFI_STORAGE_DIR"], verifier=verifier)
        predict_router._registry = registry

        resp_queue.put({"status": "ready", "pid": os.getpid()})

        while True:
            msg = cmd_queue.get()
            cmd = msg.get("cmd")
            if cmd == "stop":
                resp_queue.put({"status": "stopped"})
                break
            elif cmd == "predict":
                sim_id = msg["simulation_id"]
                try:
                    model = predict_router._get_cached_serving_model(simulation_id=sim_id)
                    with torch.no_grad():
                        inp = torch.ones(1, NUM_FEATURES)
                        out = float(model(inp).item())

                    cached_entry = predict_router._serving_model_cache.get(sim_id)
                    cached_ver = cached_entry[1].get("version") if cached_entry else None
                    resp_queue.put(
                        {
                            "status": "ok",
                            "version": cached_ver,
                            "prediction": out,
                        }
                    )
                except HTTPException as http_exc:
                    resp_queue.put(
                        {
                            "status": "http_error",
                            "status_code": http_exc.status_code,
                            "detail": http_exc.detail,
                        }
                    )
                except Exception as exc:
                    resp_queue.put(
                        {
                            "status": "error",
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        }
                    )
            elif cmd == "get_cached_version":
                sim_id = msg["simulation_id"]
                cached_entry = predict_router._serving_model_cache.get(sim_id)
                cached_ver = cached_entry[1].get("version") if cached_entry else None
                resp_queue.put({"status": "ok", "cached_version": cached_ver})
            elif cmd == "set_app_env":
                os.environ["APP_ENV"] = msg["app_env"]
                get_settings.cache_clear()
                resp_queue.put({"status": "ok"})
            else:
                resp_queue.put({"status": "error", "error": f"Unknown command: {cmd}"})
    except Exception as fatal_exc:
        resp_queue.put(
            {
                "status": "fatal_error",
                "error": str(fatal_exc),
                "error_type": type(fatal_exc).__name__,
            }
        )


def _governance_worker_entrypoint(
    cmd_queue: mp.Queue,
    resp_queue: mp.Queue,
    env_vars: dict[str, str],
) -> None:
    """Entrypoint for Governance Worker B running in an independent spawned OS process."""
    try:
        for k, v in env_vars.items():
            os.environ[k] = v

        from app.config import get_settings

        get_settings.cache_clear()

        from app.application.services.model_registry import ModelRegistry
        from app.application.services.training_provenance_service import (
            DurableTrustStore,
            TrainingReceiptVerifier,
        )
        from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store

        reset_default_nonce_store()

        trust_file = env_vars["CFI_TRUST_STORE_PATH"]
        admin_token = env_vars["CFI_TRUST_STORE_ADMIN_TOKEN"]
        trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
        verifier = TrainingReceiptVerifier(trust_store=trust_store)
        registry = ModelRegistry(storage_dir=env_vars["CFI_STORAGE_DIR"], verifier=verifier)

        resp_queue.put({"status": "ready", "pid": os.getpid()})

        while True:
            msg = cmd_queue.get()
            cmd = msg.get("cmd")
            if cmd == "stop":
                resp_queue.put({"status": "stopped"})
                break
            elif cmd == "revoke_version":
                try:
                    entry = registry.revoke_version(
                        simulation_id=msg["simulation_id"],
                        version=msg["version"],
                        reason=msg["reason"],
                        auth_token=msg["auth_token"],
                    )
                    resp_queue.put({"status": "ok", "entry": entry})
                except Exception as exc:
                    resp_queue.put(
                        {
                            "status": "error",
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        }
                    )
            elif cmd == "revoke_sign_off":
                try:
                    res = registry.revoke_sign_off(
                        simulation_id=msg["simulation_id"],
                        version=msg["version"],
                        role=msg["role"],
                        user=msg["user"],
                        reason=msg["reason"],
                        auth_token=msg["auth_token"],
                    )
                    resp_queue.put({"status": "ok", "result": res})
                except Exception as exc:
                    resp_queue.put(
                        {
                            "status": "error",
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        }
                    )
            elif cmd == "revoke_key":
                try:
                    res = trust_store.revoke_key(
                        key_id=msg["key_id"],
                        reason=msg["reason"],
                        admin_token=msg["admin_token"],
                    )
                    resp_queue.put({"status": "ok", "result": res})
                except Exception as exc:
                    resp_queue.put(
                        {
                            "status": "error",
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        }
                    )
            elif cmd == "promote_version":
                try:
                    res = registry.promote_version(
                        simulation_id=msg["simulation_id"],
                        version=msg["version"],
                        target_status=msg.get("target_status", "champion"),
                        auth_token=msg["auth_token"],
                    )
                    resp_queue.put({"status": "ok", "result": res})
                except Exception as exc:
                    resp_queue.put(
                        {
                            "status": "error",
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        }
                    )
            else:
                resp_queue.put({"status": "error", "error": f"Unknown command: {cmd}"})
    except Exception as fatal_exc:
        resp_queue.put(
            {
                "status": "fatal_error",
                "error": str(fatal_exc),
                "error_type": type(fatal_exc).__name__,
            }
        )


@pytest.fixture
def multi_proc_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Sets up shared durable disk storage and configuration for independent OS worker processes."""
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

    from app.config import get_settings

    get_settings.cache_clear()

    # Setup accounts in main process
    reset_default_nonce_store()
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

    u_ml = auth_svc.register_user(
        "alice_ml_proc", "SecurePass1234!", "bank_a", ["ml_engineer"], clearance_level=2
    )
    u_comp = auth_svc.register_user(
        "carol_comp_proc", "SecurePass1234!", "bank_a", ["compliance"], clearance_level=3
    )

    tokens = {
        "ml": auth_svc.create_token_bundle(u_ml).access_token,
        "comp": auth_svc.create_token_bundle(u_comp).access_token,
    }

    # Setup training authority key in shared trust store
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_hex = (
        priv_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )
    key_id = "training_key_proc_01"
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
        "env_config": env_config,
        "tokens": tokens,
        "key_id": key_id,
        "admin_token": admin_token,
        "issuer": issuer,
        "registry": registry,
        "trust_store": trust_store,
        "jwt_secret": jwt_secret,
    }


def _register_model_helper(
    env: dict[str, Any],
    sim_id: str,
    version: int,
    weight_bias: float = 0.0,
    promote_to: str | None = "champion",
) -> dict[str, Any]:
    """Helper to save, sign off, and optionally promote a model version."""
    issuer: TrainingReceiptIssuer = env["issuer"]
    registry: ModelRegistry = env["registry"]
    tokens = env["tokens"]
    jwt_secret = env["jwt_secret"]

    model = FraudDetectionModel(input_dim=NUM_FEATURES, dp_compatible=True)
    with torch.no_grad():
        for param in model.parameters():
            param.add_(weight_bias)

    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    artifact_bytes = buf.getvalue()
    artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()

    evidence = TrainingExecutionEvidence(
        training_run_id=f"run_{sim_id}_v{version}",
        job_id=f"job_{sim_id}_v{version}",
        simulation_id=sim_id,
        completed_at="2026-10-15T00:00:00Z",
        executed_rounds=2,
        executed_epochs=2,
        sample_count=150,
        observed_metrics={"loss": 0.05},
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

    entry = registry.save_version(
        simulation_id=sim_id,
        state_dict=model.state_dict(),
        artifact_bytes=artifact_bytes,
        training_receipt=receipt,
        provenance_type="TRAINING_EVIDENCE_VERIFIED",
        owning_bank_id="bank_a",
        auth_token=tokens["ml"],
    )

    sig_ml = generate_signoff_signature(
        sim_id, version, artifact_sha, "ml_engineer", "alice_ml_proc", jwt_secret
    )
    registry.sign_off(
        sim_id, version, "ml_engineer", "alice_ml_proc", sig_ml, auth_token=tokens["ml"]
    )
    sig_comp = generate_signoff_signature(
        sim_id, version, artifact_sha, "compliance", "carol_comp_proc", jwt_secret
    )
    registry.sign_off(
        sim_id, version, "compliance", "carol_comp_proc", sig_comp, auth_token=tokens["comp"]
    )

    if promote_to:
        registry.promote_version(sim_id, version, target_status=promote_to, auth_token=tokens["ml"])
    return entry


class WorkerHarness:
    """Manages independent OS worker processes for deterministic cross-process verification."""

    def __init__(self, env_vars: dict[str, str]) -> None:
        self.ctx = mp.get_context("spawn")
        self.env_vars = env_vars

        self.serving_cmd_q = self.ctx.Queue()
        self.serving_resp_q = self.ctx.Queue()
        self.serving_proc = self.ctx.Process(
            target=_serving_worker_entrypoint,
            args=(self.serving_cmd_q, self.serving_resp_q, self.env_vars),
        )

        self.gov_cmd_q = self.ctx.Queue()
        self.gov_resp_q = self.ctx.Queue()
        self.gov_proc = self.ctx.Process(
            target=_governance_worker_entrypoint,
            args=(self.gov_cmd_q, self.gov_resp_q, self.env_vars),
        )

    def start(self) -> None:
        self.serving_proc.start()
        s_ready = self.serving_resp_q.get(timeout=15)
        assert s_ready.get("status") == "ready"

        self.gov_proc.start()
        g_ready = self.gov_resp_q.get(timeout=15)
        assert g_ready.get("status") == "ready"

        assert self.serving_proc.pid != self.gov_proc.pid, (
            "Workers must run in distinct OS processes"
        )
        assert self.serving_proc.pid != os.getpid(), "Worker A must not run in test runner process"
        assert self.gov_proc.pid != os.getpid(), "Worker B must not run in test runner process"

    def stop(self) -> None:
        try:
            if self.serving_proc.is_alive():
                self.serving_cmd_q.put({"cmd": "stop"})
                self.serving_proc.join(timeout=5)
                if self.serving_proc.is_alive():
                    self.serving_proc.kill()
        except Exception:
            pass

        try:
            if self.gov_proc.is_alive():
                self.gov_cmd_q.put({"cmd": "stop"})
                self.gov_proc.join(timeout=5)
                if self.gov_proc.is_alive():
                    self.gov_proc.kill()
        except Exception:
            pass

    def predict_worker_a(self, simulation_id: str) -> dict[str, Any]:
        self.serving_cmd_q.put({"cmd": "predict", "simulation_id": simulation_id})
        return self.serving_resp_q.get(timeout=15)

    def set_app_env_worker_a(self, app_env: str) -> None:
        self.serving_cmd_q.put({"cmd": "set_app_env", "app_env": app_env})
        resp = self.serving_resp_q.get(timeout=10)
        assert resp.get("status") == "ok"

    def gov_revoke_model(
        self, simulation_id: str, version: int, reason: str, auth_token: str
    ) -> dict[str, Any]:
        self.gov_cmd_q.put(
            {
                "cmd": "revoke_version",
                "simulation_id": simulation_id,
                "version": version,
                "reason": reason,
                "auth_token": auth_token,
            }
        )
        return self.gov_resp_q.get(timeout=15)

    def gov_revoke_signoff(
        self,
        simulation_id: str,
        version: int,
        role: str,
        user: str,
        reason: str,
        auth_token: str,
    ) -> dict[str, Any]:
        self.gov_cmd_q.put(
            {
                "cmd": "revoke_sign_off",
                "simulation_id": simulation_id,
                "version": version,
                "role": role,
                "user": user,
                "reason": reason,
                "auth_token": auth_token,
            }
        )
        return self.gov_resp_q.get(timeout=15)

    def gov_revoke_key(self, key_id: str, reason: str, admin_token: str) -> dict[str, Any]:
        self.gov_cmd_q.put(
            {
                "cmd": "revoke_key",
                "key_id": key_id,
                "reason": reason,
                "admin_token": admin_token,
            }
        )
        return self.gov_resp_q.get(timeout=15)

    def gov_promote_version(
        self, simulation_id: str, version: int, auth_token: str
    ) -> dict[str, Any]:
        self.gov_cmd_q.put(
            {
                "cmd": "promote_version",
                "simulation_id": simulation_id,
                "version": version,
                "auth_token": auth_token,
            }
        )
        return self.gov_resp_q.get(timeout=15)


class TestServingRevocationAcrossProcesses:
    """Verifies immediate fail-closed revocation across genuinely independent OS processes."""

    def test_independent_process_model_version_revocation(
        self, multi_proc_env: dict[str, Any]
    ) -> None:
        """Worker B revokes champion v1 in separate process; Worker A immediately fails closed on next inference."""
        sim_id = "sim_ipc_model_rev"
        _register_model_helper(multi_proc_env, sim_id, version=1, promote_to="champion")

        harness = WorkerHarness(multi_proc_env["env_config"])
        harness.start()
        try:
            # 1. Worker A performs inference: successfully warms in-memory cache with v1
            pred1 = harness.predict_worker_a(sim_id)
            assert pred1.get("status") == "ok"
            assert pred1.get("version") == 1
            assert isinstance(pred1.get("prediction"), float)

            # 2. Worker B (separate OS process) revokes version 1 in registry
            gov_res = harness.gov_revoke_model(
                simulation_id=sim_id,
                version=1,
                reason="Materials flaw identified in model weights",
                auth_token=multi_proc_env["tokens"]["comp"],
            )
            assert gov_res.get("status") == "ok"
            entry = gov_res["entry"]
            assert entry["is_revoked"] is True
            assert entry["status"] == "inactive"

            # 3. Worker A (without restart) issues another inference request: MUST fail closed (HTTP 503)
            pred2 = harness.predict_worker_a(sim_id)
            assert pred2.get("status") == "http_error"
            assert pred2.get("status_code") == 503
            assert (
                "no verified active champion model artifact found"
                in str(pred2.get("detail")).lower()
                or "champion manifest resolution failed" in str(pred2.get("detail")).lower()
            )
        finally:
            harness.stop()

    def test_independent_process_signoff_revocation(self, multi_proc_env: dict[str, Any]) -> None:
        """Worker B revokes compliance sign-off; Worker A immediately demotes and halts serving."""
        sim_id = "sim_ipc_signoff_rev"
        _register_model_helper(multi_proc_env, sim_id, version=1, promote_to="champion")

        harness = WorkerHarness(multi_proc_env["env_config"])
        harness.start()
        try:
            # 1. Worker A warms cache
            pred1 = harness.predict_worker_a(sim_id)
            assert pred1.get("status") == "ok"
            assert pred1.get("version") == 1

            # 2. Worker B revokes compliance sign-off
            gov_res = harness.gov_revoke_signoff(
                simulation_id=sim_id,
                version=1,
                role="compliance",
                user="carol_comp_proc",
                reason="Governance audit revealed missing compliance certification",
                auth_token=multi_proc_env["tokens"]["comp"],
            )
            assert gov_res.get("status") == "ok"

            # 3. In production mode, dual sign-offs are strictly mandatory for active serving
            harness.set_app_env_worker_a("production")

            # 4. Worker A attempts inference: rejects model due to revoked compliance sign-off
            pred2 = harness.predict_worker_a(sim_id)
            assert pred2.get("status") == "http_error"
            assert pred2.get("status_code") == 503
        finally:
            harness.stop()

    def test_independent_process_issuer_key_revocation(
        self, multi_proc_env: dict[str, Any]
    ) -> None:
        """Worker B revokes training issuer key in TrustStore; Worker A evicts cached model and halts serving."""
        sim_id = "sim_ipc_key_rev"
        _register_model_helper(multi_proc_env, sim_id, version=1, promote_to="champion")

        harness = WorkerHarness(multi_proc_env["env_config"])
        harness.start()
        try:
            # 1. Worker A warms cache
            pred1 = harness.predict_worker_a(sim_id)
            assert pred1.get("status") == "ok"
            assert pred1.get("version") == 1

            # 2. Worker B revokes signing key in shared trust store
            gov_res = harness.gov_revoke_key(
                key_id=multi_proc_env["key_id"],
                reason="Training authority key compromised",
                admin_token=multi_proc_env["admin_token"],
            )
            assert gov_res.get("status") == "ok"

            # 3. Worker A attempts inference without restart: detects revoked signing key and evicts cache
            pred2 = harness.predict_worker_a(sim_id)
            assert pred2.get("status") == "http_error"
            assert pred2.get("status_code") == 503
            assert (
                "model authority or signing key has been revoked"
                in str(pred2.get("detail")).lower()
                or "no verified active champion model artifact found"
                in str(pred2.get("detail")).lower()
            )
        finally:
            harness.stop()

    def test_independent_process_champion_promotion_reload(
        self, multi_proc_env: dict[str, Any]
    ) -> None:
        """Worker B promotes champion v2; Worker A automatically reloads new weights without restart."""
        sim_id = "sim_ipc_promo_reload"
        _register_model_helper(
            multi_proc_env, sim_id, version=1, weight_bias=0.0, promote_to="champion"
        )
        _register_model_helper(multi_proc_env, sim_id, version=2, weight_bias=3.0, promote_to=None)

        harness = WorkerHarness(multi_proc_env["env_config"])
        harness.start()
        try:
            # 1. Worker A warms cache with v1
            pred1 = harness.predict_worker_a(sim_id)
            assert pred1.get("status") == "ok"
            assert pred1.get("version") == 1
            out1 = pred1["prediction"]

            # 2. Worker B promotes v2 to champion
            gov_res = harness.gov_promote_version(
                simulation_id=sim_id,
                version=2,
                auth_token=multi_proc_env["tokens"]["ml"],
            )
            assert gov_res.get("status") == "ok"

            # 3. Worker A issues inference: detects version change, reloads v2, and computes new output
            pred2 = harness.predict_worker_a(sim_id)
            assert pred2.get("status") == "ok"
            assert pred2.get("version") == 2
            out2 = pred2["prediction"]

            # Invariant: Output reflects genuine updated model weights
            assert out1 != out2, f"Expected output change between v1 and v2, got {out1} == {out2}"
        finally:
            harness.stop()
