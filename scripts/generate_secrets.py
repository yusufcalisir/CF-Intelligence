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
    hmac_salt = secrets.token_hex(32)
    grafana_pass = secrets.token_urlsafe(16)

    content = source_path.read_text(encoding="utf-8")

    # Replace passwords and secrets
    content = content.replace("cfi_secure_pass_2026_change_in_production", pg_pass)
    content = content.replace("cfi_redis_secure_pass_2026_change_in_production", redis_pass)
    content = content.replace("cfi_super_secret_jwt_key_2026_enterprise_floor_must_be_64_chars_hex", jwt_secret)
    content = content.replace("cfi_consortium_global_pseudonymization_salt_2026", hmac_salt)
    content = content.replace("admin_change_in_production", grafana_pass)

    dest_path.write_text(content, encoding="utf-8")
    print(f"[SUCCESS] Generated hardened production environment: {dest_path}")
    print("----------------------------------------------------------------------")
    print("  PostgreSQL Password : [SECURELY GENERATED]")
    print("  Redis Auth Token    : [SECURELY GENERATED]")
    print("  JWT Secret Key      : [256-BIT HIGH-ENTROPY HEX]")
    print("  HMAC Consortium Salt: [256-BIT HIGH-ENTROPY HEX]")
    print("----------------------------------------------------------------------")
    print("You can now launch the enterprise consortium with:")
    print("  docker compose up -d --build")


if __name__ == "__main__":
    root_dir = Path(__file__).resolve().parent.parent
    src = root_dir / ".env.example"
    dst = root_dir / ".env"

    if dst.exists():
        confirm = input("[WARNING] .env already exists. Overwrite with new cryptographic secrets? (y/N): ")
        if confirm.strip().lower() != "y":
            print("[ABORTED] Preserved existing .env file.")
            sys.exit(0)

    generate_env_file(src, dst)
