"""Unit tests for the Inter-Bank Encrypted FININT Case Messaging module.

Validates:
- Encrypted ticket creation with Curve25519 ECDH + AES-256-GCM.
- SHA-256 evidence attachment and verification.
- Ticket state machine — valid transitions and rejection of invalid ones.
- Immutable SHA-256 audit chain integrity (compute + verify).
- FININT bridge API endpoints (FastAPI TestClient).
- Metrics aggregation.
- Keypair generation.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

import pytest
from fastapi.testclient import TestClient

# ── Service-layer imports ───────────────────────────────────────────────────────

from app.application.services.bridge_case_service import (
    BridgeCaseService,
    InvalidTicketTransitionError,
    TicketNotFoundError,
    _compute_event_hash,
    decrypt_payload,
    encrypt_payload,
    generate_bank_keypair,
    get_bridge_service,
)
from app.domain.enums import FinintTicketStatus, FinintTicketType

# ── Ensure TESTING mode (disables DDoS middleware per-request counters) ──────────
os.environ.setdefault("TESTING", "1")


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def svc() -> BridgeCaseService:
    """Fresh BridgeCaseService instance for each test."""
    return BridgeCaseService()


@pytest.fixture()
def keypair() -> tuple[str, str]:
    """Freshly generated Curve25519 keypair (priv_b64, pub_b64)."""
    return generate_bank_keypair()


@pytest.fixture()
def sample_payload() -> bytes:
    return json.dumps({
        "subject_hash": hashlib.sha256(b"account-XYZ").hexdigest(),
        "reason": "Suspected mule account — rapid pass-through velocity detected.",
        "amount_eur": 49_750,
        "reference": "TXN-2026-09-22-001",
    }).encode("utf-8")


@pytest.fixture()
def populated_ticket(svc: BridgeCaseService, keypair: tuple[str, str], sample_payload: bytes):
    """A pre-created FININT ticket in OPEN state."""
    priv_b64, pub_b64 = keypair
    ticket = svc.create_ticket(
        ticket_type=FinintTicketType.MULE_ACCOUNT_ALERT,
        originating_bank_id=hashlib.sha256(b"bank-alpha").hexdigest(),
        recipient_bank_id=hashlib.sha256(b"bank-beta").hexdigest(),
        plaintext_payload=sample_payload,
        recipient_public_key_b64=pub_b64,
        actor="officer-001",
    )
    return ticket, priv_b64


# ═══════════════════════════════════════════════════════════════════════════════
# Cryptographic layer tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCryptographicLayer:
    def test_keypair_generation_produces_nonempty_keys(self):
        priv_b64, pub_b64 = generate_bank_keypair()
        assert len(priv_b64) >= 40
        assert len(pub_b64) >= 40

    def test_keypair_uniqueness(self):
        pair1 = generate_bank_keypair()
        pair2 = generate_bank_keypair()
        assert pair1[0] != pair2[0], "Private keys must be unique"
        assert pair1[1] != pair2[1], "Public keys must be unique"

    def test_encrypt_produces_distinct_components(self, keypair, sample_payload):
        _, pub_b64 = keypair
        ciphertext_b64, nonce_b64, ephem_pub_b64 = encrypt_payload(sample_payload, pub_b64)
        assert ciphertext_b64 != ""
        assert nonce_b64 != ""
        assert ephem_pub_b64 != ""

    def test_encrypt_decrypt_roundtrip(self, keypair, sample_payload):
        priv_b64, pub_b64 = keypair
        ciphertext_b64, nonce_b64, ephem_pub_b64 = encrypt_payload(sample_payload, pub_b64)
        decrypted = decrypt_payload(ciphertext_b64, nonce_b64, ephem_pub_b64, priv_b64)
        assert decrypted == sample_payload

    def test_different_payloads_produce_different_ciphertexts(self, keypair):
        _, pub_b64 = keypair
        ct1, _, _ = encrypt_payload(b"payload-A", pub_b64)
        ct2, _, _ = encrypt_payload(b"payload-B", pub_b64)
        assert ct1 != ct2

    def test_nonce_is_unique_per_encryption(self, keypair, sample_payload):
        _, pub_b64 = keypair
        _, n1, _ = encrypt_payload(sample_payload, pub_b64)
        _, n2, _ = encrypt_payload(sample_payload, pub_b64)
        assert n1 != n2, "Each encryption must use a fresh random nonce"


# ═══════════════════════════════════════════════════════════════════════════════
# Ticket creation tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTicketCreation:
    def test_create_ticket_returns_valid_ticket(self, svc, keypair, sample_payload):
        priv_b64, pub_b64 = keypair
        ticket = svc.create_ticket(
            ticket_type=FinintTicketType.URGENT_FREEZE_REQUEST,
            originating_bank_id="bank-orig-id",
            recipient_bank_id="bank-recv-id",
            plaintext_payload=sample_payload,
            recipient_public_key_b64=pub_b64,
        )
        assert ticket.id
        assert ticket.ticket_type == FinintTicketType.URGENT_FREEZE_REQUEST
        assert ticket.status == FinintTicketStatus.OPEN
        assert ticket.sla_hours == 4  # URGENT_FREEZE_REQUEST SLA

    def test_create_ticket_all_types(self, svc, keypair, sample_payload):
        _, pub_b64 = keypair
        for ticket_type in FinintTicketType:
            ticket = svc.create_ticket(
                ticket_type=ticket_type,
                originating_bank_id="bank-a",
                recipient_bank_id="bank-b",
                plaintext_payload=sample_payload,
                recipient_public_key_b64=pub_b64,
            )
            assert ticket.ticket_type == ticket_type

    def test_create_ticket_with_evidence(self, svc, keypair, sample_payload):
        _, pub_b64 = keypair
        evidence_blobs = [b"evidence-doc-1", b"evidence-doc-2"]
        ticket = svc.create_ticket(
            ticket_type=FinintTicketType.INFORMATION_REQUEST,
            originating_bank_id="bank-a",
            recipient_bank_id="bank-b",
            plaintext_payload=sample_payload,
            recipient_public_key_b64=pub_b64,
            evidence_bytes_list=evidence_blobs,
        )
        assert len(ticket.evidence_hashes) == 2
        for i, blob in enumerate(evidence_blobs):
            expected_hash = hashlib.sha256(blob).hexdigest()
            assert ticket.evidence_hashes[i] == expected_hash

    def test_create_ticket_invalid_type_raises(self, svc, keypair, sample_payload):
        _, pub_b64 = keypair
        with pytest.raises(ValueError, match="Unknown ticket type"):
            svc.create_ticket(
                ticket_type="INVALID_TYPE",
                originating_bank_id="bank-a",
                recipient_bank_id="bank-b",
                plaintext_payload=sample_payload,
                recipient_public_key_b64=pub_b64,
            )

    def test_ticket_stored_in_service(self, svc, keypair, sample_payload):
        _, pub_b64 = keypair
        ticket = svc.create_ticket(
            ticket_type=FinintTicketType.MULE_ACCOUNT_ALERT,
            originating_bank_id="bank-a",
            recipient_bank_id="bank-b",
            plaintext_payload=sample_payload,
            recipient_public_key_b64=pub_b64,
        )
        retrieved = svc.get_ticket(ticket.id)
        assert retrieved.id == ticket.id

    def test_initial_audit_trail_has_one_entry(self, svc, keypair, sample_payload):
        _, pub_b64 = keypair
        ticket = svc.create_ticket(
            ticket_type=FinintTicketType.INFORMATION_REQUEST,
            originating_bank_id="bank-a",
            recipient_bank_id="bank-b",
            plaintext_payload=sample_payload,
            recipient_public_key_b64=pub_b64,
        )
        assert len(ticket.audit_trail) == 1
        assert ticket.audit_trail[0].action == "TICKET_CREATED"
        assert ticket.audit_trail[0].new_status == FinintTicketStatus.OPEN


# ═══════════════════════════════════════════════════════════════════════════════
# State machine tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestStateMachine:
    def test_open_to_acknowledged(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        updated = svc.transition_status(ticket.id, FinintTicketStatus.ACKNOWLEDGED, actor="officer-002")
        assert updated.status == FinintTicketStatus.ACKNOWLEDGED

    def test_acknowledged_to_funds_frozen(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        svc.transition_status(ticket.id, FinintTicketStatus.ACKNOWLEDGED, actor="officer-002")
        updated = svc.transition_status(ticket.id, FinintTicketStatus.FUNDS_FROZEN, actor="officer-003")
        assert updated.status == FinintTicketStatus.FUNDS_FROZEN

    def test_acknowledged_to_information_attached(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        svc.transition_status(ticket.id, FinintTicketStatus.ACKNOWLEDGED, actor="officer-002")
        updated = svc.transition_status(ticket.id, FinintTicketStatus.INFORMATION_ATTACHED, actor="officer-003")
        assert updated.status == FinintTicketStatus.INFORMATION_ATTACHED

    def test_open_to_declined(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        updated = svc.transition_status(ticket.id, FinintTicketStatus.DECLINED, actor="officer-002")
        assert updated.status == FinintTicketStatus.DECLINED

    def test_declined_to_closed(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        svc.transition_status(ticket.id, FinintTicketStatus.DECLINED, actor="officer-002")
        updated = svc.transition_status(ticket.id, FinintTicketStatus.CLOSED, actor="officer-003")
        assert updated.status == FinintTicketStatus.CLOSED
        assert updated.closed_at is not None

    def test_invalid_transition_raises(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        with pytest.raises(InvalidTicketTransitionError):
            svc.transition_status(ticket.id, FinintTicketStatus.CLOSED, actor="officer-002")

    def test_closed_is_terminal(self, svc, keypair, sample_payload):
        _, pub_b64 = keypair
        ticket = svc.create_ticket(
            ticket_type=FinintTicketType.INFORMATION_REQUEST,
            originating_bank_id="bank-a",
            recipient_bank_id="bank-b",
            plaintext_payload=sample_payload,
            recipient_public_key_b64=pub_b64,
        )
        svc.transition_status(ticket.id, FinintTicketStatus.ACKNOWLEDGED, actor="o1")
        svc.transition_status(ticket.id, FinintTicketStatus.INFORMATION_ATTACHED, actor="o2")
        svc.transition_status(ticket.id, FinintTicketStatus.CLOSED, actor="o3")
        with pytest.raises(InvalidTicketTransitionError):
            svc.transition_status(ticket.id, FinintTicketStatus.OPEN, actor="o4")

    def test_transition_not_found_raises(self, svc):
        with pytest.raises(TicketNotFoundError):
            svc.transition_status("nonexistent-id", FinintTicketStatus.ACKNOWLEDGED, actor="officer")

    def test_audit_trail_grows_with_transitions(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        svc.transition_status(ticket.id, FinintTicketStatus.ACKNOWLEDGED, actor="o2")
        svc.transition_status(ticket.id, FinintTicketStatus.DECLINED, actor="o3")
        updated = svc.get_ticket(ticket.id)
        # 1 TICKET_CREATED + 2 transitions = 3 entries
        assert len(updated.audit_trail) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# Evidence tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvidenceHandling:
    def test_attach_evidence_returns_sha256_hash(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        raw_evidence = b"transaction_ledger_entry_2026"
        returned_hash = svc.attach_evidence(ticket.id, raw_evidence, actor="analyst-007")
        expected = hashlib.sha256(raw_evidence).hexdigest()
        assert returned_hash == expected

    def test_attach_evidence_hash_registered_on_ticket(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        raw_evidence = b"kyc_document_bytes"
        evidence_hash = svc.attach_evidence(ticket.id, raw_evidence, actor="analyst-007")
        updated = svc.get_ticket(ticket.id)
        assert evidence_hash in updated.evidence_hashes

    def test_verify_evidence_true(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        raw_evidence = b"account_statement"
        svc.attach_evidence(ticket.id, raw_evidence, actor="analyst-007")
        assert svc.verify_evidence(ticket.id, raw_evidence) is True

    def test_verify_evidence_false_for_wrong_bytes(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        raw_evidence = b"account_statement"
        svc.attach_evidence(ticket.id, raw_evidence, actor="analyst-007")
        assert svc.verify_evidence(ticket.id, b"tampered_bytes") is False

    def test_attach_evidence_not_found_raises(self, svc):
        with pytest.raises(TicketNotFoundError):
            svc.attach_evidence("nonexistent", b"data", actor="analyst")

    def test_duplicate_evidence_not_registered_twice(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        raw = b"same_evidence"
        svc.attach_evidence(ticket.id, raw, actor="analyst")
        svc.attach_evidence(ticket.id, raw, actor="analyst")
        updated = svc.get_ticket(ticket.id)
        evidence_hash = hashlib.sha256(raw).hexdigest()
        assert updated.evidence_hashes.count(evidence_hash) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Audit chain verification tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditChainIntegrity:
    def test_fresh_ticket_chain_is_intact(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        assert svc.verify_audit_chain(ticket.id) is True

    def test_chain_remains_intact_after_transitions(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        svc.transition_status(ticket.id, FinintTicketStatus.ACKNOWLEDGED, actor="o2")
        svc.transition_status(ticket.id, FinintTicketStatus.FUNDS_FROZEN, actor="o3")
        svc.transition_status(ticket.id, FinintTicketStatus.CLOSED, actor="o4")
        assert svc.verify_audit_chain(ticket.id) is True

    def test_tampered_chain_detected(self, svc, populated_ticket):
        ticket, _ = populated_ticket
        svc.transition_status(ticket.id, FinintTicketStatus.ACKNOWLEDGED, actor="o2")
        updated = svc.get_ticket(ticket.id)
        # Tamper the first entry hash
        updated.audit_trail[0].event_hash = "tampered_" + "0" * 55
        assert svc.verify_audit_chain(ticket.id) is False

    def test_verify_chain_not_found_raises(self, svc):
        with pytest.raises(TicketNotFoundError):
            svc.verify_audit_chain("no-such-ticket")

    def test_event_hash_computation_is_deterministic(self):
        from datetime import datetime, UTC
        ts = datetime(2026, 9, 22, 18, 0, 0, tzinfo=UTC)
        h1 = _compute_event_hash("prev", 0, "actor", "action", ts)
        h2 = _compute_event_hash("prev", 0, "actor", "action", ts)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex


# ═══════════════════════════════════════════════════════════════════════════════
# Listing and retrieval tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestListingAndRetrieval:
    def test_list_all_tickets(self, svc, keypair, sample_payload):
        _, pub_b64 = keypair
        for ticket_type in FinintTicketType:
            svc.create_ticket(
                ticket_type=ticket_type,
                originating_bank_id="bank-a",
                recipient_bank_id="bank-b",
                plaintext_payload=sample_payload,
                recipient_public_key_b64=pub_b64,
            )
        result = svc.list_tickets()
        assert len(result) == len(FinintTicketType)

    def test_list_filter_by_bank(self, svc, keypair, sample_payload):
        _, pub_b64 = keypair
        svc.create_ticket(
            ticket_type=FinintTicketType.MULE_ACCOUNT_ALERT,
            originating_bank_id="bank-alpha",
            recipient_bank_id="bank-beta",
            plaintext_payload=sample_payload,
            recipient_public_key_b64=pub_b64,
        )
        svc.create_ticket(
            ticket_type=FinintTicketType.INFORMATION_REQUEST,
            originating_bank_id="bank-gamma",
            recipient_bank_id="bank-delta",
            plaintext_payload=sample_payload,
            recipient_public_key_b64=pub_b64,
        )
        result = svc.list_tickets(bank_id="bank-alpha")
        assert len(result) == 1
        assert result[0].originating_bank_id == "bank-alpha"

    def test_list_filter_by_status(self, svc, keypair, sample_payload):
        _, pub_b64 = keypair
        t1 = svc.create_ticket(
            ticket_type=FinintTicketType.INFORMATION_REQUEST,
            originating_bank_id="bank-a",
            recipient_bank_id="bank-b",
            plaintext_payload=sample_payload,
            recipient_public_key_b64=pub_b64,
        )
        svc.transition_status(t1.id, FinintTicketStatus.ACKNOWLEDGED, actor="o1")
        # Second ticket stays OPEN
        svc.create_ticket(
            ticket_type=FinintTicketType.MULE_ACCOUNT_ALERT,
            originating_bank_id="bank-a",
            recipient_bank_id="bank-b",
            plaintext_payload=sample_payload,
            recipient_public_key_b64=pub_b64,
        )
        acknowledged = svc.list_tickets(status_filter=FinintTicketStatus.ACKNOWLEDGED)
        assert len(acknowledged) == 1
        assert acknowledged[0].id == t1.id

    def test_get_ticket_not_found_raises(self, svc):
        with pytest.raises(TicketNotFoundError):
            svc.get_ticket("nonexistent-uuid")

    def test_limit_parameter_respected(self, svc, keypair, sample_payload):
        _, pub_b64 = keypair
        for _ in range(10):
            svc.create_ticket(
                ticket_type=FinintTicketType.INFORMATION_REQUEST,
                originating_bank_id="bank-a",
                recipient_bank_id="bank-b",
                plaintext_payload=sample_payload,
                recipient_public_key_b64=pub_b64,
            )
        result = svc.list_tickets(limit=5)
        assert len(result) == 5


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetrics:
    def test_metrics_empty_service(self, svc):
        m = svc.get_metrics()
        assert m["total_tickets"] == 0
        assert m["by_status"] == {}
        assert m["by_type"] == {}

    def test_metrics_after_creation(self, svc, keypair, sample_payload):
        _, pub_b64 = keypair
        svc.create_ticket(
            ticket_type=FinintTicketType.URGENT_FREEZE_REQUEST,
            originating_bank_id="bank-a",
            recipient_bank_id="bank-b",
            plaintext_payload=sample_payload,
            recipient_public_key_b64=pub_b64,
        )
        m = svc.get_metrics()
        assert m["total_tickets"] == 1
        assert m["by_status"].get("OPEN") == 1
        assert m["by_type"].get("URGENT_FREEZE_REQUEST") == 1
        assert "crypto_backend" in m


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP API endpoint tests
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient for the full application."""
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def api_keypair():
    return generate_bank_keypair()


class TestBridgeMessagingAPI:

    def _payload_b64(self) -> str:
        return base64.urlsafe_b64encode(
            json.dumps({"subject": "test", "amount": 100}).encode()
        ).decode().rstrip("=")

    def test_generate_keypair_endpoint(self, client):
        resp = client.post("/api/v1/bridge/keypair")
        assert resp.status_code == 201
        data = resp.json()
        assert "private_key_b64" in data
        assert "public_key_b64" in data
        assert "warning" in data

    def test_create_ticket_endpoint(self, client, api_keypair):
        priv_b64, pub_b64 = api_keypair
        payload = {
            "ticket_type": "INFORMATION_REQUEST",
            "originating_bank_id": "bank-origin-" + hashlib.sha256(b"origin").hexdigest()[:8],
            "recipient_bank_id": "bank-recipient-" + hashlib.sha256(b"recipient").hexdigest()[:8],
            "plaintext_payload_b64": self._payload_b64(),
            "recipient_public_key_b64": pub_b64,
            "actor": "test-officer",
        }
        resp = client.post("/api/v1/bridge/tickets", json=payload)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["ticket_type"] == "INFORMATION_REQUEST"
        assert data["status"] == "OPEN"
        assert data["encrypted_payload"] != ""
        assert data["sla_hours"] == 72
        assert len(data["audit_trail"]) == 1

    def test_list_tickets_endpoint(self, client, api_keypair):
        _, pub_b64 = api_keypair
        payload = {
            "ticket_type": "MULE_ACCOUNT_ALERT",
            "originating_bank_id": "bank-list-test",
            "recipient_bank_id": "bank-list-recv",
            "plaintext_payload_b64": self._payload_b64(),
            "recipient_public_key_b64": pub_b64,
            "actor": "test-officer",
        }
        client.post("/api/v1/bridge/tickets", json=payload)
        resp = client.get("/api/v1/bridge/tickets")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_get_ticket_endpoint(self, client, api_keypair):
        _, pub_b64 = api_keypair
        create_resp = client.post("/api/v1/bridge/tickets", json={
            "ticket_type": "TRANSACTION_DISPUTE_TRACE",
            "originating_bank_id": "bank-get-orig",
            "recipient_bank_id": "bank-get-recv",
            "plaintext_payload_b64": self._payload_b64(),
            "recipient_public_key_b64": pub_b64,
            "actor": "test-officer",
        })
        assert create_resp.status_code == 201
        ticket_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/bridge/tickets/{ticket_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == ticket_id

    def test_get_ticket_not_found(self, client):
        resp = client.get("/api/v1/bridge/tickets/nonexistent-uuid-123")
        assert resp.status_code == 404

    def test_transition_ticket_endpoint(self, client, api_keypair):
        _, pub_b64 = api_keypair
        create_resp = client.post("/api/v1/bridge/tickets", json={
            "ticket_type": "URGENT_FREEZE_REQUEST",
            "originating_bank_id": "bank-transition-orig",
            "recipient_bank_id": "bank-transition-recv",
            "plaintext_payload_b64": self._payload_b64(),
            "recipient_public_key_b64": pub_b64,
            "actor": "test-officer",
        })
        ticket_id = create_resp.json()["id"]
        resp = client.post(f"/api/v1/bridge/tickets/{ticket_id}/transition", json={
            "new_status": "ACKNOWLEDGED",
            "actor": "recipient-officer",
            "action": "Ticket received and acknowledged per SLA procedure.",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ACKNOWLEDGED"

    def test_invalid_transition_returns_409(self, client, api_keypair):
        _, pub_b64 = api_keypair
        create_resp = client.post("/api/v1/bridge/tickets", json={
            "ticket_type": "INFORMATION_REQUEST",
            "originating_bank_id": "bank-409-orig",
            "recipient_bank_id": "bank-409-recv",
            "plaintext_payload_b64": self._payload_b64(),
            "recipient_public_key_b64": pub_b64,
            "actor": "test-officer",
        })
        ticket_id = create_resp.json()["id"]
        # Attempt invalid: OPEN → CLOSED (not allowed)
        resp = client.post(f"/api/v1/bridge/tickets/{ticket_id}/transition", json={
            "new_status": "CLOSED",
            "actor": "attacker",
        })
        assert resp.status_code == 409

    def test_attach_evidence_endpoint(self, client, api_keypair):
        _, pub_b64 = api_keypair
        create_resp = client.post("/api/v1/bridge/tickets", json={
            "ticket_type": "MULE_ACCOUNT_ALERT",
            "originating_bank_id": "bank-ev-orig",
            "recipient_bank_id": "bank-ev-recv",
            "plaintext_payload_b64": self._payload_b64(),
            "recipient_public_key_b64": pub_b64,
            "actor": "test-officer",
        })
        ticket_id = create_resp.json()["id"]
        evidence_b64 = base64.urlsafe_b64encode(b"kyc_doc_contents").decode().rstrip("=")
        resp = client.post(f"/api/v1/bridge/tickets/{ticket_id}/evidence", json={
            "evidence_b64": evidence_b64,
            "actor": "analyst-123",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert len(data["evidence_hash"]) == 64
        assert data["ticket_id"] == ticket_id

    def test_verify_evidence_endpoint(self, client, api_keypair):
        _, pub_b64 = api_keypair
        create_resp = client.post("/api/v1/bridge/tickets", json={
            "ticket_type": "MULE_ACCOUNT_ALERT",
            "originating_bank_id": "bank-vev-orig",
            "recipient_bank_id": "bank-vev-recv",
            "plaintext_payload_b64": self._payload_b64(),
            "recipient_public_key_b64": pub_b64,
            "actor": "test-officer",
        })
        ticket_id = create_resp.json()["id"]
        raw_evidence = b"account_statement_page_1"
        ev_b64 = base64.urlsafe_b64encode(raw_evidence).decode().rstrip("=")
        client.post(f"/api/v1/bridge/tickets/{ticket_id}/evidence", json={
            "evidence_b64": ev_b64,
            "actor": "analyst",
        })
        resp = client.post(f"/api/v1/bridge/tickets/{ticket_id}/verify-evidence", json={
            "evidence_b64": ev_b64,
        })
        assert resp.status_code == 200
        assert resp.json()["verified"] is True

    def test_verify_chain_endpoint_intact(self, client, api_keypair):
        _, pub_b64 = api_keypair
        create_resp = client.post("/api/v1/bridge/tickets", json={
            "ticket_type": "INFORMATION_REQUEST",
            "originating_bank_id": "bank-chain-orig",
            "recipient_bank_id": "bank-chain-recv",
            "plaintext_payload_b64": self._payload_b64(),
            "recipient_public_key_b64": pub_b64,
            "actor": "test-officer",
        })
        ticket_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/bridge/tickets/{ticket_id}/verify-chain")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chain_intact"] is True
        assert data["entry_count"] >= 1

    def test_metrics_endpoint(self, client):
        resp = client.get("/api/v1/bridge/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_tickets" in data
        assert "by_status" in data
        assert "by_type" in data
        assert "crypto_backend" in data

    def test_v1_router_also_works(self, client, api_keypair):
        """Verify dual-routing: /v1/bridge also serves the same endpoints."""
        _, pub_b64 = api_keypair
        resp = client.post("/v1/bridge/tickets", json={
            "ticket_type": "INFORMATION_REQUEST",
            "originating_bank_id": "bank-v1-orig",
            "recipient_bank_id": "bank-v1-recv",
            "plaintext_payload_b64": self._payload_b64(),
            "recipient_public_key_b64": pub_b64,
            "actor": "test-officer",
        })
        assert resp.status_code == 201

    def test_audit_chain_endpoint(self, client, api_keypair):
        _, pub_b64 = api_keypair
        create_resp = client.post("/api/v1/bridge/tickets", json={
            "ticket_type": "MULE_ACCOUNT_ALERT",
            "originating_bank_id": "bank-audit-orig",
            "recipient_bank_id": "bank-audit-recv",
            "plaintext_payload_b64": self._payload_b64(),
            "recipient_public_key_b64": pub_b64,
            "actor": "test-officer",
        })
        ticket_id = create_resp.json()["id"]
        client.post(f"/api/v1/bridge/tickets/{ticket_id}/transition", json={
            "new_status": "ACKNOWLEDGED",
            "actor": "officer-recv",
        })
        resp = client.get(f"/api/v1/bridge/tickets/{ticket_id}/audit-chain")
        assert resp.status_code == 200
        chain = resp.json()
        assert len(chain) == 2  # TICKET_CREATED + transition
        assert chain[0]["action"] == "TICKET_CREATED"

    def test_list_filter_by_status_query_param(self, client, api_keypair):
        _, pub_b64 = api_keypair
        create_resp = client.post("/api/v1/bridge/tickets", json={
            "ticket_type": "INFORMATION_REQUEST",
            "originating_bank_id": "bank-filter-orig",
            "recipient_bank_id": "bank-filter-recv",
            "plaintext_payload_b64": self._payload_b64(),
            "recipient_public_key_b64": pub_b64,
            "actor": "test-officer",
        })
        ticket_id = create_resp.json()["id"]
        client.post(f"/api/v1/bridge/tickets/{ticket_id}/transition", json={
            "new_status": "ACKNOWLEDGED",
            "actor": "officer",
        })
        resp = client.get("/api/v1/bridge/tickets", params={"status": "ACKNOWLEDGED"})
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["status"] == "ACKNOWLEDGED" for t in data)
