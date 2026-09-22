"""Unit and integration test suite for Enterprise AML OpenAPI Drop-in Adapter.

Validates:
- OpenAPI-compatible person ingestion (INDIVIDUAL and LEGAL_ENTITY) with documents and UBOs.
- Transaction ingestion linked to registered customer profiles.
- Real-time monitoring checks with scenario triggers:
  * Sub-€10k structuring / smurfing detection.
  * FATF high-risk / sanctioned country corridors (KP, IR, SY, MM).
  * Rapid velocity bursts.
  * Integration with 9-signal composite risk engine.
- Offline monitoring checks.
- PEP and sanctions screening checks (both profile-based and ad-hoc searches).
- Webhook subscription lifecycle and HMAC-SHA256 event dispatching.
- Adapter operational metrics aggregation.
- Multi-tenant bank isolation.
- FastAPI REST endpoints across both canonical (/api/v1, /api/v2) and dual routes.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.application.schemas.open_aml_schemas import (
    Address,
    Document,
    DocumentType,
    MonitoringAction,
    MonitoringCheckRequest,
    MonitoringMode,
    OpenAMLScreeningCheckRequest,
    OpenAMLWebhookSubscriptionRequest,
    PersonCreateRequest,
    PersonStatus,
    PersonTransactionCreateRequest,
    PersonType,
    UBORecord,
)
from app.application.services.open_aml_service import (
    OpenAMLService,
    get_open_aml_service,
    reset_open_aml_service,
)
from app.main import app

os.environ.setdefault("TESTING", "1")


@pytest.fixture()
def client() -> TestClient:
    reset_open_aml_service()
    return TestClient(app)


@pytest.fixture()
def svc() -> OpenAMLService:
    reset_open_aml_service()
    return get_open_aml_service()


# ── Service Unit Tests ─────────────────────────────────────────────────────────

class TestOpenAMLService:
    """Unit tests for OpenAMLService business logic."""

    def test_create_and_get_individual_person(self, svc: OpenAMLService) -> None:
        req = PersonCreateRequest(
            person_type=PersonType.INDIVIDUAL,
            status=PersonStatus.ACTIVE,
            first_name="Jean-Luc",
            last_name="Picard",
            date_of_birth="1965-07-13",
            nationality="FR",
            country_of_residence="FR",
            addresses=[
                Address(street="Rue de Paris 42", city="Paris", postal_code="75001", country="FR")
            ],
            documents=[
                Document(document_type=DocumentType.PASSPORT, document_number="FR987654321", country_of_issue="FR")
            ],
        )

        created = svc.create_person("bank_a", req)
        assert created.person_id is not None
        assert created.person_type == PersonType.INDIVIDUAL
        assert created.display_name == "Jean-Luc Picard"
        assert created.nationality == "FR"
        assert created.documents_count == 1

        retrieved = svc.get_person(created.person_id, tenant_id="bank_a")
        assert retrieved is not None
        assert retrieved.person_id == created.person_id
        assert retrieved.first_name == "Jean-Luc"

    def test_create_legal_entity_with_ubos(self, svc: OpenAMLService) -> None:
        req = PersonCreateRequest(
            person_type=PersonType.LEGAL_ENTITY,
            company_name="Nordic Maritime Logistics ApS",
            registration_number="DK-48192019",
            country_of_incorporation="DK",
            ubos=[
                UBORecord(full_name="Henrik Lindqvist", ownership_percentage=60.0, is_pep=False),
                UBORecord(full_name="Astrid Lindqvist", ownership_percentage=40.0, is_pep=True),
            ],
        )

        created = svc.create_person("bank_a", req)
        assert created.person_type == PersonType.LEGAL_ENTITY
        assert created.company_name == "Nordic Maritime Logistics ApS"
        assert created.ubo_count == 2

    def test_get_nonexistent_person_returns_none(self, svc: OpenAMLService) -> None:
        result = svc.get_person("non-existent-id", tenant_id="bank_a")
        assert result is None

    def test_add_transaction_for_person(self, svc: OpenAMLService) -> None:
        person = svc.create_person(
            "bank_a",
            PersonCreateRequest(person_type=PersonType.INDIVIDUAL, first_name="Alice", last_name="Smith"),
        )

        tx_req = PersonTransactionCreateRequest(
            external_transaction_id="TX-EU-9901",
            amount=Decimal("1500.00"),
            currency="EUR",
            direction="OUTGOING",
            counterparty_name="Bob Jones",
            counterparty_iban="DE89370400440532013000",
            originator_country="FR",
            beneficiary_country="DE",
        )

        tx = svc.add_transaction("bank_a", person.person_id, tx_req)
        assert tx.transaction_id is not None
        assert tx.person_id == person.person_id
        assert tx.amount == 1500.00
        assert tx.currency == "EUR"

    def test_add_transaction_for_nonexistent_person_raises(self, svc: OpenAMLService) -> None:
        tx_req = PersonTransactionCreateRequest(
            external_transaction_id="TX-FAIL-1",
            amount=Decimal("500.00"),
            currency="EUR",
        )
        with pytest.raises(ValueError, match="not found"):
            svc.add_transaction("bank_a", "ghost-person", tx_req)

    def test_realtime_monitoring_check_structuring(self, svc: OpenAMLService) -> None:
        person = svc.create_person(
            "bank_a",
            PersonCreateRequest(person_type=PersonType.INDIVIDUAL, first_name="Marco", last_name="Rossi"),
        )
        # Structuring amount sub-€10k: €9,850
        tx = svc.add_transaction(
            "bank_a",
            person.person_id,
            PersonTransactionCreateRequest(
                external_transaction_id="TX-STRUC-1",
                amount=Decimal("9850.00"),
                currency="EUR",
                beneficiary_country="IT",
            ),
        )

        check_req = MonitoringCheckRequest(
            mode=MonitoringMode.ONLINE,
        )
        resp = svc.perform_monitoring_check("bank_a", tx.transaction_id, check_req)

        assert resp.transaction_id == tx.transaction_id
        assert resp.mode == MonitoringMode.ONLINE
        assert any("STRUCTURING" in s for s in resp.triggered_scenarios)
        assert resp.action in (MonitoringAction.REVIEW, MonitoringAction.BLOCK)
        assert resp.composite_risk_score > 300

    def test_realtime_monitoring_check_fatf_sanctioned_corridor(self, svc: OpenAMLService) -> None:
        person = svc.create_person(
            "bank_a",
            PersonCreateRequest(person_type=PersonType.INDIVIDUAL, first_name="Dmitri", last_name="Volkov"),
        )
        # Beneficiary country is KP (North Korea)
        tx = svc.add_transaction(
            "bank_a",
            person.person_id,
            PersonTransactionCreateRequest(
                external_transaction_id="TX-FATF-1",
                amount=Decimal("4500.00"),
                currency="EUR",
                beneficiary_country="KP",
            ),
        )

        check_req = MonitoringCheckRequest(mode=MonitoringMode.ONLINE)
        resp = svc.perform_monitoring_check("bank_a", tx.transaction_id, check_req)

        assert any("FATF_SANCTIONED_CORRIDOR" in s for s in resp.triggered_scenarios)
        assert resp.action == MonitoringAction.BLOCK
        assert resp.composite_risk_score >= 700

    def test_realtime_monitoring_check_velocity_burst(self, svc: OpenAMLService) -> None:
        person = svc.create_person(
            "bank_a",
            PersonCreateRequest(person_type=PersonType.INDIVIDUAL, first_name="Speedy", last_name="Gonzales"),
        )
        # Create 5 rapid transactions for the same person
        txs = []
        for i in range(5):
            t = svc.add_transaction(
                "bank_a",
                person.person_id,
                PersonTransactionCreateRequest(
                    external_transaction_id=f"TX-VEL-{i}",
                    amount=Decimal("200.00"),
                    currency="EUR",
                ),
            )
            txs.append(t)

        resp = svc.perform_monitoring_check(
            "bank_a", txs[-1].transaction_id, MonitoringCheckRequest(mode=MonitoringMode.ONLINE)
        )
        assert any("VELOCITY" in s for s in resp.triggered_scenarios)

    def test_offline_monitoring_check(self, svc: OpenAMLService) -> None:
        person = svc.create_person(
            "bank_a",
            PersonCreateRequest(person_type=PersonType.INDIVIDUAL, first_name="Normal", last_name="User"),
        )
        tx = svc.add_transaction(
            "bank_a",
            person.person_id,
            PersonTransactionCreateRequest(
                external_transaction_id="TX-NORM-1",
                amount=Decimal("50.00"),
                currency="EUR",
                beneficiary_country="DE",
            ),
        )

        resp = svc.perform_monitoring_check(
            "bank_a", tx.transaction_id, MonitoringCheckRequest(mode=MonitoringMode.OFFLINE)
        )
        assert resp.mode == MonitoringMode.OFFLINE
        assert resp.action == MonitoringAction.ALLOW
        assert len(resp.triggered_scenarios) == 0

    def test_screening_check_for_person(self, svc: OpenAMLService) -> None:
        # Create person with name similar to a sanctioned entity in real watchlist
        person = svc.create_person(
            "bank_a",
            PersonCreateRequest(
                person_type=PersonType.INDIVIDUAL,
                first_name="Viktor",
                last_name="Bout",
                nationality="RU",
            ),
        )

        resp = svc.perform_screening_check(
            "bank_a",
            person.person_id,
            OpenAMLScreeningCheckRequest(match_threshold=70.0),
        )
        assert resp.person_id == person.person_id
        assert isinstance(resp.matches, list)
        assert resp.search_id is not None

    def test_adhoc_screening_search(self, svc: OpenAMLService) -> None:
        resp = svc.screen_search(
            "bank_a",
            OpenAMLScreeningCheckRequest(
                query_name="Viktor Anatolyevich Bout",
                match_threshold=60.0,
            ),
        )
        assert resp.hit_count >= 0
        assert resp.search_id is not None

    def test_webhook_subscription_and_dispatch(self, svc: OpenAMLService) -> None:
        sub = svc.register_webhook_subscription(
            OpenAMLWebhookSubscriptionRequest(
                callback_url="https://compliance.bank-a.internal/aml-webhooks",
                events=["ALERT_CREATED", "SCREENING_ALERT_CREATED"],
            ),
            tenant_id="bank_a",
        )
        assert sub.subscription_id is not None
        assert sub.signing_secret is not None
        assert sub.status == "ACTIVE"

        # Trigger an alert check to cause a webhook dispatch
        person = svc.create_person(
            "bank_a",
            PersonCreateRequest(person_type=PersonType.INDIVIDUAL, first_name="Alert", last_name="Tester"),
        )
        tx = svc.add_transaction(
            "bank_a",
            person.person_id,
            PersonTransactionCreateRequest(
                external_transaction_id="TX-ALERT-WH",
                amount=Decimal("9900.00"),
                currency="EUR",
            ),
        )
        svc.perform_monitoring_check(
            "bank_a", tx.transaction_id, MonitoringCheckRequest(mode=MonitoringMode.ONLINE)
        )

        events = svc.get_webhook_events("bank_a")
        assert len(events) >= 1
        assert events[0].event_type.value == "ALERT_CREATED"
        assert events[0].payload["transaction_id"] == tx.transaction_id

    def test_adapter_metrics(self, svc: OpenAMLService) -> None:
        svc.create_person(
            "bank_a",
            PersonCreateRequest(person_type=PersonType.INDIVIDUAL, first_name="Metrics", last_name="One"),
        )
        metrics = svc.get_metrics("bank_a")
        assert metrics.total_persons_registered == 1
        assert metrics.total_monitoring_checks == 0
        assert metrics.adapter_version == "2.0.0"

    def test_multi_tenant_bank_isolation(self, svc: OpenAMLService) -> None:
        p_a = svc.create_person(
            "bank_alpha",
            PersonCreateRequest(person_type=PersonType.INDIVIDUAL, first_name="Alpha", last_name="User"),
        )
        p_b = svc.create_person(
            "bank_beta",
            PersonCreateRequest(person_type=PersonType.INDIVIDUAL, first_name="Beta", last_name="User"),
        )

        # Alpha cannot see Beta's person
        assert svc.get_person(p_a.person_id, tenant_id="bank_alpha") is not None
        assert svc.get_person(p_b.person_id, tenant_id="bank_alpha") is None

        # Beta cannot see Alpha's person
        assert svc.get_person(p_b.person_id, tenant_id="bank_beta") is not None
        assert svc.get_person(p_a.person_id, tenant_id="bank_beta") is None


# ── FastAPI Router Integration Tests ──────────────────────────────────────────

class TestOpenAMLRouter:
    """Integration tests for FastAPI endpoints in open_aml_adapter."""

    def test_create_and_get_person_api_v2(self, client: TestClient) -> None:
        payload = {
            "person_type": "INDIVIDUAL",
            "first_name": "Alexander",
            "last_name": "Dubcek",
            "nationality": "SK",
            "country_of_residence": "SK",
            "documents": [
                {
                    "document_type": "NATIONAL_ID",
                    "document_number": "SK-781920",
                    "country_of_issue": "SK",
                }
            ],
        }
        res = client.post(
            "/api/v2/persons",
            json=payload,
            headers={"X-Bank-ID": "bank_test"},
        )
        assert res.status_code == 201
        data = res.json()
        assert data["person_id"] is not None
        assert data["display_name"] == "Alexander Dubcek"

        person_id = data["person_id"]

        # Fetch person via GET /api/v2/persons/{person_id}
        res_get = client.get(
            f"/api/v2/persons/{person_id}",
            headers={"X-Bank-ID": "bank_test"},
        )
        assert res_get.status_code == 200
        assert res_get.json()["person_id"] == person_id

    def test_create_person_api_v1_compatibility(self, client: TestClient) -> None:
        payload = {
            "person_type": "LEGAL_ENTITY",
            "company_name": "Baltic Cybernetics OU",
            "country_of_incorporation": "EE",
        }
        res = client.post(
            "/api/v1/persons",
            json=payload,
            headers={"X-Bank-ID": "bank_test"},
        )
        assert res.status_code == 201
        assert res.json()["company_name"] == "Baltic Cybernetics OU"

    def test_get_person_not_found(self, client: TestClient) -> None:
        res = client.get(
            "/api/v2/persons/nonexistent-id-12345",
            headers={"X-Bank-ID": "bank_test"},
        )
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    def test_create_transaction_for_person_api(self, client: TestClient) -> None:
        # First create person
        p_res = client.post(
            "/api/v2/persons",
            json={"person_type": "INDIVIDUAL", "first_name": "Elena", "last_name": "Vasilieva"},
            headers={"X-Bank-ID": "bank_test"},
        )
        person_id = p_res.json()["person_id"]

        # Post transaction
        tx_payload = {
            "external_transaction_id": "TX-API-441",
            "amount": "12500.50",
            "currency": "EUR",
            "direction": "OUTGOING",
            "counterparty_name": "Stichting Logistics",
            "beneficiary_country": "NL",
        }
        res = client.post(
            f"/api/v1/persons/{person_id}/transactions",
            json=tx_payload,
            headers={"X-Bank-ID": "bank_test"},
        )
        assert res.status_code == 201
        data = res.json()
        assert data["transaction_id"] is not None
        assert data["person_id"] == person_id
        assert data["amount"] == 12500.50

    def test_create_transaction_unknown_person_returns_404(self, client: TestClient) -> None:
        tx_payload = {
            "external_transaction_id": "TX-FAIL",
            "amount": "100.00",
            "currency": "EUR",
        }
        res = client.post(
            "/api/v1/persons/unknown-person-id/transactions",
            json=tx_payload,
            headers={"X-Bank-ID": "bank_test"},
        )
        assert res.status_code == 404

    def test_perform_monitoring_check_api(self, client: TestClient) -> None:
        # Create person and transaction
        p_res = client.post(
            "/api/v2/persons",
            json={"person_type": "INDIVIDUAL", "first_name": "Hans", "last_name": "Gruber"},
            headers={"X-Bank-ID": "bank_test"},
        )
        person_id = p_res.json()["person_id"]

        tx_res = client.post(
            f"/api/v1/persons/{person_id}/transactions",
            json={
                "external_transaction_id": "TX-MON-99",
                "amount": "9950.00",
                "currency": "EUR",
                "beneficiary_country": "DE",
            },
            headers={"X-Bank-ID": "bank_test"},
        )
        transaction_id = tx_res.json()["transaction_id"]

        # Run monitoring check
        check_res = client.post(
            f"/api/v1/transactions/{transaction_id}/monitoring-checks",
            json={"mode": "ONLINE", "channel": "MOBILE"},
            headers={"X-Bank-ID": "bank_test"},
        )
        assert check_res.status_code == 200
        check_data = check_res.json()
        assert check_data["check_id"] is not None
        assert check_data["transaction_id"] == transaction_id
        assert check_data["action"] in ["ALLOW", "REVIEW", "BLOCK"]
        assert len(check_data["triggered_scenarios"]) > 0

    def test_perform_monitoring_check_unknown_tx_returns_404(self, client: TestClient) -> None:
        res = client.post(
            "/api/v1/transactions/unknown-tx-9999/monitoring-checks",
            json={"mode": "ONLINE"},
            headers={"X-Bank-ID": "bank_test"},
        )
        assert res.status_code == 404

    def test_screening_checks_endpoints(self, client: TestClient) -> None:
        # 1. Person screening check
        p_res = client.post(
            "/api/v2/persons",
            json={"person_type": "INDIVIDUAL", "first_name": "Sergei", "last_name": "Ivanov"},
            headers={"X-Bank-ID": "bank_test"},
        )
        person_id = p_res.json()["person_id"]

        res_screen = client.post(
            f"/api/v1/persons/{person_id}/screening-checks",
            json={"match_threshold": 75.0},
            headers={"X-Bank-ID": "bank_test"},
        )
        assert res_screen.status_code == 200
        assert res_screen.json()["search_id"] is not None

        # 2. Ad-hoc screening search
        res_adhoc = client.post(
            "/api/v2/screening-searches",
            json={"query_name": "Al-Qaeda", "match_threshold": 50.0},
            headers={"X-Bank-ID": "bank_test"},
        )
        assert res_adhoc.status_code == 200
        assert res_adhoc.json()["search_id"] is not None

    def test_webhook_subscriptions_and_events_api(self, client: TestClient) -> None:
        # Register webhook
        wh_res = client.post(
            "/api/v1/aml-adapter/webhooks/subscriptions",
            json={
                "callback_url": "https://bank.example.com/aml/hooks",
                "events": ["ALERT_CREATED", "SCREENING_ALERT_CREATED"],
                "description": "Integration test webhook",
            },
            headers={"X-Bank-ID": "bank_test"},
        )
        assert wh_res.status_code == 201
        assert wh_res.json()["subscription_id"] is not None
        assert wh_res.json()["signing_secret"] is not None

        # Query events
        ev_res = client.get(
            "/api/v1/aml-adapter/webhooks/events",
            headers={"X-Bank-ID": "bank_test"},
        )
        assert ev_res.status_code == 200
        assert isinstance(ev_res.json(), list)

    def test_metrics_api(self, client: TestClient) -> None:
        res = client.get(
            "/api/v1/aml-adapter/metrics",
            headers={"X-Bank-ID": "bank_test"},
        )
        assert res.status_code == 200
        data = res.json()
        assert "total_persons_registered" in data
        assert "total_monitoring_checks" in data
        assert data["adapter_version"] == "2.0.0"
