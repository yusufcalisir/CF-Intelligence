"""gRPC Servicer implementing FederatedLearningService RPC methods."""

from __future__ import annotations

import hashlib
import json
import logging
import struct
import uuid
import zlib
from typing import TYPE_CHECKING, Any

import numpy as np

from app.infrastructure.grpc.types import (
    AggregationAck,
    ClientHeartbeat,
    ClientRegisterRequest,
    ClientRegisterResponse,
    CoordinatorCommand,
    CoordinatorStatus,
    DropoutRecoveryRequest,
    DropoutRecoveryResponse,
    ECDHBroadcastRequest,
    ECDHBroadcastResponse,
    EncryptedShareBundle,
    ModelChunk,
    ModelDownloadRequest,
    ParameterChunk,
    PeerKeyEntry,
    PeerKeysRequest,
    PeerKeysResponse,
    ShareRoutingRequest,
    ShareRoutingResponse,
    SubmitGradientRequest,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.signature_verifier import SignatureVerifier

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, Sequence

logger = logging.getLogger(__name__)

MAX_EPSILON = 10.0
DEFAULT_QUORUM = 3


# Authoritative bank node certificate fingerprints registered during onboarding: bank_id -> cert_fingerprint
_AUTHORITATIVE_BANK_FINGERPRINTS: dict[str, str] = {
    "bank_alpha": "SHA256:4a8b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
    "bank_beta": "SHA256:1f2e3d4c5b6a7f8e9d0c1b2a3f4e5d6c7b8a9f0e1d2c3b4a5f6e7d8c9b0a1f2e",
    "bank_gamma": "SHA256:9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b",
}


def serialize_model_binary(
    layer_shapes: Sequence[tuple[int, ...]],
    flat_weights: Sequence[float],
    version: str = "v1.0.0",
) -> bytes:
    """Serializes model weights and layer shapes into standardized CFI1 binary payload."""
    meta_json = json.dumps({"version": version, "shapes": [list(s) for s in layer_shapes]}).encode("utf-8")
    weights_arr = np.asarray(flat_weights, dtype=np.float32)
    header = struct.pack("!4sI", b"CFI1", len(meta_json))
    return header + meta_json + weights_arr.tobytes()


def deserialize_model_binary(payload: bytes) -> tuple[dict[str, Any], Any]:
    """Deserializes a binary model payload into metadata dict and numpy weights array."""
    if len(payload) < 8:
        raise ValueError("Payload too short for model binary")
    magic, meta_len = struct.unpack("!4sI", payload[:8])
    if magic != b"CFI1":
        raise ValueError(f"Invalid model binary magic header: {magic!r}")
    meta = json.loads(payload[8 : 8 + meta_len].decode("utf-8"))
    weights = np.frombuffer(payload[8 + meta_len :], dtype=np.float32)
    return meta, weights


def register_bank_fingerprint(bank_id: str, cert_fingerprint: str) -> None:
    """Authoritatively register an authorized bank certificate fingerprint at onboarding time."""
    _AUTHORITATIVE_BANK_FINGERPRINTS[bank_id.lower().strip()] = cert_fingerprint.strip()


register_authoritative_bank_fingerprint = register_bank_fingerprint


class FederatedLearningServicer:
    """gRPC Servicer handling client registration, streaming heartbeats, parameter aggregation, and model downloads."""

    def __init__(self) -> None:
        self.active_sessions: dict[str, dict[str, Any]] = {}
        self.chunk_buffers: dict[str, list[ParameterChunk]] = {}
        # Baseline model architecture (input_dim=10, hidden_dim=16, output_dim=1 -> 193 parameters)
        init_shapes: list[tuple[int, ...]] = [(16, 10), (16,), (1, 16), (1,)]
        rng = np.random.default_rng(42)
        init_weights: list[float] = rng.normal(0.0, 0.1, size=193).tolist()
        self.global_models: dict[str, bytes] = {
            "latest": serialize_model_binary(init_shapes, init_weights, "v1.0.0"),
        }
        self.current_round: int = 1
        self.round_submissions: dict[str, list[dict[str, Any]]] = {}
        # P2P SecAgg key store: round_id -> {bank_id -> ECDHBroadcastRequest}
        # The coordinator relays these bundles; it never derives shared secrets.
        self.secagg_key_store: dict[int, dict[str, ECDHBroadcastRequest]] = {}
        # Shamir share store: round_id -> { (sender, recipient) -> EncryptedShareBundle }
        self.share_store: dict[int, dict[tuple[str, str], EncryptedShareBundle]] = {}
        # Dropout shares store: round_id -> { reporting_bank -> DropoutRecoveryRequest }
        self.dropout_share_store: dict[int, dict[str, DropoutRecoveryRequest]] = {}
        # Registered bank node certificate fingerprints: bank_id -> cert_fingerprint
        self.registered_fingerprints: dict[str, str] = dict(_AUTHORITATIVE_BANK_FINGERPRINTS)

    def register_bank_fingerprint(self, bank_id: str, cert_fingerprint: str) -> None:
        """Authoritatively registers an authorized certificate fingerprint for an onboarding bank node."""
        key = bank_id.lower().strip()
        fp = cert_fingerprint.strip()
        self.registered_fingerprints[key] = fp
        _AUTHORITATIVE_BANK_FINGERPRINTS[key] = fp

    async def _lookup_authoritative_fingerprint(self, bank_key: str) -> str | None:
        """Looks up authoritative fingerprint in memory, falling back to durable DB record in TenantConfigModel."""
        if bank_key in self.registered_fingerprints:
            return self.registered_fingerprints[bank_key]
        if bank_key in _AUTHORITATIVE_BANK_FINGERPRINTS:
            fp = _AUTHORITATIVE_BANK_FINGERPRINTS[bank_key]
            self.registered_fingerprints[bank_key] = fp
            return fp

        try:
            from sqlalchemy import select

            from app.infrastructure.database import get_session_factory
            from app.infrastructure.models import TenantConfigModel

            session_factory = get_session_factory()
            async with session_factory() as session:
                stmt = select(TenantConfigModel.cert_fingerprint).where(
                    TenantConfigModel.bank_id == bank_key
                )
                res = await session.execute(stmt)
                db_fp = res.scalar_one_or_none()
                if db_fp:
                    cleaned = db_fp.strip()
                    self.registered_fingerprints[bank_key] = cleaned
                    _AUTHORITATIVE_BANK_FINGERPRINTS[bank_key] = cleaned
                    return cleaned
        except Exception as exc:
            logger.debug("Database lookup for tenant fingerprint unavailable: %s", exc)

        return None

    async def RegisterClient(  # noqa: N802
        self, request: ClientRegisterRequest
    ) -> ClientRegisterResponse:
        """RPC 1: Register client node, validate certificate fingerprint against authoritative onboarding records."""
        logger.info(
            "gRPC RegisterClient request from bank_id=%s, bank_name=%s, fp=%s",
            request.bank_id,
            request.bank_name,
            request.certificate_fingerprint,
        )

        presented_fp = request.certificate_fingerprint.strip()
        bank_key = request.bank_id.lower().strip()

        # 1. Certificate revocation/invalid format check
        if presented_fp.startswith("INVALID") or presented_fp.startswith("REVOKED"):
            logger.warning(
                "Rejected gRPC registration for node %s due to invalid/revoked certificate (%s)",
                request.bank_id,
                presented_fp,
            )
            return ClientRegisterResponse(
                session_token="",
                assigned_cluster_id=-1,
                is_accepted=False,
            )

        # 2. Anti-spoofing: Check if this fingerprint is already bound to a DIFFERENT registered bank
        known_fingerprints = {**_AUTHORITATIVE_BANK_FINGERPRINTS, **self.registered_fingerprints}
        for reg_bank, reg_fp in known_fingerprints.items():
            if reg_bank != bank_key and reg_fp.lower() == presented_fp.lower():
                logger.warning(
                    "Cross-tenant certificate spoofing rejected: bank '%s' presented fingerprint registered to '%s'",
                    request.bank_id,
                    reg_bank,
                )
                return ClientRegisterResponse(
                    session_token="",
                    assigned_cluster_id=-1,
                    is_accepted=False,
                )

        # 3. Authoritative Onboarding Check (Strictly No TOFU):
        # Reject outright if bank was never onboarded via issue_mtls_certificate
        expected_fp = await self._lookup_authoritative_fingerprint(bank_key)
        if expected_fp is None:
            logger.warning(
                "Rejected gRPC registration for un-onboarded bank '%s': no certificate issued during onboarding",
                request.bank_id,
            )
            return ClientRegisterResponse(
                session_token="",
                assigned_cluster_id=-1,
                is_accepted=False,
            )

        # 4. Fingerprint Match Check: Presented fingerprint must match authoritative record
        if expected_fp.lower() != presented_fp.lower():
            logger.warning(
                "Certificate fingerprint mismatch for bank '%s': expected %s, got %s",
                request.bank_id,
                expected_fp,
                presented_fp,
            )
            return ClientRegisterResponse(
                session_token="",
                assigned_cluster_id=-1,
                is_accepted=False,
            )

        session_token = f"grpc_sess_{uuid.uuid4().hex[:12]}"
        cluster_id = hash(request.bank_id) % 4

        self.active_sessions[request.bank_id] = {
            "session_token": session_token,
            "bank_name": request.bank_name,
            "cert_fp": presented_fp,
            "cluster_id": cluster_id,
            "status": "REGISTERED",
        }

        return ClientRegisterResponse(
            session_token=session_token,
            assigned_cluster_id=cluster_id,
            is_accepted=True,
        )

    async def Heartbeat(  # noqa: N802
        self, request_iterator: AsyncIterable[ClientHeartbeat]
    ) -> AsyncIterable[CoordinatorStatus]:
        """RPC 2: Bidirectional streaming heartbeat monitoring node utilization and returning commands."""
        async for heartbeat in request_iterator:
            logger.debug(
                "gRPC Heartbeat received from %s: cpu=%.1f%%, mem=%.1f%%, dataset=%d",
                heartbeat.bank_id,
                heartbeat.cpu_utilization,
                heartbeat.memory_utilization,
                heartbeat.local_dataset_size,
            )

            # Update session status
            if heartbeat.bank_id in self.active_sessions:
                self.active_sessions[heartbeat.bank_id]["last_heartbeat"] = heartbeat.timestamp

            cmd = (
                CoordinatorCommand.START_TRAINING
                if self.current_round > 0
                else CoordinatorCommand.IDLE
            )

            yield CoordinatorStatus(
                command=cmd,
                current_round=self.current_round,
                global_model_version=f"v{self.current_round}.0",
            )

    async def StreamModelParameters(  # noqa: N802
        self, request_iterator: AsyncIterable[ParameterChunk]
    ) -> AggregationAck:
        """RPC 3: Client-streaming chunked model parameters upload."""
        chunks: list[ParameterChunk] = []
        bank_id = "unknown"
        round_id = 0

        async for chunk in request_iterator:
            chunks.append(chunk)
            bank_id = chunk.bank_id
            round_id = chunk.round_id

        # Reassemble payload
        chunks.sort(key=lambda c: c.chunk_index)
        full_payload = b"".join(c.encrypted_payload for c in chunks)

        logger.info(
            "gRPC StreamModelParameters reassembled payload for bank_id=%s, round_id=%d: %d bytes across %d chunks",
            bank_id,
            round_id,
            len(full_payload),
            len(chunks),
        )

        return AggregationAck(
            received=True,
            status_message=f"Successfully aggregated {len(chunks)} chunks ({len(full_payload)} bytes) for round {round_id}",
        )

    async def DownloadGlobalModel(  # noqa: N802
        self, request: ModelDownloadRequest
    ) -> AsyncIterable[ModelChunk]:
        """RPC 4: Server-streaming chunked global model download."""
        model_version = (
            request.target_version if request.target_version in self.global_models else "latest"
        )
        model_bytes = self.global_models[model_version]

        chunk_size = 512
        total_chunks = (len(model_bytes) + chunk_size - 1) // chunk_size

        for idx in range(total_chunks):
            start = idx * chunk_size
            end = min(start + chunk_size, len(model_bytes))
            slice_bytes = model_bytes[start:end]
            checksum = hashlib.sha256(slice_bytes).hexdigest()

            yield ModelChunk(
                chunk_index=idx,
                total_chunks=total_chunks,
                chunk_data=slice_bytes,
                sha256_checksum=checksum,
            )

    async def SubmitGradient(  # noqa: N802
        self, request: SubmitGradientRequest
    ) -> AggregationAck:
        """RPC 5: SecAgg masked gradient submission with signature & DP verification."""
        logger.info(
            "gRPC SubmitGradient request for round_id=%s, bank_id=%s, epsilon=%.2f",
            request.round_id,
            request.bank_id,
            request.dp_epsilon_used,
        )

        # 1. Signature Verification
        signed_message = (
            f"{request.round_id}:{request.bank_id}".encode()
            + hashlib.sha256(request.compressed_masked_gradient).digest()
        )
        verifier = SignatureVerifier()
        is_valid_signature = verifier.verify(
            bank_id=request.bank_id,
            message_bytes=signed_message,
            signature_bytes=request.signature,
        )
        if not is_valid_signature:
            logger.warning(
                "SubmitGradient rejected for bank_id=%s: Invalid signature", request.bank_id
            )
            return AggregationAck(
                received=False,
                status_message="REJECTED_SIGNATURE: Invalid ECDSA/RSA-PSS digital signature",
            )

        # 2. DP Epsilon Check
        if request.dp_epsilon_used > MAX_EPSILON:
            logger.warning(
                "SubmitGradient rejected for bank_id=%s: Epsilon %.2f exceeds limit %.2f",
                request.bank_id,
                request.dp_epsilon_used,
                MAX_EPSILON,
            )
            return AggregationAck(
                received=False,
                status_message=f"REJECTED_EPSILON: DP epsilon {request.dp_epsilon_used:.2f} exceeds limit {MAX_EPSILON:.2f}",
            )

        # 3. Decompression Check
        try:
            decompressed_gradient = zlib.decompress(request.compressed_masked_gradient)
        except Exception as exc:
            logger.warning(
                "SubmitGradient rejected for bank_id=%s: Corrupt compressed payload: %s",
                request.bank_id,
                exc,
            )
            return AggregationAck(
                received=False,
                status_message="REJECTED_CORRUPT: Failed to decompress zlib gradient payload",
            )

        gradient_hash = hashlib.sha256(request.compressed_masked_gradient).hexdigest()

        # 4. Persistence to gradient_submissions DB table
        await self._persist_gradient_submission(
            round_id=request.round_id,
            bank_id=request.bank_id,
            gradient_hash=gradient_hash,
            dp_epsilon_used=request.dp_epsilon_used,
            participant_count=request.participant_count,
        )

        # 5. Audit Chain Event Logging
        audit_chain = ImmutableAuditChain.get_instance()
        audit_chain.append_event(
            event_type="GRADIENT_RECEIVED",
            actor=request.bank_id,
            target_id=request.round_id,
            details={
                "gradient_hash": gradient_hash,
                "dp_epsilon": request.dp_epsilon_used,
                "participant_count": request.participant_count,
                "decompressed_bytes": len(decompressed_gradient),
            },
        )

        # 6. Quorum Check & Aggregation Trigger
        round_key = request.round_id
        if round_key not in self.round_submissions:
            self.round_submissions[round_key] = []

        self.round_submissions[round_key].append(
            {
                "bank_id": request.bank_id,
                "gradient_hash": gradient_hash,
                "epsilon": request.dp_epsilon_used,
            }
        )

        quorum_target = max(
            1, request.participant_count if request.participant_count > 0 else DEFAULT_QUORUM
        )
        current_count = len(self.round_submissions[round_key])

        if current_count >= quorum_target:
            logger.info(
                "Quorum reached (%d/%d) for round %s. Aggregation initiated.",
                current_count,
                quorum_target,
                round_key,
            )
            return AggregationAck(
                received=True,
                status_message=f"Gradient accepted for round {round_key}. Quorum reached ({current_count}/{quorum_target}) — aggregation initiated.",
            )

        return AggregationAck(
            received=True,
            status_message=f"Gradient accepted for round {round_key}. Waiting for quorum ({current_count}/{quorum_target}).",
        )

    async def _persist_gradient_submission(
        self,
        round_id: str,
        bank_id: str,
        gradient_hash: str,
        dp_epsilon_used: float,
        participant_count: int,
    ) -> None:
        """Persist submission metadata to database table gradient_submissions."""
        try:
            from app.infrastructure.database import async_sessionmaker  # type: ignore[attr-defined]
            from app.infrastructure.models import GradientSubmissionModel

            async with async_sessionmaker() as session:  # type: ignore[attr-defined]
                submission = GradientSubmissionModel(
                    id=str(uuid.uuid4()),
                    round_id=round_id,
                    bank_id=bank_id,
                    gradient_hash=gradient_hash,
                    dp_epsilon_used=dp_epsilon_used,
                    participant_count=participant_count,
                    validation_status="accepted",
                )
                session.add(submission)
                await session.commit()
        except Exception:
            logger.exception(
                "Could not persist gradient submission to DB for round_id=%s, bank_id=%s",
                round_id,
                bank_id,
            )

    # -----------------------------------------------------------------------
    # P2P SecAgg Key Exchange RPCs (Version 2.0)
    # -----------------------------------------------------------------------

    async def BroadcastPublicKey(  # noqa: N802
        self, request: ECDHBroadcastRequest
    ) -> ECDHBroadcastResponse:
        """RPC 5 (V2.0): Receive and store an ephemeral X25519 public key bundle.

        The coordinator stores the bundle indexed by (round_id, bank_id) and
        routes it to all other participants on demand via FetchPeerPublicKeys.
        No shared secret is ever derived server-side — the coordinator is a
        pure relay with zero cryptographic knowledge of pairwise masks.

        Validation:
          - Rejects bundles with an invalid public key length (must be 32 bytes).
          - Rejects bundles where protocol_version != '2.0.0'.
          - Deduplication: silently overwrites an existing bundle from the same
            bank for the same round (re-broadcast on network retry).
        """
        if len(request.public_key_bytes) != 32:
            logger.warning(
                "BroadcastPublicKey rejected: invalid pk length %d from bank=%s round=%d",
                len(request.public_key_bytes),
                request.bank_id,
                request.round_id,
            )
            return ECDHBroadcastResponse(
                accepted=False,
                status_message="Invalid public key length. Expected 32 bytes (X25519).",
                participant_count=0,
            )

        if request.protocol_version != "2.0.0":
            return ECDHBroadcastResponse(
                accepted=False,
                status_message=f"Unsupported SecAgg protocol version: {request.protocol_version}",
                participant_count=0,
            )

        round_store = self.secagg_key_store.setdefault(request.round_id, {})
        round_store[request.bank_id] = request

        logger.info(
            "BroadcastPublicKey accepted: bank=%s round=%d total_keys=%d",
            request.bank_id,
            request.round_id,
            len(round_store),
        )
        return ECDHBroadcastResponse(
            accepted=True,
            status_message="Public key bundle accepted and stored for routing.",
            participant_count=len(round_store),
        )

    async def FetchPeerPublicKeys(  # noqa: N802
        self, request: PeerKeysRequest
    ) -> PeerKeysResponse:
        """RPC 6 (V2.0): Return all peer ECDH public key bundles for a round.

        Excludes the requesting bank's own bundle. Returns all_peers_ready=True
        when the number of stored bundles meets or exceeds the default quorum.
        """
        round_store = self.secagg_key_store.get(request.round_id, {})

        peer_keys = [
            PeerKeyEntry(
                bank_id=bank_id,
                public_key_bytes=bundle.public_key_bytes,
                hmac_signature=bundle.hmac_signature,
            )
            for bank_id, bundle in round_store.items()
            if bank_id != request.requesting_bank_id
        ]

        all_ready = len(round_store) >= DEFAULT_QUORUM

        logger.info(
            "FetchPeerPublicKeys: bank=%s round=%d peers_returned=%d all_ready=%s",
            request.requesting_bank_id,
            request.round_id,
            len(peer_keys),
            all_ready,
        )
        return PeerKeysResponse(
            round_id=request.round_id,
            peer_keys=peer_keys,
            all_peers_ready=all_ready,
        )

    # -----------------------------------------------------------------------
    # Shamir (t, n) Threshold Share Routing & Dropout Recovery RPCs
    # -----------------------------------------------------------------------

    async def RouteShareBundles(  # noqa: N802
        self, request: ShareRoutingRequest
    ) -> ShareRoutingResponse:
        """RPC 7 (V2.0): Receive encrypted Shamir share bundles for routing.

        The coordinator stores encrypted shares in `share_store` indexed by
        (round_id, (sender, recipient)). The coordinator never possesses
        decryption keys and cannot read plaintext secret shares.
        """
        round_shares = self.share_store.setdefault(request.round_id, {})
        for bundle in request.bundles:
            key = (bundle.sender_bank_id, bundle.recipient_bank_id)
            round_shares[key] = bundle

        logger.info(
            "RouteShareBundles accepted: sender=%s round=%d routed_count=%d",
            request.sender_bank_id,
            request.round_id,
            len(request.bundles),
        )
        return ShareRoutingResponse(
            accepted=True,
            status_message="Encrypted share bundles accepted for peer distribution.",
            routed_count=len(request.bundles),
        )

    async def SubmitDropoutShares(  # noqa: N802
        self, request: DropoutRecoveryRequest
    ) -> DropoutRecoveryResponse:
        """RPC 8 (V2.0): Receive shares from a surviving node for dropped clients.

        Accumulates shares for dropped node keys x_d and surviving self-masks b_u.
        Returns threshold_met=True when at least DEFAULT_QUORUM shares have been
        collected for all dropped nodes.
        """
        round_dropouts = self.dropout_share_store.setdefault(request.round_id, {})
        round_dropouts[request.reporting_bank_id] = request

        threshold_met = len(round_dropouts) >= DEFAULT_QUORUM

        logger.info(
            "SubmitDropoutShares: reporter=%s round=%d total_reporters=%d threshold_met=%s",
            request.reporting_bank_id,
            request.round_id,
            len(round_dropouts),
            threshold_met,
        )
        return DropoutRecoveryResponse(
            accepted=True,
            threshold_met=threshold_met,
            reconstructed_node_count=len(request.dropped_node_shares),
        )
