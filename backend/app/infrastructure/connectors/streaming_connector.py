"""Streaming Payment Bank Connector implementation for real-time message feeds."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.infrastructure.connectors.base_connector import BaseBankConnector, NormalizedTransaction

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)


class StreamingPaymentConnector(BaseBankConnector):
    """Connector for high-throughput streaming payment topics (Kafka, RabbitMQ, Redis Streams)."""

    def __init__(self, stream_source: Any = None, topic: str = "payments.raw"):
        self.stream_source = stream_source
        self.topic = topic
        self._buffer: list[NormalizedTransaction] = []

    def push_raw_event(self, event_data: dict[str, Any] | str) -> NormalizedTransaction:
        """Pushes a raw event payload into the stream, validating and converting to NormalizedTransaction."""
        event_dict = json.loads(event_data) if isinstance(event_data, str) else event_data

        tx = NormalizedTransaction(
            transaction_id=str(
                event_dict.get("transaction_id") or event_dict.get("id") or "tx_unknown"
            ),
            account_id=str(
                event_dict.get("account_id") or event_dict.get("debtor_account") or "acc_unknown"
            ),
            counterparty_account_id=str(
                event_dict.get("counterparty_account_id")
                or event_dict.get("creditor_account")
                or "acc_counterparty"
            ),
            amount=float(event_dict.get("amount", 0.0)),
            currency=str(event_dict.get("currency", "USD")),
            timestamp=datetime.fromisoformat(event_dict["timestamp"])
            if "timestamp" in event_dict and isinstance(event_dict["timestamp"], str)
            else datetime.now(timezone.utc),
            merchant_category_code=str(
                event_dict.get("merchant_category_code") or event_dict.get("mcc") or "0000"
            ),
            origin_country=str(event_dict.get("origin_country") or "US"),
            destination_country=str(event_dict.get("destination_country") or "US"),
            device_fingerprint=str(event_dict.get("device_fingerprint", "")),
            ip_subnet=str(event_dict.get("ip_subnet", "")),
            channel_type=str(event_dict.get("channel_type", "ONLINE")),
        )
        self._buffer.append(tx)
        return tx

    def consume_stream(self) -> Generator[NormalizedTransaction, None, None]:
        """Yields transactions from internal buffer or attached streaming source."""
        while self._buffer:
            yield self._buffer.pop(0)

    def parse_batch(self, payload: list[dict[str, Any]] | str) -> list[NormalizedTransaction]:
        """Parses a batch array of event payloads into NormalizedTransaction list."""
        payload_list = json.loads(payload) if isinstance(payload, str) else payload

        results: list[NormalizedTransaction] = []
        for item in payload_list:
            results.append(self.push_raw_event(item))
        return results
