"""Unit tests for Per-Counterparty Execution Outcomes and Partial Failure Management.

Verifies:
1. Fully successful multi-counterparty query retains all verified signals and reports COMPLETED.
2. Controlled worker/transport failure on one counterpart retains verified results for completed
   counterparts, reports PARTIAL_FAILURE, and contains no fabricated signal for the failed counterpart.
3. Complete failure across all counterparts truthfully reports FAILED without representing any
   failed execution as NO_MATCH.
4. Sanitization of counterpart execution failures strips internal credentials, URLs, and private details.
5. All-or-nothing authorization preflight remains fail-closed; missing grants or revoked consent
   are never converted into partial success.
6. FastAPI router endpoint exposes typed execution outcomes, overall outcome, and failure records.
"""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.application.schemas.cross_bank_intelligence import (
    CrossBankIntelligenceQueryRequest,
    CrossBankIntelligenceSignal,
    MultiCounterpartyIntelligenceQueryRequest,
)
from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
from app.application.services.entity_resolution import EntityResolutionService, _entity_to_dict
from app.application.services.isolated_matching_service import (
    InstitutionalCoordinatorClient,
    InstitutionalWorkerClient,
)
from app.domain.consortium_governance import (
    ConsortiumSecurityError,
    CounterpartyTimeoutError,
    CounterpartyWorkerUnavailableError,
    MemberRole,
)
from app.domain.investigation_entities import Entity, EntityType, RiskLevel
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.security.principal_auth import AuthenticatedPrincipal
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.main import app


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    store_file = tmp_path / "execution_outcomes_consortium_store.json"
    account_file = tmp_path / "execution_outcomes_account_store.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    monkeypatch.setenv("JWT_SECRET", "test_jwt_secret_execution_outcomes_must_be_32bytes_min!")
    monkeypatch.setenv("CFI_JWT_SECRET", "test_jwt_secret_execution_outcomes_must_be_32bytes_min!")
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_SECRET", "test_secret_execution_outcomes_32bytes_min!")
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
    service = ConsortiumGovernanceService()
    c_id = "consortium_tri_outcomes"

    service.create_consortium(
        consortium_id=c_id,
        name="Tri-Bank Consortium Outcomes",
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


def _create_verified_signal(bank_id: str, consortium_id: str, match: bool) -> CrossBankIntelligenceSignal:
    return CrossBankIntelligenceSignal(
        signal_id=f"sig_{bank_id}_valid",
        consortium_id=consortium_id,
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
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


def test_multi_counterparty_full_success_reports_completed(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
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

    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    sig_b = _create_verified_signal("bank_b", c_id, match=True)
    sig_c = _create_verified_signal("bank_c", c_id, match=False)

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            return sig_c
        raise ValueError(f"Unexpected target {req.target_bank_id}")

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        req = MultiCounterpartyIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="cust_test_001",
            consortium_id=c_id,
            target_bank_ids=["bank_b", "bank_c"],
            purpose="AML_COLLABORATIVE_INVESTIGATION",
            execution_mode="VERIFIED_OPERATIONAL",
        )
        res = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert res.overall_outcome == "COMPLETED"
    assert res.outcome == "COMPLETED"
    assert res.counterpart_count == 2
    assert len(res.results) == 2
    assert res.results["bank_b"].match_confirmed is True
    assert res.results["bank_c"].match_confirmed is False
    assert len(res.failures) == 0

    assert res.execution_outcomes["bank_b"].status == "SUCCESS"
    assert res.execution_outcomes["bank_b"].signal is not None
    assert res.execution_outcomes["bank_b"].failure is None

    assert res.execution_outcomes["bank_c"].status == "SUCCESS"
    assert res.execution_outcomes["bank_c"].signal is not None
    assert res.execution_outcomes["bank_c"].failure is None


def test_multi_counterparty_partial_failure_worker_unavailable(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
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

    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            raise urllib.error.URLError("Connection refused by counterpart worker daemon on port 8000")
        raise ValueError(f"Unexpected target {req.target_bank_id}")

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        req = MultiCounterpartyIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="cust_test_001",
            consortium_id=c_id,
            target_bank_ids=["bank_b", "bank_c"],
            purpose="AML_COLLABORATIVE_INVESTIGATION",
            execution_mode="VERIFIED_OPERATIONAL",
        )
        res = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert res.overall_outcome == "PARTIAL_FAILURE"
    assert res.outcome == "PARTIAL_FAILURE"
    assert res.counterpart_count == 2

    # Bank B succeeded and is preserved
    assert "bank_b" in res.results
    assert res.results["bank_b"].match_confirmed is True
    assert res.execution_outcomes["bank_b"].status == "SUCCESS"
    assert res.execution_outcomes["bank_b"].signal is not None

    # Bank C failed: NO fabricated signal anywhere in results
    assert "bank_c" not in res.results
    assert "bank_c" in res.failures
    failure_c = res.failures["bank_c"]
    assert failure_c.target_bank_id == "bank_c"
    assert failure_c.error_category == "WORKER_UNAVAILABLE"
    assert "unreachable or offline" in failure_c.error_message
    assert failure_c.retryable is True

    assert res.execution_outcomes["bank_c"].status == "FAILED"
    assert res.execution_outcomes["bank_c"].signal is None
    assert res.execution_outcomes["bank_c"].failure is not None


def test_multi_counterparty_all_counterparties_fail(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
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

    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        if req.target_bank_id == "bank_b":
            raise TimeoutError("Timed out waiting for worker response")
        elif req.target_bank_id == "bank_c":
            raise ConnectionRefusedError("Failed to establish transport connection")
        raise ValueError(f"Unexpected target {req.target_bank_id}")

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        req = MultiCounterpartyIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="cust_test_001",
            consortium_id=c_id,
            target_bank_ids=["bank_b", "bank_c"],
            purpose="AML_COLLABORATIVE_INVESTIGATION",
            execution_mode="VERIFIED_OPERATIONAL",
        )
        res = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert res.overall_outcome == "FAILED"
    assert res.outcome == "FAILED"
    assert res.counterpart_count == 2

    # Crucial: No fabricated signals, results is empty dictionary
    assert len(res.results) == 0
    assert len(res.failures) == 2

    assert res.failures["bank_b"].error_category == "TIMEOUT"
    assert res.execution_outcomes["bank_b"].status == "FAILED"
    assert res.execution_outcomes["bank_b"].signal is None

    assert res.failures["bank_c"].error_category == "WORKER_UNAVAILABLE"
    assert res.execution_outcomes["bank_c"].status == "FAILED"
    assert res.execution_outcomes["bank_c"].signal is None


def test_multi_counterparty_sanitization_strips_sensitive_data(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
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

    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    sensitive_exception = ConnectionRefusedError(
        "Connection failed to http://cfi_vault_worker_internal:9090/vault/private_keys?token=secret_internal_jwt_token_9999"
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=sensitive_exception):
        req = MultiCounterpartyIntelligenceQueryRequest(
            requesting_bank_id="bank_a",
            query_entity_id="cust_test_001",
            consortium_id=c_id,
            target_bank_ids=["bank_b"],
            purpose="AML_COLLABORATIVE_INVESTIGATION",
            execution_mode="VERIFIED_OPERATIONAL",
        )
        res = service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])

    assert res.overall_outcome == "FAILED"
    failure = res.failures["bank_b"]
    # Verify no private tokens, port numbers, or internal URLs leaked
    assert "secret_internal_jwt_token_9999" not in failure.error_message
    assert "9090" not in failure.error_message
    assert "cfi_vault_worker_internal" not in failure.error_message
    assert failure.error_category in ("WORKER_UNAVAILABLE", "TRANSPORT_FAILURE")


def test_multi_counterparty_preflight_authorization_remains_fail_closed(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
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

    # Bank C revokes pairwise grant
    gov_svc.revoke_pairwise_operational_grant(
        consortium_id=c_id,
        grant_id="grant_a_c",
        revoking_bank_id="bank_c",
        reason="Revoked by institution bank_c",
        auth_token=auth_tokens["bank_c"],
    )

    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    # Must raise ConsortiumSecurityError before any execution occurs; never convert to partial success
    with pytest.raises(ConsortiumSecurityError, match="Pairwise operational authorization rejected"):
        service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


def test_multi_counterparty_fastapi_router_outcomes(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    gov_svc, c_id = tri_consortium
    cbi_instance = CrossBankIntelligenceService.get_instance()
    cbi_instance.consortium_service = gov_svc

    test_ent = Entity(
        id="cust_router_ent",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_router_ent",
        bank_id="bank_a",
        display_label="Router Entity",
        risk_level=RiskLevel.HIGH,
    )
    cbi_instance.entity_service._entities.set("cust_router_ent", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            raise ConnectionRefusedError("Connection refused by worker container")
        raise ValueError(f"Unexpected target: {req.target_bank_id}")

    client = TestClient(app)
    payload = {
        "consortium_id": c_id,
        "requesting_bank_id": "bank_a",
        "target_bank_ids": ["bank_b", "bank_c"],
        "query_entity_id": "cust_router_ent",
        "purpose": "AML_COLLABORATIVE_INVESTIGATION",
        "execution_mode": "VERIFIED_OPERATIONAL",
    }

    with patch.object(cbi_instance, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        resp = client.post(
            "/api/v1/intelligence/cross-bank/query-multi",
            json=payload,
            headers={"Authorization": f"Bearer {auth_tokens['bank_a']}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_outcome"] == "PARTIAL_FAILURE"
    assert data["outcome"] == "PARTIAL_FAILURE"
    assert data["counterpart_count"] == 2
    assert "bank_b" in data["results"]
    assert "bank_c" not in data["results"]
    assert "bank_c" in data["failures"]
    assert data["failures"]["bank_c"]["error_category"] == "WORKER_UNAVAILABLE"
    assert data["execution_outcomes"]["bank_b"]["status"] == "SUCCESS"
    assert data["execution_outcomes"]["bank_c"]["status"] == "FAILED"


def test_multi_counterparty_ticket_integrity_mismatch_fails_closed(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Security boundary: A Bank C ticket-integrity mismatch causes fail-closed request termination.

    Must NEVER be converted into PARTIAL_FAILURE or EXECUTION_ERROR.
    """
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
    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            raise ConsortiumSecurityError("Adoption rejection: Ticket ID mismatch between ticket and provenance.")
        raise ValueError(f"Unexpected target: {req.target_bank_id}")

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        with pytest.raises(ConsortiumSecurityError, match="Ticket ID mismatch between ticket and provenance"):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


def test_multi_counterparty_invalid_signature_fails_closed(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Security boundary: Invalid cryptographic signatures cause immediate fail-closed termination."""
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
    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            raise ConsortiumSecurityError("Adoption rejection: Worker B authorization signature invalid under enrolled key.")
        raise ValueError(f"Unexpected target: {req.target_bank_id}")

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        with pytest.raises(ConsortiumSecurityError, match="authorization signature invalid"):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


def test_multi_counterparty_publication_admission_rejection_fails_closed(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Security boundary: Linearized publication / reservation admission rejections fail closed."""
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
    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            raise ConsortiumSecurityError("Publication rejected: Pilot reservation expired during linearization.")
        raise ValueError(f"Unexpected target: {req.target_bank_id}")

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        with pytest.raises(ConsortiumSecurityError, match="Publication rejected"):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


def test_multi_counterparty_unexpected_runtime_error_fails_closed(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """Security boundary: Unexpected generic exceptions must propagate and terminate the request."""
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
    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            raise RuntimeError("Unexpected memory corruption during PSI execution")
        raise ValueError(f"Unexpected target: {req.target_bank_id}")

    req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        with pytest.raises(RuntimeError, match="Unexpected memory corruption"):
            service.query_multi_counterparty_intelligence(req=req, auth_token=auth_tokens["bank_a"])


def test_multi_counterparty_router_fails_closed_on_integrity_violation(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """FastAPI router endpoint returns HTTP 403 when counterpart integrity is violated."""
    gov_svc, c_id = tri_consortium
    cbi_instance = CrossBankIntelligenceService.get_instance()
    cbi_instance.consortium_service = gov_svc

    test_ent = Entity(
        id="cust_router_ent_integrity",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_router_cust_integrity",
        bank_id="bank_a",
        display_label="Router Test Entity",
        risk_level=RiskLevel.HIGH,
    )
    cbi_instance.entity_service._entities.set("cust_router_ent_integrity", _entity_to_dict(test_ent))

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            raise ConsortiumSecurityError("Adoption rejection: Ticket ID mismatch between ticket and provenance.")
        raise ValueError(f"Unexpected target: {req.target_bank_id}")

    client = TestClient(app)
    payload = {
        "consortium_id": c_id,
        "requesting_bank_id": "bank_a",
        "target_bank_ids": ["bank_b", "bank_c"],
        "query_entity_id": "cust_router_ent_integrity",
        "purpose": "AML_COLLABORATIVE_INVESTIGATION",
        "execution_mode": "VERIFIED_OPERATIONAL",
    }

    with patch.object(cbi_instance, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        resp = client.post(
            "/api/v1/intelligence/cross-bank/query-multi",
            json=payload,
            headers={"Authorization": f"Bearer {auth_tokens['bank_a']}"},
        )

    # Must be HTTP 403 Forbidden fail-closed; NEVER HTTP 200 PARTIAL_FAILURE
    assert resp.status_code == 403
    assert "Ticket ID mismatch between ticket and provenance" in resp.json()["detail"]


def _make_mock_response(body_bytes: bytes, status: int = 200) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.read.return_value = body_bytes
    mock_resp.status = status
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None
    return mock_resp


def test_coordinator_client_malformed_http200_fails_closed(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """A malformed HTTP 200 coordinator response fails closed with ValueError, not transport failure."""
    client = InstitutionalCoordinatorClient(base_url="http://127.0.0.1:8999")
    req = CrossBankIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="cust_test_001",
        consortium_id="consortium_tri_outcomes",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    malformed_http200 = _make_mock_response(b"<html><body>502 Bad Gateway masked as 200 OK</body></html>")

    # 1. Direct client parsing raises ValueError, NOT CounterpartyTransportError
    with patch("urllib.request.urlopen", return_value=malformed_http200):
        with pytest.raises(ValueError, match="Coordinator returned malformed JSON response"):
            client.execute_match(req=req, auth_token=auth_tokens["bank_a"])

    # 2. Multi-counterparty query fails closed rather than yielding PARTIAL_FAILURE
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
    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    sig_b = _create_verified_signal("bank_b", c_id, match=True)

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        if req.target_bank_id == "bank_b":
            return sig_b
        elif req.target_bank_id == "bank_c":
            # Simulate coordinator client encountering malformed HTTP 200
            with patch("urllib.request.urlopen", return_value=malformed_http200):
                return client.execute_match(req=req, auth_token=auth_tokens["bank_a"])
        raise ValueError(f"Unexpected target: {req.target_bank_id}")

    multi_req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b", "bank_c"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        with pytest.raises(ValueError, match="Coordinator returned malformed JSON response"):
            service.query_multi_counterparty_intelligence(req=multi_req, auth_token=auth_tokens["bank_a"])


def test_coordinator_client_invalid_signal_schema_http200_fails_closed(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """A well-formed HTTP 200 coordinator response with invalid schema fails closed."""
    client = InstitutionalCoordinatorClient(base_url="http://127.0.0.1:8999")
    req = CrossBankIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="cust_test_001",
        consortium_id="consortium_tri_outcomes",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    invalid_schema_http200 = _make_mock_response(b'{"status": "ok", "unexpected_field": "corrupted"}')

    # Direct client parsing raises ValueError for schema validation
    with patch("urllib.request.urlopen", return_value=invalid_schema_http200):
        with pytest.raises(ValueError, match="Coordinator returned invalid signal schema"):
            client.execute_match(req=req, auth_token=auth_tokens["bank_a"])

    # Multi-counterparty query fails closed
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
    service = CrossBankIntelligenceService(consortium_service=gov_svc, entity_service=entity_svc)

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        with patch("urllib.request.urlopen", return_value=invalid_schema_http200):
            return client.execute_match(req=req, auth_token=auth_tokens["bank_a"])

    multi_req = MultiCounterpartyIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        query_entity_id="cust_test_001",
        consortium_id=c_id,
        target_bank_ids=["bank_b"],
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    with patch.object(service, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        with pytest.raises(ValueError, match="Coordinator returned invalid signal schema"):
            service.query_multi_counterparty_intelligence(req=multi_req, auth_token=auth_tokens["bank_a"])


def test_coordinator_client_payload_type_mismatch_fails_closed(
    auth_tokens: dict[str, str],
) -> None:
    """Coordinator returning a JSON list or non-dict raises ValueError."""
    client = InstitutionalCoordinatorClient(base_url="http://127.0.0.1:8999")
    req = CrossBankIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="cust_test_001",
        consortium_id="consortium_tri_outcomes",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    list_payload = _make_mock_response(b'[{"signal_id": "123"}]')
    with patch("urllib.request.urlopen", return_value=list_payload):
        with pytest.raises(ValueError, match="Coordinator returned unexpected payload type: expected dict, got list"):
            client.execute_match(req=req, auth_token=auth_tokens["bank_a"])


def test_worker_client_malformed_http200_cannot_masquerade_as_worker_unavailable() -> None:
    """A malformed HTTP 200 worker response raises ValueError and cannot masquerade as WORKER_UNAVAILABLE."""
    worker = InstitutionalWorkerClient(bank_id="bank_c", base_url="http://127.0.0.1:8998")

    malformed_http200 = _make_mock_response(b"<html>500 Internal Error as 200</html>")

    # 1. _post raises ValueError on malformed JSON, NOT CounterpartyWorkerUnavailableError
    with patch("urllib.request.urlopen", return_value=malformed_http200):
        with pytest.raises(ValueError, match="Worker for 'bank_c' returned malformed JSON response"):
            worker._post("/worker/store-entity", {"entity": {}})

    # 2. _post raises ValueError on non-dict payload
    non_dict_http200 = _make_mock_response(b'["not", "a", "dict"]')
    with patch("urllib.request.urlopen", return_value=non_dict_http200):
        with pytest.raises(ValueError, match="Worker for 'bank_c' returned unexpected payload type: expected dict, got list"):
            worker._post("/worker/store-entity", {"entity": {}})

    # 3. health_check raises ValueError on malformed JSON
    with patch("urllib.request.urlopen", return_value=malformed_http200):
        with pytest.raises(ValueError, match="Worker for 'bank_c' returned malformed JSON response"):
            worker.health_check()

    # 4. get_public_key raises ValueError on malformed JSON or missing key
    with patch("urllib.request.urlopen", return_value=malformed_http200):
        with pytest.raises(ValueError, match="Worker for 'bank_c' returned malformed JSON response"):
            worker.get_public_key()

    missing_key_http200 = _make_mock_response(b'{"wrong_key": "123"}')
    with patch("urllib.request.urlopen", return_value=missing_key_http200):
        with pytest.raises(ValueError, match="Worker for 'bank_c' returned invalid public-key response payload"):
            worker.get_public_key()


def test_worker_client_disconnection_raises_typed_worker_unavailable() -> None:
    """Genuine connection refusal or disconnection raises typed CounterpartyWorkerUnavailableError."""
    worker = InstitutionalWorkerClient(bank_id="bank_c", base_url="http://127.0.0.1:8998")

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(reason=ConnectionRefusedError("Connection refused"))):
        with pytest.raises(CounterpartyWorkerUnavailableError, match="unreachable or offline"):
            worker._post("/worker/store-entity", {"entity": {}})

    with patch("urllib.request.urlopen", side_effect=TimeoutError("Request timed out")):
        with pytest.raises(CounterpartyTimeoutError, match="timed out"):
            worker._post("/worker/store-entity", {"entity": {}})


def test_schema_validation_error_does_not_leak_sentinel_secret(
    auth_tokens: dict[str, str],
) -> None:
    """Schema validation failure raises a nonrecoverable exception without disclosing sentinel secret."""
    client = InstitutionalCoordinatorClient(base_url="http://127.0.0.1:8999")
    req = CrossBankIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="cust_test_001",
        consortium_id="consortium_tri_outcomes",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    sentinel_secret = "TOP_SECRET_CREDENTIAL_SENTINEL_VAL_12345"
    payload = {
        "signal_id": "sig-001",
        "consortium_id": "consortium_tri_outcomes",
        "requesting_bank_id": "bank_a",
        "privacy_hash_truncated": "abc1234",
        "match_confirmed": True,
        "match_category": "EXACT_MATCH",
        "match_confidence": 0.99,
        "contributing_institutions_count": 1,
        "sanitized_risk_level": sentinel_secret,  # invalid enum value containing sensitive sentinel
        "execution_mode": "VERIFIED_OPERATIONAL",
        "attestation_status": "HARDWARE_ENCLAVE_VERIFIED",
    }

    mock_resp = _make_mock_response(json.dumps(payload).encode("utf-8"))

    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(ValueError) as exc_info:
            client.execute_match(req=req, auth_token=auth_tokens["bank_a"])

    err_str = str(exc_info.value)
    # 1. Public error message is bounded and fixed
    assert err_str == "Coordinator returned invalid signal schema"
    # 2. Public error message NEVER leaks the sentinel secret
    assert sentinel_secret not in err_str
    # 3. Root cause is preserved in __cause__ for server-side diagnosis
    assert exc_info.value.__cause__ is not None
    # 4. Confirm Pydantic raw error did contain the secret, proving our bounded message guarded it
    assert sentinel_secret in str(exc_info.value.__cause__)


def test_malformed_json_and_non_utf8_do_not_disclose_raw_response_content(
    auth_tokens: dict[str, str],
) -> None:
    """Malformed JSON and non-UTF8 response failures do not disclose raw response body content."""
    coord_client = InstitutionalCoordinatorClient(base_url="http://127.0.0.1:8999")
    worker_client = InstitutionalWorkerClient(bank_id="bank_c", base_url="http://127.0.0.1:8998")

    req = CrossBankIntelligenceQueryRequest(
        requesting_bank_id="bank_a",
        target_bank_id="bank_b",
        query_entity_id="cust_test_001",
        consortium_id="consortium_tri_outcomes",
        purpose="AML_COLLABORATIVE_INVESTIGATION",
        execution_mode="VERIFIED_OPERATIONAL",
    )

    coord_raw_secret = "SENTINEL_RAW_SECRET_DATA_XYZ_987"
    worker_raw_secret = "WORKER_SENTINEL_RAW_SECRET_ABC_654"

    # 1. Coordinator malformed JSON
    coord_malformed = _make_mock_response(f"{coord_raw_secret}{{invalid_json".encode())
    with patch("urllib.request.urlopen", return_value=coord_malformed):
        with pytest.raises(ValueError) as exc_info:
            coord_client.execute_match(req=req, auth_token=auth_tokens["bank_a"])
    assert str(exc_info.value) == "Coordinator returned malformed JSON response"
    assert coord_raw_secret not in str(exc_info.value)

    # 2. Coordinator non-UTF8 bytes
    coord_non_utf8 = _make_mock_response(b"SECRET_COORD_BYTES_\xff\xfe")
    with patch("urllib.request.urlopen", return_value=coord_non_utf8):
        with pytest.raises(ValueError) as exc_info:
            coord_client.execute_match(req=req, auth_token=auth_tokens["bank_a"])
    assert str(exc_info.value) == "Coordinator returned non-UTF8 response"
    assert "SECRET_COORD_BYTES" not in str(exc_info.value)

    # 3. Worker malformed JSON
    worker_malformed = _make_mock_response(f"{worker_raw_secret}{{broken".encode())
    with patch("urllib.request.urlopen", return_value=worker_malformed):
        with pytest.raises(ValueError) as exc_info:
            worker_client._post("/worker/store-entity", {"entity": {}})
    assert str(exc_info.value) == "Worker for 'bank_c' returned malformed JSON response"
    assert worker_raw_secret not in str(exc_info.value)

    # 4. Worker non-UTF8 bytes
    worker_non_utf8 = _make_mock_response(b"SECRET_WORKER_BYTES_\xff\xfe")
    with patch("urllib.request.urlopen", return_value=worker_non_utf8):
        with pytest.raises(ValueError) as exc_info:
            worker_client._post("/worker/store-entity", {"entity": {}})
    assert str(exc_info.value) == "Worker for 'bank_c' returned non-UTF8 response"
    assert "SECRET_WORKER_BYTES" not in str(exc_info.value)


def test_multi_counterparty_api_does_not_leak_sentinel_secret_on_schema_validation_error(
    tri_consortium: tuple[ConsortiumGovernanceService, str],
    auth_tokens: dict[str, str],
) -> None:
    """FastAPI router endpoint returns HTTP 400 without leaking sentinel secret from invalid signal."""
    gov_svc, c_id = tri_consortium
    cbi_instance = CrossBankIntelligenceService.get_instance()
    cbi_instance.consortium_service = gov_svc

    test_ent = Entity(
        id="cust_router_ent_secret_leak_guard",
        entity_type=EntityType.CUSTOMER,
        privacy_id="hash_router_cust_secret_leak_guard",
        bank_id="bank_a",
        display_label="Router Leak Guard Test Entity",
        risk_level=RiskLevel.HIGH,
    )
    cbi_instance.entity_service._entities.set("cust_router_ent_secret_leak_guard", _entity_to_dict(test_ent))

    sentinel_secret = "E2E_API_SENTINEL_SECRET_TOKEN_DO_NOT_LEAK_999"
    coord_client = InstitutionalCoordinatorClient(base_url="http://127.0.0.1:8999")

    # Deliberately invalid schema payload containing the sentinel secret
    invalid_payload = {
        "signal_id": "sig-001",
        "consortium_id": c_id,
        "requesting_bank_id": "bank_a",
        "privacy_hash_truncated": "abc1234",
        "match_confirmed": True,
        "match_category": "EXACT_MATCH",
        "match_confidence": 0.99,
        "contributing_institutions_count": 1,
        "sanitized_risk_level": sentinel_secret,  # schema-invalid field value
        "execution_mode": "VERIFIED_OPERATIONAL",
        "attestation_status": "HARDWARE_ENCLAVE_VERIFIED",
    }
    mock_resp = _make_mock_response(json.dumps(invalid_payload).encode("utf-8"))

    def mock_exec_operational(
        req: CrossBankIntelligenceQueryRequest,
        auth_token: str | None,
        auth_principal: AuthenticatedPrincipal,
        audited_actor: str,
    ) -> CrossBankIntelligenceSignal:
        with patch("urllib.request.urlopen", return_value=mock_resp):
            return coord_client.execute_match(req=req, auth_token=auth_tokens["bank_a"])

    client = TestClient(app)
    req_body = {
        "consortium_id": c_id,
        "requesting_bank_id": "bank_a",
        "target_bank_ids": ["bank_b"],
        "query_entity_id": "cust_router_ent_secret_leak_guard",
        "purpose": "AML_COLLABORATIVE_INVESTIGATION",
        "execution_mode": "VERIFIED_OPERATIONAL",
    }

    with patch.object(cbi_instance, "_execute_verified_operational_psi", side_effect=mock_exec_operational):
        resp = client.post(
            "/api/v1/intelligence/cross-bank/query-multi",
            json=req_body,
            headers={"Authorization": f"Bearer {auth_tokens['bank_a']}"},
        )

    # 1. Nonrecoverable failure: HTTP 400 Bad Request
    assert resp.status_code == 400
    # 2. Public detail contains fixed bounded message
    assert resp.json()["detail"] == "Coordinator returned invalid signal schema"
    # 3. Sentinel secret NEVER appears in the HTTP response body
    assert sentinel_secret not in resp.text
    assert sentinel_secret not in str(resp.content)


