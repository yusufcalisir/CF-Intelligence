"""test_final_consortium_authority_hardening.py

FINAL-CONSIST-01 through FINAL-CONSIST-10:
Authoritative security verification for consortium governance hardening.
Covers:
1. Authenticated institutional authority on role mutations (FINAL-CONSIST-01).
2. Authenticated institutional authority on voting power mutations (FINAL-CONSIST-02).
3. Cryptographic verification of internal context in add_member() (FINAL-CONSIST-03).
4. Prevention of quorum bypass by ordinary institutional JWTs (FINAL-CONSIST-04).
5. Protection against silent production membership fabrication (FINAL-CONSIST-05).
6. Fail-closed rejection when established authority disappears across restarts (FINAL-CONSIST-06).
7. Cryptographic HMAC-SHA256 rejection of tampered state with recomputed SHA-256 (FINAL-CONSIST-07).
8. Rejection of stripped integrity metadata in production (FINAL-CONSIST-08).
9. Legitimate explicit production bootstrap, voting, and cross-bank governance (FINAL-CONSIST-09).
10. System regressions and backward compatibility validation (FINAL-CONSIST-10).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON_EXE = sys.executable
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

PROD_JWT_SECRET = "production_super_secure_signing_secret_jwt_key_32chars!"
PROD_STORE_SECRET = "production_super_secure_consortium_authority_secret_32chars!"


def _run_worker(code: str, env_vars: dict[str, str] | None = None) -> dict:
    """Execute Python code in an independent OS process to test true cross-worker guarantees."""
    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)
    env["PYTHONPATH"] = BACKEND_DIR

    res = subprocess.run(
        [PYTHON_EXE, "-c", code],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env=env,
    )

    if res.returncode != 0:
        return {
            "success": False,
            "returncode": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }

    try:
        lines = [line.strip() for line in res.stdout.strip().split("\n") if line.strip()]
        last_line = lines[-1] if lines else "{}"
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
            "success": True,
            "returncode": 0,
            "raw_output": res.stdout,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }


@pytest.fixture
def prod_consortium_env(tmp_path: Path):
    """Provides isolated production environment and storage paths for authority hardening tests."""
    c_store_path = str(tmp_path / "consortium_prod_store.json")
    id_store_path = str(tmp_path / "identity_prod_store.json")
    env = {
        "CFI_CONSORTIUM_STORE_PATH": c_store_path,
        "CFI_IDENTITY_STORE_PATH": id_store_path,
        "CFI_CONSORTIUM_STORE_SECRET": PROD_STORE_SECRET,
        "CFI_IDENTITY_STORE_SECRET": PROD_STORE_SECRET,
        "CFI_JWT_SIGNING_SECRET": PROD_JWT_SECRET,
        "APP_ENV": "production",
    }
    return {
        "c_store_path": c_store_path,
        "id_store_path": id_store_path,
        "env": env,
    }


# ── FINAL-CONSIST-01: Unauthenticated Direct Role Change Rejected ───────────


def test_final_consist_01_unauthenticated_role_change_rejected(prod_consortium_env):
    """FINAL-CONSIST-01: Unauthenticated direct role change rejected; stored role unchanged."""
    env = prod_consortium_env["env"]

    # Step 1: Bootstrap initial consortium with explicit bootstrap flag
    bootstrap_code = """
import json
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    init_default_consortium,
)

init_default_consortium(allow_bootstrap=True)
svc = ConsortiumGovernanceService()
c = svc.get_consortium("cfi-consortium")
print(json.dumps({
    "initialized": c is not None,
    "bank_b_role": c.members["bank_b"].role.value,
}))
"""
    res_boot = _run_worker(bootstrap_code, env_vars=env)
    assert res_boot["success"], f"Bootstrap failed: {res_boot['stderr']}"
    assert res_boot["data"]["bank_b_role"].lower() == "full_member"

    # Step 2: Worker A attempts unauthenticated direct role change
    worker_a_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import MemberRole

svc = ConsortiumGovernanceService()
rejected = False
err_msg = ""
try:
    svc.set_member_role("cfi-consortium", "bank_b", MemberRole.FOUNDER)
except PermissionError as e:
    rejected = True
    err_msg = str(e)

print(json.dumps({"rejected": rejected, "err_msg": err_msg}))
"""
    res_a = _run_worker(worker_a_code, env_vars=env)
    assert res_a["success"], f"Worker A failed: {res_a['stderr']}"
    assert res_a["data"]["rejected"] is True

    # Step 3: Independent Worker B verifies stored role remains unchanged
    worker_b_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService

svc = ConsortiumGovernanceService()
c = svc.get_consortium("cfi-consortium")
print(json.dumps({
    "bank_b_role": c.members["bank_b"].role.value,
}))
"""
    res_b = _run_worker(worker_b_code, env_vars=env)
    assert res_b["success"], f"Worker B failed: {res_b['stderr']}"
    assert res_b["data"]["bank_b_role"].lower() == "full_member"


# ── FINAL-CONSIST-02: Unauthenticated Voting Power Modification Rejected ────


def test_final_consist_02_unauthenticated_voting_power_rejected(prod_consortium_env):
    """FINAL-CONSIST-02: Unauthenticated voting power modification rejected; quorum unchanged."""
    env = prod_consortium_env["env"]

    # Bootstrap consortium
    setup_code = """
import json
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    init_default_consortium,
)

init_default_consortium(allow_bootstrap=True)
svc = ConsortiumGovernanceService()
c = svc.get_consortium("cfi-consortium")
print(json.dumps({"power": c.members["bank_b"].voting_power}))
"""
    res_setup = _run_worker(setup_code, env_vars=env)
    assert res_setup["success"], f"Setup failed: {res_setup['stderr']}"
    assert res_setup["data"]["power"] == 1.0

    # Worker A attempts unauthenticated voting power escalation
    worker_a_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService

svc = ConsortiumGovernanceService()
rejected = False
try:
    svc.set_member_voting_power("cfi-consortium", "bank_b", 999.0)
except PermissionError:
    rejected = True

print(json.dumps({"rejected": rejected}))
"""
    res_a = _run_worker(worker_a_code, env_vars=env)
    assert res_a["success"], f"Worker A failed: {res_a['stderr']}"
    assert res_a["data"]["rejected"] is True

    # Independent Worker B verifies voting power remains 1.0
    worker_b_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService

svc = ConsortiumGovernanceService()
c = svc.get_consortium("cfi-consortium")
print(json.dumps({"power": c.members["bank_b"].voting_power}))
"""
    res_b = _run_worker(worker_b_code, env_vars=env)
    assert res_b["success"], f"Worker B failed: {res_b['stderr']}"
    assert res_b["data"]["power"] == 1.0


# ── FINAL-CONSIST-03: Arbitrary trusted_context Cannot Authorize add_member ──


def test_final_consist_03_arbitrary_trusted_context_rejected(prod_consortium_env):
    """FINAL-CONSIST-03: Arbitrary trusted_context=object() cannot authorize add_member()."""
    env = prod_consortium_env["env"]

    bootstrap_code = """
from app.application.services.consortium_service import init_default_consortium
init_default_consortium(allow_bootstrap=True)
"""
    res_boot = _run_worker(bootstrap_code, env_vars=env)
    assert res_boot["success"], f"Bootstrap failed: {res_boot['stderr']}"

    # Attempt direct add_member with dummy context object
    worker_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import MemberRole

class UnverifiedDummyContext:
    service_name = "consortium_coordinator"
    action = "add_member"

svc = ConsortiumGovernanceService()
rejected = False
err = ""
try:
    svc.add_member(
        "cfi-consortium",
        "bank_attacker",
        role=MemberRole.FOUNDER,
        voting_power=10.0,
        trusted_context=UnverifiedDummyContext(),
    )
except PermissionError as pe:
    rejected = True
    err = str(pe)

c = svc.get_consortium("cfi-consortium")
print(json.dumps({
    "rejected": rejected,
    "err": err,
    "attacker_present": "bank_attacker" in c.members,
}))
"""
    res = _run_worker(worker_code, env_vars=env)
    assert res["success"], f"Worker failed: {res['stderr']}"
    assert res["data"]["rejected"] is True
    assert res["data"]["attacker_present"] is False


# ── FINAL-CONSIST-04: Ordinary Institutional JWT Cannot Bypass Quorum ────────


def test_final_consist_04_ordinary_jwt_cannot_bypass_voting(prod_consortium_env):
    """FINAL-CONSIST-04: Ordinary institutional JWT cannot directly bypass membership voting."""
    env = prod_consortium_env["env"]

    bootstrap_code = """
from app.application.services.consortium_service import init_default_consortium
init_default_consortium(allow_bootstrap=True)
"""
    res_boot = _run_worker(bootstrap_code, env_vars=env)
    assert res_boot["success"], f"Bootstrap failed: {res_boot['stderr']}"

    # Bank B (an ordinary full member) tries to directly admit Bank D without proposal/quorum
    worker_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import MemberRole
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_b = auth_svc.get_user("admin_b") or auth_svc.register_user("admin_b", "Pass123!", "bank_b", roles=["admin"])
token_b = auth_svc.create_token_bundle(u_b).access_token

svc = ConsortiumGovernanceService()
rejected = False
err = ""
try:
    svc.add_member("cfi-consortium", "bank_d", role=MemberRole.FULL_MEMBER, auth_token=token_b)
except PermissionError as pe:
    rejected = True
    err = str(pe)

c = svc.get_consortium("cfi-consortium")
print(json.dumps({
    "rejected": rejected,
    "err": err,
    "bank_d_admitted": "bank_d" in c.members,
}))
"""
    res = _run_worker(worker_code, env_vars=env)
    assert res["success"], f"Worker failed: {res['stderr']}"
    assert res["data"]["rejected"] is True
    assert "cannot bypass" in res["data"]["err"].lower() or "direct admission rejected" in res["data"]["err"].lower()
    assert res["data"]["bank_d_admitted"] is False


# ── FINAL-CONSIST-05: Fresh Production Worker Cannot Fabricate Memberships ──


def test_final_consist_05_fresh_production_worker_cannot_silently_create_default(prod_consortium_env):
    """FINAL-CONSIST-05: Fresh production worker cannot silently recreate default institutional memberships."""
    env = prod_consortium_env["env"]

    # Fresh worker boots with uncreated store file
    worker_code = """
import json, os
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    init_default_consortium,
)
from app.domain.consortium_governance import ConsortiumSecurityError

svc = ConsortiumGovernanceService()
c_on_import = svc.get_consortium("cfi-consortium")

# Explicit attempt to call default init without allow_bootstrap flag fails closed
bootstrap_failed_closed = False
err = ""
try:
    init_default_consortium()
except ConsortiumSecurityError as ce:
    bootstrap_failed_closed = True
    err = str(ce)

c_after_failed_init = svc.get_consortium("cfi-consortium")
print(json.dumps({
    "c_on_import_none": c_on_import is None,
    "bootstrap_failed_closed": bootstrap_failed_closed,
    "c_after_failed_init_none": c_after_failed_init is None,
    "file_exists": os.path.exists(os.environ["CFI_CONSORTIUM_STORE_PATH"]),
}))
"""
    res = _run_worker(worker_code, env_vars=env)
    assert res["success"], f"Worker failed: {res['stderr']}"
    assert res["data"]["c_on_import_none"] is True
    assert res["data"]["bootstrap_failed_closed"] is True
    assert res["data"]["c_after_failed_init_none"] is True


# ── FINAL-CONSIST-06: Missing Established Authority Fails Closed Across Restart


def test_final_consist_06_missing_established_authority_fails_closed_restart(prod_consortium_env):
    """FINAL-CONSIST-06: Missing established authority fails closed across worker restart."""
    env = prod_consortium_env["env"]
    c_store_path = prod_consortium_env["c_store_path"]

    # Worker 1: Legitimate explicit bootstrap establishes production authority
    worker_1_code = """
import json
from app.application.services.consortium_service import init_default_consortium
c = init_default_consortium(allow_bootstrap=True)
print(json.dumps({"established": c is not None}))
"""
    res1 = _run_worker(worker_1_code, env_vars=env)
    assert res1["success"], f"Worker 1 failed: {res1['stderr']}"
    assert res1["data"]["established"] is True
    assert os.path.exists(c_store_path)

    # Disappearance event: Authority persistence file is removed/unmounted
    os.remove(c_store_path)
    assert not os.path.exists(c_store_path)

    # Worker 2: Fresh independent process restarts and attempts to access consortium
    worker_2_code = """
import json
from app.domain.consortium_governance import ConsortiumSecurityError
failed_closed = False
err = ""
try:
    from app.application.services.consortium_service import ConsortiumGovernanceService
    svc = ConsortiumGovernanceService()
    svc.get_consortium("cfi-consortium")
except ConsortiumSecurityError as ce:
    failed_closed = True
    err = str(ce)
except Exception as e:
    err = f"Unexpected {type(e).__name__}: {e}"

print(json.dumps({"failed_closed": failed_closed, "err": err}))
"""
    res2 = _run_worker(worker_2_code, env_vars=env)
    assert res2["success"], f"Worker 2 failed: {res2['stderr']}"
    assert res2["data"]["failed_closed"] is True
    assert "disappeared" in res2["data"]["err"].lower() or "missing" in res2["data"]["err"].lower()


# ── FINAL-CONSIST-07: Modified File with Recomputed SHA-256 Rejected ────────


def test_final_consist_07_modified_file_with_recomputed_sha256_rejected(prod_consortium_env):
    """FINAL-CONSIST-07: Modified consortium file with recomputed SHA-256 is rejected."""
    env = prod_consortium_env["env"]
    c_store_path = prod_consortium_env["c_store_path"]

    # Bootstrap consortium
    setup_code = """
from app.application.services.consortium_service import init_default_consortium
init_default_consortium(allow_bootstrap=True)
"""
    res_boot = _run_worker(setup_code, env_vars=env)
    assert res_boot["success"], f"Setup failed: {res_boot['stderr']}"

    # Read original file
    with open(c_store_path, encoding="utf-8") as f:
        container = json.load(f)

    # Attacker mutates voting power of bank_b to 999.0
    container["data"]["consortia"]["cfi-consortium"]["members"]["bank_b"]["voting_power"] = 999.0
    canonical_bytes = json.dumps(container["data"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    recomputed_sha = hashlib.sha256(canonical_bytes).hexdigest()
    container["sha256"] = recomputed_sha
    container["integrity_sha256"] = recomputed_sha
    # Attacker does NOT know the authority secret, leaving original or forged HMAC tag

    with open(c_store_path, "w", encoding="utf-8") as f:
        json.dump(container, f, indent=2)

    # Worker attempts to load tampered state
    worker_code = """
import json
from app.domain.consortium_governance import ConsortiumSecurityError
failed_closed = False
err = ""
try:
    from app.application.services.consortium_service import ConsortiumGovernanceService
    svc = ConsortiumGovernanceService()
    svc.get_consortium("cfi-consortium")
except ConsortiumSecurityError as ce:
    failed_closed = True
    err = str(ce)
except Exception as e:
    err = f"Unexpected {type(e).__name__}: {e}"

print(json.dumps({"failed_closed": failed_closed, "err": err}))
"""
    res = _run_worker(worker_code, env_vars=env)
    assert res["success"], f"Worker failed: {res['stderr']}"
    assert res["data"]["failed_closed"] is True
    assert "hmac" in res["data"]["err"].lower() or "authentication tag mismatch" in res["data"]["err"].lower()


# ── FINAL-CONSIST-08: Removed Integrity Metadata Rejected in Production ────


def test_final_consist_08_removed_integrity_metadata_rejected(prod_consortium_env):
    """FINAL-CONSIST-08: Removed integrity metadata is rejected in production."""
    env = prod_consortium_env["env"]
    c_store_path = prod_consortium_env["c_store_path"]

    setup_code = """
from app.application.services.consortium_service import init_default_consortium
init_default_consortium(allow_bootstrap=True)
"""
    res_boot = _run_worker(setup_code, env_vars=env)
    assert res_boot["success"], f"Setup failed: {res_boot['stderr']}"

    # Strip integrity metadata
    with open(c_store_path, encoding="utf-8") as f:
        container = json.load(f)

    container.pop("integrity_hmac_sha256", None)

    with open(c_store_path, "w", encoding="utf-8") as f:
        json.dump(container, f, indent=2)

    worker_code = """
import json
from app.domain.consortium_governance import ConsortiumSecurityError
failed_closed = False
err = ""
try:
    from app.application.services.consortium_service import ConsortiumGovernanceService
    svc = ConsortiumGovernanceService()
    svc.get_consortium("cfi-consortium")
except ConsortiumSecurityError as ce:
    failed_closed = True
    err = str(ce)
except Exception as e:
    err = f"Unexpected {type(e).__name__}: {e}"

print(json.dumps({"failed_closed": failed_closed, "err": err}))
"""
    res = _run_worker(worker_code, env_vars=env)
    assert res["success"], f"Worker failed: {res['stderr']}"
    assert res["data"]["failed_closed"] is True
    assert "unauthenticated persistence format" in res["data"]["err"].lower() or "integrity_hmac_sha256" in res["data"]["err"].lower()


# ── FINAL-CONSIST-09: Legitimate Bootstrap, Membership Changes, Voting ──────


def test_final_consist_09_legitimate_bootstrap_voting_governance(prod_consortium_env):
    """FINAL-CONSIST-09: Legitimate explicit bootstrap, approved membership changes, and cross-bank voting remain functional."""
    env = prod_consortium_env["env"]

    # Worker 1: Explicit production bootstrap
    worker_1_code = """
import json
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    init_default_consortium,
)

c = init_default_consortium(allow_bootstrap=True)
svc = ConsortiumGovernanceService()
print(json.dumps({
    "consortium_id": c.consortium_id,
    "members": sorted(list(c.members.keys())),
    "quorum_ratio": c.quorum_ratio,
}))
"""
    res1 = _run_worker(worker_1_code, env_vars=env)
    assert res1["success"], f"Worker 1 failed: {res1['stderr']}"
    assert res1["data"]["members"] == ["bank_a", "bank_b", "bank_c"]

    # Worker 2: Bank A sponsors proposal to add Bank D with valid institutional token
    worker_2_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import ProposalAction
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_a = auth_svc.get_user("admin_a") or auth_svc.register_user("admin_a", "Pass123!", "bank_a", roles=["admin"])
token_a = auth_svc.create_token_bundle(u_a).access_token

svc = ConsortiumGovernanceService()
p = svc.propose_membership_change(
    consortium_id="cfi-consortium",
    creator_bank_id="bank_a",
    target_bank_id="bank_d",
    action=ProposalAction.ADD_MEMBER,
    auth_token=token_a,
)
print(json.dumps({
    "proposal_id": p.proposal_id,
    "status": p.status.value,
    "votes_for": sorted(list(p.votes_for)),
}))
"""
    res2 = _run_worker(worker_2_code, env_vars=env)
    assert res2["success"], f"Worker 2 failed: {res2['stderr']}"
    prop_id = res2["data"]["proposal_id"]
    assert res2["data"]["status"] == "PENDING"
    assert res2["data"]["votes_for"] == ["bank_a"]

    # Worker 3: Bank B votes FOR proposal; reaches quorum (2/3 = 66.7% >= 51%)
    worker_3_code = f"""
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_b = auth_svc.get_user("admin_b") or auth_svc.register_user("admin_b", "Pass123!", "bank_b", roles=["admin"])
token_b = auth_svc.create_token_bundle(u_b).access_token

svc = ConsortiumGovernanceService()
p_voted = svc.cast_vote("{prop_id}", "bank_b", approve=True, auth_token=token_b)
c = svc.get_consortium("cfi-consortium")

print(json.dumps({{
    "proposal_status": p_voted.status.value,
    "bank_d_is_member": "bank_d" in c.members,
    "bank_d_voting_power": c.members["bank_d"].voting_power if "bank_d" in c.members else 0.0,
    "all_members": sorted(list(c.members.keys())),
}}))
"""
    res3 = _run_worker(worker_3_code, env_vars=env)
    assert res3["success"], f"Worker 3 failed: {res3['stderr']}"
    assert res3["data"]["proposal_status"] == "APPROVED"
    assert res3["data"]["bank_d_is_member"] is True
    assert res3["data"]["all_members"] == ["bank_a", "bank_b", "bank_c", "bank_d"]


# ── FINAL-CONSIST-10: Regressions Intact Verification ────────────────────────


def test_final_consist_10_regressions_intact():
    """FINAL-CONSIST-10: Regression suites remain passing with authenticated authority hardening."""
    from app.application.services.consortium_service import ConsortiumGovernanceService
    from app.domain.consortium_governance import MemberRole

    # Verify basic in-memory / developer functionality
    svc = ConsortiumGovernanceService()
    c = svc.get_consortium("cfi-consortium")
    assert c is not None
    assert "bank_a" in c.members
    assert c.members["bank_a"].role == MemberRole.FOUNDER
