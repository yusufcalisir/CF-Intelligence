"""Unit tests for Phase 11: Concurrency & Race Condition Safety.

Tests:
  1. test_model_promotion_concurrency_single_champion
  2. test_model_registry_concurrent_dual_signoff
  3. test_immutable_audit_chain_concurrent_writers_and_readers
  4. test_tenant_quota_burst_concurrency_atomic_acquire
  5. test_idempotency_store_concurrent_duplicate_in_flight_lock
"""

from __future__ import annotations

import concurrent.futures
import time
from typing import Any

import torch

from app.application.services.idempotency import IdempotencyService
from app.application.services.model_registry import ModelRegistry
from app.application.services.tenant_metering import TenantMeteringService, TenantQuotaLimits
from app.domain.model_governance import (
    ModelRegistryVault,
    ModelStatus,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain


def test_model_promotion_concurrency_single_champion() -> None:
    """Ensure concurrent simultaneous promotion requests maintain single production champion invariant."""
    signing_key = b"test_secret_hsm_key_32_bytes_ok"
    vault = ModelRegistryVault()
    signoffs = [
        {"role": "ml_engineer", "user": "alice", "signature": "sig_alice"},
        {"role": "compliance_officer", "user": "bob", "signature": "sig_bob"},
    ]

    candidates = []
    for i in range(8):
        cp = vault.register_checkpoint(
            version_str=f"v3.{i}.0",
            weights_bytes=f"weights_{i}".encode(),
            hyperparameters={"lr": 0.01},
            dataset_hash=f"hash_{i}",
            dp_epsilon=1.0,
        )
        vault.sign_checkpoint(cp.model_id, signing_key)
        candidates.append(cp.model_id)

    promoted_results = []

    def try_promote(model_id: str) -> None:
        res = vault.promote_to_production(model_id, signoffs, signing_key)
        promoted_results.append(res.model_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(try_promote, mid) for mid in candidates]
        concurrent.futures.wait(futures)

    production_models = [
        cp.model_id for cp in vault._checkpoints.values() if cp.status == ModelStatus.PRODUCTION
    ]
    assert len(production_models) == 1, f"Expected exactly 1 production champion, found {len(production_models)}"
    assert vault.get_production_model() is not None


def test_model_registry_concurrent_dual_signoff(tmp_path: Any) -> None:
    """Assert concurrent dual sign-offs do not drop approvals or corrupt registry.json."""
    registry = ModelRegistry(storage_dir=str(tmp_path))
    sim_id = "test_concurrent_signoff_sim"

    for i in range(1, 4):
        registry.save_version(
            simulation_id=sim_id,
            state_dict={"w": torch.tensor([float(i)])},
            metrics={"auc_roc": 0.85},
            is_promoted=(i == 1),
            status="champion" if i == 1 else "candidate",
        )

    def do_signoff(ver: int, role: str) -> None:
        registry.sign_off(
            simulation_id=sim_id,
            version=ver,
            role=role,
            user=f"user_{role}",
            signature=f"sig_{role}_{ver}",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futs = []
        for v in (2, 3):
            futs.append(executor.submit(do_signoff, v, "ml_engineer"))
            futs.append(executor.submit(do_signoff, v, "compliance"))
        concurrent.futures.wait(futs)

    manifest = registry.list_versions(sim_id)
    assert len(manifest) == 3
    # Both version 2 and 3 must have received both sign-offs and been promoted to challenger
    v2 = next(e for e in manifest if e["version"] == 2)
    v3 = next(e for e in manifest if e["version"] == 3)
    assert len(v2["sign_offs"]) == 2
    assert v2["status"] == "challenger"
    assert len(v3["sign_offs"]) == 2
    assert v3["status"] == "challenger"


def test_immutable_audit_chain_concurrent_writers_and_readers() -> None:
    """Assert audit chain integrity remains strictly valid under high-concurrency appends and reads."""
    chain = ImmutableAuditChain()
    num_threads = 20
    appends_per_thread = 10

    def writer(tid: int) -> None:
        for j in range(appends_per_thread):
            chain.append_event(
                event_type=f"AUDIT_T{tid}_E{j}",
                actor=f"bank_{tid}",
                target_id=f"target_{j}",
                details={"value": j * tid},
            )

    reader_errors: list[str] = []

    def reader() -> None:
        for _ in range(30):
            report = chain.verify_chain_integrity()
            if not report.is_valid:
                reader_errors.append(str(report.tamper_reason))

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads + 2) as executor:
        wfuts = [executor.submit(writer, i) for i in range(num_threads)]
        rfuts = [executor.submit(reader) for _ in range(2)]
        concurrent.futures.wait(wfuts + rfuts)

    assert len(reader_errors) == 0, f"Reader detected corruption during concurrent writes: {reader_errors}"
    final_report = chain.verify_chain_integrity()
    assert final_report.is_valid is True
    assert final_report.total_records >= (num_threads * appends_per_thread)


def test_tenant_quota_burst_concurrency_atomic_acquire() -> None:
    """Assert burst of concurrent requests cannot bypass configured tenant quota limit."""
    metering = TenantMeteringService()
    tenant = "bank_concurrency_test"
    metering.set_quota_limits(tenant, TenantQuotaLimits(max_daily_inferences=3))

    concurrency = 30
    allowed = 0
    rejected = 0

    def worker() -> None:
        nonlocal allowed, rejected
        is_allowed, _ = metering.acquire_quota(tenant, "INFERENCE", 1)
        if is_allowed:
            time.sleep(0.002)
            allowed += 1
        else:
            rejected += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futs = [executor.submit(worker) for _ in range(concurrency)]
        concurrent.futures.wait(futs)

    assert allowed == 3, f"Quota allowed {allowed} requests, expected exactly 3"
    assert rejected == concurrency - 3
    usage = metering.get_usage(tenant)
    assert usage.daily_inferences == 3


def test_idempotency_store_concurrent_duplicate_in_flight_lock() -> None:
    """Assert concurrent requests with identical Idempotency-Key are deduplicated and execute once."""
    idem = IdempotencyService()
    test_key = "test_concurrent_idempotency_uuid_9988"
    concurrency = 12
    executions = 0
    in_progress = 0
    replayed = 0

    def worker(req_id: int) -> dict[str, Any]:
        nonlocal executions, in_progress, replayed
        status, cached = idem.acquire(test_key)
        if status == "HIT":
            replayed += 1
            return cached
        if status == "IN_PROGRESS":
            in_progress += 1
            return {"status": "CONFLICT"}

        # Simulate creation work
        time.sleep(0.01)
        executions += 1
        res = {"resource_id": f"res_{req_id}", "status": "CREATED"}
        idem.complete(test_key, res)
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futs = [executor.submit(worker, i) for i in range(concurrency)]
        concurrent.futures.wait(futs)

    assert executions == 1, f"Expected exactly 1 execution, got {executions}"
    assert in_progress + replayed == concurrency - 1

    # After completion, subsequent request must HIT cache
    post_status, post_cached = idem.acquire(test_key)
    assert post_status == "HIT"
    assert post_cached["status"] == "CREATED"
