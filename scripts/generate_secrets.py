#!/usr/bin/env python3
"""Enterprise Secrets & Environment Generator for CFI Platform.

Generates cryptographically random 256-bit secrets for PostgreSQL, Redis,
JWT authentication, and HMAC consortium salts, creating a production-ready .env.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path


def generate_env_file(source_path: Path, dest_path: Path) -> None:
    if not source_path.exists():
        print(f"[ERROR] Source template not found: {source_path}")
        sys.exit(1)

    pg_pass = secrets.token_urlsafe(24)
    redis_pass = secrets.token_urlsafe(24)
    jwt_secret = secrets.token_hex(32)
    oidc_jwt_secret = secrets.token_hex(32)
    payload_signing_secret = secrets.token_hex(32)
    coordinator_token = secrets.token_hex(32)
    worker_token = secrets.token_hex(32)
    while worker_token == coordinator_token:
        worker_token = secrets.token_hex(32)
    identity_store_secret = secrets.token_hex(32)
    consortium_store_secret = secrets.token_hex(32)
    while consortium_store_secret == identity_store_secret:
        consortium_store_secret = secrets.token_hex(32)
    hmac_salt = secrets.token_hex(32)
    reg_envelope_secret = secrets.token_hex(32)
    cron_secret = secrets.token_hex(32)
    grafana_pass = secrets.token_urlsafe(16)

    content = source_path.read_text(encoding="utf-8")

    # Replace passwords and secrets across all configuration directives
    content = content.replace("cfi_secure_pass_2026_change_in_production", pg_pass)
    content = content.replace("cfi_redis_secure_pass_2026_change_in_production", redis_pass)
    content = content.replace("cfi_super_secret_jwt_key_2026_enterprise_floor_must_be_64_chars_hex", jwt_secret)
    content = content.replace("cfi_oidc_jwt_secret_key_2026_enterprise_change_in_production", oidc_jwt_secret)
    content = content.replace("cfi_oidc_jwt_secret_key_2026_enterprise_hs256", oidc_jwt_secret)
    content = content.replace("cfi_payload_signing_secret_2026_change_in_production", payload_signing_secret)
    content = content.replace("cfi_local_secret_key_2026_change_me_in_production", payload_signing_secret)
    content = content.replace("cfi_training_coordinator_token_2026_change_in_production", coordinator_token)
    content = content.replace("cfi_training_worker_token_2026_change_in_production", worker_token)
    content = content.replace("cfi_identity_store_secret_2026_change_in_production", identity_store_secret)
    content = content.replace("cfi_consortium_store_secret_2026_change_in_production", consortium_store_secret)
    content = content.replace("cfi_consortium_global_pseudonymization_salt_2026", hmac_salt)
    content = content.replace("cfi_regulatory_envelope_hmac_secret_2026_enterprise", reg_envelope_secret)
    content = content.replace("cfi_cron_secret_secure_token_2026", cron_secret)
    content = content.replace("admin_change_in_production", grafana_pass)

    # Ensure required sync training job URL is present if operating on a minimal template
    if "TRAINING_JOB_DB_URL=" not in content:
        content += (
            f"\n# Training Authority Database URL (sync driver for execution leases)\n"
            f"TRAINING_JOB_DB_URL=postgresql://cfi_admin:{pg_pass}@postgres:5432/cfi_fraud_db\n"
        )

    dest_path.write_text(content, encoding="utf-8")
    print(f"[SUCCESS] Generated hardened production environment: {dest_path}")
    print("----------------------------------------------------------------------")
    print("  PostgreSQL Password          : [SECURELY GENERATED]")
    print("  Redis Auth Token             : [SECURELY GENERATED]")
    print("  JWT Secret Key               : [256-BIT HIGH-ENTROPY HEX]")
    print("  OIDC JWT Signing Secret      : [256-BIT HIGH-ENTROPY HEX]")
    print("  Payload Signing Secret       : [256-BIT HIGH-ENTROPY HEX]")
    print("  Training Coordinator Token   : [256-BIT HIGH-ENTROPY HEX]")
    print("  Training Worker Token        : [256-BIT HIGH-ENTROPY HEX]")
    print("  Identity Store Secret        : [256-BIT HIGH-ENTROPY HEX]")
    print("  HMAC Consortium Salt         : [256-BIT HIGH-ENTROPY HEX]")
    print("----------------------------------------------------------------------")
    print("You can now launch the enterprise consortium with:")
    print("  docker compose up -d --build")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Enterprise Secrets & Environment Generator for CFI Platform."
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Overwrite existing .env without confirmation prompt",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".env.example",
        help="Path to source .env template (default: .env.example)",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".env",
        help="Path to destination .env file (default: .env)",
    )
    args = parser.parse_args()

    src = args.source
    dst = args.destination

    if dst.exists() and not args.force:
        if not sys.stdin.isatty():
            print("[INFO] Destination .env already exists and stdin is non-interactive. Use --force to overwrite.")
            sys.exit(0)
        confirm = input("[WARNING] .env already exists. Overwrite with new cryptographic secrets? (y/N): ")
        if confirm.strip().lower() != "y":
            print("[ABORTED] Preserved existing .env file.")
            sys.exit(0)

    generate_env_file(src, dst)
