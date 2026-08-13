"""Targeted unit tests for GnosisSafeMultiSigCoordinatorDriver.

Covers:
  - 2-of-3 proposal submission & single proposer signature
  - Second trustee signature triggering execution (2/3 threshold met)
  - Rejection of unauthorized proposer/signer addresses
  - Rejection of duplicate signatures from the same owner
  - Rejection of confirmation on already executed proposals
"""

from __future__ import annotations

import pytest

from app.infrastructure.security.gnosis_multisig_coordinator import (
    GnosisSafeMultiSigCoordinatorDriver,
    GovernanceActionType,
)


@pytest.fixture()
def driver() -> GnosisSafeMultiSigCoordinatorDriver:
    return GnosisSafeMultiSigCoordinatorDriver()


def test_submit_proposal_success(driver: GnosisSafeMultiSigCoordinatorDriver) -> None:
    """Proposer owner must successfully create proposal with confirmation_count=1."""
    proposer = driver.owner_wallets[0]
    prop = driver.submit_proposal(
        proposer_wallet=proposer,
        action_type=GovernanceActionType.DISTRIBUTE_INCENTIVES,
        epoch_id=101,
        payload={"total_wei": 1000000},
    )

    assert prop.tx_id == 0
    assert prop.confirmation_count == 1
    assert prop.executed is False
    assert prop.confirmations[proposer] is True


def test_threshold_met_executes_proposal(
    driver: GnosisSafeMultiSigCoordinatorDriver,
) -> None:
    """Second trustee confirmation must push count to 2 and set executed=True."""
    proposer = driver.owner_wallets[0]
    second_trustee = driver.owner_wallets[1]

    prop = driver.submit_proposal(
        proposer_wallet=proposer,
        action_type=GovernanceActionType.QUARANTINE_PARTICIPANT,
        epoch_id=102,
    )
    assert prop.executed is False

    updated_prop = driver.confirm_proposal(prop.tx_id, second_trustee)
    assert updated_prop.confirmation_count == 2
    assert updated_prop.executed is True
    assert updated_prop.confirmations[second_trustee] is True


def test_unauthorized_proposer_raises(
    driver: GnosisSafeMultiSigCoordinatorDriver,
) -> None:
    """Non-owner proposer must raise ValueError."""
    unauthorized = "0x0000000000000000000000000000000000000099"
    with pytest.raises(ValueError, match="not an authorized trustee owner"):
        driver.submit_proposal(
            proposer_wallet=unauthorized,
            action_type=GovernanceActionType.DEPOSIT_POOL,
            epoch_id=103,
        )


def test_duplicate_signature_raises(
    driver: GnosisSafeMultiSigCoordinatorDriver,
) -> None:
    """Confirming twice by the same owner must raise ValueError."""
    proposer = driver.owner_wallets[0]
    prop = driver.submit_proposal(
        proposer_wallet=proposer,
        action_type=GovernanceActionType.DEPOSIT_POOL,
        epoch_id=104,
    )

    with pytest.raises(ValueError, match="already been confirmed"):
        driver.confirm_proposal(prop.tx_id, proposer)


def test_confirm_executed_proposal_raises(
    driver: GnosisSafeMultiSigCoordinatorDriver,
) -> None:
    """Confirming an already executed proposal must raise RuntimeError."""
    proposer = driver.owner_wallets[0]
    owner2 = driver.owner_wallets[1]
    owner3 = driver.owner_wallets[2]

    prop = driver.submit_proposal(proposer, GovernanceActionType.DISTRIBUTE_INCENTIVES, 105)
    driver.confirm_proposal(prop.tx_id, owner2)  # Now executed!

    with pytest.raises(RuntimeError, match="already been executed"):
        driver.confirm_proposal(prop.tx_id, owner3)
