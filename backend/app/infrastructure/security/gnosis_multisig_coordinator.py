"""Gnosis Safe 2-of-3 Multi-Sig Coordinator Governance Driver.

Enforces 2-of-3 threshold multi-signature approval for critical consortium coordinator
actions (Incentive Settlement, Bank Quarantine, Pool Deposit). Eliminates single-wallet
EOA centralization risks and Single Points of Failure (SPOF).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class GovernanceActionType(StrEnum):
    DISTRIBUTE_INCENTIVES = "DISTRIBUTE_INCENTIVES"
    QUARANTINE_PARTICIPANT = "QUARANTINE_PARTICIPANT"
    DEPOSIT_POOL = "DEPOSIT_POOL"
    TRANSFER_OWNERSHIP = "TRANSFER_OWNERSHIP"


@dataclass(frozen=True)
class MultiSigProposal:
    """Container for Gnosis Safe Multi-Sig Proposal State."""

    tx_id: int
    action_type: GovernanceActionType
    epoch_id: int
    payload_hash: str
    payload_summary: str
    confirmation_count: int
    threshold: int = 2
    executed: bool = False
    confirmations: dict[str, bool] = field(default_factory=dict)
    proposer: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC).isoformat()
    )


class GnosisSafeMultiSigCoordinatorDriver:
    """Driver managing Gnosis Safe 2-of-3 threshold governance for coordinator operations."""

    def __init__(self, owner_wallets: list[str] | None = None) -> None:
        self.threshold = 2
        self.owner_wallets = owner_wallets or [
            "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",  # Central Bank / Regulator
            "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",  # Trustee Alpha (Bank A)
            "0x3C44CdD160573615659514930278505963E8A155",  # Trustee Beta (Bank B)
        ]
        self._proposals: dict[int, MultiSigProposal] = {}
        self._tx_counter = 0

    def submit_proposal(
        self,
        proposer_wallet: str,
        action_type: GovernanceActionType | str,
        epoch_id: int,
        payload: dict[str, Any] | None = None,
    ) -> MultiSigProposal:
        """Submits a new governance proposal requiring 2-of-3 trustee signatures."""
        if proposer_wallet not in self.owner_wallets:
            raise ValueError(
                f"Proposer wallet '{proposer_wallet}' is not an authorized trustee owner."
            )

        if isinstance(action_type, str):
            action_type = GovernanceActionType(action_type)

        tx_id = self._tx_counter
        self._tx_counter += 1

        payload_str = json.dumps(payload or {}, sort_keys=True)
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
        summary = f"{action_type.value} for Epoch #{epoch_id}"

        confirmations = {owner: (owner == proposer_wallet) for owner in self.owner_wallets}

        proposal = MultiSigProposal(
            tx_id=tx_id,
            action_type=action_type,
            epoch_id=epoch_id,
            payload_hash=payload_hash,
            payload_summary=summary,
            confirmation_count=1,
            executed=False,
            confirmations=confirmations,
            proposer=proposer_wallet,
        )
        self._proposals[tx_id] = proposal

        logger.info(
            "Submitted Gnosis Safe Multi-Sig proposal: tx_id=%d, action=%s, proposer=%s",
            tx_id,
            action_type.value,
            proposer_wallet,
        )
        return proposal

    def confirm_proposal(self, tx_id: int, owner_wallet: str) -> MultiSigProposal:
        """Confirms a pending proposal with a trustee signature. Executes automatically upon 2/3 signatures."""
        if tx_id not in self._proposals:
            raise KeyError(f"Transaction ID #{tx_id} does not exist.")

        if owner_wallet not in self.owner_wallets:
            raise ValueError(
                f"Signer wallet '{owner_wallet}' is not an authorized trustee owner."
            )

        prop = self._proposals[tx_id]
        if prop.executed:
            raise RuntimeError(f"Proposal #{tx_id} has already been executed.")

        if prop.confirmations.get(owner_wallet, False):
            raise ValueError(f"Proposal #{tx_id} has already been confirmed by '{owner_wallet}'.")

        updated_confirmations = dict(prop.confirmations)
        updated_confirmations[owner_wallet] = True
        new_count = sum(1 for v in updated_confirmations.values() if v)
        is_executed = new_count >= self.threshold

        updated_proposal = MultiSigProposal(
            tx_id=prop.tx_id,
            action_type=prop.action_type,
            epoch_id=prop.epoch_id,
            payload_hash=prop.payload_hash,
            payload_summary=prop.payload_summary,
            confirmation_count=new_count,
            threshold=prop.threshold,
            executed=is_executed,
            confirmations=updated_confirmations,
            proposer=prop.proposer,
            created_at=prop.created_at,
        )
        self._proposals[tx_id] = updated_proposal

        logger.info(
            "Confirmed proposal #%d: count=%d/%d, executed=%s, signer=%s",
            tx_id,
            new_count,
            self.threshold,
            is_executed,
            owner_wallet,
        )
        return updated_proposal

    def get_proposal(self, tx_id: int) -> MultiSigProposal | None:
        return self._proposals.get(tx_id)

    def get_all_proposals(self) -> list[MultiSigProposal]:
        return list(self._proposals.values())
