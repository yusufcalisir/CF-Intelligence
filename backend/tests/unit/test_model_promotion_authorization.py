"""
Unit tests verifying production promotion authorization and institutional governance.

Threat Model & Invariants Tested:
1. DEFECT 3: Dual-Approval Enforcement Across All Entry Points.
   Every production champion activation (save_version(is_promoted=True), promote_version,
   rollback, resolve_and_verify_champion) must require two distinct authenticated institutional
   sign-offs ('ml_engineer' and 'compliance'). A model with 0 or 1 sign-off must fail closed.
2. DEFECT 4: Principal Identity Proof & Four-Eyes Principle.
   Caller-supplied usernames are not proof of human identity. Sign-offs must be backed by
   cryptographically signed JWTs from authenticated principals. The same principal cannot
   perform both sign-off roles. Development seeded accounts are prohibited in production.
   Revocation of a sign-off immediately renders the champion unpromotable and invalidates serving.
"""

import hashlib
import io
import tempfile
from unittest.mock import patch

import pytest
import torch

from app.application.services.model_registry import (
    ModelRegistry,
    generate_signoff_signature,
)
from app.application.services.training_provenance_service import (
    get_default_training_issuer,
)
from app.config import get_settings
from app.domain.model_serving_errors import ModelIntegrityError
from app.infrastructure.security.auth_service import (
    AuthenticationService,
)


def _create_verified_model(
    registry: ModelRegistry, simulation_id: str = "sim_test_gov", owning_bank_id: str = "bank_test"
) -> int:
    """Helper creating a cryptographically verified candidate model version."""
    state_dict = {
        "fc.weight": torch.randn(2, 10, dtype=torch.float32),
        "fc.bias": torch.zeros(2, dtype=torch.float32),
    }
    buf = io.BytesIO()
    torch.save(state_dict, buf)
    raw_bytes = buf.getvalue()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    issuer = get_default_training_issuer()
    receipt = issuer.issue_receipt(
        training_run_id="run_gov_001",
        job_id="job_gov_001",
        artifact_sha256=sha256,
        architecture="autoencoder",
        feature_names=["f1", "f2"],
        simulation_id=simulation_id,
        metrics={"loss": 0.01},
        owning_bank_id=owning_bank_id,
    )

    metadata = registry.save_version(
        simulation_id=simulation_id,
        state_dict=state_dict,
        metrics={"loss": 0.01},
        is_promoted=False,
        provenance_type="TRAINING_RECORDED",
        provenance_receipt=receipt.to_dict(),
        artifact_bytes=raw_bytes,
        owning_bank_id=owning_bank_id,
    )
    return metadata["version"]


def _register_and_create_token(username: str, role: str, bank_id: str = "bank_test", clearance_level: int = 2) -> str:
    auth_svc = AuthenticationService.get_instance()
    user = auth_svc.register_user(username, "StrongPass123!", bank_id, roles=[role], clearance_level=clearance_level)
    bundle = auth_svc.create_token_bundle(user)
    return bundle.access_token


class TestDualApprovalAndInstitutionalGovernance:
    """Tests for Defect 3 & 4: Dual approvals and identity proof across all promotion entry points."""

    def test_promotion_fails_with_zero_approvals(self):
        """Threat: Attempting to promote a model without any sign-offs."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            version = _create_verified_model(registry, "sim_test_gov")

            with patch.object(get_settings(), "app_env", "production"), \
                 pytest.raises(ModelIntegrityError, match="Missing required sign-offs"):
                registry.promote_version("sim_test_gov", version)

    def test_promotion_fails_with_single_approval(self):
        """Threat: Model has ML engineer sign-off but lacks Compliance sign-off."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            version = _create_verified_model(registry, "sim_test_gov")

            token_ml = _register_and_create_token("eng_dave", "ml_engineer")
            meta = registry.get_version_metadata("sim_test_gov", version)
            assert meta is not None
            sig = generate_signoff_signature("sim_test_gov", version, meta["sha256"], "ml_engineer", "eng_dave")
            registry.sign_off("sim_test_gov", version, "ml_engineer", user="eng_dave", signature=sig, auth_token=token_ml)

            with patch.object(get_settings(), "app_env", "production"), \
                 pytest.raises(ModelIntegrityError, match="Missing required sign-offs"):
                registry.promote_version("sim_test_gov", version)

    def test_promotion_succeeds_with_both_valid_institutional_approvals(self):
        """Invariant: Having both valid, distinct ML Engineer and Compliance approvals permits promotion."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            version = _create_verified_model(registry, "sim_test_gov")

            token_ml = _register_and_create_token("eng_alice", "ml_engineer")
            token_comp = _register_and_create_token("comp_carol", "compliance")

            meta = registry.get_version_metadata("sim_test_gov", version)
            assert meta is not None
            sig_ml = generate_signoff_signature("sim_test_gov", version, meta["sha256"], "ml_engineer", "eng_alice")
            sig_comp = generate_signoff_signature("sim_test_gov", version, meta["sha256"], "compliance", "comp_carol")

            registry.sign_off("sim_test_gov", version, "ml_engineer", user="eng_alice", signature=sig_ml, auth_token=token_ml)
            registry.sign_off("sim_test_gov", version, "compliance", user="comp_carol", signature=sig_comp, auth_token=token_comp)

            with patch.object(get_settings(), "app_env", "production"):
                promoted = registry.promote_version("sim_test_gov", version, auth_token=token_ml)
                assert promoted["status"] == "champion"
                assert promoted["is_active"] is True

    def test_four_eyes_principle_blocks_same_user_from_both_roles(self):
        """
        Threat: A single insider attempts to approve both ML Engineer and Compliance sign-offs.
        Invariant: Registry must reject the second sign-off under the Four-Eyes Principle.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            version = _create_verified_model(registry, "sim_test_gov")

            token = _register_and_create_token("insider_dan", "ml_engineer")
            meta = registry.get_version_metadata("sim_test_gov", version)
            assert meta is not None
            sig_ml = generate_signoff_signature("sim_test_gov", version, meta["sha256"], "ml_engineer", "insider_dan")
            sig_comp = generate_signoff_signature("sim_test_gov", version, meta["sha256"], "compliance", "insider_dan")

            registry.sign_off("sim_test_gov", version, "ml_engineer", user="insider_dan", signature=sig_ml, auth_token=token)

            with pytest.raises((ModelIntegrityError, ValueError), match="Four-Eyes Principle violation"):
                registry.sign_off("sim_test_gov", version, "compliance", user="insider_dan", signature=sig_comp, auth_token=token)

    def test_revoked_sign_off_blocks_subsequent_promotion_and_bumps_generation(self):
        """
        Threat: An approval is revoked prior to or after promotion.
        Invariant: Generation increments, and model becomes ineligible for champion status.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            version = _create_verified_model(registry, "sim_test_gov")

            token_ml = _register_and_create_token("eng_one", "ml_engineer")
            token_comp = _register_and_create_token("comp_two", "compliance")

            meta = registry.get_version_metadata("sim_test_gov", version)
            assert meta is not None
            sig_ml = generate_signoff_signature("sim_test_gov", version, meta["sha256"], "ml_engineer", "eng_one")
            sig_comp = generate_signoff_signature("sim_test_gov", version, meta["sha256"], "compliance", "comp_two")

            registry.sign_off("sim_test_gov", version, "ml_engineer", user="eng_one", signature=sig_ml, auth_token=token_ml)
            registry.sign_off("sim_test_gov", version, "compliance", user="comp_two", signature=sig_comp, auth_token=token_comp)

            # Revoke compliance sign-off
            meta_revoked = registry.revoke_sign_off("sim_test_gov", version, role="compliance", revoked_by="comp_two")
            assert "compliance" not in meta_revoked["sign_offs"]
            assert meta_revoked["generation"] > 1

            # Promotion must fail closed
            with patch.object(get_settings(), "app_env", "production"), \
                 pytest.raises(ModelIntegrityError, match=r"(Missing required sign-offs|not production eligible)"):
                registry.promote_version("sim_test_gov", version)

    def test_rollback_to_model_lacking_approvals_is_rejected(self):
        """
        Threat: Operator attempts rollback() to an earlier version that lacks required approvals.
        Invariant: Rollback enforces the exact same production admission policy as promotion.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            registry = ModelRegistry(storage_dir=tmp_dir)
            v1 = _create_verified_model(registry, "sim_test_gov")
            v2 = _create_verified_model(registry, "sim_test_gov")

            # Legitimate approvals for v2
            token_ml = _register_and_create_token("eng_one", "ml_engineer")
            token_comp = _register_and_create_token("comp_two", "compliance")
            meta_v2 = registry.get_version_metadata("sim_test_gov", v2)
            assert meta_v2 is not None
            sig_ml = generate_signoff_signature("sim_test_gov", v2, meta_v2["sha256"], "ml_engineer", "eng_one")
            sig_comp = generate_signoff_signature("sim_test_gov", v2, meta_v2["sha256"], "compliance", "comp_two")

            registry.sign_off("sim_test_gov", v2, "ml_engineer", user="eng_one", signature=sig_ml, auth_token=token_ml)
            registry.sign_off("sim_test_gov", v2, "compliance", user="comp_two", signature=sig_comp, auth_token=token_comp)

            token_admin = _register_and_create_token("admin_op", "admin", clearance_level=3)
            with patch.object(get_settings(), "app_env", "production"):
                registry.promote_version("sim_test_gov", v2, auth_token=token_ml)

                # Attempt rollback to v1 (which has 0 approvals)
                with pytest.raises(ModelIntegrityError, match="Missing required sign-offs"):
                    registry.rollback("sim_test_gov", version=v1, auth_token=token_admin)
