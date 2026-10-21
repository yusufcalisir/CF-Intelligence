"""Focused Security Verification Suite for Shared Model Serving Authorization Boundary Hardening.

Verifies Security Contracts A through L:
A. Bank A has no authorized model. Bank B owns the sole active champion. Bank A omits simulation_id on /api/v1/predict.
   Reject before model loading (HTTP 404).
B. Repeat A against batch, explain, and scoring aliases (/api/v1/predict/batch, /api/v1/predict/explain, /api/v1/predict/score).
C. A configured Bank B default champion cannot be executed by Bank A through an omitted simulation_id.
D. Authorization helper never invokes _get_cached_serving_model_with_ident(None) to establish authority.
E. Bank B initially belongs to a consortium and successfully resolves its authorized champion with omitted simulation_id.
F. After warming authorization state, remove Bank B from that consortium WITHOUT changing registry root directory mtime.
G. Bank B's next request must be rejected. A warm authorization cache must not preserve revoked access.
H. Change simulation ownership without changing registry root directory mtime. Previously authorized institutions must not retain access.
I. Explicit unauthorized simulation requests remain rejected (HTTP 403).
J. Legitimate authorized omitted-simulation resolution still succeeds (HTTP 200).
K. Production calls with missing or unauthenticated principals cannot obtain model authority merely by supplying caller_tenant (HTTP 401).
L. Existing security regressions remain valid.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
import torch
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.application.services.model_registry import (
    ModelRegistry,
    generate_signoff_signature,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.model_serving_authorization import (
    authorize_model_serving,
    clear_serving_auth_cache,
    resolve_effective_caller_tenant,
)
from app.config import get_settings
from app.domain.consortium_governance import (
    Consortium,
    ConsortiumMember,
    ConsortiumStatus,
    MemberRole,
)
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.main import app

PROD_SECRET = "production_super_secure_signing_key_32_bytes_long_12345!"
PROD_AUTH_SECRET = "production-identity-authority-secret-high-entropy-64bytes!"

_PROV_CONTEXT: dict[str, Any] = {}


@pytest.fixture
def test_env(monkeypatch):
    """Sets up an isolated registry directory, trust store, and reset caches."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_shared_auth_test_reg_")
    store_path = os.path.join(temp_dir, "shared_identity_store.json")
    trust_file = os.path.join(temp_dir, "shared_trust.json")
    audit_file = os.path.join(temp_dir, "shared_audit.json")
    consortium_file = os.path.join(temp_dir, "shared_consortium_store.json")

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

    test_c_store = DurableConsortiumStore(
        store_path=consortium_file,
        authority_secret=PROD_AUTH_SECRET,
        app_env="development",
    )
    old_c_store = DurableConsortiumStore._instance
    DurableConsortiumStore.set_instance(test_c_store)

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
    kid = "issuer_shared_01"
    trust_store.register_approved_key(key_id=kid, public_key_hex=pub_hex, admin_token=admin_token)

    _PROV_CONTEXT["trust_store"] = trust_store
    _PROV_CONTEXT["priv"] = priv
    _PROV_CONTEXT["kid"] = kid

    test_reg = ModelRegistry(storage_dir=temp_dir)
    monkeypatch.setattr("app.presentation.routers.predict._registry", test_reg)

    clear_serving_auth_cache()
    from app.presentation.routers.predict import clear_all_serving_caches
    clear_all_serving_caches()

    yield temp_dir

    clear_serving_auth_cache()
    clear_all_serving_caches()
    DurableConsortiumStore.set_instance(old_c_store)
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
    owning_bank_id: str | None,
    consortium_id: str | None = None,
) -> dict[str, Any]:
    """Helper: Trains and registers a verified champion model with authentic cryptographic receipt and signoffs."""
    import hashlib
    import io

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
        consortium_id=consortium_id,
    )

    registry._save_ownership(
        simulation_id,
        {
            "simulation_id": simulation_id,
            "owning_bank_id": owning_bank_id,
            "consortium_id": consortium_id,
            "bound_at": "2026-01-01T00:00:00Z",
            "bound_by": "test_seed",
            "is_unbound": False,
        },
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

    auth_svc = AuthenticationService.get_instance()
    actor_bank = owning_bank_id or "bank_b"
    u_ml = auth_svc.register_user(f"ml_{simulation_id}", "Pass123!Secure", actor_bank, roles=["ml_engineer"], clearance_level=2)
    u_cp = auth_svc.register_user(f"cp_{simulation_id}", "Pass123!Secure", actor_bank, roles=["compliance"], clearance_level=3)

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


def _valid_predict_payload() -> dict[str, Any]:
    return {
        "transaction_id": "tx_shared_001",
        "transaction_amount": 125.0,
        "merchant_category": "retail",
        "velocity": 2.0,
        "country_code": "US",
        "device_type": "web",
        "customer_history_score": 0.95,
        "chargeback_count": 0,
        "account_age_days": 180,
        "merchant_risk_score": 0.05,
        "hour_of_day": 14.0,
    }


# ==============================================================================
# TEST A: Bank A has no authorized model. Bank B owns sole active champion.
#         Bank A omits simulation_id on /api/v1/predict. Reject before loading.
# ==============================================================================
def test_a_bank_a_no_champion_bank_b_sole_active_rejects_before_loading(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
):
    _create_champion_for_bank(test_env, "sim_bank_b_exclusive", owning_bank_id="bank_b")

    from app.presentation.routers.predict import _registry
    _registry.storage_dir = test_env

    headers_a = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}
    payload = _valid_predict_payload()
    payload["simulation_id"] = None

    with patch.object(get_settings(), "app_env", "production"):
        with patch("app.presentation.routers.predict._get_cached_serving_model_with_ident") as mock_loader:
            resp = client.post("/api/v1/predict", json=payload, headers=headers_a)
            assert resp.status_code == 404, resp.text
            assert "No authorized active champion model found for institution 'bank_a'" in resp.text
            mock_loader.assert_not_called()


# ==============================================================================
# TEST B: Repeat A against batch, explain, and scoring aliases
# ==============================================================================
def test_b_scoring_aliases_reject_omitted_sim_before_loading(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
):
    _create_champion_for_bank(test_env, "sim_bank_b_only", owning_bank_id="bank_b")
    from app.presentation.routers.predict import _registry
    _registry.storage_dir = test_env

    headers_a = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}

    with patch.object(get_settings(), "app_env", "production"):
        # 1. Batch prediction (/api/v1/predict/batch)
        batch_payload = {
            "simulation_id": None,
            "transactions": [_valid_predict_payload()],
        }
        with patch("app.presentation.routers.predict._get_cached_serving_model_with_ident") as mock_loader:
            resp_batch = client.post("/api/v1/predict/batch", json=batch_payload, headers=headers_a)
            assert resp_batch.status_code == 404, resp_batch.text
            mock_loader.assert_not_called()

        # 2. Explain prediction (/api/v1/predict/explain)
        explain_payload = {
            "simulation_id": None,
            "transaction": _valid_predict_payload(),
        }
        with patch("app.presentation.routers.predict._get_cached_serving_model") as mock_loader_explain:
            resp_explain = client.post("/api/v1/predict/explain", json=explain_payload, headers=headers_a)
            assert resp_explain.status_code == 404, resp_explain.text
            mock_loader_explain.assert_not_called()

        # 3. Predict Score alias (/api/v1/predict/score)
        score_alias_payload = {
            "simulation_id": None,
            "transaction_id": "tx_score_001",
            "account_id": "acc_score_001",
            "merchant_id": "merch_score_001",
            "device_id": "dev_score_001",
            "amount": 100.0,
            "merchant_category": "retail",
            "velocity": 1.0,
            "country_code": "US",
            "country": "US",
            "device_type": "mobile",
            "customer_history_score": 0.90,
            "chargeback_count": 0,
            "account_age_days": 120,
            "merchant_risk_score": 0.05,
            "hour_of_day": 12.0,
        }
        with patch("app.presentation.routers.predict._get_cached_serving_model_with_ident") as mock_loader_score:
            resp_score = client.post("/api/v1/predict/score", json=score_alias_payload, headers=headers_a)
            assert resp_score.status_code == 404, resp_score.text
            mock_loader_score.assert_not_called()


# ==============================================================================
# TEST C: A configured Bank B default champion cannot be executed by Bank A
# ==============================================================================
def test_c_configured_default_champion_cannot_be_executed_by_unauthorized_bank(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    _create_champion_for_bank(test_env, "sim_bank_b_default", owning_bank_id="bank_b")
    from app.presentation.routers.predict import _registry
    _registry.storage_dir = test_env

    settings = get_settings()
    monkeypatch.setattr(settings, "default_simulation_id", "sim_bank_b_default")
    monkeypatch.setenv("CFI_DEFAULT_SIMULATION_ID", "sim_bank_b_default")

    headers_a = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}
    payload = _valid_predict_payload()
    payload["simulation_id"] = None

    with patch.object(get_settings(), "app_env", "production"):
        with patch("app.presentation.routers.predict._get_cached_serving_model_with_ident") as mock_loader:
            resp = client.post("/api/v1/predict", json=payload, headers=headers_a)
            assert resp.status_code == 404, resp.text
            assert "No authorized active champion model found for institution 'bank_a'" in resp.text
            mock_loader.assert_not_called()


# ==============================================================================
# TEST D: Authorization helper never invokes _get_cached_serving_model_with_ident(None)
# ==============================================================================
def test_d_authorization_helper_never_probes_serving_model_loader(
    test_env: str,
):
    reg = ModelRegistry(storage_dir=test_env)
    principal_a = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    with patch.object(get_settings(), "app_env", "production"):
        with patch("app.presentation.routers.predict._get_cached_serving_model_with_ident") as mock_loader:
            with pytest.raises(HTTPException) as exc_info:
                authorize_model_serving(
                    principal=principal_a,
                    caller_tenant="bank_a",
                    simulation_id=None,
                    registry=reg,
                )
            assert exc_info.value.status_code == 404
            mock_loader.assert_not_called()


# ==============================================================================
# TEST E, F, G: Warm cache cannot preserve revoked consortium membership
# ==============================================================================
def test_e_f_g_consortium_revocation_takes_effect_without_mtime_change(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
):
    c_store = DurableConsortiumStore.get_instance()
    consortium = Consortium(
        consortium_id="consortium_shared_01",
        name="Shared Test Consortium",
        status=ConsortiumStatus.ACTIVE,
        members={
            "bank_b": ConsortiumMember(
                bank_id="bank_b",
                role=MemberRole.FULL_MEMBER,
            ),
        },
    )
    def _add_consortium(consortia: dict[str, Any], proposals: dict[str, Any]) -> None:
        consortia[consortium.consortium_id] = consortium

    c_store.atomic_mutate(_add_consortium)

    _create_champion_for_bank(
        storage_dir=test_env,
        simulation_id="sim_consortium_model",
        owning_bank_id=None,
        consortium_id="consortium_shared_01",
    )

    from app.presentation.routers.predict import _registry

    headers_b = {"Authorization": f"Bearer {auth_tokens['bank_b']}"}
    payload = _valid_predict_payload()
    payload["simulation_id"] = None

    principal_b = AuthenticatedPrincipal(
        principal_id="analyst_b",
        tenant_id="bank_b",
        roles=["analyst"],
        is_authenticated=True,
    )

    with patch.object(get_settings(), "app_env", "production"):
        # Step E: Bank B initially belongs to consortium and resolves its authorized champion
        resolved_sim, _ = authorize_model_serving(
            principal=principal_b,
            caller_tenant="bank_b",
            simulation_id=None,
            registry=_registry,
        )
        assert resolved_sim == "sim_consortium_model"

        resp1 = client.post("/api/v1/predict", json=payload, headers=headers_b)
        assert resp1.status_code == 200, resp1.text
        data1 = resp1.json()
        assert 0.0 <= data1["fraud_probability"] <= 1.0

        # Step F: Record registry root directory mtime and remove Bank B from consortium
        reg_root = _registry.registry_root
        initial_mtime = os.path.getmtime(reg_root)

        def _revoke_member_b(consortia: dict[str, Any], proposals: dict[str, Any]) -> None:
            c = consortia.get("consortium_shared_01")
            if c:
                c.members.pop("bank_b", None)
                c.removed_members["bank_b"] = {
                    "revoked_at": datetime.now(UTC).isoformat(),
                    "reason": "revocation_test",
                }

        c_store.atomic_mutate(_revoke_member_b)
        # Verify registry root mtime was NOT touched
        assert os.path.getmtime(reg_root) == initial_mtime

        # Step G: Bank B's next request must immediately be rejected (no warm authorization cache bypass)
        with pytest.raises(HTTPException) as exc_info:
            authorize_model_serving(
                principal=principal_b,
                caller_tenant="bank_b",
                simulation_id=None,
                registry=_registry,
            )
        assert exc_info.value.status_code == 404

        resp2 = client.post("/api/v1/predict", json=payload, headers=headers_b)
        assert resp2.status_code == 404, resp2.text
        assert "No authorized active champion model found for institution 'bank_b'" in resp2.text


# ==============================================================================
# TEST H: Simulation ownership mutation without changing registry mtime
# ==============================================================================
def test_h_ownership_rebinding_invalidates_access_without_mtime_change(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
):
    _create_champion_for_bank(
        storage_dir=test_env,
        simulation_id="sim_rebindable",
        owning_bank_id="bank_b",
    )
    from app.presentation.routers.predict import _registry

    headers_b = {"Authorization": f"Bearer {auth_tokens['bank_b']}"}
    payload = _valid_predict_payload()
    payload["simulation_id"] = None

    principal_b = AuthenticatedPrincipal(
        principal_id="analyst_b",
        tenant_id="bank_b",
        roles=["analyst"],
        is_authenticated=True,
    )

    with patch.object(get_settings(), "app_env", "production"):
        # Directly verify authorize_model_serving resolves Bank B's model
        resolved_sim, _ = authorize_model_serving(
            principal=principal_b,
            caller_tenant="bank_b",
            simulation_id=None,
            registry=_registry,
        )
        assert resolved_sim == "sim_rebindable"

        # Request 1: Bank B resolves its own model
        resp1 = client.post("/api/v1/predict", json=payload, headers=headers_b)
        assert resp1.status_code == 200, resp1.text
        assert 0.0 <= resp1.json()["fraud_probability"] <= 1.0

        # Mutate ownership to bank_c and reset directory mtime
        reg_root = _registry.registry_root
        orig_mtime = os.path.getmtime(reg_root)
        _registry._save_ownership(
            "sim_rebindable",
            {
                "simulation_id": "sim_rebindable",
                "owning_bank_id": "bank_c",
                "consortium_id": None,
                "bound_at": "2026-01-01T00:00:00Z",
                "bound_by": "rebind_test",
                "is_unbound": False,
            },
        )
        os.utime(reg_root, (orig_mtime, orig_mtime))
        assert os.path.getmtime(reg_root) == orig_mtime

        # Direct authorization helper must immediately reject Bank B
        with pytest.raises(HTTPException) as exc_info:
            authorize_model_serving(
                principal=principal_b,
                caller_tenant="bank_b",
                simulation_id=None,
                registry=_registry,
            )
        assert exc_info.value.status_code == 404

        # Next request from Bank B must be rejected
        resp2 = client.post("/api/v1/predict", json=payload, headers=headers_b)
        assert resp2.status_code == 404, resp2.text
        assert "No authorized active champion model found for institution 'bank_b'" in resp2.text


# ==============================================================================
# TEST I: Explicit unauthorized simulation requests remain rejected
# ==============================================================================
def test_i_explicit_unauthorized_simulation_request_rejected(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
):
    _create_champion_for_bank(test_env, "sim_bank_b_private", owning_bank_id="bank_b")
    from app.presentation.routers.predict import _registry
    _registry.storage_dir = test_env

    headers_a = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}
    payload = _valid_predict_payload()
    payload["simulation_id"] = "sim_bank_b_private"

    with patch.object(get_settings(), "app_env", "production"):
        resp = client.post("/api/v1/predict", json=payload, headers=headers_a)
        assert resp.status_code == 403, resp.text
        assert "not authorized to execute private champion model owned by bank 'bank_b'" in resp.text


# ==============================================================================
# TEST J: Legitimate authorized omitted-simulation resolution succeeds
# ==============================================================================
def test_j_legitimate_authorized_omitted_simulation_succeeds(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
):
    _create_champion_for_bank(test_env, "sim_bank_a_genuine", owning_bank_id="bank_a")
    from app.presentation.routers.predict import _registry
    _registry.storage_dir = test_env

    headers_a = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}
    payload = _valid_predict_payload()
    payload["simulation_id"] = None

    principal_a = AuthenticatedPrincipal(
        principal_id="analyst_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    with patch.object(get_settings(), "app_env", "production"):
        resolved_sim, _ = authorize_model_serving(
            principal=principal_a,
            caller_tenant="bank_a",
            simulation_id=None,
            registry=_registry,
        )
        assert resolved_sim == "sim_bank_a_genuine"

        resp = client.post("/api/v1/predict", json=payload, headers=headers_a)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert 0.0 <= data["fraud_probability"] <= 1.0


# ==============================================================================
# TEST K: Production calls with missing/unauthenticated principal cannot obtain authority
# ==============================================================================
def test_k_unauthenticated_production_call_cannot_obtain_model_authority(
    client: TestClient,
    test_env: str,
):
    reg = ModelRegistry(storage_dir=test_env)

    with patch.object(get_settings(), "app_env", "production"):
        # 1. Direct helper call with principal=None in production raises HTTP 401
        with pytest.raises(HTTPException) as exc_info:
            authorize_model_serving(
                principal=None,
                caller_tenant="bank_a",
                simulation_id=None,
                registry=reg,
            )
        assert exc_info.value.status_code == 401

        # 2. Direct resolve_effective_caller_tenant with principal=None in production raises HTTP 401
        with pytest.raises(HTTPException) as exc_info2:
            resolve_effective_caller_tenant(
                principal=None,
                caller_tenant="bank_a",
                requested_bank_id="bank_a",
            )
        assert exc_info2.value.status_code == 401

        # 3. HTTP call with no Authorization header to /api/v1/predict rejects with 401
        payload = _valid_predict_payload()
        resp = client.post("/api/v1/predict", json=payload, headers={"X-Bank-ID": "bank_a"})
        assert resp.status_code == 401, resp.text
