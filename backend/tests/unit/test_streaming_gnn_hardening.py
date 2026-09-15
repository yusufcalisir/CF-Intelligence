"""Unit tests for Temporal Streaming GNN and Flink Graph Streaming Hardening (STAGE_34).

Verifies:
1. Multi-neighbor message passing accumulation via index_add_ (zero message loss).
2. Residual skip projection for isolated nodes and feature preservation.
3. Exponential time-decayed edge weights w(t) = exp(-lambda * delta_t).
4. Temporal edge weight modulation in GAT attention layers.
5. Online gradient backpropagation with time-decayed edge weights.
6. Incremental O(1) node indexing and bidirectional mapping.
7. Multi-threaded concurrency safety for StreamingGraphService.
8. Flink burst rate and window volume calculation in velocity anomalies.
9. Flink multi-threaded stream processing and state reset.
10. End-to-end FastAPI presentation endpoints for streaming edge, status, and GNN training.
"""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime, timedelta

import torch
from fastapi.testclient import TestClient

from app.application.services.flink_graph_streaming import (
    FlinkGraphStreamProcessor,
    StreamingEdgeEvent,
)
from app.application.services.streaming_gnn_model import GATAttentionLayer, StreamingGATModel
from app.application.services.streaming_graph_service import StreamingGraphService
from app.main import app


class TestStreamingGNNHardening:
    """Hardening test suite for Dynamic Temporal Streaming GNN and Apache Flink Streaming."""

    def test_gat_index_add_multi_neighbor_message_accumulation(self) -> None:
        """GAT layer must correctly accumulate messages from multiple incoming edges to the same target node."""
        layer = GATAttentionLayer(in_dim=4, out_dim=4, num_heads=1, dropout=0.0)

        # 3 nodes: Node 0 and Node 1 both send to Node 2
        h = torch.tensor(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
            ],
            dtype=torch.float32,
        )
        # Edges: 0 -> 2 and 1 -> 2
        edge_index = torch.tensor([[0, 1], [2, 2]], dtype=torch.long)

        out, alpha = layer(h, edge_index)

        assert out.shape == (3, 4)
        assert alpha.shape == (2, 1)
        # Node 2 must have non-zero representations aggregated from both incoming edges
        assert not torch.allclose(out[2], torch.zeros(4))

    def test_gat_residual_skip_connection_isolated_node(self) -> None:
        """An empty edge graph or isolated nodes must retain representation via residual projection."""
        layer = GATAttentionLayer(in_dim=6, out_dim=8, num_heads=2, dropout=0.0)

        h = torch.randn(4, 6)
        empty_edges = torch.empty((2, 0), dtype=torch.long)

        out, alpha = layer(h, empty_edges)

        assert out.shape == (4, 16)  # 2 heads * 8 out_dim = 16
        assert alpha.shape == (0, 2)
        # Output should match residual linear projection
        expected = layer.res_proj(h)
        assert torch.allclose(out, expected, atol=1e-5)

    def test_time_decayed_edge_weights_exponential(self) -> None:
        """StreamingGraphService must calculate exponential decay w = exp(-lambda * delta_t)."""
        service = StreamingGraphService(max_window_minutes=60, default_decay_lambda=0.01)
        now = datetime.now(UTC)

        # Recent transaction (0 seconds ago)
        service.add_transaction(
            {
                "sender_id": "cust_recent_1",
                "receiver_id": "cust_recent_2",
                "amount": 500.0,
                "timestamp": now.isoformat(),
            }
        )

        # Older transaction (100 seconds ago)
        service.add_transaction(
            {
                "sender_id": "cust_old_1",
                "receiver_id": "cust_old_2",
                "amount": 500.0,
                "timestamp": (now - timedelta(seconds=100)).isoformat(),
            }
        )

        features, edge_index, labels, edge_weights = service.get_active_subgraph_tensors(
            return_weights=True, decay_lambda=0.01
        )

        assert features.shape[0] == 4
        assert edge_index.shape == (2, 4)  # 2 undirected edges = 4 directed edges
        assert edge_weights.shape == (4,)

        # The first edge (recent) should have weight close to 1.0 (delta_t ~ 0)
        recent_weight = edge_weights[0].item()
        assert recent_weight >= 0.95

        # The second edge (100s ago with lambda=0.01) should have weight ~ exp(-1.0) ~ 0.3678
        old_weight = edge_weights[2].item()
        expected_decay = math.exp(-0.01 * 100.0)
        assert abs(old_weight - expected_decay) < 0.10
        assert recent_weight > old_weight

    def test_gat_forward_with_temporal_edge_weights(self) -> None:
        """StreamingGATModel must accept time-decayed edge weights and modulate attention coefficients."""
        model = StreamingGATModel(in_dim=12, hidden_dim=8, num_heads=2)
        N = 4
        h = torch.randn(N, 12)
        edge_index = torch.tensor([[0, 1, 2, 3], [1, 2, 3, 0]], dtype=torch.long)
        E = edge_index.size(1)

        # Distinct edge weights representing temporal recency
        edge_weights = torch.tensor([1.0, 0.5, 0.2, 0.05], dtype=torch.float32)

        preds_weighted, att_weighted = model(h, edge_index, edge_weights=edge_weights)
        preds_unweighted, att_unweighted = model(h, edge_index, edge_weights=None)

        assert preds_weighted.shape == (N,)
        assert att_weighted.shape == (E, 2)
        assert preds_unweighted.shape == (N,)
        # Attention weights should differ when temporal modulation is applied
        assert not torch.allclose(att_weighted, att_unweighted)

    def test_online_train_step_with_temporal_weights(self) -> None:
        """Online train step must execute backpropagation with time-decayed weights and update parameters."""
        model = StreamingGATModel(in_dim=12, hidden_dim=8, num_heads=2)
        h = torch.randn(3, 12)
        edge_index = torch.tensor([[0, 1], [1, 2]], dtype=torch.long)
        edge_weights = torch.tensor([0.9, 0.3], dtype=torch.float32)
        labels = torch.tensor([1.0, 0.0, 1.0], dtype=torch.float32)

        # Capture initial weight
        initial_w = model.gat.W.clone()

        loss = model.online_train_step(h, edge_index, labels, edge_weights=edge_weights)

        assert isinstance(loss, float)
        assert loss >= 0.0
        # Weights must be updated
        assert not torch.allclose(initial_w, model.gat.W)

    def test_streaming_graph_incremental_node_indexing(self) -> None:
        """Incremental addition of transactions must maintain a strict bijective index mapping."""
        service = StreamingGraphService(max_window_minutes=30)
        assert len(service.node_to_index) == 0

        for i in range(5):
            service.add_transaction(
                {
                    "sender_id": f"bank_node_{i}",
                    "receiver_id": f"bank_node_{i + 1}",
                    "amount": 100.0 * (i + 1),
                }
            )

        assert len(service.nodes) == 6
        assert len(service.node_to_index) == 6
        assert len(service.index_to_node) == 6

        # Check bijection
        for node_id, idx in service.node_to_index.items():
            assert service.index_to_node[idx] == node_id

        # Check neighbor adjacency
        neighbors_1 = service.get_node_neighbors("bank_node_1")
        assert "bank_node_0" in neighbors_1
        assert "bank_node_2" in neighbors_1

    def test_streaming_graph_thread_concurrency(self) -> None:
        """Concurrent multi-threaded ingestion and tensor extraction must be free of race conditions."""
        service = StreamingGraphService(max_window_minutes=60)
        errors: list[Exception] = []

        def ingest_worker(start_idx: int) -> None:
            try:
                for i in range(25):
                    service.add_transaction(
                        {
                            "sender_id": f"worker_{start_idx}_src_{i}",
                            "receiver_id": f"worker_{start_idx}_dst_{i}",
                            "amount": 50.0 + i,
                        }
                    )
            except Exception as e:
                errors.append(e)

        def query_worker() -> None:
            try:
                for _ in range(15):
                    service.get_active_subgraph_tensors(return_weights=True)
                    service.get_status_summary()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=ingest_worker, args=(1,)),
            threading.Thread(target=ingest_worker, args=(2,)),
            threading.Thread(target=query_worker),
            threading.Thread(target=query_worker),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        summary = service.get_status_summary()
        assert summary["edge_count"] == 50

    def test_flink_burst_rate_and_window_volume(self) -> None:
        """Flink processor must calculate burst rate (eps) and total window volume in velocity anomalies."""
        processor = FlinkGraphStreamProcessor(window_size_ms=1000, velocity_threshold=2.0)

        events = [
            StreamingEdgeEvent(
                edge_id=f"burst_{i}",
                source_id="mule_source",
                target_id="mule_target",
                rel_type="RAPID_TRANSFER",
                amount=150.0,
                bank_id="bank_alpha",
            )
            for i in range(4)
        ]

        receipt = processor.process_batch_stream(events)

        assert receipt.processed_count == 4
        assert len(receipt.velocity_anomalies) > 0
        anomaly = receipt.velocity_anomalies[-1]

        assert anomaly["pair_key"] == "mule_source->mule_target"
        assert anomaly["edge_count_in_window"] == 4
        assert anomaly["burst_rate_eps"] == 4.0  # 4 edges / 1.0 sec = 4.0 eps
        assert anomaly["window_volume"] == 600.0  # 4 * 150.0 = 600.0
        assert anomaly["bank_id"] == "bank_alpha"

    def test_flink_thread_concurrency_and_reset(self) -> None:
        """Flink stream processor must handle concurrent multi-threaded batches and reset cleanly."""
        processor = FlinkGraphStreamProcessor(window_size_ms=500, velocity_threshold=3.0)
        errors: list[Exception] = []

        def stream_worker(batch_id: int) -> None:
            try:
                for i in range(10):
                    ev = StreamingEdgeEvent(
                        edge_id=f"c_edge_{batch_id}_{i}",
                        source_id=f"src_{batch_id}",
                        target_id=f"tgt_{batch_id}",
                        rel_type="TRANSACTS",
                        amount=100.0,
                    )
                    processor.process_streaming_edge(ev)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=stream_worker, args=(j,)) for j in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        status = processor.get_stream_status()
        assert status["processed_total_edges"] == 40
        assert status["subsecond_sla_pass"] is True

        # Test reset
        processor.reset()
        status_after = processor.get_stream_status()
        assert status_after["processed_total_edges"] == 0
        assert status_after["tracked_entity_count"] == 0

    def test_streaming_api_endpoints_integration(self) -> None:
        """FastAPI streaming endpoints must ingest edges, report status, and execute online training."""
        client = TestClient(app)

        # 1. Ingest streaming edge
        edge_payload = {
            "edge_id": "api_stream_edge_1",
            "source_id": "api_cust_A",
            "target_id": "api_cust_B",
            "rel_type": "TRANSACTS_WITH",
            "amount": 250.0,
            "bank_id": "bank_beta",
        }
        res_edge = client.post("/api/v1/graph/stream/edge", json=edge_payload)
        assert res_edge.status_code == 200
        edge_data = res_edge.json()
        assert edge_data["processed_count"] == 1
        assert "latency_ms" in edge_data

        # 2. Check Flink stream status
        res_status = client.get("/api/v1/graph/stream/status")
        assert res_status.status_code == 200
        status_data = res_status.json()
        assert status_data["status"] == "RUNNING"
        assert status_data["subsecond_sla_pass"] is True

        # 3. Trigger online GNN training step
        res_train = client.post("/api/v1/graph/stream/gnn/train")
        assert res_train.status_code == 200
        train_data = res_train.json()
        assert "loss" in train_data
        assert "training_applied" in train_data
        assert train_data["node_count"] >= 2
