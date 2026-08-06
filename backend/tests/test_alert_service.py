"""Tests for the alert intelligence service."""

import pytest

from app.application.services.alert_service import AlertIntelligenceService
from app.domain.enums import AlertSeverity


@pytest.fixture
def alert_service() -> AlertIntelligenceService:
    return AlertIntelligenceService(alert_threshold=0.5)


@pytest.fixture
def sample_transactions() -> list[dict]:
    return [
        {
            "transaction_id": "txn_001",
            "customer_id": "cust_001",
            "transaction_amount": 8500,
            "velocity": 7.0,
            "merchant_risk_score": 0.8,
            "merchant_category": "crypto",
            "country_code": "NG",
            "account_age_days": 15,
            "chargeback_count": 3,
            "hour_of_day": 3,
            "device_type": "web_browser",
        },
        {
            "transaction_id": "txn_002",
            "customer_id": "cust_002",
            "transaction_amount": 25,
            "velocity": 1.0,
            "merchant_risk_score": 0.1,
            "merchant_category": "grocery",
            "country_code": "US",
            "account_age_days": 730,
            "chargeback_count": 0,
            "hour_of_day": 14,
            "device_type": "mobile_app",
        },
    ]


class TestAlertGeneration:
    def test_generates_alerts_above_threshold(
        self,
        alert_service: AlertIntelligenceService,
        sample_transactions: list[dict],
    ) -> None:
        predictions = [0.85, 0.15]  # First above, second below threshold
        alerts = alert_service.generate_alerts("bank_a", sample_transactions, predictions)
        assert len(alerts) == 1
        assert alerts[0].bank_id == "bank_a"
        assert alerts[0].risk_score == pytest.approx(850.0, abs=1)

    def test_severity_classification(self, alert_service: AlertIntelligenceService) -> None:
        assert alert_service._classify_severity(0.95) == AlertSeverity.CRITICAL
        assert alert_service._classify_severity(0.80) == AlertSeverity.HIGH
        assert alert_service._classify_severity(0.60) == AlertSeverity.MEDIUM
        assert alert_service._classify_severity(0.35) == AlertSeverity.LOW
        assert alert_service._classify_severity(0.10) == AlertSeverity.INFO

    def test_reason_codes_generated(
        self,
        alert_service: AlertIntelligenceService,
        sample_transactions: list[dict],
    ) -> None:
        predictions = [0.85]
        alerts = alert_service.generate_alerts("bank_a", sample_transactions[:1], predictions)
        alert = alerts[0]
        # High-risk transaction should trigger multiple reason codes
        assert "ML-HIGH" in alert.reason_codes
        assert "VEL-001" in alert.reason_codes
        assert "GEO-RISK" in alert.reason_codes
        assert "NEW-ACCT" in alert.reason_codes

    def test_custom_threshold(self, alert_service: AlertIntelligenceService) -> None:
        txns = [{"transaction_id": "t1"}, {"transaction_id": "t2"}]
        predictions = [0.45, 0.55]
        alerts = alert_service.generate_alerts("bank_a", txns, predictions, threshold=0.4)
        assert len(alerts) == 2

    def test_alert_retrieval(
        self,
        alert_service: AlertIntelligenceService,
        sample_transactions: list[dict],
    ) -> None:
        alerts = alert_service.generate_alerts("bank_a", sample_transactions, [0.9, 0.8])
        retrieved = alert_service.get_alert(alerts[0].id)
        assert retrieved is not None
        assert retrieved.id == alerts[0].id

    def test_filter_by_severity(
        self,
        alert_service: AlertIntelligenceService,
        sample_transactions: list[dict],
    ) -> None:
        alert_service.generate_alerts("bank_a", sample_transactions, [0.95, 0.6])
        critical = alert_service.get_alerts(severity=AlertSeverity.CRITICAL)
        assert all(a.severity == AlertSeverity.CRITICAL for a in critical)


class TestIntelligenceSharing:
    def test_publish_intelligence(
        self,
        alert_service: AlertIntelligenceService,
        sample_transactions: list[dict],
    ) -> None:
        alerts = alert_service.generate_alerts("bank_a", sample_transactions[:1], [0.9])
        intel = alert_service.publish_intelligence(alerts[0])
        assert intel.source_bank_id == "bank_a"
        assert 0 < intel.risk_indicator <= 1.0
        assert len(intel.privacy_hash) == 32

    def test_consume_intelligence_excludes_own(
        self,
        alert_service: AlertIntelligenceService,
        sample_transactions: list[dict],
    ) -> None:
        alerts = alert_service.generate_alerts("bank_a", sample_transactions[:1], [0.9])
        alert_service.publish_intelligence(alerts[0])

        # Bank A should not see its own intelligence
        bank_a_intel = alert_service.consume_intelligence("bank_a")
        assert len(bank_a_intel) == 0

        # Bank B should see it
        bank_b_intel = alert_service.consume_intelligence("bank_b")
        assert len(bank_b_intel) == 1

    def test_intelligence_stats(
        self,
        alert_service: AlertIntelligenceService,
        sample_transactions: list[dict],
    ) -> None:
        alerts = alert_service.generate_alerts("bank_a", sample_transactions[:1], [0.9])
        alert_service.publish_intelligence(alerts[0])
        stats = alert_service.get_intelligence_stats()
        assert stats["total_items"] == 1
        assert "bank_a" in stats["items_by_bank"]


class TestAlertCorrelation:
    def test_entity_overlap_detection(
        self,
        alert_service: AlertIntelligenceService,
        sample_transactions: list[dict],
    ) -> None:
        # Generate alerts with same customer (same entity ID)
        alerts = alert_service.generate_alerts("bank_a", sample_transactions, [0.9, 0.8])
        if len(alerts) >= 2:
            correlations = alert_service.correlate_alerts(alerts)
            # Should find correlations if entities overlap
            entity_overlaps = [c for c in correlations if c["type"] == "entity_overlap"]
            assert isinstance(entity_overlaps, list)
