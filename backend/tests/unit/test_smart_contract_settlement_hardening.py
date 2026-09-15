"""Targeted hardening unit tests for Smart Contract Settlement & Gnosis Multi-Sig."""

from __future__ import annotations

import concurrent.futures

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.security.gnosis_multisig_coordinator import (
    GnosisSafeMultiSigCoordinatorDriver,
    GovernanceActionType,
)
from app.infrastructure.security.smart_contract_driver import (
    EpochAlreadySettledError,
    InvalidSettlementParameterError,
    SmartContractSettlementDriver,
)
from app.main import app


@pytest.fixture(autouse=True)
def clean_driver_state():
    driver = SmartContractSettlementDriver.get_instance()
    driver.reset()
    yield
    driver.reset()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_duplicate_epoch_settlement_rejected():
    driver = SmartContractSettlementDriver.get_instance()
    contributions = {"Bank A": 0.6, "Bank B": 0.4}
    audit_hash = "112233445566778899aabbccddeeff00"

    # First settlement should succeed
    receipt1 = driver.settle_incentives(
        epoch_id="epoch_test_dup_001",
        contributions=contributions,
        audit_proof_hash=audit_hash,
    )
    assert receipt1["status"] == "SUCCESS"

    # Second settlement on the same epoch must raise EpochAlreadySettledError
    with pytest.raises(EpochAlreadySettledError, match="has already been settled"):
        driver.settle_incentives(
            epoch_id="epoch_test_dup_001",
            contributions=contributions,
            audit_proof_hash=audit_hash,
        )


def test_invalid_settlement_parameters_rejected():
    driver = SmartContractSettlementDriver.get_instance()

    with pytest.raises(InvalidSettlementParameterError, match="Empty recipients"):
        driver.settle_incentives(epoch_id="epoch_test_invalid_1", contributions={})

    with pytest.raises(InvalidSettlementParameterError, match="Invalid cryptographic audit"):
        driver.settle_incentives(
            epoch_id="epoch_test_invalid_2",
            contributions={"Bank A": 1.0},
            audit_proof_hash="short",
        )

    with pytest.raises(InvalidSettlementParameterError, match="Total pool budget"):
        driver.settle_incentives(
            epoch_id="epoch_test_invalid_3",
            contributions={"Bank A": 1.0},
            audit_proof_hash="112233445566778899aabbcc",
            total_pool_usd=-100.0,
        )


def test_quarantine_bank_lifecycle():
    driver = SmartContractSettlementDriver.get_instance()
    assert not driver.is_bank_quarantined("Bank C")

    driver.quarantine_bank("Bank C", reason="Malicious model poisoning")
    assert driver.is_bank_quarantined("Bank C")

    # Settling incentives with Bank C quarantined must result in 0 payout for Bank C
    contributions = {"Bank A": 0.5, "Bank B": 0.3, "Bank C": 0.2}
    receipt = driver.settle_incentives(
        epoch_id="epoch_quarantine_test_01",
        contributions=contributions,
        audit_proof_hash="112233445566778899aabbccddeeff00",
        total_pool_usd=100000.0,
    )
    payout_c = next(p for p in receipt["payouts"] if p["bank_name"] == "Bank C")
    assert payout_c["is_quarantined"] is True
    assert payout_c["payout_usd"] == 0.0
    assert payout_c["status"] == "BLOCKED_QUARANTINE"

    # Clearing quarantine restores normal participation
    driver.clear_quarantine("Bank C")
    assert not driver.is_bank_quarantined("Bank C")


def test_slash_bank_penalty():
    driver = SmartContractSettlementDriver.get_instance()
    with pytest.raises(ValueError, match="greater than zero"):
        driver.slash_bank("Bank B", penalty_usd=0.0)

    record = driver.slash_bank("Bank B", penalty_usd=25000.0, reason="Byzantine Sybil collusion")
    assert record["penalty_usd"] == 25000.0
    assert record["bank"] == "Bank B"
    assert driver.is_bank_quarantined("Bank B")

    slashed = driver.get_slashed_penalties()
    assert "Bank B" in slashed
    assert len(slashed["Bank B"]) == 1


def test_gnosis_multisig_revoke_confirmation():
    driver = GnosisSafeMultiSigCoordinatorDriver()
    owner1 = driver.owner_wallets[0]
    owner2 = driver.owner_wallets[1]

    prop = driver.submit_proposal(owner1, GovernanceActionType.DISTRIBUTE_INCENTIVES, 901)
    assert prop.confirmation_count == 1
    assert prop.confirmations[owner1] is True

    # Owner 1 revokes confirmation
    revoked = driver.revoke_confirmation(prop.tx_id, owner1)
    assert revoked.confirmation_count == 0
    assert revoked.confirmations[owner1] is False
    assert revoked.executed is False

    # Cannot revoke if not confirmed
    with pytest.raises(ValueError, match="has not been confirmed"):
        driver.revoke_confirmation(prop.tx_id, owner1)

    with pytest.raises(ValueError, match="has not been confirmed"):
        driver.revoke_confirmation(prop.tx_id, owner2)

    # Re-confirm and execute, then revocation is disallowed
    driver.confirm_proposal(prop.tx_id, owner1)
    driver.confirm_proposal(prop.tx_id, owner2)  # threshold reached!
    with pytest.raises(RuntimeError, match="already been executed"):
        driver.revoke_confirmation(prop.tx_id, owner1)


def test_settlement_router_endpoints(client: TestClient):
    owner1 = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    owner2 = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

    # 1. Propose
    res = client.post(
        "/api/v1/settlement/multisig/propose",
        json={
            "proposer_wallet": owner1,
            "action_type": "DISTRIBUTE_INCENTIVES",
            "epoch_id": 990,
            "payload": {"amount": 50000},
        },
    )
    assert res.status_code == 200, res.text
    tx_id = res.json()["tx_id"]

    # 2. Get proposal by ID
    res_get = client.get(f"/api/v1/settlement/multisig/proposals/{tx_id}")
    assert res_get.status_code == 200
    assert res_get.json()["confirmation_count"] == 1

    # 3. Confirm via owner2 and check threshold execution
    res_conf = client.post(
        "/api/v1/settlement/multisig/confirm",
        json={"tx_id": tx_id, "owner_wallet": owner2},
    )
    assert res_conf.status_code == 200
    assert res_conf.json()["confirmation_count"] == 2
    assert res_conf.json()["executed"] is True

    # 4. Propose second action for revocation test
    res2 = client.post(
        "/api/v1/settlement/multisig/propose",
        json={
            "proposer_wallet": owner1,
            "action_type": "DEPOSIT_POOL",
            "epoch_id": 991,
        },
    )
    tx_id2 = res2.json()["tx_id"]

    # Revoke proposal confirmation
    res_rev = client.post(
        "/api/v1/settlement/multisig/revoke",
        json={"tx_id": tx_id2, "owner_wallet": owner1},
    )
    assert res_rev.status_code == 200
    assert res_rev.json()["confirmation_count"] == 0

    # 4. Quarantine and Clear Quarantine
    res_q = client.post(
        "/api/v1/settlement/quarantine",
        json={"bank_name_or_wallet": "Bank A", "reason": "Test Poisoning"},
    )
    assert res_q.status_code == 200

    res_cq = client.post(
        "/api/v1/settlement/clear-quarantine",
        json={"bank_name_or_wallet": "Bank A"},
    )
    assert res_cq.status_code == 200

    # 5. Slash
    res_slash = client.post(
        "/api/v1/settlement/slash",
        json={"bank_name_or_wallet": "Bank B", "penalty_usd": 15000.0, "reason": "Test Slash"},
    )
    assert res_slash.status_code == 200
    assert res_slash.json()["slashed_record"]["penalty_usd"] == 15000.0

    res_slashed = client.get("/api/v1/settlement/slashed")
    assert res_slashed.status_code == 200
    assert "Bank B" in res_slashed.json()

    # 6. Trigger duplicate epoch -> 409
    payload = {
        "epoch_id": "api_epoch_dup_01",
        "contributions": {"Bank A": 1.0},
        "audit_proof_hash": "112233445566778899aabbccddeeff00",
        "total_pool_usd": 10000.0,
        "currency": "wCBDC",
    }
    r1 = client.post("/api/v1/settlement/trigger", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/api/v1/settlement/trigger", json=payload)
    assert r2.status_code == 409


def test_smart_contract_driver_concurrency():
    driver = SmartContractSettlementDriver.get_instance()
    audit_hash = "112233445566778899aabbccddeeff00"

    def settle_job(idx: int) -> dict:
        return driver.settle_incentives(
            epoch_id=f"concurrent_epoch_{idx}",
            contributions={"Bank A": 0.5, "Bank B": 0.5},
            audit_proof_hash=audit_hash,
            total_pool_usd=10000.0,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(settle_job, i) for i in range(12)]
        results = [f.result() for f in futures]

    assert len(results) == 12
    assert all(r["status"] == "SUCCESS" for r in results)
    history = driver.get_settlement_history()
    assert len(history) == 12
