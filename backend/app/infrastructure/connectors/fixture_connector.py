"""Test-only Fixture Connector reading offline benchmark/sample dataset files."""

from __future__ import annotations

import json
import os

if os.getenv("APP_ENV") == "production":
    raise ImportError("FixtureConnector must not be used in production")

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.infrastructure.connectors.base_connector import (
    BaseBankConnector,
    NormalizedTransaction,
)

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)


class FixtureConnector(BaseBankConnector):
    """Test-only connector for ingesting sample transaction datasets (Parquet, CSV, JSON)."""

    def __init__(self, fixture_path: str | Path) -> None:
        self.fixture_path = Path(fixture_path)
        if not self.fixture_path.exists():
            raise FileNotFoundError(f"Fixture file not found: {self.fixture_path}")

        self._buffered_transactions: list[NormalizedTransaction] = []
        self._load_fixture()

    def _load_fixture(self) -> None:
        """Parse fixture file into NormalizedTransaction list based on extension."""
        ext = self.fixture_path.suffix.lower()
        if ext in (".parquet", ".pq"):
            self._load_parquet()
        elif ext == ".json":
            self._load_json()
        elif ext == ".csv":
            self._load_csv()
        elif ext == ".xml":
            self._load_xml()
        else:
            raise ValueError(f"Unsupported fixture format extension: {ext}")

    def _load_parquet(self) -> None:
        import pandas as pd

        df = pd.read_parquet(self.fixture_path)
        self._buffered_transactions = self._dataframe_to_normalized(df)

    def _load_csv(self) -> None:
        import pandas as pd

        df = pd.read_csv(self.fixture_path)
        self._buffered_transactions = self._dataframe_to_normalized(df)

    def _load_json(self) -> None:
        content = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        if isinstance(content, list):
            items = content
        elif isinstance(content, dict) and "transactions" in content:
            items = content["transactions"]
        else:
            items = [content]

        self._buffered_transactions = [self._row_to_normalized(row) for row in items]

    def _load_xml(self) -> None:
        from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector

        iso_parser = ISO20022MessagingConnector()
        xml_text = self.fixture_path.read_text(encoding="utf-8")
        if "camt.053" in xml_text:
            self._buffered_transactions = iso_parser.parse_camt053_xml(xml_text)
        else:
            self._buffered_transactions = [iso_parser.parse_pacs008_xml(xml_text)]

    def _dataframe_to_normalized(self, df: Any) -> list[NormalizedTransaction]:
        results: list[NormalizedTransaction] = []
        for idx, row in df.iterrows():
            row_dict = row.to_dict()
            results.append(self._row_to_normalized(row_dict, default_id=f"tx_fix_{idx}"))
        return results

    def _row_to_normalized(
        self, row: dict[str, Any], default_id: str = "tx_fix_0"
    ) -> NormalizedTransaction:
        tx_id = str(row.get("transaction_id") or row.get("tx_id") or row.get("id") or default_id)
        acc_id = str(row.get("account_id") or row.get("debtor_account") or "ACC_001")
        cp_acc_id = str(
            row.get("counterparty_account_id") or row.get("creditor_account") or "ACC_002"
        )
        amt = float(row.get("amount") or row.get("amount_usd") or 100.0)
        curr = str(row.get("currency") or "USD")

        ts_raw = row.get("timestamp") or row.get("created_at")
        if isinstance(ts_raw, datetime):
            ts = ts_raw
        elif isinstance(ts_raw, str):
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                ts = datetime.now(UTC)
        else:
            ts = datetime.now(UTC)

        return NormalizedTransaction(
            transaction_id=tx_id,
            account_id=acc_id,
            counterparty_account_id=cp_acc_id,
            amount=amt,
            currency=curr,
            timestamp=ts,
            merchant_category_code=str(row.get("merchant_category_code") or "5411"),
            origin_country=str(row.get("origin_country") or "US"),
            destination_country=str(row.get("destination_country") or "US"),
            device_fingerprint=str(row.get("device_fingerprint") or ""),
            ip_subnet=str(row.get("ip_subnet") or ""),
            channel_type=str(row.get("channel_type") or "ONLINE"),
        )

    def fetch_transactions(self, limit: int = 100) -> list[NormalizedTransaction]:
        """Fetch up to ``limit`` normalized transaction objects from loaded fixture."""
        return self._buffered_transactions[:limit]

    def consume_stream(self) -> Generator[NormalizedTransaction, None, None]:
        """Yield continuous transaction events from buffered fixture data."""
        yield from self._buffered_transactions

    def parse_batch(self, payload: Any) -> list[NormalizedTransaction]:
        """Parse input payload or return buffered transactions."""
        if isinstance(payload, (str, Path)):
            temp_conn = FixtureConnector(payload)
            return temp_conn.fetch_transactions()
        return self._buffered_transactions
