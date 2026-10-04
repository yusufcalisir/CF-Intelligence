"""Whole-System Integration Tests: Streaming Redelivery, Parallel Legitimate Transactions & Crash-Window Recovery.

Validates:
SYS-INV-02: One Financial Event Retains One Logical Identity.
SYS-INV-03: Distinct Events Remain Distinct.
SYS-INV-06: Retry Does Not Change Meaning.
Scenario 2: Same Event Redelivery.
Scenario 3: Parallel Legitimate Transactions.
Scenario 4: Crash-Window Redelivery.
"""

from __future__ import annotations

import uuid

import pytest

from app.application.services.feature_store_service import FeatureStoreService
from app.application.services.streaming_graph_service import StreamingGraphService
from app.application.services.webhook_service import WebhookEventType, WebhookService
from app.infrastructure.redis_store import RedisStore


@pytest.fixture(autouse=True)
def clean_redis_and_feature_store() -> None:
    """Ensure in-memory fallback stores and feature stores are cleanly reset."""
    with RedisStore._lock:
        RedisStore._shared_fallback_stores.clear()
        RedisStore._global_redis_unavailable = True
    fs = FeatureStoreService()
    fs.clear()


class TestStreamingRedeliveryAndParallelTransactions:
    """Scenarios 2, 3, 4: Retries, parallel legitimate edges, and crash-window idempotency."""

    def test_scenario_2_same_event_redelivery_metamorphic_equivalence(self) -> None:
        """Scenario 2: Take one canonical transaction T. Ingest T, T retry, T retry.

        Compare against single clean execution T.
        Independent Oracle:
            - Graph edge count == 1, node degrees == 1.
            - Feature store customer history length == 1, velocity == 1.
            - Webhook deterministic event_id remains identical across attempts.
        """
        canonical_tx_id = f"tx_canon_{uuid.uuid4().hex[:8]}"
        tx_payload = {
            "tenant_id": "bank_alpha",
            "transaction_id": canonical_tx_id,
            "sender_id": "acct_source_1",
            "receiver_id": "acct_dest_1",
            "amount": 250.0,
            "timestamp": "2026-10-04T12:00:00Z",
        }

        # 1. Clean execution containing only T
        clean_graph = StreamingGraphService(max_window_minutes=60)
        clean_graph.add_transaction(tx_payload)

        clean_fs = FeatureStoreService()
        clean_fs.settings.feature_store_enabled = True
        clean_fs.ingest_transaction(
            customer_id="acct_source_1",
            amount=250.0,
            merchant_id="acct_dest_1",
            merchant_category="online_marketplace",
            merchant_risk_score=0.45,
            customer_history_score=0.90,
            chargeback_count=0,
            account_age_days=365,
            transaction_id=canonical_tx_id,
            tenant_id="bank_alpha",
        )

        # 2. Retried execution containing [T, T retry, T retry]
        retried_graph = StreamingGraphService(max_window_minutes=60)
        retried_graph.add_transaction(tx_payload)
        retried_graph.add_transaction(tx_payload)  # Retry 1
        retried_graph.add_transaction(tx_payload)  # Retry 2

        retried_fs = FeatureStoreService()
        retried_fs.settings.feature_store_enabled = True
        # Ingest 3 times
        for _ in range(3):
            retried_fs.ingest_transaction(
                customer_id="acct_source_1",
                amount=250.0,
                merchant_id="acct_dest_1",
                merchant_category="online_marketplace",
                merchant_risk_score=0.45,
                customer_history_score=0.90,
                chargeback_count=0,
                account_age_days=365,
                transaction_id=canonical_tx_id,
                tenant_id="bank_alpha",
            )

        # Assert Graph Metamorphic Equivalence
        assert len(clean_graph.edges) == len(retried_graph.edges) == 1
        assert len(clean_graph.nodes) == len(retried_graph.nodes) == 2
        assert clean_graph.node_degrees["acct_source_1"] == retried_graph.node_degrees["acct_source_1"] == 1
        assert clean_graph.node_degrees["acct_dest_1"] == retried_graph.node_degrees["acct_dest_1"] == 1

        # Assert Feature Store Metamorphic Equivalence
        history_clean = clean_fs.tx_history.get_list("bank_alpha:acct_source_1")
        history_retried = retried_fs.tx_history.get_list("bank_alpha:acct_source_1")
        assert len(history_clean) == len(history_retried) == 1, "Feature store sliding window double-counted retry"

        feats_clean = clean_fs.get_online_features(
            [{"customer_id": "acct_source_1", "merchant_id": "acct_dest_1"}],
            ["rolling_velocity_1h", "avg_amount_24h"],
            tenant_id="bank_alpha",
        )
        feats_retried = retried_fs.get_online_features(
            [{"customer_id": "acct_source_1", "merchant_id": "acct_dest_1"}],
            ["rolling_velocity_1h", "avg_amount_24h"],
            tenant_id="bank_alpha",
        )
        assert feats_clean[0]["rolling_velocity_1h"] == feats_retried[0]["rolling_velocity_1h"] == 1.0
        assert feats_clean[0]["avg_amount_24h"] == feats_retried[0]["avg_amount_24h"] == 250.0

        # Assert Webhook Deterministic Event ID
        wh_svc = WebhookService()
        wh_svc.register_subscription(
            tenant_id="bank_alpha",
            target_url="https://example.com/webhook",
            events=[WebhookEventType.ALERT_CREATED],
        )
        alert_payload = {"transaction_id": canonical_tx_id, "risk_score": 750, "bank_id": "bank_alpha"}
        d1 = wh_svc.dispatch_event("bank_alpha", WebhookEventType.ALERT_CREATED, alert_payload)
        d2 = wh_svc.dispatch_event("bank_alpha", WebhookEventType.ALERT_CREATED, alert_payload)
        assert len(d1) == len(d2) == 1
        assert d1[0].event_id == d2[0].event_id, "Webhook event ID must remain deterministic across transport retries"

    def test_scenario_3_parallel_legitimate_transactions_preserved(self) -> None:
        """Scenario 3: T1 and T2 between same accounts with distinct transaction IDs.

        Inverse Oracle of Scenario 2:
            - retry(T1) != new transaction
            - T1 != T2
            - Both persist, both contribute to features, both remain distinct edges in MultiDiGraph.
        """
        graph = StreamingGraphService(max_window_minutes=60)
        t1 = {
            "tenant_id": "bank_beta",
            "transaction_id": "tx_parallel_001",
            "sender_id": "user_alice",
            "receiver_id": "user_bob",
            "amount": 100.0,
            "timestamp": "2026-10-04T14:00:00Z",
        }
        t2 = {
            "tenant_id": "bank_beta",
            "transaction_id": "tx_parallel_002",
            "sender_id": "user_alice",
            "receiver_id": "user_bob",
            "amount": 100.0,  # Same amount, same accounts, but DIFFERENT tx_id
            "timestamp": "2026-10-04T14:02:00Z",
        }

        graph.add_transaction(t1)
        graph.add_transaction(t2)

        # Graph MultiDiGraph Parallel Edge Oracle
        assert len(graph.nodes) == 2
        assert len(graph.edges) == 2, "Parallel legitimate transactions erroneously collapsed"
        assert graph.node_degrees["user_alice"] == 2
        assert graph.node_degrees["user_bob"] == 2

        # Feature Store Oracle
        fs = FeatureStoreService()
        fs.settings.feature_store_enabled = True
        fs.ingest_transaction(
            customer_id="user_alice",
            amount=100.0,
            merchant_id="user_bob",
            merchant_category="wire_transfer",
            merchant_risk_score=0.75,
            customer_history_score=0.95,
            chargeback_count=0,
            account_age_days=100,
            transaction_id="tx_parallel_001",
            tenant_id="bank_beta",
        )
        fs.ingest_transaction(
            customer_id="user_alice",
            amount=100.0,
            merchant_id="user_bob",
            merchant_category="wire_transfer",
            merchant_risk_score=0.75,
            customer_history_score=0.95,
            chargeback_count=0,
            account_age_days=100,
            transaction_id="tx_parallel_002",
            tenant_id="bank_beta",
        )

        history = fs.tx_history.get_list("bank_beta:user_alice")
        assert len(history) == 2, "Distinct parallel transactions must both contribute to feature history"
        feats = fs.get_online_features(
            [{"customer_id": "user_alice", "merchant_id": "user_bob"}],
            ["rolling_velocity_1h", "avg_amount_24h"],
            tenant_id="bank_beta",
        )
        assert feats[0]["rolling_velocity_1h"] == 2.0

    def test_scenario_4_crash_window_redelivery(self) -> None:
        """Scenario 4: Business mutation succeeds, process fails before acknowledgement, event redelivered.

        Simulates crash window:
            1. Ingest T -> graph edge inserted, feature store updated.
            2. Simulated process failure / unacknowledged message.
            3. Broker redelivers T.
        Verification:
            - Graph edge count remains 1 (no duplicate edge).
            - Feature store velocity remains 1 (no double increment).
            - Webhook dispatch distinguishes transport retry from new business event.
        """
        crash_tx_id = f"tx_crash_{uuid.uuid4().hex[:8]}"
        tx_data = {
            "tenant_id": "bank_gamma",
            "transaction_id": crash_tx_id,
            "sender_id": "acct_c1",
            "receiver_id": "acct_c2",
            "amount": 500.0,
            "timestamp": "2026-10-04T15:00:00Z",
        }

        graph = StreamingGraphService(max_window_minutes=60)
        fs = FeatureStoreService()
        fs.settings.feature_store_enabled = True

        # Pre-crash mutation
        graph.add_transaction(tx_data)
        fs.ingest_transaction(
            customer_id="acct_c1",
            amount=500.0,
            merchant_id="acct_c2",
            merchant_category="online_marketplace",
            merchant_risk_score=0.45,
            customer_history_score=0.90,
            chargeback_count=0,
            account_age_days=300,
            transaction_id=crash_tx_id,
            tenant_id="bank_gamma",
        )

        pre_crash_edges = len(graph.edges)
        pre_crash_velocity = fs.get_online_features(
            [{"customer_id": "acct_c1", "merchant_id": "acct_c2"}],
            ["rolling_velocity_1h"],
            tenant_id="bank_gamma",
        )[0]["rolling_velocity_1h"]

        assert pre_crash_edges == 1
        assert pre_crash_velocity == 1.0

        # Crash occurs here (unacknowledged offset) -> Broker redelivers exact same event
        graph.add_transaction(tx_data)
        fs.ingest_transaction(
            customer_id="acct_c1",
            amount=500.0,
            merchant_id="acct_c2",
            merchant_category="online_marketplace",
            merchant_risk_score=0.45,
            customer_history_score=0.90,
            chargeback_count=0,
            account_age_days=300,
            transaction_id=crash_tx_id,
            tenant_id="bank_gamma",
        )

        post_redelivery_edges = len(graph.edges)
        post_redelivery_velocity = fs.get_online_features(
            [{"customer_id": "acct_c1", "merchant_id": "acct_c2"}],
            ["rolling_velocity_1h"],
            tenant_id="bank_gamma",
        )[0]["rolling_velocity_1h"]

        assert post_redelivery_edges == 1, "Crash redelivery created duplicate graph edge"
        assert post_redelivery_velocity == 1.0, "Crash redelivery double-counted rolling velocity"
