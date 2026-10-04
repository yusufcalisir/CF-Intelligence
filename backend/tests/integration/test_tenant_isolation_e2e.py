"""Whole-System Integration Tests: End-to-End Tenant Isolation and Cross-Tenant Collision Safety.

Validates:
SYS-INV-01: Tenant Isolation Survives the Entire Path.
Scenario 1: Cross-Tenant Same-ID Collision.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.application.services.case_service import CaseManagementService
from app.application.services.feature_store_service import FeatureStoreService
from app.application.services.streaming_graph_service import StreamingGraphService
from app.domain.enums import CasePriority
from app.infrastructure.database import Base
from app.infrastructure.redis_store import RedisStore
from app.infrastructure.repositories.alert_repository import AlertRepository
from app.main import app


@pytest.fixture(autouse=True)
def clean_redis_and_feature_store() -> None:
    """Ensure in-memory fallback stores and feature stores are cleanly reset."""
    with RedisStore._lock:
        RedisStore._shared_fallback_stores.clear()
        RedisStore._global_redis_unavailable = True
    fs = FeatureStoreService()
    fs.clear()


class TestCrossTenantSameIdCollision:
    """Scenario 1: Two different tenant banks ingest a transaction with the identical transaction_id."""

    @pytest.mark.asyncio
    async def test_cross_tenant_same_transaction_id_coexistence(self) -> None:
        """Verify that Bank A and Bank B both processing tx_shared_001 coexist cleanly without collision.

        Independent Oracle:
            - Bank A features and Bank B features are partitioned: keys_a disjoint from keys_b.
            - Graph edge registry contains both 'bank_a:tx_shared_001' and 'bank_b:tx_shared_001'.
            - Retrying Bank A's transaction leaves Bank B's state completely untouched.
        """
        shared_tx_id = f"tx_shared_{uuid.uuid4().hex[:6]}"

        # 1. Feature Store Scoping
        fs = FeatureStoreService()
        fs.settings.feature_store_enabled = True

        # Bank A ingestion
        fs.ingest_transaction(
            customer_id="cust_alice",
            amount=150.0,
            merchant_id="merch_grocery",
            merchant_category="grocery",
            merchant_risk_score=0.03,
            customer_history_score=0.98,
            chargeback_count=0,
            account_age_days=500,
            transaction_id=shared_tx_id,
            tenant_id="bank_a",
        )

        # Bank B ingestion of same transaction_id with conflicting payload
        fs.ingest_transaction(
            customer_id="cust_alice",  # Same local customer ID name in Bank B
            amount=9900.0,  # Completely different amount
            merchant_id="merch_crypto",
            merchant_category="crypto",
            merchant_risk_score=0.95,
            customer_history_score=0.10,
            chargeback_count=5,
            account_age_days=10,
            transaction_id=shared_tx_id,
            tenant_id="bank_b",
        )

        # Query features for Bank A vs Bank B
        feats_a = fs.get_online_features(
            [{"customer_id": "cust_alice", "merchant_id": "merch_grocery"}],
            ["rolling_velocity_1h", "avg_amount_24h", "chargeback_count"],
            tenant_id="bank_a",
        )
        feats_b = fs.get_online_features(
            [{"customer_id": "cust_alice", "merchant_id": "merch_crypto"}],
            ["rolling_velocity_1h", "avg_amount_24h", "chargeback_count"],
            tenant_id="bank_b",
        )

        assert feats_a and feats_b
        assert feats_a[0]["chargeback_count"] == 0, "Bank A chargeback contaminated by Bank B"
        assert feats_b[0]["chargeback_count"] == 5, "Bank B chargeback contaminated by Bank A"
        assert feats_a[0]["avg_amount_24h"] == 150.0
        assert feats_b[0]["avg_amount_24h"] == 9900.0

        # 2. Streaming Graph Scoping
        graph = StreamingGraphService(max_window_minutes=60)
        graph.add_transaction({
            "bank_id": "bank_a",
            "transaction_id": shared_tx_id,
            "sender_id": "bank_a_cust_alice",
            "receiver_id": "bank_a_merch_grocery",
            "amount": 150.0,
            "timestamp": datetime.now(UTC).isoformat(),
        })
        graph.add_transaction({
            "bank_id": "bank_b",
            "transaction_id": shared_tx_id,
            "sender_id": "bank_b_cust_alice",
            "receiver_id": "bank_b_merch_crypto",
            "amount": 9900.0,
            "timestamp": datetime.now(UTC).isoformat(),
        })

        assert len(graph.edges) == 2, "Cross-tenant transaction ID collision prevented second edge"
        assert "bank_a:" + shared_tx_id in graph._seen_transactions
        assert "bank_b:" + shared_tx_id in graph._seen_transactions

        # 3. Retry Bank A — Bank B must remain completely unaffected
        initial_bank_b_edge = graph._seen_transactions["bank_b:" + shared_tx_id]
        graph.add_transaction({
            "bank_id": "bank_a",
            "transaction_id": shared_tx_id,
            "sender_id": "bank_a_cust_alice",
            "receiver_id": "bank_a_merch_grocery",
            "amount": 150.0,
            "timestamp": datetime.now(UTC).isoformat(),
        })

        assert len(graph.edges) == 2, "Retry of Bank A created duplicate graph edge"
        assert graph._seen_transactions["bank_b:" + shared_tx_id] == initial_bank_b_edge

        # 4. Database Persistence Scoping
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with session_factory() as session:
            repo = AlertRepository(session)
            alert_a = await repo.create(
                bank_id="bank_a",
                transaction_id=shared_tx_id,
                risk_score=0.15,
                severity="low",
            )
            alert_b = await repo.create(
                bank_id="bank_b",
                transaction_id=shared_tx_id,
                risk_score=0.92,
                severity="critical",
            )

            assert alert_a.id != alert_b.id
            found_a = await repo.get_by_transaction_id(shared_tx_id, bank_id="bank_a")
            found_b = await repo.get_by_transaction_id(shared_tx_id, bank_id="bank_b")
            assert found_a is not None and found_a.risk_score == 0.15
            assert found_b is not None and found_b.risk_score == 0.92

        await engine.dispose()

    def test_cross_tenant_api_access_blocked(self) -> None:
        """Verify API layer strictly prevents cross-tenant access to cases, alerts, and predictions."""
        client = TestClient(app)

        # 1. Bank A creates a case
        case_svc = CaseManagementService()
        case_a = case_svc.create_case(
            title="Bank A Secret Case",
            priority=CasePriority.P1_CRITICAL,
            assigned_to="bank_a_investigator",
            bank_id="bank_a",
        )

        # 2. Bank B tries to access Bank A's case
        res_b = client.get(
            f"/api/v1/cases/{case_a.id}",
            headers={"X-Tenant-ID": "bank_b"},
        )
        assert res_b.status_code == 403, f"Expected 403 Forbidden for cross-tenant case access, got {res_b.status_code}"
        assert "Broken Access Control Prevention" in res_b.json()["detail"]

        # 3. Bank A accesses own case -> 200 OK
        res_a = client.get(
            f"/api/v1/cases/{case_a.id}",
            headers={"X-Tenant-ID": "bank_a"},
        )
        assert res_a.status_code == 200
        assert res_a.json()["id"] == case_a.id

        # 4. Bank B lists cases -> Bank A case is excluded
        list_b = client.get("/api/v1/cases", headers={"X-Tenant-ID": "bank_b"})
        assert list_b.status_code == 200
        case_ids_b = [c["id"] for c in list_b.json()]
        assert case_a.id not in case_ids_b, "Bank A case leaked into Bank B case list"
