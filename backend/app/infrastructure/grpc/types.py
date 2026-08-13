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


# ---------------------------------------------------------------------------
# P2P SecAgg Key Exchange Messages (Version 2.0)
# ---------------------------------------------------------------------------


@dataclass
class ECDHBroadcastRequest:
    """Sent by a bank node to broadcast its ephemeral X25519 public key.

    The coordinator routes this to all other active participants and stores it
    for the duration of the key-exchange phase of the current round.
    """

    bank_id: str
    round_id: int
    public_key_bytes: bytes   # 32-byte raw X25519 public key
    hmac_signature: bytes     # HMAC-SHA256 over (bank_id || round_id || pk)
    protocol_version: str = "2.0.0"


@dataclass
class ECDHBroadcastResponse:
    """Coordinator acknowledgement of a received ECDH public key bundle."""

    accepted: bool
    status_message: str
    participant_count: int    # Number of active participants in this round


@dataclass
class PeerKeyEntry:
    """A single peer's authenticated public key bundle."""

    bank_id: str
    public_key_bytes: bytes
    hmac_signature: bytes


@dataclass
class PeerKeysRequest:
    """Request sent by a bank node to retrieve all peer public keys for a round."""

    requesting_bank_id: str
    round_id: int


@dataclass
class PeerKeysResponse:
    """All authenticated peer public key bundles for a given FL round."""

    round_id: int
    peer_keys: list[PeerKeyEntry]
    all_peers_ready: bool     # True when all expected participants have broadcast
