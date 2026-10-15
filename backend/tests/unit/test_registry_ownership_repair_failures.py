"""Focused regression tests verifying ownership metadata repair failure semantics and fail-closed behavior.

Verifies:
Test C: Legitimate ownership transfer where derived manifest persistence fails during repair.
        - No false claim of successful durable repair.
        - No unauthorized access based on Bank A's stale derived ownership.
        - A subsequent healthy recovery completes the repair.
        - Both independent workers observe the same authoritative owner afterward (cross-process).
Test D: Corrupt or unreadable ownership record of an already-bound simulation.
        - The registry does not silently fall back to stale manifest ownership as authoritative truth.
        - Ownership-dependent governance actions fail closed (save_version, sign_off, promote, rebind).
        - No unauthorized rebind or approval occurs.
        - Restoring valid authoritative state restores legitimate behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import torch
import torch.nn as nn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.application.services.model_registry import (
    ModelAuthorizationError,
    ModelIntegrityError,
    ModelRegistry,
    generate_signoff_signature,
)
from app.application.services.training_provenance_service import (
    DurableTrustStore,
    TrainingExecutionEvidence,
    TrainingReceiptIssuer,
    TrainingReceiptVerifier,
)
from app.config import get_settings
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_account_store import DurableAccountStore
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class LinearModel(nn.Module):
    def __init__(self, in_features: int = 10, out_features: int = 2):
        super().__init__()
        self.fc = nn.Linear(in_features, out_features)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


@pytest.fixture
def ownership_repair_env(tmp_path, monkeypatch):
    audit_file = str(tmp_path / "security" / "audit_chain.json")
    account_file = str(tmp_path / "security" / "accounts.json")
    trust_file = str(tmp_path / "security" / "trust_store.json")
    reg_dir = str(tmp_path / "models")
    os.makedirs(os.path.dirname(audit_file), exist_ok=True)
    os.makedirs(reg_dir, exist_ok=True)

    audit_secret = "CFI_PROD_AUDIT_LEDGER_SECRET_2026_TEST"
    jwt_secret = "CFI_PROD_JWT_SIGNING_KEY_2026_TEST"
    authority_secret = "CFI_PROD_ACCOUNT_AUTHORITY_SECRET_2026"
    admin_token = "CFI_TRUST_ADMIN_TOKEN_2026"
    payload_secret = "CFI_PROD_PAYLOAD_SIGNING_SECRET_2026_TEST"

    monkeypatch.setenv("CFI_AUDIT_LEDGER_PATH", audit_file)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", audit_secret)
    monkeypatch.setenv("CFI_TRUST_STORE_PATH", trust_file)
    monkeypatch.setenv("CFI_TRUST_STORE_ADMIN_TOKEN", admin_token)
    monkeypatch.setenv("CFI_MODEL_REGISTRY_DIR", reg_dir)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", jwt_secret)
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", account_file)
    monkeypatch.setenv("CFI_ACCOUNT_AUTHORITY_SECRET", authority_secret)
    monkeypatch.setenv("CFI_PAYLOAD_SIGNING_SECRET", payload_secret)
    monkeypatch.setenv("APP_ENV", "production")

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

    u_alice = auth_svc.register_user("alice_bank_a", "SecurePass1234!", "bank_a", ["ml_lead", "ml_engineer"], clearance_level=2)
    u_bob = auth_svc.register_user("bob_bank_b", "SecurePass1234!", "bank_b", ["ml_lead", "ml_engineer"], clearance_level=2)
    u_carol = auth_svc.register_user("carol_comp_a", "SecurePass1234!", "bank_a", ["compliance", "compliance_officer"], clearance_level=2)
    u_dan = auth_svc.register_user("dan_comp_b", "SecurePass1234!", "bank_b", ["compliance", "compliance_officer"], clearance_level=2)

    tokens = {
        "alice_a": auth_svc.create_token_bundle(u_alice).access_token,
        "bob_b": auth_svc.create_token_bundle(u_bob).access_token,
        "carol_a": auth_svc.create_token_bundle(u_carol).access_token,
        "dan_b": auth_svc.create_token_bundle(u_dan).access_token,
    }

    priv = ed25519.Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    kid = "issuer_ownership_test_01"
    trust_store = DurableTrustStore(trust_file_path=trust_file, admin_token=admin_token)
    trust_store.register_approved_key(key_id=kid, public_key_hex=pub_hex, admin_token=admin_token)

    settings = get_settings()
    orig_env = settings.app_env
    settings.app_env = "development"
    try:
        issuer = TrainingReceiptIssuer(
            key_id=kid,
            private_key=priv,
            require_authenticated_job=False,
            app_env="development",
            trust_store=trust_store,
        )
    finally:
        settings.app_env = orig_env

    verifier = TrainingReceiptVerifier(trust_store=trust_store)
    registry = ModelRegistry(storage_dir=reg_dir, verifier=verifier)

    monkeypatch.setenv("CFI_TRAINING_RECEIPT_PUBLIC_KEY", pub_hex)
    monkeypatch.setenv("CFI_IDENTITY_STORE_PATH", account_file)
    monkeypatch.setenv("CFI_IDENTITY_STORE_SECRET", authority_secret)

    env_dict = {
        "tmp_path": tmp_path,
        "audit_file": audit_file,
        "account_file": account_file,
        "trust_file": trust_file,
        "reg_dir": reg_dir,
        "registry": registry,
        "tokens": tokens,
        "issuer": issuer,
        "verifier": verifier,
        "pub_hex": pub_hex,
        "jwt_secret": jwt_secret,
        "authority_secret": authority_secret,
        "audit_secret": audit_secret,
        "admin_token": admin_token,
        "payload_secret": payload_secret,
    }

    yield env_dict

    get_settings.cache_clear()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None


def _build_candidate(env: dict, sim_id: str, owning_bank: str = "bank_a") -> tuple[str, Any]:
    torch.manual_seed(303)
    net = LinearModel()
    candidate_path = str(env["tmp_path"] / f"cand_{uuid.uuid4().hex}.pt")
    torch.save(net.state_dict(), candidate_path)

    with open(candidate_path, "rb") as f:
        mbytes = f.read()
    msha = hashlib.sha256(mbytes).hexdigest()

    ev = TrainingExecutionEvidence(
        training_run_id=f"run-{sim_id}",
        job_id=f"job-{sim_id}",
        simulation_id=sim_id,
        completed_at=datetime.now(UTC).isoformat(),
        executed_rounds=3,
        executed_epochs=3,
        sample_count=200,
        observed_metrics={"loss": 0.05, "auc": 0.94},
        artifact_sha256=msha,
        architecture="LinearModel",
        feature_schema_version="2.0",
        feature_names=[f"f{i}" for i in range(10)],
    )
    settings = get_settings()
    orig = settings.app_env
    settings.app_env = "development"
    try:
        receipt = env["issuer"].issue_receipt(
            evidence=ev,
            model_bytes=mbytes,
            owning_bank_id=owning_bank,
            architecture="LinearModel",
            input_dim=10,
            feature_names=[f"f{i}" for i in range(10)],
            feature_schema_version="2.0",
        )
    finally:
        settings.app_env = orig

    return candidate_path, receipt


def _run_worker(code: str, env_vars: dict[str, str], timeout: float = 30.0) -> dict[str, Any]:
    """Execute Python code in an independent OS process with dedicated environment."""
    child_env = os.environ.copy()
    child_env["PYTHONPATH"] = BACKEND_DIR
    child_env.update(env_vars)

    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=child_env,
        timeout=timeout,
    )
    if res.returncode != 0:
        return {
            "success": False,
            "returncode": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }

    lines = [line.strip() for line in res.stdout.strip().split("\n") if line.strip()]
    last_line = lines[-1] if lines else "{}"
    try:
        parsed = json.loads(last_line)
        return {
            "success": True,
            "returncode": 0,
            "data": parsed,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    except Exception:
        return {
            "success": False,
            "returncode": res.returncode,
            "error": "Failed to parse json",
            "stdout": res.stdout,
            "stderr": res.stderr,
        }


def test_ownership_repair_persistence_failure(ownership_repair_env):
    """Test C: Verifies that ownership repair failures are never silently accepted.

    1. Initial registration establishes simulation ownership as Bank A.
    2. Legitimate transfer updates authoritative ownership.json to Bank B,
       while derived manifest (registry.json) remains at Bank A.
    3. Failure injected into _save_manifest during repair attempts:
       - System never reports successful durable repair (_load_manifest fails closed).
       - Bank A cannot perform governance actions based on stale derived manifest.
    4. Healthy recovery restores _save_manifest:
       - Repair completes cleanly.
    5. Two independent OS worker processes inspect state:
       - Both independently observe Bank B in ownership.json and registry.json.
    """
    env = ownership_repair_env
    reg: ModelRegistry = env["registry"]
    sim_id = "sim_test_c_repair_01"

    # Step 1: Initial registration bound to Bank A
    cand_path, receipt = _build_candidate(env, sim_id, owning_bank="bank_a")
    reg.save_version(
        simulation_id=sim_id,
        source_model_path=cand_path,
        metrics={"auc": 0.95, "loss": 0.04},
        parameters={"architecture": "LinearModel"},
        owning_bank_id="bank_a",
        auth_token=env["tokens"]["alice_a"],
        training_receipt=receipt.to_dict(),
    )

    sim_dir = reg._get_sim_dir(sim_id)
    own_path = os.path.join(sim_dir, "ownership.json")
    manifest_path = os.path.join(sim_dir, "registry.json")

    with open(own_path, encoding="utf-8") as f:
        own_init = json.load(f)
    assert own_init["owning_bank_id"] == "bank_a"

    with open(manifest_path, encoding="utf-8") as f:
        manifest_init = json.load(f)
    assert manifest_init[0]["owning_bank_id"] == "bank_a"

    # Step 2: Legitimate ownership transfer to Bank B recorded in authoritative ownership.json
    # Simulate an interrupted transfer state where ownership.json was successfully committed to Bank B,
    # but the derived manifest (registry.json) still holds Bank A.
    own_record_b = {
        **own_init,
        "owning_bank_id": "bank_b",
        "last_mutation_id": f"mut_transfer_{uuid.uuid4().hex}",
        "applied_mutation_ids": own_init.get("applied_mutation_ids", []) + ["mut_transfer_test_c"],
    }
    with open(own_path, "w", encoding="utf-8") as f:
        json.dump(own_record_b, f, indent=2)

    # Confirm stale manifest still identifies Bank A
    with open(manifest_path, encoding="utf-8") as f:
        stale_manifest = json.load(f)
    assert stale_manifest[0]["owning_bank_id"] == "bank_a"

    # Step 3: Inject failure when persisting repaired manifest
    orig_save_manifest = reg._save_manifest

    def failing_save_manifest(simulation_id: str, manifest: list[dict[str, Any]]) -> None:
        raise ModelIntegrityError(f"Simulated storage failure persisting manifest repair for '{simulation_id}'")

    reg._save_manifest = failing_save_manifest

    # A) Verify _load_manifest raises ModelIntegrityError and does NOT silently succeed with in-memory fix
    with pytest.raises(ModelIntegrityError) as exc_info:
        reg._load_manifest(sim_id)
    assert "manifest repair" in str(exc_info.value).lower() or "authoritative ownership record" in str(exc_info.value).lower()

    # B) On disk, registry.json was NOT falsely updated
    with open(manifest_path, encoding="utf-8") as f:
        disk_manifest_during_fail = json.load(f)
    assert disk_manifest_during_fail[0]["owning_bank_id"] == "bank_a"

    # C) Verify Bank A cannot gain unauthorized access based on Bank A's stale derived ownership
    cand_path2, receipt2 = _build_candidate(env, sim_id, owning_bank="bank_a")
    with pytest.raises(ModelAuthorizationError) as auth_err:
        reg.save_version(
            simulation_id=sim_id,
            source_model_path=cand_path2,
            metrics={"auc": 0.94, "loss": 0.05},
            parameters={"architecture": "LinearModel"},
            auth_token=env["tokens"]["alice_a"],  # Bank A user
            training_receipt=receipt2.to_dict(),
        )
    assert "Cross-bank authorization rejected" in str(auth_err.value)
    assert "bank_b" in str(auth_err.value)
    assert not os.path.exists(os.path.join(sim_dir, "model_v2.pt"))

    # C2) Legitimate owner (Bank B) attempting operation while repair persistence fails also fails closed
    cand_path_b, receipt_b = _build_candidate(env, sim_id, owning_bank="bank_b")
    with pytest.raises(ModelIntegrityError):
        reg.save_version(
            simulation_id=sim_id,
            source_model_path=cand_path_b,
            metrics={"auc": 0.95, "loss": 0.04},
            parameters={"architecture": "LinearModel"},
            auth_token=env["tokens"]["bob_b"],  # Bank B user
            training_receipt=receipt_b.to_dict(),
        )

    # Step 4: Subsequent healthy recovery completes the repair
    reg._save_manifest = orig_save_manifest

    # Calling _load_manifest now successfully repairs and persists the manifest
    repaired_manifest = reg._load_manifest(sim_id)
    assert repaired_manifest[0]["owning_bank_id"] == "bank_b"

    with open(manifest_path, encoding="utf-8") as f:
        disk_manifest_after_repair = json.load(f)
    assert disk_manifest_after_repair[0]["owning_bank_id"] == "bank_b"

    # Legitimate owner (Bank B) now succeeds
    v2_entry = reg.save_version(
        simulation_id=sim_id,
        source_model_path=cand_path_b,
        metrics={"auc": 0.95, "loss": 0.04},
        parameters={"architecture": "LinearModel"},
        auth_token=env["tokens"]["bob_b"],
        training_receipt=receipt_b.to_dict(),
    )
    assert v2_entry["version"] == 2
    assert v2_entry["owning_bank_id"] == "bank_b"

    # Bank A remains unauthorized
    with pytest.raises(ModelAuthorizationError):
        reg.save_version(
            simulation_id=sim_id,
            source_model_path=cand_path2,
            metrics={"auc": 0.94, "loss": 0.05},
            parameters={"architecture": "LinearModel"},
            auth_token=env["tokens"]["alice_a"],
            training_receipt=receipt2.to_dict(),
        )

    # Step 5: Both independent OS workers observe the same authoritative owner afterward
    worker_env_vars = {
        "CFI_AUDIT_LEDGER_PATH": env["audit_file"],
        "CFI_AUDIT_LEDGER_SECRET": env["audit_secret"],
        "CFI_TRUST_STORE_PATH": env["trust_file"],
        "CFI_TRUST_STORE_ADMIN_TOKEN": env["admin_token"],
        "CFI_MODEL_REGISTRY_DIR": env["reg_dir"],
        "CFI_JWT_SIGNING_SECRET": env["jwt_secret"],
        "CFI_ACCOUNT_STORE_PATH": env["account_file"],
        "CFI_ACCOUNT_AUTHORITY_SECRET": env["authority_secret"],
        "CFI_PAYLOAD_SIGNING_SECRET": env["payload_secret"],
        "APP_ENV": "production",
    }

    worker_code = f"""
import json
from app.application.services.model_registry import ModelRegistry

reg = ModelRegistry(storage_dir={json.dumps(env["reg_dir"])})
own = reg.get_simulation_ownership({json.dumps(sim_id)})
manifest = reg._load_manifest({json.dumps(sim_id)})

res = {{
    "owning_bank_id": own.get("owning_bank_id"),
    "manifest_owner": manifest[0].get("owning_bank_id") if manifest else None,
}}
print(json.dumps(res))
"""

    w1 = _run_worker(worker_code, worker_env_vars)
    assert w1["success"], f"Worker 1 failed: {w1.get('stderr')}"
    assert w1["data"]["owning_bank_id"] == "bank_b"
    assert w1["data"]["manifest_owner"] == "bank_b"

    w2 = _run_worker(worker_code, worker_env_vars)
    assert w2["success"], f"Worker 2 failed: {w2.get('stderr')}"
    assert w2["data"]["owning_bank_id"] == "bank_b"
    assert w2["data"]["manifest_owner"] == "bank_b"


def test_corrupt_or_unreadable_ownership_record_fails_closed(ownership_repair_env):
    """Test D: Corrupt or unreadable ownership record of an already-bound simulation.

    Verifies:
    1. The registry does NOT silently fall back to stale manifest ownership as authoritative truth.
    2. Ownership-dependent governance actions fail closed (save_version, sign_off, promote, rebind).
    3. Empty (0 bytes) ownership record also fails closed with explicit corruption error.
    4. Restoring valid authoritative state restores legitimate governance behavior.
    """
    env = ownership_repair_env
    reg: ModelRegistry = env["registry"]
    sim_id = "sim_test_d_corrupt_01"

    # Step 1: Initial registration bound to Bank A
    cand_path, receipt = _build_candidate(env, sim_id, owning_bank="bank_a")
    v1_entry = reg.save_version(
        simulation_id=sim_id,
        source_model_path=cand_path,
        metrics={"auc": 0.95, "loss": 0.04},
        parameters={"architecture": "LinearModel"},
        owning_bank_id="bank_a",
        auth_token=env["tokens"]["alice_a"],
        training_receipt=receipt.to_dict(),
    )
    assert v1_entry["version"] == 1

    sim_dir = reg._get_sim_dir(sim_id)
    own_path = os.path.join(sim_dir, "ownership.json")

    # Save backup of valid ownership record
    with open(own_path, encoding="utf-8") as f:
        valid_own_bytes = f.read()

    # Step 2: Corrupt ownership record with invalid JSON syntax
    with open(own_path, "w", encoding="utf-8") as f:
        f.write("CORRUPTED_JSON_DATA{{{{")

    # 1. get_simulation_ownership raises ModelIntegrityError (never falls back to manifest)
    with pytest.raises(ModelIntegrityError) as exc_info:
        reg.get_simulation_ownership(sim_id)
    assert "corrupted or unreadable" in str(exc_info.value).lower()

    # 2. _load_manifest raises ModelIntegrityError
    with pytest.raises(ModelIntegrityError) as exc_info2:
        reg._load_manifest(sim_id)
    assert "corrupted or unreadable" in str(exc_info2.value).lower()

    # 3. Ownership-dependent governance actions FAIL CLOSED:

    # 3a) save_version fails closed
    cand_path_v2, receipt_v2 = _build_candidate(env, sim_id, owning_bank="bank_a")
    with pytest.raises(ModelIntegrityError):
        reg.save_version(
            simulation_id=sim_id,
            source_model_path=cand_path_v2,
            metrics={"auc": 0.96, "loss": 0.03},
            parameters={"architecture": "LinearModel"},
            auth_token=env["tokens"]["alice_a"],
            training_receipt=receipt_v2.to_dict(),
        )

    # 3b) sign_off fails closed
    sig = generate_signoff_signature(
        simulation_id=sim_id,
        version=1,
        artifact_sha256=v1_entry["sha256"],
        role="compliance",
        user="carol_comp_a",
        signing_secret=env["payload_secret"],
    )
    with pytest.raises(ModelIntegrityError):
        reg.sign_off(
            simulation_id=sim_id,
            version=1,
            role="compliance",
            user="carol_comp_a",
            signature=sig,
            auth_token=env["tokens"]["carol_a"],
        )

    # 3c) promote_version fails closed
    with pytest.raises(ModelIntegrityError):
        reg.promote_version(
            simulation_id=sim_id,
            version=1,
            target_status="champion",
            auth_token=env["tokens"]["alice_a"],
        )

    # 3d) bind_simulation_ownership fails closed (no unauthorized rebind)
    with pytest.raises(ModelIntegrityError):
        reg.bind_simulation_ownership(
            simulation_id=sim_id,
            owning_bank_id="bank_b",
            auth_token=env["tokens"]["bob_b"],
        )

    # Step 3: Test empty (0 bytes) ownership record also fails closed
    with open(own_path, "w", encoding="utf-8") as f:
        f.write("")

    with pytest.raises(ModelIntegrityError) as exc_empty_own:
        reg.get_simulation_ownership(sim_id)
    assert "empty (0 bytes)" in str(exc_empty_own.value).lower()

    with pytest.raises(ModelIntegrityError) as exc_empty_man:
        reg._load_manifest(sim_id)
    assert "empty (0 bytes)" in str(exc_empty_man.value).lower()

    # Step 4: Restoring valid authoritative state restores legitimate behavior
    with open(own_path, "w", encoding="utf-8") as f:
        f.write(valid_own_bytes)

    # Verification passes
    restored_own = reg.get_simulation_ownership(sim_id)
    assert restored_own["owning_bank_id"] == "bank_a"
    assert not restored_own["is_unbound"]

    restored_manifest = reg._load_manifest(sim_id)
    assert len(restored_manifest) == 1
    assert restored_manifest[0]["owning_bank_id"] == "bank_a"

    # Legitimate governance action (sign_off) now succeeds
    signed_entry = reg.sign_off(
        simulation_id=sim_id,
        version=1,
        role="compliance",
        user="carol_comp_a",
        signature=sig,
        auth_token=env["tokens"]["carol_a"],
    )
    assert any(s["user"] == "carol_comp_a" for s in signed_entry["sign_offs"])
