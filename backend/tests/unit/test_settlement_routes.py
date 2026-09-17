"""Unit tests for Web3 & CBDC Smart Contract Incentive Settlement API routes.

Validates multi-prefix routing, LOO Shapley incentive distribution, participant payout claims,
Gnosis Safe 2-of-3 multi-sig governance proposals, and participant node quarantine/slashing.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.security.smart_contract_driver import SmartContractSettlementDriver
from app.main import app
from app.presentation.routers.settlement import _CLAIMED_PAYOUTS


@pytest.fixture(autouse=True)
def clean_settlement_state() -> Generator[None, None, None]:
    """Ensures clean driver and claim registry state across tests."""
    driver = SmartContractSettlementDriver.get_instance()
    driver.reset()
    _CLAIMED_PAYOUTS.clear()
    yield
    driver.reset()
    _CLAIMED_PAYOUTS.clear()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


# ── Contract Info & History Tests ──────────────────────────────


@pytest.mark.parametrize("prefix", ["/api/v1/settlement", "/v1/settlement"])
def test_get_contract_info(client: TestClient, prefix: str) -> None:
    resp = client.get(f"{prefix}/contract-info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["contract_address"].startswith("0x")
    assert data["coordinator_address"].startswith("0x")
    assert "wCBDC" in data["supported_currencies"]
    assert data["chain_id"] == 11155111
    assert isinstance(data["abi"], list)


def test_empty_settlement_history(client: TestClient) -> None:
    resp = client.get("/api/v1/settlement/history")
    assert resp.status_code == 200
    assert resp.json() == []

    # Verify canonical alias /epochs
    alias_resp = client.get("/api/v1/settlement/epochs")
    assert alias_resp.status_code == 200
    assert alias_resp.json() == []


# ── Incentive Settlement & Claim Flow Tests ────────────────────


def test_settlement_trigger_and_epochs_flow(client: TestClient) -> None:
    trigger_payload = {
        "epoch_id": "sim_epoch_001",
        "contributions": {
            "Bank A": 0.50,
            "Bank B": 0.30,
            "Bank C": 0.20,
        },
        "quarantine_statuses": {
            "Bank A": False,
            "Bank B": False,
            "Bank C": False,
        },
        "audit_proof_hash": "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890",
        "total_pool_usd": 100000.0,
        "currency": "wCBDC",
    }

    resp = client.post("/api/v1/settlement/trigger", json=trigger_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["epoch_id"] == "sim_epoch_001"
    assert data["status"] == "SUCCESS"
    assert data["transaction_hash"].startswith("0x")
    assert data["total_distributed_usd"] == 100000.0
    assert len(data["payouts"]) == 3

    # Check Bank A received 50% = $50,000
    bank_a = next(p for p in data["payouts"] if p["bank_name"] == "Bank A")
    assert bank_a["share_percent"] == 50.0
    assert bank_a["payout_usd"] == 50000.0
    assert bank_a["status"] == "DISTRIBUTED"

    # Verify history now reflects the epoch
    hist_resp = client.get("/api/v1/settlement/history")
    assert hist_resp.status_code == 200
    assert len(hist_resp.json()) == 1


def test_settlement_duplicate_epoch_conflict_409(client: TestClient) -> None:
    payload = {
        "epoch_id": "duplicate_epoch_01",
        "contributions": {"Bank A": 1.0},
        "audit_proof_hash": "11223344556677889900aabbccddeeff",
        "total_pool_usd": 50000.0,
    }
    r1 = client.post("/api/v1/settlement/trigger", json=payload)
    assert r1.status_code == 200

    r2 = client.post("/api/v1/settlement/trigger", json=payload)
    assert r2.status_code == 409
    assert "already been settled" in r2.json()["detail"]


def test_settlement_trigger_validation_errors(client: TestClient) -> None:
    # Empty contributions -> 422
    resp1 = client.post(
        "/api/v1/settlement/trigger",
        json={
            "epoch_id": "bad_epoch",
            "contributions": {},
            "audit_proof_hash": "11223344556677889900aabbccddeeff",
        },
    )
    assert resp1.status_code == 422

    # Audit proof hash too short -> 422
    resp2 = client.post(
        "/api/v1/settlement/trigger",
        json={
            "epoch_id": "bad_epoch",
            "contributions": {"Bank A": 1.0},
            "audit_proof_hash": "short_hash",
        },
    )
    assert resp2.status_code == 422

    # Zero total pool USD -> 422
    resp3 = client.post(
        "/api/v1/settlement/trigger",
        json={
            "epoch_id": "bad_epoch",
            "contributions": {"Bank A": 1.0},
            "audit_proof_hash": "11223344556677889900aabbccddeeff",
            "total_pool_usd": 0.0,
        },
    )
    assert resp3.status_code == 422


def test_participant_payout_claim_lifecycle(client: TestClient) -> None:
    # 1. Setup settled epoch
    client.post(
        "/api/v1/settlement/trigger",
        json={
            "epoch_id": "epoch_claim_test",
            "contributions": {"Bank A": 0.70, "Bank B": 0.30},
            "audit_proof_hash": "aabbccddeeff00112233445566778899",
            "total_pool_usd": 10000.0,
        },
    )

    # 2. Claim against non-existent epoch -> 404
    r404_epoch = client.post(
        "/api/v1/settlement/claim",
        json={"epoch_id": "unknown_epoch", "claimant_bank": "Bank A"},
    )
    assert r404_epoch.status_code == 404

    # 3. Claim by non-participant bank -> 404
    r404_bank = client.post(
        "/api/v1/settlement/claim",
        json={"epoch_id": "epoch_claim_test", "claimant_bank": "Unknown Bank"},
    )
    assert r404_bank.status_code == 404

    # 4. Legitimate claim by Bank A -> 200
    claim_resp = client.post(
        "/api/v1/settlement/claim",
        json={"epoch_id": "epoch_claim_test", "claimant_bank": "Bank A"},
    )
    assert claim_resp.status_code == 200
    claim_data = claim_resp.json()
    assert claim_data["claimant_bank"] == "Bank A"
    assert claim_data["payout_usd"] == 7000.0
    assert claim_data["status"] == "CLAIMED"
    assert claim_data["transaction_hash"].startswith("0x")

    # 5. Duplicate claim by Bank A -> 409 Conflict
    dup_resp = client.post(
        "/api/v1/settlement/claim",
        json={"epoch_id": "epoch_claim_test", "claimant_bank": "Bank A"},
    )
    assert dup_resp.status_code == 409
    assert "already been claimed" in dup_resp.json()["detail"]


def test_quarantined_participant_claim_blocked_403(client: TestClient) -> None:
    # Trigger with Bank C quarantined
    client.post(
        "/api/v1/settlement/trigger",
        json={
            "epoch_id": "epoch_quarantine_claim",
            "contributions": {"Bank A": 0.60, "Bank C": 0.40},
            "quarantine_statuses": {"Bank C": True},
            "audit_proof_hash": "11223344556677889900aabbccddeeff",
            "total_pool_usd": 10000.0,
        },
    )

    # Bank C attempts claim -> 403 Forbidden
    resp = client.post(
        "/api/v1/settlement/claim",
        json={"epoch_id": "epoch_quarantine_claim", "claimant_bank": "Bank C"},
    )
    assert resp.status_code == 403
    assert "quarantined on-chain" in resp.json()["detail"]


# ── Gnosis Safe Multi-Sig Governance Tests ─────────────────────


def test_multisig_proposals_lifecycle(client: TestClient) -> None:
    # 1. Propose action by authorized trustee
    trustee1 = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    trustee2 = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

    propose_payload = {
        "proposer_wallet": trustee1,
        "action_type": "QUARANTINE_PARTICIPANT",
        "epoch_id": 42,
        "payload": {"target_bank": "Bank Malicious"},
    }

    p_resp = client.post("/api/v1/settlement/multisig/propose", json=propose_payload)
    assert p_resp.status_code == 200
    tx_id = p_resp.json()["tx_id"]
    assert tx_id >= 0

    # 2. Get proposal by ID
    get_resp = client.get(f"/api/v1/settlement/multisig/proposals/{tx_id}")
    assert get_resp.status_code == 200
    prop = get_resp.json()
    assert prop["tx_id"] == tx_id
    assert prop["confirmation_count"] == 1  # Proposer automatically confirms
    assert prop["executed"] is False

    # 3. Confirm with trustee2 -> threshold 2/3 reached -> executed = True
    c_resp = client.post(
        "/api/v1/settlement/multisig/confirm",
        json={"tx_id": tx_id, "owner_wallet": trustee2},
    )
    assert c_resp.status_code == 200
    assert c_resp.json()["confirmation_count"] == 2
    assert c_resp.json()["executed"] is True

    # 4. List all proposals
    list_resp = client.get("/api/v1/settlement/multisig/proposals")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) >= 1


def test_multisig_proposal_not_found(client: TestClient) -> None:
    resp = client.get("/api/v1/settlement/multisig/proposals/99999")
    assert resp.status_code == 404


# ── Node Quarantine & Byzantine Slashing Tests ─────────────────


def test_quarantine_and_clear_lifecycle(client: TestClient) -> None:
    # Quarantine bank
    q_resp = client.post(
        "/api/v1/settlement/quarantine",
        json={"bank_name_or_wallet": "Bank Rogue", "reason": "Repeated gradient poisoning"},
    )
    assert q_resp.status_code == 200
    assert "quarantined on-chain" in q_resp.json()["message"]

    # Clear quarantine
    c_resp = client.post(
        "/api/v1/settlement/clear-quarantine",
        json={"bank_name_or_wallet": "Bank Rogue"},
    )
    assert c_resp.status_code == 200
    assert "Quarantine cleared" in c_resp.json()["message"]


def test_byzantine_slashing_and_catalog(client: TestClient) -> None:
    slash_payload = {
        "bank_name_or_wallet": "Bank Poisoner",
        "penalty_usd": 25000.0,
        "reason": "Byzantine sign-flipping attack verified",
    }
    s_resp = client.post("/api/v1/settlement/slash", json=slash_payload)
    assert s_resp.status_code == 200
    s_data = s_resp.json()
    assert s_data["status"] == "SUCCESS"
    assert s_data["slashed_record"]["penalty_usd"] == 25000.0

    # Retrieve catalog
    catalog_resp = client.get("/api/v1/settlement/slashed")
    assert catalog_resp.status_code == 200
    cat = catalog_resp.json()
    assert "Bank Poisoner" in cat
    assert len(cat["Bank Poisoner"]) == 1
