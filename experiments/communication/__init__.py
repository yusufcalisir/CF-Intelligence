"""Federated Communication Cost & Bandwidth Profiling Experiment Suite.

Profiles:
1. Serialized wire payload measurements across numerical formats (FP32, FP16, INT8, Top-K COO, Zstd)
2. Multi-round protocol bandwidth overheads (FedAvg, FedProx, SCAFFOLD, Curve25519 SecAgg, TenSEAL CKKS)
3. Return on Investment (ROI) of bandwidth consumption in financial fraud detection
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.infrastructure.security.compression_engine import (
    BandwidthBenchmarkSuiteResult,
    FederatedCommunicationProfiler,
    FederatedProtocol,
    GradientCompressionEngine,
    ProtocolCommunicationProfile,
    SerializationFormat,
    SerializedPayloadProfile,
)

__all__ = [
    "BandwidthBenchmarkSuiteResult",
    "FederatedCommunicationProfiler",
    "FederatedProtocol",
    "GradientCompressionEngine",
    "ProtocolCommunicationProfile",
    "SerializationFormat",
    "SerializedPayloadProfile",
]
