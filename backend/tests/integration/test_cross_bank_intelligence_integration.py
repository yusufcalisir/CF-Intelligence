"""Integration test suite for Cross-Bank Intelligence HTTP Endpoints.

Verifies:
1. Dual-routing (/api/v1 and /v1 prefixes).
2. End-to-end authorized intelligence queries across two institutional contexts.
3. Strict tenant header isolation enforcement (preventing header spoofing).
4. Evidence ledger integration and sub-resource binding.
5. Consortium sharing policy endpoint and disclosure rules.
"""

from __future__ import annotations

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
def isolate_integration_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    store_file = tmp_path / "consortium_integration_store.json"
    account_file = tmp_path / "account_integration_store.json"
    monkeypatch.setenv("CFI_CONSORTIUM_STORE_PATH", str(store_file))
    monkeypatch.setenv("CFI_ACCOUNT_STORE_PATH", str(account_file))
    DurableConsortiumStore.set_instance(None)
    AuthenticationService.reset_instance()
    yield
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
            "integ_admin_a", "AdminPass123!", "bank_a", roles=["admin", "analyst"], clearance_level=3
        )
    else:
        u_a.roles = ["admin", "analyst"]
        auth_svc.identity_store.save_user(u_a)

    u_b = auth_svc.get_user("integ_admin_b")
    if not u_b:
        u_b = auth_svc.register_user(
            "integ_admin_b", "AdminPass123!", "bank_b", roles=["admin", "analyst"], clearance_level=3
        )
    else:
        u_b.roles = ["admin", "analyst"]
        auth_svc.identity_store.save_user(u_b)

    return {
        "bank_a": auth_svc.create_token_bundle(u_a).access_token,
        "bank_b": auth_svc.create_token_bundle(u_b).access_token,
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
            "execution_mode": "VERIFIED_OPERATIONAL",
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
            assert "bank_b" in data["contributing_institutions"]
            assert data["sanitized_risk_level"] in ("CRITICAL", "HIGH")
            assert data["attestation_status"] == "UNAVAILABLE_NO_HARDWARE_ENCLAVE"
            assert "advisory and privacy-preserving" in data["disclaimer"]

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
        """Test caller passing X-Tenant-ID for bank_b while requesting as bank_a is rejected."""
        payload = {
            "requesting_bank_id": "bank_a",
            "query_entity_id": setup_consortium_entities["entity_a_id"],
            "consortium_id": "integ_alliance",
        }
        headers = {
            "Authorization": f"Bearer {auth_tokens['bank_a']}",
            "X-Tenant-ID": "bank_b",  # Spoofed tenant header
        }
        resp = client.post("/api/v1/intelligence/cross-bank/query", json=payload, headers=headers)
        assert resp.status_code == 403

    def test_http_attach_signal_to_case_evidence_and_subresource(
        self,
        client: TestClient,
        auth_tokens: dict[str, str],
        setup_consortium_entities: dict[str, Any],
    ) -> None:
        """Test attaching verified cross-bank signal to an open case via both dedicated and subresource routes."""
        from app.application.services.case_service import CaseManagementService

        case_svc = CaseManagementService()
        case = case_svc.create_case(
            title="Consortium Mules Review",
            bank_id="bank_a",
            assigned_to="analyst_alice",
            priority=CasePriority.P1_CRITICAL,
        )

        signal_payload = {
            "signal_id": "sig-integ-101",
            "consortium_id": "integ_alliance",
            "requesting_bank_id": "bank_a",
            "query_entity_id": setup_consortium_entities["entity_a_id"],
            "privacy_hash_truncated": "hash_abc...",
            "match_confirmed": True,
            "match_category": "EXACT_MATCH",
            "match_confidence": 1.0,
            "contributing_institutions_count": 1,
            "contributing_institutions": ["bank_b"],
            "sanitized_risk_level": "CRITICAL",
            "sanitized_typology_indicators": ["CROSS_BANK_ENTITY_OBSERVED"],
            "graph_context": {"sanitized_degree_band": "BILATERAL"},
            "provenance": {"governance_authority": "consortium:integ_alliance"},
            "execution_mode": "VERIFIED_OPERATIONAL",
            "attestation_status": "UNAVAILABLE_NO_HARDWARE_ENCLAVE",
        }

        # 1. Attach via /api/v1/intelligence/cross-bank/attach-to-case
        attach_req = {
            "case_id": case.id,
            "signal": signal_payload,
            "notes": "Linked from cross-bank match query.",
        }
        resp = client.post(
            "/api/v1/intelligence/cross-bank/attach-to-case",
            json=attach_req,
            headers={"X-Tenant-ID": "bank_a"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["case_id"] == case.id
        assert data["evidence_type"] == "CROSS_BANK_INTELLIGENCE"
        assert len(data["content_hash"]) == 64
        assert data["timeline_event_recorded"] is True

        # 2. Attach via subresource /api/v1/cases/{case_id}/evidence/cross-bank-intelligence
        resp_sub = client.post(
            f"/api/v1/cases/{case.id}/evidence/cross-bank-intelligence",
            json={"signal": signal_payload, "notes": "Subresource attach verification"},
            headers={"X-Tenant-ID": "bank_a"},
        )
        assert resp_sub.status_code == 200
        data_sub = resp_sub.json()
        assert data_sub["case_id"] == case.id
        assert data_sub["evidence_type"] == "CROSS_BANK_INTELLIGENCE"

    def test_http_consortium_policy_endpoint(
        self,
        client: TestClient,
        setup_consortium_entities: dict[str, Any],
    ) -> None:
        """Test GET policy returns disclosure limits and active member counts."""
        for prefix in ("/api/v1/intelligence/cross-bank", "/v1/intelligence/cross-bank"):
            resp = client.get(
                f"{prefix}/consortiums/integ_alliance/policy",
                headers={"X-Tenant-ID": "bank_a"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["consortium_id"] == "integ_alliance"
            assert data["active_member_count"] == 2
            assert data["permitted_disclosure"]["allow_raw_pii_sharing"] is False
            assert data["permitted_disclosure"]["allow_institution_scoped_signals"] is True
