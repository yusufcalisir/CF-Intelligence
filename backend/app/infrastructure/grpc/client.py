"""High-performance gRPC client driver for bank node operations — Phase 37.1.

Connects to the coordinator over a real mTLS gRPC channel.
Implements automatic reconnect (3 retries, 5 s back-off) and cert-rotation
watching (channel is recycled when the cert file on disk changes).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import TYPE_CHECKING

import grpc
import grpc.aio

from app.infrastructure.grpc.types import (
    AggregationAck,
    ClientHeartbeat,
    ClientRegisterRequest,
    ClientRegisterResponse,
    CoordinatorStatus,
    ModelChunk,
    ModelDownloadRequest,
    ParameterChunk,
    SubmitGradientRequest,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterable
    from pathlib import Path

logger = logging.getLogger(__name__)

_RETRY_STATUSES = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.UNAUTHENTICATED}
_MAX_RETRIES = 3
_RETRY_DELAY_S = 5.0


# ── Helper: read PEM bytes ────────────────────────────────────────────────────


def _read_pem(path: str) -> bytes:
    with open(path, "rb") as fh:  # noqa: PTH123
        return fh.read()


def _cert_mtime(path: str) -> float:
    """Return modification time of cert file, or 0.0 if unavailable."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# ── GRPCBankClient ────────────────────────────────────────────────────────────


class GRPCBankClient:
    """mTLS-enabled gRPC client for bank-side federated operations.

    TLS credentials are loaded from:
    - ``cert_path``   / ``GRPC_CLIENT_CERT`` env var
    - ``key_path``    / ``GRPC_CLIENT_KEY`` env var
    - ``ca_cert_path``/ ``GRPC_CA_CERT`` env var

    Channel lifecycle:
    - Call ``await connect()`` before making RPCs.
    - Call ``await disconnect()`` to close the channel gracefully.
    """

    def __init__(
        self,
        target: str = "coordinator.cf-intelligence.io:50051",
        cert_path: str | Path | None = None,
        key_path: str | Path | None = None,
        ca_cert_path: str | Path | None = None,
        # Kept for backward-compatibility with existing tests that pass a servicer directly
        servicer=None,
    ) -> None:
        self.target = target
        self.cert_path = str(cert_path or os.getenv("GRPC_CLIENT_CERT", ""))
        self.key_path = str(key_path or os.getenv("GRPC_CLIENT_KEY", ""))
        self.ca_cert_path = str(ca_cert_path or os.getenv("GRPC_CA_CERT", ""))
        self._channel: grpc.aio.Channel | None = None
        self._cert_mtime_snapshot: float = 0.0
        self.session_token: str | None = None

        # Backward-compat: if a servicer is injected (unit tests), bypass real channel
        self._servicer = servicer

    # ── Channel management ────────────────────────────────────────────────────

    def _build_credentials(self) -> grpc.ChannelCredentials:
        """Build SSL channel credentials from PEM files."""
        ca_bytes = _read_pem(self.ca_cert_path) if self.ca_cert_path else None
        cert_bytes = _read_pem(self.cert_path) if self.cert_path else None
        key_bytes = _read_pem(self.key_path) if self.key_path else None
        return grpc.ssl_channel_credentials(
            root_certificates=ca_bytes,
            private_key=key_bytes,
            certificate_chain=cert_bytes,
        )

    async def connect(self) -> None:
        """Open the mTLS gRPC channel to the coordinator."""
        credentials = self._build_credentials()
        self._channel = grpc.aio.secure_channel(self.target, credentials)
        self._cert_mtime_snapshot = _cert_mtime(self.cert_path)
        logger.info("gRPC mTLS channel opened to %s", self.target)

    async def disconnect(self) -> None:
        """Close the gRPC channel gracefully."""
        if self._channel:
            await self._channel.close(grace=5)
            self._channel = None
        logger.info("gRPC channel closed.")

    async def _ensure_channel(self) -> None:
        """Detect cert rotation and recreate channel if cert file changed."""
        if not self._channel:
            await self.connect()
            return
        current_mtime = _cert_mtime(self.cert_path)
        if current_mtime != self._cert_mtime_snapshot:
            logger.info(
                "Cert file modified (mtime %s → %s) — recycling gRPC channel",
                self._cert_mtime_snapshot,
                current_mtime,
            )
            await self.disconnect()
            await self.connect()

    # ── Retry decorator ───────────────────────────────────────────────────────

    async def _with_retry(self, coro_fn, *args, **kwargs):
        """Execute ``coro_fn(*args, **kwargs)`` with retry on transient gRPC errors."""
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                await self._ensure_channel()
                return await coro_fn(*args, **kwargs)
            except grpc.aio.AioRpcError as exc:
                if exc.code() in _RETRY_STATUSES:
                    last_exc = exc
                    logger.warning(
                        "gRPC transient error (attempt %d/%d): %s %s",
                        attempt,
                        _MAX_RETRIES,
                        exc.code(),
                        exc.details(),
                    )
                    await asyncio.sleep(_RETRY_DELAY_S)
                else:
                    raise
        raise RuntimeError(
            f"gRPC call failed after {_MAX_RETRIES} retries: {last_exc}"
        ) from last_exc

    # ── RPC methods ───────────────────────────────────────────────────────────

    async def register(
        self,
        bank_id: str,
        bank_name: str,
        cert_fingerprint: str = "SHA256:abcd1234efgh5678",
    ) -> ClientRegisterResponse:
        """Register bank node with the coordinator servicer."""
        req = ClientRegisterRequest(
            bank_id=bank_id,
            bank_name=bank_name,
            certificate_fingerprint=cert_fingerprint,
        )
        if self._servicer:
            # Unit-test path: bypass real channel
            res = await self._servicer.RegisterClient(req)
        else:
            res = await self._with_retry(
                lambda: self._channel.unary_unary(  # type: ignore[union-attr]
                    "/federated.FederatedLearning/RegisterClient"
                )(req)
            )
        if res.is_accepted:
            self.session_token = res.session_token
        return res

    async def send_heartbeats(
        self,
        heartbeat_stream: AsyncIterable[ClientHeartbeat],
    ) -> AsyncGenerator[CoordinatorStatus, None]:
        """Stream heartbeats and receive coordinator status stream."""
        if self._servicer:
            async for status in self._servicer.Heartbeat(heartbeat_stream):
                yield status
        else:
            await self._ensure_channel()
            async for status in self._channel.unary_stream(  # type: ignore[union-attr]
                "/federated.FederatedLearning/Heartbeat"
            )(heartbeat_stream):
                yield status

    async def upload_model_parameters(
        self,
        bank_id: str,
        round_id: int,
        encrypted_weights_bytes: bytes,
        chunk_size: int = 1024,
    ) -> AggregationAck:
        """Stream chunked model parameter update to the coordinator."""
        total_chunks = (len(encrypted_weights_bytes) + chunk_size - 1) // chunk_size

        async def chunk_generator() -> AsyncGenerator[ParameterChunk, None]:
            for idx in range(total_chunks):
                start = idx * chunk_size
                end = min(start + chunk_size, len(encrypted_weights_bytes))
                chunk_payload = encrypted_weights_bytes[start:end]
                signature = hashlib.sha256(chunk_payload).digest()
                yield ParameterChunk(
                    bank_id=bank_id,
                    round_id=round_id,
                    chunk_index=idx,
                    total_chunks=total_chunks,
                    encrypted_payload=chunk_payload,
                    digital_signature=signature,
                )

        if self._servicer:
            return await self._servicer.StreamModelParameters(chunk_generator())

        await self._ensure_channel()
        return await self._channel.stream_unary(  # type: ignore[union-attr]
            "/federated.FederatedLearning/StreamModelParameters"
        )(chunk_generator())

    async def download_global_model(
        self,
        bank_id: str,
        version: str = "latest",
    ) -> bytes:
        """Download and verify chunked global model from the coordinator."""
        req = ModelDownloadRequest(bank_id=bank_id, target_version=version)

        if self._servicer:
            source = self._servicer.DownloadGlobalModel(req)
        else:
            await self._ensure_channel()
            source = self._channel.unary_stream(  # type: ignore[union-attr]
                "/federated.FederatedLearning/DownloadGlobalModel"
            )(req)

        downloaded_chunks: list[ModelChunk] = []
        async for chunk in source:
            expected = hashlib.sha256(chunk.chunk_data).hexdigest()
            if chunk.sha256_checksum != expected:
                raise ValueError(f"Checksum mismatch on gRPC model chunk {chunk.chunk_index}")
            downloaded_chunks.append(chunk)

        downloaded_chunks.sort(key=lambda c: c.chunk_index)
        return b"".join(c.chunk_data for c in downloaded_chunks)

    async def submit_gradient(
        self,
        round_id: str,
        bank_id: str,
        masked_gradient_bytes: bytes,
        dp_epsilon_used: float = 1.0,
        participant_count: int = 3,
        private_key_pem: str | None = None,
        protocol_version: str = "1.0.0",
    ) -> AggregationAck:
        """Compress, sign, and submit SecAgg masked gradient update to coordinator."""
        import zlib

        from app.infrastructure.security.signature_verifier import DigitalEnvelopeSigner

        compressed = zlib.compress(masked_gradient_bytes)
        signed_message = f"{round_id}:{bank_id}".encode() + hashlib.sha256(compressed).digest()
        signer = DigitalEnvelopeSigner()
        signature = signer.sign_payload(
            payload_bytes=signed_message,
            bank_id=bank_id,
            private_key_pem=private_key_pem,
        )

        req = SubmitGradientRequest(
            round_id=str(round_id),
            bank_id=bank_id,
            compressed_masked_gradient=compressed,
            dp_epsilon_used=dp_epsilon_used,
            participant_count=participant_count,
            signature=signature,
            protocol_version=protocol_version,
        )

        if self._servicer:
            return await self._servicer.SubmitGradient(req)

        await self._ensure_channel()
        return await self._channel.unary_unary(  # type: ignore[union-attr]
            "/federated.FederatedLearning/SubmitGradient"
        )(req)
