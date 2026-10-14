"""Adversarial security tests for challenger model production admission.

Verifies:
1. Unverified model promoted to challenger -> rejected with ModelIntegrityError.
2. Structural fixture or fabricated eligibility metadata -> rejected.
3. Missing or invalid/untrusted cryptographic receipt -> rejected.
4. Tampered artifact digest mismatch on disk -> rejected.
5. Authentic eligible candidate -> challenger promotion succeeds under governance policy
   (requires genuine training provenance, receipt, hash, schema compatibility, but NOT champion dual sign-offs).
6. Production prediction cannot select an unverified challenger (fails safe to champion).
7. Existing champion and legitimate challenger inference remain correct.
8. Rejected challenger promotion leaves existing champion and manifest unmodified.
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
    compute_file_sha256,
    create_trusted_internal_context,
    generate_signoff_signature,
)
from app.application.services.model_service import NUM_FEATURES, ModelService
from app.application.services.training_provenance_service import (
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.domain.model_serving_errors import (
    ModelIntegrityError,
)


def _create_mock_checkpoint(input_dim: int = NUM_FEATURES) -> tuple[dict[str, torch.Tensor], bytes, str]:
    """Create reproducible PyTorch model state_dict, serialized bytes, and SHA256 digest."""
    svc = ModelService(get_settings())
    model = svc.create_model(input_dim=input_dim, dp_compatible=True)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    raw_bytes = buf.getvalue()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    return model.state_dict(), raw_bytes, sha256


def _create_genuine_signed_receipt(
    priv_key: ed25519.Ed25519PrivateKey,
    key_id: str,
    sim_id: str,
    model_bytes: bytes,
    sha256: str,
) -> dict:
    """Generate genuine TrainingExecutionReceipt with real Ed25519 signature."""
    issuer = TrainingReceiptIssuer(
        private_key=priv_key,
        key_id=key_id,
        require_authenticated_job=False,
    )
    evidence = TrainingExecutionEvidence(
        training_run_id=f"run_{sim_id}",
        job_id=f"job_{sim_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=3,
        executed_epochs=3,
        sample_count=600,
        observed_metrics={"loss": 0.04, "auc": 0.88},
        artifact_sha256=sha256,
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        feature_names=list(FEATURE_NAMES),
    )
    receipt = issuer.issue_receipt(evidence=evidence, model_bytes=model_bytes)
    return receipt.to_dict()


# ── TEST 1: Unverified model promoted to challenger rejected ─────────────────

def test_unverified_model_promoted_to_challenger_rejected():
    """Unverified model cannot be promoted to challenger in production."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = get_settings()
        reg = ModelRegistry(storage_dir=tmp_dir)
        state_dict, raw_bytes, _ = _create_mock_checkpoint()

        v1 = reg.save_version(
            "sim_challenger_test",
            state_dict,
            metrics={"auc": 0.70},
            is_promoted=False,
            provenance_type="UNVERIFIED",
            artifact_bytes=raw_bytes,
        )
        assert v1["provenance_type"] == "UNVERIFIED"

        with (
            patch.object(settings, "app_env", "production"),
            pytest.raises(
                ModelIntegrityError,
                match="Production admission rejected: model provenance 'UNVERIFIED' is ineligible",
            ),
        ):
            reg.promote_version("sim_challenger_test", version=v1["version"], target_status="challenger")

        # Manifest must remain unpromoted
        manifest = reg._load_manifest("sim_challenger_test")
        entry = manifest[0]
        assert entry["status"] != "challenger"
        assert entry["is_active"] is False


# ── TEST 2: Structural fixture promoted to challenger rejected ───────────────

def test_structural_fixture_promoted_to_challenger_rejected():
    """Structural fixture cannot be promoted to challenger in production."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = get_settings()
        reg = ModelRegistry(storage_dir=tmp_dir)
        state_dict, raw_bytes, _ = _create_mock_checkpoint()

        v1 = reg.save_version(
            "sim_fixture_test",
            state_dict,
            metrics={"auc": 0.60},
            is_promoted=False,
            provenance_type="STRUCTURAL_FIXTURE",
            artifact_bytes=raw_bytes,
        )

        with (
            patch.object(settings, "app_env", "production"),
            pytest.raises(
                ModelIntegrityError,
                match="Structural fixture cannot be promoted",
            ),
        ):
            reg.promote_version("sim_fixture_test", version=v1["version"], target_status="challenger")


# ── TEST 3: Fabricated eligibility metadata rejected ─────────────────────────

def test_fabricated_eligibility_metadata_rejected():
    """Fabricated metadata claiming TRAINING_RECORDED without valid receipt fails closed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = get_settings()
        priv = ed25519.Ed25519PrivateKey.generate()
        verifier = TrainingReceiptVerifier(trusted_keys={"trusted_authority_key": priv.public_key()})
        reg = ModelRegistry(storage_dir=tmp_dir, verifier=verifier)
        state_dict, _, _ = _create_mock_checkpoint()

        sim_dir = reg._get_sim_dir("sim_fake_meta")
        filepath = os.path.join(sim_dir, "model_v1.pt")
        torch.save(state_dict, filepath)
        file_sha = compute_file_sha256(filepath)

        # Injected manifest entry claiming verified provenance with fabricated receipt from untrusted key
        injected = {
            "version": 1,
            "filename": "model_v1.pt",
            "metrics": {"auc": 0.90},
            "is_active": False,
            "status": "inactive",
            "sha256": file_sha,
            "provenance_type": "TRAINING_RECORDED",
            "is_production_eligible": True,
            "training_receipt": {
                "execution_id": "fake_exec",
                "key_id": "untrusted_rogue_key",
                "signature": "bad_signature_00000000000000000000000000000000",
                "schema_version": "1.0",
                "signature_scheme": "Ed25519",
                "artifact_sha256": file_sha,
                "created_at": datetime.now(UTC).isoformat(),
            },
        }
        reg._save_manifest("sim_fake_meta", [injected])

        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            pytest.raises(
                ModelIntegrityError,
                match="cryptographic training receipt verification failed",
            ),
        ):
            reg.promote_version("sim_fake_meta", version=1, target_status="challenger")


# ── TEST 4: Missing receipt promoted to challenger rejected ──────────────────

def test_missing_receipt_promoted_to_challenger_rejected():
    """Model lacking training receipt cannot be promoted to challenger in production."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = get_settings()
        reg = ModelRegistry(storage_dir=tmp_dir)
        state_dict, _, _ = _create_mock_checkpoint()

        sim_dir = reg._get_sim_dir("sim_no_rcpt")
        filepath = os.path.join(sim_dir, "model_v1.pt")
        torch.save(state_dict, filepath)
        file_sha = compute_file_sha256(filepath)

        # Marked eligible but missing training_receipt
        injected = {
            "version": 1,
            "filename": "model_v1.pt",
            "metrics": {"auc": 0.85},
            "is_active": False,
            "status": "inactive",
            "sha256": file_sha,
            "provenance_type": "TRAINING_RECORDED",
            "is_production_eligible": True,
            "training_receipt": None,
        }
        reg._save_manifest("sim_no_rcpt", [injected])

        with (
            patch.object(settings, "app_env", "production"),
            pytest.raises(
                ModelIntegrityError,
                match="model lacks verified cryptographic training execution receipt",
            ),
        ):
            reg.promote_version("sim_no_rcpt", version=1, target_status="challenger")


# ── TEST 5: Tampered artifact on disk rejected ───────────────────────────────

def test_tampered_artifact_on_disk_rejected():
    """Artifact modified on disk after registration is rejected due to digest mismatch."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = get_settings()
        priv = ed25519.Ed25519PrivateKey.generate()
        verifier = TrainingReceiptVerifier(trusted_keys={"auth_challenger_01": priv.public_key()})

        state_dict, raw_bytes, sha256 = _create_mock_checkpoint()
        rcpt = _create_genuine_signed_receipt(priv, "auth_challenger_01", "sim_tamper", raw_bytes, sha256)

        reg = ModelRegistry(storage_dir=tmp_dir, verifier=verifier)

        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
        ):
            v1 = reg.save_version(
                "sim_tamper",
                state_dict,
                metrics={"loss": 0.04},
                is_promoted=False,
                training_receipt=rcpt,
                artifact_bytes=raw_bytes,
            )
            assert v1["provenance_type"] == "TRAINING_RECORDED"

            # Adversary tampers with the file on disk
            sim_dir = reg._get_sim_dir("sim_tamper")
            filepath = os.path.join(sim_dir, v1["filename"])
            with open(filepath, "wb") as f:
                f.write(b"CORRUPTED_OR_BACKDOORED_WEIGHTS_DATA")

            with pytest.raises(
                ModelIntegrityError,
                match="artifact SHA-256 digest mismatch",
            ):
                reg.promote_version("sim_tamper", version=v1["version"], target_status="challenger")


# ── TEST 6: Authentic eligible candidate promotion to challenger succeeds ────

def test_authentic_eligible_candidate_promotion_succeeds():
    """Authentic candidate with genuine receipt succeeds without requiring dual human signoffs."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = get_settings()
        priv = ed25519.Ed25519PrivateKey.generate()
        verifier = TrainingReceiptVerifier(trusted_keys={"auth_challenger_02": priv.public_key()})

        state_dict, raw_bytes, sha256 = _create_mock_checkpoint()
        rcpt = _create_genuine_signed_receipt(priv, "auth_challenger_02", "sim_auth_chall", raw_bytes, sha256)

        reg = ModelRegistry(storage_dir=tmp_dir, verifier=verifier)

        from app.infrastructure.security.durable_nonce_store import reset_default_nonce_store

        test_sec = "cfi_test_internal_authority_secret_key_32b!"
        nonce_store_path = os.path.join(tmp_dir, "nonces.json")
        reset_default_nonce_store()
        try:
            with (
                patch.object(settings, "app_env", "production"),
                patch.object(settings, "internal_context_signing_secret", test_sec),
                patch.object(settings, "internal_nonce_store_path", nonce_store_path),
                patch.object(settings, "oidc_jwt_signing_secret", test_sec),
                patch.object(settings, "payload_signing_secret", test_sec),
                patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            ):
                v1 = reg.save_version(
                    "sim_auth_chall",
                    state_dict,
                    metrics={"loss": 0.04, "auc": 0.88},
                    is_promoted=False,
                    training_receipt=rcpt,
                    artifact_bytes=raw_bytes,
                )
            assert v1["provenance_type"] == "TRAINING_RECORDED"
            assert v1["is_production_eligible"] is True

            # Promote to challenger under production environment
            ctx_internal = create_trusted_internal_context(
                service_name="model_evaluation_engine",
                action="promote_version",
                resource_type="model",
                resource_id="sim_auth_chall",
                model_version=v1["version"],
            )
            promoted = reg.promote_version(
                "sim_auth_chall",
                version=v1["version"],
                target_status="challenger",
                trusted_context=ctx_internal,
            )
            assert promoted["status"] == "challenger"
            assert promoted["is_active"] is False

            # Verify manifest
            manifest = reg._load_manifest("sim_auth_chall")
            entry = manifest[0]
            assert entry["status"] == "challenger"
            assert entry["is_active"] is False

            # Verify resolve_and_verify_challenger
            resolved = reg.resolve_and_verify_challenger("sim_auth_chall")
            assert resolved is not None
            fpath, bdata, meta = resolved
            assert meta["version"] == 1
            assert meta["status"] == "challenger"
            assert meta["sha256"] == sha256
        finally:
            reset_default_nonce_store()


# ── TEST 7: Production prediction cannot select unverified challenger ────────

def test_production_prediction_cannot_select_unverified_challenger():
    """Predict router fails safe when challenger is unverified, routing 100% traffic to champion."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = get_settings()
        reg = ModelRegistry(storage_dir=tmp_dir)
        svc = ModelService(settings)
        m = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)

        sim_dir = reg._get_sim_dir("sim_pred_test")

        # 1. Champion artifact
        champ_path = os.path.join(sim_dir, "model_v1.pt")
        torch.save(m.state_dict(), champ_path)
        champ_sha = compute_file_sha256(champ_path)

        # 2. Challenger artifact
        chall_path = os.path.join(sim_dir, "model_v2.pt")
        torch.save(m.state_dict(), chall_path)
        chall_sha = compute_file_sha256(chall_path)

        # Champion sign-offs for production
        sig_ml = generate_signoff_signature("sim_pred_test", 1, champ_sha, "ml_engineer", "auditor_ml")
        sig_cp = generate_signoff_signature("sim_pred_test", 1, champ_sha, "compliance", "auditor_cp")
        champ_entry = {
            "version": 1,
            "filename": "model_v1.pt",
            "is_active": True,
            "status": "champion",
            "sha256": champ_sha,
            "provenance_type": "TRAINING_RECORDED",
            "is_production_eligible": True,
            "training_receipt": {"valid": "dummy"},
            "sign_offs": [
                {"role": "ml_engineer", "user": "auditor_ml", "signature": sig_ml, "is_signature_valid": True, "is_role_verified": True, "is_identity_verified": True},
                {"role": "compliance", "user": "auditor_cp", "signature": sig_cp, "is_signature_valid": True, "is_role_verified": True, "is_identity_verified": True},
            ],
        }

        # Unverified challenger injected
        chall_entry = {
            "version": 2,
            "filename": "model_v2.pt",
            "is_active": False,
            "status": "challenger",
            "sha256": chall_sha,
            "provenance_type": "UNVERIFIED",
            "is_production_eligible": False,
            "training_receipt": None,
        }
        reg._save_manifest("sim_pred_test", [champ_entry, chall_entry])

        eval_engine = ModelEvaluationEngine(reg)

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

        with (
            patch.object(settings, "app_env", "production"),
            patch.object(predict_module, "_registry", reg),
            patch.object(predict_module, "_eval_engine", eval_engine),
            patch.object(eval_engine, "get_traffic_share", return_value=1.0),  # 100% traffic intended for challenger
        ):
            predict_module._load_challenger_model.cache_clear()

            txn_payload = TransactionPredictRequest(
                simulation_id="sim_pred_test",
                transaction_amount=250.0,
                amount=250.0,
                merchant_category="retail",
                country_code="US",
                device_type="web_browser",
                velocity=1.0,
                hour_of_day=14,
                day_of_week=3,
                is_international=0,
                merchant_risk_score=0.03,
                customer_history_score=0.98,
                chargeback_count=0,
                account_age_days=500,
                bank_id="bank_a",
            )

            with patch.object(eval_engine, "log_prediction", wraps=eval_engine.log_prediction) as mock_log:
                # Invoke prediction route handler
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
                # Must route to champion because challenger is unverified!
                mock_log.assert_called_once()
                call_kwargs = mock_log.call_args.kwargs
                assert call_kwargs["routed_to"] == "champion"
                assert call_kwargs["challenger_version"] is None
                assert call_kwargs["challenger_prob"] is None


# ── TEST 8: Existing champion and legitimate challenger inference correct ────

def test_existing_champion_and_legitimate_challenger_inference_correct():
    """Verified challenger can be loaded and evaluated alongside champion."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = get_settings()
        priv = ed25519.Ed25519PrivateKey.generate()
        verifier = TrainingReceiptVerifier(trusted_keys={"auth_chall_08": priv.public_key()})

        state_dict, raw_bytes, sha256 = _create_mock_checkpoint()
        rcpt = _create_genuine_signed_receipt(priv, "auth_chall_08", "sim_both_ok", raw_bytes, sha256)

        reg = ModelRegistry(storage_dir=tmp_dir, verifier=verifier)

        from app.infrastructure.security.auth_service import AuthenticationService
        auth_svc = AuthenticationService(
            signing_secret="production_safe_test_secret_32_bytes_len",
            in_memory=True,
            authority_secret="production_safe_test_auth_secret_32_bytes_len",
            app_env="development",
        )
        auth_svc.register_user("eng_ml", "Pass123!", "bank_a", ["ml_engineer"], clearance_level=2)
        auth_svc.register_user("eng_cp", "Pass123!", "bank_a", ["compliance"], clearance_level=3)

        with (
            patch.object(settings, "app_env", "production"),
            patch.object(prov_service, "_DEFAULT_VERIFIER", verifier),
            patch.object(AuthenticationService, "get_instance", return_value=auth_svc),
        ):
            # Champion v1
            reg.save_version("sim_both_ok", state_dict, metrics={"auc": 0.85}, is_promoted=False, training_receipt=rcpt, artifact_bytes=raw_bytes)
            sig_ml = generate_signoff_signature("sim_both_ok", 1, sha256, "ml_engineer", "eng_ml")
            sig_cp = generate_signoff_signature("sim_both_ok", 1, sha256, "compliance", "eng_cp")
            manifest = reg._load_manifest("sim_both_ok")
            manifest[0]["status"] = "champion"
            manifest[0]["is_active"] = True
            manifest[0]["sign_offs"] = [
                {"role": "ml_engineer", "user": "eng_ml", "signature": sig_ml, "is_signature_valid": True, "is_role_verified": True, "is_identity_verified": True},
                {"role": "compliance", "user": "eng_cp", "signature": sig_cp, "is_signature_valid": True, "is_role_verified": True, "is_identity_verified": True},
            ]
            reg._save_manifest("sim_both_ok", manifest)

            # Challenger v2
            v2 = reg.save_version("sim_both_ok", state_dict, metrics={"auc": 0.90}, is_promoted=False, training_receipt=rcpt, artifact_bytes=raw_bytes)
            user_eng = auth_svc.get_user("eng_ml")
            assert user_eng is not None
            token_ml = auth_svc.create_token_bundle(user_eng).access_token
            promoted_chall = reg.promote_version("sim_both_ok", version=v2["version"], target_status="challenger", auth_token=token_ml)
            assert promoted_chall["status"] == "challenger"

            # Both resolve cleanly
            champ_res = reg.resolve_and_verify_champion("sim_both_ok")
            chall_res = reg.resolve_and_verify_challenger("sim_both_ok")
            assert champ_res is not None
            assert chall_res is not None
            assert champ_res[2]["status"] == "champion"
            assert chall_res[2]["status"] == "challenger"

            # Test live prediction routing to verified challenger
            eval_engine = ModelEvaluationEngine(reg)
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

            with (
                patch.object(predict_module, "_registry", reg),
                patch.object(predict_module, "_eval_engine", eval_engine),
                patch.object(eval_engine, "get_traffic_share", return_value=1.0),
            ):
                predict_module._load_challenger_model.cache_clear()
                predict_module._cached_serving_model = None

                txn_payload = TransactionPredictRequest(
                    simulation_id="sim_both_ok",
                    transaction_amount=150.0,
                    amount=150.0,
                    merchant_category="retail",
                    country_code="US",
                    device_type="web_browser",
                    velocity=1.0,
                    hour_of_day=10,
                    day_of_week=2,
                    is_international=0,
                    merchant_risk_score=0.03,
                    customer_history_score=0.98,
                    chargeback_count=0,
                    account_age_days=500,
                    bank_id="bank_a",
                )

                with patch.object(eval_engine, "log_prediction", wraps=eval_engine.log_prediction) as mock_log:
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
                    assert call_kwargs["routed_to"] == "challenger"
                    assert call_kwargs["challenger_version"] == 2
                    assert call_kwargs["challenger_prob"] is not None


# ── TEST 9: Rejected challenger leaves existing champion unmodified ──────────

def test_rejected_challenger_leaves_existing_champion_unmodified():
    """When a challenger promotion is rejected, existing champion state is 100% preserved."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        settings = get_settings()
        reg = ModelRegistry(storage_dir=tmp_dir)
        state_dict, _, _ = _create_mock_checkpoint()

        sim_dir = reg._get_sim_dir("sim_preserve_champ")
        champ_path = os.path.join(sim_dir, "model_v1.pt")
        torch.save(state_dict, champ_path)
        champ_sha = compute_file_sha256(champ_path)

        unverified_path = os.path.join(sim_dir, "model_v2.pt")
        torch.save(state_dict, unverified_path)
        unv_sha = compute_file_sha256(unverified_path)

        sig_ml = generate_signoff_signature("sim_preserve_champ", 1, champ_sha, "ml_engineer", "usr_ml")
        sig_cp = generate_signoff_signature("sim_preserve_champ", 1, champ_sha, "compliance", "usr_cp")

        manifest = [
            {
                "version": 1,
                "generation": 1,
                "filename": "model_v1.pt",
                "is_active": True,
                "status": "champion",
                "sha256": champ_sha,
                "provenance_type": "TRAINING_RECORDED",
                "is_production_eligible": True,
                "training_receipt": {"r": "receipt"},
                "sign_offs": [
                    {"role": "ml_engineer", "user": "usr_ml", "signature": sig_ml, "is_signature_valid": True, "is_role_verified": True, "is_identity_verified": True},
                    {"role": "compliance", "user": "usr_cp", "signature": sig_cp, "is_signature_valid": True, "is_role_verified": True, "is_identity_verified": True},
                ],
            },
            {
                "version": 2,
                "generation": 1,
                "filename": "model_v2.pt",
                "is_active": False,
                "status": "inactive",
                "sha256": unv_sha,
                "provenance_type": "UNVERIFIED",
                "is_production_eligible": False,
                "training_receipt": None,
            },
        ]
        reg._save_manifest("sim_preserve_champ", manifest)

        with (
            patch.object(settings, "app_env", "production"),
            pytest.raises(ModelIntegrityError),
        ):
            reg.promote_version("sim_preserve_champ", version=2, target_status="challenger")

        # Verify champion v1 is completely untouched
        updated_manifest = reg._load_manifest("sim_preserve_champ")
        champ_entry = next(e for e in updated_manifest if e["version"] == 1)
        unv_entry = next(e for e in updated_manifest if e["version"] == 2)

        assert champ_entry["status"] == "champion"
        assert champ_entry["is_active"] is True
        assert champ_entry["generation"] == 1

        assert unv_entry["status"] == "inactive"
        assert unv_entry["is_active"] is False
