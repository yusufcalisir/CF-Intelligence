"""Unit tests for Cross-Chain Inter-Bank Settlement & Layer-2 Liquidity Bridge Driver."""

from __future__ import annotations

import unittest

from app.domain.value_objects_bridge import (
    CrossChainBridgeProtocol,
    SettlementNetwork,
)
from app.infrastructure.security.layer2_crosschain_bridge import Layer2CrossChainBridgeDriver


class TestLayer2CrossChainBridgeDriver(unittest.TestCase):
    """Test suite verifying Chainlink CCIP, LayerZero, and multi-ledger settlement routing."""

    def setUp(self) -> None:
        self.driver = Layer2CrossChainBridgeDriver()

    def test_multi_ledger_shapley_disbursement(self) -> None:
        """Assert Shapley allocations are correctly disbursed across diverse member bank ledgers."""
        allocations = {
            "bank_alpha": 0.40,
            "bank_beta": 0.30,
            "bank_gamma": 0.20,
            "bank_delta": 0.10,
        }
        res = self.driver.disburse_crosschain_incentives(
            epoch_id=42,
            allocations=allocations,
            pool_amount=100_000.0,
            currency="wCBDC",
        )

        self.assertEqual(res.epoch_id, 42)
        self.assertEqual(res.total_pool_amount, 100_000.0)
        self.assertEqual(len(res.routes), 4)
        self.assertTrue(res.is_fully_finalized)
        self.assertIsNotNone(res.bridge_audit_hash)

        # Verify individual bank routes
        route_map = {r.bank_id: r for r in res.routes}

        # Bank Alpha on Arbitrum One
        self.assertEqual(route_map["bank_alpha"].network, SettlementNetwork.ARBITRUM_ONE)
        self.assertEqual(route_map["bank_alpha"].protocol, CrossChainBridgeProtocol.CHAINLINK_CCIP)
        self.assertAlmostEqual(route_map["bank_alpha"].amount, 40_000.0, places=2)

        # Bank Beta on Optimism Mainnet
        self.assertEqual(route_map["bank_beta"].network, SettlementNetwork.OPTIMISM_MAINNET)
        self.assertAlmostEqual(route_map["bank_beta"].amount, 30_000.0, places=2)

        # Bank Gamma on Canton Network
        self.assertEqual(route_map["bank_gamma"].network, SettlementNetwork.CANTON_NETWORK)
        self.assertEqual(route_map["bank_gamma"].protocol, CrossChainBridgeProtocol.CANTON_INTEROP)
        self.assertAlmostEqual(route_map["bank_gamma"].amount, 20_000.0, places=2)

        # Bank Delta on Hyperledger Fabric
        self.assertEqual(route_map["bank_delta"].network, SettlementNetwork.HYPERLEDGER_FABRIC)
        self.assertEqual(route_map["bank_delta"].protocol, CrossChainBridgeProtocol.FABRIC_INTEROP)
        self.assertAlmostEqual(route_map["bank_delta"].amount, 10_000.0, places=2)

    def test_ccip_message_formatting(self) -> None:
        """Assert Chainlink CCIP EVM2AnyMessage payload formatting."""
        msg = self.driver.format_ccip_message(
            target_network=SettlementNetwork.ARBITRUM_ONE,
            recipient="0x1234567890123456789012345678901234567890",
            amount=5000.0,
            token_symbol="wCBDC",
        )

        self.assertEqual(msg["receiver"], "0x1234567890123456789012345678901234567890")
        self.assertEqual(msg["tokenAmounts"][0]["token"], "wCBDC")
        self.assertTrue(msg["ccip_message_id"].startswith("0x"))
        self.assertEqual(len(msg["ccip_message_id"]), 66)

    def test_network_telemetry_metrics(self) -> None:
        """Assert supported settlement network metrics and SLA parameters."""
        metrics = self.driver.get_network_metrics()

        self.assertGreaterEqual(len(metrics), 5)
        networks = [m["network"] for m in metrics]
        self.assertIn("ArbitrumOne", networks)
        self.assertIn("OptimismMainnet", networks)
        self.assertIn("CantonNetwork", networks)
        self.assertIn("HyperledgerFabric", networks)


if __name__ == "__main__":
    unittest.main()
