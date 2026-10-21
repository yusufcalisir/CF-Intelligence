"""Unit and Security Tests for Orphaned Shadow Metrics Authorization Boundary Hardening.

Verifies Requirements A through E across all registered aliases:
- /api/v1/models/{simulation_id}/shadow/metrics
- /v1/models/{simulation_id}/shadow/metrics
- /api/v1/registry/{simulation_id}/shadow/metrics
- /v1/registry/{simulation_id}/shadow/metrics

Test Scenarios:
A. Bank B has historical metrics but its simulation directory is missing. Bank A cannot read those metrics.
B. Bank B retains authorized access only if authoritative ownership can still be verified.
C. Unknown simulation IDs do not bypass authorization (fail closed 404/401).
D. Existing authorized shadow metrics requests still succeed (owner, consortium, privileged auditor).
E. Existing 01-H security tests remain passing.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.consortium_governance import (
    Consortium,
    ConsortiumMember,
    ConsortiumStatus,
    MemberRole,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.main import app
from app.presentation.routers.model_registry import _eval_engine, registry

SHADOW_METRICS_ALIASES = [
    "/api/v1/models/{simulation_id}/shadow/metrics",
    "/v1/models/{simulation_id}/shadow/metrics",
    "/api/v1/registry/{simulation_id}/shadow/metrics",
    "/v1/registry/{simulation_id}/shadow/metrics",
]


@pytest.fixture
def test_env():
    temp_dir = tempfile.mkdtemp()
    old_storage = os.environ.get("CFI_STORAGE_DIR")
    os.environ["CFI_STORAGE_DIR"] = temp_dir

    old_reg_storage = registry.storage_dir
    old_reg_root = registry.registry_root

    registry.storage_dir = temp_dir
    registry.registry_root = os.path.join(temp_dir, "registry")
    os.makedirs(registry.registry_root, exist_ok=True)

    c_store = DurableConsortiumStore(
        store_path=os.path.join(temp_dir, "consortium_store.json"),
        in_memory=False,
        authority_secret="cfi_test_authority_secret_2026_super_secure",
        app_env="development",
    )
    old_c_store = DurableConsortiumStore._instance
    DurableConsortiumStore.set_instance(c_store)

    yield temp_dir

    DurableConsortiumStore.set_instance(old_c_store)
    registry.storage_dir = old_reg_storage
    registry.registry_root = old_reg_root

    if old_storage is not None:
        os.environ["CFI_STORAGE_DIR"] = old_storage
    else:
        os.environ.pop("CFI_STORAGE_DIR", None)

    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_tokens(test_env) -> dict[str, str]:
    auth_svc = AuthenticationService.get_instance()
    user_a = auth_svc.register_user("analyst_a", "Password123!", "bank_a", ["analyst"])
    token_a = auth_svc.create_token_bundle(user_a).access_token

    user_b = auth_svc.register_user("analyst_b", "Password123!", "bank_b", ["analyst"])
    token_b = auth_svc.create_token_bundle(user_b).access_token

    user_c = auth_svc.register_user("analyst_c", "Password123!", "bank_c", ["analyst"])
    token_c = auth_svc.create_token_bundle(user_c).access_token

    user_auditor = auth_svc.register_user(
        "auditor_user", "Password123!", "global", ["compliance_auditor"]
    )
    token_auditor = auth_svc.create_token_bundle(user_auditor).access_token

    return {
        "bank_a": token_a,
        "bank_b": token_b,
        "bank_c": token_c,
        "auditor": token_auditor,
    }


def _seed_shadow_metrics_in_engine(simulation_id: str, sample_count: int = 150) -> dict[str, Any]:
    metrics = {
        "champion_version": 1,
        "champion_auc": 0.945,
        "champion_pr_auc": 0.892,
        "champion_fpr": 0.02,
        "champion_latency_ms": 11.5,
        "challenger_version": 2,
        "challenger_auc": 0.961,
        "challenger_pr_auc": 0.915,
        "challenger_fpr": 0.015,
        "challenger_latency_ms": 12.0,
        "paired_champion_pr_auc": 0.892,
        "paired_sample_count": sample_count,
        "traffic_share": 0.25,
        "sample_count": sample_count,
    }
    _eval_engine._store.set(f"{simulation_id}:shadow_metrics", metrics)
    return metrics


def _save_historical_ownership_file(
    storage_dir: str, simulation_id: str, owning_bank_id: str | None, consortium_id: str | None = None
) -> str:
    hist_dir = os.path.join(storage_dir, "historical_ownership")
    os.makedirs(hist_dir, exist_ok=True)
    fpath = os.path.join(hist_dir, f"{simulation_id}.json")
    record = {
        "simulation_id": simulation_id,
        "owning_bank_id": owning_bank_id,
        "consortium_id": consortium_id,
        "bound_at": "2026-01-01T00:00:00Z",
        "bound_by": "test_archive",
        "is_unbound": not owning_bank_id and not consortium_id,
    }
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return fpath


# ==============================================================================
# TEST A: Bank B has historical metrics, simulation dir missing. Bank A rejected.
# ==============================================================================
def test_a_bank_b_historical_metrics_missing_dir_bank_a_cannot_read(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
):
    sim_id = "sim_bank_b_historic_001"
    # 1. Historical metrics exist in evaluation engine store
    _seed_shadow_metrics_in_engine(sim_id, sample_count=200)

    # 2. Simulation directory in registry does NOT exist
    sim_dir = registry._get_sim_dir(sim_id, create=False)
    assert not os.path.isdir(sim_dir)

    # 3. Authoritative historical ownership record confirms Bank B owns the simulation
    _save_historical_ownership_file(test_env, sim_id, owning_bank_id="bank_b")

    # 4. Bank A requests metrics across all 4 aliases -> rejected with 403 Forbidden
    headers_a = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}
    for alias_pattern in SHADOW_METRICS_ALIASES:
        url = alias_pattern.format(simulation_id=sim_id)
        resp = client.get(url, headers=headers_a)
        assert resp.status_code == 403, f"Expected 403 for Bank A on {url}, got {resp.status_code}"
        assert "not authorized" in resp.text.lower()


# ==============================================================================
# TEST B: Bank B retains authorized access only if authoritative ownership verified.
# ==============================================================================
def test_b_bank_b_retains_access_only_if_authoritative_ownership_verified(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
):
    sim_id = "sim_bank_b_historic_002"
    _seed_shadow_metrics_in_engine(sim_id, sample_count=350)

    sim_dir = registry._get_sim_dir(sim_id, create=False)
    assert not os.path.isdir(sim_dir)

    # Step 1: With separate authoritative historical ownership record present
    hist_fpath = _save_historical_ownership_file(test_env, sim_id, owning_bank_id="bank_b")

    headers_b = {"Authorization": f"Bearer {auth_tokens['bank_b']}"}
    for alias_pattern in SHADOW_METRICS_ALIASES:
        url = alias_pattern.format(simulation_id=sim_id)
        resp = client.get(url, headers=headers_b)
        assert resp.status_code == 200, f"Expected 200 for Bank B on {url}, got {resp.status_code}"
        data = resp.json()
        assert data["sample_count"] == 350
        assert data["champion_auc"] == 0.945

    # Step 2: Remove the authoritative ownership record -> Bank B's access fails closed
    os.remove(hist_fpath)
    for alias_pattern in SHADOW_METRICS_ALIASES:
        url = alias_pattern.format(simulation_id=sim_id)
        resp = client.get(url, headers=headers_b)
        assert resp.status_code == 404, f"Expected 404 for unverified ownership on {url}, got {resp.status_code}"


# ==============================================================================
# TEST C: Unknown simulation IDs do not bypass authorization.
# ==============================================================================
def test_c_unknown_simulation_ids_do_not_bypass_authorization(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
):
    unknown_sim_id = "sim_completely_unknown_9999"
    headers_a = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}
    headers_b = {"Authorization": f"Bearer {auth_tokens['bank_b']}"}

    for alias_pattern in SHADOW_METRICS_ALIASES:
        url = alias_pattern.format(simulation_id=unknown_sim_id)
        # Authenticated Bank A -> 404
        resp_a = client.get(url, headers=headers_a)
        assert resp_a.status_code == 404, f"Expected 404 on {url}, got {resp_a.status_code}"

        # Authenticated Bank B -> 404
        resp_b = client.get(url, headers=headers_b)
        assert resp_b.status_code == 404, f"Expected 404 on {url}, got {resp_b.status_code}"

        # Anonymous caller in development mode -> 404 (does not return mock or unbound success)
        resp_anon = client.get(url)
        assert resp_anon.status_code in (401, 404), f"Expected 401 or 404 for anon on {url}, got {resp_anon.status_code}"


# ==============================================================================
# TEST D: Existing authorized shadow metrics requests still succeed.
# ==============================================================================
def test_d_existing_authorized_shadow_metrics_succeed(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
):
    sim_id = "sim_existing_active_001"
    # Seed active model directory and ownership in registry
    registry._get_sim_dir(sim_id, create=True)
    registry._save_ownership(
        sim_id,
        {
            "simulation_id": sim_id,
            "owning_bank_id": "bank_a",
            "consortium_id": None,
            "bound_at": "2026-01-01T00:00:00Z",
            "bound_by": "test_seed",
            "is_unbound": False,
        },
    )
    _seed_shadow_metrics_in_engine(sim_id, sample_count=500)

    headers_a = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}
    headers_b = {"Authorization": f"Bearer {auth_tokens['bank_b']}"}
    headers_auditor = {"Authorization": f"Bearer {auth_tokens['auditor']}"}

    for alias_pattern in SHADOW_METRICS_ALIASES:
        url = alias_pattern.format(simulation_id=sim_id)
        # 1. Owner Bank A -> 200
        resp_a = client.get(url, headers=headers_a)
        assert resp_a.status_code == 200, f"Expected 200 on {url}, got {resp_a.status_code}"
        assert resp_a.json()["sample_count"] == 500

        # 2. Non-owner Bank B -> 403
        resp_b = client.get(url, headers=headers_b)
        assert resp_b.status_code == 403, f"Expected 403 on {url}, got {resp_b.status_code}"

        # 3. Privileged Compliance Auditor -> 200
        resp_aud = client.get(url, headers=headers_auditor)
        assert resp_aud.status_code == 200, f"Expected 200 on {url}, got {resp_aud.status_code}"
        assert resp_aud.json()["sample_count"] == 500


# ==============================================================================
# TEST E: Consortium models authorize active members and reject revoked members.
# ==============================================================================
def test_e_consortium_shadow_metrics_governance(
    client: TestClient,
    test_env: str,
    auth_tokens: dict[str, str],
):
    c_store = DurableConsortiumStore.get_instance()
    consortium = Consortium(
        consortium_id="consortium_shadow_01",
        name="Shadow Metrics Consortium",
        status=ConsortiumStatus.ACTIVE,
        members={
            "bank_a": ConsortiumMember(bank_id="bank_a", role=MemberRole.FULL_MEMBER),
            "bank_b": ConsortiumMember(bank_id="bank_b", role=MemberRole.FULL_MEMBER),
        },
    )

    def _add_c(consortia: dict[str, Any], proposals: dict[str, Any]) -> None:
        consortia[consortium.consortium_id] = consortium

    c_store.atomic_mutate(_add_c)

    sim_id = "sim_consortium_shadow_001"
    registry._get_sim_dir(sim_id, create=True)
    registry._save_ownership(
        sim_id,
        {
            "simulation_id": sim_id,
            "owning_bank_id": None,
            "consortium_id": "consortium_shadow_01",
            "bound_at": "2026-01-01T00:00:00Z",
            "bound_by": "test_seed",
            "is_unbound": False,
        },
    )
    _seed_shadow_metrics_in_engine(sim_id, sample_count=777)

    headers_a = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}
    headers_b = {"Authorization": f"Bearer {auth_tokens['bank_b']}"}
    headers_c = {"Authorization": f"Bearer {auth_tokens['bank_c']}"}

    url = f"/api/v1/models/{sim_id}/shadow/metrics"

    # Both active members have access
    assert client.get(url, headers=headers_a).status_code == 200
    assert client.get(url, headers=headers_b).status_code == 200

    # Non-member Bank C rejected
    assert client.get(url, headers=headers_c).status_code == 403

    # Revoke Bank B from consortium
    def _revoke_b(consortia: dict[str, Any], proposals: dict[str, Any]) -> None:
        c = consortia.get("consortium_shadow_01")
        if c:
            c.members.pop("bank_b", None)
            c.removed_members["bank_b"] = {"revoked_at": "2026-01-01T00:00:00Z", "reason": "test"}

    c_store.atomic_mutate(_revoke_b)

    # Bank B now rejected
    assert client.get(url, headers=headers_b).status_code == 403
