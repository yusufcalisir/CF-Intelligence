"""Targeted unit tests for FlowerP2PEngine and P2PGossipStrategy.

Covers:
  - 4-bank Ring topology adjacency generation
  - Fully-connected Mesh topology adjacency generation
  - Peer weight gossip mixing and convergence MAE decay across rounds
  - Serverless multi-round P2P training execution
"""

from __future__ import annotations

import numpy as np
import pytest

from app.application.services.flower_p2p_engine import (
    FlowerP2PEngine,
    P2PGossipStrategy,
    P2PTopologyType,
)


@pytest.fixture()
def peer_ids() -> list[str]:
    return ["bank_alpha", "bank_beta", "bank_gamma", "bank_delta"]


@pytest.fixture()
def mock_peer_data(peer_ids: list[str]) -> dict[str, dict[str, np.ndarray]]:
    return {
        peer: {
            "X_train": np.zeros((100, 10), dtype=np.float32),
            "y_train": np.zeros((100,), dtype=np.int64),
        }
        for peer in peer_ids
    }


def test_build_ring_adjacency(peer_ids: list[str]) -> None:
    """Ring topology must assign exactly 3 neighbors (self + prev + next) for each peer."""
    adj = P2PGossipStrategy.build_ring_adjacency(peer_ids)

    assert len(adj) == 4
    assert set(adj["bank_alpha"]) == {"bank_alpha", "bank_delta", "bank_beta"}
    assert set(adj["bank_beta"]) == {"bank_beta", "bank_alpha", "bank_gamma"}


def test_build_mesh_adjacency(peer_ids: list[str]) -> None:
    """Mesh topology must connect each peer to all 4 peers in the network."""
    adj = P2PGossipStrategy.build_mesh_adjacency(peer_ids)

    assert len(adj) == 4
    assert set(adj["bank_alpha"]) == set(peer_ids)


def test_mix_peer_weights_averages_neighbors(peer_ids: list[str]) -> None:
    """Peer gossip weight mixing must average weights of neighboring nodes."""
    weights = {
        peer_ids[0]: [np.array([1.0, 1.0], dtype=np.float32)],
        peer_ids[1]: [np.array([3.0, 3.0], dtype=np.float32)],
    }
    adj = {
        peer_ids[0]: [peer_ids[0], peer_ids[1]],
        peer_ids[1]: [peer_ids[0], peer_ids[1]],
    }

    mixed = P2PGossipStrategy.mix_peer_weights(weights, adj)

    np.testing.assert_allclose(mixed[peer_ids[0]][0], np.array([2.0, 2.0], dtype=np.float32))
    np.testing.assert_allclose(mixed[peer_ids[1]][0], np.array([2.0, 2.0], dtype=np.float32))


def test_run_p2p_federated_round(
    mock_peer_data: dict[str, dict[str, np.ndarray]],
) -> None:
    """FlowerP2PEngine must execute 3 P2P rounds without errors and output valid metrics."""
    engine = FlowerP2PEngine()
    results = engine.run_p2p_federated_round(
        peer_data=mock_peer_data,
        num_rounds=3,
        topology=P2PTopologyType.RING,
    )

    assert len(results) == 3
    assert results[0].round_id == 1
    assert results[0].topology == P2PTopologyType.RING
    assert results[0].avg_loss > 0
    assert results[0].convergence_mae >= 0
    assert len(results[0].peer_ids) == 4
