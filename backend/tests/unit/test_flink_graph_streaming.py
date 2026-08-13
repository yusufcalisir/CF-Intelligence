from app.application.services.flink_graph_streaming import (
    FlinkGraphStreamProcessor,
    StreamingEdgeEvent,
)


def test_flink_processor_subsecond_latency_sla() -> None:
    processor = FlinkGraphStreamProcessor(window_size_ms=500, velocity_threshold=3.0)

    events = [
        StreamingEdgeEvent(
            edge_id=f"e_{i}",
            source_id="bank_a_cust_101",
            target_id="bank_b_cust_202",
            rel_type="TRANSACTS_WITH",
            amount=500.0 + i * 10,
        )
        for i in range(10)
    ]

    receipt = processor.process_batch_stream(events)

    assert receipt.processed_count == 10
    assert receipt.latency_ms < 50.0  # Sub-second SLA (<50ms processing time)
    assert receipt.window_size_ms == 500
    assert len(receipt.velocity_anomalies) >= 1
    assert "bank_a_cust_101" in receipt.high_risk_entities


def test_flink_processor_velocity_anomaly_detection() -> None:
    processor = FlinkGraphStreamProcessor(window_size_ms=1000, velocity_threshold=2.0)

    # 1st event -> below threshold
    receipt1 = processor.process_streaming_edge(
        StreamingEdgeEvent(
            edge_id="e_1",
            source_id="node_x",
            target_id="node_y",
            rel_type="SHARES_IP",
            amount=100.0,
        )
    )
    assert len(receipt1.velocity_anomalies) == 0

    # 2nd & 3rd events -> exceeds threshold
    receipt2 = processor.process_batch_stream(
        [
            StreamingEdgeEvent(
                edge_id="e_2",
                source_id="node_x",
                target_id="node_y",
                rel_type="SHARES_IP",
                amount=150.0,
            ),
            StreamingEdgeEvent(
                edge_id="e_3",
                source_id="node_x",
                target_id="node_y",
                rel_type="SHARES_IP",
                amount=200.0,
            ),
        ]
    )
    assert len(receipt2.velocity_anomalies) > 0
    assert receipt2.velocity_anomalies[0]["pair_key"] == "node_x->node_y"


def test_flink_processor_status_metrics() -> None:
    processor = FlinkGraphStreamProcessor()
    status = processor.get_stream_status()

    assert status["status"] == "RUNNING"
    assert status["engine"] == "Apache Flink PyFlink DataStream"
    assert status["subsecond_sla_pass"] is True
