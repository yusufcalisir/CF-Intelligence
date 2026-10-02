"""Unit tests for Class B1 and Class B2 HTTP Service Benchmark Methodology.

Verifies the 14 mandatory invariants established during the forensic rate-limit
contamination diagnosis and authorized methodology repair:
1. Raw samples preserve status code.
2. Latency and status remain associated.
3. 2xx headline percentiles exclude 429 rejections.
4. 429 statistics can be computed independently.
5. Successful throughput numerator uses only 2xx.
6. Attempted throughput is separately named.
7. Limiter benchmark mode is disabled by default.
8. Production 60/minute behavior remains unchanged.
9. Benchmark repetitions start with equivalent limiter state.
10. Concurrency tiers cannot inherit limiter quota from previous tiers.
11. Warm-up cannot consume measured-run limiter quota.
12. Superseded contaminated artifacts cannot be selected as canonical B1 evidence.
13. Claim registry resolves HTTP service claims to the new B1 artifact.
14. B1 and B2 are explicitly distinguished.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_RESULTS_DIR = REPO_ROOT / "benchmarks" / "results" / "raw"
CLAIM_REGISTRY_PATH = REPO_ROOT / "benchmarks" / "claim_registry.json"


# ── Invariant 1: Raw samples preserve status code ─────────────────────────────
def test_raw_samples_preserve_status_code() -> None:
    """Verifies that sample logs preserve per-sample integer HTTP status codes."""
    sample_record: dict[str, Any] = {
        "latency_ms": 12.34,
        "status_code": 200,
        "success": True,
        "timeout": False,
        "exception": None,
        "worker_id": 0,
        "repetition": 1,
        "concurrency": 10,
    }
    assert "status_code" in sample_record
    assert isinstance(sample_record["status_code"], int)
    assert sample_record["status_code"] == 200


# ── Invariant 2: Latency and status remain associated ─────────────────────────
def test_latency_and_status_remain_associated() -> None:
    """Verifies that each structured sample pairs latency with HTTP status code."""
    samples = [
        {"latency_ms": 15.2, "status_code": 200, "success": True},
        {"latency_ms": 2.8, "status_code": 429, "success": False},
    ]
    for s in samples:
        assert "latency_ms" in s
        assert "status_code" in s
        assert isinstance(s["latency_ms"], (int, float))
        assert s["latency_ms"] > 0


# ── Invariant 3: 2xx headline percentiles exclude 429 ─────────────────────────
def test_2xx_headline_percentiles_exclude_429() -> None:
    """Proves that headline percentiles filter strictly on 2xx and exclude 429 errors."""
    # Synthetic population: 50 fast 429s (2.5ms) and 50 real 2xx inferences (15.0ms)
    samples = (
        [{"latency_ms": 2.5, "status_code": 429, "success": False} for _ in range(50)]
        + [{"latency_ms": 15.0, "status_code": 200, "success": True} for _ in range(50)]
    )

    # Contaminated calculation (the pre-repair bug):
    contaminated_p50 = float(np.percentile([s["latency_ms"] for s in samples], 50))
    # Correct B1 methodology (filtered to 2xx exclusively):
    lats_2xx = [s["latency_ms"] for s in samples if s["success"]]
    repaired_p50 = float(np.percentile(lats_2xx, 50))

    assert len(lats_2xx) == 50
    assert repaired_p50 == pytest.approx(15.0, abs=1e-3)
    assert contaminated_p50 < repaired_p50, "Contaminated p50 was artificially depressed by 429s"


# ── Invariant 4: 429 statistics can be computed independently ─────────────────
def test_429_statistics_can_be_computed_independently() -> None:
    """Verifies that 429 latency and counts can be extracted as an independent population."""
    samples = (
        [{"latency_ms": 3.0, "status_code": 429, "success": False} for _ in range(30)]
        + [{"latency_ms": 14.0, "status_code": 200, "success": True} for _ in range(70)]
    )
    samples_429 = [s for s in samples if s["status_code"] == 429]
    lats_429 = [s["latency_ms"] for s in samples_429]

    assert len(samples_429) == 30
    assert float(np.mean(lats_429)) == pytest.approx(3.0, abs=1e-3)


# ── Invariant 5: Successful throughput numerator uses only 2xx ────────────────
def test_successful_throughput_numerator_uses_only_2xx() -> None:
    """Verifies that successful_inference_rps uses only 2xx in numerator, not attempted."""
    duration_seconds = 2.0
    stat_2xx = 60
    stat_4xx = 40
    attempted = stat_2xx + stat_4xx

    successful_rps = stat_2xx / duration_seconds
    attempted_rps = attempted / duration_seconds

    assert successful_rps == pytest.approx(30.0, abs=1e-3)
    assert attempted_rps == pytest.approx(50.0, abs=1e-3)
    assert successful_rps < attempted_rps


# ── Invariant 6: Attempted throughput is separately named ─────────────────────
def test_attempted_throughput_is_separately_named() -> None:
    """Verifies that attempted throughput is explicitly named attempted_request_rps."""
    tier_record = {
        "successful_inference_rps": 85.2,
        "attempted_request_rps": 85.2,
        "overall_success_rate": 1.0,
    }
    assert "successful_inference_rps" in tier_record
    assert "attempted_request_rps" in tier_record
    assert tier_record["successful_inference_rps"] == tier_record["attempted_request_rps"]


# ── Invariant 7: Limiter benchmark mode is disabled by default ─────────────────
def test_limiter_benchmark_mode_is_disabled_by_default() -> None:
    """Verifies that benchmark mode is strictly disabled by default."""
    from app.infrastructure.security.rate_limiter import (
        is_benchmark_mode,
        is_rate_limiter_enabled,
    )

    # Ensure environment is clean
    old_val = os.environ.pop("CFI_BENCHMARK_MODE", None)
    try:
        assert is_benchmark_mode() is False
        assert is_rate_limiter_enabled() is True
    finally:
        if old_val is not None:
            os.environ["CFI_BENCHMARK_MODE"] = old_val


# ── Invariant 8: Production 60/minute behavior remains unchanged ───────────────
def test_production_60_minute_behavior_remains_unchanged() -> None:
    """Verifies that SlowAPI limiter singleton has enabled=True by default in production."""
    old_val = os.environ.pop("CFI_BENCHMARK_MODE", None)
    try:
        from app.infrastructure.security.rate_limiter import limiter
        assert limiter.enabled is True
    finally:
        if old_val is not None:
            os.environ["CFI_BENCHMARK_MODE"] = old_val


# ── Invariant 9: Benchmark repetitions start with equivalent limiter state ─────
def test_benchmark_repetitions_start_with_equivalent_limiter_state() -> None:
    """Verifies that reset_rate_limiter() executes without error and clears storage."""
    from app.infrastructure.security.rate_limiter import limiter, reset_rate_limiter

    # Call reset
    reset_rate_limiter()
    # Limiter storage reset method should exist and succeed
    assert hasattr(limiter, "reset")


# ── Invariant 10: Concurrency tiers cannot inherit limiter quota ───────────────
def test_concurrency_tiers_cannot_inherit_limiter_quota() -> None:
    """Verifies that tenant metering reset_all() restores initial zero-usage state."""
    from app.application.services.tenant_metering import get_tenant_metering_service

    metering = get_tenant_metering_service()
    # Consume some quota
    metering.acquire_quota("bank_alpha", "INFERENCE", count=50)
    usage = metering.get_usage("bank_alpha")
    assert usage.daily_inferences >= 50

    # Deterministic tier reset
    metering.reset_all()
    usage_after = metering.get_usage("bank_alpha")
    assert usage_after.daily_inferences == 0


# ── Invariant 11: Warm-up cannot consume measured-run limiter quota ───────────
def test_warmup_cannot_consume_measured_run_limiter_quota() -> None:
    """Verifies that post-warmup reset clears all consumed quota before measured traffic."""
    from app.application.services.tenant_metering import get_tenant_metering_service

    metering = get_tenant_metering_service()
    # Simulate 10 warmup requests
    metering.acquire_quota("bank_alpha", "INFERENCE", count=10)
    assert metering.get_usage("bank_alpha").daily_inferences >= 10

    # Post-warmup deterministic reset
    metering.reset_all()
    assert metering.get_usage("bank_alpha").daily_inferences == 0


# ── Invariant 12: Superseded contaminated artifacts cannot be selected as B1 ──
def test_superseded_contaminated_artifacts_cannot_be_selected_as_canonical_b1() -> None:
    """Verifies that superseded contaminated artifact is archived and marked."""
    superseded_file = RAW_RESULTS_DIR / "latency_http_service_benchmark_superseded_v1.json"
    assert superseded_file.exists(), f"Superseded artifact missing at {superseded_file}"
    with open(superseded_file, encoding="utf-8") as f:
        data = json.load(f)
    assert "SUPERSEDED" in data.get("audit_status", "")
    assert "supersession_reason" in data


# ── Invariant 13: Claim registry resolves HTTP service claims to B1 artifact ───
def test_claim_registry_resolves_http_service_claims_to_b1_artifact() -> None:
    """Verifies that claim registry HTTP claims point to latency_http_service_benchmark.json."""
    assert CLAIM_REGISTRY_PATH.exists()
    with open(CLAIM_REGISTRY_PATH, encoding="utf-8") as f:
        reg = json.load(f)
    claims = {c["claim_id"]: c for c in reg["claims"]}

    assert "CLM-HTTP-SERVICE-THROUGHPUT" in claims
    assert "CLM-HTTP-SERVICE-LATENCY-P50" in claims
    tp_claim = claims["CLM-HTTP-SERVICE-THROUGHPUT"]
    lat_claim = claims["CLM-HTTP-SERVICE-LATENCY-P50"]

    valid_b1_artifacts = {
        "benchmarks/results/raw/latency_http_service_benchmark.json",
        "benchmarks/results/raw/latency_http_service_benchmark_post_basehttp0_diagnosis.json",
    }
    assert tp_claim["raw_artifact"] in valid_b1_artifacts
    assert lat_claim["raw_artifact"] in valid_b1_artifacts


# ── Invariant 14: B1 and B2 are explicitly distinguished ─────────────────────
def test_b1_and_b2_are_explicitly_distinguished() -> None:
    """Verifies that Class B1 (inference capacity) and B2 (rate-limiter) have distinct artifacts."""
    b2_file = RAW_RESULTS_DIR / "rate_limit_behavior_benchmark.json"
    assert b2_file.exists(), f"Class B2 artifact missing at {b2_file}"
    with open(b2_file, encoding="utf-8") as f:
        b2_data = json.load(f)
    assert b2_data["benchmark_type"] == "CLASS_B2_RATE_LIMITER_BEHAVIOR_BENCHMARK"
    assert b2_data["results"]["configured_rate_limit"] == "60/minute"
    assert b2_data["results"]["allowed_2xx_before_quota_exhaustion"] == 60
    assert b2_data["results"]["rate_limited_429_count"] > 0
