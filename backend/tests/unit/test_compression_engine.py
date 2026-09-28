"""Unit tests for GradientCompressionEngine and FederatedCommunicationProfiler.

Tests cover:
- Top-K gradient sparsification (accuracy, sparsity invariants, thresholding, edge cases)
- Lossless compression and decompression roundtrips (zlib / zstd format, bit-exact recovery)
- Binary quantization (FP32, FP16 half-precision, INT8 symmetric affine)
- Coordinate sparse (COO) encoding and decoding
- Wire payload transfer size hierarchy and compression profiling
- Protocol communication profiling (FedAvg vs FedProx vs SCAFFOLD vs Curve25519 SecAgg vs TenSEAL CKKS)
- Integration with FederatedLearningEngine communication telemetry
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.application.services.fl_engine import FederatedLearningEngine
from app.domain.enums import AggregationMethod
from app.infrastructure.security.compression_engine import (
    BandwidthBenchmarkSuiteResult,
    FederatedCommunicationProfiler,
    FederatedProtocol,
    GradientCompressionEngine,
    ProtocolCommunicationProfile,
    SerializationFormat,
)


class TestTopKSparsification:
    """Validates Top-K gradient sparsification algorithms and boundary behavior."""

    @pytest.fixture
    def engine(self) -> GradientCompressionEngine:
        return GradientCompressionEngine(default_k_percent=0.20)

    def test_sparsify_top_k_retains_exact_percentage(self, engine: GradientCompressionEngine) -> None:
        """K=20% on 1,000 items retains exactly 200 non-zero elements."""
        weights = [float(i) for i in range(1, 1001)]
        sparsified = engine.sparsify_top_k(weights, k_percent=0.20)

        assert len(sparsified) == 1000
        non_zero = [w for w in sparsified if w != 0.0]
        assert len(non_zero) == 200
        # The retained 200 should be 801 to 1000
        assert min(non_zero) == 801.0
        assert max(non_zero) == 1000.0

    def test_sparsify_top_k_preserves_large_negative_magnitudes(
        self, engine: GradientCompressionEngine
    ) -> None:
        """Top-K operates on absolute magnitudes, preserving large negative gradients."""
        weights = [-100.0, 0.5, -0.2, 50.0, 0.1, -80.0, 0.01, 20.0, -0.05, 0.02]
        # Keep top 30% (3 items): -100.0, -80.0, 50.0
        sparsified = engine.sparsify_top_k(weights, k_percent=0.30)
        retained = {w for w in sparsified if w != 0.0}
        assert retained == {-100.0, -80.0, 50.0}

    def test_sparsify_top_k_clamping_and_defaults(self, engine: GradientCompressionEngine) -> None:
        """k_percent is clamped between 0.01 and 1.0."""
        weights = [1.0, 2.0, 3.0, 4.0, 5.0]
        # None uses default_k_percent (0.20 -> 1 element)
        default_res = engine.sparsify_top_k(weights, k_percent=None)
        assert sum(1 for w in default_res if w != 0.0) == 1

        # 0.0 clamped to 0.01 (at least 1 element)
        zero_res = engine.sparsify_top_k(weights, k_percent=0.0)
        assert sum(1 for w in zero_res if w != 0.0) == 1

        # 1.5 clamped to 1.0 (all 5 elements retained)
        over_res = engine.sparsify_top_k(weights, k_percent=1.5)
        assert sum(1 for w in over_res if w != 0.0) == 5

    def test_sparsify_top_k_empty_and_zero_inputs(self, engine: GradientCompressionEngine) -> None:
        """Handles empty and all-zero weight vectors safely."""
        assert engine.sparsify_top_k([]) == []
        assert engine.sparsify_top_k(None) == []  # type: ignore[arg-type]

        all_zeros = [0.0] * 50
        res = engine.sparsify_top_k(all_zeros, k_percent=0.20)
        assert len(res) == 50
        assert all(w == 0.0 for w in res)


class TestLosslessCompression:
    """Validates lossless byte compression and decompression."""

    @pytest.fixture
    def engine(self) -> GradientCompressionEngine:
        return GradientCompressionEngine(compression_level=6)

    def test_compress_decompress_roundtrip(self, engine: GradientCompressionEngine) -> None:
        """Compressed payload decompresses to identical bytes."""
        original = b"FEDERATED_LEARNING_BANK_GRADIENT_UPDATE_PAYLOAD_TEST_VECTOR_12345" * 20
        compressed = engine.compress_payload(original)
        assert len(compressed) < len(original)
        decompressed = engine.decompress_payload(compressed)
        assert decompressed == original

    def test_compress_empty_payload(self, engine: GradientCompressionEngine) -> None:
        """Empty input bytes returns empty bytes."""
        assert engine.compress_payload(b"") == b""
        assert engine.decompress_payload(b"") == b""

    def test_compress_gradient_vector_backward_compatibility(
        self, engine: GradientCompressionEngine
    ) -> None:
        """Maintains backward compatibility with legacy compress_gradient_vector."""
        weights = [0.1 * i for i in range(100)]
        meta = engine.compress_gradient_vector(weights, k_percent=0.10)
        assert meta["original_length"] == 100
        assert meta["sparsified_length"] == 100
        assert meta["compressed_size"] > 0
        assert meta["raw_size"] > meta["compressed_size"]
        assert isinstance(meta["compressed_bytes"], bytes)


class TestBinaryQuantization:
    """Validates FP32, FP16 half-precision, and INT8 uniform symmetric quantization."""

    @pytest.fixture
    def engine(self) -> GradientCompressionEngine:
        return GradientCompressionEngine()

    def test_fp32_encode_decode_roundtrip(self, engine: GradientCompressionEngine) -> None:
        """FP32 binary serialization achieves zero error."""
        weights = [1.25, -3.5, 0.0, 42.125, -0.0078125]
        buf = engine.encode_fp32(weights)
        assert len(buf) == len(weights) * 4
        recovered = engine.decode_fp32(buf)
        assert len(recovered) == len(weights)
        for orig, rec in zip(weights, recovered, strict=False):
            assert math.isclose(orig, rec, rel_tol=1e-6)

    def test_fp16_encode_decode_precision(self, engine: GradientCompressionEngine) -> None:
        """FP16 half-precision reduces size by 50% with low reconstruction error."""
        weights = [0.1234, -0.5678, 1.9876, -3.1415, 0.0]
        buf = engine.encode_fp16(weights)
        assert len(buf) == len(weights) * 2  # exactly 2 bytes per float
        recovered = engine.decode_fp16(buf)
        assert len(recovered) == len(weights)
        for orig, rec in zip(weights, recovered, strict=False):
            assert abs(orig - rec) < 1e-3

    def test_int8_symmetric_quantization_bounded_error(
        self, engine: GradientCompressionEngine
    ) -> None:
        """INT8 symmetric affine quantization satisfies max error bound scale / 2."""
        rng = np.random.default_rng(42)
        weights = rng.uniform(-1.0, 1.0, size=200).tolist()
        buf = engine.encode_int8(weights)
        # 4 bytes scale header + 200 bytes quantized values
        assert len(buf) == 4 + 200
        recovered = engine.decode_int8(buf)
        assert len(recovered) == 200

        max_val = max(abs(w) for w in weights)
        scale = max_val / 127.0
        for orig, rec in zip(weights, recovered, strict=False):
            assert abs(orig - rec) <= (scale / 2.0) + 1e-5

    def test_quantization_empty_and_zero_inputs(self, engine: GradientCompressionEngine) -> None:
        """Handles empty buffers and zero vectors gracefully."""
        assert engine.encode_fp32([]) == b""
        assert engine.decode_fp32(b"") == []
        assert engine.encode_fp16([]) == b""
        assert engine.decode_fp16(b"") == []
        assert engine.encode_int8([]) == b""
        assert engine.decode_int8(b"") == []

        zeros = [0.0] * 10
        rec_int8 = engine.decode_int8(engine.encode_int8(zeros))
        assert len(rec_int8) == 10
        assert all(w == 0.0 for w in rec_int8)


class TestSparseCoordinateEncoding:
    """Validates Top-K Coordinate (COO) sparse binary format."""

    @pytest.fixture
    def engine(self) -> GradientCompressionEngine:
        return GradientCompressionEngine()

    def test_sparse_coo_encode_decode_roundtrip(self, engine: GradientCompressionEngine) -> None:
        """Top-K COO buffer decodes back to dense vector with non-zeros preserved."""
        weights = [0.0] * 500
        weights[10] = 5.25
        weights[100] = -3.50
        weights[250] = 8.125
        weights[499] = -1.75

        buf = engine.encode_sparse_coo(weights, k_percent=0.05)
        # Header (10 bytes) + 4 indices (8 bytes) + 4 float16 values (8 bytes) = 26 bytes
        assert len(buf) < 100
        recovered = engine.decode_sparse_coo(buf)
        assert len(recovered) == 500
        assert math.isclose(recovered[10], 5.25, rel_tol=1e-3)
        assert math.isclose(recovered[100], -3.50, rel_tol=1e-3)
        assert math.isclose(recovered[250], 8.125, rel_tol=1e-3)
        assert math.isclose(recovered[499], -1.75, rel_tol=1e-3)
        # Other elements are zero
        assert sum(1 for w in recovered if w != 0.0) == 4

    def test_sparse_coo_empty_and_invalid_buffers(self, engine: GradientCompressionEngine) -> None:
        """Corrupted or undersized COO buffers return safe empty lists."""
        assert engine.encode_sparse_coo([]) == b""
        assert engine.decode_sparse_coo(b"") == []
        assert engine.decode_sparse_coo(b"CORRUPTED") == []


class TestSerializationDispatcherAndProfiling:
    """Validates full serialization dispatcher and wire size hierarchy."""

    @pytest.fixture
    def engine(self) -> GradientCompressionEngine:
        return GradientCompressionEngine()

    def test_all_serialization_formats_produce_valid_roundtrip(
        self, engine: GradientCompressionEngine
    ) -> None:
        """All 7 SerializationFormat enums serialize and deserialize cleanly."""
        rng = np.random.default_rng(42)
        weights = rng.normal(0.0, 0.1, size=250).tolist()

        for fmt in SerializationFormat:
            wire = engine.serialize_payload(weights, payload_format=fmt, k_percent=0.20)
            assert isinstance(wire, bytes)
            assert len(wire) > 0

            rec = engine.deserialize_payload(wire, payload_format=fmt)
            assert len(rec) == 250
            if fmt in (SerializationFormat.RAW_JSON, SerializationFormat.RAW_FP32, SerializationFormat.ZSTD_COMPRESSED):
                for o, r in zip(weights, rec, strict=False):
                    assert math.isclose(o, r, abs_tol=1e-5)

    def test_wire_size_hierarchy(self, engine: GradientCompressionEngine) -> None:
        """Verifies wire size relationships:
        size(SPARSE_TOPK_ZSTD) < size(INT8) < size(FP16) < size(FP32) < size(RAW_JSON).
        """
        rng = np.random.default_rng(42)
        weights = rng.normal(0.0, 0.05, size=1969).tolist()

        profiles = engine.compare_all_serialization_formats(weights, k_percent=0.20)
        p_json = profiles[SerializationFormat.RAW_JSON.value]
        p_fp32 = profiles[SerializationFormat.RAW_FP32.value]
        p_fp16 = profiles[SerializationFormat.QUANTIZED_FP16.value]
        p_int8 = profiles[SerializationFormat.QUANTIZED_INT8.value]
        p_sparse_zstd = profiles[SerializationFormat.SPARSE_TOPK_ZSTD.value]

        assert p_json.wire_bytes > p_fp32.wire_bytes
        assert p_fp32.wire_bytes > p_fp16.wire_bytes
        assert p_fp16.wire_bytes > p_int8.wire_bytes
        assert p_int8.wire_bytes > p_sparse_zstd.wire_bytes

        # FP32 on 1,969 parameters is exactly 7,876 bytes
        assert p_fp32.wire_bytes == 1969 * 4
        # FP16 is exactly half of FP32
        assert p_fp16.wire_bytes == 1969 * 2
        # INT8 is 1 byte per param + 4 bytes header
        assert p_int8.wire_bytes == 1969 + 4

    def test_profile_serialized_payload_metrics(self, engine: GradientCompressionEngine) -> None:
        """SerializedPayloadProfile fields are consistent and bounded."""
        weights = [float(i) for i in range(100)]
        profile = engine.profile_serialized_payload(
            weights, payload_format=SerializationFormat.QUANTIZED_FP16
        )

        assert profile.num_parameters == 100
        assert profile.raw_uncompressed_bytes == 400
        assert profile.wire_bytes == 200
        assert profile.compression_ratio_vs_fp32 == 0.50
        assert profile.bandwidth_savings_pct == 50.0
        assert profile.serialization_time_us >= 0.0
        assert profile.deserialization_time_us >= 0.0
        assert profile.reconstruction_mae >= 0.0


class TestFederatedCommunicationProfiler:
    """Validates multi-round communication accounting across federated aggregation protocols."""

    @pytest.fixture
    def profiler(self) -> FederatedCommunicationProfiler:
        return FederatedCommunicationProfiler()

    def test_fedavg_and_fedprox_identical_bandwidth(
        self, profiler: FederatedCommunicationProfiler
    ) -> None:
        """FedProx has identical communication payload to FedAvg."""
        p_avg = profiler.profile_protocol(
            num_parameters=1969, num_clients=3, num_rounds=5, protocol=FederatedProtocol.FED_AVG
        )
        p_prox = profiler.profile_protocol(
            num_parameters=1969, num_clients=3, num_rounds=5, protocol=FederatedProtocol.FED_PROX
        )

        assert p_avg.total_round_bytes == p_prox.total_round_bytes
        assert p_avg.total_multi_round_bytes == p_prox.total_multi_round_bytes
        assert p_avg.relative_overhead_vs_fedavg == 1.0
        assert p_prox.relative_overhead_vs_fedavg == 1.0
        # 1 server broadcast (7,876) + 3 client uploads (3 * 7,876) = 31,504 bytes per round
        assert p_avg.total_round_bytes == 31504
        assert p_avg.total_multi_round_bytes == 31504 * 5

    def test_scaffold_exact_2x_overhead(self, profiler: FederatedCommunicationProfiler) -> None:
        """SCAFFOLD requires exactly 2x bandwidth due to control variates."""
        p_avg = profiler.profile_protocol(
            num_parameters=1969, num_clients=3, num_rounds=5, protocol=FederatedProtocol.FED_AVG
        )
        p_scaffold = profiler.profile_protocol(
            num_parameters=1969, num_clients=3, num_rounds=5, protocol=FederatedProtocol.SCAFFOLD
        )

        assert p_scaffold.total_round_bytes == 2 * p_avg.total_round_bytes
        assert p_scaffold.total_multi_round_bytes == 2 * p_avg.total_multi_round_bytes
        assert p_scaffold.relative_overhead_vs_fedavg == 2.0

    def test_curve25519_secagg_overhead_bounds(
        self, profiler: FederatedCommunicationProfiler
    ) -> None:
        """Curve25519 SecAgg adds minor cryptographic coordination overhead (4% - 10%)."""
        p_secagg = profiler.profile_protocol(
            num_parameters=1969,
            num_clients=3,
            num_rounds=5,
            protocol=FederatedProtocol.CURVE25519_SECAGG,
        )

        assert p_secagg.relative_overhead_vs_fedavg > 1.0
        assert p_secagg.relative_overhead_vs_fedavg < 1.15
        assert p_secagg.total_multi_round_bytes > 157520

    def test_tenseal_ckks_expansion_bounds(
        self, profiler: FederatedCommunicationProfiler
    ) -> None:
        """TenSEAL CKKS exhibits polynomial ciphertext expansion (~10.5x)."""
        p_ckks = profiler.profile_protocol(
            num_parameters=1969,
            num_clients=3,
            num_rounds=5,
            protocol=FederatedProtocol.TENSEAL_CKKS,
        )

        assert p_ckks.relative_overhead_vs_fedavg == 10.5
        assert p_ckks.total_multi_round_mb > 1.0

    def test_run_full_bandwidth_benchmark_suite(
        self, profiler: FederatedCommunicationProfiler
    ) -> None:
        """Executes complete suite across all 7 formats and 5 protocols."""
        suite = profiler.run_full_bandwidth_benchmark(
            num_clients=3, num_rounds=5, model_name="CanonicalFraudMLP"
        )
        assert isinstance(suite, BandwidthBenchmarkSuiteResult)
        assert suite.num_parameters == 1969
        assert len(suite.serialization_profiles) == len(SerializationFormat)
        assert len(suite.protocol_profiles) == len(FederatedProtocol)
        assert "RAW_FP32" in suite.serialization_profiles
        assert "FED_AVG" in suite.protocol_profiles


class TestFederatedEngineIntegration:
    """Validates live FederatedLearningEngine communication telemetry integration."""

    def test_fl_engine_profile_communication_round(self) -> None:
        """FederatedLearningEngine delegates communication profiling to profiler."""
        mock_settings = MagicMock()
        mock_model_svc = MagicMock()
        mock_privacy_svc = MagicMock()

        engine = FederatedLearningEngine(
            settings=mock_settings,
            model_service=mock_model_svc,
            privacy_service=mock_privacy_svc,
        )

        # Profile FedAvg round
        profile_avg = engine.profile_communication_round(
            num_parameters=1969,
            num_clients=3,
            method=AggregationMethod.FED_AVG,
            serialization_format=SerializationFormat.RAW_FP32,
        )
        assert isinstance(profile_avg, ProtocolCommunicationProfile)
        assert profile_avg.protocol == FederatedProtocol.FED_AVG
        assert profile_avg.total_round_bytes == 31504

        # Profile SCAFFOLD round
        profile_scaffold = engine.profile_communication_round(
            num_parameters=1969,
            num_clients=3,
            method=AggregationMethod.SCAFFOLD,
            serialization_format=SerializationFormat.RAW_FP32,
        )
        assert profile_scaffold.protocol == FederatedProtocol.SCAFFOLD
        assert profile_scaffold.total_round_bytes == 63008
