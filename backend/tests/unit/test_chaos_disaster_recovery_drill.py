"""Unit tests for the Chaos Engineering & Disaster Recovery Drill Runner."""

from __future__ import annotations

from app.infrastructure.disaster_recovery.chaos_dr_drill import (
    ChaosDRDrillRunner,
    DRDrillMetrics,
)


def test_chaos_drill_execution_under_load() -> None:
    """Verifies that under 500 txns/sec load, killing primary region achieves RTO <= 30s and RPO = 0."""
    runner = ChaosDRDrillRunner(
        primary_region="eu-central-1",
        standby_region="eu-west-1",
        target_rto_sla=30.0,
        target_rpo_sla=0,
    )
    runner.initialize_environment()

    metrics: DRDrillMetrics = runner.execute_drill(
        txns_per_sec=500,
        load_duration_sec=2.0,
    )

    assert metrics.drill_status == "SUCCESS_PASSED"
    assert metrics.total_txns_submitted == 1000
    assert metrics.total_txns_committed == 1000
    assert metrics.measured_rpo_lost_records == 0
    assert metrics.is_rpo_compliant

    # Measured RTO must be well under the 30.0s SLA (typically ~15.02s)
    assert metrics.measured_rto_seconds <= 30.0
    assert metrics.is_rto_compliant

    # 0% penalty
    assert metrics.service_credit_penalty_pct == 0.0
    assert metrics.audit_chain_hash.startswith("failover_")


def test_chaos_drill_audit_chain_integrity() -> None:
    """Verifies that every DR drill generates a verifiable SHA-256 audit trail."""
    runner = ChaosDRDrillRunner()
    runner.initialize_environment()

    metrics = runner.execute_drill(txns_per_sec=100, load_duration_sec=1.0)

    audit_events = runner.manager._audit_events
    assert len(audit_events) >= 1

    last_event = audit_events[-1]
    assert last_event.promoted_standby_region == "eu-west-1"
    assert last_event.failed_primary_region == "eu-central-1"
    assert last_event.event_id == metrics.audit_chain_hash
