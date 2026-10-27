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
        for table_name in ("cross_bank_budget_reservations", "cross_bank_budget_consumption", "cross_bank_execution_budgets"):
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

