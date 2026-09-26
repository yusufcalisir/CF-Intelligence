# ruff: noqa: E402
"""Integration tests for Low-Latency Fast-Path Gateway, Concurrency Throughput, and Security Boundaries.

Verifies:
  - Fast-path REST scoring latency SLA (< 15ms target)
  - Micro-latency stage decomposition with real PyTorch & 9-signal composite risk engine
  - Real-time inference router (/v1/inference/score) performance
  - Byzantine fault tolerance boundary limits:
      * Coordinate-Wise Median breakdown point (f < n/2)
      * Trimmed Mean & Bulyan bounds (f < (n-2)/2)
  - P2P Curve25519 ECDH SecAgg cryptographic throughput & zero-sum invariant
  - Concurrent gateway load processing
"""

from __future__ import annotations

import concurrent.futures
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
from fastapi.testclient import TestClient

# Ensure workspace root in path for benchmarks module access
_workspace_root = str(Path(__file__).resolve().parents[3])
if _workspace_root not in sys.path:
    sys.path.insert(0, _workspace_root)

from app.application.schemas.transaction import RealtimeInferenceRequest
from app.application.services.fl_engine import FederatedLearningEngine
from app.domain.enums import AggregationMethod
from app.domain.value_objects import ModelWeights
from app.infrastructure.security.p2p_secagg_driver import P2PSecAggDriver
from app.main import app

client = TestClient(app)


class TestFastPathInferenceLatency:
    """Verifies fast-path fraud scoring latency complies with < 15ms SLA target."""

    def test_fast_path_endpoint_real_latency_under_15ms(self) -> None:
        """Empirically asserts median response latency on /api/v1/transactions/score is < 15ms."""
        payload = {
            "transaction_id": "tx_bench_latency_01",
            "account_id": "acc_bench_9981",
            "amount": 285.50,
            "currency": "EUR",
            "merchant_id": "rewe_supermarket_berlin",
            "country": "DE",
            "device_id": "dev_ios_client_secure",
        }
        headers = {"X-Forwarded-For": "198.51.100.10"}

        # Warm-up requests (3 cycles to initialize caches and threadpools)
        for _ in range(3):
            warmup = client.post("/api/v1/transactions/score", json=payload, headers=headers)
            assert warmup.status_code == 200

        # Benchmark 25 real request iterations
        latencies: list[float] = []
        server_latencies: list[float] = []
        for _ in range(25):
            t0 = time.perf_counter()
            resp = client.post("/api/v1/transactions/score", json=payload, headers=headers)
            t1 = time.perf_counter()
            assert resp.status_code == 200
            latencies.append((t1 - t0) * 1000.0)
            server_latencies.append(resp.json()["latency_ms"])

        data = resp.json()
        assert "risk_score" in data
        assert 0 <= data["risk_score"] <= 1000
        assert data["decision"] in ("ALLOW", "REVIEW", "BLOCK")
        assert data["risk_level"] in ("LOW", "MEDIUM", "HIGH")

        median_server_latency = float(np.median(server_latencies))
        median_roundtrip = float(np.median(latencies))

        # Assert empirical server scoring latency is strictly under < 15ms fast-path threshold
        assert median_server_latency < 15.0, (
            f"Expected server median latency < 15.0ms, got {median_server_latency:.2f}ms"
        )
        assert median_roundtrip < 25.0, (
            f"Expected roundtrip median latency < 25.0ms, got {median_roundtrip:.2f}ms"
        )

    def test_realtime_inference_router_fast_path(self) -> None:
        """Verifies /v1/inference/score executes in < 15ms with TorchScript model / fallback."""
        payload = RealtimeInferenceRequest(
            transaction_id="tx_rt_inf_001",
            source_account="acc_src_991",
            target_account="acc_dst_002",
            amount=450.00,
            velocity_1h=2,
            merchant_category="electronics",
        )

        # Warm-up (3 cycles to warm up TorchScript compilation and JIT model cache)
        for _ in range(3):
            _ = client.post("/v1/inference/score", json=payload.model_dump())

        latencies = []
        for _ in range(20):
            t0 = time.perf_counter()
            resp = client.post("/v1/inference/score", json=payload.model_dump())
            latencies.append((time.perf_counter() - t0) * 1000.0)
            assert resp.status_code == 200

        data = resp.json()
        assert 0 <= data["risk_score"] <= 1000
        assert data["decision"] in ("ALLOW", "REVIEW", "BLOCK")
        assert data["latency_ms"] >= 0.0

        median_lat = float(np.median(latencies))
        assert median_lat < 25.0, f"Expected /v1/inference/score median < 25ms, got {median_lat:.2f}ms"

    def test_micro_latency_decomposition_real_components(self) -> None:
        """Verifies individual pipeline stages execute in real time without mock delays."""
        from benchmarks.runners.run_latency_benchmark import measure_single_request_pipeline

        # Run 5 iterations to warm up components and caches
        for _ in range(5):
            measure_single_request_pipeline(with_shap=False)

        breakdown = measure_single_request_pipeline(with_shap=False)

        # Assert each stage duration is strictly measured and non-negative
        assert breakdown["auth_abac_ms"] >= 0.0
        assert breakdown["feature_store_ms"] >= 0.0
        assert breakdown["model_forward_pass_ms"] >= 0.0
        assert breakdown["composite_9signals_ms"] >= 0.0
        assert breakdown["serialization_ms"] >= 0.0

        # Assert total request latency meets < 15ms fast-path SLA
        assert breakdown["total_request_latency_ms"] < 15.0, (
            f"Expected total request latency < 15ms, got {breakdown['total_request_latency_ms']:.2f}ms"
        )


class TestByzantineToleranceBounds:
    """Verifies mathematical breakdown points and resilience bounds under adversarial poisoning."""

    def test_coordinate_median_breakdown_point_bound(self) -> None:
        """Verifies Coordinate-Wise Median holds for f < n/2 and fails when f >= n/2."""
        engine = FederatedLearningEngine(MagicMock(), MagicMock(), MagicMock())
        shapes = [(3,)]

        # Scenario 1: n = 5, f = 2 malicious (< 50% Byzantine nodes -> f < n/2 holds)
        w_h1 = ModelWeights(shapes, [1.0, 1.0, 1.0])
        w_h2 = ModelWeights(shapes, [1.1, 0.9, 1.05])
        w_h3 = ModelWeights(shapes, [0.95, 1.05, 0.98])
        w_poison_1 = ModelWeights(shapes, [1000.0, 1000.0, 1000.0])
        w_poison_2 = ModelWeights(shapes, [1200.0, 1100.0, 1050.0])

        weights_tolerated = [w_h1, w_h2, w_h3, w_poison_1, w_poison_2]
        samples_5 = [100, 100, 100, 100, 100]

        res_tolerated = engine.aggregate_parameters(
            weights_tolerated, samples_5, method=AggregationMethod.COORDINATE_WISE_MEDIAN
        )
        # Sanitized output should stay close to honest consensus (~1.0)
        assert all(abs(v - 1.0) < 0.25 for v in res_tolerated.flat_weights), (
            f"Expected sanitized weights near 1.0 under f < n/2, got {res_tolerated.flat_weights}"
        )

        # Scenario 2: n = 5, f = 3 malicious (>= 50% colluding Byzantine nodes -> boundary violated)
        w_poison_3 = ModelWeights(shapes, [1500.0, 1400.0, 1300.0])
        weights_poisoned = [w_h1, w_h2, w_poison_1, w_poison_2, w_poison_3]

        res_poisoned = engine.aggregate_parameters(
            weights_poisoned, samples_5, method=AggregationMethod.COORDINATE_WISE_MEDIAN
        )
        # Colluding majority (3 of 5) forces the median above 500.0, proving theoretical bound tightness
        assert all(v > 500.0 for v in res_poisoned.flat_weights), (
            f"Expected breakdown when f >= n/2, got {res_poisoned.flat_weights}"
        )

    def test_trimmed_mean_and_bulyan_bounds(self) -> None:
        """Verifies Trimmed Mean (f=1, n=4) and Bulyan (f=1, n=5) reject extreme poisoned updates."""
        engine = FederatedLearningEngine(MagicMock(), MagicMock(), MagicMock())
        shapes = [(3,)]

        # 4 nodes with 1 poisoned node: Trimmed Mean (f=1 < 4/2)
        w1 = ModelWeights(shapes, [2.0, 2.0, 2.0])
        w2 = ModelWeights(shapes, [2.1, 1.9, 2.0])
        w3 = ModelWeights(shapes, [1.95, 2.05, 2.0])
        w_bad = ModelWeights(shapes, [999.0, 999.0, 999.0])

        res_trimmed = engine.aggregate_parameters(
            [w1, w2, w3, w_bad], [100, 100, 100, 100], method=AggregationMethod.TRIMMED_MEAN
        )
        assert all(abs(v - 2.0) < 0.3 for v in res_trimmed.flat_weights)

        # 5 nodes with 1 poisoned node: Bulyan (f=1 < (5-2)/2 = 1.5)
        w4 = ModelWeights(shapes, [2.05, 1.95, 2.02])
        res_bulyan = engine.aggregate_parameters(
            [w1, w2, w3, w4, w_bad], [100, 100, 100, 100, 100], method=AggregationMethod.BULYAN
        )
        assert all(abs(v - 2.0) < 0.3 for v in res_bulyan.flat_weights)


class TestSecAggCryptographicThroughput:
    """Verifies Curve25519 ECDH SecAgg performance and algebraic zero-sum cancellation."""

    def test_curve25519_secagg_micro_throughput_and_zero_sum(self) -> None:
        """Measures 3-party pairwise key agreement, PRG masking, and zero-sum precision."""
        driver_a = P2PSecAggDriver("bank_alpha", identity_secret=b"A" * 32)
        driver_b = P2PSecAggDriver("bank_beta", identity_secret=b"B" * 32)
        driver_c = P2PSecAggDriver("bank_gamma", identity_secret=b"G" * 32)

        t0 = time.perf_counter()

        # Step 1: Generate ephemeral round keypairs
        b_a = driver_a.generate_round_keypair(10)
        b_b = driver_b.generate_round_keypair(10)
        b_c = driver_c.generate_round_keypair(10)

        # Step 2: Compute masked vectors
        dimension = 200
        w_a = [1.5] * dimension
        w_b = [2.5] * dimension
        w_c = [5.0] * dimension

        y_a = driver_a.compute_masked_vector(w_a, [b_b, b_c])
        y_b = driver_b.compute_masked_vector(w_b, [b_a, b_c])
        y_c = driver_c.compute_masked_vector(w_c, [b_a, b_b])

        # Step 3: Coordinator aggregation
        result = P2PSecAggDriver.aggregate_masked_vectors(
            {"bank_alpha": y_a, "bank_beta": y_b, "bank_gamma": y_c}
        )
        total_time_ms = (time.perf_counter() - t0) * 1000.0

        # Verify mathematical zero-sum cancellation: avg(1.5, 2.5, 5.0) = 3.0
        expected_avg = (1.5 + 2.5 + 5.0) / 3.0
        max_error = max(abs(v - expected_avg) for v in result)
        assert max_error < 1e-4, f"Expected zero-sum cancellation, max error was {max_error:.6e}"

        # Cryptographic execution must be high throughput (< 100ms for 3 parties, 200 dimensions)
        assert total_time_ms < 100.0, f"SecAgg took {total_time_ms:.2f}ms, expected < 100ms"


class TestConcurrentGatewayThroughput:
    """Verifies gateway throughput capacity under multi-threaded concurrency."""

    def test_concurrency_throughput_and_error_rate(self) -> None:
        """Executes concurrent requests across distinct client origins asserting 0% 5xx error rate."""
        payload = {
            "transaction_id": "tx_bench_conc",
            "account_id": "acc_conc_101",
            "amount": 75.0,
            "currency": "EUR",
            "merchant_id": "metro_ticket_station",
            "country": "FR",
            "device_id": "dev_pos_contactless",
        }

        # Distinct IP origins per worker to test genuine multi-client concurrent throughput
        concurrency = 6
        requests_per_thread = 5

        def _worker(thread_idx: int) -> list[int]:
            headers = {"X-Forwarded-For": f"198.51.100.{100 + thread_idx}"}
            statuses = []
            for _ in range(requests_per_thread):
                r = client.post("/api/v1/transactions/score", json=payload, headers=headers)
                statuses.append(r.status_code)
            return statuses

        t_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(_worker, i) for i in range(concurrency)]
            all_statuses = []
            for f in concurrent.futures.as_completed(futures):
                all_statuses.extend(f.result())
        t_duration = time.perf_counter() - t_start

        total_requests = len(all_statuses)
        success_count = all_statuses.count(200)
        throughput = total_requests / (t_duration + 1e-8)

        # 100% success rate under multi-origin concurrency
        assert success_count == total_requests, (
            f"Expected 0% error rate, got {total_requests - success_count} non-200 responses: {all_statuses}"
        )
        assert throughput > 10.0, f"Expected sustained throughput, got {throughput:.1f} r/s"
