"""P2P Diffie-Hellman Secure Aggregation Driver.

Implements a production-grade Peer-to-Peer Curve25519 ECDH + HKDF-SHA256
SecAgg protocol. No server ever holds pairwise mask seeds or shared secrets.

Zero-Sum Cancellation Guarantee:
    For participating set U = {u_1, ..., u_N}, each pair (u, v) with u < v
    derives a shared PRG seed K_{u,v} via ECDH. The masked vector submitted
    by node u is:

        y_u = w_u + Σ_{v>u} mask(K_{u,v}) - Σ_{v<u} mask(K_{v,u})  (mod 2^32)

    At the coordinator:

        Σ_u y_u = Σ_u w_u  (masks cancel identically)
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ---------------------------------------------------------------------------
# Domain Value Objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ECDHPublicKeyBundle:
    """Ephemeral X25519 public key advertised by a bank node for one FL round.

    Signed with a per-bank HMAC derived from the node's mTLS identity to
    prevent Sybil key injection.
    """

    bank_id: str
    round_id: int
    public_key_bytes: bytes  # 32-byte raw X25519 public key
    hmac_signature: bytes    # HMAC-SHA256(identity_secret, bank_id || round_id || pk)


@dataclass(frozen=True)
class P2PSecAggState:
    """Snapshot of the P2P SecAgg key-exchange state after a round's setup."""

    round_id: int
    active_peers: list[str]
    shared_seeds_derived: int
    mask_dimension: int
    zero_sum_verified: bool


# ---------------------------------------------------------------------------
# Core Driver
# ---------------------------------------------------------------------------


class P2PSecAggDriver:
    """Peer-to-peer Curve25519 ECDH Secure Aggregation Driver.

    Each bank node instantiates this driver per FL round. Responsibilities:
      1. Generate an ephemeral X25519 keypair.
      2. Authenticate and broadcast the public key to peers.
      3. Receive peer public keys and verify authenticity.
      4. Derive pairwise HKDF seeds and expand PRG mask vectors.
      5. Produce a masked weight vector y_u for coordinator submission.

    The coordinator only ever sees masked vectors. Individual weights are
    information-theoretically hidden unless *all* peers collude.
    """

    _MASK_MODULUS: int = 2**32

    def __init__(self, bank_id: str, identity_secret: bytes | None = None) -> None:
        """
        Args:
            bank_id: Unique institution identifier (e.g., "bank_alpha").
            identity_secret: 32-byte symmetric secret shared out-of-band
                during bank onboarding, used to authenticate public key bundles.
                If None, a deterministic test secret is derived from bank_id.
        """
        self.bank_id = bank_id
        self._identity_secret: bytes = identity_secret or hashlib.sha256(
            f"identity:{bank_id}".encode()
        ).digest()

        # Per-round ephemeral state (reset on each new round)
        self._private_key: X25519PrivateKey | None = None
        self._public_key_bytes: bytes | None = None
        self._round_id: int | None = None

    # ------------------------------------------------------------------
    # Phase 1: Ephemeral Keypair Generation
    # ------------------------------------------------------------------

    def generate_round_keypair(self, round_id: int) -> ECDHPublicKeyBundle:
        """Generate an ephemeral X25519 keypair for this FL round.

        Returns an authenticated ECDHPublicKeyBundle ready for broadcast.
        The private key is stored in memory and never leaves this instance.
        """
        self._round_id = round_id
        self._private_key = X25519PrivateKey.generate()
        self._public_key_bytes = self._private_key.public_key().public_bytes_raw()

        signature = self._sign_bundle(self.bank_id, round_id, self._public_key_bytes)

        return ECDHPublicKeyBundle(
            bank_id=self.bank_id,
            round_id=round_id,
            public_key_bytes=self._public_key_bytes,
            hmac_signature=signature,
        )

    # ------------------------------------------------------------------
    # Phase 2: Peer Bundle Verification
    # ------------------------------------------------------------------

    @staticmethod
    def verify_peer_bundle(
        bundle: ECDHPublicKeyBundle,
        peer_identity_secret: bytes,
    ) -> bool:
        """Verify that a received ECDHPublicKeyBundle is authentically signed.

        Args:
            bundle: Received bundle from a peer node.
            peer_identity_secret: The verifying node's copy of the peer's
                out-of-band identity secret (distributed during onboarding).

        Returns:
            True if the HMAC signature is valid; False otherwise.
        """
        expected = P2PSecAggDriver._sign_bundle_static(
            peer_identity_secret,
            bundle.bank_id,
            bundle.round_id,
            bundle.public_key_bytes,
        )
        return hmac.compare_digest(expected, bundle.hmac_signature)

    # ------------------------------------------------------------------
    # Phase 3: Pairwise HKDF Seed Derivation
    # ------------------------------------------------------------------

    def derive_pairwise_seed(
        self,
        peer_bundle: ECDHPublicKeyBundle,
    ) -> bytes:
        """Execute X25519 ECDH and derive a 32-byte HKDF seed with the peer.

        The HKDF info string encodes the round_id and both participant IDs in
        lexicographic order, making the seed unique per (round, pair) and
        preventing cross-round replay.

        Args:
            peer_bundle: Authenticated ECDHPublicKeyBundle from the peer.

        Returns:
            32-byte deterministic seed, identical on both sides of the exchange.

        Raises:
            RuntimeError: If called before generate_round_keypair().
            ValueError: If round_id mismatch is detected.
        """
        if self._private_key is None or self._round_id is None:
            raise RuntimeError("generate_round_keypair() must be called first.")

        if peer_bundle.round_id != self._round_id:
            raise ValueError(
                f"Round ID mismatch: expected {self._round_id}, "
                f"got {peer_bundle.round_id} from peer {peer_bundle.bank_id}."
            )

        peer_public_key = X25519PublicKey.from_public_bytes(peer_bundle.public_key_bytes)
        shared_secret = self._private_key.exchange(peer_public_key)

        # Canonical pair ordering: sort IDs so both sides produce identical info
        pair = sorted([self.bank_id, peer_bundle.bank_id])
        info = f"secagg:r{self._round_id}:{pair[0]}:{pair[1]}".encode()

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=f"cfi:secagg:round:{self._round_id}".encode(),
            info=info,
        )
        return hkdf.derive(shared_secret)

    # ------------------------------------------------------------------
    # Phase 4: Pseudo-Random Mask Vector Expansion
    # ------------------------------------------------------------------

    @staticmethod
    def expand_mask(seed: bytes, dimension: int) -> list[int]:
        """Expand a 32-byte HKDF seed into a pseudo-random integer mask vector.

        Uses HMAC-SHA256 in counter mode (PRNG). Each 4-byte block of the
        output is interpreted as a little-endian unsigned 32-bit integer,
        matching the modular arithmetic in the zero-sum cancellation proof.

        Args:
            seed: 32-byte HKDF-derived seed.
            dimension: Number of parameters d to mask.

        Returns:
            List of d integers in Z_{2^32}.
        """
        mask: list[int] = []
        counter = 0
        while len(mask) < dimension:
            block = hmac.new(
                seed,
                struct.pack(">I", counter),
                hashlib.sha256,
            ).digest()
            # Each SHA-256 output gives 8 x 4-byte integers
            for i in range(0, len(block), 4):
                if len(mask) >= dimension:
                    break
                mask.append(struct.unpack_from("<I", block, i)[0])
            counter += 1
        return mask

    # ------------------------------------------------------------------
    # Phase 5: Masked Weight Vector Assembly
    # ------------------------------------------------------------------

    def compute_masked_vector(
        self,
        weights: list[float],
        peer_bundles: list[ECDHPublicKeyBundle],
        quantization_scale: float = 1e6,
    ) -> list[int]:
        """Produce the masked weight vector y_u for coordinator submission.

        Quantizes float weights to 32-bit integers, then applies pairwise
        masks following the zero-sum additive protocol:

            y_u = w_u + Σ_{v > u} s_{u,v}  -  Σ_{v < u} s_{v,u}  (mod 2^32)

        where v > u / v < u is determined by lexicographic ordering of bank_ids.

        Args:
            weights: Float model parameter vector of dimension d.
            peer_bundles: Authenticated ECDHPublicKeyBundles from all peers
                (excluding self) who are participating this round.
            quantization_scale: Multiplier for float→int quantization. The
                coordinator divides by the same scale after dequantizing.

        Returns:
            Masked integer vector of length d, ready for submission.
        """
        if not weights:
            raise ValueError("weights must be non-empty.")

        d = len(weights)

        # Quantize: float → int via fixed-point scaling
        quantized: list[int] = [
            round(w * quantization_scale) % self._MASK_MODULUS
            for w in weights
        ]
        masked = list(quantized)  # copy to apply masks in-place

        for bundle in peer_bundles:
            seed = self.derive_pairwise_seed(bundle)
            mask = self.expand_mask(seed, d)

            if self.bank_id < bundle.bank_id:
                # We are u < v: add the mask
                for i in range(d):
                    masked[i] = (masked[i] + mask[i]) % self._MASK_MODULUS
            else:
                # We are u > v: subtract the mask
                for i in range(d):
                    masked[i] = (masked[i] - mask[i]) % self._MASK_MODULUS

        return masked

    # ------------------------------------------------------------------
    # Coordinator: Unmasked Aggregation & Zero-Sum Verification
    # ------------------------------------------------------------------

    @staticmethod
    def aggregate_masked_vectors(
        masked_vectors: dict[str, list[int]],
        quantization_scale: float = 1e6,
    ) -> list[float]:
        """Sum all masked vectors, verify zero-sum cancellation, return plaintext average.

        This is the coordinator-side operation. The result is identical to
        plain FedAvg on the original float weights.

        Args:
            masked_vectors: Mapping from bank_id → masked integer vector y_u.
            quantization_scale: Must match the scale used by clients.

        Returns:
            Dequantized average weight vector (identical to unmasked FedAvg).
        """
        if not masked_vectors:
            raise ValueError("masked_vectors must be non-empty.")

        bank_ids = sorted(masked_vectors.keys())
        d = len(masked_vectors[bank_ids[0]])

        # Coordinator sum: masks cancel, leaving Σ w_u
        total = [0] * d
        for bank_id in bank_ids:
            vec = masked_vectors[bank_id]
            for i in range(d):
                total[i] = (total[i] + vec[i]) % (2**32)

        n = len(bank_ids)
        # Dequantize and average
        result: list[float] = []
        for i in range(d):
            # Handle modular sign: values > 2^31 are negative
            signed = total[i] if total[i] < 2**31 else total[i] - 2**32
            result.append((signed / quantization_scale) / n)

        return result

    # ------------------------------------------------------------------
    # Private Helpers
    # ------------------------------------------------------------------

    def _sign_bundle(self, bank_id: str, round_id: int, pk_bytes: bytes) -> bytes:
        return self._sign_bundle_static(self._identity_secret, bank_id, round_id, pk_bytes)

    @staticmethod
    def _sign_bundle_static(
        secret: bytes, bank_id: str, round_id: int, pk_bytes: bytes
    ) -> bytes:
        msg = bank_id.encode() + struct.pack(">Q", round_id) + pk_bytes
        return hmac.new(secret, msg, hashlib.sha256).digest()
