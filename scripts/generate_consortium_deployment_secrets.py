#!/usr/bin/env python3
"""Consortium Deployment Secret and Institutional Identity Generator.

Generates cryptographically random parameters for the local multi-bank
consortium sandbox deployment environment:
1. Sovereign Ed25519 institutional keypairs for Bank A, Bank B, and Bank C.
2. Scoped institutional worker bearer tokens (matching, ingestion, admin).
3. Independent non-predictable analyst credentials for Bank A, Bank B, and Bank C.
4. Peer verification key registries for worker nodes and backend admission.
5. Durable storage, cache, and platform signing secrets.

Usage:
    python scripts/generate_consortium_deployment_secrets.py --output .env.consortium

Protects the generated output file with restrictive filesystem permissions (0600 / user-only ACL).
Ensures zero hardcoded or test-shaped magic values.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import stat
import subprocess
import sys
from pathlib import Path

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
        # Localhost-only sandbox port bindings (127.0.0.1)
        "CFI_PORT_HTTP": "80",
        "CFI_PORT_BACKEND": "8000",
        "CFI_PORT_FRONTEND": "3000",
        "CFI_PORT_COORDINATOR": "8200",
        "CFI_PORT_WORKER_A": "8101",
        "CFI_PORT_WORKER_B": "8102",
        "CFI_PORT_WORKER_C": "8103",
        # Storage & infrastructure (internal to consortium-core-net)
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
        # Independent non-predictable institutional analyst & operator credentials (sandbox bootstrap)
        "ANALYST_BANK_A_PASSWORD": secrets.token_urlsafe(24),
        "ANALYST_BANK_B_PASSWORD": secrets.token_urlsafe(24),
        "ANALYST_BANK_C_PASSWORD": secrets.token_urlsafe(24),
        "CONSORTIUM_OPERATOR_PASSWORD": secrets.token_urlsafe(24),
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


def _restrict_directory_permissions(target_dir: Path) -> None:
    """Apply restrictive owner-only permissions (0700 / Windows user ACL) where supported."""
    with contextlib.suppress(OSError):
        os.chmod(target_dir, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    if os.name == "nt":
        username = os.environ.get("USERNAME")
        if username:
            subprocess.run(
                [
                    "icacls",
                    str(target_dir),
                    "/inheritance:r",
                    "/grant:r",
                    f"{username}:(OI)(CI)(F)",
                ],
                capture_output=True,
                text=True,
                check=False,
            )


def _restrict_file_permissions(target_path: Path) -> None:
    """Apply restrictive owner-only permissions (0600 / Windows user ACL) where supported."""
    with contextlib.suppress(OSError):
        os.chmod(target_path, stat.S_IRUSR | stat.S_IWUSR)

    if os.name == "nt":
        username = os.environ.get("USERNAME")
        if username:
            subprocess.run(
                [
                    "icacls",
                    str(target_path),
                    "/inheritance:r",
                    "/grant:r",
                    f"{username}:(R,W)",
                ],
                capture_output=True,
                text=True,
                check=False,
            )


def ensure_safe_secret_file_target(target_path: Path, force: bool = False) -> None:
    """Prevent accidental overwrite of active institutional signing keys and credentials."""
    if not target_path.exists():
        return
    try:
        existing_text = target_path.read_text(encoding="utf-8")
    except OSError:
        existing_text = ""
    has_active_secrets = any(
        marker in existing_text
        for marker in (
            "WORKER_BANK_A_SIGNING_KEY=",
            "CFI_INSTITUTIONAL_PUBLIC_KEYS_JSON=",
            "ANALYST_BANK_A_PASSWORD=",
        )
    )
    if (existing_text.strip() or has_active_secrets) and not force:
        raise FileExistsError(
            f"Refusing to overwrite existing consortium secret file '{target_path}' containing active "
            "institutional keys/credentials. Pass --force (or force=True) to explicitly rotate/overwrite."
        )


def write_isolated_key_custody_files(
    env_vars: dict[str, str], custody_dir: Path, force: bool = False
) -> dict[str, Path]:
    """Write per-institution isolated key files and public verification registry with 0600 permissions."""
    custody_dir.mkdir(parents=True, exist_ok=True)
    _restrict_directory_permissions(custody_dir)
    written: dict[str, Path] = {}
    key_map = {
        "bank_a": env_vars["WORKER_BANK_A_SIGNING_KEY"],
        "bank_b": env_vars["WORKER_BANK_B_SIGNING_KEY"],
        "bank_c": env_vars["WORKER_BANK_C_SIGNING_KEY"],
    }
    for bank_id, priv_hex in key_map.items():
        # Write both standard .pem and .ed25519.key representations with 0600 permissions
        for suffix in (f"{bank_id}_signing_key.pem", f"{bank_id}.ed25519.key"):
            key_path = custody_dir / suffix
            if key_path.exists() and key_path.stat().st_size > 0 and not force:
                raise FileExistsError(
                    f"Refusing to overwrite existing institutional private key file '{key_path}' without --force."
                )
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(str(key_path), flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(priv_hex)
            _restrict_file_permissions(key_path)

        pem_path = custody_dir / f"{bank_id}_signing_key.pem"
        written[bank_id] = pem_path
        written[f"{bank_id}_ed25519"] = custody_dir / f"{bank_id}.ed25519.key"

    reg_path = custody_dir / "institutional_public_keys.json"
    if reg_path.exists() and reg_path.stat().st_size > 0 and not force:
        raise FileExistsError(
            f"Refusing to overwrite existing institutional public keys registry '{reg_path}' without --force."
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(reg_path), flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(env_vars["CFI_INSTITUTIONAL_PUBLIC_KEYS_JSON"])
    _restrict_file_permissions(reg_path)
    written["public_registry"] = reg_path
    return written


def write_env_file(
    env_vars: dict[str, str],
    target_path: Path,
    force: bool = False,
    strip_private_keys: bool = False,
) -> None:
    """Format and write environment dictionary to file with restrictive permissions."""
    ensure_safe_secret_file_target(target_path, force=force)
    lines = [
        "# ==============================================================================",
        "# Cross-Bank Federated Intelligence (CFI) - Local Consortium Sandbox Secrets",
        "# Classification: SINGLE_HOST_DOCKER_SANDBOX (Not multi-institution HSM custody)",
        "# Generated automatically. DO NOT COMMIT OR LEAK THIS FILE.",
        "# ==============================================================================",
        "",
    ]
    for key, val in env_vars.items():
        if strip_private_keys and key in (
            "WORKER_BANK_A_SIGNING_KEY",
            "WORKER_BANK_B_SIGNING_KEY",
            "WORKER_BANK_C_SIGNING_KEY",
        ):
            continue
        lines.append(f"{key}={val}")
    lines.append("")

    content = "\n".join(lines)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(target_path), flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    _restrict_file_permissions(target_path)


DEFAULT_KEY_CUSTODY_DIR = Path("deploy/consortium/keys")


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
        "--force",
        action="store_true",
        help="Explicitly authorize overwriting an existing .env.consortium secret file.",
    )
    parser.add_argument(
        "--key-custody-dir",
        type=Path,
        default=DEFAULT_KEY_CUSTODY_DIR,
        help="Directory to write isolated per-institution Ed25519 key files (default: deploy/consortium/keys). Pass 'none' to disable.",
    )
    parser.add_argument(
        "--print-registry",
        action="store_true",
        help="Print the generated public verification keys registry to stdout.",
    )
    args = parser.parse_args()

    custody_dir: Path | None = args.key_custody_dir
    if custody_dir is not None and str(custody_dir).strip().lower() in ("none", ""):
        custody_dir = None

    try:
        ensure_safe_secret_file_target(args.output, force=args.force)
        env_vars = generate_consortium_deployment_secrets()
        if custody_dir is not None:
            write_isolated_key_custody_files(env_vars, custody_dir, force=args.force)
            write_env_file(env_vars, args.output, force=args.force, strip_private_keys=True)
        else:
            write_env_file(env_vars, args.output, force=args.force, strip_private_keys=False)
    except FileExistsError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        sys.exit(1)

    sys.stdout.write(f"Consortium deployment secrets written successfully to '{args.output}'.\n")
    if custody_dir is not None:
        sys.stdout.write(f"Institutional signing keys written to file custody directory '{custody_dir}'.\n")
    if args.print_registry:
        reg = json.loads(env_vars["CFI_INSTITUTIONAL_PUBLIC_KEYS_JSON"])
        sys.stdout.write("Enrolled Institutional Public Keys (Ed25519):\n")
        for bank_id, pub in reg.items():
            sys.stdout.write(f"  - {bank_id}: {pub}\n")


if __name__ == "__main__":
    main()
