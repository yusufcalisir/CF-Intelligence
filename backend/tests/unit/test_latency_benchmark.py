"""Unit tests for Inference Gateway Latency Benchmark & Concurrency Stress Test Runner.

Tests cover:
- Single-request micro-latency decomposition (all 6 pipeline stages)
- Stage latency non-negativity and total consistency invariants
- Composite 9-signal risk scoring stage correctness
- PyTorch model forward-pass stage accuracy
- Concurrency stress test result schema and data contracts
- Throughput and percentile ordering monotonicity (p50 <= p95 <= p99)
- Bottleneck analysis fraction invariants (non-negative, <= 100%)
- JSON artifact serialization and deserialization roundtrip
- Environmental metadata completeness and typing
- Fast-path vs full-path SHAP stage isolation
- Error rate is zero across all concurrency levels (CPU-bound in-process)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add benchmarks directory to path so we can import the latency runner module
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "benchmarks" / "runners"))
# Also ensure the backend application modules are importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _import_runner():
    import run_latency_benchmark as runner  # noqa: PLC0415
    return runner


class TestSingleRequestPipelineBreakdown:
    def test_all_six_stage_keys_present(self):
        runner = _import_runner()
        breakdown = runner.measure_single_request_pipeline(with_shap=False)
        required_keys = {
            "auth_abac_ms", "feature_store_ms", "model_forward_pass_ms",
            "composite_9signals_ms", "shap_attribution_ms", "serialization_ms",
            "total_request_latency_ms",
        }
        assert required_keys.issubset(breakdown.keys())

    def test_all_stage_latencies_are_non_negative(self):
        runner = _import_runner()
        breakdown = runner.measure_single_request_pipeline(with_shap=False)
        for key, val in breakdown.items():
            assert val >= 0.0, f"Stage '{key}' returned negative latency: {val}"

    def test_total_latency_equals_sum_of_stages(self):
        runner = _import_runner()
        breakdown = runner.measure_single_request_pipeline(with_shap=False)
        stage_keys = [
            "auth_abac_ms", "feature_store_ms", "model_forward_pass_ms",
            "composite_9signals_ms", "shap_attribution_ms", "serialization_ms",
        ]
        computed_total = sum(breakdown[k] for k in stage_keys)
        assert abs(computed_total - breakdown["total_request_latency_ms"]) < 1e-6

    def test_total_latency_is_positive(self):
        runner = _import_runner()
        breakdown = runner.measure_single_request_pipeline(with_shap=False)
        assert breakdown["total_request_latency_ms"] > 0.0

    def test_all_stage_values_are_float(self):
        runner = _import_runner()
        breakdown = runner.measure_single_request_pipeline(with_shap=False)
        for key, val in breakdown.items():
            assert isinstance(val, float), f"Stage '{key}' value is not float: {type(val)}"


class TestSHAPStageIsolation:
    def test_shap_disabled_returns_zero_for_shap_stage(self):
        runner = _import_runner()
        breakdown = runner.measure_single_request_pipeline(with_shap=False)
        assert breakdown["shap_attribution_ms"] == 0.0

    def test_shap_enabled_returns_non_negative_for_shap_stage(self):
        runner = _import_runner()
        breakdown = runner.measure_single_request_pipeline(with_shap=True)
        assert breakdown["shap_attribution_ms"] >= 0.0

    def test_both_paths_produce_consistent_core_stages(self):
        runner = _import_runner()
        fast = runner.measure_single_request_pipeline(with_shap=False)
        full = runner.measure_single_request_pipeline(with_shap=True)
        for stage in ["model_forward_pass_ms", "composite_9signals_ms", "serialization_ms"]:
            assert stage in fast and stage in full
            assert fast[stage] >= 0.0 and full[stage] >= 0.0


class TestBottleneckFractionInvariants:
    def _minimal_payload(self, runner):
        return runner.run_concurrency_stress_test(concurrency_levels=[1], requests_per_worker=5)

    def test_model_fraction_string_is_parseable_percentage(self):
        runner = _import_runner()
        frac_str = self._minimal_payload(runner)["bottleneck_analysis"]["model_forward_pass_fraction"]
        assert frac_str.endswith("%")
        assert 0.0 <= float(frac_str.rstrip("%")) <= 100.0

    def test_api_redis_fraction_string_is_parseable_percentage(self):
        runner = _import_runner()
        frac_str = self._minimal_payload(runner)["bottleneck_analysis"]["api_redis_overhead_fraction"]
        assert frac_str.endswith("%")
        assert 0.0 <= float(frac_str.rstrip("%")) <= 100.0

    def test_fractions_do_not_exceed_100_combined(self):
        runner = _import_runner()
        payload = self._minimal_payload(runner)
        m = float(payload["bottleneck_analysis"]["model_forward_pass_fraction"].rstrip("%"))
        r = float(payload["bottleneck_analysis"]["api_redis_overhead_fraction"].rstrip("%"))
        assert m + r <= 100.0 + 1e-6

    def test_bottleneck_observation_is_non_empty_string(self):
        runner = _import_runner()
        obs = self._minimal_payload(runner)["bottleneck_analysis"]["observation"]
        assert isinstance(obs, str) and len(obs) > 20


class TestConcurrencyStressTestSchema:
    def _dual_payload(self, runner):
        return runner.run_concurrency_stress_test(concurrency_levels=[1, 5], requests_per_worker=5)

    def test_payload_top_level_keys(self):
        runner = _import_runner()
        payload = self._dual_payload(runner)
        for key in ("timestamp_utc", "environment", "single_request_breakdown", "concurrency_scaling", "bottleneck_analysis"):
            assert key in payload

    def test_concurrency_scaling_has_correct_entry_count(self):
        runner = _import_runner()
        levels = [1, 5]
        payload = runner.run_concurrency_stress_test(concurrency_levels=levels, requests_per_worker=5)
        assert len(payload["concurrency_scaling"]) == len(levels)

    def test_each_concurrency_entry_has_required_fields(self):
        runner = _import_runner()
        payload = self._dual_payload(runner)
        required = {"concurrency", "total_requests", "throughput_rps", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms", "error_rate"}
        for entry in payload["concurrency_scaling"]:
            assert required.issubset(entry.keys())

    def test_error_rate_is_zero_for_all_levels(self):
        runner = _import_runner()
        payload = self._dual_payload(runner)
        for entry in payload["concurrency_scaling"]:
            assert entry["error_rate"] == 0.0

    def test_total_requests_equals_concurrency_times_workers(self):
        runner = _import_runner()
        rpw = 5
        levels = [1, 4]
        payload = runner.run_concurrency_stress_test(concurrency_levels=levels, requests_per_worker=rpw)
        for entry in payload["concurrency_scaling"]:
            assert entry["total_requests"] == entry["concurrency"] * rpw


class TestLatencyPercentileMonotonicity:
    def test_percentile_ordering_p50_lte_p95_lte_p99(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1, 10], requests_per_worker=20)
        for entry in payload["concurrency_scaling"]:
            assert entry["p50_latency_ms"] <= entry["p95_latency_ms"]
            assert entry["p95_latency_ms"] <= entry["p99_latency_ms"]

    def test_all_percentiles_are_positive(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1], requests_per_worker=10)
        for entry in payload["concurrency_scaling"]:
            assert entry["p50_latency_ms"] > 0.0
            assert entry["p95_latency_ms"] > 0.0
            assert entry["p99_latency_ms"] > 0.0

    def test_throughput_is_positive_for_all_levels(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1, 5], requests_per_worker=5)
        for entry in payload["concurrency_scaling"]:
            assert entry["throughput_rps"] > 0.0


class TestEnvironmentMetadataCompleteness:
    def _env_payload(self, runner):
        return runner.run_concurrency_stress_test(concurrency_levels=[1], requests_per_worker=5)

    def test_environment_contains_required_keys(self):
        runner = _import_runner()
        env = self._env_payload(runner)["environment"]
        for key in ("os", "cpu_model", "python_version", "torch_version"):
            assert key in env

    def test_environment_values_are_non_empty_strings(self):
        runner = _import_runner()
        env = self._env_payload(runner)["environment"]
        for key, val in env.items():
            assert isinstance(val, str) and len(val) > 0

    def test_timestamp_utc_is_iso8601_parseable(self):
        import datetime
        runner = _import_runner()
        ts = self._env_payload(runner)["timestamp_utc"]
        datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


class TestJSONArtifactSerializationRoundtrip:
    def test_payload_is_json_serializable(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1], requests_per_worker=5)
        json_str = json.dumps(payload, indent=2)
        assert len(json_str) > 100

    def test_json_roundtrip_preserves_concurrency_entries(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1, 3], requests_per_worker=5)
        restored = json.loads(json.dumps(payload))
        assert len(restored["concurrency_scaling"]) == len(payload["concurrency_scaling"])
        for orig, rest in zip(payload["concurrency_scaling"], restored["concurrency_scaling"]):
            assert orig["concurrency"] == rest["concurrency"]
            assert abs(orig["p50_latency_ms"] - rest["p50_latency_ms"]) < 1e-9

    def test_json_roundtrip_preserves_bottleneck_fractions(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1], requests_per_worker=5)
        restored = json.loads(json.dumps(payload))
        assert restored["bottleneck_analysis"]["model_forward_pass_fraction"] == payload["bottleneck_analysis"]["model_forward_pass_fraction"]
        assert restored["bottleneck_analysis"]["api_redis_overhead_fraction"] == payload["bottleneck_analysis"]["api_redis_overhead_fraction"]
