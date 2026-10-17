"""Adversarial security tests for post-admission issuer revocation enforcement (REV-01 through REV-10).

Covers:
- REV-01: Valid candidate, then issuer revocation, then champion promotion -> rejected.
- REV-02: Valid candidate, then issuer revocation, then challenger promotion -> rejected.
- REV-03: Previously active champion signed by revoked issuer -> rejected on subsequent resolution.
- REV-04: Previously active challenger signed by revoked issuer -> rejected on subsequent resolution.
- REV-05: Champion already cached before revocation -> subsequent inference fails closed.
- REV-06: Challenger already cached by _load_challenger_model() before revocation -> subsequent inference cannot use it.
- REV-07: Rollback or reactivation cannot restore a revoked-issuer model.
- REV-08: Genuine valid receipt signed by another still-trusted issuer continues to work.
- REV-09: Corrupted, missing, or inaccessible authoritative trust state does not result in continued acceptance of cached models.
- REV-10: Legitimate model registration, promotion, and inference remain functional while trust is valid.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tempfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import BackgroundTasks
from starlette.requests import Request
from starlette.responses import Response

import app.application.services.training_provenance_service as prov_service
import app.presentation.routers.predict as predict_module
from app.application.schemas.transaction import TransactionPredictRequest
from app.application.services.data_generator import FEATURE_NAMES
from app.application.services.model_registry import (
    ModelEvaluationEngine,
    ModelRegistry,
    generate_signoff_signature,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.domain.model_serving_errors import ModelIntegrityError, ModelNotAvailableError
from app.infrastructure.security.auth_service import AuthenticationService
from app.presentation.routers.realtime_inference import (
    get_scripted_model,
    reset_model_cache,
)


def _setup_test_env(tmp_dir: str):
    """Initialize a durable trust store, verifier, and model registry with clean isolated storage."""
    reset_model_cache()
    predict_module.clear_all_serving_caches()
    storage_dir = os.path.join(tmp_dir, "storage")
    os.makedirs(storage_dir, exist_ok=True)
    trust_file = os.path.join(tmp_dir, "trust_store.json")
    admin_token = "admin-secret-token-revocation-test-12345"
    trust_store = DurableTrustStore(
        trust_file_path=trust_file,
        admin_token=admin_token,
    )
    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    registry = ModelRegistry(storage_dir=storage_dir, verifier=verifier)
    auth_file = os.path.join(tmp_dir, "auth_store.json")
    auth_svc = AuthenticationService(signing_secret="revocation_test_secret_32bytes_long!", store_path=auth_file)
    AuthenticationService._instance = auth_svc
    return storage_dir, trust_store, verifier, registry, admin_token


def _create_issuer(trust_store: DurableTrustStore, admin_token: str, key_id: str):
    """Generate an authentic Ed25519 key pair and register it in the durable trust store."""
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key()
    pub_hex = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    trust_store.register_approved_key(key_id, pub_hex, admin_token=admin_token)
    return priv, key_id


def _build_candidate(
    reg: ModelRegistry,
    priv: ed25519.Ed25519PrivateKey,
    key_id: str,
    sim_id: str,
    version_metrics: dict | None = None,
    owning_bank_id: str = "bank_a",
):
    """Construct authentic PyTorch model weights, hash, genuine Ed25519 receipt, and candidate version."""
    svc = ModelService(get_settings())
    model = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    model_bytes = buf.getvalue()
    sha256 = hashlib.sha256(model_bytes).hexdigest()

    issuer = TrainingReceiptIssuer(private_key=priv, key_id=key_id, require_authenticated_job=False)
    evidence = TrainingExecutionEvidence(
        training_run_id=f"run_{sim_id}_{key_id}",
        job_id=f"job_{sim_id}_{key_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=3,
        executed_epochs=3,
        sample_count=100,
        observed_metrics=version_metrics or {"loss": 0.05, "auc": 0.92},
        artifact_sha256=sha256,
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        feature_names=list(FEATURE_NAMES),
        owning_bank_id=owning_bank_id,
    )
    receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes).to_dict()

    entry = reg.save_version(
        simulation_id=sim_id,
        state_dict=model.state_dict(),
        metrics=version_metrics or {"loss": 0.05, "auc": 0.92},
        artifact_bytes=model_bytes,
        training_receipt=receipt,
        is_promoted=False,
        owning_bank_id=owning_bank_id,
    )
    return entry, model, model_bytes, sha256, receipt


def _attach_champion_signoffs(reg: ModelRegistry, sim_id: str, version: int, sha256: str):
    """Attach ML Engineer and Compliance signoffs to manifest without auto-promoting."""
    auth_svc = AuthenticationService.get_instance()
    u1 = auth_svc.register_user(
        f"eng_{sim_id}_{version}", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2
    )
    u2 = auth_svc.register_user(
        f"comp_{sim_id}_{version}", "Pass123!Secure", "bank_a", roles=["compliance"], clearance_level=3
    )

    sig_ml = generate_signoff_signature(sim_id, version, sha256, "ml_engineer", u1.username)
    sig_comp = generate_signoff_signature(sim_id, version, sha256, "compliance", u2.username)

    manifest = reg._load_manifest(sim_id)
    entry = next(e for e in manifest if e["version"] == version)
    entry["sign_offs"] = [
        {
            "role": "ml_engineer",
            "user": u1.username,
            "signature": sig_ml,
            "signature_scheme": "APPLICATION_HMAC_SHA256",
            "is_signature_valid": True,
            "is_role_verified": True,
            "is_identity_verified": True,
            "is_independent_human_authorization": True,
            "payload_bound": f"{sim_id}:{version}:{sha256}:ml_engineer:{u1.username}",
            "timestamp": datetime.now(UTC).isoformat(),
            "fairness_score": 1.0,
            "bias_metric": 0.0,
            "drift_divergence": 0.0,
            "is_revoked": False,
        },
        {
            "role": "compliance",
            "user": u2.username,
            "signature": sig_comp,
            "signature_scheme": "APPLICATION_HMAC_SHA256",
            "is_signature_valid": True,
            "is_role_verified": True,
            "is_identity_verified": True,
            "is_independent_human_authorization": True,
            "payload_bound": f"{sim_id}:{version}:{sha256}:compliance:{u2.username}",
            "timestamp": datetime.now(UTC).isoformat(),
            "fairness_score": 1.0,
            "bias_metric": 0.0,
            "drift_divergence": 0.0,
            "is_revoked": False,
        },
    ]
    reg._save_manifest(sim_id, manifest)
    return auth_svc.create_token_bundle(u1).access_token


# ── REV-01: Valid candidate, then issuer revocation, then champion promotion ──

def test_rev01_candidate_champion_promotion_rejected_after_issuer_revocation():
    """REV-01: Valid candidate, then issuer revocation, then champion promotion -> rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, trust_store, verifier, reg, admin_token = _setup_test_env(tmp_dir)
        priv1, kid1 = _create_issuer(trust_store, admin_token, "issuer_rev01")
        sim_id = "sim_rev01"
        entry, _, _, sha256, _ = _build_candidate(reg, priv1, kid1, sim_id)
        v = entry["version"]
        _attach_champion_signoffs(reg, sim_id, v, sha256)

        # Durably revoke issuer key in trust store
        trust_store.revoke_key(kid1, reason="key_compromised", admin_token=admin_token)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            pytest.raises(ModelIntegrityError, match="revoked"),
        ):
            reg.promote_version(sim_id, v, target_status="champion")

        # Verify candidate is NOT promoted
        manifest = reg._load_manifest(sim_id)
        assert manifest[0]["status"] == "inactive"
        assert not manifest[0].get("is_active", False)


# ── REV-02: Valid candidate, then issuer revocation, then challenger promotion

def test_rev02_candidate_challenger_promotion_rejected_after_issuer_revocation():
    """REV-02: Valid candidate, then issuer revocation, then challenger promotion -> rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, trust_store, verifier, reg, admin_token = _setup_test_env(tmp_dir)
        priv1, kid1 = _create_issuer(trust_store, admin_token, "issuer_rev02")
        sim_id = "sim_rev02"
        entry, _, _, _, _ = _build_candidate(reg, priv1, kid1, sim_id)
        v = entry["version"]

        # Durably revoke issuer key in trust store
        trust_store.revoke_key(kid1, reason="key_compromised", admin_token=admin_token)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            pytest.raises(ModelIntegrityError, match="revoked"),
        ):
            reg.promote_version(sim_id, v, target_status="challenger")

        # Verify candidate is NOT promoted to challenger
        manifest = reg._load_manifest(sim_id)
        assert manifest[0]["status"] == "inactive"
        assert not manifest[0].get("is_active", False)


# ── REV-03: Active champion signed by revoked issuer rejected on resolution ──

def test_rev03_previously_active_champion_rejected_on_subsequent_resolution():
    """REV-03: Previously active champion signed by revoked issuer -> rejected on subsequent resolution."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, trust_store, verifier, reg, admin_token = _setup_test_env(tmp_dir)
        priv1, kid1 = _create_issuer(trust_store, admin_token, "issuer_rev03")
        sim_id = "sim_rev03"
        entry, _, _, sha256, _ = _build_candidate(reg, priv1, kid1, sim_id)
        v = entry["version"]
        token_ml = _attach_champion_signoffs(reg, sim_id, v, sha256)

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            reg.promote_version(sim_id, v, target_status="champion", auth_token=token_ml)
            # Verify resolution succeeds before revocation
            resolved_before = reg.resolve_and_verify_champion(sim_id)
            assert resolved_before is not None

            # Revoke issuer key
            trust_store.revoke_key(kid1, reason="key_compromised", admin_token=admin_token)

            # Subsequent resolution must fail closed
            with pytest.raises(ModelIntegrityError, match="revoked"):
                reg.resolve_and_verify_champion(sim_id)

            # get_active_champion_identity must return None
            ident = reg.get_active_champion_identity(sim_id)
            assert ident is None


# ── REV-04: Active challenger signed by revoked issuer rejected on resolution

def test_rev04_previously_active_challenger_rejected_on_subsequent_resolution():
    """REV-04: Previously active challenger signed by revoked issuer -> rejected on subsequent resolution."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, trust_store, verifier, reg, admin_token = _setup_test_env(tmp_dir)
        priv1, kid1 = _create_issuer(trust_store, admin_token, "issuer_rev04")
        sim_id = "sim_rev04"
        entry, _, _, _, _ = _build_candidate(reg, priv1, kid1, sim_id)
        v = entry["version"]
        auth_svc = AuthenticationService.get_instance()
        u_ml = auth_svc.register_user(f"eng_{sim_id}_{v}", "Pass123!Secure", "bank_a", roles=["ml_engineer"], clearance_level=2)
        token_ml = auth_svc.create_token_bundle(u_ml).access_token

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            reg.promote_version(sim_id, v, target_status="challenger", auth_token=token_ml)
            resolved_before = reg.resolve_and_verify_challenger(sim_id)
            assert resolved_before is not None

            # Revoke issuer key
            trust_store.revoke_key(kid1, reason="key_compromised", admin_token=admin_token)

            # Subsequent resolution must fail closed
            with pytest.raises(ModelIntegrityError, match="revoked"):
                reg.resolve_and_verify_challenger(sim_id)


# ── REV-05: Cached champion in ModelService fails closed after revocation ────

def test_rev05_champion_cached_before_revocation_fails_closed_on_subsequent_inference():
    """REV-05: Champion already cached before revocation -> subsequent inference fails closed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, trust_store, verifier, reg, admin_token = _setup_test_env(tmp_dir)
        priv1, kid1 = _create_issuer(trust_store, admin_token, "issuer_rev05")
        sim_id = "sim_rev05"
        entry, _, _, sha256, _ = _build_candidate(reg, priv1, kid1, sim_id)
        v = entry["version"]
        token_ml = _attach_champion_signoffs(reg, sim_id, v, sha256)

        svc = ModelService(get_settings())
        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            patch("app.application.services.model_registry.ModelRegistry", return_value=reg),
            patch("app.presentation.routers.realtime_inference._get_redis_client", return_value=None),
        ):
            reg.promote_version(sim_id, v, target_status="champion", auth_token=token_ml)

            # Warm caches before revocation
            champ_model = svc.get_champion(simulation_id=sim_id, dp_compatible=True, registry=reg)
            assert champ_model is not None

            scripted, _ = get_scripted_model(simulation_id=sim_id)
            assert scripted is not None

            # Revoke issuer key in durable trust store
            trust_store.revoke_key(kid1, reason="compromise", admin_token=admin_token)

            # Subsequent ModelService.get_champion must fail closed
            with pytest.raises(ModelIntegrityError, match="revoked"):
                svc.get_champion(simulation_id=sim_id, dp_compatible=True, registry=reg)

            # Subsequent realtime_inference cached lookup must fail closed
            with pytest.raises(ModelIntegrityError, match="revoked"):
                get_scripted_model(simulation_id=sim_id)


# ── REV-06: Cached challenger in predict._load_challenger_model evicted ───────

def test_rev06_challenger_cached_before_revocation_cannot_be_served():
    """REV-06: Challenger already cached by _load_challenger_model() before revocation -> subsequent inference cannot use it."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, trust_store, verifier, reg, admin_token = _setup_test_env(tmp_dir)
        priv1, kid1 = _create_issuer(trust_store, admin_token, "issuer_rev06_c")
        priv2, kid2 = _create_issuer(trust_store, admin_token, "issuer_rev06_chall")
        sim_id = "sim_rev06"

        # Champion signed by kid1
        entry1, _, _, sha256_1, _ = _build_candidate(reg, priv1, kid1, sim_id)
        v1 = entry1["version"]
        token_ml = _attach_champion_signoffs(reg, sim_id, v1, sha256_1)

        # Challenger signed by kid2
        entry2, _, _, _, _ = _build_candidate(reg, priv2, kid2, sim_id)
        v2 = entry2["version"]

        svc = ModelService(get_settings())
        eval_engine = ModelEvaluationEngine(reg)
        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            patch.object(predict_module, "_registry", reg),
            patch.object(predict_module, "_model_service", svc),
            patch.object(predict_module, "_eval_engine", eval_engine),
            patch.object(eval_engine, "get_traffic_share", return_value=1.0),
        ):
            reg.promote_version(sim_id, v1, target_status="champion", auth_token=token_ml)
            reg.promote_version(sim_id, v2, target_status="challenger", auth_token=token_ml)

            # Warm cache for challenger model
            cached_chall = predict_module._load_challenger_model(sim_id, v2, dp_compatible=True)
            assert cached_chall is not None

            # Revoke challenger's issuer key kid2
            trust_store.revoke_key(kid2, reason="challenger_key_compromised", admin_token=admin_token)

            # Direct cached call must fail closed
            with pytest.raises(ModelIntegrityError, match="revoked"):
                predict_module._load_challenger_model(sim_id, v2, dp_compatible=True)

            # Live prediction request must not use revoked challenger
            req = Request(
                scope={
                    "type": "http",
                    "method": "POST",
                    "path": "/api/v1/predict",
                    "headers": [(b"content-type", b"application/json")],
                    "client": ("127.0.0.1", 12345),
                    "server": ("127.0.0.1", 8000),
                }
            )
            resp = Response()
            mock_session = MagicMock()
            mock_session.execute = AsyncMock()
            mock_session.commit = AsyncMock()
            bg = BackgroundTasks()

            txn_payload = TransactionPredictRequest(
                simulation_id=sim_id,
                transaction_amount=100.0,
                amount=100.0,
                merchant_category="retail",
                country_code="US",
                device_type="web_browser",
                velocity=1.0,
                hour_of_day=12,
                day_of_week=2,
                is_international=0,
                merchant_risk_score=0.03,
                customer_history_score=0.98,
                chargeback_count=0,
                account_age_days=500,
                bank_id="bank_a",
            )

            with patch.object(eval_engine, "log_prediction") as mock_log:
                res = asyncio.run(
                    predict_module.predict_transaction(
                        request=req,
                        response=resp,
                        payload=txn_payload,
                        session=mock_session,
                        background_tasks=bg,
                        metered_tenant="test_tenant",
                    )
                )
                assert res is not None
                mock_log.assert_called_once()
                call_kwargs = mock_log.call_args.kwargs
                assert call_kwargs["routed_to"] == "champion"
                assert call_kwargs["challenger_version"] is None
                assert call_kwargs["challenger_prob"] is None


# ── REV-07: Rollback cannot restore revoked-issuer model ─────────────────────

def test_rev07_rollback_cannot_restore_revoked_issuer_model():
    """REV-07: Rollback or reactivation cannot restore a revoked-issuer model."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, trust_store, verifier, reg, admin_token = _setup_test_env(tmp_dir)
        priv1, kid1 = _create_issuer(trust_store, admin_token, "issuer_rev07_v1")
        priv2, kid2 = _create_issuer(trust_store, admin_token, "issuer_rev07_v2")
        sim_id = "sim_rev07"

        # v1 signed by kid1
        entry1, _, _, sha256_1, _ = _build_candidate(reg, priv1, kid1, sim_id)
        v1 = entry1["version"]
        tok1 = _attach_champion_signoffs(reg, sim_id, v1, sha256_1)

        # v2 signed by kid2
        entry2, _, _, sha256_2, _ = _build_candidate(reg, priv2, kid2, sim_id)
        v2 = entry2["version"]
        tok2 = _attach_champion_signoffs(reg, sim_id, v2, sha256_2)

        auth_svc = AuthenticationService.get_instance()
        u_rb = auth_svc.register_user(f"rb_{sim_id}", "Pass123!Secure", "bank_a", roles=["compliance"], clearance_level=3)
        tok_rb = auth_svc.create_token_bundle(u_rb).access_token

        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            # Promote v1, then promote v2
            reg.promote_version(sim_id, v1, target_status="champion", auth_token=tok1)
            reg.promote_version(sim_id, v2, target_status="champion", auth_token=tok2)

            # Revoke v1's issuer key kid1
            trust_store.revoke_key(kid1, reason="v1_compromised", admin_token=admin_token)

            # Rollback to v1 must be rejected
            with pytest.raises(ModelIntegrityError, match="revoked"):
                reg.rollback(sim_id, v1, auth_token=tok_rb)

            # Champion remains v2
            active = reg.get_active_version(sim_id)
            assert active is not None
            assert active["version"] == v2


# ── REV-08: Genuine valid receipt signed by still-trusted issuer works ───────

def test_rev08_still_trusted_issuer_continues_working():
    """REV-08: Genuine valid receipt signed by another still-trusted issuer continues to work."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, trust_store, verifier, reg, admin_token = _setup_test_env(tmp_dir)
        priv1, kid1 = _create_issuer(trust_store, admin_token, "issuer_rev08_bad")
        priv2, kid2 = _create_issuer(trust_store, admin_token, "issuer_rev08_good")
        sim_id = "sim_rev08"

        # v1 signed by kid1 (bad)
        _build_candidate(reg, priv1, kid1, sim_id)
        # v2 signed by kid2 (good)
        entry2, _, _, sha256_2, _ = _build_candidate(reg, priv2, kid2, sim_id)
        v2 = entry2["version"]
        token_ml = _attach_champion_signoffs(reg, sim_id, v2, sha256_2)

        # Revoke only kid1
        trust_store.revoke_key(kid1, reason="compromised", admin_token=admin_token)

        settings = get_settings()
        svc = ModelService(settings)
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            # Promote v2 signed by kid2 -> succeeds
            promoted = reg.promote_version(sim_id, v2, target_status="champion", auth_token=token_ml)
            assert promoted["status"] == "champion"

            # Resolve v2 -> succeeds
            resolved = reg.resolve_and_verify_champion(sim_id)
            assert resolved is not None
            assert resolved[2]["version"] == v2

            # ModelService.get_champion -> succeeds
            champ = svc.get_champion(simulation_id=sim_id, dp_compatible=True, registry=reg)
            assert champ is not None


# ── REV-09: Corrupted or inaccessible trust state fails closed ───────────────

def test_rev09_corrupted_or_inaccessible_trust_state_fails_closed():
    """REV-09: Corrupted, missing, or inaccessible authoritative trust state does not result in continued acceptance of cached models."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, trust_store, verifier, reg, admin_token = _setup_test_env(tmp_dir)
        priv1, kid1 = _create_issuer(trust_store, admin_token, "issuer_rev09")
        sim_id = "sim_rev09"
        entry, _, _, sha256, _ = _build_candidate(reg, priv1, kid1, sim_id)
        v = entry["version"]
        token_ml = _attach_champion_signoffs(reg, sim_id, v, sha256)

        svc = ModelService(get_settings())
        settings = get_settings()
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            patch("app.application.services.model_registry.ModelRegistry", return_value=reg),
            patch("app.presentation.routers.realtime_inference._get_redis_client", return_value=None),
        ):
            reg.promote_version(sim_id, v, target_status="champion", auth_token=token_ml)

            # Cache the model
            champ_model = svc.get_champion(simulation_id=sim_id, dp_compatible=True, registry=reg)
            assert champ_model is not None

            scripted, _ = get_scripted_model(simulation_id=sim_id)
            assert scripted is not None

            # Now corrupt the trust store file on disk completely
            trust_file_path = trust_store._trust_file_path
            with open(trust_file_path, "w", encoding="utf-8") as f:
                f.write("{corrupted_json_syntax: true!!}")

            # Force trust store cache invalidation by resetting in-memory approved keys
            trust_store._last_mtime = 0.0
            trust_store._approved_keys.clear()
            trust_store._revoked_keys.clear()

            # Resolution must fail closed (not serve blindly)
            with pytest.raises((ModelIntegrityError, ModelNotAvailableError)):
                reg.resolve_and_verify_champion(sim_id)

            # Cached model lookup in realtime_inference must fail closed
            with pytest.raises((ModelIntegrityError, ModelNotAvailableError)):
                get_scripted_model(simulation_id=sim_id)


# ── REV-10: Legitimate model registration, promotion, and inference ──────────

def test_rev10_legitimate_lifecycle_functional_while_trust_valid():
    """REV-10: Legitimate model registration, promotion, and inference remain functional while trust is valid."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        _, trust_store, verifier, reg, admin_token = _setup_test_env(tmp_dir)
        priv1, kid1 = _create_issuer(trust_store, admin_token, "issuer_rev10")
        sim_id = "sim_rev10"

        # 1. Registration
        entry, _, _, sha256, _ = _build_candidate(reg, priv1, kid1, sim_id)
        v = entry["version"]
        assert entry["provenance_type"] == "TRAINING_RECORDED"

        # 2. Sign-offs
        token_ml = _attach_champion_signoffs(reg, sim_id, v, sha256)

        settings = get_settings()
        svc = ModelService(settings)
        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            patch("app.application.services.model_registry.ModelRegistry", return_value=reg),
            patch("app.presentation.routers.realtime_inference._get_redis_client", return_value=None),
        ):
            # 3. Promotion
            promoted = reg.promote_version(sim_id, v, target_status="champion", auth_token=token_ml)
            assert promoted["status"] == "champion"
            assert promoted["is_active"] is True

            # 4. Resolution
            resolved = reg.resolve_and_verify_champion(sim_id)
            assert resolved is not None
            _, _, meta = resolved
            assert meta["version"] == v

            # 5. Inference via ModelService
            champ = svc.get_champion(simulation_id=sim_id, dp_compatible=True, registry=reg)
            assert champ is not None
            dummy_input = torch.zeros(1, NUM_FEATURES)
            with torch.no_grad():
                pred = champ(dummy_input)
            assert torch.isfinite(pred).all()

            # 6. Inference via realtime_inference
            model_script, _ = get_scripted_model(simulation_id=sim_id)
            assert model_script is not None
