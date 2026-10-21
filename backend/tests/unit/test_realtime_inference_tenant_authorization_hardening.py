"""Automated Focused Verification Suite for Realtime Inference Tenant Authorization Hardening.

Verifies Security Contracts A through H:
A. Bank A has no authorized champion; Bank B has the sole active champion. Bank A omits simulation_id.
   Inference must NOT execute Bank B's model.
B. Bank B's champion is configured as the global default. Bank A omits simulation_id.
   Access must fail unless Bank A is independently authorized.
C. Bank A has exactly one authorized champion. Omitted simulation_id resolves to that champion
   and inference succeeds.
D. Multiple authorized champions require explicit selection (HTTP 503).
E. Explicitly requested Bank B simulation_id is rejected for Bank A (HTTP 403).
F. Unauthorized requests do not invoke get_scripted_model(), access local/Redis model caches,
   or execute the forward pass.
G. Both /api/v1/inference/score and /v1/inference/score enforce identical behavior.
H. Existing authorization and secure serving regressions remain valid.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any
from unittest.mock import patch

import pytest
import torch
from fastapi.testclient import TestClient

from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.config import get_settings
from app.main import app
from app.presentation.routers.realtime_inference import (
    reset_circuit_breaker,
    reset_model_cache,
)

PROD_SECRET = "production_super_secure_signing_key_32_bytes_long_12345!"
PROD_AUTH_SECRET = "production-identity-authority-secret-high-entropy-64bytes!"


_PROV_CONTEXT: dict[str, Any] = {}


@pytest.fixture
def test_env(monkeypatch):
    """Sets up an isolated registry directory and resets caches."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_focused_test_reg_")
    store_path = os.path.join(temp_dir, "harden_identity_store.json")
    trust_file = os.path.join(temp_dir, "trust.json")
    audit_file = os.path.join(temp_dir, "audit.json")

    old_storage = os.environ.get("CFI_STORAGE_DIR")
    old_default = os.environ.get("CFI_DEFAULT_SIMULATION_ID")
    os.environ["CFI_STORAGE_DIR"] = temp_dir
    os.environ["CFI_TRAINING_RECEIPT_TRUST_STORE"] = trust_file
    os.environ["CFI_AUDIT_LEDGER_PATH"] = audit_file
    os.environ["CFI_JWT_SIGNING_SECRET"] = PROD_SECRET
    os.environ["CFI_IDENTITY_STORE_SECRET"] = PROD_AUTH_SECRET
    os.environ["CFI_IDENTITY_STORE_PATH"] = store_path

    settings = get_settings()
    monkeypatch.setattr(settings, "identity_store_path", store_path)
    monkeypatch.setattr(settings, "oidc_jwt_signing_secret", PROD_SECRET)
    monkeypatch.setattr(settings, "identity_store_secret", PROD_AUTH_SECRET)
    monkeypatch.setattr(settings, "payload_signing_secret", PROD_AUTH_SECRET)

    from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
    ImmutableAuditChain.reset_instance()

    from app.infrastructure.security.auth_service import AuthenticationService
    AuthenticationService.reset_instance()
    AuthenticationService(
        signing_secret=PROD_SECRET,
        store_path=store_path,
        authority_secret=PROD_AUTH_SECRET,
        app_env="production",
    )

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from app.application.services.training_provenance_service import (
        DurableTrustStore,
        reset_default_training_verifier,
        reset_default_trust_store,
    )

    reset_default_trust_store()
    reset_default_training_verifier()

    admin_token = "adm-sec-12345"
    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_focused_01"
    trust_store.register_approved_key(key_id=kid, public_key_hex=pub_hex, admin_token=admin_token)

    _PROV_CONTEXT["trust_store"] = trust_store
    _PROV_CONTEXT["priv"] = priv
    _PROV_CONTEXT["kid"] = kid

    reset_circuit_breaker()
    reset_model_cache()

    yield temp_dir

    reset_circuit_breaker()
    reset_model_cache()
    AuthenticationService.reset_instance()
    ImmutableAuditChain.reset_instance()
    reset_default_trust_store()
    reset_default_training_verifier()

    if old_storage is not None:
        os.environ["CFI_STORAGE_DIR"] = old_storage
    else:
        os.environ.pop("CFI_STORAGE_DIR", None)

    if old_default is not None:
        os.environ["CFI_DEFAULT_SIMULATION_ID"] = old_default
    else:
        os.environ.pop("CFI_DEFAULT_SIMULATION_ID", None)

    os.environ.pop("CFI_TRAINING_RECEIPT_TRUST_STORE", None)
    os.environ.pop("CFI_AUDIT_LEDGER_PATH", None)
    os.environ.pop("CFI_JWT_SIGNING_SECRET", None)
    os.environ.pop("CFI_IDENTITY_STORE_SECRET", None)
    os.environ.pop("CFI_IDENTITY_STORE_PATH", None)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_tokens(test_env) -> dict[str, str]:
    from app.infrastructure.security.auth_service import AuthenticationService
    auth_svc = AuthenticationService.get_instance()
    user_a = auth_svc.register_user("analyst_a", "Password2026!", "bank_a", ["analyst"])
    token_a = auth_svc.create_token_bundle(user_a).access_token

    user_b = auth_svc.register_user("analyst_b", "Password2026!", "bank_b", ["analyst"])
    token_b = auth_svc.create_token_bundle(user_b).access_token

    return {
        "bank_a": token_a,
        "bank_b": token_b,
    }


def _create_champion_for_bank(
    storage_dir: str,
    simulation_id: str,
    owning_bank_id: str,
) -> dict[str, Any]:
    """Helper: Trains a minimal model and registers it as champion owned by owning_bank_id."""
    import hashlib
    import io
    from datetime import UTC, datetime

    from app.application.services.model_registry import generate_signoff_signature
    from app.application.services.training_provenance_service import (
        TrainingExecutionEvidence,
        TrainingReceiptIssuer,
    )
    from app.infrastructure.security.auth_service import AuthenticationService

    settings = get_settings()
    svc = ModelService(settings)
    registry = ModelRegistry(storage_dir=storage_dir)

    model = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    model_bytes = buf.getvalue()
    sha256 = hashlib.sha256(model_bytes).hexdigest()

    trust_store = _PROV_CONTEXT["trust_store"]
    priv = _PROV_CONTEXT["priv"]
    kid = _PROV_CONTEXT["kid"]

    issuer = TrainingReceiptIssuer(
        key_id=kid,
        private_key=priv,
        require_authenticated_job=False,
        app_env="development",
        trust_store=trust_store,
    )

    ev = TrainingExecutionEvidence(
        training_run_id=f"run-{simulation_id}",
        job_id=f"job-{simulation_id}",
        simulation_id=simulation_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=1,
        executed_epochs=1,
        sample_count=100,
        observed_metrics={"loss": 0.1, "auc": 0.9},
        artifact_sha256=sha256,
        architecture="FraudDetectionModel-GroupNorm",
        feature_schema_version="1.0.0",
        feature_names=[f"f_{i}" for i in range(NUM_FEATURES)],
    )
    receipt = issuer.issue_receipt(
        evidence=ev,
        model_bytes=model_bytes,
        owning_bank_id=owning_bank_id,
    )

    entry = registry.save_version(
        simulation_id=simulation_id,
        state_dict=model.state_dict(),
        artifact_bytes=model_bytes,
        training_receipt=receipt.to_dict(),
        is_promoted=True,
        status="champion",
        provenance_type="TRAINING_RECORDED",
        expected_architecture="FraudDetectionModel-GroupNorm",
        expected_feature_schema_version="1.0.0",
        expected_input_dim=NUM_FEATURES,
        expected_feature_names=[f"f_{i}" for i in range(NUM_FEATURES)],
    )
    registry._save_ownership(
        simulation_id,
        {
            "simulation_id": simulation_id,
            "owning_bank_id": owning_bank_id,
            "consortium_id": None,
            "bound_at": "2026-01-01T00:00:00Z",
            "bound_by": "test_seed",
            "is_unbound": False,
        },
    )

    # In production, get_active_champion_identity requires valid dual sign-offs
    auth_svc = AuthenticationService.get_instance()
    u_ml = auth_svc.register_user(f"ml_{simulation_id}", "Pass123!Secure", owning_bank_id, roles=["ml_engineer"], clearance_level=2)
    u_cp = auth_svc.register_user(f"cp_{simulation_id}", "Pass123!Secure", owning_bank_id, roles=["compliance"], clearance_level=3)

    version = entry["version"]
    sig_ml = generate_signoff_signature(simulation_id, version, sha256, "ml_engineer", u_ml.username, signing_secret=PROD_AUTH_SECRET)
    sig_cp = generate_signoff_signature(simulation_id, version, sha256, "compliance", u_cp.username, signing_secret=PROD_AUTH_SECRET)

    manifest = registry._load_manifest(simulation_id)
    manifest_entry = next(e for e in manifest if e["version"] == version)
    manifest_entry["status"] = "champion"
    manifest_entry["is_active"] = True
    manifest_entry["sign_offs"] = [
        {
            "role": "ml_engineer",
            "user": u_ml.username,
            "signature": sig_ml,
            "signature_scheme": "APPLICATION_HMAC_SHA256",
            "is_signature_valid": True,
            "is_role_verified": True,
            "is_identity_verified": True,
            "is_independent_human_authorization": True,
            "payload_bound": f"{simulation_id}:{version}:{sha256}:ml_engineer:{u_ml.username}",
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
            "payload_bound": f"{simulation_id}:{version}:{sha256}:compliance:{u_cp.username}",
            "timestamp": datetime.now(UTC).isoformat(),
            "fairness_score": 1.0,
            "bias_metric": 0.0,
            "drift_divergence": 0.0,
            "is_revoked": False,
        },
    ]
    registry._save_manifest(simulation_id, manifest)
    return manifest_entry


def _make_prod_payload(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Returns a fully valid production scoring payload."""
    payload = {
        "transaction_id": "tx_focus_001",
        "amount": 100.0,
        "merchant_category": "grocery",
        "velocity_1h": 1,
        "country_code": "US",
        "device_type": "mobile_app",
        "merchant_risk_score": 0.05,
        "customer_history_score": 0.95,
        "chargeback_count": 0.0,
        "account_age_days": 365.0,
        "hour_of_day": 12.0,
    }
    if extra:
        payload.update(extra)
    return payload


# ── Focused Test A: Bank A has no champion; Bank B has sole active champion ────

def test_focused_a_bank_a_no_champion_bank_b_sole_active_cannot_execute_b(
    test_env: str, client: TestClient, auth_tokens: dict[str, str]
):
    """A. Bank A has no authorized champion; Bank B has the sole active champion.

    Bank A omits simulation_id. Inference must NOT execute Bank B's model.
    """
    _create_champion_for_bank(test_env, "sim_bank_b_only", "bank_b")

    token_a = auth_tokens["bank_a"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    payload = _make_prod_payload()
    assert "simulation_id" not in payload

    with patch.object(get_settings(), "app_env", "production"):
        with patch("app.presentation.routers.realtime_inference.get_scripted_model") as mock_get_model:
            resp_v1 = client.post("/v1/inference/score", json=payload, headers=headers_a)
            assert resp_v1.status_code == 404
            assert "no authorized active champion model found" in resp_v1.text.lower()
            mock_get_model.assert_not_called()

            resp_api = client.post("/api/v1/inference/score", json=payload, headers=headers_a)
            assert resp_api.status_code == 404
            assert "no authorized active champion model found" in resp_api.text.lower()
            mock_get_model.assert_not_called()


# ── Focused Test B: Bank B's champion configured as global default ─────────────

def test_focused_b_bank_b_champion_global_default_bank_a_omitted_fails(
    test_env: str, client: TestClient, auth_tokens: dict[str, str], monkeypatch: pytest.MonkeyPatch
):
    """B. Bank B's champion is configured as global default. Bank A omits simulation_id.

    Access must fail unless Bank A is independently authorized.
    """
    _create_champion_for_bank(test_env, "sim_bank_b_default", "bank_b")

    monkeypatch.setenv("CFI_DEFAULT_SIMULATION_ID", "sim_bank_b_default")
    monkeypatch.setattr(get_settings(), "default_simulation_id", "sim_bank_b_default")

    token_a = auth_tokens["bank_a"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    payload = _make_prod_payload()

    with patch.object(get_settings(), "app_env", "production"):
        with patch("app.presentation.routers.realtime_inference.get_scripted_model") as mock_get_model:
            resp_v1 = client.post("/v1/inference/score", json=payload, headers=headers_a)
            assert resp_v1.status_code in (403, 404)
            mock_get_model.assert_not_called()

            resp_api = client.post("/api/v1/inference/score", json=payload, headers=headers_a)
            assert resp_api.status_code in (403, 404)
            mock_get_model.assert_not_called()


# ── Focused Test C: Bank A has exactly one authorized champion ──────────────────

def test_focused_c_bank_a_exactly_one_champion_omitted_sim_succeeds(
    test_env: str, client: TestClient, auth_tokens: dict[str, str]
):
    """C. Bank A has exactly one authorized champion.

    Omitted simulation_id resolves to that champion and inference succeeds.
    """
    _create_champion_for_bank(test_env, "sim_bank_a_champion", "bank_a")

    token_a = auth_tokens["bank_a"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    payload = _make_prod_payload()
    assert "simulation_id" not in payload

    with patch.object(get_settings(), "app_env", "production"):
        resp_v1 = client.post("/v1/inference/score", json=payload, headers=headers_a)
        assert resp_v1.status_code == 200, f"Failed: {resp_v1.status_code} - {resp_v1.text}"
        d1 = resp_v1.json()
        assert d1["evaluated_by"] == "ML_MODEL"
        assert 0.0 <= d1["risk_score"] <= 1.0

        resp_api = client.post("/api/v1/inference/score", json=payload, headers=headers_a)
        assert resp_api.status_code == 200
        d2 = resp_api.json()
        assert d2["evaluated_by"] == "ML_MODEL"
        assert d2["risk_score"] == d1["risk_score"]


# ── Focused Test D: Multiple authorized champions require explicit selection ───

def test_focused_d_multiple_authorized_champions_require_explicit_selection(
    test_env: str, client: TestClient, auth_tokens: dict[str, str]
):
    """D. Multiple authorized champions require explicit selection (HTTP 503)."""
    _create_champion_for_bank(test_env, "sim_bank_a_one", "bank_a")
    _create_champion_for_bank(test_env, "sim_bank_a_two", "bank_a")

    token_a = auth_tokens["bank_a"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    payload = _make_prod_payload()
    assert "simulation_id" not in payload

    with patch.object(get_settings(), "app_env", "production"):
        with patch("app.presentation.routers.realtime_inference.get_scripted_model") as mock_get_model:
            resp_v1 = client.post("/v1/inference/score", json=payload, headers=headers_a)
            assert resp_v1.status_code == 503, f"Expected 503, got {resp_v1.status_code} - {resp_v1.text}"
            assert "ambiguous model selection" in resp_v1.text.lower()
            mock_get_model.assert_not_called()

            resp_api = client.post("/api/v1/inference/score", json=payload, headers=headers_a)
            assert resp_api.status_code == 503
            assert "ambiguous model selection" in resp_api.text.lower()
            mock_get_model.assert_not_called()


# ── Focused Test E: Explicitly requested Bank B simulation_id rejected ──────────

def test_focused_e_explicitly_requested_bank_b_sim_rejected_for_bank_a(
    test_env: str, client: TestClient, auth_tokens: dict[str, str]
):
    """E. Explicitly requested Bank B simulation_id is rejected for Bank A (HTTP 403)."""
    _create_champion_for_bank(test_env, "sim_bank_b_exclusive", "bank_b")

    token_a = auth_tokens["bank_a"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    payload = _make_prod_payload({"simulation_id": "sim_bank_b_exclusive"})

    with patch.object(get_settings(), "app_env", "production"):
        with patch("app.presentation.routers.realtime_inference.get_scripted_model") as mock_get_model:
            resp_v1 = client.post("/v1/inference/score", json=payload, headers=headers_a)
            assert resp_v1.status_code == 403
            assert "broken access control" in resp_v1.text.lower() or "not authorized" in resp_v1.text.lower()
            mock_get_model.assert_not_called()

            resp_api = client.post("/api/v1/inference/score", json=payload, headers=headers_a)
            assert resp_api.status_code == 403
            assert "broken access control" in resp_api.text.lower() or "not authorized" in resp_api.text.lower()
            mock_get_model.assert_not_called()


# ── Focused Test F: Unauthorized requests do not invoke model or caches ─────────

def test_focused_f_unauthorized_requests_do_not_invoke_model_or_caches(
    test_env: str, client: TestClient, auth_tokens: dict[str, str]
):
    """F. Unauthorized requests do not invoke get_scripted_model(), access local/Redis model caches,

    or execute the forward pass.
    """
    _create_champion_for_bank(test_env, "sim_bank_b_only", "bank_b")

    token_a = auth_tokens["bank_a"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    payload_unauthorized = _make_prod_payload({"simulation_id": "sim_bank_b_only"})

    with patch("app.presentation.routers.realtime_inference.get_scripted_model") as mock_get_model, \
         patch("app.presentation.routers.realtime_inference._local_cache.get") as mock_cache_get, \
         patch("app.presentation.routers.realtime_inference._get_redis_client") as mock_redis_get:

        resp = client.post("/v1/inference/score", json=payload_unauthorized, headers=headers_a)
        assert resp.status_code == 403

        # Critical: No model loading, cache inspection, or Redis reads occurred
        mock_get_model.assert_not_called()
        mock_cache_get.assert_not_called()
        mock_redis_get.assert_not_called()


# ── Focused Test G: Endpoint parity between /api/v1 and /v1 ─────────────────────

def test_focused_g_endpoints_parity(
    test_env: str, client: TestClient, auth_tokens: dict[str, str]
):
    """G. Both /api/v1/inference/score and /v1/inference/score enforce identical behavior."""
    _create_champion_for_bank(test_env, "sim_bank_a_shared", "bank_a")

    token_a = auth_tokens["bank_a"]
    headers_a = {"Authorization": f"Bearer {token_a}"}

    payload = _make_prod_payload({"simulation_id": "sim_bank_a_shared"})

    resp1 = client.post("/v1/inference/score", json=payload, headers=headers_a)
    resp2 = client.post("/api/v1/inference/score", json=payload, headers=headers_a)

    assert resp1.status_code == resp2.status_code == 200
    assert resp1.json()["decision"] == resp2.json()["decision"]
    assert resp1.json()["evaluated_by"] == resp2.json()["evaluated_by"] == "ML_MODEL"
