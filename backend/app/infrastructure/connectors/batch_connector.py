"""Batch End-Of-Day (EOD) File Bank Connector for CSV and Parquet dumps."""

from __future__ import annotations

import csv
import io
import logging
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.infrastructure.connectors.base_connector import BaseBankConnector, NormalizedTransaction

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)


class BatchEODFileConnector(BaseBankConnector):
    """Connector for validating and ingesting EOD batch CSV and Parquet transaction dumps."""

    def __init__(self):
        super().__init__()
        self._batch_queue: deque[NormalizedTransaction] = deque()

    def parse_csv_stream(self, csv_content: str | bytes) -> list[NormalizedTransaction]:
        """Parses a CSV formatted transaction batch into NormalizedTransaction list."""
        if isinstance(csv_content, bytes):
            csv_content = csv_content.decode("utf-8")

        reader = csv.DictReader(io.StringIO(csv_content))
        transactions: list[NormalizedTransaction] = []

        for row in reader:
            tx = NormalizedTransaction(
                transaction_id=str(
                    row.get("transaction_id") or row.get("tx_id") or f"batch_{len(transactions)}"
                ),
                account_id=str(row.get("account_id") or row.get("sender") or "UNKNOWN_SENDER"),
                counterparty_account_id=str(
                    row.get("counterparty_account_id") or row.get("receiver") or "UNKNOWN_RECEIVER"
                ),
                amount=float(row.get("amount", 0.0)),
                currency=str(row.get("currency", "USD")),
                timestamp=datetime.fromisoformat(row["timestamp"])
                if "timestamp" in row and row["timestamp"]
                else datetime.now(timezone.utc),
                merchant_category_code=str(
                    row.get("merchant_category_code") or row.get("mcc") or "0000"
                ),
                origin_country=str(row.get("origin_country") or "US"),
                destination_country=str(row.get("destination_country") or "US"),
                device_fingerprint=str(row.get("device_fingerprint", "")),
                ip_subnet=str(row.get("ip_subnet", "")),
                channel_type=str(row.get("channel_type", "BATCH_FILE")),
            )
            transactions.append(tx)

        self._batch_queue.extend(transactions)
        return transactions

    def parse_parquet_rows(self, rows: list[dict[str, Any]]) -> list[NormalizedTransaction]:
        """Parses a list of row dictionaries extracted from a Parquet file."""
        transactions: list[NormalizedTransaction] = []
        for row in rows:
            tx = NormalizedTransaction(
                transaction_id=str(row.get("transaction_id", f"pq_{len(transactions)}")),
                account_id=str(row.get("account_id", "UNKNOWN")),
                counterparty_account_id=str(row.get("counterparty_account_id", "UNKNOWN")),
                amount=float(row.get("amount", 0.0)),
                currency=str(row.get("currency", "USD")),
                timestamp=datetime.fromisoformat(str(row["timestamp"]))
                if "timestamp" in row
                else datetime.utcnow(),
                merchant_category_code=str(row.get("merchant_category_code", "0000")),
                origin_country=str(row.get("origin_country", "US")),
                destination_country=str(row.get("destination_country", "US")),
                device_fingerprint=str(row.get("device_fingerprint", "")),
                ip_subnet=str(row.get("ip_subnet", "")),
                channel_type=str(row.get("channel_type", "PARQUET_BATCH")),
            )
            transactions.append(tx)

        self._batch_queue.extend(transactions)
        return transactions

    def consume_stream(self) -> Generator[NormalizedTransaction, None, None]:
        """Yields transactions from ingested batch file queue."""
        while self._batch_queue:
            yield self._batch_queue.popleft()

    def parse_batch(self, payload: Any) -> list[NormalizedTransaction]:
        """Parses string CSV content or list of dict rows."""
        if isinstance(payload, (str, bytes)):
            return self.parse_csv_stream(payload)
        elif isinstance(payload, list):
            return self.parse_parquet_rows(payload)
        return []
