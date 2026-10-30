#!/usr/bin/env python3
"""Consortium Deployment Secret and Institutional Identity Generator.

Generates cryptographically authoritative parameters for the multi-bank
consortium deployment environment:
1. Sovereign Ed25519 institutional keypairs for Bank A, Bank B, and Bank C.
2. Scoped institutional bearer tokens (matching, ingestion, admin).
3. Peer verification key registries for worker nodes.
4. Backend verification registry mapping for admission verification.
5. Durable storage, cache, and platform signing secrets.

Outputs the generated configuration to '.env.consortium' by default.
Ensures zero hardcoded or test-shaped magic values.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def generate_institutional_keypair() -> tuple[str, str]:
    """Generate sovereign Ed25519 keypair returning (private_key_hex, public_key_hex)."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    raw_priv = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_pub = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return raw_priv.hex(), raw_pub.hex()


def generate_consortium_deployment_secrets() -> dict[str, str]:
    """Generate complete dictionary of deployment environment variables."""
    # Sovereign keypairs for each institution
    priv_a, pub_a = generate_institutional_keypair()
    priv_b, pub_b = generate_institutional_keypair()
    priv_c, pub_c = generate_institutional_keypair()

    # Trusted peer registries for each worker node
    trusted_peers_a = json.dumps({"bank_b": pub_b, "bank_c": pub_c})
    trusted_peers_b = json.dumps({"bank_a": pub_a, "bank_c": pub_c})
    trusted_peers_c = json.dumps({"bank_a": pub_a, "bank_b": pub_b})

    # Central backend institutional verification registry
    backend_institutional_registry = json.dumps(
        {"bank_a": pub_a, "bank_b": pub_b, "bank_c": pub_c}
    )

    env_vars: dict[str, str] = {
        # Environment & logging
        "APP_ENV": "production",
        "LOG_LEVEL": "INFO",
        # Port bindings
        "CFI_PORT_HTTP": "80",
        "CFI_PORT_HTTPS": "443",
        "CFI_PORT_BACKEND": "8000",
        "CFI_PORT_FRONTEND": "3000",
        "CFI_PORT_COORDINATOR": "8200",
        "CFI_PORT_WORKER_A": "8101",
        "CFI_PORT_WORKER_B": "8102",
        "CFI_PORT_WORKER_C": "8103",
        "CFI_PORT_PG": "5432",
        "CFI_PORT_REDIS": "6379",
        # Storage & infrastructure
        "POSTGRES_USER": "cfi_admin",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "POSTGRES_DB": "cfi_fraud_db",
        "REDIS_PASSWORD": secrets.token_urlsafe(24),
        # Platform security
        "SECRET_KEY": secrets.token_hex(32),
        "OIDC_JWT_SIGNING_SECRET": secrets.token_hex(32),
        "PAYLOAD_SIGNING_SECRET": secrets.token_hex(32),
        "IDENTITY_STORE_SECRET": secrets.token_hex(32),
        "CFI_AUDIT_LEDGER_SECRET": secrets.token_hex(32),
        "CFI_CONSORTIUM_STORE_SECRET": secrets.token_hex(32),
        "CONSORTIUM_HMAC_SALT": secrets.token_hex(32),
        "CFI_REGULATORY_ENVELOPE_SECRET": secrets.token_hex(32),
        "CFI_CRON_SECRET": secrets.token_hex(24),
        "CFI_COORDINATOR_SECRET": secrets.token_hex(24),
        "TRAINING_COORDINATOR_TOKEN": secrets.token_hex(32),
        "TRAINING_WORKER_TOKEN": secrets.token_hex(32),
        # Bank A worker
        "WORKER_BANK_A_SIGNING_KEY": priv_a,
        "WORKER_BANK_A_AUTH_SECRET": secrets.token_hex(32),
        "WORKER_BANK_A_MATCHING_TOKEN": secrets.token_hex(32),
        "WORKER_BANK_A_INGESTION_TOKEN": secrets.token_hex(32),
        "WORKER_BANK_A_ADMIN_TOKEN": secrets.token_hex(32),
        "WORKER_BANK_A_TRUSTED_PEERS_JSON": trusted_peers_a,
        # Bank B worker
        "WORKER_BANK_B_SIGNING_KEY": priv_b,
        "WORKER_BANK_B_AUTH_SECRET": secrets.token_hex(32),
        "WORKER_BANK_B_MATCHING_TOKEN": secrets.token_hex(32),
        "WORKER_BANK_B_INGESTION_TOKEN": secrets.token_hex(32),
        "WORKER_BANK_B_ADMIN_TOKEN": secrets.token_hex(32),
        "WORKER_BANK_B_TRUSTED_PEERS_JSON": trusted_peers_b,
        # Bank C worker
        "WORKER_BANK_C_SIGNING_KEY": priv_c,
        "WORKER_BANK_C_AUTH_SECRET": secrets.token_hex(32),
        "WORKER_BANK_C_MATCHING_TOKEN": secrets.token_hex(32),
        "WORKER_BANK_C_INGESTION_TOKEN": secrets.token_hex(32),
        "WORKER_BANK_C_ADMIN_TOKEN": secrets.token_hex(32),
        "WORKER_BANK_C_TRUSTED_PEERS_JSON": trusted_peers_c,
        # Central backend registry
        "CFI_INSTITUTIONAL_PUBLIC_KEYS_JSON": backend_institutional_registry,
    }

    return env_vars


def write_env_file(env_vars: dict[str, str], target_path: Path) -> None:
    """Format and write environment dictionary to file."""
    lines = [
        "# ==============================================================================",
        "# Cross-Bank Federated Intelligence (CFI) — Active Consortium Secrets",
        "# Generated automatically. DO NOT COMMIT OR LEAK THIS FILE.",
        "# ==============================================================================",
        "",
    ]
    for key, val in env_vars.items():
        lines.append(f"{key}={val}")
    lines.append("")

    target_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate authoritative secrets and Ed25519 keys for consortium deployment."
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path(".env.consortium"),
        help="Target output file path (default: .env.consortium)",
    )
    parser.add_argument(
        "--print-registry",
        action="store_true",
        help="Print the generated public verification keys registry to stdout.",
    )
    args = parser.parse_args()

    env_vars = generate_consortium_deployment_secrets()
    write_env_file(env_vars, args.output)

    sys.stdout.write(f"Consortium deployment secrets written successfully to '{args.output}'.\n")
    if args.print_registry:
        reg = json.loads(env_vars["CFI_INSTITUTIONAL_PUBLIC_KEYS_JSON"])
        sys.stdout.write("Enrolled Institutional Public Keys (Ed25519):\n")
        for bank_id, pub in reg.items():
            sys.stdout.write(f"  - {bank_id}: {pub}\n")


if __name__ == "__main__":
    main()
