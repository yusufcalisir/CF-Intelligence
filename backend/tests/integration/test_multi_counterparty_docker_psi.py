"""Integration tests for Multi-Counterparty PSI Query Orchestration over Genuine Three-Bank Docker Topology (Module M6.3).

Verifies:
1. Authenticated Bank A analyst executes an authorized multi-counterparty OpenMined PSI query
   against both Bank B and Bank C within a single API / service request.
2. Independent pairwise executions over isolated Docker containers with real OpenMined PSI:
   - Bank B (holding matching target record) yields verified match_confirmed=True.
   - Bank C (holding distinct clean record) yields verified match_confirmed=False.
   - No fabricated joint intersection; distinct separately identifiable signals per counterpart.
3. Each execution ticket binds the authentic bilateral operational grant (grant_m63_ab and grant_m63_ac),
   never a broader legacy grant or manufactured identifier.
4. Pre-flight pairwise governance validation fails closed: if any target's bilateral grant is revoked,
   the entire query request is rejected before any dispatch to workers occurs.
5. FastAPI HTTP API router endpoint (/intelligence/query-multi) enforces authentication,
   tenant isolation, schema constraints, and error mapping.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.schemas.cross_bank_intelligence import (
    MultiCounterpartyIntelligenceQueryRequest,
    MultiCounterpartyIntelligenceQueryResponse,
)
from app.application.services.consortium_service import (
    ConsortiumGovernanceService,
)
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import _entity_to_dict
from app.domain.consortium_governance import ConsortiumSecurityError, MemberRole
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.main import app
from tests.integration.conftest import (
    SHARED_CONSORTIUM_STORE_SECRET,
    SHARED_JWT_SECRET,
    _run_cmd,
)


@pytest.fixture
def isolated_tri_governance_env(
    three_bank_docker_topology: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[ConsortiumGovernanceService, str, str, str], None, None]:
    """Isolated consortium governance environment synchronized with four-container topology."""
    store_file = tmp_path / "docker_tri_m63_consortium_store.json"
    account_file = tmp_path / "docker_tri_m63_account_store.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", SHARED_CONSORTIUM_STORE_SECRET)
    monkeypatch.setenv("JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_OIDC_JWT_SIGNING_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_IDENTITY_STORE_SECRET", SHARED_JWT_SECRET)
    monkeypatch.setenv("CFI_AUDIT_LEDGER_SECRET", SHARED_JWT_SECRET)
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    CrossBankIntelligenceService.reset_instance()

    gov_svc = ConsortiumGovernanceService()
    cbi_svc = CrossBankIntelligenceService.get_instance()
    cbi_svc.consortium_service = gov_svc
    for b_id, ident in three_bank_docker_topology["idents"].items():
        cbi_svc.institutional_registry.register_public_key(
            b_id, ident.public_key, allow_overwrite=True
        )
    auth_svc = AuthenticationService.get_instance()

    u_a = auth_svc.get_user("tri_analyst_a")
    if not u_a:
        u_a = auth_svc.register_user("tri_analyst_a", "Pass123!", "bank_a", roles=["admin", "compliance", "ml_lead"])
    else:
        u_a.roles = ["admin", "compliance", "ml_lead"]
        auth_svc.identity_store.save_user(u_a)

    u_b = auth_svc.get_user("tri_analyst_b")
    if not u_b:
        u_b = auth_svc.register_user("tri_analyst_b", "Pass123!", "bank_b", roles=["admin", "compliance", "ml_lead"])
    else:
        u_b.roles = ["admin", "compliance", "ml_lead"]
        auth_svc.identity_store.save_user(u_b)

    u_c = auth_svc.get_user("tri_analyst_c")
    if not u_c:
        u_c = auth_svc.register_user("tri_analyst_c", "Pass123!", "bank_c", roles=["admin", "compliance", "ml_lead"])
    else:
        u_c.roles = ["admin", "compliance", "ml_lead"]
        auth_svc.identity_store.save_user(u_c)

    token_a = auth_svc.create_token_bundle(u_a).access_token
    token_b = auth_svc.create_token_bundle(u_b).access_token
    token_c = auth_svc.create_token_bundle(u_c).access_token

    c_id = "c_tri_m63_docker"
    gov_svc.create_consortium(
        consortium_id=c_id,
        name="Tri-Bank M6.3 Docker Consortium",
        founder_bank_id="bank_a",
        auth_token=token_a,
    )
    gov_svc.add_member(
        consortium_id=c_id,
        bank_id="bank_b",
        role=MemberRole.FULL_MEMBER,
        auth_token=token_a,
    )
    gov_svc.add_member(
        consortium_id=c_id,
        bank_id="bank_c",
        role=MemberRole.FULL_MEMBER,
        auth_token=token_a,
    )

    # Pairwise Grant A <-> B
    gov_svc.register_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_m63_ab",
        institution_a="bank_a",
        institution_b="bank_b",
        authority="BILATERAL_AML_TREATY",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allowed_jurisdictions=["EU", "US"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        allow_cross_border=False,
        allow_institution_identity_sharing=True,
        revision=1,
        auth_token=token_a,
    )
    gov_svc.approve_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_m63_ab",
        approving_bank_id="bank_b",
        auth_token=token_b,
    )

    # Pairwise Grant A <-> C
    gov_svc.register_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_m63_ac",
        institution_a="bank_a",
        institution_b="bank_c",
        authority="BILATERAL_AML_TREATY",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allowed_jurisdictions=["EU", "US"],
        institution_jurisdictions={"bank_a": "EU", "bank_c": "EU"},
        allow_cross_border=False,
        allow_institution_identity_sharing=True,
        revision=1,
        auth_token=token_a,
    )
    gov_svc.approve_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_m63_ac",
        approving_bank_id="bank_c",
        auth_token=token_c,
    )

    # Synchronize consortium store file and account store file into all 4 container filesystems
    c_a = three_bank_docker_topology["container_worker_a"]
    c_b = three_bank_docker_topology["container_worker_b"]
    c_c = three_bank_docker_topology["container_worker_c"]
    c_coord = three_bank_docker_topology["container_coordinator"]
    for c in [c_a, c_b, c_c, c_coord]:
        _run_cmd(["docker", "cp", str(store_file), f"{c}:/tmp/consortium_store.json"], check=False)
        _run_cmd(["docker", "exec", "-u", "0", c, "chmod", "666", "/tmp/consortium_store.json"], check=False)
        _run_cmd(["docker", "cp", str(account_file), f"{c}:/tmp/account_store.json"], check=False)
        _run_cmd(["docker", "exec", "-u", "0", c, "chmod", "666", "/tmp/account_store.json"], check=False)

    # Register authorized grants on worker containers
    grant_ab = gov_svc.get_pairwise_operational_grant(c_id, "grant_m63_ab")
    grant_ac = gov_svc.get_pairwise_operational_grant(c_id, "grant_m63_ac")
    assert grant_ab is not None and grant_ac is not None
    digest_ab = grant_ab.get("scope_digest", "")
    digest_ac = grant_ac.get("scope_digest", "")

    client_a = three_bank_docker_topology["client_a"]
    client_b = three_bank_docker_topology["client_b"]
    client_c = three_bank_docker_topology["client_c"]

    client_a.register_authorized_grant(
        consortium_id=c_id,
        grant_id="grant_m63_ab",
        revision=1,
        scope_digest=digest_ab,
        permitted_counterparts=["bank_b"],
        permitted_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allow_institution_identity_sharing=True,
    )
    client_a.register_authorized_grant(
        consortium_id=c_id,
        grant_id="grant_m63_ac",
        revision=1,
        scope_digest=digest_ac,
        permitted_counterparts=["bank_c"],
        permitted_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allow_institution_identity_sharing=True,
    )
    client_b.register_authorized_grant(
        consortium_id=c_id,
        grant_id="grant_m63_ab",
        revision=1,
        scope_digest=digest_ab,
        permitted_counterparts=["bank_a"],
        permitted_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allow_institution_identity_sharing=True,
    )
    client_c.register_authorized_grant(
        consortium_id=c_id,
        grant_id="grant_m63_ac",
        revision=1,
        scope_digest=digest_ac,
        permitted_counterparts=["bank_a"],
        permitted_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allow_institution_identity_sharing=True,
    )

    yield gov_svc, token_a, token_b, token_c

    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    CrossBankIntelligenceService.reset_instance()
    from app.main import app
    app.dependency_overrides.clear()


def test_multi_counterparty_docker_psi_execution(
    three_bank_docker_topology: dict[str, Any],
    isolated_tri_governance_env: tuple[ConsortiumGovernanceService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies Bank A executes multi-counterparty PSI query against Bank B and Bank C over genuine Docker topology."""
    gov_svc, token_a, _, _ = isolated_tri_governance_env
    client_a = three_bank_docker_topology["client_a"]
    client_b = three_bank_docker_topology["client_b"]
    client_c = three_bank_docker_topology["client_c"]
    port_coord: int = three_bank_docker_topology["port_coord"]

    coord_url = f"http://127.0.0.1:{port_coord}"
    monkeypatch.setenv("CFI_COORDINATOR_URL", coord_url)

    # Seed private entities into institutional vaults:
    # Target in Bank A: IBAN_M63_SYNDICATE_MATCH_001
    # Bank B: IBAN_M63_SYNDICATE_MATCH_001 (EXACT MATCH!)
    # Bank C: IBAN_M63_CLEAN_BENIGN_8888 (NO MATCH!)
    shared_identifier = "TR990001112223334445556667"
    clean_identifier = "TR112233445566778899001122"

    client_a.store_private_entity({
        "id": "ent_target_a",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["ORGANIZED_CRIME"],
    })
    client_b.store_private_entity({
        "id": "ent_target_b",
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["ORGANIZED_CRIME"],
    })
    client_c.store_private_entity({
        "id": "ent_target_c",
        "bank_id": "bank_c",
        "entity_type": "account",
        "raw_identifier": clean_identifier,
        "risk_level": "low",
        "typologies": [],
    })

    cbi_service = CrossBankIntelligenceService.get_instance()
    cbi_service.consortium_service = gov_svc
    CrossBankIntelligenceService._instance = cbi_service
    ent_a = Entity(
        id="ent_target_a",
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(shared_identifier.encode()).hexdigest(),
        bank_id="bank_a",
        display_label="Syndicate Target A",
        risk_level=RiskLevel.HIGH,
        attributes={"raw_identifier": shared_identifier},
    )
    cbi_service.entity_service._entities.set("ent_target_a", _entity_to_dict(ent_a))

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id="c_tri_m63_docker",
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        query_entity_id="ent_target_a",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        use_established_psi=True,
        require_established_psi=True,
    )

    response: MultiCounterpartyIntelligenceQueryResponse = cbi_service.query_multi_counterparty_intelligence(
        req=req,
        auth_token=token_a,
    )

    assert response.consortium_id == "c_tri_m63_docker"
    assert response.requesting_bank_id == "bank_a"
    assert response.query_entity_id == "ent_target_a"
    assert response.counterpart_count == 2
    assert set(response.results.keys()) == {"bank_b", "bank_c"}

    # 1. Bank B outcome verification (Confirmed match over Docker OpenMined PSI)
    sig_b = response.results["bank_b"]
    assert sig_b.match_confirmed is True
    assert sig_b.match_category == "PROBABILISTIC_FUZZY"
    assert sig_b.match_confidence == 0.999999999
    assert sig_b.contributing_institutions == ["bank_b"]
    assert sig_b.contributing_institutions_count == 1
    assert sig_b.execution_mode == "VERIFIED_OPERATIONAL"
    assert sig_b.assurance_level == "REVIEWED_ESTABLISHED_PSI_VERIFIED"
    assert sig_b.provenance["target_bank_id"] == "bank_b"
    assert sig_b.provenance["grant_id"] == "grant_m63_ab"

    # 2. Bank C outcome verification (Disjoint non-match over Docker OpenMined PSI)
    sig_c = response.results["bank_c"]
    assert sig_c.match_confirmed is False
    assert sig_c.match_category == "NO_MATCH"
    assert sig_c.match_confidence == 0.0
    assert sig_c.contributing_institutions == []
    assert sig_c.contributing_institutions_count == 0
    assert sig_c.execution_mode == "VERIFIED_OPERATIONAL"
    assert sig_c.assurance_level == "REVIEWED_ESTABLISHED_PSI_VERIFIED"
    assert sig_c.provenance["target_bank_id"] == "bank_c"
    assert sig_c.provenance["grant_id"] == "grant_m63_ac"


def test_multi_counterparty_preflight_revocation_denial(
    three_bank_docker_topology: dict[str, Any],
    isolated_tri_governance_env: tuple[ConsortiumGovernanceService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that revoking Bank C's pairwise grant causes the entire multi-counterparty query to fail closed."""
    gov_svc, token_a, _, token_c = isolated_tri_governance_env
    port_coord: int = three_bank_docker_topology["port_coord"]
    monkeypatch.setenv("CFI_COORDINATOR_URL", f"http://127.0.0.1:{port_coord}")

    shared_identifier = "TR880001112223334445556667"
    cbi_service = CrossBankIntelligenceService.get_instance()
    cbi_service.consortium_service = gov_svc
    CrossBankIntelligenceService._instance = cbi_service
    ent_a = Entity(
        id="ent_target_rev",
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(shared_identifier.encode()).hexdigest(),
        bank_id="bank_a",
        display_label="Revocation Test Target",
        risk_level=RiskLevel.HIGH,
        attributes={"raw_identifier": shared_identifier},
    )
    cbi_service.entity_service._entities.set("ent_target_rev", _entity_to_dict(ent_a))

    # Bank C revokes bilateral grant grant_m63_ac
    gov_svc.revoke_pairwise_operational_grant(
        consortium_id="c_tri_m63_docker",
        grant_id="grant_m63_ac",
        revoking_bank_id="bank_c",
        reason="bilateral treaty terminated by Bank C",
        auth_token=token_c,
    )

    # Sync revoked store into containers
    store_file = Path(gov_svc._store._store_path)
    for c in [
        three_bank_docker_topology["container_worker_a"],
        three_bank_docker_topology["container_worker_b"],
        three_bank_docker_topology["container_worker_c"],
        three_bank_docker_topology["container_coordinator"],
    ]:
        _run_cmd(["docker", "cp", str(store_file), f"{c}:/tmp/consortium_store.json"], check=False)

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id="c_tri_m63_docker",
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        query_entity_id="ent_target_rev",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        use_established_psi=True,
    )

    # Must fail closed before dispatching any queries!
    with pytest.raises(ConsortiumSecurityError) as exc_info:
        cbi_service.query_multi_counterparty_intelligence(req=req, auth_token=token_a)

    err_msg = str(exc_info.value)
    assert "Pairwise operational authorization rejected for bank_a <-> bank_c" in err_msg


def test_multi_counterparty_fastapi_router_api(
    three_bank_docker_topology: dict[str, Any],
    isolated_tri_governance_env: tuple[ConsortiumGovernanceService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies FastAPI HTTP API router endpoint (/intelligence/query-multi)."""
    gov_svc, token_a, token_b, _ = isolated_tri_governance_env
    port_coord: int = three_bank_docker_topology["port_coord"]
    monkeypatch.setenv("CFI_COORDINATOR_URL", f"http://127.0.0.1:{port_coord}")

    shared_identifier = "TR770001112223334445556667"
    client_a = three_bank_docker_topology["client_a"]
    client_b = three_bank_docker_topology["client_b"]
    client_c = three_bank_docker_topology["client_c"]

    client_a.store_private_entity({
        "id": "ent_api_test",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["MONEY_LAUNDERING"],
    })
    client_b.store_private_entity({
        "id": "ent_api_b",
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["MONEY_LAUNDERING"],
    })
    client_c.store_private_entity({
        "id": "ent_api_c",
        "bank_id": "bank_c",
        "entity_type": "account",
        "raw_identifier": "TR_CLEAN_NON_MATCHING",
        "risk_level": "low",
        "typologies": [],
    })

    # Register entity in router service's store
    ent_a = Entity(
        id="ent_api_test",
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(shared_identifier.encode()).hexdigest(),
        bank_id="bank_a",
        display_label="API Test Entity",
        risk_level=RiskLevel.HIGH,
        attributes={"raw_identifier": shared_identifier},
    )
    cbi_instance = CrossBankIntelligenceService.get_instance()
    cbi_instance.consortium_service = gov_svc
    cbi_instance.entity_service._entities.set("ent_api_test", _entity_to_dict(ent_a))

    http_client = TestClient(app)

    # 1. Successful query by Bank A analyst
    payload = {
        "consortium_id": "c_tri_m63_docker",
        "requesting_bank_id": "bank_a",
        "target_bank_ids": ["bank_b", "bank_c"],
        "query_entity_id": "ent_api_test",
        "purpose": "AML_COLLABORATIVE_INVESTIGATION",
        "execution_mode": "VERIFIED_OPERATIONAL",
        "use_established_psi": True,
        "require_established_psi": True,
    }
    resp = http_client.post(
        "/api/v1/intelligence/cross-bank/query-multi",
        json=payload,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 200, f"Router returned {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["requesting_bank_id"] == "bank_a"
    assert data["counterpart_count"] == 2
    assert data["overall_outcome"] == "COMPLETED"
    assert data["outcome"] == "COMPLETED"
    assert len(data.get("failures", {})) == 0
    assert "bank_b" in data["results"]
    assert "bank_c" in data["results"]
    assert data["results"]["bank_b"]["match_confirmed"] is True
    assert data["results"]["bank_c"]["match_confirmed"] is False

    # Also verify dual routing prefix /v1/intelligence/cross-bank/query-multi
    resp_v1 = http_client.post(
        "/v1/intelligence/cross-bank/query-multi",
        json=payload,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_v1.status_code == 200

    # 2. Cross-tenant impersonation attempt (Bank B token claiming requesting_bank_id = 'bank_a')
    resp_impersonation = http_client.post(
        "/api/v1/intelligence/cross-bank/query-multi",
        json=payload,
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp_impersonation.status_code == 403, (
        f"Impersonation was not rejected: {resp_impersonation.status_code}"
    )

    # 3. Validation rejection for duplicate targets
    invalid_dup = dict(payload)
    invalid_dup["target_bank_ids"] = ["bank_b", "bank_b"]
    resp_dup = http_client.post(
        "/api/v1/intelligence/cross-bank/query-multi",
        json=invalid_dup,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_dup.status_code in (400, 422)

    # 4. Validation rejection for self-targeting
    invalid_self = dict(payload)
    invalid_self["target_bank_ids"] = ["bank_a", "bank_b"]
    resp_self = http_client.post(
        "/api/v1/intelligence/cross-bank/query-multi",
        json=invalid_self,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp_self.status_code in (400, 422)


def test_multi_counterparty_docker_psi_controlled_worker_failure(
    three_bank_docker_topology: dict[str, Any],
    isolated_tri_governance_env: tuple[ConsortiumGovernanceService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that stopping Bank C's container causes a partial failure, retaining Bank B without fabricated signals."""
    gov_svc, token_a, _, _ = isolated_tri_governance_env
    client_a = three_bank_docker_topology["client_a"]
    client_b = three_bank_docker_topology["client_b"]
    port_coord: int = three_bank_docker_topology["port_coord"]
    container_c = three_bank_docker_topology["container_worker_c"]

    coord_url = f"http://127.0.0.1:{port_coord}"
    monkeypatch.setenv("CFI_COORDINATOR_URL", coord_url)

    shared_identifier = "TR330001112223334445556667"
    client_a.store_private_entity({
        "id": "ent_partial_a",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["ORGANIZED_CRIME"],
    })
    client_b.store_private_entity({
        "id": "ent_partial_b",
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["ORGANIZED_CRIME"],
    })

    cbi_service = CrossBankIntelligenceService.get_instance()
    cbi_service.consortium_service = gov_svc
    CrossBankIntelligenceService._instance = cbi_service
    ent_a = Entity(
        id="ent_partial_a",
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(shared_identifier.encode()).hexdigest(),
        bank_id="bank_a",
        display_label="Partial Test Target",
        risk_level=RiskLevel.HIGH,
        attributes={"raw_identifier": shared_identifier},
    )
    cbi_service.entity_service._entities.set("ent_partial_a", _entity_to_dict(ent_a))

    # Temporarily stop Bank C's worker container to trigger genuine worker unavailability
    _run_cmd(["docker", "stop", container_c], check=True)
    try:
        req = MultiCounterpartyIntelligenceQueryRequest(
            consortium_id="c_tri_m63_docker",
            requesting_bank_id="bank_a",
            target_bank_ids=["bank_b", "bank_c"],
            query_entity_id="ent_partial_a",
            purpose="AML_COLLABORATIVE_INVESTIGATION",
            execution_mode="VERIFIED_OPERATIONAL",
            use_established_psi=True,
            require_established_psi=True,
        )

        response: MultiCounterpartyIntelligenceQueryResponse = cbi_service.query_multi_counterparty_intelligence(
            req=req,
            auth_token=token_a,
        )

        assert response.overall_outcome == "PARTIAL_FAILURE"
        assert response.outcome == "PARTIAL_FAILURE"
        assert response.counterpart_count == 2

        # 1. Bank B succeeded and returned genuine verified signal
        assert "bank_b" in response.results
        sig_b = response.results["bank_b"]
        assert sig_b.match_confirmed is True
        assert sig_b.match_category == "PROBABILISTIC_FUZZY"
        assert response.execution_outcomes["bank_b"].status == "SUCCESS"
        assert response.execution_outcomes["bank_b"].signal is not None
        assert response.execution_outcomes["bank_b"].failure is None

        # 2. Bank C failed: no signal fabricated, recorded in failures
        assert "bank_c" not in response.results
        assert "bank_c" in response.failures
        fail_c = response.failures["bank_c"]
        assert fail_c.target_bank_id == "bank_c"
        assert fail_c.error_category in ("WORKER_UNAVAILABLE", "TRANSPORT_FAILURE", "TIMEOUT")
        assert response.execution_outcomes["bank_c"].status == "FAILED"
        assert response.execution_outcomes["bank_c"].signal is None
        assert response.execution_outcomes["bank_c"].failure is not None
    finally:
        _run_cmd(["docker", "start", container_c], check=False)


def test_multi_counterparty_docker_psi_circuit_breaker_recovery(
    three_bank_docker_topology: dict[str, Any],
    isolated_tri_governance_env: tuple[ConsortiumGovernanceService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that an open circuit breaker against an unavailable Docker container recovers to CLOSED
    after container restart and genuine verified operational PSI execution."""
    gov_svc, token_a, _, _ = isolated_tri_governance_env
    client_a = three_bank_docker_topology["client_a"]
    client_b = three_bank_docker_topology["client_b"]
    client_c = three_bank_docker_topology["client_c"]
    port_coord: int = three_bank_docker_topology["port_coord"]
    port_c: int = three_bank_docker_topology["port_c"]
    container_c = three_bank_docker_topology["container_worker_c"]

    coord_url = f"http://127.0.0.1:{port_coord}"
    monkeypatch.setenv("CFI_COORDINATOR_URL", coord_url)

    shared_identifier = "TR550001112223334445556667"
    client_a.store_private_entity({
        "id": "ent_cb_rec_a",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["ORGANIZED_CRIME"],
    })
    client_b.store_private_entity({
        "id": "ent_cb_rec_b",
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["ORGANIZED_CRIME"],
    })
    client_c.store_private_entity({
        "id": "ent_cb_rec_c",
        "bank_id": "bank_c",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["ORGANIZED_CRIME"],
    })

    cbi_service = CrossBankIntelligenceService.get_instance()
    cbi_service.consortium_service = gov_svc
    CrossBankIntelligenceService._instance = cbi_service
    ent_a = Entity(
        id="ent_cb_rec_a",
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(shared_identifier.encode()).hexdigest(),
        bank_id="bank_a",
        display_label="Circuit Breaker Recovery Target",
        risk_level=RiskLevel.HIGH,
        attributes={"raw_identifier": shared_identifier},
    )
    cbi_service.entity_service._entities.set("ent_cb_rec_a", _entity_to_dict(ent_a))

    # Temporarily stop Bank C's container to trigger worker failure
    _run_cmd(["docker", "stop", container_c], check=True)
    try:
        # 1. Query with circuit breaker enabled (failure threshold=1, cooldown=0.2s)
        req_fail = MultiCounterpartyIntelligenceQueryRequest(
            consortium_id="c_tri_m63_docker",
            requesting_bank_id="bank_a",
            target_bank_ids=["bank_b", "bank_c"],
            query_entity_id="ent_cb_rec_a",
            purpose="AML_COLLABORATIVE_INVESTIGATION",
            execution_mode="VERIFIED_OPERATIONAL",
            use_established_psi=True,
            require_established_psi=True,
            circuit_breaker_enabled=True,
            circuit_breaker_failure_threshold=1,
            circuit_breaker_cooldown_seconds=3.0,
        )

        resp1 = cbi_service.query_multi_counterparty_intelligence(req=req_fail, auth_token=token_a)
        assert resp1.overall_outcome == "PARTIAL_FAILURE"
        assert resp1.execution_outcomes["bank_b"].status == "SUCCESS"
        assert resp1.execution_outcomes["bank_c"].status == "FAILED"

        # Circuit for Bank C is now OPEN in SQLite
        st_open = cbi_service.circuit_breaker_store.get_circuit_state("c_tri_m63_docker", "bank_a", "bank_c")
        assert st_open["state"] == "OPEN"
        assert st_open["consecutive_failures"] >= 1

        # 2. Before cooldown expires: immediate subsequent dispatch is rejected before PSI
        resp2 = cbi_service.query_multi_counterparty_intelligence(req=req_fail, auth_token=token_a)
        assert resp2.execution_outcomes["bank_c"].status == "FAILED"
        assert resp2.execution_outcomes["bank_c"].failure is not None
        assert resp2.execution_outcomes["bank_c"].failure.error_category == "CIRCUIT_BREAKER_OPEN"

        # 3. Restart Bank C container
        _run_cmd(["docker", "start", container_c], check=True)
        t_start = time.monotonic()
        healthy = False
        while time.monotonic() - t_start < 20.0:
            try:
                import urllib.request
                with urllib.request.urlopen(f"http://127.0.0.1:{port_c}/worker/health", timeout=1.0) as h_resp:
                    if h_resp.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.5)
        assert healthy, "Worker C container failed to reach healthy state within 20s"
        time.sleep(2.0)  # ensure 3.0s cooldown has elapsed

        # 4. Dispatch recovery probe: Bank C executes genuine operational PSI and recovers!
        resp3 = cbi_service.query_multi_counterparty_intelligence(req=req_fail, auth_token=token_a)
        assert resp3.overall_outcome == "COMPLETED"
        assert resp3.execution_outcomes["bank_c"].status == "SUCCESS"
        sig_c = resp3.execution_outcomes["bank_c"].signal
        assert sig_c is not None
        assert sig_c.execution_mode == "VERIFIED_OPERATIONAL"
        assert sig_c.is_authoritative is True

        # 5. Circuit is now confirmed CLOSED in persistent SQLite!
        st_closed = cbi_service.circuit_breaker_store.get_circuit_state("c_tri_m63_docker", "bank_a", "bank_c")
        assert st_closed["state"] == "CLOSED"
        assert st_closed["consecutive_failures"] == 0
        assert st_closed["half_open_probe_id"] is None
    finally:
        _run_cmd(["docker", "start", container_c], check=False)


def test_multi_counterparty_docker_psi_case_evidence_admission_lifecycle(
    three_bank_docker_topology: dict[str, Any],
    isolated_tri_governance_env: tuple[ConsortiumGovernanceService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-End verified admission lifecycle from genuine Docker OpenMined PSI to institutional case evidence.

    Verifies:
    1. Executes genuine authenticated OpenMined PSI against Bank B and Bank C.
    2. Verifies signed operational receipt through the authentic admission path (_admitted_receipts_store).
    3. Confirms signal is finalized and published (COMMITTED, PUBLISHED, is_authoritative=True).
    4. Creates genuine institution-owned investigation case (Bank A).
    5. Attaches signal using authenticated Bank A investigator.
    6. Reloads persisted evidence and verifies content integrity, scope, disclosure policy, and audit event.
    7. Proves simulated, tampered, or unpublished signals are rejected from entering the evidence ledger.
    """
    gov_svc, token_a, _, _ = isolated_tri_governance_env
    client_a = three_bank_docker_topology["client_a"]
    client_b = three_bank_docker_topology["client_b"]
    client_c = three_bank_docker_topology["client_c"]
    port_coord: int = three_bank_docker_topology["port_coord"]

    coord_url = f"http://127.0.0.1:{port_coord}"
    monkeypatch.setenv("CFI_COORDINATOR_URL", coord_url)

    # 1. Execute genuine authenticated OpenMined PSI
    shared_identifier = "TR990001112223334445558888"
    clean_identifier = "TR112233445566778899009999"

    client_a.store_private_entity({
        "id": "ent_target_e2e_a",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["CORRUPTION_FACILITATION"],
    })
    client_b.store_private_entity({
        "id": "ent_target_e2e_b",
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["CORRUPTION_FACILITATION"],
    })
    client_c.store_private_entity({
        "id": "ent_target_e2e_c",
        "bank_id": "bank_c",
        "entity_type": "account",
        "raw_identifier": clean_identifier,
        "risk_level": "low",
        "typologies": [],
    })

    cbi_service = CrossBankIntelligenceService.get_instance()
    cbi_service.consortium_service = gov_svc
    CrossBankIntelligenceService._instance = cbi_service
    ent_a = Entity(
        id="ent_target_e2e_a",
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(shared_identifier.encode()).hexdigest(),
        bank_id="bank_a",
        display_label="Syndicate Target E2E",
        risk_level=RiskLevel.HIGH,
        attributes={"raw_identifier": shared_identifier},
    )
    cbi_service.entity_service._entities.set("ent_target_e2e_a", _entity_to_dict(ent_a))

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id="c_tri_m63_docker",
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        query_entity_id="ent_target_e2e_a",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        use_established_psi=True,
        require_established_psi=True,
    )

    query_response: MultiCounterpartyIntelligenceQueryResponse = (
        cbi_service.query_multi_counterparty_intelligence(req=req, auth_token=token_a)
    )

    assert query_response.overall_outcome == "COMPLETED"
    assert "bank_b" in query_response.results
    sig_b = query_response.results["bank_b"]
    assert sig_b.match_confirmed is True
    assert sig_b.execution_mode == "VERIFIED_OPERATIONAL"
    assert sig_b.is_authoritative is True

    # 2. Verify signed operational receipt through the actual admission path
    rcp_id_b = sig_b.provenance["receipt_id"]
    admitted_rec = cbi_service._admitted_receipts_store.get(rcp_id_b)
    assert admitted_rec is not None
    assert admitted_rec["execution_mode"] == "VERIFIED_OPERATIONAL"
    assert admitted_rec["signal_id"] == sig_b.signal_id
    assert admitted_rec["requesting_bank_id"] == "bank_a"
    assert admitted_rec["target_bank_id"] == "bank_b"
    assert admitted_rec["consortium_id"] == "c_tri_m63_docker"
    assert len(admitted_rec["content_hash"]) == 64

    # 3. Confirm signal is finalized and published
    stored_sig = cbi_service._signal_store.get(sig_b.signal_id)
    assert stored_sig is not None
    assert stored_sig["admission_status"] == "COMMITTED"
    assert stored_sig["publication_status"] == "PUBLISHED"
    assert stored_sig["is_authoritative"] is True
    assert stored_sig["is_attachable"] is True
    assert stored_sig["content_hash"] == admitted_rec["content_hash"]

    # 4. Create a genuine institution-owned investigation case (Bank A)
    case_svc = cbi_service.case_service
    case = case_svc.create_case(
        title="Tri-Bank Docker PSI Case Evidence Integration",
        bank_id="bank_a",
    )
    assert case.id is not None
    assert case.bank_id == "bank_a"

    # 5. Attach the signal using an authenticated investigator
    attach_resp = cbi_service.attach_signal_to_case_evidence(
        case_id=case.id,
        signal_id=sig_b.signal_id,
        auth_token=token_a,
        notes="Authentic evidence attached from genuine OpenMined PSI execution",
    )

    assert attach_resp.evidence_id is not None
    assert attach_resp.case_id == case.id
    assert attach_resp.signal_id == sig_b.signal_id
    assert attach_resp.is_duplicate_submission is False

    # 6. Reload persisted evidence and verify matching content integrity, scope, disclosure policy, and audit event
    evidence_svc = cbi_service.evidence_service
    ev = evidence_svc.get_evidence(attach_resp.evidence_id)
    assert ev is not None
    ev_path = Path(ev["file_path"])
    assert ev_path.exists()

    persisted_raw = ev_path.read_text(encoding="utf-8")
    persisted_json = json.loads(persisted_raw)

    assert persisted_json["signal_id"] == sig_b.signal_id
    assert persisted_json["consortium_id"] == "c_tri_m63_docker"
    assert persisted_json["requesting_bank_id"] == "bank_a"
    assert persisted_json["is_authoritative"] is True
    assert persisted_json["operational_status"] == "OPERATIONAL"

    sig_content = persisted_json["signal_content"]
    assert sig_content["match_confirmed"] is True
    assert sig_content["match_category"] == "PROBABILISTIC_FUZZY"
    assert sig_content["execution_mode"] == "VERIFIED_OPERATIONAL"
    assert sig_content["is_authoritative"] is True

    # Under ATTRIBUTED_INSTITUTIONS, verify target bank identity is preserved per authorized grant policy
    assert sig_content["disclosure_mode"] == "ATTRIBUTED_INSTITUTIONS"
    assert sig_content["target_bank_id"] == "bank_b"
    assert sig_content["contributing_institutions"] == ["bank_b"]
    prov = persisted_json["provenance"]
    assert prov["target_bank_id"] == "bank_b"
    assert prov["receipt_id"] == rcp_id_b

    # Verify audit event in ImmutableAuditChain
    audit_events = ImmutableAuditChain.get_instance().get_snapshot()
    matched_audit = [
        e for e in audit_events
        if e.event_type == "CROSS_BANK_INTELLIGENCE_EVIDENCE_ATTACHED"
        and e.details.get("signal_id") == sig_b.signal_id
        and e.details.get("case_id") == case.id
    ]
    assert len(matched_audit) >= 1
    assert "tri_analyst_a" in matched_audit[0].actor_id
    assert matched_audit[0].actor_institution == "bank_a"

    # 7. Prove simulated, tampered, or unpublished signals cannot enter that ledger
    # 7a. Simulated research signal rejected
    sim_sig_id = f"sig_sim_{uuid.uuid4().hex[:8]}"
    sim_rec = dict(stored_sig)
    sim_rec["signal_id"] = sim_sig_id
    sim_rec["execution_mode"] = "SIMULATED_RESEARCH"
    sim_rec["is_authoritative"] = False
    sim_rec["is_synthetic"] = True
    sim_data = dict(stored_sig["signal_data"])
    sim_data["signal_id"] = sim_sig_id
    sim_data["execution_mode"] = "SIMULATED_RESEARCH"
    sim_data["is_authoritative"] = False
    sim_data["is_synthetic"] = True
    sim_rec["signal_data"] = sim_data
    sim_rec["content_hash"] = hashlib.sha256(json.dumps(sim_data, sort_keys=True).encode("utf-8")).hexdigest()
    cbi_service._signal_store.set(sim_sig_id, sim_rec)
    with pytest.raises(ConsortiumSecurityError, match="Simulated research signals cannot be attached"):
        cbi_service.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=sim_sig_id,
            auth_token=token_a,
        )

    # 7b. Tampered signal content rejected
    tampered_sig_id = f"sig_tamper_{uuid.uuid4().hex[:8]}"
    tampered_rec = dict(stored_sig)
    tampered_rec["signal_id"] = tampered_sig_id
    tampered_data = dict(stored_sig["signal_data"])
    tampered_data["signal_id"] = tampered_sig_id
    tampered_data["match_confidence"] = 0.50
    tampered_rec["signal_data"] = tampered_data
    tampered_rec["content_hash"] = hashlib.sha256(json.dumps(tampered_data, sort_keys=True).encode("utf-8")).hexdigest()
    cbi_service._signal_store.set(tampered_sig_id, tampered_rec)
    with pytest.raises(ConsortiumSecurityError, match="Cryptographic admission binding mismatch|diverges from cryptographically admitted"):
        cbi_service.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=tampered_sig_id,
            auth_token=token_a,
        )

    # 7c. Unpublished signal rejected
    unpub_sig_id = f"sig_unpub_{uuid.uuid4().hex[:8]}"
    unpub_rec = dict(stored_sig)
    unpub_rec["signal_id"] = unpub_sig_id
    unpub_rec["publication_status"] = "PENDING_PUBLICATION"
    cbi_service._signal_store.set(unpub_sig_id, unpub_rec)
    with pytest.raises(ConsortiumSecurityError, match="publication status is 'PENDING_PUBLICATION'"):
        cbi_service.attach_signal_to_case_evidence(
            case_id=case.id,
            signal_id=unpub_sig_id,
            auth_token=token_a,
        )


def test_multi_counterparty_docker_batch_case_evidence_assembly(
    three_bank_docker_topology: dict[str, Any],
    isolated_tri_governance_env: tuple[ConsortiumGovernanceService, str, str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Genuine Four-Container Docker Acceptance Test for Multi-Counterparty Batch Evidence Assembly (M6.5 Task 2).

    Verifies:
    1. Authenticated Bank A query against Bank B and Bank C with genuine OpenMined PSI.
    2. Authentic PSI execution and independently verified counterpart results.
    3. Authoritative server batch record stored in durable batch store.
    4. Legitimate Bank A case creation.
    5. Batch evidence assembly via HTTP POST /api/v1/intelligence/cross-bank/attach-multi-to-case.
    6. Verifies persisted evidence records, hash integrity, and batch correlation metadata.
    7. Verifies actual audit event in ImmutableAuditChain.
    8. Safe idempotent replay without duplicate evidence or version drift.
    9. Genuine partial-failure scenario: Bank C worker stopped -> Bank B attaches, Bank C is omitted as failure,
       assembly_status is PARTIAL, zero fabricated Bank C evidence written.
    """
    gov_svc, token_a, _, _ = isolated_tri_governance_env
    client_a = three_bank_docker_topology["client_a"]
    client_b = three_bank_docker_topology["client_b"]
    client_c = three_bank_docker_topology["client_c"]
    port_coord: int = three_bank_docker_topology["port_coord"]
    container_c = three_bank_docker_topology["container_worker_c"]

    coord_url = f"http://127.0.0.1:{port_coord}"
    monkeypatch.setenv("CFI_COORDINATOR_URL", coord_url)

    # 1. Setup entities in worker private datasets
    shared_identifier = "TR880001112223334445559999"
    clean_identifier = "TR772233445566778899008888"

    client_a.store_private_entity({
        "id": "ent_batch_docker_a",
        "bank_id": "bank_a",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["CORRUPTION_FACILITATION"],
    })
    client_b.store_private_entity({
        "id": "ent_batch_docker_b",
        "bank_id": "bank_b",
        "entity_type": "account",
        "raw_identifier": shared_identifier,
        "risk_level": "high",
        "typologies": ["CORRUPTION_FACILITATION"],
    })
    client_c.store_private_entity({
        "id": "ent_batch_docker_c",
        "bank_id": "bank_c",
        "entity_type": "account",
        "raw_identifier": clean_identifier,
        "risk_level": "low",
        "typologies": [],
    })

    cbi_service = CrossBankIntelligenceService.get_instance()
    cbi_service.consortium_service = gov_svc
    CrossBankIntelligenceService._instance = cbi_service
    ent_a = Entity(
        id="ent_batch_docker_a",
        entity_type=EntityType.CUSTOMER,
        privacy_id=hashlib.sha256(shared_identifier.encode()).hexdigest(),
        bank_id="bank_a",
        display_label="Docker Batch Target A",
        risk_level=RiskLevel.HIGH,
        attributes={"raw_identifier": shared_identifier},
    )
    cbi_service.entity_service._entities.set("ent_batch_docker_a", _entity_to_dict(ent_a))

    # 2. Execute genuine authenticated OpenMined PSI query against Bank B and Bank C
    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id="c_tri_m63_docker",
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        query_entity_id="ent_batch_docker_a",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        use_established_psi=True,
        require_established_psi=True,
    )

    query_response: MultiCounterpartyIntelligenceQueryResponse = (
        cbi_service.query_multi_counterparty_intelligence(req=req, auth_token=token_a)
    )

    assert query_response.overall_outcome == "COMPLETED"
    assert "bank_b" in query_response.results
    assert "bank_c" in query_response.results

    # 3. Verify server-authoritative batch record in durable batch store
    batch_record = cbi_service.batch_store.get_batch(query_response.query_id)
    assert batch_record is not None
    assert batch_record["requesting_bank_id"] == "bank_a"
    assert "bank_b" in batch_record["successful_signal_ids"]
    assert "bank_c" in batch_record["successful_signal_ids"]

    # 4. Create legitimate Bank A investigation case
    case = cbi_service.case_service.create_case(
        title="Docker Batch Evidence Assembly Case",
        bank_id="bank_a",
    )
    assert case.id is not None
    assert case.bank_id == "bank_a"

    # 5. Assemble batch into case via actual FastAPI HTTP endpoint
    client = TestClient(app)
    resp = client.post(
        "/api/v1/intelligence/cross-bank/attach-multi-to-case",
        json={
            "query_id": query_response.query_id,
            "case_id": case.id,
            "notes": "Docker batch assembled multi-counterparty intelligence evidence",
            "idempotency_key": "docker_batch_attach_key_1",
        },
        headers={"Authorization": f"Bearer {token_a}", "X-Tenant-ID": "bank_a"},
    )
    assert resp.status_code == 200, f"Attach multi error: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["assembly_status"] == "COMPLETED"
    assert data["attached_count"] == 2
    assert data["replayed_count"] == 0
    assert data["is_duplicate_submission"] is False
    assert len(data["attached_evidence"]) == 2

    # 6. Verify persisted evidence records and batch correlation
    evidence_list = cbi_service.evidence_service.get_case_evidence(case.id)
    assert len(evidence_list) == 2
    for ev in evidence_list:
        ev_path = Path(ev["file_path"])
        assert ev_path.exists()
        persisted = json.loads(ev_path.read_text(encoding="utf-8"))
        assert persisted["is_authoritative"] is True
        assert persisted["batch_query_id"] == query_response.query_id

    # 7. Verify audit event
    audit_events = ImmutableAuditChain.get_instance().get_snapshot()
    matched_audit = [
        e for e in audit_events
        if e.event_type == "CROSS_BANK_INTELLIGENCE_MULTI_EVIDENCE_ATTACHED"
        and e.details.get("query_id") == query_response.query_id
        and e.details.get("case_id") == case.id
    ]
    assert len(matched_audit) >= 1
    assert "tri_analyst_a" in matched_audit[0].actor_id

    # 8. Safe idempotent replay without duplicate evidence
    resp_rep = client.post(
        "/api/v1/intelligence/cross-bank/attach-multi-to-case",
        json={
            "query_id": query_response.query_id,
            "case_id": case.id,
            "notes": "Docker batch assembled multi-counterparty intelligence evidence",
            "idempotency_key": "docker_batch_attach_key_1",
        },
        headers={"Authorization": f"Bearer {token_a}", "X-Tenant-ID": "bank_a"},
    )
    assert resp_rep.status_code == 200
    data_rep = resp_rep.json()
    assert data_rep["is_duplicate_submission"] is True
    assert data_rep["attached_count"] == 0
    assert data_rep["replayed_count"] == 2
    assert len(cbi_service.evidence_service.get_case_evidence(case.id)) == 2

    # 9. Demonstrate genuine partial-failure scenario where Bank C worker is stopped
    _run_cmd(["docker", "stop", container_c], check=True)
    try:
        req_partial = MultiCounterpartyIntelligenceQueryRequest(
            consortium_id="c_tri_m63_docker",
            requesting_bank_id="bank_a",
            target_bank_ids=["bank_b", "bank_c"],
            query_entity_id="ent_batch_docker_a",
            purpose="AML_COLLABORATIVE_INVESTIGATION",
            execution_mode="VERIFIED_OPERATIONAL",
            use_established_psi=True,
            require_established_psi=True,
        )
        query_resp_partial = cbi_service.query_multi_counterparty_intelligence(
            req=req_partial, auth_token=token_a
        )
        assert query_resp_partial.overall_outcome == "PARTIAL_FAILURE"
        assert "bank_b" in query_resp_partial.results
        assert "bank_c" in query_resp_partial.failures

        case_partial = cbi_service.case_service.create_case(
            title="Docker Partial Failure Assembly Case",
            bank_id="bank_a",
        )

        resp_p = client.post(
            "/api/v1/intelligence/cross-bank/attach-multi-to-case",
            json={
                "query_id": query_resp_partial.query_id,
                "case_id": case_partial.id,
                "notes": "Partial assembly with unavailable counterpart",
            },
            headers={"Authorization": f"Bearer {token_a}", "X-Tenant-ID": "bank_a"},
        )
        assert resp_p.status_code == 200, f"Attach partial error: {resp_p.status_code} {resp_p.text}"
        data_p = resp_p.json()
        assert data_p["assembly_status"] == "PARTIAL"
        assert data_p["attached_count"] == 1
        assert data_p["failed_count"] == 1
        assert len(data_p["attached_evidence"]) == 1
        assert len(data_p["omitted_or_failed"]) == 1
        assert data_p["omitted_or_failed"][0]["target_bank_id"] is None
        assert data_p["omitted_or_failed"][0]["category"] in ("COUNTERPART_UNAVAILABLE", "COUNTERPART_EXECUTION_FAILURE")
        assert len(cbi_service.evidence_service.get_case_evidence(case_partial.id)) == 1
    finally:
        _run_cmd(["docker", "start", container_c], check=True)
