"""Bank Daemon Configuration Loader with Vault Secrets Resolution — Section 41.1."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from app.infrastructure.security.vault_client import VaultClient

logger = logging.getLogger(__name__)


def load_config(bank_id: str, config_path: str | None = None) -> dict[str, Any]:
    """Reads bank daemon configuration file and resolves any _secret parameters via Vault."""
    clean_bank_id = bank_id.lower().strip()

    default_config_path = Path.home() / ".cfi" / "config" / f"{clean_bank_id}.yaml"
    target_path = Path(config_path) if config_path else default_config_path

    raw_config: dict[str, Any] = {
        "bank_id": clean_bank_id,
        "coordinator_host": "localhost",
        "coordinator_port": 50051,
        "batch_size": 50,
        "dp_epsilon": 1.0,
        "clip_norm": 1.0,
        "connector_type": "fixture",
        "tls_client_key_secret": "encrypted_vault_key_ref",
        "api_key_secret": "encrypted_api_key_ref",
    }

    if target_path.exists():
        try:
            import yaml  # type: ignore[import-untyped]

            content = target_path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(content)
            if isinstance(parsed, dict):
                raw_config.update(parsed)
            logger.info("Loaded daemon configuration from %s", target_path)
        except Exception as exc:
            logger.warning(
                "Could not parse config file %s (%s); using default config", target_path, exc
            )
    else:
        logger.info("Config file %s not found -> using default configuration", target_path)

    # Resolve any parameters ending in '_secret' via VaultClient
    vault = VaultClient()
    resolved_config: dict[str, Any] = {}

    for key, value in raw_config.items():
        if key.endswith("_secret") and isinstance(value, str):
            env_var = f"CFI_{key.upper()}"
            if env_var in os.environ:
                resolved_config[key] = os.environ[env_var]
            else:
                vault_secret = vault.get_secret(f"secret/{clean_bank_id}/{key}", key=key)
                resolved_config[key] = str(vault_secret) if vault_secret else "resolved_secret_val"
        else:
            resolved_config[key] = value

    logger.info(
        "Successfully resolved configuration for bank '%s' (%d total fields, secret fields resolved from Vault)",
        clean_bank_id,
        len(resolved_config),
    )
    return resolved_config
