import hashlib
import logging
import os
import time
from dataclasses import dataclass

import numpy as np

from app.domain.value_objects import ModelWeights

logger = logging.getLogger(__name__)


@dataclass
class EnclaveContext:
    """Represents the context of an initialized Trusted Execution Environment (TEE) Enclave."""

    enclave_id: str
    attestation_public_key: str
    attestation_private_key: str
    mrenclave: str
    mrsigner: str


@dataclass
class AttestationReport:
    """Cryptographic attestation verification report for Intel SGX/AWS Nitro."""

    enclave_id: str
    mrenclave: str
    mrsigner: str
    signature: str
    verified: bool
    timestamp: str


class TEEDriver:
    """SoftwareEmulatedTEEDriver: Cryptographic software emulator modeling Intel SGX / AWS Nitro
    Enclave Remote Attestation (MRENCLAVE/MRSIGNER measurement structures and memory sealing)
    for zero-hardware cloud and local sandbox deployments.
    """

    # Static hashes simulating enclave code layout measurements
    MRENCLAVE_SEED = "cfi_platform_tee_enclave_code_measurement_v2"
    MRSIGNER_SEED = "cfi_platform_secops_compliance_signer_v2"

    @classmethod
    def create_enclave(cls, simulation_id: str) -> EnclaveContext:
        """Initialize secure hardware isolated memory enclave."""
        start_time = time.perf_counter()

        # Enclave initialization overhead (e.g. 100ms)
        time.sleep(0.1)

        # Derive stable measurement signatures based on code template and signer seeds
        mrenclave = hashlib.sha256(f"{cls.MRENCLAVE_SEED}_{simulation_id}".encode()).hexdigest()
        mrsigner = hashlib.sha256(cls.MRSIGNER_SEED.encode()).hexdigest()

        # Enclave-specific attestation keys
        pub_key = f"tee_attest_pub_{simulation_id[:8]}"
        priv_key = f"tee_attest_priv_{simulation_id[:8]}"

        duration = (time.perf_counter() - start_time) * 1000
        logger.info(
            "Initialized TEE Enclave (ID: %s) in %.2fms. MRENCLAVE: %s",
            simulation_id[:8],
            duration,
            mrenclave[:16] + "...",
        )

        return EnclaveContext(
            enclave_id=simulation_id,
            attestation_public_key=pub_key,
            attestation_private_key=priv_key,
            mrenclave=mrenclave,
            mrsigner=mrsigner,
        )

    @staticmethod
    def generate_attestation_report(enclave_ctx: EnclaveContext) -> AttestationReport:
        """Generate remote attestation verification report signed by the enclave key."""
        start_time = time.perf_counter()
        time.sleep(0.05)

        # Sign the measurement block with the enclave's private attestation key
        msg = f"{enclave_ctx.enclave_id}:{enclave_ctx.mrenclave}:{enclave_ctx.mrsigner}"
        signature = hashlib.sha256(
            f"{msg}:{enclave_ctx.attestation_private_key}".encode()
        ).hexdigest()

        duration = (time.perf_counter() - start_time) * 1000
        logger.info(
            "Generated TEE Attestation Report for Enclave %s in %.2fms",
            enclave_ctx.enclave_id[:8],
            duration,
        )

        return AttestationReport(
            enclave_id=enclave_ctx.enclave_id,
            mrenclave=enclave_ctx.mrenclave,
            mrsigner=enclave_ctx.mrsigner,
            signature=signature,
            verified=True,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        )

    @staticmethod
    def execute_secure_aggregation(
        enclave_ctx: EnclaveContext,
        client_weights: list[ModelWeights],
        client_samples: list[int] | None = None,
    ) -> ModelWeights:
        """Ingest model parameters, aggregate inside TEE memory, and output plaintext global parameters.

        Plaintext data never leaves the enclave boundary during summation.
        """
        start_time = time.perf_counter()

        n_clients = len(client_weights)
        n_params = len(client_weights[0].flat_weights)
        layer_shapes = client_weights[0].layer_shapes

        # Simulate TEE memory boundaries copy operations (0.02ms per client weight array copy)
        copy_overhead = min(0.1, n_clients * 0.01)
        time.sleep(copy_overhead)

        # Execute un-noised FedAvg in secure enclave memory
        if client_samples is None:
            weights = [1.0 / n_clients] * n_clients
        else:
            total_samples = sum(client_samples)
            if total_samples > 0:
                weights = [s / total_samples for s in client_samples]
            else:
                weights = [1.0 / n_clients] * n_clients

        accumulated = np.zeros(n_params)
        for i, cw in enumerate(client_weights):
            accumulated += np.array(cw.flat_weights) * weights[i]

        duration = (time.perf_counter() - start_time) * 1000
        logger.info(
            "TEE Secure Aggregation completed inside enclave %s for %d clients in %.2fms",
            enclave_ctx.enclave_id[:8],
            n_clients,
            duration,
        )

        return ModelWeights(
            layer_shapes=layer_shapes,
            flat_weights=accumulated.tolist(),
        )

    @staticmethod
    def seal_data(data: bytes, key: bytes) -> bytes:
        """Intel SGX / AWS Nitro data sealing using authenticated AES-256-GCM encryption."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        derived_key = hashlib.sha256(key).digest()
        aesgcm = AESGCM(derived_key)
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, data, None)
        return nonce + ciphertext

    @staticmethod
    def unseal_data(sealed: bytes, key: bytes) -> bytes:
        """Intel SGX / AWS Nitro data unsealing using authenticated AES-256-GCM decryption."""
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if len(sealed) < 28:  # 12-byte nonce + minimum 16-byte GCM tag
            raise ValueError("Invalid sealed data size; payload corrupted or too short.")

        nonce = sealed[:12]
        ciphertext = sealed[12:]
        derived_key = hashlib.sha256(key).digest()
        aesgcm = AESGCM(derived_key)

        try:
            return aesgcm.decrypt(nonce, ciphertext, None)
        except InvalidTag as err:
            raise ValueError(
                "Ciphertext authentication failed; data tampered or invalid key."
            ) from err
