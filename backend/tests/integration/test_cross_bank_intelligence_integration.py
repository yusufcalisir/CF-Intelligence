"""Integration test suite for Cross-Bank Intelligence HTTP Endpoints.

Verifies:
1. Dual-routing (/api/v1 and /v1 prefixes).
2. End-to-end authorized intelligence queries across institutional boundaries.
3. Strict institutional authentication (HTTP 401 on missing/invalid auth).
4. Strict tenant header isolation enforcement (preventing header spoofing).
5. Anti-forgery evidence ledger integration (rejecting unissued signals).
6. Consortium sharing policy endpoint access control (HTTP 403 on non-members).
7. Operational privacy mode fail-closed enforcement (HTTP 412 when hardware enclaves absent).
"""

from __future__ import annotations

import datetime
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.services.consortium_service import ConsortiumGovernanceService
from app.application.services.entity_resolution import EntityResolutionService
from app.domain.consortium_governance import MemberRole
from app.domain.enums import CasePriority, EntityType, RiskLevel
from app.infrastructure.security.auth_service import AuthenticationService
from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore
from app.main import app


@pytest.fixture(autouse=True)
def isolate_integration_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    store_file = tmp_path / "consortium_integration_store.json"
    account_file = tmp_path / "account_integration_store.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
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
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_tokens() -> dict[str, str]:
    auth_svc = AuthenticationService.get_instance()
    u_a = auth_svc.get_user("integ_admin_a")
    if not u_a:
        u_a = auth_svc.register_user(
            "integ_admin_a",
            "AdminPass123!",
            "bank_a",
            roles=["admin", "analyst"],
            clearance_level=3,
        )
    else:
        u_a.roles = ["admin", "analyst"]
        auth_svc.identity_store.save_user(u_a)

    u_b = auth_svc.get_user("integ_admin_b")
    if not u_b:
        u_b = auth_svc.register_user(
            "integ_admin_b",
            "AdminPass123!",
            "bank_b",
            roles=["admin", "analyst"],
            clearance_level=3,
        )
    else:
        u_b.roles = ["admin", "analyst"]
        auth_svc.identity_store.save_user(u_b)

    u_outsider = auth_svc.get_user("integ_outsider_c")
    if not u_outsider:
        u_outsider = auth_svc.register_user(
            "integ_outsider_c",
            "AdminPass123!",
            "bank_c",
            roles=["admin", "analyst"],
            clearance_level=3,
        )
    else:
        u_outsider.roles = ["admin", "analyst"]
        auth_svc.identity_store.save_user(u_outsider)

    return {
        "bank_a": auth_svc.create_token_bundle(u_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_b).access_token,
        "bank_c": auth_svc.create_token_bundle(u_outsider).access_token,
    }


@pytest.fixture
def setup_consortium_entities(auth_tokens: dict[str, str]) -> dict[str, Any]:
    gov_svc = ConsortiumGovernanceService()
    gov_svc.create_consortium(
        consortium_id="integ_alliance",
        name="Cross-Bank Integration Alliance",
        founder_bank_id="bank_a",
        auth_token=auth_tokens["bank_a"],
    )

    from app.application.services.model_registry import create_trusted_internal_context

    ctx = create_trusted_internal_context(
        service_name="consortium_coordinator",
        action="add_member",
        resource_type="consortium",
        resource_id="integ_alliance",
        target_bank_id="bank_b",
    )
    gov_svc.add_member(
        "integ_alliance",
        "bank_b",
        role=MemberRole.FULL_MEMBER,
        voting_power=1.0,
        trusted_context=ctx,
    )

    entity_svc = EntityResolutionService()
    shared_email = "integ.target.suspect@ring.com"
    ent_a = entity_svc.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier=shared_email,
        bank_id="bank_a",
    )
    entity_svc.update_risk_level(ent_a.id, RiskLevel.HIGH)

    ent_b = entity_svc.create_entity(
        entity_type=EntityType.CUSTOMER,
        raw_identifier=shared_email,
        bank_id="bank_b",
    )
    entity_svc.update_risk_level(ent_b.id, RiskLevel.CRITICAL)

    return {
        "consortium_id": "integ_alliance",
        "entity_a_id": ent_a.id,
        "entity_b_id": ent_b.id,
        "privacy_id": ent_a.privacy_id,
    }


class TestCrossBankIntelligenceIntegration:
    def test_http_cross_bank_query_dual_routing(
        self,
        client: TestClient,
        auth_tokens: dict[str, str],
        setup_consortium_entities: dict[str, Any],
    ) -> None:
        """Test POST query on both /api/v1/intelligence/cross-bank/query and /v1/..."""
        payload = {
            "requesting_bank_id": "bank_a",
            "query_entity_id": setup_consortium_entities["entity_a_id"],
            "consortium_id": "integ_alliance",
            "target_bank_id": "bank_b",
            "execution_mode": "SIMULATED_RESEARCH",
        }
        headers = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}

        for prefix in ("/api/v1/intelligence/cross-bank", "/v1/intelligence/cross-bank"):
            resp = client.post(f"{prefix}/query", json=payload, headers=headers)
            assert resp.status_code == 200, f"Failed at {prefix}: {resp.text}"
            data = resp.json()
            assert data["match_confirmed"] is True
            assert data["match_category"] == "EXACT_MATCH"
            assert data["match_confidence"] == 1.0
            assert data["contributing_institutions_count"] == 1
            assert data["execution_mode"] == "SIMULATED_RESEARCH"
            assert data["attestation_status"] == "SOFTWARE_EMULATED"
            assert "advisory and privacy-preserving" in data["disclaimer"]

    def test_http_query_unauthenticated_returns_401(
        self,
        client: TestClient,
        setup_consortium_entities: dict[str, Any],
    ) -> None:
        """Test query without Authorization header returns HTTP 401 Unauthorized."""
        payload = {
            "requesting_bank_id": "bank_a",
            "query_entity_id": setup_consortium_entities["entity_a_id"],
            "consortium_id": "integ_alliance",
        }
        resp = client.post("/api/v1/intelligence/cross-bank/query", json=payload)
        assert resp.status_code == 401

    def test_http_tenant_header_without_auth_returns_401(
        self,
        client: TestClient,
        setup_consortium_entities: dict[str, Any],
    ) -> None:
        """Test caller supplying X-Tenant-ID header without valid credentials returns HTTP 401."""
        payload = {
            "requesting_bank_id": "bank_a",
            "query_entity_id": setup_consortium_entities["entity_a_id"],
            "consortium_id": "integ_alliance",
        }
        resp = client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=payload,
            headers={"X-Tenant-ID": "bank_a"},
        )
        assert resp.status_code == 401

    def test_http_operational_mode_request_returns_412_precondition_failed(
        self,
        client: TestClient,
        auth_tokens: dict[str, str],
        setup_consortium_entities: dict[str, Any],
    ) -> None:
        """Test requesting VERIFIED_OPERATIONAL fails closed with HTTP 412 Precondition Failed."""
        payload = {
            "requesting_bank_id": "bank_a",
            "query_entity_id": setup_consortium_entities["entity_a_id"],
            "consortium_id": "integ_alliance",
            "execution_mode": "VERIFIED_OPERATIONAL",
        }
        headers = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}
        resp = client.post("/api/v1/intelligence/cross-bank/query", json=payload, headers=headers)
        assert resp.status_code == 412
        assert "Operational privacy attestation unavailable" in resp.json()["detail"]

    def test_http_query_unauthorized_consortium_returns_403(
        self,
        client: TestClient,
        auth_tokens: dict[str, str],
        setup_consortium_entities: dict[str, Any],
    ) -> None:
        """Test query with non-existent or unjoined consortium returns 403 Forbidden."""
        payload = {
            "requesting_bank_id": "bank_a",
            "query_entity_id": setup_consortium_entities["entity_a_id"],
            "consortium_id": "non_existent_consortium_xyz",
        }
        headers = {"Authorization": f"Bearer {auth_tokens['bank_a']}"}
        resp = client.post("/api/v1/intelligence/cross-bank/query", json=payload, headers=headers)
        assert resp.status_code == 403

    def test_http_tenant_header_isolation_enforcement(
        self,
        client: TestClient,
        auth_tokens: dict[str, str],
        setup_consortium_entities: dict[str, Any],
    ) -> None:
        """Test caller passing X-Tenant-ID for bank_b while authenticated as bank_a is rejected."""
        payload = {
            "requesting_bank_id": "bank_a",
            "query_entity_id": setup_consortium_entities["entity_a_id"],
            "consortium_id": "integ_alliance",
        }
        headers = {
            "Authorization": f"Bearer {auth_tokens['bank_a']}",
            "X-Tenant-ID": "bank_b",  # Conflicting tenant header
        }
        resp = client.post("/api/v1/intelligence/cross-bank/query", json=payload, headers=headers)
        assert resp.status_code == 403

    def test_http_attach_authoritative_signal_to_case_evidence(
        self,
        client: TestClient,
        auth_tokens: dict[str, str],
        setup_consortium_entities: dict[str, Any],
    ) -> None:
        """Test issuing an authoritative signal and attaching it via both routes."""
        from app.application.services.case_service import CaseManagementService

        # 1. Issue authoritative signal
        query_payload = {
            "requesting_bank_id": "bank_a",
            "query_entity_id": setup_consortium_entities["entity_a_id"],
            "consortium_id": "integ_alliance",
            "target_bank_id": "bank_b",
            "execution_mode": "SIMULATED_RESEARCH",
        }
        q_resp = client.post(
            "/api/v1/intelligence/cross-bank/query",
            json=query_payload,
            headers={"Authorization": f"Bearer {auth_tokens['bank_a']}"},
        )
        assert q_resp.status_code == 200
        signal_id = q_resp.json()["signal_id"]

        # Mark signal authoritative and admitted for authoritative HTTP evidence attachment test
        import hashlib
        import json

        from app.application.services.cross_bank_intelligence import CrossBankIntelligenceService
        intel_svc = CrossBankIntelligenceService.get_instance()
        record = intel_svc._signal_store.get(signal_id)
        assert record is not None
        rcp_id = f"rcp_{signal_id}"
        sig_data = dict(record["signal_data"])
        sig_data["execution_mode"] = "VERIFIED_OPERATIONAL"
        sig_data["is_authoritative"] = True
        sig_data["is_synthetic"] = False
        record["signal_data"] = sig_data
        record["content_hash"] = hashlib.sha256(json.dumps(sig_data, sort_keys=True).encode("utf-8")).hexdigest()
        record["is_authoritative"] = True
        record["is_synthetic"] = False
        record["execution_mode"] = "VERIFIED_OPERATIONAL"
        record["operational_status"] = "OPERATIONAL"
        record["admission_status"] = "COMMITTED"
        record["publication_status"] = "PUBLISHED"
        record["is_attachable"] = True
        record["target_bank_id"] = "bank_b"
        record["receipt_id"] = rcp_id
        intel_svc._signal_store.set(signal_id, record)
        intel_svc._admitted_receipts_store.set(
            rcp_id,
            {
                "admitted_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "signal_id": signal_id,
                "receipt_id": rcp_id,
                "content_hash": record["content_hash"],
                "requesting_bank_id": "bank_a",
                "target_bank_id": "bank_b",
                "consortium_id": "integ_alliance",
                "execution_mode": "VERIFIED_OPERATIONAL",
            },
        )

        # 2. Create case
        case_svc = CaseManagementService()
        case = case_svc.create_case(
            title="Consortium Mules Review",
            bank_id="bank_a",
            assigned_to="analyst_alice",
            priority=CasePriority.P1_CRITICAL,
        )

        # 3. Attach via /api/v1/intelligence/cross-bank/attach-to-case
        attach_req = {
            "case_id": case.id,
            "signal_id": signal_id,
            "notes": "Linked from cross-bank match query.",
        }
        resp = client.post(
            "/api/v1/intelligence/cross-bank/attach-to-case",
            json=attach_req,
            headers={"Authorization": f"Bearer {auth_tokens['bank_a']}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["case_id"] == case.id
        assert data["signal_id"] == signal_id
        assert data["evidence_type"] == "CROSS_BANK_INTELLIGENCE"
        assert len(data["content_hash"]) == 64
        assert data["timeline_event_recorded"] is True

        # 4. Attach via subresource /api/v1/cases/{case_id}/evidence/cross-bank-intelligence (idempotent replay)
        resp_sub = client.post(
            f"/api/v1/cases/{case.id}/evidence/cross-bank-intelligence",
            json={"signal_id": signal_id, "notes": "Subresource attach verification"},
            headers={"Authorization": f"Bearer {auth_tokens['bank_a']}"},
        )
        assert resp_sub.status_code == 200
        data_sub = resp_sub.json()
        assert data_sub["case_id"] == case.id
        assert data_sub["signal_id"] == signal_id
        assert data_sub["is_duplicate_submission"] is True

    def test_http_forged_signal_attachment_returns_400(
        self,
        client: TestClient,
        auth_tokens: dict[str, str],
    ) -> None:
        """Test attempting to attach an unissued fabricated signal_id returns HTTP 400."""
        from app.application.services.case_service import CaseManagementService

        case_svc = CaseManagementService()
        case = case_svc.create_case(title="Forgery Case", bank_id="bank_a")

        attach_req = {
            "case_id": case.id,
            "signal_id": "fabricated-fake-signal-9999",
            "notes": "Malicious forgery attempt",
        }
        resp = client.post(
            "/api/v1/intelligence/cross-bank/attach-to-case",
            json=attach_req,
            headers={"Authorization": f"Bearer {auth_tokens['bank_a']}"},
        )
        assert resp.status_code == 400
        assert "not found or was never issued" in resp.json()["detail"]

    def test_http_consortium_policy_endpoint_access_control(
        self,
        client: TestClient,
        auth_tokens: dict[str, str],
        setup_consortium_entities: dict[str, Any],
    ) -> None:
        """Test GET policy requires authenticated member and rejects non-member with HTTP 403."""
        # 1. Member bank_a can view policy
        for prefix in ("/api/v1/intelligence/cross-bank", "/v1/intelligence/cross-bank"):
            resp = client.get(
                f"{prefix}/consortiums/integ_alliance/policy",
                headers={"Authorization": f"Bearer {auth_tokens['bank_a']}"},
            )
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["consortium_id"] == "integ_alliance"
            assert data["active_member_count"] == 2
            assert data["permitted_disclosure"]["allow_raw_pii_sharing"] is False
            assert data["permitted_disclosure"]["allow_institution_scoped_signals"] is True

        # 2. Non-member bank_c is rejected with HTTP 403
        resp_c = client.get(
            "/api/v1/intelligence/cross-bank/consortiums/integ_alliance/policy",
            headers={"Authorization": f"Bearer {auth_tokens['bank_c']}"},
        )
        assert resp_c.status_code == 403
        assert "not an authorized member" in resp_c.json()["detail"]
