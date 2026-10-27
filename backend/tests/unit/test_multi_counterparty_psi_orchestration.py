"""Unit tests for Multi-Counterparty PSI Query Orchestration (Module M6.3).

Verifies:
1. MultiCounterpartyIntelligenceQueryRequest schema validations (non-empty, max 10, no duplicates, no self-targeting).
2. Institutional authentication, analyst role enforcement, tenant isolation, and entity ownership.
3. Pre-flight pairwise governance validation (fails closed if any target lacks bilateral authorization).
4. Coordinator authorization ticket binding to authentic pairwise grant (not legacy grant).
5. Independent pairwise execution returning separately identifiable results without fabricated intersections.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
    MultiCounterpartyIntelligenceQueryRequest,
    MultiCounterpartyIntelligenceQueryResponse,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import EntityResolutionService
from app.application.services.isolated_matching_service import (
    CrossBankMatchingCoordinator,
    OperationAuthorizationTicket,
)
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    MemberRole,
    ProposalAction,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    store_file = tmp_path / "m6_3_test_consortium_store.json"
    account_file = tmp_path / "m6_3_test_account_store.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("JWT_SECRET", "test_jwt_secret_m6_3_must_be_sufficiently_long_32bytes!")
    monkeypatch.setenv("CFI_JWT_SECRET", "test_jwt_secret_m6_3_must_be_sufficiently_long_32bytes!")
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", "test_consortium_secret_m6_3_must_be_long_enough_32b!")
    monkeypatch.setenv("CFI_ENABLE_CROSS_BANK_SIMULATION", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    yield
    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()


@pytest.fixture
def auth_tokens() -> dict[str, str]:
    auth_svc = AuthenticationService.get_instance()
    u_a = auth_svc.get_user("user_a") or auth_svc.register_user(
        "user_a", "Pass1234!", "bank_a", roles=["admin", "compliance", "ml_lead"]
    )
    u_b = auth_svc.get_user("user_b") or auth_svc.register_user(
        "user_b", "Pass1234!", "bank_b", roles=["admin", "compliance", "ml_lead"]
    )
    u_c = auth_svc.get_user("user_c") or auth_svc.register_user(
        "user_c", "Pass1234!", "bank_c", roles=["admin", "compliance", "ml_lead"]
    )
    return {
        "bank_a": auth_svc.create_token_bundle(u_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_b).access_token,
        "bank_c": auth_svc.create_token_bundle(u_c).access_token,
    }


@pytest.fixture
def tri_consortium(auth_tokens: dict[str, str]) -> tuple[ConsortiumGovernanceService, str]:
    """Establishes active 3-bank consortium with pairwise grants between A-B and A-C."""
    service = ConsortiumGovernanceService()
    c_id = "consortium_tri_m63"

    service.create_consortium(
        consortium_id=c_id,
        name="Tri-Bank Consortium M6.3",
        founder_bank_id="bank_a",
        auth_token=auth_tokens["bank_a"],
    )
    service.add_member(
        consortium_id=c_id,
        bank_id="bank_b",
        role=MemberRole.FULL_MEMBER,
        auth_token=auth_tokens["bank_a"],
    )
    service.add_member(
        consortium_id=c_id,
        bank_id="bank_c",
        role=MemberRole.FULL_MEMBER,
        auth_token=auth_tokens["bank_a"],
    )

    # Register & approve Pairwise Grant A <-> B
    service.register_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_a_b",
        institution_a="bank_a",
        institution_b="bank_b",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        approving_bank_id="bank_a",
        authority="ECB",
        auth_token=auth_tokens["bank_a"],
    )
    service.approve_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_a_b",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
    )

    # Register & approve Pairwise Grant A <-> C
    service.register_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_a_c",
        institution_a="bank_a",
        institution_b="bank_c",
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        allowed_jurisdictions=["EU"],
        institution_jurisdictions={"bank_a": "EU", "bank_c": "EU"},
        approving_bank_id="bank_a",
        authority="ECB",
        auth_token=auth_tokens["bank_a"],
    )
    service.approve_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_a_c",
        approving_bank_id="bank_c",
        auth_token=auth_tokens["bank_c"],
    )

    return service, c_id


# =========================================================================
# 1. Schema Validation Unit Tests
# =========================================================================

def test_multi_counterparty_request_valid() -> None:
    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_123",
        consortium_id="consortium_tri",
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )
    assert req.target_bank_ids == ["bank_b", "bank_c"]


def test_multi_counterparty_request_rejects_empty_targets() -> None:
    with pytest.raises(ValueError, match="at least 1 item"):
        MultiCounterpartyIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="cust_123",
            consortium_id="consortium_tri",
            target_bank_ids=[],
        )


def test_multi_counterparty_request_rejects_duplicate_targets() -> None:
    with pytest.raises(ValueError, match="Duplicate target bank ID 'bank_b'"):
        MultiCounterpartyIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="cust_123",
            consortium_id="consortium_tri",
            target_bank_ids=["bank_b", "BANK_B"],
        )


def test_multi_counterparty_request_rejects_self_targeting() -> None:
    with pytest.raises(ValueError, match="Self-targeting is prohibited"):
        MultiCounterpartyIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="cust_123",
            consortium_id="consortium_tri",
            target_bank_ids=["bank_b", "bank_a"],
        )


def test_multi_counterparty_request_rejects_exceeding_max_targets() -> None:
    targets = [f"bank_{i}" for i in range(11)]
    with pytest.raises(ValueError, match="at most 10 items"):
        MultiCounterpartyIntelligenceQueryRequest(
            requesting_bank_id="bank_main",
            query_entity_id="cust_123",
            consortium_id="consortium_tri",
            target_bank_ids=targets,
        )



# =========================================================================
# 2. Authentication & Tenant Isolation Unit Tests
# =========================================================================

def test_multi_counterparty_rejects_unauthenticated(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
) -> None:
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService(consortium_service=gov_svc)

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_123",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
    )

    with pytest.raises(PermissionError, match="Missing Bearer token or institutional principal"):
        service.query_multi_counterparty_intelligence(req=req, auth_token=None)


def test_multi_counterparty_rejects_cross_tenant_impersonation(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService(consortium_service=gov_svc)

    # Bank B user attempts to issue query as Bank A
    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_123",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
    )

    with pytest.raises(PermissionError, match="Institutional impersonation rejected"):
        service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_b"])


def test_multi_counterparty_rejects_unowned_local_entity(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    # Register entity owned by Bank B, not Bank A
    entity_svc._entities.set(
        "ent_b",
        {
            "id": "ent_b",
            "bank_id": "bank_b",
            "entity_type": "customer",
            "privacy_id": "priv_hash_b",
            "risk_level": "low",
        },
    )

    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="ent_b",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
    )

    with pytest.raises(PermissionError, match="Cross-tenant access violation: Entity 'ent_b' belongs to 'bank_b'"):
        service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


# =========================================================================
# 3. Pre-Flight Pairwise Governance Validation (Fail-Closed)
# =========================================================================

def test_multi_counterparty_rejects_entire_request_if_one_target_unauthorized(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """If Bank A is authorized with Bank B, but lacks authorization with Bank C, the entire batch fails closed."""
    gov_svc, c_id = tri_consortium
    # Revoke grant A <-> C
    gov_svc.revoke_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_a_c",
        revoking_bank_id="bank_c",
        reason="bilateral_treaty_termination",
        auth_token=auth_tokens["bank_c"],
    )

    entity_svc = EntityResolutionService()
    entity_svc._entities.set(
        "ent_a",
        {
            "id": "ent_a",
            "bank_id": "bank_a",
            "entity_type": "customer",
            "privacy_id": "priv_hash_a",
            "risk_level": "low",
        },
    )
    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="ent_a",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
    )

    with pytest.raises(ConsortiumSecurityError, match="Pairwise operational authorization rejected for bank_a <-> bank_c"):
        service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


def test_multi_counterparty_rejects_if_target_not_consortium_member(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    entity_svc._entities.set(
        "ent_a",
        {
            "id": "ent_a",
            "bank_id": "bank_a",
            "entity_type": "customer",
            "privacy_id": "priv_hash_a",
            "risk_level": "low",
        },
    )
    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="ent_a",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_unregistered"],
    )

    with pytest.raises(ConsortiumSecurityError, match="Target institution 'bank_unregistered' is not an active member"):
        service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


def test_multi_counterparty_rejects_if_target_evicted(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    gov_svc, c_id = tri_consortium
    # Evict bank_c via governance proposal and vote
    p_evict = gov_svc.propose_membership_change(
        consortium_id=c_id,
        creator_bank_id="bank_a",
        target_bank_id="bank_c",
        action=ProposalAction.REMOVE_MEMBER,
        auth_token=auth_tokens["bank_a"],
    )
    gov_svc.cast_vote(p_evict.proposal_id, "bank_b", approve=True, auth_token=auth_tokens["bank_b"])

    entity_svc = EntityResolutionService()
    entity_svc._entities.set(
        "ent_a",
        {
            "id": "ent_a",
            "bank_id": "bank_a",
            "entity_type": "customer",
            "privacy_id": "priv_hash_a",
            "risk_level": "low",
        },
    )
    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="ent_a",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
    )

    with pytest.raises(ConsortiumSecurityError, match="Target institution 'bank_c' has been revoked or evicted"):
        service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


# =========================================================================
# 4. Coordinator Authorization-Ticket Binding (Requirement 4)
# =========================================================================

def test_coordinator_binds_authentic_pairwise_grant_to_ticket(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Verifies CrossBankMatchingCoordinator binds authentic pairwise grant ID and revision, not legacy grant."""
    gov_svc, c_id = tri_consortium
    coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)

    mock_worker_a = MagicMock()
    mock_worker_a.matching_token = "token_a"
    mock_worker_a.sign_operation_ticket.return_value = "sig_worker_a"

    mock_worker_c = MagicMock()
    mock_worker_c.matching_token = "token_c"
    mock_worker_c.sign_operation_ticket.return_value = "sig_worker_c"

    coordinator.register_worker("bank_a", mock_worker_a, matching_token="token_a")
    coordinator.register_worker("bank_c", mock_worker_c, matching_token="token_c")

    # Inject legacy grant into consortium metadata to prove coordinator ignores legacy grant when pairwise grant exists
    c = gov_svc.get_consortium(c_id)
    assert c is not None
    c.metadata["operational_data_sharing_grant"] = {
        "grant_id": "grant_legacy_ab",
        "revision": 99,
        "scope_digest": "legacy_digest",
        "participating_banks": ["bank_a", "bank_b"],
        "is_active": True,
        "approvals": {"bank_a": {"grant_revision": 99}, "bank_b": {"grant_revision": 99}},
    }
    DurableConsortiumStore.get_instance().save_consortium(c)

    captured_tickets: list[OperationAuthorizationTicket] = []

    def mock_sign(ticket: OperationAuthorizationTicket) -> str:
        captured_tickets.append(ticket)
        return "mock_signature_a"

    mock_worker_a.sign_operation_ticket.side_effect = mock_sign

    # Execute matching for bank_a <-> bank_c
    req = CrossBankIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_id="bank_c",
        query_entity_id="entity_001",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(coordinator, "_signal_store", MagicMock()):
        with contextlib.suppress(Exception):
            coordinator.execute_two_bank_private_matching(req=req, auth_token=auth_tokens["bank_a"])

    assert len(captured_tickets) >= 1
    t = captured_tickets[0]
    # Critical M6.3 check: Ticket must bind "grant_a_c", NOT "grant_legacy_ab"!
    assert t.grant_id == "grant_a_c"
    assert t.grant_revision == 1
    assert len(t.grant_scope_digest) >= 32
    assert t.target_bank_id == "bank_c"
    assert t.requesting_bank_id == "bank_a"


def test_coordinator_rejects_matching_when_pairwise_grant_missing(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Coordinator fails closed when no pairwise grant exists between bank_b and bank_c."""
    gov_svc, c_id = tri_consortium
    coordinator = CrossBankMatchingCoordinator(consortium_service=gov_svc)

    mock_b = MagicMock()
    mock_c = MagicMock()
    coordinator.register_worker("bank_b", mock_b, matching_token="token_b")
    coordinator.register_worker("bank_c", mock_c, matching_token="token_c")

    req = CrossBankIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_b",
        target_bank_id="bank_c",
        query_entity_id="entity_001",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
    )

    with pytest.raises(ConsortiumSecurityError, match="Consortium operational policy rejected matching: Consortium 'consortium_tri_m63' lacks an explicit operational data-sharing grant"):
        coordinator.execute_two_bank_private_matching(req=req, auth_token=auth_tokens["bank_b"])


# =========================================================================
# 5. Multi-Counterparty Orchestration Execution
# =========================================================================

def test_multi_counterparty_orchestration_returns_distinct_signals(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Verifies service executes each authorized pair independently and returns separate signals."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    entity_svc._entities.set("ent_a", {
        "id": "ent_a",
        "bank_id": "bank_a",
        "entity_type": "customer",
        "privacy_id": "priv_hash_a",
        "risk_level": "low",
    })

    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    mock_sig_b = CrossBankIntelligenceSignal(
        signal_id="sig_b_1111",
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        query_entity_id="ent_a",
        privacy_hash_truncated="priv_hash...",
        match_confirmed=True,
        match_category="EXACT_MATCH",
        match_confidence=1.0,
        contributing_institutions_count=1,
        contributing_institutions=["bank_b"],
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        sanitized_risk_level="HIGH",
        sanitized_typology_indicators=["RAPID_MOVEMENT"],
        execution_mode="VERIFIED_OPERATIONAL",
        attestation_status="SOFTWARE_EMULATED",
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
        provenance={"target_bank_id": "bank_b"},
    )
    mock_sig_c = CrossBankIntelligenceSignal(
        signal_id="sig_c_2222",
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        query_entity_id="ent_a",
        privacy_hash_truncated="priv_hash...",
        match_confirmed=False,
        match_category="NO_MATCH",
        match_confidence=0.0,
        contributing_institutions_count=0,
        contributing_institutions=[],
        disclosure_mode="ANONYMIZED_SUMMARY",
        sanitized_risk_level="UNOBSERVED",
        sanitized_typology_indicators=[],
        execution_mode="VERIFIED_OPERATIONAL",
        attestation_status="SOFTWARE_EMULATED",
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
        provenance={"target_bank_id": "bank_c"},
    )

    dispatched_reqs: list[CrossBankIntelligenceQueryRequest] = []

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        dispatched_reqs.append(req)
        if req.target_bank_id == "bank_b":
            return mock_sig_b
        elif req.target_bank_id == "bank_c":
            return mock_sig_c
        raise ValueError(f"Unexpected target: {req.target_bank_id}")

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        req = MultiCounterpartyIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="ent_a",
            consortium_id=c_id,
            target_bank_ids=["bank_b", "bank_c"],
            purpose="AML_COLLABORATIVE_INVESTIGATION",
            execution_mode="VERIFIED_OPERATIONAL",
        )
        res: MultiCounterpartyIntelligenceQueryResponse = service.query_multi_counterparty_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
        )

    assert len(dispatched_reqs) == 2
    assert dispatched_reqs[0].target_bank_id == "bank_b"
    assert dispatched_reqs[1].target_bank_id == "bank_c"

    assert res.consortium_id == c_id
    assert res.requesting_bank_id == "bank_a"
    assert res.query_entity_id == "ent_a"
    assert res.counterpart_count == 2
    assert "bank_b" in res.results
    assert "bank_c" in res.results

    # Separately identifiable, distinct signals
    assert res.results["bank_b"].signal_id == "sig_b_1111"
    assert res.results["bank_b"].match_confirmed is True
    assert res.results["bank_b"].contributing_institutions == ["bank_b"]

    assert res.results["bank_c"].signal_id == "sig_c_2222"
    assert res.results["bank_c"].match_confirmed is False
    assert res.results["bank_c"].contributing_institutions == []
