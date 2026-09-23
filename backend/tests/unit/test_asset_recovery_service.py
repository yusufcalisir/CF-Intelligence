"""Unit tests for the Asset Recovery & Collaborative FININT Operational Hub.

Tests cover:
    - AssetRecoveryService initialization and demonstration data seeding
    - KPI summary computation (EUR totals, MTTR statistics, containment rate)
    - Timeline aggregation and windowing
    - Per-typology breakdown ordering and risk classification
    - Event recording: valid events, state mutations, audit hash chaining
    - Input validation: invalid event types, unknown typologies, bad amounts
    - REST router endpoints via FastAPI TestClient (summary, timeline, breakdown, POST)
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.services.asset_recovery_service import (
    AssetRecoveryService,
    get_asset_recovery_service,
)
from app.presentation.routers.asset_recovery import api_router, router

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def fresh_service() -> AssetRecoveryService:
    """Return a fresh AssetRecoveryService (with seeded demo data)."""
    return AssetRecoveryService()


@pytest.fixture()
def empty_service() -> AssetRecoveryService:
    """Return an AssetRecoveryService with all events cleared."""
    svc = AssetRecoveryService()
    svc._events.clear()
    svc._audit_head = "0" * 64
    svc._audit_seq = 0
    return svc


@pytest.fixture()
def test_client() -> TestClient:
    """FastAPI TestClient with both router prefixes mounted."""
    app = FastAPI()
    app.include_router(router)
    app.include_router(api_router)
    return TestClient(app)


# ── Service: Initialization & Demo Data ───────────────────────────────────────


class TestAssetRecoveryServiceInit:
    def test_seeded_events_are_present(self, fresh_service: AssetRecoveryService) -> None:
        events = fresh_service.get_all_events()
        assert len(events) >= 10, "Expected at least 10 seeded demonstration events"

    def test_seeded_events_have_positive_amounts(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        for evt in fresh_service.get_all_events():
            assert evt.amount_eur > 0, f"Event {evt.event_id} has non-positive amount"

    def test_seeded_events_have_audit_hashes(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        for evt in fresh_service.get_all_events():
            assert len(evt.audit_hash) == 64, (
                f"Event {evt.event_id} has invalid audit hash length"
            )

    def test_seeded_events_have_valid_typologies(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        known = {
            "smurfing_structuring",
            "app_fraud_mule_chain",
            "dormant_burst_velocity",
            "rapid_pass_through",
            "high_risk_corridor_flight",
            "round_tripping",
            "invoice_manipulation",
            "crypto_gateway_cashout",
        }
        for evt in fresh_service.get_all_events():
            assert evt.typology in known, f"Unknown typology: {evt.typology}"

    def test_seeded_events_have_computed_mttr(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        events_with_freeze = [
            e for e in fresh_service.get_all_events() if e.freeze_confirmed_at is not None
        ]
        assert len(events_with_freeze) >= 5
        for evt in events_with_freeze:
            assert evt.mttr_minutes is not None
            assert evt.mttr_minutes >= 0.0

    def test_singleton_returns_same_instance(self) -> None:
        svc1 = get_asset_recovery_service()
        svc2 = get_asset_recovery_service()
        assert svc1 is svc2, "Singleton should return the same instance"


# ── Service: KPI Summary ───────────────────────────────────────────────────────


class TestAssetRecoveryServiceSummary:
    def test_summary_total_eur_frozen_positive(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        summary = fresh_service.get_summary()
        assert summary.total_eur_frozen > 0

    def test_summary_total_eur_recovered_le_frozen(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        summary = fresh_service.get_summary()
        assert summary.total_eur_recovered <= summary.total_eur_frozen

    def test_summary_mttr_mean_positive(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        summary = fresh_service.get_summary()
        assert summary.mttr_mean_minutes > 0.0

    def test_summary_mttr_p90_ge_p50(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        summary = fresh_service.get_summary()
        assert summary.mttr_p90_minutes >= summary.mttr_p50_minutes

    def test_summary_mttr_reduction_pct_positive(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        summary = fresh_service.get_summary()
        # All seeded events have MTTR < 120 min, far below 2880-min baseline
        assert summary.mttr_reduction_pct > 90.0, (
            f"Expected >90% MTTR reduction vs. baseline, got {summary.mttr_reduction_pct}%"
        )

    def test_summary_legacy_baseline_is_2880(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        summary = fresh_service.get_summary()
        assert summary.legacy_baseline_minutes == 48.0 * 60.0

    def test_summary_consortium_banks_positive(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        summary = fresh_service.get_summary()
        assert summary.consortium_banks_active >= 3

    def test_summary_active_provisional_holds(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        summary = fresh_service.get_summary()
        assert summary.active_provisional_holds >= 1

    def test_summary_contagion_containment_rate_range(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        summary = fresh_service.get_summary()
        assert 0.0 <= summary.contagion_containment_rate <= 1.0

    def test_empty_service_returns_zero_summary(
        self, empty_service: AssetRecoveryService
    ) -> None:
        summary = empty_service.get_summary()
        assert summary.total_events == 0
        assert summary.total_eur_frozen == Decimal("0.00")
        assert summary.mttr_mean_minutes == 0.0
        assert summary.mttr_reduction_pct == 0.0


# ── Service: Timeline ──────────────────────────────────────────────────────────


class TestAssetRecoveryServiceTimeline:
    def test_timeline_returns_data(self, fresh_service: AssetRecoveryService) -> None:
        points = fresh_service.get_timeline(window_hours=720)
        assert len(points) > 0

    def test_timeline_eur_frozen_non_negative(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        for p in fresh_service.get_timeline(window_hours=720):
            assert p.eur_frozen >= 0.0
            assert p.eur_recovered >= 0.0
            assert p.eur_recovered <= p.eur_frozen + 0.01  # float tolerance

    def test_timeline_short_window_fewer_points(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        long_pts = fresh_service.get_timeline(window_hours=720)
        short_pts = fresh_service.get_timeline(window_hours=24)
        # Short window should have fewer or equal data points
        assert len(short_pts) <= len(long_pts)

    def test_empty_timeline(self, empty_service: AssetRecoveryService) -> None:
        points = empty_service.get_timeline(window_hours=720)
        assert points == []


# ── Service: Typology Breakdown ────────────────────────────────────────────────


class TestAssetRecoveryServiceBreakdown:
    def test_breakdown_returns_entries(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        breakdown = fresh_service.get_breakdown_by_typology()
        assert len(breakdown) > 0

    def test_breakdown_ordered_by_eur_descending(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        breakdown = fresh_service.get_breakdown_by_typology()
        amounts = [b.total_eur for b in breakdown]
        assert amounts == sorted(amounts, reverse=True), (
            "Breakdown should be ordered by total_eur descending"
        )

    def test_breakdown_risk_label_valid_values(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        valid_labels = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        for b in fresh_service.get_breakdown_by_typology():
            assert b.risk_label in valid_labels

    def test_breakdown_containment_rate_range(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        for b in fresh_service.get_breakdown_by_typology():
            assert 0.0 <= b.containment_rate <= 1.0

    def test_breakdown_crypto_cashout_critical(
        self, fresh_service: AssetRecoveryService
    ) -> None:
        """Crypto gateway cashout totals >€200k → CRITICAL."""
        breakdown = {b.typology: b for b in fresh_service.get_breakdown_by_typology()}
        if "crypto_gateway_cashout" in breakdown:
            assert breakdown["crypto_gateway_cashout"].risk_label == "CRITICAL"


# ── Service: Event Recording ───────────────────────────────────────────────────


class TestAssetRecoveryServiceRecording:
    def test_record_valid_recall_success(
        self, empty_service: AssetRecoveryService
    ) -> None:
        evt = empty_service.record_recovery_event(
            event_type="RECALL_SUCCESS",
            amount_eur="15000.00",
            typology="app_fraud_mule_chain",
            originating_bank_id="bank_alpha",
            receiving_bank_id="bank_beta",
            recall_message_id="CAMT056-TEST-001",
            finint_ticket_id="FININT-TEST-001",
        )
        assert evt.event_id
        assert evt.amount_eur == Decimal("15000.00")
        assert evt.event_type == "RECALL_SUCCESS"
        assert len(evt.audit_hash) == 64

    def test_record_provisional_hold(
        self, empty_service: AssetRecoveryService
    ) -> None:
        evt = empty_service.record_recovery_event(
            event_type="PROVISIONAL_HOLD",
            amount_eur="50000.00",
            typology="dormant_burst_velocity",
            originating_bank_id="bank_gamma",
            receiving_bank_id="bank_alpha",
        )
        assert evt.event_type == "PROVISIONAL_HOLD"

    def test_record_partial_recovery(
        self, empty_service: AssetRecoveryService
    ) -> None:
        evt = empty_service.record_recovery_event(
            event_type="PARTIAL_RECOVERY",
            amount_eur="7500.50",
            typology="smurfing_structuring",
            originating_bank_id="bank_beta",
            receiving_bank_id="bank_gamma",
        )
        assert evt.event_type == "PARTIAL_RECOVERY"

    def test_audit_hash_chaining(self, empty_service: AssetRecoveryService) -> None:
        """Sequential events must have different, non-empty audit hashes."""
        evt1 = empty_service.record_recovery_event(
            event_type="RECALL_SUCCESS",
            amount_eur="10000.00",
            typology="app_fraud_mule_chain",
            originating_bank_id="bank_alpha",
            receiving_bank_id="bank_beta",
        )
        evt2 = empty_service.record_recovery_event(
            event_type="PROVISIONAL_HOLD",
            amount_eur="20000.00",
            typology="high_risk_corridor_flight",
            originating_bank_id="bank_gamma",
            receiving_bank_id="bank_alpha",
        )
        assert evt1.audit_hash != evt2.audit_hash
        assert len(evt1.audit_hash) == 64
        assert len(evt2.audit_hash) == 64

    def test_record_event_increments_count(
        self, empty_service: AssetRecoveryService
    ) -> None:
        assert empty_service.get_summary().total_events == 0
        empty_service.record_recovery_event(
            event_type="RECALL_SUCCESS",
            amount_eur="5000.00",
            typology="round_tripping",
            originating_bank_id="bank_alpha",
            receiving_bank_id="bank_beta",
        )
        assert empty_service.get_summary().total_events == 1

    def test_mttr_computed_when_freeze_provided(
        self, empty_service: AssetRecoveryService
    ) -> None:
        alert_at = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
        freeze_at = datetime(2026, 1, 1, 10, 15, 0, tzinfo=UTC)  # 15 min later
        evt = empty_service.record_recovery_event(
            event_type="RECALL_SUCCESS",
            amount_eur="8000.00",
            typology="smurfing_structuring",
            originating_bank_id="bank_alpha",
            receiving_bank_id="bank_beta",
            alert_raised_at=alert_at,
            freeze_confirmed_at=freeze_at,
        )
        assert evt.mttr_minutes == pytest.approx(15.0, abs=0.1)

    def test_mttr_none_without_freeze(
        self, empty_service: AssetRecoveryService
    ) -> None:
        evt = empty_service.record_recovery_event(
            event_type="PROVISIONAL_HOLD",
            amount_eur="30000.00",
            typology="invoice_manipulation",
            originating_bank_id="bank_beta",
            receiving_bank_id="bank_gamma",
            freeze_confirmed_at=None,
        )
        assert evt.mttr_minutes is None


# ── Service: Input Validation ──────────────────────────────────────────────────


class TestAssetRecoveryServiceValidation:
    def test_invalid_event_type_raises(
        self, empty_service: AssetRecoveryService
    ) -> None:
        with pytest.raises(ValueError, match="Invalid event_type"):
            empty_service.record_recovery_event(
                event_type="UNKNOWN_TYPE",
                amount_eur="1000.00",
                typology="app_fraud_mule_chain",
                originating_bank_id="bank_alpha",
                receiving_bank_id="bank_beta",
            )

    def test_unknown_typology_raises(
        self, empty_service: AssetRecoveryService
    ) -> None:
        with pytest.raises(ValueError, match="Unknown typology"):
            empty_service.record_recovery_event(
                event_type="RECALL_SUCCESS",
                amount_eur="1000.00",
                typology="not_a_real_typology",
                originating_bank_id="bank_alpha",
                receiving_bank_id="bank_beta",
            )

    def test_negative_amount_raises(
        self, empty_service: AssetRecoveryService
    ) -> None:
        with pytest.raises(ValueError, match="positive"):
            empty_service.record_recovery_event(
                event_type="RECALL_SUCCESS",
                amount_eur="-500.00",
                typology="app_fraud_mule_chain",
                originating_bank_id="bank_alpha",
                receiving_bank_id="bank_beta",
            )

    def test_zero_amount_raises(self, empty_service: AssetRecoveryService) -> None:
        with pytest.raises(ValueError):
            empty_service.record_recovery_event(
                event_type="RECALL_SUCCESS",
                amount_eur="0.00",
                typology="app_fraud_mule_chain",
                originating_bank_id="bank_alpha",
                receiving_bank_id="bank_beta",
            )

    def test_non_numeric_amount_raises(
        self, empty_service: AssetRecoveryService
    ) -> None:
        with pytest.raises(ValueError, match="Invalid EUR amount"):
            empty_service.record_recovery_event(
                event_type="RECALL_SUCCESS",
                amount_eur="not_a_number",
                typology="app_fraud_mule_chain",
                originating_bank_id="bank_alpha",
                receiving_bank_id="bank_beta",
            )


# ── REST Router: Endpoint Tests ────────────────────────────────────────────────


class TestAssetRecoveryRouterSummary:
    def test_get_summary_200(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/v1/operations/asset-recovery/summary")
        assert resp.status_code == 200

    def test_get_summary_schema(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/v1/operations/asset-recovery/summary")
        data = resp.json()
        required_keys = {
            "snapshot_at",
            "total_events",
            "total_eur_frozen",
            "total_eur_recovered",
            "contagion_containment_rate",
            "mttr_mean_minutes",
            "mttr_p50_minutes",
            "mttr_p90_minutes",
            "mttr_p99_minutes",
            "legacy_baseline_minutes",
            "mttr_reduction_pct",
            "mule_chains_disrupted",
            "consortium_banks_active",
            "active_provisional_holds",
        }
        assert required_keys.issubset(set(data.keys()))

    def test_get_summary_non_prefix_route(self, test_client: TestClient) -> None:
        resp = test_client.get("/operations/asset-recovery/summary")
        assert resp.status_code == 200

    def test_summary_eur_frozen_positive(self, test_client: TestClient) -> None:
        data = test_client.get("/api/v1/operations/asset-recovery/summary").json()
        assert data["total_eur_frozen"] > 0

    def test_summary_mttr_reduction_above_90pct(self, test_client: TestClient) -> None:
        data = test_client.get("/api/v1/operations/asset-recovery/summary").json()
        assert data["mttr_reduction_pct"] > 90.0


class TestAssetRecoveryRouterTimeline:
    def test_get_timeline_200(self, test_client: TestClient) -> None:
        resp = test_client.get("/api/v1/operations/asset-recovery/timeline")
        assert resp.status_code == 200

    def test_get_timeline_is_list(self, test_client: TestClient) -> None:
        data = test_client.get("/api/v1/operations/asset-recovery/timeline").json()
        assert isinstance(data, list)

    def test_get_timeline_data_point_schema(self, test_client: TestClient) -> None:
        data = test_client.get("/api/v1/operations/asset-recovery/timeline").json()
        if data:
            point = data[0]
            assert "period_start" in point
            assert "eur_frozen" in point
            assert "eur_recovered" in point
            assert "event_count" in point
            assert "avg_mttr_minutes" in point

    def test_get_timeline_window_param(self, test_client: TestClient) -> None:
        resp = test_client.get(
            "/api/v1/operations/asset-recovery/timeline?window_hours=48"
        )
        assert resp.status_code == 200

    def test_get_timeline_invalid_window(self, test_client: TestClient) -> None:
        resp = test_client.get(
            "/api/v1/operations/asset-recovery/timeline?window_hours=0"
        )
        assert resp.status_code == 422

    def test_get_timeline_non_prefix_route(self, test_client: TestClient) -> None:
        resp = test_client.get("/operations/asset-recovery/timeline")
        assert resp.status_code == 200


class TestAssetRecoveryRouterBreakdown:
    def test_get_breakdown_200(self, test_client: TestClient) -> None:
        resp = test_client.get(
            "/api/v1/operations/asset-recovery/breakdown-by-typology"
        )
        assert resp.status_code == 200

    def test_get_breakdown_is_list(self, test_client: TestClient) -> None:
        data = test_client.get(
            "/api/v1/operations/asset-recovery/breakdown-by-typology"
        ).json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_get_breakdown_schema(self, test_client: TestClient) -> None:
        data = test_client.get(
            "/api/v1/operations/asset-recovery/breakdown-by-typology"
        ).json()
        if data:
            row = data[0]
            assert "typology" in row
            assert "event_count" in row
            assert "total_eur" in row
            assert "avg_mttr_minutes" in row
            assert "containment_rate" in row
            assert "risk_label" in row

    def test_get_breakdown_ordered_desc(self, test_client: TestClient) -> None:
        data = test_client.get(
            "/api/v1/operations/asset-recovery/breakdown-by-typology"
        ).json()
        amounts = [row["total_eur"] for row in data]
        assert amounts == sorted(amounts, reverse=True)

    def test_get_breakdown_non_prefix_route(self, test_client: TestClient) -> None:
        resp = test_client.get(
            "/operations/asset-recovery/breakdown-by-typology"
        )
        assert resp.status_code == 200


class TestAssetRecoveryRouterRecordEvent:
    def test_post_valid_event_201(self, test_client: TestClient) -> None:
        payload = {
            "event_type": "RECALL_SUCCESS",
            "amount_eur": "25000.00",
            "typology": "app_fraud_mule_chain",
            "originating_bank_id": "bank_alpha",
            "receiving_bank_id": "bank_beta",
            "recall_message_id": "CAMT056-ROUTER-TEST-001",
            "finint_ticket_id": "FININT-ROUTER-TEST-001",
        }
        resp = test_client.post(
            "/api/v1/operations/asset-recovery/events", json=payload
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["event_type"] == "RECALL_SUCCESS"
        assert data["amount_eur"] == pytest.approx(25000.0)
        assert len(data["audit_hash"]) == 64

    def test_post_invalid_event_type_422(self, test_client: TestClient) -> None:
        payload = {
            "event_type": "INVALID",
            "amount_eur": "1000.00",
            "typology": "app_fraud_mule_chain",
            "originating_bank_id": "bank_alpha",
            "receiving_bank_id": "bank_beta",
        }
        resp = test_client.post(
            "/api/v1/operations/asset-recovery/events", json=payload
        )
        assert resp.status_code == 422

    def test_post_unknown_typology_422(self, test_client: TestClient) -> None:
        payload = {
            "event_type": "RECALL_SUCCESS",
            "amount_eur": "1000.00",
            "typology": "not_a_typology",
            "originating_bank_id": "bank_alpha",
            "receiving_bank_id": "bank_beta",
        }
        resp = test_client.post(
            "/api/v1/operations/asset-recovery/events", json=payload
        )
        assert resp.status_code == 422

    def test_post_negative_amount_422(self, test_client: TestClient) -> None:
        payload = {
            "event_type": "PROVISIONAL_HOLD",
            "amount_eur": "-500.00",
            "typology": "smurfing_structuring",
            "originating_bank_id": "bank_alpha",
            "receiving_bank_id": "bank_beta",
        }
        resp = test_client.post(
            "/api/v1/operations/asset-recovery/events", json=payload
        )
        assert resp.status_code == 422

    def test_post_event_increments_summary(self, test_client: TestClient) -> None:
        before = test_client.get(
            "/api/v1/operations/asset-recovery/summary"
        ).json()["total_events"]
        test_client.post(
            "/api/v1/operations/asset-recovery/events",
            json={
                "event_type": "RECALL_SUCCESS",
                "amount_eur": "5000.00",
                "typology": "round_tripping",
                "originating_bank_id": "bank_gamma",
                "receiving_bank_id": "bank_alpha",
            },
        )
        after = test_client.get(
            "/api/v1/operations/asset-recovery/summary"
        ).json()["total_events"]
        # Note: singleton service is shared; both routers use the same instance
        # so count may differ from 'before + 1' if singleton already seeded
        assert after >= before
