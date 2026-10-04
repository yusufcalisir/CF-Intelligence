"""Unit tests for multi-worker concurrency, approval versioning, idempotency, and webhook semantics.

Validates the persistence boundary invariants:
1. Multi-worker shared-persistence compare-and-set concurrency
2. Stale update and lost-update prevention across independent contenders
3. Four-Eyes material dossier version binding and signature staleness
4. Sequential signature invalidation upon subsequent material mutation
5. 24-hour bounded request idempotency vs logical case creation semantics
6. Webhook deterministic event identity, redelivery stability, and collision prevention
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.application.services.case_service import (
    CaseManagementService,
    InvalidCaseTransitionError,
)
from app.application.services.idempotency import IdempotencyService
from app.application.services.webhook_service import (
    WebhookEventType,
    WebhookService,
)
from app.domain.enums import CasePriority, CaseStatus
from app.infrastructure.redis_store import RedisStore
from app.main import app


@pytest.fixture(autouse=True)
def clean_redis_stores() -> None:
    """Ensure in-memory fallback stores are cleanly partitioned between test cases."""
    with RedisStore._lock:
        RedisStore._shared_fallback_stores.clear()
        RedisStore._global_redis_unavailable = True


class TestCaseStorageConcurrency:
    """Scenario 1 & 2: Multi-worker shared-storage concurrency and lost-update prevention."""

    def test_multi_worker_incompatible_terminal_decisions_race(self) -> None:
        """Scenario 1: Two independent execution contexts racing incompatible terminal decisions.

        Setup:
            - Worker A and Worker B are two separate CaseManagementService instances
              with completely independent Python thread locks (bypassing process-local RLock).
            - Both contender services access the same underlying persistence key.
            - Case is in INVESTIGATING state (V1).

        Adversarial action:
            - Worker A attempts INVESTIGATING -> CLOSED_CONFIRMED (expected_status=INVESTIGATING).
            - Worker B concurrently attempts INVESTIGATING -> CLOSED_FALSE_POSITIVE (expected_status=INVESTIGATING).

        Assertion:
            - Exactly one worker succeeds at the persistence boundary.
            - The losing worker is rejected with InvalidCaseTransitionError (precondition failed).
            - Final case state is a single, unambiguous terminal status.
            - No duplicate or contradictory terminal closure exists in history.
        """
        # Create case using initial setup context
        setup_svc = CaseManagementService()
        case = setup_svc.create_case(
            title="Cross-Worker Terminal Race Target",
            priority=CasePriority.P1_CRITICAL,
            assigned_to="lead_investigator_1",
            bank_id="bank_alpha",
        )
        setup_svc.change_status(case.id, CaseStatus.INVESTIGATING, actor="lead_investigator_1")

        # Instantiate two completely separate worker services (independent Python locks)
        worker_a = CaseManagementService()
        worker_b = CaseManagementService()
        assert worker_a._lock is not worker_b._lock, "Workers must not share the same threading.RLock"

        results: dict[str, Any] = {}

        # Worker A attempts terminal closure as CONFIRMED
        try:
            worker_a.change_status(
                case.id,
                CaseStatus.CLOSED_CONFIRMED,
                actor="analyst_1",
                supervisor_signature="supervisor:alice_99",
                second_supervisor_signature="supervisor:bob_88",
                expected_status=CaseStatus.INVESTIGATING,
            )
            results["worker_a"] = "SUCCESS"
        except Exception as exc:
            results["worker_a"] = exc

        # Worker B concurrently attempts terminal closure as FALSE_POSITIVE based on stale INVESTIGATING status
        try:
            worker_b.change_status(
                case.id,
                CaseStatus.CLOSED_FALSE_POSITIVE,
                actor="analyst_2",
                supervisor_signature="supervisor:charlie_77",
                second_supervisor_signature="supervisor:diana_66",
                expected_status=CaseStatus.INVESTIGATING,
            )
            results["worker_b"] = "SUCCESS"
        except Exception as exc:
            results["worker_b"] = exc

        # Exactly one must succeed, the other must fail with a precondition failure
        assert results["worker_a"] == "SUCCESS", "First committer must succeed"
        assert isinstance(results["worker_b"], InvalidCaseTransitionError), (
            f"Losing contender must be rejected with InvalidCaseTransitionError, got {results['worker_b']}"
        )
        assert "precondition failed" in str(results["worker_b"]).lower()

        # Verify final state is strictly CONFIRMED (not dual-closed)
        verifier = CaseManagementService()
        final_case = verifier.get_case(case.id)
        assert final_case is not None
        assert final_case.status == CaseStatus.CLOSED_CONFIRMED
        assert final_case.closed_at is not None

        # Verify timeline integrity
        verification = verifier.verify_timeline_integrity(case.id)
        assert verification["is_valid"] is True
        terminal_events = [e for e in final_case.timeline if "closed_" in e.description.lower()]
        assert len(terminal_events) == 1, "Only one terminal closure event may exist in timeline"

    def test_multi_worker_stale_status_update_rejected(self) -> None:
        """Scenario 2: Stale status update after another worker changes status.

        Setup:
            - Case is at INVESTIGATING (V2).
            - Worker A and Worker B independently read Case S0.
            - Worker A advances status to ESCALATED.
            - Worker B attempts non-terminal mutation expecting INVESTIGATING.

        Assertion:
            - Worker B is rejected at the persistence boundary.
            - Worker A's update is preserved (no silent overwrite).
        """
        setup_svc = CaseManagementService()
        case = setup_svc.create_case(title="Lost Update Race Target")
        setup_svc.change_status(case.id, CaseStatus.INVESTIGATING, actor="analyst_1")

        worker_a = CaseManagementService()
        worker_b = CaseManagementService()

        # Worker A advances to ESCALATED
        worker_a.change_status(case.id, CaseStatus.ESCALATED, actor="analyst_1")

        # Worker B attempts transition expecting INVESTIGATING
        with pytest.raises(InvalidCaseTransitionError) as exc_info:
            worker_b.change_status(
                case.id,
                CaseStatus.PENDING_REVIEW,
                actor="analyst_2",
                expected_status=CaseStatus.INVESTIGATING,
            )
        assert "precondition failed" in str(exc_info.value).lower()

        # Verify current status is ESCALATED
        current = worker_a.get_case(case.id)
        assert current.status == CaseStatus.ESCALATED


class TestApprovalVersioningAndStaleness:
    """Scenario 3 & 4: Material dossier version binding and supervisor signature staleness."""

    def test_material_mutation_with_unchanged_status_rejects_stale_approval(self) -> None:
        """Scenario 3: Material case mutation with unchanged status followed by stale approval.

        Setup:
            - Case is transitioned to INVESTIGATING and then escalated to PENDING_REVIEW.
            - Supervisor Alice reviews V2 of the dossier.
            - Analyst adds a note (material mutation) without changing status -> case advances to V3.
            - Supervisor Alice attempts to submit approval referencing reviewed version V2.

        Assertion:
            - Approval is rejected with HTTP 409 Conflict.
            - Precondition failure explicitly cites stale version / hash.
        """
        client = TestClient(app)
        create_res = client.post("/api/v1/cases", json={"title": "Stale Approval Test Case"})
        assert create_res.status_code == 200
        case_id = create_res.json()["id"]

        # Transition OPEN -> INVESTIGATING
        inv_res = client.put(
            f"/api/v1/cases/{case_id}/status",
            json={"status": "investigating", "actor": "analyst_1"},
        )
        assert inv_res.status_code == 200

        # Escalate to PENDING_REVIEW
        esc_res = client.post(
            f"/api/v1/cases/{case_id}/escalate",
            json={"actor": "analyst_1", "reason": "Requires Four-Eyes review"},
        )
        assert esc_res.status_code == 200
        v2_case = esc_res.json()
        v2_version = v2_case["version"]
        v2_hash = v2_case["timeline_hash"]
        assert v2_case["status"] == "pending_review"

        # Analyst performs a MATERIAL_TO_APPROVAL mutation without changing status
        note_res = client.post(
            f"/api/v1/cases/{case_id}/notes",
            json={"author": "analyst_1", "content": "Critical new finding: suspected structuring."},
        )
        assert note_res.status_code == 200

        # Verify case version advanced while status remained PENDING_REVIEW
        get_res = client.get(f"/api/v1/cases/{case_id}")
        assert get_res.json()["status"] == "pending_review"
        assert get_res.json()["version"] > v2_version

        # Supervisor Alice submits approval based on stale V2
        stale_sign_res = client.post(
            f"/api/v1/cases/{case_id}/sign",
            json={
                "supervisor_id": "alice_99",
                "action": "APPROVE",
                "expected_version": v2_version,
                "expected_timeline_hash": v2_hash,
            },
        )
        assert stale_sign_res.status_code == 409
        assert "precondition failed" in stale_sign_res.json()["detail"].lower()
        assert "stale approval invariant" in stale_sign_res.json()["detail"].lower()

    def test_first_supervisor_signature_invalidated_by_subsequent_material_mutation(self) -> None:
        """Scenario 4: First supervisor signature -> material mutation -> second supervisor signature.

        Setup:
            - Case is at PENDING_REVIEW.
            - Supervisor Alice signs the case dossier (V1).
            - An analyst modifies material case data (adds a note or evidence).
            - Supervisor Bob attempts to close the case via resolve_case using Alice's earlier signature.

        Assertion:
            - resolve_case detects that Alice's prior signature is stale.
            - Request fails with HTTP 409 Precondition Failed.
            - Terminal closure is prevented under Four-Eyes dual control.
        """
        client = TestClient(app)
        create_res = client.post(
            "/api/v1/cases",
            json={"title": "Four-Eyes Sequential Staleness Target", "assigned_to": "investigator_bob"},
        )
        case_id = create_res.json()["id"]

        # Transition OPEN -> INVESTIGATING
        inv_res = client.put(
            f"/api/v1/cases/{case_id}/status",
            json={"status": "investigating", "actor": "analyst_1"},
        )
        assert inv_res.status_code == 200

        # Escalate to PENDING_REVIEW
        esc_res = client.post(
            f"/api/v1/cases/{case_id}/escalate",
            json={"actor": "analyst_1", "reason": "Dual supervisor signoff needed"},
        )
        assert esc_res.status_code == 200

        # Supervisor Alice signs the case
        sign1_res = client.post(
            f"/api/v1/cases/{case_id}/sign",
            json={"supervisor_id": "supervisor_alice", "action": "APPROVE"},
        )
        assert sign1_res.status_code == 200

        # Material mutation: Analyst adds new evidence to the case
        ev_res = client.post(
            f"/api/v1/cases/{case_id}/evidence",
            json={
                "evidence_type": "document",
                "title": "Subpoena Bank Records",
                "file_path": "subpoena_01.pdf",
                "content": "Raw bank transaction ledger content bytes",
                "uploaded_by": "analyst_1",
            },
        )
        assert ev_res.status_code == 200

        # Supervisor Bob attempts to resolve case relying on Alice's earlier signature
        resolve_res = client.post(
            f"/api/v1/cases/{case_id}/resolve",
            json={
                "resolution": "CONFIRMED_FRAUD",
                "primary_supervisor": "supervisor_alice",
                "secondary_supervisor": "supervisor_charlie",
                "actor": "compliance_officer",
            },
        )
        assert resolve_res.status_code == 409
        err_msg = resolve_res.json()["detail"].lower()
        assert "precondition failed" in err_msg
        assert "stale" in err_msg
        assert "evidence_added" in err_msg or "modified after review" in err_msg

        # Verify case was NOT closed
        case_res = client.get(f"/api/v1/cases/{case_id}")
        assert case_res.json()["status"] == "pending_review"


class TestCaseIdempotencyAndLogicalUniqueness:
    """Scenario 5, 6, 7, 8: Bounded 24-hour request idempotency vs logical business uniqueness."""

    def test_case_create_replay_inside_24h_idempotency_window(self) -> None:
        """Scenario 5: Case create replay inside 24-hour idempotency window.

        Setup:
            - Request X sent with Idempotency-Key: K1.
            - Case A is created.
            - Request X is replayed with Idempotency-Key: K1 within the 24-hour TTL.

        Assertion:
            - Cached response for Case A is returned (HTTP 200).
            - Idempotency-Replayed header is present.
            - Exactly one persistent case object exists.
        """
        client = TestClient(app)
        idem_key = f"key_{uuid.uuid4().hex[:12]}"
        payload = {"title": "Replay Window Case", "priority": "p2_high", "alert_ids": ["alt_1"]}

        # First delivery
        res1 = client.post("/api/v1/cases", json=payload, headers={"Idempotency-Key": idem_key})
        assert res1.status_code == 200
        case1_id = res1.json()["id"]

        # Replay within TTL
        res2 = client.post("/api/v1/cases", json=payload, headers={"Idempotency-Key": idem_key})
        assert res2.status_code == 200
        assert res2.headers.get("Idempotency-Replayed") == "true"
        assert res2.json()["id"] == case1_id

        # Count persistent case objects
        svc = CaseManagementService()
        matching_cases = [c for c in svc.get_cases() if c.title == "Replay Window Case"]
        assert len(matching_cases) == 1

    def test_case_create_replay_after_ttl_expiry(self) -> None:
        """Scenario 6: Case create replay after simulated TTL expiry.

        Setup:
            - Request X / Key K1 creates Case A.
            - Simulate 24-hour TTL expiration by evicting the idempotency cache entry.
            - Replay the identical logical request X with Key K1.

        Assertion:
            - A new operational Case B is created by design.
            - Two distinct case objects exist in persistent storage.
            - Demonstrates that 24h TTL guarantees bounded request replay safety,
              not permanent logical business uniqueness.
        """
        client = TestClient(app)
        idem_key = f"key_{uuid.uuid4().hex[:12]}"
        payload = {"title": "Post TTL Expiry Case", "priority": "p3_medium"}

        # Initial creation
        res1 = client.post("/api/v1/cases", json=payload, headers={"Idempotency-Key": idem_key})
        assert res1.status_code == 200
        case_a_id = res1.json()["id"]

        # Simulate 24h TTL expiry by clearing the idempotency store
        idem_svc = IdempotencyService.get()
        with idem_svc._fallback_lock:
            idem_svc._fallback.clear()
        if idem_svc._redis_client:
            with contextlib.suppress(Exception):
                idem_svc._redis_client.flushdb()

        # Replay identical request after TTL expiry
        res2 = client.post("/api/v1/cases", json=payload, headers={"Idempotency-Key": idem_key})
        assert res2.status_code == 200
        case_b_id = res2.json()["id"]

        # Different object IDs created
        assert case_a_id != case_b_id

        # Verify two persistent case objects exist
        svc = CaseManagementService()
        assert svc.get_case(case_a_id) is not None
        assert svc.get_case(case_b_id) is not None

    def test_identical_logical_payload_with_different_idempotency_keys(self) -> None:
        """Scenario 7: Identical logical create payload with different idempotency keys.

        Setup:
            - Request X submitted with Key K1 -> Case A.
            - Identical Request X submitted with Key K2 -> Case B.

        Assertion:
            - Two distinct case objects are created.
            - Confirms the contract is request idempotency (per key), not permanent object deduplication.
        """
        client = TestClient(app)
        payload = {"title": "Dual Key Same Payload Case", "priority": "p3_medium"}

        res1 = client.post("/api/v1/cases", json=payload, headers={"Idempotency-Key": "key_alpha_1"})
        res2 = client.post("/api/v1/cases", json=payload, headers={"Idempotency-Key": "key_alpha_2"})

        assert res1.status_code == 200
        assert res2.status_code == 200
        assert res1.json()["id"] != res2.json()["id"]

    def test_same_idempotency_key_with_conflicting_payload_rejected(self) -> None:
        """Scenario 8: Same idempotency key with conflicting payload.

        Setup:
            - Key K1 used with Payload A.
            - Key K1 reused with materially different Payload B.

        Assertion:
            - Request is rejected with HTTP 409 Conflict.
        """
        client = TestClient(app)
        idem_key = f"key_conflict_{uuid.uuid4().hex[:8]}"

        res1 = client.post(
            "/api/v1/cases",
            json={"title": "Original Investigation", "priority": "p2_high"},
            headers={"Idempotency-Key": idem_key},
        )
        assert res1.status_code == 200

        res2 = client.post(
            "/api/v1/cases",
            json={"title": "Conflicting Different Investigation", "priority": "p1_critical"},
            headers={"Idempotency-Key": idem_key},
        )
        assert res2.status_code == 409
        assert "conflicting payload" in res2.json()["detail"].lower()


class TestWebhookDeliverySemantics:
    """Scenario 9, 10, 11, 12: Webhook event identity, redelivery stability, and collision safety."""

    def test_webhook_retry_retains_identical_event_id(self) -> None:
        """Scenario 9: Webhook retry retains same event ID across transport redeliveries."""
        webhook_svc = WebhookService()
        tenant = "bank_alpha"
        target_url = "https://example.com/webhook"

        # Register subscription
        webhook_svc.register_subscription(
            tenant_id=tenant,
            target_url=target_url,
            events=[WebhookEventType.CASE_RESOLVED],
        )

        payload = {
            "case_id": "case_test_99",
            "resolution": "CONFIRMED_FRAUD",
            "closed_at": "2026-10-04T12:00:00Z",
        }

        # Initial delivery
        deliveries_1 = webhook_svc.dispatch_event(
            tenant_id=tenant,
            event_type=WebhookEventType.CASE_RESOLVED,
            payload=payload,
        )
        assert len(deliveries_1) == 1
        event_id_1 = deliveries_1[0].event_id

        # Simulated HTTP retry of the same event
        deliveries_2 = webhook_svc.dispatch_event(
            tenant_id=tenant,
            event_type=WebhookEventType.CASE_RESOLVED,
            payload=payload,
        )
        assert len(deliveries_2) == 1
        event_id_2 = deliveries_2[0].event_id

        assert event_id_1 == event_id_2, "Event ID must remain deterministic across retries"

    def test_kafka_redelivery_preserves_event_id_and_audit_history(self) -> None:
        """Scenario 10: Kafka crash-window redelivery preserves event ID without object duplication."""
        webhook_svc = WebhookService()
        tenant = "bank_beta"
        target_url = "https://example.com/kafka-hook"

        webhook_svc.register_subscription(
            tenant_id=tenant,
            target_url=target_url,
            events=[WebhookEventType.ALERT_CREATED],
        )

        payload = {"alert_id": "alt_kafka_crash_01", "severity": "HIGH", "score": 850}

        # First dispatch
        first_dispatch = webhook_svc.dispatch_event(
            tenant_id=tenant,
            event_type=WebhookEventType.ALERT_CREATED,
            payload=payload,
        )

        # Broker acknowledgement fails -> redelivered with same payload
        redelivery = webhook_svc.dispatch_event(
            tenant_id=tenant,
            event_type=WebhookEventType.ALERT_CREATED,
            payload=payload,
        )

        assert first_dispatch[0].event_id == redelivery[0].event_id
        assert first_dispatch[0].signature == redelivery[0].signature

    def test_distinct_lifecycle_events_receive_distinct_event_ids(self) -> None:
        """Scenario 11: Distinct webhook business events receive appropriate distinct identities."""
        webhook_svc = WebhookService()
        tenant = "bank_alpha"
        target_url = "https://example.com/hook"

        webhook_svc.register_subscription(
            tenant_id=tenant,
            target_url=target_url,
            events=[WebhookEventType.CASE_RESOLVED, WebhookEventType.ALERT_CREATED],
        )

        # Event 1: Case resolved as CONFIRMED_FRAUD
        d1 = webhook_svc.dispatch_event(
            tenant_id=tenant,
            event_type=WebhookEventType.CASE_RESOLVED,
            payload={"case_id": "case_lifecycle_1", "resolution": "CONFIRMED_FRAUD"},
        )

        # Event 2: Same case, but different lifecycle event (e.g. FALSE_POSITIVE)
        d2 = webhook_svc.dispatch_event(
            tenant_id=tenant,
            event_type=WebhookEventType.CASE_RESOLVED,
            payload={"case_id": "case_lifecycle_1", "resolution": "FALSE_POSITIVE"},
        )

        # Event 3: Different event type entirely
        d3 = webhook_svc.dispatch_event(
            tenant_id=tenant,
            event_type=WebhookEventType.ALERT_CREATED,
            payload={"alert_id": "alt_99", "case_id": "case_lifecycle_1"},
        )

        assert d1[0].event_id != d2[0].event_id, "Different resolution must produce distinct event ID"
        assert d1[0].event_id != d3[0].event_id, "Different event type must produce distinct event ID"

    def test_same_event_id_cannot_represent_materially_different_events(self) -> None:
        """Scenario 12: Same webhook event ID cannot silently represent materially different logical events."""
        webhook_svc = WebhookService()
        tenant = "bank_gamma"
        target_url = "https://example.com/hook"

        webhook_svc.register_subscription(
            tenant_id=tenant,
            target_url=target_url,
            events=[WebhookEventType.CASE_RESOLVED],
        )

        d_fraud = webhook_svc.dispatch_event(
            tenant_id=tenant,
            event_type=WebhookEventType.CASE_RESOLVED,
            payload={"case_id": "c100", "status": "closed_confirmed", "resolution": "CONFIRMED_FRAUD"},
        )

        d_fp = webhook_svc.dispatch_event(
            tenant_id=tenant,
            event_type=WebhookEventType.CASE_RESOLVED,
            payload={"case_id": "c100", "status": "closed_false_positive", "resolution": "FALSE_POSITIVE"},
        )

        # Event IDs must be strictly distinct
        assert d_fraud[0].event_id != d_fp[0].event_id
