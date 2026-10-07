"""Parquet and CSV Benchmark Dataset Bank Connector."""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from app.infrastructure.connectors.base_connector import BaseBankConnector, NormalizedTransaction

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)


class ParquetConnector(BaseBankConnector):
    """Connector for reading and ingesting Parquet and CSV benchmark datasets into NormalizedTransaction streams."""

    def __init__(self, filepath: str | Path | None = None) -> None:
        self.filepath = Path(filepath) if filepath else None
        self._buffered_transactions: list[NormalizedTransaction] = []
        if self.filepath and self.filepath.exists():
            self.load_file(self.filepath)

    def load_file(self, filepath: str | Path) -> list[NormalizedTransaction]:
        """Loads a Parquet or CSV file from disk into the transaction buffer."""
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Benchmark dataset file not found: {path}")

        df = pd.read_parquet(path) if path.suffix in (".parquet", ".pq") else pd.read_csv(path)

        transactions = self._dataframe_to_normalized(df)
        self._buffered_transactions.extend(transactions)
        logger.info("Loaded %d transactions from %s", len(transactions), path)
        return transactions

    def _dataframe_to_normalized(self, df: pd.DataFrame) -> list[NormalizedTransaction]:
        """Converts Pandas DataFrame rows to NormalizedTransaction schemas."""
        transactions: list[NormalizedTransaction] = []
        for idx, row in df.iterrows():
            ts_val = row.get("timestamp")
            if isinstance(ts_val, str):
                ts = datetime.fromisoformat(ts_val)
            elif isinstance(ts_val, datetime):
                ts = ts_val
            else:
                ts = datetime.now(UTC)

            raw_orig = row.get("origin_country")
            origin_country: str | None = None
            if raw_orig is not None and not bool(pd.isna(raw_orig)):
                s_orig = str(raw_orig).strip()
                if s_orig and s_orig.lower() != "nan":
                    origin_country = s_orig

            raw_dest = row.get("destination_country")
            destination_country: str | None = None
            if raw_dest is not None and not bool(pd.isna(raw_dest)):
                s_dest = str(raw_dest).strip()
                if s_dest and s_dest.lower() != "nan":
                    destination_country = s_dest

            raw_amt = row.get("amount")
            amt = float(raw_amt) if (raw_amt is not None and not bool(pd.isna(raw_amt))) else 100.0

            tx = NormalizedTransaction(
                transaction_id=str(row.get("transaction_id") or f"parquet_tx_{idx}"),
                account_id=str(row.get("account_id") or "UNKNOWN_DEBTOR"),
                counterparty_account_id=str(
                    row.get("counterparty_account_id") or "UNKNOWN_CREDITOR"
                ),
                amount=amt,
                currency=str(row.get("currency", "USD")),
                timestamp=ts,
                merchant_category_code=str(row.get("merchant_category_code", "5999")),
                origin_country=origin_country,
                destination_country=destination_country,
                device_fingerprint=str(row.get("device_fingerprint", "")),
                ip_subnet=str(row.get("ip_subnet", "")),
                channel_type=str(row.get("channel_type", "BENCHMARK_PARQUET")),
            )
            transactions.append(tx)
        return transactions

    def read_parquet_batches(
        self,
        filepath: str | Path,
        batch_size: int = 10000,
    ) -> Generator[list[NormalizedTransaction], None, None]:
        """Streams Parquet record batches using PyArrow for zero-copy memory-efficient processing."""
        import pyarrow.parquet as pq

        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Parquet file not found: {path}")

        parquet_file = pq.ParquetFile(path)
        for batch in parquet_file.iter_batches(batch_size=batch_size):
            df_batch = batch.to_pandas()
            txs = self._dataframe_to_normalized(df_batch)
            yield txs

    def parse_batch(self, payload: Any) -> list[NormalizedTransaction]:
        """Parses batch payloads from raw Parquet binary buffers, CSV strings, or DataFrame objects."""
        if isinstance(payload, pd.DataFrame):
            txs = self._dataframe_to_normalized(payload)
        elif isinstance(payload, bytes):
            try:
                df = pd.read_parquet(io.BytesIO(payload))
            except Exception:
                df = pd.read_csv(io.BytesIO(payload))
            txs = self._dataframe_to_normalized(df)
        elif isinstance(payload, (str, Path)):
            txs = self.load_file(payload)
        else:
            raise ValueError(f"Unsupported payload type for ParquetConnector: {type(payload)}")

        self._buffered_transactions.extend(txs)
        return txs

    def consume_stream(self) -> Generator[NormalizedTransaction, None, None]:
        """Yields continuously from buffered transactions."""
        yield from self._buffered_transactions
