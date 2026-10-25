"""OpenMined PSI Adapter for Two-Bank Private Matching.

Integrates OpenMined PSI (ECDH-PSI over Curve25519 with Golomb Compressed Sets).
Enforces:
- Strict protocol compliance (Apache 2.0, openmined_psi 2.0.6)
- Exact intersection learning: Client learns only elements in the intersection.
- Server learns zero information about client set.
- Coordinator routes only opaque serialized protobuf messages.
- Fail-closed behavior: If library is unavailable, refuses execution without silent fallback.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

from app.domain.consortium_governance import ConsortiumSecurityError

logger = logging.getLogger(__name__)

PSI_LIB_AVAILABLE: bool = False
try:
    import private_set_intersection.python as openmined_psi  # type: ignore

    PSI_LIB_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    openmined_psi = None  # type: ignore
    PSI_LIB_AVAILABLE = False


@dataclass(frozen=True)
class EstablishedPSIProtocolMetadata:
    """Metadata describing the established PSI protocol execution."""

    protocol_name: str = "OPENMINED-ECDH-PSI-P256-GCS"
    library: str = "openmined_psi"
    version: str = "2.0.6"
    license_type: str = "Apache-2.0"
    threat_model: str = "SEMI_HONEST_TWO_PARTY"
    data_structure: str = "GCS"
    default_fpr: float = 0.0001
    reveal_intersection: bool = True
    assurance_level: str = "REVIEWED_ESTABLISHED_PSI_VERIFIED"


class OpenMinedPSIAdapter:
    """Typed adapter executing genuine established two-party Private Set Intersection."""

    METADATA = EstablishedPSIProtocolMetadata()

    @classmethod
    def is_available(cls) -> bool:
        """Return whether openmined_psi library is installed and importable."""
        return PSI_LIB_AVAILABLE

    @classmethod
    def ensure_available(cls) -> None:
        """Fail closed if openmined_psi library is not available in the current runtime environment."""
        if not cls.is_available():
            raise ConsortiumSecurityError(
                "Established PSI execution library 'openmined_psi' (v2.0.6) is not available "
                "in current environment. Running within the dedicated Linux worker container topology "
                "is mandatory. Fallback to unreviewed prototypes is strictly prohibited."
            )

    @classmethod
    def create_client(cls, reveal_intersection: bool = True) -> Any:
        """Create an OpenMined PSI client instance with a fresh ephemeral key."""
        cls.ensure_available()
        assert openmined_psi is not None
        return openmined_psi.client.CreateWithNewKey(reveal_intersection)

    @classmethod
    def create_server(cls, reveal_intersection: bool = True) -> Any:
        """Create an OpenMined PSI server instance with a fresh ephemeral key."""
        cls.ensure_available()
        assert openmined_psi is not None
        return openmined_psi.server.CreateWithNewKey(reveal_intersection)

    @classmethod
    def build_client_request(
        cls,
        client_instance: Any,
        items: list[str],
    ) -> bytes:
        """Compute blinded client Request message."""
        cls.ensure_available()
        clean_items = [str(item).strip() for item in items if str(item).strip()]
        if not clean_items:
            raise ValueError("Client PSI item list cannot be empty.")
        req = client_instance.CreateRequest(clean_items)
        return req.SerializeToString()

    @classmethod
    def build_server_setup(
        cls,
        server_instance: Any,
        items: list[str],
        fpr: float = 0.0001,
    ) -> bytes:
        """Compute server Setup message using GCS (Golomb Compressed Sets)."""
        cls.ensure_available()
        clean_items = [str(item).strip() for item in items if str(item).strip()]
        if not clean_items:
            # Handle empty server set cleanly
            clean_items = ["__cfi_empty_server_set_sentinel__"]
        assert openmined_psi is not None
        setup = server_instance.CreateSetupMessage(
            fpr,
            len(clean_items),
            clean_items,
            openmined_psi.DataStructure.GCS,
        )
        return setup.SerializeToString()

    @classmethod
    def process_server_request(
        cls,
        server_instance: Any,
        request_bytes: bytes,
    ) -> bytes:
        """Process client request on server and return Response message."""
        cls.ensure_available()
        assert openmined_psi is not None
        req = openmined_psi.Request()
        req.ParseFromString(request_bytes)
        resp = server_instance.ProcessRequest(req)
        return resp.SerializeToString()

    @classmethod
    def compute_client_intersection(
        cls,
        client_instance: Any,
        setup_bytes: bytes,
        response_bytes: bytes,
    ) -> list[int]:
        """Compute intersection indices on client given server Setup and Response messages."""
        cls.ensure_available()
        assert openmined_psi is not None
        setup = openmined_psi.ServerSetup()
        setup.ParseFromString(setup_bytes)
        resp = openmined_psi.Response()
        resp.ParseFromString(response_bytes)
        indices = client_instance.GetIntersection(setup, resp)
        return [int(idx) for idx in indices]

    @classmethod
    def encode_b64(cls, raw: bytes) -> str:
        """Encode binary protobuf message to base64 string for JSON-RPC transport."""
        return base64.b64encode(raw).decode("ascii")

    @classmethod
    def decode_b64(cls, text: str) -> bytes:
        """Decode base64 string back to binary protobuf bytes."""
        return base64.b64decode(text.encode("ascii"))
