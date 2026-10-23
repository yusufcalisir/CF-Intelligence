"""End-to-end integration tests for Transaction-to-Investigation workflow.

Verifies the canonical institutional fraud detection journey:
1. Authenticated institutional transaction ingress via Mambu Webhook (HMAC-SHA256 verified).
2. Normalization to standard transaction schema and categorical/numerical feature mapping.
3. Authorized champion model inference and durable prediction evidence in ModelEvaluationEngine.
4. Fraud risk evaluation, composite scoring, and AlertIntelligenceService triage.
5. Analyst-managed Case creation linking the generated alert and preserving bank tenant binding.
6. Four-Eyes supervisor governance and dual-signoff case closure.
7. Retraining feedback loop recording ground-truth labels bound to genuine prediction evidence.
8. Unauthorized cross-tenant isolation enforcement.
9. Fail-closed behavior on missing champion models or malformed inputs.
10. Idempotency preservation on duplicate event delivery.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.application.services.alert_service import AlertIntelligenceService
from app.application.services.label_feedback_pipeline import LocalLabelFeedbackPipeline
from app.application.services.model_registry import ModelEvaluationEngine, ModelRegistry
from app.domain.enums import CasePriority, CaseStatus
from app.main import app
from app.presentation.routers.cases import get_case_service
from app.presentation.routers.core_banking_gateway import get_mambu_connector

client = TestClient(app)


def _compute_mambu_hmac(secret: str | None, raw_bytes: bytes) -> str:
    """Compute Mambu HMAC-SHA256 signature header value."""
    key = (secret or "").encode("utf-8")
    digest = hmac.new(key, raw_bytes, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture
def clean_stores():
    """Ensure clean in-memory / local state for evaluation, alert, and case stores."""
    case_svc = get_case_service()
    case_svc._cases.clear()

    alert_svc = AlertIntelligenceService()
    alert_svc._alert_store.clear()

    feedback_pipeline = LocalLabelFeedbackPipeline()
    feedback_pipeline.clear_buffer("bank_alpha")
    feedback_pipeline.clear_buffer("bank_beta")

    yield

    case_svc._cases.clear()
    alert_svc._alert_store.clear()
    feedback_pipeline.clear_buffer("bank_alpha")
    feedback_pipeline.clear_buffer("bank_beta")


class TestTransactionToInvestigationWorkflow:
    """Integration test suite verifying the continuous transaction-to-investigation flow."""

    def test_end_to_end_transaction_to_investigation_workflow(self, clean_stores) -> None:
        """Verify the complete pilot journey from Mambu webhook to case closure and feedback."""
        connector = get_mambu_connector()
        tx_id = f"TX_PILOT_{uuid.uuid4().hex[:12].upper()}"
        acc_id = f"ACC_PILOT_{uuid.uuid4().hex[:8].upper()}"

        # 1. Authenticated institutional transaction ingress via Mambu Webhook
        payload = {
            "type": "deposit-transaction.created",
            "transactionId": tx_id,
            "accountId": acc_id,
            "counterpartyAccountId": "ACC_BENEFICIARY_99",
            "amount": 14500.0,
            "currencyCode": "EUR",
            "channel": "ONLINE",
            "mcc": "6051",  # Crypto merchant category code -> high risk
            "bankId": "bank_alpha",
            "originCountry": "NG",
            "velocity": 25.0,
            "customer_history_score": 0.10,
            "chargeback_count": 4,
            "account_age_days": 10,
        }
        raw_body = json.dumps(payload).encode("utf-8")
        sig_header = _compute_mambu_hmac(connector.webhook_secret, raw_body)

        ingress_resp = client.post(
            "/connectors/core-banking/mambu/webhook",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Mambu-Signature": sig_header,
            },
        )
        assert ingress_resp.status_code == 200
        ingress_data = ingress_resp.json()
        assert ingress_data["status"] == "ACCEPTED"
        assert ingress_data["event_type"] == "TRANSACTION"
        assert ingress_data["transaction_id"] == tx_id
        assert ingress_data["account_id"] == acc_id
        assert ingress_data["amount"] == 14500.0

        # 2. Verify risk score, triage decision, and alert ID
        assert ingress_data["risk_score"] is not None
        assert ingress_data["risk_score"] >= 600.0
        assert ingress_data["decision"] in ("HOLD", "FLAG")
        alert_id = ingress_data["alert_id"]
        assert alert_id is not None
        assert len(alert_id) > 0

        # 3. Verify prediction evidence in ModelEvaluationEngine
        registry = ModelRegistry()
        eval_engine = ModelEvaluationEngine(registry)
        pred_record = eval_engine.get_prediction_record("live_prod_v2", tx_id, bank_id="bank_alpha")
        assert pred_record is not None
        assert pred_record["routed_to"] == "champion"
        assert pred_record["champion_prob"] is not None
        assert 0.0 <= pred_record["champion_prob"] <= 1.0
        assert pred_record["bank_id"] == "bank_alpha"
        assert pred_record["actual_label"] is None  # Not yet labeled

        # 4. Verify alert generation and institutional ownership in AlertIntelligenceService
        alert_svc = AlertIntelligenceService()
        alert = alert_svc.get_alert(alert_id)
        assert alert is not None
        assert alert.id == alert_id
        assert alert.transaction_id == tx_id
        assert alert.bank_id == "bank_alpha"
        assert alert.risk_score >= 600.0

        # 5. Analyst-managed Case creation linking the generated alert
        case_resp = client.post(
            "/api/v1/cases",
            json={
                "title": f"[BANK ALPHA] High Risk Crypto Alert - Tx {tx_id}",
                "priority": "p1_critical",
                "alert_ids": [alert_id],
                "total_risk_score": alert.risk_score,
                "assigned_to": "investigator_alice",
                "bank_id": "bank_alpha",
            },
            headers={"X-Bank-ID": "bank_alpha"},
        )
        assert case_resp.status_code == 200
        case_data = case_resp.json()
        case_id = case_data["id"]
        assert case_data["status"] == "open"
        assert case_data["bank_id"] == "bank_alpha"
        assert alert_id in case_data["alert_ids"]

        # 6. Lifecycle progression: Assign and Investigate
        inv_resp = client.patch(
            f"/api/v1/cases/{case_id}",
            json={"status": "investigating", "actor": "investigator_alice"},
            headers={"X-Bank-ID": "bank_alpha"},
        )
        assert inv_resp.status_code == 200
        assert inv_resp.json()["status"] == "investigating"

        # 7. Four-Eyes supervisor dual signoff and Case resolution
        resolve_resp = client.post(
            f"/api/v1/cases/{case_id}/resolve",
            json={
                "resolution": "CONFIRMED_FRAUD",
                "primary_supervisor": "supervisor:carol",
                "secondary_supervisor": "supervisor:david",
                "actor": "analyst_bob",
            },
            headers={"X-Bank-ID": "bank_alpha"},
        )
        assert resolve_resp.status_code == 200
        resolved_case = resolve_resp.json()
        assert resolved_case["status"] == "closed_confirmed"
        assert resolved_case["closed_at"] is not None

        # 8. Verify ground-truth feedback recorded in ModelEvaluationEngine
        pred_record_after = eval_engine.get_prediction_record("live_prod_v2", tx_id, bank_id="bank_alpha")
        assert pred_record_after is not None
        assert pred_record_after["actual_label"] == 1  # CONFIRMED_FRAUD

        # 9. Verify feedback pipeline buffer has ingested analyst determination
        feedback_pipeline = LocalLabelFeedbackPipeline()
        buffer_items = feedback_pipeline.get_feedback_buffer("bank_alpha")
        assert len(buffer_items) >= 1
        assert any(item["alert_id"] == alert_id and item["determination"] == "CONFIRMED_FRAUD" for item in buffer_items)

    def test_unauthorized_cross_tenant_access_rejection(self, clean_stores) -> None:
        """Verify that an institution cannot access or close a case belonging to another bank."""
        # Create case for bank_alpha
        case_svc = get_case_service()
        case = case_svc.create_case(
            title="Alpha Bank Internal Fraud",
            priority=CasePriority.P2_HIGH,
            alert_ids=["alt_alpha_101"],
            bank_id="bank_alpha",
            assigned_to="alice",
        )

        # Attempt to access or mutate case with X-Bank-ID: bank_beta -> 403 Forbidden
        status_resp = client.patch(
            f"/api/v1/cases/{case.id}",
            json={"status": "investigating", "actor": "intruder"},
            headers={"X-Bank-ID": "bank_beta"},
        )
        assert status_resp.status_code == 403
        assert "is not authorized to access resources belonging to" in status_resp.json()["detail"]

    def test_missing_champion_model_fails_closed(self, clean_stores) -> None:
        """Verify that missing or unresolvable champion model fails closed with HTTP 503."""
        connector = get_mambu_connector()
        payload = {
            "type": "deposit-transaction.created",
            "transactionId": "TX_FAIL_CLOSED_01",
            "accountId": "ACC_FAIL_CLOSED_01",
            "amount": 1000.0,
            "currencyCode": "EUR",
        }
        raw_body = json.dumps(payload).encode("utf-8")
        sig_header = _compute_mambu_hmac(connector.webhook_secret, raw_body)

        # Mock ModelRegistry to return no champion identity
        with patch("app.presentation.routers.predict._registry.get_active_champion_identity", return_value=None):
            with patch("app.presentation.routers.predict._serving_model_cache", {}):
                resp = client.post(
                    "/connectors/core-banking/mambu/webhook",
                    content=raw_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Mambu-Signature": sig_header,
                    },
                )
                assert resp.status_code == 503
                assert "No verified active champion model artifact" in resp.json()["detail"]

    def test_malformed_transaction_fails_closed(self, clean_stores) -> None:
        """Verify that malformed or negative transaction amounts are rejected with HTTP 422."""
        connector = get_mambu_connector()
        payload = {
            "type": "deposit-transaction.created",
            "transactionId": "TX_MALFORMED_01",
            "accountId": "ACC_MALFORMED_01",
            "amount": -500.0,  # Invalid negative amount
            "currencyCode": "EUR",
        }
        raw_body = json.dumps(payload).encode("utf-8")
        sig_header = _compute_mambu_hmac(connector.webhook_secret, raw_body)

        resp = client.post(
            "/connectors/core-banking/mambu/webhook",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Mambu-Signature": sig_header,
            },
        )
        assert resp.status_code == 422
        assert "must be positive and finite" in resp.json()["detail"]

    def test_invalid_webhook_signature_rejection(self, clean_stores) -> None:
        """Verify that invalid HMAC signatures on Mambu webhooks are rejected with HTTP 401."""
        payload = {
            "type": "deposit-transaction.created",
            "transactionId": "TX_UNAUTHORIZED_01",
            "accountId": "ACC_UNAUTHORIZED_01",
            "amount": 250.0,
            "currencyCode": "EUR",
        }
        raw_body = json.dumps(payload).encode("utf-8")

        # Missing signature
        resp_missing = client.post(
            "/connectors/core-banking/mambu/webhook",
            content=raw_body,
            headers={"Content-Type": "application/json"},
        )
        assert resp_missing.status_code == 401
        assert "Missing required X-Mambu-Signature" in resp_missing.json()["detail"]

        # Forged signature
        resp_bad = client.post(
            "/connectors/core-banking/mambu/webhook",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Mambu-Signature": "sha256=badbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbad",
            },
        )
        assert resp_bad.status_code == 401
        assert "Invalid Mambu HMAC-SHA256" in resp_bad.json()["detail"]

    def test_case_creation_idempotency(self, clean_stores) -> None:
        """Verify that duplicate case creation with identical Idempotency-Key returns cached response."""
        idem_key = f"IDEM_{uuid.uuid4().hex}"
        case_req = {
            "title": "Idempotency Protection Test",
            "priority": "p2_high",
            "alert_ids": ["alt_idem_1"],
            "total_risk_score": 750.0,
            "bank_id": "bank_alpha",
        }

        # First dispatch
        resp1 = client.post(
            "/api/v1/cases",
            json=case_req,
            headers={"Idempotency-Key": idem_key, "X-Bank-ID": "bank_alpha"},
        )
        assert resp1.status_code == 200
        case1_id = resp1.json()["id"]

        # Duplicate replay
        resp2 = client.post(
            "/api/v1/cases",
            json=case_req,
            headers={"Idempotency-Key": idem_key, "X-Bank-ID": "bank_alpha"},
        )
        assert resp2.status_code == 200
        assert resp2.headers.get("idempotency-replayed") == "true"
        assert resp2.json()["id"] == case1_id

    def test_signed_webhook_mismatching_tenant_claim_rejection(self, clean_stores) -> None:
        """Verify that a validly signed webhook with mismatching bankId payload claim is rejected (HTTP 403)."""
        connector = get_mambu_connector()
        payload = {
            "type": "deposit-transaction.created",
            "transactionId": f"TX_MISMATCH_{uuid.uuid4().hex[:8]}",
            "accountId": "ACC_ALPHA_01",
            "amount": 500.0,
            "currencyCode": "EUR",
            "bankId": "bank_beta",  # Mismatching tenant claim
        }
        raw_body = json.dumps(payload).encode("utf-8")
        sig_header = _compute_mambu_hmac(connector.webhook_secret, raw_body)

        resp = client.post(
            "/connectors/core-banking/mambu/webhook",
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Mambu-Signature": sig_header,
            },
        )
        assert resp.status_code == 403
        assert "does not match connector institutional tenant" in resp.json()["detail"]

    def test_missing_or_default_production_webhook_secret_fails_closed(self, clean_stores) -> None:
        """Verify that repository default signing secrets in production mode fail closed with HTTP 401."""
        connector = get_mambu_connector()
        payload = {
            "type": "deposit-transaction.created",
            "transactionId": f"TX_PROD_SEC_{uuid.uuid4().hex[:8]}",
            "accountId": "ACC_ALPHA_01",
            "amount": 250.0,
            "currencyCode": "EUR",
            "bankId": "bank_alpha",
        }
        raw_body = json.dumps(payload).encode("utf-8")
        sig_header = _compute_mambu_hmac(connector.webhook_secret, raw_body)

        from app.config import get_settings

        settings = get_settings()
        orig_env = settings.app_env
        try:
            settings.app_env = "production"
            resp = client.post(
                "/connectors/core-banking/mambu/webhook",
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Mambu-Signature": sig_header,
                },
            )
            assert resp.status_code == 401
            assert "repository-default" in resp.json()["detail"].lower()
        finally:
            settings.app_env = orig_env

    def test_revoked_consortium_institutional_serving_rejection(self, clean_stores) -> None:
        """Verify that institutional tenant revoked from an active consortium is rejected (HTTP 403)."""
        connector = get_mambu_connector()
        payload = {
            "type": "deposit-transaction.created",
            "transactionId": f"TX_REVOKED_{uuid.uuid4().hex[:8]}",
            "accountId": "ACC_ALPHA_REVOKED",
            "amount": 3000.0,
            "currencyCode": "EUR",
            "bankId": "bank_alpha",
        }
        raw_body = json.dumps(payload).encode("utf-8")
        sig_header = _compute_mambu_hmac(connector.webhook_secret, raw_body)

        from app.domain.consortium_governance import (
            Consortium,
            ConsortiumMember,
            ConsortiumStatus,
            MemberRole,
        )
        from app.infrastructure.storage.durable_consortium_store import DurableConsortiumStore

        store = DurableConsortiumStore.get_instance()
        test_c_id = f"consortium_revoked_test_{uuid.uuid4().hex[:6]}"
        revoked_consortium = Consortium(
            consortium_id=test_c_id,
            name="Revocation Test Alliance",
            status=ConsortiumStatus.ACTIVE,
            members={
                "bank_beta": ConsortiumMember(bank_id="bank_beta", role=MemberRole.FOUNDER, voting_power=1.0),
            },
            removed_members={"bank_alpha": {"reason": "Revoked for regulatory breach"}},
        )
        with store._lock:
            store._consortia[test_c_id] = revoked_consortium

        try:
            resp = client.post(
                "/connectors/core-banking/mambu/webhook",
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Mambu-Signature": sig_header,
                },
            )
            assert resp.status_code == 403
            assert "has been revoked" in resp.json()["detail"]
        finally:
            with store._lock:
                store._consortia.pop(test_c_id, None)

    def test_production_feature_validation_without_store_fails_closed(self, clean_stores) -> None:
        """Verify that in production mode, webhooks missing mandatory ML features cannot bypass validation (HTTP 422)."""
        connector = get_mambu_connector()
        orig_tenant = connector.tenant_id
        orig_secret = connector.webhook_secret
        connector_secret = "mambu_secure_production_secret_key_2026_xyz"
        connector.tenant_id = "bank_a"
        connector.webhook_secret = connector_secret
        payload = {
            "type": "deposit-transaction.created",
            "transactionId": f"TX_PROD_MISSING_{uuid.uuid4().hex[:8]}",
            "accountId": "ACC_PROD_01",
            "amount": 1000.0,
            "currencyCode": "EUR",
            "bankId": "bank_a",
            "mcc": "5411",
        }
        raw_body = json.dumps(payload).encode("utf-8")
        sig_header = _compute_mambu_hmac(connector_secret, raw_body)

        from app.config import get_settings

        settings = get_settings()
        orig_env = settings.app_env
        orig_fs = settings.feature_store_enabled
        def mock_champ(simulation_id=None):
            if simulation_id == "live_prod_v2":
                return {"version": 1, "generation": 1, "simulation_id": "live_prod_v2"}
            return None

        try:
            settings.app_env = "production"
            settings.feature_store_enabled = False
            with patch(
                "app.application.services.model_registry.ModelRegistry.get_active_champion_identity",
                side_effect=mock_champ,
            ):
                resp = client.post(
                    "/connectors/core-banking/mambu/webhook",
                    content=raw_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Mambu-Signature": sig_header,
                    },
                )
            assert resp.status_code == 422
            detail = resp.json()["detail"]
            assert "Production feature contract violation" in detail
            assert "Missing required features" in detail
        finally:
            settings.app_env = orig_env
            settings.feature_store_enabled = orig_fs
            connector.webhook_secret = orig_secret
            connector.tenant_id = orig_tenant

    def test_alert_persistence_failure_returns_retryable_error(self, clean_stores) -> None:
        """Verify that when alert persistence fails on a high-risk transaction, gateway returns HTTP 503 instead of 200."""
        connector = get_mambu_connector()
        tx_id = f"TX_ALERT_FAIL_{uuid.uuid4().hex[:8]}"
        payload = {
            "type": "deposit-transaction.created",
            "transactionId": tx_id,
            "accountId": "ACC_HIGH_RISK_01",
            "amount": 18000.0,
            "currencyCode": "EUR",
            "channel": "ONLINE",
            "mcc": "6051",
            "bankId": "bank_alpha",
            "originCountry": "NG",
            "velocity": 35.0,
            "customer_history_score": 0.05,
            "chargeback_count": 5,
            "account_age_days": 5,
        }
        raw_body = json.dumps(payload).encode("utf-8")
        sig_header = _compute_mambu_hmac(connector.webhook_secret, raw_body)

        with patch.object(AlertIntelligenceService, "generate_alerts", return_value=[]):
            resp = client.post(
                "/connectors/core-banking/mambu/webhook",
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Mambu-Signature": sig_header,
                },
            )
            assert resp.status_code == 503
            assert "Alert persistence failed" in resp.json()["detail"]

    def test_case_feedback_genuine_correlation_and_cross_tenant_rejection(self, clean_stores) -> None:
        """Verify that case feedback uses genuine alert evidence and rejects cross-tenant alert correlation."""
        alert_svc = AlertIntelligenceService()
        case_svc = get_case_service()
        eval_engine = ModelEvaluationEngine(ModelRegistry())

        # 1. Genuine Alert Creation for bank_alpha
        tx_alpha = f"TX_GENUINE_{uuid.uuid4().hex[:8]}"
        alerts_a = alert_svc.generate_alerts(
            bank_id="bank_alpha",
            transactions=[{"transaction_id": tx_alpha, "customer_id": "CUST_GENUINE_01", "amount": 5000.0}],
            predictions=[0.85],
        )
        alert_alpha = alerts_a[0]

        eval_engine.log_prediction(
            simulation_id="live_prod_v2",
            transaction_id=tx_alpha,
            champion_version=1,
            champion_prob=0.85,
            routed_to="champion",
            bank_id="bank_alpha",
        )

        # 2. Case for bank_alpha with genuine alert
        case_alpha = case_svc.create_case(
            title="Genuine Alpha Case",
            priority=CasePriority.P1_CRITICAL,
            alert_ids=[alert_alpha.id],
            bank_id="bank_alpha",
            assigned_to="alice",
        )
        case_svc.change_status(case_alpha.id, CaseStatus.INVESTIGATING, actor="alice")
        closed_alpha = case_svc.change_status(
            case_alpha.id,
            CaseStatus.CLOSED_CONFIRMED,
            actor="alice",
            supervisor_signature="supervisor_bob",
        )
        last_event = closed_alpha.timeline[-1]
        assert last_event.metadata.get("prediction_evidence_recorded") is True
        pred_rec = eval_engine.get_prediction_record("live_prod_v2", tx_alpha, bank_id="bank_alpha")
        assert pred_rec is not None
        assert pred_rec["actual_label"] == 1

        # 3. Missing alert: Cannot correlate ground-truth prediction evidence
        case_missing = case_svc.create_case(
            title="Missing Alert Case",
            priority=CasePriority.P2_HIGH,
            alert_ids=["non_existent_alert_12345"],
            bank_id="bank_alpha",
            assigned_to="alice",
        )
        case_svc.change_status(case_missing.id, CaseStatus.INVESTIGATING, actor="alice")
        closed_missing = case_svc.change_status(
            case_missing.id,
            CaseStatus.CLOSED_FALSE_POSITIVE,
            actor="alice",
            supervisor_signature="supervisor_bob",
        )
        assert closed_missing.timeline[-1].metadata.get("prediction_evidence_recorded") is False

        # 4. Cross-tenant alert: Case bank_alpha linking bank_beta alert raises PermissionError
        alerts_b = alert_svc.generate_alerts(
            bank_id="bank_beta",
            transactions=[{"transaction_id": f"TX_BETA_{uuid.uuid4().hex[:8]}", "customer_id": "CUST_BETA_01", "amount": 9000.0}],
            predictions=[0.90],
        )
        alert_beta = alerts_b[0]
        case_cross = case_svc.create_case(
            title="Cross-Tenant Case",
            priority=CasePriority.P1_CRITICAL,
            alert_ids=[alert_beta.id],
            bank_id="bank_alpha",  # Bank mismatch!
            assigned_to="alice",
        )
        case_svc.change_status(case_cross.id, CaseStatus.INVESTIGATING, actor="alice")
        with pytest.raises(PermissionError) as exc_info:
            case_svc.change_status(
                case_cross.id,
                CaseStatus.CLOSED_CONFIRMED,
                actor="alice",
                supervisor_signature="supervisor_bob",
            )
        assert "Cross-tenant alert feedback rejected" in str(exc_info.value)
