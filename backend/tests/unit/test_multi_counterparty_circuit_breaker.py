"""Unit and Integration Tests for Durable Per-Counterparty Circuit Breaker and Recovery Admission.

Adheres strictly to .agents/AGENTS.md, runtime truth, and M6.4 Task 5 requirements:
- Configurable circuit breaker scoped to consortium_id, requesting_bank_id, and target_bank_id.
- States: CLOSED, OPEN, HALF_OPEN.
- Only eligible transport/timeout/worker-unavailability errors count toward opening.
- Security, governance, cryptographic, and admission rejections never open circuit.
- Open circuit rejects dispatch before sending any PSI request.
- Preserves partial failure contract (healthy counterparts succeed).
- Admits at most one recovery probe across concurrent service instances in HALF_OPEN.
- Closes circuit only after authenticated, cryptographically verified healthy execution.
- Integrates with durable execution budgets: does not consume budget on pre-dispatch circuit rejection.
- Preserves idempotency: replay does not initiate recovery probes or consume budget.
- Stores strictly bounded diagnostic categories with zero sensitive data.
"""

from __future__ import annotations

import datetime
import time
from collections.abc import Generator
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
    MultiCounterpartyIntelligenceQueryRequest,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import (
    EntityResolutionService,
    _entity_to_dict,
)
from app.config import get_settings
from app.domain.circuit_breaker_policy import (
    CounterpartyCircuitBreakerOpenError,
    CounterpartyCircuitBreakerPolicy,
)
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    CounterpartyWorkerUnavailableError,
    MemberRole,
)
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_circuit_breaker_store import (
    ALLOWED_CIRCUIT_BREAKER_REASONS,
    DurableCrossBankCircuitBreakerStore,
    sanitize_circuit_breaker_reason,
)
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
    store_file = tmp_path / "cb_consortium_store.json"
    account_file = tmp_path / "cb_account_store.json"
    budget_file = tmp_path / "cb_budget_store.db"
    idempotency_file = tmp_path / "cb_idempotency_store.db"
    circuit_breaker_file = tmp_path / "cb_circuit_breaker_store.db"

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("CFI_EXECUTION_BUDGET_DB_PATH", str(budget_file))
    monkeypatch.setenv("CFI_IDEMPOTENCY_DB_PATH", str(idempotency_file))
    monkeypatch.setenv("CFI_CIRCUIT_BREAKER_DB_PATH", str(circuit_breaker_file))
    monkeypatch.setenv("CFI_DURABLE_BUDGET_STORE_PATH", str(budget_file))
    monkeypatch.setenv("CFI_IDEMPOTENCY_STORE_PATH", str(idempotency_file))
    monkeypatch.setenv("JWT_SECRET", "test_jwt_secret_cb_must_be_32bytes_min!")
    monkeypatch.setenv("CFI_JWT_SECRET", "test_jwt_secret_cb_must_be_32bytes_min!")
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", "test_secret_cb_32bytes_min!")
    monkeypatch.setenv("CFI_ENABLE_CROSS_BANK_SIMULATION", "true")

    from app.config import get_settings

    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurableCrossBankExecutionBudgetStore.reset_instance()
    DurableCrossBankIdempotencyStore.reset_instance()
    DurableCrossBankCircuitBreakerStore.reset_instance()
    CrossBankIntelligenceService.reset_instance()
    yield
    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurableCrossBankExecutionBudgetStore.reset_instance()
    DurableCrossBankIdempotencyStore.reset_instance()
    DurableCrossBankCircuitBreakerStore.reset_instance()
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
    c_id = "consortium_tri_cb"

    service.create_consortium(
        consortium_id=c_id,
        name="Tri-Bank Consortium Circuit Breaker",
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
        id="ENT-CB-001",
        bank_id="bank_a",
        entity_type=EntityType.CUSTOMER,
        risk_level=RiskLevel.MEDIUM,
        privacy_id="deadbeef" * 8,
        display_label="Test Customer",
    )
    ent_svc._entities.set("ENT-CB-001", _entity_to_dict(ent))
    svc = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=ent_svc)
    svc.budget_store.set_budget(
        consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_b", max_executions=10
    )
    svc.budget_store.set_budget(
        consortium_id=c_id, requesting_bank_id="bank_a", counterpart_bank_id="bank_c", max_executions=10
    )
    return svc, ent, c_id


def _mock_signal(
    req: CrossBankIntelligenceQueryRequest | None = None,
    *a: Any,
    match_confirmed: bool = True,
    **kw: Any,
) -> CrossBankIntelligenceSignal:
    assert req is not None
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
        disclosure_mode="ATTRIBUTED_INSTITUTIONS",
        sanitized_risk_level="HIGH" if match_confirmed else "UNOBSERVED",
        sanitized_typology_indicators=["CROSS_BANK_ENTITY_OBSERVED"] if match_confirmed else [],
        graph_context={"has_incident_cross_bank_correlations": match_confirmed, "sanitized_degree_band": "BILATERAL"},
        provenance={
            "query_timestamp": datetime.datetime.now(UTC).isoformat(),
            "earliest_consortium_observation": datetime.datetime.now(UTC).isoformat(),
            "latest_consortium_observation": datetime.datetime.now(UTC).isoformat(),
            "governance_authority": f"consortium:{req.consortium_id}",
            "execution_environment": "VERIFIED_OPERATIONAL",
            "operational_status": "OPERATIONAL",
        },
        execution_mode=req.execution_mode,
        attestation_status="SOFTWARE_EMULATED",
        is_authoritative=True,
        is_synthetic=False,
    )


# ==============================================================================
# Acceptance Tests
# ==============================================================================


def test_default_behavior_preserves_unconstrained_execution_when_disabled(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    """Requirement: Default behavior must preserve existing unconstrained deployments unless explicitly enabled."""
    svc, ent, c_id = test_setup
    principal = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst", "investigator"],
        is_authenticated=True,
    )

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        execution_mode="SIMULATED_RESEARCH",
    )
    assert req.circuit_breaker_enabled is None

    with patch.object(svc, "query_cross_bank_intelligence", side_effect=_mock_signal) as mock_dispatch:
        resp = svc.query_multi_counterparty_intelligence(req=req, principal=principal)
        assert resp.overall_outcome == "COMPLETED"
        assert resp.execution_outcomes["bank_b"].status == "SUCCESS"
        assert resp.execution_outcomes["bank_c"].status == "SUCCESS"
        assert mock_dispatch.call_count == 2

    # Circuit breaker store has 0 records because breaker was never enabled
    cb_state_b = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_b")
    assert cb_state_b["state"] == "CLOSED"
    assert cb_state_b["consecutive_failures"] == 0


def test_repeated_eligible_transport_failures_open_only_affected_target(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    """Requirement: Repeated eligible transport/timeout failures open only the affected counterpart's circuit."""
    svc, ent, c_id = test_setup
    principal = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    def dispatch_side_effect(req: CrossBankIntelligenceQueryRequest, **kwargs: Any) -> CrossBankIntelligenceSignal:
        if req.target_bank_id == "bank_c":
            raise CounterpartyWorkerUnavailableError("Worker on bank_c connection refused.")
        return _mock_signal(req)

    # Threshold is 3 consecutive failures
    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        circuit_breaker_enabled=True,
        circuit_breaker_failure_threshold=3,
        circuit_breaker_cooldown_seconds=60.0,
        execution_mode="SIMULATED_RESEARCH",
    )

    with patch.object(svc, "query_cross_bank_intelligence", side_effect=dispatch_side_effect):
        # Query 1: failure 1 for bank_c
        resp1 = svc.query_multi_counterparty_intelligence(req=req, principal=principal)
        assert resp1.overall_outcome == "PARTIAL_FAILURE"
        assert resp1.execution_outcomes["bank_b"].status == "SUCCESS"
        assert resp1.execution_outcomes["bank_c"].status == "FAILED"
        cb_c = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")
        assert cb_c["state"] == "CLOSED"
        assert cb_c["consecutive_failures"] == 1

        # Query 2: failure 2 for bank_c
        resp2 = svc.query_multi_counterparty_intelligence(req=req, principal=principal)
        assert resp2.overall_outcome == "PARTIAL_FAILURE"
        cb_c = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")
        assert cb_c["state"] == "CLOSED"
        assert cb_c["consecutive_failures"] == 2

        # Query 3: failure 3 for bank_c -> opens circuit!
        resp3 = svc.query_multi_counterparty_intelligence(req=req, principal=principal)
        assert resp3.overall_outcome == "PARTIAL_FAILURE"
        cb_c = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")
        assert cb_c["state"] == "OPEN"
        assert cb_c["consecutive_failures"] == 3
        assert cb_c["last_failure_reason"] == "WORKER_UNAVAILABLE"

        # Bank B remains healthy and CLOSED
        cb_b = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_b")
        assert cb_b["state"] == "CLOSED"
        assert cb_b["consecutive_failures"] == 0


def test_open_circuit_blocks_dispatch_before_psi_and_releases_budget(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    """Requirement: When circuit is OPEN, reject dispatch before sending any PSI request without consuming budget."""
    svc, ent, c_id = test_setup
    principal = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=2,
        cooldown_seconds=60.0,
    )

    # Record 2 failures directly into store to open Bank C's circuit
    svc.circuit_breaker_store.record_failure(c_id, "bank_a", "bank_c", "TIMEOUT", policy=policy)
    svc.circuit_breaker_store.record_failure(c_id, "bank_a", "bank_c", "TIMEOUT", policy=policy)

    state_c = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")
    assert state_c["state"] == "OPEN"

    # Set budgets to observe consumption
    svc.budget_store.set_budget(c_id, "bank_a", "bank_b", max_executions=10)
    svc.budget_store.set_budget(c_id, "bank_a", "bank_c", max_executions=10)

    # Pre-execution budget check
    b_c_before = svc.budget_store.get_budget(c_id, "bank_a", "bank_c")
    assert b_c_before is not None
    assert b_c_before["consumed_executions"] == 0
    assert b_c_before["in_flight_reservations"] == 0

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        circuit_breaker_enabled=True,
        circuit_breaker_failure_threshold=2,
        circuit_breaker_cooldown_seconds=60.0,
        execution_mode="SIMULATED_RESEARCH",
    )

    dispatched_targets: list[str] = []

    def mock_dispatch(req: CrossBankIntelligenceQueryRequest, **kwargs: Any) -> CrossBankIntelligenceSignal:
        dispatched_targets.append(str(req.target_bank_id))
        return _mock_signal(req)

    with patch.object(svc, "query_cross_bank_intelligence", side_effect=mock_dispatch):
        resp = svc.query_multi_counterparty_intelligence(req=req, principal=principal)

        # Bank C was NEVER dispatched!
        assert "bank_c" not in dispatched_targets
        assert dispatched_targets == ["bank_b"]

        # Outcomes: Bank B succeeded, Bank C failed cleanly with CIRCUIT_BREAKER_OPEN
        assert resp.overall_outcome == "PARTIAL_FAILURE"
        assert resp.execution_outcomes["bank_b"].status == "SUCCESS"

        c_outcome = resp.execution_outcomes["bank_c"]
        assert c_outcome.status == "FAILED"
        assert c_outcome.signal is None
        assert c_outcome.failure is not None
        assert c_outcome.failure.error_category == "CIRCUIT_BREAKER_OPEN"
        assert "cooldown in progress" in c_outcome.failure.error_message
        assert c_outcome.failure.retryable is False

        # Budget verification: Bank C reservation was released, consumed is still 0!
        b_c_after = svc.budget_store.get_budget(c_id, "bank_a", "bank_c")
        assert b_c_after is not None
        assert b_c_after["consumed_executions"] == 0
        assert b_c_after["in_flight_reservations"] == 0

        # Bank B consumed 1 execution
        b_b_after = svc.budget_store.get_budget(c_id, "bank_a", "bank_b")
        assert b_b_after is not None
        assert b_b_after["consumed_executions"] == 1
        assert b_b_after["in_flight_reservations"] == 0


def test_concurrent_service_instances_enforce_single_recovery_probe_gate(
    tmp_path: Path,
) -> None:
    """Requirement: Concurrent service instances cannot admit multiple half-open recovery probes."""
    shared_db = str(tmp_path / "shared_cb_store.db")

    # Instance 1 and Instance 2 share the exact same SQLite WAL database file
    store1 = DurableCrossBankCircuitBreakerStore(db_path=shared_db)
    store2 = DurableCrossBankCircuitBreakerStore(db_path=shared_db)

    policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=1,
        cooldown_seconds=0.05,  # 50ms cooldown gives ample time to verify OPEN state before expiry
        half_open_probe_timeout_seconds=30.0,
    )

    # Open the circuit via store1
    store1.record_failure("consortium_alpha", "bank_a", "bank_c", "TIMEOUT", policy=policy)
    st = store1.get_circuit_state("consortium_alpha", "bank_a", "bank_c")
    assert st["state"] == "OPEN"

    # Sleep slightly so cooldown expires
    time.sleep(0.06)

    # Store 1 attempts to dispatch -> cooldown expired -> admitted as single recovery probe
    allowed1, state1, probe1 = store1.evaluate_and_acquire_dispatch(
        "consortium_alpha", "bank_a", "bank_c", policy=policy
    )
    assert allowed1 is True
    assert state1 == "HALF_OPEN"
    assert probe1 is not None

    # Concurrently, Store 2 attempts to dispatch to the same counterpart
    allowed2, state2, reason2 = store2.evaluate_and_acquire_dispatch(
        "consortium_alpha", "bank_a", "bank_c", policy=policy
    )
    # Store 2 MUST be rejected because probe 1 is actively in flight!
    assert allowed2 is False
    assert state2 == "HALF_OPEN"
    assert reason2 == "recovery probe currently in flight"


def test_successful_verified_recovery_closes_circuit(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    """Requirement: A valid verified operational recovery closes the circuit; subsequent requests execute normally."""
    svc, ent, c_id = test_setup
    principal = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=1,
        cooldown_seconds=0.05,
    )

    # 1. Open circuit
    svc.circuit_breaker_store.record_failure(c_id, "bank_a", "bank_c", "TIMEOUT", policy=policy)
    assert svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")["state"] == "OPEN"

    # 2. Wait for cooldown expiry
    time.sleep(0.06)

    # 3. Execute query: Bank C is admitted as probe in VERIFIED_OPERATIONAL mode and succeeds
    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_ids=["bank_c"],
        circuit_breaker_enabled=True,
        circuit_breaker_cooldown_seconds=0.05,
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(svc, "_execute_verified_operational_psi", side_effect=_mock_signal) as mock_dispatch:
        resp = svc.query_multi_counterparty_intelligence(req=req, principal=principal)
        assert resp.overall_outcome == "COMPLETED"
        assert resp.execution_outcomes["bank_c"].status == "SUCCESS"
        assert mock_dispatch.call_count == 1

    # Circuit is now CLOSED and consecutive failures reset to 0
    st = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")
    assert st["state"] == "CLOSED"
    assert st["consecutive_failures"] == 0
    assert st["half_open_probe_id"] is None

    # Next query executes cleanly without probe
    with patch.object(svc, "_execute_verified_operational_psi", side_effect=_mock_signal) as mock_dispatch2:
        resp2 = svc.query_multi_counterparty_intelligence(req=req, principal=principal)
        assert resp2.overall_outcome == "COMPLETED"
        assert mock_dispatch2.call_count == 1


def test_simulated_research_cannot_transition_half_open_to_closed(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    """Correction B: Simulated or unverified research results cannot transition HALF_OPEN circuit to CLOSED."""
    svc, ent, c_id = test_setup
    principal = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=1,
        cooldown_seconds=0.02,
    )

    # 1. Open circuit
    svc.circuit_breaker_store.record_failure(c_id, "bank_a", "bank_c", "TIMEOUT", policy=policy)
    time.sleep(0.03)

    # 2. Attempt multi-counterparty query in SIMULATED_RESEARCH mode
    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_ids=["bank_c"],
        circuit_breaker_enabled=True,
        circuit_breaker_cooldown_seconds=0.02,
        execution_mode="SIMULATED_RESEARCH",
    )

    resp = svc.query_multi_counterparty_intelligence(req=req, principal=principal)
    assert "bank_c" in resp.failures
    assert resp.failures["bank_c"].error_category == "CIRCUIT_BREAKER_OPEN"
    # Circuit MUST NOT be CLOSED by simulated research
    st = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")
    assert st["state"] != "CLOSED"
    assert st["state"] == "OPEN"

    # 3. Even if a HALF_OPEN probe lease is active, an unverified/simulated signal MUST abort the probe rather than close it
    admitted, state, probe_id = svc.circuit_breaker_store.evaluate_and_acquire_dispatch(
        c_id, "bank_a", "bank_c", policy=policy, allow_probe=True
    )
    assert admitted is True
    assert state == "HALF_OPEN"
    assert probe_id is not None
    st_half = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")
    assert st_half["state"] == "HALF_OPEN"

    single_req = CrossBankIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_id="bank_c",
        execution_mode="SIMULATED_RESEARCH",
    )
    sim_signal = _mock_signal(single_req)
    sim_signal.execution_mode = "SIMULATED_RESEARCH"
    sim_signal.is_synthetic = True
    sim_signal.is_authoritative = False
    assert svc._is_verified_operational_signal(sim_signal) is False

    if svc._is_verified_operational_signal(sim_signal):
        svc.circuit_breaker_store.record_success(c_id, "bank_a", "bank_c", probe_id=probe_id)
    else:
        svc.circuit_breaker_store.record_probe_aborted(
            c_id, "bank_a", "bank_c", probe_id=probe_id, reason="SECURITY_REJECTED", policy=policy
        )

    st_after = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")
    assert st_after["state"] == "OPEN"
    assert st_after["state"] != "CLOSED"


def test_failed_recovery_probe_safely_reopens_circuit(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    """Requirement: A failed recovery probe safely reopens the circuit with a fresh cooldown."""
    svc, ent, c_id = test_setup
    principal = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=1,
        cooldown_seconds=0.2,
    )

    # 1. Open circuit
    svc.circuit_breaker_store.record_failure(c_id, "bank_a", "bank_c", "TIMEOUT", policy=policy)
    assert svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")["state"] == "OPEN"

    # 2. Wait for cooldown expiry
    time.sleep(0.25)

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_ids=["bank_c"],
        circuit_breaker_enabled=True,
        circuit_breaker_cooldown_seconds=60.0,  # fresh 60s cooldown on failure
        execution_mode="VERIFIED_OPERATIONAL",
    )

    # Operational probe fails with worker unavailable error
    with patch.object(
        svc,
        "_execute_verified_operational_psi",
        side_effect=CounterpartyWorkerUnavailableError("Worker still dead."),
    ):
        resp = svc.query_multi_counterparty_intelligence(req=req, principal=principal)
        assert resp.overall_outcome == "FAILED"
        assert resp.execution_outcomes["bank_c"].status == "FAILED"

    # Circuit safely re-opened!
    st = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")
    assert st["state"] == "OPEN"
    assert st["consecutive_failures"] == 2
    assert st["half_open_probe_id"] is None
    assert st["last_failure_reason"] == "WORKER_UNAVAILABLE"


def test_security_and_governance_failures_do_not_open_circuit(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    """Requirement: Cryptographic, governance, authorization, consent, and integrity failures must
    never be treated as evidence of worker availability or open the circuit."""
    svc, ent, c_id = test_setup
    principal = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_ids=["bank_c"],
        circuit_breaker_enabled=True,
        circuit_breaker_failure_threshold=1,
        execution_mode="SIMULATED_RESEARCH",
    )

    # Simulate security error (e.g. ticket signature verification mismatch)
    with patch.object(
        svc,
        "query_cross_bank_intelligence",
        side_effect=ConsortiumSecurityError("Ed25519 signature verification failed on ticket."),
    ):
        with pytest.raises(ConsortiumSecurityError, match="signature verification failed"):
            svc.query_multi_counterparty_intelligence(req=req, principal=principal)

    # Verify that circuit breaker is still CLOSED and has 0 failures recorded
    st = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")
    assert st["state"] == "CLOSED"
    assert st["consecutive_failures"] == 0


def test_idempotency_replay_does_not_trigger_recovery_probe_or_consume_budget(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    """Requirement: Idempotency replay of completed request must not initiate new recovery probe or consume quota."""
    svc, ent, c_id = test_setup
    principal = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        idempotency_key="cb_idem_test_key_001",
        circuit_breaker_enabled=True,
        execution_mode="SIMULATED_RESEARCH",
    )

    # Set budgets to observe consumption
    svc.budget_store.set_budget(c_id, "bank_a", "bank_b", max_executions=10)
    svc.budget_store.set_budget(c_id, "bank_a", "bank_c", max_executions=10)

    with patch.object(svc, "query_cross_bank_intelligence", side_effect=_mock_signal) as mock_dispatch:
        # First execution succeeds
        resp1 = svc.query_multi_counterparty_intelligence(req=req, principal=principal)
        assert resp1.overall_outcome == "COMPLETED"
        assert mock_dispatch.call_count == 2

    b_c_before = svc.budget_store.get_budget(c_id, "bank_a", "bank_c")
    assert b_c_before is not None
    assert b_c_before["consumed_executions"] == 1

    # Now transition Bank C to HALF_OPEN directly
    svc.circuit_breaker_store.record_failure(
        c_id,
        "bank_a",
        "bank_c",
        "TIMEOUT",
        policy=CounterpartyCircuitBreakerPolicy(enabled=True, failure_threshold=1, cooldown_seconds=0.001),
    )
    time.sleep(0.01)

    # Replay with same idempotency key
    with patch.object(svc, "query_cross_bank_intelligence") as mock_dispatch_replay:
        resp2 = svc.query_multi_counterparty_intelligence(req=req, principal=principal)
        # Idempotency replay returned cached result
        assert getattr(resp2, "_idempotency_replayed", False) is True
        assert resp2.overall_outcome == "COMPLETED"
        # Zero dispatch executed!
        assert mock_dispatch_replay.call_count == 0

    # No recovery probe was acquired
    st = svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")
    assert st["half_open_probe_id"] is None

    # Quota was NOT consumed again
    b_c_after = svc.budget_store.get_budget(c_id, "bank_a", "bank_c")
    assert b_c_after is not None
    assert b_c_after["consumed_executions"] == b_c_before["consumed_executions"]


def test_stale_recovery_probe_reclaimed_after_timeout(
    tmp_path: Path,
) -> None:
    """Correction D: An in-flight recovery probe preserves a blocked state and prevents overlapping probe admission."""
    db_file = str(tmp_path / "stale_probe_test.db")
    store = DurableCrossBankCircuitBreakerStore(db_path=db_file)

    policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=1,
        cooldown_seconds=0.01,
        half_open_probe_timeout_seconds=0.03,  # short 30ms lease
    )

    store.record_failure("c1", "bank_a", "bank_c", "TIMEOUT", policy=policy)
    time.sleep(0.02)  # expire cooldown

    # Acquire probe 1
    allowed1, state1, probe1 = store.evaluate_and_acquire_dispatch("c1", "bank_a", "bank_c", policy=policy)
    assert allowed1 is True
    assert state1 == "HALF_OPEN"
    assert probe1 is not None

    # Immediate second call is rejected
    allowed2, state2, reason2 = store.evaluate_and_acquire_dispatch("c1", "bank_a", "bank_c", policy=policy)
    assert allowed2 is False
    assert state2 == "HALF_OPEN"

    # Wait past probe lease timeout: Correction D ensures second probe is NOT automatically admitted
    time.sleep(0.05)

    # In-flight recovery probe preserves blocked state; caller 3 is NOT admitted
    allowed3, state3, reason3 = store.evaluate_and_acquire_dispatch("c1", "bank_a", "bank_c", policy=policy)
    assert allowed3 is False
    assert state3 == "HALF_OPEN"
    assert "currently in flight" in reason3

    # Only explicit lifecycle resolution (e.g. failure or reset) resolves the lease
    assert store.record_failure("c1", "bank_a", "bank_c", "TIMEOUT", probe_id=probe1, policy=policy) is True
    assert store.get_circuit_state("c1", "bank_a", "bank_c")["state"] == "OPEN"


def test_single_counterparty_query_respects_circuit_breaker(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    """Requirement: Direct single-target queries also enforce circuit breaker and raise CounterpartyCircuitBreakerOpenError."""
    svc, ent, c_id = test_setup
    principal = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=1,
        cooldown_seconds=60.0,
    )
    svc.circuit_breaker_store.record_failure(c_id, "bank_a", "bank_c", "TIMEOUT", policy=policy)

    single_req = CrossBankIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_id="bank_c",
        circuit_breaker_enabled=True,
        execution_mode="SIMULATED_RESEARCH",
    )

    with pytest.raises(CounterpartyCircuitBreakerOpenError, match="Circuit breaker is OPEN"):
        svc.query_cross_bank_intelligence(req=single_req, principal=principal)


def test_storage_diagnostic_category_sanitization() -> None:
    """Requirement: Diagnostic categories are strictly sanitized, preventing leakage of raw exception text or secrets."""
    assert sanitize_circuit_breaker_reason("Timeout connecting to https://secret.bank.internal/psi") == "TIMEOUT"
    assert sanitize_circuit_breaker_reason("Connection refused to 10.0.0.5:8080") == "WORKER_UNAVAILABLE"
    assert sanitize_circuit_breaker_reason("HTTP 502 Bad Gateway") == "TRANSPORT_FAILURE"
    assert sanitize_circuit_breaker_reason("Random garbage string") == "INDETERMINATE"
    assert sanitize_circuit_breaker_reason(None) == "INDETERMINATE"

    for cat in ALLOWED_CIRCUIT_BREAKER_REASONS:
        assert sanitize_circuit_breaker_reason(cat) == cat


def test_policy_bounds_validation() -> None:
    """Requirement: Policy enforces strict, finite, bounded parameters."""
    with pytest.raises(ValueError, match="failure_threshold must be an integer between 1 and 50"):
        CounterpartyCircuitBreakerPolicy(failure_threshold=0)

    with pytest.raises(ValueError, match="failure_threshold must be an integer between 1 and 50"):
        CounterpartyCircuitBreakerPolicy(failure_threshold=51)

    with pytest.raises(ValueError, match="cooldown_seconds must be a finite float between 0.001 and 3600.0"):
        CounterpartyCircuitBreakerPolicy(cooldown_seconds=0.0)

    with pytest.raises(ValueError, match="cooldown_seconds must be a finite float between 0.001 and 3600.0"):
        CounterpartyCircuitBreakerPolicy(cooldown_seconds=float("inf"))

    with pytest.raises(ValueError, match="half_open_probe_timeout_seconds must be a finite float"):
        CounterpartyCircuitBreakerPolicy(half_open_probe_timeout_seconds=0.0)


def test_independent_store_in_flight_recovery_blocks_second_dispatch_after_deadline(
    tmp_path: Path,
) -> None:
    """Correction D: An in-flight recovery probe across independent stores blocks second dispatch even after lease deadline."""
    db_file = str(tmp_path / "shared_stale_probe.db")
    store1 = DurableCrossBankCircuitBreakerStore(db_path=db_file)
    store2 = DurableCrossBankCircuitBreakerStore(db_path=db_file)

    policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=1,
        cooldown_seconds=0.01,
        half_open_probe_timeout_seconds=0.03,
    )

    store1.record_failure("c1", "bank_a", "bank_c", "TIMEOUT", policy=policy)
    time.sleep(0.02)

    # Store 1 acquires recovery probe
    allowed1, state1, probe1 = store1.evaluate_and_acquire_dispatch("c1", "bank_a", "bank_c", policy=policy)
    assert allowed1 is True
    assert state1 == "HALF_OPEN"
    assert probe1 is not None

    # Wait past lease deadline (30ms)
    time.sleep(0.05)

    # Store 2 attempts dispatch: MUST NOT be admitted as a replacement probe
    allowed2, state2, reason2 = store2.evaluate_and_acquire_dispatch("c1", "bank_a", "bank_c", policy=policy)
    assert allowed2 is False
    assert state2 == "HALF_OPEN"
    assert "currently in flight" in reason2


def test_server_authoritative_circuit_policy_cannot_be_disabled_or_weakened(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Correction A: Server-authoritative circuit policy prevents client from disabling or weakening an enabled breaker."""
    svc, ent, c_id = test_setup
    principal = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    # Enforce server-side circuit breaker settings
    monkeypatch.setenv("CFI_CIRCUIT_BREAKER_ENABLED", "true")
    monkeypatch.setenv("CFI_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("CFI_CIRCUIT_BREAKER_COOLDOWN_SECONDS", "30.0")
    get_settings.cache_clear()

    server_policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=2,
        cooldown_seconds=30.0,
    )
    svc.circuit_breaker_store.record_failure(c_id, "bank_a", "bank_c", "TIMEOUT", policy=server_policy)
    svc.circuit_breaker_store.record_failure(c_id, "bank_a", "bank_c", "TIMEOUT", policy=server_policy)
    assert svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")["state"] == "OPEN"

    # Client attempts to bypass by supplying circuit_breaker_enabled=False and weakening threshold/cooldown
    multi_req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_ids=["bank_c"],
        circuit_breaker_enabled=False,
        circuit_breaker_failure_threshold=50,
        circuit_breaker_cooldown_seconds=0.001,
        execution_mode="SIMULATED_RESEARCH",
    )
    resolved_multi = svc._resolve_circuit_breaker_policy(multi_req)
    assert resolved_multi.enabled is True
    assert resolved_multi.failure_threshold == 2
    assert resolved_multi.cooldown_seconds == 30.0

    resp = svc.query_multi_counterparty_intelligence(req=multi_req, principal=principal)
    assert resp.overall_outcome == "FAILED"
    assert resp.execution_outcomes["bank_c"].status == "FAILED"
    assert resp.execution_outcomes["bank_c"].failure.error_category == "CIRCUIT_BREAKER_OPEN"

    # Single-counterparty request attempting client bypass
    single_req = CrossBankIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_id="bank_c",
        circuit_breaker_enabled=False,
        circuit_breaker_failure_threshold=50,
        circuit_breaker_cooldown_seconds=0.001,
        execution_mode="SIMULATED_RESEARCH",
    )
    resolved_single = svc._resolve_circuit_breaker_policy(single_req)
    assert resolved_single.enabled is True
    assert resolved_single.failure_threshold == 2
    assert resolved_single.cooldown_seconds == 30.0

    with pytest.raises(CounterpartyCircuitBreakerOpenError):
        svc.query_cross_bank_intelligence(req=single_req, principal=principal)
    get_settings.cache_clear()


def test_fenced_recovery_transitions_reject_superseded_or_mismatched_probes(
    tmp_path: Path,
) -> None:
    """Correction C: Late, superseded, missing, or incorrect probe tokens cannot mutate state."""
    db_file = str(tmp_path / "fencing_test.db")
    store = DurableCrossBankCircuitBreakerStore(db_path=db_file)

    policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=1,
        cooldown_seconds=0.01,
    )

    # 1. Open circuit
    store.record_failure("c1", "bank_a", "bank_c", "TIMEOUT", policy=policy)
    time.sleep(0.02)

    # 2. Admit active probe Gen-1
    allowed, state, gen1_probe = store.evaluate_and_acquire_dispatch("c1", "bank_a", "bank_c", policy=policy)
    assert allowed is True
    assert gen1_probe is not None

    # 3. Old or invalid probe attempts record_success() -> MUST FAIL and NOT close the circuit
    assert store.record_success("c1", "bank_a", "bank_c", probe_id="stale-old-probe") is False
    assert store.record_success("c1", "bank_a", "bank_c", probe_id=None) is False
    assert store.get_circuit_state("c1", "bank_a", "bank_c")["state"] == "HALF_OPEN"

    # 4. Old probe attempts record_probe_aborted() -> MUST FAIL and NOT clear Gen-1's lease
    assert store.record_probe_aborted("c1", "bank_a", "bank_c", probe_id="stale-old-probe") is False
    assert store.get_circuit_state("c1", "bank_a", "bank_c")["half_open_probe_id"] == gen1_probe

    # 5. Old probe attempts record_failure() -> MUST FAIL and NOT clear Gen-1's lease
    assert store.record_failure("c1", "bank_a", "bank_c", "TIMEOUT", probe_id="stale-old-probe") is False
    assert store.get_circuit_state("c1", "bank_a", "bank_c")["half_open_probe_id"] == gen1_probe

    # 6. Legitimate Gen-1 probe records failure -> Successfully transitions to OPEN
    assert store.record_failure("c1", "bank_a", "bank_c", "TIMEOUT", probe_id=gen1_probe, policy=policy) is True
    assert store.get_circuit_state("c1", "bank_a", "bank_c")["state"] == "OPEN"

    # 7. Late Gen-1 success callback arriving while OPEN -> MUST FAIL and NOT close the circuit
    assert store.record_success("c1", "bank_a", "bank_c", probe_id=gen1_probe) is False
    assert store.get_circuit_state("c1", "bank_a", "bank_c")["state"] == "OPEN"


def test_single_target_resolves_probe_lifecycle_and_nested_calls_do_not_double_acquire(
    test_setup: tuple[CrossBankIntelligenceService, Entity, str],
    auth_tokens: dict[str, str],
) -> None:
    """Correction E: Single-target requests resolve probe lifecycle, and nested calls do not double-acquire."""
    svc, ent, c_id = test_setup
    principal = AuthenticatedPrincipal(
        principal_id="user_a",
        tenant_id="bank_a",
        roles=["analyst"],
        is_authenticated=True,
    )

    policy = CounterpartyCircuitBreakerPolicy(
        enabled=True,
        failure_threshold=1,
        cooldown_seconds=0.01,
    )

    # 1. Open circuit
    svc.circuit_breaker_store.record_failure(c_id, "bank_a", "bank_c", "TIMEOUT", policy=policy)
    time.sleep(0.02)

    # 2. Single-target request executes in VERIFIED_OPERATIONAL mode with mocked operational PSI
    single_req = CrossBankIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_id="bank_c",
        circuit_breaker_enabled=True,
        circuit_breaker_cooldown_seconds=0.01,
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(svc, "_execute_verified_operational_psi", side_effect=_mock_signal):
        sig = svc.query_cross_bank_intelligence(req=single_req, principal=principal)
        assert sig is not None

    # Single-target query resolved its own probe: circuit is now CLOSED!
    assert svc.circuit_breaker_store.get_circuit_state(c_id, "bank_a", "bank_c")["state"] == "CLOSED"

    # 3. Open circuit again
    svc.circuit_breaker_store.record_failure(c_id, "bank_a", "bank_c", "TIMEOUT", policy=policy)
    time.sleep(0.02)

    # 4. Multi-counterparty request calling nested query_cross_bank_intelligence in SIMULATED_RESEARCH
    multi_req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id=ent.id,
        consortium_id=c_id,
        target_bank_ids=["bank_c"],
        circuit_breaker_enabled=True,
        circuit_breaker_cooldown_seconds=0.01,
        execution_mode="SIMULATED_RESEARCH",
    )

    # Verify that multi-counterparty does not crash with unexpected self-rejection from nested call
    with patch.object(svc, "query_cross_bank_intelligence", wraps=svc.query_cross_bank_intelligence):
        resp = svc.query_multi_counterparty_intelligence(req=multi_req, principal=principal)
        assert resp is not None
