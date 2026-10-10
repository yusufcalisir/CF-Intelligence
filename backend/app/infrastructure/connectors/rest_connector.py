"""HTTP REST Bank Connector implementing the connector port."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

import httpx

from app.infrastructure.connectors.base_connector import BaseBankConnector, NormalizedTransaction

if TYPE_CHECKING:
    from collections.abc import Generator

    from app.domain.value_objects import ModelWeights

logger = logging.getLogger(__name__)


class AuthenticationError(RuntimeError):
    """Raised when REST bank connector authentication fails."""


class RESTBankConnector(BaseBankConnector):
    """Sends FL commands to bank nodes over HTTP REST APIs and ingests real-time webhook payloads."""

    def __init__(
        self,
        base_url: str,
        auth_type: str = "none",
        api_key: str = "",
        oauth_client_id: str = "",
        oauth_client_secret: str = "",
        oauth_token_url: str = "",
        client_cert_path: str = "",
        client_key_path: str = "",
        signing_secret: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.auth_type = auth_type
        self.api_key = api_key
        self.oauth_client_id = oauth_client_id
        self.oauth_client_secret = oauth_client_secret
        self.oauth_token_url = oauth_token_url
        self.client_cert_path = client_cert_path
        self.client_key_path = client_key_path
        self._token: str | None = None
        from app.config import get_settings

        self.settings = get_settings()
        self.signing_secret = signing_secret

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_type == "apikey" and self.api_key:
            headers["X-API-KEY"] = self.api_key
        elif self.auth_type == "oauth2":
            token = self._get_oauth2_token()
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _sign_payload(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[bytes, dict[str, str]]:
        import json

        body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        secret = self.signing_secret or self.settings.payload_signing_secret
        if not secret or not str(secret).strip():
            raise AuthenticationError(
                "Outbound bank dispatch requires a configured, non-empty payload_signing_secret"
            )
        import hashlib
        import hmac
        import time

        timestamp = str(time.time())
        sign_data = timestamp.encode("utf-8") + b"." + body_bytes
        signature = hmac.new(secret.encode("utf-8"), sign_data, hashlib.sha256).hexdigest()
        new_headers = dict(headers)
        new_headers["X-Payload-Signature"] = signature
        new_headers["X-Payload-Timestamp"] = timestamp
        return body_bytes, new_headers

    def _get_oauth2_token(self) -> str:
        if self._token:
            return self._token
        if not self.oauth_token_url:
            raise AuthenticationError("OAuth2 token_url is not configured.")
        logger.info("Requesting OAuth2 client credentials token from %s", self.oauth_token_url)
        try:
            # Send standard client credentials request
            payload = {
                "grant_type": "client_credentials",
                "client_id": self.settings.oauth_client_id,
                "client_secret": self.settings.oauth_client_secret,
            }
            with httpx.Client() as client:
                resp = client.post(self.oauth_token_url, data=payload, timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    tok = data.get("access_token")
                    if tok and str(tok).strip():
                        self._token = str(tok).strip()
                        return self._token
                    raise AuthenticationError(
                        f"OAuth2 server returned 200 but response is missing access_token: {data}"
                    )
                logger.error(
                    "OAuth2 server %s returned status %d: %s",
                    self.oauth_token_url,
                    resp.status_code,
                    resp.text,
                )
                raise AuthenticationError(
                    f"OAuth2 server returned status {resp.status_code} during token acquisition"
                )
        except AuthenticationError:
            raise
        except Exception as exc:
            logger.error(
                "Failed to fetch OAuth2 token from %s: %s",
                self.oauth_token_url,
                exc,
            )
            raise AuthenticationError(
                f"OAuth2 token acquisition failed from {self.oauth_token_url}: {exc}"
            ) from exc

    def _get_client(self) -> httpx.Client:
        import os

        # Mutual TLS support hook
        if self.auth_type == "mtls" and self.client_cert_path and self.client_key_path:
            if os.path.exists(self.client_cert_path) and os.path.exists(self.client_key_path):
                logger.info("Configuring Mutual TLS with cert: %s", self.client_cert_path)
                return httpx.Client(cert=(self.client_cert_path, self.client_key_path))
            else:
                logger.warning(
                    "mTLS configured but certificate/key files do not exist: %s, %s. Using default client.",
                    self.client_cert_path,
                    self.client_key_path,
                )
        return httpx.Client()

    def initialize(
        self,
        bank_id: str,
        num_transactions: int,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Trigger initialization over HTTP REST endpoint."""
        url = f"{self.base_url}/api/v1/bank-client/initialize"
        payload = {
            "bank_id": bank_id,
            "num_transactions": num_transactions,
            "seed": seed,
        }
        body_bytes, headers = self._sign_payload(payload, self._get_headers())
        with self._get_client() as client:
            resp = client.post(url, content=body_bytes, headers=headers, timeout=30.0)
            resp.raise_for_status()
            return resp.json()

    def train(
        self,
        bank_id: str,
        weights: ModelWeights,
        learning_rate: float,
        batch_size: int,
        epochs: int,
        enable_dp: bool,
        dp_epsilon: float,
        dp_delta: float,
        dp_max_grad_norm: float,
        correlation_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Trigger model training over HTTP REST endpoint."""
        url = f"{self.base_url}/api/v1/bank-client/train"
        schema_weights = {
            "layer_shapes": [list(shape) for shape in weights.layer_shapes],
            "flat_weights": weights.flat_weights,
        }
        payload = {
            "weights": schema_weights,
            "learning_rate": learning_rate,
            "batch_size": batch_size,
            "epochs": epochs,
            "enable_dp": enable_dp,
            "dp_epsilon": dp_epsilon,
            "dp_delta": dp_delta,
            "dp_max_grad_norm": dp_max_grad_norm,
            "fedprox_mu": kwargs.get("fedprox_mu", 0.0),
            "moon_mu": kwargs.get("moon_mu", 0.0),
            "moon_temperature": kwargs.get("moon_temperature", 0.5),
        }

        prev_local_weights = kwargs.get("prev_local_weights")
        if prev_local_weights:
            payload["prev_local_weights"] = {
                "layer_shapes": [list(shape) for shape in prev_local_weights.layer_shapes],
                "flat_weights": prev_local_weights.flat_weights,
            }

        body_bytes, headers = self._sign_payload(payload, self._get_headers())
        with self._get_client() as client:
            resp = client.post(url, content=body_bytes, headers=headers, timeout=120.0)
            resp.raise_for_status()
            res_data = resp.json()
            res_data["correlation_id"] = correlation_id
            return res_data

    def evaluate(
        self,
        bank_id: str,
        weights: ModelWeights,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Trigger model evaluation over HTTP REST endpoint."""
        url = f"{self.base_url}/api/v1/bank-client/evaluate"
        schema_weights = {
            "layer_shapes": [list(shape) for shape in weights.layer_shapes],
            "flat_weights": weights.flat_weights,
        }
        payload = {"weights": schema_weights}
        body_bytes, headers = self._sign_payload(payload, self._get_headers())
        with self._get_client() as client:
            resp = client.post(url, content=body_bytes, headers=headers, timeout=60.0)
            resp.raise_for_status()
            res_data = resp.json()
            res_data["correlation_id"] = correlation_id
            return res_data

    def consume_stream(self) -> Generator[NormalizedTransaction, None, None]:
        """Yields transactions received via HTTP REST webhook endpoint."""
        if not hasattr(self, "_webhook_queue"):
            self._webhook_queue: list[NormalizedTransaction] = []
        while self._webhook_queue:
            yield self._webhook_queue.pop(0)

    def parse_batch(self, payload: Any) -> list[NormalizedTransaction]:
        """Parses webhook JSON body payload into NormalizedTransaction instances."""
        if not hasattr(self, "_webhook_queue"):
            self._webhook_queue = []

        if isinstance(payload, dict):
            payload = [payload]

        results: list[NormalizedTransaction] = []
        if isinstance(payload, list):
            for item in payload:
                acc_id = item.get("account_id")
                if not acc_id or str(acc_id).strip() in ("", "UNKNOWN", "UNKNOWN_DEBTOR"):
                    raise ValueError(f"Transaction item {item.get('transaction_id')} missing mandatory account_id")

                cpty_id = item.get("counterparty_account_id")
                if not cpty_id or str(cpty_id).strip() in ("", "UNKNOWN", "UNKNOWN_CREDITOR"):
                    raise ValueError(f"Transaction item {item.get('transaction_id')} missing mandatory counterparty_account_id")

                amt_val = item.get("amount")
                if amt_val is None:
                    raise ValueError(f"Transaction item {item.get('transaction_id')} missing mandatory amount")
                try:
                    amt = float(amt_val)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"Transaction item {item.get('transaction_id')} invalid amount: {amt_val}") from exc
                if amt <= 0 or not math.isfinite(amt):
                    raise ValueError(f"Transaction item {item.get('transaction_id')} amount must be positive and finite: {amt}")

                tx = NormalizedTransaction(
                    transaction_id=str(item.get("transaction_id") or f"wh_{len(results)}"),
                    account_id=str(acc_id).strip(),
                    counterparty_account_id=str(cpty_id).strip(),
                    amount=amt,
                    currency=str(item.get("currency", "USD")),
                    merchant_category_code=str(item.get("merchant_category_code", "0000")),
                    channel_type="REST_WEBHOOK",
                )
                results.append(tx)

        self._webhook_queue.extend(results)
        return results
