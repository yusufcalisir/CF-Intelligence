"""Integration tests for Section 38.1: Delete Mock Connector & Harden Production Factory."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.infrastructure.connectors.factory import BankConnectorFactory
from app.infrastructure.connectors.fixture_connector import FixtureConnector
from app.infrastructure.connectors.iso20022_connector import ISO20022MessagingConnector


def test_mock_connector_not_importable_in_production(monkeypatch) -> None:
    """Verifies that mock_connector is deleted and FixtureConnector raises ImportError in production."""
    # 1. Deleted mock_connector module must fail import
    if "app.infrastructure.connectors.mock_connector" in sys.modules:
        del sys.modules["app.infrastructure.connectors.mock_connector"]

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.infrastructure.connectors.mock_connector")

    # 2. FixtureConnector must fail import when APP_ENV is production
    monkeypatch.setenv("APP_ENV", "production")
    if "app.infrastructure.connectors.fixture_connector" in sys.modules:
        del sys.modules["app.infrastructure.connectors.fixture_connector"]

    with pytest.raises(ImportError, match="must not be used in production"):
        importlib.import_module("app.infrastructure.connectors.fixture_connector")

    # Reset monkeypatch for subsequent tests
    monkeypatch.delenv("APP_ENV", raising=False)
    if "app.infrastructure.connectors.fixture_connector" in sys.modules:
        del sys.modules["app.infrastructure.connectors.fixture_connector"]
    importlib.import_module("app.infrastructure.connectors.fixture_connector")


def test_factory_raises_on_unknown_type(monkeypatch) -> None:
    """Verifies that BankConnectorFactory raises ValueError on 'mock' or unknown connector types."""
    settings = MagicMock()
    settings.bank_a_connector_type = "mock"
    settings.bank_a_auth_type = "none"
    settings.bank_a_api_key = ""

    with pytest.raises(ValueError, match="Unknown connector type"):
        BankConnectorFactory.get_connector("bank-a", settings)

    settings.bank_a_connector_type = "unsupported_unknown_type"
    with pytest.raises(ValueError, match="Unknown connector type"):
        BankConnectorFactory.get_connector("bank-a", settings)


def test_fixture_connector_reads_parquet() -> None:
    """Verifies that FixtureConnector loads parquet fixture and returns NormalizedTransaction list."""
    fixture_file = Path("backend/tests/fixtures/transactions.parquet")
    if not fixture_file.exists():
        fixture_file = Path("tests/fixtures/transactions.parquet")

    connector = FixtureConnector(fixture_file)
    txs = connector.fetch_transactions(10)

    assert len(txs) == 10
    assert txs[0].transaction_id.startswith("tx_pq_")
    assert txs[0].amount > 0
    assert txs[0].currency == "USD"


def test_iso20022_connector_created_successfully() -> None:
    """Verifies that BankConnectorFactory instantiates ISO20022MessagingConnector for iso20022 config."""
    settings = MagicMock()
    settings.bank_a_connector_type = "iso20022"
    settings.bank_a_auth_type = "none"
    settings.bank_a_api_key = ""

    connector = BankConnectorFactory.get_connector("bank-a", settings)

    assert isinstance(connector, ISO20022MessagingConnector)
