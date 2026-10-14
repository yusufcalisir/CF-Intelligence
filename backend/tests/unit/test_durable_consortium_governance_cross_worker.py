# ruff: noqa: E402
"""Multi-process adversarial verification test suite for Durable Consortium Governance & Cross-Worker Revocation.

Covers:
- CONSIST-01: Multi-worker consortium creation and authoritative visibility.
- CONSIST-02: Cross-worker member eviction and governance action rejection.
- CONSIST-03: Cross-worker consortium-model approval/promotion rejection for evicted member.
- CONSIST-04: Worker restart preserves removals, proposals, votes, and policies.
- CONSIST-05: Concurrent cross-worker voting race condition and quorum integrity.
- CONSIST-06: Corrupted or missing production consortium authority fails closed.
- CONSIST-07: Default consortium bootstrap idempotency prevents expelled member resurrection.
- CONSIST-08: Direct membership mutation cannot bypass production authorization.
- CONSIST-09: Legitimate multi-bank governance, voting, and model lifecycle success.
- CONSIST-10: Existing governance and tenant authority regressions remain intact.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parent.parent.parent)


def _run_worker(
    code: str,
    env_vars: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Execute Python code in an independent OS process with dedicated environment."""
    env = os.environ.copy()
    env["PYTHONPATH"] = BACKEND_DIR
    if env_vars:
        env.update(env_vars)

    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
        env=env,
        timeout=timeout,
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
def isolated_consortium_env(tmp_path: Path):
    """Provides isolated durable store paths for cross-worker tests."""
    c_store_path = str(tmp_path / "consortium_test_store.json")
    id_store_path = str(tmp_path / "identity_test_store.json")
    env = {
        "CFI_CONSORTIUM_STORE_PATH": c_store_path,
        "CFI_IDENTITY_STORE_PATH": id_store_path,
        "APP_ENV": "development",
    }
    return {
        "c_store_path": c_store_path,
        "id_store_path": id_store_path,
        "env": env,
    }


# ── CONSIST-01: Multi-Worker Consortium Creation & Visibility ───────────────


def test_consist_01_worker_a_creates_worker_b_observes(isolated_consortium_env):
    """CONSIST-01: Worker A creates consortium; independent Worker B observes authoritative state."""
    env = isolated_consortium_env["env"]

    worker_a_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_a = auth_svc.get_user("admin_a") or auth_svc.register_user("admin_a", "Pass123!", "bank_a", roles=["admin"])
token_a = auth_svc.create_token_bundle(u_a).access_token

svc = ConsortiumGovernanceService()
c = svc.create_consortium(
    consortium_id="consortium_cross_01",
    name="Cross-Worker Alliance",
    founder_bank_id="bank_a",
    quorum_ratio=0.6,
    max_epsilon=4.5,
    auth_token=token_a,
)
print(json.dumps({
    "consortium_id": c.consortium_id,
    "founder": c.founder_bank_id,
    "members": list(c.members.keys()),
    "quorum_ratio": c.quorum_ratio,
    "max_epsilon": c.max_epsilon,
}))
"""
    res_a = _run_worker(worker_a_code, env_vars=env)
    assert res_a["success"], f"Worker A failed: {res_a['stderr']}"
    assert res_a["data"]["consortium_id"] == "consortium_cross_01"

    worker_b_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService

svc = ConsortiumGovernanceService()
c = svc.get_consortium("consortium_cross_01")
if not c:
    print(json.dumps({"found": False}))
else:
    print(json.dumps({
        "found": True,
        "consortium_id": c.consortium_id,
        "name": c.name,
        "founder": c.founder_bank_id,
        "members": list(c.members.keys()),
        "quorum_ratio": c.quorum_ratio,
        "max_epsilon": c.max_epsilon,
        "status": c.status.value,
    }))
"""
    res_b = _run_worker(worker_b_code, env_vars=env)
    assert res_b["success"], f"Worker B failed: {res_b['stderr']}"
    data_b = res_b["data"]
    assert data_b["found"] is True
    assert data_b["consortium_id"] == "consortium_cross_01"
    assert data_b["founder"] == "bank_a"
    assert data_b["members"] == ["bank_a"]
    assert data_b["quorum_ratio"] == 0.6
    assert data_b["max_epsilon"] == 4.5
    assert data_b["status"] == "ACTIVE"


# ── CONSIST-02: Cross-Worker Member Eviction & Governance Rejection ─────────


def test_consist_02_worker_a_evicts_worker_b_rejects(isolated_consortium_env):
    """CONSIST-02: Worker A removes Bank B; Worker B rejects Bank B's next governance action."""
    env = isolated_consortium_env["env"]

    setup_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import ProposalAction
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_a = auth_svc.get_user("admin_a") or auth_svc.register_user("admin_a", "Pass123!", "bank_a", roles=["admin"])
u_b = auth_svc.get_user("admin_b") or auth_svc.register_user("admin_b", "Pass123!", "bank_b", roles=["admin"])
token_a = auth_svc.create_token_bundle(u_a).access_token
token_b = auth_svc.create_token_bundle(u_b).access_token

svc = ConsortiumGovernanceService()
c = svc.create_consortium(
    consortium_id="consortium_cross_02",
    name="Alliance 02",
    founder_bank_id="bank_a",
    quorum_ratio=0.51,
    auth_token=token_a,
)
p_add = svc.propose_membership_change(
    consortium_id="consortium_cross_02",
    creator_bank_id="bank_a",
    target_bank_id="bank_b",
    action=ProposalAction.ADD_MEMBER,
    auth_token=token_a,
)
print(json.dumps({
    "consortium_id": c.consortium_id,
    "members_after_add": list(svc.get_consortium("consortium_cross_02").members.keys()),
}))
"""
    res_setup = _run_worker(setup_code, env_vars=env)
    assert res_setup["success"], f"Setup failed: {res_setup['stderr']}"
    assert "bank_b" in res_setup["data"]["members_after_add"]

    # Worker A removes Bank B via valid proposal and voting
    worker_a_eviction = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import ProposalAction
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_a = auth_svc.get_user("admin_a")
token_a = auth_svc.create_token_bundle(u_a).access_token

svc = ConsortiumGovernanceService()
svc.set_member_voting_power("consortium_cross_02", "bank_a", 2.0)

p_evict = svc.propose_membership_change(
    consortium_id="consortium_cross_02",
    creator_bank_id="bank_a",
    target_bank_id="bank_b",
    action=ProposalAction.REMOVE_MEMBER,
    auth_token=token_a,
)
c = svc.get_consortium("consortium_cross_02")
print(json.dumps({
    "evict_proposal_status": p_evict.status.value,
    "bank_b_still_member": "bank_b" in c.members,
    "bank_b_in_removed": "bank_b" in c.removed_members,
}))
"""
    res_evict = _run_worker(worker_a_eviction, env_vars=env)
    assert res_evict["success"], f"Worker A eviction failed: {res_evict['stderr']}"
    assert res_evict["data"]["evict_proposal_status"] == "APPROVED"
    assert res_evict["data"]["bank_b_still_member"] is False
    assert res_evict["data"]["bank_b_in_removed"] is True

    # Worker B attempts a governance action as Bank B
    worker_b_action = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_b = auth_svc.get_user("admin_b")
token_b = auth_svc.create_token_bundle(u_b).access_token

svc = ConsortiumGovernanceService()
rejected = False
error_msg = ""
try:
    svc.propose_policy_update(
        consortium_id="consortium_cross_02",
        creator_bank_id="bank_b",
        policy_updates={"new_max_epsilon": 2.0},
        auth_token=token_b,
    )
except Exception as e:
    rejected = True
    error_msg = str(e)

print(json.dumps({
    "rejected": rejected,
    "error_msg": error_msg,
}))
"""
    res_b = _run_worker(worker_b_action, env_vars=env)
    assert res_b["success"], f"Worker B failed: {res_b['stderr']}"
    assert res_b["data"]["rejected"] is True
    assert "not a member" in res_b["data"]["error_msg"].lower()


# ── CONSIST-03: Cross-Worker Model Governance Rejection for Evicted Member ──


def test_consist_03_worker_b_rejects_removed_member_model_governance(isolated_consortium_env):
    """CONSIST-03: Worker B rejects removed Bank B's subsequent consortium-model approval or promotion."""
    env = isolated_consortium_env["env"]

    worker_a_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import ProposalAction
from app.application.services.model_registry import ModelRegistry, create_trusted_internal_context
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_a = auth_svc.get_user("admin_a") or auth_svc.register_user("admin_a", "Pass123!", "bank_a", roles=["admin"])
u_b = auth_svc.get_user("admin_b") or auth_svc.register_user("admin_b", "Pass123!", "bank_b", roles=["admin"])
token_a = auth_svc.create_token_bundle(u_a).access_token

svc = ConsortiumGovernanceService()
c = svc.create_consortium(
    consortium_id="consortium_cross_03",
    name="Alliance 03",
    founder_bank_id="bank_a",
    quorum_ratio=0.51,
    auth_token=token_a,
)
# Add bank_b
svc.propose_membership_change(
    consortium_id="consortium_cross_03",
    creator_bank_id="bank_a",
    target_bank_id="bank_b",
    action=ProposalAction.ADD_MEMBER,
    auth_token=token_a,
)

# Bind model to consortium_cross_03
reg = ModelRegistry()
ctx_bind = create_trusted_internal_context("training_orchestrator", "bind_ownership")
reg.bind_simulation_ownership(
    simulation_id="sim_consortium_03",
    consortium_id="consortium_cross_03",
    trusted_context=ctx_bind,
)

# Evict bank_b (founder power 2.0)
svc.set_member_voting_power("consortium_cross_03", "bank_a", 2.0)
svc.propose_membership_change(
    consortium_id="consortium_cross_03",
    creator_bank_id="bank_a",
    target_bank_id="bank_b",
    action=ProposalAction.REMOVE_MEMBER,
    auth_token=token_a,
)
print(json.dumps({"status": "ready"}))
"""
    res_a = _run_worker(worker_a_code, env_vars=env)
    assert res_a["success"], f"Worker A failed: {res_a['stderr']}"

    # Worker B tries to exercise institutional governance over the consortium model
    worker_b_code = """
import json
from app.application.services.model_registry import ModelRegistry, ModelAuthorizationError
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_b = auth_svc.get_user("admin_b")

reg = ModelRegistry()
rejected = False
error_msg = ""
try:
    reg._verify_institutional_governance_authority(
        simulation_id="sim_consortium_03",
        user_rec=u_b,
        action_name="model promotion",
    )
except ModelAuthorizationError as e:
    rejected = True
    error_msg = str(e)

print(json.dumps({
    "rejected": rejected,
    "error_msg": error_msg,
}))
"""
    res_b = _run_worker(worker_b_code, env_vars=env)
    assert res_b["success"], f"Worker B failed: {res_b['stderr']}"
    assert res_b["data"]["rejected"] is True
    assert "not a member of consortium" in res_b["data"]["error_msg"]


# ── CONSIST-04: Worker Restart Preserves State & Removals ───────────────────


def test_consist_04_worker_restart_preserves_state(isolated_consortium_env):
    """CONSIST-04: Worker restart preserves membership removals, proposals, votes, and policies."""
    env = isolated_consortium_env["env"]

    worker_a_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import ProposalAction
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_a = auth_svc.get_user("admin_a") or auth_svc.register_user("admin_a", "Pass123!", "bank_a", roles=["admin"])
u_b = auth_svc.get_user("admin_b") or auth_svc.register_user("admin_b", "Pass123!", "bank_b", roles=["admin"])
token_a = auth_svc.create_token_bundle(u_a).access_token
token_b = auth_svc.create_token_bundle(u_b).access_token

svc = ConsortiumGovernanceService()
svc.create_consortium(
    consortium_id="consortium_restart_04",
    name="Restart Preservation Net",
    founder_bank_id="bank_a",
    quorum_ratio=0.51,
    max_epsilon=5.0,
    auth_token=token_a,
)
svc.propose_membership_change(
    consortium_id="consortium_restart_04",
    creator_bank_id="bank_a",
    target_bank_id="bank_b",
    action=ProposalAction.ADD_MEMBER,
    auth_token=token_a,
)
p_pol = svc.propose_policy_update(
    consortium_id="consortium_restart_04",
    creator_bank_id="bank_a",
    policy_updates={"new_max_epsilon": 3.8},
    auth_token=token_a,
)
svc.cast_vote(p_pol.proposal_id, "bank_b", approve=True, auth_token=token_b)

svc.set_member_voting_power("consortium_restart_04", "bank_a", 2.0)
p_evict = svc.propose_membership_change(
    consortium_id="consortium_restart_04",
    creator_bank_id="bank_a",
    target_bank_id="bank_b",
    action=ProposalAction.REMOVE_MEMBER,
    auth_token=token_a,
)

print(json.dumps({
    "policy_proposal_id": p_pol.proposal_id,
    "evict_proposal_id": p_evict.proposal_id,
}))
"""
    res_a = _run_worker(worker_a_code, env_vars=env)
    assert res_a["success"], f"Worker A failed: {res_a['stderr']}"
    p_pol_id = res_a["data"]["policy_proposal_id"]
    p_evict_id = res_a["data"]["evict_proposal_id"]

    # Fresh Worker Process (Simulating complete restart)
    worker_restart_code = f"""
import json
from app.application.services.consortium_service import ConsortiumGovernanceService

svc = ConsortiumGovernanceService()
c = svc.get_consortium("consortium_restart_04")
p_pol = svc.get_proposal("{p_pol_id}")
p_evict = svc.get_proposal("{p_evict_id}")

print(json.dumps({{
    "consortium_name": c.name,
    "max_epsilon": c.max_epsilon,
    "members": list(c.members.keys()),
    "removed_members": list(c.removed_members.keys()),
    "policy_status": p_pol.status.value,
    "policy_votes_for": sorted(list(p_pol.votes_for)),
    "evict_status": p_evict.status.value,
    "evict_votes_for": sorted(list(p_evict.votes_for)),
}}))
"""
    res_restart = _run_worker(worker_restart_code, env_vars=env)
    assert res_restart["success"], f"Worker restart failed: {res_restart['stderr']}"
    data = res_restart["data"]
    assert data["consortium_name"] == "Restart Preservation Net"
    assert data["max_epsilon"] == 3.8
    assert data["members"] == ["bank_a"]
    assert "bank_b" in data["removed_members"]
    assert data["policy_status"] == "APPROVED"
    assert "bank_b" in data["policy_votes_for"]
    assert data["evict_status"] == "APPROVED"
    assert "bank_a" in data["evict_votes_for"]


# ── CONSIST-05: Concurrent Cross-Worker Voting Integrity ────────────────────


def test_consist_05_concurrent_cross_worker_voting(isolated_consortium_env):
    """CONSIST-05: Concurrent voting across workers does not lose updates or double-count."""
    import concurrent.futures

    env = isolated_consortium_env["env"]

    setup_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import ProposalAction, MemberRole
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_a = auth_svc.get_user("admin_a") or auth_svc.register_user("admin_a", "Pass123!", "bank_a", roles=["admin"])
u_b = auth_svc.get_user("admin_b") or auth_svc.register_user("admin_b", "Pass123!", "bank_b", roles=["admin"])
u_c = auth_svc.get_user("admin_c") or auth_svc.register_user("admin_c", "Pass123!", "bank_c", roles=["admin"])
u_d = auth_svc.get_user("admin_d") or auth_svc.register_user("admin_d", "Pass123!", "bank_d", roles=["admin"])
token_a = auth_svc.create_token_bundle(u_a).access_token

svc = ConsortiumGovernanceService()
c = svc.create_consortium(
    consortium_id="consortium_race_05",
    name="Race Test Net",
    founder_bank_id="bank_a",
    quorum_ratio=0.75,
    auth_token=token_a,
)
svc.add_member("consortium_race_05", "bank_b", role=MemberRole.FULL_MEMBER, voting_power=1.0)
svc.add_member("consortium_race_05", "bank_c", role=MemberRole.FULL_MEMBER, voting_power=1.0)
svc.add_member("consortium_race_05", "bank_d", role=MemberRole.FULL_MEMBER, voting_power=1.0)

prop = svc.propose_policy_update(
    consortium_id="consortium_race_05",
    creator_bank_id="bank_a",
    policy_updates={"new_max_epsilon": 2.5},
    auth_token=token_a,
)
print(json.dumps({"proposal_id": prop.proposal_id}))
"""
    res_setup = _run_worker(setup_code, env_vars=env)
    assert res_setup["success"], f"Setup failed: {res_setup['stderr']}"
    prop_id = res_setup["data"]["proposal_id"]

    def cast_vote_worker(bank_name: str, vote_for: bool):
        worker_code = f"""
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
user = auth_svc.get_user("admin_{bank_name.split('_')[-1]}")
token = auth_svc.create_token_bundle(user).access_token

svc = ConsortiumGovernanceService()
p = svc.cast_vote(
    proposal_id="{prop_id}",
    bank_id="{bank_name}",
    approve={vote_for},
    auth_token=token,
)
print(json.dumps({{"status": p.status.value, "votes_for": list(p.votes_for), "votes_against": list(p.votes_against)}}))
"""
        return _run_worker(worker_code, env_vars=env)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_b = executor.submit(cast_vote_worker, "bank_b", True)
        f_c = executor.submit(cast_vote_worker, "bank_c", False)
        res_b = f_b.result()
        res_c = f_c.result()

    assert res_b["success"], f"Vote B failed: {res_b['stderr']}"
    assert res_c["success"], f"Vote C failed: {res_c['stderr']}"

    verify_code = f"""
import json
from app.application.services.consortium_service import ConsortiumGovernanceService

svc = ConsortiumGovernanceService()
p = svc.get_proposal("{prop_id}")
print(json.dumps({{
    "status": p.status.value,
    "votes_for": sorted(list(p.votes_for)),
    "votes_against": sorted(list(p.votes_against)),
}}))
"""
    res_final = _run_worker(verify_code, env_vars=env)
    assert res_final["success"], f"Final verify failed: {res_final['stderr']}"
    final_data = res_final["data"]
    assert "bank_b" in final_data["votes_for"]
    assert "bank_c" in final_data["votes_against"]
    assert final_data["status"] == "PENDING"


# ── CONSIST-06: Corrupted Production Authority Fails Closed ─────────────────


def test_consist_06_corrupted_production_authority_fails_closed(isolated_consortium_env):
    """CONSIST-06: Corrupted production consortium store strictly fails closed."""
    c_store_path = isolated_consortium_env["c_store_path"]

    # Write corrupt data
    with open(c_store_path, "w", encoding="utf-8") as f:
        f.write('{"version": 1, "data": {"consortia": "CORRUPTED_NOT_DICT"}, "sha256": "invalid"}')

    env = isolated_consortium_env["env"].copy()
    env["APP_ENV"] = "production"
    env["CFI_JWT_SIGNING_SECRET"] = "production_super_secure_signing_secret_jwt_key_32chars!"
    env["CFI_IDENTITY_STORE_SECRET"] = "production_super_secure_identity_authority_secret_32chars!"

    worker_code = """
import json
failed_closed = False
err_type = ""
try:
    from app.application.services.consortium_service import ConsortiumGovernanceService
    svc = ConsortiumGovernanceService()
    svc.get_consortium("cfi-consortium")
except Exception as e:
    if "ConsortiumSecurityError" in type(e).__name__:
        failed_closed = True
        err_type = type(e).__name__
    else:
        err_type = f"{type(e).__name__}: {e}"

print(json.dumps({"failed_closed": failed_closed, "err_type": err_type}))
"""
    res = _run_worker(worker_code, env_vars=env)
    assert res["success"], f"Worker failed unexpectedly: {res['stderr']}"
    assert res["data"]["failed_closed"] is True
    assert res["data"]["err_type"] == "ConsortiumSecurityError"


# ── CONSIST-07: Bootstrap Idempotency Prevents Expelled Resurrection ────────


def test_consist_07_bootstrap_prevents_expelled_member_resurrection(isolated_consortium_env):
    """CONSIST-07: Default consortium bootstrap cannot readmit an expelled member."""
    env = isolated_consortium_env["env"]

    step1_code = """
import json
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    init_default_consortium,
)
from app.domain.consortium_governance import ProposalAction
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_a = auth_svc.get_user("admin_a") or auth_svc.register_user("admin_a", "Pass123!", "bank_a", roles=["admin"])
u_b = auth_svc.get_user("admin_b") or auth_svc.register_user("admin_b", "Pass123!", "bank_b", roles=["admin"])
token_a = auth_svc.create_token_bundle(u_a).access_token
token_b = auth_svc.create_token_bundle(u_b).access_token

c = init_default_consortium()
svc = ConsortiumGovernanceService()

p = svc.propose_membership_change(
    consortium_id="cfi-consortium",
    creator_bank_id="bank_a",
    target_bank_id="bank_c",
    action=ProposalAction.REMOVE_MEMBER,
    auth_token=token_a,
)
svc.cast_vote(p.proposal_id, "bank_b", approve=True, auth_token=token_b)

c_after = svc.get_consortium("cfi-consortium")
print(json.dumps({
    "evict_status": p.status.value,
    "members_after_evict": list(c_after.members.keys()),
    "removed_members": list(c_after.removed_members.keys()),
}))
"""
    res_step1 = _run_worker(step1_code, env_vars=env)
    assert res_step1["success"], f"Step 1 failed: {res_step1['stderr']}"
    assert "bank_c" not in res_step1["data"]["members_after_evict"]
    assert "bank_c" in res_step1["data"]["removed_members"]

    step2_code = """
import json
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
    init_default_consortium,
)

c = init_default_consortium()
svc = ConsortiumGovernanceService()
stored_c = svc.get_consortium("cfi-consortium")

print(json.dumps({
    "members": list(stored_c.members.keys()),
    "removed_members": list(stored_c.removed_members.keys()),
    "has_bank_c": "bank_c" in stored_c.members,
}))
"""
    res_step2 = _run_worker(step2_code, env_vars=env)
    assert res_step2["success"], f"Step 2 failed: {res_step2['stderr']}"
    data2 = res_step2["data"]
    assert data2["has_bank_c"] is False
    assert "bank_c" not in data2["members"]
    assert "bank_c" in data2["removed_members"]


# ── CONSIST-08: Direct Membership Mutation Bypasses Rejected ────────────────


def test_consist_08_direct_membership_mutation_bypasses_rejected(isolated_consortium_env):
    """CONSIST-08: Direct membership mutation cannot bypass production authorization or readmit expelled members."""
    env = isolated_consortium_env["env"].copy()
    env["APP_ENV"] = "production"
    env["CFI_JWT_SIGNING_SECRET"] = "production_super_secure_signing_secret_jwt_key_32chars!"
    env["CFI_IDENTITY_STORE_SECRET"] = "production_super_secure_identity_authority_secret_32chars!"

    worker_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import MemberRole, ConsortiumSecurityError
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_a = auth_svc.get_user("admin_a") or auth_svc.register_user("admin_a", "Pass123!", "bank_a", roles=["admin"])
token_a = auth_svc.create_token_bundle(u_a).access_token

svc = ConsortiumGovernanceService()
c = svc.create_consortium("c_bypass_08", "Bypass Test", "bank_a", auth_token=token_a)

# Test 1: Direct add_member without authentication in production mode
unauth_rejected = False
try:
    svc.add_member("c_bypass_08", "bank_b", MemberRole.FULL_MEMBER, auth_token=None)
except PermissionError:
    unauth_rejected = True

# Test 2: Direct add_member with authentication
svc.add_member("c_bypass_08", "bank_b", MemberRole.FULL_MEMBER, auth_token=token_a)

# Simulate bank_b eviction record in removed_members
def _evict(consortia, proposals):
    consortia["c_bypass_08"].members.pop("bank_b", None)
    consortia["c_bypass_08"].removed_members["bank_b"] = {"reason": "expelled"}
svc._store.atomic_mutate(_evict)

# Test 3: Attempting to directly re-add expelled bank_b fails closed
tombstone_rejected = False
try:
    svc.add_member("c_bypass_08", "bank_b", MemberRole.FULL_MEMBER, auth_token=token_a)
except (PermissionError, ConsortiumSecurityError):
    tombstone_rejected = True

print(json.dumps({
    "unauth_rejected": unauth_rejected,
    "tombstone_rejected": tombstone_rejected,
}))
"""
    res = _run_worker(worker_code, env_vars=env)
    assert res["success"], f"Worker failed: {res['stderr']}"
    assert res["data"]["unauth_rejected"] is True
    assert res["data"]["tombstone_rejected"] is True


# ── CONSIST-09: Positive Multi-Bank Governance & Lifecycle ──────────────────


def test_consist_09_legitimate_multi_bank_lifecycle_succeeds(isolated_consortium_env):
    """CONSIST-09: Legitimate Bank A + Bank B governance, independent voting, and model lifecycle succeed."""
    env = isolated_consortium_env["env"]

    worker_code = """
import json
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.domain.consortium_governance import ProposalAction, ProposalStatus
from app.application.services.model_registry import ModelRegistry, create_trusted_internal_context
from app.infrastructure.security.auth_service import AuthenticationService

auth_svc = AuthenticationService.get_instance()
u_a = auth_svc.get_user("admin_a") or auth_svc.register_user("admin_a", "Pass123!", "bank_a", roles=["admin"])
u_b = auth_svc.get_user("admin_b") or auth_svc.register_user("admin_b", "Pass123!", "bank_b", roles=["admin"])
token_a = auth_svc.create_token_bundle(u_a).access_token
token_b = auth_svc.create_token_bundle(u_b).access_token

svc = ConsortiumGovernanceService()
c = svc.create_consortium(
    consortium_id="consortium_life_09",
    name="Lifecycle Alliance",
    founder_bank_id="bank_a",
    quorum_ratio=0.51,
    max_epsilon=5.0,
    auth_token=token_a,
)

p_b = svc.propose_membership_change(
    consortium_id="consortium_life_09",
    creator_bank_id="bank_a",
    target_bank_id="bank_b",
    action=ProposalAction.ADD_MEMBER,
    auth_token=token_a,
)
assert p_b.status == ProposalStatus.APPROVED

p_pol = svc.propose_policy_update(
    consortium_id="consortium_life_09",
    creator_bank_id="bank_a",
    policy_updates={"new_max_epsilon": 3.0},
    auth_token=token_a,
)
svc.cast_vote(p_pol.proposal_id, "bank_b", approve=True, auth_token=token_b)
assert p_pol.status == ProposalStatus.APPROVED

c_updated = svc.get_consortium("consortium_life_09")
assert c_updated.max_epsilon == 3.0

reg = ModelRegistry()
ctx_bind = create_trusted_internal_context("training_orchestrator", "bind_ownership")
reg.bind_simulation_ownership(
    simulation_id="sim_consortium_09",
    consortium_id="consortium_life_09",
    trusted_context=ctx_bind,
)

reg._verify_institutional_governance_authority(
    simulation_id="sim_consortium_09",
    user_rec=u_b,
    action_name="model promotion",
)

print(json.dumps({
    "success": True,
    "final_epsilon": c_updated.max_epsilon,
    "members": list(c_updated.members.keys()),
}))
"""
    res = _run_worker(worker_code, env_vars=env)
    assert res["success"], f"Worker failed: {res['stderr']}"
    assert res["data"]["success"] is True
    assert res["data"]["final_epsilon"] == 3.0
    assert sorted(res["data"]["members"]) == ["bank_a", "bank_b"]


# ── CONSIST-10: Tenant & RBAC Authorization Boundary Regressions Intact ─────


def test_consist_10_regressions_intact(isolated_consortium_env):
    """CONSIST-10: Verifies that tenant authority boundaries, credentials, and claim validations remain strictly intact."""
    from app.application.services.consortium_service import ConsortiumGovernanceService
    from app.infrastructure.security.auth_service import AuthenticationService

    auth_svc = AuthenticationService.get_instance()
    u_a = auth_svc.get_user("admin_a") or auth_svc.register_user("admin_a", "Pass123!", "bank_a", roles=["admin"])
    token_a = auth_svc.create_token_bundle(u_a).access_token

    svc = ConsortiumGovernanceService()
    with pytest.raises(PermissionError, match="Authentication required"):
        svc.create_consortium("c_reg_10", "Regression Net", "bank_a", auth_token=None)

    with pytest.raises(PermissionError, match="Actor 'bank_b' does not match authenticated token principal"):
        svc.create_consortium("c_reg_10", "Regression Net", "bank_a", auth_token=token_a, actor="bank_b")

    c = svc.create_consortium("c_reg_10", "Regression Net", "bank_a", auth_token=token_a)
    assert c.consortium_id == "c_reg_10"
