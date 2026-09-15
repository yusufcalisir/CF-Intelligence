"""Hardening and unit tests for Alert Processing, Triage & Deduplication Engine (STAGE_37).

Validates:
1. Alert generation and threshold filtering
2. Sliding-window deduplication detection
3. Sliding-window deduplication risk score escalation and reason code merging
4. Sliding-window deduplication window expiration
5. Multi-factor triage priority assignment and SLA computation
6. Burst velocity duplicate triage escalation to P1_CRITICAL
7. Zero-mock invariant: unknown alert ID returns HTTP 404
8. Alert status update endpoint and tenant isolation enforcement
9. On-demand triage re-evaluation and deduplication stats endpoints
10. Multi-threaded concurrency and thread safety under 20 concurrent threads
"""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.application.services.alert_service import (
    AlertDeduplicationEngine,
    AlertIntelligenceService,
    AlertTriageEngine,
    DeduplicationConfig,
)
from app.dependencies import resolve_tenant
from app.domain.entities_phase2 import Alert
from app.domain.enums import AlertSeverity, TriageAction, TriagePriority
from app.main import app


def test_alert_generation_and_threshold_filtering() -> None:
    """Verifies alerts below threshold are ignored, and alerts above threshold are scaled."""
    svc = AlertIntelligenceService(alert_threshold=0.60)
    txns = [
        {"transaction_id": "tx_high", "customer_id": "c1", "transaction_amount": 1200},
        {"transaction_id": "tx_low", "customer_id": "c2", "transaction_amount": 30},
    ]
    scores = [0.85, 0.40]

    alerts = svc.generate_alerts("bank_a", txns, scores)
    assert len(alerts) == 1
    assert alerts[0].transaction_id == "tx_high"
    assert alerts[0].risk_score == 850.0
    assert alerts[0].severity == AlertSeverity.HIGH
    assert alerts[0].confidence == 0.85


def test_sliding_window_deduplication_detection() -> None:
    """Verifies duplicate transactions within window W are detected and counted."""
    engine = AlertDeduplicationEngine(DeduplicationConfig(window_seconds=300.0))
    now = datetime.now(UTC)

    a1 = Alert(
        bank_id="bank_a",
        transaction_id="tx_1",
        risk_score=750.0,
        severity=AlertSeverity.HIGH,
        reason_codes=["VEL-001"],
        involved_entity_ids=["cust_alpha"],
    )
    is_dup1, a1_proc = engine.process_alert(a1, primary_entity_id="cust_alpha", now=now)
    assert not is_dup1
    assert a1_proc.dedup_count == 1
    assert not a1_proc.is_duplicate

    # Second alert 30s later with same pattern
    a2 = Alert(
        bank_id="bank_a",
        transaction_id="tx_2",
        risk_score=800.0,
        severity=AlertSeverity.HIGH,
        reason_codes=["VEL-001"],
        involved_entity_ids=["cust_alpha"],
    )
    is_dup2, a2_proc = engine.process_alert(
        a2, primary_entity_id="cust_alpha", now=now + timedelta(seconds=30)
    )
    assert is_dup2
    assert a2_proc.dedup_count == 2
    assert a2_proc.is_duplicate
    assert a2_proc.dedup_key == a1_proc.dedup_key


def test_sliding_window_deduplication_risk_score_escalation() -> None:
    """Verifies duplicate occurrences escalate risk score and merge reason codes."""
    svc = AlertIntelligenceService(alert_threshold=0.50, dedup_window_seconds=300.0)

    # First attempt: score 600
    t1 = [{"transaction_id": "tx_burst_1", "customer_id": "cust_burst", "velocity": 6.0}]
    a1 = svc.generate_alerts("bank_a", t1, [0.60])[0]
    assert a1.risk_score == 600.0
    assert a1.dedup_count == 1

    # Second attempt: score 720, adds HIGH-AMT
    t2 = [{
        "transaction_id": "tx_burst_2",
        "customer_id": "cust_burst",
        "velocity": 6.0,
        "transaction_amount": 9000,
    }]
    a2 = svc.generate_alerts("bank_a", t2, [0.72])[0]
    assert a2.is_duplicate
    assert a2.dedup_count == 2
    assert a2.risk_score == 720.0
    assert "HIGH-AMT" in a2.reason_codes
    assert "VEL-001" in a2.reason_codes


def test_sliding_window_deduplication_expiration() -> None:
    """Verifies transactions occurring outside window W are treated as fresh alerts."""
    engine = AlertDeduplicationEngine(DeduplicationConfig(window_seconds=60.0))
    t0 = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)

    a1 = Alert(
        bank_id="bank_a",
        transaction_id="tx_old",
        risk_score=700.0,
        reason_codes=["VEL-001"],
        involved_entity_ids=["cust_exp"],
    )
    is_dup1, _ = engine.process_alert(a1, primary_entity_id="cust_exp", now=t0)
    assert not is_dup1

    # Transaction 120s later (> 60s window)
    t1 = t0 + timedelta(seconds=120)
    a2 = Alert(
        bank_id="bank_a",
        transaction_id="tx_fresh",
        risk_score=710.0,
        reason_codes=["VEL-001"],
        involved_entity_ids=["cust_exp"],
    )
    is_dup2, a2_proc = engine.process_alert(a2, primary_entity_id="cust_exp", now=t1)
    assert not is_dup2
    assert a2_proc.dedup_count == 1
    assert not a2_proc.is_duplicate


def test_triage_priority_assignment_multi_factor() -> None:
    """Verifies multi-factor algorithmic triage scoring across severity, amount, and sanctions."""
    triage = AlertTriageEngine()

    # Case 1: Standard Medium Alert
    r1 = triage.evaluate_triage(
        txn={"transaction_amount": 250, "country_code": "US"},
        risk_score=550.0,
        severity=AlertSeverity.MEDIUM,
    )
    assert r1.priority == TriagePriority.P3_MEDIUM
    assert r1.action == TriageAction.QUEUE_STANDARD
    assert r1.sla_minutes == 1440

    # Case 2: High amount ($15,000) escalates P2 to P1
    r2 = triage.evaluate_triage(
        txn={"transaction_amount": 15000.0, "country_code": "US"},
        risk_score=780.0,
        severity=AlertSeverity.HIGH,
    )
    assert r2.priority == TriagePriority.P1_CRITICAL
    assert r2.action == TriageAction.ESCALATE_IMMEDIATE
    assert r2.sla_minutes == 15
    assert any("10,000" in reason for reason in r2.reasons)

    # Case 3: High-risk jurisdiction (NG) escalates P3 to P2
    r3 = triage.evaluate_triage(
        txn={"transaction_amount": 500.0, "country_code": "NG"},
        risk_score=580.0,
        severity=AlertSeverity.MEDIUM,
        reason_codes=["GEO-RISK"],
    )
    assert r3.priority == TriagePriority.P2_HIGH
    assert r3.action == TriageAction.INVESTIGATE_CASE
    assert r3.sla_minutes == 120


def test_triage_priority_escalation_on_burst_velocity() -> None:
    """Verifies duplicate count >= 3 escalates priority to P1_CRITICAL."""
    triage = AlertTriageEngine()

    res = triage.evaluate_triage(
        txn={"transaction_amount": 100.0, "country_code": "US"},
        risk_score=520.0,
        severity=AlertSeverity.MEDIUM,
        dedup_count=3,
    )
    assert res.priority == TriagePriority.P1_CRITICAL
    assert res.action == TriageAction.ESCALATE_IMMEDIATE
    assert res.sla_minutes == 15
    assert any("Burst velocity deduplication attack" in reason for reason in res.reasons)


def test_zero_mock_unknown_alert_returns_404() -> None:
    """Verifies unknown alert ID returns HTTP 404 (zero fake MD5 fallback alerts)."""
    client = TestClient(app)
    resp = client.get("/api/v1/alerts/nonexistent_fake_alert_id_999999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Alert not found"


def test_alert_status_update_endpoint_and_tenant_isolation() -> None:
    """Verifies PATCH /alerts/{id}/status updates status and blocks cross-tenant updates."""
    from app.presentation.routers.alerts import get_alert_service

    svc = get_alert_service()
    txns = [{"transaction_id": "tx_status_test", "customer_id": "c_stat"}]
    created = svc.generate_alerts("bank_a", txns, [0.85])[0]

    client = TestClient(app)

    # Cross-tenant attempt: caller is bank_b but alert belongs to bank_a
    app.dependency_overrides[resolve_tenant] = lambda: "bank_b"
    try:
        resp_forbidden = client.patch(
            f"/api/v1/alerts/{created.id}/status",
            json={"status": "investigating", "resolution_notes": "Cross-tenant unauthorized edit"},
            headers={"x-tenant-id": "bank_b"},
        )
        assert resp_forbidden.status_code == 403
    finally:
        app.dependency_overrides.pop(resolve_tenant, None)

    # Valid tenant attempt: caller is bank_a
    app.dependency_overrides[resolve_tenant] = lambda: "bank_a"
    try:
        resp_ok = client.patch(
            f"/api/v1/alerts/{created.id}/status",
            json={"status": "investigating", "resolution_notes": "Analyst assigned to review"},
            headers={"x-tenant-id": "bank_a"},
        )
        assert resp_ok.status_code == 200
        data = resp_ok.json()
        assert data["status"] == "investigating"
        assert any("Analyst assigned" in rf for rf in data["risk_factors"])
    finally:
        app.dependency_overrides.pop(resolve_tenant, None)


def test_alert_triage_and_dedup_stats_endpoints() -> None:
    """Verifies POST /alerts/{id}/triage and GET /alerts/dedup/stats."""
    from app.presentation.routers.alerts import get_alert_service

    svc = get_alert_service()
    txns = [{"transaction_id": "tx_triage_api", "customer_id": "c_triage"}]
    alert = svc.generate_alerts("bank_a", txns, [0.88])[0]

    client = TestClient(app)
    app.dependency_overrides[resolve_tenant] = lambda: "bank_a"
    try:
        # Triage on-demand
        resp_triage = client.post(
            f"/api/v1/alerts/{alert.id}/triage",
            json={"transaction_amount": 25000.0, "country_code": "RU"},
            headers={"x-tenant-id": "bank_a"},
        )
        assert resp_triage.status_code == 200
        tdata = resp_triage.json()
        assert tdata["triage_priority"] == "p1_critical"
        assert tdata["triage_action"] == "escalate_immediate"
        assert tdata["sla_minutes"] == 15

        # Deduplication stats
        resp_stats = client.get("/api/v1/alerts/dedup/stats")
        assert resp_stats.status_code == 200
        sdata = resp_stats.json()
        assert "total_processed" in sdata
        assert "duplicates_detected" in sdata
        assert "deduplication_ratio" in sdata
        assert "active_sliding_window_keys" in sdata
    finally:
        app.dependency_overrides.pop(resolve_tenant, None)


def test_alert_service_thread_concurrency() -> None:
    """Verifies concurrent alert processing and deduplication under 20 threads."""
    svc = AlertIntelligenceService(alert_threshold=0.50, dedup_window_seconds=300.0)

    def worker(i: int) -> Alert:
        # Same customer_id to stress deduplication under concurrency
        txn = {
            "transaction_id": f"tx_concurrent_{i}",
            "customer_id": "cust_shared_stress",
            "transaction_amount": 500 + i * 10,
            "velocity": 5.5,
        }
        return svc.generate_alerts("bank_a", [txn], [0.80])[0]

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(worker, i) for i in range(20)]
        results = [f.result() for f in futures]

    assert len(results) == 20
    # Exactly one should be initial (count 1), and remaining 19 should be duplicates
    counts = [r.dedup_count for r in results]
    assert 1 in counts
    assert max(counts) == 20

    stats = svc.get_dedup_stats()
    assert stats["total_processed"] == 20
    assert stats["duplicates_detected"] == 19
    assert stats["deduplication_ratio"] == 0.95
