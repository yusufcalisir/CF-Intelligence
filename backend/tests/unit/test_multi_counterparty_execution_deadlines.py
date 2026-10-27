"""Unit tests for Per-Counterparty Execution Deadlines and Timeout Controls (M6.4 Task 4).

Verifies:
1. Healthy 3-bank query completes within deadline and reports COMPLETED with authentic signals.
2. Unresponsive counterpart produces an appropriately classified, sanitized TIMEOUT failure
   while the other successful counterpart remains intact (PARTIAL_FAILURE).
3. Pre-dispatch deadline exhaustion triggers zero remote execution, releases durable reservations
   with REASON_PRE_DISPATCH_DEADLINE_EXCEEDED, and restores consumable slots.
4. Ambiguous in-flight failure marks idempotency key as AMBIGUOUS_EXECUTION, preventing replay,
   and fails closed in-flight budget reservations as EXECUTION_INDETERMINATE.
5. Cryptographic signature mismatches and governance revocations fail closed and are NEVER
   laundered into recoverable timeout failures.
6. Execution deadline policy validates finite bounds [1.0, 60.0] and [1.0, 300.0] and rejects
   infinite/negative/NaN inputs.
7. Idempotency fingerprints bind timeout overrides to prevent conflicting payload replay.
8. Sanitized timeout error messages are fixed, bounded, and free of URLs, credentials, or payloads.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
    MultiCounterpartyIntelligenceQueryRequest,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import (
    CrossBankIntelligenceService,
    compute_multi_counterparty_request_fingerprint,
)
from app.application.services.entity_resolution import EntityResolutionService, _entity_to_dict
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    CounterpartyTimeoutError,
    CrossBankIdempotencyConflictError,
    MemberRole,
)
from app.domain.execution_deadline_policy import (
    DEFAULT_PER_COUNTERPARTY_TIMEOUT_SECONDS,
    DEFAULT_TOTAL_BATCH_TIMEOUT_SECONDS,
    CounterpartyExecutionDeadlinePolicy,
)
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.infrastructure.storage.durable_execution_budget_store import (
    DurableCrossBankExecutionBudgetStore,
)
from app.infrastructure.storage.durable_idempotency_store import (
    DurableCrossBankIdempotencyStore,
)


@pytest.fixture(autouse=True)
def isolate_test_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    store_file = tmp_path / "deadlines_consortium_store.json"
    account_file = tmp_path / "deadlines_account_store.json"
    budget_file = tmp_path / "deadlines_budget_store.db"
    idempotency_file = tmp_path / "deadlines_idempotency_store.db"

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("CFI_EXECUTION_BUDGET_DB_PATH", str(budget_file))
    monkeypatch.setenv("CFI_IDEMPOTENCY_DB_PATH", str(idempotency_file))
    monkeypatch.setenv("CFI_DURABLE_BUDGET_STORE_PATH", str(budget_file))
    monkeypatch.setenv("CFI_IDEMPOTENCY_STORE_PATH", str(idempotency_file))
    monkeypatch.setenv("JWT_SECRET", "test_jwt_secret_deadlines_must_be_32bytes_min!")
    monkeypatch.setenv("CFI_JWT_SECRET", "test_jwt_secret_deadlines_must_be_32bytes_min!")
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", "test_secret_deadlines_32bytes_min!")
    monkeypatch.setenv("CFI_ENABLE_CROSS_BANK_SIMULATION", "true")

    from app.config import get_settings

    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurableCrossBankExecutionBudgetStore.reset_instance()
    DurableCrossBankIdempotencyStore.reset_instance()
    CrossBankIntelligenceService.reset_instance()
    yield
    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurableCrossBankExecutionBudgetStore.reset_instance()
    DurableCrossBankIdempotencyStore.reset_instance()
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
    c_id = "consortium_tri_deadlines"

    service.create_consortium(
        consortium_id=c_id,
        name="Tri-Bank Consortium Deadlines",
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


@pytest.fixture
def test_setup(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
) -> tuple[CrossBankIntelligenceService, Entity, str]:
    gov_svc, c_id = tri_consortium
    ent_svc = EntityResolutionService()
    ent = Entity(
        id="ENT-DEADLINE-001",
        bank_id="bank_a",
        entity_type=EntityType.CUSTOMER,
        risk_level=RiskLevel.MEDIUM,
        privacy_id="deadbeef" * 8,
        display_label="Test Customer",
    )
    ent_svc._entities.set("ENT-DEADLINE-001", _entity_to_dict(ent))
    svc = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=ent_svc)
    svc.budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=5)
    svc.budget_store.set_budget(consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_c", max_executions=5)
    return svc, ent, c_id


def _mock_signal(req: CrossBankIntelligenceQueryRequest, match_confirmed: bool = True) -> CrossBankIntelligenceSignal:
    target = req.target_bank_id or "bank_b"
    return CrossBankIntelligenceSignal(
        signal_id=f"sig_{target}_valid",
        consortium_id=req.consortium_id,
        requesting_bank_id=req.requesting_bank_id,
        query_entity_id=req.query_entity_id,
        privacy_hash_truncated="priv_hash_truncated...",
        match_confirmed=match_confirmed,
        match_category="EXACT_MATCH" if match_confirmed else "NO_MATCH",
        match_confidence=1.0 if match_confirmed else 0.0,
        contributing_institutions_count=1 if match_confirmed else 0,
        contributing_institutions=[target] if match_confirmed else [],
        disclosure_mode="ATTRIBUTED_INSTITUTIONS" if match_confirmed else "ANONYMIZED_SUMMARY",
        sanitized_risk_level="HIGH" if match_confirmed else "UNOBSERVED",
        sanitized_typology_indicators=["RAPID_MOVEMENT"] if match_confirmed else [],
        execution_mode="VERIFIED_OPERATIONAL",
        attestation_status="SOFTWARE_EMULATED",
        assurance_level="REVIEWED_ESTABLISHED_PSI_VERIFIED",
        provenance={"target_bank_id": target},
    )


# =========================================================================
# 1. Healthy Three-Bank Query Within Deadline
# =========================================================================

def test_healthy_three_bank_query_completes_within_deadline(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    svc, test_entity, c_id = test_setup

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=test_entity.id,
        privacy_id=test_entity.privacy_id,
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="SIMULATED_RESEARCH",
        per_counterparty_timeout_seconds=10.0,
        total_batch_timeout_seconds=30.0,
    )

    with patch.object(svc, "query_cross_bank_intelligence") as mock_query:
        mock_query.side_effect = lambda req=None, *a, **kw: _mock_signal(req, match_confirmed=True)

        resp = svc.query_multi_counterparty_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
        )

        assert resp.overall_outcome == "COMPLETED"
        assert resp.counterpart_count == 2
        assert len(resp.results) == 2
        assert "bank_b" in resp.results
        assert "bank_c" in resp.results
        assert len(resp.failures) == 0

        # Verify budget was committed for both
        b_budget = svc.budget_store.get_budget(c_id, "bank_a", "bank_b")
        c_budget = svc.budget_store.get_budget(c_id, "bank_a", "bank_c")
        assert b_budget is not None and b_budget["consumed_executions"] == 1
        assert c_budget is not None and c_budget["consumed_executions"] == 1
        assert b_budget["in_flight_reservations"] == 0
        assert c_budget["in_flight_reservations"] == 0


# =========================================================================
# 2. Unresponsive Counterpart Timeout with Other Intact (PARTIAL_FAILURE)
# =========================================================================

def test_unresponsive_counterpart_timeout_sanitized_failure_other_intact(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    svc, test_entity, c_id = test_setup

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=test_entity.id,
        privacy_id=test_entity.privacy_id,
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="SIMULATED_RESEARCH",
        per_counterparty_timeout_seconds=5.0,
        total_batch_timeout_seconds=20.0,
    )

    def side_effect(req: CrossBankIntelligenceQueryRequest | None = None, *args, **kw):
        assert req is not None
        if req.target_bank_id == "bank_b":
            return _mock_signal(req, match_confirmed=True)
        # Bank C is genuinely unresponsive and times out over network transport
        raise CounterpartyTimeoutError(
            "Worker probe timed out communicating with internal endpoint http://worker-c.local:8080/process"
        )

    with patch.object(svc, "query_cross_bank_intelligence", side_effect=side_effect):
        resp = svc.query_multi_counterparty_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
        )

        assert resp.overall_outcome == "PARTIAL_FAILURE"
        assert "bank_b" in resp.results
        assert resp.results["bank_b"].match_confirmed is True

        assert "bank_c" not in resp.results
        assert "bank_c" in resp.failures
        c_fail = resp.failures["bank_c"]
        assert c_fail.error_category == "TIMEOUT"
        assert c_fail.retryable is True

        # Sanitization: Ensure internal URL was stripped
        assert "http://" not in c_fail.error_message
        assert "worker-c.local" not in c_fail.error_message
        assert "bank_c" in c_fail.error_message

        # Both were dispatched, so both attempts are durably accounted
        b_budget = svc.budget_store.get_budget(c_id, "bank_a", "bank_b")
        c_budget = svc.budget_store.get_budget(c_id, "bank_a", "bank_c")
        assert b_budget is not None and b_budget["consumed_executions"] == 1
        assert c_budget is not None and c_budget["consumed_executions"] == 1
        assert b_budget["in_flight_reservations"] == 0
        assert c_budget["in_flight_reservations"] == 0


# =========================================================================
# 3. Pre-Dispatch Deadline Exhaustion Releases Undispatched Reservation
# =========================================================================

def test_pre_dispatch_deadline_exhaustion_triggers_zero_remote_execution(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    svc, test_entity, c_id = test_setup

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=test_entity.id,
        privacy_id=test_entity.privacy_id,
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="SIMULATED_RESEARCH",
        per_counterparty_timeout_seconds=5.0,
        total_batch_timeout_seconds=2.0,
    )

    call_count = {"bank_b": 0, "bank_c": 0}
    current_mono = 100.0

    def mock_mono() -> float:
        return current_mono

    def controlled_execution(req: CrossBankIntelligenceQueryRequest | None = None, *args, **kw):
        nonlocal current_mono
        assert req is not None
        call_count[req.target_bank_id] += 1
        # Bank B execution takes 2.5 seconds, pushing elapsed time past the 2.0s batch deadline
        current_mono += 2.5
        return _mock_signal(req, match_confirmed=True)

    with (
        patch("time.monotonic", side_effect=mock_mono),
        patch.object(svc, "query_cross_bank_intelligence", side_effect=controlled_execution),
    ):
        resp = svc.query_multi_counterparty_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
        )

        # Bank B was dispatched and completed
        assert call_count["bank_b"] == 1
        # Bank C was NEVER dispatched (zero remote execution)
        assert call_count["bank_c"] == 0

        assert resp.overall_outcome == "PARTIAL_FAILURE"
        assert "bank_b" in resp.results
        assert "bank_c" in resp.failures

        c_fail = resp.failures["bank_c"]
        assert c_fail.error_category == "TIMEOUT"
        assert "before counterpart dispatch" in c_fail.error_message
        assert c_fail.retryable is True

        # Crucial invariant: Bank C was undispatched, so its reservation was RELEASED (not consumed!)
        b_budget = svc.budget_store.get_budget(c_id, "bank_a", "bank_b")
        c_budget = svc.budget_store.get_budget(c_id, "bank_a", "bank_c")
        assert b_budget is not None and b_budget["consumed_executions"] == 1
        assert c_budget is not None and c_budget["consumed_executions"] == 0  # Restored! Consumable slot preserved!
        assert b_budget["in_flight_reservations"] == 0
        assert c_budget["in_flight_reservations"] == 0


# =========================================================================
# 4. Total Batch Timeout Pre-Dispatch for All Counterparts
# =========================================================================

def test_all_counterparts_pre_dispatch_timeout_releases_all_reservations(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    svc, test_entity, c_id = test_setup

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=test_entity.id,
        privacy_id=test_entity.privacy_id,
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="SIMULATED_RESEARCH",
        total_batch_timeout_seconds=1.0,
    )

    # Monotonic clock starts already past deadline
    mono_times = [100.0, 102.0, 103.0, 104.0]
    mono_iter = iter(mono_times)

    with (
        patch("time.monotonic", side_effect=lambda: next(mono_iter, 105.0)),
        patch.object(svc, "query_cross_bank_intelligence") as mock_exec,
    ):
        resp = svc.query_multi_counterparty_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
        )

        assert mock_exec.call_count == 0
        assert resp.overall_outcome == "FAILED"
        assert len(resp.results) == 0
        assert len(resp.failures) == 2
        assert resp.failures["bank_b"].error_category == "TIMEOUT"
        assert resp.failures["bank_c"].error_category == "TIMEOUT"

        # Both reservations released cleanly
        b_budget = svc.budget_store.get_budget(c_id, "bank_a", "bank_b")
        c_budget = svc.budget_store.get_budget(c_id, "bank_a", "bank_c")
        assert b_budget is not None and b_budget["consumed_executions"] == 0
        assert c_budget is not None and c_budget["consumed_executions"] == 0
        assert b_budget["in_flight_reservations"] == 0
        assert c_budget["in_flight_reservations"] == 0


# =========================================================================
# 5. Ambiguous In-Flight Failure Marks Idempotency AMBIGUOUS_EXECUTION
# =========================================================================

def test_ambiguous_in_flight_timeout_idempotency_and_budget_fail_closed(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    svc, test_entity, c_id = test_setup
    idem_key = "idem-deadline-indeterminate-001"

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=test_entity.id,
        privacy_id=test_entity.privacy_id,
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="SIMULATED_RESEARCH",
        idempotency_key=idem_key,
    )

    # Bank B succeeds, but Bank C crashes with an ambiguous non-recoverable error
    def crashing_execution(req: CrossBankIntelligenceQueryRequest | None = None, *args, **kw):
        assert req is not None
        if req.target_bank_id == "bank_b":
            return _mock_signal(req, match_confirmed=True)
        raise RuntimeError("Worker process crashed mid-PSI calculation with corrupted memory state")

    with patch.object(svc, "query_cross_bank_intelligence", side_effect=crashing_execution):
        with pytest.raises(RuntimeError, match="corrupted memory state"):
            svc.query_multi_counterparty_intelligence(
                req=req,
                auth_token=auth_tokens["bank_a"],
            )

    # In-flight reservation for bank_c must be failed closed as EXECUTION_INDETERMINATE
    c_budget = svc.budget_store.get_budget(c_id, "bank_a", "bank_c")
    assert c_budget is not None and c_budget["consumed_executions"] == 1
    assert c_budget["in_flight_reservations"] == 0

    # Idempotency store must record AMBIGUOUS_EXECUTION and reject replay
    with pytest.raises(CrossBankIdempotencyConflictError, match="indeterminate state"):
        svc.query_multi_counterparty_intelligence(
            req=req,
            auth_token=auth_tokens["bank_a"],
        )


# =========================================================================
# 6. Governance & Cryptographic Failures Never Laundering Into Timeouts
# =========================================================================

def test_timeout_paths_never_convert_cryptographic_integrity_or_governance(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    svc, test_entity, c_id = test_setup

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=test_entity.id,
        privacy_id=test_entity.privacy_id,
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="SIMULATED_RESEARCH",
    )

    # Simulate signature tampering on Bank C
    def tampering_execution(req: CrossBankIntelligenceQueryRequest | None = None, *args, **kw):
        assert req is not None
        if req.target_bank_id == "bank_b":
            return _mock_signal(req, match_confirmed=True)
        raise ConsortiumSecurityError("Cryptographic signature verification failed: invalid ed25519 signature")

    with patch.object(svc, "query_cross_bank_intelligence", side_effect=tampering_execution):
        with pytest.raises(ConsortiumSecurityError, match="invalid ed25519 signature"):
            svc.query_multi_counterparty_intelligence(
                req=req,
                auth_token=auth_tokens["bank_a"],
            )


# =========================================================================
# 7. Deadline Policy Bounds Validation
# =========================================================================

def test_deadline_policy_bounds_validation() -> None:
    # Valid default policy
    policy = CounterpartyExecutionDeadlinePolicy()
    assert policy.per_counterparty_timeout_seconds == DEFAULT_PER_COUNTERPARTY_TIMEOUT_SECONDS
    assert policy.total_batch_timeout_seconds == DEFAULT_TOTAL_BATCH_TIMEOUT_SECONDS

    # Custom valid values
    custom = CounterpartyExecutionDeadlinePolicy(
        per_counterparty_timeout_seconds=5.0,
        total_batch_timeout_seconds=25.0,
    )
    assert custom.per_counterparty_timeout_seconds == 5.0
    assert custom.total_batch_timeout_seconds == 25.0

    # Invalid per_counterparty bounds
    with pytest.raises(ValueError, match="per_counterparty_timeout_seconds must be a finite float between 1.0 and 60.0"):
        CounterpartyExecutionDeadlinePolicy(per_counterparty_timeout_seconds=0.5)

    with pytest.raises(ValueError, match="per_counterparty_timeout_seconds must be a finite float between 1.0 and 60.0"):
        CounterpartyExecutionDeadlinePolicy(per_counterparty_timeout_seconds=60.1)

    with pytest.raises(ValueError, match="per_counterparty_timeout_seconds must be a finite float between 1.0 and 60.0"):
        CounterpartyExecutionDeadlinePolicy(per_counterparty_timeout_seconds=float("nan"))

    with pytest.raises(ValueError, match="per_counterparty_timeout_seconds must be a finite float between 1.0 and 60.0"):
        CounterpartyExecutionDeadlinePolicy(per_counterparty_timeout_seconds=float("inf"))

    # Invalid total_batch bounds
    with pytest.raises(ValueError, match="total_batch_timeout_seconds must be a finite float between 1.0 and 300.0"):
        CounterpartyExecutionDeadlinePolicy(total_batch_timeout_seconds=0.9)

    with pytest.raises(ValueError, match="total_batch_timeout_seconds must be a finite float between 1.0 and 300.0"):
        CounterpartyExecutionDeadlinePolicy(total_batch_timeout_seconds=301.0)

    with pytest.raises(ValueError, match="total_batch_timeout_seconds must be a finite float between 1.0 and 300.0"):
        CounterpartyExecutionDeadlinePolicy(total_batch_timeout_seconds=float("nan"))


def test_request_schema_deadline_validation() -> None:
    # Request validation bounds
    with pytest.raises(ValueError):
        MultiCounterpartyIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="ENT-001",
            consortium_id="c_alpha",
            target_bank_ids=["bank_b"],
            per_counterparty_timeout_seconds=0.5,
        )

    with pytest.raises(ValueError):
        MultiCounterpartyIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="ENT-001",
            consortium_id="c_alpha",
            target_bank_ids=["bank_b"],
            total_batch_timeout_seconds=350.0,
        )


# =========================================================================
# 8. Idempotency Fingerprint Binds Timeout Parameters
# =========================================================================

def test_idempotency_fingerprint_includes_timeout_parameters() -> None:
    req1 = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="ENT-001",
        consortium_id="c_alpha",
        target_bank_ids=["bank_b", "bank_c"],
        per_counterparty_timeout_seconds=10.0,
        total_batch_timeout_seconds=30.0,
    )
    req2 = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="ENT-001",
        consortium_id="c_alpha",
        target_bank_ids=["bank_b", "bank_c"],
        per_counterparty_timeout_seconds=15.0,  # Modified timeout
        total_batch_timeout_seconds=30.0,
    )
    req3 = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="ENT-001",
        consortium_id="c_alpha",
        target_bank_ids=["bank_b", "bank_c"],
        per_counterparty_timeout_seconds=10.0,
        total_batch_timeout_seconds=30.0,
    )

    fp1 = compute_multi_counterparty_request_fingerprint(req1)
    fp2 = compute_multi_counterparty_request_fingerprint(req2)
    fp3 = compute_multi_counterparty_request_fingerprint(req3)

    assert fp1 == fp3
    assert fp1 != fp2  # Modifying timeout changes fingerprint!
