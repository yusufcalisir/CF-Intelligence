"""gRPC Server Interceptor for Protocol Version Handshake & Compatibility Negotiation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import grpc

if TYPE_CHECKING:
    from collections.abc import Callable

from app.domain.protocol_versioning import (
    VersionCompatibilityMatrix,
    VersionNegotiationStatus,
)

logger = logging.getLogger(__name__)

# Protocol header metadata keys (lowercase in gRPC)
PROTOCOL_VERSION_HEADER_KEY = "x-cfi-protocol-version"
SCHEMA_HASH_HEADER_KEY = "x-cfi-schema-hash"


class ProtocolVersionInterceptor(grpc.ServerInterceptor):
    """gRPC Server Interceptor validating client protocol version header prior to method execution."""

    def __init__(self, matrix: VersionCompatibilityMatrix | None = None) -> None:
        self.matrix = matrix or VersionCompatibilityMatrix()

    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Any],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        """Intercepts RPC request call, extracts protocol version metadata, and validates compatibility."""
        metadata = dict(handler_call_details.invocation_metadata or [])

        def _to_str(val: bytes | str | None) -> str | None:
            if isinstance(val, bytes):
                return val.decode("utf-8")
            return val

        raw_ver = metadata.get(PROTOCOL_VERSION_HEADER_KEY)
        raw_hash = metadata.get(SCHEMA_HASH_HEADER_KEY)

        client_version = _to_str(raw_ver) or str(self.matrix.min_supported_version)
        client_schema_hash = _to_str(raw_hash)

        status, reason = self.matrix.negotiate_protocol_version(
            client_version_str=client_version,
            client_schema_hash=client_schema_hash,
        )

        if status == VersionNegotiationStatus.INCOMPATIBLE:
            logger.warning(
                "SIEM | VERSION_MISMATCH | client_version=%s min_supported=%s reason=%s",
                client_version,
                self.matrix.min_supported_version,
                reason,
            )

            def abort_handler(request: Any, context: grpc.ServicerContext) -> Any:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"Protocol version {client_version} unsupported. "
                    f"Minimum: {self.matrix.min_supported_version}. "
                    "Upgrade: https://docs.cf-intelligence.io/upgrade",
                )

            return grpc.unary_unary_rpc_method_handler(abort_handler)

        return continuation(handler_call_details)
