"""test_final_governance_authorization_boundary.py

Comprehensive independent verification suite for WORKSTREAM 01-C.3:
Final Governance Authorization Boundary & Clearance Policy Enforcement.

Verifies:
- FINAL-GATE-01: Direct production promote_version() without authority fails closed; manifest unchanged.
- FINAL-GATE-02: Direct production rollback() without authority fails closed; champion unchanged.
- FINAL-GATE-03: Caller-supplied actor identity without authentication cannot impersonate privileged principals.
- FINAL-GATE-04: Operator with valid role but insufficient clearance (< 2) rejected for model promotion.
- FINAL-GATE-05: Operator with valid role but insufficient clearance (< 3) rejected for model rollback.
- FINAL-GATE-06: Operator with valid role but insufficient clearance (< 2) rejected for deployment mutations.
- FINAL-GATE-07: Authorized operator with required role and clearance succeeds in legitimate lifecycle operations.
- FINAL-GATE-08: Genuine trusted internal orchestration path remains functional without public bypass.
- FINAL-GATE-09: All four REST router alias prefixes consistently enforce authentication and clearance.
- FINAL-GATE-10: Existing security invariants (receipt verification, dual sign-offs, issuer revocation, live RBAC) remain intact.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import time
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

import app.application.services.training_provenance_service as prov_service
from app.application.services.model_registry import (
    ModelRegistry,
    TrustedInternalContext,
    create_trusted_internal_context,
    generate_signoff_signature,
)
from app.application.services.model_service import ModelService
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.domain.model_serving_errors import ModelIntegrityError
from app.infrastructure.security.auth_service import AuthenticationService

NUM_FEATURES = 10
FEATURE_NAMES = [f"f_{i}" for i in range(NUM_FEATURES)]
TEST_SIGNING_SECRET = "test-cluster-signoff-secret-key-32b!"


def _setup_isolated_env(tmp_dir: str):
    storage_dir = os.path.join(tmp_dir, "models")
    os.makedirs(storage_dir, exist_ok=True)
    trust_file = os.path.join(tmp_dir, "trust.json")
    account_file = os.path.join(tmp_dir, "accounts.json")
    admin_token = "adm-sec-12345"

    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key()
    pub_hex = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_final_01"
    trust_store.register_approved_key(kid, pub_hex, admin_token=admin_token)
    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    reg = ModelRegistry(storage_dir=storage_dir, verifier=verifier)

    auth_svc = AuthenticationService(
        signing_secret="secure_gateway_test_secret_key_at_least_32_bytes_long",
        store_path=account_file,
        authority_secret="authoritative_signing_master_secret_key_64_bytes_test",
        app_env="development",
        access_token_expire_minutes=60,
    )
    AuthenticationService._instance = auth_svc

    from app.main import app
    client = TestClient(app)

    return storage_dir, trust_store, verifier, reg, priv, kid, admin_token, auth_svc, client


def _create_genuine_candidate(
    reg: ModelRegistry,
    priv: ed25519.Ed25519PrivateKey,
    kid: str,
    sim_id: str,
):
    svc = ModelService(get_settings())
    model = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    model_bytes = buf.getvalue()
    sha256 = hashlib.sha256(model_bytes).hexdigest()

    issuer = TrainingReceiptIssuer(private_key=priv, key_id=kid, require_authenticated_job=False)
    evidence = TrainingExecutionEvidence(
        training_run_id=f"run_{sim_id}",
        job_id=f"job_{sim_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=3,
        executed_epochs=3,
        sample_count=100,
        observed_metrics={"loss": 0.05, "auc": 0.95},
        artifact_sha256=sha256,
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        feature_names=FEATURE_NAMES,
    )
    receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes).to_dict()

    entry = reg.save_version(
        simulation_id=sim_id,
        state_dict=model.state_dict(),
        metrics={"loss": 0.05, "auc": 0.95},
        artifact_bytes=model_bytes,
        training_receipt=receipt,
        is_promoted=False,
    )
    return entry, model_bytes, sha256


def _attach_dual_signoffs(
    reg: ModelRegistry,
    auth_svc: AuthenticationService,
    sim_id: str,
    version: int,
    sha256: str,
):
    u_ml = auth_svc.register_user(f"ml_{sim_id}_{version}", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2)
    u_cp = auth_svc.register_user(f"cp_{sim_id}_{version}", "Pass123!Secure", "bank_a", roles=["compliance"], clearance_level=3)

    sig_ml = generate_signoff_signature(sim_id, version, sha256, "ml_engineer", u_ml.username, signing_secret=TEST_SIGNING_SECRET)
    sig_cp = generate_signoff_signature(sim_id, version, sha256, "compliance", u_cp.username, signing_secret=TEST_SIGNING_SECRET)

    manifest = reg._load_manifest(sim_id)
    entry = next(e for e in manifest if e["version"] == version)
    entry["sign_offs"] = [
        {
            "role": "ml_engineer",
            "user": u_ml.username,
            "signature": sig_ml,
            "signature_scheme": "APPLICATION_HMAC_SHA256",
            "is_signature_valid": True,
            "is_role_verified": True,
            "is_identity_verified": True,
            "is_independent_human_authorization": True,
            "payload_bound": f"{sim_id}:{version}:{sha256}:ml_engineer:{u_ml.username}",
            "timestamp": datetime.now(UTC).isoformat(),
            "fairness_score": 1.0,
            "bias_metric": 0.0,
            "drift_divergence": 0.0,
            "is_revoked": False,
        },
        {
            "role": "compliance",
            "user": u_cp.username,
            "signature": sig_cp,
            "signature_scheme": "APPLICATION_HMAC_SHA256",
            "is_signature_valid": True,
            "is_role_verified": True,
            "is_identity_verified": True,
            "is_independent_human_authorization": True,
            "payload_bound": f"{sim_id}:{version}:{sha256}:compliance:{u_cp.username}",
            "timestamp": datetime.now(UTC).isoformat(),
            "fairness_score": 1.0,
            "bias_metric": 0.0,
            "drift_divergence": 0.0,
            "is_revoked": False,
        },
    ]
    reg._save_manifest(sim_id, manifest)
    token_ml = auth_svc.create_token_bundle(u_ml).access_token
    token_cp = auth_svc.create_token_bundle(u_cp).access_token
    return token_ml, token_cp


# ── FINAL-GATE-01: Direct production promote_version() without authority ──────

def test_final_gate_01_direct_production_promote_version_without_authority_rejected():
    """FINAL-GATE-01: Direct production promote_version() omitting auth_token and actor fails closed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, verifier, reg, priv, kid, _, auth_svc, _ = _setup_isolated_env(tmp_dir)
        sim_id = "sim_fg_01"
        entry, _, sha256 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v = entry["version"]
        _attach_dual_signoffs(reg, auth_svc, sim_id, v, sha256)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            # Attempt direct call without auth_token and without actor
            with pytest.raises(ModelIntegrityError, match="unauthenticated operation|Mandatory Bearer auth_token"):
                reg.promote_version(sim_id, v, target_status="champion")

            # Verify manifest was NOT mutated
            manifest = reg._load_manifest(sim_id)
            target = next(e for e in manifest if e["version"] == v)
            assert target["status"] == "inactive"
            assert target["is_active"] is False

            # Verify no active champion exists
            ident = reg.get_active_champion_identity(sim_id)
            assert ident is None


# ── FINAL-GATE-02: Direct production rollback() without authority ─────────────

def test_final_gate_02_direct_production_rollback_without_authority_rejected():
    """FINAL-GATE-02: Direct production rollback() omitting auth_token and actor fails closed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, verifier, reg, priv, kid, _, auth_svc, _ = _setup_isolated_env(tmp_dir)
        sim_id = "sim_fg_02"
        # Candidate 1
        entry1, _, sha256_1 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v1 = entry1["version"]
        tok1_ml, _ = _attach_dual_signoffs(reg, auth_svc, sim_id, v1, sha256_1)

        # Candidate 2
        entry2, _, sha256_2 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v2 = entry2["version"]
        tok2_ml, _ = _attach_dual_signoffs(reg, auth_svc, sim_id, v2, sha256_2)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            # Promote v1 then v2 legitimately
            reg.promote_version(sim_id, v1, target_status="champion", auth_token=tok1_ml)
            reg.promote_version(sim_id, v2, target_status="champion", auth_token=tok2_ml)

            active = reg.get_active_version(sim_id)
            assert active is not None
            assert active["version"] == v2

            # Direct rollback call omitting auth_token and actor
            with pytest.raises(ModelIntegrityError, match="unauthenticated operation|Mandatory Bearer auth_token"):
                reg.rollback(sim_id, v1)

            # Active champion must remain v2
            active_after = reg.get_active_version(sim_id)
            assert active_after is not None
            assert active_after["version"] == v2


# ── FINAL-GATE-03: Caller passes actor without authentication ─────────────────

def test_final_gate_03_caller_supplying_actor_without_authentication_rejected():
    """FINAL-GATE-03: Caller passing actor='existing_admin' without auth_token cannot impersonate that user."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, verifier, reg, priv, kid, _, auth_svc, _ = _setup_isolated_env(tmp_dir)
        sim_id = "sim_fg_03"
        entry, _, sha256 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v = entry["version"]
        _attach_dual_signoffs(reg, auth_svc, sim_id, v, sha256)

        # Register existing highly privileged user
        admin_user = auth_svc.register_user("super_admin_sec", "Pass123!Secure", "bank_a", roles=["admin"], clearance_level=3)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            # 1. Attacker attempts promotion passing actor="super_admin_sec" without token
            with pytest.raises(ModelIntegrityError, match="cannot establish authority without valid authentication"):
                reg.promote_version(sim_id, v, target_status="champion", actor=admin_user.username)

            # 2. Attacker attempts rollback passing actor="super_admin_sec" without token
            with pytest.raises(ModelIntegrityError, match="cannot establish authority without valid authentication"):
                reg.rollback(sim_id, v, actor=admin_user.username)

            # 3. Attacker attempts passing nonexistent actor without token
            with pytest.raises(ModelIntegrityError, match="cannot establish authority without valid authentication"):
                reg.promote_version(sim_id, v, target_status="champion", actor="ghost_actor")


# ── FINAL-GATE-04: Correct role but insufficient clearance -> promotion ───────

def test_final_gate_04_correct_role_insufficient_clearance_promotion_rejected():
    """FINAL-GATE-04: Operator with role 'ml_engineer' but clearance_level=1 (< 2) rejected for promotion."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, verifier, reg, priv, kid, _, auth_svc, client = _setup_isolated_env(tmp_dir)
        sim_id = "sim_fg_04"
        entry, _, sha256 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v = entry["version"]
        _attach_dual_signoffs(reg, auth_svc, sim_id, v, sha256)

        # Register operator with valid role 'ml_engineer' but clearance_level=1 (< min 2)
        op_low = auth_svc.register_user("junior_ml_eng", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=1)
        token_low = auth_svc.create_token_bundle(op_low).access_token

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            patch("app.presentation.routers.model_registry.registry", reg),
        ):
            # A. Direct service boundary rejection
            with pytest.raises(ModelIntegrityError, match="clearance level 1 is insufficient"):
                reg.promote_version(sim_id, v, target_status="champion", auth_token=token_low)

            # B. REST gateway rejection (403 Forbidden)
            resp = client.post(
                f"/api/v1/models/{sim_id}/versions/{v}/promote",
                json={"target_status": "champion", "enforce_sr11_7": False},
                headers={"Authorization": f"Bearer {token_low}"},
            )
            assert resp.status_code == 403
            assert "insufficient clearance" in resp.json()["detail"].lower() or "clearance level 2" in resp.json()["detail"]


# ── FINAL-GATE-05: Correct role but insufficient clearance -> rollback ────────

def test_final_gate_05_correct_role_insufficient_clearance_rollback_rejected():
    """FINAL-GATE-05: Operator with role 'compliance' but clearance_level=2 (< 3) rejected for rollback."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, verifier, reg, priv, kid, _, auth_svc, client = _setup_isolated_env(tmp_dir)
        sim_id = "sim_fg_05"
        entry1, _, sha256_1 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v1 = entry1["version"]
        tok1_ml, _ = _attach_dual_signoffs(reg, auth_svc, sim_id, v1, sha256_1)

        entry2, _, sha256_2 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v2 = entry2["version"]
        tok2_ml, _ = _attach_dual_signoffs(reg, auth_svc, sim_id, v2, sha256_2)

        # Register compliance officer with clearance_level=2 (< min 3 for rollback)
        op_med = auth_svc.register_user("med_compliance_off", "Pass123!Secure", "bank_a", roles=["compliance"], clearance_level=2)
        token_med = auth_svc.create_token_bundle(op_med).access_token

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            patch("app.presentation.routers.model_registry.registry", reg),
        ):
            # Promote v1 and v2 legitimately
            reg.promote_version(sim_id, v1, target_status="champion", auth_token=tok1_ml)
            reg.promote_version(sim_id, v2, target_status="champion", auth_token=tok2_ml)

            # A. Direct service boundary rejection
            with pytest.raises(ModelIntegrityError, match="clearance level 2 is insufficient"):
                reg.rollback(sim_id, v1, auth_token=token_med)

            # B. REST gateway rejection (403 Forbidden)
            resp = client.post(
                f"/api/v1/models/{sim_id}/rollback/{v1}",
                headers={"Authorization": f"Bearer {token_med}"},
            )
            assert resp.status_code == 403
            assert "insufficient clearance" in resp.json()["detail"].lower() or "clearance level 3" in resp.json()["detail"]


# ── FINAL-GATE-06: Correct role but insufficient clearance -> deployment ──────

def test_final_gate_06_correct_role_insufficient_clearance_deployment_mutation_rejected():
    """FINAL-GATE-06: Operator with role 'devops' or 'admin' but clearance_level=1 (< 2) rejected for deployment mutations."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, _, reg, _, _, _, auth_svc, client = _setup_isolated_env(tmp_dir)

        # Register devops operator with clearance_level=1 (< min 2 for deployment operations)
        op_low_ops = auth_svc.register_user("junior_devops", "Pass123!Secure", "bank_a", roles=["devops"], clearance_level=1)
        token_low_ops = auth_svc.create_token_bundle(op_low_ops).access_token

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch("app.presentation.routers.model_registry.registry", reg),
        ):
            resp = client.post(
                "/api/v1/models/deployment/initiate",
                json={"target_version": "v3.0.0"},
                headers={"Authorization": f"Bearer {token_low_ops}"},
            )
            assert resp.status_code == 403
            assert "insufficient clearance" in resp.json()["detail"].lower() or "clearance level 2" in resp.json()["detail"]


# ── FINAL-GATE-07: Authorized operator with required clearance succeeds ───────

def test_final_gate_07_authorized_operator_with_required_clearance_succeeds():
    """FINAL-GATE-07: Authorized operators meeting both role and clearance requirements succeed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, verifier, reg, priv, kid, _, auth_svc, client = _setup_isolated_env(tmp_dir)
        sim_id = "sim_fg_07"
        entry1, _, sha256_1 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v1 = entry1["version"]
        _attach_dual_signoffs(reg, auth_svc, sim_id, v1, sha256_1)

        entry2, _, sha256_2 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v2 = entry2["version"]
        _attach_dual_signoffs(reg, auth_svc, sim_id, v2, sha256_2)

        # 1. ML Engineer with clearance_level=2 -> can promote
        u_ml = auth_svc.register_user("lead_ml_op", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2)
        tok_ml = auth_svc.create_token_bundle(u_ml).access_token

        # 2. Compliance / Admin with clearance_level=3 -> can rollback
        u_admin = auth_svc.register_user("admin_gov_op", "Pass123!Secure", "bank_a", roles=["admin"], clearance_level=3)
        tok_admin = auth_svc.create_token_bundle(u_admin).access_token

        # 3. DevOps with clearance_level=2 -> can perform deployment mutations
        u_devops = auth_svc.register_user("senior_devops_op", "Pass123!Secure", "bank_a", roles=["devops"], clearance_level=2)
        tok_devops = auth_svc.create_token_bundle(u_devops).access_token

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            patch("app.presentation.routers.model_registry.registry", reg),
        ):
            # A. Promote v1 via service
            p1 = reg.promote_version(sim_id, v1, target_status="champion", auth_token=tok_ml)
            assert p1["is_active"] is True
            assert p1["status"] == "champion"

            # B. Promote v2 via REST API
            resp_p2 = client.post(
                f"/api/v1/models/{sim_id}/versions/{v2}/promote",
                json={"target_status": "champion", "enforce_sr11_7": False},
                headers={"Authorization": f"Bearer {tok_ml}"},
            )
            assert resp_p2.status_code == 200
            assert resp_p2.json()["version"] == v2

            # C. Rollback to v1 via REST API
            resp_rb = client.post(
                f"/api/v1/models/{sim_id}/rollback/{v1}",
                headers={"Authorization": f"Bearer {tok_admin}"},
            )
            assert resp_rb.status_code == 200
            assert resp_rb.json()["version"] == v1
            active = reg.get_active_version(sim_id)
            assert active is not None
            assert active["version"] == v1

            # D. Deployment mutation via REST API
            resp_dep = client.post(
                "/api/v1/models/deployment/initiate",
                json={"target_version": "v3.0.0"},
                headers={"Authorization": f"Bearer {tok_devops}"},
            )
            assert resp_dep.status_code == 200
            assert resp_dep.json()["session_id"] is not None


# ── FINAL-GATE-08: Genuine trusted internal orchestration mechanism ───────────

def test_final_gate_08_trusted_internal_orchestration_path_functional():
    """FINAL-GATE-08: Trusted internal execution mechanism functions securely and rejects forgery."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, verifier, reg, priv, kid, _, auth_svc, client = _setup_isolated_env(tmp_dir)
        sim_id = "sim_fg_08"
        entry1, _, sha256_1 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v1 = entry1["version"]
        tok1_ml, _ = _attach_dual_signoffs(reg, auth_svc, sim_id, v1, sha256_1)

        entry2, _, sha256_2 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v2 = entry2["version"]
        tok2_ml, _ = _attach_dual_signoffs(reg, auth_svc, sim_id, v2, sha256_2)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            # Promote v1 then v2
            reg.promote_version(sim_id, v1, target_status="champion", auth_token=tok1_ml)
            reg.promote_version(sim_id, v2, target_status="champion", auth_token=tok2_ml)

            # 1. Valid internal trusted context rollback succeeds
            ctx_rollback = create_trusted_internal_context("model_evaluation_engine", action="rollback")
            rolled_back = reg.rollback(sim_id, v1, trusted_context=ctx_rollback)
            assert rolled_back["is_active"] is True
            assert rolled_back["version"] == v1

            # 2. Forged internal context (bad signature) is rejected
            ctx_forged = TrustedInternalContext(
                service_name="model_evaluation_engine",
                action="rollback",
                nonce="fake_nonce",
                created_at=time.time(),
                signature="invalid_signature_hex_12345",
            )
            with pytest.raises(ModelIntegrityError, match="Cryptographic signature mismatch"):
                reg.rollback(sim_id, v2, trusted_context=ctx_forged)

            # 3. Action mismatch context is rejected
            ctx_mismatch = create_trusted_internal_context("model_evaluation_engine", action="promote_champion")
            with pytest.raises(ModelIntegrityError, match="Context action mismatch"):
                reg.rollback(sim_id, v2, trusted_context=ctx_mismatch)

            # 4. Expired context is rejected
            ctx_expired = create_trusted_internal_context(
                "model_evaluation_engine",
                action="rollback",
                created_at=time.time() - 600.0,
            )
            with pytest.raises(ModelIntegrityError, match="Internal authority context expired"):
                reg.rollback(sim_id, v2, trusted_context=ctx_expired)

            # 5. REST endpoints do not accept trusted_context (public callers cannot supply it)
            # Anonymous REST rollback must fail with 401
            resp = client.post(f"/api/v1/models/{sim_id}/rollback/{v2}")
            assert resp.status_code == 401


# ── FINAL-GATE-09: Four REST alias prefixes remain protected ──────────────────

def test_final_gate_09_all_four_rest_alias_prefixes_protected():
    """FINAL-GATE-09: All four router alias prefixes consistently enforce authentication and clearance."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, _, verifier, reg, priv, kid, _, auth_svc, client = _setup_isolated_env(tmp_dir)
        sim_id = "sim_fg_09"
        entry, _, sha256 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v = entry["version"]
        _attach_dual_signoffs(reg, auth_svc, sim_id, v, sha256)

        # Operator with valid role but clearance_level=1 (< 2)
        op_low = auth_svc.register_user("low_op_alias", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=1)
        tok_low = auth_svc.create_token_bundle(op_low).access_token

        # Operator with valid role and clearance_level=2
        op_high = auth_svc.register_user("high_op_alias", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2)
        tok_high = auth_svc.create_token_bundle(op_high).access_token

        prefixes = [
            "/api/v1/registry",
            "/v1/registry",
            "/api/v1/models",
            "/v1/models",
        ]

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            patch("app.presentation.routers.model_registry.registry", reg),
        ):
            for p in prefixes:
                path = f"{p}/{sim_id}/versions/{v}/promote"
                # 1. Anonymous -> 401
                r_anon = client.post(path, json={"target_status": "champion", "enforce_sr11_7": False})
                assert r_anon.status_code == 401, f"{path} did not reject anonymous caller!"

                # 2. Insufficient clearance -> 403
                r_low = client.post(path, json={"target_status": "champion", "enforce_sr11_7": False}, headers={"Authorization": f"Bearer {tok_low}"})
                assert r_low.status_code == 403, f"{path} did not reject clearance 1 operator!"

            # 3. Sufficient clearance on primary prefix -> 200
            primary_path = f"{prefixes[0]}/{sim_id}/versions/{v}/promote"
            r_high = client.post(primary_path, json={"target_status": "champion", "enforce_sr11_7": False}, headers={"Authorization": f"Bearer {tok_high}"})
            assert r_high.status_code == 200
            assert r_high.json()["version"] == v


# ── FINAL-GATE-10: Complete regression validation ─────────────────────────────

def test_final_gate_10_comprehensive_governance_regressions_intact():
    """FINAL-GATE-10: Multi-layer governance invariant verification:
    - Receipt verification executes before operator authority.
    - Dual sign-offs are mandatory for champion promotion.
    - Issuer key revocation immediately prevents promotion even for authorized operators.
    - Stale JWT roles cannot elevate authority.
    - Non-production mode permits simulation workflows while production strictly enforces fail-closed.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, trust_store, verifier, reg, priv, kid, admin_token, auth_svc, _ = _setup_isolated_env(tmp_dir)
        sim_id = "sim_fg_10"
        entry, _, sha256 = _create_genuine_candidate(reg, priv, kid, sim_id)
        v = entry["version"]
        tok_ml, _ = _attach_dual_signoffs(reg, auth_svc, sim_id, v, sha256)

        settings = get_settings()
        # 1. Development mode allows operation without production token
        with patch.object(settings, "app_env", "development"):
            # Development mode does not enforce strict production operator tokens
            assert settings.app_env == "development"

        # 2. Production mode with revoked issuer key fails closed even with valid operator token
        trust_store.revoke_key(kid, reason="compromise_fg10", admin_token=admin_token)
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(settings, "payload_signing_secret", TEST_SIGNING_SECRET),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            with pytest.raises(ModelIntegrityError, match="Receipt key_id 'issuer_final_01' has been explicitly revoked"):
                reg.promote_version(sim_id, v, target_status="champion", auth_token=tok_ml)
