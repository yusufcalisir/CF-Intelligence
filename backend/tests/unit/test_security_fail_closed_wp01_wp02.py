"""Regression tests for WP-01/WP-02 fail-closed security behavior.

Network response fixtures are contract-level test doubles; they do not claim
that an external Vault or banking provider was available during testing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from app.infrastructure.connectors.mambu_connector import (
    MambuConnector,
    MambuWebhookSignatureError,
)
from app.infrastructure.connectors.thought_machine_connector import (
    ThoughtMachineConnector,
    ThoughtMachineSignatureError,
)
from app.infrastructure.connectors.rest_connector import AuthenticationError, RESTBankConnector
from app.infrastructure.security import hsm_key_service as hsm_module
from app.infrastructure.security import vault_client as vault_module
from app.infrastructure.security.hsm_key_service import HSMKeyService, HSMProvider
from app.infrastructure.security.vault_client import VaultClient, VaultUnavailableError


class _VaultResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


@pytest.mark.parametrize("method", ["encrypt", "decrypt", "create_transit_key"])
def test_vault_network_outage_never_returns_fake_success(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    def offline(*args, **kwargs):
        raise OSError("controlled Vault outage")

    monkeypatch.setattr(vault_module.urllib.request, "urlopen", offline)
    client = VaultClient(vault_url="http://vault.invalid:8200")
    with pytest.raises(VaultUnavailableError):
        if method == "encrypt":
            client.encrypt("bank_a", b"sensitive")
        elif method == "decrypt":
            client.decrypt("bank_a", "vault:v1:legitimate-ciphertext")
        else:
            client.create_transit_key("bank_a")


@pytest.mark.parametrize(
    ("method", "payload"),
    [
        ("encrypt", {"data": {}}),
        ("encrypt", {"data": {"ciphertext": ""}}),
        ("encrypt", {"data": {"ciphertext": "plaintext:invalid"}}),
        ("decrypt", {"data": {}}),
        ("decrypt", {"data": {"plaintext": "not valid base64 !!!"}}),
    ],
)
def test_vault_successful_http_without_valid_crypto_result_fails_closed(
    monkeypatch: pytest.MonkeyPatch, method: str, payload: dict
) -> None:
    monkeypatch.setattr(
        vault_module.urllib.request, "urlopen",
        lambda *args, **kwargs: _VaultResponse(payload),
    )
    client = VaultClient(vault_url="http://vault.invalid:8200")
    with pytest.raises(VaultUnavailableError):
        if method == "encrypt":
            client.encrypt("bank_a", b"sensitive")
        else:
            client.decrypt("bank_a", "vault:v1:existing")


def test_vault_valid_response_contract_without_claiming_real_crypto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only verifies parsing of a contract response, not server encryption."""
    def response(request, **kwargs):
        if "/encrypt/" in request.full_url:
            return _VaultResponse({"data": {"ciphertext": "vault:v1:mock-opaque-blob"}})
        return _VaultResponse({"data": {"plaintext": base64.b64encode(b"hello").decode()}})

    monkeypatch.setattr(vault_module.urllib.request, "urlopen", response)
    client = VaultClient(vault_url="http://vault.invalid:8200")
    assert client.encrypt("bank_a", b"hello") == "vault:v1:mock-opaque-blob"
    assert client.decrypt("bank_a", "vault:v1:mock-opaque-blob") == b"hello"


def _hsm_without_hardware() -> HSMKeyService:
    service = object.__new__(HSMKeyService)
    service.provider = HSMProvider.LOCAL_EMULATED
    service.hsm_signer = SimpleNamespace(sign_data=lambda *args, **kwargs: b"test-signature")
    return service


def test_hsm_missing_aes_gcm_rejects_encryption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hsm_module, "_CRYPTO_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="refusing insecure XOR"):
        _hsm_without_hardware().encrypt_envelope(b"secret")


def test_hsm_missing_aes_gcm_rejects_decryption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hsm_module, "_CRYPTO_AVAILABLE", False)
    with pytest.raises(RuntimeError, match="refusing insecure XOR"):
        _hsm_without_hardware().decrypt_envelope("YWJj", nonce_b64="AAAAAAAAAAAAAAAA")


def test_hsm_vault_provider_rejects_non_vault_ciphertext() -> None:
    service = _hsm_without_hardware()
    service.provider = HSMProvider.VAULT_TRANSIT
    with pytest.raises(ValueError, match="Vault-formatted ciphertext"):
        service.decrypt_envelope("arbitrary-plaintext")


@pytest.mark.parametrize(
    ("connector_cls", "error_cls"),
    [
        (MambuConnector, MambuWebhookSignatureError),
        (ThoughtMachineConnector, ThoughtMachineSignatureError),
    ],
)
def test_webhook_signature_required_and_tampering_rejected(
    connector_cls, error_cls,
) -> None:
    connector = connector_cls(webhook_secret="controlled-test-secret")
    payload = (
        {"type": "transaction.created", "transactionId": "tx1", "accountId": "a1", "amount": 15.0}
        if connector_cls is MambuConnector
        else {"posting_instruction_batch": {"id": "pib1", "posting_instructions": []}}
    )
    raw = json.dumps(payload).encode("utf-8")
    sig = hmac.new(b"controlled-test-secret", raw, hashlib.sha256).hexdigest()

    with pytest.raises(error_cls):
        connector.parse_webhook_event(payload, raw_body=raw)

    with pytest.raises(error_cls):
        connector.parse_webhook_event(payload, signature_header=sig)

    with pytest.raises(error_cls):
        connector.parse_webhook_event(payload, signature_header=sig, raw_body=raw + b" ")

    result = connector.parse_webhook_event(payload, signature_header=sig, raw_body=raw)
    assert result is not None


@pytest.mark.parametrize("connector_cls", [MambuConnector, ThoughtMachineConnector])
def test_missing_webhook_secret_fails_closed(monkeypatch: pytest.MonkeyPatch, connector_cls) -> None:
    env_name = "MAMBU_WEBHOOK_SECRET" if connector_cls is MambuConnector else "VAULT_CORE_WEBHOOK_SECRET"
    monkeypatch.delenv(env_name, raising=False)
    connector = connector_cls(webhook_secret="")
    payload = {"type": "transaction.created"} if connector_cls is MambuConnector else {}
    raw = json.dumps(payload).encode("utf-8")
    with pytest.raises((MambuWebhookSignatureError, ThoughtMachineSignatureError)):
        connector.parse_webhook_event(payload, signature_header="a" * 64, raw_body=raw)

@pytest.mark.parametrize(
    ("cert_path", "key_path"),
    [("", ""), ("/nonexistent/client.crt", "/nonexistent/client.key")],
)
def test_rest_bank_connector_never_downgrades_missing_mtls_credentials(
    cert_path: str, key_path: str,
) -> None:
    connector = RESTBankConnector(
        base_url="https://bank.invalid",
        auth_type="mtls",
        client_cert_path=cert_path,
        client_key_path=key_path,
    )
    with pytest.raises(AuthenticationError, match="mTLS is required"):
        connector._get_client()
