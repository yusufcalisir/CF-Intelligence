"""Unit test suite for High-Performance Bidirectional Streaming gRPC Transport Layer."""

from __future__ import annotations

import time

import pytest

from app.infrastructure.grpc.client import GRPCBankClient
from app.infrastructure.grpc.server import GRPCServerManager
from app.infrastructure.grpc.servicer import FederatedLearningServicer
from app.infrastructure.grpc.types import (
    ClientHeartbeat,
    CoordinatorCommand,
)


@pytest.mark.asyncio
async def test_grpc_client_registration() -> None:
    """Verifies client node gRPC registration and session token issuance."""
    servicer = FederatedLearningServicer()
    client = GRPCBankClient(servicer=servicer)

    response = await client.register(
        bank_id="bank_alpha",
        bank_name="Alpha International Bank",
        cert_fingerprint="SHA256:112233445566",
    )

    assert response.is_accepted is True
    assert response.session_token.startswith("grpc_sess_")
    assert 0 <= response.assigned_cluster_id <= 3
    assert client.session_token == response.session_token


@pytest.mark.asyncio
async def test_grpc_bidirectional_heartbeat_stream() -> None:
    """Verifies bidirectional streaming heartbeats yielding coordinator commands."""
    servicer = FederatedLearningServicer()
    client = GRPCBankClient(servicer=servicer)

    async def sample_heartbeats():
        yield ClientHeartbeat(
            bank_id="bank_alpha",
            timestamp=int(time.time()),
            cpu_utilization=15.4,
            memory_utilization=42.0,
            local_dataset_size=10000,
        )
        yield ClientHeartbeat(
            bank_id="bank_alpha",
            timestamp=int(time.time()) + 1,
            cpu_utilization=22.1,
            memory_utilization=45.5,
            local_dataset_size=10000,
        )

    statuses = []
    async for status in client.send_heartbeats(sample_heartbeats()):
        statuses.append(status)

    assert len(statuses) == 2
    assert statuses[0].command in (CoordinatorCommand.START_TRAINING, CoordinatorCommand.IDLE)
    assert statuses[0].global_model_version == "v1.0"


@pytest.mark.asyncio
async def test_grpc_stream_model_parameters() -> None:
    """Verifies client-streaming chunked parameter uploads and payload reassembly."""
    servicer = FederatedLearningServicer()
    client = GRPCBankClient(servicer=servicer)

    # 3.5 KB mock encrypted weights payload
    mock_payload = b"ENCRYPTED_PYTORCH_WEIGHTS_DATA_BLOCK_" * 100

    ack = await client.upload_model_parameters(
        bank_id="bank_beta",
        round_id=5,
        encrypted_weights_bytes=mock_payload,
        chunk_size=1024,
    )

    assert ack.received is True
    assert "Successfully aggregated" in ack.status_message
    assert str(len(mock_payload)) in ack.status_message


@pytest.mark.asyncio
async def test_grpc_download_global_model_chunks() -> None:
    """Verifies server-streaming global model download and SHA256 chunk validation."""
    servicer = FederatedLearningServicer()
    client = GRPCBankClient(servicer=servicer)

    model_bytes = await client.download_global_model(bank_id="bank_gamma", version="latest")

    assert len(model_bytes) > 0
    assert model_bytes == servicer.global_models["latest"]


@pytest.mark.asyncio
async def test_grpc_server_lifecycle() -> None:
    """Verifies GRPCServerManager start and stop lifecycle calls."""
    server_mgr = GRPCServerManager(port=50055)
    await server_mgr.start()
    assert server_mgr.is_running

    await server_mgr.stop()
    assert not server_mgr.is_running


# ---------------------------------------------------------------------------
# P2P SecAgg gRPC RPC Tests (Phase 2 - V2.0)
# ---------------------------------------------------------------------------


def _make_ecdh_request(
    bank_id: str,
    round_id: int,
    pk_bytes: bytes | None = None,
    version: str = "2.0.0",
) -> object:
    from app.infrastructure.grpc.types import ECDHBroadcastRequest
    return ECDHBroadcastRequest(
        bank_id=bank_id,
        round_id=round_id,
        public_key_bytes=pk_bytes or (b"\xab" * 32),
        hmac_signature=b"\xcd" * 32,
        protocol_version=version,
    )


@pytest.mark.asyncio
async def test_broadcast_public_key_accepted() -> None:
    """Valid X25519 bundle (32 bytes) must be accepted by the coordinator."""
    servicer = FederatedLearningServicer()
    req = _make_ecdh_request("bank_alpha", round_id=3)
    resp = await servicer.BroadcastPublicKey(req)

    assert resp.accepted is True
    assert resp.participant_count == 1
    assert 3 in servicer.secagg_key_store
    assert "bank_alpha" in servicer.secagg_key_store[3]


@pytest.mark.asyncio
async def test_broadcast_public_key_rejected_invalid_length() -> None:
    """Bundle with wrong PK length (not 32 bytes) must be rejected."""
    servicer = FederatedLearningServicer()
    req = _make_ecdh_request("bank_alpha", round_id=3, pk_bytes=b"\xab" * 31)
    resp = await servicer.BroadcastPublicKey(req)

    assert resp.accepted is False
    assert "32 bytes" in resp.status_message
    assert 3 not in servicer.secagg_key_store


@pytest.mark.asyncio
async def test_broadcast_public_key_rejected_wrong_version() -> None:
    """Bundle with unsupported protocol version must be rejected."""
    servicer = FederatedLearningServicer()
    req = _make_ecdh_request("bank_alpha", round_id=3, version="1.0.0")
    resp = await servicer.BroadcastPublicKey(req)

    assert resp.accepted is False
    assert "version" in resp.status_message.lower()


@pytest.mark.asyncio
async def test_broadcast_public_key_deduplication() -> None:
    """Re-broadcasting (network retry) from same bank overwrites without duplication."""
    servicer = FederatedLearningServicer()
    req1 = _make_ecdh_request("bank_alpha", round_id=5, pk_bytes=b"\x01" * 32)
    req2 = _make_ecdh_request("bank_alpha", round_id=5, pk_bytes=b"\x02" * 32)

    await servicer.BroadcastPublicKey(req1)
    resp = await servicer.BroadcastPublicKey(req2)

    assert resp.participant_count == 1  # still just one unique bank
    stored_pk = servicer.secagg_key_store[5]["bank_alpha"].public_key_bytes
    assert stored_pk == b"\x02" * 32   # latest overwrites


@pytest.mark.asyncio
async def test_fetch_peer_keys_excludes_self() -> None:
    """FetchPeerPublicKeys must not return the requesting bank's own bundle."""
    servicer = FederatedLearningServicer()
    for bank_id in ("bank_alpha", "bank_beta", "bank_gamma"):
        await servicer.BroadcastPublicKey(_make_ecdh_request(bank_id, round_id=7))

    from app.infrastructure.grpc.types import PeerKeysRequest
    resp = await servicer.FetchPeerPublicKeys(
        PeerKeysRequest(requesting_bank_id="bank_alpha", round_id=7)
    )

    returned_ids = {pk.bank_id for pk in resp.peer_keys}
    assert "bank_alpha" not in returned_ids
    assert returned_ids == {"bank_beta", "bank_gamma"}


@pytest.mark.asyncio
async def test_fetch_peer_keys_quorum_readiness() -> None:
    """all_peers_ready must be True only when DEFAULT_QUORUM (3) bundles are present."""
    servicer = FederatedLearningServicer()
    from app.infrastructure.grpc.types import PeerKeysRequest

    # 2 banks: not yet ready
    await servicer.BroadcastPublicKey(_make_ecdh_request("bank_alpha", round_id=9))
    await servicer.BroadcastPublicKey(_make_ecdh_request("bank_beta", round_id=9))
    resp = await servicer.FetchPeerPublicKeys(
        PeerKeysRequest(requesting_bank_id="bank_alpha", round_id=9)
    )
    assert resp.all_peers_ready is False

    # 3rd bank arrives: quorum met
    await servicer.BroadcastPublicKey(_make_ecdh_request("bank_gamma", round_id=9))
    resp2 = await servicer.FetchPeerPublicKeys(
        PeerKeysRequest(requesting_bank_id="bank_alpha", round_id=9)
    )
    assert resp2.all_peers_ready is True


@pytest.mark.asyncio
async def test_fetch_peer_keys_empty_round() -> None:
    """Fetching keys for a round where no broadcasts have occurred returns empty list."""
    servicer = FederatedLearningServicer()
    from app.infrastructure.grpc.types import PeerKeysRequest
    resp = await servicer.FetchPeerPublicKeys(
        PeerKeysRequest(requesting_bank_id="bank_alpha", round_id=99)
    )
    assert resp.peer_keys == []
    assert resp.all_peers_ready is False
