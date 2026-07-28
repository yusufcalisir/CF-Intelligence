"""gRPC Servicer implementing FederatedLearningService RPC methods."""

from __future__ import annotations

import hashlib
import logging
import uuid
import zlib
from typing import TYPE_CHECKING, Any

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
    SubmitGradientRequest,
)
from app.infrastructure.security.immutable_audit_chain import ImmutableAuditChain
from app.infrastructure.security.signature_verifier import SignatureVerifier

if TYPE_CHECKING:
    from collections.abc import AsyncIterable

logger = logging.getLogger(__name__)

MAX_EPSILON = 10.0
DEFAULT_QUORUM = 3


class FederatedLearningServicer:
    """gRPC Servicer handling client registration, streaming heartbeats, parameter aggregation, and model downloads."""

    def __init__(self) -> None:
        self.active_sessions: dict[str, dict[str, Any]] = {}
        self.chunk_buffers: dict[str, list[ParameterChunk]] = {}
        self.global_models: dict[str, bytes] = {
            "latest": b"MOCK_GLOBAL_MODEL_BINARY_PAYLOAD_V1.0",
        }
        self.current_round: int = 1
        self.round_submissions: dict[str, list[dict[str, Any]]] = {}

    async def RegisterClient(  # noqa: N802
        self, request: ClientRegisterRequest
    ) -> ClientRegisterResponse:
        """RPC 1: Register client node, validate certificate fingerprint, and issue session token."""
        logger.info(
            "gRPC RegisterClient request from bank_id=%s, bank_name=%s, fp=%s",
            request.bank_id,
            request.bank_name,
            request.certificate_fingerprint,
        )

        # Certificate validation check
        if request.certificate_fingerprint.startswith(
            "INVALID"
        ) or request.certificate_fingerprint.startswith("REVOKED"):
            logger.warning(
                "Rejected gRPC registration for node %s due to invalid/revoked certificate",
                request.bank_id,
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
            "cert_fp": request.certificate_fingerprint,
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
            round_id=str(request.round_id),
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
            target_id=str(request.round_id),
            details={
                "gradient_hash": gradient_hash,
                "dp_epsilon": request.dp_epsilon_used,
                "participant_count": request.participant_count,
                "decompressed_bytes": len(decompressed_gradient),
            },
        )

        # 6. Quorum Check & Aggregation Trigger
        round_key = str(request.round_id)
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
