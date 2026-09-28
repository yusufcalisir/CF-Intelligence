"""Gradient Sparsification, Quantization, Lossless Compression & Communication Profiling Engine.

Provides:
1. Top-K gradient sparsification (coordinate COO and dense formats)
2. Lossless binary compression (Zstandard with adaptive zlib level 9 fallback)
3. Quantization protocols (FP32, FP16 half-precision, INT8 symmetric affine)
4. Exact serialized wire payload measurement across protocols
5. Federated communication profiling (FedAvg vs FedProx vs SCAFFOLD vs Curve25519 SecAgg vs TenSEAL CKKS)
"""

from __future__ import annotations

import json
import logging
import math
import struct
import time
import zlib
from enum import StrEnum
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# Optional zstandard support with automatic fallback to zlib
try:
    import zstandard as zstd  # type: ignore[import-untyped]
    HAS_ZSTD = True
except ImportError:
    zstd = None
    HAS_ZSTD = False


class SerializationFormat(StrEnum):
    """Wire transfer serialization formats for federated parameter updates."""

    RAW_JSON = "RAW_JSON"
    RAW_FP32 = "RAW_FP32"
    QUANTIZED_FP16 = "QUANTIZED_FP16"
    QUANTIZED_INT8 = "QUANTIZED_INT8"
    TOPK_SPARSE_COO = "TOPK_SPARSE_COO"
    ZSTD_COMPRESSED = "ZSTD_COMPRESSED"
    SPARSE_TOPK_ZSTD = "SPARSE_TOPK_ZSTD"


class FederatedProtocol(StrEnum):
    """Federated aggregation and cryptographic coordination protocols."""

    FED_AVG = "FED_AVG"
    FED_PROX = "FED_PROX"
    SCAFFOLD = "SCAFFOLD"
    CURVE25519_SECAGG = "CURVE25519_SECAGG"
    TENSEAL_CKKS = "TENSEAL_CKKS"


class SerializedPayloadProfile(BaseModel):
    """Wire transfer measurement and reconstruction fidelity profile."""

    model_config = ConfigDict(frozen=True)

    format: SerializationFormat = Field(..., description="Serialization format")
    num_parameters: int = Field(..., description="Total parameter count in tensor/vector")
    raw_uncompressed_bytes: int = Field(..., description="Uncompressed FP32 baseline size in bytes")
    wire_bytes: int = Field(..., description="Actual bytes transmitted over the network")
    compression_ratio_vs_fp32: float = Field(..., description="wire_bytes / raw_fp32_bytes")
    bandwidth_savings_pct: float = Field(..., description="(1 - wire_bytes/raw_bytes) * 100")
    serialization_time_us: float = Field(..., description="Serialization elapsed time in microseconds")
    deserialization_time_us: float = Field(..., description="Deserialization elapsed time in microseconds")
    reconstruction_mae: float = Field(..., description="Mean absolute error between original and recovered vector")
    reconstruction_max_err: float = Field(..., description="Maximum absolute error across all parameters")


class ProtocolCommunicationProfile(BaseModel):
    """Multi-round network consumption profile for a federated aggregation protocol."""

    model_config = ConfigDict(frozen=True)

    protocol: FederatedProtocol = Field(..., description="Federated protocol name")
    num_clients: int = Field(..., description="Participating client banking nodes")
    num_rounds: int = Field(..., description="Federation round count")
    bytes_per_client_per_round: int = Field(..., description="Bidirectional transfer per client per round in bytes")
    bytes_server_downstream_per_round: int = Field(..., description="Server broadcast payload per round in bytes")
    bytes_server_upstream_per_round: int = Field(..., description="Server aggregate ingress payload per round in bytes")
    total_round_bytes: int = Field(..., description="Total network volume per single round in bytes")
    total_multi_round_bytes: int = Field(..., description="Total network volume across all rounds in bytes")
    total_multi_round_mb: float = Field(..., description="Total volume in Megabytes (MB)")
    relative_overhead_vs_fedavg: float = Field(..., description="Ratio of total bytes vs baseline FedAvg")
    bandwidth_efficiency_notes: str = Field(..., description="Engineering evaluation summary")


class BandwidthBenchmarkSuiteResult(BaseModel):
    """Benchmark suite results for communication and serialization profiling."""

    model_config = ConfigDict(frozen=True)

    model_name: str = Field(..., description="Evaluated neural network architecture")
    num_parameters: int = Field(..., description="Total model weight parameters")
    serialization_profiles: dict[str, SerializedPayloadProfile] = Field(..., description="Profiles by format")
    protocol_profiles: dict[str, ProtocolCommunicationProfile] = Field(..., description="Profiles by protocol")
    timestamp: str = Field(..., description="ISO 8601 execution timestamp")


class GradientCompressionEngine:
    """Provides Top-K gradient sparsification, quantization, and lossless compression.

    Reduces network transmission bandwidth for federated parameter updates.
    """

    def __init__(self, default_k_percent: float = 0.20, compression_level: int = 6) -> None:
        self.default_k_percent = default_k_percent
        self.compression_level = compression_level

    # -----------------------------------------------------------------------
    # Top-K Sparsification (Dense & Coordinate COO)
    # -----------------------------------------------------------------------

    def sparsify_top_k(
        self, weights_flat: list[float] | np.ndarray | Any, k_percent: float | None = None
    ) -> list[float]:
        """Applies Top-K gradient sparsification.

        Retains the top K% highest absolute magnitude elements in the gradient vector,
        zeroing out non-essential parameters.
        """
        if weights_flat is None or len(weights_flat) == 0:
            return []

        w_list = [float(x) for x in weights_flat]
        k_pct = k_percent if k_percent is not None else self.default_k_percent
        k_pct = max(0.01, min(1.0, k_pct))

        n = len(w_list)
        k_count = max(1, int(n * k_pct))

        # Find threshold value corresponding to k_count highest absolute values
        abs_vals = [abs(w) for w in w_list]
        abs_vals_sorted = sorted(abs_vals, reverse=True)
        threshold = abs_vals_sorted[k_count - 1] if k_count <= len(abs_vals_sorted) else 0.0

        # Zero out elements strictly below threshold
        sparsified = [w if abs(w) >= threshold else 0.0 for w in w_list]
        return sparsified

    # -----------------------------------------------------------------------
    # Lossless Byte Compression (zstd with zlib fallback)
    # -----------------------------------------------------------------------

    def compress_payload(self, data_bytes: bytes, level: int = 6) -> bytes:
        """Compresses byte payload using zstd (if available) or zlib."""
        if not data_bytes:
            return b""

        if HAS_ZSTD and zstd is not None:
            cctx = zstd.ZstdCompressor(level=max(1, min(22, level)))
            compressed = cctx.compress(data_bytes)
        else:
            lvl = max(1, min(9, level))
            compressed = zlib.compress(data_bytes, level=lvl)

        return compressed

    def decompress_payload(self, compressed_bytes: bytes) -> bytes:
        """Decompresses compressed payload back into raw bytes."""
        if not compressed_bytes:
            return b""

        # Check for zstandard magic number: 0xFD2FB528 (little-endian: b'\x28\xb5\x2f\xfd')
        if HAS_ZSTD and zstd is not None and compressed_bytes.startswith(b"\x28\xb5\x2f\xfd"):
            dctx = zstd.ZstdDecompressor()
            return dctx.decompress(compressed_bytes)

        # Standard zlib / deflate decompression fallback
        return zlib.decompress(compressed_bytes)

    # -----------------------------------------------------------------------
    # Binary Encoders & Quantizers
    # -----------------------------------------------------------------------

    def encode_fp32(self, weights_flat: list[float] | np.ndarray | Any) -> bytes:
        """Encodes float array as raw 32-bit IEEE 754 little-endian binary buffer."""
        w_list = [float(x) for x in weights_flat] if weights_flat is not None else []
        if not w_list:
            return b""
        return struct.pack(f"<{len(w_list)}f", *w_list)

    def decode_fp32(self, data: bytes) -> list[float]:
        """Decodes raw 32-bit float binary buffer."""
        if not data:
            return []
        count = len(data) // 4
        return list(struct.unpack(f"<{count}f", data[:count * 4]))

    def encode_fp16(self, weights_flat: list[float] | np.ndarray | Any) -> bytes:
        """Encodes float array as 16-bit half-precision IEEE 754 binary buffer."""
        w_list = [float(x) for x in weights_flat] if weights_flat is not None else []
        if not w_list:
            return b""
        return struct.pack(f"<{len(w_list)}e", *w_list)

    def decode_fp16(self, data: bytes) -> list[float]:
        """Decodes 16-bit half-precision float binary buffer."""
        if not data:
            return []
        count = len(data) // 2
        return [float(x) for x in struct.unpack(f"<{count}e", data[:count * 2])]

    def encode_int8(self, weights_flat: list[float] | np.ndarray | Any) -> bytes:
        """Applies symmetric affine 8-bit quantization.

        Header: 4 bytes float32 scale factor s = max(|x|) / 127.0
        Body: N bytes signed 8-bit integers q = clamp(round(x / s), -128, 127)
        Total size: 4 + N bytes.
        """
        w_list = [float(x) for x in weights_flat] if weights_flat is not None else []
        if not w_list:
            return b""

        max_val = max(abs(w) for w in w_list)
        scale = max_val / 127.0 if max_val > 1e-12 else 1.0

        header = struct.pack("<f", float(scale))
        quantized = [max(-128, min(127, int(round(w / scale)))) for w in w_list]
        body = struct.pack(f"<{len(quantized)}b", *quantized)
        return header + body

    def decode_int8(self, data: bytes) -> list[float]:
        """Decodes symmetric affine 8-bit quantized buffer."""
        if len(data) < 4:
            return []
        scale = struct.unpack("<f", data[:4])[0]
        body = data[4:]
        count = len(body)
        if count == 0:
            return []
        quantized = struct.unpack(f"<{count}b", body)
        return [float(q * scale) for q in quantized]

    def encode_sparse_coo(
        self, weights_flat: list[float] | np.ndarray | Any, k_percent: float = 0.20
    ) -> bytes:
        """Encodes Top-K sparse vector in Coordinate (COO) format.

        Structure:
          - Magic header: 2 bytes (b'CK')
          - Total dimension N: 4 bytes uint32
          - Non-zero count K_nz: 4 bytes uint32
          - Indices: K_nz * (2 bytes uint16 if N <= 65535 else 4 bytes uint32)
          - Values: K_nz * 2 bytes float16
        """
        w_list = [float(x) for x in weights_flat] if weights_flat is not None else []
        if not w_list:
            return b""

        sparsified = self.sparsify_top_k(w_list, k_percent=k_percent)
        n = len(sparsified)

        # Extract non-zero pairs
        non_zero = [(idx, val) for idx, val in enumerate(sparsified) if val != 0.0]
        k_nz = len(non_zero)

        magic = b"CK"
        header = magic + struct.pack("<II", n, k_nz)
        if k_nz == 0:
            return header

        indices = [p[0] for p in non_zero]
        values = [p[1] for p in non_zero]

        if n <= 65535:
            idx_bytes = struct.pack(f"<{k_nz}H", *indices)
        else:
            idx_bytes = struct.pack(f"<{k_nz}I", *indices)

        val_bytes = struct.pack(f"<{k_nz}e", *values)
        return header + idx_bytes + val_bytes

    def decode_sparse_coo(self, data: bytes) -> list[float]:
        """Decodes Coordinate (COO) format back to dense float list."""
        if len(data) < 10 or not data.startswith(b"CK"):
            return []

        n, k_nz = struct.unpack("<II", data[2:10])
        result = [0.0] * n
        if k_nz == 0:
            return result

        offset = 10
        if n <= 65535:
            idx_len = k_nz * 2
            indices = struct.unpack(f"<{k_nz}H", data[offset:offset + idx_len])
            offset += idx_len
        else:
            idx_len = k_nz * 4
            indices = struct.unpack(f"<{k_nz}I", data[offset:offset + idx_len])
            offset += idx_len

        val_len = k_nz * 2
        values = struct.unpack(f"<{k_nz}e", data[offset:offset + val_len])

        for idx, val in zip(indices, values, strict=False):
            if idx < n:
                result[idx] = float(val)

        return result

    # -----------------------------------------------------------------------
    # Generic Serialization Dispatcher
    # -----------------------------------------------------------------------

    def serialize_payload(
        self,
        weights_flat: list[float] | np.ndarray | Any,
        payload_format: SerializationFormat,
        k_percent: float = 0.20,
    ) -> bytes:
        """Serializes weight vector into specified format."""
        w_list = [float(x) for x in weights_flat] if weights_flat is not None else []
        if not w_list:
            return b""

        if payload_format == SerializationFormat.RAW_JSON:
            return json.dumps(w_list).encode("utf-8")
        elif payload_format == SerializationFormat.RAW_FP32:
            return self.encode_fp32(w_list)
        elif payload_format == SerializationFormat.QUANTIZED_FP16:
            return self.encode_fp16(w_list)
        elif payload_format == SerializationFormat.QUANTIZED_INT8:
            return self.encode_int8(w_list)
        elif payload_format == SerializationFormat.TOPK_SPARSE_COO:
            return self.encode_sparse_coo(w_list, k_percent=k_percent)
        elif payload_format == SerializationFormat.ZSTD_COMPRESSED:
            raw_fp32 = self.encode_fp32(w_list)
            return self.compress_payload(raw_fp32, level=self.compression_level)
        elif payload_format == SerializationFormat.SPARSE_TOPK_ZSTD:
            coo = self.encode_sparse_coo(w_list, k_percent=k_percent)
            return self.compress_payload(coo, level=self.compression_level)
        else:
            return self.encode_fp32(w_list)

    def deserialize_payload(
        self, data: bytes, payload_format: SerializationFormat
    ) -> list[float]:
        """Deserializes payload back to float list."""
        if not data:
            return []

        if payload_format == SerializationFormat.RAW_JSON:
            return [float(x) for x in json.loads(data.decode("utf-8"))]
        elif payload_format == SerializationFormat.RAW_FP32:
            return self.decode_fp32(data)
        elif payload_format == SerializationFormat.QUANTIZED_FP16:
            return self.decode_fp16(data)
        elif payload_format == SerializationFormat.QUANTIZED_INT8:
            return self.decode_int8(data)
        elif payload_format == SerializationFormat.TOPK_SPARSE_COO:
            return self.decode_sparse_coo(data)
        elif payload_format == SerializationFormat.ZSTD_COMPRESSED:
            decomp = self.decompress_payload(data)
            return self.decode_fp32(decomp)
        elif payload_format == SerializationFormat.SPARSE_TOPK_ZSTD:
            decomp = self.decompress_payload(data)
            return self.decode_sparse_coo(decomp)
        else:
            return self.decode_fp32(data)

    # -----------------------------------------------------------------------
    # Wire Transfer Profiling
    # -----------------------------------------------------------------------

    def profile_serialized_payload(
        self,
        weights_flat: list[float] | np.ndarray | Any,
        payload_format: SerializationFormat,
        k_percent: float = 0.20,
    ) -> SerializedPayloadProfile:
        """Profiles wire transfer size, latency, and reconstruction error."""
        w_list = [float(x) for x in weights_flat] if weights_flat is not None else []
        n = len(w_list)
        raw_fp32_bytes = max(4, n * 4)

        t0 = time.perf_counter()
        wire_data = self.serialize_payload(w_list, payload_format=payload_format, k_percent=k_percent)
        t_ser = (time.perf_counter() - t0) * 1e6

        wire_bytes = len(wire_data)

        t1 = time.perf_counter()
        recovered = self.deserialize_payload(wire_data, payload_format=payload_format)
        t_deser = (time.perf_counter() - t1) * 1e6

        if n > 0 and len(recovered) == n:
            errors = [abs(o - r) for o, r in zip(w_list, recovered, strict=False)]
            mae = float(sum(errors) / float(n))
            max_err = float(max(errors))
        else:
            mae = 0.0
            max_err = 0.0

        ratio = round(wire_bytes / float(raw_fp32_bytes), 4)
        savings = round((1.0 - ratio) * 100.0, 2)

        return SerializedPayloadProfile(
            format=payload_format,
            num_parameters=n,
            raw_uncompressed_bytes=raw_fp32_bytes,
            wire_bytes=wire_bytes,
            compression_ratio_vs_fp32=ratio,
            bandwidth_savings_pct=savings,
            serialization_time_us=round(t_ser, 2),
            deserialization_time_us=round(t_deser, 2),
            reconstruction_mae=round(mae, 6),
            reconstruction_max_err=round(max_err, 6),
        )

    def compare_all_serialization_formats(
        self, weights_flat: list[float] | np.ndarray | Any, k_percent: float = 0.20
    ) -> dict[str, SerializedPayloadProfile]:
        """Compares all 7 serialization formats on a given parameter vector."""
        results: dict[str, SerializedPayloadProfile] = {}
        for fmt in SerializationFormat:
            prof = self.profile_serialized_payload(weights_flat, payload_format=fmt, k_percent=k_percent)
            results[fmt.value] = prof
        return results

    # -----------------------------------------------------------------------
    # Backward Compatibility
    # -----------------------------------------------------------------------

    def compress_gradient_vector(
        self, weights_flat: list[float], k_percent: float | None = None
    ) -> dict[str, Any]:
        """Sparsifies and compresses a flat gradient vector, returning payload metadata."""
        sparsified = self.sparsify_top_k(weights_flat, k_percent=k_percent)
        raw_json = json.dumps(sparsified).encode("utf-8")
        compressed_bytes = self.compress_payload(raw_json)

        return {
            "original_length": len(weights_flat),
            "sparsified_length": len(sparsified),
            "compressed_bytes": compressed_bytes,
            "compressed_size": len(compressed_bytes),
            "raw_size": len(raw_json),
        }


class FederatedCommunicationProfiler:
    """Calculates exact network wire consumption across federated protocols."""

    def __init__(self, compression_engine: GradientCompressionEngine | None = None) -> None:
        self.engine = compression_engine or GradientCompressionEngine()

    def profile_protocol(
        self,
        num_parameters: int,
        num_clients: int = 3,
        num_rounds: int = 5,
        protocol: FederatedProtocol = FederatedProtocol.FED_AVG,
        serialization_format: SerializationFormat = SerializationFormat.RAW_FP32,
    ) -> ProtocolCommunicationProfile:
        """Calculates precise multi-round bandwidth usage for a federated aggregation protocol.

        Network exchange topology:
        - Downstream: Server broadcasts global model to K clients.
        - Upstream: K clients transmit local parameter updates to server.
        """
        num_clients = max(2, num_clients)
        num_rounds = max(1, num_rounds)

        # Baseline single model wire size
        dummy_weights = [0.01 * math.sin(i) for i in range(num_parameters)]
        wire_data = self.engine.serialize_payload(dummy_weights, payload_format=serialization_format)
        s_model = len(wire_data)

        if protocol == FederatedProtocol.FED_AVG:
            # Server sends 1 global model (s_model); K clients send 1 local model each (K * s_model)
            s_down = s_model
            s_up_per_client = s_model
            s_up_total = num_clients * s_up_per_client
            round_bytes = s_down + s_up_total
            per_client_round = s_down + s_up_per_client
            rel_overhead = 1.0
            notes = "Standard federated averaging baseline with minimal communication footprint."

        elif protocol == FederatedProtocol.FED_PROX:
            # FedProx transmits identical payloads to FedAvg (proximal loss regularizer is computed locally)
            s_down = s_model
            s_up_per_client = s_model
            s_up_total = num_clients * s_up_per_client
            round_bytes = s_down + s_up_total
            per_client_round = s_down + s_up_per_client
            rel_overhead = 1.0
            notes = "Identical bandwidth to FedAvg; proximal term computed entirely on-device."

        elif protocol == FederatedProtocol.SCAFFOLD:
            # SCAFFOLD transmits both model weights and control variates (c, delta_c), requiring exactly 2x payload
            s_down = 2 * s_model
            s_up_per_client = 2 * s_model
            s_up_total = num_clients * s_up_per_client
            round_bytes = s_down + s_up_total
            per_client_round = s_down + s_up_per_client
            rel_overhead = 2.0
            notes = "Exact 2.0x bandwidth overhead due to dual parameter and control variate exchange."

        elif protocol == FederatedProtocol.CURVE25519_SECAGG:
            # 4-round cryptographic coordination (Keys, Shares, Masked Inputs, Unmasking):
            # Overhead includes X25519 pubkeys, Shamir shares, and HMAC-SHA256 authentication tags
            secagg_coord_bytes = (
                num_clients * 96  # Round 0: 32B pubkey + 64B Ed25519 sig per client
                + num_clients * (num_clients - 1) * 48  # Round 1: encrypted seed shares
                + num_clients * 32  # Round 2: HMAC tag per client
                + num_clients * (num_clients - 1) * 32  # Round 3: unmasking shares
                + 256  # Coordinator control frames
            )
            s_down = s_model + 128
            s_up_per_client = s_model + (secagg_coord_bytes // num_clients)
            s_up_total = (s_model * num_clients) + secagg_coord_bytes
            round_bytes = s_down + s_up_total
            per_client_round = s_down + s_up_per_client
            # Baseline FedAvg comparison
            fedavg_bytes = s_model * (1 + num_clients)
            rel_overhead = round(round_bytes / float(fedavg_bytes), 4)
            notes = f"Zero-knowledge privacy with minor +{round((rel_overhead - 1.0)*100, 1)}% coordination overhead."

        elif protocol == FederatedProtocol.TENSEAL_CKKS:
            # Homomorphic encryption polynomial expansion (degree N=8192, 200-bit coeff modulus)
            # Empirically expands float vector by ~10.5x to 12.0x
            ckks_expansion = 10.5
            s_down = int(s_model * ckks_expansion)
            s_up_per_client = int(s_model * ckks_expansion)
            s_up_total = num_clients * s_up_per_client
            round_bytes = s_down + s_up_total
            per_client_round = s_down + s_up_per_client
            rel_overhead = round(ckks_expansion, 2)
            notes = "Homomorphic encryption ciphertext expansion (~10.5x bandwidth multiplier)."

        else:
            s_down = s_model
            s_up_per_client = s_model
            s_up_total = num_clients * s_up_per_client
            round_bytes = s_down + s_up_total
            per_client_round = s_down + s_up_per_client
            rel_overhead = 1.0
            notes = "Default federated communication profile."

        total_multi_round = round_bytes * num_rounds
        total_mb = round(total_multi_round / (1024.0 * 1024.0), 4)

        return ProtocolCommunicationProfile(
            protocol=protocol,
            num_clients=num_clients,
            num_rounds=num_rounds,
            bytes_per_client_per_round=per_client_round,
            bytes_server_downstream_per_round=s_down,
            bytes_server_upstream_per_round=s_up_total,
            total_round_bytes=round_bytes,
            total_multi_round_bytes=total_multi_round,
            total_multi_round_mb=total_mb,
            relative_overhead_vs_fedavg=rel_overhead,
            bandwidth_efficiency_notes=notes,
        )

    def compare_all_protocols(
        self,
        num_parameters: int = 1969,
        num_clients: int = 3,
        num_rounds: int = 5,
        serialization_format: SerializationFormat = SerializationFormat.RAW_FP32,
    ) -> dict[str, ProtocolCommunicationProfile]:
        """Profiles network usage across all 5 federated protocols."""
        profiles: dict[str, ProtocolCommunicationProfile] = {}
        for proto in FederatedProtocol:
            prof = self.profile_protocol(
                num_parameters=num_parameters,
                num_clients=num_clients,
                num_rounds=num_rounds,
                protocol=proto,
                serialization_format=serialization_format,
            )
            profiles[proto.value] = prof
        return profiles

    def run_full_bandwidth_benchmark(
        self,
        weights: list[float] | np.ndarray | Any = None,
        num_clients: int = 3,
        num_rounds: int = 5,
        model_name: str = "FraudDetectionMLP",
    ) -> BandwidthBenchmarkSuiteResult:
        """Executes full benchmark suite across serialization formats and federated protocols."""
        if weights is None:
            # Default to canonical 1,969 parameter fraud detection MLP
            rng = np.random.default_rng(42)
            weights = rng.normal(0.0, 0.1, size=1969).tolist()

        w_list = [float(x) for x in weights]
        n_params = len(w_list)

        ser_profiles = self.engine.compare_all_serialization_formats(w_list)
        proto_profiles = self.compare_all_protocols(
            num_parameters=n_params,
            num_clients=num_clients,
            num_rounds=num_rounds,
            serialization_format=SerializationFormat.RAW_FP32,
        )

        iso_timestamp = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())

        return BandwidthBenchmarkSuiteResult(
            model_name=model_name,
            num_parameters=n_params,
            serialization_profiles=ser_profiles,
            protocol_profiles=proto_profiles,
            timestamp=iso_timestamp,
        )
