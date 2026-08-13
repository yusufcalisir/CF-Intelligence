"""Web3 & CBDC Smart Contract Incentive Settlement API Endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.infrastructure.security.smart_contract_driver import (
    SmartContractSettlementDriver,
)

router = APIRouter(prefix="/api/v1/settlement", tags=["settlement"])


class SettlementTriggerRequest(BaseModel):
    epoch_id: str
    contributions: dict[str, float]
    quarantine_statuses: dict[str, bool] = Field(default_factory=dict)
    audit_proof_hash: str
    total_pool_usd: float = 100000.0
    currency: str = "wCBDC"


@router.get("/contract-info")
async def get_contract_info() -> dict[str, Any]:
    """Returns metadata and ABI for the deployed Consortium Incentive Settlement Smart Contract."""
    driver = SmartContractSettlementDriver.get_instance()
    return driver.get_contract_info()


@router.get("/history")
async def get_settlement_history() -> list[dict[str, Any]]:
    """Returns the log of executed on-chain Web3 / CBDC settlement receipts."""
    driver = SmartContractSettlementDriver.get_instance()
    return driver.get_settlement_history()


@router.post("/trigger")
async def trigger_settlement(payload: SettlementTriggerRequest) -> dict[str, Any]:
    """Manually triggers smart contract incentive settlement for a simulation epoch."""
    try:
        driver = SmartContractSettlementDriver.get_instance()
        receipt = driver.settle_incentives(
            epoch_id=payload.epoch_id,
            contributions=payload.contributions,
            quarantine_statuses=payload.quarantine_statuses,
            audit_proof_hash=payload.audit_proof_hash,
            total_pool_usd=payload.total_pool_usd,
            currency=payload.currency,
        )
        return receipt
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Settlement execution failed: {exc}") from exc


class MultiSigProposeRequest(BaseModel):
    proposer_wallet: str
    action_type: str
    epoch_id: int
    payload: dict[str, Any] = Field(default_factory=dict)


class MultiSigConfirmRequest(BaseModel):
    tx_id: int
    owner_wallet: str


@router.get("/multisig/proposals")
async def get_multisig_proposals() -> list[dict[str, Any]]:
    """Returns list of active Gnosis Safe 2-of-3 multi-sig coordinator proposals."""
    driver = SmartContractSettlementDriver.get_instance()
    props = driver.multisig_driver.get_all_proposals()
    return [
        {
            "tx_id": p.tx_id,
            "action_type": p.action_type,
            "epoch_id": p.epoch_id,
            "payload_hash": p.payload_hash,
            "payload_summary": p.payload_summary,
            "confirmation_count": p.confirmation_count,
            "threshold": p.threshold,
            "executed": p.executed,
            "confirmations": p.confirmations,
            "proposer": p.proposer,
            "created_at": p.created_at,
        }
        for p in props
    ]


@router.post("/multisig/propose")
async def propose_multisig_action(req: MultiSigProposeRequest) -> dict[str, Any]:
    """Submits a new 2-of-3 threshold multi-sig proposal for coordinator governance."""
    try:
        driver = SmartContractSettlementDriver.get_instance()
        prop = driver.multisig_driver.submit_proposal(
            proposer_wallet=req.proposer_wallet,
            action_type=req.action_type,
            epoch_id=req.epoch_id,
            payload=req.payload,
        )
        return {"status": "SUCCESS", "tx_id": prop.tx_id, "executed": prop.executed}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/multisig/confirm")
async def confirm_multisig_action(req: MultiSigConfirmRequest) -> dict[str, Any]:
    """Confirms a pending multi-sig proposal with a trustee signature."""
    try:
        driver = SmartContractSettlementDriver.get_instance()
        prop = driver.multisig_driver.confirm_proposal(
            tx_id=req.tx_id, owner_wallet=req.owner_wallet
        )
        return {
            "status": "SUCCESS",
            "tx_id": prop.tx_id,
            "confirmation_count": prop.confirmation_count,
            "executed": prop.executed,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
