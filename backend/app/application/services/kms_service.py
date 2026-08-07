"""Simulated Key Management Service (KMS) / Hardware Security Module (HSM).

Provides cryptographically isolated key material per bank tenant.
In production, this would delegate to AWS KMS, Azure Key Vault, Google Cloud KMS,
or a Thales Luna / Utimaco HSM appliance over PKCS#11.

Each bank's keys are stored in a separate directory under ``storage/{bank_id}/kms/``
and are never shared across tenants.  The system-level coordinator has its own
key namespace (``storage/kms/system/``).

Key types managed:
    * HMAC keys   — used for privacy-preserving entity hashing
    * PSI exponents — Diffie-Hellman private scalars for Private Set Intersection
    * Aggregation mask seeds — seed bytes for secure aggregation mask generation
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any

logger = logging.getLogger(__name__)

from app.infrastructure.storage.storage_utils import get_storage_dir

_STORAGE_ROOT = get_storage_dir()

# DH-PSI prime — must match psi_service.py
_PSI_PRIME = 0xDEB00B9C694F4BE84A28B101E6A0F1D8B9646D0BF1A0F53FBAFF74205A405D021C7B38A8DE5F482F6B8470E04E5FCEF5BA88CEB8E5E7A0D0BF7BCAAA83DE4F2D


class KMSService:
    """Per-tenant cryptographic key management simulator.

    Keys are lazily generated on first access and persisted to the
    tenant-isolated filesystem vault.  All key operations are scoped
    to a specific ``bank_id``.
    """

    def __init__(self, storage_root: str | None = None) -> None:
        self._storage_root = storage_root or _STORAGE_ROOT

    # ── Vault path helpers ────────────────────

    def _vault_dir(self, bank_id: str) -> str:
        """Return the KMS vault directory for a given tenant."""
        vault = os.path.join(self._storage_root, bank_id, "kms")
        os.makedirs(vault, exist_ok=True)
        return vault

    def _keys_path(self, bank_id: str) -> str:
        return os.path.join(self._vault_dir(bank_id), "keys.json")

    # ── Persistence ───────────────────────────

    def _load_keys(self, bank_id: str) -> dict[str, Any]:
        path = self._keys_path(bank_id)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning("Failed to load KMS keys for %s: %s", bank_id, exc)
        return {}

    def _save_keys(self, bank_id: str, keys: dict[str, Any]) -> None:
        path = self._keys_path(bank_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(keys, f, indent=2)

    # ── Key accessors ─────────────────────────

    def get_hmac_key(self, bank_id: str) -> str:
        """Return the HMAC-SHA256 key for privacy-preserving entity hashing.

        Generates a 256-bit key on first access and persists it in the
        bank's local vault.  This key never leaves the tenant boundary.
        """
        keys = self._load_keys(bank_id)
        if "hmac_key" not in keys:
            keys["hmac_key"] = secrets.token_hex(32)
            self._save_keys(bank_id, keys)
            logger.info("Generated new HMAC key for %s", bank_id)
        return keys["hmac_key"]

    def get_psi_private_exponent(self, bank_id: str) -> int:
        """Return the DH-PSI private scalar for modular exponentiation.

        Each bank has its own unique private exponent, generated once
        and stored in the local vault.
        """
        keys = self._load_keys(bank_id)
        if "psi_exponent" not in keys:
            exponent = secrets.randbelow(_PSI_PRIME - 2) + 2
            keys["psi_exponent"] = str(exponent)
            self._save_keys(bank_id, keys)
            logger.info("Generated new PSI private exponent for %s", bank_id)
        return int(keys["psi_exponent"])

    def get_aggregation_mask_seed(self, bank_id: str) -> bytes:
        """Return the seed bytes for secure aggregation mask generation.

        Used to initialize the NumPy RNG for pairwise mask generation
        in the federated learning secure aggregation protocol.
        """
        keys = self._load_keys(bank_id)
        if "aggregation_seed" not in keys:
            keys["aggregation_seed"] = secrets.token_hex(32)
            self._save_keys(bank_id, keys)
            logger.info("Generated new aggregation mask seed for %s", bank_id)
        return bytes.fromhex(keys["aggregation_seed"])

    def derive_round_mask_seed(self, bank_id: str, round_id: int) -> bytes:
        """Derives a round-specific mask seed using HKDF-SHA256.

        Prevents cross-round static mask seed persistence and update differencing attacks.
        """
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        master_seed = self.get_aggregation_mask_seed(bank_id)
        info = f"secagg_round_{round_id}".encode()
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=info,
        )
        return hkdf.derive(master_seed)

    def rotate_key(self, bank_id: str, key_type: str) -> str:
        """Force-rotate a specific key type for a tenant.

        Returns the new key value as a hex string.
        """
        keys = self._load_keys(bank_id)
        if key_type == "hmac_key":
            keys["hmac_key"] = secrets.token_hex(32)
        elif key_type == "psi_exponent":
            keys["psi_exponent"] = str(secrets.randbelow(_PSI_PRIME - 2) + 2)
        elif key_type == "aggregation_seed":
            keys["aggregation_seed"] = secrets.token_hex(32)
        else:
            raise ValueError(f"Unknown key type: {key_type}")

        self._save_keys(bank_id, keys)
        logger.info("Rotated %s for %s", key_type, bank_id)
        return str(keys[key_type])

    def list_tenants(self) -> list[str]:
        """List all tenants that have KMS vaults on disk."""
        tenants: list[str] = []
        if not os.path.exists(self._storage_root):
            return tenants
        for entry in os.listdir(self._storage_root):
            kms_dir = os.path.join(self._storage_root, entry, "kms")
            if os.path.isdir(kms_dir):
                tenants.append(entry)
        return sorted(tenants)


# Module-level singleton for convenience
_default_kms: KMSService | None = None


def get_kms_service() -> KMSService:
    """Return the shared KMS service instance."""
    global _default_kms
    if _default_kms is None:
        _default_kms = KMSService()
    return _default_kms
