"""Data contracts and message types for gRPC Federated Learning Transport."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class CoordinatorCommand(IntEnum):
    """Command issued by FL coordinator to bank nodes."""

    IDLE = 0
    START_TRAINING = 1
    CANCEL_ROUND = 2
    UPDATE_CONFIG = 3


@dataclass
class ClientRegisterRequest:
    bank_id: str
    bank_name: str
    certificate_fingerprint: str
    software_version: str = "1.0.0"


@dataclass
class ClientRegisterResponse:
    session_token: str
    assigned_cluster_id: int
    is_accepted: bool


@dataclass
class ClientHeartbeat:
    bank_id: str
    timestamp: int
    cpu_utilization: float
    memory_utilization: float
    local_dataset_size: int


@dataclass
class CoordinatorStatus:
    command: CoordinatorCommand
    current_round: int
    global_model_version: str


@dataclass
class ParameterChunk:
    bank_id: str
    round_id: int
    chunk_index: int
    total_chunks: int
    encrypted_payload: bytes
    digital_signature: bytes = b""


@dataclass
class AggregationAck:
    received: bool
    status_message: str


@dataclass
class SubmitGradientRequest:
    round_id: str
    bank_id: str
    compressed_masked_gradient: bytes
    dp_epsilon_used: float
    participant_count: int
    signature: bytes
    protocol_version: str = "1.0.0"


@dataclass
class ModelDownloadRequest:
    bank_id: str
    target_version: str = "latest"


@dataclass
class ModelChunk:
    chunk_index: int
    total_chunks: int
    chunk_data: bytes
    sha256_checksum: str
