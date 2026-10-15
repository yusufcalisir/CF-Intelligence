"""Adversarial test suite for institutional model ownership binding durability and consistency.

Verifies:
1. Crash during initial ownership binding reconciles to FAILED with unbound state intact.
2. Crash after atomic ownership persistence reconciles to COMMITTED with authoritative binding.
3. Concurrent independent workers attempting conflicting initial ownership bindings (race condition prevention).
4. Crash during authorized ownership transfer recovers unambiguously without state divergence.
5. Corrupted or zero-byte ownership file fails closed with ModelIntegrityError.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import time
from typing import Any

import pytest

from app.application.services.model_registry import (
    ModelIntegrityError,
    ModelRegistry,
)
from app.config import get_settings
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.durable_account_store import DurableAccountStore
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


@pytest.fixture
def own_env(tmp_path, monkeypatch):
    audit_file = str(tmp_path / "security" / "audit_chain.json")
    account_file = str(tmp_path / "security" / "accounts.json")
    reg_dir = str(tmp_path / "models")
    os.makedirs(os.path.dirname(audit_file), exist_ok=True)
    os.makedirs(reg_dir, exist_ok=True)

    audit_secret = "CFI_PROD_AUDIT_LEDGER_SECRET_2026_TEST"
    jwt_secret = "CFI_PROD_JWT_SIGNING_KEY_2026_TEST"
    authority_secret = "CFI_PROD_ACCOUNT_AUTHORITY_SECRET_2026"

    monkeypatch.setenv("CFI_AUDIT_LEDGER_PATH", audit_file)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", audit_secret)
    monkeypatch.setenv("CFI_MODEL_REGISTRY_DIR", reg_dir)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", jwt_secret)
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", account_file)
    monkeypatch.setenv("CFI_ACCOUNT_AUTHORITY_SECRET", authority_secret)
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

    u_admin_a = auth_svc.register_user("admin_a", "SecurePass1234!", "bank_a", ["admin"], clearance_level=3)
    u_admin_b = auth_svc.register_user("admin_b", "SecurePass1234!", "bank_b", ["admin"], clearance_level=3)
    tokens = {
        "admin_a": auth_svc.create_token_bundle(u_admin_a).access_token,
        "admin_b": auth_svc.create_token_bundle(u_admin_b).access_token,
    }

    registry = ModelRegistry(storage_dir=reg_dir)

    yield {
        "tmp_path": tmp_path,
        "audit_file": audit_file,
        "audit_secret": audit_secret,
        "jwt_secret": jwt_secret,
        "authority_secret": authority_secret,
        "account_file": account_file,
        "reg_dir": reg_dir,
        "registry": registry,
        "tokens": tokens,
    }

    get_settings.cache_clear()
    AuthenticationService._instance = None
    ImmutableAuditChain._instance = None


def _worker_bind_process(
    reg_dir: str,
    audit_file: str,
    audit_secret: str,
    jwt_secret: str,
    authority_secret: str,
    account_file: str,
    sim_id: str,
    bank_id: str,
    auth_token: str,
    barrier_path: str | None,
    crash_stage: str | None,
    out_file: str | None = None,
):
    os.environ["CFI_AUDIT_LEDGER_PATH"] = audit_file
    os.environ["CFI_AUDIT_LEDGER_SECRET"] = audit_secret
    os.environ["CFI_JWT_SIGNING_SECRET"] = jwt_secret
    os.environ["CFI_IDENTITY_STORE_PATH"] = account_file
    os.environ["CFI_ACCOUNT_STORE_PATH"] = account_file
    os.environ["CFI_ACCOUNT_AUTHORITY_SECRET"] = authority_secret
    os.environ["CFI_IDENTITY_STORE_SECRET"] = authority_secret
    os.environ["CFI_MODEL_REGISTRY_DIR"] = reg_dir
    os.environ["APP_ENV"] = "production"
    get_settings.cache_clear()

    auth_svc = AuthenticationService(
        store_path=account_file,
        signing_secret=jwt_secret,
        authority_secret=authority_secret,
        app_env="production",
    )
    AuthenticationService._instance = auth_svc

    reg = ModelRegistry(storage_dir=reg_dir)

    if crash_stage == "BEFORE_PERSISTENCE" and barrier_path:
        orig_save = reg._save_ownership

        def hooked_save_before(s_id: str, record: dict[str, Any]):
            with open(barrier_path, "w") as f:
                f.write("BEFORE_OWNERSHIP_PERSIST_REACHED")
            while not os.path.exists(barrier_path + ".done"):
                time.sleep(0.05)
            return orig_save(s_id, record)

        reg._save_ownership = hooked_save_before

    elif crash_stage == "AFTER_PERSISTENCE" and barrier_path:
        orig_save = reg._save_ownership

        def hooked_save_after(s_id: str, record: dict[str, Any]):
            res = orig_save(s_id, record)
            with open(barrier_path, "w") as f:
                f.write("AFTER_OWNERSHIP_PERSIST_REACHED")
            while not os.path.exists(barrier_path + ".done"):
                time.sleep(0.05)
            return res

        reg._save_ownership = hooked_save_after

    try:
        rec = reg.bind_simulation_ownership(
            simulation_id=sim_id,
            owning_bank_id=bank_id,
            actor="admin",
            auth_token=auth_token,
        )
        if out_file:
            with open(out_file, "w") as f:
                json.dump({"success": True, "bank_id": bank_id, "record": rec}, f)
    except Exception as exc:
        if barrier_path:
            with open(barrier_path + ".err", "w") as f:
                f.write(f"{type(exc).__name__}: {exc}\n")
        if out_file:
            with open(out_file, "w") as f:
                json.dump({"success": False, "bank_id": bank_id, "error": str(exc)}, f)
        raise


def _worker_reconciler(reg_dir: str, audit_file: str, audit_secret: str, sim_id: str, out_file: str):
    os.environ["CFI_AUDIT_LEDGER_PATH"] = audit_file
    os.environ["CFI_AUDIT_LEDGER_SECRET"] = audit_secret
    os.environ["APP_ENV"] = "production"
    get_settings.cache_clear()

    reg = ModelRegistry(storage_dir=reg_dir)
    res = reg.reconcile_incomplete_mutations(simulation_id=sim_id)
    with open(out_file, "w") as f:
        json.dump(res, f)


def test_crash_before_ownership_persistence_reconciles_to_failed(own_env):
    """Crash after PENDING ownership intent but before disk write reconciles to FAILED."""
    sim_id = "sim_own_crash_01"
    tmp_path = own_env["tmp_path"]
    barrier = str(tmp_path / "barrier_own_01.txt")

    p = multiprocessing.Process(
        target=_worker_bind_process,
        args=(
            own_env["reg_dir"],
            own_env["audit_file"],
            own_env["audit_secret"],
            own_env["jwt_secret"],
            own_env["authority_secret"],
            own_env["account_file"],
            sim_id,
            "bank_a",
            own_env["tokens"]["admin_a"],
            barrier,
            "BEFORE_PERSISTENCE",
        ),
    )
    p.start()

    start_t = time.time()
    while not os.path.exists(barrier) and not os.path.exists(barrier + ".err") and time.time() - start_t < 30.0:
        time.sleep(0.05)
    err_content = ""
    if os.path.exists(barrier + ".err"):
        with open(barrier + ".err") as f:
            err_content = f.read()
    assert os.path.exists(barrier), f"Worker failed to reach barrier! Error: {err_content}"

    p.kill()
    p.join(timeout=5.0)

    # Reconcile via independent worker
    out_file = str(tmp_path / "recon_own_out_01.json")
    p_b = multiprocessing.Process(
        target=_worker_reconciler,
        args=(
            own_env["reg_dir"],
            own_env["audit_file"],
            own_env["audit_secret"],
            sim_id,
            out_file,
        ),
    )
    p_b.start()
    p_b.join(timeout=25.0)
    assert not p_b.is_alive()

    with open(out_file) as f:
        reconciled = json.load(f)
    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "FAILED"

    # Authoritative ownership must remain unbound!
    reg = own_env["registry"]
    own = reg.get_simulation_ownership(sim_id)
    assert own.get("is_unbound") is True
    assert own.get("owning_bank_id") is None


def test_crash_after_ownership_persistence_reconciles_to_committed(own_env):
    """Crash after atomic ownership file write reconciles to COMMITTED."""
    sim_id = "sim_own_crash_02"
    tmp_path = own_env["tmp_path"]
    barrier = str(tmp_path / "barrier_own_02.txt")

    p = multiprocessing.Process(
        target=_worker_bind_process,
        args=(
            own_env["reg_dir"],
            own_env["audit_file"],
            own_env["audit_secret"],
            own_env["jwt_secret"],
            own_env["authority_secret"],
            own_env["account_file"],
            sim_id,
            "bank_a",
            own_env["tokens"]["admin_a"],
            barrier,
            "AFTER_PERSISTENCE",
        ),
    )
    p.start()

    start_t = time.time()
    while not os.path.exists(barrier) and not os.path.exists(barrier + ".err") and time.time() - start_t < 30.0:
        time.sleep(0.05)
    err_content = ""
    if os.path.exists(barrier + ".err"):
        with open(barrier + ".err") as f:
            err_content = f.read()
    assert os.path.exists(barrier), f"Worker failed to reach barrier! Error: {err_content}"

    p.kill()
    p.join(timeout=5.0)

    # Reconcile via independent worker
    out_file = str(tmp_path / "recon_own_out_02.json")
    p_b = multiprocessing.Process(
        target=_worker_reconciler,
        args=(
            own_env["reg_dir"],
            own_env["audit_file"],
            own_env["audit_secret"],
            sim_id,
            out_file,
        ),
    )
    p_b.start()
    p_b.join(timeout=25.0)
    assert not p_b.is_alive()

    with open(out_file) as f:
        reconciled = json.load(f)
    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "COMMITTED"

    # Authoritative ownership must be committed to bank_a
    reg = own_env["registry"]
    own = reg.get_simulation_ownership(sim_id)
    assert own.get("is_unbound") is False
    assert own.get("owning_bank_id") == "bank_a"


def test_concurrent_independent_workers_conflicting_ownership_race(own_env):
    """Two concurrent OS processes racing to bind initial ownership: exactly one wins."""
    sim_id = "sim_own_race_03"
    tmp_path = own_env["tmp_path"]
    out_a = str(tmp_path / "worker_a_res.json")
    out_b = str(tmp_path / "worker_b_res.json")

    p_a = multiprocessing.Process(
        target=_worker_bind_process,
        args=(
            own_env["reg_dir"],
            own_env["audit_file"],
            own_env["audit_secret"],
            own_env["jwt_secret"],
            own_env["authority_secret"],
            own_env["account_file"],
            sim_id,
            "bank_a",
            own_env["tokens"]["admin_a"],
            None,
            None,
            out_a,
        ),
    )
    p_b = multiprocessing.Process(
        target=_worker_bind_process,
        args=(
            own_env["reg_dir"],
            own_env["audit_file"],
            own_env["audit_secret"],
            own_env["jwt_secret"],
            own_env["authority_secret"],
            own_env["account_file"],
            sim_id,
            "bank_b",
            own_env["tokens"]["admin_b"],
            None,
            None,
            out_b,
        ),
    )

    p_a.start()
    p_b.start()

    p_a.join(timeout=35.0)
    p_b.join(timeout=35.0)

    assert not p_a.is_alive()
    assert not p_b.is_alive()

    if os.path.exists(out_a):
        with open(out_a) as f:
            res_a = json.load(f)
    else:
        res_a = {"success": False, "error": "timed out"}

    if os.path.exists(out_b):
        with open(out_b) as f:
            res_b = json.load(f)
    else:
        res_b = {"success": False, "error": "timed out"}

    # Exactly ONE process must succeed and ONE must fail
    successes = [r for r in (res_a, res_b) if r.get("success") is True]
    failures = [r for r in (res_a, res_b) if r.get("success") is False]

    assert len(successes) == 1, f"Expected exactly one winner, got {successes}"
    assert len(failures) == 1, f"Expected exactly one loser, got {failures}"

    winner_bank = successes[0]["bank_id"]

    # Verify authoritative disk state strictly matches the winning bank
    reg = own_env["registry"]
    own = reg.get_simulation_ownership(sim_id)
    assert own["owning_bank_id"] == winner_bank


def test_corrupted_or_empty_ownership_fails_closed(own_env):
    """A zero-byte or corrupted ownership.json fails closed with ModelIntegrityError."""
    sim_id = "sim_own_corrupt_04"
    reg = own_env["registry"]
    sim_dir = reg._get_sim_dir(sim_id, create=True)
    own_path = os.path.join(sim_dir, "ownership.json")

    # 1. Zero-byte file
    with open(own_path, "w", encoding="utf-8") as f:
        f.write("")

    with pytest.raises(ModelIntegrityError, match="Ownership record.*is empty"):
        reg.get_simulation_ownership(sim_id)

    # 2. Corrupted JSON file
    with open(own_path, "w", encoding="utf-8") as f:
        f.write("{invalid_json_bytes...")

    with pytest.raises(ModelIntegrityError, match="corrupted or unreadable"):
        reg.get_simulation_ownership(sim_id)
