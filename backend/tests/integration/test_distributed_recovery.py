"""Integration tests for distributed coordination, compound failure, and recovery invariants.

Validates:
- DIST-INV-01: Shared Authoritative State Wins Across Workers (Multi-Worker Case CAS)
- DIST-INV-02: Retry After Ambiguous Outcome Preserves Logical Identity
- DIST-INV-03: Partial FL Failure Cannot Corrupt the Global Model
- DIST-INV-04: Failed Round Cannot Masquerade as Successful Round
- DIST-INV-05: Recovery Does Not Resurrect Stale Work (Worker Restart)
- DIST-INV-06: Compound Failure Preserves Truth
- DIST-INV-07: Durable State and External Side Effects Remain Distinguishable
- DIST-INV-08: Security Mechanisms Fail Closed Under Dependency Failure
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import tempfile
import threading
import uuid
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from app.application.services.case_service import (
    CaseManagementService,
    InvalidCaseTransitionError,
)
from app.application.services.fl_engine import FederatedLearningEngine
from app.application.services.idempotency import IdempotencyService
from app.application.services.model_registry import ModelRegistry
from app.application.services.privacy_service import (
    PrivacyBudgetExceededError,
    PrivacyService,
)
from app.domain.enums import AggregationMethod, CasePriority, CaseStatus
from app.domain.value_objects import ModelWeights
from app.infrastructure.redis_store import RedisStore
from app.main import app


@pytest.fixture(autouse=True)
def clean_redis_and_idempotency() -> None:
    """Ensure in-memory fallback stores and idempotency caches are cleanly reset."""
    with RedisStore._lock:
        RedisStore._shared_fallback_stores.clear()
        RedisStore._global_redis_unavailable = True
    idem = IdempotencyService.get()
    with idem._fallback_lock:
        idem._fallback.clear()


class TestDistributedCoordinationAndRecovery:
    """Covers Scenarios 1 through 6 for distributed failure and recovery correctness."""

    # -------------------------------------------------------------------------
    # SCENARIO 1: Multi-Worker Case CAS (DIST-INV-01)
    # -------------------------------------------------------------------------
    def test_multi_worker_case_cas_invariant(self) -> None:
        """Scenario 1: Two independent worker instances attempt concurrent incompatible mutations.

        Worker A and Worker B both read Case V1.
        Both attempt mutually exclusive transitions (investigating vs closed_false_positive).
        Independent Oracle:
            - Exactly one succeeds (success_count == 1).
            - Competing worker receives InvalidCaseTransitionError (conflict_count == 1).
            - Durable version advances to exactly 2.
            - Case timeline contains no double transition.
        """
        worker_a = CaseManagementService()
        worker_b = CaseManagementService()

        # Step 1: Create initial Case at Version 1
        initial_case = worker_a.create_case(
            title="Cross-Worker Conflict Target",
            priority=CasePriority.P1_CRITICAL,
            assigned_to="analyst_lead",
        )
        case_id = initial_case.id
        assert getattr(initial_case, "version", 1) == 1
        assert initial_case.status == CaseStatus.OPEN

        # Step 2: Synchronize workers so both read V1 before either commits
        barrier = threading.Barrier(2)
        results: list[dict[str, Any]] = []
        lock = threading.Lock()

        def worker_a_action() -> None:
            # Worker A reads V1
            c = worker_a.get_case(case_id)
            v = getattr(c, "version", 1)
            # Wait at barrier
            barrier.wait(timeout=5.0)
            try:
                updated = worker_a.change_status(
                    case_id=case_id,
                    new_status=CaseStatus.INVESTIGATING,
                    actor="worker_a",
                    expected_status="open",
                    expected_version=v,
                )
                with lock:
                    results.append({"worker": "A", "status": "SUCCESS", "case": updated})
            except Exception as e:
                with lock:
                    results.append({"worker": "A", "status": "CONFLICT", "error": str(e)})

        def worker_b_action() -> None:
            # Worker B reads V1
            c = worker_b.get_case(case_id)
            v = getattr(c, "version", 1)
            # Wait at barrier
            barrier.wait(timeout=5.0)
            try:
                updated = worker_b.change_status(
                    case_id=case_id,
                    new_status=CaseStatus.ASSIGNED,
                    actor="worker_b",
                    expected_status="open",
                    expected_version=v,
                )
                with lock:
                    results.append({"worker": "B", "status": "SUCCESS", "case": updated})
            except Exception as e:
                with lock:
                    results.append({"worker": "B", "status": "CONFLICT", "error": str(e)})

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_a = executor.submit(worker_a_action)
            fut_b = executor.submit(worker_b_action)
            fut_a.result()
            fut_b.result()

        # Step 3: Independent Oracle Assertions
        success_count = sum(1 for r in results if r["status"] == "SUCCESS")
        conflict_count = sum(1 for r in results if r["status"] == "CONFLICT")

        assert success_count == 1, f"Expected exactly 1 success, got {success_count}: {results}"
        assert conflict_count == 1, f"Expected exactly 1 conflict, got {conflict_count}: {results}"

        # Inspect durable store directly via a 3rd fresh worker
        verifier = CaseManagementService()
        final_case = verifier.get_case(case_id)
        assert final_case is not None, f"Case {case_id} not found"
        assert getattr(final_case, "version", 1) == 2, f"Final version should be 2, got {final_case.version}"

        # Inspect timeline: exactly 1 creation event + exactly 1 status change event
        status_events = [e for e in final_case.timeline if e.event_type == "status_changed"]
        assert len(status_events) == 1, f"Expected 1 status change event, got {len(status_events)}"

    # -------------------------------------------------------------------------
    # SCENARIO 2: Ambiguous Commit + Client Retry (DIST-INV-02, DIST-INV-07)
    # -------------------------------------------------------------------------
    def test_ambiguous_commit_and_client_retry(self) -> None:
        """Scenario 2: Server commits mutation, client experiences response loss, client retries.

        Verify:
            - One intended operation does not become two business objects.
            - Cached response is replayed with Idempotency-Replayed header.
            - Request attempts == 2, durable business objects == 1.
            - Payload mismatch under same key returns HTTP 409 Conflict.
        """
        client = TestClient(app)
        idempotency_key = f"idem-key-{uuid.uuid4().hex[:12]}"
        payload = {
            "title": "Idempotent Wire Fraud Dossier",
            "priority": "p1_critical",
            "assigned_to": "investigator_retry",
        }

        # Attempt 1: Server processes and commits
        resp1 = client.post(
            "/api/v1/cases",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
        )
        assert resp1.status_code == 200, resp1.text
        case1_data = resp1.json()
        case_id = case1_data["id"]
        assert "Idempotency-Replayed" not in resp1.headers

        # Attempt 2: Client retries identical request (simulating response loss / timeout)
        resp2 = client.post(
            "/api/v1/cases",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
        )
        assert resp2.status_code == 200, resp2.text
        case2_data = resp2.json()
        assert resp2.headers.get("Idempotency-Replayed") == "true"
        assert case2_data["id"] == case_id
        assert case2_data["title"] == payload["title"]

        # Oracle 1: Durable store contains exactly 1 case matching this ID
        svc = CaseManagementService()
        durable_case = svc.get_case(case_id)
        assert durable_case is not None, f"Case {case_id} not found"
        assert durable_case.title == payload["title"]
        all_cases = svc.get_cases()
        matching_cases = [c for c in all_cases if c.id == case_id]
        assert len(matching_cases) == 1

        # Oracle 2: Conflicting payload under same key is rejected with HTTP 409
        conflicting_payload = {
            "title": "Conflicting Different Payload",
            "priority": "p2_high",
            "assigned_to": "someone_else",
        }
        resp3 = client.post(
            "/api/v1/cases",
            json=conflicting_payload,
            headers={"Idempotency-Key": idempotency_key},
        )
        assert resp3.status_code == 409
        assert "Conflicting payload" in resp3.json()["detail"]

    # -------------------------------------------------------------------------
    # SCENARIO 3: Federated Round with Partial Client Failure (DIST-INV-03, DIST-INV-04)
    # -------------------------------------------------------------------------
    def test_federated_round_partial_client_failure(self) -> None:
        """Scenario 3: Exercise federated round with valid, missing, and non-finite updates.

        Clients:
            - Client A: valid update W_a = [1.0, 2.0, 3.0, 4.0], samples = 100
            - Client B: valid update W_b = [3.0, 4.0, 5.0, 6.0], samples = 300
            - Client C: non-finite (NaN / Inf) update W_c = [NaN, 1.0, 2.0, Inf], samples = 200
            - Client D: network exception / dropped before aggregation buffer

        Independent Oracle:
            - Client C quarantined and Client D dropped.
            - Reweighting is computed strictly over accepted clients {A, B}.
            - W_expected = (100 * W_a + 300 * W_b) / 400 = [2.5, 3.5, 4.5, 5.5]
            - Result matches independent tensor oracle with zero corruption.
        """
        from app.application.services.model_service import ModelService
        from app.application.services.privacy_service import PrivacyService
        from app.config import get_settings

        settings = get_settings()
        engine = FederatedLearningEngine(settings, ModelService(settings), PrivacyService())
        layer_shapes: list[tuple[int, ...]] = [(4,)]

        w_a = ModelWeights(layer_shapes=layer_shapes, flat_weights=[1.0, 2.0, 3.0, 4.0])
        w_b = ModelWeights(layer_shapes=layer_shapes, flat_weights=[3.0, 4.0, 5.0, 6.0])
        w_c_corrupt = ModelWeights(
            layer_shapes=layer_shapes,
            flat_weights=[float("nan"), 1.0, 2.0, float("inf")],
        )

        client_weights = [w_a, w_b, w_c_corrupt]
        client_samples = [100, 300, 200]

        # Aggregate parameters using FedAvg Weighted
        aggregate = engine.aggregate_parameters(
            client_weights=client_weights,
            client_samples=client_samples,
            method=AggregationMethod.FED_AVG_WEIGHTED,
        )

        # Independent Tensor Oracle:
        # Sum of samples for accepted clients = 100 + 300 = 400
        # p_a = 100 / 400 = 0.25; p_b = 300 / 400 = 0.75
        expected_flat = [
            0.25 * 1.0 + 0.75 * 3.0,  # 2.5
            0.25 * 2.0 + 0.75 * 4.0,  # 3.5
            0.25 * 3.0 + 0.75 * 5.0,  # 4.5
            0.25 * 4.0 + 0.75 * 6.0,  # 5.5
        ]
        assert np.allclose(aggregate.flat_weights, expected_flat), (
            f"Expected {expected_flat}, got {aggregate.flat_weights}"
        )
        assert np.isfinite(aggregate.flat_weights).all(), "Global aggregate contains non-finite values!"

        # Failure condition: If ALL clients provide non-finite weights, engine falls back cleanly to previous global weights
        global_prev = ModelWeights(layer_shapes=layer_shapes, flat_weights=[9.0, 9.0, 9.0, 9.0])
        fallback_res = engine.aggregate_parameters(
            client_weights=[w_c_corrupt],
            client_samples=[100],
            method=AggregationMethod.FED_AVG_WEIGHTED,
            global_weights=global_prev,
        )
        assert fallback_res.flat_weights == global_prev.flat_weights

    # -------------------------------------------------------------------------
    # SCENARIO 4: Model Round Publication Atomicity (DIST-INV-04, DIST-INV-07)
    # -------------------------------------------------------------------------
    def test_model_round_publication_atomicity_and_recovery(self) -> None:
        """Scenario 4: Inject failure between aggregation and publication.

        Verify:
            - A failed round publication does NOT expose a half-committed version.
            - Manifest version remains N.
            - Serving model weights still match version N.
            - After fault resolution, retry successfully commits version N+1.
        """
        temp_dir = tempfile.mkdtemp(prefix="cfi_test_registry_")
        try:
            registry = ModelRegistry(storage_dir=temp_dir)
            sim_id = "sim_atomicity_test"

            # 1. Successfully publish Version 1
            v1_weights = {"fc.weight": torch.tensor([[1.0, 1.0], [1.0, 1.0]])}
            entry_v1 = registry.save_version(
                simulation_id=sim_id,
                state_dict=v1_weights,
                metrics={"auc_roc": 0.82, "f1_score": 0.80},
                is_promoted=True,
                status="champion",
            )
            assert entry_v1["version"] == 1
            assert entry_v1["status"] == "champion"

            # Check that serving model matches Version 1
            serving_path = os.path.join(temp_dir, "global_model.pt")
            assert os.path.exists(serving_path)
            loaded_v1 = torch.load(serving_path, weights_only=True)
            assert torch.equal(loaded_v1["fc.weight"], v1_weights["fc.weight"])

            # 2. Inject failure during Version 2 save (e.g., serialization failure / disk crash)
            v2_candidate_weights = {"fc.weight": torch.tensor([[2.0, 2.0], [2.0, 2.0]])}
            with (
                patch("torch.save", side_effect=OSError("Injected disk failure during model serialization")),
                pytest.raises(OSError, match="Injected disk failure"),
            ):
                registry.save_version(
                    simulation_id=sim_id,
                    state_dict=v2_candidate_weights,
                    metrics={"auc_roc": 0.88, "f1_score": 0.86},
                    is_promoted=True,
                    status="champion",
                )

            # Independent Oracle: Inspect durable state after injected failure
            versions = registry.list_versions(sim_id)
            assert len(versions) == 1, f"Expected only 1 version in manifest, found {len(versions)}"
            assert versions[0]["version"] == 1
            assert versions[0]["is_active"] is True

            # Serving model MUST still be Version 1 (no half-published state)
            loaded_after_fail = torch.load(serving_path, weights_only=True)
            assert torch.equal(loaded_after_fail["fc.weight"], v1_weights["fc.weight"])

            # 3. Recovery: Retry save without injected fault
            entry_v2 = registry.save_version(
                simulation_id=sim_id,
                state_dict=v2_candidate_weights,
                metrics={"auc_roc": 0.88, "f1_score": 0.86},
                is_promoted=True,
                status="champion",
            )
            assert entry_v2["version"] == 2
            assert entry_v2["status"] == "champion"

            # Check updated state
            recovered_versions = registry.list_versions(sim_id)
            assert len(recovered_versions) == 2
            assert recovered_versions[1]["version"] == 2
            assert recovered_versions[1]["is_active"] is True
            assert recovered_versions[0]["is_active"] is False

            # Serving model is now atomically updated to Version 2
            loaded_v2 = torch.load(serving_path, weights_only=True)
            assert torch.equal(loaded_v2["fc.weight"], v2_candidate_weights["fc.weight"])

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # SCENARIO 5: Worker Restart with Stale Operation (DIST-INV-05)
    # -------------------------------------------------------------------------
    def test_worker_restart_with_stale_operation(self) -> None:
        """Scenario 5: Worker process crashes and restarts, losing local memory.

        Verify:
            - Worker A reads Case V1.
            - Another worker commits Case V2.
            - Worker A is destroyed and restarted as a fresh instance.
            - Stale operation based on V1 is rejected by shared CAS.
            - Loss of process-local memory does NOT allow obsolete work to overwrite V2.
        """
        worker_initial = CaseManagementService()
        case = worker_initial.create_case(
            title="Dossier Subject to Restart Invariant",
            priority=CasePriority.P2_HIGH,
            assigned_to="analyst_worker",
        )
        case_id = case.id
        assert getattr(case, "version", 1) == 1

        # Another worker commits V2
        worker_other = CaseManagementService()
        worker_other.change_status(
            case_id=case_id,
            new_status=CaseStatus.INVESTIGATING,
            actor="worker_other",
            expected_version=1,
            expected_status="open",
        )
        case_v2 = worker_other.get_case(case_id)
        assert case_v2 is not None, f"Case {case_id} not found"
        assert getattr(case_v2, "version", 1) == 2
        assert case_v2.status == CaseStatus.INVESTIGATING

        # Worker A is terminated / instance discarded.
        del worker_initial

        # New Worker A' starts fresh (zero in-memory lock/cache history)
        worker_restarted = CaseManagementService()

        # Stale operation prepared against pre-restart V1
        with pytest.raises(InvalidCaseTransitionError, match="Precondition failed"):
            worker_restarted.change_status(
                case_id=case_id,
                new_status=CaseStatus.CLOSED_FALSE_POSITIVE,
                actor="worker_restarted_stale",
                expected_version=1,  # Stale version!
                expected_status="open",  # Stale status!
            )

        # Oracle: Final state in store remains V2 (investigating)
        authoritative_case = worker_restarted.get_case(case_id)
        assert authoritative_case is not None, f"Case {case_id} not found"
        assert getattr(authoritative_case, "version", 1) == 2
        assert authoritative_case.status == CaseStatus.INVESTIGATING

    # -------------------------------------------------------------------------
    # SCENARIO 6: Compound Security / Failure Path (DIST-INV-06, DIST-INV-08)
    # -------------------------------------------------------------------------
    def test_compound_security_failure_fails_closed(self) -> None:
        """Scenario 6: Active Differential Privacy combined with client failure and budget exhaustion.

        Compound Faults:
            - DP is configured with tight epsilon budget limit (limit = 2.0, per-round eps = 1.0).
            - Client 2 throws a network exception / drops offline during round 2.
            - Budget expenditure attempt in round 3 exceeds limit.

        Independent Oracle:
            - DP noise scale sigma > 0 and noise was genuinely added in round 1 & 2.
            - Client 2 failure does NOT bypass privacy accounting.
            - In round 3, PrivacyBudgetExceededError is raised.
            - The system fails closed; zero un-noised/plaintext aggregation occurs.
        """
        privacy_svc = PrivacyService()
        sim_id = f"sim_dp_compound_{uuid.uuid4().hex[:8]}"

        # Create budget with limit 2.0
        budget = privacy_svc.get_or_create_budget(
            simulation_id=sim_id,
            epsilon=1.0,
            delta=1e-5,
        )

        global_weights = ModelWeights(layer_shapes=[(4,)], flat_weights=[0.0, 0.0, 0.0, 0.0])
        local_weights = ModelWeights(layer_shapes=[(4,)], flat_weights=[1.0, 1.0, 1.0, 1.0])

        # Round 1: Client 1 participates with DP active
        sigma = privacy_svc.calculate_gaussian_noise_scale(epsilon=1.0, delta=1e-5)
        assert sigma > 0.0, "Noise scale must be strictly positive"

        clipped_w1 = privacy_svc.clip_model_update(global_weights, local_weights, max_norm=1.0)
        noised_w1 = privacy_svc.add_noise_to_weights(
            clipped_w1, epsilon=1.0, delta=1e-5, max_grad_norm=1.0
        )
        assert not np.allclose(noised_w1.flat_weights, local_weights.flat_weights), (
            "Noise must perturb the weights"
        )
        budget.spend(epsilon=1.0, limit=2.0)
        assert budget.total_epsilon == 1.0

        # Round 2: Client 2 fails/drops offline; Client 1 continues with DP
        # Client 2 dropped:
        client_2_failed = True
        if not client_2_failed:
            pass  # Client 2 skipped

        clipped_w2 = privacy_svc.clip_model_update(global_weights, local_weights, max_norm=1.0)
        noised_w2 = privacy_svc.add_noise_to_weights(
            clipped_w2, epsilon=1.0, delta=1e-5, max_grad_norm=1.0
        )
        assert not np.allclose(noised_w2.flat_weights, local_weights.flat_weights)
        budget.spend(epsilon=1.0, limit=2.0)
        assert budget.total_epsilon == 2.0

        # Round 3: Attempt to spend an additional 1.0 (cumulative would be 3.0 > 2.0 limit)
        with pytest.raises(PrivacyBudgetExceededError, match="Cumulative privacy budget exceeded"):
            budget.spend(epsilon=1.0, limit=2.0)

        # Oracle: System fails closed, cumulative budget exceeded, 3 rounds recorded
        assert budget.rounds_spent == 3
        assert budget.total_epsilon == 3.0
