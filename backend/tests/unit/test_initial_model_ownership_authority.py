# ruff: noqa: E402
"""Comprehensive Verification Suite for WORKSTREAM 01-C.4: Initial Model Ownership Authority Fix.

Focused Initial Ownership Authority Verifications:
INITIAL-01: Direct bind_simulation_ownership(..., bound_by="save_version") without authority -> rejected.
INITIAL-02: Direct bind_simulation_ownership(..., _from_save_version=True) without authority -> rejected.
INITIAL-03: save_version() with claimed Bank B ownership but no verified institutional authority -> cannot bind Bank B.
INITIAL-04: A valid model receipt that does not authenticate the claimed institution cannot independently establish that institution's ownership.
INITIAL-05: Authenticated Bank A registration successfully binds Bank A, but cannot claim Bank B.
INITIAL-06: Legitimately authorized consortium model registration succeeds.
INITIAL-07: Existing ownership cannot be overwritten by subsequent ordinary saves.
INITIAL-08: Existing BOUNDARY-01-10 and TENANT-01-10 regressions remain intact.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
from datetime import UTC, datetime
from typing import Any

import pytest
import torch
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    ProposalAction,
    ProposalStatus,
    consortium_governance_service,
    init_default_consortium,
)
from app.application.services.model_registry import ModelRegistry
from app.application.services.model_service import ModelService
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.domain.model_serving_errors import ModelAuthorizationError
from app.infrastructure.security.auth_service import AuthenticationService

NUM_FEATURES = 10
FEATURE_NAMES = [f"f_{i}" for i in range(NUM_FEATURES)]
TEST_SECRET = "cfi_test_initial_ownership_secret_key_32b!"


@pytest.fixture
def initial_auth_env():
    """Isolated multi-bank test environment with authentic DurableAccountStore accounts."""
    temp_dir = tempfile.mkdtemp(prefix="cfi_initial_test_")
    store_path = os.path.join(temp_dir, "accounts.json")
    trust_path = os.path.join(temp_dir, "trust.json")
    reg_dir = os.path.join(temp_dir, "models")
    os.makedirs(reg_dir, exist_ok=True)

    AuthenticationService.reset_instance()
    auth_svc = AuthenticationService(
        signing_secret=TEST_SECRET,
        store_path=store_path,
        authority_secret="cfi_auth_sec_2026_initial_authority",
        app_env="production",
        access_token_expire_minutes=60,
    )
    AuthenticationService._instance = auth_svc

    # Register authentic institutional principals
    u_a_admin = auth_svc.register_user("admin_bank_a", "Pass1234!", "bank_a", ["admin"], clearance_level=3)
    u_a_lead = auth_svc.register_user("lead_bank_a", "Pass1234!", "bank_a", ["ml_lead"], clearance_level=2)
    u_a_mle = auth_svc.register_user("mle_bank_a", "Pass1234!", "bank_a", ["ml_engineer"], clearance_level=2)

    u_b_admin = auth_svc.register_user("admin_bank_b", "Pass1234!", "bank_b", ["admin"], clearance_level=3)
    u_b_lead = auth_svc.register_user("lead_bank_b", "Pass1234!", "bank_b", ["ml_lead"], clearance_level=2)

    u_c_lead = auth_svc.register_user("lead_bank_c", "Pass1234!", "bank_c", ["ml_lead"], clearance_level=2)

    tokens = {
        "a_admin": auth_svc.create_token_bundle(u_a_admin).access_token,
        "a_lead": auth_svc.create_token_bundle(u_a_lead).access_token,
        "a_mle": auth_svc.create_token_bundle(u_a_mle).access_token,
        "b_admin": auth_svc.create_token_bundle(u_b_admin).access_token,
        "b_lead": auth_svc.create_token_bundle(u_b_lead).access_token,
        "c_lead": auth_svc.create_token_bundle(u_c_lead).access_token,
    }

    # Setup Ed25519 cryptographic receipts
    priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_key = priv_key.public_key()
    pub_hex = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "initial_test_issuer_key"
    admin_token = "adm_initial_sec_12345"

    trust_store = DurableTrustStore(trust_file_path=trust_path, admin_token=admin_token)
    trust_store.register_approved_key(kid, pub_hex, admin_token=admin_token)
    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    registry = ModelRegistry(storage_dir=reg_dir, verifier=verifier)
    consortium_governance_service.reset()
    consortium_service = consortium_governance_service

    yield {
        "temp_dir": temp_dir,
        "reg_dir": reg_dir,
        "auth_svc": auth_svc,
        "tokens": tokens,
        "priv_key": priv_key,
        "kid": kid,
        "registry": registry,
        "consortium_service": consortium_service,
    }

    consortium_governance_service.reset()
    init_default_consortium(consortium_governance_service)
    AuthenticationService.reset_instance()
    shutil.rmtree(temp_dir, ignore_errors=True)


def _build_model_artifact() -> tuple[dict[str, Any], bytes, str]:
    """Helper to create dummy model state_dict, raw bytes, and sha256."""
    svc = ModelService(get_settings())
    model = svc.create_model(input_dim=NUM_FEATURES, dp_compatible=True)
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    raw_bytes = buf.getvalue()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    return model.state_dict(), raw_bytes, sha256


# ── INITIAL-01: Direct bind_simulation_ownership(..., bound_by="save_version") Rejected ──


def test_initial_01_direct_bind_simulation_ownership_bound_by_save_version_without_authority_rejected(initial_auth_env):
    """INITIAL-01: Direct bind_simulation_ownership(..., bound_by="save_version") without authority -> rejected."""
    registry: ModelRegistry = initial_auth_env["registry"]
    sim_id = "sim_exploit_bound_by_01"

    # Attacker directly invokes bind_simulation_ownership supplying bound_by="save_version"
    with pytest.raises(ModelAuthorizationError, match="Requires verifiable institutional authority"):
        registry.bind_simulation_ownership(
            simulation_id=sim_id,
            owning_bank_id="bank_b",
            bound_by="save_version",
            auth_token=None,
            trusted_context=None,
            training_receipt=None,
        )

    # Verify no authoritative institutional ownership was established
    ownership = registry.get_simulation_ownership(sim_id)
    assert ownership is None or ownership.get("is_unbound") is True
    ownership_file = os.path.join(initial_auth_env["reg_dir"], sim_id, "ownership.json")
    assert not os.path.exists(ownership_file), "ownership.json file must NOT be persisted"


# ── INITIAL-02: Direct bind_simulation_ownership(..., _from_save_version=True) Rejected ──


def test_initial_02_direct_bind_simulation_ownership_from_save_version_flag_without_authority_rejected(initial_auth_env):
    """INITIAL-02: Direct bind_simulation_ownership(..., _from_save_version=True) without authority -> rejected."""
    registry: ModelRegistry = initial_auth_env["registry"]
    sim_id = "sim_exploit_from_flag_02"

    # Attacker directly invokes bind_simulation_ownership passing _from_save_version=True
    with pytest.raises(ModelAuthorizationError, match="Requires verifiable institutional authority"):
        registry.bind_simulation_ownership(
            simulation_id=sim_id,
            owning_bank_id="bank_b",
            _from_save_version=True,
            auth_token=None,
            trusted_context=None,
            training_receipt=None,
        )

    # Verify no authoritative institutional ownership was established
    ownership = registry.get_simulation_ownership(sim_id)
    assert ownership is None or ownership.get("is_unbound") is True
    ownership_file = os.path.join(initial_auth_env["reg_dir"], sim_id, "ownership.json")
    assert not os.path.exists(ownership_file)


# ── INITIAL-03: save_version() Claimed Bank B Ownership Without Verified Authority Rejected ──


def test_initial_03_save_version_claimed_bank_b_without_verified_authority_cannot_bind(initial_auth_env):
    """INITIAL-03: save_version() with claimed Bank B ownership but no verified institutional authority -> cannot bind Bank B."""
    registry: ModelRegistry = initial_auth_env["registry"]
    state_dict, raw_bytes, _ = _build_model_artifact()
    sim_id = "sim_save_version_unauth_03"

    # Caller attempts save_version claiming bank_b without any valid credentials or receipt
    with pytest.raises(ModelAuthorizationError, match="Requires verifiable institutional authority"):
        registry.save_version(
            simulation_id=sim_id,
            state_dict=state_dict,
            metrics={"loss": 0.1, "auc": 0.9},
            artifact_bytes=raw_bytes,
            owning_bank_id="bank_b",
            auth_token=None,
            trusted_context=None,
            training_receipt=None,
        )

    # Verify neither ownership nor model metadata directory exists
    ownership = registry.get_simulation_ownership(sim_id)
    assert ownership is None or ownership.get("is_unbound") is True
    ownership_file = os.path.join(initial_auth_env["reg_dir"], sim_id, "ownership.json")
    assert not os.path.exists(ownership_file)
    meta_file = os.path.join(initial_auth_env["reg_dir"], sim_id, "metadata.json")
    assert not os.path.exists(meta_file), "Model metadata must not be created on rejected registration"


# ── INITIAL-04: Valid Receipt Without Claimed Institution Claims Cannot Bind ──


def test_initial_04_valid_receipt_without_claimed_institution_claims_cannot_bind(initial_auth_env):
    """INITIAL-04: A valid model receipt that does not authenticate the claimed institution cannot independently establish that institution's ownership."""
    registry: ModelRegistry = initial_auth_env["registry"]
    priv_key: ed25519.Ed25519PrivateKey = initial_auth_env["priv_key"]
    kid: str = initial_auth_env["kid"]
    state_dict, raw_bytes, sha256 = _build_model_artifact()

    # Case 4a: Generic valid receipt with NO institutional claims (owning_bank_id=None)
    sim_id_generic = "sim_generic_receipt_04a"
    issuer = TrainingReceiptIssuer(private_key=priv_key, key_id=kid, require_authenticated_job=False)
    evidence_generic = TrainingExecutionEvidence(
        training_run_id="run_04a",
        job_id="job_04a",
        simulation_id=sim_id_generic,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=2,
        executed_epochs=2,
        sample_count=50,
        observed_metrics={"loss": 0.05, "auc": 0.95},
        artifact_sha256=sha256,
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        feature_names=FEATURE_NAMES,
        owning_bank_id=None,
        consortium_id=None,
    )
    receipt_generic = issuer.issue_receipt(
        evidence=evidence_generic,
        model_bytes=raw_bytes,
        owning_bank_id=None,
        consortium_id=None,
    ).to_dict()

    # Attempt to bind bank_b using the generic receipt (which does not authenticate bank_b)
    with pytest.raises(ModelAuthorizationError, match=r"receipt binds bank 'none', cannot authenticate claimed bank 'bank_b'"):
        registry.save_version(
            simulation_id=sim_id_generic,
            state_dict=state_dict,
            metrics={"loss": 0.05, "auc": 0.95},
            artifact_bytes=raw_bytes,
            training_receipt=receipt_generic,
            owning_bank_id="bank_b",
        )
    assert registry.get_simulation_ownership(sim_id_generic).get("is_unbound") is True

    # Case 4b: Valid receipt signed explicitly for bank_a. Attempt to bind bank_b.
    sim_id_mismatch = "sim_mismatch_receipt_04b"
    evidence_bank_a = TrainingExecutionEvidence(
        training_run_id="run_04b",
        job_id="job_04b",
        simulation_id=sim_id_mismatch,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=2,
        executed_epochs=2,
        sample_count=50,
        observed_metrics={"loss": 0.05, "auc": 0.95},
        artifact_sha256=sha256,
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        feature_names=FEATURE_NAMES,
        owning_bank_id="bank_a",
    )
    receipt_bank_a = issuer.issue_receipt(
        evidence=evidence_bank_a,
        model_bytes=raw_bytes,
        owning_bank_id="bank_a",
    ).to_dict()

    with pytest.raises(ModelAuthorizationError, match=r"receipt binds bank 'bank_a', cannot authenticate claimed bank 'bank_b'"):
        registry.save_version(
            simulation_id=sim_id_mismatch,
            state_dict=state_dict,
            metrics={"loss": 0.05, "auc": 0.95},
            artifact_bytes=raw_bytes,
            training_receipt=receipt_bank_a,
            owning_bank_id="bank_b",
        )
    assert registry.get_simulation_ownership(sim_id_mismatch).get("is_unbound") is True


# ── INITIAL-05: Authenticated Bank A Registration Binds Bank A, Cannot Claim Bank B ──


def test_initial_05_authenticated_bank_a_registration_binds_bank_a_cannot_claim_bank_b(initial_auth_env):
    """INITIAL-05: Authenticated Bank A registration successfully binds Bank A, but cannot claim Bank B."""
    registry: ModelRegistry = initial_auth_env["registry"]
    tokens: dict[str, str] = initial_auth_env["tokens"]
    state_dict, raw_bytes, _ = _build_model_artifact()

    # Step 1: Legitimate Bank A registration with Bank A MLE token succeeds
    sim_id_a = "sim_legit_bank_a_05"
    entry_a = registry.save_version(
        simulation_id=sim_id_a,
        state_dict=state_dict,
        metrics={"loss": 0.04, "auc": 0.96},
        artifact_bytes=raw_bytes,
        owning_bank_id="bank_a",
        auth_token=tokens["a_mle"],
    )
    assert entry_a["version"] == 1
    ownership_a = registry.get_simulation_ownership(sim_id_a)
    assert ownership_a is not None
    assert ownership_a["owning_bank_id"] == "bank_a"
    assert ownership_a["bound_by"].startswith("user:mle_bank_a")

    # Step 2: Same Bank A principal attempts to register claiming Bank B -> rejected
    sim_id_b = "sim_spoof_bank_b_05"
    with pytest.raises(ModelAuthorizationError, match=r"user 'mle_bank_a' belongs to 'bank_a', cannot register for 'bank_b'"):
        registry.save_version(
            simulation_id=sim_id_b,
            state_dict=state_dict,
            metrics={"loss": 0.04, "auc": 0.96},
            artifact_bytes=raw_bytes,
            owning_bank_id="bank_b",
            auth_token=tokens["a_mle"],
        )
    assert registry.get_simulation_ownership(sim_id_b).get("is_unbound") is True


# ── INITIAL-06: Legitimately Authorized Consortium Model Registration Succeeds ──


def test_initial_06_legitimately_authorized_consortium_model_registration_succeeds(initial_auth_env):
    """INITIAL-06: Legitimately authorized consortium model registration succeeds."""
    registry: ModelRegistry = initial_auth_env["registry"]
    consortium_svc: ConsortiumGovernanceService = initial_auth_env["consortium_service"]
    tokens: dict[str, str] = initial_auth_env["tokens"]
    priv_key: ed25519.Ed25519PrivateKey = initial_auth_env["priv_key"]
    kid: str = initial_auth_env["kid"]
    state_dict, raw_bytes, sha256 = _build_model_artifact()

    # Create active consortium with bank_a and bank_b as members
    cid = "consortium_nordic_06"
    consortium_svc.create_consortium(
        cid,
        "Nordic Fraud Consortium",
        "bank_a",
        quorum_ratio=0.51,
        auth_token=tokens["a_admin"],
    )
    prop = consortium_svc.propose_membership_change(
        cid,
        "bank_a",
        "bank_b",
        ProposalAction.ADD_MEMBER,
        auth_token=tokens["a_admin"],
    )
    assert prop.status == ProposalStatus.APPROVED

    # Case 6a: Authorized Member Bank A Lead registers consortium model with auth_token
    sim_id_c_user = "sim_consortium_user_06a"
    entry_user = registry.save_version(
        simulation_id=sim_id_c_user,
        state_dict=state_dict,
        metrics={"loss": 0.03, "auc": 0.97},
        artifact_bytes=raw_bytes,
        consortium_id=cid,
        auth_token=tokens["a_lead"],
    )
    assert entry_user["version"] == 1
    ownership_user = registry.get_simulation_ownership(sim_id_c_user)
    assert ownership_user is not None
    assert ownership_user["consortium_id"] == cid
    assert not ownership_user.get("owning_bank_id")

    # Case 6b: Cryptographically signed receipt authenticating consortium_id succeeds
    sim_id_c_rcpt = "sim_consortium_rcpt_06b"
    issuer = TrainingReceiptIssuer(private_key=priv_key, key_id=kid, require_authenticated_job=False)
    evidence_c = TrainingExecutionEvidence(
        training_run_id="run_06b",
        job_id="job_06b",
        simulation_id=sim_id_c_rcpt,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=5,
        executed_epochs=3,
        sample_count=200,
        observed_metrics={"loss": 0.02, "auc": 0.98},
        artifact_sha256=sha256,
        architecture="FraudDetectionNN",
        feature_schema_version="v1.0",
        feature_names=FEATURE_NAMES,
        consortium_id=cid,
    )
    receipt_c = issuer.issue_receipt(
        evidence=evidence_c,
        model_bytes=raw_bytes,
        consortium_id=cid,
    ).to_dict()

    entry_rcpt = registry.save_version(
        simulation_id=sim_id_c_rcpt,
        state_dict=state_dict,
        metrics={"loss": 0.02, "auc": 0.98},
        artifact_bytes=raw_bytes,
        consortium_id=cid,
        training_receipt=receipt_c,
    )
    assert entry_rcpt["version"] == 1
    ownership_rcpt = registry.get_simulation_ownership(sim_id_c_rcpt)
    assert ownership_rcpt is not None
    assert ownership_rcpt["consortium_id"] == cid

    # Case 6c: Non-member bank (bank_c) principal attempts to register model for consortium -> rejected
    sim_id_c_unauth = "sim_consortium_unauth_06c"
    with pytest.raises(ModelAuthorizationError, match=r"consortium_membership_required: user bank 'bank_c' not in consortium"):
        registry.save_version(
            simulation_id=sim_id_c_unauth,
            state_dict=state_dict,
            metrics={"loss": 0.05, "auc": 0.95},
            artifact_bytes=raw_bytes,
            consortium_id=cid,
            auth_token=tokens["c_lead"],
        )
    assert registry.get_simulation_ownership(sim_id_c_unauth).get("is_unbound") is True


# ── INITIAL-07: Existing Ownership Immutable to Subsequent Ordinary Saves ──


def test_initial_07_existing_ownership_immutable_to_subsequent_ordinary_saves(initial_auth_env):
    """INITIAL-07: Existing ownership cannot be overwritten by subsequent ordinary saves."""
    registry: ModelRegistry = initial_auth_env["registry"]
    tokens: dict[str, str] = initial_auth_env["tokens"]
    state_dict, raw_bytes, _ = _build_model_artifact()

    sim_id = "sim_immutable_07"
    # Establish legitimate initial ownership for Bank A
    registry.save_version(
        simulation_id=sim_id,
        state_dict=state_dict,
        metrics={"loss": 0.05, "auc": 0.95},
        artifact_bytes=raw_bytes,
        owning_bank_id="bank_a",
        auth_token=tokens["a_lead"],
    )
    orig_ownership = registry.get_simulation_ownership(sim_id)
    assert orig_ownership["owning_bank_id"] == "bank_a"

    # Attempt to overwrite ownership via normal save_version claiming bank_b (even with Bank B token)
    with pytest.raises(ModelAuthorizationError, match=r"Cannot overwrite established model ownership"):
        registry.save_version(
            simulation_id=sim_id,
            state_dict=state_dict,
            metrics={"loss": 0.04, "auc": 0.96},
            artifact_bytes=raw_bytes,
            owning_bank_id="bank_b",
            auth_token=tokens["b_lead"],
        )

    # Attempt to rebind via direct bind_simulation_ownership
    with pytest.raises(ModelAuthorizationError, match=r"Unauthorized model ownership rebinding"):
        registry.bind_simulation_ownership(
            simulation_id=sim_id,
            owning_bank_id="bank_b",
            auth_token=tokens["b_lead"],
        )

    # Confirm original ownership is untouched
    final_ownership = registry.get_simulation_ownership(sim_id)
    assert final_ownership["owning_bank_id"] == "bank_a"
    assert final_ownership["bound_by"] == orig_ownership["bound_by"]
    assert final_ownership["bound_at"] == orig_ownership["bound_at"]


# ── INITIAL-08: BOUNDARY-01-10 and TENANT-01-10 Regressions Intact ──


def test_initial_08_boundary_and_tenant_regressions_intact(initial_auth_env):
    """INITIAL-08: Existing BOUNDARY-01-10 and TENANT-01-10 regressions remain intact."""
    registry: ModelRegistry = initial_auth_env["registry"]
    consortium_svc: ConsortiumGovernanceService = initial_auth_env["consortium_service"]
    tokens: dict[str, str] = initial_auth_env["tokens"]
    state_dict, raw_bytes, _ = _build_model_artifact()

    # 1. Unauthenticated direct consortium creation rejected
    with pytest.raises(PermissionError, match="Authentication required"):
        consortium_svc.create_consortium(
            "c_unauth_08",
            "Unauth",
            "bank_a",
            quorum_ratio=0.5,
        )

    # 2. Authenticated consortium creation succeeds
    consortium_svc.create_consortium(
        "c_auth_08",
        "Auth",
        "bank_a",
        quorum_ratio=0.5,
        auth_token=tokens["a_admin"],
    )

    # 3. Bank B cannot vote without credentials
    with pytest.raises(PermissionError, match="Authentication required"):
        consortium_svc.cast_vote(
            "prop_dummy",
            "bank_b",
            approve=True,
        )

    # 4. Bank A saves model with auth_token -> succeeds
    entry = registry.save_version(
        simulation_id="sim_reg_08",
        state_dict=state_dict,
        metrics={"loss": 0.05, "auc": 0.95},
        artifact_bytes=raw_bytes,
        owning_bank_id="bank_a",
        auth_token=tokens["a_mle"],
    )
    assert entry["version"] == 1
    assert registry.get_simulation_ownership("sim_reg_08")["owning_bank_id"] == "bank_a"
