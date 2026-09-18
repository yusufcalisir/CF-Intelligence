"""Web3 & CBDC Smart Contract Incentive Settlement API Endpoints."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Path, status

from app.application.schemas.settlement import (
    ContractInfoResponse,
    MultiSigConfirmRequest,
    MultiSigConfirmResponse,
    MultiSigProposalItem,
    MultiSigProposeRequest,
    MultiSigProposeResponse,
    MultiSigRevokeRequest,
    MultiSigRevokeResponse,
    OnChainPayoutItem,
    PayoutClaimRequest,
    PayoutClaimResponse,
    QuarantineRequest,
    QuarantineResponse,
    SettlementReceiptResponse,
    SettlementTriggerRequest,
    SlashedNodesResponse,
    SlashRecordItem,
    SlashRequest,
    SlashResponse,
)
from app.infrastructure.security.smart_contract_driver import (
    EpochAlreadySettledError,
    InvalidSettlementParameterError,
    SmartContractSettlementDriver,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/settlement", tags=["settlement"])
api_router = APIRouter(prefix="/v1/settlement", tags=["settlement"])

# In-memory participant claim tracker to prevent double-claiming
_CLAIMED_PAYOUTS: set[str] = set()


def _format_receipt(receipt: dict[str, Any]) -> SettlementReceiptResponse:
    payouts = [
        OnChainPayoutItem(
            bank_name=p["bank_name"],
            wallet_address=p["wallet_address"],
            shapley_score=p["shapley_score"],
            shapley_basis_points=p["shapley_basis_points"],
            share_percent=p["share_percent"],
            payout_usd=p["payout_usd"],
            payout_wei=str(p["payout_wei"]),
            is_quarantined=p["is_quarantined"],
            status=p["status"],
        )
        for p in receipt.get("payouts", [])
    ]
    return SettlementReceiptResponse(
        epoch_id=receipt["epoch_id"],
        status=receipt.get("status", "SUCCESS"),
        transaction_hash=receipt["transaction_hash"],
        block_number=receipt["block_number"],
        block_timestamp=receipt["block_timestamp"],
        contract_address=receipt["contract_address"],
        coordinator_address=receipt["coordinator_address"],
        currency=receipt.get("currency", "wCBDC"),
        total_pool_usd=receipt["total_pool_usd"],
        total_distributed_usd=receipt["total_distributed_usd"],
        total_distributed_wei=str(receipt["total_distributed_wei"]),
        gas_used=receipt.get("gas_used", 142850),
        effective_gas_price_gwei=float(receipt.get("effective_gas_price_gwei", 15.5)),
        audit_proof_hash=receipt["audit_proof_hash"],
        mode=receipt.get("mode", "SIMULATOR_FALLBACK"),
        audit_chain_verified=receipt.get("audit_chain_verified", True),
        payouts=payouts,
    )


# ── Contract & History Endpoints ──────────────────────────────


async def get_contract_info() -> ContractInfoResponse:
    """Returns metadata and ABI for deployed Consortium Incentive Settlement Smart Contract."""
    driver = SmartContractSettlementDriver.get_instance()
    info = driver.get_contract_info()
    return ContractInfoResponse(
        contract_address=info["contract_address"],
        coordinator_address=info["coordinator_address"],
        network_name=info["network_name"],
        chain_id=info["chain_id"],
        current_block_height=info["current_block_height"],
        supported_currencies=info["supported_currencies"],
        total_settlements_executed=info["total_settlements_executed"],
        total_quarantined_nodes=info["total_quarantined_nodes"],
        total_slashed_nodes=info["total_slashed_nodes"],
        mode=info.get("mode", "SIMULATOR_FALLBACK"),
        is_live_rpc=info.get("is_live_rpc", False),
        rpc_provider_url=info.get("rpc_provider_url"),
        abi=info["abi"],
    )


async def get_settlement_history() -> list[SettlementReceiptResponse]:
    """Returns log of executed on-chain Web3 / CBDC settlement receipts."""
    driver = SmartContractSettlementDriver.get_instance()
    history = driver.get_settlement_history()
    return [_format_receipt(r) for r in history]


async def trigger_settlement(payload: SettlementTriggerRequest) -> SettlementReceiptResponse:
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
        return _format_receipt(receipt)
    except EpochAlreadySettledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidSettlementParameterError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Settlement execution failed: {exc}",
        ) from exc


# ── Participant Payout Claims ─────────────────────────────────


async def claim_participant_payout(req: PayoutClaimRequest) -> PayoutClaimResponse:
    """Participant bank withdrawal claim against settled smart contract funds."""
    driver = SmartContractSettlementDriver.get_instance()
    matching_epoch = next(
        (r for r in driver.settlement_history if r.get("epoch_id") == req.epoch_id), None
    )
    if not matching_epoch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Settlement epoch '{req.epoch_id}' not found.",
        )

    norm_claimant = req.claimant_bank.strip().lower()
    payout_record = next(
        (
            p
            for p in matching_epoch.get("payouts", [])
            if p.get("bank_name", "").strip().lower() == norm_claimant
            or (req.claimant_wallet and p.get("wallet_address", "").lower() == req.claimant_wallet.lower())
        ),
        None,
    )

    if not payout_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bank '{req.claimant_bank}' has no allocated incentive payouts in epoch '{req.epoch_id}'.",
        )

    if payout_record.get("is_quarantined") or payout_record.get("status") == "BLOCKED_QUARANTINE":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Payout for bank '{req.claimant_bank}' is blocked: participant is quarantined on-chain.",
        )

    claim_key = f"{req.epoch_id}:{norm_claimant}"
    if claim_key in _CLAIMED_PAYOUTS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Incentive payout for epoch '{req.epoch_id}' and bank '{req.claimant_bank}' has already been claimed.",
        )

    _CLAIMED_PAYOUTS.add(claim_key)
    claim_id = f"claim_{uuid.uuid4().hex[:12]}"
    tx_hash = f"0x{hashlib.sha256(f'{claim_id}:{req.epoch_id}:{payout_record['wallet_address']}'.encode()).hexdigest()}"

    return PayoutClaimResponse(
        claim_id=claim_id,
        epoch_id=req.epoch_id,
        claimant_bank=payout_record["bank_name"],
        claimant_wallet=payout_record["wallet_address"],
        payout_usd=payout_record["payout_usd"],
        payout_wei=str(payout_record["payout_wei"]),
        currency=matching_epoch.get("currency", "wCBDC"),
        transaction_hash=tx_hash,
        block_number=matching_epoch.get("block_number", 5000001) + 1,
        claimed_at=datetime.now(UTC).isoformat(),
        status="CLAIMED",
    )


# ── Gnosis Safe Multi-Sig Governance ──────────────────────────


async def get_multisig_proposals() -> list[MultiSigProposalItem]:
    """Returns list of active Gnosis Safe 2-of-3 multi-sig coordinator proposals."""
    driver = SmartContractSettlementDriver.get_instance()
    props = driver.multisig_driver.get_all_proposals()
    return [
        MultiSigProposalItem(
            tx_id=p.tx_id,
            action_type=p.action_type.value if hasattr(p.action_type, "value") else str(p.action_type),
            epoch_id=p.epoch_id,
            payload_hash=p.payload_hash,
            payload_summary=p.payload_summary,
            confirmation_count=p.confirmation_count,
            threshold=p.threshold,
            executed=p.executed,
            confirmations=p.confirmations,
            proposer=p.proposer,
            created_at=p.created_at,
        )
        for p in props
    ]


async def get_multisig_proposal_by_id(
    tx_id: int = Path(..., ge=0, le=1_000_000, description="Transaction ID"),
) -> MultiSigProposalItem:
    """Returns details for a single Gnosis Safe proposal."""
    driver = SmartContractSettlementDriver.get_instance()
    prop = driver.multisig_driver.get_proposal(tx_id)
    if prop is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal #{tx_id} does not exist.",
        )
    return MultiSigProposalItem(
        tx_id=prop.tx_id,
        action_type=prop.action_type.value if hasattr(prop.action_type, "value") else str(prop.action_type),
        epoch_id=prop.epoch_id,
        payload_hash=prop.payload_hash,
        payload_summary=prop.payload_summary,
        confirmation_count=prop.confirmation_count,
        threshold=prop.threshold,
        executed=prop.executed,
        confirmations=prop.confirmations,
        proposer=prop.proposer,
        created_at=prop.created_at,
    )


async def propose_multisig_action(req: MultiSigProposeRequest) -> MultiSigProposeResponse:
    """Submits a new 2-of-3 threshold multi-sig proposal for coordinator governance."""
    try:
        driver = SmartContractSettlementDriver.get_instance()
        prop = driver.multisig_driver.submit_proposal(
            proposer_wallet=req.proposer_wallet,
            action_type=req.action_type,
            epoch_id=req.epoch_id,
            payload=req.payload,
        )
        return MultiSigProposeResponse(status="SUCCESS", tx_id=prop.tx_id, executed=prop.executed)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


async def confirm_multisig_action(req: MultiSigConfirmRequest) -> MultiSigConfirmResponse:
    """Confirms a pending multi-sig proposal with a trustee signature."""
    try:
        driver = SmartContractSettlementDriver.get_instance()
        prop = driver.multisig_driver.confirm_proposal(
            tx_id=req.tx_id, owner_wallet=req.owner_wallet
        )
        return MultiSigConfirmResponse(
            status="SUCCESS",
            tx_id=prop.tx_id,
            confirmation_count=prop.confirmation_count,
            executed=prop.executed,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


async def revoke_multisig_action(req: MultiSigRevokeRequest) -> MultiSigRevokeResponse:
    """Revokes a trustee confirmation for a pending multi-sig proposal."""
    try:
        driver = SmartContractSettlementDriver.get_instance()
        prop = driver.multisig_driver.revoke_confirmation(
            tx_id=req.tx_id, owner_wallet=req.owner_wallet
        )
        return MultiSigRevokeResponse(
            status="SUCCESS",
            tx_id=prop.tx_id,
            confirmation_count=prop.confirmation_count,
            executed=prop.executed,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


# ── Quarantine & Slashing Controls ────────────────────────────


async def quarantine_bank_node(req: QuarantineRequest) -> QuarantineResponse:
    """Quarantines a participant node on-chain."""
    driver = SmartContractSettlementDriver.get_instance()
    driver.quarantine_bank(req.bank_name_or_wallet, req.reason)
    return QuarantineResponse(
        status="SUCCESS",
        message=f"Bank node '{req.bank_name_or_wallet}' quarantined on-chain.",
    )


async def clear_bank_quarantine_node(req: QuarantineRequest) -> QuarantineResponse:
    """Clears quarantine status for a participant node on-chain."""
    driver = SmartContractSettlementDriver.get_instance()
    driver.clear_quarantine(req.bank_name_or_wallet)
    return QuarantineResponse(
        status="SUCCESS",
        message=f"Quarantine cleared for '{req.bank_name_or_wallet}'.",
    )


async def slash_bank_node(req: SlashRequest) -> SlashResponse:
    """Slashes a Byzantine malicious node on-chain."""
    try:
        driver = SmartContractSettlementDriver.get_instance()
        record = driver.slash_bank(req.bank_name_or_wallet, req.penalty_usd, req.reason)
        return SlashResponse(
            status="SUCCESS",
            slashed_record=SlashRecordItem(
                bank=record["bank"],
                penalty_usd=record["penalty_usd"],
                reason=record["reason"],
                timestamp=record["timestamp"],
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def get_slashed_nodes() -> SlashedNodesResponse:
    """Returns all recorded Byzantine slashing events."""
    driver = SmartContractSettlementDriver.get_instance()
    raw = driver.get_slashed_penalties()
    formatted = {
        bank: [
            SlashRecordItem(
                bank=r["bank"],
                penalty_usd=r["penalty_usd"],
                reason=r["reason"],
                timestamp=r["timestamp"],
            )
            for r in recs
        ]
        for bank, recs in raw.items()
    }
    return SlashedNodesResponse(root=formatted)


# ── Route Binding to Router Variants ──────────────────────────

for r in (router, api_router):
    r.add_api_route("/contract-info", get_contract_info, methods=["GET"], response_model=ContractInfoResponse)
    r.add_api_route("/history", get_settlement_history, methods=["GET"], response_model=list[SettlementReceiptResponse])
    r.add_api_route("/epochs", get_settlement_history, methods=["GET"], response_model=list[SettlementReceiptResponse])
    r.add_api_route("/trigger", trigger_settlement, methods=["POST"], response_model=SettlementReceiptResponse)
    r.add_api_route("/claim", claim_participant_payout, methods=["POST"], response_model=PayoutClaimResponse)
    r.add_api_route("/multisig/proposals", get_multisig_proposals, methods=["GET"], response_model=list[MultiSigProposalItem])
    r.add_api_route("/multisig/proposals/{tx_id}", get_multisig_proposal_by_id, methods=["GET"], response_model=MultiSigProposalItem)
    r.add_api_route("/multisig/propose", propose_multisig_action, methods=["POST"], response_model=MultiSigProposeResponse)
    r.add_api_route("/multisig/confirm", confirm_multisig_action, methods=["POST"], response_model=MultiSigConfirmResponse)
    r.add_api_route("/multisig/revoke", revoke_multisig_action, methods=["POST"], response_model=MultiSigRevokeResponse)
    r.add_api_route("/quarantine", quarantine_bank_node, methods=["POST"], response_model=QuarantineResponse)
    r.add_api_route("/clear-quarantine", clear_bank_quarantine_node, methods=["POST"], response_model=QuarantineResponse)
    r.add_api_route("/slash", slash_bank_node, methods=["POST"], response_model=SlashResponse)
    r.add_api_route("/slashed", get_slashed_nodes, methods=["GET"], response_model=SlashedNodesResponse)
