"""Unit tests for Durable Per-Counterparty Execution Budgets and Quota Enforcement.

Adheres strictly to .agents/AGENTS.md, runtime truth, and M6.4 Task 3 acceptance criteria:
1. A valid A-B/A-C batch executes when sufficient budget exists.
2. An exhausted A-C budget rejects the entire batch before dispatch to B or C.
3. Concurrent requests cannot exceed an available pairwise budget.
4. Idempotent replay consumes zero additional budget.
5. Pilot quota enforcement remains active and cannot be bypassed.
6. Known pre-dispatch failures release reservations; ambiguous executions never allow unsafe quota reuse.
7. Default behavior preserves unconstrained execution unless configured or required by governance.
8. Sanitized, non-sensitive quota rejection messages without raw identifiers or payloads.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import sqlite3
import time
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceSignal,
    MultiCounterpartyIntelligenceQueryRequest,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import _entity_to_dict
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    CrossBankExecutionQuotaExceededError,
    MemberRole,
)
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.domain.pilot_operations import (
    PilotEntity,
    PilotOperationalConfig,
    PilotQuotaExceededError,
    PilotStatus,
)
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.infrastructure.storage.durable_execution_budget_store import (
    DurableCrossBankExecutionBudgetStore,
)
from app.infrastructure.storage.durable_idempotency_store import (
    DurableCrossBankIdempotencyStore,
)
from app.infrastructure.storage.durable_pilot_store import DurablePilotStore
from app.main import app


@pytest.fixture(autouse=True)
def isolated_budget_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate SQLite database files and cryptographic keys for each test."""
    store_file = tmp_path / "consortium_test.json"
    account_file = tmp_path / "account_test.json"
    idem_db = tmp_path / "test_idempotency.sqlite"
    budget_db = tmp_path / "test_budget.sqlite"
    pilot_db = tmp_path / "test_pilot.sqlite"

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("CFI_IDEMPOTENCY_DB_PATH", str(idem_db))
    monkeypatch.setenv("CFI_EXECUTION_BUDGET_DB_PATH", str(budget_db))
    monkeypatch.setenv("CFI_PILOT_DB_PATH", str(pilot_db))
    monkeypatch.setenv("JWT_SECRET", "test_jwt_secret_budget_must_be_32bytes_min!")
    monkeypatch.setenv("CFI_JWT_SECRET", "test_jwt_secret_budget_must_be_32bytes_min!")
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", "test_secret_budget_32bytes_min!")
    monkeypatch.setenv("CFI_ENABLE_CROSS_BANK_SIMULATION", "true")

    from app.config import get_settings

    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurableCrossBankIdempotencyStore.reset_instance()
    DurableCrossBankExecutionBudgetStore.reset_instance()
    DurablePilotStore._instance = None
    CrossBankIntelligenceService.reset_instance()

    yield

    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurableCrossBankIdempotencyStore.reset_instance()
    DurableCrossBankExecutionBudgetStore.reset_instance()
    DurablePilotStore._instance = None
    CrossBankIntelligenceService.reset_instance()


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
    service = ConsortiumGovernanceService()
    c_id = "consortium_tri_budget"

    service.create_consortium(
        consortium_id=c_id,
        name="Tri-Bank Consortium Budget",
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

    # Pairwise Grant A <-> B
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

    # Pairwise Grant A <-> C
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


def _create_verified_signal(bank_id: str, consortium_id: str, match: bool = False) -> CrossBankIntelligenceSignal:
    return CrossBankIntelligenceSignal(
        signal_id=f"sig_{bank_id}_valid_{time.time_ns()}",
        consortium_id=consortium_id,
        requesting_bank_id="bank_a",
        query_entity_id="cust_budget_1",
        privacy_hash_truncated="priv_hash_truncated...",
        match_confirmed=match,
        match_category="EXACT_MATCH" if match else "NO_MATCH",
        match_confidence=1.0 if match else 0.0,
        contributing_institutions_count=1 if match else 0,
        contributing_institutions=[bank_id] if match else [],
        disclosure_mode="ATTRIBUTED_INSTITUTIONS" if match else "ANONYMIZED_SUMMARY",
        sanitized_risk_level="HIGH" if match else "UNOBSERVED",
        sanitized_typology_indicators=["RAPID_MOVEMENT"] if match else [],
        execution_mode="VERIFIED_OPERATIONAL",
        attestation_status="SOFTWARE_EMULATED",
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
        provenance={"target_bank_id": bank_id},
    )


def test_pairwise_budget_unconfigured_preserves_default_execution(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """When no budget is configured, multi-counterparty queries execute unconstrained."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc

    test_ent = Entity(
        id="cust_budget_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_1",
        bank_id="bank_a",
        display_label="Budget Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_1", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=True)
    sig_c = _create_verified_signal("bank_c", c_id, match=False)

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        query_entity_id="cust_budget_1",
        privacy_id="hash_budget_1",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    def mock_exec(req, *args, **kwargs):
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            return sig_c
        raise ValueError("Unknown target")

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert resp.overall_outcome == "COMPLETED"
    assert "bank_b" in resp.results
    assert "bank_c" in resp.results


def test_valid_batch_executes_when_sufficient_budget_exists(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Valid A-B/A-C batch executes when both counterparts have sufficient budget."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    # Set budget for Bank A -> Bank B (2 slots) and Bank A -> Bank C (1 slot)
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=2)
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_c", max_executions=1)

    test_ent = Entity(
        id="cust_budget_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_1",
        bank_id="bank_a",
        display_label="Budget Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_1", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=True)
    sig_c = _create_verified_signal("bank_c", c_id, match=False)

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        query_entity_id="cust_budget_1",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=lambda req, *a, **kw: sig_b if req.target_bank_id == "bank_b" else sig_c):
        resp = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert resp.overall_outcome == "COMPLETED"

    # Verify both budgets reflect 1 execution consumed
    b_status = budget_store.get_budget(c_id, "bank_a", "bank_b")
    c_status = budget_store.get_budget(c_id, "bank_a", "bank_c")
    assert b_status is not None and b_status["consumed_executions"] == 1
    assert b_status["remaining_executions"] == 1
    assert c_status is not None and c_status["consumed_executions"] == 1
    assert c_status["remaining_executions"] == 0


def test_exhausted_counterpart_budget_rejects_entire_batch_before_dispatch(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """An exhausted A-C budget rejects the entire batch before dispatch to B or C."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    # Bank A has remaining budget for Bank B (max 5, 0 consumed)
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=5)
    # Bank A has exhausted budget for Bank C (max 1, consumed 1)
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_c", max_executions=1)
    # Artificially consume the 1 slot for Bank C
    ok, bid, _ = budget_store.reserve_batch(consortium_id=c_id, requesting_bank_id="bank_a", target_bank_ids=["bank_c"])
    assert ok
    budget_store.commit_reservations(bid, ["bank_c"])

    c_status = budget_store.get_budget(c_id, "bank_a", "bank_c")
    assert c_status is not None and c_status["remaining_executions"] == 0

    test_ent = Entity(
        id="cust_budget_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_1",
        bank_id="bank_a",
        display_label="Budget Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_1", _entity_to_dict(test_ent))

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        query_entity_id="cust_budget_1",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    dispatched_targets = []

    def mock_exec(req_single, *args, **kwargs):
        dispatched_targets.append(req_single.target_bank_id)
        return _create_verified_signal(req_single.target_bank_id, c_id)

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        with pytest.raises(CrossBankExecutionQuotaExceededError) as exc_info:
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    # Assert rejection details
    assert "bank_c" in str(exc_info.value)
    assert exc_info.value.exhausted_targets == ["bank_c"]

    # Invariant: Dispatch demonstrably did NOT occur to B or C!
    assert len(dispatched_targets) == 0

    # Invariant: Bank B's consumed executions did NOT increment!
    b_status = budget_store.get_budget(c_id, "bank_a", "bank_b")
    assert b_status is not None and b_status["consumed_executions"] == 0
    assert b_status["remaining_executions"] == 5


def test_concurrent_requests_cannot_oversubscribe_budget(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Concurrent requests cannot exceed available pairwise budget under atomic locks."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    # Configure strictly 3 permitted executions for Bank A -> Bank B
    max_budget = 3
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=max_budget)

    test_ent = Entity(
        id="cust_budget_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_1",
        bank_id="bank_a",
        display_label="Budget Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_1", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=False)

    success_count = 0
    quota_exceeded_count = 0

    def run_query():
        req = MultiCounterpartyIntelligenceQueryRequest(
            consortium_id=c_id,
            requesting_bank_id="bank_a",
            target_bank_ids=["bank_b"],
            query_entity_id="cust_budget_1",
            purpose="AML_COLLABORATIVE_INVESTIGATION",
            execution_mode="VERIFIED_OPERATIONAL",
        )
        try:
            with patch.object(service, "_execute_verified_operational_psi", return_value=sig_b):
                service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])
            return "SUCCESS"
        except CrossBankExecutionQuotaExceededError:
            return "QUOTA_EXCEEDED"

    total_attempts = 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(run_query) for _ in range(total_attempts)]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res == "SUCCESS":
                success_count += 1
            elif res == "QUOTA_EXCEEDED":
                quota_exceeded_count += 1

    # Exactly max_budget (3) must succeed; the rest (7) must be rejected
    assert success_count == max_budget
    assert quota_exceeded_count == total_attempts - max_budget

    b_status = budget_store.get_budget(c_id, "bank_a", "bank_b")
    assert b_status is not None
    assert b_status["consumed_executions"] == max_budget
    assert b_status["remaining_executions"] == 0


def test_idempotent_replay_consumes_zero_additional_budget(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Idempotent replay reuses verified results without consuming additional budget or tickets."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    # Exactly 1 budget slot for Bank A -> Bank B
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=1)

    test_ent = Entity(
        id="cust_budget_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_1",
        bank_id="bank_a",
        display_label="Budget Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_1", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        query_entity_id="cust_budget_1",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", return_value=sig_b) as mock_psi:
        # 1. First execution consumes the single available budget slot
        resp1 = service.query_multi_counterparty_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
            idempotency_key="idempotent_budget_key_1",
        )
        assert resp1.overall_outcome == "COMPLETED"
        assert getattr(resp1, "_idempotency_replayed", False) is False
        assert mock_psi.call_count == 1

        b_status = budget_store.get_budget(c_id, "bank_a", "bank_b")
        assert b_status is not None and b_status["consumed_executions"] == 1
        assert b_status["remaining_executions"] == 0

        # 2. Idempotent replay: budget is exhausted, yet replay must succeed and consume 0 slots!
        resp2 = service.query_multi_counterparty_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
            idempotency_key="idempotent_budget_key_1",
        )
        assert resp2.overall_outcome == "COMPLETED"
        assert getattr(resp2, "_idempotency_replayed", False) is True
        # Mock PSI was NOT called a second time
        assert mock_psi.call_count == 1

        # Budget consumption remains exactly 1
        b_status2 = budget_store.get_budget(c_id, "bank_a", "bank_b")
        assert b_status2 is not None and b_status2["consumed_executions"] == 1
        assert b_status2["remaining_executions"] == 0

        # 3. A fresh, non-idempotent request against Bank B fails immediately with quota exhausted
        req_fresh = req.model_copy(update={"idempotency_key": "different_key_2"})
        with pytest.raises(CrossBankExecutionQuotaExceededError):
            service.query_multi_counterparty_intelligence(
                req=req_fresh,
                auth_token=auth_tokens["bank_a"],
            )


def test_pilot_quota_enforcement_remains_active_and_cannot_be_bypassed(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Pairwise execution budget does not bypass or replace authoritative pilot quota enforcement."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store
    pilot_store = DurablePilotStore.get_instance()

    # Pairwise budget is generous (10 executions available)
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=10)

    # But the governing pilot quota is strictly 1 total query
    now_dt = datetime.datetime.now(UTC)
    pilot_cfg = PilotOperationalConfig(
        pilot_id="pilot_strict_quota",
        consortium_id=c_id,
        participating_institutions=["bank_a", "bank_b"],
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION"],
        query_quota_total=1,
        query_quota_per_institution=1,
        starts_at=now_dt.isoformat(),
        expires_at=(now_dt + datetime.timedelta(days=1)).isoformat(),
    )
    pilot_entity = PilotEntity(
        config=pilot_cfg,
        status=PilotStatus.ACTIVE,
        queries_executed=1,  # Pilot quota already completely exhausted!
        institution_query_counts={"bank_a": 1},
    )
    pilot_store.save_pilot(pilot_entity)

    test_ent = Entity(
        id="cust_budget_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_1",
        bank_id="bank_a",
        display_label="Budget Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_1", _entity_to_dict(test_ent))

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        query_entity_id="cust_budget_1",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        pilot_id="pilot_strict_quota",
    )

    # Attempt query: PilotQuotaExceededError must be raised and fail closed
    with pytest.raises(PilotQuotaExceededError) as exc_info:
        service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert "pilot total query quota exhausted" in str(exc_info.value).lower()

    # Pre-dispatch pilot quota failure must NOT consume the pairwise execution budget
    b_status = budget_store.get_budget(c_id, "bank_a", "bank_b")
    assert b_status is not None and b_status["consumed_executions"] == 0
    assert b_status["remaining_executions"] == 10


def test_known_pre_dispatch_failure_releases_reservations(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Pre-dispatch failures release reservations, ensuring no quota is consumed when dispatch did not occur."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    # Exactly 1 slot for Bank A -> Bank B and Bank A -> Bank C
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=1)
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_c", max_executions=1)

    test_ent = Entity(
        id="cust_budget_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_1",
        bank_id="bank_a",
        display_label="Budget Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_1", _entity_to_dict(test_ent))

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        query_entity_id="cust_budget_1",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    # Inject pre-dispatch failure before dispatching to any counterpart
    def fail_pre_dispatch(req, *args, **kwargs):
        raise ConsortiumSecurityError("Simulated coordinator offline during preflight check")

    with patch.object(service, "_execute_verified_operational_psi", side_effect=fail_pre_dispatch):
        with pytest.raises(ConsortiumSecurityError):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    # Because dispatch demonstrably did not occur for Bank C, Bank C reservation is RELEASED
    # (And since Bank B crashed on first target attempt, Bank C was undispatched and released)
    c_status = budget_store.get_budget(c_id, "bank_a", "bank_c")
    assert c_status is not None and c_status["consumed_executions"] == 0
    assert c_status["remaining_executions"] == 1


def test_ambiguous_execution_fails_closed_without_restoring_quota(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Indeterminate execution failures fail closed without silently restoring the consumable slot."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    # Exactly 1 slot for Bank A -> Bank B
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=1)

    test_ent = Entity(
        id="cust_budget_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_1",
        bank_id="bank_a",
        display_label="Budget Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_1", _entity_to_dict(test_ent))

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        query_entity_id="cust_budget_1",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    # Dispatch occurs, but throws an unclassified fatal exception (ambiguous/indeterminate crash during matching)
    def ambiguous_crash(req, *args, **kwargs):
        raise RuntimeError("Worker crash midway through matching after ticket issuance")

    with patch.object(service, "_execute_verified_operational_psi", side_effect=ambiguous_crash):
        with pytest.raises(RuntimeError):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    # Invariant: The slot was marked FAILED_CLOSED and was NOT restored!
    b_status = budget_store.get_budget(c_id, "bank_a", "bank_b")
    assert b_status is not None and b_status["consumed_executions"] == 1
    assert b_status["remaining_executions"] == 0

    # A subsequent request is rejected due to budget exhaustion (unsafe quota reuse prevented)
    with pytest.raises(CrossBankExecutionQuotaExceededError):
        service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


def test_multi_target_partial_failure_commits_all_dispatched_counterparts(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """In PARTIAL_FAILURE, both successful and cleanly failed counterpart executions are committed."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=2)
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_c", max_executions=2)

    test_ent = Entity(
        id="cust_budget_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_1",
        bank_id="bank_a",
        display_label="Budget Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_1", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec(req, *args, **kwargs):
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            raise ConnectionRefusedError("Worker offline")
        raise ValueError("Unknown target")

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        query_entity_id="cust_budget_1",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert resp.overall_outcome == "PARTIAL_FAILURE"
    assert "bank_b" in resp.results
    assert "bank_c" in resp.failures

    # Both targets completed with definitive outcomes (SUCCESS and FAILED), so both are COMMITTED
    b_status = budget_store.get_budget(c_id, "bank_a", "bank_b")
    c_status = budget_store.get_budget(c_id, "bank_a", "bank_c")
    assert b_status is not None and b_status["consumed_executions"] == 1
    assert c_status is not None and c_status["consumed_executions"] == 1


def test_sliding_window_rollover_replenishes_budget(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Sliding window execution budget rolls over and replenishes after window_seconds expires."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    # 1 execution per 2 seconds
    budget_store.set_budget(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        max_executions=1,
        window_seconds=2,
    )

    test_ent = Entity(
        id="cust_budget_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_1",
        bank_id="bank_a",
        display_label="Budget Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_1", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=False)
    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        query_entity_id="cust_budget_1",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", return_value=sig_b):
        # 1. Execution 1 consumes the quota
        resp1 = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])
        assert resp1.overall_outcome == "COMPLETED"

        # 2. Immediate second request is rejected (quota exhausted)
        with pytest.raises(CrossBankExecutionQuotaExceededError):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

        # 3. Wait for the 2-second window to roll over
        time.sleep(2.1)

        # 4. Request 3 succeeds because window replenished!
        resp3 = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])
        assert resp3.overall_outcome == "COMPLETED"


def test_fastapi_router_returns_429_on_quota_exceeded(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """FastAPI router maps CrossBankExecutionQuotaExceededError to sanitized HTTP 429 Too Many Requests."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=1)
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_c", max_executions=1)

    # Exhaust Bank C
    ok, bid, _ = budget_store.reserve_batch(consortium_id=c_id, requesting_bank_id="bank_a", target_bank_ids=["bank_c"])
    assert ok
    budget_store.commit_reservations(bid, ["bank_c"])

    test_ent = Entity(
        id="cust_router_quota",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_router_quota",
        bank_id="bank_a",
        display_label="Router Quota Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_router_quota", _entity_to_dict(test_ent))

    client = TestClient(app)
    payload = {
        "consortium_id": c_id,
        "requesting_bank_id": "bank_a",
        "target_bank_ids": ["bank_b", "bank_c"],
        "query_entity_id": "cust_router_quota",
        "purpose": "AML_COLLABORATIVE_INVESTIGATION",
        "execution_mode": "VERIFIED_OPERATIONAL",
    }

    resp = client.post(
        "/api/v1/intelligence/cross-bank/query-multi",
        json=payload,
        headers={"Authorization": f"Bearer {auth_tokens['bank_a']}"},
    )

    assert resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    data = resp.json()
    detail = data["detail"]
    assert "Cross-bank execution quota exceeded" in detail
    assert "bank_c" in detail
    # Invariant: No customer identifiers or secrets leaked in error
    assert "cust_router_quota" not in detail
    assert "hash_router_quota" not in detail


def test_sanitized_audit_records_do_not_persist_sensitive_data(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Direct database audit verifies no raw identifiers, matching tokens, or payloads are stored."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=5)

    test_ent = Entity(
        id="sensitive_customer_john_doe_999",
        entity_type=EntityType.CUSTOMER,
        privacy_id="secret_salt_hash_xyz_12345",
        bank_id="bank_a",
        display_label="Sensitive Customer",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("sensitive_customer_john_doe_999", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        query_entity_id="sensitive_customer_john_doe_999",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", return_value=sig_b):
        service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    # Inspect SQLite database directly
    conn = sqlite3.connect(budget_store.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM cross_bank_budget_reservations;").fetchall()
    conn.close()

    assert len(rows) > 0
    for r in rows:
        row_dict = dict(r)
        dump = str(row_dict)
        # Verify no sensitive customer identifiers, salts, tokens, or payloads are stored
        assert "sensitive_customer_john_doe_999" not in dump
        assert "secret_salt_hash_xyz_12345" not in dump
        assert "private_key" not in dump
        assert "matching_token" not in dump


def test_total_budget_not_replenished_by_audit_cleanup(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """A total budget without execution window cannot be replenished merely through audit cleanup."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    # Exactly 1 total execution budget for Bank A -> Bank B (window_seconds=None)
    budget_store.set_budget(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        max_executions=1,
        window_seconds=None,
    )

    test_ent = Entity(
        id="cust_budget_audit_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_audit_1",
        bank_id="bank_a",
        display_label="Budget Audit Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_audit_1", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        query_entity_id="cust_budget_audit_1",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", return_value=sig_b):
        resp = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])
    assert resp.overall_outcome == "COMPLETED"

    # Verify budget is exhausted
    b_status = budget_store.get_budget(c_id, "bank_a", "bank_b")
    assert b_status is not None and b_status["consumed_executions"] == 1
    assert b_status["remaining_executions"] == 0

    # Simulate 8 days passing and trigger retention cleanup (prunes all reservations older than 7 days)
    now_future = datetime.datetime.now(UTC) + datetime.timedelta(days=8)
    with budget_store._lock, budget_store._get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        budget_store._execute_cleanup_in_conn(conn, now_future, now_future.isoformat())
        conn.commit()

    # Verify the reservation row was pruned from the audit reservations table
    with budget_store._get_connection() as conn:
        res_count = conn.execute("SELECT COUNT(*) as c FROM cross_bank_budget_reservations;").fetchone()["c"]
        assert res_count == 0

    # Invariant: Despite audit rows being pruned, authoritative durable consumption is preserved!
    b_status_after = budget_store.get_budget(c_id, "bank_a", "bank_b")
    assert b_status_after is not None and b_status_after["consumed_executions"] == 1
    assert b_status_after["remaining_executions"] == 0

    # A subsequent request MUST still be rejected due to quota exhaustion (silent replenishment prevented)
    with pytest.raises(CrossBankExecutionQuotaExceededError) as exc_info:
        service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])
    assert "Cross-bank execution quota exceeded" in str(exc_info.value)


def test_expired_unresolved_reservations_cannot_create_temporary_oversubscription(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
) -> None:
    """Expired unresolved reservations are atomically reconciled as FAILED_CLOSED before granting new reservations."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    # Exactly 1 total slot for Bank A -> Bank B
    budget_store.set_budget(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        max_executions=1,
    )

    # Reserve the 1 slot with a very short TTL of 0.05 seconds
    ok, bid, exhausted = budget_store.reserve_batch(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        ttl_seconds=0.05,
    )
    assert ok is True

    # Wait for the reservation to expire
    time.sleep(0.1)

    # Verify that without running periodic cleanup, attempting a new reservation immediately reconciles
    # the expired reservation to FAILED_CLOSED and rejects oversubscription
    ok2, bid2, exhausted2 = budget_store.reserve_batch(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
    )
    assert ok2 is False
    assert exhausted2 == ["bank_b"]

    # Verify the expired reservation is now FAILED_CLOSED in database
    status_b = budget_store.get_budget(c_id, "bank_a", "bank_b")
    assert status_b is not None and status_b["consumed_executions"] == 1
    assert status_b["remaining_executions"] == 0


def test_persisted_failure_reasons_never_contain_sentinel_secret(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Failure resolution reasons strictly persist bounded categories and never leak raw exception secrets."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    budget_store.set_budget(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        max_executions=5,
    )

    test_ent = Entity(
        id="cust_budget_secret_1",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_secret_1",
        bank_id="bank_a",
        display_label="Secret Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_secret_1", _entity_to_dict(test_ent))

    sentinel_secret = "SENTINEL_SECRET_TOKEN_99999_VAULT_KEY_DO_NOT_LEAK"

    def fail_with_sentinel_secret(*args, **kwargs):
        raise RuntimeError(f"Indeterminate crash containing secret: {sentinel_secret}")

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        query_entity_id="cust_budget_secret_1",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=fail_with_sentinel_secret):
        with pytest.raises(RuntimeError):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    # Inspect all rows and columns across the entire budget database
    with budget_store._get_connection() as conn:
        for table_name in (
            "cross_bank_budget_reservations",
            "cross_bank_budget_consumption",
            "cross_bank_budget_window_executions",
            "cross_bank_execution_budgets",
        ):
            rows = conn.execute(f"SELECT * FROM {table_name};").fetchall()
            for r in rows:
                dump = str(dict(r))
                assert sentinel_secret not in dump, f"Sentinel secret leaked in table {table_name}: {dump}"
                if table_name == "cross_bank_budget_reservations":
                    assert r["reason"] == "EXECUTION_INDETERMINATE"


def test_predispatch_pilot_quota_exceeded_multi_target_accounting(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Pre-dispatch pilot quota failure releases undispatched target pairwise budget while charging completed target."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    # Pairwise budgets of 1 for Bank B and Bank C
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=1)
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_c", max_executions=1)

    test_ent = Entity(
        id="cust_budget_multi_pilot",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_multi_pilot",
        bank_id="bank_a",
        display_label="Multi Pilot Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_multi_pilot", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=False)

    def execute_with_selective_pilot_failure(req, *args, **kwargs):
        if req.target_bank_id == "bank_b":
            return sig_b
        # Bank C fails pre-dispatch pilot quota
        raise PilotQuotaExceededError("Pilot total query quota exhausted for counterpart bank_c")

    req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        query_entity_id="cust_budget_multi_pilot",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=execute_with_selective_pilot_failure):
        with pytest.raises(PilotQuotaExceededError):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    # Bank B completed execution -> pairwise budget IS committed and charged
    b_status = budget_store.get_budget(c_id, "bank_a", "bank_b")
    assert b_status is not None and b_status["consumed_executions"] == 1
    assert b_status["remaining_executions"] == 0

    # Bank C encountered pre-dispatch pilot quota failure -> pairwise budget IS RELEASED and uncharged
    c_status = budget_store.get_budget(c_id, "bank_a", "bank_c")
    assert c_status is not None and c_status["consumed_executions"] == 0
    assert c_status["remaining_executions"] == 1


def test_idempotency_fail_closed_after_partial_execution_and_clean_release_when_undispatched(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Reusing identical key after partial execution is rejected; pre-dispatch abort permits clean retry."""
    from app.domain.consortium_governance import CrossBankIdempotencyConflictError

    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=5)
    budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_c", max_executions=5)

    test_ent = Entity(
        id="cust_budget_idem_partial",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_budget_idem_partial",
        bank_id="bank_a",
        display_label="Idem Partial Entity",
        risk_level=RiskLevel.HIGH,
    )
    service.entity_service._entities.set("cust_budget_idem_partial", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def execute_b_success_c_pilot_fail(req, *args, **kwargs):
        if req.target_bank_id == "bank_b":
            return sig_b
        raise PilotQuotaExceededError("Pilot quota exhausted for target bank_c")

    partial_req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b", "bank_c"],
        query_entity_id="cust_budget_idem_partial",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    idem_key_partial = "idem_key_b_success_c_pilot_fail_1"

    with patch.object(service, "_execute_verified_operational_psi", side_effect=execute_b_success_c_pilot_fail) as mock_psi:
        # 1. Initial attempt: Bank B completes, Bank C pilot quota fails
        with pytest.raises(PilotQuotaExceededError):
            service.query_multi_counterparty_intelligence(
                req=partial_req,
                auth_token=auth_tokens["bank_a"],
                idempotency_key=idem_key_partial,
            )

        assert mock_psi.call_count == 2  # Bank B and then Bank C attempted

        # Bank B consumed 1, Bank C consumed 0
        b_status_1 = budget_store.get_budget(c_id, "bank_a", "bank_b")
        c_status_1 = budget_store.get_budget(c_id, "bank_a", "bank_c")
        assert b_status_1 is not None and b_status_1["consumed_executions"] == 1
        assert c_status_1 is not None and c_status_1["consumed_executions"] == 0

        # 2. Invariant: Reusing identical key MUST be rejected in fail-closed ambiguous state
        with pytest.raises(CrossBankIdempotencyConflictError) as conf_exc:
            service.query_multi_counterparty_intelligence(
                req=partial_req,
                auth_token=auth_tokens["bank_a"],
                idempotency_key=idem_key_partial,
            )
        assert "indeterminate state" in str(conf_exc.value)

        # 3. Assert zero additional PSI executions occurred
        assert mock_psi.call_count == 2

        # 4. Assert zero additional budget consumed
        b_status_2 = budget_store.get_budget(c_id, "bank_a", "bank_b")
        c_status_2 = budget_store.get_budget(c_id, "bank_a", "bank_c")
        assert b_status_2 is not None and b_status_2["consumed_executions"] == 1
        assert c_status_2 is not None and c_status_2["consumed_executions"] == 0

    # 5. Clean abort scenario: pilot rejection BEFORE all dispatches permits safe clean release
    clean_req = MultiCounterpartyIntelligenceQueryRequest(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        query_entity_id="cust_budget_idem_partial",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    idem_key_clean = "idem_key_clean_abort_test_1"

    def fail_first_target_pilot(*args, **kwargs):
        raise PilotQuotaExceededError("Target bank_b pilot quota exhausted pre-dispatch")

    with patch.object(service, "_execute_verified_operational_psi", side_effect=fail_first_target_pilot):
        with pytest.raises(PilotQuotaExceededError):
            service.query_multi_counterparty_intelligence(
                req=clean_req,
                auth_token=auth_tokens["bank_a"],
                idempotency_key=idem_key_clean,
            )

    # Bank B was not charged for the pre-dispatch pilot failure
    b_status_pre = budget_store.get_budget(c_id, "bank_a", "bank_b")
    assert b_status_pre is not None and b_status_pre["consumed_executions"] == 1  # 1 from previous partial test

    # Invariant: Because no counterpart was dispatched and no side effects occurred, the key was cleanly released!
    # A subsequent retry with the identical key can execute normally
    with patch.object(service, "_execute_verified_operational_psi", return_value=sig_b):
        resp_clean = service.query_multi_counterparty_intelligence(
            req=clean_req,
            auth_token=auth_tokens["bank_a"],
            idempotency_key=idem_key_clean,
        )
        assert resp_clean.overall_outcome == "COMPLETED"
        assert getattr(resp_clean, "_idempotency_replayed", False) is False

    b_status_final = budget_store.get_budget(c_id, "bank_a", "bank_b")
    assert b_status_final is not None and b_status_final["consumed_executions"] == 2


def test_rolling_window_consumption_preserved_through_seven_day_audit_retention(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
) -> None:
    """A 30-day window does not replenish after 7-day audit cleanup, but replenishes after 30-day expiration."""
    gov_svc, c_id = tri_consortium
    service = CrossBankIntelligenceService.get_instance()
    service.consortium_service = gov_svc
    budget_store = service.budget_store

    # 30-day rolling window budget with max 1 execution
    thirty_days_sec = 30 * 86400
    budget_store.set_budget(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        counterpart_bank_id="bank_b",
        max_executions=1,
        window_seconds=thirty_days_sec,
    )

    now_0 = datetime.datetime.now(UTC)

    # 1. Execute and commit 1 reservation at now_0
    ok, bid, exhausted = budget_store.reserve_batch(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        now_dt=now_0,
    )
    assert ok is True
    budget_store.commit_reservations(bid, ["bank_b"])

    status_0 = budget_store.get_budget(c_id, "bank_a", "bank_b", now_dt=now_0)
    assert status_0 is not None
    assert status_0["consumed_executions"] == 1
    assert status_0["remaining_executions"] == 0

    # 2. Simulate 8 days passing (exceeding 7-day retention cleanup)
    now_8d = now_0 + datetime.timedelta(days=8)
    with budget_store._lock, budget_store._get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        budget_store._execute_cleanup_in_conn(conn, now_8d, now_8d.isoformat())
        conn.commit()

    # Verify that the disposable audit reservation row was pruned from cross_bank_budget_reservations
    with budget_store._get_connection() as conn:
        res_count = conn.execute("SELECT COUNT(*) as c FROM cross_bank_budget_reservations;").fetchone()["c"]
        assert res_count == 0

    # Invariant: Despite 7-day audit rows being pruned, rolling-window consumption is preserved for the full 30 days!
    status_8d = budget_store.get_budget(c_id, "bank_a", "bank_b", now_dt=now_8d)
    assert status_8d is not None
    assert status_8d["consumed_executions"] == 1
    assert status_8d["remaining_executions"] == 0

    # Attempting to reserve at day 8 must still be rejected (silent replenishment prevented)
    ok_8d, _, exhausted_8d = budget_store.reserve_batch(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        now_dt=now_8d,
    )
    assert ok_8d is False
    assert exhausted_8d == ["bank_b"]

    # 3. Simulate 31 days passing (exceeding the actual 30-day window)
    now_31d = now_0 + datetime.timedelta(days=31)
    with budget_store._lock, budget_store._get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        budget_store._execute_cleanup_in_conn(conn, now_31d, now_31d.isoformat())
        conn.commit()

    # Invariant: The 30-day window has now genuinely expired -> budget replenishes!
    status_31d = budget_store.get_budget(c_id, "bank_a", "bank_b", now_dt=now_31d)
    assert status_31d is not None
    assert status_31d["consumed_executions"] == 0
    assert status_31d["remaining_executions"] == 1

    # Reservation at day 31 now succeeds cleanly
    ok_31d, bid_31d, exhausted_31d = budget_store.reserve_batch(
        consortium_id=c_id,
        requesting_bank_id="bank_a",
        target_bank_ids=["bank_b"],
        now_dt=now_31d,
    )
    assert ok_31d is True
    assert exhausted_31d == []


def test_concurrent_reconciliation_two_store_instances_idempotent(tmp_path: Path) -> None:
    """Two independent budget store instances pointing to the same SQLite file reconcile concurrently.

    Asserts:
    - Exactly one reservation transitions to FAILED_CLOSED.
    - The permanent consumption ledger increments exactly once.
    - The window-history table contains exactly one execution event.
    - Repeated reconciliation does not increment consumption again.
    - Available budget never exceeds its configured limit.
    """
    db_file = tmp_path / "concurrent_reconcile_budget.sqlite"
    store1 = DurableCrossBankExecutionBudgetStore(db_path=str(db_file))
    store2 = DurableCrossBankExecutionBudgetStore(db_path=str(db_file))

    c_id = "test_consortium_concurrency"
    req_bank = "bank_a"
    counterpart = "bank_b"
    max_executions = 2

    # Configure budget on store1 (shared SQLite file)
    store1.set_budget(
        consortium_id=c_id,
        requesting_bank_id=req_bank,
        counterpart_bank_id=counterpart,
        max_executions=max_executions,
    )

    # Seed exactly one expired RESERVED reservation directly into cross_bank_budget_reservations
    now = datetime.datetime.now(UTC)
    past_iso = (now - datetime.timedelta(seconds=60)).isoformat()
    expired_iso = (now - datetime.timedelta(seconds=10)).isoformat()
    res_id = "expired-res-concurrency-1"
    batch_id = "expired-batch-concurrency-1"

    with sqlite3.connect(str(db_file)) as conn:
        conn.execute(
            """
            INSERT INTO cross_bank_budget_reservations (
                reservation_id, batch_id, consortium_id, requesting_bank_id,
                counterpart_bank_id, status, reserved_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, 'RESERVED', ?, ?);
            """,
            (res_id, batch_id, c_id, req_bank, counterpart, past_iso, expired_iso),
        )
        conn.commit()

    # Concurrently call get_budget() from both store instances
    def call_store1():
        return store1.get_budget(c_id, req_bank, counterpart, now_dt=now)

    def call_store2():
        return store2.get_budget(c_id, req_bank, counterpart, now_dt=now)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(call_store1)
        f2 = executor.submit(call_store2)
        r1 = f1.result()
        r2 = f2.result()

    # Assert: Exactly one reservation transitions to FAILED_CLOSED
    with sqlite3.connect(str(db_file)) as conn:
        conn.row_factory = sqlite3.Row
        res = conn.execute(
            "SELECT status, reason FROM cross_bank_budget_reservations WHERE reservation_id = ?;",
            (res_id,),
        ).fetchone()
        assert res is not None
        assert res["status"] == "FAILED_CLOSED"

        failed_count = conn.execute(
            "SELECT COUNT(*) as c FROM cross_bank_budget_reservations WHERE status = 'FAILED_CLOSED';"
        ).fetchone()["c"]
        assert failed_count == 1

        # Assert: The permanent consumption ledger increments exactly once
        cons_row = conn.execute(
            """
            SELECT total_consumed FROM cross_bank_budget_consumption
            WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
            """,
            (c_id, req_bank, counterpart),
        ).fetchone()
        assert cons_row is not None
        assert cons_row["total_consumed"] == 1

        # Assert: The window-history table contains exactly one execution event
        win_rows = conn.execute(
            """
            SELECT COUNT(*) as c FROM cross_bank_budget_window_executions
            WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
            """,
            (c_id, req_bank, counterpart),
        ).fetchone()["c"]
        assert win_rows == 1

    # Assert: Both concurrent callers observe consistent budget state
    assert r1 is not None and r2 is not None
    assert r1["max_executions"] == max_executions
    assert r2["max_executions"] == max_executions
    assert r1["consumed_executions"] == 1
    assert r2["consumed_executions"] == 1
    assert r1["remaining_executions"] == 1
    assert r2["remaining_executions"] == 1

    # Assert: Available budget never exceeds its configured limit
    assert r1["remaining_executions"] <= max_executions
    assert r1["remaining_executions"] + r1["consumed_executions"] <= max_executions
    assert r2["remaining_executions"] <= max_executions
    assert r2["remaining_executions"] + r2["consumed_executions"] <= max_executions

    # Assert: Repeated reconciliation does not increment consumption again
    r1_repeat = store1.get_budget(c_id, req_bank, counterpart, now_dt=now)
    r2_repeat = store2.get_budget(c_id, req_bank, counterpart, now_dt=now)

    assert r1_repeat is not None and r2_repeat is not None
    assert r1_repeat["consumed_executions"] == 1
    assert r2_repeat["consumed_executions"] == 1
    assert r1_repeat["remaining_executions"] == 1
    assert r2_repeat["remaining_executions"] == 1

    with sqlite3.connect(str(db_file)) as conn:
        conn.row_factory = sqlite3.Row
        cons_row_repeat = conn.execute(
            """
            SELECT total_consumed FROM cross_bank_budget_consumption
            WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
            """,
            (c_id, req_bank, counterpart),
        ).fetchone()
        assert cons_row_repeat is not None
        assert cons_row_repeat["total_consumed"] == 1

        win_rows_repeat = conn.execute(
            """
            SELECT COUNT(*) as c FROM cross_bank_budget_window_executions
            WHERE consortium_id = ? AND requesting_bank_id = ? AND counterpart_bank_id = ?;
            """,
            (c_id, req_bank, counterpart),
        ).fetchone()["c"]
        assert win_rows_repeat == 1

