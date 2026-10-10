"""
Unit tests verifying secure model caching, generation matching, and instant revocation.

Threat Model & Invariants Tested:
1. DEFECT 5: Stale or revoked champion models must not survive cache validation.
   The Redis envelope must strictly require and verify the model's generation against
   the authoritative registry champion generation. Missing generation, mismatched generation,
   or altered authorization state must cause immediate cache rejection.
2. Scope isolation: Models cached for simulation_id='sim_a' must never be served
   for simulation_id='sim_b'.
3. DEFECT 9: Serving-time revocation. When an approval or version is revoked, the registry's
   generation increments, causing both process-local and Redis caches to be rejected,
   failing closed rather than returning unauthorized models.
"""

import hashlib
import hmac
import io
import json
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import torch

from app.application.services.model_registry import (
    ModelRegistry,
    generate_signoff_signature,
)
from app.application.services.model_service import FraudDetectionModel
from app.application.services.training_provenance_service import (
    get_default_training_issuer,
)
from app.config import get_settings
from app.domain.model_serving_errors import (
    ModelIntegrityError,
    ModelNotAvailableError,
)
from app.infrastructure.security.auth_service import (
    AuthenticationService,
)
from app.presentation.routers.realtime_inference import (
    _local_cache,
    get_scripted_model,
    reset_model_cache,
)


def _setup_approved_champion(tmp_dir: str, simulation_id: str = "sim_cache_test") -> tuple[ModelRegistry, int]:
    registry = ModelRegistry(storage_dir=tmp_dir)

    model = FraudDetectionModel(input_dim=10, dp_compatible=True)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    raw_bytes = buf.getvalue()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    issuer = get_default_training_issuer()
    receipt = issuer.issue_receipt(
        training_run_id="run_c01",
        job_id="job_c01",
        artifact_sha256=sha256,
        architecture="FraudDetectionModel-GroupNorm",
        feature_names=[f"f_{i}" for i in range(10)],
        simulation_id=simulation_id,
        metrics={"loss": 0.01},
    )

    metadata = registry.save_version(
        simulation_id=simulation_id,
        state_dict=model.state_dict(),
        metrics={"loss": 0.01},
        is_promoted=False,
        provenance_type="TRAINING_RECORDED",
        provenance_receipt=receipt.to_dict(),
        artifact_bytes=raw_bytes,
    )
    version = metadata["version"]

    auth_svc = AuthenticationService.get_instance()
    u1 = auth_svc.register_user("eng_dave", "Pass123!", "bank_a", roles=["ml_engineer"])
    u2 = auth_svc.register_user("comp_ann", "Pass123!", "bank_a", roles=["compliance"])
    tok1 = auth_svc.create_token_bundle(u1).access_token
    tok2 = auth_svc.create_token_bundle(u2).access_token

    sig1 = generate_signoff_signature(simulation_id, version, sha256, "ml_engineer", "eng_dave")
    sig2 = generate_signoff_signature(simulation_id, version, sha256, "compliance", "comp_ann")

    registry.sign_off(simulation_id, version, "ml_engineer", user="eng_dave", signature=sig1, auth_token=tok1)
    registry.sign_off(simulation_id, version, "compliance", user="comp_ann", signature=sig2, auth_token=tok2)

    with patch.object(get_settings(), "app_env", "production"):
        registry.promote_version(simulation_id, version)

    return registry, version


class TestSecureModelCacheAndRevocation:
    """Tests for Defects 5 & 9: Redis and local cache generation checks and revocation."""

    def test_local_cache_invalidated_upon_signoff_revocation(self):
        """
        Threat: A champion model is warmed into in-memory local cache. Later, compliance revokes sign-off.
        Invariant: The local cache must notice the generation bump / revocation and refuse to serve.
        """
        reset_model_cache()
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry, version = _setup_approved_champion(tmp_dir, simulation_id="sim_rev_test")

            # 1. Warm local cache with patched ModelRegistry default
            with patch("app.presentation.routers.realtime_inference._get_redis_client", return_value=None), \
                 patch("app.application.services.model_registry.ModelRegistry", return_value=registry):
                model1, _ = get_scripted_model(simulation_id="sim_rev_test")
                assert model1 is not None

                # Verify cached entry exists in local cache
                cached_ident = registry.get_active_champion_identity(simulation_id="sim_rev_test")
                assert _local_cache.get(cached_ident) is not None

                # 2. Revoke compliance approval
                registry.revoke_sign_off("sim_rev_test", version=version, role="compliance", revoked_by="comp_ann")

                # 3. Next get_scripted_model must reject the revoked champion
                with patch.object(get_settings(), "app_env", "production"), \
                     pytest.raises((ModelIntegrityError, ModelNotAvailableError)):
                    get_scripted_model(simulation_id="sim_rev_test")

    def test_redis_cache_rejects_missing_generation(self):
        """
        Threat: An adversary injects a cache envelope into Redis missing the 'generation' field.
        Invariant: Real-time inference must reject the envelope and refuse deserialization.
        """
        reset_model_cache()
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry, version = _setup_approved_champion(tmp_dir, simulation_id="sim_redis_gen")
            meta = registry.get_version_metadata("sim_redis_gen", version)
            assert meta is not None

            envelope_data = {
                "version": version,
                "model_sha256": meta["sha256"],
                # "generation": omitted!
            }
            envelope_bytes = json.dumps(envelope_data).encode("utf-8")
            mac = hmac.new(b"test_secret", envelope_bytes, hashlib.sha256).hexdigest()
            raw_cached = json.dumps({"payload": envelope_data, "hmac": mac}).encode("utf-8")

            mock_redis = MagicMock()
            mock_redis.get.return_value = raw_cached

            with patch("app.presentation.routers.realtime_inference._get_redis_client", return_value=mock_redis), \
                 patch("app.application.services.model_registry.ModelRegistry", return_value=registry), \
                 patch("torch.jit.load") as mock_jit_load:
                model, from_redis = get_scripted_model(simulation_id="sim_redis_gen")
                assert from_redis is False
                assert not mock_jit_load.called

    def test_simulation_scope_isolation_in_cache(self):
        """
        Threat: Scope 'sim_alpha' tries to load model cached for 'sim_beta'.
        Invariant: Local and Redis cache keys must strictly partition models by simulation scope.
        """
        reset_model_cache()
        with tempfile.TemporaryDirectory() as tmp_dir:
            reg_a, ver_a = _setup_approved_champion(tmp_dir, simulation_id="sim_alpha")

            with patch("app.presentation.routers.realtime_inference._get_redis_client", return_value=None), \
                 patch("app.application.services.model_registry.ModelRegistry", return_value=reg_a):
                model_a, _ = get_scripted_model(simulation_id="sim_alpha")
                assert model_a is not None

                # Requesting sim_beta when only sim_alpha has a champion must fail closed
                with pytest.raises(ModelNotAvailableError, match="No verified active champion"):
                    get_scripted_model(simulation_id="sim_beta")
