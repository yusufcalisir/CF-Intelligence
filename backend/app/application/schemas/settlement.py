"""Pydantic v2 schemas for Web3 & CBDC Smart Contract Settlement API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel


class SettlementTriggerRequest(BaseModel):
    """Payload to trigger on-chain smart contract incentive settlement."""

    model_config = ConfigDict(extra="forbid")

    epoch_id: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Unique epoch identifier",
    )
    contributions: dict[str, float] = Field(
        ...,
        min_length=1,
        description="Bank ID to LOO Shapley contribution score mapping",
    )
    quarantine_statuses: dict[str, bool] = Field(default_factory=dict)
    audit_proof_hash: str = Field(
        ...,
        min_length=16,
        max_length=128,
        pattern=r"^[a-fA-F0-9]+$",
        description="Keccak-256 or SHA-256 hex audit proof hash",
    )
    total_pool_usd: float = Field(
        default=100000.0,
        gt=0.0,
        le=1_000_000_000.0,
        description="Total USD pool amount (0, 1B]",
    )
    currency: str = Field(
        default="wCBDC",
        max_length=16,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Currency code e.g. wCBDC, USDC, e-TRY",
    )


class OnChainPayoutItem(BaseModel):
    """Calculated on-chain payout allocation for an individual participant bank."""

    model_config = ConfigDict(extra="forbid")

    bank_name: str = Field(..., description="Participant bank name or tenant ID")
    wallet_address: str = Field(..., description="EIP-55 checksummed wallet address")
    shapley_score: float = Field(..., description="Normalized Shapley contribution score")
    shapley_basis_points: int = Field(..., description="Shapley score in basis points")
    share_percent: float = Field(..., description="Proportional pool share percentage")
    payout_usd: float = Field(..., description="Allocated payout amount in USD")
    payout_wei: str = Field(..., description="Allocated amount in token Wei string")
    is_quarantined: bool = Field(..., description="Whether node was quarantined during epoch")
    status: str = Field(..., description="Payout status: DISTRIBUTED or BLOCKED_QUARANTINE")


class SettlementReceiptResponse(BaseModel):
    """On-chain settlement transaction receipt and audit record."""

    model_config = ConfigDict(extra="forbid")

    epoch_id: str = Field(..., description="Settled epoch identifier")
    status: str = Field(..., description="Transaction execution status: SUCCESS")
    transaction_hash: str = Field(..., description="Hex 0x-prefixed transaction hash")
    block_number: int = Field(..., description="EVM block number of mined settlement")
    block_timestamp: str = Field(..., description="ISO 8601 block timestamp")
    contract_address: str = Field(..., description="Deployed settlement smart contract address")
    coordinator_address: str = Field(..., description="Coordinator authority wallet address")
    currency: str = Field(..., description="Settlement currency denomination")
    total_pool_usd: float = Field(..., description="Total deposited pool amount in USD")
    total_distributed_usd: float = Field(..., description="Sum of distributed payouts in USD")
    total_distributed_wei: str = Field(..., description="Total distributed amount in Wei")
    gas_used: int = Field(..., description="Gas units consumed by execution")
    effective_gas_price_gwei: float = Field(..., description="Effective gas price in Gwei")
    audit_proof_hash: str = Field(..., description="Linked immutable audit chain proof hash")
    payouts: list[OnChainPayoutItem] = Field(..., description="Per-participant payout breakdown")


class ContractInfoResponse(BaseModel):
    """Metadata, parameters and ABI for deployed Consortium Settlement Smart Contract."""

    model_config = ConfigDict(extra="forbid")

    contract_address: str = Field(..., description="EVM contract address")
    coordinator_address: str = Field(..., description="Coordinator wallet address")
    network_name: str = Field(..., description="Target EVM network name")
    chain_id: int = Field(..., description="EVM Chain ID")
    current_block_height: int = Field(..., description="Simulated or on-chain block height")
    supported_currencies: list[str] = Field(..., description="Supported token denominations")
    total_settlements_executed: int = Field(..., description="Total settlement receipts logged")
    total_quarantined_nodes: int = Field(..., description="Number of quarantined participant addresses")
    total_slashed_nodes: int = Field(..., description="Number of Byzantine slashed participants")
    abi: list[dict[str, Any]] = Field(..., description="Smart contract JSON ABI")


class PayoutClaimRequest(BaseModel):
    """Claim submission from participant bank to withdraw allocated incentive tokens."""

    model_config = ConfigDict(extra="forbid")

    epoch_id: str = Field(
        ..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$"
    )
    claimant_bank: str = Field(
        ..., min_length=2, max_length=128, pattern=r"^[a-zA-Z0-9_\-\s]+$"
    )
    claimant_wallet: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^0x[a-fA-F0-9]{40}$",
        description="EIP-55 wallet address",
    )
    signature: str | None = Field(default=None, max_length=256)


class PayoutClaimResponse(BaseModel):
    """Receipt confirming successful participant payout claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(..., description="Unique claim confirmation ID")
    epoch_id: str = Field(..., description="Epoch identifier")
    claimant_bank: str = Field(..., description="Participant bank name")
    claimant_wallet: str = Field(..., description="Beneficiary wallet address")
    payout_usd: float = Field(..., description="Claimed amount in USD")
    payout_wei: str = Field(..., description="Claimed amount in Wei")
    currency: str = Field(..., description="Disbursed currency denomination")
    transaction_hash: str = Field(..., description="EVM payout transaction hash")
    block_number: int = Field(..., description="Block height of payout transaction")
    claimed_at: str = Field(..., description="ISO 8601 claim timestamp")
    status: str = Field(default="CLAIMED", description="Claim status: CLAIMED")


class MultiSigProposalItem(BaseModel):
    """Gnosis Safe multi-sig governance proposal details."""

    model_config = ConfigDict(extra="forbid")

    tx_id: int = Field(..., description="Unique multi-sig transaction ID")
    action_type: str = Field(..., description="Governance action enum")
    epoch_id: int = Field(..., description="Associated epoch or round ID")
    payload_hash: str = Field(..., description="Hex SHA-256 hash of proposal payload")
    payload_summary: str = Field(..., description="Human-readable summary")
    confirmation_count: int = Field(..., description="Current trustee confirmation count")
    threshold: int = Field(..., description="Required signatures threshold (e.g. 2 of 3)")
    executed: bool = Field(..., description="Whether transaction has executed on-chain")
    confirmations: dict[str, bool] = Field(..., description="Mapping of trustee address to confirmation status")
    proposer: str = Field(..., description="Proposing trustee wallet address")
    created_at: str = Field(..., description="ISO 8601 creation timestamp")


class MultiSigProposeRequest(BaseModel):
    """Request to initiate a 2-of-3 threshold multi-sig action."""

    model_config = ConfigDict(extra="forbid")

    proposer_wallet: str = Field(
        ...,
        min_length=10,
        max_length=64,
        pattern=r"^0x[a-fA-F0-9]{40}$",
        description="EIP-55 checksummed Ethereum wallet address (0x + 40 hex chars)",
    )
    action_type: str = Field(
        ...,
        max_length=64,
        pattern=r"^[A-Z_]+$",
        description="Action type enum string e.g. QUARANTINE_BANK",
    )
    epoch_id: int = Field(default=0, ge=0, le=1_000_000)
    payload: dict[str, Any] = Field(default_factory=dict)


class MultiSigProposeResponse(BaseModel):
    """Response returned upon creating a multi-sig proposal."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="SUCCESS", description="Operation status")
    tx_id: int = Field(..., description="Created transaction ID")
    executed: bool = Field(..., description="Whether proposal executed immediately")


class MultiSigConfirmRequest(BaseModel):
    """Confirm a pending multi-sig proposal with a trustee signature."""

    model_config = ConfigDict(extra="forbid")

    tx_id: int = Field(..., ge=0, le=1_000_000)
    owner_wallet: str = Field(
        ...,
        min_length=10,
        max_length=64,
        pattern=r"^0x[a-fA-F0-9]{40}$",
        description="EIP-55 checksummed Ethereum wallet address",
    )


class MultiSigConfirmResponse(BaseModel):
    """Response returned upon confirming a multi-sig proposal."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="SUCCESS", description="Operation status")
    tx_id: int = Field(..., description="Transaction ID")
    confirmation_count: int = Field(..., description="Updated confirmation count")
    executed: bool = Field(..., description="Whether threshold was met and executed")


class MultiSigRevokeRequest(BaseModel):
    """Revoke a trustee confirmation for a pending multi-sig proposal."""

    model_config = ConfigDict(extra="forbid")

    tx_id: int = Field(..., ge=0, le=1_000_000)
    owner_wallet: str = Field(
        ...,
        min_length=10,
        max_length=64,
        pattern=r"^0x[a-fA-F0-9]{40}$",
        description="EIP-55 checksummed Ethereum wallet address",
    )


class MultiSigRevokeResponse(BaseModel):
    """Response returned upon revoking a multi-sig confirmation."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="SUCCESS", description="Operation status")
    tx_id: int = Field(..., description="Transaction ID")
    confirmation_count: int = Field(..., description="Updated confirmation count")
    executed: bool = Field(..., description="Execution status")


class QuarantineRequest(BaseModel):
    """Quarantine or clear quarantine for a participant node."""

    model_config = ConfigDict(extra="forbid")

    bank_name_or_wallet: str = Field(..., min_length=2, max_length=128)
    reason: str = Field(default="Adversarial behavior detected", max_length=256)


class QuarantineResponse(BaseModel):
    """Confirmation of node quarantine state update."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="SUCCESS", description="Operation status")
    message: str = Field(..., description="Human-readable result summary")


class SlashRequest(BaseModel):
    """Slash Byzantine malicious node on-chain."""

    model_config = ConfigDict(extra="forbid")

    bank_name_or_wallet: str = Field(..., min_length=2, max_length=128)
    penalty_usd: float = Field(..., gt=0.0, le=10_000_000.0)
    reason: str = Field(default="Byzantine gradient poisoning", max_length=256)


class SlashRecordItem(BaseModel):
    """Audit record of a slashing penalty event."""

    model_config = ConfigDict(extra="forbid")

    bank: str = Field(..., description="Slashed bank name or wallet")
    penalty_usd: float = Field(..., description="Penalty fine in USD")
    reason: str = Field(..., description="Slashing justification")
    timestamp: str = Field(..., description="ISO 8601 slash timestamp")


class SlashResponse(BaseModel):
    """Confirmation of slashing penalty execution."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="SUCCESS", description="Operation status")
    slashed_record: SlashRecordItem = Field(..., description="Recorded penalty event")


class SlashedNodesResponse(RootModel[dict[str, list[SlashRecordItem]]]):
    """Catalog of all Byzantine slashing penalties by participant."""

    root: dict[str, list[SlashRecordItem]] = Field(
        default_factory=dict,
        description="Map of participant to penalty audit logs",
    )
