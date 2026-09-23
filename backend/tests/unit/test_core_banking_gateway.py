"""Unit and Integration Tests for Cloud Core Banking Connectors and Gateway.

Tests Mambu v2 REST/webhook ingestion, Thought Machine Vault Core posting instruction
batch streaming, HMAC-SHA256 signature verification, Zero-Raw-PII customer pseudonymization,
outbound provisional account hold dispatching, and REST gateway endpoints.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.connectors.factory import (
    APPROVED_PRODUCTION_CONNECTORS,
    CONNECTOR_REGISTRY,
)
from app.infrastructure.connectors.mambu_connector import (
    MambuConnector,
    MambuWebhookSignatureError,
)
from app.infrastructure.connectors.thought_machine_connector import (
    ThoughtMachineConnector,
    ThoughtMachineSignatureError,
)
from app.main import app

client = TestClient(app)


# ── Fixtures & Helpers ────────────────────────────────────────────────────────


@pytest.fixture
def mambu_connector() -> MambuConnector:
    return MambuConnector(
        webhook_secret="test_mambu_secret_key_2026",
        base_url="https://api.mambu.test",
    )


@pytest.fixture
def thought_machine_connector() -> ThoughtMachineConnector:
    return ThoughtMachineConnector(
        webhook_secret="test_vault_core_secret_key_2026",
        base_url="https://vault-core.test:8080",
    )


def _compute_hmac(secret: str, payload_bytes: bytes) -> str:
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


# ── 1. Mambu Connector Ingestion & Security Tests ─────────────────────────────


class TestMambuConnector:
    """Tests Mambu Cloud Core Banking Connector functionality."""

    def test_mambu_transaction_event_normalization(self, mambu_connector: MambuConnector) -> None:
        payload = {
            "type": "deposit-transaction.created",
            "transactionId": "TX_MAMBU_98231",
            "accountId": "ACC_DE_881920",
            "counterpartyAccountId": "ACC_FR_119283",
            "amount": 4250.75,
            "currencyCode": "EUR",
            "channel": "ONLINE",
            "mcc": "6012",
        }
        normalized = mambu_connector.parse_webhook_event(payload)
        assert normalized.transaction_id == "TX_MAMBU_98231"
        assert normalized.account_id == "ACC_DE_881920"
        assert normalized.counterparty_account_id == "ACC_FR_119283"
        assert normalized.amount == 4250.75
        assert normalized.currency == "EUR"
        assert normalized.channel_type == "ONLINE"

    def test_mambu_client_event_pii_pseudonymization(self, mambu_connector: MambuConnector) -> None:
        payload = {
            "type": "client.created",
            "clientKey": "CLIENT_99210",
            "firstName": "John",
            "lastName": "Doe",
            "clientTier": "ENTERPRISE",
        }
        result = mambu_connector.parse_webhook_event(payload)
        assert isinstance(result, dict)
        assert result["event_type"] == "CLIENT_PSEUDONYMIZED"
        assert result["provider"] == "MAMBU"
        assert "mambu_anon_client_key_" in result["client_pseudonym"]
        assert "John" not in str(result)
        assert "Doe" not in str(result)
        assert result["tier"] == "ENTERPRISE"

    def test_mambu_account_hold_event(self, mambu_connector: MambuConnector) -> None:
        payload = {
            "type": "account.hold",
            "accountId": "ACC_HOLD_5541",
            "amount": 10000.0,
            "reason": "EPC_SCT_INST_CAMT056_RECALL",
            "blockId": "BLK_99182",
        }
        result = mambu_connector.parse_webhook_event(payload)
        assert isinstance(result, dict)
        assert result["event_type"] == "ACCOUNT_HOLD_NOTIFICATION"
        assert result["account_id"] == "ACC_HOLD_5541"
        assert result["amount"] == 10000.0
        assert result["hold_id"] == "BLK_99182"

    def test_mambu_webhook_signature_verification_success(self, mambu_connector: MambuConnector) -> None:
        payload = {"type": "deposit-transaction.created", "amount": 150.0}
        raw_body = json.dumps(payload).encode()
        sig = _compute_hmac(mambu_connector.webhook_secret, raw_body)

        norm = mambu_connector.parse_webhook_event(
            payload=payload,
            signature_header=f"sha256={sig}",
            raw_body=raw_body,
        )
        assert norm.amount == 150.0

    def test_mambu_webhook_signature_verification_failure(self, mambu_connector: MambuConnector) -> None:
        payload = {"type": "deposit-transaction.created", "amount": 999.0}
        raw_body = json.dumps(payload).encode()
        bad_sig = "0" * 64

        with pytest.raises(MambuWebhookSignatureError, match="Invalid Mambu HMAC-SHA256"):
            mambu_connector.parse_webhook_event(
                payload=payload,
                signature_header=bad_sig,
                raw_body=raw_body,
            )

    @pytest.mark.asyncio
    async def test_mambu_provisional_hold_dispatch(self, mambu_connector: MambuConnector) -> None:
        record = await mambu_connector.apply_provisional_hold(
            account_id="ACC_TARGET_1234",
            amount=8500.0,
            reason="High velocity smurfing suspicion",
            reference_id="TICKET_FININT_4401",
        )
        assert record["status"] == "HOLD_APPLIED"
        assert record["provider"] == "MAMBU"
        assert record["account_id"] == "ACC_TARGET_1234"
        assert record["amount"] == 8500.0
        assert record["reference_id"] == "TICKET_FININT_4401"
        assert len(record["audit_hash"]) == 64
        assert record["external_status"] == "SIMULATED_LOOPBACK"

    @pytest.mark.asyncio
    async def test_mambu_hold_idempotency_deduplication(self, mambu_connector: MambuConnector) -> None:
        token = f"idemp_{uuid.uuid4().hex}"
        r1 = await mambu_connector.apply_provisional_hold(
            account_id="ACC_IDEMP_1",
            amount=500.0,
            reason="Test deduplication",
            idempotency_token=token,
        )
        assert r1.get("deduplicated") is not True

        r2 = await mambu_connector.apply_provisional_hold(
            account_id="ACC_IDEMP_1",
            amount=500.0,
            reason="Test deduplication",
            idempotency_token=token,
        )
        assert r2.get("deduplicated") is True
        assert r2["hold_id"] == r1["hold_id"]

    def test_mambu_streaming_and_batch(self, mambu_connector: MambuConnector) -> None:
        batch_payload = [
            {"type": "deposit-transaction.created", "transactionId": "TX1", "amount": 100.0},
            {"type": "deposit-transaction.created", "transactionId": "TX2", "amount": 200.0},
        ]
        parsed = mambu_connector.parse_batch(batch_payload)
        assert len(parsed) == 2
        assert parsed[0].transaction_id == "TX1"
        assert parsed[1].transaction_id == "TX2"

        stream_items = list(mambu_connector.consume_stream())
        assert len(stream_items) == 2

    def test_mambu_connector_health(self, mambu_connector: MambuConnector) -> None:
        health = mambu_connector.health_check()
        assert health["connector"] == "MambuConnector"
        assert health["status"] == "HEALTHY"
        assert health["circuit_breaker"] == "CLOSED"


# ── 2. Thought Machine Connector Ingestion & Security Tests ───────────────────


class TestThoughtMachineConnector:
    """Tests Thought Machine Vault Core Banking Connector functionality."""

    def test_thought_machine_pib_normalization(
        self,
        thought_machine_connector: ThoughtMachineConnector,
    ) -> None:
        payload = {
            "posting_instruction_batch": {
                "id": "PIB_VAULT_00992",
                "client_batch_id": "BATCH_EXTERNAL_12",
                "posting_instructions": [
                    {
                        "id": "INST_01",
                        "custom_instruction": {
                            "postings": [
                                {"account_id": "ACC_DEBTOR_01", "amount": "1500.00", "denomination": "GBP", "credit": False},
                                {"account_id": "ACC_CREDITOR_01", "amount": "1500.00", "denomination": "GBP", "credit": True},
                            ],
                            "mcc": "6011",
                        },
                    },
                ],
            }
        }
        txs = thought_machine_connector.parse_webhook_event(payload)
        assert len(txs) == 1
        tx = txs[0]
        assert tx.transaction_id == "INST_01"
        assert tx.account_id == "ACC_DEBTOR_01"
        assert tx.counterparty_account_id == "ACC_CREDITOR_01"
        assert tx.amount == 1500.0
        assert tx.currency == "GBP"

    def test_thought_machine_signature_verification_success(
        self,
        thought_machine_connector: ThoughtMachineConnector,
    ) -> None:
        payload = {"posting_instruction_batch": {"id": "PIB_OK", "posting_instructions": []}}
        raw_body = json.dumps(payload).encode()
        sig = _compute_hmac(thought_machine_connector.webhook_secret, raw_body)

        txs = thought_machine_connector.parse_webhook_event(
            payload=payload,
            signature_header=sig,
            raw_body=raw_body,
        )
        assert isinstance(txs, list)

    def test_thought_machine_signature_verification_failure(
        self,
        thought_machine_connector: ThoughtMachineConnector,
    ) -> None:
        payload = {"posting_instruction_batch": {"id": "PIB_BAD"}}
        raw_body = json.dumps(payload).encode()

        with pytest.raises(ThoughtMachineSignatureError, match="Invalid Thought Machine HMAC-SHA256"):
            thought_machine_connector.parse_webhook_event(
                payload=payload,
                signature_header="bad_signature_hex",
                raw_body=raw_body,
            )

    @pytest.mark.asyncio
    async def test_thought_machine_provisional_restriction_dispatch(
        self,
        thought_machine_connector: ThoughtMachineConnector,
    ) -> None:
        record = await thought_machine_connector.apply_provisional_hold(
            account_id="ACC_VAULT_DEBTOR_88",
            amount=12000.0,
            reason="Unusual mule account burst activity",
            reference_id="ALERT_ML_0019",
        )
        assert record["status"] == "RESTRICTION_COMMITTED"
        assert record["provider"] == "THOUGHT_MACHINE"
        assert record["account_id"] == "ACC_VAULT_DEBTOR_88"
        assert record["amount"] == 12000.0
        assert len(record["audit_hash"]) == 64

    @pytest.mark.asyncio
    async def test_thought_machine_restriction_idempotency(
        self,
        thought_machine_connector: ThoughtMachineConnector,
    ) -> None:
        token = f"idemp_tm_{uuid.uuid4().hex}"
        r1 = await thought_machine_connector.apply_provisional_hold(
            account_id="ACC_TM_DEDUP",
            amount=300.0,
            reason="Deduplication test",
            idempotency_token=token,
        )
        assert r1.get("deduplicated") is not True

        r2 = await thought_machine_connector.apply_provisional_hold(
            account_id="ACC_TM_DEDUP",
            amount=300.0,
            reason="Deduplication test",
            idempotency_token=token,
        )
        assert r2.get("deduplicated") is True
        assert r2["hold_id"] == r1["hold_id"]

    def test_thought_machine_health(
        self,
        thought_machine_connector: ThoughtMachineConnector,
    ) -> None:
        health = thought_machine_connector.health_check()
        assert health["connector"] == "ThoughtMachineConnector"
        assert health["status"] == "HEALTHY"


# ── 3. Core Banking Gateway REST Router & Dual Prefix Tests ───────────────────


class TestCoreBankingGatewayRouter:
    """Tests API Gateway routes for core banking integration."""

    def test_mambu_webhook_endpoint_success(self) -> None:
        payload = {
            "type": "deposit-transaction.created",
            "transactionId": "TX_API_MAMBU_01",
            "accountId": "ACC_API_01",
            "amount": 2500.0,
            "currencyCode": "EUR",
        }
        res = client.post("/connectors/core-banking/mambu/webhook", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ACCEPTED"
        assert data["transaction_id"] == "TX_API_MAMBU_01"
        assert data["amount"] == 2500.0

    def test_mambu_webhook_v1_endpoint_dual_prefix(self) -> None:
        payload = {
            "type": "client.created",
            "clientKey": "CLIENT_V1_KEY",
            "firstName": "Alice",
            "lastName": "Smith",
        }
        res = client.post("/api/v1/connectors/core-banking/mambu/webhook", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ACCEPTED"
        assert data["event_type"] == "CLIENT_PSEUDONYMIZED"
        assert "Alice" not in str(data)

    def test_mambu_webhook_invalid_signature_401(self) -> None:
        payload = {"type": "deposit-transaction.created", "amount": 10.0}
        res = client.post(
            "/connectors/core-banking/mambu/webhook",
            json=payload,
            headers={"X-Mambu-Signature": "invalid_signature_header_hex"},
        )
        assert res.status_code == 401
        assert "Invalid Mambu HMAC-SHA256" in res.json()["detail"]

    def test_thought_machine_webhook_endpoint_success(self) -> None:
        payload = {
            "posting_instruction_batch": {
                "id": "PIB_API_001",
                "posting_instructions": [
                    {
                        "id": "INST_API_1",
                        "custom_instruction": {
                            "postings": [
                                {"account_id": "ACC_DEB", "amount": "950.0", "denomination": "EUR", "credit": False},
                                {"account_id": "ACC_CRED", "amount": "950.0", "denomination": "EUR", "credit": True},
                            ]
                        },
                    }
                ],
            }
        }
        res = client.post("/connectors/core-banking/thought-machine/webhook", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ACCEPTED"
        assert data["batch_id"] == "PIB_API_001"
        assert data["transactions_count"] == 1
        assert "INST_API_1" in data["transaction_ids"]

    def test_thought_machine_webhook_v1_dual_prefix(self) -> None:
        payload = {"posting_instruction_batch": {"id": "PIB_V1", "posting_instructions": []}}
        res = client.post("/api/v1/connectors/core-banking/thought-machine/webhook", json=payload)
        assert res.status_code == 200
        assert res.json()["status"] == "ACCEPTED"

    def test_provisional_hold_endpoint_mambu_success(self) -> None:
        hold_req = {
            "account_id": "ACC_RESTRICT_MAMBU",
            "amount": 7500.0,
            "reason": "Layering pattern detected across 3 consortium banks",
            "provider": "mambu",
            "reference_ticket_id": "CASE_9901",
        }
        res = client.post("/connectors/core-banking/holds/provisional", json=hold_req)
        assert res.status_code == 201
        data = res.json()
        assert data["status"] == "HOLD_APPLIED"
        assert data["provider"] == "MAMBU"
        assert data["account_id"] == "ACC_RESTRICT_MAMBU"
        assert data["amount"] == 7500.0
        assert len(data["audit_hash"]) == 64

    def test_provisional_hold_endpoint_thought_machine_success(self) -> None:
        hold_req = {
            "account_id": "ACC_RESTRICT_TM",
            "amount": 14200.0,
            "reason": "Rapid mule outbound dispersion",
            "provider": "thought_machine",
            "reference_ticket_id": "CASE_9902",
        }
        res = client.post("/api/v1/connectors/core-banking/holds/provisional", json=hold_req)
        assert res.status_code == 201
        data = res.json()
        assert data["status"] == "RESTRICTION_COMMITTED"
        assert data["provider"] == "THOUGHT_MACHINE"
        assert data["account_id"] == "ACC_RESTRICT_TM"
        assert data["amount"] == 14200.0

    def test_provisional_hold_unsupported_provider_400(self) -> None:
        hold_req = {
            "account_id": "ACC_ERR",
            "amount": 100.0,
            "reason": "Test unsupported",
            "provider": "unsupported_core_bank",
        }
        res = client.post("/connectors/core-banking/holds/provisional", json=hold_req)
        assert res.status_code == 400
        assert "Unsupported core banking provider" in res.json()["detail"]

    def test_provisional_hold_validation_error_422(self) -> None:
        hold_req = {
            "account_id": "ACC_ERR",
            "amount": -50.0,  # Invalid negative amount
            "reason": "Invalid",
            "provider": "mambu",
        }
        res = client.post("/connectors/core-banking/holds/provisional", json=hold_req)
        assert res.status_code == 422

    def test_core_banking_health_endpoints(self) -> None:
        res1 = client.get("/connectors/core-banking/health")
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["status"] == "HEALTHY"
        assert data1["mambu"]["connector"] == "MambuConnector"
        assert data1["thought_machine"]["connector"] == "ThoughtMachineConnector"

        res2 = client.get("/api/v1/connectors/core-banking/health")
        assert res2.status_code == 200
        assert res2.json()["status"] == "HEALTHY"


# ── 4. Factory Integration Tests ──────────────────────────────────────────────


class TestBankConnectorFactoryCoreBanking:
    """Verifies BankConnectorFactory registers Mambu and Thought Machine."""

    def test_factory_registry_contains_core_banking(self) -> None:
        assert "mambu" in CONNECTOR_REGISTRY
        assert "thought_machine" in CONNECTOR_REGISTRY
        assert "thoughtmachine" in CONNECTOR_REGISTRY
        assert CONNECTOR_REGISTRY["mambu"] == MambuConnector
        assert CONNECTOR_REGISTRY["thought_machine"] == ThoughtMachineConnector

    def test_factory_approved_connectors_contain_core_banking(self) -> None:
        assert "mambu" in APPROVED_PRODUCTION_CONNECTORS
        assert "thought_machine" in APPROVED_PRODUCTION_CONNECTORS
