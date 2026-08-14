"""Domain value objects for Cross-Chain Inter-Bank Settlement & Layer-2 Liquidity Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SettlementNetwork(StrEnum):
    """Supported distributed ledgers and Layer-2 rollup networks for inter-bank settlement."""

    ARBITRUM_ONE = "ArbitrumOne"
    OPTIMISM_MAINNET = "OptimismMainnet"
    BASE_SEPOLIA = "BaseSepolia"
    CANTON_NETWORK = "CantonNetwork"
    HYPERLEDGER_FABRIC = "HyperledgerFabric"
    ETHEREUM_SEPOLIA = "EthereumSepolia"


class CrossChainBridgeProtocol(StrEnum):
    """Cross-chain messaging and interoperability protocols."""

    CHAINLINK_CCIP = "ChainlinkCCIP"
    LAYERZERO_V2 = "LayerZeroV2"
    CANTON_INTEROP = "CantonInteroperability"
    FABRIC_INTEROP = "FabricChaincodeGateway"


@dataclass(frozen=True)
class CBDCDepositWallet:
    """Enterprise wallet or smart contract endpoint on a specific settlement ledger."""

    bank_id: str
    network: SettlementNetwork
    wallet_address: str  # 0x... or Canton Party ID / Fabric MSP ID
    token_symbol: str  # e.g., 'wCBDC', 'EUR-Deposit', 'USD-Institutional'


@dataclass(frozen=True)
class CrossChainRouteReceipt:
    """Transaction and message delivery receipt for an individual bank's cross-chain payout."""

    bank_id: str
    network: SettlementNetwork
    protocol: CrossChainBridgeProtocol
    token_symbol: str
    amount: float
    shapley_share_pct: float
    destination_recipient: str
    message_id: str  # CCIP Message ID or L2 Tx Hash
    gas_fee_usd: float
    status: str  # 'FINALIZED', 'RELAYED', 'DELIVERED'


@dataclass(frozen=True)
class CrossChainDisbursementResult:
    """Consolidated result for an entire epoch's cross-chain multi-ledger settlement."""

    epoch_id: int
    pool_currency: str
    total_pool_amount: float
    total_gas_fees_usd: float
    routes: list[CrossChainRouteReceipt]
    execution_time_ms: float
    bridge_audit_hash: str
    is_fully_finalized: bool
    audit_events: list[dict[str, Any]]
