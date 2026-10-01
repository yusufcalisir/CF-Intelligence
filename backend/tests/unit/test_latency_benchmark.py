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
        return runner.run_concurrency_stress_test(concurrency_levels=[1], requests_per_worker=5, save_artifact=False)

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
        return runner.run_concurrency_stress_test(concurrency_levels=[1, 5], requests_per_worker=5, save_artifact=False)

    def test_payload_top_level_keys(self):
        runner = _import_runner()
        payload = self._dual_payload(runner)
        for key in ("timestamp_utc", "environment", "single_request_breakdown", "concurrency_scaling", "bottleneck_analysis"):
            assert key in payload

    def test_concurrency_scaling_has_correct_entry_count(self):
        runner = _import_runner()
        levels = [1, 5]
        payload = runner.run_concurrency_stress_test(concurrency_levels=levels, requests_per_worker=5, save_artifact=False)
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
        payload = runner.run_concurrency_stress_test(concurrency_levels=levels, requests_per_worker=rpw, save_artifact=False)
        for entry in payload["concurrency_scaling"]:
            assert entry["total_requests"] == entry["concurrency"] * rpw


class TestLatencyPercentileMonotonicity:
    def test_percentile_ordering_p50_lte_p95_lte_p99(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1, 10], requests_per_worker=20, save_artifact=False)
        for entry in payload["concurrency_scaling"]:
            assert entry["p50_latency_ms"] <= entry["p95_latency_ms"]
            assert entry["p95_latency_ms"] <= entry["p99_latency_ms"]

    def test_all_percentiles_are_positive(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1], requests_per_worker=10, save_artifact=False)
        for entry in payload["concurrency_scaling"]:
            assert entry["p50_latency_ms"] > 0.0
            assert entry["p95_latency_ms"] > 0.0
            assert entry["p99_latency_ms"] > 0.0

    def test_throughput_is_positive_for_all_levels(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1, 5], requests_per_worker=5, save_artifact=False)
        for entry in payload["concurrency_scaling"]:
            assert entry["throughput_rps"] > 0.0


class TestEnvironmentMetadataCompleteness:
    def _env_payload(self, runner):
        return runner.run_concurrency_stress_test(concurrency_levels=[1], requests_per_worker=5, save_artifact=False)

    def test_environment_contains_required_keys(self):
        runner = _import_runner()
        env = self._env_payload(runner)["environment"]
        for key in ("os", "cpu_model", "python_version", "torch_version"):
            assert key in env

    def test_environment_values_are_non_empty_strings(self):
        runner = _import_runner()
        env = self._env_payload(runner)["environment"]
        for key, val in env.items():
            if val is not None:
                assert isinstance(val, (str, int, float)) and len(str(val)) > 0

    def test_timestamp_utc_is_iso8601_parseable(self):
        import datetime
        runner = _import_runner()
        ts = self._env_payload(runner)["timestamp_utc"]
        datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


class TestJSONArtifactSerializationRoundtrip:
    def test_payload_is_json_serializable(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1], requests_per_worker=5, save_artifact=False)
        json_str = json.dumps(payload, indent=2)
        assert len(json_str) > 100

    def test_json_roundtrip_preserves_concurrency_entries(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1, 3], requests_per_worker=5, save_artifact=False)
        restored = json.loads(json.dumps(payload))
        assert len(restored["concurrency_scaling"]) == len(payload["concurrency_scaling"])
        for orig, rest in zip(payload["concurrency_scaling"], restored["concurrency_scaling"]):
            assert orig["concurrency"] == rest["concurrency"]
            assert abs(orig["p50_latency_ms"] - rest["p50_latency_ms"]) < 1e-9

    def test_json_roundtrip_preserves_bottleneck_fractions(self):
        runner = _import_runner()
        payload = runner.run_concurrency_stress_test(concurrency_levels=[1], requests_per_worker=5, save_artifact=False)
        restored = json.loads(json.dumps(payload))
        assert restored["bottleneck_analysis"]["model_forward_pass_fraction"] == payload["bottleneck_analysis"]["model_forward_pass_fraction"]
        assert restored["bottleneck_analysis"]["api_redis_overhead_fraction"] == payload["bottleneck_analysis"]["api_redis_overhead_fraction"]


class TestHostHardwareCalibrationAndArtifactIntegrity:
    def test_save_artifact_false_leaves_golden_artifact_untouched(self):
        runner = _import_runner()
        golden_file = Path(__file__).resolve().parents[3] / "benchmarks" / "results" / "raw" / "latency_concurrency_benchmark.json"
        assert golden_file.exists()
        mtime_before = golden_file.stat().st_mtime
        runner.run_concurrency_stress_test(concurrency_levels=[1], requests_per_worker=2, save_artifact=False)
        mtime_after = golden_file.stat().st_mtime
        assert mtime_before == mtime_after

    def test_custom_output_path_saves_to_specified_destination(self, tmp_path):
        runner = _import_runner()
        custom_out = tmp_path / "custom_latency.json"
        runner.run_concurrency_stress_test(
            concurrency_levels=[1],
            requests_per_worker=2,
            save_artifact=True,
            output_path=custom_out,
        )
        assert custom_out.exists()
        with open(custom_out, encoding="utf-8") as f:
            data = json.load(f)
        assert "concurrency_scaling" in data
        assert len(data["concurrency_scaling"]) == 1

    def test_hardware_calibration_environment_fields(self):
        runner = _import_runner()
        env = runner.get_hardware_environment()
        assert "os" in env and len(env["os"]) > 0
        assert "cpu_model" in env and len(env["cpu_model"]) > 0
        assert "cpu_count" in env and env["cpu_count"] >= 1
        assert "python_version" in env and len(env["python_version"]) > 0
        assert "torch_version" in env and len(env["torch_version"]) > 0
        assert env["device"] == "cpu"

    def test_fast_path_host_calibration_sla_bound(self):
        runner = _import_runner()
        calib = runner.measure_host_fast_path_calibration(warmup_runs=2, measurement_runs=5)
        assert calib["sla_fast_path_passed"] is True
        assert calib["min_latency_ms"] < 25.0
        assert "stage_breakdown" in calib
        assert calib["stage_breakdown"]["total_request_latency_ms"] > 0.0

    def test_calibrated_golden_artifact_reconciliation(self):
        golden_file = Path(__file__).resolve().parents[3] / "benchmarks" / "results" / "raw" / "latency_concurrency_benchmark.json"
        with open(golden_file, encoding="utf-8") as f:
            golden = json.load(f)
        # Fast-path total must satisfy < 15ms SLA
        fast_path = golden["single_request_breakdown"]["fast_path_raw"]["total_request_latency_ms"]
        assert fast_path < 15.0
        # Peak throughput across tiers must exceed internal target floor of 1200 req/s
        peak_rps = max(item["throughput_rps"] for item in golden["concurrency_scaling"])
        assert peak_rps > 1200.0


class TestRepairedBenchmarkMethodologyInvariants:
    """Verifies all Section 29 requirements for authorized latency methodology repair."""

    def test_p99_rejects_inadequately_small_sample(self):
        """1. p99 is not reported from an inadequately small configured sample without explicit warning/rejection."""
        runner = _import_runner()
        import pytest
        # When reject=True, small sample raises ValueError
        with pytest.raises(ValueError, match="statistically inadequate"):
            runner.validate_sample_size_for_percentiles(sample_size=10, min_samples_for_p99=100, reject=True)

        # When reject=False, small sample emits UserWarning and returns False
        with pytest.warns(UserWarning, match="statistically inadequate"):
            res = runner.validate_sample_size_for_percentiles(sample_size=10, min_samples_for_p99=100, reject=False)
            assert res is False

        # When sample is adequate (>= 100, ideally >= 1000), returns True without warning
        assert runner.validate_sample_size_for_percentiles(sample_size=1000, min_samples_for_p99=100) is True

    def test_error_rate_is_calculated_from_observations(self):
        """2. error rate is calculated from observations, not hardcoded."""
        raw_file = Path(__file__).resolve().parents[3] / "benchmarks" / "results" / "raw" / "latency_microbenchmark.json"
        with open(raw_file, encoding="utf-8") as f:
            data = json.load(f)
        for entry in data["concurrency_scaling"]:
            assert "error_rate" in entry
            assert "attempted_requests" in entry
            assert "failed_requests" in entry
            computed = entry["failed_requests"] / max(1, entry["attempted_requests"])
            assert abs(computed - entry["error_rate"]) < 1e-4

    def test_raw_distribution_evidence_is_preserved(self):
        """3. raw/distribution evidence is preserved for independent recomputation."""
        raw_samples_file = Path(__file__).resolve().parents[3] / "benchmarks" / "results" / "raw" / "latency_microbenchmark_samples.json"
        assert raw_samples_file.exists()
        with open(raw_samples_file, encoding="utf-8") as f:
            samples = json.load(f)
        assert "rep_1" in samples
        assert "c_1" in samples["rep_1"]
        c1_samples = samples["rep_1"]["c_1"]
        assert len(c1_samples) >= 100
        import numpy as np
        recomputed_p50 = float(np.percentile(c1_samples, 50))
        assert recomputed_p50 > 0.0

    def test_microbenchmark_artifact_has_explicit_scope(self):
        """4. microbenchmark artifact has explicit scope excluding HTTP/network."""
        raw_file = Path(__file__).resolve().parents[3] / "benchmarks" / "results" / "raw" / "latency_microbenchmark.json"
        with open(raw_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data["benchmark_type"] == "IN_PROCESS_MICROBENCHMARK"
        scope = data["scope"].lower()
        assert "in-process" in scope
        assert "excludes" in scope or "excluding" in scope

    def test_http_service_artifact_has_explicit_scope(self):
        """5. HTTP artifact has explicit scope covering real network sockets."""
        http_file = Path(__file__).resolve().parents[3] / "benchmarks" / "results" / "raw" / "latency_http_service_benchmark.json"
        assert http_file.exists()
        with open(http_file, encoding="utf-8") as f:
            data = json.load(f)
        assert data["benchmark_type"] in ("LOCAL_HTTP_SERVICE_BENCHMARK", "CLASS_B1_LOCAL_HTTP_INFERENCE_CAPACITY_BENCHMARK")
        scope = data["scope"].lower()
        assert "uvicorn" in scope or "asgi" in scope
        assert "socket" in scope or "tcp" in scope

    def test_documentation_and_claim_registry_cannot_map_micro_to_gateway(self):
        """6. documentation and claim registry distinguish in-process microbenchmark from HTTP service."""
        claim_file = Path(__file__).resolve().parents[3] / "benchmarks" / "claim_registry.json"
        with open(claim_file, encoding="utf-8") as f:
            data = json.load(f)
        claims = {c["claim_id"]: c for c in data["claims"]}
        fastpath = claims["CLM-LATENCY-FASTPATH"]
        assert "microbenchmark" in fastpath["title"].lower() or "in-process" in fastpath["title"].lower()

    def test_legacy_results_marked_superseded(self):
        """7. legacy results are marked superseded and preserved."""
        claim_file = Path(__file__).resolve().parents[3] / "benchmarks" / "claim_registry.json"
        with open(claim_file, encoding="utf-8") as f:
            data = json.load(f)
        claims = {c["claim_id"]: c for c in data["claims"]}
        fastpath = claims["CLM-LATENCY-FASTPATH"]
        assert "legacy_artifact" in fastpath or "superseded" in fastpath.get("notes", "").lower()

    def test_claim_registry_reconciles_with_correct_artifacts(self):
        """8. claim registry values reconcile with the correct artifact."""
        claim_file = Path(__file__).resolve().parents[3] / "benchmarks" / "claim_registry.json"
        with open(claim_file, encoding="utf-8") as f:
            data = json.load(f)
        claims = {c["claim_id"]: c for c in data["claims"]}
        micro_file = Path(__file__).resolve().parents[3] / "benchmarks" / "results" / "raw" / "latency_microbenchmark.json"
        with open(micro_file, encoding="utf-8") as f:
            micro_data = json.load(f)

        fast_path = micro_data["single_request_breakdown"]["fast_path_raw"]["total_request_latency_ms"]
        assert abs(claims["CLM-LATENCY-FASTPATH"]["empirical_measured_value"] - fast_path) < 0.05

