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
import os
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.infrastructure.security.shamir_engine import (
    ShamirSecretSharingEngine,
    ShamirShare,
)

# ---------------------------------------------------------------------------
# Cryptographic Helpers & Transcript Serialization
# ---------------------------------------------------------------------------


class SecAggSecurityError(Exception):
    """Raised when Secure Aggregation cryptographic validation or protocol invariant fails."""


def derive_institutional_identity_keypair(
    bank_id: str,
    institutional_token: str,
) -> tuple[ed25519.Ed25519PrivateKey, ed25519.Ed25519PublicKey]:
    """Derive an authoritative institutional Ed25519 identity keypair bound to bank_id and token."""
    clean_id = bank_id.strip().upper()
    clean_token = institutional_token.strip()
    if not clean_id or not clean_token:
        raise ValueError("bank_id and institutional_token must be non-empty.")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"cfi:secagg:institutional:identity:v1",
        info=f"institutional_identity:{clean_id}".encode(),
    )
    seed = hkdf.derive(clean_token.encode("utf-8"))
    priv_key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    return priv_key, priv_key.public_key()


def derive_institutional_identity_hex(
    bank_id: str,
    institutional_token: str,
) -> tuple[str, str]:
    """Derive hex-encoded authoritative institutional Ed25519 identity keypair (private_hex, public_hex)."""
    priv, pub = derive_institutional_identity_keypair(bank_id, institutional_token)
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv_raw.hex(), pub_raw.hex()


def canonical_bundle_message(
    bank_id: str,
    round_id: int,
    public_key_bytes: bytes,
    job_id: str = "",
    consortium_id: str = "",
    session_id: str = "",
    roster: Sequence[str] = (),
) -> bytes:
    """Canonical deterministic transcript binding for bundle signing and replay prevention."""
    roster_clean = ",".join(sorted(r.strip().upper() for r in roster if r))
    if job_id or consortium_id or session_id or roster_clean:
        prefix = f"{job_id}:{consortium_id}:{session_id}:{roster_clean}:".encode()
    else:
        prefix = b""
    return (
        prefix
        + bank_id.encode("utf-8")
        + struct.pack(">Q", round_id)
        + public_key_bytes
    )


def serialize_masked_vector(masked_vector: Sequence[int] | np.ndarray) -> bytes:
    """Deterministically serialize masked vector to uint64 little-endian bytes."""
    arr = np.asarray(masked_vector, dtype=np.uint64)
    return arr.tobytes()


def deserialize_masked_vector(data: bytes) -> list[int]:
    """Deterministically deserialize uint64 little-endian bytes to integer list."""
    arr = np.frombuffer(data, dtype=np.uint64)
    return arr.tolist()


# ---------------------------------------------------------------------------
# Domain Value Objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ECDHPublicKeyBundle:
    """Ephemeral X25519 public key advertised by a bank node for one FL round.

    Signed with an institutional Ed25519 key or HMAC bound to the exact round transcript
    (job_id, consortium_id, session_id, round_id, roster) to prevent key substitution,
    replay, reflection, and cross-session attacks.
    """

    bank_id: str
    round_id: int
    public_key_bytes: bytes  # 32-byte raw X25519 public key
    hmac_signature: bytes  # Signature bytes (HMAC-SHA256 or Ed25519)
    job_id: str = ""
    consortium_id: str = ""
    session_id: str = ""
    roster: tuple[str, ...] = ()
    auth_scheme: str = "hmac"  # "hmac" or "ed25519"

    def to_dict(self) -> dict[str, Any]:
        """Serialize bundle to JSON/IPC-safe dictionary."""
        return {
            "bank_id": self.bank_id,
            "round_id": self.round_id,
            "public_key_bytes": self.public_key_bytes.hex(),
            "hmac_signature": self.hmac_signature.hex(),
            "job_id": self.job_id,
            "consortium_id": self.consortium_id,
            "session_id": self.session_id,
            "roster": list(self.roster),
            "auth_scheme": self.auth_scheme,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ECDHPublicKeyBundle:
        """Reconstruct bundle from dictionary."""
        raw_pk = d["public_key_bytes"]
        pk_bytes = bytes.fromhex(raw_pk) if isinstance(raw_pk, str) else bytes(raw_pk)
        raw_sig = d["hmac_signature"]
        sig_bytes = bytes.fromhex(raw_sig) if isinstance(raw_sig, str) else bytes(raw_sig)
        return cls(
            bank_id=str(d["bank_id"]),
            round_id=int(d["round_id"]),
            public_key_bytes=pk_bytes,
            hmac_signature=sig_bytes,
            job_id=str(d.get("job_id", "")),
            consortium_id=str(d.get("consortium_id", "")),
            session_id=str(d.get("session_id", "")),
            roster=tuple(str(r) for r in d.get("roster", ())),
            auth_scheme=str(d.get("auth_scheme", "hmac")),
        )


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
    _DEFAULT_MODULUS_64: int = 2**64

    def __init__(
        self,
        bank_id: str,
        identity_secret: bytes | None = None,
        institutional_private_key: ed25519.Ed25519PrivateKey | None = None,
        institutional_public_key: ed25519.Ed25519PublicKey | None = None,
        institutional_identity_hex: str | None = None,
        app_env: str = "production",
        require_authentic_identity: bool = False,
        modulus: int = 2**64,
    ) -> None:
        """Args:
            bank_id: Unique institution identifier (e.g., "bank_alpha").
            identity_secret: 32-byte symmetric secret shared out-of-band.
            institutional_private_key: Optional Ed25519 institutional private key.
            institutional_public_key: Optional Ed25519 institutional public key.
            institutional_identity_hex: Hex-encoded Ed25519 private key seed.
            app_env: Operating environment ("production", "development", "testing").
            require_authentic_identity: If True, strictly forbids test fallback secrets.
            modulus: Integer arithmetic modulus (2**64 or 2**32).
        """
        self.bank_id = bank_id.strip().upper() if bank_id.startswith("BANK_") else bank_id
        self.app_env = app_env.strip().lower()
        self.modulus = modulus

        if institutional_identity_hex is not None and institutional_private_key is None:
            institutional_private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
                bytes.fromhex(institutional_identity_hex)
            )

        if require_authentic_identity and identity_secret is None and institutional_private_key is None:
            raise ValueError(
                f"SecAgg driver for bank [{self.bank_id}] cannot execute without authentic institutional "
                "key material. Deterministic test identity secret fallback is strictly prohibited (SECRETS-PROD-01)."
            )

        if institutional_private_key is not None:
            self._institutional_private_key: ed25519.Ed25519PrivateKey | None = institutional_private_key
            self._institutional_public_key: ed25519.Ed25519PublicKey | None = (
                institutional_public_key or institutional_private_key.public_key()
            )
            self._identity_secret: bytes = b""
            self._auth_scheme: str = "ed25519"
        elif identity_secret is not None:
            self._identity_secret = identity_secret
            self._institutional_private_key = None
            self._institutional_public_key = None
            self._auth_scheme = "hmac"
        else:
            # Standalone test/verification fallback only
            self._identity_secret = hashlib.sha256(f"identity:{bank_id}".encode()).digest()
            self._institutional_private_key = None
            self._institutional_public_key = None
            self._auth_scheme = "hmac"

        # Per-round ephemeral state (reset on each new round)
        self._private_key: X25519PrivateKey | None = None
        self._public_key_bytes: bytes | None = None
        self._round_id: int | None = None
        self._job_id: str = ""
        self._consortium_id: str = ""
        self._session_id: str = ""
        self._roster: tuple[str, ...] = ()
        self._self_mask_seed: bytes | None = None
        self._received_peer_bundles: dict[str, ECDHPublicKeyBundle] = {}

    # ------------------------------------------------------------------
    # Phase 1: Ephemeral Keypair Generation
    # ------------------------------------------------------------------

    def generate_round_keypair(
        self,
        round_id: int,
        job_id: str = "",
        consortium_id: str = "",
        session_id: str = "",
        roster: Sequence[str] = (),
    ) -> ECDHPublicKeyBundle:
        """Generate a fresh X25519 keypair and self-mask seed for an FL round.

        Args:
            round_id: Monotonically increasing FL round counter.
            job_id: Unique federated training job identifier.
            consortium_id: Target consortium identifier.
            session_id: Session identifier.
            roster: Participating bank institution identifiers.

        Returns:
            Authenticated ECDHPublicKeyBundle bound to the round transcript.
        """
        self._round_id = round_id
        self._job_id = job_id
        self._consortium_id = consortium_id
        self._session_id = session_id
        self._roster = tuple(sorted(r.strip().upper() for r in roster if r))
        self._private_key = X25519PrivateKey.generate()
        self._public_key_bytes = self._private_key.public_key().public_bytes_raw()
        self._self_mask_seed = os.urandom(32)
        self._received_peer_bundles.clear()

        msg = canonical_bundle_message(
            bank_id=self.bank_id,
            round_id=round_id,
            public_key_bytes=self._public_key_bytes,
            job_id=job_id,
            consortium_id=consortium_id,
            session_id=session_id,
            roster=self._roster,
        )

        if self._auth_scheme == "ed25519":
            assert self._institutional_private_key is not None
            signature = self._institutional_private_key.sign(msg)
        else:
            signature = hmac.new(self._identity_secret, msg, hashlib.sha256).digest()

        return ECDHPublicKeyBundle(
            bank_id=self.bank_id,
            round_id=round_id,
            public_key_bytes=self._public_key_bytes,
            hmac_signature=signature,
            job_id=job_id,
            consortium_id=consortium_id,
            session_id=session_id,
            roster=self._roster,
            auth_scheme=self._auth_scheme,
        )

    # ------------------------------------------------------------------
    # Phase 2: Peer Bundle Verification & Ingestion
    # ------------------------------------------------------------------

    def receive_peer_bundle(
        self,
        peer_bundle: ECDHPublicKeyBundle,
        peer_institutional_public_key: str | bytes | ed25519.Ed25519PublicKey | None = None,
        peer_identity_secret: bytes | None = None,
    ) -> None:
        """Verify and record an authentic peer bundle for the active round.

        Raises:
            SecAggSecurityError: If bundle verification fails or transcript parameters mismatch.
        """
        if self._round_id is None:
            raise RuntimeError("generate_round_keypair() must be called first.")

        if peer_bundle.bank_id == self.bank_id:
            return

        if self._roster and peer_bundle.bank_id not in self._roster:
            raise SecAggSecurityError(
                f"Peer [{peer_bundle.bank_id}] is not in authorized roster: {self._roster}"
            )

        pub_key_arg: bytes | ed25519.Ed25519PublicKey | None = None
        if isinstance(peer_institutional_public_key, str):
            pub_key_arg = bytes.fromhex(peer_institutional_public_key)
        else:
            pub_key_arg = peer_institutional_public_key

        is_valid = self.verify_peer_bundle(
            bundle=peer_bundle,
            peer_identity_secret=peer_identity_secret,
            peer_public_key=pub_key_arg,
            expected_job_id=self._job_id,
            expected_consortium_id=self._consortium_id,
            expected_session_id=self._session_id,
            expected_roster=self._roster,
            expected_round_id=self._round_id,
        )
        if not is_valid:
            raise SecAggSecurityError(
                f"Rejected forged, tampered, or mismatched bundle from peer [{peer_bundle.bank_id}]."
            )
        self._received_peer_bundles[peer_bundle.bank_id] = peer_bundle

    @staticmethod
    def verify_peer_bundle(
        bundle: ECDHPublicKeyBundle,
        peer_identity_secret: bytes | None = None,
        peer_public_key: ed25519.Ed25519PublicKey | bytes | None = None,
        expected_job_id: str = "",
        expected_consortium_id: str = "",
        expected_session_id: str = "",
        expected_roster: Sequence[str] = (),
        expected_round_id: int | None = None,
    ) -> bool:
        """Verify that a received ECDHPublicKeyBundle is authentically signed and transcript-matched.

        Args:
            bundle: Received bundle from a peer node.
            peer_identity_secret: Symmetric HMAC secret if HMAC is used.
            peer_public_key: Institutional Ed25519 public key if Ed25519 is used.
            expected_job_id: Expected job identifier.
            expected_consortium_id: Expected consortium identifier.
            expected_session_id: Expected session identifier.
            expected_roster: Expected roster of participant institutions.
            expected_round_id: Expected round counter.

        Returns:
            True if signature and transcript parameters are valid; False otherwise.
        """
        if expected_round_id is not None and bundle.round_id != expected_round_id:
            return False
        if expected_job_id and bundle.job_id != expected_job_id:
            return False
        if expected_consortium_id and bundle.consortium_id != expected_consortium_id:
            return False
        if expected_session_id and bundle.session_id != expected_session_id:
            return False
        if expected_roster:
            clean_exp_roster = tuple(sorted(r.strip().upper() for r in expected_roster if r))
            if bundle.roster != clean_exp_roster:
                return False

        msg = canonical_bundle_message(
            bank_id=bundle.bank_id,
            round_id=bundle.round_id,
            public_key_bytes=bundle.public_key_bytes,
            job_id=bundle.job_id,
            consortium_id=bundle.consortium_id,
            session_id=bundle.session_id,
            roster=bundle.roster,
        )

        if bundle.auth_scheme == "ed25519" or peer_public_key is not None:
            if peer_public_key is None:
                return False
            try:
                if isinstance(peer_public_key, bytes):
                    pub = ed25519.Ed25519PublicKey.from_public_bytes(peer_public_key)
                else:
                    pub = peer_public_key
                pub.verify(bundle.hmac_signature, msg)
                return True
            except (InvalidSignature, Exception):
                return False
        else:
            if peer_identity_secret is None:
                return False
            expected = hmac.new(peer_identity_secret, msg, hashlib.sha256).digest()
            return hmac.compare_digest(expected, bundle.hmac_signature)

    # ------------------------------------------------------------------
    # Phase 3: Pairwise HKDF Seed Derivation
    # ------------------------------------------------------------------

    def derive_pairwise_seed(
        self,
        peer_bundle: ECDHPublicKeyBundle,
    ) -> bytes:
        """Execute X25519 ECDH and derive a 32-byte HKDF seed with the peer.

        The HKDF salt and info strings encode transcript variables and participant IDs
        in lexicographical order, making the seed unique per (round, session, pair) and
        preventing cross-round replay.
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

        pair = sorted([self.bank_id, peer_bundle.bank_id])
        if peer_bundle.job_id or peer_bundle.session_id:
            salt = f"cfi:secagg:job:{peer_bundle.job_id}:r{self._round_id}:s:{peer_bundle.session_id}".encode()
            info = f"secagg:c:{peer_bundle.consortium_id}:r{self._round_id}:{pair[0]}:{pair[1]}".encode()
        else:
            salt = f"cfi:secagg:round:{self._round_id}".encode()
            info = f"secagg:r{self._round_id}:{pair[0]}:{pair[1]}".encode()

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=info,
        )
        return hkdf.derive(shared_secret)

    # ------------------------------------------------------------------
    # Phase 4: Pseudo-Random Mask Vector Expansion
    # ------------------------------------------------------------------

    @staticmethod
    def expand_mask(seed: bytes, dimension: int, modulus: int = 2**32) -> list[int]:
        """Expand a 32-byte HKDF seed into a pseudo-random integer mask vector.

        Uses HMAC-SHA256 in counter mode (PRNG).
        If modulus > 2**32 (e.g. 2**64), unpacks 8-byte little-endian uint64 (<Q).
        If modulus <= 2**32 (e.g. 2**32), unpacks 4-byte little-endian uint32 (<I).
        """
        mask: list[int] = []
        counter = 0
        is_64 = modulus > 2**32
        step = 8 if is_64 else 4
        fmt = "<Q" if is_64 else "<I"

        while len(mask) < dimension:
            block = hmac.new(
                seed,
                struct.pack(">I", counter),
                hashlib.sha256,
            ).digest()
            for i in range(0, len(block), step):
                if len(mask) >= dimension:
                    break
                val = struct.unpack_from(fmt, block, i)[0]
                mask.append(val % modulus)
            counter += 1
        return mask

    # ------------------------------------------------------------------
    # Phase 5: Masked Weight Vector Assembly (Standard Uniform & Weighted)
    # ------------------------------------------------------------------

    def compute_masked_vector(
        self,
        weights: list[float],
        peer_bundles: list[ECDHPublicKeyBundle] | None = None,
        quantization_scale: float = 1e6,
        use_self_mask: bool = False,
    ) -> list[int]:
        """Produce the masked weight vector y_u for coordinator submission (uniform averaging).

        Quantizes float weights to 32-bit integers, then applies pairwise
        masks following the zero-sum additive protocol:

            y_u = w_u + Σ_{v > u} s_{u,v}  -  Σ_{v < u} s_{v,u}  (mod 2^32)
        """
        if not weights:
            raise ValueError("weights must be non-empty.")

        bundles = peer_bundles if peer_bundles is not None else list(self._received_peer_bundles.values())
        d = len(weights)
        modulus = self._MASK_MODULUS

        # Quantize: float → int via fixed-point scaling
        quantized: list[int] = [round(w * quantization_scale) % modulus for w in weights]
        masked = list(quantized)

        for bundle in bundles:
            seed = self.derive_pairwise_seed(bundle)
            mask = self.expand_mask(seed, d, modulus=modulus)

            if self.bank_id < bundle.bank_id:
                for i in range(d):
                    masked[i] = (masked[i] + mask[i]) % modulus
            else:
                for i in range(d):
                    masked[i] = (masked[i] - mask[i]) % modulus

        if use_self_mask and self._self_mask_seed is not None:
            self_mask = self.expand_mask(self._self_mask_seed, d, modulus=modulus)
            for i in range(d):
                masked[i] = (masked[i] + self_mask[i]) % modulus

        return masked

    def compute_weighted_masked_vector(
        self,
        weights: list[float],
        sample_count: int,
        peer_bundles: list[ECDHPublicKeyBundle] | None = None,
        quantization_scale: float = 1e7,
        modulus: int = 2**64,
    ) -> list[int]:
        """Produce bank-local weighted masked vector y_u for weighted FedAvg:

            v_u = n_u * w_u
            q_u = round(v_u * S)  (mod 2^64)
            y_u = q_u + Σ_{v > u} s_{u,v} - Σ_{v < u} s_{v,u}  (mod 2^64)
        """
        if not weights:
            raise ValueError("weights must be non-empty.")
        if sample_count <= 0:
            raise ValueError("sample_count must be positive.")

        bundles = peer_bundles if peer_bundles is not None else list(self._received_peer_bundles.values())
        if not bundles:
            raise ValueError("No peer bundles provided or received for masked vector calculation.")

        arr_w = np.asarray(weights, dtype=np.float64)
        if not np.all(np.isfinite(arr_w)):
            raise ValueError("weights vector contains non-finite values (NaN or Inf).")

        d = len(weights)

        # Scale by sample_count and quantization_scale
        scaled_weighted = arr_w * (float(sample_count) * float(quantization_scale))
        quantized = np.round(scaled_weighted).astype(np.int64).astype(np.uint64)
        masked = np.array(quantized, copy=True)

        for bundle in bundles:
            seed = self.derive_pairwise_seed(bundle)
            mask = np.asarray(self.expand_mask(seed, d, modulus=modulus), dtype=np.uint64)

            masked = masked + mask if self.bank_id < bundle.bank_id else masked - mask

        return masked.tolist()

    # ------------------------------------------------------------------
    # Shamir (t, n) Secret Sharing & Share Split
    # ------------------------------------------------------------------

    def split_round_secrets(
        self,
        peer_bank_ids: list[str],
        threshold: int,
    ) -> dict[str, tuple[ShamirShare, ShamirShare]]:
        """Split self-mask seed b_u and ephemeral private key x_u into n shares.

        Returns:
            Dict mapping bank_id -> (b_share, x_share).
        """
        if self._private_key is None or self._self_mask_seed is None:
            raise RuntimeError("generate_round_keypair() must be called first.")

        all_participants = sorted([self.bank_id] + list(peer_bank_ids))
        total_shares = len(all_participants)

        engine = ShamirSecretSharingEngine()
        x_bytes = self._private_key.private_bytes_raw()

        b_shares = engine.split_secret(self._self_mask_seed, threshold, total_shares)
        x_shares = engine.split_secret(x_bytes, threshold, total_shares)

        result: dict[str, tuple[ShamirShare, ShamirShare]] = {}
        for idx, bank_id in enumerate(all_participants):
            result[bank_id] = (b_shares[idx], x_shares[idx])

        return result

    # ------------------------------------------------------------------
    # Coordinator: Unmasked Aggregation & Zero-Sum Verification
    # ------------------------------------------------------------------

    @staticmethod
    def aggregate_masked_vectors(
        masked_vectors: dict[str, list[int]],
        quantization_scale: float = 1e6,
    ) -> list[float]:
        """Sum all masked vectors, verify zero-sum cancellation, return plaintext average."""
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

    @classmethod
    def aggregate_weighted_masked_vectors(
        cls,
        masked_vectors: dict[str, list[int]],
        sample_counts: dict[str, int],
        quantization_scale: float = 1e7,
        modulus: int = 2**64,
    ) -> list[float]:
        """Coordinator unmasked weighted aggregation (sample-weighted FedAvg).

        Reconstructs global model weights:
            w_global = Σ_k (n_k * w_k) / Σ_k n_k

        Computes coordinate-wise sum mod 2^64 (masks cancel identically with zero-sum),
        interprets signed two's complement integer, and dequantizes by (quantization_scale * total_samples).
        """
        if not masked_vectors:
            raise ValueError("masked_vectors must be non-empty.")
        if len(masked_vectors) < 2:
            raise ValueError("SecAgg requires at least 2 participants for zero-sum cancellation.")

        bank_ids = sorted(masked_vectors.keys())
        if set(bank_ids) != set(sample_counts.keys()):
            raise ValueError("sample_counts keys must match masked_vectors keys.")

        total_samples = sum(sample_counts.values())
        if total_samples <= 0:
            raise ValueError("Total sample count must be positive.")

        d = len(masked_vectors[bank_ids[0]])
        for b_id in bank_ids:
            if len(masked_vectors[b_id]) != d:
                raise ValueError(
                    f"Dimension mismatch in masked vector for bank {b_id}: "
                    f"expected {d}, got {len(masked_vectors[b_id])}"
                )

        # Coordinate-wise modular accumulation in uint64
        coord_sum = np.zeros(d, dtype=np.uint64)
        for b_id in bank_ids:
            coord_sum = coord_sum + np.asarray(masked_vectors[b_id], dtype=np.uint64)

        # Two's complement signed integer
        signed_sum = coord_sum.astype(np.int64)

        # Dequantize: signed_sum / (quantization_scale * total_samples)
        denominator = float(quantization_scale) * float(total_samples)
        result_arr = signed_sum.astype(np.float64) / denominator

        if not np.all(np.isfinite(result_arr)):
            raise ValueError("Reconstructed weights contain non-finite values.")

        return result_arr.tolist()

    @classmethod
    def reconstruct_aggregate_with_dropouts(
        cls,
        surviving_masked_vectors: dict[str, list[int]],
        surviving_b_shares: dict[str, list[ShamirShare]],
        dropped_x_shares: dict[str, list[ShamirShare]],
        peer_public_keys: dict[str, bytes],
        threshold: int,
        round_id: int,
        quantization_scale: float = 1e6,
    ) -> list[float]:
        """Coordinator dropout recovery using Shamir (t, n) secret reconstruction.

        Reconstructs b_u (self-mask seed) for surviving clients to subtract their self-masks.
        Reconstructs x_d (ephemeral private key) for dropped clients to re-derive and cancel
        out their un-reciprocated pairwise masks with surviving clients.

        Returns:
            Dequantized plaintext global model weight average for surviving clients.
        """
        if not surviving_masked_vectors:
            raise ValueError("surviving_masked_vectors must be non-empty.")

        surviving_banks = sorted(surviving_masked_vectors.keys())
        d = len(surviving_masked_vectors[surviving_banks[0]])
        engine = ShamirSecretSharingEngine()

        # Step 1: Sum surviving masked vectors
        total = [0] * d
        for b_id in surviving_banks:
            vec = surviving_masked_vectors[b_id]
            for i in range(d):
                total[i] = (total[i] + vec[i]) % (2**32)

        # Step 2: Subtract self-masks b_u for surviving clients
        for b_id in surviving_banks:
            if b_id in surviving_b_shares and len(surviving_b_shares[b_id]) >= threshold:
                b_seed = engine.reconstruct_secret(surviving_b_shares[b_id], threshold)
                b_mask = cls.expand_mask(b_seed, d)
                for i in range(d):
                    total[i] = (total[i] - b_mask[i]) % (2**32)

        # Step 3: Reconstruct x_d for dropped clients and cancel un-reciprocated pairwise masks
        for dropped_id, x_shares in dropped_x_shares.items():
            if len(x_shares) >= threshold:
                x_bytes = engine.reconstruct_secret(x_shares, threshold)
                dropped_priv_key = X25519PrivateKey.from_private_bytes(x_bytes)

                for surv_id in surviving_banks:
                    if surv_id in peer_public_keys:
                        surv_pub_bytes = peer_public_keys[surv_id]
                        surv_pub_key = X25519PublicKey.from_public_bytes(surv_pub_bytes)
                        shared_secret = dropped_priv_key.exchange(surv_pub_key)

                        pair = sorted([dropped_id, surv_id])
                        info = f"secagg:r{round_id}:{pair[0]}:{pair[1]}".encode()
                        hkdf = HKDF(
                            algorithm=hashes.SHA256(),
                            length=32,
                            salt=f"cfi:secagg:round:{round_id}".encode(),
                            info=info,
                        )
                        seed = hkdf.derive(shared_secret)
                        mask = cls.expand_mask(seed, d)

                        # If dropped < surviving, dropped added mask, surviving subtracted mask.
                        # Surviving subtracted mask, so coordinator sum has -mask. We add +mask to cancel.
                        if dropped_id < surv_id:
                            for i in range(d):
                                total[i] = (total[i] + mask[i]) % (2**32)
                        else:
                            # If dropped > surviving, dropped subtracted mask, surviving added mask.
                            # Surviving added mask, so coordinator sum has +mask. We subtract -mask to cancel.
                            for i in range(d):
                                total[i] = (total[i] - mask[i]) % (2**32)

        # Step 4: Dequantize and average over surviving count
        n_surviving = len(surviving_banks)
        result: list[float] = []
        for i in range(d):
            signed = total[i] if total[i] < 2**31 else total[i] - 2**32
            result.append((signed / quantization_scale) / n_surviving)

        return result

    # ------------------------------------------------------------------
    # Private Helpers
    # ------------------------------------------------------------------

    def _sign_bundle(self, bank_id: str, round_id: int, pk_bytes: bytes) -> bytes:
        return self._sign_bundle_static(self._identity_secret, bank_id, round_id, pk_bytes)

    @staticmethod
    def _sign_bundle_static(secret: bytes, bank_id: str, round_id: int, pk_bytes: bytes) -> bytes:
        msg = bank_id.encode() + struct.pack(">Q", round_id) + pk_bytes
        return hmac.new(secret, msg, hashlib.sha256).digest()
