"""Unit tests for cfi-cli real command implementations - Section 36.2.

All tests use unittest.mock to patch urllib network calls; no live coordinator needed.
"""

from __future__ import annotations

import hashlib
import json
import unittest.mock as mock
from pathlib import Path  # noqa: TC003  (pytest fixture - not moveable)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from app.presentation.cli.cfi_cli import (
    command_export_diagnostics,
    command_join,
    command_rotate_certs,
    command_status,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

FAKE_BUNDLE = {
    "bank_id": "bank_alpha",
    "status": "active",
    "legal_name": "Alpha National Bank",
    "jurisdiction": "TR",
    "contact_email": "security@alphabank.com",
    "data_residency_region": "eu-west-1",
    "cert_fingerprint": "abc123def456" * 5,
    "mtls_cert_pem": "-----BEGIN CERTIFICATE-----\nFAKE_CERT\n-----END CERTIFICATE-----",
    "mtls_key_pem": "-----BEGIN PRIVATE KEY-----\nFAKE_KEY\n-----END PRIVATE KEY-----",
    "connector_config_yaml": 'bank_id: "bank_alpha"\ncoordinator_url: "https://coord.test"\n',
    "coordinator_endpoint": "https://coordinator.cf-intelligence.io:50051",
}

FAKE_STATUS = {
    "bank_id": "bank_alpha",
    "legal_name": "Alpha National Bank",
    "jurisdiction": "TR",
    "status": "ACTIVE",
    "cert_fingerprint": "abc123def456",
    "vault_key_path": "transit/keys/tenant_bank_alpha",
    "schema_provisioned": True,
    "created_at": "2026-07-24T18:00:00",
    "activated_at": "2026-07-24T18:05:00",
}

FAKE_ROTATION = {
    "bank_id": "bank_alpha",
    "mtls_cert_pem": "-----BEGIN CERTIFICATE-----\nROTATED_CERT\n-----END CERTIFICATE-----",
    "mtls_key_pem": "-----BEGIN PRIVATE KEY-----\nROTATED_KEY\n-----END PRIVATE KEY-----",
    "cert_fingerprint": "newfingerprint123",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _patch_api(return_value: dict, status_code: int = 200):
    """Patch _api_request to return return_value without network calls."""
    return mock.patch(
        "app.presentation.cli.cfi_cli._api_request",
        return_value=return_value,
    )


def _setup_active_bank(tmp_path: Path, monkeypatch) -> None:
    """Redirect CFI_HOME to tmp_path and write an active_bank config."""
    monkeypatch.setattr("app.presentation.cli.cfi_cli.CFI_HOME", tmp_path)
    monkeypatch.setattr("app.presentation.cli.cfi_cli.CFI_CERTS_DIR", tmp_path / "certs")
    monkeypatch.setattr("app.presentation.cli.cfi_cli.CFI_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(
        "app.presentation.cli.cfi_cli.ACTIVE_BANK_FILE",
        tmp_path / "config" / "active_bank",
    )
    (tmp_path / "certs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "active_bank").write_text(
        json.dumps({"bank_id": "bank_alpha", "coordinator_url": "http://coord.test"}),
        encoding="utf-8",
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_join_command_saves_cert_files(tmp_path: Path, monkeypatch) -> None:
    """join should POST to coordinator and write cert, key, and config files to disk."""
    monkeypatch.setattr("app.presentation.cli.cfi_cli.CFI_HOME", tmp_path)
    monkeypatch.setattr("app.presentation.cli.cfi_cli.CFI_CERTS_DIR", tmp_path / "certs")
    monkeypatch.setattr("app.presentation.cli.cfi_cli.CFI_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(
        "app.presentation.cli.cfi_cli.ACTIVE_BANK_FILE",
        tmp_path / "config" / "active_bank",
    )

    with _patch_api(FAKE_BUNDLE):
        result = command_join(
            bank_id="bank_alpha",
            coordinator_url="http://coord.test",
            legal_name="Alpha National Bank",
            jurisdiction="TR",
            contact_email="sec@alpha.com",
            data_residency_region="eu-west-1",
        )

    # Cert files must exist
    cert_file = tmp_path / "certs" / "bank_alpha.crt"
    key_file = tmp_path / "certs" / "bank_alpha.key"
    config_file = tmp_path / "config" / "bank_alpha.yaml"
    active_file = tmp_path / "config" / "active_bank"

    assert cert_file.exists(), "Certificate file must be written"
    assert key_file.exists(), "Private key file must be written"
    assert config_file.exists(), "Connector config YAML must be written"
    assert active_file.exists(), "active_bank pointer file must be written"

    # Content checks
    assert "FAKE_CERT" in cert_file.read_text(encoding="utf-8")
    assert "FAKE_KEY" in key_file.read_text(encoding="utf-8")
    assert "bank_alpha" in config_file.read_text(encoding="utf-8")

    active = json.loads(active_file.read_text(encoding="utf-8"))
    assert active["bank_id"] == "bank_alpha"
    assert active["coordinator_url"] == "http://coord.test"

    # API response forwarded
    assert result["bank_id"] == "bank_alpha"


def test_status_command_prints_table(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    """status should query coordinator and print a formatted table with ACTIVE status."""
    _setup_active_bank(tmp_path, monkeypatch)

    with _patch_api(FAKE_STATUS):
        result = command_status()

    captured = capsys.readouterr()
    assert "bank_alpha" in captured.out
    assert "ACTIVE" in captured.out
    assert result["status"] == "ACTIVE"


def test_rotate_certs_overwrites_existing(tmp_path: Path, monkeypatch) -> None:
    """rotate-certs must overwrite existing cert/key files with the new PEM content."""
    _setup_active_bank(tmp_path, monkeypatch)

    # Write old dummy certs
    cert_file = tmp_path / "certs" / "bank_alpha.crt"
    key_file = tmp_path / "certs" / "bank_alpha.key"
    cert_file.write_text("OLD_CERT_CONTENT", encoding="utf-8")
    key_file.write_text("OLD_KEY_CONTENT", encoding="utf-8")

    with _patch_api(FAKE_ROTATION):
        result = command_rotate_certs(bank_id="bank_alpha")

    # Files must have changed
    assert "ROTATED_CERT" in cert_file.read_text(encoding="utf-8")
    assert "ROTATED_KEY" in key_file.read_text(encoding="utf-8")
    assert result["cert_fingerprint"] == "newfingerprint123"


def test_export_diagnostics_creates_signed_bundle(tmp_path: Path, monkeypatch) -> None:
    """export-diagnostics must produce a JSON file containing sha256_signature."""
    _setup_active_bank(tmp_path, monkeypatch)

    out_file = tmp_path / "diag_bundle.json"
    returned = command_export_diagnostics(output_path=str(out_file))

    assert returned == out_file
    assert out_file.exists(), "Diagnostics file must exist"

    bundle = json.loads(out_file.read_text(encoding="utf-8"))
    assert "sha256_signature" in bundle, "Bundle must contain sha256_signature field"
    assert len(bundle["sha256_signature"]) == 64, "SHA-256 hex digest must be 64 chars"
    assert "hostname" in bundle
    assert "python_version" in bundle
    assert "cli_version" in bundle

    # Signature must be deterministic: re-compute and compare
    sig_payload = {k: v for k, v in bundle.items() if k != "sha256_signature"}
    expected_sig = hashlib.sha256(json.dumps(sig_payload, sort_keys=True).encode()).hexdigest()
    assert bundle["sha256_signature"] == expected_sig, "SHA-256 signature must be valid"
