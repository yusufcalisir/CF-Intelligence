"""Real Microsoft SEAL / TenSEAL (CKKS) Fully Homomorphic Encryption (FHE) Driver.

Provides authentic CKKS (Cheon-Kim-Kim-Song) homomorphic encryption context generation,
zero-knowledge server-side homomorphic ciphertext addition/averaging, and secret-key decryption.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from app.domain.enums import FHEBackendProvenance, FHECapabilityMode
from app.domain.value_objects import ModelWeights

logger = logging.getLogger(__name__)

# Attempt to import TenSEAL (Microsoft SEAL Python binding)
try:
    import tenseal as ts  # type: ignore[import-not-found,import-untyped]

    TENSEAL_AVAILABLE = True
except ImportError:
    ts = None  # type: ignore
    TENSEAL_AVAILABLE = False


class FHEKeyRing:
    """Contains public, secret, and evaluation keys for TenSEAL CKKS homomorphic scheme."""

    def __init__(
        self,
        key_id: str,
        poly_degree: int = 8192,
        context: Any | None = None,
        public_context_bytes: bytes | None = None,
        secret_context_bytes: bytes | None = None,
        is_emulated: bool = False,
        driver_mode: str = "TENSEAL_CKKS",
        is_cryptographic: bool = True,
        backend_provenance: str = FHEBackendProvenance.REAL_CKKS.value,
    ) -> None:
        self.key_id = key_id
        self.poly_degree = poly_degree
        self.context = context
        self.public_context_bytes = public_context_bytes or b""
        self.secret_context_bytes = secret_context_bytes or b""
        self.public_key = f"fhe_pub_key_{key_id[:8]}"
        self.secret_key = f"fhe_sec_key_{key_id[:8]}"
        self.eval_key = f"fhe_eval_key_{key_id[:8]}"
        self.is_emulated = is_emulated
        self.driver_mode = driver_mode
        self.is_cryptographic = is_cryptographic
        self.backend_provenance = backend_provenance


class BaseWeightsPayload:
    """Base payload container for FHE operations."""

    def __init__(
        self,
        key_id: str,
        noise_bound: float,
        param_count: int,
        is_emulated: bool,
        is_cryptographic: bool,
        driver_mode: str,
        backend_provenance: str,
    ) -> None:
        self.key_id = key_id
        self.noise_bound = noise_bound
        self.param_count = param_count
        self.is_emulated = is_emulated
        self.is_cryptographic = is_cryptographic
        self.driver_mode = driver_mode
        self.backend_provenance = backend_provenance

    def to_dict(self) -> dict[str, Any]:
        """Serialize payload metadata for persistence and transmission."""
        return {
            "key_id": self.key_id,
            "noise_bound": self.noise_bound,
            "param_count": self.param_count,
            "is_emulated": self.is_emulated,
            "is_cryptographic": self.is_cryptographic,
            "driver_mode": self.driver_mode,
            "backend_provenance": self.backend_provenance,
        }


class EncryptedWeights(BaseWeightsPayload):
    """Represents authentic cryptographically encrypted model parameters using TenSEAL CKKS ciphertexts."""

    def __init__(
        self,
        ciphertext_bytes: bytes,
        key_id: str,
        noise_bound: float,
        param_count: int,
        driver_mode: str = "TENSEAL_CKKS",
    ) -> None:
        super().__init__(
            key_id=key_id,
            noise_bound=noise_bound,
            param_count=param_count,
            is_emulated=False,
            is_cryptographic=True,
            driver_mode=driver_mode,
            backend_provenance=FHEBackendProvenance.REAL_CKKS.value,
        )
        self.ciphertext_bytes = ciphertext_bytes

    def to_dict(self) -> dict[str, Any]:
        import base64

        data = super().to_dict()
        data["ciphertext_b64"] = base64.b64encode(self.ciphertext_bytes).decode("ascii")
        data["payload_type"] = "CRYPTOGRAPHIC_ENCRYPTED_WEIGHTS"
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EncryptedWeights:
        import base64

        if data.get("payload_type") != "CRYPTOGRAPHIC_ENCRYPTED_WEIGHTS" or data.get("is_emulated") is True:
            raise ValueError("Cannot deserialize non-cryptographic payload as EncryptedWeights.")
        return cls(
            ciphertext_bytes=base64.b64decode(data["ciphertext_b64"]),
            key_id=data["key_id"],
            noise_bound=data["noise_bound"],
            param_count=data["param_count"],
            driver_mode=data.get("driver_mode", "TENSEAL_CKKS"),
        )


class EmulatedWeights(BaseWeightsPayload):
    """Represents non-cryptographic software-emulated weights with added Gaussian noise.

    Structurally separated from EncryptedWeights. Does NOT inherit from EncryptedWeights.
    """

    def __init__(
        self,
        key_id: str,
        param_count: int,
        simulated_plaintext_vector: list[float],
        noise_bound: float = 1e-9,
        ciphertext_bytes: bytes | None = None,
        driver_mode: str = "SOFTWARE_EMULATED",
    ) -> None:
        super().__init__(
            key_id=key_id,
            noise_bound=noise_bound,
            param_count=param_count,
            is_emulated=True,
            is_cryptographic=False,
            driver_mode=driver_mode,
            backend_provenance=FHEBackendProvenance.SOFTWARE_EMULATED.value,
        )
        self.simulated_plaintext_vector = simulated_plaintext_vector
        self.emulated_vector_bytes = ciphertext_bytes or (b"SOFTWARE_EMULATED_VECTOR_" + str(param_count).encode())

    @property
    def ciphertexts(self) -> list[float]:
        """Convenience property for accessing simulated vector."""
        return self.simulated_plaintext_vector

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data["simulated_plaintext_vector"] = self.simulated_plaintext_vector
        data["payload_type"] = "SOFTWARE_EMULATED_WEIGHTS"
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmulatedWeights:
        if data.get("payload_type") != "SOFTWARE_EMULATED_WEIGHTS" and not data.get("is_emulated"):
            raise ValueError("Cannot deserialize cryptographic payload as EmulatedWeights.")
        return cls(
            key_id=data["key_id"],
            param_count=data["param_count"],
            simulated_plaintext_vector=data["simulated_plaintext_vector"],
            noise_bound=data.get("noise_bound", 1e-9),
            driver_mode=data.get("driver_mode", "SOFTWARE_EMULATED"),
        )


def verify_cryptographic_fhe(weights: Any) -> bool:
    """Verify that a weights payload object is backed by genuine cryptographic FHE.

    Rejects software-emulated vectors and non-EncryptedWeights types.
    """
    if isinstance(weights, EmulatedWeights) or not isinstance(weights, EncryptedWeights):
        raise TypeError(
            "Cryptographic FHE gate rejected: weights object is not an authentic EncryptedWeights instance."
        )
    if getattr(weights, "is_emulated", False) or not getattr(weights, "is_cryptographic", False):
        raise ValueError(
            "Cryptographic FHE gate rejected: weights object is SOFTWARE_EMULATED and lacks real CKKS encryption."
        )
    if weights.backend_provenance != FHEBackendProvenance.REAL_CKKS.value:
        raise ValueError(
            f"Cryptographic FHE gate rejected: expected provenance REAL_CKKS, got {weights.backend_provenance}"
        )
    return True


class FHEDriver:
    """Production TenSEAL (Microsoft SEAL) CKKS Fully Homomorphic Encryption Driver.

    Executes polynomial ring CKKS homomorphic vector additions directly over
    encrypted weight updates without exposing plaintext parameters to server nodes.
    """

    @staticmethod
    def generate_keys(
        simulation_id: str,
        poly_degree: int = 8192,
        capability_mode: FHECapabilityMode | str = FHECapabilityMode.FHE_REQUIRED,
    ) -> FHEKeyRing:
        """Generate TenSEAL CKKS key ring respecting explicit capability mode contract."""
        start_time = time.perf_counter()
        mode_val = capability_mode.value if isinstance(capability_mode, FHECapabilityMode) else capability_mode.upper()

        if mode_val == FHECapabilityMode.FHE_DISABLED.value:
            raise ValueError("FHE capability is marked FHE_DISABLED. Key generation rejected.")

        if TENSEAL_AVAILABLE and ts is not None and mode_val != FHECapabilityMode.FHE_EMULATION_EXPLICITLY_REQUESTED.value:
            if poly_degree <= 4096:
                coeff_mod = [40, 20, 40]
                scale = 2**20
            else:
                coeff_mod = [60, 40, 40, 60]
                scale = 2**40

            ctx = ts.context(
                ts.SCHEME_TYPE.CKKS,
                poly_modulus_degree=poly_degree,
                coeff_mod_bit_sizes=coeff_mod,
            )
            ctx.global_scale = scale
            ctx.generate_galois_keys()
            ctx.generate_relin_keys()

            secret_bytes = ctx.serialize(save_secret_key=True)
            public_bytes = ctx.serialize(save_secret_key=False)

            duration = (time.perf_counter() - start_time) * 1000
            logger.info(
                "Generated authentic TenSEAL CKKS Keyring for %s (Poly Degree: %d) in %.2fms",
                simulation_id,
                poly_degree,
                duration,
            )

            return FHEKeyRing(
                key_id=simulation_id,
                poly_degree=poly_degree,
                context=ctx,
                public_context_bytes=public_bytes,
                secret_context_bytes=secret_bytes,
                is_emulated=False,
                driver_mode="TENSEAL_CKKS",
                is_cryptographic=True,
                backend_provenance=FHEBackendProvenance.REAL_CKKS.value,
            )
        else:
            # Check capability mode before allowing emulation
            if mode_val == FHECapabilityMode.FHE_REQUIRED.value:
                raise RuntimeError(
                    f"FHE execution required ({FHECapabilityMode.FHE_REQUIRED.value}) but authentic TenSEAL "
                    f"(Microsoft SEAL) backend is unavailable. Fail-closed: refusing software emulation."
                )

            time.sleep(0.05)
            duration = (time.perf_counter() - start_time) * 1000
            logger.warning(
                "TenSEAL unavailable or emulation requested (%s). Generating SOFTWARE_EMULATED FHE keyring.",
                mode_val,
            )
            return FHEKeyRing(
                key_id=simulation_id,
                poly_degree=poly_degree,
                public_context_bytes=b"SIMULATED_FHE_PUBLIC_KEY",
                secret_context_bytes=b"SIMULATED_FHE_SECRET_KEY",
                is_emulated=True,
                driver_mode="SOFTWARE_EMULATED",
                is_cryptographic=False,
                backend_provenance=FHEBackendProvenance.SOFTWARE_EMULATED.value,
            )

    @staticmethod
    def encrypt_weights(
        weights: ModelWeights,
        key_ring: FHEKeyRing,
        rng: np.random.Generator | None = None,
    ) -> BaseWeightsPayload:
        """Encrypt float weights into TenSEAL CKKS ciphertext bytes or EmulatedWeights."""
        start_time = time.perf_counter()
        flat_arr = np.array(weights.flat_weights, dtype=np.float64)
        param_count = len(flat_arr)
        if param_count == 0:
            raise ValueError("Cannot encrypt empty weights.")

        if not key_ring.is_emulated and TENSEAL_AVAILABLE and ts is not None and key_ring.context is not None:
            # Encrypt flat vector into CKKS polynomial ciphertext
            ckks_vec = ts.ckks_vector(key_ring.context, flat_arr)
            ciphertext_bytes = ckks_vec.serialize()
            duration = (time.perf_counter() - start_time) * 1000

            logger.info(
                "Encrypted %d parameters into TenSEAL CKKS ciphertext (%d bytes) in %.2fms",
                param_count,
                len(ciphertext_bytes),
                duration,
            )
            return EncryptedWeights(
                ciphertext_bytes=ciphertext_bytes,
                key_id=key_ring.key_id,
                noise_bound=1e-9,
                param_count=param_count,
                driver_mode="TENSEAL_CKKS",
            )
        else:
            if rng is None:
                rng = np.random.default_rng()
            noise = rng.normal(0, 1e-9, param_count)
            sim_plaintext = (flat_arr + noise).tolist()
            duration = (time.perf_counter() - start_time) * 1000

            return EmulatedWeights(
                key_id=key_ring.key_id,
                param_count=param_count,
                simulated_plaintext_vector=sim_plaintext,
                noise_bound=1e-9,
                ciphertext_bytes=b"SOFTWARE_EMULATED_VECTOR_" + str(param_count).encode(),
                driver_mode="SOFTWARE_EMULATED",
            )

    @staticmethod
    def homomorphic_average(
        encrypted_updates: list[BaseWeightsPayload],
        client_samples: list[int] | None = None,
        public_context_bytes: bytes | None = None,
    ) -> BaseWeightsPayload:
        """Perform server-side homomorphic weighted addition directly over ciphertexts."""
        if not encrypted_updates:
            raise ValueError("Cannot perform homomorphic average on empty update list.")

        start_time = time.perf_counter()
        n_clients = len(encrypted_updates)
        n_params = encrypted_updates[0].param_count
        key_id = encrypted_updates[0].key_id

        for enc in encrypted_updates:
            if enc.key_id != key_id:
                raise ValueError("Mismatched FHE keys during homomorphic aggregation.")

        if client_samples is None:
            weights = [1.0 / n_clients] * n_clients
        else:
            total_samples = sum(client_samples)
            weights = (
                [s / total_samples for s in client_samples]
                if total_samples > 0
                else [1.0 / n_clients] * n_clients
            )

        any_emulated = any(isinstance(enc, EmulatedWeights) or getattr(enc, "is_emulated", False) for enc in encrypted_updates)
        if not any_emulated and TENSEAL_AVAILABLE and ts is not None and public_context_bytes:
            # Reconstruct public context without secret key
            ctx_pub = ts.context_from(public_context_bytes)

            # Homomorphic weighted sum: c_total = sum(c_i * w_i)
            first_enc = encrypted_updates[0]
            if not isinstance(first_enc, EncryptedWeights):
                raise TypeError("Expected EncryptedWeights for cryptographic aggregation.")
            v_total = (
                ts.ckks_vector_from(ctx_pub, first_enc.ciphertext_bytes) * weights[0]
            )
            for i in range(1, n_clients):
                client_enc = encrypted_updates[i]
                if not isinstance(client_enc, EncryptedWeights):
                    raise TypeError("Expected EncryptedWeights for cryptographic aggregation.")
                v_i = ts.ckks_vector_from(ctx_pub, client_enc.ciphertext_bytes)
                v_total += v_i * weights[i]

            res_bytes = v_total.serialize()
            duration = (time.perf_counter() - start_time) * 1000

            logger.info(
                "Completed TenSEAL homomorphic CKKS average over %d ciphertexts (%d params) in %.2fms",
                n_clients,
                n_params,
                duration,
            )
            return EncryptedWeights(
                ciphertext_bytes=res_bytes,
                key_id=key_id,
                noise_bound=1e-9,
                param_count=n_params,
                driver_mode="TENSEAL_CKKS",
            )
        else:
            # Software emulated aggregation
            accumulated = np.zeros(n_params)
            for i, enc in enumerate(encrypted_updates):
                vec = enc.simulated_plaintext_vector if isinstance(enc, EmulatedWeights) else getattr(enc, "ciphertexts", [0.0] * n_params)
                accumulated += np.array(vec) * weights[i]

            duration = (time.perf_counter() - start_time) * 1000
            return EmulatedWeights(
                key_id=key_id,
                param_count=n_params,
                simulated_plaintext_vector=accumulated.tolist(),
                noise_bound=1e-9,
                ciphertext_bytes=b"SOFTWARE_EMULATED_AVG_VECTOR",
                driver_mode="SOFTWARE_EMULATED",
            )

    @staticmethod
    def decrypt_weights(
        encrypted_weights: BaseWeightsPayload,
        key_ring: FHEKeyRing,
        layer_shapes: list[tuple[int, ...]],
        require_cryptographic: bool = False,
    ) -> ModelWeights:
        """Decrypt TenSEAL CKKS ciphertext or decode EmulatedWeights to ModelWeights."""
        start_time = time.perf_counter()

        if isinstance(encrypted_weights, EmulatedWeights):
            if require_cryptographic or not key_ring.is_emulated:
                raise ValueError(
                    "Cryptographic decryption rejected: cannot decrypt EmulatedWeights with authentic CKKS key ring."
                )
            if encrypted_weights.key_id != key_ring.key_id:
                raise ValueError("Invalid secret key for decryption.")
            flat_weights = encrypted_weights.simulated_plaintext_vector
            duration = (time.perf_counter() - start_time) * 1000

            return ModelWeights(
                layer_shapes=layer_shapes,
                flat_weights=flat_weights,
            )

        if encrypted_weights.key_id != key_ring.key_id:
            raise ValueError("Invalid secret key for decryption.")

        if not isinstance(encrypted_weights, EncryptedWeights):
            raise TypeError("Unsupported weights payload type for decryption.")

        if key_ring.is_emulated:
            raise ValueError(
                "Cannot decrypt authentic EncryptedWeights using an emulated FHEKeyRing."
            )

        if not TENSEAL_AVAILABLE or ts is None or not key_ring.secret_context_bytes or key_ring.context is None:
            raise RuntimeError(
                "Cannot decrypt authentic EncryptedWeights: TenSEAL backend or secret context is unavailable."
            )

        ctx_sec = ts.context_from(key_ring.secret_context_bytes)
        vec = ts.ckks_vector_from(ctx_sec, encrypted_weights.ciphertext_bytes)
        flat_weights = vec.decrypt()

        duration = (time.perf_counter() - start_time) * 1000
        logger.info(
            "Decrypted %d TenSEAL CKKS parameters in %.2fms",
            len(flat_weights),
            duration,
        )

        return ModelWeights(
            layer_shapes=layer_shapes,
            flat_weights=[float(x) for x in flat_weights],
        )

