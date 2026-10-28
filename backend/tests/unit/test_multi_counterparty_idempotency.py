"""Unit and integration tests for Multi-Counterparty Durable Request Idempotency.

Adheres strictly to .agents/AGENTS.md, runtime truth, and M6.4 Task 2 requirements:
1. Identical repeated requests with the same key reuse original verified result and do not
   create additional tickets, receipts, signals, or quota consumption.
2. Concurrent identical requests cannot execute twice.
3. Same key with changed payload or different institutional identity is rejected as conflict.
4. Revoked consent or changed grant revision prevents stale-result replay.
5. Restart recovery is demonstrated through durable storage, not in-memory mocks.
6. Controlled partial failures retain their original truthful outcome without fabricated results.
7. Callers without idempotency key preserve existing execution behavior.
8. End-to-end FastAPI router with Idempotency-Key and Idempotency-Replayed headers.
"""

from __future__ import annotations

import datetime
import threading
import time
from collections.abc import Generator
from datetime import UTC
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

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
    CounterpartyWorkerUnavailableError,
    CrossBankIdempotencyConflictError,
    MemberRole,
)
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.infrastructure.storage.durable_idempotency_store import DurableCrossBankIdempotencyStore
from app.main import app


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    store_file = tmp_path / "idempotency_consortium_store.json"
    account_file = tmp_path / "idempotency_account_store.json"
    idem_db = tmp_path / "test_cross_bank_idempotency.sqlite"

    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("CFI_IDEMPOTENCY_DB_PATH", str(idem_db))
    monkeypatch.setenv("JWT_SECRET", "test_jwt_secret_idempotency_must_be_32bytes_min!")
    monkeypatch.setenv("CFI_JWT_SECRET", "test_jwt_secret_idempotency_must_be_32bytes_min!")
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", "test_secret_idempotency_32bytes_min!")
    monkeypatch.setenv("CFI_ENABLE_CROSS_BANK_SIMULATION", "true")

    from app.config import get_settings

    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurableCrossBankIdempotencyStore.reset_instance()
    yield
    get_settings.cache_clear()
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    DurableCrossBankIdempotencyStore.reset_instance()


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
    c_id = "consortium_tri_idempotency"

    service.create_consortium(
        consortium_id=c_id,
        name="Tri-Bank Consortium Idempotency",
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


def _create_verified_signal(
    target_bank: str, consortium_id: str, match: bool = True
) -> CrossBankIntelligenceSignal:
    return CrossBankIntelligenceSignal(
        signal_id=f"sig_{target_bank}_test_001",
        consortium_id=consortium_id,
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        privacy_hash_truncated="a1b2c3d4e5f67890",
        match_confirmed=match,
        match_category="EXACT_MATCH" if match else "NO_MATCH",
        match_confidence=0.99 if match else 0.05,
        contributing_institutions_count=1 if match else 0,
        contributing_institutions=[target_bank] if match else [],
        sanitized_risk_level="HIGH" if match else "LOW",
        execution_mode="VERIFIED_OPERATIONAL",
        attestation_status="HARDWARE_ENCLAVE_VERIFIED",
        provenance={
            "ticket_id": f"ticket_{target_bank}_123",
            "receipt_id": f"rcpt_{target_bank}_999",
            "signature_verified": True,
        },
    )


def test_identical_repeated_requests_reuse_original_verified_result_and_skip_execution(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
    tmp_path: Path,
) -> None:
    """Identical repeated requests with the same key reuse original verified result and do not execute PSI again."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    test_ent = Entity(
        id="cust_test_001",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_001",
        bank_id="bank_a",
        display_label="Test Customer",
        risk_level=RiskLevel.HIGH,
    )
    entity_svc._entities.set("cust_test_001", _entity_to_dict(test_ent))

    idem_store = DurableCrossBankIdempotencyStore(db_path=str(tmp_path / "idem_test1.sqlite"))
    service = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=idem_store
    )

    sig_b = _create_verified_signal("bank_b", c_id, match=True)
    sig_c = _create_verified_signal("bank_c", c_id, match=False)

    exec_call_count = 0

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        nonlocal exec_call_count
        exec_call_count += 1
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            return sig_c
        raise ValueError(f"Unexpected target: {req.target_bank_id}")

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="idempotent-key-001",
    )

    # 1. Initial execution
    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        resp1 = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert exec_call_count == 2
    assert resp1.overall_outcome == "COMPLETED"
    assert resp1.counterpart_count == 2
    assert resp1.results["bank_b"].signal_id == sig_b.signal_id
    assert resp1.results["bank_c"].signal_id == sig_c.signal_id
    assert getattr(resp1, "_idempotency_replayed", False) is False

    # 2. Duplicate replay under identical key: PSI execution MUST NOT be invoked
    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        resp2 = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    # Verifies PSI execution was NOT repeated
    assert exec_call_count == 2
    assert getattr(resp2, "_idempotency_replayed", False) is True
    assert resp2.query_id == resp1.query_id
    assert resp2.overall_outcome == "COMPLETED"
    assert resp2.results["bank_b"].signal_id == sig_b.signal_id
    assert resp2.results["bank_c"].signal_id == sig_c.signal_id
    assert resp2.results["bank_b"].provenance["receipt_id"] == "rcpt_bank_b_999"


def test_concurrent_identical_requests_cannot_execute_twice(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
    tmp_path: Path,
) -> None:
    """A simultaneous duplicate request must never initiate a second PSI execution."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    test_ent = Entity(
        id="cust_test_001",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_001",
        bank_id="bank_a",
        display_label="Test Customer",
        risk_level=RiskLevel.HIGH,
    )
    entity_svc._entities.set("cust_test_001", _entity_to_dict(test_ent))

    idem_store = DurableCrossBankIdempotencyStore(db_path=str(tmp_path / "idem_concurrent.sqlite"))
    service = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=idem_store
    )

    sig_b = _create_verified_signal("bank_b", c_id, match=True)
    sig_c = _create_verified_signal("bank_c", c_id, match=False)

    exec_call_count = 0
    exec_lock = threading.Lock()

    def mock_exec_operational_slow(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        nonlocal exec_call_count
        with exec_lock:
            exec_call_count += 1
        time.sleep(0.3)  # simulate in-flight network duration
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            return sig_c
        raise ValueError(f"Unexpected target: {req.target_bank_id}")

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="idempotent-concurrent-key",
    )

    responses: list[Any] = []
    errors: list[Exception] = []

    def worker_thread() -> None:
        try:
            res = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])
            responses.append(res)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=worker_thread)
    t2 = threading.Thread(target=worker_thread)

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational_slow):
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    # Across both threads, PSI execution ran exactly once per counterpart (total 2 calls, not 4)
    assert exec_call_count == 2
    assert len(errors) == 0
    assert len(responses) == 2
    # Both responses share the exact same query_id and verified signals
    assert responses[0].query_id == responses[1].query_id
    assert responses[0].results["bank_b"].signal_id == responses[1].results["bank_b"].signal_id


def test_same_key_with_different_fingerprint_rejected_as_conflict(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
    tmp_path: Path,
) -> None:
    """Reusing an idempotency key with a changed payload fingerprint is rejected as a 409 conflict."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    test_ent_1 = Entity(
        id="cust_test_001",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_001",
        bank_id="bank_a",
        display_label="Test Customer 1",
        risk_level=RiskLevel.HIGH,
    )
    test_ent_2 = Entity(
        id="cust_test_002",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_002",
        bank_id="bank_a",
        display_label="Test Customer 2",
        risk_level=RiskLevel.LOW,
    )
    entity_svc._entities.set("cust_test_001", _entity_to_dict(test_ent_1))
    entity_svc._entities.set("cust_test_002", _entity_to_dict(test_ent_2))

    idem_store = DurableCrossBankIdempotencyStore(db_path=str(tmp_path / "idem_conflict.sqlite"))
    service = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=idem_store
    )

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec(req, *args, **kwargs):
        return sig_b

    req1 = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="reused-key-conflict-test",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp1 = service.query_multi_counterparty_intelligence(req=req1, auth_token=auth_tokens["bank_a"])
    assert resp1.overall_outcome == "COMPLETED"

    # Now submit under the same key but with a different query_entity_id
    req2 = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_002",  # changed entity!
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="reused-key-conflict-test",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        with pytest.raises(CrossBankIdempotencyConflictError, match="Idempotency key conflict"):
            service.query_multi_counterparty_intelligence(req=req2, auth_token=auth_tokens["bank_a"])


def test_same_key_with_different_principal_rejected_as_conflict(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
    tmp_path: Path,
) -> None:
    """An idempotency key cannot return another principal's previous result."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    test_ent = Entity(
        id="cust_test_001",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_001",
        bank_id="bank_a",
        display_label="Test Customer",
        risk_level=RiskLevel.HIGH,
    )
    entity_svc._entities.set("cust_test_001", _entity_to_dict(test_ent))

    idem_store = DurableCrossBankIdempotencyStore(db_path=str(tmp_path / "idem_principal.sqlite"))
    service = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=idem_store
    )

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec(req, *args, **kwargs):
        return sig_b

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="principal-isolation-key-01",
    )

    p1 = AuthenticatedPrincipal(
        principal_id="analyst_alice",
        roles=["admin", "ml_lead"],
        tenant_id="bank_a",
        is_authenticated=True,
    )
    p2 = AuthenticatedPrincipal(
        principal_id="analyst_bob",
        roles=["admin", "ml_lead"],
        tenant_id="bank_a",
        is_authenticated=True,
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp1 = service.query_multi_counterparty_intelligence(req=req, principal=p1)
    assert resp1.overall_outcome == "COMPLETED"

    # analyst_bob tries to reuse the same key
    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        with pytest.raises(CrossBankIdempotencyConflictError, match="Idempotency key conflict"):
            service.query_multi_counterparty_intelligence(req=req, principal=p2)


def test_revoked_consent_prevents_stale_result_replay(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
    tmp_path: Path,
) -> None:
    """A revoked bilateral grant fails closed and strictly prevents stale result replay."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    test_ent = Entity(
        id="cust_test_001",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_001",
        bank_id="bank_a",
        display_label="Test Customer",
        risk_level=RiskLevel.HIGH,
    )
    entity_svc._entities.set("cust_test_001", _entity_to_dict(test_ent))

    idem_store = DurableCrossBankIdempotencyStore(db_path=str(tmp_path / "idem_revocation.sqlite"))
    service = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=idem_store
    )

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec(req, *args, **kwargs):
        return sig_b

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="revocation-guard-key",
    )

    # 1. Initial success under valid bilateral grant
    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])
    assert resp.overall_outcome == "COMPLETED"

    # 2. Bank B revokes consent under grant_a_b
    gov_svc.revoke_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_a_b",
        revoking_bank_id="bank_b",
        reason="Revoked bilateral intelligence agreement",
        auth_token=auth_tokens["bank_b"],
    )

    # 3. Duplicate request under same key must NOT replay cached result; must fail closed!
    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        with pytest.raises(ConsortiumSecurityError, match="revoked"):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


def test_modified_grant_revision_prevents_stale_result_replay(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
    tmp_path: Path,
) -> None:
    """A modified grant revision prevents stale result replay."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    test_ent = Entity(
        id="cust_test_001",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_001",
        bank_id="bank_a",
        display_label="Test Customer",
        risk_level=RiskLevel.HIGH,
    )
    entity_svc._entities.set("cust_test_001", _entity_to_dict(test_ent))

    idem_store = DurableCrossBankIdempotencyStore(db_path=str(tmp_path / "idem_grant_mod.sqlite"))
    service = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=idem_store
    )

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec(req, *args, **kwargs):
        return sig_b

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="grant-mod-key-01",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])
    assert resp.overall_outcome == "COMPLETED"

    # Update grant_a_b to revision 2 by expanding allowed purposes and re-approving
    gov_svc.register_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_a_b",
        institution_a="bank_a",
        institution_b="bank_b",
        revision=2,
        allowed_purposes=["AML_COLLABORATIVE_INVESTIGATION", "SANCTIONS_SCREENING"],
        allowed_jurisdictions=["EU"],
        institution_jurisdictions={"bank_a": "EU", "bank_b": "EU"},
        approving_bank_id="bank_a",
        authority="ECB",
        auth_token=auth_tokens["bank_a"],
    )
    gov_svc.approve_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_a_b",
        approving_bank_id="bank_b",
        auth_token=auth_tokens["bank_b"],
    )

    # Replaying same key must fail closed because grant revision changed
    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        with pytest.raises(ConsortiumSecurityError, match="modified since execution"):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


def test_restart_recovery_demonstrated_through_durable_storage(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
    tmp_path: Path,
) -> None:
    """Successful sanitized responses can be recovered after service restart without repeating PSI."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    test_ent = Entity(
        id="cust_test_001",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_001",
        bank_id="bank_a",
        display_label="Test Customer",
        risk_level=RiskLevel.HIGH,
    )
    entity_svc._entities.set("cust_test_001", _entity_to_dict(test_ent))

    db_file = tmp_path / "durable_restart_recovery.sqlite"

    # Service Instance 1: execute query
    store_1 = DurableCrossBankIdempotencyStore(db_path=str(db_file))
    svc1 = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=store_1
    )

    sig_b = _create_verified_signal("bank_b", c_id, match=True)
    exec_call_count = 0

    def mock_exec(req, *args, **kwargs):
        nonlocal exec_call_count
        exec_call_count += 1
        return sig_b

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="restart-recovery-key-99",
    )

    with patch.object(svc1, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp1 = svc1.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert exec_call_count == 1
    assert resp1.overall_outcome == "COMPLETED"

    # Simulate Full Service Restart: destroy svc1, re-instantiate svc2 from disk db
    del svc1
    del store_1

    store_2 = DurableCrossBankIdempotencyStore(db_path=str(db_file))
    svc2 = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=store_2
    )

    # Replay on svc2: must recover response from SQLite file without executing PSI
    with patch.object(svc2, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp2 = svc2.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert exec_call_count == 1  # zero additional PSI executions!
    assert getattr(resp2, "_idempotency_replayed", False) is True
    assert resp2.query_id == resp1.query_id
    assert resp2.results["bank_b"].signal_id == sig_b.signal_id


def test_controlled_partial_failures_retain_truthful_outcome_on_replay(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
    tmp_path: Path,
) -> None:
    """Controlled partial failures retain their original truthful outcome on replay without fabricated results."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    test_ent = Entity(
        id="cust_test_001",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_001",
        bank_id="bank_a",
        display_label="Test Customer",
        risk_level=RiskLevel.HIGH,
    )
    entity_svc._entities.set("cust_test_001", _entity_to_dict(test_ent))

    idem_store = DurableCrossBankIdempotencyStore(db_path=str(tmp_path / "idem_partial.sqlite"))
    service = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=idem_store
    )

    sig_b = _create_verified_signal("bank_b", c_id, match=True)
    exec_call_count = 0

    def mock_exec_partial(req, *args, **kwargs):
        nonlocal exec_call_count
        exec_call_count += 1
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            raise CounterpartyWorkerUnavailableError("Worker for counterpart 'bank_c' is unreachable.")
        raise ValueError("Unexpected")

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="partial-failure-key-42",
    )

    # 1. Initial execution yields PARTIAL_FAILURE
    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_partial):
        resp1 = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert exec_call_count == 2
    assert resp1.overall_outcome == "PARTIAL_FAILURE"
    assert "bank_b" in resp1.results
    assert "bank_c" in resp1.failures
    assert resp1.failures["bank_c"].error_category == "WORKER_UNAVAILABLE"

    # 2. Replay must return identical truthful PARTIAL_FAILURE outcome without retrying or inventing data
    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_partial):
        resp2 = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert exec_call_count == 2  # no new executions
    assert resp2.overall_outcome == "PARTIAL_FAILURE"
    assert resp2.results["bank_b"].signal_id == sig_b.signal_id
    assert resp2.failures["bank_c"].error_category == "WORKER_UNAVAILABLE"
    assert getattr(resp2, "_idempotency_replayed", False) is True


def test_indeterminate_interrupted_execution_fails_closed(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
    tmp_path: Path,
) -> None:
    """An ambiguous or interrupted execution fails closed on replay rather than silently repeating."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    test_ent = Entity(
        id="cust_test_001",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_001",
        bank_id="bank_a",
        display_label="Test Customer",
        risk_level=RiskLevel.HIGH,
    )
    entity_svc._entities.set("cust_test_001", _entity_to_dict(test_ent))

    idem_store = DurableCrossBankIdempotencyStore(db_path=str(tmp_path / "idem_ambiguous.sqlite"))
    service = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=idem_store
    )

    def mock_crash(req, *args, **kwargs):
        raise RuntimeError("Fatal process crash during PSI execution")

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="crash-key-01",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_crash):
        with pytest.raises(RuntimeError, match="Fatal process crash"):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    # Subsequent replay must fail closed with CrossBankIdempotencyConflictError
    with pytest.raises(CrossBankIdempotencyConflictError, match="indeterminate state"):
        service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


def test_fastapi_router_end_to_end_idempotency_headers_and_conflicts(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """FastAPI router endpoint returns Idempotency-Replayed header and enforces 409 on conflict."""
    gov_svc, c_id = tri_consortium
    cbi_instance = CrossBankIntelligenceService.get_instance()
    cbi_instance.consortium_service = gov_svc

    test_ent = Entity(
        id="cust_router_idem_ent",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_router_idem_ent",
        bank_id="bank_a",
        display_label="Router Idempotency Entity",
        risk_level=RiskLevel.HIGH,
    )
    cbi_instance.entity_service._entities.set("cust_router_idem_ent", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=True)
    exec_call_count = 0

    def mock_exec(req, *args, **kwargs):
        nonlocal exec_call_count
        exec_call_count += 1
        return sig_b

    client = TestClient(app)
    req_body = {
        "consortium_id": c_id,
        "requesting_bank_id": "bank_a",
        "target_bank_ids": ["bank_b"],
        "query_entity_id": "cust_router_idem_ent",
        "purpose": "AML_COLLABORATIVE_INVESTIGATION",
        "execution_mode": "VERIFIED_OPERATIONAL",
    }

    # 1. First execution with Idempotency-Key header
    with patch.object(cbi_instance, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp1 = client.post(
            "/api/v1/intelligence/cross-bank/query-multi",
            json=req_body,
            headers={
                "Authorization": f"Bearer {auth_tokens['bank_a']}",
                "Idempotency-Key": "e2e-router-idempotency-key-001",
            },
        )
    assert resp1.status_code == 200
    assert exec_call_count == 1
    assert "Idempotency-Replayed" not in resp1.headers

    # 2. Second execution with identical key -> returns replayed response with header
    with patch.object(cbi_instance, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp2 = client.post(
            "/api/v1/intelligence/cross-bank/query-multi",
            json=req_body,
            headers={
                "Authorization": f"Bearer {auth_tokens['bank_a']}",
                "Idempotency-Key": "e2e-router-idempotency-key-001",
            },
        )
    assert resp2.status_code == 200
    assert exec_call_count == 1  # zero additional executions!
    assert resp2.headers.get("Idempotency-Replayed") == "true"
    assert resp2.json()["query_id"] == resp1.json()["query_id"]

    # 3. Third execution with same key but altered payload -> HTTP 409 Conflict
    conflicting_body = dict(req_body)
    conflicting_body["execution_mode"] = "SIMULATED_RESEARCH"
    with patch.object(cbi_instance, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp3 = client.post(
            "/api/v1/intelligence/cross-bank/query-multi",
            json=conflicting_body,
            headers={
                "Authorization": f"Bearer {auth_tokens['bank_a']}",
                "Idempotency-Key": "e2e-router-idempotency-key-001",
            },
        )
    assert resp3.status_code == 409
    assert "Idempotency key conflict" in resp3.json()["detail"]


def test_bounded_retention_and_cleanup_expired(tmp_path: Path) -> None:
    """Store enforces bounded storage retention and correctly purges expired records."""
    db_file = tmp_path / "retention_test.sqlite"
    store = DurableCrossBankIdempotencyStore(db_path=str(db_file), retention_seconds=10)

    # Acquire and complete key 1
    store.acquire(
        consortium_id="c1",
        requesting_bank_id="bank_a",
        principal_id="p1",
        idempotency_key="k1",
        request_fingerprint="fp1",
        governance_snapshot={},
        owner_id="own1",
        ttl_seconds=1,  # 1 second TTL
    )
    store.complete(
        consortium_id="c1",
        requesting_bank_id="bank_a",
        idempotency_key="k1",
        owner_id="own1",
        response_data={"query_id": "q1"},
        governance_snapshot={},
    )

    # Key 2 with 3600 second TTL
    store.acquire(
        consortium_id="c1",
        requesting_bank_id="bank_a",
        principal_id="p1",
        idempotency_key="k2",
        request_fingerprint="fp2",
        governance_snapshot={},
        owner_id="own2",
        ttl_seconds=3600,
    )
    store.complete(
        consortium_id="c1",
        requesting_bank_id="bank_a",
        idempotency_key="k2",
        owner_id="own2",
        response_data={"query_id": "q2"},
        governance_snapshot={},
    )

    time.sleep(1.2)  # wait for key 1 to expire

    purged = store.cleanup_expired()
    assert purged == 1

    # Key 1 is gone; key 2 is still present
    with store._get_connection() as conn:
        cur = conn.execute("SELECT idempotency_key FROM cross_bank_idempotency_records;")
        remaining = [row["idempotency_key"] for row in cur.fetchall()]
        assert "k1" not in remaining
        assert "k2" in remaining


def test_same_key_with_different_privacy_id_rejected_as_conflict(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
    tmp_path: Path,
) -> None:
    """Changing privacy_id under the same key yields an idempotency conflict without executing PSI."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    test_ent = Entity(
        id="cust_test_001",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_001",
        bank_id="bank_a",
        display_label="Test Customer",
        risk_level=RiskLevel.HIGH,
    )
    entity_svc._entities.set("cust_test_001", _entity_to_dict(test_ent))

    idem_store = DurableCrossBankIdempotencyStore(db_path=str(tmp_path / "idem_privacy_id.sqlite"))
    service = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=idem_store
    )

    sig_b = _create_verified_signal("bank_b", c_id, match=True)
    exec_call_count = 0

    def mock_exec(*args, **kwargs):
        nonlocal exec_call_count
        exec_call_count += 1
        return sig_b

    # Request 1: privacy_id is absent (None)
    req1 = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        privacy_id=None,
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="privacy-id-conflict-key-01",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp1 = service.query_multi_counterparty_intelligence(req=req1, auth_token=auth_tokens["bank_a"])

    assert exec_call_count == 1
    assert resp1.overall_outcome == "COMPLETED"

    # Request 2: same key, but changing privacy_id to an explicit valid value
    req2 = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        privacy_id="hash_test_cust_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="privacy-id-conflict-key-01",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        with pytest.raises(CrossBankIdempotencyConflictError, match="Idempotency key conflict"):
            service.query_multi_counterparty_intelligence(req=req2, auth_token=auth_tokens["bank_a"])

    # Crucial acceptance check: NO additional PSI executions occurred
    assert exec_call_count == 1


def test_operational_cleanup_reclaims_expired_during_normal_operations(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
    tmp_path: Path,
) -> None:
    """Expired records are reclaimed during normal store operations without manual cleanup_expired calls."""
    gov_svc, c_id = tri_consortium
    entity_svc = EntityResolutionService()
    test_ent = Entity(
        id="cust_test_001",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_test_cust_001",
        bank_id="bank_a",
        display_label="Test Customer",
        risk_level=RiskLevel.HIGH,
    )
    entity_svc._entities.set("cust_test_001", _entity_to_dict(test_ent))

    db_file = tmp_path / "auto_cleanup_test.sqlite"
    # Ensure immediate cleanup evaluation on store operations
    idem_store = DurableCrossBankIdempotencyStore(
        db_path=str(db_file), retention_seconds=86400, cleanup_interval_seconds=0.0
    )
    service = CrossBankIntelligenceService(
        consortium_service=gov_svc, entity_service=entity_svc, idempotency_store=idem_store
    )

    now_dt = datetime.datetime.now(UTC)
    expired_iso = (now_dt - datetime.timedelta(hours=25)).isoformat()
    future_iso = (now_dt + datetime.timedelta(hours=23)).isoformat()
    now_iso = now_dt.isoformat()

    req_amb = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="key_ambiguous",
    )
    fp_amb = compute_multi_counterparty_request_fingerprint(req_amb)

    # Seed store directly with:
    # 1. Expired completed record (key_expired)
    # 2. Valid completed record (key_valid)
    # 3. Active in-progress reservation (key_active_in_progress)
    # 4. Indeterminate failure within 24h retention (key_ambiguous)
    with idem_store._get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute(
            """
            INSERT INTO cross_bank_idempotency_records (
                lookup_key, idempotency_key, requesting_bank_id, principal_id,
                consortium_id, request_fingerprint, status, execution_owner_id,
                response_json, governance_snapshot_json, created_at, updated_at, expires_at
            ) VALUES
            (?, 'key_expired', 'bank_a', 'user_a', ?, 'fp_exp', 'COMPLETED', 'own_exp', '{}', '{}', ?, ?, ?),
            (?, 'key_valid', 'bank_a', 'user_a', ?, 'fp_val', 'COMPLETED', 'own_val', '{}', '{}', ?, ?, ?),
            (?, 'key_active_in_progress', 'bank_a', 'user_a', ?, 'fp_prog', 'IN_PROGRESS', 'own_prog', NULL, '{}', ?, ?, ?),
            (?, 'key_ambiguous', 'bank_a', 'user_a', ?, ?, 'FAILED_AMBIGUOUS', 'own_amb', NULL, '{}', ?, ?, ?);
            """,
            (
                idem_store._build_lookup_key(c_id, "bank_a", "key_expired"),
                c_id,
                expired_iso,
                expired_iso,
                expired_iso,
                idem_store._build_lookup_key(c_id, "bank_a", "key_valid"),
                c_id,
                now_iso,
                now_iso,
                future_iso,
                idem_store._build_lookup_key(c_id, "bank_a", "key_active_in_progress"),
                c_id,
                now_iso,
                now_iso,
                future_iso,
                idem_store._build_lookup_key(c_id, "bank_a", "key_ambiguous"),
                c_id,
                fp_amb,
                now_iso,
                now_iso,
                future_iso,
            ),
        )
        conn.commit()

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec(*args, **kwargs):
        return sig_b

    # Normal operation: perform a query with a brand new key (key_fresh)
    # Notice: We NEVER call idem_store.cleanup_expired() manually here!
    req_fresh = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="key_fresh",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec):
        resp_fresh = service.query_multi_counterparty_intelligence(req=req_fresh, auth_token=auth_tokens["bank_a"])

    assert resp_fresh.overall_outcome == "COMPLETED"

    # Verify database state after normal operation:
    with idem_store._get_connection() as conn:
        cur = conn.execute("SELECT idempotency_key, status FROM cross_bank_idempotency_records;")
        rows = {row["idempotency_key"]: row["status"] for row in cur.fetchall()}

    # 1. Expired record was reclaimed automatically
    assert "key_expired" not in rows

    # 2. Valid record remains intact
    assert "key_valid" in rows
    assert rows["key_valid"] == "COMPLETED"

    # 3. Active in-progress reservation was strictly protected from cleanup
    assert "key_active_in_progress" in rows
    assert rows["key_active_in_progress"] == "IN_PROGRESS"

    # 4. Indeterminate execution within retention remains protected and fails closed
    assert "key_ambiguous" in rows
    assert rows["key_ambiguous"] == "FAILED_AMBIGUOUS"

    # 5. Fresh key was inserted and completed
    assert "key_fresh" in rows
    assert rows["key_fresh"] == "COMPLETED"

    # Confirm ambiguous execution fails closed when called
    req_amb = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
        idempotency_key="key_ambiguous",
    )
    with pytest.raises(CrossBankIdempotencyConflictError, match="indeterminate state"):
        service.query_multi_counterparty_intelligence(req=req_amb, auth_token=auth_tokens["bank_a"])

