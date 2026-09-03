"""High-Performance Bidirectional Streaming gRPC Transport Layer for Federated Learning."""

from app.infrastructure.grpc.client import GRPCBankClient
from app.infrastructure.grpc.server import GRPCServerManager
from app.infrastructure.grpc.servicer import FederatedLearningServicer, register_bank_fingerprint
from app.infrastructure.grpc.types import (
    AggregationAck,
    ClientHeartbeat,
    ClientRegisterRequest,
    ClientRegisterResponse,
    CoordinatorCommand,
    CoordinatorStatus,
    ModelChunk,
    ModelDownloadRequest,
    ParameterChunk,
)

__all__ = [
    "AggregationAck",
    "ClientHeartbeat",
    "ClientRegisterRequest",
    "ClientRegisterResponse",
    "CoordinatorCommand",
    "CoordinatorStatus",
    "FederatedLearningServicer",
    "GRPCBankClient",
    "GRPCServerManager",
    "ModelChunk",
    "ModelDownloadRequest",
    "ParameterChunk",
    "register_bank_fingerprint",
]
